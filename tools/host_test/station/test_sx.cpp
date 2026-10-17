// Station sketch, SX126x build (DSSQ_RADIO_SX126X=1, DSSQ_SX_CHIP from run.sh), against the
// bundled patched RadioLib and the fake SX126x on the SPI (runtime.cpp):
//   * C54: a received packet is forwarded as rf_rx hex of at most 480 characters (a
//     CRC-good packet longer than 236 octets arrives with an empty payload, counted);
//   * C25: a TX fault (DIO1 without TxDone, BUSY stuck high, the TX deadline) dequeues
//     the frame exactly once and reports T|0;
//   * I3: the S| status line carries 20 fields + the compiled chip + the TCXO supply in mV,
//     with worst-case counters not truncated;
//   * pin plan H (D-72): the SX126x pins clear of every other station pin; the time on air;
//   * D-78: the LED-matrix uplink row follows the radio's own TX state.
// SKETCH (a -D from run.sh) is the sketch file, compiled into this translation unit.
#include "runtime.h"
#include SKETCH
#include <string>

static std::vector<Note> notes(const char* method) {
  std::vector<Note> v;
  std::lock_guard<std::mutex> lock(Bridge.m);
  for (auto& n : Bridge.notes) {
    if (n.method == method) v.push_back(n);
  }
  return v;
}
static void clear_notes() {
  std::lock_guard<std::mutex> lock(Bridge.m);
  Bridge.notes.clear();
}
static int t_events() {
  int n = 0;
  for (auto& e : notes("rf_evt")) n += e.text.rfind("T|0|", 0) == 0;
  return n;
}

// Puts the radio state machine in state `s` with `queued` frames waiting to be sent.
static void prepare(SxState s, uint8_t queued) {
  g_sx.active = g_sx.wanted = SxConfig{23, 1, 0, true, true, 0};
  g_sx.preamble = kSxPreamble;
  g_sx.tx_head = 0;
  g_sx.tx_count = queued;
  g_sx.tx_ok = g_sx.tx_fail = g_sx.rx_ok = g_sx.rx_crc = 0;
  for (auto& q : g_sx.txq) q.len = 10;
  g_sx.timeout_ms = 1000;
  g_sx.last_status = millis();
  g_sx.last_health = millis();
  g_sx_mod.spiConfig.timeout = kSxBusyTimeoutMs;
  // SPI framing as SX126x::modSetup() sets it in begin() (not run here).
  g_sx_mod.spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_ADDR] = Module::BITS_16;
  g_sx_mod.spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_CMD] = Module::BITS_8;
  g_sx_mod.spiConfig.widths[RADIOLIB_MODULE_SPI_WIDTH_STATUS] = Module::BITS_8;
  g_sx_mod.spiConfig.statusPos = 1;
  g_sx_mod.spiConfig.cmds[RADIOLIB_MODULE_SPI_COMMAND_READ] = RADIOLIB_SX126X_CMD_READ_REGISTER;
  g_sx_mod.spiConfig.cmds[RADIOLIB_MODULE_SPI_COMMAND_WRITE] = RADIOLIB_SX126X_CMD_WRITE_REGISTER;
  g_sx_mod.spiConfig.cmds[RADIOLIB_MODULE_SPI_COMMAND_NOP] = RADIOLIB_SX126X_CMD_NOP;
  g_sx_mod.spiConfig.stream = true;
  sx_set_state(s);
  g_pins.level[PIN_SX_BUSY] = LOW;
  clear_notes();
}

int main() {
  // ---- C54: rf_rx stays within 480 characters ----------------------------------------
  const uint8_t lens[] = {250, 236, 100};
  const size_t want[] = {0, 472, 200};
  for (int k = 0; k < 3; ++k) {
    prepare(kSxRx, 0);
    g_fsx.irq = 0x0002 | 0x0010;                      // RxDone + HeaderValid, no CrcErr
    g_fsx.rx_len = lens[k];
    g_pins.level[PIN_SX_DIO1] = HIGH;
    sx_service();
    g_pins.level[PIN_SX_DIO1] = LOW;
    auto rx = notes("rf_rx");
    char what[96];
    snprintf(what, sizeof what, "CRC-good %u-octet packet: rf_rx hex of %zu characters, counted",
             static_cast<unsigned>(lens[k]), want[k]);
    CHECK(rx.size() == 1 && rx[0].text.size() == want[k] && rx[0].crc == 1 && g_sx.rx_ok == 1, what);
  }
  // ---- C25: a TX fault dequeues the frame once ----------------------------------------
  prepare(kSxTx, 1);
  g_fsx.irq = 0x0000;                                 // DIO1 high without TxDone
  g_pins.level[PIN_SX_DIO1] = HIGH;
  sx_service();
  g_pins.level[PIN_SX_DIO1] = LOW;
  CHECK(g_sx.state == kSxOff && g_sx.tx_count == 0 && g_sx.tx_fail == 1 && t_events() == 1,
        "TX fault 'DIO1 without TxDone': frame dequeued, T|0 sent, tx_fail 1");
  prepare(kSxTx, 1);
  g_fsx.irq = 0x0001;                                 // TxDone, but BUSY stuck: finishTransmit faults
  g_pins.level[PIN_SX_DIO1] = HIGH;
  g_pins.level[PIN_SX_BUSY] = HIGH;
  sx_service();
  g_pins.level[PIN_SX_DIO1] = LOW;
  g_pins.level[PIN_SX_BUSY] = LOW;
  CHECK(g_sx.state == kSxOff && g_sx.tx_count == 0 && g_sx.tx_ok == 0 && t_events() == 1,
        "TX fault with BUSY stuck high: frame dequeued, not counted as sent");
  prepare(kSxTx, 2);
  g_sx.t_state = millis() - 2000;                     // past the deadline
  sx_service();
  CHECK(g_sx.tx_count == 1 && g_sx.tx_head == 1 && t_events() == 1,
        "TX deadline: exactly one frame dequeued (no double dequeue)");
  // ---- I3: S| carries chip and tcxo_mv --------------------------------------------------
  prepare(kSxRx, 0);
  g_sx.rx_ok = 4294967295UL;
  g_sx.rx_crc = g_sx.rx_hdr = g_sx.tx_ok = g_sx.tx_fail = g_sx.cad_busy = 4294967295UL;
  g_sx.cad_forced = g_sx.faults = g_sx.resets = 4294967295UL;
  g_sx.active.foff_hz = -50000;
  sx_status();
  auto ev = notes("rf_evt");
  std::vector<std::string> f;
  if (!ev.empty()) {
    std::string cur;
    for (char c : ev.back().text) {
      if (c == '|') {
        f.push_back(cur);
        cur.clear();
      } else {
        cur += c;
      }
    }
    f.push_back(cur);
  }
  char want_chip[8];
  snprintf(want_chip, sizeof want_chip, "%d", DSSQ_SX_CHIP);
  CHECK(f.size() == 23 && f[0] == "S" && f[20] == "-50000" && f[21] == want_chip && f[22] == "1800",
        "S| line: 20 fields + chip + tcxo_mv, worst-case counters not truncated");
  // ---- Pin plan H (D-72) and the time on air ---------------------------------------------
  const int pins[] = {PIN_RCU_M0, PIN_RCU_M1, PIN_RCU_AUX, PIN_LED_AOS, PIN_LED_ALARM, PIN_LED_UPLINK, PIN_BUZZER,
                      PIN_SX_NSS, PIN_SX_BUSY, PIN_SX_DIO1, PIN_SX_NRST, PIN_SX_RXEN, PIN_SX_TXEN, A0};
  bool distinct = true;
  for (size_t i = 0; i < sizeof pins / sizeof pins[0]; ++i)
    for (size_t j = i + 1; j < sizeof pins / sizeof pins[0]; ++j) distinct = distinct && pins[i] != pins[j];
  CHECK(distinct && PIN_SX_NRST == A4, "SX126x pins clear of the RCU, lamps, buzzer (A1) and A0; NRST on A4");
  prepare(kSxRx, 0);
  // Profile 1 (SF12 / 62.5 kHz, LDRO), preamble 12, 236 octets: ceil((8 x 236 - 48 + 28 + 16) /
  // (4 x (12 - 2))) = 48 blocks, (12 + 4.25 + 8 + 48 x 5) x 65.536 ms = 17 317.888 ms, rounded
  // up (the bandwidth converted with 32 bits, not __aeabi_f2ulz).
  CHECK(airtime_ms(236) == 17318, "time on air of one CADU at profile 1: 17 318 ms");
  // ---- D-78: the matrix uplink row also shows the radio's own TX state ----------------------
  fp_line_exec("S|1|0|0|0");                         // the handler body (no setup(): no mailbox here)
  fp_line_exec("M|P|0|-90|?|0|?");
  uint8_t mx[kMxCols * kMxRows];
  sx_set_state(kSxTx);
  matrix_frame(mx, g_last_rx_ms + 1);
  const bool tx_row = mx[2 * kMxCols] == kMxBright && mx[2 * kMxCols + 12] == kMxBright;
  sx_set_state(kSxRx);
  matrix_frame(mx, g_last_rx_ms + 1);
  CHECK(tx_row && mx[2 * kMxCols] == 0 && mx[1 * kMxCols] == 0,
        "matrix: uplink row lit while the SX126x is in TX (GDS tx 0); packet radio = no frame-sync row");
  printf("%s [SX%d]: %d failure(s)\n", SKETCH, DSSQ_SX_CHIP, g_failures);
  return g_failures ? 1 : 0;
}
