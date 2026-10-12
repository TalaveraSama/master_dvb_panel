#!/usr/bin/env python3
"""Lee la conversacion completa de OSCam con el handshake de 14 bytes.

Objetivo: ver si OSCam envia una respuesta con prefijo de longitud (2 B) y
cuantos bytes manda realmente, sin cortar la lectura.
"""

from __future__ import annotations

import os
import socket
import sys
import time

sys.path.insert(0, os.getcwd())

from app.services.md5crypt import newcamd_password  # noqa: E402
from app.services.newcamd_client import build_packet, derive_triple_des_key, parse_packet  # noqa: E402

HOST, PORT = "127.0.0.1", 5000
DESKEY = bytes.fromhex("0102030405060708091011121314")
MD5PASS = newcamd_password("astra")


def drain(sock: socket.socket, label: str, idle: float = 1.5, limit: int = 4096) -> bytes:
    """Lee hasta que pasen `idle` segundos sin datos nuevos."""
    sock.settimeout(idle)
    data = b""
    while len(data) < limit:
        try:
            chunk = sock.recv(limit - len(data))
        except socket.timeout:
            break
        if not chunk:
            print(f"  {label}: el servidor cerro tras {len(data)} bytes")
            break
        data += chunk
    return data


sock = socket.create_connection((HOST, PORT), timeout=5)
print("conectado")

key = drain(sock, "clave inicial", idle=1.5)
print(f"1) clave inicial: {len(key)} bytes -> {key.hex()}")

if len(key) != 14:
    print("   (no son 14 bytes; se continua igual)")

des = derive_triple_des_key(DESKEY, key)
print("2) DES-EDE2:", des.hex())

for etiqueta, campo in (("md5", MD5PASS.encode()), ("claro", b"astra")):
    payload = b"astra\x00" + campo
    packet = build_packet(0xE0, payload, des, 0)
    print(f"\n3) envio login ({etiqueta}) de {len(packet)} bytes")
    sock.sendall(packet)
    time.sleep(0.2)
    respuesta = drain(sock, f"respuesta {etiqueta}", idle=2.0)
    if not respuesta:
        print(f"   sin respuesta para el login {etiqueta}")
        break
    print(f"   respuesta {len(respuesta)} bytes: {respuesta.hex()}")
    if len(respuesta) >= 2:
        declarado = int.from_bytes(respuesta[:2], "big")
        print(f"   longitud declarada en los 2 primeros bytes: {declarado} (recibidos {len(respuesta)})")
    try:
        tipo, body, _cuerpo, msg_id = parse_packet(respuesta, des)
        nombres = {0xE1: "LOGIN_ACK", 0xE2: "LOGIN_NAK", 0xE4: "CARD_DATA_REQ", 0xE5: "CARD_DATA"}
        print(f"   descifrado: {nombres.get(tipo, hex(tipo))} msg_id={msg_id} body={body[:24]!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"   no descifrable: {exc}")

sock.close()
print("\ncerrado")
