#include "payload.h"
#include <math.h>
#include "../config.h"

#if __has_include("../vendor/bme690/bme69x.h")
#define VGQ_HAVE_BME690 1
extern "C" {
#include "../vendor/bme690/bme69x.h"
}
#endif
#if __has_include(<SparkFun_AS7265X.h>)
#define VGQ_HAVE_AS7265X 1
#include <SparkFun_AS7265X.h>
#endif
#if __has_include(<SparkFun_AS7343.h>)
#define VGQ_HAVE_AS7343 1
#include <SparkFun_AS7343.h>
#endif
#if __has_include(<Arduino_NiclaSenseEnv.h>)
#define VGQ_HAVE_NICLA_ENV 1
#include <Arduino_NiclaSenseEnv.h>
#endif

namespace vgq {

// =============================================================================
// BME690 via Bosch SensorAPI
// =============================================================================
#ifdef VGQ_HAVE_BME690
namespace {
struct BmeCtx { I2cBus* bus; int8_t ch; uint8_t addr; };
BmeCtx g_bme_ctx;
struct bme69x_dev g_bme;
struct bme69x_conf g_bme_conf;
struct bme69x_heatr_conf g_bme_heatr;

BME69X_INTF_RET_TYPE bme_read(uint8_t reg, uint8_t* data, uint32_t len, void* intf) {
  BmeCtx* c = static_cast<BmeCtx*>(intf);
  if (!c->bus->select(c->ch)) return -1;
  return c->bus->read(c->addr, reg, data, len) ? BME69X_INTF_RET_SUCCESS : -1;
}
BME69X_INTF_RET_TYPE bme_write(uint8_t reg, const uint8_t* data, uint32_t len, void* intf) {
  BmeCtx* c = static_cast<BmeCtx*>(intf);
  if (!c->bus->select(c->ch)) return -1;
  return c->bus->write(c->addr, reg, data, len) ? BME69X_INTF_RET_SUCCESS : -1;
}
void bme_delay_us(uint32_t us, void*) {
  if (us >= 2000) delay(us / 1000); else delayMicroseconds(us);
}
}  // namespace
#endif

bool Bme690::compiled_in() {
#ifdef VGQ_HAVE_BME690
  return true;
#else
  return false;
#endif
}

bool Bme690::begin(I2cBus& bus, int8_t ch, uint8_t addr) {
  ok_ = false;
#ifdef VGQ_HAVE_BME690
  if (!bus.select(ch)) return false;
  if (!bus.probe(addr)) { addr = (addr == 0x76) ? 0x77 : 0x76; if (!bus.probe(addr)) return false; }
  g_bme_ctx = {&bus, ch, addr};
  g_bme.intf = BME69X_I2C_INTF;
  g_bme.intf_ptr = &g_bme_ctx;
  g_bme.read = bme_read;
  g_bme.write = bme_write;
  g_bme.delay_us = bme_delay_us;
  g_bme.amb_temp = 25;
  if (bme69x_init(&g_bme) != BME69X_OK) return false;
  g_bme_conf.filter = BME69X_FILTER_OFF;
  g_bme_conf.odr = BME69X_ODR_NONE;
  g_bme_conf.os_hum = BME69X_OS_1X;
  g_bme_conf.os_pres = BME69X_OS_16X;
  g_bme_conf.os_temp = BME69X_OS_2X;
  if (bme69x_set_conf(&g_bme_conf, &g_bme) != BME69X_OK) return false;
  g_bme_heatr.enable = BME69X_ENABLE;
  g_bme_heatr.heatr_temp = 300;     // degC
  g_bme_heatr.heatr_dur = 100;      // ms
  if (bme69x_set_heatr_conf(BME69X_FORCED_MODE, &g_bme_heatr, &g_bme) != BME69X_OK) return false;
  ok_ = true;
#else
  (void)bus; (void)ch; (void)addr;
#endif
  return ok_;
}

bool Bme690::read(Bme690Reading& r) {
#ifdef VGQ_HAVE_BME690
  if (!ok_) return false;
  if (bme69x_set_op_mode(BME69X_FORCED_MODE, &g_bme) != BME69X_OK) return false;
  const uint32_t us = bme69x_get_meas_dur(BME69X_FORCED_MODE, &g_bme_conf, &g_bme) +
                      (uint32_t)g_bme_heatr.heatr_dur * 1000UL;
  g_bme.delay_us(us, g_bme.intf_ptr);
  struct bme69x_data d;
  uint8_t n = 0;
  if (bme69x_get_data(BME69X_FORCED_MODE, &d, &n, &g_bme) != BME69X_OK || n == 0) return false;
  r.t_c = d.temperature;            // BME69X_USE_FPU (default): floating-point outputs
  r.p_pa = d.pressure;
  r.rh_pct = d.humidity;
  r.gas_ohm = d.gas_resistance;
  r.gas_valid = (d.status & BME69X_GASM_VALID_MSK) && (d.status & BME69X_HEAT_STAB_MSK);
  return true;
#else
  (void)r;
  return false;
#endif
}

// =============================================================================
// Spectrometers
// =============================================================================
#ifdef VGQ_HAVE_AS7265X
static AS7265X g_triad;
#endif
#ifdef VGQ_HAVE_AS7343
static SfeAS7343ArdI2C g_as7343;
#endif

void Spectrometers::begin(I2cBus& bus, int8_t ch_triad, int8_t ch_as7343) {
  bus_ = &bus; ch_t_ = ch_triad; ch_a_ = ch_as7343;
#ifdef VGQ_HAVE_AS7265X
  if (bus.select(ch_t_) && bus.probe(0x49) && g_triad.begin(bus.wire())) {
    g_triad.disableIndicator();
    g_triad.disableBulb(AS7265x_LED_WHITE);
    g_triad.disableBulb(AS7265x_LED_IR);
    g_triad.disableBulb(AS7265x_LED_UV);
    g_triad.setGain(AS7265X_GAIN_16X);
    g_triad.setIntegrationCycles(50);
    triad_ok_ = true;
  }
  bus.restore_clock();               // AS7265X::begin() calls Wire.begin() (resets the clock)
#endif
#ifdef VGQ_HAVE_AS7343
  if (bus.select(ch_a_) && bus.probe(0x39) && g_as7343.begin(kAS7343Addr, bus.wire())) {
    as7343_ok_ = g_as7343.powerOn() && g_as7343.setAutoSmux(AUTOSMUX_18_CHANNELS) &&
                 g_as7343.setAgain(AGAIN_64) && g_as7343.enableSpectralMeasurement();
  }
  bus.restore_clock();
#endif
}

void Spectrometers::read(SpecReading& r) {
  r.triad_ok = r.as7343_ok = false;
#ifdef VGQ_HAVE_AS7265X
  if (triad_ok_ && bus_->select(ch_t_)) {
    g_triad.takeMeasurements();      // one-shot, all 18 channels
    const float v[18] = {
        g_triad.getCalibratedA(), g_triad.getCalibratedB(), g_triad.getCalibratedC(),
        g_triad.getCalibratedD(), g_triad.getCalibratedE(), g_triad.getCalibratedF(),
        g_triad.getCalibratedG(), g_triad.getCalibratedH(), g_triad.getCalibratedR(),
        g_triad.getCalibratedI(), g_triad.getCalibratedS(), g_triad.getCalibratedJ(),
        g_triad.getCalibratedT(), g_triad.getCalibratedU(), g_triad.getCalibratedV(),
        g_triad.getCalibratedW(), g_triad.getCalibratedK(), g_triad.getCalibratedL()};
    for (int i = 0; i < 18; ++i) r.triad_uW_cm2[i] = v[i];
    r.triad_temp_c = (int8_t)lroundf(g_triad.getTemperatureAverage());
    r.triad_ok = true;
  }
#endif
#ifdef VGQ_HAVE_AS7343
  if (as7343_ok_ && bus_->select(ch_a_) && g_as7343.readSpectraDataFromSensor()) {
    for (int i = 0; i < 18; ++i)
      r.as7343_counts[i] = g_as7343.getChannelData((sfe_as7343_channel_t)i);
    r.as7343_ok = true;
  }
#endif
}

// =============================================================================
// Nicla Sense Env (Arduino firmware, I2C 0x21)
// =============================================================================
#ifdef VGQ_HAVE_NICLA_ENV
static NiclaSenseEnv* g_nenv = nullptr;
#endif

bool NiclaEnv::begin(I2cBus& bus, int8_t ch) {
  bus_ = &bus; ch_ = ch;
  ok_ = false;
#ifdef VGQ_HAVE_NICLA_ENV
  if (!bus.select(ch) || !bus.probe(0x21)) return false;
  if (!g_nenv) g_nenv = new NiclaSenseEnv(bus.wire());
  if (g_nenv->begin()) {
    g_nenv->indoorAirQualitySensor().setMode(IndoorAirQualitySensorMode::indoorAirQuality);
    g_nenv->outdoorAirQualitySensor().setMode(OutdoorAirQualitySensorMode::outdoorAirQuality);
    ok_ = true;
  }
  bus.restore_clock();               // I2CDevice::begin() calls bus.begin()
#endif
  return ok_;
}

bool NiclaEnv::read(NiclaEnvReading& r) {
#ifdef VGQ_HAVE_NICLA_ENV
  if (!ok_ || !bus_->select(ch_)) return false;
  auto& th = g_nenv->temperatureHumiditySensor();
  auto& ia = g_nenv->indoorAirQualitySensor();
  auto& oa = g_nenv->outdoorAirQualitySensor();
  r.t_c = th.temperature();
  r.rh = th.humidity();
  r.iaq = ia.airQuality();
  r.tvoc = ia.TVOC();
  r.eco2 = ia.CO2();
  r.aqi = oa.airQualityIndex();
  r.no2 = oa.NO2();
  r.o3 = oa.O3();
  return true;
#else
  (void)r;
  return false;
#endif
}

// =============================================================================
// Nicla Sense ME Attitude Reference Unit (custom firmware, I2C target 0x2A)
// =============================================================================
uint8_t NiclaAru::crc8(const uint8_t* d, size_t n) {
  uint8_t c = 0;
  for (size_t i = 0; i < n; ++i) {
    c ^= d[i];
    for (int b = 0; b < 8; ++b) c = (c & 0x80) ? (uint8_t)((c << 1) ^ 0x07) : (uint8_t)(c << 1);
  }
  return c;
}

bool NiclaAru::begin(I2cBus& bus, int8_t ch) {
  bus_ = &bus; ch_ = ch;
  ok_ = bus.select(ch) && bus.probe(kAddr);
  return ok_;
}

bool NiclaAru::read(AruReading& r) {
  if (!ok_ || !bus_->select(ch_)) return false;
  uint8_t b[kFrameLen];
  if (!bus_->read_raw(kAddr, b, kFrameLen)) return false;
  if (b[0] != 0xA7 || crc8(b, kFrameLen - 1) != b[kFrameLen - 1]) return false;
  auto i16 = [&](int o) { return (int16_t)((b[o] << 8) | b[o + 1]); };
  auto u16 = [&](int o) { return (uint16_t)((b[o] << 8) | b[o + 1]); };
  auto u32 = [&](int o) {
    return ((uint32_t)b[o] << 24) | ((uint32_t)b[o + 1] << 16) | ((uint32_t)b[o + 2] << 8) | b[o + 3];
  };
  r.seq = b[1]; r.flags = b[2]; r.quat_accuracy = b[3];
  r.qw = i16(4) / 16384.0f; r.qx = i16(6) / 16384.0f; r.qy = i16(8) / 16384.0f; r.qz = i16(10) / 16384.0f;
  r.p_hpa = u32(12) / 100.0f;
  r.t_c = i16(16) / 100.0f;
  r.rh = u16(18) / 100.0f;
  r.iaq = u16(20);
  r.co2eq = u32(22);
  r.bvoc_ppm = u16(26) / 100.0f;
  r.gas_ohm = u32(28);
  r.bsec_accuracy = b[32];
  // A frozen sequence counter means the Nicla firmware has stalled.
  stale_ = (r.seq == last_seq_) ? (uint8_t)(stale_ + 1) : 0;
  last_seq_ = r.seq;
  return stale_ < 5;
}

// =============================================================================
// Analog
// =============================================================================
void Analog::begin() { analogReadResolution(12); }

void Analog::read_css(uint16_t out[4]) {
  for (int i = 0; i < 4; ++i) out[i] = (uint16_t)analogRead(PIN_CSS[i]);
}

float Analog::pa_temp_c() {
  // Divider: 3.3 V -- 10k fixed -- node -- NTC (10k @ 25 degC, B = 3950) -- GND
  const int raw = analogRead(PIN_PA_NTC);
  if (raw < 20 || raw > 4075) return NAN;               // open / short -> not fitted
  const float r_ntc = 10000.0f * raw / (4095.0f - raw);
  const float inv_t = 1.0f / 298.15f + logf(r_ntc / 10000.0f) / 3950.0f;   // Beta equation
  return 1.0f / inv_t - 273.15f;
}

float Analog::vradio_v() {
  const float v = analogRead(PIN_VRADIO) * kAdcRef / 4095.0f * kVradioDivider;
  return v < 1.0f ? NAN : v;
}

}  // namespace vgq
