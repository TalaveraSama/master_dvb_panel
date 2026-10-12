#!/usr/bin/env python3
"""Diagnóstico comparativo: nuestro FE_SET_PROPERTY vs libdvbv5.

Hipótesis: nuestro paquete difiere del de libdvbv5 (tamaño de cabecera,
contenido del array, orden de propiedades). Este script:

  1) comprueba si libdvbv5 está disponible para llamarla directamente,
  2) hace un strace de `dvb-fe-tool -a N` y de `dvbv5-scan` para extraer el
     hex exacto de los ioctl FE_SET_PROPERTY/FE_GET_PROPERTY,
  3) compara con lo que genera `pack_properties` de app.dvb.frontend_ioctl.

Uso (en el host real):
    python ioctl-compare.py 2
"""

from __future__ import annotations

import ctypes
import ctypes.util
import re
import shutil
import subprocess
import sys

sys.path.insert(0, "/home/ruben/tbs-webui")


def check_libdvbv5() -> None:
    print("--- 1) libdvbv5 disponible ---")
    name = ctypes.util.find_library("dvbv5")
    print("ctypes.util.find_library('dvbv5'):", name)
    for candidate in ("libdvbv5.so.0", "libdvbv5.so"):
        try:
            lib = ctypes.CDLL(candidate)
            print(f"  cargada {candidate}: {lib}")
            for symbol in ("dvb_fe_open", "dvb_fe_set_props", "dvb_fe_get_props", "dvb_fe_get_stats"):
                print(f"    {symbol}:", hasattr(lib, symbol))
            return
        except OSError as exc:
            print(f"  {candidate}: {exc}")


def run_strace(command: list[str], out_file: str) -> str:
    if not shutil.which("strace"):
        return ""
    try:
        with open(out_file, "w") as handle:
            subprocess.run(
                ["strace", "-f", "-e", "trace=ioctl", "-xx", "-s", "400", *command],
                stdout=subprocess.DEVNULL,
                stderr=handle,
                timeout=60,
            )
    except Exception as exc:
        print("strace falló:", exc)
        return ""
    return open(out_file, encoding="utf-8", errors="replace").read()


def extract(path: str) -> None:
    print(f"--- 2) ioctl de {path} ---")
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        print("  sin fichero")
        return
    for number, line in enumerate(text.splitlines()):
        if "0x40106f52" in line or "0x80106f53" in line:
            match = re.search(r"ioctl\(\d+, (0x[0-9a-f]+), (.*?)\)\s*=", line)
            if match:
                code, arg = match.groups()
                print(f"  [{number}] {code}: {arg[:260]}")
    counts = {"set": text.count("0x40106f52"), "get": text.count("0x80106f53")}
    print("  totales:", counts)


def ours() -> None:
    print("--- 3) nuestro pack_properties ---")
    from app.dvb import frontend_ioctl as fi

    try:
        props = [(1, 6), (2, 1346000000)]
        buffer = fi.pack_properties([fi.pack_property_u32(cmd, value) for cmd, value in props])
        print("  len(buffer):", len(buffer))
        print("  hex[:64]   :", bytes(buffer[:64]).hex())
        print("  hex[64:96] :", bytes(buffer[64:96]).hex())
        print("  num (u32 @0):", int.from_bytes(buffer[0:4], "little"))
        print("  ptr (u64 @8):", int.from_bytes(buffer[8:16], "little"))
    except Exception as exc:
        print("  fallo:", exc)


def main() -> int:
    adapter = sys.argv[1] if len(sys.argv) > 1 else "2"
    check_libdvbv5()
    run_strace(["dvb-fe-tool", "-a", adapter], "/tmp/strace-fe-tool.log")
    extract("/tmp/strace-fe-tool.log")
    run_strace(
        ["dvbv5-scan", "-a", adapter, "-o", "/tmp/scan-out.conf", "-t", "1",
         "/usr/share/dvb/dvb-s/Astra-19.2E"],
        "/tmp/strace-scan.log",
    )
    extract("/tmp/strace-scan.log")
    ours()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
