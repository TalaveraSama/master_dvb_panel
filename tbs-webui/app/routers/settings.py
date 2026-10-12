"""Ajustes del panel: adaptador por defecto, LNB, sondeo y UI."""

from __future__ import annotations

import logging

from fastapi import APIRouter

from ..deps import AppSettings, DbSession
from ..schemas import SettingsIn, SettingsOut
from ..services import settings_service
from ._common import DeviceManagerDep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])


def _to_out(data: dict, settings: AppSettings, mock: bool) -> SettingsOut:
    return SettingsOut(
        default_adapter=int(data.get("default_adapter", 0)),
        default_frontend=int(data.get("default_frontend", 0)),
        mock=mock,
        lnb=data.get("lnb", {}),
        scan=data.get("scan", {}),
        ui=data.get("ui", {}),
    )


@router.get("", response_model=SettingsOut)
def get_settings_endpoint(
    db: DbSession, settings: AppSettings, device: DeviceManagerDep
) -> SettingsOut:
    """Ajustes actuales (valores por defecto + guardados)."""
    data = settings_service.load_settings(db, settings)
    return _to_out(data, settings, device.mock)


@router.put("", response_model=SettingsOut)
def update_settings(
    payload: SettingsIn, db: DbSession, settings: AppSettings, device: DeviceManagerDep
) -> SettingsOut:
    """Actualiza parcialmente los ajustes."""
    changes = payload.model_dump(exclude_none=True)
    data = settings_service.save_settings(db, changes)

    # Aplicar en caliente lo que afecta al muestreo de señal.
    ui = data.get("ui", {})
    if "signal_poll_ms" in ui:
        device.set_poll_ms(int(ui["signal_poll_ms"]))
    if "history_seconds" in ui:
        device.set_history_seconds(int(ui["history_seconds"]))

    logger.info("Ajustes actualizados: %s", ", ".join(sorted(changes.keys())) or "(sin cambios)")
    return _to_out(data, settings, device.mock)


@router.post("/reset", response_model=SettingsOut)
def reset_settings(db: DbSession, settings: AppSettings, device: DeviceManagerDep) -> SettingsOut:
    """Restaura los ajustes por defecto."""
    data = settings_service.reset_settings(db)
    return _to_out(data, settings, device.mock)
