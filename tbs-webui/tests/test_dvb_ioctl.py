"""Tests de `app.dvb.frontend_ioctl` sin hardware.

Comprueban las constantes **verificadas contra los headers del kernel del host**
(kernel 5.15.0-198, DVB API 5.11) y el empaquetado de `struct dtv_property`,
que es la parte que no se puede depurar en Windows.
"""

from __future__ import annotations

import ctypes
import errno
import pathlib
import struct
import sys

_WEBUI = pathlib.Path(__file__).resolve().parents[1]
if str(_WEBUI) not in sys.path:
    sys.path.insert(0, str(_WEBUI))

import pytest  # noqa: E402

from app.dvb import frontend_ioctl as fi  # noqa: E402
from app.dvb import types as t  # noqa: E402


# ---------------------------------------------------------------------------
# Constantes (valores medidos con gcc en el host real)
# ---------------------------------------------------------------------------
def test_ioctls_del_frontend():
    assert fi.FE_GET_INFO == 0x80A86F3D
    assert fi.FE_READ_STATUS == 0x80046F45
    assert fi.FE_SET_PROPERTY == 0x40106F52
    assert fi.FE_GET_PROPERTY == 0x80106F53
    assert fi.FE_SET_TONE == 0x00006F42
    assert fi.FE_SET_VOLTAGE == 0x00006F43
    assert fi.FE_DISEQC_SEND_MASTER_CMD == 0x40076F3F
    assert fi.FE_DISEQC_SEND_BURST == 0x00006F41
    assert fi.FE_READ_BER == 0x80046F46
    assert fi.FE_READ_SIGNAL_STRENGTH == 0x80026F47


def test_ioctls_del_demux():
    assert fi.DMX_START == 0x00006F29
    assert fi.DMX_STOP == 0x00006F2A
    assert fi.DMX_SET_PES_FILTER == 0x40146F2C
    assert fi.DMX_SET_BUFFER_SIZE == 0x00006F2D


def test_comandos_dtv_numeracion_real_del_kernel():
    """Numeración de `/usr/include/linux/dvb/frontend.h` (NO la de la tarea)."""
    assert fi.DTV_UNDEFINED == 0
    assert fi.DTV_TUNE == 1
    assert fi.DTV_CLEAR == 2
    assert fi.DTV_FREQUENCY == 3
    assert fi.DTV_MODULATION == 4
    assert fi.DTV_BANDWIDTH_HZ == 5
    assert fi.DTV_INVERSION == 6
    assert fi.DTV_SYMBOL_RATE == 8
    assert fi.DTV_INNER_FEC == 9
    assert fi.DTV_VOLTAGE == 10
    assert fi.DTV_TONE == 11
    assert fi.DTV_PILOT == 12
    assert fi.DTV_ROLLOFF == 13
    assert fi.DTV_DELIVERY_SYSTEM == 17
    assert fi.DTV_API_VERSION == 35
    assert fi.DTV_CODE_RATE_HP == 36
    assert fi.DTV_CODE_RATE_LP == 37
    assert fi.DTV_GUARD_INTERVAL == 38
    assert fi.DTV_TRANSMISSION_MODE == 39
    assert fi.DTV_HIERARCHY == 40
    assert fi.DTV_STREAM_ID == 42
    assert fi.DTV_DVBT2_PLP_ID_LEGACY == 43
    assert fi.DTV_ENUM_DELSYS == 44
    assert fi.DTV_INTERLEAVING == 60
    assert fi.DTV_LNA == 61
    assert fi.DTV_STAT_SIGNAL_STRENGTH == 62
    assert fi.DTV_STAT_CNR == 63
    assert fi.DTV_STAT_PRE_ERROR_BIT_COUNT == 64
    assert fi.DTV_STAT_PRE_TOTAL_BIT_COUNT == 65
    assert fi.DTV_STAT_POST_ERROR_BIT_COUNT == 66
    assert fi.DTV_STAT_POST_TOTAL_BIT_COUNT == 67
    assert fi.DTV_STAT_ERROR_BLOCK_COUNT == 68
    assert fi.DTV_STAT_TOTAL_BLOCK_COUNT == 69
    assert fi.DTV_SCRAMBLING_SEQUENCE_INDEX == 70


def test_escalas_y_sistemas_de_entrega():
    assert (fi.FE_SCALE_NOT_AVAILABLE, fi.FE_SCALE_DECIBEL, fi.FE_SCALE_RELATIVE, fi.FE_SCALE_COUNTER) == (0, 1, 2, 3)
    assert (fi.SYS_DVBS, fi.SYS_DVBS2, fi.SYS_DVBT, fi.SYS_DVBT2, fi.SYS_DVBC_ANNEX_A) == (5, 6, 3, 16, 1)
    assert fi.DELIVERY_SYSTEM_TO_DRIVER["DVB-S2"] == fi.SYS_DVBS2
    assert fi.DRIVER_TO_DELIVERY_SYSTEM[fi.SYS_DVBS2] == "DVB-S2"
    assert fi.DRIVER_TO_DELIVERY_SYSTEM[fi.SYS_DVBT] == "DVB-T"
    assert fi.DRIVER_TO_DELIVERY_SYSTEM[fi.SYS_DVBT2] == "DVB-T2"


def test_tamanos_de_estructura_coinciden_con_el_kernel():
    assert fi._FE_INFO_FMT.size == 168
    assert fi._DISEQC_MASTER_FMT.size == 7
    assert fi._PES_FILTER_FMT.size == 20
    assert fi._DTV_PROP_SIZE == 76
    assert fi._DTV_PROP_U_OFFSET == 16
    assert fi._DTV_PROP_RESULT_OFFSET == 72
    assert fi._DTV_STATS_LEN_OFFSET == 16
    assert fi._DTV_STATS_FIRST_OFFSET == 17
    assert fi._DTV_STATS_STRIDE == 9
    assert fi._DTV_BUFFER_LEN_OFFSET == 48


def test_decode_info_buffer():
    raw = struct.pack(
        "<128sIIIIIIIIII",
        b"TurboSight TBS 6904se DVB-S/S2/S2x ".ljust(128, b"\0"),
        0, 950000, 2150000, 1000, 5000, 1000000, 45000000, 100, 0, 0x10000400,
    )
    info = fi.decode_info_buffer(raw, 0, 0, "/dev/dvb/adapter0/frontend0")
    assert info.name == "TurboSight TBS 6904se DVB-S/S2/S2x"
    assert info.type == "FE_QPSK"
    assert info.caps == 0x10000400
    assert (info.min_frequency, info.max_frequency) == (950000, 2150000)
    assert info.symbol_rates == (1000000, 45000000)


def test_decode_status():
    flags = fi.decode_status(0x1F)
    assert (flags.signal, flags.carrier, flags.viterbi, flags.sync, flags.lock) == (True,) * 5
    assert flags.is_locked is True
    assert flags.raw == 0x1F
    assert fi.decode_status(0x00).is_locked is False
    assert fi.decode_status(0x01).is_locked is False  # sólo señal
    assert fi.decode_status(0x10).is_locked is False  # lock sin señal


# ---------------------------------------------------------------------------
# Empaquetado de propiedades
# ---------------------------------------------------------------------------
def test_pack_property_u32_layout():
    prop = fi.pack_property_u32(fi.DTV_FREQUENCY, 1612000)
    assert len(prop) == 76
    assert struct.unpack_from("<I", prop, 0)[0] == fi.DTV_FREQUENCY
    assert struct.unpack_from("<I", prop, 16)[0] == 1612000
    assert struct.unpack_from("<i", prop, 72)[0] == 0


def test_pack_properties_cabecera_y_puntero():
    props = [fi.pack_property_u32(fi.DTV_CLEAR, 0), fi.pack_property_u32(fi.DTV_TUNE, 0)]
    buffer = fi.pack_properties(props)
    count = struct.unpack_from("<I", buffer, 0)[0]
    pointer = struct.unpack_from("<Q", buffer, 8)[0]
    assert count == 2
    # El puntero apunta al array dentro del propio buffer (offset 16).
    address = ctypes.addressof((ctypes.c_char * len(buffer)).from_buffer(buffer))
    assert pointer == address + 16
    # Las propiedades están donde dice el puntero.
    assert struct.unpack_from("<I", buffer, 16)[0] == fi.DTV_CLEAR
    assert struct.unpack_from("<I", buffer, 16 + 76)[0] == fi.DTV_TUNE
    assert len(buffer) == 16 + 2 * 76


def test_ioctl_argument_solo_cabecera_en_propiedades():
    """A fcntl se le pasa sólo la cabecera dtv_properties (16 B), nunca el array."""
    grande = bytearray(16 + 14 * 76)
    argumento = fi._ioctl_argument(fi.FE_GET_PROPERTY, grande)
    assert len(argumento) == 16
    assert fi._ioctl_argument(fi.FE_SET_PROPERTY, bytearray(92)).nbytes == 16
    # Los ioctls que no son de propiedades pasan el buffer completo.
    info = bytearray(168)
    assert fi._ioctl_argument(fi.FE_GET_INFO, info) is info
    assert fi._ioctl_argument(fi.FE_READ_STATUS, bytearray(4)).__len__() == 4
    # Un argumento entero (FE_SET_TONE) se deja tal cual.
    assert fi._ioctl_argument(fi.FE_SET_TONE, 1) == 1


def test_pack_properties_rechaza_demasiadas():
    with pytest.raises(t.DvbError):
        fi.pack_properties([fi.pack_property_u32(0, 0)] * (fi._DTV_MAX_MSGS + 1))


def test_parse_stats_entries_dos_capas_tbs():
    """La TBS 6904se devuelve 2 entradas: [0] dBm y [1] relativa (0..65535)."""
    buffer = bytearray(16 + 76)
    base = 16
    buffer[base + fi._DTV_STATS_LEN_OFFSET] = 2
    struct.pack_into("<B", buffer, base + 17, fi.FE_SCALE_DECIBEL)
    struct.pack_into("<q", buffer, base + 18, -51_700)          # -51.7 dBm
    struct.pack_into("<B", buffer, base + 17 + 9, fi.FE_SCALE_RELATIVE)
    struct.pack_into("<Q", buffer, base + 18 + 9, 52447)        # 80.03 %
    entradas = fi.parse_stats_entries(buffer, 0)
    assert len(entradas) == 2
    assert entradas[0] == (fi.FE_SCALE_DECIBEL, 2**64 - 51700, -51700)
    assert entradas[1] == (fi.FE_SCALE_RELATIVE, 52447, 52447)
    assert 79.9 < 100.0 * entradas[1][1] / 65535.0 < 80.2
    # `parse_stats_property` sigue devolviendo la primera entrada.
    assert fi.parse_stats_property(buffer, 0) == entradas[0]
    # Una capa no disponible se ignora.
    buffer[base + 17 + 9] = fi.FE_SCALE_NOT_AVAILABLE
    assert len(fi.parse_stats_entries(buffer, 0)) == 1


def test_parse_stats_property():
    buffer = bytearray(16 + 76)
    base = 16
    buffer[base + fi._DTV_STATS_LEN_OFFSET] = 1
    struct.pack_into("<B", buffer, base + 17, fi.FE_SCALE_DECIBEL)
    struct.pack_into("<q", buffer, base + 18, -42_500)  # -42.5 dBm en milésimas
    assert fi.parse_stats_property(buffer, 0) == (fi.FE_SCALE_DECIBEL, 2**64 - 42500, -42500)

    buffer[base + 17] = fi.FE_SCALE_NOT_AVAILABLE
    assert fi.parse_stats_property(buffer, 0) is None

    buffer[base + fi._DTV_STATS_LEN_OFFSET] = 0
    assert fi.parse_stats_property(buffer, 0) is None


def test_parse_stats_property_relativa():
    buffer = bytearray(16 + 76)
    base = 16
    buffer[base + fi._DTV_STATS_LEN_OFFSET] = 1
    struct.pack_into("<B", buffer, base + 17, fi.FE_SCALE_RELATIVE)
    struct.pack_into("<Q", buffer, base + 18, 65535)
    scale, unsigned, _signed = fi.parse_stats_property(buffer, 0)
    assert scale == fi.FE_SCALE_RELATIVE
    assert unsigned == 65535


def test_parse_buffer_property_enum_delsys():
    """`u.buffer.data` es `__u8[32]`: un BYTE por sistema de entrega."""
    buffer = bytearray(16 + 76)
    base = 16
    buffer[base + 16] = fi.SYS_DVBS      # data[0] = 5
    buffer[base + 17] = fi.SYS_DVBS2     # data[1] = 6
    struct.pack_into("<I", buffer, base + 48, 2)  # buffer.len
    length, values = fi.parse_buffer_property(buffer, 0)
    assert length == 2
    assert values == [fi.SYS_DVBS, fi.SYS_DVBS2] == [5, 6]

    # Reproducción del raw REAL medido en la tarjeta (TBS 6904se, adapter0):
    # cmd=44, u.data = 05 06 (DVBS, DVBS2) y u.buffer.len = 2 en el offset 48.
    real_prop = bytearray(76)
    real_prop[0:4] = struct.pack("<I", fi.DTV_ENUM_DELSYS)
    real_prop[16] = 5
    real_prop[17] = 6
    struct.pack_into("<I", real_prop, 48, 2)
    full = bytearray(16 + 76)
    struct.pack_into("<I", full, 0, 1)
    full[16 : 16 + 76] = real_prop
    length2, values2 = fi.parse_buffer_property(full, 0)
    assert length2 == 2
    assert values2 == [5, 6]
    # Y con el parseo antiguo (u32) habría dado 0x0605 = 1541 -> sin sistemas.
    assert struct.unpack_from("<I", real_prop, 16)[0] == 1541


# ---------------------------------------------------------------------------
# Propiedades de sintonización
# ---------------------------------------------------------------------------
def _props(params: t.TuneParams) -> list[tuple[int, int]]:
    return fi.build_tune_property_list(params)


def test_build_tune_property_list_dvbs2_astra():
    params = t.TuneParams(
        delivery_system="DVB-S2",
        frequency_khz=11_362_000,
        symbol_rate_ksps=22_000,
        polarization="H",
        fec="2/3",
        modulation="8PSK",
        rolloff="0.35",
        pilot="ON",
        use_lnb=True,
    )
    props = _props(params)
    commands = [cmd for cmd, _ in props]
    assert commands[0] == fi.DTV_CLEAR
    assert commands[-1] == fi.DTV_TUNE
    assert fi.DTV_VOLTAGE not in commands  # la conmutación va por ioctl legacy
    values = dict(props)
    assert values[fi.DTV_DELIVERY_SYSTEM] == fi.SYS_DVBS2
    assert values[fi.DTV_FREQUENCY] == 1_612_000        # FI en kHz
    assert values[fi.DTV_SYMBOL_RATE] == 22_000_000     # símbolos/s
    assert values[fi.DTV_INNER_FEC] == fi._FEC["2/3"] == 2
    assert values[fi.DTV_MODULATION] == 9               # 8PSK
    assert values[fi.DTV_INVERSION] == 2                # AUTO
    assert values[fi.DTV_ROLLOFF] == 0                  # 0.35
    assert values[fi.DTV_PILOT] == 0                    # ON


def test_build_tune_property_list_dvbt_usa_hz():
    params = t.TuneParams(
        delivery_system="DVB-T",
        frequency_khz=570_000,
        bandwidth_mhz=8,
        modulation="QAM/64",
        code_rate_hp="2/3",
        guard_interval="1/4",
        transmission_mode="8K",
        hierarchy="NONE",
        use_lnb=False,
    )
    values = dict(_props(params))
    assert values[fi.DTV_DELIVERY_SYSTEM] == fi.SYS_DVBT
    assert values[fi.DTV_FREQUENCY] == 570_000_000
    assert values[fi.DTV_BANDWIDTH_HZ] == 8_000_000
    assert values[fi.DTV_MODULATION] == 3               # QAM/64
    assert values[fi.DTV_CODE_RATE_HP] == 2
    assert values[fi.DTV_GUARD_INTERVAL] == 3           # 1/4
    assert values[fi.DTV_TRANSMISSION_MODE] == 1        # 8K
    assert values[fi.DTV_HIERARCHY] == 0                # NONE


def test_build_tune_property_list_multi_stream():
    params = t.TuneParams(
        delivery_system="DVB-S2",
        frequency_khz=12_551_500,
        stream_id=2,
        use_lnb=True,
    )
    values = dict(_props(params))
    assert values[fi.DTV_STREAM_ID] == 2


def test_build_tune_property_list_plp_dvbt2():
    params = t.TuneParams(
        delivery_system="DVB-T2", frequency_khz=500_000, bandwidth_mhz=8, plp_id=3
    )
    values = dict(_props(params))
    assert values[fi.DTV_DVBT2_PLP_ID_LEGACY] == 3


def test_build_tune_property_list_rechaza_sistema_desconocido():
    with pytest.raises(t.UnsupportedError):
        fi.build_tune_property_list(t.TuneParams(delivery_system="DVB-X", frequency_khz=1000))


def test_build_tune_property_list_fi_invalida():
    params = t.TuneParams(delivery_system="DVB-S2", frequency_khz=1_000, use_lnb=True)
    with pytest.raises(t.UnsupportedError):
        fi.build_tune_property_list(params)


def test_build_switching_property_list():
    params = t.TuneParams(
        delivery_system="DVB-S2",
        frequency_khz=11_362_000,
        polarization="H",
        use_lnb=True,
    )
    values = dict(fi.build_switching_property_list(params))
    assert values[fi.DTV_VOLTAGE] == fi.SEC_VOLTAGE_18  # H -> 18 V
    assert values[fi.DTV_TONE] == fi.SEC_TONE_OFF       # banda baja
    alta = t.TuneParams(delivery_system="DVB-S2", frequency_khz=12_551_500, use_lnb=True)
    assert dict(fi.build_switching_property_list(alta))[fi.DTV_TONE] == fi.SEC_TONE_ON


def test_voltage_code():
    assert fi._voltage_code("13V") == 13
    assert fi._voltage_code("18V") == 18
    assert fi._voltage_code("OFF") == 0
    assert fi._voltage_code("desconocido") == 13


# ---------------------------------------------------------------------------
# Errores y calidad
# ---------------------------------------------------------------------------
def test_translate_oserror_mapea_errno_del_contrato():
    assert isinstance(
        fi.translate_oserror(OSError(errno.EBUSY, "Device or resource busy"), "tune"),
        t.DeviceBusyError,
    )
    assert isinstance(
        fi.translate_oserror(OSError(errno.EAGAIN, "try again"), "tune"), t.DeviceBusyError
    )
    assert isinstance(
        fi.translate_oserror(OSError(errno.ENODEV, "no device"), "open"), t.DeviceNotFoundError
    )
    assert isinstance(
        fi.translate_oserror(OSError(errno.ENOENT, "missing"), "open"), t.DeviceNotFoundError
    )
    assert isinstance(
        fi.translate_oserror(OSError(errno.EINVAL, "invalid"), "ioctl"), t.UnsupportedError
    )
    assert isinstance(
        fi.translate_oserror(OSError(errno.EOPNOTSUPP, "no soportado"), "ioctl"), t.UnsupportedError
    )
    perm = fi.translate_oserror(OSError(errno.EPERM, "Operation not permitted"), "FE_SET_VOLTAGE")
    assert isinstance(perm, t.DvbError)
    assert "O_RDWR" in str(perm)
    acces = fi.translate_oserror(OSError(errno.EACCES, "Permission denied"), "open")
    assert isinstance(acces, t.DvbError)
    assert "video" in str(acces)


def test_ratio_y_calidad():
    escala = fi.FE_SCALE_COUNTER
    assert fi._ratio((escala, 0, 0), (escala, 100, 100)) == 0.0
    assert fi._ratio((escala, 5, 5), (escala, 100, 100)) == 0.05
    assert fi._ratio(None, (escala, 100, 100)) is None
    assert fi._ratio((escala, 0, 0), (escala, 0, 0)) == 0.0
    assert fi._ratio((fi.FE_SCALE_NOT_AVAILABLE, 0, 0), (escala, 0, 0)) is None

    assert fi._quality_percent(15.0, None, None) == 100.0
    assert fi._quality_percent(7.5, None, None) == 50.0
    assert fi._quality_percent(None, None, 42.0) == 42.0
    assert fi._quality_percent(None, None, None) is None
    con_ber = fi._quality_percent(15.0, 0.5, None)
    assert con_ber is not None and con_ber < 100.0


# ---------------------------------------------------------------------------
# Frontend sin hardware: errores claros
# ---------------------------------------------------------------------------
def test_ioctl_frontend_sin_abrir_falla_claro():
    info = t.FrontendInfo(0, 0, "fake", "/dev/dvb/adapter0/frontend0", "FE_QPSK", 0, (), 0, 0)
    frontend = fi.IoctlFrontend(info)
    with pytest.raises(t.DeviceNotFoundError):
        frontend.read_status()
    with pytest.raises(t.DeviceNotFoundError):
        frontend.tune(t.TuneParams(delivery_system="DVB-S2", frequency_khz=11_362_000))
    assert frontend.is_open is False
    frontend.close()  # idempotente
    frontend.close()


def test_ioctl_frontend_demux_path():
    info = t.FrontendInfo(3, 0, "fake", "/dev/dvb/adapter3/frontend0", "FE_QPSK", 0, (), 0, 0)
    frontend = fi.IoctlFrontend(info)
    assert frontend.demux_path == "/dev/dvb/adapter3/demux0"
    assert frontend.read_ts() == b""  # sin PIDs configurados no hay lectura
    frontend._close_demux()


@pytest.mark.skipif(not pathlib.Path("/dev/dvb").exists(), reason="requiere hardware DVB real")
def test_ioctl_frontend_abre_si_hay_hardware():  # pragma: no cover - host Linux
    from app.dvb import registry

    infos = registry.list_frontends(force_mock=False)
    if not infos:
        pytest.skip("sin frontends")
    frontend = fi.IoctlFrontend(infos[0])
    frontend.open()
    try:
        assert frontend.read_status().raw >= 0
        assert frontend.api_version() > 0
    finally:
        frontend.close()
