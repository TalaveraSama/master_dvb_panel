#!/usr/bin/env bash
# Instala/arranca el servicio systemd REAL (modo hardware) y lo verifica.
# Los ficheros con privilegios se generan en /tmp y se instalan con sudo,
# evitando comillas anidadas dentro de `sudo bash -lc "..."`.
set -uo pipefail

PROJECT=/home/ruben/tbs-webui
DATA=/var/lib/tbs-webui
UNIT=/etc/systemd/system/tbs-webui.service
ENVFILE=/etc/tbs-webui/tbs-webui.env
SUDO_PASS='rootmaster4.0'
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

sudo_run() { echo "$SUDO_PASS" | sudo -S -p '' "$@"; }

echo "=== 1. generar unidad y fichero de entorno en $TMP ==="
sed -e 's|__TBS_USER__|ruben|g' \
    -e 's|__TBS_GROUP__|video|g' \
    -e "s|__TBS_PROJECT_DIR__|$PROJECT|g" \
    -e "s|__TBS_DATA_DIR__|$DATA|g" \
    "$PROJECT/deploy/tbs-webui.service" > "$TMP/tbs-webui.service"

sed -e "s|^TBS_DATA_DIR=.*|TBS_DATA_DIR=$DATA|" \
    "$PROJECT/deploy/tbs-webui.env.example" > "$TMP/tbs-webui.env"
grep -q '^TBS_ADMIN_PASSWORD=' "$TMP/tbs-webui.env" || echo 'TBS_ADMIN_PASSWORD=prueba-servicio-2026' >> "$TMP/tbs-webui.env"
grep -q '^TBS_DATA_DIR=' "$TMP/tbs-webui.env" || echo "TBS_DATA_DIR=$DATA" >> "$TMP/tbs-webui.env"

echo "unidad: $(wc -l < "$TMP/tbs-webui.service") líneas"
grep -E '^(Environment|ExecStart|ReadWritePaths|User|Group)=' "$TMP/tbs-webui.service"
echo "entorno:"; grep -E '^TBS_(DATA_DIR|HOST|PORT)' "$TMP/tbs-webui.env"

echo
echo "=== 2. instalar con sudo ==="
sudo_run install -d -o ruben -g video -m 0750 "$DATA"
sudo_run install -d -o root -g root -m 0750 /etc/tbs-webui
sudo_run install -o root -g root -m 0644 "$TMP/tbs-webui.service" "$UNIT"
sudo_run install -o ruben -g video -m 0600 "$TMP/tbs-webui.env" "$ENVFILE"
sudo_run systemctl daemon-reload
sudo_run systemctl enable tbs-webui 2>&1 | tail -1
sudo_run systemctl reset-failed tbs-webui 2>/dev/null || true

echo
echo "=== 3. liberar el puerto 9000 y arrancar ==="
sudo_run fuser -k 9000/tcp 2>/dev/null || true
sleep 1
sudo_run systemctl restart tbs-webui
sleep 7
systemctl is-active tbs-webui
systemctl is-enabled tbs-webui

echo
echo "=== 4. entorno efectivo del proceso ==="
pid=$(systemctl show -p MainPID --value tbs-webui)
if [ -n "$pid" ] && [ "$pid" != "0" ]; then
  sudo_run tr '\0' '\n' < "/proc/$pid/environ" | grep -E '^TBS_(DATA_DIR|MOCK|ADMIN_USER)=' || true
else
  echo "(sin proceso principal)"
fi

echo
echo "=== 5. health y estado ==="
curl -s -m 10 http://127.0.0.1:9000/api/health; echo
systemctl status tbs-webui --no-pager -l 2>/dev/null | head -12
echo "--- últimas líneas del journal ---"
journalctl -u tbs-webui -n 8 --no-pager 2>/dev/null | tail -8
