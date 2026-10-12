"""Tests del cliente Newcamd contra un servidor simulado.

El servidor de prueba implementa el protocolo real de OSCam:

1. Al conectar envía un **nonce de 8 bytes**.
2. Recibe el login: usuario en claro (20 B) + credenciales cifradas (32 B) con
   ``DES-ECB(key=nonce XOR REMOTE_KEY)``.
3. Responde ``\\x00\\x01\\x00`` (longitud 1 + resultado 0 = OK).
4. Tras el login responde a los ECM con el control word.
"""

from __future__ import annotations

import socket
import struct
import threading
import time

import pytest

from app.config import get_settings
from app.services.newcamd_client import (
    REMOTE_KEY,
    NewcamdAccount,
    NewcamdClient,
    NewcamdError,
    build_login_block,
    decrypt_login,
    encrypt_login,
)
from app.services.newcamd_manager import decrypt_password, encrypt_password

USER = "astra"
PASSWORD = "astra"
DESKEY = bytes.fromhex("0102030405060708091011121314")
NONCE = bytes.fromhex("0011223344556677")
CW = bytes.fromhex("1122334455667788")


class FakeNewcamdServer(threading.Thread):
    """Servidor Newcamd mínimo (protocolo real) para tests."""

    def __init__(self, accept_login: bool = True, send_cw: bool = False) -> None:
        super().__init__(daemon=True)
        self.accept_login = accept_login
        self.send_cw = send_cw
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(4)
        self._listener.settimeout(5.0)
        self.port = self._listener.getsockname()[1]
        self._stopped = threading.Event()
        self.received_username: bytes | None = None
        self.received_block: bytes | None = None
        self.received_ecm = 0

    def run(self) -> None:
        try:
            while not self._stopped.is_set():
                try:
                    conn, _ = self._listener.accept()
                except (socket.timeout, OSError):
                    continue
                threading.Thread(target=self._serve, args=(conn,), daemon=True).start()
        finally:
            try:
                self._listener.close()
            except OSError:
                pass

    def _serve(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(5.0)
            # 1) nonce de 8 bytes
            conn.sendall(NONCE)
            # 2) login: usuario en claro + bloque cifrado de 32 bytes
            head = self._recv_exact(conn, 20)
            encrypted = self._recv_exact(conn, 32)
            self.received_username = head
            self.received_block = encrypted
            plain = decrypt_login(encrypted, NONCE)
            ok = (
                self.accept_login
                and head.rstrip(b"\x00").decode(errors="replace") == USER
                and plain[0:20].rstrip(b"\x00").decode(errors="replace") == PASSWORD
                and plain[20:32] == DESKEY[:12]
            )
            # respuesta de login: longitud (2) + resultado (1)
            conn.sendall(struct.pack(">H", 1) + bytes((0x00 if ok else 0x01,)))
            if not ok:
                return
            # 3) peticiones ECM
            while not self._stopped.is_set():
                header = self._recv_exact(conn, 4)
                if len(header) < 4:
                    break
                length = struct.unpack(">I", header)[0]
                self._recv_exact(conn, max(0, length - 4))
                self.received_ecm += 1
                if self.send_cw:
                    payload = (b"\x00" + CW + struct.pack(">H", 1)).ljust(13, b"\x00")
                    conn.sendall(struct.pack(">I", len(payload)) + payload)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    @staticmethod
    def _recv_exact(conn: socket.socket, size: int) -> bytes:
        buffer = bytearray()
        while len(buffer) < size:
            try:
                chunk = conn.recv(size - len(buffer))
            except (socket.timeout, OSError):
                break
            if not chunk:
                break
            buffer.extend(chunk)
        return bytes(buffer)

    def stop(self) -> None:
        self._stopped.set()
        try:
            self._listener.close()
        except OSError:
            pass
        self.join(timeout=2.0)


@pytest.fixture()
def server():
    instance = FakeNewcamdServer()
    instance.start()
    time.sleep(0.1)
    yield instance
    instance.stop()


@pytest.fixture()
def cw_server():
    instance = FakeNewcamdServer(send_cw=True)
    instance.start()
    time.sleep(0.1)
    yield instance
    instance.stop()


# ---------------------------------------------------------------------------
# Criptografía del protocolo
# ---------------------------------------------------------------------------
def test_login_block_layout():
    """El bloque de credenciales es contraseña (20 B) + deskey (14 B)."""
    block = build_login_block(DESKEY, PASSWORD)
    assert len(block) == 32
    assert block[0:20].rstrip(b"\x00").decode() == PASSWORD
    assert block[20:32] == DESKEY[:12]


def test_login_frame_layout():
    """El marco de login es usuario en claro (20 B) + 32 B cifrados."""
    frame = encrypt_login(USER, PASSWORD, DESKEY, NONCE)
    assert len(frame) == 52
    assert frame[:20].rstrip(b"\x00").decode() == USER
    assert frame[20:] != build_login_block(DESKEY, PASSWORD)[:32]  # cifrado
    assert decrypt_login(frame[20:], NONCE)[:20].rstrip(b"\x00").decode() == PASSWORD
    assert decrypt_login(frame[20:], NONCE)[20:32] == DESKEY[:12]


def test_remote_key_y_nonce():
    assert len(REMOTE_KEY) == 14
    # Con el nonce del servidor la clave de cifrado cambia
    assert encrypt_login(USER, PASSWORD, DESKEY, NONCE) != encrypt_login(USER, PASSWORD, DESKEY, REMOTE_KEY[:8])


def test_deskey_validation():
    account = NewcamdAccount(name="x", host="127.0.0.1", deskey="0102")
    with pytest.raises(NewcamdError):
        account.deskey_bytes()
    account.deskey = "0102030405060708091011121314"
    assert len(account.deskey_bytes()) == 14


# ---------------------------------------------------------------------------
# Handshake y login
# ---------------------------------------------------------------------------
def test_login_ok(server):
    account = NewcamdAccount(
        name="test", host="127.0.0.1", port=server.port, username=USER, password=PASSWORD, deskey=DESKEY.hex()
    )
    client = NewcamdClient(account)
    try:
        status = client.connect()
        assert status.state == "logged_in", status.message
        assert client.is_logged_in
        assert server.received_username.rstrip(b"\x00").decode() == USER
        assert len(server.received_block) == 32
    finally:
        client.disconnect()
    assert not client.is_logged_in
    assert client.status.state == "disconnected"


def test_login_rejected():
    bad = FakeNewcamdServer(accept_login=False)
    bad.start()
    time.sleep(0.1)
    account = NewcamdAccount(
        name="test", host="127.0.0.1", port=bad.port, username=USER, password="mala", deskey=DESKEY.hex()
    )
    client = NewcamdClient(account)
    try:
        status = client.connect()
        assert status.state == "error"
        assert "rechazado" in (status.message or "").lower()
    finally:
        client.disconnect()
        bad.stop()


def test_connection_refused_is_reported():
    """Puerto cerrado -> estado de error con mensaje claro, sin excepción."""
    account = NewcamdAccount(name="test", host="127.0.0.1", port=1, username="x", password="y")
    client = NewcamdClient(account, timeout=2.0)
    status = client.connect()
    assert status.state == "error"
    assert status.last_error
    client.disconnect()


def test_send_ecm_and_receive_cw(cw_server):
    received = []
    account = NewcamdAccount(
        name="test", host="127.0.0.1", port=cw_server.port, username=USER, password=PASSWORD, deskey=DESKEY.hex()
    )
    client = NewcamdClient(account, on_control_word=lambda cw, sid: received.append(cw))
    try:
        assert client.connect().state == "logged_in"
        assert client.send_ecm(bytes.fromhex("8030123456789abcdef0"), service_id=1) is True
        deadline = time.monotonic() + 4
        while not received and time.monotonic() < deadline:
            time.sleep(0.05)
        assert received, "no se recibió ningún control word"
        assert received[0] == CW
        assert client.status.cw_received >= 1
        assert client.status.ecm_sent == 1
        assert client.status.last_cw == CW.hex()
    finally:
        client.disconnect()
    assert cw_server.received_ecm >= 1


def test_ecm_without_connection_raises():
    account = NewcamdAccount(name="test", host="127.0.0.1", port=1)
    client = NewcamdClient(account)
    with pytest.raises(NewcamdError):
        client.send_ecm(b"\x80\x30\x01")


# ---------------------------------------------------------------------------
# Cifrado de la contraseña en reposo
# ---------------------------------------------------------------------------
def test_password_encryption_roundtrip():
    settings = get_settings()
    blob = encrypt_password("secreto-del-servidor", settings)
    assert blob and "secreto" not in blob
    assert decrypt_password(blob, settings) == "secreto-del-servidor"


def test_password_encryption_is_not_deterministic():
    settings = get_settings()
    first = encrypt_password("misma-clave", settings)
    second = encrypt_password("misma-clave", settings)
    assert first != second  # nonce aleatorio
    assert decrypt_password(first, settings) == decrypt_password(second, settings) == "misma-clave"


def test_empty_password():
    settings = get_settings()
    assert encrypt_password("", settings) == ""
    assert decrypt_password("", settings) == ""
