// =============================================================================
//  Semtech SX1268 / SX1262 LoRa transceiver on SPI through RadioLib 7.8.1: the
//  long-range radio option (contract 2.2, 14, 25). Reference module: EBYTE
//  E22-400M33S (SX1268 + external PA/LNA, SPI). The UART E22 modules hide the
//  LoRa layer; this option exposes it: SF12 at 31.25-125 kHz, per-packet RSSI,
//  SNR and frequency error, channel activity detection and Rx boosted gain.
//
//  RadioLib is the copy bundled with the sketch (sketch.yaml "- dir:
//  libraries/RadioLib"): tag 7.8.1 plus tools/radiolib-7.8.1.patch, which adds
//  RADIOLIB_STATIC_ONLY (no heap: SPI buffers are two 268-octet stack arrays per
//  command) and the module exclusions in BuildOptUser.h. This header pulls in
//  RadioLib only when config.h selects VGQ_RADIO_SX126X, so the default E22
//  builds never compile it.
//
//  Robustness rules implemented here (Semtech DS SX1261/2 rev 2.2; every library
//  behaviour cited was confirmed in the RadioLib 7.8.1 sources by the audit):
//    * BUSY (8.3.1): every SPI command waits for BUSY low with a bound (Module SPI
//      timeout); a BUSY guard in the HAL also bounds the waits RadioLib performs
//      without one (calibration in begin()). The HAL keeps RadioLib's 1 us settle
//      before the first BUSY sample, which the Zephyr core shortens to 0 us. A
//      stuck BUSY, a command error status or an SPI failure raises a FAULT; the
//      driver then resets the chip through NRST, re-initialises it, re-applies the
//      settings and reports kRadioFaultCleared - in non-blocking steps, with back-off.
//    * Chip health: RadioLib reads each status byte during the command, before it
//      executes, so a SetRx that fails to execute is never reported. Every 10 s in
//      RX (and whenever the chip is awake in STDBY_RC while hibernating) GetStatus,
//      GetDeviceErrors, the Rx gain register and two registers whose values differ
//      from their power-on defaults (OCP 0x08E7, TxClampConfig 0x08D8: Table 5-2, 15.2)
//      are checked (Tables 13-76, 13-86): a chip that reset itself is re-initialised.
//    * DIO1: an interrupt line that stays high without a raised IRQ (wiring fault) is
//      a FAULT after a few attempts, and the CAD / TX deadlines are evaluated whatever
//      DIO1 does, so no line state can hold a transmission open.
//    * TCXO (9.2.1, 13.3.6): RadioLib begin() issues SetDIO3AsTCXOCtrl and then a
//      full Calibrate(0x7F). Errors begin() leaves in OpError are kept for the
//      verification (not discarded), and RadioLib's silent fall-back to the
//      crystal when the TCXO does not start is detected (tcxo_fallback()). Image
//      calibration is requested explicitly for the operating channel after every
//      (re)initialisation and channel change, because RadioLib skips it unless the
//      frequency moves by >= 20 MHz and keeps its cached frequency across NRST.
//    * Rx boosted gain (9.6): register 0x08AC = 0x96 plus the retention list
//      0x029F..0x02A1 = 01 08 AC (RadioLib setRxBoostedGainMode(true, true)),
//      re-asserted after every reset and read back.
//    * Known limitations (15): 15.1 (0x0889 bit 2 before every TX) and 15.4
//      (0x0736 bit 2 with every SetPacketParams) are applied by RadioLib in the
//      calls used here; 15.2 (TxClampConfig 0x08D8 bits 4..1) by begin() after
//      every reset and verified; 15.3 concerns implicit-header RX with timeout,
//      which is never used (explicit header, continuous / duty-cycled RX).
//    * PA (13.1.14, 13.4.4): the nominal configuration of Table 13-21's +22 dBm row
//      (paDutyCycle 0x04, hpMax 0x07) with the drive level set by SetTxParams; not
//      RadioLib's empirical per-level table. OCP set afterwards (Table 5-2), read back.
//    * TX (14.2): SetStandby before the frame is staged; SetTx with a timeout of
//      1.5 x the computed air time + 50 ms, DIO1 on TxDone and Timeout, plus a
//      software watchdog; CAD before TX when LBT is on. A transmission requested
//      while an RX interrupt is pending waits until poll() has serviced it.
//    * RX: continuous; per packet the status (RssiPkt, SnrPkt) and the frequency
//      error are read right after RxDone, before the payload, each read
//      status-checked (13.5.2, 13.5.3). A payload that fails the LoRa CRC is
//      counted and still delivered with RxPacket::crc_ok = false (contract 20, "RS
//      rescue"): the CLTU decoder's BCH, the FECF and SDLS decide whether it is
//      accepted. A header error (no length) is counted and nothing is delivered.
//    * Hibernation (13.1.7): duty-cycled RX whose periods satisfy
//      Tpreamble + Theader <= 2 * rxPeriod + sleepPeriod for the kSxWakePreambleSym
//      WAKE preamble, with the RF switch on its RX path. In the SLEEP phases BUSY is
//      high and only an NSS edge wakes the chip (8.3.1): every command sequence that
//      may meet a sleeping chip starts with RadioLib standby(), which sends that edge
//      without waiting for BUSY.
//
//  Integration rules:
//    * Call every method from the Zephyr main thread (setup(), loop() or a Bridge
//      provide_safe handler), never from a Bridge provide handler (500 B stack).
//      Stack: 1.3 KiB on the deepest chain (poll -> RxDone -> duty-cycle restart ->
//      RadioLib stageMode -> SPItransferStream with its two 268-octet buffers; GCC
//      -fstack-usage, arm-zephyr-eabi) plus the caller and the Zephyr SPI driver.
//    * Sequence: begin() once -> configure() -> poll() every loop pass, as for
//      every vgq::Radio (radio.h). Nothing blocks except (re)initialisation.
//
//  Constants marked VERIFY depend on the module and its wiring; they are set in
//  config.h and checked during bring-up (docs/08).
// =============================================================================
#pragma once
#include "../config.h"   // VGQ_RADIO must be final before the model guard below
#include "radio.h"

#if VGQ_RADIO == VGQ_RADIO_SX126X

#include <Arduino.h>
#include <SPI.h>
#include <RadioLib.h>

#if RADIOLIB_VERSION_MAJOR != 7 || RADIOLIB_VERSION_MINOR != 8 || RADIOLIB_VERSION_PATCH != 1
#error "drivers/sx126x.* are verified against RadioLib 7.8.1 only (they rely on SX126x internals)"
#endif
#if !RADIOLIB_STATIC_ONLY
#error "RadioLib is not the patched copy: run python3 tools/radiolib.py fetch <sketch>/libraries/RadioLib"
#endif
// Module copies every transfer into two stack arrays of this size without a bound
// check; the largest transfer here is ReadBuffer / WriteBuffer: opcode + offset +
// 255 data octets + 1 status octet (DS 13.2.4).
static_assert(RADIOLIB_STATIC_SPI_ARRAY_SIZE >= 2 + 255 + 1, "RadioLib static SPI buffer too small");

// Transceiver chip on the module: 1268 = SX1268 (E22-400M33S), 1262 = SX1262.
#ifndef VGQ_SX126X_CHIP
#define VGQ_SX126X_CHIP 1268
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
  bool dio2_rf_switch = false;   // DIO2 drives the RF switch. VERIFY: module wiring.
  int8_t pa_gain_db = kSxPaGainDbDefault;   // drive -> antenna gain. VERIFY: T-RF-07.
  float ocp_ma = 140.0f;      // PA current limit (SX1262 default after SetPaConfig, DS Table 5-2).
                              // VERIFY for the SX1268 (its datasheet was not available).
  bool regulator_ldo = false; // false = DC-DC regulator
};

// RadioLib's Arduino HAL with two corrections:
//  * BUSY guard: a BUSY level held high for longer than the bound is reported low
//    once (so library loops without a timeout terminate) and latches stuck(); the
//    driver then treats the chip as faulty and resets it.
//  * delayMicroseconds(us) waits us + 1: ArduinoCore-zephyr busy-waits us - 1
//    (cores/arduino/inlines.h), which would turn RadioLib's 1 us settle between NSS
//    rising and the first BUSY sample (Module.cpp; DS 8.3.1: BUSY rises within
//    600 ns) into no delay at all.
class Sx126xHal : public ArduinoHal {
 public:
  Sx126xHal(SPIClass& bus, uint32_t busy_pin, uint32_t bound_ms);
  uint32_t digitalRead(uint32_t pin) override;
  void delayMicroseconds(RadioLibTime_t us) override;
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
// The status reads below are single SPI transactions whose BUSY timeout and status
// byte both propagate (Module::SPIreadStream): RadioLib's own getters for the same
// data discard them and can return stale bytes or error codes as measurements.
// Every int16_t is a RadioLib status code (RADIOLIB_ERR_NONE = success).
class Sx126xChip : public Sx126xChipBase {
 public:
  explicit Sx126xChip(Module* m) : Sx126xChipBase(m) {}
  using SX126x::setTx;
  using SX126x::setDioIrqParams;
  using SX126x::clearIrqStatus;
  using SX126x::clearDeviceErrors;

  // ReadRegister, n = 1..kMaxRegRead octets from addr (DS 13.2.2).
  static const uint8_t kMaxRegRead = 16;
  int16_t read_regs(uint16_t addr, uint8_t* out, uint8_t n) {
    if (out == nullptr || n == 0 || n > kMaxRegRead) return RADIOLIB_ERR_UNKNOWN;
    const uint8_t cmd[3] = {RADIOLIB_SX126X_CMD_READ_REGISTER, (uint8_t)(addr >> 8), (uint8_t)(addr & 0xFF)};
    return this->getMod()->SPIreadStream(cmd, 3, out, n);
  }
  // GetIrqStatus (DS 13.3.3): IRQ flags as RADIOLIB_SX126X_IRQ_* bits.
  int16_t irq_status(uint16_t& irq) {
    uint8_t d[2] = {0, 0};
    const int16_t s = this->getMod()->SPIreadStream(RADIOLIB_SX126X_CMD_GET_IRQ_STATUS, d, 2);
    irq = (uint16_t)(((uint16_t)d[0] << 8) | d[1]);
    return s;
  }
  // GetPacketStatus, LoRa (DS 13.5.3, Tables 13-79 / 13-80): out = RssiPkt, SnrPkt, SignalRssiPkt.
  int16_t packet_status(uint8_t out[3]) {
    return this->getMod()->SPIreadStream(RADIOLIB_SX126X_CMD_GET_PACKET_STATUS, out, 3);
  }
  // GetRxBufferStatus (DS 13.5.2): PayloadLengthRx and RxStartBufferPointer.
  int16_t rx_buffer_status(uint8_t& len, uint8_t& offset) {
    uint8_t d[2] = {0, 0};
    const int16_t s = this->getMod()->SPIreadStream(RADIOLIB_SX126X_CMD_GET_RX_BUFFER_STATUS, d, 2);
    len = d[0];
    offset = d[1];
    return s;
  }
  // ReadBuffer (DS 13.2.4): n octets from the data buffer at offset.
  int16_t read_payload(uint8_t* out, uint8_t n, uint8_t offset) { return this->readBuffer(out, n, offset); }
  // GetRssiInst (DS 13.5.4): instantaneous RSSI, -raw / 2 dBm.
  int16_t rssi_inst(uint8_t& raw) {
    return this->getMod()->SPIreadStream(RADIOLIB_SX126X_CMD_GET_RSSI_INST, &raw, 1);
  }
  // GetDeviceErrors (DS 13.6.1, Table 13-86): OpError bits.
  int16_t device_errors(uint16_t& op_error) {
    uint8_t d[2] = {0, 0};
    const int16_t s = this->getMod()->SPIreadStream(RADIOLIB_SX126X_CMD_GET_DEVICE_ERRORS, d, 2);
    op_error = (uint16_t)(((uint16_t)d[0] << 8) | d[1]);
    return s;
  }
  // GetStatus (DS 13.5.1, Table 13-76): b6:4 chip mode, b3:1 command status. RadioLib
  // returns the raw byte without a transfer status; the HAL's BUSY guard and the
  // 0x00 / 0xFF (no chip) values cover a failed read.
  uint8_t chip_status() { return this->getStatus(); }
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
  // OpError (DS Table 13-86) of the last verification or health check, including the
  // errors begin() left at the last (re)initialisation; config_ok() is false while a
  // calibration / oscillator bit (0..6) is set.
  uint16_t device_errors() const { return dev_errors_; }
  uint16_t init_errors() const { return init_errors_; }       // OpError right after begin()
  // True when RadioLib fell back to the crystal because the TCXO did not start
  // (config_ok() is then false). Flight software reports it as an EVR WARN.
  bool tcxo_fallback() const { return tcxo_fallback_; }
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
  bool receiving(uint32_t now) const;
  uint8_t bus_fault() const { return hal_.stuck() ? kRadioFaultBusyStuck : kRadioFaultSpi; }
  bool take_irq(uint32_t now, uint32_t& t_irq);
  bool check(int16_t st);
  bool init_chip();
  bool chip_present();
  bool apply_settings();
  bool verify_settings();
  bool start_rx(bool allow_duty_cycle);
  bool start_cad_or_tx(uint32_t now);
  bool launch_tx(uint32_t now);
  bool chip_healthy(uint8_t want_mode, uint8_t& fault);
  bool relisten(bool chip_in_standby, uint8_t& fault);
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
  bool duty_cycling_ = false;           // SetRxDutyCycle running (else continuous RX)
  // transmission
  uint8_t tx_buf_[kSxMaxPayload] = {0};
  uint8_t tx_len_ = 0;
  bool tx_in_flight_ = false;           // accepted by transmit(), end event not yet emitted
  bool tx_waiting_ = false;             // deferred (CAD busy / RX IRQ pending), retry at cad_retry_ms_
  uint32_t tx_req_ms_ = 0, cad_retry_ms_ = 0, cad_t0_ = 0, tx_watchdog_ms_ = 0;
  bool cad_busy_ = false;
  // reception
  bool rx_active_ = false;              // header received, payload in progress
  uint32_t rx_hdr_ms_ = 0;
  uint8_t spurious_irqs_ = 0;           // consecutive DIO1 indications without an RX IRQ raised
  // recovery and health
  uint32_t step_ms_ = 0, backoff_ms_ = 0, healthy_ms_ = 0, health_ms_ = 0;
  bool spi_ready_ = false;              // RadioLib has configured the SX126x SPI framing
  // Configuration fingerprint read back by verify_settings(): OCP 0x08E7 (0x18 =
  // 60 mA after a reset, Table 5-2; 0x38 = 140 mA configured) and TxClampConfig
  // 0x08D8 (bits 4..1 = 0100 after a reset, DS 15.2; 1111 configured). A change
  // means the chip lost its configuration.
  uint8_t ocp_reg_ = 0, clamp_reg_ = 0;
  // diagnostics
  uint16_t dev_errors_ = 0, init_errors_ = 0;
  bool tcxo_fallback_ = false;
  uint8_t rx_gain_reg_ = 0;
  float ocp_ma_rb_ = 0.0f;
};

// The flight instance - the same object as flight_radio() - with its SX126x
// diagnostics (tcxo_fallback(), device_errors(), init_errors(), rx_gain_register(),
// ocp_readback_ma()), so the executive needs no downcast. SX126x builds only.
Sx126xRadio& flight_sx126x();

}  // namespace vgq

#endif  // VGQ_RADIO == VGQ_RADIO_SX126X
