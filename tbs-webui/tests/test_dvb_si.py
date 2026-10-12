"""Tests de `app.dvb.si` (PAT/SDT/PMT, CRC-32 MPEG, reensamblado de secciones).

Todo se construye con paquetes TS sintéticos: se ejecuta en Windows sin hardware.
"""

from __future__ import annotations

import pathlib
import struct
import sys

_WEBUI = pathlib.Path(__file__).resolve().parents[1]
if str(_WEBUI) not in sys.path:
    sys.path.insert(0, str(_WEBUI))

import pytest  # noqa: E402

from app.dvb import si  # noqa: E402


# ---------------------------------------------------------------------------
# Constructores de prueba
# ---------------------------------------------------------------------------
def _service_descriptor(service_type: int, provider: str, name: str) -> bytes:
    cuerpo = (
        bytes([service_type, len(provider)])
        + provider.encode("utf-8")
        + bytes([len(name)])
        + name.encode("utf-8")
    )
    return bytes([si._SERVICE_DESCRIPTOR, len(cuerpo)]) + cuerpo


def _pat_section(programs: list[tuple[int, int]], tsid: int = 1) -> bytes:
    payload = b"".join(struct.pack(">HH", number, 0xE000 | pid) for number, pid in programs)
    return si.make_section(si.TABLE_ID_PAT, tsid, payload)


def _sdt_section(entries: list[tuple[int, int, str, str, bool]], tsid: int = 1, onid: int = 1) -> bytes:
    payload = struct.pack(">H", tsid) + b"\x00"  # transport_stream_id + reservado
    for service_id, service_type, provider, name, free_ca in entries:
        descriptor = _service_descriptor(service_type, provider, name)
        run_flags = bytes(
            [((4 << 5) if not free_ca else (4 << 5)) | (0x10 if free_ca else 0x00), len(descriptor)]
        )
        payload += struct.pack(">H", service_id) + b"\x00" + run_flags + descriptor
    return si.make_section(si.TABLE_ID_SDT_ACTUAL, onid, payload)


def _pmt_section(
    service_id: int,
    pcr_pid: int,
    streams: list[tuple[int, int, bytes]],
    program_descriptors: bytes = b"",
) -> bytes:
    payload = struct.pack(">H", 0xE000 | pcr_pid)
    payload += struct.pack(">H", 0xF000 | len(program_descriptors)) + program_descriptors
    for stream_type, pid, descriptors in streams:
        payload += bytes([stream_type])
        payload += struct.pack(">H", 0xE000 | pid)
        payload += struct.pack(">H", 0xF000 | len(descriptors)) + descriptors
    return si.make_section(si.TABLE_ID_PMT, service_id, payload)


def _ca_descriptor(ca_system_id: int = 0x0B00, ca_pid: int = 0x0050) -> bytes:
    return bytes([si._CA_DESCRIPTOR, 0x04]) + struct.pack(">HH", ca_system_id, 0xE000 | ca_pid)


def _ts(sections: list[tuple[int, bytes]]) -> bytes:
    """Convierte [(pid, sección)] en un TS con un paquete por sección."""
    packets = []
    for index, (pid, section) in enumerate(sections):
        packets.append(
            si.make_ts_packet(
                pid,
                b"\x00" + section,
                payload_unit_start=True,
                continuity_counter=index & 0x0F,
            )
        )
    return b"".join(packets)


# ---------------------------------------------------------------------------
# CRC-32/MPEG-2
# ---------------------------------------------------------------------------
def test_crc32_mpeg_vector_conocido():
    """CRC-32/MPEG-2 de "123456789" = 0x0376E6E7 (check estándar)."""
    assert si.crc32_mpeg(b"123456789") == 0x0376E6E7
    assert si.crc32_mpeg(b"") == 0xFFFFFFFF


def test_make_section_lleva_crc_valido():
    section = si.make_section(si.TABLE_ID_PAT, 1, struct.pack(">HH", 1, 0xE000 | 0x100))
    assert si.verify_crc32_mpeg(section) is True
    assert si.verify_crc32_mpeg(section[:-1] + bytes([section[-1] ^ 0xFF])) is False
    assert si.verify_crc32_mpeg(b"\x00\x01") is False


def test_make_section_longitud():
    section = si.make_section(si.TABLE_ID_PAT, 1, b"\x00" * 4)
    section_length = struct.unpack_from(">H", section, 1)[0] & 0x0FFF
    assert section_length == len(section) - 3
    assert section[0] == si.TABLE_ID_PAT


# ---------------------------------------------------------------------------
# Paquetes TS
# ---------------------------------------------------------------------------
def test_make_ts_packet_y_decode():
    packet = si.make_ts_packet(0x11, b"hola", payload_unit_start=True, continuity_counter=3)
    assert len(packet) == 188
    assert packet[0] == 0x47
    decoded = si.decode_ts_packet(packet)
    assert decoded.pid == 0x11
    assert decoded.payload_unit_start is True
    assert decoded.continuity_counter == 3
    assert decoded.has_payload is True
    assert decoded.payload == b"hola"
    assert decoded.transport_error is False


def test_decode_ts_packet_rechaza_longitud():
    with pytest.raises(ValueError):
        si.decode_ts_packet(b"\x47" * 10)


def test_iter_ts_packets_resincroniza():
    data = b"\x00\xff" + si.make_ts_packet(0x00, b"\x00" * 5) + si.make_ts_packet(0x02, b"x")
    packets = list(si.iter_ts_packets(data))
    assert [p.pid for p in packets] == [0x00, 0x02]


# ---------------------------------------------------------------------------
# Secciones PAT/SDT/PMT
# ---------------------------------------------------------------------------
def test_parse_pat():
    section_bytes = _pat_section([(1, 0x100), (2, 0x200), (0, 0x010)])
    section = si.parse_section(0, section_bytes)
    assert section is not None
    assert section.table_id == si.TABLE_ID_PAT
    assert section.crc_ok is True
    assert section.programs == ((1, 0x100), (2, 0x200), (0, 0x010))
    assert section.pmt_map == {1: 0x100, 2: 0x200}
    assert si.pmt_pids_from_pat([section]) == [0x100, 0x200]


def test_parse_sdt():
    section_bytes = _sdt_section(
        [(1001, 0x19, "RTVE", "La 1 HD", False), (1002, 0x02, "PRISA", "Cadena SER", False)]
    )
    section = si.parse_section(0x11, section_bytes)
    assert section is not None
    assert section.is_sdt is True
    assert section.table_id_extension == 1  # original_network_id
    assert len(section.services) == 2
    primera, segunda = section.services
    assert (primera.service_id, primera.name, primera.provider, primera.service_type) == (
        1001,
        "La 1 HD",
        "RTVE",
        0x19,
    )
    assert primera.free_ca_mode is False
    assert segunda.service_type == 0x02
    assert segunda.name == "Cadena SER"


def test_parse_sdt_free_ca_mode():
    section = si.parse_section(0x11, _sdt_section([(2000, 0x01, "P", "Cifrado", True)]))
    assert section is not None
    assert section.services[0].free_ca_mode is True


def test_parse_pmt_sin_ca():
    section = si.parse_section(
        0x100,
        _pmt_section(1001, 0x101, [(0x1B, 0x101, b""), (0x0F, 0x102, b"")]),
    )
    assert section is not None
    assert section.is_pmt is True
    assert section.program_number == 1001
    assert section.pcr_pid == 0x101
    assert section.is_scrambled is False
    assert section.ca_system_ids == ()
    assert [(s.pid, s.stream_type, s.kind, s.codec) for s in section.streams] == [
        (0x101, 0x1B, "video", "H.264"),
        (0x102, 0x0F, "audio", "AAC"),
    ]


def test_parse_pmt_con_ca():
    section = si.parse_section(
        0x100,
        _pmt_section(1001, 0x101, [(0x02, 0x101, b"")], program_descriptors=_ca_descriptor()),
    )
    assert section is not None
    assert section.is_scrambled is True
    assert section.ca_system_ids == (0x0B00,)


def test_parse_pmt_ca_en_flujo():
    section = si.parse_section(
        0x100, _pmt_section(1001, 0x101, [(0x1B, 0x101, _ca_descriptor(0x0500))])
    )
    assert section is not None
    assert section.ca_system_ids == (0x0500,)
    assert section.streams[0].ca_system_ids == (0x0500,)
    assert section.streams[0].codec == "H.264"


def test_parse_section_basura():
    assert si.parse_section(0, b"") is None
    assert si.parse_section(0, b"\x00" * 5) is None


def test_stream_type_codec_usa_descriptores():
    assert si.stream_type_codec(0x06, ((0x6A, b"\x00"),)) == "AC-3"
    assert si.stream_type_codec(0x06, ((0x7A, b""),)) == "E-AC-3"
    assert si.stream_type_codec(0x06) is None
    assert si.stream_type_codec(0x24) == "HEVC"
    assert si.stream_kind(0x1B) == "video"
    assert si.stream_kind(0x0F) == "audio"
    assert si.stream_kind(0x05) == "data"


def test_descriptores_de_idioma():
    descriptor = bytes([0x0A, 0x04]) + b"spa" + b"\x00"
    section = si.parse_section(0x100, _pmt_section(1, 0x101, [(0x0F, 0x102, descriptor)]))
    assert section is not None
    assert section.streams[0].languages == ("spa",)


# ---------------------------------------------------------------------------
# SectionAssembler
# ---------------------------------------------------------------------------
def test_assembler_seccion_en_un_paquete():
    section_bytes = _pat_section([(1, 0x100)])
    assembler = si.SectionAssembler(pid=0)
    section = assembler.feed(b"\x00" + section_bytes, payload_unit_start=True)
    assert section is not None
    assert section.pid == 0
    assert section.table_id == si.TABLE_ID_PAT
    assert assembler.sections_seen == 1
    assert assembler.crc_errors == 0
    assert assembler.feed(b"", payload_unit_start=False) is None


def test_assembler_seccion_repartida_en_dos_paquetes():
    section_bytes = _pat_section([(1, 0x100), (2, 0x200)])
    assembler = si.SectionAssembler(pid=0)
    corte = 6
    assert assembler.feed(b"\x00" + section_bytes[:corte], payload_unit_start=True) is None
    section = assembler.feed(section_bytes[corte:], payload_unit_start=False)
    assert section is not None
    assert section.programs == ((1, 0x100), (2, 0x200))


def test_assembler_pointer_field():
    section_bytes = _pat_section([(1, 0x100)])
    assembler = si.SectionAssembler(pid=0)
    corte = 10
    assert assembler.feed(b"\x00" + section_bytes[:corte], payload_unit_start=True) is None
    resto = section_bytes[corte:]
    section = assembler.feed(bytes([len(resto)]) + resto, payload_unit_start=True)
    assert section is not None
    assert section.programs == ((1, 0x100),)


def test_assembler_pointer_field_con_relleno():
    section_bytes = _pat_section([(1, 0x100)])
    assembler = si.SectionAssembler(pid=0)
    assert assembler.feed(b"\x00" + section_bytes, payload_unit_start=True) is not None
    # Nuevo paquete: 4 bytes que cierran la sección anterior (pointer_field=4)
    # y a continuación la sección nueva.
    section = assembler.feed(b"\x04\xff\xff\xff\xff" + section_bytes, payload_unit_start=True)
    assert section is not None
    assert section.programs == ((1, 0x100),)


def test_assembler_varias_secciones_en_un_payload():
    primera = _pat_section([(1, 0x100)])
    segunda = _pat_section([(2, 0x200)])
    assembler = si.SectionAssembler(pid=0)
    sections = assembler.feed_all(b"\x00" + primera + segunda, payload_unit_start=True)
    assert len(sections) == 2
    assert sections[0].programs == ((1, 0x100),)
    assert sections[1].programs == ((2, 0x200),)


def test_assembler_cola_entre_llamadas():
    primera = _pat_section([(1, 0x100)])
    segunda = _pat_section([(2, 0x200)])
    assembler = si.SectionAssembler(pid=0)
    primera_devuelta = assembler.feed(b"\x00" + primera + segunda, payload_unit_start=True)
    assert primera_devuelta is not None and primera_devuelta.programs == ((1, 0x100),)
    pendiente = assembler.feed(b"", payload_unit_start=False)
    assert pendiente is not None and pendiente.programs == ((2, 0x200),)
    assert assembler.feed(b"", payload_unit_start=False) is None


def test_assembler_descarta_crc_invalido():
    section_bytes = bytearray(_pat_section([(1, 0x100)]))
    section_bytes[-1] ^= 0xFF
    assembler = si.SectionAssembler(pid=0, validate_crc=True)
    assert assembler.feed(b"\x00" + bytes(section_bytes), payload_unit_start=True) is None
    assert assembler.crc_errors == 1
    # Sin validación se acepta y se marca crc_ok=False.
    permisivo = si.SectionAssembler(pid=0, validate_crc=False)
    section = permisivo.feed(b"\x00" + bytes(section_bytes), payload_unit_start=True)
    assert section is not None and section.crc_ok is False


def test_assembler_relleno_0xff():
    assembler = si.SectionAssembler(pid=0x11)
    assert assembler.feed(b"\xff" * 184, payload_unit_start=False) is None
    assert assembler.sections_seen == 0


def test_assembler_continuidad():
    assembler = si.SectionAssembler(pid=0)
    assert assembler.check_continuity(0) is False
    assert assembler.check_continuity(1) is False
    assert assembler.check_continuity(1) is False  # duplicado: no cuenta
    assert assembler.check_continuity(5) is True   # salto
    assert assembler.discontinuities == 1


def test_assembler_reset():
    assembler = si.SectionAssembler(pid=0)
    assembler.feed(b"\x00" + _pat_section([(1, 0x100)])[:8], payload_unit_start=True)
    assembler.reset()
    assert assembler.drain() == []


# ---------------------------------------------------------------------------
# build_services y tubería completa
# ---------------------------------------------------------------------------
def _sections_escenario():
    pat = si.parse_section(0, _pat_section([(1001, 0x100), (1002, 0x101)]))
    sdt = si.parse_section(
        0x11,
        _sdt_section(
            [
                (1001, 0x19, "RTVE", "La 1 HD", False),
                (1002, 0x02, "PRISA", "Cadena SER", False),
            ]
        ),
    )
    pmt1 = si.parse_section(0x100, _pmt_section(1001, 0x201, [(0x1B, 0x201, b""), (0x0F, 0x202, b"")]))
    pmt2 = si.parse_section(
        0x101, _pmt_section(1002, 0x301, [(0x03, 0x301, b"")], program_descriptors=_ca_descriptor())
    )
    return pat, sdt, pmt1, pmt2


def test_build_services():
    pat, sdt, pmt1, pmt2 = _sections_escenario()
    services = si.build_services([pat], [sdt], {0x100: [pmt1], 0x101: [pmt2]})
    assert len(services) == 2
    hd = next(s for s in services if s.service_id == 1001)
    assert hd.name == "La 1 HD"
    assert hd.provider == "RTVE"
    assert hd.service_type == 0x19
    assert hd.service_type_label == "HDTV"
    assert hd.is_fta is True and hd.scrambled is False
    assert (hd.video_pid, hd.audio_pid, hd.pcr_pid, hd.pmt_pid) == (0x201, 0x202, 0x201, 0x100)
    assert hd.video_codec == "H.264"
    assert hd.audio_codec == "AAC"
    assert hd.video_pids == (0x201,) and hd.audio_pids == (0x202,)

    radio = next(s for s in services if s.service_id == 1002)
    assert radio.service_type == 0x02
    assert radio.service_type_label == "Radio"
    assert radio.scrambled is True and radio.is_fta is False
    assert radio.ca_system_ids == (0x0B00,)
    assert radio.video_pid is None and radio.audio_pid == 0x301


def test_build_services_sin_sdt_usa_el_pmt():
    pat, _sdt, pmt1, _pmt2 = _sections_escenario()
    services = si.build_services([pat], [], {0x100: [pmt1]})
    assert len(services) == 2
    hd = next(s for s in services if s.service_id == 1001)
    assert hd.name == "Servicio 1001"
    assert hd.video_pid == 0x201
    radio = next(s for s in services if s.service_id == 1002)
    assert radio.video_pid is None


def test_build_services_sin_pat_usa_los_pmt():
    _pat, _sdt, pmt1, _pmt2 = _sections_escenario()
    services = si.build_services([], [], {0x100: [pmt1]})
    assert [s.service_id for s in services] == [1001]


def test_parse_services_from_ts_completo():
    pat, sdt, pmt1, pmt2 = _sections_escenario()
    ts = _ts([(0x00, pat.raw), (0x11, sdt.raw), (0x100, pmt1.raw), (0x101, pmt2.raw)])
    services = si.parse_services_from_ts(ts)
    assert {s.service_id for s in services} == {1001, 1002}
    hd = next(s for s in services if s.service_id == 1001)
    assert hd.name == "La 1 HD"
    assert hd.video_pid == 0x201


def test_parse_services_from_ts_seccion_repartida():
    pat, sdt, pmt1, _pmt2 = _sections_escenario()
    section = pmt1.raw
    mitad = len(section) // 2
    ts = (
        si.make_ts_packet(0x00, b"\x00" + pat.raw, payload_unit_start=True, continuity_counter=0)
        + si.make_ts_packet(0x11, b"\x00" + sdt.raw, payload_unit_start=True, continuity_counter=0)
        + si.make_ts_packet(0x100, b"\x00" + section[:mitad], payload_unit_start=True, continuity_counter=0)
        + si.make_ts_packet(0x100, section[mitad:], payload_unit_start=False, continuity_counter=1)
    )
    services = si.parse_services_from_ts(ts)
    hd = next(s for s in services if s.service_id == 1001)
    assert hd.video_pid == 0x201


def test_parse_services_from_ts_sin_datos():
    assert si.parse_services_from_ts(b"") == []
    assert si.parse_services_from_ts(b"\x00" * 400) == []
