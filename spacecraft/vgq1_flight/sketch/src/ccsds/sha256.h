// SHA-256 (FIPS 180-4) and HMAC-SHA-256 (RFC 2104 / FIPS 198-1).
// Self-contained so the uplink authentication does not depend on which crypto
// library a given Arduino core happens to export. Verified against the RFC 4231
// HMAC test vectors in tools/host_test.
#pragma once
#include <stdint.h>
#include <stddef.h>

namespace ccsds {

class Sha256 {
 public:
  Sha256() { reset(); }
  void reset();
  void update(const uint8_t* d, size_t n);
  void final(uint8_t out[32]);
 private:
  void block(const uint8_t* p);
  uint32_t h_[8];
  uint8_t buf_[64];
  size_t len_ = 0;
  uint64_t total_ = 0;
};

// HMAC-SHA-256 over the concatenation of up to three message parts.
void hmac_sha256(const uint8_t* key, size_t key_len,
                 const uint8_t* m1, size_t n1, const uint8_t* m2, size_t n2,
                 const uint8_t* m3, size_t n3, uint8_t out[32]);

// Constant-time comparison (no early exit on the first mismatching octet).
bool ct_equal(const uint8_t* a, const uint8_t* b, size_t n);

}  // namespace ccsds
