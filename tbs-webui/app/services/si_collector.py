"""Colector de tablas SI (PAT/SDT/PMT) desde el demux DVB.

Flujo habitual de un escaneo real:

1. Tras sintonizar se usa el demux del adaptador (`/dev/dvb/adapterN/demux0`) y
   se piden los PIDs de PSI: 0x00 (PAT) y 0x11 (SDT).
2. Se leen paquetes TS (188 B) durante `dwell_seconds`.
3. Con la PAT se descubren los PMT de cada servicio; se amplía el filtro del
   demux con esos PIDs y se sigue leyendo.
4. Cada PID tiene su propio `si.SectionAssembler`, que reensambla secciones
   usando el bit *payload_unit_start_indicator* y la continuación de secciones.
5. `si.build_services()` combina PAT+SDT+PMT en `ServiceInfo`.

Contrato mínimo que debe ofrecer el frontend (ver INTERFACES.md §3, extensión
`TSReader`). Si no lo ofrece, el colector devuelve `[]` y el escaneo usa el
plan B (`dvbv5-scan`) o el proveedor simulado.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Iterable, Iterator

from ..dvb.types import ServiceInfo

if TYPE_CHECKING:  # pragma: no cover - sólo para tipado
    from ..dvb import si as si_module

logger = logging.getLogger(__name__)


def _load_si():
    """Importa `app.dvb.si` de forma perezosa (puede no existir todavía)."""
    try:
        from ..dvb import si as si_module

        return si_module
    except Exception as exc:  # pragma: no cover - módulo ausente
        logger.debug("app.dvb.si no disponible: %s", exc)
        return None


def si_available() -> bool:
    return _load_si() is not None

PAT_PID = 0x0000
SDT_PID = 0x0011
NIT_PID = 0x0010
TS_PACKET_SIZE = 188


@dataclass(frozen=True)
class TsPacket:
    """Paquete de transporte ya despiezado."""

    pid: int
    payload_unit_start: bool
    payload: bytes
    continuity_counter: int
    has_payload: bool


def frontend_supports_demux(frontend: Any) -> bool:
    """True si el frontend ofrece la API mínima de lectura de TS."""
    return callable(getattr(frontend, "read_ts", None)) and callable(
        getattr(frontend, "set_pid_list", None)
    )


def iter_ts_packets(buffer: bytes) -> Iterator[TsPacket]:
    """Itera paquetes TS de 188 bytes de un buffer (resincroniza si hace falta)."""
    offset = 0
    length = len(buffer)
    while offset + TS_PACKET_SIZE <= length:
        if buffer[offset] != 0x47:
            offset += 1
            continue
        packet = buffer[offset : offset + TS_PACKET_SIZE]
        offset += TS_PACKET_SIZE

        pid = ((packet[1] & 0x1F) << 8) | packet[2]
        payload_unit_start = bool(packet[1] & 0x40)
        adaptation_control = (packet[3] >> 4) & 0x03
        continuity_counter = packet[3] & 0x0F

        has_payload = adaptation_control in (1, 3)
        index = 4
        if adaptation_control in (2, 3):
            if index >= TS_PACKET_SIZE:
                continue
            index += 1 + packet[4]
        if index >= TS_PACKET_SIZE or not has_payload:
            yield TsPacket(pid, payload_unit_start, b"", continuity_counter, False)
            continue
        yield TsPacket(pid, payload_unit_start, packet[index:], continuity_counter, True)


def collect_services(
    frontend: Any,
    dwell_seconds: float = 8.0,
    max_services: int = 400,
) -> list[ServiceInfo]:
    """Lee PSI durante `dwell_seconds` y devuelve los servicios encontrados."""
    if not frontend_supports_demux(frontend):
        logger.debug("El frontend no expone lectura de TS: no se pueden leer tablas SI")
        return []

    si = _load_si()
    if si is None:
        logger.debug("Sin parser SI disponible: no se pueden interpretar las tablas")
        return []

    assemblers: dict[int, Any] = {}
    pat_sections: list[Any] = []
    sdt_sections: list[Any] = []
    pmt_sections: dict[int, list[Any]] = {}
    pmt_pids: set[int] = set()
    expanded = False

    def assembler_for(pid: int):
        if pid not in assemblers:
            assemblers[pid] = si.SectionAssembler(pid=pid)
        return assemblers[pid]

    def discover_pmt_pids() -> set[int]:
        """PIDs de PMT presentes en las secciones PAT recibidas."""
        fn = getattr(si, "pmt_pids_from_pat", None)
        if callable(fn):
            return set(fn(pat_sections))
        # Plan B: extraer los PMT directamente de las secciones PAT crudas.
        found: set[int] = set()
        for section in pat_sections:
            pmt_map = getattr(section, "pmt_map", None)
            if isinstance(pmt_map, dict):
                found.update(int(pid) for pid in pmt_map.values() if pid)
        return found

    def build() -> list[ServiceInfo]:
        fn = getattr(si, "build_services", None)
        if callable(fn):
            return list(fn(pat_sections, sdt_sections, pmt_sections))
        logger.warning("app.dvb.si no expone build_services(); no se pueden combinar las tablas")
        return []

    try:
        frontend.set_pid_list([PAT_PID, SDT_PID, NIT_PID])
    except Exception as exc:  # pragma: no cover - depende del driver
        logger.warning("No se pudo configurar el filtro del demux: %s", exc)
        return []

    start = time.monotonic()
    deadline = start + max(1.0, dwell_seconds)
    expand_at = start + min(max(1.0, dwell_seconds * 0.4), 4.0)

    while time.monotonic() < deadline:
        if not expanded and time.monotonic() >= expand_at:
            expanded = True
            discovered = discover_pmt_pids()
            if discovered - pmt_pids:
                pmt_pids |= discovered
                try:
                    frontend.set_pid_list(sorted({PAT_PID, SDT_PID, NIT_PID} | pmt_pids))
                    logger.debug("Filtro del demux ampliado con %d PMT", len(pmt_pids))
                except Exception as exc:  # pragma: no cover
                    logger.debug("No se pudo ampliar el filtro del demux: %s", exc)

        packets = _read_packets(frontend)
        if not packets:
            time.sleep(0.05)
            continue

        for packet in packets:
            if not packet.has_payload:
                continue
            if packet.pid not in (PAT_PID, SDT_PID, NIT_PID) and packet.pid not in pmt_pids:
                continue
            assembler = assembler_for(packet.pid)
            gather = getattr(assembler, "feed_all", None)
            if callable(gather):
                secciones = list(gather(packet.payload, payload_unit_start=packet.payload_unit_start))
            else:
                unica = assembler.feed(packet.payload, payload_unit_start=packet.payload_unit_start)
                secciones = [unica] if unica is not None else []
            for section in secciones:
                if packet.pid == PAT_PID:
                    pat_sections.append(section)
                    pmt_map = getattr(section, "pmt_map", None)
                    if isinstance(pmt_map, dict):
                        pmt_pids.update(int(valor) for valor in pmt_map.values() if valor)
                elif packet.pid == SDT_PID:
                    sdt_sections.append(section)
                elif packet.pid in pmt_pids or getattr(section, "table_id", None) == 0x02:
                    pmt_sections.setdefault(packet.pid, []).append(section)

            if len(pmt_sections) >= max_services:
                deadline = time.monotonic()  # fin anticipado
                break

    services = build()
    logger.info(
        "Colector SI: %d servicios (PAT=%d SDT=%d PMT=%d)",
        len(services),
        len(pat_sections),
        len(sdt_sections),
        len(pmt_sections),
    )
    return services


def _read_packets(frontend: Any) -> list[TsPacket]:
    """Lee bytes del frontend y los despieza en paquetes TS."""
    try:
        buffer = frontend.read_ts(timeout=0.2, max_bytes=TS_PACKET_SIZE * 64)
    except Exception as exc:  # pragma: no cover - depende del driver
        logger.debug("read_ts falló: %s", exc)
        return []
    if not buffer:
        return []
    return list(iter_ts_packets(buffer))


def sections_to_services(
    pat_sections: Iterable[Any],
    sdt_sections: Iterable[Any],
    pmt_sections: dict[int, list[Any]],
) -> list[ServiceInfo]:
    """Atajo para tests: combina secciones ya parseadas."""
    si = _load_si()
    if si is None:
        return []
    fn = getattr(si, "build_services", None)
    if not callable(fn):
        return []
    return list(fn(list(pat_sections), list(sdt_sections), pmt_sections))


def extract_from_transport_stream(
    data: bytes,
    max_services: int = 400,
    *,
    log: Callable[[str], None] | None = None,
) -> list[ServiceInfo]:
    """Extrae los servicios de un volcado de transporte (TS) completo.

    Es la via que funciona en la TBS 6904se: `dvbv5-zap -P -r` deja un fichero
    con la PAT, la SDT y todos los PMT, y aqui se reensamblan las secciones y se
    combinan.

    Detalle importante: se usa `feed_all()`, no `feed()`. Un mismo payload de
    paquete TS puede contener varias secciones y `feed()` devuelve solo la
    primera; por eso antes se perdian casi todos los PMT y el escaneo acababa
    con 0 servicios.
    """
    si = _load_si()
    if si is None:
        if log:
            log("Sin analizador SI disponible")
        return []

    assemblers: dict[int, Any] = {}
    pat_sections: list[Any] = []
    sdt_sections: list[Any] = []
    pmt_sections: dict[int, list[Any]] = {}
    pmt_pids: set[int] = set()

    def feed(pid: int, payload: bytes, start: bool) -> list[Any]:
        if pid not in assemblers:
            assemblers[pid] = si.SectionAssembler(pid=pid)
        assembler = assemblers[pid]
        gather = getattr(assembler, "feed_all", None)
        if callable(gather):
            return list(gather(payload, payload_unit_start=start))
        single = assembler.feed(payload, payload_unit_start=start)
        return [single] if single is not None else []

    for packet in iter_ts_packets(data):
        if not getattr(packet, "has_payload", True):
            continue
        pid = packet.pid
        if pid not in (PAT_PID, SDT_PID, NIT_PID) and pid not in pmt_pids:
            continue
        try:
            sections = feed(pid, packet.payload, bool(packet.payload_unit_start))
        except Exception:  # pragma: no cover - depende del parser
            continue
        for section in sections:
            table_id = getattr(section, "table_id", None)
            if pid == PAT_PID:
                pat_sections.append(section)
                pmt_map = getattr(section, "pmt_map", None)
                if isinstance(pmt_map, dict):
                    pmt_pids.update(int(value) for value in pmt_map.values() if value)
            elif pid == SDT_PID:
                sdt_sections.append(section)
            elif pid in pmt_pids or table_id == 0x02:
                pmt_sections.setdefault(pid, []).append(section)

    services = sections_to_services(pat_sections, sdt_sections, pmt_sections)
    if log:
        log(
            f"Tablas: PAT={len(pat_sections)} SDT={len(sdt_sections)} "
            f"PMT={len(pmt_sections)} -> {len(services)} servicios"
        )
    logger.info(
        "Analisis del TS: %d servicios (PAT=%d SDT=%d PMT=%d)",
        len(services),
        len(pat_sections),
        len(sdt_sections),
        len(pmt_sections),
    )
    return services[:max_services] if max_services else services


def services_from_transport_stream(data: bytes, max_services: int = 400) -> list[ServiceInfo]:
    """Alias publico que usa `ts_capture`."""
    return extract_from_transport_stream(data, max_services=max_services)


def services_from_channels_conf(text: str, max_services: int = 400) -> list[ServiceInfo]:
    """Extrae servicios de un `channels.conf` de dvbv5 (plan de respaldo)."""
    services: list[ServiceInfo] = []
    actual: dict[str, str] = {}

    def volcar() -> None:
        if not actual:
            return
        nombre = actual.get("name") or ""
        sid = actual.get("service_id") or actual.get("sid")
        vpid = actual.get("video_pid") or actual.get("vpid")
        apid = actual.get("audio_pid") or actual.get("apid")
        if not nombre or not sid or not sid.isdigit():
            actual.clear()
            return
        try:
            services.append(
                ServiceInfo(
                    service_id=int(sid),
                    name=nombre,
                    provider=actual.get("provider", ""),
                    service_type=int(actual.get("service_type", 1) or 1),
                    video_pid=int(vpid) if vpid and vpid.isdigit() else None,
                    audio_pid=int(apid) if apid and apid.isdigit() else None,
                    is_fta=actual.get("scrambled", "0") in ("0", "false", "False"),
                )
            )
        except (TypeError, ValueError):
            pass
        actual.clear()

    for linea in text.splitlines():
        limpia = linea.strip()
        if not limpia:
            continue
        if limpia.startswith("[") and limpia.endswith("]"):
            volcar()
            continue
        if "=" in limpia:
            clave, _, valor = limpia.partition("=")
            actual[clave.strip().lower()] = valor.strip()
    volcar()
    return services[:max_services] if max_services else services
