"""Tests de `md5_crypt` (MD5-crypt de FreeBSD) frente a vectores de glibc.

Los hashes esperados son la salida literal de ``crypt.crypt(pw, "$1$abcdefgh$")``
en el host Ubuntu (glibc), copiada tal cual; la implementacion es Python puro
(solo `hashlib`) para que los tests tambien pasen en Windows.
"""

from __future__ import annotations

import re

import pytest

from app.services.md5crypt import md5_crypt, newcamd_password

SALT = "$1$abcdefgh$"

# password -> crypt.crypt(password, "$1$abcdefgh$") de glibc (literal)
GLIBC_VECTORS = [
    ("astra", "$1$abcdefgh$lU.OJ3dCh8uiqm.kfyIdF."),
    ("", "$1$abcdefgh$M55TzYaaccxVGbptZWaxX/"),
    ("a", "$1$abcdefgh$jUYc1Xi7pkozuzWQ0Dft71"),
    ("1234567890", "$1$abcdefgh$7KT./yJ8ONoodnM9VUtMO0"),
    (
        "contrasena-larga-de-prueba-de-mas-de-16-caracteres",
        "$1$abcdefgh$SxKtBG2AUW3mgxPxB4Fgo.",
    ),
    ("12345678", "$1$abcdefgh$poMCFpiVRevlCQ8FbmxhU1"),
    ("Passw0rd!#", "$1$abcdefgh$TPKUT8DX0NxfEZvPok5uD0"),
]

# Vectores con otros salts, tambien copiados de glibc.
GLIBC_SALT_VECTORS = [
    ("astra", "$1$a$", "$1$a$DIbaN66BT3iHokzUALEv81"),
    ("astra", "$1$abc$", "$1$abc$riAFWH2Gfs7J3KgJ362XS0"),
    ("astra", "$1$xyz12345$", "$1$xyz12345$xi6txAL7WKlhMZ8UU/Iz1."),
    ("Passw0rd!#", "$1$Zz9$", "$1$Zz9$gntMdXNBei9cl4SgoPCJf."),
]

# $1$ + 8 de salt + $ + 22 del digest = 34 caracteres (el C usa passwd[36],
# 34 utiles + NUL).
EXPECTED_LEN = len(SALT) + 22

MCRYPT_RE = re.compile(r"^\$1\$(?P<salt>[^$]{1,8})\$(?P<digest>[./0-9A-Za-z]{22})$")


@pytest.mark.parametrize(("password", "expected"), GLIBC_VECTORS)
def test_matches_glibc(password: str, expected: str) -> None:
    assert md5_crypt(password, SALT) == expected


@pytest.mark.parametrize(("password", "salt", "expected"), GLIBC_SALT_VECTORS)
def test_matches_glibc_other_salts(password: str, salt: str, expected: str) -> None:
    assert md5_crypt(password, salt) == expected


@pytest.mark.parametrize(("password", "expected"), GLIBC_VECTORS)
def test_newcamd_password_equals_default_salt(password: str, expected: str) -> None:
    assert newcamd_password(password) == expected == md5_crypt(password)


@pytest.mark.parametrize(("password", "expected"), GLIBC_VECTORS)
def test_length_and_format(password: str, expected: str) -> None:
    value = md5_crypt(password, SALT)
    assert len(value) == EXPECTED_LEN == 34
    match = MCRYPT_RE.match(value)
    assert match is not None, value
    assert match.group("salt") == "abcdefgh"
    assert value.startswith("$1$abcdefgh$")
    assert len(match.group("digest")) == 22


def test_default_salt_value() -> None:
    # La API por defecto es la que usa Newcamd.
    assert md5_crypt("astra") == md5_crypt("astra", "$1$abcdefgh$")


def test_salt_is_truncated_to_eight_chars() -> None:
    assert md5_crypt("astra", "$1$abcdefghij$") == md5_crypt("astra", "$1$abcdefgh$")
    assert md5_crypt("astra", "$1$abcdefghij$") == "$1$abcdefgh$lU.OJ3dCh8uiqm.kfyIdF."


def test_salt_without_magic_prefix() -> None:
    # El C acepta el salt pelado y antepone "$1$" a la salida.
    assert md5_crypt("astra", "abcdefgh") == "$1$abcdefgh$lU.OJ3dCh8uiqm.kfyIdF."


def test_empty_password_is_valid() -> None:
    value = md5_crypt("")
    assert value == "$1$abcdefgh$M55TzYaaccxVGbptZWaxX/"
    assert value != md5_crypt("a")


def test_known_buggy_variant_is_not_returned() -> None:
    # Regresion: el bucle "weird" (i & 1) estaba invertido y daba este hash.
    assert md5_crypt("astra") != "$1$abcdefgh$3CfHMTx/SJjEiFOQksqMJ1"


def test_long_password_covers_multiple_digest_blocks() -> None:
    # len(pw) > 16 y > 32: ejercita el bucle de copias de MD5(pw,salt,pw).
    assert (
        md5_crypt("contrasena-larga-de-prueba-de-mas-de-16-caracteres")
        == "$1$abcdefgh$SxKtBG2AUW3mgxPxB4Fgo."
    )
    assert md5_crypt("x" * 40) != md5_crypt("x" * 39)


def test_uses_hashlib_not_crypt() -> None:
    """Debe funcionar en Windows: el modulo no importa `crypt`."""
    import app.services.md5crypt as module

    source = open(module.__file__, "r", encoding="utf-8").read()
    assert "import crypt" not in source
    assert "hashlib" in source
