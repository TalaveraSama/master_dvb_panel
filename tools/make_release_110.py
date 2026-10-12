#!/usr/bin/env python3
"""Crea el release v1.1.0 (Newcamd + verificación en hardware real)."""

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

BODY = """Añade gestión **Newcamd** y cierra la verificación con hardware real.

## Novedades

- **Pestaña Newcamd**: alta/edición/borrado de cuentas, probar conexión, conectar y desconectar, y estado en vivo (ECM enviados, control words recibidos, último CW).
- **Cliente del protocolo** en `app/services/newcamd_client.py`: nonce de 8 bytes, DES-ECB con `pycryptodome`, marcos ECM de 64 B y hilo de recepción de control words.
- **Contraseñas cifradas** en SQLite (AES-CTR con clave derivada del secreto de la aplicación); la API nunca las devuelve.
- **API** `/api/newcamd/**` protegida por sesión y CSRF, con 12 tests del protocolo contra un servidor simulado.

## Correcciones

- **CSP**: se añade `unsafe-eval` (Alpine.js evalúa sus directivas con `new Function`); sin esto la SPA no arrancaba en el navegador real.
- El asistente de primer arranque vuelve a aparecer cuando no se define `TBS_ADMIN_PASSWORD` (antes se creaba un admin con contraseña aleatoria en el log).
- Revocación de sesión también en el WebSocket, 404 en PATCH con id fuera de rango, `--no-proxy-headers` y `TBS_DATA_DIR` en la unidad systemd.

## Verificado en hardware real

- **Transponder 10970 V 27900 en adapter0: LOCK** con 76.1 % de señal y −54.7 dBm (flags signal/carrier/viterbi/sync/lock).
- 4 frontends `TurboSight TBS 6904se DVB-S/S2/S2x` detectados, `DTV_API_VERSION` 5.12.
- 292 tests en verde, `tools/smoke_e2e.py` (46 comprobaciones) y verificación en navegador real (Chrome headless vía CDP).

## Pendiente documentado

El **formato exacto del bloque de credenciales** de 32 B no coincide con el OSCam probado: el handshake (nonce + DES) y la conexión funcionan y OSCam registra el intento, pero rechaza el login. `NEWCAMD-ESTADO.md` detalla las 4 variantes probadas y el método (tcpdump/strace de un cliente de referencia) para cerrarlo en 10 minutos. Todo lo demás del panel Newcamd es funcional y está cubierto por tests.
"""

payload = {
    "tag_name": "v1.1.0",
    "target_commitish": "main",
    "name": "TBS WebUI v1.1.0 — Newcamd y verificación en hardware real",
    "draft": False,
    "prerelease": False,
    "generate_release_notes": False,
    "body": BODY,
}
headers = {
    "Authorization": f"token {token}",
    "Accept": "application/vnd.github+json",
    "Content-Type": "application/json",
}
response = httpx.post(
    "https://api.github.com/repos/TalaveraSama/master_dvb_panel/releases",
    content=json.dumps(payload).encode("utf-8"),
    headers=headers,
    timeout=60.0,
)
print("HTTP", response.status_code)
if response.status_code in (200, 201):
    info = response.json()
    print("RELEASE CREADO:", info["tag_name"], "->", info["html_url"])
else:
    print(response.text[:400])
