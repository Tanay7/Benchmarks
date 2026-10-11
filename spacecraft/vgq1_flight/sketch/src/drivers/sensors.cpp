#include "sensors.h"

namespace vgq {

bool Rm3100::begin(I2cBus& bus, uint8_t addr, uint16_t cc) {
  bus_ = &bus;
  addr_ = addr;
  ok_ = pending_ = false;
  if (!bus.probe(addr)) return false;
  uint8_t rev = 0;
  if (!bus.read(addr, 0x36, &rev, 1) || rev != 0x22) return false;   // REVID
  if (!bus.write8(addr, 0x01, 0x00)) return false;         // CMM off: single-measurement mode
  // The sensor keeps its power across an MCU reset, which may have interrupted a
  // self-test with STE still set: put BIST in its power-on state (0x00, Table 5-1).
  bist_dirty_ = true;
  if (!clear_bist()) return false;
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
  // A POLL with STE set would run the self-test instead of a measurement.
  if (ok_ && bist_dirty_ && !clear_bist()) {
    pending_ = false;
    return false;
  }
  pending_ = ok_ && bus_->write8(addr_, 0x00, 0x70);       // POLL: X, Y, Z
  return pending_;
}

int8_t Rm3100::poll_drdy() {
  if (!pending_ || bus_ == nullptr) return 0;
  uint8_t st = 0;
  if (!bus_->read(addr_, kRm3100RegStatus, &st, 1)) return -1;
  return (st & kRm3100StatusDrdy) ? 1 : 0;
}

bool Rm3100::collect(float& bx, float& by, float& bz, uint32_t timeout_ms) {
  if (!pending_) return false;
  pending_ = false;
  const uint32_t t0 = millis();
  uint8_t st = 0;
  // Bounded twice: by time and by a poll count (one STATUS read per ms at most).
  for (uint32_t i = 0;; ++i) {
    if (!bus_->read(addr_, kRm3100RegStatus, &st, 1)) return false;
    if (st & kRm3100StatusDrdy) break;
    if (millis() - t0 >= timeout_ms || i >= timeout_ms) return false;
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

bool Rm3100::clear_bist() {
  if (!bus_) return false;
  for (uint8_t i = 0; i < kRm3100BistCleanupTries; ++i) {
    if (bus_->write8(addr_, kRm3100RegBist, kRm3100BistOff)) {
      bist_dirty_ = false;
      return true;
    }
  }
  return false;
}

// Manual 5.6.1: write BIST = STE | BW | BP, write POLL, wait for DRDY, read BIST while
// STE is still 1, then clear STE. Writing BIST also clears a stale DRDY (HSHAKE DRC0,
// default 1: "DRDY is cleared by any device register write", 5.6.2), so the DRDY
// awaited here can only be the self-test's own.
MagBistResult Rm3100::run_bist() {
  MagBistResult r = mag_bist_not_run();
  if (!ok_ || !bus_) return r;
  if (pending_) {                                          // between start() and collect()
    r.status = MAG_BIST_BUSY;
    return r;
  }
  bist_dirty_ = true;                                      // STE may be set from here on
  bool bus_ok = bus_->write8(addr_, kRm3100RegBist, kRm3100BistStart) &&
                bus_->write8(addr_, kRm3100RegPoll, kRm3100PollXyz);
  bool drdy = false;
  uint8_t polls = 0;
  if (bus_ok) {
    const uint32_t t0 = micros();
    for (uint8_t i = 0; i < kRm3100BistMaxPolls; ++i) {
      uint8_t st = 0;
      if (!bus_->read(addr_, kRm3100RegStatus, &st, 1)) {
        bus_ok = false;
        break;
      }
      polls = (uint8_t)(i + 1);
      if (st & kRm3100StatusDrdy) {
        drdy = true;
        break;
      }
      if ((uint32_t)(micros() - t0) >= kRm3100BistTimeoutUs) break;
      delayMicroseconds(100);
    }
  }
  uint8_t reg = 0;
  if (bus_ok && drdy && !bus_->read(addr_, kRm3100RegBist, &reg, 1)) bus_ok = false;
  if (!bus_ok) {
    r.status = MAG_BIST_BUS_ERROR;
  } else if (!drdy) {
    r.status = MAG_BIST_TIMEOUT;
  } else {
    r = rm3100_bist_decode(reg);
  }
  r.drdy_polls = polls;
  r.cleanup_ok = clear_bist();                             // STE off: POLL measures again
  return r;
}

}  // namespace vgq
