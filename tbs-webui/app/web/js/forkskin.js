/**
 * forkskin.js - SPA del panel con el ASPECTO del panel del fork astra-webui.
 *
 * Usa las clases del CSS del fork (`css/fork/*.css`) y se alimenta de NUESTRA
 * API (cookies de sesion + CSRF), con las mismas funciones ya probadas:
 *
 *   - Panel:     estado, barras de señal, sintonizadores en uso con niveles.
 *   - Canales:   lista, filtro y asignacion de perfil Newcamd a cada canal.
 *   - Perfiles:  alta/edicion/borrado, probar TCP, aplicar configuracion.
 *   - DVB:       adaptadores y transponders, sintonizar eligiendo puerto.
 *   - Ajustes:   valores por defecto y cambio de contraseña.
 *
 * Sin dependencias externas: todo se resuelve con fetch y plantillas.
 */

const API = '/api';
let csrf = '';
let state = {
  user: null,
  view: 'dashboard',
  status: null,
  tuners: [],
  channels: [],
  profiles: [],
  adapters: [],
  settings: null,
  transponders: [],
};

// ---------------------------------------------------------------------------
// Utilidades
// ---------------------------------------------------------------------------
const $ = (id) => document.getElementById(id);
const el = (tag, cls, texto) => {
  const nodo = document.createElement(tag);
  if (cls) nodo.className = cls;
  if (texto !== undefined && texto !== null) nodo.textContent = texto;
  return nodo;
};
const esc = (valor) =>
  String(valor === null || valor === undefined ? '' : valor).replace(/[&<>"']/g, (caracter) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[caracter]);

function toast(mensaje, tipo = 'info') {
  const host = $('toasts');
  if (!host) return;
  const nodo = el('div', `alert alert--${tipo === 'ok' ? 'ok' : tipo === 'error' ? 'danger' : 'info'}`, mensaje);
  nodo.style.marginBottom = '0.5rem';
  host.appendChild(nodo);
  setTimeout(() => nodo.remove(), 4200);
}

async function api(ruta, opciones = {}) {
  const config = { credentials: 'same-origin', headers: { ...(opciones.headers || {}) } };
  if (opciones.body !== undefined) {
    config.method = opciones.method || 'POST';
    config.headers['Content-Type'] = 'application/json';
    config.body = JSON.stringify(opciones.body);
    if (csrf) config.headers['X-CSRF-Token'] = csrf;
  } else if (opciones.method) {
    config.method = opciones.method;
    if (opciones.method !== 'GET' && csrf) config.headers['X-CSRF-Token'] = csrf;
  }
  const respuesta = await fetch(API + ruta, config);
  if (respuesta.status === 401) {
    mostrarLogin();
    throw new Error('Sesión caducada');
  }
  const texto = await respuesta.text();
  const datos = texto ? JSON.parse(texto) : null;
  if (!respuesta.ok) {
    const detalle = datos && datos.detail ? (typeof datos.detail === 'string' ? datos.detail : JSON.stringify(datos.detail)) : `HTTP ${respuesta.status}`;
    throw new Error(detalle);
  }
  return datos;
}

// ---------------------------------------------------------------------------
// Sesión
// ---------------------------------------------------------------------------
function mostrarLogin() {
  $('app').hidden = true;
  $('login-screen').hidden = false;
}

function mostrarApp() {
  $('login-screen').hidden = true;
  $('app').hidden = false;
}

async function entrar(evento) {
  evento.preventDefault();
  const error = $('login-error');
  error.hidden = true;
  const boton = $('login-submit');
  boton.disabled = true;
  boton.textContent = 'Entrando…';
  try {
    const datos = await api('/auth/login', {
      body: { username: $('login-user').value.trim(), password: $('login-pass').value },
    });
    csrf = datos.csrf_token || '';
    state.user = datos.user || { username: $('login-user').value.trim() };
    $('user-badge').textContent = state.user.username || 'usuario';
    mostrarApp();
    await cargarTodo();
    pintar(state.view);
  } catch (fallo) {
    error.textContent = fallo.message || 'No se pudo entrar';
    error.hidden = false;
  } finally {
    boton.disabled = false;
    boton.textContent = 'Entrar';
  }
}

async function salir() {
  try { await api('/auth/logout', { method: 'POST' }); } catch (_) { /* da igual */ }
  csrf = '';
  state.user = null;
  mostrarLogin();
}

// ---------------------------------------------------------------------------
// Carga de datos
// ---------------------------------------------------------------------------
async function cargarTodo() {
  const tareas = [
    api('/status').then((d) => { state.status = d; }).catch(() => {}),
    api('/tuners').then((d) => { state.tuners = d.tuners || []; }).catch(() => {}),
    api('/channels').then((d) => { state.channels = d.items || []; }).catch(() => {}),
    api('/newcamd/profiles').then((d) => { state.profiles = d.items || []; }).catch(() => {}),
    api('/adapters').then((d) => { state.adapters = (d && d.items) || d || []; }).catch(() => {}),
    api('/settings').then((d) => { state.settings = d; }).catch(() => {}),
  ];
  await Promise.all(tareas);
  $('conn-badge').textContent = state.status && state.status.mock ? 'simulación' : 'en línea';
  $('mock-badge').hidden = !(state.status && state.status.mock);
  $('lock-badge').textContent = state.status && state.status.tuned ? 'sintonizado' : 'sin sintonizar';
  $('tuner-badge').textContent = `${state.tuners.length} sintonizador(es) en uso`;
}

// ---------------------------------------------------------------------------
// Navegación
// ---------------------------------------------------------------------------
const SECCIONES = [
  { id: 'dashboard', titulo: 'Panel', sub: 'Estado del receptor y señal en vivo', icono: '▤' },
  { id: 'tuners', titulo: 'Sintonizadores', sub: 'Puertos en uso, bloqueo y niveles', icono: '◎' },
  { id: 'channels', titulo: 'Canales', sub: 'Servicios escaneados y perfil Newcamd', icono: '≡' },
  { id: 'profiles', titulo: 'Perfiles Newcamd', sub: 'Credenciales cifradas para el motor', icono: '⚿' },
  { id: 'dvb', titulo: 'DVB', sub: 'Adaptadores y transponders', icono: '⊞' },
  { id: 'settings', titulo: 'Ajustes', sub: 'Valores por defecto y contraseña', icono: '⚙' },
];

function pintarNav() {
  const nav = $('nav');
  nav.innerHTML = '';
  SECCIONES.forEach((seccion) => {
    const boton = el('button', 'nav__link' + (state.view === seccion.id ? ' is-active' : ''));
    boton.type = 'button';
    boton.innerHTML = `<span class="nav__icon">${seccion.icono}</span><span>${esc(seccion.titulo)}</span>`;
    boton.addEventListener('click', () => pintar(seccion.id));
    nav.appendChild(boton);
  });
}

// ---------------------------------------------------------------------------
// Vistas
// ---------------------------------------------------------------------------
function tarjeta(titulo, extra) {
  const nodo = el('article', 'card');
  const cabeza = el('div', 'card__head');
  cabeza.appendChild(el('h2', 'card__title', titulo));
  if (extra) cabeza.appendChild(el('span', 'card__hint', extra));
  nodo.appendChild(cabeza);
  return nodo;
}

function barra(etiqueta, valor, unidad) {
  const fila = el('div', 'bar-row');
  const numero = valor === null || valor === undefined ? null : Number(valor);
  fila.innerHTML = `
    <div class="row row--tight"><span>${esc(etiqueta)}</span>
      <span class="num">${numero === null ? '—' : numero.toFixed(1) + ' ' + unidad}</span></div>
    <div class="meter"><div class="meter__fill" style="width:${numero === null ? 0 : Math.max(0, Math.min(100, numero))}%"></div></div>`;
  return fila;
}

function vistaPanel(host) {
  const stats = (state.status && state.status.stats) || {};
  const card = tarjeta('Señal', state.status && state.status.tuned ? 'sintonizado' : 'sin sintonizar');
  card.appendChild(barra('Nivel de señal', stats.signal_percent, '%'));
  card.appendChild(barra('SNR / calidad', stats.snr_db, 'dB'));
  const datos = el('div', 'kv');
  datos.innerHTML = `
    <div><span>LOCK</span><strong class="${stats.locked ? 'ok' : 'warn'}">${stats.locked ? 'SÍ' : 'NO'}</strong></div>
    <div><span>BER</span><strong>${stats.ber ?? '—'}</strong></div>
    <div><span>Nivel</span><strong>${stats.signal_dbm ?? '—'} dBm</strong></div>
    <div><span>Canales guardados</span><strong>${state.channels.length}</strong></div>
    <div><span>Perfiles Newcamd</span><strong>${state.profiles.length}</strong></div>
    <div><span>Sintonizadores en uso</span><strong>${state.tuners.length}</strong></div>`;
  card.appendChild(datos);
  host.appendChild(card);

  const card2 = tarjeta('Sintonizadores en uso', 'cada puerto queda bloqueado por su transponder');
  if (!state.tuners.length) {
    card2.appendChild(el('p', 'muted', 'Ninguno. Sintoniza un transponder y aparecerá aquí bloqueado.'));
  } else {
    state.tuners.forEach((tuner) => {
      const bloque = el('div', 'tuner');
      bloque.innerHTML = `
        <div class="tuner__head"><strong>adapter${tuner.adapter}/frontend${tuner.frontend}</strong>
          <span class="badge ${tuner.signal && tuner.signal.locked ? 'badge--ok' : 'badge--warn'}">${
            tuner.signal && tuner.signal.locked ? 'CON LOCK' : 'SIN LOCK'
          }</span></div>
        <div class="muted small">${esc(tuner.label || '')}</div>
        <div class="muted small">bloqueado por <strong>${esc(tuner.owner || '—')}</strong></div>`;
      bloque.appendChild(barra('Señal', tuner.signal && tuner.signal.signal_percent, '%'));
      const liberar = el('button', 'btn btn--ghost btn--sm', 'Liberar');
      liberar.type = 'button';
      liberar.addEventListener('click', async () => {
        try {
          await api(`/tuners/${tuner.adapter}/${tuner.frontend}/release`, { method: 'POST' });
          toast('Sintonizador liberado', 'ok');
          await cargarTodo();
          pintar('tuners');
        } catch (fallo) { toast(fallo.message, 'error'); }
      });
      bloque.appendChild(liberar);
      card2.appendChild(bloque);
    });
  }
  host.appendChild(card2);
}

function vistaSintonizadores(host) {
  const card = tarjeta('Puertos de sintonizador', `${state.tuners.length} en uso`);
  if (!state.tuners.length) {
    card.appendChild(el('p', 'muted', 'Ningún puerto en uso.'));
  } else {
    const tabla = el('table', 'table');
    tabla.innerHTML = `<thead><tr><th>Puerto</th><th>Transponder</th><th>Bloqueado por</th><th>Señal</th><th>SNR</th><th></th></tr></thead>`;
    const cuerpo = el('tbody');
    state.tuners.forEach((tuner) => {
      const fila = el('tr');
      const transponder = tuner.transponder || {};
      fila.innerHTML = `
        <td class="mono">adapter${tuner.adapter}/frontend${tuner.frontend}</td>
        <td>${esc(transponder.frequency_mhz || '—')} MHz ${esc(transponder.polarization || '')}</td>
        <td>${esc(tuner.owner || '—')}</td>
        <td>${tuner.signal && tuner.signal.signal_percent !== null ? tuner.signal.signal_percent + ' %' : '—'}</td>
        <td>${tuner.signal && tuner.signal.snr_db !== null ? tuner.signal.snr_db + ' dB' : '—'}</td>`;
      const celda = el('td');
      const liberar = el('button', 'btn btn--ghost btn--sm', 'Liberar');
      liberar.type = 'button';
      liberar.addEventListener('click', async () => {
        try {
          await api(`/tuners/${tuner.adapter}/${tuner.frontend}/release`, { method: 'POST' });
          toast('Liberado', 'ok');
          await cargarTodo();
          pintar('tuners');
        } catch (fallo) { toast(fallo.message, 'error'); }
      });
      celda.appendChild(liberar);
      fila.appendChild(celda);
      cuerpo.appendChild(fila);
    });
    tabla.appendChild(cuerpo);
    card.appendChild(tabla);
  }
  host.appendChild(card);
}

function vistaCanales(host) {
  const card = tarjeta('Canales', `${state.channels.length} servicios`);
  const filtro = el('input', 'input');
  filtro.placeholder = 'Buscar canal, proveedor o PID…';
  filtro.style.marginBottom = '0.6rem';
  card.appendChild(filtro);
  const contenedor = el('div');
  card.appendChild(contenedor);

  const pintarLista = () => {
    const texto = filtro.value.trim().toLowerCase();
    const lista = state.channels.filter((canal) =>
      !texto || `${canal.display_name || canal.name} ${canal.provider} ${canal.service_id}`.toLowerCase().includes(texto));
    if (!lista.length) {
      contenedor.innerHTML = '<p class="muted">Sin resultados.</p>';
      return;
    }
    const tabla = el('table', 'table');
    tabla.innerHTML = `<thead><tr><th>Canal</th><th>PNR</th><th>Tipo</th><th>CA</th><th>Perfil Newcamd</th></tr></thead>`;
    const cuerpo = el('tbody');
    lista.slice(0, 300).forEach((canal) => {
      const fila = el('tr');
      fila.innerHTML = `
        <td>${esc(canal.display_name || canal.name)}<div class="muted small">${esc(canal.provider || '')}</div></td>
        <td class="mono">${esc(canal.service_id)}</td>
        <td>${esc(canal.service_type_label || '')}</td>
        <td>${canal.scrambled ? '<span class="badge badge--warn">cifrado</span>' : '<span class="badge badge--ok">FTA</span>'}</td>`;
      const celda = el('td');
      const selector = el('select', 'input');
      selector.innerHTML = `<option value="">— sin CAM —</option>` +
        state.profiles.map((perfil) =>
          `<option value="${esc(perfil.name)}"${canal.cam_name === perfil.name ? ' selected' : ''}>${esc(perfil.name)}</option>`).join('');
      selector.addEventListener('change', async () => {
        try {
          await api(`/newcamd/services/${canal.id}/cam`, { body: { cam_name: selector.value || null } });
          canal.cam_name = selector.value || null;
          toast(selector.value ? `Perfil ${selector.value} asignado` : 'CAM quitado', 'ok');
        } catch (fallo) { toast(fallo.message, 'error'); }
      });
      celda.appendChild(selector);
      fila.appendChild(celda);
      cuerpo.appendChild(fila);
    });
    tabla.appendChild(cuerpo);
    contenedor.innerHTML = '';
    contenedor.appendChild(tabla);
  };
  filtro.addEventListener('input', pintarLista);
  pintarLista();
  host.appendChild(card);
}

function vistaPerfiles(host) {
  const card = tarjeta('Perfiles Newcamd', 'las contraseñas y la DES key se guardan cifradas');
  const tabla = el('table', 'table');
  tabla.innerHTML = `<thead><tr><th>Nombre</th><th>Servidor</th><th>Usuario</th><th>Secretos</th><th></th></tr></thead>`;
  const cuerpo = el('tbody');
  state.profiles.forEach((perfil) => {
    const fila = el('tr');
    fila.innerHTML = `
      <td><strong>${esc(perfil.name)}</strong><div class="muted small">timeout ${esc(perfil.timeout ?? 8)} s · EMM ${
        perfil.disable_emm ? 'desactivado' : 'activo'}</div></td>
      <td class="mono">${esc(perfil.host)}:${esc(perfil.port)}</td>
      <td class="mono">${esc(perfil.username)}</td>
      <td>${perfil.password_configured ? '<span class="badge badge--ok">contraseña</span>' : '<span class="badge badge--warn">sin contraseña</span>'}
          ${perfil.des_key_configured ? '<span class="badge badge--ok">DES key</span>' : ''}</td>`;
    const celda = el('td');
    const probar = el('button', 'btn btn--ghost btn--sm', 'Probar');
    probar.type = 'button';
    probar.addEventListener('click', async () => {
      try {
        const datos = await api(`/newcamd/profiles/${perfil.id}/test`, { method: 'POST' });
        toast(datos.reachable ? `TCP accesible (${datos.latency_ms} ms)` : `No accesible: ${datos.detail}`, datos.reachable ? 'ok' : 'error');
      } catch (fallo) { toast(fallo.message, 'error'); }
    });
    const borrar = el('button', 'btn btn--ghost btn--sm', 'Borrar');
    borrar.type = 'button';
    borrar.addEventListener('click', async () => {
      try {
        await api(`/newcamd/profiles/${perfil.id}`, { method: 'DELETE' });
        toast('Perfil borrado', 'ok');
        await cargarTodo();
        pintar('profiles');
      } catch (fallo) { toast(fallo.message, 'error'); }
    });
    celda.appendChild(probar);
    celda.appendChild(borrar);
    fila.appendChild(celda);
    cuerpo.appendChild(fila);
  });
  tabla.appendChild(cuerpo);
  card.appendChild(tabla);
  host.appendChild(card);

  // Alta de perfil
  const alta = tarjeta('Nuevo perfil', 'mismos campos que el newcamd nativo');
  const rejilla = el('div', 'grid-2');
  const campos = [
    ['nombre', 'Nombre', 'text', 'newcamd_main'],
    ['host', 'Host (IP o DNS)', 'text', '192.168.1.50'],
    ['puerto', 'Puerto', 'number', '15000'],
    ['usuario', 'Usuario', 'text', 'usuario'],
    ['password', 'Contraseña', 'password', ''],
    ['deskey', 'DES key (28 hex)', 'text', '0102030405060708091011121314'],
    ['timeout', 'Timeout (s)', 'number', '8'],
  ];
  const entradas = {};
  campos.forEach(([clave, etiqueta, tipo, marcador]) => {
    const grupo = el('div', 'field');
    grupo.innerHTML = `<label class="field__label">${esc(etiqueta)}</label>`;
    const entrada = el('input', 'input');
    entrada.type = tipo;
    if (marcador) entrada.placeholder = marcador;
    if (clave === 'deskey') entrada.value = '0102030405060708091011121314';
    if (clave === 'timeout') entrada.value = '8';
    if (clave === 'puerto') entrada.value = '15000';
    entradas[clave] = entrada;
    grupo.appendChild(entrada);
    rejilla.appendChild(grupo);
  });
  alta.appendChild(rejilla);
  const crear = el('button', 'btn btn--primary', 'Crear perfil');
  crear.type = 'button';
  crear.addEventListener('click', async () => {
    try {
      await api('/newcamd/profiles', {
        body: {
          name: entradas.nombre.value.trim(),
          host: entradas.host.value.trim(),
          port: Number(entradas.puerto.value) || 15000,
          username: entradas.usuario.value.trim(),
          password: entradas.password.value,
          deskey: entradas.deskey.value.trim(),
          timeout_s: Number(entradas.timeout.value) || 8,
          disable_emm: true,
          enabled: true,
          auto_connect: false,
        },
      });
      toast('Perfil creado', 'ok');
      await cargarTodo();
      pintar('profiles');
    } catch (fallo) { toast(fallo.message, 'error'); }
  });
  alta.appendChild(crear);

  const aplicar = el('button', 'btn btn--ghost', 'Aplicar configuración al motor');
  aplicar.type = 'button';
  aplicar.style.marginLeft = '0.5rem';
  aplicar.addEventListener('click', async () => {
    try {
      const datos = await api('/newcamd/config/apply', { method: 'POST' });
      toast(`Configuración instalada en ${datos.path}`, 'ok');
    } catch (fallo) { toast(fallo.message, 'error'); }
  });
  alta.appendChild(aplicar);
  host.appendChild(alta);
}

function vistaDvb(host) {
  const card = tarjeta('Adaptadores', `${state.adapters.length} detectados`);
  const tabla = el('table', 'table');
  tabla.innerHTML = `<thead><tr><th>Adaptador</th><th>Frontend</th><th>Nombre</th><th>Sistemas</th></tr></thead>`;
  const cuerpo = el('tbody');
  state.adapters.forEach((adaptador) => {
    const fila = el('tr');
    fila.innerHTML = `
      <td class="mono">${esc(adaptador.adapter)}</td>
      <td class="mono">${esc(adaptador.frontend ?? 0)}</td>
      <td>${esc(adaptador.name || '—')}</td>
      <td>${esc((adaptador.delivery_systems || []).join(', '))}</td>`;
    cuerpo.appendChild(fila);
  });
  tabla.appendChild(cuerpo);
  card.appendChild(tabla);
  host.appendChild(card);
}

function vistaAjustes(host) {
  const card = tarjeta('Contraseña', 'cambia la contraseña del administrador');
  const rejilla = el('div', 'grid-2');
  const actual = el('input', 'input'); actual.type = 'password'; actual.placeholder = 'actual';
  const nueva = el('input', 'input'); nueva.type = 'password'; nueva.placeholder = 'nueva';
  [['Contraseña actual', actual], ['Contraseña nueva', nueva]].forEach(([etiqueta, entrada]) => {
    const grupo = el('div', 'field');
    grupo.innerHTML = `<label class="field__label">${esc(etiqueta)}</label>`;
    grupo.appendChild(entrada);
    rejilla.appendChild(grupo);
  });
  card.appendChild(rejilla);
  const guardar = el('button', 'btn btn--primary', 'Cambiar contraseña');
  guardar.type = 'button';
  guardar.addEventListener('click', async () => {
    try {
      await api('/auth/password', { body: { current_password: actual.value, new_password: nueva.value } });
      toast('Contraseña cambiada. Vuelve a entrar.', 'ok');
      setTimeout(salir, 1500);
    } catch (fallo) { toast(fallo.message, 'error'); }
  });
  card.appendChild(guardar);
  host.appendChild(card);
}

// ---------------------------------------------------------------------------
// Enrutado
// ---------------------------------------------------------------------------
const VISTAS = {
  dashboard: vistaPanel,
  tuners: vistaSintonizadores,
  channels: vistaCanales,
  profiles: vistaPerfiles,
  dvb: vistaDvb,
  settings: vistaAjustes,
};

async function pintar(vista) {
  state.view = vista;
  const seccion = SECCIONES.find((entrada) => entrada.id === vista) || SECCIONES[0];
  $('view-title').textContent = seccion.titulo;
  $('view-subtitle').textContent = seccion.sub;
  pintarNav();
  const host = $('view');
  host.innerHTML = '<p class="muted">Cargando…</p>';
  try {
    await cargarTodo();
  } catch (_) { /* se pinta con lo que haya */ }
  host.innerHTML = '';
  (VISTAS[vista] || vistaPanel)(host);
  location.hash = `#/${vista}`;
}

// ---------------------------------------------------------------------------
// Arranque
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
  $('login-form').addEventListener('submit', entrar);
  $('logout-btn').addEventListener('click', salir);
  $('menu-btn').addEventListener('click', () => $('sidebar').classList.toggle('is-open'));

  const inicial = (location.hash || '').replace('#/', '') || 'dashboard';
  state.view = VISTAS[inicial] ? inicial : 'dashboard';

  // Si ya hay sesión (cookie), se entra directo
  api('/auth/me')
    .then(async (yo) => {
      state.user = (yo && (yo.user || yo)) || { username: 'admin' };
      $('user-badge').textContent = state.user.username || 'admin';
      mostrarApp();
      await cargarTodo();
      pintar(state.view);
    })
    .catch(() => mostrarLogin());
});
