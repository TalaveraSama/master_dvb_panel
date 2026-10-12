#!/usr/bin/env python3
"""Prueba decisiva: login cifrado con la criptografia REAL de OSCam.

- Variante A: clave y cifrado calculados por el codigo de OSCam (librooscam.so).
- Variante B: los mismos calculos con nuestro port (tbs_newcamd.so).

Si A funciona y B no, el fallo esta en nuestro port. Si fallan las dos, el fallo
esta en el formato del paquete (el framing).
"""

from __future__ import annotations

import ctypes
import os
import socket
import struct
import sys

sys.path.insert(0, os.getcwd())

from app.services.md5crypt import newcamd_password  # noqa: E402
from app.services.newcamd_crypto import load_library  # noqa: E402

DESKEY = bytes.fromhex("0102030405060708091011121314")
HOST, PORT = "127.0.0.1", 5000

nuestro = load_library()
oscam = ctypes.CDLL("/home/ruben/tbs-webui/native/librooscam.so")
oscam.ref_key.restype = ctypes.c_int
oscam.ref_key.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p]
oscam.ref_encrypt.restype = ctypes.c_int
oscam.ref_encrypt.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p]


def clave_oscam(servidor: bytes, deskey: bytes) -> bytes:
    salida = ctypes.create_string_buffer(16)
    oscam.ref_key(servidor, deskey, 14, salida)
    return salida.raw[:16]


def cifrar_oscam(mensaje: bytes, clave: bytes) -> bytes:
    buffer = ctypes.create_string_buffer(mensaje, len(mensaje) + 32)
    tamano = oscam.ref_encrypt(buffer, len(mensaje), clave)
    return buffer.raw[:tamano]


def mensaje_login() -> bytes:
    msg = bytearray(15)
    msg[0] = 0xE0            # tipo: login
    msg[3] = 0x88            # client id 0x8888
    msg[4] = 0x88
    msg += b"astra\x00" + newcamd_password("astra").encode()
    return bytes(msg)


def intento(etiqueta: str, usar_oscam: bool) -> bool:
    sock = socket.create_connection((HOST, PORT), timeout=8)
    sock.settimeout(6)
    servidor = b""
    while len(servidor) < 14:
        trozo = sock.recv(14 - len(servidor))
        if not trozo:
            break
        servidor += trozo
    if len(servidor) < 14:
        print(f"  [{etiqueta}] el servidor no mando los 14 bytes")
        sock.close()
        return False

    if usar_oscam:
        clave = clave_oscam(servidor, DESKEY)
        paquete = cifrar_oscam(mensaje_login(), clave)
    else:
        clave = nuestro.login_key(servidor, DESKEY, 14)
        paquete = nuestro.encrypt(mensaje_login(), clave)

    trama = struct.pack(">H", len(paquete)) + paquete
    sock.sendall(trama)
    try:
        respuesta = sock.recv(256)
    except socket.timeout:
        respuesta = b""
    sock.close()

    if not respuesta:
        print(f"  [{etiqueta}] sin respuesta (OSCam cerro). clave={clave.hex()[:16]}… paquete={len(paquete)}B")
        return False

    tipo = respuesta[2] if len(respuesta) > 2 else None
    print(f"  [{etiqueta}] RESPUESTA {len(respuesta)} B: {respuesta.hex()[:80]}")
    try:
        descifrado = nuestro.decrypt(respuesta[2:], clave)
        print(f"      tipo descifrado: 0x{descifrado[0]:02X} (0xE1 = LOGIN OK, 0xE2 = rechazado)")
    except Exception as exc:  # noqa: BLE001
        print(f"      no se pudo descifrar la respuesta: {exc} (primer byte 0x{tipo:02X})")
    return True


print("=== A) login cifrado con el codigo REAL de OSCam ===")
a = intento("OSCam", usar_oscam=True)
print("=== B) login cifrado con nuestro port ===")
b = intento("nuestro", usar_oscam=False)
print()
print("A (codigo de OSCam) respondio:", a)
print("B (nuestro port)    respondio:", b)
