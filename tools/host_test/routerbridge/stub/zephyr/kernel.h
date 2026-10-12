// Single-threaded host stand-in for the Zephyr kernel calls Arduino_RouterBridge's bridge.h
// makes (RouterBridge host test, run.sh). The test calls update() and the loop hook
// directly, so the bridge thread is never started and a mutex is a flag.
#pragma once
#include <cstddef>
#include <cstdint>
#include <cstdlib>
struct k_mutex {
  int locked = 0;
};
struct k_thread {
  int dummy = 0;
};
typedef struct k_thread* k_tid_t;
typedef uint8_t k_thread_stack_t;
typedef struct {
  int64_t ticks;
} k_timeout_t;
#define K_FOREVER (k_timeout_t{-1})
#define K_NO_WAIT (k_timeout_t{0})
#define K_MSEC(ms) (k_timeout_t{(int64_t)(ms)})
inline int k_mutex_init(k_mutex* m) {
  m->locked = 0;
  return 0;
}
inline int k_mutex_lock(k_mutex* m, k_timeout_t) {
  if (m->locked) return -11;         // -EAGAIN: never expected single-threaded
  m->locked = 1;
  return 0;
}
inline int k_mutex_unlock(k_mutex* m) {
  m->locked = 0;
  return 0;
}
inline int32_t k_msleep(int32_t) { return 0; }
inline void k_yield() {}
extern size_t g_stack_alloc_size;     // what begin() asked for (bridge_safe_test.cpp)
inline k_thread_stack_t* k_thread_stack_alloc(size_t size, int) {
  g_stack_alloc_size = size;
  return nullptr;
}
inline k_tid_t k_thread_create(k_thread* t, k_thread_stack_t*, size_t, void (*)(void*, void*, void*), void*, void*,
                               void*, int, uint32_t, k_timeout_t) {
  return t;                           // never started
}
inline int k_thread_name_set(k_tid_t, const char*) { return 0; }
