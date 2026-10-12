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

}  // namespace vgq
