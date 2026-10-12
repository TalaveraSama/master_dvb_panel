/**
 * Captura de red para encontrar la petición que falla en la SPA autenticada.
 * Uso: node tools/ui-net-trace.mjs --base --chrome --out [--password ...]
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
const PORT = Number(args.get('port') || 9226);
mkdirSync(OUT, { recursive: true });

const login = await fetch(`${BASE}/api/auth/login`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ username: USER, password: PASSWORD }),
});
const body = await login.json();
if (login.status !== 200) { console.error('login falló', login.status, body); process.exit(1); }
const cookies = {};
for (const raw of login.headers.getSetCookie()) {
  const [pair] = raw.split(';'); const i = pair.indexOf('=');
  cookies[pair.slice(0, i).trim()] = pair.slice(i + 1).trim();
}

const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${PORT}`, `--user-data-dir=${path.join(OUT, 'chrome-profile-4')}`, '--no-first-run', '--disable-gpu', 'about:blank'], { stdio: 'ignore' });
process.on('exit', () => chrome.kill());
let version = null;
for (let i = 0; i < 60 && !version; i += 1) { try { version = await (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json(); } catch { await sleep(250); } }
const target = await (await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`, { method: 'PUT' })).json();
const ws = new WebSocket(target.webSocketDebuggerUrl);
let id = 0; const pending = new Map(); const requests = new Map(); const finished = [];
await new Promise((resolve) => { ws.onopen = resolve; });
ws.onmessage = (event) => {
  const message = JSON.parse(event.data);
  if (message.id && pending.has(message.id)) { pending.get(message.id)(message); pending.delete(message.id); return; }
  if (message.method === 'Network.requestWillBeSent') {
    requests.set(message.params.requestId, { url: message.params.request.url, method: message.params.request.method });
  }
  if (message.method === 'Network.responseReceived') {
    const record = requests.get(message.params.requestId);
    if (record && record.url.includes('/api/')) finished.push({ ...record, status: message.params.response.status });
  }
  if (message.method === 'Network.loadingFailed') {
    const record = requests.get(message.params.requestId);
    finished.push({ url: record?.url, method: record?.method, failed: message.params.errorText });
  }
};
const send = (method, params = {}) => new Promise((resolve) => { id += 1; pending.set(id, resolve); ws.send(JSON.stringify({ id, method, params })); });
const evaluate = async (expression, awaitPromise = false) => {
  const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise });
  return result.result?.result?.value;
};

await send('Page.enable'); await send('Runtime.enable'); await send('Network.enable');
const host = new URL(BASE).hostname;
for (const [name, value] of Object.entries(cookies)) await send('Network.setCookie', { name, value, domain: host, path: '/' });

await send('Page.navigate', { url: `${BASE}/#/channels` });
await sleep(4000);

console.log('=== peticiones /api/ observadas ===');
for (const record of finished) console.log(JSON.stringify(record));

const state = await evaluate(`(() => ({
  banner: (document.querySelector('.banner, #connection-error, [data-error-banner]') || {}).textContent || null,
  hasOfflineText: document.body.innerText.includes('Sin conexión con el servidor'),
  body: document.body.innerText.replace(/\\s+/g, ' ').slice(0, 500)
}))()`);
console.log('=== estado de la página ===');
console.log(JSON.stringify(state, null, 2));

// Ejecuta a mano las llamadas que hace la app para ver la respuesta cruda
const manual = await evaluate(`Promise.all([
  fetch('/api/adapters').then(r => r.status + ' adapters: ' + JSON.stringify(r.ok)).catch(e => 'adapters ERR ' + e),
  fetch('/api/status').then(r => r.status).catch(e => 'status ERR ' + e),
  fetch('/api/channels').then(r => r.status).catch(e => 'channels ERR ' + e),
  fetch('/api/settings').then(r => r.status).catch(e => 'settings ERR ' + e),
  fetch('/api/scan/status').then(r => r.status).catch(e => 'scanstatus ERR ' + e),
  fetch('/api/transponders').then(r => r.status).catch(e => 'transponders ERR ' + e)
])`, true);
console.log('=== llamadas manuales ===');
console.log(JSON.stringify(manual, null, 2));

const shot = await send('Page.captureScreenshot', { format: 'png' });
writeFileSync(path.join(OUT, '10-channels-state.png'), Buffer.from(shot.result.data, 'base64'));
ws.close(); chrome.kill(); await sleep(200);
