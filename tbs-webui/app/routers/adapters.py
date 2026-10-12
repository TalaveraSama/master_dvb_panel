"""Adaptadores y frontends DVB disponibles."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from ..dvb.base import DeviceNotFoundError, FrontendError
from ..dvb.types import FrontendInfo
from ..schemas import AdapterOut, AdaptersOut, LockStateOut
from ._common import DeviceManagerDep, lock_snapshot

router = APIRouter(prefix="/adapters", tags=["adapters"])


def frontend_to_out(info: FrontendInfo, mock: bool) -> AdapterOut:
    """Convierte la info del frontend al JSON de la API."""
    return AdapterOut(
        adapter=info.adapter,
        frontend=info.frontend,
        name=info.name,
        device_path=info.device_path,
        type=info.type,
        delivery_systems=list(info.delivery_systems),
        min_frequency=info.min_frequency,
        max_frequency=info.max_frequency,
        symbol_rates=list(info.symbol_rates or ()),
        mock=mock,
    )


@router.get("", response_model=AdaptersOut)
def list_adapters(device: DeviceManagerDep) -> AdaptersOut:
    """Lista adaptadores/frontends detectados y el estado del bloqueo."""
    try:
        infos = device.list_adapters()
    except FrontendError as exc:  # pragma: no cover - defensivo
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    snapshot = lock_snapshot()
    return AdaptersOut(
        mock=device.mock,
        locked=LockStateOut(
            owner=snapshot.get("owner"),
            acquired_at=snapshot.get("acquired_at"),
            waiting=int(snapshot.get("waiting", 0) or 0),
        ),
        adapters=[frontend_to_out(info, device.mock) for info in infos],
    )


@router.get("/{adapter}/{frontend}/info", response_model=AdapterOut)
def adapter_info(adapter: int, frontend: int, device: DeviceManagerDep) -> AdapterOut:
    """Información detallada de un frontend concreto."""
    try:
        infos = device.list_adapters()
    except FrontendError as exc:  # pragma: no cover
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    for info in infos:
        if info.adapter == adapter and info.frontend == frontend:
            return frontend_to_out(info, device.mock)
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"No existe el frontend {adapter}/{frontend}",
    )
