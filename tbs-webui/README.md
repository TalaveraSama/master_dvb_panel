# TBS WebUI

Panel web para controlar una tarjeta **DVB TBS** (probado con **TBS 6904se**, 4
sintonizadores DVB-S/S2/S2x) desde el navegador en Ubuntu: sintonización manual
con LNB/DiSEqC, barras de señal en vivo por WebSocket, escaneo de transponders,
gestión de canales y exportación a M3U / `channels.conf`.

> Estado de verificación: el backend y la interfaz están **verificados en modo
> simulación** (`TBS_MOCK=1`): 266 pruebas automáticas, 46 comprobaciones
> end-to-end y los contratos HTTP de este documento comprobados contra un
> servidor real. La **sintonización de la tarjeta física está en depuración**:
> el panel la detecta (4 adaptadores) pero el LOCK real aún no está validado.
> Ver [§14 Estado de verificación y limitaciones](#14-estado-de-verificación-y-limitaciones-conocidas).

## Índice

1. [Qué es y arquitectura](#1-qué-es-y-arquitectura)
2. [Requisitos](#2-requisitos)
3. [Instalación de los drivers TBS en Ubuntu](#3-instalación-de-los-drivers-tbs-en-ubuntu)
4. [Permisos del dispositivo](#4-permisos-del-dispositivo-dvb)
5. [Verificar la tarjeta antes del panel](#5-verificar-la-tarjeta-antes-del-panel)
6. [Instalación del panel](#6-instalación-del-panel)
7. [Arrancar como servicio (systemd)](#7-arrancar-como-servicio-systemd)
8. [Nginx como proxy inverso (opcional)](#8-nginx-como-proxy-inverso-opcional)
9. [Uso del panel](#9-uso-del-panel)
10. [API HTTP](#10-api-http)
11. [Solución de problemas](#11-solución-de-problemas)
12. [Desarrollo y pruebas](#12-desarrollo-y-pruebas)
13. [Seguridad](#13-seguridad)
14. [Estado de verificación y limitaciones conocidas](#14-estado-de-verificación-y-limitaciones-conocidas)

---

## 1. Qué es y arquitectura

| Capa | Tecnología |
|---|---|
| Backend | Python **3.11+**, FastAPI, Uvicorn |
| Acceso DVB | `ioctl` de Linux vía `fcntl`/`struct` (stdlib) + `dvbv5-scan`, `dvbv5-zap`, `dvb-fe-tool` |
| Datos | SQLite + SQLAlchemy 2.0 (`data/tbs-webui.db` o `TBS_DATA_DIR`) |
| Frontend | SPA HTML/CSS/JS con **Alpine.js local** (sin CDN), gráfica en canvas |
| Tiempo real | WebSocket `/ws/signal`, sondeo cada 500 ms, histórico de 60 s |
| Servicio | systemd (`deploy/tbs-webui.service`), opcionalmente Nginx delante |
| Seguridad | argon2id, cookie HttpOnly + SameSite, CSRF double-submit, límite de intentos |

El backend **no depende de librerías DVB externas**: habla directamente con
`/dev/dvb/adapterN/frontend0` mediante `ioctl` y usa las herramientas de
`dvb-tools` como apoyo. Todas las llamadas al hardware pasan por un candado
global (`app/dvb/registry.py`): **un solo dueño del frontend a la vez**, así que
sintonizar o escanear mientras otro proceso usa la tarjeta devuelve `503`.

```
tbs-webui/
  app/
    main.py            # FastAPI: routers, estáticos, seguridad, lifespan
    config.py          # ajustes desde variables TBS_* y .env
    database.py        # engine/sesiones SQLite
    models.py          # users, login_attempts, transponders, services, settings, scan_jobs, app_logs
    schemas.py         # contratos pydantic de entrada/salida
    security.py        # argon2id, sesiones firmadas, CSRF, límite de intentos
    deps.py            # dependencias FastAPI (sesión, CSRF, IP real)
    routers/           # health, auth, adapters, tune, status, scan, channels, export, settings, logs, ws
    services/          # device_manager, scan_service, channels_service, exporters, settings_service, si_collector
    dvb/               # types, base, frontend_ioctl, mock, tools, diseqc, satxml, si, registry
    web/               # index.html, login.html, css/, js/, vendor/alpine.min.js
  tests/               # 266 pruebas (pytest)
  deploy/              # install.sh, uninstall.sh, tbs-webui.service, nginx-tbs-webui.conf, tbs-webui.env.example
  requirements.txt     # dependencias de ejecución
  requirements-dev.txt # pytest, httpx, websockets…
  run.sh               # arranque manual
  .env.example         # plantilla de configuración
```

Arranque (equivalente a lo que ejecuta el servicio systemd):

```bash
cd tbs-webui
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 9000
# o, más cómodo:   ./run.sh
```

---

## 2. Requisitos

- **Ubuntu 22.04 LTS o 24.04 LTS** (probado en 22.04.5, kernel 5.15). Debian
  funciona con matices (avisos del instalador).
- **Una tarjeta TBS** PCIe o USB y su driver cargado (ver §3).
- Antena, **LNB** y cableado (para DVB-S/S2 hace falta alimentación 13/18 V y
  tono 22 kHz, que gestiona el propio panel).
- **Python 3.11 o superior.** Ubuntu 22.04 trae 3.10, así que se instala 3.11 con
  `uv` (no toca el Python del sistema) o con el PPA `deadsnakes`.
- Paquetes de sistema: `dvb-tools` (incluye `dvbv5-scan`, `dvbv5-zap`,
  `dvb-fe-tool`), `v4l-utils`, `dtv-scan-tables` (tablas de transponders),
  `python3-venv`, `curl`, `rsync`. Opcional: `nginx`.
- Puertos: **9000/tcp** para el panel (y 80/443 si se publica con Nginx).

Comprobación rápida del sistema:

```bash
lsb_release -ds; uname -r; python3 -V
ls -l /dev/dvb/                 # debe existir si la tarjeta está cargada
command -v dvbv5-scan dvbv5-zap dvb-fe-tool
ls /usr/share/dvb/              # dvb-s, dvb-t, dvb-c, atsc, isdb-t…
```

---

## 3. Instalación de los drivers TBS en Ubuntu

**Lo primero es saber si hace falta instalar un driver.** Los kernels recientes
de Ubuntu ya incluyen el soporte de muchas tarjetas TBS en el árbol oficial:
nuestra **TBS 6904se** usa el módulo **`tbsecp3`** (acompañado de `m88rs6060`,
`tas2101`, `gx1133` y `dvb_core`) y **funciona sin DKMS ni compilación**.

```bash
# ¿Está el módulo cargado?
lsmod | grep -E "tbsecp3|dvb"

# ¿La ve el sistema por PCIe o USB?
lspci | grep -i -E "multimedia|tbs"
lsusb | grep -i tbs

# ¿Aparecen los adaptadores?
ls /dev/dvb/
# Salida típica con una 6904se: adapter0 adapter1 adapter2 adapter3
ls -l /dev/dvb/adapter0/frontend0
# crw-rw---- 1 root video 212, 3 … /dev/dvb/adapter0/frontend0
```

Si la tarjeta **no aparece**, comprueba en este orden:

1. Que el hardware está bien conectado y alimentado (`lspci`/`lsusb` deben verla
   aunque falte el driver).
2. Qué módulos espera tu modelo (`modinfo tbsecp3`, `dmesg | tail -50`).
3. Fuerza la carga: `sudo modprobe tbsecp3` (y mira `dmesg`).
4. Si el kernel no trae soporte para tu modelo, instala los **drivers oficiales**
   de TBS (solo entonces):

```bash
sudo apt install -y build-essential linux-headers-$(uname -r) git
git clone https://github.com/tbsdtv/linux_media
cd linux_media
make
sudo make install
sudo depmod -a
sudo reboot
```

> **Riesgos de compilar módulos:**
> - Con **Secure Boot** activo el kernel rechazará módulos sin firmar; hay que
>   firmarlos o desactivar Secure Boot en la UEFI.
> - Los módulos compilados **se pierden en cada actualización de kernel**: hay
>   que repetir `make && sudo make install && sudo depmod -a` y reiniciar.
> - Mantén el kernel anterior instalado hasta confirmar que el nuevo funciona.

---

## 4. Permisos del dispositivo DVB

Los nodos de `/dev/dvb` pertenecen a `root:video` con modo `0660`, así que el
usuario que ejecuta el panel **debe pertenecer al grupo `video`**:

```bash
ls -l /dev/dvb/adapter0/frontend0      # crw-rw---- root video
id -nG                                 # ¿aparece "video"?
sudo usermod -aG video "$USER"
# IMPORTANTE: cierra sesión y vuelve a entrar (o usa `newgrp video`) para que el
# grupo nuevo se aplique al proceso; hasta entonces el panel verá "permiso denegado".
```

Alternativa por reglas **udev** (útil si prefieres un grupo propio para el panel,
por ejemplo `tbs`), en `/etc/udev/rules.d/70-tbs-dvb.rules`:

```
SUBSYSTEM=="dvb", GROUP="video", MODE="0660"
```

```bash
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Notas:

- **No ejecutes el panel como root** si puedes evitarlo: basta el grupo `video`.
  El instalador usa `User=<tu usuario>` y `Group=video` en la unidad systemd.
- Si además usas una tarjeta TBS **USB con puerto serie** (algunos modelos),
  puede hacer falta el grupo `dialout` para acceder a `/dev/ttyUSB0`:
  `sudo usermod -aG dialout "$USER"` (y volver a iniciar sesión).

---

## 5. Verificar la tarjeta antes del panel

Antes de arrancar el panel, confirma con las herramientas de `dvb-tools` que el
frontend responde. Esto separa los problemas de hardware/driver de los del panel.

```bash
# 1) Capacidades del frontend (no sintoniza): sistema de entrega, caps, rango de frecuencia
dvb-fe-tool -a 0
# Salida típica (TBS 6904se):
#   Device /dev/dvb/adapter0/frontend0 added
#   Delivery systems: DVBS, DVBS2
#   Caps: CAN_2G_MODULATION, CAN_MULTISTREAM, CAN_QPSK
#   DVB API Version 5.12
#   Frequency range: 950000 … 2150000 kHz

# 2) Escaneo real contra un transponder conocido (necesita antena y LNB)
dvbv5-scan -a 0 /usr/share/dvb/dvb-s/Astra-19.2E
# Los transponders que bloquean muestran LOCK=1 y los servicios encontrados
# (nombre, SID, video/audio PID, si están cifrados…).

# 3) Sintonizar y volcar la señal a un fichero (opcional)
dvbv5-zap -a 0 -c /usr/share/dvb/dvb-s/Astra-19.2E -c "Astra 19.2E:11362:H:22000" -o /tmp/mux.ts
```

Cómo interpretar la salida:

| Señal | Significado |
|---|---|
| `LOCK=1` | El demodulador ha enganchado el transponder |
| `signal` / `SNR` | Nivel y relación señal-ruido (los que pinta el panel) |
| `BER` / `ucblocks` | Errores antes/después de corrección |
| `Delivery system: DVBS2` | Debe coincidir con lo que selecciones en el panel |

Errores típicos:

- **`FE_SET_VOLTAGE: Operation not permitted`** o `FE_SET_TONE`: el frontend ya
  está abierto por otro proceso (otro `dvbv5-scan`, `tvheadend`, `VLC`, un segundo
  panel…) o al usuario le falta el grupo `video`. Comprueba quién lo usa con
  `sudo fuser -v /dev/dvb/adapter0/frontend0` y cierra ese proceso o reinicia el
  servicio. La capa DVB del panel devuelve `503` en esta situación.
- **`Device or resource busy`**: mismo caso (dispositivo ocupado).
- **Sin LOCK y sin error**: revisa frecuencia, polaridad, symbol rate, FEC,
  modulación y sobre todo el **LNB** (banda alta/baja, tono 22 kHz, 13/18 V) y el
  **DiSEqC** si hay conmutador.

> El panel implementa su propio candado de dispositivo, pero **no puede**
> coordinarse con procesos externos (tvheadend, VLC, dvbv5-scan manual): no los
> ejecutes a la vez.

---

## 6. Instalación del panel

### 6.1 Instalación automática (recomendada)

```bash
cd tbs-webui
sudo bash deploy/install.sh                 # + --enable-service para arrancarlo ya
```

`install.sh` es **idempotente** (se puede repetir sin miedo) y hace, en orden:

1. Comprueba que se ejecuta como root; si no, se reejecuta con `sudo`.
2. Detecta la distribución (apt) y avisa si no es Ubuntu 22.04/24.04.
3. Instala por apt: `dvb-tools`, `v4l-utils`, `dtv-scan-tables`, `python3-venv`,
   `python3-pip`, `curl`, `ca-certificates`, `openssl`, `rsync`, `sqlite3`
   (y `nginx` con `--nginx`).
4. Se asegura de tener **Python ≥ 3.11**: usa el del sistema si existe y, si no,
   instala **`uv`** de Astral para el usuario destino y crea el venv con
   `uv python install 3.11` (no toca el Python del sistema).
5. Crea `<proyecto>/.venv` e instala `requirements.txt`.
6. Añade el usuario al grupo `video` si todavía no está.
7. Crea el directorio de datos (`/var/lib/tbs-webui` por defecto) y
   `<proyecto>/.env` a partir de `.env.example`, añadiendo `TBS_DATA_DIR` y un
   `TBS_SESSION_SECRET` aleatorio de 64 caracteres (`openssl rand -hex 32`).
8. Crea `/etc/tbs-webui/tbs-webui.env` (EnvironmentFile del servicio, modo 0600).
9. Instala `/etc/systemd/system/tbs-webui.service` sustituyendo los marcadores
   `__TBS_USER__`, `__TBS_PROJECT_DIR__` y `__TBS_DATA_DIR__`. **No habilita el
   servicio sin permiso**: usa `--enable-service` o responde que sí a la pregunta.
10. Con `--nginx`, copia el sitio de Nginx y valida con `nginx -t`.
11. Imprime los siguientes pasos (relogin para el grupo `video`, arranque, URL).

Opciones útiles (`sudo bash deploy/install.sh --help`):

```bash
--user NOMBRE         # usuario que ejecutará el panel (por defecto $SUDO_USER)
--data-dir RUTA       # directorio de datos (por defecto /var/lib/tbs-webui)
--host IP --port N    # interfaz y puerto (por defecto 0.0.0.0:9000)
--enable-service      # systemctl enable --now tbs-webui
--no-service          # no preguntar; dejar la unidad instalada sin habilitar
--nginx --enable-nginx --server-name panel.lan
--skip-apt            # no ejecutar apt-get
```

### 6.2 Instalación manual

```bash
cd tbs-webui

# 1) Paquetes del sistema
sudo apt update
sudo apt install -y dvb-tools v4l-utils dtv-scan-tables python3-venv curl rsync

# 2) Python 3.11 sin tocar el del sistema (recomendado: uv)
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"          # añade ~/.local/bin al PATH
uv python install 3.11
uv venv --python 3.11 --seed .venv
uv pip install --python .venv/bin/python -r requirements.txt

#    Alternativa con deadsnakes (PPA):
#    sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt update
#    sudo apt install -y python3.11 python3.11-venv
#    python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 3) Configuración
cp .env.example .env
$EDITOR .env                            # define TBS_DATA_DIR, puerto, etc.
chmod 600 .env

# 4) Arranque manual
./run.sh                                # http://IP:9000
# o:  .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 9000
```

`run.sh` usa `<proyecto>/.venv` (o `TBS_VENV`), carga `.env` y pasa a Uvicorn el
host/puerto/nivel de log. Acepta argumentos extra (`./run.sh --reload`).

### 6.3 Variables de configuración (`TBS_*`)

Todas son opcionales; el entorno gana sobre el fichero `.env`. Plantilla completa
en `.env.example`:

| Variable | Defecto | Para qué |
|---|---|---|
| `TBS_HOST`, `TBS_PORT` | `0.0.0.0`, `9000` | Interfaz y puerto del panel |
| `TBS_DATA_DIR` | `<proyecto>/data` | BD SQLite, `secret.key` y logs (si no se define: `$XDG_DATA_HOME/tbs-webui` o `<proyecto>/data`) |
| `TBS_DATABASE_URL` | `sqlite:///<DATA_DIR>/tbs-webui.db` | Otra BD (p. ej. PostgreSQL) |
| `TBS_SESSION_SECRET` | (se genera en `secret.key`) | Firma de sesiones |
| `TBS_SESSION_COOKIE`, `TBS_CSRF_COOKIE` | `tbs_session`, `tbs_csrf` | Nombres de cookies |
| `TBS_SESSION_MAX_AGE` | `43200` | Caducidad de la sesión (s) |
| `TBS_SECURE_COOKIES` | `0` | `1` con HTTPS (cookies `Secure`) |
| `TBS_ADMIN_USER` | `admin` | Usuario administrador inicial |
| `TBS_ADMIN_PASSWORD` | (vacío) | Contraseña del primer arranque (bórrala después) |
| `TBS_ALLOW_SETUP` | `1` | Permite `POST /api/auth/setup` |
| `TBS_LOGIN_MAX_ATTEMPTS`, `TBS_LOGIN_WINDOW_SECONDS`, `TBS_LOGIN_LOCK_SECONDS` | `5`, `300`, `300` | Límite de intentos |
| `TBS_MOCK` | `0` | `1` = simulación (también se activa solo si no hay `/dev/dvb`) |
| `TBS_DEFAULT_ADAPTER`, `TBS_DEFAULT_FRONTEND` | `0`, `0` | Adaptador por defecto |
| `TBS_SCAN_TABLES_DIR` | `/usr/share/dvb` | Tablas de `dtv-scan-tables` |
| `TBS_DVBV5_SCAN_BIN`, `TBS_DVBV5_ZAP_BIN`, `TBS_DVB_FE_TOOL_BIN` | `dvbv5-scan`, … | Rutas de las herramientas |
| `TBS_DEVICE_LOCK_TIMEOUT` | `120` | Espera máxima por el candado del dispositivo (s) |
| `TBS_SIGNAL_POLL_MS`, `TBS_HISTORY_SECONDS` | `500`, `60` | Sondeo y ventana de la gráfica |
| `TBS_LOCK_TIMEOUT_S` | `5` | Espera de LOCK por transponder |
| `TBS_LNB_LOW_KHZ`, `TBS_LNB_HIGH_KHZ`, `TBS_LNB_THRESHOLD_KHZ` | `9750`, `10600`, `11700` | LNB universal |
| `TBS_LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR` |

### 6.4 Primer acceso

Con el panel arrancado, abre `http://IP:9000/login`.

Si la base de datos está vacía, en el **arranque** la aplicación crea el usuario
`TBS_ADMIN_USER` (por defecto `admin`):

- con la contraseña de **`TBS_ADMIN_PASSWORD`** si la has definido, o
- con una **contraseña aleatoria que se escribe UNA sola vez en el log**:

```bash
journalctl -u tbs-webui --since "10 min ago" | grep -i "contraseña"
# o, en manual: mira la salida de ./run.sh
```

Entra con ella y cámbiala inmediatamente en **Ajustes → Cambiar contraseña**.
`POST /api/auth/setup` (el asistente de la pantalla de login) solo responde `200`
si en ese momento **no hay ningún usuario**; en un arranque normal el admin ya se
ha creado y devuelve `409`. Nunca dejes credenciales en el repositorio ni en
`/etc/tbs-webui/tbs-webui.env` más tiempo del necesario.

Modo simulación (sin tarjeta, para probar la interfaz):

```bash
TBS_MOCK=1 ./run.sh
# o en la unidad:  TBS_MOCK=1 en /etc/tbs-webui/tbs-webui.env + systemctl restart
```

---

## 7. Arrancar como servicio (systemd)

```bash
# Instalación automática (sustituye los marcadores por ti):
sudo bash deploy/install.sh --enable-service

# Instalación manual de la unidad (el /^#/! deja intactos los comentarios):
sudo sed -e "/^[[:space:]]*#/!s|__TBS_USER__|$USER|g" \
         -e "/^[[:space:]]*#/!s|__TBS_PROJECT_DIR__|$PWD|g" \
         -e "/^[[:space:]]*#/!s|__TBS_DATA_DIR__|/var/lib/tbs-webui|g" \
         deploy/tbs-webui.service | sudo tee /etc/systemd/system/tbs-webui.service
sudo systemctl daemon-reload
sudo systemctl enable --now tbs-webui

systemctl status tbs-webui --no-pager     # estado
journalctl -u tbs-webui -f                # log en vivo
sudo systemctl restart tbs-webui          # tras cambiar .env o la unidad
sudo systemctl stop tbs-webui
```

### 7.1 `deploy/tbs-webui.env.example`

Es la plantilla del **EnvironmentFile** del servicio
(`/etc/tbs-webui/tbs-webui.env`, permisos `0600`). Formato de systemd:
`CLAVE=valor`, sin `export`, con `#` para comentarios. Define al menos
`TBS_DATA_DIR` y elige el puerto; `TBS_SESSION_SECRET` se puede dejar comentado
(la aplicación genera `secret.key`) o fijarse con `openssl rand -hex 32`.

> **Prioridad:** las variables de este fichero **ganan** sobre `<proyecto>/.env`
> (`app/config.py` consulta primero `os.environ`). Si cambias algo, hazlo en
> `/etc/tbs-webui/tbs-webui.env` y reinicia el servicio.

### 7.2 Directivas de seguridad de la unidad

| Directiva | Efecto |
|---|---|
| `User=<usuario>` / `Group=video` | El panel no corre como root y hereda el acceso a `/dev/dvb` |
| `WorkingDirectory=<proyecto>` | `app/` y `.venv/` se resuelven desde ahí |
| `EnvironmentFile=-/etc/tbs-webui/tbs-webui.env` | Configuración; el guion `-` permite que falte |
| `ExecStart=/bin/sh -c 'exec <venv>/bin/python -m uvicorn …'` | Usa `${TBS_HOST:-0.0.0.0}` y `${TBS_PORT:-9000}` aunque falte el fichero |
| `Restart=on-failure`, `RestartSec=3`, `StartLimitBurst=5/60 s` | Se recupera de caídas sin entrar en bucle |
| `KillSignal=SIGINT`, `TimeoutStopSec=20` | Parada ordenada de Uvicorn: libera el frontend y detiene el escaneo |
| `NoNewPrivileges=yes` | Sin ganancia de privilegios (setuid/capabilities) |
| `ProtectSystem=strict` | Todo el sistema en solo lectura, salvo lo indicado en `ReadWritePaths` |
| `ProtectHome=read-only` | `/home` legible (el proyecto puede vivir ahí) pero no escribible |
| `PrivateTmp=yes` | `/tmp` privado. **Ni el proyecto ni los datos pueden estar bajo `/tmp`** |
| `ReadWritePaths=<DATA_DIR>`, `StateDirectory=tbs-webui` | Único directorio escribible: la BD y `secret.key` |
| `DeviceAllow=char-dvb rw`, `DevicePolicy=closed` | Solo se permite el acceso a `/dev/dvb/*` |
| `ProtectKernel*`, `ProtectControlGroups`, `RestrictSUIDSGID`, `RestrictRealtime`, `LockPersonality` | Endurecimiento adicional del kernel |

Verificación de sintaxis y de la política aplicada:

```bash
systemd-analyze verify /etc/systemd/system/tbs-webui.service     # sin salida = OK
systemd-analyze security tbs-webui.service                       # puntuación de exposición
systemctl show tbs-webui -p User -p Group -p ReadWritePaths -p DeviceAllow
```

---

## 8. Nginx como proxy inverso (opcional)

Sirve para publicar el panel en el puerto 80/443 con nombre propio, sin abrir el
9000, y para añadir HTTPS.

```bash
# Automático (instala nginx si falta, copia el sitio y valida):
sudo bash deploy/install.sh --nginx --enable-nginx --server-name panel.lan

# Manual:
sudo apt install -y nginx
sudo cp deploy/nginx-tbs-webui.conf /etc/nginx/sites-available/tbs-webui.conf
sudo sed -i 's|__TBS_SERVER_NAME__|panel.lan|' /etc/nginx/sites-available/tbs-webui.conf
sudo ln -s /etc/nginx/sites-available/tbs-webui.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Qué hace el fichero:

- `upstream tbs_webui_backend` → `127.0.0.1:9000` con `keepalive`.
- `map $http_upgrade $connection_upgrade` + `location /ws/signal` con
  `Upgrade`/`Connection`, `proxy_buffering off` y timeouts de 24 h: **sin esto las
  barras en vivo no funcionan**.
- `proxy_set_header X-Real-IP` / `X-Forwarded-For` / `X-Forwarded-Proto`: el
  límite de intentos por IP y los registros los usan (`app/deps.py:client_ip`).
- `client_max_body_size 8m` y `proxy_read_timeout 300s` para operaciones lentas.
- `location = /api/health` sin `access_log` (monitorización limpia).

Cuidados:

- **No dupliques `default_server` en el puerto 80**: si ya hay un sitio con
  `listen 80 default_server` (el `default` de Ubuntu), quita el enlace o usa un
  `server_name` propio; si no, `nginx -t` fallará.
- `upstream` y `map` van en contexto `http`; en Ubuntu `sites-enabled/*` se
  incluye dentro de `http`, así que el fichero funciona tal cual.

HTTPS con Let's Encrypt:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d panel.lan
# Cookies Secure + reinicio del panel:
echo 'TBS_SECURE_COOKIES=1' | sudo tee -a /etc/tbs-webui/tbs-webui.env
sudo systemctl restart tbs-webui
```

**Subruta:** publicar el panel en `https://host/tbs/` **no está soportado** en
esta versión. `TBS_ROOT_PATH` se lee en `app/config.py` pero la aplicación no lo
aplica al montar la SPA ni los estáticos. Usa un puerto, un subdominio o un
`server_name` distinto.

---

## 9. Uso del panel

### 9.1 Dashboard («Panel de señal»)

- Chapa de estado **SIN SEÑAL / LOCK** y banderas `Señal`, `Portadora`,
  `Viterbi`, `Sinc.`, `Lock`.
- Barras de **Nivel de señal (%)** y **Calidad (%)**.
- Métricas: **SNR** (dB), CNR bruto, potencia (dBm), señal, calidad, **BER**,
  BER previo, **PER**, bloques con error y hora de la última lectura.
- Gráfica de los **últimos 60 s** (señal % en azul, SNR dB en ámbar), actualizada
  cada 500 ms por WebSocket.
- «Sintonización actual»: parámetros activos y botón **Liberar frontend**
  (`POST /api/tune/release`), más quién tiene el adaptador y si está en simulación.

### 9.2 Sintonización manual

Formulario con adaptador/frontend, sistema de entrega (DVB-S2, DVB-S, DVB-T,
DVB-T2, DVB-C, DVB-C2, ISDB-T, ATSC) y, según el sistema:

- **Satélite**: frecuencia (la UI la pide en **MHz** y la convierte a kHz para la
  API), polarización (V/H/L/R), symbol rate (ksps), FEC, modulación, roll-off,
  piloto, inversión, Stream ID/PLP (multistream) y timeout de LOCK.
- **Terrestre/cable**: ancho de banda, intervalo de guarda, modo de transmisión,
  jerarquía, code rate HP/LP.
- **Antena/LNB**: universal 9750/10600 con umbral 11700, tono 22 kHz
  (automático/forzado), voltaje (13 V / 18 V / apagado — automático: **H/R → 18 V,
  V/L → 13 V**) y **DiSEqC 1.0** (posiciones 1-4) o **1.1** (conmutadores 1-16).
  Hasta 4 sintonizadores pueden escanearse por separado (`adapter`/`frontend`).
- Botón «Guardar como predeterminados» (`PUT /api/settings`).

> **Unidades:** la frecuencia que introduces es la **del transponder** (por
> ejemplo 11096 MHz) y el backend **resta el LNB** para calcular la frecuencia
> intermedia (FI) que envía al frontend. El resultado de `POST /api/tune` incluye
> `frequency_khz` (transponder) e `if_frequency_khz` (FI). En la API la frecuencia
> se expresa siempre en **kHz** (portadora), no en MHz.

### 9.3 Escaneo de servicios

- Modo **«Transponder actual»**: escanea lo que tengas sintonizado.
- Modo **«Lista completa (tabla)»**: elige un fichero de `dtv-scan-tables`
  (`GET /api/scan/tables`) como `Astra-19.2E`.
- Ajustes: adaptador, timeout de LOCK y permanencia por transponder.
- Progreso (estado, %, transponders, servicios), **registro en vivo** y tabla de
  servicios encontrados; botones **Iniciar** / **Detener**.

La API también acepta una **lista explícita de transponders**
(`transponders: […]` en `POST /api/scan/start`) aunque la interfaz actual no la
exponga.

### 9.4 Canales

Búsqueda por nombre/proveedor y filtros por tipo (TV/Radio/…), acceso
(FTA/cifrado) y transponder; paginación; renombrado (nombre real o alias),
borrado individual, borrado de seleccionados y «Borrar todos»; exportación a
**M3U** y **`channels.conf`**.

> La exportación **CSV no existe**: solo M3U y `channels.conf`.

### 9.5 Transponders

Lista de transponders guardados (sistema, frecuencia, polarización, symbol rate,
satélite, nº de servicios) con búsqueda y refresco.

### 9.6 Ajustes

Adaptador/frontend por defecto, tipo de LNB (universal o personalizado
9750/10600/11700, tono y voltaje por defecto), timeout de LOCK (s y ms),
permanencia por transponder, intervalo de sondeo de señal (ms), histórico de la
gráfica (s), **cambio de contraseña** y **registro del servidor** (`GET /api/logs`).

---

## 10. API HTTP

Base: `/api`. Autenticación por **cookie de sesión**; las mutaciones exigen además
la cabecera **`X-CSRF-Token`** con el valor `csrf_token` devuelto por
`GET /api/auth/me` o por el login. Las respuestas de error son
`{"detail": …}` (en los `422` de validación, `detail` es una **lista** de errores
de pydantic).

### 10.1 Endpoints

| Método | Ruta | Sesión | Cuerpo / query | Respuesta |
|---|---|---|---|---|
| GET | `/api/health` | público | – | `{status,mock,version,adapters}` |
| GET | `/api/auth/me` | público | – | `{authenticated,username,csrf_token,must_setup,mock,version}` |
| GET | `/api/auth/session` | público | – | Alias de `/api/auth/me` |
| POST | `/api/auth/setup` | público | `{username,password}` | `{ok,username,csrf_token}` |
| POST | `/api/auth/login` | público | `{username,password}` | `{ok,username,csrf_token}` |
| POST | `/api/auth/logout` | – | – | `{ok,detail}` (borra las cookies) |
| POST | `/api/auth/password` | sesión + CSRF | `{current_password,new_password}` | `{ok,detail}` |
| GET | `/api/adapters` | sesión | – | `{mock,locked:{owner,acquired_at,waiting},adapters:[AdapterOut]}` |
| GET | `/api/adapters/{adapter}/{frontend}/info` | sesión | – | `AdapterOut` |
| POST | `/api/tune` | sesión + CSRF | `TuneRequest` | `TuneResult` |
| POST | `/api/tune/release` | sesión + CSRF | – | `{ok,detail}` |
| GET | `/api/status` | sesión | – | `{locked_by,waiting,mock,adapter,frontend,tuned,stats}` |
| GET | `/api/status/history` | sesión | `?seconds=` | `[HistoryPoint]` |
| GET | `/api/scan/tables` | sesión | – | `{bundles:[{name,sat_position,transponders,path}],dir}` |
| POST | `/api/scan/start` | sesión + CSRF | `ScanStartRequest` | `{ok,job_id}` |
| GET | `/api/scan/status` | sesión | – | `{progress,log:[…],services:[…]}` |
| GET | `/api/scan/log` | sesión | `?since=` (epoch) | `[{ts,level,msg,step}]` (lista) |
| POST | `/api/scan/stop` | sesión + CSRF | – | `{ok,detail}` |
| GET | `/api/channels` | sesión | `?q&type&fta&scrambled&transponder_id&limit(1-2000,def 200)&offset` | `{total,items:[ServiceOut]}` |
| PATCH | `/api/channels/{id}` | sesión + CSRF | `{name?}` o `{custom_name?}` | `ServiceOut` |
| DELETE | `/api/channels/{id}` | sesión + CSRF | – | `{ok,detail}` |
| DELETE | `/api/channels` | sesión + CSRF | `?ids=1,2,3` o `{"all":true}` | `{ok,detail}` |
| GET | `/api/transponders` | sesión | `?q&limit(1-5000,def 500)&offset` | `{total,items:[TransponderOut]}` |
| GET | `/api/export/m3u` | sesión | `?q&type&fta&scrambled&transponder_id&radio` | `text/plain` adjunto `canales.m3u` |
| GET | `/api/export/channels.conf` | sesión | mismos filtros (sin `radio`) | `text/plain` adjunto `channels.conf` |
| GET | `/api/settings` | sesión | – | `SettingsOut` |
| PUT | `/api/settings` | sesión + CSRF | `SettingsIn` (parcial) | `SettingsOut` |
| POST | `/api/settings/reset` | sesión + CSRF | – | `SettingsOut` |
| GET | `/api/logs` | sesión | `?limit(1-2000,def 200)&level` | `{items:[{ts,level,source,message,extra}]}` |
| WS | `/ws/signal` | cookie | ver §10.4 | mensajes `stats` / `tuned` / `error` / `pong` |

### 10.2 Códigos de error reales

| Código | Cuándo |
|---|---|
| `400` | Parámetros inválidos: ids de borrado ausentes, tabla de escaneo inexistente, sin objetivo de escaneo, sistema de entrega no soportado |
| `401` | Sin sesión o caducada (`Sesión no válida o caducada`), credenciales incorrectas, contraseña actual incorrecta |
| `403` | CSRF ausente/incorrecto (`Token CSRF ausente o inválido`), asistente inicial deshabilitado |
| `404` | Recurso no encontrado: canal, frontend, tabla de exportación sin canales |
| `409` | Conflicto: ya existe un administrador (asistente), ya hay un escaneo en curso |
| `422` | Validación de pydantic (frecuencia ≤ 0, campos faltantes…); `detail` es una lista |
| `423` | Login bloqueado temporalmente por intentos fallidos (cabecera `Retry-After`) |
| `500` | Error interno de la capa DVB no clasificado |
| `503` | Dispositivo ocupado por otro proceso/dueño (`DeviceBusyError`) |
| `504` | Sintonizado pero sin LOCK dentro del `lock_timeout_s` (`LockTimeoutError`) |

### 10.3 Ejemplos con `curl` (cookie + CSRF)

```bash
BASE=http://127.0.0.1:9000
JAR=$(mktemp)                     # guarda aquí las cookies de sesión

# 1) Login: devuelve el token CSRF
CSRF=$(curl -s -c "$JAR" -X POST "$BASE/api/auth/login" \
        -H 'Content-Type: application/json' \
        -d '{"username":"admin","password":"TU_CONTRASEÑA"}' \
      | python3 -c 'import json,sys; print(json.load(sys.stdin)["csrf_token"])')

# 2) Lecturas: solo cookie
curl -s -b "$JAR" "$BASE/api/health"; echo
curl -s -b "$JAR" "$BASE/api/adapters" | python3 -m json.tool
curl -s -b "$JAR" "$BASE/api/status"   | python3 -m json.tool

# 3) Sintonizar (POST ⇒ cookie + X-CSRF-Token)
#    frequency_khz es la frecuencia del TRANSPONDER en kHz; el backend resta el LNB.
curl -s -b "$JAR" -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
     -X POST "$BASE/api/tune" -d '{
       "adapter": 0, "frontend": 0, "delivery_system": "DVB-S2",
       "frequency_khz": 11096000, "symbol_rate_ksps": 27500,
       "polarization": "V", "fec": "3/4", "modulation": "8PSK",
       "use_lnb": true, "lnb_low_khz": 9750, "lnb_high_khz": 10600
     }' | python3 -m json.tool

curl -s -b "$JAR" -H "X-CSRF-Token: $CSRF" -X POST "$BASE/api/tune/release"; echo

# 4) Escanear una tabla completa
curl -s -b "$JAR" -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
     -X POST "$BASE/api/scan/start" \
     -d '{"adapters":[{"adapter":0,"frontend":0}],"table":"Astra-19.2E"}'; echo
curl -s -b "$JAR" "$BASE/api/scan/status" | python3 -m json.tool
curl -s -b "$JAR" -H "X-CSRF-Token: $CSRF" -X POST "$BASE/api/scan/stop"; echo

# 5) Canales y exportación
curl -s -b "$JAR" "$BASE/api/channels?type=TV&fta=true&limit=50" | python3 -m json.tool
curl -s -b "$JAR" -o canales.m3u        "$BASE/api/export/m3u"
curl -s -b "$JAR" -o channels.conf      "$BASE/api/export/channels.conf"

# 6) Ajustes y cierre de sesión
curl -s -b "$JAR" -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
     -X PUT "$BASE/api/settings" -d '{"default_adapter":0,"ui":{"signal_poll_ms":500}}'
curl -s -b "$JAR" -X POST "$BASE/api/auth/logout"; echo
```

### 10.4 WebSocket `/ws/signal`

- Requiere la cookie de sesión; si no es válida cierra con código **4401**.
- Cliente → servidor: `{"type":"subscribe"}`, `{"type":"tune", …TuneRequest}`,
  `{"type":"release"}`, `{"type":"ping"}`.
- Servidor → cliente: `{"type":"stats", …SignalStatsOut, "history":[…]}` cada
  `ui.signal_poll_ms` (500 ms por defecto), `{"type":"tuned"}`, `{"type":"pong"}`,
  `{"type":"error","detail":"…"}`.

### 10.5 Campos de `TuneRequest` (los importantes)

| Campo | Unidad / valores | Notas |
|---|---|---|
| `adapter`, `frontend` | enteros | `0..31` / `0..7` |
| `delivery_system` | `DVB-S2`, `DVB-S`, `DVB-T`, `DVB-T2`, `DVB-C`, `DVB-C2`, `ISDB-T`, `ATSC` | defecto `DVB-S2` |
| `frequency_khz` | **kHz** del transponder | obligatorio, `> 0` |
| `symbol_rate_ksps` | ksps | DVB-S/S2/C |
| `polarization` | `H`, `V`, `L`, `R` | |
| `fec`, `modulation`, `rolloff`, `pilot`, `inversion` | p. ej. `3/4`, `8PSK`, `0.35`, `ON` | se normalizan a mayúsculas |
| `bandwidth_mhz`, `guard_interval`, `transmission_mode`, `hierarchy`, `code_rate_hp/lp` | DVB-T/T2 | |
| `stream_id`, `plp_id`, `is_multi_stream` | multistream | |
| `use_lnb`, `lnb_low_khz`, `lnb_high_khz`, `lnb_threshold_khz` | `9750/10600/11700` o kHz | `use_lnb=false` no resta nada |
| `tone_22k`, `voltage`, `diseqc` | `true/false`, `13V/18V/OFF`, `1.0:0`…`1.1:15` | `null` = automático |
| `lock_timeout_s` | segundos (`0.5..60`) | defecto 5 |

`ScanStartRequest`: `adapters:[{adapter,frontend}]`, `transponders:[TransponderIn]`
o `table:"Astra-19.2E"` o `current_only:true`, `clear_existing`, `lock_timeout_s`,
`dwell_seconds` (8 s por defecto), `lnb_low_khz/lnb_high_khz/lnb_threshold_khz`,
`use_lnb`.

---

## 11. Solución de problemas

### El panel responde `503` / «dispositivo ocupado»

Otro proceso tiene el frontend abierto (escaneo en curso, otro panel, `tvheadend`,
`VLC`, `dvbv5-zap`…). El candado del panel es por proceso:

```bash
sudo fuser -v /dev/dvb/adapter0/frontend0
systemctl status tbs-webui; journalctl -u tbs-webui -n 50
curl -s -b "$JAR" http://127.0.0.1:9000/api/status    # ¿locked_by?
```

Detén el escaneo (`POST /api/scan/stop`), libera el frontend
(`POST /api/tune/release`) o cierra el otro programa.

### Sintonizo y no hay LOCK

Revisa, en este orden: **frecuencia del transponder** (kHz, no FI), polarización,
symbol rate, FEC, modulación, y sobre todo el **LNB** (banda alta/baja, tono
22 kHz, 13/18 V) y el **DiSEqC** si hay conmutador. Con la antena desalineada o el
LNB sin alimentación nunca habrá LOCK. Compara con `dvbv5-scan -a 0` (§5) para
descartar el hardware.

### `FE_SET_VOLTAGE: Operation not permitted` / `FE_SET_TONE`

Otro proceso usa el frontend o falta el grupo `video` (§4). Cierra el otro proceso
y, si acabas de añadirte al grupo, **vuelve a iniciar sesión**.

### El servicio no arranca

```bash
systemctl status tbs-webui --no-pager
journalctl -u tbs-webui -n 80 --no-pager
```

Causas habituales:

- `Falta …/.venv`: ejecuta `sudo bash deploy/install.sh`.
- `status=200/CHDIR` o `status=226/NAMESPACE`: el proyecto o `TBS_DATA_DIR`
  están bajo `/tmp` y la unidad usa `PrivateTmp=yes`; mueve el proyecto/datos
  fuera de `/tmp` y `/var/tmp`.
- Puerto ocupado (`address already in use`): `ss -ltnp | grep 9000` y cambia
  `TBS_PORT` o para el proceso que lo ocupa.
- Error 203/EXEC: la ruta de `.venv/bin/python` no es ejecutable por el usuario
  de la unidad (`sudo -u <usuario> test -x /ruta/.venv/bin/python`).
- Si acabas de cambiar la unidad o el `.env`:
  `sudo systemctl daemon-reload && sudo systemctl restart tbs-webui`.
  (`systemctl reset-failed tbs-webui` limpia el estado de reintentos si el
  servicio quedó en `activating (auto-restart)`.)

### Permisos

```bash
ls -l /dev/dvb/adapter0/frontend0     # crw-rw---- root video
id -nG                                # el usuario debe estar en "video"
sudo -u <usuario> test -r /dev/dvb/adapter0/frontend0 && echo OK
```

### Inspeccionar la base de datos

```bash
sudo apt install -y sqlite3
sqlite3 /var/lib/tbs-webui/tbs-webui.db \
  "SELECT key, value FROM settings; SELECT COUNT(*) AS canales FROM services; \
   SELECT id, username, last_login_at FROM users;"
sqlite3 /var/lib/tbs-webui/tbs-webui.db ".tables"
# users, login_attempts, transponders, services, settings, scan_jobs, app_logs
```

### Restablecer el administrador

Opción A — con `TBS_ALLOW_SETUP=1` y **sin usuarios**: `POST /api/auth/setup`
crea el admin. Si ya existe, hay que borrarlo antes:

```bash
sudo sqlite3 /var/lib/tbs-webui/tbs-webui.db "DELETE FROM users; DELETE FROM login_attempts;"
sudo systemctl restart tbs-webui     # creará admin + contraseña aleatoria en el log
journalctl -u tbs-webui --since "5 min ago" | grep -i "contraseña"
```

Opción B — fija la contraseña y reinicia, y bórrala después:

```bash
echo 'TBS_ADMIN_PASSWORD=una-clave-temporal-larga' | sudo tee -a /etc/tbs-webui/tbs-webui.env
sudo systemctl restart tbs-webui
sudo sed -i '/^TBS_ADMIN_PASSWORD=/d' /etc/tbs-webui/tbs-webui.env
sudo systemctl restart tbs-webui
```

### Descartar el hardware con el modo simulación

```bash
# Manual:
TBS_MOCK=1 ./run.sh
# Servicio:
sudo sed -i 's/^TBS_MOCK=.*/TBS_MOCK=1/' /etc/tbs-webui/tbs-webui.env
sudo systemctl restart tbs-webui
curl -s http://127.0.0.1:9000/api/health     # "mock":true
```

En simulación la API y la interfaz funcionan igual, con 4 adaptadores TBS
6904se simulados y señal que se mueve, sin tocar `/dev/dvb`.

### Logs

```bash
journalctl -u tbs-webui -f                          # servicio
journalctl -u tbs-webui --since "10 min ago" | grep -i warning
curl -s -b "$JAR" "http://127.0.0.1:9000/api/logs?limit=100"   # avisos y errores en BD
```

### Algunas tablas de escaneo muestran `transponders: 0`

En `/usr/share/dvb/dvb-s` conviven dos formatos: `[CHANNEL]` (108 ficheros en el
host de referencia) y `[channel 12345]` (163 ficheros). El contador del listado
solo reconoce el primero, así que esas tablas aparecen con **0 transponders**
aunque **el escaneo sí funciona** (se parsean correctamente al lanzarlo). Es una
limitación conocida del contador, no del escaneo.

### Nginx no recarga

`nginx -t` dirá qué pasa. Lo más común es un `default_server` duplicado en el
puerto 80 o un `server_name` que choca con el sitio `default`.

---

## 12. Desarrollo y pruebas

```bash
cd tbs-webui
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt   # o con uv

# Suite completa (266 pruebas; incluye la capa DVB con mocks)
.venv/bin/python -m pytest -q

# Solo la capa DVB, o solo la API
.venv/bin/python -m pytest tests/test_dvb_ioctl.py tests/test_dvb_si.py -q
.venv/bin/python -m pytest tests/test_auth_api.py tests/test_scan_api.py -q

# Arranque en modo simulación y smoke test end-to-end
TBS_MOCK=1 TBS_ADMIN_PASSWORD=clave-temporal .venv/bin/python -m uvicorn app.main:app --port 9001 &
.venv/bin/python ../tools/smoke_e2e.py --base http://127.0.0.1:9001 --user admin --password clave-temporal
```

`tools/smoke_e2e.py` comprueba el flujo completo (salud, asistente/login, CSRF,
adaptadores, sintonización, barras en vivo por WebSocket, escaneo con progreso y
log, canales, exportaciones, ajustes y cierre de sesión): **46 comprobaciones**.
Asume **modo simulación** (`verifica mock == true`), así que apúntalo a un
servidor arrancado con `TBS_MOCK=1`. El servidor crea el admin con
`TBS_ADMIN_PASSWORD` en el primer arranque, que es la forma reproducible de que
el login del smoke funcione.

Módulos y responsabilidades: ver la cabecera de `app/main.py`, `app/config.py`,
`app/schemas.py` (contratos JSON) y `app/dvb/` (capa de hardware: `frontend_ioctl`
real, `mock`, `satxml`, `si`, `diseqc`, `tools`, `registry`). El contrato congelado
entre módulos está en `INTERFACES.md` (raíz del repositorio).

---

## 13. Seguridad

- **Contraseñas con argon2id** (`argon2-cffi`), nunca en claro ni en el repositorio.
- **Cookie de sesión** `HttpOnly` + `SameSite=Lax` (y `Secure` con
  `TBS_SECURE_COOKIES=1`); el token va firmado con `TBS_SESSION_SECRET` o con
  `<DATA_DIR>/secret.key` (0600, generado en el primer arranque).
- **CSRF double-submit**: cookie legible `tbs_csrf` + cabecera `X-CSRF-Token` en
  POST/PUT/PATCH/DELETE. Sin ella: `403`.
- **Límite de intentos**: 5 fallos en 5 minutos por usuario+IP → `423` con
  `Retry-After` (configurable).
- **Sin documentación pública** (`/docs`, `/openapi.json` deshabilitados) y
  cabeceras `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`,
  `Cache-Control: no-store` en `/api`.
- **Servicio sin privilegios**: `User=<usuario>`, `Group=video`,
  `NoNewPrivileges`, `ProtectSystem=strict`, `DeviceAllow=char-dvb` (ver §7.2).
- **Los secretos no se versionan**: `.env`, `data/`, `*.key` y `*.db` están en
  `.gitignore`; `deploy/tbs-webui.env.example` no contiene credenciales.
- **Recomendaciones de operación**: publica siempre por HTTPS si el panel sale de
  la LAN; cambia la contraseña inicial; restringe el puerto 9000 con cortafuegos
  o escúchalo en `127.0.0.1` detrás de Nginx (`TBS_HOST=127.0.0.1`).

---

## 14. Estado de verificación y limitaciones conocidas

Verificado (con evidencia en este repositorio y en el host Ubuntu de referencia,
`Ubuntu 22.04.5`, kernel 5.15, `nginx 1.18.0`):

- **Backend/API en modo simulación**: contratos HTTP comprobados uno a uno
  (health, auth, CSRF `403`, sesión `401`, `409`, `422`, `400`, `404`, tablas,
  canales, exportaciones, ajustes, logs).
- **Suite de pruebas**: `266` pruebas recogidas por `pytest` (capa DVB incluida).
- **Smoke end-to-end**: `46/46` comprobaciones superadas con `TBS_MOCK=1`.
- **Instalación**: `deploy/install.sh` ejecutado de principio a fin en una copia
  aislada del host (venv con **Python 3.11.17** vía `uv`, `.env` con secreto
  aleatorio `0600`, `/etc/tbs-webui/tbs-webui.env`, unidad systemd generada sin
  marcadores pendientes, `systemd-analyze verify` sin errores).
- **Unidad systemd**: arrancada de verdad en el host con el endurecimiento
  completo (`ProtectSystem=strict`, `ProtectHome=read-only`, `PrivateTmp`,
  `DevicePolicy=closed`, `DeviceAllow=char-dvb`): `/api/health` responde
  `{"status":"ok","mock":true,"version":"1.0.0","adapters":4}`.
- **`deploy/uninstall.sh`**: detiene/deshabilita el servicio, elimina la unidad y
  conserva los datos (probado en el host; con `--purge` borra datos y configuración).
- **Nginx**: `nginx -t` con el sitio generado → *syntax is ok / test is successful*
  (no se ha recargado Nginx en producción porque el puerto 80 del host ya está en uso).

Pendiente / no verificado:

- **Sintonización real con la tarjeta física** (LOCK, alimentación del LNB, tono
  22 kHz, DiSEqC) en la TBS 6904se: en depuración. El host ya detecta la tarjeta
  (`tbsecp3` cargado, `04:00.0 Multimedia controller: TBS Technologies`, 4
  adaptadores, `mock:false`), pero **no** está confirmado que el panel consiga
  LOCK. Los comandos de §5 son la referencia para comprobarlo.
- **Escaneo real** contra un satélite (depende de antena/LNB).

Discrepancias detectadas entre lo esperado y el código real (documentadas aquí
tal y como se comporta la aplicación):

1. **Asistente de primer arranque**: al iniciar, si no hay usuarios, la
   aplicación **crea ya** el admin (contraseña de `TBS_ADMIN_PASSWORD` o aleatoria
   en el log), por lo que `must_setup` es `false` y `POST /api/auth/setup`
   devuelve `409`. El asistente web solo serviría si se borran los usuarios
   (§11). El primer acceso práctico es leer la contraseña del log.
2. **`TBS_ROOT_PATH`**: se lee en `app/config.py` pero la aplicación **no lo
   aplica**; no se puede publicar en una subruta (usar puerto/subdominio).
3. **`GET /api/scan/log`** devuelve una **lista** `[{ts,level,msg,step}]`, no un
   objeto `{"log":[…]}`.
4. **`DELETE /api/channels`** devuelve `{ok,detail}` (con el número de canales en
   `detail`), no `{deleted:n}`.
5. **Exportación CSV**: no existe; solo M3U y `channels.conf`.
6. **`GET /api/status`** incluye `waiting`, `adapter` y `frontend` además de los
   campos del contrato original.
7. **Etiqueta de voltaje automático en la UI**: el selector dice «Automático
   (V/L = 18 V, H/R = 13 V)», que es **justo lo contrario** de la convención
   implementada (H/R → 18 V, V/L → 13 V). El comportamiento del backend es el
   correcto.
8. **Contador de transponders por tabla**: algunas tablas en formato
   `[channel 12345]` aparecen con `0` en `GET /api/scan/tables` aunque el escaneo
   funciona (§11).

---

Licencia y créditos: proyecto interno de control DVB. Las marcas TBS/TurboSight
pertenecen a sus propietarios.
