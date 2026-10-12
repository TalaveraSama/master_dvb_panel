#!/usr/bin/env python3
"""Verificación de la capa DVB contra la tarjeta real TBS 6904se.

Uso (en el host Ubuntu con la tarjeta):

    /home/ruben/.local/bin/python3.11 tbs-webui/tools/dvb-hwcheck.py
    ... --adapter 0 --seconds 5 --table /usr/share/dvb/dvb-s/Astra-19.2E
    ... --frequency 11362000 --symbol-rate 22000 --polarization H --fec 2/3
    ... --scan        # además, plan B con dvbv5-scan (tarda más)

Qué hace:

1. Fuerza hardware real (TBS_MOCK=0) y lista los frontends de /dev/dvb.
2. Muestra las estadísticas CRUDAS (escala de DTV_STAT_SIGNAL_STRENGTH/CNR)
   para responder si el driver entrega 0..65535 o -dBm.
3. Toma el lock global (`registry.DeviceLock`), sintoniza un transponder real
   en DVB-S2 y muestrea 5 s: flags de lock, señal %, CNR, BER.
4. Si hay LOCK, lee el TS por el demux (PSI: PAT/SDT/PMT) y lista los servicios.
5. Cierra el frontend y libera el lock (nunca deja el dispositivo abierto).

Códigos de salida: 0 = LOCK, 2 = sin LOCK, 3 = tarjeta ocupada, 4 = error.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
import time

DEFAULT_TABLE = "/usr/share/dvb/dvb-s/Astra-19.2E"
# 11362 MHz H 22000 2/3 DVB-S2 (transponder de Astra 19.2E de la tabla del host)
DEFAULT_FREQUENCY = 11_362_000
DEFAULT_SYMBOL_RATE = 22_000


def _find_repo_root() -> pathlib.Path:
    """Localiza el directorio que contiene `app/dvb/__init__.py`."""
    start = pathlib.Path(__file__).resolve().parent
    for base in [start, *start.parents]:
        for candidate in (base / "tbs-webui" / "app", base / "app"):
            if (candidate / "dvb" / "__init__.py").is_file():
                return candidate.parent
    raise SystemExit("No se encontró el paquete app/ (¿está el repo sincronizado?)")


REPO_ROOT = _find_repo_root()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.dvb import diseqc, registry, satxml, si  # noqa: E402
from app.dvb import types as t  # noqa: E402
from app.dvb.frontend_ioctl import (  # noqa: E402
    DTV_STAT_CNR,
    DTV_STAT_ERROR_BLOCK_COUNT,
    DTV_STAT_POST_ERROR_BIT_COUNT,
    DTV_STAT_POST_TOTAL_BIT_COUNT,
    DTV_STAT_PRE_ERROR_BIT_COUNT,
    DTV_STAT_PRE_TOTAL_BIT_COUNT,
    DTV_STAT_SIGNAL_STRENGTH,
    DTV_STAT_TOTAL_BLOCK_COUNT,
    IoctlFrontend,
    SCALE_NAMES,
    read_frontend_info,
)
from app.dvb.types import FrontendError  # noqa: E402

STAT_NAMES = {
    DTV_STAT_SIGNAL_STRENGTH: "SIGNAL_STRENGTH",
    DTV_STAT_CNR: "CNR",
    DTV_STAT_PRE_ERROR_BIT_COUNT: "PRE_ERROR_BIT_COUNT",
    DTV_STAT_PRE_TOTAL_BIT_COUNT: "PRE_TOTAL_BIT_COUNT",
    DTV_STAT_POST_ERROR_BIT_COUNT: "POST_ERROR_BIT_COUNT",
    DTV_STAT_POST_TOTAL_BIT_COUNT: "POST_TOTAL_BIT_COUNT",
    DTV_STAT_ERROR_BLOCK_COUNT: "ERROR_BLOCK_COUNT",
    DTV_STAT_TOTAL_BLOCK_COUNT: "TOTAL_BLOCK_COUNT",
}

# Nombres de los comandos DTV (para el diagnóstico paso a paso)
DTV_NAMES = {
    0: "DTV_UNDEFINED", 1: "DTV_TUNE", 2: "DTV_CLEAR", 3: "DTV_FREQUENCY",
    4: "DTV_MODULATION", 5: "DTV_BANDWIDTH_HZ", 6: "DTV_INVERSION",
    7: "DTV_DISEQC_MASTER", 8: "DTV_SYMBOL_RATE", 9: "DTV_INNER_FEC",
    10: "DTV_VOLTAGE", 11: "DTV_TONE", 12: "DTV_PILOT", 13: "DTV_ROLLOFF",
    17: "DTV_DELIVERY_SYSTEM", 35: "DTV_API_VERSION", 36: "DTV_CODE_RATE_HP",
    37: "DTV_CODE_RATE_LP", 38: "DTV_GUARD_INTERVAL", 39: "DTV_TRANSMISSION_MODE",
    40: "DTV_HIERARCHY", 42: "DTV_STREAM_ID", 43: "DTV_DVBT2_PLP_ID_LEGACY",
    44: "DTV_ENUM_DELSYS",
}

CAP_NAMES = (
    ("FE_CAN_FEC_1_2", 0x2),
    ("FE_CAN_FEC_2_3", 0x4),
    ("FE_CAN_FEC_3_4", 0x8),
    ("FE_CAN_FEC_5_6", 0x20),
    ("FE_CAN_FEC_7_8", 0x80),
    ("FE_CAN_FEC_AUTO", 0x200),
    ("FE_CAN_QPSK", 0x400),
    ("FE_CAN_QAM_AUTO", 0x10000),
    ("FE_CAN_TRANSMISSION_MODE_AUTO", 0x20000),
    ("FE_CAN_8VSB", 0x200000),
    ("FE_CAN_MULTISTREAM", 0x4000000),
    ("FE_CAN_2G_MODULATION", 0x10000000),
    ("FE_CAN_RECOVER", 0x40000000),
)


def _caps_text(caps: int) -> str:
    names = [name for name, bit in CAP_NAMES if caps & bit]
    return f"0x{caps:08x} ({', '.join(names) if names else 'sin flags conocidos'})"


def _fmt(value: object, unit: str = "") -> str:
    if value is None:
        return "n/d"
    if isinstance(value, float):
        return f"{value:.2f}{unit}"
    return f"{value}{unit}"


def print_frontends(infos: list[t.FrontendInfo]) -> None:
    print("=" * 78)
    print(f"FRONTENDS DETECTADOS ({len(infos)})")
    print("=" * 78)
    for info in infos:
        print(f"\n[adapter{info.adapter}/frontend{info.frontend}] {info.device_path}")
        print(f"  nombre          : {info.name!r}")
        print(f"  tipo            : {info.type}")
        print(f"  caps            : {_caps_text(info.caps)}")
        print(f"  delivery systems: {', '.join(info.delivery_systems) or '(no declarados)'}")
        print(f"  frecuencia      : {info.min_frequency} .. {info.max_frequency} kHz")
        print(
            "  symbol rate     : "
            + (f"{info.symbol_rates[0]} .. {info.symbol_rates[1]} baudios" if info.symbol_rates else "n/d")
        )


def print_raw_scales(info: t.FrontendInfo, frontend: IoctlFrontend) -> dict[int, object]:
    """Imprime la escala cruda de las estadísticas (0..65535 vs dBm)."""
    print("\n" + "-" * 78)
    print(f"ESTADÍSTICAS CRUDAS (DTV_STAT_*) de adapter{info.adapter}/frontend{info.frontend}")
    print("-" * 78)
    try:
        raw = frontend.read_raw_stats()
    except FrontendError as exc:
        print(f"  FE_GET_PROPERTY falló: {exc}")
        return {}
    for cmd, entries in raw.items():
        name = STAT_NAMES.get(cmd, str(cmd))
        if not entries:
            print(f"  {name:<22} cmd={cmd:<3} NO DISPONIBLE")
            continue
        for index, (scale, unsigned, signed) in enumerate(entries):
            print(
                f"  {name:<22} cmd={cmd:<3} [{index}] escala={scale} "
                f"[{SCALE_NAMES.get(scale, '?')}] uvalue={unsigned} svalue={signed}"
            )
    for cmd, etiqueta in ((DTV_STAT_SIGNAL_STRENGTH, "SIGNAL_STRENGTH"), (DTV_STAT_CNR, "CNR")):
        for index, (scale, unsigned, signed) in enumerate(raw.get(cmd) or []):
            if scale == 1:
                unidad = "dBm" if cmd == DTV_STAT_SIGNAL_STRENGTH else "dB"
                print(f"  -> {etiqueta}[{index}] en DECIBELIOS: {signed / 1000.0:.2f} {unidad}")
            elif scale == 2:
                print(f"  -> {etiqueta}[{index}] RELATIVA: {unsigned} / 65535 = {100.0 * unsigned / 65535.0:.2f} %")
    return raw


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--adapter", type=int, default=0)
    parser.add_argument("--frontend", type=int, default=0)
    parser.add_argument("--seconds", type=float, default=5.0, help="tiempo de muestreo tras sintonizar")
    parser.add_argument("--table", default=DEFAULT_TABLE, help="tabla de sintonización a usar")
    parser.add_argument("--frequency", type=int, default=None, help="frecuencia del transponder en kHz")
    parser.add_argument("--symbol-rate", type=int, default=None, help="symbol rate en ksps")
    parser.add_argument("--polarization", default=None, help="H | V | L | R")
    parser.add_argument("--fec", default=None)
    parser.add_argument("--modulation", default=None)
    parser.add_argument("--delivery-system", default="DVB-S2")
    parser.add_argument("--lock-timeout", type=float, default=6.0)
    parser.add_argument("--lock-owner", default="hwcheck")
    parser.add_argument("--dwell", type=float, default=3.0, help="segundos de lectura del TS por el demux")
    parser.add_argument("--scan", action="store_true", help="probar además dvbv5-scan (plan B)")
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="enviar las propiedades de sintonización una a una (localiza EINVAL)",
    )
    parser.add_argument("--no-demux", action="store_true", help="no leer el TS")
    return parser.parse_args()


def diagnosticar_propiedades(frontend: IoctlFrontend, params: t.TuneParams) -> None:
    """Envía cada propiedad de sintonización por separado y reporta OK/error.

    El kernel guarda las propiedades DVBv5 en la caché del descriptor, así que
    el efecto acumulado equivale a enviarlas todas en una sola llamada; pero
    permite ver exactamente qué comando devuelve EINVAL.
    """
    from app.dvb.frontend_ioctl import build_tune_property_list

    print("\n" + "-" * 78)
    print("DIAGNÓSTICO: FE_SET_PROPERTY propiedad a propiedad (DTV_*)")
    print("-" * 78)
    for cmd, value in build_tune_property_list(params):
        nombre = DTV_NAMES.get(cmd, f"cmd={cmd}")
        try:
            frontend.set_property(cmd, value)
            print(f"  [OK]   {nombre:<26} = {value}")
        except FrontendError as exc:
            print(f"  [FALLO] {nombre:<26} = {value}  ->  {exc}")
    print("  GET DTV_API_VERSION        =", end=" ")
    try:
        print(f"0x{frontend.api_version():08x}")
    except FrontendError as exc:
        print(f"FALLO ({exc})")
    print("  GET DTV_ENUM_DELSYS        =", end=" ")
    try:
        length, values = frontend.get_property_buffer(44)
        print(f"len={length} values={values}")
    except FrontendError as exc:
        print(f"FALLO ({exc})")


def transponder_desde_tabla(args: argparse.Namespace) -> t.Transponder:
    transponder: t.Transponder | None = None
    tabla = pathlib.Path(args.table)
    if tabla.is_file():
        transponders = satxml.parse_tables_file(tabla)
        if transponders:
            transponder = transponders[0]
            print(f"Transponder de la tabla {tabla}: {transponder.label()} sat={transponder.sat_position}")
    if transponder is None:
        print(f"No se pudo leer la tabla {tabla}; se usan los valores por defecto")
    datos = {
        "delivery_system": args.delivery_system,
        "frequency_khz": args.frequency or (transponder.frequency_khz if transponder else DEFAULT_FREQUENCY),
        "symbol_rate_ksps": args.symbol_rate or (transponder.symbol_rate_ksps if transponder else DEFAULT_SYMBOL_RATE),
        "polarization": args.polarization or (transponder.polarization if transponder else "H"),
        "fec": args.fec or (transponder.fec if transponder else "2/3"),
        "modulation": args.modulation or (transponder.modulation if transponder else "8PSK"),
        "sat_position": transponder.sat_position if transponder else "19.2E",
    }
    return t.Transponder(**datos)


def muestrear(frontend: IoctlFrontend, seconds: float, interval: float = 0.5) -> tuple[bool, t.SignalStats | None]:
    """Muestrea estado y estadísticas durante `seconds`."""
    print("\n" + "-" * 78)
    print(f"MUESTREO ({seconds:.0f} s, cada {interval * 1000:.0f} ms)")
    print("-" * 78)
    print(f"{'t(s)':>5} {'raw':>4} {'S':>1}{'C':>1}{'V':>1}{'Y':>1}{'L':>1}  "
          f"{'señal%':>7} {'dBm':>7} {'CNR dB':>7} {'BER':>9} {'UCB':>4} {'cal%':>6}  error")
    locked = False
    last_stats: t.SignalStats | None = None
    start = time.monotonic()
    while True:
        elapsed = time.monotonic() - start
        try:
            stats = frontend.read_stats()
        except FrontendError as exc:
            print(f"{elapsed:5.1f}  ERROR: {exc}")
            break
        last_stats = stats
        flags = stats.lock
        print(
            f"{elapsed:5.1f} 0x{flags.raw:02x} "
            f"{int(flags.signal)}{int(flags.carrier)}{int(flags.viterbi)}{int(flags.sync)}{int(flags.lock)}  "
            f"{_fmt(stats.signal_percent):>7} {_fmt(stats.signal_dbm):>7} {_fmt(stats.snr_db):>7} "
            f"{_fmt(stats.ber):>9} {_fmt(stats.ucblocks):>4} {_fmt(stats.quality_percent):>6}  "
            f"{stats.error or ''}"
        )
        if flags.is_locked:
            locked = True
        if elapsed >= seconds:
            break
        time.sleep(interval)
    return locked, last_stats


def leer_servicios(frontend: IoctlFrontend, dwell: float) -> list[t.ServiceInfo]:
    """Lee el TS por el demux y devuelve los servicios PSI/SI encontrados."""
    print("\n" + "-" * 78)
    print(f"LECTURA DEL TS POR EL DEMUX ({dwell:.0f} s) — PAT/SDT/PMT")
    print("-" * 78)
    try:
        frontend.set_pid_list([si.PAT_PID, si.SDT_PID, si.NIT_PID])
    except FrontendError as exc:
        print(f"  No se pudo configurar el demux: {exc}")
        return []
    buffer = bytearray()
    start = time.monotonic()
    pids_vistos: set[int] = set()
    while time.monotonic() - start < dwell:
        chunk = frontend.read_ts(timeout=0.2, max_bytes=188 * 256)
        if chunk:
            buffer.extend(chunk)
            for packet in si.iter_ts_packets(chunk):
                pids_vistos.add(packet.pid)
    print(f"  {len(buffer)} bytes de TS leídos, PIDs vistos: {sorted(pids_vistos)[:24]}")
    if not buffer:
        print("  (sin datos: el demux no entregó paquetes)")
        return []
    services = si.parse_services_from_ts(bytes(buffer))
    print(f"  Servicios detectados: {len(services)}")
    for service in services[:30]:
        print(
            f"    - id={service.service_id:<6} {service.name!r:<34} prov={service.provider!r:<14} "
            f"{service.service_type_label:<5} FTA={service.is_fta!s:<5} "
            f"V={service.video_pid} A={service.audio_pid} PCR={service.pcr_pid} PMT={service.pmt_pid} "
            f"ca={service.ca_system_ids}"
        )
    return services


def main() -> int:
    args = parse_args()
    # Forzar hardware real: si TBS_MOCK está puesto, este script no tiene sentido.
    os.environ["TBS_MOCK"] = "0"
    print("=" * 78)
    print("dvb-hwcheck — verificación de la capa DVB contra hardware real")
    print("=" * 78)
    print(f"Python {sys.version.split()[0]} | repo: {REPO_ROOT}")
    print(f"/dev/dvb presente: {pathlib.Path('/dev/dvb').exists()} | mock: {registry.is_mock_mode()}")
    if registry.is_mock_mode():
        print("ERROR: no hay /dev/dvb; no se puede verificar el hardware real.")
        return 4

    infos = registry.list_frontends(force_mock=False)
    print_frontends(infos)
    if not infos:
        print("ERROR: no se encontró ningún frontend")
        return 4

    # --- escalas crudas del frontend elegido -----------------------------
    objetivo = next(
        (i for i in infos if i.adapter == args.adapter and i.frontend == args.frontend), None
    )
    if objetivo is None:
        print(f"ERROR: no existe adapter{args.adapter}/frontend{args.frontend}")
        return 4
    frontend = IoctlFrontend(objetivo)
    try:
        frontend.open()
    except FrontendError as exc:
        print(f"ERROR abriendo {objetivo.device_path}: {exc}")
        return 4

    try:
        print(f"\nFE_GET_INFO directo: {frontend.info.name!r} | DTV_API_VERSION = 0x{frontend.api_version():04x}")
        print_raw_scales(objetivo, frontend)

        # --- lock global --------------------------------------------------
        device_lock = registry.get_lock()
        print("\n" + "-" * 78)
        print(f"LOCK GLOBAL ({args.lock_owner})")
        print("-" * 78)
        try:
            device_lock.acquire(args.lock_owner, timeout=10.0)
        except t.DeviceBusyError as exc:
            print(f"OCUPADO: {exc}")
            print(f"  snapshot: {device_lock.snapshot()}")
            return 3
        print(f"  lock adquirido: {device_lock.snapshot()}")
        try:
            transponder = transponder_desde_tabla(args)
            solution = diseqc.compute_lnb(
                transponder.frequency_khz, polarization=transponder.polarization
            )
            print("\nSINTONIZACIÓN")
            print(f"  transponder     : {transponder.label()} (sat {transponder.sat_position})")
            print(f"  polarización    : {transponder.polarization} -> {solution.voltage} V")
            print(f"  banda LNB       : {solution.band} (OL {solution.local_oscillator_khz} kHz, tono 22k={solution.tone_22k})")
            print(f"  FI que se envía : {solution.frequency_khz} kHz")
            print(f"  symbol rate     : {transponder.symbol_rate_ksps} ksps, FEC {transponder.fec}, {transponder.modulation}")

            frontend.set_voltage(solution.voltage)
            frontend.set_tone(solution.tone_22k)
            params = transponder.to_tune_params(
                adapter=args.adapter,
                frontend=args.frontend,
                use_lnb=True,
                lock_timeout_s=args.lock_timeout,
            )
            if args.diagnose:
                diagnosticar_propiedades(frontend, params)
            frontend.tune(params)
            print(f"  DTV_FREQUENCY enviada: {params.if_frequency_khz} kHz (use_lnb=True)")

            inicio = time.monotonic()
            locked = frontend.wait_lock(timeout_s=args.lock_timeout)
            print(f"  wait_lock({args.lock_timeout:.1f}s) -> {locked} en {time.monotonic() - inicio:.2f} s")

            locked_sample, last_stats = muestrear(frontend, args.seconds)
            locked = locked or locked_sample

            services: list[t.ServiceInfo] = []
            if locked and not args.no_demux:
                services = leer_servicios(frontend, args.dwell)

            print("\n" + "=" * 78)
            print("VEREDICTO")
            print("=" * 78)
            print(f"  LOCK: {'SÍ' if locked else 'NO'}")
            if last_stats is not None:
                print(
                    f"  última muestra: señal={_fmt(last_stats.signal_percent, '%')} "
                    f"({_fmt(last_stats.signal_dbm, ' dBm')}) CNR={_fmt(last_stats.snr_db, ' dB')} "
                    f"BER={_fmt(last_stats.ber)} UCB={_fmt(last_stats.ucblocks)} "
                    f"calidad={_fmt(last_stats.quality_percent, '%')} flags={last_stats.lock.raw:#04x}"
                )
            if services:
                print(f"  servicios leídos del TS: {len(services)}")
                for service in services[:10]:
                    print(f"    - {service.name} ({service.service_type_label})")

            if args.scan:
                print("\n" + "-" * 78)
                print("PLAN B: dvbv5-scan")
                print("-" * 78)
                from app.dvb import tools as dvb_tools

                try:
                    scr = dvb_tools.scan_transponder_services(
                        objetivo, transponder, timeout=max(30.0, args.seconds * 4)
                    )
                    print(f"  dvbv5-scan devolvió {len(scr)} servicios")
                    for service in scr[:10]:
                        print(f"    - {service.name}")
                except FrontendError as exc:
                    print(f"  dvbv5-scan no disponible: {exc}")

            return 0 if locked else 2
        finally:
            device_lock.release(args.lock_owner)
            print(f"\nlock liberado: {device_lock.snapshot()}")
    finally:
        frontend.close()
        print(f"frontend cerrado (is_open={frontend.is_open})")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:  # pragma: no cover
        print("\nInterrumpido por el usuario")
        raise SystemExit(130)
