# 07 - Operations Procedures

How to run the VGQ-1 spacecraft and the DSS-Q1 ground station once they are built and
installed. First-time installation (parts, flashing, importing the two App Lab Apps, wiring)
is in [00_Beginner_Setup_Guide.md](00_Beginner_Setup_Guide.md).

Conventions used in every procedure:

* Each step is numbered; a **Check** line says what you must see before you go on. If the
  check fails, go to the reference given (mostly
  [14_Troubleshooting_and_Event_Response.md](14_Troubleshooting_and_Event_Response.md), "14").
* `COMMAND args` is typed exactly like that on the CardKB or in the COMMANDING tab of the web
  dashboard (both use the same syntax). `HELP` lists the commands, `HELP <command>` explains
  one. The full command dictionary is
  [05_Telemetry_and_Command_Dictionary.md](05_Telemetry_and_Command_Dictionary.md).
* Dashboard tabs: OVERVIEW, RECEIVER, LINK & SECURITY, FRAMES & PACKETS, SPACECRAFT,
  SPECTROMETER, SCIENCE, MAGCAL, SEQUENCES, FILES, COMMANDING
  ([13_Web_UI_Reference.md](13_Web_UI_Reference.md)).
* "EVR 0x0306" = a spacecraft event report (catalogue: 14 §3). Ground events, alarms and
  FDIR flags: 14 §4 to §6. Settings: [15_Configuration_Reference.md](15_Configuration_Reference.md).

> ⚠ Never transmit without an antenna or a dummy load on the radio. Use only the channels and
> power your licence and band plan allow ([09_Regulatory_and_Safety.md](09_Regulatory_and_Safety.md)).
> Encryption (SDLS AE) is generally not permitted on amateur frequencies: use AUTH or NONE there
> (§10.3).

---

## 1 Start-up

### 1.1 Ground station, App Lab mode (normal)

1. Connect the station antenna to the radio. Power the station radio (E22-400T33D: 5 V,
   at least 2 A, or the board-powered option of [03_Hardware_Integration.md](03_Hardware_Integration.md)).
   **Check:** the USB-UART is present: `ls /dev/serial/by-id/` on the VENTUNO Q.
2. Power the VENTUNO Q. In Arduino App Lab open the App **DSS-Q Ground Station** and press
   Run (it builds and loads the station I/O sketch and starts the Python ground data system).
3. **Check (App Lab log):** `DSS-Q ground station running: dashboard on port 8080 (bound to
   0.0.0.0); archive /app/data/archive; commanding token in ...`. On the very first start the
   log also prints `DSS-Q web commanding token created in /app/data/http_token: <token>`.
   The token stays in `/home/arduino/ArduinoApps/dssq-groundstation/data/http_token`.
   If the App stops at once, the log names the bad `station.toml` key (14 §2.2).
4. Open `http://<VENTUNO Q address>:8080/` in a browser on the station LAN. Press TOKEN in the
   header and enter the token (kept for this browser session only).
   **Check:** the page loads. If it does not but `curl http://localhost:8080/api/state` works on
   the board, App Lab does not publish the port on your LAN: use host mode (§1.2).
5. **Check (OVERVIEW, Latest events):**
   * `GDS DSS-Q ground station 3.0.0 started (applab), radio E22-400T33D CH23 code 2; SDLS TC AE / TM AE; ...`
     - model, channel, rate code and SDLS services are what you intend;
   * `WEB dashboard on http://0.0.0.0:8080/ (LAN-exposed, token required for commanding)`;
   * `RADIO ground E22-400T33D: ch 23 (433.125 MHz) rate 2.4k pwr 0 LBT/CAD False - matches station.toml`
     (needs the Radio Control Unit; a `MISMATCH` here: 14 §2.2);
   * no `SDLS key file not found` warning when AE or AUTH is configured;
   * no `IDENT [station] callsign is the placeholder N0CALL ...` warning: set your own
     callsign in `[station] callsign` before you transmit (§2.8).
6. **Check (header and panel):** `LOS`; alarm NONE (YELLOW only for a reason you understand,
   OVERVIEW Alarm reasons). Station panel: no fast-flashing ALARM lamp (that means "GDS link
   down"). Type a few characters on the CardKB: they appear in the COMMANDING tab.

### 1.2 Ground station, host (systemd) mode

Host mode runs the same ground data system directly on the VENTUNO Q's Linux, for an always-on
station or when App Lab does not publish port 8080 on the LAN. **Never run the App and the
service at the same time** (they would fight over the radio port, the web port and the Router
Bridge names).

1. Once: run the installer `dssq-groundstation/host/install_station.sh` (add `--service` to
   install and start `dssq-gds.service` at boot). It needs the internet once, to pip-install
   `arduino-router-bridge==0.5.1` into a virtual environment for the front panel, the Radio
   Control Unit and the SX126x radio; the App Lab path needs no download (that package is
   already in App Lab's image).
2. Configuration: as shipped
   ([`host/dssq-gds.service`](../dssq-groundstation/host/dssq-gds.service)) the unit runs
   `python -m dssq.gds --app-data <App>/data` in the App's `python/` folder, so host mode uses
   the App's own `data/station.toml`, archive, SDLS key and web token, exactly like App Lab
   (the dashboard listens on all interfaces; commanding needs the token).
3. `sudo systemctl start dssq-gds`, then `journalctl -u dssq-gds -f`.
   **Check:** the same events as §1.1 step 5.
4. Manual run without systemd (stop the service first): from `dssq-groundstation/python`,
   `python3 -m dssq.gds --app-data ../data` (the same files as the service and the App). A
   stand-alone run with `python3 -m dssq.gds --config config/station.toml` uses that file and
   an archive relative to the working directory instead. A second archive means a second SDLS
   TC sequence-number file. At its first start with the flight key the GDS warns (`no SDLS TC
   sequence-number file <file> for the key <key file>: ...`) and, with every TC service that
   numbers its frames (AUTH or AE) and any TM service, holds the uplink until an HK reports the
   spacecraft's `sdls_last_sn` (an SDLS-protected HK when `tm_service` is AE; one made at least
   120 s after the spacecraft's boot); it then continues on the time floor, 4 numbers per
   second since 2026-01-01 (WARN `SDLS TC sequence number re-synchronised to <n> (the time
   floor: 4 per second since 2026-01-01; sdls_last_sn <m>) from ...: TC uplink released`).
   Switching back to the first archive can still reuse numbers the other one radiated. Keep one
   archive per key (§10.5).

### 1.3 Spacecraft

1. Connect the spacecraft antenna. Check the radio supply and the model strap on header A0
   (open = E22-400T33D, jumper to GND = E22-400T37S, §7.8).
2. Power the UNO Q. App Lab runs the **vgq1-spacecraft** App: the flight sketch on the MCU and
   the EGSE in Python on the Linux side.
3. **Check (UNO Q App Lab log):** `VGQ-1 EGSE started: data in /app/data, boot counter <n>`,
   `flight computer boot (reset cause <n> <NAME>)`, then the EGSE status board every 10 s and
   the boot EVRs in this order: 0x0308 radio model, 0x0306 `radio E22-400T33D OK: ch 23 code 2
   pwr 33 dBm ...`, 0x0910 / 0x0911 RM3100 self-test, 0x0706 AS7265X identity, 0x0011
   `MODE BOOT -> SAFE (boot)`, 0x0001 `BOOT VGQ-1 FSW 3.0 ...`, 0x0003 `SSR capacity 256 KiB`
   (less only when the heap is short; minimum 16 KiB), 0x0004, 0x0002 `DE <callsign>` (it
   must show **your** callsign, not `N0CALL`: set `VGQ_CALLSIGN` in `config.h` and rebuild,
   §2.8), and with SDLS AE 0x0120 `SDLS TM epoch <n>: downlink protected` once the EGSE has
   answered. Any WARN or ERROR in this list (e.g. 0x0608
   brownout derating, 0x0707 AS7265X ABSENT, 0x060A I2C bus clear): 14 §3.
4. A boot counter error in the EGSE log means no epoch is issued: stop and follow §10.5.

### 1.4 First contact (every pass after a spacecraft boot)

1. Wait for the first frame. **Check:** event `RX AOS`; frame counters move (FRAMES & PACKETS);
   LINK & SECURITY shows the identification `DE <callsign> VGQ-1` from the OID frames.
2. With SDLS AE, the data frames start within about 120 s of the spacecraft boot (they wait for
   the EGSE epoch). **Check:** LINK & SECURITY: `tm_frames_ok` increasing, spacecraft TM state
   PROTECTED. Unprotected frames after 120 s: 14 §2.8.
3. The first HK shows the beacon **URGENT** and the alarm goes RED (`beacon URGENT`). This is
   expected after every boot (14 §2.1).
4. Send `MODE CRUISE` (or `MODE SAFE` to stay in safe mode).
   **Check:** command status QUEUED -> RADIATED -> ACCEPTED -> EXECUTED; EVR 0x0012
   `MODE SAFE -> CRUISE (ground command)`; beacon NOMINAL; the RED alarm clears. A first command
   refused with EVR 0x0123 means the EGSE has not restored the anti-replay counter yet: wait and
   repeat.
5. `PING 1`. **Check:** EXECUTED; the round-trip time is shown in the command history.
6. **Check (within 300 s in SAFE / CRUISE):** a TIMECORR packet arrives and SCET times appear
   (TIME panel). Command `TIMECORR` to get one sooner.

### 1.5 Practising without hardware

The ground data system contains a software spacecraft that answers almost every command
(MODE, AIRRATE / CHANNEL with revert, PKTRATE, PLAYBACK, HIBERNATE / WAKE, SPECCFG / SPECMODE
incl. bursts, CFDPPUT SSR, SEQLOAD / SEQCTRL, MAGBIST, MAGCAL with a known hard- and soft-iron
truth). Every procedure in this document except the EGSE-dependent ones (CFDPPUT EVRLOG /
CADU, hardline) can be rehearsed:

* host: `cd dssq-groundstation/python && python3 -m dssq.gds --sim` (dashboard on
  `http://127.0.0.1:8080/`);
* App Lab: environment variable `DSSQ_SIM=1` for the ground App.

The simulator keeps its SDLS TC counter and TM replay state in memory, with or without an
archive (also `--sim --app-data` and `DSSQ_SIM=1`): it never reads or writes the station's real
`sdls_sn.txt` and `sdls_tm_replay.txt`, so a practice session neither ends nor disturbs a hold of
the real counter (`dssq/gds.py` `GroundStation`, `keep_security_state`).

---

## 2 Routine commanding

### 2.1 Typing commands

* Syntax: `MNEMONIC arg arg ...`, case-insensitive, numbers decimal or `0x` hex, enumeration
  names where the dictionary defines them: `MODE ENCOUNTER` (or `ENC`), `PKTRATE MAG 10`,
  `SPECCFG AUTO 59 0 0 0`, `MAGBIST BOTH`, `CFDPPUT SSR 0`, `EVRLEVEL WARN`.
* Every command is checked against the dictionary (names, ranges, cross-argument rules) before
  it can be queued; a refusal is shown at once (`REJECTED: ...`).
* `BD <command>` sends it as a bypass (Type-BD) frame that ignores the FARM state (use it when
  the AD service is locked out or suspended). `UNLOCK` and `SETVR <n>` are the COP-1
  directives (§2.5).
* CardKB: the line you type is echoed to the dashboard; Enter sends it; an **empty Enter
  confirms** the pending hazardous command shown on the panel; `KEYTEST` + Enter echoes raw
  key codes (again, or ESC, to leave).
* The station uplinks only in the quiet window right after it has received a spacecraft
  frame (the link is half-duplex). **No downlink means no uplink**: commands stay QUEUED during
  LOS. The two exceptions are `WAKE` (§6) and the station identification, which is also sent
  during LOS once the last window's transmissions have ended (§2.8).

### 2.2 Hazardous commands and confirmation

The 8 hazardous commands: `AIRRATE`, `CHANNEL`, `CMDLOSS`, `FDIRMASK`, `HIBERNATE`, `REBOOT`,
`SSRCLEAR`, `TXINHIBIT`. The ground directives `LINKRATE`, `LINKCHAN` and `SDLS RESETREPLAY`
ask for the same confirmation.

1. Type the command. **Check:** console `HAZARDOUS <command>: confirm id <n>`; the panel shows
   the pending id.
2. Confirm within **120 s**: empty Enter on the CardKB, or CONFIRM in the dashboard's pending
   strip.
3. With `[commanding] require_two_person_for_hazardous = true` a second operator presses AUTH
   in the dashboard (it asks for the second operator's secret when `[commanding] auth_token`
   is set; the secret is never stored).

The MAGCAL uplink and a stored-sequence upload also ask for one CONFIRM (not hazardous).

### 2.3 Following a command

States: QUEUED -> RADIATED (n transmissions) -> ACCEPTED (the spacecraft's FARM acknowledged
the frame in its CLCW) -> EXECUTED or FAILED (CMDVER). TIMEOUT = no verification within the
verification timeout; SUSPENDED = the FOP stopped after `tx_limit` transmissions. Watch it in
OVERVIEW **Command status** and the COMMANDING history. FAILED carries an error code (14 §2.4).

### 2.4 Acknowledging the spacecraft beacon

The HK `beacon_level` summarises what happened since the last command the spacecraft
accepted (Deep Space 1 beacon-monitor idea): NOMINAL, INTERESTING (science event, sequence
complete), IMPORTANT (a WARN or worse EVR, an instrument lost), URGENT (fault protection
acted, SAFE by boot or fault protection, brownout, recorder overflow, sequence stopped). It only
rises until the next accepted command.

1. Read the reason: SPACECRAFT tab EVRs since the beacon rose; OVERVIEW Alarm reasons; HK FDIR.
2. Act on it (14 §3).
3. Any accepted command acknowledges the beacon. A SAFE caused by boot or fault protection
   holds it at URGENT until a `MODE` command: send `MODE CRUISE` (or `MODE SAFE`).
   **Check:** beacon NOMINAL; event `BEACON spacecraft beacon URGENT -> NOMINAL`.

### 2.5 COP-1: lockout and resynchronisation

| Situation | Indication | Action |
|---|---|---|
| FARM lockout | EVR 0x0205, FOP `FARM LOCKOUT reported`, FDIR FARM_LOCKOUT | `UNLOCK`. **Check:** EVR 0x0201, FOP `FARM lockout cleared`; re-send what was purged |
| Spacecraft rebooted or another station reset V(R) | FOP `invalid CLCW N(R)=...`, AD commands not radiated | `UNLOCK` (the FOP takes V(S) from the next CLCW) or `SETVR <n>` |
| AD service suspended (transmission limit) | FOP `FOP suspended: cmd #n not acknowledged` | Restore the uplink (14 §2.4), then re-send |

Both directives purge the commands still waiting for acknowledgement. Bypass frames are
confirmed through the CLCW's 2-bit FARM-B counter: send at most three `BD` commands before a
CLCW comes back.

### 2.6 Hardline (umbilical) commanding on the bench

Without RF, a command can reach the flight computer through the spacecraft EGSE; it goes
through exactly the same CLTU -> frame -> SDLS -> FARM-1 -> dispatcher path.

1. On the VENTUNO Q (same key and SDLS sequence-number file as the GDS):
   `cd /home/arduino/ArduinoApps/dssq-groundstation/python && python3 -m dssq.cltu "MODE CRUISE" --app-data ../data`
   The same command serves App Lab and host mode (both use `../data`: its `station.toml`, key
   and archive SN file). Only a stand-alone `--config` station uses
   `python3 -m dssq.cltu "MODE CRUISE" --config config/station.toml`. It prints one hex line
   (a BD frame on VC0). With a key and TC AUTH or AE it needs the station's SN file
   (`<archive>/sdls_sn.txt`): when the file is missing or corrupt it prints `TC uplink BLOCKED:
   ...` (naming [17_Security_Model.md](17_Security_Model.md) §7.6), creates no file and exits
   with status 1. A new key has no SN file until the GDS has received its first qualifying HK
   with it (one the spacecraft made at least 120 s after its boot): run the GDS on the same data
   folder until then (it writes the file), or write the file by hand as 17 §7.6 describes. It
   also refuses (exit status 1) a number above the time floor, 4 per second since 2026-01-01:
   `SDLS: sequence number <n> would be above the time bound <f> (4 per second since 2026-01-01;
   docs/17_Security_Model.md section 7.6): the station clock is behind, or commands were sent
   faster than that - check the clock, or wait`. Check the station clock (`date -u`), or wait
   until the floor has passed the number.
2. Append that line to `/home/arduino/ArduinoApps/vgq1-spacecraft/data/hardline_queue.txt` on
   the UNO Q, the hex digits only, **without** angle brackets (in the shell `<` and `>` are
   redirections: `echo <EB90...> >> file` fails with `syntax error near unexpected token '>>'`):
   `echo EB9020A700110010C0B4C00000040600228E001E3D5D55555554C5C5C5C5C5C5C579 >> ~/ArduinoApps/vgq1-spacecraft/data/hardline_queue.txt`
   (that line is `PKTRATE SPEC 30` without a key). The EGSE takes the file over by itself; a
   missing or empty file afterwards is normal.
3. **Check:** UNO Q console `hardline CLTU <n> octets -> flight command path`, then the status box
   `CMD acc` up by one; at the ground CMDVER with source BD/HARDLINE (record 2026-10-13-A:
   `PKTRATE SPEC 30` accepted, the first SPEC packet on VC1 within 15 s). `hardline line <n>
   rejected: ...` = a damaged line: generate it again and copy it exactly. A hardline command
   does not confirm a radio change (§7.1) and cannot be told apart from an RF `BD` command of
   the same opcode in the command history.

### 2.7 Routine command table

| Goal | Command | Notes |
|---|---|---|
| Link check | `NOOP` | |
| Round trip | `PING <tag>` | The tag comes back in CMDVER |
| Mode | `MODE SAFE` / `CRUISE` / `ENCOUNTER` / `TEST` | Cadence per mode (§3); also ends hibernation |
| Packet cadence | `PKTRATE <HK/RF/TIMECORR/MAG/ATT/SPEC/ENV> <s>` | 1..3600 s; **0 = off**. The override stays in force across mode changes until a reboot: to undo it, give the mode-table value (§3) |
| Slower downlink | `FPERIOD <ms>` | Minimum frame period 500..60000 ms; `FPERIOD 500` = automatic again (below the 1.5 s floor) |
| EVR verbosity | `EVRLEVEL DIAG/INFO/WARN/ERROR/FATAL` | Boot: INFO |
| Time correlation now | `TIMECORR` | After the next reference frame |
| Recorder | `PLAYBACK START` / `PLAYBACK STOP`; `SSRCLEAR` (hazardous) | Playback on VC2; BUSY during an SSR file export |
| Instruments on/off | `SENSORS <mask>` | Bits: 0x0001 Nicla Sense ME, 0x0080 AS7265X, 0x0100 RM3100 outboard, 0x0200 RM3100 inboard; 0x0381 = all |
| RM3100 resolution | `MAGCC <50..400>` | Boot 200; higher = less noise, slower |
| Counters | `RSTCNT` | Command, TC, CLTU and AUX time-out counters, loop statistics, and the event-latched FDIR flags CMD_LOSS, LOOP_OVERRUN, RATE_REVERT, SDLS_AUTH (the YELLOW `FDIR` alarm for them clears; state-driven flags stay) |
| Transmit power | `TXPWR <0..3>` | T33D 33 / 30 / 27 / 24 dBm (codes 1..3 VERIFY); T37S always 37 dBm; SX126x +22 / +17 / +14 / +10 dBm + module gain |
| Radio options | `RADIOOPT <flags>` | b0 LBT / CAD before transmitting, b1 Rx boosted gain (SX126x). **Both bits are set at once**: boot state = boost on, LBT off = `RADIOOPT 2`; LBT on and boost kept = `RADIOOPT 3` |
| Noise survey | `RFSCAN <first> <last>` | Downlink pauses during the survey (§7.3) |
| Reboot | `REBOOT 0xB007` (hazardous) | After 2 more frames, at the latest after max(2 frame periods + one transmission, 10 s) |
| Command-loss timer | `CMDLOSS <s>` (hazardous) | 0..604800 s, 0 = off; boot 86400 s |
| FDIR responses | `FDIRMASK <mask>` (hazardous) | Bit = FDIR flag (14 §6); 0xFFFF = all enabled (boot) |
| Silence | `TXINHIBIT <s>` (hazardous) | 10..3600 s, re-enables itself (§7.7) |

### 2.8 Station identification (callsign at both ends)

Amateur rules require every transmitter to identify itself in plain language at short
intervals and at the end of a contact ([09_Regulatory_and_Safety.md](09_Regulatory_and_Safety.md)
§1.3). Both ends do it automatically, each with its own callsign; both default to the
placeholder `N0CALL`, which is not a licensed callsign.

| End | Setting | What it sends | When |
|---|---|---|---|
| Spacecraft | `config.h` `VGQ_CALLSIGN` (rebuild) | EVR 0x0002 `DE <callsign>`, and the clear-text OID idle frames `DE <callsign> VGQ-1 ` (never encrypted) | EVR at boot and every `kCallsignPeriodS` = 600 s; OID frames whenever there is no data to send |
| Station | `station.toml` `[station] callsign` (restart) | `DE <callsign> <name>` as plain ASCII through the uplink radio path (`<name>` = `[station] name` reduced to letters, digits, `-`, `_`, `.`, at most 16) | After the first uplink transmission of a run; then at least every 600 s while the station keeps transmitting (in an uplink window up to max(60 s, 3 frame periods) before the 10 min are up, so two lost downlink frames do not delay it; if no window has carried it 600 s - 2 s - its air time after the last one, from the housekeeping without a window); once more when the uplink has been idle for max(120 s, FOP T1 = max(`[commanding] t1_s`, 2 frame periods)); during LOS from the housekeeping, as no uplink window comes; and when the GDS stops with an unidentified transmission or an identification still in the outbox. A `WAKE` counts as a transmission |

1. Before the first transmission: set both callsigns. **Check:** EVR 0x0002 shows yours; no
   `IDENT ... placeholder N0CALL` warning at the GDS start.
2. While commanding: **Check:** event `IDENT station identification (periodic): DE <callsign>
   <name>` after the first command, and LINK & SECURITY, uplink table, row "Station
   identification" (text, number sent, last time). A pass ends with `... (end): ...`.
3. Expected side effect: each identification the spacecraft hears is not a CLTU, so its RF
   `cltu_bad` counter rises by one (no EVR, no FDIR or FARM-1 effect).

---

## 3 Modes and downlink cadence

Seconds between periodic packets per mode
([`fsw/datalink.cpp`](../vgq1-spacecraft/sketch/src/fsw/datalink.cpp) `kModeCadence`), and the
frame period factor:

| Mode | HK | RF | TIMECORR | MAG | ATT | SPEC | ENV | Frame period |
|---|---|---|---|---|---|---|---|---|
| SAFE | 30 | 60 | 300 | off | off | off | off | 4.0 x busy |
| CRUISE | 60 | 60 | 300 | 30 | 60 | 300 | 120 | 2.5 x busy |
| ENCOUNTER | 20 | 30 | 120 | 4 | 8 | 30 | 30 | 2.0 x busy |
| TEST | 10 | 10 | 60 | 4 | 8 | 30 | 30 | 2.0 x busy |

* Frame period = max(busy time x factor, 2 x busy time (50 % duty limit), 1.5 s, `FPERIOD`).
  Busy time of one frame: E22 at 2.4 kbit/s 1267 ms (frame period SAFE 5.07 s, CRUISE 3.17 s,
  ENCOUNTER 2.53 s); SX126x profile 1 17.3 s (SAFE 69 s).
* The spacecraft stretches every period at run time so the periodic packets use at most 75 %
  of the measured link capacity; EVR 0x0215 reports the factor when it changes. A factor above
  x1.00 means the cadence you asked for does not fit the link.
* `PKTRATE` overrides one packet in every mode until the next reboot.

---

## 4 Stored command sequences

The spacecraft holds a table of 32 time-tagged commands (RAM only, empty after every boot).

**Sequence file format** (one command per line, `#` starts a comment):

```
# bench demo
T+00:00:10 NOOP                     # relative to SEQCTRL START
T+00:00:30 SPECMODE BURST 5
T+00:01:00 SPECMODE SURVEY 1
T+00:02:00 PKTRATE MAG 30
T+00:02:10 MODE mode=CRUISE         # named arguments also work (all named or all positional)
SCLK 86400 PING 77                  # absolute: spacecraft clock, s since the MCU booted
```

Rules (checked by the ground and again on board): at most 32 entries; relative times up to 7
days; absolute times in the future (against the ground's SCLK estimate) and at most 7 days
ahead; a stored command is at most 12 octets (so `MAGCAL` cannot be stored); hazardous commands,
`REBOOT`, `SEQLOAD` and `SEQCTRL` are refused.

1. SEQUENCES tab: paste the file, choose the first slot, press CHECK (nothing is uplinked).
   **Check:** the table lists every entry with its encoded command; an error names the line.
2. UPLINK (two-step) and CONFIRM once: one `SEQLOAD` per entry is queued in file order.
   **Check:** EVR 0x0900 `SEQ slot n loaded: ...` for every slot; HK `seq_count`.
3. Arm: `SEQCTRL START 0`. **Check:** EVR 0x0902 `SEQCTRL START`; HK `seq_state` ARMED,
   `seq_next_dt_s` counting down. Refused with `fault protection holds SAFE`: send
   `MODE CRUISE` first.
4. Execution: each entry gives EVR 0x0904 and a CMDVER with source STORED (event
   `CMD stored sequence: opcode 0x.. EXECUTED`). The last one gives EVR 0x0906
   `sequence COMPLETE` (beacon INTERESTING).
5. Control: `SEQCTRL STOP 0` (disarm, entries kept; a later START re-times the relative
   entries), `SEQCTRL CLEAR 0` (empty the table), `SEQCTRL DELETE <slot>`.

Sequences keep running during hibernation. An entry more than 300 s late is skipped (EVR
0x0905). Fault protection entering SAFE stops the sequence (EVR 0x0907, entries kept).

---

## 5 Instrument procedures

### 5.1 Magnetometer calibration (MAGCAL)

**What it does.** Nearby iron and currents shift and distort what the boom magnetometers
(RM3100) read. Rotating the spacecraft in a constant field lets the ground fit an ellipsoid to
the readings and compute a hard-iron offset and a soft-iron matrix; uplinked with `MAGCAL`,
the flight software applies `B = M (B_raw - b)` to the **attitude** solution (TRIAD). The MAG
science packets stay raw (their flag CAL says a calibration is active).

**Why it starts with `PKTRATE MAG 1`.** A MAG packet holds the mean of the 1 Hz readings taken
since the previous MAG packet. While the spacecraft turns, a mean lies inside the true
ellipsoid and would bias the fit, so the ground collects only packets with exactly one reading
(`nsamples = 1`), i.e. a 1 s MAG cadence.

**Preconditions (check them before step 1):**

| # | Precondition | How to check / do it |
|---|---|---|
| P1 | `[science] field_ut` holds the site's total field from the World Magnetic Model (1..800 uT) | MAGCAL tab shows the field; otherwise edit `station.toml` and restart (the fit is refused without it) |
| P2 | The link can carry one MAG packet per second: with TM + SDLS AE that needs E22 rate code >= 3 (4.8 kbit/s) or SX126x profile >= 6. At 2.4 kbit/s the spacecraft stretches MAG to 2 s and every packet is averaged (computed with the flight load plan: stretch x1.41 to x1.67) | Do the calibration on the bench at short range; change the rate first with `LINKRATE 3` or `LINKRATE 4` (§7.1) |
| P3 | Spacecraft in CRUISE, ENCOUNTER or TEST (SAFE has the slowest frame period) | HK mode |
| P4 | The RM3100s pass their self-test | `MAGBIST BOTH` -> EVR 0x0910 / 0x0911 `... BIST PASS X ok Y ok Z ok`; HK `mag_bist` |
| P5 | A magnetically quiet place: the spacecraft can be turned in all directions away from steel, magnets, motors and current-carrying cables | - |

**Procedure (unit 0 = outboard, unit 1 = inboard):**

1. `PKTRATE MAG 1`. **Check:** EXECUTED.
2. `PKTRATE SPEC 0` - no spectrometer cycle (and no lamp near the magnetometers) during the
   rotation; it also frees link capacity. **Check:** EXECUTED.
3. **Check:** SCIENCE tab MAG `Samples` = 1 on each packet; no new EVR 0x0215 with a factor
   above x1.00.
4. MAGCAL tab, unit 0: START. **Check:** event `MAGCAL unit 0 collection started ...`.
5. Turn the spacecraft slowly and smoothly through as many orientations as you can: each axis
   up and down, tilted, diagonals - the whole sphere, not one plane (a planar or conical motion
   is refused as ill-conditioned). Samples closer than 1 uT to the previous one are dropped as
   duplicates, so keep moving.
   **Check (live):** coverage of at least 13 of 26 direction bins (aim for 20 or more) and at
   least 50 samples; `averaged skipped` stays 0 (a single increment when a reading is delayed
   by a transmission is harmless; if it grows with every packet, P2 is not met - event WARN
   `MAGCAL unit 0: MAG packets average <n> readings ...`).
6. STOP, then FIT.
   **Check:** `MAGCAL unit 0 fit OK: residual <r> uT, coverage <k>/26`. The fit is refused
   unless: at least 50 samples; condition number <= 1000; ellipsoid axis ratio <= 4;
   coverage >= 13/26; residual RMS <= 5 % of the field; coefficients inside the MAGCAL ranges
   (offset <= 300 uT, diagonal 0.5..2.0, off-diagonal <= 0.5). REFUSED: read the reasons,
   START again (samples are kept until RESET) and cover the missing directions.
7. UPLINK (two-step), CONFIRM. **Check:** CMDVER EXECUTED; EVR 0x0912
   `MAGCAL OB b=<x>,<y>,<z> M=<m11>,...` with exactly the numbers of the uplinked command
   (shown in the MAGCAL tab); HK `magcal_state` outboard active; MAG flags `CAL`. EVR 0x0913
   = refused on board (reason in the text).
8. Repeat steps 4 to 7 for unit 1 if the inboard RM3100 is fitted.
9. Restore the cadence: `PKTRATE MAG 30` and `PKTRATE SPEC 300` in CRUISE (ENCOUNTER / TEST:
   `PKTRATE MAG 4`, `PKTRATE SPEC 30`), and the link rate if you changed it.
10. Record the command text and the fit result ([21_Test_Records.md](21_Test_Records.md)).

The calibration is RAM only (EVR 0x0004 at every boot): re-send the recorded `MAGCAL` command
after every spacecraft boot. CLEAR ONBOARD sends the identity (EVR 0x0912
`MAGCAL OB cleared (identity)`).

### 5.2 Magnetometer self-test (MAGBIST)

`MAGBIST OB`, `MAGBIST IB` or `MAGBIST BOTH`. The test runs in the next quiet moment (no
transmission, no lamp lit). **Check:** EVR 0x0910 / 0x0911 `RM3100 OB BIST PASS X ok Y ok Z ok
(reg 0x..)`; HK `mag_bist` tested and pass bits set. FAIL, TIMEOUT or BUS ERROR: 14 §2.6.
FAILED with code 5: the unit is not fitted; code 7: a self-test is already pending.

### 5.3 Spectrometer (AS7265X)

The AS7265X measures 18 bands from 410 to 940 nm. Ground analysis (reflectance, indices,
colour, light-source class) is explained in [13_Web_UI_Reference.md](13_Web_UI_Reference.md)
(SPECTROMETER, SCIENCE tabs).

**5.3.1 Exposure and lamps: `SPECCFG <gain> <cycles> <lamps> <led_cur> <ind>`**

| Argument | Values |
|---|---|
| gain | 0 = 1x, 1 = 3.7x, 2 = 16x, 3 = 64x, `AUTO` (255) = automatic exposure (aims the brightest unsaturated channel at 25-75 % of full scale) |
| cycles | 1..255: integration time (cycles + 1) x 2.78 ms; 59 = 166.8 ms (the calibration time) |
| lamps | b0 white (1), b1 IR (2), b2 UV (4); 0 = passive |
| led_cur | white code + 4 x IR code (+ 16 x UV code); codes 0 = 12.5, 1 = 25, 2 = 50, 3 = 100 mA. Limits: white <= 3 (100 mA), IR <= 2 (50 mA), UV 0 only (12.5 mA), so the allowed values are 0..11 |
| ind | indicator LED while a cycle runs: 0 off, 1..4 = 1 / 2 / 4 / 8 mA |

Examples: `SPECCFG AUTO 59 0 0 0` (passive, automatic exposure); `SPECCFG AUTO 59 1 2 0`
(white lamp at 50 mA, for LOCKIN); `SPECCFG AUTO 59 3 6 0` (white 50 mA + IR 25 mA:
2 + 4 x 1 = 6). A setting is applied from the next cycle; EVR 0x0707 `not responding: SPECCFG
stored for later` if the instrument is absent.

**5.3.2 Modes: `SPECMODE <mode> <n>`**

| Mode | n | Use |
|---|---|---|
| `SURVEY` | 1..60 (unused; give 1) | One exposure per packet (default) |
| `HDR` | 2..4 exposures | Bright and dark channels in one scene: per channel the best exposure is kept. Always passive (lamps off) |
| `LOCKIN` | 1..16 lamp-on / lamp-off pairs | Active reflectance with the configured lamps: ambient light and slow drifts cancel. Needs `lamps` != 0 (else FAILED code 3) |
| `FLUOR` | 1..16 pairs | UV excitation (UV lamp forced); channels <= 460 nm are the excitation band |
| `BURST` | 1..60 s | Continuous raw sampling into SPECBURST packets (Rice-compressed when shorter); lamps off |

**5.3.3 Cadence.** SPEC packets follow the mode table (§3: SAFE none, CRUISE 300 s,
ENCOUNTER / TEST 30 s) or `PKTRATE SPEC <s>`. LOCKIN, FLUOR and HDR take several exposures
and may finish late (never blocking).

**5.3.4 White reference (reflectance)**

1. Put a diffuse white reference target in front of the sensor so that it fills the field of
   view (about +-20.5 deg), under the light you will measure the targets with (same lamps and
   LED currents for LOCKIN).
2. Set the mode and exposure you will use for the targets.
3. Wait for a valid SPEC packet. **Check (SPECTROMETER):** valid, no saturated channel,
   at least 12 usable channels.
4. `SPECREF` (button CAPTURE WHITE REF). **Check:** event
   `SPECTRAL white reference captured (<mode>, <active illumination / ambient light>, <n> channels)`.
   It is saved in the archive and reloaded at every GDS start.
5. Measure the targets: reflectance is shown when the reference is compatible (same mode,
   lamps and illumination type).
6. Capture a new reference whenever the light, the mode or the lamps change;
   `SPECREF CLEAR` drops it.

**5.3.5 Spectral library.** `SPECLIB SAVE <name>` stores the latest spectrum (with
reflectance when available; names of 1-32 characters `A-Z a-z 0-9 _ . -`, starting with a
letter or digit; at most 64 entries); `SPECLIB DEL <name>`, `SPECLIB LIST`. The best matches (spectral angle) are shown
live.

**5.3.6 Change detection.** The spacecraft compares every spectrum with a slow baseline and
sends EVR 0x0701 when the spectral angle exceeds the threshold (boot 5.00 deg; at most one per
60 s; beacon INTERESTING). `SPECBASE <0.01 deg>` sets the threshold (0..9000) and always
restarts the baseline; `SPECBASE 0` only restarts it.

**5.3.7 Burst**

1. `SPECMODE BURST <seconds>`. It starts at the next opportunity (also in SAFE).
   **Check:** EVR 0x0704 `BURST START n=<s> s cycles=1`; SPECBURST packets in the SPECTROMETER
   tab (series, achieved rate, compression ratio).
2. **Check:** EVR 0x0704 `BURST END samples=<n> rate=<r> Hz dropped=<d> readmax=<ms> ms tx=<f>`.
3. `SPECMODE SURVEY 1` - otherwise a new burst starts at every SPEC period.

**5.3.8 Diagnostics.** `SPECDIAG <AS72651 / AS72652 / AS72653> <n_sub 0..32>` dumps versions,
device selection, LED and configuration registers, temperature and n_sub calibration words as
EVR 0x0703 lines (read-only; one line per loop pass).

---

## 6 Hibernation and wake

In hibernation the spacecraft receiver only listens duty-cycled for a wake-up preamble (E22:
Wake-on-Radio with a 2 s cycle; SX126x: duty-cycled receive for a 32-symbol preamble) and
the spacecraft transmits one HK beacon every `beacon_s` seconds; everything else is recorded
only. Stored sequences keep running.

**Enter**

1. `HIBERNATE <beacon_s>` (60..3600, hazardous), e.g. `HIBERNATE 600`, CONFIRM.
   **Check:** EVR 0x0800 `HIBERNATE: duty-cycled receive, beacon every 600 s`; HK FDIR
   `HIBERNATE`. The station expects it: LOS is logged as `LOS (hibernating)` (INFO, no RED
   alarm) and RED is raised only after 7200 s without a beacon.
2. **Check:** one HK beacon every `beacon_s` (OVERVIEW beacon level and HK time).

**Wake**

1. `WAKE` (needs the Radio Control Unit with an E22, or the SX126x station radio). It is sent
   without waiting for a downlink frame: `BD MODE SAFE` with the wake-up preamble. It waits
   only until the last uplink window's transmissions are over, at most one `WAKE` per 10 s
   (`WAKE_MIN_INTERVAL_S`: each takes an SDLS TC number; a second `WAKE` waits its turn), and,
   with the SX126x station radio, while the radio has not yet reported itself back on the
   station channel after a survey (§7.3); after max(60 s, 2 frame periods) of that it is
   dropped with WARN `WAKE not sent: station transmitter still busy after <n> s (...)`.
   **Check:** event `RADIO wake-up transmitted (BD MODE SAFE)`.
2. **Check:** EVR 0x0801 `WAKE: authenticated uplink received` and CMDVER EXECUTED for
   `MODE SAFE`; frames at the normal SAFE cadence. No reaction: repeat `WAKE` (the beacon may
   have been on the air), then check the uplink (14 §2.4).
3. `MODE CRUISE`.

A normal command is generally not heard by a hibernating receiver, so use `WAKE`. Any
`MODE` command that is received, and the command-loss timer, also end hibernation.

---

## 7 Link procedures

### 7.1 Coordinated air-rate change (`LINKRATE`) with automatic revert

Both ends must change together; the spacecraft goes back by itself if the ground does not
prove the new setting works.

1. Choose the new code ([02_Link_Budget_and_RF_Analysis.md](02_Link_Budget_and_RF_Analysis.md):
   faster = less sensitive). E22: 2 = 2.4k, 3 = 4.8k, 4 = 9.6k, 5 = 19.2k, 6 = 38.4k,
   7 = 62.5k. SX126x: profile 0..7.
2. `LINKRATE <code>`, CONFIRM (hazardous). Needs the Radio Control Unit (E22) or the SX126x
   station radio.
   **Check:** event `LINK coordinated rate change to <code> started`; the GDS queues
   `AIRRATE <code> <revert_s>` (`[linkmgr] revert_s`, default 180 s).
3. **Check:** CMDVER EXECUTED and EVR 0x0211
   `AIR RATE -> code <code> in 2 frames; revert in 180 s without uplink`. The spacecraft sends
   two more frames on the old setting, then switches (EVR 0x0306 shows the new code).
4. The GDS retunes the station after the next frame (event `RADIO ground rate code -> <code>: OK`)
   and queues a confirming `NOOP`. With the SX126x station radio, OK also covers a change the
   station MCU queued because a packet was in progress: it applies it before the next
   reception or transmission.
5. **Check:** frames arrive on the new setting (event `LINK coordinated rate change to <code>
   COMPLETE`), **and** the confirming `NOOP` is EXECUTED with EVR 0x0210
   `radio change confirmed by ground (code <code>, ch <ch>)`. The first RF telecommand frame
   the spacecraft accepts after its switch (normally this `NOOP`) is what cancels its revert; a
   hardline command does not. Both within `revert_s`.
6. If not:
   * no frames after the change: after `revert_s` + 30 s + 2 frame periods the GDS retunes the
     station back by itself (event `no frames after the rate change - ground reverting to
     <old>`), and the spacecraft reverts too (EVR 0x0602);
   * frames arrive but the `NOOP` is not EXECUTED (the uplink is weaker than the downlink):
     the spacecraft reverts after `revert_s` (EVR 0x0602) while the station stays on the new
     setting: return the station (§7.4).

The station's change is volatile: `station.toml` is not changed. Before a GDS restart either
change back, or put the new value into `station.toml` (`air_rate`) **and** the module (§7.4).
The spacecraft returns to its `config.h` default at its next boot and on command loss.

### 7.2 Coordinated channel change (`LINKCHAN`)

Same as §7.1 with `LINKCHAN <ch>`: the channel must be in `[radio] allowed_channels`; EVR
0x0212 `CHANNEL -> <ch> (<f> MHz) in 2 frames; revert in 180 s without uplink`. Raw `AIRRATE` /
`CHANNEL` commands change only the spacecraft: avoid them unless you retune the station
yourself.

### 7.3 Find a quiet channel

1. `GSCAN <first> <last>` (station survey, needs the RCU / SX126x radio; the station is deaf
   for a few seconds; without arguments channels 0..83). With the SX126x station radio a
   channel whose retune had to be queued (radio busy) gets no sample and shows n/a. The survey
   starts only after the uplink window's own transmissions (SX126x: after the station MCU has
   reported every queued uplink), waiting at most max(60 s, 2 frame periods); otherwise it is
   dropped with the WARN `ground RF survey CH<a>-<b> not run: station transmitter still busy
   ...`. It always returns to the station's operational channel, including a channel change the
   MCU has only queued (`dssq/gds.py` `TX_QUIET_JOBS`, `dssq/radioctl.py` `survey()`).
   **Check:** event `ground RF survey CH<a>-<b> in <t> s: quietest CH<x>, quietest allowed
   CH<y>`; RECEIVER spectrum chart. An ERROR `ground RF survey: the station radio did not return
   to CH<n> ...` means three attempts to return failed: an SX126x station re-sends its
   configuration every 5 s by itself; with an E22 station check with `RADIO` (§7.4).
   With the SX126x station radio nothing is radiated off the station channel: after a survey
   every uplink is held (window transmissions only fill the bridge's backlog, `WAKE` waits, a
   further `GSCAN` waits) until a status line from the station MCU shows the station channel
   with no change pending - normally the next status line, after a failed return once the
   re-sent configuration has been applied and reported (`dssq/radio/sx126x.py` TX gate,
   `home_unconfirmed()`; `dssq/radioctl.py` `off_home()`, `tx_busy()`).
2. `RFSCAN <first> <last>` (spacecraft survey; its downlink pauses). **Check:** EVR 0x0302,
   then 0x0303 `RF survey done in <t> ms: quietest CH<x> (<n> dBm)`; RFSCAN bars in RECEIVER.
3. Pick a channel quiet at both ends and in `allowed_channels`; `LINKCHAN <ch>` (§7.2).

### 7.4 Bringing the station back to the spacecraft's setting

Use this after a failed change, a station restart after a coordinated change, or whenever
the start-up check reports `MISMATCH` and there is no AOS.

1. Find the spacecraft's current channel and rate: last RF packet (SPACECRAFT tab) or the UNO Q
   EGSE status board (`ch`, `code`). After a spacecraft boot or a command-loss response they are
   the `config.h` defaults (channel 23, E22 code 2 / SX126x profile 1).
2. E22 station: the coordinated changes wrote the module's registers **volatile**. To return to
   the saved configuration (normally the `station.toml` values): power-cycle the station radio
   module, then restart the GDS. To make another setting permanent: put it into `station.toml`
   (`air_rate`, `channel`) and write it to the module with the E22 tool
   ([15_Configuration_Reference.md](15_Configuration_Reference.md) §1).
3. SX126x station: put the values into `station.toml` and restart the GDS (it pushes them to
   the station radio at start).
4. **Check:** start-up event `... - matches station.toml`; AOS.

If the spacecraft's setting is unknown, return the station to the defaults: the spacecraft
comes back to them at its next boot or after the command-loss timer (24 h by default).

### 7.5 Adaptive link manager

`LINKMGR OFF`, `LINKMGR ADVISE` (default: a recommendation is shown) or `LINKMGR AUTO` (the
recommendation is executed through §7.1). Every `period_s` (60 s) it compares the 10-minute
10th-percentile margin and the 10-minute frame error rate with `target_margin_db` (6 dB): one
step slower when the margin < target - 3 dB or FER > 10 %; one step faster when the margin >
target + 9 dB, FER < 1 % and the predicted margin at the faster rate stays >= target + 3 dB; at
most one AUTO change per `holdoff_s` (600 s); limits `min_rate` (default: the slowest distinct
code) and `max_rate` (4). It never changes the power. Needs at least 10 frames in 10 minutes.
`LINKMGR` lasts until the next GDS restart.

### 7.6 Transmit power and receiver options

* Spacecraft: `TXPWR <code>` (EVR 0x0214 shows dBm and model); `RADIOOPT <flags>` (§2.7,
  EVR 0x0213). After a BROWNOUT boot the power is derated (EVR 0x0608): fix the supply first,
  then `TXPWR 0`.
* Station: `[radio] power` (E22 module register; SX126x pushed at start). The link manager
  never changes power.

### 7.7 Silencing the transmitter

`TXINHIBIT <s>` (10..3600, hazardous). The spacecraft stops transmitting and re-enables
itself (EVR 0x0603). Expect LOS and a RED `LOS` alarm meanwhile; the station cannot uplink
either (no frames = no uplink window), so choose the duration before you send it.

### 7.8 Switching the spacecraft to the 5 W E22-400T37S

No source edit is needed: the flight software reads the A0 strap at boot
(`config.h` `PIN_RADIO_MODEL_STRAP`). The hardware changes are in
[03_Hardware_Integration.md](03_Hardware_Integration.md) (E22-400TBH-02 board, M0/M1 and RXD/TXD
caps removed, 10 kOhm pull-ups, 4.5..15 V supply of about 1.1 A at 12 V). A licence that allows
5 W is required.

1. Power the spacecraft off. Fit a jumper from the **UNO Q** header **A0 to GND** (a plain
   jumper; A0 is not 5 V tolerant). Power on. The strap exists on the spacecraft only: on the
   VENTUNO Q, A0 drives the station buzzer, so never fit a jumper there (the station is set in
   step 3).
2. **Check:** EVR 0x0308 `radio model E22-400T37S (strap to GND, 5/5 low; build E22-400T33D)`
   (WARN `SPLIT` = loose jumper); EVR 0x0306 `... pwr 37 dBm`; RF `radio_model` = 2.
3. Station: `[radio] model = "auto"` follows the spacecraft's E22 model for the link budget,
   sensitivity and power figures; set `"E22-400T37S"` when the **station** module is also a
   T37S (this also enables its abnormal-status log). Restart the GDS after an edit.
4. With the T37S: every power code is 37 dBm, the abnormal-status reports produce EVR
   0x0311..0x0315, and a PA over-temperature silences the transmitter for 300 s (EVR 0x0604).
5. Back to the T33D: remove the jumper and power-cycle.

---

## 8 Data logger and exports

The station records everything it receives ([18_Data_Products.md](18_Data_Products.md)): raw
CADUs and the event log per UTC day, frames / packets / parameters / events / commands and a
1 Hz receiver time series (`rx_stats`) in `gds.sqlite`. Archive (App Lab):
`/home/arduino/ArduinoApps/dssq-groundstation/data/archive`.

**Daily check (FILES tab, Receiver data logger panel)**

1. Rows are being written (`rx_stats` row age about 1 s), `paused` = no, write errors 0.
2. Free space well above `[gds] archive_min_free_mb` (2048 MB); used space below
   `archive_max_gb` (16 GB).
3. Last prune: what retention deleted (oldest day kept = today - `archive_keep_days` (180)).
   Copy data off the board before it ages out.

**Export to CSV**

1. FILES tab, CSV export: `from` / `to` in UTC (or LAST 10 MIN / LAST HOUR / LAST 24 H /
   TODAY), tick the products (`rf`, `params`, `events`, `frames`, `packets`, `commands`),
   EXPORT. **Check:** `CSV export ...: <product> <n> rows` and the files in the list below;
   download them from there.
2. Or on the board:
   `cd /home/arduino/ArduinoApps/dssq-groundstation/python && python3 -m dssq.export --from 2026-10-11T12:00:00Z --to 2026-10-11T13:00:00Z --what rf,events --archive ../data/archive`.
3. Refused while logging is paused (free space low); one export at a time.

**Disk full or low**

1. YELLOW `archive logging paused` or `archive write errors`: reception and commanding go on.
2. Free space (copy and delete old day folders and exports, or move the archive to a larger
   medium) or lower `archive_keep_days` / `archive_max_gb` and restart. Logging resumes by itself.
3. An absolute `archive_dir` on an external NVMe / USB drive works in App Lab only if the path
   is visible inside the App container: VERIFY on your board before relying on it.

---

## 9 File transfer (CFDP)

Files come down on virtual channel 3, the lowest priority: one File Data PDU per frame at
best (128 octets of a recorder file, 96 of an EGSE file), only in frames the other channels
leave free. Lower bound of the time: file size / 128 octets x frame period (a full 256 KiB
recorder at 2.4 kbit/s in CRUISE: 2048 frames x 3.17 s = about 1.8 h).

### 9.1 Recorder contents (`CFDPPUT SSR`)

1. Preconditions: the EGSE has given the SCLK partition (HK `sclk_partition` > 0); no
   playback, no transfer running (HK `cfdp_state` IDLE).
2. `CFDPPUT SSR <max_kib>` (0 = the whole recorder; otherwise the first `max_kib` KiB).
   **Check:** EVR 0x0A00 `CFDP SSR export ssr_p<p>_<sclk>.bin: <size> B seq <s>`; HK `cfdp_state`
   SENDING SSR, `cfdp_progress`. Recording is paused during the export.
3. **Check (FILES):** the transaction goes RECEIVING -> COMPLETE; event `CFDP <tid> '<name>'
   COMPLETE: ...` and `SSR file <name>: <n> packets archived (partition <p>)` (the packets are
   now in the database with source CFDP).
4. **Check:** EVR 0x0A01 `CFDP EOF cond 0: <sent>/<size> B, <n> not recorded` (packets not
   recorded while exporting).
5. Optionally `SSRCLEAR` (hazardous) once the file is COMPLETE.

### 9.2 EGSE files (`CFDPPUT EVRLOG`, `CFDPPUT CADU`)

`CFDPPUT EVRLOG <max_kib>` sends the newest `evr_YYYYMMDD.jsonl`, `CFDPPUT CADU <max_kib>` the
newest `cadu_YYYYMMDD.bin` from the UNO Q (limit them with `max_kib`: a day of CADUs is large,
and the station accepts at most `[cfdp] max_file_mib` = 64 MiB). **Check:** EVR 0x0A03
`CFDP relay requested: ...`, the transaction in FILES, EVR 0x0A05 at the end. EVR 0x0A04
`EGSE did not start within 30 s`: the EGSE is not running or has no such file (UNO Q log).

### 9.3 Cancel and failures

* `CFDPCANCEL` (idempotent): EVR 0x0A02; the ground state becomes CANCELLED (`.partial` +
  `.json` kept).
* INCOMPLETE (gaps), CHECKSUM_FAILURE or ABANDONED (no PDU for 600 s): send the file again
  with a new `CFDPPUT` (it is a new transaction). An INCOMPLETE file can still complete if
  late File Data arrive within 60 s.
* FAILED code 7 (BUSY): a transfer, relay or playback is running, or there is no partition
  yet (EVR 0x0A06).

---

## 10 Security keys (SDLS)

What SDLS does and the threat model are in [17_Security_Model.md](17_Security_Model.md). One
random 256-bit master key serves both ends; the uplink and downlink keys are derived from it.
**Keys are never committed to Git and never put into an App Lab zip.**

### 10.1 Generate a key (after importing the two Apps)

1. In a repository checkout (on the PC or on a board):
   `python3 tools/gen_sdls_key.py [--spi <n>] [--flight-header PATH] [--ground-key PATH]`
   (`--spi`: the AUTH SPI, 1..65535, default 1). Without the path options it writes into the
   checkout: `vgq1-spacecraft/sketch/src/sdls_key.h` and
   `dssq-groundstation/data/sdls_key.hex` (the ground App's data folder). To write straight into
   the imported Apps use `--flight-header ~/ArduinoApps/vgq1-spacecraft/sketch/src/sdls_key.h`
   and `--ground-key ~/ArduinoApps/dssq-groundstation/data/sdls_key.hex`.
   One run makes **one** key for both files. The two Apps run on two boards: run the tool once
   (on the PC, or on one board with the matching path option) and copy the file that belongs
   to the other board with `scp` (§10.2). Never run it once per board: two runs make two
   different keys. The parent folder of each output must exist (only its last level, e.g.
   `<App>/data`, is created).
   It refuses to overwrite an existing key file (exit status 1); `--force` replaces both.
2. **Check:** it prints `SDLS key written (SPI = <n>)` and both paths. Both files are created
   with owner-only permissions (0600): the flight header holds `kVgqSdlsKey[32]` and
   `VGQ_SDLS_SPI`, the ground file the same key as 64 hex digits. The flight build includes only
   a file named `sdls_key.h`.

### 10.2 Install it

1. Spacecraft: the header must be at
   `/home/arduino/ArduinoApps/vgq1-spacecraft/sketch/src/sdls_key.h` on the UNO Q (written there
   by step 1 of §10.1, or copied with `scp`); then Run the App (rebuild and upload).
   **Check:** EVR 0x0001 ends with `TC AE TM AE` (or your services).
2. Station (App Lab): the key must be
   `/home/arduino/ArduinoApps/dssq-groundstation/data/sdls_key.hex` (`chmod 600` after a copy);
   restart the App (the App always uses this path unless `[sdls] key_file` is absolute). Host
   mode with the installed service (`--app-data <App>/data`) reads the same file; only a
   stand-alone `--config` run reads `[sdls] key_file` relative to its working directory.
   **Check:** start event `SDLS TC AE / TM AE`; no `SDLS key file not found`. Normal at the
   first start with a key (no `sdls_sn.txt` in the archive yet): WARN `no SDLS TC
   sequence-number file ...` and YELLOW `TC uplink BLOCKED: ...` (commands are refused) until
   the first HK made at least 120 s after the spacecraft's boot arrives in step 3 (no event
   while it waits); the counter then continues on the time floor, 4 numbers per second since
   2026-01-01 (about 98 million in October 2026), and the uplink is released by itself (WARN
   `SDLS TC sequence number re-synchronised to <n> (the time floor: 4 per second since
   2026-01-01; sdls_last_sn <m>) ...`; §10.5). The station clock must be right.
3. **Check (first pass):** EVR 0x0120 `downlink protected`; LINK & SECURITY `tm_frames_ok`
   rising; a `NOOP` EXECUTED.
4. Delete the key files from the PC if they are not needed there; keep one offline backup.

### 10.3 Choosing the services

| Service | Spacecraft `config.h` | Station `station.toml` | Use |
|---|---|---|---|
| AE both ways (default) | `VGQ_SDLS_TC 2`, `VGQ_SDLS_TM 2` | `tc_service = "AE"`, `tm_service = "AE"` | Licensed operation where encryption is allowed |
| AUTH uplink, clear downlink | `VGQ_SDLS_TC 1`, `VGQ_SDLS_TM 0` | `tc_service = "AUTH"`, `tm_service = "NONE"` | Amateur bands: readable, but only the key holder can command |
| NONE | `0`, `0` (or no key) | `"NONE"`, `"NONE"` | Bench only |

Change both ends together (rebuild the flight sketch, restart the GDS).

### 10.4 Key rotation and suspected compromise

Rotate when: planned; the key may have leaked; the EGSE reports `SDLS TM epoch space
exhausted`; or the station raised RED `TM epoch went backwards with a valid MAC` and the
counter really regressed.

1. `python3 tools/gen_sdls_key.py --force`.
2. Install both ends as in §10.2, in the same session (the link is down in between).
3. Counters: with a new key the old nonces cannot repeat, so `boot_count.txt`,
   `sdls_last_sn.txt`, `sdls_sn.txt` and `sdls_tm_replay.txt` can simply continue. Resetting
   the spacecraft's `boot_count.txt` is allowed **only** together with a new key; then also
   send `SDLS RESETREPLAY` (§10.6), or the station rejects every frame as REPLAY.
4. Destroy every copy of the old key. After a compromise, treat everything the old key
   protected as readable by others.
5. **Check:** as §10.2 steps 1-3.

### 10.5 Counter files: backup and restore

The AES-GCM nonce must never repeat with one key. These files carry the counters that ensure
it; back them up together with the key and never put back an **older** copy while the same
key is in use.

| File | Where | Holds | If lost or corrupt |
|---|---|---|---|
| `boot_count.txt` | UNO Q `/home/arduino/ArduinoApps/vgq1-spacecraft/data/` | Last SDLS TM epoch (SCLK partition) issued | Corrupt: the EGSE issues no epoch (the flight falls back to unprotected frames after 120 s). Missing: it restarts at 1 - with a key already used, that repeats nonces. Restore a value >= the highest epoch ever used, or rotate the key |
| `sdls_last_sn.txt` | same | Last TC sequence number the MCU accepted | The MCU accepts commands again 120 s after boot (EVR 0x0124); restore it from backup |
| `sdls_sn.txt` | station archive (kept there also with `--no-archive`) | Last TC sequence number the station used | Corrupt or missing (with a key and TC AUTH or AE, with or without an archive): the uplink is held (YELLOW `TC uplink BLOCKED`; missing: WARN `no SDLS TC sequence-number file ...` at start) until an HK made at least 120 s after a spacecraft boot reports `sdls_last_sn` (an SDLS-protected one when `tm_service` is AE); the counter then continues on the time floor, 4 per second since 2026-01-01 (automatic; `sdls_last_sn` + 1 000 000 with a WARNING when the report is above the floor or the station clock is before 2026; refused above 4 227 858 432: new key). Without a key or with TC NONE the file is not used and nothing is held. `dssq.cltu` refuses meanwhile (exit status 1). The simulator never touches this file. Keep the archive (or this file) when you move or restore the station ([17_Security_Model.md](17_Security_Model.md) §7.6) |
| `sdls_tm_replay.txt` | station archive | Last accepted TM (epoch, sn) | Corrupt: restarts at (0, 0) with a warning |

Finding the highest epoch ever used, to restore `boot_count.txt` without a new key: the
first number in the station's `sdls_tm_replay.txt`, or from the archive
(`SELECT MAX(value) FROM params WHERE packet = 'HK' AND name = 'sdls_tm_epoch'`, read-only,
[18_Data_Products.md](18_Data_Products.md) §9). Write that number (or a larger one) into
`boot_count.txt` with the spacecraft App stopped; the EGSE then issues the next value. When in
doubt, rotate the key (§10.4).

### 10.6 `SDLS RESETREPLAY`

Makes the station forget the last accepted TM (epoch, sn) so the next protected frame is
accepted. Two-step confirmation (hazardous). Use it only after a key rotation with a reset
boot counter, or after a counter reset you caused and understand on the bench. Never use it to
silence an unexplained epoch regression (that is the nonce-reuse alarm).

---

## 11 Contingencies

| Situation | Indication | Immediate action | Details |
|---|---|---|---|
| Unexpected LOS | RED `LOS`, no frames | Check station (RECEIVER, radio link events), then spacecraft power / EGSE log | 14 §2.3 |
| Command loss fired | EVR 0x0601, SAFE, radio back to defaults | Station on the default link (§7.4); `MODE CRUISE`; command more often or adjust `CMDLOSS` | 14 §3.7 |
| Radio change reverted | EVR 0x0602, FDIR RATE_REVERT | Station back to the old setting (§7.4); check the margin both ways before retrying | §7.1 |
| FARM lockout | EVR 0x0205 | `UNLOCK` | §2.5 |
| Commands time out | TIMEOUT / SUSPENDED | Uplink checks; `BD NOOP` | 14 §2.4 |
| SDLS rejections | Ground `rejected by SDLS`, EVR 0x0112..0x0116 | Compare services and keys at both ends | 14 §2.8 |
| Epoch went backwards | RED `TM epoch went backwards with a valid MAC` | Stop and investigate the EGSE boot counter; rotate the key if it regressed | §10.4, §10.5 |
| EGSE not running | EVR 0x0121 null SA, 0x0123 / 0x0124, CFDPPUT BUSY | Start the spacecraft App; check `boot_count.txt` | 14 §2.8 |
| PA over-temperature (T37S) | EVR 0x0604 | Wait 300 s, improve cooling, reduce duty (`FPERIOD`, slower cadence) | 14 §2.5 |
| Brownout reset | `reset_cause` BROWNOUT, EVR 0x0608 | Fix the supply, then `TXPWR 0` | 14 §2.5 |
| Instrument lost / I2C fault | EVR 0x0606, 0x0605, 0x060A, `sensor_health` | Check wiring; it is re-probed automatically | 14 §2.6 |
| Station radio fault (T37S) | RED `ground radio fault: ...` | Station supply / cooling | 14 §4.3 |
| GDS stopped | Panel ALARM 5 Hz fast flashing (GDS link down) | Restart the App (or `sudo systemctl restart dssq-gds`); App log / `journalctl -u dssq-gds` | 14 §2.2 |
| Disk low / full | YELLOW logging paused / write errors | Free space (§8) | 14 §2.9 |
| No beacon while hibernating | RED `LOS: no beacon for ... while hibernating` | `WAKE` | §6 |
| Stuck in SAFE by fault protection | Beacon URGENT, sequence refused | Find the cause, `MODE CRUISE` | 14 §2.5 |

---

## 12 Shut-down

1. Before leaving the spacecraft unattended for long: remember the command-loss timer (24 h
   by default) sends it to SAFE on the default link.
2. Station: App Lab Stop (or `sudo systemctl stop dssq-gds`). The GDS writes the current
   pass's TDM, flushes the archive and, if a transmission is not yet covered by an
   identification, radiates a closing `DE <callsign> <name>` (§2.8), all within 4 s. An
   identification still waiting in the outbox, or still last in the SX126x bridge's backlog,
   is sent as that closing one (not repeated); if uplinks are queued behind an earlier
   identification, a new one goes after them. With the SX126x station radio the closing
   identification counts as sent only once the station MCU has accepted it, waiting at most
   0.6 s (`IDENT_FLUSH_S`); otherwise ERROR `closing identification NOT radiated: the station
   MCU did not take it within 0.6 s (TX queue full or radio down)` - identify by other means
   before you leave the frequency.
3. Spacecraft: stop the App / power off. The EGSE counters are written at every change; back
   them up with the key (§10.5).
4. Export or copy the data you need (§8).
