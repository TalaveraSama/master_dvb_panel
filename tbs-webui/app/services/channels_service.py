"""Acceso a canales y transponders en la base de datos.

Incluye la lógica **anti-duplicados**:
- `transponders`: clave natural (sistema + frecuencia + polarización + symbol rate
  + stream_id + plp_id).
- `services`: clave (transponder_id + service_id); al reescanear se actualizan los
  datos pero se conserva el nombre personalizado (`custom_name`).
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Sequence

from sqlalchemy import Select, String, cast, delete, func, or_, select, update
from sqlalchemy.orm import Session, joinedload

from ..dvb.types import ServiceInfo, Transponder
from ..models import Service, Transponder as TransponderModel

logger = logging.getLogger(__name__)

# Campos del transponder que se copian del dominio a la base de datos.
_TRANSPONDER_FIELDS = (
    "polarization",
    "symbol_rate_ksps",
    "fec",
    "modulation",
    "bandwidth_mhz",
    "inversion",
    "rolloff",
    "pilot",
    "stream_id",
    "plp_id",
    "guard_interval",
    "transmission_mode",
    "code_rate_hp",
    "code_rate_lp",
    "hierarchy",
    "sat_position",
)


def transponder_key(domain: Transponder) -> dict[str, Any]:
    """Clave natural de un transponder (para búsquedas exactas)."""
    return {
        "delivery_system": domain.delivery_system,
        "frequency_khz": int(domain.frequency_khz),
        "polarization": domain.polarization,
        "symbol_rate_ksps": domain.symbol_rate_ksps,
        "stream_id": domain.stream_id,
        "plp_id": domain.plp_id,
    }


def get_or_create_transponder(db: Session, domain: Transponder) -> TransponderModel:
    """Busca el transponder por clave natural o lo crea si no existe."""
    key = transponder_key(domain)
    stmt = select(TransponderModel).where(
        TransponderModel.delivery_system == key["delivery_system"],
        TransponderModel.frequency_khz == key["frequency_khz"],
        *[
            (getattr(TransponderModel, field).is_(None) if value is None else getattr(TransponderModel, field) == value)
            for field, value in key.items()
            if field not in ("delivery_system", "frequency_khz")
        ],
    )
    row = db.execute(stmt).scalars().first()
    if row is None:
        row = TransponderModel(**{**_transponder_data(domain)})
        db.add(row)
        db.flush()
        logger.debug("Nuevo transponder %s", row.label if hasattr(row, "label") else key)
    else:
        # Actualizar metadatos que pueden haber cambiado en el escaneo.
        for field, value in _transponder_data(domain).items():
            if value is not None and getattr(row, field) != value:
                setattr(row, field, value)
    db.commit()
    db.refresh(row)
    return row


def _transponder_data(domain: Transponder) -> dict[str, Any]:
    data: dict[str, Any] = {
        "delivery_system": domain.delivery_system,
        "frequency_khz": int(domain.frequency_khz),
    }
    for field in _TRANSPONDER_FIELDS:
        value = getattr(domain, field, None)
        if value is not None:
            data[field] = value
    return data


def upsert_service(
    db: Session,
    transponder_id: int,
    info: ServiceInfo,
    source: str = "scan",
    clear_existing: bool = False,
) -> tuple[Service, bool]:
    """Inserta o actualiza un servicio. Devuelve (servicio, creado).

    Si `clear_existing` es True se borran antes todos los servicios de ese
    transponder (útil para reescaneos limpios).
    """
    if clear_existing:
        db.execute(delete(Service).where(Service.transponder_id == transponder_id))
        db.flush()

    stmt = select(Service).where(
        Service.transponder_id == transponder_id,
        Service.service_id == info.service_id,
    )
    row = db.execute(stmt).scalars().first()
    created = row is None
    if row is None:
        row = Service(transponder_id=transponder_id, service_id=info.service_id)
        db.add(row)

    row.name = info.name or row.name or f"Service {info.service_id}"
    row.provider = info.provider or row.provider or ""
    row.service_type = int(info.service_type)
    row.service_type_label = info.service_type_label
    row.is_fta = bool(info.is_fta)
    row.scrambled = bool(info.scrambled)
    row.video_pid = info.video_pid
    row.audio_pid = info.audio_pid
    row.pcr_pid = info.pcr_pid
    row.pmt_pid = info.pmt_pid
    row.video_codec = info.video_codec
    row.audio_codec = info.audio_codec
    row.audio_pids = list(info.audio_pids or [])
    row.video_pids = list(info.video_pids or [])
    row.ca_system_ids = list(info.ca_system_ids or [])
    row.source = source
    db.commit()
    db.refresh(row)
    return row, created


def save_transponder_services(
    db: Session,
    domain: Transponder,
    services: Iterable[ServiceInfo],
    clear_existing: bool = False,
    signal: dict[str, float | None] | None = None,
) -> tuple[TransponderModel, int, int]:
    """Guarda un transponder y sus servicios.

    Devuelve (transponder, nuevos, actualizados).
    """
    from ..models import utcnow

    row = get_or_create_transponder(db, domain)
    if clear_existing:
        db.execute(delete(Service).where(Service.transponder_id == row.id))
        db.commit()

    created = updated = 0
    for info in services:
        _, was_created = upsert_service(db, row.id, info, source="scan", clear_existing=False)
        if was_created:
            created += 1
        else:
            updated += 1

    if signal:
        if signal.get("signal_percent") is not None:
            row.last_signal_percent = float(signal["signal_percent"])
        if signal.get("snr_db") is not None:
            row.last_snr_db = float(signal["snr_db"])
        if signal.get("locked"):
            row.locked_at = utcnow()
        db.commit()

    return row, created, updated


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------
def _service_query(
    q: str | None = None,
    service_type: str | None = None,
    fta: bool | None = None,
    scrambled: bool | None = None,
    transponder_id: int | None = None,
) -> Select:
    stmt = select(Service).options(joinedload(Service.transponder))
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Service.name.ilike(pattern),
                Service.custom_name.ilike(pattern),
                Service.provider.ilike(pattern),
            )
        )
    if service_type:
        stmt = stmt.where(Service.service_type_label == service_type)
    if fta is not None:
        stmt = stmt.where(Service.is_fta.is_(bool(fta)))
    if scrambled is not None:
        stmt = stmt.where(Service.scrambled.is_(bool(scrambled)))
    if transponder_id is not None:
        stmt = stmt.where(Service.transponder_id == int(transponder_id))
    return stmt


def list_channels(
    db: Session,
    q: str | None = None,
    service_type: str | None = None,
    fta: bool | None = None,
    scrambled: bool | None = None,
    transponder_id: int | None = None,
    limit: int = 200,
    offset: int = 0,
) -> tuple[int, list[Service]]:
    """Lista canales con filtros. Devuelve (total, items)."""
    stmt = _service_query(q, service_type, fta, scrambled, transponder_id)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = int(db.execute(count_stmt).scalar() or 0)

    # Orden: transponder (frecuencia) y luego nombre.
    stmt = (
        stmt.join(TransponderModel, Service.transponder_id == TransponderModel.id)
        .order_by(TransponderModel.frequency_khz.asc(), Service.name.asc())
        .limit(max(1, min(2000, limit)))
        .offset(max(0, offset))
    )
    items = list(db.execute(stmt).scalars().unique().all())
    return total, items


MAX_SQLITE_INT = 9_223_372_036_854_775_807


def is_valid_row_id(value: object) -> bool:
    """True si el id cabe en un entero de 64 bits con signo (límite de SQLite)."""
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return 0 < number <= MAX_SQLITE_INT


def get_channel(db: Session, channel_id: int) -> Service | None:
    if not is_valid_row_id(channel_id):
        return None
    return db.get(Service, channel_id)


def rename_channel(db: Session, channel_id: int, name: str | None = None, custom_name: str | None = None) -> Service | None:
    """Renombra un canal (nombre real o alias personalizado).

    Los ids fuera del rango de SQLite se tratan como "no encontrado" (404) en
    lugar de provocar un OverflowError (500).
    """
    if not is_valid_row_id(channel_id):
        return None
    row = db.get(Service, channel_id)
    if row is None:
        return None
    if name is not None:
        row.name = name.strip()
    if custom_name is not None:
        cleaned = custom_name.strip()
        row.custom_name = cleaned or None
    db.commit()
    db.refresh(row)
    return row


def delete_channels(db: Session, ids: Sequence[int]) -> int:
    """Borra canales por id. Devuelve cuántos se borraron.

    Se filtran los ids fuera del rango de un entero de 64 bits para evitar un
    OverflowError de SQLite (que devolvería un 500 en la API).
    """
    if not ids:
        return 0
    safe: list[int] = []
    for value in ids:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if 0 < number <= 9_223_372_036_854_775_807:
            safe.append(number)
    if not safe:
        return 0
    result = db.execute(delete(Service).where(Service.id.in_(safe)))
    db.commit()
    return int(result.rowcount or 0)


def delete_all_channels(db: Session) -> int:
    result = db.execute(delete(Service))
    db.commit()
    return int(result.rowcount or 0)


def list_transponders(
    db: Session,
    q: str | None = None,
    limit: int = 500,
    offset: int = 0,
) -> tuple[int, list[tuple[TransponderModel, int]]]:
    """Lista transponders con su número de servicios."""
    stmt = select(TransponderModel, func.count(Service.id)).outerjoin(
        Service, Service.transponder_id == TransponderModel.id
    )
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                TransponderModel.sat_position.ilike(pattern),
                TransponderModel.delivery_system.ilike(pattern),
                func.cast(TransponderModel.frequency_khz, __import__("sqlalchemy").String).ilike(pattern),
            )
        )
    stmt = stmt.group_by(TransponderModel.id).order_by(
        TransponderModel.frequency_khz.asc(), TransponderModel.id.asc()
    )

    count_stmt = select(func.count(TransponderModel.id))
    if q:
        count_stmt = count_stmt.where(
            or_(
                TransponderModel.sat_position.ilike(f"%{q.strip()}%"),
                TransponderModel.delivery_system.ilike(f"%{q.strip()}%"),
            )
        )
    total = int(db.execute(count_stmt).scalar() or 0)

    rows = db.execute(stmt.limit(max(1, min(5000, limit))).offset(max(0, offset))).all()
    return total, [(row[0], int(row[1])) for row in rows]


def all_transponders_for_export(db: Session) -> list[TransponderModel]:
    return list(db.execute(select(TransponderModel).order_by(TransponderModel.frequency_khz.asc())).scalars().all())


def get_transponder(db: Session, transponder_id: int) -> TransponderModel | None:
    """Transponder por id (None si no existe o el id no cabe en int64)."""
    if not is_valid_row_id(transponder_id):
        return None
    return db.get(TransponderModel, transponder_id)


def _same_identity(
    db: Session, data: dict[str, Any], exclude_id: int
) -> TransponderModel | None:
    """Busca otro transponder con la misma identidad natural."""
    stmt = select(TransponderModel).where(TransponderModel.id != exclude_id)
    for field in ("delivery_system", "frequency_khz", "polarization", "symbol_rate_ksps", "stream_id", "plp_id"):
        value = data.get(field)
        column = getattr(TransponderModel, field)
        stmt = stmt.where(column.is_(None) if value is None else column == value)
    return db.execute(stmt).scalars().first()


def update_transponder(db: Session, transponder_id: int, changes: dict[str, Any]) -> TransponderModel | None:
    """Edita un transponder. Devuelve None si no existe.

    Lanza `ValueError` si los cambios chocan con otro transponder (identidad
    duplicada); la API lo traduce a 409.
    """
    row = get_transponder(db, transponder_id)
    if row is None:
        return None

    applied = {key: value for key, value in changes.items() if value is not None}
    if not applied:
        return row

    candidate = {
        "delivery_system": applied.get("delivery_system", row.delivery_system),
        "frequency_khz": applied.get("frequency_khz", row.frequency_khz),
        "polarization": applied.get("polarization", row.polarization),
        "symbol_rate_ksps": applied.get("symbol_rate_ksps", row.symbol_rate_ksps),
        "stream_id": applied.get("stream_id", row.stream_id),
        "plp_id": applied.get("plp_id", row.plp_id),
    }
    duplicate = _same_identity(db, candidate, exclude_id=transponder_id)
    if duplicate is not None:
        raise ValueError(
            f"Ya existe el transponder #{duplicate.id} con esa identidad "
            f"({candidate['delivery_system']} {candidate['frequency_khz']} kHz)"
        )

    for key, value in applied.items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    logger.info("Transponder #%s editado: %s", transponder_id, ", ".join(sorted(applied)))
    return row


def delete_transponders(db: Session, ids: Sequence[int]) -> tuple[int, int]:
    """Borra transponders y sus canales. Devuelve (transponders, servicios).

    Los canales se borran explícitamente (además del cascade de la relación) para
    no depender de `PRAGMA foreign_keys`, que SQLite trae desactivado por defecto.
    """
    safe = [int(value) for value in ids if is_valid_row_id(value)]
    if not safe:
        return 0, 0

    services = int(
        db.execute(select(func.count(Service.id)).where(Service.transponder_id.in_(safe))).scalar() or 0
    )
    db.execute(delete(Service).where(Service.transponder_id.in_(safe)))
    db.flush()
    result = db.execute(delete(TransponderModel).where(TransponderModel.id.in_(safe)))
    db.commit()
    return int(result.rowcount or 0), services


def list_frontends_for_tuning(db: Session) -> list[int]:
    """Identificadores de adaptador que tienen transponders guardados."""
    rows = db.execute(select(TransponderModel.id)).scalars().all()
    return sorted({int(value) for value in rows})
