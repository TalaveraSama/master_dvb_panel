/**
 * TBS WebUI - vista Dashboard: barras de señal/calidad, flags de lock,
 * métricas del receptor y gráfica de los últimos 60 s.
 */

import { SignalChart } from '../charts.js';
import { api } from '../api.js';
import * as store from '../store.js';
import { byId, fmt, fmtBer, fmtPercent, fmtClock, fmtKHzAsMHz, setText, setBusy } from '../utils.js';
import { toast, confirmDialog } from '../ui.js';

const FLAG_LABELS = {
  signal: 'Señal',
  carrier: 'Portadora',
  viterbi: 'Viterbi',
  sync: 'Sinc.',
  lock: 'Lock',
};

function flagList(stats) {
  const flags = (stats && stats.flags) || {};
  return Object.keys(FLAG_LABELS).map((key) => ({
    key: key,
    label: FLAG_LABELS[key],
    active: !!flags[key],
  }));
}

/** Actualiza una barra accesible (role="progressbar"). */
function setBar(barId, fillId, valueId, percent) {
  const bar = byId(barId);
  const fill = byId(fillId);
  const value = byId(valueId);
  const numeric = percent === null || percent === undefined || !Number.isFinite(Number(percent)) ? null : Number(percent);
  const clamped = numeric === null ? 0 : Math.max(0, Math.min(100, numeric));
  if (fill) fill.style.width = clamped.toFixed(1) + '%';
  if (bar) {
    bar.setAttribute('aria-valuenow', numeric === null ? '0' : clamped.toFixed(1));
    bar.setAttribute('aria-valuetext', numeric === null ? 'sin dato' : clamped.toFixed(1) + ' por ciento');
    bar.classList.toggle('bar-empty', numeric === null);
    bar.classList.toggle('bar-warn', numeric !== null && clamped < 35);
  }
  if (value) value.textContent = fmtPercent(numeric);
}

export function init(ctx) {
  const chartCanvas = byId('signal-chart');
  let chart = null;
  if (chartCanvas) {
    chart = new SignalChart(chartCanvas, {
      windowSeconds: (store.state.settings && store.state.settings.ui && store.state.settings.ui.history_seconds) || 60,
    });
  }

  const releaseBtn = byId('release-btn');
  if (releaseBtn) {
    releaseBtn.addEventListener('click', async () => {
      const ok = await confirmDialog({
        title: 'Liberar el frontend',
        message: 'Se detendrá la sintonización actual y se liberará el dispositivo DVB.',
        confirmLabel: 'Liberar',
      });
      if (!ok) return;
      setBusy(releaseBtn, true, 'Liberando…');
      try {
        await ctx.socket.release();
        store.emit('tuned', null);
        toast('Frontend liberado.', 'success');
        await ctx.refreshStatus();
      } catch (error) {
        toast(error.detail || 'No se pudo liberar el frontend.', 'error');
        if (error.status === 503) toast('El dispositivo está ocupado por otro proceso.', 'warn');
      } finally {
        setBusy(releaseBtn, false);
      }
    });
  }

  /** Pinta el panel de flags de lock. */
  function renderFlags(stats) {
    const host = byId('dash-flags');
    if (!host) return;
    host.innerHTML = '';
    flagList(stats).forEach((flag) => {
      const span = document.createElement('span');
      span.className = 'flag' + (flag.active ? ' flag-on' : ' flag-off');
      span.dataset.flag = flag.key;
      span.setAttribute('aria-label', flag.label + ': ' + (flag.active ? 'activo' : 'inactivo'));
      span.innerHTML = '<span class="flag-dot" aria-hidden="true"></span><span>' + flag.label + '</span>';
      host.appendChild(span);
    });
  }

  /** Pinta el resumen de la sintonización actual. */
  function renderTuned(tuned) {
    const host = byId('tuned-summary');
    if (!host) return;
    if (!tuned) {
      host.innerHTML = '<p class="muted">Nada sintonizado.</p>';
      return;
    }
    const rows = [
      ['Sistema', tuned.delivery_system || '—'],
      ['Frecuencia', fmtKHzAsMHz(tuned.frequency_khz)],
      ['FI (tras LNB)', tuned.if_frequency_khz ? fmtKHzAsMHz(tuned.if_frequency_khz) : '—'],
      ['Polarización', tuned.polarization || '—'],
      ['Symbol rate', tuned.symbol_rate_ksps ? tuned.symbol_rate_ksps + ' ksps' : '—'],
      ['FEC', tuned.fec || '—'],
      ['Modulación', tuned.modulation || '—'],
      ['Adaptador', (tuned.adapter !== undefined ? tuned.adapter : 0) + '/' + (tuned.frontend !== undefined ? tuned.frontend : 0)],
    ];
    host.innerHTML =
      '<dl class="kv">' +
      rows
        .map(
          (row) =>
            '<div><dt>' + row[0] + '</dt><dd>' + (row[1] === null || row[1] === undefined ? '—' : String(row[1])) + '</dd></div>'
        )
        .join('') +
      '</dl>';
  }

  /** Vuelca un objeto SignalStatsOut en la interfaz. */
  function renderStats(stats, source, history) {
    if (!stats) return;
    const locked = !!stats.locked;

    const badge = byId('dash-lock-badge');
    if (badge) {
      badge.classList.toggle('is-locked', locked);
      badge.classList.toggle('is-unlocked', !locked);
      badge.textContent = locked ? 'BLOQUEADO' : 'SIN SEÑAL';
      badge.setAttribute('aria-label', locked ? 'Receptor bloqueado' : 'Receptor sin señal');
    }

    setBar('bar-signal', 'bar-signal-fill', 'bar-signal-value', stats.signal_percent);
    setBar('bar-quality', 'bar-quality-fill', 'bar-quality-value', stats.quality_percent);

    setText('m-signal', fmtPercent(stats.signal_percent));
    setText('m-quality', fmtPercent(stats.quality_percent));
    setText('m-snr', stats.snr_db === null || stats.snr_db === undefined ? '—' : fmt(stats.snr_db, 1, ' dB'));
    setText('m-cnr', stats.cnr_raw === null || stats.cnr_raw === undefined ? '—' : fmt(stats.cnr_raw, 2));
    setText('m-dbm', stats.signal_dbm === null || stats.signal_dbm === undefined ? '—' : fmt(stats.signal_dbm, 1, ' dBm'));
    setText('m-ber', fmtBer(stats.ber));
    setText('m-ber-pre', fmtBer(stats.ber_pre));
    setText('m-per', fmtBer(stats.per));
    setText('m-ucblocks', stats.ucblocks === null || stats.ucblocks === undefined ? '—' : String(stats.ucblocks));
    setText('m-updated', fmtClock(stats.timestamp) + (source ? ' · ' + source : ''));
    setText('dash-locked-by', store.state.lockedBy || 'libre');
    setText('dash-mock-mode', store.state.mock ? 'sí (simulación)' : 'no (hardware real)');

    const errorBox = byId('stats-error');
    if (errorBox) {
      if (stats.error) {
        errorBox.hidden = false;
        errorBox.textContent = String(stats.error);
      } else {
        errorBox.hidden = true;
        errorBox.textContent = '';
      }
    }

    renderFlags(stats);

    if (chart) {
      if (source === 'ws' && Array.isArray(history) && history.length) {
        chart.setHistory(history);
      } else {
        chart.push({ t: stats.timestamp, snr: stats.snr_db, signal: stats.signal_percent, ber: stats.ber });
      }
    }
  }

  ctx.on('stats', (payload) => {
    const stats = payload && payload.stats ? payload.stats : payload;
    renderStats(stats, payload && payload.source, payload && payload.history);
  });
  ctx.on('status', (payload) => {
    if (payload && payload.stats) renderStats(payload.stats, 'poll', null);
    if (payload && payload.tuned !== undefined) renderTuned(payload.tuned);
  });
  ctx.on('tuned', (tuned) => renderTuned(tuned));
  ctx.on('settings', (settings) => {
    if (chart && settings && settings.ui && settings.ui.history_seconds) {
      chart.setWindowSeconds(settings.ui.history_seconds);
    }
  });
  ctx.on('conn', (info) => {
    const source = info && info.state === 'open' ? 'ws' : 'poll';
    setText('dash-source', source === 'ws' ? 'WebSocket' : 'polling');
  });

  renderTuned(store.state.tuned);
  if (store.state.stats) renderStats(store.state.stats, store.state.source, null);

  return {
    activate() {
      if (chart) {
        chart.resize();
        chart.render();
      }
    },
    deactivate() {},
    destroy() {
      if (chart) chart.destroy();
    },
    chart: chart,
  };
}

export default { init };
