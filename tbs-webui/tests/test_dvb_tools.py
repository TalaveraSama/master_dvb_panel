"""Tests de `app.dvb.tools` (envoltorios de dvbv5-scan / dvb-fe-tool).

Se monkeypatchea `run_tool` con **salidas literales capturadas del host real**
(por eso aparecen los decimales con coma: el host está en español).
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

_WEBUI = pathlib.Path(__file__).resolve().parents[1]
if str(_WEBUI) not in sys.path:
    sys.path.insert(0, str(_WEBUI))

import pytest  # noqa: E402

from app.dvb import registry, satxml, tools  # noqa: E402
from app.dvb import types as t  # noqa: E402

# Salida real de `dvb-fe-tool -a 0` en el host (TBS 6904se)
FE_TOOL_INFO_OUTPUT = """ERROR    FE_SET_VOLTAGE: Operation not permitted
Device TurboSight TBS 6904se DVB-S/S2/S2x  (/dev/dvb/adapter0/frontend0) capabilities:
     CAN_2G_MODULATION
     CAN_FEC_AUTO
     CAN_INVERSION_AUTO
     CAN_MULTISTREAM
     CAN_QPSK
     CAN_RECOVER
DVB API Version 5.12, Current v5 delivery system: DVBS2
Supported delivery systems: 
     DVBS
    [DVBS2]
Frequency range for the current standard: 
From:             950 MHz
To:              2,15 GHz
Symbol rate ranges for the current standard: 
From:            1,00 MBauds
To:              45,0 MBauds
SEC: set voltage to OFF
"""

# Salida real de `dvb-fe-tool -m -c 1 -a 0` (modo femon)
FE_TOOL_STATS_OUTPUT = """ERROR    FE_SET_VOLTAGE: Operation not permitted
       (0x1f) Signal= -42,50dBm
\t  Layer A: Signal= 78,00%
\t  Layer A: C/N= 12,40dB
\t  Layer A: UCB= 0
"""

DVBS2_CHANNELS_CONF = """[La 1 HD]
\tDELIVERY_SYSTEM = DVBS2
\tFREQUENCY = 11362000
\tPOLARIZATION = HORIZONTAL
\tSYMBOL_RATE = 22000000
\tINNER_FEC = 2/3
\tSERVICE_ID = 1001
\tVIDEO_PID = 513
\tAUDIO_PID = 514
\tPMT_PID = 256
\tVIDEO_CODEC = H.264
"""

VDR_CHANNELS_CONF = """# channels.conf clásico
Das Erste;ARD:11836000:H:27500:101:102:0:0:0
Canal Pago;Movistar:11362000:H:22000:201:202:0:C0B00:0
"""


def _completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["fake"], returncode=returncode, stdout=stdout, stderr="")


# ---------------------------------------------------------------------------
# Herramientas del sistema
# ---------------------------------------------------------------------------
def test_tool_available_y_available_tools():
    assert tools.tool_available("python-no-existe-xyz") is False
    disponibles = tools.available_tools()
    assert set(disponibles) == {"dvbv5-scan", "dvbv5-zap", "dvb-fe-tool"}
    assert all(isinstance(v, bool) for v in disponibles.values())


def test_run_tool_binario_ausente_lanza_unsupported():
    with pytest.raises(t.UnsupportedError):
        tools.run_tool(["no-existe-xyz", "--help"])


def test_run_tool_sin_argumentos():
    with pytest.raises(t.DvbError):
        tools.run_tool([])


def test_run_tool_timeout(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)

    monkeypatch.setattr(tools, "tool_available", lambda name: True)
    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    with pytest.raises(t.DvbError):
        tools.run_tool(["dvbv5-scan"], timeout=1)


def test_run_tool_ok_con_binario_del_sistema():
    if not tools.tool_available("python"):
        pytest.skip("sin intérprete 'python' en el PATH")
    resultado = tools.run_tool(["python", "-c", "print(1)"], timeout=20)
    assert resultado.stdout.strip() == "1"


# ---------------------------------------------------------------------------
# channels.conf
# ---------------------------------------------------------------------------
def test_parse_channels_conf_dvbv5():
    transponders = tools.parse_channels_conf(DVBS2_CHANNELS_CONF)
    assert len(transponders) == 1
    transponder = transponders[0]
    assert transponder.delivery_system == "DVB-S2"
    assert transponder.frequency_khz == 11_362_000
    assert transponder.symbol_rate_ksps == 22_000
    assert transponder.polarization == "H"
    assert transponder.fec == "2/3"


def test_parse_channels_conf_vdr_clasico():
    transponders = tools.parse_channels_conf(VDR_CHANNELS_CONF)
    assert len(transponders) == 2
    assert transponders[0].frequency_khz == 11_836_000
    assert transponders[0].polarization == "H"
    assert transponders[0].symbol_rate_ksps == 27_500
    assert transponders[1].frequency_khz == 11_362_000


def test_parse_channels_conf_vacio():
    assert tools.parse_channels_conf("") == []
    assert tools.parse_channels_conf("# sólo comentarios\n") == []


def test_parse_channels_file_servicios_dvbv5():
    channels = tools.parse_channels_file(DVBS2_CHANNELS_CONF, path="canales.conf")
    assert channels.path == "canales.conf"
    assert len(channels.transponders) == 1
    assert len(channels.services) == 1
    servicio = channels.services[0]
    assert servicio.name == "La 1 HD"
    assert servicio.service_id == 1001
    assert servicio.video_pid == 513
    assert servicio.audio_pid == 514
    assert servicio.pmt_pid == 256
    assert servicio.is_fta is True
    assert channels.is_empty is False
    assert channels.to_dict()["services"] == 1


def test_parse_channels_file_servicios_vdr():
    channels = tools.parse_channels_file(VDR_CHANNELS_CONF)
    assert len(channels.services) == 2
    gratis, pago = channels.services
    assert gratis.name == "Das Erste"
    assert gratis.provider == "ARD"
    assert gratis.video_pid == 101
    assert gratis.is_fta is True
    assert pago.scrambled is True
    assert pago.is_fta is False


def test_parse_channels_file_vacio():
    channels = tools.parse_channels_file("")
    assert channels.is_empty is True
    assert channels.transponders == () and channels.services == ()


# ---------------------------------------------------------------------------
# Generación del formato DVBv5
# ---------------------------------------------------------------------------
def test_transponder_to_dvbv5_ida_y_vuelta():
    original = t.Transponder(
        delivery_system="DVB-S2",
        frequency_khz=11_362_000,
        symbol_rate_ksps=22_000,
        polarization="H",
        fec="2/3",
        modulation="8PSK",
        sat_position="19.2E",
    )
    texto = tools.transponder_to_dvbv5(original, name="Astra 19.2E")
    assert "[Astra 19.2E]" in texto
    assert "DELIVERY_SYSTEM = DVBS2" in texto
    assert "FREQUENCY = 11362000" in texto
    vuelta = satxml.parse_tables_text(texto, name="Astra-19.2E")
    assert len(vuelta) == 1
    assert vuelta[0].frequency_khz == original.frequency_khz
    assert vuelta[0].symbol_rate_ksps == original.symbol_rate_ksps
    assert vuelta[0].polarization == original.polarization
    assert vuelta[0].fec == original.fec
    # libdvbv5 llama PSK/8 a 8PSK (rechaza "8PSK": verificado en el host)
    assert "MODULATION = PSK/8" in texto
    assert "8PSK" not in texto
    assert vuelta[0].modulation == "8PSK"  # el dominio usa la grafía de la API


def test_transponder_to_dvbv5_terrestre_usa_hz():
    texto = tools.transponder_to_dvbv5(
        t.Transponder("DVB-T", 570_000, bandwidth_mhz=8, modulation="QAM_64"), name="Madrid"
    )
    assert "FREQUENCY = 570000000" in texto
    assert "BANDWIDTH_HZ = 8000000" in texto
    assert "MODULATION = QAM/64" in texto
    vuelta = satxml.parse_tables_text(texto)
    assert vuelta[0].frequency_khz == 570_000
    assert vuelta[0].bandwidth_mhz == 8


# ---------------------------------------------------------------------------
# dvb-fe-tool
# ---------------------------------------------------------------------------
def test_dvb_fe_tool_info_parsea_la_salida_real(monkeypatch):
    monkeypatch.setattr(tools, "run_tool", lambda *a, **k: _completed(FE_TOOL_INFO_OUTPUT))
    info = tools.dvb_fe_tool_info(0, 0)
    assert info is not None
    assert info.name == "TurboSight TBS 6904se DVB-S/S2/S2x"
    assert info.device_path == "/dev/dvb/adapter0/frontend0"
    assert info.type == "FE_QPSK"
    assert info.delivery_systems == ("DVB-S", "DVB-S2")
    assert info.min_frequency == 950_000
    assert info.max_frequency == 2_150_000
    assert info.symbol_rates == (1_000_000, 45_000_000)


def test_dvb_fe_tool_info_sin_salida_reconocible(monkeypatch):
    monkeypatch.setattr(tools, "run_tool", lambda *a, **k: _completed("nada útil"))
    assert tools.dvb_fe_tool_info(0, 0) is None


def test_dvb_fe_tool_info_binario_ausente(monkeypatch):
    def boom(*args, **kwargs):
        raise t.UnsupportedError("no está instalado")

    monkeypatch.setattr(tools, "run_tool", boom)
    assert tools.dvb_fe_tool_info(0, 0) is None


def test_dvb_fe_tool_stats_parsea_la_salida_real(monkeypatch):
    monkeypatch.setattr(tools, "run_tool", lambda *a, **k: _completed(FE_TOOL_STATS_OUTPUT))
    stats = tools.dvb_fe_tool_stats(0, 0)
    assert stats.lock.raw == 0x1F
    assert stats.lock.is_locked is True
    assert stats.signal_dbm == pytest.approx(-42.5)
    assert stats.signal_percent == pytest.approx(78.0)
    assert stats.snr_db == pytest.approx(12.4)
    assert stats.cnr_raw == pytest.approx(12.4)
    assert stats.ucblocks == 0
    assert stats.quality_percent is not None
    # El EPERM de FE_SET_VOLTAGE se reporta, pero sin inventar medidas.
    assert stats.error is not None and "FE_SET_VOLTAGE" in stats.error


def test_dvb_fe_tool_stats_salida_no_reconocida(monkeypatch):
    monkeypatch.setattr(tools, "run_tool", lambda *a, **k: _completed("(0x00) nada\n"))
    stats = tools.dvb_fe_tool_stats(0, 0)
    assert stats.lock.raw == 0
    assert stats.signal_percent is None
    assert stats.error is not None


def test_dvb_fe_tool_stats_binario_ausente_no_lanza(monkeypatch):
    def boom(*args, **kwargs):
        raise t.UnsupportedError("falta dvb-fe-tool")

    monkeypatch.setattr(tools, "run_tool", boom)
    stats = tools.dvb_fe_tool_stats(0, 0)
    assert stats.error is not None
    assert stats.lock.is_locked is False


def test_list_frontends_tool(monkeypatch):
    monkeypatch.setattr(tools, "tool_available", lambda name: True)

    monkeypatch.setattr(
        tools,
        "dvb_fe_tool_info",
        lambda adapter, frontend=0: (
            t.FrontendInfo(
                adapter=adapter,
                frontend=frontend,
                name="TurboSight TBS 6904se DVB-S/S2/S2x",
                device_path=f"/dev/dvb/adapter{adapter}/frontend0",
                type="FE_QPSK",
                caps=0,
                delivery_systems=("DVB-S", "DVB-S2"),
                min_frequency=950000,
                max_frequency=2150000,
                symbol_rates=(1000000, 45000000),
            )
            if adapter <= 3
            else None
        ),
    )
    infos = tools.list_frontends_tool()
    assert [i.adapter for i in infos] == [0, 1, 2, 3]
    assert all(i.delivery_systems == ("DVB-S", "DVB-S2") for i in infos)


def test_list_frontends_tool_sin_binario(monkeypatch):
    monkeypatch.setattr(tools, "tool_available", lambda name: False)
    with pytest.raises(t.UnsupportedError):
        tools.list_frontends_tool()


# ---------------------------------------------------------------------------
# scan_transponder_services
# ---------------------------------------------------------------------------
def test_scan_transponder_services_en_mock_no_toca_el_hardware(monkeypatch):
    monkeypatch.setenv("TBS_MOCK", "1")
    info = t.FrontendInfo(0, 0, "TBS", "/dev/dvb/adapter0/frontend0", "FE_QPSK", 0, (), 0, 0)
    transponder = t.Transponder("DVB-S2", 11_362_000, symbol_rate_ksps=22_000, polarization="H")
    assert tools.scan_transponder_services(info, transponder, timeout=1.0) == []


def test_scan_transponder_services_usa_dvbv5_scan(monkeypatch):
    monkeypatch.setenv("TBS_MOCK", "0")
    monkeypatch.setattr(registry, "is_mock_mode", lambda: False)
    monkeypatch.setattr(tools, "run_tool", lambda *a, **k: _completed(""))
    escrito: dict[str, str] = {}

    def fake_scan(adapter, transponder, out_path, timeout=0, **kwargs):
        texto = DVBS2_CHANNELS_CONF
        escrito["path"] = str(out_path)
        return tools.parse_channels_file(texto, path=str(out_path))

    monkeypatch.setattr(tools, "dvbv5_scan_to_channels", fake_scan)
    info = t.FrontendInfo(0, 0, "TBS", "/dev/dvb/adapter0/frontend0", "FE_QPSK", 0, (), 0, 0)
    transponder = t.Transponder("DVB-S2", 11_362_000, symbol_rate_ksps=22_000, polarization="H")
    services = tools.scan_transponder_services(info, transponder, timeout=2.0)
    assert [s.name for s in services] == ["La 1 HD"]
