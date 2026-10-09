// Minimal Arduino-API stub for HOST SYNTAX/TYPE CHECKING of the firmware.
// It is NOT a simulator: functions are inert. Used by tools/host_test/check_firmware.sh.
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdio.h>
#include <math.h>
#include <string>
#define HIGH 1
#define LOW 0
#define INPUT 0
#define OUTPUT 1
#define INPUT_PULLUP 2
enum { A0 = 14, A1, A2, A3, A4, A5 };
typedef uint8_t byte;
typedef bool boolean;
inline unsigned long millis() { return 0; }
inline unsigned long micros() { return 0; }
inline void delay(unsigned long) {}
inline void delayMicroseconds(unsigned int) {}
inline void pinMode(int, int) {}
inline void digitalWrite(int, int) {}
inline int digitalRead(int) { return 1; }
inline int analogRead(int) { return 0; }
inline void analogReadResolution(int) {}
inline void noInterrupts() {}
inline void interrupts() {}
inline void yield() {}
template <class T, class L> inline auto min(const T& a, const L& b) -> decltype(a < b ? a : b) { return a < b ? a : b; }
template <class T, class L> inline auto max(const T& a, const L& b) -> decltype(a < b ? b : a) { return a < b ? b : a; }
class String {
 public:
  String() {}
  String(const char* s) : s_(s ? s : "") {}
  String(int v) : s_(std::to_string(v)) {}
  String(float v, int d = 2) { char b[32]; snprintf(b, sizeof b, "%.*f", d, v); s_ = b; }
  void reserve(size_t n) { s_.reserve(n); }
  String& operator+=(char c) { s_ += c; return *this; }
  String& operator+=(const char* c) { s_ += c; return *this; }
  String& operator+=(const String& c) { s_ += c.s_; return *this; }
  friend String operator+(const String& a, const String& b) { String r(a); r += b; return r; }
  size_t length() const { return s_.size(); }
  char operator[](size_t i) const { return s_[i]; }
  const char* c_str() const { return s_.c_str(); }
 private:
  std::string s_;
};
class Print {
 public:
  virtual ~Print() {}
  virtual size_t write(uint8_t) { return 1; }
  virtual size_t write(const uint8_t*, size_t n) { return n; }
  size_t write(const char* s) { return strlen(s); }
  size_t print(const char*) { return 0; }
  size_t print(const String&) { return 0; }
  size_t print(int, int = 10) { return 0; }
  size_t println(const char* = "") { return 0; }
  size_t println(const String&) { return 0; }
  size_t println(int, int = 10) { return 0; }
};
class Stream : public Print {
 public:
  virtual int available() { return 0; }
  virtual int read() { return -1; }
  virtual int peek() { return -1; }
  size_t readBytes(uint8_t* b, size_t n) { (void)b; return n; }
  size_t readBytes(char* b, size_t n) { (void)b; return n; }
  void setTimeout(unsigned long) {}
};
class HardwareSerial : public Stream {
 public:
  void begin(unsigned long) {}
  void end() {}
  void flush() {}
  using Print::write;
  operator bool() const { return true; }
};
extern HardwareSerial Serial, Serial1, Serial2, Serial3;
#define __ARM_ARCH 8
inline void NVIC_SystemReset() {}
