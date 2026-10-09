// =============================================================================
//  VGQ-1 flight software build configuration — Arduino UNO Q (STM32U585 MCU)
//
//  Every pin/bus here is documented in docs/03_Hardware_Integration.md. Change a
//  value here and in that document together.
// =============================================================================
#pragma once
#include <stdint.h>

// ---- Radio UART selection ------------------------------------------------------
// The UNO Q's D0/D1 UART (USART1 = Serial1) is ALSO the Zephyr console: the
// bootloader banner and any runtime kernel log messages are printed on it. A
// transparent radio on that UART would transmit those bytes over the air and a
// log line printed mid-CADU would corrupt the frame.
// Default: USART3 = Serial3, routed to the header pins silk-screened SDA/SCL
// (PB11 = RX, PB10 = TX). Those pins are then a UART — never call Wire.begin();
// all sensors live on the Qwiic bus (Wire1). Set to 1 to use D0/D1 instead.
#ifndef VGQ_RADIO_ON_SERIAL1
#define VGQ_RADIO_ON_SERIAL1 0
#endif
#if VGQ_RADIO_ON_SERIAL1
#define RADIO_SERIAL Serial1          // D1 = TX -> E22 RXD, D0 = RX <- E22 TXD
#else
#define RADIO_SERIAL Serial3          // "SCL"(PB10) = TX -> E22 RXD, "SDA"(PB11) = RX <- E22 TXD
#endif

// ---- E22-400T37S control pins ------------------------------------------------------
// Fit 10 kOhm pull-ups (to 3.3 V) on M0 and M1: while the MCU boots and its pins
// float, the module then sits in deep-sleep (M1=M0=1) and cannot key the 5 W PA.
static const int PIN_E22_M0  = 2;     // D2 (PB3)
static const int PIN_E22_M1  = 3;     // D3 (PB0)
static const int PIN_E22_AUX = 7;     // D7 (PB2)  input, module busy = LOW

// ---- Sensor bus ---------------------------------------------------------------------
#define SENSOR_WIRE Wire1                       // Qwiic connector = I2C4 (PD12 SCL / PD13 SDA), 3.3 V
static const uint32_t kI2cClockHz   = 100000;   // 100 kHz: long boom cable + mux
static const uint8_t  kTcaAddr      = 0x70;
static const int      PIN_TCA_RESET = 8;        // D8 -> TCA9548A /RESET (optional, bus recovery)

// TCA9548A channel map
enum MuxChannel : int8_t {
  CH_IMU = 0,        // MPU-9250 (0x68) + AK8963 (0x0C via bypass)
  CH_MAG_OB = 1,     // RM3100 outboard (0x20) — boom tip
  CH_MAG_IB = 2,     // RM3100 inboard  (0x20) — boom mid-point
  CH_BODY = 3,       // MMC5603 (0x30), BME690 (0x76), INA226 (0x40)
  CH_SPEC1 = 4,      // AS7265x (0x49)
  CH_SPEC2 = 5,      // AS7343 (0x39), VEML7700 (0x10)
  CH_NENV = 6,       // Nicla Sense Env (0x21)
  CH_ARU = 7,        // Nicla Sense ME attitude reference unit (0x2A, custom firmware)
};

// ---- Analog inputs (12-bit) -----------------------------------------------------------
static const int PIN_CSS[4]   = {A0, A1, A2, A3};   // pyramid coarse sun sensor cells
static const int PIN_PA_NTC   = A4;                 // 10k NTC (B=3950) to GND, 10k to 3.3 V
static const int PIN_VRADIO   = A5;                 // radio supply via 100k/22k divider
static const float kVradioDivider = (100.0f + 22.0f) / 22.0f;
static const float kAdcRef = 3.3f;

// ---- Optional local OLED (SSD1309 2.42", SPI) on the spacecraft -------------------------
#ifndef VGQ_SC_OLED
#define VGQ_SC_OLED 0                    // 1 = fit a second 2.42" OLED to the spacecraft
#endif
static const int PIN_OLED_CS = 10, PIN_OLED_DC = 9, PIN_OLED_RES = 6;   // SCK D13, MOSI D11

// ---- Radio defaults (MUST match ground/config/station.toml [radio]) ----------------------
static const uint8_t  kE22Channel      = 23;     // 410.125 + 23 = 433.125 MHz  (check band plan!)
static const uint8_t  kE22AirRate      = 2;      // 0=0.3k 1=1.2k 2=2.4k 3=4.8k 4=9.6k ...
static const uint8_t  kE22PowerCode    = 0;      // 0 = 37 dBm (max) ... 3 = min. TEST mode forces 3
static const uint16_t kE22Address      = 0x0000; // same on both ends (transparent mode)
static const uint8_t  kE22NetId        = 0x00;

// ---- Timing ----------------------------------------------------------------------------------
static const uint32_t kMinFramePeriodMs  = 1500;
static const uint16_t kMaxTxDutyPermille = 500;  // PA thermal limit: transmit <= 50 % of the time
static const uint32_t kAuxTimeoutMs      = 30000;
static const uint32_t kNoiseSampleMs     = 15000; // ambient-noise RSSI sampling interval
static const uint32_t kDefaultCmdLossS   = 86400; // command-loss timer (24 h), 0 = off

// ---- Thermal limits (fault protection) --------------------------------------------------------
static const float kPaTempYellowC = 65.0f, kPaTempRedC = 80.0f;
static const float kAviTempRedC = 75.0f;

// ---- Local geomagnetic field for TRIAD (look up yours: NOAA / BGS WMM calculator) -------------
static const float kGeomagInclDeg = 60.0f;       // + = field points down (northern hemisphere)
static const float kGeomagDeclDeg = 0.0f;        // + = east of true north

// ---- Coarse sun sensor dark offsets (counts) — measure with the cells covered -----------------
static const float kCssDark[4] = {20, 20, 20, 20};
static const float kCssMinSignal = 60;

// ---- Callsign (amateur-radio station identification, sent in clear text) ---------------------
// Under amateur rules you must identify periodically; the ID is sent in an EVR.
#define VGQ_CALLSIGN "N0CALL"
static const uint32_t kCallsignPeriodS = 600;   // every 10 minutes
