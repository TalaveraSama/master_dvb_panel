/* globals.h - stub minimo para compilar module-newcamd-des.c aislado.
 *
 * El fichero original de OSCam incluye "globals.h" y "oscam-string.h" con todo
 * el proyecto; aqui solo se necesitan los tipos basicos y cs_malloc.
 */

#ifndef TBS_OSCAM_GLOBALS_STUB_H
#define TBS_OSCAM_GLOBALS_STUB_H

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

typedef unsigned char uchar;

#define cs_malloc(ptr, size) (*(ptr) = malloc(size), (ptr) != NULL)

#endif /* TBS_OSCAM_GLOBALS_STUB_H */
