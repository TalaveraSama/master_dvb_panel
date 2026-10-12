/**
 * TBS WebUI - vista de ajustes: adaptador por defecto, parámetros de LNB,
 * timeouts de escaneo, ritmo de sondeo de la UI, cambio de contraseña y
 * visor del registro del servidor.
 */

import { api } from '../api.js';
import * as store from '../store.js';
import { byId, esc, int, num, setBusy, setText, fmtDateTime } from '../utils.js';
import { toast } from '../ui.js';

const UNIVERSAL = { low: 9750, high: 10600, threshold: 11700 };

function applyLnbType() {
  const type = byId('set-lnb-type') ? byId('set-lnb-type').value : 'universal';
  const custom = type === 'custom';
  ['set-lnb-low', 'set-lnb-high', 'set-lnb-threshold'].forEach((id) => {
    const node = byId(id);
    if (node) node.disabled = !custom;
  });
  if (!custom) {
    if (byId('set-lnb-low')) byId('set-lnb-low').value = UNIVERSAL.low;
    if (byId('set-lnb-high')) byId('set-lnb-high').value = UNIVERSAL.high;
    if (byId('set-lnb-threshold')) byId('set-lnb-threshold').value = UNIVERSAL.threshold;
  }
}

/** Vuelca SettingsOut en el formulario. */
function fillForm(settings) {
  if (!settings) return;
  const lnb = settings.lnb || {};
  const scan = settings.scan || {};
  const ui = settings.ui || {};

  const adapterSelect = byId('set-adapter');
  if (adapterSelect) {
    const value = (settings.default_adapter === undefined ? 0 : settings.default_adapter) + ':' +
      (settings.default_frontend === undefined ? 0 : settings.default_frontend);
    if (!Array.prototype.some.call(adapterSelect.options, (option) => option.value === value)) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = 'Adaptador ' + settings.default_adapter + '/' + (settings.default_frontend || 0);
      adapterSelect.appendChild(option);
    }
    adapterSelect.value = value;
  }

  const low = int(lnb.low_khz, UNIVERSAL.low);
  const high = int(lnb.high_khz, UNIVERSAL.high);
  const threshold = int(lnb.threshold_khz, UNIVERSAL.threshold);
  if (byId('set-lnb-low')) byId('set-lnb-low').value = low;
  if (byId('set-lnb-high')) byId('set-lnb-high').value = high;
  if (byId('set-lnb-threshold')) byId('set-lnb-threshold').value = threshold;
  if (byId('set-lnb-type')) {
    const isUniversal = low === UNIVERSAL.low && high === UNIVERSAL.high && threshold === UNIVERSAL.threshold;
    byId('set-lnb-type').value = isUniversal ? 'universal' : 'custom';
  }
  if (byId('set-tone')) byId('set-tone').checked = !!lnb.tone_22k;
  if (byId('set-voltage')) byId('set-voltage').value = lnb.voltage || '13V';

  if (byId('set-lock-timeout')) byId('set-lock-timeout').value = num(scan.lock_timeout_s, 5);
  if (byId('set-lock-timeout-ms')) byId('set-lock-timeout-ms').value = int(scan.lock_timeout_ms, 500);
  if (byId('set-dwell')) byId('set-dwell').value = num(scan.dwell_seconds, 8);
  if (byId('set-poll-ms')) byId('set-poll-ms').value = int(ui.signal_poll_ms, 500);
  if (byId('set-history-seconds')) byId('set-history-seconds').value = int(ui.history_seconds, 60);

  setText('settings-mock', settings.mock ? 'sí (modo simulación)' : 'no (hardware real)');
  applyLnbType();
}

/** Construye el cuerpo de PUT /api/settings. */
function buildPayload() {
  const adapterValue = byId('set-adapter') ? byId('set-adapter').value : '0:0';
  const parts = adapterValue.split(':');
  return {
    default_adapter: int(parts[0], 0),
    default_frontend: int(parts[1], 0),
    lnb: {
      low_khz: int(byId('set-lnb-low').value, UNIVERSAL.low),
      high_khz: int(byId('set-lnb-high').value, UNIVERSAL.high),
      threshold_khz: int(byId('set-lnb-threshold').value, UNIVERSAL.threshold),
      tone_22k: !!byId('set-tone').checked,
      voltage: byId('set-voltage').value || '13V',
    },
    scan: {
      lock_timeout_s: num(byId('set-lock-timeout').value, 5),
      lock_timeout_ms: int(byId('set-lock-timeout-ms').value, 500),
      // Se envía siempre: SettingsIn.scan es un objeto completo y omitirlo
      // devolvería dwell_seconds a su valor por defecto en el backend.
      dwell_seconds: num(byId('set-dwell').value, 8),
    },
    ui: {
      signal_poll_ms: int(byId('set-poll-ms').value, 500),
      history_seconds: int(byId('set-history-seconds').value, 60),
    },
  };
}

export function init(ctx) {
  const form = byId('settings-form');
  if (!form) return { activate() {}, deactivate() {}, destroy() {} };

  /** Carga los ajustes desde el backend. */
  async function load() {
    try {
      const settings = await api.getSettings();
      store.emit('settings', settings);
      fillForm(settings);
      const label = byId('settings-default');
      if (label) {
        label.textContent =
          'Adaptador por defecto: ' + settings.default_adapter + '/' + (settings.default_frontend || 0);
      }
    } catch (error) {
      toast(error.detail || 'No se pudieron cargar los ajustes.', 'error');
    }
  }

  /** Rellena el selector de adaptadores por defecto. */
  function fillAdapters() {
    const select = byId('set-adapter');
    if (!select) return;
    const previous = select.value;
    select.innerHTML = '';
    (store.state.adapters || []).forEach((adapter) => {
      const option = document.createElement('option');
      option.value = adapter.adapter + ':' + (adapter.frontend === undefined ? 0 : adapter.frontend);
      option.textContent = (adapter.name || 'Adaptador ' + adapter.adapter) + ' (' + adapter.adapter + '/' + (adapter.frontend || 0) + ')';
      select.appendChild(option);
    });
    if (!select.options.length) {
      const option = document.createElement('option');
      option.value = '0:0';
      option.textContent = '0/0 (sin adaptadores detectados)';
      select.appendChild(option);
    }
    if (previous) select.value = previous;
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const save = byId('settings-save');
    const payload = buildPayload();
    setBusy(save, true, 'Guardando…');
    try {
      const settings = await api.saveSettings(payload);
      store.emit('settings', settings);
      fillForm(settings);
      toast('Ajustes guardados.', 'success');
    } catch (error) {
      toast(error.detail || 'No se pudieron guardar los ajustes.', 'error');
    } finally {
      setBusy(save, false);
    }
  });

  if (byId('settings-reload')) byId('settings-reload').addEventListener('click', load);
  if (byId('set-lnb-type')) byId('set-lnb-type').addEventListener('change', applyLnbType);

  // --- Cambio de contraseña ------------------------------------------------
  const pwForm = byId('pw-form');
  if (pwForm) {
    pwForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const current = byId('pw-current').value;
      const next = byId('pw-new').value;
      const confirm = byId('pw-confirm').value;
      if (!current) {
        toast('Introduce la contraseña actual.', 'warn');
        return;
      }
      if (next.length < 8) {
        toast('La contraseña nueva debe tener al menos 8 caracteres.', 'warn');
        return;
      }
      if (next !== confirm) {
        toast('La confirmación no coincide con la contraseña nueva.', 'warn');
        return;
      }
      const submit = byId('pw-submit');
      setBusy(submit, true, 'Cambiando…');
      try {
        await api.changePassword(current, next);
        toast('Contraseña actualizada.', 'success');
        pwForm.reset();
      } catch (error) {
        toast(error.detail || 'No se pudo cambiar la contraseña.', 'error');
      } finally {
        setBusy(submit, false);
      }
    });
  }

  // --- Registro del servidor ----------------------------------------------
  async function loadLogs() {
    const list = byId('logs-list');
    if (!list) return;
    list.innerHTML = '<li class="muted">Cargando…</li>';
    try {
      const data = await api.logs(100);
      const items = (data && data.items) || [];
      setText('logs-count', String(items.length));
      if (!items.length) {
        list.innerHTML = '<li class="muted">Sin entradas.</li>';
        return;
      }
      list.innerHTML = items
        .slice(-200)
        .map((item) => {
          if (typeof item === 'string') return '<li class="log-line"><span class="log-msg">' + esc(item) + '</span></li>';
          const level = item.level || item.status || 'info';
          const message = item.msg || item.message || JSON.stringify(item);
          const time = item.ts || item.timestamp || item.created_at || '';
          return (
            '<li class="log-line log-' + esc(level) + '">' +
            '<span class="log-time">' + esc(time ? fmtDateTime(time) : '') + '</span>' +
            '<span class="log-level">' + esc(level) + '</span>' +
            '<span class="log-msg">' + esc(message) + '</span></li>'
          );
        })
        .join('');
    } catch (error) {
      list.innerHTML = '<li class="muted">No se pudo cargar el registro.</li>';
    }
  }
  if (byId('logs-refresh')) byId('logs-refresh').addEventListener('click', loadLogs);

  ctx.on('adapters', () => {
    fillAdapters();
    if (store.state.settings) fillForm(store.state.settings);
  });
  ctx.on('settings', (settings) => fillForm(settings));

  return {
    activate() {
      fillAdapters();
      load();
      loadLogs();
    },
    deactivate() {},
    destroy() {},
  };
}

export default { init };
