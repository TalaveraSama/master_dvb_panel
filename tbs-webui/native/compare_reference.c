/*
 * compare_reference.c - Compara nuestro port con la fuente real de OSCam.
 *
 * Ejecuta `nc_login_key_get` sobre el mismo vector y compara los 16 bytes.
 * Despues cifra el mismo mensaje (con `srand` fijado en ambos lados para que el
 * relleno y el IV aleatorios sean identicos) y compara los paquetes byte a byte.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#include "tbs_newcamd.h"

/* De la fuente de OSCam (reference.c) */
int nc_des_encrypt(unsigned char *buffer, int len, unsigned char *deskey);
int nc_des_decrypt(unsigned char *buffer, int len, unsigned char *deskey);
unsigned char *nc_des_login_key_get(unsigned char *key1, unsigned char *key2, int len, unsigned char *des16);

static const uint8_t SERVER_KEY[14] = {
    0x00, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88, 0x99, 0xaa, 0xbb, 0xcc, 0xdd
};
static const uint8_t CLIENT_KEY[14] = {
    0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x10, 0x11, 0x12, 0x13, 0x14
};

static void dump(const char *label, const unsigned char *data, int len)
{
    printf("%s", label);
    for(int i = 0; i < len; i++) { printf("%02x", data[i]); }
    printf("\n");
}

int main(void)
{
    unsigned char mine[16], reference[16];
    int failures = 0;

    /* 1) Clave de sesion */
    nc_login_key_get(SERVER_KEY, CLIENT_KEY, 14, mine);
    nc_des_login_key_get((unsigned char *)SERVER_KEY, (unsigned char *)CLIENT_KEY, 14, reference);
    dump("nuestra clave : ", mine, 16);
    dump("clave de OSCam: ", reference, 16);
    if(memcmp(mine, reference, 16) != 0)
    {
        printf("FALLO: la clave de sesion NO coincide\n");
        failures++;
    }
    else
    {
        printf("OK: clave de sesion identica\n");
    }

    /* 2) Cifrado del login con el mismo relleno/IV (rand sembrado igual) */
    unsigned char buffer_mine[256], buffer_ref[256];
    const char *user = "astra";
    const char *pass = "$1$abcdefgh$lU.OJ3dCh8uiqm.kfyIdF.";
    int len = 15 + (int)strlen(user) + 1 + (int)strlen(pass);

    memset(buffer_mine, 0, sizeof(buffer_mine));
    buffer_mine[0] = 0xE0;
    buffer_mine[1] = 0;
    buffer_mine[2] = 0;
    buffer_mine[3] = 0x88;
    buffer_mine[4] = 0x88;
    memcpy(buffer_mine + 5, user, strlen(user));
    buffer_mine[5 + strlen(user)] = 0;
    memcpy(buffer_mine + 6 + strlen(user), pass, strlen(pass));
    memcpy(buffer_ref, buffer_mine, sizeof(buffer_mine));

    srand(12345);
    int encrypted_mine = nc_encrypt(buffer_mine, len, mine);
    srand(12345);
    int encrypted_ref = nc_des_encrypt(buffer_ref, len, reference);

    printf("longitudes: nuestra=%d oscam=%d\n", encrypted_mine, encrypted_ref);
    if(encrypted_mine != encrypted_ref)
    {
        printf("FALLO: longitud distinta\n");
        failures++;
    }
    else if(memcmp(buffer_mine, buffer_ref, encrypted_mine) != 0)
    {
        dump("nuestro paquete: ", buffer_mine, encrypted_mine);
        dump("paquete OSCam  : ", buffer_ref, encrypted_ref);
        printf("FALLO: el paquete cifrado NO coincide\n");
        failures++;
    }
    else
    {
        dump("paquete comun: ", buffer_mine, encrypted_mine);
        printf("OK: paquete cifrado identico a OSCam\n");
    }

    /* 3) Descifrado cruzado: lo que cifra OSCam lo descifra nuestro codigo */
    unsigned char cross[256];
    memcpy(cross, buffer_ref, encrypted_ref);
    srand(999);
    int decrypted = nc_decrypt(cross, encrypted_ref, mine);
    if(decrypted < 0)
    {
        printf("FALLO: no pudimos descifrar el paquete de OSCam\n");
        failures++;
    }
    else if(memcmp(cross, buffer_mine, len) != 0)
    {
        printf("FALLO: el descifrado cruzado no devuelve el mensaje original\n");
        failures++;
    }
    else
    {
        printf("OK: desciframos correctamente un paquete de OSCam\n");
    }

    printf(failures ? "\nRESULTADO: %d fallo(s)\n" : "\nRESULTADO: port identico a OSCam\n", failures);
    return failures ? 1 : 0;
}
