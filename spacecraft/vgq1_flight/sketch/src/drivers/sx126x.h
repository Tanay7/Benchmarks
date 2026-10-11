// =============================================================================
//  Semtech SX1268 / SX1262 LoRa transceiver on SPI through RadioLib 7.8.1: the
//  long-range radio option (contract 2.2, 14). Reference module: EBYTE
//  E22-400M33S (SX1268 + external PA/LNA, SPI). The UART E22 modules hide the
//  LoRa layer; this option exposes it: SF12 at 31.25-125 kHz, per-packet RSSI,
//  SNR and frequency error, channel activity detection and Rx boosted gain.
//
//  Robustness rules implemented here (Semtech DS SX1261/2 rev 2.2):
//    * BUSY: every SPI command waits for BUSY low with a bound (Module SPI
//      timeout), and a BUSY guard in the HAL also bounds the waits RadioLib 7.8.1
//      performs without one (calibration in begin(), BUSY after SetTx). A stuck
//      BUSY, a command error or an SPI failure raises a FAULT; the driver then
//      resets the chip through NRST, re-initialises it, re-applies the settings
//      and reports kRadioFaultCleared - in non-blocking steps, with back-off.
//    * TCXO (9.2.1): RadioLib begin() issues SetDIO3AsTCXOCtrl and then a full
//      Calibrate(0x7F); image calibration is requested explicitly for the
//      operating channel after every (re)initialisation and channel change,
//      because RadioLib skips it unless the frequency moves by >= 20 MHz.
//    * Rx boosted gain (9.6): register 0x08AC = 0x96 plus the retention list
//      0x029F..0x02A1 = 01 08 AC (RadioLib setRxBoostedGainMode(true, true)),
//      re-asserted after every reset and read back.
//    * Known limitations (15): 15.1 (0x0889 bit 2 before every TX) and 15.4
//      (0x0736 bit 2 with every SetPacketParams) are applied by RadioLib in the
//      calls used here; 15.2 (TxClampConfig 0x08D8 bits 4..1) by begin() after
//      every reset and verified; 15.3 concerns implicit-header RX with timeout,
//      which is never used (explicit header, continuous / duty-cycled RX).
//    * OCP: set explicitly after the PA configuration (Table 5-2) and read back.
//    * TX: SetTx with a timeout of 1.5 x the computed air time + 50 ms, DIO1 on
//      TxDone and Timeout, plus a software watchdog; CAD before TX when LBT is on.
//    * RX: continuous; per packet the status (RSSI, SNR, frequency error) is read
//      right after RxDone, before the payload; CRC / header errors are counted
//      and the payload discarded.
//
//  Constants marked VERIFY depend on the module and its wiring; they are set in
//  config.h and checked during bring-up (docs/08).
// =============================================================================
#pragma once
#include <Arduino.h>
#include <SPI.h>

#include "radio.h"

#if __has_include(<RadioLib.h>)
#include <RadioLib.h>
#define VGQ_HAVE_RADIOLIB 1
#else
#define VGQ_HAVE_RADIOLIB 0
#endif

// The SX126x option needs RadioLib 7.8.1: sketch.yaml "- RadioLib (7.8.1)" or the Library Manager.
#if (VGQ_RADIO == VGQ_RADIO_SX126X) && !VGQ_HAVE_RADIOLIB
#error "VGQ_RADIO selects the SX126x radio: install the RadioLib library 7.8.1"
#endif

// Transceiver chip on the module: 1268 = SX1268 (E22-400M33S), 1262 = SX1262.
#ifndef VGQ_SX126X_CHIP
#define VGQ_SX126X_CHIP 1268
#endif

#if VGQ_HAVE_RADIOLIB

#if RADIOLIB_VERSION_MAJOR != 7 || RADIOLIB_VERSION_MINOR != 8
#error "drivers/sx126x.cpp is verified against RadioLib 7.8.x only (it relies on SX126x internals)."
#endif

namespace vgq {

// Wiring and RF front end of the SX126x module. Defaults: UNO Q pins of contract 1.1.
struct Sx126xHw {
  int pin_nss = 10;           // D10 (PB9)  SPI chip select
  int pin_busy = 4;           // D4  (PA12) BUSY
  int pin_dio1 = 5;           // D5  (PA11) DIO1 interrupt
  int pin_nrst = 6;           // D6  (PB1)  NRESET
  int pin_rxen = 8;           // D8  (PB4)  module RXEN, -1 = not wired
  int pin_txen = 9;           // D9  (PB8)  module TXEN, -1 = not wired
  float tcxo_v = 1.8f;        // DIO3 TCXO supply, V (0 = crystal). VERIFY: module datasheet.
                              // RadioLib falls back to the crystal if the TCXO does not start.
  bool dio2_rf_switch = false;   // DIO2 drives the RF switch. VERIFY: module wiring.
  int8_t pa_gain_db = kSxPaGainDbDefault;   // drive -> antenna gain. VERIFY: T-RF-07.
  float ocp_ma = 140.0f;      // PA current limit (SX1262 default after SetPaConfig, DS Table 5-2).
                              // VERIFY for the SX1268 (its datasheet was not available).
  bool regulator_ldo = false; // false = DC-DC regulator
};

// RadioLib's Arduino HAL with a BUSY guard: a BUSY level held high for longer than
// the bound is reported low once (so library loops without a timeout terminate) and
// latches stuck(); the driver then treats the chip as faulty and resets it.
class Sx126xHal : public ArduinoHal {
 public:
  Sx126xHal(SPIClass& bus, uint32_t busy_pin, uint32_t bound_ms);
  uint32_t digitalRead(uint32_t pin) override;
  bool stuck() const { return stuck_; }
  void clear_stuck() { stuck_ = false; high_ = false; }

 private:
  const uint32_t busy_pin_;
  const uint32_t bound_ms_;
  bool high_ = false;           // inside a continuous BUSY-high episode
  bool stuck_ = false;
  uint32_t first_ms_ = 0, last_ms_ = 0;
};

#if VGQ_SX126X_CHIP == 1268
using Sx126xChipBase = SX1268;
#elif VGQ_SX126X_CHIP == 1262
using Sx126xChipBase = SX1262;
#else
#error "VGQ_SX126X_CHIP must be 1268 or 1262"
#endif

// The RadioLib chip class with the protected SX126x commands this driver needs
// made accessible (no RADIOLIB_GODMODE / RADIOLIB_LOW_LEVEL build flags required).
class Sx126xChip : public Sx126xChipBase {
 public:
  explicit Sx126xChip(Module* m) : Sx126xChipBase(m) {}
  using SX126x::readRegister;
  using SX126x::setTx;
  using SX126x::setDioIrqParams;
  using SX126x::clearIrqStatus;
  using SX126x::getDeviceErrors;
  using SX126x::clearDeviceErrors;
  uint8_t tx_switch_mode() const { return this->txMode; }
  void clear_staged_mode() { this->stagedMode = RADIOLIB_RADIO_MODE_NONE; }
};

class Sx126xRadio : public Radio {
 public:
  // Stores the wiring only; touches no hardware until begin(). One instance per
  // program (DIO1 interrupt state is static).
  Sx126xRadio(SPIClass& spi, const Sx126xHw& hw);

  const char* name() const override;
  bool begin() override;
  bool configure(const RadioSettings& s, bool persist) override;
  bool transmit(const uint8_t* data, size_t n, uint32_t now) override;
  bool busy(uint32_t now) const override;
  RadioEvent poll(uint32_t now, RxPacket& rx, int32_t& value) override;
  bool request_noise(uint32_t now) override;
  bool channel_busy() const override { return cad_busy_; }
  bool set_hibernation(bool on, uint32_t now) override;

  // ---- diagnostics (last values read during configuration / verification) ----
  uint16_t device_errors() const { return dev_errors_; }   // GetDeviceErrors
  uint8_t rx_gain_register() const { return rx_gain_reg_; }  // 0x08AC: 0x96 boosted, 0x94 saving
  float ocp_readback_ma() const { return ocp_ma_rb_; }

 private:
  enum class State : uint8_t { OFF, RX, HIBERNATE, CAD, TX, BACKOFF, RESET_LOW, RESET_WAIT };
  struct QueuedEvent {
    RadioEvent ev;
    int32_t value;
  };
  static const uint8_t kQueueLen = 4;

  bool operational() const { return state_ == State::RX || state_ == State::HIBERNATE; }
  bool take_irq(uint32_t now, uint32_t& t_irq);
  bool check(int16_t st);
  bool init_chip();
  bool chip_present();
  bool apply_settings();
  bool verify_settings();
  bool start_rx(bool allow_duty_cycle);
  bool start_cad_or_tx(uint32_t now);
  bool launch_tx(uint32_t now);
  RadioEvent on_rx_irq(uint32_t now, uint32_t t_irq, RxPacket& rx, int32_t& value);
  RadioEvent on_tx_irq(uint32_t now, int32_t& value);
  void on_cad_irq(uint32_t now);
  void fail(uint8_t code, uint32_t now);
  void step_recovery(uint32_t now);
  void push(RadioEvent ev, int32_t value);
  bool pop(RadioEvent& ev, int32_t& value);
  uint32_t cad_period_ms() const;

  static void isr_dio1();
  static volatile bool s_dio1_;
  static volatile uint32_t s_dio1_ms_;

  SPIClass& spi_;
  const Sx126xHw hw_;
  Sx126xHal hal_;
  Module mod_;
  Sx126xChip chip_;

  State state_ = State::OFF;
  QueuedEvent queue_[kQueueLen] = {};
  uint8_t q_head_ = 0, q_count_ = 0;
  uint16_t preamble_ = 0;               // preamble length currently programmed
  // transmission
  uint8_t tx_buf_[kSxMaxPayload] = {0};
  uint8_t tx_len_ = 0;
  bool tx_in_flight_ = false;           // accepted by transmit(), end event not yet emitted
  bool tx_waiting_ = false;             // deferred by CAD, retry at cad_retry_ms_
  uint32_t tx_req_ms_ = 0, cad_retry_ms_ = 0, cad_t0_ = 0, tx_watchdog_ms_ = 0;
  bool cad_busy_ = false;
  // reception
  bool rx_active_ = false;              // header received, payload in progress
  uint32_t rx_hdr_ms_ = 0;
  // recovery
  uint32_t step_ms_ = 0, backoff_ms_ = 0, healthy_ms_ = 0;
  bool spi_ready_ = false;              // RadioLib has configured the SX126x SPI framing
  // diagnostics
  uint16_t dev_errors_ = 0;
  uint8_t rx_gain_reg_ = 0;
  float ocp_ma_rb_ = 0.0f;
};

}  // namespace vgq

#endif  // VGQ_HAVE_RADIOLIB
