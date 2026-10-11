#include "randomizer.h"

namespace ccsds {

namespace {
uint8_t g_seq[255];
bool g_ready = false;

void build() {
  // Fibonacci LFSR, shift-left, output = register MSB. For this bit ordering
  // h(x) = x^8 + x^7 + x^5 + x^3 + 1 maps onto register taps {7,4,2,0}.
  // Verified against the 40-bit prefix quoted in CCSDS 131.0-B
  // (FF 48 0E C0 9A ...) by tools/host_test and ground/tests.
  uint8_t sr = 0xFF;
  for (int i = 0; i < 255; ++i) {
    uint8_t out = 0;
    for (int b = 0; b < 8; ++b) {
      const uint8_t bit = (sr >> 7) & 1;
      out = (uint8_t)((out << 1) | bit);
      const uint8_t fb = ((sr >> 7) ^ (sr >> 4) ^ (sr >> 2) ^ (sr >> 0)) & 1;
      sr = (uint8_t)((sr << 1) | fb);
    }
    g_seq[i] = out;
  }
  g_ready = true;
}
}  // namespace

void randomize(uint8_t* data, size_t len) {
  if (!g_ready) build();
  for (size_t i = 0; i < len; ++i) data[i] ^= g_seq[i % 255];
}

}  // namespace ccsds
