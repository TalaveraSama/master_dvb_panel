#!/usr/bin/env bash
# Arranca el panel en el host real (puerto 9000) en segundo plano con nohup.
#
# La contraseña del administrador se toma del entorno (NUNCA del repositorio):
#   TBS_ADMIN_PASSWORD=... bash tools/start-remote.sh [mock|real] [puerto]
# Si no se define, el panel arranca con el asistente web de primer arranque.
set -uo pipefail

MODE="${1:-real}"
PORT="${2:-9000}"
PROJECT="${TBS_REMOTE_DIR:-/home/ruben/tbs-webui}"
LOG="/tmp/tbs-webui-${PORT}.log"
PIDFILE="/tmp/tbs-webui-${PORT}.pid"

# Matar una instancia previa en ese puerto
if [ -f "$PIDFILE" ]; then
  kill "$(cat "$PIDFILE")" 2>/dev/null || true
  sleep 1
  rm -f "$PIDFILE"
fi
fuser -k "${PORT}/tcp" 2>/dev/null || true
sleep 0.5

export TBS_DATA_DIR="${TBS_DATA_DIR:-/home/ruben/tbs-webui-data}"
export TBS_ADMIN_USER="${TBS_ADMIN_USER:-admin}"
export TBS_LOG_LEVEL="${TBS_LOG_LEVEL:-INFO}"
if [ -n "${TBS_ADMIN_PASSWORD:-}" ]; then
  export TBS_ADMIN_PASSWORD
else
  unset TBS_ADMIN_PASSWORD 2>/dev/null || true
  echo "AVISO: sin TBS_ADMIN_PASSWORD: usa el asistente web en /login para crear el admin."
fi
if [ "$MODE" = "mock" ]; then
  export TBS_MOCK=1
else
  export TBS_MOCK=0
fi

cd "$PROJECT" || exit 1
mkdir -p "$TBS_DATA_DIR"
rm -f "$LOG"

setsid nohup "$PROJECT/.venv/bin/python" -m uvicorn app.main:app \
  --host 0.0.0.0 --port "$PORT" --log-level info \
  >"$LOG" 2>&1 &
echo $! >"$PIDFILE"
sleep 4

echo "PID: $(cat "$PIDFILE")  modo: $MODE  log: $LOG"
echo "--- primeras líneas del log ---"
head -20 "$LOG"
echo "--- health ---"
curl -s "http://127.0.0.1:${PORT}/api/health" || echo "(sin respuesta)"
echo
echo "--- proceso ---"
ss -ltnp 2>/dev/null | grep ":${PORT}" || echo "(no escucha en ${PORT})"
