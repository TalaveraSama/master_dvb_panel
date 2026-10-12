#!/usr/bin/env bash
# Preparación del host Ubuntu 22.04 con tarjeta TBS real (idempotente).
# Ejecutar con: bash tools/bootstrap-remote.sh
set -uo pipefail
SUDO_PASS="${SUDO_PASS:-}"

step() { echo; echo "### $*"; }
run_sudo() { echo "$SUDO_PASS" | sudo -S -p '' bash -lc "$*"; }

step "1. Paquetes del sistema (dvb-tools, v4l-utils, python3-venv)"
run_sudo "export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get install -y -qq dvb-tools v4l-utils python3-venv python3-pip rsync curl ca-certificates" 2>&1 | tail -5
for t in dvbv5-scan dvbv5-zap dvb-fe-tool v4l2-ctl; do printf "%-12s " "$t"; command -v "$t" || echo MISSING; done

step "2. Usuario en grupo video (acceso a /dev/dvb sin sudo)"
run_sudo "usermod -aG video ruben"
echo "grupos actuales: $(groups)"
echo "-> requiere nuevo login/SSH para aplicar el grupo"

step "3. Python >= 3.11 vía uv (no toca el Python del sistema)"
if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
  curl -LsSf https://astral.sh/uv/install.sh | sh 2>&1 | tail -3
fi
export PATH="$HOME/.local/bin:$PATH"
if command -v uv >/dev/null 2>&1; then
  uv --version
  uv python install 3.11 2>&1 | tail -3
  uv python list 2>&1 | grep -E "3\.1[12]" | head -5
else
  echo "WARN: uv no disponible; se usará python3 del sistema ($(python3 -V))"
fi

step "4. Acceso al hardware"
ls -l /dev/dvb/adapter0/frontend0
if [ -r /dev/dvb/adapter0/frontend0 ] && [ -w /dev/dvb/adapter0/frontend0 ]; then
  echo "frontend0 legible/escribible por $USER: OK"
else
  echo "frontend0 NO accesible directamente (ver grupos/permisos)"
fi

step "5. Puertos en uso (evitar colisiones con :9000)"
ss -ltnp 2>/dev/null | grep -E ":9000|:80|:8080|:8000" || echo "puerto 9000 libre"

step "6. Herramienta dvb-fe-tool sobre cada adaptador"
for a in 0 1 2 3; do
  echo "--- adapter$a"
  timeout 10 dvb-fe-tool -a "$a" 2>&1 | head -12
done
