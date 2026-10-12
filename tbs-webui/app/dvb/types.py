"""Tipos puros del dominio DVB (hoja del grafo de dependencias).

Este módulo **sólo** depende de la biblioteca estándar: no importa
`app.config`, ni FastAPI, ni ningún otro módulo de `app.dvb`. Por eso puede
importarse desde cualquier capa (servicios, routers, tests).

Contrato: INTERFACES.md §2. Las dataclasses son inmutables (`frozen=True`).

Notas de implementación (desviaciones documentadas, ver informe a Lead):

* `DeliverySystem` **no** lleva ``@dataclass(frozen=True)``. Aunque
  INTERFACES.md §2 lo muestra así, aplicar `dataclass` sobre una clase
  `(str, Enum)` rompe la igualdad de los miembros: `dataclass` genera un
  `__eq__`/`__hash__` que sólo compara la tupla de campos (vacía), de modo que
  ``DVB_S == DVB_S2`` pasaría a ser ``True`` y todos los miembros colapsarían
  en un `set`/`dict`. Se conserva la semántica pedida (enum de strings inmutable
  y hasheable) sin el decorador.
* `TuneParams` y `Transponder` incluyen, además de los campos de §2, los campos
  que `app/services/**` ya usa (`adapter`, `frontend`, `lnb_*`, `use_lnb`,
  `tone_22k`, `voltage`, `diseqc`, `lock_timeout_s` y los parámetros OFDM). Son
  campos **con valor por defecto** añadidos al final: el contrato §2 sigue
  funcionando igual (incluida la construcción posicional).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

__all__ = [
    "DeliverySystem",
    "FrontendInfo",
    "TuneParams",
    "LockFlags",
    "SignalStats",
    "ServiceInfo",
    "Transponder",
    "ScanProgress",
    "FrontendError",
    "DeviceNotFoundError",
    "DeviceBusyError",
    "UnsupportedError",
    "LockTimeoutError",
    "DvbError",
    "normalize_delivery_system",
    "service_type_label",
]

# ---------------------------------------------------------------------------
# Errores
# ---------------------------------------------------------------------------
# Se definen aquí (hoja pura) y `app/dvb/base.py` los reexporta, de modo que
# tanto `from app.dvb.types import DeviceBusyError` como
# `from app.dvb.base import DeviceBusyError` funcionan (INTERFACES.md §1 sitúa
# los errores en base.py y §2 en types.py: se admiten las dos rutas).


class FrontendError(Exception):
    """Error base de la capa DVB."""


class DeviceNotFoundError(FrontendError):
    """El dispositivo o frontend no existe (ENODEV/ENOENT/ENXIO)."""


class DeviceBusyError(FrontendError):
    """El dispositivo está ocupado por otro proceso/operación (EBUSY/EAGAIN)."""


class UnsupportedError(FrontendError):
    """El frontend no soporta la operación pedida (EINVAL/EOPNOTSUPP/ENOTTY)."""


class LockTimeoutError(FrontendError):
    """No se alcanzó el LOCK dentro del timeout."""


class DvbError(FrontendError):
    """Error genérico de la capa DVB."""


# ---------------------------------------------------------------------------
# Enumeraciones y constantes de dominio
# ---------------------------------------------------------------------------
class DeliverySystem(str, Enum):
    """Sistemas de entrega con los nombres de la API (INTERFACES.md §2).

    Hereda de `str`: los miembros se comportan como cadenas
    (``DeliverySystem.DVB_S2 == "DVB-S2"``) y serializan a JSON directamente.
    """

    DVB_S = "DVB-S"
    DVB_S2 = "DVB-S2"
    DVB_T = "DVB-T"
    DVB_T2 = "DVB-T2"
    DVB_C = "DVB-C"
    DVB_C2 = "DVB-C2"
    ISDB_T = "ISDB-T"
    ATSC = "ATSC"

    def __str__(self) -> str:  # f"{member}" -> "DVB-S2"
        return self.value


def normalize_delivery_system(value: str | DeliverySystem | None) -> str:
    """Devuelve el string de API ("DVB-S2") de un sistema de entrega."""
    if value is None:
        return ""
    if isinstance(value, DeliverySystem):
        return value.value
    text = str(value).strip().upper().replace("_", "-")
    aliases = {
        "DVB-S": "DVB-S",
        "DVBS": "DVB-S",
        "DVB-S2": "DVB-S2",
        "DVBS2": "DVB-S2",
        "DVB-S2X": "DVB-S2",
        "DVB-T": "DVB-T",
        "DVBT": "DVB-T",
        "DVB-T2": "DVB-T2",
        "DVBT2": "DVB-T2",
        "DVB-C": "DVB-C",
        "DVBC": "DVB-C",
        "DVB-C2": "DVB-C2",
        "DVBC2": "DVB-C2",
        "ISDB-T": "ISDB-T",
        "ISDBT": "ISDB-T",
        "ATSC": "ATSC",
    }
    return aliases.get(text, str(value).strip())


# Etiquetas legibles de `service_type` (ETSI EN 300 468, tabla 87)
SERVICE_TYPE_LABELS: dict[int, str] = {
    0x01: "TV",       # televisión digital
    0x02: "Radio",    # radio digital
    0x03: "Data",     # teletexto
    0x04: "Data",     # NVOD referencia
    0x05: "Data",     # NVOD time-shift
    0x06: "Data",     # mosaico
    0x0A: "Data",     # radio por satélite (audio)
    0x0B: "Data",     # mosaico de datos
    0x0C: "Data",     # difusión de datos
    0x11: "HDTV",     # MPEG-2 HD
    0x16: "TV",       # SDTV avanzado (H.264)
    0x19: "HDTV",     # HDTV avanzado (H.264)
    0x1F: "HDTV",     # UHD/HEVC
}


def service_type_label(service_type: int) -> str:
    """Etiqueta de API para un `service_type` ("TV" | "Radio" | "Data" | "HDTV")."""
    return SERVICE_TYPE_LABELS.get(int(service_type), "Data")


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FrontendInfo:
    """Descripción de un frontend (INTERFACES.md §2)."""

    adapter: int
    frontend: int
    name: str
    device_path: str
    type: str
    caps: int
    delivery_systems: tuple[str, ...]
    min_frequency: int
    max_frequency: int
    symbol_rates: tuple[int, ...] = ()

    @property
    def key(self) -> tuple[int, int]:
        return (self.adapter, self.frontend)

    def to_dict(self) -> dict[str, Any]:
        """Claves JSON exactas de `AdapterOut` (INTERFACES.md §5)."""
        return {
            "adapter": self.adapter,
            "frontend": self.frontend,
            "name": self.name,
            "device_path": self.device_path,
            "type": self.type,
            "delivery_systems": list(self.delivery_systems),
            "min_frequency": self.min_frequency,
            "max_frequency": self.max_frequency,
        }


# ---------------------------------------------------------------------------
# Sintonización
# ---------------------------------------------------------------------------
def _lnb_value_khz(value: int) -> int:
    """Normaliza un valor de oscilador local a kHz.

    En `app/config.py`, `/api/settings` y `TuneRequest` los LNB se expresan con
    la etiqueta "*_khz" pero con valores en MHz (9750 / 10600 / 11700, que son
    los reales de un LNB universal). Cualquier valor < 100000 se interpreta como
    MHz; a partir de ahí se considera kHz. Así `9750 -> 9_750_000` y
    `9_750_000 -> 9_750_000`.
    """
    number = int(value)
    return number * 1000 if 0 < number < 100_000 else number


@dataclass(frozen=True)
class TuneParams:
    """Parámetros de sintonización (INTERFACES.md §2 + extensión de servicios).

    `frequency_khz` es la frecuencia del **transponder** en kHz. Si `use_lnb`
    está activo, `if_frequency_khz` resta el oscilador local y es la frecuencia
    que se envía al frontend (DVB-S trabaja en kHz de FI).
    """

    delivery_system: str
    frequency_khz: int
    symbol_rate_ksps: int | None = None
    polarization: str | None = None
    fec: str | None = None
    modulation: str | None = None
    bandwidth_mhz: int | None = None
    code_rate_hp: str | None = None
    code_rate_lp: str | None = None
    guard_interval: str | None = None
    transmission_mode: str | None = None
    hierarchy: str | None = None
    inversion: str = "AUTO"
    rolloff: str | None = None
    pilot: str | None = None
    stream_id: int | None = None
    plp_id: int | None = None
    is_multi_stream: bool = False
    # --- extensión requerida por app/services (con defaults) ---
    adapter: int = 0
    frontend: int = 0
    lnb_low_khz: int = 9750
    lnb_high_khz: int = 10600
    lnb_threshold_khz: int = 11700
    use_lnb: bool = True
    tone_22k: bool | None = None
    voltage: str | None = None
    diseqc: str | None = None
    lock_timeout_s: float = 5.0

    # -- LNB ---------------------------------------------------------------
    @property
    def band(self) -> str:
        """Banda del LNB: "high" por encima del umbral, "low" por debajo."""
        if self.frequency_khz >= _lnb_value_khz(self.lnb_threshold_khz):
            return "high"
        return "low"

    @property
    def lnb_offset_khz(self) -> int:
        """Oscilador local a restar (0 si no hay LNB)."""
        if not self.use_lnb:
            return 0
        reference = self.lnb_high_khz if self.band == "high" else self.lnb_low_khz
        return _lnb_value_khz(reference)

    @property
    def if_frequency_khz(self) -> int:
        """Frecuencia intermedia que se envía al frontend."""
        return int(self.frequency_khz) - self.lnb_offset_khz

    def lnb_band(self) -> str:
        """Alias de `band` con la firma que usa `TuneRequest` del Lead."""
        return self.band

    def needed_tone(self) -> bool:
        """Tono 22 kHz: obligatorio en banda alta salvo indicación explícita."""
        if self.tone_22k is not None:
            return bool(self.tone_22k)
        return bool(self.use_lnb and self.band == "high")

    def needed_voltage(self) -> str:
        """Voltaje del LNB: 18 V para horizontal (H/R), 13 V para vertical (V/L).

        Convención estándar de un LNB universal: H (o R) -> 18 V, V (o L) -> 13 V.
        """
        if self.voltage is not None:
            return str(self.voltage)
        if (self.polarization or "").upper() in ("H", "R"):
            return "18V"
        return "13V"

    def to_dict(self) -> dict[str, Any]:
        """Representación serializable (para `/api/status` y el WebSocket)."""
        data = {
            field_name: getattr(self, field_name)
            for field_name in self.__dataclass_fields__
        }
        data["delivery_system"] = normalize_delivery_system(self.delivery_system)
        return data


@dataclass(frozen=True)
class LockFlags:
    """Bits de `FE_READ_STATUS` (enum fe_status del kernel)."""

    signal: bool
    carrier: bool
    viterbi: bool
    sync: bool
    lock: bool
    raw: int = 0

    @property
    def is_locked(self) -> bool:
        return bool(self.lock and self.signal)

    def to_dict(self) -> dict[str, bool]:
        return {
            "signal": bool(self.signal),
            "carrier": bool(self.carrier),
            "viterbi": bool(self.viterbi),
            "sync": bool(self.sync),
            "lock": bool(self.lock),
        }


@dataclass(frozen=True)
class SignalStats:
    """Estadísticas de señal (INTERFACES.md §2)."""

    timestamp: float
    lock: LockFlags
    signal_percent: float | None
    signal_dbm: float | None
    snr_db: float | None
    cnr_raw: float | None
    ber: float | None
    ber_pre: float | None
    per: float | None
    ucblocks: int | None
    quality_percent: float | None
    error: str | None = None

    @classmethod
    def empty(cls, error: str | None = None) -> "SignalStats":
        """Estadísticas vacías (frontend cerrado, driver sin soporte, etc.)."""
        return cls(
            timestamp=time.time(),
            lock=LockFlags(False, False, False, False, False),
            signal_percent=None,
            signal_dbm=None,
            snr_db=None,
            cnr_raw=None,
            ber=None,
            ber_pre=None,
            per=None,
            ucblocks=None,
            quality_percent=None,
            error=error,
        )


# ---------------------------------------------------------------------------
# Servicios
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ServiceInfo:
    """Servicio (canal) detectado por PSI/SI (INTERFACES.md §2)."""

    service_id: int
    name: str
    provider: str
    service_type: int
    service_type_label: str
    is_fta: bool
    scrambled: bool
    video_pid: int | None
    audio_pid: int | None
    pcr_pid: int | None
    video_codec: str | None
    audio_codec: str | None
    pmt_pid: int | None
    ca_system_ids: tuple[int, ...] = ()
    audio_pids: tuple[int, ...] = ()
    video_pids: tuple[int, ...] = ()


# ---------------------------------------------------------------------------
# Transponders
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Transponder:
    """Transponder del dominio (INTERFACES.md §2 + extensión de servicios)."""

    delivery_system: str
    frequency_khz: int
    symbol_rate_ksps: int | None = None
    polarization: str | None = None
    fec: str | None = None
    modulation: str | None = None
    bandwidth_mhz: int | None = None
    inversion: str = "AUTO"
    rolloff: str | None = None
    pilot: str | None = None
    stream_id: int | None = None
    plp_id: int | None = None
    sat_position: str | None = None
    # --- extensión requerida por app/services (con defaults) ---
    code_rate_hp: str | None = None
    code_rate_lp: str | None = None
    guard_interval: str | None = None
    transmission_mode: str | None = None
    hierarchy: str | None = None

    def to_tune_params(self, **overrides: Any) -> TuneParams:
        """Convierte a `TuneParams` (INTERFACES.md §2).

        Los `overrides` permiten fijar `adapter`, `frontend`, `use_lnb`,
        `lock_timeout_s`, etc. sin romper la llamada sin argumentos.
        """
        data: dict[str, Any] = {
            "delivery_system": normalize_delivery_system(self.delivery_system),
            "frequency_khz": int(self.frequency_khz),
            "symbol_rate_ksps": self.symbol_rate_ksps,
            "polarization": self.polarization,
            "fec": self.fec,
            "modulation": self.modulation,
            "bandwidth_mhz": self.bandwidth_mhz,
            "code_rate_hp": self.code_rate_hp,
            "code_rate_lp": self.code_rate_lp,
            "guard_interval": self.guard_interval,
            "transmission_mode": self.transmission_mode,
            "hierarchy": self.hierarchy,
            "inversion": self.inversion,
            "rolloff": self.rolloff,
            "pilot": self.pilot,
            "stream_id": self.stream_id,
            "plp_id": self.plp_id,
        }
        data.update(overrides)
        return TuneParams(**data)

    def label(self) -> str:
        """Etiqueta legible, p. ej. ``DVB-S2 11362 MHz H 22000 ksps``."""
        parts: list[str] = [
            normalize_delivery_system(self.delivery_system),
            f"{self.frequency_khz / 1000:g} MHz",
        ]
        if self.polarization:
            parts.append(str(self.polarization))
        if self.symbol_rate_ksps:
            parts.append(f"{self.symbol_rate_ksps} ksps")
        return " ".join(parts)


# ---------------------------------------------------------------------------
# Progreso de escaneo
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ScanProgress:
    """Estado del escaneo en curso (INTERFACES.md §2)."""

    state: str
    total: int
    current: int
    percent: float
    current_transponder: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    services_found: int = 0
    transponders_locked: int = 0
    transponders_scanned: int = 0
    errors: tuple[str, ...] = ()
    message: str = ""

    @classmethod
    def idle(cls) -> "ScanProgress":
        return cls(state="idle", total=0, current=0, percent=0.0)

    def to_dict(self) -> dict[str, Any]:
        data = {
            field_name: getattr(self, field_name)
            for field_name in self.__dataclass_fields__
        }
        data["errors"] = list(self.errors)
        return data
