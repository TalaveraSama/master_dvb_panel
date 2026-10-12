"""Frontend "plan B": sintonización con las herramientas DVBv5 del sistema.

`IoctlFrontend` (ioctl directo) es el modo **preferido**: se ha verificado en la
tarjeta real (TBS 6904se) que acepta todas las propiedades de sintonización,
devuelve nombre/caps/delivery systems y lee las estadísticas. Este módulo añade
un camino alternativo que no toca `/dev/dvb` desde Python:

* **tune**: lanza `dvbv5-zap -m` (modo monitor) sobre un fichero de canales
  DVBv5 generado al vuelo. La herramienta fija el LNB (voltaje/tono/DiSEqC) y
  sintoniza; imprime estado y calidad periódicamente.
* **stats/status**: se parsean las líneas de `dvbv5-zap -m`
  (``(0x1f) Signal= -51,70dBm`` / ``Layer A: Signal= 80,08%`` / ``C/N= 12,40dB``),
  que es el mismo formato que `dvb-fe-tool -m`.
* **servicios**: `dvbv5-scan` vía `tools.scan_transponder_services()`.

Elección del modo (`app/dvb/registry.py`):

* ``TBS_IOCTL=0`` (o ``TBS_FRONTEND=tools``) -> `ToolsFrontend`
* en cualquier otro caso -> `IoctlFrontend`, y si el ioctl falla con
  `UnsupportedError` se puede pedir el plan B explícitamente
  (`registry.open_frontend(..., prefer_tools=True)`).

Limitación conocida y documentada: este frontend **no** ofrece
`set_pid_list()`/`read_ts()`, así que `si_collector` se salta el demux y el
escaneo usa `dvbv5-scan` (que es exactamente el plan B previsto). Tampoco puede
leer `DTV_STAT_*` crudos: sólo la salida de texto de la herramienta.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from .base import FrontendBase
from .frontend_ioctl import decode_status
from .tools import tool_available, transponder_to_dvbv5
from .types import (
    DeviceNotFoundError,
    DvbError,
    FrontendError,
    FrontendInfo,
    LockFlags,
    SignalStats,
    TuneParams,
    UnsupportedError,
)

logger = logging.getLogger(__name__)

__all__ = ["ToolsFrontend", "TOOLS_ENV_VAR"]

TOOLS_ENV_VAR = "TBS_IOCTL"

# Salida real de `dvbv5-zap -m` en el host (locale C por LC_ALL):
#        (0x1f) Signal= -51,70dBm
#           Layer A: Signal= 80,08%
#           Layer A: C/N= 12,40dB
_STATUS_RE = re.compile(r"\(0x([0-9A-Fa-f]{2,8})\)")
_SIGNAL_DBM_RE = re.compile(r"Signal\s*=\s*(-?\d+(?:[.,]\d+)?)\s*dBm", re.IGNORECASE)
_SIGNAL_PERCENT_RE = re.compile(r"Signal\s*=\s*(\d+(?:[.,]\d+)?)\s*%", re.IGNORECASE)
_CNR_RE = re.compile(r"(?:C/N|CNR|SNR)\s*[:=]\s*(\d+(?:[.,]\d+)?)\s*dB", re.IGNORECASE)
_BER_RE = re.compile(r"(?:post-?)?BER\s*[:=]\s*(\d+(?:[.,]\d+)?)", re.IGNORECASE)
_UCB_RE = re.compile(r"(?:UCB|UNC)\s*[:=]\s*(\d+)", re.IGNORECASE)


def _to_float(text: str | None) -> float | None:
    if text is None:
        return None
    try:
        return float(str(text).replace(",", "."))
    except ValueError:
        return None


class ToolsFrontend(FrontendBase):
    """Frontend que sintoniza con `dvbv5-zap` y lee la señal de su salida.

    Implementa el mismo contrato que `IoctlFrontend`
    (`open/close/tune/wait_lock/read_status/read_stats/set_tone/set_voltage/
    send_diseqc`), sin `set_pid_list`/`read_ts` a propósito.
    """

    is_mock = False

    def __init__(
        self,
        info: FrontendInfo,
        *,
        lnbf: str = "UNIVERSAL",
        zap_timeout_s: float = 60.0,
        channel_name: str = "TBSWEBUI",
    ) -> None:
        self.info = info
        self.lnbf = lnbf
        self.zap_timeout_s = float(zap_timeout_s)
        self.channel_name = channel_name
        self._process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._opened = False
        self._channels_file: str | None = None
        self._tuned: TuneParams | None = None
        self._status = 0
        self._signal_dbm: float | None = None
        self._signal_percent: float | None = None
        self._cnr_db: float | None = None
        self._ber: float | None = None
        self._ucblocks: int | None = None
        self._updated_at: float | None = None
        self._error: str | None = None
        self.tone: bool | None = None
        self.voltage: int | None = None
        self.diseqc_log: list[bytes] = []
        self.command: tuple[str, ...] = ()

    # -- ciclo de vida -----------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self._opened

    def open(self) -> None:
        """Comprueba que las herramientas existen (no abre /dev/dvb)."""
        if self._opened:
            return
        if not tool_available("dvbv5-zap"):
            raise UnsupportedError(
                "El plan B necesita 'dvbv5-zap' (paquete dvb-tools); no está instalado"
            )
        self._opened = True
        self._stop.clear()
        logger.debug("ToolsFrontend listo para %s", self.info.device_path)

    def close(self) -> None:
        """Termina el proceso de `dvbv5-zap` y limpia (idempotente)."""
        process, self._process = self._process, None
        self._opened = False
        self._stop.set()
        if process is not None:
            try:
                process.terminate()
                try:
                    process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:  # pragma: no cover
                    process.kill()
                    process.wait(timeout=3.0)
            except Exception:  # pragma: no cover - defensivo
                logger.debug("Error cerrando dvbv5-zap", exc_info=True)
        reader, self._reader = self._reader, None
        if reader is not None and reader.is_alive():
            reader.join(timeout=2.0)
        if self._channels_file:
            try:
                os.unlink(self._channels_file)
            except OSError:  # pragma: no cover
                pass
            self._channels_file = None
        self._tuned = None

    # -- sintonización -----------------------------------------------------
    def tune(self, params: TuneParams) -> None:
        """Escribe el canal y lanza `dvbv5-zap -m` en segundo plano."""
        if not self.is_open:
            raise DeviceNotFoundError("ToolsFrontend no está abierto (open() primero)")
        self.close()
        self.open()
        self._tuned = params
        texto = transponder_to_dvbv5(
            self._transponder_from_params(params), name=self.channel_name
        )
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".conf", prefix="tbs-zap-", delete=False, encoding="utf-8"
        )
        try:
            handle.write(texto)
        finally:
            handle.close()
        self._channels_file = handle.name

        self.command = (
            "dvbv5-zap",
            "-a", str(int(self.info.adapter)),
            "-f", str(int(self.info.frontend)),
            "-c", self._channels_file,
            "-l", self.lnbf,
            "-m",
            "-t", str(int(self.zap_timeout_s)),
            self.channel_name,
        )
        env = dict(os.environ)
        env["LC_ALL"] = "C"
        env["LANG"] = "C"
        try:
            self._process = subprocess.Popen(
                list(self.command),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
            )
        except OSError as exc:
            self._process = None
            raise DvbError(f"No se pudo lanzar dvbv5-zap: {exc}") from exc
        self._reader = threading.Thread(
            target=self._read_loop, name="tools-frontend-reader", daemon=True
        )
        self._reader.start()
        logger.info("dvbv5-zap lanzado: %s", " ".join(self.command))

    def _transponder_from_params(self, params: TuneParams):
        from .types import Transponder

        return Transponder(
            delivery_system=params.delivery_system,
            frequency_khz=int(params.frequency_khz),
            symbol_rate_ksps=params.symbol_rate_ksps,
            polarization=params.polarization,
            fec=params.fec,
            modulation=params.modulation,
            bandwidth_mhz=params.bandwidth_mhz,
            inversion=params.inversion,
            rolloff=params.rolloff,
            pilot=params.pilot,
            stream_id=params.stream_id,
            plp_id=params.plp_id,
            code_rate_hp=params.code_rate_hp,
            code_rate_lp=params.code_rate_lp,
            guard_interval=params.guard_interval,
            transmission_mode=params.transmission_mode,
            hierarchy=params.hierarchy,
        )

    # -- lectura de la salida ---------------------------------------------
    def _read_loop(self) -> None:  # pragma: no cover - hilo con E/S real
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            for line in process.stdout:
                if self._stop.is_set():
                    break
                self.feed_line(line)
        except Exception as exc:  # pragma: no cover
            logger.debug("Lector de dvbv5-zap terminó: %s", exc)
        finally:
            if process.poll() not in (0, None):
                with self._lock:
                    if self._error is None:
                        self._error = f"dvbv5-zap terminó con código {process.returncode}"

    def feed_line(self, line: str) -> None:
        """Procesa una línea de la salida de `dvbv5-zap -m` (testable)."""
        text = line.strip()
        if not text:
            return
        if text.startswith("ERROR") or text.startswith("WARN"):
            with self._lock:
                self._error = text[:200]
            return
        status = _STATUS_RE.search(text)
        signal_dbm = _SIGNAL_DBM_RE.search(text)
        signal_percent = _SIGNAL_PERCENT_RE.search(text)
        cnr = _CNR_RE.search(text)
        ber = _BER_RE.search(text)
        ucb = _UCB_RE.search(text)
        if not any((status, signal_dbm, signal_percent, cnr, ber, ucb)):
            return
        with self._lock:
            if status:
                try:
                    self._status = int(status.group(1), 16)
                except ValueError:
                    pass
            if signal_dbm:
                self._signal_dbm = _to_float(signal_dbm.group(1))
            if signal_percent:
                self._signal_percent = _to_float(signal_percent.group(1))
            if cnr:
                self._cnr_db = _to_float(cnr.group(1))
            if ber:
                self._ber = _to_float(ber.group(1))
            if ucb:
                self._ucblocks = int(ucb.group(1))
            self._updated_at = time.time()

    # -- contrato del frontend --------------------------------------------
    def wait_lock(self, timeout_s: float = 5.0, raise_on_timeout: bool = False) -> bool:
        """Espera a ver FE_HAS_LOCK en la salida de la herramienta."""
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        while True:
            if self.read_status().is_locked:
                return True
            if time.monotonic() >= deadline:
                if raise_on_timeout:
                    from .types import LockTimeoutError

                    raise LockTimeoutError(
                        f"Sin LOCK con dvbv5-zap tras {timeout_s} s ({self._error or 'sin error'})"
                    )
                return False
            time.sleep(0.1)

    def read_status(self) -> LockFlags:
        """`LockFlags` a partir del último ``(0x..)`` leído."""
        with self._lock:
            return decode_status(self._status)

    def read_stats(self) -> SignalStats:
        """Estadísticas a partir de la última línea parseada."""
        with self._lock:
            flags = decode_status(self._status)
            stats = SignalStats(
                timestamp=self._updated_at or time.time(),
                lock=flags,
                signal_percent=self._signal_percent,
                signal_dbm=self._signal_dbm,
                snr_db=self._cnr_db,
                cnr_raw=self._cnr_db,
                ber=self._ber,
                ber_pre=None,
                per=None,
                ucblocks=self._ucblocks,
                quality_percent=(
                    None
                    if self._cnr_db is None
                    else round(max(0.0, min(100.0, self._cnr_db / 15.0 * 100.0)), 1)
                ),
                error=self._error,
            )
        return stats

    # -- conmutación (la hace la propia herramienta) -----------------------
    def set_tone(self, on: bool) -> None:
        """Se registra; `dvbv5-zap` fija el tono según el LNBF y la banda."""
        self.tone = bool(on)
        logger.debug("ToolsFrontend: tono 22k=%s (lo aplica dvbv5-zap con -l %s)", on, self.lnbf)

    def set_voltage(self, volts: int) -> None:
        """Se registra; `dvbv5-zap` fija el voltaje según el LNBF."""
        volts = int(volts)
        if volts not in (0, 13, 18):
            raise UnsupportedError(f"Voltaje no válido: {volts} V (usa 13, 18 o 0)")
        self.voltage = volts
        logger.debug("ToolsFrontend: voltaje=%s V (lo aplica dvbv5-zap con -l %s)", volts, self.lnbf)

    def send_diseqc(self, cmd: bytes, repeats: int = 0) -> None:
        """Se registra; para DiSEqC real usa `IoctlFrontend` o `dvbv5-zap -S`."""
        self.diseqc_log.append(bytes(cmd))
        logger.debug(
            "ToolsFrontend: DiSEqC %s registrado (usa el ioctl o 'dvbv5-zap -S N')",
            bytes(cmd).hex(),
        )

    def services(self, transponder: Any) -> list[Any]:
        """Servicios del transponder con `dvbv5-scan` (plan B del escaneo)."""
        from . import tools as dvb_tools

        return dvb_tools.scan_transponder_services(self.info, transponder, timeout=self.zap_timeout_s)

    # Sin `set_pid_list`/`read_ts` a propósito: `si_collector` se salta el demux
    # y el escaneo usa `dvbv5-scan`.
