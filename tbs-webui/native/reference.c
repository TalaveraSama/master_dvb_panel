/*
 * reference.c - Carga la fuente REAL de OSCam (module-newcamd-des.c) sin el
 * resto del proyecto, para comparar byte a byte con nuestro port.
 *
 * OSCam necesitaba CWS_NETMSGSIZE (un `const int32_t` global) y su cabecera
 * oscam-string.h (solo para cs_malloc, que aqui se sustituye por malloc).
 */

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

const int32_t CWS_NETMSGSIZE = 500;

/* Stub de lo unico que usa module-newcamd-des.c */
#include "globals.h"

/* Ahora incluimos el codigo original de OSCam tal cual */
#include "../../oscam-src/module-newcamd-des.c"
