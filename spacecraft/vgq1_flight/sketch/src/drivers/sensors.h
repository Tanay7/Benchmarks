// =============================================================================
//  PNI RM3100 geomagnetic sensor - register-level driver (no third-party code).
//
//  Registers (PNI "RM3100 & RM2100 Sensor Suite User Manual", Doc 1017252 r06,
//  Table 5-1): POLL 0x00, CMM 0x01, CCX/CCY/CCZ 0x04..0x09, MX/MY/MZ 0x24..0x2C
//  (24-bit two's complement), BIST 0x33, STATUS 0x34 (bit 7 = DRDY), REVID 0x36
//  (= 0x22). Gain = 0.3671 * cycle_count + 1.5 LSB/uT.
//  I2C address 0x20 + (SA1 << 1 | SA0): outboard 0x20, optional inboard 0x23.
//
//  Two-phase single-shot measurement: start() writes POLL and returns at once;
//  collect() reads the result when DRDY is set. The flight loop starts every
//  magnetometer, reads the Nicla while they convert, then collects - so the
//  conversion time (~ 6.8 ms for X, Y, Z at CC = 200) is overlapped, not waited.
//
//  Built-in self-test (manual 5.6.1, contract 24.5): run_bist() checks the three
//  LR sensor oscillators. With STE set, the next POLL write runs the self-test
//  instead of a normal measurement and DRDY marks its end; ZOK/YOK/XOK are valid
//  only while STE is still 1. The driver therefore never measures with STE set:
//  begin() clears BIST, run_bist() clears it again afterwards, and if that last
//  write fails start() retries it before the next POLL.
// =============================================================================
#pragma once
#include <Arduino.h>
#include "i2c_bus.h"
#include "../fsw/autonomy.h"

namespace vgq {

class Rm3100 {
 public:
  bool begin(I2cBus& bus, uint8_t addr, uint16_t cycle_count = 200);
  bool set_cycle_count(uint16_t cc);
  bool start();                                              // POLL X, Y, Z
  // Waits at most `timeout_ms` for DRDY, then reads the field in uT.
  bool collect(float& bx_uT, float& by_uT, float& bz_uT, uint32_t timeout_ms = 30);
  // Self-test, synchronous and bounded: 5 register transactions plus at most
  // kRm3100BistMaxPolls STATUS reads within kRm3100BistTimeoutUs (about 2 ms of
  // I2C at 100 kHz in the normal case). NOT_RUN when the unit is not fitted, BUSY
  // (bus untouched) between start() and collect(). Call only in a PA-off window
  // with every AS7265X lamp off. The normal measurement path is unchanged.
  MagBistResult run_bist();
  bool ok() const { return ok_; }
  uint8_t addr() const { return addr_; }
  uint16_t cycle_count() const { return cc_; }

 private:
  bool clear_bist();                                         // write BIST = 0, bounded retries
  I2cBus* bus_ = nullptr;
  uint8_t addr_ = 0x20;
  uint16_t cc_ = 200;
  float gain_ = 75.0f;
  bool ok_ = false, pending_ = false;
  bool bist_dirty_ = false;                                  // STE may still be set
};

}  // namespace vgq
