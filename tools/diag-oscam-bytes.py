#!/usr/bin/env python3
"""Diagnostico del handshake Newcamd contra el OSCam real.

Mide cuantos bytes entran y salen en cada paso usando /proc/net/snmp, para ver
exactamente donde se corta la conversacion (sin depender de tcpdump).
"""

from __future__ import annotations

import os
import socket
import sys
import time

sys.path.insert(0, os.getcwd())

from app.services.md5crypt import newcamd_password  # noqa: E402
from app.services.newcamd_client import build_packet, derive_triple_des_key, parse_packet  # noqa: E402

HOST, PORT = "127.0.0.1", 5000
USER, PASSWORD = "astra", "astra"
DESKEY = bytes.fromhex("0102030405060708091011121314")


def tcp_counters(port: int) -> tuple[int, int]:
    """(bytes_in, bytes_out) de las conexiones TCP que tocan `port`."""

    def scan(path: str) -> int:
        total = 0
        try:
            with open(path, "r", encoding="ascii") as handle:
                next(handle)
                for line in handle:
                    fields = line.split()
                    if len(fields) < 10:
                        continue
                    local, remote = fields[1], fields[2]
                    if local.endswith(":%04X" % port) or remote.endswith(":%04X" % port):
                        # En /proc/net/tcp el estado va en fields[3]; los bytes en
                        # cola (tx_queue:rx_queue) estan en fields[4] y fields[5].
                        queues = fields[4].split(":")
                        if len(queues) == 2:
                            total += int(queues[0], 16) + int(queues[1], 16)
        except OSError:
            pass
        return total

    return scan("/proc/net/tcp"), scan("/proc/net/tcp6")


print("clave derivada del md5_crypt de 'astra':", newcamd_password(PASSWORD), len(newcamd_password(PASSWORD)))

before_in, before_out = tcp_counters(PORT)
sock = socket.create_connection((HOST, PORT), timeout=10)
sock.settimeout(6)
print("conectado a", HOST, PORT)

key = b""
while len(key) < 14:
    chunk = sock.recv(14 - len(key))
    if not chunk:
        print("  el servidor cerro antes de enviar la clave (recibidos %d)" % len(key))
        break
    key += chunk
print("1) clave inicial:", key.hex(), f"({len(key)} bytes)")
after_in, after_out = tcp_counters(PORT)
print("   contadores kernel: +%d in / +%d out" % (after_in - before_in, after_out - before_out))

if len(key) != 14:
    sock.close()
    raise SystemExit(1)

des = derive_triple_des_key(DESKEY, key)
print("2) clave DES-EDE2:", des.hex())

payload = USER.encode() + b"\x00" + newcamd_password(PASSWORD).encode()
packet = build_packet(0xE0, payload, des, 0)
print("3) login:", len(packet), "bytes | payload", len(payload), "bytes")
print("   paquete:", packet.hex())

mark_in, mark_out = tcp_counters(PORT)
sock.sendall(packet)
time.sleep(0.1)
print("   contadores: +%d out" % (tcp_counters(PORT)[1] - mark_out))

try:
    data = sock.recv(512)
except socket.timeout:
    data = b""
    print("4) sin respuesta (timeout de 6 s) — OSCam no contesto al login")
if data:
    print("4) respuesta:", len(data), "bytes:", data.hex())
    try:
        tipo, respuesta, cuerpo, msg_id = parse_packet(data, des)
        print("   tipo=0x%02X msg_id=%s payload=%r" % (tipo, msg_id, respuesta[:20]))
    except Exception as exc:  # noqa: BLE001
        print("   no se pudo descifrar la respuesta:", exc)
    print("   (0xE1 = login OK, 0xE2 = login rechazado)")

time.sleep(0.3)
try:
    extra = sock.recv(512)
    if extra:
        print("5) datos extra:", extra.hex())
except (socket.timeout, OSError):
    pass

sock.close()
print("cerrado")
