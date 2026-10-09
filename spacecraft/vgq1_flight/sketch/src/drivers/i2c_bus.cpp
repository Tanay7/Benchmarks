#include "i2c_bus.h"

namespace vgq {

void I2cBus::begin(TwoWire& w, uint32_t clock_hz) {
  w_ = &w;
  clock_ = clock_hz;
  w_->begin();
  w_->setClock(clock_);
}

void I2cBus::restore_clock() { if (w_) w_->setClock(clock_); }

void I2cBus::note(bool ok) {
  if (ok) { consec_ = 0; return; }
  ++errors_;
  if (consec_ < 255) ++consec_;
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

bool I2cBus::write_raw(uint8_t addr, const uint8_t* buf, size_t n) {
  w_->beginTransmission(addr);
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
  w_->end();
  delay(2);
  w_->begin();
  w_->setClock(clock_);
  consec_ = 0;
}

}  // namespace vgq
