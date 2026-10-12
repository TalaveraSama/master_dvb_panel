/**
 * TBS WebUI - componentes de interfaz reutilizables.
 *
 * Incluye:
 *  - Sistema de avisos (toasts) accesible (role="status").
 *  - Diálogos modales nativos (<dialog> con form method="dialog"):
 *    confirmación y entrada de texto.
 *
 * Nota sobre Alpine.js: los widgets con estado propio (navegación móvil,
 * paneles avanzados, detalle de conexión) declaran su estado en línea con
 * x-data en el HTML; así no dependen del orden de carga de los módulos.
 */

import { byId, setBusy } from './utils.js';

const MAX_TOASTS = 5;

/** Crea un aviso flotante. kind: info | success | warn | error. */
export function toast(message, kind = 'info', timeoutMs = 5000) {
  const container = byId('toasts');
  if (!container) {
    if (kind === 'error') console.error('[TBS]', message);
    else console.info('[TBS]', message);
    return;
  }
  while (container.children.length >= MAX_TOASTS) {
    container.removeChild(container.firstElementChild);
  }
  const node = document.createElement('div');
  node.className = 'toast toast-' + kind;
  node.setAttribute('role', kind === 'error' ? 'alert' : 'status');
  node.setAttribute('aria-live', kind === 'error' ? 'assertive' : 'polite');
  node.innerHTML =
    '<span class="toast-icon" aria-hidden="true"></span>' +
    '<span class="toast-text"></span>' +
    '<button type="button" class="toast-close" aria-label="Cerrar aviso">×</button>';
  node.querySelector('.toast-text').textContent = String(message);
  const remove = () => {
    if (node.parentNode) node.parentNode.removeChild(node);
  };
  node.querySelector('.toast-close').addEventListener('click', remove);
  container.appendChild(node);
  if (timeoutMs > 0) setTimeout(remove, timeoutMs);
  return node;
}

/** Abre un <dialog> y resuelve con su returnValue al cerrarse. */
function openDialog(dialog) {
  return new Promise((resolve) => {
    if (!dialog) {
      resolve('');
      return;
    }
    const onClose = () => {
      dialog.removeEventListener('close', onClose);
      resolve(dialog.returnValue || '');
    };
    dialog.returnValue = '';
    dialog.addEventListener('close', onClose);
    if (typeof dialog.showModal === 'function') {
      try {
        dialog.showModal();
      } catch (_) {
        dialog.setAttribute('open', '');
      }
    } else {
      dialog.setAttribute('open', '');
    }
  });
}

/**
 * Diálogo de confirmación.
 * @returns {Promise<boolean>}
 */
export async function confirmDialog(options) {
  const opts = options || {};
  const dialog = byId('confirm-dialog');
  if (!dialog) return window.confirm(opts.message || '¿Seguro?');
  byId('confirm-title').textContent = opts.title || 'Confirmar';
  byId('confirm-text').textContent = opts.message || '';
  const ok = byId('confirm-ok');
  const cancel = byId('confirm-cancel');
  ok.textContent = opts.confirmLabel || 'Confirmar';
  ok.className = 'btn ' + (opts.danger ? 'btn-danger' : 'btn-primary');
  cancel.textContent = opts.cancelLabel || 'Cancelar';
  const result = await openDialog(dialog);
  return result === 'ok';
}

/**
 * Diálogo de entrada de texto (renombrar).
 * @returns {Promise<string|null>} texto o null si se cancela.
 */
export async function promptDialog(options) {
  const opts = options || {};
  const dialog = byId('prompt-dialog');
  const input = byId('prompt-input');
  if (!dialog || !input) {
    const value = window.prompt(opts.label || 'Nuevo valor', opts.value || '');
    return value === null ? null : value;
  }
  byId('prompt-title').textContent = opts.title || 'Editar';
  byId('prompt-label').textContent = opts.label || 'Valor';
  input.value = opts.value || '';
  input.maxLength = opts.maxLength || 120;
  byId('prompt-ok').textContent = opts.confirmLabel || 'Guardar';
  const result = await openDialog(dialog);
  return result === 'ok' ? input.value.trim() : null;
}

/**
 * Ejecuta una acción async con un botón en estado ocupado y aviso de error.
 * @returns {Promise<{ok:boolean, value?:any, error?:Error}>}
 */
export async function withBusy(button, busyLabel, action, options) {
  const opts = options || {};
  setBusy(button, true, busyLabel);
  try {
    const value = await action();
    return { ok: true, value: value };
  } catch (error) {
    const message = (error && error.detail) || (error && error.message) || 'Error inesperado';
    if (!opts.silent) toast(message, 'error', 7000);
    return { ok: false, error: error };
  } finally {
    setBusy(button, false);
  }
}

/**
 * Inicializa los botones "Mostrar/Ocultar" de los campos de contraseña
 * declarados con el atributo data-reveal="<id-del-input>".
 */
export function initRevealButtons(root) {
  const scope = root || document;
  Array.prototype.forEach.call(scope.querySelectorAll('[data-reveal]'), (button) => {
    const input = byId(button.dataset.reveal);
    if (!input || button.dataset.revealReady === '1') return;
    button.dataset.revealReady = '1';
    button.setAttribute('aria-pressed', 'false');
    button.addEventListener('click', () => {
      const shown = input.type === 'text';
      input.type = shown ? 'password' : 'text';
      button.textContent = shown ? 'Mostrar' : 'Ocultar';
      button.setAttribute('aria-pressed', shown ? 'false' : 'true');
    });
  });
}
