/**
 * TBS WebUI - estado compartido mínimo con bus de eventos.
 * Evita acoplar las vistas entre sí: todas leen `state` y escuchan eventos.
 */

export const state = {
  /** SignalStatsOut más reciente (o un equivalente generado por el polling). */
  stats: null,
  /** última respuesta completa de GET /api/status. */
  status: null,
  /** TuneRequest actualmente sintonizado (o null). */
  tuned: null,
  /** SettingsOut. */
  settings: null,
  /** Lista de AdapterOut. */
  adapters: [],
  mock: false,
  lockedBy: null,
  /** connecting | open | polling | closed. */
  conn: 'closed',
  /** Fuente del último dato de señal: ws | poll. */
  source: null,
  username: null,
  version: null,
};

const listeners = new Map();

/** Suscribe `handler` al evento `name`. Devuelve una función para darse de baja. */
export function on(name, handler) {
  if (!listeners.has(name)) listeners.set(name, new Set());
  listeners.get(name).add(handler);
  return () => {
    const set = listeners.get(name);
    if (set) set.delete(handler);
  };
}

/** Publica un evento con un payload. */
export function emit(name, payload) {
  const set = listeners.get(name);
  if (!set) return;
  set.forEach((handler) => {
    try {
      handler(payload);
    } catch (error) {
      console.error('[TBS] error en el manejador de "' + name + '"', error);
    }
  });
}

/** Actualiza el estado y emite el evento correspondiente. */
export function set(key, value, eventName) {
  state[key] = value;
  emit(eventName || 'change', { key: key, value: value });
}

export default { state, on, emit, set };
