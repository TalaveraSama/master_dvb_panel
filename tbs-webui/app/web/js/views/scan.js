/**
 * TBS WebUI - vista de escaneo: inicio/parada, progreso, log en vivo y
 * tabla de servicios encontrados.
 *
 * Usa POST /api/scan/start, POST /api/scan/stop y sondea GET /api/scan/status
 * cada segundo mientras el trabajo está en marcha.
 */

import { api } from '../api.js';
import * as store from '../store.js';
import { byId, int, num, esc, setBusy, setText, show, fmtDateTime } from '../utils.js';
import { toast } from '../ui.js';

const POLL_MS = 1000;

export function init(ctx) {
  const form = byId('scan-form');
  if (!form) return { activate() {}, deactivate() {}, destroy() {} };

  let pollTimer = null;
  let renderedLog = 0;
  let running = false;
  let tables = [];

  /** Carga las listas de transponders disponibles. */
  async function loadTables() {
    try {
      const data = await api.scanTables();
      tables = (data && data.bundles) || [];
      const select = byId('scan-table');
      if (!select) return;
      const previous = select.value;
      select.innerHTML = '';
      if (!tables.length) {
        const option = document.createElement('option');
        option.value = '';
        option.textContent = 'No hay listas importadas (dvb-scan-tables)';
        select.appendChild(option);
      }
      tables.forEach((bundle) => {
        const option = document.createElement('option');
        option.value = bundle.name;
        option.textContent =
          bundle.name + (bundle.sat_position ? ' · ' + bundle.sat_position : '') + ' · ' + bundle.transponders + ' TP';
        select.appendChild(option);
      });
      if (previous) select.value = previous;
      updateTableInfo();
    } catch (error) {
      toast(error.detail || 'No se pudieron cargar las listas de escaneo.', 'error');
    }
  }

  function updateTableInfo() {
    const select = byId('scan-table');
    const info = byId('scan-table-info');
    if (!select || !info) return;
    const bundle = tables.find((item) => item.name === select.value);
    info.textContent = bundle
      ? bundle.transponders + ' transponders' + (bundle.sat_position ? ' · ' + bundle.sat_position : '')
      : 'Selecciona una lista.';
  }

  function currentMode() {
    const checked = document.querySelector('input[name="scan-mode"]:checked');
    return checked ? checked.value : 'current';
  }

  function applyModeVisibility() {
    const mode = currentMode();
    show(byId('scan-table-row'), mode === 'table');
    show(byId('scan-mode-hint'), true);
    const hint = byId('scan-mode-hint');
    if (hint) {
      hint.textContent =
        mode === 'current'
          ? 'Se escaneará únicamente el transponder sintonizado actualmente.'
          : 'Se recorrerá la lista completa de transponders de la tabla seleccionada.';
    }
  }

  // --- Log ----------------------------------------------------------------
  function resetLog() {
    renderedLog = 0;
    const log = byId('scan-log');
    if (log) log.innerHTML = '';
    setText('scan-log-count', '0');
  }

  function appendLog(entries) {
    const log = byId('scan-log');
    if (!log || !Array.isArray(entries)) return;
    if (entries.length < renderedLog) resetLog();
    const fragment = document.createDocumentFragment();
    for (let i = renderedLog; i < entries.length; i += 1) {
      const entry = entries[i] || {};
      const li = document.createElement('li');
      li.className = 'log-line log-' + esc(entry.level || 'info');
      const time = entry.ts ? fmtDateTime(entry.ts) : '';
      li.innerHTML =
        '<span class="log-time">' + esc(time.slice(-8)) + '</span>' +
        '<span class="log-level">' + esc(entry.level || 'info') + '</span>' +
        '<span class="log-msg">' + esc(entry.msg || '') + '</span>';
      fragment.appendChild(li);
    }
    log.appendChild(fragment);
    renderedLog = entries.length;
    setText('scan-log-count', String(renderedLog));
    const autoscroll = byId('scan-autoscroll');
    if (!autoscroll || autoscroll.checked) log.scrollTop = log.scrollHeight;
  }

  // --- Servicios ----------------------------------------------------------
  function renderServices(services) {
    const body = byId('scan-services-body');
    if (!body) return;
    const items = Array.isArray(services) ? services : [];
    setText('scan-services-count', String(items.length));
    if (!items.length) {
      body.innerHTML = '<tr><td colspan="7" class="muted center">Todavía no se han encontrado servicios.</td></tr>';
      return;
    }
    const fragment = document.createDocumentFragment();
    items.forEach((service) => {
      const tr = document.createElement('tr');
      tr.innerHTML =
        '<td class="mono">' + esc(service.service_id) + '</td>' +
        '<td>' + esc(service.display_name || service.name || '—') + '</td>' +
        '<td>' + esc(service.provider || '—') + '</td>' +
        '<td><span class="badge">' + esc(service.service_type_label || '—') + '</span></td>' +
        '<td class="mono">' + esc(service.video_pid === null || service.video_pid === undefined ? '—' : service.video_pid) + '</td>' +
        '<td class="mono">' + esc(service.audio_pid === null || service.audio_pid === undefined ? '—' : service.audio_pid) + '</td>' +
        '<td>' + (service.scrambled ? '<span class="badge badge-warn">Cifrado</span>' : '<span class="badge badge-ok">FTA</span>') + '</td>';
      fragment.appendChild(tr);
    });
    body.innerHTML = '';
    body.appendChild(fragment);
  }

  function renderErrors(errors) {
    const box = byId('scan-errors');
    if (!box) return;
    const list = Array.isArray(errors) ? errors : [];
    if (!list.length) {
      box.hidden = true;
      box.innerHTML = '';
      return;
    }
    box.hidden = false;
    box.innerHTML =
      '<h3>Errores (' + list.length + ')</h3><ul>' +
      list
        .slice(-20)
        .map((item) => '<li>' + esc(item) + '</li>')
        .join('') +
      '</ul>';
  }

  function renderProgress(progress) {
    const data = progress || {};
    const state = data.state || 'idle';
    const percent = num(data.percent, 0);
    const bar = byId('scan-progress');
    const fill = byId('scan-progress-fill');
    if (fill) fill.style.width = Math.max(0, Math.min(100, percent)).toFixed(1) + '%';
    if (bar) {
      bar.setAttribute('aria-valuenow', String(Math.round(percent)));
      bar.setAttribute('aria-valuetext', percent.toFixed(1) + ' por ciento, estado ' + state);
    }
    setText('scan-percent', percent.toFixed(1) + ' %');
    setText('scan-state', state);
    const stateEl = byId('scan-state');
    if (stateEl) stateEl.className = 'badge state-' + state;
    setText('scan-message', data.message || '—');
    setText('scan-current-tp', data.current_transponder || '—');
    setText('scan-services-found', data.services_found === undefined ? '0' : String(data.services_found));
    setText('scan-locked', data.transponders_locked === undefined ? '0' : String(data.transponders_locked));
    setText('scan-scanned', data.transponders_scanned === undefined ? '0' : String(data.transponders_scanned));
    setText('scan-total', data.total === undefined ? '0' : String(data.total));
    renderErrors(data.errors);
    return state;
  }

  /** Un ciclo de sondeo de /api/scan/status. */
  async function poll() {
    try {
      const data = await api.scanStatus();
      if (!data) return;
      const state = renderProgress(data.progress);
      appendLog(data.log);
      renderServices(data.services);
      const active = state === 'running' || state === 'stopping' || state === 'starting';
      if (!active && running) {
        running = false;
        stopPolling();
        setBusy(byId('scan-start'), false);
        setBusy(byId('scan-stop'), false);
        if (byId('scan-stop')) byId('scan-stop').disabled = true;
        if (state === 'done') toast('Escaneo completado.', 'success');
        else if (state === 'error') toast('El escaneo terminó con errores.', 'error', 8000);
        else if (state === 'cancelled') toast('Escaneo cancelado.', 'warn');
        store.emit('channels-changed');
        store.emit('transponders-changed');
      }
    } catch (error) {
      if (running) {
        running = false;
        stopPolling();
        setBusy(byId('scan-start'), false);
        setBusy(byId('scan-stop'), false);
      }
    }
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(poll, POLL_MS);
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  // --- Arranque del escaneo ----------------------------------------------
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const startBtn = byId('scan-start');
    const mode = currentMode();
    const adapterSelect = byId('scan-adapter');
    let adapter = 0;
    let frontend = 0;
    if (adapterSelect && adapterSelect.value) {
      const parts = adapterSelect.value.split(':');
      adapter = int(parts[0], 0);
      frontend = int(parts[1], 0);
    } else if (store.state.settings) {
      adapter = int(store.state.settings.default_adapter, 0);
      frontend = int(store.state.settings.default_frontend, 0);
    }

    const payload = {
      adapters: [{ adapter: adapter, frontend: frontend }],
      transponders: null,
      table: null,
      current_only: mode === 'current',
      clear_existing: !!(byId('scan-clear') && byId('scan-clear').checked),
      lock_timeout_s: num(byId('scan-lock-timeout') && byId('scan-lock-timeout').value, 5),
    };
    const dwell = num(byId('scan-dwell') && byId('scan-dwell').value, null);
    if (dwell !== null) payload.dwell_seconds = dwell;

    const settings = store.state.settings;
    if (settings && settings.lnb) {
      payload.use_lnb = true;
      payload.lnb_low_khz = int(settings.lnb.low_khz, 9750);
      payload.lnb_high_khz = int(settings.lnb.high_khz, 10600);
      payload.lnb_threshold_khz = int(settings.lnb.threshold_khz, 11700);
    }

    if (mode === 'table') {
      const table = byId('scan-table') ? byId('scan-table').value : '';
      if (!table) {
        toast('Selecciona una lista de transponders o usa el modo "transponder actual".', 'warn');
        return;
      }
      payload.table = table;
    }

    setBusy(startBtn, true, 'Iniciando…');
    resetLog();
    renderServices([]);
    renderErrors([]);
    try {
      const response = await api.scanStart(payload);
      running = true;
      if (byId('scan-stop')) byId('scan-stop').disabled = false;
      toast('Escaneo iniciado' + (response && response.job_id ? ' (job ' + response.job_id + ')' : '') + '.', 'success');
      setBusy(startBtn, false);
      await poll();
      startPolling();
    } catch (error) {
      running = false;
      setBusy(startBtn, false);
      if (error.status === 409 || error.status === 503) {
        toast('Ya hay un trabajo en curso o el dispositivo está ocupado.', 'warn', 7000);
      } else {
        toast(error.detail || 'No se pudo iniciar el escaneo.', 'error', 7000);
      }
    }
  });

  const stopBtn = byId('scan-stop');
  if (stopBtn) {
    stopBtn.disabled = true;
    stopBtn.addEventListener('click', async () => {
      setBusy(stopBtn, true, 'Deteniendo…');
      try {
        await api.scanStop();
        toast('Detención solicitada.', 'info');
      } catch (error) {
        toast(error.detail || 'No se pudo detener el escaneo.', 'error');
      } finally {
        setBusy(stopBtn, false);
      }
    });
  }

  if (byId('scan-mode-current')) byId('scan-mode-current').addEventListener('change', applyModeVisibility);
  if (byId('scan-mode-table')) byId('scan-mode-table').addEventListener('change', applyModeVisibility);
  if (byId('scan-table')) byId('scan-table').addEventListener('change', updateTableInfo);

  ctx.on('adapters', () => {
    const select = byId('scan-adapter');
    if (!select) return;
    const previous = select.value;
    select.innerHTML = '';
    (store.state.adapters || []).forEach((adapter) => {
      const option = document.createElement('option');
      option.value = adapter.adapter + ':' + (adapter.frontend === undefined ? 0 : adapter.frontend);
      option.textContent = (adapter.name || 'Adaptador ' + adapter.adapter) + ' (' + adapter.adapter + '/' + (adapter.frontend || 0) + ')';
      select.appendChild(option);
    });
    if (previous) select.value = previous;
  });

  applyModeVisibility();
  loadTables();

  return {
    activate() {
      applyModeVisibility();
      loadTables();
    },
    deactivate() {},
    destroy() {
      stopPolling();
    },
  };
}

export default { init };
