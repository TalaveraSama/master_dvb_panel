/**
 * TBS WebUI - utilidades compartidas.
 * Sin dependencias externas. Identificadores en inglés, textos de UI en español.
 */

/** Atajo de querySelector. */
export function qs(sel, root) {
  return (root || document).querySelector(sel);
}

/** Atajo de querySelectorAll -> Array. */
export function qsa(sel, root) {
  return Array.prototype.slice.call((root || document).querySelectorAll(sel));
}

/** Atajo de getElementById. */
export function byId(id) {
  return document.getElementById(id);
}

/** Escapa texto para interpolarlo en innerHTML de forma segura. */
export function esc(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** Retrasa la ejecución hasta que pasen `wait` ms sin nuevas llamadas. */
export function debounce(fn, wait = 250) {
  let timer = null;
  function debounced() {
    const args = arguments;
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => {
      timer = null;
      fn.apply(null, args);
    }, wait);
  }
  debounced.cancel = () => {
    if (timer) clearTimeout(timer);
    timer = null;
  };
  return debounced;
}

/** Limita un número al rango [lo, hi]. */
export function clamp(value, lo, hi) {
  return Math.min(hi, Math.max(lo, value));
}

/** Convierte a número; devuelve `fallback` si no es finito o está vacío. */
export function num(value, fallback = null) {
  if (value === null || value === undefined || value === '') return fallback;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

/** Convierte a entero; devuelve `fallback` si no es finito. */
export function int(value, fallback = null) {
  const parsed = num(value, null);
  return parsed === null ? fallback : Math.trunc(parsed);
}

/** Formatea un número con `digits` decimales; "—" si es nulo. */
export function fmt(value, digits = 1, suffix = '') {
  const parsed = num(value, null);
  if (parsed === null) return '—';
  return parsed.toFixed(digits) + suffix;
}

/** Formatea un porcentaje 0..100. */
export function fmtPercent(value) {
  const parsed = num(value, null);
  if (parsed === null) return '—';
  return parsed.toFixed(1) + ' %';
}

/** Formatea un BER (escala 0..1) en notación científica cuando es pequeño. */
export function fmtBer(value) {
  const parsed = num(value, null);
  if (parsed === null) return '—';
  if (parsed === 0) return '0';
  const abs = Math.abs(parsed);
  if (abs < 1e-4) return parsed.toExponential(2);
  return parsed.toFixed(5);
}

/** Formatea una fecha ISO (o marca de tiempo) para mostrarla en español. */
export function fmtDateTime(value) {
  if (value === null || value === undefined || value === '') return '—';
  const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  const pad = (n) => String(n).padStart(2, '0');
  return (
    pad(date.getDate()) + '/' + pad(date.getMonth() + 1) + '/' + date.getFullYear() +
    ' ' + pad(date.getHours()) + ':' + pad(date.getMinutes()) + ':' + pad(date.getSeconds())
  );
}

/** Formatea una hora corta (HH:MM:SS) a partir de un timestamp en segundos. */
export function fmtClock(secondsEpoch) {
  const value = num(secondsEpoch, null);
  if (value === null) return '—';
  const date = new Date(value * 1000);
  if (Number.isNaN(date.getTime())) return '—';
  const pad = (n) => String(n).padStart(2, '0');
  return pad(date.getHours()) + ':' + pad(date.getMinutes()) + ':' + pad(date.getSeconds());
}

/** Formatea una duración en segundos como "1 h 02 min 03 s". */
export function fmtDuration(seconds) {
  const total = num(seconds, null);
  if (total === null || total < 0) return '—';
  const s = Math.floor(total % 60);
  const m = Math.floor((total / 60) % 60);
  const h = Math.floor(total / 3600);
  if (h > 0) return h + ' h ' + String(m).padStart(2, '0') + ' min';
  if (m > 0) return m + ' min ' + String(s).padStart(2, '0') + ' s';
  return s + ' s';
}

/** Frecuencia en kHz -> texto en MHz. */
export function fmtKHzAsMHz(khz) {
  const value = num(khz, null);
  if (value === null) return '—';
  return (value / 1000).toFixed(3) + ' MHz';
}

/** Escribe el textContent de un elemento (por id o por referencia). */
export function setText(target, text) {
  const node = typeof target === 'string' ? byId(target) : target;
  if (node) node.textContent = text === null || text === undefined ? '—' : String(text);
}

/** Muestra u oculta un elemento. */
export function show(target, visible) {
  const node = typeof target === 'string' ? byId(target) : target;
  if (!node) return;
  node.hidden = !visible;
}

/** Crea un elemento con atributos e hijos. */
export function createEl(tag, attrs, children) {
  const node = document.createElement(tag);
  if (attrs) {
    Object.keys(attrs).forEach((key) => {
      const value = attrs[key];
      if (value === null || value === undefined || value === false) return;
      if (key === 'text') node.textContent = String(value);
      else if (key === 'html') node.innerHTML = String(value);
      else if (key === 'class') node.className = String(value);
      else if (key === 'dataset') {
        Object.keys(value).forEach((dataKey) => {
          node.dataset[dataKey] = String(value[dataKey]);
        });
      } else if (key.startsWith('on') && typeof value === 'function') {
        node.addEventListener(key.slice(2).toLowerCase(), value);
      } else {
        node.setAttribute(key, String(value));
      }
    });
  }
  if (children) {
    (Array.isArray(children) ? children : [children]).forEach((child) => {
      if (child === null || child === undefined || child === false) return;
      node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
    });
  }
  return node;
}

/** Marca un botón como ocupado y lo deshabilita mientras dura la operación. */
export function setBusy(button, busy, busyLabel) {
  if (!button) return;
  if (busy) {
    if (button.dataset.idleLabel === undefined) button.dataset.idleLabel = button.innerHTML;
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    if (busyLabel) button.innerHTML = '<span class="spinner" aria-hidden="true"></span> ' + esc(busyLabel);
  } else {
    button.disabled = false;
    button.removeAttribute('aria-busy');
    if (button.dataset.idleLabel !== undefined) {
      button.innerHTML = button.dataset.idleLabel;
      delete button.dataset.idleLabel;
    }
  }
}

/** ¿El usuario prefiere movimiento reducido? */
export function prefersReducedMotion() {
  return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
}

/** Genera un id corto para claves de lista de la UI. */
export function uid(prefix = 'id') {
  return prefix + '-' + Math.random().toString(36).slice(2, 9);
}
