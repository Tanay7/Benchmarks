# 10 - References

Every external document, standard, library and tool the project relies on, with the exact
version or commit that was used. Where a fact in the code or the documents comes from one of these,
the code comment or the document names the section.

Conventions: "used for" says which part of the project depends on the reference; "checked with"
says how the project verified its implementation against it. Issue dates marked (*) were not
checked against a copy of the document inside this project (public.ccsds.org was not reachable
from the build environment); confirm them at https://public.ccsds.org before relying on them.

---

## 1 CCSDS Recommended Standards (Blue Books) implemented

| Document | Title | Issue, date | Used for | Checked with |
|---|---|---|---|---|
| CCSDS 131.0-B-5 | TM Synchronization and Channel Coding | Issue 5, September 2023 | ASM 0x1ACFFC1D, Reed-Solomon (255,223) E = 16 dual basis, shortened to a 200-octet frame, 255-bit pseudo-randomizer | flight RS encoder vs the independent ground decoder (`tests/test_cross_check.py`), optional `reedsolo` reference |
| CCSDS 132.0-B-3 | TM Space Data Link Protocol | Issue 3, October 2021 | TM transfer frames, virtual channels, First Header Pointer, OCF (CLCW), FECF | bit-exact flight/ground cross-checks |
| CCSDS 133.0-B-2 | Space Packet Protocol | Issue 2, June 2020 | TM and TC space packets, APIDs, sequence counts | same |
| CCSDS 301.0-B-4 | Time Code Formats | Issue 4, November 2010 | CUC secondary header (4 + 2 octets, implicit P-field 0x2E) | same |
| CCSDS 231.0-B-4 | TC Synchronization and Channel Coding | Issue 4, July 2021 | CLTU, BCH(63,56) code blocks | flight BCH encoder vs ground (`test_flight_cltu_matches_ground_encoder`) |
| CCSDS 232.0-B-4 | TC Space Data Link Protocol | Issue 4, October 2021 | TC transfer frames, Type-AD / BD / BC | host tests, `tests/test_tc.py`, `test_fop.py` |
| CCSDS 232.1-B-2 | Communications Operation Procedure-1 | Issue 2, September 2010 | FARM-1 (flight), FOP-1 (ground), CLCW | host FARM-1 state-table test, `tests/test_fop.py` |
| CCSDS 732.1-B-2 | Unified Space Data Link Protocol (USLP) | Issue 2, October 2021 (*) | selectable USLP downlink framing, USLP TC frames | bit-exact cross-checks (`tests/test_uslp.py`); spacepackets 0.32.0 as a second implementation during development |
| CCSDS 355.0-B-2 | Space Data Link Security Protocol (SDLS) | Issue 2, July 2022 (*) | SDLS AE (AES-256-GCM) on TC and TM, AUTH (HMAC), null SA, anti-replay | `tests/test_sdls.py` (known answers, tamper and replay) |
| CCSDS 727.0-B-5 | CCSDS File Delivery Protocol (CFDP) | Issue 5, July 2020 (*) | class 1 (unacknowledged): Metadata, File Data, EOF, modular checksum | `tests/test_cfdp.py`; cross-checked against spacepackets 0.32.0 and cfdp-py 0.7.0 |
| CCSDS 121.0-B-3 | Lossless Data Compression | Issue 3, August 2020 | Adaptive Entropy Coder ("Rice") for SPECBURST | the official CCSDS 121.0-B-2 test data (`121B2TestData`, distributed with libaec): flight 56/56 files with n <= 16 bit-exact, ground 74/74 streams |
| CCSDS 503.0-B-2 + Cor. 1 | Tracking Data Message (TDM) | Issue 2, June 2020; Technical Corrigendum 1, October 2021 | per-pass TDM (KVN) | validating reader parses the Blue Book annex examples (`tests/test_products.py`; optionally Orekit's transcriptions) |
| CCSDS 660.0-B-2 | XML Telemetric and Command Exchange (XTCE) | Issue 2, February 2020 (adopts OMG XTCE 1.2) | mission database export from the dictionary | validated against `SpaceSystemV1.2.xsd` when `xmlschema` is installed and `DSSQ_XTCE_XSD` names the schema file; structural checks always |

Supporting CCSDS documents: CCSDS 130.0-G (Overview of Space Communications Protocols, Green Book;
background), SANA registries (the spacecraft ID 0x0A7 used here is **not** registered - a private
value for this terrestrial demonstration). Which standards are deliberately not used, and why:
[11_Standards_Status_and_Roadmap.md](11_Standards_Status_and_Roadmap.md).

## 2 Other standards and specifications

| Document | Used for |
|---|---|
| NIST FIPS 197, Advanced Encryption Standard (AES) | AES-256 on both ends; Appendix C.3 example as a test vector |
| NIST SP 800-38D, Recommendation for Block Cipher Modes of Operation: Galois/Counter Mode (GCM) and GMAC | GCM-AE / GCM-AD, 96-bit IVs, 128-bit tags; section 8 (IV uniqueness) is the reason for the nonce design |
| D. McGrew, J. Viega, "The Galois/Counter Mode of Operation (GCM)", revised submission to NIST | test cases 13-16 (AES-256, 96-bit IV) on both ends |
| NIST FIPS 180-4, Secure Hash Standard | SHA-256 (HMAC, key derivation) |
| IETF RFC 2104 (HMAC), RFC 4231 (HMAC-SHA-256 test vectors) | SDLS AUTH, key derivation |
| OMG XTCE 1.2 (namespace http://www.omg.org/spec/XTCE/20180204), schema `SpaceSystemV1.2.xsd` | XTCE export |
| CIE 015:2018 Colorimetry | XYZ, xy, CIE 1960 UCS (u, v), CCT, Duv |
| ITU-R P.525 (free-space attenuation), ITU-R P.526 (diffraction, Fresnel zones) | link budget (`tools/link_budget.py`) |
| IARU Region 1 / national band plans; your licence | allowed channels and power ([09_Regulatory_and_Safety.md](09_Regulatory_and_Safety.md)) |
| ITU Radio Regulations, Article 25, No. 25.9 (amateur stations transmit their call sign at short intervals); US 47 CFR 97.119(a) as one national example (every 10 minutes and at the end of a communication) | station identification at both ends: flight `kCallsignPeriodS`, ground `StationIdent` (D-61 in [12_Design_Decisions.md](12_Design_Decisions.md); [09_Regulatory_and_Safety.md](09_Regulatory_and_Safety.md) section 1.3) |
| RFC 4180 (CSV) | CSV export format |

## 3 Hardware documentation

| Part | Document (version) | Used for |
|---|---|---|
| EBYTE E22-400T33D (default radio) | E22-400T33D V2.1 user manual v1.05 (Chinese edition) | supply 3.3-5.5 V (>= 4.7 V full power), TX 1.0 A typ / 1.2 A max, sensitivity -126 / -128 / -130 dBm at 2.4k, registers REG0-REG3, AUX timing (5.6.2), M0/M1 pull-ups. Its REG1 power table appears copied from the T30D: **VERIFY** codes 1-3 (T-RF-07) |
| EBYTE E22-400T37S (5 W option) | E22-xxxT37S user manual v1.5 | 37 dBm at every power code, 4.5-15 V supply, -126 dBm typ, abnormal-status log `FF FF FF 01..04`, PA over-temperature (about 120 degC), "very weak" M0/M1 pull-ups |
| EBYTE E22-400TBH-02 test board | E22/E32-xxxTBH-02 user manual v1.0 (2024-03-06) | header pin-out, M0/M1 and RXD/TXD jumper caps, CH340X USB-UART |
| EBYTE E22-400M33S (SX126x reference module) | module datasheet not available to the project | TCXO voltage, PA gain (11 dB default), RF-switch wiring are **VERIFY** items |
| Semtech SX1261/2 (SX1268 is the same command set for 410-510 MHz) | datasheet rev 2.2 (owner-supplied) | sensitivity Table 3-8, FERR_L limits, BUSY (8.3.1), TCXO (9.2.1, 13.3.6), Rx boosted gain retention (9.6), duty-cycled RX (13.1.7), packet status (13.5.2, 13.5.3), PA tables (13.1.14, 13.4.4), known limitations (15.1-15.4) |
| ams OSRAM AS7265x | datasheet v1-04 (2018-07-09) | virtual-register I2C protocol, gains, integration (N + 1) x 2.78 ms, LED drivers, accuracy figures |
| SparkFun Spectral Triad AS7265x (SEN-15050) | hookup guide; `Firmware/AS7265x/Readme.md` (INT / RST pins swapped); library 1.0.5 and its examples (Example3_Settings LED limits) | board facts, LED current limits; the library itself is not used |
| PNI Sensor RM3100 | user manual Doc 1017252 r06 | I2C register map, cycle counts, DRDY, built-in self-test (5.6.1), I2C example (5.8.4) |
| Arduino Nicla Sense ME (ABX00050) | datasheet (modified 08/10/2026) | power (VIN 3.5-5.5 V), VDDIO_EXT 3.3 V, J2 header |
| Bosch BHI260AP | datasheet BST-BHI260AP-DS000-02, rev 1.1 (15 April 2021) | FIFO event IDs and scaling (Table 88, 13.2.8), virtual-sensor dynamic range |
| Bosch BMM150 | datasheet BST-BMM150-DS001-05, rev 1.4 | range, resolution, offsets |
| Bosch BMP390 | datasheet BST-BMP390-DS002-07, rev 1.7 | range 300-1250 hPa, accuracy |
| Bosch BME688 | datasheet BST-BME688-DS000-03, rev 1.3 | temperature, humidity, gas, IAQ accuracy |
| STMicroelectronics STM32U5 series (the UNO Q MCU, STM32U585) | reference manual RM0456; the ST HAL IWDG driver (`HAL_IWDG_Init`) and Zephyr's `wdt_iwdg_stm32` driver as sources of the register sequence | independent watchdog (IWDG: key register sequence, prescaler / reload limits, status-register wait, debug freeze; D-60), RCC reset flags (reset cause, D-17) |
| Arduino UNO Q (ABX00162 / ABX00173) | datasheet and user manual (modified 17/06/2026), schematic | MCU / MPU, header pin functions, A4/A5 3.3 V-only I2C3, A0 not 5 V tolerant (9.7), JANALOG pin 5 = USB VBUS, power inputs |
| Arduino VENTUNO Q (ABX00181) | datasheet (modified 06/10/2026) | QCS8275, 16 GB LPDDR5, 64 GB eMMC, M.2 2230 NVMe (non-bootable), header I2C (PH12 / PH11), +5V_LIMITED 2.8 A, input supply |
| M5Stack CardKB Mini Keyboard Programmable Unit (MEGA328P), SKU U035 | product page (owner-supplied) | I2C address 0x5F, Grove HY2.0-4P pin colours, 5 V supply |
| Microchip ATmega328P | datasheet (speed grades; general knowledge) | why 3.3 V CardKB operation is "try first" |

## 4 Board support and platform

| Item | Version / commit | Used for |
|---|---|---|
| Arduino App Lab (arduino-app-cli, app-bricks-py) | the release on the boards; sources read for `compose_template.go`, `orchestrator.go`, `run.sh` | App layout, container mounts, port publishing, `requirements.txt` behaviour (contract 18, 23; PB-6.1) |
| arduino-cli | 1.5.2-0.20260925125641-e39aa9996f70 (the version App Lab embeds; real builds); 1.5.1 in CI (`arduino/setup-arduino-cli`) | real builds |
| ArduinoCore-zephyr ("Arduino Q Boards", `arduino:zephyr`) | main `b08a65041483f870e7edbfe3f5ad20589b2e799b` (2026-10-09) and tag 1.0.0 `79b3f1afdad455f55e4a25030953617152c0227c` | both real-build environments; variant overlays `arduino_uno_q_stm32u585xx` (`serials`, `i2cs`, router serial, console) and `arduino_ventuno_q_stm32h5f5xx`; its build configuration has `CONFIG_WATCHDOG` not set (why the flight programs the IWDG directly, D-60) |
| Zephyr (west workspace of the core) | main: `71389af800f061eef0dc3712821a8f7f0d12b331`; 1.0.0: `1743741760ee5d2d58da50d504855d43f9f8e826` | kernel facts (thread stacks, heap, DAC driver) |
| ArduinoCore-API | tag 1.5.2 (`cd91833d90b4fe50e428021ba5051e2b7ceafc84`) | typed `pinMode` / `digitalWrite` / `attachInterrupt` (RadioLib hunk 1) |
| Zephyr SDK | 1.0.1: arm-zephyr-eabi-gcc 14.3.0, GNU ld 2.43.1 | real builds, stack measurements |
| Arduino docs (docs-content) | UNO Q / VENTUNO Q user manuals and power specifications | pin naming, power advice |
| Owner's UNO Q / VENTUNO Q LINPACK App Lab packages | - | App folder layout, FP32-only MCU FPU, heap behaviour, Bridge thread rules |

## 5 Software libraries

### 5.1 In the sketches

| Library | Version | Commit | Source | Notes |
|---|---|---|---|---|
| RadioLib | 7.8.1 | `2325a38a89dd0f0e402c19f67fe910111da1e246` | github.com/jgromes/RadioLib | bundled and patched; SX126x option only ([22_Bundled_Libraries.md](22_Bundled_Libraries.md)) |
| Arduino_RouterBridge | 0.4.3 | `d681b87211864148367f2d77096ae84c5badcb2f` | github.com/arduino-libraries/Arduino_RouterBridge | bundled and patched |
| Arduino_RPClite | 0.3.1 | `871a213aefa53020f5a6881a3a99d0f66d172fd7` | platform library dependency | unchanged |
| MsgPack | 0.4.2 | `1f552c31b940d6e9063ee17a4b3fa10c47b27169` | platform | unchanged |
| DebugLog | 0.8.4 | `b581f7dde6c276c5df684e2328f406d9754d2f46` | platform | unchanged |
| ArxContainer | 0.7.0 | `d6affcd0bc83219b863c20abf7c269214db8db2a` | platform | unchanged |
| ArxTypeTraits | 0.3.2 | `702de9cc59c7e047cdc169ae3547718b289d2c02` | platform | unchanged |
| Wire, SPI, Arduino_LED_Matrix | with the core | - | arduino:zephyr | - |
| Arduino_BHY2 | 1.0.8 | - | Arduino Library Manager | Nicla Sense ME firmware only, with the "Arduino Mbed OS Nicla Boards" core (Arduino IDE; needs the internet once) |

### 5.2 On the Linux sides

| Package | Version | Where | Notes |
|---|---|---|---|
| Python | 3.11 or newer (App container 3.13; tested on 3.11 and 3.13) | both Apps | standard library only at run time |
| `arduino.app_utils`, `arduino.router_bridge` | from App Lab's Python image | both Apps | not installed by the project |
| arduino-router-bridge (PyPI) | 0.5.1 | host (systemd) mode only, installed by `install_station.sh` | brings msgpack |

### 5.3 Development and verification only (never on the boards)

| Tool / package | Version used | Role |
|---|---|---|
| pytest | >= 8 (9.1.1 used) | ground test runner |
| reedsolo | 1.7.0 | optional independent Reed-Solomon reference |
| cryptography | >= 42 (50.0.2 used) | optional independent AES-GCM cross-check |
| xmlschema | 4.3.2 | optional XTCE schema validation |
| ruff | 0.17.0 | Python lint |
| mypy | 2.4.0 | Python type check |
| actionlint | 1.7.7 | GitHub Actions workflow lint |
| g++ (host) | C++17 | host tests, `check_firmware.sh` |
| libaec | 1.1.7; test data at commit `b8fd98cfbba2fcc82f1c99813465d7c4806b9d4d` | CCSDS 121.0 reference implementation and official test data |
| spacepackets 0.32.0, cfdp-py 0.7.0 | - | independent USLP / CFDP implementations used for cross-checks during development |
| Orekit TDM example files | - | optional TDM reader test (`DSSQ_OREKIT_TDM_DIR`) |
| Yamcs `SpaceSystemV1.2.xsd` | - | XTCE schema |
| GitHub Actions | `actions/checkout` v7.0.1 (`3d3c42e5...`), `actions/setup-python` v7.0.0 (`5fda3b95...`), `arduino/setup-arduino-cli` v2.0.0 (`81d31074...`) | CI, pinned by full commit hash in `ci.yml` |

## 6 Algorithms and physics

* H. D. Black, "A passive system for determining the attitude of a satellite", AIAA Journal 2(7),
  1964 - TRIAD.
* B. P. Welford, "Note on a method for calculating corrected sums of squares and products",
  Technometrics 4(3), 1962 - single-pass variance (FP32 magnetometer statistics).
* S. W. Shepperd, "Quaternion from rotation matrix", Journal of Guidance and Control 1(3), 1978.
* E. R. Berlekamp, *Algebraic Coding Theory*, 1968; J. L. Massey, "Shift-register synthesis and BCH
  decoding", IEEE Trans. IT-15, 1969; G. D. Forney, "On decoding BCH codes", IEEE Trans. IT-11,
  1965 - Reed-Solomon and BCH decoding.
* V. Shoup, table-driven multiplication in GF(2^128) (8-bit tables) - the ground GHASH.
* C. E. Shannon, "A Mathematical Theory of Communication", 1948 - Eb/N0 >= -1.59 dB.
* Semtech LoRa demodulator SNR limits (SF7 -7.5 dB ... SF12 -20 dB) and air-time formula - LoRa
  sensitivity and air time (`dssq/link.py`, `drivers/radio.cpp`).
* 4/3 effective Earth radius for the radio horizon.
* C. Wyman, P.-P. Sloan, P. Shirley, "Simple Analytic Approximations to the CIE XYZ Color Matching
  Functions", Journal of Computer Graphics Techniques 2(2), 2013 - colour-matching functions.
* C. S. McCamy, "Correlated color temperature as an explicit function of chromaticity
  coordinates", Color Research & Application 17(2), 1992 - CCT cross-check.
* Vegetation indices: NDVI (Rouse 1974), GNDVI (Gitelson 1996), NDRE (Barnes 2000), WBI
  (Penuelas 1993, R900/R970 - here a proxy), PRI (Gamon 1992, R531/R570 - here a proxy).
* SI 2019 exact constants (Planck, Avogadro) - PPFD.
* NOAA NCEI / BGS World Magnetic Model calculators - site inclination, declination and total field
  (`config.h` `kGeomagInclDeg`, `[science] field_ut`).
* G. J. Holzmann, "The Power of Ten - Rules for Developing Safety Critical Code", IEEE Computer
  39(6), 2006 (NASA/JPL) - the flight coding rules.
* Deep Space 1 Beacon Monitor Operations Experiment (NASA JPL) - beacon urgency levels.
* Voyager Computer Command Subsystem time-tagged sequences - stored command sequences.

## 7 Further reading

* Fortescue, Swinerd and Stark (eds.), *Spacecraft Systems Engineering*, Wiley.
* Carden, Jedlicka and Henry, *Telemetry Systems Engineering*, Artech House.
* JPL DESCANSO (Deep Space Communications and Navigation Systems) book series, especially
  J. Taylor (ed.), *Deep Space Communications* - free from JPL.
* CCSDS 130.0-G, *Overview of Space Communications Protocols* (Green Book) - free at
  https://public.ccsds.org.
