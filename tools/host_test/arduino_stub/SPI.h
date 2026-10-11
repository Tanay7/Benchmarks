// Minimal SPI stub (ArduinoCore-API HardwareSPI shape) for host type checking only.
#pragma once
#include "Arduino.h"
enum BitOrder { LSBFIRST = 0, MSBFIRST = 1 };
enum SPIMode { SPI_MODE0 = 0, SPI_MODE1 = 1, SPI_MODE2 = 2, SPI_MODE3 = 3 };
class SPISettings {
 public:
  SPISettings() {}
  SPISettings(uint32_t, BitOrder, SPIMode) {}
};
class SPIClass {
 public:
  virtual ~SPIClass() {}
  void begin() {}
  void end() {}
  void beginTransaction(SPISettings) {}
  void endTransaction() {}
  uint8_t transfer(uint8_t d) { return d; }
  uint16_t transfer16(uint16_t d) { return d; }
  void transfer(void*, size_t) {}
};
extern SPIClass SPI;
