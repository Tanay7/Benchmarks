#include "gcm.h"
#include "sha256.h"     // ct_equal()
#include <string.h>

namespace ccsds {

namespace {
inline uint64_t load64(const uint8_t* p) {
  uint64_t v = 0;
  for (int i = 0; i < 8; ++i) v = (v << 8) | p[i];
  return v;
}
inline void store64(uint8_t* p, uint64_t v) {
  for (int i = 7; i >= 0; --i) { p[i] = (uint8_t)v; v >>= 8; }
}

// X <- X . H in GF(2^128) with the GCM bit order (SP 800-38D §6.3, Algorithm 1):
// bit 0 is the MSB of octet 0, R = 11100001 || 0^120. Branch-free on the data.
void gf_mult(uint64_t& xh, uint64_t& xl, uint64_t hh, uint64_t hl) {
  uint64_t zh = 0, zl = 0, vh = hh, vl = hl;
  for (int i = 0; i < 128; ++i) {
    const uint64_t m = 0 - ((i < 64 ? xh >> (63 - i) : xl >> (127 - i)) & 1);
    zh ^= vh & m;
    zl ^= vl & m;
    const uint64_t r = 0 - (vl & 1);           // V >> 1, folding in R when a 1 drops out
    vl = (vl >> 1) | (vh << 63);
    vh = (vh >> 1) ^ (0xE100000000000000ULL & r);
  }
  xh = zh;
  xl = zl;
}

// GHASH over `n` octets, the last partial block zero-padded (the 0^v / 0^u of §7.1).
void ghash(uint64_t& yh, uint64_t& yl, uint64_t hh, uint64_t hl, const uint8_t* p, size_t n) {
  while (n) {
    uint8_t b[16] = {0};
    const size_t take = n < 16 ? n : 16;
    memcpy(b, p, take);
    yh ^= load64(b);
    yl ^= load64(b + 8);
    gf_mult(yh, yl, hh, hl);
    p += take;
    n -= take;
  }
}
}  // namespace

void AesGcm::set_key(const uint8_t key[kKeyLen]) {
  aes_.set_key(key);
  uint8_t h[16] = {0};
  aes_.encrypt(h, h);
  h_hi_ = load64(h);
  h_lo_ = load64(h + 8);
  secure_zero(h, sizeof h);
  keyed_ = true;
}

void AesGcm::wipe() {
  aes_.wipe();
  h_hi_ = h_lo_ = 0;
  keyed_ = false;
}

void AesGcm::gctr(const uint8_t iv[kIvLen], uint8_t* data, size_t len) const {
  uint8_t cb[16], ks[16];
  memcpy(cb, iv, kIvLen);
  uint32_t ctr = 2;                              // inc32(J0), J0 ends in 0x00000001
  while (len) {
    cb[12] = (uint8_t)(ctr >> 24); cb[13] = (uint8_t)(ctr >> 16);
    cb[14] = (uint8_t)(ctr >> 8);  cb[15] = (uint8_t)ctr;
    aes_.encrypt(cb, ks);
    const size_t take = len < 16 ? len : 16;
    for (size_t i = 0; i < take; ++i) data[i] ^= ks[i];
    data += take;
    len -= take;
    ++ctr;
  }
  secure_zero(ks, sizeof ks);
}

void AesGcm::tag_of(const uint8_t iv[kIvLen], const uint8_t* aad, size_t aad_len,
                    const uint8_t* ct, size_t len, uint8_t tag[kTagLen]) const {
  uint64_t yh = 0, yl = 0;
  ghash(yh, yl, h_hi_, h_lo_, aad, aad_len);
  ghash(yh, yl, h_hi_, h_lo_, ct, len);
  uint8_t lens[16];
  store64(lens, (uint64_t)aad_len * 8);           // bit lengths
  store64(lens + 8, (uint64_t)len * 8);
  ghash(yh, yl, h_hi_, h_lo_, lens, 16);
  uint8_t j0[16];
  memcpy(j0, iv, kIvLen);
  j0[12] = 0; j0[13] = 0; j0[14] = 0; j0[15] = 1;
  aes_.encrypt(j0, j0);
  store64(tag, yh);
  store64(tag + 8, yl);
  for (size_t i = 0; i < kTagLen; ++i) tag[i] ^= j0[i];
}

void AesGcm::encrypt(const uint8_t iv[kIvLen], const uint8_t* aad, size_t aad_len,
                     uint8_t* data, size_t len, uint8_t tag[kTagLen]) const {
  gctr(iv, data, len);
  tag_of(iv, aad, aad_len, data, len, tag);
}

bool AesGcm::verify(const uint8_t iv[kIvLen], const uint8_t* aad, size_t aad_len,
                    const uint8_t* ct, size_t len, const uint8_t tag[kTagLen]) const {
  if (!keyed_) return false;
  uint8_t t[kTagLen];
  tag_of(iv, aad, aad_len, ct, len, t);
  return ct_equal(t, tag, kTagLen);
}

bool AesGcm::decrypt(const uint8_t iv[kIvLen], const uint8_t* aad, size_t aad_len,
                     uint8_t* data, size_t len, const uint8_t tag[kTagLen]) const {
  if (!verify(iv, aad, aad_len, data, len, tag)) return false;
  gctr(iv, data, len);
  return true;
}

}  // namespace ccsds
