// =============================================================================
//  UART pump for arduino:zephyr cores with 64-octet Serial rings (before 1.0.0: core
//  commit 40abc1f7 "Serial: increase default ringbuffer size to 1024", 2026-08-18;
//  config.h VGQ_SMALL_SERIAL_RINGS). The App Lab release that pins the board's core
//  below 1.0.0 builds the flight sketch with 0.90.0, so it must run on those rings.
//
//  A thread above the loop's priority (kUartPumpPriority; the loop runs at
//  CONFIG_MAIN_THREAD_PRIORITY 14, the bridge thread at 5) moves octets between the
//  core's rings and two FIFOs of its own every kUartPumpPeriodMs:
//    * receive: everything the 64-octet ring holds goes into a 1024-octet FIFO, so a long
//      loop pass loses nothing (the ring alone overflows after 67 ms at 9600 bit/s);
//    * transmit: a whole radio frame is queued at once and never blocks the loop
//      (ZephyrSerial::write() would wait until all but 64 octets are out: ~180 ms for a
//      240-octet frame). The ring is topped up every ~1 ms while it holds 67 ms of data,
//      so a frame's UART stream stays continuous (one E22 packet, no idle gap).
//  Only built with small rings; with 1024-octet rings the E22 driver uses the Serial
//  object directly, as before.
//
//  Threads: after begin() the pump thread is the only one that reads or writes the
//  Serial object; the loop uses the FIFOs (single producer / single consumer each,
//  byte_fifo.h) and, in flush(), availableForWrite() (semaphore-protected in the core).
//  Stack: kUartPumpStackBytes from the dynamic-thread pool (CONFIG_DYNAMIC_THREAD_STACK_SIZE
//  1024, 2 slots; the bridge thread takes its stack from the heap, docs/22).
// =============================================================================
#pragma once
#include "../config.h"

#if VGQ_SMALL_SERIAL_RINGS
#include <Arduino.h>
#include <zephyr/kernel.h>

#include "byte_fifo.h"

namespace vgq {

class UartPump {
 public:
  // Opens the port at `baud` and starts the thread (once; later calls only check that
  // it runs). false: no thread (no stack) - the caller then uses the Serial directly.
  bool begin(HardwareSerial& s, unsigned long baud);
  bool running() const { return tid_ != nullptr; }

  // ---- loop side ----
  int available() const { return (int)rx_.size(); }
  int read() {
    uint8_t b;
    return rx_.pop(b) ? (int)b : -1;
  }
  void drain_rx() { rx_.clear(); }
  // All or nothing, never blocks: n when the octets were queued, 0 otherwise.
  size_t write(const uint8_t* p, size_t n) { return tx_.push_all(p, n) ? n : 0; }
  // Waits until every queued octet has left the UART, at most timeout_ms (true = done).
  bool flush(uint32_t timeout_ms);
  // Octets lost because the receive FIFO was full (the loop did not read for ~1 s).
  uint32_t rx_overflows() const { return __atomic_load_n(&rx_overflows_, __ATOMIC_RELAXED); }

 private:
  static void entry(void* self, void* p2, void* p3);
  void run();

  HardwareSerial* s_ = nullptr;
  ByteFifo<1024> rx_;                     // producer: pump thread, consumer: loop
  ByteFifo<512> tx_;                      // producer: loop, consumer: pump thread
  struct k_thread thread_;
  k_tid_t tid_ = nullptr;
  uint32_t rx_overflows_ = 0;
};

}  // namespace vgq
#endif
