#!/usr/bin/env python3
"""Diagnóstico directo contra OSCam: usuarios configurados y login Newcamd."""

from __future__ import annotations

import socket
import sys
import time

sys.path.insert(0, "/home/ruben/tbs-webui")

from app.services.newcamd import NewcamdAccount, NewcamdClient, _cipher_key  # noqa: E402

DESKEY = bytes.fromhex("0102030405060708091011121314")

for user, password in (("astra", "astra"),):
    print(f"--- probando usuario {user!r} ---")
    account = NewcamdAccount(
        name="diag", host="127.0.0.1", port=5000, username=user, password=password, deskey=DESKEY.hex()
    )

    # Inspección cruda del handshake
    sock = socket.create_connection(("127.0.0.1", 5000), timeout=5)
    nonce = sock.recv(8)
    print("  nonce:", nonce.hex(), "| clave:", _cipher_key(nonce).hex())
    from app.services.newcamd import encrypt_login

    frame = encrypt_login(user, password, DESKEY, nonce)
    print("  enviando login de", len(frame), "bytes")
    sock.sendall(frame)
    time.sleep(0.3)
    try:
        data = sock.recv(16)
        print("  respuesta:", data.hex())
    except Exception as exc:
        print("  sin respuesta:", exc)
    sock.close()

    client = NewcamdClient(account)
    status = client.connect()
    print("  resultado:", status.state, "|", (status.message or "")[:90])
    client.disconnect()
    time.sleep(0.2)
