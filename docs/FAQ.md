# Frequently Asked Questions

Short, plain-language answers. Each one ends with a link to the document that has the full
detail. Numbers come from the code (file names given) or the datasheets; where an earlier
explanation disagreed with the final code, the code's value is used here.

**General** - [What is this project?](#what-is-this-project) - [Is it really "deep space"?](#is-it-really-deep-space) - [Has it been tested on real hardware?](#has-it-been-tested-on-real-hardware)
**Radio and range** - [How far will it reach?](#how-far-will-it-reach) - [What about my 35 dBi antennas?](#what-about-my-35-dbi-antennas) - [Which spreading factor / profile should I use?](#which-spreading-factor--profile-should-i-use) - [How do I switch to the 5 W (37 dBm) module?](#how-do-i-switch-to-the-5-w-37-dbm-module)
**Data path** - [How does complex telemetry travel over a simple radio?](#how-does-complex-telemetry-travel-over-a-simple-radio) - [How big are the packets and frames?](#how-big-are-the-packets-and-frames) - [What is Rice compression?](#what-is-rice-compression) - [Where does the signal processing happen?](#where-does-the-signal-processing-happen) - [What commands can I send?](#what-commands-can-i-send)
**Security and rules** - [What is SDLS, in plain words?](#what-is-sdls-in-plain-words) - [Is encryption allowed? Which mode should I use?](#is-encryption-allowed-which-mode-should-i-use)
**Hardware** - [Why do four sensors on one I2C bus not clash?](#why-do-four-sensors-on-one-i2c-bus-not-clash) - [What happens if it gets hot?](#what-happens-if-it-gets-hot) - [What can the AS7265X really measure?](#what-can-the-as7265x-really-measure) - [How busy are the Linux processors?](#how-busy-are-the-linux-processors) - [Where is my data stored?](#where-is-my-data-stored)
**Software** - [What are the tests, in plain words?](#what-are-the-tests-in-plain-words) - [What does CI do?](#what-does-ci-do) - [What coding standard does the code follow?](#what-coding-standard-does-the-code-follow) - [Why are RadioLib and Arduino_RouterBridge bundled and patched?](#why-are-radiolib-and-arduino_routerbridge-bundled-and-patched) - [What is not implemented?](#what-is-not-implemented)

---

## General

### What is this project?
A complete model of a deep-space telemetry and telecommand link built from two Arduino boards:
the **VGQ-1 spacecraft** on an Arduino UNO Q (flight software on its microcontroller, a
recorder/"EGSE" on its Linux side, instruments Nicla Sense ME, AS7265X spectrometer, RM3100
magnetometers) and the **DSS-Q1 ground station** on an Arduino VENTUNO Q (ground data system and
web dashboard on Linux, keyboard and lamps on its microcontroller). They talk over a 433 MHz LoRa
radio using the CCSDS protocols real space agencies use. More: [README.md](../README.md),
[01_System_Design.md](01_System_Design.md).

### Is it really "deep space"?
The **protocols** are deep-space-style (CCSDS TM/TC, Reed-Solomon, COP-1, SDLS, CFDP, TDM, XTCE).
The **radio** is a UHF LoRa link meant for ground, mountain or balloon distances. For scale: at
the Moon (384 400 km) even the most sensitive SX126x profile would lack about 8 dB with a 12 dBi
Yagi and no fade margin (free-space budget from `dssq/link.py`).
[02_Link_Budget_and_RF_Analysis.md](02_Link_Budget_and_RF_Analysis.md).

### Has it been tested on real hardware?
**Not yet.** Every automated check passes (964 host checks; 749 ground tests, of which 746
pass and 3 skip for optional reference files; the firmware type-check; the real Arduino builds;
CI on every push), but the boards and radios have not been run together. Every value that only
hardware can confirm is tagged VERIFY and has a test.
[08_Verification_and_Test.md](08_Verification_and_Test.md), [20_Known_Limitations.md](20_Known_Limitations.md).

---

## Radio and range

### How far will it reach?
In free space the link budget is huge; on the ground the **horizon and terrain** limit you first.
Assumptions: E22-400T33D at 33 dBm, 2.4 kbit/s (sensitivity -128 dBm, datasheet), spacecraft
2.15 dBi whip with 0.5 dB cable, station 12 dBi Yagi with 1 dB cable, 433.125 MHz, 10 dB fade
margin (`tools/link_budget.py`):

| Scenario | Limit | Free-space margin at that distance |
|---|---|---|
| Both antennas 2 m above flat ground | radio horizon 11.7 km | 67 dB |
| Spacecraft 2 m, station mast 10 m | horizon 18.9 km | 63 dB |
| Hilltop 100 m to a 10 m mast | horizon 54 km | 54 dB |
| Balloon at 30 km altitude to a 10 m mast | horizon 727 km | 31 dB |
| Free space only (no horizon), T33D 2.4k | 8 385 km | 10 dB |
| Free space, SX126x profile 0 (SF12 / 31.25 kHz, -143.1 dBm computed) | about 47 700 km | 10 dB |

Trees, buildings and hills cost tens of dB, so real ground range is set by line of sight, not by
the radio. Run `python3 tools/link_budget.py --help` for your own numbers, and measure with test
T-FLD-01. [02_Link_Budget_and_RF_Analysis.md](02_Link_Budget_and_RF_Analysis.md).

### What about my 35 dBi antennas?
A 35 dBi gain at 433 MHz (wavelength 0.69 m) needs an antenna about **17 m across** (a parabolic
dish, G = efficiency x (pi D / wavelength)^2 with 55 % efficiency). A hand-held or rod antenna
labelled "35 dBi" cannot have that gain; use the measured or datasheet gain in
`[station] antenna_gain_dbi` (typical: a dipole or whip 2 dBi, a good 433 MHz Yagi 10-14 dBi).
[02_Link_Budget_and_RF_Analysis.md](02_Link_Budget_and_RF_Analysis.md).

### Which spreading factor / profile should I use?
The **E22 modules have no spreading-factor control**: they offer air rates, of which codes 0-2
are all 2.4 kbit/s (the most sensitive). The **SX126x option** has 8 LoRa profiles (the
"rate code" 0-7), all with coding rate 4/5 (`dssq/link.py`):

| Code | SF / BW | Bit rate | Air time per 236-octet CADU | Sensitivity (source) |
|---|---|---|---|---|
| 0 | SF12 / 31.25 kHz | 73 bit/s | 34.6 s | -143.1 dBm (computed) |
| 1 (default) | SF12 / 62.5 kHz | 146 bit/s | 17.3 s | -140.0 dBm (computed) |
| 2 | SF12 / 125 kHz | 293 bit/s | 8.7 s | -137.0 dBm (datasheet) |
| 3 | SF11 / 125 kHz | 537 bit/s | 4.7 s | -134.5 dBm (computed) |
| 4 | SF10 / 125 kHz | 977 bit/s | 2.2 s | -132.0 dBm (computed) |
| 5 | SF9 / 125 kHz | 1758 bit/s | 1.2 s | -129.5 dBm (computed) |
| 6 | SF8 / 125 kHz | 3125 bit/s | 0.66 s | -127.0 dBm (computed) |
| 7 | SF7 / 125 kHz | 5469 bit/s | 0.37 s | -124.0 dBm (datasheet) |

Rule: use the fastest profile that still leaves a comfortable margin (about 10 dB); every step
slower gains about 2.5-3 dB but halves the data. Change it with the ground directive
`LINKRATE <code>` (a coordinated, self-reverting change of both ends) or set `kRadioRateCode`
(config.h) and `[radio] air_rate` together. Profiles narrower than 62.5 kHz need TCXOs at both
ends. [02_Link_Budget_and_RF_Analysis.md](02_Link_Budget_and_RF_Analysis.md),
[07_Operations_Procedures.md](07_Operations_Procedures.md).

### How do I switch to the 5 W (37 dBm) module?
No code edit. Fit the E22-400T37S on its E22-400TBH-02 board, remove the board's M0/M1 and RXD/TXD
jumper caps, wire header 10 M0 -> D2, 7 M1 -> D3, 4 AUX -> D7, 5 TXD -> the UNO Q pin printed
"SDA", 6 RXD -> "SCL", 10 kOhm pull-ups from M0 and M1 to 3.3 V, and its own 4.5-15 V supply
(about 1.1 A at 12 V). Then **jumper header A0 to GND** (to GND only, never to 5 V) and power-cycle:
the flight reads A0 five times, 1 ms apart, at boot; a majority LOW selects the T37S. EVR 0x0308
and the RF packet's `radio_model` confirm it. On the T37S every power code is 37 dBm and the
module's fault reports are decoded. The station follows by itself with `[radio] model = "auto"`.
You need a licence for 5 W. [00_Beginner_Setup_Guide.md](00_Beginner_Setup_Guide.md),
[03_Hardware_Integration.md](03_Hardware_Integration.md).

---

## Data path

### How does complex telemetry travel over a simple radio?
The radio never sees "complex telemetry": it moves one block of **236 octets** at a time.
1. Each measurement becomes a CCSDS **Space Packet**: 6-octet header (APID = what it is, plus a
   sequence count) + 6-octet time + data. Housekeeping: 78 octets of data, 90-octet packet.
   Spectral bursts are Rice-compressed first.
2. Packets fill fixed **200-octet frames** (TM or USLP); a packet may continue into the next frame.
   The frame header names the virtual channel: VC0 engineering, VC1 science, VC2 recorder
   playback, VC3 files; idle frames carry the callsign.
3. **SDLS** encrypts the frame's data (AES-256-GCM) and adds a 16-octet tag (14-octet header).
4. **Reed-Solomon (255,223)** adds 32 parity octets (repairs up to 16 bad octets per frame), and a
   4-octet sync marker goes in front: 4 + 200 + 32 = **236 octets = one CADU = one LoRa packet**
   (fits the E22's 240-octet packet).
5. The E22 (or the SX126x) sends it. At 2.4 kbit/s one CADU keeps the E22 busy about 1.27 s
   (1.02 s on air + the 9600-baud UART); on SX126x profile 1 it takes 17.3 s.

The ground reverses every step: sync search, RS repair, SDLS check and decryption (forged or
replayed frames are dropped), packet re-assembly per virtual channel, decoding into named
engineering values, archive and web UI. The price is bandwidth: a frame carries 158 octets of
packets (188 without SDLS), and the spacecraft transmits at most 50 % of the time, so at 2.4 kbit/s
the downlink carries about 62 octets/s in ENCOUNTER and 31 octets/s in SAFE. Hence compression,
priorities and the on-board recorder. [04_Space_Data_Link_ICD.md](04_Space_Data_Link_ICD.md).

### How big are the packets and frames?
Every packet = 6 (header) + 6 (time) + data (`fsw/tlm_packets.h`, `ccsds/cfdp.h`, `fsw/spectral.h`):

| Packet | Data | Packet |
|---|---|---|
| HK | 78 | 90 |
| RF | 49 | 61 |
| CMDVER | 8 | 20 |
| TIMECORR | 10 | 22 |
| MAG | 46 | 58 |
| ATT | 37 | 49 |
| ENV | 24 | 36 |
| SPEC | 129 | 141 |
| EVR | 3 + text <= 80 | 15-95 |
| RFSCAN | 4 + <= 84 channels | 16-100 |
| SPECBURST | <= 124 | <= 136 |
| CFDP PDU | <= 146 | <= 158 (exactly the data space of one encrypted frame) |

Frames: TM 200 octets (data 188 in clear, 158 with SDLS AE); USLP 200 (data zone 183 / 153).
CADU 236. Uplink: TC packet with SDLS AE <= 27 octets (TC framing; 23 with USLP), TC frame <= 64,
CLTU <= 90.
[05_Telemetry_and_Command_Dictionary.md](05_Telemetry_and_Command_Dictionary.md).

### What is Rice compression?
Lossless compression from CCSDS 121.0-B-3, used for spectrometer bursts. Each sample is
predicted to equal the previous one and only the error is kept; signs are folded into small
positive numbers (0 -> 0, -1 -> 1, +1 -> 2, -2 -> 3, +2 -> 4); each value is then Rice-coded with
a parameter k: the low k bits as they are, the high part as that many 0s followed by a 1 (k = 1,
value 4 -> "0010": 4 bits instead of 16). Blocks of 16 samples; the best option is chosen per
block (including "all zero" and "no compression", so the output is never much larger than the raw
data); a full reference sample every 128 blocks limits the damage of a lost frame. The host
test's corpus shrinks from 53 844 to 24 028 octets (2.2x). The flight encoder reproduces all 56
official CCSDS test files it can take (n <= 16) and the ground decoder all 74, octet for octet.
[04_Space_Data_Link_ICD.md](04_Space_Data_Link_ICD.md).

### Where does the signal processing happen?

| Where | What |
|---|---|
| The radio chip / module | LoRa modulation and demodulation, LoRa CRC; the E22 also packs the UART data into LoRa packets; the SX126x measures RSSI, SNR and frequency error per packet |
| Spacecraft MCU (STM32U585) | Packets, frames, SDLS (AES-256-GCM / HMAC), Reed-Solomon encoding, randomizer and sync marker; CLTU / BCH decoding, SDLS checks, FARM-1; Rice compression; spectrometer exposure control and on-board spectral analysis; TRIAD attitude; fault protection |
| Spacecraft Linux (QRB2210, the EGSE) | No signal processing: archives every CADU, issues the SDLS epoch, restores the SDLS counter, relays files for CFDP, hardline commands |
| Station MCU (STM32H5F5) | Keyboard, lamps, buzzer, the E22's mode pins; with the SX126x option the radio driver, CAD and the AFC frequency offset |
| Station Linux (QCS8275) | Sync search, de-randomizing, Reed-Solomon decoding, SDLS decryption and checks, packet extraction, decoding, limits, receiver statistics, link budget, AFC loop, Doppler, TDM / XTCE, spectral analytics, magnetometer calibration fit, archive, web server. The browser draws the charts |

[01_System_Design.md](01_System_Design.md).

### What commands can I send?
32 spacecraft commands (opcodes 0x01-0x20) plus the COP-1 directives Unlock and Set V(R), and
ground directives. A command typed on the CardKB or in the COMMANDING tab is checked by the ground
(name, arguments, ranges), becomes a packet, is protected by SDLS, framed, BCH-coded into a CLTU and
radiated; COP-1 retransmits until the spacecraft acknowledges; the flight checks it again, runs it
and replies with CMDVER (accepted / rejected + error code + the command's source).
**8 hazardous commands** (marked *) need a second CONFIRM, optionally a second operator.

| Group | Commands |
|---|---|
| Link and radio | NOOP, PING, TXPWR, AIRRATE*, CHANNEL*, FPERIOD, RADIOOPT, TXINHIBIT*, RFSCAN, HIBERNATE* |
| Modes and safety | MODE (SAFE / CRUISE / ENCOUNTER / TEST), REBOOT* (key 0xB007), CMDLOSS* (default 24 h), FDIRMASK*, RSTCNT, EVRLEVEL, TIMECORR |
| Data | PKTRATE, PLAYBACK, SSRCLEAR*, CFDPPUT, CFDPCANCEL |
| Instruments | SENSORS, MAGCC, MAGBIST, MAGCAL, SPECCFG, SPECMODE, SPECBASE, SPECDIAG |
| Automation | SEQLOAD (one stored command per slot), SEQCTRL (START / STOP / CLEAR / DELETE) |
| Ground directives | LINKRATE, LINKCHAN (coordinated changes of both ends), WAKE, GSCAN, RADIO, LINKMGR, AFC, TDM, SPECREF, SPECLIB, SDLS RESETREPLAY |

[05_Telemetry_and_Command_Dictionary.md](05_Telemetry_and_Command_Dictionary.md),
[07_Operations_Procedures.md](07_Operations_Procedures.md).

---

## Security and rules

### What is SDLS, in plain words?
SDLS (CCSDS 355.0-B-2) **locks** (only key holders can read) and **seals** (any change is detected)
every frame. One random 256-bit master key, made once by `tools/gen_sdls_key.py`, is written
as two files - the flight header and the station's key file - and each file is copied to its
board; never into Git, never into a zip. Separate uplink and downlink keys are derived from it. AES-256-GCM encrypts and adds a 16-octet tag. Every frame
carries a sequence number that only goes up; anything not newer is refused. The nonce must never
repeat, so it contains a boot "epoch" the spacecraft's Linux side hands out at every boot; until it
has one, the spacecraft holds its encrypted frames, and after 120 s it sends clearly marked
unprotected frames rather than risk a repeat. Since the Phase C review an epoch value is offered at
most once ever: interleaving tests found 0 nonce reuses (the old code reused in 255 of 300 runs).
[17_Security_Model.md](17_Security_Model.md), [04_Space_Data_Link_ICD.md](04_Space_Data_Link_ICD.md).

### Is encryption allowed? Which mode should I use?
Encryption that hides the meaning of messages is **generally not permitted on amateur
frequencies**, and 2 W / 5 W at 433 MHz needs an amateur licence. Use **AUTH**: commands are
authenticated (nobody else can command the spacecraft) but stay readable; telemetry in clear.
AE (full encryption, the default) is only for where your licence allows it; NONE is for the
bench. Also set your callsign at both ends (`VGQ_CALLSIGN` in `config.h`, `[station] callsign`
in `station.toml`; both default to the placeholder `N0CALL`): the spacecraft and the ground
station both identify themselves with it in plain text.
[09_Regulatory_and_Safety.md](09_Regulatory_and_Safety.md).

---

## Hardware

### Why do four sensors on one I2C bus not clash?
All instruments share one bus, `Wire2` = I2C3 on the UNO Q's header pins A4 (SDA) and A5 (SCL),
3.3 V, 100 kHz. They cannot clash because (1) every device has its own 7-bit address - RM3100
outboard 0x20, RM3100 inboard 0x23, Nicla Sense ME 0x2A, AS7265X 0x49; (2) there is one controller,
the UNO Q, which talks to one device at a time; (3) the lines are open-drain: devices only pull
low and the pull-up resistors pull high, so no two outputs can fight. Use one set of 4.7 kOhm
pull-ups to 3.3 V (A4/A5 are not 5 V tolerant). The header pins printed "SDA/SCL" are the
radio's UART, not I2C. [03_Hardware_Integration.md](03_Hardware_Integration.md).

### What happens if it gets hot?
The Nicla's BME688 temperature rises (spacecraft bus limits: YELLOW at 60 degC, RED at 75 degC),
humidity usually drops, gas resistance / IAQ / CO2-equivalent change with fumes, pressure barely
changes. The AS7265X's die temperature rises (spectral drift) and the RM3100's offset drifts. Fault
protection: bus >= 75 degC sets the AVI_OVERTEMP flag (cleared 5 degC lower; a stale reading
clears it); an E22-400T37S PA over-temperature report (about 120 degC) switches the transmitter
off for 300 s and enters SAFE. On the link, crystal drift shows as frequency error (the SX126x AFC
tracks it) and the PA gives about 1 dB or less. Parts are typically rated -40..+85 degC. Heat-test
with a hair dryer at a distance - never an oven or a flame, and never heat lithium cells.
[09_Regulatory_and_Safety.md](09_Regulatory_and_Safety.md).

### What can the AS7265X really measure?
18 channels from 410 to 940 nm, each about 20 nm wide (+/-10 nm centre tolerance), integration
(N + 1) x 2.78 ms, gain 1 / 3.7 / 16 / 64x, field of view +/-20.5 deg, factory calibration with
diffuse light (+/-12 %), die temperature +/-8.5 degC. The sensor gives 18 numbers; everything else
is ground maths and is labelled: reflectance against a white reference and band ratios / change
detection (good), NDVI / GNDVI / NDRE (reasonable), colour XYZ / CCT ("approximate (18-band)"),
WBI and PRI ("proxy"), irradiance / PPFD / lux ("estimate"). It is not a laboratory
spectrometer. [20_Known_Limitations.md](20_Known_Limitations.md) S-01,
[13_Web_UI_Reference.md](13_Web_UI_Reference.md).

### How busy are the Linux processors?
**Ground (measured):** the simulator plus the full ground data system for 60 s used 0.81 s of CPU
(1.3 % of one core) and 29 MB of memory on the development PC, including the simulator's own
work. The station handles less than 200 octets/s: per frame a sync search, an RS decode, one
pure-Python AES-GCM decryption (about 0.3 ms on a PC), decoding, statistics and one database
write. The VENTUNO Q (Qualcomm QCS8275, 16 GB LPDDR5, 64 GB eMMC) has a large margin.
**Spacecraft EGSE (not measured):** light by design - one CADU copy at most every 1.5 s, a status
line per second, counter files. [01_System_Design.md](01_System_Design.md).

### Where is my data stored?
On the VENTUNO Q under `/home/arduino/ArduinoApps/dssq-groundstation/data/archive/`: `gds.sqlite`
(frames, packets, parameters, events, commands, the 1 Hz receiver series `rx_stats`), one folder per
UTC day with `cadu.bin` and `events.log`, `files/` (CFDP), `tdm/`, `exports/` (CSV). Retention 180
days / 16 GB, and raw logging pauses below 2 GB free. Each archived CADU takes 248 octets
(time + RSSI + length + CADU): about 8.5 MB per day of continuous contact in ENCOUNTER at 2.4 kbit/s.
An external NVMe or USB drive works only if the App container can see it (VERIFY).
[18_Data_Products.md](18_Data_Products.md).

---

## Software

### What are the tests, in plain words?
1. **Host tests:** the flight C++ compiled for a PC, 964 checks (standards' test vectors, official
   CCSDS data, radio maths, spectral, autonomy, integration streams), built twice: warnings as
   errors, and with memory and undefined-behaviour checkers.
2. **Ground tests:** 749 pytest tests on Python 3.11 and 3.13: the ground decodes exactly what
   the flight encodes (vectors the C++ writes), SDLS, COP-1, CFDP, products, magcal, AFC, web UI,
   data logger, station identification, and two documentation checks (every dashboard key is
   documented; every citation tag in the code is indexed).
3. **check_firmware.sh:** every Arduino file compiled against strict stand-ins in every radio,
   framing and SDLS option; all 80 RadioLib units for both boards; a negative control proving the
   stock RadioLib fails on the VENTUNO Q; bundled library copies verified.
4. **Harnesses:** the real station sketch on PC threads (26 checks) and the patched RouterBridge
   against its real dependencies (12 checks, the stock version must fail 4).
5. **Real builds:** the exact App Lab compiler and board package (arduino-cli + `arduino:zephyr`),
   0 warnings in our code.
6. **CI** repeats these on every push.

What none of them can prove: real range, wiring, I2C on a long cable, the RF environment - that is
what the hardware tests are for. [08_Verification_and_Test.md](08_Verification_and_Test.md).

### What does CI do?
One GitHub Actions workflow ([.github/workflows/ci.yml](../.github/workflows/ci.yml)) on every push
and pull request: **lint** (ruff, mypy, shellcheck, text-file hygiene), **host-tests** (host tests strict and
under sanitizers with the full official CCSDS 121.0 data, check_firmware.sh, the station harness
under sanitizers), **ground-tests** (Python 3.11 and 3.13) and **arduino-build** (both sketches,
default and SX126x builds, the bundled libraries must appear in the logs, the RouterBridge test and
its stack bound). [08_Verification_and_Test.md](08_Verification_and_Test.md) section 4.

### What coding standard does the code follow?
Flight C++ follows NASA/JPL "Power of Ten" discipline: no recursion, every loop has a fixed bound,
no heap allocation after start-up (except the recorder, allocated once at boot), every return
value checked, range checks on every external input (commands, radio, sensors, Bridge), no
type-punning, 32-bit floats only, no call that blocks the loop more than about 20 ms, warning-free
with -Wall -Wextra -Wshadow. Python: standard library only at run time, type hints, no bare
`except`, validated inputs, deterministic tests, ruff and mypy clean. Every module starts with a
header naming its purpose and the standard or datasheet section it implements.
[16_Developer_Guide.md](16_Developer_Guide.md).

### Why are RadioLib and Arduino_RouterBridge bundled and patched?
So the boards never download anything and always build with the exact, audited version.
**RadioLib 7.8.1** (SX126x driver): the patch adds the VENTUNO Q platform entry (the stock library
does not compile there), forbids heap use per SPI transfer and excludes unused modules.
**Arduino_RouterBridge 0.4.3** (the MCU-Linux RPC library): the patch raises the RPC thread's stack
from 500 to 1536 octets (measured worst case 1084) and makes "safe" handlers run only on the loop
thread, as the library's README promises. `tools/libbundle.py` fetches the exact tag, checks its
commit and a SHA-256 of the pristine files, applies the patch and writes `PROVENANCE.txt`;
`sketch.yaml` points arduino-cli at the bundled copy, and CI fails otherwise. The sketches' own RPC
mailbox stays as defence in depth. [22_Bundled_Libraries.md](22_Bundled_Libraries.md).

### What is not implemented?
DSN-style features left for later: pass reports, a BER/FER test mode with known frames, predicts
and sequence of events, acquisition sweep, uplink Doppler pre-compensation, station G/T estimate,
operator alerts by e-mail, SLE-style frame delivery to Yamcs / OpenMCT, light-time-aware
commanding; hardware ideas: an SDR spectrum recorder, antenna rotator tracking, a second station
for diversity, GPS-disciplined station time. Not feasible with these radios: true ranging (the
SX126x has none; 1 ms of timing = 150 km), delta-DOR, carrier-phase tracking.
[11_Standards_Status_and_Roadmap.md](11_Standards_Status_and_Roadmap.md).
