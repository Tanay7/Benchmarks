# VGQ-1 / DSS-Q1 - Project Summary

**A deep-space-style telemetry and telecommand link on two Arduino boards, built to the CCSDS
standards that space agencies use.** Repository:
[github.com/Tanay7/vgq1-deep-space-link](https://github.com/Tanay7/vgq1-deep-space-link).

## What it is

| | VGQ-1 spacecraft | DSS-Q1 ground station |
|---|---|---|
| Board | Arduino UNO Q (STM32U585 MCU + Qualcomm QRB2210 Linux) | Arduino VENTUNO Q (STM32H5F5 MCU + Qualcomm QCS8275 Linux) |
| MCU software | Flight software in C++: telemetry, commanding, security, fault protection, autonomy, instruments | Station I/O: keyboard console, lamps and buzzer, radio control, optional SX126x radio |
| Linux software | EGSE: CADU archive, boot epoch for encryption, file relay, hardline commanding | Ground data system in Python (standard library only) with an 11-tab web dashboard |
| Radio | 433 MHz LoRa: EBYTE E22-400T33D (2 W, default), E22-400T37S (5 W, selected by a jumper), or an SX126x on SPI | same model at both ends |
| Instruments | Nicla Sense ME (attitude, environment), AS7265X 18-band spectrometer, one or two RM3100 magnetometers | - |

Both ends are packaged as Arduino App Lab Apps that install without downloading anything.

## How it works

Measurements become CCSDS Space Packets, packed into 200-octet TM or USLP frames on four virtual
channels, protected by SDLS (AES-256-GCM, or authentication only for amateur bands),
Reed-Solomon (255,223) coded and sent as one 236-octet LoRa packet each. The ground synchronises,
repairs, verifies, decrypts, decodes and archives them, and serves over 2 000 live values to the
dashboard.
Commands go the other way through COP-1 (acknowledged, retransmitted, sequence-checked) as
BCH-coded CLTUs. Further capabilities: CFDP file delivery, CCSDS 121.0 lossless compression,
stored command sequences, beacon urgency levels, a solid-state recorder with playback, automatic
frequency correction and Doppler (SX126x), CCSDS Tracking Data Messages, an XTCE mission database
export, magnetometer calibration, spectroscopy with lock-in, HDR and burst modes, a receiver
data logger, a hardware watchdog on the flight MCU, and plain-text station identification at
both ends.

**Standards implemented:** CCSDS 131.0 (TM coding), 132.0 (TM), 133.0 (Space Packets), 231.0 /
232.0 / 232.1 (TC, COP-1), 301.0 (time codes), 355.0 (SDLS), 732.1 (USLP), 727.0 (CFDP class 1),
121.0 (compression), 503.0 (TDM), 660.0 / XTCE 1.2; FIPS-197 and NIST SP 800-38D (AES-GCM).

## How it is verified

| Layer | Result on the current tree |
|---|---|
| Flight C++ on a PC (host tests), strict warnings and AddressSanitizer + UBSan | 977 checks, 0 failures |
| Ground and EGSE test suite (Python 3.11 and 3.13) | 752 tests: 749 passed, 3 skipped (optional reference files) |
| Flight-to-ground cross-checks | every packet, frame, CLTU, SDLS frame and CFDP file bit-exact |
| Official CCSDS 121.0 test data | flight 56/56 and ground 74/74 files octet for octet |
| Firmware type-check in every build option + station and RouterBridge harnesses | 70 ok, 26 and 12 checks, negative controls fail as intended |
| Real Arduino builds (App Lab's compiler and board package) | 9 builds (both sketches, default and SX126x radio; the flight sketch on three platform versions, back to 0.90.0), 0 warnings in project code, every symbol resolvable by the board's loader |
| Adversarial review (18 reviewers with skeptic verification) | 62 confirmed findings fixed, with a regression test wherever one is feasible |
| Continuous integration | lint, host tests, ground tests and real builds on every push |

Engineering discipline: NASA/JPL "Power of Ten" rules for the flight C++ (no recursion, bounded
loops, no heap after start-up, every input range-checked); beyond the Arduino platforms, the
only third-party libraries are RadioLib and Arduino_RouterBridge (exact releases, audited,
minimally patched and bundled) and Arduino_BHY2 for the Nicla firmware; the ground runs on the
Python standard library; every assumption that hardware must confirm is tagged VERIFY with a
named test.

## Status

> **First bench link, downlink only.** All automated verification passes. The flight App has run
> on a UNO Q with its radio and the AS7265X, and the ground station received and decoded its
> downlink on the bench (2026-10-12/13, [21_Test_Records.md](21_Test_Records.md) records 2026-10-12-A
> and 2026-10-13-A); commands sent by hardline were accepted on the board (record 2026-10-13-A); the radio uplink has not been run yet. The 63 hardware and standards items (58 still open: power
> ladders, the frequency-error sign, sensor timings, platform behaviour, the watchdog, on-air
> identification, power draw, three standards checks; one closed by record 2026-10-13-A) are listed with their tests in
> [20_Known_Limitations.md](20_Known_Limitations.md) and
> [08_Verification_and_Test.md](08_Verification_and_Test.md); measured results will go into
> [21_Test_Records.md](21_Test_Records.md).

Start here: [README.md](../README.md) - [00_Beginner_Setup_Guide.md](00_Beginner_Setup_Guide.md) -
[01_System_Design.md](01_System_Design.md) - [FAQ.md](FAQ.md).
