// Minimal <zephyr/kernel.h> subset for HOST SYNTAX/TYPE CHECKING (check_firmware.sh),
// reached as on the target through Arduino_RouterBridge.h. Signatures follow Zephyr's
// include/zephyr/kernel.h of the arduino:zephyr core (llext-edk); the bodies are inert
// single-threaded stand-ins (a counter), not a scheduler. The real builds are the
// source of truth for the API.
#pragma once
#include <errno.h>
#include <stdint.h>
#include "sys/atomic.h"

typedef int64_t k_ticks_t;
typedef struct {
  k_ticks_t ticks;
} k_timeout_t;
#define K_NO_WAIT (k_timeout_t{0})
#define K_FOREVER (k_timeout_t{-1})
#define K_MSEC(ms) (k_timeout_t{(k_ticks_t)(ms)})

struct k_thread {
  int unused;
};
typedef struct k_thread* k_tid_t;
static inline k_tid_t k_current_get(void) {
  static struct k_thread main_thread;
  return &main_thread;
}
static inline void k_yield(void) {}
static inline int32_t k_msleep(int32_t ms) { return (void)ms, 0; }

struct k_sem {
  unsigned int count;
  unsigned int limit;
};
static inline int k_sem_init(struct k_sem* sem, unsigned int initial_count, unsigned int limit) {
  if (limit == 0 || initial_count > limit) return -EINVAL;
  sem->count = initial_count;
  sem->limit = limit;
  return 0;
}
// 0 when taken; -EBUSY for K_NO_WAIT and -EAGAIN after a timeout, as Zephyr returns.
static inline int k_sem_take(struct k_sem* sem, k_timeout_t timeout) {
  if (sem->count > 0) {
    --sem->count;
    return 0;
  }
  return timeout.ticks == 0 ? -EBUSY : -EAGAIN;
}
static inline void k_sem_give(struct k_sem* sem) {
  if (sem->count < sem->limit) ++sem->count;
}
static inline void k_sem_reset(struct k_sem* sem) { sem->count = 0; }
static inline unsigned int k_sem_count_get(struct k_sem* sem) { return sem->count; }
