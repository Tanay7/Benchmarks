# 12 - Design Decision Log

This log records every significant design decision of VGQ-1 (spacecraft) and DSS-Q1 (ground
station): **what** was chosen, **which alternatives** existed, **why**, and the **evidence**
(datasheet section, standard clause, test, real build or commit). Its purpose is that anyone -
including a future maintainer with no access to the original discussions - can answer "why is it
like this?" from the repository alone.

How to read an entry:

* **Decision** - what the code does now.
* **Alternatives** - the other options. "(recorded)" marks an alternative that was actually tried
  or written down during the project; the others are the obvious options a reviewer would ask
  about.
* **Reason** - why this option won. Where the design record gives no explicit reason, the entry
  says so and states the engineering rationale separately.
* **Evidence** - where the facts come from: a file and symbol, a datasheet section, a standard
  clause, a test name, a real build or a commit (`git show <hash>`; commit messages carry the
  verification numbers).
* **Status** - current, or the decision that superseded it.

Sources used to build this log: two design records kept while the project was built - the v3
interface contract (sections 0-44; later sections supersede earlier ones) and the Phase B
integration specification with its review log (38 findings) - the Phase C adversarial review
(64 findings), the git history and the explanations given to the owner. The two design records
were working notes and are **not in the repository**; their content lives on in this log and in
the other documents. Code comments still cite them as "contract N" and "PB-x.y" (and the record
IDs C-nn, F-nn, I1-I4): [Appendix B](#appendix-b---citation-tags-in-code) maps every such tag
to its topic and to the place where it is documented now (`tests/test_citations.py` keeps the
two in step).

Facts here were checked against the code on 2026-10-12. Test counts and build sizes are kept in
[08_Verification_and_Test.md](08_Verification_and_Test.md) and
[01_System_Design.md](01_System_Design.md) (resource budgets); entries quote the numbers of a
specific proof only. Where a value is not proven on hardware the entry says **VERIFY on
hardware**; every such item is listed in [20_Known_Limitations.md](20_Known_Limitations.md).

---

## Index

| ID | Decision | Area |
|---|---|---|
| [D-01](#d-01) | Two Arduino Q boards, each split into MCU (real time) and Linux | Architecture |
| [D-02](#d-02) | All flight software on the MCU; the UNO Q Linux side is optional EGSE | Architecture |
| [D-03](#d-03) | CCSDS protocols end to end, private spacecraft ID 0x0A7 | Architecture |
| [D-04](#d-04) | Build options are `config.h` defines; the E22 model is read at boot | Architecture |
| [D-05](#d-05) | The repository is the two App Lab App folders | Architecture |
| [D-06](#d-06) | Offline Apps: standard library only, no `requirements.txt` | Architecture |
| [D-07](#d-07) | Pure C++ protocol and algorithm code, compiled and tested on a PC | Architecture |
| [D-08](#d-08) | EBYTE E22-400T33D is the default radio | Hardware |
| [D-09](#d-09) | E22-400T37S kept, chosen at boot by a jumper on header A0 | Hardware |
| [D-10](#d-10) | SX126x SPI module as the long-range option, LoRa profiles 0-7 | Hardware |
| [D-11](#d-11) | E22 UART on `Serial3` (header SDA/SCL pins), not on D0/D1 | Hardware |
| [D-12](#d-12) | External 10 kOhm pull-ups on M0/M1; T37S board jumpers removed | Hardware |
| [D-13](#d-13) | One sensor bus: `Wire2` = I2C3 on A4/A5 (the AS7265X optionally on the Qwiic socket), no multiplexer | Hardware |
| [D-14](#d-14) | Instrument set: Nicla Sense ME, AS7265X, one or two RM3100 | Hardware |
| [D-15](#d-15) | One CardKB on the VENTUNO Q header I2C; second operator in the web UI | Hardware |
| [D-16](#d-16) | The web dashboard is the only station display | Hardware |
| [D-17](#d-17) | Board-powered radio supported; brown-out derating; reset cause by backup register | Hardware |
| [D-18](#d-18) | Channel grid default 433.125 MHz, allowed-channel list, callsign in clear | Hardware |
| [D-19](#d-19) | 200-octet frames + RS(255,223) = 236-octet CADU = one LoRa packet | Link |
| [D-20](#d-20) | No inner LDPC/turbo/convolutional code; 255-bit randomizer | Link |
| [D-21](#d-21) | "RS rescue": concatenated coding on the SX126x path | Link |
| [D-22](#d-22) | Virtual channels VC0-VC3 + OID, fixed priority, priority reserve | Link |
| [D-23](#d-23) | TM frames by default, USLP selectable, uplink format auto-detected | Link |
| [D-24](#d-24) | Downlink paced by measured capacity; 1.5 s frame floor; 50 % TX duty | Link |
| [D-25](#d-25) | Every sensitivity value carries its source | Link |
| [D-26](#d-26) | SX126x: Rx boosted gain with retention, CAD, ground AFC | Link |
| [D-27](#d-27) | CFDP class 1 (unacknowledged) with an EGSE relay | Link |
| [D-28](#d-28) | SDLS AE by default; AUTH for amateur operation; NONE on the bench | Security |
| [D-29](#d-29) | One master key, per-direction derived keys, never in Git or a zip | Security |
| [D-30](#d-30) | TM nonce safety: epoch = EGSE boot counter, hold, null-SA fallback | Security |
| [D-31](#d-31) | An epoch value is offered to the MCU at most once, ever | Security |
| [D-32](#d-32) | TC sequence-number file: atomic, never lowered, locked, self-healing | Security |
| [D-33](#d-33) | TC anti-replay held until the EGSE restores the counter | Security |
| [D-34](#d-34) | A change of SDLS status on a VC is a packet discontinuity | Security |
| [D-35](#d-35) | Pure-Python AES-256-GCM on the ground, a different formulation | Security |
| [D-36](#d-36) | Web UI security: token, Host checks, hash CSP, bounded server | Security |
| [D-37](#d-37) | One two-stage command parser for live and stored commands | Commanding |
| [D-38](#d-38) | CMDVER carries the command source in the stage octet | Commanding |
| [D-39](#d-39) | Hazardous commands: confirmation, optional two-person rule, self-healing changes | Commanding |
| [D-40](#d-40) | RadioLib 7.8.1 bundled and patched (reversal of the in-house driver plan) | Libraries |
| [D-41](#d-41) | Arduino_RouterBridge 0.4.3 bundled and patched | Libraries |
| [D-42](#d-42) | RPC mailbox to the loop thread, kept as defence in depth | Libraries |
| [D-43](#d-43) | Bridge rules: no `Bridge.call`, no `Monitor`, gated notifications | Libraries |
| [D-44](#d-44) | Own AS7265X driver; LED current limits; INT pin not used | Science |
| [D-45](#d-45) | Exposures in PA-off windows; lamps never lit during magnetometer samples | Science |
| [D-46](#d-46) | CCSDS 121.0-B-3 lossless compression for SPECBURST | Science |
| [D-47](#d-47) | Log-encoded spectra; heuristics and approximations labelled | Science |
| [D-48](#d-48) | Magnetometer calibration fitted on the ground, applied on board to attitude only | Science |
| [D-49](#d-49) | Stored command sequences (Voyager CCS style), RAM only | Autonomy |
| [D-50](#d-50) | Beacon urgency levels (Deep Space 1 style) | Autonomy |
| [D-51](#d-51) | Standard ground products: CCSDS TDM and XTCE | Ground |
| [D-52](#d-52) | Sensor facts taken from the manufacturers' data, not from libraries | Science |
| [D-53](#d-53) | Data logger: SQLite WAL, 1 Hz receiver series, retention, free-space guard | Ground |
| [D-54](#d-54) | Ground robustness: snapshot copy, error boundaries, best-effort archive | Ground |
| [D-55](#d-55) | Host (systemd) mode kept as a fallback | Ground |
| [D-56](#d-56) | Coding standard: Power of Ten, FP32 only, no heap after start-up | Quality |
| [D-57](#d-57) | Five verification layers, all automated in CI except hardware | Quality |
| [D-58](#d-58) | Dictionary document generated from the code; drift tests | Quality |
| [D-59](#d-59) | Adversarial reviews before integration and before release | Quality |
| [D-60](#d-60) | Hardware watchdog: the STM32U585 IWDG, programmed by direct register writes | Autonomy |
| [D-61](#d-61) | The ground station identifies itself in clear text on the uplink | Ground |
| [D-62](#d-62) | The UDP telecommand listener accepts loopback addresses only | Security |
| [D-63](#d-63) | Transmitter duty measured over whole transmission cycles (`TxDutyMeter`) | Link |
| [D-64](#d-64) | RSTCNT also clears the four event-latched FDIR flags | Autonomy |
| [D-65](#d-65) | An unknown TC counter is held, then re-synchronised onto a time floor (4 numbers per second since 2026-01-01) | Security |
| [D-66](#d-66) | 64-octet Serial rings (arduino:zephyr before 1.0.0): a UART pump thread for the E22 port, long notifications into an empty ring | Software |
| [D-67](#d-67) | I2C bus clear releases the lines with the MCU's weak pull-ups: an empty A4/A5 bus is not a stuck bus | Software |

Also: [Superseded decisions](#superseded-decisions), [Appendix A - Phase C review outcomes](#appendix-a---phase-c-review-outcomes),
[Appendix B - Citation tags in code](#appendix-b---citation-tags-in-code),
[How to add a decision](#how-to-add-a-decision).

---

## Architecture

<a id="d-01"></a>
### D-01 Two Arduino Q boards, each split into MCU (real time) and Linux

| | |
|---|---|
| **Decision** | Spacecraft = Arduino UNO Q: STMicroelectronics STM32U585 MCU (Arm Cortex-M33 up to 160 MHz, Arduino core on Zephyr) + Qualcomm Dragonwing QRB2210 (4 x Cortex-A53, Debian). Ground = Arduino VENTUNO Q: STM32H5F5 MCU (Cortex-M33 up to 250 MHz) + Qualcomm Dragonwing IQ8 QCS8275 (Ubuntu, 16 GB LPDDR5, 64 GB eMMC). On both boards the MCU does the real-time work (radio timing, sensors, CCSDS, lamps, keyboard) and Linux does storage, networking, the Python ground system and the web UI; they talk through the Arduino Router Bridge (MessagePack-RPC over a UART). |
| **Alternatives** | The boards are the owner's hardware baseline (contract 1), not an open choice. Within them: run everything on Linux (no deterministic timing), or everything on the MCU (no space for an archive, a web server or Python analytics). |
| **Reason** | The MCU gives deterministic timing without an operating-system scheduler between the radio and the flight loop; Linux gives a file system, a database and a browser-reachable UI. Ground CPU load is small: the simulator plus the full GDS used 0.81 s of CPU in 60 s (1.3 % of one core, 29 MB resident) during development, see [FAQ.md](FAQ.md). |
| **Evidence** | Arduino ABX00162 (UNO Q) datasheet section 1 and the MCU/MPU summary; ABX00181 (VENTUNO Q) datasheet "Main MPU / Real-time MCU / System Memory" table. |
| **Status** | Current. |

<a id="d-02"></a>
### D-02 All flight software on the MCU; the UNO Q Linux side is optional EGSE

| | |
|---|---|
| **Decision** | Every flight function (CCSDS stack, radio, sensors, FDIR, autonomy, SSR) runs in the MCU sketch. The UNO Q's Linux side runs only the EGSE ([../vgq1-spacecraft/python/main.py](../vgq1-spacecraft/python/main.py)): CADU archive, the SCLK partition / SDLS TM epoch, the SDLS TC counter restore, the CFDP relay of EGSE files, hardline ("umbilical") commanding and a status board. "The MCU flies without it." |
| **Alternatives** | Move parts of the flight software (archive, security state, file handling) to Linux. |
| **Reason** | The bench rack must be able to fail without the spacecraft failing. The MCU starts in seconds and is deterministic; the Linux side is not real-time. The price is explicit, bounded degradation without the EGSE: encrypted telemetry falls back to the flagged null SA after 120 s (D-30), CFDP is refused while no partition exists (EVR 0x0A06, D-27), and the TC anti-replay hold ends after 120 s (D-33). |
| **Evidence** | Phase B specification PB-6.3; EGSE module header; `config.h` `kSdlsEpochWaitS`, `kSdlsSnWaitS`. |
| **Status** | Current. |

<a id="d-03"></a>
### D-03 CCSDS protocols end to end, private spacecraft ID 0x0A7

| | |
|---|---|
| **Decision** | Space Packets (CCSDS 133.0-B-2) with CUC time (301.0-B-4) -> TM Transfer Frames (132.0-B-3) or USLP (732.1-B-2) -> SDLS (355.0-B-2) -> Reed-Solomon, randomizer and ASM (131.0-B-5). Uplink: TC packets -> TC or USLP frames -> SDLS -> COP-1 (232.1-B-2) -> BCH and CLTU (231.0-B-4). Files: CFDP class 1 (727.0-B-5). Compression: 121.0-B-3. Products: TDM (503.0-B-2), XTCE (660.0-B-2). Spacecraft ID 0x0A7. |
| **Alternatives** | An ad-hoc packet format on raw LoRa payloads; a hobby protocol (for example a plain checksum and sequence byte). |
| **Reason** | The project is a deep-space-style demonstrator: standard formats can be verified against official test vectors and test data, and decoded by standard ground tools (UDP links to Yamcs / OpenC3, XTCE mission database). SCID 0x0A7 is **not** registered with SANA: it is a private value for this terrestrial demonstration (change it on both ends together). |
| **Evidence** | `ccsds/ccsds_types.h` header and `kSpacecraftId`; status of each standard in [11_Standards_Status_and_Roadmap.md](11_Standards_Status_and_Roadmap.md); bit-exact flight/ground cross-checks in `tests/test_cross_check.py`. |
| **Status** | Current. |

<a id="d-04"></a>
### D-04 Build options are `config.h` defines; the E22 model is read at boot

| | |
|---|---|
| **Decision** | Compile-time switches are `#ifndef`-guarded defines in [../vgq1-spacecraft/sketch/src/config.h](../vgq1-spacecraft/sketch/src/config.h) (`VGQ_RADIO`, `VGQ_SX126X_CHIP`, `VGQ_RADIO_ON_SERIAL1`, `VGQ_DOWNLINK_FRAMING`, `VGQ_SDLS_TC`, `VGQ_SDLS_TM`, `VGQ_DEBUG_MONITOR`, `VGQ_CALLSIGN`) and at the top of the station sketch (`DSSQ_RADIO_SX126X`, `DSSQ_SX_CHIP`). The choice between the two E22 modules needs no build at all (D-09). Everything on the ground is in `station.toml`. |
| **Alternatives** | Build flags in the sketch profile (recorded: impossible); one sketch per variant. |
| **Reason** | App Lab always compiles the sketch's default profile, and an arduino-cli profile cannot carry build flags (no build-properties entry). A command-line build may still override a define with `--build-property "compiler.cpp.extra_flags=-D..."`; App Lab users edit the define. |
| **Evidence** | Contract 25.1 (arduino-app-cli `orchestrator.go`, arduino-cli `internal/arduino/sketch/profiles.go`); `config.h` header; `check_firmware.sh` compiles `sketch.ino` and `fsw/flight.cpp` for every switch value (flight build matrix). |
| **Status** | Current. |

<a id="d-05"></a>
### D-05 The repository is the two App Lab App folders

| | |
|---|---|
| **Decision** | The top-level folders `vgq1-spacecraft/` (UNO Q App) and `dssq-groundstation/` (VENTUNO Q App) are complete App Lab Apps: `app.yaml`, `python/main.py` (+ the `dssq` package and `python/config/station.toml` on the ground), `sketch/` (`sketch.ino`, `sketch.yaml`, sources). `data/` is created at run time. The release zips are plain archives of these folders with the bundled libraries added; `tools/`, `tests/`, `docs/` and the Nicla firmware stay outside the zips. Host-mode files live in `dssq-groundstation/host/`. |
| **Alternatives** | (recorded) The Phase A-C layout `spacecraft/vgq1_flight/` + `ground/dssq/` + `ground/station_io/`, with zips assembled by copying from several places. |
| **Reason** | App Lab bind-mounts **only the App folder** into the container (at `/app`), so the ground package must live inside the App; a zip that is simply an archive of a folder is easy to review and reproduce; `data/` is App Lab's reserved persistent folder. The layout follows the owner's working LINPACK App packages. |
| **Evidence** | Contract 18; PB-6.1 (arduino-app-cli `compose_template.go`: volumes = App home -> `/app`, `/dev`); `dssq-groundstation/python/main.py` `package_root()`; `tests/test_web.py::test_app_lab_layout_runs_main_in_a_subprocess`. |
| **Status** | Current (restructured with `git mv` at the start of Phase D, 2026-10-12). |

<a id="d-06"></a>
### D-06 Offline Apps: standard library only, no `requirements.txt`

| | |
|---|---|
| **Decision** | Neither App ships `python/requirements.txt`. The ground system runs on the Python standard library (3.11 or newer): AES-256-GCM in pure Python (D-35), the E22 serial port through `termios` with an exclusive `flock` instead of pyserial, `tomllib` instead of tomli. `arduino.app_utils` / `arduino.router_bridge` come from App Lab's Python image. The dashboard is one self-contained HTML file (no CDN, no web font). The sketches need no download (bundled libraries, D-40, D-41). Test tools are listed only in [../requirements-dev.txt](../requirements-dev.txt) for the PC. |
| **Alternatives** | (recorded) v2: `cryptography`, `pyserial`, `tomli` through a `requirements.txt`; a web-UI Brick. |
| **Reason** | Owner directive "the setup shouldn't require any internet to download the packages" (contract 23). App Lab's `run.sh` pip-installs `python/requirements.txt` when it exists and has a non-blank line - comment lines count - at the first start and after every change, which needs the internet. |
| **Evidence** | Contract 23 (verified in arduino-app-cli `compose_template.go` and app-bricks-py `run.sh`); commit `c8c3b04` (the whole ground suite passed with `cryptography`, `reedsolo`, `pyserial`, `tomli` and `xmlschema` all blocked; only the two optional cross-checks skipped); `tests/test_sdls.py::test_sdls_ae_needs_no_third_party_package`. |
| **Limits** | Not possible offline (documented, contract 23 rule 4): building the Nicla Sense ME firmware (Arduino IDE + "Arduino Mbed OS Nicla Boards" + Arduino_BHY2), App Lab and the board platform themselves. Host mode (D-55) installs `arduino-router-bridge==0.5.1` with pip, which needs the internet once. |
| **Status** | Current since `c8c3b04` (2026-10-11). |

<a id="d-07"></a>
### D-07 Pure C++ protocol and algorithm code, compiled and tested on a PC

| | |
|---|---|
| **Decision** | Code that needs no hardware lives in `src/ccsds/` (protocols), `src/fsw/` (executive helpers and algorithms, except `flight.cpp` and `status_display.cpp`) and `src/drivers/radio.cpp` (radio tables and LoRa maths), and never includes `Arduino.h`. [../tools/host_test/](../tools/host_test/) compiles exactly these flight sources with `g++` and writes vectors that the independently written ground decoder must reproduce bit for bit. |
| **Alternatives** | (recorded) A separate `src/core/` folder (contract 0), withdrawn for `src/fsw/` (contract 17.2, conflict C-12); testing only on hardware. |
| **Reason** | The real flight code is exercised on every push with warnings as errors and under AddressSanitizer + UBSan, and flight and ground cannot silently diverge. |
| **Evidence** | `tools/host_test/Makefile`; `host_test.cpp` header; `tests/test_cross_check.py` (flight CADUs, packets, command table, TC accept chain). |
| **Status** | Current. |

---

## Hardware and wiring

<a id="d-08"></a>
### D-08 EBYTE E22-400T33D is the default radio

| | |
|---|---|
| **Decision** | Both ends use the EBYTE E22-400T33D: SX1262-based UART module, 33 dBm (2 W), 3.3-5.5 V supply (>= 4.7 V for full power), 3.3 V logic, DIP pins. Defaults: rate code 2 = 2.4 kbit/s (the most sensitive: -128 dBm typical), channel 23, power code 0 (33 dBm). |
| **Alternatives** | (recorded) The E22-400T37S 5 W module (the v2 default, kept: D-09); the SX126x SPI module (kept as an option: D-10). |
| **Reason** | Owner's v3 hardware baseline (contract 1). The T33D runs from a 5 V supply (1.0 A typical, 1.2 A maximum at 33 dBm) instead of the T37S's 4.5-15 V / about 1.1 A at 12 V, and it shares the T37S's UART register protocol, so one driver serves both. |
| **Evidence** | E22-400T33D user manual v1.05 (supply, current, sensitivity -126/-128/-130 dBm min/typ/max at 2.4k, REG0..REG3); contract 2.1; commit `93cf4a3`. |
| **Open** | Power codes 1-3 = 30/27/24 dBm are **VERIFY on hardware**: the v1.05 manual's REG1 table was copied from the T30D (contract 2.1, test T-RF-07). |
| **Status** | Current since `93cf4a3`. |

<a id="d-09"></a>
### D-09 E22-400T37S kept, chosen at boot by a jumper on header A0

| | |
|---|---|
| **Decision** | An E22 build reads header A0 (D14, PA4) once at boot with the internal pull-up: 5 reads 1 ms apart, majority LOW = **E22-400T37S** (jumper to GND), open = the build default (E22-400T33D). The model is then a run-time value: power-code meaning (T37S always 37 dBm, REG1 = 0x24), the 50 % TX duty limit, the T37S fault-log decoding (`FF FF FF 01..04`), the sensitivity and the RF packet `radio_model` follow it; EVR 0x0308 reports it (WARN for a split reading). The ground follows the reported model when `[radio] model = "auto"` (or the key is absent). The SX126x option stays a build switch (different wiring). |
| **Alternatives** | Edit `VGQ_RADIO` and rebuild (the v3 start); a telecommand; two binaries. |
| **Reason** | Owner requirement: switching between the two E22 modules "must need NO source edit" (contract 33). A strap is read before the radio is configured, needs no uplink and survives every reboot; one binary works because both modules use the same register protocol. **Why A0:** a spare header pin used by nothing else; the board DTS gives PA4 to DAC1, but the Zephyr DAC driver enables the output only in `dac_channel_setup()` (on `analogWrite()`, never called), so `pinMode()` makes it a plain input. A0 is **not 5 V tolerant** (ABX00162 section 9.7): a jumper to GND only. |
| **Evidence** | `config.h` `PIN_RADIO_MODEL_STRAP`, `kStrapSamples`, `kStrapSettleUs`; host test `radio_model_strap_mapping` (`tools/host_test/test_radio.cpp`); `tests/test_radio.py::test_absent_or_auto_model_follows_the_spacecraft_and_explicit_stays`, `test_gds_follows_the_reported_e22_model_in_auto_mode`; commit `dd1f4c0`. Switching procedure and the TBH-02 wiring: [00_Beginner_Setup_Guide.md](00_Beginner_Setup_Guide.md), [03_Hardware_Integration.md](03_Hardware_Integration.md). |
| **Status** | Current since `dd1f4c0`. |

<a id="d-10"></a>
### D-10 SX126x SPI module as the long-range option, LoRa profiles 0-7

| | |
|---|---|
| **Decision** | Build option `VGQ_RADIO = 3` (flight) / `DSSQ_RADIO_SX126X 1` (station): an SX1268 (default; reference module EBYTE E22-400M33S) or SX1262 on SPI, through RadioLib 7.8.1 (D-40). The "rate code" selects one of eight LoRa profiles (CR 4/5, explicit header, CRC on, sync word 0x12, 12-symbol preamble): 0 = SF12/31.25 kHz ... 7 = SF7/125 kHz. Default **profile 1 = SF12 / 62.5 kHz**. |
| **Alternatives** | Only the UART modules (whose firmware hides spreading factor, bandwidth, SNR, frequency error, CAD and Rx boosted gain); an in-house SX126x driver (D-40). |
| **Reason** | Only direct chip access gives the slow, narrow LoRa settings (computed sensitivity -140.0 dBm at profile 1 against -128 dBm for the T33D at 2.4k) and per-packet SNR and frequency error (needed for AFC and Doppler, D-26). **Why profile 1 by default:** the contract marks it as the default without a written reason. The trade-off it represents, from the code's own numbers: profile 0 gains 3.1 dB but doubles the air time of one CADU (34.6 s against 17.3 s, `dssq.link.airtime_s`) and halves the tolerated TX-RX frequency offset (+-7.8 kHz against +-15.6 kHz at 433 MHz: +-25 % of BW, Semtech DS SX1261/2 rev 2.2 FERR_L), and both ends need a TCXO below 62.5 kHz. |
| **Evidence** | Contract 2.2 and 14; `drivers/radio.cpp` profile table; `dssq/link.py` `SX126X_PROFILES`, `lora_sensitivity_computed_dbm` (S = -174 + 10 log10(BW) + NF 6 dB + SNRmin(SF)); Semtech DS SX1261/2 rev 2.2 Table 3-8. |
| **Status** | Current. Hardware values (TCXO voltage, PA gain 11 dB, RF switch wiring) are **VERIFY on hardware**. |

<a id="d-11"></a>
### D-11 E22 UART on `Serial3` (header SDA/SCL pins), not on D0/D1

| | |
|---|---|
| **Decision** | On the UNO Q the E22 UART is `Serial3` = USART3 on the header pins silk-screened **SDA (PB11 = RX, from E22 TXD)** and **SCL (PB10 = TX, to E22 RXD)**. `Wire.begin()` is never called (`Wire` = I2C2 shares PB10/PB11). M0 = D2, M1 = D3, AUX = D7. `VGQ_RADIO_ON_SERIAL1 1` would move it to D0/D1. |
| **Alternatives** | `Serial1` on D0/D1, the obvious "UART pins". |
| **Reason** | `Serial1` (USART1, D0/D1) is also the **Zephyr console**: the bootloader banner and any kernel log line are printed on it. A transparent radio on that UART would transmit those bytes, and a log line in the middle of a CADU would corrupt the frame. |
| **Evidence** | ArduinoCore-zephyr variant `arduino_uno_q_stm32u585xx.overlay`: `serials = <&usart1>, <&lpuart1>, <&usart3>` (so `Serial3` = USART3), `arduino,router-serial = <&lpuart1>`, and the comment "usart1 ... is assigned to zephyr,console"; `config.h` `RADIO_SERIAL`; contract 1.1. |
| **Status** | Current (since v2, kept in v3). |

<a id="d-12"></a>
### D-12 External 10 kOhm pull-ups on M0/M1; T37S board jumpers removed

| | |
|---|---|
| **Decision** | 10 kOhm pull-ups to 3.3 V on M0 and M1 of either E22 module. For the T37S on the E22-400TBH-02 board: remove the M0/M1 jumper caps (they tie M1-GND and GND-M0) and the RXD/TXD caps (they connect the on-board CH340X USB-UART), then wire header pins 10 M0, 7 M1, 4 AUX, 5 TXD, 6 RXD. D3 (PB0, M1) is not 5 V tolerant. |
| **Alternatives** | Rely on the module's own pull-ups; leave the jumpers. |
| **Reason** | EBYTE calls the module pull-ups "very weak" ("can not be suspended"). With firm pull-ups the module sleeps - and cannot key its PA - while the MCU boots and its pins float. |
| **Evidence** | E22-xxxT37S manual v1.5 pins 9/10; E22-400T33D manual; E22-400TBH-02 manual; `config.h` E22 pin comments; commit `9a83f1e`. |
| **Status** | Current. |

<a id="d-13"></a>
### D-13 One sensor bus: `Wire2` = I2C3 on A4/A5 (the AS7265X optionally on the Qwiic socket), no multiplexer

| | |
|---|---|
| **Decision** | Every instrument is on one I2C bus: `Wire2` = I2C3 on the UNO Q header pins **A4 = SDA (PC1)** and **A5 = SCL (PC0)**, 3.3 V, 100 kHz, wired with header jumpers or soldered leads. Unique 7-bit addresses: RM3100 outboard 0x20, RM3100 inboard 0x23 (optional), Nicla Sense ME 0x2A, AS7265X 0x49. One set of 4.7 kOhm pull-ups to 3.3 V. No Qwiic connector or cable for any sensor; no I2C multiplexer. |
| **Alternatives** | (recorded) v2: one Qwiic bus (`Wire1`, I2C4) daisy-chained through the boards' Qwiic sockets (commit `ae6087d`); a TCA9548A multiplexer. |
| **Reason** | Owner directive "No Qwiic connectors ANYWHERE in the system" (contract 16.1; the CardKB exception is D-15). The core's UNO Q variant lists `i2cs = <&i2c2>, <&i2c4>, <&i2c3>`, so `Wire2` is I2C3 on A4/A5 and `Wire1` (I2C4) is the Qwiic socket. The addresses are distinct, so no multiplexer is needed (fewer parts, fewer failure points); one controller polls one target at a time, and open-drain lines cannot clash. 100 kHz tolerates a long boom cable. A4/A5 are **3.3 V only** ("use pull-ups to 3.3 V only ... not 5 V-tolerant in any mode", ABX00162), so every pull-up goes to 3.3 V and no breakout whose pull-ups follow its supply is powered from 5 V. |
| **Evidence** | Commit `eaefd3b`; variant overlay `i2cs`; `config.h` `SENSOR_WIRE`, `kI2cClockHz`; I2C bus clear (up to 9 SCL pulses + STOP) on the same pins (`kPinSensorSda` 18, `kPinSensorScl` 19). |
| **Revision 2026-10-12** | On the owner's request the AS7265X may instead be plugged into the UNO Q Qwiic socket (`Wire1` = I2C4, `config.h` `QWIIC_WIRE`), the only Qwiic use in the system. `As7265x::begin()` takes the Qwiic bus as an alternate: every probe (boot wait, absent and fault re-probes) tries the bus in use, then the other, and the driver stays on the bus that answered; EVR 0x0706 names it. The Qwiic bus is a second `I2cBus` without GPIO bus clear (PD12/PD13 are not Arduino pin numbers): its errors add to HK `i2c_errors` and FDIR `I2C_BUS`, whose response there is a controller re-initialisation (EVR 0x0605 `Qwiic I2C bus fault ...`). On Qwiic the AS7265X no longer provides the A4/A5 pull-ups (03 section 4.7). |
| **Status** | Current since `eaefd3b`. |

<a id="d-14"></a>
### D-14 Instrument set: Nicla Sense ME, AS7265X, one or two RM3100

| | |
|---|---|
| **Decision** | Nicla Sense ME as the attitude reference unit and environment sensor (custom firmware [../nicla_sense_me_aru/](../nicla_sense_me_aru/), two CRC-protected 32-octet pages at 0x2A), SparkFun AS7265X triad spectrometer (18 channels, 410-940 nm), PNI RM3100 science magnetometer on a boom (outboard 0x20, optional inboard 0x23, auto-detected). |
| **Alternatives** | (recorded) v2 candidates removed by the owner: MPU-9250, MMC5603, VEML7700, BME690, AS7343, TCA9548A, INA226, coarse sun sensor, PA thermistor (commit `ae6087d`); Nicla Sense Env (removed entirely in v3, contract 1). |
| **Reason** | Owner selection. PA temperature and supply health come from the E22 self-reports instead of extra sensors; bus temperature comes from the Nicla Sense ME. |
| **Evidence** | Contract 1; commits `ae6087d`, `bfd7215`. |
| **Status** | Current. |

<a id="d-15"></a>
### D-15 One CardKB on the VENTUNO Q header I2C; second operator in the web UI

| | |
|---|---|
| **Decision** | Exactly one M5Stack CardKB, on the VENTUNO Q header I2C bus `Wire` (pins silk-screened SDA = PH12, SCL = PH11; I2C4), with loose leads to its Grove socket through an adapter, 3.3 V first (or 5 V through a BSS138 level shifter). Neither board's Qwiic socket is used. With the two-person rule enabled, the second operator authorises in the web dashboard (optional `X-DSSQ-Auth` secret). |
| **Alternatives** | (recorded, in order) two CardKBs (#2 on Qwiic `Wire1`); a physical AUTH key switch on A4; one CardKB on the Qwiic port; a TCA9548A to keep two keyboards. |
| **Reason** | Latest owner directive (contract 17.1, superseding 16.1 and 16.3). The VENTUNO Q's only other MCU I2C bus is on the 60-pin high-density carrier connector, and two CardKBs cannot share a bus (fixed address 0x5F). Wire by silkscreen: the Arduino user manual says D20 = SDA / D21 = SCL while the variant overlay lists the GPIO numbers the other way round; the I2C4 peripheral fixes the roles. M5Stack specifies 5 V for the CardKB and does not publish its I2C levels, so 3.3 V is "try first" (nothing can be over-voltaged). |
| **Evidence** | Contract 16.3, 17.1, 21, 25.3 (ABX00181: header I2C on JDIGITAL 17 SDA PH12 / 18 SCL PH11; A4/A5 have no hardware I2C); station sketch header; conflict C-11. |
| **Status** | Current. |

<a id="d-16"></a>
### D-16 The web dashboard is the only station display

| | |
|---|---|
| **Decision** | No OLED or other screen. The station's MCU drives only lamps (AOS / ALARM / UPLINK), a buzzer and the CardKB; everything else is in the web dashboard (11 tabs) served by the GDS. |
| **Alternatives** | (recorded) v2 OLED pages on the station MCU (U8g2), an optional spacecraft OLED. |
| **Reason** | Unlimited detail (hundreds of values, charts, statistics windows) reachable from any device on the station LAN; no extra library. |
| **Evidence** | Commit `ae6087d` ("receiver display on the web only"); [13_Web_UI_Reference.md](13_Web_UI_Reference.md), [06_Displays.md](06_Displays.md). |
| **Status** | Current. |

<a id="d-17"></a>
### D-17 Board-powered radio supported; brown-out derating; reset cause by backup register

| | |
|---|---|
| **Decision** | (1) Powering the T33D from the boards is a **supported** configuration next to the external supply: spacecraft T33D VCC from UNO Q JANALOG pin 5 (USB VBUS pass-through) with the UNO Q fed through USB-C from a regulated 5 V supply able to give the board's 3 A plus the radio's 1.2 A peaks; ground T33D VCC from the VENTUNO Q `+5V_LIMITED` (2.8 A load switch). (2) After a brown-out reset the radio starts at power code `kBrownoutPowerCode` = 2 (EVR 0x0608) until the operator restores it with TXPWR. (3) Brown-out is told apart from power-on by a magic word in TAMP backup register 31. |
| **Alternatives** | External supply only; trust the RCC reset flags alone. |
| **Reason** | Owner decision to test the T33D powered from the boards (contract 15). JANALOG pin 5 is `5V_USB_VBUS`, not the diode-OR 5 V rail (contract 16.2, ABX00162 schematic), so VIN power would leave it unpowered. Derating after a brown-out keeps a weak supply from resetting the board again. The RCC flags cannot separate a power-up from a brown-out on the STM32U5: Phase C finding C16/C56 showed **every cold power-up** was reported as BROWNOUT (derated power, beacon URGENT, ground YELLOW); the backup-register marker survives a reset but not a power-up. |
| **Evidence** | Contract 15, 16.2, 25.3; `flight.cpp` `read_reset_cause()`; `config.h` `kBrownoutPowerCode`; commit `fbf16cf`; test T-HW-07 (supply under transmit) in [08_Verification_and_Test.md](08_Verification_and_Test.md). |
| **Status** | Current. Supply behaviour under transmit is **VERIFY on hardware**. |

<a id="d-18"></a>
### D-18 Channel grid default 433.125 MHz, allowed-channel list, callsign in clear

| | |
|---|---|
| **Decision** | Channel grid f = 410.125 MHz + CH x 1 MHz (CH 0-83, all models); default CH 23 = 433.125 MHz. The GDS refuses CHANNEL / LINKCHAN outside `[radio] allowed_channels` (default 20-24 = 430.125-434.125 MHz). The spacecraft callsign (`config.h` `VGQ_CALLSIGN`, default `N0CALL`) is sent in EVR 0x0002 every 600 s (`kCallsignPeriodS`) and as the idle data of every OID frame ("DE <callsign> VGQ-1 "), which is never encrypted; the ground station identifies its own transmissions with `[station] callsign` (D-61). |
| **Alternatives** | A satellite-segment frequency; identification only in encrypted frames. |
| **Reason** | The default list stays in the 70 cm amateur band of ITU Regions 1 and 3 and avoids the 435-438 MHz satellite segment; amateur identification must stay readable even when the payload is encrypted (contract 5). Operators must check their own licence and band plan ([09_Regulatory_and_Safety.md](09_Regulatory_and_Safety.md)). |
| **Evidence** | E22 manuals (channel formula); `station.toml` `[radio]` comments; `config.h` callsign block; contract 5. |
| **Status** | Current. |

---

## Link layer and coding

<a id="d-19"></a>
### D-19 200-octet frames + RS(255,223) = 236-octet CADU = one LoRa packet

| | |
|---|---|
| **Decision** | Fixed 200-octet transfer frames (TM: 6 header + 188 data + 4 OCF/CLCW + 2 FECF; USLP the same total). Each frame gets Reed-Solomon (255,223), E = 16, dual basis, shortened by 23 virtual-fill octets (+32 parity), the 255-bit randomizer and the 4-octet ASM 0x1ACFFC1D: **4 + 200 + 32 = 236 octets = one CADU = one radio packet**, inside the E22's 240-octet sub-packet. |
| **Alternatives** | Longer frames split over several radio packets; shorter frames (more header overhead); no RS. |
| **Reason** | The E22 delivers a whole packet or nothing (it drops packets that fail its LoRa CRC), so the unit of loss is the radio packet. With one CADU per packet every loss is exactly one frame, visible as a frame-count gap, and the receiver never has to re-assemble a CADU from fragments. RS still corrects residual errors (up to 16 octets per codeblock) and its correction counts are a link-quality measurement the E22 does not otherwise expose. Cost: with SDLS AE, 158 of the 236 octets are packet data; at 2.4 kbit/s one CADU keeps the T33D busy 1.267 s (UART transfer + air time, `dssq.link.busy_s`), so with the 50 % duty ceiling the downlink carries at most about 62 octet/s of packets (158 octets per 2.53 s). |
| **Evidence** | `ccsds/ccsds_types.h` (`kTmFrameLen` 200, `kCaduLen` 236, comment "One CADU must fit into ONE E22 sub-packet (240 octets)"); E22 manuals REG1[7:6] = 00 (240 octets); CCSDS 131.0-B-5 sections 4 (RS) and 9-10 (ASM, randomizer); ground RS decoder cross-checked (`tests/test_cross_check.py::test_flight_rs_vector`). |
| **Status** | Current since v2 (`6c53786`). |

<a id="d-20"></a>
### D-20 No inner LDPC/turbo/convolutional code; 255-bit randomizer

| | |
|---|---|
| **Decision** | No LDPC, turbo or convolutional code inside the packet; the 255-bit CCSDS pseudo-randomizer (not the 131 071-bit sequence of 131.0-B-5). |
| **Alternatives** | The stronger CCSDS codes of 131.0-B-5; the longer randomizer. |
| **Reason** | Inside a packet that the E22 either delivers or drops, a stronger inner code cannot recover the dropped packets: the gain would be zero (the SX126x path is different, see D-21). The longer randomizer avoids spectral lines on high-rate links; at <= 62.5 kbit/s over LoRa, which whitens its own payload, it changes nothing, and the 255-bit sequence is what ground tools decode by default. |
| **Evidence** | Old docs/01 section 6 and docs/11 section 2 (v2 design record); CCSDS 131.0-B-5. |
| **Status** | Current. |

<a id="d-21"></a>
### D-21 "RS rescue": concatenated coding on the SX126x path

| | |
|---|---|
| **Decision** | With the SX126x the modem's CRC stays on, but a payload that fails it is still delivered, flagged `crc_ok = false` (station: only when it is exactly one 236-octet CADU; flight: any length, for uplink CLTUs). The ground runs the normal ASM search and RS decode: a frame whose RS decode succeeds is accepted and counted "RS-rescued", otherwise it is counted and dropped. The flight does the same for uplink CLTUs through BCH, FECF and SDLS (`tc_rescued`, EVR 0x0307). |
| **Alternatives** | Drop every CRC-failed packet (what the E22 firmware does internally). |
| **Reason** | It turns LoRa's Hamming code (inner) and RS (outer) into a concatenated code, as on deep-space links. No check is bypassed: FECF, RS, BCH and SDLS still decide. |
| **Evidence** | Contract 20; RadioLib 7.8.1 `readData()` fills the buffer while returning `RADIOLIB_ERR_CRC_MISMATCH` (verified in the source); commit `dd1f4c0`; `RxPacket::crc_ok`. |
| **Status** | Current. Rescue rates are **VERIFY on hardware**. |

<a id="d-22"></a>
### D-22 Virtual channels VC0-VC3 + OID, fixed priority, priority reserve

| | |
|---|---|
| **Decision** | VC0 real-time engineering (HK, RF, EVR, CMDVER, TIMECORR, RFSCAN), VC1 real-time science (MAG, ATT, SPEC, ENV, SPECBURST), VC2 SSR playback, VC3 CFDP file delivery, OID frames (TM VC7 / USLP VCID 63) when nothing else is queued. Downlink priority VC0 > VC1 > VC2 > VC3 > OID. The last 256 octets (`kVcReserveOctets`) of each 1024-octet VC buffer are reserved for priority packets (EVRs of severity WARN or higher, the epoch EVRs). VC3 is paced so it never holds more than two packets (`kCfdpVc3BacklogMax` = 320 octets). |
| **Alternatives** | One channel for everything; round-robin. |
| **Reason** | Engineering data and alarms must never wait behind science or files; OID frames keep the link and the CLCW (uplink acknowledgements) alive and carry the clear-text identification. |
| **Evidence** | `ccsds_types.h` `enum Vc`; `fsw/datalink.h` `Downlink`, `kVcReserveOctets`; `ccsds/tm.h` `VcStream::kBufLen`; Phase B review finding 13 (priority reserve); `config.h` `kCfdpVc3BacklogMax`. |
| **Status** | Current. |

<a id="d-23"></a>
### D-23 TM frames by default, USLP selectable, uplink format auto-detected

| | |
|---|---|
| **Decision** | Downlink framing is TM (132.0-B-3) by default; `VGQ_DOWNLINK_FRAMING 1` selects USLP (732.1-B-2), with the same 200-octet frame and CADU. The ground auto-detects per frame (first nibble 0xC = USLP). The flight accepts TC and USLP uplink frames (TFVN '00' or '1100'); the ground sends TC unless `[link] tc_framing = "USLP"`. |
| **Alternatives** | USLP only (the newer standard); TM only. |
| **Reason** | TM/TC frames are what deep-space missions and ground tools decode by default; USLP is implemented for interoperability tests. Limit: SEQLOAD and MAGCAL (26-octet TC packets) do not fit a 64-octet USLP TC frame with SDLS AE; the GDS refuses them before queueing, so `tc_framing = "TC"` is kept for them (conflict C-08). |
| **Evidence** | Contract 6; `ccsds/uslp.*`; `tests/test_uslp.py` (flight-ground bit-exact); commit `9c7d893`. |
| **Status** | Current. |

<a id="d-24"></a>
### D-24 Downlink paced by measured capacity; 1.5 s frame floor; 50 % TX duty

| | |
|---|---|
| **Decision** | The frame period follows the measured radio busy time (per mode: x4.0 BOOT/SAFE ... x2.0 ENCOUNTER/TEST) with a floor of `kMinFramePeriodMs` = 1500 ms and a TX duty limit of `kMaxTxDutyPermille` = 500 (50 %). Periodic packet periods are **stretched at run time** so their load stays <= 75 % of the measured link capacity, and the routine-EVR budget follows the capacity left (never above the v2 rate of 10 per minute). The E22 busy time used for all of this is never shorter than the modelled air time of the frame. The duty value the spacecraft reports is averaged over whole transmission cycles (D-63). |
| **Alternatives** | (recorded) v2: a fixed cadence table per mode. |
| **Reason** | Phase B review finding 6: on SX126x profiles 0-4 the fixed table overloads the link (profile 1 SAFE: 4.09 octet/s of HK + RF + TIMECORR against 2.21 octet/s of capacity), and a VC that always grows overflows (conflict C-27). Phase C finding C24: both EBYTE manuals (5.6.2) say AUX can rise while the last packet is still on air, so AUX alone could understate the busy time and break the 50 % PA duty limit. |
| **Evidence** | `fsw/datalink.h` `LoadPlan`, `EvrGate` (host test `downlink_load` over every model, code, mode and framing); `drivers/radio.h` `E22TxTracker` (`min_busy_ms`); PB-3.3; commit `fbf16cf`. |
| **Status** | Current. |

<a id="d-25"></a>
### D-25 Every sensitivity value carries its source

| | |
|---|---|
| **Decision** | Each receiver sensitivity used by the link budget, the margin and the link manager is labelled `datasheet`, `computed`, `estimated` or `measured`: E22 at 2.4k from the manuals; faster E22 rates estimated as S(2.4k) + 10 log10(rate / 2400); SX126x from Semtech Table 3-8 where the (SF, BW) pair is listed, else computed. Measured values come only from test T-RF-03 (`tools/link_budget.py sensitivity`) into `[radio.sensitivity_measured]` and then override the others. |
| **Alternatives** | One number per setting with no provenance. |
| **Reason** | "Never present an invented number as a measured fact" (contract 0 and 13). The table in the contract (profile 0 -143.0, profile 7 -124.5) differed from the formula and the datasheet; the code follows the formula (-143.1) and the datasheet (-124.0) (conflict C-14). |
| **Evidence** | Contract 2.2, 2.3, 14; `drivers/radio.cpp`, `dssq/link.py` `sensitivity()`; [02_Link_Budget_and_RF_Analysis.md](02_Link_Budget_and_RF_Analysis.md). |
| **Status** | Current. The measured table is empty until the hardware test is run. |

<a id="d-26"></a>
### D-26 SX126x: Rx boosted gain with retention, CAD, ground AFC

| | |
|---|---|
| **Decision** | Rx boosted gain on (register 0x08AC = 0x96) with the retention list 0x029F-0x02A1 = 01 08 AC so it survives warm sleep, re-asserted after every reset and read back (RF flag b0 = verified). CAD before every transmission when LBT is on. The ground runs automatic frequency correction (robust median of 8 packets with SNR >= SNRmin + 3 dB, dead band max(50 Hz, 2 % BW), slew <= 25 % of FERR_L per step, bound 0.8 FERR_L, 600 s hold through LOS, a one-time sign self-check) and sends the offset to the station MCU (`rf_foff`). |
| **Alternatives** | Default gain; no frequency tracking (rely on crystals). |
| **Reason** | Boosted gain buys about 4 dB (Table 3-8: -137 against -133 dBm at BW 125 kHz / SF12) and is not retained across sleep unless the retention registers are written (DS 9.6). The tolerated TX-RX offset at SF12 / 31.25 kHz is only +-7.8 kHz (18 ppm at 433 MHz); crystal drift with temperature can exceed it. |
| **Evidence** | Contract 14, 24.8; `drivers/sx126x.h` header (DS 9.6, 13.x, 15.x); `dssq/link.py` `AfcLoop`; the AFC tests in `tests/test_radio.py` (commit `a970823`). |
| **Status** | Current. The frequency-error **sign** convention is **VERIFY on hardware** (the AFC self-check flips it once if needed). |

<a id="d-27"></a>
### D-27 CFDP class 1 (unacknowledged) with an EGSE relay

| | |
|---|---|
| **Decision** | Files go down with CCSDS 727.0-B-5 class 1: Metadata, File Data, EOF, modular checksum, one PDU per Space Packet (APID 0x030) on VC3. Sources: 0 = the SSR contents (built on the MCU, 128-octet segments; SSR recording paused meanwhile), 1 = the EGSE event log, 2 = the latest EGSE CADU archive (built on the UNO Q Linux side by `cfdp_tx.py` with sequence numbers >= 0x80000000 and 96-octet segments, relayed PDU by PDU through `cfdp_pdu`). The ground writes complete files, or `.partial` + a JSON report listing missing ranges. CFDPPUT is refused (CE_BUSY, EVR 0x0A06) while the SCLK partition is 0. A `RelayGate` admits only the PDUs of the transfer the MCU authorised. |
| **Alternatives** | CFDP class 2 (acknowledged, retransmissions); SSR playback only (still available: PLAYBACK on VC2). |
| **Reason** | The contract specifies class 1 without a written reason. Engineering rationale: class 1 needs no uplink traffic during a transfer, which suits a half-duplex link whose low-rate uplink is shared with commanding; gaps are reported, not hidden, and a transfer can be repeated. 96-octet relay segments keep the hex text of a PDU (<= 220 characters) inside the Router Bridge's 256-octet request buffer. Refusal at partition 0: CFDP sequence numbers would otherwise repeat across boots (Phase B review finding 4); RelayGate: review finding 5. |
| **Evidence** | Contract 7; `ccsds/cfdp.h` (`kCfdpFlightSegLen` 128, `kCfdpRelaySegLen` 96, `kCfdpEgseSeqFlag`); `tests/test_cfdp.py`; host test vectors `cfdp_pdus.txt`; commit `3fc7d51`. |
| **Status** | Current. The EOF fault-location TLV on a non-zero condition is kept (conflict C-18; the ground accepts both). |

---

## Security

The threat model, key handling and procedures are in [17_Security_Model.md](17_Security_Model.md).

<a id="d-28"></a>
### D-28 SDLS AE by default; AUTH for amateur operation; NONE on the bench

| | |
|---|---|
| **Decision** | Three services (CCSDS 355.0-B-2): **AE** = AES-256-GCM authenticated encryption on TC (SPI 2) and TM (SPI 3) - the **default** on both ends (`VGQ_SDLS_TC 2`, `VGQ_SDLS_TM 2`; `[sdls] tc_service = tm_service = "AE"`); **AUTH** = HMAC-SHA-256 truncated to 128 bits on TC only (SPI 1, the v2 format kept byte-exact); **NONE**. Without a key both ends run NONE and say so (HK, YELLOW alarm). |
| **Alternatives** | (recorded) v2: authentication only ("amateur rules forbid obscuring content"); encryption without authentication; authentication of TM. |
| **Reason** | AE is implemented at the owner's request (contract 5) and makes commands both secret and tamper-proof. Encryption that obscures content is **generally not permitted on amateur frequencies**: for amateur operation use AUTH (telemetry readable, nobody else can command) or NONE ([09_Regulatory_and_Safety.md](09_Regulatory_and_Safety.md)). No authentication-only TM service is defined here. A frame that fails SDLS never touches FARM or anti-replay state, so a spoofer cannot even cause a lockout. |
| **Evidence** | Contract 5; `ccsds/sdls.h` header; `config.h` `VGQ_SDLS_TC` / `VGQ_SDLS_TM` (compile-time `#error` for invalid values); `tests/test_sdls.py`; commit `9c7d893`. |
| **Status** | Current since `9c7d893` (AE added; AUTH kept). |

<a id="d-29"></a>
### D-29 One master key, per-direction derived keys, never in Git or a zip

| | |
|---|---|
| **Decision** | One random 256-bit master key from the OS CSPRNG (`secrets.token_bytes(32)`), written by [../tools/gen_sdls_key.py](../tools/gen_sdls_key.py) to the flight header `sdls_key.h` and the ground key file `sdls_key.hex` (mode 0600; both git-ignored, never packaged). AE keys are derived per direction: K_tc_ae = HMAC-SHA-256(master, "VGQ1-SDLS-TC-AE"), K_tm_ae = HMAC-SHA-256(master, "VGQ1-SDLS-TM-AE"). AUTH uses the master key directly. |
| **Alternatives** | Separate random keys per direction (more files to keep in step); a key typed into a configuration file. |
| **Reason** | One secret to generate, copy and protect. Each direction gets its own GCM key, and the IV also carries the direction ('TC' / 'TM'), so an uplink nonce can never equal a downlink nonce. Keys are generated **after** importing the Apps: ONE run of the tool on one machine writes both files (`--flight-header`, `--ground-key`; defaults `vgq1-spacecraft/sketch/src/sdls_key.h` and `dssq-groundstation/data/sdls_key.hex`), and each file is copied to its board; a second run would make a different key. |
| **Evidence** | Contract 5; `ccsds/sdls.cpp` `sdls_derive_key()`; `tests/test_sdls.py::test_key_derivation_known_answer`, `test_gen_sdls_key_writes_both_files`, `test_gen_sdls_key_into_imported_app_folders`; `.gitignore`. |
| **Status** | Current. |

<a id="d-30"></a>
### D-30 TM nonce safety: epoch = EGSE boot counter, hold, null-SA fallback

| | |
|---|---|
| **Decision** | GCM IV = direction u16 ('TC' 0x5443 / 'TM' 0x544D) \| epoch u16 \| 0x00000000 \| sn u32. TC: epoch 0, sn = the ground's persistent counter. TM: epoch = the SCLK partition, a **new persisted EGSE boot-counter value (1-65534) for every MCU boot**; sn restarts at 1 per epoch. Until it has an epoch the flight **holds** VC0-VC3 (only OID frames go out); after `kSdlsEpochWaitS` = 120 s without one it sends **clearly flagged null-SA frames** (SPI 0, IV and MAC zero, data clear; EVR 0x0121 WARN, HK `sdls_tm_state` = 3) and resumes protection as soon as an epoch arrives. The flight refuses an epoch of 0 or lower than its current one; the same epoch again is a no-op. The ground requires (epoch, sn) to increase, persists that state (`sdls_tm_replay.txt`) and raises a RED `tm_epoch_regressed` alarm for a valid frame with a lower epoch. |
| **Alternatives** | A random IV per frame (no counter to persist, but a 96-bit random nonce needs a good entropy source on the MCU and gives no replay order); a flight-side persistent counter in flash (wear, and a power cut during the write); guessing an epoch after a timeout. |
| **Reason** | GCM nonce reuse under one key is catastrophic (it reveals the XOR of plaintexts and the authentication key). The MCU keeps no state across a reset, so the Linux side, which has a file system, provides a counter that only goes up. Sending clearly flagged clear frames after 120 s is safer than guessing and keeps the spacecraft observable without the EGSE. |
| **Evidence** | Contract 5; `ccsds/sdls.h` / `sdls.cpp` `SdlsTm::set_epoch`, `poll_epoch_timeout`, `state`; PB-6.3 rules; NIST SP 800-38D section 8 (IV uniqueness); `tests/test_sdls.py::test_flight_null_sa_frame`, `test_tm_epoch_regression_is_counted`, `test_tm_replay_state_persisted_and_restored`. |
| **Status** | Current; hardened by D-31. |

<a id="d-31"></a>
### D-31 An epoch value is offered to the MCU at most once, ever

| | |
|---|---|
| **Decision** | The EGSE persists `boot_count + 1` **before** offering it, takes it out of "pending" **before** the `set_partition` call, and **retires** it on any call without a definite answer: it is never offered again, and a new value follows once a status shows the MCU does not hold it (back-off 1 s doubling to 60 s, so a persistent fault uses at most one value per minute: 65 534 values last more than 45 days). An explicit refusal raises the counter above the MCU's reported epoch (at most 3 refusals in a row, then issuing stops). A new value is issued on every MCU boot, at EGSE start and whenever the MCU reports epoch 0. At 65 534 issuing stops: a new key is required. |
| **Alternatives** | (recorded) PB-6.3 rule 3 as first specified: retry an unanswered call with the **same** value. |
| **Reason** | Phase C critical finding C13 and major finding C12: an unanswered call may still have been applied, and the same value can then reach two MCU boots (for example when the answer is lost and the MCU reboots silently, or when a late request is served by the next boot). That restarts sn under the same (key, epoch): GCM nonce reuse. Skipping a value never endangers a nonce. |
| **Evidence** | Commit `fbf16cf` ("interleaving tests (exhaustive and randomised) find no nonce reuse; the previous code reused in 255 of 300 runs"); `tests/test_cfdp.py::test_epoch_nonce_safety_finding_scenarios`, `test_epoch_nonce_safety_random_interleavings` (60 seeds x 400 events by default), `test_epoch_nonce_safety_exhaustive_short_orderings` (13^3 orderings; depth 4 = 28 561 orderings also passed in Phase C), `test_egse_partition_unanswered_value_is_never_offered_again`. |
| **Status** | Current since `fbf16cf`. |

<a id="d-32"></a>
### D-32 TC sequence-number file: atomic, never lowered, locked, self-healing

| | |
|---|---|
| **Decision** | The ground's TC sequence number (`<archive>/sdls_sn.txt`, shared by the GDS and the hardline tool `dssq.cltu`) is written atomically (temporary file, fsync, rename, directory fsync) **before** the frame that uses it leaves, under an exclusive `flock` on `sdls_sn.txt.lock` held from the read to the rename; every sender (the GDS and `dssq.cltu`) re-reads the file under that lock and takes max(file, memory) + 1, and nothing ever writes a lower number (also `observe_spacecraft_sn()` re-reads the file first). An **empty or corrupt file, or a missing one for a key used with TC AUTH or AE, blocks only the TC uplink** (the GDS with or without an archive; `dssq.cltu` refuses) until an HK reports the spacecraft's `sdls_last_sn` (one made at least 120 s after the flight's boot); the counter then continues on the time floor, 4 numbers per second since 2026-01-01, above every number the key can have used (D-65). While running, every HK from an AE-protected frame raises the number to max(sn, `sdls_last_sn`); an HK from an unprotected frame never moves a running counter. The one exception is the release of a hold at a station whose TM service is NONE: there no protected HK can come, so any HK ends the hold, and the floor makes its reported value irrelevant (D-65). Without a key or with TC NONE no number is used, so a corrupt file blocks nothing. The 32-bit space is never wrapped: at its end a new key is required. |
| **Alternatives** | (recorded) v2: `int(read_text() or 0)` and a plain write per frame; refuse to start the GDS on a corrupt file (the Phase B reviewer's proposal). |
| **Reason** | An AE sequence number is a GCM nonce. Phase B review finding 18 (crash safety); Phase C findings C10 (a lower value written over a file the hardline tool had advanced) and C11 (two processes taking the same number). The deliberate deviation from the reviewer's proposal: blocking only the uplink keeps telemetry, alarms and the web UI working, and the file heals itself from protected telemetry. |
| **Evidence** | `dssq/ccsds/sdls.py` module header and `SdlsSender`; `tests/test_sdls.py::test_sn_file_is_written_atomically_before_use`, `test_corrupt_sn_file_blocks_until_a_protected_hk`, `test_unprotected_hk_never_moves_the_sn`, `test_sn_file_lock_serialises_the_read_modify_write`, `test_two_processes_never_share_a_sequence_number`, `test_held_sender_never_writes_below_the_file`. |
| **Status** | Current; the handling of a missing file was replaced in the Phase D final fix round (D-65). Backup and loss procedure: [17_Security_Model.md](17_Security_Model.md). |

<a id="d-33"></a>
### D-33 TC anti-replay held until the EGSE restores the counter

| | |
|---|---|
| **Decision** | The flight's last accepted TC sequence number lives in RAM. After a reset, authentic TC frames are verified but **refused** (`SDLS_NOT_RESTORED`, EVR 0x0123) until the EGSE restores the last value (`set_sdls_sn`, from `sdls_last_sn.txt`); after `kSdlsSnWaitS` = 120 s without a restore they are accepted again from the RAM counter (EVR 0x0124). The EGSE never lowers its stored copy. |
| **Alternatives** | Accept from 0 after every reset (the earlier behaviour); keep the counter in backup registers. |
| **Reason** | Phase C finding C17: with the counter at 0, any authentic frame recorded earlier would pass the replay check, execute, and then lower the persisted counter. The 120 s bound keeps the spacecraft commandable without the EGSE (D-02). |
| **Evidence** | `ccsds/sdls.h` `hold_for_restore()` / `release_hold()`; `config.h` `kSdlsSnWaitS`; EGSE `_do_sn()` ("never lower the stored counter"); commit `fbf16cf`. |
| **Status** | Current. |

<a id="d-34"></a>
### D-34 A change of SDLS status on a VC is a packet discontinuity

| | |
|---|---|
| **Decision** | The ground packet extractor treats a change between protected and unprotected frames on the same VC as a discontinuity: the partial packet is dropped and extraction resynchronises on the next First Header Pointer. Each packet carries a `protected` flag that is true only when every octet came from verified frames; only such HK may move the TC sequence number (D-32). The FHP is also checked against the buffered packet length. |
| **Alternatives** | Track protection per frame only. |
| **Reason** | Phase C critical finding C9: the null SA (D-30) is accepted but unauthenticated, so a forged null-SA frame could start a packet that a following genuine AE frame completed; the GDS would have treated attacker data as protected (and moved the TC SN). Finding C1: after SSRCLEAR a stale partial packet could be spliced onto new data with no frame-count gap. |
| **Evidence** | Interface decision I2; `dssq/ccsds/packets.py`; `tests/test_packets.py::test_null_sa_frame_cannot_splice_into_an_ae_frame`, `test_stale_partial_packet_is_not_spliced_after_a_vc_reset`; `tests/test_end_to_end.py::test_spacecraft_sn_only_from_an_all_protected_hk`. |
| **Status** | Current since `fbf16cf`. |

<a id="d-35"></a>
### D-35 Pure-Python AES-256-GCM on the ground, a different formulation

| | |
|---|---|
| **Decision** | `dssq/ccsds/aes_gcm.py`: AES-256 (FIPS-197) and GCM (NIST SP 800-38D), 96-bit IVs and 128-bit tags only, tag checked with `hmac.compare_digest` before any plaintext is produced. Deliberately a different formulation from the flight C++ (32-bit T-tables and Shoup's 8-bit GHASH tables on the ground; the S-box is generated from its definition and compared with the flight's literal table). |
| **Alternatives** | (recorded) The `cryptography` package (needs pip, D-06). |
| **Reason** | Offline installation (D-06), and agreement between two independent formulations is a real cross-check. Speed is ample: 280-325 us per 200-octet frame on a 2.8 GHz x86 (CPython 3.11-3.13); the fastest E22 rate needs at most about 11 ms of CPU per second. |
| **Evidence** | Module header; `tests/test_sdls.py` (FIPS-197 C.3, GCM test cases 13-16 of McGrew & Viega, every flight AES/GCM vector byte-identical, optional randomized cross-check against `cryptography`); commit `c8c3b04`. |
| **Status** | Current. The speed on the VENTUNO Q is **VERIFY on hardware** (`python -m dssq.ccsds.aes_gcm` prints it). |

<a id="d-36"></a>
### D-36 Web UI security: token, Host checks, hash CSP, bounded server

| | |
|---|---|
| **Decision** | Every POST is commanding. With `[gds] http_token` set a POST needs `X-DSSQ-Token` (constant-time compare); a server bound to a non-loopback address **without** a token refuses every POST (read-only dashboard). In App Lab and in host mode (`--app-data`) the server listens on all interfaces and the token is created once (`secrets.token_hex(16)`, `<App>/data/http_token`, mode 0600). AUTH confirmations may also need the second operator's `X-DSSQ-Auth`. POSTs must be `application/json`; Origin must match Host; Host must be loopback (loopback bind) or an IP literal, a single-label or `.local` name, the machine's name, or an `http_allowed_hosts` entry (LAN bind). One inline script allowed by its SHA-256 in the Content-Security-Policy; all telemetry text inserted as text; `nosniff`, `no-store`, `DENY` framing, `no-referrer`. Bounded resources: 32 connections, 8 per address, a 10 s total request deadline, 131 072-octet bodies. The token lives in `sessionStorage`, the AUTH secret is asked every time. The UDP telecommand interface for an external mission control system is a separate path without authentication and listens on loopback only (D-62). |
| **Alternatives** | (recorded) v2: `innerHTML` rendering and the token in `localStorage`; no Host check on a LAN bind; per-operation socket timeouts only. HTTPS (not available in the standard-library server). |
| **Reason** | Phase B review finding 2 (stored XSS through telemetry text, e.g. a forged callsign in OID frames), Phase C findings C38 (32 slow clients locked everyone out) and C39 (DNS rebinding on a LAN bind). Telemetry is untrusted even with SDLS: OID frames are never protected and NONE is a valid service. |
| **Evidence** | `dssq/display/web.py` module header; `tests/test_web.py::test_token_and_exposure_rules`, `test_csrf_and_dns_rebinding_defences`, `test_lan_server_refuses_foreign_host_names`, `test_slow_clients_cannot_hold_the_slots`, `test_dashboard_has_no_external_resource_and_no_inline_handler`, `test_dashboard_token_handling_and_text_only_rendering`; commit `6d4028e`. |
| **Status** | Current. Plain HTTP: the token crosses the LAN in clear (residual risk, [17_Security_Model.md](17_Security_Model.md)). |

---

## Commanding

<a id="d-37"></a>
### D-37 One two-stage command parser for live and stored commands

| | |
|---|---|
| **Decision** | `parse_tc_header()` checks the TC packet header; `parse_user_data()` decodes and range-checks opcode and arguments with no side effects. The same second stage validates commands loaded into the stored-sequence table, so a stored command gets exactly the checks a live one gets. The ground dictionary applies the same rules (`CommandDef.check`), so the ground never queues a command the spacecraft would refuse for its arguments. The flight table has 32 opcodes (0x01-0x20). |
| **Alternatives** | A separate validator for stored commands. |
| **Reason** | One rule set, tested once; no divergence between live and stored commanding (autonomy hand-off, contract 29). |
| **Evidence** | `fsw/commands.h`; host test vector `command_table.txt` compared with the ground by `tests/test_cross_check.py::test_flight_command_table_matches_ground`; generated [05_Telemetry_and_Command_Dictionary.md](05_Telemetry_and_Command_Dictionary.md). |
| **Status** | Current. |

<a id="d-38"></a>
### D-38 CMDVER carries the command source in the stage octet

| | |
|---|---|
| **Decision** | The CMDVER packet layout is unchanged (8 octets). Its `stage` octet carries the stage in bits 1..0 (2 EXECUTED, 3 FAILED) and the **command source in bits 7..6**: 0 = Type-AD frame (`tc_seq` = its N(S)), 1 = Type-BD frame or hardline, 2 = stored sequence. The ground matches AD records by N(S) + opcode only for source 0, uses the BD wildcard only for source 1, and never matches a stored command to a FOP record. |
| **Alternatives** | (recorded) Sentinel values in `tc_seq` (0xFF = bypass, 0xFE = stored); widening `tc_seq` to 16 bits (layout change). |
| **Reason** | Phase C findings C2 and C55: an AD frame's N(S) takes every value 0-255 (FARM-1 counts modulo 256, CCSDS 232.1-B-2), so the sentinels collided with real frames 254 and 255 and the ground dropped or misattributed those verifications. Two spare bits of the stage octet keep the layout. |
| **Evidence** | Interface decision I1; `fsw/tlm_packets.h` `CmdSource`, `kCmdVerSourceShift`; `dssq/dictionary.py` `CMD_SOURCES`; commit `fbf16cf`. |
| **Status** | Current since `fbf16cf`. |

<a id="d-39"></a>
### D-39 Hazardous commands: confirmation, optional two-person rule, self-healing changes

| | |
|---|---|
| **Decision** | Eight commands are hazardous (AIRRATE, SSRCLEAR, REBOOT, CMDLOSS, FDIRMASK, TXINHIBIT, HIBERNATE, CHANNEL): the GDS needs a second CONFIRM step, and with `require_two_person_for_hazardous = true` an AUTH from a second operator. The stored-sequence table refuses every hazardous opcode, REBOOT, SEQLOAD and SEQCTRL (no nesting); the flight and ground forbidden lists are compared by a test. Radio changes (AIRRATE, CHANNEL) take effect after two more frames and revert automatically after `revert_s` without uplink; TX inhibit re-enables itself; command loss (default 24 h) returns to SAFE with the default radio; REBOOT needs the magic 0xB007 and executes within a bound even when no frame can be sent. |
| **Alternatives** | Flight-side hazard flags only; manual recovery of a lost link. |
| **Reason** | Every state change that could cut the link must heal by itself - the core rule of deep-space operations. Phase C finding C18: a commanded REBOOT never executed while no frame was transmitted; it now executes after two more frames or after max(10 s, two frame periods + one busy time), at once while TX is inhibited or the radio is unconfigured. |
| **Evidence** | `dssq/dictionary.py` `COMMANDS` (`hazardous`); `fsw/autonomy.cpp` `opcode_forbidden()`; `tests/test_sequence.py::test_flight_forbidden_list_matches_dictionary`; `flight.cpp` `kRebootMaxWaitMs` = 10 000 ms; `config.h` `kDefaultCmdLossS` = 86 400 s. |
| **Status** | Current. |

---

## MCU <-> Linux link and third-party libraries

Full detail: [22_Bundled_Libraries.md](22_Bundled_Libraries.md).

<a id="d-40"></a>
### D-40 RadioLib 7.8.1 bundled and patched (reversal of the in-house driver plan)

| | |
|---|---|
| **Decision** | The SX126x option of both sketches uses RadioLib 7.8.1 (tag commit `2325a38a`), fetched from GitHub by `tools/radiolib.py`, verified (commit + SHA-256 of every bundled file), patched with `tools/radiolib-7.8.1.patch` and bundled as `sketch/libraries/RadioLib` (`- dir:` in `sketch.yaml`). Two hunks: a VENTUNO Q platform entry and `BuildOptUser.h` (`RADIOLIB_STATIC_ONLY`, unused modules excluded). Library behaviours that can be handled from our side are handled in our drivers. The default E22 builds never compile RadioLib. |
| **Alternatives** | (recorded, in order) (1) Stock RadioLib from the Library Manager - fails to compile on the VENTUNO Q and allocates heap on every SPI transfer; (2) contract 23 rule 1: replace RadioLib by an in-house SX126x driver written from the datasheet (`sx126x_core.*`, host-tested against a chip model); (3) owner reversal, contract 25: "continue building SX126x driver on RadioLib ... also fix any issue that might be present in that library yourself"; (4) contract 30: "go ahead with the bundled patched copy" - the stock variant is not offered again. |
| **Reason** | The owner chose the maintained community driver over a new in-house one, on condition that its defects were fixed. Patch only what cannot be fixed from our code; keep the delta small and documented; keep the boards offline. |
| **Evidence** | Patch header (audit RL-01, RL-02, RL-03 with real-toolchain evidence); `check_firmware.sh` (80 of 80 RadioLib units on both boards, negative control proving the stock copy fails on the VENTUNO Q); real builds of the station sketch (E22, SX1268, SX1262) on core main and 1.0.0 with 0 warnings (commit `f0ff5e8`); conflict C-10. |
| **Status** | Current since `f0ff5e8`. Supersedes the in-house driver plan (contract 23 rule 1; the host-test hook of commit `049f8bf` was later removed, Phase C finding C58). |

<a id="d-41"></a>
### D-41 Arduino_RouterBridge 0.4.3 bundled and patched

| | |
|---|---|
| **Decision** | Both sketches build against the Arduino_RouterBridge release the platform ships (0.4.3, tag commit `d681b872`), fetched, verified and patched by `tools/routerbridge.py` with `tools/arduino_routerbridge-0.4.3.patch`, bundled as `sketch/libraries/Arduino_RouterBridge`. Two hunks in `src/bridge.h`: the bridge thread stack 500 -> 1536 octets (measured worst case 1084 octets + 42 %), and `update()` never executes `"__safe__"` methods, so `provide_safe` handlers really run on the loop thread. |
| **Alternatives** | (recorded) Keep the stock library with only the mailbox work-around (D-42) - the residual risk of a 500-octet stack with no hardware stack protection was left as an "owner decision pending" item after Phase C; ask upstream and wait. |
| **Reason** | Owner decision (contract 41, 2026-10-11): "bundle a patched copy of the Bridge library with a larger stack". The stack overflow cannot be fixed from the sketch, and silent memory corruption is the worst failure mode on a board without a stack guard. |
| **Evidence** | Patch header (GCC `-fstack-usage -fcallgraph-info=su` stack analysis; host test against the real `bridge.h`: stock 4 of 12 checks fail, patched 12 of 12 under ASan + UBSan); commit `5b06a04` (real builds of both sketches log "Using library Arduino_RouterBridge at version 0.4.3 in folder: <sketch>/libraries/Arduino_RouterBridge"). Re-checked in CI on every push: the compile-log check, `tools/host_test/routerbridge/run.sh` (hunk 2 with the stock-tag negative control) and `stackcalc.py --check` (hunk 1, the worst case recomputed from real builds of both sketches). |
| **Status** | Current since `5b06a04`. |

<a id="d-42"></a>
### D-42 RPC mailbox to the loop thread, kept as defence in depth

| | |
|---|---|
| **Decision** | In both sketches every RPC handler is a small shim: on the loop thread it executes directly; on any other thread it copies its arguments into one static slot, signals the loop and waits with a bound (flight `kRpcWaitMs` = 6000 ms; station 1500 ms, 2500 ms for `rcu_mode`). `loop()` runs the request on its own stack at its top and between long phases. A request the loop never started is withdrawn and never executed later. RPC names and signatures are unchanged. |
| **Alternatives** | Trust the library's README that `provide_safe` runs on the loop thread (the v2 assumption); rely only on the patched library (D-41). |
| **Reason** | Phase C critical finding C50: stock 0.4.3 runs `provide_safe` handlers on its own priority-5 thread with a 500-octet stack, preempting `loop()` anywhere - data races on the VC queues, SDLS state and radio, and stack overflow. The mailbox was the first fix (interface decision I4); after D-41 it stays so that a build against the stock library is still safe. |
| **Evidence** | `fsw/rpc_mailbox.h` header; station sketch "Bridge services: a mailbox to the loop thread"; host test `rpc_mailbox` with real threads (`tools/host_test/test_integration.cpp`); commit `fbf16cf`. |
| **Status** | Current. |

<a id="d-43"></a>
### D-43 Bridge rules: no `Bridge.call`, no `Monitor`, gated notifications

| | |
|---|---|
| **Decision** | The flight MCU never calls `Bridge.call()` (only `notify` and `provide_safe`). `Monitor` / `Serial` are not used in a flight build (`LOGF` compiles to nothing unless `VGQ_DEBUG_MONITOR = 1`, bench only). `Bridge.begin()` and `provide_safe()` (which do block until the router answers) are used in `setup()` only; a failed registration raises EVR 0x0005. A notification is sent only when the router UART (LPUART1, 115 200 baud, 1024-octet TX ring) has room for it plus 32 octets; at most one large notification per loop pass, `sc_evr` <= 2 per pass and <= 8 per second; what does not fit is counted (`evr_nd`, `cadu_nd`). The MCU status goes out as two notifications, `sc_status` (1 Hz, 22 keys, worst case 307 characters) and `sc_status_x` (every 5 s, 29 keys, worst case 444 characters), both under the 480-character limit; the worst cases are measured by host test `egse_status` on every run. |
| **Alternatives** | Use `Bridge.call()` for EGSE services; debug output on `Monitor`; one status notification. |
| **Reason** | Phase B review finding 1: in 0.4.3 `RpcCall::result()` spins `while (true)` until a response arrives and a `Monitor` write can issue a blocking call - if the router or Linux stopped, the flight loop would hang. Review finding 21: `ZephyrSerial::write()` yields while the ring is full, so an ungated notification could stall the loop. Review finding 3: one status object measured 503 characters typical and 596 worst case (conflict C-25). |
| **Evidence** | PB-0.4; `config.h` `kEvrNotifyMaxPerS`, `kEvrNotifyMaxPerPass`, `VGQ_DEBUG_MONITOR`; `fsw/tlm_packets.h` `format_sc_status*` (host-tested worst-case lengths). |
| **Status** | Current. Notification integrity and `loop_max_ms` <= 20 ms on a real board are **VERIFY on hardware**. |

---

## Science and payload

<a id="d-44"></a>
### D-44 Own AS7265X driver; LED current limits; INT pin not used

| | |
|---|---|
| **Decision** | `drivers/as7265x.*` implements the ams I2C virtual-register protocol itself, non-blocking, with timeouts on every poll, and uses every chip feature (gain, integration, bank modes, DATA_RDY, LED_DRV as an electronic shutter, LED_IND, per-device temperature, DEV_SEL health, versions, soft-reset recovery, read-only COEF dump). LED drive is limited per lamp (white codes 0-3, IR 0-2, UV 0 only = 12.5 mA) on both ends; `kPinSpecInt` = -1 (DATA_RDY polled). COEF_WRITE and the firmware-update registers are never used. |
| **Alternatives** | (recorded) The SparkFun AS7265x library 1.0.5 (v2). |
| **Reason** | A blocking third-party library does not fit the non-blocking flight loop or the no-download rule. A cross-check against the SparkFun library and the ams datasheet confirmed 20 findings (commit `8115f61`), among them a protocol desync that could reach COEF_WRITE (persistent calibration) or the firmware-update registers, and LED overdrive (SparkFun Example3: UV max 30 mA, IR max 65 mA). On the SparkFun board the first-generation ICs have `!INT` and `!RST-ADDR` swapped, so INT is left unconnected. |
| **Evidence** | ams AS7265x datasheet v1-04; SparkFun `Firmware/AS7265x/Readme.md` (pin swap), hookup guide; contract 8, 13, 19, 26; conflicts C-04, C-05; commits `bfd7215`, `8115f61`. |
| **Status** | Current. DATA_RDY timing and calibrated-value comparability are **VERIFY on hardware** (T-SC-08). |

<a id="d-45"></a>
### D-45 Exposures in PA-off windows; lamps never lit during magnetometer samples

| | |
|---|---|
| **Decision** | Sensor sampling and blocking instrument reads run only while the radio PA is off. Every non-burst AS7265X exposure starts only with the PA off; lamps are switched off right after their frame, and an RM3100 sample waits up to `kSpecLampDeferMaxMs` = 2100 ms for the lamps to go dark (then it is taken anyway with MAG flag b4). A BURST starts with the PA off and then runs through transmissions; its end EVR counts the frames sent meanwhile. |
| **Alternatives** | Sample at any time; stop a burst at every frame; hold the downlink for a 60 s burst. |
| **Reason** | Keeps up to 5 W of RF and the LED currents out of the magnetometer data, and keeps blocking I2C work out of the radio timing. The 2100 ms value is just above the driver's own lamp-watchdog limit for the longest exposure (1424 + 44 + 50 + 500 = 2018 ms, Phase B review finding 37). For bursts the overlap is reported, not hidden (conflict C-26). |
| **Evidence** | `config.h` `kSpecLampDeferMaxMs`; `as7265x.h` `set_exposure_gate()`; MAG flag `MAG_F_LAMP`; commit `9a83f1e`. |
| **Status** | Current; 2100 ms is an engineering value, **VERIFY on hardware** (T-SC-08). |

<a id="d-46"></a>
### D-46 CCSDS 121.0-B-3 lossless compression for SPECBURST

| | |
|---|---|
| **Decision** | SPECBURST (APID 0x024) has an encoding field: 0 = raw, 1 = CCSDS 121.0-B-3 Adaptive Entropy Coder with the unit-delay predictor (n = 16, J = 16, r = 128, basic options), over the burst's u16 counts in **channel-major** order. The flight sends whichever is shorter. Flight encoder `src/ccsds/rice.*` (static buffers, documented worst case); ground decoder `dssq/ccsds/rice.py` (+ reference encoder). |
| **Alternatives** | No compression; a general-purpose compressor (LZ-type, not available offline on the MCU and not a space standard). |
| **Reason** | Bandwidth is the scarce resource (at most about 62 octet/s of packets at 2.4 kbit/s, D-19). The standard is lossless and simple enough for the MCU, and official test data allow bit-exact verification. Channel-major order lets the predictor see each channel's time series. Incompressible data expands slightly (260 bits per 16-sample block), hence "whichever is shorter". |
| **Evidence** | Commit `1bbd28e`: the flight encoder reproduces all 56 official CCSDS 121.0 test files with n <= 16 bit-exact; the ground covers all 74 official streams; cross-checked against libaec 1.1.7; differential fuzzing 9000 cases, 0 mismatches under ASan/UBSan; CI runs the full official data (libaec `121B2TestData` at a pinned commit). Contract 24.3, 28. |
| **Status** | Current. Encoder stack and run time on the MCU are **VERIFY on hardware**. |

<a id="d-47"></a>
### D-47 Log-encoded spectra; heuristics and approximations labelled

| | |
|---|---|
| **Decision** | SPEC v3.1 sends each calibrated value as a u16 log code (1e-4 to 4.29e5 uW/cm^2, step 0.068 %) with its 1-sigma uncertainty; identical encoder/decoder on both ends. The onboard light-source classifier and change detection (spectral angle against an EWMA baseline, EVR 0x0701) are labelled heuristic; ground colorimetry (XYZ, xy, CCT, Duv from 12 bands and the Wyman 2013 CMF fit) is labelled "approximate (18-band)", WBI and PRI are "proxies", PPFD and lux are estimates. |
| **Alternatives** | Float32 values (twice the size); presenting derived values as measurements. |
| **Reason** | 18 values fit one packet with uncertainty; an 18-band hobby sensor is not a laboratory spectrometer, and "every heuristic is labelled heuristic, every approximation approximate" (contract 13). |
| **Evidence** | Contract 13.3, 13.7; `fsw/spectral.h`; `dssq/spectral.py`; shared vector file `spectral_vectors.txt`. |
| **Status** | Current. |

<a id="d-48"></a>
### D-48 Magnetometer calibration fitted on the ground, applied on board to attitude only

| | |
|---|---|
| **Decision** | `dssq/magcal.py` fits a 9-parameter ellipsoid to RM3100 vectors collected while the spacecraft is rotated (pure Python, Gaussian elimination with partial pivoting), scales it to the site field (`[science] field_ut`, no default on purpose) and refuses poor coverage or conditioning. The result is uplinked as MAGCAL (offsets 0.01 uT, matrix Q2.14, rejected unless positive definite). The flight applies it to the TRIAD attitude path only; MAG packets stay raw (flag b5 = calibration active). Collection uses single-sample MAG packets (start with `PKTRATE MAG 1`). |
| **Alternatives** | Fit on board; calibrate the science data on board. |
| **Reason** | The fit needs a whole rotation's data and some numerics - easy on the ground, wasteful on the MCU. Raw science data stay reprocessable. Phase C finding C48: interval-averaged vectors shrink while rotating and bias the soft-iron scale. |
| **Evidence** | Contract 24.6; `tests/test_magcal.py`; commits `a970823`, `fbf16cf`. |
| **Status** | Current. Thresholds with a real rotation are **VERIFY on hardware**. |

<a id="d-49"></a>
### D-49 Stored command sequences (Voyager CCS style), RAM only

| | |
|---|---|
| **Decision** | A static table of 32 entries (time absolute or relative to START, 12 command octets); SEQLOAD validates the embedded command with the live validator (D-37); at most one due entry per loop pass (earliest, lower slot on a tie); an entry more than 300 s late is skipped and reported; entering SAFE stops the sequence (STOPPED_BY_FDIR); sequences keep running in hibernation. Absolute times must be in the future and at most 7 days ahead. The table and MAGCAL are RAM only: empty after every boot (EVR 0x0004 says so). |
| **Alternatives** | Persistent storage of sequences; unlimited look-ahead. |
| **Reason** | Owner-selected advanced function (contract 24.2). RAM only keeps a reset from replaying stale plans. The 7-day limit (stricter than the contract) keeps modulo-2^32 SCLK ordering unambiguous (conflict C-19). |
| **Evidence** | `fsw/autonomy.*`; 209 new host checks including 200 random sequences across the u32 wrap against a 64-bit model (commit `8ebb2a1`); `tests/test_sequence.py`. |
| **Status** | Current. |

<a id="d-50"></a>
### D-50 Beacon urgency levels (Deep Space 1 style)

| | |
|---|---|
| **Decision** | HK `beacon_level`: 0 NOMINAL, 1 INTERESTING (science event or sequence complete), 2 IMPORTANT (any WARN-or-worse EVR or a lost sensor), 3 URGENT (an FDIR condition needing action: radio fault, brown-out, SSR drops, SAFE, sequence stopped by FDIR, sensor-bus failure, plus AVI_OVERTEMP, CMD_LOSS and FARM_LOCKOUT). Latched until the next authenticated ground contact; carried by the hibernation beacon; shown by the station lamps and buzzer. |
| **Alternatives** | Operators read every EVR; no latching. |
| **Reason** | Deep Space 1 Beacon Monitor Operations Experiment: a one-number summary that a cheap, infrequent contact can read. The three additions are a documented design choice of the autonomy work (conflict C-20, listed for owner review). A commanded file transfer does not latch URGENT (SSR export drops are counted separately, conflict C-06). |
| **Evidence** | Contract 24.4; `fsw/autonomy.*` `BeaconMonitor`; PB-6.4 (annunciator patterns). |
| **Status** | Current. |

<a id="d-51"></a>
### D-51 Standard ground products: CCSDS TDM and XTCE

| | |
|---|---|
| **Decision** | Per pass the GDS writes a CCSDS 503.0-B-2 Cor. 1 Tracking Data Message (KVN; keywords exactly as the Blue Book; PARTICIPANT_1 = station, PARTICIPANT_2 = VGQ-1, PATH 2,1; DOPPLER_INSTANTANEOUS only when the frequency offset is calibrated). The mission database is exported as CCSDS 660.0-B-2 / XTCE 1.2 generated from `dictionary.py`. |
| **Alternatives** | Own CSV formats only. |
| **Reason** | Standard products load into standard tools. The PARTICIPANT order follows the Blue Book one-way examples (the transmitter first in PATH, 503.0-B-2 3.3.2), not the tentative order of the contract (conflict C-17). |
| **Evidence** | `dssq/products.py` (with a validating reader that parses the Blue Book examples; XTCE validated against `SpaceSystemV1.2.xsd` when `xmlschema` is installed and `DSSQ_XTCE_XSD` names the schema - otherwise well-formedness and structural checks only); commit `a970823`. |
| **Status** | Current. Loading the XTCE into a real mission control system is **VERIFY**. |

<a id="d-52"></a>
### D-52 Sensor facts taken from the manufacturers' data, not from libraries

| | |
|---|---|
| **Decision** | RM3100: I2C register addressing as in PNI's own I2C example (MX pointer 0x24, not the SPI read form 0xA4), built-in self-test per manual 5.6.1. Nicla Sense ME firmware: barometer = 24-bit unsigned at 1/128 Pa, so the value is sent in exact pascals; BMM150 field scaled by the dynamic range the BHI260 reports and sent in 0.1 uT (flag when the range was read, documented fallback otherwise). |
| **Alternatives** | Follow the community libraries (QRM3100 uses 0xA4 / 0xB4; Arduino_BHY2 1.0.8 uses a 0.0078 factor). |
| **Reason** | The earlier ARU firmware sent pressure 100 times too large (the library value is already Pa, not hPa). Datasheet facts win over library conventions. |
| **Evidence** | PNI RM3100 manual sections 5.6.1 and 5.8.4; Bosch BHI260AP datasheet Table 88 and section 13.2.8, BMP390 / BMM150 / BME688 datasheets; contract 22; commit `2206fc4`; behavioural test of the ARU scaling (262 / -131 / 655 counts at 2500 uT -> 200 / -100 / 500 x 0.1 uT; 101 325 Pa exact). |
| **Status** | Current. Field magnitude against the site WMM total field is **VERIFY on hardware** (T-SC-06). |

---

## Ground data system

<a id="d-53"></a>
### D-53 Data logger: SQLite WAL, 1 Hz receiver series, retention, free-space guard

| | |
|---|---|
| **Decision** | `gds.sqlite` runs in WAL journal mode with `synchronous=NORMAL` (the PRAGMA answer is checked). A `rx_stats` row per second records RSSI, SNR, noise floor, margin, frequency error, 10-min FER, RS corrections, AFC and Doppler. CSV export (`python -m dssq.export` and the authenticated FILES tab) opens the database read-only, reads in bounded pages, writes UTF-8 with units in the headers and guards spreadsheet formulas; the server names export files (no path input). Retention: `archive_keep_days` (180) and `archive_max_gb` (16 GB), oldest first, never the current UTC day, a future-dated folder or a symbolic link. A free-space guard (`archive_min_free_mb` = 2048 MB) pauses only the raw products (`cadu.bin`, `rx_stats`) with a WARN event and YELLOW alarm and resumes above floor x 1.05. Writes are best-effort: a full disk stops recording, never the downlink, FOP-1 or commanding. |
| **Alternatives** | Rollback journal; unlimited growth; stop the station when the disk is full. |
| **Reason** | Owner request (contract 39). WAL lets an export read while the GDS writes, and a power loss can lose at most the last commits, never corrupt the file. The eMMC is 64 GB. Phase C finding C33: archive I/O errors used to abort the processing of every received CADU. |
| **Evidence** | `dssq/archive.py`, `dssq/datalog.py`, `dssq/export.py` headers; `tests/test_logger.py` (retention by age and size, guard pause/resume, read-only export never blocks the writer, CSV format, web export authentication); commit `7f0d36c`. |
| **Status** | Current since `7f0d36c`. An external NVMe/USB archive inside the App container is **VERIFY on hardware**. |

<a id="d-54"></a>
### D-54 Ground robustness: snapshot copy, error boundaries, best-effort archive

| | |
|---|---|
| **Decision** | `snapshot()` returns a JSON-safe deep copy built inside the station lock; science analytics are served separately (`science_snapshot()`); the front panel uses a cheap `panel_status()`; the XTCE export is cached. `Runner.step()` runs each stage inside a documented error boundary (log + count + continue), so an exception never stops the App (App Lab would `sys.exit(1)`); Router Bridge registrations that fail at start are retried every 10 s. Every value received over RF is untrusted text, even with SDLS. |
| **Alternatives** | Return live objects; let exceptions propagate. |
| **Reason** | Phase B review findings 7 (an escaping exception stops the App), 14 and 15 (snapshot cost and thread safety); Phase C finding C52 (an unreachable router at start disabled the CardKB, lamps and SX126x radio for the whole run). |
| **Evidence** | `dssq/gds.py` (`Runner`, `json_safe`, `REGISTER_RETRY_S`); `tests/test_web.py::test_error_boundary_and_bounded_resources`; PB-5.1, PB-6.1. |
| **Status** | Current. |

<a id="d-55"></a>
### D-55 Host (systemd) mode kept as a fallback

| | |
|---|---|
| **Decision** | Besides the App Lab App, the ground system can run on the VENTUNO Q host as a systemd service ([../dssq-groundstation/host/install_station.sh](../dssq-groundstation/host/install_station.sh), `dssq-gds.service`, `python -m dssq.gds --app-data <App>/data` in `<App>/python`, so host mode shares the App's `data/` folder: configuration, archive, SDLS key and web token). The GDS itself needs only the standard library; a virtual environment **outside** the App folder (`$DSSQ_VENV`, default `~/.venvs/dssq-gds`) holds `arduino-router-bridge==0.5.1` for the front panel, RCU and SX126x link (Ubuntu 24.04 does not allow pip into the system Python). App mode and host mode must not run at the same time (one Linux client per provided Bridge name). |
| **Alternatives** | App Lab only. |
| **Reason** | Whether the App container publishes the web port on the LAN and can open the USB-UART was not verifiable without the board (contract 18), so a documented always-on fallback exists. Phase C finding C53: the service had claimed to be standard-library only although its Bridge paths need `arduino-router-bridge`; the installer now provides it (one-time internet access in host mode only). |
| **Evidence** | Contract 18; PB-6.2; commit `fbf16cf`. |
| **Status** | Current. App-mode port exposure and serial access are **VERIFY on hardware**. |

---

## Quality and verification

<a id="d-56"></a>
### D-56 Coding standard: Power of Ten, FP32 only, no heap after start-up

| | |
|---|---|
| **Decision** | Flight C++ follows Holzmann's "Power of Ten" rules (NASA/JPL): no recursion, every loop bounded, no heap after initialisation (the SSR is allocated once at boot: the largest power of two up to 256 KiB that leaves a 32 KiB reserve), every return value checked, range checks on every external input (telecommands, radio, sensors, Bridge), smallest scope, no type punning (`memcpy` / `put_u16`), warning-free with `-Wall -Wextra -Wshadow`. FP32 only (`float`, `sqrtf`, Welford statistics instead of sums of squares). The main loop never blocks more than about 20 ms in one call (state machines, not waits; documented exceptions in `setup()` and a dead-module E22 configure); a pass that hangs anyway, inside a library, is ended by the hardware watchdog (D-60). Python: typed, no bare `except`, validated inputs, deterministic tests, ruff and mypy clean. |
| **Alternatives** | Ordinary Arduino style (blocking reads, `String`, `delay()`, dynamic allocation). |
| **Reason** | Owner's engineering standard (contract 17.3). The STM32U585's FPU is single precision only (double precision runs in software, about 17 times slower). Documented exception (not our code): each `Bridge.notify()` allocates transiently inside the library and frees it before returning (bounded by the 490-octet notification limit, inside the 32 KiB reserve). |
| **Evidence** | Contract 0 and 17.3; PB-0.4; commit `9a83f1e` (FP32 Welford against an FP64 reference, SSR on the heap at boot); [16_Developer_Guide.md](16_Developer_Guide.md) section 5. |
| **Status** | Current. Long-run heap behaviour is **VERIFY on hardware**. |

<a id="d-57"></a>
### D-57 Five verification layers, all automated in CI except hardware

| | |
|---|---|
| **Decision** | (1) Host tests: the flight C++ compiled on a PC, built twice (warnings as errors; AddressSanitizer + UBSan), with standards vectors and the official CCSDS 121.0 data; plus the real station sketch on host threads (ASan + UBSan, ThreadSanitizer) and the RouterBridge host test. (2) Ground pytest suite on Python 3.11 and 3.13, decoding what the flight encodes; ruff and mypy. (3) `check_firmware.sh`: every Arduino file against strict stand-ins in every radio / framing / SDLS option, all RadioLib units for both boards, a negative control and the bundled-copy checks. (4) Real builds with the App Lab compiler and platform (default and SX126x radio, both sketches), the bundled libraries proven in use, and the bridge-thread stack bound. (5) GitHub CI runs all of it on every push (jobs `lint`, `host-tests`, `ground-tests`, `arduino-build`), every action pinned to a full commit hash. Hardware tests are written procedures ([08_Verification_and_Test.md](08_Verification_and_Test.md)). |
| **Alternatives** | Hardware-only testing; a single build configuration. |
| **Reason** | No layer alone proves the system: host tests prove the logic, the stand-ins prove every option still type-checks, the real build proves the toolchain accepts it, and the hardware tests prove what software cannot (range, wiring, the RF environment). |
| **Evidence** | [../.github/workflows/ci.yml](../.github/workflows/ci.yml); every commit message since `0a73319` records the gate it passed; current counts per layer: [08_Verification_and_Test.md](08_Verification_and_Test.md); commands: [16_Developer_Guide.md](16_Developer_Guide.md) sections 6-7. |
| **Status** | Current. |

<a id="d-58"></a>
### D-58 Dictionary document generated from the code; drift tests

| | |
|---|---|
| **Decision** | [05_Telemetry_and_Command_Dictionary.md](05_Telemetry_and_Command_Dictionary.md) is generated by `tools/gen_dictionary_doc.py` from `dictionary.py`, `cmdparse.py` and the literal `evr(severity, 0x....` calls and cadence table of the flight sources; it is never edited by hand. The web UI reference is guarded by a drift test (every top-level snapshot key and every receiver scalar must appear in [13_Web_UI_Reference.md](13_Web_UI_Reference.md)). |
| **Alternatives** | Hand-written dictionary tables (the v2 docs drifted). |
| **Reason** | A document that is generated, or tested against the code, cannot silently become wrong. |
| **Evidence** | `tools/gen_dictionary_doc.py` header; `tests/test_docs.py`; contract 31. |
| **Status** | Current. |

<a id="d-59"></a>
### D-59 Adversarial reviews before integration and before release

| | |
|---|---|
| **Decision** | Two independent review rounds whose findings had to survive an attempt to refute them before any fix: **Phase B** reviewed the integration specification (38 findings, none rejected; 3 accepted with corrected evidence, 1 with a broader fix, 1 fix deliberately different from the proposal); **Phase C** reviewed the integrated code with 18 read-only reviewers, one lens each (protocol/CCSDS, memory safety, radio driver, SDLS/security, sensors, ground UI, docs vs code, ...), each finding checked by one or two independent skeptics: 64 findings, 62 confirmed (3 critical, 25 major, 34 minor), 2 split verdicts handled as test gaps. Fixes were applied serially, each with a regression test where feasible, and every gate re-run. |
| **Alternatives** | Self-review only; fix findings without verification. |
| **Reason** | Owner agreement on a larger reviewer panel (15-20, contract 34). Verification before fixing avoids "fixes" for non-problems; serial fixes with full gates avoid regressions. |
| **Evidence** | Phase B review log (summarised in the decisions above: findings 1-38); commit `fbf16cf` (Phase C, with verification numbers); Appendix A. |
| **Status** | Done for v3. |

---

## Final fix round (Phase D)

Six decisions taken in the last fix round before release, after the rest of this log was
written. Each belongs to the area named in the [Index](#index).

<a id="d-60"></a>
### D-60 Hardware watchdog: the STM32U585 IWDG, programmed by direct register writes

| | |
|---|---|
| **Decision** | The flight arms the independent watchdog (IWDG, clocked by the LSI oscillator) at the very end of `setup()` (`Flight::watchdog_start()`) and refreshes it once per completed `loop()` pass, on the loop thread only (`Flight::watchdog_feed()`, called from `sketch.ino`). Timeout `kIwdgTimeoutMs` = 40 000 ms at the nominal `kIwdgLsiHz` = 32 000 Hz: `iwdg_settings()` takes the smallest prescaler whose 12-bit reload reaches it (/512, reload 2499) and never rounds the timeout down. The registers are written in the RM0456 / ST HAL order: start (KR 0xCCCC), unlock (0x5555), prescaler, reload, wait at most `kIwdgUpdateWaitMs` = 200 ms for the status register, refresh (0xAAAA); the IWDG is frozen while a debugger halts the core. If the settings are not confirmed in time, EVR 0x060C (ERROR) says so and the watchdog runs anyway. A watchdog reset is reported as reset cause 3 WATCHDOG (HK `reset_cause`, EVR 0x0001). `VGQ_IWDG` = 0 removes it, for bench debugging only. A hang inside `setup()`, before the watchdog starts, is not covered. |
| **Alternatives** | (recorded) The Zephyr watchdog API (`wdt_*`) - not available to a sketch; (recorded) no watchdog, which was the state before this round and was listed as a known limitation; a software watchdog on a timer or a second thread (it runs on the same kernel, so it cannot catch a hung kernel or a blocked interrupt); refreshing from a timer or from any thread (that would keep a hung loop alive). |
| **Reason** | The flight code's own loops are bounded (D-56), but library waits are not under our control (D-43: `Bridge` calls, `Wire` transfers, the E22 driver), and an unattended spacecraft must recover by itself, like every other self-healing rule (D-39). 40 s is about ten times the worst documented loop pass (an E22 `configure()` on a dead module, about 4 s; each timed-out I2C transfer 0.5 s; [01_System_Design.md](01_System_Design.md) section 9.4) and well below the 131 s maximum, so tolerance of the LSI frequency does not matter. Refreshing on the loop thread only means a hung `loop()` resets the MCU even while the bridge thread still runs. The Arduino Zephyr core is built without `CONFIG_WATCHDOG` (no IWDG device, and no `wdt_*` function among the symbols it exports to sketches), so direct register access is the only way; the sketch already reads `RCC_CSR` the same way (D-17). |
| **Evidence** | `config.h` IWDG block (`VGQ_IWDG`, `kIwdgTimeoutMs`, `kIwdgLsiHz`, `kIwdgUpdateWaitMs`); `fsw/flight.cpp` `Flight::watchdog_start()`, `Flight::watchdog_feed()`; `fsw/autonomy.h` `iwdg_settings()`, `iwdg_timeout_ms()`; host test `iwdg_settings_and_reset_cause` (40 s -> prescaler /512, reload 2499; IWDG reset flag -> reset cause 3); STM32U5 reference manual RM0456 (IWDG chapter); [01_System_Design.md](01_System_Design.md) section 9.4. |
| **Status** | Current. A real watchdog reset on the board is **VERIFY on hardware** (test T-FD-09 in [08_Verification_and_Test.md](08_Verification_and_Test.md)). |

<a id="d-61"></a>
### D-61 The ground station identifies itself in clear text on the uplink

| | |
|---|---|
| **Decision** | The ground transmitter sends `DE <callsign> <name>` in printable ASCII (`[station] callsign`, default `N0CALL` with a WARN at start; the station name reduced to ASCII letters, digits, `-`, `_` and `.`, at most 16 characters) as a packet of its own through the same radio path as the CLTUs (E22 transparent packet or SX126x packet). It is sent right after the first uplink of a run, then at least every 10 minutes while uplink transmissions continue (in an uplink window up to max(60 s, 3 frame periods) early, at most 600 s, with the longest expected frame period or the reported RF `frame_period_ms` when longer, `_ident_margins()`: a window still carries it when two downlink frames are lost), once when the uplink has been idle for max(120 s, FOP T1), from the housekeeping while the downlink is lost (no window comes) once the last window's transmissions have ended (`_uplink_busy_until`, on a monotonic clock), from the housekeeping without a window when no window has carried the periodic one 600 s - 2 s - its own air time after the last one (`_id_overdue()`), and at shutdown, as the first step, if a transmission is still uncovered or an identification is still in the outbox. In a window its air time is booked first in the window's air-time budget (`UplinkWindowPlan`, the shortest quiet window of any mode less 0.2 s, counted from the frame's ERT + 0.1 s so that a window handled late gets only what is left) and it is sent after the CLTUs that still fit, so a window never overruns into the next downlink frame; only the overdue identification sent without a window can collide with one downlink frame ([20_Known_Limitations.md](20_Known_Limitations.md) L-28). WAKE transmissions count as uplink. The spacecraft is unchanged: the packet holds no CLTU start sequence (EB 90; every octet is below 0x80), so the flight's CLTU search counts it in RF `cltu_bad` and drops it with no EVR, FDIR or FARM-1 effect. The spacecraft identifies its own transmitter separately (OID frames and EVR 0x0002, D-18). Shown in snapshot `link.station_id` and in the dashboard's uplink table. |
| **Alternatives** | Rely on the spacecraft's identification only (it identifies the spacecraft's transmitter, not the ground's); a callsign field inside the TC frames (the CCSDS TC frame has none, and with AE the frame content is encrypted, while identification must be readable); identification in another emission mode such as Morse (the station's radio path sends only packets); a flight-side recogniser for the identification packet (unnecessary: the existing CLTU search already ignores it). |
| **Reason** | Amateur rules require a transmitting station to identify itself in plain language at short intervals (ITU Radio Regulations No. 25.9; for example US 47 CFR 97.119(a): at least every 10 minutes and at the end of a communication). Before this round only the spacecraft identified itself. Printable ASCII guarantees that the flight never finds a CLTU in the packet, so the TC path gains no new state and the flight needs no change. |
| **Evidence** | `dssq/gds.py` `StationIdent`, `ident_text()`, `ID_INTERVAL_S`, `ID_EARLY_S`, `ID_LOST_FRAMES`, `ID_OVERDUE_GUARD_S`, `ID_END_IDLE_S`, `ID_NAME_MAX`, `WINDOW_REACTION_S`; `dssq/config.py` `CALLSIGN_PLACEHOLDER`; `tests/test_station_id.py` (for example `test_e22_periodic_id_while_commanding_for_25_minutes`, `test_periodic_id_keeps_10_minutes_when_frames_are_lost`, `test_late_or_batched_frames_never_overrun_the_quiet_window`, `test_runner_close_sends_an_id_left_in_the_outbox_first`, `test_flight_drops_a_non_cltu_packet_without_evr_or_fdir`); host tests `station_id_packet_is_not_a_cltu` and `station_id_e22_bursts` (`tools/host_test/test_station_id.cpp`); [09_Regulatory_and_Safety.md](09_Regulatory_and_Safety.md) section 1.3; [04_Space_Data_Link_ICD.md](04_Space_Data_Link_ICD.md) section 5.8. |
| **Status** | Current. Reception of both identifications by an independent receiver is **VERIFY on hardware** (test T-TC-07 in [08_Verification_and_Test.md](08_Verification_and_Test.md)); check your national identification rules. |

<a id="d-62"></a>
### D-62 The UDP telecommand listener accepts loopback addresses only

| | |
|---|---|
| **Decision** | `[interop] tc_frames_udp_listen` (TC frames from an external mission control system) accepts only `"127.x.y.z:port"` or `"[::1]:port"`. Host names (also `localhost`), `0.0.0.0`, `[::]` and every LAN address are refused with an error naming the key; there is no opt-in for a LAN bind. The address is checked when the configuration is validated (`config.py` `parse_udp_listen()`), again before the GDS starts anything, and once more before the socket is bound (`gds.py` `_start_udp_listener`). The outgoing TM links (`tm_frames_udp`, `tm_packets_udp`) are not affected. |
| **Alternatives** | (recorded) Any address, as before this round (listed as a residual risk); an opt-in key for a LAN bind; an authentication layer on the UDP interface (its purpose is to accept plain frames from a standard tool). |
| **Reason** | The UDP interface has no authentication, and its frames bypass the hazardous-command CONFIRM: the GDS wraps them with the station's SDLS key and radiates them. Anything that can send it a datagram can therefore command the spacecraft with the station's authority. On loopback only processes on the station host can, and those can read the key anyway. Host names are refused because `localhost` is resolved through the name service and could point elsewhere. |
| **Evidence** | `dssq/config.py` `parse_udp_listen()` and `validate()`; `dssq/gds.py` `Runner` (checked before anything starts) and `_start_udp_listener`; `tests/test_radio.py::test_udp_tc_listener_is_loopback_only`; [17_Security_Model.md](17_Security_Model.md) threat T16. |
| **Status** | Current. The refusal on the station board is checked by test T-GS-08 in [08_Verification_and_Test.md](08_Verification_and_Test.md). |

<a id="d-63"></a>
### D-63 Transmitter duty measured over whole transmission cycles (`TxDutyMeter`)

| | |
|---|---|
| **Decision** | The transmitter duty reported in RF `tx_duty` and in `sc_status` `duty_pct` is the busy time averaged over whole transmission cycles (from the launch of one transmission to the launch of the next): the last `TxDutyMeter::kCycles` = 16 cycles launched within `kWindowMs` = 300 s, the newest always counted. The cycle still open counts with max(time elapsed, the period in force), so the value never jumps up when a transmission ends and decays towards 0 while the transmitter is silent (TXINHIBIT, hibernation). It replaces the fixed 60 s window of one-second buckets. The 50 % limit itself is enforced by the frame spacing (D-24), not by this meter. The simulator mirrors it; the ground alarm limits are YELLOW at 55 % and RED at 70 % (`dictionary.py`). |
| **Alternatives** | (recorded) The fixed 60 s window (sum of the busy time of the transmissions that ended in the last 60 one-second slots); a longer fixed window (still phase-dependent unless it is a multiple of the frame period); an exponential average (no clean bound). |
| **Reason** | A fixed window that is not a multiple of the frame period swings with the phase of the sample. With the SX126x SF12 busy times (17.3 s at profile 1, 34.6 s at profile 0) a 60 s window holds one or two transmissions and read 28.9 % or 57.7 % for a true 50 %: a false ground YELLOW. Each closed cycle lasts at least the frame period, which is at least busy / 0.5, so the whole-cycle average can exceed 50 % only if the flight's own frame spacing did. 300 s holds at least two cycles of SF12 profile 0 in SAFE. |
| **Evidence** | `fsw/datalink.h` `TxDutyMeter`; host tests `tx_duty_never_above_the_pa_limit` (every model, rate code and mode: never above 500 permille), `tx_duty_sf12_has_no_window_artefact` (the old algorithm kept as a negative control) and `tx_duty_open_cycle_and_silence` (`tools/host_test/test_tx_duty.cpp`); `tests/test_end_to_end.py::test_sx126x_sf12_duty_reads_50_percent_without_a_false_yellow`. |
| **Status** | Current. |

<a id="d-64"></a>
### D-64 RSTCNT also clears the four event-latched FDIR flags

| | |
|---|---|
| **Decision** | RSTCNT (opcode 0x0B, not hazardous) resets the counters and also clears the four FDIR flags that only record that an event happened and that no monitor clears: CMD_LOSS (b4), LOOP_OVERRUN (b7), RATE_REVERT (b12) and SDLS_AUTH (b13) (`kFdirClearedByRstcnt`, `fdir_after_rstcnt()`). The other twelve flags follow a state and are cleared only by their own monitors (for example I2C_BUS after an error-free period following a recovery, AUX_TIMEOUT at the next completed transmission). A new event sets a cleared flag again, and the beacon monitor sees a new rising edge. The ground dictionary (`FDIR_CLEARED_BY_RSTCNT`) and the simulator do the same. |
| **Alternatives** | (recorded) Keep these flags until the next reboot, as before this round; clear them automatically at the next ground contact; a new FDIR-clear command (a new opcode and dictionary entry). |
| **Reason** | An event flag that nothing clears keeps the ground's YELLOW "FDIR" alarm on for good, so a new event goes unnoticed; the only remedy was a REBOOT, which is hazardous and empties the RAM-only tables (stored sequence, MAGCAL). RSTCNT is already the operator's "acknowledge" command and adds no opcode. A flag that follows a state must not be cleared by command, because the condition may still be present. |
| **Evidence** | `fsw/tlm_packets.h` `kFdirClearedByRstcnt`; host test `rstcnt_clears_event_latched_fdir_flags` (the 12 state-driven and 4 event flags partition the 16 bits); `tests/test_end_to_end.py::test_rstcnt_clears_the_event_latched_fdir_flags` (the flight constant read from the source equals the ground's; the simulator clears the same flags); [14_Troubleshooting_and_Event_Response.md](14_Troubleshooting_and_Event_Response.md) section 6. |
| **Status** | Current. |

<a id="d-65"></a>
### D-65 An unknown TC counter is held, then re-synchronised onto a time floor (4 numbers per second since 2026-01-01)

| | |
|---|---|
| **Decision** | When the station's TC sequence number is unknown - `sdls_sn.txt` corrupt, or missing for a key used with TC AUTH or AE (the GDS with or without an archive: `--no-archive` keeps the file in `<archive_dir>`, and the hardline tool `dssq.cltu`, both `SdlsSender(require_file=True)`) - the uplink is held: the GDS refuses commands and warns at start; `dssq.cltu` refuses with exit status 1 and creates no file. The hold ends when an HK reports the spacecraft's `sdls_last_sn`: from an AE-protected frame, or any HK at a station whose TM service is NONE (`_sn_release_unprotected`), and never an HK whose uptime is below `SDLS_SN_RESTORE_WAIT_S` = 120 s that reports 0 (the flight's counter is RAM 0 until the EGSE restores it; a WARN at most once a minute while waiting); an unprotected report above the floor never releases (ERROR), so a forged HK at a TM NONE station can only delay a release. The counter is then set to max(file, T), written to the file before the release, with a WARN naming the rule used: T = the **time floor** `sn_time_floor(now)` = `SDLS_SN_TIME_RATE` (4) x seconds of station wall clock since `SDLS_SN_TIME_T0` = 1 767 225 600 (2026-01-01T00:00:00Z), about 98 million in October 2026, when `sdls_last_sn` <= the floor; T = `sdls_last_sn` + `SDLS_SN_RESYNC_GAP` (1 000 000) only when the report is above the floor (WARNING "is the station clock behind?") or the clock is before 2026 (WARNING: no time floor). A target above `SDLS_SN_RELEASE_MAX` = 2^32 - 2^26 = 4 227 858 432 is refused (ERROR, once per reported value) and the uplink stays held: a new key is needed; the floor itself reaches it on 2059-06-30. The station's use of numbers is rate-limited in code so that the floor stays ahead of it: one uplink window per `WINDOW_MIN_SPACING_S` = 1.4 s of frame ERT with at most 4 TC frames, one `WAKE` per `WAKE_MIN_INTERVAL_S` = 10 s, and `dssq.cltu` refuses a number above the floor - at most 4 / 1.4 + 1 / 10 = 2.96 numbers per second. The software simulator (`--sim`, `DSSQ_SIM=1`, with or without an archive) keeps its counter in memory and never touches the real files. |
| **Alternatives** | (recorded) Treat a missing file as first use: start at 0 and let the next protected HK raise the counter to `sdls_last_sn` (the behaviour before this round); (recorded) hold only for TC AE with TM AE (the rule before this round: AUTH and TM NONE stations were not held); (recorded) `sdls_last_sn` + 1 000 000 alone (the rule earlier in this round: a stale report brings the counter back onto numbers already used); the floor plus the 1 000 000 gap; continue at `sdls_last_sn` + 1; refuse to start the GDS; make the operator always write the file by hand. |
| **Reason** | The spacecraft reports only the last number it **accepted**. Numbers above it may already have been radiated and lost (on the uplink, or COP-1 retransmissions, each of which takes a fresh number), so continuing at `sdls_last_sn` + 1 can radiate two different frames under one GCM nonce (AE) or make a recorded, never-accepted frame valid again while the station's own new frames are refused (AUTH, which has no IV): both services are held. Nothing on disk tells a new key from a used one. No single HK can be proven fresh either: a recording replayed after an archive loss (which also loses the TM replay state), the boot HK sent before the EGSE restores the flight's counter, or a forged HK at a TM NONE station can report a number from before numbers the key has already used, and `sdls_last_sn` + 1 000 000 then lands on them again (the review's reproduction: a new key's first numbers 1 000 001 to 1 000 005, the archive lost, the recorded first-pass HK replayed: the next number was 1 000 001 again). The time floor does not depend on the report: because the station uses fewer than 3 numbers per second, every number used with a key stays below 4 x (seconds since 2026-01-01) as long as the station clock is right, and a release lands on the floor, above the file, the report and any lost numbers. No gap is added on top: a key released once numbers its frames from the floor, so a genuine report is close to the floor, and floor + gap would push the counter up to 1 000 000 numbers ahead of the floor, where a second loss with a stale report could reuse them. At a TM NONE station no HK can be authenticated, so an unprotected one must be allowed to end the hold; with the floor its value no longer matters, a higher one only uses up number space, and `SDLS_SN_RELEASE_MAX` keeps at least 2^26 - 1 numbers whatever it reports. Residuals ([17_Security_Model.md](17_Security_Model.md) R15): a clock behind at a release, before 2026 only the gap, a burst of up to 5 numbers in the first 5 s after a release. Holding only the uplink keeps telemetry, alarms and the web UI working (as D-32). |
| **Evidence** | `dssq/ccsds/sdls.py` module docstring (the rate argument), `sn_time_floor()`, `SDLS_SN_TIME_T0`, `SDLS_SN_TIME_RATE`, `SDLS_SN_RESYNC_GAP`, `SDLS_SN_RELEASE_MAX`, `SDLS_SN_RESTORE_WAIT_S`, `SdlsSender` (`require_file`, `hold()`, `observe_spacecraft_sn()`); `dssq/gds.py` `WINDOW_MIN_SPACING_S`, `WAKE_MIN_INTERVAL_S`, `GroundStation.__init__` (`keep_security_state`, `_sn_release_unprotected`), `_uplink_window()`, `run_radio_jobs()`; `dssq/cltu.py` `build_hardline_cltu()`; `tests/test_end_to_end.py::test_existing_key_without_sn_file_holds_and_resyncs_above_the_lost_tail`, `test_every_numbered_tc_service_holds_without_its_sn_file`, `test_a_replayed_first_pass_hk_after_archive_loss_cannot_reuse`, `test_a_forged_hk_under_tm_none_cannot_reuse`, `test_the_boot_hk_before_the_restore_never_ends_the_hold`, `test_the_simulator_never_touches_the_real_counter`, `test_no_archive_keeps_and_uses_the_sn_file`; `tests/test_sdls.py::test_hardline_uses_the_app_copy_and_refuses_without_an_sn_file`, `test_held_sender_never_writes_below_the_file`, `test_a_replayed_or_forged_hk_cannot_cause_reuse`, `test_resync_fallbacks_and_limits`, `test_hardline_never_outruns_the_time_floor`; `tests/test_station_id.py::test_sdls_numbers_stay_below_3_per_second_whatever_the_downlink_does`, `test_wake_at_most_once_per_interval`; [17_Security_Model.md](17_Security_Model.md) section 7.6 and R15. |
| **Status** | Current. |

<a id="d-66"></a>
### D-66 64-octet Serial rings (arduino:zephyr before 1.0.0): a UART pump thread for the E22 port, long notifications into an empty ring

| | |
|---|---|
| **Decision** | The flight sketch detects the core's Serial ring size at compile time (`CONFIG_ARDUINO_API_SERIAL_BUFFER_SIZE` from the core's `autoconf.h`; `config.h VGQ_SMALL_SERIAL_RINGS` when below 1024). With small rings: (1) the E22 port goes through a UART pump (`drivers/uart_pump.h`), a thread at priority 6 (the loop runs at 14, the bridge thread at 5) that every 1 ms moves the received octets into a 1024-octet FIFO and tops the 64-octet TX ring up from a 512-octet FIFO; the E22 driver reads and writes those FIFOs (single producer / single consumer, `drivers/byte_fifo.h`), so `transmit()` never blocks and no received octet is lost to a long loop pass; (2) `Flight::notify_room()` lets a notification longer than the ring go out only into an empty ring, where `Bridge.notify()` then waits at most ~45 ms (530 octets at 115200 bit/s, one large notification per pass) while the bridge thread keeps reading the router link and the pump the radio port. With 1024-octet rings nothing changes. |
| **Alternatives** | (recorded) Require arduino:zephyr 1.0.0 (not possible: the stable App Lab pins the board's core below 1.0.0, `ARDUINO_APP_CLI__PLATFORM_VERSION_CONSTRAINT` default `<1.0.0` until arduino-app-cli 0.14.0); enlarge the rings from the sketch (not possible: the size is a core Kconfig value compiled into the core's Serial objects); split the frame over several loop passes (a pause in the UART stream splits the E22's RF packet); raise the bridge thread above the loop (not needed: at priority 5 it already preempts the loop and reads the router link every ~1 ms). |
| **Reason** | Found on the owner's UNO Q (2026-10-12): with 64-octet rings every notification over 16 octets failed `notify_room()` for ever (the EGSE waited for `sc_status`), and `ZephyrSerial::write()` would block ~180 ms for a 240-octet E22 frame, while a 64-octet RX ring holds only 67 ms of radio data at 9600 bit/s. |
| **Evidence** | Core commit `40abc1f7` ("Serial: increase default ringbuffer size to 1024", 2026-08-18; `Kconfig` default 64 at tag 0.90.0, 1024 at 1.0.0); `zephyrSerial.cpp` `write()` (put / `yield()` loop) and `availableForWrite()`; `CONFIG_MAIN_THREAD_PRIORITY` 14 in the 0.90.0 loader configuration; host test `byte_fifo` (two threads, 4 MiB through a 64-octet FIFO, also under ThreadSanitizer); `check_firmware.sh` small-ring checks; real builds on 0.90.0 and 1.0.0 with the loader symbol check; pump thread stack 428 of 1024 octets (`stackcalc.py` root `UartPump::entry`: 256 call chain + 8 + 108 FP frame + 56 TLS). First board run on 0.90.0, 2026-10-12 ([21_Test_Records.md](21_Test_Records.md) record 2026-10-12-A): `sc_status` every 10 s, radio check passed (FDIR 0x0000, no LOOP_OVERRUN), 19 of 19 frames archived by the EGSE as `tm_cadu` copies; the receive half is not exercised until a ground station transmits (V-30). |
| **Status** | Current. |

<a id="d-67"></a>
### D-67 I2C bus clear releases the lines with the MCU's weak pull-ups: an empty A4/A5 bus is not a stuck bus

| | |
|---|---|
| **Decision** | `I2cBus::bus_clear()` releases SDA and SCL with `pinMode(pin, INPUT_PULLUP)` instead of `INPUT`, so the MCU's own weak pull-up (about 40 kOhm) is on whenever it reads a line. A bus with external pull-ups behaves as before (the weak pull-up is in parallel); a bus with nothing on it reads high, so the bus clear passes and nothing is retried; a target that holds a line low still reads low (EVR 0x060A as before). Wire `begin()` re-applies the I2C pinctrl state afterwards, so I2C traffic itself is unchanged. |
| **Alternatives** | (recorded) Leave it and tell the operator to fit 4.7 kOhm pull-ups on A4/A5 even with no instrument there (an error event for a correct, empty bus); skip the bus clear while no A4/A5 instrument has ever answered (more state, and the boot clear would still report); report "no pull-ups" as its own event (the weak pull-ups make the case harmless, so there is nothing to act on until an instrument is added, and then a missing pull-up shows as that instrument absent, docs/14). |
| **Reason** | Found on the owner's UNO Q (2026-10-12, record 2026-10-12-A): with the AS7265X on the Qwiic connector and nothing on A4/A5, no pull-up held those lines, they read low, and every re-probe of the absent Nicla Sense ME / RM3100 (30 s doubling to 10 min) logged EVR 0x060A `I2C bus clear: SDA or SCL still low after 9 clocks`. |
| **Evidence** | ArduinoCore-zephyr `pinMode()` implements `INPUT_PULLUP` (wiring_digital.cpp, see `drivers/i2c_bus.h`); STM32U585 datasheet weak pull-up RPU about 40 kOhm; the EGSE log of record 2026-10-12-A (EVR 0x060A at 23:13:05Z and 23:14:06Z). The fix itself is not yet confirmed on the board: the next run with nothing on A4/A5 should show no EVR 0x060A. |
| **Status** | Current. |

---

## Superseded decisions

| Earlier decision | Replaced by | When / evidence |
|---|---|---|
| E22-400T37S (5 W) as the default radio (v2) | E22-400T33D default, T37S by strap (D-08, D-09) | contract 1, 33; commits `93cf4a3`, `dd1f4c0` |
| Sensors on one Qwiic bus (`Wire1`) with the Nicla Sense Env (v2) | `Wire2` on A4/A5, no Qwiic, no Nicla Sense Env (D-13, D-14) | contract 1, 16.1; commit `eaefd3b` |
| SDLS authentication only (v2) | AE default, AUTH for amateur use (D-28) | contract 5; commit `9c7d893` |
| Two CardKBs, then an AUTH key switch, then a CardKB on Qwiic | One CardKB on header `Wire` (D-15) | contract 16.1 -> 16.3 -> 17.1 |
| In-house SX126x driver (contract 23 rule 1) | Bundled patched RadioLib (D-40) | contract 25, 30; commit `f0ff5e8` |
| Stock Arduino_RouterBridge; "`provide_safe` runs on the loop thread" (v2 docs) | Mailbox (D-42), then a bundled patched copy (D-41) | Phase C C50; contract 41; commits `fbf16cf`, `5b06a04` |
| Epoch retried with the same value after a lost answer (PB-6.3 rule 3) | Offered at most once (D-31) | Phase C C12/C13; commit `fbf16cf` |
| CMDVER `tc_seq` sentinels 0xFE / 0xFF | Source in stage bits 7..6 (D-38) | Phase C C2/C55; commit `fbf16cf` |
| Every RCC brown-out flag reported as BROWNOUT | Backup-register discriminator (D-17) | Phase C C16/C56; commit `fbf16cf` |
| Fixed per-mode cadence table | Capacity-paced periods (D-24) | Phase B review finding 6, C-27 |
| `cryptography`, `pyserial`, `tomli` at run time | Standard library only (D-06, D-35) | contract 23; commit `c8c3b04` |
| SparkFun AS7265x library | Own driver (D-44) | contract 8; commits `bfd7215`, `8115f61` |
| Station OLED, optional spacecraft OLED | Web dashboard only (D-16) | commit `ae6087d` |
| `src/core/` for pure algorithms | `src/fsw/` (D-07) | contract 17.2, C-12 |
| `tools/sensitivity_test.py` | `tools/link_budget.py sensitivity` (D-25) | contract 17.2, C-13 |
| Layout `spacecraft/`, `ground/` | Two App folders (D-05) | contract 18; Phase D |
| No hardware watchdog (the Zephyr watchdog API is not built into the core) | IWDG by direct register writes (D-60) | Phase D final fix round |
| Only the spacecraft identified itself on air | The ground station identifies itself too (D-61) | Phase D final fix round |
| UDP telecommand listener on any address | Loopback only (D-62) | Phase D final fix round |
| TX duty from a fixed 60 s window of one-second buckets | Whole-cycle `TxDutyMeter` (D-63) | Phase D final fix round; the old algorithm is the negative control in `test_tx_duty.cpp` |
| Event-latched FDIR flags kept until a reboot | Cleared by RSTCNT (D-64) | Phase D final fix round |
| A missing TC sequence-number file meant first use (start at 0, raised to `sdls_last_sn` by the next protected HK; held only with TC AE + TM AE) | Hold for TC AUTH and AE, then the time floor (D-65) | Phase D final fix round |
| A held TC counter re-synchronised to `sdls_last_sn` + 1 000 000 | The time floor, 4 numbers per second since 2026-01-01; the gap only as the fallback before 2026 or for a report above the floor (D-65) | Phase D final fix round (a replayed first-pass HK after an archive loss reused numbers: `test_a_replayed_first_pass_hk_after_archive_loss_cannot_reuse`) |

---

## Appendix A - Phase C review outcomes

All 62 confirmed findings (IDs C1-C62 as cited in code comments and tests), with the outcome.
Fixed in commit `fbf16cf` unless noted. "->" names the decision that records the lasting rule.

| ID | Sev. | Area | Finding (short) | Outcome |
|---|---|---|---|---|
| C1 | major | TM framing | Stale partial packet spliced onto new data after a VC reset (SSRCLEAR) | FHP consistency check, partial dropped -> D-34 |
| C2 | major | TC / COP-1 | CMDVER `tc_seq` sentinels collide with N(S) 254/255 | Source in stage bits 7..6 -> D-38 |
| C3 | major | TC / COP-1 | FOP suspension at the transmission limit leaves a sequence hole | Sent queue purged, V(S) re-initialised from N(R) |
| C4 | major | TC / COP-1 | Lockout recovery keeps stale frames that re-lock the FARM | Sent queue purged on UNLOCK / Set V(R) |
| C5 | major | TC / COP-1 | FOP accepts any N(R) | N(R) validated; an invalid one suspends (FARM reset suspected) |
| C6 | minor | TC / COP-1 | BC/BD confirmed by any FARM-B counter change | Expected counter per bypass frame |
| C7 | minor | TC / COP-1 | TC frames with Bypass 0 / Control 1 accepted as Type-AD | Rejected on both ends |
| C8 | minor | USLP | Ground TC parser skips the SCID check (UDP interop) | Foreign SCID rejected |
| C9 | critical | SDLS | Forged null-SA frame splices into an AE packet | SDLS status change = discontinuity -> D-34 |
| C10 | major | SDLS | Protected HK writes a lower SN over the shared file | Re-read under lock, never lowered -> D-32 |
| C11 | minor | SDLS | No inter-process lock on the SN file | `flock` on `sdls_sn.txt.lock` -> D-32 |
| C12 | major | SDLS | Pending epoch retried after an unseen MCU reboot | Value retired -> D-31 |
| C13 | critical | EGSE | An epoch can reach two MCU boots (GCM nonce reuse) | Offered at most once; interleaving tests -> D-31 |
| C14 | major | EGSE | CFDPCANCEL racing a relay PDU abandons the relay | One EOF(cancel) offered |
| C15 | minor | EGSE | EGSE restart during a relay blocks CFDPPUT for 10 min | Orphaned relay closed with EOF(cancel) after 40 s |
| C16 | major | Flight | Every cold power-up reported as BROWNOUT | Backup-register marker -> D-17 |
| C17 | major | Flight | TC anti-replay at 0 until the EGSE restores it | Restore hold, 120 s bound -> D-33 |
| C18 | major | Flight | REBOOT never executes without a transmitted frame | Bounded deadline -> D-39 |
| C19 | minor | Flight | A hardline TC confirms an RF rate/channel change | Only RF frames confirm |
| C20 | minor | Flight | Hibernation beacon HK not necessarily in the beacon frame | Beacon = at most 2 frames (`kBeaconMaxFrames`): the backlog that still fits, then the fresh HK (the rest is in the SSR) |
| C21 | minor | Flight | T37S over-temperature during a TX inhibit gets no cool-down / SAFE | Inhibit extended, SAFE entered |
| C22 | minor | Flight | Bus temperature kept after the Nicla is lost | Reading n/a, flag cleared |
| C23 | minor | Flight memory safety | CLCW No-Bit-Lock / command-loss timer wrong after 49.7 days | Latched flag, saturating counter |
| C24 | major | E22 radio | Busy time taken from the AUX edge (may be too short) | Never below modelled air time -> D-24 |
| C25 | minor | SX126x | Station re-sends a radiated frame after a fault | Faulted transmission finished as failed |
| C26 | minor | SX126x | `[radio.sx126x]` chip / TCXO never checked against the MCU | Status fields 21/22, mismatch alarm (I3) |
| C27 | minor | SX126x | False MCU reboot detected at the 49.7-day `millis()` wrap | Wrap recognised |
| C28 | major | Sensors | Nicla and RM3100 probed only at boot | Re-probed (rate-limited) |
| C29 | minor | Sensors | MAGCC between POLL and collect rescales a sample | Deferred while a conversion is pending |
| C30 | minor | Sensors | Comment claims a repeated start (core sends STOP) | Corrected |
| C31 | minor | Sensors | Bus-clear comment wrong about `Wire.end()` | Corrected |
| C32 | major | Ground core | Link manager uses lifetime FER incl. LOS gaps | 10-min FER excluding the LOS gap |
| C33 | major | Ground core | Archive I/O errors abort CADU processing | Best-effort archive -> D-53 |
| C34 | major | Ground core | SX126x link manager never returns to profiles 0/1 | Model-dependent `min_rate` default |
| C35 | minor | Ground core | Replay rejects CADUs with ASM bit errors | Same tolerance as live |
| C36 | minor | Ground core | AFC reconfigure overwrites the calibrated offset | Calibration kept |
| C37 | minor | Ground core | Simulator omits the ground UART delay | Added |
| C38 | major | Web security | 32 slow clients lock out the web server | Total deadline, per-address limit -> D-36 |
| C39 | minor | Web security | LAN-bound server accepts any Host (DNS rebinding) | Host allow-list -> D-36 |
| C40 | minor | Dashboard | Alarm-reason colours guessed (RED/YELLOW swapped) | `alarm_red_count` |
| C41 | minor | Dashboard | "pass" window drops the AOS frame, counts the LOS gap | Fixed |
| C42 | minor | Dashboard | Margin/SNR keep last-packet values after LOS | Marked as last value / n/a |
| C43 | minor | Dashboard | Ground RF survey uncalibrated against a calibrated floor | Calibration applied |
| C44 | minor | Dashboard | Time-correlation drift shown with no fit | n/a until enough samples |
| C45 | minor | Dashboard | "RCU active" shown for the SX126x station | Fixed |
| C46 | minor | Dashboard | Colorimetry text shown with no spectrum | Fixed |
| C47 | major | Science | Fusion and TRIAD quaternions compared in different frames | Both converted to body -> NED |
| C48 | minor | Science | MAGCAL fit uses interval-averaged vectors | Single-sample packets only -> D-48 |
| C49 | minor | Science | "Calibrated" shown without a calibration | Label from HK `magcal_state` |
| C50 | critical | Station / App Lab | `provide_safe` handlers run on the bridge thread | Mailbox -> D-42; later D-41 |
| C51 | major | Station / App Lab | CardKB KEYTEST mode cannot be left | Buffering kept; ESC leaves |
| C52 | major | Station / App Lab | Router unreachable at start disables panel and SX126x for good | Registration retried every 10 s -> D-54 |
| C53 | major | Station / App Lab | Host service claims stdlib-only but needs `arduino-router-bridge` | Installer provides it -> D-55 |
| C54 | minor | Station / App Lab | `rf_rx` notification can exceed 480 characters | Payloads limited to 236 octets |
| C55 | major | Cross-consistency | = C2 on the ground side | -> D-38 |
| C56 | major | Cross-consistency | = C16 seen from the ground (YELLOW on every boot) | -> D-17 |
| C57 | major | Tests / CI | FARM-1 window edges, Set V(R) in Lockout, FARM-B counter untested | Host-test state table added |
| C58 | major | Tests / CI | SX126x driver has no runtime test; Makefile names a missing file | Makefile fixed; duty-cycle periods host-tested in `radio.cpp` |
| C59 | minor | Tests / CI | Missing vector files skip instead of failing | `pytest.fail` |
| C60 | minor | Tests / CI | Flaky web test at server capacity | Tolerant polling |
| C61 | minor | Tests / CI | Dead prelude and a false comment in `check_firmware.sh` | Removed |
| C62 | minor | Tests / CI | Host-test binary not rebuilt after a `config.h` edit | Header dependencies in the Makefile |

Phase C verification (commit `fbf16cf`, numbers in its message): host tests with -Werror and
under ASan/UBSan (with the official CCSDS 121.0 data), `check_firmware.sh` without a FAIL line, the
ground suite on Python 3.13 and 3.11, ruff and actionlint clean, and real arduino:zephyr builds of
both sketches (E22 and SX1268) with no warning in our code. Current results:
[08_Verification_and_Test.md](08_Verification_and_Test.md); build sizes:
[01_System_Design.md](01_System_Design.md) section 9.1.

Hand-offs that became documentation (contract 40): the FARM-B 2-bit counter ambiguity, the BD
hardline and RF BD frames sharing one source code, AD commanding waiting for directive
confirmation, the EGSE single-process assumption and best-effort directory `fsync` are listed in
[20_Known_Limitations.md](20_Known_Limitations.md).

## Appendix B - Citation tags in code

Code comments, tests and scripts cite the two design records of the project by short tags.
The records themselves are not in the repository (see the introduction); this table says what
each tag is about and where that subject is documented now. Tag families:

* `contract N` / `contract N.M` - section N.M of the v3 interface contract (also written
  "contract v3 section N" or "contract §N").
* `PB-x` / `PB-x.y` - section x.y of the Phase B integration specification.
* `C-nn` - entry nn of the Phase B conflict register (where two sources disagreed, and the
  binding resolution). Not to be confused with the Phase C finding IDs C1-C62 (no hyphen) of
  [Appendix A](#appendix-a---phase-c-review-outcomes).
* `F-nn` - item nn of the Phase B follow-up list (PB-7).
* `I1`-`I4` - the interface decisions taken with the Phase C fixes.

`tests/test_citations.py` scans `vgq1-spacecraft/`, `dssq-groundstation/`,
`nicla_sense_me_aru/`, `tools/`, `tests/` and `.github/` (not the bundled `libraries/`
folders, patch files or caches) and fails when a tag in the code has no row here, or when a row
names a tag that no longer appears in the code. In the last column, "D-nn" is a decision of
this log and "NN §x" is section x of the document whose file name starts with NN in this
folder (for example 04 = [04_Space_Data_Link_ICD.md](04_Space_Data_Link_ICD.md)).

| Tag | Topic | Documented now |
|---|---|---|
| `contract 1.1` | Spacecraft (UNO Q) pin assignment: radio UART and M0/M1/AUX, model strap, sensor bus, SX126x SPI pins | [D-11](#d-11), [D-13](#d-13); 03 §4.1 |
| `contract 2` | Radio models, air-rate codes, power codes and sensitivity values | [D-08](#d-08), [D-10](#d-10), [D-25](#d-25); 02 §2 |
| `contract 2.1` | E22 UART models (T33D, T37S) and their register protocol | [D-08](#d-08), [D-09](#d-09); 02 §2.2-2.4; 04 §3.2 |
| `contract 2.2` | SX126x LoRa profiles 0-7, the "rate code" of the SPI option | [D-10](#d-10); 02 §2.5 |
| `contract 2.3` | Measured receiver sensitivity (test T-RF-03) and the `measured` source label | [D-25](#d-25); 02 §3.5; 08 §8.2 |
| `contract 3` | Telemetry packets: flight `tlm_packets.h` against ground `dictionary.py`, big-endian | [D-58](#d-58); 05 §1 |
| `contract 3.2` | RF packet: radio model codes and RF flags | 05 §1 (APID 0x011), §3 (RF.flags, RF.radio_model) |
| `contract 3.3` | MAG flag b4: an AS7265X lamp was lit during an RM3100 sample | [D-45](#d-45); 05 §3 (MAG.flags) |
| `contract 3.7` | Sensor-health bits, the SENSORS enable mask, FDIR flag RADIO_FAULT | 05 §3 (HK.sensor_health, HK.fdir_flags) |
| `contract 5` | SDLS: authentication and authenticated encryption (AES-256-GCM), keys, nonces | [D-28](#d-28) to [D-35](#d-35); 17; 04 §4.7, §5.4 |
| `contract 6` | USLP framing mode | [D-23](#d-23); 04 §4.3, §5.3 |
| `contract 7` | CFDP class 1 file delivery, recorder export and the EGSE relay | [D-27](#d-27); 04 §7 |
| `contract 8` | AS7265X processing on both ends (calibrated values, colour-matching fit) | [D-44](#d-44), [D-47](#d-47); 13 §8 |
| `contract 9` | Configuration surface: `config.h`, `station.toml` and the station sketch's compiled defaults | [D-04](#d-04); 15 |
| `contract 10` | Ground snapshot keys for the dashboard | [D-58](#d-58); 13 §18 |
| `contract 13` | Advanced spectroscopy (supersedes the first SPEC layout and commands) | [D-44](#d-44), [D-45](#d-45), [D-47](#d-47); 07 §5.3; 13 §8 |
| `contract 13.3` | Onboard spectral intelligence: log spectral code, light-source classifier, change detection | [D-47](#d-47) |
| `contract 13.4` | SPEC packet v3.1 (129 octets) | [D-47](#d-47); 05 §1 (APID 0x022) |
| `contract 13.5` | SPECBURST packet, first layout (superseded by contract 28) | [D-46](#d-46); 05 §1 |
| `contract 13.6` | Spectrometer commands and the science EVRs 0x0701 / 0x0702 | 05 §4; 14 §3.8 |
| `contract 13.7` | Ground spectral analytics, labelled heuristic or approximate | [D-47](#d-47); 13 §8 |
| `contract 14` | SX126x hardening: boosted gain with retention, CAD, error recovery | [D-26](#d-26); 02 §9, §11 |
| `contract 15` | Board-powered radio: brown-out derating and the reset cause | [D-17](#d-17); 03 §4.2.2; 08 §6.1 |
| `contract 16.3` | Ground keyboard, intermediate owner directive (superseded by contract 17.1) | [D-15](#d-15); [Superseded decisions](#superseded-decisions) |
| `contract 17.1` | Ground keyboard, final: one CardKB on the header I2C, second operator in the web UI | [D-15](#d-15); 06 §3 |
| `contract 18` | App Lab Apps, the App's `data/` folder, the host (systemd) fallback | [D-05](#d-05), [D-55](#d-55); 07 §1.2; 16 §9 |
| `contract 19` | AS7265X board facts (SparkFun SEN-15050: INT / RST swap, LED limits) | [D-44](#d-44); 03 §4.7.1 |
| `contract 20` | "RS rescue": concatenated coding on the SX126x path | [D-21](#d-21); 02 §11.3 |
| `contract 22.1` | RM3100 driver checked against the PNI manual (I2C register forms) | [D-52](#d-52) |
| `contract 23` | Self-contained offline setup: standard library only (and the in-house SX126x driver plan, later reversed) | [D-06](#d-06), [D-35](#d-35), [D-40](#d-40) |
| `contract 24` | Onboard autonomy and the other advanced functions | [D-49](#d-49), [D-50](#d-50); 01 §7 |
| `contract 24.1` | New opcodes and HK fields of the advanced functions | [D-37](#d-37); 05 §1 (HK), §4 |
| `contract 24.2` | Stored command sequences | [D-49](#d-49); 07 §4 |
| `contract 24.3` | CCSDS 121.0-B-3 lossless compression | [D-46](#d-46) |
| `contract 24.4` | Beacon urgency levels and the station annunciator | [D-50](#d-50); 06 §2 |
| `contract 24.5` | RM3100 built-in self-test (MAGBIST) | [D-52](#d-52); 07 §5.2 |
| `contract 24.6` | Magnetometer calibration (MAGCAL) | [D-48](#d-48); 07 §5.1 |
| `contract 24.7` | CCSDS Tracking Data Message | [D-51](#d-51); 18 §11 |
| `contract 24.8` | Ground automatic frequency correction (SX126x) | [D-26](#d-26); 02 §9.3 |
| `contract 25` | SX126x driver on RadioLib, library defects fixed by the project (owner reversal) | [D-40](#d-40); 22 §3 |
| `contract 25.1` | App Lab build profiles take no build flags, so build options are `config.h` defines | [D-04](#d-04) |
| `contract 25.2` | Policy for a bundled, patched library | [D-40](#d-40); 22 §2 |
| `contract 26` | AS7265X cross-check outcome: LED current limits, unknown lamp state, ERR_NO_LAMP | [D-44](#d-44) |
| `contract 27` | Offline ground rework follow-ups (E22 configuration at 9600 bit/s, no `requirements.txt`) | [D-06](#d-06); 15 §4.3 |
| `contract 28` | Compression outcome and the final SPECBURST layout | [D-46](#d-46); 05 §1 |
| `contract 29` | Autonomy integration: two-stage command parser, RAM-only tables | [D-37](#d-37), [D-49](#d-49) |
| `contract 30` | Owner decision: build with the bundled, patched RadioLib | [D-40](#d-40); 22 §3 |
| `contract 33` | Radio model chosen without a code edit (A0 strap on the spacecraft, `model = "auto"` on the ground) | [D-09](#d-09); 03 §4.5 |
| `contract 39` | Receiver data logger | [D-53](#d-53); 18 §6.6, §7, §8 |
| `contract 41` | Bundled, patched Arduino_RouterBridge | [D-41](#d-41); 22 §4 |
| `PB-0.4` | Global rules: no blocking Bridge call, gated notifications, no heap after start-up, bounded loops | [D-43](#d-43), [D-56](#d-56); 16 §5 |
| `PB-1` | Telemetry packet field tables | [D-58](#d-58); 05 §1 |
| `PB-1.1` | Common TM packet framing, `kMaxPayload`, per-APID sequence counters | 04 §4.8 |
| `PB-1.2` | APID and virtual-channel map | [D-22](#d-22); 04 §4.6 |
| `PB-1.3` | HK packet (APID 0x010, 78 octets) | 05 §1 (APID 0x010); 13 §7.3 |
| `PB-1.4` | RF packet (APID 0x011, 49 octets) | 05 §1 (APID 0x011); 13 §7.4 |
| `PB-1.6` | CMDVER packet | [D-38](#d-38); 04 §4.10 |
| `PB-1.7` | TIMECORR packet (time correlation) | 04 §4.9 |
| `PB-1.8` | MAG packet | [D-45](#d-45), [D-48](#d-48); 05 §1 (APID 0x020) |
| `PB-1.9` | ATT packet and its flags | 05 §1 (APID 0x021), §3 (ATT.flags) |
| `PB-1.10` | SPEC v3.1 packet and its flags | [D-47](#d-47); 05 §1 (APID 0x022) |
| `PB-1.11` | ENV packet (Nicla Sense ME) | [D-52](#d-52); 05 §1 (APID 0x023) |
| `PB-1.12` | SPECBURST packet | [D-46](#d-46); 05 §1 |
| `PB-1.13` | CFDP packets (APID 0x030) | [D-27](#d-27); 04 §7 |
| `PB-1.15` | Sensor-health bits | 05 §3 (HK.sensor_health) |
| `PB-1.16` | Ground decoding rules from the AS7265X and compression outcomes | [D-44](#d-44), [D-46](#d-46); 13 §8 |
| `PB-2` | Telecommands | [D-37](#d-37); 05 §4 |
| `PB-2.1` | TC packet and the two-stage parser | [D-37](#d-37); 04 §5.5 |
| `PB-2.2` | Command error codes and verification | [D-38](#d-38); 05 §3 (CMDVER.error_code) |
| `PB-2.3` | Command table 0x01-0x20 | [D-37](#d-37), [D-39](#d-39); 05 §4 |
| `PB-2.4` | Ground command dictionary model (refuses what the spacecraft or the frame size would refuse) | [D-23](#d-23), [D-37](#d-37) |
| `PB-2.5` | CFDP command behaviour and the relay gate | [D-27](#d-27); 04 §7.4 |
| `PB-2.6` | EVR event-ID map | [D-58](#d-58); 14 §3 |
| `PB-3.1` | Flight `config.h` content | [D-04](#d-04); 15 §3 |
| `PB-3.2` | `station.toml` keys and defaults | 15 §4 |
| `PB-3.3` | Downlink load control | [D-24](#d-24); 01 §7.5 |
| `PB-4` | MCU-Linux Router Bridge RPC on both boards | [D-42](#d-42), [D-43](#d-43); 04 §9 |
| `PB-4.1` | Spacecraft RPC: EGSE calls and MCU notifications | [D-43](#d-43); 04 §9.2-9.4 |
| `PB-5` | Ground snapshot, web endpoints and dashboard | [D-54](#d-54); 13 |
| `PB-5.1` | Snapshot principles: a JSON-safe copy built inside the station lock | [D-54](#d-54) |
| `PB-5.3` | Snapshot keys | 13 §18 |
| `PB-5.4` | Operational facts the UI must show; a field at its "not available" value is shown as no value | 05 §1 (column "N/A"); 13 §1.5 |
| `PB-5.5` | Alarm logic | 13 §15; 14 §5 |
| `PB-5.6` | Web endpoints and their limits | [D-36](#d-36); 13 §16 |
| `PB-5.7` | Dashboard | [D-16](#d-16); 13 |
| `PB-6.1` | App Lab facts: entry points, the `data/` folder, an escaping exception stops the App | [D-05](#d-05), [D-54](#d-54) |
| `PB-6.2` | Ground App layout and its files (configuration, key, archive) | [D-05](#d-05), [D-29](#d-29); 15 §7 |
| `PB-6.3` | Spacecraft EGSE: TM epoch, TC sequence-number restore, file relay | [D-30](#d-30), [D-31](#d-31), [D-33](#d-33); 04 §9.6 |
| `PB-6.4` | Station annunciator: lamp and buzzer patterns | [D-50](#d-50); 06 §2 |
| `PB-7` | The list of follow-ups owed to the integration | the `F-nn` rows below |
| `PB-8.1` | Interface names agreed for the integration (for example `rf_foff` for the contract's frequency-offset call) | 04 §9.7 |
| `PB-8.4` | Python interfaces: the GDS `Runner` and the decoder | [D-54](#d-54); 16 §3 |
| `PB-8.4a` | Simulator: software spacecraft, RF channel and ground radio | 07 §1.5; 16 §8 |
| `PB-8.5` | Checks every change must pass | [D-57](#d-57); 08 §2 |
| `PB-8.6` | Flight executive integration rules (PA-off windows, bursts, bus recovery) | [D-45](#d-45); 01 §3.2 |
| `C-03` | SPECBURST layout: contract 28 wins over contract 13.5 | [D-46](#d-46) |
| `C-06` | The recorder keeps two drop counters: overwrite drops latch URGENT, export-pause drops do not | [D-50](#d-50); 05 §1 (HK `ssr_dropped`) |
| `C-07` | CFDP segment sizes live only in `ccsds/cfdp.h` | [D-27](#d-27) |
| `C-08` | SEQLOAD and MAGCAL do not fit a USLP TC frame with AE | [D-23](#d-23); 04 §5.6 |
| `C-15` | AS7265X burst integration per bank: (1 + 1) x 2.78 ms = 5.56 ms | 15 §3.6 |
| `C-21` | ATT flags b5 / b6: which magnetometer fed TRIAD, BMM150 scale read | 05 §3 (ATT.flags) |
| `C-22` | `cfdp_cancel` carries the transfer's sequence number in decimal | [D-27](#d-27); 04 §9.3 |
| `C-24` | `kMaxPayload` = 146 octets | 04 §4.8 |
| `C-25` | MCU status split into `sc_status` and `sc_status_x` | [D-43](#d-43); 04 §9.4 |
| `C-26` | A burst starts with the PA off, then runs through transmissions | [D-45](#d-45) |
| `C-27` | Packet periods stretched to the measured link capacity | [D-24](#d-24); 01 §7.5 |
| `F-03` | Real I2C bus clear in `I2cBus::recover()` | 01 §6 |
| `F-04` | Unknown lamp state treated as lit; ERR_NO_LAMP | [D-44](#d-44), [D-45](#d-45); 14 §3.8 |
| `F-06` | Burst read time reported in EVR 0x0704 | 14 §3.8 |
| `F-07` | AS7265X health bit taken from the run-time state | 05 §3 (HK.sensor_health) |
| `F-08` | Pseudo-terminal tests of the E22 serial path | 08 §2.2 |
| `F-10` | Configuration check: E22 UART code 3 (9600 bit/s) | 15 §4.3 |
| `F-13` | No `requirements.txt` in either App | [D-06](#d-06) |
| `F-23` | TRIAD attitude from the calibrated magnetometer | [D-48](#d-48) |
| `F-25` | FDIR clears I2C_BUS and AUX_TIMEOUT by itself | [D-64](#d-64); 14 §6 |
| `F-31` | Station keys `afc`, `freq_offset_calibrated` and `field_ut` | 15 §4.3, §4.4, §4.10 |
| `F-32` | AFC in the GDS | [D-26](#d-26); 02 §9.3 |
| `F-33` | TDM recording in the GDS | [D-51](#d-51); 18 §11 |
| `F-38` | RS rescue of uplink frames on the spacecraft (EVR 0x0307) | [D-21](#d-21); 14 §3.4 |
| `F-40` | CRC-failed packets of CADU length delivered for RS rescue | [D-21](#d-21); 02 §11.3 |
| `F-42` | OID identification `DE <callsign> VGQ-1` and the ground's `link.ident` | [D-18](#d-18); 04 §4.4 |
| `F-45` | USLP uplink in FOP-1; replay of SDLS, USLP and VC3 data | [D-23](#d-23); 18 §13 |
| `F-47` | CFDP receiver in the GDS | [D-27](#d-27); 18 §14 |
| `F-52` | Old default-model constants removed from `dssq/link.py` (callers use the model tables) | code clean-up only; no lasting rule |
| `F-54` | Ground test comparing the flight radio tables with `dssq.link` | [D-25](#d-25); 08 §2.2 |
| `F-60` | Non-blocking RM3100 read (`Rm3100::poll_drdy()`) | [D-56](#d-56) |
| `F-63` | Hardline CLTU tool `dssq.cltu` brought to v3 (key, SDLS service, framing) | [D-32](#d-32); 07 §2.6 |
| `F-64` | Crash-safe SDLS state on the ground | [D-32](#d-32) |
| `I1` | CMDVER command source in the stage octet | [D-38](#d-38) |
| `I2` | A change of SDLS status on a VC is a packet discontinuity | [D-34](#d-34) |
| `I3` | The SX126x station status line carries the compiled chip and TCXO settings, compared with `station.toml` | 04 §9.7; Appendix A (C26) |
| `I4` | RPC mailbox to the loop thread | [D-42](#d-42); 04 §9.5 |

---

## How to add a decision

1. Take the next free ID (D-65, ...). Never renumber or delete an entry: mark it superseded and
   add a row to [Superseded decisions](#superseded-decisions).
2. Fill in all five fields. Evidence must be checkable by someone else: a file and symbol, a
   datasheet section, a standard clause, a test name, or a commit whose message carries the
   numbers.
3. If something is not proven on hardware, write **VERIFY on hardware** and add it to
   [20_Known_Limitations.md](20_Known_Limitations.md).
4. Add the entry to the index table and link it from the documents it affects.
