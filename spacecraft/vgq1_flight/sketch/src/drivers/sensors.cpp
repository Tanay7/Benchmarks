#include "sensors.h"

namespace vgq {

static int16_t be_i16(const uint8_t* p) { return (int16_t)((p[0] << 8) | p[1]); }
static int16_t le_i16(const uint8_t* p) { return (int16_t)((p[1] << 8) | p[0]); }

// =============================================================================
// MPU-9250 + AK8963
// =============================================================================
bool Mpu9250::begin(I2cBus& bus, int8_t ch, uint8_t addr) {
  bus_ = &bus; ch_ = ch; addr_ = addr;
  ok_ = mag_ok_ = false;
  if (!bus.select(ch) || !bus.probe(addr)) return false;
  bus.read(addr, 0x75, &who_, 1);                          // WHO_AM_I
  bus.write8(addr, 0x6B, 0x80);                            // PWR_MGMT_1: device reset
  delay(100);
  bus.write8(addr, 0x6B, 0x01);                            // clock = PLL (gyro)
  bus.write8(addr, 0x1A, 0x03);                            // CONFIG: DLPF 41 Hz
  bus.write8(addr, 0x19, 9);                               // SMPLRT_DIV: 1 kHz / 10 = 100 Hz
  bus.write8(addr, 0x1B, 0x08);                            // GYRO_CONFIG: +/-500 dps (65.5 LSB/dps)
  bus.write8(addr, 0x1C, 0x08);                            // ACCEL_CONFIG: +/-4 g (8192 LSB/g)
  bus.write8(addr, 0x1D, 0x03);                            // ACCEL_CONFIG2: DLPF 41 Hz
  bus.write8(addr, 0x37, 0x02);                            // INT_PIN_CFG: BYPASS_EN -> AK8963 at 0x0C
  ok_ = (who_ == 0x71 || who_ == 0x73 || who_ == 0x70);
  // AK8963 magnetometer (absent on MPU-6500 based clones)
  uint8_t wia = 0;
  if (bus.probe(0x0C) && bus.read(0x0C, 0x00, &wia, 1) && wia == 0x48) {
    bus.write8(0x0C, 0x0A, 0x00); delay(10);               // power down
    bus.write8(0x0C, 0x0A, 0x0F); delay(10);               // fuse ROM access
    uint8_t asa[3];
    if (bus.read(0x0C, 0x10, asa, 3))
      for (int i = 0; i < 3; ++i) asa_[i] = ((asa[i] - 128) * 0.5f) / 128.0f + 1.0f;
    bus.write8(0x0C, 0x0A, 0x00); delay(10);
    bus.write8(0x0C, 0x0A, 0x16); delay(10);               // 16-bit, continuous mode 2 (100 Hz)
    mag_ok_ = true;
  }
  return ok_;
}

bool Mpu9250::read() {
  if (!ok_ || !bus_->select(ch_)) return false;
  uint8_t b[14];
  if (!bus_->read(addr_, 0x3B, b, 14)) return false;
  ax_g = be_i16(b + 0) / 8192.0f;
  ay_g = be_i16(b + 2) / 8192.0f;
  az_g = be_i16(b + 4) / 8192.0f;
  temp_c = be_i16(b + 6) / 333.87f + 21.0f;
  gx_dps = be_i16(b + 8) / 65.5f;
  gy_dps = be_i16(b + 10) / 65.5f;
  gz_dps = be_i16(b + 12) / 65.5f;
  if (mag_ok_) {
    uint8_t st1 = 0, m[7];
    if (bus_->read(0x0C, 0x02, &st1, 1) && (st1 & 0x01) && bus_->read(0x0C, 0x03, m, 7)) {
      if (!(m[6] & 0x08)) {                                  // ST2.HOFL: magnetic overflow
        const float k = 0.15f;                               // uT/LSB in 16-bit mode
        const float hx = le_i16(m + 0) * asa_[0] * k;
        const float hy = le_i16(m + 2) * asa_[1] * k;
        const float hz = le_i16(m + 4) * asa_[2] * k;
        mx_uT = hy; my_uT = hx; mz_uT = -hz;                  // AK8963 -> MPU axes
      }
    }
  }
  return true;
}

// =============================================================================
// RM3100
// =============================================================================
bool Rm3100::begin(I2cBus& bus, int8_t ch, uint8_t addr, uint16_t cc) {
  bus_ = &bus; ch_ = ch; addr_ = addr;
  ok_ = false;
  if (!bus.select(ch) || !bus.probe(addr)) return false;
  uint8_t rev = 0;
  bus.read(addr, 0x36, &rev, 1);                           // REVID
  if (rev != 0x22) return false;
  bus.write8(addr, 0x01, 0x00);                            // CMM off: single-measurement mode
  ok_ = set_cycle_count(cc);
  return ok_;
}

bool Rm3100::set_cycle_count(uint16_t cc) {
  if (!bus_->select(ch_)) return false;
  const uint8_t b[6] = {(uint8_t)(cc >> 8), (uint8_t)cc, (uint8_t)(cc >> 8), (uint8_t)cc,
                        (uint8_t)(cc >> 8), (uint8_t)cc};
  if (!bus_->write(addr_, 0x04, b, 6)) return false;       // CCX, CCY, CCZ
  gain_ = 0.3671f * cc + 1.5f;                             // LSB per uT (PNI datasheet)
  return true;
}

bool Rm3100::measure(float& bx, float& by, float& bz) {
  if (!ok_ || !bus_->select(ch_)) return false;
  if (!bus_->write8(addr_, 0x00, 0x70)) return false;      // POLL: X, Y, Z
  const uint32_t t0 = millis();
  uint8_t st = 0;
  do {
    if (!bus_->read(addr_, 0x34, &st, 1)) return false;    // STATUS
    if (st & 0x80) break;                                  // DRDY
    delay(1);
  } while (millis() - t0 < 50);
  if (!(st & 0x80)) return false;
  uint8_t m[9];
  if (!bus_->read(addr_, 0x24, m, 9)) return false;
  int32_t v[3];
  for (int i = 0; i < 3; ++i) {
    int32_t x = ((int32_t)m[3 * i] << 16) | ((int32_t)m[3 * i + 1] << 8) | m[3 * i + 2];
    if (x & 0x800000) x -= 0x1000000;                      // 24-bit two's complement
    v[i] = x;
  }
  bx = v[0] / gain_; by = v[1] / gain_; bz = v[2] / gain_;
  return true;
}

// =============================================================================
// MMC5603NJ
// =============================================================================
bool Mmc5603::begin(I2cBus& bus, int8_t ch, uint8_t addr) {
  bus_ = &bus; ch_ = ch; addr_ = addr;
  ok_ = false;
  if (!bus.select(ch) || !bus.probe(addr)) return false;
  uint8_t pid = 0;
  bus.read(addr, 0x39, &pid, 1);
  if (pid != 0x10) return false;
  bus.write8(addr, 0x1C, 0x80);                            // CTRL1: software reset
  delay(20);
  bus.write8(addr, 0x1B, 0x08); delay(1);                  // SET pulse
  bus.write8(addr, 0x1B, 0x10); delay(1);                  // RESET pulse
  ok_ = true;
  return true;
}

bool Mmc5603::wait_status(uint8_t mask, uint32_t timeout_ms) {
  const uint32_t t0 = millis();
  uint8_t st = 0;
  while (millis() - t0 < timeout_ms) {
    if (!bus_->read(addr_, 0x18, &st, 1)) return false;
    if (st & mask) return true;
    delay(1);
  }
  return false;
}

bool Mmc5603::measure(float& bx, float& by, float& bz) {
  if (!ok_ || !bus_->select(ch_)) return false;
  if (!bus_->write8(addr_, 0x1B, 0x21)) return false;      // TM_M + Auto_SR_en
  if (!wait_status(0x40, 20)) return false;                // Meas_M_done
  uint8_t b[9];
  if (!bus_->read(addr_, 0x00, b, 9)) return false;
  const int32_t x = (int32_t)(((uint32_t)b[0] << 12) | ((uint32_t)b[1] << 4) | (b[6] >> 4)) - (1 << 19);
  const int32_t y = (int32_t)(((uint32_t)b[2] << 12) | ((uint32_t)b[3] << 4) | (b[7] >> 4)) - (1 << 19);
  const int32_t z = (int32_t)(((uint32_t)b[4] << 12) | ((uint32_t)b[5] << 4) | (b[8] >> 4)) - (1 << 19);
  bx = x * 0.00625f; by = y * 0.00625f; bz = z * 0.00625f;
  return true;
}

bool Mmc5603::temperature(float& t) {
  if (!ok_ || !bus_->select(ch_)) return false;
  if (!bus_->write8(addr_, 0x1B, 0x02)) return false;      // TM_T
  if (!wait_status(0x80, 20)) return false;                // Meas_T_done
  uint8_t r = 0;
  if (!bus_->read(addr_, 0x09, &r, 1)) return false;
  t = r * 0.8f - 75.0f;
  return true;
}

// =============================================================================
// VEML7700 (auto-range)
// =============================================================================
namespace {
// gain codes ordered from least to most sensitive, with their numeric gain
const uint8_t kGainCode[4] = {0x02, 0x03, 0x00, 0x01};   // 1/8, 1/4, 1, 2
const float   kGainVal[4]  = {0.125f, 0.25f, 1.0f, 2.0f};
const uint8_t kItCode[6]   = {0x0C, 0x08, 0x00, 0x01, 0x02, 0x03};   // 25,50,100,200,400,800 ms
const uint16_t kItMs[6]    = {25, 50, 100, 200, 400, 800};
}

bool Veml7700::apply() {
  const uint16_t conf = (uint16_t)((kGainCode[gain_idx_] << 11) | (kItCode[it_idx_] << 6));  // SD=0
  const uint8_t b[2] = {(uint8_t)conf, (uint8_t)(conf >> 8)};                                   // LSB first
  return bus_->write(addr_, 0x00, b, 2);
}

bool Veml7700::begin(I2cBus& bus, int8_t ch, uint8_t addr) {
  bus_ = &bus; ch_ = ch; addr_ = addr;
  ok_ = false;
  if (!bus.select(ch) || !bus.probe(addr)) return false;
  gain_idx_ = 0; it_idx_ = 2;                              // start at 1/8, 100 ms (sunlight-safe)
  ok_ = apply();
  return ok_;
}

bool Veml7700::read_lux(float& lux) {
  if (!ok_ || !bus_->select(ch_)) return false;
  uint8_t b[2];
  if (!bus_->read(addr_, 0x04, b, 2)) return false;
  const uint16_t raw = (uint16_t)(b[0] | (b[1] << 8));
  const float res = 0.0036f * (800.0f / kItMs[it_idx_]) * (2.0f / kGainVal[gain_idx_]);
  lux = raw * res;
  if (kGainVal[gain_idx_] < 1.0f && lux > 1000.0f)        // Vishay non-linearity correction
    lux = (((6.0135e-13f * lux - 9.3924e-9f) * lux + 8.1488e-5f) * lux + 1.0023f) * lux;
  // Auto-range for the next reading (settles over a few cycles).
  if (raw > 60000) {
    if (it_idx_ > 0) --it_idx_; else if (gain_idx_ > 0) --gain_idx_;
    apply();
  } else if (raw < 100) {
    if (gain_idx_ < 3) ++gain_idx_; else if (it_idx_ < 5) ++it_idx_;
    apply();
  }
  return true;
}

// =============================================================================
// INA226 (optional radio-supply monitor)
// =============================================================================
bool Ina226::begin(I2cBus& bus, int8_t ch, float shunt_ohm, uint8_t addr) {
  bus_ = &bus; ch_ = ch; addr_ = addr; shunt_ = shunt_ohm;
  ok_ = false;
  if (!bus.select(ch) || !bus.probe(addr)) return false;
  // CONFIG: AVG=16, VBUSCT=VSHCT=1.1 ms, continuous shunt+bus
  const uint8_t cfg[2] = {0x45, 0x27};
  ok_ = bus.write(addr, 0x00, cfg, 2);
  return ok_;
}

bool Ina226::read(float& v, float& i) {
  if (!ok_ || !bus_->select(ch_)) return false;
  uint8_t b[2];
  if (!bus_->read(addr_, 0x02, b, 2)) return false;
  v = (uint16_t)((b[0] << 8) | b[1]) * 1.25e-3f;
  if (!bus_->read(addr_, 0x01, b, 2)) return false;
  i = be_i16(b) * 2.5e-6f / shunt_;
  return true;
}

}  // namespace vgq
