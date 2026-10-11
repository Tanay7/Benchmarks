#include "spectral.h"
#include <math.h>
#include <string.h>

namespace vgq {

const uint16_t kSpecWavelengthNm[kSpecChannels] = {410, 435, 460, 485, 510, 535, 560, 585, 610,
                                                   645, 680, 705, 730, 760, 810, 860, 900, 940};
const uint8_t kSpecSlotChannel[kSpecDevices][kSpecSlots] = {
    {8, 10, 12, 13, 14, 15},   // AS72651 (NIR): R610 S680 T730 U760 V810 W860
    {6, 7, 9, 11, 16, 17},     // AS72652 (VIS): G560 H585 I645 J705 K900 L940
    {0, 1, 2, 3, 4, 5},        // AS72653 (UV) : A410 B435 C460 D485 E510 F535
};

// HEURISTIC thresholds - see SpecClassThresholds and the synthetic spectra in
// test_spectral.cpp. Feature values of those spectra (20 nm Gaussian channels):
//   2856 K blackbody  nir/vis 1.30  blue 0.38  flat 0.76  peak/mean 1.9
//   6500 K blackbody  nir/vis 0.27  blue 1.12  flat 0.95  peak/mean 1.3
//   phosphor white LED nir/vis <0.03 line 0.68 peak/mean 2.9-3.3
//   tri-phosphor fluorescent nir/vis 0.06 line 4.0 peak/mean 4.1
//   single LED (525 / 630 / 850 nm)   peak/mean 9.7 / 11.8 / 16.4
const SpecClassThresholds kSpecClassThresholds = {
    0.5f,      // dark_total_uw    : sum of 18 bands below 0.5 uW/cm2 -> DARK
    5.0f,      // dark_snr         : total below 5 sigma of its own noise -> DARK
    6.0f,      // narrow_peak_mean
    0.15f,     // low_nir_vis
    1.5f,      // fluor_line
    0.70f,     // thermal_nir_vis
    0.70f,     // thermal_blue_max
    0.70f,     // day_blue_min
    0.60f,     // day_flatness_min
    0.6931472f // conf_full_ln = ln 2
};

namespace {

const float kGain[4] = {1.0f, 3.7f, 16.0f, 64.0f};   // datasheet Figure 29
const float kNaN = NAN;
const float kRadToDeg = 57.29577951f;
const uint8_t kAgcMaxRungs = 24;     // > the 8 + 3 + 8 rungs of the longest ladder

inline uint8_t gain_clamp(uint8_t g) { return g > 3 ? 3 : g; }
inline uint8_t cycles_clamp(uint8_t c) { return c == 0 ? 1 : c; }

// Sensitivity relative to full scale: raw/full-scale grows with gain and, above 64
// integration cycles, with time (below that the full scale grows with time too: 1024
// counts per 2.78 ms cycle, Figure 30). Saturation decisions use this, not gain x time.
float frac_gain(const SpecExposure& e) {
  const float t = (float)((uint16_t)cycles_clamp(e.int_cycles) + 1u) / 64.0f;
  return kGain[gain_clamp(e.gain_code)] * (t > 1.0f ? t : 1.0f);
}

bool same(const SpecExposure& a, const SpecExposure& b) {
  return a.gain_code == b.gain_code && a.int_cycles == b.int_cycles;
}

// One rung up / down the AGC ladder; false at its end.
bool agc_up(SpecExposure& e, uint8_t base) {
  const uint16_t c = e.int_cycles;
  if (c < base) { e.int_cycles = (uint8_t)(2u * c < base ? 2u * c : base); return true; }
  if (e.gain_code < 3) { ++e.gain_code; return true; }
  if (c < 255) { e.int_cycles = (uint8_t)(2u * c < 255u ? 2u * c : 255u); return true; }
  return false;
}
bool agc_down(SpecExposure& e, uint8_t base) {
  const uint8_t c = e.int_cycles;
  if (e.gain_code == 3 && c > base) { e.int_cycles = (uint8_t)(c / 2 > base ? c / 2 : base); return true; }
  if (e.gain_code > 0) { --e.gain_code; return true; }
  // At 1x only integrations above 63 cycles lower raw/full-scale (see frac_gain).
  if (c > 63) { e.int_cycles = (uint8_t)(c / 2 > 63 ? c / 2 : 63); return true; }
  return false;
}

// |ln(x / thr)| with both arguments kept positive.
float log_margin(float x, float thr) {
  const float a = x > 1e-30f ? x : 1e-30f;
  return fabsf(logf(a / thr));
}

inline float fmin2(float a, float b) { return a < b ? a : b; }

int16_t clamp_cdeg(float deg) {
  const float v = floorf(deg * 100.0f + 0.5f);
  if (!(v > -32767.0f)) return -32767;
  if (!(v < 32766.0f)) return 32766;
  return (int16_t)v;
}

// Minimal big-endian serializer (same convention as tlm_packets.cpp).
struct W {
  uint8_t* p;
  size_t n = 0;
  explicit W(uint8_t* out) : p(out) {}
  void u8(uint8_t v) { p[n++] = v; }
  void i8(int8_t v) { p[n++] = (uint8_t)v; }
  void u16(uint16_t v) { p[n++] = (uint8_t)(v >> 8); p[n++] = (uint8_t)v; }
  void i16(int16_t v) { u16((uint16_t)v); }
  void u32(uint32_t v) { u16((uint16_t)(v >> 16)); u16((uint16_t)v); }
};

}  // namespace

// =============================================================================
// Channel geometry and exposure arithmetic
// =============================================================================
uint32_t spec_device_channels(uint8_t dev_mask) {
  uint32_t m = 0;
  for (uint8_t d = 0; d < kSpecDevices; ++d) {
    if (!(dev_mask & (1u << d))) continue;
    for (uint8_t k = 0; k < kSpecSlots; ++k) m |= 1u << kSpecSlotChannel[d][k];
  }
  return m;
}

uint8_t spec_bank_slots(uint8_t bank_mode, uint8_t device) {
  // Datasheet "BANK Mode 0/1 Registers", slots in register order (AS72651 R S T U V W,
  // AS72652 G H I J K L, AS72653 A B C D E F):
  //   mode 0: AS72651 S T U V, AS72652 G H I K, AS72653 A B C E
  //   mode 1: AS72651 R T U W, AS72652 G H J L, AS72653 A B D F
  static const uint8_t kSlots[2][kSpecDevices] = {{0x1E, 0x17, 0x17}, {0x2D, 0x2B, 0x2B}};
  if (bank_mode > 1 || device >= kSpecDevices) return 0x3F;
  return kSlots[bank_mode][device];
}

float spec_gain_factor(uint8_t gain_code) { return kGain[gain_clamp(gain_code)]; }

uint16_t spec_full_scale(uint8_t int_cycles) {
  const uint32_t v = 1024u * ((uint32_t)int_cycles + 1u) - 1u;
  return (uint16_t)(v > 65535u ? 65535u : v);
}

float spec_integration_ms(uint8_t int_cycles) {
  return (float)((uint16_t)int_cycles + 1u) * kSpecCycleMs;
}

uint32_t spec_conversion_ms(uint8_t int_cycles, uint8_t bank_mode) {
  const float n = bank_mode <= 1 ? 1.0f : 2.0f;
  return (uint32_t)ceilf(n * spec_integration_ms(int_cycles));
}

float spec_exposure_rel(const SpecExposure& e) {
  return kGain[gain_clamp(e.gain_code)] * (float)((uint16_t)cycles_clamp(e.int_cycles) + 1u);
}

// Works on the full-scale-relative sensitivity (frac_gain): that is what moves a
// channel away from saturation, which is the purpose of an HDR bracket. Among the four
// gains, candidates within kNearTol (log ratio, ~22 %) of the best match compete on the
// integration time closest to pref_cycles, so bracket frames take similar time.
SpecExposure spec_exposure_nearest(float rel, uint8_t pref_cycles) {
  const float kNearTol = 0.2f;
  const uint8_t pref = cycles_clamp(pref_cycles);
  SpecExposure cand[4];
  float err[4];
  float best_err = 1e30f;
  for (uint8_t g = 0; g < 4; ++g) {
    const float m = (rel > 0.0f && isfinite(rel)) ? rel / kGain[g] : 1.0f;
    uint8_t c;
    if (m <= 1.0f) {
      c = pref > 63 ? 63 : pref;                       // any N <= 63 gives frac = gain
    } else {
      float cf = floorf(64.0f * m + 0.5f) - 1.0f;      // (N + 1) / 64 = m
      if (cf < 63.0f) cf = 63.0f;
      if (cf > 255.0f) cf = 255.0f;
      c = (uint8_t)cf;
    }
    cand[g] = SpecExposure{g, c};
    err[g] = (rel > 0.0f && isfinite(rel)) ? log_margin(frac_gain(cand[g]), rel) : (float)g;
    if (err[g] < best_err) best_err = err[g];
  }
  uint8_t pick = 0;
  float pick_tie = 1e30f, pick_err = 1e30f;
  for (uint8_t g = 0; g < 4; ++g) {
    if (err[g] > best_err + kNearTol) continue;
    const float tie = log_margin((float)cand[g].int_cycles + 1.0f, (float)pref + 1.0f);
    if (tie < pick_tie - 1e-6f || (fabsf(tie - pick_tie) <= 1e-6f && err[g] < pick_err)) {
      pick = g; pick_tie = tie; pick_err = err[g];
    }
  }
  return cand[pick];
}

uint8_t spec_hdr_bracket(const SpecExposure& center, uint8_t n,
                         SpecExposure out[kSpecHdrMaxFrames]) {
  if (n < 2) n = 2;
  if (n > kSpecHdrMaxFrames) n = kSpecHdrMaxFrames;
  const SpecExposure c{gain_clamp(center.gain_code), cycles_clamp(center.int_cycles)};
  const float f0 = frac_gain(c);
  uint8_t count = 0;
  for (uint8_t k = 0; k < n; ++k) {
    const float step = 2.0f * ((float)k - 0.5f * (float)(n - 1));   // 4^(k - (n-1)/2)
    const SpecExposure e = spec_exposure_nearest(f0 * exp2f(step), c.int_cycles);
    bool dup = false;
    for (uint8_t j = 0; j < count; ++j) dup = dup || same(out[j], e);
    if (!dup) out[count++] = e;                      // clamped ends may coincide
  }
  return count;
}

// =============================================================================
// Saturation and AGC
// =============================================================================
uint32_t spec_saturation_mask(const uint16_t raw[kSpecChannels], uint8_t int_cycles,
                              uint32_t valid) {
  const uint16_t thr = (uint16_t)(spec_full_scale(int_cycles) - 1u);
  uint32_t m = 0;
  for (uint8_t i = 0; i < kSpecChannels; ++i)
    if ((valid & (1u << i)) && raw[i] >= thr) m |= 1u << i;
  return m;
}

uint16_t spec_peak(const uint16_t raw[kSpecChannels], uint32_t valid) {
  uint16_t p = 0;
  for (uint8_t i = 0; i < kSpecChannels; ++i)
    if ((valid & (1u << i)) && raw[i] > p) p = raw[i];
  return p;
}

SpecAgcResult spec_agc_step(const SpecExposure& cur, uint8_t base_cycles, uint16_t peak,
                            bool saturated, SpecExposure& next) {
  const uint8_t base = cycles_clamp(base_cycles);
  const SpecExposure c0{gain_clamp(cur.gain_code), cycles_clamp(cur.int_cycles)};
  next = c0;
  const float g0 = frac_gain(c0);
  if (saturated) {
    // Exactly one EFFECTIVE rung down (rungs that leave raw/full-scale unchanged are
    // passed over): the true level of a saturated channel is unknown.
    SpecExposure t = c0;
    for (uint8_t i = 0; i < kAgcMaxRungs; ++i) {
      if (!agc_down(t, base)) break;
      if (frac_gain(t) < g0 * 0.999f) { next = t; return SPEC_AGC_STEPPED; }
    }
    return SPEC_AGC_LIMIT;
  }
  const float f0 = (float)peak / (float)spec_full_scale(c0.int_cycles);
  if (f0 >= kSpecAgcLow && f0 <= kSpecAgcHigh) return SPEC_AGC_SETTLED;
  bool moved = false;
  if (f0 < kSpecAgcLow) {
    for (uint8_t i = 0; i < kAgcMaxRungs; ++i) {
      SpecExposure t = next;
      if (!agc_up(t, base)) return moved ? SPEC_AGC_STEPPED : SPEC_AGC_LIMIT;
      if (f0 * frac_gain(t) / g0 > kSpecAgcUpMax) break;   // would overshoot: stop here
      next = t;
      moved = true;
    }
    return moved ? SPEC_AGC_STEPPED : SPEC_AGC_SETTLED;      // no rung lands in the window
  }
  for (uint8_t i = 0; i < kAgcMaxRungs; ++i) {               // too bright, not saturated
    SpecExposure t = next;
    if (!agc_down(t, base)) break;
    next = t;
    moved = true;
    if (f0 * frac_gain(t) / g0 <= kSpecAgcUpMax) break;
  }
  return moved ? SPEC_AGC_STEPPED : SPEC_AGC_LIMIT;
}

// =============================================================================
// Uncertainty model
// =============================================================================
float spec_sigma_counts(uint8_t gain_code) {
  const float dark = 5.0f * kGain[gain_clamp(gain_code)] / 64.0f;
  return sqrtf(1.0f / 12.0f + dark * dark);
}

float spec_counts_per_uw(uint8_t gain_code, uint8_t int_cycles) {
  return 35.0f * (kGain[gain_clamp(gain_code)] / 16.0f) * (spec_integration_ms(int_cycles) / 166.0f);
}

float spec_sigma_uw(float cal, uint16_t raw, uint8_t gain_code, uint8_t int_cycles) {
  const float sc = spec_sigma_counts(gain_code);
  if (raw > 0 && isfinite(cal)) {
    const float ratio = fabsf(cal / (float)raw);
    if (ratio > 0.0f && isfinite(ratio)) return sc * ratio;
  }
  return sc / spec_counts_per_uw(gain_code, int_cycles);
}

// =============================================================================
// HDR and lock-in
// =============================================================================
void spec_hdr_merge(const SpecFrame* frames, uint8_t n, float spec[kSpecChannels],
                    float unc[kSpecChannels], uint16_t raw[kSpecChannels], uint32_t& sat_mask,
                    uint8_t chosen[kSpecChannels]) {
  sat_mask = 0;
  if (n > kSpecHdrMaxFrames) n = kSpecHdrMaxFrames;
  for (uint8_t ch = 0; ch < kSpecChannels; ++ch) {
    spec[ch] = kNaN; unc[ch] = kNaN; raw[ch] = 0xFFFF; chosen[ch] = 0xFF;
    if (!frames || n == 0) continue;
    const uint32_t bit = 1u << ch;
    bool all_valid = true;
    for (uint8_t k = 0; k < n; ++k) all_valid = all_valid && (frames[k].valid & bit);
    if (!all_valid) continue;
    int best = -1, lowest = 0;
    uint16_t best_raw = 0;
    for (uint8_t k = 0; k < n; ++k) {
      const uint16_t r = frames[k].raw[ch];
      const float usable = kSpecHdrMaxFrac * (float)spec_full_scale(frames[k].exp.int_cycles);
      if ((float)r < usable && (best < 0 || r > best_raw)) { best = k; best_raw = r; }
      if (frac_gain(frames[k].exp) < frac_gain(frames[lowest].exp)) lowest = k;
    }
    if (best < 0) best = lowest;                     // saturated or near it everywhere
    const SpecFrame& f = frames[best];
    spec[ch] = f.cal[ch];
    raw[ch] = f.raw[ch];
    unc[ch] = spec_sigma_uw(f.cal[ch], f.raw[ch], f.exp.gain_code, f.exp.int_cycles);
    chosen[ch] = (uint8_t)best;
    if (f.raw[ch] >= (uint16_t)(spec_full_scale(f.exp.int_cycles) - 1u)) sat_mask |= bit;
  }
}

void SpecLockin::reset() {
  n_ = 0;
  valid_ = kSpecAllChannels;
  memset(mean_, 0, sizeof mean_);
  memset(m2_, 0, sizeof m2_);
}

void SpecLockin::add(const SpecFrame& on, const SpecFrame& off) {
  if (n_ == 255) return;
  ++n_;
  valid_ &= on.valid & off.valid;
  const float inv = 1.0f / (float)n_;
  for (uint8_t ch = 0; ch < kSpecChannels; ++ch) {
    if (!(valid_ & (1u << ch))) continue;
    const float d = on.cal[ch] - off.cal[ch];
    if (!isfinite(d)) { valid_ &= ~(1u << ch); continue; }
    const float delta = d - mean_[ch];
    mean_[ch] += delta * inv;
    m2_[ch] += delta * (d - mean_[ch]);              // Welford: no catastrophic cancellation
  }
}

void SpecLockin::finish(const float model_sigma[kSpecChannels], float mean[kSpecChannels],
                        float unc[kSpecChannels]) const {
  for (uint8_t ch = 0; ch < kSpecChannels; ++ch) {
    if (n_ == 0 || !(valid_ & (1u << ch))) { mean[ch] = kNaN; unc[ch] = kNaN; continue; }
    mean[ch] = mean_[ch];
    if (n_ >= 2) {
      const float var = m2_[ch] / (float)(n_ - 1);
      unc[ch] = sqrtf(var > 0.0f ? var : 0.0f) / sqrtf((float)n_);
    } else {
      unc[ch] = model_sigma[ch];
    }
  }
}

// =============================================================================
// Logarithmic spectral code
// =============================================================================
uint16_t spec_log_encode(float v) {
  if (isnan(v)) return 0xFFFF;
  const uint16_t s = v < 0.0f ? 0x8000 : 0;
  const float a = fabsf(v);
  if (!(a >= 1e-4f)) return s;
  const float x = 1.0f + 1024.0f * log2f(a / 1e-4f);
  if (!(x < 32766.0f)) return (uint16_t)(s | 32766u);        // also +/-infinity
  return (uint16_t)(s | (uint16_t)floorf(x + 0.5f));
}

float spec_log_decode(uint16_t code) {
  if (code == 0xFFFF) return kNaN;
  const uint16_t m = code & 0x7FFF;
  if (m == 0) return 0.0f;
  const float a = 1e-4f * exp2f((float)(m - 1) / 1024.0f);
  return (code & 0x8000) ? -a : a;
}

// =============================================================================
// Classifier
// =============================================================================
bool spec_features(const float spec[kSpecChannels], const float unc[kSpecChannels],
                   uint32_t valid, SpecFeatures& f) {
  f = SpecFeatures{0, 0, 0, 0, 0, 0, 0};
  float c[kSpecChannels];
  float sum = 0, vis = 0, nir = 0, mx = 0, var = 0;
  bool complete = true;
  for (uint8_t i = 0; i < kSpecChannels; ++i) {
    const float v = spec[i];
    if (!(valid & (1u << i)) || !isfinite(v)) { c[i] = 0; complete = false; continue; }
    c[i] = v > 0.0f ? v : 0.0f;
    sum += c[i];
    if (i < 12) vis += c[i]; else nir += c[i];       // 410..705 nm | 730..940 nm
    if (c[i] > mx) mx = c[i];
    if (isfinite(unc[i])) var += unc[i] * unc[i];
  }
  f.total = sum;
  f.snr = var > 0.0f ? sum / sqrtf(var) : (sum > 0.0f ? 1e6f : 0.0f);
  if (!complete || !(sum > 0.0f)) return false;
  const float mean = sum / (float)kSpecChannels;
  f.nir_vis = vis > 0.0f ? nir / vis : 1e6f;
  f.blue = c[6] > 0.0f ? c[2] / c[6] : 1e6f;                  // 460 / 560 nm
  const float eps = 1e-6f * mx;
  float logsum = 0;
  for (uint8_t i = 0; i < kSpecChannels; ++i) logsum += logf(c[i] + eps);
  f.flatness = expf(logsum / (float)kSpecChannels) / mean;
  f.peak_mean = mx / mean;
  const float l1 = c[2] > 0.0f ? c[1] / c[2] : 1e6f;          // 435 / 460: Hg 436 nm line
  const float l2 = c[9] > 0.0f ? c[8] / c[9] : 1e6f;          // 610 / 645: Eu 611 nm line
  f.line = fmin2(l1, l2);
  return true;
}

uint8_t spec_classify(const SpecFeatures& f, bool features_ok, bool own_lamp, uint8_t& conf,
                      const SpecClassThresholds& t) {
  conf = 0;
  if (own_lamp) { conf = 100; return SPEC_CLASS_OWN_LAMP; }
  const bool dark_total = f.total < t.dark_total_uw, dark_snr = f.snr < t.dark_snr;
  float m = 0.0f;                                     // smallest margin on the decision path
  uint8_t cls = SPEC_CLASS_UNKNOWN;
  if (dark_total || dark_snr) {
    const float m1 = dark_total ? log_margin(f.total, t.dark_total_uw) : 0.0f;
    const float m2 = dark_snr ? log_margin(f.snr, t.dark_snr) : 0.0f;
    m = m1 > m2 ? m1 : m2;                            // either condition decides DARK
    cls = SPEC_CLASS_DARK;
  } else {
    if (!features_ok) return SPEC_CLASS_UNKNOWN;
    m = fmin2(log_margin(f.total, t.dark_total_uw), log_margin(f.snr, t.dark_snr));
    m = fmin2(m, log_margin(f.peak_mean, t.narrow_peak_mean));
    if (f.peak_mean >= t.narrow_peak_mean) {
      cls = SPEC_CLASS_NARROWBAND;
    } else {
      m = fmin2(m, log_margin(f.nir_vis, t.low_nir_vis));
      if (f.nir_vis < t.low_nir_vis) {                 // no NIR: LED or discharge lamp
        m = fmin2(m, log_margin(f.line, t.fluor_line));
        cls = f.line >= t.fluor_line ? SPEC_CLASS_FLUORESCENT : SPEC_CLASS_WHITE_LED;
      } else {
        m = fmin2(m, log_margin(f.nir_vis, t.thermal_nir_vis));
        if (f.nir_vis >= t.thermal_nir_vis) {         // thermal emitter: NIR-rich, little blue
          if (f.blue < t.thermal_blue_max) {
            m = fmin2(m, log_margin(f.blue, t.thermal_blue_max));
            cls = SPEC_CLASS_INCANDESCENT;
          }
        } else if (f.blue >= t.day_blue_min && f.flatness >= t.day_flatness_min) {
          m = fmin2(m, fmin2(log_margin(f.blue, t.day_blue_min),
                             log_margin(f.flatness, t.day_flatness_min)));
          cls = SPEC_CLASS_DAYLIGHT;
        }
      }
    }
  }
  if (cls == SPEC_CLASS_UNKNOWN) return cls;
  const float q = m / t.conf_full_ln;
  conf = (uint8_t)floorf(100.0f * (q < 1.0f ? q : 1.0f) + 0.5f);
  return cls;
}

// =============================================================================
// Change detection
// =============================================================================
// Unit vector of the clipped spectrum over `valid`; false if it has no signal.
static bool unit_spectrum(const float s[kSpecChannels], uint32_t valid, float u[kSpecChannels]) {
  float ss = 0;
  for (uint8_t i = 0; i < kSpecChannels; ++i) {
    const float v = s[i];
    u[i] = ((valid & (1u << i)) && isfinite(v) && v > 0.0f) ? v : 0.0f;
    ss += u[i] * u[i];
  }
  if (!(ss > 0.0f) || !isfinite(ss)) return false;
  const float inv = 1.0f / sqrtf(ss);
  for (uint8_t i = 0; i < kSpecChannels; ++i) u[i] *= inv;
  return true;
}

// 2 atan2(|u - v|, |u + v|): accurate for small and large angles alike (acos is not).
static float unit_angle_deg(const float u[kSpecChannels], const float v[kSpecChannels]) {
  float dm = 0, dp = 0;
  for (uint8_t i = 0; i < kSpecChannels; ++i) {
    dm += (u[i] - v[i]) * (u[i] - v[i]);
    dp += (u[i] + v[i]) * (u[i] + v[i]);
  }
  return 2.0f * atan2f(sqrtf(dm), sqrtf(dp)) * kRadToDeg;
}

float spec_angle_deg(const float a[kSpecChannels], const float b[kSpecChannels], uint32_t valid) {
  float ua[kSpecChannels], ub[kSpecChannels];
  if (!unit_spectrum(a, valid, ua) || !unit_spectrum(b, valid, ub)) return kNaN;
  return unit_angle_deg(ua, ub);
}

float SpecChangeDetector::update(const float spec[kSpecChannels], uint32_t valid, float alpha) {
  float u[kSpecChannels];
  if (!unit_spectrum(spec, valid, u)) return kNaN;
  if (!have_ || valid != valid_) {
    memcpy(base_, u, sizeof base_);
    valid_ = valid;
    have_ = true;
    return kNaN;
  }
  const float angle = unit_angle_deg(u, base_);
  const float a = (alpha > 0.0f && alpha <= 1.0f) ? alpha : 0.1f;
  float nb[kSpecChannels];
  for (uint8_t i = 0; i < kSpecChannels; ++i) nb[i] = (1.0f - a) * base_[i] + a * u[i];
  if (unit_spectrum(nb, valid, base_)) return angle;
  memcpy(base_, u, sizeof base_);                    // cannot happen for non-negative inputs
  return angle;
}

// =============================================================================
// Boresight
// =============================================================================
bool spec_boresight(const Quat& q, uint8_t world, const Vec3& bore_body, float& zen_deg,
                    float& az_deg) {
  zen_deg = kNaN;
  az_deg = kNaN;
  const float qn = sqrtf(q.w * q.w + q.x * q.x + q.y * q.y + q.z * q.z);
  const float bn = v_norm(bore_body);
  if (!(qn > 1e-3f) || !isfinite(qn) || !(bn > 1e-6f) || !isfinite(bn)) return false;
  const float w = q.w / qn;
  const Vec3 u{q.x / qn, q.y / qn, q.z / qn};
  const Vec3 v{bore_body.x / bn, bore_body.y / bn, bore_body.z / bn};
  // v' = v + w t + u x t, t = 2 u x v  (rotation of v by the unit quaternion)
  const Vec3 t0 = v_cross(u, v);
  const Vec3 t{2.0f * t0.x, 2.0f * t0.y, 2.0f * t0.z};
  const Vec3 ut = v_cross(u, t);
  const Vec3 r{v.x + w * t.x + ut.x, v.y + w * t.y + ut.y, v.z + w * t.z + ut.z};
  float up, north, east;
  if (world == SPEC_WORLD_ENU) { up = r.z; north = r.y; east = r.x; }
  else { up = -r.z; north = r.x; east = r.y; }        // NED
  const float h = sqrtf(north * north + east * east);
  zen_deg = atan2f(h, up) * kRadToDeg;                 // 0 = straight up, 180 = straight down
  if (h > 1e-4f) {
    float az = atan2f(east, north) * kRadToDeg;
    if (az < 0.0f) az += 360.0f;
    if (az >= 360.0f) az -= 360.0f;
    az_deg = az;
  }
  return true;
}

// =============================================================================
// Configuration and results
// =============================================================================
bool spec_config_valid(const SpecConfig& c) {
  if (c.mode > SPEC_BURST) return false;
  if (c.gain > 3 && c.gain != kSpecGainAuto) return false;
  if (c.int_cycles == 0 || c.lamps > 7 || c.led_cur > 63 || c.ind > 4) return false;
  if (c.burst_cycles == 0 || c.burst_bank > 2) return false;
  switch (c.mode) {
    case SPEC_HDR: return c.n >= 2 && c.n <= 4;
    case SPEC_LOCKIN: return c.n >= 1 && c.n <= 16 && (c.lamps & 7) != 0;
    case SPEC_FLUOR: return c.n >= 1 && c.n <= 16;
    case SPEC_BURST: return c.n >= 1 && c.n <= 60;
    default: return c.n >= 1 && c.n <= 60;             // SURVEY: n not used
  }
}

uint8_t spec_effective_lamps(const SpecConfig& c) {
  switch (c.mode) {
    case SPEC_FLUOR: return SPEC_LAMP_UV;
    case SPEC_HDR:
    case SPEC_BURST: return 0;
    default: return (uint8_t)(c.lamps & 7);
  }
}

void spec_result_clear(SpecResult& r) {
  r = SpecResult();
  for (uint8_t i = 0; i < kSpecChannels; ++i) { r.spec[i] = kNaN; r.unc[i] = kNaN; r.raw[i] = 0xFFFF; }
  for (uint8_t d = 0; d < kSpecDevices; ++d) r.temp[d] = -128;
}

// =============================================================================
// Telemetry field sets
// =============================================================================
void spec_fill_v31(const SpecResult& r, const SpecAttitude& att, uint8_t class_id,
                   uint8_t class_conf, float change_deg, SpecV31& out) {
  out.mode = r.mode;
  out.flags = (uint8_t)(r.flags & 0x7F);
  out.gain_code = r.gain_code;
  out.int_cycles = r.int_cycles;
  out.led_cur = r.led_cur;
  out.n_frames = r.n_frames;
  out.sat_mask = r.sat_mask & kSpecAllChannels;
  for (uint8_t i = 0; i < kSpecChannels; ++i) {
    out.spec_log[i] = spec_log_encode(r.spec[i]);
    out.unc_log[i] = spec_log_encode(r.unc[i]);
    out.raw[i] = r.raw[i];
  }
  for (uint8_t d = 0; d < kSpecDevices; ++d) out.temp[d] = r.temp[d];
  out.bore_zen_cdeg = 0x7FFF;
  out.bore_az_cdeg = 0x7FFF;
  float zen, az;
  if (att.valid && spec_boresight(att.q_body_to_world, att.world, att.bore_body, zen, az)) {
    out.flags |= SPEC_F_ATTITUDE;
    out.bore_zen_cdeg = clamp_cdeg(zen);
    if (!isnan(az)) out.bore_az_cdeg = clamp_cdeg(az > 180.0f ? az - 360.0f : az);
  }
  out.class_id = class_id;
  out.class_conf = class_conf > 100 ? 100 : class_conf;
  if (isnan(change_deg) || change_deg < 0.0f) {
    out.change_cdeg = 0xFFFF;
  } else {
    const float v = floorf(change_deg * 100.0f + 0.5f);
    out.change_cdeg = (uint16_t)(v < 65534.0f ? v : 65534.0f);
  }
}

size_t pack_spec_v31(const SpecV31& p, uint8_t* out, size_t cap) {
  if (!out || cap < kSpecV31Len) return 0;
  W w(out);
  w.u8(p.mode); w.u8(p.flags); w.u8(p.gain_code); w.u8(p.int_cycles); w.u8(p.led_cur);
  w.u8(p.n_frames); w.u32(p.sat_mask);
  for (uint8_t i = 0; i < kSpecChannels; ++i) w.u16(p.spec_log[i]);
  for (uint8_t i = 0; i < kSpecChannels; ++i) w.u16(p.unc_log[i]);
  for (uint8_t i = 0; i < kSpecChannels; ++i) w.u16(p.raw[i]);
  for (uint8_t d = 0; d < kSpecDevices; ++d) w.i8(p.temp[d]);
  w.i16(p.bore_zen_cdeg); w.i16(p.bore_az_cdeg);
  w.u8(p.class_id); w.u8(p.class_conf); w.u16(p.change_cdeg);
  return w.n;
}

size_t pack_specburst(uint32_t t0_ms, uint16_t rate_dhz, uint8_t gain_code, uint8_t int_cycles,
                      const SpecBurstSample* s, uint8_t n, uint8_t* out, size_t cap) {
  if (!s || !out || n == 0 || n > kSpecBurstPerPacket || cap < 9u + 38u * n) return 0;
  W w(out);
  w.u32(t0_ms); w.u16(rate_dhz); w.u8(gain_code); w.u8(int_cycles); w.u8(n);
  for (uint8_t k = 0; k < n; ++k) {
    w.u16(s[k].dt_ms);
    for (uint8_t i = 0; i < kSpecChannels; ++i) w.u16(s[k].raw[i]);
  }
  return w.n;
}

// =============================================================================
// Processing pipeline
// =============================================================================
bool SpecProcessor::set_baseline_cmd(uint16_t threshold_cdeg) {
  if (threshold_cdeg > 9000) return false;
  det_.reset();
  if (threshold_cdeg) tun_.event_cdeg = threshold_cdeg;
  return true;
}

SpecEvents SpecProcessor::process(const SpecResult& r, const SpecAttitude& att, uint32_t now_s,
                                  SpecV31& out) {
  SpecEvents ev;
  ev.old_class = ev.new_class = reported_;
  if (!(r.flags & SPEC_F_VALID) || r.mode == SPEC_BURST) {   // nothing to classify
    spec_fill_v31(r, att, SPEC_CLASS_UNKNOWN, 0, kNaN, out);
    return ev;
  }
  SpecFeatures f;
  const bool complete = spec_features(r.spec, r.unc, r.valid_mask, f);
  uint8_t conf = 0;
  const uint8_t cls = spec_classify(f, complete, (r.flags & SPEC_F_ACTIVE) != 0, conf);
  ev.new_class = cls;
  ev.conf = conf;
  // A new class is reported only after it persisted for class_persist cycles.
  if (cls == reported_) {
    cand_count_ = 0;
  } else {
    if (cls != candidate_) { candidate_ = cls; cand_count_ = 0; }
    if (cand_count_ < 255) ++cand_count_;
    if (cand_count_ >= (tun_.class_persist ? tun_.class_persist : 1)) {
      ev.class_changed = true;
      ev.old_class = reported_;
      reported_ = cls;
      cand_count_ = 0;
    }
  }
  // Spectra of different modes / lamps are not comparable: restart the baseline.
  const uint16_t sig = (uint16_t)(r.mode | (r.lamps << 3) | ((r.flags & SPEC_F_ACTIVE) ? 0x40 : 0));
  if (sig != signature_) { det_.reset(); signature_ = sig; }
  float angle = kNaN;
  const bool significant = f.total >= kSpecClassThresholds.dark_total_uw &&
                           f.snr >= kSpecClassThresholds.dark_snr;
  if (significant) angle = det_.update(r.spec, r.valid_mask, tun_.baseline_alpha);
  if (!isnan(angle)) {
    const float cd = floorf(angle * 100.0f + 0.5f);
    ev.angle_cdeg = (uint16_t)(cd < 65534.0f ? cd : 65534.0f);
    if (tun_.event_cdeg && ev.angle_cdeg > tun_.event_cdeg &&
        (!have_event_ || now_s - last_event_s_ >= tun_.event_min_gap_s)) {
      ev.spectral_event = true;
      have_event_ = true;
      last_event_s_ = now_s;
    }
  }
  spec_fill_v31(r, att, cls, conf, angle, out);
  return ev;
}

}  // namespace vgq
