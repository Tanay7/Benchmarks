# DSS-Q Ground Station

Ground station of the VGQ-1 / DSS-Q1 project for the **Arduino VENTUNO Q**, packaged as an Arduino App Lab App. It receives, decodes, archives and displays the telemetry of the **VGQ-1** spacecraft (the App **VGQ-1 Flight Software**, `vgq1-spacecraft`, on an Arduino UNO Q) and sends it telecommands. Project home: <https://github.com/Tanay7/vgq1-deep-space-link>.

The App has two parts, one for each processor of the board:

- the **ground data system** (`python/`) runs on the Linux side, in App Lab's Python container: the radio receiver chain, frame and packet decoding, link security, commanding with COP-1, the archive and data logger, file reception, science analytics, and the operator **web dashboard on port 8080**;
- the **station I/O sketch** (`sketch/`) runs on the board's STM32H5F5 microcontroller: one CardKB keyboard on the header I2C pins, the annunciator LEDs and buzzer, the Radio Control Unit of the E22 module (M0, M1, AUX) and, as an option, an SX126x radio on SPI.

The App installs **without the internet**. The ground data system uses only the Python standard library (no `python/requirements.txt`, so App Lab installs no package), the dashboard is one self-contained page (no CDN, no web font), and the two Arduino libraries the sketch needs are inside the zip (`sketch/libraries/`).

---

## 1. What runs where

| Part | Runs on | What it does |
|---|---|---|
| `python/main.py` | Linux side, App Lab's Python container | App Lab entry point: prepares `data/`, loads the configuration, starts the ground data system and its web server, and talks to the station sketch through App Lab's Bridge |
| `python/dssq/` | Linux side | The ground data system package: E22 serial driver (standard-library `termios`) and SX126x link, frame synchronisation, Reed-Solomon, CCSDS TM / USLP / TC, SDLS (pure-Python AES-256-GCM), COP-1 (FOP-1), CFDP receiver, decommutation, archive (SQLite) and data logger, CSV export, CCSDS TDM and XTCE products, spectral analytics, magnetometer calibration, the web dashboard (`display/`) |
| `python/config/station.toml` | Linux side | The packaged default configuration; copied to `data/station.toml` at the first start |
| `sketch/` | STM32H5F5 (Arduino core on Zephyr, FQBN `arduino:zephyr:ventunoq`) | Station I/O: CardKB console, annunciators, Radio Control Unit, optional SX126x radio |
| `sketch/libraries/RadioLib` | (compiled into the sketch) | RadioLib 7.8.1 with a small documented patch (the stock release does not compile for the VENTUNO Q). Used only by the SX126x option; the default build does not compile it |
| `sketch/libraries/Arduino_RouterBridge` | (compiled into the sketch) | Arduino_RouterBridge 0.4.3 (the MCU-to-Linux RPC library) with a small documented patch: a larger stack for its thread, and `provide_safe` handlers only on the loop thread |
| `host/` | Linux side, outside App Lab | `install_station.sh` and the systemd unit `dssq-gds.service`: the optional host mode, which runs the same ground data system on the same `data/` folder (section 6) |

Each library folder holds a `PROVENANCE.txt` (upstream, tag, commit, SHA-256 of the pristine files and of the patch) and its unchanged license file. The patches and the reasons for them: [docs/22](https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/22_Bundled_Libraries.md).

**Radio.** The default station radio is an **EBYTE E22-400T33D** on a USB-UART adapter (`/dev/ttyUSB0` by default), with its M0/M1/AUX pins on the station sketch's Radio Control Unit. `[radio] model` in `station.toml` selects `"E22-400T33D"` (default), `"E22-400T37S"` or `"SX126x"`; `"auto"` follows the E22 model the spacecraft reports. The spacecraft selects its 5 W E22-400T37S with a jumper from the UNO Q's A0 to GND; the station has no such jumper: on the VENTUNO Q, A0 drives the buzzer, so never put a jumper there. The SX126x option is also a build switch of the sketch (`#define DSSQ_RADIO_SX126X 1` in `sketch/sketch.ino`). Wiring: [docs/03](https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/03_Hardware_Integration.md).

---

## 2. Installing and running

### 2.1 Import the App

1. Download `dssq-groundstation-ventunoq.zip` (the `dist/` folder of the repository) and, if you like, check it against `dist/SHA256SUMS`: `sha256sum -c SHA256SUMS`.
2. In Arduino App Lab connected to the VENTUNO Q: **My Apps** -> open any App -> **▼** next to its name -> **Import App** -> choose the zip.
3. The App lands in `~/ArduinoApps/dssq-groundstation` on the board (user `arduino`: `/home/arduino/ArduinoApps/dssq-groundstation`). If a folder of that name exists already, App Lab imports into a second folder with the date added; to replace a version, follow section 2.6.

### 2.2 Generate the link key (once, for both Apps)

The zips never contain a key. The link security (SDLS, CCSDS 355.0-B-2) needs ONE 256-bit key at both ends: a key file for this App and a header for the spacecraft's flight software. `tools/gen_sdls_key.py` from the repository writes both from one run (Python 3.11 or later, standard library only). Run it **once, on your PC**, then copy each file to its board:

```bash
# on your PC, in a copy of the repository
python3 tools/gen_sdls_key.py --flight-header ~/vgq1-keys/sdls_key.h --ground-key ~/vgq1-keys/sdls_key.hex
scp ~/vgq1-keys/sdls_key.hex arduino@<ventuno-q-address>:ArduinoApps/dssq-groundstation/data/sdls_key.hex
scp ~/vgq1-keys/sdls_key.h   arduino@<uno-q-address>:ArduinoApps/vgq1-spacecraft/sketch/src/sdls_key.h
```

- This App reads the key from `data/sdls_key.hex`, under App Lab and in host mode alike, unless `[sdls] key_file` in `data/station.toml` is an absolute path. `data/` exists after the first start; before it, `scp` cannot create it, so either press **Run** once first or create the folder (`mkdir ~/ArduinoApps/dssq-groundstation/data` on the board).
- Running the tool again, for example once on each board, creates **a different key**: the two ends would then reject each other's frames.
- Both files are created readable by their owner only (mode 0600). Keep a copy of the pair somewhere safe and never commit, share or package them. The tool refuses to overwrite existing keys unless `--force` is given, and then both ends must get the new pair.
- The security services must match at both ends: `[sdls] tc_service` / `tm_service` in `data/station.toml` (default `"AE"`, authenticated encryption, for both) and `VGQ_SDLS_TC` / `VGQ_SDLS_TM` in the flight software's `sketch/src/config.h`. Encryption is generally not permitted on amateur frequencies: use `"AUTH"` (telecommand) and `"NONE"` (telemetry) there ([docs/09](https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/09_Regulatory_and_Safety.md)).
- Without the key file both ends run NONE (unprotected); the dashboard says so with a YELLOW alarm while a service is configured. The App still works for bench tests.
- ⚠ **A key and its counter belong together.** The telecommand sequence number of a key is kept in `data/archive/sdls_sn.txt`. Never give the key to a second archive or a second station folder without that file, and never put an older copy of it back: with authenticated encryption (AE) a sequence number used twice under the same key reuses an AES-GCM nonce, which breaks the encryption and the authentication of those frames. When the file is missing or corrupt the station does not guess: it holds every telecommand until the spacecraft's housekeeping reports its last accepted number, and then continues on a time floor (4 numbers per second counted from 1 January 2026) that lies above every number the key can have used - so keep the station clock right (section 2.3). To start a fresh archive, generate a new key pair (section 2.2) for both Apps.

### 2.3 First start

Open the App in App Lab and press **Run**. App Lab:

1. builds `sketch/` for the VENTUNO Q's MCU with the default profile and the two bundled libraries (nothing is downloaded), and flashes it;
2. starts `python/main.py` in the App's Python container (no package is installed: there is no `requirements.txt`).

At its first start the App:

- creates `data/` and copies the packaged `python/config/station.toml` to `data/station.toml`. **Edit `data/station.toml`**, never the packaged copy: the App reads `data/station.toml` and never overwrites it;
- creates the web commanding token `data/http_token` (mode 0600) and prints it once to the console: `DSS-Q web commanding token created in /app/data/http_token: <token> (enter it once in the dashboard)` (`/app` is the App folder inside the container);
- serves the dashboard on all interfaces, port 8080 (`app.yaml` publishes the port), and logs `DSS-Q ground station running: dashboard on port 8080 (bound to 0.0.0.0); archive /app/data/archive; commanding token in /app/data/http_token`;
- with a key in `data/sdls_key.hex` and a telecommand service AUTH or AE, but no `data/archive/sdls_sn.txt` yet (the first start with a new key): logs the WARN event `no SDLS TC sequence-number file ...` and holds the uplink (YELLOW alarm `TC uplink BLOCKED: ...`, commands are refused) until the first housekeeping packet that the spacecraft made at least 2 minutes after its boot arrives (earlier ones are ignored without a message). It then starts the telecommand counter on the time floor - 4 numbers per second since 2026-01-01, about 98 million in October 2026 - writes the file and releases the uplink by itself (WARN `SDLS TC sequence number re-synchronised to <n> (the time floor: 4 per second since 2026-01-01; sdls_last_sn 0) ...: TC uplink released`). This is normal ([docs/17](https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/17_Security_Model.md) section 7.6).

Set at least `[station]` and `[radio]` in `data/station.toml`, then press **Run** again:

- `[station] callsign`: your amateur callsign (default `N0CALL`, a placeholder; while it is set the station logs the WARN event `[station] callsign is the placeholder N0CALL ...` at every start). Section 5 says what the station transmits with it;
- `[radio]`: the radio `model` and `port`, and the `channel` and `air_rate`, which must match the flight software's `config.h`.

Every key: [docs/15](https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/15_Configuration_Reference.md).

### 2.4 Open the dashboard

From a computer on the same network, open `http://<ventuno-q-address>:8080/`. The dashboard has 11 tabs: OVERVIEW, RECEIVER, LINK & SECURITY, FRAMES & PACKETS, SPACECRAFT, SPECTROMETER, SCIENCE, MAGCAL, SEQUENCES, FILES and COMMANDING. Every metric is explained in [docs/13](https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/13_Web_UI_Reference.md).

- Reading needs no token. **Commanding** does: press **TOKEN** in the header and paste the contents of `data/http_token`. The browser keeps it for that session only.
- The server answers only addresses that cannot be a rebound web name: an IP address, a single-label name or `<name>.local`. To use another DNS name, list it in `[gds] http_allowed_hosts`.

### 2.5 See that it works

| Check | Expected |
|---|---|
| App Lab console | the `DSS-Q ground station running: ...` line of section 2.3, then a status summary every `[gds] console_interval_s` (10 s) |
| On the board: `curl -s http://127.0.0.1:8080/api/state \| head -c 200` | a JSON object: the web server runs |
| From a PC: `curl -s http://<ventuno-q-address>:8080/metrics \| head` | Prometheus text: the port is reachable from the network |
| Dashboard OVERVIEW and RECEIVER | the radio and station I/O online; with the spacecraft transmitting, frames and housekeeping arriving, RSSI / SNR and the frame counters moving |
| `~/ArduinoApps/dssq-groundstation/data/archive/` | `gds.sqlite` and a `YYYYMMDD/` folder for the current UTC day |

**Whether App Lab's container exposes port 8080 to the network is not verified on hardware yet.** If the board answers the first `curl` but the PC does not, the container does not publish the port: use the host mode of section 6. The same applies if the App cannot open the E22's serial port.

**Without any hardware**, the ground data system runs on a PC with a software spacecraft: in a copy of the repository, `cd dssq-groundstation/python && python3 -m dssq.gds --sim --config config/station.toml`, then open `http://127.0.0.1:8080/`. This demonstration keeps its own archive in `python/archive/` and runs without a key; never give it a station's key. The simulator (`--sim`, or `DSSQ_SIM=1` in App Lab) keeps its telecommand counter and telemetry anti-replay state in memory: it never reads or writes a real `sdls_sn.txt`.

### 2.6 Updating to a new version without losing the archive or the counters

`data/` holds the operator's configuration, the token, the key, the archive and the link-security counters (`archive/sdls_sn.txt`, the telecommand sequence number, must not go back with the same key). Deleting an App in App Lab deletes its folder with `data/`. So:

1. Stop the App (and the host-mode service, if you use it). Copy the whole `~/ArduinoApps/dssq-groundstation/data/` folder somewhere outside `~/ArduinoApps`: the key and `archive/sdls_sn.txt` must travel together.
2. Delete the App (**▼** -> **Delete App**) and import the new zip (section 2.1).
3. Copy the saved folder back as the new App's `data/` folder (the newest copy, never an older one), then press **Run**. Compare your `station.toml` with the new packaged `python/config/station.toml` for new keys (a missing key takes its built-in default).

---

## 3. Where the data lives

Everything the App keeps is in its `data/` folder, the folder App Lab reserves for an App's persistent data: `~/ArduinoApps/dssq-groundstation/data/` on the board (`/app/data` inside the container), on the board's 64 GB eMMC. Host mode (section 6) uses the same folder.

| Path | Content |
|---|---|
| `station.toml` | The operator's configuration (copied from the packaged default at the first start) |
| `http_token` | The web commanding token (mode 0600) |
| `sdls_key.hex` | The SDLS key (section 2.2); never in a zip |
| `archive/gds.sqlite` | Frames, packets, decommutated parameters, events, commands and the 1 Hz receiver time series `rx_stats` |
| `archive/YYYYMMDD/cadu.bin`, `events.log` | Raw CADUs as received and the human-readable event log, one folder per UTC day |
| `archive/files/`, `archive/tdm/`, `archive/exports/` | Files received by CFDP, CCSDS tracking data messages, CSV exports (FILES tab, or `python3 -m dssq.export`) |
| `archive/sdls_sn.txt`, `archive/sdls_tm_replay.txt` | SDLS counters: telecommand sequence number and telemetry anti-replay state |

Retention is set in `[gds]`: days kept (`archive_keep_days`, 180), a size cap (`archive_max_gb`, 16) and a free-space floor (`archive_min_free_mb`, 2048) below which raw logging pauses. Formats, the CSV export and loading the data in Python: [docs/18](https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/18_Data_Products.md).

---

## 4. Troubleshooting

| Message or symptom | Meaning / fix |
|---|---|
| `configuration /app/data/station.toml: ...` (the App stops) | A value in `data/station.toml` is invalid; the message names the key. Fix it and press **Run** |
| `ground data system not started: ...` | Usually port 8080 is in use, for example by the host-mode service (section 6). Stop the other program |
| `[interop] tc_frames_udp_listen = ... must be "", "127.0.0.1:port" ...` | The UDP telecommand interface for an external mission control system listens on the loopback interface only (`127.x.y.z:port` or `[::1]:port`): the interface has no authentication of its own. Use a loopback address, or leave it empty |
| WARN `[station] callsign is the placeholder N0CALL` | Set your amateur callsign in `data/station.toml` before transmitting (section 2.3) |
| `dssq package not found` | The App folder is incomplete. Import the zip again |
| `not running under App Lab (no arduino.app_utils)` | `python/main.py` was started outside App Lab. On the board use the host mode (section 6) |
| The dashboard opens on the board but not from a PC | The container does not publish the port, or a firewall blocks it: use the host mode (section 6) |
| `bad or missing X-DSSQ-Token` when sending a command | Press **TOKEN** in the dashboard header and paste the contents of `data/http_token` (the same file in host mode) |
| `commanding disabled: set [gds] http_token` | A stand-alone run (`python3 -m dssq.gds` without `--app-data`) on a network address without a token: the dashboard stays read-only. Set `[gds] http_token` in the `station.toml` it uses. The App and host mode create the token by themselves |
| The dashboard refuses a DNS name | Add the name to `[gds] http_allowed_hosts` (section 2.4) |
| The E22 is not found or `permission denied` on the serial port | Check `port` in `[radio]` (`ls /dev/serial/by-id/`). If the container cannot open it, use the host mode (section 6) |
| `router bridge unavailable: front panel / RCU / SX126x radio disabled` | Host mode without `arduino.router_bridge`: run `host/install_station.sh` (section 6) |
| Frames arrive but are `UNPROTECTED`, or every frame fails authentication | The two ends have different keys or services (section 2.2) |
| `... Bridge ...` errors while the host-mode service also runs | Only one program may provide the station's Bridge methods: run either the App or the service, never both |

Every event and alarm of the ground and the spacecraft, with the operator response: [docs/14](https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/14_Troubleshooting_and_Event_Response.md).

**Never export this App from App Lab with its data included, and never share such an export.** `data/` holds the key, the web token and the link counters. App Lab's own **Export** leaves `data/` out unless you ask for it. The release zips are built by `tools/package_apps.py`, which never packages `data/` and refuses to run while a key or token file is anywhere in the App folder.

---

## 5. Configuration

`data/station.toml` (App Lab and host mode alike) holds every setting: station callsign, position and antenna, radio model, channel, rate and power, link framing, SDLS services and key file, CFDP, spectral analytics, archive and retention, web server, external interfaces, commanding (FOP-1 timers, two-person rule). The station refuses to start on an invalid value and names the key. Several values must match the flight software's `config.h`: [docs/15](https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/15_Configuration_Reference.md) lists every key and every must-match pair.

**Station identification.** Both ends identify with an amateur callsign in clear text ([docs/09 section 1.3](https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/09_Regulatory_and_Safety.md#13-station-identification)):

- this station transmits `DE <callsign> <name>` (`[station] callsign` and `name`, plain ASCII) through the same radio path as its telecommands: right after the first uplink of a run, at least every 10 minutes while it keeps transmitting, and once after the last uplink (when the uplink has been idle for 120 s or the COP-1 timer T1, whichever is longer, also while the downlink is lost). The spacecraft ignores these packets (they are not CLTUs);
- the spacecraft sends event 0x0002 `DE <callsign>` at boot and every 10 minutes, and its idle frames carry `DE <callsign> VGQ-1 ` (`VGQ_CALLSIGN` in its `sketch/src/config.h`).

**External interfaces.** `[interop]` can send telemetry frames and packets to a mission control system over UDP and receive telecommand frames from one. The telecommand listener (`tc_frames_udp_listen`) accepts only a loopback address, `127.x.y.z:port` or `[::1]:port`: the interface has no authentication of its own and its frames bypass the hazardous-command CONFIRM (the station protects them with its SDLS key and radiates them), so only programs on the board itself may reach it.

---

## 6. Host mode (fallback and always-on operation)

The same ground data system can run directly on the board's Linux (Ubuntu 24.04), without App Lab, as a systemd service or in a terminal. Use it if the App's container does not publish port 8080 to the network or cannot open the serial port, or to run the station at every boot. Host mode uses **the App's own `data/` folder**: the same `station.toml`, archive, key and web token as the App (`python -m dssq.gds --app-data <App>/data`).

```bash
# on the VENTUNO Q, after stopping the App in App Lab
~/ArduinoApps/dssq-groundstation/host/install_station.sh             # one-time setup
~/ArduinoApps/dssq-groundstation/host/install_station.sh --service   # + the systemd service, started at every boot
journalctl -u dssq-gds -f                                            # the service's log

# or in a terminal (foreground), without the service:
cd ~/ArduinoApps/dssq-groundstation/python
~/.venvs/dssq-gds/bin/python -m dssq.gds --app-data ~/ArduinoApps/dssq-groundstation/data
```

`install_station.sh` installs `python3-venv` if needed and creates `~/.venvs/dssq-gds` with `arduino-router-bridge` 0.5.1, which the front panel, the Radio Control Unit and the SX126x radio need in host mode (App Lab's image already has it). **This step needs the internet once**; the App Lab path never does. With `--service` it installs `host/dssq-gds.service` for this App folder, that environment and your user. As in the App, the dashboard listens on all interfaces on port 8080 and commanding needs the token in `data/http_token`; to keep the dashboard on the board only, add `Environment=DSSQ_HTTP_HOST=127.0.0.1` under `[Service]` in `/etc/systemd/system/dssq-gds.service`.

⚠ **One station, one data folder.**

- Run either the App or host mode, never both: they would provide the same Bridge methods and compete for the serial port and port 8080. Stop the App before starting the service, and `sudo systemctl disable --now dssq-gds` before going back to the App.
- Always start host mode with `--app-data <App>/data` (the service does). A run without it keeps a separate archive with its own telecommand sequence-number file: it holds the uplink until a housekeeping packet arrives, and switching between the two archives under the same key can reuse sequence numbers (with AE, AES-GCM nonces). The key file never goes anywhere without its `archive/sdls_sn.txt`.

---

## 7. Files

```
app.yaml                      App descriptor (name, icon, description, ports: 8080; no Bricks)
README.md                     this file
python/main.py                App Lab entry point
python/config/station.toml    packaged default configuration (copied to data/ at the first start)
python/dssq/                  the ground data system package
    gds.py                    runner: radio, decoding, archive, commanding, web server, console
    ccsds/                    TM, USLP, TC, CFDP, Reed-Solomon, randomizer, CRC, SDLS, AES-GCM,
                              time codes, Rice decompression
    radio/                    E22 serial driver, SX126x link through the station sketch
    display/                  web server, dashboard.html, front panel (CardKB, annunciators)
    archive.py, datalog.py, export.py, products.py, replay.py, spectral.py, magcal.py, ...
sketch/sketch.ino             station I/O sketch
sketch/sketch.yaml            build profile: arduino:zephyr:ventunoq + the two bundled libraries
sketch/libraries/RadioLib     bundled RadioLib 7.8.1 (patched; PROVENANCE.txt, license.txt)
sketch/libraries/Arduino_RouterBridge
                              bundled Arduino_RouterBridge 0.4.3 (patched; PROVENANCE.txt, LICENSE)
host/install_station.sh       host mode: one-time setup (section 6)
host/dssq-gds.service         host mode: systemd unit
data/                         created at the first start (section 3); never part of the zip
```

## 8. More documentation

All in the repository, <https://github.com/Tanay7/vgq1-deep-space-link>:

- [docs/00 Beginner Setup Guide](https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/00_Beginner_Setup_Guide.md): the whole system step by step, from the parts list to the first contact
- [docs/06 Displays](https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/06_Displays.md) and [docs/13 Web UI Reference](https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/13_Web_UI_Reference.md)
- [docs/07 Operations Procedures](https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/07_Operations_Procedures.md)
- [docs/02 Link Budget and RF Analysis](https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/02_Link_Budget_and_RF_Analysis.md)
- [docs/16 Developer Guide](https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/16_Developer_Guide.md): building from the repository, tests, and the release procedure that makes this zip
- [docs/17 Security Model](https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/17_Security_Model.md): keys, counters, web authentication, recovery
