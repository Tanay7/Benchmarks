# VGQ-1 / DSS-Q1 v3 — PHASE B (integration) specification

Binding for every Phase B implementer. Read sections 0 and 8 first, then the sections your
workstream owns. Where this document and `contract_v3.md` differ, this document records the
conflict and its resolution (section 0.3); where it is silent, the contract applies; where both
are silent, follow the existing code's conventions. Cited as "§n" = contract_v3.md section n,
"PB-n" = this document. Repository: `/home/user/Benchmarks`. Do not run `git commit`/`git push`
(§0: the orchestrator commits).

---------------------------------------------------------------------------------------------------

## 0 Scope, baseline, conventions, conflict register

### 0.1 What Phase B delivers
Phase A/A2 built every module stand-alone (SDLS AE, USLP, CFDP, AS7265X + spectral, radio
abstraction, Nicla ARU, Rice, autonomy, ground products). Nothing is wired together yet:

* `spacecraft/vgq1_flight/sketch/src/fsw/flight.h/.cpp` is still the v2 executive (uses `E22`,
  `E22Config`, `NiclaEnv`, `Spectrometer`, v2 packets) and does not compile:
  `check_firmware.sh` FAILs `sketch.ino` and `src/fsw/flight.cpp` (verified 2026-10-11:
  `flight.h:79: error: 'E22' does not name a type`).
* `src/config.h`, `fsw/tlm_packets.*`, `fsw/commands.*` are v2 (HK 58, RF 38, SPEC 114, ENV 51,
  opcodes up to 0x16, `kE22Channel/kE22AirRate/kE22PowerCode`, `kAuxTimeoutMs`).
* `ground/dssq/gds.py`, `dictionary.py`, `decom.py`, `rxstats.py`, `config.py`, `sim.py`,
  `config/station.toml`, `display/*` are v2 (no SDLS TM, no USLP selection, no CFDP, no SPEC
  v3.1/SPECBURST/ENV v3, no AFC/TDM/XTCE/MAGCAL/sequences, CardKB #2 remnants).
* EGSE `spacecraft/vgq1_flight/python/main.py` has no CFDP relay and stores data in
  `python/egse_data` (contract 18 wants `<app>/data`).

Phase B integrates all of it on both ends, makes the ground runnable as an App Lab app, and
closes every follow-up owed to Phase B (PB-7).

### 0.2 Baseline (measured before Phase B, 2026-10-11)
* `make -C tools/host_test host_test && tools/host_test/host_test <tmp>` → `HOST TEST PASSED (0 failures)`.
* `cd ground && <scratchpad>/venv/bin/python -m pytest -q` → `358 passed, 1 skipped`
  (skip = `test_rice.py` needs `CCSDS121_TESTDATA`).
* `bash tools/host_test/check_firmware.sh` → every TU `ok` except the two FAIL lines above.
* `git status` clean.
Phase B must end with every PB-8.5 check green and no FAIL line.

### 0.3 Conflict register (source conflicts and the binding resolution)

| # | Conflict | Resolution | Reason |
|---|---|---|---|
| C-01 | HK 71 octets (§3.1) vs 78 (§24.1) | **78** (PB-1.3) | §24.1 is later and appends 6 fields totalling 7 octets (seq_next_dt_s is a u16): 71 → 78 |
| C-02 | SPEC 119 (§3.5) vs v3.1 129 (§13.4) | **129** | §13 supersedes §3.5 |
| C-03 | SPECBURST §13.5 (interleaved `{dt, raw[18]}`, no encoding) vs §28 (encoding field, dt array, payload) | **§28** layout; raw payload order fixed here as time-major (PB-1.12) | §28 is later; it did not fix the raw order, time-major keeps encoding 0 identical to the §13.5 sample grouping |
| C-04 | SPECCFG 4 args (§4) vs 5 args (§13.6) vs LED limits (§26) | 5 args; `led_cur` valid set = {0..11} (white 0..3, IR 0..2, UV 0) | §26 supersedes; the set {white ≤ 3, IR ≤ 2, UV = 0} is exactly the integers 0..11 (bits 5:4 must be 0, bits 3:2 ≠ 11) |
| C-05 | §4/§13.6 "SPECCFG not hazardous" vs §26 "'Not hazardous' is WRONG" | SPECCFG keeps `hazardous = False`; the unsafe currents are **rejected** by validation on both ends | §26 text: "values outside the set are rejected"; the flight forbidden list (`opcode_forbidden`) excludes 0x16 and `test_sequence.py::test_flight_forbidden_list_matches_dictionary` enforces equality with the ground hazardous set |
| C-06 | CFDP SSR export counts paused packets as `ssr_dropped` (§7) vs beacon URGENT on SSR drops (§24.4) | `Ssr` keeps two counters: overwrite drops (`dropped()`, feeds the beacon) and export-pause drops (`paused_drops()`); HK `ssr_dropped` = their saturated sum | A commanded file transfer must not latch URGENT (autonomy verify item); HK keeps the §7 meaning |
| C-07 | config.h holds "CFDP segment sizes" (§9) vs code keeps them in `ccsds/cfdp.h` | Keep them in `cfdp.h` only (`kCfdpFlightSegLen 128`, `kCfdpRelaySegLen 96`); config.h does not duplicate | single source of truth, already host-tested |
| C-08 | §5 "TC AE packet ≤ 27 octets" holds for TC v1; USLP TC + AE allows ≤ 23 | `kTcMaxFrameLen` stays 64. SEQLOAD/MAGCAL (26-octet TC packets) cannot be sent with `tc_framing = "USLP"` + AE; the GDS refuses them before queueing (PB-2.4) | changing the mission frame limit is a protocol change outside Phase B; default `tc_framing = "TC"` carries them |
| C-09 | app.yaml "name, icon, description only" (§18) vs `ports: [N]` (§23) | ground app.yaml declares `ports: [8080]` | §23 is later and verified against arduino-app-cli `compose_template.go` (ports exposed N:N) |
| C-10 | §23 rule 1 (in-house SX126x driver) vs §25/§30 (RadioLib, bundled patched copy) | RadioLib 7.8.1 bundled patched (`- dir: libraries/RadioLib`) | §25/§30 owner decision |
| C-11 | CardKB #2 / AUTH key (§1.2, §16) vs one CardKB on header `Wire` (§16.3/§17.1/§25.3) | one CardKB, header SDA/SCL (PH12/PH11) by silkscreen; second operator authorises in the web UI | latest owner directive |
| C-12 | `src/core/` (§0) vs `src/fsw/` (§17.2) | pure algorithm code in `src/fsw/` | §17.2 |
| C-13 | `tools/sensitivity_test.py` (§9) vs `tools/link_budget.py sensitivity` (§17.2) | `link_budget.py sensitivity` (already implemented) | §17.2 |
| C-14 | §2.2 table profile 0 = -143.0 dBm vs formula -143.05 → -143.1; profile 7 -124.5 vs datasheet -124.0 (§14) | code values (-143.1 computed, -124.0 datasheet) | code follows the formula/datasheet; docs fix in Phase D |
| C-15 | §13.2 "kSpecBurstCycles 1 -> 2.78 ms per bank" vs datasheet Figure 30 table (N + 1) x 2.78 = 5.56 ms | 5.56 ms (code, `spectral.h`) | datasheet table + SparkFun Example5; docs fix in Phase D |
| C-16 | §13.4 `bore_az_cdeg` 0..360 vs i16 range | flight sends (-180, 180] in 0.01 deg, ground maps to 0..360 (`spectral.azimuth_from_cdeg`) | 36000 does not fit an i16 |
| C-17 | §24.7 PARTICIPANT_1 = spacecraft, PATH 1,2 (tentative) vs Blue Book one-way examples | keep `products.py`: PARTICIPANT_1 = station, PARTICIPANT_2 = VGQ-1, PATH 2,1 | §24.7 asked to verify against the examples; 503.0-B-2 3.3.2 only requires the transmitter first in PATH |
| C-18 | §7 EOF "no TLVs" not stated for EOF; code appends the Entity-ID fault-location TLV on a non-zero condition | keep code (ground accepts both); VERIFY in docs | cross-checked against two 727.0-B-5 implementations |
| C-19 | §24.2 absolute times "not in the future" vs flight also limits to 7 days ahead | keep flight (stricter); ground mirrors with `now_sclk_s` | keeps mod-2^32 ordering unambiguous (autonomy verify item) |
| C-20 | §24.4 URGENT list vs implementation adds AVI_OVERTEMP, CMD_LOSS, FARM_LOCKOUT | keep implementation; listed as owner-review item (Phase D docs) | documented design choice of the autonomy workstream |
| C-21 | §3.4 "ATT unchanged" vs need to show which magnetometer fed TRIAD and whether the BMM150 scale was read | layout unchanged; two previously-zero flag bits are defined (ATT flags b5, b6, PB-1.9) | additive, no octet moves; the operator must be able to judge TRIAD quality |
| C-22 | contract RPC `cfdp_cancel` has no argument | `cfdp_cancel(String seq_decimal)` | the EGSE cancels only the matching transfer; EGSE sequence numbers ≥ 0x80000000 are negative as int32, so a decimal String avoids signedness ambiguity (same as `cfdp_req`) |
| C-23 | `[radio.sx126x] wake_preamble = 32` suggested (radio report) | not added | the MCU constant (`kSxWakePreamble = 32` = flight `kSxWakePreambleSym`) is not configurable; a TOML key would be a no-op |
| C-24 | `kMaxPayload = 128` (tlm_packets.h) vs SPEC v3.1 129 and CFDP PDU up to 146 | `kMaxPayload = 146` with static_asserts (PB-1.1) | required by the new packets |
| C-25 | contract MCU→Linux status = one `sc_status` notification vs its key set (45 keys = 503 characters typical, 596 worst case, measured) and the ≤ 480-character rule (PB-4) | two notifications: `sc_status` (1 Hz, core, ≤ 294 characters worst case) and `sc_status_x` (every 5 s, extended, ≤ 409) built by a host-tested formatter (PB-4.1) | a status that is always truncated is useless; splitting keeps every key |
| C-26 | §13.2 "acquisition modes ... PA-off windows" vs BURST = continuous sampling for up to 60 s | every non-BURST exposure starts only with the PA off (`set_exposure_gate`); a BURST *starts* only with the PA off and then runs through transmissions; the burst-end EVR counts the frames transmitted during it (PB-8.6 item 3) | holding the downlink for 60 s, or stopping a burst at every frame, would defeat the mode; the overlap is reported, not hidden |
| C-27 | v2 cadence table (fixed periods) vs SX126x profiles 0..4, whose capacity is below the table's load (e.g. profile 1 SAFE: 4.09 octet/s of packets vs 2.21 octet/s capacity, PB-3.3) | packet periods are stretched at run time from the measured link capacity (PB-3.3) and the routine-EVR budget follows the capacity | a VC that always grows overflows; the cadence must follow the link |

### 0.4 Global rules recap (binding; §0, §17)
* Flight C++: FP32 only, no recursion, every loop bounded, every return value checked, range
  check on every external input (TC, radio, sensors, Bridge), no type punning
  (memcpy/`put_u16`/`get_u16`), warning-free with `-Wall -Wextra -Wshadow`, the main loop never
  blocks > ~20 ms in one call (documented exceptions: `setup()`, E22
  `configure()`/`set_hibernation()` 60-80 ms typical, bounded ~4 s on a dead module (radio
  report), `Rm3100::run_bist()` ≤ ~5 ms + transactions), never `Bridge.call()` on the MCU.
* Heap: the SSR is the only allocation our code makes after boot. **Documented exception (not
  our code):** every `Bridge.notify()` allocates transiently inside the library — the `String`
  arguments and the MsgPack `Packer` (a `std::vector` grown by `emplace_back`, Arduino_RPClite
  `decoder.h` `send_call`, MsgPack `Packer.h`) — and frees it before returning; the same holds
  for the `String` argument of a `provide_safe` handler. Bound: one notification ≤ 490 octets
  on the wire, so the transient peak is a few KiB (vector growth + String), inside the
  `kHeapReserveBytes` (32 KiB) the SSR leaves free (VERIFY T-HW: long-run heap check). Our code
  builds every notification `String` with `reserve()` first (`to_hex` reserves `2 n`).
* **Monitor / Serial are forbidden in the flight runtime.** Fact (Arduino_RouterBridge 0.4.3
  `monitor.h`, `bridge.h`): `Monitor.write()` first evaluates `operator bool()`, which issues a
  blocking `bridge->call("mon/connected")` whenever the monitor is not connected, and in
  compatibility mode (router without `$/version`) every write is a blocking
  `call("mon/write")`; `RpcCall::result()` spins `while (true)` until a response arrives — no
  timeout. If the router or the Linux side stops, the flight loop would hang. `Serial` is an
  alias of `Monitor` on the UNO Q (`ARDUINO_ROUTERBRIDGE_PROVIDES_SERIAL`, `singletons.cpp`).
  Therefore `LOGF` compiles to nothing unless `VGQ_DEBUG_MONITOR` is 1 (default 0; bench
  debugging only, documented as able to block), and `sketch.ino` calls `Monitor.begin()` only
  under that macro. Everything the operator needs reaches the EGSE through `sc_evr` /
  `sc_status` notifications, which never wait (rule below).
* Bridge calls that DO block until the router answers, used in `setup()` only (documented):
  `Bridge.begin()` (`call("$/reset")`), each `Bridge.provide_safe()` (`call("$/register")`),
  and `Monitor.begin()` in a debug build. A cold power-up therefore waits for Linux (as v2).
* Bridge notifications never wait for the UART. Facts: the router UART on the UNO Q is LPUART1
  (`arduino,router-serial = <&lpuart1>`, variant overlay) = the core object
  `ARDUINO_ROUTER_SERIAL` (`zephyrSerial.h`; `Serial2` by the core's naming, used by the
  library's `Bridge(ARDUINO_ROUTER_SERIAL)`), 115200 baud (`bridge.h` `DEFAULT_SERIAL_BAUD`) =
  11 520 octet/s; its TX ring is `CONFIG_ARDUINO_API_SERIAL_BUFFER_SIZE` = 1024 octets (UNO Q
  autoconf); `ZephyrSerial::write()` yields in a loop while the ring is full and RPClite's
  `send()` loops until everything is written. Rule (WS2, one helper `notify_room(size)`): a
  notification is issued only when `ARDUINO_ROUTER_SERIAL.availableForWrite()` ≥ its payload +
  32 octets (MsgPack framing margin); otherwise `tm_cadu` is kept (ONE pending 236-octet copy;
  a second while one is pending is dropped and counted `cadu_nd`), `sc_status`/`sc_status_x`
  are deferred to a later pass, `sc_evr` is dropped and counted (`evr_nd`). In addition at most
  ONE large notification (`tm_cadu` ≈ 490 octets, `sc_status`, `sc_status_x`) per loop pass and
  `sc_evr` ≤ 2 per pass and ≤ `kEvrNotifyMaxPerS` = 8 per second. The only writer of that UART
  is the loop thread (`provide_safe` handlers and their responses run in it; the flight
  provides no non-safe method), so the check is exact and no write ever waits. Average volume
  ≤ 490/1.5 s + 310/1 s + 440/5 s + 8 x 110/s ≈ 1.6 kB/s (14 % of the UART).
* Python: 3.11+ (station 3.12, App container 3.13), standard library only at run time, typed,
  no bare `except`, no `except Exception` in new code except at a documented thread boundary
  (log + continue): the documented boundaries are `Runner.step()` stages (PB-8.4), the
  `FrontPanel` threads, the web request handler and the EGSE user loop; validated inputs,
  deterministic tests. Every value that arrives over RF is untrusted text/data on the ground,
  even when SDLS is configured (OID frames are never protected, §5, and NONE is a valid
  service).
* No new directories. New files allowed in Phase B (each justified in PB-8.3):
  `spacecraft/vgq1_flight/sketch/src/fsw/datalink.h`, `datalink.cpp`,
  `tools/host_test/test_integration.cpp`, `ground/tests/test_web.py`. Nothing else. (The
  existing `.github/workflows/ci.yml`, commits 0a73319/83237ac, is not new; PB-8.1 gives it an
  owner.)
* Never create `ground/config/sdls_key.hex` or `spacecraft/vgq1_flight/sketch/src/sdls_key.h`
  in the tree; tests use temp dirs (PB-8.5 shows how to type-check the keyed build).
* No AI-model names anywhere. Big-endian on the wire. Comment style as the existing files.

---------------------------------------------------------------------------------------------------

## 1 Telemetry (flight `fsw/tlm_packets.h` + `fsw/spectral.h` ⇄ ground `dssq/dictionary.py` + `dssq/spectral.py`)

### 1.1 Common framing of every TM packet (§3, 133.0-B-2, 301.0-B-4)
* Space Packet primary header 6 octets (version 0, type 0, secondary-header flag 1, APID,
  sequence flags 11, 14-bit per-APID sequence count, length-1) + CUC secondary header 6 octets
  (4 coarse s + 2 fine 2^-16 s, implicit P-field 0x2E) + user data (the layouts below).
* `kMaxPayload` (largest user data) becomes **146** = `ccsds::kCfdpMaxPduLen` (Metadata PDU with
  two 64-character names). Add in `tlm_packets.h` (or `datalink.h`):
  `static_assert(kSpecV31Len <= kMaxPayload)`, `static_assert(kSpecBurstMaxLen <= kMaxPayload)`,
  `static_assert(ccsds::kCfdpMaxPduLen <= kMaxPayload)`. Largest Space Packet = 158 octets
  (= one TM-AE data field).
* Sentinels: i16 0x7FFF, u16 0xFFFF, i32 0x7FFFFFFF, u32 0xFFFFFFFF, i8 -128 = not available
  (ground `dictionary.NA`; the decom maps them to `None`). The radio API's `kRadioNaI16 =
  INT16_MIN` / `kRadioNaI32 = INT32_MIN` must be mapped to 0x7FFF / 0x7FFFFFFF in packets.
* Per-APID sequence counters: replace `seq_index()` (which silently maps unknown APIDs to the
  ENV counter) by `uint8_t apid_index(uint16_t apid)` returning 0..11 in the order HK, RF, EVR,
  CMDVER, TIMECORR, RFSCAN, MAG, ATT, SPEC, ENV, SPECBURST, CFDP and **0xFF for any other APID**,
  with `static const uint8_t kNumApids = 12;` in `tlm_packets.h`. The flight member becomes
  `uint16_t apid_seq_[kNumApids]` (renamed from v2 `seq_[P_COUNT + 4]`, which had 11 entries and
  whose name `seq_` is taken by the `CommandSequencer`, PB-8.6 item 1). `emit()` refuses 0xFF
  (returns false and counts `emit_refused_`, shown in `sc_status_x`; no log output, PB-0.4).

### 1.2 APID / VC map (§3, §7, §13)

| APID | Name | VC | Length (octets) | Recorded in SSR | Source |
|---|---|---|---|---|---|
| 0x010 | HK | 0 | 78 | yes | §3.1 + §24.1 |
| 0x011 | RF | 0 | 49 | yes | §3.2 |
| 0x012 | EVR | 0 | 3 + text (0..80) | yes | v2 |
| 0x013 | CMDVER | 0 | 8 | yes | v2 |
| 0x014 | TIMECORR | 0 | 10 | yes | v2, §6 |
| 0x015 | RFSCAN | 0 | 4 + count (1..84) | yes | v2 |
| 0x020 | MAG | 1 | 46 | yes | §3.3, §24.6 |
| 0x021 | ATT | 1 | 37 | yes | §3.4 |
| 0x022 | SPEC v3.1 | 1 | 129 | yes | §13.4 |
| 0x023 | ENV | 1 | 24 | yes | §3.6 |
| 0x024 | SPECBURST | 1 | 10 + 2n + payload (≤ 124) | yes | §13.5, §28 |
| 0x030 | CFDP | **3** | one PDU, 10..146 | **no** (PB-1.13) | §7 |
| 0x0C0 | TC (uplink) | — | 6 + user data | — | v2 |

VCs: 0 RT engineering, 1 RT science, 2 SSR playback, **3 file delivery (CFDP)**, OID = TM VC 7 /
USLP VCID 63 (never SDLS). Downlink priority VC0 > VC1 > VC2 > VC3 > OID with the v2
anti-starvation rule (PB-8.2 `Downlink`). Each VC buffer is 1024 octets (`VcStream::kBufLen`);
the last `kVcReserveOctets` = 256 octets of every VC buffer are reserved for priority packets
(all of which travel on VC0: EVR with severity ≥ 2, CMDVER, EVR 0x0120/0x0121/0x0122), so
routine packets can never crowd out a warning or a command verification (PB-8.2
`Downlink::push`). Periodic packets are paced to the
link capacity (PB-3.3). SPECBURST is event-driven and can exceed the link (one ≤ 136-octet
packet per 3 samples at the achieved burst rate): every SPECBURST packet is recorded in the
SSR; its real-time push simply fails
when VC1 is full (counted in `Downlink::overflows(1)`), and the operator plays the burst back
(PLAYBACK) or exports it (CFDPPUT 0). `ccsds_types.h`: add `VC_FILE = 3`, set
`kNumDataVcs = 4` (keep `VC_IDLE = 7`). Ground `ccsds/__init__.py`: `VC_NAMES = {0: "RT-ENG",
1: "RT-SCI", 2: "PLAYBACK", 3: "FILE (CFDP)", 7: "IDLE/OID", 63: "IDLE/OID (USLP)"}`.

### 1.3 HK — APID 0x010, VC0, 78 octets (§3.1 + §24.1; C-01)

| Off | Flight field | Type | Ground name | Scale / unit | Notes / enum |
|---|---|---|---|---|---|
| 0 | fsw_mode | u8 | fsw_mode | — | 0 BOOT 1 SAFE 2 CRUISE 3 ENCOUNTER 4 TEST |
| 1 | sclk_partition | u16 | sclk_partition | — | EGSE boot counter = SDLS TM epoch; 0 = none yet (PB-6.3) |
| 3 | uptime_s | u32 | uptime_s | s | SCLK seconds |
| 7 | reset_cause | u8 | reset_cause | — | 0 POWER-ON 1 PIN 2 SOFTWARE 3 WATCHDOG 4 BROWNOUT 255 UNKNOWN |
| 8 | fdir_flags | u16 | fdir_flags | — | FdirBit (unchanged; b15 = E22-400T37S abnormal status only, §3.7) |
| 10 | sensor_health | u16 | sensor_health | — | v3 SensorBit (PB-1.15) |
| 12 | cmd_accepted | u16 | cmd_accepted | — | |
| 14 | cmd_rejected | u16 | cmd_rejected | — | |
| 16 | cmd_last_opcode | u8 | cmd_last_opcode | — | |
| 17 | cmd_last_status | u8 | cmd_last_status | — | CmdError (PB-2.2) |
| 18 | tc_frames_ok | u16 | tc_frames_ok | — | passed FECF + SDLS |
| 20 | tc_frames_bad | u16 | tc_frames_bad | — | |
| 22 | farm_state | u8 | farm_state | — | 1 OPEN 2 WAIT 3 LOCKOUT |
| 23 | farm_vr | u8 | farm_vr | — | |
| 24 | cmd_loss_timer_s | u32 | cmd_loss_timer_s | s | |
| 28 | bus_temp_cC | i16 | bus_temp | 0.01 degC | Nicla Sense ME (BME688); limits (-20, 0, 60, 75) |
| 30 | radio_fault | u8 | radio_fault | — | last radio fault: 0 none, 1..4 T37S abnormal status (FF FF FF 0x), **16 BUSY_STUCK 17 SPI 18 REINIT 19 NO_IRQ** (SX126x, `RadioFault`) ; limits (None, None, 0.5, 2.5) |
| 31 | radio_fault_reports | u16 | radio_fault_reports | — | T37S reports + SX126x FAULT events (saturating) |
| 33 | loop_max_ms | u16 | loop_max_ms | ms | reset after each HK |
| 35 | loop_overruns | u16 | loop_overruns | — | passes > 250 ms |
| 37 | i2c_errors | u16 | i2c_errors | — | `I2cBus::errors()` saturated |
| 39 | ssr_fill_permille | u16 | ssr_fill | 0.1 % | limits (None, None, 80, 95) |
| 41 | ssr_dropped | u16 | ssr_dropped | — | `dropped() + paused_drops()` saturated (C-06) |
| 43 | ssr_kib | u16 | ssr_kib | KiB | |
| 45 | vc0_backlog | u16 | vc0_backlog | octets | |
| 47 | vc1_backlog | u16 | vc1_backlog | octets | |
| 49 | vc2_backlog | u16 | vc2_backlog | octets | |
| 51 | **vc3_backlog** | u16 | vc3_backlog | octets | NEW |
| 53 | **sdls_tc_service** | u8 | sdls_tc_service | — | 0 NONE 1 AUTH 2 AE (`Sdls::service()`); replaces `sdls_enabled` |
| 54 | **sdls_tm_service** | u8 | sdls_tm_service | — | 0 NONE 2 AE (`SdlsTm::service()`) |
| 55 | sdls_auth_fail | u16 | sdls_auth_fail | — | TC frames rejected by SDLS; limits (None, None, 1, 10) |
| 57 | sdls_last_sn | u32 | sdls_last_sn | — | last accepted TC SN |
| 61 | **sdls_tm_epoch** | u16 | sdls_tm_epoch | — | 0 = none yet |
| 63 | **sdls_tm_state** | u8 | sdls_tm_state | — | 0 OFF 1 PROTECTED 2 WAITING FOR EPOCH 3 UNPROTECTED (null SA); limits (None, None, 1.5, None) |
| 64 | **cfdp_state** | u8 | cfdp_state | — | 0 IDLE 1 SENDING SSR 2 RELAYING EGSE FILE |
| 65 | **cfdp_seq** | u32 | cfdp_seq | — | current/last transaction sequence number, 0 = none since boot (a real one is never 0 or 0xFFFFFFFF, PB-1.13) |
| 69 | **cfdp_progress_permille** | u16 | cfdp_progress | 0.1 % | |
| 71 | **seq_state** | u8 | seq_state | — | 0 IDLE/EMPTY 1 ARMED 2 COMPLETE 3 STOPPED_BY_FDIR |
| 72 | **seq_count** | u8 | seq_count | — | entries pending |
| 73 | **seq_next_dt_s** | u16 | seq_next_dt_s | s | 0xFFFF none/not armed (→ None), saturates 0xFFFE |
| 75 | **mag_bist** | u8 | mag_bist | — | b0..2 OB X/Y/Z OK, b3 OB tested, b4..6 IB X/Y/Z OK, b7 IB tested |
| 76 | **magcal_state** | u8 | magcal_state | — | b0 OB calibration active, b1 IB active |
| 77 | **beacon_level** | u8 | beacon_level | — | 0 NOMINAL 1 INTERESTING 2 IMPORTANT 3 URGENT; limits (None, None, 1.5, 2.5) |

Hibernation beacon (§24.4, §3): the beacon is this same HK packet, generated once per
`beacon_s` while hibernating, sent as the next VC0 frame.

Changes required:
* Flight `HkPacket`: delete `sdls_enabled`; add the bold fields in this order; `pack_hk()` writes
  them in table order and returns 78 (`static const size_t kHkLen = 78`).
* Ground `PACKETS[0x010]`: delete `sdls_enabled`; insert `vc3_backlog` after `vc2_backlog`;
  append the bold fields with the names/scales above; `ssr_fill` and `cfdp_progress` scale 0.1.
* host_test `tlm_vectors.txt` HK line: length check 78 and expectations for every new field.

### 1.4 RF — APID 0x011, VC0, 49 octets (§3.2)

| Off | Flight field | Type | Ground name | Scale / unit | Notes |
|---|---|---|---|---|---|
| 0 | **radio_model** | u8 | radio_model | — | 1 E22-400T33D 2 E22-400T37S 3 SX126x (`radio_.model()`) |
| 1 | air_rate_code | u8 | air_rate_code | — | E22 REG0[2:0] / SX126x profile 0..7 |
| 2 | tx_power_code | u8 | tx_power_code | — | 0..3, meaning per model (§2) |
| 3 | channel | u8 | channel | — | f = 410.125 + CH MHz |
| 4 | radio_cfg_ok | u8 | radio_cfg_ok | — | was `e22_cfg_ok`; `radio_.config_ok()`; limits (0, None, None, None) |
| 5 | **framing** | u8 | framing | — | 0 TM, 1 USLP (`VGQ_DOWNLINK_FRAMING`) |
| 6 | frames_sent | u32 | frames_sent | — | |
| 10 | oid_frames_sent | u32 | oid_frames_sent | — | |
| 14 | tx_airtime_ms_total | u32 | tx_airtime_ms_total | ms | |
| 18 | tx_duty_permille | u16 | tx_duty | 0.1 % | 60 s window; limits (None, None, 55, 70) |
| 20 | frame_period_ms | u16 | frame_period_ms | ms | saturates at 65535 (SX126x profile 0 periods exceed it, PB-5.4) |
| 22 | aux_timeouts | u16 | radio_timeouts | — | TX_TIMEOUT events (all models) |
| 24 | uplink_rssi_dbm | i16 | uplink_rssi | dBm | last uplink packet; NA 0x7FFF |
| 26 | **uplink_snr_cdb** | i16 | uplink_snr | 0.01 dB | SX126x only, NA 0x7FFF |
| 28 | **uplink_freq_err_hz** | i32 | uplink_freq_err | Hz | SX126x only, raw, NA 0x7FFFFFFF |
| 32 | noise_floor_dbm | i16 | sc_noise_floor | dBm | NA 0x7FFF |
| 34 | cltu_ok | u16 | cltu_ok | — | |
| 36 | cltu_bad | u16 | cltu_bad | — | |
| 38 | bch_corrected | u16 | bch_corrected | — | |
| 40 | rx_bytes | u32 | rx_bytes | octets | `radio_.rx_bytes()` |
| 44 | mcfc | u8 | mcfc | — | `framer.mcfc()` (kept in USLP mode, where no frame carries it) |
| 45 | last_airtime_ds | u8 | last_airtime | 0.1 s | saturates at 25.5 s (SX126x profile 0 = 34.6 s) |
| 46 | **lbt_busy** | u16 | lbt_busy | — | `radio_.lbt_deferrals()` |
| 48 | **flags** | u8 | rf_flags | — | b0 Rx boosted gain verified (0x08AC = 0x96), b1 LBT/CAD on (`radio_.rf_flags()`) |

Changes: flight `RfPacket` renames `e22_cfg_ok` → `radio_cfg_ok`, adds the bold fields,
`pack_rf()` returns 49. Ground `PACKETS[0x011]`: desc "Telecom subsystem (any radio model)",
names as above (`radio_timeouts`, `uplink_snr` 'h' 0.01 dB, `uplink_freq_err` 'i' 1.0 Hz,
`lbt_busy`, `rf_flags`), delete the T37S-only wording.

### 1.5 EVR — APID 0x012, VC0, variable (unchanged layout)
`severity u8 (0 DIAG, 1 INFO, 2 WARN, 3 ERROR, 4 FATAL) | event_id u16 | ASCII text 0..80`.
Complete v3 event-ID map in PB-2.6. Ground: `spectral.EVR_SPECTRAL_IDS` becomes
`range(0x0701, 0x0708)` (0x0701..0x0707).

### 1.6 CMDVER — APID 0x013, VC0, 8 octets (layout unchanged)
`tc_seq u8 | opcode u8 | stage u8 | error_code u8 | ground_tag u32`. `tc_seq`: N(S) of the
carrying AD frame, **0xFF** bypass (BD), **0xFE stored-sequence dispatch** (NEW; flight
`kCmdVerSeqStored`, ground `dictionary.CMDVER_SEQ_STORED`). stage 2 EXECUTED / 3 FAILED;
error_code = CmdError (PB-2.2). The ground FOP must ignore `tc_seq` 0xFE for COP-1 bookkeeping
and log it as "stored sequence: opcode 0xXX EXECUTED/FAILED".

### 1.7 TIMECORR — APID 0x014, VC0, 10 octets (layout unchanged, meaning per §6)
`ref_mcfc u8 | sclk_coarse u32 | sclk_fine u16 | air_rate_code u8 | ref_airtime_ms u16`.
`ref_mcfc` = TM: the MCFC of the reference frame; **USLP: the VCF count of a VC0 reference
frame — only VC0 frames may be references** (`TmFramer::last_count()`, `last_vcid()`; ground
`TransferFrame.timecorr_key`). SCLK = when the PA was keyed: for the SX126x with LBT/CAD use
`radio_.last_tx_launch_ms()`, i.e. `tx_start_sclk_us_ += (last_tx_launch_ms() - transmit_ms) *
1000` at TX_DONE (radio report).

### 1.8 MAG — APID 0x020, VC1, 46 octets (layout unchanged; §3.3, §24.6, §26)

| Off | Field | Type | Ground | Scale |
|---|---|---|---|---|
| 0 | nsamples | u8 | nsamples | |
| 1 | flags | u8 | flags | b0 sample taken with PA keyed, b1 OB ok, b2 IB ok, b3 BMM150 ok, **b4 an AS7265X lamp was lit (or its state unknown) during an RM3100 sample**, **b5 onboard MAGCAL active (`magcal_.state() != 0`)**, b6-7 0 |
| 2/6/10 | ob bx/by/bz | i32 | ob_b_x/y/z | 1e-3 uT (nT on wire), NA 0x7FFFFFFF |
| 14 | ob rms | u16 | ob_rms | 1e-3 uT |
| 16..29 | inboard (same) | | ib_* | |
| 30..43 | body BMM150 (same) | | body_* | |
| 44 | rm3100_cycle_count | u16 | rm3100_cc | |

MAG stays raw science (§24.6); flight flag constants `MAG_F_PA_KEYED 0x01, MAG_F_OB 0x02,
MAG_F_IB 0x04, MAG_F_BMM150 0x08, MAG_F_LAMP 0x10, MAG_F_CAL 0x20`. Ground: only the flags
description changes.

### 1.9 ATT — APID 0x021, VC1, 37 octets (layout unchanged; C-21)
`flags u8 | acc_mg i16[3] (1e-3 g) | gyro_ddps i16[3] (0.1 deg/s) | mag_dT i16[3] (BMM150, 0.1 uT)
| q_fus i16[4] (/16384, w x y z) | q_fus_acc_mrad u16 (1e-3 rad) | q_triad i16[4] (/16384)`.
flags: b0 fusion quaternion, b1 accel, b2 gyro, b3 BMM150, b4 TRIAD valid, **b5 BMM150 scale read
from the BHI260 (ARU page 0 flag b4; 0 = firmware fallback 1/16 uT/LSB)**, **b6 TRIAD used the
calibrated RM3100 field (0 = BMM150)**. Flight constants `ATT_F_QUAT 0x01 ... ATT_F_MAG_SCALE 0x20,
ATT_F_TRIAD_RM3100 0x40`. Ground: flags description only.

### 1.10 SPEC v3.1 — APID 0x022, VC1, 129 octets (§13.4; C-02, C-16)
Flight `SpecV31` / `pack_spec_v31()` (spectral.h, already implemented); ground
`spectral.SPEC_V31_FIELDS` (already implemented, 7-tuples with decoder hook) is spliced into
`dictionary.PACKETS[0x022]` (replacing the v2 SPEC entry).

| Off | Field | Type | Ground name | Meaning |
|---|---|---|---|---|
| 0 | mode | u8 | spec_mode | 0 SURVEY 1 HDR 2 LOCKIN 3 FLUOR 4 BURST |
| 1 | flags | u8 | flags | b0 valid, b1 white, b2 IR, b3 UV lamp **actually driven**, b4 AGC, b5 active (on-off), b6 any saturated, b7 attitude valid |
| 2 | gain_code | u8 | gain_code | 0 1x 1 3.7x 2 16x 3 64x; HDR: AGC centre (§26 AS-DP-04) |
| 3 | int_cycles | u8 | int_cycles | N: (N+1) x 2.78 ms; HDR: centre, not necessarily taken |
| 4 | led_cur | u8 | led_cur | applied codes (`spec_led_cur_applied`), b1:0 white b3:2 IR b5:4 UV |
| 5 | n_frames | u8 | n_frames | exposures merged / pairs / burst samples |
| 6 | sat_mask | u32 | sat_mask | bit i = channel i (wavelength order) |
| 10 | spec_log u16[18] | | spec_<nm>nm | log code → µW/cm² (`spec_log_decode`, §13.3) |
| 46 | unc_log u16[18] | | unc_<nm>nm | 1-sigma µW/cm² |
| 82 | raw u16[18] | | raw_<nm>nm | counts; 0xFFFF = not converted, **or saturated when the sat bit is set** (PB-1.16) |
| 118 | temp i8[3] | | temp_0..2 | degC, -128 n/a (devices AS72651, 52, 53) |
| 121 | bore_zen_cdeg | i16 | bore_zen | 0.01 deg, NA 0x7FFF |
| 123 | bore_az_cdeg | i16 | bore_az | 0.01 deg sent (-180, 180], ground 0..360 (C-16) |
| 125 | class_id | u8 | spec_class | 0 UNKNOWN 1 DARK 2 DAYLIGHT 3 INCANDESCENT/HALOGEN 4 WHITE LED 5 FLUORESCENT 6 NARROWBAND 7 OWN LAMP (heuristic) |
| 126 | class_conf | u8 | spec_conf | % |
| 127 | change_cdeg | u16 | spec_change | 0.01 deg, NA 0xFFFF |

Channel order: A410 B435 C460 D485 E510 F535 G560 H585 R610 I645 S680 J705 T730 U760 V810 W860
K900 L940. Changes: flight removes `SpecPacket`/`pack_spec` (v2) from tlm_packets.*; ground
removes the v2 0x022 entry. Decom must honour the 7th tuple element (PB-8.4, WS3).

### 1.11 ENV — APID 0x023, VC1, 24 octets (§3.6; Nicla Sense ME only)

| Off | Flight field | Type | Ground | Scale / unit | NA |
|---|---|---|---|---|---|
| 0 | flags | u8 | flags | b0 BMP390 pressure, b1 BSEC, b2 temperature/humidity, b3 gas resistance | — |
| 1 | p_cPa | u32 | p | 0.01 hPa (= Pa) | 0xFFFFFFFF |
| 5 | t_cC | i16 | t | 0.01 degC | 0x7FFF |
| 7 | rh_cpct | u16 | rh | 0.01 % (the BHI260 delivers whole %RH: UI shows 1 %, §22.3) | 0xFFFF |
| 9 | iaq | u16 | iaq | — | 0xFFFF |
| 11 | iaq_s | u16 | iaq_s | — | 0xFFFF |
| 13 | co2eq_ppm | u32 | co2eq | ppm | 0xFFFFFFFF |
| 17 | bvoc_cppm | u16 | bvoc | 0.01 ppm | 0xFFFF |
| 19 | gas_ohm | u32 | gas | ohm | 0xFFFFFFFF |
| 23 | accuracy | u8 | accuracy | BSEC 0..3; 0 when b1 clear (UI shows "—" when b1 clear) | — |

Mapping from `AruReading.env_flags` (page 1: b0 p, b1 BSEC, b2 T, b3 RH, b4 gas): ENV b0 = b0,
b1 = b1, b2 = (b2 | b3) with the missing one sent as its sentinel, b3 = b4. Pressure: send the
page's raw u32 Pa (add `uint32_t p_pa` to `AruReading`, filled from bytes 3..6; no float round
trip). Changes: flight `EnvPacket` rewritten (old `nenv_*`/`nme_*` fields deleted), `pack_env()`
returns 24; ground `PACKETS[0x023]` rewritten ("Environment (Nicla Sense ME)").

### 1.12 SPECBURST — APID 0x024, VC1, variable (§13.5 superseded by §28; C-03; §26)

| Off | Field | Type | Meaning |
|---|---|---|---|
| 0 | t0_ms | u32 | millis() of the first sample = SCLK ms since MCU boot mod 2^32 |
| 4 | rate_dhz | u16 | achieved rate, 0.1 Hz |
| 6 | gain_code | u8 | |
| 7 | int_cycles | u8 | burst integration N (`kSpecBurstCycles`) |
| 8 | n | u8 | samples in this packet, 1..3 |
| 9 | encoding | u8 | 0 raw, 1 CCSDS 121.0-B-3 (n=16, J=16, r=128, Basic, preprocessor on) |
| 10 | dt_ms | u16[n] | DATA_RDY detection of sample k, ms since t0 |
| 10+2n | payload | | encoding 0: n x 18 u16 big-endian, **time-major** (sample 0 channels 0..17, then sample 1 ...); encoding 1: `rice_encode(kRiceMission, cm, 18n)` with channel-major `cm[ch*n + t] = s[t].raw[ch]`, length 1..36n-1 octets (zero fill to an octet) |

Raw value semantics (§26 AS-DP-07): 0xFFFF = channel not converted in the BANK mode (→ None);
0xFFFE = a real count ≥ 65534 when full scale is 65535 (N ≥ 63) → saturated
(`spec_burst_raw()`); otherwise counts. Length: raw 10 + 38n (max 124 = `kSpecBurstMaxLen`);
Rice strictly shorter than raw. Flight: `RICE_OK` → encoding 1; `RICE_ERR_OVERFLOW` → raw;
any other status → raw + EVR WARN 0x0704 "BURST rice status %u". Ground: encoding 1 →
`rows = rice.time_major(rice.decode(payload, 18 * n), 18)`; `RiceError` → counter
`rx["decoder"]["rice_errors"]` + WARN event, packet dropped; encoding not 0/1 → ValueError
(counted as decom error); `compression_ratio = 36 n / len(payload)`.
Per-sample read time is not carried (layout fixed by §28); `SpecResult.burst_read_max_ms` goes
into the burst-end EVR 0x0704 text (PB-2.6) — AS-T8.

### 1.13 CFDP — APID 0x030, VC3 (§7)
User data = exactly one class-1 PDU (cfdp.h mission profile: 10-octet header, Metadata 0x07,
File Data, EOF 0x04 with Entity-ID TLV when the condition is not 0). MCU PDUs ≤ 142 (file data
128 octets), Metadata ≤ 146; EGSE-relayed PDUs ≤ 110 (`kCfdpRelayMaxPduLen`). Sequence numbers:
MCU `cfdp_make_seq(partition, n)` (< 0x80000000), EGSE-built `kCfdpEgseSeqFlag | cfdp_make_seq(...)`
assigned by the MCU and sent in `cfdp_req`. Per-boot counter n ∈ 1..0xFFFE (wraps 0xFFFE → 1),
so `cfdp_seq` is never 0 or 0xFFFFFFFF (the u32 sentinel). **Uniqueness across boots comes only
from the partition** (the EGSE boot counter): with partition 0 every boot would reuse 1, 2, ...
and the ground receiver silently drops a (source, seq) it already finished (`cfdp.py`
`Receiver.receive()`: a key in `finished` is counted "late" and its PDUs discarded). Therefore
every CFDPPUT is refused with CE_BUSY + EVR 0x0A06 while `sclk_.partition == 0` (PB-2.5); without
the EGSE, SSR data are dumped with PLAYBACK. (`cfdp_make_seq` keeps 15 partition bits, so
partitions p and p + 32768 alias; the ground keeps only the last 50 finished transactions, so an
alias needs 32768 EGSE boots in between — accepted, documented.) CFDP packets are **not recorded in the
SSR** (`emit(..., record = false)`): they are copies of files that already exist, and an SSR
export must not record its own PDUs. Ground: `dictionary.APID_CFDP = 0x030`; decom returns
`{"pdu_len": n}` (no fields); the GDS passes the user data to `ccsds.cfdp.Receiver.receive()`.

### 1.14 RFSCAN — APID 0x015 (unchanged)
`first_ch u8 | count u8 | duration_ms u16 | noise i8[count]` (-128 n/a, count ≤ 84).

### 1.15 Sensor bits (HK.sensor_health, SENSORS mask; §3.7)
`SNS_NICLA_ME b0, SNS_ME_QUAT b1, SNS_ME_ACC b2, SNS_ME_GYRO b3, SNS_ME_MAG b4, SNS_ME_BARO b5,
SNS_ME_BSEC b6, SNS_AS7265X b7, SNS_RM3100_OB b8, SNS_RM3100_IB b9`; delete `SNS_NICLA_ENV`,
`SNS_ENV_TH/IAQ/OAQ`. `kSensorInstrumentMask = 0x0381` (b0|b7|b8|b9). Ground `SENSOR_BITS` =
the 10 names in bit order. `autonomy.h` uses the names (`kBeaconInstruments`) and needs no edit;
`test_autonomy.cpp` uses names too (re-run, do not edit unless it fails). AS7265X health is set
from `spec_.ok()` at run time (not only from `begin()`), §26 AS-PROT-7.

### 1.16 Ground decoding fixes owed (§26, §28)
* `spectral._channels`: map raw 0xFFFF → saturated count (keep the value 0xFFFF as int) whenever
  that channel's sat bit is set, **whatever `int_cycles` is** (AS-DP-04 minimum fix).
* `spectral.analyse`: when `mode == HDR`, `raw_fraction` = None for every channel and
  `exposure["note"] = "bracket centre, not necessarily an exposure taken; raw[] from the frame
  chosen per channel"` (AS-DP-04).
* `decode_specburst`: §28 layout above; per sample `raw` (None for 0xFFFF) and `saturated`
  (`r >= full_scale(int_cycles) - 1`, 0xFFFE counts as saturated); returns `encoding`,
  `payload_len`, `compression_ratio`.
* `pack_specburst(t0, rate, gain, cyc, samples, *, encoding: int | None = None)`: reference
  encoder (None = choose shorter, like the flight) for the simulator and tests.

### 1.17 Summary of dictionary.py structure changes (WS3)
* Field tuples may have a 7th element (decoder callable); every consumer (`decom`, `products`,
  `tools/gen_dictionary_doc.py`, `gds` anomaly loop) must accept 6- and 7-tuples.
* New module constants: `APID_HK..APID_CFDP` (all APIDs), `VARIABLE_APIDS = {0x012, 0x015,
  0x024, 0x030}`, enums `RADIO_MODELS {1,2,3}`, `FRAMINGS {0: "TM", 1: "USLP"}`,
  `SDLS_SERVICES {0: "NONE", 1: "AUTH", 2: "AE"}`, `SDLS_TM_STATES` (= `ccsds.sdls.TM_STATE_NAMES`),
  `CFDP_STATES {0: "IDLE", 1: "SENDING SSR", 2: "RELAYING EGSE FILE"}`, `SEQ_STATES {0:
  "IDLE/EMPTY", 1: "ARMED", 2: "COMPLETE", 3: "STOPPED_BY_FDIR"}`, `BEACON_LEVELS {0: "NOMINAL",
  1: "INTERESTING", 2: "IMPORTANT", 3: "URGENT"}`, `RADIO_FAULTS` += {16: "SX126x BUSY STUCK",
  17: "SX126x SPI/COMMAND ERROR", 18: "SX126x REINIT FAILED", 19: "SX126x NO IRQ"},
  `CMD_ERRORS` += {7: "BUSY"}, `CMDVER_SEQ_BYPASS = 0xFF`, `CMDVER_SEQ_STORED = 0xFE`,
  `MAG_FLAG_BITS`, `ATT_FLAG_BITS`, `ENV_FLAG_BITS`, `RF_FLAG_BITS` (name lists),
  `decode_bits(value, names) -> list[str]`, `decode_mag_bist(v) -> {"ob": {"tested", "x", "y",
  "z"}, "ib": {...}}`.
* Remove `AIR_RATES_BPS` (T37S-only table); callers use `link.rate_bps(model, code)`.
* Keep `checked_limits` and `NA`.

---------------------------------------------------------------------------------------------------

## 2 Commands (flight `fsw/commands.h/.cpp` ⇄ ground `dssq/dictionary.py` COMMANDS)

### 2.1 TC packet and the two-stage parser (§4, §29 autonomy hand-off)
TC packet: primary header 6 octets (version 0, type 1, no secondary header, APID 0x0C0, sequence
flags 11), user data = `opcode u8 | arguments` (big-endian). Flight API (WS1):

```cpp
// commands.h
enum Opcode : uint8_t { OP_NOOP = 0x01, OP_MODE = 0x02, OP_TXPWR = 0x03, OP_AIRRATE = 0x04,
  OP_FPERIOD = 0x05, OP_PKTRATE = 0x06, OP_PLAYBACK = 0x07, OP_SSRCLEAR = 0x08, OP_PING = 0x09,
  OP_SENSORS = 0x0A, OP_RSTCNT = 0x0B, OP_REBOOT = 0x0C, OP_MAGCC = 0x0D, OP_CMDLOSS = 0x0E,
  OP_FDIRMASK = 0x0F, OP_TXINHIBIT = 0x10, OP_EVRLEVEL = 0x11, OP_TIMECORR = 0x12,
  OP_RFSCAN = 0x13, OP_HIBERNATE = 0x14, OP_CHANNEL = 0x15, OP_SPECCFG = 0x16, OP_CFDPPUT = 0x17,
  OP_CFDPCANCEL = 0x18, OP_RADIOOPT = 0x19, OP_SPECMODE = 0x1A, OP_SPECBASE = 0x1B,
  OP_SPECDIAG = 0x1C, OP_SEQLOAD = 0x1D, OP_SEQCTRL = 0x1E, OP_MAGBIST = 0x1F, OP_MAGCAL = 0x20 };
enum CmdError : uint8_t { CE_OK = 0, CE_UNKNOWN_OPCODE = 1, CE_BAD_LENGTH = 2, CE_BAD_ARGUMENT = 3,
  CE_HARDWARE = 5, CE_BAD_MAGIC = 6, CE_BUSY = 7 };        // 4 stays retired
struct Command {
  uint8_t opcode = 0;
  uint8_t a8 = 0, b8 = 0, c8 = 0, d8 = 0, e8 = 0;           // 8-bit arguments in order
  uint16_t a16 = 0, b16 = 0;
  uint32_t a32 = 0;
  const uint8_t* args = nullptr;                             // first argument octet
  uint8_t nargs = 0;                                         // argument octets
};
// Header check: version/type/APID 0x0C0/sequence flags 11/length; user data = opcode + args.
CmdError parse_tc_header(const uint8_t* pkt, size_t len, const uint8_t*& user, size_t& n);
// Decodes and range-checks opcode + arguments (n >= 1); identical for live and stored commands.
CmdError parse_user_data(const uint8_t* u, size_t n, Command& c);
CmdError parse_tc_packet(const uint8_t* pkt, size_t len, Command& c);   // header + user data
// SeqCmdValidator for CommandSequencer::set_validator (autonomy.h): parse_user_data, no side effects.
uint8_t validate_stored_command(void* ctx, const uint8_t* cmd, uint8_t len);
```
`commands.cpp`: `static_assert(OP_SEQLOAD == kOpcodeSeqLoad && OP_SEQCTRL == kOpcodeSeqCtrl &&
OP_MAGBIST == kOpcodeMagBist && OP_MAGCAL == kOpcodeMagCal)`. Header rule added: sequence flags
must be 11 (else CE_BAD_ARGUMENT). Argument count must be exact (else CE_BAD_LENGTH).

### 2.2 Error codes, verification
CmdError 0 OK, 1 UNKNOWN_OPCODE, 2 BAD_LENGTH, 3 BAD_ARGUMENT, 5 HARDWARE (instrument/radio
absent), 6 BAD_MAGIC, **7 BUSY (resource in use or not ready yet — retry later: CFDP transfer,
SSR export/playback, RF survey, SPECDIAG dump, MAGBIST pending, CFDPPUT before the EGSE has
supplied the SCLK partition)**. `seq_cmd_error()` / `magcal_cmd_error()` (autonomy) map their
statuses to 2/3. Every executed command emits CMDVER (stage 2 or 3) and updates
`cmd_last_opcode/status`, `cmd_accepted/rejected`. A parse failure also emits EVR 0x0500+e.

### 2.3 Command table 0x01..0x20
Haz = hazardous (ground two-step confirmation, plus second-operator AUTH with the two-person
rule). Seq = may be stored by SEQLOAD (flight `CommandSequencer::opcode_forbidden()` = hazardous
∪ {REBOOT, SEQLOAD, SEQCTRL}; also a stored command is at most 12 octets incl. the opcode).
"check" = extra validation beyond per-argument ranges, implemented identically in the flight
`parse_user_data()` and the ground `CommandDef.check` (PB-2.4).

| Op | Mnemonic | Arguments (order, type, range) | check | Haz | Seq | Flight behaviour (WS2, `Flight::execute_user`) |
|---|---|---|---|---|---|---|
| 0x01 | NOOP | — | | | yes | none |
| 0x02 | MODE | mode u8 1..4 (SAFE 1, CRUISE 2, ENCOUNTER/ENC 3, TEST 4) | | | yes | `set_mode(a8)`; `safe_by_fdir_ = false`; exits hibernation |
| 0x03 | TXPWR | code u8 0..3 | | | yes | `rcfg_.power_code = a8`; reconfigure when idle; EVR 0x0214 with dBm (`radio_tx_power_dbm`) |
| 0x04 | AIRRATE | code u8 0..7, revert_s u16 60..3600 | | YES | no | coordinated change after 2 more frames at the old setting; auto-revert unless a valid TC arrives (v2 logic, `RadioSettings.rate_code`); EVR 0x0211 |
| 0x05 | FPERIOD | ms u16 500..60000 | | | yes | minimum frame period (`cmd_frame_period_ms_`) |
| 0x06 | PKTRATE | apid u16, period_s u16 0..3600 (0 = off) | apid ∈ {0x010 HK, 0x011 RF, 0x014 TIMECORR, 0x020 MAG, 0x021 ATT, 0x022 SPEC, 0x023 ENV} | | yes | `period_override_[i]` |
| 0x07 | PLAYBACK | start u8 0..1 (START 1, STOP 0) | | | yes | `ssr_.start_playback()` (false while an SSR export runs → CE_BUSY) / `stop_playback()`; EVR 0x0401 |
| 0x08 | SSRCLEAR | — | | YES | no | CE_BUSY while exporting; else clear SSR + VC2 |
| 0x09 | PING | tag u32 | | | yes | CMDVER ground_tag = tag |
| 0x0A | SENSORS | mask u16 | mask & ~0x0381 == 0 | | yes | `sensor_enable_ = mask` |
| 0x0B | RSTCNT | — | | | yes | clears cmd/tc/cltu counters, `aux_timeouts_`, loop stats |
| 0x0C | REBOOT | magic u16 = 0xB007 (else CE_BAD_MAGIC) | | YES | no | reboot after 2 more frames |
| 0x0D | MAGCC | cycle_count u16 50..400 | | | yes | both RM3100s; CE_HARDWARE if neither accepts |
| 0x0E | CMDLOSS | seconds u32 0..604800 | | YES | no | command-loss timer (0 = off) |
| 0x0F | FDIRMASK | mask u16 | | YES | no | FDIR response mask |
| 0x10 | TXINHIBIT | seconds u16 10..3600 | | YES | no | transmitter silent, auto re-enable |
| 0x11 | EVRLEVEL | level u8 0..4 (DIAG..FATAL) | | | yes | minimum downlinked EVR severity |
| 0x12 | TIMECORR | — | | | yes | TIMECORR after the next reference frame (TM: any frame; USLP: next VC0 frame) |
| 0x13 | RFSCAN | first_ch u8 0..83, last_ch u8 0..83 | first ≤ last | | yes | CE_BUSY if a survey is already running; survey via `request_noise()` (downlink paused); EVR 0x0302/0x0303 |
| 0x14 | HIBERNATE | beacon_s u16 60..3600 | | YES | no | `radio_.set_hibernation(true)` when idle; HK beacon every beacon_s; EVR 0x0800 |
| 0x15 | CHANNEL | ch u8 0..83, revert_s u16 60..3600 | ground also: ch ∈ [radio] allowed_channels | YES | no | coordinated change (as AIRRATE); EVR 0x0212 |
| 0x16 | SPECCFG | gain u8 {0,1,2,3,255 AUTO/AGC}, cycles u8 1..255, lamps u8 0..7 (b0 white b1 IR b2 UV), led_cur u8 0..11 (§26), ind u8 0..4 | gain ∈ {0..3,255}; led_cur ≤ 11 (= `spec_led_cur_applied(v) == v`) | | yes | `cfg = spec_cfg_` with the 5 fields; `spec_config_valid(cfg)` else CE_BAD_ARGUMENT (e.g. LOCKIN with lamps 0); store (applies at the next cycle, also when the instrument is absent — EVR notes it) |
| 0x17 | CFDPPUT | source u8 0..2 (SSR 0, EVRLOG 1, CADU 2), max_kib u16 (0 = whole file) | | | yes | PB-2.5 (CE_BUSY while the SCLK partition is 0) |
| 0x18 | CFDPCANCEL | — | | | yes | PB-2.5; always CE_OK (idempotent) |
| 0x19 | RADIOOPT | flags u8 0..3 (b0 LBT/CAD before TX, b1 Rx boosted gain — SX126x only, ignored by E22) | | | yes | `rcfg_.lbt = b0; rcfg_.rx_boost = b1`; reconfigure when idle; EVR 0x0213 |
| 0x1A | SPECMODE | mode u8 0..4 (SURVEY HDR LOCKIN FLUOR BURST), n u8 1..60 | SURVEY 1..60 (unused), HDR 2..4, LOCKIN 1..16, FLUOR 1..16, BURST 1..60 | | yes | `spec_config_valid` with current lamps (LOCKIN needs ≥ 1 lamp) else CE_BAD_ARGUMENT; a running BURST is aborted when the mode changes; takes effect at the next cycle |
| 0x1B | SPECBASE | threshold_cdeg u16 0..9000 (0 = only reset the baseline) | | | yes | `SpecProcessor::set_baseline_cmd()` |
| 0x1C | SPECDIAG | device u8 0..2, n_sub u8 0..32 | | | yes | `spec_.request_diag()`: false → CE_BUSY (dump queued) or CE_HARDWARE (no chip); lines → EVR 0x0703 (exempt from the routine-EVR budget but paced: PB-2.6) |
| 0x1D | SEQLOAD | 19 octets: slot u8 0..31, flags u8 (b0 relative), t_s u32, len u8 1..12, cmd u8[12] (zero padded) | length 19 at parse; full checks in `CommandSequencer::load` | | no | `seq_.load(args, 19, sclk_s, rep)`; CmdError = `seq_cmd_error(st)`; EVR 0x0900 (ok) / 0x0901 (refused, `seq_status_text`, rep.cmd_error) |
| 0x1E | SEQCTRL | action u8 0..3 (START 0, STOP 1, CLEAR/CLEAR_ALL 2, DELETE 3), slot u8 0..31 | slot must be 0 unless DELETE | | no | `seq_.control(a8, b8, sclk_s, safe_by_fdir_)`; EVR 0x0902 / 0x0903 |
| 0x1F | MAGBIST | unit u8 0..2 (OB/OUTBOARD 0, IB/INBOARD 1, BOTH 2) | | | yes | mask = `magbist_unit_mask(a8)` & fitted units; none fitted → CE_HARDWARE; pending already → CE_BUSY; else run at the next PA-off window with lamps off; result → HK `mag_bist`, EVR 0x0910/0x0911 |
| 0x20 | MAGCAL | 19 octets: unit u8 0..1, off_x/off_y/off_z i16 (0.01 uT, abs ≤ 30000), m11/m22/m33 i16 8192..32767 (Q2.14), m12/m13/m23 i16 -8192..8192 | length 19 at parse; ground also checks M positive definite (exact integer Sylvester, as `magcal_parse`) | | no (20 octets > 12) | `magcal_.load(args, 19, unit)`; CmdError = `magcal_cmd_error()`; EVR 0x0912 (`magcal_evr_text`) / 0x0913 (`magcal_status_text`) |

Hazardous set (unchanged): 0x04, 0x08, 0x0C, 0x0E, 0x0F, 0x10, 0x14, 0x15. Sequence-forbidden
(flight, unchanged): hazardous ∪ {0x0C, 0x1D, 0x1E} = {0x04, 0x08, 0x0C, 0x0E, 0x0F, 0x10, 0x14,
0x15, 0x1D, 0x1E}. None of 0x16..0x20 is hazardous, so the flight list needs no edit and
`test_flight_forbidden_list_matches_dictionary` keeps passing (C-05).

TC packet sizes: SEQLOAD and MAGCAL are 26-octet packets (6 + 1 + 19): they fit TC v1 frames
with AE (≤ 27, §5) but not USLP + AE (≤ 23) — C-08.

### 2.4 Ground dictionary model (WS3)
`CommandDef(mnemonic, opcode, args, hazardous=False, desc="", check=None, check_doc="",
builder_only=False)`:
* `args`: `(name, struct_code, lo, hi, enum | None)` as today (`enum` maps upper-case labels to
  values; a label value must itself pass `lo..hi`). Argument names exactly as in the table
  (SPECCFG: `gain, cycles, lamps, led_cur, ind`; CFDPPUT: `source, max_kib`; RADIOOPT: `flags`;
  SPECMODE: `mode, n`; SPECBASE: `threshold_cdeg`; SPECDIAG: `device, n_sub`; SEQCTRL: `action,
  slot`; MAGBIST: `unit`; MAGCAL: `unit, off_x, off_y, off_z, m11, m22, m33, m12, m13, m23`).
  SPECCFG `gain` is `('gain', 'B', 0, 255, {'AUTO': 255, 'AGC': 255})` + check.
* `check(values: Mapping[str, int]) -> str | None`: error text or None; applied by
  `cmdparse._encode_args` after the range checks, therefore by `parse()` and
  `encode_dictionary()` (sequence files) alike. `check_doc` is the one-line rule text (XTCE
  LongDescription, docs/05, `/api/dictionary`).
* `builder_only = True` for SEQLOAD: listed (16 args: `slot B 0..31, flags B 0..1, t_s I,
  len B 1..12, cmd_00..cmd_11 B 0..255`) so XTCE exports it, but `parse()` refuses it ("SEQLOAD
  is built from a sequence file: web Sequences panel / cmdparse.seqload_commands") —
  autonomy hand-off.
* `GROUND_DIRECTIVES` (GDS, never uplinked as such): `GSCAN [first last]`, `WAKE`, `LINKRATE
  <code>`, `LINKCHAN <ch>`, `LINKMGR OFF|ADVISE|AUTO`, `RADIO`, `SPECREF [CLEAR]`, `SPECLIB
  SAVE|DEL <name> | SPECLIB LIST`, `TDM WRITE`, `AFC RESET`, `SDLS RESETREPLAY` (forgets the TM
  (epoch, sn) replay window; two-step confirmation like a hazardous command).
* GDS pre-uplink frame-size check (C-08): `submit_command` computes the frame length for
  `[link] tc_framing` and the SDLS TC overhead (AUTH 22, AE 30) — TC v1 `5 + ovh + 6 + len(ud)
  + 2`, USLP AD `8 + ovh + 1 + 6 + len(ud) + 2`, USLP BD/BC `7 + ...` — and REJECTS above 64 with
  "frame N > 64 octets with <framing>/<service>: use [link] tc_framing = \"TC\"". The FOP must
  never see a frame it cannot build (today `build_tc_frame` raises inside `next_cltus`).
* GDS also rejects CHANNEL / LINKCHAN values outside `[radio] allowed_channels`.

### 2.5 CFDP command behaviour (flight, §7)
* Every `CFDPPUT`: CE_BUSY + EVR 0x0A06 while `sclk_.partition == 0` (PB-1.13).
* `CFDPPUT 0` (SSR): CE_BUSY if `cfdp_tx_.active()` or `relay_gate_.state() != NONE` or
  `ssr_.playing()`; else `ssr_.begin_export()` (pauses recording; false →
  CE_BUSY), name `cfdp_ssr_file_name(partition, sclk_s)`, seq `cfdp_make_seq(partition, n)`,
  `cfdp_tx_.begin(seq, name, &ssr_src_, max_kib * 1024u)` (false → `end_export()`, CE_HARDWARE);
  EVR 0x0A00. Pacing in `loop()`: while `cfdp_tx_.active()` and `dl_.backlog(3) + 12 +
  ccsds::kCfdpMaxPduLen ≤ kCfdpVc3BacklogMax` (320; i.e. room for the largest possible next
  packet, 158 octets, so VC3 never holds more than two packets — the length of the next PDU is
  not known before `next_pdu()` builds it), at most one PDU per pass:
  `n = cfdp_tx_.next_pdu(buf, sizeof buf)` → `emit(APID_CFDP, buf, n, VC_FILE, false)`. When the
  sender returns to idle: `ssr_.end_export()`, EVR 0x0A01 with `last_condition()`, octets and
  `paused_drops()` delta.
* Relay authorisation (`RelayGate relay_gate_`, pure, `datalink.h`, PB-8.2; host-tested). States
  NONE / REQUESTED / ACTIVE / CANCELLING with the one authorised `seq` and a timestamp. RAM only,
  so after an MCU reboot it is NONE and any still-running EGSE transfer is refused.
  - `CFDPPUT 1|2` (EGSE event log / latest CADU archive): CE_BUSY if `cfdp_tx_.active()` or the
    gate is not NONE; else seq = `kCfdpEgseSeqFlag | cfdp_make_seq(partition, n)`,
    `relay_gate_.request(seq, now)` (→ REQUESTED), `Bridge.notify("cfdp_req",
    "<source>,<seq>,<max_kib>")` (decimal), EVR 0x0A03.
  - `cfdp_pdu` RPC (PB-4.1): `relay_gate_.admit(pdu, n, now)` returns −1 unless
    `CfdpRelayMonitor::acceptable(pdu, n)` AND the PDU's seq equals the authorised seq AND
    (state REQUESTED or ACTIVE, or state CANCELLING and the PDU is an EOF). Admitted: queue,
    `relay_.observe()`, REQUESTED → ACTIVE; an admitted EOF → NONE (EVR 0x0A05).
  - `CFDPCANCEL`: SSR transfer → `cfdp_tx_.cancel()` (next PDU = EOF condition 15); gate
    REQUESTED/ACTIVE → `Bridge.notify("cfdp_cancel", "<seq>")`, gate → CANCELLING (so the EGSE's
    EOF(cancel) still reaches the ground); EVR 0x0A02 says what was cancelled ("nothing active"
    otherwise). Always CE_OK.
  - `relay_gate_.poll(now)` once per pass: REQUESTED for `kRelayStartTimeoutMs` (30 s) without a
    PDU → NONE + EVR WARN 0x0A04 "EGSE did not start within 30 s"; ACTIVE and
    `relay_.expired(now)` → `relay_.reset()`, NONE + EVR 0x0A04 "silent, expired"; CANCELLING for
    30 s without the EOF → NONE + EVR 0x0A04 "cancelled, no EOF within 30 s".
  - Refused PDUs are counted (`relay_ref` in `sc_status_x`), no EVR (an EGSE retry loop must not
    flood VC0).
* HK: `cfdp_state` 1 while `cfdp_tx_.active()`, 2 while the gate is REQUESTED/ACTIVE/CANCELLING,
  else 0; `cfdp_seq`/`cfdp_progress_permille` from the active one, else the last one.

### 2.6 EVR event-ID map (complete; flight WS2, ground WS3/WS4)
Severity 0 DIAG 1 INFO 2 WARN 3 ERROR 4 FATAL. Keep literal IDs in `evr(sev, 0x....` calls
(tools/gen_dictionary_doc.py parses them). `evr()` order:
1. `beacon_.note_evr(sev, id)` FIRST (before any filter).
2. `sc_evr` to the EGSE (PB-4.1) for every EVR, subject only to the notification rules of
   PB-0.4 (≤ 2 per pass, ≤ 8 per second, UART room; excess counted `evr_nd`).
3. Level filter (`evr_level_`, default 1 = INFO).
4. Routine-EVR budget (`EvrGate`, PB-8.2, host-tested) for severity < 3: a token bucket of at
   most 10 EVRs refilled continuously at `LoadPlan::evr_milli_per_s` (PB-3.3: the capacity left
   by the stretched periodic packets up to 95 %, never more than the v2 rate of 10 per
   minute). Severity ≥ 3 is never
   throttled. **Exempt** from the budget: every EVR raised during `setup()` (the boot sequence
   of PB-8.6 item 2 emits ten with severity < 3 — 0x0608, 0x0306, 0x0910, 0x0911,
   0x0706/0x0707, 0x0011, 0x0001, 0x0003, 0x0004, 0x0002 — and with the v2 budget of 10 would
   exhaust it before EVR 0x0120 "epoch accepted" arrives seconds later), 0x0120, 0x0121, 0x0122
   and the SPECDIAG lines 0x0703. The bucket starts full when `setup()` returns.
5. Downlink push (`Downlink::push`, priority for severity ≥ 2 and 0x0120..0x0122, PB-1.2).
SPECDIAG pacing: at most one 0x0703 line per loop pass and only while `dl_.backlog(0) + 95 ≤
512`; the driver keeps the next line until `pop_diag_line()` is called (`build_diag()` returns
false while a line is waiting, `as7265x.cpp`), so pacing only delays the dump (≤ 9 lines: one
header + `ceil(n_sub / 4)` COEF lines of 4 words, `as7265x.cpp` `build_diag`).
Every text's worst-case expansion is ≤ 80 characters (`pack_evr` truncates at 80): the WS2
report lists each format with its worst-case arguments and length.

| ID | Sev | Text (printf) | Trigger |
|---|---|---|---|
| 0x0001 | 1 | `BOOT VGQ-1 FSW 3.0 rst=%u %s radio=%s %s TC %s TM %s` (reset code + name, radio model, framing TM/USLP, SDLS services; worst case 74 characters) | setup |
| 0x0002 | 1 | `DE %s` | boot + every kCallsignPeriodS |
| 0x0003 | 1 | `SSR capacity %lu KiB` | setup |
| 0x0004 | 1 | `stored sequence table and MAGCAL are RAM only: empty after boot` | setup (§29) |
| 0x0005 | 2 | `Bridge registration failed: %s` (failed names, e.g. "begin cfdp_pdu") | setup, only on failure (PB-8.6 item 8) |
| 0x0011..14 | 1 | `MODE %s -> %s (%s)` | set_mode |
| 0x0101..07 | 2 | `TC frame rejected: %s` (TcParseResult name) | tc_accept FRAME/TFDF stage |
| 0x0111..16 | 3 | `SDLS rejected TC frame: %s` (`Sdls::result_name`) | tc_accept SDLS stage; FDIR_SDLS_AUTH |
| 0x0120 | 1 | `SDLS TM epoch %u: downlink protected` | first/new epoch accepted |
| 0x0121 | 2 | `SDLS TM: no epoch after %u s: null SA (clear) until the EGSE sends one` | `poll_epoch_timeout()` true (§5) |
| 0x0122 | 2 | `SDLS TM epoch %u refused (current %u)` | set_partition lower/0 |
| 0x0201..06 | 1/0/3/2 | COP-1 events (unchanged texts) | FARM verdicts |
| 0x0210 | 1 | `radio change confirmed by ground (code %u, ch %u)` | |
| 0x0211 | 2 | `AIR RATE -> code %u in 2 frames; revert in %u s without uplink` | AIRRATE |
| 0x0212 | 2 | `CHANNEL -> %u (%.3f MHz) in 2 frames; revert in %u s without uplink` | CHANNEL |
| 0x0213 | 1 | `RADIOOPT LBT/CAD %s, Rx boost %s` | RADIOOPT |
| 0x0214 | 1 | `TXPWR code %u = %d dBm (%s)` | TXPWR |
| 0x0215 | 1 | `downlink load: packet periods x%u.%02u (capacity %lu B/min)` | `LoadPlan::stretch_x100` changed by ≥ 10 % (PB-3.3) |
| 0x0301 | 3 | `radio TX timeout (%lu ms) - TX path suspect` | TX_TIMEOUT; FDIR_AUX_TIMEOUT |
| 0x0302 | 1 | `RF survey CH%u..CH%u starting (downlink paused)` | RFSCAN (was 0x0700) |
| 0x0303 | 1 | `RF survey done in %lu ms: quietest CH%u (%d dBm)` | (was 0x0701, §29) |
| 0x0304 | 3 | `radio fault %u (%s) - recovering` | FAULT 16..19; FDIR_RADIO_CFG |
| 0x0305 | 1 | `radio recovered (%u recoveries)` | FAULT 0 (cleared) |
| 0x0306 | 1/3 | `radio %s %s: ch %u code %u pwr %d dBm sens %.1f dBm %s` (model, OK/FAILED, ..., sensitivity source; worst case 77 characters) | boot + every configure() |
| 0x0307 | 1 | `uplink with LoRa CRC error rescued by BCH/FECF/SDLS (%u total)` | §20 flight side (INFO so it passes the default level 1; the running count is also `tc_rescued` in `sc_status_x`) |
| 0x0311..14 | 3 | `E22 reports %s - module RF output disabled` | T37S abnormal status |
| 0x0315 | 1 | `E22 abnormal status cleared (%u reports)` | |
| 0x0401 | 1 | `SSR playback %s (%lu packets)` | |
| 0x0402 | 1 | `SSR playback complete` | |
| 0x0501..07 | 2 | `command 0x%02X rejected, error %u` | parse failure |
| 0x0601 | 3 | command loss (unchanged) | |
| 0x0602 | 3 | radio change not confirmed - reverting | |
| 0x0603 | 1 | transmitter re-enabled | |
| 0x0604 | 4 | PA over-temperature: TX off (T37S) | |
| 0x0605 | 3 | `I2C bus fault (%u consecutive errors) - bus clear + re-init` | FDIR |
| 0x0606 / 0x0607 | 3 / 1 | Nicla Sense ME lost / responding again | |
| 0x0608 | 2 | `brownout reset: TX power derated (code %u) - check radio supply` | §15 |
| 0x0609 | 1 | `I2C bus healthy again: FDIR I2C_BUS cleared` | 10 s without errors after recovery |
| 0x060A | 3 | `I2C bus clear: SDA still low after 9 clocks` | bus clear failed |
| 0x0701 | 1 | `spectral event: angle %.2f deg (threshold %.2f)` | SpecEvents.spectral_event (§13.3) |
| 0x0702 | 1 | `light source %s -> %s (%u %%)` | class_changed |
| 0x0703 | 1 | SPECDIAG line (driver text ≤ 80) | |
| 0x0704 | 1/2 | `BURST START n=%u s cycles=%u` / `BURST END samples=%u rate=%.1f Hz dropped=%u readmax=%u ms tx=%u` (tx = frames transmitted while the burst ran, C-26) / `BURST rice status %u` (2) | |
| 0x0705 | 2 | `AS7265X recovered by soft reset` | `take_recovered()` |
| 0x0706 | 1 | `AS7265X HW %02X/%02X FW %u.%u.%u devices %s` | boot / recovery identification (§13.1) |
| 0x0707 | 2 | `AS7265X %s` (ABSENT / FAULT / LAMP STATE UNKNOWN / available again) | driver state change |
| 0x0800 / 0x0801 | 2 | HIBERNATE / WAKE (unchanged) | |
| 0x0900 | 1 | `SEQ slot %u loaded: %s %lu s op 0x%02X%s` | SEQLOAD ok (" replaced" suffix) |
| 0x0901 | 2 | `SEQLOAD refused: %s (cmd error %u)` | |
| 0x0902 / 0x0903 | 1 / 2 | `SEQCTRL %s` / `SEQCTRL %s refused: %s` | |
| 0x0904 | 1 | `SEQ slot %u: opcode 0x%02X` | dispatch |
| 0x0905 | 2 | `SEQ slot %u skipped: %lu s late` | SKIPPED_LATE |
| 0x0906 | 1 | `sequence COMPLETE` | |
| 0x0907 | 2 | `sequence STOPPED by fault protection` | |
| 0x0910 / 0x0911 | 1 or 2 | `mag_bist_evr_text()` OB / IB (`mag_bist_evr_severity`) | boot + MAGBIST |
| 0x0912 / 0x0913 | 1 / 2 | `magcal_evr_text()` / `MAGCAL refused: %s` | MAGCAL |
| 0x0A00 | 1 | `CFDP SSR export %s: %lu B seq %lu` | CFDPPUT 0 |
| 0x0A01 | 1 | `CFDP EOF cond %u: %lu/%lu B, %lu not recorded` (the file name is in 0x0A00) | SSR sender idle |
| 0x0A02 | 1 | `CFDP cancel: %s` | CFDPCANCEL |
| 0x0A03 | 1 | `CFDP relay requested: source %u seq %lu max %u KiB` | CFDPPUT 1/2 |
| 0x0A04 | 2 | `CFDP relay seq %lu: %s` ("EGSE did not start within 30 s" / "silent, expired" / "cancelled, no EOF within 30 s") | `RelayGate::poll` |
| 0x0A05 | 1 | `CFDP relay seq %lu EOF cond %u` | relayed EOF admitted |
| 0x0A06 | 2 | `CFDPPUT refused: no SCLK partition from the EGSE yet` | CFDPPUT while partition 0 (PB-1.13) |

Beacon consequences (autonomy): 0x0701/0x0702 → INTERESTING; any sev ≥ 2 → IMPORTANT until the
next ground contact (commanded WARN EVRs are acknowledged by the contact that caused them because
`on_ground_contact()` runs after `execute()`).

---------------------------------------------------------------------------------------------------

## 3 Configuration surface (§9, §2, §5, §6, §7, §13, §15, §19, §24)

### 3.1 Flight `spacecraft/vgq1_flight/sketch/src/config.h` — final content (WS2)
Order matters: the model macros must be defined before any `#if VGQ_RADIO == ...` (radio report:
an `#if` on undefined macros compares 0 == 0). Every pin/bus item stays documented in docs/03.

| Item | Final value | Source / note |
|---|---|---|
| `VGQ_RADIO_E22_T33D` / `_E22_T37S` / `_SX126X` | 1 / 2 / 3 (`#ifndef` guarded) | §9, radio.h |
| `VGQ_RADIO` | `VGQ_RADIO_E22_T33D` (`#ifndef`) | §9 default T33D |
| `VGQ_SX126X_CHIP` | 1268 (`#ifndef`; 1262 allowed) | §2.2 |
| `VGQ_RADIO_ON_SERIAL1` / `RADIO_SERIAL` | 0 / `Serial3` (USART3 PB11 RX / PB10 TX) | §1.1 unchanged |
| `PIN_E22_M0` / `PIN_E22_M1` / `PIN_E22_AUX` | 2 (PB3) / 3 (PB0) / 7 (PB2) | §1.1; comment: T33D DIP pins and T37S TBH-02 header |
| `PIN_SX_NSS`, `PIN_SX_BUSY`, `PIN_SX_DIO1`, `PIN_SX_NRST`, `PIN_SX_RXEN`, `PIN_SX_TXEN` | `static const int` 10 (PB9), 4 (PA12), 5 (PA11), 6 (PB1), 8 (PB4), 9 (PB8); -1 = not wired for RXEN/TXEN | §1.1; UNO Q variant overlay digital-pin-gpios confirms D4 PA12, D5 PA11, D6 PB1, D8 PB4, D9 PB8, D10 PB9 |
| `kSxTcxoV` | 1.8f (VERIFY module datasheet) | §2.2 |
| `kSxDio2RfSwitch` | false (VERIFY wiring) | §2.2 |
| `kSxPaGainDb` | `int8_t` 11 (VERIFY T-RF-07) | §2.2 |
| `kSxOcpMa` | 140.0f (SX1262 value; VERIFY SX1268) | §14 |
| `kRadioChannel` | 23 (433.125 MHz) | §2 |
| `kRadioRateCode` | `(VGQ_RADIO == VGQ_RADIO_SX126X) ? 1 : 2` | §9 |
| `kRadioPowerCode` | 0 | §9 |
| `kBrownoutPowerCode` | 2 | §15 |
| `kRadioLbt` / `kRadioRxBoost` | false / true | §9 |
| `kE22Address` / `kE22NetId` | 0x0000 / 0x00 | v2 |
| deleted | `kE22Channel`, `kE22AirRate`, `kE22PowerCode`, `kAuxTimeoutMs` (now `kE22AuxTimeoutMs` in radio.h) | radio report |
| `VGQ_DOWNLINK_FRAMING` | 0 (TM 132.0-B-3); 1 = USLP (`#ifndef`) | §6 |
| `VGQ_SDLS_TC` | 2 (AE); 1 AUTH, 0 NONE (`#ifndef`) | §5 |
| `VGQ_SDLS_TM` | 2 (AE); 0 NONE (`#ifndef`); `static_assert(VGQ_SDLS_TM == 0 \|\| VGQ_SDLS_TM == 2)` | §5 (AUTH not defined for TM) |
| `kSdlsEpochWaitS` | 120 | §5 |
| `VGQ_SDLS_SPI` | from the generated `sdls_key.h` (AUTH SPI, default 1) | v2 |
| `SENSOR_WIRE` | `Wire2` (I2C3, A4 = SDA PC1, A5 = SCL PC0, 3.3 V only) | §1, §16.1 |
| `kPinSensorSda` / `kPinSensorScl` | 18 (A4, PC1) / 19 (A5, PC0) — bus-clear GPIOs (PB-7 AS-T6) | variant overlay: index 18 = PC1, 19 = PC0 |
| `kI2cClockHz` | 100000 | v2 |
| `kI2cClearHalfPeriodUs` / `kI2cClearMaxPulses` | 5 / 9 | AS-T6 fix text |
| `kRm3100ObAddr` / `kRm3100IbAddr` | 0x20 / 0x23 | v2 |
| `kRm3100ToBody` | `static const float[3][3]` identity (VERIFY mounting) | PB-5 ATT/TRIAD with the boom magnetometer |
| `kMagCollectTimeoutMs` | 30 (DRDY must arrive within; bounded wait across loop passes) | F-60, PB-8.6 item 3 (non-blocking sampling) |
| `kSpecLampDeferMaxMs` | 2100 (max wait of an RM3100 sample for the lamps to go dark; then sample anyway and set MAG b4) | **engineering value, VERIFY T-SC-08**: just above the driver's own lamp-watchdog limit for the longest single exposure, `conv + conv/32 + kSpecDrdyMarginMs + kSpecLampMarginMs` = 1424 + 44 + 50 + 500 = 2018 ms (conv = one-shot at N = 255: 2 x 256 x 2.78 ms, `spec_conversion_ms`; `as7265x.cpp` line 976), so a deferral never ends while the driver may legitimately still have a lamp lit; `lamp_state_unknown()` (FAULT) can last longer — then MAG b4 marks the sample |
| `kAruMagUtPerLsb` | 0.1f | §22.3 |
| `kPinSpecInt` | -1 (INT not wired; DATA_RDY polling) | §19 |
| `kSpecDefaultGain` / `kSpecDefaultCycles` | 255 (AGC) / 59 (166.8 ms) | spectral.h SpecConfig defaults |
| `kSpecBurstCycles` | 1 (5.56 ms per bank, C-15) | §13.2 |
| `kSpecBaselineAlpha` / `kSpecEventCdeg` | 0.1f / 500 | §13.3 |
| `kSpecBoresightBody` | {0, 0, 1} (VERIFY mounting) | §13.3 |
| `kCfdpVc3BacklogMax` | 320 (pacing rule `backlog(3) + 12 + kCfdpMaxPduLen ≤ 320`, PB-2.5) | §7 pacing |
| `kEvrNotifyMaxPerS` / `kEvrNotifyMaxPerPass` | 8 / 2 | PB-0.4 notification rules |
| `VGQ_DEBUG_MONITOR` | 0 (`#ifndef`); 1 = `LOGF` to `Monitor` for bench debugging only (can block, PB-0.4) | PB-0.4 |
| `kSsrMaxBytes` / `kSsrMinBytes` / `kHeapReserveBytes` | 262144 / 16384 / 32768 (the reserve also covers the transient Bridge allocations, PB-0.4) | v2 |
| `kMinFramePeriodMs` / `kMaxTxDutyPermille` | 1500 / 500 (used by `frame_period_ms()`, now in `datalink.h`, PB-3.3) | v2 |
| `kNoiseSampleMs` / `kDefaultCmdLossS` | 15000 / 86400 | v2 |
| `kPaCooldownS` / `kBusTempRedC` | 300 / 75.0f | v2 |
| `kGeomagInclDeg` / `kGeomagDeclDeg` | 60.0f / 0.0f (site values) | v2 |
| `VGQ_CALLSIGN` / `kCallsignPeriodS` | "N0CALL" / 600 | §5 OID idle text "DE <callsign> VGQ-1 " |
| `kI2cBusHealthyS` | 10 (seconds without errors before FDIR_I2C_BUS clears) | §29 |
| `kRelayStartTimeoutMs` | 30000 | PB-2.5 |

Comments to update: the "E22-400T37S control pins" block names both E22 models; "PA temperature
comes from the E22 itself" applies to the T37S only; delete "Nicla Sense Env".

### 3.2 Ground `ground/config/station.toml` — final keys and defaults (WS4)
`config.DEFAULTS` mirrors this file exactly. Merge rule (fix): a user table updates the defaults
key by key, **including nested tables** (`[radio.sx126x]`, `[radio.sensitivity_measured]`,
`[spectral.classifier]`): two-level merge, so a partial nested table keeps the other defaults.
`load_config()` then calls `validate(cfg)` which raises `ValueError` naming the key.

| Table.key | Default | Validation | Source |
|---|---|---|---|
| station.name / latitude_deg / longitude_deg / altitude_m / antenna / antenna_gain_dbi / rx_line_loss_db | "DSS-Q1" / 0.0 / 0.0 / 0.0 / "433 MHz Yagi" / 12.0 / 1.0 | lat ±90, lon ±180, finite | v2 |
| spacecraft.name / scid / distance_km / antenna_gain_dbi / tx_line_loss_db | "VGQ-1" / 0x0A7 / 0.0 / 2.15 / 0.5 | scid == 0x0A7 (ground constant), distance ≥ 0 | v2 |
| radio.model | "E22-400T33D" | `link.get_model()` | §9 |
| radio.port | "/dev/ttyUSB0" | E22 only | v2 |
| radio.address / netid / parity / subpacket / fixed / relay / wor_role / wor_cycle | 0 / 0 / 0 / 0 / false / false / false / 0 | `e22.regs_from_cfg` ranges | v2 |
| radio.uart_code | 3 | **must be 3 (9600) for E22 models** (CONFIG mode is always 9600; §27) | §27 |
| radio.air_rate | 2 (E22; SX126x users set 1 = SF12/62.5 kHz) | 0..7 | §2 |
| radio.power | 0 | 0..3 (T33D 33/30/27/24 dBm VERIFY T-RF-07; T37S all 37; SX126x drive 22/17/14/10 + pa_gain_db) | §2 |
| radio.channel | 23 | 0..83 and ∈ allowed_channels | §2 |
| radio.rssi_noise / rssi_byte | true / true | E22 | v2 |
| radio.fault_log | false (T37S only; ignored on the T33D) | bool | radio report |
| radio.lbt | false | bool | v2 |
| radio.noise_bw_hz | 125000.0 (E22 assumption; SX126x uses the profile BW) | > 0 | v2 |
| radio.noise_poll_s | 20.0 | ≥ 5 | v2 |
| radio.radio_latency_s | 0.0 (VERIFY T-TM-04) | 0..10 | v2 |
| radio.rssi_cal_offset_db | 0.0 (T-RF-02) | ±30 | v2 |
| radio.allowed_channels | [20, 21, 22, 23, 24] | each 0..83 | v2 |
| radio.freq_offset_cal_hz | 0.0 | ±50000 | §2.2 |
| radio.freq_offset_calibrated | false (true only after the T-RF frequency calibration; TDM DOPPLER only when true) | bool | §29 products |
| radio.sx126x.chip | "SX1268" | "SX1268" or "SX1262" | §2.2 |
| radio.sx126x.tcxo_v | 1.8 | 0 or 1.6..3.3 | §2.2 |
| radio.sx126x.rx_boosted_gain / cad_before_tx | true / true | bool | §2.2 |
| radio.sx126x.sync_word / preamble | 0x12 / 12 | must equal flight `kSxSyncWord`/`kSxPreambleSym` (12) | §2.2 |
| radio.sx126x.pa_gain_db | 11.0 | 0..20 | §2.2 |
| radio.sx126x.afc | true | bool | §24.8 |
| radio.sensitivity_measured | {} | `link.parse_measured` ("MODEL:code" = dBm, -160..-60) | §2.3 |
| link.tm_framing | "auto" | "auto" / "TM" / "USLP" | §6 |
| link.tc_framing | "TC" | "TC" / "USLP" | §6 |
| sdls.tc_service / tm_service | "AE" / "AE" | NONE/AUTH/AE; tm: NONE/AE | §5 |
| sdls.key_file | "config/sdls_key.hex" — host: relative to the working directory (`ground/`, as v2). **App: always `<App>/data/sdls_key.hex`** unless the operator's `data/station.toml` gives an ABSOLUTE path (a relative value is ignored in App mode, PB-6.2 step 3) | missing file → both NONE (§5 "without a key file both ends run NONE and say so") + YELLOW alarm reason "SDLS key file not found: <path>" (PB-5.5) | §5 |
| sdls.spi | 1 (AUTH SPI = flight `VGQ_SDLS_SPI`) | 1..65535 | v2 |
| sdls.accept_null | true (null-SA frames accepted, flagged UNPROTECTED) | bool | §5 |
| sdls.enabled | **removed**; if present: WARN "replaced by tc_service/tm_service" and ignored | | §5 |
| cfdp.inactivity_s / check_s / files_dir / max_file_mib | 600 / 60 / "files" (under archive_dir) / 64 | > 0 | §7 |
| spectral.white_ref_file / library_file | "spec_white_ref.json" / "spec_library.json" (under archive_dir) | plain file names | §13.7 |
| spectral.classifier | {} (threshold overrides, `ClassThresholds.from_mapping`) | | §13.7 |
| science.r_outboard_m / r_inboard_m | 1.0 / 0.5 | > 0 | v2 |
| science.field_ut | **absent** (no silent default; MAGCAL fit refuses without it) | 1..800 | §29 products |
| tdm.auto_write | true (write at LOS) | bool | §24.7 |
| linkmgr.mode / target_margin_db / period_s / holdoff_s / min_rate / max_rate / revert_s | "ADVISE" / 6.0 / 60 / 600 / 2 (SX126x: 0) / 4 / 180 | rates 0..7, min ≤ max | v2, radio report |
| interop.tm_frames_udp / tm_packets_udp / tc_frames_udp_listen | "" | "host:port" | v2 |
| gds.archive_dir | "archive" (host: relative to the working directory; App: a relative value is resolved against `<App>/data`, PB-6.2) | | v2 |
| gds.http_host / http_port | "127.0.0.1" / 8080 (App: "0.0.0.0", PB-6.2 step 3) | port 1..65535 | v2, §23 |
| gds.http_token | "" (required when http_host is not loopback: POST endpoints refuse without it, PB-5.6) | | v2 |
| gds.los_timeout_s | 90.0 = the MINIMUM; the GDS uses `max(los_timeout_s, 3 x P)` where P = the longest expected frame period of the spacecraft's current radio model/code = 4.0 (SAFE factor `kModePeriodX10`) x the busy-time estimate (`link.busy_s(model, code)`, PB-5.3.3) — e.g. SX126x profile 0: 3 x 138.6 s = 416 s, profile 1: 208 s; E22 2.4k: 15.2 s → 90 s. The effective value is in the snapshot (`rx.state.los_timeout_s`) | > 0 | v2 |
| gds.frontpanel / rcu | true / true (both need `arduino.router_bridge` / `arduino.app_utils`; when the import or the connection fails the Runner logs WARN and runs without them; sim mode never builds a bridge, PB-8.4) | bool | v2 |
| gds.router_address | "" (host: library default unix:///var/run/arduino-router.sock; App: `APP_SOCKET`) | | v2, PB-6.1 |
| gds.console_interval_s | 10.0 | | v2 |
| commanding.t1_s / tx_limit / window / verify_timeout_s | 45.0 / 3 / 5 / 300.0 — `t1_s` and `verify_timeout_s` are MINIMA: the FOP uses `max(t1_s, 2 x P)` and `max(verify_timeout_s, 4 x P)` with P as for `los_timeout_s` (SX126x profile 0: t1 ≥ 277 s, verify ≥ 554 s), event INFO when the derived value is in force | | v2 |
| commanding.require_two_person_for_hazardous | false | bool | v2 |
| commanding.auth_token | "" (when set, `/api/confirm` with role AUTH also needs header `X-DSSQ-Auth`) | | PB-5.6 |

Comment fixes in station.toml: air_rate/power comments per model (radio report); `fault_log`
T37S only; two-person comment → "second operator authorises with the web AUTH button"; `[sdls]`
regulatory note (§5: encryption generally not permitted on amateur frequencies; use AUTH/NONE).
`tools/gen_sdls_key.py` final message: "set [sdls] tc_service/tm_service" (not `enabled`); "host
GDS: uses ground/config/sdls_key.hex; ground App Lab app: copy this file to <App>/data/sdls_key.hex
(the App ignores a relative key_file)"; and "a new key allows the EGSE boot counter to restart;
with an old key it must never go back" (PB-6.3). Docstring updated the same way (it still says
`[sdls] enabled = true`). WS4 test: `gen_sdls_key.main()` with `GROUND`/`FLIGHT` monkeypatched to
a temp dir writes both files (0600 for the hex) and prints the App path hint.

### 3.3 Downlink load control (flight; pure functions in `datalink.h/.cpp`, WS1; used by WS2)
Problem (measured with the flight formulas, 236-octet CADU, data field 153 octets = USLP + AE,
the smallest; v2 cadence table and frame-period rule): the steady-state packet load exceeds the
downlink capacity for the SX126x profiles 0..4 — e.g. profile 0 SAFE 3.7 x / TEST 19 x, profile 1 SAFE 1.85 x /
CRUISE 1.73 x / ENCOUNTER 7.5 x, profile 2 ENCOUNTER 3.8 x, profile 4 TEST 1.19 x — so VC0/VC1
would grow until they overflow (profile 1 SAFE: HK 90 octets / 30 s + RF 61 / 60 s + TIMECORR
22 / 300 s = 4.09 octet/s against 153 octets per 69.3 s frame period = 2.21 octet/s). Every E22
case without the T37S PA over-temperature back-off is ≤ 0.70 x (worst: TEST at codes 0..2 with
USLP + AE, 0.6954 x). Rule: the periodic
packet periods are stretched at run time so that their load stays ≤ 75 % of the capacity (so
no E22 case changes from v2); routine EVRs get what remains up to 95 % (never more than the v2
10 per minute); the last 5 % plus the 256-octet VC0 priority reserve (PB-1.2) absorb CMDVER,
error EVRs, TIMECORR on request and bursts.
```cpp
enum PeriodicIdx : uint8_t { PER_HK = 0, PER_RF, PER_TIMECORR, PER_MAG, PER_ATT, PER_SPEC, PER_ENV,
                             kNumPeriodic };
extern const uint16_t kModeCadence[5][kNumPeriodic];  // s, 0 = off — the v2 kCadence table moved
                                                      // unchanged from flight.cpp
extern const uint8_t  kModePeriodX10[5];              // v2 kPeriodX10 {40, 40, 25, 20, 20}, moved
extern const uint16_t kPeriodicPktLen[kNumPeriodic];  // whole Space Packets: 90, 61, 22, 58, 49, 141, 36
static const uint16_t kPeriodicLoadPermille = 750;
static const uint16_t kLinkUsePermille = 950;         // periodic + routine EVRs together
static const uint16_t kEvrNominalOctets = 80;         // 12 + 3 + ~65 text
static const uint32_t kEvrMaxMilliPerS = 167;         // never more than the v2 10 per minute
// v2 rule, unchanged, now pure: busy x kModePeriodX10[mode] / 10, x2 with PA over-temperature,
// >= busy / kMaxTxDutyPermille, >= kMinFramePeriodMs, >= the FPERIOD command value.
uint32_t frame_period_ms(uint32_t busy_ms, uint8_t mode, bool pa_overtemp, uint16_t cmd_period_ms);
struct LoadPlan {
  uint16_t stretch_x100;       // >= 100; effective period = ceil(period x stretch / 100), <= 65535 s
  uint32_t evr_milli_per_s;    // routine-EVR refill, 1000 = one EVR per second
  uint32_t capacity_octets_per_min;
};
// period_s = the periods in force (table or PKTRATE override, 0 = off); capacity C =
// data_field_octets / frame_period; load L = sum(kPeriodicPktLen / period). stretch =
// max(100, ceil(100 x L / (0.75 x C))), saturating at 65535; evr = min(kEvrMaxMilliPerS,
// (0.95 x C - L x 100 / stretch) x 1000 / kEvrNominalOctets), which is at least
// min(kEvrMaxMilliPerS, 0.20 x C x 1000 / 80) by construction.
// Integer arithmetic only (octets x 1000 / ms); no float.
LoadPlan plan_downlink_load(const uint16_t period_s[kNumPeriodic], uint32_t frame_period_ms,
                            uint16_t data_field_octets);
```
Executive (WS2): recompute the plan in every 1 Hz tick from `frame_period_ms(...)` and
`dl_.data_field_len()`; apply `stretch_x100` to every periodic packet (table periods and PKTRATE
overrides alike — an override cannot exceed the link either) and `evr_milli_per_s` to the
`EvrGate`; EVR 0x0215 when `stretch_x100` changes by ≥ 10 %. SPECBURST (event-driven) is not
paced (PB-1.2). WS1 host test `downlink_load`: for every model (T33D, T37S, SX126x), code 0..7,
mode SAFE/CRUISE/ENCOUNTER/TEST, framing x service (TM/USLP x NONE/AE), PA over-temperature on
and off: the stretched periodic load ≤ 75 % and periodic + routine-EVR rate x `kEvrNominalOctets`
≤ 95 % of the capacity; every E22 case without PA over-temperature returns stretch 100 (v2
cadence unchanged; with the T37S over-temperature back-off, period x 2, ENCOUNTER/TEST at codes
0..3 are stretched up to 1.86 x — v2 overflowed there instead and relied on FDIR_VC_CONGEST,
which is kept); SX126x profile 1 SAFE with USLP + AE returns stretch 247 (4.09 / (0.75 x 2.21)).

---------------------------------------------------------------------------------------------------

## 4 MCU ⇄ Linux Router Bridge RPC (both boards)

Facts (sources read): MCU services are registered with `Bridge.provide_safe(name, fn)` and run
in the MCU loop thread between `loop()` iterations (no locking needed). The MCU inbound request
buffer is `BRIDGE_RPC_BUFFER_SIZE = DEFAULT_RPC_BUFFER_SIZE = DECODER_BUFFER_SIZE / 4 = 256`
octets (Arduino_RPClite `request.h`, `Arduino_RPClite.h`), hence the hex-argument limits below.
Linux handlers registered with `provide` run sequentially on the bridge's dispatcher thread and
**must not call `Bridge.call`/`provide`** (app_utils `bridge.py`: RuntimeError); they only
validate and enqueue. A method name may be provided by ONE Linux client (`bridge.py`): each
board runs exactly one Linux process that provides names (the EGSE on the UNO Q, the GDS on the
VENTUNO Q). The MCU never calls `Bridge.call` (§0). MCU→Linux notifications larger than 256
octets (`tm_cadu` 472 hex chars, v2) are unverified on hardware: keep every MCU notification
≤ 480 characters (VERIFY T-HW, docs/08). Every MCU notification obeys the PB-0.4 rules (UART
room check, one large notification per pass, `sc_evr` budget), so none ever waits.

### 4.1 Spacecraft — UNO Q (MCU flight sketch ⇄ Linux EGSE `spacecraft/vgq1_flight/python/main.py`)

| Name | Direction / kind | Arguments | Return | Caller / where | Rules |
|---|---|---|---|---|---|
| `set_partition` | Linux→MCU call (provide_safe) | `int p` | `bool`: true applied; false if p ∉ 1..65534 or the SDLS TM epoch refused (lower than current) | EGSE user loop only | sets `sclk_.partition = p` and `sdls_tm_.set_epoch(p)` (same value = no-op; EVR 0x0120/0x0122) |
| `set_sdls_sn` | Linux→MCU call | `int sn` (opaque int32 two's complement of the u32 SN) | `bool` true | EGSE loop | `sdls_tc_.set_last_sn((uint32_t)sn)` — only raises |
| `hl_cltu` | Linux→MCU call | `String hex`, 2..220 hex chars (≤ 110 octets) | `bool`: false on bad hex/length | EGSE loop (hardline queue) | runs the same `tc_accept()` path as RF |
| `cfdp_pdu` | Linux→MCU call | `String hex`, 20..220 hex chars (one PDU ≤ `kCfdpRelayMaxPduLen` 110) | `int`: **1** queued, **0** busy (VC3 has no room: `dl_.backlog(3) + 12 + n > kCfdpVc3BacklogMax`, or SDLS TM waiting for an epoch), **-1** refused (bad hex or length, or `RelayGate::admit()` refuses: not well-formed per `CfdpRelayMonitor::acceptable()`, no relay requested/active, seq ≠ the authorised seq, or a non-EOF PDU while cancelling — PB-2.5) | EGSE loop | queue = `emit(APID_CFDP, pdu, n, VC_FILE, false)` then `relay_.observe(pdu, n, millis())`; idempotent for retries; the EGSE abandons a transfer on -1 |
| `sc_boot` | MCU→Linux notify | `int reset_cause` | — | end of `Flight::setup()` | EGSE must answer with a NEW partition (PB-6.3) |
| `tm_cadu` | MCU→Linux notify | `String hex` (472 chars, one CADU) | — | after every `radio_.transmit()` accepted (one pending copy when the UART has no room, PB-0.4) | EGSE archive |
| `sc_status` | MCU→Linux notify | `String json` (≤ 294 chars worst case) | — | 1 Hz (`tick_1hz`, deferred to a pass without another large notification) | core keys below |
| `sc_status_x` | MCU→Linux notify | `String json` (≤ 409 chars worst case) | — | every 5 s | extended keys below (C-25) |
| `sc_evr` | MCU→Linux notify | `String "0x%04X %s %s"` (id, severity name, text) | — | every EVR before the level filter / routine budget, ≤ 2 per pass and ≤ 8 per second (excess counted `evr_nd`) | EGSE event log (CFDP source 1) |
| `sdls_sn` | MCU→Linux notify | `int sn` (int32 two's complement) | — | after each TC frame accepted by SDLS | EGSE persists it |
| `cfdp_req` | MCU→Linux notify | `String "<source>,<seq>,<max_kib>"` decimal; source 1 or 2; seq ≥ 0x80000000 | — | CFDPPUT 1/2 execution | EGSE starts `cfdp_tx.start_request()` |
| `cfdp_cancel` | MCU→Linux notify | `String "<seq>"` decimal (C-22) | — | CFDPCANCEL with a relay requested/active | EGSE `RelayTransfer.cancel()` if seq matches |

Status JSON (all keys short, numbers unless noted; C-25). One set of 45 keys measured 503
characters with typical mid-pass values and 596 worst case, so the status is split:
* `sc_status` (1 Hz, 22 keys, worst case 294 characters): `mode` (str), `sclk`, `part`,
  `epoch`, `tm_state`, `tm_sn`, `frames`, `oid`, `mcfc`, `period_ms`, `busy_ms`, `duty_pct`,
  `model` (1..3), `fr` (0 TM / 1 USLP), `ch`, `code`, `pwr`, `lbt` (0/1), `tx` (0/1), `inhibit`
  (0/1), `hib` (0/1), `beacon`.
* `sc_status_x` (every 5 s, 29 keys, worst case 409 characters): `sclk`, `ul_rssi`,
  `ul_snr_cdb`, `noise`, `farm`, `vr`, `acc`, `rej`, `fdir`, `sns`, `vc0`, `vc1`, `vc2`, `vc3`,
  `ssr_pm`, `bus_c`, `rf_fault`, `tc_svc`, `tm_svc`, `cfdp` (state), `cfdp_seq`, `cfdp_pm`, `seq`
  (state), `tc_rescued`, `evr_nd`, `cadu_nd`, `emit_ref`, `relay_ref`, `stretch` (x100).
Formatting is a pure, host-tested function in `tlm_packets.h/.cpp` (WS1): `struct EgseStatus`
(every value above) and `size_t format_sc_status(const EgseStatus&, char* out, size_t cap)` /
`format_sc_status_x(...)` — `snprintf` into the caller's `char[481]`; returns 0 (and the caller
sends nothing) if the result would not fit. Floats are clamped before printing (`duty_pct`
0..100.0, `bus_c` -999.0..999.0, NaN → -999.0) with one decimal. WS1 host test `egse_status`:
every field at its extreme (u32 max, i16 min, 3-digit enums) gives ≤ 480 characters and valid
JSON (the test parses it back key by key); the WS1 report prints both worst-case lengths.

### 4.2 Ground — VENTUNO Q (MCU station sketch ⇄ Linux GDS)

| Name | Direction / kind | Arguments | Return | Caller (Linux side) | Notes |
|---|---|---|---|---|---|
| `fp_line` | Linux→MCU **notify** (MCU provide_safe) | `String` ≤ 60 chars: `S\|<aos 0/1>\|<alarm 0/1/2>\|<uplink 0/1>\|<beacon -1/0..3>` or `Q\|<confirm id or -1>` | (bool, ignored for notify) | `FrontPanel` tx thread, 1 Hz | 5th S field is NEW (PB-6.4); sketches that read only 4 fields keep working |
| `rcu_mode` | Linux→MCU call | `int m` 0..3 (M1:M0) | `int` 1 = AUX ready, 0 = still busy or m out of range | GDS radio thread (`RadioManager` via `FrontPanel.rcu()`) | blocks the station MCU loop ≤ ~1 s (AUX wait); E22 builds |
| `rf_tx` | Linux→MCU call | `String hex` 2..224 chars (≤ 112 octets) | 1 queued, 0 queue full (4 slots), -1 rejected, -2 radio down | GDS radio thread (`Sx126xBridge.write`) | SX126x build |
| `rf_wake` | Linux→MCU call | as rf_tx | as rf_tx | radio thread (`RadioManager.wor_transmit`) | 32-symbol preamble |
| `rf_cfg` | Linux→MCU call | `int ch, int code, int pwr, int flags` (-1 keep; flags b0 CAD before TX, b1 Rx boost) | 1 applied, 2 queued, -1 range | radio thread | |
| `rf_noise` | Linux→MCU call | — | `int` 0.1 dBm median of 8 GetRssiInst, -32768 n/a | radio thread (`noise_dbm()`) | |
| `rf_cad` | Linux→MCU call | — | 1 started, 0 busy, -2 down | radio thread | result arrives as an `rf_evt` C line |
| `rf_foff` | Linux→MCU call | `int hz`, \|hz\| ≤ 50000 | 1 applied, -1 range, -2 radio down | radio thread (AFC `confirm`) | §24.8; provided by the RadioLib workstream (public API = committed + rf_foff) |
| `fp_in` | MCU→Linux notify | `String`: `K\|<typed>`, `CMD\|<text>`, `CONFIRM\|<id>`, `H\|<fw>\|<rcu 0/1>\|<kb 0/1>` (5 s) | — | handler appends to `FrontPanel` inbox; `fp-rx` thread acts | kb2 removed (§27) |
| `rf_rx` | MCU→Linux notify | `String hex, int rssi_ddbm, int snr_cdb, int freq_err_hz, int crc_ok` | — | `Sx126xBridge._on_rf_rx` validates + enqueues | today "" when crc_ok = 0; after G1 the CADU-length payload is kept (§20, F-40, WS6b) |
| `rf_evt` | MCU→Linux notify | `String` `S\|...` (19 fields, 5 s), `C\|det\|ms`, `T\|ok\|ms\|octets`, `E\|code\|text` | — | `Sx126xBridge._on_rf_evt` | |

Linux-side single client: `display/frontpanel.py` provides one adapter used by the FrontPanel,
the SX126x backend and the RCU (PB-8.4 `BridgeLink`). Host mode: `arduino.router_bridge.Bridge(
address or DEFAULT_ADDRESS)`; App mode: `arduino.app_utils.Bridge` (connects with `APP_SOCKET`).
The radio thread is the GDS main loop (`Runner.step`); the web thread never calls the bridge.
`BridgeLink` converts every library failure into the exceptions the callers already handle:
`arduino.app_utils.AppError` (raised by `_get_bridge()` when the router cannot be reached within
5 s; it derives from `Exception` only) → `ConnectionError`; the router_bridge client's
`TimeoutError` / `ConnectionError` / `ValueError` / `RuntimeError` pass through unchanged. So
`Sx126xBridge._call` (catches `TimeoutError, ConnectionError, ValueError, RuntimeError, OSError`)
and the F-12 FrontPanel list never see an unexpected type.

---------------------------------------------------------------------------------------------------

## 5 Ground snapshot schema, web endpoints, dashboard (§10, §13.7, §17.4, §24, owner directives)

### 5.1 Principles
* `GroundStation.snapshot()` returns ONE JSON-safe dict, assembled under `gs.lock` and passed
  through `gds.json_safe()` (the v2 `web.clean`, moved to `gds.py` by WS4; `web.py` imports it)
  **inside the lock**, so the result shares no container with the live state (v2 returned
  `"tlm": self.tlm` itself and `clean()` iterated it outside the lock while the radio thread
  added keys: "dictionary changed size during iteration"). It is computed on request. Keys below
  are binding (WS4 produces, WS5 consumes, `ground/tests/test_web.py` and `test_end_to_end.py`
  assert them).
* Cost control: `snapshot()` never contains `science`; the expensive science part (burst FFTs,
  300-row history, sky map, library) is built only by `GroundStation.science_snapshot()`
  (served by `/api/science`), and the front panel uses `GroundStation.panel_status()` (the five
  values of the S/Q lines, no snapshot). `xtce_bytes()` builds the XTCE document outside
  `gs.lock` (it reads only the static dictionary) and caches it per (version, UTC date).
  Test (WS4): a thread adding packets while another serialises `snapshot()` 1000 times raises
  nothing.
* Units are in the key name or stated here; `None` = not available (UI shows "—").
* Nothing is invented: every value is MEASURED, DERIVED with a stated assumption (`*_est` or
  `label`), or CONFIGURED; sensitivity always carries its `source` ("measured" / "datasheet" /
  "estimated" / "computed").
* RSSI precision (owner question "accurate RSSI up to 4 decimals"): the E22 reports packet RSSI
  and noise as an integer dBm (manual: dBm = -(256 - value), 1 dB steps); the SX126x reports
  RssiPkt in 0.5 dB steps (DS GetPacketStatus: signal power = -RssiPkt/2 dBm) and SNR in
  0.25 dB steps (SnrPkt/4). No single reading is accurate to 0.0001 dB. The snapshot keeps full
  float precision; the UI prints window statistics (mean, median, percentiles, SEM) with 4
  decimals next to `n` and the SEM, prints single readings with 4 decimals plus their
  `rssi_resolution_db`, and states that absolute accuracy is set by `rssi_cal_offset_db`
  (T-RF-02). FSPL uses the exact constant 20·log10(4π·10⁹/c) = 32.44778 dB (not 32.44):
  `FSPL = 20·log10(4π·d·f/c)`, d in m, f in Hz — WS4 replaces `32.44` in rxstats and
  `tools/link_budget.py`.
* Data rates: rx/link/apids update per frame or packet; HK-derived keys per HK packet (10-60 s);
  `radio.sx126x` per MCU status (5 s); `station_io` hello 5 s; `cfdp` per PDU + 1 Hz poll;
  `science.*` per SPEC/SPECBURST packet; events immediately. The dashboard polls `/api/state`
  at 1 Hz and `/api/science` at 1 Hz while a science tab is visible, else every 10 s.

### 5.2 Top-level keys of `snapshot()`

| Key | Type | Content |
|---|---|---|
| `schema` | int | 3 |
| `version` | str | `dssq.__version__` (bump to "3.0.0") |
| `utc` | float s | snapshot time |
| `station`, `spacecraft` | str | names |
| `uptime_s` | float s | GDS uptime |
| `mode` | str | "host" / "applab" / "sim" |
| `alarm` | str | "NONE" / "YELLOW" / "RED" |
| `alarm_reasons` | list[str] | every reason currently raising the alarm (PB-5.5) |
| `beacon` | dict | PB-5.3.1 |
| `rx` | dict | receiver statistics (rxstats), PB-5.3.2 |
| `radio` | dict | radio configuration/backends, PB-5.3.3 |
| `link` | dict | framing, SDLS, OID ident (§10), PB-5.3.4 |
| `sync` | dict | `{"state": SEARCH/LOCK/FLYWHEEL or "PACKET" (SX126x), **FrameSynchronizer.stats}` |
| `vc` | dict | per VC name: PacketExtractor stats + `{"frames", "lost", "last_vcfc", "sdls_rejected", "last_ert"}` for VC 0..3 and OID |
| `apids` | dict | per packet name: `{apid, count, seq_gaps, lost, last_seq, age_s, iat_s, latency{window stats}, size, vcid}` (incl. SPECBURST, CFDP) |
| `tlm` | dict | per packet name: `{apid, values, limits, ert, scet, sclk, seq, vcid, units}` (v3 names) |
| `decoded` | dict | PB-5.3.5 |
| `time` | dict | `{partition, last_sclk, scet_of_last_sclk, corr_samples, drift_ppm, residual_rms_ms, owlt_s, timecorr_key: "MCFC"/"VC0 VCF count"}` |
| `cop` | dict | `Fop1.state()` + `{"tc_framing", "sdls_service", "sdls_sn", "frame_limit": 64}` |
| `cfdp` | dict | `Receiver.snapshot()` = `{"transactions": [summary...], "counters": {pdus, malformed, foreign, unsupported, late}}` + `{"files_dir", "onboard": {state, state_name, seq, progress_pct}}` |
| `tdm` | dict | `{"enabled", "auto_write", "records", "dropped", "aos_t", "last_file", "last_write_t", "files": [{name, size, mtime}] (newest 20)}` |
| `sequence` | dict | PB-5.3.6 |
| `magcal` | dict | PB-5.3.7 |
| (`science`) | — | NOT in `snapshot()`; `science_snapshot()` returns the PB-5.3.8 dict (`/api/science`) |
| `health` | dict | `{"stage_errors": {stage: n}, "last_error": {stage, text, t} or None, "tdm_rejected": n}` — `Runner.step` error boundary (PB-8.4) |
| `linkmgr` | dict | `LinkManager.state()` |
| `pending_confirm` | list | `{id, text, kind ("command"/"directive"/"sequence"/"magcal"/"sdls"), confirmed, authorized, t, expires_s}` |
| `evrs` | list | last 40 `{ert, scet, sclk, severity, event_id, text}` |
| `events` | list | last 80 `{t, level, source, text}` |
| `anomalies` | list | last 20 EWMA anomalies |
| `console` | list | console lines |
| `station_io` | dict | PB-5.3.9 |
| `cmd_stages`, `cmd_errors` | dict | enums for the UI |

#### 5.3.1 `beacon`
`{"level": int|None, "name": str|None, "t": ert of the HK that carried it, "since": t of the last
change, "history": [[t, level], ...] (last 50 changes), "hibernating": bool (HK fdir b14)}`.
Event on every change: INFO when it falls, WARN rising to INTERESTING/IMPORTANT, ERROR to URGENT.

#### 5.3.2 `rx` (rxstats, WS4)
* `state`: `{los, aos_t, passes, pass_duration_s, since_last_frame_s, time_in_lock_pct,
  los_timeout_s (effective, PB-3.2), hibernating (HK fdir b14)}`.
* `rf` (scalars; the v2 key names are kept where the meaning is unchanged, because `/metrics`
  derives the Prometheus names from them, PB-5.6; the three renames — `sensitivity_est_dbm` →
`sensitivity_dbm` (it now carries `sensitivity_source`, which may be "measured"/"datasheet"),
`noise_bw_assumed_hz` → `noise_bw_hz` (+ `noise_bw_source`), `frames.lost_mcfc` → `frames.lost`
— are listed in PB-5.6): `freq_mhz`, `freq_hz`, `model`,
  `air_rate_code`, `rate_label`, `air_rate_bps`,
  `rssi_dbm` (last packet, calibrated), `rssi_raw_dbm`, `rssi_resolution_db` (E22 1.0, SX126x
  0.5), `rssi_cal_offset_db`, `rssi_mw`, `noise_dbm` (calibrated), `noise_source` ("E22 noise
  register" / "SX126x GetRssiInst median of 8"), `noise_age_s`,
  `snr_est_db` (RSSI − noise, both models), `pr_est_dbm`, `n0_est_dbm_hz`, `cn0_est_dbhz`
  (E22: from RSSI/noise and `noise_bw_hz`; SX126x: measured SNR + 10·log10(BW), labelled
  `cn0_source`), `ebn0_est_db`, `noise_bw_hz`, `noise_bw_source` ("profile BW" / "assumed"),
  `sensitivity_dbm`, `sensitivity_source`, `margin_est_db` (RSSI − sensitivity; v2 name, `_est`
  because the sensitivity may be estimated/computed), `required_snr_db`
  (LoRa SNRmin of the SF; None for E22), `snr_margin_db` (measured SNR − required),
  (the last raw frequency error is `rf.packet.freq_err_hz`; `rf.freq_err_hz` and `rf.snr_db`
  are the §10 window dicts below, never scalars), `freq_offset_limit_hz` (FERR_L, SX126x), `freq_offset_state`
  ("OK"/"WARN"/"ALARM", §14: WARN > 50 %, ALARM > 80 % of FERR_L), `crc_fail_packets`,
  `packet_error_rate` (SX126x: crc_fail / (ok + crc_fail)).
* `rf.packet`: `{rssi_dbm, snr_db, freq_err_hz, crc_ok, t}` (§10).
* `rf.snr_db`, `rf.freq_err_hz`: 10-min window `{mean, sem, min, max, n}` (§10).
* `rf.doppler`: `{offset_cal_hz, calibrated: bool, doppler_hz, range_rate_m_s, source:
  "afc estimate" | "raw frequency error"}`; range_rate = -c·doppler/f (§2.2); with AFC enabled
  use `afc.estimate_hz` (§29 products (a)); `doppler_hz` None unless SX126x.
* `rf.afc`: `AfcLoop.snapshot()` (keys enabled, state, correction_hz, estimate_hz, residual_hz,
  sign, static_hz, doppler_hz, pending_hz, samples, limit_hz, bound_hz, slew_hz, deadband_hz,
  snr_min_db, accepted, rejected_snr, rejected_invalid, skipped_settling, steps, sign_flips,
  rpc_failures) + `events` (last 20).
* `budget` (link budget, design vs measured): `sc_tx_power_dbm_nominal`, `power_verified`
  (ONE rule, the one `link.describe()` already implements: True only for the E22-400T37S, whose
  manual gives 37 dBm for every code; False for every T33D code — the T33D v1.05 REG1 table was
  copied from the T30D, contract §2 — and every SX126x code; VERIFY T-RF-07), `sc_antenna_gain_dbi`,
  `sc_line_loss_db`, `sc_eirp_dbm_est`, `distance_km`, `owlt_s`, `fspl_db` (exact formula),
  `ground_gain_dbi`, `ground_line_loss_db`, `predicted_pr_dbm`, `predicted_margin_db`,
  `predicted_snr_db`, `measured_path_loss_db_est`, `measured_path_loss_mean10m_db`,
  `excess_loss_db_est`, `range_at_sensitivity_km_est`, `sensitivity_dbm`, `sensitivity_source`,
  `thermal_noise_dbm` (kTB = -174 + 10·log10(B)), `noise_figure_assumed_db` (SX126x 6.0 /
  E22 None), `uplink`: `{gs_power_dbm (configured power code → link.power_dbm), gs_eirp_dbm_est,
  sc_sensitivity_dbm, sc_sensitivity_source (from RF packet model/code), predicted_pr_dbm,
  predicted_margin_db, measured_uplink_rssi_dbm (RF.uplink_rssi), measured_margin_db}`.
* `windows`: for "1m", "10m", "pass": per metric `rssi`, `noise`, `snr_est`, `snr_meas`,
  `freq_err`, `iat` (inter-arrival s), `rs` (symbols corrected), `asm` (ASM bit errors):
  `{n, mean, min, max, std, sem, p10, p50, p90, last, fade_depth}` + for "pass" `truncated`
  (bool). Fix of a v2 defect: the "pass" window was `Window(10**9)` — never trimmed, never reset
  at AOS (unbounded memory, a full sort per `stats()` call, and not a pass window at all). v3:
  every "pass" window is cleared at AOS and holds at most `PASS_WINDOW_MAX` = 20 000 samples
  (`deque(maxlen=...)`; ≥ 8 h at the fastest E22 frame period of 1.5 s); `truncated` is True once
  samples were discarded.
* `hist`: `rssi` (5 dB bins), `snr` (2 dB), `freq_err` (100 Hz bins ±FERR_L or ±5 kHz),
  `rs_corrections` (0..16), `asm_bit_errors` (0..4).
* `decoder`: `codeblocks, clean, corrected, uncorrectable, symbols_corrected, max_corrections,
  symbol_error_rate_est, bit_error_rate_est_range [SER/8, SER], codeword_error_rate
  (uncorrectable / codeblocks), fecf_fail_after_rs, foreign_or_bad_header, rs_rescued`
  (SX126x CRC-failed CADUs recovered by RS, §20), `rs_rescue_failed`, `rice_errors`,
  `decom_errors`, `sdls_rejected` (by verdict: BAD_MAC, BAD_IV, BAD_SPI, BAD_LENGTH, REPLAY).
* `frames`: `ok`, `lost` (TM: MCFC gaps; USLP: per-VC VCF gaps, `FrameLossTracker`; renamed
  from v2 `lost_mcfc` because USLP frames carry no MCFC; gds.py's console line updated), `fer`
  (lost/(ok+lost)), `oid_frames`, `idle_frame_pct`, `channel_efficiency_pct` (packet octets /
  data-field octets, data field = `data_field_len(format, sdls overhead)`), `frames_per_min`,
  `info_rate_bps`, `cadu_rate_bps`, `by_format` {"TM": n, "USLP": n}, `per_vc` {vc: {frames,
  lost}}.
* `serial` (E22): `octets, octets_per_s, noise_queries, noise_replies`.
* `radio_link` (§27): E22: `{connected, faults, reopens, tx_dropped, last_error, port}`;
  SX126x: `{connected, mcu_age_s}`; events on connect/disconnect.
* `uplink`: `cltus, octets, last_t, windows, rejected_too_long, framing`.
* `two_way`: latest RF packet values + `uplink_margin_db_est`.
* `series` (last 300 frames): `t, rssi, noise, snr_est, snr_meas, freq_err, margin, rs`.
* `events`: last 20 AOS/LOS.

#### 5.3.3 `radio`
`{**link.describe(settings)}` — model, packet_mode, rate_code, rate_label, bps, sf, bw_khz, cr,
power_dbm, channel, freq_mhz, rx_boost, cad, sensitivity_dbm, sensitivity_source (§10) plus
model_id, interface, ldro, power_code, power_verified, lbt, airtime_s, airtime_source,
freq_offset_limit_hz, freq_offset_cal_hz, has_fault_log, has_noise_register — and:
`model_seen` (from RF `radio_model`), `model_mismatch` (bool; WARN event once), `rcu` (bool),
`ground_config` (RadioManager.read_config()), `ground_fault`, `last_scan`, `allowed_channels`,
`configured {channel, air_rate, power, lbt}`, `link_change`, `jobs_pending`, `sx126x`
(`Sx126xBridge.snapshot()` or None), `sc_airtime_s` (`link.airtime_s` for the spacecraft's
model/code = LoRa air time only: E22 2.4k 1.023 s, SX126x per PB-5.4), `sc_busy_s_est`
(`link.busy_s(model, code)`, NEW in link.py, WS4 = what the flight's `radio_airtime_ms()` uses
as busy time: E22 = UART transfer 236 x 10 / 9600 s + air time = 0.246 + 1.023 ≈ 1.27 s at
2.4k (the flight's integer arithmetic gives 245 + 1022 = 1267 ms); SX126x = air time) — the
value comparable with RF `last_airtime`, shown when that field
saturates at 25.5 s, and the basis of the derived LOS/FOP timeouts (PB-3.2).

#### 5.3.4 `link` (§10)
`{"tm_framing_cfg": "auto|TM|USLP", "tm_framing_seen": {"TM": n, "USLP": n, "last": str},
"tc_framing": "TC|USLP", "sdls": ccsds.sdls.link_snapshot(tc, tm) (tc_service, tm_service,
tm_epoch, tm_last_sn, tm_frames_ok, tm_auth_fail, tm_replay, tm_unprotected, tc_next_sn) + 
{"spacecraft_tm_state": HK sdls_tm_state name, "mismatch_suspected": bool}, "ident": {"text":
str|None, "t": float|None, "count": int}}`. `mismatch_suspected` = ≥ 3 consecutive data frames
failing with BAD_SPI/BAD_IV (frames carry no AE header: spacecraft probably runs NONE) — WARN
event once. `ident.text` comes from unprotected OID frames (any printable ASCII, including
`<`, `>`, `"`, `&`): it is data, rendered with `textContent` only (PB-5.7). Additional
`sdls` keys: `"tm_epoch_regressed": int` (REPLAY verdicts whose epoch is LOWER than the last
accepted one with a valid MAC — a replayed recording or an EGSE boot-counter regression, i.e.
possible GCM nonce reuse: RED, PB-5.5), `"key_file": str`, `"key_loaded": bool`,
`"tc_sn_state": "OK" | "BLOCKED: <reason>"` (PB-7 F-64).

#### 5.3.5 `decoded` (from the latest real-time HK/RF)
`mode, fdir [names], sensors_ok [names], sensors_missing [names], reset_cause, farm_state,
radio_fault (from `dictionary.RADIO_FAULTS`, codes 0..4 and 16..19 — v2 gds.py imported
`RADIO_FAULTS` from both dictionary and framesync and the framesync table, which has only the
ground E22 codes 1..4, won; v3 imports framesync's as `GROUND_RADIO_FAULTS` for the ground E22
reports), radio_model, framing, rate_label, air_rate_bps (link.rate_bps(model, code); v2 name),
sdls_tc_service, sdls_tm_service, sdls_tm_state, cfdp_state, seq_state, beacon, mag_bist
(decode_mag_bist), magcal {ob_active, ib_active}, rf_flags [names]`.

#### 5.3.6 `sequence`
`{"onboard": {"state", "state_name", "count", "next_dt_s"} (HK), "file": {"name", "text_lines",
"entries": [{slot, line_no, time_text, relative, t_s, mnemonic, args, cmd_hex}], "error":
{line_no, message}|None, "checked_t"} | None, "uplink": {"pending_confirm_id", "queued_ids":
[...], "first_slot"}, "sclk_estimate_s": int|None}`.

#### 5.3.7 `magcal`
`{"field_ut": float|None, "units": {"0": {...}, "1": {...}}, "onboard": {"state", "ob_active",
"ib_active"}, "bist": decode_mag_bist(HK)}`; per unit: `{"collecting": bool, "coverage":
MagCalCollector.live_coverage() (unit, n, coverage, bins_hit, bins, centre_ut, rejected,
duplicates), "fit": MagCalResult.as_dict()|None, "command": magcal_command(...)|None}`.
Collectors take MAG ob_b_x/y/z (unit 0) / ib_b_x/y/z (unit 1) in uT from REAL-TIME VC1 packets
only (not playback, not CFDP files).

#### 5.3.8 `science_snapshot()` (§13.7; served by `/api/science`, never part of `snapshot()`)
`SpectralState.snapshot(now)` keys: `spectral` (analyse() of the newest SPEC: available, t,
label, valid, mode, mode_name, flags{raw, valid, agc, active, saturated, attitude, lamp_bits,
lamps}, exposure{gain_code, gain_x, int_cycles, integration_ms, full_scale, n_frames, led_ma{white,
IR, UV}, note (HDR)}, wavelength_nm, spectrum, uncertainty, snr, raw, raw_fraction, saturated,
normalized, temperature_c, boresight{zenith_deg, azimuth_deg}, onboard{class_id, class,
confidence, change_deg}, features{n_valid, peak_nm, peak_value, centroid_nm, vis_sum, nir_sum,
nir_vis}, reflectance{compatible, reason, ref_t, values, uncertainty}, white_ref_angle_deg,
indices{basis, label, ndvi/gndvi/ndre/wbi_proxy/pri_proxy: {value, sigma, formula, bands_nm, note,
saturated}}, radiometry{label, assumption, irradiance_total/vis/nir, ppfd, illuminance: {value,
sigma, unit, band_nm}, basis}, colorimetry{X, Y, Z, x, y, x_sigma, y_sigma, u, v, cct_k, duv,
cct_valid, cct_mccamy_k, mccamy_in_range, cct_mccamy_diff_pct, method, reason}, classification{
label, class_id, class, confidence, features, reasons, consistency, onboard, agrees},
fluorescence{masked, excitation_max_nm, f680_f730, blue_green_index, emission_total,
emission_peak_nm}, library_match{best, ranked}, warnings, notes, white_ref, temperature_drift,
events, counters, directives)`, `spectral_history` (wavelength_nm, max_rows, t, sclk, mode,
class_id, lux, cct_k, values[rows][18]), `spectral_burst` (n_samples, duration_s,
rate_hz_measured, rate_hz_reported, channels[...], broadband, fft, transients, series, t0_ms,
gain_code, int_cycles, t_first, t_last, duplicates, dropped, active, previous[...]) — plus NEW
`compression` {packets_raw, packets_rice, ratio_mean, last_ratio} (§28) — `spectral_skymap`
(label, zen_step_deg, az_step_deg, n_total, bins[{zen_lo, zen_hi, az_lo, az_hi, n, lux_mean,
lux_std, ppfd_mean, ppfd_std}]), `spectral_library` (max_entries, file, entries[...]).
Additionally `science.cfg` = `[science]` table and `science.led_limits` = `{"max_code":
{"white": 3, "IR": 2, "UV": 0}, "allowed_led_cur": 0..11}` (§26).

#### 5.3.9 `station_io`
`{"connected": bool (hello < 12 s), "connected_t", "fw", "rcu": bool, "kb": bool (CardKB at 0x5F
on the header Wire bus), "input": live typed line, "last_status_line": the last S| line sent,
"beacon_sent": int}`; `kb1`/`kb2` removed (§16.3, §27).

### 5.4 Operational notes the UI must surface (facts)
* SX126x air time of one 236-octet CADU (Semtech formula, 12-symbol preamble, CR 4/5, explicit
  header, CRC, LDRO per 16.38 ms): profile 0 34.64 s, 1 17.32 s, 2 8.66 s, 3 4.74 s, 4 2.16 s,
  5 1.18 s, 6 0.66 s, 7 0.37 s (computed 2026-10-11 with link.py's formula). E22 at 2.4k: air
  time 1.023 s (`link.airtime_s`, shown as `radio.sc_airtime_s`, labelled "air time est.") and
  busy time ≈ 1.27 s incl. the 9600-baud UART transfer (`radio.sc_busy_s_est`, labelled "busy
  time est. — compare with RF last_airtime"); the UI shows both with their labels. RF
  `last_airtime` (u8, 0.1 s) saturates at 25.5 s and `frame_period_ms` (u16) at 65.535 s: show
  `radio.sc_busy_s_est` and mark saturated values.
* With the SX126x slow profiles the spacecraft stretches its packet periods (PB-3.3; EVR 0x0215
  in the event log). The ground therefore never assumes the v2 periods: expected-age displays
  use the measured inter-arrival time per APID (`apids.<name>.iat_s`, NEW: EWMA of the
  packet-to-packet ERT difference).
* `freq_offset_state` WARN/ALARM and AFC FAULT are alarms (PB-5.5).

### 5.5 Alarm logic (WS4)
RED if any RED limit, LOS after ≥ 1 pass (with the effective `los_timeout_s`, PB-3.2; NOT while
the latest real-time HK had the HIBERNATE flag (fdir b14) set — a hibernating spacecraft sends
one beacon per `beacon_s` ≤ 3600 s: the state shows "LOS (hibernating)" without an alarm until
no frame has arrived for 7200 s = two maximum beacon intervals, then RED as usual), beacon
URGENT, `freq_offset_state == "ALARM"`, ground radio fault (T37S report) active, E22 serial link
down > 30 s, `link.sdls.tm_epoch_regressed` increased in the last 24 h ("TM epoch went
backwards with a valid MAC: replayed recording or EGSE boot-counter regression (possible GCM
nonce reuse) — investigate; generate a new key if the counter regressed"), or the radio backend
unavailable (no bridge for the SX126x model). YELLOW if any YELLOW limit,
FDIR flags other than HIBERNATE/TX_INHIBIT set, beacon IMPORTANT, HK `reset_cause == 4`
(BROWNOUT, §15), SDLS TM rejects in the last 60 s, `link.sdls.mismatch_suspected`,
`freq_offset_state == "WARN"`, `radio.model_mismatch`, AFC state FAULT, SDLS key file missing
while a service other than NONE is configured, TC uplink blocked (`tc_sn_state` BLOCKED, F-64),
any `health.stage_errors` increase in the last 60 s. `alarm_reasons` lists each. The station
annunciator gets alarm 0/1/2 and the beacon level separately (PB-4.2).

### 5.6 Web endpoints (WS5 `display/web.py`; stdlib `ThreadingHTTPServer`)

| Method | Path | Request | Response | Auth |
|---|---|---|---|---|
| GET | `/` , `/index.html` | — | dashboard.html | none |
| GET | `/api/state` | — | `gs.snapshot()` (has no `science`) | none |
| GET | `/api/science` | — | `gs.science_snapshot()` | none |
| GET | `/api/dictionary` | — | `{"commands": {mn: {opcode, args: [{name, code, lo, hi, enum}], hazardous, builder_only, desc, check_doc}}, "directives": {...}, "ground_directives": {...}, "packets": {apid: {name, fields: [{name, code, scale, unit, desc}]}}}` | none |
| GET | `/metrics` | — | Prometheus text built from `gs.snapshot()` (gauges derived from the `rx.rf`/`rx.decoder`/`rx.frames`/`rx.budget` key names as in v2, so a renamed key renames its metric; the v3 renames are `dssq_rf_sensitivity_est_dbm` → `dssq_rf_sensitivity_dbm`, `dssq_rf_noise_bw_assumed_hz` → `dssq_rf_noise_bw_hz`, `dssq_frames_lost_mcfc` → `dssq_frames_lost` (docs/06 lists them, Phase D); new keys add gauges automatically) + beacon_level, sdls tm_frames_ok/auth_fail/replay/unprotected/tm_epoch_regressed, afc correction/estimate, rs_rescued, rice_errors, cfdp counters, freq_err stats, stage_errors | none |
| GET | `/api/xtce` | — | `gs.xtce_bytes()` (cached per version and UTC date, built outside `gs.lock`), `application/xml`, `Content-Disposition: attachment; filename="vgq1.xtce.xml"` | none |
| GET | `/api/tdm` | — | `[{name, size, mtime}]` of `<archive>/tdm/*.kvn` | none |
| GET | `/api/tdm/<name>` | name must match `^VGQ1_[A-Za-z0-9_.-]+\.kvn$` and exist in `<archive>/tdm` | `text/plain` attachment | none |
| GET | `/api/files` | — | CFDP files in `<archive>/files` `[{name, size, mtime, state}]` | none |
| GET | `/api/files/<name>` | name matches `^[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}$` (CFDP names may carry the receiver's `.<source>-<seq>` insert and a `.partial` / `.json` suffix); `Path(files_dir, name).resolve().parent == files_dir.resolve()`; file exists | `application/octet-stream` attachment | none |
| POST | `/api/cmd` | `{"text": str}` (commands, BD, UNLOCK, SETVR, HELP, ground directives) | `submit_command()` result | token |
| POST | `/api/confirm` | `{"id": int, "role": "CONFIRM"\|"AUTH"}` | `confirm()` result | token; AUTH also `X-DSSQ-Auth` when `[commanding] auth_token` set |
| POST | `/api/cancel` | `{"id": int}` | `{"status": "CANCELLED"}` | token |
| POST | `/api/seq/check` | `{"text": str ≤ 65536, "first_slot": int}` | `gs.seq_check()` (no side effects) | token |
| POST | `/api/seq/uplink` | `{"text", "first_slot"}` | `gs.seq_uplink()` → `{"status": "CONFIRM", "id", ...}`; on confirm all SEQLOAD queued in order | token |
| POST | `/api/magcal` | `{"action": "start"\|"stop"\|"reset"\|"fit"\|"uplink"\|"clear", "unit": 0\|1}` | `gs.magcal_action()`; uplink/clear → CONFIRM id (two-step) | token |
| POST | `/api/tdm/write` | `{}` | `gs.tdm_write()` → `{status, path, message}` | token |

Rules: token = header `X-DSSQ-Token` equal to `[gds] http_token` (constant-time compare,
`hmac.compare_digest`). When `http_host` is not loopback and no token is configured, every POST
returns 403 `{"error": "commanding disabled: set [gds] http_token"}` (read-only dashboard still
works). Request bodies > 131072 octets → 413; bad JSON → 400; unknown path → 404; all responses
`Cache-Control: no-store` and `X-Content-Type-Options: nosniff`; JSON via `gds.json_safe()`.
Defence in depth against script injection (PB-5.7): the dashboard response carries
`Content-Security-Policy: default-src 'none'; script-src 'sha256-<H>'; style-src
'unsafe-inline'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none';
frame-ancestors 'none'`, where `<H>` = base64(SHA-256) of the text of the page's single inline
`<script>` element, computed by `web.py` once at start (stdlib `hashlib`, `base64`). A hash
source makes the browser refuse every injected inline handler (`onerror=`, `onclick=`) and
every injected `<script>`, so the page itself must contain no inline event-handler attributes
(event delegation with `data-*` attributes instead). Hazardous commands, coordinated link changes,
`SDLS RESETREPLAY`, sequence uplinks and MAGCAL uplinks/clears use the two-step
`pending_confirm` flow (expiry 120 s; with `require_two_person_for_hazardous` an AUTH is also
needed for the hazardous ones). SPECREF/SPECLIB/TDM/AFC are ground directives through
`/api/cmd`. The web thread never calls the Bridge and never blocks on the radio.

### 5.7 Dashboard (WS5 `display/dashboard.html`; single self-contained file, no CDN, no web fonts)
Header bar: station, spacecraft, AOS/LOS, sync, alarm (+ reasons tooltip), **beacon banner**
(full-width colour band: NOMINAL none, INTERESTING blue, IMPORTANT amber, URGENT red flashing;
text "BEACON <LEVEL> since <t>"), mode, UTC, RSSI 1-min mean ± SEM (4 dp), SNR, margin, FER,
frames, SCLK, COP-1, SDLS TM state, framing seen.
Tabs (all from `/api/state` unless marked science):
1. OVERVIEW — tiles, signal history, spacecraft summary, latest events, command status.
2. RECEIVER — the in-depth station receiver page: RF now (every `rx.rf` scalar with unit and
   resolution), statistics table (3 windows x 8 metrics x 11 statistics, 4 decimals for dB
   quantities), link budget table (design vs measured vs uplink, every term with unit and
   source badge; FSPL formula shown), radio configuration (`radio`, VERIFY badges for
   unverified power/sensitivity), AFC panel (state, correction, estimate, residual, sign,
   counters, events), Doppler / range-rate, frequency-error gauge against FERR_L with WARN/ALARM
   bands, decoder (RS/SER/BER/CER/FECF/rescued/rice), frames per VC/format, packets per APID,
   SX126x MCU status (rx_ok, rx_crc, rx_hdr, tx_ok, tx_fail, cad_busy, cad_forced, faults,
   resets, rx_gain_reg + verified), serial link, two-way link; canvas charts: RSSI/noise/SNR
   time series, frequency error series, histograms (RSSI, SNR, freq error, RS corrections, ASM
   errors), RF survey (ground GSCAN + spacecraft RFSCAN).
3. LINK & SECURITY — framing, SDLS TC/TM (services, epoch, SN, counters, mismatch), OID ident
   text, COP-1/FOP, CLCW, time correlation.
4. FRAMES & PACKETS — VC table, APID table, time correlation, CLCW (existing, v3 VCs).
5. SPACECRAFT — HK (all 43 fields decoded, enums named, limits coloured), RF (all fields), FDIR
   and sensors (v3 bits), beacon history, onboard sequence state, MAG BIST, MAGCAL state, CFDP
   state, anomalies.
6. SPECTROMETER (science) — spectrum ± 1σ (linear/log), raw counts with full scale and
   saturation markers, normalized spectrum, reflectance vs white reference, indices table with
   ±σ and basis label, radiometry (W/m², PPFD, lux; "approximate (18-band)"), colorimetry (CIE
   1931 xy diagram with the Planckian locus drawn from the same model, CCT/Duv, McCamy
   cross-check), classification ground vs onboard (features, reasons, agreement), fluorescence
   (FLUOR), library matches, waterfall of the last 300 spectra, burst (per-channel series, FFT,
   transients, achieved vs reported rate, compression ratio), sky map (polar bins, lux/PPFD),
   die-temperature drift, instrument state (mode, gain, integration, LED currents, n_frames,
   temperatures, boresight), white reference + library controls, SPECCFG/SPECMODE builders
   that only offer legal values (white 12.5/25/50/100 mA, IR 12.5/25/50 mA, UV 12.5 mA; gain
   1/3.7/16/64/AGC; per-mode n), event log (onboard EVR 0x0701..0x0707 + ground).
7. SCIENCE (science) — magnetometers (OB/IB/body, |B|, dual-sensor separation), attitude
   (fusion vs TRIAD, Euler ZYX, flags incl. TRIAD source and BMM150 scale), environment (ENV:
   pressure hPa, T, RH to 1 %, IAQ, CO2eq, bVOC, gas, accuracy).
8. MAGCAL — per unit: start/stop/reset collection, live coverage (26 direction bins), fit
   (offset, matrix, residual RMS, coverage, condition, axis ratio, refusal reasons), uplink
   (two-step), clear (identity), onboard state.
9. SEQUENCES — editor, check (table of entries / first error with line number), uplink
   (two-step), SEQCTRL START/STOP/CLEAR/DELETE buttons, onboard state.
10. FILES — CFDP transactions (state, progress bar, missing ranges, condition), CFDPPUT 0/1/2 and
   CFDPCANCEL buttons, received files download, TDM files + "write TDM now", XTCE download.
11. COMMANDING — command entry with dictionary-driven help and argument hints, pending
   confirmations (CONFIRM / AUTH), history, console, station I/O (one CardKB).
Formatting: dB/dBm statistics 4 dp; single readings 4 dp + "(resolution x dB)"; Hz 1 dp; m/s 3
dp; µW/cm² 4 significant digits; rates/percent 2 dp; FER/BER/CER scientific with 3 significant
digits; times UTC ISO-8601. Every derived value shows its label (approximate / heuristic / est.).
**Untrusted text (binding, security):** every value taken from `/api/state`, `/api/science`
or `/api/dictionary` is inserted with `textContent` / `createTextNode` / attribute setters
(`el.title = ...`, `el.dataset.x = ...`), or passed through the page's ONE `esc()` function
(escapes `& < > " '`) when a template string is unavoidable; `innerHTML` is used only for
static markup written in the page. This covers EVR texts, event texts, console lines, pending
confirmation texts, OID ident text, file names, sequence-file lines, spectral library names,
anomaly parameter names and table cells (the v2 `rows()` helper and lines 281, 320-330, 338,
344 of the v2 page inserted such strings with `innerHTML`). Rationale: EVR/OID texts arrive
over RF (OID frames are never authenticated, and with SDLS NONE nothing is), and a script
injected into the page could POST commands with the operator's token. No inline event-handler
attributes (CSP, PB-5.6). Tokens: the commanding token (`X-DSSQ-Token`) is entered once per
browser session and kept in `sessionStorage` (try/catch; the page works without it); the
second-operator secret (`X-DSSQ-Auth`) is prompted for every AUTH action and never stored.

---------------------------------------------------------------------------------------------------

## 6 App Lab entry points and the spacecraft EGSE (§18, §23, §5, §7)

### 6.1 Facts used (read in arduino-app-cli / app-bricks-py sources, scratchpad refs2)
* The App folder is bind-mounted at `/app` in the main container (`compose_template.go`
  volumes: app home → `/app`, `/dev` → `/dev`), groups include `dialout`, device cgroup rules
  include ttyUSB/ttyACM; `app.yaml` `ports: [N]` is published N:N; entry point `/run.sh` runs
  `python/main.py`; `python/requirements.txt` would be pip-installed (needs the internet) —
  **ship none** (§23, §27).
* `arduino.app_utils.Bridge` is a process-wide client connected on first use to
  `os.environ["APP_SOCKET"]` (else `arduino.router_bridge.DEFAULT_ADDRESS` =
  `unix:///var/run/arduino-router.sock`); `call(name, *args, timeout=10)` raises
  `ValueError`/`TimeoutError`/`ConnectionError`/`RuntimeError`; `provide` handlers run on one
  dispatcher thread and must not call back. `App.run(user_loop=f)` calls `f()` repeatedly until
  SIGTERM/SIGINT, then shuts down.
* Whether App Lab exposes the port on the LAN is NOT verified (§18): docs (Phase D) say how to
  check (`curl http://<board>:8080/api/state`) and the systemd fallback.
* Only the App folder is mounted (`compose_template.go`: volumes = app home → `/app`, `/dev`,
  board-specific read-only mounts): code outside the App folder (e.g. the repository's
  `ground/dssq`) is NOT visible in the container. An App Lab run therefore needs `dssq` INSIDE
  the App folder (`python/dssq`, the Phase D layout).
* `App.run(user_loop=f)`: any `Exception` escaping `f` is logged by `App.loop()` ("Unhandled
  exception in application loop"), the loop returns 1 and `run()` calls `sys.exit(1)` — the App
  stops. SIGTERM is turned into a `BaseException` raised in the main thread; App Lab kills the
  process `SHUTDOWN_GRACE_PERIOD_S` = 5.0 s after the signal (`app_utils/app.py`).

### 6.2 Ground App (`ground/station_io/` = the future `dssq-groundstation` App; WS5)
`ground/station_io/app.yaml` (C-09):
```yaml
name: DSS-Q Ground Station
icon: 📡
description: DSS-Q1 ground station for VGQ-1 - CCSDS ground data system and operator web dashboard (port 8080, Python standard library only) plus the station I/O sketch (CardKB, annunciators, Radio Control Unit, optional SX126x radio).
ports:
  - 8080
```
`ground/station_io/python/main.py` (replaces the 12-line placeholder). Structure: module-level
code only defines functions; `def main(argv: list[str] | None = None) -> int` does the work and
the file ends with `if __name__ == "__main__": sys.exit(main())`, so tests import it safely.
1. `APP = Path(__file__).resolve().parents[1]`; `DATA = APP / "data"`, `mkdir(exist_ok=True)`.
2. Import path: insert `APP / "python"` into `sys.path`. **Inside App Lab only the App folder
   exists in the container (PB-6.1), so an App Lab run requires the Phase D layout:
   `python/dssq/` and `python/config/station.toml` inside the App folder.** Outside the
   container (a developer running `python3 ground/station_io/python/main.py` from the
   repository) `APP/python/dssq` does not exist and `Path(__file__).resolve().parents[2]` (the
   repository's `ground/`) is inserted instead; if neither contains `dssq`, exit 2 with "dssq
   package not found: an App Lab App must contain python/dssq (Phase D layout)".
   `dssq.config.DEFAULT_PATH` (`parents[1] / "config" / "station.toml"` of `dssq/config.py`)
   resolves in both layouts.
3. Configuration: path = `$DSSQ_CONFIG` if set, else `DATA/station.toml`; on first start copy
   the packaged default (`dssq.config.DEFAULT_PATH`) to `DATA/station.toml` (never overwrite) so
   the operator edits a persistent copy. `cfg = load_config(path)`; then (App mode, explicit
   rules, `config.resolve_app_paths(cfg, DATA)`, WS4): a relative `[gds] archive_dir` →
   `DATA / archive_dir`; `[sdls] key_file` → `DATA / "sdls_key.hex"` unless it is an absolute
   path (a relative value such as the packaged "config/sdls_key.hex" is ignored, so the key never
   lands in `data/config/`); `[gds] http_host = "0.0.0.0"` unless `$DSSQ_HTTP_HOST` is set (a
   container service must listen on all interfaces to be reachable through the port mapping); if
   `http_token` is empty, read `DATA/http_token` or create it once with
   `secrets.token_hex(16)` (mode 0600) and print it to the App Lab console ("enter it once in the
   dashboard"). Exit with a clear message (code 2) on `ValueError` from validation.
4. `from arduino.app_utils import App, Bridge` (ImportError → exit 2 "not running under App
   Lab: use python -m dssq.gds"); `link = BridgeLink.from_app_utils(Bridge)`.
5. `runner = gds.Runner(cfg, bridge=link, mode="applab")` (starts the web server thread, the
   radio backend, the front panel, archive under DATA); `try: App.run(user_loop=runner.step)
   finally: runner.close()` — `step()` never lets an `Exception` escape (PB-8.4), so the App
   stops only on SIGTERM/SIGINT; `close()` finishes within 4 s (PB-8.4), inside App Lab's 5 s
   grace period.
6. Logging to stdout (App Lab console), `logging.basicConfig(level=INFO, format=...)`.
`main.py` exposes `prepare(app_dir: Path) -> dict` (steps 1-3, no network, no bridge) and
`main()` (steps 4-6). Phase B proof that the App layout runs (WS5, `test_web.py`):
(a) `prepare()` on a temp App dir: `data/station.toml` copied once and never overwritten,
`data/http_token` created with mode 0600, key path `data/sdls_key.hex` for the packaged relative
`key_file` and unchanged for an absolute one, archive under `data/`;
(b) the test assembles the Phase D layout in a temp dir (`app/python/main.py`, `app/python/dssq/`
copied from `ground/dssq`, `app/python/config/station.toml`), pre-writes `app/data/station.toml`
(sim radio, frontpanel/rcu false, a free port chosen by the test), puts a fake
`arduino/app_utils` package on `PYTHONPATH` whose `App.run(user_loop)` calls `user_loop`
repeatedly, performs `GET /api/state` from inside it and then returns, and runs `main.py` in a
subprocess: exit 0 and a valid snapshot.
`ground/deploy/dssq-gds.service` (host fallback, §18) keeps `python -m dssq.gds --config
config/station.toml`; comment that App mode and the service must not run at the same time (one
Linux client per provided Bridge name). `ground/station_io/sketch/*` unchanged in WS5 (G1 rules,
PB-8.1).

### 6.3 Spacecraft EGSE (`spacecraft/vgq1_flight/python/main.py`; WS6a)
Duties and exact behaviour:
* **Data directory** `APP/data` (§18; `APP = Path(__file__).resolve().parents[1]`). Migration: if
  `APP/python/egse_data/boot_count.txt` (or `sdls_last_sn.txt`) exists and the `data/` copy does
  not, copy it first (the boot counter must never go back, see below) and log it. Files:
  `boot_count.txt`, `sdls_last_sn.txt`, `cadu_YYYYMMDD.bin` (`[u64 unix_ns][u16 len][CADU]`,
  unchanged format, read by `dssq.archive.read_cadu_file`), `evr_YYYYMMDD.jsonl`,
  `sc_status_YYYYMMDD.jsonl`, `hardline_queue.txt`. Counter files are written atomically
  (write `*.tmp`, `flush`, `os.fsync`, `os.replace`).
* **Partition / SDLS TM epoch** (§5 nonce safety). GCM must never see the same (key, IV); IV =
  'TM' | epoch | 0 | sn with sn restarting at 1 per epoch, so an epoch value may be given to the
  MCU at most once per key. Rules: (1) on every `sc_boot` → `n = boot_count + 1`, persist, then
  `Bridge.call("set_partition", n, timeout=5)` from the loop; (2) at EGSE start and whenever a
  `sc_status` shows `"epoch": 0` (MCU without an epoch, e.g. it booted while the EGSE was down)
  and no request is in flight → issue a new value the same way; (3) a call that raised
  (`TimeoutError`/`ConnectionError`/`RuntimeError`/`ValueError`/`AppError`) is retried with the
  SAME value (the MCU treats an equal epoch as a no-op; the value is already persisted, so it is
  never used for anything else), a later `sc_boot` always issues a new one; (3a) an explicit
  **False** is a refusal, never retried with the same value: the MCU refuses p ∉ 1..65534 or p
  lower than its current epoch, which means `boot_count.txt` regressed (restored backup, copy
  from another board) — log ERROR "MCU refused epoch <n> (current <e>): boot counter regressed",
  raise `boot_count` to max(boot_count, the `epoch` of the latest `sc_status`) and issue
  `boot_count + 1` (persisted first) — at most 3 refusals in a row, then stop issuing and log
  ERROR "epoch negotiation failed: check data/boot_count.txt and the flight key"; (4) never
  re-send an old value after an EGSE restart (the v2 behaviour did: remove it); (5) values are
  limited to 1..65534 (HK u16 sentinel 0xFFFF): at 65534 refuse with
  an ERROR log "SDLS TM epoch space exhausted: generate a new key (tools/gen_sdls_key.py) and
  reset data/boot_count.txt"; (6) if `boot_count.txt` is missing at start, log WARN "first start
  or counter lost: if this flight key was used before, generate a new key" and start at 1. A
  regression the EGSE cannot see (counter lost AND the MCU rebooted) is caught on the ground:
  a TM frame with a valid MAC and a LOWER epoch than the persisted last one raises the RED
  `tm_epoch_regressed` alarm (PB-5.3.4/5.5, F-64).
* **SDLS TC SN** restore: on `sc_boot` and at start, `set_sdls_sn(last)` from
  `sdls_last_sn.txt`; `sdls_sn` notifications persisted atomically (value stored as received,
  int32 two's complement, never interpreted).
* **CFDP relay** (§7): `cfdp_req` handler parses with `cfdp_tx.parse_request()` and enqueues;
  the loop starts `cfdp_tx.start_request(DATA, text)` (refuses if a relay is active; logs), then
  per loop pass offers `tx.current()` with `Bridge.call("cfdp_pdu", pdu.hex(), timeout=5)`:
  1 → `advance()`; 0 → wait `cfdp_tx.retry_delay_s(attempt)` and retry the same PDU; -1 → ERROR
  log and abandon the transfer (the ground marks it ABANDONED after `inactivity_s`);
  `TimeoutError`/`ConnectionError`/`ValueError`/`RuntimeError` → retry with back-off.
  `cfdp_cancel(seq)` → if the active transfer's seq matches, `tx.cancel()` (next PDU is
  EOF(cancel)); else ignore with a log line. No source file → log + nothing sent (the MCU's
  30 s relay-start timeout reports it).
* **Archive / logs**: `tm_cadu` → CADU file + per-VC count (decode the first header octets
  after de-randomizing, TM and USLP aware: USLP VCID from TFPH bits); `sc_evr` → `evr_*.jsonl`
  `{utc, id, severity, text}`; `sc_status` and `sc_status_x` → `sc_status_*.jsonl` (one line each,
  `{"utc", "kind": "core"|"x", ...}`); the status board merges the latest of both. An EVR count
  gap (`evr_nd` > 0) is logged once per change ("<n> EVRs not forwarded by the MCU").
* **Hardline queue** as v2 (lines ≤ 220 hex chars; `hl_cltu`).
* **Status board** every 10 s: mode, partition/SCLK, frames/OID/MCFC, radio model/channel/code/
  power/LBT, period/busy/duty, uplink RSSI/SNR/noise, FARM, cmd acc/rej, FDIR/sensors, VC0..3,
  SSR, SDLS TC/TM service + TM state/epoch/sn, CFDP state/seq/progress, beacon, sequence state,
  archived CADUs per VC, relay progress and refusals, cadence stretch, notifications not
  forwarded (`evr_nd`, `cadu_nd`).
* Handlers never call `Bridge.call` (enqueue only); all calls from `App.run(user_loop=loop)`;
  the loop sleeps ≤ 0.2 s per pass and catches `Exception` per duty at its documented boundary
  (log + count + continue: an escaping exception would stop the App, PB-6.1). Structure as the
  ground `main.py`: `main()` + `if __name__ == "__main__"` guard. Standard library only; no
  requirements.txt.
* `spacecraft/vgq1_flight/app.yaml` description: "VGQ-1 flight software (E22-400T33D default
  radio, T37S and SX126x options; CCSDS TM/USLP, SDLS, COP-1, CFDP) with the Linux EGSE
  (CADU archive, SCLK partition / SDLS epoch, CFDP relay)."; no `ports`.

### 6.4 Station annunciator (§24.4; sketch part is WS6b after G1)
GDS side (WS4, now): `S|aos|alarm|uplink|beacon` with beacon = HK beacon_level of the latest
real-time HK, -1 before any HK. Sketch side (WS6b): ALARM LED priority (1) alarm RED → 4 Hz blink
(125 ms half period) — changed from the v2 2 Hz so it differs from IMPORTANT; (2) beacon 1/2/3 →
blink at 1/2/3 Hz; (3) alarm YELLOW → steady on; (4) else off. Buzzer: existing RED-entry and LOS
buzz kept; beacon rising to IMPORTANT → 2 chirps of 100 ms, to URGENT → 3 chirps (100 ms on /
100 ms off), on the rise only. "GDS link down" pattern unchanged. A 4-field S line (old GDS)
means beacon unknown (no beacon pattern).

---------------------------------------------------------------------------------------------------

## 7 Follow-ups owed to Phase B (§20, §24-§29, workstream reports) → files → owner

Owner = workstream of PB-8. "D" = Phase D (docs/packaging, listed only so nothing is lost).
"RL" = the concurrent RadioLib workstream (do not touch its files before gate G1).

| # | Item (source) | Files | Owner |
|---|---|---|---|
| F-01 | SPECCFG LED limits: flight validator rejects led_cur > 11 (`spec_led_cur_applied(v) == v`) (§26 AS-REG-2/AS-DP-01) | fsw/commands.cpp | WS1 |
| F-02 | same limits on the ground: dictionary `led_cur 0..11`, `spectral.LED_MAX_CODE = (3, 2, 0)` + `led_cur_allowed()`, web SPECCFG builder | dssq/dictionary.py, dssq/spectral.py; display/dashboard.html | WS3; WS5 |
| F-03 | AS-T6 real bus clear in `I2cBus::recover()` and once in `begin()`; escalate from the AS7265X FAULT path (PB-8.6 item 9). Implementation (facts: ArduinoCore-zephyr `pinMode()` implements only INPUT, INPUT_PULLUP, INPUT_PULLDOWN and OUTPUT = `GPIO_OUTPUT_LOW`; `OUTPUT_OPENDRAIN` exists in `api/Common.h` but `pinMode()` silently ignores it, `wiring_digital.cpp` 19-33 in both the main and the 1.0.0 cores; `Wire.begin()` re-applies the DEFAULT pinctrl, `Wire.cpp` 62-68): open drain is EMULATED — release a line = `pinMode(pin, INPUT)` (the board's pull-ups raise it), drive it low = `pinMode(pin, OUTPUT)` (configures and drives LOW at once); `digitalWrite(HIGH)` is never used on SDA/SCL. Sequence: `w_->end()`; if `digitalRead(SDA) == LOW`: up to `kI2cClearMaxPulses` (9) SCL pulses (low `kI2cClearHalfPeriodUs`, release, wait the same, stop early when SDA reads high, check SCL really rose — a held SCL ends with "SCL stuck low"); then a STOP (SDA low while SCL high, release SDA); both pins left as INPUT; `w_->begin()` (re-mux to I2C) + `setClock(kI2cClockHz)`; returns `bool` (SDA high at the end) → EVR 0x060A when false. Bounded: ≤ 9 x 2 x 5 us + STOP ≈ 0.1 ms + `end/begin`. | drivers/i2c_bus.h/.cpp, config.h, fsw/flight.cpp | WS2 |
| F-04 | honour `As7265x::lamp_state_unknown()` (treat as lit: defer RM3100 sample ≤ `kSpecLampDeferMaxMs`, then sample with MAG b4) and `ERR_NO_LAMP = 7` (SPEC packet invalid + EVR 0x0707 once per configuration) (§26) | fsw/flight.cpp | WS2 |
| F-05 | ground SPECBURST 0xFFFE/0xFFFF semantics; HDR raw/exposure semantics (AS-DP-04, AS-DP-07) | dssq/spectral.py | WS3 |
| F-06 | burst read time (AS-T8): `burst_read_max_ms` in the burst-end EVR 0x0704 | fsw/flight.cpp | WS2 |
| F-07 | AS-PROT-7: SNS_AS7265X from `spec_.ok()` at run time, not only `begin()` | fsw/flight.cpp | WS2 |
| F-08 | §27 pty serial tests (4 tests from scratchpad/test_e22_serial_pty.py, without the sys.path line and `import sys`) | ground/tests/test_radio.py | WS4 |
| F-09 | §27 `rx.radio_link` {connected, faults, reopens, tx_dropped, last_error} + connect/disconnect events | dssq/gds.py, dssq/rxstats.py | WS4 |
| F-10 | §27 config check `[radio] uart_code == 3` for E22 models | dssq/config.py | WS4 |
| F-11 | §27 comments naming 'cryptography' as the cross-check → "ground/dssq/ccsds/aes_gcm.py (+ cryptography when installed)" | src/ccsds/gcm.h (lines 18-19), tools/host_test/test_crypto.cpp (lines 5-6, 50-51) | WS1 |
| F-12 | §27 frontpanel: single CardKB hello `H\|fw\|rcu\|kb`, drop kb2, no `except Exception` in the call paths (catch `TimeoutError, ConnectionError, ValueError, RuntimeError, OSError`; `BridgeLink` maps `AppError` to `ConnectionError`, PB-4.2); the two thread bodies keep one documented `except Exception` boundary each (log + continue: a dead panel thread must not go unnoticed and must not take the GDS down); the tx thread uses `gs.panel_status()` (not `snapshot()`) | dssq/display/frontpanel.py | WS4 |
| F-13 | §27 no `python/requirements.txt` in any App folder | ground/station_io/python/, spacecraft/vgq1_flight/python/ | WS5, WS6a (D packaging) |
| F-14 | §28 SPECBURST flight encoding (channel-major Rice, fallback raw, EVR on other status), `kSpecBurstMaxLen` 124 | fsw/spectral.h/.cpp, tools/host_test/test_spectral.cpp | WS1 |
| F-15 | §28 ground decode, RiceError counted + WARN + drop, compression ratio | dssq/spectral.py; dssq/gds.py; dashboard | WS3; WS4; WS5 |
| F-16 | §29 opcodes 0x1D..0x20 in commands.h + static_assert; `parse_user_data` split; validator | fsw/commands.h/.cpp | WS1 |
| F-17 | §29 flight members `CommandSequencer seq_`, `BeaconMonitor beacon_`, `MagCalStore magcal_`, `MagBistResult bist_ob_/bist_ib_`, `bool safe_by_fdir_`; setup order; EVR 0x0004; boot BIST | fsw/flight.h/.cpp | WS2 |
| F-18 | §29 `evr()` calls `beacon_.note_evr()` first | fsw/flight.cpp | WS2 |
| F-19 | §29 `seq_.poll()` once per pass; EXECUTE through `execute_user(cmd, len, 0xFE)`; SKIPPED_LATE/STOPPED/COMPLETE EVRs | fsw/flight.cpp | WS2 |
| F-20 | §29 `safe_by_fdir_` true on boot SAFE and FDIR SAFE (command loss, PA over-temperature), false on MODE (live or stored) | fsw/flight.cpp | WS2 |
| F-21 | §29 `beacon_.on_ground_contact(beacon_sample())` after `execute()` for ACCEPT, ACCEPT_BYPASS, CONTROL_UNLOCK, CONTROL_SET_VR; `beacon_.update()` in `tick_1hz` | fsw/flight.cpp | WS2 |
| F-22 | §29 execute SEQLOAD/SEQCTRL/MAGBIST/MAGCAL | fsw/flight.cpp | WS2 |
| F-23 | §29 TRIAD from `magcal_.apply()` (OB, then IB, then BMM150), RM3100 rotated by `kRm3100ToBody`; ATT b6 | fsw/flight.cpp, config.h | WS2 |
| F-24 | §29 RF-survey EVRs move to 0x0302/0x0303 | fsw/flight.cpp | WS2 |
| F-25 | §29 FDIR: clear I2C_BUS after `kI2cBusHealthyS` error-free seconds following a recovery (EVR 0x0609); clear AUX_TIMEOUT at the next TX_DONE | fsw/flight.cpp | WS2 |
| F-26 | §29 HK append fields | fsw/tlm_packets.h/.cpp (WS1), fsw/flight.cpp gen_hk (WS2), dictionary.py (WS3) | WS1/WS2/WS3 |
| F-27 | §29 dictionary MAGBIST, MAGCAL, SEQCTRL, SEQLOAD (builder_only) | dssq/dictionary.py | WS3 |
| F-28 | §29 hazard flags mirrored in `opcode_forbidden` (no new hazardous opcode: no change); keep the equality test green | dssq/dictionary.py, ground/tests/test_sequence.py | WS3 |
| F-29 | §29 GDS/web sequence panel: `seq_check`, `seq_uplink` (one confirmation for the batch, SEQLOADs queued in order via COP-1), START separate | dssq/gds.py; display/web.py, dashboard.html | WS4; WS5 |
| F-30 | §29 products: 7th-tuple handling (exists), XTCE containers for SPECBURST (header + note "payload per encoding") and CFDP (opaque PDU), following `_evr_system/_rfscan_system`; `EVR_TEXT_MAX`, `RFSCAN_MAX_CHANNELS` may move to dictionary | dssq/products.py, dssq/dictionary.py | WS3 |
| F-31 | §29 config `[radio.sx126x] afc`, `[radio] freq_offset_calibrated`, `[science] field_ut` | dssq/config.py, config/station.toml | WS4 |
| F-32 | §29 GDS AFC (a): `AfcLoop.from_settings(settings, enabled=cfg.radio.sx126x.afc)`; `on_packet` per RxPacket; `tick(now, rx.los)` in housekeeping; any int → `r = backend.rf_foff(corr)`, `confirm(corr, -3 if r is None else r, time.time())` from the radio thread (-3 = "no answer (bridge)", added to `link.AFC_RPC_RESULTS`; RL provides `rf_foff`, PB-8.1); `pop_events()` → event log; `reconfigure()` on LINKRATE/LINKCHAN; `afc.estimate_hz` for rf.doppler; directive `AFC RESET` | dssq/gds.py | WS4 |
| F-33 | §29 GDS TDM (b) (record creation guarded: ValueError → `health.tdm_rejected`, PB-8.4): `TdmRecorder(station, spacecraft)`; `clear()` at AOS; `add(TdmRecord(t_utc = ert - radio_latency_s, rx_power_dbm = calibrated RSSI, snr_db, freq_offset_hz = afc.residual_hz(raw) (SX126x) / None, afc_correction_hz, nominal_freq_hz, noise_bw_hz))` after `on_packet` and before `confirm`; at LOS `write(archive_dir, freq_offset_cal_hz = cal if freq_offset_calibrated else None, radio_model)` then `clear()`; `TDM WRITE` directive and web action | dssq/gds.py | WS4 |
| F-34 | §29 web XTCE download, MAGCAL panel, TDM downloads | display/web.py, dashboard.html (+ gds methods) | WS5 (+WS4) |
| F-35 | §29 `rf_foff` in the SX126x backend | dssq/radio/sx126x.py | RL |
| F-36 | §24.4 beacon: GDS event + alarm + S-line field (WS4); dashboard banner (WS5); sketch annunciator (WS6b) | gds.py, frontpanel.py; dashboard.html; station sketch | WS4; WS5; WS6b |
| F-37 | §24.5 RM3100 BIST at boot (fitted units, lamps off) and on MAGBIST in the next PA-off window | fsw/flight.cpp | WS2 |
| F-38 | §20 RS rescue: flight delivers CRC-failed uplink packets to the CLTU decoder (BCH + FECF + SDLS still apply), counts `tc_rescued` (`sc_status_x`) and reports each rescue with EVR 0x0307 at INFO (DIAG would be filtered by the default level 1) | fsw/flight.cpp, drivers/radio.h (`RxPacket::crc_ok`, default true) | WS2 |
| F-39 | §20 RS rescue ground path: `ingest_packet` with `crc_ok = False` and 236 octets → RS decode → `rs_rescued` / `rs_rescue_failed`; never bypasses RS/FECF/SDLS | dssq/gds.py, dssq/rxstats.py | WS4 |
| F-40 | §20 deliver CRC-failed CADU-length payloads: flight `Sx126xRadio` (PACKET with `crc_ok = false` when len = 236 for the ground / any length for the flight uplink), station sketch `rf_rx` keeps the hex when crc_ok = 0 and len = 236, `parse_rf_rx` keeps such data | drivers/sx126x.cpp, ground/station_io/sketch/sketch.ino, dssq/radio/sx126x.py | WS6b (after G1) |
| F-41 | §15 brownout derating (`radio_boot_power_code`, EVR 0x0608) and ground YELLOW on reset_cause BROWNOUT | fsw/flight.cpp; gds.py | WS2; WS4 |
| F-42 | §5 OID identification "DE <callsign> VGQ-1 "; ground `link.ident` | fsw/datalink.cpp; gds.py | WS1; WS4 |
| F-43 | §5 TM nonce safety: VC0-3 held until epoch, `poll_epoch_timeout(uptime, kSdlsEpochWaitS)` → null SA + EVR 0x0121 | fsw/datalink.cpp, fsw/flight.cpp | WS1, WS2 |
| F-44 | §5/§6.3 EGSE: new partition on every MCU boot and when the MCU reports epoch 0; never re-send an old value | spacecraft/vgq1_flight/python/main.py | WS6a |
| F-45 | §6 USLP uplink in the FOP (`[link] tc_framing`) and replay/decoder support for SDLS + USLP + VC3 | dssq/fop.py (WS4); dssq/replay.py (WS3) | WS4; WS3 |
| F-46 | §7 SSR export (`begin_export`, `export_read`, `paused_drops`, `SsrFileSource`) | fsw/ssr.h/.cpp | WS1 |
| F-47 | §7 ground CFDP receiver in the GDS (`[cfdp]`), files under `<archive>/files`, auto-decode of COMPLETE `ssr_p*_*.bin` into the archive (packets table, vcid 2, source "CFDP", live `tlm` untouched) | dssq/gds.py | WS4 |
| F-48 | radio report: GDS model selection + `Sx126xBridge(link, cfg.radio).attach()` / `from_router`, `RadioManager(radio, None)` for packet radios; `ingest_packet`; no NOISE_QUERY for packet radios (noise job instead); `_time_correlate` uart time E22 only; texts name the model | dssq/gds.py | WS4 |
| F-49 | radio report: rxstats on `link.RadioSettings` / `link.sensitivity` (+source) / `link.power_dbm(model_from_id(rf.radio_model))`; `on_packet`; freq_offset_state | dssq/rxstats.py | WS4 |
| F-50 | radio report: linkmgr model-aware (`min_rate = max(min_rate, model.min_effective_rate_code)`, `link.sensitivity`) | dssq/linkmgr.py | WS4 |
| F-51 | radio report: sim uses `regs_from_cfg(r, model=link.E22_T33D)` for packet-mode configs; v3 simulator (PB-8.4a) | dssq/sim.py | WS4 |
| F-52 | radio report: `link.py` legacy exports (`AIR_RATE_BPS`, `SENSITIVITY_EST_DBM`, `TX_POWER_DBM`, `MIN_EFFECTIVE_RATE_CODE`) removed once rxstats/sim/linkmgr no longer use them | dssq/link.py | WS4 |
| F-53 | flight-radio report: config.h radio macros/constants; wiring recipe (Radio&, RadioSettings, events, TIMECORR launch correction, configure only when !busy) | config.h, fsw/flight.h/.cpp | WS2 |
| F-54 | flight-radio report: ground test comparing `<out>/radio_tables.csv` with `dssq.link` | ground/tests/test_radio.py | WS4 |
| F-55 | §25.2 RadioLib include guarded by the radio-model macro (E22 builds never pull RadioLib) — today `sx126x.h` includes it whenever `__has_include(<RadioLib.h>)` | drivers/sx126x.h | RL, else WS6b |
| F-56 | check_firmware.sh: flight matrix (PB-8.5) and SX126x station builds | tools/host_test/check_firmware.sh | WS6b (after G1) |
| F-57 | `.gitignore`: `spacecraft/vgq1_flight/data/` (EGSE data), `ground/station_io/data/` (App data) | .gitignore | WS6b (after G1) |
| F-58 | FSPL exact constant 32.44778 | dssq/rxstats.py, tools/link_budget.py | WS4 |
| F-59 | defect found while specifying: `Fop1.all` grows without bound → keep the last 500 records (summaries archived) | dssq/fop.py | WS4 |
| F-60 | defect found while specifying: `Rm3100::collect()` busy-waits up to 30 ms (> 20 ms rule) → add `int8_t poll_drdy()` (1 ready / 0 not yet / -1 bus error, does not clear `pending_`) and collect with timeout 0 across loop passes | drivers/sensors.h/.cpp, fsw/flight.cpp | WS2 |
| F-61 | defect found while specifying: `seq_index()` maps unknown APIDs to the ENV counter → `apid_index()` returning 0xFF (PB-1.1) | fsw/tlm_packets.h/.cpp | WS1 |
| F-62 | §23 rule 3: the flight `sketch.yaml` still lists `SparkFun Spectral Triad AS7265X (1.0.5)` and `Arduino_NiclaSenseEnv (1.0.1)`; v3 uses neither (in-house AS7265X driver, Nicla Sense Env removed) → delete both lines (and add the RadioLib `- dir:` line only as §30 / the RL workstream define) | spacecraft/vgq1_flight/sketch/sketch.yaml | RL, else WS6b (after G1) |
| F-63 | `ground/dssq/cltu.py` (hardline CLTU builder) is still v2: reads the removed `[sdls] enabled` (so it never loads the key), builds `SdlsSender(key, spi, ...)` with the class default service AUTH while the flight default is AE (→ BAD_SPI), ignores `[link] tc_framing`, limits the CLTU to 100 octets (`hl_cltu` accepts 110, PB-4.1) and its docstring names `python/egse_data`. Fix: services from `[sdls] tc_service` (key from `key_file`, App/host rules of PB-3.2), `build_uslp_tc_frame` when `tc_framing = "USLP"`, the C-08 frame-size check, `MAX_HARDLINE_OCTETS = 110`, the SN file shared with the GDS through the F-64 atomic code, docstring → `<App>/data/hardline_queue.txt`; test in test_sdls.py (TC/USLP x AUTH/AE accepted by the Python reference receiver) | dssq/cltu.py, tests/test_sdls.py | WS4 |
| F-64 | SDLS ground state must be crash-safe (`ccsds/sdls.py`, v2 defects: `SdlsSender.next_sn()` rewrites the SN file with a non-atomic `write_text` on every frame and an empty file reads as 0, so a power loss mid-write makes every later uplink frame a REPLAY; the TM (epoch, sn) replay window lives in RAM only, so after a GDS restart old genuine frames would be accepted). Fix: (a) TC SN file written atomically (`*.tmp`, flush, `os.fsync`, `os.replace`) BEFORE the frame leaves; a missing file = 0 (first use), an empty/unparsable file → TC uplink BLOCKED ("SDLS SN file corrupt") until a real-time HK whose frame passed SDLS AE gives `sdls_last_sn`, then `sn = max(sn, hk)` and unblocked (event); HK received in unprotected frames never moves the SN (an injected HK could push it to the wrap); (b) every protected HK with `sdls_last_sn ≥ sn` raises `sn` the same way (self-healing after a lost file); (c) the TM replay state `(epoch, sn)` persisted atomically in `<archive>/sdls_tm_replay.txt` after every accepted protected frame (≤ 1 write per frame period) and restored at start (corrupt → (0, 0) + WARN); `SDLS RESETREPLAY` persists the reset; (d) REPLAY verdicts with a lower epoch counted as `tm_epoch_regressed` (PB-5.3.4); tests in test_sdls.py | dssq/ccsds/sdls.py, dssq/gds.py, tests/test_sdls.py | WS4 |
| F-65 | `.github/workflows/ci.yml` (commits 0a73319, 83237ac): its `arduino-build` job compiles only the station sketch (commit 0a73319's message: "the flight sketch build joins the arduino-build job once integration compiles") and fetches the patched RadioLib with `tools/radiolib.py` (an RL file) → add the flight sketch compile (default E22 build, `--profile default --warnings all`) to that job; keep host tests (strict + sanitizers) and the ground suite on 3.11 + 3.13 as they are | .github/workflows/ci.yml | WS6b (after G1) |
| D-01 | docs (Phase D): §26 T-SC-08 items, §27 docs items, §28 references + VERIFY rice stack/time, §29 docs/08 VERIFY tests (AFC sign, SnrPkt BW, NTP/radio_latency, magcal rotation, XTCE in an MCS, TDM ICD), C-14/C-15 table fixes, T-RF-08 SX126x bring-up, beacon policy owner review (C-20), App Lab port check and fallback, `pip install` removal | docs/*, README.md | D |

---------------------------------------------------------------------------------------------------

## 8 Parallel implementation plan

### 8.1 Workstreams, exclusive file ownership, order

Nobody edits a file owned by another workstream. A file not listed is frozen in Phase B (report
a needed change to the orchestrator instead of editing it). Files owned by the concurrent
RadioLib workstream (RL) — `src/drivers/sx126x.h/.cpp`, `ground/station_io/sketch/sketch.ino`,
`ground/dssq/radio/sx126x.py`, both `sketch.yaml`, `tools/radiolib*`,
`tools/host_test/check_firmware.sh`, `.gitignore` — are untouchable until **gate G1** (RL work
merged by the orchestrator); after G1 they pass to WS6b. `.github/workflows/ci.yml` (it calls
RL's `tools/radiolib.py`) also passes to WS6b after G1. **Shared test file
`ground/tests/test_radio.py`:** RL's `Sx126xBridge` tests live in it (block from `class
FakeBridge` through `test_radio_manager_drives_the_sx126x_backend`); RL may edit only that block
and append after it; WS4 never edits that block and appends its new tests (F-08, F-32 GDS-side
AFC, F-54) at the END of the file under a `# ---- Phase B (WS4) ----` banner, so the G1 merge is
two disjoint hunks. WS4 codes F-32 against the contract API `rf_foff(hz) -> 1 | -1 | -2`, plus
`None` on a link failure (the committed `Sx126xBridge._call` convention), using a test double
until G1.

| WS | Name | Files owned (exclusive) |
|---|---|---|
| WS1 | Flight data & protocol layer (pure C++, host-tested) | `spacecraft/vgq1_flight/sketch/src/fsw/tlm_packets.h`, `tlm_packets.cpp`, `commands.h`, `commands.cpp`, `ssr.h`, `ssr.cpp`, `spectral.h`, `spectral.cpp`, **`datalink.h`, `datalink.cpp` (NEW)**, `autonomy.h`, `autonomy.cpp` (only if a check forces a change), `src/ccsds/ccsds_types.h`, `src/ccsds/gcm.h`; `tools/host_test/host_test.cpp`, `test_spectral.cpp`, `test_crypto.cpp`, `test_autonomy.cpp` (only if it fails), **`test_integration.cpp` (NEW)**, `tools/host_test/Makefile` (only if needed; it already globs) |
| WS2 | Flight executive & drivers integration | `src/fsw/flight.h`, `flight.cpp`, `status_display.h`, `status_display.cpp`, `src/config.h`, `sketch/sketch.ino`, `src/drivers/i2c_bus.h/.cpp`, `payload.h/.cpp`, `sensors.h/.cpp`, `radio.h` (add `RxPacket::crc_ok` only), `e22.h/.cpp` and `as7265x.h/.cpp` (only if integration exposes a defect; report it), `tools/host_test/arduino_stub/*` (additions only) |
| WS3 | Ground dictionary, decoding, products | `ground/dssq/dictionary.py`, `decom.py`, `cmdparse.py`, `spectral.py`, `products.py`, `replay.py`, `ccsds/__init__.py`; `tools/gen_dictionary_doc.py`; `ground/tests/test_cross_check.py`, `test_spectral.py`, `test_products.py`, `test_sequence.py` |
| WS4 | Ground data system core | `ground/dssq/gds.py`, `rxstats.py`, `config.py`, `linkmgr.py`, `sim.py`, `fop.py`, `archive.py`, `link.py`, `radioctl.py`, **`cltu.py` (F-63)**, `radio/e22.py` (only if needed), `ccsds/sdls.py` (F-64), other `ccsds/*.py` except `__init__.py` (only if needed), `display/frontpanel.py`, `dssq/__init__.py`; `ground/config/station.toml`; `tools/gen_sdls_key.py`, `tools/link_budget.py`; `ground/tests/test_end_to_end.py`, `test_radio.py` (shared with RL, rule above), `test_sdls.py`, `test_uslp.py`, `test_ccsds.py`, `test_magcal.py`, `test_rice.py` (the last four only if needed) |
| WS5 | Ground web UI & ground App Lab entry | `ground/dssq/display/web.py`, `display/dashboard.html`, `ground/station_io/python/main.py`, `ground/station_io/app.yaml`, `ground/deploy/dssq-gds.service`, **`ground/tests/test_web.py` (NEW)** |
| WS6 | (a) Spacecraft EGSE now; (b) post-G1 items | (a) `spacecraft/vgq1_flight/python/main.py`, `cfdp_tx.py`, `spacecraft/vgq1_flight/app.yaml`, `ground/tests/test_cfdp.py`; (b) after G1: `ground/station_io/sketch/sketch.ino`, `src/drivers/sx126x.h/.cpp`, `ground/dssq/radio/sx126x.py`, both `sketch.yaml`, `tools/host_test/check_firmware.sh`, `.gitignore`, `.github/workflows/ci.yml` (and only the items F-40, F-55, F-56, F-57, F-62, F-65, PB-6.4 sketch part, and the post-G1 `rf_foff` check of PB-8.5) |

Order and dependencies:
1. Hour 0 — **interface freeze**: the declarations in PB-8.2/8.4 are binding. WS1 lands its
   headers first (tlm_packets.h, commands.h, ssr.h, spectral.h burst block, datalink.h,
   ccsds_types.h) — WS2 codes against them in parallel. WS3 lands `dictionary.py` constants and
   `CommandDef` first — WS4 codes against them. WS5 codes against PB-5 using a recorded snapshot
   (from WS4's sim run or a hand-written fixture). WS6a codes against PB-4.1/PB-6.3.
2. Merge order (orchestrator): WS1 → WS3 → WS2 → WS4 → WS6a → WS5 → (G1) → WS6b. The flight
   and ground protocol layers change together, so single merges inside the first group leave
   known, expected failures (e.g. after WS1 alone the host vectors carry HK 78 octets while the
   ground cross-check still expects 58; after WS3 alone the v2 `gds.py` imports the removed
   `AIR_RATES_BPS`). Gates:
   - between WS1, WS3, WS2 and WS4: only "the merged workstream's own checks + `python -m
     compileall ground` + host test builds" are required;
   - **gate B1** after WS1 + WS3 + WS2 + WS4 are all merged: full PB-8.5 checks 1-6;
   - **gate B2** after WS6a, **gate B3** after WS5: full checks again;
   - **gate B4** after G1 + WS6b: full checks again (Definition of done, PB-8.7).
3. Hand-offs: WS1 → WS2 (C++ API), WS1 → WS3 (host_test vectors: `tlm_vectors.txt`,
   `integration_*.bin/txt`, `command_table.txt`, and the SPECBURST vectors that
   `test_spectral.cpp` writes for `ground/tests/test_spectral.py`, whose format changes with
   the §28 layout — WS1 states the new line format in its report, WS3 updates the reader),
   WS3 → WS4 (dictionary/decom/spectral API), WS4 → WS5 (GDS methods + snapshot), WS4 → WS6b
   (S-line beacon field already sent).

### 8.2 C++ interfaces (WS1 provides, WS2 consumes)

`fsw/tlm_packets.h` — PB-1 (structs `HkPacket` 78, `RfPacket` 49, `CmdVerPacket` 8,
`TimeCorrPacket` 10, `MagPacket` 46, `AttPacket` 37, `EnvPacket` 24; `pack_hk/rf/cmdver/
timecorr/mag/att/env/rfscan/evr`; `kMaxPayload = 146`; `enum Apid` incl. `APID_SPECBURST
0x024`, `APID_CFDP 0x030`; `uint8_t apid_index(uint16_t)` (0..11, 0xFF unknown) and
`kNumApids = 12`; v3 `SensorBit`, `kSensorInstrumentMask = 0x0381`; `MagFlag`, `AttFlag`,
`EnvFlag`; `kCmdVerSeqBypass 0xFF`, `kCmdVerSeqStored 0xFE`; `SpecPacket`/`pack_spec` deleted;
`struct EgseStatus` + `format_sc_status()` / `format_sc_status_x()`, PB-4.1).

`fsw/commands.h` — PB-2.1.

`fsw/ssr.h` additions (pure, host-tested):
```cpp
bool start_playback();                         // was void; false while exporting
bool begin_export();                           // false while playing or exporting; pauses recording
void end_export();
bool exporting() const;
uint32_t export_size() const;                  // sum of recorded packet lengths (no length prefixes)
// Sequential reader of the frozen [head, tail) snapshot as concatenated Space Packets:
// `off` must equal the octets delivered so far, else 0 (the CFDP sender then ends with
// condition 4). Copies at most n octets; never writes beyond dst[n-1].
size_t export_read(uint32_t off, uint8_t* dst, size_t n);
uint32_t paused_drops() const;                 // record() calls dropped while exporting (C-06)
class SsrFileSource : public ccsds::CfdpSource {   // in ssr.h, includes ccsds/cfdp.h
 public:
  explicit SsrFileSource(Ssr& s) : s_(s) {}
  uint32_t size() override;                    // s_.export_size()
  size_t read(uint32_t off, uint8_t* dst, size_t n) override;   // s_.export_read()
 private: Ssr& s_;
};
```
`record()` while exporting: nothing stored, `++paused_drops_`. `clear()` is not called while
exporting (the executive refuses SSRCLEAR with CE_BUSY). `dropped()` keeps counting overwrite
drops only.

`fsw/spectral.h` SPECBURST block (replaces the §13.5 one):
```cpp
static const size_t kSpecBurstHdrLen = 10;                       // incl. the encoding octet
static const size_t kSpecBurstMaxLen = kSpecBurstHdrLen + 38 * kSpecBurstPerPacket;   // 124
enum SpecBurstEncoding : uint8_t { SPEC_BURST_RAW = 0, SPEC_BURST_RICE = 1 };
// §28 layout (PB-1.12). Tries CCSDS 121.0 (kRiceMission) on the channel-major samples into
// cap 36 n - 1; RICE_OK -> encoding 1, else raw. rice_status = the attempt's ccsds::RiceStatus
// (RICE_OK or RICE_ERR_OVERFLOW are normal; anything else: the integrator raises EVR 0x0704).
// Returns the length, or 0 (nothing valid written) for n outside 1..3 / s or out null /
// cap < 10 + 38 n.
size_t pack_specburst(uint32_t t0_ms, uint16_t rate_dhz, uint8_t gain_code, uint8_t int_cycles,
                      const SpecBurstSample* s, uint8_t n, uint8_t* out, size_t cap,
                      uint8_t& rice_status);
```

`fsw/datalink.h` (NEW, pure C++; host-tested; used by flight.cpp and test_integration.cpp):
```cpp
#pragma once
#include "../ccsds/cfdp.h"
#include "../ccsds/sdls.h"
#include "../ccsds/tc.h"
#include "../ccsds/tm.h"
namespace vgq {
static const size_t kSciPriorityBacklog = 500;   // v2 anti-starvation threshold, octets (VC1)
static const size_t kIdleTextMax = 32;
struct DownlinkFrame {
  uint8_t vcid = 0;            // TM 0..3 / 7 (OID); USLP 0..3 / 63 (OID)
  uint8_t count = 0;           // TM: this frame's MCFC; USLP: its VCF count (TIMECORR ref_mcfc)
  bool oid = true;
  uint8_t security = 0;        // 0 clear, 1 AE protected, 2 null SA
};
static const size_t kVcReserveOctets = 256;    // last octets of each VC kept for priority packets
class Downlink {
 public:
  // framing from VGQ_DOWNLINK_FRAMING; sec = &SdlsTm when VGQ_SDLS_TM == AE and a key exists,
  // else nullptr (clear frames); idle_text = "DE <callsign> VGQ-1 " (truncated to kIdleTextMax).
  void init(ccsds::Framing framing, ccsds::SdlsTm* sec, const char* idle_text);
  // false: v > 3, or the VC lacks room. A non-priority packet needs backlog + len <=
  // kBufLen - kVcReserveOctets; a priority packet (EVR severity >= 2, CMDVER, EVR
  // 0x0120..0x0122) may use the whole buffer.
  bool push(uint8_t v, const uint8_t* pkt, size_t len, bool priority = false);
  size_t data_field_len() const;           // TM 188 / USLP 183 octets, minus 30 with SDLS AE
                                           // configured (the null SA keeps the AE geometry)
  size_t backlog(uint8_t v) const;         // octets queued on VC v (0 for v > 3)
  bool has_real_data(uint8_t v) const;     // VcStream::has_real_data (false for v > 3)
  void reset_vc(uint8_t v);                // SSRCLEAR empties VC 2 (no-op for v > 3)
  uint32_t overflows(uint8_t v) const;     // VcStream::overflows (0 for v > 3)
  // Exactly one CADU per call. Data VCs only while sec is null or sec->ready(); selection
  // VC0 > VC1 > VC2 > VC3, except VC1 before VC0 when VC1 holds > kSciPriorityBacklog octets
  // and the previous data frame was VC0. A refused protect() (backstop) yields an OID frame.
  void next_cadu(const ccsds::Clcw& clcw, uint8_t cadu[ccsds::kCaduLen], DownlinkFrame& info);
  uint8_t mcfc() const;                    // TmFramer::mcfc()
  ccsds::Framing framing() const;
  uint32_t oid_frames() const;
  uint32_t refused() const;
};
enum class TcStage : uint8_t { NO_CLTU = 0, FRAME_REJECTED = 1, SDLS_REJECTED = 2,
                               TFDF_REJECTED = 3, FARM = 4 };
struct TcOutcome {
  TcStage stage = TcStage::NO_CLTU;
  ccsds::TcParseResult parse = ccsds::TC_OK;
  ccsds::Sdls::Result sdls = ccsds::Sdls::SDLS_DISABLED;
  ccsds::Farm1::Verdict verdict = ccsds::Farm1::CONTROL_INVALID;
  // NOT valid at NO_CLTU / FRAME_REJECTED (tc_parse failed). From SDLS_REJECTED on the header
  // fields are valid (data not authenticated); from TFDF_REJECTED on the data is authenticated;
  // at FARM frame.data -> the TC packet (or COP-1 directive) inside `work`.
  ccsds::TcFrame frame;
  size_t info_len = 0;         // octets recovered from the CLTU
};
// "TOO_SHORT", "BAD_VERSION", "BAD_SCID", "BAD_LENGTH", "BAD_FECF", "BAD_HEADER", "BAD_TFDF"
// (TcParseResult 1..7 = EVR 0x0101..0x0107 texts); "OK" for 0, "?" otherwise. Lives here
// because tc.h has no Phase B owner.
const char* tc_parse_result_name(ccsds::TcParseResult r);
// cltu_decode -> tc_parse(open_tfdf = !sdls.enabled()) -> Sdls::process (when enabled)
// -> tc_open_tfdf -> Farm1::on_frame (tc.h order). `work` (>= 96 octets) receives the CLTU info
// octets and is decrypted in place. Never touches FARM or anti-replay state for a frame that
// fails before the FARM stage.
TcOutcome tc_accept(const uint8_t* in, size_t n, uint8_t* work, size_t work_cap,
                    ccsds::BchStats& bch, ccsds::Sdls& sdls, ccsds::Farm1& farm);

// CFDP relay authorisation (PB-2.5). Times are MCU ms (wrap-safe differences).
class RelayGate {
 public:
  enum State : uint8_t { NONE = 0, REQUESTED, ACTIVE, CANCELLING };
  enum Event : uint8_t { EV_NONE = 0, EV_START_TIMEOUT, EV_CANCEL_TIMEOUT };
  void request(uint32_t seq, uint32_t now_ms);       // NONE -> REQUESTED
  bool cancel(uint32_t now_ms);                      // REQUESTED/ACTIVE -> CANCELLING; false otherwise
  // 1 admitted, -1 refused: CfdpRelayMonitor::acceptable() && seq == seq() && (REQUESTED or
  // ACTIVE, or CANCELLING and the PDU is an EOF). REQUESTED -> ACTIVE; an admitted EOF -> NONE.
  int admit(const uint8_t* pdu, size_t len, uint32_t now_ms);
  Event poll(uint32_t now_ms, uint32_t timeout_ms);  // REQUESTED / CANCELLING expiry -> NONE
  void reset();                                      // -> NONE (relay silent, CfdpRelayMonitor expired)
  State state() const;
  uint32_t seq() const;
  uint32_t refused() const;                          // admit() == -1 count (sc_status_x relay_ref)
};

// Routine-EVR budget (PB-2.6): token bucket in milli-EVRs, capacity 10 EVRs.
class EvrGate {
 public:
  void refill(uint32_t elapsed_ms, uint32_t milli_per_s);   // adds elapsed x rate, caps at 10000
  void fill();                                             // full bucket (end of setup())
  bool admit(uint8_t severity, bool exempt);               // sev >= 3 or exempt: true, no token
  uint32_t throttled() const;
};

// Downlink load control (PB-3.3): PeriodicIdx, kModeCadence, kModePeriodX10, kPeriodicPktLen,
// frame_period_ms(), LoadPlan, plan_downlink_load() as declared there.
}  // namespace vgq
```
`ccsds_types.h`: `VC_FILE = 3`, `kNumDataVcs = 4`.

### 8.3 New files and their justification (§17.2 minimalism)
* `src/fsw/datalink.h/.cpp` — the downlink scheduler, the uplink acceptance chain, the CFDP
  relay gate, the EVR budget and the downlink load plan are the security- and protocol-critical
  core of the executive; moving them out of the Arduino-bound `flight.cpp` makes them
  host-testable (Makefile already compiles every `src/fsw/*.cpp`) and lets the end-to-end test
  exercise the exact flight code.
* `tools/host_test/test_integration.cpp` — §0 requires new host checks in a new
  `test_<topic>.cpp`; it produces the flight→ground and ground→flight vectors.
* `ground/tests/test_web.py` — §0 requires new tests in new files; the HTTP API and its auth
  rules have no existing test file.
No other new file. No new directory.

### 8.4 Python interfaces
WS3 (consumed by WS4/WS5):
* `dictionary`: PB-1.17 and PB-2.4 (`CommandDef(..., check, check_doc, builder_only)`).
* `decom.Decommutator.decode(pkt) -> dict` handles fixed APIDs (6- or 7-tuples), EVR, RFSCAN,
  SPECBURST (`spectral.decode_specburst`, may raise `rice.RiceError`), CFDP (`{"pdu_len": n}`);
  `decom.encode(apid, values: Mapping) -> bytes` (inverse for fixed APIDs: scale inversion,
  rounding half away from zero, None → sentinel; for the simulator and tests).
* `cmdparse.parse()` refuses `builder_only`; `_encode_args` applies `check`; unchanged API
  otherwise (`seq_forbidden_opcodes`, `parse_sequence`, `seqload_commands`, `tc_packet`).
* `spectral.decode_specburst/pack_specburst` per PB-1.12/1.16; `LED_MAX_CODE`,
  `led_cur_allowed(v) -> bool`; `EVR_SPECTRAL_IDS = range(0x0701, 0x0708)`.
* `products`: SPECBURST and CFDP XTCE systems; `build_xtce()` unchanged signature.
* `replay.main(argv)`: new `--config` (SDLS key/services, `[link]` framing) and VC 0..3.

WS4 (consumed by WS5/WS6):
```python
# dssq/display/frontpanel.py
class BridgeLink:
    """One Router Bridge client for the whole GDS (FrontPanel, SX126x backend, RCU)."""
    @classmethod
    def from_router(cls, address: str | None = None, timeout_s: float = 10.0) -> "BridgeLink": ...
    @classmethod
    def from_app_utils(cls, bridge_cls: type) -> "BridgeLink": ...   # arduino.app_utils.Bridge
    def provide(self, name: str, fn: Callable[..., object]) -> None: ...
    def notify(self, name: str, *args: object) -> None: ...
    def call(self, name: str, *args: object, timeout: float = 3.0) -> object: ...
    def connect(self, timeout: float) -> bool: ...                    # app_utils: True
class FrontPanel:
    def __init__(self, gs, link: BridgeLink): ...                    # start(), stop(), rcu()
# dssq/gds.py
class GroundStation:
    def snapshot(self) -> dict: ...            # PB-5; json_safe deep copy built inside the lock
    def science_snapshot(self) -> dict: ...    # PB-5.3.8; only for /api/science
    def panel_status(self) -> dict: ...        # {los, alarm, uplink, beacon, pending_ids}, cheap
    def submit_command(self, text: str, source: str = "web") -> dict: ...
    def confirm(self, cid: int, role: str = "CONFIRM") -> dict: ...
    def cancel(self, cid: int) -> None: ...
    def seq_check(self, text: str, first_slot: int = 0) -> dict: ...
    def seq_uplink(self, text: str, first_slot: int = 0, source: str = "web") -> dict: ...
    def magcal_action(self, action: str, unit: int, source: str = "web") -> dict: ...
    def tdm_write(self, source: str = "web") -> dict: ...
    def xtce_bytes(self) -> bytes: ...
    files_dir: Path; tdm_dir: Path; cfg: dict; lock: threading.RLock
    def ingest(self, data: bytes, now: float | None = None) -> None: ...          # E22 bytes
    def ingest_packet(self, p: "link.RxPacket") -> None: ...                       # SX126x
class Runner:
    def __init__(self, cfg: dict, *, bridge: BridgeLink | None = None, mode: str = "host",
                 sim: bool = False, sim_loss: float = 0.05, sim_errors: int = 3,
                 archive: bool = True, web: bool = True) -> None: ...
    def step(self) -> None: ...      # one bounded iteration: radio read (<= 50 ms), ingest,
                                     # packets(), outbox write, radio jobs, AFC confirm, console
    def close(self) -> None: ...     # front panel stop, archive close, radio close (idempotent)
    gs: GroundStation
def json_safe(o: object) -> object: ...   # the v2 web.clean (NaN/inf -> None, new containers)
def main(argv: list[str] | None = None) -> int: ...   # host CLI: same options as v2
```
Every GroundStation method takes `gs.lock` itself; web threads call them directly.

`Runner` rules (binding):
* **Error boundary.** `step()` runs its stages — `radio_read`, `ingest`, `packets`, `outbox`,
  `jobs`, `afc`, `tdm`, `housekeeping`, `console` — each inside one documented boundary that
  catches `Exception` (never `BaseException`, so SIGTERM/SIGINT and `SystemExit` still stop the
  App), increments `health.stage_errors[stage]`, logs the traceback at most once per stage per
  60 s, raises a WARN event once per stage per 60 s, and continues with the next stage; after 5
  consecutive `radio_read` failures it sleeps 1 s per pass (no busy loop). Reason: under
  `App.run` any escaping exception ends the App with exit code 1 (PB-6.1). Known raisers it
  must survive: `products.TdmRecord.__post_init__` ValueError for `t_utc` outside 2000..2100 (a
  board without NTP reads 1970), `rice.RiceError`, `cfdp.CfdpError`, decom ValueError,
  `AppError` (mapped by `BridgeLink` to `ConnectionError`), `OSError` from the archive/serial.
* **TDM records**: `TdmRecord(...)` is created inside `try/except ValueError` →
  `health.tdm_rejected += 1` and one WARN per pass "station clock outside 2000..2100 (NTP not
  synchronised?): TDM records skipped" — a bad clock never stops reception.
* **Bridge availability**: in sim mode no bridge is created (front panel, RCU and the SX126x
  backend are off; the simulator provides the radio). Otherwise, when `bridge is None` and
  `frontpanel`/`rcu`/SX126x need one, the Runner tries `BridgeLink.from_router(address)`;
  `ImportError` (no `arduino` package, e.g. a development PC) or `ConnectionError` → WARN event
  "router bridge unavailable: front panel / RCU / SX126x radio disabled" and the Runner continues
  without them (E22 model: radio without RCU; SX126x model: no radio, RED alarm "radio backend
  unavailable", web UI still served).
* **Shutdown**: `close()` is idempotent and finishes within 4 s (App Lab kills the process 5 s
  after SIGTERM, PB-6.1): web server `shutdown()` + `server_close()`, front-panel threads joined
  with 1 s timeouts, the in-progress TDM pass written (`tdm.auto_write`, records > 0), archive
  and SDLS state flushed (fsync), radio closed; each step bounded, a step that overruns is
  abandoned with an ERROR log so the rest still runs.
* `Runner.__init__` validates nothing twice: `cfg` comes from `load_config()` (validated).

### 8.4a v3 simulator (`dssq/sim.py`, WS4; F-51) — what the tests of PB-8.5 need
`SimSpacecraft(cfg, *, loss=0.02, max_errors=3, rssi_mean=-112.0, fading_db=3.0, seed=None,
clock=time.time, packet_mode: bool | None = None, sdls_key: bytes | None = None,
epoch: int = 1, framing: str = "TM")` — deterministic with `seed` and an injected `clock`.
`packet_mode` None = from `[radio] model` (SX126x → True). Built only from existing reference
code (no second implementation of any protocol):
* Telemetry: HK/RF/TIMECORR/MAG/ATT/ENV via `decom.encode(apid, values)` (PB-8.4 WS3) with v3
  values (HK `beacon_level`, `sdls_tm_state`, `cfdp_*`, `seq_*`, `mag_bist`, `magcal_state`);
  SPEC v3.1 via `spectral.pack_spec_v31`; SPECBURST via `spectral.pack_specburst` (encoding
  None = shorter, so both encodings occur); EVR/CMDVER/RFSCAN as v2; Space Packets via
  `ccsds.packets.build_packet`.
* Framing: VC0..3 + OID multiplexing with the flight priority rule; TM frames
  `ccsds.tm.build_frame`/`build_oid_frame` or USLP `build_uslp_frame`/`build_uslp_oid_frame`
  (`framing`); SDLS TM via `ccsds.sdls.SdlsTmSender(sdls_key, "AE")` with `set_epoch(epoch)`
  (`epoch=0` → frames held, then the null SA after 120 s simulated, as the flight); OID idle
  text "DE <callsign> VGQ-1 "; CADUs via `ccsds.tm.encode_cadu`.
* Uplink: `ccsds.tc.cltu_decode` → `parse_tc_frame(open_tfdf=...)` →
  `ccsds.sdls.SdlsReceiver(sdls_key, spi, service)` (service = `[sdls] tc_service`, as the
  flight's compile-time `VGQ_SDLS_TC`; no key → NONE) → `tc_open_tfdf` → `Farm1Model`; commands executed:
  NOOP, MODE, PING, RSTCNT, AIRRATE/CHANNEL (v2 logic), HIBERNATE/WAKE, SPECMODE (BURST →
  SPECBURST packets for n seconds), SPECCFG, CFDPPUT 0 (an in-memory "SSR" of the recorded
  packets sent with `ccsds.cfdp.Sender` on VC3, sequence `cfdp_make_seq` rule with the sim
  partition), CFDPCANCEL, SEQLOAD/SEQCTRL (stored and dispatched by simulated SCLK, CMDVER
  `tc_seq` 0xFE), MAGBIST (HK `mag_bist`), MAGCAL (HK `magcal_state`); anything else → CMDVER
  FAILED with the flight's CmdError from `cmdparse` validation.
* MAG: a constant field `[science] field_ut` (default 50 uT in the sim) seen through a known
  hard-iron offset and soft-iron matrix while the simulated attitude rotates (so the MAGCAL fit
  test can compare the fit with the truth).
* Radio: E22 byte-stream mode = the v2 interface (`read/write/close/rcu_*`, `[CADU][RSSI]`);
  packet mode = a `link.RadioBackend` (`packets()` → `RxPacket` with RSSI, SNR, frequency error
  = static offset + slow Doppler ramp − the correction applied through `rf_foff(hz)` (returns 1),
  `crc_ok = False` with a configurable probability on CADUs carrying ≤ 16 symbol errors per
  codeword so RS rescues them).

### 8.5 Checks every workstream must pass (and the orchestrator re-runs at each gate, PB-8.1)
Common (on the files you own; failures caused only by another workstream's unmerged file are
reported, not fixed):
1. `make -C tools/host_test host_test && tools/host_test/host_test $(mktemp -d -p <scratchpad>)`
   → `HOST TEST PASSED (0 failures)`, zero compiler warnings; also built with the CI flags
   (`.github/workflows/ci.yml`: `CXXFLAGS="-std=c++17 -O2 -Wall -Wextra -Wshadow
   -Wno-unused-parameter -Werror -DVGQ_HOST_BUILD -I<src>"`, then the AddressSanitizer + UBSan
   build).
2. `bash tools/host_test/check_firmware.sh` and `LIBS_DIR=<scratchpad>/libs bash
   tools/host_test/check_firmware.sh` → no FAIL, no warn (after WS2: the two v2 FAILs gone).
3. `cd ground && <scratchpad>/venv/bin/python -m pytest -q` (Python 3.13) and
   `<scratchpad>/venv311/bin/python -m pytest -q` (Python 3.11, as CI) → all pass (baseline 358
   + new), the known skip only.
4. Python lint on owned files: `ruff check --select E,F,W,B,UP,SIM,C4,RUF --line-length 120
   --target-version py311`; `mypy --python-version 3.11` adds no error in owned files; every
   owned module imports under Python 3.11.
5. No new directories; only the 4 new files of PB-8.3; no AI-model names; no key files.
6. **Real Arduino build** (from gate B1 on; the environment exists: `<scratchpad>/realbuild/
   bin/acompile.sh` compiles exactly as App Lab does — arduino-cli 1.5.2 as embedded by App Lab,
   arduino:zephyr built from source, the sketch's default profile, FQBN with
   `wait_linux_boot=app`; `out/sanity_summary.txt`: "TOTAL ok=52 unexpected=0"; today the flight
   sketch fails there with `'Spectrometer' does not name a type`, flight.h:84):
   `acompile.sh unoq spacecraft/vgq1_flight/sketch` (default E22-400T33D build) and
   `acompile.sh ventunoq ground/station_io/sketch` → exit 0; with `WARN=all` no warning from our
   files; the report quotes the linker's FLASH/RAM usage lines for both. After G1 (WS6b) also
   the SX126x flight build (`-DVGQ_RADIO=3` through the profile / build property the RL
   workstream defines) and the station `DSSQ_RADIO_SX126X=1` build. Until F-62 lands (after G1)
   the flight sketch's in-tree `sketch.yaml` still lists two stale libraries that the offline
   profile cache does not contain (`realbuild/arduino15/internal` holds only the six platform
   dependencies), so the flight build runs on a temporary copy of the sketch whose
   `sketch.yaml` lacks those two lines (as `realbuild/work/snapshot-a970823` did); from G1 on, the
   tree as is. Run by WS2 (flight), WS6b (after G1) and the orchestrator at each gate.

WS1 extra: host tests for `apid_index`, every packer length/offset (HK 78, RF 49, ENV 24),
`parse_user_data` boundaries for all 32 opcodes, `Ssr` export (sequential reads, paused drops,
playback/export exclusion, export of a wrapped ring), `pack_specburst` (Rice chosen when
shorter, raw on overflow, 0xFFFE, n = 1..3, rejects n = 0/4 and short cap), `Downlink`
(priority, anti-starvation, VC3 lowest, OID while WAIT_EPOCH, null SA after
`poll_epoch_timeout`, USLP VCID 63 OID, idle text, TM/USLP counts, priority push uses the
256-octet reserve and a routine push does not, `data_field_len` 188/183/158/153), `tc_accept`
(TC/USLP x NONE/AUTH/AE, replay, bad MAC never touches FARM, COP-1 control frames, `frame`
untouched at FRAME_REJECTED), `tc_parse_result_name` (all 8 values + out of range),
`RelayGate` (seq mismatch, unrequested PDU, PDU after reboot = fresh gate, EOF-only while
CANCELLING, start/cancel timeouts, wrap-safe times, refused count), `EvrGate` (bucket cap,
exemptions, severity ≥ 3 never throttled, fractional refill), `downlink_load` (PB-3.3),
`egse_status` (PB-4.1). Run the whole host test under `-fsanitize=address,undefined` once (as the
autonomy workstream did; CI does it on every push).
`test_integration.cpp` writes into `out`:
* `integration_tm_ae.bin`, `integration_uslp_ae.bin`, `integration_tm_nullsa.bin`,
  `integration_tm_clear.bin`: CADU streams built by `Downlink` (key `00 01 .. 1F`, epoch 7 for
  the AE streams; null-SA stream = AE configured, no epoch, fallback engaged) carrying one of
  each packet: HK (known values, beacon_level 2, sdls_tm_state per stream), RF, EVR, CMDVER
  (tc_seq 0xFE), TIMECORR, RFSCAN, MAG (flags 0x30), ATT (flags 0x7F), SPEC v3.1 (from
  `SpecProcessor` on a synthetic result), ENV, SPECBURST (one Rice-encoded, one raw packet), and
  a complete CFDP transfer of an `SsrFileSource` export (Metadata, File Data..., EOF);
* `integration_expect.txt`: per stream and packet the expected field values (format of
  `tlm_vectors.txt`), the CFDP file name, size, modular checksum and the file content as hex;
* `command_table.txt`: `<user-data hex> <CmdError>` for every opcode at lo, hi, lo-1, hi+1 and
  every `check` rule case;
* uplink: if `out/ground_tc.txt` exists (written by the Python test), each line `name key-hex
  service framing cltu-hex` goes through `tc_accept` + `parse_tc_header` + `parse_user_data` with
  fresh `Sdls`/`Farm1` state per name group; results to `out/flight_tc_results.txt` as `name
  stage parse sdls verdict opcode cmd_error`.

WS2 extra: the flight type-check matrix (until WS6b adds it to check_firmware.sh, run by hand),
all `-std=gnu++17 -fsyntax-only -Wall -Wextra -Wshadow -Wno-unused-parameter
-DARDUINO=10607 -DARDUINO_UNO_Q -DARDUINO_ARCH_ZEPHYR -I tools/host_test/arduino_stub` on
`sketch.ino` and `src/fsw/flight.cpp`: default; `-DVGQ_RADIO=2`; `-DVGQ_RADIO=3` (+
`-DVGQ_SX126X_CHIP=1262`) with `-isystem <libs>/RadioLib/src`; `-DVGQ_DOWNLINK_FRAMING=1`;
`-DVGQ_SDLS_TC=1`; `-DVGQ_SDLS_TC=0 -DVGQ_SDLS_TM=0`; and the keyed build without touching the
tree: create `T=$(mktemp -d -p <scratchpad>)` and `$T/x/`, write `$T/sdls_key.h` with the exact
gen_sdls_key.py content (`#pragma once`, `#include <stdint.h>`, `#define VGQ_SDLS_SPI 1`,
`static const uint8_t kVgqSdlsKey[32] = {...};`) and add `-iquote $T/x` to the compile line, so
`__has_include("../sdls_key.h")` in `flight.cpp` resolves to `$T/sdls_key.h` (quote includes
are searched relative to the including file's directory first, then in the `-iquote`
directories). The WS2 report contains a loop-timing budget table (every call in `loop()` with
its bound).

WS3 extra: `test_cross_check.py` decodes the four `integration_*.bin` streams with the ground
pipeline (`decode_codeblock` with the AE geometry when configured, `SdlsTmReceiver(bytes(range(32)))`,
`PacketExtractor` VC 0..3, `Decommutator`, `spectral.decode_specburst`, `cfdp.Receiver(tmp)`)
and asserts: every packet bit-exact and every expected value; SDLS verdicts (OK / UNPROTECTED /
NONE); OID ident "DE N0CALL VGQ-1"; CFDP state COMPLETE, checksum_ok, file content equal;
`command_table.txt` accept/reject identical to `cmdparse` (`encode_dictionary` + checks); the
uplink round trip (writes `ground_tc.txt` built with `SdlsSender`/`build_tc_frame`/
`build_uslp_tc_frame`/`build_cltu` for NOOP, SPECCFG, MAGCAL, SEQLOAD, UNLOCK, SETVR, a replayed
SN, a bad MAC; reruns host_test; asserts the flight verdicts). XTCE still validates.

WS4 extra: `test_end_to_end.py` — (a) sim world with v3 packets: HK beacon, SDLS AE both ways,
`tm_framing auto` with a USLP sim, CFDPPUT 0 → COMPLETE file, SPECMODE BURST → science burst,
sequence check/uplink/START via GDS methods, MAGCAL collect/fit/uplink with a synthetic
rotation, TDM written at LOS; (b) packet-mode sim (`SimSpacecraft(packet_mode=True)`) feeding
`ingest_packet` with SNR/freq error → AFC converges, `rf.doppler`, RS rescue counters; (c) the
`integration_tm_ae.bin` vector fed as an E22 byte stream (`[CADU][RSSI]`) into
`GroundStation.ingest` → snapshot keys of PB-5 present and correct; (d) robustness: `Runner.step`
survives an injected exception in every stage (counted in `health.stage_errors`, next stage
still runs), a TDM record with `t_utc` = 1970 is counted in `health.tdm_rejected`, `close()`
returns in < 4 s with a stuck front-panel thread, a Runner with `frontpanel = rcu = true` and no
`arduino` package starts with the WARN event, `snapshot()` serialised 1000 times while another
thread ingests raises nothing, the "pass" window is cleared at AOS and capped, the effective
LOS/FOP timeouts for SX126x profile 0 (416 s / 277 s) and LOS suppression while hibernating.
`test_sdls.py` (F-63/F-64): atomic SN writes, corrupt SN file → uplink BLOCKED until a
protected HK, unprotected HK never moves the SN, TM replay state persisted/restored,
`tm_epoch_regressed`, `cltu.py` frames accepted by the reference receiver for TC/USLP x
AUTH/AE. `test_radio.py` (appended section, PB-8.1): pty tests, radio_tables.csv vs link.py,
GDS AFC glue with an `rf_foff` double returning 1 / -1 / -2 / None.

WS5 extra: `test_web.py` starts `start_web()` on 127.0.0.1:0 with a stub `gs` (fixed snapshot,
recording method calls): every endpoint, 403 without token when exposed, `X-DSSQ-Auth` for
AUTH, 413/400/404, path-traversal attempts on `/api/files/` and `/api/tdm/` rejected,
`json_safe()` removes NaN, `/api/state` has no `science` key, `/api/science` has the five
spectral keys; the dashboard HTML contains no `http://`/`https://` resource reference (no CDN);
**security**: the CSP header is present and its hash equals SHA-256 of the page's only
`<script>` text, the page has no inline `on*=` attribute and no `localStorage` use for a
token, and `innerHTML` appears only with literal (static) markup — checked by a small static
scan of `dashboard.html` (every `innerHTML` assignment's right-hand side must be a string
literal or the `esc()`/static helper), plus a stub snapshot whose EVR text, event text, console
line, pending text, OID ident and file name are `<img src=x onerror=alert(1)>` served through
`/api/state` unchanged (the server never alters data; the page renders it as text);
`main.py` per PB-6.2 (a) and (b).

WS6a extra: in `test_cfdp.py`, EGSE logic with a fake `arduino.app_utils` (injected into
`sys.modules`): partition rules (new value on sc_boot, on epoch 0, same value on a raised
call, a False answer → counter raised above the reported epoch and a NEW value, three refusals
→ stop, refusal at 65534, migration from `python/egse_data`), atomic counter writes, relay loop
(1/0/-1/exceptions), cancel matching, `sc_status` + `sc_status_x` merged, no `Bridge.call` from a
handler (the fake raises if called on the dispatcher path), an exception in one duty does not
escape the user loop.

WS6b extra (after G1): check_firmware.sh runs the WS2 matrix + station `DSSQ_RADIO_SX126X=1`
(1268 and 1262, `-Wno-cpp` while RadioLib's VENTUNO Q warning exists); host/ground suites green;
post-G1 `rf_foff` check: RL's `Sx126xBridge.rf_foff(hz)` returns 1 / -1 / -2 from the MCU and
None on a link failure, and `GroundStation` feeds each to `AfcLoop.confirm` (None mapped to the
link-error code `-3` that WS4 adds to `link.AFC_RPC_RESULTS` as "no answer (bridge)") — run the
WS4 AFC glue test against the real class instead of the double; real Arduino builds of PB-8.5
item 6 incl. the SX126x variants; CI job `arduino-build` compiles the flight sketch (F-65).

### 8.6 WS2 integration recipe (flight executive) — binding order of work
1. `flight.h`: replace `E22 radio_; E22Config rcfg_, pending_cfg_, revert_cfg_` with `Radio& radio_
   = flight_radio(); RadioSettings rcfg_, pending_cfg_, revert_cfg_; RxPacket rx_pkt_;`; delete
   `rx_buf_`, `rx_len_`, `last_rx_byte_ms_`, `awaiting_rssi_`, `rssi_req_ms_`, `service_rx()`,
   `process_rx_packet()`, `NiclaEnv nenv_`, `Spectrometer spec_`, `vc_[]`, `framer_`; add
   `Downlink dl_; ccsds::Sdls sdls_tc_; ccsds::SdlsTm sdls_tm_; As7265x spec_; SpecConfig
   spec_cfg_; SpecProcessor spec_proc_; ccsds::CfdpSender cfdp_tx_; ccsds::CfdpRelayMonitor
   relay_; RelayGate relay_gate_; EvrGate evr_gate_; LoadPlan load_; SsrFileSource
   ssr_src_{ssr_}; CommandSequencer seq_; BeaconMonitor beacon_; MagCalStore magcal_;
   MagBistResult bist_ob_ = mag_bist_not_run(), bist_ib_ = mag_bist_not_run(); bool
   safe_by_fdir_ = true; bool in_setup_ = true;` plus the state named below. The v2 per-APID
   counter array `uint16_t seq_[P_COUNT + 4]` is renamed `apid_seq_[kNumApids]` (12 entries,
   PB-1.1) — its v2 name collides with the `CommandSequencer seq_` and its size (11) cannot hold
   the CFDP index 11. `ccsds::Sdls sdls_` becomes `sdls_tc_`; `evr_budget_` is replaced by
   `evr_gate_`.
2. `setup()` order: display; reset cause; RS init; SSR; SDLS (`sdls_tc_.configure(VGQ_SDLS_TC,
   key, 32, VGQ_SDLS_SPI)`, `sdls_tm_.init(VGQ_SDLS_TM, key, 32)`, or both NONE without
   `sdls_key.h`); `dl_.init(...)`; `seq_.reset(); seq_.set_validator(validate_stored_command,
   nullptr); magcal_.reset(); beacon_.begin(reset_cause_)` (before the first `evr()`);
   `init_radio()` (begin, `radio_boot_power_code`, EVR 0x0608 if derated, configure, EVR 0x0306);
   `init_sensors()` (bus begin with boot bus clear, ARU, `spec_.begin(bus_, kPinSpecInt)`, RM3100s,
   BIST on fitted units → `bist_ob_/bist_ib_`, EVR 0x0910/0x0911, EVR 0x0706); `set_mode(SAFE,
   "boot")`, `safe_by_fdir_ = true`; EVRs 0x0001, 0x0003, 0x0004, 0x0002; `notify("sc_boot")`;
   then `in_setup_ = false; evr_gate_.fill();` (every EVR above is exempt from the routine
   budget, PB-2.6). No `LOGF`/`Monitor` output (PB-0.4). VC0 during the epoch hold (§5, up to
   `kSdlsEpochWaitS` = 120 s without the EGSE): the ten boot EVRs are ≈ 620 octets (15-octet
   header + text, texts from the formats above, e.g. 0x0001 74, 0x0306 77, 0x0004 63, BIST ≈ 50
   characters), inside the 768 octets that routine packets may use; HK/RF generated during a
   long hold are refused from VC0 once they would cross the 256-octet reserve (they stay in the
   SSR, counted in `overflows(0)`), and the reserve keeps room for 0x0121/0x0120 and WARN EVRs.
   With the EGSE running the epoch arrives within seconds and nothing is refused.
3. `loop()` order (each step bounded): SCLK; radio events (≤ 4 `poll()` per pass: PACKET →
   uplink path (crc_ok false allowed, F-38), NOISE → noise/scan, TX_DONE/TX_TIMEOUT → v2
   TX_WAIT_DONE body + TIMECORR reference + clear AUX_TIMEOUT on DONE, FAULT → radio_fault /
   FDIR); spectrometer (`set_poll_budget_us(tx ? 2000 : kSpecPollBudgetUs)`,
   `set_lamp_gate(!mag_sample_pending_)`, `set_exposure_gate(!pa_keyed)` — §13.2 "PA-off
   windows": the driver starts a new exposure (and a burst) only while the gate is open
   (`PH_GATE`, `as7265x.cpp`), so every SURVEY/HDR/LOCKIN/FLUOR exposure is taken with the PA
   off; a BURST runs continuously once started and is NOT stopped by later transmissions (C-26):
   the executive counts the frames transmitted while `burst_active()` and reports them as `tx=`
   in the burst-end EVR 0x0704 —, `poll(now)`, results → `SpecProcessor::process` with
   `SpecAttitude` (ARU quaternion, world ENU, else TRIAD `spec_conj`, NED) → `pack_spec_v31` →
   emit SPEC + EVRs 0x0701/0x0702; burst samples → `pack_specburst` (3 per packet, or the rest at
   burst end) → emit SPECBURST; diag lines → EVR 0x0703 (at most one per pass, only while
   `dl_.backlog(0) + 95 ≤ 512`, PB-2.6); `take_recovered()` → 0x0705; state
   changes → 0x0707; escalation F-03); MAG/ARU sampling state machine (PA off: start RM3100s,
   read ARU pages, collect with `poll_drdy()` across passes ≤ `kMagCollectTimeoutMs`; never while
   `lamps_lit() || lamp_state_unknown()` unless deferred > `kSpecLampDeferMaxMs`, then MAG b4);
   deferred MAGBIST; `seq_.poll()` (one entry); CFDP pacing + `relay_gate_.poll()` (relay start,
   silence and cancel timeouts, PB-2.5); `sdls_tm_.poll_epoch_timeout(uptime_s,
   kSdlsEpochWaitS)` → EVR 0x0121; downlink (`service_tx`: `!radio_.busy(now)`, hibernation
   beacon = `gen_hk()`, SSR playback refill of VC2, `dl_.next_cadu()`, `radio_.transmit()`,
   `notify("tm_cadu")` under the PB-0.4 notification rules, or the pending copy); 1 Hz tick
   (`load_ = plan_downlink_load(...)` + EVR 0x0215 on change, PB-3.3; `evr_gate_.refill()`;
   cadence with the stretched periods incl. ENV/SPEC start requests, FDIR, `beacon_.update()`,
   noise sampling via `request_noise()`, callsign, `sc_status` (+ `sc_status_x` every 5 s),
   deferred to a pass without another large notification); display 4 Hz (row 7 cols 5..7 =
   beacon level).
   WS2 report: the loop-timing table lists every call above with its bound, including
   `Bridge.notify` (bounded by the room check: no wait; heap transient only) and the
   `provide_safe` handlers run by `__loopHook` between loop iterations (`hl_cltu` = one
   `tc_accept`, `cfdp_pdu` = parse + one emit); measured `loop_max_ms` with the EGSE attached is a
   VERIFY T-HW item.
4. Uplink path: `tc_accept()` → counters/EVRs per stage (0x0101.., 0x0111.., FDIR_SDLS_AUTH,
   `notify("sdls_sn")` after SDLS OK) → exit hibernation on any authenticated frame → FARM
   verdict handling (v2) → for ACCEPT/ACCEPT_BYPASS: `parse_tc_header` → `execute_user(user, n,
   seq or 0xFF)`; then for ACCEPT, ACCEPT_BYPASS, CONTROL_UNLOCK, CONTROL_SET_VR:
   `beacon_.on_ground_contact(beacon_sample())`.
5. `execute_user(const uint8_t* u, size_t n, uint8_t tc_seq)`: `parse_user_data` → per PB-2.3 →
   CMDVER (priority push), counters, EVRs (no `LOGF`, PB-0.4). Stored commands come from
   `seq_.poll()` with tc_seq 0xFE.
6. Packet generators: `gen_hk/rf/timecorr/mag/att/env` per PB-1; SPEC/SPECBURST/CFDP are
   event-driven (not in the cadence table except the SPEC start request). `emit()` gains `bool
   record = true` and returns false for an unknown APID.
7. FDIR additions: F-25, SX126x faults → FDIR_RADIO_CFG (16..19) cleared by FAULT 0, T37S faults
   unchanged; command loss falls back to `kRadioRateCode`/`kRadioChannel`. FDIR_VC_CONGEST
   thresholds change from v2 "> 800 set / < 300 clear" to "> 640 set / < 300 clear" octets:
   routine packets can now fill a VC only to 768 octets (PB-1.2 reserve), so 800 would never be
   reached on VC1.
8. `sketch.ino`: `Bridge.begin();` (blocks until the router answers — setup only, documented,
   PB-0.4); `#if VGQ_DEBUG_MONITOR Monitor.begin(); #endif`; then `provide_safe` (each blocks
   until the router registers it — setup only) for `set_partition` (`bool(int)` →
   `g_fsw.egse_set_partition(p)`, which returns bool after the range/epoch checks),
   `set_sdls_sn` (`bool(int)`), `hl_cltu` (`bool(String)`), `cfdp_pdu` (`int(String)`), then
   `g_fsw.setup()`; the return values of `Bridge.begin()` / `provide_safe()` are checked; a
   failure is passed to `g_fsw.setup()`, which raises EVR 0x0005 (sev 2) `Bridge registration
   failed: %s` (the names that failed) after the boot EVRs. Header comment
   updated to v3 (E22-400T33D default, SDLS AE, USLP, CFDP). `sc_status`/`sc_status_x` per
   PB-4.1. `LOGF` in flight.cpp:
   `#if VGQ_DEBUG_MONITOR` → the v2 macro; `#else` → `do { if (0) { (void)snprintf(nullptr, 0,
   __VA_ARGS__); } } while (0)` (format still type-checked, no code, no unused-variable
   warnings).
9. AS-T6 escalation: when `spec_.state()` enters `ST_FAULT` and `bus_.consecutive_errors() >= 3`,
   or FDIR I2C_BUS fires, call `bus_.recover()` (the emulated open-drain bus clear of F-03) at
   most once per 10 s.

### 8.7 Definition of done (Phase B)
All PB-8.5 checks green at gate B3 and again at gate B4 (PB-8.1); every PB-7 row with
owner WS1..WS6 closed (rows owned by RL/D listed in the Phase B report as handed over); every
workstream report lists: files changed, test commands + results, VERIFY items, deviations from
this specification with reasons (none expected), and anything left for Phase C.

---------------------------------------------------------------------------------------------------

## 9 Review log (adversarial review of this specification, 38 findings)

Every finding was checked against the sources named in it. **None was rejected outright.** Three
were accepted with a correction of their evidence (12, 21, 28) and one with a broader fix (38).
"Fixed in" names the section that now carries the rule.

| # | Verdict | Evidence checked | Fixed in |
|---|---|---|---|
| 1 | correct | `monitor.h`: `write()` → `operator bool()` → blocking `call("mon/connected")` when not connected; `_compatibility_mode` → `call("mon/write")`; `bridge.h` `RpcCall::result()` loops `while (true)`; flight.cpp `LOGF` → `Monitor.println` in `evr()` and `execute()` | PB-0.4 (Monitor rule, setup-only blocking calls), PB-3.1 `VGQ_DEBUG_MONITOR`, PB-8.6 items 2, 3, 5, 8 |
| 2 | correct | v2 dashboard inserts EVR/event/console/pending/tile text with `innerHTML` (lines 170, 281, 320-330, 338, 344); `uslp.ident_text()` accepts any printable ASCII; token in `localStorage` | PB-5.7 (text-only rendering, one `esc()`, `sessionStorage`, AUTH never stored), PB-5.6 (hash-based CSP, `nosniff`), PB-5.3.4, PB-8.5 WS5 tests |
| 3 | correct | measured with Python `json.dumps(separators=(",", ":"))`: 45 keys, 503 characters typical, 596 worst case | C-25, PB-4.1 (`sc_status` ≤ 294, `sc_status_x` ≤ 409, host-tested formatter) |
| 4 | correct | `cfdp_make_seq(0, n)` = n; ground `cfdp.py` `Receiver.receive()`: a key in `finished` → counted "late", PDUs dropped | PB-1.13, PB-2.2, PB-2.5 (CE_BUSY + EVR 0x0A06 while partition 0) |
| 5 | correct | `CfdpRelayMonitor::acceptable()` checks form and the EGSE flag only (`cfdp.cpp`) | PB-2.5 / PB-8.2 `RelayGate`, PB-4.1 `cfdp_pdu` |
| 6 | correct | flight formulas: SX126x profile 1 busy 17.318 s, SAFE period 69.27 s, capacity 2.21 octet/s (153-octet field) vs 4.09 octet/s of HK+RF+TIMECORR; profiles 0..4 overload, every E22 case ≤ 0.70 x without over-temperature | C-27, PB-3.3 (run-time stretch + capacity-based EVR budget, host test `downlink_load`) |
| 7 | correct | `app_utils/app.py` `loop()`: `except Exception` → return 1 → `run()` → `sys.exit(1)`; `TdmRecord` raises for `t_utc` outside 2000..2100; `_get_bridge()` raises `AppError(Exception)` | PB-6.1, PB-8.4 Runner rules (stage boundary, TDM guard), PB-4.2 (`AppError` → `ConnectionError`) |
| 8 | correct | `compose_template.go`: volumes = app home → `/app`, `/dev`, board mounts only | PB-6.1, PB-6.2 step 2 + layout test |
| 9 | correct | "config/sdls_key.hex" resolved against `DATA` = `data/config/sdls_key.hex`; citation pointed to PB-6.1 | PB-3.2 (`key_file`, `archive_dir`, `http_host` rows), PB-6.2 step 3, `gen_sdls_key.py` message + test |
| 10 | correct | `cltu.py` reads `[sdls] enabled`, `SdlsSender` default service AUTH, no `tc_framing`, `MAX_HARDLINE_OCTETS = 100`, docstring `python/egse_data` | PB-7 F-63, PB-8.1 (WS4 owns `cltu.py`) |
| 11 | correct | flight.h:97 `uint16_t seq_[P_COUNT + 4]` (P_COUNT = 7 → 11 entries) vs 12 APIDs and `CommandSequencer seq_` | PB-1.1 (`kNumApids`, `apid_seq_`), PB-8.6 item 1 |
| 12 | correct risk, evidence corrected | the driver emits at most 1 header + ceil(32/4) = 8 COEF lines = **9 lines** (`build_diag` reads 4 words per line), ≈ 650-750 octets of EVR packets, not "about 35 lines, 3.3 KB"; an unpaced dump of that size into the 1024-octet VC0 is still unsafe | PB-2.6 (one line per pass while `backlog(0) + 95 ≤ 512`; the driver holds the next line), PB-2.3 SPECDIAG row, PB-8.6 item 3 |
| 13 | correct | flight.cpp: `evr_budget_ = 10`, refilled only when `t % 60 == 0`; ten boot EVRs below severity 3 | PB-2.6 (`EvrGate`, setup EVRs and 0x0120..0x0122 exempt, bucket full after setup), PB-8.6 item 2 (VC0 budget during the epoch hold), PB-1.2 priority reserve |
| 14 | correct | gds.py `snapshot()` returns `"tlm": self.tlm`; web.py serialises after the lock is released | PB-5.1 (`json_safe` copy inside the lock + concurrency test) |
| 15 | correct | frontpanel.py `_tx_loop` calls `gs.snapshot()` each second; `/metrics` and `/api/state` too | PB-5.1, PB-5.2, PB-5.6, PB-8.4 (`science_snapshot()`, `panel_status()`, cached XTCE) |
| 16 | correct | rxstats.py:94 `("pass", 10 ** 9)`, never cleared at AOS | PB-5.3.2 `windows` (cleared at AOS, `maxlen` 20 000, `truncated`) |
| 17 | correct | rule (3) retried the same value on any failure, including an explicit refusal | PB-6.3 rules (3)/(3a) + ground cross-check `tm_epoch_regressed` |
| 18 | correct | sdls.py 117-118 (`int(read_text() or 0)`), 148-149 (`write_text` per frame); TM `last` in RAM only. One deviation from the proposed fix: a corrupt SN file blocks only the TC uplink (the GDS still starts, so telemetry, alarms and the web UI keep working) and self-heals from the flight's `sdls_last_sn` in an AE-protected HK | PB-7 F-64, PB-5.3.4, PB-5.5 |
| 19 | correct | no simulator section existed; v2 sim.py has no USLP/SDLS TM/CFDP/SPECBURST/packet mode; F-03 cited "PB-8.2 WS2 item 9" | PB-8.4a (simulator built from the existing reference builders), F-03/F-51 references |
| 20 | correct | `arduino` not importable in the development venv; frontpanel.py imports `arduino.router_bridge`; station.toml has `frontpanel = true`, `rcu = true` | PB-3.2 row, PB-8.4 "Bridge availability" |
| 21 | correct, fix strengthened | `Packer` = `std::vector` + `emplace_back`; `String` arguments; `ZephyrSerial::write()` yields while the 1024-octet ring is full; UART 115200 baud. Rather than only documenting a wait, the notifications are now gated on `ARDUINO_ROUTER_SERIAL.availableForWrite()` (router UART = LPUART1 per the UNO Q overlay; the only writer is the loop thread), so no write waits | PB-0.4 (heap exception + bound, notification rules), PB-4.1, PB-8.6 item 3 timing table |
| 22 | correct | EVR 0x0307 severity 0 vs `evr_level_ = 1` | PB-2.6 (INFO with running count), F-38 |
| 23 | correct | `set_exposure_gate()` exists (`as7265x.h:184`, checked in `PH_GATE`) and was never called | C-26, PB-8.6 item 3, EVR 0x0704 `tx=` |
| 24 | correct | `rf_foff` absent from today's `sx126x.py`; RL's tests are in `test_radio.py` (FakeBridge block) | PB-8.1 (shared-file rule, contract API + `None`), F-32 (-3 mapping), PB-8.5 WS6b post-G1 check |
| 25 | correct | `realbuild/bin/acompile.sh`, `out/sanity_summary.txt` "TOTAL ok=52 unexpected=0"; snapshot flight build fails at flight.h:84 as expected | PB-8.5 item 6 (with the temporary-copy rule until F-62) |
| 26 | correct | `wiring_digital.cpp` 19-33 (main and 1.0.0 cores): no open-drain branch; `Wire.cpp` 62-68 `begin()` re-applies pinctrl | F-03 implementation text, PB-8.6 item 9 |
| 27 | correct | `SHUTDOWN_GRACE_PERIOD_S = 5.0`; v2 placeholder `main.py` | PB-6.2 (`main()`/`prepare()` + guard), PB-8.4 shutdown rule, PB-6.3 EGSE structure |
| 28 | correct, lengths re-measured | worst cases with this spec's own argument ranges: 0x0001 89, 0x0306 114, 0x0A01 100 characters (the review quoted 88/~105/83 from other example values); also 0x0121 reached 87 with `%lu` | PB-2.6 (shortened formats, worst cases 74 / 77 / 66 / 73, WS2 report lists all) |
| 29 | correct | §24.1: six fields, `seq_next_dt_s` u16 → 7 octets | C-01 |
| 30 | correct | 2 x 154 = 308 < 320 lets a third packet in | PB-2.5 (`backlog + 12 + kCfdpMaxPduLen ≤ 320`), PB-3.1, PB-4.1 |
| 31 | correct | `tc_parse` fails at FRAME_REJECTED, so `frame` is not valid there; tc.h has no name function | PB-8.2 `TcOutcome` comment, `tc_parse_result_name` in `datalink.h` |
| 32 | correct | link.py:430 `"power_verified": m.name == E22_T37S` | PB-5.3.2 `budget` (one rule) |
| 33 | correct | SAFE period at profile 0 = 4.0 x 34.636 s = 138.5 s → 3 periods 416 s; beacon up to 3600 s | PB-3.2 (derived LOS/FOP minima), PB-5.3.2 `state`, PB-5.5 (no LOS alarm while hibernating) |
| 34 | correct | gds.py:44 and :46 both import `RADIO_FAULTS`; the second (framesync, ground codes only) wins | PB-5.3.5 |
| 35 | correct, completed | `/metrics` derives gauge names from the key names (web.py `prometheus()`); besides `lost_mcfc`, the spec also renamed `sensitivity_est_dbm` and `noise_bw_assumed_hz` | PB-5.3.2 (v2 names kept where meaning unchanged: `air_rate_bps`, `air_rate_code`, `margin_est_db`), PB-5.3.5, PB-5.6 (three documented renames) |
| 36 | correct | `link.airtime_s(T33D, 2)` = 236 x 8 x 1.3 / 2400 = 1.023 s; flight `e22_airtime_ms` adds the 9600-baud UART transfer (245 ms) = 1267 ms | PB-5.3.3 (`sc_airtime_s` + new `sc_busy_s_est`), PB-5.4 |
| 37 | correct, value changed | no source existed for 1500 ms; the driver's own lamp-watchdog limit for the longest exposure is 1424 + 44 + 50 + 500 = 2018 ms (`as7265x.cpp:976`), so 1500 could end a deferral while a lamp may still legitimately be lit | PB-3.1 `kSpecLampDeferMaxMs` = 2100, engineering value, VERIFY T-SC-08 |
| 38 | correct, broader | after WS1 alone the ground cross-check sees HK 78 vs 58; after WS3 alone v2 gds.py imports the removed `AIR_RATES_BPS` | PB-8.1 gates B1-B4, PB-8.5 heading, PB-8.7 |

Found during the re-read (not in the review): §23 rule 3 stale flight `sketch.yaml` libraries
(F-62); the CI workflow had no owner (F-65); the VC reserve makes the v2 FDIR_VC_CONGEST
threshold of 800 octets unreachable (PB-8.6 item 7: 640); a failed Bridge registration was silent
(EVR 0x0005); `apids.<name>.iat_s` so the ground never assumes v2 packet periods (PB-5.4); the
CI host-test flags (`-Werror`, sanitizers) added to PB-8.5 item 1.
