// =============================================================================
//  Spacecraft-side status lights on the UNO Q LED matrix (layout: status_display.h).
// =============================================================================
#include "status_display.h"
#include <Arduino_LED_Matrix.h>
#include <string.h>

namespace vgq {

namespace {

Arduino_LED_Matrix g_matrix;
const uint8_t kCols = 13;
const uint8_t kRows = 8;
const uint8_t kSensorBits = 10;          // HK sensor_health b0..b9 (tlm_packets.h SensorBit)
const uint8_t kBeaconCol = 5;            // row 7, columns 5..7
const uint8_t kBeaconUrgent = 3;

void bar(uint8_t* f, uint8_t row, uint16_t permille, uint8_t level) {
  const uint32_t pm = permille > 1000u ? 1000u : permille;
  const uint32_t cols = (pm * kCols + 999u) / 1000u;
  for (uint8_t c = 0; c < kCols; ++c) f[row * kCols + c] = c < cols ? level : 0;
}

}  // namespace

void StatusDisplay::begin() {
  (void)g_matrix.begin();
  g_matrix.setGrayscaleBits(3);          // brightness levels 0..7
  memset(frame_, 0, sizeof(frame_));
  g_matrix.draw(frame_);
}

void StatusDisplay::update(const ScDisplayData& d) {
  static_assert(sizeof(frame_) == (size_t)kCols * kRows, "LED matrix is 13 x 8");
  ++tick_;
  const bool blink = (tick_ / 2) & 1;
  uint8_t* f = frame_;
  memset(f, 0, sizeof(frame_));
  for (uint8_t c = 0; c < kCols; ++c) f[0 * kCols + c] = d.tx_on ? 7 : 0;
  for (uint8_t c = 0; c < kCols; ++c) f[1 * kCols + c] = d.rx_flash ? 5 : 0;
  for (uint8_t c = 0; c < kCols; ++c) f[2 * kCols + c] = d.farm_lockout ? (blink ? 7 : 0) : 1;
  bar(f, 3, d.vc0_pm, 4);
  bar(f, 4, d.vc1_pm, 4);
  bar(f, 5, d.ssr_pm, 3);
  for (uint8_t c = 0; c < kSensorBits; ++c) f[6 * kCols + c] = ((d.sensor_health >> c) & 1u) ? 3 : 0;
  if (d.mode <= 4) f[7 * kCols + d.mode] = 7;
  const uint8_t level = d.beacon <= kBeaconUrgent ? d.beacon : kBeaconUrgent;
  if (level < kBeaconUrgent || blink)
    for (uint8_t c = 0; c < level; ++c) f[7 * kCols + kBeaconCol + c] = 7;
  if (d.fdir_active && blink)
    for (uint8_t c = 8; c < kCols; ++c) f[7 * kCols + c] = 7;
  g_matrix.draw(f);
}

}  // namespace vgq
