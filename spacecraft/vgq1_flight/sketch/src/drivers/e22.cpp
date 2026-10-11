#include "e22.h"

namespace vgq {

void E22Config::to_regs(uint8_t r[9]) const {
  r[0] = (uint8_t)(address >> 8);
  r[1] = (uint8_t)address;
  r[2] = netid;
  r[3] = (uint8_t)(((uart_code & 7) << 5) | ((parity & 3) << 3) | (air_rate & 7));
  r[4] = (uint8_t)(((subpacket & 3) << 6) | ((rssi_noise ? 1 : 0) << 5) | ((fault_log ? 1 : 0) << 2) |
                   (power & 3));
  r[5] = channel;
  r[6] = (uint8_t)(((rssi_byte ? 1 : 0) << 7) | ((fixed ? 1 : 0) << 6) | ((relay ? 1 : 0) << 5) |
                   ((lbt ? 1 : 0) << 4) | ((wor_role ? 1 : 0) << 3) | (wor_cycle & 7));
  r[7] = 0;   // CRYPT_H (no encryption)
  r[8] = 0;   // CRYPT_L
}

void E22Config::from_regs(const uint8_t r[9]) {
  address = (uint16_t)((r[0] << 8) | r[1]);
  netid = r[2];
  uart_code = (r[3] >> 5) & 7; parity = (r[3] >> 3) & 3; air_rate = r[3] & 7;
  subpacket = (r[4] >> 6) & 3; rssi_noise = (r[4] >> 5) & 1; fault_log = (r[4] >> 2) & 1;
  power = r[4] & 3;
  channel = r[5];
  rssi_byte = (r[6] >> 7) & 1; fixed = (r[6] >> 6) & 1; relay = (r[6] >> 5) & 1;
  lbt = (r[6] >> 4) & 1; wor_role = (r[6] >> 3) & 1; wor_cycle = r[6] & 7;
}

void E22::begin(HardwareSerial& s, int m0, int m1, int aux) {
  s_ = &s;
  m0_ = m0; m1_ = m1; aux_ = aux;
  pinMode(aux_, INPUT_PULLUP);
  // Drive deep-sleep first (matches the external pull-ups), then configure.
  pinMode(m0_, OUTPUT);
  pinMode(m1_, OUTPUT);
  digitalWrite(m0_, HIGH);
  digitalWrite(m1_, HIGH);
  mode_ = SLEEP;
  s_->begin(9600);     // config mode requires 9600 8N1; we also operate at 9600
  wait_aux(2000);      // power-on self-test holds AUX low
}

bool E22::wait_aux(uint32_t timeout_ms) {
  const uint32_t t0 = millis();
  while (!aux_ready()) {
    if (millis() - t0 > timeout_ms) return false;
    delay(1);
  }
  return true;
}

bool E22::set_mode(Mode m) {
  if (!wait_aux(1000)) return false;
  digitalWrite(m0_, (m & 1) ? HIGH : LOW);
  digitalWrite(m1_, (m & 2) ? HIGH : LOW);
  delay(5);                 // EBYTE: allow >= 2 ms after a mode change
  const bool ok = wait_aux(1000);
  delay(2);
  while (s_->available()) s_->read();   // discard anything emitted during the switch
  mode_ = m;
  return ok;
}

bool E22::command(const uint8_t* cmd, size_t n, uint8_t* reply, size_t reply_len,
                  uint32_t timeout_ms) {
  while (s_->available()) s_->read();
  s_->write(cmd, n);
  s_->flush();
  size_t got = 0;
  const uint32_t t0 = millis();
  while (got < reply_len && millis() - t0 < timeout_ms) {
    if (s_->available()) reply[got++] = (uint8_t)s_->read();
  }
  return got == reply_len;
}

bool E22::read_product_info(uint8_t info[7]) {
  const Mode prev = mode_;
  if (!set_mode(CONFIG)) return false;
  const uint8_t cmd[3] = {0xC1, 0x80, 0x07};
  uint8_t r[10];
  const bool ok = command(cmd, 3, r, 10, 1000) && r[0] == 0xC1 && r[1] == 0x80;
  if (ok) memcpy(info, r + 3, 7);
  set_mode(prev == CONFIG ? NORMAL : prev);
  return ok;
}

bool E22::configure(const E22Config& c, bool save) {
  if (!set_mode(CONFIG)) return false;
  uint8_t cmd[12];
  cmd[0] = save ? 0xC0 : 0xC2;
  cmd[1] = 0x00;
  cmd[2] = 0x09;
  c.to_regs(cmd + 3);
  uint8_t r[12];
  bool ok = command(cmd, 12, r, 12, 1000) && r[0] == 0xC1;
  // Independent read-back (CRYPT bytes read back as 0, which is what we wrote).
  if (ok) {
    const uint8_t rd[3] = {0xC1, 0x00, 0x09};
    uint8_t rb[12];
    ok = command(rd, 3, rb, 12, 1000) && memcmp(rb + 3, cmd + 3, 7) == 0;
  }
  set_mode(NORMAL);
  return ok;
}

void E22::transmit(const uint8_t* d, size_t n) { s_->write(d, n); }

void E22::request_rssi() {
  const uint8_t cmd[6] = {0xC0, 0xC1, 0xC2, 0xC3, 0x00, 0x02};
  s_->write(cmd, 6);
}

}  // namespace vgq
