#!/usr/bin/env python3
"""Comprueba parse_tables_file vs count_transponders en las tablas reales."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "/home/ruben/tbs-webui")

from app.dvb import satxml  # noqa: E402

base = Path("/usr/share/dvb/dvb-s")
ok = bad = 0
examples = []
for path in sorted(base.iterdir()):
    if not path.is_file():
        continue
    parsed = len(satxml.parse_tables_file(path))
    counted = satxml.count_transponders(path)
    if parsed == counted:
        ok += 1
    else:
        bad += 1
        if len(examples) < 10:
            examples.append(f"  {path.name}: parse={parsed} count={counted}")

print(f"tablas dvb-s: {ok + bad}  coinciden: {ok}  discrepan: {bad}")
for line in examples:
    print(line)

for name in ("Astra-19.2E", "ABS-2-75.0E", "Hotbird-13.0E"):
    path = base / name
    if path.is_file():
        print(f"{name}: parse={len(satxml.parse_tables_file(path))} count={satxml.count_transponders(path)}")
