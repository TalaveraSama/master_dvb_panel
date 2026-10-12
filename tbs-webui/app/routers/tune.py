"""Sintonización manual y liberación del dispositivo."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status

from ..dvb.base import FrontendError
from ..schemas import OkOut, TuneRequest, TuneResult
from ..services.device_manager import stats_to_dict
from ._common import DeviceManagerDep, frontend_error_to_http, to_tune_params

logger = logging.getLogger(__name__)

router = APIRouter(tags=["tune"])


@router.post("/tune", response_model=TuneResult)
def tune(payload: TuneRequest, device: DeviceManagerDep) -> TuneResult:
    """Sintoniza el transponder indicado y devuelve las estadísticas."""
    params = to_tune_params(payload)
    try:
        stats = device.tune(params, owner="api:tune")
    except FrontendError as exc:
        logger.warning("Sintonización fallida: %s", exc)
        raise frontend_error_to_http(exc) from exc

    from ..schemas import SignalStatsOut

    stats_out = SignalStatsOut(**stats_to_dict(stats)) if stats else None
    detail = None
    if stats is not None and not stats.lock.is_locked:
        if stats.error:
            detail = f"Sin LOCK: {stats.error}"
        else:
            detail = (
                "Sintonizado pero sin LOCK. Comprueba frecuencia, polaridad, symbol rate "
                "y la alimentación del LNB (13/18 V, tono 22 kHz)."
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


@router.post("/tune/release", response_model=OkOut)
def release(device: DeviceManagerDep) -> OkOut:
    """Libera el frontend (deja de leer señal y cierra el dispositivo)."""
    device.release()
    return OkOut(ok=True, detail="Dispositivo liberado")
