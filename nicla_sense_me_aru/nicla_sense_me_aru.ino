// =============================================================================
//  VGQ-1 Attitude Reference Unit (ARU) - Arduino Nicla Sense ME firmware
//
//  The Nicla's BHI260AP smart sensor runs Bosch's on-chip sensor fusion; this
//  sketch exposes the fused rotation vector, the corrected accelerometer,
//  gyroscope and magnetometer (BMM150), the BMP390 pressure, the BME688 /
//  BSEC environment outputs and the BHI260AP 6-DOF part (game rotation vector
//  and 1 s window statistics of the accelerometer and gyroscope) to the
//  spacecraft computer (UNO Q) as three fixed 32-octet pages on I2C. This
//  mirrors how a real attitude reference unit hands a pre-computed solution to
//  the C&DH computer over a bus.
//
//  Bus (D-74): the Nicla is an I2C TARGET at 0x2A on its `Wire` bus, nRF52832
//  P0_22 (SDA) / P0_23 (SCL). The same two nets are the J2 header I2C pins (pin 1
//  SDA, pin 2 SCL) and the ESLOV connector J5 (pin 4 SDA, pin 3 SCL). J2 is
//  level-translated to VDDIO_EXT, which BHY2.begin() sets to 3.3 V; J5 at the same
//  level is an inference (its translator is not documented: VERIFY T-SC-16). On the
//  spacecraft the ESLOV cable goes to the UNO Q header A4 (SDA) / A5 (SCL) - the
//  NICLA bus - with J5 pin 1 on 5 V, pin 5 on GND and pin 2 (INT) not connected:
//  the pages are polled, and NICLA_STANDALONE never drives the ESLOV INT line.
//  Connect J2 OR J5 to the host, never both. No firmware difference between the two.
//
//  Protocol: the host writes ONE octet (page number 0, 1 or 2; any other value
//  selects page 0), then reads 32. Pages are 32 octets so that no I2C buffer
//  anywhere needs to exceed 32. All three pages are rebuilt together at 10 Hz and
//  carry the same rebuild counter `seq`, so the host can pair them.
//
//  Page 0 - motion (magic 0xA7), big-endian:
//    0 magic | 1 seq | 2 flags (b0 quaternion, b1 accel, b2 gyro, b3 mag,
//                               b4 mag scale read from the BHI260, else 1/16 uT/LSB fallback)
//    3..10  rotation vector qw qx qy qz   int16 x 16384
//    11..12 rotation-vector accuracy      uint16 mrad (BHI260 estimate)
//    13..18 accel x y z                   int16 mg  (range read from the BHI260)
//    19..24 gyro  x y z                   int16 0.1 deg/s
//    25..30 mag   x y z                   int16 0.1 uT (BMM150, corrected)
//    31 CRC-8 (poly 0x07, init 0) over 0..30
//  Page 1 - environment (magic 0xA8):
//    0 magic | 1 seq | 2 flags (b0 pressure, b1 BSEC, b2 temperature, b3 humidity, b4 gas)
//    3..6   pressure     uint32 Pa (= hPa x 100), BMP390
//    7..8   temperature  int16  degC x 100
//    9..10  humidity     uint16 %RH x 100 (the BHI260 delivers whole %RH)
//    11..12 BSEC IAQ     uint16      | 13..14 BSEC static IAQ uint16
//    15..18 CO2 equiv.   uint32 ppm  | 19..20 bVOC equiv. uint16 ppm x 100
//    21..24 gas resistance uint32 ohm (SENSOR_ID_GAS)
//    25 BSEC accuracy (0..3) | 26..30 reserved (0) | 31 CRC-8 over 0..30
//  Page 2 - IMU 6-DOF (magic 0xA9, D-75):
//    0 magic | 1 seq (the same rebuild counter as pages 0 and 1)
//    2 flags: b0 game rotation vector valid (present, a sample within 200 ms, norm^2 > 0.25)
//             b1 window statistics valid (>= 20 accelerometer and >= 20 gyroscope samples)
//             b2 accelerometer near full scale in the window (any |raw| >= 32000)
//             b3 gyroscope near full scale in the window (any |raw| >= 32000, or rate_mean clamped)
//             b4 samples missed in the window (fewer than 25 - 2 of either, or a buffer overrun)
//             b5 game rotation vector present in the BHI260AP firmware (hasSensor(37))
//             b6 accelerometer range read from the BHI260AP (else the +/-8 g default)
//             b7 0
//    3..10  game rotation vector qw qx qy qz   int16 x 16384 (BHI260AP id 37: accelerometer +
//           gyroscope only, no magnetometer - its heading reference is arbitrary and drifts)
//    11     win_seq   uint8, +1 per completed 1.000 s window (0 = none completed yet)
//    12     n         uint8, samples in that window, min(accelerometer, gyroscope)
//    13..18 acc_mean x y z   int16 mg: window mean of the corrected accelerometer
//    19..20 vib_rms          uint16 0.1 mg: sqrt(sum of the three per-axis variances) in
//                            the window, i.e. the RMS of the acceleration with the window
//                            mean removed (population variance)
//    21..22 vib_peak         uint16 1 mg: max over the samples of |a_i - a_mean|
//    23..28 rate_mean x y z  int16 0.01 deg/s: window mean of the corrected gyroscope
//    29..30 rate_peak        uint16 0.1 deg/s: max over the samples of |omega_i|
//    31 CRC-8 over 0..30
//    Windows: 1.000 s of the nRF52's millis(), filled with the 25 Hz accelerometer and
//    gyroscope samples that arrived in it ("Below an ODR of 100 Hz, the data provided is sub
//    sampled from a 100 Hz signal", BHI260AP datasheet, virtual sensor output data rates);
//    statistics in two passes (exact mean, then the deviations). The page always shows the
//    last completed window.
//
//  Validity (every flag clears when its sensor goes silent): a flag needs a sample within
//  5 nominal periods of its virtual sensor - 200 ms for the 25 Hz motion sensors, 5 s for
//  the 1 Hz barometer, 15 s for the BME688 outputs (temperature, humidity, gas, BSEC),
//  which BSEC measures every 3 s in its low-power mode. Temperature and humidity are
//  "on-change" virtual sensors (BHI260AP datasheet Table 88: a frame only when the value
//  changes), so a steady reading sends nothing new: they stay valid while the same BME688
//  still reports through its continuous outputs (gas, BSEC). VERIFY the cadences on the
//  bench (the ARU serial log prints the sample counts).
//  Sample detection: Fresh<> below. Arduino_BHY2 keeps only the latest sample of each
//  virtual sensor and sets a protected flag on every new one; clearDataAvailFlag() is not
//  used because 1.0.8 declares it bool without a return statement (undefined behaviour).
//  At most 10 sensor objects may be subscribed (SensorManager _sensors[10], no bounds
//  check): the game rotation vector is the 10th and last.
//
//  Scaling (BHI260AP datasheet Table 88 and 13.2.8, Arduino_BHY2 1.0.8 source):
//    * accelerometer, gyroscope and magnetometer corrected are int16 vectors
//      "scaled to the current dynamic range": physical = raw / 32768 * range,
//      range in g, deg/s and uT. getConfiguration() returns that range
//      (Virtual Sensor Configuration parameter 0x0500 + ID, octets 0x0E..0x0F).
//      The library itself hands out raw counts (scale factor 1.0);
//    * barometer: 24-bit unsigned, 1 LSB = 1/128 Pa. The library multiplies by
//      0.0078, not 1/128 = 0.0078125 (-0.16 %, about -1.6 hPa at sea level,
//      three times the BMP390's +/-0.5 hPa absolute accuracy), so the raw count
//      is recovered with the library's own factor and rescaled exactly;
//    * temperature 0.01 degC, humidity 1 %RH, gas resistance 1 ohm per LSB;
//    * SensorQuaternion::accuracy() is an angle estimate (radians), not 0..3; for the
//      game rotation vector the BHI260AP always reports 0 (datasheet Table 89), so page 2
//      carries none;
//    * with the default SENSOR_DATA_FIXED_LENGTH (10) the BSEC frame delivers
//      IAQ, static IAQ, bVOC, CO2 and accuracy; gas resistance therefore comes
//      from the separate gas virtual sensor.
//  Bosch datasheets: BMM150 range +/-1300 uT (x, y), +/-2500 uT (z), 0.3 uT
//  resolution; BMP390 operating range 300..1250 hPa; BME688 0..100 %RH, IAQ 0..500.
//  NICLA_STANDALONE keeps the library from claiming the I2C bus itself.
//  Build: arduino:mbed_nicla:nicla_sense with Arduino_BHY2 1.0.8 (CI job nicla-build).
// =============================================================================
#include "Arduino.h"
#include "Arduino_BHY2.h"
#include <Wire.h>

static const uint8_t kI2cAddr = 0x2A;

// One new sample per call at most: true when the library stored a sample since the last
// call. _dataAvail is the protected flag SensorManager::process() sets for every sample.
template <class S>
struct Fresh : S {
  using S::S;
  bool take() {
    const bool d = this->_dataAvail;
    this->_dataAvail = false;
    return d;
  }
};

// Subscription order = begin() order below; the game rotation vector is the 10th object.
Fresh<SensorQuaternion> rotation(SENSOR_ID_RV);
Fresh<SensorXYZ> accel(SENSOR_ID_ACC);
Fresh<SensorXYZ> gyro(SENSOR_ID_GYRO);
Fresh<SensorXYZ> mag(SENSOR_ID_MAG);
Fresh<Sensor> pressure(SENSOR_ID_BARO);
Fresh<Sensor> temperature(SENSOR_ID_TEMP);
Fresh<Sensor> humidity(SENSOR_ID_HUM);
Fresh<Sensor> gas(SENSOR_ID_GAS);
Fresh<SensorBSEC> bsec(SENSOR_ID_BSEC);
Fresh<SensorQuaternion> game(SENSOR_ID_GAMERV);

static const uint8_t kPages = 3;
static volatile uint8_t g_pages[kPages][32];
static volatile uint8_t g_page_sel = 0;
static uint8_t g_seq = 0;
static float g_acc_mg_per_lsb = 8000.0f / 32768.0f;     // replaced by the configured range
static float g_gyro_ddps_per_lsb = 20000.0f / 32768.0f;
static float g_mag_dut_per_lsb = 1.0f / 16.0f * 10.0f;  // 0.1 uT per LSB; fallback 1/16 uT/LSB
static bool g_acc_range_known = false, g_mag_range_known = false;
static float g_baro_lib_scale = 0.0078f;                // Arduino_BHY2 factor, looked up in setup()
static bool g_grv_present = false;                      // game.begin(): hasSensor(SENSOR_ID_GAMERV)

// BMP390 full-accuracy operating range, Pa (datasheet: 300..1250 hPa).
static const float kPressureMinPa = 30000.0f;
static const float kPressureMaxPa = 125000.0f;

// ---- freshness: 5 nominal periods per virtual sensor (header "Validity") ----------------
static const uint32_t kStaleMotionMs = 5 * 40;          // 25 Hz
static const uint32_t kStaleBaroMs = 5 * 1000;          // 1 Hz
static const uint32_t kStaleBmeMs = 5 * 3000;           // BSEC low-power period 3 s
struct Track {
  uint32_t last_ms = 0;
  uint32_t count = 0;                                   // samples since boot (serial log)
  bool seen = false;
};
static Track t_rv, t_acc, t_gyro, t_mag, t_baro, t_temp, t_hum, t_gas, t_bsec, t_grv;
static void note(Track& t, uint32_t now) {
  t.last_ms = now;
  t.seen = true;
  ++t.count;
}
static bool fresh(const Track& t, uint32_t now, uint32_t stale_ms) {
  return t.seen && now - t.last_ms <= stale_ms;
}
// On-change virtual sensor (temperature, humidity): its own frame, or the BME688 still
// reporting through its continuous outputs once a first value has arrived.
static bool fresh_on_change(const Track& t, uint32_t now) {
  return fresh(t, now, kStaleBmeMs) ||
         (t.seen && (fresh(t_gas, now, kStaleBmeMs) || fresh(t_bsec, now, kStaleBmeMs)));
}

// ---- 6-DOF windows (page 2) ---------------------------------------------------------------
static const uint32_t kWindowMs = 1000;
static const uint8_t kWinExpected = 25;                 // 25 Hz x 1 s
static const uint8_t kWinMinValid = 20;                 // b1
static const uint8_t kWinCap = 64;                      // samples buffered per sensor
static const int16_t kNearFullScale = 32000;            // b2 / b3
static int16_t g_acc_buf[kWinCap][3], g_gyr_buf[kWinCap][3];
static uint8_t g_n_acc = 0, g_n_gyr = 0;
static bool g_overrun = false, g_acc_sat = false, g_gyr_sat = false;
static uint32_t g_win_t0 = 0;
struct Window {                                         // the last completed window
  uint8_t seq = 0, n = 0, flags = 0;                    // flags: page-2 b1..b4
  int16_t acc_mean[3] = {0, 0, 0};
  uint16_t vib_rms = 0, vib_peak = 0;
  int16_t rate_mean[3] = {0, 0, 0};
  uint16_t rate_peak = 0;
};
static Window g_win;
static const uint8_t kF_GRV = 0x01, kF_STATS = 0x02, kF_ACC_SAT = 0x04, kF_GYRO_SAT = 0x08,
                     kF_MISSED = 0x10, kF_GRV_PRESENT = 0x20, kF_ACC_RANGE = 0x40;

static uint8_t crc8(const uint8_t* d, size_t n) {
  uint8_t c = 0;
  for (size_t i = 0; i < n; ++i) {
    c ^= d[i];
    for (int b = 0; b < 8; ++b) c = (c & 0x80) ? (uint8_t)((c << 1) ^ 0x07) : (uint8_t)(c << 1);
  }
  return c;
}
static void put16(uint8_t* p, uint16_t v) { p[0] = (uint8_t)(v >> 8); p[1] = (uint8_t)(v & 0xFF); }
static void put32(uint8_t* p, uint32_t v) { put16(p, (uint16_t)(v >> 16)); put16(p + 2, (uint16_t)(v & 0xFFFF)); }
static int16_t sat16(float v) {
  if (v > 32767.0f) return 32767;
  if (v < -32768.0f) return -32768;
  return (int16_t)lroundf(v);
}
static uint16_t satu16(float v) {                       // 0..65534 (0xFFFF stays free)
  if (!(v > 0.0f)) return 0;
  if (v >= 65534.0f) return 65534;
  return (uint16_t)lroundf(v);
}
static bool near_fs(int16_t x, int16_t y, int16_t z) {
  return x >= kNearFullScale || x <= -kNearFullScale || y >= kNearFullScale || y <= -kNearFullScale ||
         z >= kNearFullScale || z <= -kNearFullScale;
}

// Reads the current dynamic ranges of the vector sensors (0 = not reported yet;
// the previous or default scale is then kept).
static void read_ranges() {
  const SensorConfig ca = accel.getConfiguration();
  const SensorConfig cg = gyro.getConfiguration();
  const SensorConfig cm = mag.getConfiguration();
  if (ca.range) {
    g_acc_mg_per_lsb = ca.range * 1000.0f / 32768.0f;
    g_acc_range_known = true;
  }
  if (cg.range) g_gyro_ddps_per_lsb = cg.range * 10.0f / 32768.0f;
  if (cm.range) {
    g_mag_dut_per_lsb = cm.range * 10.0f / 32768.0f;
    g_mag_range_known = true;
  }
}

static void on_receive(int n) {               // host selects the page
  if (n > 0) {
    const int v = Wire.read();
    g_page_sel = (uint8_t)((v >= 0 && v < kPages) ? v : 0);
  }
  while (Wire.available()) Wire.read();
}

static void on_request() {
  uint8_t tmp[32];
  const uint8_t sel = g_page_sel < kPages ? g_page_sel : 0;
  noInterrupts();
  for (int i = 0; i < 32; ++i) tmp[i] = g_pages[sel][i];
  interrupts();
  Wire.write(tmp, sizeof tmp);
}

// Closes the current 1 s window: two-pass statistics of the buffered samples, then a new
// empty window. Scaled with the ranges in force at the end of the window.
static void close_window() {
  Window w;
  w.seq = (uint8_t)(g_win.seq + 1u);
  const uint8_t na = g_n_acc, ng = g_n_gyr;
  w.n = na < ng ? na : ng;
  uint8_t f = 0;
  if (na >= kWinMinValid && ng >= kWinMinValid) f |= kF_STATS;
  if (g_acc_sat) f |= kF_ACC_SAT;
  if (g_gyr_sat) f |= kF_GYRO_SAT;
  if (na + 2 < kWinExpected || ng + 2 < kWinExpected || g_overrun) f |= kF_MISSED;
  if (na) {
    int32_t sum[3] = {0, 0, 0};
    for (uint8_t k = 0; k < na; ++k)
      for (uint8_t i = 0; i < 3; ++i) sum[i] += g_acc_buf[k][i];
    float mean[3], var = 0.0f, peak2 = 0.0f;
    for (uint8_t i = 0; i < 3; ++i) mean[i] = (float)sum[i] / (float)na;
    for (uint8_t k = 0; k < na; ++k) {
      float d2 = 0.0f;
      for (uint8_t i = 0; i < 3; ++i) {
        const float d = (float)g_acc_buf[k][i] - mean[i];
        d2 += d * d;
      }
      var += d2;                                        // sum over axes of the squared deviations
      if (d2 > peak2) peak2 = d2;
    }
    var /= (float)na;                                   // population variance, summed over axes
    for (uint8_t i = 0; i < 3; ++i) w.acc_mean[i] = sat16(mean[i] * g_acc_mg_per_lsb);
    w.vib_rms = satu16(sqrtf(var) * g_acc_mg_per_lsb * 10.0f);
    w.vib_peak = satu16(sqrtf(peak2) * g_acc_mg_per_lsb);
  }
  if (ng) {
    int32_t sum[3] = {0, 0, 0};
    float peak2 = 0.0f;
    for (uint8_t k = 0; k < ng; ++k) {
      float m2 = 0.0f;
      for (uint8_t i = 0; i < 3; ++i) {
        sum[i] += g_gyr_buf[k][i];
        m2 += (float)g_gyr_buf[k][i] * (float)g_gyr_buf[k][i];
      }
      if (m2 > peak2) peak2 = m2;
    }
    const float cdps_per_lsb = g_gyro_ddps_per_lsb * 10.0f;   // 0.01 deg/s
    for (uint8_t i = 0; i < 3; ++i) {
      const float r = (float)sum[i] / (float)ng * cdps_per_lsb;
      if (r > 32767.0f || r < -32767.0f) f |= kF_GYRO_SAT;    // +/-327.67 deg/s exceeded
      w.rate_mean[i] = r > 32767.0f ? 32767 : r < -32767.0f ? -32767 : (int16_t)lroundf(r);
    }
    w.rate_peak = satu16(sqrtf(peak2) * g_gyro_ddps_per_lsb);
  }
  w.flags = f;
  g_win = w;
  g_n_acc = g_n_gyr = 0;
  g_overrun = g_acc_sat = g_gyr_sat = false;
}

void setup() {
  Serial.begin(115200);
  BHY2.begin(NICLA_STANDALONE);
  rotation.begin(25, 0);                      // 25 Hz fusion output
  accel.begin(25, 0);
  gyro.begin(25, 0);
  mag.begin(25, 0);
  pressure.begin(1, 0);
  temperature.begin(1, 0);
  humidity.begin(1, 0);
  gas.begin(1, 0);
  bsec.begin(1, 0);                           // BSEC low-power (3 s) cadence internally
  g_grv_present = game.begin(25, 0);          // 10th and last object; false: not in the firmware
  delay(1000);                                // let the BHI260 apply the configuration
  BHY2.update();
  read_ranges();
  for (int i = 0; i < NUM_SUPPORTEND_SENSOR; ++i) {   // the library's own barometer factor
    if (SensorList[i].id == SENSOR_ID_BARO && SensorList[i].scaleFactor > 0.0f) {
      g_baro_lib_scale = SensorList[i].scaleFactor;
      break;
    }
  }
  Wire.begin(kI2cAddr);                       // I2C target: J2 header pins = ESLOV J5 (same bus)
  Wire.onReceive(on_receive);
  Wire.onRequest(on_request);
  g_win_t0 = millis();
  Serial.print("VGQ-1 ARU (Nicla Sense ME) ready at I2C 0x2A (J2 / ESLOV), accel +/-");
  Serial.print(g_acc_mg_per_lsb * 32768.0f / 1000.0f);
  Serial.print(g_acc_range_known ? " g" : " g (default)");
  Serial.print(", gyro +/-");
  Serial.print(g_gyro_ddps_per_lsb * 32768.0f / 10.0f);
  Serial.print(" dps, mag +/-");              // logged for test T-SC-06
  Serial.print(g_mag_dut_per_lsb * 32768.0f / 10.0f);
  Serial.println(g_mag_range_known ? " uT" : " uT (fallback, range not reported)");
  Serial.print("6-DOF: game rotation vector ");
  Serial.print(g_grv_present ? "present" : "ABSENT in the BHI260AP firmware");
  Serial.println(", windows 1.000 s of 25 Hz accel + gyro samples (page 2)");
}

void loop() {
  BHY2.update();
  const uint32_t now = millis();
  // Every new sample, every pass: the library keeps only the latest one.
  if (rotation.take()) note(t_rv, now);
  if (accel.take()) {
    note(t_acc, now);
    const int16_t x = accel.x(), y = accel.y(), z = accel.z();
    if (near_fs(x, y, z)) g_acc_sat = true;
    if (g_n_acc < kWinCap) {
      g_acc_buf[g_n_acc][0] = x; g_acc_buf[g_n_acc][1] = y; g_acc_buf[g_n_acc][2] = z;
      ++g_n_acc;
    } else {
      g_overrun = true;
    }
  }
  if (gyro.take()) {
    note(t_gyro, now);
    const int16_t x = gyro.x(), y = gyro.y(), z = gyro.z();
    if (near_fs(x, y, z)) g_gyr_sat = true;
    if (g_n_gyr < kWinCap) {
      g_gyr_buf[g_n_gyr][0] = x; g_gyr_buf[g_n_gyr][1] = y; g_gyr_buf[g_n_gyr][2] = z;
      ++g_n_gyr;
    } else {
      g_overrun = true;
    }
  }
  if (mag.take()) note(t_mag, now);
  if (pressure.take()) note(t_baro, now);
  if (temperature.take()) note(t_temp, now);
  if (humidity.take()) note(t_hum, now);
  if (gas.take()) note(t_gas, now);
  if (bsec.take()) note(t_bsec, now);
  if (game.take()) note(t_grv, now);
  if (now - g_win_t0 >= kWindowMs) {
    close_window();
    g_win_t0 += kWindowMs;
    if (now - g_win_t0 >= kWindowMs) g_win_t0 = now;   // loop held up > 1 s: restart the grid
  }

  static uint32_t last = 0;
  if (now - last < 100) return;               // rebuild the three pages at 10 Hz
  last = now;
  ++g_seq;
  if ((!g_mag_range_known || !g_acc_range_known) && (g_seq % 10) == 0) read_ranges();   // retry 1/s
  static bool start_report_done = false;
  if (!start_report_done && now >= 10000u) {  // once, 10 s after power-up
    start_report_done = true;
    read_ranges();                            // once more: the BHI260AP may report them late
    Serial.print("samples since start: rv "); // cadence check (header "Validity")
    Serial.print(t_rv.count); Serial.print(" acc "); Serial.print(t_acc.count);
    Serial.print(" gyro "); Serial.print(t_gyro.count); Serial.print(" mag "); Serial.print(t_mag.count);
    Serial.print(" grv "); Serial.print(t_grv.count); Serial.print(" baro "); Serial.print(t_baro.count);
    Serial.print(" temp "); Serial.print(t_temp.count); Serial.print(" hum "); Serial.print(t_hum.count);
    Serial.print(" gas "); Serial.print(t_gas.count); Serial.print(" bsec "); Serial.println(t_bsec.count);
  }

  uint8_t m[32] = {0};
  uint8_t f = 0;
  m[0] = 0xA7;
  m[1] = g_seq;
  const float qw = rotation.w(), qx = rotation.x(), qy = rotation.y(), qz = rotation.z();
  if (fresh(t_rv, now, kStaleMotionMs) && qw * qw + qx * qx + qy * qy + qz * qz > 0.25f) f |= 0x01;
  put16(m + 3, (uint16_t)sat16(qw * 16384.0f));
  put16(m + 5, (uint16_t)sat16(qx * 16384.0f));
  put16(m + 7, (uint16_t)sat16(qy * 16384.0f));
  put16(m + 9, (uint16_t)sat16(qz * 16384.0f));
  const float acc_mrad = rotation.accuracy() * 1000.0f;
  put16(m + 11, acc_mrad >= 65535.0f ? 0xFFFE : (uint16_t)lroundf(acc_mrad));
  const int16_t ax = accel.x(), ay = accel.y(), az = accel.z();
  if (fresh(t_acc, now, kStaleMotionMs) && (ax || ay || az)) f |= 0x02;
  put16(m + 13, (uint16_t)sat16(ax * g_acc_mg_per_lsb));
  put16(m + 15, (uint16_t)sat16(ay * g_acc_mg_per_lsb));
  put16(m + 17, (uint16_t)sat16(az * g_acc_mg_per_lsb));
  if (fresh(t_gyro, now, kStaleMotionMs)) f |= 0x04;   // a gyro at rest legitimately reads ~0
  put16(m + 19, (uint16_t)sat16(gyro.x() * g_gyro_ddps_per_lsb));
  put16(m + 21, (uint16_t)sat16(gyro.y() * g_gyro_ddps_per_lsb));
  put16(m + 23, (uint16_t)sat16(gyro.z() * g_gyro_ddps_per_lsb));
  const int16_t mx = mag.x(), my = mag.y(), mz = mag.z();
  if (fresh(t_mag, now, kStaleMotionMs) && (mx || my || mz)) f |= 0x08;
  if (g_mag_range_known) f |= 0x10;
  put16(m + 25, (uint16_t)sat16(mx * g_mag_dut_per_lsb));
  put16(m + 27, (uint16_t)sat16(my * g_mag_dut_per_lsb));
  put16(m + 29, (uint16_t)sat16(mz * g_mag_dut_per_lsb));
  m[2] = f;
  m[31] = crc8(m, 31);

  uint8_t e[32] = {0};
  uint8_t g = 0;
  e[0] = 0xA8;
  e[1] = g_seq;
  // value() = raw * library factor; raw = 1/128 Pa (see header).
  const float p_pa = pressure.value() / g_baro_lib_scale * (1.0f / 128.0f);
  if (fresh(t_baro, now, kStaleBaroMs) && p_pa >= kPressureMinPa && p_pa <= kPressureMaxPa) {
    g |= 0x01;
    put32(e + 3, (uint32_t)lroundf(p_pa));
  }
  if (fresh(t_bsec, now, kStaleBmeMs) && (bsec.accuracy() > 0 || bsec.iaq() > 0)) g |= 0x02;
  if (fresh_on_change(t_temp, now)) g |= 0x04;
  put16(e + 7, (uint16_t)sat16(temperature.value() * 100.0f));
  const float h = humidity.value();
  if (fresh_on_change(t_hum, now) && h > 0.0f) g |= 0x08;
  put16(e + 9, (uint16_t)lroundf(h * 100.0f));
  put16(e + 11, bsec.iaq());
  put16(e + 13, bsec.iaq_s());
  put32(e + 15, bsec.co2_eq());
  put16(e + 19, (uint16_t)lroundf(bsec.b_voc_eq() * 100.0f));
  const float r_gas = gas.value();            // ohm
  if (fresh(t_gas, now, kStaleBmeMs) && r_gas > 0.0f) g |= 0x10;
  put32(e + 21, (uint32_t)lroundf(r_gas));
  e[25] = bsec.accuracy();
  e[2] = g;
  e[31] = crc8(e, 31);

  uint8_t s[32] = {0};
  uint8_t k = (uint8_t)(g_win.flags & (kF_STATS | kF_ACC_SAT | kF_GYRO_SAT | kF_MISSED));
  s[0] = 0xA9;
  s[1] = g_seq;
  const float gw = game.w(), gx = game.x(), gy = game.y(), gz = game.z();
  if (g_grv_present && fresh(t_grv, now, kStaleMotionMs) && gw * gw + gx * gx + gy * gy + gz * gz > 0.25f)
    k |= kF_GRV;
  if (g_grv_present) k |= kF_GRV_PRESENT;
  if (g_acc_range_known) k |= kF_ACC_RANGE;
  put16(s + 3, (uint16_t)sat16(gw * 16384.0f));
  put16(s + 5, (uint16_t)sat16(gx * 16384.0f));
  put16(s + 7, (uint16_t)sat16(gy * 16384.0f));
  put16(s + 9, (uint16_t)sat16(gz * 16384.0f));
  s[11] = g_win.seq;
  s[12] = g_win.n;
  for (int i = 0; i < 3; ++i) {
    put16(s + 13 + 2 * i, (uint16_t)g_win.acc_mean[i]);
    put16(s + 23 + 2 * i, (uint16_t)g_win.rate_mean[i]);
  }
  put16(s + 19, g_win.vib_rms);
  put16(s + 21, g_win.vib_peak);
  put16(s + 29, g_win.rate_peak);
  s[2] = k;
  s[31] = crc8(s, 31);

  noInterrupts();
  for (int i = 0; i < 32; ++i) { g_pages[0][i] = m[i]; g_pages[1][i] = e[i]; g_pages[2][i] = s[i]; }
  interrupts();
}
