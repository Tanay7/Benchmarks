// I2C bus access with error accounting and bus recovery (the FDIR "I2C_BUS"
// response). All four instruments have distinct addresses, so there is no
// multiplexer: one Qwiic bus (Wire1, 3.3 V) carries everything.
#pragma once
#include <Arduino.h>
#include <Wire.h>

namespace vgq {

class I2cBus {
 public:
  void begin(TwoWire& w, uint32_t clock_hz);
  bool probe(uint8_t addr);
  bool write8(uint8_t addr, uint8_t reg, uint8_t val);
  bool write(uint8_t addr, uint8_t reg, const uint8_t* buf, size_t n);
  bool write_raw(uint8_t addr, const uint8_t* buf, size_t n);   // no register pointer
  bool read(uint8_t addr, uint8_t reg, uint8_t* buf, size_t n);
  bool read_raw(uint8_t addr, uint8_t* buf, size_t n);          // no register pointer write
  void restore_clock();          // some vendor libraries call begin() and reset the clock
  void recover();                // re-initialise the controller after a bus fault
  TwoWire& wire() { return *w_; }
  uint32_t errors() const { return errors_; }
  uint8_t consecutive_errors() const { return consec_; }

 private:
  void note(bool ok);
  TwoWire* w_ = nullptr;
  uint32_t clock_ = 100000;
  uint32_t errors_ = 0;
  uint8_t consec_ = 0;
};

}  // namespace vgq
