// =============================================================================
//  DSS-Q1 Station I/O, Radio Control Unit and optional SX126x ground radio
//  VENTUNO Q MCU (STM32H5F5, Arduino core on Zephyr)
//
//  Split of work on the VENTUNO Q:
//    Linux CPU (Dragonwing QCS8275) : the ground data system and its web dashboard
//                                     (python -m dssq.gds) - the station's only display
//    MCU (this sketch)              : real-time station I/O, linked to Linux by the
//                                     Arduino Router Bridge (MessagePack-RPC)
//
//  Functions
//   * CardKB command keyboard - exactly ONE, on `Wire` = the JDIGITAL header I2C pins
//     SDA (PH12) and SCL (PH11) (Arduino VENTUNO Q user manual: D20 = SDA, D21 = SCL;
//     the ArduinoCore-zephyr variant overlay lists the two GPIO numbers the other way
//     round - wire by the SDA / SCL silkscreen, the I2C4 peripheral fixes the roles).
//     Loose jumper leads from the header to the CardKB's Grove (HY2.0-4P) socket via an
//     adapter: SDA, SCL, GND and 3.3 V - or 5 V through a BSS138 bidirectional level
//     shifter (LV = 3.3 V, HV = 5 V): M5Stack specifies 5 V for the CardKB v1.1 and does
//     not publish its I2C levels. The Qwiic socket is not used for anything.
//     Every keystroke is echoed to the GDS (live typing line on the dashboard), Enter
//     sends the line, an empty Enter CONFIRMs the pending hazardous command, KEYTEST
//     toggles raw key-code echo. With the two-person rule enabled the second operator
//     authorises in the web dashboard (there is no second keyboard and no AUTH key).
//   * AOS / ALARM / UPLINK LEDs and a buzzer (alarm escalation, LOS). If the GDS falls
//     silent for 5 s, AOS goes dark and ALARM flashes fast ("GDS link down").
//   * Radio Control Unit for the UART E22 (default build): drives the station module's
//     M0/M1 and reads AUX so the GDS can switch NORMAL / WOR / CONFIG / SLEEP.
//   * Optional SX126x LoRa ground radio over SPI (build switch DSSQ_RADIO_SX126X, see
//     below): RadioLib 7.8.1 (bundled, patched copy), Rx boosted gain with retention,
//     per-packet RSSI / SNR / frequency error, channel-activity detection before
//     transmission, a frequency offset for the ground AFC, bounded BUSY / TX / CAD
//     timeouts and automatic reset-and-reconfigure recovery
//     (contract v3 sections 2.2, 14, 24.8 and 25; Semtech SX1261/2 datasheet rev 2.2).
//
//  Bridge services (Python side: ground/dssq/display/frontpanel.py, radio/sx126x.py)
//   provide_safe "fp_line" (String)   S|aos|alarm|uplink   Q|<confirm id or -1>
//   provide_safe "rcu_mode" (int)     set E22 mode 0..3, returns AUX (1 = ready)
//   notify       "fp_in"   (String)   K|<typed text>  CMD|<text>  CONFIRM|<id>
//                                     H|<fw>|<rcu>|<kb>  (hello, every 5 s)
//   SX126x build only:
//   provide_safe "rf_tx"   (String hex) -> 1 queued, 0 queue full, -1 rejected, -2 radio down
//   provide_safe "rf_cfg"  (ch, code, pwr, flags; -1 = keep) -> 1 applied, 2 queued, -1 range
//   provide_safe "rf_foff" (int hz) -> 1 applied (at once, or - while a transmission, a
//                                     CAD or a packet is in progress - right after it,
//                                     before the next reception or transmission),
//                                     -1 |hz| > 50000, -2 radio down (value not kept)
//   provide_safe "rf_noise"() -> median instantaneous RSSI in 0.1 dBm, -32768 = n/a
//   provide_safe "rf_cad"  () -> 1 started, 0 busy, -2 radio down
//   provide_safe "rf_wake" (String hex) -> as rf_tx, sent with the 32-symbol wake-up
//                                     preamble the hibernating spacecraft listens for
//   notify       "rf_rx"   (hex, rssi 0.1 dBm, snr 0.01 dB, freq error Hz, crc_ok)
//   notify       "rf_evt"  (String)   S|status...  C|cad  T|tx done  E|code|text
//
//  CardKB key codes vary between firmware revisions: type KEYTEST + Enter to have
//  every raw key code echoed to the dashboard, and adjust the KEY_* constants.
// =============================================================================
#include <Arduino.h>
#include <Arduino_RouterBridge.h>
#include <Wire.h>

// -----------------------------------------------------------------------------
//  Build option: SX126x ground radio. Default 0 = the E22 UART build (unchanged).
//  To enable it in Arduino App Lab:
//    1. set the line below to `#define DSSQ_RADIO_SX126X 1` (and DSSQ_SX_CHIP to the
//       chip on your module: 1268 = SX1268, e.g. EBYTE E22-400M33S; 1262 = SX1262);
//    2. set [radio] model = "SX126x" in ground/config/station.toml.
//  RadioLib is not downloaded by the board: tools/radiolib.py fetches release 7.8.1
//  (tag commit verified), applies the project patch (VENTUNO Q platform entry with the
//  PinMode / PinStatus casts the Zephyr core needs; RADIOLIB_STATIC_ONLY, so no heap
//  allocation per SPI transfer; unused modules excluded) and places the result in
//  libraries/RadioLib next to this file, which sketch.yaml lists. The include below is
//  guarded by the build switch: the default E22 build never includes RadioLib.h, and
//  arduino-cli compiles a profile library only for a sketch that includes it.
// -----------------------------------------------------------------------------
#ifndef DSSQ_RADIO_SX126X
#define DSSQ_RADIO_SX126X 0
#endif
#ifndef DSSQ_SX_CHIP
#define DSSQ_SX_CHIP 1268
#endif

#if DSSQ_RADIO_SX126X
#include <SPI.h>
#include <RadioLib.h>
#if RADIOLIB_VERSION_MAJOR != 7 || RADIOLIB_VERSION_MINOR != 8 || RADIOLIB_VERSION_PATCH != 1
#error "the station SX126x code is verified against RadioLib 7.8.1 only (it uses SX126x internals)"
#endif
#if !RADIOLIB_STATIC_ONLY
#error "use the bundled RadioLib patched by tools/radiolib.py (RADIOLIB_STATIC_ONLY must be 1)"
#endif
static const char* kFw = "DSSQ-IO 3.1 SX126x";

// Types that appear in function signatures. They must precede the first function of
// the sketch: the Arduino builder inserts its generated prototypes there.
enum SxState : uint8_t { kSxOff = 0, kSxRx = 1, kSxCad = 2, kSxTx = 3, kSxReset = 4 };
struct SxConfig {
  uint8_t channel, rate_code, power_code;
  bool cad, boost;
  int32_t foff_hz;                                    // RF offset from the channel centre (rf_foff)
};
#else
static const char* kFw = "DSSQ-IO 3.1";
#endif

// ---------------------------------------------------------------- pin map
// Radio Control Unit -> station E22 (T33D: DIP pins M0 / M1 / AUX; T37S: TBH-02 header
// 10 M0, 7 M1, 4 AUX): M0 <- D2, M1 <- D3, AUX -> D4, GND common. Until setup() drives
// M0/M1 LOW (NORMAL) they rest HIGH through the module's "very weak" pull-ups (deep
// sleep); 10 kOhm pull-ups to 3.3 V make that rest state robust. Never fit pull-downs.
static const int PIN_RCU_M0 = 2, PIN_RCU_M1 = 3, PIN_RCU_AUX = 4;
// Annunciators (via 330 Ohm resistors / an NPN transistor for the buzzer)
static const int PIN_LED_AOS = 5, PIN_LED_ALARM = 6, PIN_LED_UPLINK = 7, PIN_BUZZER = A0;

static const uint32_t kKeyScanMs = 20;               // 50 Hz CardKB scan
static const uint32_t kHelloMs = 5000;
static const uint32_t kGdsSilentMs = 5000;           // no status line from Linux: link down
static const uint32_t kRcuAuxTimeoutMs = 1000;       // E22: AUX high after a mode change
static const uint32_t kBuzzMs = 400;

// ---------------------------------------------------------------- CardKB
static const uint8_t CARDKB_ADDR = 0x5F;
static const uint8_t KEY_ENTER_CR = 0x0D, KEY_ENTER_LF = 0x0A, KEY_BS = 0x08, KEY_DEL = 0x7F;
static const uint8_t KEY_ESC = 0x1B;

// ---------------------------------------------------------------- state
static char g_input[64];
static uint8_t g_input_len = 0;
static int g_pending_confirm = -1;
static bool g_keytest = false;
static bool g_aos = false, g_uplink = false;
static uint8_t g_alarm = 0, g_prev_alarm = 0;
static uint32_t g_last_rx_ms = 0, g_last_hello_ms = 0, g_buzz_t0 = 0;
static bool g_buzz = false, g_kb = false;
// All timing is elapsed time (now - t0 >= duration): unsigned arithmetic that stays
// correct across the 49.7-day millis() wrap.

static void send_line(const char* s) { Bridge.notify("fp_in", String(s)); }

static void echo_input() {                          // live typing echo for the dashboard
  char m[72];
  snprintf(m, sizeof m, "K|%s", g_input);
  send_line(m);
}

static int split(char* s, char** f, int maxf) {     // "a|b|c" -> fields, in place
  int n = 0;
  f[n++] = s;
  for (char* p = s; *p && n < maxf; ++p)
    if (*p == '|') { *p = 0; f[n++] = p + 1; }
  return n;
}

static void handle_line(char* line) {
  g_last_rx_ms = millis();
  char* f[6];
  const int n = split(line, f, 6);
  if (f[0][0] == 'S' && n >= 4) {                   // S|aos|alarm|uplink
    const bool aos = atoi(f[1]) != 0;
    const int alarm = atoi(f[2]);
    g_alarm = (uint8_t)((alarm >= 0 && alarm <= 2) ? alarm : 2);   // unknown level: worst case
    g_uplink = atoi(f[3]) != 0;
    if ((g_alarm == 2 && g_prev_alarm != 2) || (g_aos && !aos)) {
      g_buzz = true;
      g_buzz_t0 = millis();
    }
    g_prev_alarm = g_alarm;
    g_aos = aos;
  } else if (f[0][0] == 'Q' && n >= 2) {            // Q|id : hazardous command awaiting CONFIRM
    const int id = atoi(f[1]);
    g_pending_confirm = id >= 0 ? id : -1;
  }
}

// --- Bridge services (provide_safe: executed in the loop thread) -------------
static bool rpc_fp_line(String s) {
  char buf[64];
  strncpy(buf, s.c_str(), sizeof buf - 1);
  buf[sizeof buf - 1] = 0;
  handle_line(buf);
  return true;
}

static int rpc_rcu_mode(int m) {
  if (m < 0 || m > 3) return 0;                     // range check: modes 0..3 only
  digitalWrite(PIN_RCU_M0, (m & 1) ? HIGH : LOW);
  digitalWrite(PIN_RCU_M1, (m & 2) ? HIGH : LOW);
  delay(3);                                          // E22: >= 2 ms after a mode change
  for (uint32_t i = 0; i < kRcuAuxTimeoutMs && digitalRead(PIN_RCU_AUX) == LOW; ++i) delay(1);
  return digitalRead(PIN_RCU_AUX) == HIGH ? 1 : 0;
}

// ---------------------------------------------------------------- keyboard
static int read_cardkb() {
  if (Wire.requestFrom(CARDKB_ADDR, (size_t)1) != 1) return -1;   // not present
  return Wire.available() ? Wire.read() : 0;
}

static void on_key(uint8_t c) {
  if (g_keytest && c != KEY_ENTER_CR && c != KEY_ENTER_LF) {
    char m[24];
    snprintf(m, sizeof m, "K|key code 0x%02X", c);
    send_line(m);
    return;
  }
  if (c == KEY_ESC) {                                // clear the line, drop a pending confirm
    g_input_len = 0;
    g_input[0] = 0;
    g_pending_confirm = -1;
    echo_input();
    return;
  }
  if (c == KEY_ENTER_CR || c == KEY_ENTER_LF) {
    if (g_input_len == 0 && g_pending_confirm >= 0) {       // empty Enter = CONFIRM
      char m[24];
      snprintf(m, sizeof m, "CONFIRM|%d", g_pending_confirm);
      send_line(m);
      g_pending_confirm = -1;
      return;
    }
    if (!g_input_len) return;
    g_input[g_input_len] = 0;
    if (!strcmp(g_input, "KEYTEST")) {
      g_keytest = !g_keytest;
      send_line(g_keytest ? "K|key test ON (KEYTEST + Enter to leave)" : "K|key test OFF");
    } else {
      char m[72];
      snprintf(m, sizeof m, "CMD|%s", g_input);
      send_line(m);
    }
    g_input_len = 0;
    g_input[0] = 0;
    if (!g_keytest) echo_input();
    return;
  }
  if (c == KEY_BS || c == KEY_DEL) {
    if (g_input_len) g_input[--g_input_len] = 0;
    echo_input();
    return;
  }
  if (c >= 0x20 && c < 0x7F && g_input_len < sizeof g_input - 1) {
    g_input[g_input_len++] = (char)toupper(c);
    g_input[g_input_len] = 0;
    echo_input();
  }
}

static void annunciators() {
  const uint32_t now = millis();
  if (now - g_last_rx_ms > kGdsSilentMs) {           // GDS link down: no status from Linux
    digitalWrite(PIN_LED_AOS, LOW);
    digitalWrite(PIN_LED_ALARM, ((now / 100) & 1) ? HIGH : LOW);
    digitalWrite(PIN_LED_UPLINK, LOW);
    digitalWrite(PIN_BUZZER, LOW);
    return;
  }
  digitalWrite(PIN_LED_AOS, g_aos ? HIGH : LOW);
  digitalWrite(PIN_LED_ALARM, g_alarm == 2 ? (((now / 250) & 1) ? HIGH : LOW) : (g_alarm == 1 ? HIGH : LOW));
  digitalWrite(PIN_LED_UPLINK, g_uplink ? HIGH : LOW);
  if (g_buzz && now - g_buzz_t0 >= kBuzzMs) g_buzz = false;
  digitalWrite(PIN_BUZZER, g_buzz ? HIGH : LOW);
}

#if DSSQ_RADIO_SX126X
// =============================================================================
//  SX126x ground radio (contract v3 2.2, 14, 24.8, 25; Semtech SX1261/2 DS rev 2.2)
//
//  Wiring (VENTUNO Q JDIGITAL / JANALOG headers, all 3.3 V logic):
//    `SPI` = SPI2 (variant overlay: spis = <&spi2>, <&spi3>): SCK D13 (PB13),
//    MISO D12 (PB14), MOSI D11 (PB15); NSS D10 (PB12) driven as a GPIO by RadioLib;
//    BUSY D8 (PB4); DIO1 D9 (PB7, polled - no interrupt needed); NRST A1 (PA5, a
//    plain GPIO output here); RXEN A2 (PE12); TXEN A3 (PE13). No conflict with the
//    RCU (D2-D4), the LEDs (D5-D7), the buzzer (A0) or the CardKB bus (PH11/PH12).
//    Module supply, TCXO and RF-switch wiring: VERIFY against the module datasheet.
//
//  Link parameters identical to the spacecraft (src/drivers/sx126x.*): explicit
//  header, payload CRC on, coding rate 4/5, preamble 12 symbols, sync word 0x12,
//  LowDataRateOptimize forced on exactly when Tsym >= 16.38 ms (DS 6.1.1.4) so both
//  ends agree independently of library heuristics; IQ standard.
//
//  Rules applied (contract 14: a BUSY / SPI / command-status failure is a radio
//  fault: NRST reset, begin(), configuration re-applied, E| event - never a hang):
//  * HAL (StationHal): every SPI command waits for BUSY with a bound (RadioLib Module,
//    kSxBusyTimeoutMs). A BUSY level held high for kSxBusyGuardMs is reported low
//    once and latched, so also the waits RadioLib performs without a bound (after
//    Calibrate(0x7F) in begin()) end; the latch is a fault. delayMicroseconds() adds
//    the 1 us the Zephyr core drops (it busy-waits us - 1), keeping RadioLib's settle
//    time between NSS rising and the first BUSY sample above TSW = 600 ns (DS 8.3.1).
//  * Status reads (IRQ, packet status, Rx buffer, registers, chip status, OpError) are
//    single SPI transactions whose transfer and command status are checked.
//  * Configuration (sx_apply, after every reset and change): image calibration
//    requested explicitly for the channel (RadioLib calibrates only for moves >= 20 MHz
//    and keeps its cached frequency across an NRST reset, after which the chip holds
//    the 902-928 MHz default, DS 9.2.1); the offset of rf_foff written as the exact
//    SetRfFrequency word; PA by the datasheet method (SetPaConfig 04 07 00 01 = DS
//    Table 13-21 +22 dBm row, level by SetTxParams, DS 13.4.4) instead of RadioLib's
//    empirical per-level table; OCP set after the PA (Table 5-2: SetPaConfig rewrites
//    it); Rx boosted gain with its retention list (DS 9.6). RadioLib's writeRegister()
//    cannot report a failure, so Rx gain, retention list, OCP, sync word, IQ (15.4)
//    and TxClampConfig (15.2) are read back, and OpError is checked.
//  * TX: SetStandby first (DS 14.2), a chip TX timeout of 1.5 x air time + margin and
//    a software deadline behind it; the air time is computed locally (no SPI).
//  * Every 10 s in RX: chip mode and command status (GetStatus), OpError, Rx gain. A
//    chip found outside RX (failed SetRx, or a chip-internal reset that restored the
//    POR defaults) is re-initialised completely.
//  * RadioLib's begin() silently falls back to the crystal when the TCXO does not
//    start; that is detected and reported (E|12, status flag b3).
//  Packet status (RSSI = RssiPkt, SNR) and the frequency-error register are read right
//  after RxDone, before the payload; a payload with a bad CRC is discarded and reported.
//  Known limitations (DS ch. 15) as covered by RadioLib 7.8.1 in the calls used here:
//  15.1 BW500 modulation quality - fixSensitivity() in stageMode(TX); 15.2 PA
//  clamping - fixPaClamping() in begin(); 15.3 implicit-header timeout - not
//  applicable (explicit header, continuous RX); 15.4 inverted IQ - fixInvertedIQ() in
//  setPacketParams(). TCXO: begin() runs SetDIO3AsTCXOCtrl and then the full
//  calibration (DS 9.2.1). begin() is called only once BUSY has been seen LOW after an
//  NRST pulse; failed initialisations back off exponentially (max 60 s).
// =============================================================================
static const int PIN_SX_NSS = 10, PIN_SX_BUSY = 8, PIN_SX_DIO1 = 9;
static const int PIN_SX_NRST = A1, PIN_SX_RXEN = A2, PIN_SX_TXEN = A3;

static const float kSxTcxoV = 1.8f;                  // DIO3 TCXO supply - VERIFY module
static const bool kSxDio2RfSwitch = false;           // RXEN/TXEN drive the switch - VERIFY module
static const bool kSxUseLdo = false;                 // DC-DC regulator
static const uint8_t kSxSyncWord = 0x12;             // private network
static const uint8_t kSyncRegs[2] = {0x14, 0x24};    // 0x0740/0x0741: 0x12 + RadioLib control bits 0x44
static const uint16_t kSxPreamble = 12;
static const uint16_t kSxWakePreamble = 32;          // = flight kSxWakePreambleSym (duty-cycled RX)
static const uint8_t kSxCodingRate = 5;              // 4/5: RadioLib takes the denominator = CR + 4
static const float kSxOcpMa = 140.0f;                // DS Table 5-2: OCP after SetPaConfig (SX1262)
static const uint32_t kSxBusyTimeoutMs = 100;        // per SPI command (RadioLib default 1000)
static const uint32_t kSxBusyGuardMs = 50;           // < kSxBusyTimeoutMs: the guard sees a stuck BUSY first
static const uint32_t kSxResetPulseMs = 1;           // NRESET low >= 100 us (DS 8.1)
static const uint32_t kTxMarginMs = 100;             // added to 1.5 x air time
static const uint32_t kDeadlineSlackMs = 50;         // software deadline behind the chip timeout
static const uint32_t kLbtMaxWaitMs = 2000;          // then transmit anyway: E22 LBT limit (T33D 7.2 REG3 b4)
static const uint8_t kCadBackoffSymbols = 4;         // backoff unit after a busy channel
static const uint8_t kCadMaxSymbols = 16;            // upper bound of the CAD duration
static const uint32_t kHealthPeriodMs = 10000;
static const uint32_t kStatusPeriodMs = 5000;
static const uint32_t kRetryMinMs = 1000, kRetryMaxMs = 60000;
static const uint8_t kNoiseSamples = 8;
static const int kNoiseNa = -32768;
static const size_t kRfTxMaxOctets = 112;            // hex request fits the 256-octet RPC buffer
static const uint8_t kTxSlots = 4;
static const int32_t kFoffMaxHz = 50000;             // rf_foff range (contract 24.8)
static_assert(sizeof(int) >= 4, "rf_foff / rf_cfg take their arguments as int (int32 on the wire)");
static const float kLdroSymbolMs = 16.38f;           // DS 6.1.1.4
static const uint16_t kRegRxGain = 0x08AC, kRegRxGainRetention = 0x029F, kRegFreqError = 0x076B;
static const uint16_t kRegOcp = 0x08E7, kRegTxClamp = 0x08D8, kRegIqConfig = 0x0736, kRegSyncWord = 0x0740;
static const uint8_t kRxGainBoosted = 0x96, kRxGainPowerSaving = 0x94;   // DS Table 9-3
static const uint8_t kChipModeMask = 0x70, kChipModeRx = 0x50;           // GetStatus b6:4 (Table 13-76)
static const uint16_t kOpErrReinitMask = 0x0060;     // DS Table 13-86: XOSC_START_ERR, PLL_LOCK_ERR
static const uint16_t kOpErrCalibMask = 0x001F;      // DS Table 13-86: RC64K, RC13M, PLL, ADC, IMG calibration

struct SxProfile { uint8_t sf; float bw_khz; };
static const SxProfile kProfiles[8] = {{12, 31.25f}, {12, 62.5f}, {12, 125.0f}, {11, 125.0f},
                                       {10, 125.0f}, {9, 125.0f}, {8, 125.0f}, {7, 125.0f}};
static const int8_t kDriveDbm[4] = {22, 17, 14, 10};

// RadioLib's Arduino HAL on the `SPI` bus with RadioLib's default SPI settings (mode 0,
// MSB first, 2 MHz; DS 8.2 allows up to 16 MHz), a BUSY guard and a corrected
// microsecond delay (see the rules above). Static object: no heap allocation (the
// four-pin Module constructor would allocate its own HAL with new).
class StationHal : public ArduinoHal {
 public:
  StationHal() : ArduinoHal(SPI, SPISettings(2000000, MSBFIRST, SPI_MODE0)) {}

  uint32_t digitalRead(uint32_t pin) override {
    if (pin == (uint32_t)RADIOLIB_NC) return 0;
    const uint32_t level = (uint32_t)::digitalRead((pin_size_t)pin);
    if (pin != (uint32_t)PIN_SX_BUSY) return level;
    if (level == (uint32_t)LOW) {
      high_ = false;
      return level;
    }
    const uint32_t now = ::millis();
    // Reads inside one library wait loop are microseconds apart: a gap of more than
    // 2 ms starts a new BUSY episode.
    if (!high_ || now - last_ms_ > 2u) {
      high_ = true;
      first_ms_ = now;
    }
    last_ms_ = now;
    if (now - first_ms_ < kSxBusyGuardMs) return level;
    stuck_ = true;                                    // end the library's loop once; sx_ok() faults
    high_ = false;
    return (uint32_t)LOW;
  }

  void delayMicroseconds(RadioLibTime_t us) override {
    if (us < 0xFFFFFFFFUL) ++us;
    ::delayMicroseconds((unsigned int)us);
  }

  bool stuck() const { return stuck_; }
  void clear_stuck() {
    stuck_ = false;
    high_ = false;
  }

 private:
  bool high_ = false, stuck_ = false;
  uint32_t first_ms_ = 0, last_ms_ = 0;
};

// RadioLib keeps register access and raw command helpers protected; this thin
// subclass exposes exactly what verification and status-checked I/O need. Every
// helper is ONE SPI transaction and returns RadioLib's transfer status, which covers
// the BUSY wait and the chip's command-status bits (SX126x::SPIparseStatus).
template <class Chip>
class StationRadio : public Chip {
 public:
  explicit StationRadio(Module* module) : Chip(module) {}

  // ReadRegister (DS 13.2.2): opcode, address, then data from the fifth octet - the
  // same framing as RadioLib's readRegister(), whose data transfer drops its status.
  int16_t readReg(uint16_t addr, uint8_t* data, uint8_t n) {
    const uint8_t cmd[3] = {RADIOLIB_SX126X_CMD_READ_REGISTER, (uint8_t)(addr >> 8), (uint8_t)(addr & 0xFF)};
    return this->getMod()->SPIreadStream(cmd, 3, data, n);
  }

  int16_t irqStatus(uint16_t* irq) {                  // GetIrqStatus (DS 13.3.3)
    uint8_t d[2] = {0, 0};
    const int16_t st = this->getMod()->SPIreadStream(RADIOLIB_SX126X_CMD_GET_IRQ_STATUS, d, 2);
    *irq = (uint16_t)(((uint16_t)d[0] << 8) | d[1]);
    return st;
  }

  int16_t clearIrq(uint16_t mask) { return this->clearIrqStatus(mask); }

  int16_t packetStatus(uint8_t* st3) {               // RssiPkt, SnrPkt, SignalRssiPkt (DS 13.5.3)
    return this->getMod()->SPIreadStream(RADIOLIB_SX126X_CMD_GET_PACKET_STATUS, st3, 3);
  }

  int16_t rxBuffer(uint8_t* len, uint8_t* offset) {  // GetRxBufferStatus (DS 13.5.2)
    uint8_t d[2] = {0, 0};
    const int16_t st = this->getMod()->SPIreadStream(RADIOLIB_SX126X_CMD_GET_RX_BUFFER_STATUS, d, 2);
    *len = d[0];
    *offset = d[1];
    return st;
  }

  int16_t readPayload(uint8_t* data, uint8_t n, uint8_t offset) { return this->readBuffer(data, n, offset); }

  int16_t rssiInst(int16_t* ddbm) {                  // GetRssiInst (DS 13.5.4): -RssiInst / 2 dBm
    uint8_t raw = 0;
    const int16_t st = this->getMod()->SPIreadStream(RADIOLIB_SX126X_CMD_GET_RSSI_INST, &raw, 1);
    *ddbm = (int16_t)(-5 * (int16_t)raw);
    return st;
  }

  // GetStatus (DS 13.5.1, Table 13-76): the status octet is the answer itself, so the
  // status width is set to 0 for this one transfer (as RadioLib's getStatus(), which
  // however drops the transfer status). The parsed command status reports a failure
  // of the previous command, e.g. a SetRx that could not be executed.
  int16_t chipStatus(uint8_t* status) {
    Module* m = this->getMod();
    const uint8_t cmd = RADIOLIB_SX126X_CMD_GET_STATUS;
    m->spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_STATUS] = Module::BITS_0;
    const int16_t st = m->SPItransferStream(&cmd, 1, false, nullptr, status, 1, true);
    m->spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_STATUS] = Module::BITS_8;
    return st;
  }

  int16_t deviceErrors(uint16_t* op_error) {          // GetDeviceErrors (DS 13.6.1, Table 13-86)
    uint8_t d[2] = {0, 0};
    const int16_t st = this->getMod()->SPIreadStream(RADIOLIB_SX126X_CMD_GET_DEVICE_ERRORS, d, 2);
    *op_error = (uint16_t)(((uint16_t)d[0] << 8) | d[1]);
    return st;
  }

  int16_t clearErrors() { return this->clearDeviceErrors(); }

  // SetRfFrequency (DS 13.4.1) with a raw word: RadioLib's setFrequency(float MHz)
  // resolves only the float grid, 2^-15 MHz = 30.5 Hz between 256 and 512 MHz.
  int16_t setFrf(uint32_t frf) { return this->setRfFrequency(frf); }

  // LoRa transmission with the chip's own TX timeout (DS 13.1.4 SetTx, 15.625 us
  // steps) and a bounded wait for the PA ramp (BUSY low); RadioLib's startTransmit()
  // uses no chip timeout and waits for BUSY without a bound.
  int16_t startTransmitTimed(const uint8_t* data, size_t len, uint32_t timeout_ms) {
    int16_t st = this->standby();                     // DS 14.2 step 1: configure in STDBY_RC
    if (st != RADIOLIB_ERR_NONE) return st;
    RadioModeConfig_t cfg;
    cfg.transmit.data = data;
    cfg.transmit.len = len;
    cfg.transmit.addr = 0;
    st = this->stageMode(RADIOLIB_RADIO_MODE_TX, &cfg);
    if (st != RADIOLIB_ERR_NONE) return st;
    this->getMod()->setRfSwitchState(this->txMode);
    uint32_t raw = timeout_ms * 64UL;                 // 1 ms = 64 x 15.625 us
    if (raw > 0xFFFFFEUL) raw = 0xFFFFFEUL;           // 0xFFFFFF is not a timeout value
    st = this->setTx(raw);
    if (st != RADIOLIB_ERR_NONE) return st;
    for (uint32_t i = 0; i < kSxBusyTimeoutMs * 10UL; ++i) {    // 100 us polls
      if (digitalRead(PIN_SX_BUSY) == LOW) return RADIOLIB_ERR_NONE;
      delayMicroseconds(100);
    }
    return RADIOLIB_ERR_SPI_CMD_TIMEOUT;
  }
};

#if DSSQ_SX_CHIP == 1268
typedef StationRadio<SX1268> SxRadio;
static const char* kSxChip = "SX1268";
#elif DSSQ_SX_CHIP == 1262
typedef StationRadio<SX1262> SxRadio;
static const char* kSxChip = "SX1262";
#else
#error "DSSQ_SX_CHIP must be 1268 or 1262"
#endif

enum SxEvent : uint8_t {
  kEvBusyTimeout = 1, kEvSpiError = 2, kEvTxTimeout = 3, kEvInitFailed = 4, kEvRecovered = 5,
  kEvRxGainReasserted = 6, kEvCadTimeout = 7, kEvDeviceErrors = 8, kEvRxError = 9,
  kEvConfigFailed = 10, kEvModeRestored = 11, kEvTcxoFallback = 12
};

struct TxSlot {
  uint8_t len;
  bool wake;                                          // send with the wake-up preamble
  uint8_t data[kRfTxMaxOctets];
};

struct SxCtx {
  SxState state;
  SxConfig active, wanted;
  bool cfg_pending, cad_request, cad_for_tx, in_fault, holding, lbt_waiting, tcxo_fallback, calib_error;
  uint8_t init_failures, rx_gain_reg;
  uint16_t preamble;                                  // preamble length currently programmed
  uint32_t t_state, timeout_ms;                       // current state: entry time, time limit
  uint32_t retry_ms, backoff_t0, backoff_ms, hold_t0, hold_ms, lbt_t0, last_health, last_status;
  uint32_t tx_airtime_ms;
  uint32_t rx_ok, rx_crc, rx_hdr, tx_ok, tx_fail, cad_busy, cad_forced, faults, resets;
  TxSlot txq[kTxSlots];
  uint8_t tx_head, tx_count;
};

static SxCtx g_sx = {};
static StationHal g_sx_hal;
static Module g_sx_mod(&g_sx_hal, PIN_SX_NSS, PIN_SX_DIO1, PIN_SX_NRST, PIN_SX_BUSY);
static SxRadio g_radio(&g_sx_mod);
static uint8_t g_rx_buf[256];
static char g_hex[2 * 255 + 1];
static uint32_t g_rng = 0x2545F491UL;

static uint32_t xorshift32() {                       // backoff jitter, no library dependency
  g_rng ^= g_rng << 13;
  g_rng ^= g_rng >> 17;
  g_rng ^= g_rng << 5;
  return g_rng;
}

static float symbol_ms(const SxConfig& c) {
  return (float)(1UL << kProfiles[c.rate_code].sf) / kProfiles[c.rate_code].bw_khz;
}

// LoRa time on air of a len-octet packet with the active profile and the programmed
// preamble, in ms rounded up. Contract 2.2 / DS 6.1.4 (explicit header, CRC on, CR 4/5,
// LDRO by the rule sx_apply() programs), integers only: T = (Npre + 4.25 + Npay) Tsym,
// Npay = 8 + max(ceil((8 len - 4 SF + 28 + 16) / (4 (SF - 2 LDRO))), 0) (CR + 4),
// Tsym = 2^SF / BW. Computed locally because RadioLib getTimeOnAir() reads the packet
// type without a status check and returns an error code through its unsigned result.
static uint32_t airtime_ms(size_t len) {
  const SxProfile& p = kProfiles[g_sx.active.rate_code];
  const int32_t de = symbol_ms(g_sx.active) >= kLdroSymbolMs ? 2 : 0;
  const int32_t num = 8 * (int32_t)len - 4 * (int32_t)p.sf + 28 + 16;
  const int32_t den = 4 * ((int32_t)p.sf - de);
  const int32_t blocks = num > 0 ? (num + den - 1) / den : 0;
  const uint64_t quarter_symbols = 4ull * g_sx.preamble + 17ull + 4ull * (8ull + (uint64_t)blocks * kSxCodingRate);
  const uint64_t bw_hz = (uint64_t)(p.bw_khz * 1000.0f + 0.5f);
  const uint64_t us = (quarter_symbols * (1000000ull << p.sf) + 4ull * bw_hz - 1ull) / (4ull * bw_hz);
  return (uint32_t)((us + 999ull) / 1000ull);
}

// SetRfFrequency word for channel ch plus hz (DS 13.4.1: f = word x 32 MHz / 2^25).
// The channel centre 410.125 + ch MHz is exactly (3281 + 8 ch) x 2^17 steps - the word
// RadioLib's setFrequency() writes for it - and the offset is rounded to the nearest
// step (hz x 2^25 / 32e6 = hz x 16384 / 15625), within 0.48 Hz of the request.
static uint32_t sx_frf(uint8_t ch, int32_t hz) {
  const int64_t num = (int64_t)hz * 16384;
  const int64_t steps = num >= 0 ? (num + 7812) / 15625 : -((7812 - num) / 15625);
  return (uint32_t)((int64_t)((3281u + 8u * (uint32_t)ch) << 17) + steps);
}

static void sx_evt(const char* line) { Bridge.notify("rf_evt", String(line)); }

static void sx_event(uint8_t code, const char* text) {
  char m[128];
  snprintf(m, sizeof m, "E|%u|%s", (unsigned)code, text);
  sx_evt(m);
}

static void sx_set_state(SxState s) {
  g_sx.state = s;
  g_sx.t_state = millis();
}

// S| flags: b0 CAD before TX, b1 Rx boosted gain requested, b2 boosted gain verified
// (0x08AC read back), b3 TCXO fell back to the crystal, b4 a configuration (rf_cfg /
// rf_foff) is waiting to be applied, b5 a calibration error (OpError bits 0-4) was seen
// since the last initialisation (cleared when begin() recalibrates after an NRST reset).
static uint8_t sx_flags() {
  return (uint8_t)((g_sx.active.cad ? 1 : 0) | (g_sx.active.boost ? 2 : 0) |
                   (g_sx.rx_gain_reg == kRxGainBoosted ? 4 : 0) | (g_sx.tcxo_fallback ? 8 : 0) |
                   (g_sx.cfg_pending ? 16 : 0) | (g_sx.calib_error ? 32 : 0));
}

// Status line, field order = ground/dssq/radio/sx126x.py STATUS_FIELDS; the trailing
// field is the RF offset in effect (Hz, rf_foff), the one a pending change replaces.
static void sx_status() {
  char m[224];
  snprintf(m, sizeof m, "S|%s|%s|%u|%u|%u|%u|%u|%u|%lu|%lu|%lu|%lu|%lu|%lu|%lu|%lu|%lu|%u|%lu|%ld", kFw, kSxChip,
           (unsigned)g_sx.state, (unsigned)g_sx.active.channel, (unsigned)g_sx.active.rate_code,
           (unsigned)g_sx.active.power_code, (unsigned)sx_flags(), (unsigned)g_sx.rx_gain_reg,
           (unsigned long)g_sx.rx_ok, (unsigned long)g_sx.rx_crc, (unsigned long)g_sx.rx_hdr,
           (unsigned long)g_sx.tx_ok, (unsigned long)g_sx.tx_fail, (unsigned long)g_sx.cad_busy,
           (unsigned long)g_sx.cad_forced, (unsigned long)g_sx.faults, (unsigned long)g_sx.resets,
           (unsigned)g_sx.tx_count, (unsigned long)(millis() / 1000UL), (long)g_sx.active.foff_hz);
  sx_evt(m);
  g_sx.last_status = millis();
}

// Any radio failure ends here: report, drop the transmission in flight (COP-1
// retransmits), and schedule a reset with exponential backoff.
static void sx_fault(uint8_t code, const char* text) {
  ++g_sx.faults;
  g_sx.in_fault = true;
  g_sx_hal.clear_stuck();                            // the reset below starts a clean BUSY history
  sx_event(code, text);
  if (g_sx.state == kSxTx) ++g_sx.tx_fail;
  if (g_sx.init_failures < 16) ++g_sx.init_failures;
  uint32_t backoff = kRetryMinMs;
  for (uint8_t i = 1; i < g_sx.init_failures && backoff < kRetryMaxMs; ++i) backoff *= 2;
  if (backoff > kRetryMaxMs) backoff = kRetryMaxMs;
  g_sx.retry_ms = backoff;
  g_sx.holding = false;
  g_sx.lbt_waiting = false;                          // a recovered radio starts LBT afresh
  digitalWrite(PIN_SX_TXEN, LOW);                    // PA path off whatever happens next
  sx_set_state(kSxOff);
  sx_status();
}

// The one check for every RadioLib result: a non-OK status or a latched BUSY guard is
// a fault (the guard also fires inside calls that report OK, e.g. begin()).
static bool sx_ok(int16_t st, uint8_t code, const char* what) {
  const bool stuck = g_sx_hal.stuck();
  if (st == RADIOLIB_ERR_NONE && !stuck) return true;
  char m[64];
  if (stuck) {
    snprintf(m, sizeof m, "%s: BUSY high for %lu ms", what, (unsigned long)kSxBusyGuardMs);
  } else {
    snprintf(m, sizeof m, "%s failed (%d)", what, (int)st);
  }
  sx_fault(stuck || st == RADIOLIB_ERR_SPI_CMD_TIMEOUT ? (uint8_t)kEvBusyTimeout : code, m);
  return false;
}

// Reads one register and compares it under a mask: 1 = as expected, 0 = mismatch
// (reported as a configuration fault), -1 = SPI fault (raised).
static int sx_expect_reg(uint16_t addr, uint8_t mask, uint8_t want, const char* what) {
  uint8_t v = 0;
  char m[64];
  snprintf(m, sizeof m, "read 0x%04X", (unsigned)addr);
  if (!sx_ok(g_radio.readReg(addr, &v, 1), kEvSpiError, m)) return -1;
  if ((v & mask) == want) return 1;
  snprintf(m, sizeof m, "%s does not read back (0x%04X = 0x%02X)", what, (unsigned)addr, (unsigned)v);
  sx_fault(kEvConfigFailed, m);
  return 0;
}

// Reads the Rx gain register back: 1 = as requested, 0 = mismatch, -1 = SPI fault (raised).
static int sx_check_rx_gain(bool boost) {
  uint8_t v = 0;
  if (!sx_ok(g_radio.readReg(kRegRxGain, &v, 1), kEvSpiError, "read 0x08AC")) return -1;
  g_sx.rx_gain_reg = v;
  return v == (boost ? kRxGainBoosted : kRxGainPowerSaving) ? 1 : 0;
}

// OpError (DS 13.6.1, Table 13-86): any bit is reported and cleared (DS 13.6.2: all
// at once); XOSC_START_ERR / PLL_LOCK_ERR need a full re-initialisation. A calibration
// bit is reported, latched in the status flags (b5) and the radio kept running: a
// persistent calibration fault must not turn into a reset storm (the flight driver
// clears config_ok the same way). *seen = the OpError read. false = a fault was raised.
static bool sx_check_device_errors(const char* when, uint16_t* seen) {
  uint16_t err = 0;
  *seen = 0;
  if (!sx_ok(g_radio.deviceErrors(&err), kEvSpiError, "GetDeviceErrors")) return false;
  *seen = err;
  if (err == 0) return true;
  char m[64];
  snprintf(m, sizeof m, "OpError 0x%04X %s", (unsigned)err, when);
  if (!sx_ok(g_radio.clearErrors(), kEvSpiError, "ClearDeviceErrors")) return false;
  if (err & kOpErrReinitMask) {
    sx_fault(kEvDeviceErrors, m);
    return false;
  }
  sx_event(kEvDeviceErrors, m);
  return true;
}

static bool sx_set_preamble(uint16_t symbols) {
  if (g_sx.preamble == symbols) return true;
  if (!sx_ok(g_radio.setPreambleLength(symbols), kEvConfigFailed, "setPreambleLength")) return false;
  g_sx.preamble = symbols;
  return true;
}

static bool sx_start_rx() {
  if (!sx_set_preamble(kSxPreamble)) return false;    // after a wake-up transmission
  const uint32_t irq_flags = RADIOLIB_IRQ_RX_DEFAULT_FLAGS;
  const uint32_t irq_mask = (1UL << RADIOLIB_IRQ_RX_DONE) | (1UL << RADIOLIB_IRQ_HEADER_ERR);
  if (!sx_ok(g_radio.startReceive(RADIOLIB_SX126X_RX_TIMEOUT_INF, irq_flags, irq_mask, 0), kEvSpiError,
             "startReceive"))
    return false;
  g_sx.holding = false;
  sx_set_state(kSxRx);
  return true;
}

// Applies a configuration to the chip, verifies it and restarts continuous RX. Any
// failure is a fault: the radio is then reset and re-initialised with g_sx.wanted.
static bool sx_apply(const SxConfig& c) {
  const SxProfile& p = kProfiles[c.rate_code];
  const float f_mhz = 410.125f + (float)c.channel;    // exact in float: (3281 + 8 ch) / 8
  if (!sx_ok(g_radio.standby(), kEvSpiError, "standby")) return false;   // STDBY_RC, as CalibrateImage needs
  // RadioLib's setFrequency() range-checks and records the channel; its own image
  // calibration is skipped and requested explicitly, on every apply (DS 9.2.1; RadioLib
  // maps 430-440 MHz to Table 9-2 0x6B 0x6F, other channels to f +- 4 MHz). An rf_foff
  // offset (<= 50 kHz) stays inside the calibrated range and is written as a raw word.
  if (!sx_ok(g_radio.setFrequency(f_mhz, true), kEvConfigFailed, "setFrequency")) return false;
  if (!sx_ok(g_radio.calibrateImage(f_mhz), kEvConfigFailed, "calibrateImage")) return false;
  if (c.foff_hz != 0 && !sx_ok(g_radio.setFrf(sx_frf(c.channel, c.foff_hz)), kEvConfigFailed, "SetRfFrequency"))
    return false;
  if (!sx_ok(g_radio.setSpreadingFactor(p.sf), kEvConfigFailed, "setSpreadingFactor")) return false;
  if (!sx_ok(g_radio.setBandwidth(p.bw_khz), kEvConfigFailed, "setBandwidth")) return false;
  if (!sx_ok(g_radio.forceLDRO(symbol_ms(c) >= kLdroSymbolMs), kEvConfigFailed, "forceLDRO")) return false;
  // optimize = false: SetPaConfig 0x04 0x07 0x00 0x01 for every level and the level set
  // by SetTxParams (DS 13.4.4), as the power codes (contract 2.2, chip drive dBm) assume.
  if (!sx_ok(g_radio.setOutputPower(kDriveDbm[c.power_code], false), kEvConfigFailed, "setOutputPower")) return false;
  // After the PA: SetPaConfig rewrites OCP and RadioLib restores the previous value
  // (60 mA after begin()), too low for +22 dBm drive.
  if (!sx_ok(g_radio.setCurrentLimit(kSxOcpMa), kEvConfigFailed, "setCurrentLimit")) return false;
  if (!sx_ok(g_radio.setRxBoostedGainMode(c.boost, true), kEvConfigFailed, "setRxBoostedGainMode")) return false;
  // Read-backs: RadioLib's writeRegister() always reports success.
  const int gain = sx_check_rx_gain(c.boost);
  if (gain < 0) return false;
  if (gain == 0) {
    sx_fault(kEvConfigFailed, "Rx gain register does not read back");
    return false;
  }
  if (c.boost) {                                      // retention entry (DS 9.6) must be in place
    uint8_t r[3] = {0, 0, 0};
    if (!sx_ok(g_radio.readReg(kRegRxGainRetention, r, 3), kEvSpiError, "read 0x029F")) return false;
    if (r[0] != 0x01 || r[1] != 0x08 || r[2] != 0xAC) {
      sx_fault(kEvConfigFailed, "Rx gain retention registers do not read back");
      return false;
    }
  }
  // OCP: 6 bits in 2.5 mA steps (Table 5-2), the value RadioLib setCurrentLimit() writes.
  if (sx_expect_reg(kRegOcp, 0x3F, (uint8_t)(kSxOcpMa / 2.5f), "OCP") != 1) return false;
  if (sx_expect_reg(kRegSyncWord, 0xFF, kSyncRegs[0], "sync word") != 1) return false;
  if (sx_expect_reg(kRegSyncWord + 1, 0xFF, kSyncRegs[1], "sync word") != 1) return false;
  if (sx_expect_reg(kRegIqConfig, 0x04, 0x04, "IQ (DS 15.4)") != 1) return false;              // standard IQ
  if (sx_expect_reg(kRegTxClamp, 0x1E, 0x1E, "TxClampConfig (DS 15.2)") != 1) return false;    // bits 4..1
  // Errors raised by begin() (TCXO start, Calibrate(0x7F), CalibrateImage) or by this
  // configuration; RadioLib's setTCXO() already cleared the expected POR XOSC_START_ERR.
  uint16_t err = 0;
  if (!sx_check_device_errors("after configuration", &err)) return false;
  if (err & kOpErrCalibMask) g_sx.calib_error = true;
  g_sx.active = c;
  g_sx.cfg_pending = false;
  return sx_start_rx();
}

// Back to RX after a transmission or a CAD: a configuration that arrived meanwhile
// (rf_cfg, rf_foff) is applied first, so no packet is received with a stale one.
static void sx_resume_rx() {
  if (!g_sx.cfg_pending) {
    sx_start_rx();
    return;
  }
  if (sx_apply(g_sx.wanted)) sx_status();
}

static void sx_begin_reset() {
  pinMode(PIN_SX_NRST, OUTPUT);
  digitalWrite(PIN_SX_NRST, LOW);
  delay(kSxResetPulseMs);
  digitalWrite(PIN_SX_NRST, HIGH);
  sx_set_state(kSxReset);
}

// Called once BUSY is LOW after the NRST pulse (the chip answers SPI).
static void sx_init() {
  const SxConfig& c = g_sx.wanted;
  const SxProfile& p = kProfiles[c.rate_code];
  g_sx_mod.spiConfig.timeout = kSxBusyTimeoutMs;
  // The chip was just reset through NRST and BUSY is low: skip RadioLib's own reset
  // loop so a missing / miswired module fails in ~0.1 s instead of ~10 s (findChip
  // then makes 10 version-string reads 10 ms apart). Buffer base addresses are set by
  // every stageMode() and are at their POR values after the reset anyway.
  g_radio.resetOnStartup = false;
  g_radio.setRfSwitchPins(PIN_SX_RXEN, PIN_SX_TXEN);
  g_sx_hal.clear_stuck();
  g_sx.tcxo_fallback = false;
  g_sx.calib_error = false;                           // begin() runs the full Calibrate(0x7F) again
  const int16_t st = g_radio.begin(410.125f + (float)c.channel, p.bw_khz, p.sf, kSxCodingRate, kSxSyncWord,
                                   kDriveDbm[c.power_code], kSxPreamble, kSxTcxoV, kSxUseLdo);
  if (!sx_ok(st, kEvInitFailed, "begin")) return;
  g_sx.preamble = kSxPreamble;
  // RadioLib re-arms tcxoVoltage from the argument on every begin() and zeroes it when
  // it fell back to the crystal (SetDIO3AsTCXOCtrl followed by XOSC_START_ERR, DS 13.3.6).
  g_sx.tcxo_fallback = kSxTcxoV > 0.0f && g_radio.tcxoVoltage <= 0.0f;
  if (g_sx.tcxo_fallback) sx_event(kEvTcxoFallback, "TCXO did not start - running on the crystal (VERIFY module)");
  if (!sx_ok(g_radio.setDio2AsRfSwitch(kSxDio2RfSwitch), kEvInitFailed, "setDio2AsRfSwitch")) return;
  if (!sx_ok(g_radio.explicitHeader(), kEvInitFailed, "explicitHeader")) return;
  if (!sx_ok(g_radio.setCRC(2), kEvInitFailed, "setCRC")) return;
  if (!sx_apply(c)) return;
  ++g_sx.resets;
  g_sx.init_failures = 0;
  if (g_sx.in_fault) {
    g_sx.in_fault = false;
    sx_event(kEvRecovered, "radio re-initialised and reconfigured");
  }
  sx_status();
}

static void sx_hex(const uint8_t* d, uint8_t n) {
  static const char kDigits[] = "0123456789ABCDEF";
  for (uint8_t i = 0; i < n; ++i) {
    g_hex[2 * i] = kDigits[d[i] >> 4];
    g_hex[2 * i + 1] = kDigits[d[i] & 0x0F];
  }
  g_hex[2 * n] = 0;
}

// DIO1 in RX: RxDone or a header error. Packet status and frequency error first,
// payload last, IRQs cleared; the radio stays in continuous RX.
static void sx_on_rx_irq() {
  uint16_t irq = 0;
  if (!sx_ok(g_radio.irqStatus(&irq), kEvSpiError, "GetIrqStatus")) return;
  if (!(irq & RADIOLIB_SX126X_IRQ_RX_DONE)) {
    if (irq & RADIOLIB_SX126X_IRQ_HEADER_ERR) ++g_sx.rx_hdr;
    sx_ok(g_radio.clearIrq(RADIOLIB_SX126X_IRQ_ALL), kEvSpiError, "ClearIrqStatus");
    g_sx.holding = false;
    return;
  }
  uint8_t ps[3] = {0, 0, 0};
  if (!sx_ok(g_radio.packetStatus(ps), kEvSpiError, "GetPacketStatus")) return;
  uint8_t fe[3] = {0, 0, 0};
  if (!sx_ok(g_radio.readReg(kRegFreqError, fe, 3), kEvSpiError, "read 0x076B")) return;
  const bool crc_ok = !(irq & RADIOLIB_SX126X_IRQ_CRC_ERR) &&
                      !((irq & RADIOLIB_SX126X_IRQ_HEADER_ERR) && !(irq & RADIOLIB_SX126X_IRQ_HEADER_VALID));
  // Frequency error: 20-bit two's complement, Hz = efe x 1.55 x BW[kHz] / 1600 (register
  // 0x076B and scale as used by RadioLib getFrequencyError(); not in DS Table 12-1 - VERIFY).
  uint32_t efe = (((uint32_t)fe[0] << 16) | ((uint32_t)fe[1] << 8) | fe[2]) & 0x0FFFFFUL;
  const int32_t efe_s = (efe & 0x80000UL) ? (int32_t)efe - 0x100000L : (int32_t)efe;
  const float ferr = 1.55f * (float)efe_s * kProfiles[g_sx.active.rate_code].bw_khz / 1600.0f;
  const int rssi_ddbm = -5 * (int)ps[0];              // RssiPkt / 2 dBm
  const int snr_raw = ps[1] < 128 ? (int)ps[1] : (int)ps[1] - 256;   // two's complement octet
  const int snr_cdb = 25 * snr_raw;                   // SnrPkt / 4 dB
  uint8_t len = 0;
  bool good = crc_ok;
  if (good) {
    uint8_t off = 0;
    if (!sx_ok(g_radio.rxBuffer(&len, &off), kEvSpiError, "GetRxBufferStatus")) return;
    if (len == 0) {
      good = false;
      sx_event(kEvRxError, "RxDone with an empty payload");
    } else if (!sx_ok(g_radio.readPayload(g_rx_buf, len, off), kEvSpiError, "ReadBuffer")) {
      return;
    }
  }
  if (!sx_ok(g_radio.clearIrq(RADIOLIB_SX126X_IRQ_ALL), kEvSpiError, "ClearIrqStatus")) return;
  g_sx.holding = false;
  if (good) {
    ++g_sx.rx_ok;
    sx_hex(g_rx_buf, len);
  } else {
    ++g_sx.rx_crc;
    g_hex[0] = 0;                                     // payload discarded (contract 14)
  }
  Bridge.notify("rf_rx", String(g_hex), rssi_ddbm, snr_cdb, (int32_t)lrintf(ferr), good ? 1 : 0);
}

static bool sx_start_tx() {
  const TxSlot& s = g_sx.txq[g_sx.tx_head];
  g_sx.lbt_waiting = false;
  if (!sx_set_preamble(s.wake ? kSxWakePreamble : kSxPreamble)) return false;
  g_sx.tx_airtime_ms = airtime_ms(s.len);             // includes the programmed preamble
  const uint32_t timeout = g_sx.tx_airtime_ms + g_sx.tx_airtime_ms / 2 + kTxMarginMs;
  if (!sx_ok(g_radio.startTransmitTimed(s.data, s.len, timeout), kEvSpiError, "start TX")) return false;
  sx_set_state(kSxTx);
  g_sx.timeout_ms = timeout + kDeadlineSlackMs;
  return true;
}

static void sx_tx_finished(bool ok) {
  const uint8_t len = g_sx.txq[g_sx.tx_head].len;
  g_sx.tx_head = (uint8_t)((g_sx.tx_head + 1) % kTxSlots);
  --g_sx.tx_count;
  char m[40];
  snprintf(m, sizeof m, "T|%u|%lu|%u", ok ? 1u : 0u, (unsigned long)g_sx.tx_airtime_ms, (unsigned)len);
  sx_evt(m);
}

static bool sx_start_cad(bool for_tx) {
  if (!sx_ok(g_radio.startChannelScan(), kEvSpiError, "startChannelScan")) return false;
  g_sx.cad_for_tx = for_tx;
  sx_set_state(kSxCad);
  g_sx.timeout_ms = (uint32_t)(symbol_ms(g_sx.active) * (kCadMaxSymbols + 2)) + kDeadlineSlackMs;
  return true;
}

// A LoRa header seen but no RxDone yet: a packet is arriving - do not abort it.
static bool sx_reception_in_progress(uint32_t now) {
  if (g_sx.holding) {
    if (now - g_sx.hold_t0 < g_sx.hold_ms) return true;
    g_sx.holding = false;                             // bounded hold expired: proceed
    return !sx_ok(g_radio.clearIrq(RADIOLIB_SX126X_IRQ_HEADER_VALID), kEvSpiError, "ClearIrqStatus");
  }
  uint16_t irq = 0;
  if (!sx_ok(g_radio.irqStatus(&irq), kEvSpiError, "GetIrqStatus")) return true;
  if (irq & RADIOLIB_SX126X_IRQ_HEADER_VALID) {
    g_sx.holding = true;
    g_sx.hold_t0 = now;
    g_sx.hold_ms = airtime_ms(255) + kTxMarginMs;     // longest possible packet
    return true;
  }
  return false;
}

static void sx_health(uint32_t now) {
  g_sx.last_health = now;
  uint8_t status = 0;
  if (!sx_ok(g_radio.chipStatus(&status), kEvSpiError, "GetStatus")) return;
  if ((status & kChipModeMask) != kChipModeRx) {
    // A SetRx that could not be executed, or a chip-internal reset (supply dip): after
    // the latter the chip runs on its POR defaults, so only a full re-initialisation
    // restores a known configuration.
    char m[48];
    snprintf(m, sizeof m, "chip not in RX (status 0x%02X)", (unsigned)status);
    sx_fault(kEvModeRestored, m);
    return;
  }
  uint16_t err = 0;
  if (!sx_check_device_errors("in RX", &err)) return;
  if (err & kOpErrCalibMask) g_sx.calib_error = true;
  const int gain = sx_check_rx_gain(g_sx.active.boost);
  if (gain != 0) return;                              // as requested, or a fault already raised
  if (!sx_ok(g_radio.setRxBoostedGainMode(g_sx.active.boost, true), kEvSpiError, "setRxBoostedGainMode")) return;
  sx_event(kEvRxGainReasserted, "Rx gain register re-asserted");
  if (sx_check_rx_gain(g_sx.active.boost) == 0) sx_fault(kEvConfigFailed, "Rx gain does not hold");
}

// DIO1 after SetCad: the IRQ status (status-checked) says DETECTED or DONE; anything
// else is a fault. The chip is in STDBY_RC (RadioLib's CAD exit mode CAD_ONLY).
static void sx_on_cad_irq(uint32_t now) {
  uint16_t irq = 0;
  if (!sx_ok(g_radio.irqStatus(&irq), kEvSpiError, "GetIrqStatus (CAD)")) return;
  if (!(irq & (RADIOLIB_SX126X_IRQ_CAD_DETECTED | RADIOLIB_SX126X_IRQ_CAD_DONE))) {
    sx_fault(kEvSpiError, "DIO1 without CadDone");    // only the CAD IRQs are routed to DIO1
    return;
  }
  const bool busy = (irq & RADIOLIB_SX126X_IRQ_CAD_DETECTED) != 0;
  if (!g_sx.cad_for_tx) {
    char m[32];
    snprintf(m, sizeof m, "C|%u|%lu", busy ? 1u : 0u, (unsigned long)(now - g_sx.t_state));
    sx_evt(m);
    sx_resume_rx();
  } else if (busy) {
    ++g_sx.cad_busy;
    if (!g_sx.lbt_waiting) {
      g_sx.lbt_waiting = true;
      g_sx.lbt_t0 = now;
    }
    const uint32_t units = 1 + (xorshift32() & 3);
    g_sx.backoff_t0 = now;
    g_sx.backoff_ms = (uint32_t)(symbol_ms(g_sx.active) * kCadBackoffSymbols * units);
    sx_resume_rx();
  } else if (g_sx.cfg_pending) {
    sx_resume_rx();                                   // new configuration first; the next pass re-checks the channel
  } else {
    sx_start_tx();                                    // channel free: transmit now
  }
}

static void sx_service() {
  const uint32_t now = millis();
  switch (g_sx.state) {
    case kSxOff:
      if (now - g_sx.t_state >= g_sx.retry_ms) sx_begin_reset();
      break;
    case kSxReset:
      if (digitalRead(PIN_SX_BUSY) == LOW) {
        sx_init();
      } else if (now - g_sx.t_state > kSxBusyTimeoutMs) {
        sx_fault(kEvBusyTimeout, "BUSY stuck high after reset");
      }
      break;
    case kSxRx: {
      if (digitalRead(PIN_SX_DIO1) == HIGH) {
        sx_on_rx_irq();
        break;
      }
      const bool want_tx = g_sx.tx_count > 0 && now - g_sx.backoff_t0 >= g_sx.backoff_ms;
      if (!g_sx.cfg_pending && !want_tx && !g_sx.cad_request) {
        if (now - g_sx.last_health >= kHealthPeriodMs) sx_health(now);
        break;
      }
      if (sx_reception_in_progress(now) || g_sx.state != kSxRx) break;
      if (g_sx.cfg_pending) {
        if (sx_apply(g_sx.wanted)) sx_status();
      } else if (want_tx) {
        if (!g_sx.active.cad) {
          sx_start_tx();
        } else if (g_sx.lbt_waiting && now - g_sx.lbt_t0 >= kLbtMaxWaitMs) {
          ++g_sx.cad_forced;                          // busy for 2 s: transmit anyway
          sx_start_tx();
        } else {
          sx_start_cad(true);
        }
      } else {
        g_sx.cad_request = false;
        sx_start_cad(false);
      }
      break;
    }
    case kSxCad:
      if (digitalRead(PIN_SX_DIO1) == HIGH) {
        sx_on_cad_irq(now);
      } else if (now - g_sx.t_state >= g_sx.timeout_ms) {
        sx_fault(kEvCadTimeout, "no CadDone before the deadline");
      }
      break;
    case kSxTx:
      if (digitalRead(PIN_SX_DIO1) == HIGH) {
        uint16_t irq = 0;
        if (!sx_ok(g_radio.irqStatus(&irq), kEvSpiError, "GetIrqStatus")) break;
        if (!(irq & RADIOLIB_SX126X_IRQ_TX_DONE)) {
          sx_fault(kEvTxTimeout, "DIO1 without TxDone");
          break;
        }
        if (!sx_ok(g_radio.finishTransmit(), kEvSpiError, "finishTransmit")) break;
        ++g_sx.tx_ok;
        sx_tx_finished(true);
        sx_resume_rx();
      } else if (now - g_sx.t_state >= g_sx.timeout_ms) {
        sx_tx_finished(false);
        sx_fault(kEvTxTimeout, "no TxDone before the deadline");
      }
      break;
  }
  if (now - g_sx.last_status >= kStatusPeriodMs) sx_status();
}

// --- SX126x Bridge services (provide_safe: executed in the loop thread) ------
static int hex_nibble(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

static int queue_tx(const String& hex, bool wake) {
  if (g_sx.state == kSxOff && g_sx.init_failures >= 3) return -2;   // radio down, not recovering
  const size_t n = hex.length();
  if (n < 2 || (n & 1) || n > 2 * kRfTxMaxOctets) return -1;
  if (g_sx.tx_count >= kTxSlots) return 0;
  TxSlot& s = g_sx.txq[(g_sx.tx_head + g_sx.tx_count) % kTxSlots];
  const char* p = hex.c_str();
  for (size_t i = 0; i < n / 2; ++i) {
    const int hi = hex_nibble(p[2 * i]), lo = hex_nibble(p[2 * i + 1]);
    if (hi < 0 || lo < 0) return -1;
    s.data[i] = (uint8_t)((hi << 4) | lo);
  }
  s.len = (uint8_t)(n / 2);
  s.wake = wake;
  ++g_sx.tx_count;
  return 1;
}

static int rpc_rf_tx(String hex) { return queue_tx(hex, false); }
static int rpc_rf_wake(String hex) { return queue_tx(hex, true); }

static int rpc_rf_cfg(int ch, int code, int pwr, int flags) {
  if (ch < -1 || ch > 83 || code < -1 || code > 7 || pwr < -1 || pwr > 3 || flags < -1 || flags > 3) return -1;
  SxConfig c = g_sx.cfg_pending ? g_sx.wanted : g_sx.active;
  if (ch >= 0) c.channel = (uint8_t)ch;
  if (code >= 0) c.rate_code = (uint8_t)code;
  if (pwr >= 0) c.power_code = (uint8_t)pwr;
  if (flags >= 0) {
    c.cad = (flags & 1) != 0;
    c.boost = (flags & 2) != 0;
  }
  g_sx.wanted = c;
  g_sx.cfg_pending = true;
  if (g_sx.state != kSxRx) return 2;                  // applied when idle / at re-initialisation
  if (sx_reception_in_progress(millis())) return 2;
  const bool ok = sx_apply(c);
  sx_status();
  return ok ? 1 : 2;
}

// Ground AFC (contract 24.8): one RF offset for RX and TX, written by sx_apply() in
// STDBY_RC between operations - never during a transmission or a CAD. Returns
//  1  in effect now, or held in g_sx.wanted (status flag b4) and applied as soon as the
//     transmission, the CAD or the packet being received ends, before any further
//     reception or transmission; the packet in progress is the last one with the old
//     offset (link.AfcLoop ignores packets ending within one air time of a change);
// -1  |hz| > kFoffMaxHz (nothing changed);
// -2  the radio is down (OFF / RESET), or a fault occurred during the call (IRQ-status
//     read or the configuration itself; the radio is then reset): the previous offset
//     stays wanted, so the MCU never runs with a value the GDS was told did not take
//     effect. The offset survives radio resets (g_sx.wanted), not an MCU reboot.
static int rpc_rf_foff(int hz) {
  if (hz < -kFoffMaxHz || hz > kFoffMaxHz) return -1;
  if (g_sx.state == kSxOff || g_sx.state == kSxReset) return -2;
  SxConfig c = g_sx.cfg_pending ? g_sx.wanted : g_sx.active;
  if (!g_sx.cfg_pending && c.foff_hz == (int32_t)hz) return 1;   // already in effect
  const int32_t previous = c.foff_hz;
  c.foff_hz = (int32_t)hz;
  g_sx.wanted = c;
  g_sx.cfg_pending = true;
  if (g_sx.state != kSxRx) return 1;                  // TX or CAD: applied when it ends
  const bool arriving = sx_reception_in_progress(millis());
  if (g_sx.state == kSxRx) {                          // else the IRQ-status read raised a fault
    if (arriving) return 1;                           // applied after this packet
    if (sx_apply(c)) {
      sx_status();
      return 1;
    }
  }
  g_sx.wanted.foff_hz = previous;                     // the fault path re-initialises with g_sx.wanted
  return -2;
}

static int rpc_rf_noise() {
  if (g_sx.state != kSxRx) return kNoiseNa;
  int16_t v[kNoiseSamples];
  for (uint8_t i = 0; i < kNoiseSamples; ++i) {       // ~8 ms in total
    if (!sx_ok(g_radio.rssiInst(&v[i]), kEvSpiError, "GetRssiInst")) return kNoiseNa;
    delay(1);
  }
  for (uint8_t i = 1; i < kNoiseSamples; ++i) {       // insertion sort, 8 elements
    const int16_t x = v[i];
    uint8_t j = i;
    while (j > 0 && v[j - 1] > x) {
      v[j] = v[j - 1];
      --j;
    }
    v[j] = x;
  }
  return (v[kNoiseSamples / 2 - 1] + v[kNoiseSamples / 2]) / 2;
}

static int rpc_rf_cad() {
  if (g_sx.state == kSxOff) return -2;
  if (g_sx.state != kSxRx || g_sx.cad_request) return 0;
  g_sx.cad_request = true;
  return 1;
}

static void sx_setup() {
  // StationHal (explicit SPI bus) makes RadioLib skip SPI.begin(), which initialises the
  // SPI2 device if needed and applies its default pin multiplexing - D10 = SPI2_NSS
  // included (core init_dev_apply_pinctrl). Called once, before NSS becomes a GPIO here
  // and in RadioLib's Module::init(); never after.
  SPI.begin();
  pinMode(PIN_SX_NSS, OUTPUT);                        // chip deselected from boot on
  digitalWrite(PIN_SX_NSS, HIGH);
  pinMode(PIN_SX_DIO1, INPUT);
  pinMode(PIN_SX_BUSY, INPUT);
  pinMode(PIN_SX_TXEN, OUTPUT);
  digitalWrite(PIN_SX_TXEN, LOW);
  pinMode(PIN_SX_RXEN, OUTPUT);
  digitalWrite(PIN_SX_RXEN, LOW);
  // Compiled defaults until the GDS pushes station.toml (contract 9: CH 23, profile 1,
  // power code 0, CAD before TX and Rx boosted gain on; no frequency offset).
  g_sx.wanted = {23, 1, 0, true, true, 0};
  g_sx.active = g_sx.wanted;
  g_rng ^= micros();
  if (g_rng == 0) g_rng = 0x2545F491UL;               // xorshift must not start at 0
  Bridge.provide_safe("rf_tx", rpc_rf_tx);
  Bridge.provide_safe("rf_cfg", rpc_rf_cfg);
  Bridge.provide_safe("rf_foff", rpc_rf_foff);
  Bridge.provide_safe("rf_noise", rpc_rf_noise);
  Bridge.provide_safe("rf_cad", rpc_rf_cad);
  Bridge.provide_safe("rf_wake", rpc_rf_wake);
  g_sx.retry_ms = 0;                                  // first initialisation immediately
  sx_set_state(kSxOff);
}
#endif  // DSSQ_RADIO_SX126X

// ---------------------------------------------------------------- setup/loop
void setup() {
  pinMode(PIN_RCU_M0, OUTPUT);
  pinMode(PIN_RCU_M1, OUTPUT);
  digitalWrite(PIN_RCU_M0, LOW);                    // station radio: NORMAL (listening)
  digitalWrite(PIN_RCU_M1, LOW);
  pinMode(PIN_RCU_AUX, INPUT_PULLUP);
  const int outs[] = {PIN_LED_AOS, PIN_LED_ALARM, PIN_LED_UPLINK, PIN_BUZZER};
  for (int p : outs) { pinMode(p, OUTPUT); digitalWrite(p, LOW); }

  Bridge.begin();                                   // waits for the arduino-router service
  Bridge.provide_safe("fp_line", rpc_fp_line);
  Bridge.provide_safe("rcu_mode", rpc_rcu_mode);
  Wire.begin();                                     // header SDA/SCL (I2C4): the CardKB
  Wire.setClock(100000);
#if DSSQ_RADIO_SX126X
  sx_setup();
#endif
}

void loop() {
  static uint32_t last_kb = 0;
  const uint32_t now = millis();
  if (now - last_kb >= kKeyScanMs) {
    last_kb = now;
    const int k = read_cardkb();
    g_kb = k >= 0;
    if (k > 0) on_key((uint8_t)k);
  }
  if (now - g_last_hello_ms >= kHelloMs) {
    g_last_hello_ms = now;
    char h[48];
    snprintf(h, sizeof h, "H|%s|1|%d", kFw, g_kb ? 1 : 0);
    send_line(h);
  }
  annunciators();
#if DSSQ_RADIO_SX126X
  sx_service();
#endif
}
