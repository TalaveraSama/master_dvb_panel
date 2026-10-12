"""Tests de la tabla de canales y de las exportaciones M3U/channels.conf."""

from __future__ import annotations

import pytest

from app.database import SessionLocal
from app.models import Service, Transponder


@pytest.fixture()
def seeded():
    """Inserta un transponder con 3 canales (TV, radio y cifrado)."""
    session = SessionLocal()
    try:
        transponder = Transponder(
            delivery_system="DVB-S2",
            frequency_khz=11096000,
            polarization="V",
            symbol_rate_ksps=27500,
            fec="3/4",
            modulation="8PSK",
            sat_position="19.2E",
        )
        session.add(transponder)
        session.flush()
        session.add_all(
            [
                Service(
                    transponder_id=transponder.id,
                    service_id=101,
                    name="Canal Uno",
                    provider="Proveedor A",
                    service_type=1,
                    service_type_label="TV",
                    is_fta=True,
                    video_pid=512,
                    audio_pid=650,
                    pcr_pid=512,
                    pmt_pid=100,
                ),
                Service(
                    transponder_id=transponder.id,
                    service_id=102,
                    name="Radio Dos",
                    provider="Proveedor A",
                    service_type=2,
                    service_type_label="Radio",
                    is_fta=True,
                    audio_pid=660,
                ),
                Service(
                    transponder_id=transponder.id,
                    service_id=103,
                    name="Canal Pago",
                    provider="Proveedor B",
                    service_type=1,
                    service_type_label="TV",
                    is_fta=False,
                    scrambled=True,
                    video_pid=513,
                    audio_pid=661,
                    ca_system_ids=[1280],
                ),
            ]
        )
        session.commit()
        return transponder.id
    finally:
        session.close()


def test_list_channels(admin_client, seeded):
    response = admin_client.get("/api/channels")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    names = {item["name"] for item in body["items"]}
    assert names == {"Canal Uno", "Radio Dos", "Canal Pago"}
    first = body["items"][0]
    assert first["delivery_system"] == "DVB-S2"
    assert first["frequency_khz"] == 11096000
    assert first["display_name"] == first["name"]


def test_search_and_filters(admin_client, seeded):
    assert admin_client.get("/api/channels", params={"q": "radio"}).json()["total"] == 1
    assert admin_client.get("/api/channels", params={"q": "proveedor b"}).json()["total"] == 1
    assert admin_client.get("/api/channels", params={"type": "TV"}).json()["total"] == 2
    assert admin_client.get("/api/channels", params={"type": "Radio"}).json()["total"] == 1
    assert admin_client.get("/api/channels", params={"fta": False}).json()["total"] == 1
    assert admin_client.get("/api/channels", params={"scrambled": True}).json()["total"] == 1
    assert admin_client.get("/api/channels", params={"transponder_id": seeded}).json()["total"] == 3


def test_patch_channel(admin_client, seeded):
    channel_id = admin_client.get("/api/channels").json()["items"][0]["id"]
    renamed = admin_client.patch(f"/api/channels/{channel_id}", json={"custom_name": "Mi Canal"})
    assert renamed.status_code == 200
    assert renamed.json()["custom_name"] == "Mi Canal"
    assert renamed.json()["display_name"] == "Mi Canal"

    renamed_again = admin_client.patch(f"/api/channels/{channel_id}", json={"name": "Nombre Real"})
    assert renamed_again.json()["name"] == "Nombre Real"
    assert renamed_again.json()["display_name"] == "Mi Canal"  # el alias tiene prioridad

    empty = admin_client.patch(f"/api/channels/{channel_id}", json={})
    assert empty.status_code == 422

    missing = admin_client.patch("/api/channels/9999", json={"name": "X"})
    assert missing.status_code == 404


def test_delete_channels(admin_client, seeded):
    ids = [item["id"] for item in admin_client.get("/api/channels").json()["items"]]
    single = admin_client.delete(f"/api/channels/{ids[0]}")
    assert single.status_code == 200
    assert admin_client.get("/api/channels").json()["total"] == 2

    multi = admin_client.delete("/api/channels", params={"ids": f"{ids[1]},{ids[2]}"})
    assert multi.status_code == 200
    assert admin_client.get("/api/channels").json()["total"] == 0

    again = admin_client.delete(f"/api/channels/{ids[0]}")
    assert again.status_code == 404


def test_delete_all_channels(admin_client, seeded):
    response = admin_client.request("DELETE", "/api/channels", json={"all": True})
    assert response.status_code == 200
    assert admin_client.get("/api/channels").json()["total"] == 0


def test_transponders_list(admin_client, seeded):
    response = admin_client.get("/api/transponders")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["services_count"] == 3
    assert item["label"] == "11096 MHz V 27500 ksps DVB-S2"
    assert item["sat_position"] == "19.2E"


def test_export_m3u(admin_client, seeded):
    response = admin_client.get("/api/export/m3u")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    body = response.text
    assert body.startswith("#EXTM3U")
    assert "#EXTINF" in body
    assert "Canal Uno" in body
    assert "Radio Dos" in body
    assert "group-title=\"Proveedor A\"" in body
    assert body.count("#EXTINF") == 3

    # Sin radios
    only_tv = admin_client.get("/api/export/m3u", params={"radio": False}).text
    assert only_tv.count("#EXTINF") == 2


def test_export_channels_conf(admin_client, seeded):
    response = admin_client.get("/api/export/channels.conf")
    assert response.status_code == 200
    assert "channels.conf" in response.headers["content-disposition"]
    body = response.text
    assert "dvbv5-zap" in body  # cabecera explicativa
    lines = [line for line in body.splitlines() if line and not line.startswith("#")]
    assert len(lines) == 3

    by_name = {line.split(":", 1)[0]: line for line in lines}
    assert set(by_name) == {"Canal Uno", "Radio Dos", "Canal Pago"}

    uno = by_name["Canal Uno"]
    assert "11096000000" in uno  # frecuencia en Hz
    assert "sr=27500" in uno
    assert "sid=101" in uno
    assert "pol=V" in uno
    assert "vpid=512" in uno
    assert "apid=650" in uno
    # Todos los campos separados por ':' y sin ':' dentro de los nombres
    assert len(uno.split(":")) == 12


def test_export_without_channels_returns_404(admin_client):
    assert admin_client.get("/api/export/m3u").status_code == 404
    assert admin_client.get("/api/export/channels.conf").status_code == 404
