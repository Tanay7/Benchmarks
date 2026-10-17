// SPI stub for host type checking only: the declarations of ArduinoCore-API 1.5.2
// api/HardwareSPI.h and ArduinoCore-zephyr libraries/SPI/SPI.h (class shapes and
// signatures; no behaviour).
#pragma once
#include "Arduino.h"

namespace arduino {
typedef enum {
  SPI_MODE0 = 0,
  SPI_MODE1 = 1,
  SPI_MODE2 = 2,
  SPI_MODE3 = 3,
} SPIMode;
typedef enum {
  SPI_CONTROLLER = 0,
  SPI_PERIPHERAL = 1,
} SPIBusMode;

class SPISettings {
 public:
  SPISettings(uint32_t clock, BitOrder order, SPIMode mode, SPIBusMode bus = SPI_CONTROLLER)
      : clockFreq(clock), dataMode(mode), bitOrder(order), busMode(bus) {}
  SPISettings(uint32_t clock, BitOrder order, int mode, SPIBusMode bus = SPI_CONTROLLER)
      : clockFreq(clock), dataMode((SPIMode)mode), bitOrder(order), busMode(bus) {}
  SPISettings() : clockFreq(4000000), dataMode(SPI_MODE0), bitOrder(MSBFIRST), busMode(SPI_CONTROLLER) {}
  uint32_t getClockFreq() const { return clockFreq; }
  SPIMode getDataMode() const { return dataMode; }
  BitOrder getBitOrder() const { return bitOrder; }
  SPIBusMode getBusMode() const { return busMode; }

 private:
  uint32_t clockFreq;
  SPIMode dataMode;
  BitOrder bitOrder;
  SPIBusMode busMode;
};

class HardwareSPI {
 public:
  virtual ~HardwareSPI() {}
  virtual uint8_t transfer(uint8_t data) = 0;
  virtual uint16_t transfer16(uint16_t data) = 0;
  virtual void transfer(void* buf, size_t count) = 0;
  virtual void usingInterrupt(int interruptNumber) = 0;
  virtual void notUsingInterrupt(int interruptNumber) = 0;
  virtual void beginTransaction(SPISettings settings) = 0;
  virtual void endTransaction(void) = 0;
  virtual void attachInterrupt() = 0;
  virtual void detachInterrupt() = 0;
  virtual void begin() = 0;
  virtual void end() = 0;
};
typedef HardwareSPI SPIClass;

class ZephyrSPI : public HardwareSPI {
 public:
  uint8_t transfer(uint8_t data) override;
  uint16_t transfer16(uint16_t data) override;
  void transfer(void* buf, size_t count) override;
  void usingInterrupt(int interruptNumber) override;
  void notUsingInterrupt(int interruptNumber) override;
  void beginTransaction(SPISettings settings) override;
  void endTransaction(void) override;
  void attachInterrupt() override;
  void detachInterrupt() override;
  void begin() override;
  void end() override;
};
}  // namespace arduino

// spis = <&spi2>, <&spi3> in the UNO Q variant overlay.
extern arduino::ZephyrSPI SPI;
extern arduino::ZephyrSPI SPI1;

using arduino::SPI_MODE0;
using arduino::SPI_MODE1;
using arduino::SPI_MODE2;
using arduino::SPI_MODE3;
using arduino::SPISettings;
