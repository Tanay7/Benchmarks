#include "reed_solomon.h"
#include <string.h>

namespace ccsds {

namespace {
const unsigned kNN = 255;       // codeword length (symbols)
const unsigned kNRoots = 32;    // parity symbols
const unsigned kA0 = kNN;       // log(0) sentinel ("alpha^-inf")
const unsigned kGfPoly = 0x187; // x^8 + x^7 + x^2 + x + 1
const unsigned kFcr = 112;      // first consecutive root (in units of prim)
const unsigned kPrim = 11;      // primitive element alpha^11

// Rows of the Berlekamp dual-basis transformation matrix (CCSDS 131.0-B Annex F).
const uint8_t kTal[8] = {0x8d, 0xef, 0xec, 0x86, 0xfa, 0x99, 0xaf, 0x7b};

inline unsigned modnn(unsigned x) {
  while (x >= kNN) {
    x -= kNN;
    x = (x >> 8) + (x & kNN);
  }
  return x;
}
}  // namespace

bool    ReedSolomon::s_ready = false;
uint8_t ReedSolomon::s_alphaTo[256];
uint8_t ReedSolomon::s_indexOf[256];
uint8_t ReedSolomon::s_genpoly[33];
uint8_t ReedSolomon::s_taltab[256];
uint8_t ReedSolomon::s_tal1tab[256];

void ReedSolomon::init() {
  if (s_ready) return;

  // --- GF(2^8) log / antilog tables -------------------------------------------
  s_indexOf[0] = (uint8_t)kA0;
  s_alphaTo[kA0] = 0;
  unsigned sr = 1;
  for (unsigned i = 0; i < kNN; ++i) {
    s_indexOf[sr] = (uint8_t)i;
    s_alphaTo[i] = (uint8_t)sr;
    sr <<= 1;
    if (sr & 0x100) sr ^= kGfPoly;
    sr &= kNN;
  }

  // --- Generator polynomial g(x) = prod (x - alpha^(prim*(fcr+i))) ------------
  uint8_t gp[33];
  memset(gp, 0, sizeof(gp));
  gp[0] = 1;
  unsigned root = kFcr * kPrim;
  for (unsigned i = 0; i < kNRoots; ++i, root += kPrim) {
    gp[i + 1] = 1;
    for (unsigned j = i; j > 0; --j) {
      if (gp[j] != 0)
        gp[j] = gp[j - 1] ^ s_alphaTo[modnn(s_indexOf[gp[j]] + root)];
      else
        gp[j] = gp[j - 1];
    }
    gp[0] = s_alphaTo[modnn(s_indexOf[gp[0]] + root)];
  }
  for (unsigned i = 0; i <= kNRoots; ++i) s_genpoly[i] = s_indexOf[gp[i]];

  // --- Dual-basis <-> conventional conversion tables --------------------------
  for (unsigned i = 0; i < 256; ++i) {
    uint8_t v = 0;
    for (unsigned j = 0; j < 8; ++j)        // each column of the matrix
      for (unsigned k = 0; k < 8; ++k)      // each row of the matrix
        if (i & (1u << k)) v ^= (uint8_t)(kTal[7 - k] & (1u << j));
    s_taltab[i] = v;
    s_tal1tab[v] = (uint8_t)i;
  }
  s_ready = true;
}

uint8_t ReedSolomon::toDual(uint8_t c) { return s_taltab[c]; }
uint8_t ReedSolomon::fromDual(uint8_t d) { return s_tal1tab[d]; }

void ReedSolomon::encode(const uint8_t* data, size_t len, uint8_t parity[32]) {
  if (!s_ready) init();
  // Systematic LFSR encoder (conventional basis). Leading virtual-fill zeros do
  // not change the LFSR state, so the shortened code is handled for free.
  memset(parity, 0, kNRoots);
  for (size_t i = 0; i < len; ++i) {
    const uint8_t conv = s_tal1tab[data[i]];             // dual -> conventional
    const unsigned feedback = s_indexOf[conv ^ parity[0]];
    if (feedback != kA0) {
      for (unsigned j = 1; j < kNRoots; ++j)
        parity[j] ^= s_alphaTo[modnn(feedback + s_genpoly[kNRoots - j])];
    }
    memmove(&parity[0], &parity[1], kNRoots - 1);
    parity[kNRoots - 1] = (feedback != kA0) ? s_alphaTo[modnn(feedback + s_genpoly[0])] : 0;
  }
  for (unsigned i = 0; i < kNRoots; ++i) parity[i] = s_taltab[parity[i]]; // -> dual
}

}  // namespace ccsds
