# Changelog

All notable changes to the VGQ-1 spacecraft / DSS-Q1 ground station project. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The project has no release tags: the
software identifies itself as flight software 3.0 (boot event 0x0001) and ground package
`dssq` 3.0.0, so the sections below are grouped by development phase, newest first, with the
commit that introduced each change. Verification numbers are those reported in the commit
messages; the current numbers are in [docs/08_Verification_and_Test.md](docs/08_Verification_and_Test.md).

Bench runs so far: the flight App on the UNO Q (2026-10-12) and the first downlink received by the
ground station (2026-10-13), [docs/21_Test_Records.md](docs/21_Test_Records.md) records 2026-10-12-A and
2026-10-13-A. Commanding has not been run on hardware yet ([docs/20_Known_Limitations.md](docs/20_Known_Limitations.md)).

---

## [Unreleased] - First downlink to the ground station; E15-USB-T2 ground radio (2026-10-13)

The ground station on the owner's VENTUNO Q received and decoded the spacecraft's downlink on the
bench (record 2026-10-13-A): AOS within seconds, `lost=0`, no Reed-Solomon corrections, the
SAFE beacon decoded. The ground radio is an E22-400T33D on EBYTE's E15-USB-T2 USB test board,
which the owner keeps; the defaults follow it.

### Changed
- `[gds] rcu` defaults to `false` (`dssq/config.py`, `config/station.toml`; D-68): the E22 sits on
  the E15-USB-T2, whose jumper caps hold M0/M1 (both fitted = normal mode). With `true` and no
  Radio Control Unit wired, every start reported `ERROR RADIO ground E22-400T33D did not answer
  the configuration read`. The RCU (`rcu = true`, D2/D3/D4) remains the option for `RADIO`,
  `GSCAN`, `WAKE`, `LINKRATE`, `LINKCHAN`. Existing `data/station.toml` files keep their value.
- Setup guide part 9 rewritten for the E15-USB-T2: supply jumper on 5V; a fitted cap = 0, a
  removed cap = 1 (EBYTE E51-TTL-500 datasheet, which describes the board); the E22's
  configuration mode is the M0 cap only, both removed is its deep sleep; the RCU as option 9.3.
  Part 7: the callsign rule (keep `N0CALL` until licensed). Part 11: a step attenuator in line,
  and the sign of an overdriven receiver.

### Fixed
- The TDM warning named the station clock for every rejected record. On the bench an overdriven
  E22 gave RSSI -256 dBm with a correct clock and the console said `station clock outside
  2000..2100 (NTP not synchronised?)`. It now names the clock only when the time is out of range,
  otherwise `a measurement outside the TDM limits` with the field (`dssq/gds.py _tdm_add`; new test
  `test_tdm_rejection_names_the_field_not_the_clock`).
- docs/20: the 64-octet ring item added on 2026-10-12 reused the number V-30; it is V-68 now.

### Added
- docs/21 record 2026-10-13-A (first downlink: the E15-USB-T2 jumpers, the one-time
  configuration, RSSI -1 / -256 dBm with both antennas on one bench, -39 / -40 dBm with 54 dB in
  line). docs/20: V-31 closed (the App container opens `/dev/ttyUSB0`); new V-69 (ground radio
  powered from USB, transmit current) and V-70 (the E22 RSSI byte at the end of its range).
  docs/14: rows for the configuration-read error, `no reply` from the one-time configuration,
  the callsign refusal, the overdriven receiver, the empty spectrometer page in SAFE and the
  start-up EVRs not forwarded. docs/03 section 5.3.0; FAQ: the USB-UART adapter, the empty
  spectrometer page, the callsign before a licence.
- The first hardline command and the first spectrum at the ground (record 2026-10-13-A,
  observations 9-12). docs/07 section 2.6: a concrete hardline line and the expected console
  lines (typing the hex inside `< >` makes the shell fail). docs/14: rows for a dashboard that
  stops updating after App Lab is switched to the other board (`curl .../api/state`, Run at
  startup on both boards), the dashboard address (`http://<board name>.local:8080`) and the
  hardline shell error. docs/00 part 5: the dashboard by board name.

## [Unreleased] - UNO Q flight App on arduino:zephyr before 1.0.0 (2026-10-12)

The first import of the flight App on a real UNO Q (App Lab building with arduino:zephyr up to
0.90.0, Zephyr SDK 0.16.8 toolchain) stopped at the link, and once that was fixed, the EGSE
stopped at its first import. All three faults were reproduced (0.90.0 built from source; the
app-bricks-py release history) and are fixed; CI now builds the flight sketch on that core too.

### Fixed
- Link error `undefined reference to '__errno'` from `libm_nano.a` (`expf`, `log10f`, `powf`):
  cores before 1.0.0 link the sketch with newlib-nano's libm, whose errno-setting wrappers need
  `__errno`, which neither the sketch link nor the loader provides. New
  `vgq1-spacecraft/sketch/src/libc_compat.cpp` defines it (weak, never read; unused and dropped
  from the build on 1.0.0, whose picolibc libm does not need it).
- The sketch would then have linked but not loaded on those cores: `k_current_get()` (Router
  Bridge mailbox, `src/fsw/rpc_mailbox.h`) left a weak reference to the C++ thread-local init
  function `_ZTH13z_tls_current`, which the Zephyr 4.2 loader of 0.90.0 does not export and
  therefore refuses (1.0.0 compiles with `-fno-extern-tls-init`). The mailbox now asks the
  scheduler (`k_sched_current_thread_query()`, exported by every release).
- EGSE start: `cannot import name 'AppError' from 'arduino.app_utils'`, exit code 2.
  `arduino.app_utils.AppError` exists only from app-bricks-py 0.13.0 (commit 9a95825,
  2026-08-13); the EGSE imported it unconditionally. It now treats it as an optional "no answer"
  error, as the ground's `BridgeLink.from_app_utils` already did. The App Lab test of
  `test_cfdp.py` runs with and without `AppError` (fails on the previous `main.py`).

- Notifications and the E22 radio on the 64-octet Serial rings of arduino:zephyr before 1.0.0
  (core commit `40abc1f7` raised them to 1024; the stable App Lab builds the UNO Q with 0.90.0).
  On the owner's board the EGSE's calls worked but no notification ever arrived:
  `notify_room()` needed the payload + 48 octets free in a 64-octet ring. And
  `ZephyrSerial::write()` would have blocked ~180 ms per 240-octet E22 frame, with the 64-octet
  RX ring holding only 67 ms of radio data. D-66: with small rings (`config.h
  VGQ_SMALL_SERIAL_RINGS`, from the core's `CONFIG_ARDUINO_API_SERIAL_BUFFER_SIZE`) the E22
  port goes through a UART pump thread (`drivers/uart_pump.h`, priority 6, every 1 ms, 1024 /
  512-octet FIFOs, `drivers/byte_fifo.h`), and a notification longer than the ring goes out
  only into an empty ring (at most ~45 ms at 115200 bit/s). With 1024-octet rings nothing
  changes. New host test `test_byte_fifo.cpp` (11 checks, incl. two threads, also under
  ThreadSanitizer); `check_firmware.sh` checks the small-ring path (70 ok); pump thread
  stack 428 of 1024 octets. Confirmed on the owner's UNO Q (record 2026-10-12-A).
- False I2C bus fault with nothing on A4/A5 (the AS7265X on Qwiic): with no pull-up the empty
  bus read low and every re-probe of the absent instruments logged EVR 0x060A `I2C bus clear:
  SDA or SCL still low after 9 clocks` (owner's board, record 2026-10-12-A). D-67: the bus
  clear releases the lines with the MCU's weak pull-ups (`INPUT_PULLUP`, about 40 kOhm), so an
  empty bus reads high while a target holding a line low is still found
  (`drivers/i2c_bus.cpp`). Confirmed on the owner's board (second run of record 2026-10-12-A).

### Added
- [docs/21](docs/21_Test_Records.md) record 2026-10-12-A, the first bench run of the flight App
  (spacecraft alone): build and start on 0.90.0, radio configuration, beacon, AS7265X on Qwiic,
  CADU copies over the router, no reset with the radio on the board's 5 V. Its numbers are
  added to docs/20 V-19 (busy 1528 ms against the model's 1267 ms), V-22, V-27 and V-68 (first numbered V-30, a number already taken), and
  the setup guide's expected `busy` / `period` now use the measured values.
- The AS7265X may be plugged into the UNO Q Qwiic socket (`Wire1`, `config.h` `QWIIC_WIRE`) instead
  of A4/A5, on the owner's request (D-13 revision). The driver probes A4/A5 first, then Qwiic, at
  boot and at every re-probe and stays on the bus that answers; EVR 0x0706 names it (`on A4/A5` /
  `on Qwiic`). The Qwiic bus counts into HK `i2c_errors` and FDIR `I2C_BUS` (re-initialisation;
  EVR 0x0605 `Qwiic I2C bus fault ...`). The boot wait for the AS7265X is now bounded by time,
  not by a probe count. On Qwiic the A4/A5 bus needs its own pull-ups (docs/03 section 4.7).
- `tools/llext_check.py`: every undefined symbol of a sketch's final ELF must be exported by the
  board's loader (`syms-dynamic.ld`) or also be undefined in a baseline sketch of the same core.
  CI runs it for both sketches and both radio options, and builds the flight sketch again on
  `arduino:zephyr` 0.90.0 with the same check.
- [docs/14](docs/14_Troubleshooting_and_Event_Response.md) section 2.10: App Lab import and start
  problems (`Invalid library directory in sketch project: libraries/RadioLib`, the link error
  above, a sketch the loader refuses, the OpenOCD messages when flashing the VENTUNO Q).

### Changed
- `dist/vgq1-spacecraft-unoq.zip` rebuilt (the station package is unchanged; the ground App
  already ran on the owner's VENTUNO Q, whose App Lab is newer). Flight sizes on
  1.0.0: 232860 octets flash, 128396 RAM (+44 / -16); on 0.90.0: 222148 / 120913
  ([docs/01](docs/01_System_Design.md) section 9.1). Bridge-thread worst-case stack unchanged
  (1084 octets).

---

## [Unreleased] - Phase D: repository layout, packaging, final fixes, documentation (2026-10-12)

### Changed
- The repository **is** the two Arduino App Lab Apps: `vgq1-spacecraft/` (UNO Q: flight sketch +
  Linux EGSE) and `dssq-groundstation/` (VENTUNO Q: station sketch + ground data system, host-mode
  files in `host/`), plus `nicla_sense_me_aru/`, `tools/`, `tests/` (the test suite, run from the
  repository root) and `docs/`. Every path in code, tests, tools and CI follows the new layout.
- The repository was renamed to `Tanay7/vgq1-deep-space-link`.
- `requirements-dev.txt` at the root lists the PC test and lint tools only; the boards need no
  pip package.
- Transmit duty (RF `tx_duty`) is averaged over whole transmission cycles (`TxDutyMeter`: at most
  16 cycles launched within 300 s; the open cycle counts as at least the period in force, so the
  value decays to 0 while the transmitter is silent) instead of a fixed 60 s window, which read
  28.8-57.7 % for a true 50 % at SF12 and raised false YELLOW alarms. New host test file
  `test_tx_duty.cpp` (4 groups, 17 checks).
- `RSTCNT` also clears the event-latched FDIR flags CMD_LOSS, LOOP_OVERRUN, RATE_REVERT and
  SDLS_AUTH (`kFdirClearedByRstcnt`; dictionary and simulator follow); flags that follow a state
  are left alone.
- Ground details: the SX126x backend refuses an `rf_rx` payload above 236 octets
  (`RF_RX_MAX_OCTETS`); the `rf_cfg` result -2 ("MCU busy") is documented and a queued result (2)
  counts as success; the dashboard labels the COP-1 field "SDLS TC service / last SN used"; the
  simulator truncates `last_airtime` like the flight (1267 ms reads 1.2 s); `gen_sdls_key.py`
  says to run it once and copy each file to its board.

### Added
- Hardware watchdog on the flight MCU: the independent watchdog (IWDG) is armed at the end of
  `setup()` with a 40 s timeout and refreshed once per `loop()` pass on the loop thread
  (`VGQ_IWDG`, `kIwdgTimeoutMs`); the register values are computed by `iwdg_settings()` and
  host-tested; EVR 0x060C if the settings are not confirmed at start-up. The platform has no
  Zephyr watchdog driver, so the registers are written directly. Not yet provoked on hardware
  (docs/20 L-38, V-28; test T-FD-09).
- Ground station identification: `[station] callsign` (default placeholder `N0CALL`, validated,
  WARN at start while unchanged) and `StationIdent`, which transmits `DE <callsign> <name>` in
  plain ASCII on the uplink after the first transmission of a run, at least every 10 minutes
  while transmitting and at the end of a session; the spacecraft counts it in RF `cltu_bad` and
  ignores it. Snapshot `link.station_id`, a "Station identification" row in the uplink table,
  `test_station_id.cpp` (2 groups, 12 checks) and `tests/test_station_id.py`.
- `tools/package_apps.py`: builds and verifies the two reproducible App Lab zips in `dist/`
  (with `SHA256SUMS`), bundling the patched libraries and refusing any key file, key material or
  `requirements.txt`; 71 packaging tests. A `README.md` in each App folder.
- `pyproject.toml` with the pytest, ruff and mypy settings, and a CI `lint` job (ruff, mypy,
  shellcheck, text-file hygiene).
- The station sketch regression harness (`tools/host_test/station`, 26 checks) and the
  RouterBridge harness with the bridge-thread stack bound (`tools/host_test/routerbridge`),
  both run by CI; CI also builds both sketches with the SX126x option and fails on any warning
  in this repository's code.
- `tests/test_citations.py`: every citation tag in the code has a row in docs/12 appendix B, and
  every row is still cited.
- `python -m dssq.replay --app-data DIR`: the replay reads the ground App's `station.toml` and
  `sdls_key.hex` from its data folder, with the same path rules as `dssq.gds` and `dssq.cltu`
  (one shared `config.load_tool_config`); test
  `test_replay_app_data_finds_the_key_and_station_toml`.
- The complete documentation set: beginner setup guide, system design, link budget, hardware
  integration, ICD, generated dictionary, displays, operations, verification and test,
  regulatory and safety, references, standards roadmap, design decisions, web UI reference,
  troubleshooting and event response, configuration reference, developer guide, security
  model, data products, glossary, known limitations, test record templates, bundled libraries,
  FAQ and a one-page project summary. A final consistency pass checked every document against
  the final code (numbers, paths, configuration keys, events, cross-links).

### Fixed
- Uplink window plan (`UplinkWindowPlan` in `dssq/gds.py`): everything the station radiates after
  a received frame - the station identification first when it is due, then external TC frames
  and FOP-1 CLTUs while their air time fits - stays inside the shortest quiet window of any mode
  less 0.2 s. A CLTU that does not fit waits for the next window and is not built, so no SDLS
  sequence number is spent on it. Before, two long CLTUs plus the identification could overrun
  the window at the SF12 profiles and cost the next downlink frame. A `WAKE` and the
  identification during LOS wait until the window's planned transmissions are over. Tests in
  `tests/test_station_id.py`.
- The window plan counts elapsed time: a window's transmissions start no earlier than the
  frame's arrival time + 0.1 s (`WINDOW_REACTION_S`), the current time and the end of the
  previous window's transmissions, and must end by arrival + 0.1 s + budget, so a frame handled
  late (a stalled radio thread) or in a batch never overruns the quiet window, and a frame
  whose quiet window has passed books nothing
  (`test_late_or_batched_frames_never_overrun_the_quiet_window`).
- Station identification interval with lost frames: the periodic identification now falls due
  max(60 s, 3 frame periods) early (at most 600 s; the reported RF `frame_period_ms` counts
  when it is longer) instead of max(60 s, one frame period), so a window still carries it when
  two downlink frames are lost; if no window has carried it 600 s - 2 s - its air time after
  the last one, the housekeeping sends it without a window (`_id_overdue()`; that one may
  collide with one downlink frame, docs/20 L-28)
  (`test_periodic_id_keeps_10_minutes_when_frames_are_lost`).
- Shutdown: `Runner.close()` takes the outbox first, and an identification queued but not yet
  written (for example the LOS identification after a `WAKE`) is the first shutdown step;
  nothing else left in the outbox is radiated
  (`test_runner_close_sends_an_id_left_in_the_outbox_first`). With the SX126x station an
  identification still last in the bridge's backlog is flushed instead of repeated
  (`Sx126xBridge.queued_last()`), one with uplinks queued behind it gets a new closing
  identification after them, and the closing identification counts as sent only once the
  station MCU has accepted it, waiting at most 0.6 s (`IDENT_FLUSH_S`); otherwise ERROR
  `closing identification NOT radiated ...`
  (`test_closing_id_waits_for_a_free_mcu_slot_or_reports_it_was_not_sent`,
  `test_closing_id_goes_after_the_uplinks_queued_behind_an_earlier_id`).
- The window's busy deadline, the `WAKE` / `GSCAN` gate and the `GSCAN` wait run on a monotonic
  clock (`SteadyClock`: follows forward steps of the wall clock, ignores backward ones), so a
  clock stepped back by NTP no longer holds the LOS identification and the radio jobs; LOS
  detection stays on the wall clock
  (`test_a_wall_clock_step_back_does_not_hold_the_los_id_or_the_radio_jobs`). The whole window
  plan uses that one time base: the frame's ERT is converted once (now - max(0, wall clock -
  ERT stamp)) and the busy deadline is the plan's start + the air time it used, so a clock step
  between the ERT stamp and the window moves nothing
  (`test_a_clock_step_between_the_ert_stamp_and_the_window_holds_nothing`).
- Ground RF survey (`GSCAN`) with the SX126x station: it returns to the GDS's operational
  channel even when a channel change is only queued on the MCU, a refused return raises an
  ERROR and the station configuration is re-sent until the MCU accepts it, and the survey waits
  for the window's uplinks and for the station transmitter to be idle (at most max(60 s, 2
  frame periods), then it is refused with a WARN) instead of retuning under queued uplinks
  (`tests/test_radio.py`). After a survey a TX gate holds every uplink (window transmissions
  only fill the backlog, `WAKE` waits, `radio.sx126x.tx_hold` gives the reason) until a status
  line shows the station channel with no change pending, so nothing is radiated on a surveyed
  channel after a failed return (`test_after_a_failed_return_nothing_is_radiated_off_channel`,
  `test_a_wall_clock_step_back_cannot_prolong_the_hold_after_a_survey`).
- Flight transmit duty across a radio reconfiguration: the next frame waits the period owed to
  the last frame sent at the old settings (`reconfig_hold_ms()`, `launch_spacing_ms()` in
  `fsw/datalink.h`), so a change from a slow to a fast setting can no longer key the PA almost
  back to back (SX126x profile 0 -> 1 in ENCOUNTER: 99.9 % for one cycle); host test
  `tx_duty_across_a_radio_reconfiguration`.
- The simulator's RF `last_airtime` is 0 at boot and after a radio reconfiguration until the
  next transmission has ended, as on the flight.

### Security
- Unknown TC sequence number: when `sdls_sn.txt` is missing (for a key used with TC AUTH or
  AE) or corrupt, the station holds the uplink - the GDS with or without an archive, and
  `dssq.cltu`, which refuses with exit status 1 and creates no file (the old "created fresh"
  warning is gone). The next HK reporting `sdls_last_sn` (AE-protected, or any HK at a station
  whose TM service is NONE; never one made within 120 s of a flight boot that reports 0,
  `SDLS_SN_RESTORE_WAIT_S`, whose value predates the EGSE's restore - WARN at most once a
  minute while it waits; never an unprotected report above the time floor, which is refused
  with an ERROR, so a forged HK can only delay a release) re-synchronises the
  counter onto the time floor and releases the uplink: max(file, `sn_time_floor()`), the floor
  being 4 numbers per second of station clock since 2026-01-01T00:00:00Z (`SDLS_SN_TIME_RATE`,
  `SDLS_SN_TIME_T0`; about 98 million in October 2026). The floor does not depend on the
  report, so a replayed recording after an archive loss, the boot HK before the restore or a
  forged HK at a TM NONE station can no longer bring the counter back onto numbers already used
  (with `sdls_last_sn` + 1 000 000 alone they did). No gap is added on top of the floor;
  `sdls_last_sn` + 1 000 000 (`SDLS_SN_RESYNC_GAP`) remains only as the fallback for a station
  clock before 2026 or a report above the floor, each with a WARNING in the event. A target
  above 4 227 858 432 (`SDLS_SN_RELEASE_MAX` = 2^32 - 2^26; the floor itself from 2059-06-30)
  is refused and the uplink stays held. A new key starts on the floor of its first release
  (docs/17 section 7.6, docs/12 D-65). Tests
  `test_existing_key_without_sn_file_holds_and_resyncs_above_the_lost_tail`,
  `test_every_numbered_tc_service_holds_without_its_sn_file`,
  `test_a_replayed_first_pass_hk_after_archive_loss_cannot_reuse`,
  `test_a_forged_hk_under_tm_none_cannot_reuse`,
  `test_the_boot_hk_before_the_restore_never_ends_the_hold`,
  `test_hardline_uses_the_app_copy_and_refuses_without_an_sn_file`,
  `test_a_replayed_or_forged_hk_cannot_cause_reuse`, `test_resync_fallbacks_and_limits`.
- Rate gates that keep the station below the time floor's 4 numbers per second (at most
  4 / 1.4 + 1 / 10 = 2.96 per second): uplink windows open at most once per 1.4 s of frame
  ERT (`WINDOW_MIN_SPACING_S`; flight frames are at least 1.5 s apart, so a replayed or forged
  frame stream cannot open more), with at most 4 TC frames each; a `WAKE` runs at most once per
  10 s (`WAKE_MIN_INTERVAL_S`); `dssq.cltu` refuses a number above the floor (`SDLS: sequence
  number <n> would be above the time bound <f> ...`, exit status 1)
  (`test_sdls_numbers_stay_below_3_per_second_whatever_the_downlink_does`,
  `test_wake_at_most_once_per_interval`, `test_hardline_never_outruns_the_time_floor`).
- The software simulator (`--sim`, App Lab `DSSQ_SIM=1`, with or without an archive) keeps its
  SDLS TC counter and TM replay state in memory and never touches the real files, so its
  spacecraft's HK can neither end the real station's hold nor write the real counter
  (`test_the_simulator_never_touches_the_real_counter`).
- Without a key (or with TC NONE) a corrupt `sdls_sn.txt` blocks nothing: no ERROR at start, no
  YELLOW `TC uplink BLOCKED`, and HKs leave the file alone; `tc_sn_state` reads BLOCKED only
  while the uplink is really held (`test_no_key_and_a_corrupt_sn_file_block_nothing`).
- One TC sequence-number allocator for every sender (the GDS and `dssq.cltu`): under the file
  lock it re-reads `sdls_sn.txt`, takes max(file, memory) + 1 and writes it before the frame is
  built; `observe_spacecraft_sn()` re-reads the file first also while the uplink is held, so
  nothing ever writes a lower value (`test_held_sender_never_writes_below_the_file`).
- `--no-archive` keeps the SDLS security counters (`sdls_sn.txt`, `sdls_tm_replay.txt`) in
  `<archive_dir>`: they are security state, not archive data; only the software simulator
  keeps them in memory (`test_no_archive_keeps_and_uses_the_sn_file`).
- The UDP telecommand listener `[interop] tc_frames_udp_listen` (no authentication, no hazardous
  CONFIRM) accepts only a loopback address (`127.x.y.z:port` or `[::1]:port`); host names,
  `0.0.0.0`, `[::]` and LAN addresses are refused at start, and the ground data system checks
  again before it opens the socket. Test `test_udp_tc_listener_is_loopback_only`.

### Removed
- The temporary design records `docs/WIP_*.md` (the v3 contract, the Phase B specification and
  the web UI draft); their content is folded into the final documentation, and the citation tags
  that code comments use ("contract N", "PB-x.y") are indexed in
  [docs/12_Design_Decisions.md](docs/12_Design_Decisions.md) appendix B.

### Verification after the final fixes
- Host tests 964 checks in 89 groups (963 without the official CCSDS 121.0 data), strict and
  under ASan + UBSan; check_firmware.sh 64 ok; station harness 26 / 26 (plain, ASan + UBSan,
  TSan); pytest 749 tests, 746 passed, 3 skipped, on Python 3.13 and 3.11; ruff, mypy and
  actionlint clean; real UNO Q and VENTUNO Q builds with 0 warnings in our code (sizes in
  [docs/08_Verification_and_Test.md](docs/08_Verification_and_Test.md) section 2.6).

---

## [3.0.0] - 2026-10-11 - v3

### Phase C - adversarial review and hardening

#### Security
- The spacecraft EGSE offers every SDLS TM epoch value at most once ever (persisted before the
  call, retired on any unanswered call): interleaving tests find no nonce reuse; the previous
  code reused a nonce in 255 of 300 runs. (fbf16cf)
- Ground: a change of SDLS status on a virtual channel is a discontinuity, so octets of a forged
  unprotected frame can never join a protected packet; the per-packet protected flag also gates
  the TC sequence-number observation. (fbf16cf)
- The SDLS TC anti-replay counter is held on the spacecraft until the EGSE restores it; the
  ground's SN file is never lowered and is lock-protected. (fbf16cf)

#### Fixed
- 62 confirmed findings of eighteen read-only reviewers (3 critical, 25 major, 34 minor; each
  finding challenged by one or two independent skeptics; two split verdicts handled as test
  gaps), with a regression test wherever feasible. Selection: Arduino_RouterBridge 0.4.3 runs
  `provide_safe` handlers on its own 500-octet-stack thread, so both sketches now post every RPC
  to a single-slot mailbox served by `loop()`; COP-1 FOP lockout / suspension / N(R) validation
  per CCSDS 232.1; First Header Pointer consistency check; CMDVER command source in stage bits
  7..6; E22 busy time never shorter than the modelled air time; a power-on is no longer reported
  as a brown-out (backup-register marker); REBOOT executes without a transmitted frame; sensors
  re-probed after boot; link-manager FER over 10 minutes excluding the LOS gap; archive errors
  never stop reception; web-server slot, deadline and Host checks. Verified: host tests strict and
  under ASan/UBSan, check_firmware.sh 62 ok, ground pytest 581 passed on Python 3.13 and 3.11,
  real builds with 0 warnings in our code (flight E22 231 544 B flash / 127 748 B RAM, flight
  SX1268 324 840 B, station E22 100 516 B, station SX1268 207 376 B). (fbf16cf)

#### Added
- Receiver data logger: a 1 Hz `rx_stats` table (RSSI, SNR, noise floor, margin, frequency
  error, 10-minute FER, RS corrections, AFC, Doppler, AOS/LOS), CSV export from the command line
  and the FILES tab, retention by age and size, and a free-space guard that pauses raw logging
  with a WARN and a YELLOW alarm. Ground pytest 598 passed (17 new tests). (7f0d36c)
- Arduino_RouterBridge 0.4.3 bundled with both sketches with a two-hunk patch: the bridge thread
  stack raised from 500 to 1536 octets (measured worst case 1084 octets) and `update()` never
  runs `__safe__` methods. `tools/libbundle.py` became the shared fetch / verify / patch /
  provenance engine of `tools/radiolib.py` and the new `tools/routerbridge.py`. Verified:
  check_firmware.sh 64 ok, pytest 598, the compile logs name the bundled copy, flight 231 856 B,
  station 100 828 B. (5b06a04)

### Phase B - integration

#### Added
- The v3 flight software on the UNO Q: HK grown to 78 octets, the 32-opcode command table, a
  downlink scheduler and TC accept chain (TC / USLP x NONE / AUTH / AE, replay and bad-MAC
  rejection), VC 3 for CFDP, SSR export, the channel-major Rice-compressed SPECBURST; the flight
  executive on the radio abstraction; sensors on `Wire2` (A4 / A5); I2C bus recovery, RM3100
  self-test, AS7265X burst; the v3 ground dictionary, products and data system. Verified:
  check_firmware.sh 42 ok, pytest 423, real flight build 226 740 B flash / 124 760 B RAM on core
  main and 1.0.0. (a90e7e7)
- The UNO Q Linux-side EGSE: CADU archive, SCLK partition / SDLS TM epoch from a persisted boot
  counter, CFDP relay of files to the MCU, no `requirements.txt`. 19 new EGSE tests, pytest 444.
  (02c273a)
- Radio model without code edits: a jumper from header A0 to GND selects the E22-400T37S at boot
  (majority of five reads), open keeps the E22-400T33D; EVR 0x0308 reports it. RS rescue on the
  SX126x path (CRC-failed packets still go to the Reed-Solomon decoder). CI compiles the flight
  sketch too. Station annunciator priorities and beacon chirps. Host tests 854 ok,
  check_firmware.sh 62 ok, pytest 454. (dd1f4c0)
- The v3 web UI: one self-contained `dashboard.html` (no CDN, no web font) with 11 tabs -
  OVERVIEW, RECEIVER, LINK & SECURITY, FRAMES & PACKETS, SPACECRAFT, SPECTROMETER, SCIENCE, MAGCAL,
  SEQUENCES, FILES, COMMANDING - a Content-Security-Policy with the script's hash, token and
  second-operator checks, `/metrics`; the App Lab entry point of the ground App. Headless
  Chromium: all tabs with zero page, console or HTTP errors. pytest 475. (6d4028e)
- RadioLib 7.8.1 bundled with each sketch through `tools/radiolib.py` (tag commit and SHA-256
  verified) and `tools/radiolib-7.8.1.patch` (VENTUNO Q platform entry, `RADIOLIB_STATIC_ONLY`,
  module exclusions); audited SX126x drivers on both ends (packet RSSI, status-checked reads,
  image calibration on every channel change, OCP restored, bounded BUSY waits); check_firmware.sh
  compiles all 80 RadioLib units for both boards plus a negative control. (f0ff5e8)

#### Changed
- The v3 design contract, the Phase B specification and the web UI draft were kept in the
  repository as temporary working copies across a session pause. (90d615d, b58e1a7)

### Phase A - v3 workstreams

#### Added
- Host-test registry (`HOST_TEST()` per topic file) and the `FrameSecurity` seam for SDLS on
  downlink frames. (5480728)
- SDLS authenticated encryption (AES-256-GCM on TC and TM, null-SA fallback, HMAC service kept
  byte-exact), USLP framing (CCSDS 732.1-B-2) selectable next to TM, the CFDP PDU core. (9c7d893)
- CFDP class-1 file delivery: flight sender, ground receiver (complete, incomplete, checksum
  failure, cancelled, abandoned), stdlib-only EGSE sender. (3fc7d51)
- AS7265X driver of our own (non-blocking, every chip feature, five acquisition modes), on-board
  spectral processing (AGC, HDR, lock-in, log encoding, classifier, change detection,
  boresight) and the ground spectral analytics. (bfd7215)
- Radio abstraction: E22-400T33D default, E22-400T37S retained, SX126x option; `dssq/link.py` as
  the single source of radio facts; the T-RF-03 sensitivity analysis in `tools/link_budget.py`.
  (93cf4a3)
- CCSDS 121.0-B-3 lossless compression: flight encoder and ground decoder, bit-exact on the
  official test data (flight 56/56, ground 74/74), differential fuzzing 9000 cases with 0
  mismatches. (1bbd28e)
- On-board autonomy: stored command sequences (32 slots), beacon urgency levels, MAGCAL store,
  RM3100 self-test decode; 209 new host checks. (8ebb2a1)
- Ground products: CCSDS 503.0-B-2 Tracking Data Message, XTCE 1.2 export, magnetometer
  ellipsoid fit, automatic frequency correction for the SX126x. 82 new product / magcal tests and
  11 AFC tests. (a970823)
- CI on every push: host tests strict and under sanitizers with the official CCSDS 121.0 data,
  the ground suite on Python 3.11 and 3.13, the real Arduino build. (0a73319, 83237ac)

#### Changed
- The ground station runs on the Python standard library only: pure-Python AES-256-GCM
  (FIPS-197, SP 800-38D), a termios serial port, `tomllib`. With every optional package blocked:
  167 passed, 2 optional skips. (c8c3b04)
- Flight sensor bus moved to the UNO Q header pins: `Wire2` = I2C3 on A4 / A5; no Qwiic.
  (eaefd3b)
- Nicla firmware: exact BMP390 pressure in Pa (the previous value was 100 times too large) and
  the BMM150 field in 0.1 uT from the BHI260's reported range. (2206fc4)

#### Fixed
- AS7265X driver: virtual-register protocol desynchronisation, LED over-drive (white 0-3, IR
  0-2, UV 0 only) and recovery defects - 20 findings confirmed against the SparkFun library 1.0.5
  and the ams datasheet. (8115f61)

#### Removed
- Nicla Sense Env (owner decision); the placeholder `test.md`. (bfd7215, 6e185cc)

---

## [2.0.0] - 2026-10-09 - v2: first complete system

### Added
- CCSDS flight and ground core: TM packets, VC multiplexing, frames, RS(255,223) dual basis,
  randomizer, ASM; TC CLTU/BCH, frames, FARM-1; SDLS HMAC-SHA-256; E22 driver, sensor drivers,
  FDIR, SSR, TRIAD attitude; the Python CCSDS stack; host harness with bit-exact flight-to-ground
  cross-checks (18 tests). (6c53786)
- Ground station: GDS engine, receiver statistics, web dashboard, software spacecraft
  simulator, Radio Control Unit, link manager, UDP interoperability, Prometheus metrics; RF survey,
  coordinated rate / channel changes, Wake-on-Radio hibernation (28 tests). (0204f8d)
- Replay, link-budget and SDLS-key tools; systemd unit. (b525052)
- Payload: Nicla Sense ME, Nicla Sense Env, AS7265X, RM3100 (one or two); the receiver display on
  the web only. (ae6087d)
- Documentation set 00-11 and the dictionary generator; the station I/O sketch as an App Lab
  App. (611d253, aae17d6)

### Changed
- EBYTE E22-xxxT37S manual v1.5 facts applied (2.4k floor, fixed 37 dBm, abnormal-status
  reports); single-precision statistics and a heap-allocated recorder for the STM32U585. (9a83f1e)
- Hardware facts checked against Arduino's UNO Q / VENTUNO Q documentation (power, Qwiic, 5 V
  tolerance). (e5fe34c)

### Removed
- Dead code on both ends (verified with extra warnings, a symbol cross-reference and vulture).
  (9cd3a1e)
