"""Depuración del handshake Newcamd contra el servidor simulado."""

from __future__ import annotations

import sys
import time

sys.path.insert(0, "tbs-webui")
sys.path.insert(0, "tbs-webui/tests")

from test_newcamd_protocol import DESKEY, PASSWORD, SERVER_KEY, USER, FakeNewcamdServer  # noqa: E402

from app.services.newcamd_client import (  # noqa: E402
    MSG_LOGIN,
    NewcamdAccount,
    NewcamdClient,
    build_packet,
    derive_triple_des_key,
    parse_packet,
)

key = derive_triple_des_key(DESKEY, SERVER_KEY)
packet = build_packet(MSG_LOGIN, USER.encode() + b"\x00" + PASSWORD.encode(), key, 0)
print("paquete de login:", len(packet), "bytes | longitud declarada:", int.from_bytes(packet[:2], "big"))
tipo, body, msg_id = parse_packet(packet, key)
print("parse propio -> tipo: 0x%02X body: %r msg_id: %s" % (tipo, body[:24], msg_id))

server = FakeNewcamdServer(send_cw=True)
server.start()
time.sleep(0.2)
print("servidor en puerto", server.port)

client = NewcamdClient(
    NewcamdAccount(name="dbg", host="127.0.0.1", port=server.port, username=USER, password=PASSWORD, deskey=DESKEY.hex())
)
status = client.connect()
print("connect ->", status.state, "|", (status.message or "")[:80])
print("  usuario recibido por el servidor:", repr(server.received_user), "clave:", repr(server.received_password))
print("  caid:", status.caid, "providers:", status.providers)
if status.state == "logged_in":
    client.send_ecm(bytes.fromhex("8030123456789abcdef0"), service_id=1)
    time.sleep(1.0)
    print("  ECM enviados:", client.status.ecm_sent, "CW recibidos:", client.status.cw_received, client.status.last_cw)
client.disconnect()
server.stop()
