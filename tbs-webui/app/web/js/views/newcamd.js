/**
 * TBS WebUI - vista Newcamd.
 *
 * Gestiona **varias** cuentas de servidores Newcamd propios o autorizados:
 * alta, edición, duplicado, habilitar/deshabilitar, borrado, prueba de conexión
 * y conexión/desconexión, con estado en vivo (ECM, control words, CAID).
 *
 * AVISO: úsalo sólo con servidores de tu propiedad o para los que tengas
 * autorización expresa.
 */

import { api } from '../api.js';
import { toast, confirmDialog } from '../ui.js';
import { byId, esc, setBusy } from '../utils.js';

let refreshTimer = null;
let editingId = null;
let accounts = [];

const STATE_LABEL = {
  disconnected: ['Desconectado', 'badge'],
  connecting: ['Conectando…', 'badge badge-warn'],
  connected: ['Conectado', 'badge badge-ok'],
  logged_in: ['Sesión iniciada', 'badge badge-ok'],
  error: ['Error', 'badge badge-err'],
};

const DEFAULT_DESKEY = '0102030405060708091011121314';

// ---------------------------------------------------------------------------
// Pintado
// ---------------------------------------------------------------------------
function stateCell(item) {
  const status = item.status || {};
  const [label, css] = STATE_LABEL[status.state] || [status.state || '—', 'badge'];
  const detail = status.last_error || status.message || '';
  const title = detail ? ` title="${esc(detail)}"` : '';
  const dot = status.state === 'logged_in' ? '●' : status.state === 'error' ? '■' : '○';
  return `<span class="${css}"${title}>${dot} ${esc(label)}</span>`;
}

function renderSummary(list) {
  const host = byId('newcamd-summary');
  if (!host) return;
  if (!list.length) {
    host.textContent = 'sin cuentas';
    return;
  }
  const connected = list.filter((item) => (item.status || {}).state === 'logged_in').length;
  const enabled = list.filter((item) => item.enabled).length;
  host.textContent = `${list.length} cuenta(s) · ${enabled} activa(s) · ${connected} conectada(s)`;
}

function renderRows(list) {
  const tbody = byId('newcamd-rows');
  if (!tbody) return;
  if (!list.length) {
    tbody.innerHTML =
      '<tr><td colspan="6" class="muted">No hay cuentas Newcamd. Pulsa «＋ Nueva cuenta» para añadir la primera.</td></tr>';
    return;
  }
  tbody.innerHTML = list
    .map((item) => {
      const status = item.status || {};
      const connected = status.state === 'logged_in';
      const badges = [];
      if (item.auto_connect) badges.push('<span class="badge badge-ok" title="Conectar al arrancar">auto</span>');
      if (!item.enabled) badges.push('<span class="badge badge-warn">deshabilitada</span>');
      const cw = status.last_cw ? ` <span class="muted">${esc(String(status.last_cw).slice(0, 8))}…</span>` : '';
      const caid = status.caid ? ` <span class="muted">CAID ${Number(status.caid).toString(16)}</span>` : '';
      return `<tr data-id="${item.id}"${item.enabled ? '' : ' class="row-disabled"'}>
        <td>${stateCell(item)}</td>
        <td>
          <strong>${esc(item.name)}</strong> ${badges.join(' ')}
          ${item.notes ? `<div class="muted small">${esc(item.notes)}</div>` : ''}
        </td>
        <td class="mono small">${esc(item.host)}:${item.port}</td>
        <td class="mono small">${esc(item.username || '—')}${
          item.password_set ? '' : ' <span class="badge badge-warn" title="Sin contraseña guardada">sin clave</span>'
        }</td>
        <td class="mono small" title="ECM enviados / control words recibidos">${status.ecm_sent || 0} / ${
          status.cw_received || 0
        }${cw}${caid}</td>
        <td class="col-actions">
          <button type="button" class="btn btn-ghost btn-sm" data-action="${connected ? 'disconnect' : 'connect'}" ${
            item.enabled ? '' : 'disabled'
          }>${connected ? 'Desconectar' : 'Conectar'}</button>
          <button type="button" class="btn btn-ghost btn-sm" data-action="test">Probar</button>
          <button type="button" class="btn btn-ghost btn-sm" data-action="edit">Editar</button>
          <button type="button" class="btn btn-ghost btn-sm" data-action="duplicate" title="Duplicar cuenta">Duplicar</button>
          <button type="button" class="btn btn-ghost btn-sm" data-action="toggle" title="${
            item.enabled ? 'Deshabilitar' : 'Habilitar'
          }">${item.enabled ? 'Desactivar' : 'Activar'}</button>
          <button type="button" class="btn btn-ghost btn-sm btn-danger" data-action="delete">Borrar</button>
        </td>
      </tr>`;
    })
    .join('');
}

async function load({ quiet = false } = {}) {
  try {
    const data = await api.newcamdAccounts();
    accounts = data.items || [];
    const disabled = byId('newcamd-disabled');
    if (disabled) disabled.hidden = Boolean(data.enabled);
    const newButton = byId('newcamd-new');
    if (newButton) newButton.disabled = !data.enabled;
    renderSummary(accounts);
    renderRows(accounts);
  } catch (error) {
    if (!quiet) toast(error.detail || 'No se pudieron cargar las cuentas Newcamd', 'error');
    const tbody = byId('newcamd-rows');
    if (tbody) {
      tbody.innerHTML = `<tr><td colspan="6" class="muted">${esc(error.detail || 'Error al cargar')}</td></tr>`;
    }
  }
}

// ---------------------------------------------------------------------------
// Estado real de la tarjeta (OSCam)
// ---------------------------------------------------------------------------
function renderOscam(data) {
  const body = byId('oscam-body');
  const summary = byId('oscam-summary');
  const url = byId('oscam-url');
  if (url) url.textContent = data.url || '';
  if (summary) {
    summary.textContent = data.available
      ? `OSCam ${data.version || ''}${data.card_ready ? ' · tarjeta lista' : ''}`
      : 'OSCam no disponible';
  }
  if (!body) return;

  if (!data.available) {
    body.innerHTML = `<span class="badge badge-err">Sin conexión</span> ${esc(data.message || '')}`;
    return;
  }

  const readers = data.readers || [];
  const servicios = (data.uptime_s ? ` · en marcha ${Math.round(data.uptime_s / 60)} min` : '') +
    (data.revision ? ` · rev ${esc(String(data.revision).slice(0, 8))}` : '');
  const filas = readers.length
    ? readers
        .map((reader) => {
          const clase = reader.ok ? 'badge badge-ok' : 'badge badge-warn';
          const caid = reader.caid && reader.caid !== '0000' ? ` · CAID ${esc(reader.caid)}` : '';
          const idle =
            reader.idle_seconds !== null && reader.idle_seconds !== undefined
              ? ` · inactivo ${reader.idle_seconds} s`
              : '';
          return `<li><span class="${clase}">${esc(reader.status_text)}</span>
            <strong>${esc(reader.name)}</strong>
            <span class="mono small">${esc(reader.protocol)}${caid}${idle}</span></li>`;
        })
        .join('')
    : '<li class="muted">OSCam no tiene lectores configurados.</li>';

  body.innerHTML = `
    <div class="muted small">${esc(data.message || '')}${servicios}</div>
    <ul class="log-list" style="margin-top:0.5rem">${filas}</ul>`;
}

async function loadOscam({ quiet = false, force = false } = {}) {
  const boton = byId('oscam-refresh');
  if (boton && force) setBusy(boton, true, 'Consultando…');
  try {
    renderOscam(await api.newcamdOscam(force));
  } catch (error) {
    if (!quiet) toast(error.detail || 'No se pudo consultar OSCam', 'error');
    const body = byId('oscam-body');
    if (body) body.innerHTML = `<span class="badge badge-err">Error</span> ${esc(error.detail || '')}`;
  } finally {
    if (boton && force) setBusy(boton, false);
  }
}

// ---------------------------------------------------------------------------
// Editor
// ---------------------------------------------------------------------------
function openEditor(item = null) {
  editingId = item ? item.id : null;
  const card = byId('newcamd-editor');
  const title = byId('newcamd-editor-title');
  if (!card) return;
  card.hidden = false;
  if (title) title.textContent = item ? `Editar «${item.name}»` : 'Nueva cuenta';

  byId('nc-name').value = item ? item.name : '';
  byId('nc-host').value = item ? item.host : '';
  byId('nc-port').value = item ? item.port : 50001;
  byId('nc-user').value = item ? item.username || '' : '';
  const pass = byId('nc-pass');
  pass.value = '';
  pass.placeholder = item && item.password_set ? '(sin cambios)' : 'contraseña';
  byId('nc-deskey').value = item ? item.deskey : DEFAULT_DESKEY;
  byId('nc-enabled').checked = item ? Boolean(item.enabled) : true;
  byId('nc-auto').checked = item ? Boolean(item.auto_connect) : false;
  byId('nc-notes').value = item ? item.notes || '' : '';
  byId('nc-result').textContent = '';
  byId('nc-save').textContent = item ? 'Guardar cambios' : 'Crear cuenta';
  card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  byId('nc-name').focus();
}

function closeEditor() {
  editingId = null;
  const card = byId('newcamd-editor');
  if (card) card.hidden = true;
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
  if (!payload.name || !payload.host) {
    byId('nc-result').textContent = '✖ El nombre y el servidor son obligatorios';
    return;
  }
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
    closeEditor();
    await load();
  } catch (error) {
    byId('nc-result').textContent = '✖ ' + (error.detail || 'No se pudo guardar');
    toast(error.detail || 'No se pudo guardar la cuenta', 'error');
  } finally {
    setBusy(button, false);
  }
}

async function testForm() {
  const payload = formPayload();
  const button = byId('nc-test');
  const result = byId('nc-result');
  if (!payload.host) {
    result.textContent = '✖ Indica el servidor';
    return;
  }
  setBusy(button, true, 'Probando…');
  result.textContent = '';
  try {
    const body = {
      host: payload.host,
      port: payload.port,
      username: payload.username,
      deskey: payload.deskey,
    };
    if (payload.password) body.password = payload.password;
    if (editingId && !payload.password) body.id = editingId;
    const data = await api.newcamdTest(body);
    const text = data.ok ? '✔ ' + data.message : '✖ ' + data.message;
    result.textContent =
      text + (data.details && data.details.caid ? ` · CAID ${Number(data.details.caid).toString(16)}` : '');
    toast(data.message, data.ok ? 'ok' : 'error');
  } catch (error) {
    result.textContent = '✖ ' + (error.detail || 'Error de conexión');
    toast(error.detail || 'Error de conexión', 'error');
  } finally {
    setBusy(button, false);
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
  const action = button.dataset.action;
  const item = accounts.find((entry) => entry.id === id);

  if (action === 'edit') {
    if (item) openEditor(item);
    return;
  }

  if (action === 'duplicate') {
    if (!item) return;
    openEditor({
      ...item,
      id: null,
      name: `${item.name}-copia`,
      notes: item.notes || 'copia',
    });
    byId('nc-pass').placeholder = 'contraseña de la copia';
    return;
  }

  if (action === 'delete') {
    const ok = await confirmDialog({
      title: 'Borrar cuenta Newcamd',
      message: `¿Borrar la cuenta «${item ? item.name : id}»? Se cerrará su conexión.`,
      confirmLabel: 'Borrar',
    });
    if (!ok) return;
    try {
      await api.newcamdDelete(id);
      toast('Cuenta borrada', 'ok');
      if (editingId === id) closeEditor();
      await load();
    } catch (error) {
      toast(error.detail || 'No se pudo borrar', 'error');
    }
    return;
  }

  if (action === 'toggle') {
    if (!item) return;
    setBusy(button, true, item.enabled ? 'Desactivando…' : 'Activando…');
    try {
      await api.newcamdUpdate(id, {
        name: item.name,
        host: item.host,
        port: item.port,
        username: item.username || '',
        deskey: item.deskey,
        enabled: !item.enabled,
        auto_connect: item.auto_connect,
        notes: item.notes || null,
      });
      toast(item.enabled ? 'Cuenta deshabilitada' : 'Cuenta habilitada', 'ok');
      await load({ quiet: true });
    } catch (error) {
      toast(error.detail || 'No se pudo cambiar el estado', 'error');
    } finally {
      setBusy(button, false);
    }
    return;
  }

  if (action === 'connect' || action === 'disconnect') {
    setBusy(button, true, action === 'connect' ? 'Conectando…' : 'Cerrando…');
    try {
      if (action === 'connect') {
        const result = await api.newcamdConnect(id);
        const status = result.status || {};
        toast(
          status.state === 'logged_in'
            ? `Sesión iniciada${status.caid ? ` (CAID ${Number(status.caid).toString(16)})` : ''}`
            : status.message || 'No se pudo iniciar la sesión',
          status.state === 'logged_in' ? 'ok' : 'error',
        );
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
  const pendientes = accounts.filter((item) => item.enabled && (item.status || {}).state !== 'logged_in');
  if (!pendientes.length) {
    toast('No hay cuentas activas pendientes de conectar', 'info');
    return;
  }
  const button = byId('newcamd-connect-all');
  setBusy(button, true, 'Conectando…');
  let ok = 0;
  try {
    for (const item of pendientes) {
      try {
        await api.newcamdConnect(item.id);
        ok += 1;
      } catch (_) {
        /* se informa al final */
      }
    }
    toast(`${ok}/${pendientes.length} cuentas conectadas`, ok ? 'ok' : 'error');
  } finally {
    setBusy(button, false);
    await load({ quiet: true });
  }
}

async function disconnectAll() {
  const conectadas = accounts.filter((item) => (item.status || {}).state === 'logged_in');
  if (!conectadas.length) {
    toast('No hay cuentas conectadas', 'info');
    return;
  }
  const button = byId('newcamd-disconnect-all');
  setBusy(button, true, 'Cerrando…');
  try {
    for (const item of conectadas) {
      try {
        await api.newcamdDisconnect(item.id);
      } catch (_) {
        /* continuar */
      }
    }
    toast('Conexiones cerradas', 'ok');
  } finally {
    setBusy(button, false);
    await load({ quiet: true });
  }
}

// ---------------------------------------------------------------------------
// Ciclo de vida
// ---------------------------------------------------------------------------
export function init() {
  const bind = (id, event, handler) => {
    const node = byId(id);
    if (node) node.addEventListener(event, handler);
  };
  bind('newcamd-new', 'click', () => openEditor(null));
  bind('newcamd-refresh', 'click', () => load());
  bind('newcamd-connect-all', 'click', connectAll);
  bind('newcamd-disconnect-all', 'click', disconnectAll);
  bind('newcamd-form', 'submit', submit);
  bind('nc-test', 'click', testForm);
  bind('nc-cancel', 'click', closeEditor);
  bind('newcamd-rows', 'click', onRowClick);
  bind('oscam-refresh', 'click', () => loadOscam({ force: true }));

  load();
  loadOscam();
  // Estado en vivo (ECM/CW, conexiones y tarjeta) mientras la vista está visible.
  refreshTimer = window.setInterval(() => {
    const view = document.querySelector('section[data-view="newcamd"]');
    if (view && !view.hidden) {
      load({ quiet: true });
      loadOscam({ quiet: true });
    }
  }, 5000);
}

export function deactivate() {
  if (refreshTimer) {
    window.clearInterval(refreshTimer);
    refreshTimer = null;
  }
  closeEditor();
}

export default { init, deactivate };
