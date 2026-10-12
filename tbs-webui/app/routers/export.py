"""Exportación de canales a M3U y channels.conf (dvbv5)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import PlainTextResponse

from ..deps import DbSession
from ..services import channels_service, exporters

router = APIRouter(prefix="/export", tags=["export"])


def _filters(q: str | None, type: str | None, fta: bool | None, scrambled: bool | None, transponder_id: int | None):
    return {
        "q": q,
        "service_type": type,
        "fta": fta,
        "scrambled": scrambled,
        "transponder_id": transponder_id,
    }


@router.get("/m3u", response_class=PlainTextResponse)
def export_m3u(
    db: DbSession,
    q: str | None = None,
    type: str | None = Query(default=None, description="TV | Radio | Data | HDTV"),
    fta: bool | None = None,
    scrambled: bool | None = None,
    transponder_id: int | None = None,
    radio: bool = Query(default=True, description="Incluir canales de radio"),
) -> PlainTextResponse:
    """Descarga la lista de canales en formato M3U."""
    _, items = channels_service.list_channels(db, limit=2000, **_filters(q, type, fta, scrambled, transponder_id))
    if not items:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No hay canales que exportar")
    body = exporters.to_m3u(items, include_radio=radio)
    return PlainTextResponse(
        content=body,
        media_type="audio/x-mpegurl",
        headers={"Content-Disposition": 'attachment; filename="canales.m3u"'},
    )


@router.get("/channels.conf", response_class=PlainTextResponse)
def export_channels_conf(
    db: DbSession,
    q: str | None = None,
    type: str | None = Query(default=None, description="TV | Radio | Data | HDTV"),
    fta: bool | None = None,
    scrambled: bool | None = None,
    transponder_id: int | None = None,
) -> PlainTextResponse:
    """Descarga los canales en formato channels.conf (dvbv5-zap)."""
    _, items = channels_service.list_channels(db, limit=2000, **_filters(q, type, fta, scrambled, transponder_id))
    if not items:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No hay canales que exportar")

    services_by_transponder: dict[int, list] = {}
    transponders = {}
    for service in items:
        parent = getattr(service, "transponder", None)
        if parent is None:
            continue
        transponders[parent.id] = parent
        services_by_transponder.setdefault(parent.id, []).append(service)

    body = exporters.to_channels_conf(
        sorted(transponders.values(), key=lambda row: row.frequency_khz), services_by_transponder
    )
    return PlainTextResponse(
        content=body,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="channels.conf"'},
    )
