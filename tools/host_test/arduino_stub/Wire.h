#pragma once
#include "Arduino.h"
class TwoWire : public Stream {
 public:
  void begin() {}
  void begin(uint8_t) {}
  void end() {}
  void setClock(uint32_t) {}
  void beginTransmission(uint8_t) {}
  uint8_t endTransmission(bool = true) { return 0; }
  size_t requestFrom(uint8_t, size_t, bool = true) { return 0; }
  using Print::write;
  void onRequest(void (*)()) {}
  void onReceive(void (*)(int)) {}
};
extern TwoWire Wire, Wire1, Wire2;
