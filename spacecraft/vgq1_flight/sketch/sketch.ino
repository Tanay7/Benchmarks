// =============================================================================
//  VGQ-1 — spacecraft flight software, Arduino UNO Q (STM32U585 MCU)
//
//  A CCSDS-faithful deep-space telemetry & telecommand demonstrator:
//    sensors -> Space Packets -> VC multiplexing -> TM Transfer Frames ->
//    RS(255,223) + randomizer + ASM -> EBYTE E22-400T37S (433 MHz LoRa, 5 W)
//    uplink: CLTU (BCH) -> TC frames -> COP-1/FARM-1 -> command dispatch
//
//  Build/deploy with Arduino App Lab (the app folder contains this sketch and
//  the Linux-side EGSE in python/). See docs/07_Operations_Procedures.md.
//  Wiring: docs/03_Hardware_Integration.md. Protocol: docs/04_Space_Data_Link_ICD.md
// =============================================================================
#include <Arduino_RouterBridge.h>
#include "src/fsw/flight.h"

static vgq::Flight g_fsw;

// --- EGSE (Linux) -> MCU services. provide_safe => executed in the loop thread
//     between loop() iterations, so no locking is needed inside Flight.
static bool rpc_set_partition(int p) {
  g_fsw.egse_set_partition(p);
  return true;
}
static bool rpc_hardline_cltu(String hex) {   // bench "umbilical" commanding
  return g_fsw.egse_hardline_cltu(hex);
}

void setup() {
  // Bridge.begin() waits for the Linux-side arduino-router service; on a cold
  // power-up that means the MCU waits for Linux to boot before flying.
  Bridge.begin();
  Monitor.begin();
  Bridge.provide_safe("set_partition", rpc_set_partition);
  Bridge.provide_safe("hl_cltu", rpc_hardline_cltu);
  g_fsw.setup();
}

void loop() {
  g_fsw.loop();
}
