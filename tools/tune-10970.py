#!/usr/bin/env python3
"""Sintoniza un transponder concreto en el host real y muestra señal/LOCK."""

from __future__ import annotations

import json
import os
import sys
import time

import httpx

BASE = os.environ.get("TBS_BASE", "http://127.0.0.1:9000")
PASSWORD = os.environ.get("TBS_ADMIN_PASSWORD", "prueba-servicio-2026")
FREQ = int(os.environ.get("TBS_FREQ", "10970000"))
SR = int(os.environ.get("TBS_SR", "27900"))
POL = os.environ.get("TBS_POL", "V")

client = httpx.Client(base_url=BASE, timeout=30.0)
login = client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
if login.status_code != 200:
    print("login falló", login.status_code, login.text[:160])
    sys.exit(1)
csrf = login.json()["csrf_token"]
headers = {"X-CSRF-Token": csrf}

body = {
    "adapter": 0,
    "frontend": 0,
    "delivery_system": "DVB-S2",
    "frequency_khz": FREQ,
    "symbol_rate_ksps": SR,
    "polarization": POL,
    "fec": "AUTO",
    "modulation": "AUTO",
    "use_lnb": True,
    "lnb_low_khz": 9750,
    "lnb_high_khz": 10600,
    "lnb_threshold_khz": 11700,
    "lock_timeout_s": 8.0,
}
print(f"sintonizando {FREQ / 1000:.1f} MHz {POL} {SR} ksps en adapter0 …")
tune = client.post("/api/tune", json=body, headers=headers)
print("tune:", tune.status_code)
data = tune.json()
print("  IF:", data.get("if_frequency_khz"), "kHz  locked:", data.get("locked"))

for i in range(10):
    time.sleep(0.7)
    status = client.get("/api/status").json()
    stats = status.get("stats") or {}
    flags = stats.get("flags") or {}
    print(
        f"  t={i * 0.7:4.1f}s señal={stats.get('signal_percent')}% ({stats.get('signal_dbm')} dBm) "
        f"SNR={stats.get('snr_db')} BER={stats.get('ber')} "
        f"flags[sig={int(bool(flags.get('signal')))},car={int(bool(flags.get('carrier')))},"
        f"vit={int(bool(flags.get('viterbi')))},sync={int(bool(flags.get('sync')))},"
        f"lock={int(bool(flags.get('lock')))}]"
    )
    if flags.get("lock"):
        print("  => LOCK ✔")
        break

client.post("/api/auth/logout", headers=headers)
