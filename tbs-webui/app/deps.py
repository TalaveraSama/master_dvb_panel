"""Dependencias de FastAPI: sesión de BD, usuario autenticado y CSRF.

Todas las rutas `/api/**` (salvo `health`, `auth/me`, `auth/login` y `auth/setup`)
dependen de `require_user`; el WebSocket valida la sesión por su cuenta en
`routers/ws.py` porque no pasa por las dependencias HTTP.
"""

from __future__ import annotations

from typing import Annotated, Iterator

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .database import get_sessionmaker
from .models import User
from .security import csrf_matches

# Métodos que modifican estado y por tanto exigen CSRF.
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def get_db() -> Iterator[Session]:
    """Una sesión de base de datos por petición."""
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()


DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


def get_session_user(request: Request, db: DbSession) -> User | None:
    """Usuario de la sesión, o None si no hay sesión válida o está revocada."""
    from .security import read_session_token, session_is_current

    token = request.cookies.get(request.app.state.settings.session_cookie)
    data = read_session_token(token, request.app.state.settings)
    if data is None:
        return None
    user = db.get(User, data.user_id)
    if user is None or user.username != data.username:
        return None
    # Revocación en servidor: logout y cambio de contraseña suben la época.
    if not session_is_current(data, user):
        return None
    return user


def require_user(request: Request, db: DbSession) -> User:
    """Exige sesión válida; si no, 401."""
    user = get_session_user(request, db)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesión no válida o caducada",
            headers={"WWW-Authenticate": "Cookie"},
        )
    return user


def require_csrf(
    request: Request,
    user: Annotated[User, Depends(require_user)],
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> User:
    """Exige sesión válida y, en métodos que mutan estado, token CSRF correcto.

    Se aplica como dependencia de **router** en `main.py`, de modo que cualquier
    ruta nueva dentro de ese router queda protegida automáticamente.
    """
    settings: Settings = request.app.state.settings
    if request.method in UNSAFE_METHODS:
        cookie_token = request.cookies.get(settings.csrf_cookie)
        if not csrf_matches(cookie_token, x_csrf_token):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Token CSRF ausente o inválido",
            )
    return user


CurrentUser = Annotated[User, Depends(require_user)]
CsrfUser = Annotated[User, Depends(require_csrf)]


def client_ip(request: Request) -> str | None:
    """IP del cliente.

    `X-Forwarded-For` sólo se tiene en cuenta si `TBS_TRUST_PROXY_HEADERS=1`
    (detrás de un proxy inverso de confianza). Sin ese ajuste, un cliente podría
    falsificar la IP registrada en la auditoría y en el límite de intentos.
    """
    if getattr(request.app.state.settings, "trust_proxy_headers", False):
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None
