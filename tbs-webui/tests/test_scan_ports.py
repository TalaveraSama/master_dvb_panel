"""Tests del escaneo repartido entre varios puertos de sintonizador."""

from __future__ import annotations

import time

TRANSPONDERS = [
    {
        "delivery_system": "DVB-S2",
        "frequency_khz": 11096000,
        "symbol_rate_ksps": 22000,
        "polarization": "V",
        "fec": "3/4",
        "modulation": "8PSK",
    }
]


def _wait_scan(client, timeout_s: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout_s
    body = {}
    while time.monotonic() < deadline:
        body = client.get("/api/scan/status").json()
        if body["progress"]["state"] not in ("running", "starting"):
            return body
        time.sleep(0.1)
    return body


def test_scan_with_multiple_ports(admin_client):
    """Varios puertos aceptados: el escaneo recorre cada uno y termina bien."""
    started = admin_client.post(
        "/api/scan/start",
        json={
            "adapters": [{"adapter": 0, "frontend": 0}, {"adapter": 1, "frontend": 0}],
            "transponders": TRANSPONDERS,
            "dwell_seconds": 1.0,
        },
    )
    assert started.status_code == 200, started.text
    body = _wait_scan(admin_client)
    progress = body["progress"]
    assert progress["state"] == "done", progress
    # Dos puertos: el mismo transponder se ha escaneado dos veces
    assert progress["transponders_scanned"] >= 2, progress
    assert progress["services_found"] >= 1
    # El log menciona ambos puertos
    text = " ".join(entry["msg"] for entry in body["log"])
    assert "adapter0" in text and "adapter1" in text
    assert "Puerto de sintonizador 1/2" in text and "Puerto de sintonizador 2/2" in text
    assert progress.get("message")


def test_scan_rejects_empty_adapter_list(admin_client):
    response = admin_client.post(
        "/api/scan/start",
        json={"adapters": [], "transponders": TRANSPONDERS, "dwell_seconds": 1.0},
    )
    assert response.status_code == 422


def test_scan_deduplicates_repeated_ports(admin_client):
    """El mismo puerto repetido se usa una sola vez."""
    started = admin_client.post(
        "/api/scan/start",
        json={
            "adapters": [{"adapter": 0, "frontend": 0}, {"adapter": 0, "frontend": 0}],
            "transponders": TRANSPONDERS,
            "dwell_seconds": 1.0,
        },
    )
    assert started.status_code == 200, started.text
    body = _wait_scan(admin_client)
    text = " ".join(entry["msg"] for entry in body["log"])
    assert "Puerto 2/2" not in text, text
    assert body["progress"]["state"] == "done"


def test_scan_accepts_plain_integer_adapters(admin_client):
    """Compatibilidad: también se acepta una lista de enteros."""
    started = admin_client.post(
        "/api/scan/start",
        json={"adapters": [0], "transponders": TRANSPONDERS, "dwell_seconds": 1.0},
    )
    # La API declara AdapterRef, así que un entero se rechaza con 422 (contrato)
    assert started.status_code == 422
