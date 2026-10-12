"""Configuración central de la aplicación.

Los valores se toman de **variables de entorno** (prefijo `TBS_`) y de un
fichero `.env` opcional situado en la raíz del proyecto. La lectura se hace de
forma explícita para que el comportamiento sea idéntico en cualquier entorno y
no dependa de terceros.

**Ningún secreto vive en el código**: la clave de firma de sesión se genera en
el primer arranque y se guarda con permisos 0600 en el directorio de datos.

Variables reconocidas (todas opcionales):

    TBS_HOST, TBS_PORT, TBS_ROOT_PATH
    TBS_DATA_DIR, TBS_DATABASE_URL
    TBS_SESSION_SECRET, TBS_SESSION_COOKIE, TBS_CSRF_COOKIE,
    TBS_SESSION_MAX_AGE, TBS_SECURE_COOKIES
    TBS_ADMIN_USER, TBS_ADMIN_PASSWORD, TBS_ALLOW_SETUP
    TBS_LOGIN_MAX_ATTEMPTS, TBS_LOGIN_WINDOW_SECONDS, TBS_LOGIN_LOCK_SECONDS
    TBS_MOCK, TBS_DEFAULT_ADAPTER, TBS_DEFAULT_FRONTEND
    TBS_SCAN_TABLES_DIR, TBS_DVBV5_SCAN_BIN, TBS_DVBV5_ZAP_BIN, TBS_DVB_FE_TOOL_BIN
    TBS_DEVICE_LOCK_TIMEOUT, TBS_SIGNAL_POLL_MS, TBS_HISTORY_SECONDS, TBS_LOCK_TIMEOUT_S
    TBS_LNB_LOW_KHZ, TBS_LNB_HIGH_KHZ, TBS_LNB_THRESHOLD_KHZ
    TBS_LOG_LEVEL
"""

from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path

# Raíz del proyecto = .../tbs-webui  (este fichero está en app/config.py)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PREFIX = "TBS_"

_TRUE_VALUES = {"1", "true", "yes", "y", "on", "si", "sí"}
_FALSE_VALUES = {"0", "false", "no", "n", "off"}


def _read_env_file(path: Path) -> dict[str, str]:
    """Lee un `.env` sencillo (CLAVE=valor, comillas opcionales)."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _env(name: str, default: str | None = None, env_file: dict[str, str] | None = None) -> str | None:
    """Variable de entorno con fallback al `.env` (entorno gana)."""
    key = f"{ENV_PREFIX}{name}"
    if key in os.environ:
        return os.environ[key]
    if env_file and key in env_file:
        return env_file[key]
    return default


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    lowered = value.strip().lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    return default


def _as_int(value: str | None, default: int) -> int:
    if value is None or not str(value).strip():
        return default
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _as_float(value: str | None, default: float) -> float:
    if value is None or not str(value).strip():
        return default
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _as_path(value: str | None, default: Path) -> Path:
    if value is None or not str(value).strip():
        return default
    return Path(str(value).strip()).expanduser()


def _default_data_dir() -> Path:
    """Directorio de datos: XDG_DATA_HOME o ./data dentro del proyecto."""
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "tbs-webui"
    return PROJECT_ROOT / "data"


class Settings:
    """Ajustes de la aplicación (entorno > .env > valores por defecto)."""

    def __init__(self, env_file: Path | None = None) -> None:
        self.project_root = PROJECT_ROOT
        env_values = _read_env_file(env_file or (PROJECT_ROOT / ".env"))
        get = lambda name, default=None: _env(name, default, env_values)  # noqa: E731

        # --- Red ---
        self.host = str(get("HOST", "0.0.0.0"))
        self.port = min(65535, max(1, _as_int(get("PORT"), 9000)))
        self.root_path = str(get("ROOT_PATH", "") or "")

        # --- Datos ---
        self.data_dir = _as_path(get("DATA_DIR"), _default_data_dir())
        self.database_url = get("DATABASE_URL") or None

        # --- Seguridad ---
        self.session_secret = get("SESSION_SECRET") or None
        self.session_cookie = str(get("SESSION_COOKIE", "tbs_session"))
        self.csrf_cookie = str(get("CSRF_COOKIE", "tbs_csrf"))
        self.session_max_age = max(60, _as_int(get("SESSION_MAX_AGE"), 12 * 3600))
        self.secure_cookies = _as_bool(get("SECURE_COOKIES"), False)
        # Sólo confiar en X-Forwarded-For cuando hay un proxy inverso de confianza.
        self.trust_proxy_headers = _as_bool(get("TRUST_PROXY_HEADERS"), False)
        self.admin_user = str(get("ADMIN_USER", "admin") or "admin")
        self.admin_password = get("ADMIN_PASSWORD") or None
        self.allow_setup = _as_bool(get("ALLOW_SETUP"), True)

        # --- Límite de intentos de login ---
        self.login_max_attempts = max(1, _as_int(get("LOGIN_MAX_ATTEMPTS"), 5))
        self.login_window_seconds = max(10, _as_int(get("LOGIN_WINDOW_SECONDS"), 300))
        self.login_lock_seconds = max(10, _as_int(get("LOGIN_LOCK_SECONDS"), 300))

        # --- DVB ---
        self.mock = _as_bool(get("MOCK"), False)
        self.default_adapter = max(0, _as_int(get("DEFAULT_ADAPTER"), 0))
        self.default_frontend = max(0, _as_int(get("DEFAULT_FRONTEND"), 0))
        self.scan_tables_dir = _as_path(get("SCAN_TABLES_DIR"), Path("/usr/share/dvb"))
        self.dvbv5_scan_bin = str(get("DVBV5_SCAN_BIN", "dvbv5-scan"))
        self.dvbv5_zap_bin = str(get("DVBV5_ZAP_BIN", "dvbv5-zap"))
        self.dvb_fe_tool_bin = str(get("DVB_FE_TOOL_BIN", "dvb-fe-tool"))
        self.device_lock_timeout = max(1.0, _as_float(get("DEVICE_LOCK_TIMEOUT"), 120.0))

        # --- UI / sondeo ---
        self.signal_poll_ms = min(5000, max(100, _as_int(get("SIGNAL_POLL_MS"), 500)))
        self.history_seconds = min(600, max(10, _as_int(get("HISTORY_SECONDS"), 60)))
        self.lock_timeout_s = max(0.5, _as_float(get("LOCK_TIMEOUT_S"), 5.0))

        # --- LNB por defecto (universal) ---
        self.lnb_low_khz = _as_int(get("LNB_LOW_KHZ"), 9750)
        self.lnb_high_khz = _as_int(get("LNB_HIGH_KHZ"), 10600)
        self.lnb_threshold_khz = _as_int(get("LNB_THRESHOLD_KHZ"), 11700)

        # --- Newcamd (card sharing propio o autorizado) ---
        self.newcamd_enabled = _as_bool(get("NEWCAMD_ENABLED"), True)
        self.newcamd_timeout_s = max(1.0, _as_float(get("NEWCAMD_TIMEOUT_S"), 8.0))
        self.newcamd_auto_connect = _as_bool(get("NEWCAMD_AUTO_CONNECT"), False)

        # --- Transponder favorito (botón de sintonización rápida) ---
        self.favourite_adapter = max(0, _as_int(get("FAVOURITE_ADAPTER"), 0))
        self.favourite_frequency_khz = _as_int(get("FAVOURITE_FREQUENCY_KHZ"), 10970000)
        self.favourite_symbol_rate_ksps = _as_int(get("FAVOURITE_SYMBOL_RATE_KSPS"), 27900)
        self.favourite_polarization = str(get("FAVOURITE_POLARIZATION", "V") or "V").upper()[:1]

        self.log_level = str(get("LOG_LEVEL", "INFO") or "INFO").upper()

    # ------------------------------------------------------------------
    # Rutas derivadas
    # ------------------------------------------------------------------
    @property
    def db_path(self) -> Path:
        """Ruta del fichero SQLite (si se usa SQLite)."""
        return self.data_dir / "tbs-webui.db"

    def resolved_database_url(self) -> str:
        return self.database_url or f"sqlite:///{self.db_path}"

    @property
    def secret(self) -> str:
        """Clave de firma de sesión, generada y persistida si no existe."""
        if self.session_secret:
            return self.session_secret
        return _load_or_create_secret(self.data_dir)

    def prepare_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.data_dir, 0o700)
        except OSError:
            pass

    def is_mock(self) -> bool:
        """Modo mock explícito o implícito (sin /dev/dvb en el sistema)."""
        if self.mock:
            return True
        return not Path("/dev/dvb").is_dir()

    def mask_secrets(self) -> dict[str, object]:
        """Resumen seguro para logs (sin valores sensibles)."""
        return {
            "host": self.host,
            "port": self.port,
            "data_dir": str(self.data_dir),
            "database": "sqlite" if self.resolved_database_url().startswith("sqlite") else "externo",
            "mock": self.is_mock(),
            "session_secret_source": "env" if self.session_secret else "fichero",
            "admin_password_source": "env" if self.admin_password else "asistente/aleatoria",
            "log_level": self.log_level,
        }


def _load_or_create_secret(data_dir: Path) -> str:
    """Lee o crea `<data_dir>/secret.key` con permisos 0600."""
    data_dir.mkdir(parents=True, exist_ok=True)
    secret_file = data_dir / "secret.key"
    if secret_file.is_file():
        value = secret_file.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = secrets.token_urlsafe(48)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(secret_file, flags, 0o600)  # se crea ya con 0600
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(value + "\n")
    try:
        os.chmod(secret_file, 0o600)
    except OSError:
        pass
    return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Instancia única de ajustes (cacheada)."""
    settings = Settings()
    settings.prepare_dirs()
    return settings
