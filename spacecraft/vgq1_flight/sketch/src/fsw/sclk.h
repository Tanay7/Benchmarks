// Spacecraft clock (SCLK): free-running 64-bit microsecond counter since MCU boot,
// exported as CCSDS CUC (4 octets seconds + 2 octets 2^-16 s). A new SCLK
// "partition" starts at every boot (number supplied by the Linux EGSE, which
// persists it; 0 = unknown). Ground correlates SCLK -> UTC (see timecode.py).
#pragma once
#include <Arduino.h>
#include "../ccsds/tm.h"

namespace vgq {

class Sclk {
 public:
  // Must be called more often than every 71 minutes (micros() wrap) — the main
  // loop calls it every iteration.
  uint64_t now_us() {
    const uint32_t m = micros();
    if (m < last_) high_ += 1ULL << 32;
    last_ = m;
    return high_ | m;
  }
  uint32_t seconds() { return (uint32_t)(now_us() / 1000000ULL); }
  ccsds::CucTime cuc() {
    const uint64_t us = now_us();
    ccsds::CucTime t;
    t.coarse = (uint32_t)(us / 1000000ULL);
    t.fine = (uint16_t)(((us % 1000000ULL) << 16) / 1000000ULL);
    return t;
  }
  uint16_t partition = 0;

 private:
  uint32_t last_ = 0;
  uint64_t high_ = 0;
};

}  // namespace vgq
