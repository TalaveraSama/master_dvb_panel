"""md5_crypt (MD5-crypt de FreeBSD) en Python puro.

El protocolo Newcamd no envia la contrasena en claro: envia
``md5_crypt(password, "$1$abcdefgh$")``, es decir ``$1$abcdefgh$`` + 22
caracteres del digest = 34 caracteres (el mismo resultado, byte a byte, que
`crypt(3)` de glibc con el prefijo ``$1$``). Es el mismo algoritmo que la
funcion `crypt(3)` de Linux con el prefijo ``$1$``. El buffer del C es
``char passwd[36]``: 34 caracteres utiles mas el terminador NUL.

Port literal de ``md5_crypt()`` de ``modules/astra/md5.c`` (a su vez port de
FreeBSD), verificado byte a byte contra ``crypt.crypt`` de glibc. Se
implementa en Python puro (solo `hashlib`) para que funcione tambien en
Windows, donde el modulo `crypt` no existe.
"""

from __future__ import annotations

import hashlib

# Alfabeto base64 de crypt(3) (mismo que ./0123456789ABC...xyz)
_ITOA64 = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

_MAGIC = b"$1$"


def _to64(value: int, length: int) -> str:
    """Codifica `length` digitos base64, del bit MENOS significativo al mas."""
    out = []
    for _ in range(length):
        out.append(_ITOA64[value & 0x3F])
        value >>= 6
    return "".join(out)


def _split_salt(salt: str) -> bytes:
    """Extrae el salt real: hasta 8 caracteres tras ``$1$`` (o tras el inicio)."""
    sp = salt.encode("latin-1", "replace")
    if sp.startswith(_MAGIC):
        sp = sp[len(_MAGIC) :]
    return sp.split(b"$", 1)[0][:8]


def md5_crypt(password: str, salt: str = "$1$abcdefgh$") -> str:
    """Devuelve el hash MD5-crypt de `password` (34 caracteres con salt de 8).

    `salt` admite el formato ``$1$<hasta 8 caracteres>$`` o el salt pelado.
    Con un salt que empieza por ``$1$`` el resultado es identico a
    ``crypt.crypt(password, salt)`` de glibc. Con un salt pelado se antepone
    ``$1$`` a la salida, igual que hace ``md5_crypt()`` del C (glibc, en
    cambio, lo interpretaria como DES y no como MD5-crypt).
    """
    raw = password.encode("latin-1", "replace")
    true_salt = _split_salt(salt)
    pw_len = len(raw)
    sl = len(true_salt)

    ctx = hashlib.md5()
    # La contrasena primero, que es lo mas desconocido; luego el magic y el salt.
    ctx.update(raw)
    ctx.update(_MAGIC)
    ctx.update(true_salt)

    # Tantas copias de MD5(pw, salt, pw) como hagan falta para cubrir len(pw).
    inner = hashlib.md5()
    inner.update(raw)
    inner.update(true_salt)
    inner.update(raw)
    digest = inner.digest()
    remaining = pw_len
    while remaining > 0:
        ctx.update(digest[: 16 if remaining > 16 else remaining])
        remaining -= 16

    # "Then something really weird...": en el C, `final` se pone a cero con
    # memset() y luego, por cada bit de len(pw) de menor a mayor:
    #     d = (i & 1) ? final : (uint8_t *)pw;   md5_update(&ctx, d, 1);
    # Es decir, bit a 1 -> un byte 0x00; bit a 0 -> SOLO el primer byte de pw
    # (el tamano pasado es 1, no pw_len). Invertir esta condicion produce un
    # hash que no coincide con glibc.
    index = pw_len
    while index:
        ctx.update(raw[:1] if (index & 1) == 0 else b"\x00")
        index >>= 1

    final = ctx.digest()

    # 1000 iteraciones de reforzamiento.
    for i in range(1000):
        loop = hashlib.md5()
        loop.update(raw if (i & 1) else final)
        if i % 3:
            loop.update(true_salt)
        if i % 7:
            loop.update(raw)
        loop.update(final if (i & 1) else raw)
        final = loop.digest()

    # Empaquetado: el C usa `final[17]` con final[16] = final[5].
    tail = final + bytes([final[5]])
    encoded = []
    for i in range(5):
        value = (tail[i] << 16) | (tail[i + 6] << 8) | tail[i + 12]
        encoded.append(_to64(value, 4))
    encoded.append(_to64(tail[11], 2))
    return "$1$" + true_salt.decode("latin-1") + "$" + "".join(encoded)


def newcamd_password(password: str) -> str:
    """Contrasena tal como la espera el protocolo Newcamd (34 caracteres)."""
    return md5_crypt(password, "$1$abcdefgh$")
