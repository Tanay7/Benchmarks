# 14 - Troubleshooting and Event Response

Use this document in two ways:

* **Something is wrong and you do not know why:** start at the fault tree (section 2). Each
  row goes from a symptom to the likely cause, the check that proves it and the action.
* **A message appeared:** look it up in the catalogues. Section 3 lists every event report
  (EVR) the flight software can send, section 4 every ground event, section 5 every alarm
  reason, section 6 every fault-protection (FDIR) flag. Each entry says what to do.

The catalogues were extracted from the final code: every `evr(...)` call in
[`vgq1-spacecraft/sketch/src`](../vgq1-spacecraft/sketch/src) (100 call sites, 98 distinct event
ids after expanding the `base + code` families) and every event source and alarm reason in
[`dssq-groundstation/python/dssq`](../dssq-groundstation/python/dssq) (`gds.py`, `datalog.py`,
`linkmgr.py`, `link.py`, `ccsds/cfdp.py`, `ccsds/sdls.py`, `fop.py`, `spectral.py`, `radio/`,
`display/web.py`). Procedures referred to as "07 §n" are in
[07_Operations_Procedures.md](07_Operations_Procedures.md); settings are in
[15_Configuration_Reference.md](15_Configuration_Reference.md); every dashboard field is
explained in [13_Web_UI_Reference.md](13_Web_UI_Reference.md).

---

## 1 Where messages appear and how to read them

| Place | What it shows | Notes |
|---|---|---|
| Dashboard OVERVIEW: **Alarm reasons** | The current RED reasons, then the YELLOW ones (section 5) | Re-computed once a second |
| Dashboard OVERVIEW: **Latest events** | The last 80 ground events | Same lines as `events.log` |
| Dashboard SPACECRAFT: **Spacecraft EVRs** | The last 40 EVRs received, newest first | Severity, id, text, SCET |
| `<archive>/YYYYMMDD/events.log` | Every ground event, one line each: `2026-10-12T01:54:13+00:00 WARN  LIMITS   HK.beacon_level = 3 RED` (UTC time, level, source, text) | Also the `events` table of `gds.sqlite` ([18_Data_Products.md](18_Data_Products.md)) |
| App Lab log of the ground App (or `journalctl -u dssq-gds`) | Every ground event plus start-up errors, tracebacks and the station SX126x radio events | Configuration errors appear only here |
| App Lab log of the spacecraft App (UNO Q EGSE) | EGSE messages (section 4.23) and every EVR the flight software notified | |
| UNO Q `<App>/data/evr_YYYYMMDD.jsonl` | Every EVR the MCU notified to the EGSE, also those not downlinked | Complete bench record ([18_Data_Products.md](18_Data_Products.md) §10) |

**Reading a spacecraft EVR on the ground.** It appears as a ground event with source `S/C EVR`
and the text `[0x0306] radio E22-400T33D OK: ...`. The ground level is INFO for DIAG and INFO
EVRs, WARN for WARN, ERROR for ERROR and FATAL. A suffix `(SSR playback)` means the EVR came
from the on-board recorder (VC2), not in real time.

**Why an EVR may be missing from the downlink** (it is still in the EGSE log):

1. Level filter: only EVRs with severity >= `EVRLEVEL` are downlinked (boot default 1 = INFO,
   so the two DIAG EVRs 0x0203 and 0x0204 are not sent unless you command `EVRLEVEL DIAG`).
2. Routine budget: EVRs with severity below ERROR share a token bucket of 10 EVRs refilled from
   the link capacity left over by the periodic packets, never faster than 10 per minute.
   ERROR and FATAL EVRs, every EVR raised during boot, the SDLS epoch EVRs 0x0120..0x0122 and
   the SPECDIAG lines 0x0703 are never throttled.
3. The notification to the EGSE is limited to 8 per second and 2 per loop pass; the excess is
   counted (`evr_nd` in the EGSE status board).

**Beacon side effects.** Every EVR is seen by the beacon monitor before any filter: any WARN,
ERROR or FATAL EVR raises the spacecraft beacon to at least IMPORTANT, and 0x0701 / 0x0702 to
at least INTERESTING, until the next accepted command (07 §2.4).

---

## 2 Fault tree

### 2.1 Expected indications that are not faults

| You see | Why | Action |
|---|---|---|
| After every spacecraft boot: beacon **URGENT**, RED alarm `beacon URGENT`, LIMITS `HK.beacon_level = 3 RED`, EVENTS `spacecraft beacon URGENT` | The flight software starts in SAFE, and SAFE entered by boot or fault protection is an URGENT condition by design: a reset is an event the operator must acknowledge | Send `MODE CRUISE` (or `MODE SAFE` to stay safe). The command clears the fault-protection SAFE flag and acknowledges the beacon; it drops to NOMINAL with that command (07 §2.4) |
| Only OID frames (identification `DE <callsign> VGQ-1`) for up to 120 s after a spacecraft boot | With SDLS TM AE the data frames are held until the EGSE (UNO Q Linux side) supplies a new epoch | Normal while the EGSE starts. More than 120 s: EVR 0x0121, see row 2.3-3 |
| YELLOW `FDIR CMD_LOSS` / `RATE_REVERT` / `SDLS_AUTH` / `LOOP_OVERRUN` that never clears by itself | These four FDIR flags record an event and no monitor clears them (section 6) | Understand the cause (section 6), then acknowledge with `RSTCNT`: it clears these four flags with the counters (or a reboot) |
| RED `LOS` while a `TXINHIBIT` is in force | The spacecraft is silent on purpose | Wait for the inhibit to end (EVR 0x0603), then AOS |
| `LOS (hibernating)` at INFO level | The spacecraft hibernates and sends one beacon every `beacon_s` | Normal; RED only after 7200 s without a beacon |
| RF `cltu_bad` rises by one shortly after commands, without any 0x01xx EVR; events `IDENT station identification (periodic / end): DE <callsign> <name>` | The station's plain-text identification (section 4.21) reaches the spacecraft receiver; it holds no CLTU, so the flight counts it in `cltu_bad` and drops it | Normal. A `cltu_bad` rise without an identification next to it is a damaged uplink: 2.4 |
| WARN `IDENT [station] callsign is the placeholder N0CALL ...` at every GDS start | `[station] callsign` was not set | Set your licensed callsign ([15](15_Configuration_Reference.md) §4.1) and restart |
| `HK.cmd_loss_timer_s` growing | Seconds since the last valid command; it resets with every accepted command | Command at least once per `CMDLOSS` period (default 24 h) or the spacecraft goes SAFE and returns to the default radio |

### 2.2 Station and dashboard

| Symptom | Likely cause | Check | Action |
|---|---|---|---|
| The ground App stops at once | Invalid `station.toml` value | App Lab log: `configuration <path>: [section] key ...` (exit status 2) | Correct the key named (ranges: [15](15_Configuration_Reference.md) §4) and restart |
| App runs but the dashboard does not load | Port not published on the LAN by App Lab, or wrong address | On the board: `curl http://localhost:8080/api/state`; from the PC: `curl http://<board>:8080/api/state` | If only the board answers: use host (systemd) mode (07 §1.2). Check `[gds] http_port`, firewalls |
| Dashboard loads but every command gives 403 / "read-only" | LAN-exposed server without `[gds] http_token` | EVENTS at start: WARN `WEB dashboard on http://0.0.0.0:8080/ (LAN-exposed WITHOUT [gds] http_token: commanding disabled ...)` | Set `[gds] http_token` (App Lab: use `<App>/data/http_token`) and enter it in the dashboard |
| Dashboard reached by a DNS name shows an error | DNS-rebinding guard: only IP addresses, single-label and `.local` names and the board's host name are accepted | Use the IP address | Add the name to `[gds] http_allowed_hosts` and restart |
| RED `radio backend unavailable: E22-400T33D serial port: ...` | USB-UART missing, wrong `[radio] port`, permission | `ls /dev/serial/by-id/`; the GDS retries every 10 s (INFO `<port> opened` when it works) | Fix the cable / port name; host mode: user in group `dialout` |
| RED `ground radio link down for N s` | The E22 serial link failed after it was open, or the SX126x station MCU stopped reporting | EVENTS ERROR `RADIO ground ... link lost: <detail>` | Re-plug / power the station radio; SX126x: check the station sketch is running |
| WARN `GDS router bridge unavailable: front panel / RCU / SX126x radio disabled` | Host mode without `arduino-router-bridge`, or the router not running | `systemctl status arduino-router`; host mode: run `install_station.sh` | An SX126x station has no radio without the bridge; an E22 station still receives (no `WAKE`, `GSCAN`, `LINKRATE`) |
| `LINKRATE` etc. REJECTED: `... needs the Radio Control Unit ...` | `[gds] rcu = false`, no bridge, or RCU wires missing | RADIO events at start | Wire M0/M1/AUX to the station MCU (D2/D3/D4) and set `rcu = true` |
| WARN `RADIO ground E22-400T33D: ch ... MISMATCH [...]` at start | The station module's saved registers differ from `station.toml` (also after a coordinated change and a GDS restart) | The list names the keys | Write the module (`python -m dssq.radio.e22 --write`, [15](15_Configuration_Reference.md) §1) or correct `station.toml` to the link in use |
| Station panel: AOS dark, ALARM flashing fast (5 Hz) | "GDS link down": the station MCU received no status line for 5 s | Is the GDS running? | Restart the ground App / service ([06_Displays.md](06_Displays.md)) |
| CardKB keys do nothing or wrong characters | Wiring, supply level, or key codes of another CardKB firmware | Type `KEYTEST` + Enter: raw key codes are echoed to the dashboard | 3.3 V first, then 5 V through a BSS138 shifter; adjust `KEY_*` in the station sketch |
| YELLOW `GDS stage errors in the last 60 s` | An internal exception caught by a GDS stage boundary | EVENTS WARN `GDS stage '<stage>' failed: ...`; App log has the traceback | The GDS keeps running. Report the traceback; restart if it repeats |

### 2.3 No AOS (no frames received)

The station shows LOS and the frame counters do not move.

| # | Likely cause | Check | Action |
|---|---|---|---|
| 1 | Spacecraft not transmitting: no power, radio configuration failed, transmitter inhibited, hibernating, PA over-temperature | UNO Q App Lab log / EGSE status board: `tx`, `inhibit`, `hib`; EVRs 0x0306 FAILED, 0x0603, 0x0604, 0x0800 in `evr_*.jsonl` | Power and wiring; wait for the inhibit; `WAKE` (07 §6) |
| 2 | Channel or rate code differs between the ends (after a reverted change, a station restart after `LINKRATE`, or an edited file) | Station: `RADIO` directive / start event; spacecraft: RF packet `channel`, `air_rate_code` (EGSE status `ch`, `code`) | Put the station on the spacecraft's values (07 §7.4). The spacecraft returns to its `config.h` defaults at its next boot or after the command-loss timer |
| 3 | Different radio models (T33D against T37S is compatible; E22 against SX126x never is) | EVR 0x0308, RF `radio_model`; YELLOW `radio model mismatch` | Same model family at both ends ([15](15_Configuration_Reference.md) §2 row 1) |
| 4 | Signal too weak | RECEIVER tab: RSSI, noise floor; frames failing RS (WARN `DECODER RS uncorrectable codeblock(s)`) | Antennas, polarisation, line of sight, distance; a slower rate code (more sensitive) when the link is still up |
| 5 | Frames arrive but SDLS rejects them (counts as received, no packets) | WARN `SDLS TM frame VCn rejected by SDLS: ...`; YELLOW `SDLS service mismatch suspected` | See 2.8 |
| 6 | Station receiver deaf: wrong port, E22 in the wrong mode, antenna disconnected | RECEIVER: serial octet counter moving, noise-floor readings arriving; an antenna disconnected or connected shows as a change of the noise floor | Check the station radio chain; `RADIO` directive reads the module back |

### 2.4 Commands do not get through

Commands go QUEUED -> RADIATED -> ACCEPTED (CLCW) -> EXECUTED / FAILED (CMDVER). Find the last
state reached in OVERVIEW **Command status**.

| Last state | Likely cause | Check | Action |
|---|---|---|---|
| REJECTED at once (console) | The dictionary refused it: unknown mnemonic, argument out of range, `CHANNEL` / `LINKCHAN` outside `allowed_channels`, or the TC frame would exceed 64 octets (USLP + AE with SEQLOAD / MAGCAL) | The console message says which | Correct the command; keep `[link] tc_framing = "TC"` for SEQLOAD and MAGCAL |
| REJECTED `TC uplink BLOCKED: SDLS SN file corrupt ...` or `TC uplink BLOCKED: no TC sequence-number file ...` | The ground SDLS sequence-number file is unreadable, or missing for a loaded key with TC AUTH or AE (normal at the first start with a new key; also a new archive folder; section 4.6): the station refuses to guess a sequence number | YELLOW `TC uplink BLOCKED` | Self-heals with the next HK the spacecraft made at least 120 s after its boot - AE-protected when `[sdls] tm_service` is AE, any HK when it is NONE (WARN `SDLS TC sequence number re-synchronised to <n> (the time floor: 4 per second since 2026-01-01; sdls_last_sn <m>) from ...: TC uplink released`); needs downlink and a correct station clock |
| Stays QUEUED | **No downlink frame received**: the station transmits only in the quiet window right after it received a frame (half-duplex) | Header LOS? | Restore the downlink first (2.3). `WAKE` is the only uplink sent without a received frame |
| Stays QUEUED, hazardous command | Waiting for CONFIRM (and AUTH with the two-person rule) | OVERVIEW pending strip, `Q|<id>` on the panel | CONFIRM within 120 s (empty Enter on the CardKB, or CONFIRM in the web) |
| RADIATED, then TIMEOUT or SUSPENDED | Uplink not received or not accepted | RF packet `uplink_rssi` (n/a = nothing heard), `cltu_ok` / `cltu_bad`; HK `tc_frames_bad`, `sdls_auth_fail`; EVRs 0x01xx | Station TX power and antenna; SDLS (2.8); FOP events (section 4.11) |
| RADIATED, CLCW shows Lockout | FARM-1 lockout | EVR 0x0205, YELLOW `FDIR FARM_LOCKOUT`, FOP WARN `FARM LOCKOUT reported` | Send `UNLOCK` (07 §2.5) |
| SUSPENDED `transmission limit 3 reached` | Three transmissions without acknowledgement | FOP WARN `FOP suspended: cmd #n not acknowledged` | Fix the uplink, then re-send; the FOP re-initialises from the next CLCW |
| Suspended after `invalid CLCW N(R)` | The spacecraft FARM was reset (reboot) or another station sent SET V(R) | FOP WARN `invalid CLCW N(R)=...` | `SETVR <n>` or `UNLOCK` (07 §2.5) |
| FAILED (CMDVER) | Accepted but refused by the flight software | CMDVER error code (table below), plus the EVR | See the table |

CMDVER error codes (`HK.cmd_last_status` uses the same codes):

| Code | Name | Meaning | Typical action |
|---|---|---|---|
| 1 | UNKNOWN_OPCODE | Opcode not 0x01..0x20 (EVR 0x0501) | Ground and flight dictionaries differ: rebuild both from one release |
| 2 | BAD_LENGTH | Argument length wrong for the opcode (EVR 0x0502) | As above |
| 3 | BAD_ARGUMENT | A value or a cross-argument rule failed (EVR 0x0503); also every refused SEQLOAD / SEQCTRL / MAGCAL (their own EVR gives the reason) and SPECBASE / SPECCFG / SPECMODE combinations the flight refuses | Read the EVR (0x0901, 0x0903, 0x0913) |
| 5 | HARDWARE | The instrument is absent: MAGCC / MAGBIST without a fitted RM3100, SPECDIAG with the AS7265X absent, a CFDP sender that could not start (no EVR) | Check `sensor_health` |
| 6 | BAD_MAGIC | REBOOT without 0xB007 (EVR 0x0506) | Use `REBOOT 0xB007` |
| 7 | BUSY | Resource in use or not ready: CFDPPUT without SCLK partition (EVR 0x0A06) or while a transfer / relay / playback runs; PLAYBACK START or SSRCLEAR during an SSR export; RFSCAN during a survey; SPECDIAG while a dump is queued; MAGBIST while a self-test is pending (no EVR except 0x0A06) | Retry later |

### 2.5 Spacecraft resets or goes SAFE unexpectedly

| Symptom | Likely cause | Check | Action |
|---|---|---|---|
| HK `sclk_partition` jumped, `uptime_s` small, EVR 0x0001 | Reboot | HK `reset_cause`: POWER-ON, PIN, SOFTWARE (commanded REBOOT), WATCHDOG (the hardware watchdog: a loop pass hung for 40 s, `config.h` `kIwdgTimeoutMs`), BROWNOUT | WATCHDOG: report with the EGSE log and the last EVRs before the gap; BROWNOUT: next row |
| `reset_cause` BROWNOUT, EVR 0x0608, YELLOW `spacecraft reset cause BROWNOUT` | The supply sagged during a transmission | Supply voltage at the radio during a long transmission (board-powered option: >= 4.7 V needed for full power) | Fix the supply, then restore the power with `TXPWR 0` ([03_Hardware_Integration.md](03_Hardware_Integration.md)) |
| EVR 0x0601 COMMAND LOSS, mode SAFE, radio back to defaults | No valid command for `CMDLOSS` seconds (default 86400) | HK `cmd_loss_timer_s` | Command more often, or raise / disable the timer with `CMDLOSS` (hazardous) |
| EVR 0x0604, transmitter off 300 s, SAFE | E22-400T37S PA over-temperature | `radio_fault` 3 or 4 | Improve cooling; reduce the duty cycle (slower cadence, longer `FPERIOD`) |
| Stored sequence stopped (EVR 0x0907) | Fault protection put the spacecraft in SAFE | The EVR that preceded it | Fix the cause, `MODE CRUISE`, `SEQCTRL START` again (entries are kept) |

### 2.6 Instruments

| Symptom | Likely cause | Check | Action |
|---|---|---|---|
| `sensor_health` bit missing since boot | Wiring, address strap, power, or instrument disabled by `SENSORS` | EVR 0x0707 ABSENT (AS7265X); I2C errors; boot EVRs 0x0910 / 0x0911 (`NOT RUN (not fitted)`) | Check A4/A5 wiring and the 3.3 V pull-ups; the spacecraft re-probes missing units every 30 s doubling to 10 min (EVR 0x060B when found) |
| EVR 0x0606 Nicla Sense ME stopped responding | Bus fault or the ARU firmware stopped | HK `i2c_errors`, EVR 0x0605 | Power-cycle the Nicla; EVR 0x0607 when it answers again |
| EVR 0x0605 then 0x060A | A device holds SDA low; the bus clear failed | `i2c_errors` rising | Disconnect instruments one by one; a hung AS72651 is the usual suspect |
| EVR 0x0707 `LAMP STATE UNKNOWN` | The spectrometer driver lost its LED state | MAG flags `LAMP` on magnetometer samples | Wait for recovery (EVR 0x0705 / `available again`); MAG samples taken meanwhile are flagged |
| SPEC packets invalid in LOCKIN / FLUOR, EVR 0x0707 `no lamp on a present device` | The configured lamps sit on an absent device | EVR 0x0706 `devices` list | Use a lamp of a present device (`SPECCFG`) or SURVEY |
| Spectrum saturated (SPEC flags b6, `sat_mask`) | Too much light for the exposure | SPECTROMETER tab | Use automatic exposure (`SPECCFG AUTO ...`) or a lower gain / shorter integration |
| RM3100 BIST `FAIL` / `TIMEOUT` (EVR 0x0910 / 0x0911 WARN) | Sensor or wiring fault | HK `mag_bist` | Re-run `MAGBIST BOTH`; check the boom cable |

### 2.7 RF details (SX126x)

| Symptom | Likely cause | Check | Action |
|---|---|---|---|
| YELLOW / RED `frequency offset > 50 % / 80 % of FERR_L` | TX-RX crystal offset approaching the LoRa tolerance (+-25 % of BW, +-50 ppm at SF12, the tighter applies) | RECEIVER: frequency error, AFC | Enable the AFC (`[radio.sx126x] afc = true`); calibrate `freq_offset_cal_hz`; TCXOs at both ends below 62.5 kHz bandwidth |
| YELLOW `AFC FAULT (frequency-error sign check failed twice)` | The residual grew after a step twice: wrong sign convention or a drifting reference | AFC events (section 4.13) | Check the station frequency reference, then `AFC RESET` |
| YELLOW `ground SX126x hardware mismatch: ...` | The station sketch was built for another chip / TCXO than `[radio.sx126x]` says | The text names both | Rebuild the station sketch or correct `station.toml` |
| EVR 0x0309 TCXO did not start | Wrong `kSxTcxoV` for the spacecraft module, or the module has a crystal | - | Correct `kSxTcxoV` (0 for a crystal) and rebuild |

### 2.8 Security (SDLS)

| Symptom | Likely cause | Check | Action |
|---|---|---|---|
| Ground WARN `TM frame ... rejected by SDLS: BAD_SPI` / `BAD_IV` repeatedly, YELLOW `SDLS service mismatch suspected` | Spacecraft runs TM NONE while the station expects AE (or no key on board) | HK / EGSE `tm_svc`; EVR 0x0001 text `TC <svc> TM <svc>` | Same `VGQ_SDLS_TM` and `[sdls] tm_service`; install the flight key and rebuild |
| `BAD_MAC` on every frame (ground) and EVR 0x0115 `bad MAC` (flight) | Different keys at the two ends | - | Install the same key at both ends (07 §10) |
| Ground `REPLAY` on every frame after a spacecraft change | The EGSE boot counter went back (lower epoch) with the same key | RED `TM epoch went backwards with a valid MAC ...` | Treat as possible nonce reuse: generate a new key (07 §10.4); if the cause is understood (e.g. bench test with a fresh EGSE), `SDLS RESETREPLAY` |
| EVR 0x0121 null SA, `HK.sdls_tm_state` = 3 (YELLOW limit) | No epoch from the EGSE within 120 s: EGSE not running, its boot counter unreadable, or the Router Bridge registration failed (EVR 0x0005) | UNO Q App log (section 4.23) | Start / fix the EGSE; the downlink is protected again as soon as an epoch arrives (EVR 0x0120) |
| EVR 0x0123 commands refused after boot | The EGSE has not restored the anti-replay counter yet | Within 120 s of boot | Wait; after 120 s without the EGSE the spacecraft accepts again (EVR 0x0124) |
| HK `sdls_auth_fail` rising, EVR 0x0112..0x0116 | Someone else transmitting, keys or services different, or a damaged uplink | The EVR names the check that failed | See the EVR rows in section 3 |

### 2.9 Files, archive and time

| Symptom | Likely cause | Check | Action |
|---|---|---|---|
| CFDPPUT FAILED BUSY, EVR 0x0A06 | No SCLK partition from the EGSE yet | HK `sclk_partition` = 0 | Start the EGSE; retry |
| Transfer ends INCOMPLETE / ABANDONED | Frames lost, transfer cancelled, or no PDU for `inactivity_s` | FILES tab, CFDP events | Re-send with a new `CFDPPUT` (07 §9) |
| YELLOW `archive logging paused: free space ...` | The archive file system is below `[gds] archive_min_free_mb` | FILES tab logger table | Free space or lower `archive_keep_days` / `archive_max_gb`; logging resumes by itself |
| YELLOW `archive write errors in the last 60 s` | Disk full or failing; the archive stops recording, the link continues | ARCHIVE WARN event with the error | Free space / replace the medium |
| SCET column empty in the archive | No time correlation yet (needs a TIMECORR packet whose reference frame was received) | TIME panel: correlation samples | Command `TIMECORR`; wait for the next periodic TIMECORR |
| TDM files missing | `[tdm] auto_write = false`, no measurement in the pass, or the station clock is outside 2000..2100 (WARN `TDM station clock outside ...`) | TDM events | Synchronise the station clock (NTP); `TDM WRITE` |

### 2.10 Importing and starting an App in App Lab

| Symptom | Likely cause | Check | Action |
|---|---|---|---|
| App Lab stops at once: `Invalid library directory in sketch project: libraries/RadioLib` | The App was made from the repository folder (or a zip of it) instead of the package: the repository does not hold `sketch/libraries/` (the patched RadioLib and Arduino_RouterBridge copies are written by tools and are not tracked) | The imported zip is a few hundred kB instead of about 5.4 MB; the App has no `sketch/libraries/` folder | Delete the App and import `dist/vgq1-spacecraft-unoq.zip` or `dist/dssq-groundstation-ventunoq.zip`. Building from the repository instead: run the two `fetch` commands in the comment of `sketch/sketch.yaml` first |
| UNO Q flight App: the build ends with `libm_nano.a(lib_a-wf_exp.o): in function 'expf': ... undefined reference to '__errno'` (also `log10f`, `powf`) and `collect2: error: ld returned 1 exit status` | A flight package older than the fix, built by an arduino:zephyr core before 1.0.0 (an App Lab that was not updated): those cores link newlib-nano's libm, whose `expf` / `log10f` / `powf` need `__errno` | The paths in the message contain `arm-zephyr-eabi/0.16.8` | Import the current `dist/vgq1-spacecraft-unoq.zip` (it has `sketch/src/libc_compat.cpp`), or update App Lab and the board so that it builds with arduino:zephyr 1.0.0 or later |
| UNO Q: the EGSE's calls work (`SCLK partition / SDLS TM epoch 1 accepted`) but it keeps printing `waiting for the flight software status (sc_status) ...` | A flight package older than the fix on a core before 1.0.0: its 64-octet Serial rings never had room for a notification (D-66) | Build log: `Board platform: arduino:zephyr (0.90.0)` | Import the current package |
| UNO Q flight App builds and flashes, but the EGSE keeps printing `waiting for the flight software status (sc_status) ...` and the LED matrix stays dark (a running sketch shows at least the mode and the blinking URGENT beacon in row 7) | The board's loader refused the sketch: it refuses one that needs a symbol the loader does not export, weak references included. Flight packages older than the fix did this on cores before 1.0.0 (`_ZTH13z_tls_current`, from `k_current_get()`) | Core version in the build log (`Board platform: arduino:zephyr (<version>)`); for a modified sketch, `tools/llext_check.py` on its build ([08](08_Verification_and_Test.md) section 2.6) | Import the current package. After changing the flight code, run the loader symbol check |
| UNO Q flight App: the Python output ends with `EGSE ERROR not running under Arduino App Lab (cannot import name 'AppError' from 'arduino.app_utils' ...)` and `exited with code 2` | A flight package older than the fix on an App Lab whose Python library (app-bricks-py) is older than 0.13.0: `AppError` does not exist there | The App Lab build log shows a core before 1.0.0 (`Board platform: arduino:zephyr (0.90.0)`) | Import the current `dist/vgq1-spacecraft-unoq.zip` (the EGSE uses `AppError` only where the library has it), or update App Lab and the board |
| Flashing the VENTUNO Q prints OpenOCD `Error:` lines (`Translation from khz to adapter speed not implemented`, `Execution of event examine-end failed`, `... reset-start failed`) | Messages of the board's flashing tool (OpenOCD with the `linuxgpiod` adapter, whose speed cannot be set), not of this project | The log continues with `Padding image section ...`, `shutdown command invoked` and the Python start-up | None while the App starts; the station sketch then runs (lamps and CardKB answer once wired, docs/00 part 9) |

---

## 3 Spacecraft event reports (EVR) - complete catalogue

Severity: 0 DIAG, 1 INFO, 2 WARN, 3 ERROR, 4 FATAL. "Text" is the printf format sent (at most
80 characters). Rows marked `+n` list every value the family can take. Source:
[`fsw/flight.cpp`](../vgq1-spacecraft/sketch/src/fsw/flight.cpp) (all `evr()` calls) with the
texts of [`fsw/autonomy.cpp`](../vgq1-spacecraft/sketch/src/fsw/autonomy.cpp),
[`fsw/datalink.cpp`](../vgq1-spacecraft/sketch/src/fsw/datalink.cpp),
[`ccsds/sdls.cpp`](../vgq1-spacecraft/sketch/src/ccsds/sdls.cpp) and
[`drivers/as7265x.cpp`](../vgq1-spacecraft/sketch/src/drivers/as7265x.cpp).

### 3.1 System 0x00xx

| Id | Sev | Text | When | Operator action |
|---|---|---|---|---|
| 0x0001 | INFO | `BOOT VGQ-1 FSW 3.0 rst=%u %s radio=%s %s TC %s TM %s` | Boot: reset cause, radio model, framing (TM / USLP), SDLS services | Check reset cause, model and services are what you expect (2.5) |
| 0x0002 | INFO | `DE %s` | Boot and every `kCallsignPeriodS` (600 s): station identification | None; it must show your callsign |
| 0x0003 | INFO | `SSR capacity %lu KiB` | Boot: recorder heap obtained (256 KiB normally) | Less than 256 KiB: report (heap tight) |
| 0x0004 | INFO | `stored sequence table and MAGCAL are RAM only: empty after boot` | Boot | Re-load sequences and re-uplink MAGCAL after every boot (07 §4, §5.1) |
| 0x0005 | WARN | `Bridge registration failed: %s` | Boot: a Router Bridge registration failed (names listed) | EGSE services (epoch, SN restore, hardline, CFDP relay) may not work: restart the spacecraft App; check the router |
| 0x0011 | INFO | `MODE %s -> %s (%s)` (to SAFE) | Mode change; reason `boot`, `ground command`, `stored sequence`, `command loss` or `PA over-temperature` | After boot: send `MODE CRUISE` (2.1); otherwise follow the reason |
| 0x0012 | INFO | as above (to CRUISE) | | None |
| 0x0013 | INFO | as above (to ENCOUNTER) | | None (higher cadence: watch the link load, EVR 0x0215) |
| 0x0014 | INFO | as above (to TEST) | | None |

A mode change also ends hibernation (EVR 0x0801 `WAKE: mode change`).

### 3.2 Uplink and SDLS 0x01xx

| Id | Sev | Text | When | Operator action |
|---|---|---|---|---|
| 0x0101 | WARN | `TC frame rejected: TOO_SHORT` | A decoded CLTU held a frame shorter than a header | Damaged or foreign uplink; if repeated, check the station transmitter |
| 0x0102 | WARN | `TC frame rejected: BAD_VERSION` | Version bits neither TC ('00') nor USLP ('1100') | Foreign transmitter or station software mismatch |
| 0x0103 | WARN | `TC frame rejected: BAD_SCID` | Spacecraft ID not 0x0A7 | Another mission's uplink, or a station built for another SCID |
| 0x0104 | WARN | `TC frame rejected: BAD_LENGTH` | Frame length field does not match | Damaged frame; repeated: software mismatch |
| 0x0105 | WARN | `TC frame rejected: BAD_FECF` | Frame CRC wrong after BCH | Damaged uplink (rare: BCH corrects first) |
| 0x0106 | WARN | `TC frame rejected: BAD_HEADER` | USLP: truncated header, source flag, AD frame without a count, control command on AD | Station `tc_framing` / software version |
| 0x0107 | WARN | `TC frame rejected: BAD_TFDF` | USLP: construction rule or UPID wrong (also after SDLS) | As above |
| 0x0112 | ERROR | `SDLS rejected TC frame: bad length` | Frame too short for the service: the ground uses another service (NONE / AUTH against AE) | Match `[sdls] tc_service` and `VGQ_SDLS_TC` |
| 0x0113 | ERROR | `SDLS rejected TC frame: bad SPI` | SPI does not match the service (AUTH SPI 1, AE SPI 2) | Match the services and `[sdls] spi` / `VGQ_SDLS_SPI` |
| 0x0114 | ERROR | `SDLS rejected TC frame: REPLAY` | Authentic frame with a sequence number not above the last accepted | A replayed recording, or the ground SN file went back: the ground raises its SN from the next protected HK by itself |
| 0x0115 | ERROR | `SDLS rejected TC frame: bad MAC` | Authentication failed | Keys differ, or someone else transmits. Never ignore repeated bad MACs |
| 0x0116 | ERROR | `SDLS rejected TC frame: bad IV` | AE IV not 'TC' / epoch 0 / zero field | Foreign sender or wrong direction (a TM recording sent as TC) |
| 0x0120 | INFO | `SDLS TM epoch %u: downlink protected` | The EGSE supplied a new epoch; AE protection runs | None |
| 0x0121 | WARN | `SDLS TM: no epoch after %lu s: null SA (clear) until the EGSE sends one` | 120 s after boot without an epoch: frames go out unprotected, flagged | Start / fix the EGSE (2.8) |
| 0x0122 | WARN | `SDLS TM epoch %ld refused (current %u)` | The EGSE offered an epoch outside 1..65534 or lower than the current one | EGSE boot counter regressed: never lower it with the same key; restore the highest value or rotate the key (07 §10) |
| 0x0123 | WARN | `SDLS TC refused: anti-replay counter not restored by the EGSE yet` | An authentic command within 120 s of boot, before the EGSE restored the counter | Wait for the EGSE restore |
| 0x0124 | WARN | `SDLS TC: no anti-replay restore after %lu s: accepting above sn %lu` | No restore within 120 s: commands accepted above the RAM counter so the spacecraft stays commandable | Start the EGSE; an old recorded command could be accepted meanwhile (security note in [17_Security_Model.md](17_Security_Model.md)) |

0x0111 (`disabled`) and 0x0117 (`SN not restored`) are never sent: a disabled service is not
checked, and the not-restored case is reported as 0x0123. Every 0x0112..0x0116 sets FDIR
`SDLS_AUTH` (latched until `RSTCNT` or a reboot).

### 3.3 COP-1 and link changes 0x02xx

| Id | Sev | Text | When | Operator action |
|---|---|---|---|---|
| 0x0201 | INFO | `COP-1 UNLOCK` | FARM accepted an Unlock directive | None |
| 0x0202 | INFO | `COP-1 SET V(R)=%u` | FARM accepted Set V(R) | None |
| 0x0203 | DIAG | `FARM gap: N(S)=%u V(R)=%u, retransmit requested` | An AD frame ahead of V(R): retransmission requested (not downlinked at the default EVRLEVEL) | None (FOP retransmits) |
| 0x0204 | DIAG | `FARM duplicate N(S)=%u discarded` | An AD frame already accepted | None |
| 0x0205 | ERROR | `FARM LOCKOUT (N(S)=%u V(R)=%u) - ground must UNLOCK` | AD frame outside the FARM window | `UNLOCK` (07 §2.5) |
| 0x0206 | WARN | `invalid COP-1 control command` | A control frame that is neither Unlock nor Set V(R) | Station software mismatch |
| 0x0210 | INFO | `radio change confirmed by ground (code %u, ch %u)` | A valid RF command arrived on the new rate / channel: the auto-revert is cancelled | None: the change is final |
| 0x0211 | WARN | `AIR RATE -> code %u in 2 frames; revert in %u s without uplink` | AIRRATE accepted | Normally part of `LINKRATE` (07 §7). A raw AIRRATE needs the station changed by hand |
| 0x0212 | WARN | `CHANNEL -> %u (%lu.%03lu MHz) in 2 frames; revert in %u s without uplink` | CHANNEL accepted | As above for `LINKCHAN` |
| 0x0213 | INFO | `RADIOOPT LBT/CAD %s, Rx boost %s` | RADIOOPT accepted | None |
| 0x0214 | INFO | `TXPWR code %u = %d dBm (%s)` | TXPWR accepted | None |
| 0x0215 | INFO | `downlink load: packet periods x%u.%02u (capacity %lu B/min)` | The on-board load plan changed by >= 10 %: periodic packets are stretched to <= 75 % of the link capacity | A factor above x1.00 means the cadence you set does not fit the link (MAGCAL needs x1.00, 07 §5.1) |

### 3.4 Radio 0x03xx

| Id | Sev | Text | When | Operator action |
|---|---|---|---|---|
| 0x0301 | ERROR | `radio TX timeout (%lu ms) - TX path suspect` | A transmission did not complete (E22 AUX or SX126x TX timeout); FDIR `AUX_TIMEOUT` until the next good transmission | Check the radio supply and wiring; repeated: radio fault |
| 0x0302 | INFO | `RF survey CH%u..CH%u starting (downlink paused)` | RFSCAN accepted | Expect a short downlink gap |
| 0x0303 | INFO | `RF survey done in %lu ms: quietest CH%u (%d dBm)` | Survey finished; the RFSCAN packet follows | Use with `GSCAN` to pick a channel (07 §7.3) |
| 0x0304 | ERROR | `radio fault %u (%s) - recovering` | SX126x fault 16 BUSY stuck, 17 SPI/command error, 18 re-init failed, 19 no IRQ; FDIR `RADIO_CFG`; reset and reconfigure follow | Watch for 0x0305; repeated: module or wiring |
| 0x0305 | INFO | `radio recovered (%u recoveries)` | SX126x reset and reconfigured | Count recoveries; frequent ones point to supply or wiring |
| 0x0306 | INFO / ERROR | `radio %s %s: ch %u code %u pwr %d dBm sens %s%u.%u dBm %s` | Boot and every reconfiguration: model, `OK` or `FAILED`, channel, rate, power, sensitivity and its source | FAILED: FDIR `RADIO_CFG`; check the module (E22 M0/M1/AUX pull-ups, supply) |
| 0x0307 | INFO | `uplink with LoRa CRC error rescued by BCH/FECF/SDLS (%lu total)` | SX126x: a CLTU with a LoRa CRC error still decoded and was accepted | None (concatenated coding working) |
| 0x0308 | INFO / WARN | `radio model %s (no strap; build %s)` / `radio model %s (strap %s, %u/%u low; build %s)` | Boot: the E22 model chosen by the A0 strap; WARN when the reads were split | WARN: a loose jumper or a long lead: fix the strap |
| 0x0309 | WARN | `SX126x TCXO did not start: running on the crystal (VERIFY kSxTcxoV)` | After begin or a recovery | Correct `kSxTcxoV` (2.7) |
| 0x0311 | ERROR | `E22 reports UNDER-VOLTAGE (<4.5 V) - module RF output disabled` | E22-400T37S abnormal status 01; FDIR `RADIO_FAULT` + `LOW_BUS_V` | Supply too low or wires too thin for the TX current |
| 0x0312 | ERROR | `E22 reports OVER-VOLTAGE (>15 V) - module RF output disabled` | Status 02 | Supply above the module limit: switch off and fix |
| 0x0313 | ERROR | `E22 reports OVER-TEMPERATURE (>120 C) - module RF output disabled` | Status 03; FDIR `PA_OVERTEMP` and the response of 0x0604 | Cooling |
| 0x0314 | ERROR | `E22 reports OVER-VOLTAGE + OVER-TEMPERATURE - module RF output disabled` | Status 04 | Both |
| 0x0315 | INFO | `E22 abnormal status cleared (%u reports)` | 3 s after the T37S stopped reporting; flags cleared | None |

### 3.5 Recorder 0x04xx

| Id | Sev | Text | When | Operator action |
|---|---|---|---|---|
| 0x0401 | INFO | `SSR playback %s (%lu packets)` | PLAYBACK START / STOP accepted | None |
| 0x0402 | INFO | `SSR playback complete` | The recorder was played back completely on VC2 | `SSRCLEAR` if you want to free it (hazardous) |

### 3.6 Command rejection 0x05xx

| Id | Sev | Text | When | Operator action |
|---|---|---|---|---|
| 0x0501 | WARN | `command 0x%02X rejected, error 1` | Unknown opcode | Dictionary mismatch between the ends |
| 0x0502 | WARN | `command 0x%02X rejected, error 2` | Wrong argument length or packet length | Dictionary mismatch |
| 0x0503 | WARN | `command 0x%02X rejected, error 3` | Argument out of range / rule failed, or a bad TC packet header | Correct the command; dictionary mismatch if the ground accepted it |
| 0x0506 | WARN | `command 0x%02X rejected, error 6` | REBOOT without the magic 0xB007 | `REBOOT 0xB007` |

Execution refusals with code 5 (HARDWARE) or 7 (BUSY) are reported in CMDVER only (2.4).

### 3.7 Fault protection 0x06xx

| Id | Sev | Text | When | Operator action |
|---|---|---|---|---|
| 0x0601 | ERROR | `COMMAND LOSS: no valid uplink for %lu s -> SAFE, default radio` | Command-loss timer expired (FDIR `CMD_LOSS`, latched until `RSTCNT`); radio back to `config.h` channel / rate; hibernation ended; stored sequence stopped | Put the station on the default link; `MODE CRUISE`; review why commanding stopped |
| 0x0602 | ERROR | `radio change NOT confirmed by uplink - reverting to ch %u code %u` | No valid RF command within `revert_s` after a rate / channel change (FDIR `RATE_REVERT`, latched until `RSTCNT`) | The station must be on the old values: the GDS reverts too (LINK WARN); investigate the margin before retrying |
| 0x0603 | INFO | `transmitter re-enabled` | End of `TXINHIBIT` or of the PA cool-down | Expect AOS |
| 0x0604 | FATAL | `PA over-temperature: transmitter OFF for %u s, SAFE` | E22-400T37S over-temperature report with the FDIR response enabled | Cooling; reduce the duty cycle; wait 300 s |
| 0x0605 | ERROR | `I2C bus fault (%u consecutive errors) - bus clear + re-init`; `Qwiic I2C bus fault (%u consecutive errors) - re-init` for the Qwiic socket (AS7265X there) | More than 10 consecutive bus errors (at most once per 10 s); FDIR `I2C_BUS` | Check the sensor wiring; 0x0609 when healthy |
| 0x0606 | ERROR | `Nicla Sense ME stopped responding` | More than 5 failed reads; FDIR `SENSOR_LOST`; bus temperature becomes n/a | 2.6 |
| 0x0607 | INFO | `Nicla Sense ME responding again` | Reads succeed again; `SENSOR_LOST` cleared | None |
| 0x0608 | WARN | `brownout reset: TX power derated (code %u) - check radio supply` | Boot after a BROWNOUT reset | Fix the supply, then `TXPWR 0` (2.5) |
| 0x0609 | INFO | `I2C bus healthy again: FDIR I2C_BUS cleared` | 10 s without a new bus error | None |
| 0x060A | ERROR | `I2C bus clear: SDA or SCL still low after %u clocks` | Boot or recovery: a line stays low after 9 clocks | A device or a short holds the line: disconnect devices one by one |
| 0x060B | INFO | `instrument acquired: %s` (`Nicla Sense ME`, `RM3100 outboard`, `RM3100 inboard`) | A unit missing at boot answered a re-probe (a self-test follows for an RM3100) | None |
| 0x060C | ERROR | `IWDG settings not confirmed in %lu ms: watchdog runs, timeout may be short` | Boot: the hardware watchdog's prescaler / reload did not reach the IWDG within 200 ms (`config.h` `kIwdgUpdateWaitMs`); it runs, possibly with the reset value of 512 ms | Expect repeated WATCHDOG resets: report; a build with `VGQ_IWDG = 0` runs without the watchdog |

### 3.8 Spectrometer 0x07xx

| Id | Sev | Text | When | Operator action |
|---|---|---|---|---|
| 0x0701 | INFO | `spectral event: angle %u.%02u deg (threshold %u.%02u)` | The spectrum changed by more than the threshold against the slow baseline (at most one per 60 s) | Science event: look at the SPECTROMETER tab; `SPECBASE` to tune or reset |
| 0x0702 | INFO | `light source %s -> %s (%u %%)` | The on-board light-source class changed (heuristic) | Compare with the ground class (SPECTRAL events) |
| 0x0703 | INFO | SPECDIAG line: `AS7265X dev%u HW %02X%02X FW %u.%u.%u SEL %02X LED %02X CFG %02X IT %u T %d C` or `AS7265X dev%u COEF %02u-%02u: <words>` | One line per loop pass after `SPECDIAG` | Diagnostic record; keep it with the test log |
| 0x0704 | INFO / WARN | `BURST START n=%u s cycles=%u` / `BURST END samples=%u rate=%u.%u Hz dropped=%u readmax=%u ms tx=%u` / `BURST rice status %u` (WARN) | BURST mode start and end; WARN when the Rice encoder returned an unexpected status (that packet is sent raw) | After a burst return to SURVEY (07 §5.3); report a rice status WARN |
| 0x0705 | WARN | `AS7265X recovered by soft reset` | The driver recovered the instrument after repeated timeouts | None if rare |
| 0x0706 | INFO | `AS7265X HW %02X/%02X FW %u.%u.%u devices %s on %s` | Boot and after a recovery: identity, devices present (NIR, VIS, UV) and the bus it answered on (`A4/A5` or `Qwiic`) | A missing device: its six channels are invalid |
| 0x0707 | WARN | `AS7265X %s`: `ABSENT`, `FAULT`, `available again`, `LAMP STATE UNKNOWN`, `not responding: SPECCFG stored for later`, `no lamp on a present device: LOCKIN/FLUOR invalid` | Driver state changes and configuration problems | 2.6 |

### 3.9 Hibernation 0x08xx

| Id | Sev | Text | When | Operator action |
|---|---|---|---|---|
| 0x0800 | WARN | `HIBERNATE: duty-cycled receive, beacon every %u s` | HIBERNATE accepted (FDIR `HIBERNATE`, informational) | Expect LOS between beacons (07 §6) |
| 0x0801 | WARN | `WAKE: %s` (`authenticated uplink received`, `mode change`, `command loss`) | Hibernation ended | `MODE CRUISE` when you want normal cadence |

### 3.10 Autonomy 0x09xx

| Id | Sev | Text | When | Operator action |
|---|---|---|---|---|
| 0x0900 | INFO | `SEQ slot %u loaded: %s %lu s op 0x%02X%s` (`T+` or `SCLK`, ` replaced`) | SEQLOAD accepted | None |
| 0x0901 | WARN | `SEQLOAD refused: %s (cmd error %u)` | Reasons: `argument block length`, `slot out of range`, `reserved flag bits set`, `command length not 1..12`, `padding not zero`, `time not in the future`, `time beyond 7 days`, `opcode not allowed in a sequence`, `no command validator`, `embedded command invalid` | Fix the sequence file (07 §4); `time not in the future`: the SCLK estimate was stale, reload |
| 0x0902 | INFO | `SEQCTRL %s` (`START`, `STOP`, `CLEAR_ALL`, `DELETE slot %u`) | SEQCTRL accepted | None |
| 0x0903 | WARN | `SEQCTRL %s refused: %s` (`action out of range`, `slot must be 0 for this action`, `slot empty`, `no entries loaded`, `already armed`, `fault protection holds SAFE`) | SEQCTRL refused | `already armed`: STOP first; `fault protection holds SAFE`: `MODE CRUISE` first |
| 0x0904 | INFO | `SEQ slot %u: opcode 0x%02X` | A stored command is dispatched (its CMDVER has source STORED) | None |
| 0x0905 | WARN | `SEQ slot %u skipped: %lu s late` | Entry more than 300 s late (e.g. after a long loop stall) | Reload the entry if still needed |
| 0x0906 | INFO | `sequence COMPLETE` | Last pending entry dispatched or skipped (beacon INTERESTING) | None |
| 0x0907 | WARN | `sequence STOPPED by fault protection` | SAFE by fault protection while armed (entries kept) | 2.5 |
| 0x0910 | INFO / WARN | `RM3100 OB BIST %s ...` (`PASS`, `FAIL` with `X ok/FAIL Y ... Z ... (reg 0x..)`, `NOT RUN (not fitted)`, `BUSY (measurement in progress)`, `BUS ERROR`, `TIMEOUT (no DRDY)`, `INVALID (STE read 0)`, suffix `STE NOT CLEARED`) | Boot and MAGBIST, outboard unit; WARN for anything but PASS / NOT RUN / BUSY | FAIL / TIMEOUT / BUS ERROR: 2.6 |
| 0x0911 | INFO / WARN | `RM3100 IB BIST ...` | As 0x0910, inboard unit | As above |
| 0x0912 | INFO | `MAGCAL OB b=%d,%d,%d M=%d,%d,%d,%d,%d,%d` / `MAGCAL IB ...` / `MAGCAL OB cleared (identity)` | MAGCAL accepted: offsets in 0.01 uT, matrix in 1/16384 | Compare with the uplinked command (07 §5.1) |
| 0x0913 | WARN | `MAGCAL refused: %s` (`argument block length`, `unit out of range`, `offset beyond 300 uT`, `diagonal not 0.5..2.0`, `off-diagonal beyond 0.5`, `matrix not positive definite`) | MAGCAL refused | Re-fit; the ground refuses the same cases before uplink |

### 3.11 File delivery (CFDP) 0x0Axx

| Id | Sev | Text | When | Operator action |
|---|---|---|---|---|
| 0x0A00 | INFO | `CFDP SSR export %s: %lu B seq %lu` | `CFDPPUT SSR` started (file `ssr_p<partition>_<sclk>.bin`); recording paused | Watch FILES (07 §9) |
| 0x0A01 | INFO | `CFDP EOF cond %u: %lu/%lu B, %lu not recorded` | SSR export ended (condition 0 no error, 4 the source stopped, 15 cancelled); packets not recorded meanwhile | Ground state should read COMPLETE |
| 0x0A02 | INFO | `CFDP cancel: %s` (`SSR export seq %lu`, `relay seq %lu`, `relay seq %lu already cancelling`, `nothing active`) | CFDPCANCEL | None |
| 0x0A03 | INFO | `CFDP relay requested: source %u seq %lu max %u KiB` | `CFDPPUT EVRLOG` / `CADU`: the EGSE is asked to send the file | Watch FILES |
| 0x0A04 | WARN | `CFDP relay seq %lu: %s` (`EGSE did not start within 30 s`, `cancelled, no EOF within 30 s`, `silent, expired`) | Relay supervision timeouts (start 30 s, silence 600 s) | Check the EGSE App and that the file exists (UNO Q App log) |
| 0x0A05 | INFO | `CFDP relay seq %lu EOF cond %u` | The relayed EOF passed | None |
| 0x0A06 | WARN | `CFDPPUT refused: no SCLK partition from the EGSE yet` | CFDPPUT before the first epoch (CMDVER BUSY) | Start the EGSE; retry |

---

## 4 Ground events - complete catalogue by source

Format in `events.log`: `<UTC> <LEVEL> <SOURCE> <text>`. `<x>` marks a value.

### 4.1 GDS (process)

| Level | Text | When | Action |
|---|---|---|---|
| INFO | `DSS-Q ground station <version> started (<mode>), radio <model>[ (auto: follows the spacecraft)] CH<ch> code <code>; SDLS TC <svc> / TM <svc>; tc_framing <f>, tm_framing <f>` | Start | Check model, channel, code and services |
| INFO | `SIMULATION MODE - software spacecraft, no RF` | Start with `--sim` / `DSSQ_SIM=1` | Training only |
| WARN | `router bridge unavailable: front panel / RCU / SX126x radio disabled (<e>)` | Host mode without the bridge package or router | 2.2 |
| WARN | `front panel not started: <e> (retried every 10 s)` | The station MCU did not answer the registration | Wait; INFO below when it works |
| INFO | `front panel started (Router Bridge reachable)` | Registration retry succeeded | None |
| WARN | `stage '<stage>' failed: <Exception>: <msg>` | An exception caught by one of the stages register, radio_read, ingest, packets, outbox, jobs, afc, tdm, housekeeping, console (at most once per 60 s per stage) | 2.2 |

### 4.2 WEB

| Level | Text | When | Action |
|---|---|---|---|
| INFO | `dashboard on http://<host>:<port>/ (loopback only[, token required for commanding])` | Start, loopback bind | None |
| INFO | `dashboard on http://<host>:<port>/ (LAN-exposed, token required for commanding)` | Start, LAN bind with a token | None |
| WARN | `dashboard on http://<host>:<port>/ (LAN-exposed WITHOUT [gds] http_token: commanding disabled (read-only dashboard))` | LAN bind, no token | Set `[gds] http_token` |

### 4.3 RADIO

| Level | Text | When | Action |
|---|---|---|---|
| ERROR | `radio backend unavailable: <reason>` | Start: serial port not opened, no Router Bridge for the SX126x, SX126x backend not attached (retried every 10 s) | 2.2 |
| INFO | `<port> opened` | The E22 serial port opened on a retry | None |
| INFO | `SX126x backend attached (Router Bridge reachable)` | SX126x registration retry succeeded | None |
| INFO | `ground <model> link connected` | The radio link came back | None |
| ERROR | `ground <model> link lost[: <detail>]` | E22 serial fault or SX126x MCU status stale (> 15 s) | 2.2 |
| ERROR | `ground <model> did not answer the configuration read` | `RADIO` directive or start-up verification | Check the RCU wiring (M0/M1/AUX) and the module supply |
| INFO / WARN | `ground <model>: ch <c> (<f> MHz) rate <r> pwr <p> LBT/CAD <l> - matches station.toml` / `... MISMATCH [<keys>]` | Start-up verification and `RADIO` | MISMATCH: 2.2 |
| ERROR | `ground <model> reports <fault> - its transmitter is disabled until the condition clears (check the station supply / cooling)` | Station E22-400T37S abnormal status (first report, then at most once a minute) | Station supply / cooling; RED alarm while it lasts |
| WARN | `spacecraft reports radio model <a>, the station is configured for <b> ([radio] model): check station.toml and the flight build` | RF packet model differs | [15](15_Configuration_Reference.md) §2 row 1 |
| INFO | `spacecraft radio model <a> matches the station again` | Mismatch ended | None |
| INFO | `[radio] model = auto: following the spacecraft's radio model <a> (was <b>) for the link budget, sensitivity and power figures` | `model = "auto"` and the spacecraft switched E22 model | None |
| INFO / WARN | `ground RF survey CH<a>-<b> in <t> s: quietest CH<x>, quietest allowed CH<y>` / `ground RF survey failed (radio not available)` | `GSCAN`. SX126x station: a channel whose retune the station MCU only queued (radio busy with a packet) gets no noise sample (n/a in the chart) | 07 §7.3 |
| WARN | `ground RF survey CH<a>-<b> not run: station transmitter still busy after <n> s (<reason>)` / `ground RF survey CH<a>-<b> not run: <reason>` / `WAKE not sent: station transmitter still busy after <n> s (<reason>)` | The station transmitter stayed busy (queued uplinks not yet reported by the MCU; SX126x: the radio not yet reported back on its channel after a survey) for max(60 s, 2 frame periods), or the survey refused to start | Repeat `GSCAN` / `WAKE` later; 07 §7.3, §6 |
| ERROR | `ground RF survey: the station radio did not return to CH<n> - it may be receiving on a surveyed channel; every uplink is held (radio.sx126x.tx_hold) until the MCU reports CH<n> again, and the station configuration is re-sent until the MCU accepts it` (SX126x) / `ground RF survey: the station radio did not return to CH<n> - it may be receiving and transmitting on a surveyed channel; check it with RADIO` (E22) | Three attempts to return to the operational channel after a survey failed | SX126x: wait for the configuration to be accepted. Until a status line shows the station channel with no change pending, the TX gate holds every uplink - window transmissions only fill the backlog (`radio.sx126x.tx_backlog`), `WAKE` and further surveys wait - and `radio.sx126x.tx_hold` gives the reason (`dssq/radio/sx126x.py` `home_unconfirmed()`); E22: `RADIO`, 07 §7.4 |
| INFO / ERROR | `wake-up transmitted (BD MODE SAFE)` / `wake-up transmission failed` | `WAKE` | 07 §6 |
| WARN | `WAKE not sent: TC uplink BLOCKED (<reason>)` | `WAKE` while the SDLS SN is blocked | 2.4 |
| ERROR | `WAKE frame not built: <e>` | Frame construction failed | Report |
| INFO / ERROR | `ground rate code -> <v>: OK` / `FAILED`, `ground channel -> <v>: OK` / `FAILED` | Ground half of a coordinated change. SX126x: OK also when the MCU queued the change (`rf_cfg` result 2: applied as soon as the radio is idle, before the next reception or transmission); FAILED = out of range (-1), MCU busy (-2) or no answer | FAILED: the station did not retune: 07 §7.4 |

Station SX126x radio events (`rf_evt E|code|text` from the station MCU) are not written to the
GDS event log: they appear in the App log (`ground SX126x <NAME>: <text>`) and in the
RECEIVER tab (`radio.sx126x.events`). Codes: 1 BUSY_TIMEOUT, 2 SPI_ERROR, 3 TX_TIMEOUT,
4 INIT_FAILED, 5 RECOVERED, 6 RX_GAIN_REASSERTED, 7 CAD_TIMEOUT, 8 DEVICE_ERRORS, 9 RX_ERROR,
10 CONFIG_FAILED, 11 MODE_RESTORED, 12 TCXO_FALLBACK, plus HW_MISMATCH from the ground backend.
A fault resets and reconfigures the radio with back-off (1 s doubling to 60 s), followed by
RECOVERED. Frequent faults point to the module supply or the SPI wiring.

### 4.4 RX (receiver)

| Level | Text | When | Action |
|---|---|---|---|
| INFO | `AOS` | First frame after LOS | None |
| WARN | `LOS` | No frame for max(`[gds] los_timeout_s`, 3 x frame period) | 2.3 (RED alarm) |
| INFO | `LOS (hibernating)` | LOS while the spacecraft hibernates or a HIBERNATE was radiated less than 7200 s ago | None |

### 4.5 DECODER and DECOM

| Level | Text | When | Action |
|---|---|---|---|
| WARN | `RS uncorrectable codeblock(s) (logged at most once a minute)` | A CADU with more than 16 symbol errors (or a false lock) | Weak signal or interference (2.3 row 4) |
| WARN | `<packet>: Rice decode failed (<e>); packet dropped` | A corrupt CCSDS 121.0 SPECBURST payload (at most once per 10 s) | Rare; report if repeated |
| WARN | `<packet>: <error>` | Malformed packet (wrong length, unknown APID) (at most once per 10 s per APID) | Dictionary mismatch between flight and ground: same release at both ends |

### 4.6 SDLS

| Level | Text | When | Action |
|---|---|---|---|
| WARN | `SDLS key file not found: <path>: uplink and downlink run without SDLS (NONE)` / `SDLS key file unreadable: <e>: ...` | Start with a service set and no usable key (also a YELLOW alarm) | Install the key (07 §10) or set the services to NONE |
| ERROR | `SDLS SN file corrupt (<file>: '<text>'): the numbers already used with this key are unknown; held until an HK reports the spacecraft's sdls_last_sn, then the counter continues above every number the key can have used (docs/17_Security_Model.md section 7.6)` | Start: unreadable or empty `sdls_sn.txt` with a key and TC AUTH or AE (YELLOW `TC uplink BLOCKED`). Without a key or with TC NONE nothing is held and this event does not appear | Self-heals with the next qualifying HK (row `re-synchronised` below) |
| WARN | `no SDLS TC sequence-number file <file> for the key <key file>: if this key was used before - by the App Lab App or host mode with another archive folder, by another station, or before the archive was restored without it - its numbers are unknown. The TC uplink is held until an SDLS-protected HK reports the spacecraft's sdls_last_sn; the counter then continues above every number the key can have used (the time floor, docs/17_Security_Model.md section 7.6). A new key never used before needs nothing else.` (with `tm_service` NONE: `... held until an HK reports ...`) | Start with TC AUTH or AE, a key loaded and no `sdls_sn.txt` (a new key's first start, a new archive folder, an archive restored without it; also with `--no-archive`; never in the simulator, which keeps its counter in memory). It comes with ERROR `no TC sequence-number file <file> for this key: the numbers already used with it are unknown; held until an HK reports the spacecraft's sdls_last_sn, then the counter continues above every number the key can have used (docs/17_Security_Model.md section 7.6)` and YELLOW `TC uplink BLOCKED` | Expected on a first start: wait for the first HK the spacecraft makes at least 120 s after its boot (it needs the downlink; no event while waiting). Hardline commanding (`dssq.cltu`) is refused until then. Otherwise [17_Security_Model.md](17_Security_Model.md) section 7.6 |
| WARN | `SDLS TM replay state file <file> corrupt (<text>): replay window restarted at (0, 0)` | Start: unreadable `sdls_tm_replay.txt` | Old recorded frames could be accepted once: be aware |
| WARN | `<n> consecutive data frames without a valid AE header (BAD_SPI / BAD_IV): the spacecraft probably runs SDLS TM NONE - check [sdls] tm_service against the flight VGQ_SDLS_TM` | 3 consecutive data frames | 2.8 |
| WARN | `TM frame VC<n> rejected by SDLS: <verdict> (dropped, never decoded)` with verdict `BAD_LENGTH`, `BAD_SPI`, `BAD_IV`, `BAD_MAC`, `REPLAY` (at most once per 10 s per verdict) | A data frame failed TM security | 2.8 |
| INFO | `SDLS TC sequence number raised <old> -> <new> from a protected HK (sdls_last_sn <n>)` | The spacecraft accepted numbers the station had not recorded (e.g. the hardline tool) | None |
| WARN | `SDLS TC sequence number re-synchronised to <n> (the time floor: 4 per second since 2026-01-01; sdls_last_sn <m>) from an SDLS-protected HK, above every number the key can have used: TC uplink released`. Variants: `from an UNPROTECTED HK (TM service NONE)` at a station whose `tm_service` is NONE; `(the SN file was higher)` when the file already held more; `(sdls_last_sn <m> + 1000000)` when the gap is used, with the text ending in `; WARNING: the reported number is above the time floor <f> - is the station clock behind? (docs/17_Security_Model.md section 7.6)` or `; WARNING: the station clock is before 2026-01-01 - no time floor, only the gap above the reported number: set the clock (docs/17_Security_Model.md section 7.6)` (these two endings also follow `(the SN file was higher)` in the same situations) | A held uplink released by an HK made at least 120 s after a flight boot: the counter continues at max(file, time floor), the floor being 4 numbers per second since 2026-01-01 (about 98 million in October 2026); `sdls_last_sn` + 1 000 000 only in the two WARNING cases. Written before the release | None; a WARNING variant: check the station clock (`date -u`, NTP) - a clock behind can reuse numbers ([17_Security_Model.md](17_Security_Model.md) section 7.6) |
| ERROR | `SDLS TC counter NOT re-synchronised: <target> (sdls_last_sn <m> + 1000000, time floor <f>) is above 4227858432 - too few numbers would be left for this key (or the HK is forged): the TC uplink stays blocked; generate a new key (docs/17_Security_Model.md section 7.6)` (`time floor None` with a clock before 2026) | The re-synchronisation target is above 4 227 858 432 (`SDLS_SN_RELEASE_MAX` = 2^32 - 2^26): a huge reported number, or the time floor itself from 2059-06-30; once per reported value | Rotate the key (07 §10.4); a forged HK is possible at a TM NONE station |
| ERROR | `SDLS TC counter NOT re-synchronised: an UNPROTECTED HK reports sdls_last_sn <n>, above the time floor <f> - the station clock is behind or the HK is forged: the TC uplink stays blocked; check the clock, then write the floor by hand (docs/17_Security_Model.md section 7.6)` (once per reported value) | At a station whose `tm_service` is NONE, a held counter would be placed above the time floor by an unauthenticated report: refused, the uplink stays held (`sdls.py` `observe_spacecraft_sn`) | Check the station clock (NTP); if it is right the HK may be forged: write the floor by hand (17 §7.6) |
| WARN | `an HK made <u> s after a spacecraft boot reports sdls_last_sn 0 (the flight's TC counter is not restored by the EGSE yet): it does not end the TC uplink hold - an HK from 120 s after the boot, or one reporting a restored number, does; a spacecraft that resets more often keeps the station held: write the floor by hand (docs/17_Security_Model.md section 7.6)` (at most once a minute) | The TC uplink is held and an HK made less than 120 s after a flight boot reports 0 (`sdls.py` `boot_hk()`, `gds.py` `_on_hk`) | Normal for a new key's first start: wait 2 min after the spacecraft boots. If it repeats, the spacecraft is resetting: see 2.5, or write the floor by hand (17 §7.6) |
| ERROR | `SDLS SN file not written: <e>` | A TC sequence number raised or re-synchronised from an HK could not be persisted (at most once a minute; a hold stays). A command whose number cannot be persisted is not sent (FOP WARN `frame not built`) | Disk full / permissions |
| ERROR | `SDLS TM replay state not saved to <file>: <e>` | Replay state not persisted | Disk full / permissions |
| ERROR | `TM epoch went backwards with a valid MAC: replayed recording or EGSE boot-counter regression (possible GCM nonce reuse) - investigate; generate a new key if the counter regressed` | A REPLAY verdict with a lower epoch (RED alarm for 24 h) | 2.8; 07 §10.4 |
| WARN | `TM replay window reset by <source>: the next (epoch, sn) is accepted` | `SDLS RESETREPLAY` confirmed | Only after a key rotation or an understood counter reset |

### 4.7 LIMITS

| Level | Text | When |
|---|---|---|
| WARN | `<packet>.<parameter> = <value> RED` / `YELLOW` | A real-time parameter entered a limit |
| INFO | `<packet>.<parameter> = <value> OK` | It left the limit |

Limits (red low / yellow low / yellow high / red high; RED when value <= red low or >= red high,
YELLOW when <= yellow low or >= yellow high), from
[`dssq/dictionary.py`](../dssq-groundstation/python/dssq/dictionary.py):

| Parameter | Unit | Red low | Yellow low | Yellow high | Red high | Action when out |
|---|---|---|---|---|---|---|
| `HK.bus_temp` | degC | -20 | 0 | 60 | 75 | Cool or warm the bus; FDIR `AVI_OVERTEMP` at 75 degC ([03](03_Hardware_Integration.md) thermal) |
| `HK.radio_fault` | code | - | - | 0.5 (any fault) | 2.5 (codes >= 3: over-temperature, SX126x faults) | Section 3.4 |
| `HK.loop_max_ms` | ms | - | - | 600 | 1000 | Flight loop stalled: report with the EGSE log |
| `HK.ssr_fill` | % | - | - | 80 | 95 | Play back / export the recorder, then `SSRCLEAR` |
| `HK.vc0_backlog`, `HK.vc1_backlog` | octets | - | - | 600 | 900 | Downlink congested: reduce cadence or raise the rate |
| `HK.sdls_auth_fail` | count | - | - | 1 | 10 | 2.8 |
| `HK.sdls_tm_state` | code | - | - | 1.5 (waiting for epoch, unprotected fallback) | - | 2.8 |
| `HK.beacon_level` | code | - | - | 1.5 (IMPORTANT) | 2.5 (URGENT) | 07 §2.4 |
| `RF.radio_cfg_ok` | flag | 0 | - | - | - | EVR 0x0306 FAILED |
| `RF.tx_duty` | % | - | - | 55 | 70 | The flight averages the busy time over whole transmission cycles (launch to next launch; the last 16 cycles started within 300 s, `fsw/datalink.h` `TxDutyMeter`); its frame spacing keeps that at <= 50.0 % in every mode and radio setting (an open cycle counts at least one frame period, so the value decays to 0 while silent). 55 % or more means transmissions took longer than the busy time they were planned with (LBT / CAD deferrals, a slow AUX, timeouts): check EVR 0x0301 and the radio supply |

### 4.8 BEACON

| Level | Text | When | Action |
|---|---|---|---|
| INFO / WARN / ERROR | `spacecraft beacon <old> -> <new>` | HK beacon level changed: INFO when it fell, WARN for IMPORTANT, ERROR for URGENT | 07 §2.4 |
| WARN / ERROR | `spacecraft beacon <level>` | First HK after start with level > NOMINAL | As above |

### 4.9 S/C EVR

`[0x<id>] <text>[ (SSR playback)]` - every downlinked EVR (section 3).

### 4.10 CMD (commanding)

| Level | Text | When | Action |
|---|---|---|---|
| INFO | `#<id> <command> queued by <source>` | Command accepted by the ground and queued | None |
| WARN / INFO | `[HAZARDOUS ]<command>: confirm id <n>[ + 2nd-operator AUTH] (from <source>)` | Confirmation requested (WARN for hazardous) | CONFIRM within 120 s |
| INFO / WARN | `#<id> <command> -> <state> (<detail>)` | CMDVER received: INFO for EXECUTED, WARN for FAILED (TIMEOUT and SUSPENDED show in the command status and the FOP events) | 2.4 |
| INFO / WARN | `stored sequence: opcode 0x<op> <stage>[ (<error>)]` | CMDVER of a stored-sequence command | WARN: read the error (2.4) |
| WARN | `CMDVER with unknown command source <n> (opcode 0x<op>) ignored` | Malformed CMDVER (at most once a minute) | Dictionary mismatch |

### 4.11 FOP (COP-1 sending end)

| Level | Text | When | Action |
|---|---|---|---|
| INFO | `frame period up to <P> s: T1 <t> s, verification timeout <v> s (configured minima <a> / <b> s)` | Slow link: the timers were raised to 2 P and 4 P | None |
| WARN | `cmd #<id> <command>: frame not built (<e>)` | TC frame construction failed (e.g. SDLS SN not persisted) | Look at the SDLS events |
| WARN | `FOP: <n> unacknowledged frame(s) purged (<why>)` | Purge after lockout clearance, transmission limit or a directive | Re-send what is still needed |
| WARN | `FARM LOCKOUT reported - AD service suspended, send UNLOCK` | CLCW shows Lockout | `UNLOCK` |
| WARN | `FARM lockout cleared` | Lockout gone; AD service re-initialised | None |
| WARN | `invalid CLCW N(R)=<n> (expected <a>..<b>) - spacecraft FARM reset or SET V(R) by another station? AD service suspended: resynchronise with SETVR or UNLOCK` | N(R) outside the expected range (spacecraft rebooted) | `UNLOCK` or `SETVR <n>` (07 §2.5) |
| WARN | `FOP suspended: cmd #<id> not acknowledged` | Transmission limit reached | 2.4 |

### 4.12 LINK (coordinated changes, link manager)

| Level | Text | When | Action |
|---|---|---|---|
| INFO | `link manager -> <OFF / ADVISE / AUTO> (<source>)` | `LINKMGR` | None |
| INFO | `link manager AUTO: LINKRATE <code> - <why>` | AUTO starts a change | Watch the change |
| INFO | `coordinated <rate / chan> change to <v> started (<source>)` | Confirmed `LINKRATE` / `LINKCHAN` | 07 §7 |
| WARN | `coordinated <kind> change rejected by the spacecraft` | CMDVER FAILED for the AIRRATE / CHANNEL | Read the error |
| WARN | `coordinated change: no command verification, abandoned` | No CMDVER within max(600 s, 4 x frame period) | Check the uplink; the spacecraft may still switch and revert |
| INFO | `coordinated <kind> change to <v> COMPLETE` | First frame received after both ends switched | None |
| WARN | `no frames after the <kind> change - ground reverting to <old>` | No frame for `revert_s` + 30 s + 2 frame periods after the station switched | The spacecraft reverts too (EVR 0x0602) |

### 4.13 AFC (SX126x)

| Level | Text | When | Action |
|---|---|---|---|
| WARN | `AFC: static offset <x> Hz clipped to +-<b> Hz (80 % of FERR_L)` | `freq_offset_cal_hz` beyond the profile's bound | Re-calibrate |
| WARN | `AFC: no rf_foff confirmation for <x> Hz within 10 s - correction unchanged` | Station MCU did not answer | Check the station sketch / bridge |
| WARN | `AFC: rf_foff(<x>) failed: <out of range / radio down / no answer (bridge)>` | Correction refused | As above |
| WARN | `AFC: unexpected rf_foff confirmation for <x> Hz ignored` | Stray answer | None |
| WARN | `AFC: residual grew after a <s> Hz step (<a> -> <b> Hz) - frequency-error sign convention inverted to <+1/-1> (VERIFY)` | Sign self-check flipped the sign once | Note it; VERIFY item ([08](08_Verification_and_Test.md)) |
| ERROR | `AFC: residual grew again after the sign inversion (<a> -> <b> Hz) - AFC stopped, back to the static offset; check the station frequency reference and reset the AFC` | Second divergence: AFC FAULT (YELLOW) | 2.7 |
| WARN | `AFC: estimate <e> Hz beyond the correction bound +-<b> Hz (80 % of FERR_L) - correction held at the bound` | Offset too large to correct | Calibrate `freq_offset_cal_hz`; TCXOs |
| INFO | `AFC: LOS - holding <x> Hz for 600 s` | LOS | None |
| INFO | `AFC: no signal for 600 s - back to the static offset <x> Hz` | Long LOS | None |
| INFO | `AFC: signal back, tracking resumes from <x> Hz` | AOS within the hold | None |
| WARN | `the radio backend provides no rf_foff: the AFC cannot steer the station (corrections are reported as 'no answer')` | Backend without the RPC | Station sketch too old: rebuild |
| INFO | `AFC reset by <source>: back to the static offset <x> Hz` | `AFC RESET` | None |

### 4.14 CFDP (file receiver)

`<tid>` = `<source entity hex>/<sequence number hex>`, e.g. `01/0002000a`; the origin is `MCU`
or `EGSE` (sequence number bit 31).

| Level | Text | When | Action |
|---|---|---|---|
| INFO | `<tid> receiving '<name>' (<size> octets from the <MCU/EGSE>, checksum type <n>)` | Metadata PDU | None |
| INFO | `<tid> '<name>' COMPLETE: <size> octets, modular checksum <x> verified -> <path>` | File complete | Find it in FILES / `<archive>/files` |
| WARN | `<tid> '<name>' INCOMPLETE: <n> of <size> octets missing in <k> range(s): ...` | EOF with gaps (`.partial` + `.json` saved); late data may still complete it within `check_s` | Re-send (07 §9) |
| WARN | `<tid> '<name>' CHECKSUM_FAILURE: checksum <a> computed, <b> expected` / `...: checksum type <n> not supported (modular only)` | All octets present, checksum wrong | Re-send |
| WARN | `<tid> '<name>' CANCELLED: condition <c> (<name>)[ at entity 0x..] after <n> octets sent` | EOF with a non-zero condition (15 = cancel request) | None if you cancelled |
| WARN | `<tid> '<name>' ABANDONED: no PDU for <t> s (inactivity detected, limit <l> s)` / `...: file size <n> exceeds the receiver limit of <l> octets` / `...: more than 16 transactions open` | Inactivity, size limit (`max_file_mib`) or too many open transfers | Re-send; raise `max_file_mib` if needed |
| INFO | `<tid> late File Data filled every gap` | INCOMPLETE completed within `check_s` | None |
| WARN | `<tid> second Metadata PDU differs from the first: ignored` / `<tid> file name '<a>' sanitised to '<b>'` / `<tid> <n> octets received beyond the Metadata file size (...)` / `<tid> File Data [<a>, <b>) beyond the file size <l> rejected (...)` / `<tid> duplicate File Data at offset <o> with different ...` / `<tid> EOF file size <a> differs from the Metadata file size <b>: the EOF value is used` / `<tid> <n> octets beyond the EOF file size ignored (...)` / `<tid> the Metadata PDU never arrived: saved as '<name>'` | Protocol anomalies (kept and reported) | Report if repeated |
| WARN | `<tid> cannot write '<name>': <e>` / `<tid> cannot write the partial file of '<name>': <e>` | Disk problem | Free space |
| WARN | `malformed PDU dropped (<e>); <n> so far` / `<directive> directive ignored: this receiver is class 1 only` / `acknowledged-mode (class 2) PDU ignored: this receiver is class 1 only` | Bad or unsupported PDU (at most once per 10 s) | Report |
| INFO / WARN | `SSR file <name>: <n> packets archived (partition <p>)[, <k> undecodable][, <m> trailing octets]` | A COMPLETE SSR export decoded into the archive (WARN with undecodable or trailing data) | None |
| WARN | `SSR file <name> not readable: <e>` | Disk problem | Check `<archive>/files` |

### 4.15 TDM

| Level | Text | When | Action |
|---|---|---|---|
| INFO | `pass TDM written: <file> (<n> records)` | At LOS (and shutdown) with `auto_write` | None |
| INFO | `TDM written by <source>: <file> (<n> records)` | `TDM WRITE` / FILES button | None |
| WARN | `pass TDM not written: <e>` / `TDM not written: <e>` | Disk error or invalid records | Disk space |
| WARN | `station clock outside 2000..2100 (NTP not synchronised?): TDM records skipped (<e>)` | Station clock invalid (once per pass) | Synchronise the clock |

### 4.16 MAGCAL

| Level | Text | When | Action |
|---|---|---|---|
| INFO | `[science] field_ut not set: the MAGCAL fit is disabled until it is` | Start without `field_ut` | Set it (07 §5.1) |
| INFO | `unit <u> collection started by <source>: rotate the spacecraft (MAG at a 1 s period, PKTRATE MAG 1: averaged MAG packets are not collected)` | START | 07 §5.1 |
| WARN | `unit <u>: MAG packets average <n> readings and are not collected - command PKTRATE MAG 1 (1 s, unaveraged) while calibrating` | First averaged MAG packet during a collection | `PKTRATE MAG 1`; check the load factor (EVR 0x0215) |
| INFO / WARN | `unit <u> fit OK: residual <r> uT, coverage <k>/26` / `unit <u> fit REFUSED: <reasons>` | FIT | REFUSED: follow the reasons (07 §5.1) |

### 4.17 SPECTRAL (ground spectral analytics)

| Level | Text | When | Action |
|---|---|---|---|
| INFO / WARN | `white reference captured (<mode>, <active illumination / ambient light>, <n> channels)[ - NOT saved to disk: <e>]` | `SPECREF` | WARN: disk |
| INFO | `white reference cleared[ - file not removed: <e>]` | `SPECREF CLEAR` | None |
| WARN | `white reference <file> ignored: <e>` | Start: corrupt reference file | Capture again |
| INFO / WARN | `library entry '<name>' saved` / `replaced` ` (with reflectance)` / ` (spectrum only)`[` - <error>`], `library entry '<name>' deleted` | `SPECLIB SAVE` / `DEL` | WARN: disk |
| WARN | `<library load error>` | Start: corrupt library file | Check `spec_library.json` |
| INFO / WARN | `light-source class ground <a> / onboard <b>: agree again` / `DISAGREE (near a threshold?)` | Ground and on-board classes (dis)agree | Informational (heuristic classes) |
| INFO | `burst t0 = <t> ms: receiving (gain code <g>, N = <n>)` / `burst t0 = <t> ms complete: <n> samples[, <d> s][, <r> Hz measured][ (<r> Hz reported)]` | SPECBURST reception | None |
| WARN | `SPECBURST packet with invalid header or time ignored` | Corrupt SPECBURST | Report if repeated |

### 4.18 ARCHIVE (data logger)

| Level | Text | When | Action |
|---|---|---|---|
| WARN | `free space <f> MB < [gds] archive_min_free_mb <m> MB on <dir>: raw CADU and rx_stats logging PAUSED (frames, packets and events still recorded; it resumes by itself)` | Free space below the floor (YELLOW alarm) | Free space ([18](18_Data_Products.md) §6) |
| INFO | `free space <f> MB: raw CADU and rx_stats logging resumed after <t> s (<n> CADU records and <m> rx_stats rows skipped so far)` | Free space back above floor + 5 % | None |
| INFO | `retention: <n> day folder(s) deleted (<span>) and <m> rx_stats / params / frames rows older than <UTC> - <reason>` | Age (`archive_keep_days`) or size (`archive_max_gb`) prune | None; copy data off the board before it ages out |
| WARN | `archive write failed (<n> so far): <error> - reception and commanding continue, the recording is incomplete` | Disk full or failing (at most once a minute; YELLOW for 60 s) | Free space / check the medium |

### 4.19 EXPORT

| Level | Text | When | Action |
|---|---|---|---|
| INFO | `CSV export <from> .. <to> (<source>): <product> <n> rows, ...` | Export done (FILES tab or API) | Download from FILES |
| WARN | `CSV export (<source>) failed: <Exception>: <e>` | I/O or database error | Disk space; retry |

### 4.20 INTEROP

| Level | Text | When | Action |
|---|---|---|---|
| WARN | `external TC frame rejected (bad header/FECF)` | A frame from `tc_frames_udp_listen` failed parsing | Check the external MCS |
| WARN | `external TC frame not sent: <e>` | Re-wrapping failed (SDLS, size) | As above |

### 4.21 IDENT (station identification)

| Level | Text | When | Action |
|---|---|---|---|
| WARN | `[station] callsign is the placeholder N0CALL: set your amateur callsign in station.toml before transmitting (the uplink identifies with it)` | Start, while `[station] callsign` is `N0CALL` | Set your callsign (09 §1.3) |
| INFO | `station identification (<reason>): DE <callsign> <name>` | The station queued its identification for transmission (`gds.py` `_identify()`, `StationIdent`). The event is logged when the identification is put in the outbox: with the E22 it goes to the module with the next outbox write and is sent at once; with the SX126x it can wait in the bridge's backlog while the station MCU's TX queue is full or while uplinks are held after a survey (4.3). Reasons: `periodic` = the first uplink of a run, then at least every 600 s while it transmits (sent in an uplink window up to max(60 s, 3 frame periods) early, at most 600 s, so two lost downlink frames do not delay it; at SX126x profile 0 that is about every 184 to 323 s); `end` = the uplink has been idle for max(120 s, FOP T1 = max(`[commanding] t1_s`, 2 frame periods)). In an uplink window its air time is booked before the window's CLTUs and it is sent after them (`UplinkWindowPlan`; a CLTU that no longer fits waits for the next window). During LOS it is sent from the housekeeping once the last window's planned transmissions are over, since no uplink window comes; a `periodic` one also comes from the housekeeping, outside any window, when no window has carried it 600 s - 2 s - its own air time after the last one (`_id_overdue()`: more than two frames lost, or a fade shorter than the LOS timeout; it may then collide with one downlink frame). A `WAKE` counts as a transmission. When the GDS stops with an uncovered transmission it sends a closing identification **without** an event, as the first shutdown step - also an identification still waiting in the outbox, or one still last in the SX126x bridge's backlog, which is flushed rather than repeated (`Sx126xBridge.queued_last()`); if uplinks are queued behind an earlier identification, a new one is sent after them. With the SX126x it is recorded as sent only once the station MCU accepted it within `IDENT_FLUSH_S` = 0.6 s, else the ERROR below | None; each one the spacecraft hears adds 1 to RF `cltu_bad` (no EVR, no FDIR or FARM effect) |
| ERROR | `closing identification NOT radiated: the station MCU did not take it within 0.6 s (TX queue full or radio down)` | GDS shutdown with the SX126x station radio: `Runner.close()` waits at most `IDENT_FLUSH_S` = 0.6 s for the station MCU to accept the closing identification and every uplink queued before it (`flush_tx()`; nothing is handed over while the TX gate holds uplinks after a survey, 4.3); it is recorded as sent only once accepted | Identify the station by other means before leaving the frequency; check the station radio (2.2) |

### 4.22 Messages that are not events

The command console answers immediately (not logged as events): `REJECTED: <reason>`,
`usage: ...`, `TC uplink BLOCKED: ...`, `confirmation expired (120 s)`,
`<directive>: scheduled (runs in the next quiet window)`, `HAZARDOUS ...: confirm id <n>`.

The hardline tool `dssq.cltu` (07 §2.6) prints its refusals on standard error, prints no CLTU
and exits with status 1 (`dssq/cltu.py` `build_hardline_cltu()`); the SDLS ones:

| Message | Cause | Action |
|---|---|---|
| `TC uplink BLOCKED: <reason>. Start the GDS on this data folder and let an HK re-synchronise the counter (it writes the SN file), or follow that section; never start a used key at 1` | A key with TC AUTH or AE and the SN file `<archive>/sdls_sn.txt` missing or corrupt (the same hold as the GDS, 4.6); no file is created | Run the GDS on the same data folder until a qualifying HK has released the counter, or write the file by hand ([17_Security_Model.md](17_Security_Model.md) section 7.6) |
| `SDLS: sequence number <n> would be above the time bound <f> (4 per second since 2026-01-01; docs/17_Security_Model.md section 7.6): the station clock is behind, or commands were sent faster than that - check the clock, or wait` | The next number is above the time floor: the station clock is behind, the SN file holds a value above the floor (a hand-written one, or a release by the 1 000 000 gap), or scripted commands came faster than 4 per second | Check the station clock (`date -u`, NTP); otherwise wait until the floor has passed the number, (`<n>` - `<f>`) / 4 s |

### 4.23 Spacecraft EGSE messages (UNO Q App log)

The EGSE (`vgq1-spacecraft/python/main.py`) logs to the App Lab console of the spacecraft App.
The operator-relevant ones:

| Level | Message (abridged) | Meaning | Action |
|---|---|---|---|
| INFO | `VGQ-1 EGSE started: data in <dir>, boot counter <n>` | Start | None |
| INFO | `flight computer boot (reset cause <n> <NAME>)` | MCU boot seen: a new epoch and the SN restore follow | None |
| WARN | `first start or counter lost (boot_count.txt missing): if this flight key was used before, generate a new key (tools/gen_sdls_key.py)` | No boot counter: epochs restart at 1 | With a used key: rotate the key (07 §10.4) |
| ERROR | `<file>: SCLK partitions / SDLS TM epochs are NOT issued. Restore the highest value ever used (never a lower one), or generate a new key ... and delete the file` | Boot counter unreadable: no epoch is issued (the flight falls back to the null SA after 120 s) | 07 §10.5 |
| ERROR | `SDLS TM epoch space exhausted: generate a new key ... and reset data/boot_count.txt` | 65534 epochs used | Rotate the key |
| ERROR | `MCU refused epoch <n> (current <m>): boot counter regressed` / `epoch negotiation failed: check data/boot_count.txt and the flight key` | The MCU refused the offered epochs (EVR 0x0122) | 07 §10.5 |
| ERROR | `<file>: SDLS TC anti-replay counter NOT restored (...); the ground's SN file stays authoritative` | `sdls_last_sn.txt` unreadable | The flight accepts commands after 120 s (EVR 0x0124); restore the file |
| WARN | `<n> EVRs / CADU copies not forwarded by the MCU since its boot (+<k>)` | Notification limits reached | Normal under load; the downlink is unaffected |
| ERROR | `CFDPPUT source <n>: no <pattern> in <dir> - nothing sent (...)` | No EGSE file to send yet | Wait until the EGSE has written one |
| ERROR | `hardline line <n> rejected ...` / `hardline CLTU (<k> octets) not confirmed ...: NOT re-sent` | Bad line in `hardline_queue.txt` or no MCU answer | Rebuild the line with `python -m dssq.cltu`; re-append |

---

## 5 Alarm reasons (dashboard header, OVERVIEW, station ALARM lamp)

The alarm is RED when any RED reason holds, YELLOW when only YELLOW reasons hold, NONE otherwise
([`gds.py`](../dssq-groundstation/python/dssq/gds.py) `_compute_alarm`, once a second).

| Level | Reason text | Meaning | Action |
|---|---|---|---|
| RED | `<packet>.<parameter> RED limit` | A real-time parameter in its red range (4.7) | Per the limit table |
| RED | `LOS` | No frame for the LOS timeout after at least one pass, not hibernating | 2.3 |
| RED | `LOS: no beacon for <t> s while hibernating` | Hibernating and no beacon for more than 7200 s | `WAKE`; 2.3 |
| RED | `beacon URGENT` | HK beacon level 3 | 07 §2.4 |
| RED | `frequency offset > 80 % of FERR_L` | SX126x: median frequency error of the last 3 packets above 80 % of the tolerance | 2.7 |
| RED | `ground radio fault: <fault>` | Station E22-400T37S abnormal status within the last 5 s | Station supply / cooling |
| RED | `radio backend unavailable: <reason>` | No station radio | 2.2 |
| RED | `ground radio link down for <t> s` | Station radio link down for more than 30 s | 2.2 |
| RED | `TM epoch went backwards with a valid MAC: ... (possible GCM nonce reuse) ...` | Epoch regression seen in the last 24 h | 2.8; 07 §10.4 |
| YELLOW | `<packet>.<parameter> YELLOW limit` | Yellow range | Per the limit table |
| YELLOW | `beacon IMPORTANT` | HK beacon level 2 | 07 §2.4 |
| YELLOW | `frequency offset > 50 % of FERR_L` | SX126x frequency error above 50 % of the tolerance | 2.7 |
| YELLOW | `FDIR <flag>,<flag>...` | HK `fdir_flags` other than HIBERNATE and TX_INHIBIT | Section 6 |
| YELLOW | `spacecraft reset cause BROWNOUT (check the radio supply)` | HK `reset_cause` = BROWNOUT | 2.5 |
| YELLOW | `<n> SDLS TM rejects in the last 60 s` | Data frames rejected by TM security | 2.8 |
| YELLOW | `SDLS service mismatch suspected (spacecraft TM without AE header)` | 3 consecutive BAD_SPI / BAD_IV frames | 2.8 |
| YELLOW | `radio model mismatch: spacecraft <a>, station <b>` | RF packet model differs from `[radio] model` | [15](15_Configuration_Reference.md) §2 |
| YELLOW | `ground SX126x hardware mismatch: <text>` | Station sketch chip / TCXO differs from `[radio.sx126x]` | 2.7 |
| YELLOW | `AFC FAULT (frequency-error sign check failed twice): AFC RESET after checking` | AFC stopped | 2.7 |
| YELLOW | `SDLS key file not found: <path>` / `SDLS key file unreadable: <e>` | A service is configured but no key is loaded | 07 §10 |
| YELLOW | `TC uplink BLOCKED: <reason>` | Only while SDLS protects the uplink (a key loaded and TC AUTH or AE): the ground SDLS SN file is corrupt or missing (4.6), and the uplink is held until an HK made at least 120 s after a flight boot (AE-protected when `tm_service` is AE) re-synchronises the counter onto the time floor. Without a key or with TC NONE a corrupt file raises no alarm | 2.4 |
| YELLOW | `archive logging paused: free space <f> MB < [gds] archive_min_free_mb <m> MB (raw CADU, rx_stats)` | Free-space guard active | 2.9 |
| YELLOW | `archive write errors in the last 60 s (<error>)` | Archive writes failing | 2.9 |
| YELLOW | `GDS stage errors in the last 60 s (health.stage_errors)` | Internal exception caught | 2.2 |

The station annunciator mirrors the alarm: ALARM lamp 4 Hz blink for RED, steady for YELLOW,
1 / 2 / 3 Hz blink for a beacon INTERESTING / IMPORTANT / URGENT; the buzzer sounds 400 ms on
entering RED or at LOS and chirps 2 / 3 times when the beacon rises to IMPORTANT / URGENT
([06_Displays.md](06_Displays.md)).

---

## 6 Fault-protection (FDIR) flags - `HK.fdir_flags`

"Response" = what the flight software does; the responses marked "masked by FDIRMASK bit" are
disabled when that bit of `FDIRMASK` is 0 (boot value 0xFFFF = all enabled). "URGENT edge" =
the flag rising raises the beacon to URGENT.

| Bit | Flag | Set when | Cleared when | Response / EVR | URGENT edge |
|---|---|---|---|---|---|
| 0 | RADIO_CFG | Radio configuration failed, SX126x fault, hibernation radio change failed | Next successful configuration / recovery | EVR 0x0306 FAILED, 0x0304 | yes |
| 1 | AUX_TIMEOUT | A transmission timed out | Next good transmission | EVR 0x0301 | yes |
| 2 | PA_OVERTEMP | E22-400T37S over-temperature report | 3 s after the reports stop | Frame period doubled; transmitter off 300 s + SAFE (masked by FDIRMASK bit 2); EVR 0x0313/0x0314, 0x0604 | yes |
| 3 | AVI_OVERTEMP | Bus temperature >= 75 degC | Below 70 degC, or the reading becomes n/a | Flag only (no EVR); HK `bus_temp` limits | yes |
| 4 | CMD_LOSS | Command-loss timer expired | **Latched**: `RSTCNT` or a reboot | SAFE + default radio (masked by bit 4); EVR 0x0601 | yes |
| 5 | I2C_BUS | More than 10 consecutive bus errors (with bit 5 set) | 10 s without a new bus error | Bus clear + re-init; EVR 0x0605, 0x0609 | yes |
| 6 | SENSOR_LOST | Nicla Sense ME stopped responding | It responds again | EVR 0x0606 / 0x0607 | no (an instrument loss raises IMPORTANT) |
| 7 | LOOP_OVERRUN | A loop pass took more than 250 ms (`HK.loop_overruns` > 0) | **Latched**: `RSTCNT` (zeroes the counter and clears the flag) or a reboot | Flag only | no |
| 8 | VC_CONGEST | VC0 or VC1 backlog > 640 octets | Both < 300 octets | Real-time science recorded only, not sent (masked by bit 8) | no |
| 9 | LOW_BUS_V | E22-400T37S under-voltage report | 3 s after the reports stop | EVR 0x0311 | yes |
| 10 | FARM_LOCKOUT | FARM-1 in Lockout | Lockout cleared (`UNLOCK`) | EVR 0x0205 | yes |
| 11 | TX_INHIBIT | `TXINHIBIT` or PA cool-down | Inhibit time over | EVR 0x0603 at the end (not counted in the YELLOW alarm) | no |
| 12 | RATE_REVERT | A rate / channel change reverted | **Latched**: `RSTCNT` or a reboot | EVR 0x0602 | no |
| 13 | SDLS_AUTH | A TC frame failed SDLS | **Latched**: `RSTCNT` or a reboot | EVR 0x0112..0x0116 | no |
| 14 | HIBERNATE | Hibernating (informational) | Wake | EVR 0x0800 / 0x0801 (not counted in the YELLOW alarm) | no |
| 15 | RADIO_FAULT | E22-400T37S abnormal-status report | 3 s after the reports stop | EVR 0x0311..0x0314, 0x0315 | yes |

`FDIRMASK` is hazardous: disabling a response (for example the command-loss response during a
long bench test) must be undone before flight-like operation.
