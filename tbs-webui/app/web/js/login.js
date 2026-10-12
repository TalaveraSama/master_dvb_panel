/**
 * TBS WebUI - lógica de la pantalla de acceso (/login).
 *
 * Cubre los tres estados posibles:
 *  - Ya hay sesión -> redirección al panel.
 *  - No hay usuarios -> formulario de creación del administrador (must_setup).
 *  - Caso normal    -> formulario de usuario/contraseña con mensajes para
 *                      401 (credenciales), 423 (bloqueado) y 429 (límite).
 */

import { api, setCsrfToken } from './api.js';
import { byId, setBusy, show } from './utils.js';
import { toast, initRevealButtons } from './ui.js';

/** Muestra un mensaje de error en el formulario indicado. */
function setError(panel, message) {
  const box = byId(panel + '-error');
  if (!box) return;
  if (!message) {
    box.hidden = true;
    box.textContent = '';
    return;
  }
  box.hidden = false;
  box.textContent = message;
}

/** Traduce un error de la API a un mensaje claro para el usuario. */
function messageFor(error, context) {
  const status = error && error.status;
  if (status === 401) return 'Usuario o contraseña incorrectos.';
  if (status === 423) {
    const retry = error.extra && error.extra.retryAfter;
    const seconds = retry ? parseInt(retry, 10) : NaN;
    if (Number.isFinite(seconds)) {
      return 'Cuenta bloqueada temporalmente por intentos fallidos. Vuelve a intentarlo en ' + Math.ceil(seconds / 60) + ' min.';
    }
    return 'Cuenta bloqueada temporalmente por intentos fallidos. Espera unos minutos.';
  }
  if (status === 429) return 'Demasiados intentos seguidos. Espera unos segundos antes de reintentar.';
  if (status === 409) return 'Ya existe un usuario administrador. Inicia sesión.';
  if (status === 400 || status === 422) return (error && error.detail) || 'Revisa los datos introducidos.';
  if (status === 0) return 'No se pudo conectar con el servidor.';
  if (context === 'setup') return (error && error.detail) || 'No se pudo crear el administrador.';
  return (error && error.detail) || 'No se pudo iniciar sesión.';
}

/** Destino tras el acceso (respeta ?next=). */
function nextUrl() {
  const params = new URLSearchParams(window.location.search);
  const next = params.get('next');
  if (next && next.startsWith('/') && !next.startsWith('//')) return next;
  return '/';
}

async function handleLogin(event) {
  event.preventDefault();
  setError('login', '');
  const submit = byId('login-submit');
  const username = byId('login-username').value.trim();
  const password = byId('login-password').value;
  if (!username || !password) {
    setError('login', 'Introduce usuario y contraseña.');
    return;
  }
  setBusy(submit, true, 'Entrando…');
  try {
    const data = await api.login(username, password);
    setCsrfToken(data && data.csrf_token);
    window.location.replace(nextUrl());
  } catch (error) {
    setError('login', messageFor(error, 'login'));
    setBusy(submit, false);
    if (error && error.status === 423) {
      // Refresca el estado por si el backend ya desbloqueó la cuenta.
      setTimeout(() => window.location.reload(), 30000);
    }
  }
}

async function handleSetup(event) {
  event.preventDefault();
  setError('setup', '');
  const submit = byId('setup-submit');
  const username = byId('setup-username').value.trim();
  const password = byId('setup-password').value;
  const confirm = byId('setup-password2').value;
  if (username.length < 3) {
    setError('setup', 'El usuario debe tener al menos 3 caracteres.');
    return;
  }
  if (password.length < 8) {
    setError('setup', 'La contraseña debe tener al menos 8 caracteres.');
    return;
  }
  if (password !== confirm) {
    setError('setup', 'Las dos contraseñas no coinciden.');
    return;
  }
  setBusy(submit, true, 'Creando…');
  try {
    await api.setup(username, password);
  } catch (error) {
    setError('setup', messageFor(error, 'setup'));
    setBusy(submit, false);
    return;
  }
  // Tras crear el administrador, intentamos entrar automáticamente.
  try {
    const data = await api.login(username, password);
    setCsrfToken(data && data.csrf_token);
    toast('Administrador creado. Entrando…', 'success');
    window.location.replace(nextUrl());
  } catch (error) {
    setBusy(submit, false);
    toast('Administrador creado. Inicia sesión con tus credenciales.', 'success', 8000);
    showLoginPanel('Usuario administrador creado. Ya puedes iniciar sesión.');
  }
}

function showLoginPanel(notice) {
  show(byId('panel-setup'), false);
  show(byId('panel-login'), true);
  const box = byId('login-notice');
  if (box) {
    if (notice) {
      box.hidden = false;
      box.textContent = notice;
    } else {
      box.hidden = true;
      box.textContent = '';
    }
  }
  const input = byId('login-username');
  if (input) input.focus();
}

function showSetupPanel() {
  show(byId('panel-login'), false);
  show(byId('panel-setup'), true);
  const input = byId('setup-username');
  if (input) input.focus();
}

async function boot() {
  initRevealButtons();
  const loginForm = byId('login-form');
  const setupForm = byId('setup-form');
  if (loginForm) loginForm.addEventListener('submit', handleLogin);
  if (setupForm) setupForm.addEventListener('submit', handleSetup);

  let me = null;
  try {
    me = await api.me();
  } catch (error) {
    setError('login', messageFor(error, 'login'));
    show(byId('panel-login'), true);
    document.body.classList.add('app-ready');
    return;
  }

  if (me && me.authenticated && !me.must_setup) {
    window.location.replace(nextUrl());
    return;
  }

  if (me && me.must_setup) {
    showSetupPanel();
    const hint = byId('setup-hint');
    if (hint) hint.hidden = false;
  } else {
    showLoginPanel('');
  }
  document.body.classList.add('app-ready');

  const username = byId('login-username');
  if (username && me && me.must_setup === false) username.focus();
}

document.addEventListener('DOMContentLoaded', () => {
  boot().catch((error) => {
    console.error('[TBS] fallo al preparar el acceso', error);
    document.body.classList.add('app-ready');
  });
});
