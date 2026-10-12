/**
 * Comprobación puntual: ¿carga Alpine.js en el navegador real?
 * Uso: node tools/ui-check-alpine.mjs --base http://127.0.0.1:8010 --chrome <ruta> --out <dir>
 */
import { spawn } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { setTimeout as sleep } from 'node:timers/promises';
import path from 'node:path';

const args = new Map();
for (let i = 2; i < process.argv.length; i += 2) args.set(process.argv[i].replace(/^--/, ''), process.argv[i + 1]);
const BASE = args.get('base');
const CHROME = args.get('chrome');
const OUT = args.get('out') || '.';
const PORT = Number(args.get('port') || 9223);
mkdirSync(OUT, { recursive: true });

const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${PORT}`, `--user-data-dir=${path.join(OUT, 'chrome-profile-2')}`, '--no-first-run', '--disable-gpu', 'about:blank'], { stdio: 'ignore' });
process.on('exit', () => chrome.kill());

let info = null;
for (let i = 0; i < 60 && !info; i += 1) {
  try {
    info = await (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json();
  } catch { await sleep(250); }
}
const target = await (await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`, { method: 'PUT' })).json();
const ws = new WebSocket(target.webSocketDebuggerUrl);
let id = 0;
const pending = new Map();
const events = [];
await new Promise((resolve) => { ws.onopen = resolve; });
ws.onmessage = (event) => {
  const message = JSON.parse(event.data);
  if (message.id && pending.has(message.id)) { pending.get(message.id)(message); pending.delete(message.id); }
  else if (message.method) events.push(message);
};
const send = (method, params = {}) => new Promise((resolve) => { id += 1; pending.set(id, resolve); ws.send(JSON.stringify({ id, method, params })); });
const evaluate = async (expression, awaitPromise = false) => {
  const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise });
  return result.result?.result?.value;
};

await send('Page.enable');
await send('Runtime.enable');
await send('Network.enable');
await send('Page.navigate', { url: `${BASE}/` });
await sleep(2500);

const report = await evaluate(`(() => ({
  alpineType: typeof window.Alpine,
  alpineVersion: window.Alpine && window.Alpine.version,
  scriptTags: [...document.querySelectorAll('script[src]')].map(s => s.getAttribute('src')),
  xDataCount: document.querySelectorAll('[x-data]').length,
  xCloakCount: document.querySelectorAll('[x-cloak]').length,
  mobNavClickable: !!document.querySelector('[data-mobile-toggle], #mobile-toggle, .nav-toggle'),
  wsIndicator: (() => {
    const nodes = [...document.querySelectorAll('[data-ws-state],[id*=ws],[class*=ws-]')];
    return nodes.slice(0, 5).map(n => ({ tag: n.tagName, id: n.id, cls: n.className, text: (n.textContent || '').trim().slice(0, 40) }));
  })(),
  errors: window.__tbsErrors || null
}))()`);

const responses = events.filter((e) => e.method === 'Network.responseReceived').map((e) => e.params.response.url + ' -> ' + e.params.response.status);
const alpineResponse = responses.filter((line) => line.includes('alpine'));
console.log(JSON.stringify(report, null, 2));
console.log('--- respuestas de red (alpine y js) ---');
console.log(alpineResponse.join('\n') || '(no se pidió alpine.min.js)');
console.log('--- todas las respuestas 4xx/5xx ---');
console.log(responses.filter((line) => / (4\d\d|5\d\d)$/.test(line)).join('\n') || '(ninguna)');
const failures = events.filter((e) => e.method === 'Network.loadingFailed').map((e) => e.params.errorText + ' ' + e.params.requestId);
console.log('--- fallos de carga ---');
console.log(failures.join('\n') || '(ninguno)');
ws.close();
chrome.kill();
await sleep(200);
