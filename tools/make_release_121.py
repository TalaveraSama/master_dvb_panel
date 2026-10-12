#!/usr/bin/env python3
"""Crea el release v1.2.1 (sección Newcamd reencuadrada y editable)."""

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

BODY = """Corrige el encuadre de la pestaña **Newcamd** y añade gestión completa de varias cuentas.

## Correcciones

- **Encuadre**: la sección Newcamd estaba **fuera de `<main>`**, así que perdía el layout del resto de vistas (ancho, tarjetas y cabecera). Ahora vive dentro del contenedor principal y se ve alineada como las demás.
- Columna de acciones con ancho ajustado, celdas alineadas y tabla responsive (oculta columnas secundarias en móvil).

## Edición de cuentas

- **Editor en tarjeta propia** (se abre y se cierra) con rejilla responsive, campos obligatorios marcados (`*`) y botones **Crear / Probar conexión / Cancelar**.
- **Tabla con resumen**: total de cuentas, activas y conectadas.
- **Acciones por cuenta**: `Conectar`, `Probar`, `Editar`, `Duplicar`, `Activar/Desactivar` y `Borrar`.
- **Varias cuentas Newcamd** de verdad: alta, duplicado, edición, habilitado/deshabilitado, notas por cuenta y estado en vivo (ECM enviados, control words, CAID).
- Botones **Conectar todas** / **Desconectar todas**.
- Contraseñas cifradas en SQLite (AES-CTR con clave derivada del secreto de la aplicación) y nunca devueltas por la API.

## Verificación

- **13/13 comprobaciones en navegador real** (Chrome headless vía CDP) contra el panel de `192.168.1.21:9000`: encuadre dentro de `<main>`, alineación, listado, resumen, botones por fila, apertura del editor, alta desde la UI, edición con datos cargados, duplicado y cero errores de consola.
- **294 tests** del proyecto en verde.

Reproducido con: `node tools/ui-check-newcamd.mjs --base http://IP:9000 --user admin --password ... --chrome <ruta>`
"""

response = httpx.post(
    "https://api.github.com/repos/TalaveraSama/master_dvb_panel/releases",
    content=json.dumps(
        {
            "tag_name": "v1.2.1",
            "target_commitish": "main",
            "name": "TBS WebUI v1.2.1 — Newcamd: encuadre corregido y cuentas editables",
            "draft": False,
            "prerelease": False,
            "generate_release_notes": False,
            "body": BODY,
        }
    ).encode("utf-8"),
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
