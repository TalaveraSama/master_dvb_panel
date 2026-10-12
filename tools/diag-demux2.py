#!/usr/bin/env python3
"""Diagnostico del demux usando ctypes (buffer de ioctl correcto)."""

from __future__ import annotations

import ctypes
import os
import struct
import sys
import time

sys.path.insert(0, os.getcwd())

DMX_SET_BUFFER_SIZE = 0x40046F1D
DMX_SET_PES_FILTER = 0x40106F0C
DMX_START = 0x6F1B
DMX_STOP = 0x6F1A

DMX_IN_FRONTEND = 0
DMX_OUT_TAP = 2
DMX_PES_OTHER = 20

DEMUX_PATH = "/dev/dvb/adapter0/demux0"
libc = ctypes.CDLL("libc.so.6", use_errno=True)


class DmxPesFilterParams(ctypes.Structure):
    _fields_ = [
        ("pid", ctypes.c_uint16),
        ("input", ctypes.c_uint16),   # enum dmx_input
        ("output", ctypes.c_uint16),  # enum dmx_output
        ("pes_type", ctypes.c_uint16),
        ("flags", ctypes.c_uint32),
    ]


def read_filter(pid: int, seconds: float, label: str) -> int:
    print(f"\n--- {label}: PID 0x{pid:04X} durante {seconds:.0f} s ---")
    fd = os.open(DEMUX_PATH, os.O_RDWR | os.O_NONBLOCK)
    total = 0
    try:
        if libc.ioctl(fd, DMX_SET_BUFFER_SIZE, ctypes.c_ulong(1 << 18)) < 0:
            print("  DMX_SET_BUFFER_SIZE fallo:", os.strerror(ctypes.get_errno()))

        params = DmxPesFilterParams(
            pid=pid, input=DMX_IN_FRONTEND, output=DMX_OUT_TAP, pes_type=DMX_PES_OTHER, flags=0
        )
        if libc.ioctl(fd, DMX_SET_PES_FILTER, ctypes.byref(params)) < 0:
            print("  DMX_SET_PES_FILTER fallo:", os.strerror(ctypes.get_errno()))
            os.close(fd)
            return 0
        if libc.ioctl(fd, DMX_START, 0) < 0:
            print("  DMX_START fallo:", os.strerror(ctypes.get_errno()))
            os.close(fd)
            return 0

        first = b""
        start = time.monotonic()
        while time.monotonic() - start < seconds:
            try:
                data = os.read(fd, 65536)
            except BlockingIOError:
                time.sleep(0.05)
                continue
            if not data:
                time.sleep(0.05)
                continue
            if not first:
                first = data[:16]
            total += len(data)
        print(f"  bytes={total} paquetes={total // 188}")
        if first:
            print("  primeros bytes:", first.hex())
    finally:
        try:
            libc.ioctl(fd, DMX_STOP, 0)
        except OSError:
            pass
        os.close(fd)
    return total


# El frontend lo tiene sintonizado el servicio del panel (10970 V).
read_filter(0x0000, 4.0, "PAT")
read_filter(0x0011, 2.0, "SDT")
# Sin filtro: PID 0x2000 es el comodin de "todos" en el API antiguo
read_filter(0x2000, 3.0, "TODOS (0x2000)")
