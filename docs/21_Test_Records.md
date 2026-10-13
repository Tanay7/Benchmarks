# 21 - Hardware Test Records (fill-in templates)

The software of this project is verified automatically (host tests, ground tests, real builds;
[08_Verification_and_Test.md](08_Verification_and_Test.md)). What no automated test can show is
how the **hardware** behaves: real range, received power, supply currents, the magnetometer in its
real mounting, behaviour when warm. These templates are where those measured numbers go. They
also close the "VERIFY on hardware" items ([20_Known_Limitations.md](20_Known_Limitations.md),
[03_Hardware_Integration.md](03_Hardware_Integration.md) section 11).

## How to use these templates

1. Read the procedure of the test in [08_Verification_and_Test.md](08_Verification_and_Test.md)
   and the safety rules in [09_Regulatory_and_Safety.md](09_Regulatory_and_Safety.md).
2. Copy the session header (SES) and the record you need into your lab notebook or a new
   Markdown file. Fill in **every** field; write "n/a" with a reason rather than leaving one empty.
3. Keep the raw data next to the record: the receiver data can be exported as CSV from the FILES
   tab or with `python -m dssq.export` ([18_Data_Products.md](18_Data_Products.md)). Name the
   export in the record.
4. When a result changes a configuration value (for example `rssi_cal_offset_db`), write the old
   and the new value in the record and change the file in the same session.

Conventions:

| Item | Rule |
|---|---|
| Time | UTC, ISO 8601 (`2026-10-12T14:05:00Z`). The ground archive and the CSV exports use UTC |
| Power and RSSI | dBm; say whether `[radio] rssi_cal_offset_db` was already applied (the dashboard and the exports include it) |
| Averages | mean +/- standard error of the mean (SEM) and the number of samples n, with the window used (RECEIVER tab windows "1m", "10m", "pass", or the CSV interval) |
| Distances | metres or km, with the method (GPS, laser, map) |
| Uncertainty | write the instrument and its accuracy for every measured value you rely on |
| Source tags | "contract N" is a section of the project's design contract, the tag the code comments cite; the tags are indexed in [12_Design_Decisions.md](12_Design_Decisions.md) appendix B (citation tags) |

## Index

| Record | What it measures | Related tests ([08_Verification_and_Test.md](08_Verification_and_Test.md)) | Feeds |
|---|---|---|---|
| [SES](#ses---session-header) | who, when, what software and hardware | - | every record |
| [PWR](#pwr---power-draw-and-supply-voltages) | supply voltages and currents of both ends | T-HW-01 (supplies under load), T-HW-07 (board-powered radio), T-HW-08 (brown-out), T-FLD-03 (power draw in operation) | power budget, [03_Hardware_Integration.md](03_Hardware_Integration.md) section 6 |
| [RSSI](#rssi---received-power-against-attenuation-and-distance) | received power against attenuation (bench) and against distance (field) | T-RF-01 (bench link), T-RF-02 (RSSI calibration), T-FLD-02 (RSSI versus distance) | `[radio] rssi_cal_offset_db`, path-loss exponent |
| [RNG](#rng---range-test-one-site-pair) | a range test for one pair of sites | T-FLD-01 (range) | link budget validation, [02_Link_Budget_and_RF_Analysis.md](02_Link_Budget_and_RF_Analysis.md) |
| [MAG](#mag---magnetometer-self-test-noise-and-calibration) | RM3100 self-test, noise and calibration; BMM150 scale | T-SC-05 (noise), T-SC-06 (BMM150 scale), T-SC-07 (PA-off sampling), T-SC-10 (self-test), T-SC-11 (calibration) | `MAGCAL` coefficients, `[science]` |
| [TMP](#tmp---temperature-test) | behaviour when warm | T-RF-09 (duty cycle and PA temperature) | limits, [03_Hardware_Integration.md](03_Hardware_Integration.md) section 8 |
| [TXP](#txp---transmit-power-per-power-code) | transmit power per power code | T-RF-07 | `kT33dPowerDbm` (VERIFY), `[radio.sx126x] pa_gain_db` |
| [SENS](#sens---receiver-sensitivity) | receiver sensitivity per rate code | T-RF-03 | `[radio.sensitivity_measured]` |
| [IDENT](#ident---station-identification-on-air) | plain-text identification of both transmitters | T-TC-07 (station identification on air) | `VGQ_CALLSIGN`, `[station] callsign`, [09_Regulatory_and_Safety.md](09_Regulatory_and_Safety.md) section 1.3 |
| [WDG](#wdg---hardware-watchdog) | the flight MCU's hardware watchdog resets a hung loop | T-FD-09 (hardware watchdog) | closes docs/20 V-28 (L-38); not yet provoked on hardware: VERIFY |

Completed records: [2026-10-12-A](#2026-10-12-a---first-bench-run-of-the-flight-app) (first bench run of the flight App, spacecraft only);
[2026-10-13-A](#2026-10-13-a---first-downlink-received-by-the-ground-station) (first downlink received by the ground station).

---

## SES - Session header

Fill once per test session; every record refers to it by its session ID.

| Field | Value |
|---|---|
| Session ID (e.g. `2026-10-12-A`) | |
| Date and UTC start / end | |
| Operator(s) | |
| Site(s) and weather (temperature, rain, wind) | |
| Repository commit or release (zip names in `dist/`) | |
| Flight software version (EVR 0x0001 `BOOT VGQ-1 FSW ...`) | |
| Ground software version (dashboard header, e.g. `GDS 3.0.0`) | |
| Station I/O firmware (COMMANDING tab, Station I/O, e.g. `DSSQ-IO 3.1`) | |
| Spacecraft radio model (RF packet `radio_model`) and strap state (EVR 0x0308) | |
| Ground radio model (`[radio] model`) | |
| Callsigns: spacecraft `VGQ_CALLSIGN` (config.h) / ground `[station] callsign` (station.toml) | |
| Channel / rate code / power code, both ends | |
| Framing (TM / USLP) and SDLS services (TC / TM) | |
| Spacecraft power configuration (A, B, T37S branch; docs/03 section 4.2) | |
| Antennas (type, gain in dBi and its source) and feeder loss, both ends | |
| `[radio] rssi_cal_offset_db` in force | |
| Instruments used (model, serial number, calibration date, accuracy) | |
| Deviations from the procedure | |

---

## PWR - Power draw and supply voltages

**Purpose (T-HW-01, T-HW-07, T-HW-08, T-FLD-03).** Replace the design budgets of
[03_Hardware_Integration.md](03_Hardware_Integration.md) section 6 with measured values; close the power VERIFY items (UNO Q real consumption, Nicla
Sense ME current with the ARU firmware, AS7265X lamp load on the UNO Q 3.3 V rail, E22 supply
voltage under transmit in configuration B = T-HW-07, E22-400T37S current below 12 V).

**Equipment.** Bench supply with a current read-out, or a DC clamp meter / inline power meter;
a multimeter with min/max hold, or an oscilloscope for the transmit dips. Radios on dummy loads
or on the attenuator set (docs/03 section 10).

**States to measure** (how to reach them):

| State | How |
|---|---|
| S1 boot | the first 60 s after power-on |
| S2 SAFE, receiving | between two frames in SAFE mode (frame period about 5 s at 2.4 kbit/s) |
| S3 transmitting | during a frame (E22 at 2.4 kbit/s: about 1.27 s busy per frame) |
| S4 ENCOUNTER | `MODE ENCOUNTER`: highest transmit duty (50 %) and fast sensor cadence |
| S5 lamps on | `SPECCFG` with all permitted lamps at their maximum codes, e.g. `SPECCFG AUTO 59 7 11 0` (white code 3, IR code 2, UV code 0), then `SPECMODE LOCKIN 4` |

### PWR-1 Spacecraft

Power configuration: ______ (A / B / T37S branch). Supply voltage at no load: ______ V.

| Point | State | Voltage (V) min / typ | Current (A) avg | Current (A) peak | Instrument |
|---|---|---|---|---|---|
| Battery / 12 V input (A) or 5 V USB-C input (B) | S1 | | | | |
| same | S2 | | | | |
| same | S3 | | | | |
| same | S4 | | | | |
| same | S5 | | | | |
| UNO Q JANALOG pin 8 VIN (A) | S3 | | - | - | |
| E22 VCC at the module pins | S2 | | | | |
| E22 VCC at the module pins | S3 | **min:** | | | |
| UNO Q JANALOG pin 5 (B only, T-HW-07) | S3, longest transmission | **min:** | - | - | |
| UNO Q +3V3 (JANALOG pin 4) | S5 | **min:** | - | - | |
| Nicla Sense ME VIN (J2 pin 9) | S2 | | | | |
| E22-400T37S DC input (T37S branch) | S3 | **min:** | | | |

Derived:

| Quantity | Measured | Budget (docs/03 section 6) | Within budget? |
|---|---|---|---|
| Spacecraft average input power in S2 (W) | | - | |
| Spacecraft average input power in S4 (W) | | upper bound 21.9 W (configuration A) | |
| Spacecraft peak input current (A) | | 2.17 A at 12 V (configuration A) | |
| Battery run time at the S4 average, h = usable Wh / W | | - | |

Pass criteria:

| Criterion | Limit | Source | Result |
|---|---|---|---|
| E22-400T33D VCC during transmission | >= 4.7 V (full power), never above 5.5 V | T33D manual 2.1, 2.2 | |
| E22-400T37S supply during transmission | 4.5-15 V (no FF FF FF 01 / 02 report, no EVR 0x0311 / 0x0312) | T37S manual 5.7 | |
| UNO Q 3.3 V rail with all lamps on | >= 3.1 V | ABX00162 3.2 (PWR_3P3V minimum) | |
| No brown-out after the test | HK `reset_cause` not BROWNOUT, no EVR 0x0608, no YELLOW "reset cause BROWNOUT" | contract 15 | |
| Configuration B only: RSSI at the ground within 1 dB of the configuration A run | compare the 10-min means | T-HW-07 | |

### PWR-2 Ground station

| Point | State | Voltage (V) | Current (A) avg | Current (A) peak | Instrument |
|---|---|---|---|---|---|
| VENTUNO Q 12 V input | boot | | | | |
| VENTUNO Q 12 V input | GDS running, receiving | | | | |
| VENTUNO Q 12 V input | sending a command (`NOOP`) | | | | |
| E22 VCC at the module pins (+5V_LIMITED or own supply) | sending a command | **min:** | | | |
| CardKB supply (option A 3.3 V / option B 5 V) | typing | | | | |

Compare with the measured Arduino figures used in the budget (12 V: booting 17.9 W peak,
"Hello World on MPU" 7.52 W average, 13.3 W peak; ABX00181 3.4.1).

| Result summary | |
|---|---|
| Budgets confirmed / changed (what, old, new) | |
| Open issues | |
| Signed (operator, date) | |

---

## RSSI - Received power against attenuation and distance

**Purpose.** (a) On the bench: calibrate the ground's RSSI reading against a known path loss
(T-RF-02, after the bench link T-RF-01) and fill `[radio] rssi_cal_offset_db`. (b) In the field:
measure how received power falls with distance (path-loss exponent, T-FLD-02).

### RSSI-1 Bench calibration (known attenuation)

Set-up: coax set of docs/03 section 10. Measure or take from the datasheets the loss of every
attenuator, cable and adapter. Set `rssi_cal_offset_db = 0.0` before the first step, or write
the value in force here: ______ dB.

Spacecraft transmit power used: ______ dBm. Source: TXP record (measured) / datasheet (T33D code 0:
32-34 dBm, typ 33; T37S: 36-37.5 dBm, typ 37).

| Step | Total path loss (dB) = sum of attenuators + cables + adapters | Expected received power (dBm) = Ptx - loss | Measured RSSI mean (dBm), 10m window | SEM (dB) | n | Difference expected - measured (dB) |
|---|---|---|---|---|---|---|
| 1 | | | | | | |
| 2 | | | | | | |
| 3 | | | | | | |

Offset to enter: `rssi_cal_offset_db` = mean of the differences = ______ dB (old value ______ dB).
The calibration cannot be better than the uncertainty of Ptx (+/-1 dB from the datasheet unless
TXP was measured): state it: ______ dB.

### RSSI-2 Field, received power against distance

One row per position. Keep antenna heights, orientation and settings constant during the series.
Predicted received power: Pr = Ptx + Gtx - Ltx + Grx - Lrx - FSPL, with
FSPL = 20 log10(d_km) + 20 log10(f_MHz) + 32.45 dB (f = 410.125 + channel MHz;
tools/link_budget.py). The dashboard RECEIVER tab shows the same prediction when
`[spacecraft] distance_km` is set.

| # | Distance d (m) and method | Line of sight? (yes / partial / no) | Predicted Pr (dBm) | RSSI mean (dBm) | SEM (dB) | n | SNR (SX126x) (dB) | Noise floor (dBm) | Margin (dB) | FER 10 min | Excess loss = predicted - measured (dB) | Export file |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | | | | | | | | | | | | |
| 2 | | | | | | | | | | | | |
| 3 | | | | | | | | | | | | |
| 4 | | | | | | | | | | | | |
| 5 | | | | | | | | | | | | |
| 6 | | | | | | | | | | | | |

Path-loss exponent: fit RSSI = A - 10 n log10(d / 1 m) by least squares over the rows
(x = 10 log10(d / 1 m), slope = -n). Free space gives n = 2.

| Result | Value |
|---|---|
| A (dBm at 1 m, fitted) | |
| n (fitted) and its standard error | |
| Rows excluded and why | |

---

## RNG - Range test, one site pair

**Purpose (T-FLD-01).** Measure what the link really does at a distance and compare it with the
link budget ([02_Link_Budget_and_RF_Analysis.md](02_Link_Budget_and_RF_Analysis.md)). One record per
site pair and setting. The "reading the RECEIVER tab during a range test" walkthrough in
[13_Web_UI_Reference.md](13_Web_UI_Reference.md) explains every number you copy here. Plan with `python3 tools/link_budget.py ...` first and enter its prediction.

| Field | Value |
|---|---|
| Session ID (SES) | |
| Spacecraft site: name, latitude, longitude, ground height (m) | |
| Ground station site: name, latitude, longitude, ground height (m) | |
| Distance (km) and method | |
| Antenna heights above ground, spacecraft / station (m) | |
| Radio horizon 4.12 (sqrt(h1) + sqrt(h2)) km | |
| First Fresnel radius at mid-path, 17.32 sqrt(d1 d2 / (f_GHz d)) m, and the estimated clearance | |
| Obstacles on the path | |
| Antennas and gains (dBi, source), feeder losses (dB) | |
| Radio model, channel, rate code, power code (both ends) | |
| Predicted received power and margin (`tools/link_budget.py` output) | |
| `[spacecraft] distance_km` entered in station.toml | |

Measurements (one row per 10-minute block; values from the RECEIVER and SPACECRAFT tabs or the
`rf` CSV export):

| Block start (UTC) | RSSI mean +/- SEM (dBm) | SNR mean (dB, SX126x) | Noise floor (dBm) | Margin (dB) | FER (10 min) | RS symbols corrected | Uncorrectable code blocks | Frames ok / lost | Uplink: PING executed / sent | Uplink RSSI at the spacecraft (dBm) | Excess loss (dB) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| | | | | | | | | | | | |
| | | | | | | | | | | | |
| | | | | | | | | | | | |

| Result summary | |
|---|---|
| Downlink usable? (FER below 10 %) | |
| Uplink usable? (commands executed) | |
| Measured minus predicted received power (dB) and the likely reasons | |
| Recommendation (rate code, antenna, height) for the next test | |
| Export files and TDM file of the pass (`archive/tdm/`) | |
| Signed (operator, date) | |

---

## MAG - Magnetometer self-test, noise and calibration

**Purpose (T-SC-05, T-SC-06, T-SC-07, T-SC-10, T-SC-11).** Check the RM3100 units and the Nicla
Sense ME magnetometer in their final mounting, and calibrate the RM3100s (hard-iron offset and
soft-iron matrix) with MAGCAL. The MAGCAL
procedure is in [07_Operations_Procedures.md](07_Operations_Procedures.md) and starts with
`PKTRATE MAG 1`; the web MAGCAL tab is described in [13_Web_UI_Reference.md](13_Web_UI_Reference.md).

Site field (World Magnetic Model calculator, NOAA or BGS):

| Field | Value |
|---|---|
| Total field F (uT), entered as `[science] field_ut` | |
| Inclination (deg), entered as `kGeomagInclDeg` | |
| Declination (deg, east positive), entered as `kGeomagDeclDeg` | |
| Boom distances `r_outboard_m` / `r_inboard_m` (m) | |
| RM3100 cycle count in force (`MAGCC`, default 200) | |

### MAG-1 Self-test

Send `MAGBIST BOTH` (the test runs in the next PA-off window with every lamp off). Read HK
`mag_bist` and EVR 0x0910 (outboard) / 0x0911 (inboard).

| Unit | X ok | Y ok | Z ok | EVR text | Pass? |
|---|---|---|---|---|---|
| Outboard (0x20) | | | | | |
| Inboard (0x23) | | | | | |

### MAG-2 Noise and cleanliness at rest

Spacecraft still, radio transmitting normally, 10 minutes. Values from the MAG packet / SCIENCE tab.

| Unit | Mean field magnitude (uT) | Field fluctuation RMS (nT) | MAG flags b0 (sample while PA keyed) ever set? | b4 (lamp lit) ever set? |
|---|---|---|---|---|
| Outboard | | | | |
| Inboard | | | | |

Reference: the RM3100 noise is 15 nT at cycle count 200 (RM3100 manual Table 3-1). A much larger
fluctuation that follows the transmit rhythm points to the radio supply wiring (docs/03 section 9).

### MAG-3 Calibration (MAGCAL)

| Step | Value |
|---|---|
| `PKTRATE MAG 1` sent and executed (UTC) | |
| Rotation method (by hand, turntable) and duration | |
| Samples collected, outboard / inboard | |
| Sphere coverage (bins hit out of 26), outboard / inboard | |
| Fit residual RMS (uT), outboard / inboard | |
| Fit refused? Reason shown | |
| Hard-iron offset b (uT): x, y, z - outboard | |
| Hard-iron offset b (uT): x, y, z - inboard | |
| Soft-iron matrix M diagonal m11, m22, m33 - outboard | |
| Soft-iron matrix M off-diagonal m12, m13, m23 - outboard | |
| Soft-iron matrix M - inboard | |
| `MAGCAL` uplinked, CMDVER accepted, EVR 0x0912 text | |
| MAG packet flags b5 (onboard calibration active) set? | |
| Packet rate restored (`PKTRATE MAG <s>`, value) | |

Check after calibration: rotate the spacecraft slowly through many orientations.

| Quantity | Before calibration | After calibration | Target |
|---|---|---|---|
| Calibrated field magnitude spread (max - min) (uT) | | | small compared with F |
| Mean calibrated magnitude (uT) vs F | | | equal to F within the residual |

### MAG-4 Nicla Sense ME magnetometer scale (T-SC-06)

Rotate the Nicla Sense ME slowly through all orientations; read the body (BMM150) field magnitude.

| Quantity | Value |
|---|---|
| ARU banner at flashing: `mag +/-... uT` or `(fallback, range not reported)` | |
| Field magnitude min / max / mean (uT) | |
| Ratio mean / F | |
| Pass: magnitude constant and equal to F (allow for local hard-iron offsets) | |
| Action if the ratio is a constant factor: `kAruMagUtPerLsb` in config.h (developer, [16_Developer_Guide.md](16_Developer_Guide.md)) | |

| Result summary | |
|---|---|
| Coefficients in force (unit, date) | |
| Open issues | |
| Signed (operator, date) | |

---

## TMP - Temperature test

**Purpose (with T-RF-09).** See how the spacecraft behaves when warm and check the alarm and
fault-protection limits. ⚠ Safety: heat only with a **hair dryer held at a distance**, never an oven or a flame;
never heat batteries; keep the UNO Q and the VENTUNO Q within their -10 to +60 C ambient rating
(ABX00162, ABX00181 section 3.2). Heat the **Nicla Sense ME locally** to test the bus limits; it is
rated to +85 C (ABX00050). Stop at 78 C bus temperature.

Limits that should trigger (dssq/dictionary.py, config.h):

| Quantity | YELLOW | RED / FDIR |
|---|---|---|
| HK `bus_temp` (Nicla Sense ME BME688) | >= 60 C (or <= 0 C) | RED >= 75 C (or <= -20 C); FDIR AVI_OVERTEMP set at >= 75 C (`kBusTempRedC`), cleared below 70 C |
| RF `tx_duty` | >= 55 % | >= 70 % (the flight never exceeds 50 %) |
| E22-400T37S PA (module) | - | > 120 C: FF FF FF 03, EVR 0x0313, transmitter off 300 s, SAFE (`kPaCooldownS`) |

Log (one row per 2-5 minutes; start with 10 minutes at room temperature):

| UTC | Heat applied to | bus_temp (C) | Relative humidity (%) | Pressure (hPa) | Gas resistance (ohm) / IAQ | AS7265X die temperatures temp_0..2 (C) | RM3100 outboard field (uT) x / y / z | Ground RSSI (dBm) / margin (dB) | Frequency error (Hz, SX126x) | FDIR flags | Alarms / EVRs |
|---|---|---|---|---|---|---|---|---|---|---|---|
| | none (baseline) | | | | | | | | | | |
| | | | | | | | | | | | |
| | | | | | | | | | | | |
| | | | | | | | | | | | |
| | cooling | | | | | | | | | | |

What to expect (plain words): the BME688 temperature rises and the relative humidity usually
drops; the gas readings change if there are fumes; pressure barely changes with heat; the
AS7265X die temperature rises (the spectrum drifts); the RM3100 offset drifts; with the SX126x
radio the crystal drifts and the frequency error moves (the ground AFC follows it); the PA gain
drops slightly, so RSSI and margin fall by about 1 dB or less.

| Result summary | Value |
|---|---|
| Highest bus_temp reached (C) | |
| YELLOW / RED limits and FDIR AVI_OVERTEMP behaved as listed? | |
| RM3100 offset drift (nT per C), outboard | |
| AS7265X signal drift with die temperature (informational) | |
| RF effects (RSSI, margin, frequency error) | |
| Signed (operator, date) | |

---

## TXP - Transmit power per power code

**Purpose (T-RF-07).** The E22-400T33D power codes 1..3 are assumed to give 30 / 27 / 24 dBm: the
v1.05 manual's REG1 table was copied from the T30D (contract 2.1). The SX126x module's PA gain
(`kSxPaGainDb` = 11 dB) is also assumed. Measure them.

Equipment: RF power meter or spectrum analyser rated for the input level, behind a calibrated
attenuator able to take the full transmit power (>= 5 W for the T33D, >= 10 W for the T37S).
Change the code with `TXPWR <code>` (needs a working uplink) and wait one frame.

Path loss between the radio and the meter (attenuator + cable): ______ dB (source: ______).

| Model | Code | Assumed (dBm) | Meter reading (dBm) | Transmit power = reading + path loss (dBm) | Supply current while transmitting (A) | VCC while transmitting (V) |
|---|---|---|---|---|---|---|
| E22-400T33D | 0 | 33 (datasheet 32-34) | | | | |
| E22-400T33D | 1 | 30 (VERIFY) | | | | |
| E22-400T33D | 2 | 27 (VERIFY) | | | | |
| E22-400T33D | 3 | 24 (VERIFY) | | | | |
| E22-400T37S | any | 37 (datasheet 36-37.5) | | | | |
| SX126x | 0 | 22 + PA gain (VERIFY) | | | | |
| SX126x | 1 | 17 + PA gain | | | | |
| SX126x | 2 | 14 + PA gain | | | | |
| SX126x | 3 | 10 + PA gain | | | | |

Action: if the measured values differ, the tables `kT33dPowerDbm` (vgq1-spacecraft/sketch/src/drivers/radio.cpp)
and the ground model in `dssq-groundstation/python/dssq/link.py` are changed together by a developer
([16_Developer_Guide.md](16_Developer_Guide.md)); the SX126x PA gain goes into `kSxPaGainDb` (config.h)
and `[radio.sx126x] pa_gain_db` (station.toml).

| Signed (operator, date) | |
|---|---|

---

## SENS - Receiver sensitivity

**Purpose (T-RF-03).** Replace the datasheet / estimated / computed sensitivity with a measured
one. The analysis tool refuses to report a value unless the frame error rate crosses 10 %:
`python3 tools/link_budget.py sensitivity --archive <archive> --steps steps.csv --ref-dbm <Pref>`
(or `--live`). `<archive>` is the station's archive folder, under App Lab
`/home/arduino/ArduinoApps/dssq-groundstation/data/archive`.

Reference received power at 0 dB step attenuation `Pref`: ______ dBm (from RSSI-1, or measured).

Steps (also the rows of `steps.csv`: `start_utc,end_utc,atten_db,rate_code[,model]`):

| start_utc | end_utc | Step attenuation (dB) | Rate code | Model | Frames expected | Frames ok | FER | Mean RSSI (dBm) |
|---|---|---|---|---|---|---|---|---|
| | | | | | | | | |
| | | | | | | | | |
| | | | | | | | | |
| | | | | | | | | |
| | | | | | | | | |

| Result | Value |
|---|---|
| Sensitivity at FER = 10 % (dBm), tool output | |
| Datasheet / estimated / computed value for comparison (dBm) | |
| TOML line added to `[radio.sensitivity_measured]` (e.g. `"E22-400T33D:2" = -127.5`) | |
| Signed (operator, date) | |

---

## IDENT - Station identification on air

**Purpose (T-TC-07).** Show that both transmitters identify themselves in plain text at the
required times ([09_Regulatory_and_Safety.md](09_Regulatory_and_Safety.md) section 1.3) and that
the spacecraft ignores the ground's identification. Procedure: docs/08 section 9.4.

What each end sends:

| End | Text | When | Source |
|---|---|---|---|
| Spacecraft | EVR 0x0002 `DE <callsign>`; `DE <callsign> VGQ-1` in every idle (OID) frame, never encrypted | at boot and every 600 s (`kCallsignPeriodS`); OID frames whenever there is no data | config.h `VGQ_CALLSIGN` |
| Ground | `DE <callsign> <name>`, plain ASCII through the command radio path | right after the first uplink of a run; then at least every 600 s while uplink transmissions continue (sent in an uplink window up to max(60 s, 3 frame periods) early, at most 600 s, so two lost frames are tolerated; from the housekeeping outside a window when it is about to become overdue); once when the uplink has been idle for max(120 s, FOP T1) ("end"); from the housekeeping during LOS; at shutdown if the last transmission is not covered (an identification still in the outbox goes first) | `dssq/gds.py` `StationIdent`, `[station] callsign` |

Set-up: amateur configuration (TC AUTH, TM NONE) so the downlink is readable without the key.

| Field | Value |
|---|---|
| Session ID (SES) | |
| Spacecraft callsign (`VGQ_CALLSIGN`), sketch rebuilt and uploaded (UTC) | |
| Ground callsign (`[station] callsign`) and `[station] name` | |
| WARN event `[station] callsign is the placeholder N0CALL` absent after the restart? | |
| LINK & SECURITY tab OID identification text (snapshot `link.ident`) | |
| RF `cltu_bad` at the start | |

Spacecraft EVR 0x0002 (EVENTS tab):

| # | UTC | Text | Minutes since the previous one |
|---|---|---|---|
| 1 (boot) | | | - |
| 2 | | | |
| 3 | | | |

Ground identifications (IDENT events `station identification (<reason>): DE <callsign> <name>`;
uplink table row "Station identification", snapshot `link.station_id`):

| # | UTC | Reason (periodic / end) | Last command before it (UTC) | Minutes since the previous identification | RF `cltu_bad` after it |
|---|---|---|---|---|---|
| 1 | | | | - | |
| 2 | | | | | |
| 3 | | | | | |
| 4 | | | | | |
| end | | end | | | |
| shutdown | | | | | |

Pass criteria:

| Criterion | Limit | Result |
|---|---|---|
| Spacecraft EVR 0x0002 intervals | <= 10 min (600 s) | |
| Ground identification right after the first command | in the same uplink session | |
| Ground identifications while commanding (25 min, a command at least every minute) | none more than 10 min apart | |
| Closing identification | one `end` event after the uplink was idle for max(120 s, T1) | |
| Spacecraft ignores the identification | `cltu_bad` +1 per identification; HK `farm_state`, `farm_vr`, `cmd_rejected`, `tc_frames_bad` unchanged; no EVR | |
| Independent receiver (optional, spare E22 in transparent mode on the same channel, air rate, address and net ID) | the ground's identification readable as text | |
| National identification rules met (interval, format) | your licence conditions | |

| Result summary | |
|---|---|
| Open issues | |
| Signed (operator, date) | |

---

## WDG - Hardware watchdog

**Purpose (T-FD-09).** Show on the real UNO Q that the flight's independent hardware watchdog
(IWDG) resets a hung main loop. The design: armed at the end of `setup()`, refreshed once per
`loop()` pass, timeout 40 s at the nominal 32 kHz LSI (config.h `VGQ_IWDG` = 1,
`kIwdgTimeoutMs` = 40000, `kIwdgLsiHz` = 32000). It has **not yet been provoked on the hardware**
(docs/20 V-28, design limit L-38): until this record passes, it stays a **VERIFY** item. Procedure: docs/08
section 11.1.

⚠ The deliberate hang is a **bench build only**: never leave it in a build that flies or
transmits unattended. A debugger halt is not a valid test (the watchdog is frozen while the core
is halted).

| Field | Value |
|---|---|
| Session ID (SES) | |
| Normal build: EVR 0x060C `IWDG settings not confirmed in 200 ms` absent at boot? | |
| Normal build: reset cause after T-HW-10 (24 h) - no WATCHDOG reset? | |
| Bench build: hang line added to `loop()` (exact text) | |
| Bench build uploaded (UTC) | |

Hang and reset (one row per boot; the hang repeats every boot):

| # | Boot event (UTC) | EVR 0x0001 text (`BOOT ... rst=<n> <cause>`) | HK `reset_cause` | Last frame before the reset (UTC) | Planned hang time (UTC) | Time from hang to boot (s) |
|---|---|---|---|---|---|---|
| 1 | | | | - | | - |
| 2 | | | | | | |
| 3 | | | | | | |

The EGSE console also logs `flight computer boot (reset cause 3 WATCHDOG)` and issues a new
SCLK partition at each boot; the beacon goes URGENT (every boot enters SAFE mode).

Derived:

| Quantity | Value |
|---|---|
| Mean time from hang to boot (s), n | |
| Implied LSI frequency = 32000 Hz x 40 s / measured time (Hz) | |

Pass criteria:

| Criterion | Limit | Result |
|---|---|---|
| Normal build | no EVR 0x060C; no WATCHDOG reset in T-HW-10 | |
| Bench build | `reset_cause` 3 WATCHDOG after each hang | |
| Timeout | about 40 s; record the measured value (the LSI tolerance sets it) | |
| Normal build restored | uploaded again, the first criterion re-checked | |

| Result summary | |
|---|---|
| V-28 (L-38) closed? (yes / no, why) | |
| Signed (operator, date) | |

---

## Completed records

### 2026-10-12-A - First bench run of the flight App

The spacecraft alone (no ground station), from the owner's App Lab console log and photos of the
wiring. Not a complete run of any test in [08_Verification_and_Test.md](08_Verification_and_Test.md);
it closes no item on its own, and adds the observations below to the items it names.

| Field (SES) | Value |
|---|---|
| Session ID | 2026-10-12-A |
| Date and UTC start / end | 2026-10-12; EGSE start 23:12:38Z, last status box 23:14:29Z (111 s of log) |
| Operator(s) | project owner |
| Site(s) and weather | indoor bench; not recorded |
| Repository commit or release | `6cbe1a8`, `dist/vgq1-spacecraft-unoq.zip`; App Lab built it with arduino:zephyr 0.90.0 (64-octet Serial rings, D-66): 224756 octets flash (28 %), 123925 RAM (47 %) |
| Flight software version | n/a: EVR 0x0001 was raised before the EGSE was listening (observation 7) |
| Ground software version / station I/O firmware | n/a: no ground station |
| Spacecraft radio model and strap state | E22-400T33D (status box `RADIO E22-400T33D(1)`); A0 open (photos) |
| Ground radio model | n/a |
| Callsigns | not recorded |
| Channel / rate code / power code | spacecraft: ch 23 = 433.125 MHz, rate code 2, power code 0 (33 dBm); ground n/a |
| Framing and SDLS services | TM; SDLS TC NONE, TM NONE |
| Spacecraft power configuration | UNO Q from its USB-C socket (supply not recorded); E22 VCC from JANALOG pin 5 (5V), i.e. board-powered (docs/20 V-27); **no** 470-1000 uF / 100 nF at the E22, **no** 10 kOhm M0/M1 pull-ups |
| Antennas and feeder loss | not recorded (an antenna or a 50 Ohm dummy load is required, docs/00 part 8.1) |
| `rssi_cal_offset_db` in force | n/a |
| Instruments used | none: every value below is the flight software's own telemetry in the EGSE console |
| Deviations from the procedure | AS7265X on the Qwiic connector (D-13 revision), nothing on A4/A5; Nicla Sense ME and RM3100 not connected; E22 capacitors and M0/M1 pull-ups not fitted |

Wiring (photos): E22 GND -> GND, VCC -> 5V, AUX -> D7, TXD -> header pin "SDA", RXD -> header pin
"SCL", M1 -> D3, M0 -> D2, as in docs/00 part 8.3.

| # | Observation | Evidence (EGSE console) | Result | Feeds |
|---|---|---|---|---|
| 1 | Build, link and load on arduino:zephyr 0.90.0 | build sizes above; App started, no loader error | pass | the 0.90.0 fixes of the CHANGELOG (`__errno`, `_ZTH13z_tls_current`, `AppError`) |
| 2 | Flight status reaches the EGSE with 64-octet rings | status box every 10 s from 23:12:48Z, `status age core 0-1 s` | pass | D-66, docs/20 V-68 |
| 3 | Radio start-up check (configuration written and read back) | `FDIR 0x0000` (bit 0 RADIO_CFG clear in every box), `radio fault 0`, no AUX timeout | pass: both UART directions and the M0/M1/AUX wires work | docs/00 part 8.3 |
| 4 | Beacon and duty cycle | `MODE SAFE`, `BEACON URGENT(3)`; `frames` 2 -> 19 in 101 s; `period` 6108-6116 ms, `busy` 1527-1529 ms, `duty` 24.9-25.0 %; no LOOP_OVERRUN | as designed; `busy` is 20.6 % above the busy-time model (1267 ms) | docs/20 V-19 |
| 5 | CADU copies over the router (472 hex characters each) | `archived 19 CADUs {TM VC0: 8, TM VC7: 11}` = `frames` 19; `NOT FORWARDED ... CADUs 0` | pass on 0.90.0 | docs/20 V-22 |
| 6 | Board-powered radio, no bulk capacitor, code 0 at 25 % duty | SCLK 1/14 -> 1/115 s without a restart, no new boot | no reset in 111 s; supply voltage not measured | docs/20 V-27 |
| 7 | Start-up EVRs | `8 EVRs not forwarded by the MCU since its boot (+8)` once at 23:12:39Z, constant afterwards; SCLK 14 s at 23:12:48Z puts the MCU boot about 4 s before the EGSE start | the start-up EVRs did not reach the console: raised before the EGSE was listening, and 8 of them refused by the MCU's own notification limits; they are in the downlink | docs/00 section 5.3 |
| 8 | AS7265X on the Qwiic connector | `sensors 0x0080` (bit 7) | detected; its lamps not exercised (SAFE mode schedules no SPEC, docs/07 5.3.3), so only the board's red power LED was lit | D-13 revision |
| 9 | Empty A4/A5 bus | `EVR 0x060A ERROR I2C bus clear: SDA or SCL still low after 9 clocks` at 23:13:05Z and 23:14:06Z (re-probes of the absent Nicla Sense ME / RM3100) | a false bus fault: no pull-up on an empty bus; fixed by D-67. Second run (below): no EVR 0x060A | D-67 |
| 10 | Spacecraft radio noise reading | `noise` -70 to -75 dBm; -57 to -58 dBm in the second run | recorded only: absolute values are uncalibrated (T-RF-02); what changed between the runs was not recorded | docs/13 |
| 11 | Transient flags after boot | `FDIR 0x0100` (bit 8 VC_CONGEST, VC0 579 octets) in the first box only | cleared by the second box: the start-up packets queued faster than one frame per 6 s drains them | - |

Second run, the same evening (App re-imported from the zip of commit `f71475e`, D-67; hardware
unchanged as far as recorded): EGSE start 23:49:34Z, last status box 23:50:24Z (50 s of log).
Same build sizes; `RADIO E22-400T33D(1)`, `FDIR 0x0000` from the second box (0x0100 VC_CONGEST in
the first, as before), `sensors 0x0080`, `busy 1528 ms`, `period 6112 ms`, `duty 25.0 %`, `frames`
2 -> 9, CADUs archived 8, `CADUs 0` not forwarded, no restart. **No EVR 0x060A** (the first run
logged it at the first re-probe, about 30 s after boot; this run reached SCLK 55 s without it), and
`7 EVRs not forwarded` instead of 8. `noise` -57 to -58 dBm.

| Result summary | |
|---|---|
| Open issues | V-19 model error 20.6 % at code 2; RF output not seen by a receiver; noise reading 12-18 dB higher in the second run, cause not known |
| Signed (operator, date) | |

### 2026-10-13-A - First downlink received by the ground station

From the owner's App Lab console logs, terminal output and photos. The spacecraft ran as in
record 2026-10-12-A (zip of commit `f71475e`, SAFE mode, beacon URGENT). No command was sent.

| Field (SES) | Value |
|---|---|
| Session ID | 2026-10-13-A |
| Date and UTC start / end | 2026-10-13; ground App starts 00:04:51Z, 00:35:10Z and 00:43:32Z; last status 00:44:12Z |
| Operator(s) | project owner |
| Site(s) and weather | indoor bench; not recorded |
| Repository commit or release | ground: `dist/dssq-groundstation-ventunoq.zip` (contents unchanged since the Phase D packaging; station sketch 100324 octets flash, 40632 RAM, arduino:zephyr 1.0.0); spacecraft: `f71475e` |
| Ground software version | `DSS-Q ground station 3.0.0 started (applab)` |
| Spacecraft radio model and strap state | E22-400T33D, A0 open |
| Ground radio | E22-400T33D (label V2.1; product info `00 22 40 21 0a 00 00`) plugged straight onto an **EBYTE E15-USB-T2 V1.2** (CP2102 USB-UART) in a VENTUNO Q USB-A port, `/dev/ttyUSB0`; supply jumper 5V; M0/M1 set by the adapter's jumper caps; no wires to D2/D3/D4 |
| Callsigns | `N0CALL` at both ends (no licence yet; the ground did not transmit commands) |
| Channel / rate code / power code | ch 23 = 433.125 MHz, rate code 2, power code 0 at both ends |
| Framing and SDLS services | TM; SDLS NONE at both ends (no key files) |
| Antennas | an antenna on each radio, on one bench (type, gain and distance not recorded) |
| Attenuation | first link: none; then a push-button step attenuator set to 54 dB in line on one side (which side, and its power rating, not recorded) |
| `rssi_cal_offset_db` in force | 0.0 (default) |
| Instruments used | none: every value is the ground software's own reading |
| Deviations from the procedure | no Radio Control Unit (`rcu = false` from the third start); ground radio powered from USB; antennas instead of the coax set of docs/00 part 11 |

| # | Observation | Evidence | Result | Feeds |
|---|---|---|---|---|
| 1 | First start of the ground App (00:04Z) | `first start: default configuration copied to /app/data/station.toml`; `ERROR RADIO ground E22-400T33D did not answer the configuration read`; `LOS sync=SEARCH` | No radio: `rcu = true` switched D2/D3, which were not connected; the module's M0/M1 sat on the adapter's open jumper pairs, M0 = M1 = 1 = deep sleep (T33D manual 6.1), in which it does not answer | docs/14 section 2.2 |
| 2 | The App opened the USB-UART | no `radio backend unavailable` event, the serial port open (`gds.py _open_e22`) | pass: `/dev/ttyUSB0` usable inside the App container | docs/20 V-31 (closed) |
| 3 | E15-USB-T2 jumpers | EBYTE E51-TTL-500 datasheet v1.0, sections 3.6 and 5, which describe the E15-USB-T2 (CP2102): a fitted mode jumper = 0, a removed one = 1; a 3.3 V / 5 V supply jumper | The E22's configuration mode is M1 = 1, M0 = 0, i.e. one cap on the M0 pair only. Both caps removed is the E51's configuration mode but the E22's deep sleep | docs/00 section 9.2 |
| 4 | One-time configuration (`python3 -m dssq.radio.e22 --port /dev/ttyUSB0 --write --config .../station.toml`) | first try `no reply: is the module in CONFIG mode (M0 = 0, M1 = 1) and powered?`, second try, after the cap was moved, `written.`: ch 23, 433.125 MHz, 2.4k, uart 9600, sub-packet 240, `rssi_byte True`, `rssi_noise_enable True`, power code 0 (33.0 dBm, `power_verified False`) | pass; the pair whose cap gave `written.` is M0 | docs/00 section 9.2 |
| 5 | Callsign | `TAN` would stop the App: `[station] callsign` needs 3-16 letters and digits, at least one of each (`dssq/config.py`) | `N0CALL` kept until a licence; only a WARN at start | docs/09 section 1.3, docs/14 |
| 6 | First downlink, no attenuator (00:35Z start, both caps fitted, `rcu` still true) | `RX AOS` 6 s after start; 80 s: `frames=13 lost=0 RScorr=0`; `RSSI` alternating -1.0 / -256.0 dBm, `SNRest` 71 / -184 dB, `noise` -72 / -73 dBm; RED `beacon URGENT` (SAFE beacon, expected); still `did not answer the configuration read` | Frames, Reed-Solomon and telemetry decode: pass. RSSI at the end of its range: the receiver was overdriven (antennas too close) | docs/20 V-70 |
| 7 | TDM warning | `WARN TDM station clock outside 2000..2100 (NTP not synchronised?): TDM records skipped (rx_power_dbm = -256.0 outside -200..60)` | The clock was right; the -256 dBm reading was rejected. The message now names the real cause (`gds.py _tdm_add`, test `test_tdm_rejection_names_the_field_not_the_clock`) | CHANGELOG |
| 8 | Second downlink, 54 dB in line, `rcu = false` (00:43Z start) | no RADIO error; `RX AOS` 2 s after start; `RSSI` -39 / -40 dBm, `noise` -73 dBm, `SNRest` 33-34 dB; `frames` 2 -> 7, `lost=0`, `RScorr=0`; `sync` LOCK with occasional FLYWHEEL | pass. RSSI and noise uncalibrated (V-18): the -40 dBm is not yet a measured power | docs/20 V-18, V-70 |

| Result summary | |
|---|---|
| Open issues | which side the attenuator was on and its power rating; RSSI calibration (T-RF-02) with a closed coax path; the uplink (needs a licence or a fully closed coax path, and a ground supply for transmitting, V-69) |
| Signed (operator, date) | |

