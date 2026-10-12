#!/usr/bin/env python3
"""Lee el TS completo desde /dev/dvb/adapter0/dvr0 y busca las tablas SI.

`dvr0` entrega el transporte entero sin filtros, asi que es la via mas fiable
para el sondeo de PAT/SDT.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.getcwd())

DVR_PATH = "/dev/dvb/adapter0/dvr0"

print(f"=== leyendo {DVR_PATH} durante 4 s ===")
fd = os.open(DVR_PATH, os.O_RDONLY | os.O_NONBLOCK)
total = 0
pids: dict[int, int] = {}
tables: dict[int, int] = {}
byte0 = {}
sections: dict[int, bytes] = {}
start = time.monotonic()
try:
    while time.monotonic() - start < 4.0:
        try:
            data = os.read(fd, 65536)
        except BlockingIOError:
            time.sleep(0.02)
            continue
        if not data:
            time.sleep(0.02)
            continue
        total += len(data)
        for offset in range(0, len(data) - 187, 188):
            packet = data[offset : offset + 188]
            head = packet[0]
            byte0[head] = byte0.get(head, 0) + 1
            if head != 0x47:
                continue
            pid = ((packet[1] & 0x1F) << 8) | packet[2]
            pids[pid] = pids.get(pid, 0) + 1
            # Seccion PSI que empieza en este paquete (payload_unit_start)
            if packet[1] & 0x40:
                pointer = packet[4]
                table_id = packet[5 + pointer] if 5 + pointer < 188 else None
                if table_id is not None:
                    tables[table_id] = tables.get(table_id, 0) + 1
                    if pid not in sections:
                        sections[pid] = packet[5 + pointer : 5 + pointer + 32]
finally:
    os.close(fd)

print(f"bytes={total} paquetes={total // 188} (esperado ~{int(4 * 1000 * 1000 / 8 / 188)} si el TS va a ~1 Mbit/s)")
print("primer byte de cada paquete:", {hex(k): v for k, v in list(byte0.items())[:6]})
print("PIDs mas vistos:", [(hex(p), c) for p, c in sorted(pids.items(), key=lambda kv: -kv[1])[:12]])
print("table_ids vistos:", {hex(k): v for k, v in sorted(tables.items())})

NOMBRES = {0x00: "PAT", 0x11: "SDT/BAT", 0x10: "NIT", 0x02: "PMT", 0x12: "EIT", 0x14: "TDT"}
print("\n--- cabeceras de seccion ---")
for pid, section in list(sections.items())[:8]:
    tabla = section[0]
    print(f"  PID {hex(pid)}: table_id {hex(tabla)} ({NOMBRES.get(tabla, '?')}) -> {section[:24].hex()}")
