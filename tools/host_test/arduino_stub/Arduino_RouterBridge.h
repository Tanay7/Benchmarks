#pragma once
#include "Arduino.h"
// The real header includes these too (Arduino_RouterBridge 0.4.3 bridge.h:30-31).
#include <zephyr/kernel.h>
#include <zephyr/sys/atomic.h>
struct BridgeStub {
  bool begin() { return true; }
  template <class F> bool provide(const char*, F) { return true; }
  template <class F> bool provide_safe(const char*, F) { return true; }
  template <class... A> void notify(const char*, A&&...) {}
};
extern BridgeStub Bridge;
struct MonitorStub : public Print { bool begin(unsigned long = 0) { return true; } };
extern MonitorStub Monitor;

// ---- Router UART (WS2 addition) ------------------------------------------------------
// ArduinoCore-zephyr cores/arduino/zephyrSerial.h defines ARDUINO_ROUTER_SERIAL as the
// ZephyrSerial object of the board's arduino,router-serial node (UNO Q: LPUART1) and
// ZephyrSerial::availableForWrite() returns the free space of its 1024-octet TX ring.
// The flight checks that space before every notification (PB-0.4); modelled here.
#ifndef ARDUINO_ROUTER_SERIAL
class RouterSerialStub : public HardwareSerial {
 public:
  int availableForWrite() { return 1024; }
};
extern RouterSerialStub RouterSerial;
#define ARDUINO_ROUTER_SERIAL RouterSerial
#endif
