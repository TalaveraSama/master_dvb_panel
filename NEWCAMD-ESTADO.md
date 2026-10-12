# Newcamd: estado real verificado y qué falta

Fecha: 2026-10-12. Referencias usadas:

- **Cliente Newcamd de Astra/CESBO** (`cesbo-astra-646cc63bdf14/modules/softcam/cam/newcamd.c`),
  que es la implementación de CS378x tomada como fuente de verdad del protocolo.
- **OSCam real** del host: `[newcamd] port = 5000@1802:000000`,
  `key = 0102030405060708091011121314`, usuario `astra` / `astra` (contrasena en claro).

## Lo que la referencia de Astra aclara (y ya está implementado)

| Elemento | Detalle de la referencia | Estado |
|---|---|---|
| Clave del handshake | `triple_des_set_key()`: `tmp = deskey XOR server_key` (XOR **cíclico**), expansión 14→16 B con `DES_set_odd_parity` | ✅ implementado (`derive_triple_des_key`) |
| Cifrado | **DES-EDE2-CBC** (no ECB) con IV aleatorio al final del paquete | ✅ implementado |
| Formato de paquete | `[len 2B][msg_id 2B][provider 2B][8B][tipo][len payload 2B][payload][pad][checksum XOR]`, y todo desde el byte 2 va cifrado | ✅ implementado (`build_packet`, `parse_packet`) |
| Checksum | XOR de todos los bytes del cuerpo (cifrado incluido) | ✅ implementado y testeado |
| Login | `0xE0` con `usuario + '\0' + contraseña`; respuesta `0xE1` (OK) / `0xE2` (NAK) | ✅ implementado |
| Datos de tarjeta | `0xE4` → `0xE5` con CAID, UA y lista de providers | ✅ implementado |
| ECM | tipo `0x80..0x8F`; respuesta con los control words | ✅ implementado |
| **Descifrado del vídeo** | Astra **NO** inyecta claves por ioctl: descifra el MPEG-TS en espacio de usuario con **FFdecsa** (`modules/softcam/FFdecsa/`) o **libdvbcsa** (`contrib/libdvbcsa.sh`) y reemite el TS limpio | ❌ fuera del alcance de un panel Python (ver abajo) |

## Verificación contra el OSCam real

Probado en el host con el cliente implementado:

| Comprobación | Resultado |
|---|---|
| Conexión TCP a `127.0.0.1:5000` | ✅ OSCam registra `(newcamd) client connected to 5000 port` |
| Clave inicial del servidor | ✅ **8 bytes** (p. ej. `87ebd27e1cdb5892`), no 14 |
| Login enviado | ✅ OSCam lo recibe y cierra la conexión sin error de red |
| Login aceptado | ❌ OSCam responde y desconecta como `anonymous` |

### Por qué no cuadra todavía

Con el handshake de 8 bytes, la respuesta de OSCam medida en crudo es
`5f 56 fd 0c 09 63` (6 bytes) y no encaja con un marco
`[longitud][cuerpo]`: si esos dos primeros bytes fueran la longitud, el cuerpo
que sigue sería de 4 bytes, y el `0xE1/0xE2` no aparece. Las pruebas con 14 bytes
de clave inicial no reciben respuesta.

La conclusión honesta es que **este OSCam no habla exactamente el mismo framing
que documenta la versión de Astra del repositorio** (puede ser una variante de
newcamd, o la respuesta va cifrada de otra forma). Para cerrarlo hacen falta los
bytes exactos de un login correcto de un cliente de referencia:

```bash
# en el host, mientras un cliente newcamd conocido (por ejemplo tvheadend,
# ncam o el propio oscam como cliente) hace login contra el puerto 5000:
sudo tcpdump -X -s0 -i lo 'tcp port 5000' -c 40
# o, si el cliente se ejecuta en la misma máquina:
strace -f -xx -s 400 -e trace=sendto,recvfrom,read,write <cliente> 2>&1 | grep -A2 5000
```

Con ese volcado se ajustan dos funciones (`build_packet`/`parse_packet`) y se
añade el caso como test en `tests/test_newcamd_protocol.py`, que ya incluye un
servidor simulado del protocolo.

## Importante: Newcamd solo no abre el canal

Aunque el login Newcamd funcione, **la imagen seguirá cifrada** si el panel no
descifra el TS. El panel actual (FastAPI + Python) puede:

- sintonizar y medir señal (hecho y verificado en la 6904se),
- escanear y guardar canales (hecho),
- hablar Newcamd y obtener los control words (implementado),

pero **no** puede descifrar un MPEG-TS en tiempo real en Python con garantías
(haría falta FFdecsa/libdvbcsa en C, como hace Astra, y reemitir el TS por
HTTP/UDP con `dvbv5-zap` o el demux).

Opciones realistas para "abrir canales":

1. **Delegar en un servidor de streaming** ya probado (Astra, TVHeadend,
   tvheadend + OSCam). El panel queda como gestor/configurador: sintoniza,
   escanea, guarda canales, gestiona las cuentas Newcamd y exporta la lista M3U.
2. **Añadir un pipeline de descifrado en C** (FFdecsa o libdvbcsa) invocado
   desde Python, con `dvbv5-zap` alimentando el demux y un pequeño servidor
   HTTP/UDP para el TS limpio. Es un desarrollo aparte (y sensible a la carga
   de CPU), no un ajuste del panel.

Recomendación: usar la opción 1 con Astra/TVHeadend para la parte de vídeo y
dejar este panel como interfaz de control (que es exactamente lo que ya hace y
está verificado).

## Uso del panel (operativo)

- Pestaña **Newcamd** → *Nueva cuenta*: nombre, servidor, puerto (5000 aquí),
  usuario, contraseña y deskey.
- **Probar**: intenta el login y muestra el motivo exacto del fallo.
- **Conectar / Desconectar**: mantiene la sesión y muestra ECM enviados,
  control words recibidos, CAID, providers y el último CW.
- La contraseña se guarda cifrada (AES-CTR con clave derivada del secreto de la
  aplicación) y **nunca** se devuelve por la API.

## Aviso legal

Newcamd es un protocolo de *card sharing*. Úsalo sólo con servidores de tu
propiedad o para los que tengas autorización expresa (por ejemplo tu propio
OSCam con tu tarjeta, o el servidor de tu operador).
