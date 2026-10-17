#include "attitude.h"
#include <math.h>

namespace vgq {

Vec3 v_cross(const Vec3& a, const Vec3& b) {
  return {a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x};
}
float v_dot(const Vec3& a, const Vec3& b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
float v_norm(const Vec3& a) { return sqrtf(v_dot(a, a)); }
Vec3 v_unit(const Vec3& a) {
  const float n = v_norm(a);
  if (n <= 0.0f) return {0, 0, 0};
  return {a.x / n, a.y / n, a.z / n};
}

static void mat_to_quat(const float m[3][3], Quat& q) {
  // Shepperd's method (numerically robust).
  const float tr = m[0][0] + m[1][1] + m[2][2];
  if (tr > 0) {
    const float s = sqrtf(tr + 1.0f) * 2;
    q.w = 0.25f * s;
    q.x = (m[2][1] - m[1][2]) / s;
    q.y = (m[0][2] - m[2][0]) / s;
    q.z = (m[1][0] - m[0][1]) / s;
  } else if (m[0][0] > m[1][1] && m[0][0] > m[2][2]) {
    const float s = sqrtf(1.0f + m[0][0] - m[1][1] - m[2][2]) * 2;
    q.w = (m[2][1] - m[1][2]) / s;
    q.x = 0.25f * s;
    q.y = (m[0][1] + m[1][0]) / s;
    q.z = (m[0][2] + m[2][0]) / s;
  } else if (m[1][1] > m[2][2]) {
    const float s = sqrtf(1.0f + m[1][1] - m[0][0] - m[2][2]) * 2;
    q.w = (m[0][2] - m[2][0]) / s;
    q.x = (m[0][1] + m[1][0]) / s;
    q.y = 0.25f * s;
    q.z = (m[1][2] + m[2][1]) / s;
  } else {
    const float s = sqrtf(1.0f + m[2][2] - m[0][0] - m[1][1]) * 2;
    q.w = (m[1][0] - m[0][1]) / s;
    q.x = (m[0][2] + m[2][0]) / s;
    q.y = (m[1][2] + m[2][1]) / s;
    q.z = 0.25f * s;
  }
  if (q.w < 0) { q.w = -q.w; q.x = -q.x; q.y = -q.y; q.z = -q.z; }   // canonical sign
}

bool triad_ned(const Vec3& accel_b, const Vec3& mag_b, float incl_deg, float decl_deg, Quat& q) {
  const float d2r = 3.14159265f / 180.0f;
  const float I = incl_deg * d2r, D = decl_deg * d2r;
  // Reference vectors in NED: "up" (what an accelerometer at rest measures) and B.
  const Vec3 r1{0, 0, -1};
  const Vec3 r2{cosf(I) * cosf(D), cosf(I) * sinf(D), sinf(I)};
  const Vec3 b1 = v_unit(accel_b);
  const Vec3 bx = v_cross(b1, v_unit(mag_b));
  const Vec3 rx = v_cross(r1, r2);
  if (v_norm(bx) < 0.05f || v_norm(rx) < 0.05f) return false;   // vectors ~parallel
  const Vec3 b2 = v_unit(bx), b3 = v_cross(b1, b2);
  const Vec3 t2 = v_unit(rx), t3 = v_cross(r1, t2);
  // A = sum_k b_k r_k^T  maps NED coordinates to body coordinates: v_b = A v_ned.
  const Vec3 B[3] = {b1, b2, b3}, R[3] = {r1, t2, t3};
  float A[3][3] = {{0}};
  for (int k = 0; k < 3; ++k) {
    const float b[3] = {B[k].x, B[k].y, B[k].z};
    const float r[3] = {R[k].x, R[k].y, R[k].z};
    for (int i = 0; i < 3; ++i)
      for (int j = 0; j < 3; ++j) A[i][j] += b[i] * r[j];
  }
  // q is the Hamilton quaternion (scalar first) whose rotation matrix equals A.
  mat_to_quat(A, q);
  return true;
}

void MagStats::add(float bx, float by, float bz) {
  ++n_;
  const float inv = 1.0f / (float)n_;
  mx_ += (bx - mx_) * inv;
  my_ += (by - my_) * inv;
  mz_ += (bz - mz_) * inv;
  const float m = sqrtf(bx * bx + by * by + bz * bz);
  const float d = m - mm_;
  mm_ += d * inv;
  m2_ += d * (m - mm_);               // Welford update: deviations only, no cancellation
}

void MagStats::mean(float& bx, float& by, float& bz) const {
  if (!n_) { bx = by = bz = 0; return; }
  bx = mx_; by = my_; bz = mz_;
}

float MagStats::rms_fluct() const {   // population standard deviation of |B|
  if (n_ < 2) return 0.0f;
  const float v = m2_ / (float)n_;
  return v > 0.0f ? sqrtf(v) : 0.0f;
}

// =============================================================================
// BHI260AP 6-DOF: ARU page 2 and its aggregation (D-75)
// =============================================================================
uint8_t aru_crc8(const uint8_t* d, size_t n) {
  uint8_t c = 0;
  for (size_t i = 0; i < n; ++i) {
    c ^= d[i];
    for (int b = 0; b < 8; ++b) c = (c & 0x80) ? (uint8_t)((c << 1) ^ 0x07) : (uint8_t)(c << 1);
  }
  return c;
}

namespace {

inline uint16_t be_u16(const uint8_t* p) { return (uint16_t)((p[0] << 8) | p[1]); }
// Two's complement without an implementation-defined narrowing conversion.
inline int16_t be_i16(const uint8_t* p) {
  const uint16_t u = be_u16(p);
  return u < 0x8000u ? (int16_t)u : (int16_t)((int32_t)u - 65536);
}
const uint16_t kNaU16v = 0xFFFF;    // tlm_packets.h kNaU16 / kNaI16 (this module does not include it)
const int16_t kNaI16v = 0x7FFF;

// floor(sqrt(x)), integer only (bit-by-bit method; no floating point, no libm).
uint32_t isqrt32(uint32_t x) {
  uint32_t r = 0, bit = 1u << 30;
  while (bit > x) bit >>= 2;
  while (bit) {
    if (x >= r + bit) {
      x -= r + bit;
      r = (r >> 1) + bit;
    } else {
      r >>= 1;
    }
    bit >>= 2;
  }
  return r;
}

// sqrt(num / den) rounded to the nearest integer (half up), for num / den < 2^32, den > 0.
uint32_t rms_round(uint64_t num, uint64_t den) {
  const uint64_t q = num / den, rem = num % den;
  const uint32_t k = isqrt32((uint32_t)q);                       // floor(sqrt(num / den))
  // Round up when num / den >= (k + 1/2)^2 = k^2 + k + 1/4.
  const uint64_t t = (uint64_t)k * k + k;
  return (q > t || (q == t && 4u * rem >= den)) ? k + 1u : k;
}

}  // namespace

bool aru_decode_imu(const uint8_t* page, AruImu& out) {
  if (page == nullptr || page[0] != kAruMagicImu || aru_crc8(page, kAruPageLen - 1) != page[kAruPageLen - 1])
    return false;
  AruImu m;
  m.seq = page[1];
  m.flags = page[2];
  for (uint8_t i = 0; i < 4; ++i) m.q_grv[i] = be_i16(page + 3 + 2 * i);
  // A rotation quaternion has unit norm (x 16384 on the wire): outside 0.81..1.21 it is not
  // an attitude and its flag is withdrawn. The integer form is exact: 16384^2 = 2^28.
  int64_t n2 = 0;
  for (uint8_t i = 0; i < 4; ++i) n2 += (int64_t)m.q_grv[i] * m.q_grv[i];
  const int64_t one = (int64_t)16384 * 16384;
  if (!(n2 * 100 > one * 81 && n2 * 100 < one * 121)) m.flags &= (uint8_t)~ARU_IMU_GRV;
  m.win_seq = page[11];
  m.n = page[12];
  for (uint8_t i = 0; i < 3; ++i) {
    m.acc_mean_mg[i] = be_i16(page + 13 + 2 * i);
    m.rate_mean_cdps[i] = be_i16(page + 23 + 2 * i);
  }
  m.vib_rms_dmg = be_u16(page + 19);
  m.vib_peak_mg = be_u16(page + 21);
  m.rate_peak_ddps = be_u16(page + 29);
  out = m;
  return true;
}

void ImuStats::reset() {
  n_ = 0;
  vib2_ = 0;
  vib_peak_ = rate_peak_ = 0;
  rate_sum_[0] = rate_sum_[1] = rate_sum_[2] = 0;
  gap_ = acc_sat_ = gyro_sat_ = false;
}

bool ImuStats::add(const AruImu& m) {
  if (seen_ && m.win_seq == last_win_) return false;          // the same window again
  // win_seq 0 with nothing counted is the ARU before its first window: nothing to add yet.
  if (m.win_seq == 0 && m.n == 0 && !(m.flags & (ARU_IMU_STATS | ARU_IMU_MISSED))) {
    seen_ = true;
    last_win_ = 0;
    return false;
  }
  // Windows that completed between two readings went by unread (modulo 256: a counter
  // that moved backwards - an ARU restart - also shows as a gap).
  if (seen_ && (uint8_t)(m.win_seq - last_win_) != 1u) gap_ = true;
  seen_ = true;
  last_win_ = m.win_seq;
  if (m.flags & ARU_IMU_MISSED) gap_ = true;
  if (m.flags & ARU_IMU_ACC_SAT) acc_sat_ = true;
  if (m.flags & ARU_IMU_GYRO_SAT) gyro_sat_ = true;
  if (!(m.flags & ARU_IMU_STATS)) return true;                 // counted as seen, no statistics
  if (n_ < 0xFFFFFFFFu) ++n_;
  vib2_ += (uint64_t)m.vib_rms_dmg * m.vib_rms_dmg;
  if (m.vib_peak_mg > vib_peak_) vib_peak_ = m.vib_peak_mg;
  if (m.rate_peak_ddps > rate_peak_) rate_peak_ = m.rate_peak_ddps;
  for (uint8_t i = 0; i < 3; ++i) rate_sum_[i] += m.rate_mean_cdps[i];
  return true;
}

uint16_t ImuStats::vib_rms_dmg() const {
  if (n_ == 0) return kNaU16v;
  const uint32_t r = rms_round(vib2_, n_);                       // every term <= 65535^2: mean < 2^32
  return r >= 65534u ? (uint16_t)65534u : (uint16_t)r;
}

uint16_t ImuStats::vib_peak_mg() const {
  if (n_ == 0) return kNaU16v;
  return vib_peak_ >= kNaU16v ? (uint16_t)(kNaU16v - 1u) : vib_peak_;
}

int16_t ImuStats::rate_mean_cdps(uint8_t axis) const {
  if (n_ == 0 || axis > 2) return kNaI16v;
  // Rounded half away from zero (the flight's lroundf), clamped clear of the sentinel.
  const int64_t s = rate_sum_[axis], n = (int64_t)n_;
  int64_t q = (s >= 0 ? s + n / 2 : s - n / 2) / n;
  if (q > 32766) q = 32766;
  if (q < -32767) q = -32767;
  return (int16_t)q;
}

uint16_t ImuStats::rate_peak_ddps() const {
  if (n_ == 0) return kNaU16v;
  return rate_peak_ >= kNaU16v ? (uint16_t)(kNaU16v - 1u) : rate_peak_;
}

}  // namespace vgq
