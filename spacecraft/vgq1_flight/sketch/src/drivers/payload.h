// =============================================================================
//  Science payload & auxiliary instruments that use vendor libraries or custom
//  protocols. Every vendor dependency is optional at COMPILE time
//  (__has_include): if a library is missing the instrument reports "absent"
//  in HK instead of breaking the build.
//
//    BME690            Bosch BME690_SensorAPI (BSD-3), fetched into src/vendor/bme690
//                      by tools/fetch_vendor_libs.sh
//    AS7265x           "SparkFun Spectral Triad AS7265X" Arduino library
//    AS7343            "SparkFun AS7343" Arduino library (+ SparkFun Toolkit)
//    Nicla Sense Env   "Arduino_NiclaSenseEnv" library (board runs Arduino's firmware)
//    Nicla Sense ME    custom firmware: spacecraft/nicla_sense_me_aru (I2C target 0x2A)
// =============================================================================
#pragma once
#include <Arduino.h>
#include "i2c_bus.h"

namespace vgq {

struct Bme690Reading { float t_c, p_pa, rh_pct, gas_ohm; bool gas_valid; };
class Bme690 {
 public:
  bool begin(I2cBus& bus, int8_t ch, uint8_t addr = 0x76);
  bool read(Bme690Reading& r);       // forced mode, blocks ~ 200 ms (heater cycle)
  bool ok() const { return ok_; }
  static bool compiled_in();
 private:
  bool ok_ = false;
};

struct SpecReading {
  bool triad_ok = false, as7343_ok = false;
  float triad_uW_cm2[18];
  int8_t triad_temp_c = 0;
  uint16_t as7343_counts[18];
};
class Spectrometers {
 public:
  void begin(I2cBus& bus, int8_t ch_triad, int8_t ch_as7343);
  void read(SpecReading& r);         // blocks up to ~ 0.5 s (integration)
  bool triad_ok() const { return triad_ok_; }
  bool as7343_ok() const { return as7343_ok_; }
  uint8_t triad_gain_code() const { return 2; }      // 16x
  uint8_t triad_int_cycles() const { return 50; }    // 50 x 2.8 ms
  uint8_t as7343_gain_code() const { return 7; }     // AGAIN_64
 private:
  I2cBus* bus_ = nullptr;
  int8_t ch_t_ = -1, ch_a_ = -1;
  bool triad_ok_ = false, as7343_ok_ = false;
};

struct NiclaEnvReading {
  float t_c = NAN, rh = NAN, iaq = NAN, tvoc = NAN, eco2 = NAN, no2 = NAN, o3 = NAN;
  int aqi = -1;
};
class NiclaEnv {
 public:
  bool begin(I2cBus& bus, int8_t ch);
  bool read(NiclaEnvReading& r);
  bool ok() const { return ok_; }
 private:
  I2cBus* bus_ = nullptr;
  int8_t ch_ = -1;
  bool ok_ = false;
};

// Nicla Sense ME "Attitude Reference Unit" frame (36 octets, big-endian, CRC-8)
struct AruReading {
  uint8_t seq = 0, flags = 0, quat_accuracy = 0, bsec_accuracy = 0;
  float qw = 1, qx = 0, qy = 0, qz = 0;
  float p_hpa = NAN, t_c = NAN, rh = NAN, bvoc_ppm = NAN;
  uint16_t iaq = 0;
  uint32_t co2eq = 0, gas_ohm = 0;
};
class NiclaAru {
 public:
  static const uint8_t kAddr = 0x2A;
  static const size_t kFrameLen = 36;
  bool begin(I2cBus& bus, int8_t ch);
  bool read(AruReading& r);
  bool ok() const { return ok_; }
  static uint8_t crc8(const uint8_t* d, size_t n);   // poly 0x07, init 0x00
 private:
  I2cBus* bus_ = nullptr;
  int8_t ch_ = -1;
  bool ok_ = false;
  uint8_t last_seq_ = 0;
  uint8_t stale_ = 0;
};

// Analog channels: pyramid CSS, PA NTC, radio-supply divider
class Analog {
 public:
  void begin();
  void read_css(uint16_t out[4]);
  float pa_temp_c();          // NAN if the NTC is open/shorted (not fitted)
  float vradio_v();           // NAN if the divider is not fitted (reads ~0 V)
};

}  // namespace vgq
