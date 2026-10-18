# v4 branch contract - UNO Q ground station, separate sensor buses, 6-DOF IMU, legitimate web UI

Branch: `claude/unoq-ground-station` in /home/user/Benchmarks (created from 13a60c5, the v1 head).
The original branch `claude/wonderful-hamilton-dzjfby` is NEVER modified. The new branch holds the
WHOLE project (spacecraft, Nicla firmware, ground station, docs, tests, tools, CI, dist zips).

Inventory reports (read the sections you need; they cite file:line for everything):
- R1 ground port:      scratchpad/v4/reports/R1_ground_port.md
- R2 docs:             scratchpad/v4/reports/R2_docs.md
- R3 sensors / 6-DOF:  scratchpad/v4/reports/R3_sensors.md
- R4 web UI audit:     scratchpad/v4/reports/R4_webui.md
- R5 T37S / TBH-02:    scratchpad/v4/reports/R5_t37s.md
- R6 datasheets:       scratchpad/v4/reports/R6_datasheets.md
(scratchpad = /tmp/claude-0/-home-user/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad)

## 0 Ground rules (all workstreams)

* Facts only. Every number comes from a datasheet, the code, or a measurement; unknowns are marked
  VERIFY with a test ID. Never invent a datasheet value.
* New design decisions are recorded as D-71..D-81 (section 9) in docs/12; code comments cite them as
  "D-7x". Do NOT write the bare form "contract N" in new comments (tests/test_citations.py reads it as
  a v3 tag).
* No AI model names or identifiers anywhere in code, docs, commits or zips.
* SDLS key files stay git-ignored and never enter a commit or a zip.
* Keep the project's style: comment density, naming, units in names, sentinels, big-endian packets,
  docs tone (plain, factual, tables).
* The name "Voyager" disappears from code and docs; use "Interstellar probe" (e.g. "an Interstellar
  probe's command-loss protection"). Keep citations findable by series/chapter/section without the
  name (e.g. "J. Taylor (ed.), Deep Space Communications, JPL 2014, ch. 3, 3.7.3.2"). Do not rename
  VGQ-1.
* VENTUNO Q disappears from all current code, UI, zips, instructions and current-state docs. Factual
  history keeps its truth: CHANGELOG entries of earlier phases and the completed test record
  2026-10-13-A stay as recorded, under a clear label "earlier ground hardware (VENTUNO Q, v1 branch)".
* Checks that must pass before any hand-off: ruff, mypy (both runs), pytest (all), host tests (strict
  flags), sanitizer host build, check_firmware.sh, station harness, real App-Lab builds (flight on
  rel090/rel100/main with the loader symbol check; station sketch on rel090 for unoq with the loader
  symbol check), packaging + SHA256SUMS, docs tests.

## 1 Ground station hardware (D-71, D-72, D-78)

* Board: Arduino UNO Q 4 GB, SKU ABX00173 (4 GB LPDDR4X, 32 GB eMMC; same user manual as ABX00162).
  Debian Linux + App Lab on the board; the ground App runs exactly as on the spacecraft board.
* USB: one USB-C port (role switching). The owner plugs a USB-C hub/dongle WITH an external
  power-delivery input (Arduino: "USB-C dongle with external power delivery capabilities"). The PD
  charger powers the dongle and, through it, the UNO Q (5 V / 3 A contract only). The E15-USB-T2 +
  E22-400T33D goes into a hub USB-A port and appears as /dev/ttyUSB0 (CP2102). Prefer the stable path
  /dev/serial/by-id/... in station.toml.
* Power facts: E22-400T33D TX 0.85 / 1.0 / 1.2 A, >= 4.7 V for full power; a hub port's current is
  not specified -> receiving works from any port; transmitting (uplink, identification) needs a
  powered port able to source >= 1.5 A at >= 4.75 V, or the E22 on its own 5 V supply (USB-UART
  adapter option). VERIFY V-69 (re-scoped to the UNO Q + hub). Alternative board power: VIN 7-24 V
  (e.g. 12 V field battery) - then the dongle must still have its own PD supply for its ports.
* First set-up needs a direct USB-C link to the PC (App Lab); afterwards App Lab reaches the board
  over Wi-Fi ("Run at startup" on). Give the two boards distinct names.
* Ground MCU (STM32U585) pin plan "H" (D-72), FQBN arduino:zephyr:unoq:
  - RCU (option, [gds] rcu = true): M0 = D2, M1 = D3, AUX = D7 (identical to the spacecraft E22 pins)
  - Lamps: AOS = D4, ALARM = D5, UPLINK = D6 (330 ohm series); buzzer = A1 via NPN (A0 left free:
    A0 is the radio-model strap on the SPACECRAFT only)
  - CardKB: header SDA/SCL (Wire = I2C2, D20 = PB11 SDA, D21 = PB10 SCL; on the ground no UART uses
    these pins)
  - SX126x option: the same pins as the spacecraft SX126x option (D8/D9/D10-D13 per config).
  - LED matrix (13 x 8): ground link status (D-78), driven from Linux through the existing Bridge
    lamp path once per second: row 0 AOS (lit) / LOS (off), row 1 frame-sync LOCK (lit) /
    FLYWHEEL (blink), row 2 uplink TX (lit while transmitting), rows 3-4 RSSI bar (0..13 columns =
    -135..-30 dBm, blank when no packet in the last LOS timeout, all columns blinking on OVERLOAD),
    row 5 margin state (0..13 columns = margin 0..+26 dB against the worst-case sensitivity; blink
    when "at noise floor / not measurable"), row 6 frames lost in the last 10 min (0..13 columns),
    row 7 alarm level (GREEN 1 column, YELLOW 5, RED 13 blinking) + station clock unsynchronised
    (last column blinking). Use Arduino_LED_Matrix as the flight does; must pass the 0.90.0 loader
    symbol check; if it cannot, drop the matrix and say so.
* Station sketch must use k_sched_current_thread_query() instead of k_current_get() (0.90.0 loader).
* RCU state (D-78): the hello line no longer hard-codes rcu=1. The MCU reports the result and age of
  its last rcu_mode() call; the UI shows "RCU: enabled/disabled in station.toml" and "last mode
  change: OK / FAILED at <time> / never".
* Archive defaults for the 32 GB eMMC (D-80): archive_max_gb = 8, archive_min_free_mb = 2048
  (VERIFY free space after Debian + App Lab: `df -h /home`).

## 2 Ground code port (D-71, D-79)  - details in R1

Everything in R1's change list: sketch.yaml FQBN unoq; app.yaml; host/ scripts (Debian, not
Ubuntu); gds.py / frontpanel.py / radioctl.py / radio/e22.py / radio/sx126x.py / aes_gcm.py /
config.py / station.toml / dashboard.html wording; package_apps.py AppSpec (zip
dist/dssq-groundstation-unoq.zip, board unoq); CI (station sketch, both radio options, built on
0.90.0 with the loader check; remove every ventunoq target and baseline); check_firmware.sh;
station harness kernel stub (k_sched_current_thread_query); tests (test_packaging, test_web,
test_logger, test_docs, test_citations as needed); RadioLib patch hunk 1 (VENTUNO Q platform
entry) removed (D-79) with a new negative control in check_firmware.sh and the PROVENANCE patch
hash updated; App README absolute links point at blob/claude/unoq-ground-station/.

## 3 Spacecraft sensor buses (D-73, D-74)  - details in R3 sections 3-4

* Roles: NICLA bus = Wire2 = I2C3 on header A4 (SDA, PC1) / A5 (SCL, PC0), with the existing bus
  clear; Nicla Sense ME 0x2A, and the AS7265X 0x49 recommended here. MAG bus = Wire1 = I2C4 on the
  Qwiic socket: RM3100 outboard 0x20 and optional inboard 0x23 ALONE (the long boom cable is
  isolated from the attitude reference).
* Robustness: each instrument is probed on its preferred bus first and on the other bus second
  (AS7265X: A4/A5 then Qwiic, as today; Nicla: A4/A5 then Qwiic; RM3100: Qwiic then A4/A5). A device
  found only on the non-preferred bus is USED, with a WARN EVR "<device> found on <bus>; the
  recommended bus is <bus> (docs/03 ...)". The acquisition EVR names the bus ("instrument acquired:
  %s on %s"). HK layout unchanged (i2c_errors stays the sum); EVRs name the bus.
* FDIR: one recovery block per bus with its own rate limiter; EVR 0x0605/0x060A name the bus; the
  single FDIR_I2C_BUS flag stays (no free FDIR bit).
* Qwiic bus clear: implement through the Zephyr GPIO specs of the i2c4 node (scl-gpios / sda-gpios)
  only if it passes the loader symbol check on 0.90.0 and 1.0.0/main; otherwise controller re-init
  only, documented as a limitation.
* RM3100 loss detection: N = 5 consecutive failed samples -> clear its HK sensor bit, mark not ok,
  WARN EVR "RM3100 <ob|ib> stopped responding"; re-probe like the AS7265X; the acquisition EVR
  reports recovery. Pick free EVR IDs in 0x06xx, register them in the ground dictionary and docs/05.
* ESLOV (D-74): the ARU firmware's I2C target (Wire, 0x2A) is the bus the ESLOV connector carries
  (Arduino_BHY2 EslovHandler uses Wire; Nicla Wire = P0_22 SDA / P0_23 SCL = J2 pins 1/2 = J5 pins
  4/3). No firmware change for ESLOV. Wiring (4 of 5 wires): J5-1 5V -> the spacecraft's 5 V rail
  (see docs/03 power: the 5 V bus of configuration A, or JANALOG 5 V when the UNO Q is USB-powered),
  J5-3 SCL -> A5, J5-4 SDA -> A4, J5-5 GND -> GND; J5-2 INT not connected. Pull-ups 4.7 kOhm to
  +3V3 on A4/A5 unless the AS7265X board's I2C-jumper pull-ups are on this bus. VERIFY T-SC-16
  (continuity J5-3/J2-2 and J5-4/J2-1; idle level 3.2-3.3 V).

### 3.1 Owner's actual parts (addendum, overrides section 3 where they differ)

The owner's RM3100 breakout has bare pins (no Qwiic or ESLOV connector) and the AS7265X (SparkFun
Triad) is plugged into the UNO Q's Qwiic socket. The documented layout for these parts:
* Nicla Sense ME: ESLOV loose wires -> A4 (SDA), A5 (SCL), 5 V, GND (NICLA bus, Wire2).
* AS7265X: stays on the Qwiic socket with its Qwiic cable (MAG bus side, Wire1). The AS7265X has
  NO preferred bus: found on either bus it is used WITHOUT a warning.
* RM3100 outboard (and optional inboard): bare wires to the SparkFun AS7265X board's 4-pin I2C row
  GND / 3.3V / SDA / SCL (or to its second Qwiic connector with a Qwiic-to-jumper cable) - so it is
  on the Qwiic bus (Wire1), separate from the Nicla. RM3100 breakout in I2C mode: pin 10 I2CEN ->
  3V3; outboard address 0x20: pin 4 SSN/SA0 and pin 2 SO/SA1 -> GND; inboard 0x23: both -> 3V3;
  pin 12 DVDD and 13 AVDD -> 3V3; pins 7 AVSS and 14 DVSS -> GND; pin 1 SCL, pin 3 SDA; pin 5
  DRDY not connected; pins 6, 8, 9, 11 NC. The breakout has no pull-ups; the AS7265X board's I2C
  jumper pull-ups serve the bus (keep that jumper closed). MAG bus clock 100 kHz (boom cable).
* Separation rule enforced by detection: the Nicla and the RM3100(s) must be on different buses.
  If the flight finds the Nicla and an RM3100 on the SAME bus, the WARN EVR says "not separated";
  the ground raises a YELLOW alarm reason "sensor wiring: RM3100 and Nicla not on separate buses"
  and the SPACECRAFT tab's sensor-bus row shows "not separated". The data is still used.

## 4 BHI260AP 6-DOF IMU (D-75)  - details in R3 section 5

### 4.1 ARU firmware (nicla_sense_me_aru.ino)
* Add page 2 "IMU 6-DOF", magic 0xA9, 32 octets big-endian, CRC-8 poly 0x07 init 0 over 0..30, host
  writes 0x02 then reads 32. Layout exactly R3 5.5:
  0 magic 0xA9 | 1 seq (same g_seq as pages 0/1) | 2 flags: b0 GRV valid (present, fresh <= 200 ms,
  norm^2 > 0.25), b1 window stats valid (>= 20 accel and >= 20 gyro samples), b2 accel near full
  scale (any |raw| >= 32000), b3 gyro near full scale, b4 samples missed, b5 GRV present in the BHI
  firmware, b6 accel range read from the BHI, b7 0 | 3-10 q_grv w,x,y,z int16 Q14 | 11 win_seq u8 |
  12 n u8 (min(accel, gyro) samples) | 13-18 acc_mean x,y,z int16 mg | 19-20 vib_rms u16 0.1 mg |
  21-22 vib_peak u16 1 mg | 23-28 rate_mean x,y,z int16 0.01 deg/s | 29-30 rate_peak u16 0.1 deg/s |
  31 CRC-8.
* Algorithm R3 5.6: Fresh<> wrapper (never call clearDataAvailFlag), GRV is the 10th and last sensor
  object, 1.000 s windows of 25 Hz samples, two-pass statistics, three pages rebuilt together.
* Fix validity: gyro (page 0 b2) and temperature (page 1 b2) flags only when fresh data arrived
  (5 nominal periods); every flag clears when its sensor goes silent.
* on_receive: page = v <= 2 ? v : 0.

### 4.2 Flight
* payload.h/.cpp: struct AruImu and NiclaAru::read_imu (page 2, magic 0xA9, CRC, quaternion norm
  gate); read page 0 then page 2 immediately (pairing imu.seq == motion.seq, one retry of page 0),
  then page 1.
* attitude.h: host-testable ImuStats (add() only when win_seq changes, gaps counted, RMS of window
  RMS, max peaks, mean of window rate means, OR of saturation flags; reset in gen_att()).
* ATT v4 (APID 0x021) = 59 octets user data: offsets 0-36 unchanged; 37 imu_flags u8 (IMU_F_GRV b0,
  IMU_F_STATS b1, IMU_F_PAIRED b2, IMU_F_ACC_SAT b3, IMU_F_GYRO_SAT b4, IMU_F_GAP b5,
  IMU_F_GRV_PRESENT b6); 38-45 q_grv w,x,y,z i16 Q14 (sentinel 0x7FFF); 46 imu_nwin u8 (0 = none);
  47-48 vib_rms u16 0.1 mg (RMS of the windows' RMS; 0xFFFF); 49-50 vib_peak u16 1 mg (max;
  0xFFFF); 51-56 rate_mean x,y,z i16 0.01 deg/s (mean of the window means; 0x7FFF); 57-58 rate_peak
  u16 0.1 deg/s (max; 0xFFFF).
* SensorBit SNS_ME_GRV = 1u << 10 (GRV valid). New enum ImuFlag with the IMU_F_ prefix, contiguous
  from bit 0 (cross-check test requirement).
* Downlink load (R3 5.8): TEST 44.74 octets/s = 71.8 % (TM+AE), 74.1 % (USLP+AE): no stretch at
  2.4 kbit/s. Update docs/01 load figures.

### 4.3 Ground (R3 5.9)
* dictionary (ATT fields, IMU_FLAG_BITS, SENSOR_BITS + ME_GRV), decom, products: up vector, gravity,
  tilt / roll / pitch from q_grv (state the convention), relative heading (labelled relative,
  drifting), 9-DOF vs 6-DOF heading offset and tilt disagreement (only when IMU_F_PAIRED), vibration,
  rate vector / |omega| / spin axis (only when |omega| > 10x floor), stationary/moving/unknown from
  [science] still_rate_dps = 0.5 and still_vib_mg = 10 (VERIFY T-SC-14). GRV accuracy shown "n/a".
  Linear acceleration (acc_mean - g) shown with the +/-50 mg offset caveat.
* Dashboard SCIENCE: new "IMU (6-DOF)" panel with provenance labels (onboard / derived on ground).
* Simulator: physically consistent ATT (accel and BMM150 field rotate with the quaternion; body
  rates, not Euler rates), q_grv = slow drift (2 deg/h) of q_fus, window stats from the true rates,
  vib ~2 mg rms.
* Labels: BMM150 values in ATT are "magnetometer corrected (BSX)", not raw.

## 5 Web UI legitimacy (D-76)  - details in R4 section 9

Implement R4 P0 (F-P0-1 .. F-P0-7, with F-P0-6 as the noise-floor state logic), P1 (F-P1-1 ..
F-P1-10), P2-1, P2-2, and these additions:
* FER with an exact Clopper-Pearson 95 % interval (and the rule of three at zero losses) wherever FER
  is shown (frames received vs lost in the window).
* Station clock synchronisation state from the kernel (ntp_adjtime / adjtimex read-only query via
  ctypes: TIME_ERROR or STA_UNSYNC = unsynchronised), shown in GDS health and the header strip; TDM
  records are flagged when the clock is unsynchronised; /metrics dssq_station_clock_synced.
* Every displayed number has a real producer; anything that cannot be measured with the configured
  hardware renders "n/a (<reason>)", never a number. The drift tests (tests/test_web.py,
  tests/test_docs.py) stay green and docs/13 is updated in step.

## 6 E22-400T37S on the TBH-02 (D-77)  - details in R5

Correct, unambiguous wiring for the spacecraft UNO Q and the ground UNO Q (hub) exactly as R5
specifies; docs get ONE consolidated T37S chapter (docs/03) with the other docs pointing to it.
Code: fault_log defaults to true when [radio] model = "E22-400T37S" (config.py, station.toml,
radio/e22.py consistent); the configuration tool refuses to send any command unless the operator
confirms configuration mode (--mode2-confirmed or an interactive prompt) because in mode 0 the
octets go out on air; the 50 % duty is a project limit (not an EBYTE requirement) in all wording.

## 7 Documentation (Phase docs)  - details in R2

R2's proposed structure per file; well-defined numbered sections; per-sensor interface chapters in
docs/03 (Nicla Sense ME + ESLOV, BHI260AP 6-DOF, BMM150 / BMP390 / BME688 behind the BHI, AS7265X,
RM3100, CardKB), each: what it measures, datasheet specs (cited), interface, address, bus, pins,
wiring table, power, pull-ups, cable, software path (firmware -> packet -> ground -> UI), checks
(tests), troubleshooting. Two-board hardware chapters (spacecraft UNO Q, ground UNO Q 4 GB with the
USB-C hub). Ultra-long-range orientation: default budget and setup flow aimed at long range;
bench steps are preparation. Everything consistent with the code of this branch.

## 8 Verification

Adversarial review of the full diff, all checks of section 0, real builds, docs drift tests,
greps: no "VENTUNO" outside labelled history, no "Voyager" anywhere, no model identifiers.

## 9 Decisions to record in docs/12

D-71 Ground station on an Arduino UNO Q 4 GB (ABX00173) with a USB-C hub; VENTUNO Q retired.
D-72 Ground MCU pin plan H (E22 control pins as on the spacecraft; lamps D4-D6; buzzer A1; A0 free).
D-73 Separate sensor buses: Nicla (+ AS7265X recommended) on A4/A5, RM3100 alone on Qwiic;
     preferred-then-alternate probing with a WARN; per-bus FDIR.
D-74 Nicla Sense ME through its ESLOV connector, no firmware change, INT unused.
D-75 BHI260AP 6-DOF: ARU page 2 and ATT v4 (59 octets), ground-derived quantities, exclusions.
D-76 Web UI legitimacy rules (section 5).
D-77 E22-400T37S / TBH-02 interfacing corrected for both UNO Q ends; fault_log default; config tool
     mode-2 guard; 50 % duty is a project limit.
D-78 RCU state reported, not assumed; ground LED-matrix link status.
D-79 RadioLib patch: VENTUNO Q platform hunk removed.
D-80 Archive defaults for the 32 GB eMMC.
D-81 "Interstellar probe" replaces the former spacecraft name in all text.

## 10 Source update (owner uploads, RM3100) - for the documentation phase

Extracted (read only) under scratchpad/rm3100new/: bb.txt = PNI RM3100 Breakout Board User Manual
(Doc 1017252 r08, Dec 2022), um.txt = RM3100 & RM2100 Magneto-Inductive Magnetometer User Manual
Doc 1017252 V15.0 (supersedes the r06 cited so far), qg.txt = PNI "RM3100 Arduino Quick Guide",
0faf2a3e/ PNI RM3100-Arduino example (NUCLEO-L152RE), efd0dc64/ community RM3100-UNO sketch.
Facts: the breakout = the evaluation board without header pins installed (the owner must solder
headers/wires); Table 4-1 pinout as in section 3.1; supply 2.0-3.6 V, abs max 3.7 V, AVDD within
0.1 V of DVDD, DVDD up first or together (tie both to the same 3.3 V); no on-board I2C pull-ups
(PNI: 4.7 kOhm); SPI and I2C clocks <= 1 MHz; the printed arrow is the x axis, NED frame; keep away
from currents/ferrous parts (1/d^3). V15.0 R13 claims to fix SA0/SA1 but its text is still
self-contradictory - irrelevant for 0x20/0x23. The 147 Hz vs 430 Hz contradiction remains.
The RM3100-UNO sketch targets a 5 V Arduino UNO and cycle count 1024: do not copy its wiring
(3.7 V abs max) or its cycle count (PNI characterises 50/100/200; the project uses 200).
Cite V15.0 instead of r06 in all docs.
