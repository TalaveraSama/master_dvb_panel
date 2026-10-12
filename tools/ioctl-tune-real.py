#!/usr/bin/env python3
"""Secuencia de sintonización REAL con buffer ctypes: prueba y verifica LOCK.

Estrategia: enviar un único FE_SET_PROPERTY por propiedad (el kernel acepta una
propiedad por ioctl) y leer FE_READ_STATUS + FE_GET_PROPERTY de estadísticas.

Uso: python ioctl-tune-real.py /dev/dvb/adapter1/frontend0 1346000 27500
"""

from __future__ import annotations

import ctypes
import errno as errno_mod
import fcntl
import os
import struct
import sys
import time

FE_SET_PROPERTY = 0x40106F52
FE_GET_PROPERTY = 0x80106F53
FE_READ_STATUS = 0x80046F46

PROP_SIZE = 48
HEADER_SIZE = 16
BUFSIZE = HEADER_SIZE + PROP_SIZE + 64

DTV_TUNE = 0
DTV_CLEAR = 0
DTV_DELIVERY_SYSTEM = 1
DTV_FREQUENCY = 2
DTV_MODULATION = 3
DTV_SYMBOL_RATE = 11
DTV_INNER_FEC = 12
DTV_INVERSION = 5
DTV_ROLLOFF = 16
DTV_PILOT = 15
DTV_STAT_SIGNAL_STRENGTH = 18
DTV_STAT_CNR = 19
DTV_STAT_POST_ERROR_BIT_COUNT = 22
DTV_STAT_POST_TOTAL_BIT_COUNT = 23

FE_HAS_SIGNAL, FE_HAS_CARRIER, FE_HAS_VITERBI, FE_HAS_SYNC, FE_HAS_LOCK = 1, 2, 4, 8, 16


def make_buf() -> tuple:
    buf = ctypes.create_string_buffer(BUFSIZE)
    return buf, ctypes.addressof(buf)


def set_one(fd: int, buf, base: int, cmd: int, value: int, label: str) -> bool:
    struct.pack_into("<I", buf, 0, 1)
    struct.pack_into("<Q", buf, 8, base + HEADER_SIZE)
    struct.pack_into("<I", buf, HEADER_SIZE, cmd)
    struct.pack_into("<I", buf, HEADER_SIZE + 16, value)
    try:
        fcntl.ioctl(fd, FE_SET_PROPERTY, buf, True)
        print(f"[OK  ] SET {label} = {value}")
        return True
    except OSError as exc:
        code = exc.errno or 0
        print(f"[FAIL] SET {label} = {value} -> errno={code} {errno_mod.errorcode.get(code, '?')}")
        return False


def get_prop(fd: int, buf, base: int, cmd: int) -> tuple[int, int, int]:
    """Devuelve (len, data_u32, len_bytes_buffer)."""
    struct.pack_into("<I", buf, 0, 1)
    struct.pack_into("<Q", buf, 8, base + HEADER_SIZE)
    struct.pack_into("<I", buf, HEADER_SIZE, cmd)
    struct.pack_into("<I", buf, HEADER_SIZE + 16, 0)
    struct.pack_into("<I", buf, HEADER_SIZE + 4, 0)  # len = 0
    try:
        fcntl.ioctl(fd, FE_GET_PROPERTY, buf, True)
        size = struct.unpack_from("<I", buf, HEADER_SIZE + 4)[0]
        data = struct.unpack_from("<I", buf, HEADER_SIZE + 16)[0]
        signed = struct.unpack_from("<i", buf, HEADER_SIZE + 16)[0]
        return size, data, signed
    except OSError as exc:
        code = exc.errno or 0
        print(f"      GET {cmd} falló: errno={code} {errno_mod.errorcode.get(code, '?')}")
        return -1, -1, -1


def read_status(fd: int) -> int:
    raw = bytearray(4)
    fcntl.ioctl(fd, FE_READ_STATUS, raw, True)
    return struct.unpack("<I", raw)[0]


def main() -> int:
    dev = sys.argv[1] if len(sys.argv) > 1 else "/dev/dvb/adapter1/frontend0"
    freq = int(sys.argv[2]) if len(sys.argv) > 2 else 1346000
    sr = int(sys.argv[3]) if len(sys.argv) > 3 else 27500
    ctypes.CDLL("libc.so.6", use_errno=True)
    fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
    buf, base = make_buf()
    print("fd:", fd, dev, "freq:", freq, "kHz")

    print("\n--- GET iniciales ---")
    for cmd, label in (
        (42, "DTV_API_VERSION"),
        (50, "DTV_ENUM_DELSYS"),
        (DTV_STAT_SIGNAL_STRENGTH, "SIGNAL_STRENGTH"),
        (DTV_STAT_CNR, "CNR"),
    ):
        size, data, signed = get_prop(fd, buf, base, cmd)
        print(f"      {label}: len={size} u32={data} i32={signed}")

    print("\n--- SET una a una (sin DTV_CLEAR) ---")
    set_one(fd, buf, base, DTV_DELIVERY_SYSTEM, 6, "DELIVERY_SYSTEM=DVBS2")
    set_one(fd, buf, base, DTV_FREQUENCY, freq * 1000, "FREQUENCY")
    set_one(fd, buf, base, DTV_SYMBOL_RATE, sr * 1000, "SYMBOL_RATE")
    set_one(fd, buf, base, DTV_MODULATION, 5, "MODULATION=8PSK")
    set_one(fd, buf, base, DTV_INNER_FEC, 9, "INNER_FEC=AUTO")
    set_one(fd, buf, base, DTV_INVERSION, 2, "INVERSION=AUTO")
    set_one(fd, buf, base, DTV_ROLLOFF, 3, "ROLLOFF=0.35")
    set_one(fd, buf, base, DTV_PILOT, 2, "PILOT=AUTO")
    ok = set_one(fd, buf, base, DTV_TUNE, 0, "TUNE")

    print("\n--- esperando LOCK ---")
    names = ["signal", "carrier", "viterbi", "sync", "lock"]
    for index in range(12):
        status = read_status(fd)
        flags = " ".join(n for bit, n in zip((1, 2, 4, 8, 16), names) if status & bit)
        size_s, data_s, signed_s = get_prop(fd, buf, base, DTV_STAT_SIGNAL_STRENGTH)
        size_c, data_c, signed_c = get_prop(fd, buf, base, DTV_STAT_CNR)
        print(
            f"  t={index * 0.5:4.1f}s status=0x{status:02x} [{flags or '-'}] "
            f"strength(len={size_s}, i32={signed_s}, u32={data_s}) cnr(i32={signed_c})"
        )
        if status & FE_HAS_LOCK:
            print("  => LOCK")
            break
        time.sleep(0.5)

    print("\n--- GET finales ---")
    for cmd, label in (
        (DTV_STAT_SIGNAL_STRENGTH, "SIGNAL_STRENGTH"),
        (DTV_STAT_CNR, "CNR"),
        (DTV_STAT_POST_ERROR_BIT_COUNT, "POST_ERROR_BIT_COUNT"),
        (DTV_STAT_POST_TOTAL_BIT_COUNT, "POST_TOTAL_BIT_COUNT"),
    ):
        size, data, signed = get_prop(fd, buf, base, cmd)
        print(f"      {label}: len={size} u32={data} i32={signed}")

    os.close(fd)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
