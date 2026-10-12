#!/usr/bin/env python3
"""Depura el colector SI: sintoniza 10970 V, lee el TS y muestra que tablas llegan."""

from __future__ import annotations

import logging
import os
import sys
import time

sys.path.insert(0, os.getcwd())

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")

from app.config import get_settings  # noqa: E402
from app.dvb import registry  # noqa: E402
from app.dvb.types import Transponder, TuneParams  # noqa: E402
from app.services import si_collector  # noqa: E402

settings = get_settings()
adapter, frontend_id = 0, 0

frontend = registry.open_frontend(adapter, frontend_id)
frontend.open()
print("frontend:", getattr(frontend.info, "name", "?"))
print("soporta demux:", si_collector.frontend_supports_demux(frontend))
print("tiene read_ts:", hasattr(frontend, "read_ts"))
print("tiene set_pid_list:", hasattr(frontend, "set_pid_list"))

params = TuneParams(
    adapter=adapter,
    frontend=frontend_id,
    delivery_system="DVB-S2",
    frequency_khz=10970000,
    symbol_rate_ksps=27900,
    polarization="V",
    fec="3/4",
    modulation="8PSK",
    use_lnb=True,
    lnb_low_khz=9750,
    lnb_high_khz=10600,
    lnb_threshold_khz=11700,
    lock_timeout_s=8,
)
frontend.tune(params)
print("LOCK:", frontend.wait_lock(timeout_s=8))
stats = frontend.read_stats()
print("señal:", stats.signal_percent, "CNR:", stats.snr_db)

# 1) Leer TS cruda para ver si hay paquetes y que PIDs aparecen
print("\n=== lectura directa del TS (3 s, sin filtros propios) ===")
try:
    frontend.set_pid_list([0x0000, 0x0011, 0x0010])
    print("filtro PAT/SDT/NIT puesto")
except Exception as exc:  # noqa: BLE001
    print("set_pid_list fallo:", exc)

pids: dict[int, int] = {}
total = 0
start = time.monotonic()
while time.monotonic() - start < 3.0:
    try:
        data = frontend.read_ts(timeout=0.5, max_bytes=188 * 512)
    except Exception as exc:  # noqa: BLE001
        print("read_ts fallo:", exc)
        break
    if not data:
        continue
    total += len(data)
    for offset in range(0, len(data) - 187, 188):
        packet = data[offset : offset + 188]
        if packet[0] != 0x47:
            continue
        pid = ((packet[1] & 0x1F) << 8) | packet[2]
        pids[pid] = pids.get(pid, 0) + 1

print(f"bytes leidos: {total} ({total // 188} paquetes)")
print("PIDs vistos:", sorted(pids.items(), key=lambda kv: -kv[1])[:12])

print("\n=== colector SI integrado (8 s) ===")
services = si_collector.collect_services(frontend, dwell_seconds=8.0)
print("servicios:", len(services))
for service in services[:10]:
    print("   ", service)

frontend.close()
