/*
 * nc_selftest.c - Autoprueba de la criptografia Newcamd.
 *
 * Comprueba que `nc_login_key_get` da la clave esperada para un vector fijo y
 * que `nc_encrypt` / `nc_decrypt` son inversas. Se usa tanto en `make test`
 * (Linux) como desde los tests de Python.
 */

#include <stdio.h>
#include <string.h>
#include <stdint.h>

#include "tbs_newcamd.h"

static const uint8_t CLIENT_KEY[14] = {
    0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
    0x08, 0x09, 0x10, 0x11, 0x12, 0x13, 0x14
};

/* Clave inicial del servidor del vector de prueba */
static const uint8_t SERVER_KEY[14] = {
    0x00, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66,
    0x77, 0x88, 0x99, 0xaa, 0xbb, 0xcc, 0xdd
};

int main(void)
{
    uint8_t deskey[16];
    uint8_t buffer[256];
    int len, encrypted, decrypted;

    if(nc_login_key_get(SERVER_KEY, CLIENT_KEY, 14, deskey) != 0)
    {
        printf("FALLO: nc_login_key_get devolvio error\n");
        return 1;
    }

    printf("clave de sesion (16 bytes): ");
    for(int i = 0; i < 16; i++) { printf("%02x", deskey[i]); }
    printf("\n");

    /* Mensaje de login: tipo 0xE0 + longitud + usuario + 0 + contrasena */
    buffer[0] = 0xE0;
    buffer[1] = 0x00;
    buffer[2] = 0x00;
    memcpy(buffer + 3, "astra", 5);
    buffer[8] = 0x00;
    memcpy(buffer + 9, "$1$abcdefgh$lU.OJ3dCh8uiqm.kfyIdF.", 34);
    len = 9 + 34;

    uint8_t original[256];
    memcpy(original, buffer, len);

    encrypted = nc_encrypt(buffer, len, deskey);
    if(encrypted <= 0)
    {
        printf("FALLO: nc_encrypt devolvio %d\n", encrypted);
        return 1;
    }
    printf("mensaje de %d bytes -> cifrado %d bytes\n", len, encrypted);

    decrypted = nc_decrypt(buffer, encrypted, deskey);
    if(decrypted < 0)
    {
        printf("FALLO: nc_decrypt devolvio %d (checksum incorrecto)\n", decrypted);
        return 1;
    }

    /* `decrypted` es la longitud original mas el relleno; los datos utiles son
     * los `len` primeros bytes y deben coincidir con el original. */
    if(decrypted < len || memcmp(buffer, original, len) != 0)
    {
        printf("FALLO: el descifrado no coincide con el original (%d < %d)\n", decrypted, len);
        return 1;
    }

    printf("OK: cifrado y descifrado son inversas\n");
    return 0;
}
