"""Envoltorios de las herramientas DVBv5 del sistema (`dvbv5-scan`, `dvb-fe-tool`).

Se usan como **plan B**: la vía principal es el ioctl directo
(`frontend_ioctl.IoctlFrontend`) y el colector PSI/SI (`si.py`). Ninguna
función deja escapar excepciones no controladas:

* binario ausente -> `UnsupportedError`
* timeout del proceso -> `DvbError`
* salida no parseable -> lista vacía o `SignalStats` con `error`

Todos los subprocesos se lanzan con ``LC_ALL=C`` para que los números usen
punto decimal: el host está en español y `dvb-fe-tool` imprime ``-90,50dBm``
(coma) con el locale por defecto.

API que consume `app/services/scan_service.py`::

    tools.scan_transponder_services(fe_info, transponder, timeout) -> list[ServiceInfo]
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .types import (
    DvbError,
    FrontendInfo,
    LockFlags,
    ServiceInfo,
    SignalStats,
    Transponder,
    UnsupportedError,
    normalize_delivery_system,
    service_type_label,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ChannelsFile",
    "available_tools",
    "run_tool",
    "parse_channels_conf",
    "parse_channels_file",
    "transponder_to_dvbv5",
    "dvbv5_scan_to_channels",
    "scan_transponder_services",
    "dvb_fe_tool_stats",
    "dvb_fe_tool_info",
    "list_frontends_tool",
    "tool_available",
]

DEFAULT_SCAN_TIMEOUT = 60.0
DEFAULT_FE_TOOL_TIMEOUT = 15.0
LNBF_DEFAULT = "UNIVERSAL"

_FREQ_UNITS = {"DVBS", "DVBS2"}


# ---------------------------------------------------------------------------
# Ejecución de subprocesos
# ---------------------------------------------------------------------------
def _tool_env() -> dict[str, str]:
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    return env


def tool_available(name: str) -> bool:
    """True si el binario está en el PATH."""
    return shutil.which(name) is not None


def available_tools() -> dict[str, bool]:
    """Presencia de las herramientas DVBv5 en el sistema."""
    return {name: tool_available(name) for name in ("dvbv5-scan", "dvbv5-zap", "dvb-fe-tool")}


def run_tool(
    args: Sequence[str],
    *,
    timeout: float = DEFAULT_SCAN_TIMEOUT,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Ejecuta una herramienta del sistema capturando su salida.

    :raises UnsupportedError: si el binario no existe.
    :raises DvbError: si supera el timeout o el proceso no se puede lanzar.
    """
    if not args:
        raise DvbError("run_tool() sin argumentos")
    binary = str(args[0])
    if not tool_available(binary):
        raise UnsupportedError(
            f"La herramienta '{binary}' no está instalada en el sistema "
            "(paquete dvb-tools/dvbv5-tools)"
        )
    try:
        result = subprocess.run(
            [str(arg) for arg in args],
            capture_output=True,
            text=True,
            timeout=max(1.0, float(timeout)),
            env=_tool_env(),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DvbError(f"'{binary}' excedió el timeout de {timeout} s") from exc
    except OSError as exc:
        raise DvbError(f"No se pudo ejecutar '{binary}': {exc}") from exc
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        message = detail[-1] if detail else f"código {result.returncode}"
        raise DvbError(f"'{binary}' terminó con error: {message}")
    return result


def _combined_output(result: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(part for part in (result.stdout, result.stderr) if part)


# ---------------------------------------------------------------------------
# Formato DVBv5 de las tablas / channels.conf
# ---------------------------------------------------------------------------
_POLARIZATION_TEXT = {"H": "HORIZONTAL", "V": "VERTICAL", "L": "LEFT", "R": "RIGHT"}
# Nombres que acepta libdvbv5 al leer/escribir ficheros DVBv5 (dvb-v5-std.c).
# OJO: 8PSK se llama "PSK/8" y 16APSK "APSK/16"; "8PSK" da
# "ERROR value 8PSK is invalid for MODULATION" (medido en el host).
_MODULATION_TEXT = {
    "QPSK": "QPSK", "PSK_8": "PSK/8", "8PSK": "PSK/8",
    "APSK_16": "APSK/16", "16APSK": "APSK/16",
    "APSK_32": "APSK/32", "32APSK": "APSK/32",
    "QAM_16": "QAM/16", "QAM_32": "QAM/32", "QAM_64": "QAM/64",
    "QAM_128": "QAM/128", "QAM_256": "QAM/256",
    "QAM_AUTO": "QAM/AUTO", "AUTO": "QAM/AUTO",
    "8VSB": "8VSB", "VSB_8": "8VSB", "16VSB": "16VSB", "VSB_16": "16VSB",
}


def _dvbv5_frequency(transponder: Transponder, system: str) -> int:
    """FREQUENCY como lo espera dvbv5-scan: kHz en satélite, Hz en el resto."""
    if system in ("DVB-S", "DVB-S2"):
        return int(transponder.frequency_khz)
    return int(transponder.frequency_khz) * 1000


def transponder_to_dvbv5(transponder: Transponder, name: str = "TUNE") -> str:
    """Serializa un `Transponder` al formato DVBv5 de las tablas de escaneo."""
    system = normalize_delivery_system(transponder.delivery_system)
    key = system.replace("-", "").upper()
    lines = [f"[{name}]", f"\tDELIVERY_SYSTEM = {key}"]
    lines.append(f"\tFREQUENCY = {_dvbv5_frequency(transponder, system)}")
    if transponder.polarization:
        lines.append(
            f"\tPOLARIZATION = {_POLARIZATION_TEXT.get(transponder.polarization.upper(), transponder.polarization)}"
        )
    if transponder.symbol_rate_ksps:
        lines.append(f"\tSYMBOL_RATE = {int(transponder.symbol_rate_ksps) * 1000}")
    if transponder.fec:
        lines.append(f"\tINNER_FEC = {transponder.fec}")
    if transponder.modulation:
        lines.append(
            f"\tMODULATION = {_MODULATION_TEXT.get(transponder.modulation.upper(), transponder.modulation)}"
        )
    if transponder.bandwidth_mhz:
        lines.append(f"\tBANDWIDTH_HZ = {int(transponder.bandwidth_mhz) * 1_000_000}")
    if transponder.code_rate_hp:
        lines.append(f"\tCODE_RATE_HP = {transponder.code_rate_hp}")
    if transponder.code_rate_lp:
        lines.append(f"\tCODE_RATE_LP = {transponder.code_rate_lp}")
    if transponder.transmission_mode:
        lines.append(f"\tTRANSMISSION_MODE = {transponder.transmission_mode}")
    if transponder.guard_interval:
        lines.append(f"\tGUARD_INTERVAL = {transponder.guard_interval}")
    if transponder.hierarchy:
        lines.append(f"\tHIERARCHY = {transponder.hierarchy}")
    if transponder.rolloff:
        lines.append(f"\tROLLOFF = {transponder.rolloff}")
    if transponder.pilot:
        lines.append(f"\tPILOT = {transponder.pilot}")
    if transponder.stream_id is not None:
        lines.append(f"\tSTREAM_ID = {int(transponder.stream_id)}")
    if transponder.plp_id is not None:
        lines.append(f"\tPLP_ID = {int(transponder.plp_id)}")
    if transponder.inversion:
        lines.append(f"\tINVERSION = {transponder.inversion}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# channels.conf (VDR clásico y DVBv5)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ChannelsFile:
    """Contenido parseado de un fichero de canales/transponders."""

    path: str
    raw: str
    transponders: tuple[Transponder, ...] = ()
    services: tuple[ServiceInfo, ...] = ()
    command: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.transponders and not self.services

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "transponders": len(self.transponders),
            "services": len(self.services),
            "command": list(self.command),
        }


def _vdr_line_to_transponder(line: str, sat_position: str | None = None) -> Transponder | None:
    """Convierte una línea `channels.conf` clásica en `Transponder`.

    Formato antiguo: ``Nombre:FREQ:POL:SR:VPID:APID:...`` donde FREQ va en kHz
    (satélite) y SR en ksps. Formato VDR 2.x: ``Nombre;Proveedor:FREQ:POL:SR:...``.
    """
    fields = line.split(":")
    if len(fields) < 4:
        return None
    name = fields[0].split(";")[0].strip()
    del name  # el nombre del servicio no forma parte del transponder
    frequency = _to_number(fields[1])
    if not frequency:
        return None
    frequency_khz = int(frequency) if frequency >= 100_000 else int(frequency) * 1000
    polarization = None
    for char in fields[2].strip().upper():
        if char in ("H", "V", "L", "R"):
            polarization = char
            break
    symbol_rate = _to_number(fields[3])
    symbol_rate_ksps = None
    if symbol_rate:
        symbol_rate_ksps = int(round(symbol_rate)) if symbol_rate >= 1000 else int(round(symbol_rate * 1000))
    return Transponder(
        delivery_system="DVB-S2",
        frequency_khz=frequency_khz,
        symbol_rate_ksps=symbol_rate_ksps,
        polarization=polarization,
        sat_position=sat_position,
    )


def _to_number(text: str) -> float | None:
    match = re.search(r"-?\d+(?:[.,]\d+)?", str(text))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def parse_channels_conf(text: str) -> list[Transponder]:
    """Transponders de un `channels.conf` (DVBv5 o VDR clásico)."""
    if not text or not text.strip():
        return []
    from . import satxml

    transponders = list(satxml.parse_tables_text(text))
    if transponders:
        return transponders
    result: list[Transponder] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        if "=" in line and ":" not in line.split("=")[0]:
            continue
        transponder = _vdr_line_to_transponder(line)
        if transponder is not None:
            result.append(transponder)
    return result


def _service_from_dvbv5_block(name: str, values: dict[str, str]) -> ServiceInfo:
    service_id = int(_to_number(values.get("SERVICE_ID", "0")) or 0)
    video_pid = values.get("VIDEO_PID") or values.get("VPID")
    audio_pid = values.get("AUDIO_PID") or values.get("APID")
    pmt_pid = values.get("PMT_PID")
    ca_ids = tuple(
        int(part, 16) if part.lower().startswith("0x") else int(_to_number(part) or 0)
        for part in re.findall(r"[0-9A-Fa-fx]+", values.get("CA_SYSTEM_ID", ""))
    )
    scrambled = bool(ca_ids) or str(values.get("SCRAMBLED", "")).strip().upper() in ("1", "TRUE", "YES")
    service_type = int(_to_number(values.get("SERVICE_TYPE", "1")) or 1)

    def _pid(value: str | None) -> int | None:
        number = _to_number(value) if value else None
        return int(number) if number is not None else None

    return ServiceInfo(
        service_id=service_id,
        name=name,
        provider=values.get("PROVIDER", "") or "",
        service_type=service_type,
        service_type_label=service_type_label(service_type),
        is_fta=not scrambled,
        scrambled=scrambled,
        video_pid=_pid(video_pid),
        audio_pid=_pid(audio_pid),
        pcr_pid=_pid(values.get("PCR_PID")) or _pid(video_pid),
        video_codec=values.get("VIDEO_CODEC") or None,
        audio_codec=values.get("AUDIO_CODEC") or None,
        pmt_pid=_pid(pmt_pid),
        ca_system_ids=ca_ids,
        audio_pids=tuple(p for p in (_pid(audio_pid),) if p),
        video_pids=tuple(p for p in (_pid(video_pid),) if p),
    )


def parse_channels_file(text: str, path: str = "", command: Sequence[str] = ()) -> ChannelsFile:
    """Parsea un fichero de canales devolviendo transponders y servicios."""
    raw = text or ""
    transponders = tuple(parse_channels_conf(raw))
    services: list[ServiceInfo] = []

    # Servicios del formato DVBv5 ([nombre] + CLAVE = VALOR)
    current: str | None = None
    values: dict[str, str] = {}
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            if current and values.get("FREQUENCY"):
                services.append(_service_from_dvbv5_block(current, values))
            current = line[1:-1].strip()
            values = {}
            continue
        if current is not None and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip().upper()] = value.strip()
    if current and values.get("FREQUENCY"):
        services.append(_service_from_dvbv5_block(current, values))

    # Servicios del formato VDR (`Nombre;Proveedor:freq:pol:sr:vpids...`)
    if not services:
        for raw_line in raw.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith("[") or "=" in line:
                continue
            fields = line.split(":")
            if len(fields) < 4 or ";" not in fields[0]:
                continue
            name, _, provider = fields[0].partition(";")
            pids = _parse_vdr_pids(fields[4:])
            # El flag de acceso condicional es un campo "C" (o "C<hex>").
            scrambled = any(
                re.fullmatch(r"C[0-9A-Fa-f]*", part.strip()) for part in fields[4:] if part.strip()
            )
            services.append(
                ServiceInfo(
                    service_id=pids.get("service_id", 0),
                    name=name.strip(),
                    provider=provider.strip(),
                    service_type=1,
                    service_type_label="TV",
                    is_fta=not scrambled,
                    scrambled=scrambled,
                    video_pid=pids.get("video_pid"),
                    audio_pid=pids.get("audio_pid"),
                    pcr_pid=pids.get("pcr_pid") or pids.get("video_pid"),
                    video_codec=None,
                    audio_codec=None,
                    pmt_pid=pids.get("pmt_pid"),
                    audio_pids=tuple(p for p in (pids.get("audio_pid"),) if p),
                    video_pids=tuple(p for p in (pids.get("video_pid"),) if p),
                )
            )
    return ChannelsFile(
        path=str(path),
        raw=raw,
        transponders=transponders,
        services=tuple(services),
        command=tuple(command),
    )


def _parse_vdr_pids(fields: Sequence[str]) -> dict[str, int]:
    """Extrae PIDs de los campos numéricos de una línea VDR."""
    result: dict[str, int] = {}
    numbers: list[int] = []
    for field in fields:
        match = re.match(r"^(\d+)", str(field))
        if match:
            numbers.append(int(match.group(1)))
        ca = re.match(r"^C(\d+)", str(field))
        if ca:
            result["service_id"] = int(ca.group(1))
    if numbers:
        result.setdefault("video_pid", numbers[0])
    if len(numbers) > 1:
        result.setdefault("audio_pid", numbers[1])
    if len(numbers) > 2:
        result.setdefault("pmt_pid", numbers[2])
    return result


# ---------------------------------------------------------------------------
# dvbv5-scan
# ---------------------------------------------------------------------------
def dvbv5_scan_to_channels(
    adapter: int,
    transponder: Transponder,
    out_path: str | Path,
    timeout: float = DEFAULT_SCAN_TIMEOUT,
    *,
    frontend: int = 0,
    output_format: str = "DVBV5",
    wait_s: float | None = None,
    sat_number: int | None = None,
) -> ChannelsFile:
    """Escanea un transponder con `dvbv5-scan` y parsea el fichero resultante.

    :raises UnsupportedError: si `dvbv5-scan` no está instalado.
    :raises DvbError: si el proceso falla o excede el timeout.
    """
    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tbs-scan-") as tmp:
        initial = Path(tmp) / "initial.conf"
        initial.write_text(transponder_to_dvbv5(transponder), encoding="utf-8")
        args: list[str] = [
            "dvbv5-scan",
            "-a", str(int(adapter)),
            "-f", str(int(frontend)),
            "-I", "DVBV5",
            "-O", str(output_format),
            "-o", str(out_file),
        ]
        if wait_s is not None:
            args += ["-W", str(float(wait_s))]
        if sat_number is not None:
            args += ["-S", str(int(sat_number))]
        args += ["-l", LNBF_DEFAULT]
        args.append(str(initial))

        result = run_tool(args, timeout=timeout)
        combined = _combined_output(result)
        if result.returncode != 0 and "lnbf" in combined.lower():
            # Reintento sin LNBF explícito (versiones antiguas de la herramienta).
            args = [arg for arg in args if arg not in ("-l", LNBF_DEFAULT)]
            result = run_tool(args, timeout=timeout)
            combined = _combined_output(result)
        if result.returncode != 0:
            last = (combined.strip().splitlines() or ["sin salida"])[-1]
            raise DvbError(f"dvbv5-scan falló (código {result.returncode}): {last}")
        text = out_file.read_text(encoding="utf-8", errors="replace") if out_file.is_file() else ""
    return parse_channels_file(text, path=str(out_file), command=tuple(args))


def scan_transponder_services(
    fe_info: FrontendInfo,
    transponder: Transponder,
    timeout: float = 30.0,
) -> list[ServiceInfo]:
    """Servicios de un transponder usando `dvbv5-scan` (plan B del escaneo).

    Devuelve ``[]`` (con aviso en el log) si no se puede leer nada. Sólo lanza
    `UnsupportedError` cuando la herramienta no existe.
    """
    # En modo simulación no se toca el hardware.
    try:
        from . import registry

        if registry.is_mock_mode():
            logger.debug("scan_transponder_services: modo mock, no se lanza dvbv5-scan")
            return []
    except Exception:  # pragma: no cover - defensivo
        pass

    adapter = int(getattr(fe_info, "adapter", 0) or 0)
    frontend = int(getattr(fe_info, "frontend", 0) or 0)
    with tempfile.TemporaryDirectory(prefix="tbs-scan-out-") as tmp:
        out_file = Path(tmp) / "channels.conf"
        channels = dvbv5_scan_to_channels(
            adapter,
            transponder,
            out_file,
            timeout=timeout,
            frontend=frontend,
        )
    if channels.services:
        return list(channels.services)
    logger.info(
        "dvbv5-scan no devolvió servicios para %s (transponders=%d)",
        transponder.label() if hasattr(transponder, "label") else transponder,
        len(channels.transponders),
    )
    return []


# ---------------------------------------------------------------------------
# dvb-fe-tool
# ---------------------------------------------------------------------------
_STATUS_RE = re.compile(r"\(0x([0-9A-Fa-f]{2,8})\)")
_SIGNAL_DBM_RE = re.compile(r"Signal\s*=\s*(-?\d+(?:[.,]\d+)?)\s*dBm", re.IGNORECASE)
_SIGNAL_PERCENT_RE = re.compile(r"Signal\s*=\s*(\d+(?:[.,]\d+)?)\s*%", re.IGNORECASE)
_CNR_RE = re.compile(r"(?:C/N|CNR|SNR)\s*[:=]\s*(\d+(?:[.,]\d+)?)\s*dB", re.IGNORECASE)
_BER_RE = re.compile(r"(?:post-?)?BER\s*[:=]\s*(\d+(?:[.,]\d+)?)", re.IGNORECASE)
_UCB_RE = re.compile(r"(?:UCB|UNC)\s*[:=]\s*(\d+)", re.IGNORECASE)
_ERROR_RE = re.compile(r"^(ERROR|WARNING)\s+(.*)$", re.MULTILINE)


def _to_float(text: str | None) -> float | None:
    if text is None:
        return None
    try:
        return float(str(text).replace(",", "."))
    except ValueError:
        return None


def _decode_status(value: int) -> LockFlags:
    from .frontend_ioctl import decode_status

    return decode_status(value)


def dvb_fe_tool_stats(adapter: int = 0, frontend: int = 0, timeout: float = DEFAULT_FE_TOOL_TIMEOUT) -> SignalStats:
    """Estadísticas leyendo la salida de `dvb-fe-tool -m`.

    `dvb-fe-tool` abre el frontend en sólo lectura (por eso da EPERM al fijar
    voltaje), pero permite leer estado y calidad. Devuelve `SignalStats` con
    `error` relleno si algo falla; nunca lanza por salida no parseable.
    """
    try:
        result = run_tool(
            ["dvb-fe-tool", "-m", "-c", "1", "-a", str(int(adapter)), "-f", str(int(frontend))],
            timeout=timeout,
        )
    except (UnsupportedError, DvbError) as exc:
        return SignalStats.empty(str(exc))

    text = _combined_output(result)
    status_value = 0
    match = _STATUS_RE.search(text)
    if match:
        try:
            status_value = int(match.group(1), 16)
        except ValueError:
            status_value = 0
    flags = _decode_status(status_value)

    signal_dbm = _to_float(m.group(1)) if (m := _SIGNAL_DBM_RE.search(text)) else None
    signal_percent = _to_float(m.group(1)) if (m := _SIGNAL_PERCENT_RE.search(text)) else None
    snr_db = _to_float(m.group(1)) if (m := _CNR_RE.search(text)) else None
    ber = _to_float(m.group(1)) if (m := _BER_RE.search(text)) else None
    ucblocks = int(m.group(1)) if (m := _UCB_RE.search(text)) else None

    errors = [f"{level}: {message.strip()}" for level, message in _ERROR_RE.findall(text)]
    error = "; ".join(errors) if errors else None
    if signal_dbm is None and signal_percent is None and snr_db is None and error is None:
        error = "Salida de dvb-fe-tool no reconocida"

    quality = None
    if snr_db is not None:
        quality = round(max(0.0, min(100.0, snr_db / 15.0 * 100.0)), 1)
    elif signal_percent is not None:
        quality = round(signal_percent, 1)

    return SignalStats(
        timestamp=time.time(),
        lock=flags,
        signal_percent=signal_percent,
        signal_dbm=signal_dbm,
        snr_db=snr_db,
        cnr_raw=snr_db,
        ber=ber,
        ber_pre=None,
        per=None,
        ucblocks=ucblocks,
        quality_percent=quality,
        error=error,
    )


_DEVICE_RE = re.compile(r"^Device\s+(.*?)\s+\((\S+)\)", re.MULTILINE)
_DELSYS_RE = re.compile(r"Supported delivery systems:\s*(.*?)(?:\n\s*\n|\Z)", re.DOTALL)
_FREQ_RANGE_RE = re.compile(
    r"Frequency range.*?From:\s*([\d.,]+)\s*(\w+)\s*To:\s*([\d.,]+)\s*(\w+)", re.DOTALL | re.IGNORECASE
)
_SR_RANGE_RE = re.compile(
    r"Symbol rate ranges.*?From:\s*([\d.,]+)\s*(\S+)\s*To:\s*([\d.,]+)\s*(\S+)", re.DOTALL | re.IGNORECASE
)
_DELSYS_NAMES = {
    "DVBS": "DVB-S",
    "DVBS2": "DVB-S2",
    "DVBT": "DVB-T",
    "DVBT2": "DVB-T2",
    "DVBC": "DVB-C",
    "DVBC2": "DVB-C2",
    "ISDBT": "ISDB-T",
    "ATSC": "ATSC",
}


def _parse_unit_to_khz(value: float, unit: str) -> int:
    unit = unit.strip().lower()
    if unit.startswith("ghz") or unit == "ghz":
        return int(round(value * 1_000_000))
    if unit.startswith("mhz"):
        return int(round(value * 1000))
    if unit.startswith("khz"):
        return int(round(value))
    if unit.startswith("hz"):
        return int(round(value / 1000))
    return int(round(value))


def _parse_unit_to_sps(value: float, unit: str) -> int:
    unit = unit.strip().lower()
    if unit.startswith("mbaud") or unit.startswith("msps") or unit.startswith("mbd"):
        return int(round(value * 1_000_000))
    if unit.startswith("kbaud") or unit.startswith("ksps"):
        return int(round(value * 1000))
    return int(round(value))


def dvb_fe_tool_info(adapter: int = 0, frontend: int = 0, timeout: float = DEFAULT_FE_TOOL_TIMEOUT) -> FrontendInfo | None:
    """Info de un frontend a partir de `dvb-fe-tool -a N` (o ``None`` si falla)."""
    try:
        result = run_tool(
            ["dvb-fe-tool", "-a", str(int(adapter)), "-f", str(int(frontend))],
            timeout=timeout,
        )
    except (UnsupportedError, DvbError):
        return None
    text = _combined_output(result)
    device = _DEVICE_RE.search(text)
    if not device:
        return None
    name, device_path = device.group(1).strip(), device.group(2).strip()
    systems: list[str] = []
    section = _DELSYS_RE.search(text)
    if section:
        for token in re.findall(r"[A-Z0-9]+", section.group(1).upper()):
            mapped = _DELSYS_NAMES.get(token)
            if mapped and mapped not in systems:
                systems.append(mapped)
    min_frequency = max_frequency = 0
    if (freq := _FREQ_RANGE_RE.search(text)):
        min_frequency = _parse_unit_to_khz(_to_float(freq.group(1)) or 0.0, freq.group(2))
        max_frequency = _parse_unit_to_khz(_to_float(freq.group(3)) or 0.0, freq.group(4))
    symbol_rates: tuple[int, ...] = ()
    if (sr := _SR_RANGE_RE.search(text)):
        symbol_rates = (
            _parse_unit_to_sps(_to_float(sr.group(1)) or 0.0, sr.group(2)),
            _parse_unit_to_sps(_to_float(sr.group(3)) or 0.0, sr.group(4)),
        )
    return FrontendInfo(
        adapter=int(adapter),
        frontend=int(frontend),
        name=name,
        device_path=device_path,
        type="FE_QPSK" if any(s.startswith("DVB-S") for s in systems) else "FE_UNKNOWN",
        caps=0,
        delivery_systems=tuple(systems),
        min_frequency=min_frequency,
        max_frequency=max_frequency,
        symbol_rates=symbol_rates,
    )


def list_frontends_tool(max_adapters: int = 8, frontend: int = 0) -> list[FrontendInfo]:
    """Lista frontends usando `dvb-fe-tool` (fallback si no hay ioctl).

    Itera adaptadores 0..N-1 y se detiene tras el primer hueco.
    """
    if not tool_available("dvb-fe-tool"):
        raise UnsupportedError("La herramienta 'dvb-fe-tool' no está instalada")
    infos: list[FrontendInfo] = []
    misses = 0
    for adapter in range(int(max_adapters)):
        info = dvb_fe_tool_info(adapter, frontend)
        if info is None:
            misses += 1
            if misses >= 2:
                break
            continue
        misses = 0
        infos.append(info)
    return infos
