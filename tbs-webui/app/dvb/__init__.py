"""Capa DVB de hardware real (TBS 6904se / driver tbsecp3).

Estructura (INTERFACES.md §1):

* `types`           — dataclasses/enums puros (hoja, sólo stdlib)
* `base`            — Protocol `Frontend` + errores
* `frontend_ioctl`  — implementación Linux real (ioctl DVBv5 + demux)
* `tools_frontend`  — plan B: dvbv5-zap/dvbv5-scan como subproceso
* `mock`            — simulación para desarrollo y tests
* `tools`           — envoltorios de dvbv5-scan/dvbv5-zap/dvb-fe-tool
* `diseqc`          — LNB, tono 22 kHz, voltaje y DiSEqC 1.0/1.1
* `satxml`          — tablas de sintonización de dtv-scan-tables
* `si`              — parser PSI/SI (PAT/SDT/PMT)
* `registry`        — descubrimiento, factoría y lock global

Regla de integración (§6): este paquete **nunca** importa `app.config`,
`app.models` ni FastAPI.
"""

from __future__ import annotations

from . import base, diseqc, frontend_ioctl, mock, registry, satxml, si, tools, tools_frontend, types
from .base import Frontend
from .registry import DeviceLock, get_lock, is_mock_mode, is_tools_mode, list_frontends, open_frontend
from .tools_frontend import ToolsFrontend
from .types import (
    DeviceBusyError,
    DeviceNotFoundError,
    DvbError,
    FrontendError,
    FrontendInfo,
    LockFlags,
    LockTimeoutError,
    ScanProgress,
    ServiceInfo,
    SignalStats,
    Transponder,
    TuneParams,
    UnsupportedError,
)

__all__ = [
    # submódulos
    "base",
    "diseqc",
    "frontend_ioctl",
    "mock",
    "registry",
    "satxml",
    "si",
    "tools",
    "tools_frontend",
    "types",
    # fachada
    "Frontend",
    "DeviceLock",
    "get_lock",
    "is_mock_mode",
    "is_tools_mode",
    "list_frontends",
    "open_frontend",
    "ToolsFrontend",
    "FrontendInfo",
    "LockFlags",
    "ScanProgress",
    "ServiceInfo",
    "SignalStats",
    "Transponder",
    "TuneParams",
    "FrontendError",
    "DeviceNotFoundError",
    "DeviceBusyError",
    "UnsupportedError",
    "LockTimeoutError",
    "DvbError",
]
