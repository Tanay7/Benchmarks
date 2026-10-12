// =============================================================================
//  UART pump for 64-octet Serial rings (see uart_pump.h).
// =============================================================================
#include "uart_pump.h"

#if VGQ_SMALL_SERIAL_RINGS

namespace vgq {

bool UartPump::begin(HardwareSerial& s, unsigned long baud) {
  if (tid_ != nullptr) return true;          // already running: the port stays open
  s_ = &s;
  s.begin(baud);
  k_thread_stack_t* const stack = k_thread_stack_alloc(kUartPumpStackBytes, 0);
  if (stack == nullptr) return false;
  tid_ = k_thread_create(&thread_, stack, kUartPumpStackBytes, &UartPump::entry, this, nullptr,
                         nullptr, kUartPumpPriority, 0, K_NO_WAIT);
  if (tid_ != nullptr) (void)k_thread_name_set(tid_, "uart_pump");
  return tid_ != nullptr;
}

bool UartPump::flush(uint32_t timeout_ms) {
  const uint32_t t0 = millis();
  // The FIFO first, then the core's ring (empty = availableForWrite() is the whole ring),
  // then one character time for the shift register (1.04 ms at 9600 bit/s).
  while (tx_.size() != 0 || s_->availableForWrite() < CONFIG_ARDUINO_API_SERIAL_BUFFER_SIZE) {
    if (millis() - t0 > timeout_ms) return false;
    delay(1);
  }
  delay(2);
  return true;
}

void UartPump::entry(void* self, void* p2, void* p3) {
  (void)p2;
  (void)p3;
  static_cast<UartPump*>(self)->run();
}

void UartPump::run() {
  uint8_t chunk[CONFIG_ARDUINO_API_SERIAL_BUFFER_SIZE];
  for (;;) {
    // Receive: everything the ring holds (at most one ring per pass).
    for (int n = s_->available(); n > 0; --n) {
      const int c = s_->read();
      if (c < 0) break;
      if (!rx_.push((uint8_t)c)) __atomic_fetch_add(&rx_overflows_, 1u, __ATOMIC_RELAXED);
    }
    // Transmit: top the ring up with no more than it takes, so write() never waits.
    const int room = s_->availableForWrite();
    if (room > 0) {
      const size_t k = tx_.pop_some(chunk, (size_t)room < sizeof chunk ? (size_t)room : sizeof chunk);
      if (k > 0) (void)s_->write(chunk, k);
    }
    k_msleep(kUartPumpPeriodMs);
  }
}

}  // namespace vgq
#endif
