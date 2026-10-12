"""Protocolos internos de los servicios.

Permiten sustituir la capa DVB por mocks en los tests sin tocar los routers.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ..dvb.types import FrontendInfo, ScanProgress, ServiceInfo, SignalStats, Transponder, TuneParams


@runtime_checkable
class DeviceManagerProtocol(Protocol):
    """Gestión del frontend: un único dueño del hardware a la vez."""

    def list_adapters(self) -> list[FrontendInfo]: ...

    def tune(self, params: TuneParams) -> SignalStats: ...

    def release(self) -> None: ...

    def current_stats(self) -> SignalStats | None: ...

    def status(self) -> dict[str, Any]: ...

    @property
    def lock_owner(self) -> str | None: ...


@runtime_checkable
class ScanServiceProtocol(Protocol):
    """Escaneo en segundo plano con progreso y log consultables."""

    def start(self, request: Any) -> str: ...

    def stop(self) -> bool: ...

    @property
    def progress(self) -> ScanProgress: ...

    def log(self, since: float | None = None) -> list[dict[str, Any]]: ...

    def services(self) -> list[ServiceInfo]: ...

    def is_running(self) -> bool: ...


@runtime_checkable
class ExporterProtocol(Protocol):
    def to_m3u(self, services: list[Any]) -> str: ...

    def to_channels_conf(self, transponders: list[Transponder]) -> str: ...
