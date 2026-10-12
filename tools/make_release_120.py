#!/usr/bin/env python3
"""Crea el release v1.2.0 (Newcamd segun la referencia de Astra + estado real)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import httpx

GIT = next((Path.home() / "AppData/Local/GitHubDesktop").glob("app-*/resources/app/git/cmd/git.exe"), None)
if GIT is None:
    sys.exit("git no encontrado")
cred = subprocess.run(
    [str(GIT), "credential", "fill"], input="protocol=https\nhost=github.com\n\n", capture_output=True, text=True
).stdout
token = next((line.split("=", 1)[1] for line in cred.splitlines() if line.startswith("password=")), "")
if not token:
    sys.exit("sin credencial")

BODY = """Reescribe el cliente **Newcamd** siguiendo la implementación de referencia del repositorio de Astra/CESBO (`modules/softcam/cam/newcamd.c`) y documenta con honestidad qué falta para abrir canales.

## Protocolo corregido (CS378x)

- **Clave del handshake**: `deskey XOR server_key` (XOR cíclico) expandida a 16 bytes con **paridad impar** (`DES_set_odd_parity`), exactamente como `triple_des_set_key()`.
- **Cifrado real**: **DES-EDE2-CBC** con IV aleatorio al final del paquete (antes usaba DES-ECB, que era incorrecto).
- **Framing completo**: `[longitud 2 B][msg_id][provider][tipo][longitud payload][payload][relleno][checksum XOR]`, cifrado desde el byte 2 y validado con checksum.
- **Mensajes**: `0xE0` login (usuario + `\\0` + contraseña), `0xE1` OK / `0xE2` NAK, `0xE4`/`0xE5` datos de tarjeta (CAID, providers) y `0x80..0x8F` ECM con control words.
- **Handshake corregido a 8 bytes**, que es lo que envía realmente el OSCam probado.

## Verificación

- **14 tests del protocolo** con un servidor simulado que implementa el framing real: login OK/NAK, CAID y providers, ECM y recepción de control words, checksum, paridad de la clave derivada y cifrado de contraseñas. Total del proyecto: **294 tests**.
- **Contra el OSCam real** del host (`[newcamd] port = 5000`, usuario `astra`): la conexión TCP y el handshake funcionan y OSCam registra el intento, pero **el framing que responde este OSCam no coincide con el de la referencia** (respuesta cruda de 6 bytes sin `0xE1/0xE2`). `NEWCAMD-ESTADO.md` documenta las medidas exactas y el método con `tcpdump`/`strace` para cerrarlo con un cliente de referencia.

## Conclusión importante: Newcamd solo no abre el canal

El propio código de Astra lo demuestra: **no inyecta claves por ioctl**. Descifra el MPEG-TS en espacio de usuario con **FFdecsa** (`modules/softcam/FFdecsa/`) o **libdvbcsa** y reemite el TS limpio. Un panel en Python no puede descifrar vídeo en tiempo real con garantías, así que:

1. **Recomendado**: dejar la decodificación al servidor que ya funciona (Astra o TVHeadend + OSCam) y usar este panel como interfaz de control: sintoniza, mide señal, escanea, guarda canales, gestiona las cuentas Newcamd y exporta M3U/channels.conf.
2. **Alternativa**: añadir un pipeline en C (FFdecsa/libdvbcsa) alimentado por `dvbv5-zap` y un servidor de TS por HTTP/UDP. Es un desarrollo aparte.

## Verificado también en esta versión

- Transponder **10970 V 27900 en adapter 0: LOCK** con 75.1 % y −54.9 dBm.
- Panel en `192.168.1.21:9000` como servicio systemd (`active` + `enabled`), modo hardware, 4 frontends TBS 6904se.
"""

payload = {
    "tag_name": "v1.2.0",
    "target_commitish": "main",
    "name": "TBS WebUI v1.2.0 — Newcamd (CS378x) según la referencia de Astra",
    "draft": False,
    "prerelease": False,
    "generate_release_notes": False,
    "body": BODY,
}
response = httpx.post(
    "https://api.github.com/repos/TalaveraSama/master_dvb_panel/releases",
    content=json.dumps(payload).encode("utf-8"),
    headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
    },
    timeout=60.0,
)
print("HTTP", response.status_code)
if response.status_code in (200, 201):
    info = response.json()
    print("RELEASE:", info["tag_name"], "->", info["html_url"])
else:
    print(response.text[:300])
