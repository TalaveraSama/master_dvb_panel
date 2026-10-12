"""Tests de la API de perfiles Newcamd y de la configuracion del motor.

Cada test construye su propio payload con un nombre unico porque la base de
datos se comparte entre tests del mismo proceso.
"""

from __future__ import annotations

import itertools
import os
import socket

_CONTADOR = itertools.count()


def perfil(**cambios) -> dict:
    """Payload de perfil valido y unico."""
    base = {
        "name": f"newcamd_{next(_CONTADOR)}",
        "host": "192.168.1.50",
        "port": 15000,
        "username": "usuario",
        "password": "secreto",
        "deskey": "0102030405060708091011121314",
        "timeout_s": 8,
        "disable_emm": True,
        "enabled": True,
        "auto_connect": False,
        "notes": "perfil principal",
    }
    base.update(cambios)
    return base


def crear(client, **cambios):
    payload = perfil(**cambios)
    return client.post("/api/newcamd/profiles", json=payload), payload


# ---------------------------------------------------------------------------
# CRUD y ocultacion de secretos
# ---------------------------------------------------------------------------
def test_crear_perfil_no_expone_secretos(admin_client):
    respuesta, _ = crear(admin_client)
    assert respuesta.status_code == 201, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["password_set"] is True
    assert "secreto" not in str(cuerpo)
    assert "password" not in cuerpo


def test_listar_perfiles_sin_secretos(admin_client):
    crear(admin_client)
    datos = admin_client.get("/api/newcamd/profiles").json()
    assert datos["total"] >= 1
    item = datos["items"][0]
    assert item["password_configured"] is True
    assert item["des_key_configured"] is True
    assert "password" not in item
    assert "secreto" not in str(datos)
    assert datos["engine"]["native_newcamd"] is True


def test_nombre_duplicado(admin_client):
    _, payload = crear(admin_client)
    repetido = admin_client.post("/api/newcamd/profiles", json=payload)
    assert repetido.status_code == 409


def test_validaciones_de_perfil(admin_client):
    assert admin_client.post("/api/newcamd/profiles", json=perfil(name="1malo")).status_code == 422
    assert admin_client.post("/api/newcamd/profiles", json=perfil(deskey="zz")).status_code == 422
    assert admin_client.post("/api/newcamd/profiles", json=perfil(port=0)).status_code == 422
    assert admin_client.post("/api/newcamd/profiles", json=perfil(name="mal nombre")).status_code == 422


def test_editar_perfil_conserva_secretos(admin_client):
    respuesta, payload = crear(admin_client)
    perfil_id = respuesta.json()["id"]
    cambios = dict(payload, host="otro.servidor.local", password=None)
    editado = admin_client.put(f"/api/newcamd/profiles/{perfil_id}", json=cambios)
    assert editado.status_code == 200, editado.text
    assert editado.json()["host"] == "otro.servidor.local"
    assert editado.json()["password_set"] is True


def test_borrar_perfil(admin_client):
    respuesta, _ = crear(admin_client)
    perfil_id = respuesta.json()["id"]
    assert admin_client.delete(f"/api/newcamd/profiles/{perfil_id}").status_code == 200
    assert admin_client.delete(f"/api/newcamd/profiles/{perfil_id}").status_code == 404


def test_requiere_sesion(client):
    assert client.get("/api/newcamd/profiles").status_code == 401
    assert client.post("/api/newcamd/profiles", json=perfil()).status_code == 401


# ---------------------------------------------------------------------------
# Configuracion del motor
# ---------------------------------------------------------------------------
def test_config_genera_lua(admin_client):
    _, payload = crear(admin_client)
    datos = admin_client.get("/api/newcamd/config").json()
    assert datos["profiles"] >= 1
    assert "= newcamd({" in datos["lua"]
    assert f'name = "{payload["name"]}"' in datos["lua"]
    assert f'host = "{payload["host"]}"' in datos["lua"]
    assert f"port = {payload['port']}" in datos["lua"]
    assert "disable_emm = 1" in datos["lua"]
    assert datos["path"]


def test_apply_instala_la_configuracion(admin_client, tmp_path, monkeypatch):
    monkeypatch.setenv("TBS_DATA_DIR", str(tmp_path))
    crear(admin_client)
    aplicado = admin_client.post("/api/newcamd/config/apply")
    assert aplicado.status_code == 200, aplicado.text
    cuerpo = aplicado.json()
    assert cuerpo["ok"] is True
    with open(cuerpo["path"], encoding="utf-8") as manejador:
        contenido = manejador.read()
    assert "= newcamd({" in contenido
    assert contenido.startswith("-- Generado por TBS WebUI")


def test_apply_y_rollback(admin_client, tmp_path, monkeypatch):
    monkeypatch.setenv("TBS_DATA_DIR", str(tmp_path))
    crear(admin_client)
    assert admin_client.post("/api/newcamd/config/apply").status_code == 200
    # La segunda aplicacion genera la copia de seguridad
    assert admin_client.post("/api/newcamd/config/apply").status_code == 200
    vuelta = admin_client.post("/api/newcamd/config/rollback")
    assert vuelta.status_code == 200, vuelta.text
    assert vuelta.json()["ok"] is True


def test_apply_rechaza_perfil_incompleto(admin_client):
    """Un perfil al que le falta la contrasena no se puede aplicar."""
    from app.database import get_sessionmaker
    from app.models import NewcamdAccount

    respuesta, _ = crear(admin_client)
    sesion = get_sessionmaker()()
    try:
        fila = sesion.query(NewcamdAccount).filter(NewcamdAccount.id == respuesta.json()["id"]).one()
        fila.password_enc = ""
        sesion.commit()
    finally:
        sesion.close()

    aplicado = admin_client.post("/api/newcamd/config/apply")
    assert aplicado.status_code == 422
    assert "no es valido" in aplicado.json()["detail"]


def test_rollback_sin_copia(admin_client, tmp_path, monkeypatch):
    monkeypatch.setenv("TBS_DATA_DIR", str(tmp_path / "vacio"))
    respuesta = admin_client.post("/api/newcamd/config/rollback")
    assert respuesta.status_code in (200, 404), respuesta.text


# ---------------------------------------------------------------------------
# Comprobacion de conectividad (solo TCP)
# ---------------------------------------------------------------------------
def test_test_perfil_comprueba_tcp(admin_client):
    servidor = socket.socket()
    servidor.bind(("127.0.0.1", 0))
    servidor.listen(1)
    puerto = servidor.getsockname()[1]
    try:
        respuesta, _ = crear(admin_client, host="127.0.0.1", port=puerto)
        perfil_id = respuesta.json()["id"]
        datos = admin_client.post(f"/api/newcamd/profiles/{perfil_id}/test").json()
        assert datos["ok"] is True
        assert datos["reachable"] is True
        assert datos["port"] == puerto
        assert "TCP" in datos["note"]
    finally:
        servidor.close()


def test_test_perfil_puerto_cerrado(admin_client):
    respuesta, _ = crear(admin_client, host="127.0.0.1", port=1)
    datos = admin_client.post(f"/api/newcamd/profiles/{respuesta.json()['id']}/test").json()
    assert datos["ok"] is False
    assert datos["reachable"] is False


def test_test_perfil_inexistente(admin_client):
    assert admin_client.post("/api/newcamd/profiles/999999/test").status_code == 404


def test_host_dns_es_valido(admin_client):
    """No se limita a direcciones IP: se admiten nombres DNS."""
    respuesta, _ = crear(admin_client, host="servidor.local", port=15000)
    assert respuesta.status_code == 201, respuesta.text
