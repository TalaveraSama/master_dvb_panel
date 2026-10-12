"""Estado del dispositivo y estadísticas de señal."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status as http_status

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


@router.get("/tuners", response_model=dict)
def tuners(device: DeviceManagerDep) -> dict:
    """Puertos de sintonizador **en uso**, con su transponder y niveles en vivo.

    Cada puerto queda bloqueado por el transponder que tiene sintonizado, así que
    este endpoint es el que alimenta los indicadores del cuadro de mando.
    """
    return device.tuners.to_dict()


@router.post("/tuners/{adapter}/{frontend}/release", response_model=dict)
def release_tuner(adapter: int, frontend: int, device: DeviceManagerDep) -> dict:
    """Libera un puerto de sintonizador para que otro pueda usarlo."""
    tuned = device.tuned_params()
    era_el_activo = tuned is not None and tuned.adapter == adapter and tuned.frontend == frontend
    if era_el_activo:
        # Cierra el frontend y suelta la reserva
        device.release()
    liberado = device.tuners.release(adapter, frontend) or era_el_activo
    if not liberado:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail="Ese sintonizador no estaba en uso"
        )
    return {"ok": True, "detail": f"Sintonizador adapter{adapter}/frontend{frontend} liberado"}
