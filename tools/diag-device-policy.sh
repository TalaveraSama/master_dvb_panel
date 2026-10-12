#!/usr/bin/env bash
# Diagnóstico: ¿por qué el servicio no puede abrir /dev/dvb/.../frontend0?
set +e
echo "=== pertenencia al grupo video ==="
id ruben
getent group video

echo
echo "=== permisos y ACL del frontend ==="
ls -l /dev/dvb/adapter0/frontend0
getfacl -p /dev/dvb/adapter0/frontend0 2>/dev/null | head -12

echo
echo "=== ¿systemd aplica política de dispositivos? ==="
systemctl show tbs-webui -p DevicePolicy -p DeviceAllow -p PrivateDevices -p ProtectSystem -p ProtectHome

echo
echo "=== prueba: mismo endurecimiento, varias políticas ==="
for policy in auto closed; do
  echo "--- DevicePolicy=$policy ---"
  echo "rootmaster4.0" | sudo -S -p '' bash -lc "
    systemd-run --unit=tbs-dbg-$policy --wait --collect --pipe \
      -p User=ruben -p Group=video -p DeviceAllow=char-dvb -p DevicePolicy=$policy \
      /usr/bin/python3 -c '
import os, errno
p = \"/dev/dvb/adapter0/frontend0\"
for flags, name in ((os.O_RDONLY, \"O_RDONLY\"), (os.O_RDWR, \"O_RDWR\")):
    try:
        fd = os.open(p, flags | os.O_NONBLOCK)
        os.close(fd)
        print(f\"  {name}: OK\")
    except OSError as exc:
        print(f\"  {name}: {errno.errorcode.get(exc.errno)} {exc.strerror}\")
'
  " 2>&1 | grep -v '^\[sudo\]' | tail -4
done

echo
echo "=== prueba manual sin sandbox pero con Group=video ==="
echo "rootmaster4.0" | sudo -S -p '' bash -lc "
  setpriv --reuid=ruben --regid=video --clear-groups /usr/bin/python3 -c '
import os, errno
p = \"/dev/dvb/adapter0/frontend0\"
try:
    fd = os.open(p, os.O_RDWR | os.O_NONBLOCK); os.close(fd); print(\"  O_RDWR: OK\")
except OSError as exc:
    print(\"  O_RDWR:\", errno.errorcode.get(exc.errno), exc.strerror)
'
" 2>&1 | grep -v '^\[sudo\]' | tail -2

echo
echo "=== ¿hay otro proceso usando el frontend? ==="
for p in /proc/[0-9]*; do
  if ls -l "$p/fd" 2>/dev/null | grep -q '/dev/dvb'; then
    echo "  PID ${p#/proc/} $(cat "$p/comm" 2>/dev/null)"
  fi
done
echo "(fin)"
