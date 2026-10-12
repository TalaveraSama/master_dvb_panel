/**
 * TBS WebUI - vista de transponders: listado con búsqueda.
 */

import { api } from '../api.js';
import { byId, debounce, esc, setText } from '../utils.js';
import { toast } from '../ui.js';

export function init(ctx) {
  const body = byId('tp-body');
  if (!body) return { activate() {}, deactivate() {}, destroy() {} };

  async function load() {
    const status = byId('tp-status');
    if (status) status.textContent = 'Cargando…';
    const query = { q: byId('tp-search') ? byId('tp-search').value.trim() : '' };
    if (!query.q) delete query.q;
    try {
      const data = await api.transponders(query);
      const items = (data && data.items) || [];
      setText('tp-count', String((data && data.total) || 0));
      if (!items.length) {
        body.innerHTML = '<tr><td colspan="9" class="muted center">No hay transponders registrados.</td></tr>';
      } else {
        const fragment = document.createDocumentFragment();
        items.forEach((tp) => {
          const tr = document.createElement('tr');
          const value = (input) => (input === null || input === undefined ? '—' : input);
          tr.innerHTML =
            '<td class="mono">' + esc(tp.id) + '</td>' +
            '<td>' + esc(value(tp.delivery_system)) + '</td>' +
            '<td class="mono">' + esc(tp.frequency_khz ? (tp.frequency_khz / 1000).toFixed(3) + ' MHz' : '—') + '</td>' +
            '<td>' + esc(value(tp.polarization)) + '</td>' +
            '<td class="mono">' + esc(tp.symbol_rate_ksps ? tp.symbol_rate_ksps + ' ksps' : '—') + '</td>' +
            '<td>' + esc(value(tp.fec)) + '</td>' +
            '<td>' + esc(value(tp.modulation)) + '</td>' +
            '<td>' + esc(value(tp.sat_position)) + '</td>' +
            '<td class="mono">' + esc(value(tp.services_count)) + '</td>';
          fragment.appendChild(tr);
        });
        body.innerHTML = '';
        body.appendChild(fragment);
      }
      if (status) status.textContent = items.length + ' transponders mostrados.';
    } catch (error) {
      if (status) status.textContent = error.detail || 'Error al cargar los transponders.';
      toast(error.detail || 'No se pudieron cargar los transponders.', 'error');
    }
  }

  const search = byId('tp-search');
  if (search) search.addEventListener('input', debounce(load, 300));
  if (byId('tp-refresh')) byId('tp-refresh').addEventListener('click', load);

  ctx.on('transponders-changed', load);

  return {
    activate() {
      load();
    },
    deactivate() {},
    destroy() {},
  };
}

export default { init };
