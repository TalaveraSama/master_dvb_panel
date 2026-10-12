#!/usr/bin/env python3
"""Captura el handshake real de OSCam (primeros bytes) y prueba variantes de tamaño."""

from __future__ import annotations

import socket
import sys
import time

sys.path.insert(0, "/home/ruben/tbs-webui")

from app.services.newcamd_client import build_packet, derive_triple_des_key  # noqa: E402

USER, PASSWORD = "astra", "astra"
DESKEY = bytes.fromhex("0102030405060708091011121314")

for key_size in (8, 14):
    print(f"\n=== probando con clave inicial de {key_size} bytes ===")
    try:
        sock = socket.create_connection(("127.0.0.1", 5000), timeout=6)
        sock.settimeout(6)
        server_key = b""
        while len(server_key) < key_size:
            chunk = sock.recv(key_size - len(server_key))
            if not chunk:
                break
            server_key += chunk
        print("  clave recibida:", server_key.hex(), f"({len(server_key)} B)")
        key = derive_triple_des_key(DESKEY, server_key)
        print("  clave DES-EDE2:", key.hex())
        payload = USER.encode() + b"\x00" + PASSWORD.encode()
        packet = build_packet(0xE0, payload, key, 0)
        sock.sendall(packet)
        print("  login enviado:", len(packet), "bytes")
        time.sleep(1.0)
        try:
            data = sock.recv(64)
            print("  respuesta:", data.hex() if data else "(vacia)")
            if data:
                tipo_visible = data[0]
                print("  primer byte:", hex(tipo_visible))
        except socket.timeout:
            print("  sin respuesta (timeout)")
        sock.close()
    except OSError as exc:
        print("  error:", exc)
    time.sleep(0.5)
