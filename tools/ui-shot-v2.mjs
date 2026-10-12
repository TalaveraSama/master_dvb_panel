/**
 * Captura la vista /v2 (aspecto del fork) para revisar el diseno.
 * Uso: node tools/ui-shot-v2.mjs --base --chrome --out --password
 */
import { spawn } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { setTimeout as sleep } from 'node:timers/promises';
import path from 'node:path';

const args = new Map();
for (let i = 2; i < process.argv.length; i += 2) args.set(process.argv[i].replace(/^--/, ''), process.argv[i + 1]);
const BASE = args.get('base');
const USER = args.get('user') || 'admin';
const PASSWORD = args.get('password') || process.env.TBS_ADMIN_PASSWORD || '';
const CHROME = args.get('chrome');
const OUT = args.get('out') || '.';
const PORT = Number(args.get('port') || 9241);
mkdirSync(OUT, { recursive: true });

const errores = [];
const login = await fetch(`${BASE}/api/auth/login`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ username: USER, password: PASSWORD }),
});
const cuerpo = await login.json();
if (login.status !== 200) { console.error('login falló', login.status); process.exit(1); }
const cookies = {};
for (const raw of login.headers.getSetCookie()) {
  const [p] = raw.split(';'); const i = p.indexOf('=');
  cookies[p.slice(0, i).trim()] = p.slice(i + 1).trim();
}

const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${PORT}`, `--user-data-dir=${path.join(OUT, 'cp')}`, '--no-first-run', '--disable-gpu', '--window-size=1500,1200', 'about:blank'], { stdio: 'ignore' });
process.on('exit', () => chrome.kill());
let version = null;
for (let i = 0; i < 60 && !version; i += 1) { try { version = await (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json(); } catch { await sleep(250); } }
const target = await (await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`, { method: 'PUT' })).json();
const ws = new WebSocket(target.webSocketDebuggerUrl);
let id = 0; const pend = new Map();
await new Promise((r) => { ws.onopen = r; });
ws.onmessage = (event) => {
  const m = JSON.parse(event.data);
  if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); }
  else if (m.method === 'Runtime.exceptionThrown') errores.push(m.params.exceptionDetails?.exception?.description || m.params.exceptionDetails?.text);
  else if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') errores.push((m.params.args || []).map((a) => a.value ?? a.description).join(' '));
};
const send = (method, params = {}) => new Promise((resolve) => { id += 1; pend.set(id, resolve); ws.send(JSON.stringify({ id, method, params })); });
const evaluate = async (expr) => {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
  return r.result?.result?.value;
};
const shot = async (nombre) => {
  const s = await send('Page.captureScreenshot', { format: 'png' });
  writeFileSync(path.join(OUT, `${nombre}.png`), Buffer.from(s.result.data, 'base64'));
};

await send('Page.enable'); await send('Runtime.enable'); await send('Network.enable');
const host = new URL(BASE).hostname;
for (const [n, v] of Object.entries(cookies)) await send('Network.setCookie', { name: n, value: v, domain: host, path: '/' });
await send('Page.navigate', { url: `${BASE}/v2` });
await sleep(3200);

const loginVisible = await evaluate(`!document.getElementById('login-screen').hidden`);
if (loginVisible) {
  console.log('pide login (se rellena el formulario)');
  await evaluate(`(() => {
    document.getElementById('login-user').value = '${USER}';
    document.getElementById('login-pass').value = '${PASSWORD}';
    document.getElementById('login-form').requestSubmit();
  })()`);
  await sleep(3000);
}
await shot('40-v2-panel');

const estado = await evaluate(`(() => {
  const app = document.getElementById('app');
  const view = document.getElementById('view');
  const css = getComputedStyle(document.querySelector('.sidebar') || document.body);
  return {
    appVisible: app && !app.hidden,
    titulo: (document.getElementById('view-title') || {}).textContent,
    navItems: document.querySelectorAll('#nav .nav__item').length,
    contenido: view ? view.textContent.trim().slice(0, 80) : '',
    barras: document.querySelectorAll('.bar__fill').length,
    fondo: css.backgroundColor,
    anchoSidebar: document.querySelector('.sidebar') ? Math.round(document.querySelector('.sidebar').getBoundingClientRect().width) : 0,
  };
})()`);
console.log('estado:', JSON.stringify(estado, null, 2));
console.log('errores de consola:', errores.length ? errores.slice(0, 3) : 'ninguno');

ws.close(); chrome.kill(); await sleep(200);
process.exit(estado.appVisible && estado.navItems > 0 ? 0 : 1);
