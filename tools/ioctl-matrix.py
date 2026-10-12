#!/usr/bin/env python3
"""Diagnóstico fino de FE_SET_PROPERTY/FE_GET_PROPERTY (layout + mutate).

Prueba la matriz {mutate True/False} x {buffer corto/largo} con el layout
correcto de `struct dtv_properties` (16 B) + N x `struct dtv_property` (48 B),
y muestra el errno REAL de cada intento.

Uso: python ioctl-matrix.py /dev/dvb/adapter1/frontend0
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
FE_READ_STATUS = 0x80046F46

PROP_SIZE = 48
HEADER_SIZE = 16


def pack(props: list[tuple[int, int]], pad_to: int = 0) -> bytearray:
    total = HEADER_SIZE + PROP_SIZE * len(props)
    total = max(total, pad_to)
    buf = bytearray(total)
    struct.pack_into("<I", buf, 0, len(props))  # num
    struct.pack_into("<Q", buf, 8, HEADER_SIZE)  # puntero relativo al inicio
    offset = HEADER_SIZE
    for cmd, value in props:
        struct.pack_into("<I", buf, offset, cmd)
        struct.pack_into("<I", buf, offset + 16, value)
        offset += PROP_SIZE
    return buf


def try_ioctl(fd: int, request: int, buf: bytearray, mutate: bool, label: str) -> None:
    ctypes.set_errno(0)
    try:
        fcntl.ioctl(fd, request, buf, mutate)
        print(f"[OK  ] {label:52} len={len(buf):5} mutate={mutate}")
    except (OSError, ValueError) as exc:
        code = getattr(exc, "errno", None)
        name = errno_mod.errorcode.get(code or 0, "?")
        print(
            f"[FAIL] {label:52} len={len(buf):5} mutate={mutate} "
            f"-> {type(exc).__name__}: errno={code} {name} {exc}"
        )


def main() -> int:
    dev = sys.argv[1] if len(sys.argv) > 1 else "/dev/dvb/adapter1/frontend0"
    ctypes.CDLL("libc.so.6", use_errno=True)
    fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
    print("fd:", fd, "dispositivo:", dev)

    # 1) GET corto (4 B, cabe en "int"): mutate True/False
    for mutate in (True, False):
        buf = bytearray(4)
        try_ioctl(fd, FE_READ_STATUS, buf, mutate, "FE_READ_STATUS (4 B)")
    print("   status bytes:", buf.hex(), "->", struct.unpack("<I", buf)[0] if len(buf) >= 4 else "?")

    # 2) GET_PROPERTY con 1 propiedad (64 B) y con 8 propiedades (400 B)
    for count in (1, 2, 8):
        for mutate in (True, False):
            props = [(42, 0)] * count  # DTV_API_VERSION repetido
            buf = pack(props)
            try_ioctl(fd, FE_GET_PROPERTY, buf, mutate, f"FE_GET_PROPERTY {count} props")
            if len(buf) >= HEADER_SIZE + 16:
                print(f"        valor[0]: {struct.unpack_from('<I', buf, HEADER_SIZE + 16)[0]}")

    # 3) SET_PROPERTY: DTV_CLEAR (1 prop) y lista larga + relleno a 1032 B
    for label, props, pad in (
        ("SET DTV_CLEAR (1 prop)", [(0, 0)], 0),
        ("SET DTV_CLEAR (1 prop, pad 1032)", [(0, 0)], 1032),
        (
            "SET tune DVB-S2 (10 props)",
            [(0, 0), (1, 6), (2, 1346000000), (11, 27500000), (3, 5), (12, 9)],
            0,
        ),
        (
            "SET tune DVB-S2 (10 props, pad 1032)",
            [(0, 0), (1, 6), (2, 1346000000), (11, 27500000), (3, 5), (12, 9)],
            1032,
        ),
    ):
        for mutate in (True, False):
            buf = pack(props, pad)
            try_ioctl(fd, FE_SET_PROPERTY, buf, mutate, label)

    os.close(fd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
