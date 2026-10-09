// =============================================================================
//  Attitude determination helpers (AACS analogue). Pure C++ (host-testable).
//
//  * Pyramid coarse sun sensor: four cosine detectors whose normals are tilted
//    45 deg from +Z toward +X, -X, +Y, -Y. With all four illuminated:
//        Sx ~ (I0 - I1) / (2 sin45),  Sy ~ (I2 - I3) / (2 sin45),
//        Sz ~ (I0 + I1 + I2 + I3) / (4 cos45)
//    which is valid inside a +/-45 deg cone about +Z (the classic 4-cell pyramid).
//  * TRIAD (Black, 1964): body attitude w.r.t. local NED from two vector pairs —
//    gravity (accelerometer, more accurate -> primary) and the geomagnetic field.
//    The NED field reference comes from inclination/declination for your site
//    (NOAA/BGS World Magnetic Model calculator), set in config.h.
//  * Running magnetometer statistics (mean vector, RMS of |B| fluctuation).
// =============================================================================
#pragma once
#include <stdint.h>

namespace vgq {

struct Vec3 { float x, y, z; };
struct Quat { float w, x, y, z; };

Vec3 v_cross(const Vec3& a, const Vec3& b);
float v_dot(const Vec3& a, const Vec3& b);
float v_norm(const Vec3& a);
Vec3 v_unit(const Vec3& a);

// Returns false if the sun is outside the pyramid's linear field of view or the
// signal is below `min_signal` counts (after dark-offset subtraction).
bool css_sun_vector(const float counts[4], const float dark[4], float min_signal, Vec3& sun);

// accel_b: specific force in body frame (at rest it points UP); mag_b: field in body.
// incl_deg / decl_deg: local geomagnetic inclination (+down) and declination (+east).
// Produces q = rotation from NED to body (scalar first). Returns false if degenerate.
bool triad_ned(const Vec3& accel_b, const Vec3& mag_b, float incl_deg, float decl_deg, Quat& q);

class MagStats {
 public:
  void reset() { n_ = 0; sx_ = sy_ = sz_ = sm_ = sm2_ = 0; }
  void add(float bx, float by, float bz);
  uint8_t count() const { return (uint8_t)(n_ > 255 ? 255 : n_); }
  void mean(float& bx, float& by, float& bz) const;
  float rms_fluct() const;   // sqrt(E[|B|^2] - E[|B|]^2)
 private:
  uint32_t n_ = 0;
  double sx_ = 0, sy_ = 0, sz_ = 0, sm_ = 0, sm2_ = 0;
};

}  // namespace vgq
