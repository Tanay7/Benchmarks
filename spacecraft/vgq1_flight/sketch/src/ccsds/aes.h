// =============================================================================
//  AES-256 forward cipher (FIPS-197), the block cipher under the SDLS
//  authenticated-encryption service (AES-256-GCM, see gcm.h / sdls.h).
//
//  GCM only ever runs the cipher in the forward direction (CTR keystream and
//  the hash subkey H = E_K(0^128)), so the inverse cipher is not implemented.
//  Byte-oriented, one 256-octet S-box in flash and the 240-octet expanded key
//  in RAM - no T-tables, small enough for any MCU. The S-box lookup is not
//  constant-time on CPUs with data caches; the threat model here is an RF
//  adversary who sees frames, not cycle-level timing of the flight MCU.
//
//  Self-contained (no Arduino) and verified against FIPS-197 Appendix C.3 in
//  tools/host_test/test_crypto.cpp.
// =============================================================================
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace ccsds {

class Aes256 {
 public:
  static const size_t kKeyLen = 32;
  static const size_t kBlockLen = 16;
  static const int kRounds = 14;

  void set_key(const uint8_t key[kKeyLen]);
  // One block, forward cipher. `in` and `out` may be the same buffer.
  void encrypt(const uint8_t in[kBlockLen], uint8_t out[kBlockLen]) const;
  // Zeroes the expanded key.
  void wipe();

 private:
  uint8_t rk_[kBlockLen * (kRounds + 1)] = {0};   // 15 round keys
};

// Zeroes `n` octets through a volatile pointer so the compiler cannot drop it.
void secure_zero(void* p, size_t n);

}  // namespace ccsds
