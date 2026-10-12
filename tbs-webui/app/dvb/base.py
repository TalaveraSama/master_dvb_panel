"""Protocolo `Frontend` y errores de la capa DVB (INTERFACES.md §3).

`base` es una hoja junto con `types`: no importa `app.config` ni FastAPI.

Los errores se definen en `types.py` (hoja pura) y se reexportan aquí porque
INTERFACES.md §1 los sitúa en este módulo; así las dos rutas de import usadas
por `app/services/**` funcionan:

    from app.dvb.base import DeviceBusyError, FrontendError
    from app.dvb.types import DeviceBusyError, FrontendError
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .types import (
    DeviceBusyError,
    DeviceNotFoundError,
    DvbError,
    FrontendError,
    FrontendInfo,
    LockFlags,
    LockTimeoutError,
    SignalStats,
    TuneParams,
    UnsupportedError,
)

__all__ = [
    "Frontend",
    "FrontendBase",
    "split_diseqc_messages",
    "FrontendInfo",
    "LockFlags",
    "SignalStats",
    "TuneParams",
    "FrontendError",
    "DeviceNotFoundError",
    "DeviceBusyError",
    "UnsupportedError",
    "LockTimeoutError",
    "DvbError",
]


@runtime_checkable
class Frontend(Protocol):
    """Interfaz mínima de un frontend DVB (INTERFACES.md §3).

    La implementa `frontend_ioctl.IoctlFrontend` (hardware real) y
    `mock.MockFrontend` (simulación). Extensión opcional para leer el TS:
    `set_pid_list()` + `read_ts()` (la usa `app/services/si_collector.py`).
    """

    info: FrontendInfo
    is_mock: bool

    def open(self) -> None: ...

    def close(self) -> None: ...

    def tune(self, params: TuneParams) -> None: ...

    def wait_lock(self, timeout_s: float = 5.0) -> bool: ...

    def read_status(self) -> LockFlags: ...

    def read_stats(self) -> SignalStats: ...

    def set_tone(self, on: bool) -> None: ...

    def set_voltage(self, volts: int) -> None: ...

    def send_diseqc(self, cmd: bytes, repeats: int = 0) -> None: ...


def split_diseqc_messages(cmd: bytes) -> list[bytes]:
    """Trocea una secuencia DiSEqC en mensajes que admite el kernel.

    `FE_DISEQC_SEND_MASTER_CMD` acepta un único mensaje de 3 a 6 bytes, pero una
    orden DiSEqC 1.1 son dos mensajes (committed + uncommitted, 4 + 4 bytes).
    Este helper convierte una secuencia de 4·N bytes en N mensajes de 4 bytes.

    :raises UnsupportedError: si la longitud no es válida.
    """
    payload = bytes(cmd or b"")
    if not payload:
        raise UnsupportedError("Mensaje DiSEqC vacío")
    if 3 <= len(payload) <= 6:
        return [payload]
    if len(payload) > 6 and len(payload) % 4 == 0:
        messages = [payload[offset : offset + 4] for offset in range(0, len(payload), 4)]
        if all(3 <= len(message) <= 6 for message in messages):
            return messages
    raise UnsupportedError(
        f"Secuencia DiSEqC de longitud inválida ({len(payload)} bytes); "
        "se espera un mensaje de 3..6 bytes o una secuencia de múltiplos de 4"
    )


class FrontendBase:
    """Comportamiento compartido por las implementaciones (context manager).

    No sustituye al Protocol: sólo evita duplicar `__enter__`/`__exit__` y el
    cierre idempotente en las dos implementaciones.
    """
    info: FrontendInfo
    is_mock: bool = False

    def __enter__(self) -> "FrontendBase":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - red de seguridad
        try:
            self.close()
        except Exception:
            pass

    # Los métodos reales los define cada implementación.
    def open(self) -> None:  # pragma: no cover - abstracto
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - abstracto
        raise NotImplementedError

    def tune(self, params: TuneParams) -> None:  # pragma: no cover - abstracto
        raise NotImplementedError

    def wait_lock(self, timeout_s: float = 5.0) -> bool:  # pragma: no cover
        raise NotImplementedError

    def read_status(self) -> LockFlags:  # pragma: no cover - abstracto
        raise NotImplementedError

    def read_stats(self) -> SignalStats:  # pragma: no cover - abstracto
        raise NotImplementedError

    def set_tone(self, on: bool) -> None:  # pragma: no cover - abstracto
        raise NotImplementedError

    def set_voltage(self, volts: int) -> None:  # pragma: no cover - abstracto
        raise NotImplementedError

    def send_diseqc(self, cmd: bytes, repeats: int = 0) -> None:  # pragma: no cover
        raise NotImplementedError
