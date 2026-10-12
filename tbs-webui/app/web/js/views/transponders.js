/**
 * TBS WebUI - vista de Transponders.
 *
 * Permite:
 *  - listar y buscar los transponders guardados,
 *  - **editar** sus parametros (frecuencia, symbol rate, polaridad, FEC,
 *    modulacion, roll-off, pilot, stream/PLP, satelite),
 *  - **elegir el puerto de sintonizador** (adaptador/frontend) y sintonizar,
 *  - borrar el transponder junto con sus canales.
 */

import { api } from '../api.js';
import * as store from '../store.js';
import { toast, confirmDialog } from '../ui.js';
import { byId, debounce, esc, int, setBusy, setText } from '../utils.js';

let items = [];
let editingId = null;

// ---------------------------------------------------------------------------
// Puerto de sintonizador
// ---------------------------------------------------------------------------
function fillAdapterSelect() {
  const select = byId('tp-adapter');
  if (!select) return;
  const adapters = store.state.adapters || [];
  const previous = select.value;
  select.innerHTML = '';
  if (!adapters.length) {
    const option = document.createElement('option');
    option.value = '';
    option.textContent = 'Sin adaptadores';
    select.appendChild(option);
    updateFrontendInfo();
    return;
  }
  adapters.forEach((adapter) => {
    const frontend = adapter.frontend === undefined ? 0 : adapter.frontend;
    const option = document.createElement('option');
    option.value = adapter.adapter + ':' + frontend;
    option.textContent =
      'adapter' + adapter.adapter + '/frontend' + frontend + (adapter.name ? ' — ' + adapter.name : '');
    select.appendChild(option);
  });
  if (previous && [...select.options].some((option) => option.value === previous)) {
    select.value = previous;
  }
  updateFrontendInfo();
}

function selectedPort() {
  const select = byId('tp-adapter');
  const value = select && select.value ? select.value : '0:0';
  const parts = value.split(':');
  return { adapter: int(parts[0], 0), frontend: int(parts[1], 0) };
}

function updateFrontendInfo() {
  const host = byId('tp-frontend-info');
  if (!host) return;
  const { adapter, frontend } = selectedPort();
  const info = (store.state.adapters || []).find(
    (entry) => entry.adapter === adapter && (entry.frontend === undefined ? 0 : entry.frontend) === frontend
  );
  host.textContent = info
    ? `${info.name || 'frontend'} · ${(info.delivery_systems || []).join('/') || 'sistemas n/d'}`
    : `adapter${adapter}/frontend${frontend}`;
}

function tuneParams() {
  const settings = store.state.settings || {};
  const lnb = settings.lnb || {};
  const { adapter, frontend } = selectedPort();
  return {
    adapter: adapter,
    frontend: frontend,
    use_lnb: true,
    lnb_low_khz: int(lnb.low_khz, 9750),
    lnb_high_khz: int(lnb.high_khz, 10600),
    lnb_threshold_khz: int(lnb.threshold_khz, 11700),
    lock_timeout_s: Number((settings.scan || {}).lock_timeout_s || 5),
  };
}

// ---------------------------------------------------------------------------
// Listado
// ---------------------------------------------------------------------------
function value(value, suffix = '') {
  return value === null || value === undefined || value === '' ? '—' : `${value}${suffix}`;
}

function render() {
  const body = byId('tp-body');
  if (!body) return;
  if (!items.length) {
    body.innerHTML = '<tr><td colspan="11" class="muted center">No hay transponders registrados.</td></tr>';
    return;
  }
  body.innerHTML = items
    .map((tp) => {
      const signal =
        tp.last_signal_percent === null || tp.last_signal_percent === undefined
          ? '—'
          : `${Number(tp.last_signal_percent).toFixed(1)} %${
              tp.last_snr_db ? ` / ${Number(tp.last_snr_db).toFixed(1)} dB` : ''
            }`;
      return `<tr data-id="${tp.id}">
        <td class="mono">${esc(tp.id)}</td>
        <td>${esc(tp.delivery_system)}</td>
        <td class="mono">${esc((tp.frequency_khz / 1000).toFixed(3))} MHz</td>
        <td>${esc(value(tp.polarization))}</td>
        <td class="mono">${esc(value(tp.symbol_rate_ksps, ' ksps'))}</td>
        <td>${esc(value(tp.fec))}</td>
        <td>${esc(value(tp.modulation))}</td>
        <td>${esc(value(tp.sat_position))}</td>
        <td class="mono">${esc(value(tp.services_count))}</td>
        <td class="mono">${esc(signal)}</td>
        <td class="col-actions">
          <button type="button" class="btn btn-ghost btn-sm" data-action="tune">Sintonizar</button>
          <button type="button" class="btn btn-ghost btn-sm" data-action="edit">Editar</button>
          <button type="button" class="btn btn-ghost btn-sm btn-danger" data-action="delete">Borrar</button>
        </td>
      </tr>`;
    })
    .join('');
}

async function load() {
  const status = byId('tp-status');
  if (status) status.textContent = 'Cargando…';
  const query = {};
  const search = byId('tp-search');
  if (search && search.value.trim()) query.q = search.value.trim();
  try {
    const data = await api.transponders(query);
    items = (data && data.items) || [];
    setText('tp-count', String((data && data.total) || 0));
    render();
    if (status) status.textContent = items.length ? `${items.length} mostrados.` : 'Sin resultados.';
  } catch (error) {
    if (status) status.textContent = error.detail || 'No se pudieron cargar los transponders.';
    toast(error.detail || 'No se pudieron cargar los transponders.', 'error');
  }
}

// ---------------------------------------------------------------------------
// Editor
// ---------------------------------------------------------------------------
function setValue(id, content) {
  const node = byId(id);
  if (node) node.value = content === null || content === undefined ? '' : content;
}

function openEditor(tp) {
  editingId = tp.id;
  const card = byId('tp-editor');
  if (!card) return;
  card.hidden = false;
  setText('tp-editor-title', `Editar transponder #${tp.id}`);
  setText('tp-editor-sub', `${(tp.frequency_khz / 1000).toFixed(3)} MHz · ${tp.services_count || 0} servicio(s)`);
  setValue('tp-f-system', tp.delivery_system || 'DVB-S2');
  setValue('tp-f-frequency', tp.frequency_khz);
  setValue('tp-f-symbol', tp.symbol_rate_ksps);
  setValue('tp-f-pol', tp.polarization || '');
  setValue('tp-f-fec', tp.fec || '');
  setValue('tp-f-mod', tp.modulation || '');
  setValue('tp-f-sat', tp.sat_position || '');
  setValue('tp-f-rolloff', tp.rolloff || '');
  setValue('tp-f-pilot', tp.pilot || '');
  setValue('tp-f-inversion', tp.inversion || 'AUTO');
  setValue('tp-f-stream', tp.stream_id);
  setValue('tp-f-plp', tp.plp_id);
  setText('tp-result', '');
  card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  byId('tp-f-frequency').focus();
}

function closeEditor() {
  editingId = null;
  const card = byId('tp-editor');
  if (card) card.hidden = true;
}

function formPayload() {
  const payload = {
    delivery_system: byId('tp-f-system').value,
    frequency_khz: int(byId('tp-f-frequency').value, 0),
    inversion: byId('tp-f-inversion').value.trim() || 'AUTO',
  };
  const optional = {
    symbol_rate_ksps: int(byId('tp-f-symbol').value, null),
    polarization: byId('tp-f-pol').value || null,
    fec: byId('tp-f-fec').value.trim() || null,
    modulation: byId('tp-f-mod').value.trim() || null,
    sat_position: byId('tp-f-sat').value.trim() || null,
    rolloff: byId('tp-f-rolloff').value.trim() || null,
    pilot: byId('tp-f-pilot').value.trim() || null,
    stream_id: int(byId('tp-f-stream').value, null),
    plp_id: int(byId('tp-f-plp').value, null),
  };
  Object.keys(optional).forEach((key) => {
    if (optional[key] !== null) payload[key] = optional[key];
  });
  return payload;
}

async function save(event) {
  event.preventDefault();
  if (!editingId) return;
  const payload = formPayload();
  if (!payload.frequency_khz) {
    setText('tp-result', '✖ La frecuencia es obligatoria');
    return;
  }
  const button = byId('tp-save');
  setBusy(button, true, 'Guardando…');
  try {
    const updated = await api.updateTransponder(editingId, payload);
    toast('Transponder actualizado', 'ok');
    closeEditor();
    await load();
    if (store.emit) store.emit('transponders:changed', updated);
  } catch (error) {
    setText('tp-result', '✖ ' + (error.detail || 'No se pudo guardar'));
    toast(error.detail || 'No se pudo guardar el transponder', 'error');
  } finally {
    setBusy(button, false);
  }
}

async function tuneEditing() {
  if (!editingId) return;
  const button = byId('tp-tune');
  const { adapter, frontend } = selectedPort();
  setBusy(button, true, 'Sintonizando…');
  setText('tp-result', '');
  try {
    const data = await api.tuneTransponder(editingId, tuneParams());
    const stats = data.stats || {};
    const text = data.locked
      ? `✔ LOCK en adapter${adapter}/frontend${frontend} · ${stats.signal_percent ?? '—'} % · ${stats.snr_db ?? '—'} dB`
      : `✖ Sin LOCK en adapter${adapter}/frontend${frontend}${
          stats.signal_percent !== null && stats.signal_percent !== undefined
            ? ` (${stats.signal_percent} %)`
            : ''
        }`;
    setText('tp-result', text + (data.detail ? ' · ' + data.detail : ''));
    toast(data.locked ? 'Sintonizado con LOCK' : 'Sintonizado sin LOCK', data.locked ? 'ok' : 'error');
    if (store.emit) store.emit('status:refresh', data);
  } catch (error) {
    setText('tp-result', '✖ ' + (error.detail || 'Error al sintonizar'));
    toast(error.detail || 'Error al sintonizar', 'error');
  } finally {
    setBusy(button, false);
    await load();
  }
}

// ---------------------------------------------------------------------------
// Acciones de fila
// ---------------------------------------------------------------------------
async function onRowClick(event) {
  const button = event.target.closest('button[data-action]');
  if (!button) return;
  const row = event.target.closest('tr[data-id]');
  if (!row) return;
  const id = Number(row.dataset.id);
  const tp = items.find((entry) => entry.id === id);
  const action = button.dataset.action;

  if (action === 'edit') {
    if (tp) openEditor(tp);
    return;
  }

  if (action === 'tune') {
    const { adapter, frontend } = selectedPort();
    setBusy(button, true, 'Sintonizando…');
    try {
      const data = await api.tuneTransponder(id, tuneParams());
      const stats = data.stats || {};
      toast(
        data.locked
          ? `LOCK en adapter${adapter}/frontend${frontend} · ${stats.signal_percent} % · ${stats.snr_db} dB`
          : `Sin LOCK en adapter${adapter}/frontend${frontend}`,
        data.locked ? 'ok' : 'error',
      );
      if (store.emit) store.emit('status:refresh', data);
    } catch (error) {
      toast(error.detail || 'Error al sintonizar', 'error');
    } finally {
      setBusy(button, false);
      await load();
    }
    return;
  }

  if (action === 'delete') {
    const ok = await confirmDialog({
      title: 'Borrar transponder',
      message: `¿Borrar el transponder #${id}${tp ? ` y sus ${tp.services_count} canal(es)` : ''}?`,
      confirmLabel: 'Borrar',
    });
    if (!ok) return;
    setBusy(button, true, 'Borrando…');
    try {
      await api.deleteTransponder(id);
      toast('Transponder borrado', 'ok');
      if (editingId === id) closeEditor();
      await load();
    } catch (error) {
      toast(error.detail || 'No se pudo borrar', 'error');
    } finally {
      setBusy(button, false);
    }
  }
}

// ---------------------------------------------------------------------------
// Ciclo de vida
// ---------------------------------------------------------------------------
export function init(ctx) {
  const body = byId('tp-body');
  if (!body) return { activate() {}, deactivate() {}, destroy() {} };

  const search = byId('tp-search');
  if (search) search.addEventListener('input', debounce(load, 300));
  const refresh = byId('tp-refresh');
  if (refresh) refresh.addEventListener('click', () => load());
  const adapter = byId('tp-adapter');
  if (adapter) adapter.addEventListener('change', updateFrontendInfo);
  const form = byId('tp-form');
  if (form) form.addEventListener('submit', save);
  const tuneButton = byId('tp-tune');
  if (tuneButton) tuneButton.addEventListener('click', tuneEditing);
  const cancel = byId('tp-cancel');
  if (cancel) cancel.addEventListener('click', closeEditor);
  body.addEventListener('click', onRowClick);

  // Los adaptadores pueden llegar despues del arranque de la vista.
  if (ctx && typeof ctx.on === 'function') {
    ctx.on('adapters', () => fillAdapterSelect());
  }
  fillAdapterSelect();

  return {
    activate() {
      fillAdapterSelect();
      load();
    },
    deactivate() {
      closeEditor();
    },
    destroy() {},
  };
}

export default { init };
