// =============================================================================
//  Single-producer / single-consumer byte FIFO, lock-free: one thread pushes, one
//  (possibly other) thread pops. Used by UartPump (uart_pump.h) between its thread and
//  the loop. N is a power of two, at most 32768. head_ and tail_ are free-running
//  32-bit counters, each written by one side only; the GCC __atomic builtins give the
//  acquire/release ordering on the MCU and on the host alike (no library needed).
//  Host-tested: tools/host_test/test_byte_fifo.cpp.
// =============================================================================
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace vgq {

template <uint32_t N>
class ByteFifo {
  static_assert(N >= 2 && N <= 32768 && (N & (N - 1)) == 0, "N must be a power of two <= 32768");

 public:
  // Either side: octets queued now (exact for the calling side's own view).
  uint32_t size() const { return load(head_) - load(tail_); }
  uint32_t space() const { return N - size(); }

  // ---- producer side ----
  bool push(uint8_t b) {
    const uint32_t h = head_;
    if (h - load(tail_) >= N) return false;
    buf_[h & (N - 1)] = b;
    store(head_, h + 1);
    return true;
  }
  // All or nothing: false, and nothing queued, when n octets do not fit.
  bool push_all(const uint8_t* p, size_t n) {
    const uint32_t h = head_;
    if (p == nullptr || n > N - (h - load(tail_))) return false;
    for (size_t i = 0; i < n; ++i) buf_[(h + (uint32_t)i) & (N - 1)] = p[i];
    store(head_, h + (uint32_t)n);
    return true;
  }

  // ---- consumer side ----
  bool pop(uint8_t& b) {
    const uint32_t t = tail_;
    if (load(head_) == t) return false;
    b = buf_[t & (N - 1)];
    store(tail_, t + 1);
    return true;
  }
  // Up to max octets into p; returns how many.
  size_t pop_some(uint8_t* p, size_t max) {
    const uint32_t t = tail_;
    const uint32_t avail = load(head_) - t;
    const uint32_t k = avail < max ? avail : (uint32_t)max;
    for (uint32_t i = 0; i < k; ++i) p[i] = buf_[(t + i) & (N - 1)];
    store(tail_, t + k);
    return k;
  }
  // Discards everything queued so far.
  void clear() { store(tail_, load(head_)); }

 private:
  static uint32_t load(const uint32_t& v) { return __atomic_load_n(&v, __ATOMIC_ACQUIRE); }
  static void store(uint32_t& v, uint32_t x) { __atomic_store_n(&v, x, __ATOMIC_RELEASE); }

  uint8_t buf_[N];
  uint32_t head_ = 0;   // written by the producer only
  uint32_t tail_ = 0;   // written by the consumer only
};

}  // namespace vgq
