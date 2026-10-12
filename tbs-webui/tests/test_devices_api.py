"""Tests de adaptadores, sintonización, estado y ajustes (modo simulación)."""

from __future__ import annotations

import time


def test_list_adapters(admin_client):
    response = admin_client.get("/api/adapters")
    assert response.status_code == 200
    body = response.json()
    assert body["mock"] is True
    assert len(body["adapters"]) >= 1
    first = body["adapters"][0]
    assert first["device_path"].startswith("/dev/dvb/adapter")
    assert "DVB-S2" in first["delivery_systems"]
    assert body["locked"]["owner"] is None


def test_adapter_info(admin_client):
    ok = admin_client.get("/api/adapters/0/0/info")
    assert ok.status_code == 200
    assert ok.json()["adapter"] == 0

    missing = admin_client.get("/api/adapters/9/0/info")
    assert missing.status_code == 404


def test_tune_returns_stats(admin_client):
    response = admin_client.post(
        "/api/tune",
        json={
            "adapter": 0,
            "frontend": 0,
            "delivery_system": "DVB-S2",
            "frequency_khz": 11096000,
            "symbol_rate_ksps": 27500,
            "polarization": "V",
            "fec": "3/4",
            "modulation": "8PSK",
            "use_lnb": True,
            "lnb_low_khz": 9750,
            "lnb_high_khz": 10600,
            "lnb_threshold_khz": 11700,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["locked"] is True
    assert body["frequency_khz"] == 11096000
    # 11096 MHz < 11700 MHz -> banda baja -> IF = 11096000 - 9750000 = 1346000 kHz
    assert body["if_frequency_khz"] == 11096000 - 9750000
    stats = body["stats"]
    assert stats is not None
    assert 0 <= stats["signal_percent"] <= 100
    assert stats["snr_db"] is not None
    assert stats["flags"]["lock"] is True


def test_tune_banda_alta_resta_lnb_alto(admin_client):
    response = admin_client.post(
        "/api/tune",
        json={
            "delivery_system": "DVB-S2",
            "frequency_khz": 12551500,
            "symbol_rate_ksps": 27500,
            "polarization": "H",
            "use_lnb": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    # Banda alta (12551.5 MHz >= 11700) -> IF = 12551500 - 10600000 = 1951500
    assert body["if_frequency_khz"] == 12551500 - 10600000


def test_tune_sin_lnb_no_resta(admin_client):
    response = admin_client.post(
        "/api/tune",
        json={
            "delivery_system": "DVB-S2",
            "frequency_khz": 11096000,
            "symbol_rate_ksps": 27500,
            "use_lnb": False,
        },
    )
    assert response.status_code == 200
    assert response.json()["if_frequency_khz"] == 11096000


def test_status_reports_tuned_device(admin_client):
    admin_client.post(
        "/api/tune",
        json={"delivery_system": "DVB-S2", "frequency_khz": 11096000, "symbol_rate_ksps": 27500, "polarization": "V"},
    )
    time.sleep(0.6)  # deja que el monitor de señal haga al menos una lectura

    status = admin_client.get("/api/status")
    assert status.status_code == 200
    body = status.json()
    assert body["mock"] is True
    assert body["adapter"] == 0
    assert body["tuned"]["frequency_khz"] == 11096000
    assert body["stats"] is not None
    assert body["stats"]["locked"] is True

    history = admin_client.get("/api/status/history").json()
    assert isinstance(history, list)
    assert len(history) >= 1
    assert "t" in history[0]


def test_release_stops_signal(admin_client):
    admin_client.post(
        "/api/tune",
        json={"delivery_system": "DVB-S2", "frequency_khz": 11096000, "symbol_rate_ksps": 27500},
    )
    release = admin_client.post("/api/tune/release")
    assert release.status_code == 200

    status = admin_client.get("/api/status").json()
    assert status["stats"] is None
    assert status["tuned"] is None


def test_settings_defaults_and_update(admin_client):
    defaults = admin_client.get("/api/settings")
    assert defaults.status_code == 200
    body = defaults.json()
    assert body["lnb"]["low_khz"] == 9750
    assert body["lnb"]["high_khz"] == 10600
    assert body["ui"]["signal_poll_ms"] == 500
    assert body["ui"]["history_seconds"] == 60

    updated = admin_client.put(
        "/api/settings",
        json={
            "default_adapter": 1,
            "lnb": {"low_khz": 9750, "high_khz": 10600, "threshold_khz": 11700, "voltage": "18V", "tone_22k": True},
            "ui": {"signal_poll_ms": 750, "history_seconds": 90},
            "scan": {"lock_timeout_s": 7.5, "dwell_seconds": 9.0, "lock_timeout_ms": 500},
        },
    )
    assert updated.status_code == 200
    assert updated.json()["default_adapter"] == 1
    assert updated.json()["lnb"]["voltage"] == "18V"
    assert updated.json()["ui"]["signal_poll_ms"] == 750

    # Se persiste entre peticiones
    again = admin_client.get("/api/settings").json()
    assert again["ui"]["signal_poll_ms"] == 750
    assert again["lnb"]["tone_22k"] is True

    # El backend aplica el nuevo periodo de sondeo al monitor
    assert admin_client.app.state.device_manager._poll_ms == 750

    reset = admin_client.post("/api/settings/reset")
    assert reset.status_code == 200
    assert reset.json()["default_adapter"] == 0


def test_settings_validation(admin_client):
    bad = admin_client.put("/api/settings", json={"ui": {"signal_poll_ms": 5}})
    assert bad.status_code == 422

    bad_port = admin_client.put("/api/settings", json={"default_adapter": 99})
    assert bad_port.status_code == 422
