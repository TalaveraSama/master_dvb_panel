#!/usr/bin/env python3
"""Diagnóstico de bajo nivel de FE_SET_PROPERTY sobre la tarjeta real."""

from __future__ import annotations

import ctypes
import errno as errno_mod
import fcntl
import os
import struct
import sys
import time

DEV = sys.argv[1] if len(sys.argv) > 1 else "/dev/dvb/adapter0/frontend0"
FREQ_KHZ = int(sys.argv[2]) if len(sys.argv) > 2 else 1346000
SR = int(sys.argv[3]) if len(sys.argv) > 3 else 27500

FE_SET_PROPERTY = 0x40106F52
FE_GET_PROPERTY = 0x80106F53
DTV_UNDEFINED, DTV_TUNE, DTV_CLEAR = 0, 0, 0
DTV_DELIVERY_SYSTEM, DTV_FREQUENCY, DTV_MODULATION = 1, 2, 3
DTV_INVERSION, DTV_SYMBOL_RATE, DTV_INNER_FEC = 5, 11, 12
DTV_ROLLOFF, DTV_PILOT = 16, 15

SYS_DVBS, SYS_DVBS2 = 5, 6
QPSK, PSK_8 = 0, 5
FEC_AUTO = 9
ROLLOFF_35 = 3
PILOT_AUTO = 2
INVERSION_AUTO = 2

PROP_SIZE = 4 + 12 + 32  # cmd + reserved[3] + union


def pack_props(props: list[tuple[int, int]]) -> bytearray:
    buf = bytearray(PROP_SIZE * len(props) + 8)
    struct.pack_into("<I", buf, 0, len(props))
    struct.pack_into("<Q", buf, 4 + 4, 0)  # puntero (fcntl lo reescribe)
    offset = 8
    for cmd, value in props:
        struct.pack_into("<I", buf, offset, cmd)
        struct.pack_into("<I", buf, offset + 16, value)
        offset += PROP_SIZE
    return buf


def main() -> int:
    ctypes.CDLL("libc.so.6", use_errno=True)  # asegura get_errno utilizable
    print(f"dispositivo: {DEV}")
    fd = os.open(DEV, os.O_RDWR | os.O_NONBLOCK)
    print("open(O_RDWR) OK, fd:", fd)

    # ¿Hay otro proceso con el dispositivo abierto?
    print("\n--- procesos con el frontend abierto ---")
    os.system("sudo -n fuser -v " + DEV + " 2>&1 || fuser -v " + DEV + " 2>&1 | head -5")

    cases = {
        "DTV_CLEAR solo": [(DTV_CLEAR, 0)],
        "DTV_API_VERSION": [(42, 0)],
        "DELIVERY_SYSTEM+DVBS2": [(DTV_CLEAR, 0), (DTV_DELIVERY_SYSTEM, SYS_DVBS2)],
        "FREQ+SR+MOD (DVB-S2)": [
            (DTV_CLEAR, 0),
            (DTV_DELIVERY_SYSTEM, SYS_DVBS2),
            (DTV_FREQUENCY, FREQ_KHZ * 1000),
            (DTV_SYMBOL_RATE, SR * 1000),
            (DTV_MODULATION, PSK_8),
            (DTV_INNER_FEC, FEC_AUTO),
            (DTV_INVERSION, INVERSION_AUTO),
            (DTV_ROLLOFF, ROLLOFF_35),
            (DTV_PILOT, PILOT_AUTO),
            (DTV_TUNE, 0),
        ],
        "FREQ solo + TUNE": [
            (DTV_CLEAR, 0),
            (DTV_DELIVERY_SYSTEM, SYS_DVBS2),
            (DTV_FREQUENCY, FREQ_KHZ * 1000),
            (DTV_TUNE, 0),
        ],
    }

    for label, props in cases.items():
        ctypes.set_errno(0)
        buf = pack_props(props)
        try:
            fcntl.ioctl(fd, FE_SET_PROPERTY, buf, True)
            print(f"[OK  ] {label}: {len(props)} props")
        except OSError as exc:
            c_errno = ctypes.get_errno()
            print(
                f"[FAIL] {label}: errno={exc.errno} ({errno_mod.errorcode.get(exc.errno or 0, '?')}) "
                f"strerror={exc.strerror!r} c_errno={c_errno} "
                f"({errno_mod.errorcode.get(c_errno, '?')})"
            )
        time.sleep(0.3)

    # Estado tras el intento
    FE_READ_STATUS = 0x80046F46
    status = bytearray(4)
    try:
        fcntl.ioctl(fd, FE_READ_STATUS, status, True)
        print("status raw:", struct.unpack("<I", status)[0], "->", bin(struct.unpack("<I", status)[0]))
    except OSError as exc:
        print("FE_READ_STATUS falló:", exc)

    os.close(fd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
