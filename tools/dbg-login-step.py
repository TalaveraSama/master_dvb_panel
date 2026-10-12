#!/usr/bin/env python3
"""Paso a paso del login nativo contra OSCam, imprimiendo todo."""

from __future__ import annotations

import os
import socket
import struct
import sys

sys.path.insert(0, os.getcwd())

from app.services.md5crypt import newcamd_password  # noqa: E402
from app.services.newcamd_crypto import load_library  # noqa: E402

DESKEY = bytes.fromhex("0102030405060708091011121314")
HOST, PORT = "127.0.0.1", 5000

crypto = load_library()
print("biblioteca:", crypto.path)
print("self_test:", crypto.self_test())

sock = socket.create_connection((HOST, PORT), timeout=8)
sock.settimeout(6)
servidor = b""
while len(servidor) < 14:
    trozo = sock.recv(14 - len(servidor))
    if not trozo:
        break
    servidor += trozo
print("clave del servidor (14 B):", servidor.hex())

clave = crypto.login_key(servidor, DESKEY, 14)
print("clave de sesion (16 B)  :", clave.hex())

mensaje = bytearray(15)      # preludio: 15 ceros
mensaje[0] = 0xE0            # tipo de mensaje
mensaje[3] = 0x88            # client id (0x8888)
mensaje[4] = 0x88
mensaje += b"astra" + b"\x00" + newcamd_password("astra").encode()
print("mensaje claro  :", len(mensaje), bytes(mensaje).hex())

paquete = crypto.encrypt(bytes(mensaje), clave)
print("paquete cifrado:", len(paquete), paquete.hex())

trama = struct.pack(">H", len(paquete)) + paquete
print("trama enviada  :", len(trama), trama.hex()[:80], "...")
sock.sendall(trama)

try:
    respuesta = sock.recv(256)
except socket.timeout:
    respuesta = b""
print("respuesta:", len(respuesta), respuesta.hex() if respuesta else "(vacia: OSCam cerro sin contestar)")
if respuesta:
    try:
        tipo, cuerpo = crypto.decrypt(respuesta[2:], clave)
        print("tipo=0x%02X cuerpo=%s" % (tipo, cuerpo.hex()))
    except Exception as exc:  # noqa: BLE001
        print("no se pudo descifrar:", exc)
sock.close()
