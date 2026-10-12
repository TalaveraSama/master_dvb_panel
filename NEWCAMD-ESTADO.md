# Newcamd en TBS WebUI — estado real verificado

Fecha: 2026-10-12. Servidor de prueba: **OSCam real** del host (192.168.1.21),
`[newcamd] port = 5000@1802:000000`, usuario `astra`/`astra`, deskey
`0102030405060708091011121314` (`/etc/oscam/oscam.conf`).

## Lo que SÍ está verificado

| Elemento | Resultado |
|---|---|
| Conexión TCP a OSCam (127.0.0.1:5000) | ✅ el servidor acepta y registra `(newcamd) client connected to 5000 port` |
| Nonce inicial de 8 bytes | ✅ recibido (p. ej. `4f3ed95442d7377d`) |
| Cifrado DES del protocolo | ✅ `pycryptodome` (`DES-ECB`, clave `nonce XOR REMOTE_KEY`) |
| Marco de login (usuario 20 B + 32 B cifrados) | ✅ enviado y leído por OSCam como intento de login |
| Aceptación del login por OSCam | ❌ **rechazado** (`(client) anonymous disconnected from 127.0.0.1`) |
| API, persistencia y UI del panel | ✅ 292 tests, cuenta creada/editada/borrada, contraseña cifrada y oculta |
| Servidor de pruebas del protocolo (mock) | ✅ handshake, login OK/rechazado, ECM y recepción de control word |

## Qué falta exactamente

El formato del bloque de credenciales de 32 bytes no coincide con el que espera
este OSCam. Se probaron cuatro variantes contra el servidor real y todas fueron
rechazadas:

| Variante del bloque (32 B) | Resultado |
|---|---|
| contraseña 20 B + deskey 12 B (la implementada) | rechazado |
| contraseña 14 B + deskey 14 B + relleno | rechazado |
| contraseña 20 B + deskey 14 B (recortado a 32) | rechazado |
| sólo contraseña 20 B + relleno | rechazado |

Además, la respuesta de login de este OSCam **no es el formato en claro
`00 01 00`**: llegan 3 bytes que cambian en cada intento (p. ej. `4a 80 ff`),
lo que indica que la respuesta va cifrada y que hay que descifrarla (últimos
8 bytes del bloque con la clave del handshake, XOR con los 8 primeros) para
distinguir éxito de error.

### Cómo terminarlo (método recomendado, 10 minutos)

1. Instalar/libdvbv5 y **`ncam`** o cualquier cliente newcamd de referencia
   (`apt install ncam` no existe en Ubuntu; usar el cliente de OSCam:
   `oscam -c /etc/oscam` con un `[newcamd]` reader, o `newcamd` de
   `dvblink`/`tvheadend`).
2. Capturar la trama real con `strace -f -xx -s 400 -e trace=sendto,recvfrom`
   o `tcpdump -X -s0 -i lo port 5000` mientras ese cliente hace login.
3. Comparar byte a byte con `build_login_block()` en
   `tbs-webui/app/services/newcamd_client.py` y ajustar el formato.
4. Añadir la variante como caso de test en `tests/test_newcamd.py` (el
   servidor simulado ya implementa el protocolo y permite probar sin OSCam).

## Uso del panel (ya operativo)

- Pestaña **Newcamd** → *Nueva cuenta*: nombre, servidor, puerto (5000 para este
  OSCam), usuario, contraseña y deskey.
- **Probar**: intenta el login y muestra el motivo exacto del fallo.
- **Conectar / Desconectar**: mantiene la sesión y muestra ECM enviados,
  control words recibidos y el último CW.
- La contraseña se guarda cifrada (AES-CTR con clave derivada del secreto de la
  aplicación) y **nunca** se devuelve por la API.

## Aviso legal

Newcamd es un protocolo de *card sharing*. Úsalo sólo con servidores de tu
propiedad o para los que tengas autorización expresa (por ejemplo tu propio
OSCam con tu tarjeta, o el servidor de tu operador).
