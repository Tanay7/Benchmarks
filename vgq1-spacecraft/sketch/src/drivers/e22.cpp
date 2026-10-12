// =============================================================================
//  EBYTE E22-400T33D / E22-400T37S UART driver (see e22.h for the register map).
//
//  Timing (manual 5.5, 5.6.4, 6.1): after power-on the module holds AUX low for
//  its self-test; a mode change takes effect >= 2 ms after AUX is high; the
//  register protocol is only available in mode 2 at 9600 8N1. Every wait below
//  is bounded by both a time limit and an iteration count.
// =============================================================================
#include "../config.h"   // VGQ_RADIO, RADIO_SERIAL, PIN_E22_* (used by flight_radio())
#include "e22.h"

namespace vgq {

namespace {

const uint32_t kSelfTestMs = 2000;    // power-on self-test (AUX low)
const uint32_t kModeAuxMs = 1000;     // AUX high before / after a mode change
const uint32_t kModeSettleMs = 5;     // EBYTE: >= 2 ms after the mode pins change
const uint32_t kCmdReplyMs = 1000;    // register command reply
const uint32_t kCmdPollUs = 100;      // reply polling step (1 octet = 1.04 ms at 9600 bit/s)
const int kMaxDrain = 512;            // UART octets handled per drain / poll() call

const uint8_t kNoiseQuery[6] = {0xC0, 0xC1, 0xC2, 0xC3, 0x00, 0x02};

}  // namespace

E22Radio::E22Radio(HardwareSerial& serial, int pin_m0, int pin_m1, int pin_aux, uint8_t model, int pin_strap)
    : Radio(model, 0), serial_(serial), m0_(pin_m0), m1_(pin_m1), aux_(pin_aux), strap_(pin_strap),
      build_model_(model), parser_(model) {}

const char* E22Radio::name() const { return radio_model_name(model_); }

bool E22Radio::begin() {
  if (build_model_ != VGQ_RADIO_E22_T33D && build_model_ != VGQ_RADIO_E22_T37S) return false;
  // Model strap (contract 33), before the first register access: kStrapSamples reads
  // with the internal pull-up, kStrapSettleUs apart (the first after one settle time),
  // so a bouncing or picked-up level cannot select the 5 W module by itself.
  strap_low_ = 0;
  if (strap_ >= 0) {
    pinMode(strap_, INPUT_PULLUP);
    for (uint8_t i = 0; i < kStrapSamples; ++i) {
      delayMicroseconds(kStrapSettleUs);
      if (digitalRead(strap_) == LOW) ++strap_low_;
    }
  }
  model_ = e22_strap_model(build_model_, strap_low_, strap_ >= 0 ? kStrapSamples : (uint8_t)0);
  parser_.set_model(model_);
  pinMode(aux_, INPUT_PULLUP);
  // Drive deep sleep first (as the external 10 kOhm pull-ups do while the MCU boots).
  pinMode(m0_, OUTPUT);
  pinMode(m1_, OUTPUT);
  digitalWrite(m0_, HIGH);
  digitalWrite(m1_, HIGH);
  mode_ = SLEEP;
  serial_.begin(kE22UartBaud);        // mode 2 requires 9600 8N1; the link also runs at 9600
  return wait_aux(kSelfTestMs);
}

bool E22Radio::aux_ready() const { return digitalRead(aux_) == HIGH; }

bool E22Radio::wait_aux(uint32_t timeout_ms) {
  const uint32_t t0 = millis();
  for (uint32_t i = 0; !aux_ready(); ++i) {
    if (millis() - t0 > timeout_ms || i > timeout_ms) return false;
    delay(1);
  }
  return true;
}

void E22Radio::drain_uart() {
  for (int i = 0; i < kMaxDrain && serial_.available() > 0; ++i) (void)serial_.read();
}

bool E22Radio::set_mode(Mode m) {
  if (!wait_aux(kModeAuxMs)) return false;   // a busy module ignores the mode pins
  digitalWrite(m0_, (m & 1) ? HIGH : LOW);
  digitalWrite(m1_, (m & 2) ? HIGH : LOW);
  mode_ = m;
  delay(kModeSettleMs);
  const bool ok = wait_aux(kModeAuxMs);
  delay(2);
  drain_uart();                              // anything emitted during the switch
  return ok;
}

bool E22Radio::command(const uint8_t* cmd, size_t n, uint8_t* reply, size_t reply_len,
                       uint32_t timeout_ms) {
  drain_uart();
  if (serial_.write(cmd, n) != n) return false;
  serial_.flush();
  size_t got = 0;
  const uint32_t t0 = millis();
  const uint32_t max_polls = timeout_ms * (1000u / kCmdPollUs);
  for (uint32_t i = 0; got < reply_len && i < max_polls && millis() - t0 < timeout_ms; ++i) {
    if (serial_.available() > 0) {
      const int c = serial_.read();
      if (c >= 0) reply[got++] = (uint8_t)c;
    } else {
      delayMicroseconds(kCmdPollUs);
    }
  }
  return got == reply_len;
}

bool E22Radio::configure(const RadioSettings& s, bool persist) {
  if (!radio_settings_valid(s)) return false;
  if (tx_.active()) return false;            // AUX is low while sending: mode 2 would time out
  settings_ = s;
  uint8_t cmd[12];
  cmd[0] = persist ? 0xC0 : 0xC2;
  cmd[1] = 0x00;
  cmd[2] = 0x09;
  e22_build_regs(model_, s, cmd + 3);
  bool ok = set_mode(CONFIG);
  if (ok) {
    uint8_t r[12];
    ok = command(cmd, sizeof cmd, r, sizeof r, kCmdReplyMs) && r[0] == 0xC1 && r[1] == 0x00 &&
         r[2] == 0x09;
  }
  if (ok) {
    // Independent read-back of 00H..06H (CRYPT_H/L are write-only).
    const uint8_t rd[3] = {0xC1, 0x00, 0x09};
    uint8_t rb[12];
    ok = command(rd, sizeof rd, rb, sizeof rb, kCmdReplyMs) && rb[0] == 0xC1 && rb[1] == 0x00 &&
         rb[2] == 0x09 && e22_regs_match(model_, cmd + 3, rb + 3);
  }
  // Leave mode 2 if the pins went there: WOR receiver while hibernating, else normal.
  bool mode_ok = false;
  if (mode_ == CONFIG) mode_ok = set_mode(hibernating_ ? WOR : NORMAL);
  cfg_ok_ = ok && mode_ok;
  return cfg_ok_;
}

bool E22Radio::transmit(const uint8_t* data, size_t n, uint32_t now) {
  if (data == nullptr || n == 0 || n > kE22MaxPacket || tx_.active()) return false;
  // Beacons while hibernating are sent from normal mode, as in v2.
  if (mode_ != NORMAL && !set_mode(NORMAL)) return false;
  // Non-blocking: the core's UART TX ring (1 KiB) takes a whole <= 240-octet frame.
  if (serial_.write(data, n) == 0) return false;
  tx_.start(now, airtime_ms(n));                    // AUX may rise while the frame is on air (5.6.2)
  tx_launch_ms_ = now;
  return true;
}

bool E22Radio::busy(uint32_t now) const {
  return tx_.active() || !aux_ready() || parser_.receiving(now) || parser_.noise_pending();
}

RadioEvent E22Radio::poll(uint32_t now, RxPacket& rx, int32_t& value) {
  for (int i = 0; i < kMaxDrain && serial_.available() > 0; ++i) {
    const int c = serial_.read();
    if (c < 0) break;
    parser_.on_byte((uint8_t)c, now);
  }
  if (tx_.active()) {
    const RadioEvent ev = tx_.poll(now, aux_ready(), value);
    if (ev != RadioEvent::NONE) {
      // After a hibernation beacon, back to the duty-cycled WOR receiver.
      if (hibernating_ && !set_mode(WOR)) cfg_ok_ = false;
      return ev;
    }
  }
  return parser_.poll(now, rx, value);
}

bool E22Radio::request_noise(uint32_t now) {
  // The query is defined for mode 0 (and the WOR transmitter role, never used here).
  if (parser_.noise_pending() || tx_.active() || mode_ != NORMAL) return false;
  if (serial_.write(kNoiseQuery, sizeof kNoiseQuery) != sizeof kNoiseQuery) return false;
  parser_.noise_requested(now);
  return true;
}

bool E22Radio::set_hibernation(bool on, uint32_t now) {
  (void)now;
  if (on == hibernating_) return true;
  if (tx_.active()) return false;
  hibernating_ = on;
  // Entering: re-write the registers (WOR receiver role, settings_.wor_cycle) and end
  // in mode 1. Leaving: mode 0; the registers need no change.
  return on ? configure(settings_, false) : set_mode(NORMAL);
}

// ---- Flight instance (E22 builds) ----------------------------------------------------
// VGQ_RADIO is the build default; the strap may select the other E22 model at boot.
#if VGQ_RADIO == VGQ_RADIO_E22_T33D || VGQ_RADIO == VGQ_RADIO_E22_T37S
namespace {
E22Radio g_flight_radio(RADIO_SERIAL, PIN_E22_M0, PIN_E22_M1, PIN_E22_AUX, VGQ_RADIO, PIN_RADIO_MODEL_STRAP);
}  // namespace
Radio& flight_radio() { return g_flight_radio; }
E22Radio& flight_e22() { return g_flight_radio; }
#endif

}  // namespace vgq
