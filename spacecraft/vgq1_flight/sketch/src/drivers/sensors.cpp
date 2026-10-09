#include "sensors.h"

namespace vgq {

bool Rm3100::begin(I2cBus& bus, uint8_t addr, uint16_t cc) {
  bus_ = &bus;
  addr_ = addr;
  ok_ = pending_ = false;
  if (!bus.probe(addr)) return false;
  uint8_t rev = 0;
  bus.read(addr, 0x36, &rev, 1);                           // REVID
  if (rev != 0x22) return false;
  bus.write8(addr, 0x01, 0x00);                            // CMM off: single-measurement mode
  ok_ = set_cycle_count(cc);
  return ok_;
}

bool Rm3100::set_cycle_count(uint16_t cc) {
  if (!bus_) return false;
  const uint8_t b[6] = {(uint8_t)(cc >> 8), (uint8_t)cc, (uint8_t)(cc >> 8), (uint8_t)cc,
                        (uint8_t)(cc >> 8), (uint8_t)cc};
  if (!bus_->write(addr_, 0x04, b, 6)) return false;       // CCX, CCY, CCZ
  cc_ = cc;
  gain_ = 0.3671f * (float)cc + 1.5f;                      // LSB per uT (PNI datasheet)
  return true;
}

bool Rm3100::start() {
  pending_ = ok_ && bus_->write8(addr_, 0x00, 0x70);       // POLL: X, Y, Z
  return pending_;
}

bool Rm3100::collect(float& bx, float& by, float& bz, uint32_t timeout_ms) {
  if (!pending_) return false;
  pending_ = false;
  const uint32_t t0 = millis();
  uint8_t st = 0;
  for (;;) {
    if (!bus_->read(addr_, 0x34, &st, 1)) return false;    // STATUS
    if (st & 0x80) break;                                  // DRDY
    if (millis() - t0 >= timeout_ms) return false;
    delay(1);
  }
  uint8_t m[9];
  if (!bus_->read(addr_, 0x24, m, 9)) return false;
  int32_t v[3];
  for (int i = 0; i < 3; ++i) {
    int32_t x = ((int32_t)m[3 * i] << 16) | ((int32_t)m[3 * i + 1] << 8) | m[3 * i + 2];
    if (x & 0x800000) x -= 0x1000000;                      // 24-bit two's complement
    v[i] = x;
  }
  const float inv = 1.0f / gain_;                          // one division, three multiplies
  bx = (float)v[0] * inv;
  by = (float)v[1] * inv;
  bz = (float)v[2] * inv;
  return true;
}

}  // namespace vgq
