# 08 - Verification and Test Plan

Verification has two layers: **automated tests** that run on any PC (and must pass before every
commit), and **hardware tests** you run once when building the system and again after changes.

## 1 Automated tests

### 1.1 How to run

```bash
# Flight protocol code compiled on the PC and checked (C++)
make -C tools/host_test && tools/host_test/host_test /tmp/vectors
# Every firmware translation unit type-checked against the Arduino API stubs
bash tools/host_test/check_firmware.sh            # LIBS_DIR=<cloned libs> also checks vendor code paths
# Ground stack, flight <-> ground cross-check, end-to-end with the software spacecraft (Python)
cd ground && .venv/bin/python -m pytest -q
# Regenerate the dictionary document after changing telemetry or commands
python3 tools/gen_dictionary_doc.py
```

### 1.2 What they prove

**Flight code on the host** (`tools/host_test/host_test.cpp`, 39 checks): CRC-16 check value;
randomizer prefix `FF 48 0E C0 9A`; RS dual-basis tables (CCSDS Annex F rows) and their
inverse; RS all-zero codeword; CLTU tail rejected as a codeblock; BCH(63,56) corrects every
single-bit error; TC frame through CLTU encode/decode; FARM-1 accept / duplicate / gap
(retransmit) / lockout / UNLOCK / SET V(R) and the CLCW; VC multiplexer CADU stream; exact
sizes of all telemetry packets (HK 58, RF 38, MAG 46, ATT 37, SPEC 114, ENV 51, RFSCAN 4+N);
command parsing and range checks; SSR heap allocation, overwrite and playback order; TRIAD
attitude recovery; SHA-256 (FIPS 180-4) and HMAC-SHA-256 (RFC 4231 cases 1 and 2); SDLS accept,
replay rejection and forged-frame rejection without advancing the replay window; FP32 Welford
magnetometer statistics within 1 nT of an FP64 reference (10 nT fluctuation on 50 uT).

**Ground and cross-check** (`ground/tests`, 32 tests):

| File | Tests |
|---|---|
| `test_ccsds.py` | CRC, randomizer prefix and period, RS against the independent `reedsolo` codec, dual-basis tables, RS corrects up to 16 symbol errors and detects 17, frame round trip with errors, packet extraction across frames and gaps, CLTU round trip + FARM, SCLK correlation fit, frame-length rejection |
| `test_cross_check.py` | **flight CADUs decode bit-exactly on the ground**; flight RS vector; flight CLTU equals the ground encoder; flight-packed telemetry decommutates to the expected values; flight-built SDLS frame verifies with Python's `hmac`; ground SDLS frame equals the flight one |
| `test_end_to_end.py` | telemetry flow through the GDS from the software spacecraft (virtual clock); command round trip through COP-1; hazardous confirmation; local rejection of bad commands; SDLS-authenticated uplink; coordinated air-rate change; ground RF survey and spacecraft RFSCAN; dashboard and Prometheus metrics |
| `test_radio.py` | E22-400T37S rate/power/sensitivity tables per the EBYTE manual; REG1 abnormal-status bit; frame synchronizer extracting `FF FF FF code` reports; link manager never commands power and stops at 2.4k |

## 2 Hardware tests

Record results (date, values, pass/fail) in a log. "Expected" values are from the datasheets or
this design; where a datasheet gives no value, the test *is* the measurement.

### 2.1 Power and wiring

| ID | Test | Method | Pass |
|---|---|---|---|
| T-HW-01 | Supplies | meter at each rail, no load and while transmitting | E22 >= 4.5 V during TX (EBYTE); 5 V rail 4.9-5.2 V |
| T-HW-02 | Spacecraft radio UART | boot log | `E22 config verified` (proves both UART directions and M0/M1/AUX) |
| T-HW-03 | Mode-pin rest state | meter on M0, M1 with the UNO Q unpowered | ~3.3 V (deep sleep) |
| T-HW-04 | Instruments | boot log `instruments:` line, LED-matrix row 6 | all fitted instruments `ok` |
| T-HW-05 | Station I/O | dashboard COMMANDING "Station I/O" | MCU CONNECTED, CardKB #1 present (#2 if fitted), typed text echoed |
| T-HW-06 | RCU | GDS start-up event | `ground E22 ... matches station.toml` |

### 2.2 RF (use the attenuator chain: 40 dB/10 W + 30 dB + 30 dB, or long separation)

| ID | Test | Method | Pass / record |
|---|---|---|---|
| T-RF-01 | Bench link | 100 dB coax path, SAFE mode, 30 min | 0 frames lost, 0 uncorrectable; RSSI about -63 dBm (37 - 100) |
| T-RF-02 | **RSSI calibration** | known path loss (attenuators + cable loss measured or datasheet) -> expected P_r; compare with the 10-min mean RSSI | record the difference; set `[radio] rssi_cal_offset_db` to it |
| T-RF-03 | **Sensitivity per air rate** | add attenuation in steps until FER reaches 10 %; repeat at 2.4k, 4.8k, 9.6k (`LINKRATE`) | record; 2.4k should be near the datasheet -126 dBm; enter results instead of the estimates in `link.py` |
| T-RF-04 | Noise floor | `GSCAN 0 83` at the station site; `RFSCAN 0 83` at the spacecraft site | record; choose the quietest **allowed** channel |
| T-RF-05 | Uplink | `PING 1` x 20 at bench level | 20/20 EXECUTED |
| T-RF-06 | Duty cycle and temperature | ENCOUNTER mode 1 h | RF `tx_duty` <= 50 %, no `E22 reports OVER-TEMPERATURE` |

### 2.3 Telemetry / telecommand

| ID | Test | Method | Pass |
|---|---|---|---|
| T-TM-01 | End-to-end integrity | compare spacecraft EGSE `cadu_*.bin` with the ground `cadu.bin` (`dssq.replay` on both) | identical frames for every MCFC received |
| T-TM-02 | Packet continuity | FRAMES & PACKETS tab, 1 h | no sequence gaps without matching lost frames |
| T-TM-03 | Recorder | `PLAYBACK START` after 30 min of CRUISE | playback packets arrive on VC2 in order |
| T-TM-04 | **Time correlation** | > 10 TIMECORR samples at a known distance | residual RMS < frame-period jitter; set `radio_latency_s` so the mean SCET offset vs a GPS/NTP-synchronised reference is 0 |
| T-TC-01 | Life cycle | `NOOP` | QUEUED -> RADIATED -> ACCEPTED -> EXECUTED |
| T-TC-02 | COP-1 retransmission | send 3 commands with the uplink blocked (disconnect the ground antenna path briefly) | FOP retransmits; all EXECUTED once restored, no duplicates |
| T-TC-03 | Authentication | (a) spacecraft keyed, ground `[sdls] enabled = false`; (b) both keyed with the same key; (c) both keyed with *different* keys | (a) every TC rejected by SDLS (EVR 0x011x), HK `sdls_auth_fail` rises; (b) accepted; (c) rejected `bad MAC` (EVR 0x0115) |
| T-TC-04 | Two-person rule | `require_two_person_for_hazardous = true`, `TXINHIBIT 30` | not radiated until both CONFIRM and AUTH |

### 2.4 Spacecraft instruments

| ID | Test | Method | Pass / record |
|---|---|---|---|
| T-SC-01 | Nicla Sense ME attitude | rotate the board 90 deg about each axis | fusion and TRIAD Euler angles follow; their difference (SCIENCE tab) stays a few degrees when still |
| T-SC-02 | Accelerometer | board still, each axis up | magnitude of a = 1.000 +/- 0.02 g |
| T-SC-03 | Spectrometer | cover / daylight / `SPECCFG 2 50 1` (white lamp) | spectrum changes as expected; lamp state shown |
| T-SC-04 | Nicla Sense Env | breathe near it | humidity and eCO2/TVOC rise |
| T-SC-05 | RM3100 noise | `MAGCC 200`, quiet room, 10 min | RMS fluctuation (MAG `ob_rms`) record; field magnitude matches the WMM total field for the site within a few uT (hard-iron of the boom area) |
| T-SC-06 | **BMM150 scale** | rotate the Nicla slowly through all orientations | MAG `body` field magnitude stays constant and equals the site's WMM total field; if it differs by a factor, set `kAruMagUtPerLsb` = WMM total / measured counts magnitude |
| T-SC-07 | PA-off sampling | ENCOUNTER, 1 h | MAG `flags` bit 0 never set |

### 2.5 Fault protection

| ID | Test | Method | Pass |
|---|---|---|---|
| T-FD-01 | Command loss | `CMDLOSS 120`, then send nothing | after 120 s: EVR 0x0601, SAFE, default channel/rate |
| T-FD-02 | Revert | `LINKRATE 3`, then switch the ground radio off before the confirming NOOP | spacecraft EVR 0x0602 after `revert_s`; link back on the old rate |
| T-FD-03 | Hibernate / wake | `HIBERNATE 120` | beacons every 120 s; `WAKE` restores normal frames |
| T-FD-04 | Instrument loss | unplug the Nicla Sense ME for 10 s | EVR 0x0606, SENSOR_LOST; EVR 0x0607 after reconnecting |
| T-FD-05 | Radio under-voltage | (optional, current-limited supply) lower the E22 supply below 4.5 V | EVR 0x0311, FDIR LOW_BUS_V; cleared after recovery |

### 2.6 Field

| ID | Test | Record |
|---|---|---|
| T-FLD-01 | Range | for each site pair: distance, antenna heights and types, predicted vs measured received power (RECEIVER link-budget table), excess loss, FER, margin p10 |
