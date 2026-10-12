"""Tests de `app.dvb.mock` (frontend simulado)."""

from __future__ import annotations

import pathlib
import sys
import time

_WEBUI = pathlib.Path(__file__).resolve().parents[1]
if str(_WEBUI) not in sys.path:
    sys.path.insert(0, str(_WEBUI))

import pytest  # noqa: E402

from app.dvb import mock as mock_module  # noqa: E402
from app.dvb import types as t  # noqa: E402


def _params(**overrides) -> t.TuneParams:
    data = {
        "delivery_system": "DVB-S2",
        "frequency_khz": 11_362_000,
        "symbol_rate_ksps": 22_000,
        "polarization": "H",
        "fec": "2/3",
        "modulation": "8PSK",
        "use_lnb": True,
    }
    data.update(overrides)
    return t.TuneParams(**data)


def test_info_del_mock_imita_la_tarjeta_real():
    info = mock_module.mock_frontend_info(2, 0)
    assert "TBS 6904se" in info.name
    assert info.device_path == "/dev/dvb/adapter2/frontend0"
    assert info.type == "FE_QPSK"
    assert info.delivery_systems == ("DVB-S", "DVB-S2")
    assert (info.min_frequency, info.max_frequency) == (950000, 2150000)
    assert info.caps & 0x10000000  # CAN_2G_MODULATION
    assert len(mock_module.mock_frontend_infos()) == 4


def test_open_close_idempotentes():
    frontend = mock_module.MockFrontend(0, 0, seed=1)
    assert frontend.is_open is False
    frontend.open()
    frontend.open()
    assert frontend.is_open is True
    assert frontend.open_count == 1
    frontend.close()
    frontend.close()
    assert frontend.is_open is False
    assert frontend.close_count == 1
    with pytest.raises(t.DeviceNotFoundError):
        frontend.read_status()


def test_context_manager():
    with mock_module.MockFrontend(0, 0, seed=1) as frontend:
        assert frontend.is_open is True
    assert frontend.is_open is False


def test_lock_paulatino_y_progresivo():
    """El contrato pide un lock de ~1.5 s con bits progresivos."""
    assert mock_module.MockFrontend.LOCK_DELAY_S == pytest.approx(1.5)
    frontend = mock_module.MockFrontend(0, 0, seed=2)
    frontend.open()
    frontend.tune(_params())
    assert frontend.read_status().raw == 0
    # Con timeout corto no hay lock y no bloquea más de la cuenta.
    inicio = time.monotonic()
    assert frontend.wait_lock(timeout_s=0.4) is False
    assert time.monotonic() - inicio < 1.2
    # Con timeout suficiente se alcanza el lock y los bits aparecen.
    assert frontend.wait_lock(timeout_s=3.0) is True
    flags = frontend.read_status()
    assert flags.is_locked is True
    assert flags.signal and flags.carrier and flags.viterbi and flags.sync and flags.lock
    frontend.close()


def test_wait_lock_raise_on_timeout():
    frontend = mock_module.MockFrontend(0, 0, seed=3)
    frontend.open()
    frontend.tune(_params())
    with pytest.raises(t.LockTimeoutError):
        frontend.wait_lock(timeout_s=0.2, raise_on_timeout=True)
    frontend.close()


def test_lock_con_delay_corto(monkeypatch):
    monkeypatch.setattr(mock_module.MockFrontend, "LOCK_DELAY_S", 0.05)
    frontend = mock_module.MockFrontend(0, 0, seed=4)
    frontend.open()
    frontend.tune(_params())
    assert frontend.wait_lock(timeout_s=2.0) is True
    frontend.close()


def test_force_no_lock():
    frontend = mock_module.MockFrontend(0, 0, seed=5, force_no_lock=True)
    frontend.open()
    frontend.tune(_params())
    assert frontend.wait_lock(timeout_s=0.3) is False
    stats = frontend.read_stats()
    assert stats.lock.is_locked is False
    assert stats.snr_db is None
    assert stats.signal_percent is not None and stats.signal_percent < 25
    frontend.close()


def test_lock_probability_cero_no_bloquea():
    frontend = mock_module.MockFrontend(0, 0, seed=6, lock_probability=0.0)
    frontend.open()
    frontend.tune(_params())
    assert frontend.wait_lock(timeout_s=0.5) is False
    frontend.close()


def test_tune_frecuencia_invalida():
    frontend = mock_module.MockFrontend(0, 0, seed=7)
    frontend.open()
    with pytest.raises(t.UnsupportedError):
        frontend.tune(_params(frequency_khz=0))
    frontend.close()


def test_tune_sistema_no_soportado():
    frontend = mock_module.MockFrontend(0, 0, seed=8)
    frontend.open()
    with pytest.raises(t.UnsupportedError):
        frontend.tune(_params(delivery_system="ISDB-T", frequency_khz=500_000))
    frontend.close()


def test_tune_sin_open():
    frontend = mock_module.MockFrontend(0, 0, seed=9)
    with pytest.raises(t.DeviceNotFoundError):
        frontend.tune(_params())


def test_stats_realistas_con_lock(monkeypatch):
    monkeypatch.setattr(mock_module.MockFrontend, "LOCK_DELAY_S", 0.01)
    frontend = mock_module.MockFrontend(0, 0, seed=10)
    frontend.open()
    frontend.tune(_params())
    assert frontend.wait_lock(timeout_s=1.0) is True
    valores = []
    for _ in range(5):
        stats = frontend.read_stats()
        assert stats.lock.is_locked is True
        assert 0.0 <= stats.signal_percent <= 100.0
        assert stats.signal_dbm is not None and stats.signal_dbm < 0
        assert stats.snr_db is not None and 5.0 < stats.snr_db < 20.0
        assert stats.quality_percent is not None
        assert stats.error is None
        valores.append(stats.signal_percent)
        time.sleep(0.05)
    assert len(set(valores)) > 1  # hay jitter
    frontend.close()


def test_stats_sin_sintonizar_devuelve_error():
    frontend = mock_module.MockFrontend(0, 0, seed=11)
    frontend.open()
    stats = frontend.read_stats()
    assert stats.error is not None
    assert stats.lock.is_locked is False
    frontend.close()


def test_conmutacion_y_diseqc():
    frontend = mock_module.MockFrontend(0, 0, seed=12)
    frontend.open()
    frontend.set_voltage(18)
    frontend.set_tone(False)
    assert (frontend.voltage, frontend.tone) == (18, False)
    frontend.send_diseqc(bytes([0xE0, 0x10, 0x38, 0xF2]))
    assert frontend.diseqc_log == [bytes([0xE0, 0x10, 0x38, 0xF2])]
    with pytest.raises(t.UnsupportedError):
        frontend.set_voltage(7)
    with pytest.raises(t.UnsupportedError):
        frontend.send_diseqc(b"\x01")
    frontend.close()


def test_mock_services_incluye_tv_radio_y_cifrados():
    frontera = mock_module.MockFrontend(0, 0, seed=13)
    transponder = t.Transponder("DVB-S2", 11_362_000, symbol_rate_ksps=22_000, polarization="H")
    services = frontera.mock_services(transponder)
    assert 6 <= len(services) <= 12
    etiquetas = {s.service_type_label for s in services}
    assert "TV" in etiquetas and "Radio" in etiquetas
    assert any(s.scrambled for s in services)
    assert any(s.is_fta for s in services)
    assert all(s.name for s in services)
    assert all(s.is_fta != s.scrambled for s in services)
    cifrado = next(s for s in services if s.scrambled)
    assert cifrado.ca_system_ids == (0x0B00,)
    radio = next(s for s in services if s.service_type == 0x02)
    assert radio.video_pid is None and radio.audio_pid is not None


def test_mock_services_varian_con_la_frecuencia():
    frontend = mock_module.MockFrontend(0, 0, seed=14)
    uno = frontend.mock_services(t.Transponder("DVB-S2", 11_362_000))
    otro = frontend.mock_services(t.Transponder("DVB-S2", 12_551_500))
    assert [s.service_id for s in uno] != [s.service_id for s in otro]


def test_mock_no_expone_demux():
    """No define `read_ts`/`set_pid_list`: así el escaneo usa mock_services()."""
    frontend = mock_module.MockFrontend(0, 0)
    assert not callable(getattr(frontend, "read_ts", None))
    assert not callable(getattr(frontend, "set_pid_list", None))


def test_mock_cumple_el_protocolo_frontend():
    from app.dvb.base import Frontend

    frontend = mock_module.MockFrontend(0, 0)
    assert isinstance(frontend, Frontend)
    assert frontend.is_mock is True
    assert isinstance(frontend.info, t.FrontendInfo)
