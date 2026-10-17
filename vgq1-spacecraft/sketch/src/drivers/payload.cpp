#include "payload.h"

namespace vgq {

// =============================================================================
// Nicla Sense ME attitude reference unit (custom firmware, I2C target 0x2A)
// =============================================================================
bool NiclaAru::begin(I2cBus& bus) {
  bus_ = &bus;
  ok_ = bus.probe(kAddr);
  return ok_;
}

// Selects page n and reads it; accepted only with the right magic and a good CRC.
bool NiclaAru::page(uint8_t n, uint8_t magic, uint8_t* b) {
  if (!ok_ || !bus_->write_raw(kAddr, &n, 1)) return false;
  if (!bus_->read_raw(kAddr, b, kPageLen)) return false;
  return b[0] == magic && crc8(b, kPageLen - 1) == b[kPageLen - 1];
}

static inline uint16_t be_u16(const uint8_t* p) { return (uint16_t)((p[0] << 8) | p[1]); }
// Two's complement without an implementation-defined narrowing conversion.
static inline int16_t be_i16(const uint8_t* p) {
  const uint16_t u = be_u16(p);
  return u < 0x8000u ? (int16_t)u : (int16_t)((int32_t)u - 65536);
}
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
  // Range check: a rotation quaternion has unit norm, and the x 16384 fixed point keeps a
  // valid one well inside 0.9..1.1. Anything else is not an attitude: the quaternion flag
  // is withdrawn so that no consumer (ATT packet, SPEC boresight) uses it.
  const float n2 = r.qw * r.qw + r.qx * r.qx + r.qy * r.qy + r.qz * r.qz;
  if (!(n2 > 0.81f && n2 < 1.21f)) r.motion_flags &= (uint8_t)~1u;
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
  r.p_pa = be_u32(b + 3);
  r.p_hpa = (float)r.p_pa / 100.0f;
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

bool NiclaAru::read_imu(AruImu& m) {
  uint8_t b[kPageLen];
  return page(2, kAruMagicImu, b) && aru_decode_imu(b, m);   // magic, CRC, quaternion norm gate
}

}  // namespace vgq
