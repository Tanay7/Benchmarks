# VGQ-1 / DSS-Q1 - a CCSDS deep-space-style telemetry link on Arduino UNO Q and VENTUNO Q

A complete, working model of a deep-space telemetry & telecommand system:

* **VGQ-1 spacecraft (transmitter)** - Arduino **UNO Q**: flight software on the STM32U585
  (CCSDS TM/TC, COP-1, SDLS, fault protection, solid-state recorder) and an EGSE recorder on its
  Linux side; instruments **Nicla Sense ME, Nicla Sense Env, AS7265X spectrometer and RM3100
  magnetometer(s)**; **EBYTE E22-400T37S** 5 W 433 MHz LoRa radio.
* **DSS-Q1 ground station (receiver)** - Arduino **VENTUNO Q**: the ground data system on its Linux
  side (frame sync, Reed-Solomon decoding, packet extraction, decommutation, limits, archive,
  COP-1 commanding, link budget and receiver statistics) with a detailed **web dashboard**; its
  MCU runs the **CardKB command keyboards**, station lamps and the radio control unit.

```
 UNO Q spacecraft ──CADUs: ASM + RS(255,223) + randomized TM frames──►  VENTUNO Q ground station
   E22-400T37S    ◄──CLTUs: BCH + SDLS-authenticated COP-1 TC frames──   E22-400T37S, web dashboard,
   4 instruments                         433 MHz LoRa                    CardKB keyboards
```

## Instruments and what they downlink

| Instrument | Data in telemetry |
|---|---|
| Nicla Sense ME | BHI260 fused orientation (quaternion + accuracy), accelerometer, gyroscope, BMM150 magnetometer, BMP390 pressure, BME688 temperature/humidity/gas resistance, BSEC IAQ / CO2-eq / bVOC; plus an independent TRIAD attitude computed on the spacecraft |
| Nicla Sense Env | HS4001 temperature & humidity, ZMOD4410 indoor air quality / TVOC / eCO2, ZMOD4510 outdoor AQI / NO2 / O3 |
| AS7265X | 18-band spectrum 410-940 nm, calibrated and raw, with gain / integration / lamp control and die temperatures |
| RM3100 (1 or 2) | science magnetometer on a boom: mean field and fluctuation; with two sensors the spacecraft's own field is separated from the ambient field |

Plus housekeeping, radio/telecom status, command verification, event reports, time correlation
and RF spectrum surveys. Full field list: [docs/05](docs/05_Telemetry_and_Command_Dictionary.md).

## Standards

CCSDS 131.0-B-5, 132.0-B-3, 133.0-B-2, 231.0-B-4, 232.0-B-4, 232.1-B-2, 301.0-B-4, 355.0-B-2
(details and trade-offs: [docs/11](docs/11_Standards_Status_and_Roadmap.md)).

## Documentation

| # | Document |
|---|---|
| 00 | [Beginner setup guide (step by step)](docs/00_Beginner_Setup_Guide.md) |
| 01 | [System design and concept of operations](docs/01_System_Design.md) |
| 02 | [Link budget and RF analysis](docs/02_Link_Budget_and_RF_Analysis.md) |
| 03 | [Hardware integration: wiring, pins, power](docs/03_Hardware_Integration.md) |
| 04 | [Space data link ICD](docs/04_Space_Data_Link_ICD.md) |
| 05 | [Telemetry & command dictionary (generated from the code)](docs/05_Telemetry_and_Command_Dictionary.md) |
| 06 | [Displays: web dashboard, station lamps, transmitter status](docs/06_Displays.md) |
| 07 | [Operations procedures](docs/07_Operations_Procedures.md) |
| 08 | [Verification and test plan](docs/08_Verification_and_Test.md) |
| 09 | [Regulatory and safety](docs/09_Regulatory_and_Safety.md) |
| 10 | [References](docs/10_References.md) |
| 11 | [Standards status and roadmap](docs/11_Standards_Status_and_Roadmap.md) |

## Repository map

```
spacecraft/
  vgq1_flight/                 App Lab app for the UNO Q
    sketch/                    flight software (STM32U585, arduino:zephyr:unoq)
      src/ccsds/               CRC, RS encoder, randomizer, TM framing, TC/CLTU/FARM-1, SHA-256, SDLS
      src/drivers/             E22 radio, I2C bus, RM3100, AS7265X, Nicla Sense Env, Nicla Sense ME link
      src/fsw/                 executive, packets, commands, attitude, recorder, status lights
      src/config.h             pins, addresses, radio defaults, limits
    python/main.py             Linux EGSE: CADU archive, boot counter, hardline commanding
  nicla_sense_me_aru/          firmware for the Nicla Sense ME (attitude reference unit)
ground/
  dssq/                        ground data system (Python, runs on the VENTUNO Q's Linux)
    ccsds/                     RS decoder, TM/TC, packets, time codes, SDLS
    gds.py framesync.py fop.py rxstats.py linkmgr.py radioctl.py sim.py replay.py cltu.py ...
    display/                   web dashboard, station I/O link
  station_io/                  App Lab app for the VENTUNO Q MCU: CardKBs, lamps, radio control unit
  config/station.toml          station configuration
  deploy/dssq-gds.service      systemd unit
  tests/                       pytest: CCSDS, flight<->ground cross-check, end-to-end, radio
tools/
  host_test/                   flight protocol code compiled and tested on a PC; firmware type check
  link_budget.py               link budget / horizon / Fresnel calculator
  gen_sdls_key.py              uplink authentication key for both ends
  gen_dictionary_doc.py        regenerates docs/05 from the code
```

## Try it in five minutes (no hardware)

```bash
cd ground
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m dssq.gds --sim
# open http://127.0.0.1:8080 and type NOOP in the COMMANDING tab
```

## Verification status

* `tools/host_test`: 39 checks of the flight C++ protocol code (RS, BCH, FARM-1, SHA-256/HMAC
  test vectors, SDLS, packet sizes, recorder, attitude, FP32 statistics) - passing.
* `ground/tests`: 32 tests including a **bit-exact flight-to-ground cross-check** and end-to-end
  runs against the software spacecraft - passing.
* `tools/host_test/check_firmware.sh`: every firmware translation unit type-checks against the
  Arduino API and the real instrument-library headers. The real build happens in Arduino App Lab
  (`arduino:zephyr:unoq` / `arduino:zephyr:ventunoq`), which these checks do not replace.
* Hardware tests to run on your boards: [docs/08](docs/08_Verification_and_Test.md).
