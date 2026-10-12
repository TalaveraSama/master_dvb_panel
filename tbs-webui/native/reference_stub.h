/* reference_stub.h - minimo necesario para compilar module-newcamd-des.c solo. */

#ifndef TBS_REFERENCE_STUB_H
#define TBS_REFERENCE_STUB_H

#include <stdint.h>
#include <stdlib.h>

/* oscam-string.h expone cs_malloc; el modulo DES no usa nada mas */
#define cs_malloc(ptr, size) (*(ptr) = malloc(size), (ptr) != NULL)

#endif /* TBS_REFERENCE_STUB_H */
