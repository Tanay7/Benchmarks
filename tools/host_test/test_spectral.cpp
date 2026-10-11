// =============================================================================
//  AS7265X onboard spectral processing (src/fsw/spectral.*): exposure arithmetic
//  against the datasheet's integration table, saturation, the AGC ladder (including a
//  closed-loop convergence sweep over nine decades of scene brightness), HDR bracket
//  and merge, lock-in statistics, the SPEC v3.1 log code, the HEURISTIC light-source
//  classifier on SYNTHETIC spectra, change detection, boresight angles and the SPEC
//  v3.1 / SPECBURST field sets. Writes, for the Python cross-check:
//
//    <out>/spectral_vectors.txt   one record per line, '#' = comment:
//      log <value %.9g> <code 4 hex>      spec_log_encode(value) == code (FP32 encoder)
//      logdec <code 4 hex> <value %.9g>   spec_log_decode(code)
//      spec31 <hex>                       a packed SPEC v3.1 user-data field (129 octets)
//      field <name> <raw integer> <engineering value %.9g | nan>
//                                         every field of that packet, ground names/units
//      specburst <hex>                    a packed SPECBURST user-data field (3 samples)
//
//  The synthetic spectra are MODELS (Planck blackbodies, Gaussian LED / phosphor /
//  mercury-europium lines seen through 20 nm FWHM Gaussian channels), not measurements.
// =============================================================================
#include <cmath>
#include <cstring>
#include <string>

#include "fsw/spectral.h"
#include "host_test.h"

using namespace vgq;

namespace {

bool near(double a, double b, double tol) { return std::fabs(a - b) <= tol; }

// ---- synthetic sources -----------------------------------------------------------------
double planck(double nm, double t_k) {               // relative spectral radiance
  const double x = 1.4387769e7 / (nm * t_k);         // c2 = hc/k = 1.4388e7 nm K
  return std::pow(nm / 1000.0, -5.0) / (std::exp(x) - 1.0);
}
double gauss(double nm, double mu, double s) { return std::exp(-0.5 * ((nm - mu) / s) * ((nm - mu) / s)); }
double skew(double nm, double mu, double sl, double sr) { return gauss(nm, mu, nm < mu ? sl : sr); }

enum Src { BB2856, BB3200, BB5000, BB6500, LED_WW, LED_CW, FL_TRI, LED525, LED630, LED850 };
double spd(Src s, double l) {
  switch (s) {
    case BB2856: return planck(l, 2856);
    case BB3200: return planck(l, 3200);
    case BB5000: return planck(l, 5000);
    case BB6500: return planck(l, 6500);
    case LED_WW: return 0.5 * gauss(l, 450, 10) + 0.8 * skew(l, 600, 45, 60);
    case LED_CW: return 1.0 * gauss(l, 450, 10) + 0.6 * skew(l, 560, 45, 55);
    case FL_TRI: return 0.6 * gauss(l, 436, 5) + 1.0 * gauss(l, 546, 5) + 0.9 * gauss(l, 611, 5) +
                        0.3 * gauss(l, 405, 4) + 0.25 * gauss(l, 578, 4) + 0.08 * gauss(l, 490, 30) + 0.02;
    case LED525: return gauss(l, 525, 12);
    case LED630: return gauss(l, 630, 8);
    default: return gauss(l, 850, 15);
  }
}
// Channel values: the SPD through 20 nm FWHM Gaussian filters, scaled to `total` uW/cm2.
void synth(Src s, double total, float out[kSpecChannels]) {
  const double sig = 20.0 / 2.354820045;
  double v[kSpecChannels], sum = 0;
  for (int c = 0; c < kSpecChannels; ++c) {
    double num = 0, den = 0;
    for (int l = 300; l <= 1100; ++l) {
      const double w = gauss(l, kSpecWavelengthNm[c], sig);
      num += w * spd(s, l);
      den += w;
    }
    v[c] = num / den;
    sum += v[c];
  }
  for (int c = 0; c < kSpecChannels; ++c) out[c] = (float)(v[c] * total / sum);
}

uint8_t classify(const float spec[kSpecChannels], float unc_each, uint32_t valid, bool own,
                 uint8_t& conf) {
  float unc[kSpecChannels];
  for (int i = 0; i < kSpecChannels; ++i) unc[i] = unc_each;
  SpecFeatures f;
  const bool ok = spec_features(spec, unc, valid, f);
  return spec_classify(f, ok, own, conf);
}

// Simulated chip for the AGC sweep: counts = rate x gain x (N + 1), clipped at full scale.
void sim_frame(double rate, const SpecExposure& e, uint16_t& peak, bool& sat) {
  const double fs = spec_full_scale(e.int_cycles);
  const double c = rate * spec_gain_factor(e.gain_code) * (e.int_cycles + 1.0);
  peak = (uint16_t)(c >= fs ? fs : c);
  sat = peak >= fs - 1;
}

SpecFrame make_frame(uint8_t g, uint8_t c, const double* rate, double uw_per_count) {
  SpecFrame f{};
  f.exp = SpecExposure{g, c};
  f.valid = kSpecAllChannels;
  const double fs = spec_full_scale(c);
  for (int i = 0; i < kSpecChannels; ++i) {
    const double counts = rate[i] * spec_gain_factor(g) * (c + 1.0);
    f.raw[i] = (uint16_t)(counts >= fs ? fs : counts);
    // MODEL ASSUMPTION (not a datasheet fact): calibrated output independent of gain/time,
    // cal = counts / (gain x time) x k. The datasheet calibrates at 16x / 166 ms only and
    // warns other conditions may need recalibration - VERIFY with T-SC-08 (spectral.h).
    f.cal[i] = (float)(f.raw[i] / (spec_gain_factor(g) * (c + 1.0)) * uw_per_count);
  }
  return f;
}

}  // namespace

// =============================================================================
HOST_TEST(spectral_exposure_table) {
  // Datasheet Figure 30: value 0x00 -> 1023, 0x01 -> 2047, 0x11 -> 18431, >= 0x40 -> 65535.
  CHECK(spec_full_scale(0) == 1023, "full scale: integration value 0x00 -> 1023");
  CHECK(spec_full_scale(1) == 2047, "full scale: 0x01 -> 2047");
  CHECK(spec_full_scale(0x11) == 18431, "full scale: 0x11 -> 18431");
  CHECK(spec_full_scale(0x3F) == 65535 && spec_full_scale(0x40) == 65535 &&
            spec_full_scale(0xFF) == 65535, "full scale: 0x3F, 0x40, 0xFF -> 65535");
  CHECK(near(spec_integration_ms(0), 2.78, 1e-4) && near(spec_integration_ms(0x11), 50.04, 1e-3) &&
            near(spec_integration_ms(0x40), 180.7, 1e-3) && near(spec_integration_ms(0xFF), 711.68, 1e-3),
        "integration time (N + 1) x 2.78 ms (datasheet 2.78 / 50 / 181 / 711 ms)");
  CHECK(spec_conversion_ms(59, 3) == 334 && spec_conversion_ms(59, 2) == 334 &&
            spec_conversion_ms(59, 0) == 167 && spec_conversion_ms(59, 1) == 167,
        "conversion: two integrations in BANK 2/3, one in BANK 0/1");
  CHECK(near(spec_gain_factor(0), 1, 0) && near(spec_gain_factor(1), 3.7, 1e-6) &&
            near(spec_gain_factor(2), 16, 0) && near(spec_gain_factor(3), 64, 0) &&
            near(spec_gain_factor(9), 64, 0), "gain codes 1x / 3.7x / 16x / 64x (out of range -> 64x)");
  // Channel geometry: every wavelength exactly once; bank-mode slot sets.
  uint32_t seen = 0;
  bool once = true;
  for (int d = 0; d < kSpecDevices; ++d)
    for (int k = 0; k < kSpecSlots; ++k) {
      const uint32_t b = 1u << kSpecSlotChannel[d][k];
      once = once && !(seen & b);
      seen |= b;
    }
  CHECK(once && seen == kSpecAllChannels, "device/slot map covers the 18 channels exactly once");
  CHECK(spec_device_channels(1) == ((1u << 8) | (1u << 10) | (1u << 12) | (1u << 13) | (1u << 14) | (1u << 15)),
        "AS72651 carries R610 S680 T730 U760 V810 W860");
  CHECK(spec_device_channels(4) == 0x3Fu, "AS72653 carries A410 .. F535");
  bool banks = true;
  for (int d = 0; d < kSpecDevices; ++d) {
    const uint8_t m0 = spec_bank_slots(0, d), m1 = spec_bank_slots(1, d);
    banks = banks && __builtin_popcount(m0) == 4 && __builtin_popcount(m1) == 4 &&
            (m0 | m1) == 0x3F && spec_bank_slots(2, d) == 0x3F;
  }
  CHECK(banks, "BANK 0 / 1: four slots per device, together all six; BANK 2: all six");
  CHECK(spec_bank_slots(0, 0) == 0x1E && spec_bank_slots(1, 0) == 0x2D,
        "BANK 0: AS72651 S T U V (R, W zero); BANK 1: R T U W (S, V zero)");
}

HOST_TEST(spectral_saturation_and_agc) {
  uint16_t raw[kSpecChannels];
  for (int i = 0; i < kSpecChannels; ++i) raw[i] = 1000;
  raw[3] = 18430;                                     // full scale 18431 at 0x11 -> fs - 1
  raw[4] = 18429;
  raw[5] = 65535;                                     // above full scale but not valid
  CHECK(spec_saturation_mask(raw, 0x11, kSpecAllChannels & ~(1u << 5)) == (1u << 3),
        "saturation: raw >= full scale - 1 only, invalid channels ignored");
  CHECK(spec_peak(raw, ~(1u << 5) & kSpecAllChannels) == 18430, "peak over valid channels");

  SpecExposure nx;
  const SpecExposure c16{2, 59};
  CHECK(spec_agc_step(c16, 59, 30000, false, nx) == SPEC_AGC_SETTLED && nx.gain_code == 2 &&
            nx.int_cycles == 59, "AGC: 49 % of full scale -> settled, unchanged");
  CHECK(spec_agc_step(c16, 59, 0, true, nx) == SPEC_AGC_STEPPED && nx.gain_code == 1 &&
            nx.int_cycles == 59, "AGC: saturated frame steps down exactly one rung (16x -> 3.7x)");
  CHECK(spec_agc_step(SpecExposure{3, 118}, 59, 0, true, nx) == SPEC_AGC_STEPPED &&
            nx.gain_code == 3 && nx.int_cycles == 59,
        "AGC: saturated at 64x above base -> halve integration back to the base first");
  CHECK(spec_agc_step(SpecExposure{3, 63}, 59, 0, true, nx) == SPEC_AGC_STEPPED &&
            nx.gain_code == 2 && nx.int_cycles == 59,
        "AGC: saturated step skips a rung that cannot lower raw/full-scale (63 -> 59 cycles)");
  CHECK(spec_agc_step(SpecExposure{0, 59}, 59, 0, true, nx) == SPEC_AGC_LIMIT,
        "AGC: saturated at 1x and <= 63 cycles -> limit (shorter time does not help, Figure 30)");
  CHECK(spec_agc_step(SpecExposure{0, 200}, 200, 0, true, nx) == SPEC_AGC_STEPPED &&
            nx.gain_code == 0 && nx.int_cycles == 100, "AGC: saturated at 1x, 200 cycles -> 100");
  CHECK(spec_agc_step(c16, 59, (uint16_t)(0.05 * 61439), false, nx) == SPEC_AGC_STEPPED &&
            nx.gain_code == 3 && nx.int_cycles == 118,
        "AGC: 5 % at 16x -> 64x and 118 cycles (predicted 37 %); 236 cycles would predict 74 %");
  CHECK(spec_agc_step(c16, 59, 6, false, nx) == SPEC_AGC_STEPPED && nx.gain_code == 3 &&
            nx.int_cycles == 255, "AGC: near-dark -> top of the ladder in one decision");
  CHECK(spec_agc_step(SpecExposure{0, 59}, 59, 600, false, nx) == SPEC_AGC_STEPPED &&
            nx.gain_code == 3 && nx.int_cycles == 59, "AGC: 1 % at 1x -> straight to 64x (64 %)");
  CHECK(spec_agc_step(SpecExposure{1, 59}, 59, (uint16_t)(0.20 * 61439), false, nx) == SPEC_AGC_SETTLED,
        "AGC: 20 % at 3.7x -> settled (16x would predict 86 % > 70 %: hysteresis)");
  CHECK(spec_agc_step(c16, 59, (uint16_t)(0.90 * 61439), false, nx) == SPEC_AGC_STEPPED &&
            nx.gain_code == 1, "AGC: 90 % unsaturated -> 3.7x (predicted 21 %)");
  CHECK(spec_agc_step(SpecExposure{3, 255}, 59, 10, false, nx) == SPEC_AGC_LIMIT,
        "AGC: dark at 64x / 255 cycles -> limit");

  // Closed loop over nine decades: never ends saturated unless at the bottom rung, never
  // oscillates, settles within 6 frames.
  bool all_ok = true;
  int worst = 0;
  for (int e = -3; e <= 6; ++e) {
    for (int m = 1; m <= 9; m += 2) {
      const double rate = m * std::pow(10.0, e);     // counts per cycle at 1x
      SpecExposure x{2, 59};
      SpecExposure hist[8];
      int steps = 0;
      SpecAgcResult r = SPEC_AGC_STEPPED;
      uint16_t peak = 0;
      bool sat = false;
      for (; steps < 8; ++steps) {
        hist[steps] = x;
        sim_frame(rate, x, peak, sat);
        r = spec_agc_step(x, 59, peak, sat, nx);
        if (r != SPEC_AGC_STEPPED) break;
        x = nx;
      }
      sim_frame(rate, x, peak, sat);
      const bool bottom = x.gain_code == 0 && x.int_cycles <= 63;
      bool osc = false;
      for (int i = 2; i <= steps && i < 8; ++i)
        osc = osc || (hist[i].gain_code == hist[i - 2].gain_code && hist[i].int_cycles == hist[i - 2].int_cycles);
      if ((sat && !bottom) || osc || steps > 6) {
        all_ok = false;
        std::printf("      AGC sweep failure: rate %.3g steps %d sat %d osc %d\n", rate, steps, sat, osc);
      }
      if (steps > worst) worst = steps;
    }
  }
  CHECK(all_ok, "AGC closed loop, 1e-3 .. 9e6 counts/cycle: no saturation, no oscillation, <= 6 frames");
}

HOST_TEST(spectral_hdr) {
  SpecExposure b[4];
  uint8_t n = spec_hdr_bracket(SpecExposure{2, 59}, 3, b);
  CHECK(n == 3 && b[0].gain_code == 1 && b[1].gain_code == 2 && b[1].int_cycles == 59 &&
            b[2].gain_code == 3 && b[2].int_cycles == 59,
        "HDR bracket n=3 around 16x: 3.7x / 16x / 64x (factors of ~4)");
  n = spec_hdr_bracket(SpecExposure{0, 59}, 3, b);
  CHECK(n == 2, "HDR bracket at the bottom: duplicate (clamped) exposure removed");
  n = spec_hdr_bracket(SpecExposure{2, 59}, 4, b);
  bool up = n == 4;
  for (int i = 1; i < n; ++i)
    up = up && spec_exposure_rel(b[i]) > spec_exposure_rel(b[i - 1]);
  CHECK(up, "HDR bracket n=4: four distinct exposures, shortest first");
  // An even bracket straddles its centre: the SpecResult int_cycles of an HDR cycle (the
  // centre) is then not one of the exposures taken - documented in spectral.h.
  n = spec_hdr_bracket(SpecExposure{2, 59}, 2, b);
  bool centre_taken = false;
  for (int i = 0; i < n; ++i) centre_taken = centre_taken || (b[i].gain_code == 2 && b[i].int_cycles == 59);
  CHECK(n == 2 && !centre_taken, "HDR bracket n=2 around 16x/59: the centre itself is not taken");

  // Three exposures of one scene: channel 0 very bright, 1 dim, 2 saturates everywhere.
  double rate[kSpecChannels];
  for (int i = 0; i < kSpecChannels; ++i) rate[i] = 20.0;
  rate[0] = 150.0;                                    // 3.7x: 33300 (54 %), 16x: sat, 64x: sat
  rate[1] = 0.5;                                      // dim: best at 64x (1920 counts)
  rate[2] = 1.0e6;
  const SpecFrame f[3] = {make_frame(1, 59, rate, 0.03), make_frame(2, 59, rate, 0.03),
                          make_frame(3, 59, rate, 0.03)};
  float spec[kSpecChannels], unc[kSpecChannels];
  uint16_t raw[kSpecChannels];
  uint8_t chosen[kSpecChannels];
  uint32_t sat = 0;
  spec_hdr_merge(f, 3, spec, unc, raw, sat, chosen);
  CHECK(chosen[0] == 0 && raw[0] == f[0].raw[0], "HDR merge: bright channel from the 3.7x exposure");
  CHECK(chosen[1] == 2 && raw[1] == f[2].raw[1], "HDR merge: dim channel from the 64x exposure");
  CHECK(chosen[3] == 1, "HDR merge: 20 counts/cycle -> 16x (19200, 31 %; 64x would be 125 %)");
  CHECK(chosen[2] == 0 && (sat & (1u << 2)) && !(sat & 1u),
        "HDR merge: saturated everywhere -> lowest exposure, flagged saturated");
  const double expect0 = 150.0 * 0.03;
  CHECK(near(spec[0], expect0, 1e-3 * expect0), "HDR merge: calibrated value of the chosen frame");
  // Uncertainty model: sigma_counts = sqrt(1/12 + (5 x 3.7/64)^2) through cal/raw.
  const double sc = std::sqrt(1.0 / 12.0 + (5.0 * 3.7 / 64.0) * (5.0 * 3.7 / 64.0));
  CHECK(near(unc[0], sc * spec[0] / raw[0], 1e-6), "HDR merge: uncertainty = sigma_counts x cal/raw");
  CHECK(near(spec_sigma_counts(3), std::sqrt(1.0 / 12.0 + 25.0), 1e-5),
        "uncertainty model at 64x: sqrt(1/12 + 5^2) counts (datasheet dark <= 5 counts)");
  CHECK(near(spec_counts_per_uw(2, 59), 35.0 * 166.8 / 166.0, 1e-3),
        "responsivity fallback: 35 counts/(uW/cm2) at 16x / 166 ms (scaled, approximate)");
  SpecFrame g[2] = {f[0], f[1]};
  g[1].valid &= ~(1u << 7);
  spec_hdr_merge(g, 2, spec, unc, raw, sat, chosen);
  CHECK(std::isnan(spec[7]) && raw[7] == 0xFFFF && chosen[7] == 0xFF,
        "HDR merge: channel missing in one exposure -> n/a");
}

HOST_TEST(spectral_lockin) {
  const float d[5] = {9, 11, 10, 12, 8};             // on - off of five pairs
  SpecLockin L;
  L.reset();
  for (int k = 0; k < 5; ++k) {
    SpecFrame on{}, off{};
    on.valid = off.valid = kSpecAllChannels;
    for (int i = 0; i < kSpecChannels; ++i) { off.cal[i] = 100.0f + k; on.cal[i] = off.cal[i] + d[k]; }
    L.add(on, off);
  }
  float model[kSpecChannels], mean[kSpecChannels], unc[kSpecChannels];
  for (int i = 0; i < kSpecChannels; ++i) model[i] = 0.123f;
  L.finish(model, mean, unc);
  CHECK(L.count() == 5 && near(mean[0], 10.0, 1e-5) && near(mean[17], 10.0, 1e-5),
        "lock-in: mean(on - off) = 10 (ambient drift cancelled)");
  CHECK(near(unc[0], std::sqrt(2.5) / std::sqrt(5.0), 1e-5), "lock-in: SEM = sample std / sqrt(n) = 0.7071");
  SpecLockin one;
  one.reset();
  SpecFrame on{}, off{};
  on.valid = off.valid = kSpecAllChannels & ~(1u << 4);
  for (int i = 0; i < kSpecChannels; ++i) { on.cal[i] = 5; off.cal[i] = 1; }
  one.add(on, off);
  one.finish(model, mean, unc);
  CHECK(near(mean[0], 4.0, 1e-6) && near(unc[0], 0.123, 1e-6), "lock-in n = 1: model sigma");
  CHECK(std::isnan(mean[4]) && std::isnan(unc[4]), "lock-in: channel invalid in a pair -> n/a");
}

HOST_TEST(spectral_log_code) {
  CHECK(spec_log_encode(0.0f) == 0x0000 && spec_log_encode(NAN) == 0xFFFF,
        "log code: 0 -> 0x0000, NaN -> 0xFFFF");
  CHECK(spec_log_encode(1e-4f) == 0x0001 && spec_log_encode(-1e-4f) == 0x8001,
        "log code: +/-1e-4 -> m = 1");
  CHECK(spec_log_encode(9.9e-5f) == 0x0000 && spec_log_encode(-9e-5f) == 0x8000,
        "log code: |v| < 1e-4 -> m = 0 (sign kept, decodes to 0)");
  CHECK(spec_log_encode(1e9f) == 0x7FFE && spec_log_encode(-1e9f) == 0xFFFE &&
            spec_log_encode(INFINITY) == 0x7FFE && spec_log_encode(-INFINITY) == 0xFFFE,
        "log code: saturates at m = 32766 (never collides with 0xFFFF)");
  CHECK(std::isnan(spec_log_decode(0xFFFF)) && spec_log_decode(0) == 0.0f && spec_log_decode(0x8000) == 0.0f,
        "log decode: 0xFFFF -> NaN, m = 0 -> 0");
  CHECK(near(spec_log_decode(0x7FFE), 1e-4 * std::pow(2.0, 32765.0 / 1024.0), 1.0) &&
            spec_log_decode(0x7FFE) > 4.28e5f, "log decode: maximum code 0x7FFE = 4.286e5 uW/cm2");
  // Round trip within 0.04 % over the whole range (step 0.068 %), monotonic encoder.
  double worst = 0;
  bool mono = true;
  uint16_t prev = 0;
  for (int i = 0; i <= 20000; ++i) {
    const float v = (float)(1e-4 * std::pow(10.0, 9.63 * i / 20000.0));
    const uint16_t c = spec_log_encode(v);
    mono = mono && c >= prev;
    prev = c;
    const double rel = std::fabs(spec_log_decode(c) - v) / v;
    if (rel > worst) worst = rel;
    const double reln = std::fabs(spec_log_decode(spec_log_encode(-v)) + v) / v;
    if (reln > worst) worst = reln;
  }
  std::printf("      log code worst round-trip error %.5f %%\n", worst * 100);
  CHECK(worst < 4e-4, "log code round trip within 0.04 % from 1e-4 to 4.27e5 (both signs)");
  CHECK(mono, "log encoder is monotonic");
}

HOST_TEST(spectral_classifier) {
  // SYNTHETIC spectra (models, see the file header), 200 uW/cm2 total, 0.05 uW/cm2 noise.
  struct Case { Src s; uint8_t want; const char* name; };
  const Case cases[] = {
      {BB2856, SPEC_CLASS_INCANDESCENT, "2856 K blackbody (illuminant A) -> INCANDESCENT"},
      {BB3200, SPEC_CLASS_INCANDESCENT, "3200 K blackbody (halogen) -> INCANDESCENT"},
      {BB5000, SPEC_CLASS_DAYLIGHT, "5000 K blackbody -> DAYLIGHT"},
      {BB6500, SPEC_CLASS_DAYLIGHT, "6500 K blackbody -> DAYLIGHT"},
      {LED_WW, SPEC_CLASS_WHITE_LED, "warm-white phosphor LED -> WHITE LED"},
      {LED_CW, SPEC_CLASS_WHITE_LED, "cool-white phosphor LED -> WHITE LED"},
      {FL_TRI, SPEC_CLASS_FLUORESCENT, "tri-phosphor fluorescent (Hg 436, Tb 546, Eu 611) -> FLUORESCENT"},
      {LED525, SPEC_CLASS_NARROWBAND, "525 nm LED -> NARROWBAND"},
      {LED630, SPEC_CLASS_NARROWBAND, "630 nm LED -> NARROWBAND"},
      {LED850, SPEC_CLASS_NARROWBAND, "850 nm LED -> NARROWBAND"},
  };
  float s[kSpecChannels];
  for (const Case& c : cases) {
    synth(c.s, 200.0, s);
    uint8_t conf = 0;
    const uint8_t cls = classify(s, 0.05f, kSpecAllChannels, false, conf);
    std::printf("      %-70s class %u conf %u %%\n", c.name, cls, conf);
    CHECK(cls == c.want && conf > 0 && conf <= 100, c.name);
  }
  uint8_t conf = 0;
  for (int i = 0; i < kSpecChannels; ++i) s[i] = 0;
  CHECK(classify(s, 0.01f, kSpecAllChannels, false, conf) == SPEC_CLASS_DARK && conf == 100,
        "zero spectrum -> DARK, 100 %");
  synth(BB6500, 0.1, s);
  CHECK(classify(s, 0.001f, kSpecAllChannels, false, conf) == SPEC_CLASS_DARK,
        "0.1 uW/cm2 total -> DARK (below dark_total_uw)");
  synth(BB6500, 10.0, s);
  CHECK(classify(s, 10.0f, kSpecAllChannels, false, conf) == SPEC_CLASS_DARK,
        "10 uW/cm2 buried in 10 uW/cm2-per-channel noise -> DARK (not significant)");
  CHECK(classify(s, 0.01f, kSpecAllChannels, true, conf) == SPEC_CLASS_OWN_LAMP && conf == 100,
        "active (lamp-on minus lamp-off) spectrum -> OWN LAMP, 100 %");
  synth(BB6500, 200.0, s);
  CHECK(classify(s, 0.05f, kSpecAllChannels & ~0x3Fu, false, conf) == SPEC_CLASS_UNKNOWN && conf == 0,
        "AS72653 missing (partial spectrum) -> UNKNOWN");
}

HOST_TEST(spectral_change_detection) {
  float a[kSpecChannels] = {0}, b[kSpecChannels] = {0};
  a[0] = 1;
  b[0] = 1; b[1] = 1;
  CHECK(near(spec_angle_deg(a, b, kSpecAllChannels), 45.0, 1e-4), "spectral angle (1,0) vs (1,1) = 45 deg");
  float c[kSpecChannels];
  synth(BB2856, 50.0, c);
  float c2[kSpecChannels];
  for (int i = 0; i < kSpecChannels; ++i) c2[i] = 7.0f * c[i];
  CHECK(near(spec_angle_deg(c, c2, kSpecAllChannels), 0.0, 0.02), "spectral angle is scale invariant");
  const float zero[kSpecChannels] = {0};
  CHECK(std::isnan(spec_angle_deg(c, zero, kSpecAllChannels)),
        "spectral angle of an empty spectrum -> n/a");

  SpecChangeDetector det;
  CHECK(std::isnan(det.update(c, kSpecAllChannels, 0.1f)), "detector: first spectrum seeds the baseline");
  CHECK(near(det.update(c2, kSpecAllChannels, 0.1f), 0.0, 0.02), "detector: same shape, brighter -> 0 deg");
  float d[kSpecChannels];
  synth(BB6500, 50.0, d);
  const double ang = spec_angle_deg(c, d, kSpecAllChannels);
  CHECK(near(det.update(d, kSpecAllChannels, 0.1f), ang, 0.02), "detector: new source -> angle to baseline");
  float last = 0;
  for (int k = 0; k < 60; ++k) last = det.update(d, kSpecAllChannels, 0.1f);
  CHECK(last < 0.05 * ang, "detector: EWMA baseline converges to the new source");

  // Processor: event threshold, 60 s rate limit, class persistence, SPECBASE.
  SpecProcessor proc;
  SpecResult r;
  spec_result_clear(r);
  r.flags = SPEC_F_VALID;
  r.valid_mask = kSpecAllChannels;
  for (int i = 0; i < kSpecChannels; ++i) { r.spec[i] = c[i]; r.unc[i] = 0.01f; }
  SpecAttitude att;
  SpecV31 v;
  SpecEvents e = proc.process(r, att, 0, v);
  CHECK(!e.spectral_event && v.change_cdeg == 0xFFFF && v.class_id == SPEC_CLASS_INCANDESCENT &&
            !e.class_changed, "processor: first spectrum -> no event, change n/a, class pending");
  e = proc.process(r, att, 1, v);
  CHECK(e.class_changed && e.old_class == SPEC_CLASS_UNKNOWN && e.new_class == SPEC_CLASS_INCANDESCENT &&
            v.change_cdeg == 0, "processor: class reported after 2 consecutive cycles");
  for (int i = 0; i < kSpecChannels; ++i) r.spec[i] = d[i];
  e = proc.process(r, att, 10, v);
  CHECK(e.spectral_event && e.angle_cdeg > 500 && v.change_cdeg == e.angle_cdeg,
        "processor: new source -> spectral event (> 5.00 deg)");
  float led[kSpecChannels];
  synth(LED_CW, 50.0, led);
  for (int i = 0; i < kSpecChannels; ++i) r.spec[i] = led[i];
  e = proc.process(r, att, 40, v);
  std::printf("      change angles: 2856 K -> 6500 K %.2f deg, then -> white LED %.2f deg\n", ang,
              e.angle_cdeg / 100.0);
  CHECK(!e.spectral_event && e.angle_cdeg > 500, "processor: second change within 60 s -> rate-limited");
  for (int i = 0; i < kSpecChannels; ++i) r.spec[i] = d[i];
  e = proc.process(r, att, 75, v);
  CHECK(e.spectral_event, "processor: change after >= 60 s -> event");
  CHECK(proc.set_baseline_cmd(9000) && proc.event_threshold_cdeg() == 9000 && !proc.set_baseline_cmd(9001) &&
            proc.set_baseline_cmd(0) && proc.event_threshold_cdeg() == 9000,
        "SPECBASE: 0 resets only, 1..9000 sets, > 9000 rejected");
  e = proc.process(r, att, 200, v);
  CHECK(v.change_cdeg == 0xFFFF, "SPECBASE: baseline restarted -> change n/a");
  r.flags = SPEC_F_VALID | SPEC_F_ACTIVE;
  r.lamps = SPEC_LAMP_WHITE;
  e = proc.process(r, att, 300, v);
  CHECK(v.class_id == SPEC_CLASS_OWN_LAMP && v.change_cdeg == 0xFFFF,
        "processor: lamps / active mode change -> OWN LAMP, baseline restarted");
}

HOST_TEST(spectral_boresight) {
  float z, a;
  const Vec3 x{1, 0, 0}, ybody{0, 1, 0}, zb{0, 0, 1};
  const float s45 = std::sqrt(0.5f);
  CHECK(spec_boresight(Quat{1, 0, 0, 0}, SPEC_WORLD_NED, x, z, a) && near(z, 90, 1e-3) && near(a, 0, 1e-3),
        "boresight: identity, body x -> horizon, north");
  CHECK(spec_boresight(Quat{s45, 0, 0, s45}, SPEC_WORLD_NED, x, z, a) && near(z, 90, 1e-3) && near(a, 90, 1e-3),
        "boresight: yaw +90 deg about down -> east (az 90)");
  const float h = 15.0f * 3.14159265f / 180.0f;
  CHECK(spec_boresight(Quat{std::cos(h), 0, std::sin(h), 0}, SPEC_WORLD_NED, x, z, a) &&
            near(z, 60, 1e-3) && near(a, 0, 1e-3), "boresight: pitch up 30 deg -> zenith angle 60");
  CHECK(spec_boresight(Quat{1, 0, 0, 0}, SPEC_WORLD_NED, zb, z, a) && near(z, 180, 1e-3) && std::isnan(a),
        "boresight: body z = down (NED) -> zenith angle 180, azimuth n/a");
  CHECK(spec_boresight(Quat{1, 0, 0, 0}, SPEC_WORLD_ENU, zb, z, a) && near(z, 0, 1e-3) && std::isnan(a),
        "boresight ENU: body z = up -> zenith angle 0");
  CHECK(spec_boresight(Quat{1, 0, 0, 0}, SPEC_WORLD_ENU, ybody, z, a) && near(a, 0, 1e-3) &&
            spec_boresight(Quat{1, 0, 0, 0}, SPEC_WORLD_ENU, x, z, a) && near(a, 90, 1e-3),
        "boresight ENU: north (y) -> az 0, east (x) -> az 90");
  const float h2 = 100.0f * 3.14159265f / 180.0f;    // yaw 200 deg
  CHECK(spec_boresight(Quat{std::cos(h2), 0, 0, std::sin(h2)}, SPEC_WORLD_NED, x, z, a) && near(a, 200, 1e-2),
        "boresight: yaw 200 deg -> az 200 (0..360)");
  CHECK(spec_boresight(Quat{2, 0, 0, 0}, SPEC_WORLD_NED, x, z, a) && near(a, 0, 1e-3) &&
            !spec_boresight(Quat{0, 0, 0, 0}, SPEC_WORLD_NED, x, z, a),
        "boresight: quaternion normalised; zero quaternion rejected");
  // TRIAD returns NED -> body: its conjugate gives the same answer as the true body -> NED q.
  Quat qt;
  const float c30 = std::cos(30.0f * 3.14159265f / 180.0f), s30 = std::sin(30.0f * 3.14159265f / 180.0f);
  // Body yawed +30 deg: gravity reaction (up) in body = (0, 0, -1); field north/down rotated by -30.
  const float I = 60.0f * 3.14159265f / 180.0f;
  const Vec3 acc{0, 0, -1};
  const Vec3 mag{std::cos(I) * c30, -std::cos(I) * s30, std::sin(I)};
  CHECK(triad_ned(acc, mag, 60.0f, 0.0f, qt) &&
            spec_boresight(spec_conj(qt), SPEC_WORLD_NED, x, z, a) && near(a, 30, 0.05) && near(z, 90, 0.05),
        "boresight from TRIAD (conjugated): yaw 30 deg -> az 30");
}

HOST_TEST(spectral_lamp_limits) {
  // Board vendor's limits (SparkFun AS7265x library 1.0.5, Example3_Settings.ino lines 65-81):
  // white LED max 120 mA (all codes), IR LED max 65 mA (100 mA not allowed), UV LED max
  // 30 mA (25 / 50 / 100 mA not allowed).
  CHECK(kSpecLedMaxCode[0] == 3 && kSpecLedMaxCode[1] == 2 && kSpecLedMaxCode[2] == 0,
        "lamp limits: white up to 100 mA, IR up to 50 mA, UV 12.5 mA only (codes 3 / 2 / 0)");
  SpecConfig c;
  const uint8_t bad[] = {0x10, 0x20, 0x30, 0x0C, 0x3F, 0x1B};
  bool rejected = true;
  for (uint8_t v : bad) { c.led_cur = v; rejected = rejected && !spec_config_valid(c); }
  CHECK(rejected, "SPECCFG led_cur: UV 25/50/100 mA (0x10/0x20/0x30), IR 100 mA (0x0C) rejected");
  const uint8_t good[] = {0x00, 0x03, 0x08, 0x0B};
  bool accepted = true;
  for (uint8_t v : good) { c.led_cur = v; accepted = accepted && spec_config_valid(c); }
  CHECK(accepted, "SPECCFG led_cur: 0x00, 0x03 (white 100 mA), 0x08 (IR 50 mA), 0x0B accepted");
  c = SpecConfig(); c.mode = SPEC_FLUOR; c.n = 1; c.led_cur = 0x30;
  CHECK(!spec_config_valid(c), "FLUOR (UV lamp forced) with UV 100 mA rejected");
  // Second layer, used for every LED_CONFIG value the driver writes: clamped whatever led_cur holds.
  bool clamped = true;
  for (int v = 0; v < 256; ++v)
    for (uint8_t d = 0; d < kSpecDevices; ++d) {
      const int code = (v >> (2 * d)) & 3;
      const int want = code < kSpecLedMaxCode[d] ? code : kSpecLedMaxCode[d];
      clamped = clamped && spec_led_code((uint8_t)v, d) == want;
    }
  CHECK(clamped && spec_led_code(0xFF, kSpecDevices) == 0, "spec_led_code: every device's code capped at its lamp limit");
  CHECK(spec_led_cur_applied(0x3F) == 0x0B && spec_led_cur_applied(0x0B) == 0x0B &&
            spec_led_cur_applied(0x31) == 0x01, "applied led_cur: 0x3F -> 0x0B, 0x31 -> 0x01");
  // Lamp bit d is device d's LED_DRV: the driver masks lamps with the DEV_SEL device mask.
  CHECK(SPEC_LAMP_WHITE == 1 && SPEC_LAMP_IR == 2 && SPEC_LAMP_UV == 4, "lamp bit d = LED_DRV of device d");
}

namespace {
// The AS72651's virtual-register protocol state as the datasheet describes it ("I2C Virtual
// Register Write Access": bit 7 of an address byte marks a pending write, the next byte is
// its data; bit 7 clear is a read request). Records every register write.
struct VregSlave {
  int pending = -1;                                   // register awaiting its data byte
  int writes = 0, bad_writes = 0, reads = 0;
  int value[128];
  VregSlave() { for (int& v : value) v = -1; }
  void byte(uint8_t b) {
    if (pending >= 0) {
      ++writes;
      if (!spec_vreg_write_allowed((uint8_t)pending, b)) ++bad_writes;
      value[pending] = b;
      pending = -1;
    } else if (b & 0x80) {
      pending = b & 0x7F;
    } else {
      ++reads;
    }
  }
  void write(uint8_t reg, uint8_t data) { byte((uint8_t)(reg | 0x80)); byte(data); }
};
}  // namespace

HOST_TEST(spectral_vreg_protocol) {
  bool refused = true;
  for (int v = 0; v < 256; ++v) {
    for (int a = 0x48; a <= 0x4B; ++a) refused = refused && !spec_vreg_write_allowed((uint8_t)a, (uint8_t)v);
    for (int a = 0x50; a <= 0x53; ++a) refused = refused && !spec_vreg_write_allowed((uint8_t)a, (uint8_t)v);
    refused = refused && !spec_vreg_write_allowed(0x55, (uint8_t)v);
    if (v & kSpecCfgFrst) refused = refused && !spec_vreg_write_allowed(kSpecVConfig, (uint8_t)v);
  }
  CHECK(refused, "write policy: FW update 0x48-0x4B, COEF_DATA 0x50-0x53, COEF_WRITE 0x55, CONFIG.FRST refused");
  CHECK(!spec_vreg_write_allowed(kSpecVInteg, 0) && !spec_vreg_write_allowed(kSpecVLed, 0x80) &&
            !spec_vreg_write_allowed(kSpecVDevSel, 3) && spec_vreg_write_allowed(kSpecVInteg, 255),
        "write policy: INTEGRATION 0, LED_CONFIG b7, DEV_SEL 3 refused; INTEGRATION 255 allowed");

  // After a virtual write whose outcome is unknown the slave is idle or waits for that
  // register's data byte. The resync byte must be harmless in both states and leave it idle.
  const uint8_t regs[] = {kSpecVFwH, kSpecVFwL, kSpecVConfig, kSpecVInteg, kSpecVLed, kSpecVDevSel,
                          kSpecVCoefRead};
  bool ok = true;
  for (uint8_t v : regs) {
    const uint8_t b = spec_vreg_resync_byte(v);
    ok = ok && !(b & 0x80) && spec_vreg_write_allowed(v, b);
    if (v == kSpecVLed) ok = ok && !(b & (kSpecLedDrvEn | kSpecLedIndEn));   // lights nothing
    VregSlave waiting;
    waiting.pending = v;
    waiting.byte(b);
    VregSlave idle;
    idle.byte(b);
    ok = ok && waiting.pending < 0 && waiting.writes == 1 && waiting.bad_writes == 0 && idle.pending < 0 &&
         idle.writes == 0 && idle.reads == 1;
  }
  CHECK(ok, "resync byte: bit 7 clear, legal for its register, lights nothing; slave idle in both states");

  // Regression AS-PROT-1 / AS-T2: the former recovery resent the "owed" data byte. If the
  // slave had latched it after all, INTEGRATION 0xD5 became a write address of COEF_WRITE
  // (0x55) and the safe-off list's first byte 0xCF its data (persistent calibration memory).
  VregSlave old_scheme;
  old_scheme.byte(0xD5);
  old_scheme.write(kSpecVDevSel, 0);
  CHECK(old_scheme.bad_writes == 1 && old_scheme.value[0x55] == 0xCF,
        "former scheme reproduced: owed 0xD5 to an idle slave -> COEF_WRITE <- 0xCF");
  // The resync byte does not depend on the value: INTEGRATION 0x80..0xFF are all safe.
  const uint8_t integ_resync = spec_vreg_resync_byte(kSpecVInteg);
  bool all_integ = true;
  for (int integ = 1; integ < 256; ++integ) {
    for (int latched = 0; latched <= 1; ++latched) {
      VregSlave s;
      s.byte((uint8_t)(0x80 | kSpecVInteg));                 // address delivered
      if (latched) s.byte((uint8_t)integ);                   // data latched, failure reported
      s.byte(integ_resync);                                  // next list: resync byte first
      s.write(kSpecVConfig, 0x6C);                           // then the safe-off list
      s.write(kSpecVDevSel, 0);
      s.write(kSpecVLed, 0x00);
      all_integ = all_integ && s.bad_writes == 0 && s.pending < 0 && s.value[kSpecVConfig] == 0x6C &&
                  s.value[kSpecVDevSel] == 0 && s.value[kSpecVLed] == 0x00;
    }
  }
  CHECK(all_integ, "resync after INTEGRATION 1..255, data latched or not: safe-off bytes land as intended");
  // Regression AS-PROT-3 / AS-T1: a latched address byte (CONFIG 0x84 or LED_CONFIG 0x87)
  // whose write was reported failed. The former safe-off list began with 0xCF, which became
  // CONFIG data SRST | INT | BANK 3 | FRST, or LED_CONFIG data with LED_DRV enabled.
  VregSlave old_cfg;
  old_cfg.byte((uint8_t)(0x80 | kSpecVConfig));
  old_cfg.write(kSpecVDevSel, 0);
  VregSlave old_led;
  old_led.byte((uint8_t)(0x80 | kSpecVLed));
  old_led.write(kSpecVDevSel, 0);
  CHECK(old_cfg.bad_writes == 1 && old_cfg.value[kSpecVConfig] == 0xCF && old_led.value[kSpecVLed] == 0xCF,
        "former scheme reproduced: 0xCF taken as CONFIG data (refused: FRST) / LED_CONFIG data (lamp on)");
  bool addr_ok = true;
  for (uint8_t v : regs) {
    for (int latched = 0; latched <= 1; ++latched) {
      VregSlave s;
      if (latched) s.byte((uint8_t)(0x80 | v));             // address latched, failure reported
      s.byte(spec_vreg_resync_byte(v));
      s.write(kSpecVConfig, 0x6C);
      s.write(kSpecVDevSel, 0);
      s.write(kSpecVLed, 0x00);
      addr_ok = addr_ok && s.bad_writes == 0 && s.pending < 0 && s.value[kSpecVConfig] == 0x6C &&
                s.value[kSpecVDevSel] == 0 && s.value[kSpecVLed] == 0x00;
    }
  }
  CHECK(addr_ok, "resync after a write address of any register, latched or not: no stray data, lamp off");

  // SPECBURST raw code (AS-DP-07): 0xFFFF is reserved for "not converted".
  CHECK(spec_burst_raw(0xFFFF) == 0xFFFE && spec_burst_raw(0xFFFE) == 0xFFFE && spec_burst_raw(1234) == 1234 &&
            spec_burst_raw(0) == 0, "SPECBURST raw: a real count of 65535 is sent as 0xFFFE");
  uint16_t raw[kSpecChannels] = {0};
  raw[0] = spec_burst_raw(0xFFFF);
  CHECK(spec_saturation_mask(raw, 63, kSpecAllChannels) == 1u,
        "SPECBURST raw 0xFFFE at full scale 65535 (int_cycles >= 63) counts as saturated");
}

HOST_TEST(spectral_config_and_packets) {
  SpecConfig c;
  CHECK(spec_config_valid(c), "default SpecConfig valid (SURVEY, AGC, 166.8 ms)");
  c.mode = SPEC_HDR; c.n = 1;
  const bool h1 = spec_config_valid(c);
  c.n = 4;
  const bool h4 = spec_config_valid(c);
  c.n = 5;
  CHECK(!h1 && h4 && !spec_config_valid(c), "SPECMODE HDR: n 2..4");
  c = SpecConfig(); c.mode = SPEC_LOCKIN; c.n = 16;
  const bool l0 = spec_config_valid(c);
  c.lamps = SPEC_LAMP_WHITE;
  const bool l1 = spec_config_valid(c);
  c.n = 17;
  CHECK(!l0 && l1 && !spec_config_valid(c), "SPECMODE LOCKIN: n 1..16 and at least one lamp");
  c = SpecConfig(); c.mode = SPEC_BURST; c.n = 60;
  const bool b60 = spec_config_valid(c);
  c.n = 61;
  CHECK(b60 && !spec_config_valid(c), "SPECMODE BURST: n 1..60 s");
  c = SpecConfig(); c.gain = 4;
  const bool g4 = spec_config_valid(c);
  c.gain = 255; c.int_cycles = 0;
  const bool i0 = spec_config_valid(c);
  c.int_cycles = 1; c.lamps = 8;
  const bool l8 = spec_config_valid(c);
  c.lamps = 7; c.led_cur = 64;
  const bool c64 = spec_config_valid(c);
  c.led_cur = 0x0B; c.ind = 5;
  const bool i5 = spec_config_valid(c);
  c.ind = 4;
  const bool ok4 = spec_config_valid(c);
  c.mode = 5;
  CHECK(!g4 && !i0 && !l8 && !c64 && !i5 && ok4 && !spec_config_valid(c),
        "SPECCFG ranges: gain 0..3|255, cycles 1..255, lamps 0..7, led_cur <= 63, ind 0..4, mode 0..4");
  c = SpecConfig(); c.mode = SPEC_FLUOR; c.lamps = SPEC_LAMP_WHITE;
  CHECK(spec_effective_lamps(c) == SPEC_LAMP_UV, "FLUOR forces the UV lamp");
  c.mode = SPEC_HDR; c.lamps = 7;
  CHECK(spec_effective_lamps(c) == 0, "HDR is passive (no lamps)");

  // A SPEC v3.1 field set exercising every field, n/a values and signs.
  SpecResult r;
  spec_result_clear(r);
  r.mode = SPEC_LOCKIN;
  r.flags = SPEC_F_VALID | SPEC_F_WHITE | SPEC_F_AGC | SPEC_F_ACTIVE | SPEC_F_SAT;
  r.gain_code = 2; r.int_cycles = 59; r.led_cur = 0x0B; r.n_frames = 8; r.lamps = SPEC_LAMP_WHITE;
  r.sat_mask = (1u << 3) | (1u << 17);
  r.valid_mask = kSpecAllChannels & ~(1u << 9);
  for (int i = 0; i < kSpecChannels; ++i) {
    r.spec[i] = (float)(0.37 * std::pow(3.1, i % 9) * ((i % 5 == 4) ? -1 : 1));
    r.unc[i] = (float)(0.0123 * (i + 1));
    r.raw[i] = (uint16_t)(1000 * i + 7);
  }
  r.spec[9] = NAN; r.unc[9] = NAN; r.raw[9] = 0xFFFF;
  r.spec[0] = 0.0f;
  r.temp[0] = 31; r.temp[1] = -5; r.temp[2] = -128;
  SpecAttitude att;
  att.valid = true;
  const float hy = 100.0f * 3.14159265f / 180.0f;     // yaw 200 deg, pitch 0, boresight body x
  att.q_body_to_world = Quat{std::cos(hy), 0, 0, std::sin(hy)};
  att.bore_body = Vec3{1, 0, 0};
  SpecV31 v;
  spec_fill_v31(r, att, SPEC_CLASS_OWN_LAMP, 100, 12.346f, v);
  uint8_t pkt[kSpecV31Len + 8];
  const size_t n = pack_spec_v31(v, pkt, sizeof pkt);
  CHECK(n == kSpecV31Len && n == 129, "SPEC v3.1 packs to 129 octets");
  CHECK(pack_spec_v31(v, pkt, kSpecV31Len - 1) == 0 && pack_spec_v31(v, nullptr, 200) == 0,
        "SPEC v3.1: short or null buffer -> 0, nothing written");
  CHECK(pkt[0] == SPEC_LOCKIN && pkt[1] == (r.flags | SPEC_F_ATTITUDE) && pkt[2] == 2 && pkt[3] == 59 &&
            pkt[4] == 0x0B && pkt[5] == 8, "SPEC v3.1 header octets (mode, flags + attitude, exposure)");
  CHECK(pkt[6] == 0x00 && pkt[7] == 0x02 && pkt[8] == 0x00 && pkt[9] == 0x08, "sat_mask big-endian at 6..9");
  CHECK(pkt[10] == 0 && pkt[11] == 0 && pkt[28] == 0xFF && pkt[29] == 0xFF && pkt[82] == 0 && pkt[83] == 7,
        "spec_log at 10 (0 -> 0x0000, NaN -> 0xFFFF at 410/645 nm), raw at 82");
  CHECK(pkt[118] == 31 && pkt[119] == 0xFB && pkt[120] == 0x80, "temperatures at 118..120 (-128 = n/a)");
  const int16_t zen = (int16_t)((pkt[121] << 8) | pkt[122]), az = (int16_t)((pkt[123] << 8) | pkt[124]);
  CHECK(zen == 9000 && az == -16000, "boresight zenith 90.00 deg; azimuth 200 deg sent as -160.00");
  CHECK(pkt[125] == SPEC_CLASS_OWN_LAMP && pkt[126] == 100 && pkt[127] == 0x04 && pkt[128] == 0xD3,
        "class, confidence, change 12.35 deg = 1235 cdeg");
  SpecAttitude no_att;
  SpecV31 v2;
  spec_fill_v31(r, no_att, 0, 0, NAN, v2);
  CHECK(v2.bore_zen_cdeg == 0x7FFF && v2.bore_az_cdeg == 0x7FFF && !(v2.flags & SPEC_F_ATTITUDE) &&
            v2.change_cdeg == 0xFFFF, "no attitude / no change -> n/a sentinels");

  SpecBurstSample s[4];
  for (int k = 0; k < 4; ++k) {
    s[k].dt_ms = (uint16_t)(k * 77);
    for (int i = 0; i < kSpecChannels; ++i) s[k].raw[i] = (uint16_t)(k * 100 + i);
  }
  s[1].raw[0] = 0xFFFF;                              // not converted in a 4-channel BANK mode
  uint8_t bp[kSpecBurstMaxLen];
  const size_t bn = pack_specburst(0x01020304, 123, 3, 1, s, 3, bp, sizeof bp);
  CHECK(bn == 123 && bp[0] == 1 && bp[3] == 4 && bp[4] == 0 && bp[5] == 123 && bp[6] == 3 && bp[7] == 1 &&
            bp[8] == 3 && bp[9 + 38] == 0 && bp[10 + 38] == 77 && bp[11 + 38] == 0xFF,
        "SPECBURST: 9 + 3 x 38 = 123 octets, header and samples big-endian");
  CHECK(pack_specburst(0, 0, 0, 1, s, 0, bp, sizeof bp) == 0 && pack_specburst(0, 0, 0, 1, s, 4, bp, 200) == 0 &&
            pack_specburst(0, 0, 0, 1, s, 3, bp, 122) == 0, "SPECBURST: n outside 1..3 or short buffer -> 0");

  // ---- vectors for the Python cross-check ----
  FILE* f = std::fopen((out + "/spectral_vectors.txt").c_str(), "w");
  if (!f) { CHECK(false, "cannot write spectral_vectors.txt"); return; }
  std::fprintf(f, "# AS7265X SPEC v3.1 vectors - tools/host_test/test_spectral.cpp (flight FP32 code)\n");
  const float lv[] = {0.0f, 1e-4f, -1e-4f, 9.99e-5f, -5e-5f, 1.0f, -1.0f, 0.5f, 2.0f, 3.14159f, 123.456f,
                      -987.654f, 1e3f, 4.2e5f, 4.3e5f, 1e9f, -1e9f, 6.789e-3f, 0.0123f, 55555.5f};
  for (float x : lv) std::fprintf(f, "log %.9g %04X\n", x, spec_log_encode(x));
  for (int i = 0; i < 200; ++i) {
    const float x = (float)(1e-4 * std::pow(10.0, (host_test::rnd() % 96300) / 10000.0)) *
                    ((host_test::rnd() & 1) ? -1.0f : 1.0f);
    std::fprintf(f, "log %.9g %04X\n", x, spec_log_encode(x));
  }
  const uint16_t dc[] = {0x0000, 0x0001, 0x8001, 0x0400, 0x7FFE, 0xFFFE, 0x8000, 0x1234, 0xFFFF};
  for (uint16_t code : dc) std::fprintf(f, "logdec %04X %.9g\n", code, spec_log_decode(code));
  std::fprintf(f, "spec31 %s\n", host_test::hex(pkt, n).c_str());
  std::fprintf(f, "field spec_mode %u %u\nfield flags %u %u\nfield gain_code %u %u\n", v.mode, v.mode,
               v.flags, v.flags, v.gain_code, v.gain_code);
  std::fprintf(f, "field int_cycles %u %u\nfield led_cur %u %u\nfield n_frames %u %u\nfield sat_mask %u %u\n",
               v.int_cycles, v.int_cycles, v.led_cur, v.led_cur, v.n_frames, v.n_frames,
               (unsigned)v.sat_mask, (unsigned)v.sat_mask);
  for (int i = 0; i < kSpecChannels; ++i)
    std::fprintf(f, "field spec_%unm %u %.9g\n", kSpecWavelengthNm[i], v.spec_log[i], spec_log_decode(v.spec_log[i]));
  for (int i = 0; i < kSpecChannels; ++i)
    std::fprintf(f, "field unc_%unm %u %.9g\n", kSpecWavelengthNm[i], v.unc_log[i], spec_log_decode(v.unc_log[i]));
  for (int i = 0; i < kSpecChannels; ++i)
    std::fprintf(f, "field raw_%unm %u %u\n", kSpecWavelengthNm[i], v.raw[i], v.raw[i]);
  for (int d = 0; d < kSpecDevices; ++d) std::fprintf(f, "field temp_%d %d %d\n", d, v.temp[d], v.temp[d]);
  std::fprintf(f, "field bore_zen %d %.9g\n", v.bore_zen_cdeg, v.bore_zen_cdeg / 100.0);
  std::fprintf(f, "field bore_az %d %.9g\n", v.bore_az_cdeg,
               v.bore_az_cdeg < 0 ? v.bore_az_cdeg / 100.0 + 360.0 : v.bore_az_cdeg / 100.0);
  std::fprintf(f, "field spec_class %u %u\nfield spec_conf %u %u\nfield spec_change %u %.9g\n", v.class_id,
               v.class_id, v.class_conf, v.class_conf, v.change_cdeg, v.change_cdeg / 100.0);
  std::fprintf(f, "specburst %s\n", host_test::hex(bp, bn).c_str());
  std::fclose(f);
  CHECK(true, "wrote spectral_vectors.txt");
}
