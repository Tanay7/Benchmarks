# 20 - Known Limitations and Open Items

This is the register of everything that is **not yet proven**, **limited on purpose**, or a
**residual risk**. It exists so that nobody has to discover these the hard way, and so that a
reviewer can see exactly what the automated tests do and do not cover.

> **Overall status.** The software passes every automated layer
> ([08_Verification_and_Test.md](08_Verification_and_Test.md) section 1). **It has not yet been
> run on the real boards and radios.** Every item marked *Open* below needs a hardware test or a
> decision. When a test closes an item, record the result in
> [21_Test_Records.md](21_Test_Records.md), change the item's status here to *Closed* with the
> date and the measured value, and update the named configuration value if needed.

How to read the tables: **ID** is stable (refer to it in issues and test records): V = not yet
verified on hardware, P = needs the internet once, L = limit by design, B = bundled library,
S = science, R = regulatory. The decision IDs D-nn belong to
[12_Design_Decisions.md](12_Design_Decisions.md). **Risk if
wrong** says what happens when the assumption is false. **Closed by** names the test in
[08_Verification_and_Test.md](08_Verification_and_Test.md) (or the action) that removes the
item. **Status**: Open (needs a test or decision), Accepted (a known limit we live with),
Mitigated (a residual risk with a protection in place).

---

## 1 Values not yet verified on hardware (every "VERIFY" in the code and the documents)

### 1.1 Radio

| ID | Assumed value (where) | Source of the assumption | Risk if wrong | Closed by | Status |
|---|---|---|---|---|---|
| V-01 | E22-400T33D power codes 1, 2, 3 = 30 / 27 / 24 dBm (`drivers/radio.cpp kT33dPowerDbm`, `dssq/link.py`, `dssq/radio/e22.py`, `dictionary.py`, `station.toml`) | 3 dB steps below the specified 33 dBm maximum; the manual v1.05 REG1 table was copied from the E22-400T30D (30/27/24/21 dBm) | Link budget, EIRP and the web UI's transmit power are off by up to 3 dB on codes 1-3; the brown-out derating level (code 2) differs. The web UI shows a "VERIFY T-RF-07" badge on these values | T-RF-07 | Open |
| V-02 | SX126x module PA gain 11 dB, so codes 0-3 radiate 33 / 28 / 25 / 21 dBm (`config.h kSxPaGainDb`, `station.toml pa_gain_db`) | EBYTE E22-400M33S reference module (33 dBm maximum) | Same as V-01 for the SX126x option | T-RF-07 | Open |
| V-03 | SX126x TCXO on DIO3 at 1.8 V (`config.h kSxTcxoV`, station sketch `kSxTcxoMv`, `station.toml tcxo_v`) | Reference module; module datasheet not in the reference set | RadioLib falls back to the crystal (flight EVR 0x0309, station S\| flag b3): worse frequency stability; profiles narrower than 62.5 kHz need a TCXO at both ends | T-RF-08 | Open |
| V-04 | SX126x RF switch: DIO2 not used; RXEN / TXEN driven by the MCU (UNO Q D8 / D9, VENTUNO Q A2 / A3) | Reference module wiring | No reception or no transmission on that module | T-RF-08 | Open |
| V-05 | SX1268 PA current limit 140 mA (`config.h kSxOcpMa`) | The SX1262 value (SX1261/2 DS rev 2.2 Table 5-2); no SX1268 datasheet in the reference set | Output power clamped below the expected drive | T-RF-08 | Open |
| V-06 | SX126x frequency error: register 0x076B-0x076D, scale Hz = 1.55 x value x BW_kHz / 1600, sign = +(f_received - f_receiver) (`drivers/sx126x.cpp`, station sketch, `dssq/link.py`) | RadioLib `getFrequencyError()`; not in DS Table 12-1; the sign is not documented anywhere | Doppler, range-rate and the TDM carry a wrong sign or scale. The AFC detects an inverted sign by itself (it flips once and reports it) | T-RF-06 | Open |
| V-07 | SX126x receiver noise figure 6 dB, used for the "computed" sensitivities of profiles 0, 1, 3-6 (`dssq/link.py SX126X_NF_DB`) | Assumption with Rx boosted gain (contract 2.2) | Margin and range estimates off by the NF error | T-RF-03 | Open |
| V-08 | SX126x SNR (SnrPkt) is referenced to the LoRa channel bandwidth (TDM `PC_N0` = SNR + 10 log10(BW)); RssiPkt is "average over the last packet" (signal plus in-band noise) | DS Table 13-80 gives no reference bandwidth; RssiPkt includes noise | `PC_N0` biased; TDM `CARRIER_POWER` over-reads at negative SNR | T-RF-06 step 8 | Open |
| V-09 | SX126x frequency estimator averaging window | Undocumented | The TDM omits `INTEGRATION_INTERVAL` | (none possible without the vendor) | Accepted |
| V-10 | E22 sensitivity above 2.4 kbit/s = S(2.4k) + 10 log10(rate / 2400) (source "estimated") | Scaling rule; only 2.4k is in the manuals (T33D -128 dBm typ, worst -126; T37S -126 dBm typ) | Margin at the faster rates off by a few dB | T-RF-03 | Open |
| V-11 | The E22-400T33D answers the noise query `C0 C1 C2 C3 00 02` with noise plus last RSSI | Documented for the T37S; the T33D manual's example asks for one octet | No noise floor / SNR estimate on the T33D | T-HW-06 | Open |
| V-12 | SX126x BUSY bounds: 25 ms guard, 40 ms SPI timeout | Calibration 3.5 ms (DS 13.1.12) + 5 ms TCXO delay; CalibrateImage duration not given | False "BUSY stuck" faults and radio resets at bring-up | T-RF-08 | Open |
| V-13 | Duty-cycled hibernation receive with a 32-symbol wake preamble (2.1 s at SF12 / 62.5 kHz) | DS 13.1.7 timing | A hibernating SX126x spacecraft misses `WAKE` | T-RF-08 | Open |
| V-14 | Rx boosted gain kept through duty-cycled RX (retention list 0x029F-0x02A1 = 01 08 AC) | DS 9.6; RadioLib source checked | About 4 dB less sensitivity in hibernation | T-RF-08 | Open |
| V-15 | AFC engineering constants: first step after 3 packets, sign check over 3 packets, confirmation timeout 10 s, retry 5 s, settle = one CADU air time + 0.5 s (`dssq/link.py`) | Engineering choices | Slow or jittery frequency tracking | T-RF-06 step 7 | Open |
| V-16 | The AFC correction is applied to the station's transmitter too, so the spacecraft receives the uplink at about +2 x Doppler | Contract 24.8 | Negligible on the ground and on a balloon; with large Doppler compare 2 x Doppler against FERR_L (7.8 kHz at SF12 / 31.25 kHz) | analysis per mission profile | Accepted |
| V-17 | Station radio pipeline latency `radio_latency_s` = 0 (`station.toml`) | Unknown until measured | Time correlation (SCET) and TDM time tags biased by the latency | T-TM-04 | Open |
| V-18 | Absolute RSSI and noise levels: `[radio] rssi_cal_offset_db` = 0.0 (`station.toml`) | Neither maker specifies the absolute accuracy of the reported RSSI | Every absolute dBm value (received power, noise floor, measured link margin) is off by the uncalibrated offset; differences between two readings stay valid | T-RF-02 | Open |
| V-19 | E22 air time and busy model: UART time + payload bits / air rate x 1.3 (flight `drivers/radio.cpp e22_airtime_ms()`, ground `dssq/link.py E22_AIRTIME_FACTOR`) | Engineering estimate for the preamble, header and the module's sub-packet handling | Frame periods, quiet windows and the duty figure at the E22 rates are off by the model error | T-RF-03 (record the measured frame period), T-RF-09 | Open |

### 1.2 Spacecraft computer and platform

| ID | Assumed value (where) | Source | Risk if wrong | Closed by | Status |
|---|---|---|---|---|---|
| V-20 | Brown-out detection by a marker in TAMP backup register 31 (`fsw/flight.cpp read_reset_cause()`): a power-up clears the backup domain | STM32U5 reset flags cannot tell power-on from brown-out; ABX00162: the board's coin input powers only the PMIC clock, not the MCU | A power-up reported as BROWNOUT: the radio starts derated (code 2, EVR 0x0608) until `TXPWR 0`. A deep brown-out that clears the backup domain is reported as POWER-ON (no derating) | T-HW-08 | Open |
| V-21 | The bundled Arduino_RouterBridge thread stack of 1536 octets holds (measured worst case 1084 octets, 42 % margin) | Static stack analysis on the real toolchain, re-run by CI on every push (`tools/host_test/routerbridge/stackcalc.py`, [22_Bundled_Libraries.md](22_Bundled_Libraries.md)); the board has no hardware stack protection | A silent stack overflow (memory corruption) under a call chain the analysis missed | T-HW-10 | Open |
| V-22 | MCU-to-Linux notifications larger than 256 octets pass the router (`tm_cadu` 472 hex characters; station `rf_rx` about 490 octets of MsgPack) | The Python bridge accepts 1 MiB; router behaviour not measured | Lost CADU copies in the EGSE archive or lost SX126x packets at the station | T-HW-10 | Open |
| V-23 | Transient heap use of every `Bridge.notify()` (String + MsgPack inside the library) stays inside the 32 KiB reserve the recorder leaves (`config.h kHeapReserveBytes`) | Bound: one notification <= 490 octets | Allocation failure inside the library | T-HW-10 | Open |
| V-24 | Loop time: no call blocks more than about 20 ms with the EGSE attached (about 45 ms with the 64-octet Serial rings of cores before 1.0.0, D-66); Rice encoder stack (320-384 octets on a 64-bit PC, less expected on the 32-bit MCU) and run time (tens of microseconds per 54-sample packet, estimate) (`ccsds/rice.h`) | Design rule and host measurements | Loop overruns (HK `loop_overruns`, FDIR LOOP_OVERRUN) | T-HW-10 | Open |
| V-25 | The recorder gets 256 KiB at boot (`kSsrMaxBytes`, largest power of two leaving 32 KiB) | Zephyr gives the sketch's heap the remaining SRAM | Shorter SSR coverage (EVR 0x0003 tells the real size) | T-HW-11 | Open |
| V-26 | `REBOOT 0xB007` (NVIC_SystemReset) restarts the sketch without App Lab's help (the build uses `wait_linux_boot=app`) | Zephyr loader behaviour | The MCU stays down after a commanded reboot until the App is restarted | T-HW-08 step 2 | Open |
| V-27 | Board-powered radio: the T33D gets >= 4.7 V from the UNO Q's USB-C VBUS pass-through (JANALOG pin 5) and from the VENTUNO Q `+5V_LIMITED` | ABX00162 / ABX00181; Arduino warns current-limited peaks may reset the board | Reduced output power, resets during transmission | T-HW-07 | Open |
| V-28 | Hardware watchdog (IWDG): the direct register sequence (start, unlock, prescaler /512, reload 2499, wait for the update flags, refresh) arms it with a 40 s timeout at the nominal 32 kHz LSI (`config.h VGQ_IWDG`, `kIwdgTimeoutMs`, `kIwdgLsiHz`; `fsw/flight.cpp watchdog_start()`; `fsw/autonomy.h iwdg_settings()`, host-tested) | RM0456 and the ST HAL sequence; never run on a board. The LSI is not trimmed: the real timeout scales with its frequency | A watchdog that never fires (a hung loop is not recovered), or fires early (EVR 0x060C at boot says the settings were not confirmed). L-38 | T-FD-09 | Open |
| V-29 | Router UART between the MCU and Linux: LPUART1 at 115200 baud with a 1024-octet TX ring (arduino:zephyr 1.0.0 on; 64 octets before, D-66) | Taken from the Phase B integration specification (tag PB-0.4, see [12_Design_Decisions.md](12_Design_Decisions.md#appendix-b---citation-tags-in-code) appendix B), not re-checked in the core source | Notification throughput lower than assumed: lost `tm_cadu` copies, slow RPC answers | T-HW-10 | Open |
| V-30 | With the 64-octet Serial rings of arduino:zephyr before 1.0.0 (the stable App Lab's core) the UART pump keeps every E22 frame one continuous UART burst and loses no received octet, and a notification longer than the ring blocks the loop at most ~45 ms (D-66) | Core source, timing arithmetic, host test of the FIFO under ThreadSanitizer, real builds; never run on a board | Split RF packets, lost uplink octets or loop overruns on a board whose App Lab builds with 0.90.0 | T-HW-10 | Open |

### 1.3 Ground-station platform (Arduino App Lab, VENTUNO Q)

| ID | Assumption | Risk if wrong | Closed by / fallback | Status |
|---|---|---|---|---|
| V-30 | App Lab publishes the App's port 8080 on the LAN (`app.yaml ports: [8080]`, published N:N per arduino-app-cli) | Web UI not reachable from another PC | T-GS-01; fallback: host mode (`host/install_station.sh --service`) | Open |
| V-31 | The App container can open the E22 USB-UART (`/dev/ttyUSB0`; App Lab mounts `/dev`, group dialout) | No radio in App mode | T-GS-02; fallback: host mode | Open |
| V-32 | ModemManager does not probe the USB-UART | Garbage written to the radio's UART at plug-in | T-GS-03 (udev ignore rule) | Open |
| V-33 | The router socket (`/var/run/arduino-router.sock`) is reachable in host mode | No CardKB, lamps or RCU in host mode | T-GS-04 | Open |
| V-34 | An absolute `[gds] archive_dir` on an external M.2 2230 NVMe or USB 3.0 drive is visible inside the App container | Archive written into the container's own file system or refused | T-GS-05; fallback: default `<App>/data/archive` (64 GB eMMC) or host mode | Open |
| V-35 | Pure-Python AES-GCM is fast enough on the QCS8275 (0.28-0.33 ms per frame on a 2.8 GHz x86) | Frames queue at the fastest E22 rates | T-GS-06 | Open |
| V-36 | The station's Linux clock is NTP-disciplined UTC | Event times, ERT, TDM time tags and time correlation wrong | T-GS-07 | Open |
| V-37 | The CardKB works at 3.3 V (M5Stack specifies 5 V; logic level not published) | Erratic keys | T-HW-05; fallback: 5 V through a BSS138 level shifter | Open |
| V-38 | VENTUNO Q CardKB pins: the user manual says D20 = SDA (PH12), D21 = SCL (PH11); the core's overlay lists them the other way round | Keyboard not detected | Wire by the SDA / SCL silkscreen (the I2C peripheral assigns the roles); T-HW-05 | Mitigated |
| V-39 | Station identification on air at both ends: the spacecraft's EVR 0x0002 `DE <callsign>` (boot and every `kCallsignPeriodS` = 600 s) and its OID frames `DE <callsign> VGQ-1`; the ground's `DE <callsign> <name>` packets reach the spacecraft radio and are dropped there (RF `cltu_bad`) without any other effect. Host-tested only (`tools/host_test/test_station_id.cpp`, `tests/test_station_id.py`) | The identification rule is not met on air (the EVR text is hidden under TM SDLS AE; OID frames, never encrypted, go out only when no data waits), or a ground identification disturbs the spacecraft receiver | T-TC-07 (in the amateur configuration TC AUTH / TM NONE, R-01) | Open |

### 1.4 Instruments

| ID | Assumed value (where) | Risk if wrong | Closed by | Status |
|---|---|---|---|---|
| V-40 | BMM150 field in 0.1 uT from the BHI260's reported range (`config.h kAruMagUtPerLsb`); fallback 1/16 uT/LSB when the range cannot be read (ATT flag b5 clear) | Body-magnetometer magnitude scaled wrongly; TRIAD fallback attitude wrong | T-SC-06 | Open |
| V-41 | RM3100 axes = body axes (`config.h kRm3100ToBody` identity) | TRIAD attitude wrong when it uses the boom magnetometer | T-SC-01, T-SC-09 | Open |
| V-42 | Spectrometer boresight = body +Z (`kSpecBoresightBody`); the Nicla fusion world frame is ENU (Android convention) (`fsw/spectral.h`) | `bore_zen` / `bore_az` and the sky map wrong | T-SC-09 | Open |
| V-43 | AS7265X timings: boot 1000 ms (firmware loads from the board's EEPROM), soft-reset settling 1000 ms, handshake step 50 ms, resynchronisation polls (`drivers/as7265x.h`) | Spurious faults and soft resets | T-SC-08 | Open |
| V-44 | AS7265X bit meanings: DEV_SEL slave flags b4 / b5, the FW-version selector, COEF byte order (`drivers/as7265x.cpp`) | Wrong device presence or version in EVR 0x0706 / SPECDIAG | T-SC-08 | Open |
| V-45 | AS7265X gain and integration apply to all three devices; calibrated output independent of gain and integration (`fsw/spectral.h`, APPROXIMATE) | AGC, HDR merge and change detection compare values with a gain-dependent bias (datasheet calibrates at 16x / 166 ms, +/-12 %) | T-SC-08 | Open |
| V-46 | AS7265X BURST: 5.56 ms per bank at `kSpecBurstCycles` = 1; samples are not contiguous at 100 kHz I2C | Lower burst rate than requested (EVR 0x0704 reports the achieved rate) | T-SC-08 | Open |
| V-47 | Lamp deferral limit 2100 ms for magnetometer samples (`config.h kSpecLampDeferMaxMs`, engineering value) | MAG samples taken with a lamp lit (flag b4) | T-SC-12 | Open |
| V-48 | RM3100 self-test: bounded by 5 ms (`kRm3100BistTimeoutUs`); a POLL with the self-test enabled does not leave a stale measurement; HSHAKE default DRC0 = 1 is relied on; a healthy unit reads 0xFF | Self-test reported as failed, or a stale first measurement after `MAGBIST` | T-SC-10 | Open |
| V-49 | MAGCAL fit thresholds (MIN_COVERAGE 0.5, MAX_CONDITION 1e3, MAX_AXIS_RATIO 4, MAX_RESIDUAL_FRACTION 0.05, MIN_STEP_UT 1.0, MIN_SAMPLES 50; `dssq/magcal.py`) come from synthetic trials. A wrong `[science] field_ut` only scales the matrix and cannot be detected by the fit | Good rotations refused, or a scaled calibration accepted | T-SC-11 | Open |

### 1.5 Standards and data products

| ID | Item | Risk | Closed by | Status |
|---|---|---|---|---|
| V-50 | CFDP EOF: the code appends the entity-ID fault-location TLV on a non-zero condition code; whether 727.0-B-5 requires or only permits it was not checked against the standard's table (the text could not be retrieved; layouts were cross-checked against spacepackets 0.32.0 and cfdp-py 0.7.0). The ground accepts both | Interoperability with another CFDP implementation | Read the EOF PDU table of CCSDS 727.0-B-5 | Open |
| V-51 | XTCE export validated against the Yamcs-distributed SpaceSystem 1.2 schema, not byte-compared with OMG's normative file, and never loaded into a real mission control system; header `validationStatus` "Working" | Import errors in Yamcs / OpenC3 | Load `vgq1.xtce.xml` into an MCS | Open |
| V-52 | TDM band "UHF" taken as 300 MHz-3 GHz (ITU-R V.431, general reference); `ORIGINATOR` = the station name, not a SANA-registered abbreviation | A strict TDM consumer may reject the file | Owner decision | Accepted |

### 1.6 Items other documents mark VERIFY

The other documents tag a few hardware facts VERIFY that have no tag in the code. Those already
listed above: App Lab port exposure (V-30), the router UART and notification sizes (V-22, V-29),
loop time, stacks and heap (V-21, V-23, V-24), the recorder size (V-25), the watchdog (V-28),
AES-GCM speed (V-35), the external archive (V-34), the CFDP EOF TLV (V-50) and the XTCE import
(V-51). The rest:

| ID | Item (document) | Risk if wrong | Closed by | Status |
|---|---|---|---|---|
| V-60 | The station sketch that the App flashed stays on the VENTUNO Q's MCU after the App is stopped, so host mode finds it ([00_Beginner_Setup_Guide.md](00_Beginner_Setup_Guide.md) part 15) | Host mode shows the station I/O offline | T-GS-04 in host mode; fallback: press Run once in App Lab, then Stop, before starting the service | Open |
| V-61 | Real range on your path; the EBYTE reference distances (16 km / 20 km) are vendor claims ([02_Link_Budget_and_RF_Analysis.md](02_Link_Budget_and_RF_Analysis.md) section 12) | Less range than the free-space budgets suggest | T-FLD-01, T-FLD-02 | Open |
| V-62 | Power draw: the UNO Q's real consumption (Arduino publishes the 5 V / 3 A design budget only), the Nicla Sense ME with this firmware (0.1 A budget, estimate), the E22-400T37S below 12 V, the AS7265X lamp current path and the UNO Q 3.3 V rail under full lamp load (>= 3.1 V); configuration B supply (V-27) ([03_Hardware_Integration.md](03_Hardware_Integration.md) sections 6 and 11) | Undersized supply, battery or fuse; brown-outs under load | T-HW-01, T-FLD-03 (record PWR in [21_Test_Records.md](21_Test_Records.md)) | Open |
| V-63 | Sensor-bus wiring: RM3100 breakout pin names and pull-ups; the boom cable (about 1 m at 100 kHz); the SX126x module supply and pin-out ([03_Hardware_Integration.md](03_Hardware_Integration.md) sections 4.6, 4.7 and 11) | I2C errors (HK `i2c_errors`), a missing instrument, or no radio on the SX126x option | The checks of [03_Hardware_Integration.md](03_Hardware_Integration.md) section 4.7, T-HW-04, T-RF-08 | Open |
| V-64 | Station I/O: the CardKB key codes of the keyboard in use, pull-ups on the VENTUNO Q header I2C, the CardKB's levels at 5 V (through the level shifter), the buzzer type (an active buzzer is required) and loudness, the lamp visibility in the station's lighting ([06_Displays.md](06_Displays.md), [03_Hardware_Integration.md](03_Hardware_Integration.md) section 5.4) | Keys not recognised, keyboard not detected, an alarm not heard or seen | T-HW-05 and the station I/O checks of [06_Displays.md](06_Displays.md) | Open |
| V-65 | RS rescue rates on the SX126x path ([12_Design_Decisions.md](12_Design_Decisions.md) D-21) | Less gain from the concatenated code than expected (no risk to integrity: FECF, RS, BCH and SDLS still decide) | T-RF-03 near sensitivity (RECEIVER `rs_rescued`) | Open |
| V-66 | HK `bus_temp` ground limits -20 / 0 / 60 / 75 degC: only 75 degC is the flight's own threshold (`kBusTempRedC`), the others are ground engineering limits ([13_Web_UI_Reference.md](13_Web_UI_Reference.md) limits table) | Alarms too early or too late | Compare with the board ratings (owner check) | Open |
| V-67 | Buck converter examples (12 V to 5 V) in the bill of materials ([03_Hardware_Integration.md](03_Hardware_Integration.md) section 7.1, S8) | An undersized converter | Check the ratings in the current datasheet before buying | Open |

---

## 2 Not fully offline (installation)

The station and the spacecraft run without any download (no pip package, no Arduino library
download: both are inside the App Lab zips in `dist/`). These one-time steps still need the
internet:

| ID | What | Why | Status |
|---|---|---|---|
| P-01 | Arduino App Lab and its board platform / Python image | Installed by App Lab itself, outside this project | Accepted |
| P-02 | Nicla Sense ME firmware: Arduino IDE + "Arduino Mbed OS Nicla Boards" core + Arduino_BHY2 (which carries the BHI260AP firmware) | Not an App Lab board; one-time flash | Accepted |
| P-03 | Rebuilding the zips: `tools/radiolib.py` / `tools/routerbridge.py` fetch the exact library tags from github.com (verified by commit and SHA-256) | The repository holds only the patches, never the library sources | Accepted |
| P-04 | Host mode only: `host/install_station.sh` pip-installs `arduino-router-bridge==0.5.1` into a virtual environment (App Lab's image already has it) | The board's host Python lacks the package | Accepted |

---

## 3 Limits by design

### 3.1 Protocols and commanding

| ID | Limit | Consequence and handling | Status |
|---|---|---|---|
| L-01 | The CLCW's FARM-B counter has 2 bits | Bypass (BD / BC) frames are confirmed by counting increments modulo 4: with 4 or more bypass frames accepted between two CLCWs the ground cannot tell how many arrived (`dssq/fop.py`) | Accepted |
| L-02 | CMDVER command source 1 means "BD frame or hardline" | A hardline command with the same opcode as an open RF BD command cannot be told apart; it is matched to the oldest open BD record | Accepted |
| L-03 | FOP-1 radiates no AD frame while an Unlock / Set V(R) directive waits for its uplink window or its confirmation | AD commands can wait up to one T1 period after a directive | Accepted |
| L-04 | Maximum TC frame 64 octets | With USLP TC framing + SDLS AE a TC packet may be at most 23 octets: `SEQLOAD` and `MAGCAL` (26 octets) need `[link] tc_framing = "TC"`; the GDS refuses them before queueing | Accepted |
| L-05 | SDLS TC sequence number is 32 bits; SDLS TM epoch is 16 bits (1..65534) | At either limit the ground / EGSE stops and asks for a new key. The epoch advances at every MCU boot, every EGSE start and every unanswered offer, so the 65 534 values are used faster than boots | Accepted |
| L-06 | GCM nonce safety rests on two counter files that must never go back: the EGSE's `boot_count.txt` (UNO Q) and the ground's `sdls_sn.txt` | Restoring an old copy, re-imaging the UNO Q's Linux side or editing them can reuse a nonce under the same key: generate a new key after any such event ([17_Security_Model.md](17_Security_Model.md)). A missing or corrupt ground file is not guessed: the uplink is held until an HK reports the spacecraft's `sdls_last_sn`, then continues on the time floor, 4 numbers per second since 2026-01-01, above every number the key can have used while the station clock was right (L-13, D-65) | Mitigated (atomic writes, never lowered, flock on the ground file, hold and re-synchronisation of an unknown ground counter) |
| L-07 | The EGSE assumes exactly one EGSE process per board | Two processes would issue epochs from the same counter file; App Lab runs one App instance | Accepted |
| L-08 | Directory fsync after an atomic rename is best effort (`_fsync_dir`) | On a file system that refuses to open a directory the rename stays atomic but a power cut right after may lose it. The boards' eMMC (ext4) supports it | Accepted |
| L-09 | CFDP class 1 (unacknowledged) | No retransmission: an INCOMPLETE file (gaps listed, partial saved) must be requested again; ABANDONED after 600 s without a PDU | Accepted |
| L-10 | EGSE-relayed CFDP segments <= 96 octets; the ground accepts files up to 64 MiB (`[cfdp] max_file_mib`) | Fits the Router Bridge's 256-octet inbound buffer | Accepted |
| L-11 | The RPC mailbox answers "not done" when the loop cannot take a request in time (station `rf_cfg` returns -2 "MCU busy: the loop did not take the request within its 1.5 s wait, nothing changed"); the request is never executed later | The ground retries (`dssq/radio/sx126x.py` documents 1 applied / 2 queued / -1 out of range / -2 MCU busy) | Accepted |
| L-12 | A CRC-good LoRa packet longer than one CADU (236 octets) reaches the GDS with an empty payload and is counted, never decoded; the backend refuses any `rf_rx` payload above `RF_RX_MAX_OCTETS` = 236 (`dssq/radio/sx126x.py`; a 237-octet payload is rejected in `tests/test_radio.py`) | Harmless: such a packet cannot be a CADU | Accepted |
| L-13 | An unknown ground TC counter (`sdls_sn.txt` missing or corrupt, with TC AUTH or AE) is released by an HK the station cannot fully trust (at a station whose TM service is NONE any HK ends the hold; with TM AE an old protected HK replayed to a fresh archive, which has no TM replay state, is accepted once), so it is re-synchronised onto the **time floor** - 4 numbers per second of station clock since 2026-01-01, independent of the reported `sdls_last_sn` - and its safety rests on the station clock instead (`dssq/ccsds/sdls.py` `observe_spacecraft_sn()`, `sn_time_floor()`, D-65) | Uplink held (YELLOW `TC uplink BLOCKED`, `dssq.cltu` refuses) until that HK, also at the first start with a new key; an HK made within 120 s of a flight boot that reports 0 never ends the hold (a new key's release waits up to 2 min after a flight boot, with a WARN at most once a minute; a spacecraft that resets more often keeps the station held until the floor is written by hand); an unprotected report above the floor is refused (ERROR), so a forged HK at a TM NONE station can only delay a release. A replayed or forged HK reporting a lower number still lands on the floor; a higher one only uses up number space. Residuals: a release with the station clock behind an earlier release's clock by more than (numbers used since) / 4 s can reuse numbers (keep the clock right, NTP); with a clock before 2026-01-01 only `sdls_last_sn` + 1 000 000 protects (WARN), and an unprotected (TM NONE) report then decides where the counter continues; in the first 5 s after a release up to 5 numbers may run ahead of the floor; a target above 4 227 858 432 (`SDLS_SN_RELEASE_MAX`) is refused and the uplink stays held until a new key - for every release from 2059-06-30; `dssq.cltu` refuses while the SN file holds a value above the floor (`tests/test_sdls.py::test_a_replayed_or_forged_hk_cannot_cause_reuse`, `test_resync_fallbacks_and_limits`, `test_hardline_never_outruns_the_time_floor`; `tests/test_end_to_end.py::test_the_boot_hk_before_the_restore_never_ends_the_hold`, `test_a_restored_report_soon_after_a_boot_ends_the_hold`, `test_a_forged_hk_cannot_place_the_counter_at_a_tm_none_station`; [17_Security_Model.md](17_Security_Model.md) section 7.6, R15) | Mitigated |

### 3.2 Radio and link

| ID | Limit | Consequence | Status |
|---|---|---|---|
| L-20 | The E22 UART modules hide spreading factor, bandwidth, SNR, frequency error, CAD and Rx boosted gain, and drop LoRa-CRC failures inside the module | No measured SNR / Doppler / RS rescue with the E22; these need the SX126x option | Accepted |
| L-21 | E22 air-rate codes 0, 1, 2 are all 2.4 kbit/s | No slower, more sensitive E22 setting; the SX126x profiles go to SF12 / 31.25 kHz | Accepted |
| L-22 | E22-400T37S has no power levels (always 37 dBm) and needs <= 50 % transmit duty | `TXPWR` is ignored on the T37S; the flight caps duty at 50 % for every model | Accepted |
| L-23 | Half-duplex: the station transmits only in the quiet window after each received CADU, at most 2 FOP-1 CLTUs (and 2 external TC frames) per window and only what fits the window's air time (the shortest quiet window of any mode less 0.2 s, counted from the frame's ERT + 0.1 s, so a window handled late or in a batch gets only what is left and a frame whose quiet window has passed carries nothing; the station identification is booked first when it is due). Windows open at most once per 1.4 s of frame ERT (`WINDOW_MIN_SPACING_S`, the bound behind the SDLS time floor) | Uplink throughput is tied to the downlink frame period; a command that does not fit waits for the next window. A frame whose ERT comes less than 1.4 s after the previous window's (genuine frames are at least 1.5 s apart, but reception jitter can bring two closer) loses its window | Accepted |
| L-24 | Slow SX126x profiles carry less than the routine telemetry load | Packet periods are stretched at run time (EVR 0x0215); e.g. profile 1 in SAFE with USLP + AE: 17.3 s per CADU, capacity 132 octets/min, periods x 2.47 (host test `downlink_load`) | Accepted |
| L-25 | The station's CAD listen-before-talk gives up after 2 s busy and transmits anyway (`cad_forced`) | Mirrors the E22's LBT limit; may transmit over another user | Open (T-RF-10, operational review) |
| L-26 | No true ranging (the SX126x has no ranging; 1 ms of timing = 150 km), no uplink Doppler pre-compensation, no delta-DOR or carrier-phase tracking | Roadmap: [11_Standards_Status_and_Roadmap.md](11_Standards_Status_and_Roadmap.md) | Accepted |
| L-27 | Profiles narrower than 62.5 kHz need a TCXO at both ends (frequency-offset tolerance at SF12 / 31.25 kHz is +/-7.8 kHz, 18 ppm) | Packet loss with crystal references | Accepted |
| L-28 | The periodic station identification normally goes in an uplink window: it falls due max(60 s, 3 frame periods) early (at most 600 s), so two lost downlink frames do not delay it. With three or more frames lost, or a fade shorter than the LOS timeout, the housekeeping sends it without a window 600 s - 2 s - its own air time after the last one (`dssq/gds.py` `_ident_margins()`, `_id_overdue()`) | The 10-minute interval holds, but that identification may collide with one downlink frame, which may then be lost. The long early margin of the slow SX126x profiles brings it more often: at profile 0 about every 184 to 323 s, 5.8 to 8.4 s on air each (16- to 36-octet text) (`tests/test_station_id.py::test_periodic_id_keeps_10_minutes_when_frames_are_lost`) | Accepted |

### 3.3 Spacecraft software

| ID | Limit | Consequence | Status |
|---|---|---|---|
| L-30 | Stored command sequences and MAGCAL coefficients live in RAM only | Empty after every MCU boot (EVR 0x0004 says so); reload them | Accepted |
| L-31 | The solid-state recorder is RAM: contents are lost on reset; when full it overwrites the oldest packets (256 KiB is about 12 h of CRUISE) | The EGSE's CADU archive on the UNO Q is the permanent record | Accepted |
| L-32 | Build switches (SX126x radio, USLP downlink, SDLS services, callsign) are `#define`s: App Lab compiles only the sketch's default profile and a profile cannot carry build flags | Edit `config.h` (or the station sketch) and rebuild. Only the E22 model (T33D / T37S) switches without an edit, by the A0 strap | Accepted |
| L-33 | Serial / Monitor output is compiled out of flight builds (`VGQ_DEBUG_MONITOR = 0`) because a Monitor write can block the loop | Debug output only on the bench; operators use EVRs | Accepted |
| L-34 | `Bridge.notify()` allocates transiently inside the library | A documented exception to "no heap after initialisation" (bounded, inside the 32 KiB reserve; V-23) | Mitigated |
| L-35 | Beacon policy (conflict register C-20, listed for owner review): URGENT also on avionics over-temperature, command loss and FARM lockout (beyond the contract's list); SAFE after every boot and SSR overwrite drops also raise URGENT, latched until the next accepted command | URGENT is normal after a boot and after about 12 h of CRUISE without playback or `SSRCLEAR` | Open (owner decision) |
| L-36 | `SEQLOAD` absolute times are limited to 7 days ahead; `MAGCAL` is refused unless the matrix is positive definite | Stricter than the contract, by design | Accepted |
| L-37 | TRIAD uses fixed site constants `kGeomagInclDeg` = 60 deg, `kGeomagDeclDeg` = 0 deg (`config.h`) | Set them for your site (WMM) and rebuild, or the TRIAD attitude is biased | Accepted |
| L-38 | The independent hardware watchdog (IWDG) is armed at the end of `setup()` with a 40 s timeout (`config.h` `VGQ_IWDG`, `kIwdgTimeoutMs`) and refreshed once per `loop()` pass, on the loop thread only. The Zephyr watchdog API is not available to a sketch (the platform is built without `CONFIG_WATCHDOG`: no IWDG device object, no `wdt_*` symbol), so the flight writes the IWDG registers directly, as it reads `RCC_CSR`. A hang during `setup()` (before the watchdog starts) is not covered | A loop pass that never ends resets the MCU after 40 s: HK `reset_cause` 3 WATCHDOG, EVR 0x0001, beacon URGENT. If the settings are not confirmed at start-up the flight reports EVR 0x060C. Not yet provoked on the hardware (V-28, test T-FD-09) | Mitigated |

### 3.4 Ground station

| ID | Limit | Consequence | Status |
|---|---|---|---|
| L-40 | The web UI is plain HTTP. Every GET (telemetry, files, exports, `/metrics`) is readable by anyone who reaches port 8080; only POSTs (commanding) need the token, which crosses the LAN in clear | Keep the station on a trusted network; host mode can bind to 127.0.0.1 ([17_Security_Model.md](17_Security_Model.md)) | Accepted |
| L-41 | Pure-Python AES table look-ups are not constant-time | Cache-timing attacks need code on the station; the threat model is an RF adversary | Accepted |
| L-42 | Retention deletes the oldest day folders and rows (`archive_keep_days` 180, `archive_max_gb` 16); below `archive_min_free_mb` (2048) the raw CADU file and `rx_stats` pause | Old raw data is gone by design; a gap in the raw products while the disk is full (frames, packets, events keep recording) | Accepted |
| L-43 | The App Lab App and the host-mode service must never run together | The Router Bridge lets one Linux client provide a method name | Accepted |
| L-44 | The UDP telecommand listener `[interop] tc_frames_udp_listen` has no authentication and its frames skip the hazardous-command CONFIRM, so it listens on a loopback address only: `127.x.y.z:port` or `[::1]:port` (`dssq/config.py parse_udp_listen()`, checked again before the listener starts). Host names (also `localhost`), `0.0.0.0`, `[::]` and LAN addresses are refused at start as a configuration error (host mode: exit status 2) | An external mission control system must run on the station host itself; there is no opt-in for a LAN bind. Host-tested (`tests/test_radio.py`); on the board: T-GS-08 | Accepted |

### 3.5 Bundled third-party libraries (residual risks)

Both libraries are bundled as exact upstream releases plus small documented patches
([22_Bundled_Libraries.md](22_Bundled_Libraries.md)). What remains:

| ID | Residual risk | Protection in place | Status |
|---|---|---|---|
| B-01 | RadioLib 7.8.1: inside `begin()`, after the full calibration, `config()` waits on BUSY without a timeout (library internal) | Before `begin()` the driver pulses NRST with bounded waits and reads the chip's version string (a missing chip fails in about 0.1 s); faults retry with a back-off from 0.1 s to 60 s. Fault injection: T-RF-08 | Mitigated |
| B-02 | Arduino_RouterBridge 0.4.3: stack margin of the patched 1536-octet thread not yet shown on hardware (V-21) | 42 % margin over the measured worst case; the sketches' RPC mailbox keeps every handler on the loop stack even with the stock library (defence in depth) | Mitigated |
| B-03 | The bundled RouterBridge 0.4.3 replaces the platform's copy, but its dependencies (Arduino_RPClite 0.3.1, MsgPack 0.4.2) still come from the installed `arduino:zephyr` platform | A platform update can change them under the patched library: re-audit and re-run every build when updating ([22_Bundled_Libraries.md](22_Bundled_Libraries.md)); CI fails if the compile log does not name the bundled copy | Mitigated |
| B-04 | A `Monitor` / `Serial` write on the UNO Q can block (a blocking RPC while the monitor is not connected; no timeout in the library) | Compiled out of flight builds (L-33) | Mitigated |

---

## 4 Science limits (what a hobby-grade instrument set really gives)

| ID | Limit |
|---|---|
| S-01 | The AS7265X gives 18 numbers (410-940 nm, about 20 nm FWHM, +/-10 nm centre tolerance, +/-12 % calibrated accuracy, die temperature +/-8.5 degC). Everything else is ground maths and is labelled: reflectance and band ratios (good), NDVI / GNDVI / NDRE (reasonable), XYZ / CCT / Duv "approximate (18-band)", WBI and PRI "proxy" (WBI needs 970 nm; PRI uses 535/560 instead of 531/570 nm), irradiance / PPFD / lux "estimate". It is not a laboratory spectrometer |
| S-02 | The light-source classifier (flight and ground) is a heuristic with named thresholds |
| S-03 | SparkFun AS7265X board: first-generation ICs with INT and RST swapped, so INT is not wired and DATA_RDY is polled over I2C |
| S-04 | Lamp currents are limited (white codes 0-3, IR 0-2 = <= 65 mA, UV 0 = <= 30 mA); other values are rejected on both ends |
| S-05 | BME688 IAQ needs the BSEC calibration period; humidity arrives in whole percent (BHI260 virtual sensor 130) |
| S-06 | The MAG packet stays raw: MAGCAL coefficients are applied to the attitude (TRIAD) path only |

---

## 5 Regulatory and operational items

| ID | Item | Status |
|---|---|---|
| R-01 | The default SDLS mode is AE (encryption). Encryption that hides the meaning of messages is generally not permitted on amateur frequencies: use AUTH (commands authenticated, content readable) or NONE there ([09_Regulatory_and_Safety.md](09_Regulatory_and_Safety.md)) | Accepted (owner's choice, documented) |
| R-02 | The ground transmitter identifies itself on the uplink with `DE <callsign> <name>` in plain ASCII (`station.toml [station] callsign`, `dssq/gds.py StationIdent`): right after the first uplink of a run, at least every 10 minutes while it transmits (in an uplink window, or from the housekeeping when no window carried it in time: L-28), once when the uplink has been idle for 120 s (or the FOP-1 retransmission timer T1, if longer), and at shutdown if a transmission is still uncovered ([09_Regulatory_and_Safety.md](09_Regulatory_and_Safety.md) section 1.3). The spacecraft counts each such packet in RF `cltu_bad` and ignores it. Host-tested only; on air: T-TC-07 (V-39) | Mitigated (set the callsign, R-03) |
| R-03 | The default callsign is `N0CALL` at both ends (`config.h VGQ_CALLSIGN`, `station.toml [station] callsign`; the station warns at start) | Set yours before transmitting |
| R-04 | 2 W (T33D), 5 W (T37S) and up to 33 dBm (SX126x option) need an amateur licence; licence-exempt 433 MHz allowances are milliwatts | Accepted |

---

## 6 Corrections to earlier figures (code wins)

Earlier texts (the v3 contract, chat explanations) disagreed with the final code here; the
documentation uses the code's values.

| Earlier figure | Code value | Source |
|---|---|---|
| SX126x profile 0 sensitivity -143.0 dBm | -143.1 dBm (computed, the formula's -143.05 rounded) | `dssq/link.py`, conflict register C-14 |
| SX126x profile 7 sensitivity -124.5 dBm (computed) | -124.0 dBm (datasheet Table 3-8) | C-14 |
| AS7265X burst 2.78 ms per bank at `kSpecBurstCycles` 1 | 5.56 ms per bank ((1 + 1) x 2.78 ms) | `fsw/spectral.h`, C-15 |
| "7 hazardous commands" | 8: AIRRATE, SSRCLEAR, REBOOT, CMDLOSS, FDIRMASK, TXINHIBIT, HIBERNATE, CHANNEL | `dssq/dictionary.py` |
| SX126x profile 1 "about 13 s" per CADU | 17.3 s per 236-octet CADU (Semtech air-time formula incl. preamble and header) | `dssq/link.py airtime_s` |
| Rice "a reference sample every 128 samples" | a reference sample every r = 128 **blocks** of 16 samples (2048 samples) | `ccsds/rice.h` |
| "About 120 octets/s of data at 2.4 kbit/s" | 158 octets of packets per CADU; with the 50 % duty cap at most about 62 octets/s (ENCOUNTER), 31 octets/s (SAFE) | `fsw/datalink.cpp frame_period_ms()` |
| RouterBridge stock header "fails 3 of 12 checks" (commit 5b06a04) | the Phase D harness makes the stock tag fail 4 of 12 | `tools/host_test/routerbridge/run.sh` |
