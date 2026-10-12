"""Salud del servicio (ruta pública, sin sesión).

Debe ir en su propio router porque el resto de la API está protegido por
dependencia de sesión a nivel de router.
"""

from __future__ import annotations

from fastapi import APIRouter

from ..schemas import HealthOut
from ._common import DeviceManagerDep

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthOut)
def health(device: DeviceManagerDep) -> HealthOut:
    """Comprobación de salud del panel (pública)."""
    from .. import __version__

    try:
        count = len(device.list_adapters())
    except Exception:  # pragma: no cover - defensivo
        count = 0
    return HealthOut(status="ok", mock=device.mock, version=__version__, adapters=count)
