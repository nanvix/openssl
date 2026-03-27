#include <stdio.h>
#include <string.h>
#include <openssl/opensslv.h>
#include <openssl/sha.h>
#include <openssl/bn.h>
int main(void) {
    unsigned char md[SHA256_DIGEST_LENGTH];
    const char *msg = "hello nanvix";
    SHA256((const unsigned char *)msg, strlen(msg), md);
    BIGNUM *bn = BN_new();
    if (!bn) { printf("OPENSSL_TEST: FAIL bn_new\n"); return 1; }
    BN_set_word(bn, 12345);
    char *s = BN_bn2dec(bn);
    if (!s || strcmp(s, "12345") != 0) {
        printf("OPENSSL_TEST: FAIL bn value\n"); return 1; }
    OPENSSL_free(s);
    BN_free(bn);
    printf("OPENSSL_TEST: PASS version=%s\n", OPENSSL_VERSION_TEXT);
    return 0;
}
