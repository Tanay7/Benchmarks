// =============================================================================
//  VGQ-1 Attitude Reference Unit (ARU) — Arduino Nicla Sense ME firmware
//
//  The Nicla's BHI260AP smart sensor runs Bosch's on-chip sensor fusion; this
//  sketch exposes its rotation-vector quaternion plus the BMP390 / BME688 (BSEC)
//  environment outputs to the spacecraft computer as a fixed 36-octet frame on
//  I2C (Nicla = I2C TARGET at 0x2A on the ESLOV/I2C pins). This mirrors how a
//  real spacecraft's attitude reference unit delivers a pre-computed solution
//  to the C&DH computer over a bus.
//
//  Frame (big-endian):
//    0 magic 0xA7 | 1 seq | 2 flags | 3 quat accuracy (0..3)
//    4..11  qw qx qy qz  int16 x 16384
//    12..15 pressure     uint32 hPa x 100
//    16..17 temperature  int16  degC x 100
//    18..19 humidity     uint16 %RH x 100
//    20..21 BSEC IAQ     uint16
//    22..25 CO2 equiv.   uint32 ppm
//    26..27 bVOC equiv.  uint16 ppm x 100
//    28..31 gas resist.  uint32 ohm
//    32 BSEC accuracy | 33,34 reserved | 35 CRC-8 (poly 0x07, init 0) over 0..34
//  flags: b0 quaternion, b1 pressure, b2 BSEC, b3 temperature, b4 humidity
//
//  Libraries: Arduino_BHY2 (Nicla Sense ME core). BHY2.begin() also enables the
//  3.3 V I/O rail (nicla::enable3V3LDO), so the ESLOV/I2C pins are 3.3 V logic —
//  compatible with the UNO Q Qwiic bus.
//  NOTE: NICLA_STANDALONE keeps the library from claiming the I2C bus itself
//  (its own ESLOV host protocol would use address 0x55).
// =============================================================================
#include "Arduino.h"
#include "Arduino_BHY2.h"
#include <Wire.h>

static const uint8_t kI2cAddr = 0x2A;

SensorQuaternion rotation(SENSOR_ID_RV);
Sensor pressure(SENSOR_ID_BARO);
Sensor temperature(SENSOR_ID_TEMP);
Sensor humidity(SENSOR_ID_HUM);
SensorBSEC bsec(SENSOR_ID_BSEC);

static volatile uint8_t g_frame[36];
static uint8_t g_seq = 0;

static uint8_t crc8(const uint8_t* d, size_t n) {
  uint8_t c = 0;
  for (size_t i = 0; i < n; ++i) {
    c ^= d[i];
    for (int b = 0; b < 8; ++b) c = (c & 0x80) ? (uint8_t)((c << 1) ^ 0x07) : (uint8_t)(c << 1);
  }
  return c;
}

static void put16(uint8_t* p, uint16_t v) { p[0] = v >> 8; p[1] = v & 0xFF; }
static void put32(uint8_t* p, uint32_t v) { put16(p, v >> 16); put16(p + 2, v & 0xFFFF); }

static void on_request() {
  // Copy under the same interrupt context: the frame is rebuilt atomically below.
  uint8_t tmp[36];
  noInterrupts();
  for (int i = 0; i < 36; ++i) tmp[i] = g_frame[i];
  interrupts();
  Wire.write(tmp, sizeof tmp);
}

void setup() {
  Serial.begin(115200);
  BHY2.begin(NICLA_STANDALONE);
  rotation.begin(25, 0);        // 25 Hz fusion output
  pressure.begin(1, 0);
  temperature.begin(1, 0);
  humidity.begin(1, 0);
  bsec.begin(1, 0);             // BSEC low-power (3 s) cadence internally
  Wire.begin(kI2cAddr);         // I2C target on the ESLOV/I2C pins
  Wire.onRequest(on_request);
  Serial.println("VGQ-1 ARU (Nicla Sense ME) ready at I2C 0x2A");
}

void loop() {
  BHY2.update();
  static uint32_t last = 0;
  if (millis() - last < 100) return;   // rebuild the frame at 10 Hz
  last = millis();

  uint8_t f[36] = {0};
  uint8_t flags = 0;
  f[0] = 0xA7;
  f[1] = ++g_seq;
  const float qw = rotation.w(), qx = rotation.x(), qy = rotation.y(), qz = rotation.z();
  const float qn = sqrtf(qw * qw + qx * qx + qy * qy + qz * qz);
  if (qn > 0.5f) flags |= 0x01;
  f[3] = (uint8_t)rotation.accuracy();
  put16(f + 4, (uint16_t)(int16_t)lroundf(qw * 16384));
  put16(f + 6, (uint16_t)(int16_t)lroundf(qx * 16384));
  put16(f + 8, (uint16_t)(int16_t)lroundf(qy * 16384));
  put16(f + 10, (uint16_t)(int16_t)lroundf(qz * 16384));
  const float p = pressure.value();          // hPa
  if (p > 100.0f) flags |= 0x02;
  put32(f + 12, (uint32_t)lroundf(p * 100.0f));
  const float t = temperature.value();
  flags |= 0x08;
  put16(f + 16, (uint16_t)(int16_t)lroundf(t * 100.0f));
  const float h = humidity.value();
  if (h > 0.0f) flags |= 0x10;
  put16(f + 18, (uint16_t)lroundf(h * 100.0f));
  if (bsec.accuracy() > 0) flags |= 0x04;
  put16(f + 20, bsec.iaq());
  put32(f + 22, bsec.co2_eq());
  put16(f + 26, (uint16_t)lroundf(bsec.b_voc_eq() * 100.0f));
  put32(f + 28, bsec.comp_g());
  f[32] = bsec.accuracy();
  f[2] = flags;
  f[35] = crc8(f, 35);

  noInterrupts();
  for (int i = 0; i < 36; ++i) g_frame[i] = f[i];
  interrupts();
}
