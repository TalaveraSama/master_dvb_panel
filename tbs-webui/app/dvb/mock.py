"""Frontend simulado (mock) para desarrollar y testear sin hardware.

Reproduce el comportamiento observable de una tarjeta TBS 6904se (4 frontends
DVB-S/S2/S2x):

* El LOCK no es instantáneo: `LOCK_DELAY_S` (~1.5 s) tras `tune()`, con los
  bits de `FE_READ_STATUS` apareciendo progresivamente.
* Señal con ruido y variación senoidal (determinista por semilla).
* Servicios simulados (TV, radio y canales de pago) para el escaneo.
* Se puede forzar la ausencia de lock (`frequency_khz == 0`, `force_no_lock=True`
  o `lock_probability=0`) para probar los caminos de error.

No define `read_ts()`/`set_pid_list()` a propósito: así
`app/services/si_collector.py` se salta el demux y `scan_service` usa
`mock_services()` (que es la vía prevista para el modo simulación).
"""

from __future__ import annotations

import math
import random
import time

from .base import FrontendBase, split_diseqc_messages
from .types import (
    DeviceNotFoundError,
    FrontendInfo,
    LockFlags,
    ServiceInfo,
    SignalStats,
    TuneParams,
    UnsupportedError,
    normalize_delivery_system,
    service_type_label,
)

__all__ = ["MockFrontend", "mock_frontend_info", "mock_frontend_infos", "MOCK_FRONTEND_COUNT"]

# La tarjeta real reporta 4 adaptadores con este nombre (verificado con
# `dvb-fe-tool -a N` en el host; el nombre lleva espacio final en el ioctl).
MOCK_DEVICE_NAME = "TurboSight TBS 6904se DVB-S/S2/S2x"
MOCK_FRONTEND_COUNT = 4

# Capacidades de la TBS 6904se (enum fe_caps del kernel)
FE_CAN_FEC_1_2 = 0x2
FE_CAN_FEC_2_3 = 0x4
FE_CAN_FEC_3_4 = 0x8
FE_CAN_FEC_5_6 = 0x20
FE_CAN_FEC_7_8 = 0x80
FE_CAN_FEC_AUTO = 0x200
FE_CAN_QPSK = 0x400
FE_CAN_QAM_AUTO = 0x10000
FE_CAN_2G_MODULATION = 0x10000000
FE_CAN_MULTISTREAM = 0x4000000
FE_CAN_RECOVER = 0x40000000

MOCK_CAPS = (
    FE_CAN_FEC_1_2
    | FE_CAN_FEC_2_3
    | FE_CAN_FEC_3_4
    | FE_CAN_FEC_5_6
    | FE_CAN_FEC_7_8
    | FE_CAN_FEC_AUTO
    | FE_CAN_QPSK
    | FE_CAN_QAM_AUTO
    | FE_CAN_2G_MODULATION
    | FE_CAN_MULTISTREAM
    | FE_CAN_RECOVER
)

SATELLITE_SYSTEMS = ("DVB-S", "DVB-S2")
SUPPORTED_SYSTEMS = SATELLITE_SYSTEMS + ("DVB-T", "DVB-T2", "DVB-C")

# Catálogo de servicios simulados: (nombre, proveedor, tipo, ¿cifrado?)
MOCK_SERVICE_CATALOG: tuple[tuple[str, str, int, bool], ...] = (
    ("La 1 HD", "RTVE", 0x19, False),
    ("La 2", "RTVE", 0x01, False),
    ("Antena 3 HD", "Atresmedia", 0x19, False),
    ("Cuatro", "Mediaset", 0x01, False),
    ("Telecinco HD", "Mediaset", 0x19, True),
    ("Movistar+ Ficción", "Movistar", 0x19, True),
    ("DAZN LaLiga", "DAZN", 0x19, True),
    ("Radio Nacional", "RTVE", 0x02, False),
    ("Cadena SER", "PRISA", 0x02, False),
    ("Radio Clásica", "RTVE", 0x02, False),
    ("Canarias Radio", "RTVE", 0x02, False),
    ("Servicio de datos", "TBS", 0x0C, False),
)

# Códec por tipo de flujo simulado
_VIDEO_CODECS = ("MPEG-2", "H.264", "HEVC")
_AUDIO_CODECS = ("MPEG-1", "AAC", "AC-3")


def mock_frontend_info(adapter: int = 0, frontend: int = 0) -> FrontendInfo:
    """`FrontendInfo` equivalente al de la TBS 6904se real."""
    return FrontendInfo(
        adapter=int(adapter),
        frontend=int(frontend),
        name=f"{MOCK_DEVICE_NAME} (mock)",
        device_path=f"/dev/dvb/adapter{int(adapter)}/frontend{int(frontend)}",
        type="FE_QPSK",
        caps=MOCK_CAPS,
        delivery_systems=SATELLITE_SYSTEMS,
        min_frequency=950000,
        max_frequency=2150000,
        symbol_rates=(1_000_000, 45_000_000),
    )


def mock_frontend_infos(count: int = MOCK_FRONTEND_COUNT) -> list[FrontendInfo]:
    """Los N frontends del mock (4 por defecto, como la tarjeta real)."""
    return [mock_frontend_info(adapter, 0) for adapter in range(int(count))]


class MockFrontend(FrontendBase):
    """Frontend simulado que implementa `base.Frontend`."""

    is_mock = True
    LOCK_DELAY_S = 1.5

    def __init__(
        self,
        adapter: int = 0,
        frontend: int = 0,
        *,
        info: FrontendInfo | None = None,
        seed: int | None = None,
        force_no_lock: bool = False,
        lock_probability: float = 1.0,
    ) -> None:
        self.info = info or mock_frontend_info(adapter, frontend)
        self.seed = seed
        self.force_no_lock = bool(force_no_lock)
        self.lock_probability = float(lock_probability)
        self._opened = False
        self._tuned: TuneParams | None = None
        self._tune_at: float | None = None
        self._lockable = True
        self._random = random.Random(seed if seed is not None else int(time.time() * 1000) % 2**31)
        self._noise = 0.0
        self._noise_at = 0.0
        # Estado observable por los tests
        self.tone: bool | None = None
        self.voltage: int | None = None
        self.diseqc_log: list[bytes] = []
        self.open_count = 0
        self.close_count = 0

    # -- ciclo de vida -----------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self._opened

    def open(self) -> None:
        """Abre el frontend simulado (idempotente)."""
        if self._opened:
            return
        self._opened = True
        self.open_count += 1
        self._tuned = None
        self._tune_at = None
        self._lockable = True

    def close(self) -> None:
        """Cierra el frontend simulado (idempotente)."""
        if not self._opened:
            return
        self._opened = False
        self.close_count += 1
        self._tuned = None
        self._tune_at = None
        self._lockable = False

    def _require_open(self) -> None:
        if not self._opened:
            raise DeviceNotFoundError(
                f"El frontend simulado {self.info.device_path} no está abierto (open() primero)"
            )

    # -- sintonización -----------------------------------------------------
    def tune(self, params: TuneParams) -> None:
        """Sintoniza en el simulador: el lock tarda `LOCK_DELAY_S`."""
        self._require_open()
        system = normalize_delivery_system(params.delivery_system)
        if system not in SUPPORTED_SYSTEMS:
            raise UnsupportedError(
                f"El simulador de la TBS 6904se no soporta {params.delivery_system!r} "
                f"(soportados: {', '.join(SUPPORTED_SYSTEMS)})"
            )
        if int(params.frequency_khz) <= 0:
            raise UnsupportedError("Frecuencia inválida: debe ser > 0 kHz")
        self._tuned = params
        self._tune_at = time.monotonic()
        self._noise_at = 0.0
        # La tarjeta real sólo entrega LOCK si hay señal: se puede forzar el fallo.
        roll = self._random.random()
        self._lockable = not self.force_no_lock and roll <= self.lock_probability

    def _elapsed(self) -> float:
        if self._tune_at is None:
            return 0.0
        return time.monotonic() - self._tune_at

    @property
    def tuned_params(self) -> TuneParams | None:
        return self._tuned

    def wait_lock(self, timeout_s: float = 5.0, raise_on_timeout: bool = False) -> bool:
        """Espera al lock simulado (o al timeout)."""
        if self._tuned is None:
            return False
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        target = self.LOCK_DELAY_S if self._lockable else min(float(timeout_s), self.LOCK_DELAY_S)
        while self._elapsed() < target:
            if time.monotonic() >= deadline:
                break
            time.sleep(0.05)
        locked = self._lockable and self._elapsed() >= self.LOCK_DELAY_S
        if not locked and raise_on_timeout:
            from .types import LockTimeoutError

            raise LockTimeoutError(
                f"Sin LOCK simulado en {self.info.device_path} tras {timeout_s} s"
            )
        return locked

    def read_status(self) -> LockFlags:
        """Bits de estado progresivos hasta el lock (como el hardware real)."""
        self._require_open()
        if self._tuned is None or not self._lockable:
            return LockFlags(False, False, False, False, False, raw=0)
        elapsed = self._elapsed()
        delay = float(self.LOCK_DELAY_S)
        value = 0
        if elapsed >= 0.20 * delay:
            value |= 0x01  # FE_HAS_SIGNAL
        if elapsed >= 0.47 * delay:
            value |= 0x02  # FE_HAS_CARRIER
        if elapsed >= 0.67 * delay:
            value |= 0x04  # FE_HAS_VITERBI
        if elapsed >= 0.87 * delay:
            value |= 0x08  # FE_HAS_SYNC
        if elapsed >= delay:
            value |= 0x10  # FE_HAS_LOCK
        return LockFlags(
            signal=bool(value & 0x01),
            carrier=bool(value & 0x02),
            viterbi=bool(value & 0x04),
            sync=bool(value & 0x08),
            lock=bool(value & 0x10),
            raw=value,
        )

    def _noise_value(self) -> float:
        """Ruido suavizado (se recalcula como mucho cada 100 ms)."""
        now = time.monotonic()
        if now - self._noise_at >= 0.1:
            self._noise = self._random.uniform(-1.0, 1.0)
            self._noise_at = now
        return self._noise

    def read_stats(self) -> SignalStats:
        """Estadísticas simuladas (señal, CNR, BER) con ruido y senos."""
        self._require_open()
        timestamp = time.time()
        flags = self.read_status()
        if self._tuned is None:
            return SignalStats.empty("Frontend simulado sin sintonizar")
        elapsed = self._elapsed()
        phase = elapsed / 3.0
        noise = self._noise_value()
        if not self._lockable:
            # Sin señal: nivel bajo y sin CNR medible (como el hardware real).
            signal_percent = max(0.0, min(100.0, 8.0 + 5.0 * math.sin(phase) + 3.0 * noise))
            return SignalStats(
                timestamp=timestamp,
                lock=flags,
                signal_percent=round(signal_percent, 1),
                signal_dbm=None,
                snr_db=None,
                cnr_raw=None,
                ber=None,
                ber_pre=None,
                per=None,
                ucblocks=None,
                quality_percent=round(signal_percent / 4.0, 1),
                error=None,
            )
        if elapsed < self.LOCK_DELAY_S:
            signal_percent = max(0.0, min(100.0, 45.0 + 20.0 * (elapsed / self.LOCK_DELAY_S) + 4.0 * noise))
            return SignalStats(
                timestamp=timestamp,
                lock=flags,
                signal_percent=round(signal_percent, 1),
                signal_dbm=None,
                snr_db=None,
                cnr_raw=None,
                ber=None,
                ber_pre=None,
                per=None,
                ucblocks=0,
                quality_percent=round(signal_percent / 3.0, 1),
                error=None,
            )
        snr_db = 12.4 + 1.6 * math.sin(phase / 2.0) + 0.35 * noise
        signal_percent = max(0.0, min(100.0, 78.0 + 6.0 * math.sin(phase) + 2.5 * noise))
        signal_dbm = -42.5 + 2.0 * math.sin(phase) + 0.5 * noise
        ber = 0.0 if elapsed < 30.0 else max(0.0, self._random.uniform(0.0, 2e-6))
        ucblocks = int(max(0.0, elapsed - self.LOCK_DELAY_S) // 10)
        quality = min(100.0, max(0.0, snr_db / 15.0 * 100.0))
        return SignalStats(
            timestamp=timestamp,
            lock=flags,
            signal_percent=round(signal_percent, 1),
            signal_dbm=round(signal_dbm, 1),
            snr_db=round(snr_db, 2),
            cnr_raw=round(snr_db, 3),
            ber=ber,
            ber_pre=ber,
            per=ber,
            ucblocks=ucblocks,
            quality_percent=round(quality, 1),
            error=None,
        )

    # -- conmutación -------------------------------------------------------
    def set_tone(self, on: bool) -> None:
        """Guarda el estado del tono de 22 kHz."""
        self._require_open()
        self.tone = bool(on)

    def set_voltage(self, volts: int) -> None:
        """Guarda el voltaje del LNB (13 | 18 | 0)."""
        self._require_open()
        volts = int(volts)
        if volts not in (0, 13, 18):
            raise UnsupportedError(f"Voltaje no válido en el simulador: {volts} V")
        self.voltage = volts

    def send_diseqc(self, cmd: bytes, repeats: int = 0) -> None:
        """Registra los mensajes DiSEqC recibidos (una entrada por mensaje)."""
        self._require_open()
        for message in split_diseqc_messages(bytes(cmd)):
            self.diseqc_log.append(message)

    # -- servicios simulados ----------------------------------------------
    def mock_services(self, transponder: object | None = None) -> list[ServiceInfo]:
        """Servicios simulados del transponder (6..12, con radio y cifrados)."""
        frequency = int(getattr(transponder, "frequency_khz", 0) or 0)
        offset = (frequency // 1000) % len(MOCK_SERVICE_CATALOG)
        catalog = list(MOCK_SERVICE_CATALOG)
        rotated = catalog[offset:] + catalog[:offset]
        count = 6 + (offset % 7)  # entre 6 y 12 servicios
        base_pid = 256 + (offset % 32)
        services: list[ServiceInfo] = []
        for index, (name, provider, service_type, scrambled) in enumerate(rotated[:count]):
            service_id = 1000 + offset * 100 + index
            video_pid = None
            audio_pid = None
            video_codec = None
            audio_codec = None
            audio_pids: tuple[int, ...] = ()
            video_pids: tuple[int, ...] = ()
            if service_type in (0x01, 0x11, 0x16, 0x19, 0x1F):
                video_pid = base_pid + index * 10 + 1
                audio_pid = video_pid + 1
                video_codec = _VIDEO_CODECS[index % len(_VIDEO_CODECS)]
                audio_codec = _AUDIO_CODECS[index % len(_AUDIO_CODECS)]
                video_pids = (video_pid,)
                audio_pids = (audio_pid,)
            elif service_type == 0x02:
                audio_pid = base_pid + index * 10 + 2
                audio_codec = _AUDIO_CODECS[index % len(_AUDIO_CODECS)]
                audio_pids = (audio_pid,)
            services.append(
                ServiceInfo(
                    service_id=service_id,
                    name=name,
                    provider=provider,
                    service_type=service_type,
                    service_type_label=service_type_label(service_type),
                    is_fta=not scrambled,
                    scrambled=scrambled,
                    video_pid=video_pid,
                    audio_pid=audio_pid,
                    pcr_pid=video_pid,
                    video_codec=video_codec,
                    audio_codec=audio_codec,
                    pmt_pid=base_pid + index + 16,
                    ca_system_ids=(0x0B00,) if scrambled else (),
                    audio_pids=audio_pids,
                    video_pids=video_pids,
                )
            )
        return services

    # Alias cómodo (por si el consumidor usa `services()`).
    def services(self, transponder: object | None = None) -> list[ServiceInfo]:
        return self.mock_services(transponder)
