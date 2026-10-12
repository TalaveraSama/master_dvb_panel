/**
 * Verifica el encuadre y la edición de la vista Newcamd.
 * Uso: node tools/ui-check-newcamd.mjs --base --chrome --out [--password]
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
const PORT = Number(args.get('port') || 9231);
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
const csrf = body.csrf_token;

const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${PORT}`, `--user-data-dir=${path.join(OUT, 'cp-nc')}`, '--no-first-run', '--disable-gpu', '--window-size=1440,1100', 'about:blank'], { stdio: 'ignore' });
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
  else if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') consoleErrors.push((m.params.args || []).map((a) => a.value ?? a.description).join(' '));
};
const send = (method, params = {}) => new Promise((resolve) => { id += 1; pending.set(id, resolve); ws.send(JSON.stringify({ id, method, params })); });
const evaluate = async (expr, awaitPromise = false) => {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise });
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

// Preparar dos cuentas vía API para comprobar la gestión de varias
const headers = { 'X-CSRF-Token': csrf, 'Content-Type': 'application/json' };
for (const name of ['newcamd-layout-a', 'newcamd-layout-b']) {
  await fetch(`${BASE}/api/newcamd/accounts`, {
    method: 'POST', headers,
    body: JSON.stringify({ name, host: '127.0.0.1', port: 5000, username: 'astra', password: 'astra', deskey: '0102030405060708091011121314' }),
  });
}

await send('Page.navigate', { url: `${BASE}/#/newcamd` });
await sleep(2600);

const layout = await evaluate(`(() => {
  const view = document.querySelector('section[data-view="newcamd"]');
  const main = document.querySelector('main');
  const content = document.getElementById('view-host') || main;
  const rect = view ? view.getBoundingClientRect() : null;
  return {
    exists: Boolean(view),
    insideMain: Boolean(view && main && main.contains(view)),
    insideContent: Boolean(view && content && content.contains(view)),
    visible: Boolean(view && !view.hidden),
    left: rect ? Math.round(rect.left) : null,
    width: rect ? Math.round(rect.width) : null,
    mainLeft: main ? Math.round(main.getBoundingClientRect().left) : null,
    rows: document.querySelectorAll('#newcamd-rows tr[data-id]').length,
    summary: (document.getElementById('newcamd-summary') || {}).textContent || null,
    editorHidden: Boolean(document.getElementById('newcamd-editor')?.hidden),
    actionButtons: document.querySelectorAll('#newcamd-rows tr[data-id] button[data-action]').length,
  };
})()`);
check('la vista Newcamd está dentro de <main> (encuadre correcto)', layout.insideMain, JSON.stringify(layout));
check('la vista Newcamd está visible', layout.visible, JSON.stringify(layout));
check('alineada con el contenido (no desbordada)', layout.left !== null && Math.abs(layout.left - layout.mainLeft) <= 120, JSON.stringify(layout));
check('lista las cuentas existentes', layout.rows >= 1, `filas=${layout.rows}`);
check('resumen de cuentas visible', Boolean(layout.summary) && layout.summary !== '—', String(layout.summary));
check('botones de acción por fila', layout.actionButtons >= 5, `botones=${layout.actionButtons}`);
await shot('20-newcamd-list');

// Abrir el editor
await evaluate(`document.getElementById('newcamd-new').click()`);
await sleep(600);
const editor = await evaluate(`(() => {
  const card = document.getElementById('newcamd-editor');
  return {
    hidden: card.hidden,
    title: (document.getElementById('newcamd-editor-title') || {}).textContent,
    fields: ['nc-name','nc-host','nc-port','nc-user','nc-pass','nc-deskey','nc-enabled','nc-auto','nc-notes']
      .filter((id) => document.getElementById(id)).length,
    cardWidth: Math.round(card.getBoundingClientRect().width),
  };
})()`);
check('el editor se abre con todos los campos', !editor.hidden && editor.fields === 9, JSON.stringify(editor));
check('el título del editor cambia', /Nueva cuenta/i.test(editor.title || ''), String(editor.title));
await shot('21-newcamd-editor');

// Crear una tercera cuenta desde el formulario (edición real)
await evaluate(`(() => {
  document.getElementById('nc-name').value = 'desde-ui';
  document.getElementById('nc-host').value = '127.0.0.1';
  document.getElementById('nc-port').value = '5000';
  document.getElementById('nc-user').value = 'astra';
  document.getElementById('nc-pass').value = 'astra';
  document.getElementById('nc-notes').value = 'creada en la prueba';
  document.getElementById('newcamd-form').requestSubmit();
})()`);
await sleep(1200);
let created = await evaluate(`[...document.querySelectorAll('#newcamd-rows tr')].some(tr => tr.textContent.includes('desde-ui'))`);
for (let i = 0; i < 10 && !created; i += 1) {
  await sleep(400);
  created = await evaluate(`[...document.querySelectorAll('#newcamd-rows tr')].some(tr => tr.textContent.includes('desde-ui'))`);
}
check('crear cuenta desde la UI y verla en la tabla', created, String(created));

// Editar la cuenta creada (abrir editor desde la fila)
await evaluate(`(() => {
  const row = [...document.querySelectorAll('#newcamd-rows tr[data-id]')].find(tr => tr.textContent.includes('desde-ui'));
  row.querySelector('button[data-action="edit"]').click();
})()`);
await sleep(500);
const editState = await evaluate(`({
  title: document.getElementById('newcamd-editor-title').textContent,
  name: document.getElementById('nc-name').value,
  notes: document.getElementById('nc-notes').value
})`);
check('editar carga los datos de la fila', editState.name === 'desde-ui' && editState.notes === 'creada en la prueba', JSON.stringify(editState));
check('el título indica edición', /Editar/i.test(editState.title), editState.title);
await shot('22-newcamd-edit');

// Duplicar
await evaluate(`document.getElementById('nc-cancel').click()`);
await sleep(300);
await evaluate(`(() => {
  const row = [...document.querySelectorAll('#newcamd-rows tr[data-id]')].find(tr => tr.textContent.includes('desde-ui'));
  row.querySelector('button[data-action="duplicate"]').click();
})()`);
await sleep(500);
const duplicate = await evaluate(`document.getElementById('nc-name').value`);
check('duplicar prepara una copia', /-copia$/.test(duplicate), String(duplicate));
await evaluate(`document.getElementById('nc-cancel').click()`);

check('sin errores de consola en la vista', consoleErrors.length === 0, consoleErrors.slice(0, 3).join(' | '));

console.log('\n' + '='.repeat(66));
const failed = results.filter((r) => !r.ok);
console.log(`RESULTADO NEWCAMD: ${results.length - failed.length}/${results.length} comprobaciones OK`);
for (const f of failed) console.log(`  - ${f.label}: ${f.detail}`);
ws.close(); chrome.kill(); await sleep(200);
process.exit(failed.length ? 1 : 0);
