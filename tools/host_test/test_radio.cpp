// =============================================================================
//  Radio abstraction (src/drivers/radio.*): E22 tables and register images, the
//  E22 UART framing and AUX tracking moved out of the v2 flight executive, the
//  SX126x LoRa profile table, Semtech time on air, FERR_L limits, TX timeouts
//  and the model-generic vgq::Radio figures; at the end of the file, the E22
//  model strap of contract 33 (one binary for the T33D and the T37S).
//
//  Reference values are derived by hand in the comments (contract v3 sections 2
//  and 14; Semtech DS SX1261/2 Table 3-8). Writes <out>/radio_tables.csv, one row
//  per (model, code), for the ground's LoRa maths (dssq/link.py) to cross-check.
// =============================================================================
#include <cmath>
#include <cstdio>
#include <cstring>
#include <string>

#include "config.h"          // model strap pin plan (contract 33); pure C++, no Arduino types
#include "drivers/radio.h"
#include "fsw/datalink.h"     // frame_period_ms(): the duty floor fed by the measured busy time
#include "host_test.h"

using namespace vgq;

namespace {

const size_t kCadu = 236;   // ASM 4 + RS(232,200) codeblock: the frame every radio carries

// Minimal concrete radio to exercise the non-virtual vgq::Radio figures.
class FakeRadio : public Radio {
 public:
  FakeRadio(uint8_t model, int8_t gain) : Radio(model, gain) {}
  const char* name() const override { return "fake"; }
  bool begin() override { return true; }
  bool configure(const RadioSettings& s, bool) override {
    settings_ = s;
    return true;
  }
  bool transmit(const uint8_t*, size_t, uint32_t) override { return false; }
  bool busy(uint32_t) const override { return false; }
  RadioEvent poll(uint32_t, RxPacket&, int32_t&) override { return RadioEvent::NONE; }
  bool request_noise(uint32_t) override { return false; }
  bool channel_busy() const override { return false; }
  bool set_hibernation(bool, uint32_t) override { return true; }
  void set_rx_boost_ok(bool on) { rx_boost_ok_ = on; }
};

void feed(E22StreamParser& p, const uint8_t* b, size_t n, uint32_t t0) {
  for (size_t i = 0; i < n; ++i) p.on_byte(b[i], t0 + (uint32_t)i);
}

}  // namespace

// ---- E22 tables ------------------------------------------------------------------------
HOST_TEST(radio_e22_tables) {
  const uint32_t bps[8] = {2400, 2400, 2400, 4800, 9600, 19200, 38400, 62500};
  bool ok = true;
  for (uint8_t c = 0; c < 8; ++c) ok = ok && e22_air_rate_bps(c) == bps[c];
  CHECK(ok && e22_air_rate_bps(8) == 0, "E22 air-rate codes 0..7 (0,1,2 = 2.4k), code 8 invalid");

  CHECK(e22_tx_power_dbm(VGQ_RADIO_E22_T33D, 0) == 33 && e22_tx_power_dbm(VGQ_RADIO_E22_T33D, 1) == 30 &&
            e22_tx_power_dbm(VGQ_RADIO_E22_T33D, 2) == 27 && e22_tx_power_dbm(VGQ_RADIO_E22_T33D, 3) == 24,
        "E22-400T33D power codes 33/30/27/24 dBm (contract 2.1)");
  ok = true;
  for (uint8_t c = 0; c < 4; ++c) ok = ok && e22_tx_power_dbm(VGQ_RADIO_E22_T37S, c) == 37;
  CHECK(ok, "E22-400T37S: every power code is 37 dBm");
  CHECK(e22_tx_power_dbm(VGQ_RADIO_E22_T33D, 4) == kRadioNaPower &&
            e22_tx_power_dbm(VGQ_RADIO_SX126X, 0) == kRadioNaPower,
        "E22 power: invalid code / non-E22 model rejected");

  // S(rate) = S(2.4k) + 10 log10(rate / 2400), rounded to 0.1 dB:
  //   x2 +3.0103 -> T33D -124.99 = -125.0   x4 +6.0206 -> -121.98 = -122.0
  //   x8 +9.0309 -> -118.97 = -119.0        x16 +12.0412 -> -115.96 = -116.0
  //   62500/2400 = 26.042: +14.1567 -> -113.84 = -113.8;  T37S the same, 2 dB higher.
  const int16_t t33d[8] = {-1280, -1280, -1280, -1250, -1220, -1190, -1160, -1138};
  const int16_t t37s[8] = {-1260, -1260, -1260, -1230, -1200, -1170, -1140, -1118};
  bool src_ok = true;
  ok = true;
  for (uint8_t c = 0; c < 8; ++c) {
    SensSource s1 = SensSource::COMPUTED, s2 = SensSource::COMPUTED;
    ok = ok && e22_sensitivity_ddbm(VGQ_RADIO_E22_T33D, c, &s1) == t33d[c] &&
         e22_sensitivity_ddbm(VGQ_RADIO_E22_T37S, c, &s2) == t37s[c];
    const SensSource want = c <= 2 ? SensSource::DATASHEET : SensSource::ESTIMATED;
    src_ok = src_ok && s1 == want && s2 == want;
  }
  CHECK(ok, "E22 sensitivities per air rate (T33D -128, T37S -126 dBm at 2.4k + estimates)");
  CHECK(src_ok, "E22 sensitivity sources: datasheet at 2.4k, estimated above");
  CHECK(std::string(sens_source_name(SensSource::DATASHEET)) == "datasheet" &&
            std::string(sens_source_name(SensSource::ESTIMATED)) == "estimated" &&
            std::string(sens_source_name(SensSource::COMPUTED)) == "computed",
        "sensitivity source names");
}

HOST_TEST(radio_e22_airtime) {
  // v2 flight estimate: n*10*1000/9600 + n*8*1300/bps, integer terms (n = 236):
  //   UART 2360000/9600 = 245;  2.4k: 2454400/2400 = 1022 -> 1267 ms
  //   4.8k 511 -> 756;  9.6k 255 -> 500;  19.2k 127 -> 372;  38.4k 63 -> 308;  62.5k 39 -> 284
  const uint32_t want[8] = {1267, 1267, 1267, 756, 500, 372, 308, 284};
  bool ok = true, same = true;
  for (uint8_t c = 0; c < 8; ++c) {
    ok = ok && e22_airtime_ms(c, kCadu) == want[c];
    const uint32_t bps = e22_air_rate_bps(c);
    const uint32_t v2 = (uint32_t)(kCadu * 10UL * 1000UL / 9600UL) + (uint32_t)(kCadu * 8UL * 1300UL / bps);
    same = same && e22_airtime_ms(c, kCadu) == v2 && radio_airtime_ms(VGQ_RADIO_E22_T33D, c, kCadu) == v2;
  }
  CHECK(ok, "E22 busy-time estimate for a 236-octet CADU per air rate");
  CHECK(same, "E22 estimate identical to the v2 Flight::frame_period_ms() formula");
  CHECK(e22_airtime_ms(9, kCadu) == 0, "E22 air time: invalid code -> 0");
}

HOST_TEST(radio_e22_registers) {
  RadioSettings s;   // defaults: CH23, code 2, power 0, no LBT, WOR cycle 3
  uint8_t r[9];
  e22_build_regs(VGQ_RADIO_E22_T33D, s, r);
  // Factory default C0 00 00 62 00 17 (T33D 7.3): ADDH 00 ADDL 00 NETID 00 REG0 62 ... REG2 17.
  // REG1 = noise RSSI (0x20), no abnormal log, power 0; REG3 = RSSI byte (0x80) | WOR 3.
  const uint8_t want33[9] = {0x00, 0x00, 0x00, 0x62, 0x20, 0x17, 0x83, 0x00, 0x00};
  CHECK(memcmp(r, want33, 9) == 0, "T33D register image 00 00 00 62 20 17 83 00 00");
  CHECK((r[4] & 0x04) == 0, "T33D: REG1 bit 2 (reserved) never set");
  e22_build_regs(VGQ_RADIO_E22_T37S, s, r);
  CHECK(r[4] == 0x24, "T37S: REG1 = noise RSSI | abnormal-status log (0x24)");
  s.power_code = 2;
  s.lbt = true;
  s.channel = 5;
  s.rate_code = 7;
  s.address = 0x1234;
  s.netid = 0x56;
  e22_build_regs(VGQ_RADIO_E22_T33D, s, r);
  const uint8_t want2[9] = {0x12, 0x34, 0x56, 0x67, 0x22, 0x05, 0x93, 0x00, 0x00};
  CHECK(memcmp(r, want2, 9) == 0, "T33D register image with LBT, power code 2, 62.5k, CH5, address");

  const uint8_t w[7] = {0x00, 0x00, 0x00, 0x62, 0x20, 0x17, 0x83};
  uint8_t rb[7];
  memcpy(rb, w, 7);
  rb[4] = 0x3C;   // reserved bits 4..2 set by the module
  CHECK(e22_regs_match(VGQ_RADIO_E22_T33D, w, rb), "T33D read-back ignores reserved REG1 bits 4..2");
  CHECK(!e22_regs_match(VGQ_RADIO_E22_T37S, w, rb), "T37S read-back compares REG1 exactly");
  memcpy(rb, w, 7);
  rb[5] = 0x18;
  CHECK(!e22_regs_match(VGQ_RADIO_E22_T33D, w, rb), "read-back detects a channel mismatch");

  RadioSettings bad;
  CHECK(radio_settings_valid(bad), "default settings valid");
  bad.channel = 84;
  CHECK(!radio_settings_valid(bad), "channel 84 rejected");
  bad = RadioSettings();
  bad.rate_code = 8;
  CHECK(!radio_settings_valid(bad), "rate code 8 rejected");
  bad = RadioSettings();
  bad.power_code = 4;
  CHECK(!radio_settings_valid(bad), "power code 4 rejected");
  bad = RadioSettings();
  bad.wor_cycle = 8;
  CHECK(!radio_settings_valid(bad), "WOR cycle 8 rejected");
  CHECK(radio_channel_freq_hz(23) == 433125000u && radio_channel_freq_hz(0) == 410125000u &&
            radio_channel_freq_hz(83) == 493125000u && radio_channel_freq_hz(84) == 0,
        "channel plan 410.125 + CH MHz, CH 0..83");
  CHECK(std::fabs(radio_channel_freq_mhz(23) - 433.125f) < 1e-4f, "CH23 = 433.125 MHz");
}

// ---- E22 UART framing (moved from the v2 flight executive) --------------------------------
HOST_TEST(radio_e22_stream) {
  RxPacket rx;
  int32_t v = 0;
  E22StreamParser p(VGQ_RADIO_E22_T33D);
  const uint8_t pkt[5] = {0xEB, 0x90, 0x01, 0x02, 0xC8};   // CLTU start + 2 octets + RSSI 0xC8
  feed(p, pkt, 5, 1000);                                  // last octet at t = 1004
  CHECK(p.receiving(1010) && p.poll(1044, rx, v) == RadioEvent::NONE, "burst not ended within 40 ms");
  const RadioEvent ev = p.poll(1045, rx, v);
  CHECK(ev == RadioEvent::PACKET && rx.len == 4 && memcmp(rx.data, pkt, 4) == 0 && rx.t_ms == 1004,
        "burst ends after > 40 ms of silence: packet = all but the RSSI octet");
  CHECK(rx.rssi_dbm == -56 && rx.snr_cdb == kRadioNaI16 && rx.freq_err_hz == kRadioNaI32,
        "RSSI octet 0xC8 -> -(256 - 200) = -56 dBm; SNR / frequency error n/a");
  CHECK(p.receiving(1103) && !p.receiving(1104), "transmit hold-off: 100 ms after the last octet");
  CHECK(p.rx_bytes() == 5, "every UART octet counted");

  // Noise reply C1 00 02 <noise 0xA0> <last 0xB0> -> -(256 - 160) = -96 dBm.
  p.noise_requested(2000);
  CHECK(p.noise_pending(), "noise query pending");
  const uint8_t noise[5] = {0xC1, 0x00, 0x02, 0xA0, 0xB0};
  feed(p, noise, 5, 2010);
  CHECK(p.poll(2055, rx, v) == RadioEvent::NOISE && v == -96 && !p.noise_pending(), "noise reply -> -96 dBm");
  p.noise_requested(3000);
  CHECK(p.poll(3300, rx, v) == RadioEvent::NONE, "noise query still pending at 300 ms");
  CHECK(p.poll(3301, rx, v) == RadioEvent::NOISE && v == kRadioNaI32, "noise query unanswered -> n/a");
  // Without a pending query the same octets are an (undecodable) uplink burst, as in v2.
  feed(p, noise, 5, 4000);
  CHECK(p.poll(4100, rx, v) == RadioEvent::PACKET && rx.len == 4 && rx.rssi_dbm == -80,
        "noise-reply pattern without a query is passed on as a packet");

  // "Wrong format" reply FF FF FF: never delivered; ends a pending noise query as n/a.
  const uint8_t ff3[3] = {0xFF, 0xFF, 0xFF};
  feed(p, ff3, 3, 5000);
  CHECK(p.poll(5100, rx, v) == RadioEvent::NONE && p.command_rejects() == 1, "FF FF FF counted, not delivered");
  p.noise_requested(6000);
  feed(p, ff3, 3, 6001);
  CHECK(p.poll(6100, rx, v) == RadioEvent::NOISE && v == kRadioNaI32 && p.command_rejects() == 2,
        "FF FF FF answers a pending noise query with n/a");

  // Abnormal-status log: decoded for the T37S only.
  const uint8_t fault[4] = {0xFF, 0xFF, 0xFF, 0x03};
  E22StreamParser p37(VGQ_RADIO_E22_T37S);
  feed(p37, fault, 4, 100);
  CHECK(p37.poll(200, rx, v) == RadioEvent::FAULT && v == kRadioFaultOverTemp, "T37S FF FF FF 03 -> FAULT 3");
  const uint8_t fault5[5] = {0xFF, 0xFF, 0xFF, 0x01, 0x00};
  feed(p37, fault5, 5, 300);
  CHECK(p37.poll(400, rx, v) == RadioEvent::FAULT && v == kRadioFaultUnderVoltage, "T37S 5-octet report -> FAULT 1");
  feed(p, fault, 4, 7000);
  CHECK(p.poll(7100, rx, v) == RadioEvent::PACKET && rx.len == 3, "T33D: FF FF FF 03 is not a status report");

  // Over-long burst: 310 octets -> buffer keeps 300, packet truncated to 256.
  E22StreamParser pl(VGQ_RADIO_E22_T33D);
  for (uint32_t i = 0; i < 310; ++i) pl.on_byte((uint8_t)i, 8000);
  CHECK(pl.poll(8041, rx, v) == RadioEvent::PACKET && rx.len == RxPacket::kMaxLen && rx.data[255] == 255 &&
            rx.rssi_dbm == (int16_t)(299 % 256) - 256 && pl.rx_bytes() == 310,
        "310-octet burst: 300 buffered, 256 delivered, RSSI from octet 300, 310 counted");
}

HOST_TEST(radio_e22_tx_tracker) {
  E22TxTracker t;
  int32_t v = 0;
  t.start(0);
  CHECK(t.active() && t.poll(100, true, v) == RadioEvent::NONE, "AUX still high at 100 ms: waiting for the edge");
  CHECK(t.poll(150, false, v) == RadioEvent::NONE && t.poll(1000, false, v) == RadioEvent::NONE,
        "AUX low: transmission in progress");
  CHECK(t.poll(1267, true, v) == RadioEvent::TX_DONE && v == 1267 && !t.active(), "AUX high again -> TX_DONE 1267 ms");
  t.start(0);
  CHECK(t.poll(201, true, v) == RadioEvent::NONE && t.poll(202, true, v) == RadioEvent::TX_DONE && v == 202,
        "missed AUX edge: assumed after 200 ms");
  t.start(0);
  CHECK(t.poll(10, false, v) == RadioEvent::NONE && t.poll(30000, false, v) == RadioEvent::NONE,
        "AUX low up to the 30 s limit");
  CHECK(t.poll(30001, false, v) == RadioEvent::TX_TIMEOUT && v == 30001 && !t.active(), "AUX timeout -> TX_TIMEOUT");
  CHECK(t.poll(40000, true, v) == RadioEvent::NONE, "idle tracker reports nothing");

  // C24 (manual 5.6.2): AUX may rise once the UART data reached the RF chip while the
  // frame is still on air. With the model's busy time as the floor, an early AUX rise
  // (here at the 246 ms UART time of a 236-octet CADU) is not the end of the frame.
  const uint32_t model = radio_airtime_ms(VGQ_RADIO_E22_T33D, 2, 236);
  CHECK(model > 700, "E22 model busy time of one CADU exceeds the UART time");
  t.start(0, model);
  CHECK(t.poll(10, false, v) == RadioEvent::NONE && t.poll(246, true, v) == RadioEvent::NONE &&
            t.poll(model - 1, true, v) == RadioEvent::NONE,
        "early AUX rise: no TX_DONE before the modelled air time");
  CHECK(t.poll(model, true, v) == RadioEvent::TX_DONE && v == (int32_t)model && !t.active(),
        "TX_DONE at the modelled air time, busy = model");
  t.start(0, model);
  CHECK(t.poll(10, false, v) == RadioEvent::NONE && t.poll(model + 300, true, v) == RadioEvent::TX_DONE &&
            v == (int32_t)(model + 300),
        "AUX later than the model: measured time kept");
  // The 50 % duty floor (kMaxTxDutyPermille) then sees at least the air time.
  CHECK(frame_period_ms(model, MODE_SAFE, false, 0) >= 2 * model,
        "frame period with the floored busy time keeps the PA under 50 % duty");
}

// ---- SX126x LoRa profiles and maths ---------------------------------------------------------
HOST_TEST(radio_sx126x_profiles) {
  const uint8_t sf[8] = {12, 12, 12, 11, 10, 9, 8, 7};
  const uint32_t bw[8] = {31250, 62500, 125000, 125000, 125000, 125000, 125000, 125000};
  // S = -174 + 10 log10(BW) + 6 + SNRmin:  10 log10 31250 = 44.9485, 62500 = 47.9588,
  // 125000 = 50.9691; SNRmin SF12 -20, SF11 -17.5, SF10 -15, SF9 -12.5, SF8 -10, SF7 -7.5:
  //   0: -143.05 -> -143.1   1: -140.04 -> -140.0   2: -137.03 (Table 3-8: -137)
  //   3: -134.53 -> -134.5   4: -132.03 -> -132.0   5: -129.53 -> -129.5
  //   6: -127.03 -> -127.0   7: -124.53 (Table 3-8: -124)
  const int16_t sens[8] = {-1431, -1400, -1370, -1345, -1320, -1295, -1270, -1240};
  const bool ds[8] = {false, false, true, false, false, false, false, true};
  bool table_ok = true, computed_ok = true, ds_ok = true;
  for (uint8_t c = 0; c < 8; ++c) {
    const LoraProfile* p = sx_profile(c);
    table_ok = table_ok && p != nullptr && p->sf == sf[c] && p->bw_hz == bw[c] && p->cr == 5 &&
               p->sens_ddbm == sens[c] && (p->src == SensSource::DATASHEET) == ds[c];
    const float s = lora_sensitivity_dbm(p->sf, p->bw_hz, kSxNoiseFigureDb);
    if (!ds[c]) computed_ok = computed_ok && (int16_t)std::lround(s * 10.0f) == p->sens_ddbm;
    else ds_ok = ds_ok && std::fabs(s * 10.0f - (float)p->sens_ddbm) < 6.0f;   // model within 0.6 dB
  }
  CHECK(table_ok && sx_profile(8) == nullptr, "SX126x profile table (SF, BW, CR 4/5, sensitivity, source)");
  CHECK(computed_ok, "computed sensitivities = -174 + 10log10(BW) + 6 + SNRmin, rounded to 0.1 dB");
  CHECK(ds_ok, "Table 3-8 profiles: computed model agrees within 0.6 dB (-137.03 / -124.53)");
  CHECK(std::isnan(lora_sensitivity_dbm(6, 125000, 6.0f)), "sensitivity model: SF6 -> NAN");

  SensSource src = SensSource::COMPUTED;
  CHECK(sx_sensitivity_ddbm(2, false, &src) == -1330 && src == SensSource::DATASHEET,
        "Rx power-saving gain, BW125 SF12: -133 dBm (Table 3-8)");
  // Elsewhere NF 6 + 4 dB: profile 1 -136.04 -> -136.0; profile 7 (SF7/125k) -120.53 -> -120.5.
  CHECK(sx_sensitivity_ddbm(1, false, &src) == -1360 && src == SensSource::ESTIMATED &&
            sx_sensitivity_ddbm(7, false, &src) == -1205 && src == SensSource::ESTIMATED,
        "Rx power-saving gain elsewhere: model with NF + 4 dB, estimated");
  CHECK(sx_sensitivity_ddbm(1, true, &src) == -1400 && src == SensSource::COMPUTED,
        "profile 1 boosted: -140.0 computed");
  CHECK(sx_sensitivity_ddbm(8, true, &src) == 0, "invalid profile -> 0");

  // Symbol time 2^SF / BW: 131072, 65536, 32768, 16384 us (>= 16380: LDRO on), then 8192 ...
  const bool ldro[8] = {true, true, true, true, false, false, false, false};
  bool ldro_ok = true;
  for (uint8_t c = 0; c < 8; ++c) ldro_ok = ldro_ok && lora_ldro_required(sf[c], bw[c]) == ldro[c];
  CHECK(ldro_ok && lora_symbol_us(12, 31250) == 131072u && lora_symbol_us(11, 125000) == 16384u,
        "LDRO when the symbol time >= 16.38 ms (SF12 all, SF11/125k)");
}

HOST_TEST(radio_sx126x_airtime) {
  // Semtech formula, 236-octet CADU, CRC on, explicit header, 12-symbol preamble, CR 4/5:
  //   num = 8*236 - 4 SF + 28 + 16 = 1932 - 4 SF;  den = 4 (SF - 2 DE)
  //   0: SF12/31.25k DE=1: 1884/40 = 47.1 -> 48; n = 8 + 48*5 = 248;
  //      T = (12 + 4.25 + 248) * 131072 us = 264.25 * 131072 = 34 635 776 us
  //   1: SF12/62.5k  same symbols * 65536 = 17 317 888 us
  //   2: SF12/125k   * 32768 = 8 658 944 us
  //   3: SF11/125k DE=1: 1888/36 = 52.4 -> 53; n = 273; 289.25 * 16384 = 4 739 072 us
  //   4: SF10 DE=0: 1892/40 = 47.3 -> 48; n = 248; 264.25 * 8192 = 2 164 736 us
  //   5: SF9: 1896/36 = 52.7 -> 53; n = 273; 289.25 * 4096 = 1 184 768 us
  //   6: SF8: 1900/32 = 59.4 -> 60; n = 308; 324.25 * 2048 = 664 064 us
  //   7: SF7: 1904/28 = 68 -> 68; n = 348; 364.25 * 1024 = 372 992 us
  const uint32_t want_us[8] = {34635776u, 17317888u, 8658944u, 4739072u, 2164736u, 1184768u, 664064u, 372992u};
  const uint32_t want_ms[8] = {34636u, 17318u, 8659u, 4740u, 2165u, 1185u, 665u, 373u};
  bool us_ok = true, ms_ok = true;
  for (uint8_t c = 0; c < 8; ++c) {
    const LoraProfile* p = sx_profile(c);
    us_ok = us_ok && lora_airtime_us(p->sf, p->bw_hz, p->cr, lora_ldro_required(p->sf, p->bw_hz),
                                     kSxPreambleSym, kCadu, true, false) == want_us[c];
    ms_ok = ms_ok && radio_airtime_ms(VGQ_RADIO_SX126X, c, kCadu) == want_ms[c];
  }
  CHECK(us_ok, "LoRa time on air of a 236-octet CADU, all 8 profiles (hand-derived)");
  CHECK(ms_ok, "radio_airtime_ms(SX126x) = air time rounded up to ms");
  // Small payload, implicit header, no CRC: SF7/125k, PL 10: num = 80-28+28 = 80 - 20 = 60;
  // ceil(60/28) = 3; n = 8 + 15 = 23; T = (8 + 4.25 + 23) * 1024 = 36 096 us.
  CHECK(lora_airtime_us(7, 125000, 5, false, 8, 10, false, true) == 36096u,
        "implicit header, no CRC, 8-symbol preamble");
  CHECK(lora_airtime_us(6, 125000, 5, false, 8, 10, true, false) == 0 &&
            lora_airtime_us(7, 0, 5, false, 8, 10, true, false) == 0 &&
            lora_airtime_us(7, 125000, 4, false, 8, 10, true, false) == 0 &&
            lora_airtime_us(7, 125000, 5, false, 8, 256, true, false) == 0,
        "air time rejects SF < 7, BW 0, CR < 5, payload > 255");

  // Bit rate SF (4/5) BW / 2^SF: 12 * 0.8 * 62500 / 4096 = 146.48;  7 * 0.8 * 125000 / 128 = 5468.75
  CHECK(radio_bitrate_bps(VGQ_RADIO_SX126X, 1) == 146u && radio_bitrate_bps(VGQ_RADIO_SX126X, 7) == 5469u &&
            radio_bitrate_bps(VGQ_RADIO_SX126X, 0) == 73u && radio_bitrate_bps(VGQ_RADIO_E22_T33D, 2) == 2400u,
        "nominal bit rates (SX126x 146 / 5469 / 73 bit/s, E22 2400)");

  // SetTx timeout = 1.5 x air + 50 ms in 15.625 us steps: (25 976 832 + 50 000) * 64 / 1000
  // = 1 665 717.248 -> 1 665 718.
  CHECK(sx_tx_timeout_raw(17317888u, kSxTxTimeoutMarginUs) == 1665718u,
        "TX timeout, profile 1 CADU: 1665718 steps (26.03 s)");
  CHECK(sx_tx_timeout_raw(UINT32_MAX, 0) == 0xFFFFFEu && sx_tx_timeout_raw(0, 0) == 1u,
        "TX timeout clamped to 1..0xFFFFFE");
}

// ---- SX126x RxDutyCycle periods for hibernation (C58: was untested inside sx126x.cpp) -------
HOST_TEST(radio_sx126x_duty_cycle) {
  bool all = true, catch_ok = true;
  for (uint8_t code = 0; code < 8; ++code) {
    const LoraProfile* p = sx_profile(code);
    uint32_t rx = 0, sl = 0;
    const bool ok = p && sx_duty_cycle_us(p->sf, p->bw_hz, kSxWakePreambleSym, rx, sl);
    if (!ok) { all = false; continue; }
    const uint64_t ts = lora_symbol_us(p->sf, p->bw_hz);
    // sleep = (n_pre - 16) Tsym; rx >= 9 Tsym; the restarted timer 2 rx + programmed sleep
    // covers preamble + header, (n_pre + 12.25) Tsym (DS 13.1.7, 6.1.4).
    const uint64_t need = ((4u * (uint64_t)kSxWakePreambleSym + 49u) * ts + 3u) / 4u;
    catch_ok = catch_ok && sl == (kSxWakePreambleSym - 16u) * ts && rx >= 9u * ts &&
               2u * (uint64_t)rx + sl - kSxDutyTransitionUs >= need;
  }
  CHECK(all && catch_ok, "SX126x duty cycle: every profile, 32-symbol WAKE preamble: sleep (n-16) Tsym, rx >= 9 Tsym, "
                         "2 rx + sleep covers preamble + header");
  uint32_t rx = 0, sl = 0;
  CHECK(!sx_duty_cycle_us(7, 125000, 16, rx, sl) && !sx_duty_cycle_us(12, 31250, 16, rx, sl),
        "SX126x duty cycle: n_pre <= 16 refused (no detection margin)");
  // SF7 / 125 kHz: Tsym 1024 us; n_pre 17 -> sleep 1024 us <= 6 ms transition: refused.
  CHECK(!sx_duty_cycle_us(7, 125000, 17, rx, sl), "SX126x duty cycle: sleep shorter than the wake-up transition refused");
  // 24-bit period field (15.625 us steps, max 262.14 s): SF12 / 31.25 kHz (Tsym 131.072 ms)
  // with 2000 symbols of sleep (262.144 s) exceeds it by 16 us; 1999 symbols do not.
  CHECK(!sx_duty_cycle_us(12, 31250, 2016, rx, sl) && sx_duty_cycle_us(12, 31250, 2015, rx, sl) &&
            sl == 1999u * 131072u,
        "SX126x duty cycle: periods beyond the 24-bit field refused, just below accepted");
  CHECK(!sx_duty_cycle_us(4, 125000, 32, rx, sl) && !sx_duty_cycle_us(12, 0, 32, rx, sl),
        "SX126x duty cycle: invalid SF / bandwidth refused");
}

HOST_TEST(radio_sx126x_limits) {
  // FERR_L at 433.125 MHz: 25 % BW vs 50 ppm (SF12) = 21 656.25 Hz, 100 ppm (SF11), 200 ppm (SF10):
  //   0: min(7812.5, 21656.25) = 7812.5 (18 ppm)   1: 15625   2: 21656.25
  //   3: min(31250, 43312.5) = 31250   4: min(31250, 86625) = 31250   5..7: 31250 (25 % BW only)
  const float want[8] = {7812.5f, 15625.0f, 21656.25f, 31250.0f, 31250.0f, 31250.0f, 31250.0f, 31250.0f};
  bool ok = true;
  for (uint8_t c = 0; c < 8; ++c) {
    const LoraProfile* p = sx_profile(c);
    ok = ok && std::fabs(lora_max_freq_offset_hz(p->sf, p->bw_hz, 433125000u) - want[c]) < 0.5f;
  }
  CHECK(ok, "maximum tolerated frequency offset (FERR_L) per profile at 433.125 MHz");
  CHECK(std::fabs(lora_max_freq_offset_hz(12, 31250, 433125000u) / 433.125f - 18.04f) < 0.01f,
        "SF12/31.25 kHz limit = 18 ppm at 433 MHz (TCXO needed, contract 14)");

  CHECK(sx_drive_dbm(0) == 22 && sx_drive_dbm(1) == 17 && sx_drive_dbm(2) == 14 && sx_drive_dbm(3) == 10 &&
            sx_drive_dbm(4) == kRadioNaPower,
        "SX126x drive steps 22/17/14/10 dBm");
  CHECK(sx_tx_power_dbm(0, 11) == 33 && sx_tx_power_dbm(1, 11) == 28 && sx_tx_power_dbm(3, 11) == 21,
        "radiated = drive + 11 dB PA gain (E22-400M33S)");
  CHECK(sx_tx_power_dbm(0, 14) == kSxModuleMaxDbm && sx_tx_power_dbm(4, 11) == kRadioNaPower,
        "radiated power capped at the 33 dBm module maximum; invalid code rejected");
}

HOST_TEST(radio_brownout_derating) {
  // Contract 15: kBrownoutPowerCode default 2 = 27 dBm on the T33D (VERIFY T-RF-07).
  CHECK(radio_boot_power_code(false, 0, 2) == 0, "normal reset: nominal power code");
  CHECK(radio_boot_power_code(true, 0, 2) == 2 &&
            radio_tx_power_dbm(VGQ_RADIO_E22_T33D, radio_boot_power_code(true, 0, 2), 0) == 27,
        "brownout reset: T33D derated from 33 to 27 dBm (code 2)");
  CHECK(radio_boot_power_code(true, 3, 2) == 3, "brownout never raises power above a lower nominal setting");
  CHECK(radio_boot_power_code(true, 9, 7) == 3 && radio_boot_power_code(false, 9, 2) == 3,
        "out-of-range codes clamped to the lowest power (3)");
  CHECK(radio_tx_power_dbm(VGQ_RADIO_E22_T37S, radio_boot_power_code(true, 0, 2), 0) == 37,
        "E22-400T37S: no power levels, derating has no effect");
  CHECK(radio_tx_power_dbm(VGQ_RADIO_SX126X, radio_boot_power_code(true, 0, 2), 11) == 25,
        "SX126x: code 2 = 14 dBm drive + 11 dB = 25 dBm");
}

// ---- vgq::Radio figures + the shared table for the ground -----------------------------------
HOST_TEST(radio_interface_figures) {
  FakeRadio sx(VGQ_RADIO_SX126X, kSxPaGainDbDefault);
  RadioSettings s;
  s.rate_code = kSxDefaultProfile;
  s.lbt = true;
  (void)sx.configure(s, false);
  CHECK(sx.airtime_ms(kCadu) == 17318u && sx.bitrate_bps() == 146u && sx.tx_power_dbm() == 33,
        "SX126x default profile: 17.318 s per CADU, 146 bit/s, 33 dBm");
  CHECK(std::fabs(sx.sensitivity_dbm() + 136.0f) < 1e-4f && sx.sensitivity_source() == SensSource::ESTIMATED,
        "Rx boosted gain not verified -> power-saving sensitivity -136.0 dBm (estimated)");
  sx.set_rx_boost_ok(true);
  CHECK(std::fabs(sx.sensitivity_dbm() + 140.0f) < 1e-4f && sx.sensitivity_source() == SensSource::COMPUTED,
        "Rx boosted gain verified -> -140.0 dBm (computed)");
  CHECK(sx.rf_flags() == 3 && std::fabs(sx.max_freq_offset_hz() - 15625.0f) < 0.5f,
        "RF flags b0 boost | b1 LBT; FERR_L 15.6 kHz");

  FakeRadio e22(VGQ_RADIO_E22_T33D, 0);
  (void)e22.configure(RadioSettings(), false);
  CHECK(e22.airtime_ms(kCadu) == 1267u && e22.tx_power_dbm() == 33 &&
            std::fabs(e22.sensitivity_dbm() + 128.0f) < 1e-4f && e22.sensitivity_source() == SensSource::DATASHEET &&
            e22.max_freq_offset_hz() == 0.0f && e22.rf_flags() == 0,
        "E22-400T33D defaults: 1267 ms per CADU, 33 dBm, -128 dBm datasheet");
  CHECK(std::string(radio_model_name(VGQ_RADIO_E22_T33D)) == "E22-400T33D" &&
            std::string(radio_model_name(VGQ_RADIO_E22_T37S)) == "E22-400T37S" &&
            std::string(radio_model_name(VGQ_RADIO_SX126X)) == "SX126x",
        "model names");

  // Shared table, one row per (model, rate code): see the header line below.
  const std::string path = out + "/radio_tables.csv";
  FILE* f = std::fopen(path.c_str(), "w");
  CHECK(f != nullptr, "write radio_tables.csv");
  if (f == nullptr) return;
  std::fprintf(f, "model,code,bitrate_bps,airtime_ms_236,airtime_us_236,sens_ddbm,sens_source,"
                  "sens_saving_ddbm,sens_saving_source,ferr_limit_hz_ch23,power_code_dbm\n");
  const uint8_t models[3] = {VGQ_RADIO_E22_T33D, VGQ_RADIO_E22_T37S, VGQ_RADIO_SX126X};
  for (uint8_t m : models) {
    for (uint8_t c = 0; c < 8; ++c) {
      SensSource s1 = SensSource::COMPUTED, s2 = SensSource::COMPUTED;
      const int16_t sb = radio_sensitivity_ddbm(m, c, true, &s1);
      const int16_t sp = radio_sensitivity_ddbm(m, c, false, &s2);
      const LoraProfile* p = sx_profile(c);
      const bool lora = m == VGQ_RADIO_SX126X;
      const uint32_t us = lora ? lora_airtime_us(p->sf, p->bw_hz, p->cr, lora_ldro_required(p->sf, p->bw_hz),
                                                 kSxPreambleSym, kCadu, true, false)
                               : 0u;
      const float ferr = lora ? lora_max_freq_offset_hz(p->sf, p->bw_hz, radio_channel_freq_hz(23)) : 0.0f;
      // power_code_dbm: radiated power of POWER code c (codes 0..3 only; SX126x with the default PA gain)
      char pwr[8] = "";
      if (c <= kRadioMaxPowerCode) {
        std::snprintf(pwr, sizeof pwr, "%d", (int)radio_tx_power_dbm(m, c, kSxPaGainDbDefault));
      }
      std::fprintf(f, "%s,%u,%u,%u,%u,%d,%s,%d,%s,%.2f,%s\n", radio_model_name(m), (unsigned)c,
                   (unsigned)radio_bitrate_bps(m, c), (unsigned)radio_airtime_ms(m, c, kCadu), (unsigned)us, sb,
                   sens_source_name(s1), sp, sens_source_name(s2), ferr, pwr);
    }
  }
  std::fclose(f);
}

// =============================================================================
//  E22 model selection without a source edit (contract 33, Phase B WS6b): the
//  boot strap -> model mapping (e22_strap_model), the model-dependent behaviour
//  that follows the model chosen at run time (REG1 image, power-code meaning,
//  abnormal-status decoding, sensitivity) and the strap pin's place in the UNO Q
//  pin plan of config.h.
// =============================================================================
HOST_TEST(radio_model_strap_mapping) {
  const uint8_t n = kStrapSamples;
  CHECK(n == 5 && n % 2 == 1, "strap: an odd number of reads (5), so a majority always exists");
  // Default build (E22-400T33D): open = T33D, jumper to GND = T37S.
  CHECK(e22_strap_model(VGQ_RADIO_E22_T33D, 0, n) == VGQ_RADIO_E22_T33D, "strap open -> E22-400T33D");
  CHECK(e22_strap_model(VGQ_RADIO_E22_T33D, n, n) == VGQ_RADIO_E22_T37S, "strap to GND -> E22-400T37S");
  // Majority rule on a split reading.
  CHECK(e22_strap_model(VGQ_RADIO_E22_T33D, 3, n) == VGQ_RADIO_E22_T37S, "3 of 5 reads low -> E22-400T37S");
  CHECK(e22_strap_model(VGQ_RADIO_E22_T33D, 2, n) == VGQ_RADIO_E22_T33D, "2 of 5 reads low -> build default");
  CHECK(e22_strap_model(VGQ_RADIO_E22_T33D, 1, 2) == VGQ_RADIO_E22_T33D, "a tie is not a majority");
  // No strap / inconsistent input: the build default stays.
  CHECK(e22_strap_model(VGQ_RADIO_E22_T33D, 0, 0) == VGQ_RADIO_E22_T33D, "no samples (no strap) -> build default");
  CHECK(e22_strap_model(VGQ_RADIO_E22_T33D, 6, n) == VGQ_RADIO_E22_T33D, "more low reads than reads -> build default");
  // A T37S build stays a T37S build whatever the strap says.
  CHECK(e22_strap_model(VGQ_RADIO_E22_T37S, 0, n) == VGQ_RADIO_E22_T37S, "VGQ_RADIO=2 build, strap open -> T37S");
  CHECK(e22_strap_model(VGQ_RADIO_E22_T37S, n, n) == VGQ_RADIO_E22_T37S, "VGQ_RADIO=2 build, strap to GND -> T37S");
  // The SX126x option is a build switch (other wiring): the strap never changes it.
  CHECK(e22_strap_model(VGQ_RADIO_SX126X, n, n) == VGQ_RADIO_SX126X, "SX126x build: strap ignored");
  CHECK(e22_strap_model(VGQ_RADIO_SX126X, 0, n) == VGQ_RADIO_SX126X, "SX126x build: strap ignored (open)");
  CHECK(e22_strap_model(0, n, n) == 0 && e22_strap_model(9, n, n) == 9, "invalid build model passed through");
}

HOST_TEST(radio_model_follows_the_strap) {
  // Every model-dependent table takes the model chosen at boot (Radio::model()).
  const uint8_t jumper = e22_strap_model(VGQ_RADIO_E22_T33D, kStrapSamples, kStrapSamples);
  const uint8_t open = e22_strap_model(VGQ_RADIO_E22_T33D, 0, kStrapSamples);
  RadioSettings s;   // CH23, code 2, power 0
  uint8_t r[9];
  e22_build_regs(jumper, s, r);
  CHECK(r[4] == 0x24, "T37S (strap): REG1 = 0x24 (noise RSSI | abnormal-status log)");
  bool all24 = true;
  for (uint8_t c = 0; c <= kRadioMaxPowerCode; ++c) {
    s.power_code = c;
    e22_build_regs(jumper, s, r);
    all24 = all24 && r[4] == 0x24;
  }
  CHECK(all24, "T37S: REG1 = 0x24 for every power code (no power levels, contract 33)");
  s.power_code = 2;
  e22_build_regs(open, s, r);
  CHECK(r[4] == 0x22, "T33D (strap open): REG1 = 0x22 at power code 2, bit 2 reserved");
  CHECK(radio_tx_power_dbm(jumper, 3, 0) == 37 && radio_tx_power_dbm(open, 3, 0) == 24,
        "power code 3: 37 dBm on the T37S, 24 dBm on the T33D");
  SensSource src = SensSource::COMPUTED;
  CHECK(radio_sensitivity_ddbm(jumper, 2, true, &src) == -1260 && radio_sensitivity_ddbm(open, 2, true, &src) == -1280,
        "2.4k sensitivity follows the model: T37S -126.0, T33D -128.0 dBm");
  CHECK(std::strcmp(radio_model_name(jumper), "E22-400T37S") == 0, "RF radio_model / EVR name: E22-400T37S");

  // Abnormal-status decoding follows the model set at begin().
  E22StreamParser p(VGQ_RADIO_E22_T33D);
  RxPacket rx;
  int32_t v = 0;
  const uint8_t report[4] = {0xFF, 0xFF, 0xFF, 0x03};
  for (uint8_t b : report) p.on_byte(b, 100);
  CHECK(p.poll(200, rx, v) == RadioEvent::PACKET, "T33D: FF FF FF 03 is not a fault report (no such log)");
  p.set_model(jumper);
  for (uint8_t b : report) p.on_byte(b, 300);
  CHECK(p.poll(400, rx, v) == RadioEvent::FAULT && v == kRadioFaultOverTemp,
        "after set_model(T37S): FF FF FF 03 -> FAULT over-temperature");
}

HOST_TEST(radio_model_strap_pin_is_free) {
  // UNO Q digital-pin-gpios indices used by config.h (contract 1.1): the strap must
  // not share a pin with the E22 control lines, the SX126x option (incl. SPI2
  // D10..D13), the sensor bus A4/A5 (18/19) or the D0/D1 console UART.
  const int used[] = {0,  1,  PIN_E22_M0, PIN_E22_M1, PIN_E22_AUX, PIN_SX_NSS, PIN_SX_BUSY, PIN_SX_DIO1,
                      PIN_SX_NRST, PIN_SX_RXEN, PIN_SX_TXEN, 11, 12, 13, kPinSensorSda, kPinSensorScl};
  bool free_pin = true;
  for (int u : used) free_pin = free_pin && u != PIN_RADIO_MODEL_STRAP;
  CHECK(free_pin, "model strap pin (A0 = 14) is used by nothing else in config.h");
  CHECK(PIN_RADIO_MODEL_STRAP == 14, "model strap on header A0 / D14 (PA4)");
  CHECK(kStrapSettleUs >= 100 && kStrapSettleUs * kStrapSamples <= 20000,
        "strap read bounded: >= 0.1 ms settle per read, <= 20 ms in total at boot");
}
