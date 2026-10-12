#!/usr/bin/env bash
# Despliegue en el host Ubuntu real (192.168.1.21) — ejecutado por el Lead.
# Crea el venv de Python 3.11 con uv, instala dependencias y deja el código listo.
set -uo pipefail

PROJECT="/home/ruben/tbs-webui"
VENV="$PROJECT/.venv"
PY="$VENV/bin/python"

echo "### 1. uv y Python 3.11"
export PATH="$HOME/.local/bin:$PATH"
command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1
uv python install 3.11 >/dev/null 2>&1
uv --version
uv python find 3.11 2>/dev/null || echo "(usando la instalación existente)"

echo
echo "### 2. Entorno virtual"
if [ ! -x "$PY" ]; then
  rm -rf "$VENV"
  uv venv "$VENV" --python 3.11
fi
"$PY" -VV

echo
echo "### 3. Dependencias"
uv pip install --python "$PY" -r "$PROJECT/requirements.txt" -q 2>&1 | tail -3
uv pip install --python "$PY" -r "$PROJECT/requirements-dev.txt" -q 2>&1 | tail -3
"$PY" -c "import fastapi, uvicorn, sqlalchemy, argon2, itsdangerous, httpx, pytest; print('dependencias OK')"

echo
echo "### 4. Sintaxis del código"
"$PY" -m compileall -q "$PROJECT/app" && echo "compileall OK"

echo
echo "### 5. Suite de tests"
cd "$PROJECT" && "$PY" -m pytest tests -q --no-header 2>&1 | tail -12

echo
echo "### 6. Puertos"
ss -ltnp 2>/dev/null | grep -E ":9000|:9001" || echo "9000/9001 libres"
