#include "status_display.h"
#include "../config.h"
#include <Arduino_LED_Matrix.h>
#if VGQ_SC_OLED
#include <U8g2lib.h>
#endif

namespace vgq {

static Arduino_LED_Matrix g_matrix;
#if VGQ_SC_OLED
static U8G2_SSD1309_128X64_NONAME2_F_4W_HW_SPI g_oled(U8G2_R0, PIN_OLED_CS, PIN_OLED_DC, PIN_OLED_RES);
#endif

void StatusDisplay::begin() {
  g_matrix.begin();
  g_matrix.setGrayscaleBits(3);          // brightness levels 0..7
  memset(frame_, 0, sizeof(frame_));
  g_matrix.draw(frame_);
#if VGQ_SC_OLED
  g_oled.begin();
  g_oled.setContrast(160);
#endif
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

#if VGQ_SC_OLED
  static const char* kModes[] = {"BOOT", "SAFE", "CRUISE", "ENCNTR", "TEST"};
  static const uint16_t kRates[] = {3, 12, 24, 48, 96, 192, 384, 625};   // x100 bps
  char l[32];
  const bool page2 = (tick_ / 20) & 1;   // alternate every ~5 s
  g_oled.clearBuffer();
  g_oled.setFont(u8g2_font_5x7_tf);
  g_oled.drawStr(0, 7, page2 ? "VGQ-1  RF / UPLINK" : "VGQ-1  S/C STATUS");
  g_oled.drawHLine(0, 9, 128);
  if (!page2) {
    snprintf(l, sizeof l, "MODE %-6s SCLK %u/%lu", d.mode <= 4 ? kModes[d.mode] : "?", d.partition,
             (unsigned long)d.sclk_s);
    g_oled.drawStr(0, 18, l);
    snprintf(l, sizeof l, "FRM %lu MCFC %u", (unsigned long)d.frames_sent, d.mcfc);
    g_oled.drawStr(0, 27, l);
    snprintf(l, sizeof l, "PA %.1fC  VRAD %.2fV", d.pa_temp_c, d.vradio_v);
    g_oled.drawStr(0, 36, l);
    snprintf(l, sizeof l, "CMD ACC %u REJ %u", d.cmd_acc, d.cmd_rej);
    g_oled.drawStr(0, 45, l);
    snprintf(l, sizeof l, "FDIR %04X  SNS %04X", d.fdir_flags, d.sensor_health);
    g_oled.drawStr(0, 54, l);
    snprintf(l, sizeof l, "VC0 %u%% VC1 %u%% SSR %u%%", d.vc0_pm / 10, d.vc1_pm / 10, d.ssr_pm / 10);
    g_oled.drawStr(0, 63, l);
  } else {
    snprintf(l, sizeof l, "%s CH%u %.3fMHz", d.tx_on ? "TX ON " : "TX off", d.channel,
             410.125 + d.channel);
    g_oled.drawStr(0, 18, l);
    snprintf(l, sizeof l, "RATE %u.%02uk PWR code %u", kRates[d.air_rate_code & 7] / 100,
             kRates[d.air_rate_code & 7] % 100, d.power_code);
    g_oled.drawStr(0, 27, l);
    snprintf(l, sizeof l, "DUTY %.1f%% PER %lums", d.tx_duty_pct, (unsigned long)d.frame_period_ms);
    g_oled.drawStr(0, 36, l);
    snprintf(l, sizeof l, "UPLINK RSSI %d dBm", d.uplink_rssi);
    g_oled.drawStr(0, 45, l);
    snprintf(l, sizeof l, "NOISE %d dBm", d.noise_dbm);
    g_oled.drawStr(0, 54, l);
    snprintf(l, sizeof l, "FARM %s V(R)=%u", d.farm_lockout ? "LOCKOUT" : "OPEN", d.farm_vr);
    g_oled.drawStr(0, 63, l);
  }
  g_oled.sendBuffer();
#endif
}

}  // namespace vgq
