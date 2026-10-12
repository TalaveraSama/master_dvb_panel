/**
 * TBS WebUI - vista de sintonización manual.
 *
 * Construye un TuneRequest completo (INTERFACES.md §5 + ampliaciones del Lead):
 * adaptador, sistema, frecuencia del transponder en kHz (el backend resta el LNB),
 * parámetros DVB-S/S2 y DVB-T/T2, LNB, tono 22 kHz, voltaje y DiSEqC.
 */

import { api } from '../api.js';
import * as store from '../store.js';
import { byId, int, num, setBusy, qsa } from '../utils.js';
import { toast, withBusy } from '../ui.js';

const SYSTEMS = {
  'DVB-S': 'sat',
  'DVB-S2': 'sat',
  'DVB-T': 'terr',
  'DVB-T2': 'terr',
  'DVB-C': 'cable',
  'DVB-C2': 'cable',
  'ISDB-T': 'terr',
  ATSC: 'terr',
};

/** Sistemas que usan symbol rate + polarización. */
const SAT_LIKE = { 'DVB-S': true, 'DVB-S2': true, 'DVB-C': true, 'DVB-C2': true, 'ISDB-T': false, ATSC: false };

function selectedSystem() {
  const select = byId('tune-system');
  return select ? select.value : 'DVB-S2';
}

/** Adaptador/frontend seleccionados. */
function selectedAdapter() {
  const select = byId('tune-adapter');
  if (select && select.value) {
    const parts = select.value.split(':');
    return { adapter: int(parts[0], 0), frontend: int(parts[1], 0) };
  }
  const settings = store.state.settings;
  return {
    adapter: settings ? int(settings.default_adapter, 0) : 0,
    frontend: settings ? int(settings.default_frontend, 0) : 0,
  };
}

/** Muestra u oculta filas según el sistema de entrega. */
function applySystemVisibility() {
  const system = selectedSystem();
  const family = SYSTEMS[system] || 'sat';
  qsa('[data-sys]').forEach((row) => {
    const list = String(row.dataset.sys).split(' ');
    row.hidden = list.indexOf(family) === -1 && list.indexOf('all') === -1;
  });
  const satOnly = SAT_LIKE[system] === true;
  qsa('[data-sat-only]').forEach((row) => {
    row.hidden = !satOnly;
  });
  qsa('[data-terr-only]').forEach((row) => {
    row.hidden = family !== 'terr';
  });
  const hint = byId('tune-freq-hint');
  const mhz = num(byId('tune-frequency') && byId('tune-frequency').value, null);
  if (hint) {
    if (mhz === null) {
      hint.textContent = 'Introduce la frecuencia del transponder en MHz.';
    } else {
      const khz = Math.round(mhz * 1000);
      const lnb = num(byId('tune-lnb-low') && byId('tune-lnb-low').value, null);
      const useLnb = byId('tune-use-lnb') ? byId('tune-use-lnb').checked : true;
      let text = '= ' + khz + ' kHz enviados a la API.';
      if (family === 'sat' && useLnb && lnb) {
        const threshold = num(byId('tune-lnb-threshold') && byId('tune-lnb-threshold').value, 11700);
        const high = num(byId('tune-lnb-high') && byId('tune-lnb-high').value, 10600);
        const lo = khz >= threshold * 1000 ? high : lnb;
        text += ' FI tras LNB: ' + Math.abs(khz - lo * 1000) + ' kHz.';
      }
      hint.textContent = text;
    }
  }
}

/** Carga los adaptadores disponibles en el selector. */
function fillAdapters() {
  const select = byId('tune-adapter');
  if (!select) return;
  const previous = select.value;
  const adapters = store.state.adapters || [];
  select.innerHTML = '';
  if (!adapters.length) {
    const option = document.createElement('option');
    option.value = '0:0';
    option.textContent = 'Sin adaptadores detectados (0/0)';
    select.appendChild(option);
    return;
  }
  adapters.forEach((adapter) => {
    const option = document.createElement('option');
    const frontend = adapter.frontend === undefined ? 0 : adapter.frontend;
    option.value = adapter.adapter + ':' + frontend;
    const name = adapter.name || 'Adaptador ' + adapter.adapter;
    option.textContent = name + ' (' + adapter.adapter + '/' + frontend + ') · ' + (adapter.type || '—');
    select.appendChild(option);
  });
  if (previous) {
    const match = Array.prototype.some.call(select.options, (option) => option.value === previous);
    if (match) select.value = previous;
  }
}

/** Vuelca los ajustes de LNB del backend en el panel de antena. */
function applySettingsToAntenna() {
  const settings = store.state.settings;
  if (!settings) return;
  const lnb = settings.lnb || {};
  if (byId('tune-lnb-low') && !byId('tune-lnb-low').dataset.touched) byId('tune-lnb-low').value = lnb.low_khz || 9750;
  if (byId('tune-lnb-high') && !byId('tune-lnb-high').dataset.touched) byId('tune-lnb-high').value = lnb.high_khz || 10600;
  if (byId('tune-lnb-threshold') && !byId('tune-lnb-threshold').dataset.touched) {
    byId('tune-lnb-threshold').value = lnb.threshold_khz || 11700;
  }
  if (byId('tune-tone') && lnb.tone_22k === true) byId('tune-tone').value = '1';
  if (byId('tune-voltage') && lnb.voltage) byId('tune-voltage').value = lnb.voltage;
}

/** Monta el TuneRequest a partir del formulario. */
export function buildTuneRequest() {
  const adapter = selectedAdapter();
  const system = selectedSystem();
  const family = SYSTEMS[system] || 'sat';
  const satLike = SAT_LIKE[system] === true;
  const mhz = num(byId('tune-frequency').value, null);
  if (mhz === null || mhz <= 0) throw new Error('Introduce una frecuencia válida en MHz.');

  const payload = {
    adapter: adapter.adapter,
    frontend: adapter.frontend,
    delivery_system: system,
    frequency_khz: Math.round(mhz * 1000),
  };

  const polarization = byId('tune-polarization').value;
  if (satLike && polarization) payload.polarization = polarization;
  const symbolRate = int(byId('tune-symbolrate').value, null);
  if (satLike && symbolRate) payload.symbol_rate_ksps = symbolRate;

  if (family === 'sat' || family === 'cable') {
    const fec = byId('tune-fec').value;
    if (fec) payload.fec = fec;
    const modulation = byId('tune-modulation').value;
    if (modulation) payload.modulation = modulation;
  }
  if (family === 'sat') {
    const rolloff = byId('tune-rolloff').value;
    if (rolloff) payload.rolloff = rolloff;
    const pilot = byId('tune-pilot').value;
    if (pilot) payload.pilot = pilot;
    const streamId = int(byId('tune-stream-id').value, null);
    if (streamId !== null) payload.stream_id = streamId;
    const plpId = int(byId('tune-plp-id').value, null);
    if (plpId !== null) {
      payload.plp_id = plpId;
      payload.is_multi_stream = true;
    }
  }
  if (family === 'terr') {
    const bandwidth = int(byId('tune-bandwidth').value, null);
    if (bandwidth) payload.bandwidth_mhz = bandwidth;
    const guard = byId('tune-guard').value;
    if (guard) payload.guard_interval = guard;
    const transmission = byId('tune-transmission').value;
    if (transmission) payload.transmission_mode = transmission;
    const hierarchy = byId('tune-hierarchy').value;
    if (hierarchy) payload.hierarchy = hierarchy;
    const codeRateHp = byId('tune-code-rate-hp').value;
    if (codeRateHp) payload.code_rate_hp = codeRateHp;
    const codeRateLp = byId('tune-code-rate-lp').value;
    if (codeRateLp) payload.code_rate_lp = codeRateLp;
    const modulation = byId('tune-modulation').value;
    if (modulation) payload.modulation = modulation;
  }

  const inversion = byId('tune-inversion').value;
  payload.inversion = inversion || 'AUTO';

  // --- Antena / LNB -------------------------------------------------------
  payload.use_lnb = byId('tune-use-lnb').checked;
  payload.lnb_low_khz = int(byId('tune-lnb-low').value, 9750);
  payload.lnb_high_khz = int(byId('tune-lnb-high').value, 10600);
  payload.lnb_threshold_khz = int(byId('tune-lnb-threshold').value, 11700);

  const tone = byId('tune-tone').value;
  payload.tone_22k = tone === '' ? null : tone === '1';

  const voltage = byId('tune-voltage').value;
  payload.voltage = voltage === '' ? null : voltage;

  const diseqc = byId('tune-diseqc').value;
  payload.diseqc = diseqc === '' ? null : diseqc;

  payload.lock_timeout_s = num(byId('tune-lock-timeout').value, 5);

  return payload;
}

export function init(ctx) {
  const form = byId('tune-form');
  if (!form) return { activate() {}, deactivate() {}, destroy() {} };

  fillAdapters();
  applySettingsToAntenna();
  applySystemVisibility();

  byId('tune-system').addEventListener('change', applySystemVisibility);
  byId('tune-frequency').addEventListener('input', applySystemVisibility);
  ['tune-lnb-low', 'tune-lnb-high', 'tune-lnb-threshold'].forEach((id) => {
    const node = byId(id);
    if (node) {
      node.addEventListener('input', () => {
        node.dataset.touched = '1';
        applySystemVisibility();
      });
    }
  });
  const useLnb = byId('tune-use-lnb');
  if (useLnb) useLnb.addEventListener('change', applySystemVisibility);

  // Guardar los valores de LNB como predeterminados (PUT /api/settings).
  const saveLnbBtn = byId('tune-save-lnb');
  if (saveLnbBtn) {
    saveLnbBtn.addEventListener('click', async () => {
      const settings = store.state.settings;
      if (!settings) {
        toast('Aún no se han cargado los ajustes.', 'warn');
        return;
      }
      const payload = Object.assign({}, settings, {
        lnb: {
          low_khz: int(byId('tune-lnb-low').value, 9750),
          high_khz: int(byId('tune-lnb-high').value, 10600),
          threshold_khz: int(byId('tune-lnb-threshold').value, 11700),
          tone_22k: byId('tune-tone').value === '' ? !!settings.lnb.tone_22k : byId('tune-tone').value === '1',
          voltage: byId('tune-voltage').value || settings.lnb.voltage || '13V',
        },
      });
      delete payload.mock;
      const result = await withBusy(saveLnbBtn, 'Guardando…', () => api.saveSettings(payload));
      if (result.ok) {
        store.emit('settings', result.value);
        toast('Valores de LNB guardados como predeterminados.', 'success');
      }
    });
  }

  // --- Sintonizar ---------------------------------------------------------
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const submit = byId('tune-submit');
    const resultBox = byId('tune-result');
    let payload;
    try {
      payload = buildTuneRequest();
    } catch (error) {
      toast(error.message, 'warn');
      return;
    }

    setBusy(submit, true, 'Sintonizando…');
    if (resultBox) {
      resultBox.hidden = false;
      resultBox.className = 'callout';
      resultBox.textContent = 'Sintonizando ' + (payload.frequency_khz / 1000).toFixed(3) + ' MHz…';
    }
    try {
      const response = await ctx.socket.tune(payload);
      const ok = response ? !!response.ok : true;
      const locked = response ? !!response.locked : null;
      if (resultBox) {
        const parts = [];
        if (response) {
          parts.push('Adaptador ' + response.adapter + '/' + response.frontend);
          if (response.if_frequency_khz) parts.push('FI ' + (response.if_frequency_khz / 1000).toFixed(3) + ' MHz');
          if (response.detail) parts.push(response.detail);
        }
        resultBox.className = 'callout ' + (locked === false ? 'callout-warn' : 'callout-ok');
        resultBox.textContent =
          (locked === null ? 'Sintonización enviada por WebSocket. ' : locked ? 'Bloqueado. ' : 'Sin lock. ') +
          parts.join(' · ');
      }
      if (locked === false) toast('El receptor no ha conseguido el lock. Revisa la señal.', 'warn', 7000);
      else toast('Sintonización aplicada.', 'success');
      store.emit('tuned', payload);
      await ctx.refreshStatus();
    } catch (error) {
      if (resultBox) {
        resultBox.hidden = false;
        resultBox.className = 'callout callout-error';
        resultBox.textContent = error.detail || 'No se pudo sintonizar.';
      }
    } finally {
      setBusy(submit, false);
    }
  });

  // --- Liberar ------------------------------------------------------------
  const releaseBtn = byId('tune-release');
  if (releaseBtn) {
    releaseBtn.addEventListener('click', async () => {
      const result = await withBusy(releaseBtn, 'Liberando…', () => ctx.socket.release());
      if (result.ok) {
        store.emit('tuned', null);
        toast('Frontend liberado.', 'success');
        await ctx.refreshStatus();
      }
    });
  }

  ctx.on('adapters', fillAdapters);
  ctx.on('settings', () => {
    applySettingsToAntenna();
    applySystemVisibility();
  });

  return {
    activate() {
      applySystemVisibility();
    },
    deactivate() {},
    destroy() {},
  };
}

export default { init, buildTuneRequest };
