// =============================================================================
//  EBYTE E22-400T37S (SX1268 + PA/LNA, 5 W) UART LoRa module driver.
//
//  Register map (EBYTE E22 user manual; identical across the E22 UART family):
//    00H ADDH | 01H ADDL | 02H NETID
//    03H REG0 = UART baud [7:5] | parity [4:3] | air data rate [2:0]
//    04H REG1 = sub-packet size [7:6] | ambient-noise RSSI enable [5] | TX power [1:0]
//    05H REG2 = channel (f = 410.125 MHz + CH x 1 MHz, CH 0..83)
//    06H REG3 = RSSI byte [7] | fixed-point [6] | relay [5] | LBT [4] | WOR role [3] | WOR cycle [2:0]
//    07H/08H CRYPT (write-only — left at 0: amateur rules forbid obscuring content)
//  Commands (configuration mode M1=1 M0=0, UART must be 9600 8N1):
//    C0 addr len data  -> write & save      C2 addr len data -> write volatile
//    C1 addr len       -> read; reply C1 addr len data
//  Normal mode RSSI query (needs REG1 bit5): C0 C1 C2 C3 00 02 -> C1 00 02 <noise> <last>
//    dBm = -(256 - value)
//  AUX: LOW while the module is busy (self-test, buffering, transmitting).
//
//  TX power code meaning for the 37 dBm part is per EBYTE's convention (00 =
//  maximum, each step lower); confirm the exact dBm per code in the manual
//  revision that came with your module.
// =============================================================================
#pragma once
#include <Arduino.h>

namespace vgq {

struct E22Config {
  uint16_t address = 0;
  uint8_t netid = 0;
  uint8_t uart_code = 0b011;     // 9600 bps
  uint8_t parity = 0b00;         // 8N1
  uint8_t air_rate = 0b010;      // 2.4 kbps
  uint8_t subpacket = 0b00;      // 240 octets
  bool rssi_noise = true;        // enable C0 C1 C2 C3 ambient-noise reads
  uint8_t power = 0b00;          // max
  uint8_t channel = 23;          // 433.125 MHz
  bool rssi_byte = true;         // append RSSI octet to every received packet
  bool fixed = false, relay = false, lbt = false, wor_role = false;
  uint8_t wor_cycle = 0;

  void to_regs(uint8_t r[9]) const;
  void from_regs(const uint8_t r[9]);
};

class E22 {
 public:
  enum Mode : uint8_t { NORMAL = 0, WOR = 1, CONFIG = 2, SLEEP = 3 };

  void begin(HardwareSerial& s, int m0, int m1, int aux);
  bool aux_ready() const { return digitalRead(aux_) == HIGH; }
  bool wait_aux(uint32_t timeout_ms);
  bool set_mode(Mode m);
  Mode mode() const { return mode_; }

  // Writes, then reads back and compares. Leaves the module in NORMAL mode.
  bool configure(const E22Config& c, bool save);
  bool read_config(E22Config& c);
  bool read_product_info(uint8_t info[7]);

  // Queues `n` octets for transmission (non-blocking: the core UART TX ring is
  // 1 KiB). Air-time is measured by the caller from AUX.
  void transmit(const uint8_t* d, size_t n);

  // Sends the ambient-noise query; the reply is parsed by the receive path.
  void request_rssi();
  HardwareSerial& serial() { return *s_; }

 private:
  bool command(const uint8_t* cmd, size_t n, uint8_t* reply, size_t reply_len, uint32_t timeout_ms);
  HardwareSerial* s_ = nullptr;
  int m0_ = -1, m1_ = -1, aux_ = -1;
  Mode mode_ = SLEEP;
};

}  // namespace vgq
