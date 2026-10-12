# Contratos de interfaz (CONGELADOS)

> Este archivo es la única fuente de verdad para los límites entre módulos.
> No cambiar sin avisar al Lead. Cualquier módulo puede importar `dvb.base`
> y `dvb.types` libremente: son hojas sin dependencias internas.

## 1. Estructura de ficheros y propiedad

```
tbs-webui/
  app/
    __init__.py
    main.py              # [Lead] FastAPI app, routers, estáticos, lifespan
    config.py            # [Lead] Settings + variables de entorno
    database.py          # [Lead] engine/session/init_db
    models.py            # [Lead] tablas SQLAlchemy
    schemas.py           # [Lead] pydantic (entrada/salida API)
    security.py          # [Lead] argon2, sesiones firmadas, CSRF, rate-limit
    deps.py              # [Lead] dependencias FastAPI (sesión, DB)
    routers/
      __init__.py        # [Lead]
      auth.py            # [Lead]
      adapters.py        # [Lead]
      tune.py            # [Lead]
      status.py          # [Lead]
      scan.py            # [Lead]
      channels.py        # [Lead]
      export.py          # [Lead]
      settings.py        # [Lead]
    services/
      __init__.py        # [Lead]
      device_manager.py  # [Lead]   (usa dvb.*)
      scan_service.py    # [Lead]   (usa dvb.*)
      exporters.py       # [Lead]   (M3U + channels.conf)
      _interfaces.py     # [Lead]   Protocolos que dvb.* debe satisfacer
    dvb/                 # [Teammate: dvb-dev]
      __init__.py
      types.py           # dataclasses puras, SIN dependencias
      base.py            # Frontend protocol + errores
      frontend_ioctl.py  # implementación Linux real (ioctl)
      mock.py            # simulación
      tools.py           # wrapper dvbv5-scan/dvbv5-zap/dvb-fe-tool
      diseqc.py          # LNB/tone/voltage/DiSEqC
      satxml.py          # parser de dvb-scan-tables (XML)
      si.py              # parser PAT/SDT/PMT
      registry.py        # discovery + factory + lock global
    web/                 # [Teammate: frontend-dev]
      index.html
      login.html
      css/styles.css
      js/*.js
      vendor/*.js        # Alpine.js local, SIN CDN
  tests/                 # [Lead] + aportaciones de cada owner
  deploy/                # [Teammate: docs-ops]
    tbs-webui.service
    nginx-tbs-webui.conf
    install.sh
  README.md              # [Teammate: docs-ops]
  requirements.txt       # [Lead]
  .env.example           # [Lead]
  run.sh                 # [Lead]
```

## 2. `app/dvb/types.py` (hoja pura)

```python
@dataclass(frozen=True)
class DeliverySystem(str, Enum):  # valores = strings de la API
    DVB_S = "DVB-S"; DVB_S2 = "DVB-S2"; DVB_T = "DVB-T"; DVB_T2 = "DVB-T2"
    DVB_C = "DVB-C"; DVB_C2 = "DVB-C2"; ISDB_T = "ISDB-T"; ATSC = "ATSC"

@dataclass(frozen=True)
class FrontendInfo:
    adapter: int; frontend: int; name: str
    device_path: str                      # /dev/dvb/adapter0/frontend0
    type: str                             # FE_QPSK, FE_OFDM, ...
    caps: int
    delivery_systems: tuple[str, ...]     # ["DVB-S","DVB-S2"]
    min_frequency: int; max_frequency: int
    symbol_rates: tuple[int, ...]         # vacío si N/A

@dataclass(frozen=True)
class TuneParams:                         # entrada de POST /api/tune / WS tune
    delivery_system: str
    frequency_khz: int                    # frecuencia del TRANSPONDER en kHz (LNB sin restar)
    symbol_rate_ksps: int | None = None   # DVB-S/S2/C
    polarization: str | None = None       # "H" | "V" | "L" | "R" | None
    fec: str | None = None                # "1/2".."9/10" | "AUTO" | None
    modulation: str | None = None         # "QPSK","8PSK","QAM64",... | "AUTO"
    bandwidth_mhz: int | None = None      # DVB-T/T2
    code_rate_hp: str | None = None; code_rate_lp: str | None = None
    guard_interval: str | None = None; transmission_mode: str | None = None
    hierarchy: str | None = None; inversion: str = "AUTO"
    rolloff: str | None = None            # "0.20","0.25","0.35"
    pilot: str | None = None
    stream_id: int | None = None; plp_id: int | None = None
    is_multi_stream: bool = False
    # --- Ampliación aprobada por el Lead (necesaria para LNB/DiSEqC) ---
    adapter: int = 0; frontend: int = 0
    use_lnb: bool = True
    # Los valores de LNB se expresan como en la práctica: 9750 / 10600 / 11700
    # (MHz de un LNB universal). El helper normaliza a kHz internamente, así que
    # también acepta 9750000.
    lnb_low_khz: int = 9750; lnb_high_khz: int = 10600; lnb_threshold_khz: int = 11700
    tone_22k: bool | None = None          # None = automático (banda alta)
    voltage: str | None = None            # "13V" | "18V" | "OFF" | None (automático)
    diseqc: str | None = None             # "1.0:0".."1.0:3", "1.1:0".."1.1:15"
    lock_timeout_s: float = 5.0
    @property
    def lnb_offset_khz(self) -> int: ...          # 0 si no hay LNB
    @property
    def if_frequency_khz(self) -> int: ...        # frequency_khz - lnb_offset_khz (en kHz)
    def lnb_band(self) -> str: ...                # "low" | "high"
    def needed_tone(self) -> bool: ...
    def needed_voltage(self) -> str: ...          # "13V" | "18V" | "OFF"
```

**Convención de unidades (importante):**
- `frequency_khz`, `symbol_rate_ksps`, `if_frequency_khz` -> **kHz**.
- `lnb_*_khz` -> etiqueta heredada: el valor típico (`9750`, `10600`, `11700`)
  se interpreta como MHz y se normaliza a kHz con `_lnb_value_khz`.
- Voltaje automático: **H/R -> 18 V**, **V/L -> 13 V** (convención universal).
- Tono 22 kHz automático: banda alta (`frecuencia >= umbral`).

> `MockFrontend.tune()` debe ser tolerante a cualquier subconjunto de estos
> campos (TuneParams para mock/tests se puede construir sólo con
> delivery_system + frequency_khz).

@dataclass(frozen=True)
class LockFlags:
    signal: bool; carrier: bool; viterbi: bool; sync: bool; lock: bool
    @property
    def is_locked(self) -> bool: ...      # lock and signal

@dataclass(frozen=True)
class SignalStats:
    timestamp: float                      # time.time()
    lock: LockFlags
    signal_percent: float | None          # 0..100 (DTV_STAT_SIGNAL_STRENGTH)
    signal_dbm: float | None
    snr_db: float | None                  # DTV_STAT_CNR en dB
    cnr_raw: float | None
    ber: float | None                     # post-BER (escala 0..1)
    ber_pre: float | None
    per: float | None
    ucblocks: int | None
    quality_percent: float | None         # derivado de snr/ber, 0..100
    error: str | None = None

@dataclass(frozen=True)
class ServiceInfo:
    service_id: int; name: str; provider: str
    service_type: int; service_type_label: str   # "TV" | "Radio" | "Data" | "HDTV"
    is_fta: bool; scrambled: bool
    video_pid: int | None; audio_pid: int | None; pcr_pid: int | None
    video_codec: str | None; audio_codec: str | None
    pmt_pid: int | None; ca_system_ids: tuple[int, ...] = ()
    audio_pids: tuple[int, ...] = (); video_pids: tuple[int, ...] = ()

@dataclass(frozen=True)
class Transponder:
    delivery_system: str; frequency_khz: int
    symbol_rate_ksps: int | None = None; polarization: str | None = None
    fec: str | None = None; modulation: str | None = None
    bandwidth_mhz: int | None = None; inversion: str = "AUTO"
    rolloff: str | None = None; pilot: str | None = None
    stream_id: int | None = None; plp_id: int | None = None
    sat_position: str | None = None
    # -> .to_tune_params() -> TuneParams

@dataclass(frozen=True)
class ScanProgress:
    state: str            # "idle"|"running"|"stopping"|"done"|"error"|"cancelled"
    total: int; current: int; percent: float
    current_transponder: str | None
    started_at: float | None; finished_at: float | None
    services_found: int; transponders_locked: int; transponders_scanned: int
    errors: tuple[str, ...] = ()
    message: str = ""

class FrontendError(Exception): ...            # base
class DeviceNotFoundError(FrontendError): ...
class DeviceBusyError(FrontendError): ...      # EBUSY / EAGAIN
class UnsupportedError(FrontendError): ...
class LockTimeoutError(FrontendError): ...
class DvbError(FrontendError): ...
```

## 3. `app/dvb/base.py`

```python
class Frontend(Protocol):
    info: FrontendInfo
    is_mock: bool
    def open(self) -> None: ...
    def close(self) -> None: ...
    def tune(self, params: TuneParams) -> None: ...   # no bloquea; ver wait_lock
    def wait_lock(self, timeout_s: float = 5.0) -> bool: ...
    def read_status(self) -> LockFlags: ...
    def read_stats(self) -> SignalStats: ...
    def set_tone(self, on: bool) -> None: ...
    def set_voltage(self, volts: int) -> None: ...     # 13 | 18 | 0
    def send_diseqc(self, cmd: bytes, repeats: int = 0) -> None: ...
    def close(self) -> None: ...
```

### 3.1 Extensión `TSReader` (opcional, para leer tablas SI del demux)

Si el frontend la implementa, `app/services/si_collector.py` la usa para
descubrir servicios reales (PAT/SDT/PMT). Si no, el escaneo cae a
`dvbv5-scan` y, en modo mock, a servicios simulados.

```python
class TSReader(Protocol):
    demux_path: str | None                  # /dev/dvb/adapterN/demux0
    def set_pid_list(self, pids: list[int]) -> None: ...   # DMX_SET_PES_FILTER
    def read_ts(self, timeout: float = 0.2, max_bytes: int = 188 * 64) -> bytes: ...
    # Mock: def mock_services(self, transponder: Transponder) -> list[ServiceInfo]
```

### 3.2 API requerida de `app/dvb/si.py`

```python
@dataclass
class Section:
    pid: int
    table_id: int
    table_id_ext: int
    version: int
    data: bytes
    # ayudantes: pmt_map() -> {service_id: pmt_pid} para la PAT;
    #            services() -> [(service_id, name, provider, service_type)] para la SDT

class SectionAssembler:
    def __init__(self, pid: int) -> None: ...
    def feed(self, payload: bytes, payload_unit_start: bool = False) -> Section | None: ...

def mpeg_crc32(data: bytes) -> int: ...
def pmt_pids_from_pat(pat_sections: list[Section]) -> set[int]: ...
def build_services(
    pat_sections: list[Section],
    sdt_sections: list[Section],
    pmt_sections: dict[int, list[Section]],
) -> list[ServiceInfo]: ...

def parse_pat(section: Section) -> dict[int, int]: ...      # service_id -> pmt_pid
def parse_sdt(section: Section) -> list[tuple[int, str, str, int]]: ...
def parse_pmt(section: Section) -> ServiceInfo: ...
```

### 3.3 Helpers requeridos en otros módulos `app/dvb`

```python
# dvb/diseqc.py
def diseqc_command(spec: str) -> bytes | None: ...   # "1.0:0".."1.0:3", "1.1:0".."1.1:15"

# dvb/satxml.py
def parse_tables_file(path: Path) -> list[Transponder]: ...
def list_tables(base_dir: Path = Path("/usr/share/dvb")) -> list[dict]: ...  # {name, sat_position, transponders, path}

# dvb/tools.py
def scan_transponder_services(info: FrontendInfo, transponder: Transponder, timeout: float = 15.0) -> list[ServiceInfo]: ...
def parse_channels_conf(text: str) -> list[Transponder]: ...
def dvbv5_scan_to_channels(...) -> ...; def dvb_fe_tool_stats(adapter: int) -> SignalStats: ...

# dvb/mock.py  -> class MockFrontend con .mock_services(transponder) -> list[ServiceInfo]
```

## 4. `app/dvb/registry.py`

```python
class DeviceLock:               # threading.RLock + estado de ocupación
    def acquire(self, owner: str, timeout: float | None = None) -> None  # DeviceBusyError
    def release(self, owner: str) -> None
    @property
    def owner(self) -> str | None
    def snapshot(self) -> dict          # {"owner":..,"acquired_at":..,"waiting":n}

def list_frontends(force_mock: bool | None = None) -> list[FrontendInfo]
def open_frontend(adapter: int, frontend: int = 0) -> Frontend
def get_lock() -> DeviceLock
def is_mock_mode() -> bool          # TBS_MOCK=1 o ausencia de /dev/dvb
```

## 5. API HTTP (contrato exacto para el frontend)

Base: `/api`. Todas las respuestas de error: `{"detail": "..."}`.
Autenticación por cookie de sesión. Mutaciones requieren cabecera
`X-CSRF-Token` con el valor de `GET /api/auth/me`.

| Método | Ruta | Cuerpo / query | Respuesta |
|---|---|---|---|
| GET  | `/api/health` | – | `{"status":"ok","mock":bool,"version":str}` (público) |
| GET  | `/api/auth/me` | – | `{"authenticated":bool,"username":str|null,"csrf_token":str|null,"must_setup":bool}` |
| POST | `/api/auth/setup` | `{username,password}` | `{ok:true}` — 409 si ya hay usuarios |
| POST | `/api/auth/login` | `{username,password}` | `{ok:true,username,csrf_token}` 401/423 |
| POST | `/api/auth/logout` | – | `{ok:true}` |
| POST | `/api/auth/password` | `{current_password,new_password}` | `{ok:true}` |
| GET  | `/api/adapters` | – | `{"mock":bool,"locked":{"owner","acquired_at","waiting"},"adapters":[AdapterOut]}` |
| GET  | `/api/adapters/{a}/{f}/info` | – | `FrontendInfoOut` |
| POST | `/api/tune` | `TuneRequest` | `{ok,locked,stats,tuned:{...}}` |
| POST | `/api/tune/release` | – | `{ok:true}` |
| GET  | `/api/status` | – | `{"locked_by":str|null,"mock":bool,"stats":SignalStatsOut|null,"tuned":TuneRequest|null}` |
| GET  | `/api/scan/tables` | – | `{"bundles":[{"name","sat_position","transponders":n}]}` |
| POST | `/api/scan/start` | `{"adapters":[{"adapter","frontend"}],"transponders":[TransponderIn] \| null,"table":"nombre"\|null,"current_only":bool,"clear_existing":bool,"lock_timeout_s":float}` | `{"ok":true,"job_id":str}` 409 si ocupado |
| GET  | `/api/scan/status` | – | `{"progress":ScanProgressOut,"log":[{"ts","level","msg"}],"services":[ServiceOut]}` |
| GET  | `/api/scan/log` | `?since=float` | `{"log":[...]}` |
| POST | `/api/scan/stop` | – | `{ok:true}` |
| GET  | `/api/channels` | `?q=&type=&fta=&transponder_id=&limit=&offset=` | `{"total":n,"items":[ChannelOut]}` |
| PATCH| `/api/channels/{id}` | `{"name":str}` | `ChannelOut` |
| DELETE| `/api/channels/{id}` | – | `{ok:true}` |
| DELETE| `/api/channels` | `?ids=1,2,3` o `{"all":true}` | `{deleted:n}` |
| GET  | `/api/transponders` | `?q=` | `{"total":n,"items":[TransponderOut]}` |
| GET  | `/api/export/m3u` | `?type=&fta=&q=` | `text/plain` adjunto `canales.m3u` |
| GET  | `/api/export/channels.conf` | idem | `text/plain` adjunto `channels.conf` |
| GET  | `/api/settings` | – | `SettingsOut` |
| PUT  | `/api/settings` | `SettingsIn` | `SettingsOut` |
| GET  | `/api/logs` | `?limit=` | `{"items":[...]}` |

### Objetos de salida (claves JSON exactas)

```jsonc
AdapterOut   = {"adapter":0,"frontend":0,"name":"TBS 6983","device_path":"/dev/dvb/adapter0/frontend0",
                "type":"FE_QPSK","delivery_systems":["DVB-S","DVB-S2"],
                "min_frequency":950000,"max_frequency":2150000,"mock":false}
SignalStatsOut = {"timestamp":1.0,"locked":true,"signal_percent":78.0,"signal_dbm":-42.5,
                  "snr_db":12.4,"ber":0.0,"ber_pre":0.0,"per":0.0,"ucblocks":0,
                  "quality_percent":82.0,"cnr_raw":12.4,"error":null,
                  "flags":{"signal":true,"carrier":true,"viterbi":true,"sync":true,"lock":true}}
ServiceOut   = {"id":1,"transponder_id":3,"service_id":101,"name":"Canal","provider":"Prov",
                "service_type":1,"service_type_label":"TV","is_fta":true,"scrambled":false,
                "video_pid":512,"audio_pid":650,"pcr_pid":512,"video_codec":"MPEG-2",
                "audio_codec":"MPEG-1","pmt_pid":100,"ca_system_ids":[],
                "delivery_system":"DVB-S2","frequency_khz":11096000,"polarization":"V",
                "symbol_rate_ksps":27500,"created_at":"2026-01-01T00:00:00",
                "custom_name":null,"display_name":"Canal"}
TransponderOut = {"id":3,"delivery_system":"DVB-S2","frequency_khz":11096000,"polarization":"V",
                  "symbol_rate_ksps":27500,"fec":"3/4","modulation":"8PSK","bandwidth_mhz":null,
                  "sat_position":"19.2E","services_count":12}
SettingsOut  = {"default_adapter":0,"default_frontend":0,"mock":false,
                "lnb":{"low_khz":9750,"high_khz":10600,"threshold_khz":11700,
                       "tone_22k":false,"voltage":"13V"},
                "scan":{"lock_timeout_s":5.0,"lock_timeout_ms":500},
                "ui":{"signal_poll_ms":500,"history_seconds":60}}
```

### WebSocket `/ws/signal`

- Requiere cookie de sesión válida; si no, **acepta y cierra con código `4401`**
  (sin `Origin` válido cierra con `4403`).
- Cada mensaje del cliente: `{"type":"subscribe"}` / `{"type":"tune", ...TuneRequest}` /
  `{"type":"release"}` / `{"type":"ping"}`.
- Servidor: `{"type":"stats", ...SignalStatsOut, "history":[{"t":float,"snr":float|null,"signal":float|null,"ber":float|null}]}`
  cada `ui.signal_poll_ms` (por defecto 500 ms).
- `{"type":"tuned","locked":bool,...}`, `{"type":"released"}`, `{"type":"error","detail":"..."}`,
  `{"type":"pong"}`.
- La historia se mantiene en memoria (máx. últimos `ui.history_seconds`).

### Notas de implementación (código real != ejemplo inicial)

- **No existe 429**: el límite de intentos devuelve **423 Locked** + `Retry-After`.
  También existe **504** (`LockTimeoutError`) además de 503 (`DeviceBusyError`).
- `DELETE /api/channels` responde `{ok,detail}` (no `{deleted:n}`) y acepta
  `?ids=1,2` o `{"all":true}`.
- `GET /api/scan/log` devuelve **una lista** de entradas (no `{"log":[...]}`).
- `GET /api/status` añade `waiting`; `GET /api/health` añade `adapters`;
  `GET /api/auth/me` añade `mock` y `version`; existe `GET /api/auth/session`
  (alias de `me`) y `GET /api/adapters/{a}/{f}/info`.
- `POST /api/auth/logout` y `POST /api/auth/password` exigen sesión **y CSRF**, y
  **revocan** las sesiones anteriores (época de sesión en servidor).
- `POST /api/scan/start` con `table` acepta sólo nombres simples o rutas dentro de
  `TBS_SCAN_TABLES_DIR` (protección anti *path traversal*).
- Primer arranque: si `TBS_ADMIN_PASSWORD` no está definida **no se crea ningún
  usuario** y el panel ofrece el asistente (`/api/auth/setup`). Con la variable
  definida, el admin se crea al arrancar.

## 6. Reglas de integración

1. `app/dvb/**` NUNCA importa `app.config`, `app.models` ni FastAPI.
2. `app/services/**` y `app/routers/**` NUNCA importan `fcntl`, `struct` ni rutas
   de `/dev/dvb` directamente: usan `app.dvb`.
3. Todo acceso a hardware pasa por `registry.get_lock()` (un solo dueño a la vez).
4. Modo mock: `TBS_MOCK=1`; si no, ausencia de `/dev/dvb/adapter*` => mock con aviso.
5. Español en UI y comentarios; identificadores en inglés.
