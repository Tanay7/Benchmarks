// =============================================================================
//  Science payload instruments.
//
//    AS7265X          "SparkFun Spectral Triad AS7265X" library (1.0.5), I2C 0x49
//    Nicla Sense Env  "Arduino_NiclaSenseEnv" library (board runs Arduino's firmware), 0x21
//    Nicla Sense ME   custom firmware spacecraft/nicla_sense_me_aru, I2C target 0x2A
//
//  Vendor libraries are optional at COMPILE time (__has_include): a missing one
//  makes that instrument report "absent" in HK instead of breaking the build.
// =============================================================================
#pragma once
#include <Arduino.h>
#include "i2c_bus.h"

namespace vgq {

// ---- AS7265X 18-channel spectrometer (410..940 nm) -----------------------------------
struct SpecReading {
  bool ok = false;
  float cal_uW_cm2[18];         // calibrated irradiance, wavelength order 410 .. 940 nm
  uint16_t raw[18];             // raw counts, same order
  int8_t temp_c[3];             // die temperature of device 0 (master), 1, 2
};
class Spectrometer {
 public:
  static const uint8_t kAddr = 0x49;
  bool begin(I2cBus& bus);
  // gain code 0 = 1x, 1 = 3.7x, 2 = 16x, 3 = 64x; integration = cycles x 2.8 ms;
  // lamps: bit 0 white, bit 1 IR, bit 2 UV (lit only while measuring).
  bool configure(uint8_t gain_code, uint8_t int_cycles, uint8_t lamps);
  bool read(SpecReading& r);    // blocks for the integration time
  bool ok() const { return ok_; }
  uint8_t gain_code() const { return gain_; }
  uint8_t int_cycles() const { return cycles_; }
  uint8_t lamps() const { return lamps_; }
 private:
  I2cBus* bus_ = nullptr;
  bool ok_ = false;
  uint8_t gain_ = 2, cycles_ = 50, lamps_ = 0;
};

// ---- Nicla Sense Env (HS4001, ZMOD4410, ZMOD4510) --------------------------------------
struct NiclaEnvReading {
  uint8_t flags = 0;            // b0 HS4001 T/RH, b1 ZMOD4410 indoor, b2 ZMOD4510 outdoor
  float t_c = NAN, rh = NAN, iaq = NAN, tvoc = NAN, eco2 = NAN, no2 = NAN, o3 = NAN;
  int aqi = -1;
};
class NiclaEnv {
 public:
  static const uint8_t kAddr = 0x21;
  bool begin(I2cBus& bus);
  bool read(NiclaEnvReading& r);
  bool ok() const { return ok_; }
 private:
  I2cBus* bus_ = nullptr;
  bool ok_ = false;
};

// ---- Nicla Sense ME attitude reference unit (two 32-octet pages, CRC-8) ---------------
// The host writes one octet (page number) and reads 32 octets. Page 0 = motion
// (magic 0xA7), page 1 = environment (magic 0xA8). Layout: nicla_sense_me_aru.ino.
struct AruReading {
  uint8_t seq = 0;
  uint8_t motion_flags = 0;     // b0 quaternion, b1 accel, b2 gyro, b3 magnetometer
  float qw = 1, qx = 0, qy = 0, qz = 0;
  uint16_t quat_acc_mrad = 0xFFFF;           // BHI260 rotation-vector accuracy estimate
  int16_t acc_mg[3] = {0, 0, 0};
  int16_t gyro_ddps[3] = {0, 0, 0};          // 0.1 deg/s
  int16_t mag_raw[3] = {0, 0, 0};            // BMM150 via BHI260, see kAruMagUtPerLsb
  uint8_t env_flags = 0;        // b0 pressure, b1 BSEC, b2 temperature, b3 humidity, b4 gas
  float p_hpa = NAN, t_c = NAN, rh = NAN, bvoc_ppm = NAN;
  uint16_t iaq = 0, iaq_s = 0;
  uint32_t co2eq = 0, gas_ohm = 0;
  uint8_t bsec_accuracy = 0;
};
class NiclaAru {
 public:
  static const uint8_t kAddr = 0x2A;
  static const size_t kPageLen = 32;
  bool begin(I2cBus& bus);
  bool read_motion(AruReading& r);           // page 0
  bool read_env(AruReading& r);              // page 1
  bool ok() const { return ok_; }
  static uint8_t crc8(const uint8_t* d, size_t n);   // poly 0x07, init 0x00
 private:
  bool page(uint8_t n, uint8_t magic, uint8_t* b);
  I2cBus* bus_ = nullptr;
  bool ok_ = false;
  uint8_t last_seq_ = 0, stale_ = 0;
};

}  // namespace vgq
