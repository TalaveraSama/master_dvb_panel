"""Fixtures de tests: entorno aislado y stub del registro DVB.

IMPORTANTE: las variables de entorno se fijan **antes** de importar `app`, ya
que la configuración se cachea en el primer import.
"""

from __future__ import annotations

import math
import os
import sys
import tempfile
import time
import types
from pathlib import Path

import pytest

# .../tbs-webui/tests/conftest.py -> .../tbs-webui (raíz del paquete `app`)
APP_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = APP_DIR.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# --- Entorno de test fijado antes de importar la aplicación ---
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="tbs-webui-tests-"))
os.environ["TBS_MOCK"] = "1"
os.environ["TBS_DATA_DIR"] = str(_TMP_ROOT)
os.environ["TBS_ADMIN_USER"] = "admin"
os.environ["TBS_DATABASE_URL"] = f"sqlite:///{_TMP_ROOT / 'test.db'}"
os.environ["TBS_LOG_LEVEL"] = "WARNING"


# ---------------------------------------------------------------------------
# Stub de app/dvb/registry.py (sólo si el módulo real no está disponible)
# ---------------------------------------------------------------------------
def _install_registry_stub() -> None:
    try:
        from app.dvb import registry  # noqa: F401

        return
    except Exception:
        pass

    from app.dvb import types as dvb_types
    from app.dvb.base import DeviceBusyError, DeviceNotFoundError, FrontendError

    class _FakeFrontend:
        is_mock = True

        def __init__(self, info: dvb_types.FrontendInfo) -> None:
            self.info = info
            self.opened = False
            self.tuned = None
            self.tone = False
            self.voltage = 0
            self.diseqc: list[bytes] = []

        def open(self) -> None:
            self.opened = True

        def close(self) -> None:
            self.opened = False

        def tune(self, params) -> None:
            self.tuned = params

        def wait_lock(self, timeout_s: float = 5.0) -> bool:
            return True

        def read_status(self):
            return dvb_types.LockFlags(True, True, True, True, True)

        def read_stats(self):
            base = 70.0 + 5.0 * math.sin(time.time())
            return dvb_types.SignalStats(
                timestamp=time.time(),
                lock=self.read_status(),
                signal_percent=base,
                signal_dbm=-42.0,
                snr_db=12.5 + 0.5 * math.cos(time.time()),
                cnr_raw=1250.0,
                ber=0.0,
                ber_pre=0.0,
                per=0.0,
                ucblocks=0,
                quality_percent=base - 5.0,
            )

        def set_tone(self, on: bool) -> None:
            self.tone = bool(on)

        def set_voltage(self, volts: int) -> None:
            self.voltage = int(volts)

        def send_diseqc(self, cmd: bytes, repeats: int = 0) -> None:
            self.diseqc.append(bytes(cmd))

        def mock_services(self, transponder=None):
            return [
                dvb_types.ServiceInfo(
                    service_id=101,
                    name="Test TV",
                    provider="Proveedor",
                    service_type=1,
                    service_type_label="TV",
                    is_fta=True,
                    scrambled=False,
                    video_pid=512,
                    audio_pid=650,
                    pcr_pid=512,
                    pmt_pid=100,
                ),
                dvb_types.ServiceInfo(
                    service_id=102,
                    name="Test Radio",
                    provider="Proveedor",
                    service_type=2,
                    service_type_label="Radio",
                    is_fta=True,
                    scrambled=False,
                    audio_pid=660,
                ),
                dvb_types.ServiceInfo(
                    service_id=103,
                    name="Canal Cifrado",
                    provider="Proveedor",
                    service_type=1,
                    service_type_label="TV",
                    is_fta=False,
                    scrambled=True,
                    video_pid=513,
                    audio_pid=661,
                    ca_system_ids=(0x0500,),
                ),
            ]

    class _FakeLock:
        def __init__(self) -> None:
            self.owner: str | None = None
            self.acquired_at: float | None = None
            self._waiting = 0

        def acquire(self, owner: str, timeout: float | None = None) -> bool:
            if self.owner is not None and self.owner != owner:
                return False
            self.owner = owner
            self.acquired_at = time.time()
            return True

        def release(self, owner: str) -> None:
            if self.owner == owner:
                self.owner = None
                self.acquired_at = None

        def snapshot(self) -> dict:
            return {"owner": self.owner, "acquired_at": self.acquired_at, "waiting": self._waiting}

    lock = _FakeLock()

    def list_frontends(force_mock: bool | None = None):
        return [
            dvb_types.FrontendInfo(
                adapter=0,
                frontend=0,
                name="TBS 6904se (simulado)",
                device_path="/dev/dvb/adapter0/frontend0",
                type="FE_QPSK",
                caps=0,
                delivery_systems=("DVB-S", "DVB-S2"),
                min_frequency=950000,
                max_frequency=2150000,
                symbol_rates=(1000000, 45000000),
            ),
            dvb_types.FrontendInfo(
                adapter=1,
                frontend=0,
                name="TBS 6904se (simulado)",
                device_path="/dev/dvb/adapter1/frontend0",
                type="FE_QPSK",
                caps=0,
                delivery_systems=("DVB-S", "DVB-S2"),
                min_frequency=950000,
                max_frequency=2150000,
                symbol_rates=(1000000, 45000000),
            ),
        ]

    def open_frontend(adapter: int = 0, frontend: int = 0):
        infos = [i for i in list_frontends() if i.adapter == adapter and i.frontend == frontend]
        if not infos:
            raise DeviceNotFoundError(f"No existe {adapter}/{frontend}")
        return _FakeFrontend(infos[0])

    module = types.ModuleType("app.dvb.registry")
    module.list_frontends = list_frontends
    module.open_frontend = open_frontend
    module.get_lock = lambda: lock
    module.is_mock_mode = lambda: True
    module.DeviceBusyError = DeviceBusyError
    module.FrontendError = FrontendError
    sys.modules["app.dvb.registry"] = module

    import app.dvb as dvb_pkg

    dvb_pkg.registry = module  # type: ignore[attr-defined]


_install_registry_stub()

from app.database import get_sessionmaker, init_db  # noqa: E402

# Crea el motor y las tablas en el directorio temporal de tests.
init_db()

TEST_ADMIN = "admin"
TEST_PASSWORD = "contrasena-segura-123"


@pytest.fixture(scope="session", autouse=True)
def admin_user():
    """Crea el usuario de pruebas una sola vez (sin pasar por el asistente)."""
    from app.security import create_user, get_user_by_name

    session = get_sessionmaker()()
    try:
        if get_user_by_name(session, TEST_ADMIN) is None:
            create_user(session, TEST_ADMIN, TEST_PASSWORD, is_admin=True)
        yield
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _clean_database():
    """Cada test arranca con canales, transponders y jobs limpios."""
    from app.models import AppLog, ScanJob, Service, Transponder

    def purge() -> None:
        session = get_sessionmaker()()
        try:
            for model in (Service, Transponder, ScanJob, AppLog):
                session.query(model).delete()
            session.commit()
        finally:
            session.close()

    purge()
    yield
    purge()


@pytest.fixture(autouse=True)
def _reset_throttle():
    """Cada test empieza sin intentos fallidos ni cambios de contraseña previos."""
    from sqlalchemy import delete

    from app.models import LoginAttempt
    from app.security import get_user_by_name, verify_password

    session = get_sessionmaker()()
    try:
        session.execute(delete(LoginAttempt))
        # Restaura la contraseña de pruebas por si un test la cambió.
        from app.security import hash_password

        user = get_user_by_name(session, TEST_ADMIN)
        if user is not None and not verify_password(TEST_PASSWORD, user.password_hash):
            user.password_hash = hash_password(TEST_PASSWORD)
        session.commit()
    finally:
        session.close()
    yield


@pytest.fixture()
def app():
    """Aplicación FastAPI con la BD de test ya inicializada."""
    from app.main import create_app

    return create_app()


@pytest.fixture()
def client(app):
    """Cliente HTTP con ciclo de vida (lifespan) en modo simulación."""
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def device_manager(app):
    """Gestor de dispositivo de la app en pruebas (modo simulación)."""
    from app.config import get_settings
    from app.services.device_manager import DeviceManager

    manager = DeviceManager(get_settings())
    yield manager
    try:
        manager.release()
    except Exception:  # pragma: no cover - cierre defensivo en tests
        pass


@pytest.fixture()
def admin_client(client):
    """Cliente autenticado como administrador, con cabecera CSRF lista."""
    response = client.post(
        "/api/auth/login",
        json={"username": TEST_ADMIN, "password": TEST_PASSWORD},
    )
    assert response.status_code == 200, response.text
    client.headers.update({"X-CSRF-Token": response.json()["csrf_token"]})
    return client


@pytest.fixture()
def anonymous_client(app):
    """Cliente sin sesión (para probar los 401 y el CSRF)."""
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client
