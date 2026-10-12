/**
 * TBS WebUI - arranque de la SPA.
 *
 * Secuencia: comprobar sesión -> cargar ajustes/adaptadores/estado -> montar
 * vistas -> enrutar por hash -> abrir el WebSocket de señal.
 */

import { api, setCsrfToken } from './api.js';
import * as store from './store.js';
import { SignalSocket, CONN } from './ws.js';
import { toast, confirmDialog, promptDialog } from './ui.js';
import { byId, qsa, setText } from './utils.js';

import * as dashboardView from './views/dashboard.js';
import * as tuneView from './views/tune.js';
import * as scanView from './views/scan.js';
import * as channelsView from './views/channels.js';
import * as newcamdView from './views/newcamd.js';
import * as transpondersView from './views/transponders.js';
import * as settingsView from './views/settings.js';

const ROUTES = ['dashboard', 'tune', 'scan', 'channels', 'newcamd', 'transponders', 'settings'];
const TITLES = {
  dashboard: 'Panel',
  tune: 'Sintonización',
  scan: 'Escaneo',
  channels: 'Canales',
  newcamd: 'Newcamd',
  transponders: 'Transponders',
  settings: 'Ajustes',
};

let socket = null;
let controllers = {};
let current = null;

/** Refresca GET /api/status y publica el resultado. */
async function refreshStatus() {
  try {
    const data = await api.status();
    if (!data) return null;
    store.state.status = data;
    store.state.lockedBy = data.locked_by || null;
    if (data.mock !== undefined) store.state.mock = !!data.mock;
    if (data.tuned !== undefined) store.state.tuned = data.tuned;
    store.emit('status', data);
    renderMockBadge();
    return data;
  } catch (error) {
    if (error && error.isAuth) return null;
    return null;
  }
}

/** Refresca la lista de adaptadores. */
async function refreshAdapters() {
  try {
    const data = await api.adapters();
    if (!data) return;
    store.state.adapters = data.adapters || [];
    store.state.lockedBy = data.locked && data.locked.owner ? data.locked.owner : null;
    if (data.mock !== undefined) store.state.mock = !!data.mock;
    store.emit('adapters', data.adapters || []);
    renderMockBadge();
  } catch (error) {
    if (!error || !error.isAuth) store.emit('adapters', []);
  }
}

/** Carga SettingsOut (necesario para los valores de LNB de la vista de sintonización). */
async function refreshSettings() {
  try {
    const data = await api.getSettings();
    if (!data) return;
    store.state.settings = data;
    if (data.mock !== undefined) store.state.mock = !!data.mock;
    store.emit('settings', data);
    renderMockBadge();
    const pollMs = data.ui && data.ui.signal_poll_ms;
    if (socket && pollMs && Number.isFinite(Number(pollMs))) {
      // El intervalo efectivo lo marca el backend; aquí solo se refleja en la UI.
      store.state.signalPollMs = Number(pollMs);
    }
  } catch (error) {
    if (!error || !error.isAuth) store.emit('settings', null);
  }
}

/** Refresca /api/health (versión y modo simulación). */
async function refreshHealth() {
  try {
    const data = await api.health();
    if (!data) return;
    store.state.version = data.version || null;
    store.state.mock = !!data.mock;
    const version = byId('app-version');
    if (version) version.textContent = data.version ? 'v' + data.version : '';
    renderMockBadge();
  } catch (_) {
    /* silencioso: la salud no es crítica */
  }
}

function renderMockBadge() {
  const badge = byId('mock-badge');
  if (!badge) return;
  badge.hidden = !store.state.mock;
  badge.textContent = 'MODO SIMULACIÓN';
  badge.title = 'No se ha detectado hardware DVB real (TBS_MOCK=1 o /dev/dvb ausente).';
}

/** Actualiza el indicador de conexión de la barra superior. */
function renderConn(state, info) {
  const dot = byId('conn-dot');
  const text = byId('conn-text');
  const wrap = byId('conn-status');
  store.state.conn = state;
  const map = {
    [CONN.OPEN]: { cls: 'conn-open', label: 'WebSocket activo' },
    [CONN.CONNECTING]: { cls: 'conn-warn', label: 'Conectando…' },
    [CONN.POLLING]: { cls: 'conn-warn', label: 'Sin WebSocket · polling 1 s' },
    [CONN.CLOSED]: { cls: 'conn-error', label: 'Desconectado' },
  };
  const entry = map[state] || map[CONN.CLOSED];
  if (dot) dot.className = 'dot ' + entry.cls;
  if (text) text.textContent = entry.label;
  if (wrap) {
    wrap.className = 'conn ' + entry.cls;
    wrap.setAttribute('aria-label', 'Estado de la conexión: ' + entry.label);
  }
  setText('conn-detail', info && info.retryInMs ? 'Reintento en ' + Math.round(info.retryInMs / 1000) + ' s' : '');
  store.emit('conn', { state: state, info: info || {} });
}

/** Muestra la sección indicada y activa su controlador. */
function showView(name) {
  if (ROUTES.indexOf(name) === -1) name = 'dashboard';
  qsa('.view').forEach((section) => {
    section.hidden = section.dataset.view !== name;
  });
  qsa('.nav-link').forEach((link) => {
    const active = link.dataset.route === name;
    link.classList.toggle('active', active);
    if (active) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  if (current && controllers[current] && controllers[current].deactivate) {
    try {
      controllers[current].deactivate();
    } catch (_) {
      /* ignorado */
    }
  }
  current = name;
  const controller = controllers[name];
  if (controller && controller.activate) controller.activate();
  document.title = (TITLES[name] || 'Panel') + ' · TBS WebUI';
}

/** Lee el hash actual y navega. */
function routeFromHash() {
  const raw = (window.location.hash || '').replace(/^#\/?/, '');
  const name = raw.split('?')[0];
  showView(name || 'dashboard');
}
/**
 * Respaldo de la navegación móvil cuando Alpine.js no está disponible
 * (por ejemplo si el fichero vendorizado no se ha servido). Con Alpine
 * presente, el propio atributo x-data gestiona el estado y esto no se ejecuta.
 */
function initNavFallback() {
  if (window.Alpine) return;
  const nav = byId('sidenav');
  const toggle = byId('nav-toggle');
  if (!nav || !toggle) return;
  const setOpen = (open) => {
    nav.classList.toggle('nav-open', open);
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  };
  toggle.addEventListener('click', () => setOpen(!nav.classList.contains('nav-open')));
  nav.addEventListener('click', (event) => {
    if (event.target.closest('a')) setOpen(false);
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') setOpen(false);
  });
}

/** Presenta el aviso de error de arranque. */
function showBootError(message) {
  const overlay = byId('boot-overlay');
  if (!overlay) return;
  overlay.hidden = false;
  setText('boot-message', message);
}

/** Arranque principal. */
async function boot() {
  // 1) Sesión
  let me = null;
  try {
    me = await api.me();
  } catch (error) {
    showBootError('No se pudo contactar con el servidor: ' + (error.detail || 'error de red') + ' Inténtalo de nuevo.');
    const retry = byId('boot-retry');
    if (retry) retry.addEventListener('click', () => window.location.reload());
    return;
  }

  if (!me || !me.authenticated) {
    window.location.replace('/login');
    return;
  }
  if (me.must_setup) {
    window.location.replace('/login');
    return;
  }

  setCsrfToken(me.csrf_token);
  store.state.username = me.username || null;
  const userLabel = byId('user-name');
  if (userLabel) userLabel.textContent = me.username || 'sesión';

  // 2) Cierre de sesión
  const logout = byId('logout-btn');
  if (logout) {
    logout.addEventListener('click', async () => {
      const ok = await confirmDialog({
        title: 'Cerrar sesión',
        message: '¿Quieres cerrar la sesión actual?',
        confirmLabel: 'Cerrar sesión',
      });
      if (!ok) return;
      try {
        await api.logout();
      } catch (_) {
        /* aunque falle, volvemos al login */
      }
      window.location.replace('/login');
    });
  }

  // 3) WebSocket de señal
  socket = new SignalSocket({
    pollMs: 1000,
    onStats: (stats, history) => {
      store.state.stats = stats;
      store.state.source = socket && socket.isOpen() ? 'ws' : 'poll';
      store.emit('stats', { stats: stats, history: history, source: store.state.source });
    },
    onState: (state, info) => renderConn(state, info),
    onError: (detail) => toast(detail, 'error', 7000),
  });

  // 4) Contexto compartido por las vistas
  const ctx = {
    api: api,
    store: store,
    socket: socket,
    toast: toast,
    confirmDialog: confirmDialog,
    promptDialog: promptDialog,
    on: store.on,
    emit: store.emit,
    refreshStatus: refreshStatus,
    refreshAdapters: refreshAdapters,
  };

  controllers = {
    dashboard: dashboardView.init(ctx),
    tune: tuneView.init(ctx),
    scan: scanView.init(ctx),
    channels: channelsView.init(ctx),
    newcamd: newcamdView.init(ctx),
    transponders: transpondersView.init(ctx),
    settings: settingsView.init(ctx),
  };

  // 5) Enrutado
  window.addEventListener('hashchange', routeFromHash);
  qsa('.nav-link').forEach((link) => {
    link.addEventListener('click', () => {
      const name = link.dataset.route;
      if (window.location.hash === '#/' + name) showView(name);
    });
  });
  routeFromHash();
  initNavFallback();

  // 6) Datos iniciales
  document.body.classList.add('app-ready');
  renderConn(CONN.CONNECTING, {});
  await Promise.all([refreshHealth(), refreshAdapters(), refreshSettings(), refreshStatus()]);
  socket.start();

  // 7) Sondeo lento del estado (respaldo adicional, cada 15 s)
  setInterval(refreshStatus, 15000);
}

// --- Arranque ---------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
  boot().catch((error) => {
    console.error('[TBS] fallo de arranque', error);
    showBootError('Error inesperado durante el arranque: ' + (error && error.message ? error.message : String(error)));
  });
});

window.addEventListener('unhandledrejection', (event) => {
  const reason = event && event.reason;
  if (reason && reason.detail) {
    toast(reason.detail, 'error', 7000);
  } else {
    console.error('[TBS] promesa rechazada sin manejar', reason);
  }
});

export { boot };
