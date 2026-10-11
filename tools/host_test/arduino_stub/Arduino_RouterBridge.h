#pragma once
#include "Arduino.h"
struct BridgeStub {
  bool begin() { return true; }
  template <class F> bool provide(const char*, F) { return true; }
  template <class F> bool provide_safe(const char*, F) { return true; }
  template <class... A> void notify(const char*, A&&...) {}
};
extern BridgeStub Bridge;
struct MonitorStub : public Print { bool begin(unsigned long = 0) { return true; } };
extern MonitorStub Monitor;
