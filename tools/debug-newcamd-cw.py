"""Depuración del flujo ECM/CW del cliente Newcamd contra el servidor simulado."""

from __future__ import annotations

import sys
import time

sys.path.insert(0, "tbs-webui")
sys.path.insert(0, "tbs-webui/tests")

from test_newcamd import CW, DESKEY, PASSWORD, USER, FakeNewcamdServer  # noqa: E402

from app.services.newcamd import NewcamdAccount, NewcamdClient  # noqa: E402

server = FakeNewcamdServer(send_cw=True)
server.start()
time.sleep(0.2)
print("servidor en puerto", server.port)

received: list[bytes] = []
account = NewcamdAccount(
    name="dbg", host="127.0.0.1", port=server.port, username=USER, password=PASSWORD, deskey=DESKEY.hex()
)
client = NewcamdClient(account, on_control_word=lambda cw, sid: received.append(cw))
print("connect ->", client.connect().to_dict())
print("send_ecm ->", client.send_ecm(bytes.fromhex("8030123456789abcdef0"), service_id=1))

for i in range(20):
    time.sleep(0.1)
    if received:
        break
print("CW recibidos:", [c.hex() for c in received], "esperado:", CW.hex())
print("status:", client.status.to_dict())
print("ECMs recibidos por el servidor:", server.received_ecm)
client.disconnect()
server.stop()
