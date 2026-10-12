#!/usr/bin/env python3
"""Explora el webif de OSCam: estado real de la tarjeta y los readers."""

from __future__ import annotations

import json
import re

import httpx

BASE = "http://127.0.0.1:8888"

for path in ("/api/status", "/status.html", "/api/?function=status", "/"):
    try:
        response = httpx.get(BASE + path, timeout=6)
        body = response.text
        print(f"\n=== {path} -> HTTP {response.status_code} ({len(body)} bytes) ===")
        if path.endswith(".json") or "application/json" in response.headers.get("content-type", ""):
            print(json.dumps(response.json(), indent=2)[:1200])
        else:
            if "status.html" in path:
                # Extraer datos de tarjeta/reader del HTML
                for patron in (
                    r"<td[^>]*>(?:Reader|Card|CAID|Providers|Status)[^<]*</td>\s*<td[^>]*>([^<]*)</td>",
                    r"class=\"status[^\"]*\"[^>]*>([^<]{2,60})<",
                ):
                    hallados = re.findall(patron, body, re.IGNORECASE)
                    if hallados:
                        print("  coincidencias:", hallados[:12])
            print(body[:400].replace("\n", " "))
    except Exception as exc:  # noqa: BLE001
        print(f"\n=== {path} -> error: {exc}")

# Intentar la API JSON documentada de OSCam
for url in (
    "http://127.0.0.1:8888/api/status",
    "http://127.0.0.1:8888/oscamapi.html?part=status",
    "http://127.0.0.1:8888/api/?function=getstatus",
):
    try:
        response = httpx.get(url, timeout=6)
        print(f"\n>>> {url} -> {response.status_code}: {response.text[:300]}")
    except Exception as exc:  # noqa: BLE001
        print(f"\n>>> {url} -> error: {exc}")
