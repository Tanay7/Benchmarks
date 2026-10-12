// =============================================================================
//  Nicla Sense ME attitude reference unit (ARU) link - I2C target 0x2A on the
//  sensor bus (Wire2, UNO Q header A4/A5; the Nicla's J2 I2C pins, 3.3 V).
//
//  The Nicla runs the custom firmware nicla_sense_me_aru (repository root) and exposes two
//  fixed 32-octet pages: the host writes ONE octet (page number) and reads 32.
//  Page 0 = motion (magic 0xA7), page 1 = environment (magic 0xA8); big-endian;
//  octet 31 = CRC-8 (poly 0x07, init 0x00) over octets 0..30. The layout is
//  documented at the top of nicla_sense_me_aru.ino.
//
//  The AS7265X spectrometer has its own driver (as7265x.h); the RM3100
//  magnetometers are in sensors.h.
// =============================================================================
#pragma once
#include <Arduino.h>
#include "i2c_bus.h"

namespace vgq {

struct AruReading {
  uint8_t seq = 0;
  uint8_t motion_flags = 0;     // b0 quaternion, b1 accel, b2 gyro, b3 magnetometer,
                                // b4 BMM150 scale read from the BHI260 (else 1/16 uT/LSB fallback)
  float qw = 1, qx = 0, qy = 0, qz = 0;      // BHI260 rotation vector (x 16384 on the wire)
  uint16_t quat_acc_mrad = 0xFFFF;           // BHI260 rotation-vector accuracy estimate
  int16_t acc_mg[3] = {0, 0, 0};
  int16_t gyro_ddps[3] = {0, 0, 0};          // 0.1 deg/s
  int16_t mag_raw[3] = {0, 0, 0};            // BMM150 via BHI260, 0.1 uT (kAruMagUtPerLsb)
  uint8_t env_flags = 0;        // b0 pressure, b1 BSEC, b2 temperature, b3 humidity, b4 gas
  uint32_t p_pa = 0xFFFFFFFFUL; // BMP390 pressure as sent (octets 3..6), Pa: the ENV packet
                                // carries it unchanged (no float round trip, PB-1.11)
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
  bool read_motion(AruReading& r);           // page 0; false also when the sequence froze
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
