#!/usr/bin/env python3
"""Analiza el TS capturado (/tmp/ts.bin) con el parser SI del proyecto."""

from __future__ import annotations

import os
import sys
from collections import Counter

sys.path.insert(0, os.getcwd())

from app.dvb import si  # noqa: E402
from app.services.si_collector import iter_ts_packets  # noqa: E402

PATH = sys.argv[1] if len(sys.argv) > 1 else "/tmp/ts.bin"
raw = open(PATH, "rb").read()
print(f"TS: {len(raw)} bytes ({len(raw) // 188} paquetes)")

pids: Counter[int] = Counter()
pat_packets = 0
sdt_packets = 0
pmt_pids: set[int] = set()
assemblers: dict[int, object] = {}
pat_sections: list = []
sdt_sections: list = []
pmt_sections: dict[int, list] = {}
sections_seen: Counter[str] = Counter()

for packet in iter_ts_packets(raw):
    pids[packet.pid] += 1
    if packet.pid == 0x0000:
        pat_packets += 1
    if packet.pid == 0x0011:
        sdt_packets += 1

print("PAT (PID 0):", pat_packets, "paquetes | SDT (PID 0x11):", sdt_packets, "paquetes")
print("PIDs mas vistos:", [(hex(p), c) for p, c in pids.most_common(10)])


def assemble(pid: int, data: bytes, start: bool):
    """Alimenta el ensamblador de secciones del parser SI."""
    if pid not in assemblers:
        assemblers[pid] = si.SectionAssembler(pid=pid)
    return assemblers[pid].feed_all(data, payload_unit_start=start)


for packet in iter_ts_packets(raw):
    if packet.pid not in (0x0000, 0x0011) and packet.pid not in pmt_pids:
        continue
    start = bool(packet.payload_unit_start)
    try:
        sections = assemble(packet.pid, packet.payload, start)
    except Exception:  # noqa: BLE001
        continue
    for section in sections:
        table_id = getattr(section, "table_id", None)
        sections_seen[f"pid={hex(packet.pid)} table={hex(table_id) if table_id is not None else '?'}"] += 1
        if packet.pid == 0x0000:
            pat_sections.append(section)
            pmt_map = getattr(section, "pmt_map", None)
            if isinstance(pmt_map, dict):
                pmt_pids.update(int(value) for value in pmt_map.values() if value)
        elif packet.pid == 0x0011:
            sdt_sections.append(section)
        elif packet.pid in pmt_pids:
            pmt_sections.setdefault(packet.pid, []).append(section)

print("\nsecciones ensambladas:", dict(sections_seen))
print("PMT PIDs detectados en la PAT:", [hex(p) for p in sorted(pmt_pids)])

servicios = []
try:
    servicios = list(si.build_services(pat_sections, sdt_sections, pmt_sections))
except Exception as exc:  # noqa: BLE001
    print("build_services fallo:", exc)

print(f"\n=== SERVICIOS: {len(servicios)} ===")
for service in servicios[:40]:
    print("   ", service)
