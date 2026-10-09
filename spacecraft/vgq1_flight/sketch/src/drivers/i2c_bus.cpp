#include "i2c_bus.h"

namespace vgq {

void I2cBus::begin(TwoWire& w, uint8_t mux_addr, int mux_reset_pin, uint32_t clock_hz) {
  w_ = &w;
  mux_ = mux_addr;
  reset_pin_ = mux_reset_pin;
  clock_ = clock_hz;
  if (reset_pin_ >= 0) {
    pinMode(reset_pin_, OUTPUT);
    digitalWrite(reset_pin_, HIGH);       // TCA9548A /RESET is active low
  }
  w_->begin();
  w_->setClock(clock_);
  current_ = -2;
  mux_ok_ = probe(mux_);
  if (mux_ok_) select(-1);
}

void I2cBus::restore_clock() { if (w_) w_->setClock(clock_); }

void I2cBus::note(bool ok) {
  if (ok) { consec_ = 0; return; }
  ++errors_;
  if (consec_ < 255) ++consec_;
}

bool I2cBus::select(int8_t ch) {
  if (!mux_ok_) return true;               // no mux fitted: everything on one bus
  if (ch == current_) return true;
  w_->beginTransmission(mux_);
  w_->write(ch < 0 ? (uint8_t)0x00 : (uint8_t)(1u << ch));
  const bool ok = w_->endTransmission() == 0;
  note(ok);
  current_ = ok ? ch : -2;
  return ok;
}

bool I2cBus::probe(uint8_t addr) {
  w_->beginTransmission(addr);
  return w_->endTransmission() == 0;      // not counted as an error (used for discovery)
}

bool I2cBus::write8(uint8_t addr, uint8_t reg, uint8_t val) { return write(addr, reg, &val, 1); }

bool I2cBus::write(uint8_t addr, uint8_t reg, const uint8_t* buf, size_t n) {
  w_->beginTransmission(addr);
  w_->write(reg);
  for (size_t i = 0; i < n; ++i) w_->write(buf[i]);
  const bool ok = w_->endTransmission() == 0;
  note(ok);
  return ok;
}

bool I2cBus::read(uint8_t addr, uint8_t reg, uint8_t* buf, size_t n) {
  w_->beginTransmission(addr);
  w_->write(reg);
  if (w_->endTransmission(false) != 0) { note(false); return false; }   // repeated start
  return read_raw(addr, buf, n);
}

bool I2cBus::read_raw(uint8_t addr, uint8_t* buf, size_t n) {
  const size_t got = w_->requestFrom(addr, n);
  bool ok = got == n;
  for (size_t i = 0; i < n; ++i) buf[i] = ok && w_->available() ? (uint8_t)w_->read() : 0;
  while (w_->available()) w_->read();
  note(ok);
  return ok;
}

void I2cBus::recover() {
  if (reset_pin_ >= 0) {
    digitalWrite(reset_pin_, LOW);
    delayMicroseconds(10);                // t_W(L) >= 6 ns per TCA9548A datasheet
    digitalWrite(reset_pin_, HIGH);
  }
  w_->end();
  delay(2);
  w_->begin();
  w_->setClock(clock_);
  current_ = -2;
  consec_ = 0;
  mux_ok_ = probe(mux_);
}

}  // namespace vgq
