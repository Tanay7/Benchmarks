// =============================================================================
//  VGQ-1 Attitude Reference Unit (ARU) - Arduino Nicla Sense ME firmware
//
//  The Nicla's BHI260AP smart sensor runs Bosch's on-chip sensor fusion; this
//  sketch exposes the fused rotation vector, the corrected accelerometer,
//  gyroscope and magnetometer (BMM150), the BMP390 pressure and the BME688 /
//  BSEC environment outputs to the spacecraft computer (UNO Q) as two fixed
//  32-octet pages on I2C. The Nicla is an I2C TARGET at 0x2A on its J2 header
//  I2C pins (SDA pin 1, SCL pin 2), level-translated to VDDIO_EXT = 3.3 V.
//  This mirrors how a real attitude reference unit hands a pre-computed
//  solution to the C&DH computer over a bus.
//
//  Protocol: the host writes ONE octet (page number 0 or 1), then reads 32.
//  Pages are 32 octets so that no I2C buffer anywhere needs to exceed 32.
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
//    * SensorQuaternion::accuracy() is an angle estimate (radians), not 0..3;
//    * with the default SENSOR_DATA_FIXED_LENGTH (10) the BSEC frame delivers
//      IAQ, static IAQ, bVOC, CO2 and accuracy; gas resistance therefore comes
//      from the separate gas virtual sensor.
//  Bosch datasheets: BMM150 range +/-1300 uT (x, y), +/-2500 uT (z), 0.3 uT
//  resolution; BMP390 operating range 300..1250 hPa; BME688 0..100 %RH, IAQ 0..500.
//  NICLA_STANDALONE keeps the library from claiming the I2C bus itself.
// =============================================================================
#include "Arduino.h"
#include "Arduino_BHY2.h"
#include <Wire.h>

static const uint8_t kI2cAddr = 0x2A;

SensorQuaternion rotation(SENSOR_ID_RV);
SensorXYZ accel(SENSOR_ID_ACC);
SensorXYZ gyro(SENSOR_ID_GYRO);
SensorXYZ mag(SENSOR_ID_MAG);
Sensor pressure(SENSOR_ID_BARO);
Sensor temperature(SENSOR_ID_TEMP);
Sensor humidity(SENSOR_ID_HUM);
Sensor gas(SENSOR_ID_GAS);
SensorBSEC bsec(SENSOR_ID_BSEC);

static volatile uint8_t g_pages[2][32];
static volatile uint8_t g_page_sel = 0;
static uint8_t g_seq = 0;
static float g_acc_mg_per_lsb = 8000.0f / 32768.0f;     // replaced by the configured range
static float g_gyro_ddps_per_lsb = 20000.0f / 32768.0f;
static float g_mag_dut_per_lsb = 1.0f / 16.0f * 10.0f;  // 0.1 uT per LSB; fallback 1/16 uT/LSB
static bool g_mag_range_known = false;
static float g_baro_lib_scale = 0.0078f;                // Arduino_BHY2 factor, looked up in setup()

// BMP390 full-accuracy operating range, Pa (datasheet: 300..1250 hPa).
static const float kPressureMinPa = 30000.0f;
static const float kPressureMaxPa = 125000.0f;

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

// Reads the current dynamic ranges of the vector sensors (0 = not reported yet;
// the previous or default scale is then kept).
static void read_ranges() {
  const SensorConfig ca = accel.getConfiguration();
  const SensorConfig cg = gyro.getConfiguration();
  const SensorConfig cm = mag.getConfiguration();
  if (ca.range) g_acc_mg_per_lsb = ca.range * 1000.0f / 32768.0f;
  if (cg.range) g_gyro_ddps_per_lsb = cg.range * 10.0f / 32768.0f;
  if (cm.range) {
    g_mag_dut_per_lsb = cm.range * 10.0f / 32768.0f;
    g_mag_range_known = true;
  }
}

static void on_receive(int n) {               // host selects the page
  if (n > 0) g_page_sel = (uint8_t)(Wire.read() & 1);
  while (Wire.available()) Wire.read();
}

static void on_request() {
  uint8_t tmp[32];
  const uint8_t sel = g_page_sel;
  noInterrupts();
  for (int i = 0; i < 32; ++i) tmp[i] = g_pages[sel][i];
  interrupts();
  Wire.write(tmp, sizeof tmp);
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
  delay(1000);                                // let the BHI260 apply the configuration
  BHY2.update();
  read_ranges();
  for (int i = 0; i < NUM_SUPPORTEND_SENSOR; ++i) {   // the library's own barometer factor
    if (SensorList[i].id == SENSOR_ID_BARO && SensorList[i].scaleFactor > 0.0f) {
      g_baro_lib_scale = SensorList[i].scaleFactor;
      break;
    }
  }
  Wire.begin(kI2cAddr);                       // I2C target on the J2 header pins
  Wire.onReceive(on_receive);
  Wire.onRequest(on_request);
  Serial.print("VGQ-1 ARU (Nicla Sense ME) ready at I2C 0x2A, accel +/-");
  Serial.print(g_acc_mg_per_lsb * 32768.0f / 1000.0f);
  Serial.print(" g, gyro +/-");
  Serial.print(g_gyro_ddps_per_lsb * 32768.0f / 10.0f);
  Serial.print(" dps, mag +/-");              // logged for test T-SC-06
  Serial.print(g_mag_dut_per_lsb * 32768.0f / 10.0f);
  Serial.println(g_mag_range_known ? " uT" : " uT (fallback, range not reported)");
}

void loop() {
  BHY2.update();
  static unsigned long last = 0;
  if (millis() - last < 100) return;          // rebuild both pages at 10 Hz
  last = millis();
  ++g_seq;
  if (!g_mag_range_known && (g_seq % 10) == 0) read_ranges();   // retry once per second

  uint8_t m[32] = {0};
  uint8_t f = 0;
  m[0] = 0xA7;
  m[1] = g_seq;
  const float qw = rotation.w(), qx = rotation.x(), qy = rotation.y(), qz = rotation.z();
  if (qw * qw + qx * qx + qy * qy + qz * qz > 0.25f) f |= 0x01;
  put16(m + 3, (uint16_t)sat16(qw * 16384.0f));
  put16(m + 5, (uint16_t)sat16(qx * 16384.0f));
  put16(m + 7, (uint16_t)sat16(qy * 16384.0f));
  put16(m + 9, (uint16_t)sat16(qz * 16384.0f));
  const float acc_mrad = rotation.accuracy() * 1000.0f;
  put16(m + 11, acc_mrad >= 65535.0f ? 0xFFFE : (uint16_t)lroundf(acc_mrad));
  const int16_t ax = accel.x(), ay = accel.y(), az = accel.z();
  if (ax || ay || az) f |= 0x02;
  put16(m + 13, (uint16_t)sat16(ax * g_acc_mg_per_lsb));
  put16(m + 15, (uint16_t)sat16(ay * g_acc_mg_per_lsb));
  put16(m + 17, (uint16_t)sat16(az * g_acc_mg_per_lsb));
  f |= 0x04;                                  // a gyro at rest legitimately reads ~0
  put16(m + 19, (uint16_t)sat16(gyro.x() * g_gyro_ddps_per_lsb));
  put16(m + 21, (uint16_t)sat16(gyro.y() * g_gyro_ddps_per_lsb));
  put16(m + 23, (uint16_t)sat16(gyro.z() * g_gyro_ddps_per_lsb));
  const int16_t mx = mag.x(), my = mag.y(), mz = mag.z();
  if (mx || my || mz) f |= 0x08;
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
  if (p_pa >= kPressureMinPa && p_pa <= kPressureMaxPa) {
    g |= 0x01;
    put32(e + 3, (uint32_t)lroundf(p_pa));
  }
  if (bsec.accuracy() > 0 || bsec.iaq() > 0) g |= 0x02;
  g |= 0x04;
  put16(e + 7, (uint16_t)sat16(temperature.value() * 100.0f));
  const float h = humidity.value();
  if (h > 0.0f) g |= 0x08;
  put16(e + 9, (uint16_t)lroundf(h * 100.0f));
  put16(e + 11, bsec.iaq());
  put16(e + 13, bsec.iaq_s());
  put32(e + 15, bsec.co2_eq());
  put16(e + 19, (uint16_t)lroundf(bsec.b_voc_eq() * 100.0f));
  const float r_gas = gas.value();            // ohm
  if (r_gas > 0.0f) g |= 0x10;
  put32(e + 21, (uint32_t)lroundf(r_gas));
  e[25] = bsec.accuracy();
  e[2] = g;
  e[31] = crc8(e, 31);

  noInterrupts();
  for (int i = 0; i < 32; ++i) { g_pages[0][i] = m[i]; g_pages[1][i] = e[i]; }
  interrupts();
}
