// =============================================================================
//  Shared helpers for the host (PC) verification harness.
//
//  Every tools/host_test/test_*.cpp file registers its checks with HOST_TEST():
//
//      #include "host_test.h"
//      HOST_TEST(aes_fips197) {            // `out` = vector output directory
//        CHECK(..., "AES-256 FIPS-197 C.3");
//      }
//
//  The Makefile compiles every test_*.cpp; main() in host_test.cpp runs its own
//  checks and then every registered test, in registration (link) order.
// =============================================================================
#pragma once
#include <cstdint>
#include <cstdio>
#include <string>

namespace host_test {

int& failures();
std::string hex(const uint8_t* p, size_t n);
// Parses a hex string (whitespace ignored) into `out`; returns the octet count.
size_t unhex(const char* s, uint8_t* out, size_t cap);
// Deterministic PRNG (xorshift32) so vectors are reproducible.
uint32_t rnd();

using TestFn = void (*)(const std::string& out);
struct Registrar {
  Registrar(const char* name, TestFn fn);
};
void run_all(const std::string& out);

}  // namespace host_test

#define CHECK(cond, msg)                                                         \
  do {                                                                           \
    if (!(cond)) { std::printf("FAIL: %s\n", msg); ++host_test::failures(); }    \
    else         { std::printf("ok:   %s\n", msg); }                             \
  } while (0)

#define HOST_TEST(name)                                                          \
  static void host_test_##name(const std::string& out);                          \
  static host_test::Registrar host_test_reg_##name(#name, host_test_##name);     \
  static void host_test_##name(const std::string& out)
