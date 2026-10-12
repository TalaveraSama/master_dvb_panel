#!/usr/bin/env bash
# Diagnóstico de OSCam: configuración de usuarios y log de intentos de login.
set +e
PW='rootmaster4.0'
SUDO() { echo "$PW" | sudo -S -p '' "$@"; }

echo "=== usuarios definidos (oscam.user) ==="
SUDO grep -aE '^\[|user|pwd|group|caid' /etc/oscam/oscam.user 2>/dev/null

echo
echo "=== newcamd en oscam.conf ==="
SUDO grep -aA3 'newcamd' /etc/oscam/oscam.conf 2>/dev/null

echo
echo "=== último log de OSCam sobre newcamd ==="
SUDO tail -25 /var/log/oscam/oscam.log 2>/dev/null | grep -iE 'newcamd|astra|login|pwd|reject|wrong|user' | tail -12

echo
echo "=== todos los procesos oscam ==="
ps aux | grep -E 'oscam' | grep -v grep | head -5

echo
echo "=== pruebas de login con varias variantes de bloque ==="
cd /home/ruben/tbs-webui || exit 1
.venv/bin/python - <<'PY'
import socket, struct, sys, time
sys.path.insert(0, "/home/ruben/tbs-webui")
from Crypto.Cipher import DES

REMOTE_KEY = bytes((0x01,0x02,0x03,0x04,0x05,0x06,0x07,0x08,0x09,0x0A,0x0B,0x0C,0x0D,0x0E))
DESKEY = bytes.fromhex("0102030405060708091011121314")
USER, PWD = "astra", "astra"

def intento(nombre, bloque):
    sock = socket.create_connection(("127.0.0.1", 5000), timeout=5)
    nonce = sock.recv(8)
    key = bytes(a ^ b for a, b in zip(nonce, REMOTE_KEY[:8]))
    cipher = DES.new(key, DES.MODE_ECB)
    payload = USER.encode().ljust(20, b"\x00") + cipher.encrypt(bloque.ljust(32, b"\x00")[:32])
    sock.sendall(payload)
    time.sleep(0.3)
    try:
        data = sock.recv(16)
    except Exception as exc:
        data = b"error " + str(exc).encode()
    print(f"  {nombre:38} -> {data.hex()}")
    sock.close()
    time.sleep(0.2)

intento("pwd20+deskey12 (actual)", PWD.encode().ljust(20, b"\x00") + DESKEY[:12])
intento("pwd14+deskey14", PWD.encode().ljust(14, b"\x00") + DESKEY)
intento("pwd20+deskey14 (34, recorta)", (PWD.encode().ljust(20, b"\x00") + DESKEY)[:32])
intento("solo pwd20", PWD.encode().ljust(20, b"\x00"))
PY
