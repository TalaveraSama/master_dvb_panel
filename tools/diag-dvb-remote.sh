#!/usr/bin/env bash
# Diagnóstico de descubrimiento/permisos de /dev/dvb para el panel.
set -uo pipefail
cd /home/ruben/tbs-webui || exit 1
PY=.venv/bin/python

echo "=== id / grupos ==="
id
echo "=== permisos ==="
ls -l /dev/dvb/adapter0/ 2>&1 | head -6
echo "=== lectura/escritura directa ==="
$PY - <<'EOF'
import os
path = "/dev/dvb/adapter0/frontend0"
try:
    fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    print("open(O_RDWR) OK, fd =", fd)
    os.close(fd)
except OSError as exc:
    print("open(O_RDWR) FALLA:", exc)
EOF

echo
echo "=== list_frontends() crudo ==="
$PY - <<'EOF'
import logging, sys
logging.basicConfig(level=logging.DEBUG, stream=sys.stdout)
sys.path.insert(0, "/home/ruben/tbs-webui")
from app.dvb import registry
print("mock mode:", registry.is_mock_mode())
try:
    infos = registry.list_frontends()
    print("frontends:", len(infos))
    for info in infos:
        print("  ", info.adapter, info.frontend, repr(info.name), info.type, info.delivery_systems, info.min_frequency, info.max_frequency)
except Exception as exc:
    import traceback
    traceback.print_exc()
EOF

echo
echo "=== log del panel (errores) ==="
grep -iE "error|traza|exception|denied|permission" /tmp/tbs-webui-9000.log | tail -15 || echo "(sin errores)"
echo
echo "=== últimas líneas del log ==="
tail -12 /tmp/tbs-webui-9000.log
