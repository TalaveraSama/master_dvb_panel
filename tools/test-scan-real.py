#!/usr/bin/env python3
"""Prueba el escaneo completo en el panel real (sintoniza y escanea 'actual')."""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.getcwd())
import httpx

BASE = os.environ.get("TBS_BASE", "http://127.0.0.1:9000")
PASSWORD = os.environ.get("TBS_ADMIN_PASSWORD", "prueba-servicio-2026")

client = httpx.Client(base_url=BASE, timeout=120.0)
login = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
print("login:", login.status_code)
headers = {"X-CSRF-Token": login.json()["csrf_token"]}


def wait_scan(timeout=90):
    deadline = time.monotonic() + timeout
    body = {}
    last = None
    while time.monotonic() < deadline:
        body = client.get("/api/scan/status").json()
        msg = body["progress"].get("message") or ""
        if msg != last:
            print("   ", body["progress"]["state"], "|", msg)
            last = msg
        if body["progress"]["state"] not in ("running", "starting"):
            return body
        time.sleep(0.7)
    return body


print("\n=== 1) Escaneo sin sintonizar (debe caer en el favorito 10970 V 27900) ===")
for adapter in (0,):
    tune = client.post(
        "/api/tune",
        json={"adapter": adapter, "frontend": 0, "delivery_system": "DVB-S2",
              "frequency_khz": 10970000, "symbol_rate_ksps": 27900, "polarization": "V",
              "fec": "3/4", "modulation": "8PSK", "use_lnb": True, "lock_timeout_s": 6},
        headers=headers,
    )
    print(f"tune adapter{adapter}:", tune.status_code, "locked:", tune.json().get("locked"),
          "señal:", (tune.json().get("stats") or {}).get("signal_percent"))

print("\n=== 2) Escaneo 'actual' con el transponder sintonizado ===")
started = client.post(
    "/api/scan/start",
    json={"adapters": [{"adapter": 0, "frontend": 0}], "current_only": True,
          "dwell_seconds": 6, "lock_timeout_s": 6},
    headers=headers,
)
print("start:", started.status_code, started.text[:150])
if started.status_code != 200:
    sys.exit(1)

body = wait_scan()
progress = body["progress"]
print("\nestado:", progress["state"])
print("transponders:", progress["transponders_scanned"], "con LOCK:", progress["transponders_locked"])
print("servicios:", progress["services_found"])
print("errores:", progress.get("errors"))
print("\n--- log del escaneo ---")
for entry in body.get("log", [])[:22]:
    print("   ", entry["level"], "|", entry["msg"][:110])

channels = client.get("/api/channels").json()
print("\ncanales guardados:", channels["total"])
for item in channels["items"][:8]:
    print("   ", item["name"], "|", item.get("provider"), "| sid", item.get("service_id"),
          "|", item.get("service_type_label"), "| FTA", item.get("is_fta"))
client.post("/api/auth/logout", headers=headers)
