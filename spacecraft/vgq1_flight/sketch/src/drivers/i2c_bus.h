// I2C bus access through a TCA9548A 1-to-8 multiplexer, with error accounting
// and bus recovery (the FDIR "I2C_BUS" response).
#pragma once
#include <Arduino.h>
#include <Wire.h>

namespace vgq {

class I2cBus {
 public:
  void begin(TwoWire& w, uint8_t mux_addr, int mux_reset_pin, uint32_t clock_hz);
  bool mux_present() const { return mux_ok_; }
  // Selects one downstream channel (0..7) or none (-1). Cached.
  bool select(int8_t ch);
  bool probe(uint8_t addr);
  bool write8(uint8_t addr, uint8_t reg, uint8_t val);
  bool write(uint8_t addr, uint8_t reg, const uint8_t* buf, size_t n);
  bool read(uint8_t addr, uint8_t reg, uint8_t* buf, size_t n);
  bool read_raw(uint8_t addr, uint8_t* buf, size_t n);   // no register pointer write
  void restore_clock();          // some vendor libraries call begin() and reset the clock
  void recover();                // pulse mux reset and re-init the controller
  TwoWire& wire() { return *w_; }
  uint32_t errors() const { return errors_; }
  uint8_t consecutive_errors() const { return consec_; }

 private:
  void note(bool ok);
  TwoWire* w_ = nullptr;
  uint8_t mux_ = 0x70;
  int reset_pin_ = -1;
  uint32_t clock_ = 100000;
  int8_t current_ = -2;
  bool mux_ok_ = false;
  uint32_t errors_ = 0;
  uint8_t consec_ = 0;
};

}  // namespace vgq
