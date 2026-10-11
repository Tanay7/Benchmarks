// =============================================================================
//  AES-256-GCM (NIST SP 800-38D) for the SDLS authenticated-encryption service
//  (CCSDS 355.0-B-2; AES-GCM is the authenticated-encryption algorithm of the
//  CCSDS cryptographic-algorithms recommendation 352.0-B).
//
//    J0  = IV || 0^31 || 1                       (96-bit IV only, §7.1 step 2)
//    C   = GCTR_K(inc32(J0), P)                  (CTR mode, 32-bit counter)
//    S   = GHASH_H(A || 0^v || C || 0^u || [len(A)]64 || [len(C)]64)
//    T   = E_K(J0) xor S                         (full 128-bit tag)
//
//  GHASH uses the bit-serial multiply of SP 800-38D Algorithm 1, written with
//  masks instead of branches. It needs no per-key table (16 octets for H),
//  which is the RAM-friendly choice for a frame or two per second.
//
//  Decryption verifies the tag (constant-time compare) BEFORE touching the
//  buffer: a frame that fails authentication is left exactly as received.
//  Verified against the McGrew-Viega GCM test cases 13-16 in
//  tools/host_test/test_crypto.cpp and cross-checked with an independent
//  implementation (Python `cryptography`) in ground/tests/test_sdls.py.
// =============================================================================
#pragma once
#include "aes.h"

namespace ccsds {

class AesGcm {
 public:
  static const size_t kKeyLen = Aes256::kKeyLen;   // 32
  static const size_t kIvLen = 12;                 // 96-bit IV
  static const size_t kTagLen = 16;                // 128-bit tag

  // Expands the AES key and computes the hash subkey H = E_K(0^128).
  void set_key(const uint8_t key[kKeyLen]);
  void wipe();
  bool keyed() const { return keyed_; }

  // Encrypts `len` octets of `data` in place and writes the tag. `aad` (may be
  // null when aad_len = 0) is authenticated but not encrypted.
  void encrypt(const uint8_t iv[kIvLen], const uint8_t* aad, size_t aad_len,
               uint8_t* data, size_t len, uint8_t tag[kTagLen]) const;
  // Checks the tag over aad || ciphertext; only if it matches decrypts `data`
  // in place and returns true. On false `data` is unchanged.
  bool decrypt(const uint8_t iv[kIvLen], const uint8_t* aad, size_t aad_len,
               uint8_t* data, size_t len, const uint8_t tag[kTagLen]) const;
  // Tag check only (no decryption).
  bool verify(const uint8_t iv[kIvLen], const uint8_t* aad, size_t aad_len,
              const uint8_t* ct, size_t len, const uint8_t tag[kTagLen]) const;

 private:
  void tag_of(const uint8_t iv[kIvLen], const uint8_t* aad, size_t aad_len,
              const uint8_t* ct, size_t len, uint8_t tag[kTagLen]) const;
  void gctr(const uint8_t iv[kIvLen], uint8_t* data, size_t len) const;

  Aes256 aes_;
  uint64_t h_hi_ = 0, h_lo_ = 0;    // H as a 128-bit big-endian integer
  bool keyed_ = false;
};

}  // namespace ccsds
