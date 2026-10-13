// =============================================================================
//  ByteFifo (drivers/byte_fifo.h): the single-producer / single-consumer FIFO between
//  the UART pump thread and the loop on cores with 64-octet Serial rings.
//  Boundaries (empty, full, all-or-nothing push_all, pop_some, wrap-around), then two
//  real threads streaming 4 MiB through a small FIFO: every octet arrives once, in order.
// =============================================================================
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <thread>

#include "drivers/byte_fifo.h"
#include "host_test.h"

using namespace vgq;

HOST_TEST(byte_fifo) {
  (void)out;
  {
    ByteFifo<8> f;
    uint8_t b = 0;
    CHECK(f.size() == 0 && f.space() == 8 && !f.pop(b), "FIFO: empty at start, pop fails");
    bool all = true;
    for (uint8_t i = 0; i < 8; ++i) all = all && f.push(i);
    CHECK(all && f.size() == 8 && f.space() == 0 && !f.push(99), "FIFO: 8 of 8 octets, the 9th refused");
    bool order = true;
    for (uint8_t i = 0; i < 8; ++i) order = order && f.pop(b) && b == i;
    CHECK(order && f.size() == 0, "FIFO: popped in order, empty again");
  }
  {
    ByteFifo<8> f;
    const uint8_t five[5] = {1, 2, 3, 4, 5};
    CHECK(f.push_all(five, 5) && f.size() == 5, "push_all: 5 octets queued");
    CHECK(!f.push_all(five, 4) && f.size() == 5, "push_all: 4 more do not fit - nothing queued");
    CHECK(f.push_all(five, 3) && f.size() == 8, "push_all: 3 more fill it exactly");
    CHECK(!f.push_all(nullptr, 1) && f.size() == 8, "push_all: null pointer refused");
    uint8_t got[16] = {0};
    const size_t k = f.pop_some(got, 6);
    CHECK(k == 6 && got[0] == 1 && got[4] == 5 && got[5] == 1 && f.size() == 2, "pop_some: 6 of 8 in order");
    f.clear();
    CHECK(f.size() == 0 && f.space() == 8, "clear: empty");
  }
  {
    // Indices wrap many times around the buffer.
    ByteFifo<16> f;
    bool ok = true;
    uint8_t chunk[7], got[7];
    uint32_t n_in = 0, n_out = 0;
    for (int round = 0; round < 10000 && ok; ++round) {
      for (uint8_t i = 0; i < 7; ++i) chunk[i] = (uint8_t)(n_in + i);
      if (f.push_all(chunk, 7)) n_in += 7;
      const size_t k = f.pop_some(got, 5);
      for (size_t i = 0; i < k; ++i) ok = ok && got[i] == (uint8_t)(n_out + i);
      n_out += (uint32_t)k;
    }
    CHECK(ok && n_in - n_out == f.size(), "wrap-around: 10000 rounds, every octet in order");
  }
  {
    // Two threads, 4 MiB through 64 octets: no loss, no duplicate, order kept.
    static ByteFifo<64> f;
    const uint32_t kTotal = 4u << 20;
    std::atomic<bool> bad{false};
    std::thread consumer([&] {
      uint32_t next = 0;
      uint8_t buf[13];
      while (next < kTotal) {
        const size_t k = f.pop_some(buf, sizeof buf);
        for (size_t i = 0; i < k; ++i) {
          if (buf[i] != (uint8_t)(next * 2654435761u >> 24)) bad = true;
          ++next;
        }
        if (k == 0) std::this_thread::yield();
      }
    });
    uint32_t sent = 0;
    while (sent < kTotal) {
      if (f.push((uint8_t)(sent * 2654435761u >> 24))) {
        ++sent;
      } else {
        std::this_thread::yield();
      }
    }
    consumer.join();
    CHECK(!bad && f.size() == 0, "two threads: 4 MiB through a 64-octet FIFO, every octet once and in order");
  }
}
