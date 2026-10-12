#!/usr/bin/env python3
"""Prueba Newcamd contra el OSCam real del host (newcamd en 127.0.0.1:5000)."""

from __future__ import annotations

import json
import sys

import httpx

BASE = "http://127.0.0.1:9000"
PANEL_PASSWORD = "prueba-servicio-2026"
OSCAM = {
    "name": "oscam-local",
    "host": "127.0.0.1",
    "port": 5000,
    "username": "astra",
    "password": "astra",
    "deskey": "0102030405060708091011121314",
}

client = httpx.Client(base_url=BASE, timeout=60.0)
login = client.post("/api/auth/login", json={"username": "admin", "password": PANEL_PASSWORD})
if login.status_code != 200:
    print("login del panel falló:", login.status_code, login.text[:150])
    sys.exit(1)
headers = {"X-CSRF-Token": login.json()["csrf_token"]}
print("panel: sesión iniciada")

accounts = client.get("/api/newcamd/accounts").json()
existing = next((a for a in accounts["items"] if a["name"] == OSCAM["name"]), None)
if existing:
    account_id = existing["id"]
    client.put(f"/api/newcamd/accounts/{account_id}", json={**OSCAM, "enabled": True, "auto_connect": False}, headers=headers)
    print(f"cuenta '{OSCAM['name']}' ya existía (id {account_id}), actualizada")
else:
    created = client.post("/api/newcamd/accounts", json=OSCAM, headers=headers)
    print("crear cuenta:", created.status_code)
    if created.status_code not in (200, 201):
        print(created.text[:250])
        sys.exit(1)
    account_id = created.json()["id"]
    print(f"cuenta creada id={account_id}")

test = client.post("/api/newcamd/test", json={"id": account_id}, headers=headers).json()
print(f"PRUEBA DE CONEXIÓN -> ok={test['ok']} estado={test['state']}")
print(f"  mensaje: {test['message'][:120]}")

connect = client.post(f"/api/newcamd/accounts/{account_id}/connect", headers=headers)
print(f"CONECTAR -> HTTP {connect.status_code}")
if connect.status_code == 200:
    print("  estado:", json.dumps(connect.json()["status"], ensure_ascii=False)[:200])
else:
    print("  ", connect.text[:200])

status = client.get(f"/api/newcamd/accounts/{account_id}/status").json()
print("ESTADO FINAL:", json.dumps(status, ensure_ascii=False)[:220])

# Envío manual de un ECM de prueba (diagnóstico de extremo a extremo)
ecm = "8030100102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f20"
sent = client.post(
    f"/api/newcamd/accounts/{account_id}/ecm", json={"ecm_hex": ecm, "service_id": 1}, headers=headers
)
print("ENVÍO DE ECM ->", sent.status_code, sent.text[:140] if sent.status_code != 200 else json.dumps(sent.json())[:140])
status2 = client.get(f"/api/newcamd/accounts/{account_id}/status").json()
print("ESTADO TRAS ECM:", json.dumps({k: status2[k] for k in ("state", "ecm_sent", "cw_received", "last_cw")}))
client.post("/api/auth/logout", headers=headers)
