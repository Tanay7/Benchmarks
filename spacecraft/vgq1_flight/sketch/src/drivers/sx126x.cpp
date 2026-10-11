// =============================================================================
//  SX1268 / SX1262 driver on RadioLib 7.8.1 - see sx126x.h for the rules it
//  implements and the datasheet sections behind them.
//
//  State machine, one step per poll() call; no step waits longer than the BUSY
//  bound except the (re)initialisation, which is ~20 ms of SPI commands and
//  calibration and runs only at boot and after a fault:
//
//      OFF --begin()--> RX <--> CAD --> TX --> RX       (HIBERNATE: duty-cycled RX)
//      any fault --> BACKOFF --> RESET_LOW --> RESET_WAIT --> RX + FAULT(cleared)
//
//  Every RadioLib call that starts or stages a mode (CAD, TX) is preceded by
//  SetStandby: it is the configuration mode the datasheet requires (DS 14.2 step
//  1) and RadioLib's standby() is the only call that first wakes a chip sleeping
//  in the RxDutyCycle loop (an NSS edge without a BUSY wait, DS 8.2.2 / 13.1.7).
// =============================================================================
#include "sx126x.h"   // includes ../config.h first: VGQ_RADIO, VGQ_SX126X_CHIP, PIN_SX_*, kSx*

#if VGQ_RADIO == VGQ_RADIO_SX126X

#include <math.h>
#include <string.h>

namespace vgq {

namespace {

const uint32_t kSpiHz = 2000000;        // conservative for header-pin wiring (chip limit 16 MHz)
// BUSY bounds. The longest legitimate BUSY-high periods are the full calibration
// (3.5 ms, DS 13.1.12) after the 5 ms TCXO start delay RadioLib programs, and SetTx
// from STDBY_RC (TCXO start + PLL lock + PA ramp). The guard must be shorter than
// RadioLib's per-command timeout so that it, not the library, sees a stuck BUSY.
const uint32_t kBusyGuardMs = 25;
const uint32_t kSpiTimeoutMs = 40;      // Module::spiConfig.timeout (RadioLib default 1000)
const uint32_t kResetLowMs = 2;         // NRESET low; DS 8.1: typically 100 us
const uint32_t kResetBusyMs = 50;       // BUSY must fall after the reset (STARTUP -> STDBY_RC)
const uint32_t kBackoffMinMs = 100;     // first recovery attempt
const uint32_t kBackoffMaxMs = 60000;   // back-off doubles up to this
const uint32_t kHealthyMs = 60000;      // healthy this long -> back-off restarts at the minimum
const uint32_t kHealthPeriodMs = 10000; // chip mode / OpError / Rx gain check in RX (as the station)
const uint32_t kTxWatchdogMarginMs = 500;   // beyond the chip's own TX timeout
const uint32_t kCadSymbols = 4;         // RadioLib default SetCadParams (CAD_ON_4_SYMB)
const uint32_t kCadMarginMs = 50;
const uint8_t kCrcLen = 2;              // LoRa payload CRC on
// RadioLib generic IRQ flags: DIO1 on RxDone, on a valid header (reception in
// progress, used by busy()) and on a header error; CRC errors arrive with RxDone.
const RadioLibIrqFlags_t kRxIrqFlags = RADIOLIB_IRQ_RX_DEFAULT_FLAGS;
const RadioLibIrqFlags_t kRxIrqMask = (1UL << RADIOLIB_IRQ_RX_DONE) | (1UL << RADIOLIB_IRQ_HEADER_VALID) |
                                      (1UL << RADIOLIB_IRQ_HEADER_ERR);
// The same three as SX126x IRQ register bits (Table 13-29): DIO1 is the OR of the
// raised IRQs mapped to it (DS 13.3, DioxMask), so it is high exactly while one of
// them is set; ClearIrqStatus clears only the bits it is given (13.3.4).
const uint16_t kRxDio1Irq = RADIOLIB_SX126X_IRQ_RX_DONE | RADIOLIB_SX126X_IRQ_HEADER_VALID |
                            RADIOLIB_SX126X_IRQ_HEADER_ERR;
// DIO1 high this many times in a row with none of them raised: the line or the
// IRQ read is wrong (a missed clear cannot explain it), so the radio is reset.
const uint8_t kMaxSpuriousIrq = 3;
const uint16_t kTxIrq = RADIOLIB_SX126X_IRQ_TX_DONE | RADIOLIB_SX126X_IRQ_TIMEOUT;
// Read-back references.
const uint8_t kSyncRegs[2] = {0x14, 0x24};          // 0x12 + RadioLib control bits 0x44 (0x1424)
const uint8_t kRxGainRetention[3] = {0x01, 0x08, 0xAC};   // DS 9.6: 0x029F..0x02A1
const uint8_t kOcpField = 0x3F;                     // OCP 0x08E7: 6 bits of 2.5 mA (Table 5-2)
const uint8_t kClampField = 0x1E;                   // TxClampConfig 0x08D8 bits 4..1 (DS 15.2)
// OpError (DS Table 13-86): bits 0..4 calibration (RC64k, RC13M, PLL, ADC, image),
// 5 XOSC_START_ERR, 6 PLL_LOCK_ERR; 8 PA_RAMP_ERR is reported but not fatal.
const uint16_t kCalibErrMask = 0x007F;
const uint16_t kOpErrReinitMask = 0x0060;           // oscillator / PLL: re-initialise
// Status byte (DS Table 13-76): chip mode in bits 6:4.
const uint8_t kChipModeMask = 0x70;
const uint8_t kChipModeStbyRc = 0x20;
const uint8_t kChipModeRx = 0x50;
// Frequency-error estimate, 20-bit two's complement in 0x076B..0x076D, scaled as
// RadioLib getFrequencyError() does: Hz = 1.55 x value x BW_kHz / 1600. Neither the
// register nor the scale is in DS Table 12-1: VERIFY (sign test, docs/08 T-RF-06).
const uint16_t kRegFreqError = RADIOLIB_SX126X_REG_FREQ_ERROR_RX_CRC;
const float kFreqErrScale = 1.55f / 1600.0f;

uint32_t pin_or_nc(int pin) { return pin < 0 ? (uint32_t)RADIOLIB_NC : (uint32_t)pin; }

int32_t ms_value(uint32_t dt) { return dt > (uint32_t)INT32_MAX ? INT32_MAX : (int32_t)dt; }

}  // namespace

// =============================================================================
// HAL: BUSY guard and the microsecond delay
// =============================================================================
Sx126xHal::Sx126xHal(SPIClass& bus, uint32_t busy_pin, uint32_t bound_ms)
    : ArduinoHal(bus, SPISettings(kSpiHz, MSBFIRST, SPI_MODE0)), busy_pin_(busy_pin), bound_ms_(bound_ms) {}

uint32_t Sx126xHal::digitalRead(uint32_t pin) {
  if (pin == (uint32_t)RADIOLIB_NC) return 0;
  const uint32_t level = (uint32_t)::digitalRead((pin_size_t)pin);
  if (pin != busy_pin_) return level;
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
  if (now - first_ms_ < bound_ms_) return level;
  stuck_ = true;   // report low once so the library's loop ends; the driver resets the chip
  high_ = false;
  return (uint32_t)LOW;
}

void Sx126xHal::delayMicroseconds(RadioLibTime_t us) { ::delayMicroseconds((unsigned int)(us + 1u)); }

// =============================================================================
// Construction, DIO1 interrupt
// =============================================================================
volatile bool Sx126xRadio::s_dio1_ = false;
volatile uint32_t Sx126xRadio::s_dio1_ms_ = 0;

void Sx126xRadio::isr_dio1() {
  s_dio1_ms_ = millis();
  s_dio1_ = true;
}

Sx126xRadio::Sx126xRadio(SPIClass& spi, const Sx126xHw& hw)
    : Radio(VGQ_RADIO_SX126X, hw.pa_gain_db),
      spi_(spi),
      hw_(hw),
      hal_(spi, pin_or_nc(hw.pin_busy), kBusyGuardMs),
      mod_(&hal_, pin_or_nc(hw.pin_nss), pin_or_nc(hw.pin_dio1), pin_or_nc(hw.pin_nrst), pin_or_nc(hw.pin_busy)),
      chip_(&mod_) {
  settings_.rate_code = kSxDefaultProfile;
}

const char* Sx126xRadio::name() const { return VGQ_SX126X_CHIP == 1262 ? "SX1262" : "SX1268"; }

bool Sx126xRadio::take_irq(uint32_t now, uint32_t& t_irq) {
  noInterrupts();
  const bool flagged = s_dio1_;
  const uint32_t t = s_dio1_ms_;
  s_dio1_ = false;
  interrupts();
  if (flagged) {
    t_irq = t;
    return true;
  }
  // DIO1 stays high until the IRQ status is cleared: a missed edge is still seen.
  if (digitalRead(hw_.pin_dio1) == HIGH) {
    t_irq = now;
    return true;
  }
  return false;
}

bool Sx126xRadio::check(int16_t st) { return st == RADIOLIB_ERR_NONE && !hal_.stuck(); }

bool Sx126xRadio::receiving(uint32_t now) const {
  // Header valid, no RxDone yet; bounded by the air time of the longest packet.
  return rx_active_ && now - rx_hdr_ms_ < airtime_ms(kSxMaxPayload);
}

// =============================================================================
// Bring-up and configuration
// =============================================================================
bool Sx126xRadio::begin() {
  if (hw_.pin_nss < 0 || hw_.pin_busy < 0 || hw_.pin_dio1 < 0 || hw_.pin_nrst < 0) return false;
  // Inputs first: the core configures a header pin only in pinMode(), and RadioLib
  // does so inside chip_.begin(), after the post-reset BUSY check below.
  pinMode(hw_.pin_busy, INPUT);
  pinMode(hw_.pin_dio1, INPUT);
  pinMode(hw_.pin_nrst, OUTPUT);
  digitalWrite(hw_.pin_nrst, HIGH);
  spi_.begin();                           // once, before RadioLib makes NSS a GPIO
  mod_.spiConfig.timeout = kSpiTimeoutMs;
  chip_.resetOnStartup = false;           // NRESET is pulsed here, with bounded waits
  if (hw_.pin_rxen >= 0 || hw_.pin_txen >= 0) {
    chip_.setRfSwitchPins(pin_or_nc(hw_.pin_rxen), pin_or_nc(hw_.pin_txen));
  }
  chip_.setDio1Action(isr_dio1);
  backoff_ms_ = kBackoffMinMs;

  digitalWrite(hw_.pin_nrst, LOW);
  delay(kResetLowMs);
  digitalWrite(hw_.pin_nrst, HIGH);
  const uint32_t t0 = millis();
  bool ready = false;
  for (uint32_t i = 0; i <= kResetBusyMs && !ready; ++i) {
    ready = digitalRead(hw_.pin_busy) == LOW;
    if (!ready && millis() - t0 > kResetBusyMs) break;
    if (!ready) delay(1);
  }
  if (ready && init_chip() && apply_settings() && verify_settings() && start_rx(true)) {
    healthy_ms_ = millis();
    return true;
  }
  // Absent or faulty: poll() keeps retrying with back-off (the module may come up later).
  fail(!ready || hal_.stuck() ? kRadioFaultBusyStuck : kRadioFaultReinit, millis());
  return false;
}

bool Sx126xRadio::init_chip() {
  const LoraProfile* p = sx_profile(settings_.rate_code);
  if (p == nullptr) return false;
  hal_.clear_stuck();
  tcxo_fallback_ = false;
  init_errors_ = 0;
  // begin(): version string, SetDIO3AsTCXOCtrl followed by Calibrate(0x7F) (the TCXO
  // case of DS 9.2.1), regulator, LoRa packet type, sync word, preamble, CRC, OCP,
  // DIO2 RF switch, standard IQ, SF, BW, frequency, the TxClampConfig fix (DS 15.2)
  // and the output power (both re-applied by apply_settings()).
  const int16_t st = chip_.begin(radio_channel_freq_mhz(settings_.channel), (float)p->bw_hz / 1000.0f, p->sf,
                                 p->cr, kSxSyncWord, sx_drive_dbm(settings_.power_code), kSxPreambleSym,
                                 hw_.tcxo_v, hw_.regulator_ldo);
  spi_ready_ = true;                      // begin() sets the SPI framing before probing the chip
  preamble_ = kSxPreambleSym;
  if (!check(st)) return false;
  // If the 32 MHz reference does not start within the TCXO delay (XOSC_START_ERR,
  // DS 13.3.6), RadioLib resets the chip, retries with the crystal and still returns
  // success, leaving tcxoVoltage = 0 (it re-arms it from the argument on every begin()).
  tcxo_fallback_ = hw_.tcxo_v > 0.0f && chip_.tcxoVoltage <= 0.0f;
  // OpError now holds only what begin() itself raised (TCXO start, Calibrate(0x7F),
  // CalibrateImage): RadioLib setTCXO() has already cleared the XOSC_START_ERR every
  // TCXO design shows at power-on. Keep it for verify_settings(), then clear it
  // (ClearDeviceErrors clears every bit, DS 13.6.2).
  uint16_t e = 0;
  if (!check(chip_.device_errors(e))) return false;
  init_errors_ = e;
  if (e != 0 && !check(chip_.clearDeviceErrors())) return false;
  // begin() makes DIO2 the RF-switch control; the module may use RXEN/TXEN instead.
  return check(chip_.setDio2AsRfSwitch(hw_.dio2_rf_switch));
}

bool Sx126xRadio::chip_present() {
  // One read of the version string ("SX1261 ..." on SX1261/2, "SX1268 ..."): avoids the
  // ~100 ms retry loop inside RadioLib begin() on every attempt while no chip answers.
  uint8_t v[16] = {0};
  if (!check(chip_.read_regs(RADIOLIB_SX126X_REG_VERSION_STRING, v, sizeof v))) return false;
  return memcmp(v, "SX126", 5) == 0;
}

bool Sx126xRadio::apply_settings() {
  const LoraProfile* p = sx_profile(settings_.rate_code);
  if (p == nullptr) return false;
  const float f_mhz = radio_channel_freq_mhz(settings_.channel);
  if (!check(chip_.standby())) return false;
  if (!check(chip_.setSpreadingFactor(p->sf))) return false;
  if (!check(chip_.setBandwidth((float)p->bw_hz / 1000.0f))) return false;
  if (!check(chip_.setCodingRate(p->cr))) return false;
  // LDRO fixed per profile (symbol >= 16.38 ms, DS 6.1.1) so that the air time and
  // the TX timeout computed by lora_airtime_us() match the modem exactly.
  if (!check(chip_.forceLDRO(lora_ldro_required(p->sf, p->bw_hz)))) return false;
  if (!check(chip_.setSyncWord(kSxSyncWord))) return false;
  if (!check(chip_.setPreambleLength(kSxPreambleSym))) return false;
  preamble_ = kSxPreambleSym;
  if (!check(chip_.setCRC(kCrcLen))) return false;
  if (!check(chip_.explicitHeader())) return false;
  if (!check(chip_.invertIQ(false))) return false;
  // Image calibration for the channel (RadioLib maps 430-440 MHz to CalibrateImage
  // 0x6B 0x6F, DS Table 9-2, other channels to +-4 MHz around them). Requested
  // explicitly: RadioLib skips it for moves < 20 MHz and keeps its cached
  // frequency across a chip reset, after which the image is calibrated for 902-928 MHz.
  if (!check(chip_.setFrequency(f_mhz, true))) return false;
  if (!check(chip_.calibrateImage(f_mhz))) return false;
  // Drive level with the datasheet's nominal PA configuration (optimize = false:
  // SetPaConfig 0x04 0x07 0x00 0x01, the Table 13-21 +22 dBm row, then SetTxParams
  // in dBm, DS 13.4.4). RadioLib's default "optimized" table changes paDutyCycle /
  // hpMax per level from bench tests on other boards; DS 13.1.14 ties paDutyCycle
  // to the matching network. Radiated power per code: VERIFY T-RF-07.
  if (!check(chip_.setOutputPower(sx_drive_dbm(settings_.power_code), false))) return false;
  // Then the current limit: SetPaConfig rewrites OCP and RadioLib restores the
  // previous value (60 mA after begin()), too low for +22 dBm drive (Table 5-2).
  if (!check(chip_.setCurrentLimit(hw_.ocp_ma))) return false;
  // Rx gain plus its retention list, so duty-cycled RX keeps it (DS 9.6).
  return check(chip_.setRxBoostedGainMode(settings_.rx_boost, true));
}

bool Sx126xRadio::verify_settings() {
  // RadioLib writeRegister() never reports a failure: read every register back.
  bool ok = true;
  uint8_t v = 0;
  if (!check(chip_.read_regs(RADIOLIB_SX126X_REG_RX_GAIN, &v, 1))) return false;
  rx_gain_reg_ = v;
  rx_boost_ok_ = v == RADIOLIB_SX126X_RX_GAIN_BOOSTED;
  ok = ok && v == (settings_.rx_boost ? RADIOLIB_SX126X_RX_GAIN_BOOSTED : RADIOLIB_SX126X_RX_GAIN_POWER_SAVING);
  uint8_t ret[3] = {0, 0, 0};
  if (!check(chip_.read_regs(RADIOLIB_SX126X_REG_RX_GAIN_RETENTION_0, ret, 3))) return false;
  ok = ok && memcmp(ret, kRxGainRetention, sizeof ret) == 0;
  if (!check(chip_.read_regs(RADIOLIB_SX126X_REG_OCP_CONFIGURATION, &v, 1))) return false;
  ocp_reg_ = v;
  ocp_ma_rb_ = (float)(v & 0x3F) * 2.5f;                  // 6 bits, 2.5 mA steps (Table 5-2)
  ok = ok && fabsf(ocp_ma_rb_ - hw_.ocp_ma) < 2.5f;
  uint8_t sw[2] = {0, 0};
  if (!check(chip_.read_regs(RADIOLIB_SX126X_REG_LORA_SYNC_WORD_MSB, sw, 2))) return false;
  ok = ok && memcmp(sw, kSyncRegs, sizeof sw) == 0;
  if (!check(chip_.read_regs(RADIOLIB_SX126X_REG_IQ_CONFIG, &v, 1))) return false;
  ok = ok && (v & 0x04) != 0;                             // DS 15.4: standard IQ -> bit 2 = 1
  if (!check(chip_.read_regs(RADIOLIB_SX126X_REG_TX_CLAMP_CONFIG, &v, 1))) return false;
  clamp_reg_ = v;
  ok = ok && (v & 0x1E) == 0x1E;                          // DS 15.2: bits 4..1 = 1111
  uint16_t e = 0;
  if (!check(chip_.device_errors(e))) return false;
  dev_errors_ = (uint16_t)(e | init_errors_);
  // A calibration or oscillator error from begin() or from the image calibration, or
  // the crystal fall-back, keeps config_ok() false until a re-initialisation fixes it.
  ok = ok && (dev_errors_ & kCalibErrMask) == 0 && !tcxo_fallback_;
  if (e != 0 && !check(chip_.clearDeviceErrors())) return false;
  cfg_ok_ = ok;
  return true;   // false only for a bus/chip failure; a mismatch shows in config_ok()
}

bool Sx126xRadio::start_rx(bool allow_duty_cycle) {
  const bool duty = allow_duty_cycle && hibernating_;
  const uint16_t pre = duty ? kSxWakePreambleSym : kSxPreambleSym;
  rx_active_ = false;
  duty_cycling_ = false;
  if (!check(chip_.standby())) return false;
  if (preamble_ != pre) {
    if (!check(chip_.setPreambleLength(pre))) return false;
    preamble_ = pre;
  }
  const LoraProfile* p = sx_profile(settings_.rate_code);
  uint32_t rx_us = 0, sleep_us = 0;
  const bool cycle = duty && p != nullptr && sx_duty_cycle_us(p->sf, p->bw_hz, pre, rx_us, sleep_us);
  s_dio1_ = false;
  // RadioLib subtracts its wake-up transition from sleep_us itself.
  const int16_t st = cycle ? chip_.startReceiveDutyCycle(rx_us, sleep_us, kRxIrqFlags, kRxIrqMask)
                           : chip_.startReceive(RADIOLIB_SX126X_RX_TIMEOUT_INF, kRxIrqFlags, kRxIrqMask, 0);
  if (!check(st)) return false;
  // startReceiveDutyCycle() leaves the RF switch in MODE_IDLE (its standby()) and,
  // unlike startReceive(), never selects MODE_RX: the module's receive path would be
  // off for the whole hibernation. GPIO only; RXEN stays high through the sleep phases.
  if (cycle) mod_.setRfSwitchState(Module::MODE_RX);
  duty_cycling_ = cycle;
  health_ms_ = millis();
  state_ = duty ? State::HIBERNATE : State::RX;
  return true;
}

bool Sx126xRadio::configure(const RadioSettings& s, bool persist) {
  (void)persist;   // no non-volatile configuration memory on the SX126x
  if (!radio_settings_valid(s) || tx_in_flight_) return false;
  settings_ = s;
  if (!operational()) return false;   // stored: applied by the next (re)initialisation
  if (apply_settings() && verify_settings() && start_rx(true)) return cfg_ok_;
  fail(bus_fault(), millis());
  return false;
}

bool Sx126xRadio::set_hibernation(bool on, uint32_t now) {
  if (on == hibernating_) return true;
  if (tx_in_flight_) return false;
  hibernating_ = on;
  if (!operational()) return true;    // takes effect when the radio is back
  if (start_rx(true)) return true;
  fail(bus_fault(), now);
  return false;
}

// =============================================================================
// Health
// =============================================================================
// The status byte RadioLib checks is clocked out during each command, before the
// command executes (DS 8.3.1): a "failure to execute" of the last command of a
// sequence (SetRx, SetRxDutyCycle) and a chip-internal reset (POR defaults, STDBY_RC)
// are invisible to it. Checked where the expected chip mode is known.
bool Sx126xRadio::chip_healthy(uint8_t want_mode, uint8_t& fault) {
  const uint8_t status = chip_.chip_status();
  uint16_t err = 0;
  uint8_t gain = 0, ocp = 0, clamp = 0;
  if (!check(chip_.device_errors(err)) || !check(chip_.read_regs(RADIOLIB_SX126X_REG_RX_GAIN, &gain, 1)) ||
      !check(chip_.read_regs(RADIOLIB_SX126X_REG_OCP_CONFIGURATION, &ocp, 1)) ||
      !check(chip_.read_regs(RADIOLIB_SX126X_REG_TX_CLAMP_CONFIG, &clamp, 1)) || status == 0x00 || status == 0xFF) {
    fault = bus_fault();
    return false;
  }
  dev_errors_ = (uint16_t)(err | init_errors_);
  // Wrong mode (left RX), configuration lost (a chip-internal reset returns to
  // STDBY_RC with reset values - indistinguishable by mode while hibernating) or an
  // oscillator / PLL failure: the configuration is unknown, so reset and
  // re-initialise (contract 14).
  const bool config_lost =
      (ocp & kOcpField) != (ocp_reg_ & kOcpField) || (clamp & kClampField) != (clamp_reg_ & kClampField);
  if ((status & kChipModeMask) != want_mode || config_lost || (err & kOpErrReinitMask) != 0) {
    fault = kRadioFaultReinit;
    return false;
  }
  if ((err & kCalibErrMask) != 0) cfg_ok_ = false;
  if (err != 0 && !check(chip_.clearDeviceErrors())) {   // reported via device_errors(), then cleared
    fault = bus_fault();
    return false;
  }
  const uint8_t want = settings_.rx_boost ? RADIOLIB_SX126X_RX_GAIN_BOOSTED : RADIOLIB_SX126X_RX_GAIN_POWER_SAVING;
  if (gain != want) {   // re-assert once (with retention), then insist
    if (!check(chip_.setRxBoostedGainMode(settings_.rx_boost, true)) ||
        !check(chip_.read_regs(RADIOLIB_SX126X_REG_RX_GAIN, &gain, 1))) {
      fault = bus_fault();
      return false;
    }
    if (gain != want) {
      fault = kRadioFaultReinit;
      return false;
    }
  }
  rx_gain_reg_ = gain;
  rx_boost_ok_ = gain == RADIOLIB_SX126X_RX_GAIN_BOOSTED;
  return true;
}

// Back to the low-power receiver after an RxDone or a transmission. When the chip is
// known to be awake in STDBY_RC (after a TX, or an RxDone in duty-cycled RX, DS
// 13.1.7) and hibernating, its health is checked first: in the sleep phases a
// GetStatus would wake it.
bool Sx126xRadio::relisten(bool chip_in_standby, uint8_t& fault) {
  if (hibernating_ && chip_in_standby && !chip_healthy(kChipModeStbyRc, fault)) return false;
  if (start_rx(true)) return true;
  fault = bus_fault();
  return false;
}

// =============================================================================
// Transmission
// =============================================================================
bool Sx126xRadio::transmit(const uint8_t* data, size_t n, uint32_t now) {
  if (data == nullptr || n == 0 || n > kSxMaxPayload || tx_in_flight_ || !operational()) return false;
  memcpy(tx_buf_, data, n);
  tx_len_ = (uint8_t)n;
  tx_in_flight_ = true;
  tx_req_ms_ = now;
  (void)start_cad_or_tx(now);   // a failure is reported as TX_TIMEOUT followed by FAULT
  return true;
}

// Called in State::RX / HIBERNATE only (transmit() and the deferred retry in poll()).
bool Sx126xRadio::start_cad_or_tx(uint32_t now) {
  tx_waiting_ = false;
  // Deferrals end after kSxLbtMaxWaitMs: then the frame is sent anyway (the same rule
  // as the E22's internal LBT), so neither LoRa traffic nor a DIO1 line stuck high
  // can block transmissions.
  const bool may_defer = now - tx_req_ms_ < kSxLbtMaxWaitMs;
  // An RX interrupt raised since the last poll() (RxDone, header): let poll() service
  // it first. SetStandby would end that reception and stageMode's ClearIrqStatus would
  // discard the uplink without counting it.
  if (may_defer && (s_dio1_ || digitalRead(hw_.pin_dio1) == HIGH)) {
    tx_waiting_ = true;
    cad_retry_ms_ = now;
    return true;
  }
  // Listen before talk.
  if (settings_.lbt && may_defer) {
    rx_active_ = false;
    // startChannelScan() begins with GetPacketType, which waits for BUSY: in the sleep
    // phase of duty-cycled RX BUSY is high (DS 8.3.1) and only an NSS edge wakes the
    // chip. standby() sends that wake-up first (and is DS 14.2 step 1 anyway).
    if (!check(chip_.standby()) || !check(chip_.startChannelScan())) {
      fail(bus_fault(), now);
      return false;
    }
    // Only now is DIO1 routed to the CAD interrupts alone: an RX edge from before the
    // SetDioIrqParams must not be taken for CadDone. CAD lasts >= 4 symbols (>= 4 ms),
    // and take_irq() also reads the DIO1 level, so CadDone cannot be missed.
    s_dio1_ = false;
    duty_cycling_ = false;
    cad_t0_ = now;
    state_ = State::CAD;
    return true;
  }
  return launch_tx(now);
}

void Sx126xRadio::on_cad_irq(uint32_t now) {
  uint16_t irq = 0;
  if (!check(chip_.irq_status(irq))) {
    fail(bus_fault(), now);
    return;
  }
  if ((irq & (RADIOLIB_SX126X_IRQ_CAD_DONE | RADIOLIB_SX126X_IRQ_CAD_DETECTED)) == 0) {
    return;   // spurious edge: keep waiting, the CAD deadline in poll() still applies
  }
  if ((irq & RADIOLIB_SX126X_IRQ_CAD_DETECTED) == 0) {   // CadDone without activity: free
    cad_busy_ = false;
    (void)launch_tx(now);
    return;
  }
  // LoRa activity: keep receiving (it may be an uplink) and try again after one CAD period.
  cad_busy_ = true;
  inc16(lbt_deferrals_);
  tx_waiting_ = true;
  cad_retry_ms_ = now + cad_period_ms();
  if (!start_rx(false)) fail(bus_fault(), now);
}

bool Sx126xRadio::launch_tx(uint32_t now) {
  const LoraProfile* p = sx_profile(settings_.rate_code);
  // DS 14.2 step 1: stage the frame in STDBY_RC. stageMode(TX) does not leave RX by
  // itself: a reception would keep writing the data buffer the frame is written to
  // (RX and TX base addresses are both 0, DS 7.2 / 7.4).
  int16_t st = p != nullptr ? chip_.standby() : RADIOLIB_ERR_UNKNOWN;
  if (st == RADIOLIB_ERR_NONE && preamble_ != kSxPreambleSym) {   // beacon while hibernating
    st = chip_.setPreambleLength(kSxPreambleSym);
    if (st == RADIOLIB_ERR_NONE) preamble_ = kSxPreambleSym;
  }
  // stageMode(TX): SetPacketParams (+ DS 15.4 IQ fix), DIO routing, buffer base,
  // WriteBuffer, ClearIrqStatus and the DS 15.1 modulation-quality fix (0x0889).
  RadioModeConfig_t cfg = {};
  cfg.transmit = TransmitConfig_t{tx_buf_, tx_len_, 0};
  if (st == RADIOLIB_ERR_NONE) st = chip_.stageMode(RADIOLIB_RADIO_MODE_TX, &cfg);
  // DIO1 on Timeout as well as TxDone (RadioLib routes only TxDone).
  if (st == RADIOLIB_ERR_NONE) st = chip_.setDioIrqParams(kTxIrq, kTxIrq);
  // Launched here rather than by RadioLib launchMode(), whose wait for BUSY after
  // SetTx has no bound: SetTx itself waits for BUSY within the SPI timeout.
  chip_.clear_staged_mode();
  uint32_t raw = 0;
  if (st == RADIOLIB_ERR_NONE) {
    const uint32_t air_us = lora_airtime_us(p->sf, p->bw_hz, p->cr, lora_ldro_required(p->sf, p->bw_hz),
                                            kSxPreambleSym, tx_len_, true, false);
    raw = sx_tx_timeout_raw(air_us, kSxTxTimeoutMarginUs);
    mod_.setRfSwitchState(chip_.tx_switch_mode());
    s_dio1_ = false;
    st = chip_.setTx(raw);
  }
  if (!check(st)) {
    fail(bus_fault(), now);
    return false;
  }
  tx_launch_ms_ = now;
  tx_watchdog_ms_ = raw / 64u + 1u + kTxWatchdogMarginMs;   // 64 timeout steps per ms
  rx_active_ = false;
  duty_cycling_ = false;
  state_ = State::TX;
  return true;
}

RadioEvent Sx126xRadio::on_tx_irq(uint32_t now, int32_t& value) {
  uint16_t irq = 0;
  if (!check(chip_.irq_status(irq))) {
    fail(bus_fault(), now);
    return RadioEvent::NONE;
  }
  const bool done = (irq & RADIOLIB_SX126X_IRQ_TX_DONE) != 0;
  const bool timed_out = (irq & RADIOLIB_SX126X_IRQ_TIMEOUT) != 0;
  if (!done && !timed_out) {   // spurious: keep waiting; poll() still applies the watchdog
    if (irq != 0 && !check(chip_.clearIrqStatus(irq))) fail(bus_fault(), now);
    return RadioEvent::NONE;
  }
  const RadioEvent ev = done ? RadioEvent::TX_DONE : RadioEvent::TX_TIMEOUT;
  const int32_t dt = ms_value(now - tx_launch_ms_);
  tx_in_flight_ = false;
  // finishTransmit(): ClearIrqStatus + standby (RF switch idle), then listen again.
  uint8_t fault = kRadioFaultSpi;
  bool ok = check(chip_.finishTransmit());
  if (!ok) {
    fault = bus_fault();
  } else {
    ok = relisten(true, fault);
  }
  if (!ok) {
    push(ev, dt);              // the transmission's end event before the FAULT
    fail(fault, now);
    return RadioEvent::NONE;
  }
  value = dt;
  return ev;
}

uint32_t Sx126xRadio::cad_period_ms() const {
  const LoraProfile* p = sx_profile(settings_.rate_code);
  if (p == nullptr) return 1;
  return kCadSymbols * lora_symbol_us(p->sf, p->bw_hz) / 1000u + 1u;
}

// =============================================================================
// Reception
// =============================================================================
RadioEvent Sx126xRadio::on_rx_irq(uint32_t now, uint32_t t_irq, RxPacket& rx, int32_t& value) {
  // RxDutyCycle: in the SLEEP phases BUSY is high and the chip takes no command until
  // an NSS falling edge wakes it (DS 8.3.1, 13.1.7). A header error does not end the
  // loop, so its IRQ can still be raised when the chip goes back to sleep, and
  // GetIrqStatus would wait for BUSY and trip the guard. Wake the chip first: nothing
  // is received while it sleeps (after RxDone it is in STDBY_RC, during a packet in
  // RX, both with BUSY low).
  const bool cycling = duty_cycling_;
  bool woken = false;
  if (cycling && digitalRead(hw_.pin_busy) == HIGH) {
    if (!check(chip_.standby())) {
      fail(bus_fault(), now);
      return RadioEvent::NONE;
    }
    duty_cycling_ = false;
    woken = true;
  }
  uint16_t irq = 0;
  if (!check(chip_.irq_status(irq))) {
    fail(bus_fault(), now);
    return RadioEvent::NONE;
  }
  uint8_t fault = kRadioFaultSpi;
  if ((irq & RADIOLIB_SX126X_IRQ_RX_DONE) == 0) {
    if ((irq & kRxDio1Irq) != 0) {
      spurious_irqs_ = 0;
    } else if (++spurious_irqs_ >= kMaxSpuriousIrq) {
      fail(kRadioFaultSpi, now);   // DIO1 high, no IRQ raised: line or IRQ read is faulty
      return RadioEvent::NONE;
    }
    if (irq & RADIOLIB_SX126X_IRQ_HEADER_ERR) {
      rx_active_ = false;
      inc16(header_errors_);
    } else if ((irq & RADIOLIB_SX126X_IRQ_HEADER_VALID) && !woken) {
      // Header decoded: a packet is arriving. Clear only this flag; RxDone follows.
      rx_active_ = true;
      rx_hdr_ms_ = now;
      if (!check(chip_.clearIrqStatus(RADIOLIB_SX126X_IRQ_HEADER_VALID))) fail(bus_fault(), now);
      return RadioEvent::NONE;
    }
    // Header error, stale flag or spurious edge: clear exactly what was seen (a newer
    // IRQ stays raised), then restart the duty cycle, which the error or the wake-up
    // may have ended.
    if (irq != 0 && !check(chip_.clearIrqStatus(irq))) {
      fail(bus_fault(), now);
      return RadioEvent::NONE;
    }
    if (hibernating_ && !relisten(woken, fault)) fail(fault, now);
    return RadioEvent::NONE;
  }
  spurious_irqs_ = 0;
  rx_active_ = false;
  const bool in_standby = cycling;          // RxDone ends the duty-cycle loop in STDBY_RC (DS 13.1.7)
  const bool header_bad = (irq & RADIOLIB_SX126X_IRQ_HEADER_ERR) && !(irq & RADIOLIB_SX126X_IRQ_HEADER_VALID);
  // Contract 20 ("RS rescue", F-40): a payload whose LoRa CRC failed is still delivered,
  // flagged crc_ok = false - the chip raises CrcErr together with RxDone and leaves the
  // payload in the buffer (DS 13.3, Table 13-29; RadioLib readData() reads it before
  // returning RADIOLIB_ERR_CRC_MISMATCH). The uplink CLTU decoder then decides alone:
  // BCH per codeblock, the FECF and SDLS (flight.cpp process_tc), so nothing bypasses
  // them. Only a packet without a valid header (no length to read) is dropped.
  const bool crc_bad = (irq & RADIOLIB_SX126X_IRQ_CRC_ERR) != 0;
  bool deliver = !header_bad;
  // THIS packet's status right after RxDone, before any other command (contract 14):
  // RssiPkt / SnrPkt (DS 13.5.3), the frequency-error estimate, then the buffer status
  // and the payload from its start pointer (continuous RX, DS 7.2). Each read is
  // status-checked; a failed one discards the packet and resets the radio.
  uint8_t ps[3] = {0, 0, 0}, fe[3] = {0, 0, 0};
  uint8_t len = 0, offset = 0;
  if (deliver) {
    if (!check(chip_.packet_status(ps)) || !check(chip_.read_regs(kRegFreqError, fe, 3)) ||
        !check(chip_.rx_buffer_status(len, offset)) ||
        (len > 0 && !check(chip_.read_payload(rx.data, len, offset)))) {
      fail(bus_fault(), now);
      return RadioEvent::NONE;
    }
    deliver = len > 0;
  }
  // Clear exactly the IRQs of this packet: in continuous RX the next packet's
  // HeaderValid may already be raised and must not be lost.
  if (!check(chip_.clearIrqStatus(irq))) {
    fail(bus_fault(), now);
    return RadioEvent::NONE;
  }
  if (crc_bad || !deliver) inc16(crc_errors_);
  if (!deliver) {
    if (hibernating_ && !relisten(in_standby, fault)) fail(fault, now);
    return RadioEvent::NONE;
  }
  // RssiPkt: average RSSI over the packet, -RssiPkt / 2 dBm - the quantity the station
  // reports, not SignalRssiPkt (byte 4, after despreading), which RadioLib getRSSI(true)
  // returns. SnrPkt: two's complement, dB x 4. Both are in range by construction.
  const int32_t snr_q = ps[1] < 128u ? (int32_t)ps[1] : (int32_t)ps[1] - 256;
  const uint32_t efe = (((uint32_t)fe[0] << 16) | ((uint32_t)fe[1] << 8) | fe[2]) & 0x0FFFFFu;
  const int32_t efe_s = (efe & 0x80000u) ? (int32_t)efe - 0x100000 : (int32_t)efe;
  const LoraProfile* p = sx_profile(settings_.rate_code);
  const float bw_hz = p != nullptr ? (float)p->bw_hz : 0.0f;
  const float ferr = kFreqErrScale * (float)efe_s * (bw_hz / 1000.0f);
  rx.len = len;
  rx.rssi_dbm = (int16_t)lroundf(-0.5f * (float)ps[0]);   // 0 .. -128 dBm
  rx.snr_cdb = (int16_t)(25 * snr_q);                       // 0.01 dB
  rx.freq_err_hz = (bw_hz > 0.0f && fabsf(ferr) <= bw_hz) ? (int32_t)lroundf(ferr) : kRadioNaI32;
  rx.crc_ok = !crc_bad;                     // counted in crc_errors() above when false
  rx.t_ms = t_irq;
  if (!crc_bad) rx_payload_bytes_ += (uint32_t)len;   // rx_bytes(): payload octets of good packets
  // In duty-cycled RX the chip is now in standby: listen again (a failure is reported
  // by the queued FAULT after this packet).
  if (hibernating_ && !relisten(in_standby, fault)) fail(fault, now);
  value = 0;
  return RadioEvent::PACKET;
}

bool Sx126xRadio::request_noise(uint32_t now) {
  if (state_ != State::RX || tx_in_flight_) return false;
  uint8_t raw = 0;
  if (!check(chip_.rssi_inst(raw))) {   // GetRssiInst (DS 13.5.4), valid in RX
    fail(bus_fault(), now);
    return false;
  }
  const float r = -0.5f * (float)raw;
  push(RadioEvent::NOISE, raw != 0 ? (int32_t)lroundf(r) : kRadioNaI32);
  return true;
}

bool Sx126xRadio::busy(uint32_t now) const {
  if (tx_in_flight_ || !operational()) return true;
  return receiving(now);
}

// =============================================================================
// Event queue, poll(), fault handling and recovery
// =============================================================================
void Sx126xRadio::push(RadioEvent ev, int32_t value) {
  if (q_count_ >= kQueueLen) return;    // cannot happen: at most 3 events per step
  queue_[(uint8_t)((q_head_ + q_count_) % kQueueLen)] = QueuedEvent{ev, value};
  ++q_count_;
}

bool Sx126xRadio::pop(RadioEvent& ev, int32_t& value) {
  if (q_count_ == 0) return false;
  ev = queue_[q_head_].ev;
  value = queue_[q_head_].value;
  q_head_ = (uint8_t)((q_head_ + 1u) % kQueueLen);
  --q_count_;
  return true;
}

RadioEvent Sx126xRadio::poll(uint32_t now, RxPacket& rx, int32_t& value) {
  RadioEvent ev = RadioEvent::NONE;
  if (pop(ev, value)) return ev;
  uint32_t t_irq = now;
  uint8_t fault = kRadioFaultSpi;
  switch (state_) {
    case State::OFF:
      break;
    case State::BACKOFF:
    case State::RESET_LOW:
    case State::RESET_WAIT:
      step_recovery(now);
      break;
    case State::RX:
    case State::HIBERNATE: {
      const bool irq = take_irq(now, t_irq);
      if (irq) ev = on_rx_irq(now, t_irq, rx, value);
      // A deferred frame is retried when no IRQ needed service in this pass; past the
      // deferral limit it goes out in any case (after the IRQ), so continuous DIO1
      // activity cannot hold a transmission open.
      const bool retry_due = operational() && tx_waiting_ && (int32_t)(now - cad_retry_ms_) >= 0 && !receiving(now);
      if (retry_due && (!irq || now - tx_req_ms_ >= kSxLbtMaxWaitMs)) {
        (void)start_cad_or_tx(now);
      } else if (!irq && state_ == State::RX && !tx_waiting_ && !rx_active_ && now - health_ms_ >= kHealthPeriodMs) {
        health_ms_ = now;
        if (!chip_healthy(kChipModeRx, fault)) fail(fault, now);
      } else if (!irq && now - healthy_ms_ > kHealthyMs) {
        backoff_ms_ = kBackoffMinMs;
      }
      break;
    }
    // The CAD and TX deadlines are checked after any interrupt handling, not instead
    // of it: a DIO1 line stuck high must not disable them.
    case State::CAD:
      if (take_irq(now, t_irq)) on_cad_irq(now);
      if (state_ == State::CAD && now - cad_t0_ > 2u * cad_period_ms() + kCadMarginMs) fail(kRadioFaultNoIrq, now);
      break;
    case State::TX:
      if (take_irq(now, t_irq)) ev = on_tx_irq(now, value);
      if (state_ == State::TX && now - tx_launch_ms_ > tx_watchdog_ms_) fail(kRadioFaultNoIrq, now);
      break;
  }
  if (ev != RadioEvent::NONE) return ev;
  return pop(ev, value) ? ev : RadioEvent::NONE;
}

void Sx126xRadio::fail(uint8_t code, uint32_t now) {
  // End a transmission in progress for the caller (exactly one end event per transmit()).
  if (tx_in_flight_) {
    push(RadioEvent::TX_TIMEOUT, ms_value(now - tx_req_ms_));
    tx_in_flight_ = false;
  }
  push(RadioEvent::FAULT, code);
  tx_waiting_ = false;
  rx_active_ = false;
  duty_cycling_ = false;
  spurious_irqs_ = 0;
  cfg_ok_ = false;
  rx_boost_ok_ = false;
  hal_.clear_stuck();
  mod_.setRfSwitchState(Module::MODE_IDLE);   // GPIO only: PA path off whatever the chip does
  if (backoff_ms_ < kBackoffMinMs) backoff_ms_ = kBackoffMinMs;
  step_ms_ = now + backoff_ms_;
  backoff_ms_ = backoff_ms_ >= kBackoffMaxMs / 2u ? kBackoffMaxMs : backoff_ms_ * 2u;
  state_ = State::BACKOFF;
}

void Sx126xRadio::step_recovery(uint32_t now) {
  switch (state_) {
    case State::BACKOFF:
      if ((int32_t)(now - step_ms_) < 0) return;
      digitalWrite(hw_.pin_nrst, LOW);
      step_ms_ = now;
      state_ = State::RESET_LOW;
      return;
    case State::RESET_LOW:
      if (now - step_ms_ < kResetLowMs) return;
      digitalWrite(hw_.pin_nrst, HIGH);
      step_ms_ = now;
      state_ = State::RESET_WAIT;
      return;
    case State::RESET_WAIT:
      if (digitalRead(hw_.pin_busy) != LOW) {
        if (now - step_ms_ > kResetBusyMs) fail(kRadioFaultBusyStuck, now);
        return;
      }
      if (spi_ready_ && !chip_present()) {
        fail(hal_.stuck() ? kRadioFaultBusyStuck : kRadioFaultReinit, now);
        return;
      }
      // STDBY_RC after reset: re-initialise and re-apply the current settings.
      if (init_chip() && apply_settings() && verify_settings() && start_rx(true)) {
        inc16(recoveries_);
        healthy_ms_ = now;
        push(RadioEvent::FAULT, kRadioFaultCleared);
      } else {
        fail(hal_.stuck() ? kRadioFaultBusyStuck : kRadioFaultReinit, now);
      }
      return;
    default:
      return;
  }
}

// ---- Flight instance (wiring from config.h) -----------------------------------------
namespace {
Sx126xHw flight_hw() {
  Sx126xHw hw;
  hw.pin_nss = PIN_SX_NSS;
  hw.pin_busy = PIN_SX_BUSY;
  hw.pin_dio1 = PIN_SX_DIO1;
  hw.pin_nrst = PIN_SX_NRST;
  hw.pin_rxen = PIN_SX_RXEN;
  hw.pin_txen = PIN_SX_TXEN;
  hw.tcxo_v = kSxTcxoV;
  hw.dio2_rf_switch = kSxDio2RfSwitch;
  hw.pa_gain_db = kSxPaGainDb;
  hw.ocp_ma = kSxOcpMa;
  return hw;
}
Sx126xRadio g_flight_radio(SPI, flight_hw());
}  // namespace
Radio& flight_radio() { return g_flight_radio; }
Sx126xRadio& flight_sx126x() { return g_flight_radio; }

}  // namespace vgq

#endif  // VGQ_RADIO == VGQ_RADIO_SX126X
