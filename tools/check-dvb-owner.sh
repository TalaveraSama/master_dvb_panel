#!/usr/bin/env bash
# ¿Qué está usando la tarjeta DVB y en qué estado está?
set +e
echo "=== fds abiertos sobre /dev/dvb ==="
found=0
for p in /proc/[0-9]*; do
  pid=${p#/proc/}
  if ls -l "$p/fd" 2>/dev/null | grep -q '/dev/dvb'; then
    found=1
    echo "PID $pid  comm=$(cat "$p/comm" 2>/dev/null)  cmd=$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null | cut -c1-120)"
  fi
done
[ "$found" = 0 ] && echo "(ninguno)"

echo
echo "=== servicios DVB/TV en ejecución ==="
systemctl list-units --type=service --state=running 2>/dev/null | grep -iE 'tvhead|dvb|vdr|minisatip|mumudvb|oscam|plex' || echo "(ninguno)"

echo
echo "=== procesos sospechosos ==="
ps aux 2>/dev/null | grep -iE 'tvhead|vdr|dvbv5|dvb-fe|minisatip' | grep -v grep || echo "(ninguno)"

echo
echo "=== módulos ==="
lsmod | grep -E 'tbsecp3|dvb_core|m88rs6060'

echo
echo "=== estado de los 4 frontends (flags actuales) ==="
cd /home/ruben/tbs-webui || exit 1
.venv/bin/python - <<'PY'
import sys
sys.path.insert(0, "/home/ruben/tbs-webui")
from app.dvb import registry
for info in registry.list_frontends():
    fe = registry.open_frontend(info.adapter, 0)
    try:
        fe.open()
        st = fe.read_status()
        stats = fe.read_stats()
        print(f"  adapter{info.adapter}: flags={st} signal={stats.signal_percent}% ({stats.signal_dbm} dBm) snr={stats.snr_db} ber={stats.ber}")
    except Exception as exc:
        print(f"  adapter{info.adapter}: ERROR {exc!r}")
    finally:
        try:
            fe.close()
        except Exception:
            pass
PY

echo
echo "=== últimas líneas del kernel relacionadas ==="
dmesg 2>/dev/null | grep -iE 'tbsecp3|dvb|frontend' | tail -8 || echo "(sin permiso o sin mensajes)"
