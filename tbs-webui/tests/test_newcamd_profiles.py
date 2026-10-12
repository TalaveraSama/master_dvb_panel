"""Tests de perfiles Newcamd: validacion, secretos cifrados y configuracion."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.services import newcamd_profiles as perfil

PERFIL = perfil.NewcamdProfile(
    name="newcamd_main",
    host="192.168.1.50",
    port=15000,
    username="usuario",
    password="secreto",
    des_key="0102030405060708091011121314",
    timeout=8,
    disable_emm=True,
)


# ---------------------------------------------------------------------------
# Validacion
# ---------------------------------------------------------------------------
def test_perfil_valido():
    PERFIL.validate()


@pytest.mark.parametrize(
    "campo,valor",
    [
        ("name", "1malo"),          # no empieza por letra
        ("name", "con espacio"),
        ("name", ""),
        ("host", "no vale!"),
        ("host", ""),
        ("port", 0),
        ("port", 70000),
        ("username", ""),
        ("password", ""),
        ("des_key", "0102"),        # no tiene 28 hex
        ("des_key", "g" * 28),      # no es hexadecimal
        ("timeout", 0),
        ("timeout", 999),
    ],
)
def test_perfil_invalido(campo, valor):
    datos = {**PERFIL.__dict__, campo: valor}
    with pytest.raises(perfil.ProfileError):
        perfil.NewcamdProfile(**datos).validate()


def test_host_acepta_dns_e_ipv6():
    assert perfil._valid_host("servidor.local")
    assert perfil._valid_host("192.168.1.50")
    assert perfil._valid_host("2001:db8::1")
    assert not perfil._valid_host("-malo.local")
    assert not perfil._valid_host("")


def test_public_dict_oculta_secretos():
    datos = PERFIL.public_dict()
    assert datos["password_configured"] is True
    assert datos["des_key_configured"] is True
    assert "password" not in datos
    assert "des_key" not in datos
    assert "secreto" not in str(datos)


# ---------------------------------------------------------------------------
# Secretos (AES-GCM con clave maestra en fichero 0600)
# ---------------------------------------------------------------------------
class _Settings:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = str(data_dir)


def test_cifrado_va_y_vuelve(tmp_path):
    settings = _Settings(tmp_path)
    blob = perfil.encrypt_secret("mi-secreto", settings)
    assert blob and "mi-secreto" not in blob
    assert perfil.decrypt_secret(blob, settings) == "mi-secreto"


def test_cifrado_no_es_determinista(tmp_path):
    settings = _Settings(tmp_path)
    assert perfil.encrypt_secret("igual", settings) != perfil.encrypt_secret("igual", settings)


def test_clave_maestra_con_permisos_0600(tmp_path):
    settings = _Settings(tmp_path)
    perfil.load_master_key(settings)
    ruta = perfil.master_key_path(settings)
    assert ruta.is_file()
    assert len(ruta.read_bytes()) == perfil.MASTER_KEY_BYTES
    if os.name != "nt":  # los permisos POSIX solo se comprueban en Linux
        assert (ruta.stat().st_mode & 0o777) == 0o600


def test_secreto_corrupto_no_revienta(tmp_path):
    settings = _Settings(tmp_path)
    assert perfil.decrypt_secret("no-es-base64!!", settings) == ""
    assert perfil.decrypt_secret("", settings) == ""


def test_clave_maestra_distinta_no_descifra(tmp_path):
    settings_a = _Settings(tmp_path / "a")
    settings_b = _Settings(tmp_path / "b")
    blob = perfil.encrypt_secret("secreto", settings_a)
    assert perfil.decrypt_secret(blob, settings_b) == ""


def test_cifrado_vacio(tmp_path):
    settings = _Settings(tmp_path)
    assert perfil.encrypt_secret("", settings) == ""
    assert perfil.decrypt_secret("", settings) == ""


# ---------------------------------------------------------------------------
# Generacion de Lua
# ---------------------------------------------------------------------------
def test_render_newcamd():
    lua = perfil.render_newcamd(PERFIL)
    assert lua.startswith("newcamd_main = newcamd({")
    assert 'host = "192.168.1.50"' in lua
    assert "port = 15000" in lua
    assert 'user = "usuario"' in lua
    assert "disable_emm = 1" in lua
    assert "timeout = 8" in lua
    assert lua.rstrip().endswith("})")


def test_render_newcamd_con_disable_emm_cero():
    perfil_a = perfil.NewcamdProfile(**{**PERFIL.__dict__, "disable_emm": False})
    assert "disable_emm = 0" in perfil.render_newcamd(perfil_a)


def test_lua_escapa_comillas_y_barras():
    assert perfil.lua_string('a"b') == '"a\\"b"'
    assert perfil.lua_string("a\\b") == '"a\\\\b"'


@pytest.mark.parametrize("malo", ["con\nsalto", "con\x00nulo", "con\rretorno"])
def test_lua_rechaza_caracteres_de_control(malo):
    with pytest.raises(perfil.ProfileError):
        perfil.lua_string(malo)


def test_render_service():
    lua = perfil.render_service("A&E HD", "panel_tuner_1", 101, "udp://192.168.1.201:1234", "newcamd_main")
    assert "make_channel({" in lua
    assert 'name = "A&E HD"' in lua
    assert "dvb://panel_tuner_1#pnr=101&cam=newcamd_main" in lua
    assert "udp://192.168.1.201:1234" in lua


def test_render_service_sin_cam():
    lua = perfil.render_service("Canal", "tuner_1", 5, "udp://127.0.0.1:1234")
    assert "dvb://tuner_1#pnr=5" in lua
    assert "cam=" not in lua


@pytest.mark.parametrize("pnr", [0, 70000, -1])
def test_render_service_pnr_invalido(pnr):
    with pytest.raises(perfil.ProfileError):
        perfil.render_service("Canal", "tuner_1", pnr, "udp://127.0.0.1:1234")


def test_render_config_completo():
    lua = perfil.render_config(
        [PERFIL],
        [
            {
                "name": "A&E HD",
                "tuner_name": "panel_tuner_1",
                "pnr": 101,
                "output_url": "udp://192.168.1.201:1234",
                "newcamd_profile": "newcamd_main",
            }
        ],
    )
    assert "newcamd_main = newcamd({" in lua
    assert "make_channel({" in lua
    assert "cam=newcamd_main" in lua
    assert lua.startswith("-- Generado por TBS WebUI")


# ---------------------------------------------------------------------------
# Aplicacion atomica y rollback
# ---------------------------------------------------------------------------
def test_apply_y_rollback(tmp_path):
    destino = tmp_path / "stream.lua"
    destino.write_text("-- configuracion original\n", encoding="utf-8")

    resultado = perfil.apply_config(destino, "-- nueva configuracion\n")
    assert resultado.ok is True
    assert resultado.backup is not None
    assert "nueva configuracion" in destino.read_text(encoding="utf-8")

    vuelta = perfil.rollback_config(destino)
    assert vuelta.ok is True
    assert "original" in destino.read_text(encoding="utf-8")


def test_apply_permisos_0600(tmp_path):
    destino = tmp_path / "stream.lua"
    assert perfil.apply_config(destino, "-- contenido\n").ok is True
    if os.name != "nt":
        assert (destino.stat().st_mode & 0o777) == 0o600


def test_rollback_sin_copia(tmp_path):
    resultado = perfil.rollback_config(tmp_path / "no-existe.lua")
    assert resultado.ok is False
    assert "copia" in resultado.detail.lower()


def test_no_queda_fichero_temporal(tmp_path):
    destino = tmp_path / "stream.lua"
    perfil.apply_config(destino, "-- contenido\n")
    assert not list(tmp_path.glob("*.tmp"))


# ---------------------------------------------------------------------------
# Comprobacion de conectividad
# ---------------------------------------------------------------------------
def test_test_tcp_connection_ok():
    import socket

    servidor = socket.socket()
    servidor.bind(("127.0.0.1", 0))
    servidor.listen(1)
    puerto = servidor.getsockname()[1]
    try:
        resultado = perfil.test_tcp_connection("127.0.0.1", puerto, timeout=2)
        assert resultado["reachable"] is True
        assert "latency_ms" in resultado
    finally:
        servidor.close()


def test_test_tcp_connection_falla():
    resultado = perfil.test_tcp_connection("127.0.0.1", 1, timeout=1)
    assert resultado["reachable"] is False
    assert "error" in resultado


def test_test_tcp_connection_host_invalido():
    assert perfil.test_tcp_connection("no vale!", 5000)["reachable"] is False
    assert perfil.test_tcp_connection("127.0.0.1", 0)["reachable"] is False


# ---------------------------------------------------------------------------
# Bloque cam del motor y asignacion por canal (modelo del panel que funciona)
# ---------------------------------------------------------------------------
def test_cam_block_desde_perfil():
    cam = perfil.cam_block(PERFIL)
    assert cam["host"] == "192.168.1.50"
    assert cam["port"] == 15000
    assert cam["user"] == "usuario"
    assert cam["pass"] == "secreto"
    assert cam["key"] == "0102030405060708091011121314"
    assert cam["disable_emm"] == 1
    assert cam["softcam"] == "newcamd_main"


def test_cam_block_sin_disable_emm():
    perfil_a = perfil.NewcamdProfile(**{**PERFIL.__dict__, "disable_emm": False})
    assert "disable_emm" not in perfil.cam_block(perfil_a)


def test_apply_cam_a_servicio():
    servicio = {
        "name": "A&E HD",
        "inputs": [{"type": "dvb", "tuner": "tuner_1", "options": {"pnr": 101, "ecm_pid": 999}}],
        "outputs": [{"type": "udp", "url": "udp://192.168.1.201:1234"}],
    }
    resultado = perfil.apply_cam_to_service(servicio, perfil.cam_block(PERFIL))
    entrada = resultado["inputs"][0]
    assert entrada["options"]["pnr"] == 101
    assert entrada["options"]["cam"]["softcam"] == "newcamd_main"
    # Nunca se fija ecm_pid: el motor se saltaria la PMT
    assert "ecm_pid" not in entrada["options"]
    # No se muta el servicio original
    assert "cam" not in servicio["inputs"][0]["options"]


def test_apply_cam_none_lo_quita():
    servicio = {"inputs": [{"type": "dvb", "tuner": "t", "options": {"pnr": 1, "cam": {"user": "x"}}}]}
    resultado = perfil.apply_cam_to_service(servicio, None)
    assert "cam" not in resultado["inputs"][0]["options"]


def test_service_dvb_url_con_cam():
    servicio = {
        "inputs": [
            {
                "type": "dvb",
                "tuner": "panel_tuner_1",
                "options": {"pnr": 101, "cam": {"softcam": "newcamd_main"}},
            }
        ]
    }
    url = perfil.service_dvb_url(servicio)
    assert url == "dvb://panel_tuner_1#pnr=101&cam=newcamd_main"


def test_service_dvb_url_sin_dvb():
    assert perfil.service_dvb_url({"inputs": [{"type": "udp"}]}) is None
