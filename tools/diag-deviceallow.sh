#!/usr/bin/env bash
# ¿Qué opción de systemd bloquea el acceso a /dev/dvb? Prueba aislada de DeviceAllow.
set +e
PW='rootmaster4.0'
PY=/usr/bin/python3
CODE='import os, errno
p = "/dev/dvb/adapter0/frontend0"
try:
    fd = os.open(p, os.O_RDWR | os.O_NONBLOCK)
    os.close(fd)
    print("  open OK")
except OSError as exc:
    print("  open " + str(errno.errorcode.get(exc.errno)) + " " + str(exc.strerror))'

probe() {
  local label="$1"; shift
  printf -- "--- %s ---\n" "$label"
  echo "$PW" | sudo -S -p '' systemd-run --unit="tbs-probe-$$-$RANDOM" --wait --collect --pipe \
    -p User=ruben -p Group=video "$@" "$PY" -c "$CODE" 2>&1 \
    | grep -vE '^\[sudo\]|Running as unit|Finished with result|Main processes|Service runtime|CPU time'
}

probe "sin DevicePolicy ni DeviceAllow"
probe "DevicePolicy=auto (sin allow)" -p DevicePolicy=auto
probe "DeviceAllow=char-dvb rw" -p DevicePolicy=auto -p "DeviceAllow=char-dvb rw"
probe "DeviceAllow=char-dvb (sin rw)" -p DevicePolicy=auto -p "DeviceAllow=char-dvb"
