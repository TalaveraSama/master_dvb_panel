#!/usr/bin/env bash
# Arranque de TBS WebUI (desarrollo o ejecución manual).
#
#   ./run.sh                  -> arranca en el puerto 9000
#   TBS_MOCK=1 ./run.sh       -> modo simulación (sin tarjeta)
#   ./run.sh --reload         -> recarga automática (desarrollo)
#
# En producción usar el servicio systemd (ver deploy/tbs-webui.service).
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

VENV="${TBS_VENV:-$PROJECT_DIR/.venv}"
PYTHON="$VENV/bin/python"

if [ ! -x "$PYTHON" ]; then
  echo "ERROR: no existe el entorno virtual en $VENV" >&2
  echo "Ejecuta primero: sudo bash deploy/install.sh   (o ./deploy/install.sh)" >&2
  exit 1
fi

# Variables de entorno del fichero .env (si existe)
if [ -f "$PROJECT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$PROJECT_DIR/.env"
  set +a
fi

HOST="${TBS_HOST:-0.0.0.0}"
PORT="${TBS_PORT:-9000}"
LOG_LEVEL="${TBS_LOG_LEVEL:-info}"

ARGS=(--host "$HOST" --port "$PORT" --log-level "${LOG_LEVEL,,}" --no-access-log)
for arg in "$@"; do
  ARGS+=("$arg")
done

echo "TBS WebUI -> http://${HOST}:${PORT}  (datos: ${TBS_DATA_DIR:-$PROJECT_DIR/data})"
exec "$PYTHON" -m uvicorn app.main:app "${ARGS[@]}"
