#include "crc16.h"

namespace ccsds {

uint16_t crc16_ccitt(const uint8_t* data, size_t len, uint16_t crc) {
  // Bit-serial implementation: at <= 1 frame / second the cost is negligible and
  // it avoids a 512-byte table in the (RAM-constrained) sketch heap.
  for (size_t i = 0; i < len; ++i) {
    crc ^= (uint16_t)data[i] << 8;
    for (int b = 0; b < 8; ++b) {
      crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
    }
  }
  return crc;
}

}  // namespace ccsds
