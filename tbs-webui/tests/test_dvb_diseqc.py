"""Tests de `app.dvb.diseqc` (LNB, voltaje, tono 22 kHz y DiSEqC 1.0/1.1).

`app/services/device_manager.py` usa `diseqc.diseqc_command("1.0:2")`, así que
ese camino está cubierto explícitamente.
"""

from __future__ import annotations

import pathlib
import sys

_WEBUI = pathlib.Path(__file__).resolve().parents[1]
if str(_WEBUI) not in sys.path:
    sys.path.insert(0, str(_WEBUI))

import pytest  # noqa: E402

from app.dvb import diseqc  # noqa: E402
from app.dvb import types as t  # noqa: E402
from app.dvb.mock import MockFrontend  # noqa: E402


# ---------------------------------------------------------------------------
# Configuración del LNB
# ---------------------------------------------------------------------------
def test_lnb_config_por_defecto():
    config = diseqc.LNBConfig()
    assert config.low_khz == 9750
    assert config.high_khz == 10600
    assert config.threshold_khz == 11700
    assert config.local_oscillator_khz(11_362_000) == 9_750_000
    assert config.local_oscillator_khz(12_551_500) == 10_600_000
    assert config.band_for(11_362_000) == "low"
    assert config.band_for(11_700_000) == "high"


def test_lnb_config_acepta_khz_reales():
    config = diseqc.LNBConfig(low_khz=9_750_000, high_khz=10_600_000, threshold_khz=11_700_000)
    assert config.local_oscillator_khz(11_362_000) == 9_750_000
    assert config.threshold == 11_700_000


# ---------------------------------------------------------------------------
# compute_lnb
# ---------------------------------------------------------------------------
def test_compute_lnb_astra_banda_baja_horizontal():
    solution = diseqc.compute_lnb(11_362_000, polarization="H")
    assert solution.frequency_khz == 1_612_000
    assert solution.transponder_khz == 11_362_000
    assert solution.band == "low"
    assert solution.tone_22k is False
    assert solution.voltage == 18
    assert solution.local_oscillator_khz == 9_750_000
    assert solution.to_dict()["frequency_khz"] == 1_612_000


def test_compute_lnb_banda_alta_vertical():
    solution = diseqc.compute_lnb(12_551_500, polarization="V")
    assert solution.frequency_khz == 1_951_500
    assert solution.band == "high"
    assert solution.tone_22k is True
    assert solution.voltage == 13
    assert solution.local_oscillator_khz == 10_600_000


def test_compute_lnb_banda_explicita():
    baja = diseqc.compute_lnb(12_000_000, band="low", polarization="H")
    assert baja.local_oscillator_khz == 9_750_000
    assert baja.frequency_khz == 2_250_000
    alta = diseqc.compute_lnb(12_000_000, band="high", polarization="H")
    assert alta.local_oscillator_khz == 10_600_000
    automatica = diseqc.compute_lnb(12_000_000, band="AUTO")
    assert automatica.band == "high"


def test_compute_lnb_polarizaciones_circulares():
    assert diseqc.compute_lnb(11_362_000, polarization="R").voltage == 18
    assert diseqc.compute_lnb(11_362_000, polarization="L").voltage == 13


def test_compute_lnb_errores():
    with pytest.raises(t.FrontendError):
        diseqc.compute_lnb(0)
    with pytest.raises(t.FrontendError):
        diseqc.compute_lnb(-1)
    with pytest.raises(t.FrontendError):
        diseqc.compute_lnb(11_362_000, band="mediana")
    with pytest.raises(t.FrontendError):
        diseqc.compute_lnb(1_000)  # FI negativa con OL 9750 MHz


def test_helpers_de_voltaje_y_tono():
    assert diseqc.voltage_for_polarization("H") == 18
    assert diseqc.voltage_for_polarization("R") == 18
    assert diseqc.voltage_for_polarization("V") == 13
    assert diseqc.voltage_for_polarization("L") == 13
    assert diseqc.voltage_for_polarization(None) == 13
    assert diseqc.tone_for_band("high") is True
    assert diseqc.tone_for_band("LOW") is False
    assert diseqc.tone_for_band(None) is False


def test_coherencia_con_tuneparams():
    """`diseqc.compute_lnb` y `TuneParams` deben dar la misma FI y voltaje."""
    params = t.TuneParams(
        delivery_system="DVB-S2", frequency_khz=11_362_000, polarization="H", use_lnb=True
    )
    solution = diseqc.compute_lnb(params.frequency_khz, polarization=params.polarization)
    assert solution.frequency_khz == params.if_frequency_khz
    assert solution.tone_22k == params.needed_tone()
    assert f"{solution.voltage}V" == params.needed_voltage()


# ---------------------------------------------------------------------------
# Mensajes DiSEqC
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("posicion", [0, 1, 2, 3])
def test_diseqc_1_0(posicion):
    mensaje = diseqc.diseqc_position_message(posicion)
    assert mensaje == bytes([0xE0, 0x10, 0x38, 0xF0 | posicion])
    assert len(mensaje) == 4


@pytest.mark.parametrize("posicion", [0, 4, 15])
def test_diseqc_1_1(posicion):
    mensaje = diseqc.diseqc_position_message(posicion, uncommitted=True)
    assert mensaje == bytes([0xE0, 0x10, 0x39, 0xF0 | posicion])


def test_diseqc_posiciones_fuera_de_rango():
    with pytest.raises(ValueError):
        diseqc.diseqc_position_message(4)
    with pytest.raises(ValueError):
        diseqc.diseqc_position_message(16, uncommitted=True)
    with pytest.raises(ValueError):
        diseqc.diseqc_position_message(-1)


def test_diseqc_secuencia_1_1():
    secuencia = diseqc.diseqc_position_sequence(5, uncommitted=True)
    assert secuencia == bytes([0xE0, 0x10, 0x38, 0xF0, 0xE0, 0x10, 0x39, 0xF5])
    assert diseqc.diseqc_position_sequence(2) == bytes([0xE0, 0x10, 0x38, 0xF2])


@pytest.mark.parametrize(
    ("spec", "esperado"),
    [
        ("1.0:0", bytes([0xE0, 0x10, 0x38, 0xF0])),
        ("1.0:3", bytes([0xE0, 0x10, 0x38, 0xF3])),
        ("10:2", bytes([0xE0, 0x10, 0x38, 0xF2])),
        ("committed:1", bytes([0xE0, 0x10, 0x38, 0xF1])),
        (
            "1.1:5",
            bytes([0xE0, 0x10, 0x38, 0xF0, 0xE0, 0x10, 0x39, 0xF5]),
        ),
        (
            "uncommitted:0",
            bytes([0xE0, 0x10, 0x38, 0xF0, 0xE0, 0x10, 0x39, 0xF0]),
        ),
    ],
)
def test_diseqc_command(spec, esperado):
    assert diseqc.diseqc_command(spec) == esperado
    assert diseqc.parse_diseqc(spec) == esperado


@pytest.mark.parametrize("spec", ["", "1.0", "1.2:0", "1.0:x", "sinformato", "1.0:9"])
def test_diseqc_command_invalido(spec):
    with pytest.raises(ValueError):
        diseqc.diseqc_command(spec)


# ---------------------------------------------------------------------------
# LnbController sobre el frontend simulado
# ---------------------------------------------------------------------------
def test_lnb_controller_switch_to():
    frontend = MockFrontend(0, 0, seed=21)
    frontend.open()
    controller = diseqc.LnbController()
    solution = controller.switch_to(frontend, 11_362_000, polarization="H")
    assert solution.frequency_khz == 1_612_000
    assert frontend.voltage == 18
    assert frontend.tone is False
    assert frontend.diseqc_log == []
    assert controller.last_solution is solution
    frontend.close()


def test_lnb_controller_con_diseqc():
    frontend = MockFrontend(0, 0, seed=22)
    frontend.open()
    controller = diseqc.LnbController()
    controller.switch_to(frontend, 12_551_500, polarization="V", diseqc="1.0:2")
    assert frontend.voltage == 13
    assert frontend.tone is True
    assert frontend.diseqc_log == [bytes([0xE0, 0x10, 0x38, 0xF2])]
    frontend.close()


def test_lnb_controller_send_y_select():
    frontend = MockFrontend(0, 0, seed=23)
    frontend.open()
    controller = diseqc.LnbController()
    # DiSEqC 1.1: dos mensajes (committed + uncommitted), uno por ioctl.
    assert controller.select_position(frontend, "1.1:3") == bytes(
        [0xE0, 0x10, 0x38, 0xF0, 0xE0, 0x10, 0x39, 0xF3]
    )
    assert frontend.diseqc_log == [
        bytes([0xE0, 0x10, 0x38, 0xF0]),
        bytes([0xE0, 0x10, 0x39, 0xF3]),
    ]
    assert controller.last_diseqc == frontend.diseqc_log[-1]
    with pytest.raises(t.FrontendError):
        diseqc.LnbController().send(object(), b"\xE0\x10\x38\xF0")
    frontend.close()


def test_split_diseqc_messages():
    from app.dvb.base import split_diseqc_messages

    assert split_diseqc_messages(b"\xE0\x10\x38\xF2") == [b"\xE0\x10\x38\xF2"]
    assert split_diseqc_messages(b"\xE0\x10\x38\xF0\xE0\x10\x39\xF5") == [
        b"\xE0\x10\x38\xF0",
        b"\xE0\x10\x39\xF5",
    ]
    with pytest.raises(t.UnsupportedError):
        split_diseqc_messages(b"")
    with pytest.raises(t.UnsupportedError):
        split_diseqc_messages(b"\x01\x02")
    with pytest.raises(t.UnsupportedError):
        split_diseqc_messages(b"\x01\x02\x03\x04\x05\x06\x07")


def test_mock_send_diseqc_acepta_secuencia():
    frontend = MockFrontend(0, 0, seed=24)
    frontend.open()
    frontend.send_diseqc(diseqc.diseqc_command("1.1:0"))
    assert len(frontend.diseqc_log) == 2
    frontend.close()
