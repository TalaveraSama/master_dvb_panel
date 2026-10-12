#!/usr/bin/env python3
"""Sintoniza 10970 V 27900, libera el frontend y escanea ese transponder."""

from __future__ import annotations

import os
import sys
import time

import httpx

BASE = os.environ.get("TBS_BASE", "http://127.0.0.1:9000")
PASSWORD = os.environ.get("TBS_ADMIN_PASSWORD", "prueba-servicio-2026")

client = httpx.Client(base_url=BASE, timeout=90.0)
login = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
if login.status_code != 200:
    print("login falló")
    sys.exit(1)
csrf = login.json()["csrf_token"]
headers = {"X-CSRF-Token": csrf}

tune = client.post(
    "/api/tune",
    json={
        "adapter": 0,
        "frontend": 0,
        "delivery_system": "DVB-S2",
        "frequency_khz": 10970000,
        "symbol_rate_ksps": 27900,
        "polarization": "V",
        "fec": "AUTO",
        "modulation": "AUTO",
        "use_lnb": True,
        "lock_timeout_s": 8.0,
    },
    headers=headers,
)
data = tune.json()
stats = data.get("stats") or {}
print("tune:", tune.status_code, "locked:", data.get("locked"), "señal:", stats.get("signal_percent"), "%", stats.get("signal_dbm"), "dBm")

# Liberar el frontend para que el escaneo pueda abrir el demux
client.post("/api/tune/release", headers=headers)
time.sleep(1)

start = client.post(
    "/api/scan/start",
    json={
        "adapters": [{"adapter": 0, "frontend": 0}],
        "transponders": [
            {"delivery_system": "DVB-S2", "frequency_khz": 10970000, "symbol_rate_ksps": 27900, "polarization": "V"}
        ],
        "dwell_seconds": 12.0,
        "lock_timeout_s": 8.0,
    },
    headers=headers,
)
print("scan:", start.status_code, start.text[:100])

progress = {}
for _ in range(90):
    time.sleep(0.5)
    progress = client.get("/api/scan/status").json()["progress"]
    if progress["state"] in ("done", "error", "cancelled"):
        break
print("estado:", progress.get("state"), "| servicios:", progress.get("services_found"), "| LOCKs:", progress.get("transponders_locked"))
for entry in client.get("/api/scan/log").json()[-10:]:
    print("  ", entry["msg"])

channels = client.get("/api/channels", params={"limit": 50}).json()
print(f"\ncanales: {channels['total']}")
for item in channels["items"][:30]:
    print(
        f"  {item['display_name'][:32]:34} SID={item['service_id']:5} {item['service_type_label']:5} "
        f"FTA={str(item['is_fta']):5} vpid={item['video_pid']} apid={item['audio_pid']} "
        f"ca={item['ca_system_ids']}"
    )
client.post("/api/auth/logout", headers=headers)
