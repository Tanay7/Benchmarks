#include "status_display.h"
#include <Arduino_LED_Matrix.h>

namespace vgq {

static Arduino_LED_Matrix g_matrix;

void StatusDisplay::begin() {
  g_matrix.begin();
  g_matrix.setGrayscaleBits(3);          // brightness levels 0..7
  memset(frame_, 0, sizeof(frame_));
  g_matrix.draw(frame_);
}

static void bar(uint8_t* f, int row, uint16_t permille, uint8_t level) {
  const int cols = (int)((permille * 13UL + 999) / 1000);
  for (int c = 0; c < 13; ++c) f[row * 13 + c] = c < cols ? level : 0;
}

void StatusDisplay::update(const ScDisplayData& d) {
  ++tick_;
  const bool blink = (tick_ / 2) & 1;
  uint8_t* f = frame_;
  memset(f, 0, 104);
  for (int c = 0; c < 13; ++c) f[0 * 13 + c] = d.tx_on ? 7 : 0;
  for (int c = 0; c < 13; ++c) f[1 * 13 + c] = d.rx_flash ? 5 : 0;
  for (int c = 0; c < 13; ++c) f[2 * 13 + c] = d.farm_lockout ? (blink ? 7 : 0) : 1;
  bar(f, 3, d.vc0_pm, 4);
  bar(f, 4, d.vc1_pm, 4);
  bar(f, 5, d.ssr_pm, 3);
  for (int c = 0; c < 13; ++c) f[6 * 13 + c] = (d.sensor_health >> c) & 1 ? 3 : 0;
  if (d.mode <= 4) f[7 * 13 + d.mode] = 7;
  if (d.fdir_active && blink)
    for (int c = 8; c < 13; ++c) f[7 * 13 + c] = 7;
  g_matrix.draw(f);
}

}  // namespace vgq
