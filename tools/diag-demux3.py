#!/usr/bin/env python3
"""Prueba el filtro de secciones del API nuevo (DMX_SET_FILTER) sobre el demux."""

from __future__ import annotations

import ctypes
import os
import sys
import time

sys.path.insert(0, os.getcwd())

# Peticiones ioctl del DVB (linux/dvb/dmx.h)
DMX_SET_SOURCE = 0x40046F31          # _IOW('o', 49, enum dmx_source)
DMX_SET_FILTER = 0xC0386F13          # _IOW('o', 19, struct dmx_section_filter)
DMX_SET_BUFFER_SIZE = 0x40046F1D
DMX_START = 0x6F1B
DMX_STOP = 0x6F1A

DMX_SOURCE_FRONT0 = 0
DMX_OUT_TS_TAP = 1
DMX_OUT_TAP = 2

DEMUX = "/dev/dvb/adapter0/demux0"
DVR = "/dev/dvb/adapter0/dvr0"
libc = ctypes.CDLL("libc.so.6", use_errno=True)


def ioctl(fd: int, request: int, arg) -> int:
    return libc.ioctl(fd, ctypes.c_ulong(request), arg)


def try_section_filter(pid: int, seconds: float) -> int:
    """Monto un filtro de seccion con el API nuevo y leo desde dvr0."""
    print(f"\n--- API nuevo: filtro de seccion PID 0x{pid:04X} ({seconds:.0f} s) ---")
    fd = os.open(DEMUX, os.O_RDWR | os.O_NONBLOCK)
    dvr = None
    try:
        # Origen del demux = frontend 0 (necesario en drivers con varios TS)
        ioctl(fd, DMX_SET_SOURCE, ctypes.c_int(DMX_SOURCE_FRONT0))
        ioctl(fd, DMX_SET_BUFFER_SIZE, ctypes.c_ulong(1 << 19))

        # struct dmx_sct_filter_params { u16 pid; dmx_filter_t filter; u32 timeout; u32 flags; }
        class Filter(ctypes.Structure):
            _fields_ = [("filter", ctypes.c_ubyte * 16), ("mask", ctypes.c_ubyte * 16), ("mode", ctypes.c_ubyte * 16)]

        class SctFilterParams(ctypes.Structure):
            _fields_ = [
                ("pid", ctypes.c_uint16),
                ("filter", Filter),
                ("timeout", ctypes.c_uint32),
                ("flags", ctypes.c_uint32),
            ]

        params = SctFilterParams()
        ctypes.memset(ctypes.byref(params), 0, ctypes.sizeof(params))
        params.pid = pid
        params.filter.mask[0] = 0xFF  # cualquier table_id
        params.flags = 0  # DMX_CHECK_CRC desactivado
        if ioctl(fd, DMX_SET_FILTER, ctypes.byref(params)) < 0:
            print("  DMX_SET_FILTER fallo:", os.strerror(ctypes.get_errno()))
            return 0
        ioctl(fd, DMX_START, ctypes.c_int(0))
        print("  filtro montado; leyendo del demux")

        total = 0
        first = b""
        start = time.monotonic()
        while time.monotonic() - start < seconds:
            try:
                data = os.read(fd, 65536)
            except BlockingIOError:
                time.sleep(0.03)
                continue
            if not data:
                time.sleep(0.03)
                continue
            total += len(data)
            if not first:
                first = data[:20]
        print(f"  leidos {total} bytes del demux")
        if first:
            print("  primeros bytes:", first.hex())
        return total
    finally:
        ioctl(fd, DMX_STOP, ctypes.c_int(0))
        os.close(fd)
        if dvr is not None:
            os.close(dvr)


try_section_filter(0x0000, 4.0)
try_section_filter(0x0011, 3.0)
