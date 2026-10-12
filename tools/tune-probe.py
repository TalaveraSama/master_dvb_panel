#!/usr/bin/env python3
"""Prueba real de sintonización vía API contra la tarjeta TBS.

Prueba una lista de transponders (o los de una tabla de dtv-scan-tables) y
reporta: LOCK, señal (% y dBm), SNR/CNR, BER y flags. Uso en el host real:

    .venv/bin/python /home/ruben/tune-probe.py --table Astra-19.2E --limit 12
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

BASE = os.environ.get("TBS_BASE_URL", "http://127.0.0.1:9000")
USER = os.environ.get("TBS_USER", "admin")
# Sin credenciales en el repositorio: la contraseña llega por entorno.
PASSWORD = os.environ.get("TBS_ADMIN_PASSWORD", "")


def login(client: httpx.Client) -> str:
    if not PASSWORD:
        print("Define TBS_ADMIN_PASSWORD en el entorno (no hay credenciales en el repo).")
        sys.exit(2)
    me = client.get("/api/auth/me").json()
    if me.get("must_setup"):
        response = client.post("/api/auth/setup", json={"username": USER, "password": PASSWORD})
    else:
        response = client.post("/api/auth/login", json={"username": USER, "password": PASSWORD})
        if response.status_code != 200:
            print("login falló:", response.status_code, response.text)
            sys.exit(1)
    return response.json()["csrf_token"]


def transponders_from_table(client: httpx.Client, table: str, limit: int) -> list[dict]:
    response = client.get(f"/api/scan/tables")
    bundles = response.json().get("bundles", [])
    names = {b["name"] for b in bundles}
    if table not in names:
        print(f"tabla {table!r} no encontrada. Algunas disponibles:")
        for name in sorted(names)[:15]:
            print("   ", name)
        sys.exit(1)
    # Se piden los transponders de la tabla mediante /api/scan/start? No: se leen
    # del parser de tablas a través de un escaneo en seco no es posible, así que
    # se usa el módulo directamente.
    sys.path.insert(0, "/home/ruben/tbs-webui")
    from pathlib import Path

    from app.dvb import satxml

    path = Path("/usr/share/dvb/dvb-s") / table
    items = satxml.parse_tables_file(path)
    result = []
    for item in items[:limit]:
        entry = {
            "delivery_system": item.delivery_system,
            "frequency_khz": item.frequency_khz,
            "symbol_rate_ksps": item.symbol_rate_ksps,
            "polarization": item.polarization,
            "fec": item.fec,
            "modulation": item.modulation,
            "rolloff": item.rolloff,
            "pilot": item.pilot,
        }
        result.append({key: value for key, value in entry.items() if value is not None})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", default=None, help="tabla de /usr/share/dvb/dvb-s")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--frequency", type=int, default=None, help="frecuencia suelta en kHz")
    parser.add_argument("--symbol-rate", type=int, default=27500)
    parser.add_argument("--polarization", default="H")
    parser.add_argument("--adapter", type=int, default=0)
    parser.add_argument("--system", default="DVB-S2")
    args = parser.parse_args()

    client = httpx.Client(base_url=BASE, timeout=90.0)
    csrf = login(client)
    headers = {"X-CSRF-Token": csrf}

    if args.frequency:
        candidates = [
            {
                "delivery_system": args.system,
                "frequency_khz": args.frequency,
                "symbol_rate_ksps": args.symbol_rate,
                "polarization": args.polarization,
            }
        ]
    else:
        candidates = transponders_from_table(client, args.table or "Astra-19.2E", args.limit)

    print(f"Probando {len(candidates)} transponders en adapter{args.adapter}")
    print("-" * 100)
    locked = 0
    for index, candidate in enumerate(candidates, start=1):
        body = dict(candidate)
        body.update(
            {
                "adapter": args.adapter,
                "frontend": 0,
                "use_lnb": True,
                "lnb_low_khz": 9750,
                "lnb_high_khz": 10600,
                "lnb_threshold_khz": 11700,
                "lock_timeout_s": 4.0,
            }
        )
        try:
            response = client.post("/api/tune", json=body, headers=headers)
        except Exception as exc:
            print(f"[{index:2}/{len(candidates)}] ERROR de red: {exc!r}")
            continue
        if response.status_code != 200:
            print(f"[{index:2}/{len(candidates)}] HTTP {response.status_code}: {response.text[:120]}")
            continue
        data = response.json()
        stats = data.get("stats") or {}
        flags = stats.get("flags") or {}
        mark = "LOCK " if data.get("locked") else "-----"
        if data.get("locked"):
            locked += 1
        label = (
            f"{body['frequency_khz'] / 1000:8.1f} MHz {body.get('polarization', '?'):1} "
            f"SR{body.get('symbol_rate_ksps', 0):6} {body.get('modulation') or '?':5}"
        )
        detail = (
            f"señal={stats.get('signal_percent')}% ({stats.get('signal_dbm')} dBm) "
            f"SNR={stats.get('snr_db')} dB BER={stats.get('ber')} "
            f"flags(sig={int(bool(flags.get('signal')))},car={int(bool(flags.get('carrier')))},"
            f"vit={int(bool(flags.get('viterbi')))},sync={int(bool(flags.get('sync')))},"
            f"lock={int(bool(flags.get('lock')))})"
        )
        print(f"[{index:2}/{len(candidates)}] {mark} {label} -> {detail}")

    print("-" * 100)
    print(f"RESULTADO: {locked}/{len(candidates)} transponders con LOCK")
    client.post("/api/tune/release", headers=headers)
    client.post("/api/auth/logout", headers=headers)
    return 0 if locked else 1


if __name__ == "__main__":
    raise SystemExit(main())
