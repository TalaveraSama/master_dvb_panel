#!/bin/bash
# Descarga la fuente de OSCam (solo la parte de Newcamd DES) y compila una
# version de referencia para comparar con nuestro port.
set -e
mkdir -p /home/ruben/oscam-src
cd /home/ruben/oscam-src

BASE="https://raw.githubusercontent.com/gfto/oscam/refs/heads/master"
for f in module-newcamd-des.c module-newcamd-des.h; do
    if [ ! -s "$f" ]; then
        curl -sSL -o "$f" "$BASE/$f"
    fi
done

ls -l
echo "--- primeras lineas de la cabecera ---"
head -40 module-newcamd-des.h
