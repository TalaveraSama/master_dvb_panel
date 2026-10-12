#!/usr/bin/env python3
"""Depura el escaneo multi-puerto: imprime el log completo."""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.getcwd())
import httpx

BASE = os.environ.get("TBS_BASE", "http://127.0.0.1:8010")
PASSWORD = os.environ.get("TBS_ADMIN_PASSWORD", "contrasena-segura-123")

client = httpx.Client(base_url=BASE, timeout=60.0)
login = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
print("login:", login.status_code)
headers = {"X-CSRF-Token": login.json()["csrf_token"]}

payload = {
    "adapters": [{"adapter": 0, "frontend": 0}, {"adapter": 1, "frontend": 0}],
    "transponders": [
        {
            "delivery_system": "DVB-S2",
            "frequency_khz": 11096000,
            "symbol_rate_ksps": 22000,
            "polarization": "V",
            "fec": "3/4",
            "modulation": "8PSK",
        }
    ],
    "dwell_seconds": 1.0,
}
started = client.post("/api/scan/start", json=payload, headers=headers)
print("start:", started.status_code, started.text[:200])
job = started.json().get("job_id") if started.status_code == 200 else None

if job:
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        body = client.get("/api/scan/status").json()
        state = body["progress"]["state"]
        if state not in ("running", "starting"):
            break
        time.sleep(0.2)
    print("estado final:", state)
    print("progress:", json.dumps(body["progress"], ensure_ascii=False)[:400])
    print("--- log completo ---")
    for entry in body.get("log", []):
        print("  ", entry.get("level", "info"), "|", entry.get("message"))

client.post("/api/auth/logout", headers=headers)
