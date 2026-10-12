# 09 - Regulatory and Safety

This project builds two radio transmitters of up to 5 W, a lithium-battery-powered spacecraft
model, lamps that include a near-UV LED, and software that can switch all of these on and off
remotely. This document says what licence you need, how to stay safe, and which software
features protect people and equipment.

> ⚠ **This is engineering guidance, not legal advice.** Radio rules differ from country to
> country and change. Before you transmit, read your national regulator's rules for your
> licence class and frequency, and the band plan of your national amateur-radio society.

---

## 1 Licensing

### 1.1 What the radios can do

| Radio | Power | Frequencies the module can tune | Source |
|---|---|---|---|
| EBYTE E22-400T33D (default, both ends) | up to 33 dBm = 2 W (maximum 32-34 dBm) | 410.125-493.125 MHz | E22-400T33D manual v1.05 2.2 |
| EBYTE E22-400T37S on the E22-400TBH-02 board (selectable with the A0 strap) | 37 dBm = 5 W (36-37.5 dBm), no lower power levels | 410.125-493.125 MHz | E22-xxxT37S manual v1.5 2.2, 7.2 |
| SX126x option (reference module EBYTE E22-400M33S) | chip drive +22 dBm + module PA, up to 33 dBm | same channel grid | SX1261/2 DS rev 2.2; contract 2.2 |

The software's channel grid is f = 410.125 MHz + CH x 1 MHz, CH 0..83
(`dssq/link.py`, E22 manuals). Default **CH 23 = 433.125 MHz**.

### 1.2 Why you need an amateur-radio licence

* Licence-exempt ("ISM" / short-range device) use of 433 MHz is limited to milliwatt levels:
  for example 10 mW e.r.p. with duty-cycle or bandwidth conditions in CEPT countries (ERC
  Recommendation 70-03, Annex 1); the US Part 15 field-strength limits are far lower. Watts at
  433 MHz are far outside these allowances.
* This project therefore operates as an **amateur-radio station in the 70 cm band**
  (430-440 MHz in ITU Regions 1 and 3, 420-450 MHz in Region 2). You need an amateur licence
  whose class allows the power you use: 2 W (T33D), 5 W (T37S) or up to 2 W (SX126x option).
  Many licences also limit effective radiated power; with the station's 12 dBi Yagi the
  E22-400T33D gives about 44 dBm (25 W) EIRP, the T37S about 48 dBm (63 W).
* Stay inside your country's allocation **and** band plan. The default allowed channels
  20-24 (430.125-434.125 MHz, `[radio] allowed_channels`) lie in the Region 1/3 allocation and
  below the 435-438 MHz amateur-satellite segment. They overlap the 433.05-434.79 MHz ISM
  segment, which many licence-exempt devices use: expect interference in both directions.
  The ground refuses `CHANNEL` and `LINKCHAN` to any channel outside `allowed_channels`.
  Region 2 operators can widen the list within their national rules.
* **Emission and bandwidth.** The E22 UART modules choose their LoRa spreading factor and
  bandwidth internally and EBYTE does not publish them: if your licence limits the occupied
  bandwidth, measure it with an SDR. The SX126x profiles use 31.25, 62.5 or 125 kHz
  ([02_Link_Budget_and_RF_Analysis.md](02_Link_Budget_and_RF_Analysis.md)).

### 1.3 Station identification

Both transmitters identify themselves with your callsign; the default `N0CALL` at both ends is a
placeholder.

* **The spacecraft** sends EVR 0x0002 `DE <callsign>` at boot and every 10 minutes
  (`config.h kCallsignPeriodS` = 600 s), and its idle (OID) frames carry `DE <callsign> VGQ-1`.
  The EVR travels in the normal telemetry, so it is readable on air only while the downlink is
  not encrypted (`VGQ_SDLS_TM 0`, section 1.4). OID frames are never encrypted, but the
  spacecraft sends one only when no telemetry, playback or file data waits.
* **Set the spacecraft callsign** in `vgq1-spacecraft/sketch/src/config.h`
  (`#define VGQ_CALLSIGN "N0CALL"`). Rebuild and re-flash.
* **The ground station identifies itself on the uplink** (`dssq/gds.py StationIdent`). Set your
  callsign in the station's `station.toml`: `[station] callsign = "N0CALL"` (3 to 16
  characters; the station logs a WARN at start while it is the placeholder). Whenever it has
  transmitted (commands, external TC frames, wake-up signals), the station sends
  `DE <callsign> <name>` in plain ASCII through the same radio path as the commands (E22 packet
  / SX126x packet): right after the first uplink of a run, then at least every 10 minutes
  while uplink transmissions continue, and once more when the uplink has been idle for 120 s
  (or the FOP-1 retransmission timer T1, if that is longer). It goes out in the next uplink
  window (booked first, so the window's air time never overruns: a command that no longer fits
  waits for the next window), from the housekeeping while the downlink is lost, or when the
  ground data system stops. The periodic one falls due early - max(60 s, 3 frame periods)
  before the 10 minutes are up - so a window still carries it when two downlink frames are lost
  (at the slow SX126x profiles it therefore comes more often, at profile 0 about every 3 to
  5.5 minutes); if no window has carried it 600 s - 2 s - its own air time after the last one,
  the housekeeping sends it without a window and it may then collide with one downlink frame
  (`gds.py` `_ident_margins()`, `_id_overdue()`;
  [20_Known_Limitations.md](20_Known_Limitations.md) L-28). `<name>` is `[station] name` reduced to ASCII letters, digits, `-`, `_` and
  `.` (at most 16 characters). The spacecraft counts such a packet in RF `cltu_bad` and
  ignores it (no CLTU start sequence, no event, no fault protection, no COP-1 effect).
* The dashboard shows both: the LINK & SECURITY tab's "OID identification" panel (the
  spacecraft) and the "Station identification" row of its uplink table (the ground, with the
  count and the last time). Test on air: T-TC-07 in
  [08_Verification_and_Test.md](08_Verification_and_Test.md). Check your national rules for the
  exact identification interval.

### 1.4 Encryption: choose AUTH on amateur frequencies

Most amateur regulations forbid messages encoded to obscure their meaning (for example US
47 CFR 97.113(a)(4)). Some allow obscured command messages only to a station in space
operation, which a ground-based model is not. The project offers three Space Data Link
Security (SDLS, CCSDS 355.0-B-2) modes:

| Mode | What goes on air | Who can read it | Who can command | Where to use it |
|---|---|---|---|---|
| **AE** (default) | AES-256-GCM: encrypted data + 16-octet tag | key holders only | key holders only | only where your licence permits encryption (not amateur bands, generally) |
| **AUTH** | TC frames carry an HMAC-SHA-256-128 tag; the command text stays readable; TM in clear | everyone | key holders only | **the amateur-radio choice** |
| NONE | everything in clear, no tag | everyone | anyone who builds a valid frame | bench tests without keys |

To switch to the amateur configuration:

1. Flight, `vgq1-spacecraft/sketch/src/config.h`: `#define VGQ_SDLS_TC 1` (AUTH) and
   `#define VGQ_SDLS_TM 0` (NONE). Rebuild and re-flash.
2. Ground, the station's `station.toml` (App Lab: `<App>/data/station.toml`): `[sdls]
   tc_service = "AUTH"`, `tm_service = "NONE"`. Restart the App.
3. Keep the shared key: AUTH still needs it. There is one key pair, generated once with
   `tools/gen_sdls_key.py --flight-header <path> --ground-key <path>` after importing the Apps,
   and each file copied to its board (`~/ArduinoApps/vgq1-spacecraft/sketch/src/sdls_key.h` on
   the UNO Q, `~/ArduinoApps/dssq-groundstation/data/sdls_key.hex` on the VENTUNO Q).
4. Check on the LINK & SECURITY tab: TC AUTH, TM NONE.

Authentication does not hide content, so it is generally regarded as compatible with the
amateur rules; confirm with your regulator. The E22 modules' own encryption registers
(CRYPT_H / CRYPT_L) are always left at 0 (`drivers/radio.cpp`). Key handling is in
[17_Security_Model.md](17_Security_Model.md).

---

## 2 RF safety

### 2.1 Protect the radios

* ⚠ **Never transmit without an antenna or a 50 Ohm load.** EBYTE: "The module must be
  connected to a 50 ohm impedance antenna when sending. No-load transmission may cause
  permanent damage to the module" (E22-xxxT37S manual v1.5). The spacecraft transmits as soon
  as it boots: connect the antenna or load **before** power-up.
* The receivers tolerate at most **+10 dBm** at the antenna port (E22-400T33D manual v1.05
  2.1, "blocking power"). Two radios with antennas side by side overload each other:

  | Transmitter and antennas | Minimum separation for <= +10 dBm | For a comfortable <= 0 dBm |
  |---|---|---|
  | T33D, dipoles (2.15 dBi) both ends | 1.3 m | 4 m |
  | T37S, dipoles both ends | 2 m | 6.4 m |
  | T33D into the station's 12 dBi Yagi | 4 m | 12.5 m |
  | T37S into the station's 12 dBi Yagi | 6.3 m | 20 m |

  Free-space estimates at 433.125 MHz (FSPL = 20 log10(4 pi d f / c)); near-field coupling can
  be worse, so keep **at least 20 m** with antennas, or use the attenuator chain on the bench
  (about 100 dB, the first attenuator rated >= 10 W; [08_Verification_and_Test.md](08_Verification_and_Test.md) 5.1).
* Never point the Yagi at the other radio at short range.

### 2.2 Protect people (RF exposure)

Keep people out of the zone where the time-averaged power density exceeds the general-public
reference level: **2.17 W/m2 at 433 MHz** (ICNIRP: f / 200 W/m2 for 400-2000 MHz); the US
limit (47 CFR 1.1310: f / 1500 mW/cm2) is 2.89 W/m2. Simple far-field estimates, distance
r = sqrt(EIRP x duty / (4 pi S)):

| Configuration | EIRP | Distance at 50 % duty (spacecraft maximum) | At 100 % duty |
|---|---|---|---|
| T33D, 2.15 dBi whip, 0.5 dB cable | 34.6 dBm (2.9 W) | 0.23 m | 0.33 m |
| T37S, 2.15 dBi whip, 0.5 dB cable | 38.6 dBm (7.3 W) | 0.37 m | 0.52 m |
| T33D, 12 dBi Yagi, 1 dB cable | 44.0 dBm (25 W) | 0.68 m | 0.96 m |
| T37S, 12 dBi Yagi, 1 dB cable | 48.0 dBm (63 W) | 1.08 m | 1.52 m |

These distances are inside or near the antennas' near field, where the formula is only an
estimate. Practical rule: **keep people at least 1 m from a transmitting whip and at least
3 m in front of a transmitting Yagi**, and mount outdoor antennas out of reach. Many
regulators require a documented RF-exposure evaluation for amateur stations (for example US
47 CFR 97.13(c)); use these figures as a starting point, not as the evaluation.

`TXINHIBIT <seconds>` (10-3600 s, hazardous, re-enables itself) silences the spacecraft
transmitter while someone works near its antenna.

---

## 3 Electrical safety

| Hazard | Rule | Source |
|---|---|---|
| E22-400T33D supply | 3.3-5.5 V; **above 5.5 V the module may be destroyed**; >= 4.7 V for full power; TX 1.0 A typ, 1.2 A max | manual v1.05 2.1, 2.2 |
| E22-400T37S supply | 4.5-15 V; TX 2.9-3.3 A at 5 V (supply >= 3.5 A) or 0.9-1.3 A at 12 V (supply >= 1.5 A) | manual v1.5 2.2 |
| Radio logic level | 3.3 V on both E22 models: "Using 5V level may cause burnout risk" | both manuals 2.2 |
| UNO Q pins | A4 / A5 (sensor bus) are 3.3 V only: every I2C pull-up to 3.3 V; D3 (radio M1) is not 5 V tolerant; **A0 (radio-model strap) to GND only, never to 5 V** | ABX00162 |
| VENTUNO Q pins | A1 is not 5 V tolerant; `+5V_LIMITED` and `+3V3_LIMITED` are current-limited at 2.8 A | ABX00181 |
| Supply peaks | 470-1000 uF low-ESR + 100 nF at the radio's VCC; short thick wires; current-limited peaks may reset a board-powered setup (test T-HW-07) | contract 15 |
| Lithium battery (spacecraft) | Fuse the battery lead (4 A); a charger and BMS made for the chemistry; never charge unattended; **never heat lithium cells** | contract 1.3 |
| Polarity | Check polarity and voltage before every first power-up; a reversed or over-voltage supply can destroy a module at once | - |

Wiring, power budget and supply sizing: [03_Hardware_Integration.md](03_Hardware_Integration.md).

---

## 4 Thermal safety

* The radios' power amplifiers get hot on long transmissions. Operating range of both E22
  modules: -40 to +85 degC (manuals 2.2). Mount the module where air can reach its shield; never
  wrap it in foam or close it in a small box.
* The flight software never transmits more than **50 %** of the time (`config.h
  kMaxTxDutyPermille` = 500); the ground flags `tx_duty` YELLOW from 55 % and RED from 70 %.
* The E22-400T37S measures its own PA: above about 120 degC it stops transmitting and reports
  it (manual v1.5 5.7). The flight then switches the transmitter off for 300 s, goes to SAFE
  and doubles the frame period (`kPaCooldownS`, EVR 0x0604).
* Spacecraft bus temperature (Nicla Sense ME): YELLOW at 60 degC, RED at 75 degC; FDIR flag
  AVI_OVERTEMP at >= 75 degC, cleared 5 degC lower.
* Heat tests: use a hair dryer at a distance, never an oven or a flame; keep lithium cells out of
  the hot zone ([FAQ.md](FAQ.md), "what happens if it gets hot").

---

## 5 Optical and mechanical safety

* The AS7265X board carries a white LED, an IR LED (875 nm) and a near-UV LED (405 nm). Do not
  look into the lamps. The software enforces the LED current limits on both ends (white codes
  0-3, IR 0-2 = <= 65 mA, UV 0 = <= 30 mA; contract 26) and switches every lamp off after each
  exposure.
* Outdoor antennas and masts: guy them, keep them clear of power lines, ground the mast and
  disconnect the coax in thunderstorms.
* Magnetometer boom: keep steel tools and batteries away while calibrating; it is also a
  mechanical hazard at eye height.

---

## 6 Safety features built into the software

Each item names where it is implemented, so it can be checked.

| Feature | What it does | Where |
|---|---|---|
| Boot to SAFE | After every reset the spacecraft starts in SAFE mode (slowest cadence, 25 % TX duty) and waits for the ground | `fsw/flight.cpp setup()` |
| Transmit duty cap | Frame period >= busy time x 4.0 (SAFE), 2.5 (CRUISE), 2.0 (ENCOUNTER / TEST), never below busy / 0.5 and never below 1.5 s, including across a rate change (the last frame at the old setting keeps its period) | `fsw/datalink.cpp frame_period_ms()`, `fsw/datalink.h launch_spacing_ms()`, `config.h` |
| PA over-temperature (T37S) | TX off 300 s, SAFE, frame period x 2 | `fsw/flight.cpp`, EVR 0x0604 |
| T37S supply faults | Under-voltage (< 4.5 V), over-voltage (> 15 V), over-temperature reports decoded into FDIR flags and EVRs | EVR 0x0311-0x0315 |
| Brown-out derating | After a BROWNOUT reset the radio starts at power code 2 until `TXPWR` | `config.h kBrownoutPowerCode`, EVR 0x0608 |
| Radio cannot key while the MCU boots | 10 kOhm pull-ups hold the E22's M0 / M1 high (deep sleep) whenever the MCU does not drive them | wiring, [03_Hardware_Integration.md](03_Hardware_Integration.md) |
| Command-loss timer | No valid command for 24 h (default; `CMDLOSS`): SAFE and back to the default channel and rate | EVR 0x0601 |
| Self-healing link changes | `AIRRATE` / `CHANNEL` switch two frames later and revert after `revert_s` (60-3600 s) unless an uplink at the new setting confirms them | EVR 0x0210 / 0x0602 |
| Transmitter inhibit | `TXINHIBIT` 10-3600 s; re-enables itself | EVR 0x0603 |
| Allowed channels | The ground refuses `CHANNEL` / `LINKCHAN` outside `[radio] allowed_channels` | `dssq/gds.py` |
| Hazardous commands | 8 commands (AIRRATE, SSRCLEAR, REBOOT, CMDLOSS, FDIRMASK, TXINHIBIT, HIBERNATE, CHANNEL) need a second CONFIRM step; optionally a second operator's AUTH (two-person rule, optional secret) | `dssq/dictionary.py`, `[commanding]` |
| Double validation | Every command is checked by the ground (name, arguments, ranges, cross-argument rules) and again by the flight before it executes | `dssq/cmdparse.py`, `fsw/commands.cpp` |
| Stored sequences | Refuse hazardous commands, REBOOT, SEQLOAD and SEQCTRL; stop on fault protection | `fsw/autonomy.cpp` |
| REBOOT key | `REBOOT` executes only with the argument 0xB007 | `fsw/commands.cpp` |
| Lamp limits and interlock | LED currents limited; lamps never lit while the magnetometers sample (a sample that cannot wait is flagged) | `drivers/as7265x.cpp`, MAG flag b4 |
| Authenticated commanding | SDLS AUTH or AE: forged or replayed commands are rejected and counted | [17_Security_Model.md](17_Security_Model.md) |
| Web commanding token | On a LAN, every POST needs the token; without one the dashboard is read-only | `dssq/display/web.py` |
| UDP telecommand input on loopback only | `[interop] tc_frames_udp_listen` (no authentication, no CONFIRM) is refused at start unless it is `127.x.y.z:port` or `[::1]:port` | `dssq/config.py parse_udp_listen()` |
| Hardware watchdog | The independent watchdog (IWDG) resets the spacecraft MCU when a loop pass hangs for 40 s; the next boot reports reset cause 3 WATCHDOG and starts in SAFE | `config.h VGQ_IWDG`, `kIwdgTimeoutMs`; `fsw/flight.cpp watchdog_start()` |
| Latched fault flags | Command loss, loop overrun, rate revert and SDLS authentication failures stay flagged until `RSTCNT` or a reboot, so a past fault is not missed | `fsw/tlm_packets.h kFdirClearedByRstcnt` |
| Station identification | Both ends identify with your callsign (section 1.3) | `config.h VGQ_CALLSIGN`; `dssq/gds.py StationIdent` |

The fault-protection responses and the operator actions for every event are in
[14_Troubleshooting_and_Event_Response.md](14_Troubleshooting_and_Event_Response.md); the tests
that exercise them are T-FD-01..10 in [08_Verification_and_Test.md](08_Verification_and_Test.md)
(the watchdog: T-FD-09).
