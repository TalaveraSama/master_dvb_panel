/* roundtrip.c - Comprueba el descifrado de nuestro nc_decrypt paso a paso. */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#include "tbs_newcamd.h"

int main(void)
{
    uint8_t deskey[16];
    const uint8_t server[14] = {0, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88, 0x99, 0xaa, 0xbb, 0xcc, 0xdd};
    const uint8_t client[14] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 0x10, 0x11, 0x12, 0x13, 0x14};

    nc_login_key_get(server, client, 14, deskey);

    /* Mensaje = lo que monta el cliente: [0]=0xE0 [1..2]=long [3..14]=cabecera [15..]=payload */
    uint8_t mensaje[128];
    int len = 15;
    mensaje[0] = 0xE0; mensaje[1] = 0; mensaje[2] = 0;
    mensaje[3] = 0x88; mensaje[4] = 0x88;
    memcpy(mensaje + 5, "astra", 5);
    mensaje[10] = 0;
    memcpy(mensaje + 11, "$1$abcdefgh$lU.OJ3dCh8uiqm.kfyIdF.", 34);
    len = 11 + 34;

    uint8_t original[128];
    memcpy(original, mensaje, len);
    printf("mensaje original (%d bytes): ", len);
    for(int i = 0; i < len; i++) { printf("%02x", original[i]); }
    printf("\n");

    int cifrado = nc_encrypt(mensaje, len, deskey);
    printf("cifrado: %d bytes\n", cifrado);

    int descifrado = nc_decrypt(mensaje, cifrado, deskey);
    printf("nc_decrypt devolvio: %d\n", descifrado);
    if(descifrado < 0)
    {
        printf("FALLO: checksum incorrecto\n");
        return 1;
    }

    printf("descifrado (%d bytes): ", descifrado);
    for(int i = 0; i < descifrado; i++) { printf("%02x", mensaje[i]); }
    printf("\n");

    if(descifrado >= len && memcmp(mensaje, original, len) == 0)
    {
        printf("OK: round-trip correcto\n");
        return 0;
    }
    printf("FALLO: no coincide\n");
    return 1;
}
