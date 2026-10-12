#!/usr/bin/env bash
# Inspección de permisos de la BD y prueba de escritura con el usuario del servicio.
set +e
DATA=/var/lib/tbs-webui

echo "=== ficheros en $DATA ==="
ls -la "$DATA" 2>&1

echo
echo "=== atributos extendidos / inmutable ==="
lsattr -d "$DATA" 2>/dev/null
lsattr "$DATA"/* 2>/dev/null

echo
echo "=== escritura como ruben:video en subdirectorio ==="
echo "rootmaster4.0" | sudo -S -p '' bash -lc "
  setpriv --reuid=ruben --regid=video --clear-groups python3 -c '
import sqlite3, os
p = \"/var/lib/tbs-webui/prueba2.db\"
c = sqlite3.connect(p)
c.execute(\"CREATE TABLE t (x)\")
c.execute(\"INSERT INTO t VALUES (1)\")
c.commit(); c.close()
print(\"sqlite OK\", os.stat(p).st_uid, os.stat(p).st_gid)
os.remove(p)
'
" 2>&1 | grep -v '^\[sudo\]'

echo
echo "=== la BD existente, ¿es escribible por ruben:video? ==="
echo "rootmaster4.0" | sudo -S -p '' bash -lc "
  setpriv --reuid=ruben --regid=video --clear-groups python3 -c '
import sqlite3
try:
    c = sqlite3.connect(\"/var/lib/tbs-webui/tbs-webui.db\")
    c.execute(\"CREATE TABLE IF NOT EXISTS prueba (x)\")
    c.commit(); c.close()
    print(\"BD existente: ESCRIBIBLE\")
except Exception as e:
    print(\"BD existente: FALLO ->\", e)
'
" 2>&1 | grep -v '^\[sudo\]'

echo
echo "=== msft/atributos del directorio padre ==="
ls -ld /var/lib /var/lib/tbs-webui

echo
echo "=== journal: contexto anterior (líneas 1-12 del arranque) ==="
journalctl -u tbs-webui -n 200 --no-pager 2>/dev/null | grep -E "Base de datos|data_dir|permisos|TBS" | tail -6

echo
echo "=== prueba con systemd-run con el mismo endurecimiento ==="
echo "rootmaster4.0" | sudo -S -p '' bash -lc '
  systemd-run --unit=tbs-diag --wait --collect --pipe \
    -p User=ruben -p Group=video -p ProtectSystem=strict -p ProtectHome=read-only \
    -p ReadWritePaths=/var/lib/tbs-webui -p PrivateTmp=yes \
    -E TBS_DATA_DIR=/var/lib/tbs-webui -E TBS_ADMIN_PASSWORD=diag-123456 \
    /home/ruben/tbs-webui/.venv/bin/python -c "
import os, sys
sys.path.insert(0, \"/home/ruben/tbs-webui\")
from app.database import init_db
init_db()
print(\"init_db OK en sandbox\")
"
' 2>&1 | grep -v '^\[sudo\]' | tail -6
