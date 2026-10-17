// =============================================================================
//  VGQ-1 flight software build configuration - Arduino UNO Q (STM32U585 MCU), v3
//
//  Every pin and bus here is documented in docs/03_Hardware_Integration.md; change
//  a value here and in that document together. Values marked VERIFY are taken from
//  the contract or a datasheet default and must be confirmed on the hardware
//  (docs/08 test plan). Source of every item: Phase B specification PB-3.1.
//
//  Pure C++ (no Arduino types): the host tests compile it (fsw/datalink.cpp).
//  Build-time selections are #ifndef-guarded so that a command-line build may
//  override them with -D (arduino-cli --build-property
//  "compiler.cpp.extra_flags=-D..."). Arduino App Lab always compiles the sketch's
//  default profile, and a sketch.yaml profile cannot carry build flags (arduino-cli
//  profiles have no build-properties entry, contract 25.1): for an App Lab build,
//  edit the #define here. The E22 model needs no edit at all: it is read from a
//  strap pin at boot (PIN_RADIO_MODEL_STRAP below, contract 33).
// =============================================================================
#pragma once
#include <stddef.h>
#include <stdint.h>

// ---- Radio model -----------------------------------------------------------------
// The model codes are defined FIRST: drivers/radio.h and drivers/sx126x.h test
// `#if VGQ_RADIO == VGQ_RADIO_SX126X`, and an #if on undefined macros silently
// compares 0 == 0. The values are fixed by the ICD (RF packet field radio_model).
#ifndef VGQ_RADIO_E22_T33D
#define VGQ_RADIO_E22_T33D 1          // EBYTE E22-400T33D, 2 W, UART (default)
#endif
#ifndef VGQ_RADIO_E22_T37S
#define VGQ_RADIO_E22_T37S 2          // EBYTE E22-400T37S on the E22-400TBH-02 board, 5 W, UART
#endif
#ifndef VGQ_RADIO_SX126X
#define VGQ_RADIO_SX126X 3            // SX1268 / SX1262 module on SPI through RadioLib 7.8.1
#endif
// Build-time radio. VGQ_RADIO_SX126X is a different wiring and selects other drivers.
// For the E22 builds it is only the DEFAULT model: the strap pin below switches an
// E22 build to the E22-400T37S at boot without any source edit (contract 33).
#ifndef VGQ_RADIO
#define VGQ_RADIO VGQ_RADIO_E22_T33D
#endif
#if VGQ_RADIO != VGQ_RADIO_E22_T33D && VGQ_RADIO != VGQ_RADIO_E22_T37S && VGQ_RADIO != VGQ_RADIO_SX126X
#error "VGQ_RADIO must be VGQ_RADIO_E22_T33D (1), VGQ_RADIO_E22_T37S (2) or VGQ_RADIO_SX126X (3)"
#endif
// Transceiver chip of the SX126x module: 1268 (EBYTE E22-400M33S reference module) or 1262.
#ifndef VGQ_SX126X_CHIP
#define VGQ_SX126X_CHIP 1268
#endif

// ---- Radio UART (E22 models) --------------------------------------------------------
// The UNO Q's D0/D1 UART (USART1 = Serial1) is ALSO the Zephyr console: the
// bootloader banner and any runtime kernel log messages are printed on it. A
// transparent radio on that UART would transmit those bytes over the air and a
// log line printed mid-CADU would corrupt the frame.
// Default: USART3 = Serial3, routed to the header pins silk-screened SDA/SCL
// (PB11 = RX, PB10 = TX). Those pins are then a UART - never call Wire.begin()
// (Wire = I2C2 shares PB10/PB11). Set to 1 to use D0/D1 instead.
#ifndef VGQ_RADIO_ON_SERIAL1
#define VGQ_RADIO_ON_SERIAL1 0
#endif
#if VGQ_RADIO_ON_SERIAL1
#define RADIO_SERIAL Serial1          // D1 = TX -> E22 RXD, D0 = RX <- E22 TXD
#else
#define RADIO_SERIAL Serial3          // "SCL"(PB10) = TX -> E22 RXD, "SDA"(PB11) = RX <- E22 TXD
#endif

// ---- Serial ring size of the core --------------------------------------------------------
// arduino:zephyr gives every Serial a TX and an RX ring of CONFIG_ARDUINO_API_SERIAL_BUFFER_SIZE
// octets (core -imacros autoconf.h): 1024 from 1.0.0, 64 before (core commit 40abc1f7). The
// App Lab release that pins the board's core below 1.0.0 builds with 0.90.0. With small
// rings the E22 port goes through a UART pump thread (drivers/uart_pump.h) and notifications
// longer than the ring wait for an empty ring (Flight::notify_room()); with 1024 nothing
// changes. Not defined (host checks): treated as 1024.
#if defined(CONFIG_ARDUINO_API_SERIAL_BUFFER_SIZE) && CONFIG_ARDUINO_API_SERIAL_BUFFER_SIZE < 1024
#define VGQ_SMALL_SERIAL_RINGS 1
#else
#define VGQ_SMALL_SERIAL_RINGS 0
#endif
static const int      kUartPumpPriority   = 6;     // above the loop (14), below the bridge thread (5)
static const int32_t  kUartPumpPeriodMs   = 1;     // 9600 bit/s: ~1 octet per pass each way
static const size_t   kUartPumpStackBytes = 1024;  // one dynamic-thread pool slot

// ---- E22 control pins (both E22 models) ------------------------------------------------
// E22-400T33D: the module's DIP pins M0, M1 and AUX. E22-400T37S on the E22-400TBH-02
// board: remove the board's M0/M1 jumper caps (they short M1-GND and GND-M0) and its
// RXD/TXD caps (they connect the on-board CH340X USB-UART to the module), then wire
// header 10 M0 <- D2, 7 M1 <- D3, 4 AUX -> D7, 5 TXD -> "SDA", 6 RXD <- "SCL".
// Fit 10 kOhm pull-ups to 3.3 V on M0 and M1 (EBYTE calls the module's own pull-ups
// "very weak", T37S manual v1.5 pins 9/10) so the module reliably sleeps - and cannot
// key its PA - while the MCU boots and its pins float.
static const int PIN_E22_M0  = 2;     // D2 (PB3)
static const int PIN_E22_M1  = 3;     // D3 (PB0)
static const int PIN_E22_AUX = 7;     // D7 (PB2)  input, module busy = LOW

// ---- E22 model strap (contract 33): one binary for both E22 modules ----------------------
// Read once at boot with the MCU's internal pull-up: a jumper from this pin to GND
// selects the E22-400T37S, an open pin keeps the build's VGQ_RADIO (E22-400T33D by
// default). It decides every model-dependent behaviour (power-code meaning, REG1
// image, T37S abnormal-status log, sensitivity) and the RF packet radio_model; EVR
// 0x0308 reports it. The SX126x option is a different wiring and stays a build switch.
// Pin: header A0 = D14 = PA4 (digital-pin-gpios index 14 of the ArduinoCore-zephyr
// variant arduino_uno_q_stm32u585xx), used by nothing else in this build. The board
// DTS gives PA4 to DAC1 (dac1_out1_pa4, status okay), but the Zephyr DAC driver only
// applies that pin mux at init; it enables the output in dac_channel_setup()
// (drivers/dac/dac_stm32.c), i.e. on analogWrite(), which this software never calls.
// pinMode() then makes PA4 a GPIO input. ABX00162 9.7: A0 is NOT 5 V tolerant - a
// plain jumper to GND only, never a pull-up to 5 V. -1 = no strap (VGQ_RADIO decides).
static const int      PIN_RADIO_MODEL_STRAP = 14;   // A0 / D14 (PA4)
static const uint8_t  kStrapSamples         = 5;    // majority of 5 reads, kStrapSettleUs apart
static const uint32_t kStrapSettleUs        = 1000; // lets the internal pull-up charge a jumper lead first

// ---- SX126x module on SPI (VGQ_RADIO_SX126X) ------------------------------------------
// UNO Q variant overlay digital-pin-gpios: D4 PA12, D5 PA11, D6 PB1, D8 PB4, D9 PB8,
// D10 PB9 (contract 1.1). RXEN/TXEN = -1 when the module's RF switch is not wired.
static const int PIN_SX_NSS  = 10;    // D10 (PB9)  SPI chip select
static const int PIN_SX_BUSY = 4;     // D4  (PA12) BUSY
static const int PIN_SX_DIO1 = 5;     // D5  (PA11) DIO1 interrupt
static const int PIN_SX_NRST = 6;     // D6  (PB1)  NRESET
static const int PIN_SX_RXEN = 8;     // D8  (PB4)  module RXEN
static const int PIN_SX_TXEN = 9;     // D9  (PB8)  module TXEN
static const float  kSxTcxoV        = 1.8f;    // DIO3 TCXO supply, V (0 = crystal). VERIFY: module datasheet
static const bool   kSxDio2RfSwitch = false;   // DIO2 drives the RF switch. VERIFY: module wiring
static const int8_t kSxPaGainDb     = 11;      // drive -> antenna gain, dB. VERIFY: T-RF-07
static const float  kSxOcpMa        = 140.0f;  // PA current limit (SX1262 value). VERIFY for the SX1268

// ---- Radio defaults (MUST match dssq-groundstation/python/config/station.toml [radio]) --
static const uint8_t  kRadioChannel   = 23;      // 410.125 + 23 = 433.125 MHz (check the band plan)
// E22: 2 = 2.4 kbit/s (codes 0..2 are all 2.4k, the most sensitive); SX126x: profile 1
// = SF12 / 62.5 kHz / CR 4/5 (contract 2.2).
static const uint8_t  kRadioRateCode  = (VGQ_RADIO == VGQ_RADIO_SX126X) ? 1 : 2;
static const uint8_t  kRadioPowerCode = 0;       // highest power on every model (T37S: always 37 dBm)
// Brown-out derating (contract 15): after a BROWNOUT reset the radio starts at this
// code instead (radio_boot_power_code: whichever is the LOWER power), EVR 0x0608.
static const uint8_t  kBrownoutPowerCode = 2;
static const bool     kRadioLbt       = false;   // E22 LBT / SX126x CAD before every transmission
static const bool     kRadioRxBoost   = true;    // SX126x Rx boosted gain (ignored by the E22)
static const uint16_t kE22Address     = 0x0000;  // same on both ends (transparent mode)
static const uint8_t  kE22NetId       = 0x00;

// ---- Downlink framing and SDLS (contract 5, 6) ------------------------------------------
// 0 = TM Transfer Frames (132.0-B-3), 1 = USLP (732.1-B-2). The uplink accepts both.
#ifndef VGQ_DOWNLINK_FRAMING
#define VGQ_DOWNLINK_FRAMING 0
#endif
#if VGQ_DOWNLINK_FRAMING != 0 && VGQ_DOWNLINK_FRAMING != 1
#error "VGQ_DOWNLINK_FRAMING must be 0 (TM) or 1 (USLP)"
#endif
// TC service: 2 = AE (AES-256-GCM), 1 = AUTH (HMAC-SHA-256-128), 0 = NONE. TM service:
// 2 = AE or 0 = NONE (no authentication-only service is defined for TM). Without the
// generated src/sdls_key.h both links run NONE and HK says so. Regulatory: encryption is
// generally not permitted on amateur frequencies - use AUTH or NONE there.
// VGQ_SDLS_SPI (AUTH SPI) comes from the generated sdls_key.h (flight.cpp defaults it to 1).
#ifndef VGQ_SDLS_TC
#define VGQ_SDLS_TC 2
#endif
#ifndef VGQ_SDLS_TM
#define VGQ_SDLS_TM 2
#endif
#if VGQ_SDLS_TC != 0 && VGQ_SDLS_TC != 1 && VGQ_SDLS_TC != 2
#error "VGQ_SDLS_TC must be 0 (NONE), 1 (AUTH) or 2 (AE)"
#endif
#if VGQ_SDLS_TM != 0 && VGQ_SDLS_TM != 2
#error "VGQ_SDLS_TM must be 0 (NONE) or 2 (AE): SDLS defines no authentication-only TM service here"
#endif
// TM nonce safety (contract 5): VC0..3 frames are held until the EGSE supplies an
// epoch; after this many seconds without one they go out under the null SA (clear,
// flagged) with EVR 0x0121 until an epoch arrives.
static const uint32_t kSdlsEpochWaitS = 120;
// TC anti-replay (contract 5, C17): authentic frames are refused (EVR 0x0123) until the
// EGSE restores the SN (set_sdls_sn, sent after sc_boot); after this many seconds without
// a restore (EGSE absent) they are accepted again from the in-RAM counter, EVR 0x0124.
static const uint32_t kSdlsSnWaitS = 120;

// ---- Sensor buses (D-73): two I2C buses with fixed roles, no multiplexer ------------------
// ArduinoCore-zephyr variant arduino_uno_q_stm32u585xx: i2cs = <&i2c2>, <&i2c4>, <&i2c3>, i.e.
// Wire = I2C2, Wire1 = I2C4, Wire2 = I2C3. Wire (header "SDA"/"SCL", PB11/PB10) is the E22
// UART here and is never started. All pins are 3.3 V only (ABX00162): every pull-up of both
// buses goes to 3.3 V, one set per bus.
//
// NICLA bus = Wire2 = I2C3 on header A4 (SDA, PC1, JANALOG 13) / A5 (SCL, PC0, JANALOG 14),
// with the GPIO bus clear (I2cBus):
//   Nicla Sense ME  0x2A  attitude reference unit (custom firmware, three 32-octet pages),
//                         wired through its ESLOV connector J5: pin 4 SDA -> A4, pin 3 SCL -> A5,
//                         pin 1 5 V, pin 5 GND, pin 2 INT not connected (D-74)
//   AS7265X         0x49  SparkFun Spectral Triad (18 channels, 410-940 nm), recommended here
#define NICLA_WIRE Wire2
// MAG bus = Wire1 = I2C4 on the Qwiic socket (pin 3 SDA PD13, pin 4 SCL PD12, pin 2 +3V3):
// the boom magnetometers ALONE, so the long boom cable is isolated from the attitude
// reference. Controller re-initialisation only, no GPIO bus clear: PD12/PD13 have no
// Arduino pin number, and the devicetree that arduino:zephyr 0.90.0, 1.0.0 and main give a
// sketch has no scl-gpios / sda-gpios on the i2c4 node (a GPIO_DT_SPEC_GET on it does not
// compile on any of the three).
//   RM3100 outboard 0x20  science magnetometer at the boom tip   (SA1 = 0, SA0 = 0)
//   RM3100 inboard  0x23  OPTIONAL, boom mid-point, auto-detected (SA1 = 1, SA0 = 1)
#define MAG_WIRE Wire1
// Every instrument is probed on its preferred bus first and on the other bus second, at
// boot and at every re-probe (fsw/sensor_bus.h): the Nicla and the AS7265X A4/A5 then
// Qwiic (the AS7265X as since D-13), the RM3100s Qwiic then A4/A5. One found only on the
// other bus is used, with WARN EVR 0x060D naming the recommended bus.
// The A4/A5 pins as GPIOs, for the NICLA bus clear (I2cBus, open drain emulated):
// digital-pin-gpios index 18 = A4 = PC1, 19 = A5 = PC0 (variant overlay).
static const int      kPinSensorSda  = 18;
static const int      kPinSensorScl  = 19;
static const uint32_t kI2cClockHz    = 100000;  // 100 kHz, both buses: tolerant of a long boom cable
// Bus clear (AS-T6): SCL half period and pulse limit (the I2C-bus recovery procedure:
// up to nine clocks release a target that holds SDA, then a STOP).
static const uint32_t kI2cClearHalfPeriodUs = 5;
static const uint8_t  kI2cClearMaxPulses    = 9;
// FDIR I2C_BUS clears after this many seconds without a new bus error (EVR 0x0609).
static const uint32_t kI2cBusHealthyS = 10;
static const uint8_t  kRm3100ObAddr  = 0x20;
static const uint8_t  kRm3100IbAddr  = 0x23;
// RM3100 sensor axes -> spacecraft body axes (the Nicla Sense ME frame used by the
// accelerometer). Identity = boom magnetometers mounted with the Nicla's axes. Used
// for the TRIAD attitude only; the MAG packet stays raw. VERIFY the mounting (docs/08).
static const float kRm3100ToBody[3][3] = {{1.0f, 0.0f, 0.0f}, {0.0f, 1.0f, 0.0f}, {0.0f, 0.0f, 1.0f}};
// DRDY must arrive within this time of the POLL write; the wait is spread over loop
// passes (Rm3100::poll_drdy), never spun. ~6.8 ms at cycle count 200.
static const uint32_t kMagCollectTimeoutMs = 30;
// Longest wait of an RM3100 sample for the AS7265X lamps to go dark; then the sample is
// taken anyway with MAG flag b4. Engineering value, VERIFY T-SC-08: just above the
// driver's lamp-watchdog limit for the longest single exposure, conversion + conversion/32
// + kSpecDrdyMarginMs + kSpecLampMarginMs = 1424 + 44 + 50 + 500 = 2018 ms (one-shot at
// N = 255), so a deferral never ends while the driver may legitimately keep a lamp lit.
static const uint32_t kSpecLampDeferMaxMs = 2100;
// Nicla ARU magnetometer field (BMM150 via the BHI260AP) in uT per LSB. The ARU
// firmware scales the BHI260's "magnetometer corrected" counts by its reported
// dynamic range (BHI260AP datasheet Table 88) and sends 0.1 uT. Test T-SC-06
// (docs/08): rotate the Nicla slowly - |B| must stay constant at the WMM total field.
static const float kAruMagUtPerLsb = 0.1f;

// ---- AS7265X spectrometer (contract 13, 19, 26) ------------------------------------------
static const int     kPinSpecInt        = -1;   // board INT not wired: DATA_RDY is polled over I2C
static const uint8_t kSpecDefaultGain   = 255;  // AGC (SpecConfig default)
static const uint8_t kSpecDefaultCycles = 59;   // (59 + 1) x 2.78 ms = 166.8 ms: the calibration time
static const uint8_t kSpecBurstCycles   = 1;    // BURST: (1 + 1) x 2.78 = 5.56 ms per bank (C-15)
static const float   kSpecBaselineAlpha = 0.1f; // change-detection EWMA weight of the newest spectrum
static const uint16_t kSpecEventCdeg    = 500;  // spectral-event threshold, 5.00 deg
// Instrument boresight in body axes. VERIFY the mounting (docs/08 T-SC-09).
static const float kSpecBoresightBody[3] = {0.0f, 0.0f, 1.0f};

// ---- CFDP and the Linux EGSE (PB-2.5, PB-0.4) ----------------------------------------------
// VC3 pacing: a PDU is built only while backlog(3) + 12 + ccsds::kCfdpMaxPduLen <= this,
// i.e. room for the largest possible next Space Packet (158 octets): VC3 never holds more
// than two packets. Segment sizes stay in ccsds/cfdp.h (single source, C-07).
static const uint32_t kCfdpVc3BacklogMax = 320;
// CFDP relay: an EGSE that does not start (or finish a cancellation) within this time
// is given up (EVR 0x0A04).
static const uint32_t kRelayStartTimeoutMs = 30000;
// sc_evr notifications: at most this many per second and per loop pass (PB-0.4).
static const uint8_t kEvrNotifyMaxPerS    = 8;
static const uint8_t kEvrNotifyMaxPerPass = 2;
// Bench debugging only: 1 = LOGF lines on the App Lab Monitor. Monitor writes can
// BLOCK the flight loop (Arduino_RouterBridge monitor.h: a blocking RPC whenever the
// monitor is not connected), so flight builds keep 0 (PB-0.4).
#ifndef VGQ_DEBUG_MONITOR
#define VGQ_DEBUG_MONITOR 0
#endif

// ---- Solid-state recorder (heap) ------------------------------------------------------------
static const size_t kSsrMaxBytes      = 262144;  // 256 KiB (power of two)
static const size_t kSsrMinBytes      = 16384;
// Must remain allocatable after the SSR: it also covers the transient allocations of
// every Bridge notification (String arguments + MsgPack packer, a few KiB, PB-0.4).
static const size_t kHeapReserveBytes = 32768;

// ---- Timing ----------------------------------------------------------------------------------
static const uint32_t kMinFramePeriodMs  = 1500;  // fsw/datalink.cpp frame_period_ms()
static const uint16_t kMaxTxDutyPermille = 500;   // PA thermal limit: transmit <= 50 % of the time
static const uint32_t kNoiseSampleMs     = 15000; // ambient-noise sampling interval
static const uint32_t kDefaultCmdLossS   = 86400; // command-loss timer (24 h), 0 = off

// ---- Independent hardware watchdog (IWDG, RM0456; Flight::watchdog_start / watchdog_feed) ------
// Armed at the end of setup() and refreshed once per loop() pass, on the loop thread only:
// a hang anywhere in the loop (a library wait that never returns, a blocked Bridge or
// I2C call) resets the MCU after kIwdgTimeoutMs, and the next boot reports reset_cause 3
// WATCHDOG (HK, EVR 0x0001, sc_boot). The platform builds without CONFIG_WATCHDOG (no
// Zephyr IWDG device, no wdt_* symbol for a sketch), so the flight writes the IWDG
// registers directly, as it reads RCC_CSR. Once started the IWDG runs until the next reset
// (App Lab's upload resets the MCU first); it is paused while a debugger halts the core.
// The timeout is far above the worst documented loop pass (01_System_Design 9.4: E22
// configure() on a dead module about 4 s; each Zephyr I2C transfer that times out 0.5 s).
#ifndef VGQ_IWDG
#define VGQ_IWDG 1                              // 0 = no hardware watchdog (bench debugging only)
#endif
static const uint32_t kIwdgTimeoutMs = 40000;   // 40 s at the nominal LSI frequency
static const uint32_t kIwdgLsiHz = 32000;       // LSI nominal 32 kHz (RM0456 / ST HAL IWDG: 131 s max)
static const uint32_t kIwdgUpdateWaitMs = 200;  // setup(): PR / RLR reach the LSI domain (ST: 6 LSI
                                                // periods x the prescaler, <= 192 ms at /1024)

// ---- Thermal (fault protection) -----------------------------------------------------------------
// E22-400T37S only: the module measures its own PA temperature; above 120 C it stops
// transmitting and reports FF FF FF 03 (T37S manual v1.5 5.7). The T37S has no power
// levels, so the flight response is to silence the transmitter for kPaCooldownS and
// continue at SAFE cadence. The other models report no PA temperature.
static const uint16_t kPaCooldownS = 300;
static const float kBusTempRedC = 75.0f;          // Nicla Sense ME temperature (spacecraft bus)

// ---- Local geomagnetic field for TRIAD (look up yours: NOAA / BGS WMM calculator) -------------
static const float kGeomagInclDeg = 60.0f;       // + = field points down (northern hemisphere)
static const float kGeomagDeclDeg = 0.0f;        // + = east of true north

// ---- Callsign (amateur-radio station identification, sent in clear text) ---------------------
// Sent in EVR 0x0002 every kCallsignPeriodS and as the OID idle data "DE <callsign> VGQ-1 "
// (contract 5), which is never encrypted.
#ifndef VGQ_CALLSIGN
#define VGQ_CALLSIGN "N0CALL"
#endif
static const uint32_t kCallsignPeriodS = 600;   // every 10 minutes
