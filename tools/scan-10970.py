#!/usr/bin/env python3
"""Escanea el transponder sintonizado (current_only) y lista los servicios."""

from __future__ import annotations

import os
import sys
import time

import httpx

BASE = os.environ.get("TBS_BASE", "http://127.0.0.1:9000")
PASSWORD = os.environ.get("TBS_ADMIN_PASSWORD", "prueba-servicio-2026")

client = httpx.Client(base_url=BASE, timeout=60.0)
login = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
if login.status_code != 200:
    print("login falló", login.status_code)
    sys.exit(1)
csrf = login.json()["csrf_token"]
headers = {"X-CSRF-Token": csrf}

start = client.post(
    "/api/scan/start",
    json={
        "adapters": [{"adapter": 0, "frontend": 0}],
        "current_only": True,
        "dwell_seconds": 10.0,
        "lock_timeout_s": 8.0,
    },
    headers=headers,
)
print("scan start:", start.status_code, start.text[:120])

progress = {}
for _ in range(80):
    time.sleep(0.5)
    status = client.get("/api/scan/status").json()
    progress = status["progress"]
    if progress["state"] in ("done", "error", "cancelled"):
        break
print("estado:", progress.get("state"), "| servicios:", progress.get("services_found"), "| LOCKs:", progress.get("transponders_locked"))
for entry in client.get("/api/scan/log").json()[-8:]:
    print("  ", entry["msg"])

channels = client.get("/api/channels", params={"limit": 40}).json()
print(f"\ncanales guardados: {channels['total']}")
for item in channels["items"][:25]:
    print(
        f"  {item['display_name'][:34]:36} SID={item['service_id']:5} "
        f"{item['service_type_label']:5} FTA={str(item['is_fta']):5} "
        f"vpid={item['video_pid']} apid={item['audio_pid']} prov={item['provider'][:18]}"
    )
client.post("/api/auth/logout", headers=headers)
