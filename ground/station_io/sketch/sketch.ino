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
//     below): RadioLib 7.8.1, Rx boosted gain with retention, per-packet RSSI / SNR /
//     frequency error, channel-activity detection before transmission, bounded BUSY /
//     TX / CAD timeouts and automatic reset-and-reconfigure recovery
//     (contract v3 sections 2.2 and 14; Semtech SX1261/2 datasheet rev 2.2).
//
//  Bridge services (Python side: ground/dssq/display/frontpanel.py, radio/sx126x.py)
//   provide_safe "fp_line" (String)   S|aos|alarm|uplink   Q|<confirm id or -1>
//   provide_safe "rcu_mode" (int)     set E22 mode 0..3, returns AUX (1 = ready)
//   notify       "fp_in"   (String)   K|<typed text>  CMD|<text>  CONFIRM|<id>
//                                     H|<fw>|<rcu>|<kb>  (hello, every 5 s)
//   SX126x build only:
//   provide_safe "rf_tx"   (String hex) -> 1 queued, 0 queue full, -1 rejected, -2 radio down
//   provide_safe "rf_cfg"  (ch, code, pwr, flags; -1 = keep) -> 1 applied, 2 queued, -1 range
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
//    2. in sketch.yaml, un-comment the library line `- RadioLib (7.8.1)` under
//       profiles: default: libraries: (or add RadioLib 7.8.1 in the Library Manager);
//    3. set [radio] model = "SX126x" in ground/config/station.toml;
//    4. RadioLib 7.8.1 knows the UNO Q (BuildOpt.h: ARDUINO_ARCH_ZEPHYR &&
//       ARDUINO_UNO_Q) but not the VENTUNO Q, so its ArduinoHal.cpp would be built
//       without the PinMode / PinStatus casts the Zephyr core's pinMode() /
//       digitalWrite() / attachInterrupt() require. Until a RadioLib release lists the
//       VENTUNO Q, build with arduino-cli and
//         --build-property "compiler.cpp.extra_flags=-DRADIOLIB_ARDUINOHAL_PIN_MODE_CAST=(PinMode)
//           -DRADIOLIB_ARDUINOHAL_PIN_STATUS_CAST=(PinStatus)
//           -DRADIOLIB_ARDUINOHAL_INTERRUPT_MODE_CAST=(PinStatus)"
//       or extend that BuildOpt.h line of the installed library with
//       `|| defined(ARDUINO_VENTUNO_Q)`. VERIFY with the first real build.
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
static const char* kFw = "DSSQ-IO 3.0 SX126x";
#else
static const char* kFw = "DSSQ-IO 3.0";
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
//  SX126x ground radio (contract v3 2.2 and 14; Semtech SX1261/2 DS rev 2.2)
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
//  Rules applied (contract 14): every SPI command waits for BUSY with a bounded
//  timeout (kSxBusyTimeoutMs, RadioLib Module); transmissions start with a chip TX
//  timeout of 1.5 x air time + margin and a software deadline behind it; CAD has a
//  deadline; any BUSY / SPI / status failure resets the chip through NRST, re-runs
//  begin(), re-applies the configuration and the Rx boosted gain (with retention:
//  0x029F = 01, 0x02A0 = 08, 0x02A1 = AC, written by RadioLib setRxBoostedGainMode(
//  true, true)) and reports an E| event - the loop never hangs on the radio.
//  Packet status (RSSI, SNR) and the frequency-error register are read right after
//  RxDone, before the payload; a payload with a bad CRC is discarded and reported.
//  Known limitations (DS ch. 15) as covered by RadioLib 7.8.1 in the calls used here:
//  15.1 BW500 modulation quality - fixSensitivity() in stageMode(TX); 15.2 PA
//  clamping - fixPaClamping() in begin(); 15.3 implicit-header timeout - not
//  applicable (explicit header, continuous RX); 15.4 inverted IQ - fixInvertedIQ() in
//  setPacketParams(). TCXO: begin() runs SetDIO3AsTCXOCtrl and then the full
//  calibration; setFrequency() image-calibrates the 430-440 MHz band (other
//  channels: calibrateImageRejection(f +- 4 MHz)). OCP: RadioLib keeps the 60 mA POR
//  value through setOutputPower(), so the limit is set to 140 mA explicitly.
//  Residual risk: RadioLib's begin() polls BUSY without a timeout after its
//  calibration; begin() is therefore only called once BUSY has been seen LOW after
//  an NRST pulse, and failed initialisations back off exponentially (max 60 s).
// =============================================================================
static const int PIN_SX_NSS = 10, PIN_SX_BUSY = 8, PIN_SX_DIO1 = 9;
static const int PIN_SX_NRST = A1, PIN_SX_RXEN = A2, PIN_SX_TXEN = A3;

static const float kSxTcxoV = 1.8f;                  // DIO3 TCXO supply - VERIFY module
static const bool kSxDio2RfSwitch = false;           // RXEN/TXEN drive the switch - VERIFY module
static const bool kSxUseLdo = false;                 // DC-DC regulator
static const uint8_t kSxSyncWord = 0x12;             // private network (register 0x1424)
static const uint16_t kSxPreamble = 12;
static const uint16_t kSxWakePreamble = 32;          // = flight kSxWakePreambleSym (duty-cycled RX)
static const uint8_t kSxCodingRate = 5;              // 4/5
static const float kSxOcpMa = 140.0f;                // DS 13.1.14: OCP after SetPaConfig (SX1262)
static const uint32_t kSxBusyTimeoutMs = 100;        // per SPI command (RadioLib default 1000)
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
static const size_t kRfTxMaxOctets = 112;            // hex request fits the 256-octet RPC buffer
static const uint8_t kTxSlots = 4;
static const float kLdroSymbolMs = 16.38f;           // DS 6.1.1.4
static const uint16_t kRegRxGain = 0x08AC, kRegRxGainRetention = 0x029F, kRegFreqError = 0x076B;
static const uint8_t kRxGainBoosted = 0x96, kRxGainPowerSaving = 0x94;   // DS Table 9-3
static const uint8_t kChipModeMask = 0x70, kChipModeRx = 0x50;           // GetStatus b6:4
static const uint16_t kOpErrReinitMask = 0x0060;     // DS Table 13-86: XOSC_START_ERR, PLL_LOCK_ERR

struct SxProfile { uint8_t sf; float bw_khz; };
static const SxProfile kProfiles[8] = {{12, 31.25f}, {12, 62.5f}, {12, 125.0f}, {11, 125.0f},
                                       {10, 125.0f}, {9, 125.0f}, {8, 125.0f}, {7, 125.0f}};
static const int8_t kDriveDbm[4] = {22, 17, 14, 10};

// RadioLib keeps register access and raw command helpers protected; this thin
// subclass exposes exactly what verification and status-checked I/O need.
template <class Chip>
class StationRadio : public Chip {
 public:
  explicit StationRadio(Module* module) : Chip(module) {}

  int16_t readReg(uint16_t addr, uint8_t* data, uint8_t n) { return this->readRegister(addr, data, n); }

  int16_t irqStatus(uint16_t* irq) {                  // GetIrqStatus with the SPI status checked
    uint8_t d[2] = {0, 0};
    const int16_t st = this->getMod()->SPIreadStream(RADIOLIB_SX126X_CMD_GET_IRQ_STATUS, d, 2);
    *irq = (uint16_t)(((uint16_t)d[0] << 8) | d[1]);
    return st;
  }

  int16_t clearIrq(uint16_t mask) { return this->clearIrqStatus(mask); }

  int16_t packetStatus(uint8_t* st3) {               // RssiPkt, SnrPkt, SignalRssiPkt (DS 13.5.3)
    return this->getMod()->SPIreadStream(RADIOLIB_SX126X_CMD_GET_PACKET_STATUS, st3, 3);
  }

  int16_t rxBuffer(uint8_t* len, uint8_t* offset) {  // GetRxBufferStatus
    uint8_t d[2] = {0, 0};
    const int16_t st = this->getMod()->SPIreadStream(RADIOLIB_SX126X_CMD_GET_RX_BUFFER_STATUS, d, 2);
    *len = d[0];
    *offset = d[1];
    return st;
  }

  int16_t readPayload(uint8_t* data, uint8_t n, uint8_t offset) { return this->readBuffer(data, n, offset); }

  int16_t rssiInst(int16_t* ddbm) {                  // GetRssiInst: -RssiInst / 2 dBm
    uint8_t raw = 0;
    const int16_t st = this->getMod()->SPIreadStream(RADIOLIB_SX126X_CMD_GET_RSSI_INST, &raw, 1);
    *ddbm = (int16_t)(-5 * (int16_t)raw);
    return st;
  }

  uint8_t chipStatus() { return this->getStatus(); }
  uint16_t deviceErrors() { return this->getDeviceErrors(); }
  int16_t clearErrors() { return this->clearDeviceErrors(); }

  // LoRa transmission with the chip's own TX timeout (DS 13.1.4 SetTx, 15.625 us
  // steps) and a bounded wait for the PA ramp (BUSY low); RadioLib's startTransmit()
  // uses no chip timeout and waits for BUSY without a bound.
  int16_t startTransmitTimed(const uint8_t* data, size_t len, uint32_t timeout_ms) {
    int16_t st = this->standby();
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

enum SxState : uint8_t { kSxOff = 0, kSxRx = 1, kSxCad = 2, kSxTx = 3, kSxReset = 4 };
enum SxEvent : uint8_t {
  kEvBusyTimeout = 1, kEvSpiError = 2, kEvTxTimeout = 3, kEvInitFailed = 4, kEvRecovered = 5,
  kEvRxGainReasserted = 6, kEvCadTimeout = 7, kEvDeviceErrors = 8, kEvRxError = 9,
  kEvConfigFailed = 10, kEvModeRestored = 11
};

struct SxConfig {
  uint8_t channel, rate_code, power_code;
  bool cad, boost;
};

struct TxSlot {
  uint8_t len;
  bool wake;                                          // send with the wake-up preamble
  uint8_t data[kRfTxMaxOctets];
};

struct SxCtx {
  SxState state;
  SxConfig active, wanted;
  bool cfg_pending, cad_request, cad_for_tx, in_fault, holding, lbt_waiting;
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
static Module g_sx_mod(PIN_SX_NSS, PIN_SX_DIO1, PIN_SX_NRST, PIN_SX_BUSY);
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

static uint32_t airtime_ms(size_t len) {             // RadioLib: Semtech formula, microseconds
  return (uint32_t)((g_radio.getTimeOnAir(len) + 999UL) / 1000UL);
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

static uint8_t sx_flags() {
  return (uint8_t)((g_sx.active.cad ? 1 : 0) | (g_sx.active.boost ? 2 : 0) |
                   (g_sx.rx_gain_reg == kRxGainBoosted ? 4 : 0));
}

static void sx_status() {
  char m[224];
  snprintf(m, sizeof m, "S|%s|%s|%u|%u|%u|%u|%u|%u|%lu|%lu|%lu|%lu|%lu|%lu|%lu|%lu|%lu|%u|%lu", kFw, kSxChip,
           (unsigned)g_sx.state, (unsigned)g_sx.active.channel, (unsigned)g_sx.active.rate_code,
           (unsigned)g_sx.active.power_code, (unsigned)sx_flags(), (unsigned)g_sx.rx_gain_reg,
           (unsigned long)g_sx.rx_ok, (unsigned long)g_sx.rx_crc, (unsigned long)g_sx.rx_hdr,
           (unsigned long)g_sx.tx_ok, (unsigned long)g_sx.tx_fail, (unsigned long)g_sx.cad_busy,
           (unsigned long)g_sx.cad_forced, (unsigned long)g_sx.faults, (unsigned long)g_sx.resets,
           (unsigned)g_sx.tx_count, (unsigned long)(millis() / 1000UL));
  sx_evt(m);
  g_sx.last_status = millis();
}

// Any radio failure ends here: report, drop the transmission in flight (COP-1
// retransmits), and schedule a reset with exponential backoff.
static void sx_fault(uint8_t code, const char* text) {
  ++g_sx.faults;
  g_sx.in_fault = true;
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

static bool sx_ok(int16_t st, uint8_t code, const char* what) {
  if (st == RADIOLIB_ERR_NONE) return true;
  char m[64];
  snprintf(m, sizeof m, "%s failed (%d)", what, (int)st);
  sx_fault(st == RADIOLIB_ERR_SPI_CMD_TIMEOUT ? (uint8_t)kEvBusyTimeout : code, m);
  return false;
}

// Reads the Rx gain register back: 1 = as requested, 0 = mismatch, -1 = SPI fault (raised).
static int sx_check_rx_gain(bool boost) {
  uint8_t v = 0;
  if (!sx_ok(g_radio.readReg(kRegRxGain, &v, 1), kEvSpiError, "read 0x08AC")) return -1;
  g_sx.rx_gain_reg = v;
  return v == (boost ? kRxGainBoosted : kRxGainPowerSaving) ? 1 : 0;
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

// Applies a configuration to the chip and restarts continuous RX. Any failure is a
// fault: the radio is then reset and re-initialised with g_sx.wanted. RadioLib's
// writeRegister() does not report the SPI status (it always returns OK), so the
// safety-relevant registers (Rx gain and its retention entry) are read back.
static bool sx_apply(const SxConfig& c) {
  const SxProfile& p = kProfiles[c.rate_code];
  if (!sx_ok(g_radio.standby(), kEvSpiError, "standby")) return false;
  if (!sx_ok(g_radio.setFrequency(410.125f + (float)c.channel), kEvConfigFailed, "setFrequency")) return false;
  if (!sx_ok(g_radio.setSpreadingFactor(p.sf), kEvConfigFailed, "setSpreadingFactor")) return false;
  if (!sx_ok(g_radio.setBandwidth(p.bw_khz), kEvConfigFailed, "setBandwidth")) return false;
  if (!sx_ok(g_radio.forceLDRO(symbol_ms(c) >= kLdroSymbolMs), kEvConfigFailed, "forceLDRO")) return false;
  if (!sx_ok(g_radio.setOutputPower(kDriveDbm[c.power_code]), kEvConfigFailed, "setOutputPower")) return false;
  if (!sx_ok(g_radio.setCurrentLimit(kSxOcpMa), kEvConfigFailed, "setCurrentLimit")) return false;
  if (!sx_ok(g_radio.setRxBoostedGainMode(c.boost, true), kEvConfigFailed, "setRxBoostedGainMode")) return false;
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
  g_sx.active = c;
  g_sx.cfg_pending = false;
  return sx_start_rx();
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
  const int16_t st = g_radio.begin(410.125f + (float)c.channel, p.bw_khz, p.sf, kSxCodingRate, kSxSyncWord,
                                   kDriveDbm[c.power_code], kSxPreamble, kSxTcxoV, kSxUseLdo);
  if (!sx_ok(st, kEvInitFailed, "begin")) return;
  g_sx.preamble = kSxPreamble;
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
  const uint8_t mode = g_radio.chipStatus() & kChipModeMask;
  if (mode != kChipModeRx) {
    sx_event(kEvModeRestored, "chip not in RX - receiver restarted");
    if (!sx_start_rx()) return;
  }
  const uint16_t err = g_radio.deviceErrors();
  if (err != 0) {                                     // report once, then clear (DS 13.6.2)
    char m[48];
    snprintf(m, sizeof m, "OpError 0x%04X", (unsigned)err);
    if (!sx_ok(g_radio.clearErrors(), kEvSpiError, "ClearDeviceErrors")) return;
    if (err & kOpErrReinitMask) {                     // oscillator / PLL: full re-initialisation
      sx_fault(kEvDeviceErrors, m);
      return;
    }
    sx_event(kEvDeviceErrors, m);
  }
  const int gain = sx_check_rx_gain(g_sx.active.boost);
  if (gain != 0) return;                              // as requested, or a fault already raised
  if (!sx_ok(g_radio.setRxBoostedGainMode(g_sx.active.boost, true), kEvSpiError, "setRxBoostedGainMode")) return;
  sx_event(kEvRxGainReasserted, "Rx gain register re-asserted");
  if (sx_check_rx_gain(g_sx.active.boost) == 0) sx_fault(kEvConfigFailed, "Rx gain does not hold");
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
        sx_apply(g_sx.wanted);
        sx_status();
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
        const int16_t r = g_radio.getChannelScanResult();
        if (r != RADIOLIB_LORA_DETECTED && r != RADIOLIB_CHANNEL_FREE) {
          sx_ok(r, kEvSpiError, "CAD result");
          break;
        }
        const bool busy = r == RADIOLIB_LORA_DETECTED;
        if (!g_sx.cad_for_tx) {
          char m[32];
          snprintf(m, sizeof m, "C|%u|%lu", busy ? 1u : 0u, (unsigned long)(now - g_sx.t_state));
          sx_evt(m);
          sx_start_rx();
        } else if (busy) {
          ++g_sx.cad_busy;
          if (!g_sx.lbt_waiting) {
            g_sx.lbt_waiting = true;
            g_sx.lbt_t0 = now;
          }
          const uint32_t units = 1 + (xorshift32() & 3);
          g_sx.backoff_t0 = now;
          g_sx.backoff_ms = (uint32_t)(symbol_ms(g_sx.active) * kCadBackoffSymbols * units);
          sx_start_rx();
        } else {
          sx_start_tx();                              // channel free: transmit now
        }
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
        sx_start_rx();
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

static int rpc_rf_noise() {
  if (g_sx.state != kSxRx) return -32768;
  int16_t v[kNoiseSamples];
  for (uint8_t i = 0; i < kNoiseSamples; ++i) {       // ~8 ms in total
    if (g_radio.rssiInst(&v[i]) != RADIOLIB_ERR_NONE) return -32768;
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
  pinMode(PIN_SX_DIO1, INPUT);
  pinMode(PIN_SX_BUSY, INPUT);
  pinMode(PIN_SX_TXEN, OUTPUT);
  digitalWrite(PIN_SX_TXEN, LOW);
  pinMode(PIN_SX_RXEN, OUTPUT);
  digitalWrite(PIN_SX_RXEN, LOW);
  // Compiled defaults until the GDS pushes station.toml (contract 9: CH 23, profile 1,
  // power code 0, CAD before TX and Rx boosted gain on).
  g_sx.wanted = {23, 1, 0, true, true};
  g_sx.active = g_sx.wanted;
  g_rng ^= micros();
  if (g_rng == 0) g_rng = 0x2545F491UL;               // xorshift must not start at 0
  Bridge.provide_safe("rf_tx", rpc_rf_tx);
  Bridge.provide_safe("rf_cfg", rpc_rf_cfg);
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
