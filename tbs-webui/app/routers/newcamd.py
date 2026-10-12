"""API de Newcamd: cuentas de card sharing propio o autorizado.

AVISO LEGAL: este módulo gestiona conexiones a servidores **Newcamd**. Úsalo
únicamente con servidores de tu propiedad o para los que tengas autorización
expresa (por ejemplo, tu propio OSCam con tu tarjeta, o el servidor de tu
operador). El proyecto no incluye ni distribuye claves ni servicios de terceros.

Endpoints:
    GET    /api/newcamd/accounts             lista (sin contraseñas)
    POST   /api/newcamd/accounts             crea una cuenta
    PUT    /api/newcamd/accounts/{id}        edita (password vacío = sin cambios)
    DELETE /api/newcamd/accounts/{id}        borra
    POST   /api/newcamd/accounts/{id}/connect
    POST   /api/newcamd/accounts/{id}/disconnect
    GET    /api/newcamd/accounts/{id}/status
    POST   /api/newcamd/test                 prueba de conexión sin guardar
    POST   /api/newcamd/accounts/{id}/ecm    envío manual de un ECM (diagnóstico)
    GET    /api/newcamd/status               estado de todas las cuentas
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi import status as http_status
from sqlalchemy import delete, select

from ..deps import AppSettings, DbSession
from ..models import NewcamdAccount as NewcamdAccountModel
from ..schemas import (
    NewcamdAccountIn,
    NewcamdAccountOut,
    NewcamdApplyOut,
    NewcamdConfigOut,
    NewcamdProfileTestOut,
    NewcamdActionOut,
    NewcamdEcmIn,
    NewcamdEcmOut,
    NewcamdListOut,
    NewcamdStatusOut,
    NewcamdTestIn,
    NewcamdTestOut,
    OkOut,
)
from ..services import newcamd_manager as mgr
from ..services import newcamd_profiles as perfiles
from ..services import oscam_status as oscam

logger = logging.getLogger(__name__)

# Cache del estado de OSCam (evita golpear el webif en cada refresco de la UI)
_OSCAM_CACHE: oscam.OscamStatusCache | None = None

router = APIRouter(prefix="/newcamd", tags=["newcamd"])


def get_manager(request: Request) -> mgr.NewcamdManager:
    """Gestor de Newcamd creado en el arranque de la aplicación."""
    return request.app.state.newcamd_manager


ManagerDep = Annotated[mgr.NewcamdManager, Depends(get_manager)]


def _to_out(row: NewcamdAccountModel, settings: AppSettings, manager: mgr.NewcamdManager) -> NewcamdAccountOut:
    return NewcamdAccountOut(**mgr.account_to_dict(row, settings, manager.status(row.id)))


def _get_row(db: DbSession, account_id: int) -> NewcamdAccountModel:
    if not (0 < int(account_id) <= 9_223_372_036_854_775_807):
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Cuenta Newcamd no encontrada")
    row = db.get(NewcamdAccountModel, account_id)
    if row is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Cuenta Newcamd no encontrada")
    return row


# ---------------------------------------------------------------------------
# Cuentas
# ---------------------------------------------------------------------------
@router.get("/accounts", response_model=NewcamdListOut)
def list_accounts(db: DbSession, settings: AppSettings, manager: ManagerDep) -> NewcamdListOut:
    """Lista las cuentas configuradas (nunca devuelve la contraseña)."""
    rows = manager.load_accounts(db)
    return NewcamdListOut(
        total=len(rows),
        enabled=settings.newcamd_enabled,
        items=[_to_out(row, settings, manager) for row in rows],
    )


@router.post("/accounts", response_model=NewcamdAccountOut, status_code=http_status.HTTP_201_CREATED)
def create_account(
    payload: NewcamdAccountIn, db: DbSession, settings: AppSettings, manager: ManagerDep
) -> NewcamdAccountOut:
    """Crea una cuenta Newcamd (la contraseña se guarda cifrada)."""
    if not settings.newcamd_enabled:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Newcamd está deshabilitado (TBS_NEWCAMD_ENABLED=0)",
        )
    existing = db.execute(
        select(NewcamdAccountModel).where(NewcamdAccountModel.name == payload.name)
    ).scalars().first()
    if existing is not None:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=f"Ya existe una cuenta llamada '{payload.name}'",
        )

    row = NewcamdAccountModel(
        name=payload.name,
        host=payload.host,
        port=payload.port,
        username=payload.username,
        password_enc=mgr.encrypt_password(payload.password or "", settings),
        deskey=payload.deskey,
        enabled=payload.enabled,
        auto_connect=payload.auto_connect,
        notes=payload.notes or None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info("Cuenta Newcamd '%s' creada (%s:%s)", row.name, row.host, row.port)

    if payload.auto_connect and row.enabled:
        try:
            manager.connect(mgr.to_domain(row, settings))
        except Exception as exc:  # la cuenta queda guardada aunque falle la conexión
            logger.warning("Conexión automática de '%s' falló: %s", row.name, exc)
    return _to_out(row, settings, manager)


@router.put("/accounts/{account_id}", response_model=NewcamdAccountOut)
def update_account(
    account_id: int,
    payload: NewcamdAccountIn,
    db: DbSession,
    settings: AppSettings,
    manager: ManagerDep,
) -> NewcamdAccountOut:
    """Edita una cuenta. Si `password` va vacío o nulo, se conserva la actual."""
    row = _get_row(db, account_id)
    row.name = payload.name
    row.host = payload.host
    row.port = payload.port
    row.username = payload.username
    row.deskey = payload.deskey
    row.enabled = payload.enabled
    row.auto_connect = payload.auto_connect
    row.notes = payload.notes or None
    if payload.password:
        row.password_enc = mgr.encrypt_password(payload.password, settings)
    db.commit()
    db.refresh(row)
    logger.info("Cuenta Newcamd '%s' actualizada", row.name)

    if not row.enabled:
        manager.disconnect(row.id)
    return _to_out(row, settings, manager)


@router.delete("/accounts/{account_id}", response_model=OkOut)
def delete_account(account_id: int, db: DbSession, manager: ManagerDep) -> OkOut:
    """Borra una cuenta y cierra su conexión."""
    row = _get_row(db, account_id)
    manager.disconnect(row.id)
    name = row.name
    db.execute(delete(NewcamdAccountModel).where(NewcamdAccountModel.id == account_id))
    db.commit()
    logger.info("Cuenta Newcamd '%s' borrada", name)
    return OkOut(ok=True, detail=f"Cuenta '{name}' borrada")


# ---------------------------------------------------------------------------
# Conexión
# ---------------------------------------------------------------------------
@router.post("/accounts/{account_id}/connect", response_model=NewcamdActionOut)
def connect_account(
    account_id: int, db: DbSession, settings: AppSettings, manager: ManagerDep
) -> NewcamdActionOut:
    """Conecta una cuenta (handshake y login Newcamd)."""
    row = _get_row(db, account_id)
    if not row.enabled:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail="La cuenta está deshabilitada")
    try:
        status_obj = manager.connect(mgr.to_domain(row, settings))
    except mgr.NewcamdAuthError as exc:
        raise HTTPException(status_code=http_status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except mgr.NewcamdError as exc:
        raise HTTPException(status_code=http_status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return NewcamdActionOut(
        ok=status_obj.state == "logged_in",
        id=row.id,
        status=NewcamdStatusOut(**status_obj.to_dict()),
    )


@router.post("/accounts/{account_id}/disconnect", response_model=NewcamdActionOut)
def disconnect_account(account_id: int, db: DbSession, manager: ManagerDep) -> NewcamdActionOut:
    """Cierra la conexión de una cuenta."""
    row = _get_row(db, account_id)
    manager.disconnect(row.id)
    return NewcamdActionOut(
        ok=True, id=row.id, status=NewcamdStatusOut(**manager.status(row.id).to_dict())
    )


@router.get("/accounts/{account_id}/status", response_model=NewcamdStatusOut)
def account_status(account_id: int, db: DbSession, manager: ManagerDep) -> NewcamdStatusOut:
    """Estado de conexión de una cuenta."""
    row = _get_row(db, account_id)
    return NewcamdStatusOut(**manager.status(row.id).to_dict())


@router.get("/status", response_model=dict)
def all_status(db: DbSession, settings: AppSettings, manager: ManagerDep) -> dict:
    """Estado de todas las cuentas (para refrescar la UI de una vez)."""
    return {
        "enabled": settings.newcamd_enabled,
        "accounts": {
            str(row.id): mgr.account_to_dict(row, settings, manager.status(row.id))
            for row in manager.load_accounts(db)
        },
    }


@router.get("/oscam", response_model=dict)
def oscam_status(settings: AppSettings, force: bool = Query(default=False)) -> dict:
    """Estado real de OSCam (tarjeta y lectores) leído de su webif.

    Este es el estado que sí refleja la realidad del hardware: OSCam lee la
    tarjeta por PC/SC y es quien descifra. El cliente Newcamd de Python no puede
    autenticarse contra OSCam porque su cifrado DES es propio (ver
    `app/services/oscam_status.py`).
    """
    global _OSCAM_CACHE
    url = getattr(settings, "oscam_webif_url", None)
    if _OSCAM_CACHE is None or _OSCAM_CACHE.url != url:
        _OSCAM_CACHE = oscam.OscamStatusCache(url)
    return _OSCAM_CACHE.get(force=force).to_dict()


@router.post("/test", response_model=NewcamdTestOut)
def test_connection(
    payload: NewcamdTestIn, db: DbSession, settings: AppSettings, manager: ManagerDep
) -> NewcamdTestOut:
    """Prueba una conexión Newcamd sin dejarla abierta.

    Si se envía `id` se usan los datos guardados (incluida la contraseña
    cifrada); los campos enviados sobrescriben esos valores.
    """
    if payload.id is not None:
        row = _get_row(db, payload.id)
        account = mgr.to_domain(row, settings)
        if payload.host:
            account.host = payload.host
        account.port = payload.port
        if payload.username:
            account.username = payload.username
        if payload.password:
            account.password = payload.password
        if payload.deskey:
            account.deskey = payload.deskey
    else:
        if not payload.host:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Indica 'host' o 'id'"
            )
        from ..services.newcamd_client import NewcamdAccount

        account = NewcamdAccount(
            name="test",
            host=payload.host,
            port=payload.port,
            username=payload.username,
            password=payload.password or "",
            deskey=payload.deskey,
        )

    status_obj = manager.test(account)
    ok = status_obj.state == "logged_in"
    return NewcamdTestOut(
        ok=ok,
        state=status_obj.state,
        message=status_obj.message or ("Conexión y login correctos" if ok else "No se pudo conectar"),
        connected_at=status_obj.connected_at,
        details=NewcamdStatusOut(**status_obj.to_dict()),
    )


@router.post("/accounts/{account_id}/ecm", response_model=NewcamdEcmOut)
def send_ecm(account_id: int, payload: NewcamdEcmIn, db: DbSession, manager: ManagerDep) -> NewcamdEcmOut:
    """Envía un ECM en hexadecimal por la conexión de la cuenta (diagnóstico)."""
    row = _get_row(db, account_id)
    try:
        ecm = bytes.fromhex(payload.ecm_hex.strip().replace(" ", ""))
    except ValueError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"ECM no válido: {exc}"
        ) from exc
    try:
        sent = manager.send_ecm(row.id, ecm, service_id=payload.service_id)
    except mgr.NewcamdError as exc:
        raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return NewcamdEcmOut(
        ok=True,
        sent=sent,
        account_id=row.id,
        detail="ECM enviado" if sent else "El socket rechazó el envío",
    )


# ===========================================================================
# Perfiles Newcamd para el motor nativo (administracion desde Python)
# ===========================================================================


def _profile_out(row: NewcamdAccountModel, settings: AppSettings) -> NewcamdAccountOut:
    """Respuesta de un perfil sin secretos (solo si estan configurados)."""
    return NewcamdAccountOut(
        id=row.id,
        name=row.name,
        host=row.host,
        port=row.port,
        username=row.username or "",
        deskey=row.deskey or "",
        enabled=bool(row.enabled),
        auto_connect=bool(row.auto_connect),
        password_set=bool(row.password_enc),
        notes=row.notes or "",
        created_at=row.created_at.isoformat() if row.created_at else "",
        updated_at=row.updated_at.isoformat() if row.updated_at else "",
    )


def _profile_from_row(row: NewcamdAccountModel, settings: AppSettings) -> perfiles.NewcamdProfile:
    """Perfil (con secretos en claro, solo en memoria) a partir de la fila."""
    return perfiles.NewcamdProfile(
        name=row.name,
        host=row.host,
        port=int(row.port),
        username=row.username or "",
        password=perfiles.decrypt_secret(row.password_enc, settings),
        des_key=perfiles.decrypt_secret(row.deskey_enc, settings) or row.deskey or "",
        timeout=int(getattr(row, "timeout_s", 8) or 8),
        disable_emm=bool(getattr(row, "disable_emm", True)),
    )


@router.get("/profiles", response_model=dict)
def list_profiles(db: DbSession, settings: AppSettings) -> dict:
    """Perfiles Newcamd **sin secretos** (solo si estan configurados)."""
    filas = db.execute(select(NewcamdAccountModel).order_by(NewcamdAccountModel.name)).scalars().all()
    items = []
    for row in filas:
        perfil = _profile_from_row(row, settings)
        datos = perfil.public_dict()
        datos["id"] = row.id
        datos["enabled"] = bool(row.enabled)
        datos["notes"] = row.notes
        items.append(datos)
    return {
        "total": len(items),
        "items": items,
        "engine": {
            "config_path": str(_config_path(settings)),
            "native_newcamd": True,
            "description": (
                "Python administra los perfiles; el motor nativo (Astra/Forkcastra) "
                "conecta por Newcamd, pide ECM y descifra con FFdecsa/libdvbcsa."
            ),
        },
    }


def _config_path(settings: AppSettings):
    from pathlib import Path

    ruta = getattr(settings, "engine_config_path", None) or "stream.lua"
    ruta = str(ruta)
    if not ruta.startswith("/"):
        from pathlib import Path as _Path

        ruta = str(_Path(getattr(settings, "data_dir", ".")) / ruta)
    return Path(ruta)


@router.get("/config", response_model=NewcamdConfigOut)
def get_config(db: DbSession, settings: AppSettings) -> NewcamdConfigOut:
    """Configuracion Lua que se generaria con los perfiles actuales."""
    filas = db.execute(
        select(NewcamdAccountModel).where(NewcamdAccountModel.enabled.is_(True)).order_by(NewcamdAccountModel.name)
    ).scalars().all()
    perfiles_lua = []
    for row in filas:
        perfil = _profile_from_row(row, settings)
        try:
            perfil.validate()
        except perfiles.ProfileError as exc:
            logger.warning("Perfil %s incompleto: %s", row.name, exc)
            continue
        perfiles_lua.append(perfil)

    ruta = _config_path(settings)
    existente = None
    if ruta.is_file():
        try:
            existente = ruta.read_text(encoding="utf-8", errors="replace")[:2000]
        except OSError:
            existente = None
    return NewcamdConfigOut(
        path=str(ruta),
        exists=ruta.is_file(),
        profiles=len(perfiles_lua),
        lua=perfiles.render_config(perfiles_lua, []),
        current=existente,
    )


@router.post("/config/apply", response_model=NewcamdApplyOut)
def apply_config(db: DbSession, settings: AppSettings) -> NewcamdApplyOut:
    """Genera e instala la configuracion del motor (con copia de seguridad)."""
    if not settings.newcamd_enabled:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Newcamd esta deshabilitado (TBS_NEWCAMD_ENABLED=0)",
        )
    filas = db.execute(
        select(NewcamdAccountModel).where(NewcamdAccountModel.enabled.is_(True)).order_by(NewcamdAccountModel.name)
    ).scalars().all()

    validos: list[perfiles.NewcamdProfile] = []
    for row in filas:
        perfil = _profile_from_row(row, settings)
        try:
            perfil.validate()
        except perfiles.ProfileError as exc:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"El perfil '{row.name}' no es valido: {exc}",
            ) from exc
        validos.append(perfil)

    lua = perfiles.render_config(validos, [])
    resultado = perfiles.apply_config(_config_path(settings), lua)
    if not resultado.ok:
        raise HTTPException(status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR, detail=resultado.detail)
    return NewcamdApplyOut(**resultado.to_dict())


@router.post("/config/rollback", response_model=NewcamdApplyOut)
def rollback(settings: AppSettings) -> NewcamdApplyOut:
    """Restaura la configuracion anterior del motor."""
    resultado = perfiles.rollback_config(_config_path(settings))
    if not resultado.ok:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=resultado.detail)
    return NewcamdApplyOut(**resultado.to_dict())


@router.post("/profiles/{account_id}/test", response_model=NewcamdProfileTestOut)
def test_profile(
    account_id: int,
    db: DbSession,
    settings: AppSettings,
    timeout: float = Query(default=3.0, gt=0, le=30),
) -> NewcamdProfileTestOut:
    """Comprueba que host:puerto aceptan TCP.

    Solo valida la red: **no** confirma login Newcamd, tarjeta ni ECM. Esa
    validacion la reporta el motor nativo (pestana Newcamd, estado de OSCam).
    """
    row = _get_row(db, account_id)
    perfil = _profile_from_row(row, settings)
    if not perfil.host:
        raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY, detail="El perfil no tiene host")
    resultado = perfiles.test_tcp_connection(perfil.host, perfil.port, timeout=timeout)
    return NewcamdProfileTestOut(
        ok=bool(resultado.get("reachable")),
        reachable=bool(resultado.get("reachable")),
        host=perfil.host,
        port=perfil.port,
        latency_ms=resultado.get("latency_ms"),
        detail=resultado.get("detail") or resultado.get("error") or "",
        note="TCP solamente: el login Newcamd y el ECM los valida el motor nativo.",
    )


@router.post("/profiles", response_model=NewcamdAccountOut, status_code=http_status.HTTP_201_CREATED)
def create_profile(
    payload: NewcamdAccountIn, db: DbSession, settings: AppSettings
) -> NewcamdAccountOut:
    """Crea un perfil guardando contrasena y DES key **cifradas** (AES-GCM)."""
    if not settings.newcamd_enabled:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Newcamd esta deshabilitado (TBS_NEWCAMD_ENABLED=0)",
        )
    existente = db.execute(
        select(NewcamdAccountModel).where(NewcamdAccountModel.name == payload.name)
    ).scalars().first()
    if existente is not None:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT, detail="Ya existe un perfil con ese nombre"
        )

    perfil = perfiles.NewcamdProfile(
        name=payload.name,
        host=payload.host,
        port=payload.port,
        username=payload.username,
        password=payload.password or "",
        des_key=(payload.deskey or "").lower(),
        timeout=int(getattr(payload, "timeout_s", 8) or 8),
        disable_emm=bool(getattr(payload, "disable_emm", True)),
    )
    try:
        perfil.validate()
    except perfiles.ProfileError as exc:
        raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    row = NewcamdAccountModel(
        name=perfil.name,
        host=perfil.host,
        port=perfil.port,
        username=perfil.username,
        password_enc=perfiles.encrypt_secret(perfil.password, settings),
        deskey=perfil.des_key,
        deskey_enc=perfiles.encrypt_secret(perfil.des_key, settings),
        timeout_s=perfil.timeout,
        disable_emm=perfil.disable_emm,
        enabled=bool(payload.enabled),
        auto_connect=bool(payload.auto_connect),
        notes=payload.notes,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _profile_out(row, settings)


@router.put("/profiles/{account_id}", response_model=NewcamdAccountOut)
def update_profile(
    account_id: int, payload: NewcamdAccountIn, db: DbSession, settings: AppSettings
) -> NewcamdAccountOut:
    """Edita un perfil; los secretos vacios se conservan."""
    row = _get_row(db, account_id)
    perfil_actual = _profile_from_row(row, settings)
    perfil = perfiles.NewcamdProfile(
        name=payload.name,
        host=payload.host,
        port=payload.port,
        username=payload.username,
        password=payload.password or perfil_actual.password,
        des_key=(payload.deskey or perfil_actual.des_key or "").lower(),
        timeout=int(getattr(payload, "timeout_s", perfil_actual.timeout) or 8),
        disable_emm=bool(getattr(payload, "disable_emm", perfil_actual.disable_emm)),
    )
    try:
        perfil.validate()
    except perfiles.ProfileError as exc:
        raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    row.name = perfil.name
    row.host = perfil.host
    row.port = perfil.port
    row.username = perfil.username
    row.password_enc = perfiles.encrypt_secret(perfil.password, settings)
    row.deskey = perfil.des_key
    row.deskey_enc = perfiles.encrypt_secret(perfil.des_key, settings)
    row.timeout_s = perfil.timeout
    row.disable_emm = perfil.disable_emm
    row.enabled = bool(payload.enabled)
    row.auto_connect = bool(payload.auto_connect)
    row.notes = payload.notes
    db.commit()
    db.refresh(row)
    return _profile_out(row, settings)


@router.delete("/profiles/{account_id}", response_model=OkOut)
def delete_profile(account_id: int, db: DbSession) -> OkOut:
    """Borra un perfil (los secretos cifrados desaparecen con el)."""
    row = _get_row(db, account_id)
    db.execute(delete(NewcamdAccountModel).where(NewcamdAccountModel.id == row.id))
    db.commit()
    return OkOut(ok=True, detail=f"Perfil '{row.name}' borrado")



# ===========================================================================
# Asignacion de un perfil (softcam) a cada canal escaneado
# Modelo portado del panel anterior: el motor recibe la opcion `cam` en la
# entrada DVB del canal, con el bloque newcamd del perfil.
# ===========================================================================


@router.put("/services/{service_id}/cam", response_model=dict)
def assign_cam_to_service(
    service_id: int,
    payload: dict,
    db: DbSession,
    settings: AppSettings,
) -> dict:
    """Asigna (o quita, con `cam_name: null`) un perfil Newcamd a un canal.

    Guarda el nombre del perfil en el canal y devuelve el bloque `cam` que el
    motor nativo usara en la entrada DVB (`dvb://tuner#pnr=101&cam=perfil`).
    """
    from ..models import Service

    fila = db.get(Service, service_id)
    if fila is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Canal no encontrado")

    nombre = payload.get("cam_name")
    if nombre in (None, "", "none", "__none"):
        fila.cam_name = None
        db.commit()
        return {"ok": True, "service_id": service_id, "cam": None, "detail": "CAM quitado del canal"}

    if not isinstance(nombre, str) or not perfiles.PROFILE_NAME.fullmatch(nombre):
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Nombre de perfil invalido",
        )

    row = db.execute(
        select(NewcamdAccountModel).where(NewcamdAccountModel.name == nombre)
    ).scalars().first()
    if row is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Ese perfil no existe")

    perfil = _profile_from_row(row, settings)
    try:
        perfil.validate()
    except perfiles.ProfileError as exc:
        raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    fila.cam_name = perfil.name
    db.commit()
    return {
        "ok": True,
        "service_id": service_id,
        "cam": perfiles.cam_block(perfil),
        "dvb_url": f"dvb://tuner_1#pnr={fila.service_id}&cam={perfil.name}",
        "detail": f"Perfil '{perfil.name}' asignado al canal",
    }


@router.get("/services/cam", response_model=dict)
def services_with_cam(db: DbSession) -> dict:
    """Canales con perfil Newcamd asignado (para la vista de canales)."""
    from ..models import Service

    filas = db.execute(
        select(Service).where(Service.cam_name.isnot(None)).order_by(Service.name)
    ).scalars().all()
    return {
        "total": len(filas),
        "items": [
            {
                "id": fila.id,
                "name": fila.custom_name or fila.name,
                "service_id": fila.service_id,
                "cam_name": fila.cam_name,
                "scrambled": bool(fila.scrambled),
                "is_fta": bool(fila.is_fta),
            }
            for fila in filas
        ],
    }
