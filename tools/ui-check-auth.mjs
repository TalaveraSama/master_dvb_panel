/**
 * Comprobación autenticada: ¿se carga Alpine y funcionan los widgets con estado?
 * Uso: node tools/ui-check-auth.mjs --base --user --password --chrome --out
 */
import { spawn } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { setTimeout as sleep } from 'node:timers/promises';
import path from 'node:path';

const args = new Map();
for (let i = 2; i < process.argv.length; i += 2) args.set(process.argv[i].replace(/^--/, ''), process.argv[i + 1]);
const BASE = args.get('base');
const USER = args.get('user') || 'admin';
const PASSWORD = args.get('password') || process.env.TBS_ADMIN_PASSWORD || '';
const CHROME = args.get('chrome');
const OUT = args.get('out') || '.';
const PORT = Number(args.get('port') || 9224);
mkdirSync(OUT, { recursive: true });

// 1) sesión por API
const login = await fetch(`${BASE}/api/auth/login`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ username: USER, password: PASSWORD }),
});
const body = await login.json();
if (login.status !== 200) { console.error('login falló', login.status, body); process.exit(1); }
const cookies = {};
for (const raw of login.headers.getSetCookie()) {
  const [pair] = raw.split(';');
  const index = pair.indexOf('=');
  cookies[pair.slice(0, index).trim()] = pair.slice(index + 1).trim();
}

const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${PORT}`, `--user-data-dir=${path.join(OUT, 'chrome-profile-3')}`, '--no-first-run', '--disable-gpu', '--window-size=1440,1100', 'about:blank'], { stdio: 'ignore' });
process.on('exit', () => chrome.kill());

let version = null;
for (let i = 0; i < 60 && !version; i += 1) {
  try { version = await (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json(); } catch { await sleep(250); }
}
const target = await (await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`, { method: 'PUT' })).json();
const ws = new WebSocket(target.webSocketDebuggerUrl);
let id = 0; const pending = new Map(); const events = []; const consoleErrors = [];
await new Promise((resolve) => { ws.onopen = resolve; });
ws.onmessage = (event) => {
  const message = JSON.parse(event.data);
  if (message.id && pending.has(message.id)) { pending.get(message.id)(message); pending.delete(message.id); }
  else if (message.method) {
    events.push(message);
    if (message.method === 'Runtime.exceptionThrown') consoleErrors.push(message.params.exceptionDetails?.exception?.description || message.params.exceptionDetails?.text);
    if (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'error') consoleErrors.push((message.params.args || []).map((a) => a.value ?? a.description).join(' '));
  }
};
const send = (method, params = {}) => new Promise((resolve) => { id += 1; pending.set(id, resolve); ws.send(JSON.stringify({ id, method, params })); });
const evaluate = async (expression, awaitPromise = false) => {
  const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise });
  if (result.result?.exceptionDetails) throw new Error(JSON.stringify(result.result.exceptionDetails).slice(0, 300));
  return result.result?.result?.value;
};

await send('Page.enable'); await send('Runtime.enable'); await send('Network.enable');
const host = new URL(BASE).hostname;
for (const [name, value] of Object.entries(cookies)) await send('Network.setCookie', { name, value, domain: host, path: '/' });

await send('Page.navigate', { url: `${BASE}/` });
await sleep(2600);

const report = await evaluate(`(() => ({
  path: location.pathname,
  alpineType: typeof window.Alpine,
  alpineVersion: window.Alpine && window.Alpine.version,
  scriptTags: [...document.querySelectorAll('script[src]')].map(s => s.getAttribute('src')),
  xDataCount: document.querySelectorAll('[x-data]').length,
  xCloakVisible: [...document.querySelectorAll('[x-cloak]')].filter(n => getComputedStyle(n).display !== 'none').length,
  wsNodes: [...document.querySelectorAll('*')].filter(n => /ws|conexi/i.test(n.id + ' ' + n.className)).slice(0, 6).map(n => ({ tag: n.tagName, id: n.id, cls: String(n.className).slice(0, 40), text: (n.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 50) }))
}))()`);
console.log('=== estado autenticado ===');
console.log(JSON.stringify(report, null, 2));

// Esperar a que el WS conecte y leer el indicador
await sleep(2500);
const live = await evaluate(`(() => {
  const text = document.body.innerText.replace(/\\s+/g, ' ');
  const match = text.match(/(conectado|conectando|desconectado|sin conexi[oó]n|reconectando)/i);
  return { indicator: match ? match[0] : null, snippet: text.slice(0, 400) };
})()`);
console.log('=== indicador de conexión ===');
console.log(JSON.stringify(live, null, 2));

const responses = events.filter((e) => e.method === 'Network.responseReceived').map((e) => `${e.params.response.status} ${e.params.response.url}`);
console.log('=== assets servidos ===');
console.log(responses.filter((line) => /alpine|\.js|\.css/.test(line)).join('\n'));
console.log('=== errores de consola ===');
console.log(consoleErrors.slice(0, 8).join('\n') || '(ninguno)');

const shot = await send('Page.captureScreenshot', { format: 'png' });
const { writeFileSync } = await import('node:fs');
writeFileSync(path.join(OUT, '09-auth-dashboard.png'), Buffer.from(shot.result.data, 'base64'));
console.log('captura:', path.join(OUT, '09-auth-dashboard.png'));

ws.close(); chrome.kill(); await sleep(200);
