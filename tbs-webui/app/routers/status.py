"""Estado del dispositivo y estadísticas de señal."""

from __future__ import annotations

from fastapi import APIRouter

from ..schemas import HistoryPoint, SignalStatsOut, StatusOut
from ..services.device_manager import stats_to_dict
from ._common import DeviceManagerDep

router = APIRouter(tags=["status"])


@router.get("/status", response_model=StatusOut)
def status(device: DeviceManagerDep) -> StatusOut:
    """Estado actual: dueño del hardware, transponder sintonizado y estadísticas."""
    snapshot = device.status()
    stats = snapshot.get("stats")
    return StatusOut(
        locked_by=snapshot.get("locked_by"),
        waiting=int(snapshot.get("waiting", 0) or 0),
        mock=device.mock,
        adapter=snapshot.get("adapter"),
        frontend=snapshot.get("frontend"),
        tuned=snapshot.get("tuned"),
        stats=SignalStatsOut(**stats_to_dict(stats)) if stats is not None else None,
    )


@router.get("/status/history", response_model=list[HistoryPoint])
def history(device: DeviceManagerDep, seconds: int | None = None) -> list[HistoryPoint]:
    """Histórico corto de señal (por defecto los últimos 60 s)."""
    points = device.history(seconds)
    return [HistoryPoint(**point) for point in points]
