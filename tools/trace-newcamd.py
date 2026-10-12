"""Traza detallada del handshake Newcamd (cliente y servidor simulado)."""

from __future__ import annotations

import socket
import sys
import threading
import time

sys.path.insert(0, "tbs-webui")
sys.path.insert(0, "tbs-webui/tests")

from test_newcamd_protocol import DESKEY, PASSWORD, SERVER_KEY, USER  # noqa: E402

from app.services.newcamd_client import (  # noqa: E402
    MSG_LOGIN,
    MSG_LOGIN_ACK,
    build_packet,
    derive_triple_des_key,
    parse_packet,
)

KEY = derive_triple_des_key(DESKEY, SERVER_KEY)
listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
listener.bind(("127.0.0.1", 0))
listener.listen(1)
port = listener.getsockname()[1]
print("servidor de traza en", port)


def server() -> None:
    conn, _ = listener.accept()
    conn.settimeout(5)
    conn.sendall(SERVER_KEY)
    header = conn.recv(2)
    length = int.from_bytes(header, "big")
    body = b""
    while len(body) < length:
        chunk = conn.recv(length - len(body))
        if not chunk:
            break
        body += chunk
    packet = header + body
    print("  [srv] paquete recibido:", len(packet), "bytes, longitud:", length)
    tipo, payload, msg_id = parse_packet(packet, KEY)
    print("  [srv] tipo=0x%02X msg_id=%s body[:26]=%r" % (tipo, msg_id, payload[:26]))
    user, _, password = payload[3:].partition(b"\x00")
    print("  [srv] user=%r pwd=%r" % (user, password.rstrip(b"\x00")))
    ok = user.decode() == USER and password.rstrip(b"\x00").decode() == PASSWORD
    reply = build_packet(MSG_LOGIN_ACK if ok else 0xE2, b"", KEY, (msg_id + 1) & 0xFFFF)
    print("  [srv] respuesta:", reply.hex(), "-> login", "OK" if ok else "RECHAZADO")
    conn.sendall(reply)
    time.sleep(0.5)
    conn.close()


threading.Thread(target=server, daemon=True).start()
time.sleep(0.2)

# Cliente manual con la misma lógica del cliente real
sock = socket.create_connection(("127.0.0.1", port), timeout=5)
server_key = sock.recv(14)
print("  [cli] clave del servidor:", server_key.hex(), "len:", len(server_key))
key = derive_triple_des_key(DESKEY, server_key)
print("  [cli] clave derivada:", key.hex(), "igual al servidor:", key == KEY)
login = build_packet(MSG_LOGIN, USER.encode() + b"\x00" + PASSWORD.encode(), key, 0)
print("  [cli] login:", login.hex())
sock.sendall(login)
header = sock.recv(2)
length = int.from_bytes(header, "big")
body = b""
while len(body) < length:
    chunk = sock.recv(length - len(body))
    if not chunk:
        break
    body += chunk
tipo, payload, msg_id = parse_packet(header + body, key)
print("  [cli] respuesta tipo=0x%02X msg_id=%s len(body)=%d" % (tipo, msg_id, len(payload)))
sock.close()
listener.close()
