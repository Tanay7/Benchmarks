// =============================================================================
//  Register-level sensor drivers (no third-party code — flight-software style).
//  Each driver owns its mux channel and reports health via ok().
//
//  MPU-9250 : TDK InvenSense register map RM-MPU-9250A-00. WHO_AM_I 0x71
//             (0x73 = MPU-9255, 0x70 = MPU-6500 => no AK8963: common clone!).
//             The part is end-of-life; many modules sold today are clones.
//  AK8963   : reached through MPU-9250 I2C bypass at 0x0C; little-endian data;
//             its X/Y axes are swapped and Z inverted relative to accel/gyro.
//  RM3100   : PNI Sensor; REVID 0x36 == 0x22; gain = 0.3671*CC + 1.5 LSB/uT.
//  MMC5603  : MEMSIC; Product ID 0x39 == 0x10; 20-bit, 0.00625 uT/LSB, offset 2^19.
//  VEML7700 : Vishay; 16-bit LSB-first registers; resolution 0.0036 lx/ct at
//             gain x2 / 800 ms, scaled by IT and gain; high-lux correction
//             polynomial from the Vishay application note.
//  INA226   : TI; bus 1.25 mV/LSB, shunt 2.5 uV/LSB (optional power monitor).
// =============================================================================
#pragma once
#include <Arduino.h>
#include "i2c_bus.h"

namespace vgq {

class Mpu9250 {
 public:
  bool begin(I2cBus& bus, int8_t ch, uint8_t addr = 0x68);
  bool read();   // updates the fields below
  bool ok() const { return ok_; }
  bool mag_ok() const { return mag_ok_; }
  uint8_t who_am_i() const { return who_; }
  float ax_g = 0, ay_g = 0, az_g = 0, gx_dps = 0, gy_dps = 0, gz_dps = 0, temp_c = NAN;
  float mx_uT = 0, my_uT = 0, mz_uT = 0;   // AK8963, already rotated into the MPU (body) axes
 private:
  I2cBus* bus_ = nullptr;
  int8_t ch_ = -1;
  uint8_t addr_ = 0x68, who_ = 0;
  bool ok_ = false, mag_ok_ = false;
  float asa_[3] = {1, 1, 1};
};

class Rm3100 {
 public:
  bool begin(I2cBus& bus, int8_t ch, uint8_t addr = 0x20, uint16_t cycle_count = 200);
  bool set_cycle_count(uint16_t cc);
  bool measure(float& bx_uT, float& by_uT, float& bz_uT);   // single-shot, blocking <= 50 ms
  bool ok() const { return ok_; }
 private:
  I2cBus* bus_ = nullptr;
  int8_t ch_ = -1;
  uint8_t addr_ = 0x20;
  float gain_ = 75.0f;
  bool ok_ = false;
};

class Mmc5603 {
 public:
  bool begin(I2cBus& bus, int8_t ch, uint8_t addr = 0x30);
  bool measure(float& bx_uT, float& by_uT, float& bz_uT);   // auto SET/RESET, single-shot
  bool temperature(float& t_c);
  bool ok() const { return ok_; }
 private:
  bool wait_status(uint8_t mask, uint32_t timeout_ms);
  I2cBus* bus_ = nullptr;
  int8_t ch_ = -1;
  uint8_t addr_ = 0x30;
  bool ok_ = false;
};

class Veml7700 {
 public:
  bool begin(I2cBus& bus, int8_t ch, uint8_t addr = 0x10);
  bool read_lux(float& lux);    // auto-ranging over gain / integration time
  bool ok() const { return ok_; }
 private:
  bool apply();
  I2cBus* bus_ = nullptr;
  int8_t ch_ = -1;
  uint8_t addr_ = 0x10;
  uint8_t gain_idx_ = 0;        // index into gain table (low -> high sensitivity)
  uint8_t it_idx_ = 2;          // index into IT table
  bool ok_ = false;
};

class Ina226 {
 public:
  bool begin(I2cBus& bus, int8_t ch, float shunt_ohm, uint8_t addr = 0x40);
  bool read(float& bus_v, float& current_a);
  bool ok() const { return ok_; }
 private:
  I2cBus* bus_ = nullptr;
  int8_t ch_ = -1;
  uint8_t addr_ = 0x40;
  float shunt_ = 0.01f;
  bool ok_ = false;
};

}  // namespace vgq
