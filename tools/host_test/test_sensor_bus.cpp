// =============================================================================
//  Host checks of the two sensor buses (D-73, fsw/sensor_bus.h) and the BHI260AP 6-DOF
//  path (D-75, fsw/attitude.h): the ARU page-2 decoder (magic, CRC, quaternion norm gate,
//  field offsets), the pairing rule, ImuStats (window de-duplication, gaps, RMS of the
//  window RMS, peaks, mean of the means, saturation, sentinels, period reset), the RM3100
//  loss counter, preferred-then-alternate probing and the per-bus recovery limiter.
// =============================================================================
#include <cmath>
#include <cstring>
#include <vector>

#include "fsw/attitude.h"
#include "fsw/sensor_bus.h"
#include "fsw/tlm_packets.h"
#include "host_test.h"

using namespace vgq;

namespace {

void put16(uint8_t* p, uint16_t v) { p[0] = (uint8_t)(v >> 8); p[1] = (uint8_t)v; }

// A page-2 image exactly as nicla_sense_me_aru.ino builds it.
struct Page2 {
  uint8_t seq = 7, flags = ARU_IMU_GRV | ARU_IMU_STATS | ARU_IMU_GRV_PRESENT | ARU_IMU_ACC_RANGE;
  int16_t q[4] = {16384, 0, 0, 0};
  uint8_t win_seq = 1, n = 25;
  int16_t acc[3] = {12, -34, 1001};
  uint16_t vib_rms = 25, vib_peak = 7;
  int16_t rate[3] = {-150, 3, 2500};
  uint16_t rate_peak = 251;
  void build(uint8_t* b) const {
    std::memset(b, 0, kAruPageLen);
    b[0] = kAruMagicImu; b[1] = seq; b[2] = flags;
    for (int i = 0; i < 4; ++i) put16(b + 3 + 2 * i, (uint16_t)q[i]);
    b[11] = win_seq; b[12] = n;
    for (int i = 0; i < 3; ++i) put16(b + 13 + 2 * i, (uint16_t)acc[i]);
    put16(b + 19, vib_rms); put16(b + 21, vib_peak);
    for (int i = 0; i < 3; ++i) put16(b + 23 + 2 * i, (uint16_t)rate[i]);
    put16(b + 29, rate_peak);
    b[31] = aru_crc8(b, kAruPageLen - 1);
  }
};

AruImu win(uint8_t seq, uint8_t flags, uint16_t vib, uint16_t vpk, int16_t rx, int16_t ry, int16_t rz, uint16_t rpk) {
  AruImu m;
  m.win_seq = seq; m.flags = flags; m.n = 25;
  m.vib_rms_dmg = vib; m.vib_peak_mg = vpk;
  m.rate_mean_cdps[0] = rx; m.rate_mean_cdps[1] = ry; m.rate_mean_cdps[2] = rz;
  m.rate_peak_ddps = rpk;
  return m;
}

}  // namespace

HOST_TEST(aru_page2_decode) {
  // CRC-8 poly 0x07 init 0: the standard check value of "123456789" is 0xF4.
  CHECK(aru_crc8((const uint8_t*)"123456789", 9) == 0xF4, "ARU CRC-8 (poly 0x07, init 0): check value 0xF4");
  uint8_t b[kAruPageLen];
  Page2 p;
  p.build(b);
  AruImu m;
  CHECK(aru_decode_imu(b, m) && m.seq == 7 && m.flags == p.flags && m.q_grv[0] == 16384 && m.win_seq == 1 &&
            m.n == 25 && m.acc_mean_mg[2] == 1001 && m.acc_mean_mg[1] == -34 && m.vib_rms_dmg == 25 &&
            m.vib_peak_mg == 7 && m.rate_mean_cdps[0] == -150 && m.rate_mean_cdps[2] == 2500 && m.rate_peak_ddps == 251,
        "page 2: every field at its offset (big-endian, signed where signed)");
  AruImu keep = m;
  uint8_t bad[kAruPageLen];
  std::memcpy(bad, b, sizeof bad);
  bad[20] ^= 0x01;
  CHECK(!aru_decode_imu(bad, m) && m.seq == keep.seq && m.vib_rms_dmg == keep.vib_rms_dmg,
        "page 2: a CRC error is refused and leaves the output unchanged");
  std::memcpy(bad, b, sizeof bad);
  bad[0] = 0xA7;                                          // a v3 ARU answers page 2 with page 0
  bad[31] = aru_crc8(bad, kAruPageLen - 1);
  CHECK(!aru_decode_imu(bad, m), "page 2: magic 0xA7 (page 0 of a v3 ARU) is refused");
  CHECK(!aru_decode_imu(nullptr, m), "page 2: null page refused");
  // Norm gate: 0.81 < |q|^2 < 1.21 keeps ARU_IMU_GRV, anything else withdraws only that flag.
  struct { int16_t w, x; bool keep; const char* what; } cases[] = {
      {16384, 0, true, "unit"},  {14745, 0, false, "|q|^2 just below 0.81 (0.9 x 16384 = 14745.6)"},
      {14746, 0, true, "|q|^2 just above 0.81"}, {18022, 0, true, "|q|^2 just below 1.21 (1.1 x 16384 = 18022.4)"},
      {18023, 0, false, "|q|^2 above 1.21"}, {0, 0, false, "zero"}, {11585, 11585, true, "(0.707, 0.707)"},
      {32767, 0, false, "w = 2 (0x7FFF)"}};
  bool gate = true;
  for (const auto& c : cases) {
    Page2 q = p;
    q.q[0] = c.w; q.q[1] = c.x;
    q.build(b);
    AruImu r;
    const bool ok = aru_decode_imu(b, r);
    gate = gate && ok && (((r.flags & ARU_IMU_GRV) != 0) == c.keep) &&
           ((r.flags & (uint8_t)~ARU_IMU_GRV) == (q.flags & (uint8_t)~ARU_IMU_GRV));
    if (!(ok && (((r.flags & ARU_IMU_GRV) != 0) == c.keep))) std::printf("      norm gate case: %s\n", c.what);
  }
  CHECK(gate, "page 2: quaternion norm gate 0.81..1.21 withdraws ARU_IMU_GRV only");
  // Pairing (Flight::start_mag_sample): page 2 belongs to page 0's rebuild when the seqs agree.
  Page2 a = p, c = p;
  a.seq = 200; c.seq = 201;
  AruImu ma, mc;
  a.build(b); (void)aru_decode_imu(b, ma);
  c.build(b); (void)aru_decode_imu(b, mc);
  const uint8_t page0_seq = 200;
  CHECK(ma.seq == page0_seq && mc.seq != page0_seq, "page 2: seq carried for the pairing with page 0");
}

HOST_TEST(imu_stats) {
  ImuStats s;
  CHECK(s.windows() == 0 && s.vib_rms_dmg() == kNaU16 && s.vib_peak_mg() == kNaU16 && s.rate_mean_cdps(0) == kNaI16 &&
            s.rate_peak_ddps() == kNaU16 && !s.gap() && !s.acc_sat() && !s.gyro_sat(),
        "ImuStats empty: ATT sentinels, no flags");
  const uint8_t V = ARU_IMU_STATS;
  // Before the ARU's first window (win_seq 0, n 0, no flags): nothing, and no gap.
  AruImu pre = win(0, 0, 0, 0, 0, 0, 0, 0);
  pre.n = 0;
  CHECK(!s.add(pre) && s.windows() == 0 && !s.gap(), "ImuStats: the ARU before its first window adds nothing, no gap");
  CHECK(s.add(win(1, V, 30, 10, 100, -200, 3000, 50)), "ImuStats: window 1 counted");
  CHECK(!s.add(win(1, V, 30, 10, 100, -200, 3000, 50)) && s.windows() == 1, "ImuStats: the same window read twice counts once");
  CHECK(s.add(win(2, V, 40, 25, 300, -100, 3001, 40)) && s.windows() == 2 && !s.gap(), "ImuStats: window 2, no gap");
  // RMS of the window RMS: sqrt((30^2 + 40^2) / 2) = 35.355 -> 35; peaks max; means mean.
  CHECK(s.vib_rms_dmg() == 35 && s.vib_peak_mg() == 25 && s.rate_peak_ddps() == 50,
        "ImuStats: vib_rms = RMS of the window RMS (35), peaks = max (25 mg, 5.0 deg/s)");
  CHECK(s.rate_mean_cdps(0) == 200 && s.rate_mean_cdps(1) == -150 && s.rate_mean_cdps(2) == 3001 &&
            s.rate_mean_cdps(3) == kNaI16,
        "ImuStats: rate_mean = mean of the window means, half away from zero (3000.5 -> 3001)");
  CHECK(s.add(win(5, V, 30, 1, 0, 0, 0, 1)) && s.gap() && s.windows() == 3, "ImuStats: windows 3 and 4 unread -> gap");
  s.reset();
  CHECK(s.windows() == 0 && !s.gap() && s.vib_rms_dmg() == kNaU16, "ImuStats reset: empty period");
  CHECK(!s.add(win(5, V, 30, 1, 0, 0, 0, 1)) && s.windows() == 0, "ImuStats reset keeps the last window: no double count");
  CHECK(s.add(win(6, V | ARU_IMU_MISSED, 10, 2, 0, 0, 0, 2)) && s.gap() && s.windows() == 1,
        "ImuStats: a window with samples missed is used and flags the gap");
  s.reset();
  CHECK(s.add(win(7, ARU_IMU_ACC_SAT | ARU_IMU_MISSED, 9999, 9999, 9999, 9999, 9999, 9999)) && s.windows() == 0 &&
            s.gap() && s.acc_sat() && !s.gyro_sat() && s.vib_rms_dmg() == kNaU16,
        "ImuStats: an invalid window (no ARU_IMU_STATS) is not aggregated; its saturation and gap still count");
  CHECK(s.add(win(8, V | ARU_IMU_GYRO_SAT, 20, 3, 0, 0, 0, 3)) && s.gyro_sat() && s.windows() == 1,
        "ImuStats: gyro saturation ORed");
  s.reset();
  // Wrap of win_seq 255 -> 0 is consecutive; a restart (counter going back) is a gap.
  (void)s.add(win(255, V, 1, 1, 0, 0, 0, 1));
  s.reset();
  CHECK(s.add(win(0, V, 1, 1, 0, 0, 0, 1)) && !s.gap(), "ImuStats: win_seq 255 -> 0 is the next window");
  CHECK(s.add(win(200, V, 1, 1, 0, 0, 0, 1)) && s.gap(), "ImuStats: a jump (ARU restart) is a gap");
  // Clamping clear of the sentinels and exactness of the integer RMS.
  s.reset();
  (void)s.add(win(1, V, 65535, 65535, 32767, -32768, 32767, 65535));
  CHECK(s.vib_rms_dmg() == 65534 && s.vib_peak_mg() == 65534 && s.rate_peak_ddps() == 65534 &&
            s.rate_mean_cdps(0) == 32766 && s.rate_mean_cdps(1) == -32767,
        "ImuStats: results clamped clear of the sentinels (65534 / 32766 / -32767)");
  bool rms_ok = true;
  for (int t = 0; t < 2000 && rms_ok; ++t) {
    ImuStats r;
    const int nw = 1 + (int)(host_test::rnd() % 40);
    double sum2 = 0;
    for (int k = 0; k < nw; ++k) {
      const uint16_t v = (uint16_t)(host_test::rnd() % 65535u);
      sum2 += (double)v * v;
      (void)r.add(win((uint8_t)(k + 1), V, v, 0, 0, 0, 0, 0));
    }
    const double want = std::floor(std::sqrt(sum2 / nw) + 0.5);   // exact in double for these sizes
    const double got = r.vib_rms_dmg();
    rms_ok = got == (want > 65534.0 ? 65534.0 : want);
    if (!rms_ok) std::printf("      rms mismatch n=%d sum2=%.0f got %.0f want %.0f\n", nw, sum2, got, want);
  }
  CHECK(rms_ok, "ImuStats: integer RMS of the window RMS equals round(sqrt(mean of squares)) (2000 random periods)");
}

HOST_TEST(sensor_bus_roles) {
  CHECK(std::strcmp(sensor_bus_name(SBUS_NICLA), "A4/A5") == 0 && std::strcmp(sensor_bus_name(SBUS_MAG), "Qwiic") == 0 &&
            std::strcmp(sensor_bus_name(kSensorBusNone), "none") == 0,
        "bus names A4/A5 / Qwiic (as EVR 0x0706 prints them)");
  CHECK(sensor_bus_other(SBUS_NICLA) == SBUS_MAG && sensor_bus_other(SBUS_MAG) == SBUS_NICLA, "other bus");
  // Preferred-then-alternate probing; the order of attempts is recorded.
  struct Case { uint8_t preferred; bool on[2]; uint8_t want; std::vector<uint8_t> tries; const char* what; };
  const Case cases[] = {
      {SBUS_NICLA, {true, true}, SBUS_NICLA, {SBUS_NICLA}, "Nicla on A4/A5: one attempt"},
      {SBUS_NICLA, {false, true}, SBUS_MAG, {SBUS_NICLA, SBUS_MAG}, "Nicla only on Qwiic: used there"},
      {SBUS_MAG, {true, true}, SBUS_MAG, {SBUS_MAG}, "RM3100 on Qwiic: one attempt"},
      {SBUS_MAG, {true, false}, SBUS_NICLA, {SBUS_MAG, SBUS_NICLA}, "RM3100 only on A4/A5: used there"},
      {SBUS_MAG, {false, false}, kSensorBusNone, {SBUS_MAG, SBUS_NICLA}, "absent: two attempts, none"},
  };
  for (const Case& c : cases) {
    std::vector<uint8_t> tries;
    const uint8_t got = probe_preferred(c.preferred, [&](uint8_t b) {
      tries.push_back(b);
      return c.on[b];
    });
    CHECK(got == c.want && tries == c.tries, c.what);
  }
  RecoverLimiter lim[2];
  CHECK(lim[0].allowed(5, 10000) && lim[1].allowed(5, 10000), "recovery limiter: first recovery allowed");
  lim[0].note(1000);
  CHECK(!lim[0].allowed(10999, 10000) && lim[0].allowed(11000, 10000) && lim[1].allowed(1001, 10000),
        "recovery limiter per bus: 10 s on the bus that recovered, the other bus unaffected");
  lim[1].note(0xFFFFF000u);
  CHECK(!lim[1].allowed(0x00001000u, 10000) && lim[1].allowed(0x00001710u, 10000),
        "recovery limiter: millis() wrap handled by unsigned difference");
}

HOST_TEST(rm3100_loss_counter) {
  LossCounter c;
  bool fired_early = false;
  for (int i = 0; i < 4; ++i) fired_early |= c.note(false, kRm3100LossSamples);
  CHECK(kRm3100LossSamples == 5 && !fired_early && c.fails == 4, "RM3100 loss: 4 failed samples do not declare a loss");
  CHECK(c.note(false, kRm3100LossSamples), "RM3100 loss: the 5th consecutive failure declares it (once)");
  CHECK(!c.note(false, kRm3100LossSamples) && !c.note(false, kRm3100LossSamples), "RM3100 loss: reported once only");
  c.reset();
  for (int i = 0; i < 4; ++i) (void)c.note(false, kRm3100LossSamples);
  CHECK(!c.note(true, kRm3100LossSamples) && c.fails == 0, "RM3100 loss: a good sample restarts the count");
  bool fired = false;
  for (int i = 0; i < 4; ++i) fired |= c.note(false, kRm3100LossSamples);
  CHECK(!fired && c.note(false, kRm3100LossSamples), "RM3100 loss: needs 5 in a row after a good sample");
  LossCounter sat;
  for (int i = 0; i < 300; ++i) (void)sat.note(false, kRm3100LossSamples);
  CHECK(sat.fails == 255, "RM3100 loss: the count saturates at 255");
  CHECK((SNS_ME_GRV == (1u << 10)) && (kSensorInstrumentMask & SNS_ME_GRV) == 0,
        "SNS_ME_GRV = bit 10, not an instrument bit of the SENSORS mask");
}
