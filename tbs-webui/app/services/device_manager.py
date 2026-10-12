"""Gestión del dispositivo DVB: dueño único, sintonización y lectura de señal.

Responsabilidades:
- Un **único** dueño del hardware a la vez (`registry.DeviceLock`): sintonizar y
  escanear comparten el mismo frontend, así que el segundo en llegar recibe
  `DeviceBusyError` (HTTP 503).
- Traducir `TuneParams` (kHz del transponder) a los parámetros del frontend
  (frecuencia intermedia tras restar el LNB, tono 22 kHz, voltaje, DiSEqC).
- Mantener un hilo de lectura que muestrea las estadísticas cada
  `signal_poll_ms` y guarda el histórico de los últimos `history_seconds`,
  que el WebSocket reenvía a todos los clientes.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any

from ..config import Settings
from ..dvb import registry
from ..dvb.base import DeviceBusyError, FrontendError
from ..dvb.types import (
    FrontendInfo,
    LockFlags,
    SignalStats,
    Transponder,
    TuneParams,
)

logger = logging.getLogger(__name__)

# Etiquetas legibles de los bits de FE_READ_STATUS
LOCK_FLAG_LABELS = {
    "signal": "Señal",
    "carrier": "Portadora",
    "viterbi": "Viterbi",
    "sync": "Sincronismo",
    "lock": "LOCK",
}


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


class DeviceManager:
    """Fachada única sobre la capa `app.dvb`."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.RLock()
        self._frontend = None  # dvb.base.Frontend | None
        self._frontend_key: tuple[int, int] | None = None
        self._tuned: TuneParams | None = None
        self._stats: SignalStats | None = None
        self._history: deque[dict[str, float | None]] = deque(maxlen=4096)
        self._history_seconds = int(settings.history_seconds)
        self._poll_ms = int(settings.signal_poll_ms)
        self._monitor: threading.Thread | None = None
        self._monitor_stop = threading.Event()
        self._last_error: str | None = None

    # ------------------------------------------------------------------
    # Propiedades y consultas
    # ------------------------------------------------------------------
    @property
    def mock(self) -> bool:
        return registry.is_mock_mode()

    @property
    def lock_owner(self) -> str | None:
        return registry.get_lock().owner

    def list_adapters(self) -> list[FrontendInfo]:
        return registry.list_frontends()

    def lock_snapshot(self) -> dict[str, Any]:
        return registry.get_lock().snapshot()

    def current_stats(self) -> SignalStats | None:
        with self._lock:
            return self._stats

    def tuned_params(self) -> TuneParams | None:
        with self._lock:
            return self._tuned

    def history(self, seconds: float | None = None) -> list[dict[str, float | None]]:
        """Puntos del histórico dentro de la ventana pedida."""
        with self._lock:
            points = list(self._history)
        if not points:
            return []
        window = float(seconds if seconds is not None else self._history_seconds)
        cutoff = time.time() - window
        return [p for p in points if (p.get("t") or 0) >= cutoff]

    def status(self) -> dict[str, Any]:
        stats = self.current_stats()
        tuned = self.tuned_params()
        lock_state = self.lock_snapshot()
        return {
            "locked_by": lock_state.get("owner"),
            "waiting": lock_state.get("waiting", 0),
            "mock": self.mock,
            "adapter": tuned.adapter if tuned else None,
            "frontend": tuned.frontend if tuned else None,
            "tuned": tuned,
            "stats": stats,
            "error": self._last_error,
        }

    def set_poll_ms(self, poll_ms: int) -> None:
        """Ajusta el periodo de muestreo (lo llama /api/settings)."""
        with self._lock:
            self._poll_ms = max(100, min(5000, int(poll_ms)))

    def set_history_seconds(self, seconds: int) -> None:
        with self._lock:
            self._history_seconds = max(10, min(600, int(seconds)))

    # ------------------------------------------------------------------
    # Sintonización
    # ------------------------------------------------------------------
    def tune(self, params: TuneParams, owner: str = "api") -> SignalStats:
        """Sintoniza y devuelve las estadísticas tras esperar el lock.

        Lanza `DeviceBusyError` si otro proceso/operación posee el hardware.
        """
        device_lock = registry.get_lock()
        if not device_lock.acquire(owner, timeout=self.settings.device_lock_timeout):
            raise DeviceBusyError(
                f"El dispositivo está ocupado por '{device_lock.owner}'. "
                "Espera o detén la operación en curso."
            )
        try:
            frontend = self._ensure_frontend(params.adapter, params.frontend)
            self._apply_switching(frontend, params)
            logger.info(
                "Sintonizando %s %d kHz (IF %d kHz) SR=%s pol=%s adapter=%d",
                params.delivery_system,
                params.frequency_khz,
                params.if_frequency_khz,
                params.symbol_rate_ksps,
                params.polarization,
                params.adapter,
            )
            # La capa DVB decide si la frecuencia que recibe es la del transponder
            # o la intermedia: le pasamos el TuneParams tal cual, ya que
            # `TuneParams.if_frequency_khz` es la que se envía al frontend.
            frontend.tune(params)
            frontend.wait_lock(timeout_s=params.lock_timeout_s)
            stats = self._read_stats_safe(frontend)
            with self._lock:
                self._tuned = params
                self._stats = stats
                self._last_error = stats.error
            self._push_history(stats)
            self._ensure_monitor()
            return stats
        except DeviceBusyError:
            raise
        except FrontendError as exc:
            self._last_error = str(exc)
            with self._lock:
                self._tuned = params
            raise
        finally:
            # El lock se libera tras sintonizar: el frontend queda abierto y el
            # hilo monitor lo usa. El escaneo pedirá el lock de nuevo.
            device_lock.release(owner)

    def release(self) -> None:
        """Libera el hardware: para el monitor y cierra el frontend."""
        self._stop_monitor()
        with self._lock:
            frontend, self._frontend = self._frontend, None
            self._frontend_key = None
            self._tuned = None
            self._stats = None
            self._history.clear()
        if frontend is not None:
            try:
                frontend.close()
            except Exception:  # pragma: no cover - cierre defensivo
                logger.exception("Error cerrando el frontend")

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------
    def _ensure_frontend(self, adapter: int, frontend_id: int):
        """Abre (o reutiliza) el frontend solicitado."""
        key = (adapter, frontend_id)
        with self._lock:
            if self._frontend is not None and self._frontend_key == key:
                return self._frontend
            old = self._frontend
            self._frontend = None
            self._frontend_key = None
        if old is not None:
            try:
                old.close()
            except Exception:
                logger.debug("Error cerrando frontend anterior", exc_info=True)
        new_frontend = registry.open_frontend(adapter, frontend_id)
        new_frontend.open()
        with self._lock:
            self._frontend = new_frontend
            self._frontend_key = key
        return new_frontend

    def _apply_switching(self, frontend, params: TuneParams) -> None:
        """Aplica voltaje, tono 22 kHz y DiSEqC antes de sintonizar.

        La capa DVB expone estos helpers como métodos opcionales: si el backend
        (mock, por ejemplo) no los implementa, se ignoran sin romper.
        """
        if params.use_lnb:
            tone = params.needed_tone()
            voltage = params.needed_voltage()
        else:
            tone = bool(params.tone_22k)
            voltage = params.voltage or "OFF"

        setter = getattr(frontend, "set_voltage", None)
        if callable(setter) and voltage != "OFF":
            volts = 18 if voltage == "18V" else 13
            try:
                setter(volts)
            except FrontendError as exc:
                logger.warning("No se pudo fijar el voltaje %s: %s", voltage, exc)

        tone_setter = getattr(frontend, "set_tone", None)
        if callable(tone_setter):
            try:
                tone_setter(tone)
            except FrontendError as exc:
                logger.warning("No se pudo fijar el tono 22 kHz: %s", exc)

        if params.diseqc:
            sender = getattr(frontend, "send_diseqc", None)
            if callable(sender):
                try:
                    from ..dvb import diseqc as diseqc_mod

                    builder = getattr(diseqc_mod, "diseqc_command", None) or getattr(
                        diseqc_mod, "parse_diseqc", None
                    )
                    command = builder(params.diseqc) if callable(builder) else None
                    if command:
                        sender(command)
                except FrontendError as exc:
                    logger.warning("DiSEqC %s falló: %s", params.diseqc, exc)
                except Exception as exc:  # pragma: no cover - depende del driver
                    logger.warning("DiSEqC %s no soportado: %s", params.diseqc, exc)

    def _read_stats_safe(self, frontend) -> SignalStats:
        """Lee estadísticas capturando errores del driver."""
        try:
            return frontend.read_stats()
        except FrontendError as exc:
            logger.warning("Error leyendo estadísticas: %s", exc)
            self._last_error = str(exc)
            return SignalStats(
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
                error=str(exc),
            )

    def _push_history(self, stats: SignalStats) -> None:
        point = {
            "t": round(stats.timestamp, 3),
            "signal": stats.signal_percent,
            "snr": stats.snr_db,
            "ber": stats.ber,
        }
        with self._lock:
            self._history.append(point)

    # ------------------------------------------------------------------
    # Monitor de señal (hilo)
    # ------------------------------------------------------------------
    def _ensure_monitor(self) -> None:
        with self._lock:
            if self._monitor is not None and self._monitor.is_alive():
                return
            self._monitor_stop.clear()
            self._monitor = threading.Thread(
                target=self._monitor_loop, name="signal-monitor", daemon=True
            )
            self._monitor.start()

    def _stop_monitor(self) -> None:
        with self._lock:
            thread = self._monitor
            self._monitor = None
        if thread is None:
            return
        self._monitor_stop.set()
        thread.join(timeout=2.0)

    def _monitor_loop(self) -> None:
        """Muestrea estadísticas mientras haya frontend abierto y sintonizado."""
        logger.debug("Monitor de señal arrancado")
        while not self._monitor_stop.is_set():
            with self._lock:
                frontend = self._frontend
                poll_ms = self._poll_ms
            if frontend is None:
                break
            stats = self._read_stats_safe(frontend)
            with self._lock:
                self._stats = stats
            self._push_history(stats)
            self._monitor_stop.wait(max(0.05, poll_ms / 1000.0))
        logger.debug("Monitor de señal detenido")

    def stop(self) -> None:
        """Parada ordenada (se llama en el apagado de la aplicación)."""
        self.release()


def stats_to_dict(stats: SignalStats | None) -> dict[str, Any] | None:
    """Convierte SignalStats al JSON exacto de la API (INTERFACES.md §5)."""
    if stats is None:
        return None
    flags = stats.lock
    return {
        "timestamp": round(stats.timestamp, 3),
        "locked": bool(flags.is_locked),
        "signal_percent": None if stats.signal_percent is None else round(stats.signal_percent, 1),
        "signal_dbm": None if stats.signal_dbm is None else round(stats.signal_dbm, 1),
        "snr_db": None if stats.snr_db is None else round(stats.snr_db, 2),
        "cnr_raw": None if stats.cnr_raw is None else round(stats.cnr_raw, 3),
        "ber": None if stats.ber is None else stats.ber,
        "ber_pre": None if stats.ber_pre is None else stats.ber_pre,
        "per": None if stats.per is None else stats.per,
        "ucblocks": stats.ucblocks,
        "quality_percent": None if stats.quality_percent is None else round(stats.quality_percent, 1),
        "error": stats.error,
        "flags": {
            "signal": bool(flags.signal),
            "carrier": bool(flags.carrier),
            "viterbi": bool(flags.viterbi),
            "sync": bool(flags.sync),
            "lock": bool(flags.lock),
        },
    }


def transponder_to_params(transponder: Transponder, defaults: dict[str, Any] | None = None) -> TuneParams:
    """Convierte un `Transponder` del dominio en `TuneParams`."""
    defaults = defaults or {}
    lnb = defaults.get("lnb", {})
    scan = defaults.get("scan", {})
    return TuneParams(
        adapter=int(defaults.get("default_adapter", 0)),
        frontend=int(defaults.get("default_frontend", 0)),
        delivery_system=transponder.delivery_system,
        frequency_khz=transponder.frequency_khz,
        symbol_rate_ksps=transponder.symbol_rate_ksps,
        polarization=transponder.polarization,
        fec=transponder.fec,
        modulation=transponder.modulation,
        bandwidth_mhz=transponder.bandwidth_mhz,
        code_rate_hp=transponder.code_rate_hp,
        code_rate_lp=transponder.code_rate_lp,
        guard_interval=transponder.guard_interval,
        transmission_mode=transponder.transmission_mode,
        hierarchy=transponder.hierarchy,
        inversion=transponder.inversion,
        rolloff=transponder.rolloff,
        pilot=transponder.pilot,
        stream_id=transponder.stream_id,
        plp_id=transponder.plp_id,
        lnb_low_khz=int(lnb.get("low_khz", 9750)),
        lnb_high_khz=int(lnb.get("high_khz", 10600)),
        lnb_threshold_khz=int(lnb.get("threshold_khz", 11700)),
        use_lnb=bool(defaults.get("use_lnb", True)),
        lock_timeout_s=float(scan.get("lock_timeout_s", 5.0)),
    )
