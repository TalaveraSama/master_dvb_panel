"""Canales guardados: listado con filtros, renombrado y borrado."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..dvb.base import FrontendError
from ..deps import DbSession
from ..schemas import (
    ChannelListOut,
    ChannelPatch,
    OkOut,
    ServiceOut,
    TransponderListOut,
    TransponderOut,
    TransponderPatch,
    TransponderTuneRequest,
    TuneRequest,
    TuneResult,
)
from ..services import channels_service
from ..services.device_manager import stats_to_dict
from ._common import DeviceManagerDep, frontend_error_to_http, service_to_out, to_tune_params

logger = logging.getLogger(__name__)

router = APIRouter(tags=["channels"])


@router.get("/channels", response_model=ChannelListOut)
def list_channels(
    db: DbSession,
    q: str | None = Query(default=None, description="Búsqueda por nombre/proveedor"),
    type: str | None = Query(default=None, description="TV | Radio | Data | HDTV"),
    fta: bool | None = Query(default=None),
    scrambled: bool | None = Query(default=None),
    transponder_id: int | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> ChannelListOut:
    """Lista de canales guardados con búsqueda y filtros."""
    total, items = channels_service.list_channels(
        db,
        q=q,
        service_type=type,
        fta=fta,
        scrambled=scrambled,
        transponder_id=transponder_id,
        limit=limit,
        offset=offset,
    )
    return ChannelListOut(total=total, items=[service_to_out(item) for item in items])


@router.patch("/channels/{channel_id}", response_model=ServiceOut)
def patch_channel(channel_id: int, payload: ChannelPatch, db: DbSession) -> ServiceOut:
    """Renombra un canal (nombre real o alias personalizado)."""
    row = channels_service.rename_channel(
        db, channel_id, name=payload.name, custom_name=payload.custom_name
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Canal no encontrado")
    return service_to_out(row)


@router.delete("/channels/{channel_id}", response_model=OkOut)
def delete_channel(channel_id: int, db: DbSession) -> OkOut:
    """Borra un canal."""
    deleted = channels_service.delete_channels(db, [channel_id])
    if deleted == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Canal no encontrado")
    return OkOut(ok=True, detail=f"{deleted} canal(es) borrado(s)")


@router.delete("/channels", response_model=OkOut)
def delete_channels(
    db: DbSession,
    ids: str | None = Query(default=None, description="Lista de ids separada por comas"),
    payload: dict | None = Body(default=None),
) -> OkOut:
    """Borra varios canales o todos (`{"all": true}`)."""
    if payload and payload.get("all"):
        count = channels_service.delete_all_channels(db)
        return OkOut(ok=True, detail=f"{count} canal(es) borrado(s)")
    parsed: list[int] = []
    if ids:
        try:
            parsed = [int(part) for part in ids.split(",") if part.strip()]
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ids inválidos") from exc
    if payload and payload.get("ids"):
        parsed.extend(int(value) for value in payload["ids"])
    if not parsed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Indica ?ids=1,2 o {\"all\": true}",
        )
    count = channels_service.delete_channels(db, parsed)
    return OkOut(ok=True, detail=f"{count} canal(es) borrado(s)")


@router.get("/transponders", response_model=TransponderListOut)
def list_transponders(
    db: DbSession,
    q: str | None = None,
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
) -> TransponderListOut:
    """Transponders guardados con su número de servicios."""
    total, rows = channels_service.list_transponders(db, q=q, limit=limit, offset=offset)
    return TransponderListOut(
        total=total,
        items=[_transponder_out(row, count) for row, count in rows],
    )


def _transponder_out(row, services_count: int) -> TransponderOut:
    return TransponderOut(
        id=row.id,
        delivery_system=row.delivery_system,
        frequency_khz=row.frequency_khz,
        polarization=row.polarization,
        symbol_rate_ksps=row.symbol_rate_ksps,
        fec=row.fec,
        modulation=row.modulation,
        bandwidth_mhz=row.bandwidth_mhz,
        inversion=row.inversion or "AUTO",
        rolloff=row.rolloff,
        pilot=row.pilot,
        stream_id=row.stream_id,
        plp_id=row.plp_id,
        sat_position=row.sat_position,
        last_signal_percent=row.last_signal_percent,
        last_snr_db=row.last_snr_db,
        label=row.label,
        services_count=services_count,
        updated_at=row.updated_at,
    )


@router.get("/transponders/{transponder_id}", response_model=TransponderOut)
def get_transponder(transponder_id: int, db: DbSession) -> TransponderOut:
    """Un transponder concreto."""
    row = channels_service.get_transponder(db, transponder_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transponder no encontrado")
    return _transponder_out(row, len(row.services or []))


@router.patch("/transponders/{transponder_id}", response_model=TransponderOut)
def patch_transponder(transponder_id: int, payload: TransponderPatch, db: DbSession) -> TransponderOut:
    """Edita frecuencia, symbol rate, polaridad, FEC, modulación, satélite, etc."""
    changes = payload.model_dump(exclude_none=True)
    try:
        row = channels_service.update_transponder(db, transponder_id, changes)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transponder no encontrado")
    return _transponder_out(row, len(row.services or []))


@router.delete("/transponders/{transponder_id}", response_model=OkOut)
def delete_transponder(transponder_id: int, db: DbSession) -> OkOut:
    """Borra un transponder junto con sus canales."""
    transponders, services = channels_service.delete_transponders(db, [transponder_id])
    if transponders == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transponder no encontrado")
    return OkOut(ok=True, detail=f"Transponder borrado ({services} canal(es) eliminado(s))")


@router.post("/transponders/{transponder_id}/tune", response_model=TuneResult)
def tune_transponder(
    transponder_id: int,
    payload: TransponderTuneRequest,
    db: DbSession,
    device: DeviceManagerDep,
) -> TuneResult:
    """Sintoniza un transponder guardado en el **puerto de sintonizador** elegido.

    Permite indicar qué adaptador/frontend (puerto) se usa, de modo que se puede
    tener varios sintonizadores y repartir el trabajo entre ellos.
    """
    row = channels_service.get_transponder(db, transponder_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transponder no encontrado")

    params = to_tune_params(
        TuneRequest(
            adapter=payload.adapter,
            frontend=payload.frontend,
            delivery_system=row.delivery_system,
            frequency_khz=row.frequency_khz,
            symbol_rate_ksps=row.symbol_rate_ksps,
            polarization=row.polarization,
            fec=row.fec,
            modulation=row.modulation,
            bandwidth_mhz=row.bandwidth_mhz,
            inversion=row.inversion or "AUTO",
            rolloff=row.rolloff,
            pilot=row.pilot,
            stream_id=row.stream_id,
            plp_id=row.plp_id,
            use_lnb=payload.use_lnb,
            lnb_low_khz=payload.lnb_low_khz,
            lnb_high_khz=payload.lnb_high_khz,
            lnb_threshold_khz=payload.lnb_threshold_khz,
            tone_22k=payload.tone_22k,
            voltage=payload.voltage,
            diseqc=payload.diseqc,
            lock_timeout_s=payload.lock_timeout_s,
        )
    )
    try:
        stats = device.tune(params, owner=f"api:tune-tp{transponder_id}")
    except FrontendError as exc:
        raise frontend_error_to_http(exc) from exc

    from ..schemas import SignalStatsOut

    stats_out = SignalStatsOut(**stats_to_dict(stats)) if stats else None
    detail = None
    if stats is not None and not stats.lock.is_locked:
        detail = stats.error or (
            "Sintonizado pero sin LOCK. Comprueba la antena/LNB o prueba otro puerto "
            "de sintonizador (el adaptador %s puede no tener señal)." % payload.adapter
        )
    return TuneResult(
        ok=True,
        locked=bool(stats and stats.lock.is_locked),
        adapter=params.adapter,
        frontend=params.frontend,
        mock=device.mock,
        frequency_khz=params.frequency_khz,
        if_frequency_khz=params.if_frequency_khz,
        delivery_system=params.delivery_system,
        lock_timeout_s=params.lock_timeout_s,
        stats=stats_out,
        detail=detail,
    )
