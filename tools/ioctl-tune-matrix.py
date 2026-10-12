#!/usr/bin/env python3
"""Aísla qué propiedad DTV_* provoca EINVAL en FE_SET_PROPERTY.

Usa buffer ctypes (la única forma que evita EFAULT) y prueba secuencias
crecientes de propiedades, mostrando el errno real de cada paso.

Uso: python ioctl-tune-matrix.py /dev/dvb/adapter0/frontend0
"""

from __future__ import annotations

import ctypes
import errno as errno_mod
import fcntl
import os
import struct
import sys

FE_SET_PROPERTY = 0x40106F52
FE_GET_PROPERTY = 0x80106F53
PROP_SIZE = 48
HEADER_SIZE = 16

DTV_UNDEFINED = 0
DTV_TUNE = 0
DTV_CLEAR = 0
DTV_DELIVERY_SYSTEM = 1
DTV_FREQUENCY = 2
DTV_MODULATION = 3
DTV_INVERSION = 5
DTV_SYMBOL_RATE = 11
DTV_INNER_FEC = 12
DTV_ROLLOFF = 16
DTV_PILOT = 15
DTV_API_VERSION = 42
DTV_ENUM_DELSYS = 50

FREQ_HZ = 1346000000
SR = 27500000

cases: list[tuple[str, list[tuple[int, int]]]] = [
    ("GET api_version", "GET"),
    ("GET enum_delsys", "GET"),
    ("SET [CLEAR]", [(DTV_CLEAR, 0)]),
    ("SET [CLEAR, TUNE]", [(DTV_CLEAR, 0), (DTV_TUNE, 0)]),
    ("SET [DELIVERY_SYSTEM=DVBS2]", [(DTV_DELIVERY_SYSTEM, 6)]),
    ("SET [FREQ]", [(DTV_FREQUENCY, FREQ_HZ)]),
    ("SET [DELIVERY,FREQ]", [(DTV_DELIVERY_SYSTEM, 6), (DTV_FREQUENCY, FREQ_HZ)]),
    ("SET [CLEAR,DELIVERY,FREQ,TUNE]", [(DTV_CLEAR, 0), (DTV_DELIVERY_SYSTEM, 6), (DTV_FREQUENCY, FREQ_HZ), (DTV_TUNE, 0)]),
    (
        "SET completo DVB-S2",
        [
            (DTV_CLEAR, 0),
            (DTV_DELIVERY_SYSTEM, 6),
            (DTV_FREQUENCY, FREQ_HZ),
            (DTV_SYMBOL_RATE, SR),
            (DTV_MODULATION, 5),
            (DTV_INNER_FEC, 9),
            (DTV_INVERSION, 2),
            (DTV_ROLLOFF, 3),
            (DTV_PILOT, 2),
            (DTV_TUNE, 0),
        ],
    ),
]


def main() -> int:
    dev = sys.argv[1] if len(sys.argv) > 1 else "/dev/dvb/adapter0/frontend0"
    ctypes.CDLL("libc.so.6", use_errno=True)
    fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
    print("fd:", fd, dev)

    for label, spec in cases:
        buf = ctypes.create_string_buffer(HEADER_SIZE + PROP_SIZE * 12 + 64)
        base = ctypes.addressof(buf)
        if spec == "GET":
            cmd = DTV_API_VERSION if "api" in label else DTV_ENUM_DELSYS
            props = [(cmd, 0)]
            request = FE_GET_PROPERTY
        else:
            props = spec  # type: ignore[assignment]
            request = FE_SET_PROPERTY

        struct.pack_into("<I", buf, 0, len(props))
        struct.pack_into("<Q", buf, 8, base + HEADER_SIZE)
        offset = HEADER_SIZE
        for cmd_value, value in props:
            struct.pack_into("<I", buf, offset, cmd_value)
            struct.pack_into("<I", buf, offset + 16, value)
            offset += PROP_SIZE

        ctypes.set_errno(0)
        try:
            fcntl.ioctl(fd, request, buf, True)
            print(f"[OK  ] {label}")
            if request == FE_GET_PROPERTY:
                data = struct.unpack_from("<I", buf.raw, HEADER_SIZE + 16)[0]
                print(f"        data={data} (0x{data:x})")
        except OSError as exc:
            code = exc.errno or 0
            print(f"[FAIL] {label} -> errno={code} {errno_mod.errorcode.get(code, '?')} {exc}")
    os.close(fd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
