#!/bin/bash
# Empaqueta el UI del fork astra-webui para traerlo al panel nuevo.
set -e
SRC=/tmp/fork-webui/astra-webui/panel/web
OUT=/tmp/uishare
rm -rf "$OUT"
mkdir -p "$OUT"
cp -r "$SRC/css" "$OUT/"
cp -r "$SRC/img" "$OUT/"
cp "$SRC/index.html" "$OUT/index-fork.html"
mkdir -p "$OUT/js"
cp "$SRC/js/app.js" "$SRC/js/api.js" "$SRC/js/ui.js" "$SRC/js/format.js" "$OUT/js/"
cd /tmp
tar -czf ui.tgz -C "$OUT" .
ls -l /tmp/ui.tgz
echo "=== ficheros ==="
find "$OUT" -type f
echo "=== clases del index (muestra) ==="
grep -oE 'class="[^"]+"' "$OUT/index-fork.html" | head -30 || true
echo "=== estructura de secciones ==="
grep -nE '<(section|nav|aside|header|main)' "$OUT/index-fork.html" | head -25 || true
