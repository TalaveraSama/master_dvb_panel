import os, socket, struct, sys, time
sys.path.insert(0, os.getcwd())
from app.services.md5crypt import newcamd_password
from app.services.newcamd_crypto import load_library

DESKEY = bytes.fromhex("0102030405060708091011121314")
crypto = load_library()

def intento(etiqueta, relleno_bytes=0, espera=0.0):
    s = socket.create_connection(("127.0.0.1", 5000), timeout=6)
    s.settimeout(4)
    servidor = b""
    while len(servidor) < 14:
        t = s.recv(14 - len(servidor))
        if not t: break
        servidor += t
    if espera: time.sleep(espera)
    try:
        extra = s.recv(64)
        if extra: print(f"  [{etiqueta}] el servidor manda {len(extra)} bytes extra: {extra.hex()}")
    except socket.timeout:
        pass
    clave = crypto.login_key(servidor, DESKEY, 14)
    usuario, passwd = b"astra", newcamd_password("astra").encode()
    cuerpo = usuario + b"\x00" + passwd + b"\x00" * relleno_bytes
    msg = bytearray(15); msg[0] = 0xE0; msg[3] = 0x88; msg[4] = 0x88; msg += cuerpo
    paq = crypto.encrypt(bytes(msg), clave)
    s.sendall(struct.pack(">H", len(paq)) + paq)
    try:
        r = s.recv(256)
    except socket.timeout:
        r = b""
    print(f"  [{etiqueta}] enviado {len(paq)+2} B -> respuesta {len(r)} B {r.hex() if r else '(cerro)'}")
    s.close()
    return r

print("=== variantes de login ===")
intento("base")
time.sleep(0.3)
intento("+1 byte null", relleno_bytes=1)
time.sleep(0.3)
intento("espera 1s tras la clave", espera=1.0)
