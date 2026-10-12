#!/usr/bin/env bash
# Arranca/asegura el servicio real en el puerto 9000 con una contraseña de prueba
# definida por entorno (nunca en el repo) y ejecuta el smoke test end-to-end.
set -uo pipefail

PASS="${TBS_ADMIN_PASSWORD:?define TBS_ADMIN_PASSWORD}"
PROJECT=/home/ruben/tbs-webui

echo "=== estado del servicio systemd ==="
systemctl is-active tbs-webui 2>/dev/null || true
systemctl show tbs-webui -p Environment -p EnvironmentFiles -p User -p Group 2>/dev/null | head -5

echo
echo "=== ¿responde el 9000? ==="
curl -s -m 5 http://127.0.0.1:9000/api/health || echo "(sin respuesta)"

echo
echo "=== arranque manual controlado con contraseña conocida ==="
fuser -k 9000/tcp 2>/dev/null || true
sleep 1
export TBS_DATA_DIR=/home/ruben/tbs-webui-data
export TBS_ADMIN_USER=admin
export TBS_ADMIN_PASSWORD="$PASS"
export TBS_LOG_LEVEL=INFO
export TBS_MOCK=0
cd "$PROJECT" || exit 1
setsid nohup .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 9000 --log-level info \
  >/tmp/tbs-webui-9000.log 2>&1 &
echo $! >/tmp/tbs-webui-9000.pid
sleep 5
curl -s -m 5 http://127.0.0.1:9000/api/health
echo

echo "=== smoke end-to-end con hardware real ==="
.venv/bin/python /home/ruben/smoke_e2e.py --base http://127.0.0.1:9000 \
  --user admin --password "$PASS" 2>&1 | tail -60
