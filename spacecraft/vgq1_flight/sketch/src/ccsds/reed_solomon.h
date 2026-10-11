// =============================================================================
//  CCSDS Reed-Solomon (255,223) encoder, E = 16, interleave depth I = 1
//  (CCSDS 131.0-B §4).
//
//  Code parameters exactly as in the Blue Book:
//    * Galois field GF(2^8), field generator F(x) = x^8 + x^7 + x^2 + x + 1 (0x187)
//    * Code generator g(x) = prod_{j=112}^{143} (x - alpha^(11 j))
//      (first consecutive root 112, primitive element alpha^11)
//    * Symbols on the link are in the Berlekamp DUAL-BASIS representation.
//      We encode in the conventional representation and convert at the edges
//      with the standard T / T^-1 transformation tables (same approach as the
//      widely used Phil Karn libfec "encode_rs_ccsds").
//    * Shortened code: the frame (200 octets) is preceded by 23 octets of
//      *virtual* zero fill that are never transmitted.
//
//  The spacecraft only ever ENCODES (uplink uses BCH, not RS); the decoder lives
//  in the ground segment (ground/dssq/ccsds/rs.py) and is cross-checked against
//  this encoder by tools/host_test.
// =============================================================================
#pragma once
#include <stdint.h>
#include <stddef.h>

namespace ccsds {

class ReedSolomon {
 public:
  // Builds the GF tables, the generator polynomial and the dual-basis tables.
  // Must be called once before encode(). ~1.8 KB of static tables.
  static void init();

  // Encodes `len` data octets (len <= 223; 223 - len octets of virtual fill are
  // implied) given in DUAL-BASIS representation, writes 32 dual-basis parity
  // octets to `parity`.
  static void encode(const uint8_t* data, size_t len, uint8_t parity[32]);

  // Exposed for unit tests.
  static uint8_t toDual(uint8_t conventional);
  static uint8_t fromDual(uint8_t dual);

 private:
  static bool    s_ready;
  static uint8_t s_alphaTo[256];
  static uint8_t s_indexOf[256];
  static uint8_t s_genpoly[33];   // index (log) form
  static uint8_t s_taltab[256];   // conventional -> dual basis
  static uint8_t s_tal1tab[256];  // dual basis   -> conventional
};

}  // namespace ccsds
