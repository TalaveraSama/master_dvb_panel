#!/usr/bin/env python3
"""Tercer enfoque: estructuras ctypes explícitas + punteros independientes.

Hipótesis a comprobar:
  C) struct dtv_properties con ptr -> array en memoria independiente (dos buffers).
  D) struct dtv_properties con ptr -> array dentro del mismo buffer (como dvbv5).
Se comparan leyendo FE_GET_PROPERTY(api_version) y FE_SET_PROPERTY(DELIVERY_SYSTEM).

Uso: python ioctl-ctypes.py /dev/dvb/adapter1/frontend0
"""

from __future__ import annotations

import ctypes
import errno as errno_mod
import fcntl
import os
import sys
import time

FE_SET_PROPERTY = 0x40106F52
FE_GET_PROPERTY = 0x80106F53


class DtvPropertyUnion(ctypes.Union):
    _fields_ = [
        ("data", ctypes.c_uint32),
        ("buffer", ctypes.c_uint8 * 32),
    ]


class DtvProperty(ctypes.Structure):
    _fields_ = [
        ("cmd", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 3),
        ("u", DtvPropertyUnion),
    ]


class DtvProperties(ctypes.Structure):
    _fields_ = [
        ("num", ctypes.c_uint32),
        ("props", ctypes.POINTER(DtvProperty)),
    ]


print("sizeof(dtv_property) =", ctypes.sizeof(DtvProperty), "(esperado 48)")
print("sizeof(dtv_properties) =", ctypes.sizeof(DtvProperties), "(esperado 16)")


def attempt(label: str, fn) -> None:
    try:
        fn()
        print(f"[OK  ] {label}")
        return True
    except OSError as exc:
        print(f"[FAIL] {label} -> errno={exc.errno} {errno_mod.errorcode.get(exc.errno or 0, '?')} {exc}")
        return False


def main() -> int:
    dev = sys.argv[1] if len(sys.argv) > 1 else "/dev/dvb/adapter1/frontend0"
    fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
    print("fd:", fd, dev)

    # --- C) punteros independientes ---
    prop = DtvProperty()
    props = DtvProperties()
    props.num = 1
    props.props = ctypes.pointer(prop)
    prop.cmd = 42  # DTV_API_VERSION
    prop.u.data = 0
    attempt("C) GET api_version (puntero independiente)", lambda: fcntl.ioctl(fd, FE_GET_PROPERTY, props, True))
    print("      num =", props.num, "data =", prop.u.data, "buffer[0..3] =", list(prop.u.buffer[:4]))

    prop.cmd = 50  # DTV_ENUM_DELSYS
    prop.u.data = 0
    attempt("C) GET enum_delsys", lambda: fcntl.ioctl(fd, FE_GET_PROPERTY, props, True))
    print("      num =", props.num, "data =", prop.u.data, "buffer =", list(prop.u.buffer[:12]))

    # SET del delivery system con la misma técnica
    prop.cmd = 1  # DTV_DELIVERY_SYSTEM
    prop.u.data = 6  # DVBS2
    attempt("C) SET delivery_system=DVBS2", lambda: fcntl.ioctl(fd, FE_SET_PROPERTY, props, True))

    # --- D) array dentro del mismo buffer ---
    array = (DtvProperty * 1)()
    array[0].cmd = 42
    array[0].u.data = 0
    container = ctypes.create_string_buffer(ctypes.sizeof(DtvProperties) + ctypes.sizeof(DtvProperty))
    header = DtvProperties.from_buffer(container)
    header.num = 1
    header.props = ctypes.cast(ctypes.addressof(container) + ctypes.sizeof(DtvProperties), ctypes.POINTER(DtvProperty))
    ctypes.memmove(ctypes.addressof(container) + ctypes.sizeof(DtvProperties), ctypes.addressof(array), ctypes.sizeof(DtvProperty))
    attempt("D) GET api_version (array en el buffer)", lambda: fcntl.ioctl(fd, FE_GET_PROPERTY, container, True))
    after = DtvProperty.from_buffer(container, ctypes.sizeof(DtvProperties))
    print("      data =", after.u.data, "num =", header.num)

    # Estado del frontend
    time.sleep(0.2)
    status = bytearray(4)
    fcntl.ioctl(fd, 0x80046F46, status, True)
    print("status:", hex(int.from_bytes(status, "little")))

    os.close(fd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
