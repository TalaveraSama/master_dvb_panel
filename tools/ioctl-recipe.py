#!/usr/bin/env python3
"""Búsqueda de la forma CORRECTA de llamar a FE_SET_PROPERTY/FE_GET_PROPERTY.

Hipótesis: `fcntl.ioctl` con bytearray no traduce direcciones contenidas en la
estructura (struct dtv_properties lleva un puntero a las propiedades), por eso
el kernel devuelve EFAULT. Se prueban dos alternativas:

  A) buffer `ctypes` (create_string_buffer) -> fcntl.ioctl con objeto buffer
  B) buffer `mmap` anónimo -> fcntl.ioctl con objeto buffer

Uso: python ioctl-recipe.py /dev/dvb/adapter1/frontend0
"""

from __future__ import annotations

import ctypes
import errno as errno_mod
import fcntl
import mmap
import os
import struct
import sys

FE_SET_PROPERTY = 0x40106F52
FE_GET_PROPERTY = 0x80106F53

PROP_SIZE = 48
HEADER_SIZE = 16
DTV_API_VERSION = 42
DTV_CLEAR = 0
DTV_TUNE = 0


def fill(buf, props: list[tuple[int, int]], base: int) -> int:
    """Rellena la estructura y devuelve la dirección ABSOLUTA del array."""
    struct.pack_into("<I", buf, 0, len(props))
    array_addr = base + HEADER_SIZE
    struct.pack_into("<Q", buf, 8, array_addr)
    offset = HEADER_SIZE
    for cmd, value in props:
        struct.pack_into("<I", buf, offset, cmd)
        struct.pack_into("<I", buf, offset + 16, value)
        offset += PROP_SIZE
    return len(props)


def attempt(label: str, fn) -> None:
    ctypes.set_errno(0)
    try:
        fn()
        print(f"[OK  ] {label}")
    except (OSError, ValueError) as exc:
        code = getattr(exc, "errno", None)
        print(f"[FAIL] {label} -> {type(exc).__name__} errno={code} {errno_mod.errorcode.get(code or 0, '?')} {exc}")


def main() -> int:
    dev = sys.argv[1] if len(sys.argv) > 1 else "/dev/dvb/adapter1/frontend0"
    ctypes.CDLL("libc.so.6", use_errno=True)
    fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
    print("fd:", fd, dev)
    total = HEADER_SIZE + PROP_SIZE * 2 + 64

    # --- A) ctypes buffer ---
    cbuf = ctypes.create_string_buffer(total)
    base = ctypes.addressof(cbuf)
    fill(cbuf, [(DTV_API_VERSION, 0)], base)
    attempt("A) ctypes + mutate=True  (GET API_VERSION)", lambda: fcntl.ioctl(fd, FE_GET_PROPERTY, cbuf, True))
    value = struct.unpack_from("<I", cbuf.raw, HEADER_SIZE + 16)[0]
    print(f"        valor[0] = {value} (0x{value:04x})")

    fill(cbuf, [(DTV_API_VERSION, 0)], base)
    attempt("A) ctypes + mutate=False (GET API_VERSION)", lambda: fcntl.ioctl(fd, FE_GET_PROPERTY, cbuf, False))
    value = struct.unpack_from("<I", cbuf.raw, HEADER_SIZE + 16)[0]
    print(f"        valor[0] = {value} (0x{value:04x})")

    fill(cbuf, [(DTV_CLEAR, 0)], base)
    attempt("A) ctypes + mutate=True  (SET DTV_CLEAR)", lambda: fcntl.ioctl(fd, FE_SET_PROPERTY, cbuf, True))
    fill(cbuf, [(DTV_CLEAR, 0), (DTV_TUNE, 0)], base)
    attempt("A) ctypes + mutate=False (SET DTV_CLEAR+TUNE)", lambda: fcntl.ioctl(fd, FE_SET_PROPERTY, cbuf, False))

    # --- B) mmap anónimo ---
    mm = mmap.mmap(-1, total)
    base = ctypes.addressof(ctypes.c_char.from_buffer(mm))
    print(f"        mmap base = 0x{base:x}")
    fill(mm, [(DTV_API_VERSION, 0)], base)
    attempt("B) mmap + mutate=True  (GET API_VERSION)", lambda: fcntl.ioctl(fd, FE_GET_PROPERTY, mm, True))
    value = struct.unpack_from("<I", mm, HEADER_SIZE + 16)[0]
    print(f"        valor[0] = {value} (0x{value:04x})")

    fill(mm, [(DTV_CLEAR, 0)], base)
    attempt("B) mmap + mutate=True  (SET DTV_CLEAR)", lambda: fcntl.ioctl(fd, FE_SET_PROPERTY, mm, True))

    mm.close()
    os.close(fd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
