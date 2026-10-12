"""Registro de puertos de sintonizador: quien lo usa y con que transponder.

Cada puerto (`adapterN/frontendM`) es un recurso **exclusivo**: mientras un
transponder lo tiene sintonizado, ningun otro proceso (escaneo, otra sintonia,
otro usuario) puede usarlo. Este modulo lleva ese control y guarda, por puerto:

  * el propietario del bloqueo (`api`, `scan:xxxx`, ...),
  * el transponder sintonizado (frecuencia, polaridad, symbol rate, sistema...),
  * los ultimos niveles de senal medidos (senal, SNR, BER y flags de lock).

El panel pide `snapshot()` para pintar en el cuadro de mando los niveles en vivo
de **todos** los sintonizadores en uso.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from ..dvb.types import TuneParams

logger = logging.getLogger(__name__)


def _stats_fields(stats: Any) -> dict[str, Any]:
    """Niveles de un `SignalStats` (evita depender de device_manager)."""
    flags = getattr(stats, "flags", None) or getattr(stats, "lock", None)
    return {
        "locked": bool(getattr(flags, "is_locked", False)),
        "signal_percent": getattr(stats, "signal_percent", None),
        "snr_db": getattr(stats, "snr_db", None),
        "ber": getattr(stats, "ber", None),
        "post_ber": getattr(stats, "post_ber", None),
        "level_dbm": getattr(stats, "signal_dbm", None),
    }


def _round(value: Any, digits: int = 2) -> float | None:
    try:
        return None if value is None else round(float(value), digits)
    except (TypeError, ValueError):
        return None

# Estados del puerto
STATE_IDLE = "idle"
STATE_TUNING = "tuning"
STATE_LOCKED = "locked"
STATE_NO_LOCK = "no_lock"


def port_key(adapter: int, frontend: int) -> str:
    """Clave estable de un puerto de sintonizador."""
    return f"adapter{int(adapter)}/frontend{int(frontend)}"


@dataclass
class TunerUsage:
    """Uso actual de un puerto de sintonizador."""

    adapter: int
    frontend: int
    owner: str = ""
    state: str = STATE_IDLE
    since: float | None = None
    label: str = ""
    # Datos del transponder sintonizado
    frequency_khz: int | None = None
    if_frequency_khz: int | None = None
    polarization: str | None = None
    symbol_rate_ksps: int | None = None
    delivery_system: str | None = None
    fec: str | None = None
    modulation: str | None = None
    # Ultimos niveles
    locked: bool = False
    signal_percent: float | None = None
    snr_db: float | None = None
    ber: float | None = None
    post_ber: float | None = None
    level_dbm: float | None = None
    updated_at: float | None = None
    history: list[dict[str, float | None]] = field(default_factory=list)

    @property
    def key(self) -> str:
        return port_key(self.adapter, self.frontend)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "adapter": self.adapter,
            "frontend": self.frontend,
            "owner": self.owner,
            "state": self.state,
            "in_use": self.state != STATE_IDLE,
            "since": self.since,
            "in_use_seconds": (time.time() - self.since) if self.since else None,
            "label": self.label,
            "transponder": {
                "frequency_khz": self.frequency_khz,
                "frequency_mhz": round(self.frequency_khz / 1000, 3) if self.frequency_khz else None,
                "if_frequency_khz": self.if_frequency_khz,
                "polarization": self.polarization,
                "symbol_rate_ksps": self.symbol_rate_ksps,
                "delivery_system": self.delivery_system,
                "fec": self.fec,
                "modulation": self.modulation,
            },
            "signal": {
                "locked": self.locked,
                "signal_percent": self.signal_percent,
                "snr_db": self.snr_db,
                "ber": self.ber,
                "post_ber": self.post_ber,
                "level_dbm": self.level_dbm,
                "updated_at": self.updated_at,
            },
            "history": list(self.history),
        }


class TunerRegistry:
    """Control de acceso exclusivo por puerto de sintonizador."""

    def __init__(self, history_seconds: float = 60.0) -> None:
        self._lock = threading.RLock()
        self._tuners: dict[str, TunerUsage] = {}
        self._history_seconds = max(5.0, float(history_seconds))

    # -- bloqueo --------------------------------------------------------
    def claim(self, adapter: int, frontend: int, owner: str) -> TunerUsage:
        """Reserva el puerto para `owner`.

        Lanza `TunerBusyError` si ya lo tiene otro propietario: **mientras un
        transponder esta sintonizado en un puerto, nadie mas puede usarlo**.
        """
        key = port_key(adapter, frontend)
        with self._lock:
            actual = self._tuners.get(key)
            if actual is not None and actual.owner and actual.owner != owner:
                raise TunerBusyError(
                    f"El sintonizador {key} esta en uso por '{actual.owner}'"
                    + (f" en {actual.frequency_khz // 1000} kHz" if actual.frequency_khz else "")
                    + ". Espera a que termine o liberalo desde el cuadro de mando."
                )
            usage = actual or TunerUsage(adapter=int(adapter), frontend=int(frontend))
            usage.owner = owner
            usage.state = STATE_TUNING
            usage.since = usage.since or time.time()
            self._tuners[key] = usage
            return usage

    def release(self, adapter: int, frontend: int, owner: str | None = None) -> bool:
        """Libera el puerto. Con `owner`, solo lo libera si es su propietario."""
        key = port_key(adapter, frontend)
        with self._lock:
            usage = self._tuners.get(key)
            if usage is None:
                return False
            if owner is not None and usage.owner and usage.owner != owner:
                return False
            self._tuners.pop(key, None)
            logger.info("Sintonizador %s liberado (era de '%s')", key, owner or usage.owner)
            return True

    def release_all(self, owner: str) -> int:
        """Libera todos los puertos de un propietario (p. ej. al acabar un escaneo)."""
        with self._lock:
            keys = [key for key, usage in self._tuners.items() if usage.owner == owner]
        liberados = 0
        for key in keys:
            usage = self._tuners.get(key)
            if usage is not None and self.release(usage.adapter, usage.frontend, owner):
                liberados += 1
        return liberados

    def owner_of(self, adapter: int, frontend: int) -> str | None:
        """Propietario actual del puerto (None si esta libre)."""
        with self._lock:
            usage = self._tuners.get(port_key(adapter, frontend))
            return usage.owner if usage and usage.owner else None

    def is_busy(self, adapter: int, frontend: int, owner: str | None = None) -> bool:
        """True si el puerto esta ocupado por alguien distinto de `owner`."""
        actual = self.owner_of(adapter, frontend)
        if not actual:
            return False
        return owner is None or actual != owner

    # -- estado ---------------------------------------------------------
    def mark_tuned(self, params: TuneParams, owner: str) -> TunerUsage:
        """Registra el transponder sintonizado en el puerto."""
        key = port_key(params.adapter, params.frontend)
        with self._lock:
            usage = self._tuners.get(key) or TunerUsage(
                adapter=int(params.adapter), frontend=int(params.frontend)
            )
            usage.owner = owner
            usage.since = usage.since or time.time()
            usage.frequency_khz = int(params.frequency_khz)
            usage.if_frequency_khz = int(params.if_frequency_khz or 0) or None
            usage.polarization = params.polarization
            usage.symbol_rate_ksps = params.symbol_rate_ksps
            usage.delivery_system = params.delivery_system
            usage.fec = params.fec
            usage.modulation = params.modulation
            usage.label = _label(params)
            usage.history = []
            self._tuners[key] = usage
            return usage

    def update_stats(self, adapter: int, frontend: int, stats: Any) -> None:
        """Guarda los ultimos niveles medidos del puerto."""
        if stats is None:
            return
        key = port_key(adapter, frontend)
        with self._lock:
            usage = self._tuners.get(key)
            if usage is None:
                return
            datos = _stats_fields(stats)
            usage.locked = datos["locked"]
            usage.signal_percent = _round(datos["signal_percent"], 1)
            usage.snr_db = _round(datos["snr_db"], 2)
            usage.ber = datos["ber"]
            usage.post_ber = datos["post_ber"]
            usage.level_dbm = _round(datos["level_dbm"], 1)
            usage.updated_at = time.time()
            usage.state = STATE_LOCKED if usage.locked else STATE_NO_LOCK
            usage.history.append(
                {
                    "t": round(usage.updated_at, 3),
                    "signal": usage.signal_percent,
                    "snr": usage.snr_db,
                }
            )
            # Se recorta la ventana de historial
            limite = usage.updated_at - self._history_seconds
            if usage.history and usage.history[0]["t"] < limite:
                usage.history = [point for point in usage.history if point["t"] >= limite]
            if len(usage.history) > 600:
                usage.history = usage.history[-600:]

    def snapshot(self) -> list[TunerUsage]:
        """Uso de todos los puertos (para el cuadro de mando)."""
        with self._lock:
            return list(self._tuners.values())

    def in_use(self) -> list[TunerUsage]:
        """Solo los puertos con un transponder sintonizado."""
        return [usage for usage in self.snapshot() if usage.state != STATE_IDLE and usage.frequency_khz]

    def to_dict(self) -> dict[str, Any]:
        """Resumen serializable para la API."""
        usados = self.in_use()
        return {
            "in_use": len(usados),
            "total": len(self.snapshot()),
            "tuners": [usage.to_dict() for usage in usados],
        }


class TunerBusyError(RuntimeError):
    """El puerto de sintonizador ya esta ocupado por otro propietario."""


def _label(params: TuneParams) -> str:
    """Etiqueta legible del transponder (p. ej. '10970 MHz V 27900 DVB-S2')."""
    partes: list[str] = []
    if params.frequency_khz:
        partes.append(f"{int(params.frequency_khz) // 1000} MHz")
    if params.polarization:
        partes.append(str(params.polarization))
    if params.symbol_rate_ksps:
        partes.append(f"{int(params.symbol_rate_ksps)} ksps")
    if params.delivery_system:
        partes.append(str(params.delivery_system))
    return " ".join(partes) or "transponder"
