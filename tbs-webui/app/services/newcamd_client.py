"""Cliente del protocolo Newcamd (CS378x / newcamd) para servidores CAS propios.

AVISO LEGAL: Newcamd es un protocolo de *card sharing*. Usalo **solo** con
servidores de tu propiedad o para los que tengas autorizacion expresa (por
ejemplo tu propio OSCam sirviendo a tu tarjeta, o el servidor de tu operador).
Este proyecto no incluye ni distribuye claves ni servicios de terceros.

Handshake implementado (formato de OSCam/CS378x):

1. El servidor envia un **nonce de 8 bytes**.
2. La clave del cifrado es ``nonce XOR REMOTE_KEY[:8]``.
3. El cliente envia: **usuario en claro (20 B)** + **32 B cifrados con
   DES-ECB** (contrasena de 20 B + los 12 primeros bytes de la deskey).
4. El servidor responde con 2 bytes de longitud + 1 byte de resultado
   (``0x00`` = correcto).
5. Tras el login, cada peticion ECM se envia como ``longitud (4 B) + bloque de
   64 B`` y el servidor responde con el control word.

`pycryptodome` aporta el DES del protocolo y el cifrado de las contrasenas
guardadas en la base de datos.
"""

from __future__ import annotations

import logging
import socket
import struct
import threading
import time
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)

# Clave remota fija del protocolo Newcamd (estandar publico en OSCam/NewCS).
REMOTE_KEY = bytes(
    (0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E)
)
LOGIN_OK = 0x00
DEFAULT_PORT = 50001
USERNAME_LEN = 20
LOGIN_BLOCK_LEN = 32
ECM_BLOCK_LEN = 64

STATE_DISCONNECTED = "disconnected"
STATE_CONNECTING = "connecting"
STATE_CONNECTED = "connected"
STATE_LOGGED_IN = "logged_in"
STATE_ERROR = "error"


class NewcamdError(Exception):
    """Error de protocolo o de red del cliente Newcamd."""


class NewcamdAuthError(NewcamdError):
    """El servidor rechazo el usuario/contrasena."""


@dataclass
class NewcamdAccount:
    """Datos de conexion de una cuenta Newcamd."""

    name: str
    host: str
    port: int = DEFAULT_PORT
    username: str = ""
    password: str = ""
    deskey: str = "0102030405060708091011121314"  # 14 bytes en hexadecimal
    enabled: bool = True
    id: int | None = None

    def deskey_bytes(self) -> bytes:
        """Clave DES de 14 bytes a partir del valor hexadecimal configurado."""
        cleaned = (self.deskey or "").strip().replace(" ", "").replace(":", "")
        if len(cleaned) != 28:
            raise NewcamdError("La deskey debe tener 28 digitos hexadecimales (14 bytes)")
        try:
            return bytes.fromhex(cleaned)
        except ValueError as exc:
            raise NewcamdError(f"deskey no valida: {exc}") from exc


@dataclass
class NewcamdStatus:
    """Estado de la conexion de una cuenta (lo que muestra la interfaz)."""

    state: str = STATE_DISCONNECTED
    message: str = ""
    connected_at: float | None = None
    last_error: str | None = None
    ecm_sent: int = 0
    cw_received: int = 0
    last_ecm_at: float | None = None
    last_cw_at: float | None = None
    last_cw: str | None = None
    bytes_in: int = 0
    bytes_out: int = 0

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "message": self.message,
            "connected_at": self.connected_at,
            "last_error": self.last_error,
            "ecm_sent": self.ecm_sent,
            "cw_received": self.cw_received,
            "last_ecm_at": self.last_ecm_at,
            "last_cw_at": self.last_cw_at,
            "last_cw": self.last_cw,
            "bytes_in": self.bytes_in,
            "bytes_out": self.bytes_out,
        }


# ---------------------------------------------------------------------------
# Criptografia del protocolo
# ---------------------------------------------------------------------------
def cipher_key(nonce: bytes | None) -> bytes:
    """Clave de 8 bytes del DES: ``nonce XOR REMOTE_KEY`` (o la clave remota)."""
    if nonce and len(nonce) == 8:
        return bytes(a ^ b for a, b in zip(nonce, REMOTE_KEY[:8]))
    return REMOTE_KEY[:8]


def build_login_block(deskey: bytes, password: str = "") -> bytes:
    """Bloque de 32 bytes que se cifra: contrasena (20 B) + deskey (12 B).

    DES-ECB exige multiplos de 8, asi que entran los 12 primeros bytes de la
    deskey; el servidor conoce la clave completa por su configuracion.
    """
    if len(deskey) != 14:
        raise NewcamdError("La clave DES debe tener 14 bytes")
    secret = password.encode("latin-1", "replace")[:20].ljust(20, b"\x00")
    return secret + deskey[:12]


def encrypt_login(username: str, password: str, deskey: bytes, nonce: bytes | None = None) -> bytes:
    """Marco de login: usuario en claro (20 B) + credenciales cifradas (32 B)."""
    from Crypto.Cipher import DES

    cipher = DES.new(cipher_key(nonce), DES.MODE_ECB)
    encrypted = cipher.encrypt(build_login_block(deskey, password))
    user = username.encode("latin-1", "replace")[:USERNAME_LEN].ljust(USERNAME_LEN, b"\x00")
    return user + encrypted


def decrypt_login(data: bytes, nonce: bytes | None = None) -> bytes:
    """Descifra el bloque de credenciales (tests y servidor simulado)."""
    from Crypto.Cipher import DES

    cipher = DES.new(cipher_key(nonce), DES.MODE_ECB)
    return cipher.decrypt(bytes(data[:LOGIN_BLOCK_LEN]))


def decode_login_answer(raw: bytes) -> bool:
    """Interpreta la respuesta de login (admite los formatos habituales)."""
    if not raw:
        return False
    if len(raw) >= 3 and raw[0] == 0x00 and raw[1] == 0x01:
        return raw[2] == LOGIN_OK
    if len(raw) >= 3 and raw[0] == 0x02:
        return raw[2] == LOGIN_OK
    if len(raw) >= 2 and raw[0] == 0x00 and raw[1] == 0x00:
        return True
    if len(raw) == 1:
        return raw[0] == LOGIN_OK
    return False


@dataclass
class EcmRequest:
    """Peticion ECM: cabecera de 16 B + datos del ECM hasta 48 B."""

    service_id: int
    ca_system_id: int = 0x0100
    ecm: bytes = b""
    provid: int = 0
    card_id: int = 0

    def payload(self) -> bytes:
        """Bloque de 64 bytes en el formato de OSCam/NewCS."""
        ecm = bytes(self.ecm)
        if len(ecm) < 3:
            raise NewcamdError("El ECM debe tener al menos 3 bytes (tabla, longitud y datos)")
        header = struct.pack(
            ">BHBH",
            0x00,
            self.service_id & 0xFFFF,
            0x0000,
            self.provid & 0xFFFF,
        )
        return header + ecm[:48].ljust(48, b"\x00")


# ---------------------------------------------------------------------------
# Cliente
# ---------------------------------------------------------------------------
class NewcamdClient:
    """Cliente Newcamd con hilo de recepcion y callback de control words."""

    def __init__(
        self,
        account: NewcamdAccount,
        timeout: float = 8.0,
        on_control_word: Callable[[bytes, bytes], None] | None = None,
    ) -> None:
        self.account = account
        self.timeout = timeout
        self.on_control_word = on_control_word
        self.status = NewcamdStatus()
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.RLock()

    # -- conexion -------------------------------------------------------
    def connect(self) -> NewcamdStatus:
        """Conecta y hace login. Devuelve el estado (no lanza por fallos de red)."""
        with self._lock:
            self.disconnect(update_state=False)
            self.status = NewcamdStatus(state=STATE_CONNECTING, message="Conectando...")
        try:
            sock = socket.create_connection((self.account.host, int(self.account.port)), timeout=self.timeout)
            sock.settimeout(self.timeout)
        except OSError as exc:
            return self._fail(f"No se pudo conectar a {self.account.host}:{self.account.port}: {exc}")

        try:
            nonce = self._recv_exact(sock, 8)
            if len(nonce) < 8:
                raise NewcamdError("El servidor no envio el nonce inicial (8 bytes)")

            login = encrypt_login(
                self.account.username,
                self.account.password,
                self.account.deskey_bytes(),
                nonce,
            )
            sock.sendall(login)
            self.status.bytes_out += len(login)

            raw = self._recv_exact(sock, 3)
            if len(raw) < 3:
                raise NewcamdError("El servidor cerro la conexion durante el login")
            if not decode_login_answer(raw):
                raise NewcamdAuthError(
                    "Login rechazado por el servidor (codigo 0x%02x). "
                    "Revisa usuario, contrasena y deskey." % raw[2]
                )
        except NewcamdAuthError as exc:
            _close(sock)
            return self._fail(str(exc), state=STATE_ERROR)
        except (NewcamdError, OSError) as exc:
            _close(sock)
            return self._fail(str(exc))

        with self._lock:
            self._socket = sock
            self.status = NewcamdStatus(
                state=STATE_LOGGED_IN,
                message="Conectado y autenticado",
                connected_at=time.time(),
            )
        self._stop.clear()
        self._thread = threading.Thread(target=self._receive_loop, name="newcamd-rx", daemon=True)
        self._thread.start()
        logger.info(
            "Newcamd: sesion iniciada con %s:%s como '%s'",
            self.account.host,
            self.account.port,
            self.account.username,
        )
        return self.status

    def disconnect(self, update_state: bool = True) -> None:
        """Cierra la conexion y el hilo de recepcion (idempotente)."""
        self._stop.set()
        with self._lock:
            sock, self._socket = self._socket, None
        if sock is not None:
            _close(sock)
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        if update_state:
            with self._lock:
                self.status.state = STATE_DISCONNECTED
                self.status.message = "Desconectado"

    @property
    def is_logged_in(self) -> bool:
        return self._socket is not None and self.status.state == STATE_LOGGED_IN

    # -- ECM ------------------------------------------------------------
    def send_ecm(self, ecm: bytes, service_id: int = 0, ca_system_id: int = 0x0100, provid: int = 0) -> bool:
        """Envia un ECM. Lanza `NewcamdError` si no hay conexion."""
        with self._lock:
            sock = self._socket
        if sock is None:
            raise NewcamdError("No hay conexion Newcamd activa")
        body = EcmRequest(service_id=service_id, ca_system_id=ca_system_id, ecm=ecm, provid=provid).payload()
        frame = struct.pack(">I", len(body) + 3) + body
        try:
            sock.sendall(frame)
        except OSError as exc:
            self._fail(f"Error enviando el ECM: {exc}")
            return False
        with self._lock:
            self.status.ecm_sent += 1
            self.status.last_ecm_at = time.time()
            self.status.bytes_out += len(frame)
        return True

    # -- recepcion ------------------------------------------------------
    def _receive_loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                sock = self._socket
            if sock is None:
                break
            try:
                header = self._recv_exact(sock, 4)
                if len(header) < 4:
                    break
                length = struct.unpack(">I", header)[0]
                if length < 3 or length > 65535:
                    break
                payload = self._recv_exact(sock, length - 4) if length > 4 else b""
                with self._lock:
                    self.status.bytes_in += 4 + len(payload)
                self._handle_frame(payload)
            except socket.timeout:
                continue
            except (OSError, NewcamdError) as exc:
                if self._stop.is_set():
                    break
                self._fail(f"Conexion perdida: {exc}")
                break
        if not self._stop.is_set():
            with self._lock:
                if self.status.state == STATE_LOGGED_IN:
                    self.status.state = STATE_DISCONNECTED
                    self.status.message = "El servidor cerro la conexion"

    def _handle_frame(self, payload: bytes) -> None:
        """Procesa una trama del servidor: control word o keepalive."""
        if not payload:
            return
        if len(payload) >= 9:
            cw = payload[1:9]
            service_id_bytes = payload[9:11] if len(payload) >= 11 else b"\x00\x00"
            with self._lock:
                self.status.cw_received += 1
                self.status.last_cw_at = time.time()
                self.status.last_cw = cw.hex()
            logger.debug("Newcamd: control word recibido (%s)", cw.hex())
            if self.on_control_word is not None:
                try:
                    self.on_control_word(cw, service_id_bytes)
                except Exception:  # pragma: no cover - el callback no debe romper el hilo
                    logger.exception("Error en el callback de control word")
            return
        if len(payload) in (1, 3):
            return
        logger.debug("Newcamd: trama no reconocida de %d bytes", len(payload))

    # -- utilidades -----------------------------------------------------
    @staticmethod
    def _recv_exact(sock: socket.socket, size: int) -> bytes:
        buffer = bytearray()
        while len(buffer) < size:
            try:
                chunk = sock.recv(size - len(buffer))
            except socket.timeout:
                raise
            except OSError as exc:
                raise NewcamdError(str(exc)) from exc
            if not chunk:
                break
            buffer.extend(chunk)
        return bytes(buffer)

    def _fail(self, message: str, state: str = STATE_ERROR) -> NewcamdStatus:
        logger.warning("Newcamd (%s): %s", self.account.name, message)
        with self._lock:
            self.status.state = state
            self.status.message = message
            self.status.last_error = message
        return self.status


def _close(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Parseo de secciones ECM del flujo DVB (para conectar el escaneo con Newcamd)
# ---------------------------------------------------------------------------
def parse_ecm_section(section: bytes) -> tuple[int, int, bytes]:
    """Extrae (ca_system_id, service_id, ecm) de una seccion ECM 0x80/0x81."""
    if len(section) < 8:
        raise NewcamdError("Seccion ECM demasiado corta")
    table_id = section[0]
    if table_id not in (0x80, 0x81, 0x82):
        raise NewcamdError(f"table_id 0x{table_id:02x} no es una seccion ECM")
    section_length = ((section[1] & 0x0F) << 8) | section[2]
    total = min(len(section), 3 + section_length)
    ecm = bytes(section[:total])
    ca_system_id = struct.unpack(">H", section[3:5])[0]
    service_id = struct.unpack(">H", section[total - 4 : total - 2])[0] if total >= 8 else 0
    return ca_system_id, service_id, ecm
