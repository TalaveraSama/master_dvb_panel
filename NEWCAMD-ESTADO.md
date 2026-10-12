# Newcamd: causa raíz verificada y estado real

Fecha: 2026-10-12. Referencias: código de **Astra/CESBO**
(`modules/softcam/cam/newcamd.c`), **OSCam 2.26.09** del host
(`module-newcamd.c` y `module-newcamd-des.c`) y **forck_astra4** (panel que usa
el módulo `newcamd` nativo de Astra dentro de un script Lua).

## Conclusión corta

El panel **sí habla el protocolo Newcamd correctamente** (handshake, framing,
`md5_crypt`), pero **no puede autenticarse contra OSCam** porque OSCam **no usa
DES estándar**:

```c
/* oscam: module-newcamd-des.c */
unsigned char *nc_des_login_key_get(unsigned char *key1, unsigned char *key2, int len, unsigned char *des16)
{
    unsigned char des14[14];
    memcpy(des14, key1, sizeof(des14));
    for(i = 0; i < len; i++) { des14[i % 14] ^= key2[i]; }
    des16 = des_key_spread(des14, des16);
    doPC1(des16);        /* <-- permutación de clave propia de OSCam */
    doPC1(des16 + 8);    /* <-- idem */
    return des16;
}
```

Además su cifrado (`EuroDes` / `nc_des`) es una implementación **propia** de DES,
no `DES_ede2_cbc_encrypt` de OpenSSL. Por eso un cliente en Python con
`pycryptodome` **nunca** podrá autenticarse contra OSCam: aunque el login esté
perfecto, la clave derivada difiere y OSCam cierra la conexión **sin responder
nada** (no envía siquiera `0xE2` de rechazo).

Astra/CESBO **no** aplica `doPC1()`, así que el mismo cliente sí funcionaría con
Astra o con un servidor Newcamd estándar (por ejemplo NCam con otro tipo de
usuario, o cualquier cardserver que use DES normal).

## Lo que quedó implementado y verificado

| Elemento | Estado |
|---|---|
| Handshake de **14 bytes** (lo que envía OSCam) | ✅ verificado con tráfico real |
| Clave `deskey XOR server_key` + expansión 14→16 + paridad impar | ✅ idéntico a Astra |
| Framing `[len 2B][msg_id][provider][tipo][len payload][payload][pad][checksum XOR]` | ✅ verificado |
| Cifrado **DES-EDE2-CBC** con IV aleatorio al final | ✅ idéntico a Astra |
| Login `0xE0` con **`md5_crypt(pass, "$1$abcdefgh$")`** (34 caracteres) | ✅ 32 tests contra `crypt(3)` |
| `0xE1/0xE2` (ACK/NAK), `0xE4/0xE5` (datos de tarjeta), `0x80..0x8F` (ECM) | ✅ servidor simulado en tests |
| Contraseñas cifradas en SQLite (AES-CTR) | ✅ |
| **Estado real de la tarjeta** vía webif de OSCam | ✅ verificado en el host |

### `md5_crypt` corregido

La implementación anterior no coincidía con glibc por **una condición invertida**
en el bucle "weird" del algoritmo (el C hace `md5_update(&ctx, (i & 1) ? final : pw, 1)`:
bit a 1 → un byte `0x00`; bit a 0 → **sólo `pw[0]`**). Ahora coincide byte a byte
con `crypt(3)` en 7 vectores fijos y 200 contraseñas aleatorias. Son **34**
caracteres (el `char passwd[36]` del C son 34 útiles + NUL).

## La vía que sí funciona: estado real de OSCam

El host tiene OSCam 2.26.09 con la tarjeta leída por **PC/SC**:

```
reader: tv_box4_1802  protocol=pcsc  status=CARDOK   (CAID 1802)
```

OSCam expone su webif en `http://127.0.0.1:8888` **sin autenticación en red
local**, y su API XML da el estado completo:

```
GET /oscamapi.html?part=status
<oscam version="2.26.09-11968" uptime="41288">
  <status>
    <client type="r" name="tv_box4_1802" protocol="pcsc">
      <connection ip="local">CARDOK</connection>
      ...
```

El panel lo consume (`app/services/oscam_status.py` + `GET /api/newcamd/oscam`) y
la pestaña **Newcamd** muestra: versión de OSCam, uptime, lectores con su estado
de tarjeta (`Tarjeta lista`), CAID, inactividad y clientes conectados. Verificado
en el panel real: **10/10 comprobaciones** en navegador (`tools/ui-check-oscam.mjs`).

## Cómo abrir canales de verdad

OSCam es quien tiene la tarjeta y quien puede descifrar. Dos caminos:

1. **Recomendado (sin desarrollo extra):** dejar que **Astra** (que usa su módulo
   `newcamd` nativo y `libdvbcsa`/FFdecsa en C) o **TVHeadend** consuman OSCam y
   hagan el descifrado; el panel sigue siendo la interfaz de control:
   sintoniza, mide señal, escanea, guarda canales, muestra el estado real de la
   tarjeta y exporta M3U/channels.conf. El formulario equivalente al de Astra
   (`NAME / ID / PROTOCOL=NewCAMd / ADDRESS / PORT / LOGIN / PASSWORD` +
   `DES KEY / CAID / TIMEOUT / SHIFT / make new connection for each input`) está
   documentado aquí como modelo a replicar si se quiere configurar Astra desde el
   panel.
2. **Portar el DES de OSCam a C** (`module-newcamd-des.c`: `doPC1`, `desCore`,
   `EuroDes`, `nc_des_encrypt`) y llamarlo desde Python con `ctypes`. Es viable
   pero es un desarrollo aparte y no aporta nada que OSCam/Astra no hagan ya.

**No** se puede descifrar en Python: el descifrado del MPEG-TS se hace en espacio
de usuario con FFdecsa/libdvbcsa (C), como demuestra el propio módulo `softcam`
de Astra.

## Aviso legal

Newcamd es un protocolo de *card sharing*. Úsalo sólo con servidores de tu
propiedad o para los que tengas autorización expresa (tu propio OSCam leyendo tu
tarjeta, o el servidor de tu operador).
