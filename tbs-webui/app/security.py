"""Seguridad: hash de contraseñas (argon2), sesiones firmadas, CSRF y límite de intentos.

Decisiones:
- Contraseñas con **argon2id** (`argon2-cffi`). Nunca se guardan en claro.
- Sesión en cookie **HttpOnly + SameSite=Lax** (+ Secure si `TBS_SECURE_COOKIES=1`),
  con payload firmado por `itsdangerous` (no se puede falsificar sin la clave).
- CSRF con patrón *double-submit*: cookie legible `tbs_csrf` + cabecera `X-CSRF-Token`.
- Límite de intentos fallidos por usuario+IP con ventana temporal y bloqueo.
"""

from __future__ import annotations

import hmac
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .models import LoginAttempt, User, utcnow

logger = logging.getLogger(__name__)

# Parámetros por defecto de argon2id (recomendados por OWASP 2024).
_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,  # 64 MiB
    parallelism=4,
    hash_len=32,
    salt_len=16,
)

SESSION_SALT = "tbs-webui.session.v1"
CSRF_SALT = "tbs-webui.csrf.v1"


# ---------------------------------------------------------------------------
# Contraseñas
# ---------------------------------------------------------------------------
def hash_password(password: str) -> str:
    """Devuelve el hash argon2id de la contraseña."""
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verifica la contraseña contra el hash (False si no coincide o está corrupto)."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    except Exception:  # pragma: no cover - defensivo
        logger.exception("Error verificando contraseña")
        return False


def needs_rehash(password_hash: str) -> bool:
    """True si el hash usa parámetros antiguos y conviene regenerarlo."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Sesiones firmadas
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SessionData:
    user_id: int
    username: str
    issued_at: int
    nonce: str
    epoch: int = 0


def _serializer(settings: Settings, salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret, salt=salt, serializer=None)


def create_session_token(user: User, settings: Settings | None = None) -> str:
    """Crea el token de sesión firmado para un usuario."""
    settings = settings or get_settings()
    payload = {
        "uid": user.id,
        "u": user.username,
        "iat": int(time.time()),
        "n": secrets.token_urlsafe(8),
        # Época de sesión: permite revocar en servidor (logout / cambio de clave).
        "e": int(getattr(user, "session_epoch", 0) or 0),
    }
    return _serializer(settings, SESSION_SALT).dumps(payload)


def read_session_token(token: str | None, settings: Settings | None = None) -> SessionData | None:
    """Valida y decodifica el token de sesión (None si es inválido o ha caducado)."""
    if not token:
        return None
    settings = settings or get_settings()
    try:
        data = _serializer(settings, SESSION_SALT).loads(token, max_age=settings.session_max_age)
    except SignatureExpired:
        logger.debug("Sesión caducada")
        return None
    except BadSignature:
        logger.debug("Firma de sesión inválida")
        return None
    except Exception:  # pragma: no cover - defensivo
        return None
    if not isinstance(data, dict) or "uid" not in data:
        return None
    return SessionData(
        user_id=int(data["uid"]),
        username=str(data.get("u", "")),
        issued_at=int(data.get("iat", 0)),
        nonce=str(data.get("n", "")),
        epoch=int(data.get("e", 0) or 0),
    )


def session_is_current(token_data: SessionData | None, user: User | None) -> bool:
    """True si la sesión sigue vigente (época sin revocar)."""
    if token_data is None or user is None:
        return False
    return int(getattr(user, "session_epoch", 0) or 0) == int(token_data.epoch)


def revoke_sessions(user: User) -> None:
    """Invalida todas las sesiones emitidas para el usuario."""
    user.session_epoch = int(getattr(user, "session_epoch", 0) or 0) + 1


# ---------------------------------------------------------------------------
# CSRF (double submit cookie)
# ---------------------------------------------------------------------------
def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_matches(cookie_token: str | None, header_token: str | None) -> bool:
    """Compara el token de la cookie con el de la cabecera en tiempo constante."""
    if not cookie_token or not header_token:
        return False
    return hmac.compare_digest(cookie_token, header_token)


# ---------------------------------------------------------------------------
# Límite de intentos de login
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ThrottleState:
    blocked: bool
    failed_attempts: int
    retry_after_s: int


def check_login_throttle(
    session: Session, username: str, ip: str | None, settings: Settings | None = None
) -> ThrottleState:
    """Cuenta fallos recientes y decide si el login está bloqueado."""
    settings = settings or get_settings()
    window_start = utcnow() - timedelta(seconds=settings.login_window_seconds)

    conditions = [LoginAttempt.success.is_(False), LoginAttempt.attempted_at >= window_start]
    # Se limita tanto por usuario como por IP de origen.
    if ip:
        conditions.append((LoginAttempt.username == username) | (LoginAttempt.ip_address == ip))
    else:
        conditions.append(LoginAttempt.username == username)

    stmt = select(func.count(LoginAttempt.id)).where(*conditions)
    failed = int(session.execute(stmt).scalar() or 0)

    if failed < settings.login_max_attempts:
        return ThrottleState(blocked=False, failed_attempts=failed, retry_after_s=0)

    # Bloqueo desde el último intento fallido.
    last_stmt = select(func.max(LoginAttempt.attempted_at)).where(*conditions)
    last_attempt = session.execute(last_stmt).scalar()
    if last_attempt is None:
        return ThrottleState(blocked=False, failed_attempts=failed, retry_after_s=0)

    unlock_at = last_attempt + timedelta(seconds=settings.login_lock_seconds)
    remaining = (unlock_at - utcnow()).total_seconds()
    if remaining <= 0:
        return ThrottleState(blocked=False, failed_attempts=failed, retry_after_s=0)
    return ThrottleState(blocked=True, failed_attempts=failed, retry_after_s=int(remaining) + 1)


def record_login_attempt(session: Session, username: str, ip: str | None, success: bool) -> None:
    """Registra un intento de login y poda los antiguos."""
    session.add(LoginAttempt(username=username[:64], ip_address=(ip or "")[:64] or None, success=success))
    cutoff = utcnow() - timedelta(days=7)
    session.execute(delete(LoginAttempt).where(LoginAttempt.attempted_at < cutoff))
    session.commit()


def reset_login_attempts(session: Session, username: str, ip: str | None) -> None:
    """Borra los fallos acumulados tras un login correcto."""
    conditions = [LoginAttempt.success.is_(False)]
    if ip:
        conditions.append((LoginAttempt.username == username) | (LoginAttempt.ip_address == ip))
    else:
        conditions.append(LoginAttempt.username == username)
    session.execute(delete(LoginAttempt).where(*conditions))
    session.commit()


# ---------------------------------------------------------------------------
# Usuarios
# ---------------------------------------------------------------------------
def user_count(session: Session) -> int:
    return int(session.execute(select(func.count(User.id))).scalar() or 0)


def get_user_by_name(session: Session, username: str) -> User | None:
    return session.execute(select(User).where(User.username == username)).scalar_one_or_none()


def create_user(session: Session, username: str, password: str, is_admin: bool = True) -> User:
    user = User(
        username=username.strip(),
        password_hash=hash_password(password),
        is_admin=is_admin,
        created_at=utcnow(),
        password_changed_at=utcnow(),
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def set_password(session: Session, user: User, new_password: str) -> None:
    user.password_hash = hash_password(new_password)
    user.password_changed_at = utcnow()
    session.commit()


def bootstrap_admin(session: Session, settings: Settings | None = None) -> User | None:
    """Crea el admin inicial **sólo** si se ha definido `TBS_ADMIN_PASSWORD`.

    Así el primer arranque funciona de las dos maneras previstas por el diseño:

    * con `TBS_ADMIN_PASSWORD` definida -> se crea el usuario directamente
      (útil para despliegues automatizados; conviene borrar la variable después);
    * sin ella -> **no se crea ningún usuario** y el panel ofrece el asistente
      web de primer arranque en `/login` (POST `/api/auth/setup`), que es la
      forma recomendada porque la contraseña no queda en el entorno ni en el log.

    Nunca se generan contraseñas aleatorias en el log (evita credenciales
    efímeras en `journalctl`).
    """
    settings = settings or get_settings()
    if user_count(session) > 0:
        return None

    password = settings.admin_password
    if not password:
        logger.info(
            "No hay usuarios y TBS_ADMIN_PASSWORD no está definida: entra en /login "
            "para crear el administrador con el asistente de primer arranque."
        )
        return None

    username = (settings.admin_user or "admin").strip() or "admin"
    user = create_user(session, username, password, is_admin=True)
    logger.info(
        "Usuario administrador '%s' creado desde TBS_ADMIN_PASSWORD "
        "(borra esa variable del entorno tras el primer arranque)",
        username,
    )
    return user


def humanize_seconds(seconds: int) -> str:
    """Formatea segundos como '2 min 5 s' para los mensajes de bloqueo."""
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    if minutes:
        return f"{minutes} min {secs} s"
    return f"{secs} s"
