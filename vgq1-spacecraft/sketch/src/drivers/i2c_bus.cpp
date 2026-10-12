// =============================================================================
//  I2C bus access, error accounting and bus clear (see i2c_bus.h).
// =============================================================================
#include "i2c_bus.h"
#include "../config.h"   // kI2cClearHalfPeriodUs, kI2cClearMaxPulses

namespace vgq {

void I2cBus::begin(TwoWire& w, uint32_t clock_hz, int pin_sda, int pin_scl) {
  w_ = &w;
  clock_ = clock_hz;
  sda_ = pin_sda;
  scl_ = pin_scl;
  // The controller has not been started yet in this run, so the GPIOs can be used
  // directly; begin() below re-muxes them to I2C.
  begin_clear_ok_ = (sda_ >= 0 && scl_ >= 0) ? bus_clear() : true;
  w_->begin();
  w_->setClock(clock_);                 // load-bearing: clears PE = resets the I2C block (header step 4)
}

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
  (void)w_->write(reg);
  for (size_t i = 0; i < n; ++i) (void)w_->write(buf[i]);   // queued; endTransmission reports
  const bool ok = w_->endTransmission() == 0;
  note(ok);
  return ok;
}

bool I2cBus::write_raw(uint8_t addr, const uint8_t* buf, size_t n) {
  w_->beginTransmission(addr);
  for (size_t i = 0; i < n; ++i) (void)w_->write(buf[i]);
  const bool ok = w_->endTransmission() == 0;
  note(ok);
  return ok;
}

bool I2cBus::read(uint8_t addr, uint8_t reg, uint8_t* buf, size_t n) {
  w_->beginTransmission(addr);
  (void)w_->write(reg);
  // Register-pointer write. ArduinoCore-zephyr ignores the stop argument (Wire.cpp
  // endTransmission: ARG_UNUSED(stopBit), i2c_write() ends with STOP), so this is a
  // complete write and the read below a separate transaction - no repeated start. Every
  // target on this bus accepts that (RM3100 manual 4.5.2 shows P before the read).
  if (w_->endTransmission(false) != 0) { note(false); return false; }
  return read_raw(addr, buf, n);
}

bool I2cBus::read_raw(uint8_t addr, uint8_t* buf, size_t n) {
  const size_t got = w_->requestFrom(addr, n);
  const bool ok = got == n;
  for (size_t i = 0; i < n; ++i) buf[i] = ok && w_->available() ? (uint8_t)w_->read() : 0;
  // Discard anything beyond n (bounded: the controller buffer holds at most what was
  // requested, so this ends after a few octets at most).
  for (size_t i = 0; i < 256 && w_->available(); ++i) (void)w_->read();
  note(ok);
  return ok;
}

// ArduinoCore-zephyr delayMicroseconds(us) busy-waits us - 1 (cores/arduino/inlines.h):
// +1 keeps every half period at least kI2cClearHalfPeriodUs (standard mode: SCL low
// >= 4.7 us, high >= 4.0 us), so the pulses are legal 100 kHz clocks or slower.
static inline void half_period() { delayMicroseconds((unsigned int)kI2cClearHalfPeriodUs + 1u); }

// A line is released with the MCU's own weak pull-up on (see the header): an empty bus
// then reads high, a target holding the line still reads low.
static inline void release(int pin) { pinMode((pin_size_t)pin, INPUT_PULLUP); }

bool I2cBus::bus_clear() {
  // Both lines released: the bus pull-ups (or only the MCU's own) define the idle level.
  release(sda_);
  release(scl_);
  half_period();
  bool ok = true;
  if (digitalRead((pin_size_t)sda_) == LOW) {
    // A target holds SDA: clock it until it releases the line (at most one byte plus
    // the acknowledge, i.e. nine clocks).
    for (uint8_t i = 0; i < kI2cClearMaxPulses; ++i) {
      pinMode((pin_size_t)scl_, OUTPUT);                       // SCL driven low
      half_period();
      release(scl_);                                           // SCL released
      half_period();
      if (digitalRead((pin_size_t)scl_) == LOW) {             // something holds SCL
        ok = false;                                            // "SCL stuck low"
        break;
      }
      if (digitalRead((pin_size_t)sda_) == HIGH) break;        // SDA released
    }
  }
  if (ok) {
    // SCL is released (high) here. Pulling SDA low now is a START condition and
    // releasing it again while SCL stays high is the STOP that returns every target
    // to idle (START + STOP, the usual recovery sequence).
    pinMode((pin_size_t)sda_, OUTPUT);                         // SDA driven low (START)
    half_period();
    release(sda_);                                             // SDA released (STOP)
    half_period();
    ok = digitalRead((pin_size_t)sda_) == HIGH && digitalRead((pin_size_t)scl_) == HIGH;
  }
  // Both pins are left released whatever happened; Wire begin() re-muxes them.
  release(sda_);
  release(scl_);
  return ok;
}

bool I2cBus::recover() {
  if (w_ == nullptr) return false;
  w_->end();
  const bool ok = (sda_ >= 0 && scl_ >= 0) ? bus_clear() : true;
  w_->begin();
  w_->setClock(clock_);                 // load-bearing: clears PE = resets the I2C block (header step 4)
  consec_ = 0;
  ++recoveries_;
  return ok;
}

}  // namespace vgq
