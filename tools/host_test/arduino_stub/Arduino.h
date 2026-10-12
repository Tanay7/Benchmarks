// Minimal Arduino-API stub for HOST SYNTAX/TYPE CHECKING of the firmware.
// It is NOT a simulator: functions are declared or inert. Used by
// tools/host_test/check_firmware.sh (g++ -fsyntax-only).
//
// The pin / interrupt / timing API is declared exactly as the arduino:zephyr core
// exposes it to sketches and libraries: ArduinoCore-API 1.5.2 (api/Common.h, the
// version pinned by ArduinoCore-zephyr's zephyr/module.yml) declares the TYPED
// functions at global scope inside extern "C", and its int overloads of pinMode()
// and digitalWrite() live in namespace arduino (api/Compat.h), made visible to
// unqualified calls by the core's `using namespace arduino;`. A ::-qualified call
// therefore sees only the typed functions - the reason RadioLib's ArduinoHal needs
// its PinMode / PinStatus casts on a platform it does not know (VENTUNO Q), which a
// looser stub would hide. Classes (String, Print, Stream, serial) are simplified.
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdio.h>
#include <math.h>
#include <string>
#include <ctype.h>
#include <stdlib.h>

// ---- ArduinoCore-API 1.5.2 api/Common.h ----------------------------------------
extern "C" {
void yield(void);
typedef enum {
  LOW = 0,
  HIGH = 1,
  CHANGE = 2,
  FALLING = 3,
  RISING = 4,
} PinStatus;
typedef enum {
  INPUT = 0x0,
  OUTPUT = 0x1,
  INPUT_PULLUP = 0x2,
  INPUT_PULLDOWN = 0x3,
  OUTPUT_OPENDRAIN = 0x4,
} PinMode;
typedef enum {
  LSBFIRST = 0,
  MSBFIRST = 1,
} BitOrder;
typedef void (*voidFuncPtr)(void);
typedef void (*voidFuncPtrParam)(void*);
typedef bool boolean;
typedef uint8_t byte;
typedef uint16_t word;
typedef uint8_t pin_size_t;
void pinMode(pin_size_t pinNumber, PinMode pinMode);
void digitalWrite(pin_size_t pinNumber, PinStatus status);
PinStatus digitalRead(pin_size_t pinNumber);
int analogRead(pin_size_t pinNumber);
void analogWrite(pin_size_t pinNumber, int value);
unsigned long millis(void);
unsigned long micros(void);
void delay(unsigned long);
void delayMicroseconds(unsigned int us);
void attachInterrupt(pin_size_t interruptNumber, voidFuncPtr callback, PinStatus mode);
void detachInterrupt(pin_size_t interruptNumber);
// ArduinoCore-zephyr cores/arduino/Arduino.h
void interrupts(void);
void noInterrupts(void);
}
template <class T, class L> auto min(const T& a, const L& b) -> decltype((b < a) ? b : a) { return (b < a) ? b : a; }
template <class T, class L> auto max(const T& a, const L& b) -> decltype((b < a) ? b : a) { return (a < b) ? b : a; }
template <class T, class L, class H> inline T constrain(T x, L lo, H hi) { return x < (T)lo ? (T)lo : (x > (T)hi ? (T)hi : x); }
unsigned long pulseIn(uint8_t pin, uint8_t state, unsigned long timeout = 1000000L);
void tone(uint8_t pin, unsigned int frequency, unsigned long duration = 0);
void noTone(uint8_t pin);

// ---- ArduinoCore-API 1.5.2 api/Compat.h ----------------------------------------
namespace arduino {
inline void pinMode(pin_size_t pinNumber, int mode) { pinMode(pinNumber, (PinMode)mode); }
inline void digitalWrite(pin_size_t pinNumber, int status) { digitalWrite(pinNumber, (PinStatus)status); }
}  // namespace arduino

// ---- ArduinoCore-zephyr cores/arduino/Arduino.h (devicetree-generated parts) ----
enum analogPins { A0 = 14, A1, A2, A3, A4, A5 };
int digitalPinToInterrupt(pin_size_t pin);

#define DEC 10
#define HEX 16
#define OCT 8
#define BIN 2

// Program-memory helpers (flash and RAM share one address space on ARM).
#define PROGMEM
#define PGM_P const char*
#define pgm_read_byte(addr) (*(const uint8_t*)(addr))
#define pgm_read_dword(addr) (*(const uint32_t*)(addr))
class __FlashStringHelper;
#define F(s) (reinterpret_cast<const __FlashStringHelper*>(s))

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
  int availableForWrite() { return 64; }   // free octets of the TX ring (ZephyrSerial)
  using Print::write;
  operator bool() const { return true; }
};
extern HardwareSerial Serial, Serial1, Serial2, Serial3;
#define __ARM_ARCH 8
inline void NVIC_SystemReset() {}

// Allow namespace-less operations if Arduino.h is included (as the core does).
using namespace arduino;
