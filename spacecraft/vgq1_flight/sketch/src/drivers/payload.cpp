#include "payload.h"
#include <math.h>
#include "../config.h"

#if __has_include(<SparkFun_AS7265X.h>)
#define VGQ_HAVE_AS7265X 1
#include <SparkFun_AS7265X.h>
#endif
#if __has_include(<Arduino_NiclaSenseEnv.h>)
#define VGQ_HAVE_NICLA_ENV 1
#include <Arduino_NiclaSenseEnv.h>
#endif

namespace vgq {

// =============================================================================
// AS7265X (SparkFun Spectral Triad)
// =============================================================================
#ifdef VGQ_HAVE_AS7265X
static AS7265X g_triad;
#endif

bool Spectrometer::begin(I2cBus& bus) {
  bus_ = &bus;
  ok_ = false;
#ifdef VGQ_HAVE_AS7265X
  if (bus.probe(kAddr) && g_triad.begin(bus.wire())) {
    g_triad.disableIndicator();
    ok_ = configure(gain_, cycles_, 0);
  }
  bus.restore_clock();                       // AS7265X::begin() calls Wire.begin() (resets the clock)
#endif
  return ok_;
}

bool Spectrometer::configure(uint8_t gain_code, uint8_t int_cycles, uint8_t lamps) {
#ifdef VGQ_HAVE_AS7265X
  if (!bus_ || int_cycles == 0) return false;
  gain_ = gain_code & 3;                     // AS7265X_GAIN_1X / 37X / 16X / 64X = 0..3
  cycles_ = int_cycles;
  lamps_ = lamps & 7;
  g_triad.setGain(gain_);
  g_triad.setIntegrationCycles(cycles_);
  g_triad.disableBulb(AS7265x_LED_WHITE);    // lamps are lit only during a measurement
  g_triad.disableBulb(AS7265x_LED_IR);
  g_triad.disableBulb(AS7265x_LED_UV);
  return true;
#else
  (void)gain_code; (void)int_cycles; (void)lamps;
  return false;
#endif
}

bool Spectrometer::read(SpecReading& r) {
  r.ok = false;
#ifdef VGQ_HAVE_AS7265X
  if (!ok_) return false;
  if (lamps_ & 1) g_triad.enableBulb(AS7265x_LED_WHITE);   // active (illuminated) spectroscopy
  if (lamps_ & 2) g_triad.enableBulb(AS7265x_LED_IR);
  if (lamps_ & 4) g_triad.enableBulb(AS7265x_LED_UV);
  g_triad.takeMeasurements();                // one-shot, all 18 channels
  if (lamps_ & 1) g_triad.disableBulb(AS7265x_LED_WHITE);
  if (lamps_ & 2) g_triad.disableBulb(AS7265x_LED_IR);
  if (lamps_ & 4) g_triad.disableBulb(AS7265x_LED_UV);
  // SparkFun channel letters in wavelength order:
  // A410 B435 C460 D485 E510 F535 G560 H585 R610 I645 S680 J705 T730 U760 V810 W860 K900 L940
  const float c[18] = {
      g_triad.getCalibratedA(), g_triad.getCalibratedB(), g_triad.getCalibratedC(),
      g_triad.getCalibratedD(), g_triad.getCalibratedE(), g_triad.getCalibratedF(),
      g_triad.getCalibratedG(), g_triad.getCalibratedH(), g_triad.getCalibratedR(),
      g_triad.getCalibratedI(), g_triad.getCalibratedS(), g_triad.getCalibratedJ(),
      g_triad.getCalibratedT(), g_triad.getCalibratedU(), g_triad.getCalibratedV(),
      g_triad.getCalibratedW(), g_triad.getCalibratedK(), g_triad.getCalibratedL()};
  const uint16_t w[18] = {
      g_triad.getA(), g_triad.getB(), g_triad.getC(), g_triad.getD(), g_triad.getE(),
      g_triad.getF(), g_triad.getG(), g_triad.getH(), g_triad.getR(), g_triad.getI(),
      g_triad.getS(), g_triad.getJ(), g_triad.getT(), g_triad.getU(), g_triad.getV(),
      g_triad.getW(), g_triad.getK(), g_triad.getL()};
  for (int i = 0; i < 18; ++i) { r.cal_uW_cm2[i] = c[i]; r.raw[i] = w[i]; }
  for (uint8_t d = 0; d < 3; ++d) r.temp_c[d] = (int8_t)g_triad.getTemperature(d);
  r.ok = true;
#endif
  return r.ok;
}

// =============================================================================
// Nicla Sense Env (Arduino firmware, I2C 0x21)
// =============================================================================
#ifdef VGQ_HAVE_NICLA_ENV
static NiclaSenseEnv* g_nenv = nullptr;
#endif

bool NiclaEnv::begin(I2cBus& bus) {
  bus_ = &bus;
  ok_ = false;
#ifdef VGQ_HAVE_NICLA_ENV
  if (!bus.probe(kAddr)) return false;
  if (!g_nenv) g_nenv = new NiclaSenseEnv(bus.wire());
  if (g_nenv->begin()) {
    g_nenv->indoorAirQualitySensor().setMode(IndoorAirQualitySensorMode::indoorAirQuality);
    g_nenv->outdoorAirQualitySensor().setMode(OutdoorAirQualitySensorMode::outdoorAirQuality);
    ok_ = true;
  }
  bus.restore_clock();                       // I2CDevice::begin() calls bus.begin()
#endif
  return ok_;
}

bool NiclaEnv::read(NiclaEnvReading& r) {
#ifdef VGQ_HAVE_NICLA_ENV
  if (!ok_) return false;
  auto& th = g_nenv->temperatureHumiditySensor();
  auto& ia = g_nenv->indoorAirQualitySensor();
  auto& oa = g_nenv->outdoorAirQualitySensor();
  r.flags = 0;
  r.t_c = th.temperature();
  r.rh = th.humidity();
  if (!isnan(r.t_c)) r.flags |= 1;
  r.iaq = ia.airQuality();
  r.tvoc = ia.TVOC();
  r.eco2 = ia.CO2();
  if (!isnan(r.iaq)) r.flags |= 2;
  r.aqi = oa.airQualityIndex();
  r.no2 = oa.NO2();
  r.o3 = oa.O3();
  if (!isnan(r.no2)) r.flags |= 4;
  return true;
#else
  (void)r;
  return false;
#endif
}

// =============================================================================
// Nicla Sense ME attitude reference unit (custom firmware, I2C target 0x2A)
// =============================================================================
uint8_t NiclaAru::crc8(const uint8_t* d, size_t n) {
  uint8_t c = 0;
  for (size_t i = 0; i < n; ++i) {
    c ^= d[i];
    for (int b = 0; b < 8; ++b) c = (c & 0x80) ? (uint8_t)((c << 1) ^ 0x07) : (uint8_t)(c << 1);
  }
  return c;
}

bool NiclaAru::begin(I2cBus& bus) {
  bus_ = &bus;
  ok_ = bus.probe(kAddr);
  return ok_;
}

bool NiclaAru::page(uint8_t n, uint8_t magic, uint8_t* b) {
  if (!ok_ || !bus_->write_raw(kAddr, &n, 1)) return false;   // select the page
  if (!bus_->read_raw(kAddr, b, kPageLen)) return false;
  return b[0] == magic && crc8(b, kPageLen - 1) == b[kPageLen - 1];
}

static inline int16_t be_i16(const uint8_t* p) { return (int16_t)((p[0] << 8) | p[1]); }
static inline uint16_t be_u16(const uint8_t* p) { return (uint16_t)((p[0] << 8) | p[1]); }
static inline uint32_t be_u32(const uint8_t* p) {
  return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | p[3];
}

bool NiclaAru::read_motion(AruReading& r) {
  uint8_t b[kPageLen];
  if (!page(0, 0xA7, b)) return false;
  const float q = 1.0f / 16384.0f;
  r.seq = b[1];
  r.motion_flags = b[2];
  r.qw = be_i16(b + 3) * q; r.qx = be_i16(b + 5) * q; r.qy = be_i16(b + 7) * q; r.qz = be_i16(b + 9) * q;
  r.quat_acc_mrad = be_u16(b + 11);
  for (int i = 0; i < 3; ++i) {
    r.acc_mg[i] = be_i16(b + 13 + 2 * i);
    r.gyro_ddps[i] = be_i16(b + 19 + 2 * i);
    r.mag_raw[i] = be_i16(b + 25 + 2 * i);
  }
  // A frozen sequence counter means the Nicla firmware has stalled.
  stale_ = (r.seq == last_seq_) ? (uint8_t)(stale_ < 255 ? stale_ + 1 : 255) : 0;
  last_seq_ = r.seq;
  return stale_ < 5;
}

bool NiclaAru::read_env(AruReading& r) {
  uint8_t b[kPageLen];
  if (!page(1, 0xA8, b)) return false;
  r.env_flags = b[2];
  r.p_hpa = be_u32(b + 3) / 100.0f;
  r.t_c = be_i16(b + 7) / 100.0f;
  r.rh = be_u16(b + 9) / 100.0f;
  r.iaq = be_u16(b + 11);
  r.iaq_s = be_u16(b + 13);
  r.co2eq = be_u32(b + 15);
  r.bvoc_ppm = be_u16(b + 19) / 100.0f;
  r.gas_ohm = be_u32(b + 21);
  r.bsec_accuracy = b[25];
  return true;
}

}  // namespace vgq
