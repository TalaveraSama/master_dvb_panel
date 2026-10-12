/**
 * TBS WebUI - vista de canales: búsqueda con debounce, filtros, renombrado,
 * borrado individual y múltiple, y descargas M3U / channels.conf.
 */

import { api } from '../api.js';
import * as store from '../store.js';
import { byId, debounce, esc, int, setBusy, setText } from '../utils.js';
import { toast, confirmDialog, promptDialog } from '../ui.js';

const PAGE_SIZES = [25, 50, 100, 200];

export function init(ctx) {
  const table = byId('ch-body');
  if (!table) return { activate() {}, deactivate() {}, destroy() {} };

  let offset = 0;
  let total = 0;
  let items = [];
  const selected = new Set();

  /** Construye el objeto de filtros actual a partir de la barra. */
  function filters() {
    const query = {
      q: byId('ch-search') ? byId('ch-search').value.trim() : '',
      type: byId('ch-type') ? byId('ch-type').value : '',
      fta: byId('ch-fta') ? byId('ch-fta').value : '',
      transponder_id: byId('ch-transponder') ? byId('ch-transponder').value : '',
      limit: byId('ch-limit') ? int(byId('ch-limit').value, 50) : 50,
      offset: offset,
    };
    Object.keys(query).forEach((key) => {
      if (query[key] === '' || query[key] === null) delete query[key];
    });
    return query;
  }

  /** Sincroniza los enlaces de descarga con los filtros activos. */
  function updateExportLinks() {
    const current = filters();
    delete current.limit;
    delete current.offset;
    const m3u = byId('ch-export-m3u');
    const conf = byId('ch-export-conf');
    if (m3u) m3u.href = api.exportM3UUrl(current);
    if (conf) conf.href = api.exportChannelsConfUrl(current);
  }

  function renderRows() {
    if (!items.length) {
      table.innerHTML = '<tr><td colspan="10" class="muted center">No hay canales que coincidan con los filtros.</td></tr>';
      return;
    }
    const fragment = document.createDocumentFragment();
    items.forEach((channel) => {
      const tr = document.createElement('tr');
      tr.dataset.id = channel.id;
      const pid = (value) => (value === null || value === undefined ? '—' : value);
      tr.innerHTML =
        '<td><input type="checkbox" class="row-check" data-id="' + esc(channel.id) + '" aria-label="Seleccionar ' +
        esc(channel.display_name || channel.name || 'canal') + '"' + (selected.has(channel.id) ? ' checked' : '') + '></td>' +
        '<td class="mono">' + esc(channel.id) + '</td>' +
        '<td><span class="cell-name">' + esc(channel.display_name || channel.name || '—') + '</span>' +
        (channel.custom_name ? ' <span class="badge badge-info" title="Nombre personalizado">renombrado</span>' : '') + '</td>' +
        '<td>' + esc(channel.provider || '—') + '</td>' +
        '<td><span class="badge">' + esc(channel.service_type_label || '—') + '</span></td>' +
        '<td class="mono">' + esc(pid(channel.service_id)) + '</td>' +
        '<td class="mono">' + esc(pid(channel.video_pid)) + ' / ' + esc(pid(channel.audio_pid)) + '</td>' +
        '<td class="mono small">' + esc(channel.delivery_system || '—') + ' · ' +
        esc(channel.frequency_khz ? (channel.frequency_khz / 1000).toFixed(3) + ' MHz' : '—') +
        (channel.polarization ? ' · ' + esc(channel.polarization) : '') + '</td>' +
        '<td>' + (channel.scrambled ? '<span class="badge badge-warn">Cifrado</span>' : '<span class="badge badge-ok">FTA</span>') + '</td>' +
        '<td class="actions">' +
        '<button type="button" class="btn btn-ghost btn-sm" data-action="rename" data-id="' + esc(channel.id) + '">Renombrar</button> ' +
        '<button type="button" class="btn btn-ghost btn-sm btn-danger-text" data-action="delete" data-id="' + esc(channel.id) + '">Borrar</button>' +
        '</td>';
      fragment.appendChild(tr);
    });
    table.innerHTML = '';
    table.appendChild(fragment);
    updateSelectionUi();
  }

  function updateSelectionUi() {
    const count = selected.size;
    setText('ch-selected-count', String(count));
    const bulk = byId('ch-bulk-delete');
    if (bulk) bulk.disabled = count === 0;
    const all = byId('ch-select-all');
    if (all) {
      all.checked = items.length > 0 && items.every((channel) => selected.has(channel.id));
      all.indeterminate = count > 0 && !all.checked;
    }
  }

  function renderPagination() {
    const limit = int(byId('ch-limit') ? byId('ch-limit').value : 50, 50);
    const from = total === 0 ? 0 : offset + 1;
    const to = Math.min(offset + limit, total);
    setText('ch-page-info', from + '–' + to + ' de ' + total);
    setText('ch-count', String(total));
    const prev = byId('ch-prev');
    const next = byId('ch-next');
    if (prev) prev.disabled = offset <= 0;
    if (next) next.disabled = offset + limit >= total;
  }

  /** Carga los canales con los filtros actuales. */
  async function load(options) {
    const opts = options || {};
    const status = byId('ch-status');
    if (status && !opts.silent) status.textContent = 'Cargando…';
    try {
      const data = await api.channels(filters());
      items = (data && data.items) || [];
      total = (data && data.total) || 0;
      if (opts.keepSelection !== true) {
        selected.clear();
      } else {
        // Elimina de la selección los canales que ya no están listados.
        const visible = new Set(items.map((channel) => channel.id));
        Array.from(selected).forEach((id) => {
          if (!visible.has(id)) selected.delete(id);
        });
      }
      renderRows();
      renderPagination();
      updateExportLinks();
      if (status) status.textContent = items.length + ' canales mostrados.';
    } catch (error) {
      if (status) status.textContent = error.detail || 'Error al cargar los canales.';
      toast(error.detail || 'No se pudieron cargar los canales.', 'error');
    }
  }

  /** Carga la lista de transponders para el filtro. */
  async function loadTransponders() {
    const select = byId('ch-transponder');
    if (!select) return;
    try {
      const data = await api.transponders({});
      const previous = select.value;
      select.innerHTML = '<option value="">Todos</option>';
      ((data && data.items) || []).forEach((tp) => {
        const option = document.createElement('option');
        option.value = tp.id;
        option.textContent =
          '#' + tp.id + ' · ' + tp.delivery_system + ' · ' + (tp.frequency_khz / 1000).toFixed(3) + ' MHz' +
          (tp.polarization ? ' ' + tp.polarization : '') + (tp.sat_position ? ' · ' + tp.sat_position : '');
        select.appendChild(option);
      });
      if (previous) select.value = previous;
    } catch (error) {
      /* el filtro simplemente queda vacío */
    }
  }

  // --- Eventos -------------------------------------------------------------
  const searchInput = byId('ch-search');
  if (searchInput) {
    searchInput.addEventListener(
      'input',
      debounce(() => {
        offset = 0;
        load();
      }, 300)
    );
  }
  ['ch-type', 'ch-fta', 'ch-transponder', 'ch-limit'].forEach((id) => {
    const node = byId(id);
    if (!node) return;
    node.addEventListener('change', () => {
      offset = 0;
      load();
    });
  });

  if (byId('ch-refresh')) byId('ch-refresh').addEventListener('click', () => load());
  if (byId('ch-prev')) {
    byId('ch-prev').addEventListener('click', () => {
      const limit = int(byId('ch-limit').value, 50);
      offset = Math.max(0, offset - limit);
      load();
    });
  }
  if (byId('ch-next')) {
    byId('ch-next').addEventListener('click', () => {
      const limit = int(byId('ch-limit').value, 50);
      offset = offset + limit;
      load();
    });
  }
  if (byId('ch-select-all')) {
    byId('ch-select-all').addEventListener('change', (event) => {
      if (event.target.checked) items.forEach((channel) => selected.add(channel.id));
      else selected.clear();
      renderRows();
    });
  }

  // Delegación de eventos para acciones de fila y casillas.
  table.addEventListener('click', async (event) => {
    const checkbox = event.target.closest('input.row-check');
    if (checkbox) {
      const id = int(checkbox.dataset.id, null);
      if (checkbox.checked) selected.add(id);
      else selected.delete(id);
      updateSelectionUi();
      return;
    }
    const button = event.target.closest('button[data-action]');
    if (!button) return;
    const id = int(button.dataset.id, null);
    const channel = items.find((item) => item.id === id);
    if (!channel) return;

    if (button.dataset.action === 'rename') {
      const name = await promptDialog({
        title: 'Renombrar canal',
        label: 'Nombre personalizado para "' + (channel.display_name || channel.name) + '"',
        value: channel.custom_name || channel.name || '',
        confirmLabel: 'Guardar',
      });
      if (name === null) return;
      try {
        await api.renameChannel(id, name);
        toast('Canal renombrado.', 'success');
        await load({ keepSelection: true });
      } catch (error) {
        toast(error.detail || 'No se pudo renombrar el canal.', 'error');
      }
      return;
    }

    if (button.dataset.action === 'delete') {
      const ok = await confirmDialog({
        title: 'Borrar canal',
        message: 'Se eliminará "' + (channel.display_name || channel.name) + '" de la base de datos.',
        confirmLabel: 'Borrar',
        danger: true,
      });
      if (!ok) return;
      try {
        await api.deleteChannel(id);
        selected.delete(id);
        toast('Canal borrado.', 'success');
        await load({ keepSelection: true });
      } catch (error) {
        toast(error.detail || 'No se pudo borrar el canal.', 'error');
      }
    }
  });

  const bulkDelete = byId('ch-bulk-delete');
  if (bulkDelete) {
    bulkDelete.addEventListener('click', async () => {
      if (!selected.size) return;
      const ok = await confirmDialog({
        title: 'Borrar canales seleccionados',
        message: 'Se eliminarán ' + selected.size + ' canales. Esta acción no se puede deshacer.',
        confirmLabel: 'Borrar ' + selected.size,
        danger: true,
      });
      if (!ok) return;
      setBusy(bulkDelete, true, 'Borrando…');
      try {
        const ids = Array.from(selected);
        const response = await api.deleteChannels(ids);
        toast('Borrados ' + ((response && response.deleted) !== undefined ? response.deleted : ids.length) + ' canales.', 'success');
        selected.clear();
        store.emit('transponders-changed');
        await load();
      } catch (error) {
        toast(error.detail || 'No se pudieron borrar los canales.', 'error');
      } finally {
        setBusy(bulkDelete, false);
      }
    });
  }

  const deleteAll = byId('ch-delete-all');
  if (deleteAll) {
    deleteAll.addEventListener('click', async () => {
      const ok = await confirmDialog({
        title: 'Borrar TODOS los canales',
        message: 'Se vaciará por completo la lista de canales. Esta acción no se puede deshacer.',
        confirmLabel: 'Borrar todo',
        danger: true,
      });
      if (!ok) return;
      setBusy(deleteAll, true, 'Borrando…');
      try {
        const response = await api.deleteAllChannels();
        toast('Borrados ' + ((response && response.deleted) || 0) + ' canales.', 'success');
        selected.clear();
        await load();
      } catch (error) {
        toast(error.detail || 'No se pudieron borrar los canales.', 'error');
      } finally {
        setBusy(deleteAll, false);
      }
    });
  }

  ctx.on('channels-changed', () => load());
  ctx.on('transponders-changed', () => loadTransponders());

  return {
    activate() {
      loadTransponders();
      load();
    },
    deactivate() {},
    destroy() {},
  };
}

export default { init };
