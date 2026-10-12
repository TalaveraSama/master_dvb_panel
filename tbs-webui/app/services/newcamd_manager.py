"""Gestor de cuentas Newcamd: persistencia, conexión y estado.

- Las cuentas se guardan en SQLite (tabla `newcamd_accounts`) con la contraseña
  **cifrada** con una clave derivada del secreto de la aplicación.
- La API nunca devuelve la contraseña: sólo si está configurada.
- `NewcamdManager` mantiene un cliente por cuenta, permite probar la conexión,
  conectar/desconectar y consultar el estado que muestra la interfaz.

AVISO LEGAL: úsalo sólo con servidores Newcamd propios o autorizados.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import threading
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import NewcamdAccount as NewcamdAccountModel
from .newcamd_client import (
    NewcamdAccount,
    NewcamdAuthError,
    NewcamdClient,
    NewcamdError,
    NewcamdStatus,
    STATE_DISCONNECTED,
    STATE_ERROR,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cifrado de la contraseña en reposo
# ---------------------------------------------------------------------------
def _secret_key(settings: Settings) -> bytes:
    """Clave de 32 bytes derivada del secreto de sesión (nunca en claro en la BD)."""
    return hashlib.sha256(f"tbs-webui.newcamd::{settings.secret}".encode()).digest()


def encrypt_password(password: str, settings: Settings) -> str:
    """Cifra la contraseña (Fernet-like: AES-CTR con HMAC no es necesario aquí).

    Se usa el propio `itsdangerous` para firmar y `pycryptodome` para cifrar en
    modo CTR con un nonce aleatorio; el resultado va en base64.
    """
    if not password:
        return ""
    from Crypto.Cipher import AES
    from Crypto.Random import get_random_bytes

    key = _secret_key(settings)
    nonce = get_random_bytes(12)
    cipher = AES.new(key, AES.MODE_CTR, nonce=nonce)
    encrypted = cipher.encrypt(password.encode("utf-8"))
    return base64.urlsafe_b64encode(nonce + encrypted).decode()


def decrypt_password(blob: str, settings: Settings) -> str:
    """Descifra la contraseña guardada (cadena vacía si no hay)."""
    if not blob:
        return ""
    from Crypto.Cipher import AES

    raw = base64.urlsafe_b64decode(blob.encode())
    nonce, encrypted = raw[:12], raw[12:]
    cipher = AES.new(_secret_key(settings), AES.MODE_CTR, nonce=nonce)
    return cipher.decrypt(encrypted).decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# Conversión modelo <-> dominio
# ---------------------------------------------------------------------------
def to_domain(row: NewcamdAccountModel, settings: Settings, with_password: bool = True) -> NewcamdAccount:
    """Convierte la fila de BD en la cuenta del dominio."""
    return NewcamdAccount(
        id=row.id,
        name=row.name,
        host=row.host,
        port=row.port,
        username=row.username,
        password=decrypt_password(row.password_enc, settings) if with_password else "",
        deskey=row.deskey,
        enabled=row.enabled,
    )


# ---------------------------------------------------------------------------
# Gestor
# ---------------------------------------------------------------------------
class NewcamdManager:
    """Mantiene las conexiones Newcamd y su estado en memoria."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._clients: dict[int, NewcamdClient] = {}
        self._status: dict[int, NewcamdStatus] = {}
        self._lock = threading.RLock()

    # -- estado ---------------------------------------------------------
    def status(self, account_id: int) -> NewcamdStatus:
        with self._lock:
            client = self._clients.get(account_id)
            if client is not None:
                return client.status
            return self._status.setdefault(account_id, NewcamdStatus())

    def statuses(self) -> dict[int, NewcamdStatus]:
        with self._lock:
            ids = set(self._clients) | set(self._status)
        return {account_id: self.status(account_id) for account_id in ids}

    def is_connected(self, account_id: int) -> bool:
        with self._lock:
            client = self._clients.get(account_id)
        return bool(client and client.is_logged_in)

    # -- ciclo de vida --------------------------------------------------
    def connect(self, account: NewcamdAccount) -> NewcamdStatus:
        """Conecta (o reconecta) una cuenta."""
        if account.id is None:
            raise NewcamdError("La cuenta no está guardada (sin id)")
        with self._lock:
            previous = self._clients.pop(account.id, None)
        if previous is not None:
            previous.disconnect()

        client = NewcamdClient(
            account,
            timeout=float(getattr(self.settings, "newcamd_timeout_s", 8.0)),
            on_control_word=self._on_control_word,
        )
        with self._lock:
            self._clients[account.id] = client
        status = client.connect()
        with self._lock:
            self._status[account.id] = status
        if status.state == STATE_ERROR:
            raise NewcamdError(status.message or "No se pudo conectar")
        return status

    def disconnect(self, account_id: int) -> bool:
        """Cierra la conexión de una cuenta. True si había algo que cerrar."""
        with self._lock:
            client = self._clients.pop(account_id, None)
        if client is None:
            return False
        client.disconnect()
        with self._lock:
            self._status[account_id] = client.status
        return True

    def disconnect_all(self) -> None:
        with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
            for client in clients:
                self._status[client.account.id or 0] = client.status
        for client in clients:
            client.disconnect()

    def test(self, account: NewcamdAccount) -> NewcamdStatus:
        """Prueba una conexión sin dejarla abierta (para el botón 'Probar').

        Se conserva el estado del intento (incluido el error) aunque después se
        cierre la conexión, para que la UI muestre el motivo real del fallo.
        """
        client = NewcamdClient(account, timeout=float(getattr(self.settings, "newcamd_timeout_s", 8.0)))
        result: NewcamdStatus
        try:
            result = client.connect()
        finally:
            try:
                # update_state=False: no debe borrar el estado del intento
                # (si falló, queremos conservar el error para la UI).
                client.disconnect(update_state=False)
            except Exception:  # pragma: no cover - cierre defensivo
                logger.debug("Error cerrando la prueba de conexión", exc_info=True)
        return result

    def send_ecm(self, account_id: int, ecm: bytes, service_id: int = 0) -> bool:
        """Envía un ECM por la conexión de esa cuenta (None si no hay conexión)."""
        with self._lock:
            client = self._clients.get(account_id)
        if client is None or not client.is_logged_in:
            raise NewcamdError("La cuenta no está conectada")
        return client.send_ecm(ecm, service_id=service_id)

    def _on_control_word(self, cw: bytes, service_id_bytes: bytes) -> None:
        """Callback cuando llega un control word (se registra para la UI)."""
        logger.info("Newcamd: control word recibido (%s) servicio %s", cw.hex(), service_id_bytes.hex())

    # -- utilidades -----------------------------------------------------
    def load_accounts(self, db: Session) -> list[NewcamdAccountModel]:
        return list(db.execute(select(NewcamdAccountModel).order_by(NewcamdAccountModel.name)).scalars())


def account_to_dict(row: NewcamdAccountModel, settings: Settings, status: NewcamdStatus | None = None) -> dict[str, Any]:
    """Representación segura para la API (sin la contraseña)."""
    return {
        "id": row.id,
        "name": row.name,
        "host": row.host,
        "port": row.port,
        "username": row.username,
        "password_set": bool(row.password_enc),
        "deskey": row.deskey,
        "enabled": row.enabled,
        "auto_connect": row.auto_connect,
        "notes": row.notes or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "status": (status or NewcamdStatus()).to_dict(),
    }
