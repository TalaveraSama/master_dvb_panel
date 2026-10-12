"""Ajustes persistentes en base de datos (tabla `settings`).

Los valores por defecto vienen de `app.config.Settings`; lo que se edita desde
la UI se guarda como JSON en clave `app.settings`.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..config import Settings
from ..models import AppSetting

SETTINGS_KEY = "app.settings"

# Estructura por defecto (coincide con schemas.SettingsOut)
_DEFAULTS: dict[str, Any] = {
    "default_adapter": 0,
    "default_frontend": 0,
    "lnb": {
        "low_khz": 9750,
        "high_khz": 10600,
        "threshold_khz": 11700,
        "tone_22k": False,
        "voltage": "13V",
    },
    "scan": {
        "lock_timeout_s": 5.0,
        "dwell_seconds": 8.0,
        "lock_timeout_ms": 500,
    },
    "ui": {
        "signal_poll_ms": 500,
        "history_seconds": 60,
    },
}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Mezcla recursiva de diccionarios (override gana)."""
    result = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        elif value is not None:
            result[key] = value
    return result


def default_settings(settings: Settings | None = None) -> dict[str, Any]:
    """Valores por defecto combinados con los del entorno."""
    base = _merge({}, _DEFAULTS)
    if settings is not None:
        base["default_adapter"] = settings.default_adapter
        base["default_frontend"] = settings.default_frontend
        base["lnb"].update(
            {
                "low_khz": settings.lnb_low_khz,
                "high_khz": settings.lnb_high_khz,
                "threshold_khz": settings.lnb_threshold_khz,
            }
        )
        base["scan"].update(
            {
                "lock_timeout_s": settings.lock_timeout_s,
                "lock_timeout_ms": max(50, int(settings.signal_poll_ms)),
            }
        )
        base["ui"].update(
            {
                "signal_poll_ms": settings.signal_poll_ms,
                "history_seconds": settings.history_seconds,
            }
        )
    return base


def load_settings(db: Session, settings: Settings | None = None) -> dict[str, Any]:
    """Lee los ajustes guardados mezclados con los valores por defecto."""
    base = default_settings(settings)
    row = db.get(AppSetting, SETTINGS_KEY)
    if row is None or not isinstance(row.value, dict):
        return base
    return _merge(base, row.value)


def save_settings(db: Session, changes: dict[str, Any]) -> dict[str, Any]:
    """Aplica cambios parciales y devuelve el resultado completo."""
    current = load_settings(db)
    updated = _merge(current, changes)
    row = db.get(AppSetting, SETTINGS_KEY)
    if row is None:
        row = AppSetting(key=SETTINGS_KEY, value=updated)
        db.add(row)
    else:
        row.value = updated
    db.commit()
    return updated


def reset_settings(db: Session) -> dict[str, Any]:
    """Restaura los valores por defecto."""
    row = db.get(AppSetting, SETTINGS_KEY)
    if row is not None:
        db.delete(row)
        db.commit()
    return default_settings()
