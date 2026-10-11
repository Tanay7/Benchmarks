// =============================================================================
//  VGQ-1 flight software build configuration — Arduino UNO Q (STM32U585 MCU)
//
//  Every pin/bus here is documented in docs/03_Hardware_Integration.md. Change a
//  value here and in that document together.
// =============================================================================
#pragma once
#include <stddef.h>
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

// ---- E22-400T37S control pins (E22-400TBH-02 board, 10-pin header) -----------------
// Remove the board's M0/M1 jumper caps (they short M1-GND and GND-M0) and its
// RXD/TXD caps (they connect the on-board CH340X USB-UART to the module), then
// wire: header 10 M0 <- D2, 7 M1 <- D3, 4 AUX -> D7, 5 TXD -> "SDA", 6 RXD <- "SCL".
// With the caps removed the module's own pull-ups take M0/M1 HIGH (mode 3, deep
// sleep), but EBYTE specifies them as "very weak" (T37S manual v1.5, pins 9/10):
// fit 10 kOhm pull-ups to 3.3 V on M0 and M1 so the module reliably sleeps - and
// cannot key the 5 W PA - while the MCU boots and its pins float.
static const int PIN_E22_M0  = 2;     // D2 (PB3)
static const int PIN_E22_M1  = 3;     // D3 (PB0)
static const int PIN_E22_AUX = 7;     // D7 (PB2)  input, module busy = LOW

// ---- Sensor bus: four instruments, one Qwiic I2C bus, no multiplexer ---------------
//   Nicla Sense ME  0x2A  attitude reference unit (custom firmware, two 32-octet pages)
//   Nicla Sense Env 0x21  Arduino firmware (Arduino_NiclaSenseEnv library)
//   AS7265X         0x49  SparkFun Spectral Triad (18 channels, 410-940 nm)
//   RM3100 outboard 0x20  science magnetometer at the boom tip   (SA1 = 0, SA0 = 0)
//   RM3100 inboard  0x23  OPTIONAL, boom mid-point, auto-detected (SA1 = 1, SA0 = 1)
// NEVER strap an RM3100 to 0x21: that is the Nicla Sense Env.
#define SENSOR_WIRE Wire1                       // Qwiic connector = I2C4 (PD12 SCL / PD13 SDA), 3.3 V
static const uint32_t kI2cClockHz    = 100000;  // 100 kHz: tolerant of a long boom cable
static const uint8_t  kRm3100ObAddr  = 0x20;
static const uint8_t  kRm3100IbAddr  = 0x23;
// BHI260AP "magnetometer corrected" output (BMM150) in uT per LSB. Bosch does not
// publish this in the Arduino library; 1/16 uT/LSB is the BMM150 convention.
// VERIFY with test T-SC-06 (docs/08): rotate the Nicla slowly through all
// orientations - |B| must stay constant and equal your site's WMM total field.
static const float kAruMagUtPerLsb = 0.0625f;

// ---- Radio defaults (MUST match ground/config/station.toml [radio]) ----------------------
static const uint8_t  kE22Channel      = 23;     // 410.125 + 23 = 433.125 MHz  (check band plan!)
static const uint8_t  kE22AirRate      = 2;      // E22-400T37S: 0,1,2 = 2.4k (slowest = most sensitive), 3=4.8k 4=9.6k ...
static const uint8_t  kE22PowerCode    = 0;      // T37S: every code = 37 dBm (no power levels)
static const uint16_t kE22Address      = 0x0000; // same on both ends (transparent mode)
static const uint8_t  kE22NetId        = 0x00;

// ---- Solid-state recorder (heap) ------------------------------------------------------------
static const size_t kSsrMaxBytes     = 262144;   // 256 KiB (power of two)
static const size_t kSsrMinBytes     = 16384;
static const size_t kHeapReserveBytes = 32768;   // must remain allocatable afterwards

// ---- Timing ----------------------------------------------------------------------------------
static const uint32_t kMinFramePeriodMs  = 1500;
static const uint16_t kMaxTxDutyPermille = 500;  // PA thermal limit: transmit <= 50 % of the time
static const uint32_t kAuxTimeoutMs      = 30000;
static const uint32_t kNoiseSampleMs     = 15000; // ambient-noise RSSI sampling interval
static const uint32_t kDefaultCmdLossS   = 86400; // command-loss timer (24 h), 0 = off

// ---- Thermal (fault protection) -----------------------------------------------------------------
// PA temperature comes from the E22 itself: above 120 C the module stops
// transmitting and reports FF FF FF 03 (T37S manual v1.5 5.7). The T37S has no
// power levels, so the flight response is to silence the transmitter for
// kPaCooldownS and continue at SAFE cadence.
static const uint16_t kPaCooldownS = 300;
static const float kBusTempRedC = 75.0f;          // Nicla Sense ME temperature (spacecraft bus)

// ---- Local geomagnetic field for TRIAD (look up yours: NOAA / BGS WMM calculator) -------------
static const float kGeomagInclDeg = 60.0f;       // + = field points down (northern hemisphere)
static const float kGeomagDeclDeg = 0.0f;        // + = east of true north

// ---- Callsign (amateur-radio station identification, sent in clear text) ---------------------
// Under amateur rules you must identify periodically; the ID is sent in an EVR.
#define VGQ_CALLSIGN "N0CALL"
static const uint32_t kCallsignPeriodS = 600;   // every 10 minutes
