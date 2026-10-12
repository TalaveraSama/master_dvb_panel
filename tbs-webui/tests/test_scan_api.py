"""Tests del escaneo: arranque, progreso, guardado y anti-duplicados."""

from __future__ import annotations

import time

from app.database import SessionLocal
from app.models import Service, Transponder


def _wait_scan(client, timeout: float = 25.0) -> dict:
    """Espera a que el escaneo termine (o falle) y devuelve el último estado."""
    deadline = time.monotonic() + timeout
    body = {}
    while time.monotonic() < deadline:
        response = client.get("/api/scan/status")
        assert response.status_code == 200
        body = response.json()
        state = body["progress"]["state"]
        if state in ("done", "error", "cancelled"):
            return body
        time.sleep(0.15)
    raise AssertionError(f"El escaneo no terminó a tiempo: {body.get('progress', {})}")


TRANSPONDERS = [
    {"delivery_system": "DVB-S2", "frequency_khz": 11096000, "symbol_rate_ksps": 27500, "polarization": "V"},
    {"delivery_system": "DVB-S2", "frequency_khz": 12226000, "symbol_rate_ksps": 27500, "polarization": "H"},
]


def test_scan_tables_endpoint(admin_client):
    """Sin dtv-scan-tables instalado devuelve lista vacía, nunca error."""
    response = admin_client.get("/api/scan/tables")
    assert response.status_code == 200
    body = response.json()
    assert "bundles" in body
    assert isinstance(body["bundles"], list)


def test_scan_start_status_and_persistence(admin_client):
    started = admin_client.post(
        "/api/scan/start",
        json={"adapters": [{"adapter": 0, "frontend": 0}], "transponders": TRANSPONDERS, "dwell_seconds": 1.0},
    )
    assert started.status_code == 200, started.text
    job_id = started.json()["job_id"]
    assert job_id

    # Un escaneo en curso no deja arrancar otro
    second = admin_client.post(
        "/api/scan/start",
        json={"adapters": [{"adapter": 0}], "transponders": TRANSPONDERS, "dwell_seconds": 1.0},
    )
    assert second.status_code in (409, 503)

    body = _wait_scan(admin_client)
    progress = body["progress"]
    assert progress["state"] == "done", progress
    assert progress["job_id"] == job_id
    assert progress["total"] == 2
    assert progress["transponders_scanned"] == 2
    assert progress["transponders_locked"] == 2
    assert progress["services_found"] >= 1
    assert progress["percent"] == 100.0
    assert body["log"], "el log de escaneo no debe estar vacío"

    # Los canales quedaron guardados en SQLite
    channels = admin_client.get("/api/channels").json()
    assert channels["total"] == progress["services_found"]
    assert all(item["frequency_khz"] in (11096000, 12226000) for item in channels["items"])

    # El log incremental funciona
    since = body["log"][0]["ts"] - 1
    incremental = admin_client.get("/api/scan/log", params={"since": since})
    assert incremental.status_code == 200
    assert len(incremental.json()) >= len(body["log"]) - 1


def test_scan_is_idempotent_no_duplicates(admin_client):
    """Reescanear el mismo transponder no duplica canales (clave transponder+service_id)."""
    payload = {
        "adapters": [{"adapter": 0, "frontend": 0}],
        "transponders": [TRANSPONDERS[0]],
        "dwell_seconds": 1.0,
    }
    assert admin_client.post("/api/scan/start", json=payload).status_code == 200
    first = _wait_scan(admin_client)
    first_total = admin_client.get("/api/channels").json()["total"]
    assert first_total >= 1

    assert admin_client.post("/api/scan/start", json=payload).status_code == 200
    second = _wait_scan(admin_client)
    second_total = admin_client.get("/api/channels").json()["total"]

    assert second_total == first_total, "el reescaneo no debe duplicar canales"
    assert second["progress"]["services_found"] == first["progress"]["services_found"]

    # Y sólo hay un transponder guardado para esa frecuencia
    session = SessionLocal()
    try:
        assert session.query(Transponder).filter(Transponder.frequency_khz == 11096000).count() == 1
        assert session.query(Service).count() == first_total
    finally:
        session.close()


def test_scan_stop(admin_client):
    transponders = [
        {"delivery_system": "DVB-S2", "frequency_khz": 11000000 + index * 1000, "symbol_rate_ksps": 27500}
        for index in range(8)
    ]
    started = admin_client.post(
        "/api/scan/start",
        json={"adapters": [{"adapter": 0}], "transponders": transponders, "dwell_seconds": 2.0},
    )
    assert started.status_code == 200

    stopped = admin_client.post("/api/scan/stop")
    assert stopped.status_code == 200
    assert stopped.json()["ok"] is True

    body = _wait_scan(admin_client)
    assert body["progress"]["state"] in ("cancelled", "done")

    # Parar cuando no hay nada no es un error
    assert admin_client.post("/api/scan/stop").status_code == 200


def test_scan_requires_target(admin_client):
    response = admin_client.post("/api/scan/start", json={"adapters": [{"adapter": 0}]})
    assert response.status_code == 400
    assert "transponders" in response.json()["detail"].lower()


def test_scan_current_only_requires_tuned(admin_client):
    response = admin_client.post(
        "/api/scan/start", json={"adapters": [{"adapter": 0}], "current_only": True, "dwell_seconds": 1.0}
    )
    assert response.status_code == 400

    admin_client.post(
        "/api/tune",
        json={"delivery_system": "DVB-S2", "frequency_khz": 11096000, "symbol_rate_ksps": 27500},
    )
    ok = admin_client.post(
        "/api/scan/start", json={"adapters": [{"adapter": 0}], "current_only": True, "dwell_seconds": 1.0}
    )
    assert ok.status_code == 200
    body = _wait_scan(admin_client)
    assert body["progress"]["state"] == "done"
    assert body["progress"]["total"] == 1
