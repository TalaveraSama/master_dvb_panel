"""Log de la aplicación consultable desde la UI."""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import desc, select

from ..deps import DbSession
from ..models import AppLog
from ..schemas import LogEntryOut, LogListOut

router = APIRouter(tags=["system"])


@router.get("/logs", response_model=LogListOut)
def logs(
    db: DbSession,
    limit: int = Query(default=200, ge=1, le=2000),
    level: str | None = None,
) -> LogListOut:
    """Últimos eventos registrados (avisos y errores por defecto)."""
    stmt = select(AppLog).order_by(desc(AppLog.ts)).limit(limit)
    if level:
        stmt = stmt.where(AppLog.level == level.upper())
    rows = db.execute(stmt).scalars().all()
    return LogListOut(
        items=[
            LogEntryOut(ts=row.ts, level=row.level, source=row.source, message=row.message)
            for row in rows
        ]
    )
