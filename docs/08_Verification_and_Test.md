# 08 - Verification and Test

This document says how the VGQ-1 spacecraft and the DSS-Q1 ground station are verified:
first the automated checks that run on any PC and on GitHub, then the hardware tests you
run on your own boards.

> **Status (Phase D tree, 2026-10-12).** Every automated layer below passes. **No hardware
> test in sections 6-12 has been run yet: the system has not been tested on real hardware.**
> Each hardware test turns a "VERIFY" item of the code into a measured fact. Record every
> result in [21_Test_Records.md](21_Test_Records.md) and close the matching entry in
> [20_Known_Limitations.md](20_Known_Limitations.md).

Contents: 1 Verification at a glance - 2 What each automated layer proves - 3 Running every
check on your PC - 4 Continuous integration - 5 Before any hardware test - 6 Power, wiring and
platform (T-HW) - 7 Ground-station platform (T-GS) - 8 Radio frequency (T-RF) - 9 Telemetry and
telecommand (T-TM, T-TC) - 10 Instruments (T-SC) - 11 Fault protection (T-FD) - 12 Field
(T-FLD) - 13 Coverage of every VERIFY item - 14 One-off verifications on record.

---

## 1 Verification at a glance

| Layer | What it is | Size and result on the Phase D tree | Where it runs |
|---|---|---|---|
| 1 Host tests | The flight C++ (protocols, algorithms, radio tables) compiled for a PC and checked | **964 checks** in 89 groups, 0 failures (963 when the official CCSDS 121.0 test data is absent). Built twice: warnings as errors, and under AddressSanitizer + UBSan | PC, CI |
| 2 Ground tests | The ground data system and the spacecraft EGSE under pytest | **750 tests: 747 passed, 3 skipped** (optional reference files not on the bench), on Python 3.13 and 3.11 | PC, CI |
| 3 Firmware type-check | `check_firmware.sh`: every Arduino file compiled against strict stand-ins of the board API, in every build option | **65 ok, 0 fail, 0 warn**, incl. 80 of 80 RadioLib units for each board and a negative control | PC, CI |
| 4 Station sketch harness | The real VENTUNO Q station sketch built for a PC and run on real threads | **26 checks**, 0 failures (12 E22 + 7 SX1268 + 7 SX1262) | PC, CI (ASan + UBSan, TSan) |
| 5 RouterBridge harness and stack bound | The bundled patched Arduino_RouterBridge against its real dependencies; the bridge thread's worst-case stack from the real build | **12 checks** pass (the stock library must fail 4: negative control); worst case **1084 of 1536 octets** (452 margin) for both sketches | PC, CI |
| 6 Real Arduino builds | The exact compiler, board package and settings App Lab uses | 9 sketch builds (flight E22 and SX1268 on core main, 1.0.0 and 0.90.0; station E22 on main and 1.0.0, SX1268 on main), **0 warnings in our code** (sizes in 2.6); the loader symbol check passes for every flight build | PC, CI |
| 7 Lint | ruff and mypy over the Python code, shellcheck over the shell scripts, text-file hygiene | clean (CI job `lint`) | PC, CI |
| 8 Simulator acceptance | The ground station driven by the software spacecraft, the web UI in a headless browser | All 11 tabs, 0 page, console or HTTP errors (E22 and SX126x modes) | PC |
| 9 Adversarial review | Phase C: 18 read-only reviewers, each finding challenged by 1-2 skeptics | 62 confirmed findings fixed (3 critical, 25 major, 34 minor), regression test wherever feasible | once |
| 10 Hardware tests | Sections 6-12 of this document | **0 run so far** | your bench and field |

Sources of the numbers: a run of each layer on the Phase D working tree (host test log,
`pytest -q`, `check_firmware.sh`, `tools/host_test/station/run.sh`,
`tools/host_test/routerbridge/run.sh` and `stackcalc.py`, `arduino-cli` logs) and the commit
messages listed in [CHANGELOG.md](../CHANGELOG.md). The counts grow when tests are added;
the CI log of the latest commit is the authority.

What none of the automated layers can prove: real radio range, the wiring, an I2C bus on a
long cable, the RF environment, the true output power, the boards' power supplies, heat. The
hardware tests exist for exactly these.

---

## 2 What each automated layer proves

### 2.1 Host tests (`tools/host_test`)

The flight software's pure C++ (everything in `vgq1-spacecraft/sketch/src/ccsds`, `src/fsw`
except `flight.cpp` and `status_display.cpp`, and `src/drivers/radio.cpp`) is compiled with the
PC compiler together with the test files ([Makefile](../tools/host_test/Makefile)). Each check
prints one `ok:` line; any `FAIL` stops CI. The run also writes the **cross-check vectors**
(CADU streams, packets, CLTUs, SDLS frames, spectra, sequences) that the ground tests decode.

| File | Groups | Checks | What is checked (standards in brackets) |
|---|---|---|---|
| [host_test.cpp](../tools/host_test/host_test.cpp) | core | 52 | CRC-16 check value 0x29B1; randomizer prefix FF 48 0E C0 9A (131.0-B); Reed-Solomon dual-basis tables (Annex F) and all-zero codeword; BCH(63,56) corrects every single-bit error; TC frame through CLTU; FARM-1 accept / duplicate / gap / lockout / Unlock / Set V(R) and the CLCW (232.1-B); VC multiplexer CADU stream; SSR allocation, overwrite, playback; TRIAD attitude; SHA-256 (FIPS 180-4) and HMAC-SHA-256 (RFC 4231 cases 1-2); SDLS accept, replay and forgery rejection (355.0-B); FP32 Welford statistics within 1 nT of an FP64 reference |
| [test_autonomy.cpp](../tools/host_test/test_autonomy.cpp) | 15 | 225 | Stored sequences (load validation, ordering, late skip, START/STOP epochs, FDIR stop, u32 clock wrap, 200 random sequences against a 64-bit model); beacon levels and latch; MAGCAL parsing and maths; RM3100 self-test decode; reset-cause classification; hardware-watchdog (IWDG) prescaler / reload; the FDIR flags `RSTCNT` clears |
| [test_cfdp.cpp](../tools/host_test/test_cfdp.cpp) | 8 | 107 | CFDP class-1 PDU header, modular checksum, file names, Metadata / File Data / EOF, whole transfers, cancel (condition 15), relay monitor, truncated inputs (727.0-B-5) |
| [test_crypto.cpp](../tools/host_test/test_crypto.cpp) | 7 | 60 | AES-256 (FIPS-197 C.3), GCM test vectors (SP 800-38D, McGrew-Viega cases 13-16) and random vectors, SDLS key derivation, AUTH compatibility, TC AE, TM protection incl. the null-SA fallback |
| [test_integration.cpp](../tools/host_test/test_integration.cpp) | 16 | 177 | APID map; v3 packet layouts and lengths; telemetry vectors; TC header and command table; SSR export; downlink scheduler and load plan; the TC accept chain; ground-built TC round trip; CFDP relay and EVR gates; EGSE status texts (worst case 307 / 444 characters, under the 480 limit); integration streams for the ground; the RPC mailbox on real threads; packet length guards |
| [test_radio.cpp](../tools/host_test/test_radio.cpp) | 14 | 115 | E22 rate / power tables, air time, register images, stream parser, TX tracker; SX126x profiles 0-7, air time, duty-cycled RX periods, frequency-offset limits; brown-out derating; the A0 strap mapping and its pin |
| [test_rice.cpp](../tools/host_test/test_rice.cpp) | 5 | 23 | CCSDS 121.0-B-3 encoder: official CCSDS test vectors (SHA-256-pinned subset embedded; the **full official suite, 56 files, when `CCSDS121_TESTDATA` is set**), hand-derived cases, argument and worst-case bounds, corpus round trip |
| [test_spectral.cpp](../tools/host_test/test_spectral.cpp) | 11 | 130 | AS7265X exposure table, saturation and AGC, HDR merge, lock-in, log encoding, light-source classifier, change detection, boresight, **LED current limits**, the virtual-register protocol, SPEC/SPECBURST packing |
| [test_station_id.cpp](../tools/host_test/test_station_id.cpp) | 2 | 12 | The ground's plain-text station identification through the flight receive chain (E22 stream parser, `tc_accept()`): no CLTU, BCH / SDLS / FARM-1 state untouched, CLTUs before or after it in one burst still accepted |
| [test_tx_duty.cpp](../tools/host_test/test_tx_duty.cpp) | 4 | 17 | RF `tx_duty` over whole transmission cycles (`TxDutyMeter`): never above the 50 % PA limit for any model, rate code and mode, also across a radio reconfiguration (with a no-hold negative control); SX126x SF12 without the window artefact of the replaced 60 s window (kept as a negative control); open cycle, silence, window, wrap |
| [test_uslp.cpp](../tools/host_test/test_uslp.cpp) | 6 | 46 | USLP header fields, TM frames, security roll-back, CADU streams, USLP TC frames incl. truncated VCF count (732.1-B-2) |
| **Total** | **89** | **964** | |

The same binary is built **twice**: once with `-Wall -Wextra -Wshadow -Werror` (no warning
allowed), once with `-fsanitize=address,undefined -fno-sanitize-recover=undefined` (any memory
error or undefined behaviour stops the run). The flight sketch itself (`flight.cpp` with the
Arduino core) is not run on the PC; layers 3 and 5 cover it.

### 2.2 Ground tests (`tests/`, pytest)

The ground code runs on the Python standard library only; the tests add `pytest` and three
optional independent references (`reedsolo`, `cryptography`, `xmlschema`) listed in
[requirements-dev.txt](../requirements-dev.txt). A test that needs a missing optional package
skips cleanly.

| File | Tests | What is checked |
|---|---|---|
| test_ccsds.py | 14 | CRC, randomizer, RS against the independent `reedsolo` codec, frame and packet extraction, CLTU + FARM, SCLK fit |
| test_cfdp.py | 121 | CFDP receiver (complete, incomplete, checksum failure, cancelled, abandoned, reordering, duplicates) and the **spacecraft EGSE** (`vgq1-spacecraft/python/main.py`): epoch rules, counter files, relay, CADU archive, nonce-reuse interleavings, the App Lab entry point with and without `arduino.app_utils.AppError` |
| test_cross_check.py | 24 | **Flight-made vectors decoded bit-exactly by the ground**: CADUs, RS, CLTUs, packets, SDLS AUTH/AE frames, USLP, CFDP, Rice, spectra; the offline replay with `--app-data` finds the station's `station.toml` and key as the GDS does |
| test_end_to_end.py | 49 | The whole GDS with the software spacecraft on a virtual clock: telemetry flow, COP-1 command round trip, hazardous confirmation, SDLS uplink, the TC counter hold and its release on the time floor without an SN file (every numbered TC service, also with `--no-archive`; a replayed first-pass HK after an archive loss, a forged HK under TM NONE and the boot HK before the EGSE restore cannot cause reuse; the simulator never touches the real counter; no key means no hold), coordinated rate change, RF survey, dashboard and `/metrics` |
| test_fop.py | 9 | FOP-1: lockout, suspension, N(R) validation, BD/BC confirmation (232.1-B-2) |
| test_logger.py | 17 | Receiver data logger: 1 Hz `rx_stats`, CSV export, retention, free-space guard |
| test_magcal.py | 22 | Ellipsoid fit, coverage and conditioning gates, MAGCAL encoding |
| test_packaging.py | 71 | `tools/package_apps.py`: zip layout and order, reproducibility, the bundled libraries, refusal of key files, key material and `requirements.txt` (some skip outside a git checkout or without the library copies) |
| test_packets.py | 6 | Packet layouts and decommutation |
| test_products.py | 62 | CCSDS TDM writer and reader, XTCE export (checked against SpaceSystem 1.2 when the schema is present) |
| test_radio.py | 76 | Radio models, sensitivity sources, LoRa maths, AFC loop, E22 serial port on pseudo-terminals, SX126x bridge backend; the ground RF survey returns to the operational channel and never retunes under the window's uplinks, and after it the TX gate radiates nothing off the station channel (also across a wall-clock step back); the closing identification waits at most 0.6 s for the station MCU or reports that it was not sent |
| test_rice.py | 34 | CCSDS 121.0 decoder and reference encoder; **all 74 official streams when `CCSDS121_TESTDATA` is set** |
| test_sdls.py | 53 | AES-256 (FIPS-197), GCM (SP 800-38D cases 13-16), tamper and length checks, crash-safe SN file, re-synchronisation on the time floor (never below the file, safe against a replayed or forged HK; the 1 000 000 fallback before 2026 or for a report above the floor; refused above 4 227 858 432), the hardline tool's refusal without an SN file and above the time floor, TM replay window, null SA |
| test_sequence.py | 65 | Sequence-file parser and SEQLOAD packing, incl. 3 cross-checks that run the host test |
| test_spectral.py | 41 | Spectral analytics, colorimetry, indices, log decoding, LED limits |
| test_station_id.py | 28 | The ground's amateur station identification: callsign validation, timing rules (first uplink, at least every 10 min also with frames lost, end of session, LOS, shutdown with an identification left in the outbox), E22 and SX126x radio paths with the software spacecraft; the uplink window plan never overruns the quiet window (every rate code and mode, E22-400T33D / T37S / SX126x, also for frames handled late or in a batch) and its predicted CLTU lengths equal the built CLTUs; WAKE and the LOS identification wait for a window's transmissions, also after a wall-clock step back; windows at most once per 1.4 s of frame ERT and a WAKE at most once per 10 s keep the SDLS TC numbers below 3 per second whatever the downlink does |
| test_tc.py | 5 | TC frames and CLTUs |
| test_uslp.py | 16 | USLP frames, flight/ground bit-exact |
| test_web.py | 26 | Web server security (token, Host/Origin checks, limits, CSP), App Lab entry point, a headless render of every tab with injected markup |
| **Total** | **739** | 736 passed, 3 skipped (Orekit TDM examples, the XTCE schema file, the full official Rice data: not on the bench) |

Not in the table: the two documentation tests. `tests/test_docs.py` (**7 tests**) builds real
snapshots and checks that every top-level snapshot key, every receiver `rx.rf` key, the logger
keys, the key-by-key blocks (e.g. `link.station_id`) and every HK and RF field appear in
[13_Web_UI_Reference.md](13_Web_UI_Reference.md), and that every snapshot path written there
exists. `tests/test_citations.py` (**4 tests**) checks that every citation tag in the code
("contract N", "PB-x.y", "C-nn", "F-nn", "I1"-"I4") has a row in
[12_Design_Decisions.md](12_Design_Decisions.md#appendix-b---citation-tags-in-code) appendix B
and every row is still cited. Together: the **750 tests, 747 passed, 3 skipped** of section 1
(measured on Python 3.13 and 3.11 on the Phase D tree; the CI log of the final commit is the
authority).

### 2.3 Firmware type-check (`tools/host_test/check_firmware.sh`)

Compiles every translation unit of both sketches with the PC compiler against
`tools/host_test/arduino_stub` (the Arduino API exactly as `arduino:zephyr` declares it), with
the board macros of the real build, `-Wall -Wextra -Wshadow`. Last run: **64 ok, 0 fail, 0 warn**.

* Both bundled libraries are verified first: RadioLib 7.8.1 + `tools/radiolib-7.8.1.patch` and
  Arduino_RouterBridge 0.4.3 + `tools/arduino_routerbridge-0.4.3.patch` (tag commit and file
  hashes, see [22_Bundled_Libraries.md](22_Bundled_Libraries.md)). A stale copy is a failure.
* Every flight file in the default build, then the **flight build matrix**: E22-400T37S build,
  SX126x (SX1268 and SX1262), USLP downlink, SDLS TC AUTH, SDLS off, a keyed build (a temporary
  key, never the tree), keyed + USLP + AUTH, the debug monitor.
* All **80 RadioLib translation units, for the UNO Q and for the VENTUNO Q** (80 of 80 clean).
* The station sketch in the E22 build and the SX126x build (both chips).
* **Negative control:** the stock RadioLib 7.8.1 must FAIL to compile for the VENTUNO Q with the
  core's `PinMode` / `PinStatus` errors - the proof that the stub is strict enough to see the
  defect the patch fixes.

It does not replace the real build: `.ino` files are compiled as plain C++, without
arduino-cli's prototype generation.

### 2.4 Station sketch harness (`tools/host_test/station/run.sh`)

Builds the **real** station sketch for the PC (stub core, a recording Bridge, a fake SX126x on
the SPI) and runs it on real threads: CardKB key-test mode, the RPC mailbox (a handler called
from another thread runs on the loop thread; an unserved request times out and never runs),
the `rf_rx` length bound, TX-fault handling, the `S|` status line fields. **26 checks**, 0
failures; CI also runs it under AddressSanitizer + UBSan and under ThreadSanitizer.

### 2.5 RouterBridge harness and stack bound (`tools/host_test/routerbridge`)

* `run.sh <verbose compile log>` compiles the **real** `bridge.h` of the bundled copy with the
  platform's own Arduino_RPClite 0.3.1, MsgPack 0.4.2, DebugLog, ArxContainer and ArxTypeTraits
  (located through the compile log; only Zephyr and the serial port are stubbed) and checks that
  the library's bridge thread never executes a `provide_safe` handler and that the loop hook runs
  each exactly once: **12 checks**. Negative control: the stock 0.4.3 tag must fail exactly the
  **4 checks** the patch fixes (needs github.com; otherwise reported as skipped).
* `stackcalc.py` recomputes the bridge thread's worst-case stack from a real build compiled with
  `-fstack-usage -fcallgraph-info=su` (every sketch, library and core call graph, plus the
  core image's kernel functions sized from their disassembly): **chain 912 + thread entry 8 + FP
  exception frame 108 + TLS 56 = 1084 octets**, which must fit the patched 1536-octet stack
  (margin 452 octets) - result identical for the flight and the station sketch.

### 2.6 Real builds (arduino-cli + arduino:zephyr)

The sketches are compiled exactly as Arduino App Lab compiles them: the sketch's default
profile, arduino-cli 1.5.2 (the version App Lab embeds), the `arduino:zephyr` platform built
from source (core main `b08a650` and release 1.0.0 with Zephyr SDK 1.0.1, `arm-zephyr-eabi-g++`
14.3; release 0.90.0 with the SDK 0.16.8 it depends on, GCC 12.2), FQBN
`arduino:zephyr:<board>:wait_linux_boot=app`, `--warnings all`. 0.90.0 is the last release
before 1.0.0 and what an App Lab that was not updated still builds with; it has no VENTUNO Q.
Flight results after the fix for those older cores (`src/libc_compat.cpp`,
`RpcMailbox::current_thread()`); the station sketch did not change. Percentages are computed here from the sizes and the maxima arduino-cli
prints (786 432 B flash on the UNO Q, 1 048 576 B on the VENTUNO Q, 262 144 B RAM on both):

| Build | Flash (program storage) | Static RAM (global variables) | Warnings in our code |
|---|---|---|---|
| UNO Q flight, default E22 (T33D / T37S by strap), core main | 233 316 B (29.7 %) | 128 484 B (49.0 %) | 0 |
| UNO Q flight, default E22, core 1.0.0 | 232 860 B | 128 396 B | 0 |
| UNO Q flight, default E22, core 0.90.0 | 222 148 B (28.2 %) | 120 913 B (46.1 %) | 0 |
| UNO Q flight, SX1268 (`VGQ_RADIO=3`), core main | 326 388 B (41.5 %) | 169 576 B (64.7 %) | 0 |
| UNO Q flight, SX1268, core 1.0.0 | 325 944 B | 169 488 B | 0 |
| UNO Q flight, SX1268, core 0.90.0 | 316 448 B (40.2 %) | 163 030 B (62.2 %) | 0 |
| VENTUNO Q station, default E22, core main | 100 828 B (9.6 %) | 40 716 B (15.5 %) | 0 |
| VENTUNO Q station, default E22, core 1.0.0 | 100 324 B | 40 632 B | 0 |
| VENTUNO Q station, SX1268 (`DSSQ_RADIO_SX126X=1`), core main | 207 692 B (19.8 %) | 89 216 B (34.0 %) | 0 |

The only warnings in these logs are in the platform's own code: 6 per build
(`-Wmissing-field-initializers` in `Wire.cpp`) on main and 1.0.0, 13 on 0.90.0
(`wiring_analog.cpp`, `Wire.cpp`, `zephyrSerial.cpp` and a Zephyr USB header). The resource budgets built on these numbers are in
[01_System_Design.md](01_System_Design.md). Every log names the bundled copies: *"Using library Arduino_RouterBridge at
version 0.4.3 in folder: `<sketch>`/libraries/Arduino_RouterBridge"* (and RadioLib 7.8.1 in the
SX126x builds). The flight's recorder (SSR) is allocated from the heap at boot and is not part
of the static RAM figure; its size on the board is a hardware check (T-HW-11).

**Loader symbol check.** A sketch is a Zephyr loadable extension (LLEXT): the board's loader
resolves its undefined symbols when it loads it and refuses the whole sketch if one is not in
its export table, weak references included. The core's check link catches most of these at
build time, not all: before the fix, the flight sketch built on 0.90.0 still referenced
`_ZTH13z_tls_current` (the C++ thread-local init function behind `k_current_get()`; 1.0.0
compiles with `-fno-extern-tls-init`), which links but does not load. `tools/llext_check.py`
compares every undefined symbol of the final ELF with the variant's `syms-dynamic.ld` and with
a baseline sketch of the same core (setup/loop, `pinMode`, `digitalWrite`): **0 unresolved**
for the flight sketch, both radio options, on main, 1.0.0 and 0.90.0 (the pre-fix 0.90.0 build
fails it with exactly that symbol: negative control). CI runs it for both sketches.

### 2.7 Lint

`ruff check .` (rules E, F, W, B, UP, SIM, C4, RUF; line length 120; Python 3.11 target) and
`mypy` (station, EGSE and tool code; settings in [pyproject.toml](../pyproject.toml)),
`shellcheck` of the four shell scripts (`check_firmware.sh`, the two harness `run.sh`,
`host/install_station.sh`), plus a check that every text file has LF line ends, a final newline
and no trailing white space.

### 2.8 Simulator acceptance

`python -m dssq.gds --sim` runs the complete ground station against a software spacecraft
(packets, frames, RS, SDLS, COP-1, CFDP, AFC in SX126x mode). The Phase B acceptance run drove
it through the web API and loaded the dashboard in headless Chromium: all 11 tabs with zero
page, console or HTTP errors, in E22 and SX126x modes (commit 6d4028e). Measured load of the
simulator plus the full GDS for 60 s: 0.81 s CPU (1.3 % of one core) and 29 MB resident memory
on the development PC, an upper bound because it includes the simulator's own encoding.

---

## 3 Running every check on your PC

From the repository root, on Linux (or WSL) with `g++`, `python3` (3.11 or newer) and `git`:

```bash
# 0. Once: the PC test tools (never needed on the boards)
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt

# 1. Host tests - strict build, then run (the folder receives the cross-check vectors)
make -C tools/host_test host_test CXXFLAGS="-std=c++17 -O2 -Wall -Wextra -Wshadow \
  -Wno-unused-parameter -Werror -DVGQ_HOST_BUILD -I../../vgq1-spacecraft/sketch/src"
mkdir -p /tmp/vectors && tools/host_test/host_test /tmp/vectors     # ends "HOST TEST PASSED (0 failures)"

# 1b. The same under AddressSanitizer + UBSan
make -C tools/host_test clean
make -C tools/host_test host_test CXXFLAGS="-std=c++17 -O1 -g -fno-omit-frame-pointer \
  -fsanitize=address,undefined -fno-sanitize-recover=undefined -Wall -Wextra \
  -Wno-unused-parameter -DVGQ_HOST_BUILD -I../../vgq1-spacecraft/sketch/src"
mkdir -p /tmp/vectors-san && tools/host_test/host_test /tmp/vectors-san

# 1c. Optional: the full official CCSDS 121.0 suite (libaec's 121B2TestData folder)
CCSDS121_TESTDATA=/path/to/libaec/data/121B2TestData tools/host_test/host_test /tmp/vectors

# 2. Ground tests (both App folders are put on the import path by tests/conftest.py)
.venv/bin/python -m pytest -q                                      # "747 passed, 3 skipped"

# 3. Firmware type-check (uses the bundled library copies, else fetches them from github.com)
bash tools/host_test/check_firmware.sh                             # every line "ok", exit 0

# 4. Station sketch harness (CI adds SAN="-fsanitize=address,undefined ..." and SAN=-fsanitize=thread)
bash tools/host_test/station/run.sh                                # "26 checks passed, 0 failed"

# 5. Real build, as App Lab (arduino-cli with the "Arduino Q Boards" platform arduino:zephyr)
for app in vgq1-spacecraft dssq-groundstation; do
  python3 tools/radiolib.py fetch "$app/sketch/libraries/RadioLib"
  python3 tools/routerbridge.py fetch "$app/sketch/libraries/Arduino_RouterBridge"
  arduino-cli compile --profile default --warnings all -v "$app/sketch" > "/tmp/$app.log" 2>&1
  grep -E "^(Sketch uses|Global variables|Using library)" "/tmp/$app.log"
done

# 6. RouterBridge harness (needs a verbose compile log from step 5)
bash tools/host_test/routerbridge/run.sh /tmp/vgq1-spacecraft.log  # ends with the negative control "ok"

# 7. Lint (settings in pyproject.toml)
.venv/bin/ruff check . && .venv/bin/mypy && .venv/bin/mypy dssq-groundstation/python/main.py
shellcheck tools/host_test/check_firmware.sh tools/host_test/station/run.sh \
  tools/host_test/routerbridge/run.sh dssq-groundstation/host/install_station.sh

# 8. The station with the software spacecraft; open http://127.0.0.1:8080
cd dssq-groundstation/python && python3 -m dssq.gds --sim
```

The bridge-thread stack bound (2.5) needs a build with
`--build-property "compiler.cpp.extra_flags=-fstack-usage -fcallgraph-info=su"`; the exact
commands are the last step of the CI workflow.

After changing telemetry or commands, regenerate the dictionary document with
`python3 tools/gen_dictionary_doc.py` (it rewrites
[05_Telemetry_and_Command_Dictionary.md](05_Telemetry_and_Command_Dictionary.md)). The
developer workflow around these commands is in [16_Developer_Guide.md](16_Developer_Guide.md).

---

## 4 Continuous integration

One GitHub Actions workflow, [.github/workflows/ci.yml](../.github/workflows/ci.yml), runs on
every push, every pull request and on demand. Every action is pinned to a full commit hash.

| Job | What it runs | Fails when |
|---|---|---|
| `lint` | ruff over every `.py`, mypy over the station, EGSE and tool code (tool versions pinned in `requirements-dev.txt`), shellcheck of the shell scripts, and a text-file check: LF line ends, final newline, no trailing white space | any finding |
| `host-tests` | Fetches the official CCSDS 121.0 test data (libaec, pinned commit); builds the host tests with `-Werror` and runs them, then rebuilds and runs them under ASan + UBSan; fetches and patches RadioLib and Arduino_RouterBridge into both sketches; runs `check_firmware.sh`; runs the station sketch harness under ASan + UBSan and under ThreadSanitizer | a compiler warning, a failed check, a sanitizer report, a stale library copy |
| `ground-tests` | The pytest suite from the repository root on Python 3.11 and on 3.13 (matrix, both always run) | any test fails |
| `arduino-build` | arduino-cli 1.5.1 with the released "Arduino Q Boards" platform (`arduino:zephyr`); bundles the patched libraries; builds the station and the flight sketch from their default profiles (default radio) and again with the SX126x option (SX1268), every warning on; checks that the logs name the bundled Arduino_RouterBridge 0.4.3 and RadioLib 7.8.1; runs the RouterBridge harness (also under ASan + UBSan), the bridge-thread stack bound and the loader symbol check (`tools/llext_check.py`) for both sketches; then installs `arduino:zephyr` 0.90.0 (the last core before 1.0.0; a warning and no build if the index has no 0.x release) and builds the flight sketch again, both radio options, with the loader symbol check | a compile error, a warning in this repository's code, a log that does not name a bundled copy, a harness failure, a worst-case stack above 1536 octets, a symbol the board's loader cannot resolve |

CI does not run hardware tests and cannot reach the boards. Note the version difference: CI installs arduino-cli 1.5.1 and the latest released
platform (then 0.90.0); the local real builds in 2.6 used arduino-cli 1.5.2 (as App Lab embeds it) and the
platform built from source at core main, 1.0.0 and 0.90.0.

---

## 5 Before any hardware test

### 5.1 Equipment

| Item | Needed for | Notes |
|---|---|---|
| Two 433 MHz antennas, or two 50 Ohm dummy loads | everything that transmits | never transmit without an antenna or load ([09_Regulatory_and_Safety.md](09_Regulatory_and_Safety.md)) |
| Coaxial attenuators, about 100 dB in total, the first rated >= 10 W (for example 40 dB 10 W + 30 dB + 30 dB) | T-RF-01..03, T-RF-06..08 | the radios accept at most +10 dBm at the antenna port (E22-400T33D manual v1.05 2.1, "blocking power") |
| Digital multimeter; bench power supply with current limit | T-HW-01, T-HW-07, T-HW-08 | |
| Oscilloscope (optional, better than a meter for supply dips) | T-HW-07, T-HW-08 | |
| RF power meter for 433 MHz bursts (or a calibrated SDR behind a known attenuator) | T-RF-07, T-RF-02 | measure the attenuator chain once with it |
| LoRa-capable signal source, or a second SX126x module (optional) | T-RF-06 reference method | the project's own station can replace it (T-RF-06 method B) |
| PC with a browser on the station's network | every test | the web UI is the instrument panel ([13_Web_UI_Reference.md](13_Web_UI_Reference.md)) |
| Compass, spirit level, a site magnetic-field value (NOAA or BGS WMM calculator) | T-SC-05, T-SC-06, T-SC-09, T-SC-11 | |
| Steady diffuse light source and a white reference card | T-SC-03, T-SC-08 | |
| NTP-synchronised clock (the VENTUNO Q on the internet, or a GPS receiver) | T-TM-04, T-GS-07 | |
| A spare E22 module on a 3.3 V USB-UART adapter (optional) | T-TC-07 step 7 | an independent receiver for the ground's plain-text identification |

### 5.2 Rules

1. Read [09_Regulatory_and_Safety.md](09_Regulatory_and_Safety.md) first. Transmit only on a
   frequency, power and emission your licence allows.
2. Build and wire exactly as [00_Beginner_Setup_Guide.md](00_Beginner_Setup_Guide.md) and
   [03_Hardware_Integration.md](03_Hardware_Integration.md) describe; run section 6 before
   section 8.
3. Bench RF tests use the attenuator chain, not antennas. Check the expected received power
   (transmit power minus attenuation) is between -110 dBm and -20 dBm before you key up.
4. For every test write down date, software commit, configuration (radio model, rate code,
   power code, channel, framing, SDLS services), the measured values and pass/fail in
   [21_Test_Records.md](21_Test_Records.md).
5. "Expected" values come from datasheets or this design. Where a datasheet gives none, the
   test **is** the measurement: record it, then update the named configuration value.
6. When a measurement disagrees with a value in the code (for example a power code), do not
   edit numbers to fit: record it and raise it as a defect, because both ends and the
   documentation must change together.

Where to look: EVR = event report in the EVENTS list of the web UI (the full catalogue with
operator actions is in [14_Troubleshooting_and_Event_Response.md](14_Troubleshooting_and_Event_Response.md));
HK, RF, MAG, SPEC = telemetry packets on the SPACECRAFT / SCIENCE / SPECTROMETER tabs
([05_Telemetry_and_Command_Dictionary.md](05_Telemetry_and_Command_Dictionary.md)).

---

## 6 Power, wiring and platform (T-HW)

| ID | Test | Method | Pass / record |
|---|---|---|---|
| T-HW-01 | Supplies under load | Meter (better: oscilloscope) at each rail with no load, then during a long transmission (ENCOUNTER mode) | E22-400T33D VCC >= 4.7 V during TX ("guaranteed output power", manual v1.05 2.2) and <= 5.5 V (absolute limit); E22-400T37S 4.5-15 V (manual v1.5 2.2), 5-12 V typical; VENTUNO Q +5V rail 4.75-5.25 V (ABX00181). Record the TX current (T33D 1.0 A typ, 1.2 A max; T37S 0.9-1.3 A at 12 V) |
| T-HW-02 | Spacecraft radio configuration | Boot, read the EVENTS list | EVR 0x0306 INFO `radio E22-400T33D OK: ch 23 code 2 pwr 33 dBm sens -128.0 dBm datasheet` (configuration written AND read back: proves both UART directions, M0, M1 and AUX); no FDIR RADIO_CFG. The read-back masks REG1's reserved bits 4..2 on the T33D, so a module that sets them still verifies |
| T-HW-03 | E22 mode pins at rest | Meter on M0 and M1 while the MCU does not drive them (power-up, an MCU reset, a sketch upload) | both about 3.3 V (logic high = deep sleep, the module cannot transmit) thanks to the 10 kOhm pull-ups: EBYTE calls the module's own pull-ups "very weak" |
| T-HW-04 | Instruments at boot | Boot with all instruments fitted; EVENTS and HK `sensor_health` | EVR 0x0706 `AS7265X HW .. FW .. devices ...` with all three devices; EVR 0x0910 (and 0x0911 if the inboard RM3100 is fitted) self-test PASS; HK sensor_health bits b0 NICLA_ME, b7 AS7265X, b8 RM3100_OB (b9 RM3100_IB) set; no EVR 0x0707 ABSENT / FAULT |
| T-HW-05 | Station I/O | COMMANDING tab, "Station I/O" panel; type on the CardKB | MCU CONNECTED, CardKB present at 0x5F; typed text echoed live; the ALARM lamp and buzzer follow a test alarm. CardKB at 3.3 V first; if keys are erratic, use the 5 V + BSS138 level shifter ([03_Hardware_Integration.md](03_Hardware_Integration.md)) |
| T-HW-06 | Ground radio configuration | GDS start-up (or the `RADIO` directive) | event `ground E22-400T33D: ch 23 (433.125 MHz) rate ... - matches station.toml`; no `MISMATCH`. The station noise floor updates every `noise_poll_s` (20 s) and the spacecraft's `sc_noise_floor` every 15 s: this confirms the T33D answers the 6-octet noise query `C0 C1 C2 C3 00 02` with noise plus last RSSI (documented for the T37S; the T33D manual's example asks for one octet) |
| T-HW-07 | Board-powered radio | See 6.1 | VCC at the module >= 4.7 V throughout a long TX; no MCU reset; RSSI within 1 dB of the external-supply run |
| T-HW-08 | Reset cause and brown-out marker | See 6.2 | POWER-ON after a cold start, BROWNOUT after a supply dip, never BROWNOUT after a power-off |
| T-HW-09 | Radio-model strap (A0) | Boot with A0 open, then with A0 jumpered to GND (to GND only, never to 5 V) | open: EVR 0x0308 `radio model E22-400T33D (strap open, 0/5 low; build E22-400T33D)`, RF `radio_model` = 1; to GND: `E22-400T37S (strap to GND, 5/5 low ...)`, `radio_model` = 2, every power code reads 37 dBm. A WARN 0x0308 `SPLIT` means a loose jumper: fix the wiring |
| T-HW-10 | 24 h endurance with the EGSE attached | See 6.3 | no reset, no hard fault, `loop_overruns` = 0, `loop_max_ms` recorded, every RPC alive |
| T-HW-11 | Solid-state recorder size | Boot; EVR 0x0003 | `SSR capacity 256 KiB` expected (largest power of two up to 256 KiB that leaves a 32 KiB heap reserve, `ssr.cpp`); record the value - it sets how long the SSR covers (about 12 h of CRUISE at 256 KiB) |

### 6.1 T-HW-07 - board-powered radio (contract sections 15 and 16.2)

The owner tests the E22-400T33D powered from the boards. Facts: the UNO Q header pin JANALOG
5 is USB-C VBUS passed through (`5V_USB_VBUS`, ABX00162 schematic), so the spacecraft UNO Q
must be powered through USB-C from a regulated 5 V supply that covers the board's own 5 V / 3 A
**plus** the radio's 1.2 A peaks (for example a 12 V to 5 V, >= 5 A buck with a USB-C lead);
do not power the UNO Q from VIN in this option. The station takes the radio's VCC from the
VENTUNO Q `+5V_LIMITED` shield pin (MP5077 load switch, 2.8 A limit; 4.75-5.25 V rail,
ABX00181). Fit 470-1000 uF low-ESR plus 100 nF at the module's VCC/GND, short thick wires.

1. Wire per [03_Hardware_Integration.md](03_Hardware_Integration.md) (board-powered option).
2. Oscilloscope on module VCC (ground at the module). MODE ENCOUNTER (longest duty).
3. Watch 10 transmissions. Record the minimum VCC during TX. **Pass: >= 4.7 V.**
4. Watch HK `reset_cause` and `sclk_partition` for 30 min. **Pass: neither changes** (every MCU
   boot gets a new partition from the EGSE, so a hidden reset shows up as a new partition).
5. Repeat T-RF-01 (same attenuators) and compare the 10-min mean RSSI with the
   external-supply run. **Pass: within 1 dB.** A lower RSSI means the supply sags during TX.
6. If step 3 fails, go back to the external-supply wiring (section 1.3 of the contract,
   [03_Hardware_Integration.md](03_Hardware_Integration.md)).

### 6.2 T-HW-08 - reset cause and the BKP31R brown-out marker

`HK reset_cause` comes from the STM32U585 RCC reset flags. Because the U5's brown-out flag is
also set by a normal power-up, the flight writes a marker word (`0x56475131`, "VGQ1") into the
TAMP backup register 31 on every boot (`fsw/flight.cpp read_reset_cause()`): a power-up
clears the backup domain (no marker = POWER-ON), a dip below the brown-out level while the
backup domain keeps power leaves it (marker = BROWNOUT). After a BROWNOUT the radio starts at
the reduced power code 2 (EVR 0x0608 WARN) and the ground raises a YELLOW alarm. Whether the
UNO Q's backup domain loses power at power-off is **VERIFY**: ABX00162 says the board's coin
input powers only the PMIC's real-time clock, not the MCU.

1. Remove all power for 10 s, power up. **Pass:** EVR 0x0001 `BOOT VGQ-1 FSW 3.0 rst=0 POWER-ON ...`,
   no EVR 0x0608.
2. Send `REBOOT 0xB007` (hazardous, confirm). **Pass:** the MCU restarts on its own, `rst=2
   SOFTWARE`, the EGSE issues a new SCLK partition.
3. Restart the App in App Lab. Record the reported cause (expected SOFTWARE or PIN).
4. Feed the board from the current-limited bench supply. Lower the voltage slowly until the MCU
   resets, then raise it again at once (do not switch off). **Pass:** `rst=4 BROWNOUT`, EVR 0x0608
   `brownout reset: TX power derated (code 2)`, ground YELLOW alarm, RF `tx_power_code` = 2.
   Restore power with `TXPWR 0`.
5. Repeat step 1 three times. **Pass: never BROWNOUT.** A BROWNOUT after a full power-off means
   the backup domain is kept alive on this board: record it (docs/20) - the brown-out derating
   then also happens after normal power-ups (safe, but costs 6 dB until `TXPWR 0`).

### 6.3 T-HW-10 - endurance, timing and the bundled RouterBridge on hardware

The patched Arduino_RouterBridge gives its RPC thread a 1536-octet stack (measured worst case
1084 octets, 42 % margin; [22_Bundled_Libraries.md](22_Bundled_Libraries.md)). The board has no
hardware stack protection, so the margin can only be confirmed by running. Other items that
only the hardware can show: the loop time with the EGSE attached, notifications larger than
256 octets through the router (the `tm_cadu` notification is 472 hex characters; the status
notifications are at most 307 and 444 characters, all under the 480-character rule), and the
transient heap use of each notification (inside the 32 KiB reserve the SSR leaves).

1. Spacecraft and station running, EGSE (the UNO Q App) running, MODE CRUISE, all instruments.
2. During the 24 h: one `CFDPPUT 0 0`, one `CFDPPUT 1 0`, a hardline command
   (`python -m dssq.cltu`, T-TC-05), a `PLAYBACK START`, `SPECMODE 4 10` (BURST, 10 s), a few
   `PING`s.
3. **Pass:** no MCU reset (HK `sclk_partition` unchanged; in particular no `reset_cause` 3
   WATCHDOG: the 40 s hardware watchdog never fires in normal operation), HK `loop_overruns` = 0, every
   `tm_cadu` archived by the EGSE (T-TM-01), every CFDP file COMPLETE, no EVR 0x0005 (Bridge
   registration failed), no EGSE log errors about RPCs. **Record** HK `loop_max_ms` (the
   design rule is that no call blocks more than about 20 ms; the documented exceptions are
   listed in [01_System_Design.md](01_System_Design.md)).
4. Station: same 24 h with the SX126x option if fitted; S| status `faults` and `resets` stay 0.

---

## 7 Ground-station platform (T-GS)

These checks confirm how Arduino App Lab runs the station App on the VENTUNO Q. The App and
its fallback (host mode, `dssq-groundstation/host/install_station.sh` + `dssq-gds.service`)
are described in [00_Beginner_Setup_Guide.md](00_Beginner_Setup_Guide.md).

| ID | Test | Method | Pass / fallback |
|---|---|---|---|
| T-GS-01 | Web UI port exposed by App Lab | Start the App; from another PC open `http://<board-ip>:8080` | The dashboard loads (app.yaml `ports: [8080]`, published N:N by App Lab). If it does not: check the App log for "address in use"; otherwise use host mode (`install_station.sh --service`) |
| T-GS-02 | E22 USB-UART inside the App container | E22 station, App running; RECEIVER tab "Other links" | radio link `connected`, no repeated reopen events. The App opens `[radio] port` (default `/dev/ttyUSB0`; check with `ls /dev/serial/by-id/`). If the container cannot open it, use host mode |
| T-GS-03 | ModemManager does not touch the USB-UART | On the board: `systemctl is-active ModemManager` | `inactive` (or not installed): pass. If active, it may probe a new `/dev/ttyUSB*` with modem commands: add a udev rule `ENV{ID_MM_DEVICE_IGNORE}="1"` for your adapter's USB vendor/product ID (from `lsusb`), reload udev, re-plug, repeat T-HW-06 |
| T-GS-04 | Router Bridge between the GDS and the station sketch | App mode: Station I/O panel shows the MCU; host mode: `ls -l /var/run/arduino-router.sock` | MCU CONNECTED and CardKB keys arrive in both modes. Never run the App and the host service at the same time (one Linux client may provide a method name) |
| T-GS-05 | External archive location (optional) | Set an absolute `[gds] archive_dir` on an M.2 2230 NVMe or USB 3.0 drive; start the App; FILES tab | Files appear on the drive and the FILES tab shows them. If the App container cannot see the path, keep the default `<App>/data/archive` (64 GB eMMC) or use host mode |
| T-GS-06 | AES-GCM speed on the VENTUNO Q | `cd ~/ArduinoApps/dssq-groundstation/python && python3 -m dssq.ccsds.aes_gcm` | Record the time per 200-octet frame (development PC: 0.28-0.33 ms). Pass: < 10 ms (the fastest E22 rate delivers at most about 33 CADUs per second) |
| T-GS-07 | Station clock | `timedatectl` on the board | "System clock synchronized: yes". Event times, ERT, the TDM and time correlation all use this clock |
| T-GS-08 | UDP telecommand listener is loopback only | See 7.1 | every non-loopback address refused at start, the loopback listener unreachable from the LAN |

### 7.1 T-GS-08 - UDP telecommand listener refusal

`[interop] tc_frames_udp_listen` lets an external mission control system on the station host
hand TC frames to the GDS, which re-wraps them with the station's SDLS key and radiates them
**without authentication and without the hazardous-command CONFIRM**. The configuration check
(`dssq/config.py parse_udp_listen()`, run again by `gds.Runner` before anything starts) accepts
only `127.x.y.z:port` or `[::1]:port`; host names (`localhost` too) and every other address are
refused, with no opt-in.

1. In the station's `station.toml` set `tc_frames_udp_listen = "0.0.0.0:10025"` and start the
   station. **Pass:** it does not start. Host mode (`python -m dssq.gds --config ...`): the log
   line `configuration: [interop] tc_frames_udp_listen = '0.0.0.0:10025' is not a loopback
   address ...` and exit status 2. App Lab: the App stops with a set-up error (exit status 2)
   naming the key in the App log.
2. Repeat with the board's LAN address, `"[::]:10025"` and `"localhost:10025"`. **Pass:** each is
   refused (`localhost`: "must be "", "127.0.0.1:port" (any 127.x.y.z) or "[::1]:port"").
3. Set `"127.0.0.1:10025"` and start. **Pass:** the station runs; on the board
   `ss -ulpn | grep 10025` lists `127.0.0.1:10025` only; a datagram sent from another PC to
   `<board-ip>:10025` queues nothing and radiates nothing (COMMANDING tab, EVENTS).
4. Set the key back to `""` unless an MCS on the station host needs it.

---

## 8 Radio frequency (T-RF)

Bench set-up for T-RF-01..03 and 05..08: spacecraft radio - coax - attenuators (about 100 dB)
- coax - station radio. Received power about 33 dBm - 100 dB = -67 dBm (T33D), 37 - 100 = -63 dBm
(T37S): 61 dB above the T33D's -128 dBm sensitivity and far below its +10 dBm limit.

| ID | Test | Method | Pass / record |
|---|---|---|---|
| T-RF-01 | Bench link | 100 dB path, MODE SAFE, 30 min | 0 frames lost (FRAMES: FER 0), 0 uncorrectable RS codewords, sync LOCK throughout; record the 10-min mean RSSI |
| T-RF-02 | **RSSI calibration** | See 8.1 | record the offset; set `[radio] rssi_cal_offset_db` |
| T-RF-03 | **Sensitivity per rate code** | See 8.2 | `[radio.sensitivity_measured]` lines from the tool |
| T-RF-04 | Noise floor and channel choice | `GSCAN 0 83` (ground directive) at the station site; `RFSCAN 0 83` (spacecraft command) at the spacecraft site | EVR 0x0303 `RF survey done ... quietest CHn`; record both surveys; choose the quietest channel inside `[radio] allowed_channels` and your licence |
| T-RF-05 | Uplink | `PING 1` x 20 at bench level | 20/20 EXECUTED (CMDVER); HK `cmd_accepted` +20; RF `uplink_rssi` recorded |
| T-RF-06 | **SX126x frequency-error sign and scale; static offset** | See 8.3 | sign and scale confirmed; `freq_offset_cal_hz` set, `freq_offset_calibrated = true` |
| T-RF-07 | **Transmit power per code** | See 8.4 | the power table confirmed or a defect raised; SX126x `pa_gain_db` / `kSxPaGainDb` set |
| T-RF-08 | **SX126x bring-up** | See 8.5 | every item of 8.5 passes |
| T-RF-09 | Duty cycle and PA temperature | MODE ENCOUNTER for 1 h; IR thermometer on the module shield | RF `tx_duty` <= 50 % (flight limit `kMaxTxDutyPermille` = 500; ground limits YELLOW 55 %, RED 70 %); no EVR 0x0313 (T37S over-temperature, > 120 degC) or 0x0604; record the module temperature (operating range -40..+85 degC) |
| T-RF-10 | Listen-before-talk / CAD | `RADIOOPT 1`; a second transmitter on the channel for a minute | RF `lbt_busy` counts deferrals; frames resume when the channel clears. SX126x station: after 2 s of CAD-busy the station transmits anyway and counts `cad_forced` - confirm this is acceptable on your channel |

### 8.1 T-RF-02 - RSSI calibration

The radios report RSSI with 1 dB (E22) or 0.5 dB (SX126x) resolution and an absolute error
nobody specifies. One offset, added to every RSSI and noise reading, fixes the absolute scale.

1. Measure the transmit power with T-RF-07 (or use the module's datasheet value and say so).
2. Measure (power meter) or take from datasheets the loss of the whole attenuator chain and
   cables at 433 MHz: `L_path`.
3. Expected received power: `P_exp = P_tx - L_path` (dBm).
4. Run the bench link 10 min; read the RECEIVER tab statistics, RSSI, 10 min window: mean
   (4 decimals) and its standard error.
5. `rssi_cal_offset_db = P_exp - mean_RSSI`. Put it in `[radio] rssi_cal_offset_db` of the
   station's `station.toml` (App Lab: `<App>/data/station.toml`), restart the App.
6. Check: the new 10-min mean RSSI equals `P_exp` within 1 dB. Record `P_tx`, `L_path`, the
   raw mean, the offset.

### 8.2 T-RF-03 - measured sensitivity

The tool turns a stepped-attenuation session into measured sensitivities; the ground then
uses them instead of the datasheet / estimated / computed values (source badge "measured").
Nobody may enter measured values without this test.

1. Bench link (T-RF-01), T-RF-02 done, the GDS archiving (default).
2. Choose a rate code (`LINKRATE <code>`; E22 codes 2-7, SX126x profiles 0-7).
3. Add attenuation in steps of 2-3 dB from an error-free level, at least 2 minutes per step,
   until frames stop. Either time-stamp the steps live:
   `python3 tools/link_budget.py sensitivity --archive <archive_dir> --live --rate-code <code>`
   or write a steps file `start_utc,end_utc,atten_db,rate_code[,model]` and run
   `python3 tools/link_budget.py sensitivity --archive <archive_dir> --steps steps.csv --ref-dbm <P at 0 dB step>`.
   `<archive_dir>` under App Lab is `/home/arduino/ArduinoApps/dssq-groundstation/data/archive`.
4. Per step the tool computes expected frames (from the frame period), the frame error rate and
   the received power; the sensitivity is the power at FER = 10 %, interpolated between the two
   steps that bracket it. It **refuses** (exit status 2) when the FER never crosses 10 %.
5. Copy the printed TOML lines into `[radio.sensitivity_measured]`, e.g.
   `"E22-400T33D:2" = -127.5`. Accepted values lie in -160..-60 dBm (`link.py`).
6. Expected: E22-400T33D at 2.4k near the datasheet -128 dBm typical (-126 worst, manual v1.05
   2.2); SX126x profiles near the computed / datasheet table in
   [02_Link_Budget_and_RF_Analysis.md](02_Link_Budget_and_RF_Analysis.md). Repeat for every
   code you will fly.

### 8.3 T-RF-06 - SX126x frequency error: sign, scale and static offset

The SX126x reports a per-packet frequency error that the ground uses for Doppler, range-rate,
the TDM and the automatic frequency correction (AFC). Its register (0x076B-0x076D) and scale
(Hz = 1.55 x value x BW_kHz / 1600) come from RadioLib's `getFrequencyError()`, not from the
datasheet (SX1261/2 DS rev 2.2 Table 12-1 does not list them), and the sign convention is
unknown: **VERIFY** (`drivers/sx126x.cpp`, station sketch, `link.py`). The ground assumes the
reported error is `+(f_received - f_receiver)` (AFC `sign = +1`).

Method A (reference): a LoRa-capable signal source sends CADUs at the channel frequency
+1000 Hz, AFC off. **Pass:** the station reports +1000 Hz plus the source's own known error.

Method B (project hardware only; the station shifts its own receiver by an exact amount -
the SX126x synthesizer step is 32 MHz / 2^25, about 0.95 Hz):

1. Bench link; a fast profile for many packets (`LINKRATE 7`, SF7 / 125 kHz).
2. `[radio.sx126x] afc = false`, `[radio] freq_offset_cal_hz = 0`; restart. Let 100 packets in.
   Record the mean frequency error `e0` (RECEIVER statistics, 10-min window, mean +/- SEM).
3. Set `freq_offset_cal_hz = 1000` (the station now listens 1000 Hz higher: the SX126x MCU
   status `foff_hz` shows +1000); restart; record `e1` the same way.
4. **Pass:** `e1 - e0 = -1000 Hz +/- 50 Hz`. Then sign and scale are right at 125 kHz.
   `+1000 Hz` means the sign convention is inverted: the AFC copes with it on its own (one
   "sign convention inverted" event), but Doppler, range-rate and the TDM would carry the wrong
   sign - raise a defect. A magnitude off by more than 5 % means the scale is wrong: record the
   factor and raise a defect.
5. Repeat steps 2-4 at profile 1 (SF12 / 62.5 kHz; 17.3 s per CADU, so allow time) to check the
   bandwidth term of the scale.
6. Static offset: with `freq_offset_cal_hz = 0`, on the bench (no motion), `e0` is the combined
   crystal offset of the two modules. Set `[radio] freq_offset_cal_hz = <e0, rounded>` and
   `freq_offset_calibrated = true` (only then does the TDM carry `DOPPLER_INSTANTANEOUS`).
7. Enable the AFC (`afc = true`), restart. **Pass:** the AFC panel reaches TRACK, the residual
   stays inside the deadband (max(50 Hz, 2 % of BW)), no sign-inversion event, the Doppler on
   the bench reads about 0 Hz. Record the AFC constants' behaviour (first step after 3 packets,
   sign check over 3 packets, confirmation timeout 10 s, settle one CADU air time + 0.5 s).
8. Optional: the SNR the chip reports (SnrPkt) and its reference bandwidth (the TDM's PC_N0 uses
   SNR + 10 log10(BW)): compare against a calibrated signal and noise if you have them.

### 8.4 T-RF-07 - transmit power per code

The E22-400T33D manual v1.05 copies its REG1 power table from the T30D (30/27/24/21 dBm); the
code assumes 33/30/27/24 dBm for codes 0-3 (code 0 = the specified 33 dBm maximum). The
E22-400T37S has no power levels (always 37 dBm). The SX126x radiated power is the chip drive
(+22/+17/+14/+10 dBm for codes 0-3) plus the module's PA gain, assumed 11 dB for the reference
EBYTE E22-400M33S. The web UI shows a "VERIFY T-RF-07" badge on every unmeasured value.

1. Spacecraft radio into the power meter through a >= 10 W attenuator (30-40 dB). Account for
   the attenuator and cable loss measured at 433 MHz.
2. MODE TEST; the spacecraft transmits CADUs continuously. Use the meter's burst/peak mode, or
   average power divided by the RF `tx_duty`.
3. For each `TXPWR 0..3`: wait for EVR 0x0214 `TXPWR code n = x dBm`, measure 10 bursts, record.
4. **Pass (T33D):** 33 / 30 / 27 / 24 dBm within +/-1 dB per code (manual 2.2: maximum 32-34 dBm).
   **T37S:** 36-37.5 dBm for every code (manual v1.5 2.2). **SX126x:** measured minus drive =
   PA gain; set `kSxPaGainDb` (config.h) and `[radio.sx126x] pa_gain_db` (station.toml) to it.
5. Repeat for the station transmitter (send `PING` repeatedly; uplink bursts are short, use
   peak hold).
6. A T33D ladder that differs from 33/30/27/24 is a defect: the tables in
   `drivers/radio.cpp`, `dssq/link.py` and the docs must change together. Also measure the
   brown-out code 2 (T-HW-08).

### 8.5 T-RF-08 - SX126x bring-up (option only)

Module constants in `config.h` and the station sketch are **VERIFY** against your module's
datasheet and wiring (reference: EBYTE E22-400M33S, SX1268).

| Item | Check | Pass |
|---|---|---|
| TCXO supply on DIO3 (`kSxTcxoV` 1.8 V flight, `kSxTcxoMv` 1800 station, `[radio.sx126x] tcxo_v`) | module datasheet; boot EVRs | no EVR 0x0309 `SX126x TCXO did not start` (flight); station `S\|` status flags b3 = 0; no chip/TCXO mismatch event |
| RF switch (`kSxDio2RfSwitch` false; RXEN D8 / TXEN D9 on the UNO Q, A2 / A3 on the VENTUNO Q) | module datasheet and wiring | packets received (RX path) and power measured (T-RF-07, TX path) |
| PA current limit (`kSxOcpMa` 140 mA, the SX1262 value; SX1268 datasheet not available) | SX1268 datasheet; supply current at code 0 | no PA clamping / power shortfall at +22 dBm drive |
| Rx boosted gain | flight RF `rf_flags` b0; station `S\|` status flags b2 | both set: register 0x08AC read back = 0x96, retention list 0x029F-0x02A1 = 01 08 AC |
| BUSY bounds (25 ms guard, 40 ms SPI timeout) | 24 h run | station `S\|` status `faults` and `resets` stay 0; no false "BUSY stuck" at bring-up. Optional fault injection: hold BUSY high - a radio fault EVR, recovery, the loop keeps running |
| Image calibration 430-440 MHz | `LINKCHAN` to another allowed channel and back | link resumes on each channel |
| Hibernation wake (32-symbol wake preamble, 2.1 s at SF12 / 62.5 kHz) | `HIBERNATE 600`, then `WAKE` | EVR 0x0801 `WAKE: ...`, normal frames resume |
| RS rescue | during T-RF-03 near sensitivity | RECEIVER decoder `rs_rescued` > 0: CRC-failed packets repaired by Reed-Solomon |
| Chip ID | `[radio.sx126x] chip` = the sketch's `DSSQ_SX_CHIP` | no mismatch event |

---

## 9 Telemetry and telecommand (T-TM, T-TC)

| ID | Test | Method | Pass |
|---|---|---|---|
| T-TM-01 | End-to-end integrity | Copy the spacecraft EGSE CADU log (`<UNO Q App>/data/cadu_YYYYMMDD.bin`, keep the `cadu_` name) to the station and decode it and the station's own archive (`<App>/data/archive/YYYYMMDD/cadu.bin`) with `python -m dssq.replay --app-data <App>/data --cadu-file <file> --packets` (the station's `station.toml` and key, [18_Data_Products.md](18_Data_Products.md) section 13) | identical frames for every MCFC the station received |
| T-TM-02 | Packet continuity | FRAMES & PACKETS tab, 1 h | no packet sequence gap without a matching lost frame |
| T-TM-03 | Recorder playback | 30 min CRUISE, then `PLAYBACK START` | packets arrive on VC2 in order; EVR 0x0402 `SSR playback complete` |
| T-TM-04 | **Time correlation** | See 9.1 | `radio_latency_s` set; residual RMS below the frame-period jitter |
| T-TM-05 | SDLS TM epoch and nonce safety | See 9.2 | protected after every boot, null SA only without the EGSE, never an epoch twice |
| T-TM-06 | CFDP file delivery | `CFDPPUT 0 0`, `CFDPPUT 1 0`, `CFDPPUT 2 0` (source, max_kib 0 = whole file), one at a time; then a `CFDPCANCEL` mid-transfer | FILES tab: three transactions COMPLETE with checksum OK, files under `<archive>/files/`; the cancelled one CANCELLED (EOF condition 15) |
| T-TC-01 | Command life cycle | `NOOP` | QUEUED -> RADIATED -> ACCEPTED -> EXECUTED |
| T-TC-02 | COP-1 retransmission | 3 commands while the uplink is briefly blocked (remove the station attenuator path for a minute) | FOP-1 retransmits; all EXECUTED exactly once after restoring |
| T-TC-03 | SDLS on the uplink | See 9.3 | accepted only with the right key and a fresh sequence number |
| T-TC-04 | Two-person rule | `require_two_person_for_hazardous = true`; `TXINHIBIT 30` | not radiated until CONFIRM and a second operator's AUTH |
| T-TC-05 | Hardline (umbilical) commanding | On the VENTUNO Q: `cd ~/ArduinoApps/dssq-groundstation/python && python3 -m dssq.cltu NOOP --app-data ../data` prints one hex line (station key and SN file); append it to the UNO Q's `~/ArduinoApps/vgq1-spacecraft/data/hardline_queue.txt`. With a key and TC AUTH or AE the tool needs the station's SN file `../data/archive/sdls_sn.txt`: run the GDS on that data folder until an HK made at least 120 s after the spacecraft's boot has arrived (it writes the file), or write it by hand ([17_Security_Model.md](17_Security_Model.md) section 7.6); without it the tool prints `TC uplink BLOCKED: no TC sequence-number file ...` and exits with status 1. With a station clock behind, it refuses with `SDLS: sequence number <n> would be above the time bound <f> ...` (exit status 1): set the clock first | EXECUTED with CMDVER source 1 (BD frame or hardline), no RF used |
| T-TC-06 | Stored sequence on hardware | A 3-line sequence file (e.g. `T+00:00:30 NOOP`) loaded from the SEQUENCES tab, then `SEQCTRL START 0` | each entry executes once (EVR 0x0904), EVR 0x0906 `sequence COMPLETE`; record the execution time error against the planned times |
| T-TC-07 | **Station identification on air, both ends** | See 9.4 | both callsigns identified at the required times; the spacecraft ignores the ground's identification |

### 9.1 T-TM-04 - time correlation and `radio_latency_s`

1. T-GS-07 passed (station clock synchronised).
2. Collect more than 10 `TIMECORR` samples (`TIMECORR` command) at a known distance.
3. The ground fits the spacecraft clock (SCLK) to UTC. Set `[radio] radio_latency_s` so that the
   mean offset of the computed spacecraft event time (SCET) against a GPS/NTP-synchronised
   reference is 0. For the SX126x the Bridge path to the station MCU is part of this latency.
4. **Pass:** residual RMS below the frame-period jitter. Record `radio_latency_s` per radio model
   and rate code.

### 9.2 T-TM-05 - SDLS TM epoch (nonce safety)

AES-GCM must never reuse a nonce. The TM nonce contains an epoch that the EGSE gives the MCU at
every MCU boot (a persisted counter that only goes up). Facts: `kSdlsEpochWaitS` = 120 s
(config.h); HK `sdls_tm_state` 1 = protected, 2 = waiting for epoch, 3 = unprotected fallback.

1. Both keyed, AE (default). Boot. **Pass:** EVR 0x0120 `SDLS TM epoch n: downlink protected`,
   HK `sdls_tm_state` = 1, LINK & SECURITY tab shows protected frames.
2. Stop the UNO Q App (EGSE), reboot the MCU. **Pass:** only OID (idle) frames for 120 s, then EVR
   0x0121 and frames under the null SA (state 3, flagged UNPROTECTED on the ground).
3. Start the EGSE. **Pass:** a new epoch, larger than every earlier one (`boot_count.txt`),
   protection resumes.
4. Repeat 10 MCU reboots. **Pass:** 10 different epochs, strictly increasing; no `tm_replay`
   or `tm_epoch_regressed` counts on the ground.

### 9.3 T-TC-03 - SDLS on the uplink

| Case | Set-up | Pass |
|---|---|---|
| a | Spacecraft keyed, station without key file (services NONE) | every TC rejected by SDLS (EVR 0x011x), HK `sdls_auth_fail` rises |
| b | Both keyed with the same key (`tools/gen_sdls_key.py`, generated after import) | accepted |
| c | Both keyed, different keys | rejected `bad MAC` (EVR 0x0115) |
| d | Re-send an old CLTU (hardline queue) | rejected `REPLAY` (EVR 0x0114) |
| e | Reboot the MCU, then command at once | refused with EVR 0x0123 until the EGSE restores the SN; without the EGSE, after 120 s accepted from the in-RAM counter with EVR 0x0124 |
| f | AUTH mode (`VGQ_SDLS_TC = 1`, `tc_service = "AUTH"`) | accepted; the command content is readable on air (amateur-band mode, [09_Regulatory_and_Safety.md](09_Regulatory_and_Safety.md)) |

### 9.4 T-TC-07 - station identification on air, both ends

Both transmitters identify themselves ([09_Regulatory_and_Safety.md](09_Regulatory_and_Safety.md)
1.3). The spacecraft sends EVR 0x0002 `DE <callsign>` at boot and every 600 s
(`config.h kCallsignPeriodS`) and puts `DE <callsign> VGQ-1 ` into every idle (OID) frame, which
is never encrypted. The ground's `StationIdent` (`dssq/gds.py`) sends `DE <callsign> <name>` as
plain ASCII through the radio path the commands use: right after the first uplink of a run; then
whenever an uplink transmission is not yet covered and the last identification is at least
600 s minus max(60 s, 3 frame periods) old (at most 600 s; it can only go in an uplink window,
so it goes early enough for a window to carry it even when two downlink frames are lost; with
more lost, the housekeeping sends it without a window 600 s - 2 s - its air time after the last
one); once more when the uplink has been idle for max(120 s, the FOP T1) ("end");
during LOS from the housekeeping, as no uplink window comes (after the last window's planned
transmissions); and at shutdown if the last transmission is not covered. In a window its air
time is booked first (it is transmitted after the window's CLTUs), and the window plan
(`UplinkWindowPlan`) admits CLTUs only while their transmitter time (busy time + 0.05 s, + 5
CAD symbols on the SX126x) fits the shortest quiet window of any mode less 0.2 s; the others
wait for a later window. So, by the busy-time model, the station has stopped before the next
downlink frame starts; step 8 checks it on air
([02_Link_Budget_and_RF_Analysis.md](02_Link_Budget_and_RF_Analysis.md) section 7.2). The
spacecraft finds no CLTU start sequence in the identification, counts it in RF `cltu_bad` and
drops it with no EVR, no FDIR flag and no FARM-1 effect (host test `test_station_id.cpp`).

1. Set your callsign at both ends: `config.h VGQ_CALLSIGN` (rebuild, upload) and the station's
   `[station] callsign`. **Pass:** no WARN event `[station] callsign is the placeholder N0CALL`.
2. Use the amateur configuration (TC AUTH, TM NONE; 09 section 1.4) so the downlink is readable
   without a key. **Pass (spacecraft):** EVENTS shows EVR 0x0002 `DE <callsign>` at boot and then
   every 10 minutes; the LINK & SECURITY tab shows the OID identification text
   `DE <callsign> VGQ-1` (snapshot `link.ident`).
3. Send `NOOP`. **Pass (ground):** an IDENT event `station identification (periodic): DE
   <callsign> DSS-Q1` right after the command's transmission; the uplink table's "Station
   identification" row (text, count, last time; snapshot `link.station_id` also holds the
   reason) updates; on the
   spacecraft RF `cltu_bad` rises by 1 per identification while HK `farm_state`, `farm_vr`,
   `cmd_rejected` and `tc_frames_bad` do not change and no EVR appears.
4. Command at least once a minute for 25 minutes (e.g. `PING 1`). **Pass:** identifications at
   most 10 minutes apart (IDENT events), none missing.
5. Stop commanding. **Pass:** one `station identification (end)` event once the uplink has been
   idle for max(120 s, T1).
6. Within 2 minutes of the step 5 identification (so that no periodic one goes with the
   command; not at SX126x profile 0, where the periodic one falls due after about 3 minutes),
   send one command, then stop the station within the next minute. **Pass:** the
   closing identification is radiated at shutdown (step 7 receiver, or the spacecraft's RF
   `cltu_bad` + 1 after the station restarts; the shutdown identification logs no event).
7. Independent check (optional): a spare E22 on a USB-UART in transparent mode, set to the same
   channel, air rate, address and net ID, opened in a serial terminal, shows the ground's
   identification as readable text. The spacecraft's frames are randomized and Reed-Solomon coded
   (CCSDS): decode a recording with `python -m dssq.replay --cadu-file <file>`, which prints the
   OID identification - a terminal shows only coded octets.
8. Window plan (E22 2.4k, or SX126x profile 1; MODE ENCOUNTER, where the quiet window is
   shortest): wait until an identification is due (about 9 minutes after the last one at E22
   2.4k, about 6.5 minutes at SX126x profile 1), then
   queue two commands with 82-octet CLTUs at once: two `MAGCAL` under TC + AE (the defaults),
   or, in the amateur configuration of step 2 (TC AUTH), under `[link] tc_framing = "USLP"`
   (the flight accepts both; under TC AUTH a MAGCAL CLTU is only 74 octets, and two of them fit
   beside the default identification). **Pass:** the identification and the first command
   share a window, the second command follows one frame period later, and no downlink frame is
   lost (FRAMES: FER unchanged, no sync loss).

---

## 10 Instruments (T-SC)

| ID | Test | Method | Pass / record |
|---|---|---|---|
| T-SC-01 | Nicla Sense ME attitude | Rotate the spacecraft 90 deg about each axis | fusion and TRIAD Euler angles follow; their difference (SCIENCE tab) stays within a few degrees when still |
| T-SC-02 | Accelerometer | Still, each axis up in turn | magnitude 1.000 +/- 0.02 g |
| T-SC-03 | Spectrometer basic | Cover / daylight / `SPECCFG 2 50 1 0 0` (white lamp) | the spectrum changes as expected; lamp flags in SPEC; the white lamp is the only lamp lit |
| T-SC-04 | (retired) | The Nicla Sense Env was removed in v3 | - |
| T-SC-05 | RM3100 noise and field | `MAGCC 200`, quiet room, 10 min | record the fluctuation (MAG `ob_rms`); the field magnitude matches the site's WMM total field within a few uT |
| T-SC-06 | **BMM150 scale** | See 10.1 | field magnitude constant at the site's WMM total field |
| T-SC-07 | Sampling in PA-off windows | MODE ENCOUNTER, 1 h | MAG flags b0 (PA keyed during a sample) never set |
| T-SC-08 | **AS7265X protocol, timing and calibration** | See 10.2 | every item of 10.2 passes or is recorded |
| T-SC-09 | **Boresight frame** | See 10.3 | `bore_az` / `bore_zen` match the pointing |
| T-SC-10 | RM3100 self-test timing | `MAGBIST 2` on the bench | EVR 0x0910 / 0x0911 PASS; DRDY within the 5 ms bound (`kRm3100BistTimeoutUs`); HK `mag_bist` 0xFF for two healthy units; the next normal MAG sample is sane (the self-test does not leave a stale value) |
| T-SC-11 | Magnetometer calibration with a real rotation | Procedure in [07_Operations_Procedures.md](07_Operations_Procedures.md) (starts with `PKTRATE MAG 1`; `[science] field_ut` set) | the fit is accepted; record coverage, condition number and residual; confirm or adjust the thresholds MIN_COVERAGE 0.5, MAX_CONDITION 1e3, MAX_AXIS_RATIO 4, MAX_RESIDUAL_FRACTION 0.05, MIN_STEP_UT 1.0, MIN_SAMPLES 50 (`dssq/magcal.py`, chosen from synthetic trials) |
| T-SC-12 | Lamp / magnetometer interlock | SPECMODE LOCKIN with lamps, MAG at 1 s | MAG flags b4 (lamp lit or unknown during a sample) set only for samples taken after the 2.1 s deferral limit (`kSpecLampDeferMaxMs`); record how often |

### 10.1 T-SC-06 - BMM150 scale

The Nicla firmware ([nicla_sense_me_aru.ino](../nicla_sense_me_aru/nicla_sense_me_aru.ino))
scales the BHI260AP's "magnetometer corrected" counts by the dynamic range the sensor reports
(BHI260AP datasheet Table 88) and sends 0.1 uT (`kAruMagUtPerLsb` = 0.1). If the range cannot
be read, a fallback of 1/16 uT/LSB is used and ATT flag b5 ("BMM150 scale read") stays clear.

1. The Nicla prints its ranges on its own serial port at start (`mag +/-... uT`); record them.
2. Away from steel, rotate the spacecraft slowly through all orientations for 2 minutes.
3. **Pass:** ATT flag b5 set; the magnitude of the BMM150 field stays constant within a few uT
   and equals the site's WMM total field. A constant ratio means a scale error: record it.

### 10.2 T-SC-08 - AS7265X checks

The AS7265X datasheet (v1-04) leaves several timings and bit meanings open; the driver uses the
SparkFun library's reading with bounded timeouts. Each row below is a VERIFY in the code.

| Item | Code | Check | Pass / record |
|---|---|---|---|
| Boot time (firmware loads from the board's EEPROM) | `as7265x.h` `kSpecBootWaitMs` 1000 ms | Cold boot | EVR 0x0706 appears; record the time from boot |
| Soft-reset settling | `kSpecResetWaitMs` 1000 ms | `SPECDIAG`, then a forced recovery (unplug SDA for 2 s) | EVR 0x0705 `recovered by soft reset` |
| Handshake response | `kSpecVregTimeoutMs` 50 ms, 8..400 status polls | 1 h of SPEC packets | no handshake errors |
| Resync byte answered as a read | `kSpecResyncPolls` | Repeated fault injection on SDA | the driver always resynchronises |
| DATA_RDY with CONFIG.INT set (INT pin not wired) | `config_value()` | Every exposure mode | frames complete; no DRDY timeouts |
| One-shot re-trigger right after begin / abort / burst end | driver state machine | Trigger a lamp-on one-shot early | the data show the lamp; DATA_RDY reads 0 just after the trigger |
| Gain and integration apply to all three devices | `as7265x.cpp` (CONFIG under the last DEV_SEL) | Step the gain | all 18 channels move together |
| Calibrated output independent of gain and integration (APPROXIMATE) | `spectral.h` | Steady diffuse source at all four gains and N = 29 / 59 / 127 / 255 | calibrated values agree within the datasheet's +/-12 %; record the spread |
| BURST rate | `kSpecBurstCycles` 1: (1 + 1) x 2.78 ms = 5.56 ms per bank | `SPECMODE 4 10` | achieved rate (EVR 0x0704 BURST END) <= 1 / conversion time; record it |
| DEV_SEL slave flags b4 / b5 | `decode_devsel()` | Boot | "devices" in EVR 0x0706 lists all three |
| FW version selector reading | `as7265x.cpp` | Boot | EVR 0x0706 shows a plausible FW version (SparkFun: major "currently 0x0C") |
| COEF word byte order | `SPECDIAG` lines | `SPECDIAG 0 4` | record the four words; do not assume a significance |
| Lamp deferral limit | `kSpecLampDeferMaxMs` 2100 ms | T-SC-12 | as T-SC-12 |

### 10.3 T-SC-09 - boresight frame

The spectrometer boresight is assumed along body +Z (`kSpecBoresightBody` = {0, 0, 1}) and the
Nicla fusion quaternion is assumed to rotate into ENU (Android sensor convention, which the
BHI260AP follows). Both are **VERIFY**.

1. Point the boresight straight up. **Pass:** `bore_zen` about 0 deg.
2. Point it horizontally to magnetic north (correct for declination). **Pass:** `bore_zen` about
   90 deg, `bore_az` about 0 deg (true north).
3. Turn it to east. **Pass:** `bore_az` about 90 deg.
4. If the axes or the azimuth direction are wrong, record the mounting and raise a defect
   (`kSpecBoresightBody`, `kRm3100ToBody`, the world frame in `fsw/spectral.h`).

---

## 11 Fault protection (T-FD)

| ID | Test | Method | Pass |
|---|---|---|---|
| T-FD-01 | Command loss | `CMDLOSS 120`, then send nothing | after 120 s: EVR 0x0601, MODE SAFE, default channel and rate |
| T-FD-02 | Unconfirmed radio change reverts | `LINKRATE 3`, then switch the station radio off before the confirming frame | after `revert_s`: EVR 0x0602, link back on the old rate |
| T-FD-03 | Hibernate and wake | `HIBERNATE 120` | one HK beacon every 120 s; `WAKE` restores normal frames (EVR 0x0801) |
| T-FD-04 | Instrument loss | Unplug the Nicla Sense ME for 10 s | EVR 0x0606, FDIR SENSOR_LOST; EVR 0x0607 after reconnecting |
| T-FD-05 | Radio under-voltage (T37S only) | Current-limited supply below 4.5 V on the T37S | EVR 0x0311, FDIR LOW_BUS_V; EVR 0x0315 cleared after recovery |
| T-FD-06 | I2C bus clear | Short SDA to GND for 2 s (through 100 Ohm) | EVR 0x0605 bus clear + re-init (EVR 0x060A while SDA is still held low); EVR 0x0609 healthy again 10 s after the last error; MAG / SPEC / ENV packets resume |
| T-FD-07 | TX inhibit | `TXINHIBIT 30` | no transmissions for 30 s, EVR 0x0603 `transmitter re-enabled` |
| T-FD-08 | Beacon levels | Cause an IMPORTANT event (e.g. T-FD-04) without commanding | HK `beacon_level` 2, banner and annunciator; drops back only after the next accepted command |
| T-FD-09 | **Hardware watchdog (IWDG)** | See 11.1 | no EVR 0x060C at boot; a deliberately hung loop resets the MCU about 40 s later with `reset_cause` 3 WATCHDOG |
| T-FD-10 | `RSTCNT` clears the latched flags | After T-FD-01 (FDIR CMD_LOSS set), send `RSTCNT` | HK `fdir_flags` loses CMD_LOSS (and LOOP_OVERRUN, RATE_REVERT, SDLS_AUTH if set: `kFdirClearedByRstcnt`, `fsw/tlm_packets.h`); state-driven flags stay as they are |

### 11.1 T-FD-09 - hardware watchdog

The flight arms the STM32U585's independent watchdog at the end of `setup()` with a 40 s timeout
(`config.h VGQ_IWDG` = 1, `kIwdgTimeoutMs` = 40000 at the nominal LSI frequency `kIwdgLsiHz` =
32 kHz; prescaler and reload from `iwdg_settings()`, host-tested) and refreshes it once per
completed `loop()` pass, on the loop thread only (`sketch.ino`, `Flight::watchdog_feed()`). No
command can hang the loop - every wait is bounded - so the watchdog can only be provoked with a
bench build. **A debugger halt is not a valid test:** `watchdog_start()` sets
`DBGMCU_APB1FZR1.DBG_IWDG_STOP`, which freezes the watchdog counter while the core is halted.

1. Normal build, cold boot. **Pass:** no EVR 0x060C (`IWDG settings not confirmed in 200 ms`);
   T-HW-10 runs 24 h without a WATCHDOG reset.
2. Bench build only (never fly it): in `vgq1-spacecraft/sketch/sketch.ino` add as the first line
   of `loop()` a deliberate hang after three minutes of uptime,
   `if (millis() > 180000u) { for (;;) { } }`, and upload it.
3. **Pass:** the telemetry stops about 3 minutes after boot; about 40 s later the MCU resets and
   boots again: EVR 0x0001 `BOOT ... rst=3 WATCHDOG`, HK `reset_cause` 3, a new SCLK partition
   from the EGSE, the beacon URGENT (every boot enters SAFE). The hang repeats every boot.
   **Record** the time from the last frame to the next boot event; the LSI's real frequency, not
   the nominal 32 kHz, sets the exact timeout.
4. Remove the line, upload the normal build again and confirm step 1. A hang during `setup()`
   (before the watchdog starts) is not covered (docs/20 L-38).

---

## 12 Field (T-FLD)

| ID | Test | Record |
|---|---|---|
| T-FLD-01 | Range | For each site pair: distance, antenna heights and types, predicted vs measured received power (RECEIVER link-budget table), excess loss, FER, margin p10, rate code. Use the "reading the RECEIVER tab during a range test" walkthrough in [13_Web_UI_Reference.md](13_Web_UI_Reference.md) and the templates in [21_Test_Records.md](21_Test_Records.md) |
| T-FLD-02 | RSSI versus distance | Received power at 5-10 distances along one line; compare with free space (FSPL) and the two-ray / horizon limits in [02_Link_Budget_and_RF_Analysis.md](02_Link_Budget_and_RF_Analysis.md) |
| T-FLD-03 | Power draw in operation | Average and peak current of both ends per mode (SAFE / CRUISE / ENCOUNTER / hibernation), for the power budget in [03_Hardware_Integration.md](03_Hardware_Integration.md) |

---

## 13 Coverage of every VERIFY item

Every "VERIFY" tag in the code or configuration (`grep -rn VERIFY` over the two Apps, `tools/`
and `tests/`, bundled libraries excluded), the hardware-only facts added in the final fix round,
and the test that closes each. Open items with
their risk are tracked in [20_Known_Limitations.md](20_Known_Limitations.md).

| VERIFY item | Where in the code | Closed by |
|---|---|---|
| E22-400T33D power codes 1-3 = 30/27/24 dBm | `drivers/radio.cpp`, `drivers/e22.h`, `drivers/radio.h`, `dssq/link.py`, `dssq/radio/e22.py`, `dssq/dictionary.py`, `station.toml`, web UI badges, `tests/test_radio.py`, `tools/link_budget.py` | T-RF-07 |
| Brown-out power code 2 = 27 dBm on the T33D | `tools/host_test/test_radio.cpp`, `config.h kBrownoutPowerCode` | T-RF-07, T-HW-08 |
| SX126x module PA gain 11 dB | `config.h kSxPaGainDb`, `drivers/sx126x.h`, `drivers/sx126x.cpp`, `station.toml pa_gain_db`, `dssq/link.py` | T-RF-07 |
| SX126x TCXO 1.8 V on DIO3 | `config.h kSxTcxoV`, `drivers/sx126x.h`, station sketch `kSxTcxoMv`, `station.toml tcxo_v`, EVR 0x0309, `dssq/radio/sx126x.py` | T-RF-08 |
| SX126x DIO2 RF switch / RXEN-TXEN wiring | `config.h kSxDio2RfSwitch`, `drivers/sx126x.h`, station sketch | T-RF-08 |
| SX1268 OCP 140 mA | `config.h kSxOcpMa`, `drivers/sx126x.h` | T-RF-08 |
| SX126x frequency-error register, scale and sign | `drivers/sx126x.cpp`, station sketch, `dssq/link.py` (Doppler, AFC sign self-check) | T-RF-06 |
| SX126x receiver noise figure 6 dB (computed sensitivities) | `dssq/link.py SX126X_NF_DB` | T-RF-03 |
| Station radio pipeline latency | `station.toml radio_latency_s` | T-TM-04 |
| AES-GCM speed on the VENTUNO Q | `dssq/ccsds/aes_gcm.py` | T-GS-06 |
| Archive on an external NVMe / USB drive visible in the App container | `station.toml [gds]` | T-GS-05 |
| BMM150 scale (0.1 uT from the BHI260 range) | `config.h kAruMagUtPerLsb`, Nicla firmware | T-SC-06 |
| RM3100 mounting to body axes | `config.h kRm3100ToBody` | T-SC-01, T-SC-09 |
| Spectrometer boresight and fusion world frame | `config.h kSpecBoresightBody`, `fsw/spectral.h`, web UI | T-SC-09 |
| AS7265X timings, bit meanings, calibration assumption, burst rate | `drivers/as7265x.h/.cpp`, `fsw/spectral.h`, `config.h kSpecLampDeferMaxMs`, `tools/host_test/test_spectral.cpp` | T-SC-08, T-SC-12 |
| RM3100 self-test duration | `fsw/autonomy.h kRm3100BistTimeoutUs` | T-SC-10 |
| Rice encoder stack and run time on the MCU | `ccsds/rice.h` | T-HW-10 (`loop_max_ms` during BURST) |
| CFDP EOF fault-location TLV required or optional | `ccsds/cfdp.h` (the ground accepts both) | standard text check (no hardware) |
| Reset cause / brown-out marker in TAMP BKP31R | `fsw/flight.cpp read_reset_cause()` | T-HW-08 |
| Bundled RouterBridge stack margin, notification sizes on the router, heap transients, loop time with the EGSE | `tools/arduino_routerbridge-0.4.3.patch`, `fsw/flight.cpp` | T-HW-10 |
| App Lab exposes port 8080; App opens the USB-UART; router socket | `dssq-groundstation/app.yaml`, `python/main.py` | T-GS-01, T-GS-02, T-GS-04 |
| Hardware watchdog resets a hung loop (40 s at the nominal LSI) | `config.h VGQ_IWDG`, `kIwdgTimeoutMs`; `fsw/flight.cpp watchdog_start()` | T-FD-09 |
| Station identification of both transmitters on air | `config.h VGQ_CALLSIGN`, `kCallsignPeriodS`; `dssq/gds.py StationIdent`, `[station] callsign` | T-TC-07 |
| UDP TC listener refuses every non-loopback address | `dssq/config.py parse_udp_listen()`, `[interop] tc_frames_udp_listen` | T-GS-08 (host-tested too: `tests/test_radio.py`) |
| E22 air time and busy model (payload bits / rate x 1.3) | `dssq/link.py E22_AIRTIME_FACTOR`, flight radio model | T-RF-03, T-RF-09 |
| Absolute RSSI (uncalibrated until the offset is measured) | `station.toml rssi_cal_offset_db` | T-RF-02 |

Items that the other documents mark VERIFY (power consumption, CardKB levels and key codes, the
buzzer, cable lengths, router UART facts, the station sketch staying on the MCU in host mode)
are collected in [20_Known_Limitations.md](20_Known_Limitations.md) section 1.6 with their test.

---

## 14 One-off verifications on record

Checks that were run once during development and are documented in the commit history
([CHANGELOG.md](../CHANGELOG.md)); they are not repeated by CI.

| Verification | Result | Commit |
|---|---|---|
| CCSDS 121.0 flight encoder against libaec 1.1.7 and by differential fuzzing under ASan/UBSan | 9000 cases, 0 mismatches; flight 56/56 official files, ground 74/74 official streams | 1bbd28e |
| Ground with `cryptography`, `reedsolo`, `pyserial`, `tomli`, `xmlschema` all blocked | 167 passed, 2 optional skips: the station needs no pip package | c8c3b04 |
| SDLS TM epoch interleavings (exhaustive and randomised) | 0 nonce reuses; the code before the fix reused in 255 of 300 runs | fbf16cf |
| AS7265X driver against the SparkFun library 1.0.5 and the ams datasheet | 20 findings confirmed and fixed | 8115f61 |
| Nicla firmware in a harness with the real `SensorID.h` | mag 262/-131/655 counts at 2500 uT -> 200/-100/500 x 0.1 uT; 101325 Pa exact | 2206fc4 |
| RouterBridge 0.4.3 on the real headers (host test with RPClite 0.3.1 and MsgPack 0.4.2) | stock header failed 3 of 12 checks at the time (the Phase D harness in CI makes it 4 of 12), patched passes all; worst-case stack 1084 octets measured with `-fstack-usage` on the real toolchain (now a CI step) | 5b06a04 |
| Receiver data logger: live simulator run | CLI and web exports, retention and the free-space guard exercised; FILES tab with zero page errors | 7f0d36c |
| XTCE export against the SpaceSystem 1.2 schema | 0 errors; 4 injected faults each give exactly 1 error | a970823 |
