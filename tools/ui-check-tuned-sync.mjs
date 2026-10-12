/**
 * Comprueba que tras POST /api/tune la UI refleja el transponder sintonizado.
 * Uso: node tools/ui-check-tuned-sync.mjs --base --chrome --out [--password]
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
const PORT = Number(args.get('port') || 9228);
mkdirSync(OUT, { recursive: true });

const login = await fetch(`${BASE}/api/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: USER, password: PASSWORD }) });
const body = await login.json();
if (login.status !== 200) { console.error('login falló'); process.exit(1); }
const cookies = {};
for (const raw of login.headers.getSetCookie()) { const [p] = raw.split(';'); const i = p.indexOf('='); cookies[p.slice(0, i).trim()] = p.slice(i + 1).trim(); }
const csrf = body.csrf_token;

const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${PORT}`, `--user-data-dir=${path.join(OUT, 'cp-tuned')}`, '--no-first-run', '--disable-gpu', '--window-size=1440,1100', 'about:blank'], { stdio: 'ignore' });
process.on('exit', () => chrome.kill());
let version = null;
for (let i = 0; i < 60 && !version; i += 1) { try { version = await (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json(); } catch { await sleep(250); } }
const target = await (await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`, { method: 'PUT' })).json();
const ws = new WebSocket(target.webSocketDebuggerUrl);
let id = 0; const pending = new Map(); const consoleErrors = [];
await new Promise((r) => { ws.onopen = r; });
ws.onmessage = (event) => {
  const m = JSON.parse(event.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  else if (m.method === 'Runtime.exceptionThrown') consoleErrors.push(m.params.exceptionDetails?.exception?.description || m.params.exceptionDetails?.text);
};
const send = (method, params = {}) => new Promise((resolve) => { id += 1; pending.set(id, resolve); ws.send(JSON.stringify({ id, method, params })); });
const evaluate = async (expression, awaitPromise = false) => {
  const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise });
  return r.result?.result?.value;
};
await send('Page.enable'); await send('Runtime.enable');
const host = new URL(BASE).hostname;
for (const [name, value] of Object.entries(cookies)) await send('Network.setCookie', { name, value, domain: host, path: '/' });
await send('Page.navigate', { url: `${BASE}/#/tune` });
await sleep(2500);

// Sintoniza desde la propia página (simula lo que hace el formulario)
const tune = await evaluate(`fetch('/api/tune', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': ${JSON.stringify(csrf)} }, body: JSON.stringify({ delivery_system: 'DVB-S2', frequency_khz: 11096000, symbol_rate_ksps: 27500, polarization: 'V', use_lnb: true }) }).then(r => r.json())`, true);
console.log('POST /api/tune ->', JSON.stringify({ locked: tune.locked, if: tune.if_frequency_khz, signal: tune.stats?.signal_percent }));

await sleep(2500);
const apiStatus = await evaluate(`fetch('/api/status').then(r => r.json())`, true);
console.log('GET /api/status ->', JSON.stringify({ locked_by: apiStatus.locked_by, adapter: apiStatus.adapter, tuned: apiStatus.tuned, statsSignal: apiStatus.stats?.signal_percent }));

const dom = await evaluate(`(() => {
  const text = document.body.innerText.replace(/\\s+/g, ' ');
  const index = text.indexOf('Sintonización actual');
  return { section: index >= 0 ? text.slice(index, index + 260) : null };
})()`);
console.log('DOM "Sintonización actual" ->', JSON.stringify(dom.section));

const shot = await send('Page.captureScreenshot', { format: 'png' });
writeFileSync(path.join(OUT, '11-tuned-sync.png'), Buffer.from(shot.result.data, 'base64'));
console.log('captura:', path.join(OUT, '11-tuned-sync.png'));
console.log('errores consola:', consoleErrors.slice(0, 5).join(' | ') || '(ninguno)');
ws.close(); chrome.kill(); await sleep(200);
