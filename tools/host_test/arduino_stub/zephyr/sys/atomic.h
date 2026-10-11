// Minimal <zephyr/sys/atomic.h> subset for HOST SYNTAX/TYPE CHECKING (see ../kernel.h).
// Zephyr: typedef long atomic_t (sys/atomic_types.h); plain operations here.
#pragma once
#include <stdbool.h>

typedef long atomic_t;
typedef atomic_t atomic_val_t;
#define ATOMIC_INIT(i) (i)
static inline bool atomic_cas(atomic_t* target, atomic_val_t old_value, atomic_val_t new_value) {
  if (*target != old_value) return false;
  *target = new_value;
  return true;
}
static inline atomic_val_t atomic_get(const atomic_t* target) { return *target; }
static inline atomic_val_t atomic_set(atomic_t* target, atomic_val_t value) {
  const atomic_val_t old = *target;
  *target = value;
  return old;
}
