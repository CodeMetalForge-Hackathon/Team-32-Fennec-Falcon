// Host-side mbedtls/md.h subset (HMAC-SHA256) implemented with OpenSSL, same signatures.
#pragma once
#include <openssl/evp.h>
#include <openssl/hmac.h>
#include <cstring>
#include <vector>

typedef enum { MBEDTLS_MD_SHA256 = 6 } mbedtls_md_type_t;
typedef struct { int type; } mbedtls_md_info_t;
typedef struct {
  const mbedtls_md_info_t* info;
  unsigned char key[64];
  size_t keylen;
  std::vector<unsigned char> msg;
} mbedtls_md_context_t;

inline const mbedtls_md_info_t* mbedtls_md_info_from_type(mbedtls_md_type_t) {
  static mbedtls_md_info_t i{6};
  return &i;
}
inline void mbedtls_md_init(mbedtls_md_context_t* c) { c->info = nullptr; c->keylen = 0; c->msg.clear(); }
inline int mbedtls_md_setup(mbedtls_md_context_t* c, const mbedtls_md_info_t* i, int) { c->info = i; return 0; }
inline int mbedtls_md_hmac_starts(mbedtls_md_context_t* c, const unsigned char* key, size_t keylen) {
  memcpy(c->key, key, keylen);
  c->keylen = keylen;
  c->msg.clear();
  return 0;
}
inline int mbedtls_md_hmac_update(mbedtls_md_context_t* c, const unsigned char* in, size_t n) {
  c->msg.insert(c->msg.end(), in, in + n);
  return 0;
}
inline int mbedtls_md_hmac_finish(mbedtls_md_context_t* c, unsigned char* out) {
  unsigned int len = 32;
  HMAC(EVP_sha256(), c->key, (int)c->keylen, c->msg.data(), c->msg.size(), out, &len);
  return 0;
}
inline void mbedtls_md_free(mbedtls_md_context_t*) {}
