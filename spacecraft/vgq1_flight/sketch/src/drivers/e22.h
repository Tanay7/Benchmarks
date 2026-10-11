// =============================================================================
//  EBYTE E22 UART LoRa modules - one driver for both supported models:
//    E22-400T33D  SX1262 + PA/LNA, 2 W (33 dBm), 3.3-5.5 V (>= 4.7 V for full
//                 power), 2.54 mm DIP: M0 M1 RXD TXD AUX VCC GND   (default)
//    E22-400T37S  5 W (37 dBm) on the E22-400TBH-02 board, 4.5-15 V  (preserved)
//
//  Register map (T33D manual v1.05 7.2 = T37S manual v1.5 7.2 except where noted):
//    00H ADDH | 01H ADDL | 02H NETID
//    03H REG0 = UART baud [7:5] | parity [4:3] | air data rate [2:0]
//             air rates: 000/001/010 = 2.4k (no slower setting), 011 = 4.8k,
//             100 = 9.6k, 101 = 19.2k, 110 = 38.4k, 111 = 62.5k
//    04H REG1 = sub-packet size [7:6] | ambient-noise RSSI enable [5] |
//             T37S: abnormal-status log enable [2]; T33D: [4:2] reserved |
//             TX power [1:0]  (T33D: 33/30/27/24 dBm, VERIFY T-RF-07; T37S: all 37 dBm)
//    05H REG2 = channel (f = 410.125 MHz + CH x 1 MHz, CH 0..83)
//    06H REG3 = RSSI byte [7] | fixed-point [6] | relay [5] | LBT [4] |
//             WOR role [3] | WOR cycle [2:0] (period (1 + code) x 500 ms)
//    07H/08H CRYPT (write-only, left 0: content is never obscured by the radio)
//    80H..86H product information (read-only)
//  Commands (configuration mode M1 = 1, M0 = 0, UART 9600 8N1 only):
//    C0 addr len data -> write and save     C2 addr len data -> write volatile
//    C1 addr len      -> read; every reply is C1 addr len data; FF FF FF = wrong format
//  Normal-mode ambient-noise query (needs REG1 bit 5): C0 C1 C2 C3 00 02 ->
//    C1 00 02 <noise> <last-packet RSSI>, dBm = -(256 - value)
//  AUX: LOW while the module is busy (self-test, buffering, mode change). After a
//  transmission AUX = 1 only means the buffer has gone to the RF chip: the last packet
//  may still be on air (manual 5.6.2), so TX_DONE also waits for the modelled air time.
//  LBT (REG3 bit 4) runs inside the module: it defers a transmission for at most
//  2 s, then sends anyway; the deferral is not observable from the host.
//
//  E22-400T37S only - abnormal-status log (REG1 bit 2, v1.5 5.7): while the module
//  is under-voltage (< 4.5 V), over-voltage (> 15 V) or over-temperature (> 120 C)
//  it stops transmitting and prints FF FF FF 01 / 02 / 03 / 04 every 500 ms. The
//  E22-400T33D has no such log; this driver never sets that bit on it.
//
//  Both models share this register protocol, so one binary serves both: begin()
//  reads the model strap (contract 33; config.h PIN_RADIO_MODEL_STRAP) and every
//  model-dependent detail - REG1 image, power-code meaning, fault-log decoding,
//  sensitivity - follows the model found there.
//
//  The receive byte stream is framed by vgq::E22StreamParser and the AUX-based
//  transmission tracking is vgq::E22TxTracker (both pure, in radio.h/.cpp).
// =============================================================================
#pragma once
#include <Arduino.h>

#include "radio.h"

namespace vgq {

class E22Radio : public Radio {
 public:
  enum Mode : uint8_t { NORMAL = 0, WOR = 1, CONFIG = 2, SLEEP = 3 };   // M1:M0

  // model: VGQ_RADIO_E22_T33D or VGQ_RADIO_E22_T37S (begin() fails for anything
  // else) - the build default. pin_strap (contract 33): the model strap read by
  // begin() before anything else (jumper to GND = E22-400T37S, open = `model`; see
  // e22_strap_model()), -1 = none. Stores references only; touches no hardware
  // until begin().
  E22Radio(HardwareSerial& serial, int pin_m0, int pin_m1, int pin_aux, uint8_t model, int pin_strap = -1);

  const char* name() const override;
  bool begin() override;
  bool configure(const RadioSettings& s, bool persist) override;
  bool transmit(const uint8_t* data, size_t n, uint32_t now) override;
  bool busy(uint32_t now) const override;
  RadioEvent poll(uint32_t now, RxPacket& rx, int32_t& value) override;
  bool request_noise(uint32_t now) override;
  bool channel_busy() const override { return false; }
  bool set_hibernation(bool on, uint32_t now) override;
  uint32_t rx_bytes() const override { return parser_.rx_bytes(); }

  // ---- E22-specific ----
  bool read_product_info(uint8_t info[7]);       // PID 80H..86H (returns in the previous mode)
  Mode mode() const { return mode_; }
  uint16_t command_rejects() const { return parser_.command_rejects(); }   // FF FF FF replies
  // Model strap as read by begin(): fitted (a pin is configured), and how many of the
  // kStrapSamples reads were LOW (jumper to GND). The model chosen is model().
  bool strap_fitted() const { return strap_ >= 0; }
  uint8_t strap_low_reads() const { return strap_low_; }
  uint8_t build_model() const { return build_model_; }

 private:
  bool aux_ready() const;
  bool wait_aux(uint32_t timeout_ms);
  bool set_mode(Mode m);
  void drain_uart();
  bool command(const uint8_t* cmd, size_t n, uint8_t* reply, size_t reply_len, uint32_t timeout_ms);

  HardwareSerial& serial_;
  const int m0_, m1_, aux_, strap_;
  const uint8_t build_model_;
  uint8_t strap_low_ = 0;
  Mode mode_ = SLEEP;
  E22StreamParser parser_;
  E22TxTracker tx_;
};

#if VGQ_RADIO != VGQ_RADIO_SX126X
// The flight instance - the same object as flight_radio() - with its model-strap
// state for the boot EVR, so the executive needs no downcast. E22 builds only.
E22Radio& flight_e22();
#endif

}  // namespace vgq
