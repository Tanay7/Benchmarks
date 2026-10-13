// =============================================================================
//  AS7265X onboard spectral processing ("spectral intelligence") - pure C++.
//
//  Everything the instrument does with numbers, kept free of Arduino and I2C so
//  that tools/host_test verifies it on a PC (test_spectral.cpp):
//    * exposure arithmetic: full scale, integration time, saturation, AGC ladder
//    * HDR merge with a per-channel counting/dark uncertainty model
//    * lock-in (lamp-on minus lamp-off) statistics
//    * the SPEC v3.1 logarithmic "spectral compression" code (docs/05)
//    * a light-source classifier (HEURISTIC) and spectral change detection
//    * boresight zenith / azimuth from the attitude quaternion
//    * the SPEC v3.1 (APID 0x022) and SPECBURST (APID 0x024) field sets
//
//  Instrument facts - ams AS7265x datasheet v1-04 (2018-07-09):
//    Figure 30  integration register value N -> (N + 1) x 2.78 ms, maximum ADC
//               value min(65535, 1024 (N + 1) - 1) (the table; the same figure's
//               text "value x 2.8 ms" disagrees by one cycle; the table is used,
//               consistent with the ~89 Hz BANK 2 rate the SparkFun library's
//               max-rate example quotes for N = 1, i.e. 5.6 ms per bank - VERIFY
//               with T-SC-08). N = 0 reads back as "error", so commands use 1..255
//    Figure 29  GAIN codes 0..3 = 1x, 3.7x, 16x, 64x
//    Fig. 21/22 BANK modes: 0 and 1 convert 4 of the 6 photodiodes per device in
//               one integration time, 2 and 3 all six in two
//    Fig. 16-20 35 counts/(uW/cm2) typical at GAIN 16x, 166 ms (+/-12 %); dark
//               counts <= 5 at GAIN 64x, 165 ms, 25 degC; 20 nm FWHM; +/-10 nm
//  Units: spectral values are the chip's calibrated output in uW/cm2 (band
//  irradiance of one 20 nm channel); uncertainties are 1-sigma in uW/cm2.
//  APPROXIMATE: the calibrated output is ASSUMED independent of GAIN and integration
//  time. The datasheet calibrates at GAIN 16x / INT_T 166 ms (Figures 16-20, note 2) and
//  warns that "any operation other than these conditions might require a new
//  calibration" (General Description). AGC, HDR merges and change detection compare
//  values taken at other exposures on this assumption. VERIFY with T-SC-08 (docs/08):
//  steady diffuse source at all four gains and N = 29 / 59 / 127 / 255, calibrated value
//  per channel compared against the +/-12 % accuracy.
//  Channel order is wavelength order (index 0 = 410 nm ... 17 = 940 nm).
//  FP32 only (the STM32U585 FPU has no FP64); every loop has a fixed bound.
// =============================================================================
#pragma once
#include <stddef.h>
#include <stdint.h>
#include "attitude.h"

namespace vgq {

// ---- Channel geometry ------------------------------------------------------------
static const uint8_t kSpecChannels = 18;
static const uint8_t kSpecDevices = 3;          // 0 AS72651 (NIR), 1 AS72652 (VIS), 2 AS72653 (UV)
static const uint8_t kSpecSlots = 6;            // data slots per device (registers 0x08 + 2k)
static const uint32_t kSpecAllChannels = 0x3FFFFu;

// Centre wavelengths, nm: A410 B435 C460 D485 E510 F535 G560 H585 R610 I645 S680 J705
// T730 U760 V810 W860 K900 L940.
extern const uint16_t kSpecWavelengthNm[kSpecChannels];
// Wavelength-order channel index of device d, slot k (raw register 0x08 + 2k, calibrated
// register 0x14 + 4k): AS72651 = R S T U V W, AS72652 = G H I J K L, AS72653 = A B C D E F.
// This is the mapping of the SparkFun AS7265X library, proven on the SparkFun Triad board;
// the datasheet's per-register captions ("Channel R or J or D ...", Figures 33-44) are
// mutually inconsistent and are NOT used.
extern const uint8_t kSpecSlotChannel[kSpecDevices][kSpecSlots];
// Channel mask (bit i = wavelength index i) of the devices set in dev_mask (b0..b2).
uint32_t spec_device_channels(uint8_t dev_mask);
// Slots (bit k = slot k) that carry data in BANK mode 0 or 1 (datasheet "BANK Mode 0/1
// Registers"); any other bank mode returns 0x3F (all six).
uint8_t spec_bank_slots(uint8_t bank_mode, uint8_t device);

// ---- Exposure ------------------------------------------------------------------------
static const uint8_t kSpecGainAuto = 255;       // SPECCFG gain value selecting AGC
static const float kSpecCycleMs = 2.78f;        // one integration cycle (Figure 30)
struct SpecExposure {
  uint8_t gain_code;                            // 0..3
  uint8_t int_cycles;                           // integration register value 1..255
};
float spec_gain_factor(uint8_t gain_code);      // 1, 3.7, 16, 64 (code clamped to 0..3)
uint16_t spec_full_scale(uint8_t int_cycles);   // min(65535, 1024 (N + 1) - 1)
float spec_integration_ms(uint8_t int_cycles);  // (N + 1) x 2.78 ms
// Time from trigger to DATA_RDY: one integration for BANK 0/1, two for BANK 2/3; ms, rounded up.
uint32_t spec_conversion_ms(uint8_t int_cycles, uint8_t bank_mode);
// Relative exposure = gain factor x (N + 1); raw counts scale with it (linear model).
float spec_exposure_rel(const SpecExposure& e);
static const uint8_t kSpecHdrMaxFrames = 4;     // HDR bracket size limit (SPECMODE HDR n 2..4)
// Feasible exposure closest (in log ratio) to a relative exposure; ties go to the
// integration value closest to pref_cycles.
SpecExposure spec_exposure_nearest(float rel, uint8_t pref_cycles);
// HDR bracket: n = 2..4 exposures centred on `center`, spaced by factors of 4 in exposure
// (rel x 4^(k - (n-1)/2)), shortest first. Returns the count written to out[] (clamped
// ends may coincide and are written once, so the count can be below n).
uint8_t spec_hdr_bracket(const SpecExposure& center, uint8_t n,
                         SpecExposure out[kSpecHdrMaxFrames]);

// ---- Saturation and automatic exposure (AGC) ----------------------------------------
// Channels in `valid` whose raw count reached full scale - 1.
uint32_t spec_saturation_mask(const uint16_t raw[kSpecChannels], uint8_t int_cycles,
                              uint32_t valid);
// Saturation of one frame: every channel of the devices read (dev_mask), valid or not. A
// channel at full scale can come with an unusable calibrated value and so be invalid; judged
// on the valid channels only it was hidden from the AGC, which then kept the highest gain
// (record 2026-10-13-A, D-69).
uint32_t spec_frame_saturation(const uint16_t raw[kSpecChannels], uint8_t int_cycles,
                               uint8_t dev_mask);
uint16_t spec_peak(const uint16_t raw[kSpecChannels], uint32_t valid);
static const float kSpecAgcLow = 0.25f;         // accept window [25 %, 75 %] of full scale
static const float kSpecAgcHigh = 0.75f;
static const float kSpecAgcUpMax = 0.70f;       // an up-move must predict <= 70 %: hysteresis
enum SpecAgcResult : uint8_t {
  SPEC_AGC_SETTLED = 0,   // peak inside the window, or no rung would improve it
  SPEC_AGC_STEPPED = 1,   // `next` differs from `cur`
  SPEC_AGC_LIMIT = 2,     // end of the ladder in the required direction
};
// One AGC decision on the ladder "gain first, then integration": below the base
// integration value only 1x is used and the time halves; above it only 64x and the time
// doubles; in between the four gains. A saturated frame always steps down exactly one rung
// (its true level is unknown); otherwise the linear model predicts the peak on each rung.
SpecAgcResult spec_agc_step(const SpecExposure& cur, uint8_t base_cycles, uint16_t peak,
                            bool saturated, SpecExposure& next);

// ---- Uncertainty model ---------------------------------------------------------------
// sigma_counts^2 = q^2/12 + dark^2, q = 1 count, dark = 5 counts (datasheet maximum at
// 64x) x gain / 64. Dark charge also grows with integration time; the datasheet gives it
// only at 165 ms, so the model deliberately does not scale it with time. Photon shot
// noise and the +/-12 % calibration accuracy (a systematic, Figures 16-20) are NOT in it:
// LOCKIN replaces the model by the measured scatter, the ground adds the systematic.
float spec_sigma_counts(uint8_t gain_code);
// Datasheet typical responsivity (35 counts per uW/cm2 at 16x, 166 ms) scaled linearly with
// gain and integration time - APPROXIMATE, used only when cal/raw cannot be formed.
float spec_counts_per_uw(uint8_t gain_code, uint8_t int_cycles);
// 1-sigma of a calibrated value: sigma_counts x |cal / raw| (the channel's own conversion).
float spec_sigma_uw(float cal, uint16_t raw, uint8_t gain_code, uint8_t int_cycles);

// ---- Frames, HDR and lock-in ----------------------------------------------------------
struct SpecFrame {                                // one exposure of all present devices
  SpecExposure exp;
  uint8_t lamps;                                  // b0 white, b1 IR, b2 UV lit during it
  uint32_t valid;                                 // channels holding data
  uint16_t raw[kSpecChannels];
  float cal[kSpecChannels];                       // uW/cm2 (NaN where not valid)
};
static const float kSpecHdrMaxFrac = 0.90f;       // usable raw count < 90 % of full scale
// Per channel: the frame with the highest raw count below 90 % of its full scale (none:
// the frame with the lowest exposure). Outputs its calibrated value, the model sigma, its
// raw count and the index of the frame chosen; sat_mask = channels saturated even there.
// Channels not valid in every frame come out NaN / 0xFFFF. n is clamped to
// kSpecHdrMaxFrames; frames == nullptr or n == 0 gives an all-n/a result.
// The merged spectrum mixes channels from different exposures: APPROXIMATE, it rests on
// the gain/time-independence assumption stated in the file header (VERIFY, T-SC-08).
// raw[ch] is the count of the frame chosen for that channel, so its full scale is that
// frame's (spec_full_scale(frames[chosen[ch]].exp.int_cycles)), not the bracket centre's.
void spec_hdr_merge(const SpecFrame* frames, uint8_t n, float spec[kSpecChannels],
                    float unc[kSpecChannels], uint16_t raw[kSpecChannels], uint32_t& sat_mask,
                    uint8_t chosen[kSpecChannels]);

// Lock-in accumulator: Welford mean and variance of d = on - off per channel.
class SpecLockin {
 public:
  void reset();
  void add(const SpecFrame& on, const SpecFrame& off);
  uint8_t count() const { return n_; }
  uint32_t valid() const { return valid_; }
  // mean(on - off); unc = sample std / sqrt(n) for n >= 2, model_sigma for n = 1.
  void finish(const float model_sigma[kSpecChannels], float mean[kSpecChannels],
              float unc[kSpecChannels]) const;

 private:
  uint8_t n_ = 0;
  uint32_t valid_ = kSpecAllChannels;
  float mean_[kSpecChannels] = {0};
  float m2_[kSpecChannels] = {0};
};

// ---- Logarithmic spectral code (docs/05; same encoder in the ground's dssq/spectral.py) ---
// NaN -> 0xFFFF; s = (v < 0); a = |v|; a < 1e-4 -> m = 0; else
// m = min(32766, 1 + floor(1024 log2(a / 1e-4) + 0.5)); code = s << 15 | m.
// Decode: (s ? -1 : 1) x 1e-4 x 2^((m - 1) / 1024) for m >= 1, 0 for m = 0.
// Range 1e-4 .. 4.286e5 uW/cm2 (code 0x7FFE), step 0.068 % (worst-case error 0.034 %). The flight
// computes it in FP32: a Python (FP64) encoder may differ by one code exactly at a rounding
// boundary, so cross-checks compare decoded values (or codes within +/-1).
uint16_t spec_log_encode(float v);
float spec_log_decode(uint16_t code);

// ---- Light-source classifier (HEURISTIC) -----------------------------------------------
enum SpecClass : uint8_t {
  SPEC_CLASS_UNKNOWN = 0, SPEC_CLASS_DARK = 1, SPEC_CLASS_DAYLIGHT = 2,
  SPEC_CLASS_INCANDESCENT = 3,   // incandescent / halogen (thermal, NIR-rich)
  SPEC_CLASS_WHITE_LED = 4, SPEC_CLASS_FLUORESCENT = 5, SPEC_CLASS_NARROWBAND = 6,
  SPEC_CLASS_OWN_LAMP = 7,       // active (lamp-on minus lamp-off) spectrum
};
struct SpecFeatures {
  float total;        // sum of the 18 channels, uW/cm2 (negatives clipped to 0)
  float snr;          // total / sqrt(sum unc^2)
  float nir_vis;      // sum 730..940 nm / sum 410..705 nm
  float blue;         // 460 nm / 560 nm
  float flatness;     // geometric / arithmetic mean (spectral flatness, 1 = flat)
  float peak_mean;    // max / mean
  float line;         // min(435/460, 610/645): mercury / europium line index
};
// Every threshold of the classifier, in one table. They are HEURISTIC values chosen from
// synthetic blackbody, phosphor-LED, tri-phosphor fluorescent and single-LED spectra
// (see test_spectral.cpp); they are not measured and must be tuned against real sources
// (docs/08). A class is decided by a chain of comparisons; the confidence is the smallest
// log-ratio distance of any compared feature from its threshold, full scale at ln 2.
struct SpecClassThresholds {
  float dark_total_uw;       // DARK if total below this ...
  float dark_snr;            // ... or the total is not significant (snr below this)
  float narrow_peak_mean;    // NARROWBAND if max/mean >= this
  float low_nir_vis;         // LED / FLUORESCENT region: nir_vis < this
  float fluor_line;          // FLUORESCENT if line index >= this (else WHITE LED)
  float thermal_nir_vis;     // INCANDESCENT if nir_vis >= this ...
  float thermal_blue_max;    // ... and blue ratio < this
  float day_blue_min;        // DAYLIGHT if blue >= this, nir_vis in [low, thermal) ...
  float day_flatness_min;    // ... and flatness >= this
  float conf_full_ln;        // log margin giving 100 % confidence
};
extern const SpecClassThresholds kSpecClassThresholds;
// Returns false (features unusable) unless all 18 channels are valid and finite.
bool spec_features(const float spec[kSpecChannels], const float unc[kSpecChannels],
                   uint32_t valid, SpecFeatures& f);
// Class (SpecClass) and confidence 0..100. own_lamp forces OWN LAMP (confidence 100).
uint8_t spec_classify(const SpecFeatures& f, bool features_ok, bool own_lamp, uint8_t& conf,
                      const SpecClassThresholds& t = kSpecClassThresholds);

// ---- Change detection --------------------------------------------------------------------
// Spectral angle (deg, 0..90) between two non-negative spectra over `valid`; NaN when
// either has no signal. Negative values are clipped to 0 (angles of noise-level spectra
// are meaningless and are not computed: see SpecProcessor).
float spec_angle_deg(const float a[kSpecChannels], const float b[kSpecChannels], uint32_t valid);
// EWMA baseline of the normalised spectrum. update() returns the angle between the new
// spectrum and the baseline BEFORE folding it in (NaN for the first spectrum after reset).
class SpecChangeDetector {
 public:
  void reset() { have_ = false; }
  float update(const float spec[kSpecChannels], uint32_t valid, float alpha);

 private:
  bool have_ = false;
  uint32_t valid_ = 0;
  float base_[kSpecChannels] = {0};
};

// ---- Spectro-attitude fusion ------------------------------------------------------------
enum SpecWorld : uint8_t { SPEC_WORLD_NED = 0, SPEC_WORLD_ENU = 1 };
// q rotates BODY vectors into the world frame (v_w = q v_b q*, Hamilton, scalar first,
// any norm > 1e-3 - it is normalised here). Sources:
//   * Nicla Sense ME fusion (BHI260AP rotation vector, ARU page 0): device -> world with
//     world = ENU in the Android sensor convention the BHI260AP follows - VERIFY the
//     frame with T-SC-09 (point the boresight north / up and read bore_az / bore_zen);
//   * TRIAD fallback: triad_ned() (attitude.h) returns the opposite rotation
//     (NED -> body); pass spec_conj() of it with SPEC_WORLD_NED.
// zen_deg = angle from local vertical UP (0..180); az_deg = from north toward east,
// 0..360, NaN when the boresight is within 1e-4 rad of the vertical.
// Returns false if q or bore_body is degenerate or not finite.
bool spec_boresight(const Quat& q_body_to_world, uint8_t world, const Vec3& bore_body,
                    float& zen_deg, float& az_deg);
inline Quat spec_conj(const Quat& q) { return Quat{q.w, -q.x, -q.y, -q.z}; }

// ---- Acquisition configuration (SPECCFG 0x16 + SPECMODE 0x1A) ---------------------------
enum SpecMode : uint8_t { SPEC_SURVEY = 0, SPEC_HDR = 1, SPEC_LOCKIN = 2, SPEC_FLUOR = 3,
                          SPEC_BURST = 4 };
enum SpecLamp : uint8_t { SPEC_LAMP_WHITE = 1, SPEC_LAMP_IR = 2, SPEC_LAMP_UV = 4 };
struct SpecConfig {
  uint8_t mode = SPEC_SURVEY;
  uint8_t gain = kSpecGainAuto;   // 0..3, or 255 = AGC
  uint8_t int_cycles = 59;        // 60 x 2.78 = 166.8 ms: the datasheet calibration time
  uint8_t lamps = 0;              // b0 white (AS72651 LED_DRV), b1 IR (AS72652), b2 UV (AS72653)
  uint8_t led_cur = 0;            // LED_DRV codes: b1:0 white, b3:2 IR, b5:4 UV; 0..3 = 12.5,
                                  // 25, 50, 100 mA (Figure 32; current accuracy +/-10 %),
                                  // each at most kSpecLedMaxCode (white 0..3, IR 0..2, UV 0)
  uint8_t ind = 0;                // indicator LED (AS72651 LED_IND): 0 off, 1..4 = 1/2/4/8 mA
                                  // (+/-30 %); on while a cycle runs
  uint8_t n = 1;                  // HDR exposures 2..4, LOCKIN/FLUOR pairs 1..16, BURST s 1..60
  uint8_t burst_cycles = 1;       // BURST integration value (1 -> 5.56 ms per bank, Figure 30)
  uint8_t burst_bank = 2;         // BURST BANK mode: 2 = all 6 channels (2 integrations),
                                  // 0 / 1 = 4 channels per device in one integration
};
// Range check of every field, per mode (LOCKIN needs at least one lamp); led_cur must
// also respect kSpecLedMaxCode for every lamp.
bool spec_config_valid(const SpecConfig& c);
// Lamps the mode uses: FLUOR -> UV only; HDR and BURST -> none; else c.lamps. Lamp bit d
// (SpecLamp) is the LED_DRV of device d, so a lamp on an absent slave is masked with the
// device mask (DEV_SEL) by the driver.
uint8_t spec_effective_lamps(const SpecConfig& c);

// ---- Lamp drive limits ------------------------------------------------------------------
// Highest LED_DRV current code (0..3 = 12.5 / 25 / 50 / 100 mA, Figure 32) each board lamp
// may be driven at, indexed by device (= lamp bit). Source: the board vendor's limits in the
// SparkFun AS7265x Arduino library 1.0.5, examples/Example3_Settings.ino lines 65-81:
//   white LED (AS72651): "max forward current of 120mA", all four codes allowed  -> 3
//   IR LED    (AS72652): "max forward current of 65mA", 100 mA "Not allowed"      -> 2
//   UV LED    (AS72653): "max forward current of 30mA so do not set the drive
//                        current higher", 25 / 50 / 100 mA "Not allowed"          -> 0
// The datasheet's 12.5..100 mA (LED_DRV pin) is the driver's range, not the lamps' rating.
extern const uint8_t kSpecLedMaxCode[kSpecDevices];
// LED_DRV current code of device dev from a led_cur field (b1:0 white, b3:2 IR, b5:4 UV),
// clamped to kSpecLedMaxCode[dev]; 0 for dev >= kSpecDevices. Every LED_CONFIG value the
// driver writes takes its current from here, whatever led_cur holds.
uint8_t spec_led_code(uint8_t led_cur, uint8_t dev);
// led_cur rebuilt from the three clamped codes: the currents actually applied.
uint8_t spec_led_cur_applied(uint8_t led_cur);

// ---- AS72651 virtual-register write policy (datasheet Figures 26-51) ----------------------
// Kept here, free of I2C, so that the host tests check it; drivers/as7265x.cpp uses it for
// every byte it writes. Writable virtual registers the driver uses:
static const uint8_t kSpecVFwH = 0x02, kSpecVFwL = 0x03;    // FW version selector (Figure 28)
static const uint8_t kSpecVConfig = 0x04;                   // CONFIG (Figure 29)
static const uint8_t kSpecVInteg = 0x05;                    // INTEGRATION (Figure 30)
static const uint8_t kSpecVLed = 0x07;                      // LED_CONFIG (Figure 32)
static const uint8_t kSpecVDevSel = 0x4F;                   // DEV_SEL (Figure 48)
static const uint8_t kSpecVCoefRead = 0x54;                 // COEF_READ sub-address (Figure 50)
static const uint8_t kSpecCfgFrst = 0x01;                   // CONFIG b0 FRST (factory reset)
static const uint8_t kSpecLedDrvEn = 0x08, kSpecLedIndEn = 0x01;   // LED_CONFIG b3 / b0
// True if `data` may be written to `vreg`. Refused: every other register - COEF_DATA
// (0x50-0x53) and COEF_WRITE (0x55, writes "to persistent memory", Figure 51), the
// firmware-update block (0x48-0x4B, Figures 45-47) - CONFIG.FRST, INTEGRATION 0 (reads back
// as "error"), LED_CONFIG b7 (read-only), DEV_SEL above 2, FW selectors outside 1..3 and
// COEF_READ sub-addresses >= 64.
bool spec_vreg_write_allowed(uint8_t vreg, uint8_t data);
// Resynchronisation byte after a virtual WRITE to vreg whose outcome is unknown (any of its
// two transactions failed: the slave may or may not have latched the byte). The datasheet
// protocol has no cancel and STATUS reads the same (TX_VALID = 0) whether the slave is idle
// or waiting for the data byte, so the driver sends this one byte on its own before
// anything else. It has bit 7 clear and spec_vreg_write_allowed(vreg, byte) holds, so it
// is harmless either way: if the slave waits for data it is a legal value for vreg that
// lights nothing (LED_CONFIG = 0, CONFIG = 0 without SRST/FRST, INTEGRATION = 5, ...);
// if the slave is idle it is a READ request (bit 7 clear = read marker, "I2C Virtual
// Register Write Access") whose answer the driver drains. Either way the slave ends idle.
// A byte with bit 7 set would instead be a WRITE address in the idle case and turn the
// next byte into data for an arbitrary register (e.g. 0xD5 -> COEF_WRITE).
uint8_t spec_vreg_resync_byte(uint8_t vreg);

// ---- Product of one acquisition cycle ------------------------------------------------------
enum SpecFlag : uint8_t {
  SPEC_F_VALID = 0x01, SPEC_F_WHITE = 0x02, SPEC_F_IR = 0x04, SPEC_F_UV = 0x08,
  SPEC_F_AGC = 0x10, SPEC_F_ACTIVE = 0x20, SPEC_F_SAT = 0x40, SPEC_F_ATTITUDE = 0x80,
};
struct SpecResult {
  uint8_t mode = SPEC_SURVEY;
  uint8_t flags = 0;              // SPEC v3.1 b0..b6 (b7 is set by spec_fill_v31)
  uint8_t gain_code = 0;
  // Base exposure. HDR: the AGC centre of the bracket, which need not be one of the
  // exposures taken (for an even n it lies between two of them, spec_hdr_bracket), and
  // raw[] then holds counts of different exposures - see spec_hdr_merge.
  uint8_t int_cycles = 0;
  uint8_t led_cur = 0;            // LED_DRV codes applied (spec_led_cur_applied)
  uint8_t n_frames = 0;           // exposures merged / on-off pairs / burst samples (<= 255)
  uint8_t lamps = 0;              // lamps actually driven (lamps on absent slaves excluded)
  uint8_t dev_mask = 0;           // devices that delivered data (b0..b2)
  uint32_t valid_mask = 0;        // channels with data
  uint32_t sat_mask = 0;          // bit i = channel i saturated
  float spec[kSpecChannels];      // uW/cm2 (BURST: NaN - raw only)
  float unc[kSpecChannels];       // 1-sigma uW/cm2
  uint16_t raw[kSpecChannels];    // raw counts of the frame the value came from (BURST: mean)
  int8_t temp[kSpecDevices];      // die temperature, degC (-128 = n/a)
  uint32_t t_start_ms = 0, t_end_ms = 0;   // millis() at cycle start / end
  uint16_t burst_samples = 0;
  uint16_t burst_rate_dhz = 0;    // achieved sample rate, 0.1 Hz
  uint16_t burst_dropped = 0;     // samples lost because the ring was full
  uint16_t burst_read_max_ms = 0; // longest sample read (DATA_RDY seen -> last channel read)
};
void spec_result_clear(SpecResult& r);   // all channels NaN / 0xFFFF / -128, counters 0

// ---- Telemetry field sets ---------------------------------------------------------------
// SPEC v3.1 (APID 0x022, VC1), 129 octets (docs/05). bore_az_cdeg is transmitted
// wrapped to (-180, 180] deg because 0..360 deg in 0.01 deg does not fit an i16: the ground
// adds 360 to negative values.
struct SpecV31 {
  uint8_t mode;
  uint8_t flags;
  uint8_t gain_code;
  uint8_t int_cycles;
  uint8_t led_cur;
  uint8_t n_frames;
  uint32_t sat_mask;
  uint16_t spec_log[kSpecChannels];   // spec_log_encode(uW/cm2)
  uint16_t unc_log[kSpecChannels];    // spec_log_encode(1-sigma uW/cm2)
  uint16_t raw[kSpecChannels];
  int8_t temp[kSpecDevices];          // degC, -128 = n/a
  int16_t bore_zen_cdeg;              // 0.01 deg, 0x7FFF = n/a
  int16_t bore_az_cdeg;               // 0.01 deg, (-180, 180], 0x7FFF = n/a
  uint8_t class_id;                   // SpecClass
  uint8_t class_conf;                 // %
  uint16_t change_cdeg;               // 0.01 deg, 0xFFFF = n/a
};
static const size_t kSpecV31Len = 129;
// Serialises the user-data field big-endian. Returns kSpecV31Len, or 0 (nothing written)
// if out is null or cap < kSpecV31Len.
size_t pack_spec_v31(const SpecV31& p, uint8_t* out, size_t cap);

// SPECBURST (APID 0x024, VC1), contract 28 (supersedes 13.5; Phase B spec PB-1.12, C-03):
//   t0_ms u32 | rate_dhz u16 | gain_code u8 | int_cycles u8 | n u8 (1..3) |
//   encoding u8 (0 raw, 1 CCSDS 121.0-B-3) | dt_ms u16[n] | payload
//   encoding 0: n x 18 raw u16, TIME-MAJOR (sample 0 channels 0..17, then sample 1 ...),
//               10 + 38 n octets (<= 124);
//   encoding 1: rice_encode(kRiceMission, cm, 18 n) of the CHANNEL-MAJOR samples
//               cm[ch * n + t] = s[t].raw[ch] (each channel's time series contiguous, so
//               the unit-delay predictor sees temporal correlation), 1 .. 36 n - 1 octets
//               (zero fill to an octet): strictly shorter than raw by construction.
// The ground decodes encoding 1 as time_major(rice.decode(payload, 18 n), 18).
// The channels of one sample are NOT simultaneous: dt_ms stamps the DATA_RDY detection and
// the values are then read one virtual register at a time (BANK 2: 3 DEV_SEL writes and 36
// single-octet reads, each a four-step handshake of several I2C transactions), so at the
// 100 kHz bus clock one sample read lasts longer than one conversion at short integration
// times and its channels come from successive conversions (datasheet "Interrupt Operation":
// only the two octets of one value are shadow-protected). SpecResult.burst_read_max_ms
// reports the longest read of the burst.
struct SpecBurstSample {
  uint16_t dt_ms;                     // DATA_RDY detection, since the first sample of the burst
  uint16_t raw[kSpecChannels];        // spec_burst_raw(); 0xFFFF = not converted in this BANK mode
};
// SPECBURST raw value of a converted channel. 0xFFFF is reserved for "not converted", so a
// real count of 65535 (reachable when int_cycles >= 63: full scale 65535, Figure 30) is sent
// as 0xFFFE - at or above full scale - 1, i.e. saturated by spec_saturation_mask's rule.
uint16_t spec_burst_raw(uint16_t counts);
static const uint8_t kSpecBurstPerPacket = 3;
static const size_t kSpecBurstHdrLen = 10;                       // incl. the encoding octet
static const size_t kSpecBurstMaxLen = kSpecBurstHdrLen + 38 * kSpecBurstPerPacket;   // 124
enum SpecBurstEncoding : uint8_t { SPEC_BURST_RAW = 0, SPEC_BURST_RICE = 1 };
// Contract 28 layout (PB-1.12). Tries CCSDS 121.0 (kRiceMission) on the channel-major
// samples into cap 36 n - 1; RICE_OK -> encoding 1, else raw. rice_status = the attempt's
// ccsds::RiceStatus (RICE_OK or RICE_ERR_OVERFLOW are normal; anything else: the
// integrator raises EVR 0x0704 "BURST rice status %u"). Returns the length, or 0
// (nothing valid written, rice_status = RICE_ERR_ARG: no attempt) for n outside 1..3 /
// s or out null / cap < 10 + 38 n.
size_t pack_specburst(uint32_t t0_ms, uint16_t rate_dhz, uint8_t gain_code, uint8_t int_cycles,
                      const SpecBurstSample* s, uint8_t n, uint8_t* out, size_t cap,
                      uint8_t& rice_status);

// ---- Onboard processing pipeline ----------------------------------------------------------
struct SpecAttitude {
  bool valid = false;
  Quat q_body_to_world{1, 0, 0, 0};
  uint8_t world = SPEC_WORLD_NED;
  Vec3 bore_body{0, 0, 1};            // instrument boresight in body axes (kSpecBoresightBody)
};
struct SpecTuning {
  float baseline_alpha = 0.1f;        // kSpecBaselineAlpha: EWMA weight of the newest spectrum
  uint16_t event_cdeg = 500;          // kSpecEventCdeg: spectral-event threshold, 5.00 deg
  uint32_t event_min_gap_s = 60;      // EVR 0x0701 rate limit
  uint8_t class_persist = 2;          // consecutive cycles before a class change is reported
};
struct SpecEvents {
  bool spectral_event = false;        // EVR 0x0701
  uint16_t angle_cdeg = 0xFFFF;
  bool class_changed = false;         // EVR 0x0702
  uint8_t old_class = 0, new_class = 0, conf = 0;
};
// Fills every SPEC v3.1 field from a result plus the classifier / change outputs.
void spec_fill_v31(const SpecResult& r, const SpecAttitude& att, uint8_t class_id,
                   uint8_t class_conf, float change_deg, SpecV31& out);

class SpecProcessor {
 public:
  explicit SpecProcessor(const SpecTuning& t = SpecTuning()) : tun_(t) {}
  // SPECBASE: threshold 0 only resets the baseline, 1..9000 also sets the threshold.
  // Returns false (nothing changed) for a value above 9000.
  bool set_baseline_cmd(uint16_t threshold_cdeg);
  uint16_t event_threshold_cdeg() const { return tun_.event_cdeg; }
  // Classifies, runs change detection (baseline reset automatically when the mode, lamps
  // or valid channels change), computes the boresight and fills `out`. now_s = SCLK s.
  SpecEvents process(const SpecResult& r, const SpecAttitude& att, uint32_t now_s, SpecV31& out);

 private:
  SpecTuning tun_;
  SpecChangeDetector det_;
  uint16_t signature_ = 0xFFFF;       // mode | lamps | active flag of the baseline
  bool have_event_ = false;
  uint32_t last_event_s_ = 0;
  uint8_t reported_ = SPEC_CLASS_UNKNOWN, candidate_ = SPEC_CLASS_UNKNOWN, cand_count_ = 0;
};

}  // namespace vgq
