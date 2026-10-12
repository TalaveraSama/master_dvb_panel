"""Descubrimiento de frontends, factoría y lock global del hardware.

Contrato: INTERFACES.md §4::

    class DeviceLock:   # threading.RLock + estado de ocupación
        def acquire(self, owner, timeout=None) -> None   # DeviceBusyError
        def release(self, owner) -> None
        @property owner -> str | None
        def snapshot() -> dict

    list_frontends(force_mock=None) -> list[FrontendInfo]
    open_frontend(adapter, frontend=0) -> Frontend
    get_lock() -> DeviceLock
    is_mock_mode() -> bool

Notas de implementación:

* `acquire()` devuelve ``True`` al conseguirlo (los consumidores de
  `app/services/**` comprueban el valor de retorno) y lanza `DeviceBusyError`
  si está ocupado, tal y como pide el contrato. Con ``timeout=None`` no espera.
* En Windows (o sin ``/dev/dvb``) todo el módulo funciona en modo mock, que es
  lo que permite ejecutar los tests sin hardware.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Iterator

from .base import Frontend
from .frontend_ioctl import IoctlFrontend, read_frontend_info
from .mock import MockFrontend, mock_frontend_infos
from .types import (
    DeviceBusyError,
    DeviceNotFoundError,
    DvbError,
    FrontendError,
    FrontendInfo,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DeviceLock",
    "get_lock",
    "reset_lock",
    "list_frontends",
    "open_frontend",
    "is_mock_mode",
    "is_tools_mode",
    "device_path",
    "devices_present",
    "sysfs_name",
    "LOCK_OWNER_IDLE",
    "MOCK_ENV_VAR",
    "TOOLS_ENV_VAR",
]

DEV_DVB_ROOT = Path("/dev/dvb")
SYSFS_DVB_ROOT = Path("/sys/class/dvb")
MOCK_ENV_VAR = "TBS_MOCK"
TOOLS_ENV_VAR = "TBS_IOCTL"
_TRUE_VALUES = {"1", "true", "yes", "on", "si", "sí"}

LOCK_OWNER_IDLE = "idle"

# Caché del sondeo de /dev/dvb (evita un glob por cada llamada en bucles).
_DEVS_CACHE: tuple[float, bool] | None = None
_DEVS_CACHE_TTL = 2.0
_MOCK_WARNED = False


# ---------------------------------------------------------------------------
# Modo mock
# ---------------------------------------------------------------------------
def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUE_VALUES


def devices_present() -> bool:
    """True si existe al menos un `/dev/dvb/adapter*` (con caché de 2 s)."""
    global _DEVS_CACHE
    now = time.monotonic()
    if _DEVS_CACHE is not None and now - _DEVS_CACHE[0] < _DEVS_CACHE_TTL:
        return _DEVS_CACHE[1]
    try:
        present = bool(list(DEV_DVB_ROOT.glob("adapter*")))
    except OSError:  # pragma: no cover - permisos o FS raro
        present = False
    _DEVS_CACHE = (now, present)
    return present


def is_mock_mode() -> bool:
    """True si hay que simular: ``TBS_MOCK=1`` o no existe ``/dev/dvb``."""
    global _MOCK_WARNED
    if _env_truthy(MOCK_ENV_VAR):
        return True
    if devices_present():
        return False
    if not _MOCK_WARNED:
        logger.warning(
            "No se encontró %s: se activa el modo simulación (mock). "
            "Define TBS_MOCK=0 para forzar el hardware real.",
            DEV_DVB_ROOT,
        )
        _MOCK_WARNED = True
    return True


def is_tools_mode() -> bool:
    """True si se prefiere el plan B (herramientas dvbv5-zap/dvbv5-scan).

    Se activa con ``TBS_IOCTL=0`` o ``TBS_FRONTEND=tools``. El modo preferido
    sigue siendo el ioctl directo (`IoctlFrontend`).
    """
    if os.environ.get("TBS_FRONTEND", "").strip().lower() in ("tools", "dvbv5", "zap"):
        return True
    value = os.environ.get(TOOLS_ENV_VAR)
    if value is None:
        return False
    return value.strip().lower() in {"0", "false", "no", "off"}


# ---------------------------------------------------------------------------
# Lock global del dispositivo
# ---------------------------------------------------------------------------
class DeviceLock:
    """Lock reentrante con dueño identificado (un único dueño del hardware).

    ``snapshot()`` devuelve ``{"owner", "acquired_at", "waiting", ...}`` tal y
    como espera ``GET /api/adapters``.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._owner: str | None = None
        self._acquired_at: float | None = None
        self._depth = 0
        self._waiting = 0

    # -- estado ------------------------------------------------------------
    @property
    def owner(self) -> str | None:
        """Nombre del dueño actual (``None`` si está libre)."""
        with self._condition:
            return self._owner

    @property
    def is_locked(self) -> bool:
        with self._condition:
            return self._owner is not None

    @property
    def waiting(self) -> int:
        with self._condition:
            return self._waiting

    def snapshot(self) -> dict[str, object]:
        """Estado del lock para la API."""
        with self._condition:
            return {
                "owner": self._owner,
                "acquired_at": self._acquired_at,
                "waiting": self._waiting,
                "depth": self._depth,
                "held_seconds": (
                    None if self._acquired_at is None else round(time.time() - self._acquired_at, 3)
                ),
                "locked": self._owner is not None,
            }

    def owned_by(self, owner: str) -> bool:
        with self._condition:
            return self._owner == str(owner)

    # -- adquisición -------------------------------------------------------
    def acquire(self, owner: str, timeout: float | None = None) -> bool:
        """Adquiere el lock. `DeviceBusyError` si está ocupado por otro dueño.

        * Si el mismo dueño ya lo tiene, es reentrante (aumenta la profundidad).
        * ``timeout=None`` o ``<= 0``: no espera, falla de inmediato.
        * Devuelve ``True`` al conseguirlo (los servicios comprueban el valor).
        """
        name = str(owner or LOCK_OWNER_IDLE)
        with self._condition:
            if self._owner == name:
                self._depth += 1
                return True
            if self._owner is None:
                self._owner = name
                self._acquired_at = time.time()
                self._depth = 1
                return True

            holder = self._owner
            if timeout is None or float(timeout) <= 0:
                raise DeviceBusyError(
                    f"El dispositivo está ocupado por '{holder}' (dueño del frontend)"
                )
            self._waiting += 1
            try:
                deadline = time.monotonic() + float(timeout)
                while self._owner is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise DeviceBusyError(
                            f"El dispositivo sigue ocupado por '{self._owner}' "
                            f"tras esperar {timeout} s"
                        )
                    self._condition.wait(remaining)
                self._owner = name
                self._acquired_at = time.time()
                self._depth = 1
                return True
            finally:
                self._waiting -= 1

    def try_acquire(self, owner: str) -> bool:
        """Intento no bloqueante: True si se consiguió, False si está ocupado."""
        try:
            return self.acquire(owner, timeout=None)
        except DeviceBusyError:
            return False

    def release(self, owner: str) -> None:
        """Libera el lock. Idempotente si ya estaba libre; `DvbError` si es de otro."""
        name = str(owner or LOCK_OWNER_IDLE)
        with self._condition:
            if self._owner is None:
                return
            if self._owner != name:
                raise DvbError(
                    f"El lock pertenece a '{self._owner}', no se puede liberar desde '{name}'"
                )
            self._depth -= 1
            if self._depth <= 0:
                self._owner = None
                self._acquired_at = None
                self._depth = 0
                self._condition.notify_all()

    def force_release(self) -> None:
        """Libera el lock sea quien sea el dueño (apagado ordenado/tests)."""
        with self._condition:
            self._owner = None
            self._acquired_at = None
            self._depth = 0
            self._condition.notify_all()

    def hold(self, owner: str, timeout: float | None = None) -> "_LockHolder":
        """Context manager: ``with get_lock().hold("tune"): ...``."""
        return _LockHolder(self, owner, timeout)

    def __repr__(self) -> str:  # pragma: no cover - depuración
        state = self.snapshot()
        return f"<DeviceLock owner={state['owner']!r} waiting={state['waiting']}>"


class _LockHolder:
    """Context manager que garantiza la liberación del `DeviceLock`."""

    def __init__(self, lock: DeviceLock, owner: str, timeout: float | None = None) -> None:
        self._lock = lock
        self._owner = owner
        self._timeout = timeout

    def __enter__(self) -> DeviceLock:
        self._lock.acquire(self._owner, self._timeout)
        return self._lock

    def __exit__(self, exc_type, exc, tb) -> None:
        self._lock.release(self._owner)


_LOCK = DeviceLock()


def get_lock() -> DeviceLock:
    """Lock global compartido por sintonización y escaneo."""
    return _LOCK


def reset_lock() -> DeviceLock:
    """Sustituye el lock global (sólo tests)."""
    global _LOCK
    _LOCK = DeviceLock()
    return _LOCK


# ---------------------------------------------------------------------------
# Descubrimiento
# ---------------------------------------------------------------------------
_DEVICE_RE = re.compile(r"^adapter(\d+)$")


def device_path(adapter: int, frontend: int = 0) -> str:
    """Ruta del dispositivo de un frontend."""
    return f"/dev/dvb/adapter{int(adapter)}/frontend{int(frontend)}"


def iter_device_paths() -> Iterator[tuple[int, int, str]]:
    """Itera ``(adapter, frontend, device_path)`` de ``/dev/dvb``.

    Es tolerante: si el directorio no existe (Windows) devuelve una secuencia
    vacía sin lanzar.
    """
    try:
        adapters = sorted(DEV_DVB_ROOT.glob("adapter*"))
    except OSError:  # pragma: no cover
        return
    for adapter_dir in adapters:
        match = _DEVICE_RE.match(adapter_dir.name)
        if not match:
            continue
        adapter = int(match.group(1))
        try:
            frontends = sorted(adapter_dir.glob("frontend*"))
        except OSError:  # pragma: no cover
            continue
        for frontend_path in frontends:
            suffix = frontend_path.name[len("frontend") :]
            if not suffix.isdigit():
                continue
            yield adapter, int(suffix), str(frontend_path)


_SYSFS_CANDIDATES = ("name", "device/name")


def sysfs_name(adapter: int, frontend: int = 0) -> str | None:
    """Nombre del frontend desde sysfs (o ``None``).

    Los kernels 5.15 no exponen ``name`` para los frontends DVB, así que se usa
    el driver del dispositivo PCI (`device/uevent`) y, si no, ``None``.
    """
    base = SYSFS_DVB_ROOT / f"dvb{int(adapter)}.frontend{int(frontend)}"
    for candidate in _SYSFS_CANDIDATES:
        path = base / candidate
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if text:
            return text
    try:
        uevent = (base / "device" / "uevent").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r"^DRIVER=(.+)$", uevent, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return None


def _fallback_info(adapter: int, frontend: int, path: str, reason: str | None = None) -> FrontendInfo:
    """`FrontendInfo` mínimo cuando no se puede abrir el dispositivo."""
    name = sysfs_name(adapter, frontend) or f"TBS adapter{adapter}"
    if reason:
        logger.warning("No se pudo leer la info de %s (%s); se usa '%s'", path, reason, name)
    return FrontendInfo(
        adapter=int(adapter),
        frontend=int(frontend),
        name=name,
        device_path=str(path),
        type="FE_UNKNOWN",
        caps=0,
        delivery_systems=(),
        min_frequency=0,
        max_frequency=0,
        symbol_rates=(),
    )


def list_frontends(force_mock: bool | None = None) -> list[FrontendInfo]:
    """Lista los frontends disponibles (mock si no hay hardware).

    Nunca lanza si ``/dev/dvb`` no existe: en ese caso devuelve la lista del
    simulador (4 frontends como la TBS 6904se real).
    """
    mock = is_mock_mode() if force_mock is None else bool(force_mock)
    if mock:
        return mock_frontend_infos()
    infos: list[FrontendInfo] = []
    for adapter, frontend, path in iter_device_paths():
        try:
            infos.append(read_frontend_info(path, adapter, frontend))
        except FrontendError as exc:
            infos.append(_fallback_info(adapter, frontend, path, str(exc)))
    if not infos:
        logger.warning("No se encontraron frontends en %s", DEV_DVB_ROOT)
    return infos


def find_frontend_info(adapter: int, frontend: int = 0) -> FrontendInfo:
    """`FrontendInfo` de un frontend concreto (lo abre en sólo lectura)."""
    path = device_path(adapter, frontend)
    if is_mock_mode():
        # Modo mock: la info sale del simulador.
        from .mock import mock_frontend_info

        return mock_frontend_info(adapter, frontend)
    if not Path(path).exists():
        raise DeviceNotFoundError(f"No existe el frontend {path}")
    try:
        return read_frontend_info(path, adapter, frontend)
    except FrontendError as exc:
        logger.warning("No se pudo leer la info de %s: %s", path, exc)
        return _fallback_info(adapter, frontend, path, str(exc))


def open_frontend(adapter: int = 0, frontend: int = 0, *, prefer_tools: bool | None = None) -> Frontend:
    """Crea (sin abrir) el frontend indicado.

    * Modo mock (sin ``/dev/dvb`` o ``TBS_MOCK=1``) -> `MockFrontend`.
    * Modo ioctl (preferido) -> `IoctlFrontend`.
    * Plan B (``TBS_IOCTL=0``/``TBS_FRONTEND=tools`` o ``prefer_tools=True``)
      -> `ToolsFrontend` (dvbv5-zap + dvb-fe-tool).

    El llamador debe invocar ``open()`` (así lo hace
    `app/services/device_manager.py`).
    """
    adapter = int(adapter)
    frontend = int(frontend)
    if is_mock_mode():
        return MockFrontend(adapter, frontend)
    path = device_path(adapter, frontend)
    if not Path(path).exists():
        raise DeviceNotFoundError(f"No existe el frontend {path}")
    info = find_frontend_info(adapter, frontend)
    if prefer_tools is None:
        prefer_tools = is_tools_mode()
    if prefer_tools:
        from .tools_frontend import ToolsFrontend

        return ToolsFrontend(info)
    return IoctlFrontend(info)
