"""Punto de entrada de TBS WebUI (FastAPI + Uvicorn).

Arranque:
    uvicorn app.main:app --host 0.0.0.0 --port 9000

Organización:
- `/api/**`    -> routers JSON. Todo protegido por sesión **excepto**
                 `/api/health`, `/api/auth/me`, `/api/auth/login` y
                 `/api/auth/setup` (primer arranque).
- `/ws/signal` -> WebSocket con estadísticas de señal en vivo (valida la cookie).
- `/`          -> index.html de la SPA, `/login` -> login.html, y `/css`,
                 `/js`, `/vendor` como estáticos.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import get_settings
from .database import get_sessionmaker, init_db
from .deps import require_csrf
from .routers import (
    adapters,
    auth,
    channels,
    export,
    health,
    logs as logs_router,
    newcamd as newcamd_router,
    scan,
    settings as settings_router,
    status as status_router,
    tune as tune_router,
    ws,
)
from .services.device_manager import DeviceManager
from .services.newcamd_manager import NewcamdManager
from .services.scan_service import ScanService

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
# Toda la API protegida exige sesión; los métodos que mutan estado exigen además
# CSRF (comprobado dentro de `require_csrf` según el método HTTP).
AUTH_DEPENDENCY = [Depends(require_csrf)]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
class DatabaseLogHandler(logging.Handler):
    """Guarda avisos y errores en `app_logs` para verlos desde la UI."""

    def __init__(self, level: int = logging.WARNING) -> None:
        super().__init__(level=level)
        self._busy = False

    def emit(self, record: logging.LogRecord) -> None:
        if self._busy:  # evita recursión si la escritura falla
            return
        self._busy = True
        try:
            from .models import AppLog

            session = get_sessionmaker()()
            try:
                session.add(
                    AppLog(
                        level=record.levelname,
                        source=record.name[:32],
                        message=self.format(record)[:2000],
                    )
                )
                session.commit()
            finally:
                session.close()
        except Exception:  # pragma: no cover - el logging nunca debe romper la app
            pass
        finally:
            self._busy = False


def configure_logging(settings) -> None:
    """Logging a consola y a base de datos."""
    level = getattr(logging, str(settings.log_level).upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)

    if not any(type(handler) is logging.StreamHandler for handler in root.handlers):
        console = logging.StreamHandler()
        console.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s", "%Y-%m-%d %H:%M:%S")
        )
        root.addHandler(console)

    if not any(isinstance(handler, DatabaseLogHandler) for handler in root.handlers):
        db_handler = DatabaseLogHandler(level=logging.WARNING)
        db_handler.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(db_handler)

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Ciclo de vida
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Arranque/parada ordenados: BD, admin inicial, servicios y hardware."""
    settings = app.state.settings
    configure_logging(settings)
    init_db()

    from .security import bootstrap_admin

    session = get_sessionmaker()()
    try:
        bootstrap_admin(session, settings)
    finally:
        session.close()

    app.state.device_manager = DeviceManager(settings)
    app.state.scan_service = ScanService(settings, app.state.device_manager)
    app.state.newcamd_manager = NewcamdManager(settings)
    app.state.session_factory = get_sessionmaker()

    from .services import settings_service

    session = get_sessionmaker()()
    try:
        stored = settings_service.load_settings(session, settings)
    finally:
        session.close()
    app.state.default_settings = stored
    app.state.device_manager.set_poll_ms(
        int(stored.get("ui", {}).get("signal_poll_ms", settings.signal_poll_ms))
    )
    app.state.device_manager.set_history_seconds(
        int(stored.get("ui", {}).get("history_seconds", settings.history_seconds))
    )

    # Newcamd: conexión automática de las cuentas marcadas (si está habilitado).
    if settings.newcamd_enabled and settings.newcamd_auto_connect:
        from .services import newcamd_manager as newcamd_module

        session = get_sessionmaker()()
        try:
            rows = app.state.newcamd_manager.load_accounts(session)
            for row in rows:
                if row.enabled and row.auto_connect:
                    try:
                        app.state.newcamd_manager.connect(newcamd_module.to_domain(row, settings))
                    except Exception as exc:
                        logger.warning("Newcamd: no se pudo conectar '%s': %s", row.name, exc)
        finally:
            session.close()

    logger.info(
        "TBS WebUI %s listo — modo %s — escuchando en %s:%s",
        __version__,
        "SIMULACIÓN" if settings.is_mock() else "HARDWARE",
        settings.host,
        settings.port,
    )
    try:
        yield
    finally:
        logger.info("Apagando TBS WebUI…")
        try:
            app.state.scan_service.stop()
            app.state.scan_service.wait(timeout=5.0)
        except Exception:  # pragma: no cover
            logger.exception("Error deteniendo el escaneo")
        try:
            app.state.device_manager.stop()
        except Exception:  # pragma: no cover
            logger.exception("Error liberando el dispositivo")
        try:
            app.state.newcamd_manager.disconnect_all()
        except Exception:  # pragma: no cover
            logger.exception("Error cerrando las conexiones Newcamd")


# ---------------------------------------------------------------------------
# Aplicación
# ---------------------------------------------------------------------------
def create_app() -> FastAPI:
    """Fábrica de la aplicación (los tests la usan con `TestClient`)."""
    settings = get_settings()

    app = FastAPI(
        title="TBS WebUI",
        description="Panel web para controlar tarjetas DVB TBS en Ubuntu",
        version=__version__,
        lifespan=lifespan,
        # Permite publicar el panel en una subruta detrás de un proxy inverso
        # (p. ej. TBS_ROOT_PATH=/tbs con Nginx sirviendo /tbs/ -> :9000/).
        root_path=settings.root_path or "",
        docs_url=None,  # panel privado: sin documentación pública
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings

    # ---- Rutas públicas de la API ----
    app.include_router(health.router, prefix="/api")
    app.include_router(auth.router, prefix="/api")

    # ---- Rutas protegidas (exigen sesión válida) ----
    for router in (
        adapters.router,
        tune_router.router,
        status_router.router,
        scan.router,
        channels.router,
        export.router,
        settings_router.router,
        logs_router.router,
        newcamd_router.router,
    ):
        app.include_router(router, prefix="/api", dependencies=AUTH_DEPENDENCY)

    # ---- WebSocket ----
    app.include_router(ws.router)

    # ---- Errores no controlados: se registran (visibles en /api/logs) ----
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request, exc):  # pragma: no cover - defensivo
        logger.exception("Error no controlado en %s %s", request.method, request.url.path)
        return JSONResponse(
            {"detail": "Error interno del servidor"},
            status_code=500,
        )

    # ---- Cabeceras de seguridad ----
    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        # CSP: todo local (sin CDNs). Alpine.js evalúa las expresiones de sus
        # directivas con `new Function`, por lo que necesita 'unsafe-eval';
        # `style-src` necesita 'unsafe-inline' por los atributos style de Alpine.
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self' data:; connect-src 'self' ws: wss:; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'self'",
        )
        # Menos ruido en estáticos: se cachean 5 min (llevan versión en el nombre
        # cuando cambian) pero sin permitir incrustarlos en otros orígenes.
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        if request.url.path.startswith("/api"):
            response.headers.setdefault("Cache-Control", "no-store")
        elif request.url.path.endswith((".js", ".css")):
            response.headers.setdefault("Cache-Control", "public, max-age=300")
        return response

    # ---- Estáticos y páginas ----
    if WEB_DIR.is_dir():
        for mount, directory in (
            ("/css", WEB_DIR / "css"),
            ("/js", WEB_DIR / "js"),
            ("/vendor", WEB_DIR / "vendor"),
            ("/assets", WEB_DIR / "assets"),
        ):
            if directory.is_dir():
                app.mount(mount, StaticFiles(directory=directory), name=mount.strip("/"))

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            """SPA principal (el JS redirige a /login si no hay sesión)."""
            return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-store"})

        @app.get("/login", include_in_schema=False)
        def login_page() -> FileResponse:
            """Pantalla de login / asistente de primer arranque."""
            return FileResponse(WEB_DIR / "login.html", headers={"Cache-Control": "no-store"})

        @app.get("/favicon.ico", include_in_schema=False)
        def favicon():
            icon = WEB_DIR / "favicon.svg"
            if icon.is_file():
                return FileResponse(icon, media_type="image/svg+xml")
            raise HTTPException(status_code=404, detail="Sin favicon")

    else:  # pragma: no cover - sólo si falta el frontend
        @app.get("/", include_in_schema=False)
        def missing_frontend() -> RedirectResponse:
            return RedirectResponse("/api/health")

    return app


app = create_app()
