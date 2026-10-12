/**
 * TBS WebUI - gráfica histórica en canvas puro (sin librerías).
 *
 * Dibuja las dos series del receptor (Signal % y SNR dB) sobre una ventana
 * temporal configurable (60 s por defecto), con rejilla, ejes y leyenda.
 * Las funciones de escalado son puras y exportadas para poder probarlas.
 */

const DEFAULT_COLORS = {
  grid: 'rgba(148, 163, 184, 0.16)',
  axisText: 'rgba(203, 213, 225, 0.72)',
  signal: '#38bdf8',
  snr: '#fbbf24',
  warning: '#f87171',
  background: 'rgba(15, 23, 42, 0.55)',
};

/** Redondea al siguiente valor "bonito" (escalera 1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10). */
export function niceCeil(value) {
  const v = Number(value);
  if (!Number.isFinite(v) || v <= 0) return 1;
  const exponent = Math.floor(Math.log10(v));
  const base = Math.pow(10, exponent);
  const normalized = v / base;
  const ladder = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10];
  for (let i = 0; i < ladder.length; i += 1) {
    if (normalized <= ladder[i]) return ladder[i] * base;
  }
  return 10 * base;
}

/** Máximo del eje SNR: al menos 20 dB y con margen del 15 %. */
export function snrAxisMax(points) {
  let max = 0;
  (points || []).forEach((p) => {
    const value = Number(p && p.snr);
    if (Number.isFinite(value) && value > max) max = value;
  });
  if (max <= 0) return 20;
  return Math.max(20, niceCeil(max * 1.15));
}

/** Proyecta un valor a la coordenada Y del lienzo. */
export function scaleY(value, min, max, top, bottom) {
  if (!Number.isFinite(value) || max === min) return bottom;
  const ratio = (value - min) / (max - min);
  return bottom - ratio * (bottom - top);
}

/** Proyecta un instante a la coordenada X dentro de la ventana temporal. */
export function scaleX(t, now, windowSeconds, left, right) {
  const start = now - windowSeconds;
  if (!Number.isFinite(t) || windowSeconds <= 0) return left;
  const ratio = (t - start) / windowSeconds;
  return left + Math.min(1, Math.max(0, ratio)) * (right - left);
}

/** Descarta los puntos fuera de la ventana temporal. */
export function visiblePoints(points, now, windowSeconds) {
  const start = now - windowSeconds;
  return (points || []).filter((p) => Number.isFinite(p && p.t) && p.t >= start);
}

/** Lee una variable CSS del documento con valor de reserva. */
function cssVar(name, fallback) {
  try {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name);
    return value && value.trim() ? value.trim() : fallback;
  } catch (_) {
    return fallback;
  }
}

export class SignalChart {
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {{windowSeconds?:number, maxPoints?:number, ariaTarget?:Element}} [opts]
   */
  constructor(canvas, opts) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.opts = opts || {};
    this.windowSeconds = this.opts.windowSeconds || 60;
    this.maxPoints = this.opts.maxPoints || 600;
    this.points = [];
    this.colors = Object.assign({}, DEFAULT_COLORS, {
      grid: cssVar('--chart-grid', DEFAULT_COLORS.grid),
      axisText: cssVar('--chart-text', DEFAULT_COLORS.axisText),
      signal: cssVar('--chart-signal', DEFAULT_COLORS.signal),
      snr: cssVar('--chart-snr', DEFAULT_COLORS.snr),
      background: cssVar('--chart-bg', DEFAULT_COLORS.background),
    });
    this.padding = { top: 22, right: 46, bottom: 22, left: 40 };
    this.onResize = () => this.resize();
    if (typeof ResizeObserver !== 'undefined' && canvas.parentElement) {
      this.observer = new ResizeObserver(this.onResize);
      this.observer.observe(canvas.parentElement);
    } else {
      window.addEventListener('resize', this.onResize);
    }
    this.resize();
  }

  setWindowSeconds(seconds) {
    const value = Number(seconds);
    if (Number.isFinite(value) && value > 5) this.windowSeconds = value;
    this.render();
  }

  /** Carga la historia que envía el servidor al suscribirse. */
  setHistory(history) {
    if (!Array.isArray(history)) return;
    this.points = history
      .filter((p) => p && Number.isFinite(Number(p.t)))
      .map((p) => ({
        t: Number(p.t),
        snr: p.snr === null || p.snr === undefined ? null : Number(p.snr),
        signal: p.signal === null || p.signal === undefined ? null : Number(p.signal),
        ber: p.ber === null || p.ber === undefined ? null : Number(p.ber),
      }));
    this.render();
  }

  /** Añade un punto nuevo y redibuja. */
  push(point) {
    if (!point || !Number.isFinite(Number(point.t))) return;
    this.points.push({
      t: Number(point.t),
      snr: point.snr === null || point.snr === undefined ? null : Number(point.snr),
      signal: point.signal === null || point.signal === undefined ? null : Number(point.signal),
      ber: point.ber === null || point.ber === undefined ? null : Number(point.ber),
    });
    const now = Number(point.t);
    this.points = visiblePoints(this.points, now, this.windowSeconds).slice(-this.maxPoints);
    this.render();
  }

  clear() {
    this.points = [];
    this.render();
  }

  destroy() {
    if (this.observer) this.observer.disconnect();
    else window.removeEventListener('resize', this.onResize);
  }

  /** Ajusta el lienzo al tamaño CSS y a la densidad de píxeles. */
  resize() {
    const canvas = this.canvas;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const cssWidth = Math.max(240, Math.round(rect.width || canvas.clientWidth || 480));
    const cssHeight = Math.max(140, Math.round(rect.height || canvas.clientHeight || 220));
    canvas.width = Math.round(cssWidth * dpr);
    canvas.height = Math.round(cssHeight * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.width = cssWidth;
    this.height = cssHeight;
    this.render();
  }

  /** Dibuja rejilla, ejes, series y leyenda. */
  render() {
    const ctx = this.ctx;
    if (!ctx || !this.width || !this.height) return;
    const { top, right, bottom, left } = this.padding;
    const x0 = left;
    const x1 = this.width - right;
    const y0 = top;
    const y1 = this.height - bottom;
    const now = this.points.length ? this.points[this.points.length - 1].t : Date.now() / 1000;

    ctx.clearRect(0, 0, this.width, this.height);

    // Fondo
    ctx.fillStyle = this.colors.background;
    ctx.fillRect(0, 0, this.width, this.height);

    const snrMax = snrAxisMax(visiblePoints(this.points, now, this.windowSeconds));
    const visible = visiblePoints(this.points, now, this.windowSeconds);

    // Rejilla horizontal + etiquetas (izquierda: %, derecha: dB)
    ctx.font = '11px system-ui, -apple-system, Segoe UI, sans-serif';
    ctx.lineWidth = 1;
    const rows = 4;
    for (let i = 0; i <= rows; i += 1) {
      const y = y0 + ((y1 - y0) * i) / rows;
      ctx.strokeStyle = this.colors.grid;
      ctx.beginPath();
      ctx.moveTo(x0, Math.round(y) + 0.5);
      ctx.lineTo(x1, Math.round(y) + 0.5);
      ctx.stroke();
      ctx.fillStyle = this.colors.axisText;
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';
      ctx.fillText(String(Math.round(100 - (100 * i) / rows)), x0 - 6, y);
      ctx.textAlign = 'left';
      ctx.fillText((snrMax - (snrMax * i) / rows).toFixed(0) + ' dB', x1 + 6, y);
    }

    // Rejilla vertical + etiquetas de tiempo (-60 s .. 0 s)
    const cols = 6;
    for (let i = 0; i <= cols; i += 1) {
      const x = x0 + ((x1 - x0) * i) / cols;
      ctx.strokeStyle = this.colors.grid;
      ctx.beginPath();
      ctx.moveTo(Math.round(x) + 0.5, y0);
      ctx.lineTo(Math.round(x) + 0.5, y1);
      ctx.stroke();
      const secondsAgo = Math.round(this.windowSeconds - (this.windowSeconds * i) / cols);
      ctx.fillStyle = this.colors.axisText;
      ctx.textAlign = i === 0 ? 'left' : i === cols ? 'right' : 'center';
      ctx.textBaseline = 'top';
      ctx.fillText(secondsAgo === 0 ? 'ahora' : '-' + secondsAgo + 's', x, y1 + 6);
    }

    if (!visible.length) {
      ctx.fillStyle = this.colors.axisText;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.font = '13px system-ui, -apple-system, Segoe UI, sans-serif';
      ctx.fillText('Sin datos todavía', (x0 + x1) / 2, (y0 + y1) / 2);
      this.drawLegend(ctx, x0, 12, snrMax);
      return;
    }

    // Serie Signal % (eje izquierdo 0..100)
    this.drawSeries(
      visible,
      (p) => p.signal,
      (value) => scaleY(value, 0, 100, y0, y1),
      now,
      x0,
      x1,
      this.colors.signal,
      true
    );

    // Serie SNR dB (eje derecho 0..snrMax)
    this.drawSeries(
      visible,
      (p) => p.snr,
      (value) => scaleY(value, 0, snrMax, y0, y1),
      now,
      x0,
      x1,
      this.colors.snr,
      false
    );

    this.drawLegend(ctx, x0, 12, snrMax);
  }

  /** Dibuja una serie saltando los huecos (valores nulos). */
  drawSeries(points, pick, project, now, x0, x1, color, fill) {
    const ctx = this.ctx;
    const { top, bottom } = this.padding;
    const y0 = top;
    const y1 = this.height - bottom;
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';

    const segments = [];
    let current = [];
    points.forEach((p) => {
      const value = pick(p);
      if (value === null || value === undefined || !Number.isFinite(value)) {
        if (current.length) segments.push(current);
        current = [];
        return;
      }
      current.push({ x: scaleX(p.t, now, this.windowSeconds, x0, x1), y: project(value) });
    });
    if (current.length) segments.push(current);

    segments.forEach((segment) => {
      if (fill && segment.length > 1) {
        ctx.beginPath();
        ctx.moveTo(segment[0].x, y1);
        segment.forEach((pt) => ctx.lineTo(pt.x, pt.y));
        ctx.lineTo(segment[segment.length - 1].x, y1);
        ctx.closePath();
        ctx.globalAlpha = 0.14;
        ctx.fillStyle = color;
        ctx.fill();
        ctx.globalAlpha = 1;
      }
      if (segment.length === 1) {
        const only = segment[0];
        ctx.beginPath();
        ctx.arc(only.x, only.y, 2.2, 0, Math.PI * 2);
        ctx.fillStyle = color;
        ctx.fill();
        return;
      }
      ctx.beginPath();
      segment.forEach((pt, index) => {
        if (index === 0) ctx.moveTo(pt.x, pt.y);
        else ctx.lineTo(pt.x, pt.y);
      });
      ctx.stroke();
    });
    ctx.restore();
  }

  /** Leyenda superior con los dos colores y sus unidades. */
  drawLegend(ctx, x, y, snrMax) {
    ctx.save();
    ctx.font = '12px system-ui, -apple-system, Segoe UI, sans-serif';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    let cursor = x + 4;
    const entries = [
      { color: this.colors.signal, label: 'Señal (%)' },
      { color: this.colors.snr, label: 'SNR (dB, máx ' + snrMax.toFixed(0) + ')' },
    ];
    entries.forEach((entry) => {
      ctx.fillStyle = entry.color;
      ctx.fillRect(cursor, y - 4, 10, 3);
      cursor += 16;
      ctx.fillStyle = this.colors.axisText;
      ctx.fillText(entry.label, cursor, y - 2);
      cursor += ctx.measureText(entry.label).width + 18;
    });
    ctx.restore();
  }
}

export default SignalChart;
