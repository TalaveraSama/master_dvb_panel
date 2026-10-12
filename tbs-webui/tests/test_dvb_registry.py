"""Tests de `app.dvb.registry` (descubrimiento, factoría y lock global)."""

from __future__ import annotations

import pathlib
import sys
import threading
import time

_WEBUI = pathlib.Path(__file__).resolve().parents[1]
if str(_WEBUI) not in sys.path:
    sys.path.insert(0, str(_WEBUI))

import pytest  # noqa: E402

from app.dvb import registry  # noqa: E402
from app.dvb import types as t  # noqa: E402


# ---------------------------------------------------------------------------
# Modo mock y descubrimiento
# ---------------------------------------------------------------------------
def test_is_mock_mode_con_variable_de_entorno(monkeypatch):
    for valor in ("1", "true", "YES", "on", "sí"):
        monkeypatch.setenv("TBS_MOCK", valor)
        assert registry.is_mock_mode() is True
    monkeypatch.setenv("TBS_MOCK", "0")
    # Sin /dev/dvb (Windows o host sin tarjeta) sigue siendo mock.
    if not pathlib.Path("/dev/dvb").exists():
        assert registry.is_mock_mode() is True


def test_list_frontends_no_peta_sin_dev_dvb():
    infos = registry.list_frontends()
    assert isinstance(infos, list)
    assert infos, "en modo mock debe devolver los 4 frontends simulados"
    assert {f.adapter for f in infos} == {0, 1, 2, 3}
    for info in infos:
        assert info.delivery_systems == ("DVB-S", "DVB-S2")
        assert info.device_path.startswith("/dev/dvb/adapter")


def test_list_frontends_force_mock():
    infos = registry.list_frontends(force_mock=True)
    assert len(infos) == 4


def test_list_frontends_real_sin_hardware_devuelve_lista_vacia():
    if pathlib.Path("/dev/dvb").exists():
        pytest.skip("host con hardware DVB real")
    assert registry.list_frontends(force_mock=False) == []


def test_iter_device_paths_no_lanza():
    paths = list(registry.iter_device_paths())
    if not pathlib.Path("/dev/dvb").exists():
        assert paths == []
    for adapter, frontend, path in paths:
        assert isinstance(adapter, int) and isinstance(frontend, int)
        assert path.startswith("/dev/dvb/adapter")


def test_device_path():
    assert registry.device_path(2, 1) == "/dev/dvb/adapter2/frontend1"
    assert registry.device_path(0) == "/dev/dvb/adapter0/frontend0"


def test_sysfs_name_sin_sysfs():
    if pathlib.Path("/sys/class/dvb").exists():
        pytest.skip("host con sysfs DVB")
    assert registry.sysfs_name(0, 0) is None


def test_open_frontend_en_mock(monkeypatch):
    from app.dvb.mock import MockFrontend

    monkeypatch.setenv("TBS_MOCK", "1")
    frontend = registry.open_frontend(1, 0)
    assert isinstance(frontend, MockFrontend)
    assert frontend.info.adapter == 1
    frontend.open()
    assert frontend.is_open is True
    frontend.close()


def test_open_frontend_real_sin_dispositivo(monkeypatch):
    monkeypatch.setattr(registry, "is_mock_mode", lambda: False)
    with pytest.raises(t.DeviceNotFoundError):
        registry.open_frontend(9, 0)


def test_find_frontend_info_en_mock(monkeypatch):
    monkeypatch.setenv("TBS_MOCK", "1")
    info = registry.find_frontend_info(3, 0)
    assert info.adapter == 3
    assert "mock" in info.name


# ---------------------------------------------------------------------------
# DeviceLock
# ---------------------------------------------------------------------------
def test_lock_acquire_release():
    lock = registry.DeviceLock()
    assert lock.owner is None
    assert lock.acquire("api") is True
    assert lock.owner == "api"
    assert lock.is_locked is True
    lock.release("api")
    assert lock.owner is None
    lock.release("api")  # idempotente


def test_lock_ocupado_lanza_device_busy():
    lock = registry.DeviceLock()
    lock.acquire("tune")
    with pytest.raises(t.DeviceBusyError) as exc:
        lock.acquire("scan")
    assert "tune" in str(exc.value)
    lock.release("tune")


def test_lock_try_acquire():
    lock = registry.DeviceLock()
    assert lock.try_acquire("a") is True
    assert lock.try_acquire("b") is False
    lock.release("a")
    assert lock.try_acquire("b") is True


def test_lock_timeout_espera_y_falla():
    lock = registry.DeviceLock()
    lock.acquire("dueño")
    inicio = time.monotonic()
    with pytest.raises(t.DeviceBusyError):
        lock.acquire("otro", timeout=0.2)
    transcurrido = time.monotonic() - inicio
    assert transcurrido >= 0.19
    assert transcurrido < 2.0
    lock.release("dueño")


def test_lock_reentrante_mismo_dueño():
    lock = registry.DeviceLock()
    lock.acquire("api")
    lock.acquire("api")
    assert lock.snapshot()["depth"] == 2
    lock.release("api")
    assert lock.owner == "api"  # sigue tomado
    lock.release("api")
    assert lock.owner is None


def test_lock_release_de_otro_dueño_falla():
    lock = registry.DeviceLock()
    lock.acquire("api")
    with pytest.raises(t.DvbError):
        lock.release("scan")
    assert lock.owner == "api"
    lock.release("api")


def test_lock_snapshot_tiene_las_claves_del_contrato():
    lock = registry.DeviceLock()
    snap = lock.snapshot()
    assert set(snap) >= {"owner", "acquired_at", "waiting"}
    assert snap["owner"] is None and snap["waiting"] == 0
    lock.acquire("api")
    snap = lock.snapshot()
    assert snap["owner"] == "api"
    assert snap["acquired_at"] is not None
    assert snap["locked"] is True
    lock.release("api")


def test_lock_espera_a_que_el_dueño_libere():
    lock = registry.DeviceLock()
    lock.acquire("primero")
    resultado: dict[str, object] = {}

    def worker() -> None:
        try:
            resultado["ok"] = lock.acquire("segundo", timeout=3.0)
            resultado["owner"] = lock.owner
        except Exception as exc:  # pragma: no cover - no debería ocurrir
            resultado["error"] = exc
        finally:
            if lock.owner == "segundo":
                lock.release("segundo")

    hilo = threading.Thread(target=worker)
    hilo.start()
    tiempo_limite = time.monotonic() + 2.0
    while lock.waiting == 0 and time.monotonic() < tiempo_limite:
        time.sleep(0.02)
    assert lock.waiting == 1, "el segundo hilo debe quedar en espera"
    assert lock.snapshot()["waiting"] == 1
    lock.release("primero")
    hilo.join(timeout=3.0)
    assert resultado.get("ok") is True
    assert resultado.get("owner") == "segundo"
    assert lock.owner is None  # el worker lo libera


def test_lock_hold_context_manager():
    lock = registry.DeviceLock()
    with lock.hold("api"):
        assert lock.owner == "api"
    assert lock.owner is None
    lock.acquire("otro")
    try:
        with pytest.raises(t.DeviceBusyError):
            with lock.hold("api", timeout=0.1):
                raise AssertionError("no debería entrar")
    finally:
        lock.release("otro")
    assert lock.owner is None


def test_lock_force_release():
    lock = registry.DeviceLock()
    lock.acquire("api")
    lock.force_release()
    assert lock.owner is None
    assert lock.acquire("otro") is True
    lock.release("otro")


def test_get_lock_es_singleton_y_reset_lo_cambia():
    primero = registry.get_lock()
    assert registry.get_lock() is primero
    segundo = registry.reset_lock()
    try:
        assert segundo is not primero
        assert registry.get_lock() is segundo
    finally:
        registry.reset_lock()


def test_owned_by():
    lock = registry.DeviceLock()
    lock.acquire("api")
    assert lock.owned_by("api") is True
    assert lock.owned_by("otro") is False
    lock.release("api")
