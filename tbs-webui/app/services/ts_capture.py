"""Captura del flujo de transporte (TS) para el analisis SI.

**Por que hace falta este modulo.** En la TBS 6904se con el driver `tbsecp3` el
demux de software **rechaza los filtros** (`DMX_SET_PES_FILTER` y
`DMX_SET_FILTER` devuelven `ENOTTY`) y `/dev/dvb/adapterN/dvr0` no entrega nada
por si solo. Verificado en el host con estos resultados:

    DMX_SET_PES_FILTER  -> ENOTTY (Inappropriate ioctl for device)
    DMX_SET_FILTER      -> ENOTTY
    lectura de dvr0     -> 0 bytes

Sin embargo **`dvbv5-zap` sí captura el TS completo** (`-P -r -o fichero`):
82 MB en 12 s con LOCK, de los que nuestro analizador SI extrae PAT, SDT y todos
los PMT sin problema.

La solucion es, por tanto, capturar con `dvbv5-zap` a un fichero temporal y
analizarlo con `app/dvb/si.py`. `collect_via_capture()` encapsula ese flujo.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from ..dvb.types import ServiceInfo, Transponder
from . import si_collector

logger = logging.getLogger(__name__)

ZAP_BIN = "dvbv5-zap"

# Estados de la senal que dvbv5-zap escribe en stderr
LOCK_MARKERS = ("Lock", "0x1f")


def zap_available() -> bool:
    """True si `dvbv5-zap` esta instalado."""
    return shutil.which(ZAP_BIN) is not None


def signals_file_for(transponder: Transponder) -> str:
    """Contenido del fichero de canales (formato DVBv5) para un transponder.

    Se usan los valores que acepta `dvbv5-zap` y que funcionan en la TBS:
    `DELIVERY_SYSTEM`, `FREQUENCY`, `POLARIZATION`, `SYMBOL_RATE`, `INNER_FEC`
    y `LNB`. La modulacion se deja fuera porque el nombre exacto depende de la
    version (el driver la deduce del sistema de entrega).
    """
    sistema = (transponder.delivery_system or "DVB-S2").upper().replace("-", "").replace("_", "")
    if sistema in ("DVBS", "DVBS2", "DVBS2X"):
        delivery = sistema  # DVBS / DVBS2 / DVBS2X
    else:
        delivery = "DVBS2"

    lineas = [
        "[tbscam]",
        f"\tDELIVERY_SYSTEM = {delivery}",
        f"\tFREQUENCY = {int(transponder.frequency_khz)}",
    ]
    if transponder.polarization:
        polaridad = str(transponder.polarization).upper()[:1]
        lineas.append(f"\tPOLARIZATION = {'VERTICAL' if polaridad == 'V' else 'HORIZONTAL'}")
    if transponder.symbol_rate_ksps:
        # dvbv5-zap espera el symbol rate en Hz
        lineas.append(f"\tSYMBOL_RATE = {int(transponder.symbol_rate_ksps) * 1000}")
    if transponder.fec:
        lineas.append(f"\tINNER_FEC = {transponder.fec}")
    if transponder.stream_id:
        lineas.append(f"\tSTREAM_ID = {int(transponder.stream_id)}")
    if transponder.plp_id:
        lineas.append(f"\tPLP_ID = {int(transponder.plp_id)}")
    lineas.append("\tLNB = UNIVERSAL")
    return "\n".join(lineas) + "\n"


def capture_transport_stream(
    transponder: Transponder,
    adapter: int = 0,
    frontend: int = 0,
    demux: int = 0,
    seconds: float = 8.0,
    max_bytes: int = 96 * 1024 * 1024,
) -> tuple[bytes | None, str]:
    """Captura el TS completo de un transponder con `dvbv5-zap`.

    Devuelve ``(datos, mensaje)``. Importante: **el frontend debe estar libre**
    (ningun proceso con `/dev/dvb/adapterN/frontendM` abierto), porque
    `dvbv5-zap` sintoniza por su cuenta.
    """
    if not zap_available():
        return None, f"{ZAP_BIN} no esta instalado (paquete dvb-tools)"

    timeout = max(3.0, float(seconds))
    with tempfile.TemporaryDirectory(prefix="tbs-ts-") as carpeta:
        config = Path(carpeta) / "channels.conf"
        salida = Path(carpeta) / "dump.ts"
        config.write_text(signals_file_for(transponder), encoding="utf-8")

        comando = [
            ZAP_BIN,
            "-a", str(int(adapter)),
            "-f", str(int(frontend)),
            "-d", str(int(demux)),
            "-c", str(config),
            "-P",              # todos los PIDs
            "-r",              # grabar en dvr
            "-o", str(salida),
            "-t", str(int(round(timeout))),
            "-s",              # silenciar el progreso
            "tbscam",
        ]
        inicio = time.monotonic()
        try:
            resultado = subprocess.run(  # noqa: S603 - argumentos controlados
                comando,
                capture_output=True,
                text=True,
                timeout=timeout + 25,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return None, "dvbv5-zap no termino a tiempo"
        except OSError as exc:
            return None, f"no se pudo ejecutar {ZAP_BIN}: {exc}"

        duracion = time.monotonic() - inicio
        salida_texto = (resultado.stdout or "") + (resultado.stderr or "")
        if not salida.exists():
            detalle = salida_texto.strip().splitlines()
            return None, "dvbv5-zap no genero el fichero: " + (detalle[-1] if detalle else f"codigo {resultado.returncode}")

        tamano = salida.stat().st_size
        if tamano <= 0:
            return None, "dvbv5-zap grabo 0 bytes (sin senal o sin LOCK)"

        if tamano > max_bytes:
            logger.warning("El TS capturado es muy grande (%d bytes); se recorta", tamano)
        datos = salida.read_bytes()[:max_bytes]
        velocidad = tamano / max(duracion, 0.001) / 1e6
        mensaje = f"{tamano / 1e6:.1f} MB en {duracion:.1f} s ({velocidad:.1f} MB/s)"
        logger.info("Captura TS de %s kHz: %s", transponder.frequency_khz, mensaje)
        return datos, mensaje


def collect_via_capture(
    transponder: Transponder,
    adapter: int = 0,
    frontend: int = 0,
    seconds: float = 8.0,
    max_services: int = 400,
) -> tuple[list[ServiceInfo], str]:
    """Captura el TS y extrae los servicios con el analizador SI.

    Devuelve ``(servicios, mensaje)`` para que el escaneo pueda registrar en su
    log que via se ha usado. El fichero temporal se borra siempre.
    """
    datos, mensaje = capture_transport_stream(
        transponder, adapter=adapter, frontend=frontend, seconds=seconds
    )
    if datos is None:
        return [], mensaje

    servicios = si_collector.services_from_transport_stream(
        datos, max_services=max_services
    )
    return servicios, f"{mensaje} · {len(servicios)} servicios"


def dvbv5_scan_available() -> bool:
    """True si `dvbv5-scan` esta instalado (plan de respaldo)."""
    return shutil.which("dvbv5-scan") is not None


def scan_with_dvbv5(
    transponders: list[Transponder], adapter: int = 0, frontend: int = 0, timeout: float = 30.0
) -> list[ServiceInfo]:
    """Respaldo: escanea una lista de transponders con `dvbv5-scan`.

    Requiere el frontend libre. Se usa solo si la captura directa no esta
    disponible.
    """
    if not dvbv5_scan_available() or not transponders:
        return []

    with tempfile.TemporaryDirectory(prefix="tbs-scan-") as carpeta:
        config = Path(carpeta) / "channels.conf"
        config.write_text(
            "\n".join(
                signals_file_for(transponder).replace("[tbscam]", f"[tp{index}]")
                for index, transponder in enumerate(transponders)
            ),
            encoding="utf-8",
        )
        try:
            resultado = subprocess.run(  # noqa: S603 - argumentos controlados
                [
                    "dvbv5-scan",
                    "-a", str(int(adapter)),
                    "-f", str(int(frontend)),
                    "-o", str(config),
                    str(config),
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.debug("dvbv5-scan fallo: %s", exc)
            return []

        if not config.exists():
            return []
        return si_collector.services_from_channels_conf(config.read_text(encoding="utf-8", errors="replace"))
