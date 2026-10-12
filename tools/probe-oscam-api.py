#!/usr/bin/env python3
"""Muestra el XML de estado de OSCam y los datos clave de la tarjeta."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import httpx

URL = "http://127.0.0.1:8888/oscamapi.html?part=status"
response = httpx.get(URL, timeout=8)
print("HTTP", response.status_code, len(response.text), "bytes")
print("=== XML (primeros 3000) ===")
print(response.text[:3000])

try:
    root = ET.fromstring(response.text)
except ET.ParseError as exc:
    print("XML invalido:", exc)
    raise SystemExit(1)

print("\n=== READERS ===")
for reader in root.iter("reader"):
    print(
        "  ",
        {
            "name": reader.get("name"),
            "protocol": reader.get("protocol"),
            "card_status": reader.get("card_status"),
            "caid": reader.get("caid"),
            "provid": reader.get("provid"),
            "type": reader.get("type"),
        },
    )

print("\n=== CLIENTS ===")
for client in root.iter("client"):
    print(
        "  ",
        {
            "name": client.get("name"),
            "type": client.get("type"),
            "protocol": client.get("protocol"),
            "au": client.get("au"),
        },
    )
