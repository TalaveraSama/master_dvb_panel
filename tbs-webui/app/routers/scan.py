"""Escaneo de mux/transponders: arranque, progreso, log y parada."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status

from ..dvb.base import DeviceBusyError, FrontendError
from ..schemas import (
    OkOut,
    ScanLogEntry,
    ScanProgressOut,
    ScanStartOut,
    ScanStartRequest,
    ScanStatusOut,
    ScanTablesOut,
    ScanTableOut,
)
from ._common import ScanServiceDep, service_to_out

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scan", tags=["scan"])


@router.get("/tables", response_model=ScanTablesOut)
def list_tables(scan: ScanServiceDep) -> ScanTablesOut:
    """Tablas de transponders disponibles (dtv-scan-tables)."""
    bundles = scan.list_tables()
    directory = str(scan.settings.scan_tables_dir)
    return ScanTablesOut(
        bundles=[ScanTableOut(**bundle) for bundle in bundles],
        dir=directory,
    )


@router.post("/start", response_model=ScanStartOut)
def start(payload: ScanStartRequest, scan: ScanServiceDep) -> ScanStartOut:
    """Inicia un escaneo en segundo plano (uno a la vez)."""
    if scan.is_running():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya hay un escaneo en curso. Deténlo antes de iniciar otro.",
        )
    try:
        job_id = scan.start(payload)
    except DeviceBusyError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except FrontendError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return ScanStartOut(ok=True, job_id=job_id)


@router.get("/status", response_model=ScanStatusOut)
def scan_status(scan: ScanServiceDep) -> ScanStatusOut:
    """Progreso, log y servicios encontrados del escaneo actual/último."""
    progress = scan.progress
    pairs = scan.services_with_transponder()
    items = [
        service_to_out(info, transponder=transponder, fallback_id=index)
        for index, (info, transponder) in enumerate(pairs, start=1)
    ]
    return ScanStatusOut(
        progress=ScanProgressOut(**scan.progress_dict()),
        log=[ScanLogEntry(**entry) for entry in scan.log()[-200:]],
        services=items,
    )


@router.get("/log", response_model=list[ScanLogEntry])
def scan_log(scan: ScanServiceDep, since: float | None = None) -> list[ScanLogEntry]:
    """Entradas del log de escaneo posteriores a `since` (epoch)."""
    return [ScanLogEntry(**entry) for entry in scan.log(since)]


@router.post("/stop", response_model=OkOut)
def stop(scan: ScanServiceDep) -> OkOut:
    """Solicita la parada del escaneo en curso."""
    if not scan.stop():
        return OkOut(ok=True, detail="No había ningún escaneo en curso")
    return OkOut(ok=True, detail="Parada solicitada")
