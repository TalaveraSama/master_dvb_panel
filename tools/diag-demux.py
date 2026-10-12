#!/usr/bin/env python3
"""Diagnostico del demux DVB: abre demux0 a mano, filtra PAT y lee el TS."""

from __future__ import annotations

import fcntl
import os
import struct
import sys
import time

sys.path.insert(0, os.getcwd())

DMX_SET_PES_FILTER = 0x40106f0c
DMX_START = 0x6f1b
DMX_STOP = 0x6f1a
DMX_SET_BUFFER_SIZE = 0x40046f1d

DMX_IN_FRONTEND = 0
DMX_OUT_TAP = 2
DMX_PES_OTHER = 20

ADAPTER = 0
FRONTEND = 0
DEMUX = 0

DEMUX_PATH = f"/dev/dvb/adapter{ADAPTER}/demux{DEMUX}"


def ioctl(fd: int, request: int, arg=0) -> int:
    return fcntl.ioctl(fd, request, arg)


def read_filter(pid: int, seconds: float) -> tuple[int, dict[int, int]]:
    """Monta un filtro para `pid` y devuelve (bytes, {pid: paquetes})."""
    print(f"\n--- filtro PID 0x{pid:04X} durante {seconds:.0f} s ---")
    fd = os.open(DEMUX_PATH, os.O_RDWR | os.O_NONBLOCK)
    try:
        ioctl(fd, DMX_SET_BUFFER_SIZE, 1 << 18)
        params = struct.pack("=IHHII", pid, DMX_IN_FRONTEND, DMX_OUT_TAP, DMX_PES_OTHER, 0)
        ioctl(fd, DMX_SET_PES_FILTER, params)
        ioctl(fd, DMX_START, 0)
    except OSError as exc:
        print("  la configuracion del filtro fallo:", exc)
        os.close(fd)
        return 0, {}

    total = 0
    counter: dict[int, int] = {}
    sync = 0
    start = time.monotonic()
    while time.monotonic() - start < seconds:
        try:
            data = os.read(fd, 65536)
        except BlockingIOError:
            time.sleep(0.05)
            continue
        except OSError as exc:
            print("  error de lectura:", exc)
            break
        if not data:
            time.sleep(0.05)
            continue
        total += len(data)
        if data[0] == 0x47:
            sync += 1
        for offset in range(0, len(data) - 187, 188):
            packet = data[offset : offset + 188]
            if packet[0] != 0x47:
                continue
            packet_pid = ((packet[1] & 0x1F) << 8) | packet[2]
            counter[packet_pid] = counter.get(packet_pid, 0) + 1

    try:
        ioctl(fd, DMX_STOP, 0)
    except OSError:
        pass
    os.close(fd)
    print(f"  bytes={total} paquetes={total // 188} con_sync={sync}")
    print(f"  PIDs: {sorted(counter.items(), key=lambda kv: -kv[1])[:8]}")
    return total, counter


print("=== estado de /dev/dvb ===")
for name in sorted(os.listdir(f"/dev/dvb/adapter{ADAPTER}")):
    path = f"/dev/dvb/adapter{ADAPTER}/{name}"
    print("  ", path, "accesible:", os.access(path, os.R_OK | os.W_OK))

# El frontend lo sintoniza el servicio del panel; aqui solo se lee el demux.
read_filter(0x0000, 4.0)   # PAT
read_filter(0x0011, 2.0)   # SDT
read_filter(0x2000, 3.0)   # "todo" (PID 0x2000 en algunos drivers = any)
