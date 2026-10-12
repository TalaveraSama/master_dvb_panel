#!/usr/bin/env python3
"""Sintonización real paso a paso con diagnóstico (sin pasar por la API)."""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, "/home/ruben/tbs-webui")

from app.dvb import registry  # noqa: E402
from app.dvb.base import FrontendError  # noqa: E402
from app.dvb.types import TuneParams  # noqa: E402


def show(label: str, value) -> None:
    print(f"{label}: {value}")


def main() -> int:
    adapter = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    freq = int(sys.argv[2]) if len(sys.argv) > 2 else 11096000
    pol = sys.argv[3] if len(sys.argv) > 3 else "H"
    sr = int(sys.argv[4]) if len(sys.argv) > 4 else 27500
    system = sys.argv[5] if len(sys.argv) > 5 else "DVB-S2"

    infos = registry.list_frontends()
    info = next((i for i in infos if i.adapter == adapter), None)
    if info is None:
        print("adapter no encontrado")
        return 1
    show("frontend", f"{info.name} {info.device_path}")

    fe = registry.open_frontend(adapter, 0)
    fe.open()
    show("estado inicial", fe.read_status())
    show("stats iniciales", fe.read_stats())

    # 1) Sólo voltaje/tono (ioctls legacy)
    for action, fn in (("set_voltage(18)", lambda: fe.set_voltage(18)), ("set_tone(True)", lambda: fe.set_tone(True))):
        try:
            fn()
            show(action, "OK")
        except FrontendError as exc:
            show(action, f"FALLA -> {exc}")

    # 2) Sintonización completa
    params = TuneParams(
        adapter=adapter,
        frontend=0,
        delivery_system=system,
        frequency_khz=freq,
        symbol_rate_ksps=sr,
        polarization=pol,
        fec="AUTO",
        modulation="AUTO",
        use_lnb=True,
        lnb_low_khz=9750,
        lnb_high_khz=10600,
        lnb_threshold_khz=11700,
        lock_timeout_s=5.0,
    )
    show("params", f"IF={params.if_frequency_khz} kHz banda={params.lnb_band()} tono={params.needed_tone()} voltaje={params.needed_voltage()}")
    try:
        fe.tune(params)
        show("tune()", "OK")
    except FrontendError as exc:
        show("tune()", f"FALLA -> {exc}")
        fe.close()
        return 1

    # 3) Esperar LOCK y muestrear
    locked = fe.wait_lock(timeout_s=6.0)
    show("wait_lock", locked)
    for index in range(6):
        stats = fe.read_stats()
        show(
            f"  t={index * 0.5:.1f}s",
            f"signal={stats.signal_percent}% ({stats.signal_dbm} dBm) snr={stats.snr_db} ber={stats.ber} "
            f"flags={stats.lock} err={stats.error}",
        )
        time.sleep(0.5)

    show("raw stats", fe.read_raw_stats())
    fe.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
