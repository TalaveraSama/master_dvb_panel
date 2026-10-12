"""Puente a `libtbsnewcamd.so`: la criptografia real del protocolo Newcamd.

OSCam **no** usa DES estandar (aplica `doPC1()` y su propio `EuroDes`), asi que
el login solo funciona si se cifra con este codigo. La biblioteca se compila con
`make -C app/../native` (fuente en `tbs-webui/native/tbs_newcamd.c`).

Uso desde el cliente:

    crypto = load_library()          # None si no esta compilada
    if crypto is None:
        # aviso: sin la biblioteca no se puede autenticar contra OSCam
        ...
    key = crypto.login_key(server_key, deskey, len(deskey))
    packet = crypto.encrypt(message, key)
    tipo, cuerpo = crypto.decrypt(packet, key)
"""

from __future__ import annotations

import ctypes
import logging
import os
from ctypes import POINTER, c_int, c_ubyte
from pathlib import Path

logger = logging.getLogger(__name__)

LIB_NAME = "tbs_newcamd.so"
ENV_VAR = "TBS_NEWCAMD_LIB"

# Rutas donde se busca la biblioteca, en orden
SEARCH_DIRS = (
    Path(__file__).resolve().parents[2] / "native",  # tbs-webui/native
    Path("/usr/local/lib"),
    Path("/usr/lib"),
)

__all__ = ["NewcamdCrypto", "load_library", "library_path", "is_available", "CryptoUnavailable"]


class CryptoUnavailable(RuntimeError):
    """La biblioteca nativa de Newcamd no esta compilada o no se puede cargar."""


class NewcamdCrypto:
    """Envoltorio de la biblioteca C (criptografia Newcamd de OSCam)."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        try:
            self._lib = ctypes.CDLL(self.path)
        except OSError as exc:  # pragma: no cover - depende del sistema
            raise CryptoUnavailable(f"No se pudo cargar {self.path}: {exc}") from exc

        self._lib.nc_login_key_get.restype = c_int
        self._lib.nc_login_key_get.argtypes = [
            POINTER(c_ubyte),
            POINTER(c_ubyte),
            c_int,
            POINTER(c_ubyte),
        ]
        self._lib.nc_encrypt.restype = c_int
        self._lib.nc_encrypt.argtypes = [POINTER(c_ubyte), c_int, POINTER(c_ubyte)]
        self._lib.nc_decrypt.restype = c_int
        self._lib.nc_decrypt.argtypes = [POINTER(c_ubyte), c_int, POINTER(c_ubyte)]

    # -- API ------------------------------------------------------------
    def login_key(self, server_key: bytes, client_key: bytes, length: int | None = None) -> bytes:
        """Clave de sesion de 16 bytes (equivale a `nc_des_login_key_get`)."""
        if len(client_key) != 14:
            raise CryptoUnavailable("La deskey debe tener 14 bytes")
        size = 14 if length is None else int(length)
        salida = (c_ubyte * 16)()
        servidor = (c_ubyte * len(server_key)).from_buffer_copy(server_key)
        cliente = (c_ubyte * 14).from_buffer_copy(client_key)
        resultado = self._lib.nc_login_key_get(servidor, cliente, size, salida)
        if resultado != 0:
            raise CryptoUnavailable("nc_login_key_get devolvio error")
        return bytes(bytearray(salida))

    def encrypt(self, message: bytes, key: bytes) -> bytes:
        """Cifra un mensaje (sin los 2 bytes de longitud). Devuelve el paquete completo."""
        if len(key) != 16:
            raise CryptoUnavailable("La clave de sesion debe tener 16 bytes")
        if len(message) < 3:
            raise CryptoUnavailable("El mensaje debe tener al menos 3 bytes")
        buffer = (c_ubyte * (len(message) + 32))()
        for index, byte in enumerate(message):
            buffer[index] = byte
        clave = (c_ubyte * 16).from_buffer_copy(key)
        tamano = self._lib.nc_encrypt(buffer, len(message), clave)
        if tamano <= 0:
            raise CryptoUnavailable(f"nc_encrypt devolvio {tamano}")
        return bytes(bytearray(buffer)[:tamano])

    def decrypt(self, packet: bytes, key: bytes) -> tuple[int, bytes]:
        """Descifra un paquete completo y devuelve ``(tipo, cuerpo)``.

        El paquete es el que devuelve `encrypt()` (los 2 bytes de longitud que
        van por el cable no se incluyen). ``cuerpo`` es el mensaje completo tal
        como lo montó el emisor: ``[0]`` tipo, ``[1..2]`` longitud del payload,
        ``[3..]`` payload y relleno. El checksum se valida en C.
        """
        if len(key) != 16:
            raise CryptoUnavailable("La clave de sesion debe tener 16 bytes")
        if len(packet) < 18:
            raise CryptoUnavailable("Paquete demasiado corto")
        buffer = (c_ubyte * (len(packet) + 8))()
        for index, byte in enumerate(packet):
            buffer[index] = byte
        clave = (c_ubyte * 16).from_buffer_copy(key)
        tamano = self._lib.nc_decrypt(buffer, len(packet), clave)
        if tamano < 0:
            raise CryptoUnavailable("nc_decrypt: checksum incorrecto o tamano invalido")
        datos = bytes(bytearray(buffer)[:tamano])
        if not datos:
            raise CryptoUnavailable("Respuesta vacia")
        return datos[0], datos

    def self_test(self) -> bool:
        """Comprueba que cifrar y descifrar son inversas."""
        key = self.login_key(bytes(14), bytes.fromhex("0102030405060708091011121314"))
        mensaje = bytes([0xE0, 0x00, 0x00]) + b"astra\x00" + b"$1$abcdefgh$lU.OJ3dCh8uiqm.kfyIdF."
        paquete = self.encrypt(mensaje, key)
        # nc_decrypt espera el paquete tal cual lo devuelve nc_encrypt (los 2
        # bytes de longitud van delante en el cable, no aqui).
        tipo, cuerpo = self.decrypt(paquete, key)
        # El descifrado devuelve el mensaje con su relleno: vale como prefijo.
        return tipo == 0xE0 and cuerpo.startswith(mensaje)


def library_path() -> Path | None:
    """Ruta de la biblioteca compilada, o None si no se encuentra."""
    override = os.environ.get(ENV_VAR)
    if override:
        candidate = Path(override)
        return candidate if candidate.is_file() else None
    for directory in SEARCH_DIRS:
        candidate = directory / LIB_NAME
        if candidate.is_file():
            return candidate
    return None


_cache: NewcamdCrypto | None = None
_cache_error: str | None = None


def load_library(force: bool = False) -> NewcamdCrypto | None:
    """Carga la biblioteca (None si no esta disponible). Silenciosa por diseno."""
    global _cache, _cache_error
    if _cache is not None and not force:
        return _cache
    path = library_path()
    if path is None:
        _cache_error = (
            f"No se encontro {LIB_NAME}. Compilala con: make -C native "
            "(requiere gcc; ver README, seccion Newcamd)."
        )
        logger.debug("Newcamd nativo no disponible: %s", _cache_error)
        return None
    try:
        _cache = NewcamdCrypto(path)
        _cache_error = None
        logger.info("Criptografia Newcamd nativa cargada desde %s", path)
        return _cache
    except CryptoUnavailable as exc:
        _cache_error = str(exc)
        logger.warning("No se pudo cargar la criptografia Newcamd nativa: %s", exc)
        return None


def is_available() -> bool:
    """True si la biblioteca nativa esta compilada y se puede usar."""
    return load_library() is not None


def unavailable_reason() -> str | None:
    """Motivo por el que la biblioteca no esta disponible (None si si lo esta)."""
    load_library()
    return _cache_error
