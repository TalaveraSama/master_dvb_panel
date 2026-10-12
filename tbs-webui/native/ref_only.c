/* ref_only.c - La criptografia Newcamd REAL de OSCam como biblioteca compartida.
 *
 * Se compila junto a la fuente original (module-newcamd-des.c) para poder
 * invocarla desde Python y comparar la conversacion byte a byte con la nuestra.
 */

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

const int32_t CWS_NETMSGSIZE = 500;

#include "../../oscam-src/module-newcamd-des.c"

int ref_key(const unsigned char *server, const unsigned char *client, int len, unsigned char *out)
{
    nc_des_login_key_get((unsigned char *)server, (unsigned char *)client, len, out);
    return 0;
}

int ref_encrypt(unsigned char *buffer, int len, unsigned char *key)
{
    return nc_des_encrypt(buffer, len, key);
}
