"""Esquemas Pydantic: validación de entrada y forma exacta de salida de la API.

Las claves JSON de salida están descritas en INTERFACES.md §5; el frontend
depende de ellas, así que no se renombran sin avisar.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DeliverySystem = Literal["DVB-S", "DVB-S2", "DVB-T", "DVB-T2", "DVB-C", "DVB-C2", "ISDB-T", "ATSC"]
Polarization = Literal["H", "V", "L", "R"]
ServiceTypeLabel = Literal["TV", "Radio", "Data", "HDTV"]


# ---------------------------------------------------------------------------
# Autenticación
# ---------------------------------------------------------------------------
class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class SetupRequest(BaseModel):
    """Primer arranque: creación del usuario administrador."""

    username: str = Field(default="admin", min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=256)

    @field_validator("username")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("El usuario no puede estar vacío")
        return value


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)

    @model_validator(mode="after")
    def _different(self) -> "PasswordChange":
        if self.current_password == self.new_password:
            raise ValueError("La nueva contraseña debe ser distinta de la actual")
        return self


class MeOut(BaseModel):
    authenticated: bool
    username: str | None = None
    csrf_token: str | None = None
    must_setup: bool = False
    mock: bool = False
    version: str = "1.0.0"


class LoginOut(BaseModel):
    ok: bool = True
    username: str
    csrf_token: str


class OkOut(BaseModel):
    ok: bool = True
    detail: str | None = None


# ---------------------------------------------------------------------------
# Adaptadores
# ---------------------------------------------------------------------------
class AdapterOut(BaseModel):
    adapter: int
    frontend: int
    name: str
    device_path: str
    type: str
    delivery_systems: list[str] = Field(default_factory=list)
    min_frequency: int = 0
    max_frequency: int = 0
    symbol_rates: list[int] = Field(default_factory=list)
    mock: bool = False


class LockStateOut(BaseModel):
    owner: str | None = None
    acquired_at: float | None = None
    waiting: int = 0


class AdaptersOut(BaseModel):
    mock: bool
    locked: LockStateOut
    adapters: list[AdapterOut]


# ---------------------------------------------------------------------------
# Sintonización
# ---------------------------------------------------------------------------
class TuneRequest(BaseModel):
    adapter: int = Field(default=0, ge=0, le=31)
    frontend: int = Field(default=0, ge=0, le=7)
    delivery_system: DeliverySystem = "DVB-S2"
    frequency_khz: int = Field(gt=0, le=100_000_000, description="Frecuencia del transponder en kHz (sin LNB)")
    symbol_rate_ksps: int | None = Field(default=None, gt=0, le=100_000)
    polarization: Polarization | None = None
    fec: str | None = None
    modulation: str | None = None
    bandwidth_mhz: int | None = Field(default=None, gt=0, le=64)
    code_rate_hp: str | None = None
    code_rate_lp: str | None = None
    guard_interval: str | None = None
    transmission_mode: str | None = None
    hierarchy: str | None = None
    inversion: str = "AUTO"
    rolloff: str | None = None
    pilot: str | None = None
    stream_id: int | None = Field(default=None, ge=0, le=255)
    plp_id: int | None = Field(default=None, ge=0, le=255)
    is_multi_stream: bool = False
    # LNB / conmutación
    lnb_low_khz: int = Field(default=9750, ge=0, le=30000)
    lnb_high_khz: int = Field(default=10600, ge=0, le=30000)
    lnb_threshold_khz: int = Field(default=11700, ge=0, le=30000)
    use_lnb: bool = True
    tone_22k: bool | None = None
    voltage: Literal["13V", "18V", "OFF"] | None = None
    diseqc: str | None = None  # "1.0:0" .. "1.0:3", "1.1:0".."1.1:15"
    lock_timeout_s: float = Field(default=5.0, gt=0, le=60)

    @field_validator("fec", "modulation", "rolloff", "pilot", "code_rate_hp", "code_rate_lp")
    @classmethod
    def _upper(cls, value: str | None) -> str | None:
        return value.strip().upper() if isinstance(value, str) and value.strip() else value

    @property
    def lnb_offset_khz(self) -> int:
        """Frecuencia del oscilador local que hay que restar (0 si no hay LNB)."""
        if not self.use_lnb:
            return 0

        def to_khz(value: int) -> int:
            """9750 -> 9 750 000 kHz; 9750000 -> 9 750 000 kHz."""
            return value * 1000 if 0 < value < 100_000 else value

        if self.frequency_khz >= to_khz(self.lnb_threshold_khz):
            return to_khz(self.lnb_high_khz)
        return to_khz(self.lnb_low_khz)

    @property
    def if_frequency_khz(self) -> int:
        """Frecuencia intermedia que se envía al frontend (LNB restado), en kHz."""
        return self.frequency_khz - self.lnb_offset_khz

    def lnb_band(self) -> str:
        threshold = self.lnb_threshold_khz
        threshold = threshold * 1000 if threshold < 100_000 else threshold
        return "high" if self.frequency_khz >= threshold else "low"

    def needed_tone(self) -> bool:
        """Tono 22 kHz: obligatorio en banda alta salvo que se indique lo contrario."""
        if self.tone_22k is not None:
            return self.tone_22k
        return self.use_lnb and self.lnb_band() == "high"

    def needed_voltage(self) -> str:
        """Voltaje del LNB: 18 V para H/R, 13 V para V/L (convención universal)."""
        if self.voltage is not None:
            return self.voltage
        if self.polarization in ("H", "R"):
            return "18V"
        return "13V"


class TuneResult(BaseModel):
    ok: bool
    locked: bool
    adapter: int
    frontend: int
    mock: bool = False
    frequency_khz: int
    if_frequency_khz: int
    delivery_system: str
    lock_timeout_s: float
    stats: "SignalStatsOut | None" = None
    detail: str | None = None


# ---------------------------------------------------------------------------
# Señal
# ---------------------------------------------------------------------------
class LockFlagsOut(BaseModel):
    signal: bool = False
    carrier: bool = False
    viterbi: bool = False
    sync: bool = False
    lock: bool = False


class SignalStatsOut(BaseModel):
    timestamp: float
    locked: bool = False
    signal_percent: float | None = None
    signal_dbm: float | None = None
    snr_db: float | None = None
    cnr_raw: float | None = None
    ber: float | None = None
    ber_pre: float | None = None
    per: float | None = None
    ucblocks: int | None = None
    quality_percent: float | None = None
    error: str | None = None
    flags: LockFlagsOut = Field(default_factory=LockFlagsOut)


class StatusOut(BaseModel):
    locked_by: str | None = None
    mock: bool = False
    adapter: int | None = None
    frontend: int | None = None
    tuned: "TunedParamsOut | None" = None
    stats: SignalStatsOut | None = None
    waiting: int = 0


class TunedParamsOut(BaseModel):
    """Parámetros del transponder sintonizado (subconjunto estable de TuneRequest)."""

    model_config = ConfigDict(from_attributes=True)

    adapter: int = 0
    frontend: int = 0
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
    use_lnb: bool = True
    lnb_low_khz: int = 9750
    lnb_high_khz: int = 10600
    lnb_threshold_khz: int = 11700
    tone_22k: bool | None = None
    voltage: str | None = None
    diseqc: str | None = None
    lock_timeout_s: float = 5.0
    # Campo derivado: frecuencia intermedia real que se envía al frontend.
    if_frequency_khz: int | None = None

    @model_validator(mode="after")
    def _fill_if_frequency(self) -> "TunedParamsOut":
        """Calcula la FI si el origen no la trae (p. ej. `TunedParamsOut` desde TuneParams)."""
        if self.if_frequency_khz is None:
            self.if_frequency_khz = _if_frequency_khz(
                self.frequency_khz,
                self.lnb_low_khz,
                self.lnb_high_khz,
                self.lnb_threshold_khz,
                self.use_lnb,
            )
        return self


def _if_frequency_khz(
    frequency_khz: int, lnb_low_khz: int, lnb_high_khz: int, lnb_threshold_khz: int, use_lnb: bool
) -> int:
    """Frecuencia intermedia = frecuencia del transponder - oscilador local del LNB."""
    if not use_lnb:
        return int(frequency_khz)

    def to_khz(value: int) -> int:
        return int(value) * 1000 if 0 < int(value) < 100_000 else int(value)

    threshold = to_khz(lnb_threshold_khz)
    offset = to_khz(lnb_high_khz) if int(frequency_khz) >= threshold else to_khz(lnb_low_khz)
    return int(frequency_khz) - offset


class HistoryPoint(BaseModel):
    t: float
    signal: float | None = None
    snr: float | None = None
    ber: float | None = None


class SignalMessage(BaseModel):
    type: Literal["stats"] = "stats"
    history: list[HistoryPoint] = Field(default_factory=list)
    # Campos de SignalStatsOut se añaden aplanados (ver routers/ws.py)
    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------------
# Escaneo
# ---------------------------------------------------------------------------
class TransponderIn(BaseModel):
    delivery_system: DeliverySystem = "DVB-S2"
    frequency_khz: int = Field(gt=0, le=100_000_000)
    symbol_rate_ksps: int | None = Field(default=None, gt=0, le=100_000)
    polarization: Polarization | None = None
    fec: str | None = None
    modulation: str | None = None
    bandwidth_mhz: int | None = None
    inversion: str = "AUTO"
    rolloff: str | None = None
    pilot: str | None = None
    stream_id: int | None = None
    plp_id: int | None = None
    sat_position: str | None = None
    code_rate_hp: str | None = None
    code_rate_lp: str | None = None
    guard_interval: str | None = None
    transmission_mode: str | None = None
    hierarchy: str | None = None


class AdapterRef(BaseModel):
    adapter: int = Field(default=0, ge=0, le=31)
    frontend: int = Field(default=0, ge=0, le=7)


class ScanStartRequest(BaseModel):
    adapters: list[AdapterRef] = Field(default_factory=lambda: [AdapterRef()])
    transponders: list[TransponderIn] | None = None
    table: str | None = None  # nombre del bundle de /usr/share/dvb
    current_only: bool = False  # escanear sólo el transponder sintonizado
    clear_existing: bool = False  # borrar canales del transponder antes de guardar
    lock_timeout_s: float = Field(default=5.0, gt=0, le=60)
    dwell_seconds: float = Field(default=8.0, gt=0, le=60)  # tiempo de escucha por transponder
    lnb_low_khz: int = 9750
    lnb_high_khz: int = 10600
    lnb_threshold_khz: int = 11700
    use_lnb: bool = True


class ScanLogEntry(BaseModel):
    ts: float
    level: str
    msg: str
    step: int = 0


class ScanProgressOut(BaseModel):
    state: str = "idle"
    job_id: str | None = None
    total: int = 0
    current: int = 0
    percent: float = 0.0
    current_transponder: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    services_found: int = 0
    transponders_locked: int = 0
    transponders_scanned: int = 0
    adapters: list[int] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    message: str = ""


class ScanStartOut(BaseModel):
    ok: bool = True
    job_id: str


class ScanStatusOut(BaseModel):
    progress: ScanProgressOut
    log: list[ScanLogEntry] = Field(default_factory=list)
    services: list["ServiceOut"] = Field(default_factory=list)


class ScanTableOut(BaseModel):
    name: str
    sat_position: str | None = None
    transponders: int = 0
    path: str | None = None


class ScanTablesOut(BaseModel):
    bundles: list[ScanTableOut] = Field(default_factory=list)
    dir: str = "/usr/share/dvb"


# ---------------------------------------------------------------------------
# Canales y transponders
# ---------------------------------------------------------------------------
class ServiceOut(BaseModel):
    id: int
    transponder_id: int
    service_id: int
    name: str
    provider: str = ""
    service_type: int = 0
    service_type_label: str = "TV"
    is_fta: bool = True
    scrambled: bool = False
    video_pid: int | None = None
    audio_pid: int | None = None
    pcr_pid: int | None = None
    pmt_pid: int | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    audio_pids: list[int] = Field(default_factory=list)
    video_pids: list[int] = Field(default_factory=list)
    ca_system_ids: list[int] = Field(default_factory=list)
    delivery_system: str
    frequency_khz: int
    polarization: str | None = None
    symbol_rate_ksps: int | None = None
    sat_position: str | None = None
    created_at: datetime
    custom_name: str | None = None
    display_name: str


class ChannelListOut(BaseModel):
    total: int
    items: list[ServiceOut]


class ChannelPatch(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    custom_name: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def _at_least_one(self) -> "ChannelPatch":
        if self.name is None and self.custom_name is None:
            raise ValueError("Indica 'name' o 'custom_name'")
        return self


class TransponderOut(BaseModel):
    id: int
    delivery_system: str
    frequency_khz: int
    polarization: str | None = None
    symbol_rate_ksps: int | None = None
    fec: str | None = None
    modulation: str | None = None
    bandwidth_mhz: int | None = None
    sat_position: str | None = None
    last_signal_percent: float | None = None
    last_snr_db: float | None = None
    label: str = ""
    services_count: int = 0
    updated_at: datetime


class TransponderListOut(BaseModel):
    total: int
    items: list[TransponderOut]


# ---------------------------------------------------------------------------
# Ajustes
# ---------------------------------------------------------------------------
class LnbSettings(BaseModel):
    low_khz: int = Field(default=9750, ge=0, le=30000)
    high_khz: int = Field(default=10600, ge=0, le=30000)
    threshold_khz: int = Field(default=11700, ge=0, le=30000)
    tone_22k: bool = False
    voltage: Literal["13V", "18V", "OFF"] = "13V"


class ScanSettings(BaseModel):
    lock_timeout_s: float = Field(default=5.0, gt=0, le=60)
    dwell_seconds: float = Field(default=8.0, gt=0, le=60)
    lock_timeout_ms: int = Field(default=500, ge=50, le=5000)


class UiSettings(BaseModel):
    signal_poll_ms: int = Field(default=500, ge=100, le=5000)
    history_seconds: int = Field(default=60, ge=10, le=600)


class SettingsOut(BaseModel):
    default_adapter: int = 0
    default_frontend: int = 0
    mock: bool = False
    lnb: LnbSettings = Field(default_factory=LnbSettings)
    scan: ScanSettings = Field(default_factory=ScanSettings)
    ui: UiSettings = Field(default_factory=UiSettings)


class SettingsIn(BaseModel):
    default_adapter: int | None = Field(default=None, ge=0, le=31)
    default_frontend: int | None = Field(default=None, ge=0, le=7)
    lnb: LnbSettings | None = None
    scan: ScanSettings | None = None
    ui: UiSettings | None = None


class HealthOut(BaseModel):
    status: str = "ok"
    mock: bool = False
    version: str = "1.0.0"
    adapters: int = 0


# ---------------------------------------------------------------------------
# Newcamd (card sharing propio o autorizado)
# ---------------------------------------------------------------------------
DESKEY_DEFAULT = "0102030405060708091011121314"


class NewcamdStatusOut(BaseModel):
    state: str = "disconnected"
    message: str = ""
    connected_at: float | None = None
    last_error: str | None = None
    ecm_sent: int = 0
    cw_received: int = 0
    last_ecm_at: float | None = None
    last_cw_at: float | None = None
    last_cw: str | None = None
    bytes_in: int = 0
    bytes_out: int = 0


class NewcamdAccountOut(BaseModel):
    id: int
    name: str
    host: str
    port: int
    username: str
    password_set: bool = False
    deskey: str = DESKEY_DEFAULT
    enabled: bool = True
    auto_connect: bool = False
    notes: str = ""
    created_at: str | None = None
    updated_at: str | None = None
    status: NewcamdStatusOut = Field(default_factory=NewcamdStatusOut)


class NewcamdListOut(BaseModel):
    total: int
    enabled: bool = True
    items: list[NewcamdAccountOut] = Field(default_factory=list)


class NewcamdAccountIn(BaseModel):
    """Alta o edición de una cuenta Newcamd. `password` vacío = no cambiar."""

    name: str = Field(min_length=1, max_length=64)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=50001, ge=1, le=65535)
    username: str = Field(default="", max_length=64)
    password: str | None = Field(default=None, max_length=128)
    deskey: str = Field(default=DESKEY_DEFAULT, min_length=28, max_length=32)
    enabled: bool = True
    auto_connect: bool = False
    notes: str | None = Field(default=None, max_length=500)

    @field_validator("deskey")
    @classmethod
    def _valid_deskey(cls, value: str) -> str:
        cleaned = value.strip().replace(" ", "").replace(":", "").lower()
        if len(cleaned) != 28:
            raise ValueError("La deskey debe tener 28 dígitos hexadecimales (14 bytes)")
        try:
            bytes.fromhex(cleaned)
        except ValueError as exc:
            raise ValueError(f"deskey no válida: {exc}") from exc
        return cleaned

    @field_validator("host")
    @classmethod
    def _clean_host(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or any(ch.isspace() for ch in cleaned):
            raise ValueError("Host no válido")
        return cleaned


class NewcamdTestIn(BaseModel):
    """Prueba de conexión sin guardar (o de una cuenta ya guardada por id)."""

    id: int | None = None
    host: str | None = None
    port: int = Field(default=50001, ge=1, le=65535)
    username: str = ""
    password: str | None = None
    deskey: str = DESKEY_DEFAULT


class NewcamdTestOut(BaseModel):
    ok: bool
    state: str
    message: str
    connected_at: float | None = None
    details: NewcamdStatusOut | None = None


class NewcamdActionOut(BaseModel):
    ok: bool
    id: int
    status: NewcamdStatusOut


class NewcamdEcmIn(BaseModel):
    """Envío manual de un ECM (diagnóstico)."""

    ecm_hex: str = Field(min_length=6, max_length=2048)
    service_id: int = Field(default=0, ge=0, le=65535)


class NewcamdEcmOut(BaseModel):
    ok: bool
    sent: bool
    account_id: int
    detail: str | None = None


class LogEntryOut(BaseModel):
    ts: datetime
    level: str
    source: str
    message: str
    extra: dict[str, Any] | None = None


class LogListOut(BaseModel):
    items: list[LogEntryOut]


# Resolución de referencias hacia delante
StatusOut.model_rebuild()
TuneResult.model_rebuild()
ScanStatusOut.model_rebuild()
