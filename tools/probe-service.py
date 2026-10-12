#!/usr/bin/env python3
"""Comprueba el panel servido por systemd en :9000 (hardware real)."""

from __future__ import annotations

import json
import os
import sys

import httpx

BASE = os.environ.get("TBS_BASE", "http://127.0.0.1:9000")
PASSWORD = os.environ.get("TBS_ADMIN_PASSWORD", "prueba-servicio-2026")

client = httpx.Client(base_url=BASE, timeout=30.0)
health = client.get("/api/health").json()
print("health:", json.dumps(health, ensure_ascii=False))

login = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
print("login:", login.status_code)
if login.status_code != 200:
    print(login.text[:200])
    sys.exit(1)
csrf = login.json()["csrf_token"]

adapters = client.get("/api/adapters").json()
print(f"adapters: {len(adapters['adapters'])} (mock={adapters['mock']})")
for item in adapters["adapters"]:
    print(f"   adapter{item['adapter']}/frontend{item['frontend']}: {item['name']} {item['delivery_systems']}")

tune = client.post(
    "/api/tune",
    json={
        "delivery_system": "DVB-S2",
        "frequency_khz": 11096000,
        "symbol_rate_ksps": 27500,
        "polarization": "V",
        "use_lnb": True,
    },
    headers={"X-CSRF-Token": csrf},
)
print("tune:", tune.status_code)
body = tune.json()
stats = body.get("stats") or {}
print(
    "  locked={locked} signal={signal}% ({dbm} dBm) snr={snr} ber={ber}".format(
        locked=body.get("locked"),
        signal=stats.get("signal_percent"),
        dbm=stats.get("signal_dbm"),
        snr=stats.get("snr_db"),
        ber=stats.get("ber"),
    )
)
print("  flags:", json.dumps(stats.get("flags")))
print("  IF:", body.get("if_frequency_khz"), "kHz")

# Varias lecturas para ver si las barras se mueven
readings = []
for _ in range(4):
    client.get("/api/tune") if False else None
    import time

    time.sleep(0.6)
    status = client.get("/api/status").json()
    if status.get("stats"):
        readings.append((status["stats"]["signal_percent"], status["stats"]["signal_dbm"]))
print("lecturas:", readings)
print("¿se mueven las barras?:", len({r[0] for r in readings}) > 1)

client.post("/api/tune/release", headers={"X-CSRF-Token": csrf})
client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
