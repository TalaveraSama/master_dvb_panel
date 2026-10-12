#!/usr/bin/env bash
# Prueba con los MISMOS settings del servicio, aislando ProtectSystem/ProtectHome.
set +e
PW='rootmaster4.0'

run_case() {
  local name="$1"; shift
  echo "--- $name ---"
  echo "$PW" | sudo -S -p '' bash -lc "
    systemd-run --unit=tbs-case-$$ --wait --collect --pipe \
      -p User=ruben -p Group=video -p DeviceAllow=char-dvb -p DevicePolicy=auto \
      $* \
      /home/ruben/tbs-webui/.venv/bin/python -c '
import os, errno, sys
sys.path.insert(0, \"/home/ruben/tbs-webui\")
p = \"/dev/dvb/adapter0/frontend0\"
try:
    fd = os.open(p, os.O_RDWR | os.O_NONBLOCK)
    print(\"  open(O_RDWR): OK fd=\", fd)
    os.close(fd)
except OSError as exc:
    print(\"  open(O_RDWR):\", errno.errorcode.get(exc.errno), exc.strerror)
try:
    from app.dvb import registry
    print(\"  list_frontends:\", [(i.adapter, i.name, list(i.delivery_systems)) for i in registry.list_frontends()][:1])
except Exception as exc:
    print(\"  list_frontends FALLA:\", repr(exc)[:160])
'
  " 2>&1 | grep -v '^\[sudo\]' | grep -vE 'Running as unit|Finished with result|Main processes|Service runtime|CPU time'
}

run_case "A) solo User/Group/DevicePolicy"
run_case "B) + ProtectSystem=strict + ReadWritePaths" \
  -p ProtectSystem=strict -p ReadWritePaths=/var/lib/tbs-webui
run_case "C) + ProtectHome=read-only" \
  -p ProtectSystem=strict -p ReadWritePaths=/var/lib/tbs-webui -p ProtectHome=read-only
run_case "D) + PrivateTmp + NoNewPrivileges + ProtectKernel* + Restrict*" \
  -p ProtectSystem=strict -p ReadWritePaths=/var/lib/tbs-webui -p ProtectHome=read-only \
  -p PrivateTmp=yes -p NoNewPrivileges=yes -p ProtectKernelTunables=yes -p ProtectKernelModules=yes \
  -p ProtectControlGroups=yes -p RestrictSUIDSGID=yes -p RestrictRealtime=yes -p LockPersonality=yes
run_case "E) sandbox completo del servicio" \
  -p ProtectSystem=strict -p ReadWritePaths=/var/lib/tbs-webui -p ProtectHome=read-only \
  -p PrivateTmp=yes -p NoNewPrivileges=yes -p ProtectKernelTunables=yes -p ProtectKernelModules=yes \
  -p ProtectControlGroups=yes -p RestrictSUIDSGID=yes -p RestrictRealtime=yes -p LockPersonality=yes \
  -p StateDirectory=tbs-webui
