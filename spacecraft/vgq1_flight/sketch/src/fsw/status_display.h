// =============================================================================
//  Spacecraft-side status lights: the UNO Q's built-in 13 x 8 LED matrix.
//       row 0  TX  - fully lit while the PA is keyed
//       row 1  RX  - flashes when an uplink packet arrives
//       row 2  COP - FARM state: dim = OPEN, blinking = LOCKOUT
//       row 3  VC0 backlog bar (0..13 columns = 0..100 %)
//       row 4  VC1 backlog bar
//       row 5  SSR fill bar
//       row 6  sensor health: one column per bit 0..12 (Nicla ME/Env, AS7265X, RM3100)
//       row 7  cols 0-4: mode (BOOT SAFE CRUISE ENCOUNTER TEST), cols 8-12 blink = FDIR active
//  Detailed transmitter-side numbers go to the Linux EGSE console (sc_status).
// =============================================================================
#pragma once
#include <Arduino.h>

namespace vgq {

struct ScDisplayData {
  uint8_t mode;
  bool tx_on, rx_flash, farm_lockout, fdir_active;
  uint16_t vc0_pm, vc1_pm, ssr_pm;    // permille
  uint16_t sensor_health;
};

class StatusDisplay {
 public:
  void begin();
  void update(const ScDisplayData& d);   // call ~4 Hz
 private:
  uint8_t frame_[104];
  uint32_t tick_ = 0;
};

}  // namespace vgq
