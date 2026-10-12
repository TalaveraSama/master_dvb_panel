"""Exportación de canales a M3U y a `channels.conf` (formato dvbv5).

- **M3U**: lista de reproducción estándar. Como el panel no sirve el TS en
  directo, cada entrada lleva la URL del *stream* (`#EXTVLCOPT` con los datos de
  sintonía no es suficiente para VLC), por lo que se incluye una URL
  `dvb://` informativa más metadatos (`tvg-*`, `#EXTINF`). Así la lista se puede
  reutilizar en herramientas que entiendan DVB o editar a mano.
- **channels.conf**: formato de `dvbv5-zap`/`dvbv5-scan`:
  `nombre:frecuencia:symbolrate:polaridad:...:SID:...` y la línea de VLC al final
  del fichero para `dvbv5-zap -c` (modo `--channels`).
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Sequence

logger = logging.getLogger(__name__)

DVBV5_HEADER = (
    "# channels.conf generado por TBS WebUI\n"
    "# Formato: nombre:frecuencia(Hz):symbol_rate:sistema_innermost:...\n"
    "# Úsalo con: dvbv5-zap -a 0 -c channels.conf -c <canal> -P\n"
)

# Mapa de FEC al formato de texto de dvbv5
FEC_LABELS = {
    "1/2": "1/2",
    "2/3": "2/3",
    "3/4": "3/4",
    "5/6": "5/6",
    "7/8": "7/8",
    "4/5": "4/5",
    "8/9": "8/9",
    "9/10": "9/10",
    "AUTO": "AUTO",
    "NONE": "NONE",
}


def _fec(value: str | None) -> str:
    if not value:
        return "AUTO"
    return FEC_LABELS.get(value.upper(), value.upper())


def to_m3u(
    services: Sequence[Any],
    base_url: str = "",
    include_radio: bool = True,
) -> str:
    """Genera la lista M3U a partir de los canales (modelos `Service`)."""
    lines = ['#EXTM3U', f"# Generado por TBS WebUI — {len(services)} canales"]
    channel_number = 0
    for service in services:
        label = getattr(service, "service_type_label", "TV")
        if label == "Radio" and not include_radio:
            continue
        channel_number += 1
        name = _sanitize(getattr(service, "display_name", None) or getattr(service, "name", "") or "Canal")
        transponder = getattr(service, "transponder", None)
        freq_khz = getattr(transponder, "frequency_khz", 0) or 0
        pol = getattr(transponder, "polarization", None) or getattr(transponder, "polarity", None) or ""
        sr = getattr(transponder, "symbol_rate_ksps", None) or 0
        system = getattr(transponder, "delivery_system", "DVB-S2") or "DVB-S2"
        provider = getattr(service, "provider", "") or ""
        sid = getattr(service, "service_id", 0)
        vpid = getattr(service, "video_pid", None)
        apid = getattr(service, "audio_pid", None)
        ca = getattr(service, "ca_system_ids", None) or []
        is_fta = getattr(service, "is_fta", True)

        tvg_id = f"{system}-{freq_khz}-{sid}"
        group = provider or getattr(transponder, "sat_position", "") or "DVB"
        duration = -1
        attrs = [
            f'tvg-id="{tvg_id}"',
            f'tvg-name="{_escape_attr(name)}"',
            f'tvg-chno="{channel_number}"',
            f'group-title="{_escape_attr(group)}"',
        ]
        lines.append(f'#EXTINF:{duration} {" ".join(attrs)},{name}')
        # Metadatos DVB (no estándar en M3U, pero inocuos y útiles)
        lines.append(
            f"#TBSEXTINFO:system={system};freq_khz={freq_khz};pol={pol};sr_ksps={sr};"
            f"sid={sid};vpid={vpid if vpid is not None else '-'};apid={apid if apid is not None else '-'};"
            f"pmt={getattr(service, 'pmt_pid', None)};scrambled={'yes' if ca else 'no'};fta={'yes' if is_fta else 'no'}"
        )
        if base_url:
            url = (
                f"{base_url.rstrip('/')}/stream/{getattr(service, 'id', sid)}"
            )
        else:
            url = f"dvb://{system}/{freq_khz}{pol}/{sr}/{sid}"
        lines.append(url)
    lines.append("")
    return "\n".join(lines)


def to_channels_conf(transponders: Iterable[Any], services_by_transponder: dict[int, list[Any]] | None = None) -> str:
    """Genera `channels.conf` en formato dvbv5.

    Si se pasan servicios por transponder, se genera una línea por servicio
    (formato de canal); si no, una línea por transponder.
    """
    services_by_transponder = services_by_transponder or {}
    lines = [DVBV5_HEADER.rstrip("\n")]
    for transponder in transponders:
        t_id = getattr(transponder, "id", None)
        freq_hz = int(getattr(transponder, "frequency_khz", 0) or 0) * 1000
        sr = getattr(transponder, "symbol_rate_ksps", None) or 0
        pol = getattr(transponder, "polarization", None) or getattr(transponder, "polarity", None) or "H"
        system = (getattr(transponder, "delivery_system", "DVB-S2") or "DVB-S2").replace("-", "")
        fec = _fec(getattr(transponder, "fec", None))
        modulation = (getattr(transponder, "modulation", None) or "AUTO").upper()
        rolloff = getattr(transponder, "rolloff", None) or "0.35"
        sat = getattr(transponder, "sat_position", None) or ""
        services = services_by_transponder.get(t_id, []) if t_id is not None else []

        if services:
            for service in services:
                name = _sanitize(
                    getattr(service, "display_name", None) or getattr(service, "name", "") or "Canal"
                )
                sid = getattr(service, "service_id", 0)
                vpid = getattr(service, "video_pid", None) or 0
                apid = getattr(service, "audio_pid", None) or 0
                lines.append(
                    ":".join(
                        [
                            name,
                            str(freq_hz),
                            f"{system.upper()}",
                            f"sr={sr}",
                            f"fec={fec}",
                            f"pol={pol}",
                            f"mod={modulation}",
                            f"rolloff={rolloff}",
                            f"vpid={vpid}",
                            f"apid={apid}",
                            f"sid={sid}",
                            "tune=auto",
                        ]
                    )
                )
        else:
            name = _sanitize(f"{system} {freq_hz // 1000} {pol}".strip())
            lines.append(
                ":".join(
                    [
                        name,
                        str(freq_hz),
                        system.upper(),
                        f"sr={sr}",
                        f"fec={fec}",
                        f"pol={pol}",
                        f"mod={modulation}",
                        f"rolloff={rolloff}",
                        f"sat={sat}" if sat else "tune=auto",
                    ]
                )
            )
    lines.append("")
    return "\n".join(lines)


def _sanitize(value: str) -> str:
    """Quita separadores problemáticos del nombre del canal."""
    cleaned = (value or "").replace(":", " ").replace("\n", " ").replace("\r", " ").strip()
    return cleaned or "Canal"


def _escape_attr(value: str) -> str:
    return (value or "").replace('"', "'")
