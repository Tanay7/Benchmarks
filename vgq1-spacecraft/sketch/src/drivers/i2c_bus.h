// =============================================================================
//  I2C bus access with error accounting and bus recovery (the FDIR "I2C_BUS"
//  response). All instruments have distinct addresses, so there is no
//  multiplexer: one bus on the UNO Q header pins (Wire2 = I2C3, A4 SDA / A5 SCL,
//  3.3 V) carries everything; only the AS7265X may instead sit on the Qwiic connector
//  (Wire1 = I2C4, a second I2cBus without GPIO bus clear: its pins are not Arduino pins).
//
//  Bus clear (AS-T6, Phase B spec F-03). A target that was interrupted in the
//  middle of a read (MCU reset, glitch, a hung AS72651) can hold SDA low for ever;
//  re-initialising the controller does not help, because only clock pulses make
//  the target finish its byte. bus_clear() therefore:
//    1. calls Wire end(); on ArduinoCore-zephyr that only unregisters a target - its
//       device_deinit() returns -ENOTSUP because the STM32 I2C driver registers no
//       deinit hook (i2c_stm32.c I2C_DEVICE_DT_INST_DEFINE, kernel/device.c) - so I2C3
//       stays enabled; pinMode() below takes the pins over as GPIOs regardless,
//    2. if SDA reads LOW, sends up to kI2cClearMaxPulses SCL pulses with
//       kI2cClearHalfPeriodUs half periods, stopping as soon as SDA is released,
//       and checks that SCL really rose after every pulse ("SCL stuck low" ends it),
//    3. with SCL released (high), pulls SDA low and releases it again: a START
//       followed by a STOP, which returns every target to idle,
//    4. re-muxes the pins to the I2C controller (Wire begin(), which re-applies
//       the DEFAULT pinctrl state, ArduinoCore-zephyr Wire.cpp) and sets the clock.
//       The setClock() is LOAD-BEARING: i2c_configure -> i2c_stm32_runtime_configure ->
//       LL_I2C_Disable clears PE, the software reset of the I2C block (RM0456 I2C_CR1
//       PE) - the only controller reset in this sequence.
//  Open drain is EMULATED, because ArduinoCore-zephyr pinMode() implements only
//  INPUT, INPUT_PULLUP, INPUT_PULLDOWN and OUTPUT (= GPIO_OUTPUT_LOW) and silently
//  ignores OUTPUT_OPENDRAIN (wiring_digital.cpp): a line is RELEASED with
//  pinMode(pin, INPUT) - the bus pull-ups raise it - and DRIVEN LOW with
//  pinMode(pin, OUTPUT), which configures and drives low at once. digitalWrite(HIGH)
//  is never used on SDA/SCL, so the MCU never drives a bus line high against a target.
//  Bound: <= 9 x 2 half periods of ~6 us + START/STOP, about 0.12 ms plus begin()/setClock().
// =============================================================================
#pragma once
#include <Arduino.h>
#include <Wire.h>

namespace vgq {

class I2cBus {
 public:
  // Starts the controller. With both GPIO numbers (>= 0) a bus clear runs first, so a
  // target left mid-transfer by the previous run cannot hold the bus; begin_clear_ok()
  // reports its result. -1 skips it (no GPIO access).
  void begin(TwoWire& w, uint32_t clock_hz, int pin_sda = -1, int pin_scl = -1);
  bool begin_clear_ok() const { return begin_clear_ok_; }
  bool probe(uint8_t addr);
  bool write8(uint8_t addr, uint8_t reg, uint8_t val);
  bool write(uint8_t addr, uint8_t reg, const uint8_t* buf, size_t n);
  bool write_raw(uint8_t addr, const uint8_t* buf, size_t n);   // no register pointer
  bool read(uint8_t addr, uint8_t reg, uint8_t* buf, size_t n);
  bool read_raw(uint8_t addr, uint8_t* buf, size_t n);          // no register pointer write
  // FDIR response: bus clear (when the pins are known) and controller re-initialisation.
  // Returns false when SDA is still low at the end (or SCL was stuck low): the caller
  // raises EVR 0x060A. Resets the consecutive-error count either way.
  bool recover();
  uint32_t errors() const { return errors_; }
  uint8_t consecutive_errors() const { return consec_; }
  uint32_t recoveries() const { return recoveries_; }

 private:
  void note(bool ok);
  bool bus_clear();              // steps 2-3 of the header; the controller must be ended
  TwoWire* w_ = nullptr;
  uint32_t clock_ = 100000;
  int sda_ = -1, scl_ = -1;
  uint32_t errors_ = 0;
  uint32_t recoveries_ = 0;
  uint8_t consec_ = 0;
  bool begin_clear_ok_ = true;
};

}  // namespace vgq
