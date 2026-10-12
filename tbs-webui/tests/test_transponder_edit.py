"""Tests de edicion de transponders y de sintonia eligiendo puerto de sintonizador."""

from __future__ import annotations

from app.database import get_sessionmaker
from app.models import Service, Transponder


def _seed():
    """Crea un transponder con un canal y devuelve su id."""
    session = get_sessionmaker()()
    try:
        tp = Transponder(
            delivery_system="DVB-S2",
            frequency_khz=10970000,
            polarization="V",
            symbol_rate_ksps=27900,
            fec="3/4",
            modulation="8PSK",
            sat_position="19.2E",
        )
        session.add(tp)
        session.flush()
        session.add(
            Service(
                transponder_id=tp.id,
                service_id=1,
                name="Canal",
                provider="Prov",
                service_type=1,
                service_type_label="TV",
                is_fta=True,
            )
        )
        session.commit()
        return tp.id
    finally:
        session.close()


def test_list_includes_editable_fields(admin_client):
    tp_id = _seed()
    data = admin_client.get("/api/transponders").json()
    assert data["total"] == 1
    item = data["items"][0]
    for field in ("inversion", "rolloff", "pilot", "stream_id", "plp_id", "label", "services_count"):
        assert field in item, field
    assert item["id"] == tp_id


def test_get_single_transponder(admin_client):
    tp_id = _seed()
    assert admin_client.get(f"/api/transponders/{tp_id}").status_code == 200
    assert admin_client.get("/api/transponders/999999").status_code == 404


def test_patch_transponder_allows_tuner_edit(admin_client):
    tp_id = _seed()
    response = admin_client.patch(
        f"/api/transponders/{tp_id}",
        json={
            "frequency_khz": 11096000,
            "symbol_rate_ksps": 27500,
            "polarization": "H",
            "fec": "5/6",
            "modulation": "QPSK",
            "sat_position": "19.2E Astra",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["frequency_khz"] == 11096000
    assert body["polarization"] == "H"
    assert body["symbol_rate_ksps"] == 27500
    assert body["fec"] == "5/6"
    assert body["sat_position"] == "19.2E Astra"
    assert "11096" in body["label"]


def test_patch_partial_keeps_other_fields(admin_client):
    tp_id = _seed()
    response = admin_client.patch(f"/api/transponders/{tp_id}", json={"modulation": "QPSK"})
    assert response.status_code == 200
    body = response.json()
    assert body["modulation"] == "QPSK"
    assert body["frequency_khz"] == 10970000
    assert body["symbol_rate_ksps"] == 27900


def test_patch_rejects_duplicate_identity(admin_client):
    """Editar la frecuencia para chocar con otro transponder devuelve 409."""
    tp_id = _seed()
    session = get_sessionmaker()()
    try:
        other = Transponder(
            delivery_system="DVB-S2",
            frequency_khz=11096000,
            polarization="V",
            symbol_rate_ksps=27900,
        )
        session.add(other)
        session.commit()
        other_id = other.id
    finally:
        session.close()

    # 11096 kHz V 27900 ya existe (other) -> el primero no puede pasar ahi
    response = admin_client.patch(f"/api/transponders/{tp_id}", json={"frequency_khz": 11096000})
    assert response.status_code == 409, response.text
    assert "existe" in response.json()["detail"].lower()

    # Cambiar el symbol rate del segundo a uno libre si funciona
    ok = admin_client.patch(f"/api/transponders/{other_id}", json={"symbol_rate_ksps": 22000})
    assert ok.status_code == 200
    assert ok.json()["symbol_rate_ksps"] == 22000


def test_patch_requires_something(admin_client):
    tp_id = _seed()
    assert admin_client.patch(f"/api/transponders/{tp_id}", json={}).status_code == 422
    assert admin_client.patch(f"/api/transponders/{tp_id}", json={"frequency_khz": 0}).status_code == 422


def test_delete_transponder_removes_services(admin_client):
    """Al borrar un transponder se borran tambien sus canales."""
    tp_id = _seed()
    response = admin_client.delete(f"/api/transponders/{tp_id}")
    assert response.status_code == 200
    assert "canal" in response.json()["detail"].lower()
    assert admin_client.get("/api/transponders").json()["total"] == 0
    assert admin_client.get("/api/channels").json()["total"] == 0


def test_delete_missing_transponder(admin_client):
    assert admin_client.delete("/api/transponders/424242").status_code == 404


def test_patch_huge_id_does_not_500(admin_client):
    for huge in ("99999999999999999999", "9223372036854775808"):
        code = admin_client.patch(f"/api/transponders/{huge}", json={"modulation": "QPSK"}).status_code
        assert code in (404, 422), f"{huge} -> {code}"


def test_tune_saved_transponder_on_chosen_port(admin_client):
    """Se puede elegir el puerto (adaptador/frontend) al sintonizar un transponder."""
    tp_id = _seed()
    response = admin_client.post(
        f"/api/transponders/{tp_id}/tune",
        json={"adapter": 1, "frontend": 0, "lock_timeout_s": 3.0},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["adapter"] == 1
    assert body["frontend"] == 0
    assert body["frequency_khz"] == 10970000
    assert body["locked"] is True
    # La frecuencia intermedia se calcula restando el LNB (banda baja 9750 kHz)
    assert body["if_frequency_khz"] == 10970000 - 9750000


def test_tune_transponder_missing(admin_client):
    assert admin_client.post("/api/transponders/424242/tune", json={}).status_code == 404


def test_tune_transponder_requires_session(client):
    assert client.post("/api/transponders/1/tune", json={}).status_code == 401
