// =============================================================================
//  PNI RM3100 geomagnetic sensor - register-level driver (no third-party code).
//
//  Registers (PNI RM3100 datasheet): POLL 0x00, CMM 0x01, CCX/CCY/CCZ 0x04..0x09,
//  MX/MY/MZ 0x24..0x2C (24-bit two's complement), STATUS 0x34 (bit 7 = DRDY),
//  REVID 0x36 (= 0x22). Gain = 0.3671 * cycle_count + 1.5 LSB/uT.
//  I2C address 0x20 + (SA1 << 1 | SA0): outboard 0x20, optional inboard 0x23.
//
//  Two-phase single-shot measurement: start() writes POLL and returns at once;
//  collect() reads the result when DRDY is set. The flight loop starts every
//  magnetometer, reads the Nicla while they convert, then collects - so the
//  conversion time (~ 6.8 ms for X, Y, Z at CC = 200) is overlapped, not waited.
// =============================================================================
#pragma once
#include <Arduino.h>
#include "i2c_bus.h"

namespace vgq {

class Rm3100 {
 public:
  bool begin(I2cBus& bus, uint8_t addr, uint16_t cycle_count = 200);
  bool set_cycle_count(uint16_t cc);
  bool start();                                              // POLL X, Y, Z
  // Waits at most `timeout_ms` for DRDY, then reads the field in uT.
  bool collect(float& bx_uT, float& by_uT, float& bz_uT, uint32_t timeout_ms = 30);
  bool ok() const { return ok_; }
  uint8_t addr() const { return addr_; }
  uint16_t cycle_count() const { return cc_; }

 private:
  I2cBus* bus_ = nullptr;
  uint8_t addr_ = 0x20;
  uint16_t cc_ = 200;
  float gain_ = 75.0f;
  bool ok_ = false, pending_ = false;
};

}  // namespace vgq
