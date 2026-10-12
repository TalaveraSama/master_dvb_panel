#!/usr/bin/env python3
"""Comprobación RÁPIDA de la capa DVB real: información de frontends y stats.

No sintoniza; sólo comprueba que el descubrimiento y la lectura de información
funcionan sobre /dev/dvb. Uso (en el host real):
    .venv/bin/python /home/ruben/dvb-info.py
"""

from __future__ import annotations

import sys

sys.path.insert(0, "/home/ruben/tbs-webui")

from app.dvb import registry  # noqa: E402


def main() -> int:
    print("mock mode:", registry.is_mock_mode())
    infos = registry.list_frontends()
    print(f"frontends detectados: {len(infos)}")
    for info in infos:
        print(
            f"  adapter{info.adapter}/frontend{info.frontend}: {info.name!r}\n"
            f"      tipo={info.type} sistemas={list(info.delivery_systems)}\n"
            f"      freq=[{info.min_frequency}, {info.max_frequency}] sr={list(info.symbol_rates)}\n"
            f"      caps={info.caps:#x} path={info.device_path}"
        )
    if not infos:
        print("ERROR: no se detectaron frontends")
        return 1

    fe = registry.open_frontend(infos[0].adapter, infos[0].frontend)
    fe.open()
    try:
        print("\napi_version:", getattr(fe, "api_version", lambda: "n/d")())
        try:
            raw = fe.read_raw_stats()  # type: ignore[attr-defined]
            print("raw stats (cmd: (escala, valor, con_signo)):")
            for cmd, value in raw.items():
                print(f"    {cmd}: {value}")
        except Exception as exc:
            print("read_raw_stats falló:", repr(exc))
        stats = fe.read_stats()
        print(
            "SignalStats:"
            f" signal={stats.signal_percent}% dbm={stats.signal_dbm}"
            f" snr={stats.snr_db}dB ber={stats.ber} flags={stats.lock}"
            f" error={stats.error}"
        )
    finally:
        fe.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
