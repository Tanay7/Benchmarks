# R3 - Sensor chain, bus split and BHI260AP 6-DOF

Branch inspected: `/home/user/Benchmarks`, `claude/unoq-ground-station` (= v1 head `13a60c5`), read only.
Every fact carries a citation (file:line, or datasheet table/section/page plus the line of the
extracted text). "not found" = searched in the listed sources without result. "Inference" marks
a conclusion drawn from cited facts that no source states directly. "VERIFY" = must be measured
or checked on the hardware.

## 0 Citation tags

| Tag | Path |
|---|---|
| `ARU` | `nicla_sense_me_aru/nicla_sense_me_aru.ino` |
| `PAY` / `PAYH` | `vgq1-spacecraft/sketch/src/drivers/payload.cpp` / `payload.h` |
| `I2C` / `I2CH` | `.../src/drivers/i2c_bus.cpp` / `i2c_bus.h` |
| `RM` / `RMH` | `.../src/drivers/sensors.cpp` / `sensors.h` (RM3100 driver) |
| `ASC` / `ASH` | `.../src/drivers/as7265x.cpp` / `as7265x.h` |
| `CFG` | `.../src/config.h` |
| `FL` / `FLH` | `.../src/fsw/flight.cpp` / `flight.h` |
| `TP` / `TPC` | `.../src/fsw/tlm_packets.h` / `tlm_packets.cpp` |
| `DL` / `DLH` | `.../src/fsw/datalink.cpp` / `datalink.h` |
| `ATC` / `ATH` | `.../src/fsw/attitude.cpp` / `attitude.h` |
| `DD`, `SIM`, `GDS`, `DEC` | `dssq-groundstation/python/dssq/dictionary.py`, `sim.py`, `gds.py`, `decom.py` |
| `WEB`, `DASH` | `dssq-groundstation/python/dssq/display/web.py`, `dashboard.html` |
| `STOML` | `dssq-groundstation/python/config/station.toml` |
| `XC`, `TW`, `TE2E` | `tests/test_cross_check.py`, `tests/test_web.py`, `tests/test_end_to_end.py` |
| `HTI`, `CHK`, `CI`, `GEN` | `tools/host_test/test_integration.cpp`, `tools/host_test/check_firmware.sh`, `.github/workflows/ci.yml`, `tools/gen_dictionary_doc.py` |
| `D01` ... `D21` | `docs/01_System_Design.md` ... `docs/21_Test_Records.md` |
| `OVL` | `/tmp/claude-0/-home-user-Benchmarks/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/refs/ArduinoCore-zephyr/variants/arduino_uno_q_stm32u585xx/arduino_uno_q_stm32u585xx.overlay` |
| `UCONF` | same folder, `arduino_uno_q_stm32u585xx.conf` |
| `DTS` | `.../scratchpad/refs/zephyr/boards/arduino/uno_q/arduino_uno_q-common.dtsi` |
| `ZWIRE`, `ZDIG`, `LPRJ` | `.../refs/ArduinoCore-zephyr/libraries/Wire/Wire.cpp`, `cores/arduino/wiring_digital.cpp`, `loader/prj.conf` |
| `MBV` | `.../refs/ArduinoCore-mbed/variants/NICLA/pins_arduino.h` (`MBV.h`) and `variant.cpp` (`MBV.cpp`) |
| `MBW`, `NSYS` | `.../refs/ArduinoCore-mbed/libraries/Wire/Wire.cpp`, `.../libraries/Nicla_System/src/Nicla_System.cpp` |
| `BHY2` | `/tmp/claude-0/-home-user/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/niclafw/nicla-sense-me-fw-main/Arduino_BHY2/` (`library.properties` says version 1.0.8 = the version the project pins, `docs/00` L201, `docs/10` L103) |
| `BHY2H` | `.../refs/Arduino_BHY2Host/` (1.0.9) |
| `NDS` | `.../scratchpad/ds/414d9b73-nicla_me_datasheet.txt` (ABX00050 user manual) |
| `NPIN` | `.../scratchpad/ds/cd6c13b5-Nicla_ESLOV_pinout.txt` (fragmentary full pinout, "Last update 27 Feb 2024") |
| `UDS` | `.../scratchpad/ds/4c60b196-ABX00162-datasheet.txt` (UNO Q, SKU ABX00162-ABX00173) |
| `BHI` | `.../scratchpad/ds/3f577a50-bst-bhi260ap-ds000.txt` (BST-BHI260AP-DS000-02 rev 1.1) |
| `BMM`, `BMP`, `BME` | `.../ds/407b699d-bst-bmm150-ds001.txt` (DS001-05 rev 1.4), `.../ds/6e980c1d-bst-bmp390-ds002.txt` (DS002-07 rev 1.7), `.../ds/53c89a2d-bst-bme688-ds000.txt` (DS000-03 rev 1.3) |
| `RMM` | `.../ds/f8597506-RM3100-User-Manual.txt` (PNI Doc 1017252 r06) |
| `ASD`, `ASG` | `.../ds/817df498-AS7265x_Datasheet.txt`, `.../ds/f7bee49d-AS7265X_hookup_guide.txt` |

---

## 1 Executive summary

1. **The ARU firmware's I2C target at 0x2A is reachable on the Nicla's ESLOV connector with no
   firmware change.** The ARU uses `Wire` (`ARU:148`); on the Nicla `Wire` = nRF52 pins P0_22
   (SDA) / P0_23 (SCL) (`MBV.h:62-63`, `MBV.h:120-121`), the same TWI that Arduino's own ESLOV
   handler uses (`BHY2/src/EslovHandler.cpp:23` `Wire.begin(ESLOV_DEFAULT_ADDRESS)`), and the
   pinout labels the ESLOV SCL/SDA nets P0_23/P0_22 (`NPIN:560-572`). The PMIC/LED bus is the
   other TWI, `Wire1` P0_15/P0_16 (`MBV.h:65-66`, `NSYS:38`), so it is not on ESLOV. The datasheet
   lists only two I2C, "one accessible via pin header" (`NDS:93`). The INT line (ESLOV pin 2 =
   P0_19, `MBV.h:82`, `MBV.cpp:90`) is **not needed** (the ARU never drives it: it runs
   `NICLA_STANDALONE`, `ARU:129`, and the ESLOV handler that drives INT is only started in
   `NICLA_I2C` mode, `BHY2/src/Arduino_BHY2.cpp:67-75`). Details and the two remaining VERIFY
   items (electrical identity J5 = J2, pull-ups) in section 4.
2. **Bus split recommendation (option (i)):** Nicla Sense ME on header A4/A5 (`Wire2` = I2C3,
   the only header I2C free in this project and the only bus with the GPIO bus clear), RM3100
   outboard 0x20 (+ optional inboard 0x23) alone on the Qwiic socket (`Wire1` = I2C4, the boom
   bus), AS7265X recommended on A4/A5 (keeps its existing auto-probe of both buses). Comparison
   and exact change list in section 3.5-3.6.
3. **A Qwiic bus clear is possible** although PD12/PD13 are not Arduino pin numbers
   (digital-pin-gpios has 76 entries, PD12/PD13 not among them, `OVL:276-352`): the board DTS
   gives `i2c4` `scl-gpios = <&gpiod 12 ...>` / `sda-gpios = <&gpiod 13 ...>` (`DTS:133-134`),
   so flight code can build `gpio_dt_spec`s and call the same Zephyr `gpio_pin_configure_dt()`
   the core's `pinMode()` uses (`ZDIG:23-31`); `Wire1.begin()` then re-applies the I2C pinctrl
   (`ZWIRE:62-68`). VERIFY with `tools/llext_check.py` on both cores (section 3.3).
4. **Arduino_BHY2 1.0.8 hard limit: at most 10 subscribed sensor objects, no bounds check**
   (`BHY2/src/sensors/SensorManager.h:16` `SensorClass* _sensors[10]`, `SensorManager.cpp:31-34`).
   The ARU already subscribes 9 (`ARU:60-68`, `ARU:130-138`). One more (game rotation vector)
   fills it; any 11th corrupts memory. The 6-DOF design (section 5) therefore adds exactly one
   object and derives everything else on the Nicla.
5. **Three Arduino_BHY2 pitfalls for the 6-DOF code:** `clearDataAvailFlag()` is declared `bool`
   but has no `return` (`BHY2/src/sensors/SensorClass.h:48`, undefined behaviour - do not call it);
   the BHI260AP "Sensor Status" accuracy (0..3) meta events are only printed in debug mode, never
   stored (`BHY2/src/BoschParser.cpp:85`, `:112-119`); the game rotation vector's accuracy field is
   always 0 by datasheet (`BHI:9118`, Table 89 p.121). No 6-DOF "accuracy" can be shown.
6. **Proposed ARU page 2 "IMU 6-DOF"** (magic 0xA9, 32 octets, CRC-8 like pages 0/1): game
   rotation vector + 1-second window statistics (window counter, sample count, accel mean,
   vibration RMS and peak with DC removed, gyro mean vector, peak rate). Layout in 5.5.
7. **Downlink: extend ATT to 59 octets (ATT v4) instead of a new APID.** At E22 2.4 kbit/s the
   periodic load rises from 41.99 to 44.74 octets/s in TEST (71.8 % of the 62.35 octets/s TM+AE
   capacity, 74.1 % with USLP+AE): no stretching in any mode; a separate 42-octet IMU APID would
   stretch TEST x1.02 (TM+AE) / x1.05 (USLP+AE) and loses the same-instant pairing with `q_fus`.
   Planner numbers in 5.8.
8. **Existing legitimacy defects found (item 7 of the owner's request):** (a) ARU page 0 flags the
   gyroscope always valid (`ARU:185`) and page 1 the temperature always valid (`ARU:209`), so a
   silent BHI260AP virtual sensor is reported as valid zeros (ATT rate 0.0 deg/s, ENV/HK
   temperature 0.00 C, which also defeats FDIR AVI_OVERTEMP); (b) no BHI-data freshness check at
   all - the ARU sequence counter advances from the nRF52 loop even if the BHI260AP stopped
   (`ARU:165`); (c) the HK `sensor_health` RM3100 bits are set at boot/re-probe and **never cleared**
   when a unit fails later (`FL:505`, `FL:1991`; no clearing site), while docs/13 says "found and
   working" (`D13:1985-1986`); (d) the simulator's ATT is physically inconsistent (constant
   acceleration (0,0,1) g and constant BMM150 field while the quaternion tumbles; Euler angle rates
   reported as body rates) (`SIM:1131-1150`); (e) HK `bus_temp` is the BME688 die temperature,
   which Bosch says is "typically above ambient" because of self-heating (`BME:662-663`, footnote 19
   p.14) - label it as such.
9. **Not compiled anywhere in CI:** the Nicla firmware (CI header lists it, `CI:6`, but the compile
   jobs build only the two arduino:zephyr sketches, `CI:194-278`; `CHK` has no Nicla step). Any
   page-2 code is unchecked unless a `arduino:mbed_nicla` compile job is added.

---

## 2 (a) The Nicla chain, end to end

### 2.1 Inside the Nicla Sense ME (hardware topology)

| Item | Fact | Source |
|---|---|---|
| Host MCU | nRF52832 in the ANNA-B112 module, Cortex-M4F 64 MHz, runs at 1.8 V; "a level shifter can adjust the logic level between 1.8V and 3.3V depending on the LDO set in BQ25120 (U9)" | `NDS:406` |
| BHI260AP | "6-axis IMU (gyroscope and accelerometer) together with a sensor fusion software framework"; ANNA-B112 is SPI host for the BHI260 | `NDS:409`, `NDS:406`; library boots it over SPI: `BHY2/src/BoschSensortec.cpp:17-18` |
| BMM150 | "dedicated I2C connection to the BHI260 (U2), acting as the host" | `NDS:419` |
| BMP390, BME688 | "U3 is controlled via an SPI interface to the BHI260 (U2), on the same bus as the BME688 (U6)" | `NDS:416` |
| External I2C | "2x SPI, 2x I2C (one accessible via pin header each)" | `NDS:93` |
| Level translation | "All I/O broken out to the pins are fed through a bidirectional voltage translator running at VDDIO_EXT"; J1/J2 pins "referenced to the VDDIO_EXT voltage"; VDDIO_EXT 1.8-3.3 V, software programmable | `NDS:435`, `NDS:469`, `NDS:220-244` |
| Translator parts | U10 SN74LVC1T45, U11 TXB0108YZPR, U12 / U13 NTS0304EUKZ | `NDS:361-394` |
| VDDIO_EXT = 3.3 V | `BHY2.begin()` -> `nicla::enable3V3LDO()` writes BQ25120 LDO_CTRL = 0xE4 (0.8 V + 25 x 0.1 V = 3.3 V) | `BHY2/src/Arduino_BHY2.cpp:55-57`, `NSYS:56-82` |
| PMIC bus | BQ25120A (0x6A) and the RGB LED driver on `Wire1` (P0_15/P0_16) | `NSYS:38`, `.../Nicla_System/src/BQ25120A.h:4`, `MBV.h:65-66` |

### 2.2 ARU firmware (Nicla side)

* Sensors subscribed (9): RV 34, ACC 4, GYRO 13, MAG 22, BARO 129, TEMP 128, HUM 130, GAS 131,
  BSEC 115 (`ARU:60-68`); motion at 25 Hz, environment at 1 Hz (`ARU:130-138`). 25 Hz is one of
  the BHI260AP's native rates (800 Hz halved down to 1.56 Hz, `BHI:3515-3516`).
* `BHY2.begin(NICLA_STANDALONE)` (`ARU:129`): no ESLOV handler, no BLE (`Arduino_BHY2.cpp:67-78`).
* I2C target 0x2A on `Wire` (`ARU:58`, `ARU:148-150`). On the mbed core the target is served by an
  RTOS thread ("I2CSlave", 2048-octet stack) that calls `onRequest`/`onReceive` in thread context
  (`MBW:35-41`, `MBW:150-197`), not in an ISR. TX buffer 256 octets (`MBW` `txBuffer[256]`,
  `Wire.h:80`).
* Pages rebuilt at 10 Hz (`ARU:163`), sequence counter `g_seq` incremented per rebuild (`ARU:165`),
  copied to `g_pages[2][32]` with interrupts off (`ARU:225-227`). Page select: one octet, `& 1`
  (`ARU:113-116`) - **must change to accept 2** for page 2.
* Ranges read from the BHI260AP (`getConfiguration().range`, Virtual Sensor Configuration
  0x0500+ID octets 0x0E-0x0F, `BHI` Table 75 p.108 at `BHI:7616-7642`) (`ARU:101-111`); defaults
  if not reported: accel +/-8 g, gyro +/-2000 dps, mag 1/16 uT/LSB (`ARU:73-75`). The Arduino
  example states the Nicla defaults are +/-8 g and +/-2000 dps
  (`BHY2/examples/IMURangeSettings/IMURangeSettings.ino:5-7`). Cross-check with the BHI260AP
  sensitivities: 32768/8 = 4096 LSB/g = S8g (Table 117 p.149, `BHI:11135`); 32768/2000 =
  16.38 LSB/(deg/s) ~ 16.4 at RFS2000 (Table 119 p.150, `BHI:11316`).

Page 0 (motion, magic 0xA7) and page 1 (environment, magic 0xA8): layout `ARU:16-33`, builders
`ARU:168-196` and `ARU:198-223`. Scaling and validity:

| Page.octets | Field | Wire scaling | Validity rule in the ARU | Note |
|---|---|---|---|---|
| 0.3-10 | RV quaternion w,x,y,z | int16 x 16384 (`ARU:174-177`) | norm^2 > 0.25 (`ARU:173`) | library factor 0.000061035 vs exact 2^-14 (`BHY2/src/sensors/SensorQuaternion.h:9`; `BHI` Table 89): < 0.05 LSB error at x16384 |
| 0.11-12 | RV accuracy | uint16 mrad, 0xFFFE saturated (`ARU:178-179`) | none | BHI "Estimated accuracy in radians", Q14 (`BHI:9114`) |
| 0.13-18 | accel | int16 mg (`ARU:182-184`) | any axis non-zero (`ARU:181`) | |
| 0.19-24 | gyro | int16 0.1 deg/s (`ARU:186-188`) | **always set** (`ARU:185`) | defect, 2.11 F1 |
| 0.25-30 | BMM150 | int16 0.1 uT (`ARU:192-194`) | any axis non-zero; b4 = range read (`ARU:190-191`) | |
| 1.3-6 | pressure | uint32 Pa (`ARU:203-207`) | 300-1250 hPa (`ARU:80-81`, BMP390 operating range `BMP:98`) | library factor 0.0078 undone (`ARU:41-44`, `SensorID.h:190`) |
| 1.7-8 | temperature | int16 0.01 C (`ARU:210`) | **always set** (`ARU:209`) | defect, 2.11 F1 |
| 1.9-10 | humidity | uint16 0.01 %RH (`ARU:211-213`) | h > 0 | BHI delivers 1 %RH LSB (`BHI` Table 88, `BHI:8425-9080`) |
| 1.11-20 | BSEC IAQ, sIAQ, CO2eq, bVOC | (`ARU:214-217`) | accuracy > 0 or IAQ > 0 (`ARU:208`) | |
| 1.21-24 | gas resistance | uint32 ohm (`ARU:218-220`) | > 0 | |
| 1.25 | BSEC accuracy 0..3 | (`ARU:221`) | - | |

### 2.3 Flight drivers

* `NiclaAru::page()` writes the page number, reads 32 octets, accepts magic + CRC-8 (poly 0x07,
  init 0) (`PAY:24-28`, `PAY:8-15`). `read_motion()` decodes page 0, withdraws the quaternion flag
  outside norm^2 0.81-1.21 (`PAY:47-51`), declares the ARU stalled after 5 equal sequence numbers
  (`PAY:58-61`). `read_env()` decodes page 1 (`PAY:64-79`). `AruReading` (`PAYH:20-36`).
* `I2cBus` (`I2CH:44-75`): `begin()` runs a bus clear first when GPIO numbers are given
  (`I2C:9-19`), `probe()` is not counted as an error (`I2C:27-30`), every other transfer counts
  (`I2C:21-25`), `read()` = register write then a separate read (no repeated start, ArduinoCore-zephyr
  ignores the stop flag) (`I2C:51-60`), `recover()` = end + bus clear + begin + setClock (`I2C:119-128`).
* `Rm3100` (`RM`, `RMH`): REVID 0x22 check, CMM off, BIST cleared, cycle count (`RM:5-19`); gain
  0.3671 x CC + 1.5 LSB/uT (`RM:29`) vs RM3100 manual Table 3-1: 75 LSB/uT at CC 200 (`RMM:210`);
  POLL / DRDY / 9-octet read (`RM:33-75`).
* `As7265x` takes an alternate bus (`ASH:125-138`), described in 3.2.

### 2.4 Flight sampling and packet generation

* 1 Hz sample request every SCLK second (`FL:1533`), taken in the next PA-off window
  (`FL:2173-2183`): `start_mag_sample()` POLLs both RM3100s, then reads ARU page 0 and page 1 while
  they convert (`FL:2202-2237`). BMM150 body vector accumulated into `mstat_body_` (`FL:2217-2220`);
  `bus_temp_c_` from page 1 only when flag b2 is set (`FL:2224`); ME health bits from the page
  flags (`FL:235-244`, `FL:2226-2227`).
* Periodic generation, cadence table and stretch: `FL:1535-1556`, `FL:1575-1588`.
* **ATT** (`FL:1763-1804`): flags b0..b6 (`TP:122-130`); acc/gyro are the **latest 1 Hz snapshot**
  (not averaged); BMM150 field `last_mag_ut_`; fusion quaternion + accuracy; TRIAD (gravity from the
  accelerometer gated 0.9-1.1 g, field from a fresh calibrated RM3100 else the BMM150)
  (`FL:1738-1761`, `ATC:48-72`), site inclination/declination `kGeomagInclDeg` 60 /
  `kGeomagDeclDeg` 0 (`CFG:286-287`).
* **ENV** (`FL:1809-1845`): page-1 flags mapped to ENV b0..b3.
* **MAG** (`FL:1712-1732`): mean of the 1 Hz samples per unit + RMS of |B| (Welford, `ATC:74-95`).
* **HK** `bus_temp_cC` (`FL:1634`), `sensor_health` (`FL:1624`), `i2c_errors` = sum of both buses
  (`FL:1639`).

### 2.5 Packet layouts touched by the sensors

| APID | Name | User data | Layout | Ground |
|---|---|---|---|---|
| 0x010 | HK | 78 | `bus_temp_cC` @28 i16 0.01 C, `sensor_health` @10, `i2c_errors` @37 (`TP:155-199`) | `DD` HK fields |
| 0x020 | MAG | 46 | nsamples, flags, 3 x {bx,by,bz i32 nT, rms u16 nT}, cc (`TP:245-253`, `TPC:109-115`) | `DD:277-286` |
| 0x021 | ATT | 37 | flags u8, acc i16[3] mg, gyro i16[3] 0.1 dps, mag i16[3] 0.1 uT, q_fus i16[4] Q14, q_fus_acc u16 mrad, q_triad i16[4] Q14 (`TP:255-263`, `TPC:117-127`) | `DD:287-296` |
| 0x023 | ENV | 24 | (`TP:265-276`, `TPC:129-134`) | `DD:299-311` |

Sensor health bits b0..b9 used, b10..b15 free (`TP:98-111`); FDIR bits: **all 16 used**
(`TP:69-86`) - a per-bus FDIR bit cannot be added without a new field.

### 2.6 Cadence and the downlink load planner

* Cadence (s, 0 = off) HK, RF, TIMECORR, MAG, ATT, SPEC, ENV: BOOT 10/10/0/0/0/0/0, SAFE
  30/60/300/0/0/0/0, CRUISE 60/60/300/30/60/300/120, ENCOUNTER 20/30/120/4/8/30/30, TEST
  10/10/60/4/8/30/30 (`DL:276-282`). ATT is **off in SAFE**.
* Frame period = busy x {4.0, 4.0, 2.5, 2.0, 2.0} per mode, >= busy/0.5, >= 1.5 s (`DL:284`,
  `DL:300-307`). Whole-packet lengths 90/61/22/58/49/141/36 (`DL:286-294`).
* Plan: capacity C = data field / frame period; L = sum(len/period); stretch = max(1,
  L / (0.75 C)); routine EVRs get up to 0.95 C - L, capped at 167 milli-EVR/s (`DL:344-385`,
  `DLH:266-279`).
* E22 at 2.4 kbit/s (rate codes 0-2): busy 1267 ms, frame period SAFE / CRUISE / ENCOUNTER-TEST
  5.068 / 3.167 / 2.534 s, capacity 31.2 / 49.9 / 62.4 octets/s with TM + AE (158 octets per frame)
  (`D01:906-911`, 2.4 kbit/s row `D01:909`). Offered periodic load today: SAFE 4.09, CRUISE 6.11, ENCOUNTER 33.24, TEST 41.99
  octets/s (`D01:928-930`) - reproduced exactly by re-running the planner arithmetic (section 5.8).

### 2.7 Ground

* `DD`: APIDs (`DD:62-73`), VC map (`DD:79-81`), `SENSOR_BITS` (`DD:130-131`), flag lists
  (`DD:132-135`), MAG/ATT/ENV field tables (`DD:277-311`). `docs/05` is generated from the
  dictionary and the flight sources (`GEN:9`, `GEN:39`, `GEN:118-126`).
* `DEC`: generic table-driven decoder; no ATT-specific code (`DEC:60-240`).
* `products.py`: TDM and XTCE only; the XTCE is built from `dd.PACKETS` (`products.py:1021-1046`,
  `:1312`), so a new ATT layout flows into it automatically.
* `magcal.py`: MAG (RM3100) only; gds collects unaveraged MAG packets (`GDS:1023-1038`).
* `GDS`: decoded sensor lists from HK (`GDS:2106-2131`); no attitude computation on the server.
* `WEB:144`: flag-name mapping `"ATT.flags": "ATT_FLAG_BITS"` (a new flag octet needs an entry).
* `SIM`: cadence mirror (`SIM:72-74`), ATT generator (`SIM:1131-1150`), MAG (`SIM:1111-1129`), ENV
  (`SIM:1165-1169`).

### 2.8 Dashboard

* SCIENCE tab panels (`DASH:270-277`); attitude rendering (`DASH:1781-1844`): Euler ZYX from
  quaternions, `q_fus` treated as device -> ENU and left-multiplied by the ENU -> NED rotation,
  `q_triad` NED -> body conjugated (`DASH:1793-1800`), fusion-vs-TRIAD angle 2 acos|q1.q2|
  (`DASH:1834`), acceleration with |a|, rate, BMM150 field (`DASH:1840-1842`). Environment
  (`DASH:1845-1851`). Test of the frame conversion: `TW:751-765`.

### 2.9 Documentation

* `D05` ATT (`D05:135-157`), ENV (`D05:233-246`), `sensor_health` (`D05:283-296`), flags
  (`D05:298-327`), EVR table (`D05:541-618`). **Stale line:** `D05:603` shows EVR 0x0706 as
  `... devices %s` while the flight prints `... devices %s on %s` (`FL:521-523`) and `D14:338` has
  the new text - docs/05 was not regenerated after the Qwiic change.
* `D13` 7.6 Sensors (`D13:1970-1991`), 9 SCIENCE (`D13:2434-2508`).
* `D08` T-SC-01..12 (`D08:735-797`).
* `D21:591`: in the recorded hardware runs the **Nicla Sense ME and RM3100 were not connected**
  (AS7265X on Qwiic, nothing on A4/A5). The ARU page protocol has never run on hardware.

### 2.10 Tests touching the chain

| Test | What it pins | Lines |
|---|---|---|
| `XC` header constants | APID values, `k*Len` lengths per packet, FDIR/SNS bit order, `MAG_F_/ATT_F_/ENV_F_/RF_F_` flags contiguous from bit 0 | `XC:178-215` (flags loop `XC:204-207`) |
| `XC` integration streams | ATT decoded `flags == 0x7F`; every packet re-encodes bit-exact | `XC:340-365` (`XC:359-360`) |
| `XC` dictionary structure | `packet_len` list `[49, 8, 10, 46, 37, 129, 24]` | `XC:558-561` |
| `XC` bit decoders | `0x0381` -> sensor names | `XC:609` |
| `TE2E` | ATT flags 127 in the end-to-end run | `TE2E:665-667` |
| `TW` | frame conversion of q_fus / q_triad | `TW:751-765` |
| `HTI` | `att_known()` vector and expected text | `HTI:236-243`, `HTI:564-575`, `HTI:868`, `HTI:921-931` |
| `HTI` `apid_index_map` | APID order 0..11 | `HTI:828-837` |
| `HTI` downlink load | stretched load <= 75 % for every model/rate/mode | around `HTI:1646` |
| Not tested | `NiclaAru` page decoding (`PAY` is not in the host-test sources: `tools/host_test/Makefile:10-13`), the ARU firmware itself (no compile job, item 9 of section 1) | |

### 2.11 Findings along the chain (legitimacy and robustness)

| ID | Finding | Evidence | Consequence | Fix |
|---|---|---|---|---|
| F1 | Gyro and temperature flagged valid unconditionally | `ARU:185`, `ARU:209` | ATT b2 (`FL:1782-1785`), HK `sns` ME_GYRO, ENV TEMP_RH and HK `bus_temp` show zeros as valid when the virtual sensor never delivered; FDIR AVI_OVERTEMP cannot trip on 0.00 C (`FL:1889-1893`) | Per-sensor freshness (5.6) |
| F2 | No freshness of BHI260AP data: `SensorClass` keeps the last value; `g_seq` counts nRF52 loop rebuilds | `ARU:165`, `BHY2/src/sensors/SensorManager.cpp:9-18` | A stalled BHI260AP looks alive with frozen data | Sample counters per sensor, flag cleared after 5 missed periods |
| F3 | RM3100 health bits never cleared after a runtime failure | set only `FL:505`, `FL:1991`; `finish_mag_unit` only sets `rm_fresh_` (`FL:2239-2250`) | HK "RM3100_OB OK" while the unit fails; no EVR; not re-probed (re-probe only when `!ok()`, `FL:1988`) | Failure counter -> clear bit, EVR, `ok_ = false` so the re-probe path takes over (3.6) |
| F4 | Simulator ATT inconsistent | `SIM:1134-1140` constant acc (0,0,1), constant mag (19.5, 0.5, 44.0); `gyro_x = TUMBLE[2]` etc. are ZYX Euler-angle rates, not body rates (`SIM:102`, `SIM:115-123`) | Displayed sim values are not physically related to the sim quaternion | acc = R^T (0,0,1) g, mag = R^T B_world (as `SIM:1124-1127`), body rates from the Euler-rate kinematics |
| F5 | `bus_temp` semantics | `TP:171` "Nicla Sense ME (BME688) temperature"; `BME:662-663` "depends on the PCB temperature, sensor element self-heating ... typically above ambient" | Labelled "bus temperature" in UI/docs | Label "Nicla board (BME688) temperature, above ambient" |
| F6 | ATT acc/gyro are a single 1 Hz snapshot sent every 8-60 s | `FL:1778-1785`, `DL:276-282` | Not representative of the period | 6-DOF window statistics (5.5-5.7) |
| F7 | TRIAD heading uses `kGeomagDeclDeg` = 0 by default | `CFG:286-287`, `ATC:48-72` | Heading is w.r.t. magnetic north unless set; UI does not say so | Show the configured declination; inclination does not enter TRIAD beyond the cross product (`ATC:55-59`) |
| F8 | docs/05 stale EVR text | `D05:603` vs `FL:521` | - | Regenerate (`GEN`) |
| F9 | Nicla firmware never compiled in CI | `CI:6` vs `CI:194-278`, `CHK` | page-2 code unchecked | Add `arduino-cli compile --fqbn arduino:mbed_nicla:nicla_sense` with Arduino_BHY2@1.0.8 |

---

## 3 (b) Buses on the spacecraft UNO Q and the RM3100 / Nicla split

### 3.1 The three I2C controllers

| Arduino object | Controller | Pins | Status in this project | Source |
|---|---|---|---|---|
| `Wire` | I2C2 | PB10 SCL / PB11 SDA = header pins printed "SCL"/"SDA" (D21/D20) | **Unusable**: USART3 (`Serial3`) drives the E22 on these pins; the flight never calls `Wire.begin()` | `OVL:393` (`i2cs = <&i2c2>, <&i2c4>, <&i2c3>`), `ZWIRE:271-289` (Wire, Wire1, Wire2 in `i2cs` order), `DTS:119-126`, `CFG:54-64`, `D03:140-144` |
| `Wire1` | I2C4 | PD12 SCL / PD13 SDA = Qwiic socket (pin 1 GND, 2 +3V3, 3 SDA PD13, 4 SCL PD12) | `QWIIC_WIRE`, today AS7265X only | `OVL:7-13`, `DTS:128-135`, `UDS:2032-2058` (9.3 Qwiic), `CFG:182-185` |
| `Wire2` | I2C3 | PC1 SDA = A4 (JANALOG 13) / PC0 SCL = A5 (JANALOG 14) | `SENSOR_WIRE`, everything else today | `OVL:32-38`, `OVL:296-297`, `UDS:2325-2440` (9.7 pins 13/14), `CFG:181` |

Logic: all 3.3 V; "For A4/A5 when used as I2C3 (PC1/PC0), use pull-ups to 3.3 V only" (`UDS:1343`);
A0..A5 not 5 V tolerant (`UDS:1342-1344`). Qwiic "3.3 V" (`UDS:2052-2058`). I2C4 can alternatively
be muxed to PF14/PF15 on JMISC (`DTS:131`, `OVL:11` pinctrl-1 "sleep"); PF14/PF15 are digital-pin
indices 40/42 (`OVL:321`, `OVL:323`) - irrelevant for the Qwiic socket. Clock: both buses at
`kI2cClockHz` = 100 kHz (`CFG:190`, `FL:488`, `FL:493`).

### 3.2 AS7265X auto-probe (D-13 revision)

* `spec_.begin(bus_, kPinSpecInt, &qwiic_)` - A4/A5 first, Qwiic second (`FL:501`).
* `begin()`: `bus_ = main_`, `alt_ = alt_bus` (`ASC:52-54`); boot probe loop of `probe_any()` up to
  `kSpecBootWaitMs` (`ASC:70-77`); absent -> `ST_ABSENT` and retry (`ASC:78-82`).
* `probe_any()`: probe the bus in use, then the other; switch to the one that answered
  (`ASC:877-884`). Used again by the absent re-probe (`ASC:446-453`) and by FAULT retries
  (`ASC:860-875`). `on_alt_bus()` (`ASH:137-138`).
* EVR 0x0706 names the bus (`FL:519-524`); FAULT recovery hits the bus it is on: A4/A5 with bus
  clear, Qwiic with re-initialisation only (`FL:2130-2137`). Decision text `D12:262-272`.

### 3.3 Bus clear (AS-T6, D-67) and whether the Qwiic bus can have one

Mechanics today (`I2CH:8-36`, `I2C:73-117`):
1. `Wire.end()` (only unregisters a target, `ZWIRE:79-88`; the comment says `device_deinit()` is
   not supported by the STM32 driver, `I2CH:12-15`);
2. lines released with `pinMode(INPUT_PULLUP)` (MCU weak pull-up ~40 kOhm, D-67), driven low with
   `pinMode(OUTPUT)` (= `GPIO_OUTPUT_LOW`, `ZDIG:31`), open drain emulated because the core ignores
   `OUTPUT_OPENDRAIN` (`I2CH:26-31`);
3. up to 9 SCL pulses while SDA is low, "SCL stuck low" check, then START + STOP (`I2C:82-117`),
   half period `kI2cClearHalfPeriodUs` + 1 us (`I2C:73-76`, `CFG:193-194`);
4. `begin()` re-applies the DEFAULT pinctrl (`ZWIRE:62-68`) and `setClock()` resets the block (PE)
   (`I2C:17-18`, `I2C:123-124`).
Pins: `kPinSensorSda` = 18 (PC1) and `kPinSensorScl` = 19 (PC0) (`CFG:186-189`; verified: index 18
= PC1, 19 = PC0 in the 76-entry `digital-pin-gpios`, `OVL:296-297`).

**Qwiic:** PD12/PD13 are not in `digital-pin-gpios` (`OVL:276-352`; PD1, PD2, PD8, PD9 are, PD12/13
are not), so `pinMode()` cannot reach them - the reason `FL:492` skips the bus clear. But:

* the board DTS declares `scl-gpios = <&gpiod 12 GPIO_ACTIVE_HIGH>` and `sda-gpios = <&gpiod 13
  GPIO_ACTIVE_HIGH>` on `&i2c4` (`DTS:133-134`), so `GPIO_DT_SPEC_GET(DT_NODELABEL(i2c4), scl_gpios)`
  resolves at compile time;
* the sketch already reaches Zephyr devices directly - the Wire library is compiled with the sketch
  and uses `DEVICE_DT_GET(...)` (`ZWIRE:288`); the loader is built with
  `CONFIG_LLEXT_EXPORT_DEVICES=y` (`LPRJ:21`) and `CONFIG_GPIO=y` (`LPRJ:27`);
* the core's `pinMode()` is itself `gpio_pin_configure_dt()` with `GPIO_INPUT | GPIO_PULL_UP` /
  `GPIO_OUTPUT_LOW` (`ZDIG:23-31`).

So `I2cBus` can be generalised from Arduino pin numbers to two `gpio_dt_spec` (A4/A5: entries 18/19
of `zephyr,user` `digital-pin-gpios`; Qwiic: `i2c4` `scl-gpios`/`sda-gpios`) with the same
release/drive primitives, and `Wire1.begin()` re-muxes PD12/PD13 afterwards (`ZWIRE:62-68`).
VERIFY: `tools/llext_check.py` resolves the new symbols on arduino:zephyr 0.90.0 and 1.x (CI runs
it, `CI:23-33`); bench test with a target holding SDA on the Qwiic bus. Whether Zephyr's own
`i2c_recover_bus()` is enabled for this build: not found (`UCONF` has no I2C recovery option; the
Zephyr driver sources are not in the refs).

### 3.4 FDIR per bus today

| Item | A4/A5 (`bus_`) | Qwiic (`qwiic_`) | Source |
|---|---|---|---|
| Boot bus clear + EVR 0x060A | yes | no | `FL:488-493` |
| FDIR trigger | > 10 consecutive errors (`kI2cFdirErrors`) | same | `FL:87`, `FL:1897-1913` |
| Response | `bus_recover()` = bus clear + re-init | `recover()` without pins = re-init only | `FL:1955-1962`, `FL:1905-1912`, `I2C:119-128` |
| FDIR flag | shared `FDIR_I2C_BUS` (bit 5) | shared | `TP:75` |
| Rate limit | **shared** `recovered_once_` / `last_recover_ms_` (10 s, `kBusRecoverMinMs`) | shared | `FL:85`, `FL:1898`, `FL:1906`, `FL:1910-1911`, `FL:1956-1957`, `FL:2134-2135` |
| Healthy again | 10 s without new errors on **either** bus | same | `FL:1914-1925`, `CFG:196` |
| HK counter | `i2c_errors` = sum | sum | `FL:1639` |
| Loss canary | Nicla (`aru_fail_count_ > 5` -> `FDIR_SENSOR_LOST`, EVR 0x0606/0x0607) | none | `FL:1926-1937` |

With the split, the shared rate limiter lets a recovery on one bus suppress the other's for 10 s,
and the Nicla canary no longer covers the magnetometer bus.

### 3.5 Separating the RM3100 and the Nicla: options compared

Constraints: unique addresses system-wide (RM3100 0x20/0x23, Nicla 0x2A, AS7265X 0x49; `CFG:173-177`),
so any device may sit on either bus without a clash; the owner wants the Nicla's ESLOV loose wires
on the UNO Q **header**; the only header I2C not taken by the E22 UART is A4/A5 (3.1).

| Criterion | (i) Nicla A4/A5, RM3100 Qwiic | (ii) RM3100 A4/A5, Nicla Qwiic | (iii) every device auto-probed on both |
|---|---|---|---|
| Owner's wish (ESLOV loose wires on the header) | **met** (A4/A5 are JANALOG 13/14) | not met: Qwiic is a 4-pin JST-SH socket (`UDS:2032-2058`), the Nicla needs a cut cable | met only if wired so; separation not enforced |
| Nicla 5 V | ESLOV pin 1 from the 5 V source (4.5) | Qwiic carries 3.3 V only (`UDS:2046-2049`): 5 V must come from elsewhere anyway | either |
| Bus clear for the boom (RM3100) | needs the new Qwiic bus clear (3.3) or re-init only | **yes** (existing A4/A5 clear) | depends on wiring |
| Bus clear for the Nicla | yes | needs Qwiic clear | depends |
| Long boom cable | on Qwiic: 3.3 V from Qwiic pin 2, the cable starts at a JST-SH pigtail | on A4/A5 header (as today) | - |
| Magnetometer bus electrically quiet (only RM3100 traffic) | yes if the AS7265X is moved to A4/A5 | yes if the AS7265X stays on Qwiic... then the Nicla shares with it | not guaranteed |
| AS7265X | recommended A4/A5 (its I2C-jumper pull-ups then serve the Nicla bus, `ASG:106-108`; an AS72651 hang is cleared by the A4/A5 bus clear, `I2CH:8-11`) | A4/A5 or Qwiic | as today (D-13) |
| Code change size | small: two fixed bus roles + per-bus FDIR | same size | larger: begin/re-probe of Nicla and RM3100 must try both buses (like `ASC:877-884`), plus a "not separated" detector |
| Separation guaranteed by software | yes (fixed roles) | yes | no (only reported) |

**Recommendation: (i) with fixed roles**, AS7265X kept as today (A4/A5 first, Qwiic alternate),
**documented on A4/A5**. If the AS7265X stays on Qwiic (the owner's current set-up, `D21:591`),
it shares the boom bus with the RM3100s: still compliant (Nicla and RM3100 separated) but a hung
AS72651 can block the magnetometers unless the Qwiic bus clear (3.3) is implemented. Optional
diagnostic: at boot probe 0x20/0x23 on A4/A5 and 0x2A on Qwiic **without using them** and raise a
WARN EVR "RM3100 0x20 answers on A4/A5: wire it to the Qwiic bus" (owner decision whether to use a
mis-wired unit anyway; see open questions).

### 3.6 Exact change list for the split

Flight (`vgq1-spacecraft/sketch/src`):
1. `CFG:173-191`: rewrite the bus block: `NICLA_WIRE Wire2` (A4/A5: Nicla 0x2A + AS7265X 0x49
   preferred), `MAG_WIRE Wire1` (Qwiic: RM3100 0x20/0x23); keep `kPinSensorSda/Scl` for A4/A5; add the
   Qwiic GPIO specs if 3.3 is implemented; drop "one I2C bus" wording.
2. `FLH:191-197`: `bus_` -> `nicla_bus_`, `qwiic_` -> `mag_bus_`; per-bus `recovered_once_` /
   `last_recover_ms_` / `bus_clear_failed_` (today single, `FL:1956-1960`).
3. `FL:486-517` `init_sensors()`: `nicla_bus_.begin(NICLA_WIRE, ..., 18, 19)`;
   `mag_bus_.begin(MAG_WIRE, ...)` (with Qwiic pins if 3.3); `aru_.begin(nicla_bus_)`;
   `spec_.begin(nicla_bus_, kPinSpecInt, &mag_bus_)`; `mag_ob_/mag_ib_.begin(mag_bus_, ...)`.
4. `FL:1969-1998` `reprobe_instruments()`: RM3100 on `mag_bus_`; Nicla on `nicla_bus_`; EVR 0x060B
   text `instrument acquired: %s on %s` (bus name), like 0x0706.
5. `FL:1897-1925` FDIR: one recovery block per bus with its own limiter; EVR 0x0605 and 0x060A name
   the bus; keep the single `FDIR_I2C_BUS` (no free FDIR bit, `TP:69-86`).
6. New RM3100 loss detection (F3): count consecutive `collect()`/`start()` failures per unit;
   after N (e.g. 5 samples) clear `SNS_RM3100_*`, set `ok_` false (so `FL:1988` re-probes), EVR WARN
   (new id, e.g. 0x060D "RM3100 %s stopped responding"); the existing 0x060B reports re-acquisition.
7. `FL:2130-2137`: AS7265X FAULT recovery uses the bus objects it is on (`on_alt_bus()` = Qwiic).
8. `FL:2202-2203` comment: the RM3100 POLL is on `mag_bus_`, the pages on `nicla_bus_` (still one
   thread, the conversion still overlaps).
9. `FL:492` comment, `I2CH:1-7`, `PAYH:1-4`, `ASH:1-4`, `RMH` header: new bus wording.
10. HK: keep `i2c_errors` (sum) or split into two u8 (layout change: `TP:176`, `DD`, `XC:560`);
    sc_status_x could carry per-bus counts (480-character limit, `TP:314-330`).

Ground: `DD` text of `SENSOR_BITS` descriptions; `GDS`/`DASH` sensor panel shows which bus (from EVR
0x060B/0x0706 only, unless HK gains a field). Docs: `D03` 3 (bus table `D03:131-138`), 4.1 pin map
(`D03:156-176`), 4.7 rewritten per bus (`D03:373-456`), 4.8 diagram (`D03:458-502`); `D12` D-13
superseded by a new decision; `D15:169`; `D00` 8.5 wiring rows; `D14` I2C events; `D08` new tests
(5.10); `D16` "Add a sensor".

### 3.7 Pull-ups and cable per bus

* One set of pull-ups per bus, to 3.3 V only (`UDS:1343`, `D03:441-448`). Range at 100 kHz:
  Rp >= (3.3 - 0.4) V / 3 mA = 0.97 kOhm; Rp <= 1000 ns / (0.8473 Cb) (NXP UM10204 7.1 as cited in
  `D03:446-448`): 4.7 kOhm allows Cb <= 251 pF, 2.2 kOhm allows <= 536 pF (arithmetic).
* A4/A5 (Nicla + AS7265X): the AS7265X board's I2C jumper carries its pull-ups (`ASG:106-108`;
  value not found); without the AS7265X fit 4.7 kOhm A4 and A5 -> +3V3 (JANALOG 4). Whether the
  Nicla has on-board pull-ups on its external I2C side: not found.
* Qwiic (RM3100 boom): whether the UNO Q has pull-ups on the Qwiic lines: not found in `UDS`
  (searched "pull"); D-67 found A4/A5 has none (`D12:899-901`). Measure check 1 of `D03:449-451`
  on the Qwiic SDA/SCL; fit 4.7 kOhm (2.2 kOhm for a long boom cable) at the UNO Q end; open any
  breakout pull-ups. RM3100: SCL up to 1 MHz (`RMM:734`), UM10204 standard/fast/fast-plus/HS
  (`RMM:801-815`), VIH 0.7 DVDD, supply 2.0-3.6 V (`RMM:298-319` Table 3-5).
* Cable capacitance per metre: not in the sources (measure, or use the cable datasheet).

---

## 4 (c) ESLOV: reaching the ARU through J5

### 4.1 Connector and pin-out

J5 = JST `SM05B-SRSS-TB(LF)(SN)` 5-pin (`NDS:399-400`); pin 1 5V "5V Power Rail", 2 INT "Digital
IO", 3 SCL, 4 SDA, 5 GND (`NDS:628-650`) - matches the owner's statement. Pinout drawing: ESLOV
"+5V INT SCL SDA GND" with nets P0_19, P0_23, P0_22 (`NPIN:560-572`).

### 4.2 Which bus, and does the ARU answer there with no firmware change?

| Evidence | Source |
|---|---|
| ARU target: `Wire.begin(0x2A)` | `ARU:58`, `ARU:148` |
| Nicla `Wire` = P0_22 SDA / P0_23 SCL; `Wire1` = P0_15 / P0_16 | `MBV.h:61-66`, `MBV.h:118-123` |
| Arduino pin 3 "SCL1" = P0_23, pin 4 "SDA1" = P0_22 (the J2 I2C pins) | `MBV.cpp:77-78`; J2 "3/SCL1 4/SDA1" `NPIN:130-131` |
| `PIN_ESLOV_INT` = p19 (P0_19); pin 12 "INT ESLOV" | `MBV.h:81-82`, `MBV.cpp:90` |
| Arduino's ESLOV handler serves ESLOV on `Wire` at 0x55, INT on P0_19 | `BHY2/src/EslovHandler.cpp:12`, `:20-29`, `EslovHandler.h:8` |
| "As shield" (J1/J2 headers) and "via ESLOV" differ only in the INT pin (p24 vs p19), same `Wire` | `BHY2/src/Arduino_BHY2.h:21-28`, `Arduino_BHY2.cpp:50-53`, `EslovHandler.h:10` |
| PMIC and LED driver use `Wire1` | `NSYS:38`, `BQ25120A.cpp:48-64`, `RGBled.cpp:154-172` |
| Only two I2C, one on the header | `NDS:93` |

**Conclusion:** ESLOV SDA/SCL are the `Wire` TWI - the same one the ARU listens on at 0x2A - so the
ARU is reachable on J5 with no firmware change, and only one host should be wired (J2 pins 1/2 and
J5 pins 3/4 are the same bus; do not connect both). VERIFY (power off): continuity J5-4 <-> J2-1 and
J5-3 <-> J2-2; this also proves both go through the same translator.

### 4.3 Signal level

* `Wire` pins are on the nRF52 1.8 V domain (`NDS:406`). J1/J2 are translated to VDDIO_EXT
  (`NDS:469`); "All I/O broken out to the pins are fed through a bidirectional voltage translator
  running at VDDIO_EXT" (`NDS:435`). The ARU sets VDDIO_EXT = 3.3 V in `BHY2.begin()`
  (`Arduino_BHY2.cpp:57`, `NSYS:74-82`).
* Arduino's host library for ESLOV supports SAMD (MKR) and mbed (Portenta) hosts
  (`BHY2H/library.properties` `architectures=samd,mbed`), both 3.3 V-logic boards (inference), and
  the Nicla datasheet names the Portenta H7 as related ESLOV product (`NDS:186-189`).
* **Inference:** ESLOV SDA/SCL are at VDDIO_EXT = 3.3 V once the ARU is running, compatible with
  A4/A5 ("pull-ups to 3.3 V only", `UDS:1343`). The translator part on J5 and the level before the
  firmware sets VDDIO_EXT: not found. VERIFY with the Nicla running and the bus pulled up: J5-3/J5-4
  idle 3.2-3.3 V, never above 3.3 V; with the Nicla unpowered and the UNO Q bus pulled up, J2 pin 7
  (VDDIO_EXT) stays ~0 V (no back-feeding through the translator).
* VDDIO_EXT (J2 pin 7) stays unconnected (firmware-generated; `D03:397`; `NPIN:133` describes the
  opposite use, powering through VDDIO_EXT with the LDO disabled - not used here).

### 4.4 INT

Not needed: the ARU protocol is polled (the host reads at 1 Hz, `FL:1533`; pages are always valid,
carry a sequence counter and a CRC, `ARU:13-33`). In `NICLA_STANDALONE` the ARU never configures
P0_19 (the only `pinMode(_eslovIntPin, OUTPUT)` is in the `NICLA_I2C` branch,
`Arduino_BHY2.cpp:71-74`). Leave ESLOV pin 2 unconnected and insulated. (Its level/translator: not
found.)

### 4.5 Power through ESLOV pin 1 (5 V)

* "The Arduino Nicla Sense ME can be powered via micro USB (J7), ESLOV (J5) or VIN ... A Schottky
  diode provides reverse polarity protection to the USB and ESLOV voltages" (`NDS:435`). The ESLOV
  5 V input range: not found; VIN pad 3.5-5.5 V, USB 4.8-5.5 V (`NDS:208-219`). Inference: treat
  J5-1 like USB (behind a Schottky): a regulated 5 V supply (4.8-5.5 V).
* Do not also feed J2 pin 9 VIN from a different source (only one supply path; the J2 VIN wiring of
  `D03:396` is replaced by ESLOV pin 1).
* Never from 3.3 V (+3V3 OUT is below the 3.5 V minimum of the Nicla supply inputs, `NDS:208-219`).
* Where on the UNO Q:
  | Spacecraft power configuration | 5 V for J5-1 | Source |
  |---|---|---|
  | A (12 V on VIN, external buck, default) | the external 5 V buck output (the rail that already feeds the E22 and today the Nicla VIN) | `D03:203-211` |
  | B (UNO Q powered from USB-C 5 V / 3 A) | JANALOG pin 5 "+5V USB VBUS" = `5V_USB_VBUS`, "5 V supply (pass-through)" | `UDS` 9.7 pin 5 (`UDS:2358-2360`), `D03:167` |
  JANALOG 5 is the USB-C VBUS net, not 5V_SYS; with the board on VIN only it is live only if Q2801
  sources VBUS (host/OTG mode, `UDS:888`) - not guaranteed: VERIFY by measuring JANALOG 5 with the
  board on VIN only before using it in configuration A. (JSPI pin 2 is the same net per the pin map,
  `UDS:2070-2074`, although `UDS:1349` calls it "5V_SYS": datasheet inconsistency.)
* Current: Nicla figures are only given for battery operation (2.5 mA advertising with 1 Hz
  polling, `NDS` power table after `:650`); the BHI260AP alone draws ~1.1-1.2 mA with the 9-DOF
  rotation vector at 25 Hz (`BHI` Table 14 p.48, `BHI:3420-3450`); BME688 gas scan 0.18 mAh per
  standard scan (`BME:101-105`). A measured value for the ARU load: not found (record in docs/21).

### 4.6 Pull-ups

Not found whether J5/J2 carry pull-ups on the Nicla. Use the bus's single set (3.7). On A4/A5 with
the AS7265X (I2C jumper closed) no extra resistor; without it, 4.7 kOhm A4/A5 -> +3V3. VERIFY check
1 and 2 of `D03:449-451` with the Nicla connected.

### 4.7 Wiring for the owner (4 of 5 wires)

| ESLOV J5 pin | Signal | UNO Q (spacecraft) | Note |
|---|---|---|---|
| 1 | 5V | config A: external 5 V buck output; config B: JANALOG 5 (+5V USB VBUS) | never to A4/A5 or any 3.3 V pin (`UDS:1342-1344`) |
| 2 | INT | **not connected**, insulate | 4.4 |
| 3 | SCL | **A5** = JANALOG 14 (PC0, I2C3 SCL) | **not** the header pin printed "SCL" (that is the E22 UART TX, `D03:140-144`, `CFG:63`) |
| 4 | SDA | **A4** = JANALOG 13 (PC1, I2C3 SDA) | **not** the pin printed "SDA" (E22 UART RX) |
| 5 | GND | GND (JANALOG 6/7) | common ground with the 5 V source |

Wire colours of the owner's cable: not found (identify pin 1 by the connector marking and continuity).
Checks after wiring: HK `sensors` bit 0 within seconds (`D13:1977`), EVR 0x060B `Nicla Sense ME`.

### 4.8 Why not Arduino's ESLOV protocol

Arduino_BHY2Host is `architectures=samd,mbed` (not zephyr), uses `Wire` at 400 kHz and needs the INT
line with unbounded busy waits (`BHY2H/src/EslovHandler.cpp:23-25`, `:48`, `:58`, `:184`) - it
would break the flight loop's bounded-time rule. The ARU's fixed pages with CRC (no INT, bounded
transfers) remain the right design.

---

## 5 (d) BHI260AP 6-DOF

### 5.1 Virtual sensors reachable through Arduino_BHY2 1.0.8 (non-magnetometer focus)

IDs and payloads from `BHY2/src/sensors/SensorID.h:7-97` and the library's scale list
(`SensorID.h:135-219`); formats, scale and reporting mode from `BHI` Table 88 p.119 (`BHI:8433-9080`).
Which IDs the Nicla's BHI260AP firmware actually provides is a run-time list ("Virtual Sensors
Present" parameter 0x011F, `BHI:3370`; library `bhy2_get_virt_sensor_list` ->
`hasSensor()`, `BoschSensortec.cpp:71-72`, `:103-107`; example `examples/ShowSensorList`); the list
for the shipped firmware: not found -> the ARU must test `hasSensor()` and report it.

| ID (wake-up) | Name | BHI format / scale | Library payload, factor | Reporting | 6-DOF use |
|---|---|---|---|---|---|
| 37 (38) | Game rotation vector (accel + gyro) | Quaternion+ Q14 x,y,z,w + accuracy (always 0 for GRV) (Table 89 p.121, `BHI:9082-9118`) | PQUATERNION; `SensorQuaternion` factor 0.000061035 (`SensorQuaternion.h:9`) | continuous | **yes**: tilt + relative heading without the magnetometer |
| 34 (35) | Rotation vector (9-DOF) | Quaternion+ with accuracy in rad | as above | continuous | already page 0 |
| 28 (29) | Gravity | 3D vector, "Dynamic" = scaled to the accel range (Table 88 note 3) | VECTOR3D, raw | continuous | derivable from GRV (5.6) |
| 31 (32) | Linear acceleration | 3D vector, dynamic | VECTOR3D, raw | continuous | derivable: accel - gravity |
| 4 (6) | Accelerometer corrected | 3D vector, dynamic | VECTOR3D | continuous | page 0, window stats |
| 13 (15) | Gyroscope corrected | 3D vector, dynamic | VECTOR3D | continuous | page 0, window stats |
| 3 / 12 (7 / 16) | Raw (uncalibrated) accel / gyro | 3D vector, dynamic | VECTOR3D | continuous | optional (offset = raw - corrected) |
| 1 / 10 | Passthrough accel / gyro | "Unmodified raw data of sensor" | VECTOR3D | continuous | not needed |
| 5 (91) / 14 (92) | Accel / gyro offset (BSX calibration) | 3D vector, dynamic | VECTOR3D | continuous | optional health (needs slots) |
| 75 | Stationary detect | event, no payload | PEVENT (`Sensor::value()` returns 1 once, `Sensor.h:18-28`) | on-change | criteria not documented in `BHI` (only Table 88 row): black box |
| 77 | Motion detect | event | PEVENT | on-change | black box |
| 142 (143) | Any motion | event | PEVENT | one-shot | black box |
| 48 | Tilt detector | event | PEVENT | special | phone feature - exclude |
| 69 (70) | Device orientation | u8: 0 portrait upright, 1 landscape left, 2 portrait upside down, 3 landscape right | P8BITUNSIGNED | on-change | phone feature - exclude |
| 43 | Orientation (Euler) | heading/pitch/roll, 360/2^15 deg (Table 90) | PEULER 0.01098 | continuous | needs the magnetometer (9-DOF) |
| 50/52/55/57/59/61/63/67 | steps, significant motion, gestures, activity, wrist tilt | events / counters | - | - | human-activity features - exclude |

Ranges and resolution: accel +/-2/4/8/16 g, 16384/8192/4096/2048 LSB/g (Table 117 p.149,
`BHI:11107-11140`); gyro +/-125...+/-2000 dps, 262.4...16.4 LSB/(deg/s) (Tables 118/119 p.149-150,
`BHI:11260-11330`). "Dynamic" outputs: physical = raw / 32768 x current range (`ARU:35-40`, range
from Table 75). Changing a range affects every virtual sensor of that physical sensor; the BHI picks
the largest requested range; fusion outputs (RV/GRV) cannot have a range (`BHI:6682-6745`, Table 58
p.96). ODR: 800 Hz halving down to 1.56 Hz; "Below an ODR of 100 Hz, the data provided is sub sampled
from a 100 Hz signal"; unsupported rates land at 90-210 % of the request (`BHI:3515-3520`).
Whether an anti-alias filter precedes that sub-sampling: not found.

Performance numbers for "legitimate" displays (`BHI` Tables 117/119):
accel zero-g offset +/-20 mg (component), +/-50 mg over life (8 g range); TC 0.5 mg/K; sensitivity
error +/-2 % max; output noise 1.2 mg rms (8 g, 80 Hz filter, ODR 200 Hz) (`BHI:11140-11210`).
Gyro zero-rate offset +/-1 deg/s (fast offset compensation off), +/-1.5 deg/s over temperature;
noise density 0.007 deg/s/sqrt(Hz) at 10 Hz; 0.07 deg/s rms (74.6 Hz, ODR 200 Hz); bias stability
5 deg/h (vibration-free, 25 C); sensitivity error +/-3 % (`BHI:11330-11440`).
Resolution note: at the +/-2000 dps default the LSB is 0.061 deg/s, coarser than the noise in a
12.5 Hz band (0.007 x sqrt(12.5) = 0.025 deg/s, arithmetic): a +/-250 dps range (LSB 0.0076 deg/s)
would resolve slow tumbles (owner decision; range changes are applied by the BSX, `BHI:6743`).

### 5.2 What "accuracy" means here (three different things)

| Quantity | Meaning | Available to the ARU? |
|---|---|---|
| Quaternion+ "Estimated accuracy in radians" | RV / geomagnetic RV heading accuracy estimate, Q14 rad; **GRV always 0** | yes (page 0 octets 11-12); GRV: meaningless (`BHI:9114-9118`) |
| Meta event "Sensor Status" 0 unreliable, 1 low, 2 medium, 3 high | per virtual sensor data quality (calibration state) (Table 100 p.128, `BHI:9729-9735` area) | **no**: Arduino_BHY2 only prints it with debug on (`BoschParser.cpp:85`, `:112-119`), no API |
| BSEC accuracy 0..3 | BME688/BSEC IAQ calibration | yes (page 1 octet 25) |

Calibration-state parameters 0x0201/0x0203/0x0205 are opaque BSX state blobs to save "when the
accuracy ... reaches 3" (`BHI:7354`, Table 71) - not an accuracy readout.

### 5.3 Library constraints that shape the design

| Constraint | Evidence | Design rule |
|---|---|---|
| Max 10 subscribed `SensorClass` objects, no bounds check | `SensorManager.h:16`, `SensorManager.cpp:31-34`; subscription happens in `begin()` only when `hasSensor()` (`SensorClass.cpp:28-52`) | ARU has 9 (`ARU:60-68`): add **only** `SensorQuaternion game(SENSOR_ID_GAMERV)` |
| `clearDataAvailFlag()` has no return | `SensorClass.h:48` | never call it; use a derived class: `template<class S> struct Fresh : S { using S::S; bool take() { bool d = this->_dataAvail; this->_dataAvail = false; return d; } };` (`_dataAvail` is protected, `SensorClass.h:59`) |
| A `SensorClass` keeps only the latest sample | `SensorManager.cpp:9-18`, `SensorXYZ.h:24-27` | call `BHY2.update()` and `take()` every loop pass; count samples; report the count |
| Every short packet also goes into a circular queue (overwrite oldest), size 40 (BLE batch enabled) | `BoschSensortec.cpp:203-209`, `BoschSensortec.h:22-28`, `BLEHandler.h:10-11` (`(244/12)/10*10 + 20` = 40), `sensortec` is meant to be linked from sketches (`BoschSensortec.h:177-180`) | alternative per-sample path without extra subscriptions (e.g. for event sensors 75/77/142 configured with `BHY2.configureSensor(id, rate, latency)`, `Arduino_BHY2.h:142`): drain `sensortec.readSensorData()` after each update; queue full (40) before draining = possible loss -> flag |
| Meta events (sensor status, dynamic range changed) not exposed | `BoschParser.cpp:59-140` | no accuracy 0..3; read ranges once more after start-up (as `ARU:166`) |
| BSEC long packets carry comp_t/comp_h/comp_g | parse path: payload > 10 -> long packet (`BoschParser.cpp:25-42`, `SensorTypes.h:7-10`); `parseBSEC` reads them (`DataParser.cpp:27-40`); `SensorBSEC.h:12-20` comment says otherwise | VERIFY on the bench (`bsec.comp_g()` vs `gas.value()`); if true, TEMP/HUM/GAS objects could be dropped to free 3 slots (changes ENV semantics: heat-compensated T, RH at 1/500 %) |

### 5.4 Physically legitimate quantities (and the ones to exclude)

Legitimate, with the stated limits:
* **Tilt / pitch / roll from gravity** (from the GRV attitude, which the fusion filters, so valid
  during motion; frame = Nicla body frame).
* **Relative heading from the GRV** (no magnetometer; reference arbitrary at Nicla boot; drifts with
  the gyro bias, bias stability 5 deg/h, `BHI` Table 119).
* **9-DOF minus 6-DOF heading offset** and **tilt disagreement** from q_RV and q_GRV taken from the
  **same** rebuild: magnetic disturbance / GRV drift indicator.
* **Vibration**: AC RMS of the acceleration within a window (per-axis mean removed), bandwidth <=
  ODR/2 (12.5 Hz at 25 Hz), noise floor ~sqrt(3) x per-axis noise (1.2 mg rms at 200 Hz ODR per
  Table 117; at 25 Hz: not specified - VERIFY at rest).
* **Peak deviation** max |a_i - a_mean| in the window.
* **Mean angular-rate vector, |omega_mean|, spin axis** (meaningful when well above the residual
  offset; uncalibrated ZRO +/-1 deg/s, BSX-corrected residual not specified - label "below ~0.2
  deg/s indistinguishable from offset", VERIFY T-SC-14); **peak |omega|**.
* **Stationarity** decided on the ground from the peaks with stated thresholds.
* Accelerometer |a| vs 1 g at rest (existing T-SC-02 check).

Not legitimate / excluded: velocity or position by integrating acceleration (drift); the mean
"linear acceleration" as a spacecraft acceleration (dominated by the residual offset, up to +/-50 mg,
and the +/-2 % sensitivity error); a GRV accuracy (always 0); absolute yaw from the GRV; device
orientation (portrait/landscape), steps, gestures, activity, tilt detector; the BHI stationary/motion
detectors unless labelled "Bosch detector, criteria undocumented".

### 5.5 Proposed ARU page 2 "IMU 6-DOF" (magic 0xA9)

Same conventions as pages 0/1: 32 octets, big-endian, octet 31 = CRC-8 poly 0x07 init 0 over 0..30
(`ARU:83-90`, `PAY:8-15`). Host writes 0x02, reads 32.

| Octets | Field | Type / unit | Definition |
|---|---|---|---|
| 0 | magic | 0xA9 | |
| 1 | seq | u8 | the same `g_seq` as pages 0/1 of this rebuild (pairing check) |
| 2 | flags | u8 | b0 GRV valid (present, fresh <= 200 ms, norm^2 > 0.25); b1 window stats valid (completed window, >= 20 accel and >= 20 gyro samples at 25 Hz); b2 accel near full scale in the window (any |raw| >= 32000); b3 gyro near full scale; b4 samples missed (count below rate x window - 2, or queue/flag overrun); b5 GRV present in the BHI firmware (`hasSensor(37)`); b6 accel range read from the BHI (as page 0 b4 for the mag); b7 0 |
| 3-10 | q_grv w,x,y,z | int16 Q14 (x16384) | BHI Table 89 order x,y,z,w re-ordered as page 0 |
| 11 | win_seq | u8 | increments once per completed 1.000 s window (Nicla `millis()`), lets the host de-duplicate and count gaps |
| 12 | n | u8 | samples in the window, min(accel, gyro) |
| 13-18 | acc_mean x,y,z | int16 mg | window mean of the corrected accelerometer |
| 19-20 | vib_rms | u16 0.1 mg | sqrt( sum over axes of the per-axis variance ) within the window (DC removed) |
| 21-22 | vib_peak | u16 1 mg | max over samples of |a_i - a_mean| |
| 23-28 | rate_mean x,y,z | int16 0.01 deg/s | window mean of the corrected gyroscope (+/-327.67 deg/s; b3 when saturated) |
| 29-30 | rate_peak | u16 0.1 deg/s | max over samples of |omega_i| |
| 31 | CRC-8 | | over 0..30 |

Gravity and linear acceleration are not on the page: gravity follows from q_grv exactly (5.9), linear
acceleration = acc_mean - gravity (with the offset caveat of 5.4).

### 5.6 ARU firmware changes (algorithm)

1. Add `Fresh<SensorQuaternion> game(SENSOR_ID_GAMERV)` (10th and last slot) and make `accel`,
   `gyro`, `rotation`, `mag`, `temperature`, ... `Fresh<>` so that each has a freshness counter (fixes
   F1/F2: clear a page flag when no sample arrived for 5 nominal periods).
2. `game.begin(25, 0)`; record `bool grv_present = game.begin(...)` (returns `hasSensor`,
   `SensorClass.cpp:28-35`).
3. Every loop pass: `BHY2.update()`; if `accel.take()` push (x,y,z) into a 64-entry window buffer; if
   `gyro.take()` likewise (384 octets each; trivial on the nRF52's 64 KB, `NDS:406`).
4. At each 1 s boundary: two-pass statistics on the buffer (exact mean, variance, peak deviation,
   peak |omega|), scale with the read ranges (`g_acc_mg_per_lsb`, `g_gyro_ddps_per_lsb` -> 0.01
   deg/s), `win_seq++`, reset the buffers. Store the results; page 2 shows the last completed window.
5. Rebuild page 2 in the same 10 Hz block as pages 0/1, same `g_seq`, copy all three pages with
   interrupts off (`ARU:225-227` -> `g_pages[3][32]`).
6. `on_receive`: `g_page_sel = v <= 2 ? v : 0` (today `& 1`, `ARU:114`).
7. Serial banner: add "GRV present/absent" and the 6-DOF window rate.
8. Header comment (`ARU:13-53`): document page 2.

### 5.7 Flight read, aggregation and packet

* `payload.h/.cpp`: `struct AruImu { uint8_t seq, flags, win_seq, n; float q[4]; int16_t acc_mean_mg[3];
  uint16_t vib_rms_dmg, vib_peak_mg; int16_t rate_mean_cdps[3]; uint16_t rate_peak_ddps; }`;
  `bool NiclaAru::read_imu(AruImu&)` = `page(2, 0xA9, b)` + decode (+ the same quaternion norm gate as
  `PAY:47-51`).
* `FL:2212-2236`: read page 0, **then page 2 immediately** (pairing: `imu.seq == motion.seq`; if not,
  re-read page 0 once), then page 1. Three pages ~ 3 x (write 1 + read 32 octets) at 100 kHz ~ 10 ms
  (arithmetic, 9 bits per octet), longer than the ~6.8 ms RM3100 conversion (`RMH:13-15`) - harmless,
  the collect happens in later passes (`FL:2185-2199`).
* `attitude.h`: pure-C++ `ImuStats` (host-testable like `MagStats`, `ATH:34-45`): `add(const AruImu&)`
  only when `win_seq` changed (count gaps = delta - 1), accumulate sum of vib_rms^2, max vib_peak,
  sum of rate_mean, max rate_peak, OR of saturation flags; `reset()` in `gen_att()` (like
  `gen_mag()`, `FL:1725-1727`).
* **ATT v4** (APID 0x021, 37 -> 59 octets; `TP:255-263`, `TPC:117-127`, `TP:283`):

| Offset | Field | Type | Unit / scaling | Sentinel |
|---|---|---|---|---|
| 0-36 | unchanged ATT v3 fields | | | |
| 37 | imu_flags | u8 | `IMU_F_GRV` b0, `IMU_F_STATS` b1 (>= 1 window), `IMU_F_PAIRED` b2 (q_grv from the same ARU rebuild as q_fus), `IMU_F_ACC_SAT` b3, `IMU_F_GYRO_SAT` b4, `IMU_F_GAP` b5 (windows or samples missed), `IMU_F_GRV_PRESENT` b6 | - |
| 38-45 | q_grv w,x,y,z | i16[4] | Q14 | 0x7FFF |
| 46 | imu_nwin | u8 | 1 s windows aggregated | (0 = none) |
| 47-48 | vib_rms | u16 | 0.1 mg; sqrt(mean of window vib_rms^2) | 0xFFFF |
| 49-50 | vib_peak | u16 | 1 mg; max over windows | 0xFFFF |
| 51-56 | rate_mean x,y,z | i16[3] | 0.01 deg/s; mean of window means | 0x7FFF |
| 57-58 | rate_peak | u16 | 0.1 deg/s; max over windows | 0xFFFF |

  A separate flag octet is required: `ATT.flags` uses b0..b6 and `XC:204-207` requires each flag
  enum to be contiguous from bit 0. New `enum ImuFlag` (`IMU_F_` prefix) must be added to that test
  loop and to `DD` (`IMU_FLAG_BITS`), `WEB:144` (`"ATT.imu_flags": "IMU_FLAG_BITS"`).
* `SensorBit`: add `SNS_ME_GRV = 1u << 10` (GRV valid), `DD` `SENSOR_BITS` + "ME_GRV" (`XC:202-203`
  checks the order); `kSensorInstrumentMask` unchanged.
* Optional: TRIAD uses the page-2 window-mean acceleration (25-sample mean, noise / 5) instead of the
  1 Hz snapshot (`FL:1742-1744`).

### 5.8 Downlink load at 2.4 kbit/s (and at ultra-long-range settings)

Re-run of `plan_downlink_load()` (`DL:344-385`) with `frame_period_ms()` (`DL:300-307`), the
cadence table (`DL:276-282`) and the E22 / SX126x busy times of `D01:906-923`. "L" = periodic load,
octets/s; % = L / C; stretch x1.00 = none; EVR = routine-EVR refill, milli-EVR/s (cap 167). Today's
row reproduces `D01:916-933` exactly (e.g. SX126x p1 SAFE x2.40 / CRUISE x2.24 / ENCOUNTER x9.72 /
TEST x12.28).

E22 2.4 kbit/s (busy 1267 ms):

| Variant | Framing | CRUISE | ENCOUNTER | TEST |
|---|---|---|---|---|
| Today (ATT 49) | TM + AE (C = 49.89 / 62.35) | 6.11 (12.2 %) x1.00 | 33.24 (53.3 %) x1.00 | 41.99 (67.3 %) x1.00, EVR 167 |
| Today | USLP + AE (C = 48.31 / 60.38) | 6.11 x1.00 | 33.24 (55.1 %) x1.00 | 41.99 (69.5 %) x1.00, EVR 167 |
| **ATT v4 (71 incl. headers)** | TM + AE | 6.48 (13.0 %) x1.00 | 35.99 (57.7 %) x1.00 | 44.74 (71.8 %) x1.00, EVR 167 |
| **ATT v4** | USLP + AE | 6.48 (13.4 %) x1.00 | 35.99 (59.6 %) x1.00 | 44.74 (74.1 %) x1.00, EVR 157 |
| ATT v4 + acc_mean (77) | USLP + AE | 6.58 x1.00 | 36.74 x1.00 | 45.49 (75.3 %) **x1.01**, EVR 153 |
| Separate IMU APID (42, at ATT cadence) | TM + AE | 6.81 x1.00 | 38.49 x1.00 | 47.24 (75.8 %) **x1.02**, EVR 161 |
| Separate IMU APID | USLP + AE | 6.81 x1.00 | 38.49 x1.00 | 47.24 (78.2 %) **x1.05**, EVR 154 |

Ultra-long-range settings (everything is already stretched; ATT v4 adds ~8 %):

| Setting, TM + AE | CRUISE | ENCOUNTER | TEST |
|---|---|---|---|
| SX126x p1 (busy 17318 ms), today | x2.24 | x9.72 | x12.28 |
| SX126x p1, ATT v4 | x2.37 | x10.52 | x13.08 |
| SX126x p0 (busy 34636 ms), today | x4.47 | x19.44 | x24.55 |
| SX126x p0, ATT v4 | x4.74 | x21.04 | x26.16 |
| SX126x p3 (busy 4740 ms), today / ATT v4 | x1.00 / x1.00 | x2.66 / x2.88 | x3.36 / x3.58 |

SAFE is unaffected (ATT off, `DL:278`). **Choice: ATT v4 (59 octets user data)** - no stretching at
2.4 kbit/s in any mode or framing, keeps q_fus and q_grv from one ARU rebuild, one APID and
counter less to maintain (APID index table `TPC:40-56`, `HTI:828-837`, `kNumApids` `TP:50`). Docs to
update: `D01:928-930` offered loads (CRUISE 6.48, ENCOUNTER 35.99, TEST 44.74) and "67 % / 69.5 %"
-> "71.8 % / 74.1 %".

### 5.9 Ground computations and display

Conventions: both quaternions are device -> world, Android convention, scalar first as sent
(`DASH:1793-1797`, `SIM:1142-1146`); world Z = up; the GRV world's horizontal axes are arbitrary.
For q = (w, x, y, z):

* Up vector in body axes (gravity direction as the accelerometer sees it at rest):
  u = R(q)^T e_z = ( 2(xz - wy), 2(yz + wx), w^2 - x^2 - y^2 + z^2 ).
  Gravity vector g_b = 1 g x u. Linear acceleration (DC) = acc_mean - g_b (show with "+/-50 mg
  offset" caveat).
* Tilt (body +Z from vertical) = acos(u_z); roll = atan2(u_y, u_z); pitch = atan2(-u_x,
  sqrt(u_y^2 + u_z^2)) (Z-up body frame; state the convention on the page).
* Relative heading (6-DOF) = atan2(2(wz + xy), 1 - 2(y^2 + z^2)) of q_grv, labelled "relative,
  reference arbitrary, drifts".
* 9-DOF vs 6-DOF: dq = q_fus (x) conj(q_grv) (maps GRV world -> RV world; Hamilton product exists in
  `DASH:1789-1792`). Heading offset psi = atan2(2(dw dz + dx dy), 1 - 2(dy^2 + dz^2)); tilt
  disagreement delta = acos(1 - 2(dx^2 + dy^2)). Only when `IMU_F_PAIRED`. Trend of psi over passes
  (deg/h) = GRV drift + magnetic disturbance.
* Vibration: vib_rms (mg, 1 decimal) and vib_peak (mg); note bandwidth <= ODR/2 and the floor.
* Rates: rate_mean vector, |omega_mean|, spin axis = rate_mean / |omega_mean| (only when |omega_mean|
  > 10x the floor), rate_peak.
* Stationary (whole ATT period): imu_nwin >= 1 and rate_peak < T_omega and vib_peak < T_a; else
  "moving"; no windows -> "unknown". Proposed `[science]` keys `still_rate_dps = 0.5`,
  `still_vib_mg = 10` (`STOML:133-137`; derivation: 0.07 deg/s rms per axis x sqrt(3) ~ 0.12 deg/s,
  ~3 sigma peak ~0.36 deg/s; 1.2 mg rms per axis x sqrt(3) ~ 2.1 mg, ~3.5 sigma ~7 mg; VERIFY
  T-SC-14).
* Remove nothing legitimate from the existing ATT panel; add an "IMU (6-DOF)" panel in SCIENCE
  (`DASH:270-277`) with provenance labels (onboard / derived on the ground).
* Simulator (`SIM:1131-1150`): q_grv = Rz(psi_drift(t)) (x) q_fus with a slow drift (e.g. 2 deg/h),
  window stats from the true body rates, vib ~2 mg rms, and the F4 corrections.

### 5.10 Tests to add

| ID | Test | Pass |
|---|---|---|
| T-SC-13 | GRV frame self-consistency: still, six faces up | the ground's up-vector from q_grv agrees with acc_mean / |acc_mean| within 1 deg (proves the device -> world convention independently of the world axes; VERIFY item of `D08:783-797`) |
| T-SC-14 | 6-DOF noise floors at rest, 10 min | record vib_rms, rate_mean, rate_peak; set `still_*` thresholds; record gyro residual offset |
| T-SC-15 | 9-DOF vs 6-DOF | still for 1 h: heading offset drift (deg/h) <= the gyro bias stability order; key the transmitter: record the heading-offset step (magnetic disturbance) |
| T-SC-16 | ESLOV wiring | continuity J5-3/J2-2, J5-4/J2-1; J5-3/4 idle 3.2-3.3 V; JANALOG 5 voltage on VIN-only power |
| T-SC-17 | Bus separation | Nicla on A4/A5 only, RM3100 on Qwiic only (EVR 0x060B bus names); unplug the boom: Nicla unaffected, RM3100 loss EVR, HK bit cleared; reconnect: re-acquired |
| T-FD-x | Qwiic bus clear (if implemented) | hold Qwiic SDA low, release: EVR names Qwiic, bus recovers |
| Host | `ImuStats` (dedupe, gaps, RMS-of-RMS, peaks), `pack_att` v4 vector, `XC` lengths `[49, 8, 10, 46, 59, 129, 24]`, `IMU_F_` contiguity, `SENSOR_BITS` + ME_GRV, `TE2E` ATT flags, `TW` new panel | |
| CI | compile `nicla_sense_me_aru` for `arduino:mbed_nicla` with Arduino_BHY2 1.0.8 | |

---

## 6 Interface facts per instrument (datasheet-based, for the documentation writers)

| Instrument | Interface | Key facts | Source |
|---|---|---|---|
| Nicla Sense ME (ARU) | I2C target 0x2A on `Wire` (J2 pins 1/2 = ESLOV pins 4/3), polled pages | VIN 3.5-5.5 V, USB 4.8-5.5 V, VDDIO_EXT 1.8-3.3 V (3.3 V set by firmware), VIH 0.7 VDDIO_EXT, VIL 0.3 VDDIO_EXT, -40..85 C | `NDS:200-244`, `ARU:13-33` |
| BHI260AP | SPI to the nRF52 (internal) | 6-axis IMU + fusion; ranges, sensitivities, noise: 5.1 | `NDS:409`, `BHI` Tables 117-119 |
| BMM150 | I2C, BHI260AP is host (internal) | +/-1300 uT (x, y), +/-2500 uT (z), ~0.3 uT resolution | `BMM:40-41`, `BMM:232-233`, `BMM:310` |
| BMP390 | SPI to the BHI260AP (internal) | 300-1250 hPa; absolute accuracy typ +/-50 Pa (300-1100 hPa, 0-65 C), relative +/-3 Pa | `BMP:80-98` |
| BME688 | SPI to the BHI260AP (internal) | RH +/-3 %, T +/-0.5 C (0-65 C), T "typically above ambient" (self-heating), standard gas scan 10.8 s; BHI virtual RH 1 %RH LSB | `BME:101-125`, `BME:662-663`, `BHI` Table 88 |
| RM3100 (MagI2C) | I2C 0b01000 + SA1 SA0 -> 0x20..0x23; I2CEN high; DRDY optional | +/-800 uT; gain 20/38/75 LSB/uT at CC 50/100/200; noise 30/20/15 nT; max single-axis rate 1600/850/440 Hz (divide by 3 for 3 axes); supply 2.0-3.6 V, AVDD within 0.1 V of DVDD; SCL <= 1 MHz | `RMM:196-240`, `RMM:282-319`, `RMM:583-760`, `RMM:801-815` |
| RM3100 pin naming | Table 4-1: pin 3 SSN/SA0, pin 28 MISO/SA1; section 4.5 example "pin 3 HIGH and pin 28 LOW -> 0b0100001" agrees; section 4.3.3 text says SA1 = pin 3, SA0 = pin 28 (contradiction) - 0x20 (both low) and 0x23 (both high) are immune | `RMM:610-651`, `RMM:801-812`, `RMM:726-746` |
| AS7265X | I2C 0x49 (8-bit 0x92/0x93), SCL 0-400 kHz, 2.7-3.6 V with I2C; SparkFun board: Qwiic or 4 pins, I2C jumper = pull-ups, JP2 closed = UART | `ASD:1514`, `ASD:535`, `ASD:437`, `ASG:89-108`; device naming: driver/datasheet AS72651 = NIR (`ASH`), the hookup guide's "AS72651 (UV)" (`ASG:77`) conflicts |

---

## 7 Open questions / VERIFY

1. Electrical identity J5-3/4 = J2-2/1 and the translator on J5 (continuity test); Nicla on-board
   pull-ups (not found).
2. ESLOV 5 V input limits (not found); JANALOG 5 voltage with the UNO Q on VIN only (Q2801, `UDS:888`).
3. UNO Q Qwiic pull-ups present or not (not found); boom cable capacitance.
4. Implement the Qwiic bus clear (3.3) - needs `llext_check` on 0.90.0 and 1.x; or accept re-init
   only and keep the AS7265X off the boom bus.
5. Owner decision: RM3100 found on the wrong bus - use it with a WARN, or refuse it (3.5).
6. Owner decision: AS7265X documented on A4/A5 (recommended) or kept on Qwiic.
7. Which virtual sensors the Nicla's BHI260AP firmware lists (`hasSensor`; not found in sources).
8. BHI sub-sampling below 100 Hz: anti-alias filtering or plain decimation (not found) -> vibration
   bandwidth statement.
9. Gyro range: keep +/-2000 dps (LSB 0.061 deg/s) or set +/-250 dps for slow tumbles.
10. BSEC comp_t/comp_h/comp_g availability in 1.0.8 (5.3) - would free three slots and give RH at
    1/500 % but changes the temperature meaning (heat-compensated).
11. HK: keep `i2c_errors` as a sum or add per-bus counters (layout change).
12. Display precision of derived angles: Q14 gives ~0.007 deg, the fusion accuracy of the GRV is
    unknown (0 by datasheet) - propose 0.1 deg with an "accuracy not reported" note.
13. Measured Nicla current on 5 V with the ARU running (record in docs/21).
