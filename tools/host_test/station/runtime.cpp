// Host runtime of the station harness (run.sh): the Arduino core functions on real time,
// a pin table (digitalWrite() records the writing thread, so a test can tell which thread
// drove a pin), the global objects the sketch uses, and a fake SX126x behind the SPI that
// answers the status reads the sketch's RadioLib calls make (opcodes per Semtech
// SX1261/2 datasheet rev 2.2 section 13).
#include <Arduino.h>
#include <SPI.h>
#include <Wire.h>
#include <Arduino_RouterBridge.h>
#include <chrono>
#include <map>
#include <mutex>
#include <thread>
#include "runtime.h"

BridgeRec Bridge;
HardwareSerial Serial, Serial1, Serial2, Serial3;
TwoWire Wire, Wire1, Wire2;
arduino::ZephyrSPI SPI, SPI1;
FakePins g_pins;
FakeSx g_fsx;

static const auto t0 = std::chrono::steady_clock::now();
unsigned long millis() {
  return static_cast<unsigned long>(
      std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - t0).count());
}
unsigned long micros() {
  return static_cast<unsigned long>(
      std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now() - t0).count());
}
void delay(unsigned long ms) { std::this_thread::sleep_for(std::chrono::milliseconds(ms)); }
void delayMicroseconds(unsigned int us) { std::this_thread::sleep_for(std::chrono::microseconds(us)); }
void yield() {}
void pinMode(pin_size_t, PinMode) {}
void digitalWrite(pin_size_t p, PinStatus s) {
  std::lock_guard<std::mutex> lock(g_pins.m);
  g_pins.level[p] = s;
  g_pins.writes.push_back({p, static_cast<int>(s), k_current_get()});
}
PinStatus digitalRead(pin_size_t p) {
  std::lock_guard<std::mutex> lock(g_pins.m);
  auto it = g_pins.level.find(p);
  return it == g_pins.level.end() ? LOW : static_cast<PinStatus>(it->second);
}
int digitalPinToInterrupt(pin_size_t p) { return p; }
void attachInterrupt(pin_size_t, voidFuncPtr, PinStatus) {}
void detachInterrupt(pin_size_t) {}
void interrupts() {}
void noInterrupts() {}
int analogRead(pin_size_t) { return 0; }
void analogWrite(pin_size_t, int) {}
unsigned long pulseIn(uint8_t, uint8_t, unsigned long) { return 0; }
void tone(uint8_t, unsigned int, unsigned long) {}
void noTone(uint8_t) {}

namespace arduino {
// One SPI transaction = one SX126x command: octet 0 is the opcode, the rest arguments
// (NOP octets on reads). Index within the transaction, opcode and two address octets.
static size_t g_idx = 0;
static uint8_t g_op = 0, g_b1 = 0, g_b2 = 0;
uint8_t ZephyrSPI::transfer(uint8_t out) {
  const size_t i = g_idx++;
  if (i == 0) {
    g_op = out;
    ++g_fsx.ops[out];
  }
  if (i == 1) g_b1 = out;
  if (i == 2) g_b2 = out;
  const uint8_t status = 0x52;           // chip mode RX, no command error
  switch (g_op) {
    case 0x12:                           // GetIrqStatus: status, IRQ[15:8], IRQ[7:0]
      if (i == 2) return static_cast<uint8_t>(g_fsx.irq >> 8);
      if (i == 3) return static_cast<uint8_t>(g_fsx.irq);
      break;
    case 0x13:                           // GetRxBufferStatus: status, length, start offset
      if (i == 2) return g_fsx.rx_len;
      if (i == 3) return g_fsx.rx_off;
      break;
    case 0x14:                           // GetPacketStatus: RssiPkt, SnrPkt, SignalRssiPkt
      if (i >= 2 && i < 5) return g_fsx.pkt[i - 2];
      break;
    case 0x17:                           // GetDeviceErrors: none
      if (i >= 2) return 0;
      break;
    case 0x1D:                           // ReadRegister: address octets 1-2, NOP, data
      if (i >= 4) {
        const uint16_t a = static_cast<uint16_t>(((g_b1 << 8) | g_b2) + (i - 4));
        auto it = g_fsx.regs.find(a);
        return it == g_fsx.regs.end() ? 0 : it->second;
      }
      break;
    case 0x1E:                           // ReadBuffer: offset octet 1, NOP, data
      if (i >= 3) return g_fsx.payload[(g_b1 + i - 3) & 0xFF];
      break;
    default:
      break;
  }
  return status;
}
uint16_t ZephyrSPI::transfer16(uint16_t d) { return d; }
void ZephyrSPI::transfer(void*, size_t) {}
void ZephyrSPI::usingInterrupt(int) {}
void ZephyrSPI::notUsingInterrupt(int) {}
void ZephyrSPI::beginTransaction(SPISettings) { g_idx = 0; }
void ZephyrSPI::endTransaction() {}
void ZephyrSPI::attachInterrupt() {}
void ZephyrSPI::detachInterrupt() {}
void ZephyrSPI::begin() {}
void ZephyrSPI::end() {}
}  // namespace arduino
