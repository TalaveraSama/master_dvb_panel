#!/usr/bin/env bash
# Despliega el código actual al host y reinicia el servicio, manteniendo
# TBS_ADMIN_PASSWORD y las dependencias (pycryptodome).
set -uo pipefail
PW='rootmaster4.0'
PROJECT=/home/ruben/tbs-webui
SUDO() { echo "$PW" | sudo -S -p '' "$@"; }

echo "=== dependencias ==="
"$HOME/.local/bin/uv" pip install --python "$PROJECT/.venv/bin/python" -r "$PROJECT/requirements.txt" pycryptodome -q 2>&1 | tail -2
"$PROJECT/.venv/bin/python" -c "from Crypto.Cipher import DES; import fastapi; print('deps OK')"

echo
echo "=== reiniciar servicio ==="
SUDO systemctl restart tbs-webui
sleep 6
systemctl is-active tbs-webui
curl -s -m 5 http://127.0.0.1:9000/api/health; echo
echo "--- rutas newcamd presentes? ---"
grep -c "newcamd" "$PROJECT/app/main.py"
journalctl -u tbs-webui -n 4 --no-pager | tail -4
