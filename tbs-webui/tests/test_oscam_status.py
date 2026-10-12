"""Tests del estado de OSCam (parseo del XML del webif)."""

from __future__ import annotations

import httpx
import pytest

from app.services import oscam_status

SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<oscam version="2.26.09-11968" revision="" commit="39e78a46" uptime="41288" readonly="0">
  <status>
    <client type="s" name="root" protocol="server" au="0">
      <times login="2026-10-11T20:41:08+0000" online="41296" idle="57"></times>
      <connection ip="127.0.0.1" port="0">OK</connection>
    </client>
    <client type="r" name="tv_box4_1802" protocol="pcsc" au="-1">
      <times login="2026-10-12T07:17:27+0000" online="3117" idle="3117"></times>
      <connection ip="local" port="0">CARDOK</connection>
    </client>
  </status>
</oscam>
"""

EMPTY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<oscam version="2.26.09" uptime="10"><status></status></oscam>
"""


def test_base_url_normaliza():
    assert oscam_status.base_url(None) == "http://127.0.0.1:8888"
    assert oscam_status.base_url("192.168.1.21:8888/") == "http://192.168.1.21:8888"
    assert oscam_status.base_url("https://oscam.local") == "https://oscam.local"


def test_parse_status_lee_reader_y_tarjeta():
    status = oscam_status.parse_status(SAMPLE_XML, "http://127.0.0.1:8888")
    assert status.available is True
    assert status.version == "2.26.09-11968"
    assert status.revision == "39e78a46"
    assert status.uptime_s == 41288
    assert status.card_ready is True
    assert len(status.readers) == 1
    reader = status.readers[0]
    assert reader.name == "tv_box4_1802"
    assert reader.protocol == "pcsc"
    assert reader.status == "CARDOK"
    assert reader.status_text == "Tarjeta lista"
    assert reader.ok is True
    assert reader.idle_seconds == 3117
    # Los clientes no-reader se listan aparte
    assert len(status.clients) == 1
    assert status.clients[0]["type"] == "s"
    assert "OSCam activo" in status.message


def test_parse_status_sin_lectores():
    status = oscam_status.parse_status(EMPTY_XML)
    assert status.available is True
    assert status.readers == []
    assert status.card_ready is False
    assert "sin lectores" in status.message


def test_parse_status_xml_invalido():
    status = oscam_status.parse_status("<oscam><status>", "http://127.0.0.1:8888")
    assert status.available is False
    assert "ilegible" in status.message


def test_parse_status_lector_sin_tarjeta():
    xml = SAMPLE_XML.replace("CARDOK", "CARDNOTFOUND")
    status = oscam_status.parse_status(xml)
    assert status.card_ready is False
    assert status.readers[0].ok is False
    assert status.readers[0].status_text == "Sin tarjeta"


def test_fetch_status_no_lanza_si_oscam_no_esta(monkeypatch):
    def fake_get(*_args, **_kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(oscam_status, "_fetch", fake_get)
    status = oscam_status.fetch_status("http://127.0.0.1:9999")
    assert status.available is False
    assert "No se pudo consultar OSCam" in status.message


def test_fetch_status_ok(monkeypatch):
    monkeypatch.setattr(oscam_status, "_fetch", lambda url, timeout=4.0: SAMPLE_XML)
    status = oscam_status.fetch_status()
    assert status.available is True
    assert status.card_ready is True


def test_cache_no_repite_llamadas(monkeypatch):
    llamadas = {"n": 0}

    def fake_fetch(url, timeout=4.0):
        llamadas["n"] += 1
        return SAMPLE_XML

    monkeypatch.setattr(oscam_status, "_fetch", fake_fetch)
    cache = oscam_status.OscamStatusCache("http://127.0.0.1:8888", ttl=60.0)
    cache.get()
    cache.get()
    assert llamadas["n"] == 1
    cache.get(force=True)
    assert llamadas["n"] == 2


def test_endpoint_oscam_requiere_sesion(client):
    assert client.get("/api/newcamd/oscam").status_code == 401


def test_endpoint_oscam_devuelve_estado(admin_client, monkeypatch):
    monkeypatch.setattr(oscam_status, "_fetch", lambda url, timeout=4.0: SAMPLE_XML)
    response = admin_client.get("/api/newcamd/oscam", params={"force": "true"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["available"] is True
    assert body["card_ready"] is True
    assert body["readers"][0]["status"] == "CARDOK"


@pytest.mark.parametrize("valor", ["CARDOK", "CARDINSERTED", "CARDNOTFOUND", "CARDNEEDINIT"])
def test_estados_de_tarjeta_conocidos(valor):
    xml = SAMPLE_XML.replace("CARDOK", valor)
    status = oscam_status.parse_status(xml)
    assert status.readers[0].status_text != valor or valor in ("CARDOK", "CARDINSERTED")
