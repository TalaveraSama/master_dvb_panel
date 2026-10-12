#!/usr/bin/env bash
# Diagnóstico del fallo "unable to open database file" del servicio systemd.
set +e
echo "=== fichero de entorno (sin secretos) ==="
grep -v PASSWORD /etc/tbs-webui/tbs-webui.env 2>/dev/null | grep -v '^#'

echo
echo "=== directorio de datos ==="
ls -ld /var/lib/tbs-webui 2>&1
ls -la /var/lib/tbs-webui 2>&1 | head -6

echo
echo "=== sandbox efectivo ==="
systemctl show tbs-webui -p ReadWritePaths -p StateDirectory -p ProtectSystem -p ProtectHome -p User -p Group

echo
echo "=== ¿puede ruben:video escribir ahí? ==="
echo "rootmaster4.0" | sudo -S -p '' bash -lc '
  setpriv --reuid=ruben --regid=video --clear-groups sh -c "touch /var/lib/tbs-webui/prueba && echo ESCRIBIBLE || echo NO-ESCRIBIBLE; rm -f /var/lib/tbs-webui/prueba"
' 2>&1 | grep -v '^\[sudo\]'

echo
echo "=== journal: primeras líneas del último arranque ==="
journalctl -u tbs-webui -n 60 --no-pager 2>/dev/null | grep -E "TBS|Base de datos|Traceback|sqlite|Error|error|File \"" | head -18

echo
echo "=== prueba manual con el MISMO entorno y usuario ==="
echo "rootmaster4.0" | sudo -S -p '' bash -lc '
  set -a; . /etc/tbs-webui/tbs-webui.env; set +a
  cd /home/ruben/tbs-webui
  echo "TBS_DATA_DIR=$TBS_DATA_DIR"
  setpriv --reuid=ruben --regid=video --clear-groups sh -c "
    TBS_MOCK=0 TBS_DATA_DIR=$TBS_DATA_DIR /home/ruben/tbs-webui/.venv/bin/python -c \"
import os, sys
sys.path.insert(0, \\\"/home/ruben/tbs-webui\\\")
print(\\\"data_dir=\\\", os.environ.get(\\\"TBS_DATA_DIR\\\"))
from app.config import get_settings
s = get_settings()
print(\\\"dir=\\\", s.data_dir, \\\"existe=\\\", s.data_dir.is_dir(), \\\"escribible=\\\", os.access(s.data_dir, os.W_OK))
from app.database import init_db
init_db()
print(\\\"BD OK:\\\", s.resolved_database_url())
\"
  "
' 2>&1 | grep -v '^\[sudo\]' | tail -12
