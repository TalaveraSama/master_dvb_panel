#!/usr/bin/env bash
# Script de reconocimiento del host Ubuntu (DVB). Sólo lectura.
set +e
echo "=== OS ==="
cat /etc/os-release | head -3
uname -r
echo
echo "=== WHOAMI ==="
id
echo
echo "=== SUDO ==="
if sudo -n true 2>/dev/null; then echo "sudo-nopass-ok"; else echo "sudo-needs-password"; fi
echo
echo "=== DVB DEV ==="
ls -l /dev/dvb/ 2>&1
echo "---"
ls -l /dev/dvb/adapter*/ 2>&1
echo
echo "=== LSUSB ==="
lsusb 2>&1 | head -20
echo
echo "=== LSPCI (multimedia) ==="
lspci 2>&1 | grep -iE "multimedia|video|tbs|dvb" | head
echo
echo "=== TOOLS ==="
for t in dvbv5-scan dvbv5-zap dvb-fe-tool python3 pip3 rsync curl tar v4l2-ctl; do
  printf "%-14s " "$t"
  command -v "$t" || echo MISSING
done
echo
echo "=== PY VERSION ==="
python3 -VV
echo
echo "=== GROUPS ==="
groups
echo
echo "=== PORTS ==="
(ss -ltnp 2>/dev/null || netstat -ltnp 2>/dev/null) | grep -E ":9000|:80|:443|:8000" 
echo "---"
echo
echo "=== NET ==="
ip -4 addr show | grep inet
echo
echo "=== HOME / DISK ==="
echo "HOME=$HOME"
df -h / | tail -1
echo
echo "=== SYSTEMD ==="
systemctl --version | head -1
echo
echo "=== KERNEL MODULES DVB ==="
lsmod 2>/dev/null | grep -iE "dvb|tbs|m88|tda|si21|stv|cx2" | head -20
echo
echo "=== DVB APT PACKAGES ==="
dpkg -l 2>/dev/null | grep -iE "dvb|v4l" | head -10
echo
echo "=== PY SITE PACKAGES (dvb) ==="
python3 -c "import importlib.util as u; print([m for m in ['fastapi','uvicorn','sqlalchemy','argon2','dvbv5','pydvb'] if u.find_spec(m)])" 2>&1
