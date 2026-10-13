# 19 - Glossary and Acronyms

Every acronym and special term used in the code and the documentation, in alphabetical order.
Where a term has a project-specific value, the value and its source are given. CCSDS standard
numbers (for example 132.0-B-3) are listed with their full titles in
[10_References.md](10_References.md).

Jump to: [A](#a) [B](#b) [C](#c) [D](#d) [E](#e) [F](#f) [G](#g) [H](#h) [I](#i) [K](#k)
[L](#l) [M](#m) [N](#n) [O](#o) [P](#p) [Q](#q) [R](#r) [S](#s) [T](#t) [U](#u) [V](#v) [W](#w)
[X](#x) - [Units and link quantities](#units-and-link-quantities)

---

### A

| Term | Meaning |
|---|---|
| AAD | Additional Authenticated Data: the part of a frame that AES-GCM authenticates but does not encrypt. Here the frame primary header (or the USLP TFPH) plus the SDLS security header |
| ABX00162 / ABX00181 | Arduino SKUs of the UNO Q and the VENTUNO Q (their datasheets are the board references) |
| AD / BD / BC frames | COP-1 telecommand frame types. **AD**: sequence-controlled (numbered, retransmitted until acknowledged); **BD**: bypass (no sequence check, e.g. hardline commands); **BC**: bypass control frames carrying the COP-1 directives Unlock and Set V(R) |
| AE | Authenticated Encryption: the SDLS service that encrypts and authenticates, here AES-256-GCM (TC SPI 2, TM SPI 3). The default mode; not for amateur bands ([09_Regulatory_and_Safety.md](09_Regulatory_and_Safety.md)) |
| AES, AES-256 | Advanced Encryption Standard (FIPS-197), a block cipher; AES-256 uses a 256-bit key |
| AFC | Automatic Frequency Correction: the ground loop that re-tunes the SX126x station receiver to the measured frequency offset (`dssq/link.py AfcLoop`, contract 24.8). SX126x option only |
| AGC | Automatic gain control; for the AS7265X it means automatic exposure (gain code 255): the spectrometer steps gain, then integration time, to keep the brightest channel at 25-75 % of full scale |
| AOS / LOS | Acquisition Of Signal / Loss Of Signal: the start and end of a pass as the ground sees it. LOS is declared after `max(los_timeout_s, 3 x frame period)` without a frame |
| APID | Application Process Identifier: the 11-bit field in a Space Packet header that says what the packet is (HK 0x010, RF 0x011, MAG 0x020, ATT 0x021, SPEC 0x022, ENV 0x023, SPECBURST 0x024, CFDP 0x030, ...; [05_Telemetry_and_Command_Dictionary.md](05_Telemetry_and_Command_Dictionary.md)) |
| App, App Lab | Arduino App Lab, the tool that builds and runs an "App" on the UNO Q / VENTUNO Q: one folder with `app.yaml`, a Python part (run in a container on the board's Linux side) and an Arduino sketch (run on the board's MCU). This project is two Apps: `vgq1-spacecraft` and `dssq-groundstation` |
| ARU | Attitude Reference Unit: the Nicla Sense ME running this project's firmware (`nicla_sense_me_aru`), read over I2C at address 0x2A |
| ASM | Attached Sync Marker: the 32-bit pattern 0x1ACFFC1D in front of every coded frame, which the receiver searches for (CCSDS 131.0-B) |
| ASan / UBSan / TSan | AddressSanitizer, UndefinedBehaviorSanitizer, ThreadSanitizer: compiler instrumentation that stops a test on a memory error, undefined behaviour or a data race |
| AUTH | The SDLS authentication-only service: HMAC-SHA-256 truncated to 128 bits, SPI 1. Commands stay readable; the amateur-band choice |
| AUX | E22 output pin: LOW while the module is busy |
| AS7265X | ams 18-channel spectral sensor chipset (AS72651 NIR, AS72652 VIS, AS72653 UV) on the SparkFun Spectral Triad board, I2C address 0x49 |

### B

| Term | Meaning |
|---|---|
| Beacon level | Deep Space 1 "Beacon Monitor" idea: one number that says how urgently the spacecraft needs attention - 0 NOMINAL, 1 INTERESTING, 2 IMPORTANT, 3 URGENT - latched until the next accepted command (HK `beacon_level`) |
| BCH(63,56) | Bose-Chaudhuri-Hocquenghem code protecting each 7-octet block of a CLTU; corrects any single-bit error per codeblock (CCSDS 231.0-B) |
| BHI260AP, BMM150, BMP390, BME688 | Bosch parts on the Nicla Sense ME: sensor-fusion IMU, magnetometer, barometer, gas / humidity / temperature sensor |
| BIST | Built-In Self-Test (RM3100 register 0x33, command `MAGBIST`) |
| Bit-exact | Two implementations produce identical octets. The ground decodes vectors produced by the flight C++ and must match them exactly |
| BOR / brown-out | A supply dip below the brown-out reset level resets the MCU; reported as HK `reset_cause` 4 BROWNOUT |
| Bridge, Router Bridge | Arduino's MCU <-> Linux RPC link on the Q boards (`arduino-router` service, library Arduino_RouterBridge on the MCU, `arduino.app_utils.Bridge` in Python). Calls: `notify` (one-way), `provide` / `provide_safe` (handlers), `call` |
| BSEC | Bosch Sensortec Environmental Cluster: the BME688 software that computes IAQ, CO2-equivalent and breath-VOC |
| BUSY | SX126x output pin: HIGH while the chip cannot accept an SPI command |
| BW | Bandwidth. For LoRa: the chirp bandwidth (31.25, 62.5 or 125 kHz in this project) |

### C

| Term | Meaning |
|---|---|
| C/N0 | Carrier-to-noise-density ratio, dB-Hz: received power minus noise power per hertz |
| CAD | Channel Activity Detection: the SX126x's fast check for a LoRa preamble on the channel, used for listen-before-talk |
| CADU | Channel Access Data Unit: ASM + Reed-Solomon codeblock. Here 4 + 232 = **236 octets**, exactly one LoRa packet (`ccsds_types.h kCaduLen`) |
| CardKB | M5Stack's mini keyboard (I2C address 0x5F) used as the station's operator console |
| CCSDS | Consultative Committee for Space Data Systems: the space agencies' standards body whose Blue Books this project implements |
| CCT, Duv | Correlated colour temperature (K) and the distance from the Planckian locus; ground colorimetry, labelled "approximate (18-band)" |
| CFDP | CCSDS File Delivery Protocol (727.0-B-5). Class 1 = unacknowledged: Metadata, File Data and EOF PDUs, checksum, no retransmission. Files travel on VC 3 |
| CI | Continuous integration: the GitHub Actions workflow that runs the tests on every push ([08_Verification_and_Test.md](08_Verification_and_Test.md) section 4) |
| Citation tags | References in code comments to the project's design records: "contract N" (section N of the v3 interface contract), "PB-x.y" (section x.y of the Phase B integration specification), "C-nn" (its conflict register), "F-nn" (its follow-up list), "I1"-"I4" (the interface decisions taken with the Phase C fixes). The records themselves are not part of the repository; every tag is indexed in [12_Design_Decisions.md](12_Design_Decisions.md#appendix-b---citation-tags-in-code) appendix B, kept in step with the code by `tests/test_citations.py`. Not to be confused with the Phase C finding IDs C1-C62 (no hyphen) |
| CLCW | Communications Link Control Word: the 4-octet report of the spacecraft's FARM-1 state (V(R), Lockout, Wait, Retransmit, FARM-B counter) carried in every TM frame's OCF |
| CLTU | Communications Link Transmission Unit: start sequence + BCH codeblocks + tail sequence around a TC frame; at most 90 octets here (a 64-octet TC frame: 2 + 10 x 8 + 8) |
| CMDVER | Command verification packet: the spacecraft's ACCEPTED / EXECUTED / FAILED report for each command, with an error code and the command source (AD, BD / hardline, stored sequence) |
| Codeblock | One Reed-Solomon codeword as sent: 200-octet frame + 32 parity octets = 232 octets (shortened RS(255,223), 23 virtual fill octets) |
| Concatenated coding | Two codes in series: on the SX126x path LoRa's own inner code plus Reed-Solomon as the outer code ("RS rescue") |
| COP-1 | Communications Operation Procedure-1 (CCSDS 232.1-B-2): the uplink's acknowledge-and-retransmit protocol, FOP-1 on the ground and FARM-1 on the spacecraft |
| CR | LoRa coding rate; 4/5 in every profile here |
| CRC, CRC-16 | Cyclic Redundancy Check. The frame CRC is CRC-16/CCITT-FALSE (check value 0x29B1) in the FECF |
| CRUISE | Flight mode: routine cadence, frame period = 2.5 x radio busy time (40 % duty) |
| CSP | Content Security Policy: the web server tells the browser to run only the dashboard's own script |
| CUC | CCSDS Unsegmented time Code (301.0-B-4). Every packet carries 6 octets: 4 coarse (seconds since the MCU boot) + 2 fine (1/65536 s), implicit P-field 0x2E |

### D

| Term | Meaning |
|---|---|
| dBm, dBi, dBW | Power relative to 1 mW; antenna gain relative to an isotropic radiator; power relative to 1 W (TDM `CARRIER_POWER`) |
| DE | "From" in amateur-radio identification. The spacecraft sends `DE <callsign>` (EVR 0x0002) and `DE <callsign> VGQ-1` (OID frames); the ground sends `DE <callsign> <name>` on the uplink (see Station identification) |
| DEV_SEL | AS7265X virtual register that selects which of the three devices the next register access addresses |
| DIO1 / DIO2 / DIO3 | SX126x digital I/O pins: interrupt (DIO1), optional RF-switch control (DIO2), TCXO supply (DIO3) |
| Doppler, range-rate | The frequency shift caused by relative motion; range-rate = -c x Doppler / f (positive = moving apart). Measured only with the SX126x option |
| DRDY | Data ready (RM3100 STATUS bit 7; AS7265X CONFIG DATA_RDY) |
| DSN | NASA's Deep Space Network, the model for the ground station's concepts |
| DSS-Q1 | This project's ground station (named in the style of the DSN's Deep Space Stations), on the VENTUNO Q |

### E

| Term | Meaning |
|---|---|
| E22-400T33D / T37S / M33S | EBYTE 433 MHz LoRa modules. T33D: 2 W, UART, the default at both ends. T37S: 5 W, UART, on the E22-400TBH-02 test board, selected by the A0 strap. M33S: SX1268 + PA/LNA on SPI, the reference module of the SX126x option |
| Eb/N0 | Energy per bit over noise density (dB) |
| EGSE | Electrical Ground Support Equipment: the "bench rack" software on the UNO Q's Linux side (`vgq1-spacecraft/python/main.py`): CADU archive, SCLK partition / SDLS epoch, SN restore, CFDP relay, hardline commanding |
| EIRP / e.r.p. | Equivalent isotropically radiated power = transmit power - cable loss + antenna gain (dBi). Effective radiated power is referenced to a dipole: e.r.p. = EIRP - 2.15 dB |
| ENCOUNTER | Flight mode: highest cadence, frame period = 2 x busy time (50 % duty) |
| ENU / NED | East-North-Up / North-East-Down reference frames (Nicla fusion world frame / TRIAD frame) |
| Epoch (SDLS TM) | The 16-bit number in every downlink nonce, equal to the SCLK partition: a new value at every MCU boot so no nonce is ever reused |
| ERT / SCET / OWLT | Earth Received Time (when the ground got a frame), Spacecraft Event Time (when it happened on board), One-Way Light Time: SCET = ERT - OWLT - radio latency |
| EVR | Event report: a numbered message from the spacecraft (ID 0xNNNN, severity DIAG / INFO / WARN / ERROR / FATAL, text <= 80 characters). Catalogue: [14_Troubleshooting_and_Event_Response.md](14_Troubleshooting_and_Event_Response.md) |
| EWMA | Exponentially weighted moving average (spectral change-detection baseline, alpha 0.1) |

### F

| Term | Meaning |
|---|---|
| FARM-1 | Frame Acceptance and Reporting Mechanism: the spacecraft half of COP-1 (states OPEN, WAIT, LOCKOUT) |
| FARM-B counter | 2-bit counter in the CLCW that counts accepted bypass frames |
| FDIR | Fault Detection, Isolation and Recovery: the flight's fault protection (16 flags in HK `fdir_flags`, e.g. CMD_LOSS, PA_OVERTEMP, I2C_BUS) |
| FECF | Frame Error Control Field: the 2-octet CRC-16 at the end of TM, USLP and TC frames |
| FER / BER / CER / SER / PER | Frame, bit, codeword, symbol and packet error rates. FER = lost / (good + lost) |
| FERR_L | The largest transmitter-receiver frequency offset the SX126x tolerates: +/-25 % of BW and, at SF10-12, +/-200/100/50 ppm (the tighter applies); +/-7.8 kHz at SF12 / 31.25 kHz |
| FHP | First Header Pointer: where the first packet header starts inside a frame's data field (0x7FF / 0xFFFF = none), so packets can span frames |
| FIPS, NIST SP 800-38D | US standards: FIPS-197 (AES), FIPS 180-4 (SHA-256), SP 800-38D (GCM) |
| FLUOR | Spectrometer mode 3: UV-LED excitation, lamp-on minus lamp-off; channels <= 460 nm masked as the excitation band |
| FOP-1 | Frame Operation Procedure: the ground half of COP-1 (numbering, retransmission, timer T1, lockout handling) |
| FQBN | Fully Qualified Board Name for arduino-cli: `arduino:zephyr:unoq`, `arduino:zephyr:ventunoq` |
| FSPL | Free-space path loss = 20 log10(4 pi d f / c) = 32.44778 + 20 log10(d / km) + 20 log10(f / MHz) dB |
| FWHM | Full width at half maximum: a filter's bandwidth (about 20 nm for each AS7265X channel) |

### G

| Term | Meaning |
|---|---|
| GCM | Galois/Counter Mode (SP 800-38D): AES in counter mode plus a 16-octet authentication tag. **A nonce must never repeat under one key** |
| GDS | Ground Data System: the station's Python software (`dssq`) - frame sync, decoding, archive, commanding, web UI |
| GSCAN / RFSCAN | Noise surveys over channels 0-83: GSCAN with the station radio (ground directive), RFSCAN with the spacecraft radio (command) |

### H

| Term | Meaning |
|---|---|
| Hardline / umbilical | Commanding over a cable instead of RF: a CLTU line placed in the EGSE's `hardline_queue.txt` goes to the flight's normal command path (`python -m dssq.cltu`) |
| Hazardous command | One of the 8 commands that need a CONFIRM step: AIRRATE, SSRCLEAR, REBOOT, CMDLOSS, FDIRMASK, TXINHIBIT, HIBERNATE, CHANNEL |
| HDR | Spectrometer mode 1, high dynamic range: 2-4 bracketed exposures merged per channel |
| Hibernation | Spacecraft power-saving state: duty-cycled listening and one HK beacon every `beacon_s`; ended by the ground's WAKE |
| HK | Housekeeping: the spacecraft's 78-octet health packet (APID 0x010) |
| HMAC | Hash-based Message Authentication Code (RFC 2104 / 4231); HMAC-SHA-256 is used for AUTH and for deriving the AE keys |
| Host mode | Running the ground data system as a systemd service on the VENTUNO Q's Linux instead of in App Lab (`dssq-groundstation/host/`) |
| Host test | The flight C++ compiled and checked on a PC (`tools/host_test`) |

### I

| Term | Meaning |
|---|---|
| I2C | Two-wire sensor bus. Spacecraft: `Wire2` = I2C3 on UNO Q header A4 (SDA) / A5 (SCL), 3.3 V, 100 kHz |
| IAQ | Index for Air Quality (0-500) from BSEC |
| ICD | Interface Control Document ([04_Space_Data_Link_ICD.md](04_Space_Data_Link_ICD.md)) |
| IV, nonce | Initialisation vector: the 12-octet number GCM needs once per frame. Here: direction ('TC' / 'TM') + epoch + 0 + sequence number |
| IWDG | Independent watchdog of the STM32U585: a down-counter on the LSI clock that resets the MCU unless it is refreshed in time. Armed at the end of `setup()` with a 40 s timeout (`config.h VGQ_IWDG`, `kIwdgTimeoutMs`), refreshed once per `loop()` pass, paused while a debugger halts the core; the next boot reports `reset_cause` 3 WATCHDOG. EVR 0x060C if its settings are not confirmed at start-up |

### K

| Term | Meaning |
|---|---|
| kTB | Thermal noise power -174 dBm/Hz + 10 log10(B) at 290 K |
| KVN | Keyword = Value Notation, the text format of the CCSDS TDM files |

### L

| Term | Meaning |
|---|---|
| LBT | Listen before talk: transmit only when the channel is free (E22 LBT, SX126x CAD; `RADIOOPT`) |
| LDRO | LoRa Low Data Rate Optimisation, switched on when a symbol lasts >= 16.38 ms |
| Link budget | The sum of gains and losses from transmitter to receiver; margin = received power - sensitivity ([02_Link_Budget_and_RF_Analysis.md](02_Link_Budget_and_RF_Analysis.md)) |
| LOCKIN | Spectrometer mode 2: lamp-on / lamp-off pairs averaged, rejecting ambient light |
| LoRa | Semtech's chirp spread-spectrum modulation. Slower settings (higher SF, narrower BW) are more sensitive |
| LSB / MSB | Least / most significant bit; also "per LSB" for a sensor's scale |
| LSI | Low-speed internal RC oscillator of the STM32 (32 kHz nominal, `kIwdgLsiHz`), the IWDG's clock; not trimmed, so the watchdog timeout varies with it |

### M

| Term | Meaning |
|---|---|
| M0 / M1 | E22 mode pins: M0 = 0, M1 = 0 normal (mode 0); M0 = 1, M1 = 0 WOR (mode 1); M0 = 0, M1 = 1 configuration (mode 2, always 9600 8N1); M0 = 1, M1 = 1 deep sleep (mode 3) (E22 manuals 6.1). Pulled up to 3.3 V so the module sleeps while the MCU boots |
| MAC | Message authentication code (the 16-octet tag), not "medium access control" here |
| MAGCAL | Magnetometer calibration: hard-iron offsets and soft-iron matrix fitted on the ground and uplinked (command `MAGCAL`, 0x20) |
| MAP ID | USLP Multiplexer Access Point identifier (always 0 here) |
| MCFC / VCFC | Master Channel Frame Count / Virtual Channel Frame Count: 8-bit frame counters used to detect lost frames |
| MCS | Mission Control System, e.g. Yamcs or OpenC3 (targets of the XTCE export and the UDP interop) |
| MCU / MPU | Microcontroller (STM32U585 on the UNO Q, STM32H5F5 on the VENTUNO Q) / the Linux processor (Qualcomm QRB2210 on the UNO Q, QCS8275 on the VENTUNO Q) |
| Mailbox (RPC) | The sketches' single-slot queue that makes every Bridge handler run on the loop thread |

### N

| Term | Meaning |
|---|---|
| N(S), N(R), V(S), V(R) | COP-1 sequence numbers: N(S) in each AD frame, V(R) the next number the FARM expects, N(R) its report in the CLCW, V(S) the FOP's next number |
| NDVI, GNDVI, NDRE, WBI, PRI | Vegetation and water indices from the spectrum (NDVI = (R860 - R680) / (R860 + R680), ...); WBI and PRI are proxies here |
| NF | Noise figure of a receiver (6 dB assumed for the SX126x computed sensitivities) |
| Noise floor | The received power with no signal (E22 noise register, SX126x median of 8 RSSI samples) |
| NRST, NSS | SX126x reset pin; SPI chip select |
| Null SA | SDLS security association SPI 0: header and trailer present but all zero, data in clear; the TM fallback when no epoch arrives within 120 s |

### O

| Term | Meaning |
|---|---|
| OCF | Operational Control Field: 4 octets at the end of every TM / USLP frame carrying the CLCW |
| OCP | Over-current protection of the SX126x PA (140 mA set, VERIFY for the SX1268) |
| OID frame | Only Idle Data frame: sent when nothing else is queued (TM VC 7, USLP VCID 63); carries the clear-text identification "DE <callsign> VGQ-1" and the CLCW |

### P

| Term | Meaning |
|---|---|
| PA / LNA | Power amplifier / low-noise amplifier |
| PA-off window | The time between transmissions; sensor reads that disturb or are disturbed by the radio happen only then |
| Partition (SCLK) | The SCLK restarts at every MCU boot; each boot gets a new partition number from the EGSE (HK `sclk_partition`), as on JPL spacecraft clocks |
| PDU | Protocol Data Unit (CFDP Metadata, File Data, EOF) |
| Phase A-D | The project's development phases: A protocol / driver work, B integration, C adversarial review and fixes, D documentation and packaging |
| Playback | Sending the SSR's recorded packets on VC 2 (`PLAYBACK START`) |
| Power of Ten | NASA/JPL coding rules followed by the flight C++: no recursion, bounded loops, no heap after start-up, checked return values, ... ([16_Developer_Guide.md](16_Developer_Guide.md)) |
| PPFD / PAR | Photosynthetic photon flux density (umol m-2 s-1) over the photosynthetically active range 400-700 nm (estimate) |
| Profile (SX126x) | The SX126x "rate code" 0-7: (SF, BW, CR) = (12, 31.25 kHz, 4/5) ... (7, 125 kHz, 4/5) |

### Q

| Term | Meaning |
|---|---|
| Qwiic | SparkFun's I2C connector system; used only if the AS7265X is plugged into the UNO Q Qwiic socket (optional, D-13) |

### R

| Term | Meaning |
|---|---|
| RadioLib | Open-source radio library (v7.8.1) used for the SX126x option, bundled patched ([22_Bundled_Libraries.md](22_Bundled_Libraries.md)) |
| Randomizer | CCSDS pseudo-random sequence XORed onto the frame so the radio sees enough bit transitions (prefix FF 48 0E C0 9A) |
| RCU | Radio Control Unit: the station MCU driving the ground E22's M0 / M1 / AUX pins |
| Real build | Compiling with the actual Arduino toolchain (arduino-cli + `arduino:zephyr`), as App Lab does |
| Rice / CCSDS 121.0 | Lossless compression (Adaptive Entropy Coder with a unit-delay predictor) used for SPECBURST; never much larger than the raw data |
| RM3100 | PNI geomagnetic sensor; outboard unit at I2C 0x20 (boom tip), optional inboard unit at 0x23 |
| RPC | Remote procedure call (see Bridge) |
| RS(255,223) | Reed-Solomon code of the downlink: 32 parity octets, corrects up to 16 wrong octets per codeblock (CCSDS 131.0-B) |
| RS rescue | SX126x option: a LoRa packet with a failed LoRa CRC is still handed to the RS decoder; if RS repairs it, the frame is used (counter `rs_rescued`) |
| RSSI | Received Signal Strength Indicator, dBm (E22 1 dB steps, SX126x 0.5 dB); calibrated with `[radio] rssi_cal_offset_db` |
| RSTCNT | Command that resets the command / TC / CLTU counters and the loop statistics and clears the event-latched FDIR flags CMD_LOSS, LOOP_OVERRUN, RATE_REVERT and SDLS_AUTH (`kFdirClearedByRstcnt`); flags that follow a state stay |

### S

| Term | Meaning |
|---|---|
| SA / SPI (SDLS) | Security Association / Security Parameter Index: which key and algorithm protect a frame. SPI 1 = AUTH, 2 = TC AE, 3 = TM AE, 0 = null SA. Not the SPI bus |
| SAFE | Flight mode after every boot and every fault response: slowest cadence (frame period = 4 x busy time, 25 % duty) |
| SANA | Space Assigned Numbers Authority (CCSDS registries); SCID 0x0A7 is a private, unregistered value |
| SCID | Spacecraft Identifier, 0x0A7 (10-bit TM/TC field, 16-bit USLP field) |
| SCLK | Spacecraft clock: seconds since the MCU boot, in every packet's CUC time |
| SDLS | Space Data Link Security (CCSDS 355.0-B-2): authentication and encryption of whole frames with anti-replay sequence numbers |
| SEM | Standard error of the mean = standard deviation / sqrt(n), shown as +/- next to window means |
| SF | LoRa spreading factor 7-12; each step up roughly halves the bit rate and gains about 2.5 dB of sensitivity |
| SEQLOAD / SEQCTRL | Commands that load and control the on-board stored command sequence (32 slots, RAM only) |
| SN | Sequence number (SDLS anti-replay): TC 32-bit from the ground's `sdls_sn.txt`; TM per boot from 1 |
| SNR | Signal-to-noise ratio (dB); the SX126x measures it per packet (0.25 dB steps), the E22 only allows an estimate (RSSI - noise) |
| SPI (bus) | Serial Peripheral Interface: the SX126x connection (SCK, MOSI, MISO, NSS) |
| SSR | Solid-State Recorder: the spacecraft's RAM packet store (up to 256 KiB), played back on VC 2 or exported over CFDP |
| Station identification | Plain-text identification of a transmitter with its callsign, required by amateur rules. Spacecraft: EVR 0x0002 every 600 s and the OID frames (`config.h VGQ_CALLSIGN`). Ground: `DE <callsign> <name>` after the first uplink, at least every 10 minutes while it transmits and at the end of a session (`[station] callsign`, `dssq/gds.py StationIdent`); the spacecraft counts it in RF `cltu_bad` and ignores it ([09_Regulatory_and_Safety.md](09_Regulatory_and_Safety.md) section 1.3) |
| SURVEY / BURST | Spectrometer modes 0 (one exposure, AGC) and 4 (continuous sampling up to 60 s into SPECBURST packets) |
| SX1261 / SX1262 / SX1268 | Semtech LoRa transceiver chips. The SX1268 is in the reference module (E22-400M33S) and the default of the SX126x option; the SX1262 is supported too (`VGQ_SX126X_CHIP`, `DSSQ_SX_CHIP`); the E22-400T33D is built around an SX1262 |
| Sync states | The frame synchroniser's SEARCH (looking for the ASM), LOCK, FLYWHEEL (tolerating misses); PACKET for the SX126x, which delivers whole packets |

### T

| Term | Meaning |
|---|---|
| T1 | FOP-1 retransmission timer (`[commanding] t1_s`, default 45 s; raised to at least 2 x the frame period) |
| TC / TM | Telecommand (uplink, ground to spacecraft) / telemetry (downlink). TC frame <= 64 octets (232.0-B-4); TM frame 200 octets (132.0-B-3) |
| TCXO | Temperature-compensated crystal oscillator; needed at both ends for SX126x profiles narrower than 62.5 kHz |
| TDM | Tracking Data Message (CCSDS 503.0-B-2): a standard text file of received frequency, Doppler, power and C/N0 per pass |
| TEST | Flight mode for bench tests (cadence as ENCOUNTER) |
| TFPH / TFDF / TFDZ / TFVN / UPID | USLP fields: Transfer Frame Primary Header, Data Field, Data Zone, Version Number (0b1100), USLP Protocol Identifier |
| TRIAD | Two-vector attitude algorithm (gravity + magnetic field) computed on board as an independent check of the Nicla fusion |
| Two-person rule | Optional: a hazardous command also needs a second operator's AUTH in the web UI |
| TX duty | RF `tx_duty`: the share of time the spacecraft transmitter is on, averaged over whole transmission cycles (launch to next launch; at most 16 cycles launched within the last 300 s, `TxDutyMeter`). The flight keeps it <= 50 %; the ground flags YELLOW from 55 %, RED from 70 % |

### U

| Term | Meaning |
|---|---|
| UART | Asynchronous serial port; the E22 modules are UART devices (9600 baud) |
| UDP interop | The station's optional UDP links to a mission control system: TM frames and packets out (`[interop] tm_frames_udp`, `tm_packets_udp`), TC frames in (`tc_frames_udp_listen`: no authentication and no CONFIRM, so loopback addresses only) |
| USLP | Unified Space Data Link Protocol (CCSDS 732.1-B-2), selectable instead of TM / TC framing |

### V

| Term | Meaning |
|---|---|
| VC / VCID | Virtual channel: VC 0 real-time engineering, VC 1 real-time science, VC 2 recorder playback, VC 3 CFDP files, VC 7 (USLP VCID 63) idle. Downlink priority VC 0 > 1 > 2 > 3 > idle |
| VERIFY | A tag in the code and docs: a value taken from a datasheet or an assumption that must be confirmed on hardware ([20_Known_Limitations.md](20_Known_Limitations.md)) |
| VGQ-1 | This project's spacecraft model, on the UNO Q |

### W

| Term | Meaning |
|---|---|
| WAKE | Ground directive that wakes a hibernating spacecraft (wake-up preamble + MODE SAFE) |
| WAL | SQLite write-ahead logging, so CSV exports never block the station |
| Watchdog | See IWDG |
| WMM | World Magnetic Model: the site's expected field (total field, inclination, declination) for magnetometer tests and MAGCAL |
| WOR | E22 Wake-On-Radio: a duty-cycled receiver woken by a long preamble |

### X

| Term | Meaning |
|---|---|
| XTCE | XML Telemetric and Command Exchange (OMG XTCE 1.2, adopted by CCSDS 660.0-B-2): the mission database export `vgq1.xtce.xml` |

---

## Units and link quantities

| Quantity | Meaning and formula |
|---|---|
| Sensitivity | The weakest signal the receiver decodes (dBm), with its source: measured (T-RF-03) > datasheet > computed / estimated |
| Margin | Received power - sensitivity (dB); fade margin = the part reserved for fading |
| Excess loss | Measured path loss - free-space path loss (terrain, obstacles, mismatch) |
| Radio horizon | 4.12 (sqrt(h1 / m) + sqrt(h2 / m)) km for antenna heights h1, h2 (4/3-earth refraction) |
| Fresnel radius | First Fresnel-zone radius 17.32 sqrt(d1 d2 / (f_GHz d)) m; keep 60 % of it clear |
| Shannon limit | -174 + 10 log10(Rb) - 1.59 dB + NF: no receiver can do better at bit rate Rb |
| Octet | 8 bits (CCSDS term for a byte) |
| KiB / MB / GB | 1024 octets; 10^6 and 10^9 octets in the station configuration |
