"""Tests del bloqueo exclusivo por sintonizador y de su estado en la API."""

from __future__ import annotations

import pytest

from app.dvb.base import DeviceBusyError
from app.dvb.types import TuneParams
from app.services.tuner_registry import (
    STATE_LOCKED,
    STATE_TUNING,
    TunerBusyError,
    TunerRegistry,
    port_key,
)


def _params(adapter: int = 0, frontend: int = 0, frecuencia: int = 10970000) -> TuneParams:
    return TuneParams(
        adapter=adapter,
        frontend=frontend,
        delivery_system="DVB-S2",
        frequency_khz=frecuencia,
        symbol_rate_ksps=27900,
        polarization="V",
        fec="3/4",
        modulation="8PSK",
        use_lnb=True,
        lock_timeout_s=3,
    )


# ---------------------------------------------------------------------------
# Registro
# ---------------------------------------------------------------------------
def test_port_key():
    assert port_key(0, 0) == "adapter0/frontend0"
    assert port_key(2, 1) == "adapter2/frontend1"


def test_claim_exclusivo():
    registro = TunerRegistry()
    registro.claim(0, 0, "api")
    assert registro.owner_of(0, 0) == "api"
    # Otro propietario no puede usarlo
    with pytest.raises(TunerBusyError) as exc:
        registro.claim(0, 0, "scan:abc")
    assert "en uso por 'api'" in str(exc.value)
    # El mismo propietario si puede reclamarlo otra vez
    registro.claim(0, 0, "api")


def test_puertos_independientes():
    """Dos sintonizadores distintos pueden usarse a la vez por dueños distintos."""
    registro = TunerRegistry()
    registro.claim(0, 0, "api")
    registro.claim(1, 0, "scan:x")  # no debe lanzar
    assert registro.owner_of(0, 0) == "api"
    assert registro.owner_of(1, 0) == "scan:x"
    assert registro.is_busy(0, 0, "scan:x") is True
    assert registro.is_busy(1, 0, "scan:x") is False


def test_release_solo_por_su_dueno():
    registro = TunerRegistry()
    registro.claim(0, 0, "api")
    assert registro.release(0, 0, "otro") is False
    assert registro.owner_of(0, 0) == "api"
    assert registro.release(0, 0, "api") is True
    assert registro.owner_of(0, 0) is None
    assert registro.release(0, 0) is False  # ya no estaba


def test_release_all_de_un_dueno():
    registro = TunerRegistry()
    registro.claim(0, 0, "scan:x")
    registro.claim(1, 0, "scan:x")
    registro.claim(2, 0, "api")
    assert registro.release_all("scan:x") == 2
    assert registro.owner_of(0, 0) is None
    assert registro.owner_of(2, 0) == "api"


def test_mark_tuned_y_niveles():
    registro = TunerRegistry()
    registro.claim(0, 0, "api")
    registro.mark_tuned(_params(), "api")

    class Flags:
        is_locked = True

    class Stats:
        lock = Flags()
        flags = Flags()
        signal_percent = 76.12
        snr_db = 11.46
        ber = 0.0001
        post_ber = 1.2e-6
        signal_dbm = -54.9

    registro.update_stats(0, 0, Stats())
    uso = registro.snapshot()[0]
    assert uso.state == STATE_LOCKED
    assert uso.locked is True
    assert uso.signal_percent == 76.1  # redondeado
    assert uso.snr_db == 11.46
    assert uso.level_dbm == -54.9
    assert uso.frequency_khz == 10970000
    assert uso.label.startswith("10970 MHz")
    assert len(uso.history) == 1


def test_snapshot_y_to_dict():
    registro = TunerRegistry()
    registro.claim(0, 0, "api")
    registro.mark_tuned(_params(), "api")
    datos = registro.to_dict()
    assert datos["in_use"] == 1
    assert datos["tuners"][0]["key"] == "adapter0/frontend0"
    assert datos["tuners"][0]["transponder"]["frequency_mhz"] == 10970.0
    assert datos["tuners"][0]["in_use"] is True


def test_estado_tuning_sin_niveles():
    registro = TunerRegistry()
    registro.claim(0, 0, "api")
    assert registro.snapshot()[0].state == STATE_TUNING
    assert registro.in_use() == []  # aun sin transponder


def test_update_stats_de_puerto_desconocido_no_falla():
    registro = TunerRegistry()
    registro.update_stats(3, 0, object())  # no debe lanzar


def test_historial_se_recorta():
    registro = TunerRegistry(history_seconds=5)

    class Flags:
        is_locked = True

    class Stats:
        lock = Flags()
        flags = Flags()
        signal_percent = 50.0
        snr_db = 10.0
        ber = 0.0
        post_ber = 0.0
        signal_dbm = -50.0

    registro.claim(0, 0, "api")
    registro.mark_tuned(_params(), "api")
    for _ in range(20):
        registro.update_stats(0, 0, Stats())
    assert len(registro.snapshot()[0].history) <= 600


# ---------------------------------------------------------------------------
# DeviceManager: el bloqueo se aplica al sintonizar
# ---------------------------------------------------------------------------
def test_device_manager_bloquea_el_puerto(device_manager):
    device_manager.tune(_params(), owner="api")
    assert device_manager.tuners.owner_of(0, 0) == "api"
    # Otro intento sobre el mismo puerto se rechaza
    with pytest.raises(DeviceBusyError) as exc:
        device_manager.tune(_params(frecuencia=12000000), owner="scan:x")
    assert "en uso" in str(exc.value)
    # Liberar permite volver a usarlo
    device_manager.release()
    assert device_manager.tuners.owner_of(0, 0) is None
    device_manager.tune(_params(adapter=1), owner="scan:y")
    assert device_manager.tuners.owner_of(1, 0) == "scan:y"


def test_release_libera_la_reserva(device_manager):
    device_manager.tune(_params(), owner="api")
    assert device_manager.tuners.in_use()
    device_manager.release()
    assert device_manager.tuners.in_use() == []


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
def test_endpoint_tuners_vacio(admin_client):
    respuesta = admin_client.get("/api/tuners")
    assert respuesta.status_code == 200
    assert respuesta.json()["in_use"] == 0
    assert respuesta.json()["tuners"] == []


def test_endpoint_tuners_tras_sintonizar(admin_client):
    admin_client.post(
        "/api/tune",
        json={
            "delivery_system": "DVB-S2",
            "frequency_khz": 10970000,
            "symbol_rate_ksps": 27900,
            "polarization": "V",
        },
    )
    datos = admin_client.get("/api/tuners").json()
    assert datos["in_use"] == 1
    tuner = datos["tuners"][0]
    assert tuner["key"] == "adapter0/frontend0"
    assert tuner["owner"]
    assert tuner["transponder"]["frequency_khz"] == 10970000
    assert "signal" in tuner


def test_endpoint_liberar_sintonizador(admin_client):
    admin_client.post(
        "/api/tune", json={"delivery_system": "DVB-S2", "frequency_khz": 10970000, "symbol_rate_ksps": 27900}
    )
    assert admin_client.get("/api/tuners").json()["in_use"] == 1
    liberar = admin_client.post("/api/tuners/0/0/release")
    assert liberar.status_code == 200
    assert admin_client.get("/api/tuners").json()["in_use"] == 0
    # Liberar otra vez devuelve 404
    assert admin_client.post("/api/tuners/0/0/release").status_code == 404


def test_endpoint_tuners_requiere_sesion(client):
    assert client.get("/api/tuners").status_code == 401
    assert client.post("/api/tuners/0/0/release").status_code == 401


def test_escaneo_usa_y_libera_el_puerto(admin_client):
    """Tras un escaneo el puerto queda libre (nada bloqueado para siempre)."""
    import time

    started = admin_client.post(
        "/api/scan/start",
        json={
            "adapters": [{"adapter": 0, "frontend": 0}],
            "transponders": [
                {
                    "delivery_system": "DVB-S2",
                    "frequency_khz": 10970000,
                    "symbol_rate_ksps": 27900,
                    "polarization": "V",
                }
            ],
            "dwell_seconds": 1.0,
        },
    )
    assert started.status_code == 200
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        estado = admin_client.get("/api/scan/status").json()["progress"]["state"]
        if estado not in ("running", "starting"):
            break
        time.sleep(0.1)
    assert admin_client.get("/api/tuners").json()["in_use"] == 0
