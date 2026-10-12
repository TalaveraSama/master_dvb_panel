"""WebSocket `/ws/signal`: barras de señal en vivo.

Protocolo (INTERFACES.md §5):
- El cliente debe presentar una cookie de sesión válida; si no, se cierra con
  código **4401**.
- Cliente -> servidor: `{"type":"subscribe"}`, `{"type":"tune", ...TuneRequest}`,
  `{"type":"release"}`, `{"type":"ping"}`.
- Servidor -> cliente: `{"type":"stats", ...SignalStatsOut, "history":[...]}` con
  la periodicidad de `ui.signal_poll_ms` (500 ms por defecto), `{"type":"error"}`,
  `{"type":"pong"}` y `{"type":"tuned"}`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..dvb.base import FrontendError
from ..models import User
from ..schemas import SignalStatsOut, TuneRequest
from ..security import read_session_token, session_is_current
from ..services.device_manager import stats_to_dict
from ._common import to_tune_params

logger = logging.getLogger(__name__)

router = APIRouter()

# Tiempo máximo de espera de mensajes del cliente dentro del bucle de envío.
RECV_TIMEOUT = 0.25
WS_UNAUTHORIZED = 4401
WS_FORBIDDEN = 4403


def _authenticate(websocket: WebSocket) -> User | None:
    """Valida la cookie de sesión del WebSocket (incluida la revocación)."""
    settings = websocket.app.state.settings
    token = websocket.cookies.get(settings.session_cookie)
    data = read_session_token(token, settings)
    if data is None:
        return None
    session = websocket.app.state.session_factory()
    try:
        user = session.get(User, data.user_id)
        if user is None or user.username != data.username:
            return None
        # Revocación en servidor: logout / cambio de contraseña suben la época.
        # Sin esta comprobación el WebSocket seguiría aceptando tokens revocados.
        if not session_is_current(data, user):
            logger.info("WebSocket rechazado: sesión revocada (usuario %s)", data.username)
            return None
        session.expunge(user)
        return user
    finally:
        session.close()


def _same_origin(websocket: WebSocket) -> bool:
    """Comprueba que el handshake venga del mismo origen (anti-CSWSH).

    Un navegador siempre envía `Origin` en el handshake WebSocket; si falta, se
    acepta (clientes de línea de comandos), pero si viene y no coincide con el
    host del panel, se rechaza. Añade defensa en profundidad frente a que un
    sitio externo abra el socket y envíe órdenes de sintonización.
    """
    origin = websocket.headers.get("origin")
    if not origin:
        return True
    try:
        from urllib.parse import urlsplit

        origin_host = urlsplit(origin).netloc.lower()
    except Exception:  # pragma: no cover - cabecera malformada
        return False
    host = (websocket.headers.get("host") or "").lower()
    return bool(origin_host) and origin_host == host


@router.websocket("/ws/signal")
async def signal_socket(websocket: WebSocket) -> None:
    """Envía estadísticas de señal en vivo y acepta órdenes de sintonización."""
    if not _same_origin(websocket):
        logger.warning("Handshake WebSocket rechazado por Origin no coincidente: %s", websocket.headers.get("origin"))
        await websocket.close(code=WS_FORBIDDEN, reason="Origen no permitido")
        return

    user = _authenticate(websocket)
    if user is None:
        # Se acepta y se cierra con 4401 para poder enviar el código de la
        # aplicación (rechazar antes del accept produce un 403 genérico).
        await websocket.accept()
        await websocket.close(code=WS_UNAUTHORIZED, reason="Sesión no válida")
        return

    await websocket.accept()
    device = websocket.app.state.device_manager
    settings = websocket.app.state.settings
    logger.info("WebSocket de señal conectado (usuario %s)", user.username)

    # Estado del cliente
    want_stats = True
    last_sent_ts = 0.0
    last_error: str | None = None

    async def send_error(detail: str) -> None:
        nonlocal last_error
        last_error = detail
        try:
            await websocket.send_json({"type": "error", "detail": detail})
        except Exception:  # pragma: no cover - cliente desconectado
            pass

    async def handle_message(raw: str) -> float | None:
        """Procesa un mensaje del cliente. Devuelve el timestamp de stats ya enviado."""
        nonlocal want_stats
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            await send_error("Mensaje JSON inválido")
            return None
        if not isinstance(message, dict):
            await send_error("Se esperaba un objeto JSON")
            return None

        kind = message.get("type")
        if kind == "ping":
            await websocket.send_json({"type": "pong", "t": time.time()})
            return None
        if kind == "subscribe":
            want_stats = True
            return None
        if kind == "release":
            device.release()
            await websocket.send_json({"type": "released"})
            return None
        if kind != "tune":
            await send_error(f"Tipo de mensaje no soportado: {kind!r}")
            return None

        payload = {key: value for key, value in message.items() if key != "type"}
        try:
            request = TuneRequest(**payload)
        except Exception as exc:
            await send_error(f"Parámetros de sintonización inválidos: {exc}")
            return None

        params = to_tune_params(request)
        try:
            # La sintonización bloquea (ioctl + espera de LOCK): se ejecuta en un
            # hilo para no bloquear el bucle de eventos.
            stats = await asyncio.to_thread(device.tune, params, f"ws:{user.username}")
        except FrontendError as exc:
            await send_error(str(exc))
            return None

        await websocket.send_json(
            {
                "type": "tuned",
                "locked": bool(stats.lock.is_locked),
                "frequency_khz": params.frequency_khz,
                "if_frequency_khz": params.if_frequency_khz,
                "detail": None if stats.lock.is_locked else "Sin LOCK: revisa parámetros y LNB",
            }
        )
        stats_dict = stats_to_dict(stats)
        if stats_dict:
            await websocket.send_json({"type": "stats", **stats_dict, "history": device.history()})
            return stats_dict["timestamp"]
        return None

    try:
        while True:
            # 1) Atender mensajes del cliente (corto)
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=RECV_TIMEOUT)
            except asyncio.TimeoutError:
                raw = None
            except WebSocketDisconnect:
                break

            if raw is not None:
                sent_ts = await handle_message(raw)
                if sent_ts:
                    last_sent_ts = sent_ts

            # 2) Reenviar la última lectura del monitor sin duplicar
            if not want_stats:
                continue
            stats = device.current_stats()
            if stats is None:
                continue
            stats_dict = stats_to_dict(stats)
            if stats_dict is None or stats_dict["timestamp"] <= last_sent_ts:
                continue
            last_sent_ts = stats_dict["timestamp"]
            await websocket.send_json({"type": "stats", **stats_dict, "history": device.history()})
    except WebSocketDisconnect:
        logger.debug("WebSocket de señal desconectado (usuario %s)", user.username)
    except Exception:  # pragma: no cover - defensivo
        logger.exception("Error en el WebSocket de señal")
    finally:
        logger.info("WebSocket de señal cerrado (usuario %s)", user.username)
