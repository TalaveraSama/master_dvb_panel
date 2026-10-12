#!/usr/bin/env bash
# Lee la configuración de OSCam y prepara un usuario newcamd para las pruebas.
set -uo pipefail
PW='rootmaster4.0'
SUDO() { echo "$PW" | sudo -S -p '' "$@"; }

echo "=== oscam.conf ==="
SUDO cat /etc/oscam/oscam.conf 2>/dev/null | grep -vE '^#' | grep -v '^$'

echo
echo "=== oscam.user ==="
SUDO cat /etc/oscam/oscam.user 2>/dev/null | grep -vE '^#' | grep -v '^$'

echo
echo "=== oscam.server (resumido) ==="
SUDO grep -E '^\[|label|protocol|device|caid' /etc/oscam/oscam.server 2>/dev/null | head -20

echo
echo "=== proceso oscam y sus puertos ==="
SUDO ss -ltnp 2>/dev/null | grep oscam || true
ps -o pid,user,args -C oscam 2>/dev/null | head -3
