#!/usr/bin/env python3
"""Prueba combinaciones de handshake/clave/contrasena contra el OSCam real.

Para cada variante mide si el servidor responde, cierra o se queda callado.
"""

from __future__ import annotations

import os
import socket
import sys
import time

sys.path.insert(0, os.getcwd())

from app.services.md5crypt import newcamd_password  # noqa: E402
from app.services.newcamd_client import (  # noqa: E402
    build_packet,
    derive_triple_des_key,
    parse_packet,
    xor_sum,
)

HOST, PORT = "127.0.0.1", 5000
USER, PASSWORD = "astra", "astra"
DESKEY = bytes.fromhex("0102030405060708091011121314")
MD5PASS = newcamd_password(PASSWORD)


def attempt(label: str, key_size: int, password_field: bytes, key_mode: str = "xor") -> None:
    sock = None
    try:
        sock = socket.create_connection((HOST, PORT), timeout=4)
        sock.settimeout(2.5)
        key = b""
        while len(key) < key_size:
            chunk = sock.recv(key_size - len(key))
            if not chunk:
                break
            key += chunk
        if len(key) != key_size:
            print(f"[{label}] solo {len(key)} bytes de clave")
            return

        if key_mode == "xor":
            des = derive_triple_des_key(DESKEY, key)
        elif key_mode == "xor8":
            # Solo los 8 primeros bytes del server key (OSCam envia 8 en algunos casos)
            des = derive_triple_des_key(DESKEY, key[:8])
        else:  # pad: usar la deskey tal cual expandida
            des = derive_triple_des_key(DESKEY, bytes(14))

        payload = USER.encode() + b"\x00" + password_field
        packet = build_packet(0xE0, payload, des, 0)
        sock.sendall(packet)

        started = time.monotonic()
        try:
            data = sock.recv(256)
        except socket.timeout:
            print(f"[{label}] sin respuesta en 2.5 s (key_size={key_size}, {key_mode})")
            return
        elapsed = time.monotonic() - started
        if not data:
            print(f"[{label}] el servidor CERRO la conexion tras {elapsed:.2f} s (key_size={key_size}, {key_mode})")
            return
        print(f"[{label}] respuesta {len(data)} B en {elapsed:.2f} s: {data.hex()}")
        try:
            tipo, body, _cuerpo, msg_id = parse_packet(data, des)
            etiqueta = {0xE1: "LOGIN_ACK", 0xE2: "LOGIN_NAK"}.get(tipo, f"0x{tipo:02X}")
            print(f"          descifrado OK -> {etiqueta} msg_id={msg_id} checksum=0x{xor_sum(body):02X}")
        except Exception as exc:  # noqa: BLE001
            print(f"          no se pudo descifrar: {exc}")
    except OSError as exc:
        print(f"[{label}] error de red: {exc}")
    finally:
        if sock is not None:
            sock.close()


print("md5 de la contrasena:", MD5PASS)
print()
attempt("14B + md5      ", 14, MD5PASS.encode())
time.sleep(0.4)
attempt("14B + claro    ", 14, PASSWORD.encode())
time.sleep(0.4)
attempt("8B  + md5      ", 8, MD5PASS.encode())
time.sleep(0.4)
attempt("8B  + claro    ", 8, PASSWORD.encode())
time.sleep(0.4)
attempt("14B + md5 xor8 ", 14, MD5PASS.encode(), key_mode="xor8")
time.sleep(0.4)
attempt("14B + md5 srv0 ", 14, MD5PASS.encode(), key_mode="pad")
