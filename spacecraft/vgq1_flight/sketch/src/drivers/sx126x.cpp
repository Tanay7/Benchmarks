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
// =============================================================================
#include "../config.h"   // VGQ_RADIO, VGQ_SX126X_CHIP, PIN_SX_* and kSx* (flight_radio() only)
#include "sx126x.h"

#if VGQ_HAVE_RADIOLIB

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
const uint32_t kTxWatchdogMarginMs = 500;   // beyond the chip's own TX timeout
const uint32_t kCadSymbols = 4;         // RadioLib default SetCadParams (CAD_ON_4_SYMB)
const uint32_t kCadMarginMs = 50;
const uint8_t kCrcLen = 2;              // LoRa payload CRC on
// RadioLib generic IRQ flags: DIO1 on RxDone, on a valid header (reception in
// progress, used by busy()) and on a header error; CRC errors arrive with RxDone.
const RadioLibIrqFlags_t kRxIrqFlags = RADIOLIB_IRQ_RX_DEFAULT_FLAGS;
const RadioLibIrqFlags_t kRxIrqMask = (1UL << RADIOLIB_IRQ_RX_DONE) | (1UL << RADIOLIB_IRQ_HEADER_VALID) |
                                      (1UL << RADIOLIB_IRQ_HEADER_ERR);
const uint16_t kTxIrq = RADIOLIB_SX126X_IRQ_TX_DONE | RADIOLIB_SX126X_IRQ_TIMEOUT;
// Read-back references.
const uint8_t kSyncRegs[2] = {0x14, 0x24};          // 0x12 + RadioLib control bits 0x44 (0x1424)
const uint8_t kRxGainRetention[3] = {0x01, 0x08, 0xAC};   // DS 9.6: 0x029F..0x02A1
const uint16_t kCalibErrMask = 0x007F;              // GetDeviceErrors bits 0..6 (calibration, XOSC, PLL)
const float kRssiMinDbm = -140.0f;                  // plausibility window for RSSI reports

uint32_t pin_or_nc(int pin) { return pin < 0 ? (uint32_t)RADIOLIB_NC : (uint32_t)pin; }

int32_t ms_value(uint32_t dt) { return dt > (uint32_t)INT32_MAX ? INT32_MAX : (int32_t)dt; }

}  // namespace

// =============================================================================
// BUSY-guarded HAL
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

// =============================================================================
// Bring-up and configuration
// =============================================================================
bool Sx126xRadio::begin() {
  if (hw_.pin_nss < 0 || hw_.pin_busy < 0 || hw_.pin_dio1 < 0 || hw_.pin_nrst < 0) return false;
  pinMode(hw_.pin_nrst, OUTPUT);
  digitalWrite(hw_.pin_nrst, HIGH);
  spi_.begin();
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
  // begin(): version string, SetDIO3AsTCXOCtrl followed by Calibrate(0x7F) (the TCXO
  // case of DS 9.2.1), regulator, LoRa packet type, sync word, preamble, CRC, OCP,
  // DIO2 RF switch, standard IQ, SF, BW, frequency (+ image calibration), the
  // TxClampConfig fix (DS 15.2) and the output power.
  const int16_t st = chip_.begin(radio_channel_freq_mhz(settings_.channel), (float)p->bw_hz / 1000.0f, p->sf,
                                 p->cr, kSxSyncWord, sx_drive_dbm(settings_.power_code), kSxPreambleSym,
                                 hw_.tcxo_v, hw_.regulator_ldo);
  spi_ready_ = true;                      // begin() sets the SPI framing before probing the chip
  preamble_ = kSxPreambleSym;
  if (!check(st)) return false;
  // Errors latched by the power-on calibration (it fails with a TCXO, DS 9.2.1) are
  // stale now that begin() has re-calibrated; clear them so verification sees only new ones.
  if (!check(chip_.clearDeviceErrors())) return false;
  // begin() makes DIO2 the RF-switch control; the module may use RXEN/TXEN instead.
  return check(chip_.setDio2AsRfSwitch(hw_.dio2_rf_switch));
}

bool Sx126xRadio::chip_present() {
  // One read of the version string ("SX1261 ..." on SX1261/2, "SX1268 ..."): avoids the
  // ~100 ms retry loop inside RadioLib begin() on every attempt while no chip answers.
  uint8_t v[16] = {0};
  if (!check(chip_.readRegister(RADIOLIB_SX126X_REG_VERSION_STRING, v, sizeof v))) return false;
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
  // Power first, then the current limit: SetPaConfig rewrites OCP and RadioLib
  // restores the previous value (60 mA after begin()), too low for +22 dBm drive.
  if (!check(chip_.setOutputPower(sx_drive_dbm(settings_.power_code)))) return false;
  if (!check(chip_.setCurrentLimit(hw_.ocp_ma))) return false;
  // Rx gain plus its retention list, so duty-cycled RX keeps it (DS 9.6).
  return check(chip_.setRxBoostedGainMode(settings_.rx_boost, true));
}

bool Sx126xRadio::verify_settings() {
  bool ok = true;
  uint8_t v = 0;
  if (!check(chip_.readRegister(RADIOLIB_SX126X_REG_RX_GAIN, &v, 1))) return false;
  rx_gain_reg_ = v;
  rx_boost_ok_ = v == RADIOLIB_SX126X_RX_GAIN_BOOSTED;
  ok = ok && v == (settings_.rx_boost ? RADIOLIB_SX126X_RX_GAIN_BOOSTED : RADIOLIB_SX126X_RX_GAIN_POWER_SAVING);
  uint8_t ret[3] = {0, 0, 0};
  if (!check(chip_.readRegister(RADIOLIB_SX126X_REG_RX_GAIN_RETENTION_0, ret, 3))) return false;
  ok = ok && memcmp(ret, kRxGainRetention, sizeof ret) == 0;
  if (!check(chip_.readRegister(RADIOLIB_SX126X_REG_OCP_CONFIGURATION, &v, 1))) return false;
  ocp_ma_rb_ = (float)(v & 0x3F) * 2.5f;                  // 6 bits, 2.5 mA steps (Table 5-2)
  ok = ok && fabsf(ocp_ma_rb_ - hw_.ocp_ma) < 2.5f;
  uint8_t sw[2] = {0, 0};
  if (!check(chip_.readRegister(RADIOLIB_SX126X_REG_LORA_SYNC_WORD_MSB, sw, 2))) return false;
  ok = ok && memcmp(sw, kSyncRegs, sizeof sw) == 0;
  if (!check(chip_.readRegister(RADIOLIB_SX126X_REG_IQ_CONFIG, &v, 1))) return false;
  ok = ok && (v & 0x04) != 0;                             // DS 15.4: standard IQ -> bit 2 = 1
  if (!check(chip_.readRegister(RADIOLIB_SX126X_REG_TX_CLAMP_CONFIG, &v, 1))) return false;
  ok = ok && (v & 0x1E) == 0x1E;                          // DS 15.2: bits 4..1 = 1111
  dev_errors_ = chip_.getDeviceErrors();
  if (hal_.stuck()) return false;
  ok = ok && (dev_errors_ & kCalibErrMask) == 0;
  if (dev_errors_ != 0 && !check(chip_.clearDeviceErrors())) return false;
  cfg_ok_ = ok;
  return true;   // false only for a bus/chip failure; a mismatch shows in config_ok()
}

bool Sx126xRadio::start_rx(bool allow_duty_cycle) {
  const bool duty = allow_duty_cycle && hibernating_;
  const uint16_t pre = duty ? kSxWakePreambleSym : kSxPreambleSym;
  rx_active_ = false;
  if (!check(chip_.standby())) return false;
  if (preamble_ != pre) {
    if (!check(chip_.setPreambleLength(pre))) return false;
    preamble_ = pre;
  }
  s_dio1_ = false;
  const int16_t st = duty ? chip_.startReceiveDutyCycleAuto(kSxWakePreambleSym, 0, kRxIrqFlags, kRxIrqMask)
                          : chip_.startReceive(RADIOLIB_SX126X_RX_TIMEOUT_INF, kRxIrqFlags, kRxIrqMask, 0);
  if (!check(st)) return false;
  state_ = duty ? State::HIBERNATE : State::RX;
  return true;
}

bool Sx126xRadio::configure(const RadioSettings& s, bool persist) {
  (void)persist;   // no non-volatile configuration memory on the SX126x
  if (!radio_settings_valid(s) || tx_in_flight_) return false;
  settings_ = s;
  if (!operational()) return false;   // stored: applied by the next (re)initialisation
  if (apply_settings() && verify_settings() && start_rx(true)) return cfg_ok_;
  fail(hal_.stuck() ? kRadioFaultBusyStuck : kRadioFaultSpi, millis());
  return false;
}

bool Sx126xRadio::set_hibernation(bool on, uint32_t now) {
  if (on == hibernating_) return true;
  if (tx_in_flight_) return false;
  hibernating_ = on;
  if (!operational()) return true;    // takes effect when the radio is back
  if (start_rx(true)) return true;
  fail(hal_.stuck() ? kRadioFaultBusyStuck : kRadioFaultSpi, now);
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

bool Sx126xRadio::start_cad_or_tx(uint32_t now) {
  tx_waiting_ = false;
  // Listen before talk: at most kSxLbtMaxWaitMs of deferrals, then send anyway
  // (the same rule as the E22's internal LBT).
  if (settings_.lbt && now - tx_req_ms_ < kSxLbtMaxWaitMs) {
    rx_active_ = false;
    s_dio1_ = false;
    if (!check(chip_.startChannelScan())) {
      fail(hal_.stuck() ? kRadioFaultBusyStuck : kRadioFaultSpi, now);
      return false;
    }
    cad_t0_ = now;
    state_ = State::CAD;
    return true;
  }
  return launch_tx(now);
}

void Sx126xRadio::on_cad_irq(uint32_t now) {
  const int16_t r = chip_.getChannelScanResult();
  if (hal_.stuck()) {
    fail(kRadioFaultBusyStuck, now);
    return;
  }
  if (r == RADIOLIB_CHANNEL_FREE) {
    cad_busy_ = false;
    (void)launch_tx(now);
    return;
  }
  if (r != RADIOLIB_LORA_DETECTED) {
    fail(kRadioFaultSpi, now);
    return;
  }
  // LoRa activity: keep receiving (it may be an uplink) and try again after one CAD period.
  cad_busy_ = true;
  inc16(lbt_deferrals_);
  tx_waiting_ = true;
  cad_retry_ms_ = now + cad_period_ms();
  if (!start_rx(false)) fail(hal_.stuck() ? kRadioFaultBusyStuck : kRadioFaultSpi, now);
}

bool Sx126xRadio::launch_tx(uint32_t now) {
  const LoraProfile* p = sx_profile(settings_.rate_code);
  int16_t st = p != nullptr ? RADIOLIB_ERR_NONE : RADIOLIB_ERR_UNKNOWN;
  if (st == RADIOLIB_ERR_NONE && preamble_ != kSxPreambleSym) {   // beacon while hibernating
    st = chip_.standby();
    if (st == RADIOLIB_ERR_NONE) st = chip_.setPreambleLength(kSxPreambleSym);
    if (st == RADIOLIB_ERR_NONE) preamble_ = kSxPreambleSym;
  }
  // stageMode(TX): standby, SetPacketParams (+ DS 15.4 IQ fix), DIO routing, buffer
  // base, WriteBuffer, ClearIrqStatus and the DS 15.1 modulation-quality fix (0x0889).
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
    fail(hal_.stuck() ? kRadioFaultBusyStuck : kRadioFaultSpi, now);
    return false;
  }
  tx_launch_ms_ = now;
  tx_watchdog_ms_ = raw / 64u + 1u + kTxWatchdogMarginMs;   // 64 timeout steps per ms
  rx_active_ = false;
  state_ = State::TX;
  return true;
}

RadioEvent Sx126xRadio::on_tx_irq(uint32_t now, int32_t& value) {
  const uint32_t irq = chip_.getIrqFlags();
  if (hal_.stuck()) {
    fail(kRadioFaultBusyStuck, now);
    return RadioEvent::NONE;
  }
  const bool done = (irq & RADIOLIB_SX126X_IRQ_TX_DONE) != 0;
  const bool timed_out = (irq & RADIOLIB_SX126X_IRQ_TIMEOUT) != 0;
  if (!done && !timed_out) {   // spurious: clear and keep waiting (watchdog still armed)
    if (!check(chip_.clearIrqStatus())) fail(kRadioFaultSpi, now);
    return RadioEvent::NONE;
  }
  const RadioEvent ev = done ? RadioEvent::TX_DONE : RadioEvent::TX_TIMEOUT;
  const int32_t dt = ms_value(now - tx_launch_ms_);
  tx_in_flight_ = false;
  // finishTransmit(): ClearIrqStatus + standby (RF switch idle), then listen again.
  if (!check(chip_.finishTransmit()) || !start_rx(true)) {
    push(ev, dt);
    fail(hal_.stuck() ? kRadioFaultBusyStuck : kRadioFaultSpi, now);
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
  const uint32_t irq = chip_.getIrqFlags();
  if (hal_.stuck()) {
    fail(kRadioFaultBusyStuck, now);
    return RadioEvent::NONE;
  }
  if ((irq & RADIOLIB_SX126X_IRQ_RX_DONE) == 0) {
    if (irq & RADIOLIB_SX126X_IRQ_HEADER_ERR) {
      rx_active_ = false;
      inc16(header_errors_);
    } else if (irq & RADIOLIB_SX126X_IRQ_HEADER_VALID) {
      // Header decoded: a packet is arriving. Clear only this flag; RxDone follows.
      rx_active_ = true;
      rx_hdr_ms_ = now;
      if (!check(chip_.clearIrqStatus(RADIOLIB_SX126X_IRQ_HEADER_VALID))) fail(kRadioFaultSpi, now);
      return RadioEvent::NONE;
    }
    // Header error or spurious: clear; the duty cycle stops on any IRQ, so restart it.
    if (!check(chip_.clearIrqStatus()) || (hibernating_ && !start_rx(true))) {
      fail(hal_.stuck() ? kRadioFaultBusyStuck : kRadioFaultSpi, now);
    }
    return RadioEvent::NONE;
  }
  rx_active_ = false;
  const bool header_bad = (irq & RADIOLIB_SX126X_IRQ_HEADER_ERR) && !(irq & RADIOLIB_SX126X_IRQ_HEADER_VALID);
  bool deliver = !(irq & RADIOLIB_SX126X_IRQ_CRC_ERR) && !header_bad;
  // Packet status of THIS packet, read right after RxDone and before the payload
  // (DS 13.5.3): RSSI, SNR and the frequency-error estimate.
  float rssi = 0.0f, snr = 0.0f, ferr = 0.0f;
  size_t len = 0;
  if (deliver) {
    rssi = chip_.getRSSI(true);
    snr = chip_.getSNR();
    ferr = chip_.getFrequencyError();
    len = chip_.getPacketLength(true);
    deliver = len > 0 && len <= kSxMaxPayload;
  }
  if (!deliver) {
    inc16(crc_errors_);
    if (!check(chip_.clearIrqStatus()) || (hibernating_ && !start_rx(true))) {
      fail(hal_.stuck() ? kRadioFaultBusyStuck : kRadioFaultSpi, now);
    }
    return RadioEvent::NONE;
  }
  // readData() starts with a status check, which also catches a command error in
  // the status reads above; it clears the IRQ status.
  const int16_t st = chip_.readData(rx.data, len);
  if (st == RADIOLIB_ERR_CRC_MISMATCH) {
    inc16(crc_errors_);
    if (hibernating_ && !start_rx(true)) fail(hal_.stuck() ? kRadioFaultBusyStuck : kRadioFaultSpi, now);
    return RadioEvent::NONE;
  }
  if (!check(st)) {
    fail(hal_.stuck() ? kRadioFaultBusyStuck : kRadioFaultSpi, now);
    return RadioEvent::NONE;
  }
  const LoraProfile* p = sx_profile(settings_.rate_code);
  const float bw_hz = p != nullptr ? (float)p->bw_hz : 0.0f;
  rx.len = (uint16_t)len;
  rx.rssi_dbm = (rssi <= 0.0f && rssi >= kRssiMinDbm) ? (int16_t)lroundf(rssi) : kRadioNaI16;
  rx.snr_cdb = (snr >= -32.0f && snr <= 32.0f) ? (int16_t)lroundf(snr * 100.0f) : kRadioNaI16;
  rx.freq_err_hz = (!isnan(ferr) && fabsf(ferr) <= bw_hz) ? (int32_t)lroundf(ferr) : kRadioNaI32;
  rx.t_ms = t_irq;
  rx_payload_bytes_ += (uint32_t)len;
  // After RxDone in duty-cycled RX the chip is in standby: listen again (a failure is
  // reported by the queued FAULT after this packet).
  if (hibernating_ && !start_rx(true)) fail(hal_.stuck() ? kRadioFaultBusyStuck : kRadioFaultSpi, now);
  value = 0;
  return RadioEvent::PACKET;
}

bool Sx126xRadio::request_noise(uint32_t now) {
  if (state_ != State::RX || tx_in_flight_) return false;
  const float r = chip_.getRSSI(false);   // GetRssiInst (DS 13.5.4), valid in RX
  if (hal_.stuck()) {
    fail(kRadioFaultBusyStuck, now);
    return false;
  }
  push(RadioEvent::NOISE, (r < 0.0f && r >= kRssiMinDbm) ? (int32_t)lroundf(r) : kRadioNaI32);
  return true;
}

bool Sx126xRadio::busy(uint32_t now) const {
  if (tx_in_flight_ || !operational()) return true;
  // A packet is arriving (header valid, no RxDone yet), bounded by the longest packet.
  return rx_active_ && now - rx_hdr_ms_ < airtime_ms(kSxMaxPayload);
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
  switch (state_) {
    case State::OFF:
      break;
    case State::BACKOFF:
    case State::RESET_LOW:
    case State::RESET_WAIT:
      step_recovery(now);
      break;
    case State::RX:
    case State::HIBERNATE:
      if (take_irq(now, t_irq)) {
        ev = on_rx_irq(now, t_irq, rx, value);
      } else if (tx_waiting_ && state_ == State::RX && (int32_t)(now - cad_retry_ms_) >= 0) {
        (void)start_cad_or_tx(now);
      } else if (now - healthy_ms_ > kHealthyMs) {
        backoff_ms_ = kBackoffMinMs;
      }
      break;
    case State::CAD:
      if (take_irq(now, t_irq)) {
        on_cad_irq(now);
      } else if (now - cad_t0_ > 2u * cad_period_ms() + kCadMarginMs) {
        fail(kRadioFaultNoIrq, now);
      }
      break;
    case State::TX:
      if (take_irq(now, t_irq)) {
        ev = on_tx_irq(now, value);
      } else if (now - tx_launch_ms_ > tx_watchdog_ms_) {
        fail(kRadioFaultNoIrq, now);
      }
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

// ---- Flight instance (SX126x builds; wiring from config.h) -------------------------
#if VGQ_RADIO == VGQ_RADIO_SX126X
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
#endif

}  // namespace vgq

#endif  // VGQ_HAVE_RADIOLIB
