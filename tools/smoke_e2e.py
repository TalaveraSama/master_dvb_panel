#!/usr/bin/env python3
"""Smoke test end-to-end contra un servidor en marcha (modo mock o real).

Uso:
    python tools/smoke_e2e.py --base http://127.0.0.1:8010 --user admin --password ...

Comprueba el flujo de aceptación completo: asistente/login, cookie de sesión +
CSRF, adaptadores, sintonización, barras de señal por WebSocket, escaneo con
progreso y log, canales guardados y exportaciones.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import httpx


class Smoke:
    def __init__(self, base: str, user: str, password: str) -> None:
        self.base = base.rstrip("/")
        self.user = user
        self.password = password
        self.client = httpx.Client(base_url=self.base, timeout=30.0, follow_redirects=False)
        self.csrf: str | None = None
        self.failures: list[str] = []
        self.checks = 0

    # ------------------------------------------------------------------
    def check(self, label: str, condition: bool, detail: str = "") -> None:
        self.checks += 1
        mark = "OK " if condition else "FAIL"
        line = f"[{mark}] {label}"
        if detail and not condition:
            line += f" -> {detail}"
        print(line)
        if not condition:
            self.failures.append(label)

    def close(self) -> None:
        self.client.close()

    # ------------------------------------------------------------------
    def run(self) -> int:
        # 1) Salud y estado público
        health = self.client.get("/api/health")
        self.check("GET /api/health = 200", health.status_code == 200, health.text)
        data = health.json()
        self.check("modo detectado en /api/health", data.get("mock") in (True, False), json.dumps(data))
        print(f"      modo: {'SIMULACIÓN' if data.get('mock') else 'HARDWARE REAL'}")
        self.check("adaptadores detectados > 0", data.get("adapters", 0) > 0, str(data.get("adapters")))

        # 2) Sin sesión: nada accesible
        for path in ("/api/adapters", "/api/status", "/api/channels", "/api/export/m3u"):
            response = self.client.get(path)
            self.check(f"sin sesión {path} = 401", response.status_code == 401, str(response.status_code))

        # 3) Login (o asistente si no hay usuarios)
        me = self.client.get("/api/auth/me").json()
        if me.get("must_setup"):
            response = self.client.post(
                "/api/auth/setup", json={"username": self.user, "password": self.password}
            )
            self.check("asistente inicial crea el admin", response.status_code == 200, response.text)
        else:
            response = self.client.post(
                "/api/auth/login", json={"username": self.user, "password": self.password}
            )
            self.check("login correcto", response.status_code == 200, response.text)
        if response.status_code != 200:
            return self.report()
        self.csrf = response.json()["csrf_token"]

        cookies = {c.name for c in self.client.cookies.jar}
        self.check("cookie de sesión presente", any("session" in name for name in cookies), str(cookies))

        # 4) CSRF obligatorio en mutaciones
        bad = self.client.put("/api/settings", json={"default_adapter": 0}, headers={"X-CSRF-Token": "malo"})
        self.check("mutación con CSRF inválido = 403", bad.status_code == 403, str(bad.status_code))

        headers = {"X-CSRF-Token": self.csrf}
        adapters = self.client.get("/api/adapters")
        self.check("GET /api/adapters con sesión = 200", adapters.status_code == 200, adapters.text)
        adata = adapters.json()
        self.check("lista de adaptadores no vacía", len(adata["adapters"]) > 0, json.dumps(adata))
        first = adata["adapters"][0]
        print(f"      frontend: adapter{first['adapter']}/frontend{first['frontend']} {first['name']}")

        # 5) Sintonización
        tune_body = {
            "adapter": first["adapter"],
            "frontend": first["frontend"],
            "delivery_system": "DVB-S2",
            "frequency_khz": 11096000,
            "symbol_rate_ksps": 27500,
            "polarization": "V",
            "fec": "3/4",
            "modulation": "8PSK",
            "use_lnb": True,
            "lnb_low_khz": 9750,
            "lnb_high_khz": 10600,
            "lnb_threshold_khz": 11700,
        }
        tune = self.client.post("/api/tune", json=tune_body, headers=headers)
        self.check("POST /api/tune = 200", tune.status_code == 200, tune.text)
        tdata = tune.json() if tune.status_code == 200 else {}
        self.check("LOCK tras sintonizar", bool(tdata.get("locked")), json.dumps(tdata.get("stats")))
        stats = tdata.get("stats") or {}
        self.check("signal_percent presente", stats.get("signal_percent") is not None, json.dumps(stats))
        self.check("snr_db presente", stats.get("snr_db") is not None, json.dumps(stats))
        self.check("flags de lock presentes", bool(stats.get("flags")), json.dumps(stats))
        print(f"      señal={stats.get('signal_percent')}%  SNR={stats.get('snr_db')} dB  BER={stats.get('ber')}")
        print(f"      IF={tdata.get('if_frequency_khz')} kHz (transponder {tdata.get('frequency_khz')} kHz)")

        # 6) Estado y barras en vivo (varias lecturas del monitor)
        readings = []
        for _ in range(4):
            time.sleep(0.6)
            status = self.client.get("/api/status").json()
            if status.get("stats"):
                readings.append(status["stats"]["signal_percent"])
        self.check("GET /api/status devuelve estadísticas", len(readings) >= 2, str(readings))
        self.check("las barras se mueven (valores distintos)", len(set(readings)) > 1, str(readings))

        history = self.client.get("/api/status/history").json()
        self.check("histórico de señal disponible", isinstance(history, list) and len(history) > 0, str(history))

        # 7) WebSocket (cliente `websockets` para probar el servidor real)
        self.check_websocket()

        # 8) Escaneo
        tables = self.client.get("/api/scan/tables")
        self.check("GET /api/scan/tables = 200", tables.status_code == 200, tables.text)
        bundle_count = len(tables.json().get("bundles", []))
        print(f"      tablas de escaneo disponibles: {bundle_count}")

        start = self.client.post(
            "/api/scan/start",
            json={
                "adapters": [{"adapter": first["adapter"], "frontend": first["frontend"]}],
                "transponders": [
                    {"delivery_system": "DVB-S2", "frequency_khz": 11096000, "symbol_rate_ksps": 27500, "polarization": "V"},
                    {"delivery_system": "DVB-S2", "frequency_khz": 12226000, "symbol_rate_ksps": 27500, "polarization": "H"},
                ],
                "dwell_seconds": 1.0,
            },
            headers=headers,
        )
        self.check("POST /api/scan/start = 200", start.status_code == 200, start.text)

        progress = {}
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            status = self.client.get("/api/scan/status").json()
            progress = status["progress"]
            if progress["state"] in ("done", "error", "cancelled"):
                break
            time.sleep(0.5)
        self.check("escaneo finalizado", progress.get("state") == "done", json.dumps(progress))
        self.check("progreso al 100%", progress.get("percent") == 100.0, str(progress.get("percent")))
        self.check("servicios encontrados", (progress.get("services_found") or 0) > 0, json.dumps(progress))
        scan_status = self.client.get("/api/scan/status").json()
        self.check("log de escaneo no vacío", len(scan_status.get("log", [])) > 0, "")
        for entry in scan_status["log"][-5:]:
            print(f"      [{entry['level']}] {entry['msg']}")

        # 9) Canales guardados
        channels = self.client.get("/api/channels").json()
        self.check("canales guardados en SQLite", channels["total"] > 0, json.dumps(channels)[:200])
        if channels["items"]:
            sample = channels["items"][0]
            print(
                f"      canal: {sample['display_name']!r} SID={sample['service_id']} "
                f"{sample['delivery_system']} {sample['frequency_khz']} kHz vpid={sample['video_pid']} "
                f"apid={sample['audio_pid']} FTA={sample['is_fta']}"
            )
        filtered = self.client.get("/api/channels", params={"q": channels["items"][0]["name"][:4]}).json()
        self.check("búsqueda de canales funciona", filtered["total"] >= 1, json.dumps(filtered)[:120])

        if channels["items"]:
            target = channels["items"][0]["id"]
            renamed = self.client.patch(
                f"/api/channels/{target}", json={"custom_name": "Canal Renombrado"}, headers=headers
            )
            self.check("PATCH /api/channels/{id} renombra", renamed.status_code == 200, renamed.text)

        transponders = self.client.get("/api/transponders").json()
        self.check("transponders guardados", transponders["total"] > 0, json.dumps(transponders)[:150])

        # 10) Exportaciones
        m3u = self.client.get("/api/export/m3u")
        self.check("export M3U = 200", m3u.status_code == 200, str(m3u.status_code))
        self.check("M3U con cabecera #EXTM3U", m3u.text.startswith("#EXTM3U"), m3u.text[:60])
        conf = self.client.get("/api/export/channels.conf")
        self.check("export channels.conf = 200", conf.status_code == 200, str(conf.status_code))
        self.check(
            "channels.conf con líneas de canal",
            any(line and not line.startswith("#") for line in conf.text.splitlines()),
            conf.text[:120],
        )
        print(f"      M3U: {len(m3u.text.splitlines())} líneas · channels.conf: {len(conf.text.splitlines())} líneas")

        # 11) Ajustes y logs
        settings = self.client.get("/api/settings")
        self.check("GET /api/settings = 200", settings.status_code == 200, settings.text)
        put = self.client.put("/api/settings", json={"ui": {"signal_poll_ms": 500}}, headers=headers)
        self.check("PUT /api/settings = 200", put.status_code == 200, put.text)
        logs = self.client.get("/api/logs")
        self.check("GET /api/logs = 200", logs.status_code == 200, logs.text)

        # 12) Liberar y cerrar sesión
        release = self.client.post("/api/tune/release", headers=headers)
        self.check("POST /api/tune/release = 200", release.status_code == 200, release.text)
        logout = self.client.post("/api/auth/logout", headers=headers)
        self.check("POST /api/auth/logout = 200", logout.status_code == 200, logout.text)
        after = self.client.get("/api/adapters")
        self.check("tras logout /api/adapters = 401", after.status_code == 401, str(after.status_code))

        return self.report()

    # ------------------------------------------------------------------
    def check_websocket(self) -> None:
        """Conecta al WebSocket de señal con la cookie de sesión y valida el flujo."""
        import asyncio

        try:
            asyncio.run(self._websocket_flow())
        except Exception as exc:  # pragma: no cover - diagnóstico
            self.check("WebSocket /ws/signal", False, repr(exc))

    async def _websocket_flow(self) -> None:
        import asyncio

        import websockets

        ws_url = self.base.replace("https://", "wss://").replace("http://", "ws://") + "/ws/signal"
        cookie_header = "; ".join(f"{c.name}={c.value}" for c in self.client.cookies.jar)
        async with websockets.connect(ws_url, additional_headers={"Cookie": cookie_header}) as ws:
            await ws.send(json.dumps({"type": "ping"}))
            pong = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            self.check("WebSocket responde a ping", pong.get("type") == "pong", json.dumps(pong))

            stats = None
            for _ in range(20):
                message = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                if message.get("type") == "stats":
                    stats = message
                    break
            self.check("WebSocket envía estadísticas en vivo", stats is not None, "")
            if stats:
                self.check("WebSocket incluye histórico", isinstance(stats.get("history"), list), "")
                self.check("WebSocket incluye flags de LOCK", bool(stats.get("flags")), "")
                print(
                    f"      WS signal={stats.get('signal_percent')}% snr={stats.get('snr_db')} "
                    f"locked={stats.get('locked')} puntos={len(stats.get('history') or [])}"
                )

            # Sintonización enviada por WebSocket
            await ws.send(
                json.dumps(
                    {
                        "type": "tune",
                        "delivery_system": "DVB-S2",
                        "frequency_khz": 10744000,
                        "symbol_rate_ksps": 22000,
                        "polarization": "H",
                    }
                )
            )
            tuned = None
            for _ in range(20):
                message = json.loads(await asyncio.wait_for(ws.recv(), timeout=8))
                if message.get("type") == "tuned":
                    tuned = message
                    break
            self.check("WebSocket acepta orden de sintonización", tuned is not None, "")
            if tuned:
                print(f"      WS tune -> locked={tuned.get('locked')} IF={tuned.get('if_frequency_khz')} kHz")

            await ws.send(json.dumps({"type": "release"}))
            released = False
            for _ in range(20):
                message = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                if message.get("type") == "released":
                    released = True
                    break
            self.check("WebSocket acepta liberar el dispositivo", released, "")

    # ------------------------------------------------------------------
    def report(self) -> int:
        print()
        print("=" * 62)
        if self.failures:
            print(f"RESULTADO: {len(self.failures)} FALLOS de {self.checks} comprobaciones")
            for failure in self.failures:
                print(f"  - {failure}")
            return 1
        print(f"RESULTADO: OK — {self.checks} comprobaciones superadas")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8010")
    parser.add_argument("--user", default="admin")
    # La contraseña se pasa por argumento o por entorno: nunca está en el repo.
    parser.add_argument(
        "--password",
        default=os.environ.get("TBS_ADMIN_PASSWORD", ""),
        help="Contraseña del panel (o define TBS_ADMIN_PASSWORD)",
    )
    args = parser.parse_args()
    if not args.password:
        print("Falta la contraseña: usa --password o la variable TBS_ADMIN_PASSWORD.")
        return 2

    smoke = Smoke(args.base, args.user, args.password)
    try:
        return smoke.run()
    except Exception as exc:
        print(f"ERROR inesperado: {exc!r}")
        return 2
    finally:
        smoke.close()


if __name__ == "__main__":
    raise SystemExit(main())
