// =============================================================================
//  VGQ-1 - spacecraft flight software v3, Arduino UNO Q (STM32U585 MCU)
//
//  A CCSDS deep-space-style telemetry & telecommand demonstrator:
//    sensors -> Space Packets (133.0-B-2) -> VC0..3 multiplexing -> TM Transfer
//    Frames (132.0-B-3) or USLP frames (732.1-B-2) -> SDLS AE (355.0-B-2) ->
//    RS(255,223) + randomizer + ASM (131.0-B) -> radio
//    uplink: CLTU (BCH, 231.0-B-4) -> TC / USLP frame -> SDLS -> COP-1 / FARM-1
//    (232.1-B-2) -> command dispatch, stored sequences; CFDP class 1 (727.0-B-5)
//  Radio: EBYTE E22-400T33D by default (config.h VGQ_RADIO; E22-400T37S and an
//  SX1268 / SX1262 module on SPI are build options).
//
//  Build/deploy with Arduino App Lab (the App folder contains this sketch and the
//  Linux-side EGSE in python/). See docs/07_Operations_Procedures.md.
//  Wiring: docs/03_Hardware_Integration.md. Protocol: docs/04_Space_Data_Link_ICD.md
// =============================================================================
#include <Arduino_RouterBridge.h>
#include "src/fsw/flight.h"

static vgq::Flight g_fsw;
// Names of the Router Bridge registrations that failed (EVR 0x0005 after boot).
static char g_bridge_failures[64];

static void note_failure(const char* name) {
  const size_t used = strlen(g_bridge_failures);
  const size_t need = strlen(name) + (used ? 1u : 0u);
  if (used + need >= sizeof g_bridge_failures) return;          // bounded: keep what fits
  if (used) g_bridge_failures[used] = ' ';
  memcpy(g_bridge_failures + used + (used ? 1u : 0u), name, strlen(name) + 1u);
}

// --- EGSE (Linux) -> MCU services (PB-4.1). The sketch is built with the bundled,
//     patched Arduino_RouterBridge 0.4.3 (libraries/Arduino_RouterBridge, written by
//     tools/routerbridge.py, contract 41): it runs provide_safe handlers only from
//     update_safe() in __loopHook, on the loop thread, and gives its "bridge" thread a
//     1536-octet stack. The stock 0.4.3 also ran them on that thread (priority 5,
//     500-octet stack; see src/fsw/rpc_mailbox.h). Defence in depth, each handler
//     still goes through Flight's mailbox, which executes at once on the loop thread
//     and otherwise waits for the loop; nothing here may use a large local or block
//     for long. Names and signatures are unchanged (the Linux side is not affected).
static bool rpc_set_partition(int p) {          // SCLK partition = SDLS TM epoch
  return g_fsw.rpc_set_partition(p);
}
static bool rpc_set_sdls_sn(int sn) {           // restores the SDLS TC anti-replay state
  return g_fsw.rpc_set_sdls_sn(sn);
}
static bool rpc_hardline_cltu(String hex) {     // bench "umbilical" commanding
  return g_fsw.rpc_hardline_cltu(hex.c_str(), hex.length());
}
static int rpc_cfdp_pdu(String hex) {           // EGSE-built CFDP PDU for the relay (VC3)
  return g_fsw.rpc_cfdp_pdu(hex.c_str(), hex.length());
}

void setup() {
  // Setup-only blocking calls (PB-0.4): Bridge.begin() waits for the Linux-side
  // arduino-router (a cold power-up therefore waits for Linux to boot), and every
  // provide_safe() waits until the router has registered the name.
  g_fsw.rpc_begin();                    // the mailbox exists before any handler can run
  if (!Bridge.begin()) note_failure("begin");
#if VGQ_DEBUG_MONITOR
  // Bench debugging only: Monitor writes can block the flight loop (config.h).
  if (!Monitor.begin()) note_failure("monitor");
#endif
  if (!Bridge.provide_safe("set_partition", rpc_set_partition)) note_failure("set_partition");
  if (!Bridge.provide_safe("set_sdls_sn", rpc_set_sdls_sn)) note_failure("set_sdls_sn");
  if (!Bridge.provide_safe("hl_cltu", rpc_hardline_cltu)) note_failure("hl_cltu");
  if (!Bridge.provide_safe("cfdp_pdu", rpc_cfdp_pdu)) note_failure("cfdp_pdu");
  g_fsw.setup(g_bridge_failures);
}

void loop() {
  g_fsw.loop();
}
