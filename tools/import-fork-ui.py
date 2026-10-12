"""Descarga el UI del fork (CSS, imagenes y shell) al repo del panel.

Uso:
    .venv\\Scripts\\python.exe tools/import-fork-ui.py

Trae `css/`, `img/` y el `index.html` de referencia desde el host donde se
descomprimio la release `astra-webui-1.0.0-src.tar.gz`. El shell del fork se
guarda como referencia (`index-fork.reference.html`) para comparar el diseno;
el UI activo del panel sigue siendo el nuestro, pero con el aspecto del fork.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from remote import run  # noqa: E402  (helper ya existente del proyecto)

REPO = Path(__file__).resolve().parent.parent
DEST = REPO / "tbs-webui" / "app" / "web"


def fetch(remote_path: str) -> bytes:
    """Lee un fichero del host y devuelve sus bytes."""
    salida = run(
        f"base64 -w0 {remote_path}",
        timeout=120,
    )
    texto = salida.strip()
    return base64.b64decode(texto)


def main() -> int:
    objetivo = DEST / "css" / "fork"
    objetivo.mkdir(parents=True, exist_ok=True)
    traidos = []

    for remoto in ("/tmp/uishare/css/cesbo.css", "/tmp/uishare/css/app.css"):
        nombre = Path(remoto).name
        datos = fetch(remoto)
        (objetivo / nombre).write_bytes(datos)
        traidos.append((str(objetivo / nombre), len(datos)))

    imagenes = DEST / "img"
    imagenes.mkdir(parents=True, exist_ok=True)
    for remoto in ("/tmp/uishare/img/logo.svg", "/tmp/uishare/img/favicon.svg"):
        nombre = Path(remoto).name
        datos = fetch(remoto)
        (imagenes / nombre).write_bytes(datos)
        traidos.append((str(imagenes / nombre), len(datos)))

    shell = fetch("/tmp/uishare/index-fork.html")
    referencia = DEST / "index-fork.reference.html"
    referencia.write_bytes(shell)
    traidos.append((str(referencia), len(shell)))

    for ruta, tamano in traidos:
        print(f"  {tamano:>8} bytes  {ruta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
