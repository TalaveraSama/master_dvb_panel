"""Conmutación de antena: LNB universal, voltaje, tono 22 kHz y DiSEqC 1.0/1.1.

Referencias:
* ETSI EN 50494 (DiSEqC 1.0) y ETSI EN 50607 (DiSEqC 1.1 / uncommitted).
* LNB universal: 9.75 GHz (banda baja) / 10.6 GHz (banda alta), umbral 11.7 GHz.
  El tono de 22 kHz selecciona la banda alta y el voltaje la polarización
  (convención del proyecto: H/R -> 18 V, V/L -> 13 V).

El helper principal es `compute_lnb()`: recibe la frecuencia del transponder en
kHz y devuelve la frecuencia intermedia (FI) ya restada, la banda, el tono y el
voltaje. `LnbController` aplica esa solución a un objeto `Frontend`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base import split_diseqc_messages
from .types import FrontendError

__all__ = [
    "LNBConfig",
    "LNBSolution",
    "LnbController",
    "compute_lnb",
    "diseqc_command",
    "parse_diseqc",
    "diseqc_position_message",
    "diseqc_position_sequence",
    "voltage_for_polarization",
    "tone_for_band",
    "DISEQC_ADDRESS_ANY",
    "DISEQC_COMMAND_COMMITTED",
    "DISEQC_COMMAND_UNCOMMITTED",
]

# Umbral de banda de un LNB universal, en kHz (11.7 GHz).
DEFAULT_THRESHOLD_KHZ = 11_700_000

DISEQC_ADDRESS_ANY = 0xE0        # todas las unidades de conmutación
DISEQC_FRAMING = 0x10            # master command, sin respuesta
DISEQC_COMMAND_COMMITTED = 0x38  # "write to port group 0" (DiSEqC 1.0)
DISEQC_COMMAND_UNCOMMITTED = 0x39  # uncommitted switch (DiSEqC 1.1)

# Nº máximo de puertos de un conmutador DiSEqC 1.0 / 1.1.
MAX_POSITION_10 = 4
MAX_POSITION_11 = 16


def _lnb_value_khz(value: int) -> int:
    """Normaliza los valores de LNB a kHz.

    `app/config.py` y `/api/settings` etiquetan los LNB como "*_khz" pero usan
    valores en MHz (9750 / 10600 / 11700). Un valor < 100000 se interpreta como
    MHz; por encima, como kHz.
    """
    number = int(value)
    return number * 1000 if 0 < number < 100_000 else number


# ---------------------------------------------------------------------------
# Configuración y solución del LNB
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LNBConfig:
    """Parámetros de un LNB universal (valores en kHz o MHz, ver `_lnb_value_khz`)."""

    low_khz: int = 9750
    high_khz: int = 10600
    threshold_khz: int = 11700

    @property
    def low_hz(self) -> int:
        return _lnb_value_khz(self.low_khz) * 1000

    @property
    def high_hz(self) -> int:
        return _lnb_value_khz(self.high_khz) * 1000

    @property
    def threshold(self) -> int:
        return _lnb_value_khz(self.threshold_khz)

    def band_for(self, frequency_khz: int) -> str:
        """Banda del LNB para una frecuencia de transponder."""
        return "high" if int(frequency_khz) >= self.threshold else "low"

    def local_oscillator_khz(self, frequency_khz: int, band: str | None = None) -> int:
        band = (band or self.band_for(frequency_khz)).lower()
        return _lnb_value_khz(self.high_khz if band == "high" else self.low_khz)


@dataclass(frozen=True)
class LNBSolution:
    """Resultado de `compute_lnb()`: todo lo necesario para sintonizar."""

    frequency_khz: int            # frecuencia intermedia (FI) a enviar al frontend
    transponder_khz: int          # frecuencia original del transponder
    band: str                     # "low" | "high"
    tone_22k: bool                # True => 22 kHz activado (banda alta)
    voltage: int                  # 13 | 18 | 0
    polarization: str | None      # "H" | "V" | "L" | "R" | None
    local_oscillator_khz: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "frequency_khz": self.frequency_khz,
            "transponder_khz": self.transponder_khz,
            "band": self.band,
            "tone_22k": self.tone_22k,
            "voltage": self.voltage,
            "polarization": self.polarization,
            "local_oscillator_khz": self.local_oscillator_khz,
        }


def voltage_for_polarization(polarization: str | None) -> int:
    """Voltaje del LNB para una polarización.

    Convención usada en todo el proyecto (TuneParams.needed_voltage, TuneRequest
    y INTERFACES.md): **H/R -> 18 V, V/L -> 13 V**. Para polarización lineal
    (el caso de Astra 19.2E) coincide con el estándar del LNB universal.
    """
    pol = (polarization or "").strip().upper()
    if pol in ("H", "R"):
        return 18
    if pol in ("V", "L"):
        return 13
    return 13


def tone_for_band(band: str | None) -> bool:
    """Tono de 22 kHz: activo en banda alta."""
    return (band or "low").strip().lower() == "high"


def compute_lnb(
    freq_khz: int,
    band: str | None = None,
    polarization: str | None = None,
    config: LNBConfig | None = None,
) -> LNBSolution:
    """Calcula la conmutación del LNB para un transponder.

    :param freq_khz: frecuencia del transponder en kHz (> 1000, p. ej. 11362000).
    :param band: "low"/"high" según banda; ``None``/``"AUTO"`` lo deduce del umbral.
    :param polarization: "H" | "V" | "L" | "R" (o ``None``).
    :param config: LNB a usar (por defecto, universal 9750/10600/11700).
    :returns: `LNBSolution` con la FI, banda, tono y voltaje.
    """
    config = config or LNBConfig()
    transponder_khz = int(freq_khz)
    if transponder_khz <= 0:
        raise FrontendError(f"Frecuencia de transponder inválida: {freq_khz}")

    if band is None or str(band).strip().upper() in ("", "AUTO"):
        resolved_band = config.band_for(transponder_khz)
    else:
        resolved_band = str(band).strip().lower()
        if resolved_band not in ("low", "high"):
            raise FrontendError(f"Banda de LNB no válida: {band!r}")

    local_oscillator = config.local_oscillator_khz(transponder_khz, resolved_band)
    intermediate = transponder_khz - local_oscillator
    if intermediate <= 0:
        raise FrontendError(
            f"FI no válida ({intermediate} kHz) para {transponder_khz} kHz "
            f"con OL {local_oscillator} kHz"
        )
    return LNBSolution(
        frequency_khz=intermediate,
        transponder_khz=transponder_khz,
        band=resolved_band,
        tone_22k=tone_for_band(resolved_band),
        voltage=voltage_for_polarization(polarization),
        polarization=(polarization or None),
        local_oscillator_khz=local_oscillator,
    )


# ---------------------------------------------------------------------------
# Mensajes DiSEqC
# ---------------------------------------------------------------------------
def diseqc_position_message(position: int, uncommitted: bool = False) -> bytes:
    """Mensaje DiSEqC de 4 bytes para seleccionar una posición.

    * 1.0 (committed): ``E0 10 38 F0..F3``
    * 1.1 (uncommitted): ``E0 10 39 F0..FF``
    """
    position = int(position)
    limit = MAX_POSITION_11 if uncommitted else MAX_POSITION_10
    if not 0 <= position < limit:
        raise ValueError(
            f"Posición DiSEqC fuera de rango ({position}); válido 0..{limit - 1}"
        )
    command = DISEQC_COMMAND_UNCOMMITTED if uncommitted else DISEQC_COMMAND_COMMITTED
    return bytes([DISEQC_ADDRESS_ANY, DISEQC_FRAMING, command, 0xF0 | position])


def diseqc_position_sequence(position: int, uncommitted: bool = False) -> bytes:
    """Secuencia completa: en 1.1 se antepone el comando committed.

    Un conmutador 1.1 real suele necesitar primero un "committed" válido
    (puerto 0) para quedar direccionado y después el "uncommitted".
    """
    if not uncommitted:
        return diseqc_position_message(position, uncommitted=False)
    return diseqc_position_message(0, uncommitted=False) + diseqc_position_message(
        position, uncommitted=True
    )


def diseqc_command(spec: str) -> bytes:
    """Convierte una especificación textual en un mensaje DiSEqC.

    Formato aceptado: ``"1.0:2"``, ``"1.1:5"``, ``"10:3"``, ``"uncommitted:7"``,
    ``"toneburst:A"``/``"burst:B"``. Devuelve los bytes del mensaje (sin
    repeticiones); ``send_diseqc(cmd)`` se encarga del envío.

    Es la función que usa `app/services/device_manager.py` para el campo
    `TuneRequest.diseqc`.
    """
    if not isinstance(spec, str) or not spec.strip():
        raise ValueError("Especificación DiSEqC vacía")
    text = spec.strip().lower().replace(" ", "")
    if ":" not in text:
        raise ValueError(f"Especificación DiSEqC no reconocida: {spec!r}")
    kind, _, raw_position = text.partition(":")
    try:
        position = int(raw_position)
    except ValueError as exc:
        raise ValueError(f"Posición DiSEqC no numérica en {spec!r}") from exc

    if kind in ("1.0", "10", "committed"):
        return diseqc_position_message(position, uncommitted=False)
    if kind in ("1.1", "11", "uncommitted"):
        return diseqc_position_sequence(position, uncommitted=True)
    raise ValueError(f"Versión DiSEqC no soportada: {kind!r}")


# Alias: `device_manager` busca `diseqc_command` o `parse_diseqc`.
parse_diseqc = diseqc_command


# ---------------------------------------------------------------------------
# Aplicación sobre un frontend
# ---------------------------------------------------------------------------
class LnbController:
    """Aplica voltaje, tono y DiSEqC a un frontend (antes de sintonizar)."""

    def __init__(self, config: LNBConfig | None = None) -> None:
        self.config = config or LNBConfig()
        self.last_solution: LNBSolution | None = None
        self.last_diseqc: bytes | None = None

    def apply(self, frontend: Any, solution: LNBSolution) -> None:
        """Voltaje + tono de una `LNBSolution`."""
        self.set_voltage(frontend, solution.voltage)
        self.set_tone(frontend, solution.tone_22k)
        self.last_solution = solution

    def set_voltage(self, frontend: Any, volts: int) -> None:
        if hasattr(frontend, "set_voltage"):
            frontend.set_voltage(int(volts))

    def set_tone(self, frontend: Any, on: bool) -> None:
        if hasattr(frontend, "set_tone"):
            frontend.set_tone(bool(on))

    def select_position(self, frontend: Any, spec: str, repeats: int = 1) -> bytes:
        """Envía una especificación DiSEqC ("1.0:2", "1.1:5")."""
        message = diseqc_command(spec)
        self.send(frontend, message, repeats=repeats)
        return message

    def send(self, frontend: Any, message: bytes, repeats: int = 1) -> None:
        """Envía bytes DiSEqC ya construidos (trocea secuencias 4·N)."""
        if not hasattr(frontend, "send_diseqc"):
            raise FrontendError("El frontend no implementa send_diseqc()")
        for single in split_diseqc_messages(bytes(message)):
            frontend.send_diseqc(single, repeats=max(0, int(repeats)))
            self.last_diseqc = single

    def switch_to(
        self,
        frontend: Any,
        frequency_khz: int,
        polarization: str | None = None,
        band: str | None = None,
        diseqc: str | None = None,
    ) -> LNBSolution:
        """Calcula y aplica toda la conmutación de un transponder.

        Orden correcto: voltaje y tono primero (alimentan el LNB) y después el
        mensaje DiSEqC.
        """
        solution = compute_lnb(frequency_khz, band=band, polarization=polarization, config=self.config)
        self.apply(frontend, solution)
        if diseqc:
            self.select_position(frontend, diseqc)
        return solution
