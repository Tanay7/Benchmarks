// Host stand-ins for the Arduino core pieces that Arduino_RouterBridge's bridge.h and
// Arduino_RPClite's SerialTransport.h use (RouterBridge host test, run.sh): time, and the
// Stream / HardwareSerial shapes. The test replaces the transport, so nothing is read.
#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string>
inline unsigned long millis() { return 0; }
inline void delay(unsigned long) {}
class Stream {
 public:
  virtual ~Stream() = default;
  virtual int available() { return 0; }
  virtual size_t write(const uint8_t*, size_t n) { return n; }
  virtual size_t write(const char*) { return 0; }
  void setTimeout(unsigned long) {}
  size_t readBytes(uint8_t*, size_t) { return 0; }
};
class HardwareSerial : public Stream {
 public:
  void begin(unsigned long) {}
  explicit operator bool() const { return true; }
};
