"""Tests de `app.dvb.tools_frontend` (plan B con dvbv5-zap) sin hardware."""

from __future__ import annotations

import pathlib
import sys

_WEBUI = pathlib.Path(__file__).resolve().parents[1]
if str(_WEBUI) not in sys.path:
    sys.path.insert(0, str(_WEBUI))

import pytest  # noqa: E402

from app.dvb import registry, tools_frontend  # noqa: E402
from app.dvb import types as t  # noqa: E402
from app.dvb.tools_frontend import ToolsFrontend  # noqa: E402

# Salidas reales de las herramientas en el host (locale C; el español usa comas)
ZAP_LINES = [
    "using demux 'dvb0.demux0'\n",
    "reading channels from file '/tmp/tbs-zap-xxx.conf'\n",
    "       (0x00) Signal= -51,70dBm\n",
    "\t  Layer A: Signal= 80,08%\n",
    "       (0x1f) Signal= -42,50dBm\n",
    "\t  Layer A: Signal= 78,00%\n",
    "\t  Layer A: C/N= 12,40dB\n",
    "\t  Layer A: UCB= 0\n",
]


def _info() -> t.FrontendInfo:
    return t.FrontendInfo(
        adapter=0,
        frontend=0,
        name="TurboSight TBS 6904se DVB-S/S2/S2x",
        device_path="/dev/dvb/adapter0/frontend0",
        type="FE_QPSK",
        caps=0x54000601,
        delivery_systems=("DVB-S", "DVB-S2"),
        min_frequency=950000,
        max_frequency=2150000,
        symbol_rates=(1000000, 45000000),
    )


def test_open_sin_herramientas_lanza_unsupported(monkeypatch):
    monkeypatch.setattr(tools_frontend, "tool_available", lambda name: False)
    frontend = ToolsFrontend(_info())
    with pytest.raises(t.UnsupportedError):
        frontend.open()
    assert frontend.is_open is False


def test_open_ok_con_herramientas(monkeypatch):
    monkeypatch.setattr(tools_frontend, "tool_available", lambda name: True)
    frontend = ToolsFrontend(_info())
    frontend.open()
    assert frontend.is_open is True
    frontend.close()
    frontend.close()  # idempotente


def test_feed_line_parsea_la_salida_real(monkeypatch):
    monkeypatch.setattr(tools_frontend, "tool_available", lambda name: True)
    frontend = ToolsFrontend(_info())
    frontend.open()
    for line in ZAP_LINES:
        frontend.feed_line(line)
    stats = frontend.read_stats()
    assert stats.lock.raw == 0x1F
    assert stats.lock.is_locked is True
    assert stats.signal_dbm == pytest.approx(-42.5)
    assert stats.signal_percent == pytest.approx(78.0)
    assert stats.snr_db == pytest.approx(12.4)
    assert stats.ucblocks == 0
    assert stats.error is None
    assert frontend.read_status().is_locked is True
    frontend.close()


def test_feed_line_estado_sin_lock(monkeypatch):
    monkeypatch.setattr(tools_frontend, "tool_available", lambda name: True)
    frontend = ToolsFrontend(_info())
    frontend.open()
    frontend.feed_line("       (0x00) Signal= -90,50dBm\n")
    stats = frontend.read_stats()
    assert stats.lock.raw == 0
    assert stats.lock.is_locked is False
    assert stats.signal_dbm == pytest.approx(-90.5)
    assert stats.error is None
    frontend.close()


def test_feed_line_error(monkeypatch):
    monkeypatch.setattr(tools_frontend, "tool_available", lambda name: True)
    frontend = ToolsFrontend(_info())
    frontend.open()
    frontend.feed_line("ERROR value 8PSK is invalid for MODULATION while parsing line 7\n")
    assert frontend.read_stats().error is not None
    frontend.close()


def test_feed_line_ignora_ruido(monkeypatch):
    monkeypatch.setattr(tools_frontend, "tool_available", lambda name: True)
    frontend = ToolsFrontend(_info())
    frontend.open()
    frontend.feed_line("using demux 'dvb0.demux0'\n")
    frontend.feed_line("\n")
    stats = frontend.read_stats()
    assert stats.lock.raw == 0 and stats.signal_dbm is None
    frontend.close()


class _FakeProcess:
    """Proceso falso de dvbv5-zap."""

    def __init__(self, lines):
        self.stdout = iter(lines)
        self.returncode = 0
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):  # pragma: no cover
        self.terminated = True


def test_tune_lanza_dvbv5_zap_con_el_canal_correcto(monkeypatch, tmp_path):
    monkeypatch.setattr(tools_frontend, "tool_available", lambda name: True)
    capturado: dict[str, object] = {}

    def fake_popen(args, **kwargs):
        capturado["args"] = list(args)
        capturado["kwargs"] = kwargs
        return _FakeProcess(["       (0x1f) Signal= -42,50dBm\n"])

    monkeypatch.setattr(tools_frontend.subprocess, "Popen", fake_popen)
    frontend = ToolsFrontend(_info())
    frontend.open()
    params = t.Transponder(
        "DVB-S2", 11_362_000, symbol_rate_ksps=22_000, polarization="H", fec="2/3", modulation="8PSK"
    ).to_tune_params(use_lnb=True)
    frontend.tune(params)
    args = capturado["args"]
    assert args[0] == "dvbv5-zap"
    assert "-a" in args and args[args.index("-a") + 1] == "0"
    assert "-m" in args
    assert args[-1] == frontend.channel_name
    # El fichero de canales se escribe en formato DVBv5 y libdvbv5 exige PSK/8.
    contenido = pathlib.Path(frontend._channels_file).read_text(encoding="utf-8")
    assert "[TBSWEBUI]" in contenido
    assert "DELIVERY_SYSTEM = DVBS2" in contenido
    assert "FREQUENCY = 11362000" in contenido
    assert "MODULATION = PSK/8" in contenido
    assert "8PSK" not in contenido
    # El hilo lector consume la línea.
    frontend._reader.join(timeout=2.0)
    assert frontend.read_stats().lock.is_locked is True
    fichero = frontend._channels_file
    frontend.close()
    assert frontend.is_open is False
    assert not pathlib.Path(fichero).exists()


def test_wait_lock_sin_lock_expira(monkeypatch):
    monkeypatch.setattr(tools_frontend, "tool_available", lambda name: True)
    monkeypatch.setattr(tools_frontend.subprocess, "Popen", lambda *a, **k: _FakeProcess([]))
    frontend = ToolsFrontend(_info())
    frontend.open()
    frontend.tune(t.TuneParams(delivery_system="DVB-S2", frequency_khz=11_362_000, use_lnb=True))
    assert frontend.wait_lock(timeout_s=0.2) is False
    with pytest.raises(t.LockTimeoutError):
        frontend.wait_lock(timeout_s=0.1, raise_on_timeout=True)
    frontend.close()


def test_wait_lock_exitoso(monkeypatch):
    monkeypatch.setattr(tools_frontend, "tool_available", lambda name: True)
    monkeypatch.setattr(
        tools_frontend.subprocess, "Popen", lambda *a, **k: _FakeProcess([])
    )
    frontend = ToolsFrontend(_info())
    frontend.open()
    frontend.tune(t.TuneParams(delivery_system="DVB-S2", frequency_khz=11_362_000, use_lnb=True))
    frontend.feed_line("       (0x1f) Signal= -42,50dBm\n")
    assert frontend.wait_lock(timeout_s=0.3) is True
    frontend.close()


def test_tune_sin_open(monkeypatch):
    monkeypatch.setattr(tools_frontend, "tool_available", lambda name: True)
    frontend = ToolsFrontend(_info())
    with pytest.raises(t.DeviceNotFoundError):
        frontend.tune(t.TuneParams(delivery_system="DVB-S2", frequency_khz=11_362_000))


def test_conmutacion_se_registra(monkeypatch):
    monkeypatch.setattr(tools_frontend, "tool_available", lambda name: True)
    frontend = ToolsFrontend(_info())
    frontend.open()
    frontend.set_voltage(18)
    frontend.set_tone(False)
    frontend.send_diseqc(bytes([0xE0, 0x10, 0x38, 0xF2]))
    assert frontend.voltage == 18
    assert frontend.tone is False
    assert frontend.diseqc_log == [bytes([0xE0, 0x10, 0x38, 0xF2])]
    with pytest.raises(t.UnsupportedError):
        frontend.set_voltage(7)
    frontend.close()


def test_no_expone_demux():
    frontend = ToolsFrontend(_info())
    assert not callable(getattr(frontend, "read_ts", None))
    assert not callable(getattr(frontend, "set_pid_list", None))


def test_cumple_el_protocolo_frontend(monkeypatch):
    from app.dvb.base import Frontend

    monkeypatch.setattr(tools_frontend, "tool_available", lambda name: True)
    frontend = ToolsFrontend(_info())
    frontend.open()
    assert isinstance(frontend, Frontend)
    frontend.close()


# ---------------------------------------------------------------------------
# Selección de modo en el registro
# ---------------------------------------------------------------------------
def test_is_tools_mode(monkeypatch):
    monkeypatch.delenv("TBS_IOCTL", raising=False)
    monkeypatch.delenv("TBS_FRONTEND", raising=False)
    assert registry.is_tools_mode() is False
    monkeypatch.setenv("TBS_IOCTL", "0")
    assert registry.is_tools_mode() is True
    monkeypatch.setenv("TBS_IOCTL", "1")
    assert registry.is_tools_mode() is False
    monkeypatch.delenv("TBS_IOCTL")
    monkeypatch.setenv("TBS_FRONTEND", "tools")
    assert registry.is_tools_mode() is True


def test_open_frontend_prefer_tools(monkeypatch):
    monkeypatch.setattr(registry, "is_mock_mode", lambda: False)
    monkeypatch.setattr(registry, "devices_present", lambda: True)
    monkeypatch.setattr(registry.Path, "exists", lambda self: True)
    monkeypatch.setattr(registry, "find_frontend_info", lambda a, f=0: _info())
    frontend = registry.open_frontend(0, 0, prefer_tools=True)
    assert isinstance(frontend, ToolsFrontend)
    frontend = registry.open_frontend(0, 0, prefer_tools=False)
    assert not isinstance(frontend, ToolsFrontend)
