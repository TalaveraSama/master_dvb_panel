"""Tests de `app.dvb.types` (contrato INTERFACES.md §2 + extensión de servicios).

Se ejecutan en Windows sin hardware.
"""

from __future__ import annotations

import pathlib
import sys

_WEBUI = pathlib.Path(__file__).resolve().parents[1]
if str(_WEBUI) not in sys.path:
    sys.path.insert(0, str(_WEBUI))

import pytest  # noqa: E402

from app.dvb import types as t  # noqa: E402


# ---------------------------------------------------------------------------
# DeliverySystem
# ---------------------------------------------------------------------------
def test_delivery_system_valores_de_la_api():
    assert t.DeliverySystem.DVB_S.value == "DVB-S"
    assert t.DeliverySystem.DVB_S2.value == "DVB-S2"
    assert t.DeliverySystem.DVB_T2.value == "DVB-T2"
    assert t.DeliverySystem.ISDB_T.value == "ISDB-T"


def test_delivery_system_no_colapsa_miembros():
    """`@dataclass` sobre un Enum rompería la igualdad: aquí NO se aplica."""
    assert t.DeliverySystem.DVB_S != t.DeliverySystem.DVB_S2
    assert len({t.DeliverySystem.DVB_S, t.DeliverySystem.DVB_S2}) == 2
    assert len({t.DeliverySystem.DVB_S: 1, t.DeliverySystem.DVB_S2: 2}) == 2
    assert t.DeliverySystem("DVB-S2") is t.DeliverySystem.DVB_S2


def test_delivery_system_es_str_legible():
    assert t.DeliverySystem.DVB_S2 == "DVB-S2"
    assert str(t.DeliverySystem.DVB_S2) == "DVB-S2"
    assert f"{t.DeliverySystem.DVB_T2}" == "DVB-T2"
    assert " ".join([t.DeliverySystem.DVB_S2, "11362 MHz"]) == "DVB-S2 11362 MHz"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("DVB-S2", "DVB-S2"),
        ("dvbs2", "DVB-S2"),
        ("DVB_S2", "DVB-S2"),
        ("DVB-S2X", "DVB-S2"),
        ("DVBS", "DVB-S"),
        ("dvb-t", "DVB-T"),
        (t.DeliverySystem.DVB_C, "DVB-C"),
        (None, ""),
    ],
)
def test_normalize_delivery_system(raw, expected):
    assert t.normalize_delivery_system(raw) == expected


# ---------------------------------------------------------------------------
# LockFlags / SignalStats
# ---------------------------------------------------------------------------
def test_lock_flags_is_locked_exige_signal_y_lock():
    assert t.LockFlags(True, True, True, True, True).is_locked is True
    assert t.LockFlags(False, True, True, True, True).is_locked is False
    assert t.LockFlags(True, True, True, True, False).is_locked is False
    assert t.LockFlags(False, False, False, False, False).is_locked is False
    assert t.LockFlags(True, False, False, False, True, raw=0x11).raw == 0x11


def test_lock_flags_es_inmutable():
    flags = t.LockFlags(True, True, True, True, True)
    with pytest.raises(Exception):
        flags.lock = False  # type: ignore[misc]


def test_signal_stats_empty():
    stats = t.SignalStats.empty("sin frontend")
    assert stats.error == "sin frontend"
    assert stats.lock.is_locked is False
    assert stats.signal_percent is None and stats.snr_db is None
    assert stats.timestamp > 0


# ---------------------------------------------------------------------------
# TuneParams: LNB y conmutación
# ---------------------------------------------------------------------------
def test_tuneparams_banda_baja_astra_19_2e():
    """11362 MHz en Astra 19.2E: OL 9750 -> FI 1612 MHz, banda baja, H -> 18 V."""
    params = t.TuneParams(
        delivery_system="DVB-S2",
        frequency_khz=11_362_000,
        polarization="H",
        symbol_rate_ksps=22_000,
        use_lnb=True,
    )
    assert params.band == "low"
    assert params.lnb_offset_khz == 9_750_000
    assert params.if_frequency_khz == 1_612_000
    assert params.needed_tone() is False
    assert params.needed_voltage() == "18V"


def test_tuneparams_banda_alta():
    """12551.5 MHz: OL 10600 -> FI 1951.5 MHz, banda alta, tono 22 kHz, V -> 13 V."""
    params = t.TuneParams(
        delivery_system="DVB-S2",
        frequency_khz=12_551_500,
        polarization="V",
        use_lnb=True,
    )
    assert params.band == "high"
    assert params.lnb_offset_khz == 10_600_000
    assert params.if_frequency_khz == 1_951_500
    assert params.needed_tone() is True
    assert params.needed_voltage() == "13V"


def test_tuneparams_respeta_override_de_tono_y_voltaje():
    params = t.TuneParams(
        delivery_system="DVB-S2",
        frequency_khz=12_551_500,
        polarization="V",
        use_lnb=True,
        tone_22k=False,
        voltage="18V",
    )
    assert params.needed_tone() is False
    assert params.needed_voltage() == "18V"


def test_tuneparams_sin_lnb_usa_la_frecuencia_tal_cual():
    """INTERFACES.md §2: `frequency_khz` ya puede venir como FI."""
    params = t.TuneParams(delivery_system="DVB-S2", frequency_khz=1_612_000, use_lnb=False)
    assert params.lnb_offset_khz == 0
    assert params.if_frequency_khz == 1_612_000
    assert params.needed_tone() is False


def test_tuneparams_acepta_lnb_en_khz_reales():
    """Acepta 9_750_000 kHz (valor real) y 9750 (MHz, como /api/settings)."""
    en_mhz = t.TuneParams(delivery_system="DVB-S2", frequency_khz=11_362_000, use_lnb=True)
    en_khz = t.TuneParams(
        delivery_system="DVB-S2",
        frequency_khz=11_362_000,
        use_lnb=True,
        lnb_low_khz=9_750_000,
        lnb_high_khz=10_600_000,
        lnb_threshold_khz=11_700_000,
    )
    assert en_mhz.if_frequency_khz == en_khz.if_frequency_khz == 1_612_000


def test_tuneparams_construccion_posicional_del_contrato():
    """Los 18 primeros campos mantienen el orden de INTERFACES.md §2."""
    params = t.TuneParams("DVB-S2", 11_362_000, 22_000, "H", "2/3", "8PSK")
    assert params.delivery_system == "DVB-S2"
    assert params.frequency_khz == 11_362_000
    assert params.symbol_rate_ksps == 22_000
    assert params.polarization == "H"
    assert params.fec == "2/3"
    assert params.modulation == "8PSK"
    assert params.inversion == "AUTO"
    assert params.is_multi_stream is False
    assert params.adapter == 0 and params.frontend == 0
    assert params.lock_timeout_s == 5.0


def test_tuneparams_to_dict_es_serializable():
    import json

    params = t.TuneParams(delivery_system=t.DeliverySystem.DVB_S2, frequency_khz=11_362_000)
    data = json.loads(json.dumps(params.to_dict()))
    assert data["delivery_system"] == "DVB-S2"
    assert data["frequency_khz"] == 11_362_000


# ---------------------------------------------------------------------------
# Transponder
# ---------------------------------------------------------------------------
def test_transponder_to_tune_params():
    transponder = t.Transponder(
        delivery_system="DVB-S2",
        frequency_khz=11_362_000,
        symbol_rate_ksps=22_000,
        polarization="H",
        fec="2/3",
        modulation="8PSK",
        sat_position="19.2E",
        code_rate_hp=None,
        transmission_mode=None,
    )
    params = transponder.to_tune_params(adapter=2, use_lnb=True, lock_timeout_s=7.0)
    assert isinstance(params, t.TuneParams)
    assert params.adapter == 2
    assert params.use_lnb is True
    assert params.lock_timeout_s == 7.0
    assert params.if_frequency_khz == 1_612_000
    assert params.symbol_rate_ksps == 22_000
    assert params.delivery_system == "DVB-S2"


def test_transponder_campos_del_contrato_y_extension():
    transponder = t.Transponder("DVB-T", 570_000, bandwidth_mhz=8)
    assert transponder.symbol_rate_ksps is None
    assert transponder.sat_position is None
    assert transponder.code_rate_hp is None
    assert transponder.hierarchy is None
    assert transponder.label() == "DVB-T 570 MHz"


def test_transponder_acepta_claves_de_transponderin():
    """`schemas.TransponderIn.model_dump()` debe caber en `Transponder`."""
    claves = {
        "delivery_system",
        "frequency_khz",
        "symbol_rate_ksps",
        "polarization",
        "fec",
        "modulation",
        "bandwidth_mhz",
        "inversion",
        "rolloff",
        "pilot",
        "stream_id",
        "plp_id",
        "sat_position",
        "code_rate_hp",
        "code_rate_lp",
        "guard_interval",
        "transmission_mode",
        "hierarchy",
    }
    assert claves <= set(t.Transponder.__dataclass_fields__)


def test_tuneparams_acepta_claves_de_tunerequest():
    """`schemas.TuneRequest.model_dump()` debe caber en `TuneParams`."""
    claves = {
        "adapter",
        "frontend",
        "delivery_system",
        "frequency_khz",
        "symbol_rate_ksps",
        "polarization",
        "fec",
        "modulation",
        "bandwidth_mhz",
        "code_rate_hp",
        "code_rate_lp",
        "guard_interval",
        "transmission_mode",
        "hierarchy",
        "inversion",
        "rolloff",
        "pilot",
        "stream_id",
        "plp_id",
        "is_multi_stream",
        "lnb_low_khz",
        "lnb_high_khz",
        "lnb_threshold_khz",
        "use_lnb",
        "tone_22k",
        "voltage",
        "diseqc",
        "lock_timeout_s",
    }
    assert claves <= set(t.TuneParams.__dataclass_fields__)


# ---------------------------------------------------------------------------
# ServiceInfo / ScanProgress / errores
# ---------------------------------------------------------------------------
def test_service_type_label():
    assert t.service_type_label(0x01) == "TV"
    assert t.service_type_label(0x02) == "Radio"
    assert t.service_type_label(0x19) == "HDTV"
    assert t.service_type_label(0x0C) == "Data"
    assert t.service_type_label(0xFFFF) == "Data"


def test_scan_progress_idle_y_to_dict():
    progress = t.ScanProgress.idle()
    assert progress.state == "idle"
    assert progress.errors == ()
    data = progress.to_dict()
    assert data["errors"] == []
    assert data["percent"] == 0.0


def test_jerarquia_de_errores():
    assert issubclass(t.DeviceBusyError, t.FrontendError)
    assert issubclass(t.DeviceNotFoundError, t.FrontendError)
    assert issubclass(t.UnsupportedError, t.FrontendError)
    assert issubclass(t.LockTimeoutError, t.FrontendError)
    assert issubclass(t.DvbError, t.FrontendError)
    assert issubclass(t.FrontendError, Exception)


def test_base_reexporta_errores_y_protocolo():
    from app.dvb import base

    assert base.DeviceBusyError is t.DeviceBusyError
    assert base.FrontendError is t.FrontendError
    assert hasattr(base, "Frontend")


def test_types_es_hoja_pura():
    """`types` no debe arrastrar otros módulos de app.dvb ni FastAPI.

    Se analizan sólo las sentencias `import` reales con AST: la documentación
    puede mencionar módulos prohibidos sin importarlos.
    """
    import ast
    import importlib

    module = importlib.import_module("app.dvb.types")
    for nombre in ("app.dvb.base", "app.dvb.registry", "app.dvb.frontend_ioctl"):
        assert nombre not in sys.modules or module is not sys.modules[nombre]

    arbol = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
    importados: set[str] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            importados.update(alias.name for alias in nodo.names)
        elif isinstance(nodo, ast.ImportFrom):
            base = nodo.module or ""
            importados.add(f"{'.' * nodo.level}{base}")
            for alias in nodo.names:
                importados.add(f"{base}.{alias.name}" if base else alias.name)

    for prohibido in ("app.config", "fastapi", ".base", ".registry"):
        assert not any(prohibido in nombre for nombre in importados), f"types importa {prohibido}"
