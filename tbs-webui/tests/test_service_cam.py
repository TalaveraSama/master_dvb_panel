"""Tests de asignacion de perfil Newcamd (softcam) a cada canal."""

from __future__ import annotations

import itertools

from app.database import get_sessionmaker
from app.models import Service, Transponder

_CONTADOR = itertools.count()


def _perfil(**cambios) -> dict:
    base = {
        "name": f"cam_{next(_CONTADOR)}",
        "host": "192.168.1.50",
        "port": 15000,
        "username": "usuario",
        "password": "secreto",
        "deskey": "0102030405060708091011121314",
        "timeout_s": 8,
        "disable_emm": True,
        "enabled": True,
        "auto_connect": False,
    }
    base.update(cambios)
    return base


def _canal() -> int:
    """Crea un transponder con un canal y devuelve el id del canal."""
    sesion = get_sessionmaker()()
    try:
        tp = Transponder(delivery_system="DVB-S2", frequency_khz=10970000, polarization="V", symbol_rate_ksps=27900)
        sesion.add(tp)
        sesion.flush()
        canal = Service(
            transponder_id=tp.id,
            service_id=101,
            name="A&E HD",
            provider="Prov",
            service_type=25,
            service_type_label="HDTV",
            is_fta=False,
            scrambled=True,
        )
        sesion.add(canal)
        sesion.commit()
        return canal.id
    finally:
        sesion.close()


def test_asignar_perfil_a_canal(admin_client):
    perfil = admin_client.post("/api/newcamd/profiles", json=_perfil()).json()
    canal_id = _canal()

    respuesta = admin_client.put(f"/api/newcamd/services/{canal_id}/cam", json={"cam_name": perfil["name"]})
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["ok"] is True
    assert cuerpo["cam"]["softcam"] == perfil["name"]
    assert cuerpo["cam"]["host"] == "192.168.1.50"
    assert cuerpo["cam"]["port"] == 15000
    assert cuerpo["cam"]["disable_emm"] == 1
    assert "password" not in cuerpo["cam"] or cuerpo["cam"].get("pass") == "secreto"
    assert f"cam={perfil['name']}" in cuerpo["dvb_url"]


def test_quitar_perfil_de_canal(admin_client):
    perfil = admin_client.post("/api/newcamd/profiles", json=_perfil()).json()
    canal_id = _canal()
    admin_client.put(f"/api/newcamd/services/{canal_id}/cam", json={"cam_name": perfil["name"]})

    quitado = admin_client.put(f"/api/newcamd/services/{canal_id}/cam", json={"cam_name": None})
    assert quitado.status_code == 200
    assert quitado.json()["cam"] is None
    assert admin_client.get("/api/newcamd/services/cam").json()["total"] == 0


def test_listar_canales_con_cam(admin_client):
    perfil = admin_client.post("/api/newcamd/profiles", json=_perfil()).json()
    canal_id = _canal()
    admin_client.put(f"/api/newcamd/services/{canal_id}/cam", json={"cam_name": perfil["name"]})

    datos = admin_client.get("/api/newcamd/services/cam").json()
    assert datos["total"] == 1
    item = datos["items"][0]
    assert item["id"] == canal_id
    assert item["cam_name"] == perfil["name"]
    assert item["name"] == "A&E HD"
    assert item["scrambled"] is True


def test_perfil_inexistente(admin_client):
    canal_id = _canal()
    respuesta = admin_client.put(f"/api/newcamd/services/{canal_id}/cam", json={"cam_name": "no_existe"})
    assert respuesta.status_code == 404


def test_canal_inexistente(admin_client):
    assert (
        admin_client.put("/api/newcamd/services/999999/cam", json={"cam_name": "x"}).status_code == 404
    )


def test_nombre_de_perfil_invalido(admin_client):
    canal_id = _canal()
    respuesta = admin_client.put(f"/api/newcamd/services/{canal_id}/cam", json={"cam_name": "1 malo"})
    assert respuesta.status_code == 422


def test_requiere_sesion(client):
    assert client.put("/api/newcamd/services/1/cam", json={"cam_name": "x"}).status_code == 401
    assert client.get("/api/newcamd/services/cam").status_code == 401


def test_perfil_incompleto_no_se_asigna(admin_client):
    """Un perfil sin contrasena guardada no se puede asignar a un canal."""
    from app.models import NewcamdAccount

    creado = admin_client.post("/api/newcamd/profiles", json=_perfil()).json()
    sesion = get_sessionmaker()()
    try:
        fila = sesion.query(NewcamdAccount).filter(NewcamdAccount.id == creado["id"]).one()
        fila.password_enc = ""
        sesion.commit()
    finally:
        sesion.close()

    canal_id = _canal()
    respuesta = admin_client.put(
        f"/api/newcamd/services/{canal_id}/cam", json={"cam_name": creado["name"]}
    )
    assert respuesta.status_code == 422
