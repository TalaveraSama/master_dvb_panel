import re
from pathlib import Path

WEB = Path("tbs-webui/app/web")

# --- 1) Shell: marca lateral, nav y boton de menu --------------------------
shell = WEB / "index-fork.html"
texto = shell.read_text(encoding="utf-8")

texto = texto.replace(
    '''      <div class="sidebar__brand">
        <img src="/img/logo.svg" alt="" width="28" height="28">
        <span>TBS WebUI</span>
      </div>''',
    '''      <div class="sidebar__brand">
        <div class="row row--tight" style="align-items:center;gap:10px">
          <img src="/img/logo.svg" alt="" height="26">
          <div>
            <div style="font-weight:650">TBS WebUI</div>
            <div class="muted small" id="brand-sub">panel DVB</div>
          </div>
        </div>
      </div>''',
)
texto = texto.replace(
    '''        <div class="sidebar__status">
          <span class="badge" id="conn-badge">conectando…</span>
          <span class="badge" id="mock-badge" hidden>simulación</span>
        </div>
        <div class="sidebar__status">
          <span class="badge" id="user-badge">—</span>
          <button class="btn btn--ghost btn--sm" id="logout-btn" type="button">Salir</button>
        </div>''',
    '''        <div class="row row--tight" style="justify-content:space-between;align-items:center">
          <span class="badge badge--info" id="conn-badge">conectando…</span>
          <span class="badge badge--warn" id="mock-badge" hidden>simulación</span>
        </div>
        <div class="row row--tight" style="justify-content:space-between;align-items:center">
          <span class="badge badge--brand" id="user-badge">—</span>
          <button class="btn btn--ghost btn--sm" id="logout-btn" type="button">Salir</button>
        </div>''',
)
texto = texto.replace('<span class="badge" id="lock-badge">—</span>', '<span class="badge badge--info" id="lock-badge">—</span>')
texto = texto.replace('<span class="badge" id="tuner-badge">—</span>', '<span class="badge badge--brand" id="tuner-badge">—</span>')
texto = texto.replace('<div class="login__brand">\n        <img src="/img/logo.svg" alt="" width="40" height="40">\n        <span>TBS WebUI</span>\n      </div>',
                      '<div class="login__brand">\n        <img src="/img/logo.svg" alt="TBS WebUI" height="34">\n      </div>')
shell.write_text(texto, encoding="utf-8")

# --- 2) JS: clases reales del CSS del fork ---------------------------------
js = WEB / "js" / "forkskin.js"
codigo = js.read_text(encoding="utf-8")

reemplazos = [
    # Navegacion
    ("'nav__item'", "'nav__link'"),
    # Barras: el fork usa .meter / .meter__fill
    ('el("div", "bar-row")', 'el("div", "mb")'),
    ('class="bar-row__head"', 'class="row row--tight"'),
    ('class="bar-row__value"', 'class="num"'),
    ('class="bar"', 'class="meter"'),
    ('class="bar__fill"', 'class="meter__fill"'),
    ('.bar__fill', '.meter__fill'),
    # Avisos: el fork usa alert--danger, no alert--error
    ("alert--${tipo === 'ok' ? 'ok' : tipo === 'error' ? 'error' : 'info'}", "alert--${tipo === 'ok' ? 'ok' : tipo === 'error' ? 'danger' : 'info'}"),
    # Rejillas del fork
    ("'grid grid--2'", "'grid-2'"),
    # Insignias
    ("'badge badge--ok'", "'badge badge--ok'"),
]
for viejo, nuevo in reemplazos:
    codigo = codigo.replace(viejo, nuevo)

# Las barras devuelven un div; el helper `barra` usaba innerHTML con .bar
codigo = codigo.replace(
    '''    <div class="meter"><div class="meter__fill" style="width:${numero === null ? 0 : Math.max(0, Math.min(100, numero))}%"></div></div>`;''',
    '''    <div class="meter"><div class="meter__fill" style="width:${numero === null ? 0 : Math.max(0, Math.min(100, numero))}%"></div></div>`;''',
)
js.write_text(codigo, encoding="utf-8")

print("shell y JS actualizados a las clases del fork")
for ruta in (shell, js):
    print(" ", ruta, ruta.stat().st_size, "bytes")
