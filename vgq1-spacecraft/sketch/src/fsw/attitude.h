// =============================================================================
//  Attitude determination helpers (AACS analogue). Pure C++ (host-testable).
//
//  * TRIAD (Black, 1964): body attitude w.r.t. local NED from two vector pairs -
//    gravity (accelerometer, more accurate -> primary) and the geomagnetic field,
//    both measured by the Nicla Sense ME. It is an attitude solution independent
//    of the BHI260's own fusion filter, so the two can be compared on the ground.
//    The NED field reference comes from inclination/declination for your site
//    (NOAA/BGS World Magnetic Model calculator), set in config.h.
//  * Running magnetometer statistics (mean vector, RMS of |B| fluctuation),
//    Welford's single-pass algorithm in SINGLE precision: the STM32U585 FPU is
//    FP32-only (FP64 is software-emulated, ~17x slower), and the textbook
//    E[x^2]-E[x]^2 form would cancel catastrophically in float (|B|^2 ~ 2500 uT^2
//    vs. a 10 nT fluctuation variance of 1e-4 uT^2).
//  * BHI260AP 6-DOF (D-75): the decoder of the Nicla ARU page 2 ("IMU 6-DOF", magic
//    0xA9, nicla_sense_me_aru.ino) and ImuStats, the aggregation of its 1 s windows over
//    one ATT period. drivers/payload.cpp reads the page; the decoding is here so that the
//    host tests reach it.
// =============================================================================
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace vgq {

struct Vec3 { float x, y, z; };
struct Quat { float w, x, y, z; };

Vec3 v_cross(const Vec3& a, const Vec3& b);
float v_dot(const Vec3& a, const Vec3& b);
float v_norm(const Vec3& a);
Vec3 v_unit(const Vec3& a);

// accel_b: specific force in body frame (at rest it points UP); mag_b: field in body.
// incl_deg / decl_deg: local geomagnetic inclination (+down) and declination (+east).
// Produces q = rotation from NED to body (scalar first). Returns false if degenerate.
bool triad_ned(const Vec3& accel_b, const Vec3& mag_b, float incl_deg, float decl_deg, Quat& q);

class MagStats {
 public:
  void reset() { n_ = 0; mx_ = my_ = mz_ = mm_ = m2_ = 0.0f; }
  void add(float bx, float by, float bz);
  uint8_t count() const { return (uint8_t)(n_ > 255 ? 255 : n_); }
  void mean(float& bx, float& by, float& bz) const;
  float rms_fluct() const;   // sqrt(E[|B|^2] - E[|B|]^2)
 private:
  uint32_t n_ = 0;
  float mx_ = 0, my_ = 0, mz_ = 0;    // running mean vector
  float mm_ = 0, m2_ = 0;             // running mean of |B| and sum of squared deviations
};

// ---- Nicla ARU pages (nicla_sense_me_aru.ino): 32 octets, big-endian, octet 31 = CRC-8 ----
static const size_t  kAruPageLen  = 32;
static const uint8_t kAruMagicImu = 0xA9;      // page 2 "IMU 6-DOF" (D-75)
// CRC-8, polynomial 0x07, initial value 0x00, no reflection, no final XOR (all pages).
uint8_t aru_crc8(const uint8_t* d, size_t n);

// Page 2 flag octet (octet 2), as the ARU firmware sets it.
enum AruImuFlag : uint8_t {
  ARU_IMU_GRV         = 0x01,   // game rotation vector valid (present, fresh <= 200 ms, norm^2 > 0.25)
  ARU_IMU_STATS       = 0x02,   // window statistics valid (>= 20 accel and >= 20 gyro samples)
  ARU_IMU_ACC_SAT     = 0x04,   // accelerometer near full scale in the window (|raw| >= 32000)
  ARU_IMU_GYRO_SAT    = 0x08,   // gyroscope near full scale in the window (or its mean clamped)
  ARU_IMU_MISSED      = 0x10,   // samples missed in the window (fewer than 23 of 25, or overrun)
  ARU_IMU_GRV_PRESENT = 0x20,   // the BHI260AP firmware provides the game rotation vector
  ARU_IMU_ACC_RANGE   = 0x40,   // accelerometer range read from the BHI260AP (else +/-8 g default)
};

// One decoded page 2: the game rotation vector of this ARU rebuild and the statistics of the
// last completed 1.000 s window (25 Hz samples). Integers exactly as sent.
struct AruImu {
  uint8_t  seq = 0;                       // ARU rebuild counter (the same as pages 0 / 1)
  uint8_t  flags = 0;                     // AruImuFlag (ARU_IMU_GRV withdrawn by the norm gate)
  int16_t  q_grv[4] = {16384, 0, 0, 0};   // w, x, y, z x 16384
  uint8_t  win_seq = 0;                   // +1 per completed window (0: none completed yet)
  uint8_t  n = 0;                         // samples in that window, min(accel, gyro)
  int16_t  acc_mean_mg[3] = {0, 0, 0};
  uint16_t vib_rms_dmg = 0;               // 0.1 mg
  uint16_t vib_peak_mg = 0;               // 1 mg
  int16_t  rate_mean_cdps[3] = {0, 0, 0}; // 0.01 deg/s
  uint16_t rate_peak_ddps = 0;            // 0.1 deg/s
};

// Decodes a page-2 image of kAruPageLen octets. False (out unchanged) for a wrong magic or a
// bad CRC - the ARU v3 firmware answers a request for page 2 with page 0 (magic 0xA7). A
// quaternion outside norm^2 0.81..1.21 is not an attitude: ARU_IMU_GRV is withdrawn, as
// for the page-0 rotation vector (payload.cpp read_motion).
bool aru_decode_imu(const uint8_t* page, AruImu& out);

// Aggregation of ARU page-2 windows over one ATT period, i.e. the time since the previous
// ATT packet (D-75). The flight reads page 2 at its 1 Hz sample, taken in a PA-off window
// and therefore not locked to the Nicla's windows: a window is counted once
// (win_seq de-duplication) and windows that went by unread are gaps. Over the valid windows
// (ARU_IMU_STATS): vib_rms = RMS of the windows' vibration RMS, peaks = maximum,
// rate_mean = mean of the window means; saturation flags are ORed over every new window.
// No windows -> the ATT sentinels. Pure (host-tested).
class ImuStats {
 public:
  // Starts a new ATT period. The last win_seq seen is kept, so a window already counted is
  // not counted again in the next period.
  void reset();
  // One page-2 reading; true when it brought a window not seen before.
  bool add(const AruImu& m);
  uint8_t windows() const { return (uint8_t)(n_ > 255u ? 255u : n_); }   // valid windows (ATT imu_nwin)
  bool gap() const { return gap_; }
  bool acc_sat() const { return acc_sat_; }
  bool gyro_sat() const { return gyro_sat_; }
  // ATT fields; the sentinel (0xFFFF / 0x7FFF) when windows() == 0.
  uint16_t vib_rms_dmg() const;
  uint16_t vib_peak_mg() const;
  int16_t rate_mean_cdps(uint8_t axis) const;
  uint16_t rate_peak_ddps() const;

 private:
  uint32_t n_ = 0;
  // Integer sums, exact for any period (2^32 windows of the largest values fit in 64 bits).
  uint64_t vib2_ = 0;                     // sum of vib_rms^2, (0.1 mg)^2
  uint16_t vib_peak_ = 0, rate_peak_ = 0;
  int64_t rate_sum_[3] = {0, 0, 0};       // sum of the window means, 0.01 deg/s
  bool gap_ = false, acc_sat_ = false, gyro_sat_ = false;
  bool seen_ = false;                     // last_win_ valid
  uint8_t last_win_ = 0;
};

}  // namespace vgq
