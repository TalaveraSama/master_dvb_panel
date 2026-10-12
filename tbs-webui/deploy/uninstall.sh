#!/usr/bin/env bash
# =============================================================================
# TBS WebUI — desinstalador
# =============================================================================
#
# Uso:
#     sudo bash deploy/uninstall.sh [opciones]
#
# Por defecto:
#   - detiene y deshabilita el servicio systemd (si existe),
#   - borra /etc/systemd/system/tbs-webui.service y recarga systemd,
#   - desactiva el sitio de Nginx si estaba enlazado,
#   - CONSERVA los datos (/var/lib/tbs-webui), /etc/tbs-webui y el código.
#
# Con --purge:
#   - borra además el directorio de datos, /etc/tbs-webui, .venv y .env.
#   Nunca borra el código fuente ni el repositorio.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

SERVICE_NAME="tbs-webui"
UNIT_DST="/etc/systemd/system/${SERVICE_NAME}.service"
ENV_DIR="/etc/tbs-webui"
NGINX_SITE="/etc/nginx/sites-available/${SERVICE_NAME}.conf"
NGINX_LINK="/etc/nginx/sites-enabled/${SERVICE_NAME}.conf"
DATA_DIR="/var/lib/tbs-webui"
PURGE=0
ASSUME_YES=0

log()  { printf '[uninstall] %s\n' "$*"; }
warn() { printf '[uninstall] AVISO: %s\n' "$*" >&2; }
die()  { printf '[uninstall] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Uso: sudo bash deploy/uninstall.sh [opciones]

Opciones:
  --data-dir RUTA   Directorio de datos usado en la instalación
                    (por defecto /var/lib/tbs-webui).
  --purge           Borra también los datos, /etc/tbs-webui, .venv y .env.
  --yes, -y         No pide confirmación.
  -h, --help        Muestra esta ayuda.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --data-dir)   DATA_DIR="${2:?falta la ruta}"; shift 2 ;;
        --data-dir=*) DATA_DIR="${1#*=}"; shift ;;
        --purge)      PURGE=1; shift ;;
        --yes|-y)     ASSUME_YES=1; shift ;;
        -h|--help)    usage; exit 0 ;;
        *)            usage >&2; die "opción desconocida: $1" ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    command -v sudo >/dev/null 2>&1 || die "ejecuta este script como root: sudo bash deploy/uninstall.sh"
    log "se necesitan privilegios de root; reejecutando con sudo…"
    exec sudo -E bash "$0" "$@"
fi

confirm() {
    [ "${ASSUME_YES}" = 1 ] && return 0
    [ -t 0 ] || return 1
    printf '[uninstall] %s [s/N] ' "$1"
    read -r answer || answer=""
    case "${answer}" in [sSyY]*) return 0 ;; *) return 1 ;; esac
}

# Borra una ruta solo si es razonable (no /, no directorios de primer nivel,
# no un ancestro del proyecto).
safe_rm_rf() {
    local target="${1:-}"
    [ -n "${target}" ] || { warn "ruta vacía: no se borra nada"; return 1; }
    [ -e "${target}" ] || { log "no existe (nada que borrar): ${target}"; return 0; }
    local resolved
    resolved="$(readlink -f -- "${target}")"
    local depth
    depth="$(printf '%s' "${resolved}" | tr -cd '/' | wc -c)"
    if [ "${depth}" -lt 2 ]; then
        warn "ruta demasiado superficial, se rechaza: ${resolved}"
        return 1
    fi
    case "${PROJECT_DIR}/" in
        "${resolved}"/*) warn "la ruta contiene el proyecto, se rechaza: ${resolved}"; return 1 ;;
    esac
    rm -rf -- "${resolved}"
    log "borrado: ${resolved}"
}

# -----------------------------------------------------------------------------
# 1. Servicio systemd
# -----------------------------------------------------------------------------
if command -v systemctl >/dev/null 2>&1; then
    if systemctl list-unit-files "${SERVICE_NAME}.service" >/dev/null 2>&1; then
        systemctl disable --now "${SERVICE_NAME}" >/dev/null 2>&1 || true
        log "servicio detenido y deshabilitado"
    fi
    if [ -f "${UNIT_DST}" ]; then
        rm -f -- "${UNIT_DST}"
        systemctl daemon-reload
        systemctl reset-failed "${SERVICE_NAME}" >/dev/null 2>&1 || true
        log "unidad eliminada: ${UNIT_DST}"
    else
        log "no había unidad en ${UNIT_DST}"
    fi
else
    warn "systemd no disponible: se omite la parte del servicio"
fi

# -----------------------------------------------------------------------------
# 2. Nginx
# -----------------------------------------------------------------------------
if [ -e "${NGINX_LINK}" ] || [ -e "${NGINX_SITE}" ]; then
    rm -f -- "${NGINX_LINK}" "${NGINX_SITE}"
    log "sitio de Nginx eliminado"
    if command -v nginx >/dev/null 2>&1; then
        if nginx -t >/dev/null 2>&1; then
            systemctl reload nginx >/dev/null 2>&1 || true
            log "nginx recargado"
        else
            warn "nginx -t falla tras eliminar el sitio; revisa /etc/nginx"
        fi
    fi
fi

# -----------------------------------------------------------------------------
# 3. Purga opcional
# -----------------------------------------------------------------------------
if [ "${PURGE}" = 1 ]; then
    log "modo --purge: se borrarán datos, configuración y entorno virtual"
    if confirm "¿Borrar ${DATA_DIR}, ${ENV_DIR}, ${PROJECT_DIR}/.venv y ${PROJECT_DIR}/.env?"; then
        safe_rm_rf "${DATA_DIR}"
        safe_rm_rf "${ENV_DIR}"
        safe_rm_rf "${PROJECT_DIR}/.venv"
        if [ -f "${PROJECT_DIR}/.env" ]; then
            rm -f -- "${PROJECT_DIR}/.env"
            log "borrado: ${PROJECT_DIR}/.env"
        fi
    else
        log "purga cancelada por el usuario"
    fi
else
    log "los datos se conservan en ${DATA_DIR} (usa --purge para borrarlos)"
fi

cat <<EOF

=============================================================================
Desinstalación terminada
  Servicio:   eliminado (${UNIT_DST})
  Datos:      $([ "${PURGE}" = 1 ] && echo "purgados" || echo "conservados en ${DATA_DIR}")
  Config:     $([ "${PURGE}" = 1 ] && echo "purgada (${ENV_DIR})" || echo "conservada en ${ENV_DIR}")
  Código:     intacto en ${PROJECT_DIR}

Para volver a instalar:
  sudo bash ${PROJECT_DIR}/deploy/install.sh --enable-service
=============================================================================
EOF
