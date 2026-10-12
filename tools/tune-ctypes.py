#!/usr/bin/env python3
"""Sintonización real (enfoque ctypes con puntero independiente) por adapter.

Envía un FE_SET_PROPERTY por propiedad y comprueba FE_READ_STATUS. Se puede
forzar la frecuencia/IF. Uso:

    python tune-ctypes.py 2 1346000 27500 [--clear]
"""

from __future__ import annotations

import ctypes
import errno as errno_mod
import fcntl
import os
import struct
import subprocess
import sys
import time

FE_SET_PROPERTY = 0x40106F52
FE_READ_STATUS = 0x80046F46


class U(ctypes.Union):
    _fields_ = [("data", ctypes.c_uint32), ("buffer", ctypes.c_uint8 * 32)]


class P(ctypes.Structure):
    _fields_ = [("cmd", ctypes.c_uint32), ("reserved", ctypes.c_uint32 * 3), ("u", U)]


class Props(ctypes.Structure):
    _fields_ = [("num", ctypes.c_uint32), ("props", ctypes.POINTER(P))]


DTV_TUNE, DTV_CLEAR = 0, 0
DTV_DELIVERY_SYSTEM, DTV_FREQUENCY, DTV_MODULATION, DTV_INVERSION = 1, 2, 3, 5
DTV_SYMBOL_RATE, DTV_INNER_FEC, DTV_ROLLOFF, DTV_PILOT = 11, 12, 16, 15
NAMES = {1: "signal", 2: "carrier", 4: "viterbi", 8: "sync", 16: "lock"}


def busy(adapter: int) -> str:
    try:
        out = subprocess.run(
            ["fuser", "-v", f"/dev/dvb/adapter{adapter}/frontend0"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stderr
        return out.strip().replace("\n", " ")
    except Exception:
        return ""


def set_prop(fd: int, prop: P, props: Props, cmd: int, value: int, label: str) -> bool:
    prop.cmd = cmd
    prop.u.data = value
    try:
        fcntl.ioctl(fd, FE_SET_PROPERTY, props, True)
        return True
    except OSError as exc:
        print(f"    [FAIL] {label} -> errno={exc.errno} {errno_mod.errorcode.get(exc.errno or 0, '?')}")
        return False


def main() -> int:
    adapter = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    freq_khz = int(sys.argv[2]) if len(sys.argv) > 2 else 1346000
    sr_ksps = int(sys.argv[3]) if len(sys.argv) > 3 else 27500
    path = f"/dev/dvb/adapter{adapter}/frontend0"
    print(f"adapter{adapter} ({path}) — procesos que lo usan: {busy(adapter) or 'ninguno'}")

    fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    prop = P()
    props = Props()
    props.num = 1
    props.props = ctypes.pointer(prop)

    def status() -> int:
        raw = bytearray(4)
        fcntl.ioctl(fd, FE_READ_STATUS, raw, True)
        return struct.unpack("<I", raw)[0]

    print("status inicial:", hex(status()))
    steps = [
        (DTV_CLEAR, 0, "CLEAR"),
        (DTV_DELIVERY_SYSTEM, 6, "DELIVERY_SYSTEM=DVBS2"),
        (DTV_FREQUENCY, freq_khz * 1000, "FREQUENCY"),
        (DTV_SYMBOL_RATE, sr_ksps * 1000, "SYMBOL_RATE"),
        (DTV_MODULATION, 5, "MODULATION=8PSK"),
        (DTV_INNER_FEC, 9, "INNER_FEC=AUTO"),
        (DTV_INVERSION, 2, "INVERSION=AUTO"),
        (DTV_ROLLOFF, 3, "ROLLOFF=0.35"),
        (DTV_PILOT, 2, "PILOT=AUTO"),
    ]
    for cmd, value, label in steps:
        ok = set_prop(fd, prop, props, cmd, value, label)
        print(f"    [{'OK  ' if ok else 'FAIL'}] {label}")

    print("\n--- TUNE ---")
    # Con DVBv5, DTV_TUNE se envía tras fijar los parámetros (sin DTV_CLEAR junto)
    if set_prop(fd, prop, props, DTV_TUNE, 0, "TUNE"):
        print("    [OK  ] TUNE")

    print("\n--- LOCK ---")
    for i in range(20):
        st = status()
        flags = [n for bit, n in NAMES.items() if st & bit]
        print(f"  t={i * 0.5:5.1f}s status=0x{st:02x} {' '.join(flags) or '-'}")
        if st & 16:
            print("  => LOCK ✔")
            break
        time.sleep(0.5)
    os.close(fd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
