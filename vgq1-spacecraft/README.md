# VGQ-1 Spacecraft

Flight software and bench EGSE of the **VGQ-1** spacecraft for the **Arduino UNO Q** (ABX00162 with 2 GB RAM, ABX00173 with 4 GB), packaged as an Arduino App Lab App. It is one half of the VGQ-1 / DSS-Q1 project: the other half is the ground station App **DSS-Q Ground Station** (`dssq-groundstation`) on an Arduino VENTUNO Q. Project home: <https://github.com/Tanay7/vgq1-deep-space-link>.

The App has two parts, one for each processor of the board:

- the **flight software** (`sketch/`) runs on the STM32U585 microcontroller. It is the spacecraft: it reads the sensors, keeps the telemetry, runs the radio and executes telecommands. It flies on its own; nothing on the Linux side is needed for that;
- the **EGSE** (`python/`, Electrical Ground Support Equipment) runs on the Linux side, in App Lab's Python container. It is the bench rack next to the flight computer: it keeps the counters the link security needs across MCU resets, records every frame the spacecraft transmits, relays files and gives a status board.

The App installs **without the internet**. It has no `python/requirements.txt`, so App Lab installs no Python package, and the two Arduino libraries it needs are inside the zip (`sketch/libraries/`), so the sketch builds without the Library Manager.

---

## 1. What runs where

| Part | Runs on | What it does |
|---|---|---|
| `sketch/` (`sketch.ino`, `src/`) | STM32U585 (Arduino core on Zephyr, FQBN `arduino:zephyr:unoq`) | Flight software: sensor acquisition, housekeeping, CCSDS telemetry (TM or USLP frames, Reed-Solomon, randomizer), telecommand reception with COP-1, SDLS link security, CFDP file downlink, stored command sequences, beacon levels, fault protection with a 40 s hardware watchdog (IWDG), station identification with your callsign, and status lights on the board's 13 x 8 LED matrix |
| `python/main.py` | Linux side, App Lab's Python container (standard library only) | EGSE: the SCLK partition / SDLS telemetry epoch, the SDLS telecommand counter restore, the CADU archive, the event and status logs, the CFDP relay of Linux files, hardline ("umbilical") commanding, a status board every 10 s |
| `python/cfdp_tx.py` | Linux side | The CFDP class 1 sender the EGSE uses (CCSDS 727.0-B-5) |
| `sketch/libraries/RadioLib` | (compiled into the sketch) | RadioLib 7.8.1 with a small documented patch. Used only by the SX126x radio option; the default E22 build does not compile it |
| `sketch/libraries/Arduino_RouterBridge` | (compiled into the sketch) | Arduino_RouterBridge 0.4.3 (the MCU-to-Linux RPC library) with a small documented patch: a larger stack for its thread, and `provide_safe` handlers only on the loop thread |

Each library folder holds a `PROVENANCE.txt` (upstream, tag, commit, SHA-256 of the pristine files and of the patch) and its unchanged license file. The patches and the reasons for them are in [docs/22](https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/22_Bundled_Libraries.md).

**Radio.** The default build drives an **EBYTE E22-400T33D** over a UART. The same binary drives the 5 W **E22-400T37S** when a jumper connects the UNO Q's header pin **A0 to GND** at boot (A0 is not 5 V tolerant: a plain jumper to GND only). The ground station then needs `[radio] model = "E22-400T37S"` or `"auto"` in its `station.toml`; on the VENTUNO Q, A0 drives the station buzzer, so never put a jumper there. An **SX126x** module on SPI is a different wiring and a build switch (`VGQ_RADIO` in `sketch/src/config.h`). Wiring for all three: [docs/03](https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/03_Hardware_Integration.md).

---

## 2. Installing and running

### 2.1 Import the App

1. Download `vgq1-spacecraft-unoq.zip` (the `dist/` folder of the repository) and, if you like, check it against `dist/SHA256SUMS`: `sha256sum -c SHA256SUMS`.
2. In Arduino App Lab connected to the UNO Q: **My Apps** -> open any App -> **▼** next to its name -> **Import App** -> choose the zip.
3. The App lands in `~/ArduinoApps/vgq1-spacecraft` on the board (user `arduino`: `/home/arduino/ArduinoApps/vgq1-spacecraft`). If a folder of that name exists already, App Lab imports into a second folder with the date added; to replace a version, follow section 2.5.

### 2.2 Generate the link key (once, for both Apps)

The zips never contain a key. The link security (SDLS, CCSDS 355.0-B-2) needs ONE 256-bit key in two forms: a header compiled into this App's flight software and a key file for the ground App. `tools/gen_sdls_key.py` from the repository writes both from one run (Python 3.11 or later, standard library only). Run it **once, on your PC**, then copy each file to its board:

```bash
# on your PC, in a copy of the repository
python3 tools/gen_sdls_key.py --flight-header ~/vgq1-keys/sdls_key.h --ground-key ~/vgq1-keys/sdls_key.hex
scp ~/vgq1-keys/sdls_key.h   arduino@<uno-q-address>:ArduinoApps/vgq1-spacecraft/sketch/src/sdls_key.h
scp ~/vgq1-keys/sdls_key.hex arduino@<ventuno-q-address>:ArduinoApps/dssq-groundstation/data/sdls_key.hex
```

- The ground App's `data/` folder exists after its first start (or create it with `mkdir`); see the ground App's README.
- Running the tool again, for example once on each board, creates **a different key**: the two ends would then reject each other's frames.
- Both files are created readable by their owner only (mode 0600). Keep a copy of the pair somewhere safe and never commit, share or package them. The tool refuses to overwrite existing keys unless `--force` is given, and then both ends must get the new pair.
- The security services are set at both ends and must match: `VGQ_SDLS_TC` / `VGQ_SDLS_TM` in `sketch/src/config.h` (default AE, authenticated encryption, for both) and `[sdls] tc_service` / `tm_service` in the ground App's `station.toml`. Encryption is generally not permitted on amateur frequencies: use AUTH (telecommand) and NONE (telemetry) there ([docs/09](https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/09_Regulatory_and_Safety.md)).
- Without the header both links run NONE (unprotected) and the housekeeping says so. The App still works for bench tests.

### 2.3 First start

Open the App in App Lab and press **Run**. App Lab:

1. builds `sketch/` for the UNO Q's MCU with the board's default profile and the two bundled libraries (nothing is downloaded), and flashes it;
2. starts `python/main.py` in the App's Python container. There is no `requirements.txt`, so no package is installed.

The first build takes longer than the later ones. After a new key or a change in `sketch/`, press **Run** again: the sketch is rebuilt with it. Before the spacecraft transmits on the air, set your amateur callsign (section 4).

### 2.4 See that it works

| Where | What you should see |
|---|---|
| App Lab console | `VGQ-1 EGSE started: data in /app/data, boot counter N` (`/app` is the App folder inside the container); `notifications provided to the MCU: ...`; `flight computer boot (reset cause ...)`; `SCLK partition / SDLS TM epoch N issued (...)`. The first start also prints `first start or counter lost (boot_count.txt missing)`: expected once |
| App Lab console, every 10 s | the `VGQ-1 SPACECRAFT EGSE` status board: mode and spacecraft clock, radio model, channel and transmitter, uplink RSSI / SNR and COP-1 state, fault protection and sensor bits, SDLS services and epoch, CFDP, and the EGSE's own counters (CADUs archived, EVRs). Before the flight software reports: `waiting for the flight software status (sc_status) ...` |
| LED matrix | row 0 TX (lit while transmitting), row 1 RX (flashes on an uplink packet), row 2 COP-1 state, rows 3-5 backlog and recorder fill bars, row 6 sensor health (one column per sensor), row 7 mode, beacon level and fault protection |
| `~/ArduinoApps/vgq1-spacecraft/data/` | `cadu_YYYYMMDD.bin` growing while the spacecraft transmits (section 3) |
| The ground App | frames, housekeeping and events on the dashboard of the DSS-Q Ground Station App |

Everything the EGSE writes is also in App Lab's log of the App (`arduino-app-cli app logs user:vgq1-spacecraft --follow` in a terminal on the board).

### 2.5 Updating to a new version without losing the counters

⚠ The EGSE's counters protect the link: with the **same** key, `boot_count.txt` must never go back, or telemetry nonces would repeat (section 3). Deleting an App in App Lab deletes its folder with `data/` and with the key header in `sketch/src/`. So:

1. Stop the App. Copy `~/ArduinoApps/vgq1-spacecraft/data/` and `~/ArduinoApps/vgq1-spacecraft/sketch/src/sdls_key.h` somewhere outside `~/ArduinoApps`.
2. Delete the App (**▼** -> **Delete App**) and import the new zip (section 2.1).
3. Copy the saved `data/` files back into the new App's `data/` folder and the header back to `sketch/src/sdls_key.h`, then press **Run**.

If the counters are lost, generate a new key pair for both Apps (section 2.2): a new key lets the counter start again.

---

## 3. Where the data lives

Everything the EGSE keeps is in the App's `data/` folder, the folder App Lab reserves for an App's persistent data: `~/ArduinoApps/vgq1-spacecraft/data/` on the board (`/app/data` inside the container). The folder is created at the first start.

| File | Content |
|---|---|
| `boot_count.txt` | The last SCLK partition / SDLS telemetry epoch issued (one decimal number). With the same key it must never go back; never edit it downwards |
| `sdls_last_sn.txt` | The last SDLS telecommand sequence number the flight software accepted, given back to the MCU after every MCU boot (anti-replay) |
| `cadu_YYYYMMDD.bin` | Every CADU handed to the radio, one file per UTC day; records `[u64 unix_ns][u16 length][CADU]`. Decode one on the VENTUNO Q: copy it there (e.g. with `scp` into `~/`), keeping its `cadu_` name (the name tells the reader this record format), then `cd ~/ArduinoApps/dssq-groundstation/python && python3 -m dssq.replay --app-data ../data --cadu-file ~/cadu_YYYYMMDD.bin`. `--app-data` uses the ground App's `station.toml` and key (`data/sdls_key.hex`, the same key as this App's `sdls_key.h`), so the data frames of an AE downlink open too; see [docs/18](https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/18_Data_Products.md) section 13 |
| `evr_YYYYMMDD.jsonl` | The flight software's event reports (EVRs), one JSON object per line |
| `sc_status_YYYYMMDD.jsonl` | The flight software's status reports, one JSON object per line |
| `hardline_queue.txt` | Hardline commanding: CLTU hex lines you append here go to the flight command path without RF. Make a line on the VENTUNO Q with `cd ~/ArduinoApps/dssq-groundstation/python && python3 -m dssq.cltu NOOP --app-data ../data` (it uses the ground App's `station.toml`, key and sequence counter, so the station never reuses a sequence number). With a key it needs the ground App's counter file `data/archive/sdls_sn.txt`: until the ground station has received a housekeeping packet with that key, made at least 2 minutes after the spacecraft booted (it then writes the file), the tool refuses with `TC uplink BLOCKED: ...` and exit status 1. It also refuses a sequence number above the time floor (4 per second since 2026-01-01: `SDLS: sequence number ... would be above the time bound ...`), so the ground station's clock must be right ([docs/17](https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/17_Security_Model.md) section 7.6) |

Back up `boot_count.txt`, `sdls_last_sn.txt` and the key header together: they belong to one key.

---

## 4. Configuration

The flight software's settings are compile-time constants in `sketch/src/config.h`: the radio model, the radio channel and rate (they must match the ground's `station.toml [radio]`), the downlink framing (TM or USLP), the SDLS services, the pins. Every setting, its default, range and the setting it must match at the other end: [docs/15](https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/15_Configuration_Reference.md). After a change press **Run**: App Lab rebuilds the sketch.

**Callsign (both ends).** Set your amateur callsign at both ends before transmitting; both default to the placeholder `N0CALL`:

- spacecraft: `VGQ_CALLSIGN` in `sketch/src/config.h`. The flight software sends event 0x0002 `DE <callsign>` at boot and every 10 minutes (`kCallsignPeriodS` = 600 s), and its idle (OID) frames carry `DE <callsign> VGQ-1 `, always in clear text;
- ground station: `[station] callsign` in its `data/station.toml`. The station transmits `DE <callsign> <name>` in plain text after its first uplink, at least every 10 minutes while it keeps transmitting, and after its last uplink.

The rules behind it: [docs/09 section 1.3](https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/09_Regulatory_and_Safety.md#13-station-identification).

**E22-400T37S.** It needs no source edit: the jumper from A0 to GND on the UNO Q selects it at boot (section 1), and the flight software reports the model it found (event 0x0308, and the `radio_model` field of the RF packet). At the ground station set `[radio] model = "E22-400T37S"`, or `"auto"` to follow the model the spacecraft reports. Never put a jumper on the VENTUNO Q's A0: it is the station buzzer output.

---

## 5. Troubleshooting

| Message or symptom | Meaning / fix |
|---|---|
| `not running under Arduino App Lab` | `python/main.py` was started outside App Lab. It is the App's entry point; start the App with **Run** |
| `first start or counter lost (boot_count.txt missing)` | Expected on the very first start. Later it means `data/` was lost: if this key was used before, generate a new key pair for both Apps (section 2.2) |
| `... SCLK partitions / SDLS TM epochs are NOT issued` | `boot_count.txt` is unreadable or negative. Restore the highest value ever used (never a lower one), or generate a new key pair and delete the file |
| `MCU refused epoch N (current M): boot counter regressed` | `boot_count.txt` went back (for example restored from an old backup). Restore the newest value, or generate a new key pair |
| `SDLS TM epoch space exhausted` | All 65,534 epochs of this key are used. Generate a new key pair (section 2.2) |
| `flight computer boot (reset cause 3 WATCHDOG)` | The flight loop stopped for 40 s and the hardware watchdog reset the MCU. The flight software restarts by itself; look at the events before the reset ([docs/14](https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/14_Troubleshooting_and_Event_Response.md)) |
| `waiting for the flight software status (sc_status) ...` for more than a minute | The sketch is not running or not talking to Linux. Check the App Lab build log, then press **Run** again; `arduino-app-cli app clean-cache user:vgq1-spacecraft --force` forces a clean rebuild |
| `Bridge.provide(...) failed: ... another process providing the same name?` | Another program on the board provides the same notification names (for example a second copy of this App). Stop it |
| `hardline line N rejected` / `refused by the MCU` | The line in `hardline_queue.txt` is not a CLTU of 2-220 hex digits, or the flight software rejected it (check the key and the framing settings of `dssq.cltu`) |
| Build error naming `libraries/RadioLib` or `libraries/Arduino_RouterBridge` | A bundled library folder was removed or changed. Import the zip again: the build profile needs both bundled libraries |
| The ground station shows `UNPROTECTED` frames | One end has no key or the services differ: check `sdls_key.h` here, `data/sdls_key.hex` there, and that both use the same services (section 2.2) |

The complete event (EVR) catalogue with the operator response for each event: [docs/14](https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/14_Troubleshooting_and_Event_Response.md).

**Never share an App Lab export of this App once it holds a key.** App Lab's own **Export** copies the whole App folder (without `data/`), so it would carry `sketch/src/sdls_key.h`. The release zips are built by `tools/package_apps.py`, which refuses to package a key.

---

## 6. Files

```
app.yaml                      App descriptor (name, icon, description; no Bricks)
README.md                     this file
python/main.py                App Lab entry point: the EGSE
python/cfdp_tx.py             CFDP class 1 sender of the EGSE
sketch/sketch.ino             flight software entry point
sketch/sketch.yaml            build profile: arduino:zephyr:unoq + the two bundled libraries
sketch/src/config.h           build-time settings (radio, framing, SDLS services, pins)
sketch/src/ccsds/             protocols: TM, USLP, TC, Reed-Solomon, randomizer, CRC, SDLS
                              (AES-256-GCM, HMAC-SHA-256), CFDP, Rice compression
sketch/src/drivers/           radio (E22 UART, SX126x), sensors, AS7265X, I2C bus, payload
sketch/src/fsw/               flight software: modes, commands, telemetry packets, recorder,
                              attitude, spectral processing, autonomy, data link, LED matrix
sketch/libraries/RadioLib     bundled RadioLib 7.8.1 (patched; PROVENANCE.txt, license.txt)
sketch/libraries/Arduino_RouterBridge
                              bundled Arduino_RouterBridge 0.4.3 (patched; PROVENANCE.txt, LICENSE)
data/                         created at the first start (section 3); never part of the zip
```

## 7. More documentation

All in the repository, <https://github.com/Tanay7/vgq1-deep-space-link>:

- [docs/00 Beginner Setup Guide](https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/00_Beginner_Setup_Guide.md): the whole system step by step, from the parts list to the first contact
- [docs/01 System Design](https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/01_System_Design.md) and [docs/04 Space Data Link ICD](https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/04_Space_Data_Link_ICD.md)
- [docs/03 Hardware Integration](https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/03_Hardware_Integration.md): wiring of both boards
- [docs/05 Telemetry and Command Dictionary](https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/05_Telemetry_and_Command_Dictionary.md)
- [docs/07 Operations Procedures](https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/07_Operations_Procedures.md)
- [docs/16 Developer Guide](https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/16_Developer_Guide.md): building from the repository, tests, and the release procedure that makes this zip
- [docs/17 Security Model](https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/17_Security_Model.md): keys, counters, recovery
