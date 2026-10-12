#!/usr/bin/env python3
"""Prueba Newcamd contra el OSCam real del host (127.0.0.1:5000, user astra)."""

from __future__ import annotations

import json
import os
import sys

import httpx

BASE = os.environ.get("TBS_BASE", "http://127.0.0.1:9000")
PANEL_PASSWORD = os.environ.get("TBS_ADMIN_PASSWORD", "prueba-servicio-2026")
ACCOUNT = {
    "name": "oscam-local",
    "host": "127.0.0.1",
    "port": 5000,
    "username": "astra",
    "password": "astra",
    "deskey": "0102030405060708091011121314",
    "enabled": True,
    "auto_connect": False,
    "notes": "OSCam local (newcamd 5000)",
}

client = httpx.Client(base_url=BASE, timeout=60.0)
login = client.post("/api/auth/login", json={"username": "admin", "password": PANEL_PASSWORD})
if login.status_code != 200:
    print("login del panel fallo:", login.status_code)
    sys.exit(1)
headers = {"X-CSRF-Token": login.json()["csrf_token"]}

accounts = client.get("/api/newcamd/accounts").json()
existing = next((a for a in accounts["items"] if a["name"] == ACCOUNT["name"]), None)
if existing:
    account_id = existing["id"]
    updated = client.put(f"/api/newcamd/accounts/{account_id}", json=ACCOUNT, headers=headers)
    print(f"cuenta {account_id} actualizada:", updated.status_code, "puerto:", updated.json().get("port") if updated.status_code == 200 else updated.text[:120])
else:
    created = client.post("/api/newcamd/accounts", json=ACCOUNT, headers=headers)
    print("crear cuenta:", created.status_code)
    if created.status_code not in (200, 201):
        print(created.text[:250])
        sys.exit(1)
    account_id = created.json()["id"]
    print("cuenta creada:", account_id, "puerto:", created.json().get("port"))

test = client.post("/api/newcamd/test", json={"id": account_id}, headers=headers).json()
print(f"PRUEBA -> ok={test['ok']} estado={test['state']} mensaje={test['message'][:90]}")

connect = client.post(f"/api/newcamd/accounts/{account_id}/connect", headers=headers)
print("CONECTAR ->", connect.status_code)
if connect.status_code == 200:
    payload = connect.json()
    print("  estado:", json.dumps(payload["status"], ensure_ascii=False)[:230])
    status = client.get(f"/api/newcamd/accounts/{account_id}/status").json()
    print("  CAID:", status.get("caid"), "providers:", status.get("providers"))
else:
    print("  ", connect.text[:200])

status = client.get(f"/api/newcamd/accounts/{account_id}/status").json()
if status["state"] == "logged_in":
    ecm = "8030100102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f20"
    sent = client.post(f"/api/newcamd/accounts/{account_id}/ecm", json={"ecm_hex": ecm, "service_id": 1}, headers=headers)
    print("ENVIO ECM ->", sent.status_code, sent.text[:120])
    import time

    time.sleep(2)
    after = client.get(f"/api/newcamd/accounts/{account_id}/status").json()
    print("  ECM enviados:", after["ecm_sent"], "CW recibidos:", after["cw_received"], "ultimo CW:", after["last_cw"])
else:
    print("SIN SESION: no se envia ECM")

client.post("/api/auth/logout", headers=headers)
