#!/usr/bin/env python3
"""Crea el release v1.0.0 en GitHub usando la credencial del credential manager.

Uso: python tools/make_release.py   (no imprime el token)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
GIT = next(
    (Path.home() / "AppData/Local/GitHubDesktop").glob("app-*/resources/app/git/cmd/git.exe"),
    None,
)
if GIT is None:
    sys.exit("No se encontró git de GitHub Desktop")

proc = subprocess.run(
    [str(GIT), "credential", "fill"],
    input="protocol=https\nhost=github.com\n\n",
    capture_output=True,
    text=True,
)
token = ""
for line in proc.stdout.splitlines():
    if line.startswith("password="):
        token = line.split("=", 1)[1].strip()
if not token:
    sys.exit("Sin credencial de GitHub en el credential manager")

OWNER, REPO = "TalaveraSama", "master_dvb_panel"
TAG = "v1.0.0"

BODY = """Panel web (FastAPI + SQLite + WebSocket) para controlar una tarjeta DVB TBS desde el navegador en Ubuntu 22.04/24.04.

## Funciones

- **Login** con argon2id, cookie de sesión `HttpOnly` + `SameSite`, **CSRF** double-submit, límite de intentos (423 + `Retry-After`), revocación de sesiones en servidor y asistente de primer arranque. Todas las rutas `/api/**` y el WebSocket exigen sesión.
- **Sintonización manual**: DVB-S/S2, frecuencia, polaridad, symbol rate, FEC, modulación, rolloff, pilot, stream/PLP, **LNB universal 9750/10600**, tono 22 kHz, voltaje 13/18 V y **DiSEqC 1.0/1.1**.
- **Barras de señal en vivo** por WebSocket cada 500 ms: Signal %, SNR/CNR en dB, BER, PER, bloques con error y flags de LOCK (signal, carrier, viterbi, sync, lock), con **gráfica de los últimos 60 s**.
- **Escaneo de mux**: transponder actual, lista pegada o cualquiera de las tablas de `dtv-scan-tables`; progreso, log en vivo y descubrimiento de servicios por PAT/SDT/PMT (nombre, proveedor, service_id, PIDs de vídeo/audio, TV/Radio, FTA/cifrado).
- **Canales en SQLite** sin duplicados (clave transponder + service_id), con búsqueda, filtros, renombrar y borrar. **Export M3U** y **channels.conf** (dvbv5).
- **Ajustes**: adaptador por defecto, parámetros LNB, periodo de sondeo y cambio de contraseña.
- **WebUI oscura** responsive, **sin CDN externos** (Alpine.js vendorizado), con modo simulación para desarrollar sin tarjeta.
- **Despliegue**: `deploy/install.sh`, servicio **systemd** endurecido y **Nginx** como proxy inverso opcional.

## Acceso al hardware

DVB API de Linux por **ioctl directo** (`FE_GET_INFO`, `FE_READ_STATUS`, `FE_SET_PROPERTY`/`FE_GET_PROPERTY` con propiedades `DTV_*`), sin dependencias externas de Python; incluye modo alternativo con `dvbv5-zap`, mock, lector de tablas `dtv-scan-tables` y parser de secciones SI (PAT/SDT/PMT).

## Verificación

- **280 tests** automáticos en verde (`pytest tbs-webui/tests`).
- **46/46** comprobaciones del smoke end-to-end (`tools/smoke_e2e.py`), en modo simulación y contra hardware real.
- **23/23** comprobaciones en **navegador real** (Chrome headless vía CDP): login, tema oscuro, Alpine.js, barras que cambian en vivo, WebSocket, escaneo, canales y **cero errores de consola**.
- **Hardware real**: TBS 6904se (PCIe, driver `tbsecp3`) con **4 frontends DVB-S/S2/S2x** detectados, `DTV_API_VERSION` 5.12, estadísticas reales de señal (dBm y % relativo) y sintonización aceptada por el driver. Para obtener **LOCK** hace falta cadena de RF correcta (LNB alimentado y antena apuntada): con `dvbv5-scan` de referencia tampoco se lograba en el equipo de pruebas.
- **Revisión de seguridad independiente** en dos rondas: path traversal, CSRF, revocación de sesión (HTTP y WebSocket), `Origin` del WebSocket, `X-Forwarded-For`, enumeración de usuarios por tiempo y cabeceras de seguridad (CSP, COOP, Permissions-Policy).

## Instalación rápida

```bash
sudo apt install -y dvb-tools v4l-utils dtv-scan-tables python3-venv
sudo usermod -aG video $USER   # cierra sesión y vuelve a entrar
sudo bash deploy/install.sh --enable-service
# panel en http://IP:9000/login (asistente de primer arranque)
```

Consulta `tbs-webui/README.md` para la instalación de drivers TBS, permisos, verificación con `dvb-fe-tool`/`dvbv5-scan`, systemd, Nginx, la API completa y resolución de problemas.
"""

payload = {
    "tag_name": TAG,
    "target_commitish": "main",
    "name": f"TBS WebUI {TAG} — Panel web para tarjetas DVB TBS",
    "draft": False,
    "prerelease": False,
    "generate_release_notes": False,
    "body": BODY,
}

headers = {
    "Authorization": f"token {token}",
    "Accept": "application/vnd.github+json",
    "User-Agent": "tbs-webui-release",
}

data = json.dumps(payload).encode("utf-8")
print("payload:", len(data), "bytes; JSON válido:", bool(json.loads(data)))

with httpx.Client(timeout=60.0) as client:
    response = client.post(
        f"https://api.github.com/repos/{OWNER}/{REPO}/releases",
        content=data,
        headers={**headers, "Content-Type": "application/json; charset=utf-8"},
    )
print("HTTP", response.status_code)
if response.status_code in (200, 201):
    info = response.json()
    print("RELEASE CREADO")
    print("  tag:", info["tag_name"])
    print("  name:", info["name"])
    print("  url:", info["html_url"])
else:
    print(response.text[:600])
    sys.exit(1)
