#!/usr/bin/env python3
"""Depura el envoltorio Python de la criptografia nativa."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.getcwd())

from app.services.newcamd_crypto import load_library  # noqa: E402

crypto = load_library()
print("biblioteca:", crypto.path if crypto else None)
if crypto is None:
    raise SystemExit("sin biblioteca")

key = crypto.login_key(bytes(14), bytes.fromhex("0102030405060708091011121314"))
print("clave:", key.hex())

mensaje = bytes([0xE0, 0x00, 0x00]) + b"astra\x00" + b"$1$abcdefgh$lU.OJ3dCh8uiqm.kfyIdF."
print("mensaje:", len(mensaje), mensaje.hex())
paquete = crypto.encrypt(mensaje, key)
print("paquete cifrado:", len(paquete), paquete.hex()[:60], "...")
tipo, cuerpo = crypto.decrypt(paquete, key)
print("tipo: 0x%02X" % tipo)
print("cuerpo:", len(cuerpo), cuerpo.hex())
print("cuerpo esperado:", len(mensaje), mensaje.hex())
print("coincide:", cuerpo == mensaje)
print("self_test:", crypto.self_test())
