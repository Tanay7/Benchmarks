// Test double of <zephyr/sys/atomic.h> for the station harness (run.sh): sequentially
// consistent GCC builtins, as Zephyr's ATOMIC_OPERATIONS_BUILTIN implementation uses, so
// ThreadSanitizer sees the same synchronisation as the target code relies on.
#pragma once
typedef long atomic_t;
typedef atomic_t atomic_val_t;
#define ATOMIC_INIT(i) (i)
static inline bool atomic_cas(atomic_t* t, atomic_val_t o, atomic_val_t n) {
  return __atomic_compare_exchange_n(t, &o, n, false, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
}
static inline atomic_val_t atomic_get(const atomic_t* t) { return __atomic_load_n(t, __ATOMIC_SEQ_CST); }
static inline atomic_val_t atomic_set(atomic_t* t, atomic_val_t v) { return __atomic_exchange_n(t, v, __ATOMIC_SEQ_CST); }
