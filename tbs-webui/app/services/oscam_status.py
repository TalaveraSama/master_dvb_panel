"""Integración con OSCam: estado real de la tarjeta y los readers.

**Por qué no hablamos Newcamd directamente con OSCam** (verificado en el host):

OSCam no usa DES estándar en Newcamd. Su `nc_des_login_key_get()` coge los 14
bytes de la clave del servidor, aplica `des_key_spread()` + **`doPC1()`** (una
permutación propia de la clave) y su `EuroDes()` implementa un DES propio
(`module-newcamd-des.c`), no `DES_ede2_*` de OpenSSL. Por eso un cliente en
Python con `pycryptodome` **no puede** cifrar el login como OSCam espera: el
handshake (14 bytes, XOR, login `0xE0` con `md5_crypt`) es correcto, pero la
clave derivada difiere y OSCam cierra la conexión sin responder.

Lo que SÍ funciona y es lo que usa este módulo: el **webif de OSCam**, que
expone el estado real de la tarjeta en XML sin autenticación en red local.

    GET /oscamapi.html?part=status   -> <oscam><status><client .../></status></oscam>

Con eso el panel muestra: versión de OSCam, si está vivo, los readers (protocolo
PC/SC, estado de tarjeta, CAID) y los clientes conectados.
"""

from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:8888"
STATUS_PATH = "/oscamapi.html?part=status"
TIMEOUT_S = 4.0
CACHE_S = 3.0

# Estados de tarjeta de OSCam -> texto legible
CARD_STATUS_TEXT = {
    "CARDOK": "Tarjeta lista",
    "CARDNEEDINIT": "Inicializando tarjeta",
    "CARDINSERTED": "Tarjeta insertada",
    "CARDNOTFOUND": "Sin tarjeta",
    "CARDERROR": "Error de tarjeta",
    "CARDNONE": "Sin tarjeta",
    "NONE": "Sin lector",
}


@dataclass
class OscamReader:
    """Un reader (lector de tarjeta) de OSCam."""

    name: str
    protocol: str
    status: str
    status_text: str
    caid: str | None = None
    provid: str | None = None
    idle_seconds: int | None = None
    online_seconds: int | None = None
    ok: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "protocol": self.protocol,
            "status": self.status,
            "status_text": self.status_text,
            "caid": self.caid,
            "provid": self.provid,
            "idle_seconds": self.idle_seconds,
            "online_seconds": self.online_seconds,
            "ok": self.ok,
        }


@dataclass
class OscamStatus:
    """Estado completo de OSCam para la interfaz."""

    available: bool = False
    url: str = DEFAULT_URL
    version: str | None = None
    revision: str | None = None
    uptime_s: int | None = None
    message: str = ""
    readers: list[OscamReader] = field(default_factory=list)
    clients: list[dict[str, Any]] = field(default_factory=list)
    card_ready: bool = False
    fetched_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "url": self.url,
            "version": self.version,
            "revision": self.revision,
            "uptime_s": self.uptime_s,
            "message": self.message,
            "readers": [reader.to_dict() for reader in self.readers],
            "clients": self.clients,
            "card_ready": self.card_ready,
            "fetched_at": self.fetched_at,
        }


def base_url(configured: str | None = None) -> str:
    """Normaliza la URL del webif de OSCam."""
    url = (configured or DEFAULT_URL).strip() or DEFAULT_URL
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    return url.rstrip("/")


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def parse_status(xml_text: str, url: str = DEFAULT_URL) -> OscamStatus:
    """Convierte el XML del webif en un `OscamStatus`."""
    status = OscamStatus(available=True, url=url, fetched_at=time.time())
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        status.available = False
        status.message = f"Respuesta de OSCam ilegible: {exc}"
        return status

    status.version = root.get("version")
    status.revision = root.get("revision") or root.get("commit")
    status.uptime_s = _int_or_none(root.get("uptime"))

    for client in root.iter("client"):
        tipo = client.get("type")
        times = client.find("times")
        connection = client.find("connection")
        online = _int_or_none(times.get("online")) if times is not None else None
        idle = _int_or_none(times.get("idle")) if times is not None else None
        estado = (connection.text or "").strip() if connection is not None else ""

        if tipo == "r":
            status.readers.append(
                OscamReader(
                    name=client.get("name") or "reader",
                    protocol=client.get("protocol") or "?",
                    status=estado,
                    status_text=CARD_STATUS_TEXT.get(estado, estado or "Desconocido"),
                    caid=(client.get("caid") or None),
                    provid=(client.get("provid") or None),
                    idle_seconds=idle,
                    online_seconds=online,
                    ok=estado in ("CARDOK", "CARDINSERTED"),
                )
            )
        else:
            status.clients.append(
                {
                    "name": client.get("name"),
                    "type": tipo,
                    "protocol": client.get("protocol"),
                    "au": client.get("au"),
                    "online_seconds": online,
                    "idle_seconds": idle,
                    "status": estado,
                }
            )

    status.card_ready = any(reader.ok for reader in status.readers)
    if status.card_ready:
        status.message = f"OSCam activo · {len(status.readers)} lector(es)"
    elif status.readers:
        status.message = "OSCam activo pero ningún lector con tarjeta lista"
    else:
        status.message = "OSCam activo sin lectores configurados"
    return status


def _fetch(url: str, timeout: float = TIMEOUT_S) -> str:
    import httpx

    response = httpx.get(url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    return response.text


def fetch_status(configured_url: str | None = None, timeout: float = TIMEOUT_S) -> OscamStatus:
    """Lee el estado del webif de OSCam (no lanza: devuelve `available=False`)."""
    url = base_url(configured_url)
    try:
        xml_text = _fetch(url + STATUS_PATH, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - cualquier fallo de red/HTTP
        logger.debug("OSCam no disponible en %s: %s", url, exc)
        return OscamStatus(
            available=False,
            url=url,
            message=f"No se pudo consultar OSCam en {url} ({exc.__class__.__name__}). "
            "Comprueba que OSCam está en marcha y que el webif responde.",
            fetched_at=time.time(),
        )
    return parse_status(xml_text, url)


class OscamStatusCache:
    """Cachea el estado unos segundos para no golpear el webif en cada refresco."""

    def __init__(self, url: str | None = None, ttl: float = CACHE_S) -> None:
        self.url = url
        self.ttl = ttl
        self._status: OscamStatus | None = None
        self._at = 0.0

    def get(self, force: bool = False) -> OscamStatus:
        now = time.time()
        if force or self._status is None or (now - self._at) > self.ttl:
            self._status = fetch_status(self.url)
            self._at = now
        return self._status
