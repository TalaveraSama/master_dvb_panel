/**
 * TBS WebUI - cliente del WebSocket /ws/signal.
 *
 * Comportamiento (INTERFACES.md §5):
 *  - Mensajes al servidor: {"type":"subscribe"} | {"type":"tune", ...TuneRequest} |
 *    {"type":"release"} | {"type":"ping"}.
 *  - Mensajes del servidor: {"type":"stats", ...SignalStatsOut, "history":[...]},
 *    {"type":"error","detail":...}, {"type":"pong"}.
 *  - Reconexión automática con backoff exponencial + jitter.
 *  - Si el WS no está disponible, se degrada a polling de /api/status cada 1 s.
 *  - Cierre con código 4401 (sesión no válida) => redirección a /login.
 */

import api from './api.js';

export const WS_PATH = '/ws/signal';
export const CLOSE_UNAUTHORIZED = 4401;

/** Estados publicados a la UI. */
export const CONN = {
  CONNECTING: 'connecting',
  OPEN: 'open',
  POLLING: 'polling',
  CLOSED: 'closed',
};

export class SignalSocket {
  /**
   * @param {object} opts
   * @param {(stats:object, history:Array|null) => void} opts.onStats
   * @param {(state:string, info:object) => void} opts.onState
   * @param {(detail:string) => void} [opts.onError]
   * @param {number} [opts.pollMs] intervalo del polling de respaldo (1000 ms)
   */
  constructor(opts) {
    this.opts = opts || {};
    this.pollMs = this.opts.pollMs || 1000;
    this.ws = null;
    this.stopped = true;
    this.state = CONN.CLOSED;
    this.attempts = 0;
    this.pollTimer = null;
    this.reconnectTimer = null;
    this.pingTimer = null;
    this.watchdogTimer = null;
    this.lastMessageAt = 0;
    this.lastStats = null;
    this.onlineHandler = null;
  }

  /** Arranca la conexión y el polling de respaldo. */
  start() {
    if (!this.stopped) return;
    this.stopped = false;
    this.onlineHandler = () => {
      // Al recuperar la red, reconecta de inmediato.
      if (this.state !== CONN.OPEN) this.reconnectNow();
    };
    window.addEventListener('online', this.onlineHandler);
    this.connect();
    // Datos inmediatos aunque el WS tarde (o no exista).
    this.startPolling();
  }

  /** Detiene todo (socket, polling y temporizadores). */
  stop() {
    this.stopped = true;
    if (this.onlineHandler) {
      window.removeEventListener('online', this.onlineHandler);
      this.onlineHandler = null;
    }
    this.stopPolling();
    this.stopPing();
    this.stopWatchdog();
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      const socket = this.ws;
      this.ws = null;
      socket.onopen = socket.onmessage = socket.onclose = socket.onerror = null;
      try {
        socket.close(1000, 'cliente detenido');
      } catch (_) {
        /* ignorado */
      }
    }
    this.setState(CONN.CLOSED);
  }

  /** ¿El WebSocket está operativo? */
  isOpen() {
    return !!this.ws && this.ws.readyState === 1;
  }

  /** Fuerza una reconexión inmediata (reinicia el backoff). */
  reconnectNow() {
    if (this.stopped) return;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.attempts = 0;
    if (!this.isOpen() && this.ws) {
      try {
        this.ws.close(4000, 'reconectar');
      } catch (_) {
        /* ignorado */
      }
    } else if (!this.isOpen()) {
      this.connect();
    }
  }

  // --- Conexión ------------------------------------------------------------

  connect() {
    if (this.stopped || this.isOpen()) return;
    this.setState(CONN.CONNECTING);
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = protocol + '//' + window.location.host + WS_PATH;
    let socket;
    try {
      socket = new WebSocket(url);
    } catch (_) {
      this.scheduleReconnect();
      return;
    }
    this.ws = socket;

    socket.onopen = () => {
      this.attempts = 0;
      this.setState(CONN.OPEN, { url: url });
      this.stopPolling();
      this.send({ type: 'subscribe' });
      this.startPing();
      this.startWatchdog();
    };

    socket.onmessage = (event) => {
      this.lastMessageAt = Date.now();
      this.handleMessage(event.data);
    };

    socket.onclose = (event) => {
      this.ws = null;
      this.stopPing();
      this.stopWatchdog();
      if (event && event.code === CLOSE_UNAUTHORIZED) {
        this.stopped = true;
        this.stopPolling();
        window.location.replace('/login');
        return;
      }
      this.startPolling();
      this.scheduleReconnect();
    };

    socket.onerror = () => {
      /* onclose se encarga de la reconexión */
    };
  }

  /** Backoff exponencial (500 ms -> 15 s) con jitter. */
  scheduleReconnect() {
    if (this.stopped || this.reconnectTimer) return;
    const base = Math.min(15000, 500 * Math.pow(2, this.attempts));
    const delay = Math.round(base * (0.8 + Math.random() * 0.4));
    this.attempts = Math.min(this.attempts + 1, 8);
    this.setState(this.pollTimer ? CONN.POLLING : CONN.CLOSED, { retryInMs: delay, attempts: this.attempts });
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  // --- Mensajes ------------------------------------------------------------

  send(payload) {
    if (!this.isOpen()) return false;
    try {
      this.ws.send(JSON.stringify(payload));
      return true;
    } catch (_) {
      return false;
    }
  }

  handleMessage(raw) {
    let data = null;
    try {
      data = JSON.parse(raw);
    } catch (_) {
      return;
    }
    if (!data || typeof data !== 'object') return;
    if (data.type === 'pong') return;
    if (data.type === 'error') {
      if (typeof this.opts.onError === 'function') this.opts.onError(data.detail || 'Error del servidor');
      return;
    }
    // "stats" o cualquier objeto con forma de SignalStatsOut.
    if (data.type === 'stats' || data.timestamp !== undefined || data.locked !== undefined) {
      const history = Array.isArray(data.history) ? data.history : null;
      this.lastStats = data;
      if (typeof this.opts.onStats === 'function') this.opts.onStats(data, history);
    }
  }

  // --- Sintonización -------------------------------------------------------

  /**
   * Envía una sintonización por WS; si no hay WS, usa POST /api/tune.
   * @returns {Promise<object|null>} respuesta REST o null si fue por WS.
   */
  async tune(payload) {
    if (this.isOpen() && this.send(Object.assign({ type: 'tune' }, payload))) {
      return null;
    }
    const result = await api.tune(payload);
    if (result && result.stats && typeof this.opts.onStats === 'function') {
      this.opts.onStats(result.stats, null);
    }
    return result;
  }

  /** Libera el frontend por WS o, si no hay WS, por REST. */
  async release() {
    if (this.isOpen() && this.send({ type: 'release' })) return null;
    return api.release();
  }

  // --- Latido y vigilancia -------------------------------------------------

  startPing() {
    this.stopPing();
    this.pingTimer = setInterval(() => {
      if (this.isOpen()) this.send({ type: 'ping' });
    }, 20000);
  }

  stopPing() {
    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  /** Si no llegan datos en 6 s con el socket abierto, se fuerza reconexión. */
  startWatchdog() {
    this.stopWatchdog();
    this.lastMessageAt = Date.now();
    this.watchdogTimer = setInterval(() => {
      if (!this.isOpen()) return;
      if (Date.now() - this.lastMessageAt > 6000) {
        try {
          this.ws.close(4001, 'sin datos');
        } catch (_) {
          /* ignorado */
        }
      }
    }, 2000);
  }

  stopWatchdog() {
    if (this.watchdogTimer) {
      clearInterval(this.watchdogTimer);
      this.watchdogTimer = null;
    }
  }

  // --- Polling de respaldo -------------------------------------------------

  startPolling() {
    if (this.pollTimer || this.stopped) return;
    const tick = async () => {
      try {
        const data = await api.status({ redirectOn401: false });
        if (!data) return;
        const stats = data.stats || null;
        if (stats) {
          this.opts.onStats && this.opts.onStats(stats, null);
        } else {
          this.opts.onStats &&
            this.opts.onStats(
              {
                timestamp: Date.now() / 1000,
                locked: false,
                signal_percent: null,
                signal_dbm: null,
                snr_db: null,
                ber: null,
                ber_pre: null,
                per: null,
                ucblocks: null,
                quality_percent: null,
                cnr_raw: null,
                error: null,
                flags: { signal: false, carrier: false, viterbi: false, sync: false, lock: false },
                tuned: data.tuned || null,
                locked_by: data.locked_by || null,
                mock: !!data.mock,
                source: 'poll',
              },
              null
            );
        }
      } catch (error) {
        if (error && error.isAuth) {
          // La sesión caducó: no insistir con el polling.
          this.stopPolling();
        }
      }
    };
    this.pollTimer = setInterval(tick, this.pollMs);
    tick();
  }

  stopPolling() {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
    if (this.state === CONN.POLLING) this.setState(this.isOpen() ? CONN.OPEN : CONN.CLOSED);
  }

  // --- Estado --------------------------------------------------------------

  setState(state, info) {
    if (this.state === state && !info) return;
    this.state = state;
    if (typeof this.opts.onState === 'function') this.opts.onState(state, info || {});
  }
}

export default SignalSocket;
