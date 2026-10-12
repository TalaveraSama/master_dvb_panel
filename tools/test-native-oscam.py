#!/usr/bin/env python3
"""Prueba el modulo Newcamd nativo: autoprueba y conexion real a OSCam."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.getcwd())

from app.services.newcamd_client import NewcamdAccount, NewcamdClient  # noqa: E402
from app.services.newcamd_crypto import is_available, library_path, unavailable_reason  # noqa: E402
from app.services.newcamd_crypto import load_library  # noqa: E402

print("biblioteca:", library_path())
print("disponible:", is_available())
print("motivo:", unavailable_reason())
crypto = load_library()
print("autoprueba cifrado/descifrado:", crypto.self_test() if crypto else "sin biblioteca")

if crypto is not None:
    # Clave derivada con un vector fijo, para comparar con `make test`
    key = crypto.login_key(
        bytes.fromhex("00112233445566778899aabbccdd"),
        bytes.fromhex("0102030405060708091011121314"),
    )
    print("clave de sesion (vector fijo):", key.hex())

print("\n=== CONEXION REAL A OSCam (127.0.0.1:5000) ===")
account = NewcamdAccount(
    name="oscam-nativo",
    host="127.0.0.1",
    port=5000,
    username="astra",
    password="astra",
    deskey="0102030405060708091011121314",
    use_native_crypto=True,
)
client = NewcamdClient(account, timeout=8.0)
status = client.connect()
print("estado:", status.state)
print("mensaje:", status.message)
print("CAID:", status.caid, "providers:", status.providers)
print("bytes out:", status.bytes_out, "in:", status.bytes_in)

if status.state == "logged_in":
    print("\n*** LOGIN CORRECTO CONTRA OSCam ***")
    from app.services.md5crypt import newcamd_password  # noqa: E402

    print("(contrasena enviada en formato newcamd:", newcamd_password("astra"), ")")
    ecm = bytes.fromhex("8030100102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f20")
    for intento in range(3):
        enviado = client.send_ecm(ecm, service_id=1, ca_system_id=0x1802)
        print(f"ECM {intento + 1} enviado:", enviado)
        import time

        time.sleep(1.5)
        print("  ECM:", client.status.ecm_sent, "CW:", client.status.cw_received, "ultimo CW:", client.status.last_cw)
    print("estado final:", client.status.to_dict())

client.disconnect()
print("desconectado")
