#!/usr/bin/env bash
# =============================================================================
# TBS WebUI — instalador para Ubuntu 22.04 / 24.04
# =============================================================================
#
# Uso:
#     sudo bash deploy/install.sh [opciones]
#
# Qué hace, en orden:
#   1. Comprueba que se ejecuta como root (si no, se reejecuta con sudo).
#   2. Detecta la distribución (apt) y avisa si no es Ubuntu 22.04/24.04.
#   3. Instala por apt: dvb-tools, v4l-utils, dtv-scan-tables, python3-venv,
#      python3-pip, curl, ca-certificates, openssl, rsync y sqlite3
#      (y nginx si se pide con --nginx). Es idempotente.
#   4. Se asegura de tener Python >= 3.11: usa el del sistema si sirve y, si no,
#      instala `uv` (Astral) para el usuario destino y crea el venv con
#      `uv python install 3.11` (NO toca el Python del sistema).
#   5. Crea/actualiza el entorno virtual <proyecto>/.venv e instala
#      requirements.txt.
#   6. Añade el usuario destino al grupo `video` (acceso a /dev/dvb).
#   7. Crea el directorio de datos y el fichero <proyecto>/.env (con
#      TBS_DATA_DIR y un TBS_SESSION_SECRET aleatorio) si no existen.
#   8. Crea /etc/tbs-webui/tbs-webui.env (EnvironmentFile del servicio).
#   9. Instala la unidad systemd /etc/systemd/system/tbs-webui.service
#      sustituyendo los marcadores __TBS_USER__ / __TBS_PROJECT_DIR__ /
#      __TBS_DATA_DIR__. NO la habilita sin permiso: hay que usar
#      --enable-service o responder que sí en la pregunta final.
#  10. (--nginx) Copia el sitio de Nginx y valida con `nginx -t`.
#  11. Imprime los siguientes pasos (relogin del grupo video, arranque, URL).
#
# Este script NO contiene ninguna credencial: el administrador se crea con
# contraseña aleatoria en el primer arranque (se escribe una vez en el log) o
# con TBS_ADMIN_PASSWORD si se define a mano.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

SERVICE_NAME="tbs-webui"
UNIT_DST="/etc/systemd/system/${SERVICE_NAME}.service"
ENV_DIR="/etc/tbs-webui"
ENV_FILE_SYSTEMD="${ENV_DIR}/tbs-webui.env"
NGINX_SITE="/etc/nginx/sites-available/${SERVICE_NAME}.conf"
NGINX_LINK="/etc/nginx/sites-enabled/${SERVICE_NAME}.conf"

TBS_USER="${SUDO_USER:-}"
DATA_DIR="/var/lib/tbs-webui"
PANEL_HOST="0.0.0.0"
PANEL_PORT="9000"
SERVER_NAME="_"
INSTALL_NGINX=0
ENABLE_NGINX=0
ENABLE_SERVICE=-1   # -1 = preguntar, 0 = no, 1 = sí
SKIP_APT=0
NEED_RELOGIN=0

log()  { printf '[install] %s\n' "$*"; }
warn() { printf '[install] AVISO: %s\n' "$*" >&2; }
die()  { printf '[install] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Uso: sudo bash deploy/install.sh [opciones]

Opciones:
  --user NOMBRE        Usuario que ejecutará el panel (por defecto $SUDO_USER).
                       Se añade al grupo `video` y se usa en User= de la unidad.
  --data-dir RUTA      Directorio de datos (por defecto /var/lib/tbs-webui).
  --host IP            Interfaz de escucha (por defecto 0.0.0.0).
  --port PUERTO        Puerto del panel (por defecto 9000).
  --enable-service     Habilita y arranca el servicio systemd al terminar.
  --no-service         No pregunta: deja el servicio instalado sin habilitar.
  --nginx              Instala el sitio de Nginx (sites-available).
  --enable-nginx       Además lo enlaza en sites-enabled.
  --server-name NOMBRE server_name del sitio Nginx (por defecto "_").
  --skip-apt           No ejecuta apt-get (entornos ya preparados).
  -h, --help           Muestra esta ayuda.

Ejemplos:
  sudo bash deploy/install.sh
  sudo bash deploy/install.sh --user ruben --enable-service
  sudo bash deploy/install.sh --user ruben --nginx --server-name panel.lan
EOF
}

# -----------------------------------------------------------------------------
# 0. Argumentos (antes de pedir root para que --help funcione)
# -----------------------------------------------------------------------------
while [ $# -gt 0 ]; do
    case "$1" in
        --user)          TBS_USER="${2:?falta el nombre de usuario}"; shift 2 ;;
        --user=*)        TBS_USER="${1#*=}"; shift ;;
        --data-dir)      DATA_DIR="${2:?falta la ruta}"; shift 2 ;;
        --data-dir=*)    DATA_DIR="${1#*=}"; shift ;;
        --host)          PANEL_HOST="${2:?falta la IP}"; shift 2 ;;
        --host=*)        PANEL_HOST="${1#*=}"; shift ;;
        --port)          PANEL_PORT="${2:?falta el puerto}"; shift 2 ;;
        --port=*)        PANEL_PORT="${1#*=}"; shift ;;
        --enable-service) ENABLE_SERVICE=1; shift ;;
        --no-service)    ENABLE_SERVICE=0; shift ;;
        --nginx)         INSTALL_NGINX=1; shift ;;
        --enable-nginx)  INSTALL_NGINX=1; ENABLE_NGINX=1; shift ;;
        --server-name)   SERVER_NAME="${2:?falta el nombre}"; shift 2 ;;
        --server-name=*) SERVER_NAME="${1#*=}"; shift ;;
        --skip-apt)      SKIP_APT=1; shift ;;
        -h|--help)       usage; exit 0 ;;
        *)               usage >&2; die "opción desconocida: $1" ;;
    esac
done

# -----------------------------------------------------------------------------
# 1. Privilegios
# -----------------------------------------------------------------------------
if [ "$(id -u)" -ne 0 ]; then
    command -v sudo >/dev/null 2>&1 || die "ejecuta este script como root: sudo bash deploy/install.sh"
    log "se necesitan privilegios de root; reejecutando con sudo…"
    exec sudo -E bash "$0" "$@"
fi

if [ -z "${TBS_USER}" ]; then
    TBS_USER="${SUDO_USER:-}"
fi
[ -n "${TBS_USER}" ] || die "no se pudo determinar el usuario destino; usa --user NOMBRE"
id "${TBS_USER}" >/dev/null 2>&1 || die "el usuario '${TBS_USER}' no existe"
USER_HOME="$(getent passwd "${TBS_USER}" | cut -d: -f6)"
[ -n "${USER_HOME}" ] || die "no se pudo obtener el HOME de '${TBS_USER}'"
if [ "${TBS_USER}" = "root" ]; then
    warn "ejecutar el panel como root no es recomendable; usa --user <usuario normal>"
fi

log "proyecto:   ${PROJECT_DIR}"
log "usuario:    ${TBS_USER} (home ${USER_HOME})"
log "datos:      ${DATA_DIR}"
log "escucha:    ${PANEL_HOST}:${PANEL_PORT}"

# Ejecuta un comando como el usuario destino (el venv debe ser suyo).
as_user() {
    if [ "${TBS_USER}" = "root" ]; then
        "$@"
    else
        sudo -u "${TBS_USER}" -H -- "$@"
    fi
}

# -----------------------------------------------------------------------------
# 2. Distribución
# -----------------------------------------------------------------------------
DISTRO_ID=""; DISTRO_VER=""
if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    DISTRO_ID="${ID:-}"; DISTRO_VER="${VERSION_ID:-}"
fi
case "${DISTRO_ID}" in
    ubuntu)
        case "${DISTRO_VER}" in
            22.04|24.04) : ;;
            *) warn "probado en Ubuntu 22.04/24.04; continúo con ${DISTRO_ID} ${DISTRO_VER}" ;;
        esac
        ;;
    debian) warn "probado en Ubuntu; continúo con Debian ${DISTRO_VER}" ;;
    *)      warn "distribución no reconocida (${DISTRO_ID:-desconocida}); asumo apt" ;;
esac
command -v apt-get >/dev/null 2>&1 || die "este instalador requiere apt (Ubuntu/Debian)"

# -----------------------------------------------------------------------------
# 3. Paquetes del sistema
# -----------------------------------------------------------------------------
APT_PACKAGES="dvb-tools v4l-utils dtv-scan-tables python3-venv python3-pip curl ca-certificates openssl rsync sqlite3"
if [ "${INSTALL_NGINX}" = 1 ]; then
    APT_PACKAGES="${APT_PACKAGES} nginx"
fi

if [ "${SKIP_APT}" = 1 ]; then
    log "apt: omitido (--skip-apt)"
else
    export DEBIAN_FRONTEND=noninteractive
    log "apt-get update"
    apt-get update -qq
    log "apt-get install: ${APT_PACKAGES}"
    # shellcheck disable=SC2086
    apt-get install -y --no-install-recommends ${APT_PACKAGES}
    log "herramientas DVB: $(command -v dvbv5-scan || echo 'dvbv5-scan no encontrado') / $(command -v dvb-fe-tool || echo 'dvb-fe-tool no encontrado')"
    if [ -d /usr/share/dvb ]; then
        log "tablas de escaneo en /usr/share/dvb: $(find /usr/share/dvb -type f | wc -l) ficheros"
    else
        warn "no existe /usr/share/dvb; el escaneo por tabla no estará disponible"
    fi
fi

# -----------------------------------------------------------------------------
# 4-5. Python >= 3.11, entorno virtual y dependencias
# -----------------------------------------------------------------------------
VENV="${PROJECT_DIR}/.venv"
PY="${VENV}/bin/python"
USED_UV=0
UV_BIN=""

python311_ok() {
    "$1" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)' >/dev/null 2>&1
}

if [ -x "${PY}" ] && python311_ok "${PY}"; then
    log "entorno virtual ya presente: ${VENV} ($("${PY}" -V 2>&1))"
else
    HOST_PY=""
    for candidate in python3.13 python3.12 python3.11 python3; do
        if command -v "${candidate}" >/dev/null 2>&1 && python311_ok "$(command -v "${candidate}")"; then
            HOST_PY="$(command -v "${candidate}")"
            break
        fi
    done

    if [ -n "${HOST_PY}" ]; then
        log "creando el entorno virtual con ${HOST_PY}"
        as_user "${HOST_PY}" -m venv "${VENV}"
    else
        log "el sistema no trae Python 3.11+; instalando uv (Astral) para ${TBS_USER}"
        UV_BIN="$(as_user bash -lc 'command -v uv' 2>/dev/null || true)"
        if [ -z "${UV_BIN}" ] || [ ! -x "${UV_BIN}" ]; then
            as_user bash -lc 'curl -LsSf https://astral.sh/uv/install.sh | sh'
            UV_BIN="${USER_HOME}/.local/bin/uv"
        fi
        [ -x "${UV_BIN}" ] || die "no se pudo instalar uv (esperado en ${UV_BIN})"
        log "uv: $("${UV_BIN}" --version)"
        as_user "${UV_BIN}" python install 3.11
        as_user "${UV_BIN}" venv --python 3.11 --seed "${VENV}"
        USED_UV=1
    fi
    [ -x "${PY}" ] || die "no se creó el entorno virtual en ${VENV}"
    log "entorno virtual listo: $("${PY}" -V 2>&1)"
fi

log "instalando dependencias de ${PROJECT_DIR}/requirements.txt"
if [ "${USED_UV}" = 1 ] && [ -x "${UV_BIN}" ]; then
    as_user "${UV_BIN}" pip install --python "${PY}" -r "${PROJECT_DIR}/requirements.txt"
else
    as_user "${PY}" -m pip install --upgrade pip >/dev/null
    as_user "${PY}" -m pip install -r "${PROJECT_DIR}/requirements.txt"
fi
"${PY}" -c 'import fastapi, uvicorn, sqlalchemy, argon2, itsdangerous' \
    || die "faltan dependencias en el entorno virtual"
log "dependencias verificadas (fastapi, uvicorn, sqlalchemy, argon2, itsdangerous)"

if [ -f "${PROJECT_DIR}/run.sh" ]; then
    chmod +x "${PROJECT_DIR}/run.sh"
fi

# -----------------------------------------------------------------------------
# 6. Acceso al hardware: grupo video
# -----------------------------------------------------------------------------
if id -nG "${TBS_USER}" | tr ' ' '\n' | grep -qx video; then
    log "${TBS_USER} ya pertenece al grupo 'video'"
else
    usermod -aG video "${TBS_USER}"
    NEED_RELOGIN=1
    log "${TBS_USER} añadido al grupo 'video'"
fi
if [ -e /dev/dvb/adapter0/frontend0 ]; then
    log "dispositivo DVB detectado: $(ls -l /dev/dvb/adapter0/frontend0 | awk '{print $1, $3, $4}')"
else
    warn "no se detecta /dev/dvb/adapter0/frontend0: el panel arrancará en modo simulación"
fi

# -----------------------------------------------------------------------------
# 7. Directorio de datos y .env del proyecto
# -----------------------------------------------------------------------------
install -d -m 0700 -o "${TBS_USER}" -g "${TBS_USER}" "${DATA_DIR}"
log "directorio de datos preparado: ${DATA_DIR}"

PROJECT_ENV="${PROJECT_DIR}/.env"
if [ -f "${PROJECT_ENV}" ]; then
    log "ya existe ${PROJECT_ENV} (no se modifica)"
else
    [ -f "${PROJECT_DIR}/.env.example" ] || die "falta ${PROJECT_DIR}/.env.example"
    SECRET="$(openssl rand -hex 32)"
    cp "${PROJECT_DIR}/.env.example" "${PROJECT_ENV}"
    {
        printf '\n# --- Añadido por deploy/install.sh (%s) ---\n' "$(date -Is)"
        printf 'TBS_HOST=%s\n' "${PANEL_HOST}"
        printf 'TBS_PORT=%s\n' "${PANEL_PORT}"
        printf 'TBS_DATA_DIR=%s\n' "${DATA_DIR}"
        printf 'TBS_SESSION_SECRET=%s\n' "${SECRET}"
    } >> "${PROJECT_ENV}"
    # El .env puede contener el secreto de sesión: solo el usuario destino.
    chmod 0600 "${PROJECT_ENV}"
    chown "${TBS_USER}:${TBS_USER}" "${PROJECT_ENV}"
    unset SECRET
    log "creado ${PROJECT_ENV} (0600) con TBS_DATA_DIR y un TBS_SESSION_SECRET aleatorio"
fi

# -----------------------------------------------------------------------------
# 8. EnvironmentFile del servicio
# -----------------------------------------------------------------------------
if [ -f "${ENV_FILE_SYSTEMD}" ]; then
    log "ya existe ${ENV_FILE_SYSTEMD} (no se modifica)"
else
    [ -f "${SCRIPT_DIR}/tbs-webui.env.example" ] || die "falta ${SCRIPT_DIR}/tbs-webui.env.example"
    install -d -m 0755 "${ENV_DIR}"
    sed -e "s|^TBS_DATA_DIR=.*|TBS_DATA_DIR=${DATA_DIR}|" \
        -e "s|^TBS_HOST=.*|TBS_HOST=${PANEL_HOST}|" \
        -e "s|^TBS_PORT=.*|TBS_PORT=${PANEL_PORT}|" \
        "${SCRIPT_DIR}/tbs-webui.env.example" > "${ENV_FILE_SYSTEMD}"
    chmod 0600 "${ENV_FILE_SYSTEMD}"
    log "creado ${ENV_FILE_SYSTEMD} (0600)"
fi

# -----------------------------------------------------------------------------
# 9. Unidad systemd
# -----------------------------------------------------------------------------
SERVICE_INSTALLED=0
if ! command -v systemctl >/dev/null 2>&1; then
    warn "systemd no disponible en este sistema: se omite la instalación del servicio"
else
    [ -f "${SCRIPT_DIR}/tbs-webui.service" ] || die "falta ${SCRIPT_DIR}/tbs-webui.service"
    # Solo se sustituye en las líneas activas: los comentarios conservan los
    # marcadores porque son los que documentan la instalación manual.
    sed -e "/^[[:space:]]*#/!s|__TBS_USER__|${TBS_USER}|g" \
        -e "/^[[:space:]]*#/!s|__TBS_PROJECT_DIR__|${PROJECT_DIR}|g" \
        -e "/^[[:space:]]*#/!s|__TBS_DATA_DIR__|${DATA_DIR}|g" \
        "${SCRIPT_DIR}/tbs-webui.service" > "${UNIT_DST}"
    chmod 0644 "${UNIT_DST}"
    systemctl daemon-reload
    SERVICE_INSTALLED=1
    log "unidad instalada: ${UNIT_DST}"

    if systemctl is-active --quiet "${SERVICE_NAME}"; then
        systemctl restart "${SERVICE_NAME}"
        log "servicio reiniciado para aplicar la nueva unidad"
    fi
fi

if [ "${SERVICE_INSTALLED}" = 1 ]; then
    if [ "${ENABLE_SERVICE}" = 1 ]; then
        systemctl enable --now "${SERVICE_NAME}"
        log "servicio habilitado y arrancado"
    elif [ "${ENABLE_SERVICE}" = 0 ]; then
        log "servicio instalado sin habilitar (--no-service)"
    elif [ -t 0 ] && [ -t 1 ]; then
        printf '[install] ¿Habilitar y arrancar el servicio ahora? [s/N] '
        read -r answer || answer=""
        case "${answer}" in
            [sSyY]*) systemctl enable --now "${SERVICE_NAME}"; log "servicio habilitado y arrancado" ;;
            *)       log "servicio instalado sin habilitar" ;;
        esac
    else
        log "sin terminal interactiva: el servicio queda instalado sin habilitar"
    fi
fi

# -----------------------------------------------------------------------------
# 10. Nginx (opcional)
# -----------------------------------------------------------------------------
if [ "${INSTALL_NGINX}" = 1 ]; then
    [ -f "${SCRIPT_DIR}/nginx-tbs-webui.conf" ] || die "falta ${SCRIPT_DIR}/nginx-tbs-webui.conf"
    install -d -m 0755 /etc/nginx/sites-available
    sed -e "/^[[:space:]]*#/!s|__TBS_SERVER_NAME__|${SERVER_NAME}|g" \
        -e "/^[[:space:]]*#/!s|__TBS_BACKEND_PORT__|${PANEL_PORT}|g" \
        "${SCRIPT_DIR}/nginx-tbs-webui.conf" > "${NGINX_SITE}"
    log "sitio Nginx copiado: ${NGINX_SITE} (server_name=${SERVER_NAME})"
    if [ "${ENABLE_NGINX}" = 1 ]; then
        ln -sfn "${NGINX_SITE}" "${NGINX_LINK}"
        log "sitio habilitado: ${NGINX_LINK}"
    else
        log "sitio NO habilitado; actívalo con: sudo ln -s ${NGINX_SITE} ${NGINX_LINK}"
    fi
    if command -v nginx >/dev/null 2>&1; then
        if nginx -t; then
            log "nginx -t: configuración válida"
        else
            warn "nginx -t ha fallado; revisa ${NGINX_SITE} antes de recargar (systemctl reload nginx)"
        fi
    fi
fi

# -----------------------------------------------------------------------------
# 11. Resumen y siguientes pasos
# -----------------------------------------------------------------------------
HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -n "${HOST_IP}" ] || HOST_IP="<IP-del-servidor>"

cat <<EOF

=============================================================================
Instalación completada
  Proyecto:   ${PROJECT_DIR}
  Python:     $("${PY}" -V 2>&1)  (${VENV})
  Datos:      ${DATA_DIR}
  Config:     ${PROJECT_ENV}
              ${ENV_FILE_SYSTEMD}
  Servicio:   ${UNIT_DST}$( [ "${SERVICE_INSTALLED}" = 1 ] || printf ' (systemd no disponible)')

Siguientes pasos
EOF

STEP=1
if [ "${NEED_RELOGIN}" = 1 ]; then
    printf '  %d. CIERRA SESIÓN Y VUELVE A ENTRAR como %s (o ejecuta `newgrp video`)\n' "${STEP}" "${TBS_USER}"
    printf '     para que el grupo "video" aplique; si no, no habrá acceso a /dev/dvb.\n'
    STEP=$((STEP + 1))
fi
if [ "${SERVICE_INSTALLED}" = 1 ]; then
    printf '  %d. Arranca el servicio:   sudo systemctl enable --now %s\n' "${STEP}" "${SERVICE_NAME}"
    STEP=$((STEP + 1))
    printf '  %d. Comprueba:             systemctl status %s --no-pager\n' "${STEP}" "${SERVICE_NAME}"
    STEP=$((STEP + 1))
    printf '  %d. Sigue el log:          journalctl -u %s -f\n' "${STEP}" "${SERVICE_NAME}"
    STEP=$((STEP + 1))
    printf '     (también en modo manual: %s/run.sh)\n' "${PROJECT_DIR}"
else
    printf '  %d. Arranca en manual:     %s/run.sh\n' "${STEP}" "${PROJECT_DIR}"
    STEP=$((STEP + 1))
fi
printf '  %d. Abre el panel:         http://%s:%s/login\n' "${STEP}" "${HOST_IP}" "${PANEL_PORT}"
STEP=$((STEP + 1))
cat <<EOF
  ${STEP}. Primer acceso: si no definiste TBS_ADMIN_PASSWORD, la aplicación crea
     el usuario 'admin' con una contraseña aleatoria que aparece UNA vez en el
     log:
         journalctl -u ${SERVICE_NAME} --since "10 min ago" | grep -i "contraseña"
     Entra y cámbiala en Ajustes → Cambiar contraseña. No la dejes en ficheros.

Comprobación rápida de salud:
  curl -s http://127.0.0.1:${PANEL_PORT}/api/health
=============================================================================
EOF
