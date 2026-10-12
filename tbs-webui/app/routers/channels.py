"""Canales guardados: listado con filtros, renombrado y borrado."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..deps import DbSession
from ..schemas import (
    ChannelListOut,
    ChannelPatch,
    OkOut,
    ServiceOut,
    TransponderListOut,
    TransponderOut,
)
from ..services import channels_service
from ._common import service_to_out

logger = logging.getLogger(__name__)

router = APIRouter(tags=["channels"])


@router.get("/channels", response_model=ChannelListOut)
def list_channels(
    db: DbSession,
    q: str | None = Query(default=None, description="Búsqueda por nombre/proveedor"),
    type: str | None = Query(default=None, description="TV | Radio | Data | HDTV"),
    fta: bool | None = Query(default=None),
    scrambled: bool | None = Query(default=None),
    transponder_id: int | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> ChannelListOut:
    """Lista de canales guardados con búsqueda y filtros."""
    total, items = channels_service.list_channels(
        db,
        q=q,
        service_type=type,
        fta=fta,
        scrambled=scrambled,
        transponder_id=transponder_id,
        limit=limit,
        offset=offset,
    )
    return ChannelListOut(total=total, items=[service_to_out(item) for item in items])


@router.patch("/channels/{channel_id}", response_model=ServiceOut)
def patch_channel(channel_id: int, payload: ChannelPatch, db: DbSession) -> ServiceOut:
    """Renombra un canal (nombre real o alias personalizado)."""
    row = channels_service.rename_channel(
        db, channel_id, name=payload.name, custom_name=payload.custom_name
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Canal no encontrado")
    return service_to_out(row)


@router.delete("/channels/{channel_id}", response_model=OkOut)
def delete_channel(channel_id: int, db: DbSession) -> OkOut:
    """Borra un canal."""
    deleted = channels_service.delete_channels(db, [channel_id])
    if deleted == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Canal no encontrado")
    return OkOut(ok=True, detail=f"{deleted} canal(es) borrado(s)")


@router.delete("/channels", response_model=OkOut)
def delete_channels(
    db: DbSession,
    ids: str | None = Query(default=None, description="Lista de ids separada por comas"),
    payload: dict | None = Body(default=None),
) -> OkOut:
    """Borra varios canales o todos (`{"all": true}`)."""
    if payload and payload.get("all"):
        count = channels_service.delete_all_channels(db)
        return OkOut(ok=True, detail=f"{count} canal(es) borrado(s)")
    parsed: list[int] = []
    if ids:
        try:
            parsed = [int(part) for part in ids.split(",") if part.strip()]
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ids inválidos") from exc
    if payload and payload.get("ids"):
        parsed.extend(int(value) for value in payload["ids"])
    if not parsed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Indica ?ids=1,2 o {\"all\": true}",
        )
    count = channels_service.delete_channels(db, parsed)
    return OkOut(ok=True, detail=f"{count} canal(es) borrado(s)")


@router.get("/transponders", response_model=TransponderListOut)
def list_transponders(
    db: DbSession,
    q: str | None = None,
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
) -> TransponderListOut:
    """Transponders guardados con su número de servicios."""
    total, rows = channels_service.list_transponders(db, q=q, limit=limit, offset=offset)
    items = []
    for row, services_count in rows:
        items.append(
            TransponderOut(
                id=row.id,
                delivery_system=row.delivery_system,
                frequency_khz=row.frequency_khz,
                polarization=row.polarization,
                symbol_rate_ksps=row.symbol_rate_ksps,
                fec=row.fec,
                modulation=row.modulation,
                bandwidth_mhz=row.bandwidth_mhz,
                sat_position=row.sat_position,
                last_signal_percent=row.last_signal_percent,
                last_snr_db=row.last_snr_db,
                label=row.label,
                services_count=services_count,
                updated_at=row.updated_at,
            )
        )
    return TransponderListOut(total=total, items=items)
