/**
 * Verifica la tarjeta de estado real de OSCam en la vista Newcamd.
 * Uso: node tools/ui-check-oscam.mjs --base --chrome --out [--password]
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
const PORT = Number(args.get('port') || 9233);
mkdirSync(OUT, { recursive: true });

const results = [];
const check = (label, ok, detail = '') => {
  results.push({ label, ok: Boolean(ok), detail });
  console.log(`[${ok ? 'OK ' : 'FAIL'}] ${label}${ok || !detail ? '' : ` -> ${detail}`}`);
};

const login = await fetch(`${BASE}/api/auth/login`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ username: USER, password: PASSWORD }),
});
const body = await login.json();
if (login.status !== 200) { console.error('login falló', login.status, body); process.exit(1); }
const cookies = {};
for (const raw of login.headers.getSetCookie()) {
  const [p] = raw.split(';'); const i = p.indexOf('=');
  cookies[p.slice(0, i).trim()] = p.slice(i + 1).trim();
}

// API directa
const api = await fetch(`${BASE}/api/newcamd/oscam?force=true`, { headers: { cookie: Object.entries(cookies).map(([k, v]) => `${k}=${v}`).join('; ') } });
const data = await api.json();
console.log('API /api/newcamd/oscam ->', api.status, JSON.stringify(data).slice(0, 240));
check('la API de estado de OSCam responde', api.status === 200);
check('OSCam disponible', data.available === true, JSON.stringify(data.message));
check('detecta la tarjeta lista', data.card_ready === true, JSON.stringify(data.readers));
check('lee el lector PC/SC', (data.readers || []).some((r) => r.protocol === 'pcsc'), JSON.stringify(data.readers));

const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${PORT}`, `--user-data-dir=${path.join(OUT, 'cp-oscam')}`, '--no-first-run', '--disable-gpu', '--window-size=1440,1200', 'about:blank'], { stdio: 'ignore' });
process.on('exit', () => chrome.kill());
let version = null;
for (let i = 0; i < 60 && !version; i += 1) { try { version = await (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json(); } catch { await sleep(250); } }
const target = await (await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`, { method: 'PUT' })).json();
const ws = new WebSocket(target.webSocketDebuggerUrl);
let id = 0; const pending = new Map(); const errors = [];
await new Promise((r) => { ws.onopen = r; });
ws.onmessage = (event) => {
  const m = JSON.parse(event.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  else if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails?.exception?.description || m.params.exceptionDetails?.text);
  else if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') errors.push((m.params.args || []).map((a) => a.value ?? a.description).join(' '));
};
const send = (method, params = {}) => new Promise((resolve) => { id += 1; pending.set(id, resolve); ws.send(JSON.stringify({ id, method, params })); });
const evaluate = async (expr) => {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.result?.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 240));
  return r.result?.result?.value;
};
const shot = async (name) => {
  const s = await send('Page.captureScreenshot', { format: 'png' });
  const f = path.join(OUT, `${name}.png`);
  writeFileSync(f, Buffer.from(s.result.data, 'base64'));
  return f;
};

await send('Page.enable'); await send('Runtime.enable'); await send('Network.enable');
const host = new URL(BASE).hostname;
for (const [n, v] of Object.entries(cookies)) await send('Network.setCookie', { name: n, value: v, domain: host, path: '/' });
await send('Page.navigate', { url: `${BASE}/#/newcamd` });
await sleep(3200);

const ui = await evaluate(`(() => {
  const card = document.getElementById('oscam-card');
  const body = document.getElementById('oscam-body');
  return {
    cardExiste: Boolean(card),
    cardDentroDeMain: Boolean(card && document.querySelector('main') && document.querySelector('main').contains(card)),
    resumen: (document.getElementById('oscam-summary') || {}).textContent || '',
    texto: (body || {}).textContent || '',
    items: document.querySelectorAll('#oscam-body li').length,
    aviso: /doPC1/.test(document.body.textContent || ''),
  };
})()`);
check('la tarjeta OSCam está en la vista', ui.cardExiste && ui.cardDentroDeMain);
check('resumen muestra OSCam/versión', /OSCam/.test(ui.resumen), ui.resumen);
check('muestra el lector con su estado', ui.items >= 1, `items=${ui.items}`);
check('indica la tarjeta lista', /Tarjeta lista|CARDOK/.test(ui.texto), ui.texto.slice(0, 120));
check('explica la limitación de OSCam (doPC1)', ui.aviso === true);
await shot('30-oscam-estado');
check('sin errores de consola', errors.length === 0, errors.slice(0, 2).join(' | '));

console.log('\n' + '='.repeat(66));
const failed = results.filter((r) => !r.ok);
console.log(`RESULTADO OSCAM UI: ${results.length - failed.length}/${results.length} comprobaciones OK`);
for (const f of failed) console.log(`  - ${f.label}: ${f.detail}`);
ws.close(); chrome.kill(); await sleep(200);
process.exit(failed.length ? 1 : 0);
