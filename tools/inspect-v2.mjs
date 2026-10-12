/**
 * Inspecciona las clases y estilos que el navegador aplica de verdad en /v2.
 */
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import path from 'node:path';

const args = new Map();
for (let i = 2; i < process.argv.length; i += 2) args.set(process.argv[i].replace(/^--/, ''), process.argv[i + 1]);
const BASE = args.get('base');
const PASSWORD = args.get('password') || '';
const CHROME = args.get('chrome');
const PORT = 9247;

const login = await fetch(`${BASE}/api/auth/login`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ username: 'admin', password: PASSWORD }),
});
const cookies = {};
for (const raw of login.headers.getSetCookie()) {
  const [p] = raw.split(';'); const i = p.indexOf('=');
  cookies[p.slice(0, i).trim()] = p.slice(i + 1).trim();
}

const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${PORT}`, `--user-data-dir=${path.join(process.env.TEMP || '.', 'cp-insp')}`, '--no-first-run', '--disable-gpu', 'about:blank'], { stdio: 'ignore' });
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
};
const send = (method, params = {}) => new Promise((resolve) => { id += 1; pend.set(id, resolve); ws.send(JSON.stringify({ id, method, params })); });
const evaluate = async (expr) => {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.result?.exceptionDetails) return { error: JSON.stringify(r.result.exceptionDetails).slice(0, 200) };
  return r.result?.result?.value;
};

await send('Page.enable'); await send('Runtime.enable'); await send('Network.enable');
const host = new URL(BASE).hostname;
for (const [n, v] of Object.entries(cookies)) await send('Network.setCookie', { name: n, value: v, domain: host, path: '/' });
await send('Page.navigate', { url: `${BASE}/v2` });
await sleep(3000);
if (await evaluate(`!document.getElementById('login-screen').hidden`)) {
  await evaluate(`(() => { document.getElementById('login-user').value='admin'; document.getElementById('login-pass').value='${PASSWORD}'; document.getElementById('login-form').requestSubmit(); })()`);
  await sleep(2800);
}

const informe = await evaluate(`(() => {
  const nav = document.querySelector('#nav .nav__link, #nav button');
  const sidebar = document.querySelector('.sidebar');
  const card = document.querySelector('.card');
  const meter = document.querySelector('.meter');
  const shell = getComputedStyle(document.querySelector('.sidebar__brand'));
  return {
    navHtml: nav ? nav.outerHTML.slice(0, 220) : 'sin nav',
    sidebarAncho: sidebar ? getComputedStyle(sidebar).width : null,
    sidebarFlex: sidebar ? getComputedStyle(sidebar).flexBasis : null,
    appDisplay: getComputedStyle(document.getElementById('app')).display,
    navDisplay: nav ? getComputedStyle(nav).display : null,
    cardFondo: card ? getComputedStyle(card).backgroundColor : null,
    cardRadio: card ? getComputedStyle(card).borderRadius : null,
    meterAlto: meter ? getComputedStyle(meter).height : null,
    bodyFondo: getComputedStyle(document.body).backgroundColor,
    claseBody: document.body.className,
    hojaCargada: [...document.styleSheets].map((h) => h.href || 'inline'),
    varsRaiz: getComputedStyle(document.documentElement).getPropertyValue('--panel').trim(),
  };
})()`);
console.log(JSON.stringify(informe, null, 2));
ws.close(); chrome.kill(); await sleep(200);
