// Single-threaded host stand-in for <zephyr/sys/atomic.h> (RouterBridge host test, run.sh).
#pragma once
typedef long atomic_t;
#define ATOMIC_INIT(i) (i)
inline bool atomic_cas(atomic_t* t, atomic_t o, atomic_t n) {
  if (*t != o) return false;
  *t = n;
  return true;
}
