"""Frontend DVB real por ioctl (Linux). Sin dependencias fuera de la stdlib.

Todo el módulo usa `fcntl`, `struct`, `os`, `select` y `ctypes` (stdlib).

FUENTE DE LAS CONSTANTES
------------------------
Los números no se copian de memoria: se han verificado **contra los headers
uapi del kernel que corre en el host de destino** (Ubuntu 22.04, kernel
5.15.0-198-generic):

* ``/usr/include/linux/dvb/frontend.h`` (DVB API 5.11 en este kernel)
* ``/usr/include/linux/dvb/dmx.h``
* Documentación oficial:
  https://linuxtv.org/downloads/v4l-dvb-apis-new/ y
  https://github.com/torvalds/linux/blob/master/include/uapi/linux/dvb/frontend.h

Verificación empírica (``gcc`` + ``/tmp/dvb_constcheck`` en el host), que
también sirvió para descartar los valores —incorrectos— que circulaban en la
descripción de la tarea:

    DVB_API_VERSION=5.11
    sizeof(struct dtv_property)=76   (u @16, result @72)
    sizeof(struct dtv_fe_stats)=37   sizeof(struct dtv_stats)=9
    sizeof(struct dvb_frontend_info)=168  (name@0, type@128, caps@164)
    FE_GET_INFO            = 0x80a86f3d   _IOR('o', 61, struct dvb_frontend_info)
    FE_DISEQC_SEND_MASTER_CMD = 0x40076f3f  _IOW('o', 63, struct dvb_diseqc_master_cmd)
    FE_DISEQC_SEND_BURST   = 0x00006f41   _IO('o', 65)
    FE_SET_TONE            = 0x00006f42   _IO('o', 66)
    FE_SET_VOLTAGE         = 0x00006f43   _IO('o', 67)
    FE_READ_STATUS         = 0x80046f45   _IOR('o', 69, fe_status_t)
    FE_READ_BER            = 0x80046f46   _IOR('o', 70, __u32)
    FE_SET_PROPERTY        = 0x40106f52   _IOW('o', 82, struct dtv_properties)
    FE_GET_PROPERTY        = 0x80106f53   _IOR('o', 83, struct dtv_properties)
    DTV_UNDEFINED=0 DTV_TUNE=1 DTV_CLEAR=2 DTV_FREQUENCY=3 DTV_MODULATION=4
    DTV_BANDWIDTH_HZ=5 DTV_INVERSION=6 DTV_DISEQC_MASTER=7 DTV_SYMBOL_RATE=8
    DTV_INNER_FEC=9 DTV_VOLTAGE=10 DTV_TONE=11 DTV_PILOT=12 DTV_ROLLOFF=13
    DTV_DELIVERY_SYSTEM=17 DTV_API_VERSION=35 DTV_CODE_RATE_HP=36
    DTV_CODE_RATE_LP=37 DTV_GUARD_INTERVAL=38 DTV_TRANSMISSION_MODE=39
    DTV_HIERARCHY=40 DTV_STREAM_ID=42 DTV_DVBT2_PLP_ID_LEGACY=43
    DTV_ENUM_DELSYS=44 DTV_INTERLEAVING=60 DTV_LNA=61
    DTV_STAT_SIGNAL_STRENGTH=62 DTV_STAT_CNR=63 DTV_STAT_PRE_ERROR_BIT_COUNT=64
    DTV_STAT_PRE_TOTAL_BIT_COUNT=65 DTV_STAT_POST_ERROR_BIT_COUNT=66
    DTV_STAT_POST_TOTAL_BIT_COUNT=67 DTV_STAT_ERROR_BLOCK_COUNT=68
    DTV_STAT_TOTAL_BLOCK_COUNT=69 DTV_SCRAMBLING_SEQUENCE_INDEX=70
    DMX_START=0x6f29 DMX_SET_PES_FILTER=0x40146f2c DMX_SET_BUFFER_SIZE=0x6f2d
    sizeof(struct dmx_pes_filter_params)=20

En vez de escribir los ioctl como literales se calculan con las macros
`_IOC/_IO/_IOR/_IOW` (``asm-generic/ioctl.h``), de modo que el módulo sigue
siendo correcto en otras arquitecturas; los valores de arriba se usan en los
tests como comprobación.

NOTA SOBRE `O_RDONLY`
---------------------
El kernel sólo acepta ioctls ``_IOR`` en un fd abierto en modo lectura
(``drivers/media/dvb-core/dvb_frontend.c``, líneas ~2097-2116 del árbol 5.15:
"if the frontend is opened in read-only mode, only the ioctls that don't
interfere with the tune logic should be accepted ... That matches all _IOR()
ioctls"). Por eso ``FE_SET_VOLTAGE`` devuelve EPERM con ``dvb-fe-tool``, que
abre el dispositivo en sólo lectura; con ``O_RDWR`` funciona. No es un problema
de permisos ni del grupo ``video``.

NOTA SOBRE `fcntl.ioctl` Y `mutate_flag` (medido en la tarjeta real)
--------------------------------------------------------------------
`fcntl.ioctl(fd, req, buffer, mutate_flag)` no se comporta igual según el
tamaño del buffer ni según el ioctl. Matriz comprobada con Python 3.11.17 sobre
``/dev/dvb/adapter0/frontend0`` (TBS 6904se, kernel 5.15.0-198):

    ==========================================  ===========  ===========
    caso                                        mutate=False mutate=True
    ==========================================  ===========  ===========
    FE_GET_INFO (168 B, struct de salida)       ceros        OK
    FE_GET_PROPERTY, buffer <= 1024 B           OK           ceros
    FE_GET_PROPERTY, buffer >  1024 B           ValueError   OK
    FE_SET_PROPERTY, buffer <= 1024 B           OK           OK
    FE_SET_PROPERTY, buffer >  1024 B           ValueError   OK
    ==========================================  ===========  ===========

* Con ``mutate=False`` fcntl entrega al kernel una **copia desechable** del
  buffer: si la respuesta llega por ese mismo buffer (FE_GET_INFO) se pierde
  (nombre vacío, ``caps=0``). Sólo "funcionaba" con las propiedades DVB porque
  el kernel escribe el array a través del puntero interno de
  ``struct dtv_properties``, que no se copia.
* Con ``mutate=True`` y un buffer > 1024 B fcntl lanza
  ``ValueError: ioctl string arg too long`` (no es OSError: hay que traducirlo).

Solución adoptada: ``mutate=True`` siempre y, en FE_SET/GET_PROPERTY, pasar
**sólo la cabecera** de 16 B de ``struct dtv_properties`` (el array de
propiedades, de hasta 64·76 B, lo lee/escribe el kernel por el puntero). Con eso
funcionan tanto 1 propiedad como 14, sin copias ni ValueErrors.
"""

from __future__ import annotations

import ctypes
import errno
import logging
import os
import select
import struct
import time
from typing import Any, Iterable, Sequence

from .base import FrontendBase, split_diseqc_messages
from .types import (
    DeviceBusyError,
    DeviceNotFoundError,
    DvbError,
    FrontendError,
    FrontendInfo,
    LockFlags,
    SignalStats,
    TuneParams,
    UnsupportedError,
    normalize_delivery_system,
)

logger = logging.getLogger(__name__)

__all__ = [
    "IoctlFrontend",
    "read_frontend_info",
    "enumerate_delivery_systems",
    "build_tune_property_list",
    "build_switching_property_list",
    "pack_properties",
    "parse_stats_property",
    "parse_stats_entries",
    "SCALE_NAMES",
    "STATS_COMMANDS",
    # constantes
    "FE_GET_INFO",
    "FE_READ_STATUS",
    "FE_SET_PROPERTY",
    "FE_GET_PROPERTY",
    "DTV_CLEAR",
    "DTV_TUNE",
    "DTV_STAT_SIGNAL_STRENGTH",
    "DTV_STAT_CNR",
    "DTV_ENUM_DELSYS",
]

# ---------------------------------------------------------------------------
# Macros _IOC (asm-generic/ioctl.h)
# ---------------------------------------------------------------------------
_IOC_NRBITS = 8
_IOC_TYPEBITS = 8
_IOC_SIZEBITS = 14
_IOC_DIRBITS = 2

_IOC_NRSHIFT = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS
_IOC_DIRSHIFT = _IOC_SIZESHIFT + _IOC_SIZEBITS

_IOC_NONE = 0
_IOC_WRITE = 1
_IOC_READ = 2


def _IOC(direction: int, type_: str, nr: int, size: int) -> int:
    return (
        (direction << _IOC_DIRSHIFT)
        | (ord(type_) << _IOC_TYPESHIFT)
        | (nr << _IOC_NRSHIFT)
        | (size << _IOC_SIZESHIFT)
    )


def _IO(type_: str, nr: int) -> int:
    return _IOC(_IOC_NONE, type_, nr, 0)


def _IOR(type_: str, nr: int, size: int) -> int:
    return _IOC(_IOC_READ, type_, nr, size)


def _IOW(type_: str, nr: int, size: int) -> int:
    return _IOC(_IOC_WRITE, type_, nr, size)


# ---------------------------------------------------------------------------
# Estructuras del kernel (tamaños verificados con gcc en el host)
# ---------------------------------------------------------------------------
# struct dvb_frontend_info { char name[128]; enum fe_type type; __u32 ...; enum fe_caps caps; }
_FE_INFO_FMT = struct.Struct("<128sIIIIIIIIII")
assert _FE_INFO_FMT.size == 168, _FE_INFO_FMT.size

_FE_STATUS_FMT = struct.Struct("<I")

# struct dvb_diseqc_master_cmd { __u8 msg[6]; __u8 msg_len; }  __attribute__((packed))
_DISEQC_MASTER_FMT = struct.Struct("<6sB")
assert _DISEQC_MASTER_FMT.size == 7

# struct dtv_property { __u32 cmd; __u32 reserved[3]; union {__u32 data;
#   struct dtv_fe_stats st; struct {__u8 data[32]; __u32 len; ...} buffer;} u;
#   int result; }  __attribute__((packed))
_DTV_PROP_SIZE = 76
_DTV_PROP_U_OFFSET = 16       # offsetof(u)
_DTV_PROP_RESULT_OFFSET = 72  # offsetof(result)
_DTV_UNION_SIZE = 56
_DTV_STATS_LEN_OFFSET = 16    # u.st.len (dentro del dtv_property)
_DTV_STATS_FIRST_OFFSET = 17  # u.st.stat[0]
_DTV_STATS_STRIDE = 9         # 1 byte escala + 8 bytes valor
_DTV_BUFFER_DATA_OFFSET = 16  # u.buffer.data[32]
_DTV_BUFFER_LEN_OFFSET = 48   # u.buffer.len
_DTV_PROPS_HEADER_SIZE = 16   # struct dtv_properties { __u32 num; ptr props; }
_DTV_PROPS_PTR_OFFSET = 8
_DTV_MAX_MSGS = 64

_MAX_DTV_STATS = 4

# ---------------------------------------------------------------------------
# ioctls del frontend
# ---------------------------------------------------------------------------
FE_GET_INFO = _IOR("o", 61, _FE_INFO_FMT.size)
FE_DISEQC_RESET_OVERLOAD = _IO("o", 62)
FE_DISEQC_SEND_MASTER_CMD = _IOW("o", 63, _DISEQC_MASTER_FMT.size)
FE_DISEQC_RECV_SLAVE_REPLY = _IOR("o", 64, 9)
FE_DISEQC_SEND_BURST = _IO("o", 65)
FE_SET_TONE = _IO("o", 66)
FE_SET_VOLTAGE = _IO("o", 67)
FE_ENABLE_HIGH_LNB_VOLTAGE = _IO("o", 68)
FE_READ_STATUS = _IOR("o", 69, _FE_STATUS_FMT.size)
FE_READ_BER = _IOR("o", 70, 4)
FE_READ_SIGNAL_STRENGTH = _IOR("o", 71, 2)
FE_READ_SNR = _IOR("o", 72, 2)
FE_READ_UNCORRECTED_BLOCKS = _IOR("o", 73, 4)
FE_SET_FRONTEND = _IOW("o", 76, 32)
FE_GET_FRONTEND = _IOR("o", 77, 32)
FE_GET_EVENT = _IOR("o", 78, 40)
FE_SET_PROPERTY = _IOW("o", 82, _DTV_PROPS_HEADER_SIZE)
FE_GET_PROPERTY = _IOR("o", 83, _DTV_PROPS_HEADER_SIZE)

# ---------------------------------------------------------------------------
# ioctls del demux (linux/dvb/dmx.h) — extensión para leer el TS
# ---------------------------------------------------------------------------
DMX_START = _IO("o", 41)
DMX_STOP = _IO("o", 42)
DMX_SET_FILTER = _IOW("o", 43, 60)
DMX_SET_PES_FILTER = _IOW("o", 44, 20)
DMX_SET_BUFFER_SIZE = _IO("o", 45)
DMX_ADD_PID = _IOW("o", 51, 2)

DMX_IN_FRONTEND = 0
DMX_IN_DVR = 1
DMX_OUT_DECODER = 0
DMX_OUT_TAP = 1
DMX_OUT_TS_TAP = 2
DMX_OUT_TSDEMUX_TAP = 3
DMX_PES_OTHER = 20
DMX_IMMEDIATE_START = 4

# struct dmx_pes_filter_params { __u16 pid; enum input; enum output;
#                                enum pes_type; __u32 flags; }  (20 bytes)
_PES_FILTER_FMT = struct.Struct("<H2xIIII")
assert _PES_FILTER_FMT.size == 20

# ---------------------------------------------------------------------------
# Comandos DTV (INTERFACES.md §2 / kernel uapi frontend.h)
# ---------------------------------------------------------------------------
DTV_UNDEFINED = 0
DTV_TUNE = 1
DTV_CLEAR = 2
DTV_FREQUENCY = 3
DTV_MODULATION = 4
DTV_BANDWIDTH_HZ = 5
DTV_INVERSION = 6
DTV_DISEQC_MASTER = 7
DTV_SYMBOL_RATE = 8
DTV_INNER_FEC = 9
DTV_VOLTAGE = 10
DTV_TONE = 11
DTV_PILOT = 12
DTV_ROLLOFF = 13
DTV_DISEQC_SLAVE_REPLY = 14
DTV_FE_CAPABILITY_COUNT = 15
DTV_FE_CAPABILITY = 16
DTV_DELIVERY_SYSTEM = 17
DTV_ISDBT_PARTIAL_RECEPTION = 18
DTV_ISDBT_SOUND_BROADCASTING = 19
DTV_ISDBT_SB_SUBCHANNEL_ID = 20
DTV_ISDBT_SB_SEGMENT_IDX = 21
DTV_ISDBT_SB_SEGMENT_COUNT = 22
DTV_ISDBT_LAYERA_FEC = 23
DTV_ISDBT_LAYERA_MODULATION = 24
DTV_ISDBT_LAYERA_SEGMENT_COUNT = 25
DTV_ISDBT_LAYERA_TIME_INTERLEAVING = 26
DTV_ISDBT_LAYERB_FEC = 27
DTV_ISDBT_LAYERB_MODULATION = 28
DTV_ISDBT_LAYERB_SEGMENT_COUNT = 29
DTV_ISDBT_LAYERB_TIME_INTERLEAVING = 30
DTV_ISDBT_LAYERC_FEC = 31
DTV_ISDBT_LAYERC_MODULATION = 32
DTV_ISDBT_LAYERC_SEGMENT_COUNT = 33
DTV_ISDBT_LAYERC_TIME_INTERLEAVING = 34
DTV_API_VERSION = 35
DTV_CODE_RATE_HP = 36
DTV_CODE_RATE_LP = 37
DTV_GUARD_INTERVAL = 38
DTV_TRANSMISSION_MODE = 39
DTV_HIERARCHY = 40
DTV_ISDBT_LAYER_ENABLED = 41
DTV_STREAM_ID = 42
DTV_DVBT2_PLP_ID_LEGACY = 43
DTV_ENUM_DELSYS = 44
DTV_ATSCMH_FIC_VER = 45
DTV_ATSCMH_PARADE_ID = 46
DTV_ATSCMH_NOG = 47
DTV_ATSCMH_TNOG = 48
DTV_ATSCMH_SGN = 49
DTV_ATSCMH_PRC = 50
DTV_ATSCMH_RS_FRAME_MODE = 51
DTV_ATSCMH_RS_FRAME_ENSEMBLE = 52
DTV_ATSCMH_RS_CODE_MODE_PRI = 53
DTV_ATSCMH_RS_CODE_MODE_SEC = 54
DTV_ATSCMH_SCCC_BLOCK_MODE = 55
DTV_ATSCMH_SCCC_CODE_MODE_A = 56
DTV_ATSCMH_SCCC_CODE_MODE_B = 57
DTV_ATSCMH_SCCC_CODE_MODE_C = 58
DTV_ATSCMH_SCCC_CODE_MODE_D = 59
DTV_INTERLEAVING = 60
DTV_LNA = 61
DTV_STAT_SIGNAL_STRENGTH = 62
DTV_STAT_CNR = 63
DTV_STAT_PRE_ERROR_BIT_COUNT = 64
DTV_STAT_PRE_TOTAL_BIT_COUNT = 65
DTV_STAT_POST_ERROR_BIT_COUNT = 66
DTV_STAT_POST_TOTAL_BIT_COUNT = 67
DTV_STAT_ERROR_BLOCK_COUNT = 68
DTV_STAT_TOTAL_BLOCK_COUNT = 69
DTV_SCRAMBLING_SEQUENCE_INDEX = 70
DTV_MAX_COMMAND = DTV_SCRAMBLING_SEQUENCE_INDEX

# Estadísticas que se piden con FE_GET_PROPERTY.
STATS_COMMANDS: tuple[int, ...] = (
    DTV_STAT_SIGNAL_STRENGTH,
    DTV_STAT_CNR,
    DTV_STAT_PRE_ERROR_BIT_COUNT,
    DTV_STAT_PRE_TOTAL_BIT_COUNT,
    DTV_STAT_POST_ERROR_BIT_COUNT,
    DTV_STAT_POST_TOTAL_BIT_COUNT,
    DTV_STAT_ERROR_BLOCK_COUNT,
)
# `DTV_STAT_TOTAL_BLOCK_COUNT` es opcional (no lo implementan todos los drivers).
STATS_COMMANDS_OPTIONAL: tuple[int, ...] = (DTV_STAT_TOTAL_BLOCK_COUNT,)

# ---------------------------------------------------------------------------
# Escalas y enums del kernel
# ---------------------------------------------------------------------------
FE_SCALE_NOT_AVAILABLE = 0
FE_SCALE_DECIBEL = 1
FE_SCALE_RELATIVE = 2
FE_SCALE_COUNTER = 3

SCALE_NAMES: dict[int, str] = {
    FE_SCALE_NOT_AVAILABLE: "NO_DISPONIBLE",
    FE_SCALE_DECIBEL: "DECIBEL (0.001 dB; en señal -> dBm)",
    FE_SCALE_RELATIVE: "RELATIVA (0..65535)",
    FE_SCALE_COUNTER: "CONTADOR",
}

FE_NONE = 0x00
FE_HAS_SIGNAL = 0x01
FE_HAS_CARRIER = 0x02
FE_HAS_VITERBI = 0x04
FE_HAS_SYNC = 0x08
FE_HAS_LOCK = 0x10
FE_TIMEDOUT = 0x20
FE_REINIT = 0x40

FE_QPSK, FE_QAM, FE_OFDM, FE_ATSC = 0, 1, 2, 3
FE_TYPE_NAMES = {FE_QPSK: "FE_QPSK", FE_QAM: "FE_QAM", FE_OFDM: "FE_OFDM", FE_ATSC: "FE_ATSC"}

SEC_VOLTAGE_13 = 0
SEC_VOLTAGE_18 = 1
SEC_VOLTAGE_OFF = 2

_SEC_VOLTAGE_CODE = {13: SEC_VOLTAGE_13, 18: SEC_VOLTAGE_18, 0: SEC_VOLTAGE_OFF}

SEC_TONE_ON = 0
SEC_TONE_OFF = 1

SEC_MINI_A = 0
SEC_MINI_B = 1

# enum fe_delivery_system
SYS_UNDEFINED = 0
SYS_DVBC_ANNEX_A = 1
SYS_DVBC_ANNEX_B = 2
SYS_DVBT = 3
SYS_DSS = 4
SYS_DVBS = 5
SYS_DVBS2 = 6
SYS_DVBH = 7
SYS_ISDBT = 8
SYS_ISDBS = 9
SYS_ISDBC = 10
SYS_ATSC = 11
SYS_ATSCMH = 12
SYS_DTMB = 13
SYS_CMMB = 14
SYS_DAB = 15
SYS_DVBT2 = 16
SYS_TURBO = 17
SYS_DVBC_ANNEX_C = 18

DELIVERY_SYSTEM_TO_DRIVER: dict[str, int] = {
    "DVB-S": SYS_DVBS,
    "DVB-S2": SYS_DVBS2,
    "DVB-T": SYS_DVBT,
    "DVB-T2": SYS_DVBT2,
    "DVB-C": SYS_DVBC_ANNEX_A,
    # El kernel no define DVB-C2; se usa el anexo C como aproximación.
    "DVB-C2": SYS_DVBC_ANNEX_C,
    "ISDB-T": SYS_ISDBT,
    "ATSC": SYS_ATSC,
}
DRIVER_TO_DELIVERY_SYSTEM: dict[int, str] = {
    SYS_DVBS: "DVB-S",
    SYS_DVBS2: "DVB-S2",
    SYS_DVBT: "DVB-T",
    SYS_DVBT2: "DVB-T2",
    SYS_DVBC_ANNEX_A: "DVB-C",
    SYS_DVBC_ANNEX_C: "DVB-C2",
    SYS_DVBC_ANNEX_B: "DVB-C",
    SYS_ISDBT: "ISDB-T",
    SYS_ATSC: "ATSC",
}

# enum fe_modulation
_MODULATION = {
    "QPSK": 0,
    "QAM_16": 1,
    "QAM_32": 2,
    "QAM_64": 3,
    "QAM_128": 4,
    "QAM_256": 5,
    "QAM_AUTO": 6,
    "AUTO": 6,
    "VSB_8": 7,
    "VSB_16": 8,
    "PSK_8": 9,
    "8PSK": 9,
    "APSK_16": 10,
    "16APSK": 10,
    "APSK_32": 11,
    "32APSK": 11,
    "DQPSK": 12,
    "QAM_4_NR": 13,
}

# enum fe_code_rate
_FEC = {
    "NONE": 0,
    "1/2": 1,
    "2/3": 2,
    "3/4": 3,
    "4/5": 4,
    "5/6": 5,
    "6/7": 6,
    "7/8": 7,
    "8/9": 8,
    "AUTO": 9,
    "3/5": 10,
    "9/10": 11,
    "2/5": 12,
}

_INVERSION = {"OFF": 0, "ON": 1, "AUTO": 2}
_ROLLOFF = {"0.35": 0, "35": 0, "ROLLOFF_35": 0, "0.20": 1, "20": 1, "ROLLOFF_20": 1,
            "0.25": 2, "25": 2, "ROLLOFF_25": 2, "AUTO": 3}
_PILOT = {"ON": 0, "OFF": 1, "AUTO": 2}
_GUARD_INTERVAL = {
    "1/32": 0, "1/16": 1, "1/8": 2, "1/4": 3, "AUTO": 4,
    "1/128": 5, "19/128": 6, "19/256": 7, "PN420": 8, "PN595": 9, "PN945": 10,
}
_TRANSMISSION_MODE = {
    "2K": 0, "8K": 1, "AUTO": 2, "4K": 3, "1K": 4, "16K": 5, "32K": 6,
    "C1": 7, "C3780": 8,
}
_HIERARCHY = {"NONE": 0, "1": 1, "2": 2, "4": 3, "AUTO": 4}


def _normalize_token(value: str | None) -> str:
    """Normaliza "QAM/64", "8psk", "3/4 " -> "QAM_64", "8PSK", "3/4"."""
    if value is None:
        return ""
    return str(value).strip().upper().replace("/", "_").replace(" ", "")


def _lookup(table: dict[str, int], value: Any, *, default: int | None, field: str) -> int:
    token = _normalize_token(value)
    if not token:
        if default is None:
            raise UnsupportedError(f"Falta el parámetro {field}")
        return default
    if token in table:
        return table[token]
    # "3_4" -> "3/4" (tras normalizar se cambió la barra)
    restored = token.replace("_", "/")
    if restored in table:
        return table[restored]
    if default is None:
        raise UnsupportedError(f"Valor no soportado para {field}: {value!r}")
    return default


# ---------------------------------------------------------------------------
# Empaquetado de `struct dtv_property` / `struct dtv_properties`
# ---------------------------------------------------------------------------
_DTV_HEADER_FMT = struct.Struct("<4I")  # cmd + reserved[3]


def pack_property_u32(cmd: int, value: int = 0) -> bytes:
    """Un `struct dtv_property` con `u.data = value` (76 bytes)."""
    union = struct.pack("<I", int(value) & 0xFFFFFFFF).ljust(_DTV_UNION_SIZE, b"\0")
    return _DTV_HEADER_FMT.pack(int(cmd), 0, 0, 0) + union + b"\0\0\0\0"


def pack_property_stats(cmd: int) -> bytes:
    """Un `struct dtv_property` pidiendo estadísticas (`u.st` a cero)."""
    return pack_property_u32(cmd, 0)


def pack_properties(props: Sequence[bytes]) -> bytearray:
    """Construye el buffer `struct dtv_properties` + array de propiedades.

    Devuelve un ``bytearray`` cuyo primer campo es `num`, después el puntero al
    array y a continuación las propiedades empaquetadas (a las que apunta ese
    puntero). El buffer se pasa tal cual a `fcntl.ioctl`; la bandera
    `mutate_flag` la elige `_pick_mutate_flag()` por tamaño (ver `_ioctl`).
    """
    count = len(props)
    if count > _DTV_MAX_MSGS:
        raise DvbError(f"Demasiadas propiedades DVB ({count} > {_DTV_MAX_MSGS})")
    size = _DTV_PROPS_HEADER_SIZE + count * _DTV_PROP_SIZE
    buffer = bytearray(size)
    array = (ctypes.c_char * size).from_buffer(buffer)
    struct.pack_into("<I", buffer, 0, count)
    struct.pack_into("<Q", buffer, _DTV_PROPS_PTR_OFFSET, ctypes.addressof(array) + _DTV_PROPS_HEADER_SIZE)
    for index, prop in enumerate(props):
        if len(prop) != _DTV_PROP_SIZE:
            raise DvbError(f"Propiedad DTV de tamaño inválido: {len(prop)}")
        offset = _DTV_PROPS_HEADER_SIZE + index * _DTV_PROP_SIZE
        buffer[offset : offset + _DTV_PROP_SIZE] = prop
    # `array` comparte memoria con `buffer`: el puntero apunta al buffer del
    # bytearray, que sigue vivo mientras lo esté el propio bytearray.
    return buffer


def property_result(buffer: bytes | bytearray, index: int) -> int:
    """Campo `result` de la propiedad `index` (0 = OK)."""
    offset = _DTV_PROPS_HEADER_SIZE + index * _DTV_PROP_SIZE + _DTV_PROP_RESULT_OFFSET
    return struct.unpack_from("<i", buffer, offset)[0]


def property_data(buffer: bytes | bytearray, index: int) -> int:
    """Campo `u.data` de la propiedad `index`."""
    offset = _DTV_PROPS_HEADER_SIZE + index * _DTV_PROP_SIZE + _DTV_PROP_U_OFFSET
    return struct.unpack_from("<I", buffer, offset)[0]


def parse_stats_property(buffer: bytes | bytearray, index: int) -> tuple[int, int, int] | None:
    """Extrae (escala, valor_sin_signo, valor_con_signo) de `u.st.stat[0]`.

    Devuelve ``None`` si el driver dice que la medida no está disponible.
    """
    entries = parse_stats_entries(buffer, index)
    return entries[0] if entries else None


def parse_stats_entries(buffer: bytes | bytearray, index: int) -> list[tuple[int, int, int]]:
    """Todas las entradas de `u.st` (hasta 4) como (escala, uvalue, svalue).

    La TBS 6904se devuelve **dos** entradas para DTV_STAT_SIGNAL_STRENGTH:
    ``stat[0]`` en dBm (FE_SCALE_DECIBEL) y ``stat[1]`` relativa 0..65535; es
    exactamente lo que imprime `dvb-fe-tool` como "Signal= -51,7dBm" y
    "Layer A: Signal= 80,08%". Se ignoran las entradas no disponibles.
    """
    base = _DTV_PROPS_HEADER_SIZE + index * _DTV_PROP_SIZE
    length = buffer[base + _DTV_STATS_LEN_OFFSET]
    if length <= 0:
        return []
    entries: list[tuple[int, int, int]] = []
    for i in range(min(int(length), _MAX_DTV_STATS)):
        first = base + _DTV_STATS_FIRST_OFFSET + i * _DTV_STATS_STRIDE
        scale = buffer[first]
        if scale == FE_SCALE_NOT_AVAILABLE:
            continue
        unsigned = struct.unpack_from("<Q", buffer, first + 1)[0]
        signed = struct.unpack_from("<q", buffer, first + 1)[0]
        entries.append((scale, unsigned, signed))
    return entries


def parse_buffer_property(buffer: bytes | bytearray, index: int) -> tuple[int, list[int]]:
    """Extrae `u.buffer` (datos + longitud), p. ej. de DTV_ENUM_DELSYS.

    OJO: en `struct dtv_property` el campo es ``__u8 data[32]`` (bytes, no
    ``__u32``). El kernel escribe **un byte por valor**:
    ``tvp->u.buffer.data[i] = delsys`` (verificado en la tarjeta real: los 4
    bytes iniciales del buffer son ``05 06`` para DVBS/DVBS2, no ``05 00 00 00``
    ``06 00 00 00``). libdvbv5 lo lee igual (``prop.u.buffer.data[i]``).
    """
    base = _DTV_PROPS_HEADER_SIZE + index * _DTV_PROP_SIZE
    length = struct.unpack_from("<I", buffer, base + _DTV_BUFFER_LEN_OFFSET)[0]
    available = 32
    count = max(0, min(int(length), available))
    values = [buffer[base + _DTV_BUFFER_DATA_OFFSET + i] for i in range(count)]
    return int(length), values


# ---------------------------------------------------------------------------
# Traducción de errores
# ---------------------------------------------------------------------------
_ERRNO_MAP: dict[int, type[FrontendError]] = {
    errno.EBUSY: DeviceBusyError,
    errno.EAGAIN: DeviceBusyError,
    errno.EWOULDBLOCK: DeviceBusyError,
    errno.ENODEV: DeviceNotFoundError,
    errno.ENOENT: DeviceNotFoundError,
    errno.ENXIO: DeviceNotFoundError,
    errno.EOPNOTSUPP: UnsupportedError,
    errno.EINVAL: UnsupportedError,
    errno.ENOTTY: UnsupportedError,
    errno.ENOSYS: UnsupportedError,
}


def translate_oserror(exc: OSError, action: str, path: str | None = None) -> FrontendError:
    """Convierte un `OSError` de ioctl/open en la excepción del contrato (§2)."""
    code = exc.errno or 0
    name = errno.errorcode.get(code, str(code))
    where = f" en {path}" if path else ""
    if code == errno.EPERM:
        return DvbError(
            f"{action}{where}: EPERM ({exc.strerror}). El frontend está abierto en "
            "modo sólo lectura o el driver rechaza la operación; ábrelo con O_RDWR. "
            "Ver nota sobre O_RDONLY en frontend_ioctl.py"
        )
    if code == errno.EACCES:
        return DvbError(
            f"{action}{where}: EACCES ({exc.strerror}). El usuario necesita acceso al "
            "dispositivo (grupo 'video' o ACL de systemd-logind)"
        )
    cls = _ERRNO_MAP.get(code, DvbError)
    return cls(f"{action}{where}: {name} ({exc.strerror})")


# ---------------------------------------------------------------------------
# Construcción de las propiedades de sintonización
# ---------------------------------------------------------------------------
def _voltage_code(voltage: str) -> int:
    """Convierte "13V" | "18V" | "OFF" en el valor de SEC_VOLTAGE_*."""
    return {"13V": 13, "18V": 18, "OFF": 0}.get(str(voltage).strip().upper(), 13)


def _frequency_value(params: TuneParams) -> tuple[int, int]:
    """Devuelve (valor, comando) de frecuencia para el driver.

    * DVB-S/S2 (y demás satélite): kHz de frecuencia intermedia.
    * DVB-T/T2/C/C2, ISDB-T, ATSC: Hz.
    """
    system = normalize_delivery_system(params.delivery_system)
    if system in ("DVB-S", "DVB-S2"):
        if params.use_lnb:
            value = params.if_frequency_khz
            if value <= 0:
                raise UnsupportedError(
                    f"FI no válida ({value} kHz): revisa el LNB "
                    f"(transponder {params.frequency_khz} kHz, OL {params.lnb_offset_khz} kHz)"
                )
        else:
            value = int(params.frequency_khz)
        return int(value), DTV_FREQUENCY
    return int(params.frequency_khz) * 1000, DTV_FREQUENCY


def build_tune_property_list(params: TuneParams) -> list[tuple[int, int]]:
    """Lista de (cmd DTV_*, valor) que se envía a FE_SET_PROPERTY.

    Es una función pura: los tests comprueban el orden y los valores sin
    necesidad de hardware.
    """
    system = normalize_delivery_system(params.delivery_system)
    if system not in DELIVERY_SYSTEM_TO_DRIVER:
        raise UnsupportedError(f"Sistema de entrega no soportado: {params.delivery_system!r}")

    props: list[tuple[int, int]] = [
        (DTV_CLEAR, 0),
        (DTV_DELIVERY_SYSTEM, DELIVERY_SYSTEM_TO_DRIVER[system]),
    ]

    frequency, _ = _frequency_value(params)
    props.append((DTV_FREQUENCY, frequency))

    if params.symbol_rate_ksps is not None:
        props.append((DTV_SYMBOL_RATE, int(params.symbol_rate_ksps) * 1000))
    if params.fec or system in ("DVB-S", "DVB-S2", "DVB-C", "DVB-C2"):
        props.append((DTV_INNER_FEC, _lookup(_FEC, params.fec, default=9, field="fec")))
    if params.modulation or system in ("DVB-S", "DVB-S2"):
        props.append(
            (DTV_MODULATION, _lookup(_MODULATION, params.modulation, default=6, field="modulation"))
        )
    props.append((DTV_INVERSION, _lookup(_INVERSION, params.inversion, default=2, field="inversion")))

    if params.bandwidth_mhz:
        props.append((DTV_BANDWIDTH_HZ, int(params.bandwidth_mhz) * 1_000_000))
    if params.code_rate_hp:
        props.append((DTV_CODE_RATE_HP, _lookup(_FEC, params.code_rate_hp, default=9, field="code_rate_hp")))
    if params.code_rate_lp:
        props.append((DTV_CODE_RATE_LP, _lookup(_FEC, params.code_rate_lp, default=9, field="code_rate_lp")))
    if params.guard_interval:
        props.append(
            (DTV_GUARD_INTERVAL,
             _lookup(_GUARD_INTERVAL, params.guard_interval, default=4, field="guard_interval"))
        )
    if params.transmission_mode:
        props.append(
            (DTV_TRANSMISSION_MODE,
             _lookup(_TRANSMISSION_MODE, params.transmission_mode, default=2, field="transmission_mode"))
        )
    if params.hierarchy:
        props.append((DTV_HIERARCHY, _lookup(_HIERARCHY, params.hierarchy, default=4, field="hierarchy")))
    if params.rolloff:
        props.append((DTV_ROLLOFF, _lookup(_ROLLOFF, params.rolloff, default=3, field="rolloff")))
    if params.pilot:
        props.append((DTV_PILOT, _lookup(_PILOT, params.pilot, default=2, field="pilot")))
    if params.stream_id is not None and int(params.stream_id) >= 0:
        props.append((DTV_STREAM_ID, int(params.stream_id)))
    if params.plp_id is not None and int(params.plp_id) >= 0:
        props.append((DTV_DVBT2_PLP_ID_LEGACY, int(params.plp_id)))

    # La conmutación (voltaje/tono) NO va en esta lista: se aplica antes con
    # los ioctls legacy FE_SET_VOLTAGE/FE_SET_TONE (ver `tune()`), que es lo que
    # hace libdvbv5/dvbv5-zap. El kernel guarda ese estado en `fepriv->tone` /
    # `fepriv->voltage` y lo reaplica al sintonizar
    # (dvb_frontend.c:3064-3067), mientras que DTV_CLEAR sólo limpia la caché
    # DVBv5 y "does not effect hardware" (dvb_frontend.c:1912-1918).

    props.append((DTV_TUNE, 0))
    return props


def build_switching_property_list(params: TuneParams) -> list[tuple[int, int]]:
    """Propiedades DVBv5 equivalentes de voltaje/tono (DTV_VOLTAGE/DTV_TONE).

    El kernel las traduce a los ioctls legacy FE_SET_VOLTAGE/FE_SET_TONE
    (dvb_frontend.c:1957-1966). Se usan desde `set_tone_voltage()` y en tests.
    """
    voltage = params.needed_voltage()
    code = {"13V": SEC_VOLTAGE_13, "18V": SEC_VOLTAGE_18, "OFF": SEC_VOLTAGE_OFF}.get(
        str(voltage).upper()
    )
    props: list[tuple[int, int]] = []
    if code is not None:
        props.append((DTV_VOLTAGE, code))
    props.append((DTV_TONE, SEC_TONE_ON if params.needed_tone() else SEC_TONE_OFF))
    return props


def _pack_tune_properties(props: Iterable[tuple[int, int]]) -> bytearray:
    return pack_properties([pack_property_u32(cmd, value) for cmd, value in props])


# ---------------------------------------------------------------------------
# Lectura de información del frontend
# ---------------------------------------------------------------------------
def _decode_name(raw: bytes) -> str:
    name = raw.split(b"\0", 1)[0]
    for encoding in ("utf-8", "latin-1"):
        try:
            return name.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return name.decode("latin-1", "replace").strip()


_IOCTL_BUFSZ = 1024  # IOCTL_BUFSZ de Modules/fcntlmodule.c

# ioctls cuyo argumento es `struct dtv_properties` (cabecera + puntero al array)
_PROPERTY_IOCTLS = frozenset({FE_SET_PROPERTY, FE_GET_PROPERTY})


def _ioctl_argument(request: int, arg: Any) -> Any:
    """Argumento que se pasa a `fcntl.ioctl`.

    Para FE_SET/GET_PROPERTY se pasa **sólo la cabecera** `struct dtv_properties`
    (16 B): el kernel lee/escribe el array de propiedades a través del puntero
    que va dentro, así que fcntl no necesita (ni debe) copiar el buffer entero.
    """
    if request in _PROPERTY_IOCTLS and isinstance(arg, (bytearray, bytes, memoryview)):
        return memoryview(arg)[: _DTV_PROPS_HEADER_SIZE]
    return arg


def _ioctl(fd: int, request: int, arg: Any, mutate: bool = True) -> Any:
    """`fcntl.ioctl` con la bandera `mutate_flag` correcta para DVB.

    Reglas medidas en la tarjeta real (TBS 6904se, Python 3.11.17): ver la tabla
    del docstring del módulo. En resumen:

    * ``mutate=False`` (o ``None``: fcntl lo interpreta como falso) hace que
      fcntl use una **copia desechable** del buffer: la respuesta del kernel
      nunca llega a nuestro objeto (FE_GET_INFO devolvía nombre vacío y caps=0;
      FE_READ_STATUS devolvía siempre 0x00). Sólo funcionaba por accidente
      cuando el kernel escribía a través de un puntero interno (array de
      `dtv_properties`).
    * Con ``mutate=True`` y un buffer > 1024 B, fcntl lanza
      ``ValueError: ioctl string arg too long``.

    Por eso se usa siempre ``mutate=True`` y, en las propiedades DVB, se pasa
    únicamente la cabecera de 16 B (los 76·N bytes del array los escribe el
    kernel por el puntero).
    """
    import fcntl

    try:
        return fcntl.ioctl(fd, request, _ioctl_argument(request, arg), mutate)
    except ValueError as exc:
        # fcntl usa ValueError (no OSError) cuando el argumento no le encaja.
        # Se unifica como OSError para que un ioctl problemático no derribe el
        # descubrimiento de frontends.
        raise OSError(f"ioctl inválido ({request:#x}): {exc}") from exc


def decode_info_buffer(buffer: bytes | bytearray, adapter: int, frontend: int, device_path: str) -> FrontendInfo:
    """Convierte 168 bytes de FE_GET_INFO en `FrontendInfo` (sin delsys)."""
    (
        raw_name,
        fe_type,
        frequency_min,
        frequency_max,
        _stepsize,
        _tolerance,
        symbol_rate_min,
        symbol_rate_max,
        _sr_tolerance,
        _notifier,
        caps,
    ) = _FE_INFO_FMT.unpack_from(buffer, 0)
    symbol_rates = ()
    if symbol_rate_min or symbol_rate_max:
        symbol_rates = (int(symbol_rate_min), int(symbol_rate_max))
    return FrontendInfo(
        adapter=int(adapter),
        frontend=int(frontend),
        name=_decode_name(raw_name),
        device_path=device_path,
        type=FE_TYPE_NAMES.get(int(fe_type), f"FE_UNKNOWN({int(fe_type)})"),
        caps=int(caps),
        delivery_systems=(),
        min_frequency=int(frequency_min),
        max_frequency=int(frequency_max),
        symbol_rates=symbol_rates,
    )


def enumerate_delivery_systems(fd: int) -> tuple[int, tuple[str, ...]]:
    """Consulta DTV_ENUM_DELSYS. Devuelve (versión de API, sistemas soportados)."""
    api_version = 0
    systems: tuple[str, ...] = ()
    try:
        buffer = pack_properties([pack_property_u32(DTV_API_VERSION, 0)])
        # `_ioctl` elige mutate_flag por tamaño (ver la tabla del docstring del
        # módulo): <= 1024 B -> False (puntero directo y respuesta en el buffer),
        # > 1024 B -> True (con False, fcntl lanza "ioctl string arg too long").
        _ioctl(fd, FE_GET_PROPERTY, buffer)
        api_version = property_data(buffer, 0)
    except OSError as exc:
        logger.debug("DTV_API_VERSION no disponible: %s", exc)
    try:
        buffer = pack_properties([pack_property_u32(DTV_ENUM_DELSYS, 0)])
        _ioctl(fd, FE_GET_PROPERTY, buffer)
        _length, values = parse_buffer_property(buffer, 0)
        systems = tuple(
            DRIVER_TO_DELIVERY_SYSTEM[value]
            for value in values
            if value in DRIVER_TO_DELIVERY_SYSTEM
        )
    except OSError as exc:
        logger.debug("DTV_ENUM_DELSYS no disponible: %s", exc)
    return api_version, systems


def read_frontend_info(device_path: str, adapter: int = 0, frontend: int = 0) -> FrontendInfo:
    """Abre el dispositivo en sólo lectura y devuelve su `FrontendInfo`.

    Se usa desde `registry.list_frontends()`; no requiere permiso de escritura,
    así que funciona aunque el usuario no pueda sintonizar.
    """
    try:
        fd = os.open(device_path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as exc:
        raise translate_oserror(exc, "open(O_RDONLY)", device_path) from exc
    try:
        raw = bytearray(_FE_INFO_FMT.size)
        _ioctl(fd, FE_GET_INFO, raw)
        info = decode_info_buffer(raw, adapter, frontend, device_path)
        _api, systems = enumerate_delivery_systems(fd)
        if systems:
            info = FrontendInfo(
                adapter=info.adapter,
                frontend=info.frontend,
                name=info.name,
                device_path=info.device_path,
                type=info.type,
                caps=info.caps,
                delivery_systems=systems,
                min_frequency=info.min_frequency,
                max_frequency=info.max_frequency,
                symbol_rates=info.symbol_rates,
            )
        return info
    except OSError as exc:
        raise translate_oserror(exc, "FE_GET_INFO", device_path) from exc
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# Implementación del frontend
# ---------------------------------------------------------------------------
class IoctlFrontend(FrontendBase):
    """Frontend DVB real (ioctl). Implementa `base.Frontend` y la extensión TS.

    Ejemplo::

        fe = IoctlFrontend(info)
        fe.open()
        fe.tune(params)
        if fe.wait_lock(5.0):
            print(fe.read_stats())
        fe.close()
    """

    is_mock = False

    def __init__(self, info: FrontendInfo, demux_path: str | None = None) -> None:
        self.info = info
        self.device_path = info.device_path
        self.demux_path = demux_path or self._guess_demux_path(info.device_path)
        self._fd: int | None = None
        self._demux_fds: dict[int, int] = {}
        self._pid_list: list[int] = []
        self._tuned: TuneParams | None = None
        self._last_stats: SignalStats | None = None

    # -- utilidades --------------------------------------------------------
    @staticmethod
    def _guess_demux_path(device_path: str) -> str:
        """`/dev/dvb/adapterN/frontendM` -> `/dev/dvb/adapterN/demux0`.

        Se construye con separador POSIX explícito porque la ruta es siempre de
        Linux (aunque los tests se ejecuten en Windows).
        """
        base = device_path.replace("\\", "/").rsplit("/", 1)[0]
        return f"{base}/demux0"

    @property
    def is_open(self) -> bool:
        return self._fd is not None

    @property
    def fd(self) -> int | None:
        return self._fd

    @property
    def pid_list(self) -> list[int]:
        return list(self._pid_list)

    def _require_fd(self) -> int:
        if self._fd is None:
            raise DeviceNotFoundError(
                f"El frontend {self.device_path} no está abierto (llama antes a open())"
            )
        return self._fd

    def _ioctl(self, request: int, arg: Any, mutate: bool = True) -> Any:
        """ioctl del frontend abierto, traduciendo los errores al contrato.

        OJO: `mutate` debe ser un bool de verdad. `fcntl.ioctl` parsea la bandera
        como predicado, así que ``None`` equivale a ``False`` (copia desechable)
        y los ioctls de salida como FE_READ_STATUS devolverían siempre ceros.
        """
        fd = self._require_fd()
        try:
            return _ioctl(fd, request, arg, mutate)
        except OSError as exc:
            raise translate_oserror(exc, f"ioctl 0x{request:08x}", self.device_path) from exc

    # -- ciclo de vida -----------------------------------------------------
    def open(self) -> None:
        """Abre el frontend en O_RDWR|O_NONBLOCK (idempotente)."""
        if self._fd is not None:
            return
        try:
            self._fd = os.open(self.device_path, os.O_RDWR | os.O_NONBLOCK)
        except OSError as exc:
            raise translate_oserror(exc, "open(O_RDWR|O_NONBLOCK)", self.device_path) from exc
        try:
            self.refresh_info()
        except FrontendError as exc:  # pragma: no cover - driver raro
            logger.debug("No se pudo refrescar la info de %s: %s", self.device_path, exc)

    def close(self) -> None:
        """Cierra el frontend y los filtros del demux (idempotente)."""
        self._close_demux()
        fd, self._fd = self._fd, None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:  # pragma: no cover - ya cerrado
                logger.debug("Error cerrando %s", self.device_path, exc_info=True)

    def refresh_info(self) -> FrontendInfo:
        """Vuelve a leer FE_GET_INFO y DTV_ENUM_DELSYS."""
        raw = bytearray(_FE_INFO_FMT.size)
        self._ioctl(FE_GET_INFO, raw)
        info = decode_info_buffer(raw, self.info.adapter, self.info.frontend, self.device_path)
        _api, systems = enumerate_delivery_systems(self._require_fd())
        if systems:
            info = FrontendInfo(
                adapter=info.adapter,
                frontend=info.frontend,
                name=info.name,
                device_path=info.device_path,
                type=info.type,
                caps=info.caps,
                delivery_systems=systems,
                min_frequency=info.min_frequency,
                max_frequency=info.max_frequency,
                symbol_rates=info.symbol_rates,
            )
        self.info = info
        return info

    def api_version(self) -> int:
        """DTV_API_VERSION (p. ej. 0x050B = 5.11)."""
        buffer = pack_properties([pack_property_u32(DTV_API_VERSION, 0)])
        self._ioctl(FE_GET_PROPERTY, buffer)
        return property_data(buffer, 0)

    # -- sintonización -----------------------------------------------------
    def tune(self, params: TuneParams) -> None:
        """Aplica los parámetros (FE_SET_PROPERTY con DTV_CLEAR ... DTV_TUNE).

        Antes de sintonizar se aplica la conmutación del LNB (voltaje y tono)
        si el sistema es de satélite y el llamador no lo ha hecho ya; un fallo
        de conmutación no aborta la sintonización (se registra un aviso).
        """
        system = normalize_delivery_system(params.delivery_system)
        if system in ("DVB-S", "DVB-S2") and (
            params.use_lnb or params.tone_22k is not None or params.voltage is not None
        ):
            try:
                self.set_voltage(_voltage_code(params.needed_voltage()))
                self.set_tone(bool(params.needed_tone()))
            except FrontendError as exc:
                logger.warning("No se pudo aplicar la conmutación del LNB: %s", exc)

        props = build_tune_property_list(params)
        buffer = _pack_tune_properties(props)
        self._ioctl(FE_SET_PROPERTY, buffer)
        self._tuned = params
        logger.debug(
            "tune %s: %d propiedades enviadas a %s",
            params.delivery_system,
            len(props),
            self.device_path,
        )

    def wait_lock(self, timeout_s: float = 5.0, raise_on_timeout: bool = False) -> bool:
        """Espera a `LockFlags.is_locked` (señal + lock). False si expira."""
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        while True:
            flags = self.read_status()
            if flags.is_locked:
                return True
            if time.monotonic() >= deadline:
                if raise_on_timeout:
                    from .types import LockTimeoutError

                    raise LockTimeoutError(
                        f"Sin LOCK en {self.device_path} tras {timeout_s} s "
                        f"(estado: señal={flags.signal} portadora={flags.carrier} "
                        f"viterbi={flags.viterbi} sync={flags.sync} lock={flags.lock})"
                    )
                return False
            time.sleep(0.1)

    def set_property(self, cmd: int, value: int = 0) -> None:
        """Envía UNA propiedad DVBv5 (diagnóstico de FE_SET_PROPERTY paso a paso).

        El kernel acumula las propiedades en la caché del descriptor, así que
        enviarlas de una en una es equivalente a enviarlas todas juntas.
        """
        self._ioctl(FE_SET_PROPERTY, pack_properties([pack_property_u32(cmd, value)]))

    def get_property(self, cmd: int) -> int:
        """Lee `u.data` de UNA propiedad DVBv5."""
        buffer = pack_properties([pack_property_u32(cmd, 0)])
        self._ioctl(FE_GET_PROPERTY, buffer)
        return property_data(buffer, 0)

    def get_property_buffer(self, cmd: int) -> tuple[int, list[int]]:
        """Lee `u.buffer` (datos + longitud) de UNA propiedad DVBv5."""
        buffer = pack_properties([pack_property_u32(cmd, 0)])
        self._ioctl(FE_GET_PROPERTY, buffer)
        return parse_buffer_property(buffer, 0)

    def read_status(self) -> LockFlags:
        """FE_READ_STATUS -> `LockFlags`."""
        raw = bytearray(_FE_STATUS_FMT.size)
        self._ioctl(FE_READ_STATUS, raw)
        value = _FE_STATUS_FMT.unpack_from(raw, 0)[0]
        return decode_status(value)

    def read_raw_stats(self) -> dict[int, list[tuple[int, int, int]]]:
        """FE_GET_PROPERTY sin interpretar: ``{cmd: [(escala, uvalue, svalue), ...]}``.

        Sirve para diagnóstico (p. ej. saber si DTV_STAT_SIGNAL_STRENGTH llega en
        escala relativa 0..65535 o en dBm, y cuántas capas devuelve el driver).
        """
        fd = self._require_fd()
        commands = list(STATS_COMMANDS) + list(STATS_COMMANDS_OPTIONAL)
        try:
            buffer = pack_properties([pack_property_stats(cmd) for cmd in commands])
            _ioctl(fd, FE_GET_PROPERTY, buffer)
        except OSError:
            commands = list(STATS_COMMANDS)
            buffer = pack_properties([pack_property_stats(cmd) for cmd in commands])
            _ioctl(fd, FE_GET_PROPERTY, buffer)
        return {cmd: parse_stats_entries(buffer, index) for index, cmd in enumerate(commands)}

    def _read_stats_values(self) -> dict[int, list[tuple[int, int, int]]] | None:
        """Lee todas las estadísticas; ``None`` si el ioctl falla del todo."""
        fd = self._require_fd()
        commands = list(STATS_COMMANDS) + list(STATS_COMMANDS_OPTIONAL)
        try:
            buffer = pack_properties([pack_property_stats(cmd) for cmd in commands])
            _ioctl(fd, FE_GET_PROPERTY, buffer)
        except OSError as exc:
            # Algún driver no soporta DTV_STAT_TOTAL_BLOCK_COUNT: se reintenta
            # sin las opcionales.
            logger.debug("FE_GET_PROPERTY completo falló (%s); se reintenta sin opcionales", exc)
            commands = list(STATS_COMMANDS)
            try:
                buffer = pack_properties([pack_property_stats(cmd) for cmd in commands])
                _ioctl(fd, FE_GET_PROPERTY, buffer)
            except OSError as exc2:
                logger.warning(
                    "FE_GET_PROPERTY de estadísticas falló: %s",
                    translate_oserror(exc2, "FE_GET_PROPERTY", self.device_path),
                )
                return None
        return {cmd: parse_stats_entries(buffer, index) for index, cmd in enumerate(commands)}

    def read_stats(self) -> SignalStats:
        """FE_GET_PROPERTY de las estadísticas DTV_STAT_* + FE_READ_STATUS."""
        timestamp = time.time()
        try:
            flags = self.read_status()
        except FrontendError as exc:
            return SignalStats.empty(str(exc))

        values = self._read_stats_values()
        if values is None:
            return SignalStats.empty(
                f"No se pudieron leer las estadísticas de {self.device_path}"
            )

        strength = values.get(DTV_STAT_SIGNAL_STRENGTH)
        signal_percent: float | None = None
        signal_dbm: float | None = None
        # La TBS 6904se devuelve 2 entradas en SIGNAL_STRENGTH: [0] en dBm y [1]
        # relativa (es lo que dvb-fe-tool imprime como "-51,7dBm" y "80,08%").
        strength = values.get(DTV_STAT_SIGNAL_STRENGTH) or []
        for scale, unsigned, signed in strength:
            if scale == FE_SCALE_DECIBEL and signal_dbm is None:
                signal_dbm = round(signed / 1000.0, 1)
            elif scale in (FE_SCALE_RELATIVE, FE_SCALE_COUNTER) and signal_percent is None:
                signal_percent = round(100.0 * unsigned / 65535.0, 1)

        cnr = values.get(DTV_STAT_CNR) or []
        snr_db: float | None = None
        cnr_raw: float | None = None
        for scale, unsigned, signed in cnr:
            if scale == FE_SCALE_DECIBEL and snr_db is None:
                cnr_raw = signed / 1000.0
                snr_db = round(cnr_raw, 2)
            elif scale == FE_SCALE_RELATIVE and snr_db is None:
                cnr_raw = float(unsigned)
                snr_db = round(100.0 * unsigned / 65535.0 * 0.2, 2)  # 0..20 dB aprox.

        pre_err = _first(values.get(DTV_STAT_PRE_ERROR_BIT_COUNT))
        pre_total = _first(values.get(DTV_STAT_PRE_TOTAL_BIT_COUNT))
        post_err = _first(values.get(DTV_STAT_POST_ERROR_BIT_COUNT))
        post_total = _first(values.get(DTV_STAT_POST_TOTAL_BIT_COUNT))
        blocks = _first(values.get(DTV_STAT_ERROR_BLOCK_COUNT))
        total_blocks = _first(values.get(DTV_STAT_TOTAL_BLOCK_COUNT))

        ber_pre = _ratio(pre_err, pre_total)
        ber = _ratio(post_err, post_total)
        per = _ratio(blocks, total_blocks)
        ucblocks = None
        if blocks is not None and blocks[0] == FE_SCALE_COUNTER:
            ucblocks = int(blocks[1])

        quality = _quality_percent(snr_db, ber, signal_percent)
        stats = SignalStats(
            timestamp=timestamp,
            lock=flags,
            signal_percent=signal_percent,
            signal_dbm=signal_dbm,
            snr_db=snr_db,
            cnr_raw=cnr_raw,
            ber=ber,
            ber_pre=ber_pre,
            per=per,
            ucblocks=ucblocks,
            quality_percent=quality,
            error=None,
        )
        self._last_stats = stats
        return stats

    # -- conmutación -------------------------------------------------------
    def set_tone(self, on: bool) -> None:
        """FE_SET_TONE (SEC_TONE_ON=0 / SEC_TONE_OFF=1)."""
        self._ioctl(FE_SET_TONE, SEC_TONE_ON if on else SEC_TONE_OFF)

    def set_voltage(self, volts: int) -> None:
        """FE_SET_VOLTAGE: 13 | 18 | 0 (OFF)."""
        volts = int(volts)
        code = {13: SEC_VOLTAGE_13, 18: SEC_VOLTAGE_18, 0: SEC_VOLTAGE_OFF}.get(volts)
        if code is None:
            raise UnsupportedError(f"Voltaje no válido: {volts} V (usa 13, 18 o 0)")
        self._ioctl(FE_SET_VOLTAGE, code)

    def set_tone_voltage(self, tone: bool, volts: int) -> None:
        """Voltaje y tono con las propiedades DVBv5 (DTV_VOLTAGE/DTV_TONE)."""
        props = [
            pack_property_u32(DTV_VOLTAGE, _SEC_VOLTAGE_CODE[int(volts)]),
            pack_property_u32(DTV_TONE, SEC_TONE_ON if tone else SEC_TONE_OFF),
        ]
        self._ioctl(FE_SET_PROPERTY, pack_properties(props))

    def send_diseqc(self, cmd: bytes, repeats: int = 0) -> None:
        """FE_DISEQC_SEND_MASTER_CMD, repetido `repeats + 1` veces.

        Acepta un mensaje de 3..6 bytes o una secuencia (p. ej. DiSEqC 1.1 =
        committed + uncommitted, 8 bytes); la secuencia se envía en llamadas
        separadas porque el kernel sólo admite 3..6 bytes por ioctl.
        """
        for message in split_diseqc_messages(bytes(cmd)):
            payload = _DISEQC_MASTER_FMT.pack(message.ljust(6, b"\0"), len(message))
            for _ in range(max(1, int(repeats) + 1)):
                self._ioctl(FE_DISEQC_SEND_MASTER_CMD, payload)
                if repeats:
                    time.sleep(0.05)

    def send_tone_burst(self, mini: str = "A") -> None:
        """Mini-DiSEqC (tone burst) para conmutadores de 2 posiciones."""
        code = SEC_MINI_A if str(mini).strip().upper().startswith("A") else SEC_MINI_B
        self._ioctl(FE_DISEQC_SEND_BURST, code)

    def diseqc_reset_overload(self) -> None:
        """FE_DISEQC_RESET_OVERLOAD."""
        self._ioctl(FE_DISEQC_RESET_OVERLOAD, 0)

    # -- lectura del TS (demux) -------------------------------------------
    def set_pid_list(self, pids: Sequence[int]) -> None:
        """Configura filtros PES/TS_TAP para los PIDs indicados.

        Cada PID usa un descriptor propio sobre `demux0` con
        `DMX_SET_PES_FILTER(output=DMX_OUT_TAP)`, de forma que ``read_ts()``
        recibe los paquetes TS completos de ese PID (188 bytes, con 0x47).
        """
        wanted = sorted({int(pid) for pid in pids if 0 <= int(pid) <= 0x1FFF})
        if wanted == self._pid_list and self._demux_fds:
            return
        self._close_demux()
        if not wanted:
            self._pid_list = []
            return
        for pid in wanted:
            try:
                fd = os.open(self.demux_path, os.O_RDWR | os.O_NONBLOCK)
            except OSError as exc:
                self._close_demux()
                raise translate_oserror(exc, "open(demux)", self.demux_path) from exc
            try:
                _ioctl(fd, DMX_SET_BUFFER_SIZE, 1 << 18)
                params = _PES_FILTER_FMT.pack(
                    pid, DMX_IN_FRONTEND, DMX_OUT_TAP, DMX_PES_OTHER, 0
                )
                _ioctl(fd, DMX_SET_PES_FILTER, params)
                _ioctl(fd, DMX_START, 0)
            except OSError as exc:
                os.close(fd)
                self._close_demux()
                raise translate_oserror(exc, f"DMX_SET_PES_FILTER(pid={pid})", self.demux_path) from exc
            self._demux_fds[pid] = fd
        self._pid_list = wanted

    def read_ts(self, timeout: float = 0.2, max_bytes: int = 188 * 64) -> bytes:
        """Lee paquetes TS de los PIDs configurados (no bloqueante)."""
        if not self._demux_fds:
            return b""
        max_bytes = max(188, int(max_bytes))
        try:
            ready, _, _ = select.select(list(self._demux_fds.values()), [], [], max(0.0, float(timeout)))
        except (OSError, ValueError):  # pragma: no cover - fd cerrado por otro hilo
            return b""
        chunks: list[bytes] = []
        remaining = max_bytes
        for fd in ready:
            if remaining <= 0:
                break
            try:
                data = os.read(fd, min(65536, remaining))
            except BlockingIOError:
                continue
            except OSError as exc:
                logger.debug("read_ts: error leyendo del demux: %s", exc)
                continue
            if data:
                chunks.append(data)
                remaining -= len(data)
        return b"".join(chunks)

    def _close_demux(self) -> None:
        for fd in self._demux_fds.values():
            try:
                _ioctl(fd, DMX_STOP, 0)
            except OSError:
                pass
            try:
                os.close(fd)
            except OSError:  # pragma: no cover
                pass
        self._demux_fds.clear()
        self._pid_list = []


# ---------------------------------------------------------------------------
# Conversión de estadísticas
# ---------------------------------------------------------------------------
def decode_status(value: int) -> LockFlags:
    """Bits de `fe_status_t` -> `LockFlags`."""
    return LockFlags(
        signal=bool(value & FE_HAS_SIGNAL),
        carrier=bool(value & FE_HAS_CARRIER),
        viterbi=bool(value & FE_HAS_VITERBI),
        sync=bool(value & FE_HAS_SYNC),
        lock=bool(value & FE_HAS_LOCK),
        raw=int(value),
    )


def _first(entries: list[tuple[int, int, int]] | None) -> tuple[int, int, int] | None:
    """Primera entrada de una estadística (o ``None``)."""
    return entries[0] if entries else None


def _ratio(error_stat: tuple[int, int, int] | None, total_stat: tuple[int, int, int] | None) -> float | None:
    """BER/PER a partir de error/total (None si no hay datos)."""
    if error_stat is None or total_stat is None:
        return None
    if error_stat[0] == FE_SCALE_NOT_AVAILABLE or total_stat[0] == FE_SCALE_NOT_AVAILABLE:
        return None
    error = error_stat[1]
    total = total_stat[1]
    if total <= 0:
        return 0.0 if error == 0 else None
    return error / total


def _quality_percent(
    snr_db: float | None, ber: float | None, signal_percent: float | None
) -> float | None:
    """Calidad derivada (0..100).

    Heurística documentada: 15 dB de CNR equivalen a 100 %; el BER penaliza de
    forma multiplicativa. Si no hay CNR se usa la señal relativa.
    """
    if snr_db is not None:
        quality = 100.0 * min(1.0, max(0.0, snr_db / 15.0))
    elif signal_percent is not None:
        quality = float(signal_percent)
    else:
        return None
    if ber is not None and ber > 0:
        quality *= max(0.0, 1.0 - min(1.0, ber * 10.0))
    return round(max(0.0, min(100.0, quality)), 1)
