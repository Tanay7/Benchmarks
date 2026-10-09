// =============================================================================
//  Spacecraft-side status display.
//
//  1) UNO Q 13 x 8 LED matrix ("status lights", always present):
//       row 0  TX  — fully lit while the PA is keyed
//       row 1  RX  — flashes when an uplink packet arrives
//       row 2  COP — FARM state: dim = OPEN, blinking = LOCKOUT
//       row 3  VC0 backlog bar (0..13 columns = 0..100 %)
//       row 4  VC1 backlog bar
//       row 5  SSR fill bar
//       row 6  sensor health: one column per sensor bit 0..12 (lit = healthy)
//       row 7  cols 0-4: mode (BOOT SAFE CRUISE ENCOUNTER TEST), cols 8-12 blink = FDIR active
//  2) Optional second 2.42" SSD1309 OLED (VGQ_SC_OLED=1): two pages of
//     transmitter-side engineering values (see docs/06_Displays.md).
// =============================================================================
#pragma once
#include <Arduino.h>

namespace vgq {

struct ScDisplayData {
  uint8_t mode;
  bool tx_on, rx_flash, farm_lockout, fdir_active;
  uint16_t vc0_pm, vc1_pm, ssr_pm;    // permille
  uint16_t sensor_health;
  // OLED-only values
  uint32_t sclk_s, frames_sent;
  uint16_t partition;
  float tx_duty_pct, pa_temp_c, vradio_v;
  int16_t uplink_rssi, noise_dbm;
  uint8_t air_rate_code, power_code, channel, farm_vr, mcfc;
  uint16_t cmd_acc, cmd_rej, fdir_flags;
  uint32_t frame_period_ms;
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
