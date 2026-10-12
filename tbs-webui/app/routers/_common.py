"""Dependencias compartidas por los routers: instancias de servicios y helpers."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status

from ..dvb import registry
from ..dvb.base import DeviceBusyError, FrontendError
from ..dvb.types import TuneParams
from ..services.device_manager import DeviceManager
from ..services.scan_service import ScanService


def get_device_manager(request: Request) -> DeviceManager:
    """Instancia única de DeviceManager creada en el arranque de la app."""
    return request.app.state.device_manager


def get_scan_service(request: Request) -> ScanService:
    """Instancia única del servicio de escaneo."""
    return request.app.state.scan_service


DeviceManagerDep = Annotated[DeviceManager, Depends(get_device_manager)]
ScanServiceDep = Annotated[ScanService, Depends(get_scan_service)]


def to_tune_params(payload: Any) -> TuneParams:
    """Convierte un `TuneRequest`/dict en `TuneParams` del dominio DVB.

    `frequency_khz` se mantiene como frecuencia del transponder; el offset del
    LNB se calcula con `if_frequency_khz` dentro del propio TuneParams.
    """
    if isinstance(payload, TuneParams):
        return payload
    if hasattr(payload, "model_dump"):
        data = payload.model_dump()
    elif isinstance(payload, dict):
        data = dict(payload)
    else:  # pragma: no cover - defensivo
        raise HTTPException(status_code=400, detail="Parámetros de sintonización inválidos")

    data.pop("lnb_threshold_khz_unused", None)
    allowed = set(TuneParams.__dataclass_fields__)
    filtered = {key: value for key, value in data.items() if key in allowed}
    try:
        return TuneParams(**filtered)
    except TypeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Parámetros de sintonización inválidos: {exc}",
        ) from exc


def frontend_error_to_http(exc: FrontendError) -> HTTPException:
    """Traduce errores de la capa DVB a respuestas HTTP coherentes."""
    from ..dvb.base import DeviceNotFoundError, LockTimeoutError, UnsupportedError

    if isinstance(exc, DeviceBusyError):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    if isinstance(exc, DeviceNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, UnsupportedError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if isinstance(exc, LockTimeoutError):
        return HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


def lock_snapshot() -> dict[str, Any]:
    """Estado del dispositivo (dueño, momento de adquisición, cola)."""
    return registry.get_lock().snapshot()


def service_to_out(
    service: Any,
    transponder: Any | None = None,
    fallback_id: int = 0,
    transponder_id: int | None = None,
) -> Any:
    """Convierte un modelo `Service` (BD) o un `ServiceInfo` (aire) a `ServiceOut`."""
    from ..schemas import ServiceOut

    is_model = hasattr(service, "transponder_id")
    if is_model:
        parent = transponder if transponder is not None else getattr(service, "transponder", None)
        return ServiceOut(
            id=int(service.id),
            transponder_id=int(service.transponder_id),
            service_id=int(service.service_id),
            name=service.name or "",
            provider=service.provider or "",
            service_type=int(service.service_type or 0),
            service_type_label=service.service_type_label or "TV",
            is_fta=bool(service.is_fta),
            scrambled=bool(service.scrambled),
            video_pid=service.video_pid,
            audio_pid=service.audio_pid,
            pcr_pid=service.pcr_pid,
            pmt_pid=service.pmt_pid,
            video_codec=service.video_codec,
            audio_codec=service.audio_codec,
            audio_pids=list(service.audio_pids or []),
            video_pids=list(service.video_pids or []),
            ca_system_ids=list(service.ca_system_ids or []),
            delivery_system=getattr(parent, "delivery_system", "") if parent is not None else "",
            frequency_khz=int(getattr(parent, "frequency_khz", 0) or 0) if parent is not None else 0,
            polarization=getattr(parent, "polarization", None) if parent is not None else None,
            symbol_rate_ksps=getattr(parent, "symbol_rate_ksps", None) if parent is not None else None,
            sat_position=getattr(parent, "sat_position", None) if parent is not None else None,
            created_at=service.created_at,
            custom_name=service.custom_name,
            display_name=service.display_name,
        )

    # ServiceInfo del aire (durante un escaneo)
    from datetime import datetime, timezone

    return ServiceOut(
        id=int(fallback_id),
        transponder_id=int(transponder_id if transponder_id is not None else 0),
        service_id=int(getattr(service, "service_id", 0)),
        name=getattr(service, "name", "") or "",
        provider=getattr(service, "provider", "") or "",
        service_type=int(getattr(service, "service_type", 0) or 0),
        service_type_label=getattr(service, "service_type_label", "TV") or "TV",
        is_fta=bool(getattr(service, "is_fta", True)),
        scrambled=bool(getattr(service, "scrambled", False)),
        video_pid=getattr(service, "video_pid", None),
        audio_pid=getattr(service, "audio_pid", None),
        pcr_pid=getattr(service, "pcr_pid", None),
        pmt_pid=getattr(service, "pmt_pid", None),
        video_codec=getattr(service, "video_codec", None),
        audio_codec=getattr(service, "audio_codec", None),
        audio_pids=list(getattr(service, "audio_pids", ()) or ()),
        video_pids=list(getattr(service, "video_pids", ()) or ()),
        ca_system_ids=list(getattr(service, "ca_system_ids", ()) or ()),
        delivery_system=getattr(transponder, "delivery_system", "") if transponder is not None else "",
        frequency_khz=int(getattr(transponder, "frequency_khz", 0) or 0) if transponder is not None else 0,
        polarization=getattr(transponder, "polarization", None) if transponder is not None else None,
        symbol_rate_ksps=getattr(transponder, "symbol_rate_ksps", None) if transponder is not None else None,
        sat_position=getattr(transponder, "sat_position", None) if transponder is not None else None,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        custom_name=None,
        display_name=getattr(service, "name", "") or f"Service {getattr(service, 'service_id', 0)}",
    )
