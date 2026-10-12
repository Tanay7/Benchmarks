// Shared state of the station harness (run.sh): the pin table written by digitalWrite()
// (with the writing thread), the fake SX126x behind the SPI (runtime.cpp) and the check
// counter of the test programs.
#pragma once
#include <stdint.h>
#include <stdio.h>
#include <map>
#include <mutex>
#include <vector>
#include <zephyr/kernel.h>

struct PinWrite {
  int pin, level;
  k_tid_t thread;
};
struct FakePins {
  std::mutex m;
  std::map<int, int> level;
  std::vector<PinWrite> writes;
};
// The status the fake SX126x returns for GetIrqStatus / GetRxBufferStatus /
// GetPacketStatus / ReadRegister / ReadBuffer (runtime.cpp ZephyrSPI::transfer).
struct FakeSx {
  uint16_t irq = 0;
  uint8_t rx_len = 0, rx_off = 0, pkt[3] = {100, 20, 100}, payload[256] = {};
  std::map<uint16_t, uint8_t> regs;
  std::map<uint8_t, int> ops;            // SPI opcode -> commands seen
};
extern FakePins g_pins;
extern FakeSx g_fsx;

inline int g_failures = 0;
#define CHECK(c, what)                    \
  do {                                    \
    if (c) {                              \
      printf("ok   %s\n", what);          \
    } else {                              \
      printf("FAIL %s\n", what);          \
      ++g_failures;                       \
    }                                     \
  } while (0)
