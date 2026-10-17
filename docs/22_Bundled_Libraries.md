# 22 - Bundled Third-Party Libraries (patched)

Two third-party Arduino libraries are built into the sketches from a **pinned, verified and
patched copy** that sits next to each sketch, instead of a copy the board downloads:

* **RadioLib 7.8.1** - the SX126x LoRa driver of the long-range SPI radio option (both sketches).
* **Arduino_RouterBridge 0.4.3** - the MCU side of the MCU <-> Linux RPC link (both sketches).

This page says what each library does here, why it is bundled, what every patch hunk changes
and why (with the evidence), how the copies are fetched and checked, how the build, CI and the
App Lab zips use them, and how to move to a newer upstream release.

Plain-words version: the repository never stores the libraries' source code. It stores a small
patch file and a tool. The tool downloads the exact upstream release from GitHub, proves it is
the right one (commit hash and a checksum of every file), applies the patch, and writes the
result into `sketch/libraries/<name>`. The App Lab zips contain that result, so the boards never
download anything.

Decision records: [12_Design_Decisions.md](12_Design_Decisions.md) D-40 (RadioLib), D-41
(Arduino_RouterBridge), D-42 (RPC mailbox). Contract sections: 25.2, 30, 41, 42.

---

## 1 Summary

| | RadioLib | Arduino_RouterBridge |
|---|---|---|
| Upstream | https://github.com/jgromes/RadioLib.git | https://github.com/arduino-libraries/Arduino_RouterBridge.git |
| Pinned tag | `7.8.1` | `0.4.3` (the release the arduino:zephyr platform installs) |
| Tag commit | `2325a38a89dd0f0e402c19f67fe910111da1e246` | `d681b87211864148367f2d77096ae84c5badcb2f` |
| Pristine tree SHA-256 (section 5.3) | `84370304e2fa416a2d4aa798b49a576d6821252691191afdc3ed413a49fa9189` | `c86d601236aef85b351c39c472273cd8bf96c3d38a8a18c708788945b5813194` |
| Bundled files | `library.properties`, `license.txt`, `src/` (176 files) | `library.properties`, `LICENSE`, `src/` (11 files) |
| Patch | [../tools/radiolib-7.8.1.patch](../tools/radiolib-7.8.1.patch) (1 hunk, 1 file; D-79) | [../tools/arduino_routerbridge-0.4.3.patch](../tools/arduino_routerbridge-0.4.3.patch) (2 hunks, 1 file) |
| Patch SHA-256 (as written into PROVENANCE.txt) | `48e4854224a10b73710453ec47e6a8987f266c89fa044e4a0a75c460ff0c738b` | `6324a5d39715d4efad41e6c5eeb641542769b3f5bc65289f2436b5aa4ab8fd9c` |
| Tool | [../tools/radiolib.py](../tools/radiolib.py) | [../tools/routerbridge.py](../tools/routerbridge.py) |
| Shared engine | [../tools/libbundle.py](../tools/libbundle.py) | same |
| Bundled as | `<sketch>/libraries/RadioLib` | `<sketch>/libraries/Arduino_RouterBridge` |
| Licence | MIT (`license.txt`, unchanged) | MPL-2.0 (`LICENSE`, unchanged; modified file: `src/bridge.h`) |
| Compiled when | only in an SX126x build (`VGQ_RADIO = 3` flight, `DSSQ_RADIO_SX126X 1` station) | always (both sketches) |

The digests and file counts above were re-checked on 2026-10-12 by running both tools'
`fetch` and `check` commands (section 5) against GitHub; the shell formula of section 5.3 gives
the same two pristine digests.

---

## 2 Why bundle a patched copy instead of downloading the library

| Reason | Detail and source |
|---|---|
| Offline installation | The owner asked for a setup that needs no internet on the boards (contract 23). An App Lab sketch profile may list a library either as an index library `Name (version)`, which arduino-cli downloads, or as `- dir: <path>`, a folder next to the sketch (arduino-cli 1.3.0 and later; App Lab embeds arduino-cli 1.5.2). A `- dir:` library inside the App folder travels with the zip. |
| Exact version | The drivers rely on library internals (both sketches refuse to compile against any RadioLib other than 7.8.1, section 3.5). A pinned tag, a verified commit and a checksum of every bundled file make the build reproducible. |
| A defect that our code cannot fix | App Lab always builds the sketch's **default profile**, and a profile cannot carry build flags (arduino-cli profiles have no build-properties entry, contract 25.1). A `-D` option therefore never reaches the library's own `.cpp` files. The only way to change a library build option for every translation unit is a file inside the library (RadioLib `BuildOptUser.h`), and a defect inside a header class (`bridge.h`) can only be fixed in that header. |
| Small, reviewable delta | The repository holds only the patch (a few dozen lines) and the tool, never the third-party source. The patch header explains every hunk. The copies written by the tool are git-ignored. |
| Owner decisions | Contract 30 (RadioLib: "go ahead with the bundled patched copy"; the stock Library-Manager variant is not offered again) and contract 41 (Arduino_RouterBridge: "bundle a patched copy of the Bridge library with a larger stack"). |

Alternatives that were rejected are listed in the decision log (D-40, D-41): the stock library
from the Library Manager, an in-house SX126x driver (contract 23 rule 1, reversed by the owner
in contract 25), and keeping the stock RouterBridge with only the mailbox work-around.

---

## 3 RadioLib 7.8.1

### 3.1 What it does in this project

RadioLib is a general radio-chip library. Only its SX126x driver (with `PhysicalLayer`, `Module`
and the Arduino HAL) is used, and only by the **SX126x (SPI) radio option**:

* flight: [../vgq1-spacecraft/sketch/src/drivers/sx126x.h](../vgq1-spacecraft/sketch/src/drivers/sx126x.h) / `.cpp` (`VGQ_RADIO = VGQ_RADIO_SX126X` in `config.h`);
* station: the SX126x path of [../dssq-groundstation/sketch/sketch.ino](../dssq-groundstation/sketch/sketch.ino) (`#define DSSQ_RADIO_SX126X 1`).

The default builds use the UART E22 modules and never include `RadioLib.h`: the include is
behind the radio-model switch, and arduino-cli compiles a profile library only when a sketch
file includes it. The copy is still staged (the profile names it), so one sketch.yaml serves all
radio options.

### 3.2 Hunk 1 - `src/BuildOpt.h`: a platform entry for the VENTUNO Q (audit RL-01)

**In plain words.** RadioLib has a list of known boards. For each board it knows how to call the
Arduino pin functions. The VENTUNO Q is not on the list, so RadioLib treats it as an "unknown
Arduino" and calls the pin functions in a way the board's core rejects. The station sketch with
the SX126x option did not compile at all. The hunk adds the VENTUNO Q to the list, with exactly
the same settings as its sibling board, the UNO Q.

| | |
|---|---|
| Defect | arduino:zephyr builds the VENTUNO Q with `-DARDUINO_VENTUNO_Q -DARDUINO_ARCH_ZEPHYR` (boards.txt `ventunoq.build.board=VENTUNO_Q`). `BuildOpt.h` only has a branch for `ARDUINO_ARCH_ZEPHYR && ARDUINO_UNO_Q`, so the VENTUNO Q takes the "Unknown Arduino" branch with empty cast macros. `ArduinoHal.cpp` then passes `uint32_t` values to `::pinMode`, `::digitalWrite` and `::attachInterrupt`, but ArduinoCore-API 1.5.2 declares only the typed global functions (`PinMode`, `PinStatus`); its integer overloads live in `namespace arduino`, which a `::`-qualified call never searches. |
| Evidence | Pristine 7.8.1 for the VENTUNO Q with the real toolchain (arduino-cli 1.5.2, Zephyr SDK `arm-zephyr-eabi-g++`, ArduinoCore-zephyr `b08a650` with ArduinoCore-API 1.5.2): `hal/Arduino/ArduinoHal.cpp:28:52`, `:35:59` and `:49:89` "invalid conversion from 'uint32_t' to 'PinMode' / 'PinStatus'", compile exit status 1, plus the `RadioLib.h:74` "#warning ... might not be compatible". 79 of the 80 library translation units compile; on the UNO Q all 80 do. Reproduced on every run of `check_firmware.sh` by the negative control (section 7). |
| Fix | A `#elif defined(ARDUINO_ARCH_ZEPHYR) && defined(ARDUINO_VENTUNO_Q)` branch identical to the UNO Q one: platform name "Arduino VENTUNO Q (Zephyr OS)" and the casts `(PinMode)`, `(PinStatus)`, `(PinStatus)`. Same core, same API, so the same casts are correct. |
| Result | 80 of 80 library translation units on both boards, no warning; real arduino:zephyr builds of the station sketch (E22, SX1268, SX1262) on core main and core 1.0.0 with 0 warnings in sketch or library (commit `f0ff5e8`). |

### 3.3 Hunk 2 - `src/BuildOptUser.h`: no heap per SPI transfer, unused modules excluded (audit RL-02, RL-03)

**In plain words.** By default RadioLib asks for two new memory blocks from the heap for
**every** command it sends to the radio chip, and gives them back afterwards. The flight rules
forbid heap use after start-up (Power of Ten rule 3, contract 17.3), and on this core a failed
request is not even detected: RadioLib would write to address 0. The hunk switches RadioLib to
its "static only" mode, where the two buffers live on the stack. It also switches off every radio
chip and protocol module the project does not use, so builds are smaller and faster.

| | |
|---|---|
| Defect | `RADIOLIB_STATIC_ONLY` defaults to 0, so `Module::SPItransferStream()` allocates two `new uint8_t[]` buffers for every SPI command (`Module.cpp:340`, `:383`; freed at `:468-469`). The core's `operator new` returns `malloc()`'s result unchecked (ArduinoCore-zephyr `cores/arduino/new.cpp`, `NEW_TERMINATES_ON_FAILURE` commented out) and RadioLib never tests it: on heap exhaustion the first store through the buffer (`Module.cpp:346`) writes to address 0. |
| Evidence | `objdump` of the pristine `Module.o` shows `operator new[]` / `operator delete[]` references in `SPItransfer` / `SPItransferStream`, and none when patched. An AddressSanitizer harness with the core's `operator new` semantics reports "SEGV on unknown address 0x000000000000 ... Module::SPItransferStream Module.cpp:346" on the pristine library. |
| Why in this file | App Lab profiles cannot pass `-D` flags. `TypeDef.h` includes `BuildOptUser.h` before `BuildOpt.h`, so this file is the one place that every library and sketch translation unit reads identically. Identical options in every unit also avoid a one-definition-rule difference in class layouts (for example `RADIOLIB_EXCLUDE_DIRECT_RECEIVE` changes members). |
| Fix | `#define RADIOLIB_STATIC_ONLY (1)`: two stack arrays of `RADIOLIB_STATIC_SPI_ARRAY_SIZE` = 268 octets per transfer. The largest transfer the drivers issue is ReadBuffer / WriteBuffer of 255 octets + 2 command octets + 1 status octet = 258 octets. Plus `RADIOLIB_EXCLUDE_*` for every 7.8.1 module and protocol except SX126x and LoRaWAN (CC1101, nRF24, RF69, SX1231, Si443x, RFM2x, SX127x, STM32WLx, SX128x, LR11x0, LR2021, AFSK, AX.25, Hellschreiber, Morse, RTTY, SSTV, direct receive, Bell, APRS, 4-FSK, pager, ADS-B). Each is written exactly `(1)` because `BuildOpt.h` re-defines `RADIOLIB_EXCLUDE_STM32WLX` as `(1)`. |
| Why LoRaWAN stays (RL-03) | `LoRaWANPacMan.cpp` and `LoRaWANPackageTS009.cpp` test `RADIOLIB_EXCLUDE_LORAWAN` on their first line, before any include has reached `BuildOptUser.h`, so with the exclusion set they compile against excluded headers and fail ("'LoRaWANPackageTS003' was not declared", "'LoRaWANPackageTS009' does not name a type"). Unused LoRaWAN code is removed by the linker (`-Wl,--gc-sections`, platform.txt). |
| Stack cost | Measured with GCC `-fstack-usage` (arm-zephyr-eabi): `SPItransferStream` 584 octets; the deepest SX126x call chain of the flight driver 1.3 KiB, against the 32 KiB Zephyr main-thread stack that runs `setup()`, `loop()` and the Bridge `provide_safe` handlers. RadioLib must never be called from a Bridge `provide` (non-safe) handler, which would run on the bridge thread (section 4). |

### 3.4 Defects that are NOT patched - handled in our drivers

Contract 25.2: anything that can be handled from our side stays in our code. The audit of
commit `f0ff5e8` found these library behaviours and works around each of them in
`drivers/sx126x.cpp` (flight) and in the station sketch:

| RadioLib 7.8.1 behaviour | Our handling |
|---|---|
| Image calibration skipped for frequency moves below 20 MHz, and the cached frequency survives an NRST reset | image calibration requested explicitly for the operating channel after every (re)initialisation and channel change |
| `setOutputPower()` restores the 60 mA OCP | OCP set explicitly afterwards and read back |
| `writeRegister()` never reports a failure | every register write that matters is verified by read-back |
| `getRSSI(true)` returns SignalRssiPkt | RssiPkt read directly from GetPacketStatus |
| `getSNR()` / `getFrequencyError()` return error codes as if they were measurements | each status read is checked before the value is used |
| `startReceiveDutyCycleAuto()` periods violate Semtech DS 13.1.7 for a long wake preamble | duty-cycle periods computed so that Tpreamble + Theader <= 2 x rxPeriod + sleepPeriod for the 32-symbol WAKE preamble |
| Duty-cycled RX leaves the RF switch idle | RF switch set to its RX path for hibernation listening |
| `startChannelScan()` without a wake-up from sleep; `stageMode(TX)` without SetStandby | every sequence that may meet a sleeping chip starts with `standby()`; SetStandby before staging a frame |
| `delayMicroseconds(1)` is 0 us on the Zephyr core | the HAL keeps a 1 us settle before the first BUSY sample |
| Unbounded BUSY waits in `begin()`, `launchMode()`, `scanChannel()` | a BUSY guard in the HAL bounds them; a stuck BUSY raises a FAULT and a non-blocking reset-and-reconfigure |

Details: the module header of [../vgq1-spacecraft/sketch/src/drivers/sx126x.h](../vgq1-spacecraft/sketch/src/drivers/sx126x.h)
(Semtech SX1261/2 datasheet rev 2.2 sections cited per rule).

### 3.5 Guards in our code

Both SX126x code paths refuse to compile against the wrong library:

```cpp
#if RADIOLIB_VERSION_MAJOR != 7 || RADIOLIB_VERSION_MINOR != 8 || RADIOLIB_VERSION_PATCH != 1
#error "... verified against RadioLib 7.8.1 only ..."
#endif
#if !RADIOLIB_STATIC_ONLY
#error "... not the patched copy: run python3 tools/radiolib.py fetch <sketch>/libraries/RadioLib"
#endif
```

(`vgq1-spacecraft/sketch/src/drivers/sx126x.h` and `dssq-groundstation/sketch/sketch.ino`.)
A stock RadioLib therefore fails at the first line instead of building a sketch that allocates
on every SPI transfer.

---

## 4 Arduino_RouterBridge 0.4.3

### 4.1 What it does in this project

On both Arduino Q boards the microcontroller (MCU) and the Linux processor talk through the
`arduino-router` service over a UART, with MessagePack-RPC. Arduino_RouterBridge is the MCU
side: `Bridge.begin()`, `Bridge.notify()`, `Bridge.provide()` / `Bridge.provide_safe()`, and
the App Lab `Monitor`. Its dependencies come unchanged from the platform: Arduino_RPClite 0.3.1,
MsgPack 0.4.2, DebugLog 0.8.4, ArxContainer 0.7.0, ArxTypeTraits 0.3.2.

| Sketch | Services the MCU provides (`provide_safe`) | Notifications the MCU sends |
|---|---|---|
| Flight (UNO Q) | `set_partition`, `set_sdls_sn`, `hl_cltu`, `cfdp_pdu` | `tm_cadu`, `sc_status`, `sc_status_x`, `sc_evr`, `sc_boot`, `sdls_sn`, `cfdp_req`, `cfdp_cancel` |
| Station (VENTUNO Q) | `fp_line`, `rcu_mode`; SX126x build also `rf_tx`, `rf_cfg`, `rf_foff`, `rf_noise`, `rf_cad`, `rf_wake` | `fp_in`; SX126x build also `rf_rx`, `rf_evt` |

The full RPC contract (arguments, sizes, the 480-character notification limit) is in
[04_Space_Data_Link_ICD.md](04_Space_Data_Link_ICD.md).

The library's README promises that a method registered with `provide_safe()` runs "in the main
loop thread". Phase C review finding C50 (critical, section 4.3) showed that the stock 0.4.3 code
does not keep that promise, and a stack measurement showed its own thread stack is too small
(section 4.2).

### 4.2 Hunk 1 - `src/bridge.h`: bridge thread stack 500 -> 1536 octets

**In plain words.** `Bridge.begin()` starts a helper thread called "bridge" that checks for RPC
messages every millisecond. The library gives that thread 500 octets of stack. We measured how
much it really needs: about 1084 octets in the worst case, more than twice as much. The board has
no hardware stack protection, so an overflow would silently overwrite other memory. The hunk gives
the thread 1536 octets (the measured worst case plus 42 %).

| | |
|---|---|
| Defect | `Bridge.begin()` starts the "bridge" thread (`update()` every 1 ms, priority 5) with `UPDATE_THREAD_STACK_SIZE` = 500 octets. Zephyr has no stack guard in these builds (`CONFIG_HW_STACK_PROTECTION` and `CONFIG_STACK_SENTINEL` not set). |
| Evidence | GCC `-fstack-usage -fcallgraph-info=su` over every sketch, library and core translation unit, plus the frames of the core image's kernel / libc / UART driver functions (`push` / `vpush` / `sub sp` in `zephyr.elf`), with indirect calls resolved to their targets. `BridgeClass::update()` alone: 368 octets (it holds an `RPCRequest<256>`). Deepest chain from the thread entry: 920 octets (stock 928): `z_thread_entry` 8, `updateEntryPoint` 8, `update` 368, `get_rpc` 40, `decode` 16, `parse_packet` 72, `unpackObject` 80, `unpackExt` 32, `object::ext` 56, vector growth 40, `malloc` 16, `k_mutex_lock` ... `sys_clock_elapsed` 184. FP exception frame (lazy stacking, `CONFIG_FPU_SHARING`) 108. TLS block carved from the stack top (`__tdata` 8 + `__tbss` 48) 56. **Worst case 1084 octets** (stock 1092). The stock stack is exceeded as soon as `get_rpc()` parses a request: `update` 368 + entry 16 + TLS 56 = 440 octets before any callee. Heap-corruption error paths (`LOG_ERR` in immediate log mode, a 1056-octet frame) are excluded: they only follow memory corruption. |
| Fix | `#define UPDATE_THREAD_STACK_SIZE 1536` = 1084 + 452 octets (42 %) margin. Pool limits: `k_thread_stack_alloc()` serves sizes up to `CONFIG_DYNAMIC_THREAD_STACK_SIZE` (1024, 2 slots) from its pool and larger ones, with `CONFIG_DYNAMIC_THREAD_ALLOC=y`, from the 32 KiB system heap (`CONFIG_HEAP_MEM_POOL_SIZE`). 1024 would not hold the worst case with any margin, so the stack now comes once, in `begin()` during `setup()`, from that heap, which nothing else in the core or the sketches uses; the pool slot it used to take stays free. |
| Re-checked on every push | [../tools/host_test/routerbridge/stackcalc.py](../tools/host_test/routerbridge/stackcalc.py) recomputes the worst case from a real build compiled with `-fstack-usage -fcallgraph-info=su` (deepest chain from `updateEntryPoint()` + `z_thread_entry` + the FP exception frame + the TLS block, kernel / libc / UART functions sized from the core image's disassembly, indirect calls resolved by explicit rules) and fails unless it fits the bundled copy's `UPDATE_THREAD_STACK_SIZE`, with no variable-sized frame and no unresolved indirect call on an explored path. Two paths are cut because they cannot run on the bridge thread: the flight's EGSE services (`egse_`, executed only on the loop thread) and Zephyr's `LOG_ERR` construction (`z_log_msg_runtime`, reached only after heap corruption). |

### 4.3 Hunk 2 - `src/bridge.h`: `update()` never executes `"__safe__"` methods

**In plain words.** The library has two ways to answer an RPC: on its own helper thread
(`provide`), or later, in the sketch's main loop (`provide_safe`), where the sketch's data is in a
consistent state. The stock code mixed them up: the helper thread also picked up the "safe"
requests and ran them at once, interrupting `loop()` at any point. A flight handler could then
change radio, security or queue state while `loop()` was in the middle of using it. The hunk
makes the helper thread leave "safe" requests in the queue; the main loop runs them between two
`loop()` calls, as the README promises.

| | |
|---|---|
| Defect | `update()` (bridge thread) fetches requests with `server->get_rpc(req)` without a tag, and Arduino_RPClite 0.3.1 `RPCServer::get_rpc()` filters by tag only when one is given (`server.h:60`). So the bridge thread also takes the methods registered with `provide_safe()` and runs them, preempting `loop()` (main thread priority 14) anywhere. Only `update_safe()`, called from `__loopHook` between two `loop()` calls, filters on `"__safe__"`. |
| Evidence | A host test compiled against the real `bridge.h` and the real Arduino_RPClite 0.3.1, MsgPack 0.4.2, DebugLog, ArxContainer and ArxTypeTraits sources (only Zephyr and the UART stubbed): with the stock header, `update()` runs all three `provide_safe` requests of the test on the bridge thread (4 of 12 checks fail); with the patch none, and each runs exactly once from the loop hook (12 of 12 pass, under ASan + UBSan). The test is in the repository ([../tools/host_test/routerbridge/bridge_safe_test.cpp](../tools/host_test/routerbridge/bridge_safe_test.cpp), driver `run.sh`) and runs in CI, including the negative control: the stock tag must fail exactly those 4 checks. It refuses platform libraries other than the audited Arduino_RPClite 0.3.1 and MsgPack 0.4.2. The defect was first found by the Phase C review (finding C50, critical) by reading `bridge.h:204-210, :247` and `server.h:60`. |
| Fix | Before `get_rpc()`, `update()` peeks at the method name of the request at the head of the transport's decoder (the same decoder object `get_rpc()` reads, under the same `read_mutex`, so the head cannot change in between) and leaves it queued when that method is bound with the `"__safe__"` tag, or when no method name is available yet. `update_safe()` then takes it on the loop thread. Untagged (`provide`) methods are served by `update()` as before; responses to `Bridge.call()` are unaffected (they are matched after the head). A safe request at the head holds the requests behind it until the next loop pass, as `update_safe()` already did for untagged heads. Both fixes use only Arduino_RPClite's public API (`RpcDecoderManager::getDecoder`, `RPCServer::hasTag`), so RPClite is not changed. |

**Not changed:** Arduino_RPClite, MsgPack, the 256-octet request buffer
(`BRIDGE_RPC_BUFFER_SIZE`), the thread priority (5).

### 4.4 Defence in depth: the RPC mailbox stays

Before the patch existed, Phase C fixed finding C50 inside both sketches (interface decision
I4): every RPC handler became a small **mailbox post**. With the patched library the mailbox is
no longer needed for correctness, but it stays, so a build against the stock library (for
example a developer build whose `sketch.yaml` lost its `- dir:` line, so that arduino-cli takes the
platform's copy) is still safe.

* Flight: [../vgq1-spacecraft/sketch/src/fsw/rpc_mailbox.h](../vgq1-spacecraft/sketch/src/fsw/rpc_mailbox.h).
  A handler that runs on the loop thread (`on_loop_thread()`, the normal case with the patched
  library) executes directly. On any other thread it copies its arguments into one static slot
  (at most `kRpcTextMax` = 220 characters), signals the loop, and waits at most
  `kRpcWaitMs` = 6000 ms (longer than the EGSE's 5 s call timeout and the longest documented loop
  pass, an E22 configure on a dead module of about 4 s). `loop()` takes the request at its top and
  between long phases, runs the real handler on its own stack and completes it. A request the loop
  never started is withdrawn and never executed later. The protocol is host-tested with real
  threads (`HOST_TEST(rpc_mailbox)` in [../tools/host_test/test_integration.cpp](../tools/host_test/test_integration.cpp)).
* Station: the "Bridge services: a mailbox to the loop thread" section of
  [../dssq-groundstation/sketch/sketch.ino](../dssq-groundstation/sketch/sketch.ino): one atomic
  slot (IDLE / POSTED / RUNNING), waits of 1500 ms (below the 2 s call timeout of
  `dssq/radio/sx126x.py`) and 2500 ms for `rcu_mode` (below the 3 s RCU timeout of
  `dssq/display/frontpanel.py`), so a "not done" reply always arrives before the caller gives up.

Other flight rules that keep the Bridge harmless (Phase B specification PB-0.4, review finding 1):
the flight never calls `Bridge.call()` (it blocks without a timeout in 0.4.3); `Monitor` / `Serial`
are not used in a flight build (a `Monitor` write can issue a blocking call, so `LOGF` compiles to
nothing unless `VGQ_DEBUG_MONITOR = 1`); notifications are sent only when the router UART has room
(`availableForWrite()`), so no write waits.

---

## 5 The tools: fetch, verify, patch, check

`tools/radiolib.py` and `tools/routerbridge.py` are thin wrappers. Each describes its library in a
`LibrarySpec` (upstream URL, tag, commit, patch file, bundled files, pristine digest, licence line,
walk bounds) and calls the shared engine [../tools/libbundle.py](../tools/libbundle.py). Python
3.11 or newer, standard library only; `fetch` needs the `git` command line and access to
github.com.

### 5.1 Commands

| Command | What it does |
|---|---|
| `python3 tools/radiolib.py fetch DEST` | `git clone --depth 1 --single-branch --branch 7.8.1` (with `core.autocrlf=false`), check that the tag is commit `2325a38a...`, read only the bundled files (`library.properties`, `license.txt`, `src/`; symbolic links refused), compare their digest with the pinned pristine digest, apply the patch with the exact-hunk applier, write `PROVENANCE.txt`, and move the result into `DEST` in one rename (DEST is never half written). |
| `python3 tools/radiolib.py fetch --unpatched DEST` | the same without the patch: a stock reference copy for comparison builds (the negative control), never for a sketch |
| `python3 tools/radiolib.py fetch --repo URL DEST` | the same from a mirror or a local clone (the tag commit and the digest are still checked) |
| `python3 tools/radiolib.py check DIR` | offline proof that `DIR` is exactly tag 7.8.1 plus the current patch: the file set (nothing missing, nothing extra, no symbolic link), `PROVENANCE.txt` (library, tag, commit, pristine digest, patch digest), reverse-applying the patch gives the pinned pristine digest, and re-applying it gives `DIR` back octet for octet |
| `python3 tools/radiolib.py check --unpatched DIR` | proves `DIR` is the stock tag |
| `tools/routerbridge.py ...` | the same four commands for Arduino_RouterBridge 0.4.3 |

`DEST` must be absent, empty, or a copy the tool wrote earlier (it then contains
`PROVENANCE.txt` and is replaced); any other non-empty folder is refused.

Exit status: 0 success; 1 verification or patch failure (do not use the copy); 2 usage error;
3 git missing or the repository not reachable.

### 5.2 The exact-hunk patch applier

The engine applies a git-format unified diff itself instead of calling `patch` or `git apply`, so
the result does not depend on the host's tools. Every hunk must match at **exactly** the line its
header names: no fuzz, no offset. It refuses file creation, deletion, renames, mode changes, binary
patches, hunks without context, `\ No newline at end of file` markers, a path outside the bundled
files, a file patched twice, and overlapping or out-of-order hunks. A Windows checkout that turned
the whole patch into CRLF is converted back to LF before parsing and hashing, so the patch digest
does not depend on the machine. Text before the first `diff --git` line is commentary: that is
where each patch documents its hunks. `git apply` and `patch -p1` also accept the files.

### 5.3 The pristine digest

The pristine digest is the SHA-256 of the `sha256sum`-style manifest (`<sha256 of file>  <path>\n`
for every bundled file, sorted by path). Anyone can recompute it from a stock copy:

```sh
python3 tools/radiolib.py fetch --unpatched /tmp/stock/RadioLib
cd /tmp/stock/RadioLib && find library.properties license.txt src -type f | LC_ALL=C sort | xargs sha256sum | sha256sum
# 84370304e2fa416a2d4aa798b49a576d6821252691191afdc3ed413a49fa9189  -

python3 tools/routerbridge.py fetch --unpatched /tmp/stock/Arduino_RouterBridge
cd /tmp/stock/Arduino_RouterBridge && find library.properties LICENSE src -type f | LC_ALL=C sort | xargs sha256sum | sha256sum
# c86d601236aef85b351c39c472273cd8bf96c3d38a8a18c708788945b5813194  -
```

(Both values reproduced on 2026-10-12.)

### 5.4 PROVENANCE.txt

Written by `fetch` into every copy and checked by `check`. Example (real output):

```text
# Arduino_RouterBridge bundled with the VGQ-1 / DSS-Q1 Arduino sketches (contract v3 section 41).
# Generated by tools/routerbridge.py - do not edit; re-run 'python3 tools/routerbridge.py fetch <dir>'.
# Verify offline with 'python3 tools/routerbridge.py check <dir>'.
library: Arduino_RouterBridge
upstream: https://github.com/arduino-libraries/Arduino_RouterBridge.git
tag: 0.4.3
commit: d681b87211864148367f2d77096ae84c5badcb2f
pristine-tree-sha256: c86d601236aef85b351c39c472273cd8bf96c3d38a8a18c708788945b5813194
patch: arduino_routerbridge-0.4.3.patch
patch-sha256: 6324a5d39715d4efad41e6c5eeb641542769b3f5bc65289f2436b5aa4ab8fd9c
files: library.properties, LICENSE, src/ (11 files)
license: MPL-2.0 (LICENSE, unchanged; the modified file is src/bridge.h, its source is this copy)
```

---

## 6 How the build uses the bundled copy

Both sketch profiles list the two folders
([../vgq1-spacecraft/sketch/sketch.yaml](../vgq1-spacecraft/sketch/sketch.yaml),
[../dssq-groundstation/sketch/sketch.yaml](../dssq-groundstation/sketch/sketch.yaml)):

```yaml
profiles:
  default:
    fqbn: arduino:zephyr:unoq            # station: arduino:zephyr:ventunoq
    platforms:
      - platform: arduino:zephyr
    libraries:
      - dir: libraries/RadioLib
      - dir: libraries/Arduino_RouterBridge
default_profile: default
```

* A `- dir:` path is relative to the sketch folder. arduino-cli **refuses** a profile whose
  folder is missing, so a build from the repository needs the two `fetch` commands first
  (section 8 and [16_Developer_Guide.md](16_Developer_Guide.md)). The App Lab zips already contain
  the folders.
* A profile library wins over the platform's bundled library of the same name. The proof is the
  verbose compile log, which names the folder of every library used:
  `Using library Arduino_RouterBridge at version 0.4.3 in folder: <sketch>/libraries/Arduino_RouterBridge`
  (first shown by the real builds of commit `5b06a04`, with no warning in our code; build sizes:
  [08_Verification_and_Test.md](08_Verification_and_Test.md)). CI fails when that line names any
  other folder (section 8).
* `Wire`, `SPI` and `Arduino_LED_Matrix` ship with the arduino:zephyr core; Arduino_RPClite and
  the other RouterBridge dependencies come from the platform's library dependencies.

---

## 7 Verification

| Check | What it proves | Where |
|---|---|---|
| `fetch` | the tag is the pinned commit; the bundled files are exactly the pinned pristine tree; the patch applies at exactly its lines | [../tools/libbundle.py](../tools/libbundle.py), every CI run, every packaging run |
| `check` | a copy on disk is exactly pristine + current patch (file set, every octet, provenance) | `check_firmware.sh`, packaging, by hand |
| `check_firmware.sh`: bundled copies | a copy in a sketch's `libraries/` that does not pass `check` is a **FAIL** ("stale bundled copy: run ... fetch") | [../tools/host_test/check_firmware.sh](../tools/host_test/check_firmware.sh) |
| `check_firmware.sh`: library units | every RadioLib translation unit compiles against strict Arduino stand-ins for the UNO Q **and** the VENTUNO Q (80 of 80 each, `-Wall -Wextra`) | same |
| `check_firmware.sh`: negative control | the **stock** 7.8.1 `ArduinoHal.cpp` must FAIL for the VENTUNO Q with the `PinMode` / `PinStatus` errors; if it ever compiles, the stand-ins are no longer strict enough to see what hunk 1 fixes, and the check fails | same |
| `check_firmware.sh`: drivers | flight `drivers/sx126x.cpp` with `VGQ_RADIO=3` for the SX1268 and the SX1262; the flight build matrix with `VGQ_RADIO=3`; the station sketch with `DSSQ_RADIO_SX126X=1` for both chips | same |
| `check_firmware.sh`: RouterBridge | each bundled copy passes `routerbridge.py check`; without a bundled copy a fresh fetch proves the tag and patch still apply (skipped without network) | same |
| RouterBridge hunk 2 | the bridge thread never runs a `provide_safe` method, `update_safe()` runs each exactly once (12 checks, also under ASan + UBSan); the stock tag fails exactly 4 of them (negative control); only the audited RPClite 0.3.1 / MsgPack 0.4.2 are accepted | [../tools/host_test/routerbridge/run.sh](../tools/host_test/routerbridge/run.sh) `<verbose compile log>` |
| RouterBridge hunk 1 | the bridge thread's worst-case stack, recomputed from a real build of each sketch, fits `UPDATE_THREAD_STACK_SIZE` | [../tools/host_test/routerbridge/stackcalc.py](../tools/host_test/routerbridge/stackcalc.py) |
| Station sketch with the bundled RadioLib | the real station sketch runs on host threads (E22 build and SX126x build for both chips) | [../tools/host_test/station/run.sh](../tools/host_test/station/run.sh) (refuses a RadioLib copy that `radiolib.py check` rejects) |
| Real builds | arduino-cli with the arduino:zephyr platform, as App Lab builds: the compile logs name the bundled RouterBridge (default builds) and the bundled RadioLib (SX126x builds); no warning in our code | CI `arduino-build`; commits `f0ff5e8`, `fbf16cf`, `5b06a04` |
| Version guards | `#error` unless RadioLib is 7.8.1 with `RADIOLIB_STATIC_ONLY` | section 3.5 |
| Mailbox | the defence-in-depth protocol works with real threads | `HOST_TEST(rpc_mailbox)`; the station harness (mailbox checks under ThreadSanitizer in CI) |

First recorded in the commit messages: real builds naming `<sketch>/libraries/Arduino_RouterBridge`
(commit `5b06a04`); 80 of 80 RadioLib units on both boards and the negative control (commit
`f0ff5e8`). The current results of every check, and the test layers, are in
[08_Verification_and_Test.md](08_Verification_and_Test.md).

---

## 8 Continuous integration

Two jobs of [../.github/workflows/ci.yml](../.github/workflows/ci.yml) use the bundled copies, on
every push and pull request.

**`host-tests` job** - fetches both libraries into both sketches (`radiolib.py fetch`,
`routerbridge.py fetch`), then runs `check_firmware.sh` (bundled copies, all RadioLib units on
both boards, the negative control, every SX126x build switch) and the station sketch harness
under AddressSanitizer + UBSan and under ThreadSanitizer.

**`arduino-build` job:**

1. For each sketch whose `sketch.yaml` names `dir: libraries/RadioLib` or
   `dir: libraries/Arduino_RouterBridge`: `python3 tools/radiolib.py fetch` /
   `python3 tools/routerbridge.py fetch` into `<sketch>/libraries/<name>`.
2. Install the "Arduino Q Boards" platform (`arduino:zephyr`).
3. Four verbose real builds with `--profile default --warnings all`: the station and the flight
   sketch with their default radio, and both with the SX126x option (SX1268) through
   `--build-property "compiler.cpp.extra_flags=..."`. A compiler warning in this repository's
   code fails the job (the libraries' own warnings are not ours).
4. Fail unless the logs contain
   `Using library Arduino_RouterBridge at version 0.4.3 in folder: $PWD/<sketch>/libraries/Arduino_RouterBridge`
   (default builds) and
   `Using library RadioLib at version 7.8.1 in folder: $PWD/<sketch>/libraries/RadioLib`
   (SX126x builds).
5. Run the RouterBridge host test against the platform's Arduino_RPClite / MsgPack named by the
   flight and the station build logs (the second run under ASan + UBSan), with the stock-tag
   negative control.
6. Rebuild both sketches with `-fstack-usage -fcallgraph-info=su` and run `stackcalc.py --check`
   on each bundled `bridge.h`.

A tag that moved, a changed upstream file, a patch that no longer applies, a build that picked the
platform's copy, a platform update that changes RPClite / MsgPack, or a code change that makes the
bridge thread's stack too small therefore stops CI. All CI jobs are listed in
[16_Developer_Guide.md](16_Developer_Guide.md).

---

## 9 Packaging (App Lab zips)

The release zips (`dist/vgq1-spacecraft-unoq.zip`, `dist/dssq-groundstation-ventunoq.zip`)
contain each App folder with `sketch/libraries/RadioLib` and
`sketch/libraries/Arduino_RouterBridge` already in place, each with its `PROVENANCE.txt`, so App
Lab builds the sketch offline. The copies are produced by the same `fetch` (or verified with the
same `check`) as in CI, never copied from an unverified folder. The packaging command and its
options are in [16_Developer_Guide.md](16_Developer_Guide.md) section 9.

---

## 10 Moving to a newer upstream release

Do this when an upstream release fixes something we need, or when the arduino:zephyr platform
moves to a newer Arduino_RouterBridge. RadioLib is used as the example; RouterBridge is the same
with its own files.

1. **Read the release.** Read the upstream change log and the diff between the pinned tag and the
   new tag for every file the patch touches (`src/BuildOpt.h`, `src/BuildOptUser.h`; for
   RouterBridge `src/bridge.h`) and for every behaviour listed in section 3.4 or 4.
   Check: you can say for each hunk whether the new release still needs it.
2. **Get a stock copy of the new tag**, e.g. `git clone --depth 1 --branch <new tag> <upstream URL>
   /tmp/new`. Note the tag commit: `git -C /tmp/new rev-parse HEAD^{commit}`.
   Check: the commit matches the tag on the upstream web page.
3. **Compute the new pristine digest** with the formula of section 5.3 (bundled files only:
   `library.properties`, `license.txt`, `src` for RadioLib; `library.properties`, `LICENSE`, `src`
   for RouterBridge).
4. **Regenerate the patch.** In `/tmp/new` (a git clone, so it is already a repository) re-apply
   each still-needed change by hand, keeping the `// VGQ patch:` comments, then write the diff
   with `git -C /tmp/new diff -- src > /tmp/body.patch` (paths `a/src/...` / `b/src/...`, as the
   tool expects). Create `tools/radiolib-<new>.patch` = the documentation header (new tag, commit,
   line numbers, evidence, "Not patched" list) followed by that diff.
   Check: on a fresh stock copy `git apply --check tools/radiolib-<new>.patch` succeeds.
5. **Update the pins** in the wrapper (`tools/radiolib.py` `SPEC`): `tag`, `commit`, `patch_file`,
   `pristine_tree_sha256`, and the file-count comments (`max_files` must still bound the walk).
6. **Update every place that names the version:** the `RADIOLIB_VERSION_*` `#error` lines
   (section 3.5); the CI log checks (`Using library RadioLib at version 7.8.1`,
   `Using library Arduino_RouterBridge at version 0.4.3`) and the same string in
   `tools/package_apps.py --build`; for RouterBridge the expected `UPDATE_THREAD_STACK_SIZE` and the
   negative-control texts in `tools/host_test/routerbridge/run.sh` and `stackcalc.py`; the comments
   in `check_firmware.sh`, `station/run.sh`, both `sketch.yaml` files and `.gitignore`; this
   document, [10_References.md](10_References.md) and the decision log. If the platform moves to
   other Arduino_RPClite / MsgPack releases, `routerbridge/run.sh` refuses them until the patch is
   re-audited and its pins are updated.
7. **Re-audit** the driver workarounds of section 3.4 (or the mailbox assumptions of section 4.4)
   against the new source: drop a workaround only when the new release is proven to fix it.
8. **Fetch and check:** `python3 tools/radiolib.py fetch /tmp/try/RadioLib` then
   `python3 tools/radiolib.py check /tmp/try/RadioLib`. Both must print `ok`.
9. **Replace the bundled copies** in both sketches: `rm -rf <sketch>/libraries/RadioLib` and fetch
   again (the tool refuses to overwrite a folder it did not write).
10. **Run every check** of [16_Developer_Guide.md](16_Developer_Guide.md) section 6: host tests
    (strict and sanitizers), `check_firmware.sh` (all RadioLib units on both boards, the negative
    control - update its expected errors if the new release fixes the VENTUNO Q entry), the station
    harness (ASan + UBSan and TSan), the RouterBridge host test and `stackcalc.py` on both sketches,
    the ground pytest suite, and the real builds of both sketches for every radio option (E22,
    SX1268, SX1262) on the current platform.
11. **Hardware.** Repeat the SX126x bring-up tests of [08_Verification_and_Test.md](08_Verification_and_Test.md)
    (configuration read-back, Rx boosted gain, CAD, packet status, hibernation wake) - a library
    update changes code that no host test executes.
12. **Commit** the patch, the wrapper, the guards and the documents together; the commit message
    names the new tag, commit, digest and the check results. Build new App Lab zips.

---

## 11 Licences

* RadioLib is MIT licensed. `license.txt` is bundled unchanged; the patch only adds build options
  and a platform entry.
* Arduino_RouterBridge is MPL-2.0. `LICENSE` is bundled unchanged. The modified file is
  `src/bridge.h`; its complete modified source is the bundled copy itself (it is a header), and the
  change is published as `tools/arduino_routerbridge-0.4.3.patch` in this repository.
* Arduino_RPClite, MsgPack and the other dependencies are not bundled or modified: they come from
  the arduino:zephyr platform.

## 12 Related documents

[12_Design_Decisions.md](12_Design_Decisions.md) (D-40 to D-42) -
[16_Developer_Guide.md](16_Developer_Guide.md) (build, checks, release) -
[04_Space_Data_Link_ICD.md](04_Space_Data_Link_ICD.md) (RPC contract) -
[08_Verification_and_Test.md](08_Verification_and_Test.md) -
[10_References.md](10_References.md) -
[20_Known_Limitations.md](20_Known_Limitations.md).
