"""Autenticación: primer arranque, login, logout y cambio de contraseña.

- Cookie de sesión **HttpOnly + SameSite=Lax** (y `Secure` si `TBS_SECURE_COOKIES=1`).
- Cookie CSRF legible por JS (`tbs_csrf`) + cabecera `X-CSRF-Token`.
- Límite de intentos fallidos por usuario e IP (bloqueo temporal).
"""

from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from .. import __version__
from ..deps import DbSession, client_ip, get_session_user, require_csrf
from ..models import User
from ..schemas import (
    Credentials,
    LoginOut,
    MeOut,
    OkOut,
    PasswordChange,
    SetupRequest,
)
from ..security import (
    bootstrap_admin,
    check_login_throttle,
    create_session_token,
    create_user,
    get_user_by_name,
    hash_password,
    humanize_seconds,
    new_csrf_token,
    record_login_attempt,
    reset_login_attempts,
    set_password,
    user_count,
    verify_password,
)

logger = logging.getLogger(__name__)

# Hash señuelo (argon2id de un valor aleatorio) para igualar el tiempo de
# respuesta cuando el usuario no existe y evitar enumeración por temporización.
DUMMY_HASH = hash_password(secrets.token_urlsafe(24))

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookies(response: Response, token: str, csrf: str, settings) -> None:
    """Fija las cookies de sesión y CSRF."""
    max_age = settings.session_max_age
    response.set_cookie(
        key=settings.session_cookie,
        value=token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies,
        path="/",
    )
    # La cookie CSRF debe ser legible por JS para el patrón double-submit.
    response.set_cookie(
        key=settings.csrf_cookie,
        value=csrf,
        max_age=max_age,
        httponly=False,
        samesite="lax",
        secure=settings.secure_cookies,
        path="/",
    )


def _clear_session_cookies(response: Response, settings) -> None:
    response.delete_cookie(settings.session_cookie, path="/")
    response.delete_cookie(settings.csrf_cookie, path="/")


@router.get("/me", response_model=MeOut)
def me(request: Request, db: DbSession) -> MeOut:
    """Estado de la sesión actual (público: lo usa la pantalla de login)."""
    settings = request.app.state.settings
    user = get_session_user(request, db)
    must_setup = user_count(db) == 0 and settings.allow_setup
    if user is None:
        return MeOut(
            authenticated=False,
            username=None,
            csrf_token=None,
            must_setup=must_setup,
            mock=settings.is_mock(),
            version=__version__,
        )
    csrf = request.cookies.get(settings.csrf_cookie) or new_csrf_token()
    return MeOut(
        authenticated=True,
        username=user.username,
        csrf_token=csrf,
        must_setup=False,
        mock=settings.is_mock(),
        version=__version__,
    )


@router.post("/setup", response_model=LoginOut)
def setup(payload: SetupRequest, request: Request, response: Response, db: DbSession) -> LoginOut:
    """Asistente de primer arranque: crea el administrador si no hay usuarios."""
    settings = request.app.state.settings
    if not settings.allow_setup:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="El asistente inicial está deshabilitado")
    if user_count(db) > 0:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ya existe un usuario administrador")

    username = payload.username.strip() or "admin"
    user = create_user(db, username, payload.password, is_admin=True)
    token = create_session_token(user, settings)
    csrf = new_csrf_token()
    _set_session_cookies(response, token, csrf, settings)
    logger.info("Administrador '%s' creado desde el asistente web", user.username)
    return LoginOut(ok=True, username=user.username, csrf_token=csrf)


@router.post("/login", response_model=LoginOut)
def login(payload: Credentials, request: Request, response: Response, db: DbSession) -> LoginOut:
    """Login con límite de intentos fallidos."""
    settings = request.app.state.settings
    ip = client_ip(request)
    username = payload.username.strip()

    # Primer arranque: sólo se crea el admin si TBS_ADMIN_PASSWORD está definida;
    # si no, el usuario debe pasar por el asistente (/api/auth/setup).
    if user_count(db) == 0:
        bootstrap_admin(db, settings)

    throttle = check_login_throttle(db, username, ip, settings)
    if throttle.blocked:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=(
                f"Demasiados intentos fallidos. Reintenta en "
                f"{humanize_seconds(throttle.retry_after_s)}."
            ),
            headers={"Retry-After": str(throttle.retry_after_s)},
        )

    user = get_user_by_name(db, username)
    password_ok = verify_password(payload.password, user.password_hash) if user is not None else False
    if user is None:
        # Verificación señuelo para no revelar por tiempo si el usuario existe.
        verify_password(payload.password, DUMMY_HASH)
    if user is None or not password_ok:
        record_login_attempt(db, username, ip, success=False)
        remaining = max(0, settings.login_max_attempts - (throttle.failed_attempts + 1))
        logger.warning("Login fallido para '%s' desde %s (%d intentos restantes)", username, ip, remaining)
        detail = "Usuario o contraseña incorrectos"
        if remaining <= 2:
            detail += f". Te quedan {remaining} intento(s) antes del bloqueo temporal"
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)

    reset_login_attempts(db, username, ip)
    record_login_attempt(db, username, ip, success=True)
    from ..models import utcnow

    user.last_login_at = utcnow()
    db.commit()

    token = create_session_token(user, settings)
    csrf = new_csrf_token()
    _set_session_cookies(response, token, csrf, settings)
    logger.info("Login correcto de '%s' desde %s", user.username, ip)
    return LoginOut(ok=True, username=user.username, csrf_token=csrf)


@router.post("/logout", response_model=OkOut)
def logout(
    request: Request,
    response: Response,
    db: DbSession,
    user: User = Depends(require_csrf),
) -> OkOut:
    """Cierra la sesión, revoca los tokens emitidos y borra las cookies."""
    settings = request.app.state.settings
    from ..security import revoke_sessions

    # Sube la época de sesión: cualquier token anterior deja de ser válido,
    # aunque el cliente conserve la cookie (revocación real en servidor).
    revoke_sessions(user)
    db.commit()
    _clear_session_cookies(response, settings)
    logger.info("Sesión cerrada y revocada para '%s'", user.username)
    return OkOut(ok=True, detail="Sesión cerrada")


@router.post("/password", response_model=OkOut)
def change_password(
    payload: PasswordChange,
    request: Request,
    response: Response,
    db: DbSession,
    user: User = Depends(require_csrf),
) -> OkOut:
    """Cambia la contraseña y revoca las sesiones anteriores."""
    from ..security import revoke_sessions

    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="La contraseña actual no es correcta")
    settings = request.app.state.settings
    set_password(db, user, payload.new_password)
    # Revoca todas las sesiones (incluida la actual) y emite una nueva para no
    # dejar al usuario fuera del panel tras el cambio.
    revoke_sessions(user)
    db.commit()
    token = create_session_token(user, settings)
    csrf = new_csrf_token()
    _set_session_cookies(response, token, csrf, settings)
    logger.info("Contraseña cambiada y sesiones revocadas para '%s'", user.username)
    return OkOut(ok=True, detail="Contraseña actualizada")


@router.get("/session", response_model=MeOut)
def session_info(request: Request, db: DbSession) -> MeOut:
    """Alias explícito de /auth/me para comprobar la validez de la sesión."""
    return me(request, db)
