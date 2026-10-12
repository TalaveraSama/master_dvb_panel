#!/usr/bin/env bash
# Identifica los frontends DVB reales y su capacidad.
set +e
for a in 0 1 2 3; do
  echo "=== adapter$a name ==="
  cat /sys/class/dvb/dvb$a.frontend0/device/name 2>/dev/null \
    || cat /sys/class/dvb/dvb$a.frontend0/device/../name 2>/dev/null \
    || echo "(sin nombre en sysfs)"
  readlink -f /sys/class/dvb/dvb$a.frontend0/device 2>/dev/null
done
echo
echo "=== dvb-fe-tool -a 0 ==="
dvb-fe-tool -a 0 2>&1 | head -30
echo
echo "=== dvb-fe-tool -a 1 ==="
dvb-fe-tool -a 1 2>&1 | head -30
echo
echo "=== dvb-fe-tool --help (uso) ==="
dvb-fe-tool --help 2>&1 | head -20
echo
echo "=== dtv-scan-tables ==="
ls /usr/share/dvb/ 2>&1
ls /usr/share/dvb/dvb-s/ 2>/dev/null | head
echo
echo "=== adapters via /dev/dvb ==="
ls -d /dev/dvb/adapter* 2>&1
