"""Lectura de las tablas de sintonización de `dtv-scan-tables`.

En el host real (Ubuntu 22.04 + `dtv-scan-tables`) los ficheros de
``/usr/share/dvb/dvb-s/`` y ``/usr/share/dvb/dvb-t/`` usan el **formato DVBv5**
de bloques ``[CHANNEL]`` con ``CLAVE = VALOR``; en
``/usr/share/dvb/dvb-legacy/`` están las tablas antiguas de líneas planas
(``S2 11362000 H 22000000 2/3 AUTO 8PSK``). Algunas versiones distribuyen
también el formato XML ``<satellite>``/``<transponder>``. Este módulo acepta
los tres, detectándolos automáticamente.

Ejemplo real del host (``/usr/share/dvb/dvb-s/Astra-19.2E``)::

    [CHANNEL]
        DELIVERY_SYSTEM = DVBS2
        FREQUENCY = 11362000
        POLARIZATION = HORIZONTAL
        SYMBOL_RATE = 22000000
        INNER_FEC = 2/3

API que consume `app/services/scan_service.py`::

    satxml.list_tables(dir)        -> [{"name","sat_position","transponders","path"}]
    satxml.parse_tables_file(path) -> list[Transponder]
    satxml.load_bundles(dir)       -> {name: list[Transponder]}
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import Iterable, Iterator

from .types import Transponder, normalize_delivery_system

__all__ = [
    "parse_tables_file",
    "parse_tables_text",
    "count_transponders",
    "load_bundle",
    "load_bundles",
    "list_tables",
    "bundle_names",
    "sat_position_from_name",
    "normalize_delivery_system",
    "TABLE_DIRS",
    "DEFAULT_TABLES_DIR",
]

DEFAULT_TABLES_DIR = Path("/usr/share/dvb")
# Subdirectorios con tablas útiles (dvb-legacy se omite: duplica a dvb-s/dvb-t).
TABLE_DIRS: tuple[str, ...] = ("dvb-s", "dvb-t", "dvb-c", "atsc", "isdb-t")

_POSITION_RE = re.compile(r"(\d{1,3})(?:[.,](\d))?\s*([EWew])")

# --- equivalencias ---------------------------------------------------------
_DELIVERY_SYSTEMS = {
    "DVBS": "DVB-S",
    "DVB-S": "DVB-S",
    "S": "DVB-S",
    "DVBS2": "DVB-S2",
    "DVB-S2": "DVB-S2",
    "S2": "DVB-S2",
    "DVBS2X": "DVB-S2",
    "DVBT": "DVB-T",
    "DVB-T": "DVB-T",
    "T": "DVB-T",
    "DVBT2": "DVB-T2",
    "DVB-T2": "DVB-T2",
    "T2": "DVB-T2",
    "DVBC": "DVB-C",
    "DVB-C": "DVB-C",
    "C": "DVB-C",
    "DVBC2": "DVB-C2",
    "DVB-C2": "DVB-C2",
    "C2": "DVB-C2",
    "ISDBT": "ISDB-T",
    "ISDB-T": "ISDB-T",
    "ATSC": "ATSC",
    "ATSCMH": "ATSC",
}
_POLARIZATIONS = {
    "H": "H",
    "HORIZONTAL": "H",
    "HORIZONTAL_LINEAR": "H",
    "HOR": "H",
    "0": "H",
    "V": "V",
    "VERTICAL": "V",
    "VER": "V",
    "1": "V",
    "L": "L",
    "LEFT": "L",
    "LEFT_CIRCULAR": "L",
    "2": "L",
    "R": "R",
    "RIGHT": "R",
    "RIGHT_CIRCULAR": "R",
    "3": "R",
    "NONE": None,
    "": None,
}
# Índices de `enum fe_code_rate` (los usa el formato XML de dvb-apps).
_FEC_BY_INDEX = (
    "NONE", "1/2", "2/3", "3/4", "4/5", "5/6", "6/7", "7/8", "8/9", "AUTO", "3/5", "9/10", "2/5",
)
# Índices de `enum fe_modulation`.
_MODULATION_BY_INDEX = (
    "QPSK", "QAM_16", "QAM_32", "QAM_64", "QAM_128", "QAM_256", "AUTO",
    "VSB_8", "VSB_16", "8PSK", "16APSK", "32APSK", "DQPSK", "QAM_4_NR",
)
_BANDWIDTH_BY_INDEX = ("8MHz", "7MHz", "6MHz", "AUTO", "5MHz", "10MHz", "1.712MHz")


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def sat_position_from_name(name: str) -> str | None:
    """Posición orbital a partir del nombre del fichero.

    ``"Astra-19.2E" -> "19.2E"``, ``"AMC10_C-135.0W" -> "135.0W"``;
    ``"es-Madrid"`` (terrestre) -> ``None``.
    """
    if not name:
        return None
    match = _POSITION_RE.search(Path(str(name)).name.replace("_", "-"))
    if not match:
        return None
    degrees, decimal, letter = match.groups()
    value = f"{int(degrees)}.{decimal}" if decimal is not None else str(int(degrees))
    return f"{value}{letter.upper()}"


def _to_int(value: object) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(float(text.replace(",", ".")))
    except ValueError:
        return None


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _fec(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    if not text:
        return None
    if text.isdigit():
        index = int(text)
        if 0 <= index < len(_FEC_BY_INDEX):
            return _FEC_BY_INDEX[index]
    return text


def _modulation(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    if not text:
        return None
    if text.isdigit():
        index = int(text)
        if 0 <= index < len(_MODULATION_BY_INDEX):
            return _MODULATION_BY_INDEX[index]
    normalized = text.replace("/", "_").replace(" ", "").replace("-", "_")
    # "256QAM"/"QAM256" -> "QAM_256" (formato de las tablas antiguas de cable)
    match = re.match(r"^(\d+)QAM$", normalized)
    if match:
        return f"QAM_{match.group(1)}"
    match = re.match(r"^QAM(\d+)$", normalized)
    if match:
        return f"QAM_{match.group(1)}"
    # libdvbv5 usa "PSK/8" y "APSK/16" en sus ficheros: se normalizan a la
    # grafía de la API ("8PSK", "16APSK", "32APSK"; ver INTERFACES.md §2).
    if normalized in ("PSK_8", "8PSK"):
        return "8PSK"
    if normalized in ("APSK_16", "16APSK"):
        return "16APSK"
    if normalized in ("APSK_32", "32APSK"):
        return "32APSK"
    return normalized


def _polarization(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper().replace(" ", "_")
    return _POLARIZATIONS.get(text, text if text in ("H", "V", "L", "R") else None)


def _bandwidth_mhz(value: object) -> int | None:
    """BANDWIDTH_HZ (Hz), "8MHz" o "8" (MHz) -> MHz."""
    if value is None:
        return None
    text = str(value).strip().upper()
    if not text:
        return None
    match = re.match(r"^([\d.]+)\s*MHZ$", text)
    if match:
        return max(1, int(round(float(match.group(1)))))
    if text.isdigit():
        number = int(text)
        if number >= 1_000_000:  # venía en Hz
            return max(1, round(number / 1_000_000))
        if number >= 1_000:  # venía en kHz
            return max(1, round(number / 1_000))
        return number or None  # ya en MHz
    return None


def _normalize_system(value: object, default: str = "DVB-S2") -> str:
    if value is None:
        return default
    text = str(value).strip().upper().replace("_", "").replace("-", "")
    return _DELIVERY_SYSTEMS.get(text, normalize_delivery_system(str(value).strip()) or default)


def _symbol_rate_ksps(value: object, system: str) -> int | None:
    """SYMBOL_RATE de las tablas (símbolos/s) -> ksps del dominio.

    Las tablas de `dtv-scan-tables` usan símbolos/s (22000000 = 22000 ksps);
    si el valor es pequeño se asume que ya viene en ksps.
    """
    number = _to_int(value)
    if number is None or number <= 0:
        return None
    if number >= 100_000:  # símbolos/s
        return int(round(number / 1000))
    return number


def _frequency_khz(value: object, system: str) -> int | None:
    """FREQUENCY en kHz (DVB-S) o Hz (DVB-T/C/ATSC/ISDB-T)."""
    number = _to_int(value)
    if number is None or number <= 0:
        return None
    if system in ("DVB-S", "DVB-S2"):
        # En las tablas satelitales ya viene en kHz (11362000 = 11362 MHz).
        return number if number < 100_000_000 else int(round(number / 1000))
    # Terrestre/cable: viene en Hz.
    return int(round(number / 1000))


# ---------------------------------------------------------------------------
# Formato DVBv5 ([CHANNEL] + CLAVE = VALOR)
# ---------------------------------------------------------------------------
def _parse_dvbv5_blocks(text: str, sat_position: str | None) -> list[Transponder]:
    transponders: list[Transponder] = []
    current: dict[str, str] = {}

    def flush() -> None:
        if not current:
            return
        transponder = _transponder_from_dvbv5(current, sat_position)
        if transponder is not None:
            transponders.append(transponder)
        current.clear()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            flush()
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            current[key.strip().upper()] = value.strip()
    flush()
    return transponders


def _transponder_from_dvbv5(values: dict[str, str], sat_position: str | None) -> Transponder | None:
    system = _normalize_system(values.get("DELIVERY_SYSTEM"))
    frequency = _frequency_khz(values.get("FREQUENCY"), system)
    if not frequency:
        return None
    return Transponder(
        delivery_system=system,
        frequency_khz=frequency,
        symbol_rate_ksps=_symbol_rate_ksps(values.get("SYMBOL_RATE"), system),
        polarization=_polarization(values.get("POLARIZATION")),
        fec=_fec(values.get("INNER_FEC")),
        modulation=_modulation(values.get("MODULATION")),
        bandwidth_mhz=_bandwidth_mhz(values.get("BANDWIDTH_HZ")),
        inversion=str(values.get("INVERSION", "AUTO")).strip().upper() or "AUTO",
        rolloff=_rolloff(values.get("ROLLOFF")),
        pilot=_pilot(values.get("PILOT")),
        stream_id=_to_int(values.get("STREAM_ID")),
        plp_id=_to_int(values.get("PLP_ID") or values.get("DVBT2_PLP_ID")),
        sat_position=sat_position,
        code_rate_hp=_fec(values.get("CODE_RATE_HP")),
        code_rate_lp=_fec(values.get("CODE_RATE_LP")),
        guard_interval=_guard(values.get("GUARD_INTERVAL")),
        transmission_mode=_transmission_mode(values.get("TRANSMISSION_MODE")),
        hierarchy=_hierarchy(values.get("HIERARCHY")),
    )


def _rolloff(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    if not text:
        return None
    return {"0.35": "0.35", "35": "0.35", "ROLLOFF_35": "0.35",
            "0.20": "0.20", "20": "0.20", "ROLLOFF_20": "0.20",
            "0.25": "0.25", "25": "0.25", "ROLLOFF_25": "0.25",
            "AUTO": "AUTO"}.get(text, text)


def _pilot(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    if not text:
        return None
    return {"ON": "ON", "1": "ON", "OFF": "OFF", "0": "OFF", "AUTO": "AUTO"}.get(text, text)


def _guard(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper().replace("GUARD_INTERVAL_", "")
    return text or None


def _transmission_mode(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper().replace("TRANSMISSION_MODE_", "")
    return text or None


def _hierarchy(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper().replace("HIERARCHY_", "")
    return text or None


# ---------------------------------------------------------------------------
# Formato antiguo de líneas planas (dvb-legacy)
# ---------------------------------------------------------------------------
def _parse_legacy_lines(text: str, sat_position: str | None) -> list[Transponder]:
    transponders: list[Transponder] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        kind = parts[0].upper()
        if kind not in ("S", "S2", "T", "T2", "C", "C2", "A"):
            continue
        transponder = _transponder_from_legacy(kind, parts[1:], sat_position)
        if transponder is not None:
            transponders.append(transponder)
    return transponders


def _transponder_from_legacy(kind: str, fields: list[str], sat_position: str | None) -> Transponder | None:
    system = {
        "S": "DVB-S", "S2": "DVB-S2", "T": "DVB-T", "T2": "DVB-T2",
        "C": "DVB-C", "C2": "DVB-C2", "A": "ATSC",
    }[kind]
    frequency = _frequency_khz(fields[0], system)
    if not frequency:
        return None
    if system in ("DVB-S", "DVB-S2"):
        # S  <freq_kHz> <pol> <symbolrate_sps> <fec> [inversion] [modulation]
        polarization = _polarization(fields[1]) if len(fields) > 1 else None
        symbol_rate = _symbol_rate_ksps(fields[2], system) if len(fields) > 2 else None
        fec = _fec(fields[3]) if len(fields) > 3 else None
        inversion = str(fields[4]).strip().upper() if len(fields) > 4 else "AUTO"
        modulation = _modulation(fields[5]) if len(fields) > 5 else None
        if modulation is None and system == "DVB-S2":
            modulation = "8PSK"
        return Transponder(
            delivery_system=system,
            frequency_khz=frequency,
            symbol_rate_ksps=symbol_rate,
            polarization=polarization,
            fec=fec,
            modulation=modulation,
            inversion=inversion if inversion in ("ON", "OFF", "AUTO") else "AUTO",
            sat_position=sat_position,
        )
    if system in ("DVB-T", "DVB-T2"):
        # T <freq_Hz> <bandwidth> <fec_hp> <fec_lp> <modulation> <mode> <guard> <hierarchy>
        return Transponder(
            delivery_system=system,
            frequency_khz=frequency,
            bandwidth_mhz=_bandwidth_mhz(fields[1]) if len(fields) > 1 else None,
            code_rate_hp=_fec(fields[2]) if len(fields) > 2 else None,
            code_rate_lp=_fec(fields[3]) if len(fields) > 3 else None,
            modulation=_modulation(fields[4]) if len(fields) > 4 else None,
            transmission_mode=_transmission_mode(fields[5]) if len(fields) > 5 else None,
            guard_interval=_guard(fields[6]) if len(fields) > 6 else None,
            hierarchy=_hierarchy(fields[7]) if len(fields) > 7 else None,
            sat_position=sat_position,
        )
    # C <freq_Hz> <symbolrate_sps> <modulation> <fec>
    return Transponder(
        delivery_system=system,
        frequency_khz=frequency,
        symbol_rate_ksps=_symbol_rate_ksps(fields[1], system) if len(fields) > 1 else None,
        modulation=_modulation(fields[2]) if len(fields) > 2 else None,
        fec=_fec(fields[3]) if len(fields) > 3 else None,
        sat_position=sat_position,
    )


# ---------------------------------------------------------------------------
# Formato XML (dvb-apps: <satellite>/<transponder>)
# ---------------------------------------------------------------------------
def _parse_xml(text: str, default_position: str | None) -> list[Transponder]:
    transponders: list[Transponder] = []
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return transponders

    satellite_position = root.get("name") or default_position
    position = sat_position_from_name(satellite_position) if satellite_position else default_position

    for element in root.iter():
        if element.tag.lower() not in ("transponder", "channel"):
            continue
        values = dict(element.attrib)
        # También se aceptan hijos <fec_inner>2</fec_inner>.
        for child in element:
            if child.text and child.text.strip():
                values.setdefault(child.tag.lower(), child.text.strip())
        system = _normalize_system(values.get("delivery_system") or values.get("type"),
                                   default="DVB-S2" if element.tag.lower() == "transponder" else "DVB-T")
        frequency = _frequency_khz(values.get("frequency"), system)
        if not frequency:
            continue
        bandwidth = values.get("bandwidth")
        if bandwidth and str(bandwidth).isdigit() and int(bandwidth) < len(_BANDWIDTH_BY_INDEX):
            bandwidth_mhz = _bandwidth_mhz(_BANDWIDTH_BY_INDEX[int(bandwidth)])
        else:
            bandwidth_mhz = _bandwidth_mhz(bandwidth)
        transponders.append(
            Transponder(
                delivery_system=system,
                frequency_khz=frequency,
                symbol_rate_ksps=_symbol_rate_ksps(values.get("symbol_rate"), system),
                polarization=_polarization(values.get("polarization")),
                fec=_fec(values.get("fec_inner") or values.get("fec")),
                modulation=_modulation(values.get("modulation")),
                bandwidth_mhz=bandwidth_mhz,
                inversion=str(values.get("inversion", "AUTO")).strip().upper() or "AUTO",
                rolloff=_rolloff(values.get("rolloff")),
                pilot=_pilot(values.get("pilot")),
                stream_id=_to_int(values.get("stream_id")),
                plp_id=_to_int(values.get("plp_id")),
                sat_position=position,
                code_rate_hp=_fec(values.get("code_rate_HP") or values.get("code_rate_hp")),
                code_rate_lp=_fec(values.get("code_rate_LP") or values.get("code_rate_lp")),
                guard_interval=_guard(values.get("guard_interval")),
                transmission_mode=_transmission_mode(values.get("transmission_mode")),
                hierarchy=_hierarchy(values.get("hierarchy_information") or values.get("hierarchy")),
            )
        )
    return transponders


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------
def parse_tables_text(text: str, name: str | None = None) -> list[Transponder]:
    """Parsea el contenido de una tabla (detecta DVBv5, XML o legacy)."""
    if not text or not text.strip():
        return []
    sat_position = sat_position_from_name(name) if name else None

    stripped = text.lstrip("\ufeff \t\r\n")
    if stripped.startswith("<"):
        return _parse_xml(stripped, sat_position)

    has_blocks = "[CHANNEL]" in text or re.search(r"^\s*[A-Z_]+\s*=", text, re.MULTILINE)
    if has_blocks:
        transponders = _parse_dvbv5_blocks(text, sat_position)
        if transponders:
            return transponders
    return _parse_legacy_lines(text, sat_position)


def parse_tables_file(path: str | Path) -> list[Transponder]:
    """Lee y parsea un fichero de tabla de sintonización."""
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8", errors="replace")
    return parse_tables_text(text, name=file_path.name)


def count_transponders(path: str | Path) -> int:
    """Cuenta los transponders reales de un fichero de tablas.

    Se apoya en `parse_tables_file` para que el número que muestra
    `GET /api/scan/tables` coincida **exactamente** con lo que se escanearía.
    Antes se contaban sólo los bloques `[CHANNEL]`, y las tablas que usan
    `[channel 3450]` (o formatos heredados) aparecían con 0 transponders.
    """
    try:
        return len(parse_tables_file(path))
    except OSError:
        return 0
    except Exception:  # pragma: no cover - fichero corrupto: no debe romper el listado
        logger.debug("No se pudo contar transponders de %s", path, exc_info=True)
        return 0


def _iter_table_files(directory: str | Path) -> Iterator[tuple[str, Path]]:
    base = Path(directory)
    for sub in TABLE_DIRS:
        folder = base / sub
        if not folder.is_dir():
            continue
        for file in sorted(folder.iterdir()):
            if not file.is_file() or file.name.startswith("."):
                continue
            yield file.name, file


def bundle_names(directory: str | Path = DEFAULT_TABLES_DIR) -> list[str]:
    """Nombres de tabla disponibles (sin duplicados; prioriza dvb-s)."""
    names: list[str] = []
    for name, _path in _iter_table_files(directory):
        if name not in names:
            names.append(name)
    return sorted(names)


def find_table(name: str, directory: str | Path = DEFAULT_TABLES_DIR) -> Path | None:
    """Localiza una tabla por nombre o ruta (igual que `scan_service._load_table`)."""
    candidate = Path(name)
    if candidate.is_file():
        return candidate
    base = Path(directory)
    for sub in TABLE_DIRS:
        path = base / sub / name
        if path.is_file():
            return path
    for file_name, path in _iter_table_files(directory):
        if file_name == name or Path(file_name).stem == name:
            return path
    return None


def load_bundle(path: str | Path) -> list[Transponder]:
    """Transponders de un fichero de tabla."""
    return parse_tables_file(path)


def load_bundles(directory: str | Path = DEFAULT_TABLES_DIR) -> dict[str, list[Transponder]]:
    """Todas las tablas de un directorio: ``{nombre: [Transponder, ...]}``."""
    bundles: dict[str, list[Transponder]] = {}
    for name, path in _iter_table_files(directory):
        if name in bundles:
            continue
        try:
            bundles[name] = parse_tables_file(path)
        except OSError:
            bundles[name] = []
    return bundles


def list_tables(directory: str | Path = DEFAULT_TABLES_DIR) -> list[dict[str, object]]:
    """Resumen para ``GET /api/scan/tables`` (claves de `ScanTableOut`)."""
    tables: list[dict[str, object]] = []
    seen: set[str] = set()
    for name, path in _iter_table_files(directory):
        if name in seen:
            continue
        seen.add(name)
        tables.append(
            {
                "name": name,
                "sat_position": sat_position_from_name(name),
                "transponders": count_transponders(path),
                "path": str(path),
            }
        )
    return sorted(tables, key=lambda item: str(item["name"]))


def transponders_to_dicts(transponders: Iterable[Transponder]) -> list[dict[str, object]]:
    """Utilidad para tests/serialización rápida."""
    return [
        {
            "delivery_system": normalize_delivery_system(t.delivery_system),
            "frequency_khz": t.frequency_khz,
            "symbol_rate_ksps": t.symbol_rate_ksps,
            "polarization": t.polarization,
            "fec": t.fec,
            "modulation": t.modulation,
            "sat_position": t.sat_position,
        }
        for t in transponders
    ]
