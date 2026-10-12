/**
 * TBS WebUI - vista Newcamd.
 *
 * Gestiona cuentas de servidores Newcamd propios o autorizados: alta, edición,
 * borrado, prueba de conexión (login), conexión/desconexión y estado en vivo
 * (ECM enviados, control words recibidos, bytes y último CW).
 *
 * AVISO: úsalo sólo con servidores de tu propiedad o para los que tengas
 * autorización expresa.
 */

import { api } from '../api.js';
import { toast, confirmDialog } from '../ui.js';
import { byId, esc, setBusy } from '../utils.js';

let refreshTimer = null;
let editingId = null;

const STATE_LABEL = {
  disconnected: ['Desconectado', 'badge', ''],
  connecting: ['Conectando…', 'badge badge-warn', ''],
  connected: ['Conectado', 'badge badge-ok', ''],
  logged_in: ['Sesión iniciada', 'badge badge-ok', ''],
  error: ['Error', 'badge badge-err', ''],
};

function stateCell(item) {
  const status = item.status || {};
  const [label, css] = STATE_LABEL[status.state] || [status.state || '—', 'badge', ''];
  const title = status.last_error || status.message || '';
  const hint = title ? ` title="${esc(title)}"` : '';
  const dot = status.state === 'logged_in' ? '●' : status.state === 'error' ? '■' : '○';
  return `<span class="${css}"${hint}>${dot} ${esc(label)}</span>`;
}

function renderRows(list) {
  const tbody = byId('newcamd-rows');
  if (!tbody) return;
  if (!list.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="muted">No hay cuentas Newcamd configuradas.</td></tr>';
    return;
  }
  tbody.innerHTML = list
    .map((item) => {
      const status = item.status || {};
      const connected = status.state === 'logged_in';
      return `<tr data-id="${item.id}">
        <td>${stateCell(item)}</td>
        <td>${esc(item.name)}${item.auto_connect ? ' <span class="muted small" title="Conectar al arrancar">auto</span>' : ''}${
          item.enabled ? '' : ' <span class="badge badge-warn">deshabilitada</span>'
        }</td>
        <td class="mono small">${esc(item.host)}:${item.port}</td>
        <td class="mono small">${esc(item.username || '—')}</td>
        <td class="mono small" title="ECM enviados / control words recibidos">${status.ecm_sent || 0} / ${
          status.cw_received || 0
        }${status.last_cw ? ` <span class="muted">${esc(status.last_cw.slice(0, 8))}…</span>` : ''}</td>
        <td class="col-actions">
          <button type="button" class="btn btn-ghost btn-sm" data-action="${connected ? 'disconnect' : 'connect'}">${
            connected ? 'Desconectar' : 'Conectar'
          }</button>
          <button type="button" class="btn btn-ghost btn-sm" data-action="test">Probar</button>
          <button type="button" class="btn btn-ghost btn-sm" data-action="edit">Editar</button>
          <button type="button" class="btn btn-ghost btn-sm btn-danger" data-action="delete">Borrar</button>
        </td>
      </tr>`;
    })
    .join('');
}

async function load({ quiet = false } = {}) {
  try {
    const data = await api.newcamdAccounts();
    const disabled = byId('newcamd-disabled');
    if (disabled) disabled.hidden = Boolean(data.enabled);
    const newButton = byId('newcamd-new');
    if (newButton) newButton.disabled = !data.enabled;
    renderRows(data.items || []);
  } catch (error) {
    if (!quiet) toast(error.detail || 'No se pudieron cargar las cuentas Newcamd', 'error');
    const tbody = byId('newcamd-rows');
    if (tbody) tbody.innerHTML = `<tr><td colspan="6" class="muted">${esc(error.detail || 'Error al cargar')}</td></tr>`;
  }
}

function openForm(item = null) {
  editingId = item ? item.id : null;
  const form = byId('newcamd-form');
  if (!form) return;
  form.hidden = false;
  byId('nc-name').value = item ? item.name : '';
  byId('nc-host').value = item ? item.host : '';
  byId('nc-port').value = item ? item.port : 50001;
  byId('nc-user').value = item ? item.username || '' : '';
  byId('nc-pass').value = '';
  byId('nc-pass').placeholder = item && item.password_set ? '(sin cambios)' : 'contraseña';
  byId('nc-deskey').value = item ? item.deskey : '0102030405060708091011121314';
  byId('nc-enabled').checked = item ? Boolean(item.enabled) : true;
  byId('nc-auto').checked = item ? Boolean(item.auto_connect) : false;
  byId('nc-notes').value = item ? item.notes || '' : '';
  byId('nc-result').textContent = '';
  byId('nc-save').textContent = item ? 'Guardar cambios' : 'Crear cuenta';
  byId('nc-name').focus();
}

function closeForm() {
  editingId = null;
  const form = byId('newcamd-form');
  if (form) form.hidden = true;
}

function formPayload() {
  return {
    name: byId('nc-name').value.trim(),
    host: byId('nc-host').value.trim(),
    port: Number(byId('nc-port').value) || 50001,
    username: byId('nc-user').value.trim(),
    password: byId('nc-pass').value || null,
    deskey: byId('nc-deskey').value.trim(),
    enabled: byId('nc-enabled').checked,
    auto_connect: byId('nc-auto').checked,
    notes: byId('nc-notes').value.trim() || null,
  };
}

async function submit(event) {
  event.preventDefault();
  const payload = formPayload();
  const button = byId('nc-save');
  setBusy(button, true, 'Guardando…');
  try {
    if (editingId) {
      await api.newcamdUpdate(editingId, payload);
      toast('Cuenta actualizada', 'ok');
    } else {
      await api.newcamdCreate(payload);
      toast('Cuenta creada', 'ok');
    }
    closeForm();
    await load();
  } catch (error) {
    byId('nc-result').textContent = error.detail || 'No se pudo guardar';
    toast(error.detail || 'No se pudo guardar la cuenta', 'error');
  } finally {
    setBusy(button, false);
  }
}

async function testForm() {
  const payload = formPayload();
  const button = byId('nc-test');
  const result = byId('nc-result');
  setBusy(button, true, 'Probando…');
  result.textContent = '';
  try {
    const body = Object.assign({}, payload);
    if (editingId && !body.password) delete body.password;
    if (editingId) body.id = editingId;
    delete body.enabled;
    delete body.auto_connect;
    delete body.notes;
    const data = await api.newcamdTest(body);
    result.textContent = data.ok ? '✔ ' + data.message : '✖ ' + data.message;
    toast(data.message, data.ok ? 'ok' : 'error');
  } catch (error) {
    result.textContent = '✖ ' + (error.detail || 'Error de conexión');
    toast(error.detail || 'Error de conexión', 'error');
  } finally {
    setBusy(button, false);
  }
}

async function onRowClick(event) {
  const button = event.target.closest('button[data-action]');
  if (!button) return;
  const row = event.target.closest('tr[data-id]');
  if (!row) return;
  const id = Number(row.dataset.id);
  const action = button.dataset.action;

  if (action === 'edit') {
    const data = await api.newcamdAccounts();
    const item = (data.items || []).find((entry) => entry.id === id);
    if (item) openForm(item);
    return;
  }
  if (action === 'delete') {
    const ok = await confirmDialog({
      title: 'Borrar cuenta Newcamd',
      message: '¿Quieres borrar esta cuenta? Se cerrará su conexión.',
      confirmLabel: 'Borrar',
    });
    if (!ok) return;
    try {
      await api.newcamdDelete(id);
      toast('Cuenta borrada', 'ok');
      await load();
    } catch (error) {
      toast(error.detail || 'No se pudo borrar', 'error');
    }
    return;
  }
  if (action === 'connect' || action === 'disconnect') {
    setBusy(button, true, action === 'connect' ? 'Conectando…' : 'Cerrando…');
    try {
      if (action === 'connect') {
        await api.newcamdConnect(id);
        toast('Sesión Newcamd iniciada', 'ok');
      } else {
        await api.newcamdDisconnect(id);
        toast('Conexión cerrada', 'ok');
      }
    } catch (error) {
      toast(error.detail || 'Operación fallida', 'error');
    } finally {
      setBusy(button, false);
      await load({ quiet: true });
    }
    return;
  }
  if (action === 'test') {
    setBusy(button, true, 'Probando…');
    try {
      const data = await api.newcamdTest({ id });
      toast(data.message, data.ok ? 'ok' : 'error');
      await load({ quiet: true });
    } catch (error) {
      toast(error.detail || 'No se pudo conectar', 'error');
    } finally {
      setBusy(button, false);
    }
  }
}

async function connectAll() {
  try {
    const data = await api.newcamdAccounts();
    const pendientes = (data.items || []).filter(
      (item) => item.enabled && item.status && item.status.state !== 'logged_in'
    );
    if (!pendientes.length) {
      toast('No hay cuentas pendientes de conectar', 'info');
      return;
    }
    let ok = 0;
    for (const item of pendientes) {
      try {
        await api.newcamdConnect(item.id);
        ok += 1;
      } catch (_) {
        /* se informa al final */
      }
    }
    toast(`${ok}/${pendientes.length} cuentas conectadas`, ok ? 'ok' : 'error');
    await load({ quiet: true });
  } catch (error) {
    toast(error.detail || 'Error', 'error');
  }
}

async function disconnectAll() {
  try {
    const data = await api.newcamdAccounts();
    for (const item of data.items || []) {
      if (item.status && item.status.state === 'logged_in') {
        try {
          await api.newcamdDisconnect(item.id);
        } catch (_) {
          /* continuar */
        }
      }
    }
    toast('Conexiones cerradas', 'ok');
    await load({ quiet: true });
  } catch (error) {
    toast(error.detail || 'Error', 'error');
  }
}

export function init() {
  const newButton = byId('newcamd-new');
  if (newButton) newButton.addEventListener('click', () => openForm(null));
  const refresh = byId('newcamd-refresh');
  if (refresh) refresh.addEventListener('click', () => load());
  const connectAllButton = byId('newcamd-connect-all');
  if (connectAllButton) connectAllButton.addEventListener('click', connectAll);
  const disconnectAllButton = byId('newcamd-disconnect-all');
  if (disconnectAllButton) disconnectAllButton.addEventListener('click', disconnectAll);
  const form = byId('newcamd-form');
  if (form) form.addEventListener('submit', submit);
  const test = byId('nc-test');
  if (test) test.addEventListener('click', testForm);
  const cancel = byId('nc-cancel');
  if (cancel) cancel.addEventListener('click', closeForm);
  const tbody = byId('newcamd-rows');
  if (tbody) tbody.addEventListener('click', onRowClick);

  load();
  // Refresco periódico del estado (ECM/CW cambian en vivo).
  refreshTimer = window.setInterval(() => {
    const view = document.querySelector('section[data-view="newcamd"]');
    if (view && !view.hidden) load({ quiet: true });
  }, 5000);
}

export function deactivate() {
  if (refreshTimer) {
    window.clearInterval(refreshTimer);
    refreshTimer = null;
  }
  closeForm();
}

export default { init, deactivate };
