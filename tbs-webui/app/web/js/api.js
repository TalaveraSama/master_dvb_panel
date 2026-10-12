/**
 * TBS WebUI - capa de acceso a la API HTTP (INTERFACES.md §5).
 *
 * Responsabilidades:
 *  - Construir las peticiones con la cabecera X-CSRF-Token en las mutaciones.
 *  - Normalizar los errores del backend ({"detail": "..."}) a ApiError.
 *  - Redirigir a /login ante un 401 (salvo cuando lo pide el propio login).
 *
 * Todas las rutas son relativas para que FastAPI sirva app/web como estáticos.
 */

const API_BASE = '/api';

/** Error de API con el código HTTP y el detalle devuelto por el backend. */
export class ApiError extends Error {
  constructor(detail, status, path, extra) {
    super(detail || 'Error de comunicación con el servidor');
    this.name = 'ApiError';
    this.detail = detail || 'Error de comunicación con el servidor';
    this.status = status;
    this.path = path;
    this.extra = extra || null;
  }

  get isAuth() {
    return this.status === 401;
  }

  get isCsrf() {
    return this.status === 403;
  }

  get isBusy() {
    return this.status === 503 || this.status === 409;
  }
}

let csrfToken = null;

/** Guarda el token CSRF recibido de /api/auth/me o del login. */
export function setCsrfToken(token) {
  csrfToken = token || null;
}

/** Devuelve el token CSRF en memoria (o null). */
export function getCsrfToken() {
  return csrfToken;
}

/** Construye una query string omitiendo valores vacíos. */
export function buildQuery(query) {
  if (!query) return '';
  const params = new URLSearchParams();
  Object.keys(query).forEach((key) => {
    const value = query[key];
    if (value === null || value === undefined || value === '') return;
    params.append(key, String(value));
  });
  const text = params.toString();
  return text ? '?' + text : '';
}

/** Redirige a la pantalla de login conservando el destino. */
function redirectToLogin() {
  const here = window.location.pathname + window.location.search;
  if (window.location.pathname === '/login' || window.location.pathname === '/login.html') return;
  const next = here && here !== '/' ? '?next=' + encodeURIComponent(here) : '';
  window.location.replace('/login' + next);
}

/** Extrae el detalle de error de una respuesta (JSON {"detail": ...} o texto). */
async function readErrorDetail(response) {
  let detail = null;
  try {
    const text = await response.text();
    if (text) {
      try {
        const parsed = JSON.parse(text);
        if (parsed && typeof parsed === 'object') {
          if (typeof parsed.detail === 'string') {
            detail = parsed.detail;
          } else if (Array.isArray(parsed.detail)) {
            // Errores de validación de pydantic.
            detail = parsed.detail
              .map((item) => {
                const where = Array.isArray(item.loc) ? item.loc.filter((p) => p !== 'body').join('.') : '';
                return (where ? where + ': ' : '') + (item.msg || 'valor inválido');
              })
              .join('; ');
          } else if (typeof parsed.message === 'string') {
            detail = parsed.message;
          }
        }
      } catch (_) {
        detail = text.slice(0, 400);
      }
    }
  } catch (_) {
    detail = null;
  }
  return detail;
}

/** Mensaje en español para los códigos de estado relevantes. */
function defaultMessage(status) {
  switch (status) {
    case 400:
      return 'La petición no es válida.';
    case 401:
      return 'La sesión ha caducado. Inicia sesión de nuevo.';
    case 403:
      return 'Token CSRF inválido o ausente. Recarga la página.';
    case 404:
      return 'El recurso solicitado no existe.';
    case 409:
      return 'La operación entra en conflicto con el estado actual (recurso ocupado).';
    case 422:
      return 'Los datos enviados no son válidos.';
    case 423:
      return 'Cuenta bloqueada temporalmente por demasiados intentos.';
    case 429:
      return 'Demasiados intentos. Espera unos segundos antes de reintentar.';
    case 500:
      return 'Error interno del servidor.';
    case 503:
      return 'El dispositivo está ocupado por otro proceso.';
    default:
      return 'Error inesperado (' + status + ').';
  }
}

/**
 * Petición JSON genérica.
 * @param {string} path ruta relativa a /api (ej. "/status").
 * @param {object} [options]
 */
export async function request(path, options) {
  const opts = options || {};
  const method = (opts.method || 'GET').toUpperCase();
  const url = API_BASE + path + buildQuery(opts.query);
  const headers = Object.assign({ Accept: 'application/json' }, opts.headers || {});
  const isMutation = method !== 'GET' && method !== 'HEAD';

  if (isMutation) {
    ensureCsrf();
    if (csrfToken) headers['X-CSRF-Token'] = csrfToken;
  }
  let payload;
  if (opts.body !== undefined && opts.body !== null) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(opts.body);
  }

  const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
  const timeoutMs = opts.timeoutMs || 20000;
  let timer = null;
  if (controller && timeoutMs > 0) {
    timer = setTimeout(() => controller.abort(), timeoutMs);
  }

  let response;
  try {
    response = await fetch(url, {
      method: method,
      headers: headers,
      body: payload,
      credentials: 'same-origin',
      cache: 'no-store',
      signal: controller ? controller.signal : undefined,
    });
  } catch (error) {
    if (timer) clearTimeout(timer);
    if (error && error.name === 'AbortError') {
      throw new ApiError('El servidor tardó demasiado en responder.', 0, path);
    }
    throw new ApiError('No se pudo conectar con el servidor.', 0, path);
  }
  if (timer) clearTimeout(timer);

  if (!response.ok) {
    const detail = (await readErrorDetail(response)) || defaultMessage(response.status);
    const error = new ApiError(detail, response.status, path, {
      retryAfter: response.headers.get('Retry-After'),
    });
    if (response.status === 401 && opts.redirectOn401 !== false) {
      redirectToLogin();
    }
    throw error;
  }

  if (opts.raw) return response;
  if (response.status === 204) return null;
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch (_) {
    return text;
  }
}

/** Intenta obtener el token CSRF si aún no lo tenemos (una sola vez por sesión). */
let csrfPromise = null;
export function ensureCsrf() {
  if (csrfToken) return Promise.resolve(csrfToken);
  if (!csrfPromise) {
    csrfPromise = request('/auth/me', { redirectOn401: false })
      .then((data) => {
        if (data && data.csrf_token) csrfToken = data.csrf_token;
        return csrfToken;
      })
      .catch(() => null)
      .then((token) => {
        csrfPromise = null;
        return token;
      });
  }
  return csrfPromise;
}

/** URL relativa para descargas (el navegador envía la cookie de sesión). */
export function downloadUrl(path, query) {
  return API_BASE + path + buildQuery(query);
}

export const api = {
  // --- Autenticación -------------------------------------------------------
  me: (opts) => request('/auth/me', Object.assign({ redirectOn401: false }, opts)),
  setup: (username, password) =>
    request('/auth/setup', { method: 'POST', body: { username, password }, redirectOn401: false }),
  login: (username, password) =>
    request('/auth/login', { method: 'POST', body: { username, password }, redirectOn401: false }),
  logout: () => request('/auth/logout', { method: 'POST' }),
  changePassword: (current_password, new_password) =>
    request('/auth/password', { method: 'POST', body: { current_password, new_password } }),

  // --- Salud y adaptadores -------------------------------------------------
  health: (opts) => request('/health', Object.assign({ redirectOn401: false }, opts)),
  adapters: () => request('/adapters'),
  frontendInfo: (adapter, frontend) => request('/adapters/' + adapter + '/' + frontend + '/info'),

  // --- Sintonización / estado ---------------------------------------------
  tune: (payload) => request('/tune', { method: 'POST', body: payload, timeoutMs: 30000 }),
  release: () => request('/tune/release', { method: 'POST' }),
  status: (opts) => request('/status', opts),

  // --- Escaneo -------------------------------------------------------------
  scanTables: () => request('/scan/tables'),
  scanStart: (payload) => request('/scan/start', { method: 'POST', body: payload, timeoutMs: 60000 }),
  scanStatus: (opts) => request('/scan/status', opts),
  scanLog: (since, opts) =>
    request('/scan/log', Object.assign({ query: { since: since } }, opts)),
  scanStop: () => request('/scan/stop', { method: 'POST' }),

  // --- Canales y transponders ---------------------------------------------
  channels: (query, opts) => request('/channels', Object.assign({ query: query }, opts)),
  renameChannel: (id, name) => request('/channels/' + id, { method: 'PATCH', body: { name: name } }),
  deleteChannel: (id) => request('/channels/' + id, { method: 'DELETE' }),
  deleteChannels: (ids) =>
    request('/channels', { method: 'DELETE', query: { ids: (ids || []).join(',') } }),
  deleteAllChannels: () => request('/channels', { method: 'DELETE', body: { all: true } }),
  transponders: (query, opts) => request('/transponders', Object.assign({ query: query }, opts)),

  // --- Exportación ---------------------------------------------------------
  exportM3UUrl: (query) => downloadUrl('/export/m3u', query),
  exportChannelsConfUrl: (query) => downloadUrl('/export/channels.conf', query),

  // --- Ajustes y registros -------------------------------------------------
  getSettings: () => request('/settings'),
  saveSettings: (payload) => request('/settings', { method: 'PUT', body: payload }),
  logs: (limit) => request('/logs', { query: { limit: limit } }),
};

export default api;
