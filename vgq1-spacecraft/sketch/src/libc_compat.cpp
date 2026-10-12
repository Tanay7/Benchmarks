// =============================================================================
//  libc_compat.cpp - C library glue for older arduino:zephyr cores
//
//  arduino:zephyr up to 0.90.0 (Zephyr SDK 0.16.8 toolchain, GCC 12.2) links a sketch
//  against the toolchain's newlib-nano libm (platform.txt: --specs=nano.specs ... -lm),
//  while the loader firmware's C library is picolibc. The newlib-nano wrappers of expf,
//  logf, log10f, powf, sqrtf (src/fsw/spectral.cpp, src/drivers/radio.cpp, ...) set
//  errno through __errno(), which neither those libraries nor the loader's export
//  table provide, so the build stops at the core's LLEXT check link:
//    wf_exp.c:(.text.expf+0x34): undefined reference to `__errno'
//  This definition gives the wrappers somewhere to write. Nothing in the flight
//  software reads errno. From 1.0.0 the core links picolibc's libm, which does not use
//  __errno, so nothing references this definition there; it is weak, so a C library
//  that does define __errno takes precedence.
// =============================================================================
#if defined(ARDUINO_ARCH_ZEPHYR) && !defined(VGQ_HOST_BUILD)

extern "C" __attribute__((weak)) int* __errno(void) {
  static int libm_errno;
  return &libm_errno;
}

#endif
