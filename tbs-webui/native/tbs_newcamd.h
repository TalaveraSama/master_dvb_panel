/*
 * tbs_newcamd.h - Criptografia del protocolo Newcamd (port de OSCam).
 *
 * Ver tbs_newcamd.c para la explicacion completa. Licencia: GPLv3.
 */

#ifndef TBS_NEWCAMD_H
#define TBS_NEWCAMD_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Deriva la clave de sesion de 16 bytes.
 *   key1 = 14 bytes de clave inicial del servidor
 *   key2 = deskey del usuario (14 bytes), len = 14
 *   des16 = salida de 16 bytes
 * Devuelve 0 si todo va bien, -1 con argumentos invalidos. */
int nc_login_key_get(const uint8_t *key1, const uint8_t *key2, int len, uint8_t *des16);

/* Cifra un mensaje Newcamd en el sitio. `buffer` necesita len + 16 bytes de
 * capacidad. Devuelve la longitud cifrada o -1 si el tamano no es valido. */
int nc_encrypt(uint8_t *buffer, int len, const uint8_t *deskey);

/* Descifra y valida el checksum. Devuelve la longitud util (sin el IV) o -1. */
int nc_decrypt(uint8_t *buffer, int len, const uint8_t *deskey);

#ifdef __cplusplus
}
#endif

#endif /* TBS_NEWCAMD_H */
