/**
 * Prueba de aceptación REAL en navegador headless (Chrome DevTools Protocol).
 *
 * Comprueba lo que ni pytest ni `node --check` pueden: que el JS se ejecuta en
 * un motor real, que Alpine hidrata, que la UI pinta las barras de señal, que
 * el WebSocket entrega datos y que las barras/de la gráfica CAMBIAN con el
 * tiempo. Guarda capturas PNG de cada vista.
 *
 * Uso:
 *   node tools/ui-browser-test.mjs --base http://127.0.0.1:8010 \
 *        --user admin --password ... --chrome <ruta msedge/chrome> --out <dir>
 */

import { spawn } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { setTimeout as sleep } from 'node:timers/promises';
import path from 'node:path';

const args = new Map();
for (let i = 2; i < process.argv.length; i += 2) {
  args.set(process.argv[i].replace(/^--/, ''), process.argv[i + 1]);
}
const BASE = args.get('base') || 'http://127.0.0.1:8010';
const USER = args.get('user') || 'admin';
const PASSWORD = args.get('password') || process.env.TBS_ADMIN_PASSWORD || '';
const CHROME = args.get('chrome');
const OUT = args.get('out') || process.cwd();
const PORT = Number(args.get('port') || 9222);

if (!PASSWORD) {
  console.error('Falta --password o TBS_ADMIN_PASSWORD');
  process.exit(2);
}
if (!CHROME) {
  console.error('Falta --chrome <ruta al ejecutable>');
  process.exit(2);
}
mkdirSync(OUT, { recursive: true });

const results = [];
const consoleErrors = [];
function check(label, ok, detail = '') {
  results.push({ label, ok: Boolean(ok), detail });
  console.log(`[${ok ? 'OK ' : 'FAIL'}] ${label}${ok || !detail ? '' : ` -> ${detail}`}`);
}

// ---------------------------------------------------------------------------
// Lanzar Chrome en modo headless con depuración remota
// ---------------------------------------------------------------------------
const profile = path.join(OUT, 'chrome-profile');
const chrome = spawn(
  CHROME,
  [
    '--headless=new',
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${profile}`,
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-gpu',
    '--disable-dev-shm-usage',
    '--window-size=1440,1000',
    'about:blank',
  ],
  { stdio: 'ignore' },
);
process.on('exit', () => chrome.kill());

async function waitForDevtools() {
  for (let i = 0; i < 60; i += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${PORT}/json/version`);
      if (response.ok) return (await response.json());
    } catch {
      /* todavía arrancando */
    }
    await sleep(250);
  }
  throw new Error('Chrome no expuso el puerto de depuración');
}

const version = await waitForDevtools();
console.log(`Chrome: ${version['Browser']}`);

// ---------------------------------------------------------------------------
// Cliente CDP mínimo (WebSocket nativo de Node)
// ---------------------------------------------------------------------------
class CDP {
  constructor(url) {
    this.ws = new WebSocket(url);
    this.id = 0;
    this.pending = new Map();
    this.handlers = new Map();
    this.ready = new Promise((resolve, reject) => {
      this.ws.onopen = resolve;
      this.ws.onerror = (event) => reject(new Error(`WS error: ${event.message || 'desconocido'}`));
    });
    this.ws.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.id && this.pending.has(message.id)) {
        const { resolve, reject } = this.pending.get(message.id);
        this.pending.delete(message.id);
        if (message.error) reject(new Error(JSON.stringify(message.error)));
        else resolve(message.result);
      } else if (message.method) {
        const list = this.handlers.get(message.method) || [];
        for (const handler of list) handler(message.params);
      }
    };
  }
  on(method, handler) {
    if (!this.handlers.has(method)) this.handlers.set(method, []);
    this.handlers.get(method).push(handler);
  }
  send(method, params = {}) {
    this.id += 1;
    const id = this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  close() {
    try {
      this.ws.close();
    } catch {
      /* ignorar */
    }
  }
}

const target = await (await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`, { method: 'PUT' })).json();
const cdp = new CDP(target.webSocketDebuggerUrl);
await cdp.ready;
await cdp.send('Page.enable');
await cdp.send('Runtime.enable');
await cdp.send('Log.enable');
await cdp.send('Network.enable');
cdp.on('Log.entryAdded', (params) => {
  if (params.entry.level === 'error') consoleErrors.push(`log: ${params.entry.text}`);
});
cdp.on('Runtime.exceptionThrown', (params) => {
  consoleErrors.push(`exception: ${params.exceptionDetails?.exception?.description || params.exceptionDetails?.text}`);
});
cdp.on('Runtime.consoleAPICalled', (params) => {
  if (params.type === 'error') {
    consoleErrors.push(`console: ${(params.args || []).map((a) => a.value ?? a.description).join(' ')}`);
  }
});

async function evaluate(expression, awaitPromise = false) {
  const result = await cdp.send('Runtime.evaluate', {
    expression,
    returnByValue: true,
    awaitPromise,
  });
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
  }
  return result.result.value;
}

async function goto(url, waitMs = 1200) {
  await cdp.send('Page.navigate', { url });
  await sleep(waitMs);
}

async function screenshot(name) {
  const shot = await cdp.send('Page.captureScreenshot', { format: 'png' });
  const file = path.join(OUT, `${name}.png`);
  writeFileSync(file, Buffer.from(shot.data, 'base64'));
  return file;
}

// ---------------------------------------------------------------------------
// 1) Login real vía API (Node) y cookies inyectadas en el navegador
// ---------------------------------------------------------------------------
const loginResponse = await fetch(`${BASE}/api/auth/login`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ username: USER, password: PASSWORD }),
});
const loginBody = await loginResponse.json().catch(() => ({}));
check('login real por API', loginResponse.status === 200, `${loginResponse.status} ${JSON.stringify(loginBody)}`);
if (loginResponse.status !== 200) {
  console.log('No se puede continuar sin sesión');
  process.exit(1);
}
const setCookies = loginResponse.headers.getSetCookie ? loginResponse.headers.getSetCookie() : [];
const jar = {};
for (const raw of setCookies) {
  const [pair] = raw.split(';');
  const index = pair.indexOf('=');
  jar[pair.slice(0, index).trim()] = pair.slice(index + 1).trim();
}
const csrf = loginBody.csrf_token;
check('cookies de sesión y CSRF recibidas', Boolean(jar.tbs_session && jar.tbs_csrf), JSON.stringify(Object.keys(jar)));

const host = new URL(BASE).hostname;
for (const [name, value] of Object.entries(jar)) {
  await cdp.send('Network.setCookie', { name, value, domain: host, path: '/' });
}

// ---------------------------------------------------------------------------
// 2) Pantalla de login (sin sesión) se sirve y "hidrata"
// ---------------------------------------------------------------------------
await cdp.send('Network.clearBrowserCookies');
await goto(`${BASE}/login`, 1500);
const loginInfo = await evaluate(`(() => ({
  url: location.pathname,
  hasForm: !!document.querySelector('form'),
  passwordInputs: document.querySelectorAll('input[type=password]').length,
  usernameInputs: document.querySelectorAll('input[type=text],input[type=email],input#username,input[name=username]').length,
  title: document.title,
  scriptTags: [...document.querySelectorAll('script[src]')].map(s => s.getAttribute('src')),
  alpine: typeof window.Alpine !== 'undefined' ? (window.Alpine.version || 'presente') : null,
  bootOverlayVisible: (() => {
    const overlay = document.getElementById('boot-overlay');
    if (!overlay) return false;
    return getComputedStyle(overlay).display !== 'none' && overlay.offsetHeight > 0;
  })(),
  darkBg: getComputedStyle(document.body).backgroundColor,
  html: document.body.innerText.slice(0, 200)
}))()`);
check('/login carga el formulario', loginInfo.hasForm && loginInfo.passwordInputs >= 1, JSON.stringify(loginInfo));
check('no hay CDN externo en la pantalla de acceso', loginInfo.scriptTags.every((s) => s.startsWith('/')), JSON.stringify(loginInfo.scriptTags));
check('tema oscuro aplicado', /rgb\((\d+), (\d+), (\d+)\)/.test(loginInfo.darkBg) &&
  loginInfo.darkBg.match(/\d+/g).slice(0, 3).every((v) => Number(v) < 90), loginInfo.darkBg);
check('el overlay de error de arranque NO tapa la pantalla', !loginInfo.bootOverlayVisible, `overlay visible=${loginInfo.bootOverlayVisible}`);
const loginShot = await screenshot('01-login');

// ---------------------------------------------------------------------------
// 3) Con sesión: dashboard y barras en vivo
// ---------------------------------------------------------------------------
for (const [name, value] of Object.entries(jar)) {
  await cdp.send('Network.setCookie', { name, value, domain: host, path: '/' });
}
await goto(`${BASE}/`, 2500);
const dashboard = await evaluate(`(() => {
  const text = document.body.innerText;
  const overlay = document.getElementById('boot-overlay');
  return {
    url: location.pathname,
    redirectedToLogin: location.pathname === '/login',
    bootOverlayVisible: Boolean(overlay) && getComputedStyle(overlay).display !== 'none' && overlay.offsetHeight > 0,
    alpine: typeof window.Alpine !== 'undefined' ? window.Alpine.version : null,
    hasLock: /lock/i.test(text),
    hasSignal: /se[nñ]al/i.test(text),
    hasSnr: /snr|cnr/i.test(text),
    hasBer: /ber/i.test(text),
    canvases: document.querySelectorAll('canvas').length,
    wsIndicator: (() => {
      const node = [...document.querySelectorAll('*')].find(n => /conectad|desconectad|reconectand|sin conexi[oó]n con el servidor/i.test(n.textContent || '') && n.children.length === 0);
      return node ? (node.textContent || '').trim().slice(0, 24) : null;
    })(),
    bodyLength: text.length,
    sample: text.slice(0, 220)
  };
})()`);
check('raíz sirve la SPA autenticada', !dashboard.redirectedToLogin, JSON.stringify(dashboard));
check('el overlay de arranque no tapa el panel', !dashboard.bootOverlayVisible, `visible=${dashboard.bootOverlayVisible}`);
check('Alpine.js 3.x cargado localmente', Boolean(dashboard.alpine), String(dashboard.alpine));
check('dashboard muestra señal/SNR/BER/LOCK', dashboard.hasLock && dashboard.hasSignal && dashboard.hasSnr && dashboard.hasBer, JSON.stringify(dashboard));
check('gráfica histórica presente (canvas)', dashboard.canvases >= 1, String(dashboard.canvases));
check('indicador de estado de conexión visible', Boolean(dashboard.wsIndicator), String(dashboard.wsIndicator));

// Sintonizar desde la UI usando la propia API del navegador (con CSRF)
const tuneResult = await evaluate(
  `fetch('/api/tune', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': ${JSON.stringify(csrf)} },
      body: JSON.stringify({ delivery_system: 'DVB-S2', frequency_khz: 11096000, symbol_rate_ksps: 27500, polarization: 'V', use_lnb: true })
   }).then(r => r.json())`,
  true,
);
check('POST /api/tune desde el navegador devuelve LOCK', tuneResult && tuneResult.locked === true, JSON.stringify(tuneResult).slice(0, 200));

// Esperar a que el WebSocket alimente la UI y comprobar que los valores CAMBIAN
const samples = [];
for (let i = 0; i < 6; i += 1) {
  await sleep(700);
  samples.push(
    await evaluate(`(() => {
      const text = document.body.innerText;
      const numbers = [...text.matchAll(/(-?\\d+(?:[.,]\\d+)?)\\s*(%|dB|dBm)/g)].map(m => m[0]);
      return { numbers: numbers.slice(0, 8), ws: document.documentElement.dataset.wsState || null };
    })()`),
  );
}
const flattened = samples.map((s) => s.numbers.join('|'));
const distinct = new Set(flattened).size;
check('la UI recibe datos en vivo (valores que cambian)', distinct >= 2, JSON.stringify(flattened));
const dashboardShot = await screenshot('02-dashboard');

// ---------------------------------------------------------------------------
// 4) Navegar por las vistas y capturarlas
// ---------------------------------------------------------------------------
const views = [
  ['tune', '03-tune'],
  ['scan', '04-scan'],
  ['channels', '05-channels'],
  ['settings', '06-settings'],
];
for (const [view, name] of views) {
  await goto(`${BASE}/#/${view}`, 1600);
  const info = await evaluate(`({ hash: location.hash, text: document.body.innerText.length, visible: !document.body.innerText.includes('No se pudo') })`);
  check(`vista #/${view} renderiza`, info.text > 200, JSON.stringify(info));
  await screenshot(name);
}

// Escaneo desde la UI (arrancar y comprobar progreso)
const scanStart = await evaluate(
  `fetch('/api/scan/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': ${JSON.stringify(csrf)} },
      body: JSON.stringify({ adapters: [{ adapter: 0, frontend: 0 }], transponders: [
         { delivery_system: 'DVB-S2', frequency_khz: 11096000, symbol_rate_ksps: 27500, polarization: 'V' }],
         dwell_seconds: 1.0 })
   }).then(r => r.json())`,
  true,
);
check('escaneo arrancado desde el navegador', scanStart && scanStart.job_id, JSON.stringify(scanStart));

let scanProgress = null;
for (let i = 0; i < 40; i += 1) {
  await sleep(500);
  scanProgress = await evaluate(`fetch('/api/scan/status').then(r => r.json()).then(d => d.progress)`, true);
  if (['done', 'error', 'cancelled'].includes(scanProgress.state)) break;
}
check('escaneo completado desde la UI', scanProgress && scanProgress.state === 'done', JSON.stringify(scanProgress));
await goto(`${BASE}/#/scan`, 1500);
await screenshot('07-scan-done');

// Canales guardados y exportaciones
const channels = await evaluate(`fetch('/api/channels').then(r => r.json())`, true);
check('canales guardados tras el escaneo', channels && channels.total > 0, JSON.stringify(channels).slice(0, 160));
await goto(`${BASE}/#/channels`, 1800);
const channelRows = await evaluate(`document.querySelectorAll('table tbody tr').length`);
check('la tabla de canales pinta filas', channelRows > 0, `filas=${channelRows}`);
await screenshot('08-channels-list');

// ---------------------------------------------------------------------------
// 5) Errores de consola
// ---------------------------------------------------------------------------
check('sin errores de consola ni excepciones JS', consoleErrors.length === 0, consoleErrors.slice(0, 5).join(' | '));

// ---------------------------------------------------------------------------
// Informe
// ---------------------------------------------------------------------------
const failures = results.filter((r) => !r.ok);
console.log('\n' + '='.repeat(70));
console.log(`RESULTADO NAVEGADOR: ${results.length - failures.length}/${results.length} comprobaciones OK`);
console.log(`Capturas: ${[loginShot, dashboardShot].join(', ')} y 6 más en ${OUT}`);
if (failures.length) {
  for (const failure of failures) console.log(`  - ${failure.label}: ${failure.detail}`);
}
if (consoleErrors.length) {
  console.log('Errores de consola:');
  for (const error of consoleErrors.slice(0, 10)) console.log(`  * ${error}`);
}
cdp.close();
chrome.kill();
await sleep(300);
process.exit(failures.length ? 1 : 0);
