# 16 - Developer Guide

For anyone who changes the code: where everything is, how the pieces connect, what to touch when
you add a sensor, a packet, a command, an event report or a configuration key, the coding rules,
every check you can run locally (the same ones CI runs), and how a release is packaged.

Why things are the way they are: [12_Design_Decisions.md](12_Design_Decisions.md). Protocol
layouts: [04_Space_Data_Link_ICD.md](04_Space_Data_Link_ICD.md). Generated packet and command
tables: [05_Telemetry_and_Command_Dictionary.md](05_Telemetry_and_Command_Dictionary.md).

---

## 1 What you need

| Tool | Version | For |
|---|---|---|
| git | any recent | everything; `tools/radiolib.py` / `routerbridge.py fetch` and `package_apps.py` call it |
| Python | 3.11 or newer (CI: 3.11 and 3.13) | ground code, tests, tools |
| g++ and make | C++17 (CI: ubuntu-latest GCC) | host tests, `check_firmware.sh`, the harnesses |
| Development packages | `requirements-dev.txt`: pytest, ruff 0.17.0, mypy 2.4.0, and the optional reedsolo 1.7.0, cryptography, xmlschema 4.3.2 | tests and lint |
| arduino-cli + "Arduino Q Boards" (`arduino:zephyr`) | 1.5.x (CI 1.5.1; App Lab embeds 1.5.2) | real builds (optional locally, always in CI) |
| actionlint | 1.7.7 used | only when you edit `.github/workflows/ci.yml` |
| shellcheck | any recent (CI: the runner's) | the shell scripts (CI job `lint`) |
| Network access to github.com | - | fetching the bundled libraries and the official CCSDS 121.0 test data |

One-time set-up from the repository root:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
# the bundled libraries, as App Lab and CI use them (git-ignored copies)
for s in vgq1-spacecraft/sketch dssq-groundstation/sketch; do
  python3 tools/radiolib.py fetch "$s/libraries/RadioLib"
  python3 tools/routerbridge.py fetch "$s/libraries/Arduino_RouterBridge"
done
```

Never install anything into an App folder (`python/requirements.txt` would make App Lab download
packages: decision D-06).

---

## 2 Repository map

```text
vgq1-spacecraft/                 UNO Q App (flight software + Linux EGSE)
  app.yaml, README.md
  python/main.py                 EGSE: CADU archive, SCLK partition = SDLS TM epoch, TC SN restore,
                                 CFDP relay, hardline queue, status board (App Lab entry point)
  python/cfdp_tx.py              EGSE CFDP class-1 sender (stdlib only)
  data/                          run-time state, never in Git or a zip: boot_count.txt (SDLS TM
                                 epoch), sdls_last_sn.txt, hardline_queue.txt, cadu_/evr_/sc_status_ day files
  sketch/sketch.ino              Router Bridge registrations, setup()/loop() -> vgq::Flight
  sketch/sketch.yaml             default profile: arduino:zephyr:unoq + the two bundled libraries
  sketch/src/config.h            every build switch, pin and tunable of the flight (D-04)
  sketch/src/ccsds/              protocols, pure C++ (host-tested)
    ccsds_types.h                mission constants (SCID, frame / CADU geometry, VCs), helpers
    tm.*  uslp.*  tc.*           TM / USLP framing, TC + CLTU/BCH + FARM-1
    reed_solomon.*  randomizer.*  crc16.*
    sdls.*  frame_security.h     SDLS TC (AUTH / AE) and TM (AE / null SA)
    aes.*  gcm.*  sha256.*       crypto primitives
    cfdp.*  rice.*               CFDP class-1 sender, CCSDS 121.0 encoder
  sketch/src/drivers/            hardware access
    radio.*                      radio abstraction + pure tables / LoRa maths (host-tested)
    e22.*  sx126x.*              E22 UART driver; SX126x via the bundled RadioLib
    uart_pump.*  byte_fifo.h     E22 port pump thread for 64-octet Serial rings (D-66)
    i2c_bus.*                    I2C with error accounting and bus clear
    sensors.*  payload.*  as7265x.*   RM3100, Nicla ARU link, AS7265X
  sketch/src/fsw/                executive and algorithms
    flight.*                     the executive (CCS, FDS, AACS, telecom, FDIR) - needs Arduino
    datalink.*                   downlink scheduler, TC accept chain, load plan, gates (host-tested)
    commands.*  tlm_packets.*    flight half of the dictionary (host-tested)
    autonomy.*                   stored sequences, beacon level, MAGCAL store, BIST decode
    spectral.*  attitude.*  ssr.*  sclk.h  rpc_mailbox.h  status_display.*
  sketch/src/libc_compat.cpp     __errno for cores before 1.0.0 (newlib-nano libm)
dssq-groundstation/              VENTUNO Q App (station sketch + ground data system)
  app.yaml (ports: [8080]), README.md
  python/main.py                 App Lab entry: data folder, config copy, token, Runner
  python/config/station.toml     packaged default configuration (copied to <App>/data once)
  python/dssq/                   the ground data system package
    gds.py                       GroundStation + Runner: receive chain, commanding, snapshot
    config.py                    defaults, validation, App Lab path rules
    dictionary.py  decom.py  cmdparse.py   ground half of the dictionary, decoding, command language
    fop.py  framesync.py  link.py  linkmgr.py  rxstats.py  radioctl.py
    archive.py  datalog.py  export.py      archive, 1 Hz logger + retention, CSV export
    products.py  magcal.py  spectral.py    TDM / XTCE, magnetometer calibration, spectral analytics
    replay.py  cltu.py  sim.py             offline replay, hardline CLTUs, software spacecraft
    ccsds/                       ground protocol stack (tm, uslp, tc, packets, rs, randomizer, crc,
                                 timecode, sdls, aes_gcm, cfdp, rice)
    radio/e22.py  radio/sx126x.py          E22 serial (termios) and SX126x Bridge backends
    display/web.py  dashboard.html  frontpanel.py   web server + UI, station I/O link
  sketch/sketch.ino, sketch.yaml station MCU: CardKB, lamps, buzzer, RCU, optional SX126x radio
  host/install_station.sh, dssq-gds.service   host (systemd) mode (D-55)
  data/                          run-time state, created at first start, never in Git or a zip:
                                 station.toml, sdls_key.hex, http_token, archive/ (same in host mode)
nicla_sense_me_aru/              Nicla Sense ME firmware (Arduino IDE, Mbed Nicla core + Arduino_BHY2)
tools/
  host_test/                     flight code on a PC: Makefile, host_test.cpp (runner) and one
                                 test_<topic>.cpp per area - autonomy (sequences, beacon, IWDG
                                 settings, RSTCNT), byte_fifo (UART pump FIFO), cfdp, crypto, integration (packets, commands,
                                 vectors, mailbox), radio, rice, spectral, station_id (the ground
                                 identification is never a CLTU), tx_duty (TxDutyMeter), uslp
    check_firmware.sh            every Arduino file against strict stand-ins (arduino_stub/)
    station/run.sh               the station sketch on host threads (test_e22.cpp, test_sx.cpp)
    routerbridge/run.sh, stackcalc.py   bundled RouterBridge: behaviour test, stack bound
  radiolib.py  routerbridge.py  libbundle.py  *.patch   bundled libraries (22_Bundled_Libraries.md)
  package_apps.py                builds / verifies the two App Lab zips
  llext_check.py                 will the board's loader accept a built sketch (docs/08 section 2.6)
  gen_sdls_key.py                SDLS key for both ends
  gen_dictionary_doc.py          writes docs/05 from the code
  link_budget.py                 link budget; `sensitivity` = test T-RF-03 analysis
tests/                           pytest suite (run from the repository root; conftest.py sets the path):
                                 one test_<topic>.py per area, among them test_station_id.py (ground
                                 identification), test_docs.py (docs/13 drift), test_citations.py
                                 (docs/12 appendix B) and test_packaging.py (the App Lab zips)
docs/                            this documentation; docs/img/ screenshots
dist/                            the two App Lab zips + SHA256SUMS (committed releases)
requirements-dev.txt, pyproject.toml, .github/workflows/ci.yml
```

---

## 3 How the pieces connect

**Flight loop.** `sketch.ino` registers four Router Bridge services and calls `Flight::setup()`
once and `Flight::loop()` forever. Each pass services the RPC mailbox, the radio events, the
uplink accept chain (`datalink` `tc_accept`: CLTU -> frame -> SDLS -> FARM-1 -> `parse_user_data`
-> `execute`), the 1 Hz FDIR and housekeeping tick, the periodic packet generators (cadence table
`kModeCadence` stretched by the load plan), the instruments in PA-off windows, the stored-sequence
table, CFDP pacing, and finally the next frame when the frame period allows it. `setup()` ends by
arming the hardware watchdog (IWDG, 40 s), and `sketch.ino` refreshes it after every completed
pass (`Flight::watchdog_feed()`, D-60): a change that makes one pass take tens of seconds resets
the MCU.

**Downlink path.** A generator fills a packet struct -> `pack_*()` (big-endian user data) ->
`Flight::emit(apid, payload, n, vc, record, priority)` adds the primary and CUC headers and the
per-APID count, records it in the SSR, and pushes it to a VC queue -> `Downlink` picks the VC by
priority -> `TmFramer` (TM or USLP) builds the 200-octet frame through the `FrameSecurity` hook
(SDLS) -> RS, randomizer, ASM -> 236-octet CADU -> radio; a copy goes to the EGSE (`tm_cadu`).

**Ground path.** `gds.Runner.step()` stages: radio input -> frame sync (ASM) -> RS ->
TM/USLP parse -> SDLS -> per-VC packet extraction -> `decom` with `dictionary.PACKETS` ->
`_on_<apid>` handlers, archive, receiver statistics, limits and alarms -> snapshot for the web
UI. Commanding: web / CardKB text -> `cmdparse` (dictionary checks) -> confirmation -> `fop` (COP-1)
-> SDLS -> TC frame -> CLTU -> radio.

**The dictionary is defined twice and tested once.** Flight: `fsw/tlm_packets.h`,
`fsw/commands.h`, `fsw/spectral.h`. Ground: `dssq/dictionary.py` (+ `spectral.py` for SPEC and
SPECBURST). The host test packs known values with the flight code and writes vector files
(`tlm_vectors.txt`, `command_table.txt`, integration CADU streams, ...); `tests/test_cross_check.py`
decodes them with the ground code. Any divergence fails the build.

---

## 4 End-to-end checklists

Each list names every file that must change. Work through it top to bottom; the checks of
section 6 tell you when you missed one.

### 4.1 Add a field to an existing packet

1. Flight `fsw/tlm_packets.h`: add the member to the struct **at the end** of the packet if you
   can (the layout is an interface), update the offset comments and the `k<Name>Len` constant.
   Use the "not available" sentinels for missing values.
2. Flight `fsw/tlm_packets.cpp` `pack_<name>()`: write it big-endian with the `put_*` helpers (no
   casts on buffers).
3. Flight `fsw/flight.cpp` `gen_<name>()`: fill it (range-check anything from outside).
4. If the packet is periodic: its length in `kPeriodicPktLen` (`fsw/datalink.cpp`) follows from
   `k<Name>Len`; check the downlink load (host test `downlink_load`).
5. Host test `tools/host_test/test_integration.cpp`: the known-value builder (`hk_known()` and
   friends) and its expected text in the `tlm_vectors` writer.
6. Ground `dssq/dictionary.py` `PACKETS[apid]["fields"]`: the same field at the same place
   `(name, struct code, scale, unit, description, limits[, decoder])`; add it to `NO_NA` if every
   raw value is a reading (counters, masks).
7. Ground display: `gds.py` (snapshot / decoded values) and `display/dashboard.html` if the
   operator must see it; a new snapshot key must also appear in
   [13_Web_UI_Reference.md](13_Web_UI_Reference.md) (drift test `tests/test_docs.py`).
8. Simulator `dssq/sim.py`: emit a plausible value.
9. Regenerate docs/05 (`python3 tools/gen_dictionary_doc.py`) and update
   [04_Space_Data_Link_ICD.md](04_Space_Data_Link_ICD.md) if it lists the layout.
10. Run section 6 (host tests, pytest - `test_cross_check.py` above all).

### 4.2 Add a new packet (APID)

All of 4.1, plus:

1. `fsw/tlm_packets.h`: a new `APID_*` (11 bits; keep the grouping: 0x01x engineering on VC0,
   0x02x science on VC1, 0x03x files on VC3), the struct, `k<Name>Len`, `pack_<name>()`.
   Update `kNumApids` and the index order of `apid_index()` (an unknown APID is refused by
   `emit()`, it never borrows another counter). Keep the user data <= `kMaxPayload` (146 octets,
   one AE frame's data field minus 12 header octets).
2. `fsw/flight.cpp`: a `gen_<name>()` and a call site. A **periodic** packet also needs a
   `PeriodicIdx` entry and a column in `kModeCadence` (`fsw/datalink.h/.cpp`), `kPeriodicPktLen`,
   `gen_periodic()`, and the ground `PERIODIC_APIDS` / PKTRATE enumeration in `dictionary.py`;
   an **event** packet is emitted where the event happens. Choose the VC (`VC_RT_ENG` /
   `VC_RT_SCI`), whether it is recorded in the SSR (`record`), and whether it may use the VC
   priority reserve (`priority`: alarms only).
3. Ground `dictionary.py`: `APID_<NAME>`, `APID_VC`, `PACKETS` (fixed layout) - or, for a
   variable-length packet, `VARIABLE_APIDS` plus a decoder in `decom.py` / `spectral.py` and an
   explicit builder in `products.py` for XTCE (fixed layouts are exported automatically).
4. Ground `gds.py`: a handler in the packet dispatch if the packet drives state, alarms or the UI.
5. Tests: a packing / cross-check case (host test + `tests/test_cross_check.py`), and a
   simulator case (`sim.py`) so `tests/test_end_to_end.py` sees it.
6. Docs: 04 (APID table), 05 (generated), 13 (if shown), 18 (if archived differently).

### 4.3 Add a telecommand

1. Flight `fsw/commands.h`: the opcode in `enum Opcode` (next free value; today 0x20 is the last)
   and `kOpcodeMax`. Document the argument mapping in the comment block.
2. Flight `fsw/commands.cpp` `parse_user_data()`: exact argument length, every range and
   cross-argument rule (`CE_BAD_LENGTH`, `CE_BAD_ARGUMENT`). No side effects here.
3. Flight `fsw/flight.cpp` `execute()`: the action; resource or hardware checks (`CE_BUSY`,
   `CE_HARDWARE`) belong here; emit an EVR for anything the operator must see (4.4).
4. Hazardous? Ground `dictionary.py` `hazardous=True` **and** flight `fsw/autonomy.cpp`
   `opcode_forbidden()` so stored sequences refuse it (test
   `tests/test_sequence.py::test_flight_forbidden_list_matches_dictionary` compares the two).
   Could it cut the link? Then it must heal by itself (revert timer, timeout) - D-39.
5. Ground `dictionary.py` `COMMANDS`: `CommandDef(mnemonic, opcode, args, hazardous, desc,
   check, check_doc)` with the same ranges as the flight; `check` mirrors every cross-argument
   rule. The command language (`cmdparse.py`), the XTCE export and the web dictionary pick it up.
6. Host test `test_integration.cpp` command table: at least four rows per opcode (accepted, bad
   length, out-of-range values; `test_flight_command_table_matches_ground` requires
   `rows >= 4 x commands` and every opcode present), and move the "first unknown opcode" row
   (`Bytes{0x21}` today) to the next free value.
7. If it changes the uplink size: a TC packet must fit the 64-octet TC frame (27 octets of packet
   with AE; less with USLP + AE, see D-23).
8. Operations and docs: 05 (generated), [07_Operations_Procedures.md](07_Operations_Procedures.md),
   [14_Troubleshooting_and_Event_Response.md](14_Troubleshooting_and_Event_Response.md) for new
   EVRs; the simulator if the command changes behaviour that tests observe.

### 4.4 Add an event report (EVR)

1. Pick an unused id in the right block (0x00xx boot / system, 0x01xx uplink and SDLS, 0x02xx
   COP-1 and radio changes, 0x03xx radio, 0x04xx SSR, 0x05xx command rejections, 0x06xx fault
   protection, 0x07xx spectrometer, 0x08xx hibernation, 0x09xx sequences / magnetometer, 0x0Axx
   CFDP). `grep -n "0x0NNN" vgq1-spacecraft/sketch/src` must find nothing.
2. Write `evr(<severity digit>, 0xNNNN, "<literal format>", ...)` - the severity and the id as
   literals: `tools/gen_dictionary_doc.py` parses exactly that form for docs/05.
3. Severity: 0 DIAG, 1 INFO, 2 WARN, 3 ERROR, 4 FATAL. Know the consequences: severity >= 2
   raises the beacon to IMPORTANT until the next ground contact and uses the VC0 priority reserve;
   0x0701 / 0x0702 raise it to INTERESTING; severity < 3 is subject to the routine-EVR budget
   (`EvrGate`; exempt: EVRs raised in `setup()`, 0x0120-0x0122, 0x0703).
4. Text: worst case <= 80 characters after formatting (`pack_evr` truncates); no floating-point
   `printf` (print tenths or hundredths from integers, as the existing EVRs do); no text that
   could be confused with a command. Count the worst case and note it in the review.
5. Ground: nothing to code for a plain EVR (decoded generically, logged, levels mapped); add
   behaviour only if the ground must react (alarm, UI). Update the EVR catalogue with the operator
   action in [14_Troubleshooting_and_Event_Response.md](14_Troubleshooting_and_Event_Response.md)
   and regenerate docs/05.

### 4.5 Add a sensor (instrument)

1. Hardware first: a unique 7-bit address on the `Wire2` bus (today 0x20, 0x23, 0x2A, 0x49 are
   taken), 3.3 V supply and pull-ups only (A4/A5 are not 5 V tolerant), no Qwiic (only the AS7265X may use it, D-13); update
   [03_Hardware_Integration.md](03_Hardware_Integration.md).
2. Driver `src/drivers/<sensor>.*` on `I2cBus` (never `Wire` directly): register map from the
   datasheet with the section cited, a timeout on every wait, non-blocking (start / poll / result,
   spread over loop passes, <= about 20 ms per call), every return value checked, no heap, no
   third-party library.
3. Executive `flight.cpp`: probe in `init_sensors()`, re-acquire in `reprobe_instruments()` if it
   can appear later, sample only in PA-off windows (and never while an AS7265X lamp is lit if it is
   magnetic), feed the FDIR (`FDIR_I2C_BUS`, `FDIR_SENSOR_LOST`, an EVR on loss and recovery).
4. Health and control: a bit in `SensorBit` (`fsw/tlm_packets.h`; b10 is the next free) and in
   `kSensorInstrumentMask` if SENSORS may switch it; mirror `SENSOR_BITS` /
   `SENSOR_INSTRUMENT_MASK` in `dictionary.py`.
5. Data: a field in an existing packet (4.1) or a new packet (4.2); its cadence.
6. Pure processing in `src/fsw/` with a host test (`tools/host_test/test_<topic>.cpp`).
7. `check_firmware.sh` compiles the new driver automatically (every `src/**/*.cpp`); add its
   Arduino API to `tools/host_test/arduino_stub/` only if the stand-ins lack it.
8. Docs: 03 (wiring, address), 05 (generated), 08 (a hardware test), 15 (any new setting), 20
   (VERIFY items).

### 4.6 Add a configuration key

**Flight (`config.h`):**

1. A named constant with its unit and source in the comment (`static const` with a `k` prefix; a
   build switch as an `#ifndef`-guarded `#define` with an `#error` for invalid values).
2. Values that are not proven: say **VERIFY** in the comment and add the item to
   [20_Known_Limitations.md](20_Known_Limitations.md) and a test to
   [08_Verification_and_Test.md](08_Verification_and_Test.md).
3. A new build switch: add its values to the flight build matrix of `check_firmware.sh`.

**Ground (`station.toml`):**

1. `dssq-groundstation/python/config/station.toml`: the key with its default and a comment (unit,
   range, which flight constant it must match).
2. `dssq/config.py`: the same default in `DEFAULTS` (the two must mirror each other; tests compare
   them) and a check in `validate()` that raises `ValueError` naming the key.
3. If the key is a path or a secret: the App Lab rules of `resolve_app_paths()`; a secret must
   stay empty in the packaged file (`package_apps.py` refuses `[gds] http_token` /
   `[commanding] auth_token`).
4. A test in the topic's test file (default value, an invalid value refused).
5. [15_Configuration_Reference.md](15_Configuration_Reference.md): the key, default, range, units
   and any **MUST-MATCH** partner on the other end (for example `[radio] channel` and
   `kRadioChannel`).

---

## 5 Coding standard

### 5.1 Flight C++ (MCU)

* Holzmann's "Power of Ten" rules (D-56): no recursion; every loop has a fixed upper bound; no
  heap after initialisation (the SSR is allocated once at boot; `new`, `malloc`, `String` growth
  and STL containers are not used at run time); every return value checked; range checks on every
  value from outside (telecommands, radio, sensors, Bridge); smallest variable scope; no undefined
  behaviour (no type punning on buffers - `memcpy` and the `put_u16` / `get_u16` helpers).
* Warning-free with `-Wall -Wextra -Wshadow` (host builds use `-Werror`; CI fails on any compiler
  warning in our code in the real builds).
* FP32 only: `float`, `sqrtf`, `f` literals; no `double` arithmetic in the flight loop; no
  floating-point `printf`.
* Never block: state machines, not waits; one call <= about 20 ms (documented exceptions only).
  Never `Bridge.call()`; never `Monitor` / `Serial` except under `VGQ_DEBUG_MONITOR` (D-43).
* Pure code (no `Arduino.h`) in `src/ccsds/` and `src/fsw/` so the host tests compile it.
* Big-endian on the wire everywhere; sentinels for "not available".
* Comments: a module header (purpose, governing standard or datasheet section, units), contracts
  on non-obvious functions, short "why" comments; VERIFY for every unproven value.

### 5.2 Python (ground, EGSE, tools)

* Standard library only in both Apps (D-06); test-only packages are optional and skip cleanly.
* Python 3.11 syntax (`target-version = "py311"`), type hints everywhere, dataclasses for
  records; `ruff` rules E, F, W, B, UP, SIM, C4, RUF at line length 120; `mypy` clean with
  `check_untyped_defs` (settings in [../pyproject.toml](../pyproject.toml)).
* No bare `except`; `except Exception` only at a documented thread or stage boundary (Runner
  stages, front-panel threads, the web request handler, the EGSE user loop) that logs, counts and
  continues.
* Validate every external input (configuration, RF data, HTTP bodies, files); everything that
  arrived over RF is untrusted text even with SDLS (D-36).
* Crash-safe persistence: temporary file, fsync, rename (`atomic_write_*` helpers) for every
  state file.
* Deterministic tests: fixed seeds, fake clocks, temporary directories; never write into the tree
  (keys especially).

### 5.3 Repository rules

* Never commit keys (`sdls_key.h`, `sdls_key.hex`) or tokens; they are git-ignored, and the
  packaging refuses them.
* Text files: LF line ends, a final newline, no trailing white space (CI `lint` job).
* Few new files and folders; pure algorithm code goes into the existing `src/fsw/` modules.
* No AI-model names anywhere (code, comments, documents, commit messages).
* Commit messages: an imperative subject line; the body says what changed and why, and the
  verification run with its numbers (which checks, how many passed, real-build sizes).

### 5.4 Citation tags in comments

Many comments cite the project's design records by short tags: `contract 24.2` (the v3
interface contract), `PB-3.3` (the Phase B integration specification), `C-27` (its conflict
register), `F-25` (its follow-up list) and `I1`-`I4` (the Phase C interface decisions). The
records themselves are not in the repository: each tag's topic and the place where it is
documented now are in [12_Design_Decisions.md](12_Design_Decisions.md) appendix B (citation
tags). New comments should cite the decision (`D-24`) or the document section instead. If you
add a citation tag, or remove the last use of one, update appendix B in the same change:
`tests/test_citations.py` fails in both directions and names the tag.

---

## 6 Running every check locally

Run from the repository root. The order below is the order of a full gate.

### 6.1 Host tests (flight code on the PC)

```sh
# strict build, warnings are errors (as CI)
make -C tools/host_test host_test \
  CXXFLAGS="-std=c++17 -O2 -Wall -Wextra -Wshadow -Wno-unused-parameter -Werror -DVGQ_HOST_BUILD -I../../vgq1-spacecraft/sketch/src"
mkdir -p /tmp/vgq-vectors && tools/host_test/host_test /tmp/vgq-vectors
# expected last line: HOST TEST PASSED (0 failures)

# AddressSanitizer + UBSan
make -C tools/host_test clean
make -C tools/host_test host_test \
  CXXFLAGS="-std=c++17 -O1 -g -fno-omit-frame-pointer -fsanitize=address,undefined -fno-sanitize-recover=undefined -Wall -Wextra -Wno-unused-parameter -DVGQ_HOST_BUILD -I../../vgq1-spacecraft/sketch/src"
mkdir -p /tmp/vgq-vectors-san && tools/host_test/host_test /tmp/vgq-vectors-san
```

`make -C tools/host_test` alone builds with the default flags and writes the vectors to
`tools/host_test/vectors/`. The current number of checks per test file is in
[08_Verification_and_Test.md](08_Verification_and_Test.md) section 2.1. With the official
CCSDS 121.0 test data the Rice tests run every file:

```sh
git init -q /tmp/libaec && git -C /tmp/libaec fetch -q --depth 1 \
  https://github.com/MathisRosenhauer/libaec.git b8fd98cfbba2fcc82f1c99813465d7c4806b9d4d
git -C /tmp/libaec checkout -q FETCH_HEAD
CCSDS121_TESTDATA=/tmp/libaec/data/121B2TestData tools/host_test/host_test /tmp/vgq-vectors
```

### 6.2 Firmware compile check and the harnesses

```sh
bash tools/host_test/check_firmware.sh          # exit 0 = no FAIL line
# optional: LIBS_DIR=<folder with Arduino libraries> to type-check vendor-library paths

SAN="-fsanitize=address,undefined -fno-sanitize-recover=undefined" bash tools/host_test/station/run.sh
SAN=-fsanitize=thread bash tools/host_test/station/run.sh      # exit 0 = every check passed
```

`check_firmware.sh` compiles every translation unit of both sketches against the strict Arduino
stand-ins in `tools/host_test/arduino_stub/` for every build switch (radio models, USLP, SDLS
modes, a keyed build with a temporary key, the debug monitor), all RadioLib units for both
boards, the stock-RadioLib negative control, and checks the bundled copies. It does **not**
replace the real build (`.ino` files are compiled as plain C++). Without network access the
RadioLib parts are reported as skipped.

### 6.3 Ground test suite, lint and types

```sh
.venv/bin/python -m pytest -q                    # tests/ (pyproject.toml), builds tools/host_test itself
python3.11 -m pytest -q                          # repeat on the oldest supported Python
.venv/bin/ruff check .
.venv/bin/mypy && .venv/bin/mypy dssq-groundstation/python/main.py   # two runs: two main.py files
shellcheck tools/host_test/check_firmware.sh tools/host_test/station/run.sh \
  tools/host_test/routerbridge/run.sh dssq-groundstation/host/install_station.sh
```

Useful options: `-k <expr>`, `-x`; `EGSE_RANDOM_SEEDS=<n>` (default 60) and
`EGSE_EXHAUSTIVE_DEPTH=4` (28 561 orderings) for the nonce-safety tests of `test_cfdp.py`;
`CCSDS121_TESTDATA` (all 74 official Rice streams); `DSSQ_OREKIT_TDM_DIR` (Orekit's TDM example
files); `DSSQ_XTCE_XSD` (path of `SpaceSystemV1.2.xsd`, with `xmlschema` installed: XTCE schema
validation). Tests that need an optional package or file skip cleanly without it.

The current results of every layer (tests per file, skips, `check_firmware.sh` and harness
results) are recorded in [08_Verification_and_Test.md](08_Verification_and_Test.md); a run on
your machine must match them, with skips only for the optional data sets above.

### 6.4 Real builds (the App Lab toolchain)

```sh
arduino-cli core update-index && arduino-cli core install arduino:zephyr
arduino-cli compile --profile default --warnings all -v vgq1-spacecraft/sketch    > /tmp/flight.log
arduino-cli compile --profile default --warnings all -v dssq-groundstation/sketch > /tmp/station.log
# SX126x options
arduino-cli compile --profile default --warnings all \
  --build-property "compiler.cpp.extra_flags=-DVGQ_RADIO=3 -DVGQ_SX126X_CHIP=1268" vgq1-spacecraft/sketch
arduino-cli compile --profile default --warnings all \
  --build-property "compiler.cpp.extra_flags=-DDSSQ_RADIO_SX126X=1 -DDSSQ_SX_CHIP=1268" dssq-groundstation/sketch
grep "Using library Arduino_RouterBridge" /tmp/flight.log   # must name <sketch>/libraries/Arduino_RouterBridge
```

The profile names the board (`arduino:zephyr:unoq` / `arduino:zephyr:ventunoq`); the bundled
libraries must have been fetched (section 1), otherwise arduino-cli refuses the profile. Then, for
the bundled RouterBridge:

```sh
bash tools/host_test/routerbridge/run.sh /tmp/flight.log        # hunk-2 behaviour + stock negative control
arduino-cli compile --profile default -v --build-path /tmp/stack-flight \
  --build-property "compiler.cpp.extra_flags=-fstack-usage -fcallgraph-info=su" \
  vgq1-spacecraft/sketch > /tmp/stack-flight.log
python3 tools/host_test/routerbridge/stackcalc.py --log /tmp/stack-flight.log --cut egse_ \
  --cut z_log_msg_runtime --check vgq1-spacecraft/sketch/libraries/Arduino_RouterBridge/src/bridge.h /tmp/stack-flight
```

Flash and RAM of the reference builds, and the margin left: [01_System_Design.md](01_System_Design.md)
section 9.1 and [08_Verification_and_Test.md](08_Verification_and_Test.md) section 2.6. Compare
your build against them; a large jump needs a reason in the commit message.

### 6.5 Workflow lint and generated documents

```sh
actionlint .github/workflows/ci.yml               # only when the workflow changed
python3 tools/gen_dictionary_doc.py               # rewrites docs/05 - commit it with the code change
git diff --stat docs/05_Telemetry_and_Command_Dictionary.md
```

### 6.6 The gate before a commit

| Check | Command | Must show |
|---|---|---|
| Host tests, strict | 6.1 | `HOST TEST PASSED (0 failures)` |
| Host tests, ASan + UBSan | 6.1 | the same, no sanitizer report |
| Firmware check | `bash tools/host_test/check_firmware.sh` | exit 0, no `FAIL` line |
| Station harness | 6.2, both `SAN` values | exit 0 |
| Ground tests | `python -m pytest -q` on 3.11 and 3.13 | all passed (optional skips only) |
| Lint | `ruff check .`, `mypy` (two runs), `shellcheck` (the scripts of the CI `lint` job) | no findings |
| Real builds | 6.4 (CI does it if you cannot) | exit 0, no warning in our code, bundled libraries named in the log |
| Generated docs | `python3 tools/gen_dictionary_doc.py` | no unexpected diff |

---

## 7 Continuous integration

[../.github/workflows/ci.yml](../.github/workflows/ci.yml) runs on every push, pull request and
manual dispatch; every action is pinned to a full commit hash.

| Job | What it runs |
|---|---|
| `lint` | Python 3.13: `pip install -r requirements-dev.txt`; `ruff check .`; `mypy` and `mypy dssq-groundstation/python/main.py`; `shellcheck` on the four shell scripts (`check_firmware.sh`, the two harness `run.sh`, `install_station.sh`); every tracked text file has LF line ends, a final newline and no trailing white space |
| `host-tests` | the official CCSDS 121.0 test data (libaec at a pinned commit); host tests strict (`-Werror`) and under ASan + UBSan; fetch of both bundled libraries into both sketches; `check_firmware.sh`; the station harness under ASan + UBSan and under ThreadSanitizer |
| `ground-tests` | `python -m pytest -q` from the repository root on Python 3.11 and 3.13 |
| `arduino-build` | arduino-cli 1.5.1 + `arduino:zephyr`; fetch of the bundled libraries; four real builds (station and flight, default radio and SX126x / SX1268) with every warning on and none allowed in our code; the logs must name the bundled Arduino_RouterBridge 0.4.3 and RadioLib 7.8.1; the RouterBridge host test (second run under ASan + UBSan) with its stock-tag negative control; the bridge-thread stack bound for both sketches |

A red CI run is fixed before anything else is merged. Details of the bundled-library checks:
[22_Bundled_Libraries.md](22_Bundled_Libraries.md) sections 7-8.

---

## 8 Debugging aids

| Aid | How |
|---|---|
| Run the ground without hardware | `cd dssq-groundstation/python && python3 -m dssq.gds --config config/station.toml --sim` (software spacecraft; `--sim-loss`), dashboard on http://127.0.0.1:8080; in App Lab set `DSSQ_SIM=1` |
| Decode a recording | `cd dssq-groundstation/python && python -m dssq.replay --app-data ../data --cadu-file ../data/archive/YYYYMMDD/cadu.bin --packets --values` (`--app-data`: the ground App's `data/station.toml` and key `data/sdls_key.hex`, as the GDS uses them; a spacecraft EGSE log `cadu_YYYYMMDD.bin` copied from the UNO Q's `<App>/data/` too; [18_Data_Products.md](18_Data_Products.md) section 13) |
| Command over the "umbilical" | `python -m dssq.cltu "NOOP" --app-data <ground App>/data` (the station's key and SN file, shared with the running GDS; without `--app-data` the paths of `config/station.toml` relative to the working directory) -> append the hex line to `<spacecraft App>/data/hardline_queue.txt` on the UNO Q. With a key and TC AUTH or AE the SN file `<archive>/sdls_sn.txt` must exist: until the GDS has received an HK with that key sent at least 120 s after the spacecraft booted (or the file was written by hand, [17_Security_Model.md](17_Security_Model.md) section 7.6) the tool refuses with `TC uplink BLOCKED: ...` and exit status 1; it also refuses a sequence number above the time floor (4 per second since 2026-01-01) |
| E22 module registers | `python -m dssq.radio.e22 --port /dev/ttyUSB0 --config config/station.toml` (module in CONFIG mode; `--write` saves the project configuration) |
| Flight debug text | `#define VGQ_DEBUG_MONITOR 1` (bench only: `Monitor` writes can block the loop) |
| EGSE view | the App Lab console of the UNO Q App (status board every 10 s; event and status logs in `data/`) |
| Products | `python -m dssq.products xtce > vgq1.xtce.xml`; `python -m dssq.products tdm ...` |
| Link budget | `python3 tools/link_budget.py budget --config ... --distance-km 10` |

---

## 9 Release and packaging (`tools/package_apps.py`)

A release is the two App Lab zips in `dist/`, built from a committed tree:

| File | App folder inside | Board |
|---|---|---|
| `dist/vgq1-spacecraft-unoq.zip` | `vgq1-spacecraft` | Arduino UNO Q |
| `dist/dssq-groundstation-ventunoq.zip` | `dssq-groundstation` | Arduino VENTUNO Q |
| `dist/SHA256SUMS` | - | check with `cd dist && sha256sum -c SHA256SUMS` |

What goes in: the files git tracks under each App folder (untracked files never enter), minus
hidden files, caches, tests, `data/` and other run-time folders; plus
`sketch/libraries/RadioLib` and `sketch/libraries/Arduino_RouterBridge` (patched copies with
`PROVENANCE.txt`, fetched and verified like CI, or taken from `--libs-from DIR`, which must hold
`DIR/RadioLib` and `DIR/Arduino_RouterBridge` written by the fetch tools; either way the packaged
octets pass the offline `check` first). Refused: a `requirements.txt`, tracked files under
`sketch/libraries/`, symbolic links, submodules. **Secrets:** the build is refused (exit 4,
nothing written) when a file or folder named like `*sdls_key*`, `http_token*`, `*.key` or `*.pem`
(any case) exists anywhere under an App folder on disk, when a packaged file holds key material, or when the
packaged `station.toml` sets `[gds] http_token` or `[commanding] auth_token`.

Procedure:

1. All checks of section 6.6 green; CI green on the commit to be released.
2. Commit everything: the zips hold the working-tree contents of tracked files, and the tool
   prints a note when files under the App folders differ from `HEAD`.
3. Move any key or token files out of the App folders (for example the keys written by
   `gen_sdls_key.py` without options: `vgq1-spacecraft/sketch/src/sdls_key.h` and
   `dssq-groundstation/data/sdls_key.hex`, or a `data/http_token` left by a local run). Keep
   them safe: they are the link keys, one pair made once
   ([17_Security_Model.md](17_Security_Model.md) section 7.1).
4. Build:
   ```sh
   python3 tools/package_apps.py                     # fetches the libraries from GitHub
   python3 tools/package_apps.py --libs-from /tmp/libs   # offline: copies written by the fetch tools
   ```
   Check: the output lists both zips with their contents summary and "wrote ... and SHA256SUMS".
   `--out DIR` writes (or, with `--verify`, checks) another folder instead of `dist/`.
5. Verify (also on another machine, or later):
   ```sh
   python3 tools/package_apps.py --verify
   python3 tools/package_apps.py --verify --build    # + real compile of each unpacked sketch
   ```
   `--verify` re-checks layout, entry order, timestamps, attributes, CRCs, `app.yaml`,
   `sketch.yaml`, both libraries (offline `check`), the ground configuration, that every Python
   file compiles, and `SHA256SUMS`. `--build` compiles each unpacked sketch with arduino-cli (or
   the wrapper given by `--compiler` / `$VGQ_ACOMPILE`) and requires the bundled
   Arduino_RouterBridge in the compile log.
6. Reproducibility: every timestamp is `SOURCE_DATE_EPOCH` when set, else the committer date of the
   newest commit that touched a packaging input; entries are sorted, attributes normalised. Two
   builds of the same commit give identical files (same zlib; the tool prints its version):
   build twice and compare `sha256sum dist/*.zip`.
7. Commit `dist/` (the zips and `SHA256SUMS`) together with the change log entry in
   [../CHANGELOG.md](../CHANGELOG.md).
8. Hardware acceptance: import both zips in App Lab (My Apps -> open any App -> the menu next to
   its name -> Import App), generate the key after the import
   ([17_Security_Model.md](17_Security_Model.md) section 7.1) and run the first-contact procedure
   of [00_Beginner_Setup_Guide.md](00_Beginner_Setup_Guide.md).

Exit status of the tool: 0 success; 1 a check failed; 2 usage or environment error (not a git
work tree, git missing, no compiler for `--build`, bad `SOURCE_DATE_EPOCH`); 3 a library could not
be fetched; 4 refused because of secret material.

---

## 10 Updating a bundled library

Follow [22_Bundled_Libraries.md](22_Bundled_Libraries.md) section 10 (read the release, regenerate
the patch, update the pins and every version string, re-audit, run every check, hardware
bring-up, new zips).
