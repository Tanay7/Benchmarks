// Test double of the <zephyr/kernel.h> subset the station sketch uses, on REAL host threads
// (std::thread) for the station harness (run.sh): k_current_get() is per thread and k_sem
// blocks with timeouts, so the sketch's Bridge mailbox runs as on the target (a handler
// thread waits, the loop thread serves). Found before tools/host_test/arduino_stub's
// single-threaded version (include order in run.sh).
#pragma once
#include <errno.h>
#include <stdint.h>
#include <chrono>
#include <condition_variable>
#include <mutex>
#include "sys/atomic.h"

typedef struct {
  int64_t ms;
} k_timeout_t;
#define K_NO_WAIT (k_timeout_t{0})
#define K_FOREVER (k_timeout_t{-1})
#define K_MSEC(x) (k_timeout_t{(int64_t)(x)})

struct k_thread {
  int unused;
};
typedef k_thread* k_tid_t;
inline k_tid_t k_current_get() {
  thread_local k_thread self;
  return &self;
}

struct k_sem {
  std::mutex m;
  std::condition_variable cv;
  unsigned count = 0, limit = 1;
};
inline int k_sem_init(k_sem* s, unsigned initial, unsigned limit) {
  s->count = initial;
  s->limit = limit;
  return 0;
}
// 0 when taken; -EBUSY for K_NO_WAIT and -EAGAIN after a timeout, as Zephyr returns.
inline int k_sem_take(k_sem* s, k_timeout_t t) {
  std::unique_lock<std::mutex> lock(s->m);
  auto ready = [&] { return s->count > 0; };
  if (t.ms < 0) {
    s->cv.wait(lock, ready);
  } else if (!s->cv.wait_for(lock, std::chrono::milliseconds(t.ms), ready)) {
    return t.ms == 0 ? -EBUSY : -EAGAIN;
  }
  --s->count;
  return 0;
}
inline void k_sem_give(k_sem* s) {
  {
    std::lock_guard<std::mutex> lock(s->m);
    if (s->count < s->limit) ++s->count;
  }
  s->cv.notify_all();
}
inline void k_yield() {}
