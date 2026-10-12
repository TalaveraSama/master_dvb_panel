"""Servicio de escaneo de transponders en segundo plano.

- Un único escaneo a la vez (comparte el hardware con la sintonización a través
  de `registry.DeviceLock`).
- Progreso, log en vivo y servicios encontrados en memoria (consultables desde
  la API mientras corre).
- Persistencia en SQLite con claves anti-duplicados (ver `channels_service`).

Estrategia por transponder:
1. Sintonizar y esperar LOCK.
2. Escuchar PSI con el colector integrado (`si_collector`).
3. Si el colector no puede leer (frontend sin demux) se usa `dvbv5-scan` como
   plan B; en modo mock se generan servicios simulados.
4. Guardar transponder + servicios y anotar señal/SNR medidos.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy.orm import Session

from ..config import Settings
from ..database import session_scope
from ..dvb import registry
from ..dvb.base import DeviceBusyError, FrontendError
from ..dvb.types import ScanProgress, ServiceInfo, Transponder, TuneParams
from ..models import ScanJob, utcnow
from . import channels_service, si_collector
from .device_manager import DeviceManager

logger = logging.getLogger(__name__)

MAX_LOG_ENTRIES = 800


@dataclass
class ScanLog:
    """Buffer circular de log del escaneo actual."""

    entries: list[dict[str, Any]] = field(default_factory=list)

    def add(self, msg: str, level: str = "info", step: int | None = None) -> None:
        entry = {
            "ts": round(time.time(), 3),
            "level": level,
            "msg": msg,
            "step": step if step is not None else len(self.entries),
        }
        self.entries.append(entry)
        if len(self.entries) > MAX_LOG_ENTRIES:
            del self.entries[: len(self.entries) - MAX_LOG_ENTRIES]
        getattr(logger, "info" if level == "info" else level, logger.info)("[scan] %s", msg)

    def since(self, timestamp: float | None) -> list[dict[str, Any]]:
        if timestamp is None:
            return list(self.entries)
        return [e for e in self.entries if e["ts"] > timestamp]

    def clear(self) -> None:
        self.entries.clear()


class ScanService:
    """Orquestador del escaneo."""

    def __init__(self, settings: Settings, device_manager: DeviceManager) -> None:
        self.settings = settings
        self.device = device_manager
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._progress = ScanProgress(state="idle", total=0, current=0, percent=0.0)
        self._log = ScanLog()
        self._services: list[tuple[ServiceInfo, Transponder]] = []
        self._job_id: str | None = None

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    @property
    def progress(self) -> ScanProgress:
        with self._lock:
            return self._progress

    @property
    def job_id(self) -> str | None:
        with self._lock:
            return self._job_id

    def log(self, since: float | None = None) -> list[dict[str, Any]]:
        with self._lock:
            return self._log.since(since)

    def services(self) -> list[ServiceInfo]:
        with self._lock:
            return [info for info, _ in self._services]

    def services_with_transponder(self) -> list[tuple[ServiceInfo, Transponder]]:
        """Servicios encontrados junto al transponder del que provienen."""
        with self._lock:
            return list(self._services)

    def start(self, request: Any) -> str:
        """Lanza un escaneo. Devuelve el job_id. DeviceBusyError si ya hay uno."""
        with self._lock:
            if self.is_running():
                raise DeviceBusyError("Ya hay un escaneo en curso")

        transponders = self._resolve_transponders(request)
        if not transponders:
            raise ValueError("No hay transponders que escanear: indica una tabla, una lista o sintoniza antes")

        adapter_refs = getattr(request, "adapters", None) or []
        adapters = [int(getattr(ref, "adapter", 0)) for ref in adapter_refs] or [self.settings.default_adapter]
        frontend_id = int(getattr(adapter_refs[0], "frontend", 0)) if adapter_refs else self.settings.default_frontend

        job_id = str(uuid.uuid4())
        dwell = float(getattr(request, "dwell_seconds", 8.0) or 8.0)
        lock_timeout = float(getattr(request, "lock_timeout_s", self.settings.lock_timeout_s) or 5.0)

        with self._lock:
            self._job_id = job_id
            self._stop_event = threading.Event()
            self._services = []
            self._log.clear()
            self._progress = ScanProgress(
                state="running",
                total=len(transponders),
                current=0,
                percent=0.0,
                started_at=time.time(),
                message=f"Escaneando {len(transponders)} transponders",
            )
            self._thread = threading.Thread(
                target=self._run,
                name="scan-worker",
                daemon=True,
                kwargs={
                    "job_id": job_id,
                    "transponders": transponders,
                    "adapter": adapters[0],
                    "frontend": frontend_id,
                    "dwell_seconds": dwell,
                    "lock_timeout_s": lock_timeout,
                    "clear_existing": bool(getattr(request, "clear_existing", False)),
                },
            )
            self._thread.start()
        self._record_job(job_id, adapters[0], frontend_id, len(transponders), getattr(request, "table", None))
        return job_id

    def stop(self) -> bool:
        """Pide la parada del escaneo en curso. True si había algo que parar."""
        with self._lock:
            if not self.is_running():
                return False
            self._progress = self._clone(
                state="stopping", message="Deteniendo escaneo…"
            )
            self._log.add("Parada solicitada por el usuario", level="warning")
        self._stop_event.set()
        return True

    def list_tables(self) -> list[dict[str, Any]]:
        """Tablas de dvb-scan-tables disponibles en el sistema."""
        from ..dvb import satxml

        try:
            return satxml.list_tables(self.settings.scan_tables_dir)
        except Exception:  # pragma: no cover - sistema sin dtv-scan-tables
            logger.exception("No se pudieron listar las tablas de escaneo")
            return []

    def _clone(self, **changes: Any) -> ScanProgress:
        """Copia el progreso actual aplicando cambios (evita mutar el dataclass)."""
        data = {field: getattr(self._progress, field) for field in ScanProgress.__dataclass_fields__}
        data.update(changes)
        # `job_id` no forma parte del dataclass del dominio: se inyecta aquí para
        # que la API pueda informar de qué trabajo está en curso.
        return ScanProgress(**data)  # type: ignore[arg-type]

    def progress_dict(self) -> dict[str, Any]:
        """Progreso serializable, incluyendo el `job_id` actual."""
        with self._lock:
            data = {field: getattr(self._progress, field) for field in ScanProgress.__dataclass_fields__}
            data["job_id"] = self._job_id
            return data

    def wait(self, timeout: float | None = None) -> None:
        """Espera a que el escaneo termine (tests y apagado ordenado)."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)

    # ------------------------------------------------------------------
    # Resolución de la lista de transponders
    # ------------------------------------------------------------------
    def _resolve_transponders(self, request: Any) -> list[Transponder]:
        explicit = getattr(request, "transponders", None)
        if getattr(request, "current_only", False):
            tuned = self.device.tuned_params()
            if tuned is None:
                raise ValueError("No hay ningún transponder sintonizado para 'current_only'")
            return [
                Transponder(
                    delivery_system=tuned.delivery_system,
                    frequency_khz=tuned.frequency_khz,
                    symbol_rate_ksps=tuned.symbol_rate_ksps,
                    polarization=tuned.polarization,
                    fec=tuned.fec,
                    modulation=tuned.modulation,
                    bandwidth_mhz=tuned.bandwidth_mhz,
                    inversion=tuned.inversion,
                    rolloff=tuned.rolloff,
                    pilot=tuned.pilot,
                    stream_id=tuned.stream_id,
                    plp_id=tuned.plp_id,
                )
            ]
        if explicit:
            from ..schemas import TransponderIn

            result: list[Transponder] = []
            for item in explicit:
                if isinstance(item, TransponderIn):
                    result.append(Transponder(**item.model_dump()))
                elif isinstance(item, dict):
                    result.append(Transponder(**item))
                elif isinstance(item, Transponder):
                    result.append(item)
            return result

        table = getattr(request, "table", None)
        if table:
            return self._load_table(table)
        return []

    def _load_table(self, name: str) -> list[Transponder]:
        """Carga una tabla de dvb-scan-tables por **nombre**, no por ruta.

        SEGURIDAD: el nombre que llega del cliente NUNCA se usa como ruta
        arbitraria. Sólo se aceptan:
          * un nombre simple (sin separadores ni `..`) buscado dentro de
            `TBS_SCAN_TABLES_DIR` (dvb-s, dvb-t, dvb-c), o
          * una ruta absoluta que esté **dentro** de ese directorio.

        Así se evita el *path traversal* (leer `/etc/shadow`, la clave de firma,
        etc.) a través de `POST /api/scan/start`.
        """
        import re
        from pathlib import Path

        from ..dvb import satxml

        if not name or not name.strip():
            raise ValueError("Nombre de tabla vacío")

        tables_dir = Path(self.settings.scan_tables_dir).resolve()
        raw = name.strip().replace("\\", "/")
        candidate: Path | None = None

        if "/" in raw:
            # Sólo se admite una ruta si resuelve dentro del directorio permitido.
            resolved = Path(raw).expanduser().resolve()
            if resolved == tables_dir or tables_dir in resolved.parents:
                candidate = resolved
            else:
                logger.warning("Intento de leer una tabla fuera de %s: %r", tables_dir, name)
                raise ValueError("La tabla debe estar dentro del directorio de tablas de escaneo")
        else:
            # Nombre simple: nunca se interpreta como ruta.
            if not re.fullmatch(r"[A-Za-z0-9._+\-]{1,120}", raw):
                raise ValueError("Nombre de tabla no válido")
            for base in ("dvb-s", "dvb-t", "dvb-c"):
                directory = tables_dir / base
                if not directory.is_dir():
                    continue
                direct = directory / raw
                if direct.is_file():
                    candidate = direct
                    break
                for file in sorted(directory.iterdir()):
                    if file.is_file() and (file.name == raw or file.stem == raw):
                        candidate = file
                        break
                if candidate is not None:
                    break

        if candidate is None or not candidate.is_file():
            raise ValueError(f"Tabla de escaneo no encontrada: {name}")

        # Doble comprobación (defensa en profundidad): el fichero final sigue
        # estando dentro del directorio permitido.
        resolved = candidate.resolve()
        if tables_dir not in resolved.parents:
            raise ValueError("La tabla debe estar dentro del directorio de tablas de escaneo")
        return satxml.parse_tables_file(resolved)

    # ------------------------------------------------------------------
    # Trabajo en segundo plano
    # ------------------------------------------------------------------
    def _run(
        self,
        job_id: str,
        transponders: list[Transponder],
        adapter: int,
        frontend: int,
        dwell_seconds: float,
        lock_timeout_s: float,
        clear_existing: bool,
    ) -> None:
        device_lock = registry.get_lock()
        owner = f"scan:{job_id[:8]}"
        acquired = False
        errors: list[str] = []
        locked_count = 0
        scanned = 0
        services_total = 0

        try:
            self._log.add(f"Solicitando hardware (adaptador {adapter}, frontend {frontend})")
            try:
                if not device_lock.acquire(owner, timeout=self.settings.device_lock_timeout):
                    raise DeviceBusyError(
                        f"El dispositivo está ocupado por '{device_lock.owner}'. "
                        "Detén la sintonización o espera a que termine."
                    )
            except DeviceBusyError as exc:
                raise exc
            acquired = True

            fe = registry.open_frontend(adapter, frontend)
            fe.open()
            self._log.add(f"Frontend abierto: {getattr(fe.info, 'name', 'desconocido')}")

            for index, transponder in enumerate(transponders, start=1):
                if self._stop_event.is_set():
                    self._finish("cancelled", "Escaneo cancelado por el usuario", errors)
                    return

                label = self._label(transponder)
                self._update_progress(
                    current=index - 1,
                    current_transponder=label,
                    transponders_locked=locked_count,
                    transponders_scanned=scanned,
                    services_found=services_total,
                    message=f"[{index}/{len(transponders)}] {label}",
                )
                self._log.add(f"[{index}/{len(transponders)}] Sintonizando {label}")

                params = self._tune_params(transponder, adapter, frontend, lock_timeout_s)
                try:
                    fe.tune(params)
                    locked = fe.wait_lock(timeout_s=lock_timeout_s)
                    stats = fe.read_stats()
                except FrontendError as exc:
                    errors.append(f"{label}: {exc}")
                    self._log.add(f"Error sintonizando {label}: {exc}", level="error")
                    scanned += 1
                    continue

                scanned += 1
                if locked:
                    locked_count += 1
                    self._log.add(
                        f"LOCK en {label} — señal {self._fmt(stats.signal_percent, '%')} "
                        f"CNR {self._fmt(stats.snr_db, 'dB')}"
                    )
                else:
                    self._log.add(
                        f"Sin LOCK en {label} (señal {self._fmt(stats.signal_percent, '%')}); "
                        "se intenta leer igualmente",
                        level="warning",
                    )

                services = self._collect_services(fe, dwell_seconds, transponder)
                services_total += len(services)
                self._log.add(f"  {len(services)} servicios detectados")

                try:
                    with session_scope() as db:
                        row, created, updated = channels_service.save_transponder_services(
                            db,
                            transponder,
                            services,
                            clear_existing=clear_existing,
                            signal={
                                "signal_percent": stats.signal_percent,
                                "snr_db": stats.snr_db,
                                "locked": locked,
                            },
                        )
                    self._log.add(
                        f"  Guardado transponder #{row.id}: {created} nuevos, {updated} actualizados"
                    )
                except Exception as exc:  # pragma: no cover - errores de BD
                    logger.exception("Error guardando servicios de %s", label)
                    errors.append(f"{label}: error guardando ({exc})")
                    self._log.add(f"  Error guardando: {exc}", level="error")

                with self._lock:
                    self._services.extend((info, transponder) for info in services)

                self._update_progress(
                    current=index,
                    percent=round(index / len(transponders) * 100.0, 1),
                    current_transponder=label,
                    transponders_locked=locked_count,
                    transponders_scanned=scanned,
                    services_found=services_total,
                )

            if self._stop_event.is_set():
                self._finish("cancelled", "Escaneo cancelado por el usuario", errors)
            else:
                self._finish(
                    "done",
                    f"Escaneo completado: {services_total} servicios en {scanned} transponders",
                    errors,
                )
        except DeviceBusyError as exc:
            self._log.add(str(exc), level="error")
            self._finish("error", str(exc), errors)
        except Exception as exc:  # pragma: no cover - defensivo
            logger.exception("Escaneo falló inesperadamente")
            self._log.add(f"Fallo inesperado: {exc}", level="error")
            self._finish("error", str(exc), errors)
        finally:
            try:
                if acquired:
                    fe.close()  # type: ignore[possibly-undefined]
            except Exception:
                logger.debug("Error cerrando el frontend tras el escaneo", exc_info=True)
            if acquired:
                device_lock.release(owner)
            self._close_job()

    def _collect_services(self, fe: Any, dwell_seconds: float, transponder: Transponder) -> list[ServiceInfo]:
        """Obtiene los servicios del transponder por la mejor vía disponible."""
        # 1) Colector SI integrado (ioctl/demux)
        if si_collector.frontend_supports_demux(fe):
            try:
                services = si_collector.collect_services(fe, dwell_seconds=dwell_seconds)
                if services:
                    return services
                self._log.add("  El colector SI no encontró servicios; se prueba dvbv5-scan", level="warning")
            except Exception as exc:  # pragma: no cover - depende del hardware
                self._log.add(f"  Colector SI falló: {exc}", level="warning")

        # 2) Plan B: dvbv5-scan
        try:
            from ..dvb import tools as dvb_tools

            services = dvb_tools.scan_transponder_services(
                fe.info, transponder, timeout=max(dwell_seconds * 2, 10.0)
            )
            if services:
                self._log.add(f"  dvbv5-scan devolvió {len(services)} servicios")
                return services
        except FrontendError as exc:
            self._log.add(f"  dvbv5-scan no disponible: {exc}", level="warning")
        except Exception as exc:  # pragma: no cover
            self._log.add(f"  dvbv5-scan falló: {exc}", level="warning")

        # 3) Modo simulación
        if getattr(fe, "is_mock", False):
            try:
                return list(fe.mock_services(transponder))  # type: ignore[attr-defined]
            except Exception:  # pragma: no cover
                logger.debug("El mock no ofrece servicios", exc_info=True)
        return []

    def _tune_params(
        self, transponder: Transponder, adapter: int, frontend: int, lock_timeout_s: float
    ) -> TuneParams:
        return TuneParams(
            adapter=adapter,
            frontend=frontend,
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
            lnb_low_khz=self.settings.lnb_low_khz,
            lnb_high_khz=self.settings.lnb_high_khz,
            lnb_threshold_khz=self.settings.lnb_threshold_khz,
            use_lnb=True,
            lock_timeout_s=lock_timeout_s,
        )

    @staticmethod
    def _label(transponder: Transponder) -> str:
        parts = [transponder.delivery_system, f"{transponder.frequency_khz / 1000:g} MHz"]
        if transponder.polarization:
            parts.append(transponder.polarization)
        if transponder.symbol_rate_ksps:
            parts.append(f"{transponder.symbol_rate_ksps} ksps")
        return " ".join(parts)

    @staticmethod
    def _fmt(value: float | None, unit: str) -> str:
        return "n/d" if value is None else f"{value:.1f}{unit}"

    def _update_progress(self, **changes: Any) -> None:
        with self._lock:
            self._progress = self._clone(**changes)

    def _finish(self, state: str, message: str, errors: list[str]) -> None:
        with self._lock:
            self._progress = self._clone(
                state=state,
                message=message,
                errors=list(errors),
                finished_at=time.time(),
                percent=100.0 if state == "done" else self._progress.percent,
            )
        self._log.add(message, level="info" if state == "done" else "warning")

    # ------------------------------------------------------------------
    # Persistencia del job
    # ------------------------------------------------------------------
    def _record_job(
        self, job_id: str, adapter: int, frontend: int, total: int, table: str | None
    ) -> None:
        try:
            with session_scope() as db:
                db.add(
                    ScanJob(
                        job_id=job_id,
                        state="running",
                        adapter=adapter,
                        frontend=frontend,
                        total=total,
                        tables=table,
                    )
                )
        except Exception:  # pragma: no cover
            logger.exception("No se pudo registrar el job de escaneo")

    def _close_job(self) -> None:
        progress = self.progress
        job_id = self.job_id
        if not job_id:
            return
        try:
            with session_scope() as db:
                row = db.query(ScanJob).filter(ScanJob.job_id == job_id).one_or_none()
                if row is not None:
                    row.state = progress.state
                    row.current = progress.current
                    row.services_found = progress.services_found
                    row.transponders_locked = progress.transponders_locked
                    row.message = progress.message
                    row.finished_at = utcnow()
        except Exception:  # pragma: no cover
            logger.exception("No se pudo cerrar el job de escaneo")
