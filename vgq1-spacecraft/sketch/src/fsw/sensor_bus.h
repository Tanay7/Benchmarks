// =============================================================================
//  The two sensor buses and their fault protection (D-73). Pure C++, header only,
//  host-tested (tools/host_test/test_sensor_bus.cpp); Flight wires it to the two
//  I2cBus objects (drivers/i2c_bus.h).
//
//  Roles (config.h NICLA_WIRE / MAG_WIRE):
//    SBUS_NICLA  Wire2 = I2C3, header A4 (SDA) / A5 (SCL), GPIO bus clear: the Nicla
//                Sense ME (0x2A, through its ESLOV connector, D-74)
//    SBUS_MAG    Wire1 = I2C4, the Qwiic socket: the boom RM3100s (0x20, optional 0x23),
//                so the long boom cable is isolated from the attitude reference;
//                controller re-initialisation only (i2c_bus.h)
//    The AS7265X (0x49) has no preferred bus: it is used on either without a warning (in the
//    owner's layout it is on the Qwiic socket and the RM3100s are wired to its I2C row).
//  Every address is distinct, so any instrument may sit on either bus: each is probed on
//  its preferred bus first and on the other one second (probe_preferred), and a Nicla or
//  RM3100 found only on the other bus is used with a WARN EVR naming the recommended bus.
//  Separation rule: the Nicla and an RM3100 on the SAME bus (sensor_buses_shared) are used,
//  with a WARN EVR "... not separated".
//
//  Fault protection per bus: its own recovery rate limit (RecoverLimiter), so a recovery
//  on one bus never holds back the other's. HK keeps one i2c_errors (the sum of both buses)
//  and one FDIR I2C_BUS flag (every FDIR bit is in use); the EVRs name the bus.
//  Instrument loss on the MAG bus: LossCounter (RM3100, kRm3100LossSamples failed samples
//  in a row); the Nicla Sense ME keeps its own canary (Flight::fdir_1hz).
// =============================================================================
#pragma once
#include <stdint.h>

namespace vgq {

enum SensorBus : uint8_t { SBUS_NICLA = 0, SBUS_MAG = 1 };
static const uint8_t kSensorBuses = 2;
static const uint8_t kSensorBusNone = 0xFF;          // probe_preferred(): found on neither bus

// Bus names as the EVRs print them (the AS7265X identity EVR 0x0706 uses the same two).
inline const char* sensor_bus_name(uint8_t b) {
  return b == SBUS_NICLA ? "A4/A5" : b == SBUS_MAG ? "Qwiic" : "none";
}
inline uint8_t sensor_bus_other(uint8_t b) { return b == SBUS_NICLA ? (uint8_t)SBUS_MAG : (uint8_t)SBUS_NICLA; }

// Separation rule (D-73): true when two instruments were both found and on the same bus
// (the Nicla Sense ME and an RM3100 must not be; kSensorBusNone = not found, never shared).
inline bool sensor_buses_shared(uint8_t a, uint8_t b) { return a != kSensorBusNone && a == b; }

// Preferred-then-alternate probing: try_bus(bus) attempts the instrument's begin() on one
// bus and returns true when it answered. Returns the bus that answered, or kSensorBusNone.
// At most two calls; the preferred bus always first.
template <class TryBus>
uint8_t probe_preferred(uint8_t preferred, TryBus try_bus) {
  if (try_bus(preferred)) return preferred;
  const uint8_t other = sensor_bus_other(preferred);
  return try_bus(other) ? other : kSensorBusNone;
}

// One bus's FDIR recovery rate limit: at most one recovery (bus clear and/or controller
// re-initialisation) per min_ms, counted from the last one on THIS bus.
struct RecoverLimiter {
  bool done_once = false;                  // last_ms valid
  uint32_t last_ms = 0;
  bool allowed(uint32_t now_ms, uint32_t min_ms) const { return !done_once || now_ms - last_ms >= min_ms; }
  void note(uint32_t now_ms) {
    done_once = true;
    last_ms = now_ms;
  }
};

// RM3100 loss detection (D-73): a unit that answered once is declared lost after this many
// consecutive failed samples (POLL refused, DRDY not seen within kMagCollectTimeoutMs, or
// the result read failed): its HK bit clears and the re-probe path takes over.
static const uint8_t kRm3100LossSamples = 5;

// Consecutive failed samples of one instrument. note() returns true exactly once, on the
// failure that reaches the limit; a good sample (or reset()) starts the count again.
struct LossCounter {
  uint8_t fails = 0;
  bool note(bool ok, uint8_t limit) {
    if (ok) {
      fails = 0;
      return false;
    }
    if (fails < 255u) ++fails;
    return fails == limit;
  }
  void reset() { fails = 0; }
};

}  // namespace vgq
