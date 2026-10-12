"""Parser de tablas PSI/SI (PAT, SDT, PMT) y reensamblado de secciones.

API que consume `app/services/si_collector.py`:

* ``SectionAssembler(pid=...)`` con ``feed(payload, payload_unit_start=...)``
  -> `Section` o ``None``.
* ``Section.table_id`` y ``Section.pmt_map``.
* ``pmt_pids_from_pat(sections)``.
* ``build_services(pat_sections, sdt_sections, pmt_sections)`` -> list[ServiceInfo].

Además se ofrece la tubería completa desde bytes TS:
``parse_services_from_ts(data)``, y utilidades para construir paquetes en tests:
``make_ts_packet()`` y ``make_section()``.

Referencias: ISO/IEC 13818-1 (PSI: PAT = tabla 0x00, PMT = 0x02) y
ETSI EN 300 468 (SI: SDT = 0x42/0x46). CRC-32/MPEG-2: polinomio 0x04C11DB7,
inicial 0xFFFFFFFF, sin reflexión y sin XOR final (check de ``b"123456789"`` =
0x0376E6E7).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Iterable, Iterator, Sequence

from .types import ServiceInfo, service_type_label

__all__ = [
    "crc32_mpeg",
    "verify_crc32_mpeg",
    "Section",
    "SdtService",
    "StreamInfo",
    "SectionAssembler",
    "TsPacket",
    "iter_ts_packets",
    "make_ts_packet",
    "make_section",
    "parse_section",
    "parse_pat",
    "parse_sdt",
    "parse_pmt",
    "pmt_pids_from_pat",
    "build_services",
    "parse_services_from_ts",
    "stream_type_codec",
    "stream_kind",
    "PAT_PID",
    "SDT_PID",
    "NIT_PID",
]

TS_PACKET_SIZE = 188
MAX_SECTION_LENGTH = 4093          # 12 bits de section_length: 0x3FD
PAT_PID = 0x0000
CAT_PID = 0x0001
NIT_PID = 0x0010
SDT_PID = 0x0011
EIT_PID = 0x0012

TABLE_ID_PAT = 0x00
TABLE_ID_PMT = 0x02
TABLE_ID_SDT_ACTUAL = 0x42
TABLE_ID_SDT_OTHER = 0x46

# Dónde empieza el cuerpo de cada tabla. PAT/PMT usan la cabecera PSI de 8
# bytes; la SDT añade transport_stream_id(2) + reserved(1) + original_network_id(2)
# + reserved(1) => 11; la NIT añade además network_descriptors_length (12).
_TABLE_PAYLOAD_OFFSET: dict[int, int] = {
    0x00: 8,   # PAT
    0x02: 8,   # PMT
    0x42: 11,  # SDT (TS actual)
    0x46: 11,  # SDT (otro TS)
    0x40: 12,  # NIT (TS actual)
    0x41: 12,  # NIT (otro TS)
}

# ---------------------------------------------------------------------------
# CRC-32/MPEG-2
# ---------------------------------------------------------------------------
_CRC_POLY = 0x04C11DB7
_CRC_TABLE: list[int] = []


def _build_crc_table() -> list[int]:
    table = []
    for index in range(256):
        crc = index << 24
        for _ in range(8):
            crc = ((crc << 1) ^ _CRC_POLY) & 0xFFFFFFFF if crc & 0x80000000 else (crc << 1) & 0xFFFFFFFF
        table.append(crc)
    return table


_CRC_TABLE = _build_crc_table()


def crc32_mpeg(data: bytes) -> int:
    """CRC-32/MPEG-2 de `data` (el que usan las secciones PSI/SI)."""
    crc = 0xFFFFFFFF
    for byte in data:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _CRC_TABLE[((crc >> 24) ^ byte) & 0xFF]
    return crc


def verify_crc32_mpeg(section: bytes) -> bool:
    """True si el CRC de la sección (últimos 4 bytes) es válido."""
    if len(section) < 4:
        return False
    return crc32_mpeg(section) == 0


def _append_crc(payload: bytes) -> bytes:
    return payload + struct.pack(">I", crc32_mpeg(payload))


# ---------------------------------------------------------------------------
# Códecs
# ---------------------------------------------------------------------------
_STREAM_TYPE_CODECS: dict[int, str] = {
    0x01: "MPEG-1",
    0x02: "MPEG-2",
    0x03: "MPEG-1",
    0x04: "MPEG-2",
    0x0F: "AAC",
    0x10: "MPEG-4",
    0x11: "AAC",
    0x1B: "H.264",
    0x24: "HEVC",
    0x42: "AVS",
    0x81: "AC-3",
    0x82: "HDMV",
    0x86: "SCTE-35",
    0x87: "E-AC-3",
    0xEA: "VC-1",
}
_VIDEO_STREAM_TYPES = {0x01, 0x02, 0x10, 0x1B, 0x24, 0x42, 0xEA}
_AUDIO_STREAM_TYPES = {0x03, 0x04, 0x0F, 0x11, 0x81, 0x87}
_AC3_DESCRIPTOR = 0x6A
_EAC3_DESCRIPTOR = 0x7A
_DTS_DESCRIPTOR = 0x7B
_AAC_DESCRIPTOR = 0x7C
_CA_DESCRIPTOR = 0x09
_LANGUAGE_DESCRIPTOR = 0x0A
_SERVICE_DESCRIPTOR = 0x48


def stream_kind(stream_type: int) -> str:
    """"video" | "audio" | "data" según `stream_type`."""
    if stream_type in _VIDEO_STREAM_TYPES:
        return "video"
    if stream_type in _AUDIO_STREAM_TYPES:
        return "audio"
    return "data"


def stream_type_codec(stream_type: int, descriptors: Sequence[tuple[int, bytes]] = ()) -> str | None:
    """Códec legible a partir de `stream_type` y descriptores."""
    for tag, _payload in descriptors or ():
        if tag == _AC3_DESCRIPTOR:
            return "AC-3"
        if tag == _EAC3_DESCRIPTOR:
            return "E-AC-3"
        if tag == _DTS_DESCRIPTOR:
            return "DTS"
        if tag == _AAC_DESCRIPTOR:
            return "AAC"
    return _STREAM_TYPE_CODECS.get(int(stream_type))


# ---------------------------------------------------------------------------
# Secciones
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SdtService:
    """Entrada de la tabla SDT (nombre, proveedor, tipo, acceso condicional)."""

    service_id: int
    name: str
    provider: str
    service_type: int
    free_ca_mode: bool
    scrambled: bool = False
    running_status: int = 0


@dataclass(frozen=True)
class StreamInfo:
    """Flujo elemental de un PMT."""

    pid: int
    stream_type: int
    kind: str
    codec: str | None = None
    ca_system_ids: tuple[int, ...] = ()
    languages: tuple[str, ...] = ()
    descriptors: tuple[tuple[int, bytes], ...] = ()


@dataclass(frozen=True)
class Section:
    """Una sección PSI/SI completa (con CRC verificado o descartado)."""

    pid: int
    table_id: int
    section_length: int
    table_id_extension: int
    version_number: int
    current_next: bool
    section_number: int
    last_section_number: int
    payload: bytes
    raw: bytes
    crc_ok: bool
    # PAT
    programs: tuple[tuple[int, int], ...] = ()
    # SDT
    services: tuple[SdtService, ...] = ()
    # PMT
    program_number: int | None = None
    pcr_pid: int | None = None
    streams: tuple[StreamInfo, ...] = ()
    ca_system_ids: tuple[int, ...] = ()
    is_scrambled: bool = False

    @property
    def pmt_map(self) -> dict[int, int]:
        """``{program_number: pmt_pid}`` de una sección PAT (0 = NIT excluido)."""
        return {number: pid for number, pid in self.programs if number != 0}

    @property
    def is_pat(self) -> bool:
        return self.table_id == TABLE_ID_PAT

    @property
    def is_pmt(self) -> bool:
        return self.table_id == TABLE_ID_PMT

    @property
    def is_sdt(self) -> bool:
        return self.table_id in (TABLE_ID_SDT_ACTUAL, TABLE_ID_SDT_OTHER)


# ---------------------------------------------------------------------------
# Utilidades de bajo nivel
# ---------------------------------------------------------------------------
def _parse_descriptors(data: bytes) -> tuple[tuple[int, bytes], ...]:
    """Trocea un bucle de descriptores en (tag, payload)."""
    result: list[tuple[int, bytes]] = []
    offset = 0
    length = len(data)
    while offset + 2 <= length:
        tag = data[offset]
        size = data[offset + 1]
        offset += 2
        if offset + size > length:
            break
        result.append((tag, data[offset : offset + size]))
        offset += size
    return tuple(result)


def _ca_system_ids(descriptors: Iterable[tuple[int, bytes]]) -> tuple[int, ...]:
    ids: list[int] = []
    for tag, payload in descriptors:
        if tag == _CA_DESCRIPTOR and len(payload) >= 2:
            ca_system_id = struct.unpack_from(">H", payload, 0)[0]
            if ca_system_id not in ids:
                ids.append(ca_system_id)
    return tuple(ids)


def _languages(descriptors: Iterable[tuple[int, bytes]]) -> tuple[str, ...]:
    languages: list[str] = []
    for tag, payload in descriptors:
        if tag == _LANGUAGE_DESCRIPTOR:
            for offset in range(0, len(payload) - 2, 4):
                code = payload[offset : offset + 3].decode("ascii", "replace").strip()
                if code and code not in languages:
                    languages.append(code)
    return tuple(languages)


def parse_pat(section: Section) -> tuple[tuple[int, int], ...]:
    """Entradas (program_number, pid) de una PAT."""
    programs: list[tuple[int, int]] = []
    data = section.payload
    for offset in range(0, len(data) - 3, 4):
        program_number = struct.unpack_from(">H", data, offset)[0]
        pid = struct.unpack_from(">H", data, offset + 2)[0] & 0x1FFF
        programs.append((program_number, pid))
    return tuple(programs)


def parse_sdt(section: Section) -> tuple[SdtService, ...]:
    """Servicios de una sección SDT."""
    services: list[SdtService] = []
    data = section.payload
    offset = 0
    length = len(data)
    while offset + 5 <= length:
        service_id = struct.unpack_from(">H", data, offset)[0]
        # offset+2: reservado(6) | EIT_schedule_flag | EIT_pf_flag
        run_flags = data[offset + 3]
        running_status = (run_flags >> 5) & 0x07
        free_ca_mode = bool(run_flags & 0x10)
        descriptors_length = ((run_flags & 0x0F) << 8) | data[offset + 4]
        offset += 5
        if offset + descriptors_length > length:
            break
        descriptors = _parse_descriptors(data[offset : offset + descriptors_length])
        offset += descriptors_length

        name = ""
        provider = ""
        service_type = 0x01
        for tag, payload in descriptors:
            if tag == _SERVICE_DESCRIPTOR and len(payload) >= 2:
                service_type = payload[0]
                provider_length = payload[1]
                start = 2
                provider = _decode_dvb_string(payload[start : start + provider_length])
                start += provider_length
                if start < len(payload):
                    service_length = payload[start]
                    name = _decode_dvb_string(payload[start + 1 : start + 1 + service_length])
                break
        services.append(
            SdtService(
                service_id=service_id,
                name=name,
                provider=provider,
                service_type=service_type,
                free_ca_mode=free_ca_mode,
                scrambled=False,  # el cifrado real se decide con el PMT (CA_descriptor)
                running_status=running_status,
            )
        )
    return tuple(services)


def parse_pmt(section: Section) -> dict[str, object]:
    """PMT: program_number, pcr_pid, streams, CA y si está cifrado."""
    data = section.payload
    if len(data) < 4:
        return {"program_number": section.table_id_extension, "pcr_pid": None, "streams": (), "ca_system_ids": (), "is_scrambled": False}
    pcr_pid = struct.unpack_from(">H", data, 0)[0] & 0x1FFF
    program_info_length = struct.unpack_from(">H", data, 2)[0] & 0x0FFF
    offset = 4
    program_descriptors = _parse_descriptors(data[offset : offset + program_info_length])
    offset += program_info_length
    ca_ids = list(_ca_system_ids(program_descriptors))

    streams: list[StreamInfo] = []
    length = len(data)
    while offset + 5 <= length:
        stream_type = data[offset]
        pid = struct.unpack_from(">H", data, offset + 1)[0] & 0x1FFF
        es_info_length = struct.unpack_from(">H", data, offset + 3)[0] & 0x0FFF
        offset += 5
        if offset + es_info_length > length:
            break
        descriptors = _parse_descriptors(data[offset : offset + es_info_length])
        offset += es_info_length
        stream_ca = _ca_system_ids(descriptors)
        for ca_id in stream_ca:
            if ca_id not in ca_ids:
                ca_ids.append(ca_id)
        streams.append(
            StreamInfo(
                pid=pid,
                stream_type=stream_type,
                kind=stream_kind(stream_type),
                codec=stream_type_codec(stream_type, descriptors),
                ca_system_ids=stream_ca,
                languages=_languages(descriptors),
                descriptors=descriptors,
            )
        )
    return {
        "program_number": section.table_id_extension,
        "pcr_pid": pcr_pid,
        "streams": tuple(streams),
        "ca_system_ids": tuple(ca_ids),
        "is_scrambled": bool(ca_ids),
    }


def _decode_dvb_string(data: bytes) -> str:
    """Texto de la DVB (tabla A.4): UTF-8 con fallback latin-1."""
    if not data:
        return ""
    if data[0] < 0x20:  # selector de juego de caracteres
        data = data[1:]
    for encoding in ("utf-8", "latin-1"):
        try:
            return data.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", "replace").strip()


def parse_section(pid: int, raw: bytes) -> Section | None:
    """Convierte bytes de una sección completa en `Section`."""
    if len(raw) < 12:
        return None
    table_id = raw[0]
    section_length = struct.unpack_from(">H", raw, 1)[0] & 0x0FFF
    if section_length + 3 > len(raw):
        return None
    body = raw[: section_length + 3]
    table_id_extension = struct.unpack_from(">H", body, 3)[0]
    version = (body[5] >> 1) & 0x1F
    current_next = bool(body[5] & 0x01)
    section_number = body[6]
    last_section_number = body[7]
    payload_offset = _TABLE_PAYLOAD_OFFSET.get(table_id, 8)
    if payload_offset > len(body) - 4:
        return None
    payload = body[payload_offset : len(body) - 4]
    crc_ok = verify_crc32_mpeg(body)

    section = Section(
        pid=int(pid),
        table_id=table_id,
        section_length=section_length,
        table_id_extension=table_id_extension,
        version_number=version,
        current_next=current_next,
        section_number=section_number,
        last_section_number=last_section_number,
        payload=payload,
        raw=body,
        crc_ok=crc_ok,
    )
    if table_id == TABLE_ID_PAT:
        section = _replace(section, programs=parse_pat(section))
    elif table_id in (TABLE_ID_SDT_ACTUAL, TABLE_ID_SDT_OTHER):
        section = _replace(section, services=parse_sdt(section))
    elif table_id == TABLE_ID_PMT:
        info = parse_pmt(section)
        section = _replace(
            section,
            program_number=int(info["program_number"]),  # type: ignore[arg-type]
            pcr_pid=info["pcr_pid"],  # type: ignore[arg-type]
            streams=info["streams"],  # type: ignore[arg-type]
            ca_system_ids=info["ca_system_ids"],  # type: ignore[arg-type]
            is_scrambled=bool(info["is_scrambled"]),
        )
    return section


def _replace(section: Section, **changes: object) -> Section:
    """`dataclasses.replace` sin reexportar el módulo entero."""
    from dataclasses import replace as _dc_replace

    return _dc_replace(section, **changes)


# ---------------------------------------------------------------------------
# Reensamblado de secciones
# ---------------------------------------------------------------------------
class SectionAssembler:
    """Reensambla secciones de un mismo PID a partir de payloads de paquete TS.

    Uso (igual que en `app/services/si_collector.py`)::

        assembler = SectionAssembler(pid=0x11)
        section = assembler.feed(packet.payload, payload_unit_start=...)

    `feed()` devuelve la primera sección completa disponible. Si un mismo
    payload contiene varias secciones, las demás quedan en cola y se devuelven
    en llamadas posteriores a `feed()` (o todas con `feed_all()`/`drain()`).
    """

    def __init__(self, pid: int | None = None, *, validate_crc: bool = True) -> None:
        self.pid = pid
        self.validate_crc = validate_crc
        self._buffer = bytearray()
        self._expected: int | None = None
        self._continuity: int | None = None
        self._queue: list[Section] = []
        self.sections_seen = 0
        self.crc_errors = 0
        self.discontinuities = 0

    # -- API ---------------------------------------------------------------
    def feed(self, payload: bytes, payload_unit_start: bool = False) -> Section | None:
        """Añade el payload de un paquete TS y devuelve una sección completa."""
        if self._queue:
            return self._queue.pop(0)
        self._feed_bytes(bytes(payload or b""), bool(payload_unit_start))
        if self._queue:
            return self._queue.pop(0)
        return None

    def feed_all(self, payload: bytes, payload_unit_start: bool = False) -> list[Section]:
        """Como `feed()` pero devolviendo todas las secciones completadas."""
        self._feed_bytes(bytes(payload or b""), bool(payload_unit_start))
        sections = list(self._queue)
        self._queue.clear()
        return sections

    def drain(self) -> list[Section]:
        """Secciones en cola pendientes de devolver."""
        sections = list(self._queue)
        self._queue.clear()
        return sections

    def reset(self) -> None:
        """Descarta el estado de reensamblado."""
        self._buffer.clear()
        self._expected = None
        self._continuity = None
        self._queue.clear()

    def check_continuity(self, continuity_counter: int) -> bool:
        """Detecta saltos del contador de continuidad (True si hay salto)."""
        previous, self._continuity = self._continuity, int(continuity_counter) & 0x0F
        if previous is None:
            return False
        if (previous + 1) & 0x0F == self._continuity:
            return False
        # Repetición del mismo contador (paquete duplicado) no es discontinuidad
        if previous == self._continuity:
            return False
        self.discontinuities += 1
        self._buffer.clear()
        self._expected = None
        return True

    # -- internos ----------------------------------------------------------
    def _feed_bytes(self, payload: bytes, payload_unit_start: bool) -> None:
        if not payload:
            return
        if payload[0] == 0xFF and not self._buffer:
            return  # relleno
        if payload_unit_start:
            pointer = payload[0]
            offset = 1
            if pointer:
                if self._expected is not None:
                    # Los primeros `pointer` bytes cierran la sección anterior.
                    self._consume(payload[offset : offset + pointer])
                offset += pointer
            # Cualquier resto incompleto se descarta: empieza una sección nueva.
            self._buffer.clear()
            self._expected = None
            self._consume(payload[offset:])
        else:
            if self._expected is None and not self._buffer:
                return  # no hay sección en curso
            self._consume(payload)

    def _consume(self, data: bytes) -> None:
        offset = 0
        length = len(data)
        while offset < length:
            if self._expected is None:
                # Saltar relleno y leer la cabecera mínima de 3 bytes.
                while offset < length and not self._buffer and data[offset] == 0xFF:
                    offset += 1
                needed = 3 - len(self._buffer)
                take = min(needed, length - offset)
                if take > 0:
                    self._buffer.extend(data[offset : offset + take])
                    offset += take
                if len(self._buffer) < 3:
                    return
                section_length = struct.unpack_from(">H", self._buffer, 1)[0] & 0x0FFF
                self._expected = section_length + 3
                if self._expected < 12 or self._expected > MAX_SECTION_LENGTH + 3:
                    # Longitud imposible: resincronizar descartando el byte.
                    self.crc_errors += 1
                    self._buffer.clear()
                    self._expected = None
                    continue
            remaining = self._expected - len(self._buffer)
            take = min(remaining, length - offset)
            if take > 0:
                self._buffer.extend(data[offset : offset + take])
                offset += take
            if len(self._buffer) >= self._expected:
                self._finish_section()
            else:
                return

    def _finish_section(self) -> None:
        raw = bytes(self._buffer[: self._expected])
        self._buffer.clear()
        self._expected = None
        self.sections_seen += 1
        if self.validate_crc and not verify_crc32_mpeg(raw):
            self.crc_errors += 1
            return
        section = parse_section(self.pid if self.pid is not None else -1, raw)
        if section is not None:
            self._queue.append(section)


# ---------------------------------------------------------------------------
# Paquetes TS
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TsPacket:
    """Paquete de transporte despiezado."""

    pid: int
    payload_unit_start: bool
    payload: bytes
    continuity_counter: int
    has_payload: bool
    transport_error: bool = False
    scrambling: int = 0


def iter_ts_packets(data: bytes) -> Iterator[TsPacket]:
    """Itera paquetes TS de 188 bytes de un buffer (resincroniza si hace falta)."""
    offset = 0
    length = len(data)
    while offset + TS_PACKET_SIZE <= length:
        if data[offset] != 0x47:
            offset += 1
            continue
        packet = data[offset : offset + TS_PACKET_SIZE]
        offset += TS_PACKET_SIZE
        yield decode_ts_packet(packet)


def decode_ts_packet(packet: bytes) -> TsPacket:
    """Despieza un paquete TS de 188 bytes."""
    if len(packet) != TS_PACKET_SIZE:
        raise ValueError(f"El paquete TS debe tener 188 bytes, tiene {len(packet)}")
    transport_error = bool(packet[1] & 0x80)
    payload_unit_start = bool(packet[1] & 0x40)
    pid = ((packet[1] & 0x1F) << 8) | packet[2]
    scrambling = (packet[3] >> 6) & 0x03
    adaptation_control = (packet[3] >> 4) & 0x03
    continuity_counter = packet[3] & 0x0F
    has_payload = adaptation_control in (1, 3)
    index = 4
    if adaptation_control in (2, 3):
        if index >= TS_PACKET_SIZE:
            return TsPacket(pid, payload_unit_start, b"", continuity_counter, False, transport_error, scrambling)
        index += 1 + packet[4]
    if index >= TS_PACKET_SIZE or not has_payload:
        return TsPacket(pid, payload_unit_start, b"", continuity_counter, False, transport_error, scrambling)
    return TsPacket(
        pid,
        payload_unit_start,
        packet[index:],
        continuity_counter,
        True,
        transport_error,
        scrambling,
    )


def make_ts_packet(
    pid: int,
    payload: bytes = b"",
    *,
    payload_unit_start: bool = False,
    continuity_counter: int = 0,
    transport_error: bool = False,
    scrambling: int = 0,
    stuff: int = 0xFF,
) -> bytes:
    """Construye un paquete TS de 188 bytes (útil en tests)."""
    body = bytes(payload or b"")
    if len(body) > 184:
        raise ValueError("El payload de un paquete TS no puede superar 184 bytes")
    con_adaptacion = len(body) < 184
    # adaptation_field_control: 0b01 = sólo payload, 0b11 = adaptación + payload.
    adaptation_control = 0x30 if con_adaptacion else 0x10
    header = bytes(
        [
            0x47,
            (0x80 if transport_error else 0x00)
            | (0x40 if payload_unit_start else 0x00)
            | ((int(pid) >> 8) & 0x1F),
            int(pid) & 0xFF,
            ((int(scrambling) & 0x03) << 6) | adaptation_control | (int(continuity_counter) & 0x0F),
        ]
    )
    if not con_adaptacion:
        return header + body
    # adaptation_field para rellenar hasta 188: 4 (cabecera) + 1 (longitud) +
    # adaptation_field_length bytes + payload = 188.
    adaptation_length = 183 - len(body)
    return header + bytes([adaptation_length]) + bytes([stuff]) * adaptation_length + body


def make_section(
    table_id: int,
    table_id_extension: int,
    payload: bytes,
    *,
    version: int = 0,
    section_number: int = 0,
    last_section_number: int = 0,
    current_next: bool = True,
    with_crc: bool = True,
) -> bytes:
    """Construye una sección PSI/SI completa (con CRC correcto por defecto)."""
    body = bytes(payload)
    section_length = 5 + len(body) + (4 if with_crc else 0)
    header = struct.pack(">BHH", int(table_id), 0xB000 | (section_length & 0x0FFF), int(table_id_extension))
    tail = bytes(
        [
            (0xC0 | ((int(version) & 0x1F) << 1) | (0x01 if current_next else 0x00)),
            int(section_number) & 0xFF,
            int(last_section_number) & 0xFF,
        ]
    )
    section = header + tail + body
    return _append_crc(section) if with_crc else section


# ---------------------------------------------------------------------------
# Combinación de tablas
# ---------------------------------------------------------------------------
def pmt_pids_from_pat(pat_sections: Iterable[Section]) -> list[int]:
    """PIDs de PMT referenciados por las secciones PAT."""
    pids: list[int] = []
    for section in pat_sections or ():
        for program_number, pid in getattr(section, "programs", ()) or ():
            if program_number != 0 and pid and pid not in pids:
                pids.append(pid)
    return pids


def build_services(
    pat_sections: Iterable[Section],
    sdt_sections: Iterable[Section],
    pmt_sections: dict[int, list[Section]] | None = None,
) -> list[ServiceInfo]:
    """Combina PAT + SDT + PMT en `ServiceInfo` (uno por programa)."""
    programs: dict[int, int] = {}
    for section in pat_sections or ():
        for program_number, pid in getattr(section, "programs", ()) or ():
            if program_number != 0:
                programs[program_number] = pid

    sdt_by_id: dict[int, SdtService] = {}
    for section in sdt_sections or ():
        for service in getattr(section, "services", ()) or ():
            if service.service_id not in sdt_by_id or service.name:
                sdt_by_id[service.service_id] = service

    pmt_by_program: dict[int, Section] = {}
    for sections in (pmt_sections or {}).values():
        for section in sections or ():
            if getattr(section, "table_id", None) == TABLE_ID_PMT:
                number = section.program_number if section.program_number is not None else section.table_id_extension
                pmt_by_program[int(number)] = section

    # Si no hay PAT utilizable, se usan los programas de los PMT.
    if not programs:
        for number, section in pmt_by_program.items():
            programs[number] = section.pid

    services: list[ServiceInfo] = []
    for service_id in sorted(programs):
        pmt = pmt_by_program.get(service_id)
        sdt = sdt_by_id.get(service_id)
        streams = getattr(pmt, "streams", ()) or ()
        video_streams = [s for s in streams if s.kind == "video"]
        audio_streams = [s for s in streams if s.kind == "audio"]
        ca_ids: list[int] = list(getattr(pmt, "ca_system_ids", ()) or ())
        # El SDT marca free_CA_mode cuando al menos un flujo va cifrado; el
        # CA_descriptor del PMT es la prueba directa.
        scrambled = bool(ca_ids) or bool(getattr(pmt, "is_scrambled", False)) or bool(
            sdt is not None and sdt.free_ca_mode
        )

        service_type = sdt.service_type if sdt is not None else _guess_service_type(video_streams, audio_streams)
        service_type = _refine_service_type(service_type, video_streams)
        name = (sdt.name if sdt is not None else "") or f"Servicio {service_id}"
        provider = sdt.provider if sdt is not None else ""

        services.append(
            ServiceInfo(
                service_id=int(service_id),
                name=name,
                provider=provider,
                service_type=int(service_type),
                service_type_label=service_type_label(int(service_type)),
                is_fta=not scrambled,
                scrambled=scrambled,
                video_pid=video_streams[0].pid if video_streams else None,
                audio_pid=audio_streams[0].pid if audio_streams else None,
                pcr_pid=getattr(pmt, "pcr_pid", None),
                video_codec=video_streams[0].codec if video_streams else None,
                audio_codec=audio_streams[0].codec if audio_streams else None,
                pmt_pid=programs.get(service_id),
                ca_system_ids=tuple(ca_ids),
                audio_pids=tuple(s.pid for s in audio_streams),
                video_pids=tuple(s.pid for s in video_streams),
            )
        )
    return services


def _guess_service_type(video_streams: Sequence[StreamInfo], audio_streams: Sequence[StreamInfo]) -> int:
    if video_streams:
        return 0x01
    if audio_streams:
        return 0x02
    return 0x0C


def _refine_service_type(service_type: int, video_streams: Sequence[StreamInfo]) -> int:
    """Promociona a HD (0x19) si el vídeo es H.264/HEVC y el tipo era TV/desconocido."""
    if not video_streams:
        return service_type
    codec = (video_streams[0].codec or "").upper()
    if service_type in (0x01, 0x16, 0x1F) and codec in ("H.264", "HEVC", "MPEG-4"):
        return 0x19
    return service_type


# ---------------------------------------------------------------------------
# Tubería completa desde bytes TS
# ---------------------------------------------------------------------------
def parse_services_from_ts(
    data: bytes,
    *,
    extra_pmt_pids: Iterable[int] = (),
    validate_crc: bool = False,
) -> list[ServiceInfo]:
    """Lee PAT/SDT/PMT de un buffer TS crudo y devuelve los servicios.

    Los PMT se descubren con la PAT, así que basta con que el buffer contenga
    PAT, SDT y los PMT. `extra_pmt_pids` permite forzar PIDs de PMT conocidos.
    """
    pat_sections: list[Section] = []
    sdt_sections: list[Section] = []
    pmt_sections: dict[int, list[Section]] = {}
    assemblers: dict[int, SectionAssembler] = {}
    known_pmt: set[int] = set(int(pid) for pid in extra_pmt_pids)

    def assembler_for(pid: int) -> SectionAssembler:
        if pid not in assemblers:
            assemblers[pid] = SectionAssembler(pid=pid, validate_crc=validate_crc)
        return assemblers[pid]

    for packet in iter_ts_packets(data):
        if packet.transport_error or not packet.has_payload:
            continue
        if packet.pid not in (PAT_PID, SDT_PID) and packet.pid not in known_pmt:
            continue
        assembler = assembler_for(packet.pid)
        assembler.check_continuity(packet.continuity_counter)
        for section in assembler.feed_all(packet.payload, packet.payload_unit_start):
            if packet.pid == PAT_PID:
                pat_sections.append(section)
                known_pmt.update(section.pmt_map.values())
            elif packet.pid == SDT_PID:
                sdt_sections.append(section)
            else:
                pmt_sections.setdefault(packet.pid, []).append(section)
    return build_services(pat_sections, sdt_sections, pmt_sections)
