# 02 - Link Budget and RF Analysis

This document explains how far VGQ-1 and DSS-Q1 can talk, why, and how to check it. It
covers the three radio options (E22-400T33D, E22-400T37S, SX126x), their sensitivities, the
link-budget equations, worked budgets, the range you can expect in real places, what the
ground station measures, frequency error and Doppler, and how the link manager picks a rate.

| If you need ... | read |
|---|---|
| system overview, link capacity per setting | [01_System_Design.md](01_System_Design.md) (section 9.5) |
| wiring of the radios, power supplies, antennas and coax | [03_Hardware_Integration.md](03_Hardware_Integration.md) |
| frame, packet and CLTU formats | [04_Space_Data_Link_ICD.md](04_Space_Data_Link_ICD.md) |
| the RF tests (T-RF-xx) that confirm the VERIFY items | [08_Verification_and_Test.md](08_Verification_and_Test.md) |
| licence, power limits, encryption on amateur bands | [09_Regulatory_and_Safety.md](09_Regulatory_and_Safety.md) |
| every radio setting and its default | [15_Configuration_Reference.md](15_Configuration_Reference.md) |

Conventions:

* Every number names its source: a datasheet section, a code file and symbol, or the tool
  run that printed it. "Datasheet" values are the manufacturer's typical values.
  "Estimated" and "computed" values are derived and labelled as such everywhere, also on the
  dashboard.
* All budgets below were printed by [tools/link_budget.py](../tools/link_budget.py) (run for
  this document). The tool takes its radio tables from
  [dssq/link.py](../dssq-groundstation/python/dssq/link.py), the single ground-side source.
  Re-run any case yourself, for example:

  ```
  python3 tools/link_budget.py budget --all-rates
  python3 tools/link_budget.py budget --model SX126x --rate-code 1 --gr 12 --distance-km 40 --h1 30 --h2 300
  python3 tools/link_budget.py budget --config dssq-groundstation/python/config/station.toml
  ```

* Items only the hardware can confirm are marked **VERIFY** with their test number and are
  listed in [20_Known_Limitations.md](20_Known_Limitations.md).

---

## 1 What limits the range (plain words)

A radio link works while the power arriving at the receiver stays above the receiver's
**sensitivity** (the weakest signal it can still decode), with some **margin** left over for
fading. Three things decide the received power:

1. What the transmitter puts into the air (power plus antenna gain, the **EIRP**).
2. How much is lost on the way. In free space the loss grows by 6 dB every time the distance
   doubles (**free-space path loss**, FSPL).
3. What the receiving antenna collects (its gain, minus cable loss).

With the default radios and simple dipole antennas the budget leaves about 59 dB of margin at
10 km (section 5.1). In free space that is enough for about 2,700 km, keeping a 10 dB fade
margin. **On Earth the link ends much earlier, at the radio horizon**: the Earth's curvature
hides one antenna from the other. Two antennas at 2 m and 10 m see each other only up to about
18.9 km (section 6). Hills, buildings and trees in the path (the **Fresnel zone**) cut the link
earlier still.

So, in order of effect:

1. **Antenna height** (raises the horizon and clears the Fresnel zone): a hill, a mast, a
   balloon. This is the only thing that moves the horizon.
2. **A clear line of sight**: no buildings or trees in the first Fresnel zone.
3. **Ground antenna gain and short, low-loss coax**: a 12 dBi Yagi adds 9.85 dB over a dipole.
4. **A slower, more sensitive radio setting**: the E22 at 2.4 kbit/s is already its slowest;
   the SX126x option reaches 12 to 15 dB more sensitivity (section 2.5).
5. **A quiet channel**: `RFSCAN` (spacecraft) and `GSCAN` (ground) find the quietest allowed
   channel; local interference raises the real noise floor above the datasheet's.

This mirrors deep space: there the link budget, not the geometry, is the limit, and every
decibel of antenna gain and receiver sensitivity is fought for. Here the budget is generous
and the geometry wins.

---

## 2 The radios

### 2.1 The three options at a glance

| Option | Interface | Output power | Sensitivity at its slowest setting | What the ground gets per packet | How it is selected |
|---|---|---|---|---|---|
| EBYTE **E22-400T33D** (default) | UART, LoRa inside EBYTE's firmware | 33 dBm (2 W) at code 0 | -128 dBm typ at 2.4 kbit/s (datasheet) | RSSI octet, ambient-noise register | default build, spacecraft pin A0 open |
| EBYTE **E22-400T37S** on the E22-400TBH-02 board (preserved option) | UART, same register protocol | 37 dBm (5 W), no power levels | -126 dBm typ at 2.4 kbit/s (datasheet) | RSSI octet, noise register, abnormal-status reports | spacecraft: A0 jumpered to GND at boot ([config.h](../vgq1-spacecraft/sketch/src/config.h) `PIN_RADIO_MODEL_STRAP`); station: `[radio] model` |
| **SX1268 / SX1262** module on SPI (reference module EBYTE E22-400M33S) | SPI, RadioLib 7.8.1 (bundled, patched) | 33 dBm max (chip 22 dBm + module PA, VERIFY T-RF-07) | -143.1 dBm at profile 0, -140.0 dBm at profile 1 (computed) | RSSI, true SNR, frequency error, CRC flag, CAD | build switches `VGQ_RADIO = 3` and `DSSQ_RADIO_SX126X = 1`, `[radio] model = "SX126x"` |

Sources: E22 manuals (sections 2.3 and 2.4 below), [drivers/radio.cpp](../vgq1-spacecraft/sketch/src/drivers/radio.cpp)
tables, [dssq/link.py](../dssq-groundstation/python/dssq/link.py) `MODELS`. Both ends must
run the same model; the E22 ground can follow the model the spacecraft reports in its RF
packet (`[radio] model = "auto"`, contract section 33). The default
[station.toml](../dssq-groundstation/python/config/station.toml) sets `model = "E22-400T33D"`
explicitly.

The two E22 modules are "transparent" UART radios: EBYTE's firmware chooses the LoRa
spreading factor and bandwidth behind each air-rate code and does not publish them. The
SX126x option gives the software full control of the LoRa modulation, a measured SNR and a
frequency-error estimate per packet. Everything above the radio (CCSDS framing, coding, SDLS,
COP-1, the ground data system) is the same for all three.

### 2.2 E22-400T33D (default)

Source: EBYTE E22-400T33D user manual v1.05, sections 2.1 and 2.2 (the extract's page header
says "E22-400T30D": the manual was derived from the T30D's; the T33D values are in its tables).

| Parameter | Value | Consequence |
|---|---|---|
| Supply | 3.3 to 5.5 V; above 4.7 V for full performance | exceeding 5.5 V may destroy the module |
| Logic level | 3.3 V | direct connection to the UNO Q |
| Frequency | 410.125 to 493.125 MHz (CH 0 to 83) | section 2.6 |
| Maximum TX power | 32 / 33 / 34 dBm (min / typ / max) | code 0 = 33 dBm |
| TX current | 850 / 1000 / 1200 mA at 33 dBm | supply sized in [03](03_Hardware_Integration.md) |
| RX current | 11 / 13 / 15 mA | |
| Sleep current | about 2 uA | |
| Receive sensitivity | -126 / -128 / -130 dBm at 2.4 kbit/s | the code uses the typical -128 dBm |
| Air data rate | 2.4 to 62.5 kbit/s | no slower setting than 2.4k |
| Sub-packet length | 240 octets (32 / 64 / 128 / 240 settable), 1000-octet buffer | one 236-octet CADU per LoRa packet |
| Blocking (maximum input) | 10 dBm | never couple two units closely without attenuators |
| Reference distance (EBYTE) | 16 km, clear and open, 5 dBi antennas at 2.5 m, 2.4 kbit/s | section 6.4 |
| Temperature | -40 to 85 degC | |
| RF connector | SMA, 50 ohm | |

### 2.3 E22-400T37S (preserved option)

Source: EBYTE E22-400T37S user manual v1.5, sections 2.2, 5.7 and 7.2
(extract scratchpad `t37s.txt`).

| Parameter | Value | Consequence |
|---|---|---|
| Supply | 4.5 to 15 V (5 to 12 V typical; 5 V or more for full power) | its own supply, not the board's 5 V pin |
| TX current at 5 V | 2900 / 3100 / 3300 mA (supply of at least 3.5 A) | |
| TX current at 12 V | 900 / 1100 / 1300 mA (supply of at least 1.5 A) | 12 V is the practical choice |
| RX current at 12 V | 43 mA | |
| TX power | 36 / 37 / 37.5 dBm | 5 W |
| Power levels | none: REG1 codes 00 to 11 are all 37 dBm, "This module has no power levels" (7.2) | output cannot be reduced in software; bench tests need attenuators; `TXPWR` has no effect |
| Receive sensitivity | -125 / -126 / -127 dBm at 2.4 kbit/s | the code uses -126 dBm |
| Air data rate | listed as 0.3k / 2.4k / 62.5k, but register codes 0, 1 and 2 are all 2.4k (7.2) | slowest real setting 2.4 kbit/s |
| Blocking | 10 dBm | |
| Reference distance (EBYTE) | 20 km, clear and open, 5 dBi antennas at 2.5 m, 2.4 kbit/s | section 6.4 |
| Abnormal-status reports | `FF FF FF 01` under-voltage (< 4.5 V), `02` over-voltage (> 15 V), `03` over-temperature (> 120 degC), `04` (5.7), when REG1 bit 2 is set | the flight decodes them into FDIR flags and EVRs ([01](01_System_Design.md) section 6) |

The flight writes REG1 = 0x24 for the T37S (240-octet sub-packets, ambient-noise RSSI on,
abnormal-status log on, power field 00): [drivers/radio.cpp](../vgq1-spacecraft/sketch/src/drivers/radio.cpp)
`e22_build_regs()`. Over-temperature silences the transmitter for 300 s (`kPaCooldownS`) and
puts the spacecraft in SAFE.

### 2.4 E22 air-rate codes and power codes

The rate code is REG0 bits 2..0 (both manuals, section 7.2). Codes 0, 1 and 2 are all
2.4 kbit/s on both modules; the code default is 2 ([config.h](../vgq1-spacecraft/sketch/src/config.h)
`kRadioRateCode`, station.toml `air_rate = 2`).

| Code | Air rate | Sensitivity T33D | Sensitivity T37S | Source | Flight busy time per CADU | Air time per CADU (ground estimate) |
|---|---|---|---|---|---|---|
| 0, 1, 2 | 2.4 kbit/s | -128.0 dBm | -126.0 dBm | datasheet | 1267 ms | 1022.7 ms |
| 3 | 4.8 kbit/s | -125.0 dBm | -123.0 dBm | estimated | 756 ms | 511.3 ms |
| 4 | 9.6 kbit/s | -122.0 dBm | -120.0 dBm | estimated | 500 ms | 255.7 ms |
| 5 | 19.2 kbit/s | -119.0 dBm | -117.0 dBm | estimated | 372 ms | 127.8 ms |
| 6 | 38.4 kbit/s | -116.0 dBm | -114.0 dBm | estimated | 308 ms | 63.9 ms |
| 7 | 62.5 kbit/s | -113.8 dBm | -111.8 dBm | estimated | 284 ms | 39.3 ms |

Sources: sensitivities [drivers/radio.cpp](../vgq1-spacecraft/sketch/src/drivers/radio.cpp)
`e22_sensitivity_ddbm()` and [dssq/link.py](../dssq-groundstation/python/dssq/link.py)
`sensitivity()`; flight busy time `e22_airtime_ms()`; ground air time `airtime_s()` (both
printed by `link_budget.py --all-rates`). EBYTE publishes no sensitivity above 2.4 kbit/s;
the estimate adds the spreading-gain loss, +10 log10(rate / 2400 bit/s) dB (section 3.3).

| Power code | T33D | T37S | SX126x (chip drive + 11 dB module PA gain, capped at 33 dBm) |
|---|---|---|---|
| 0 (default) | 33 dBm | 37 dBm | 22 + 11 = 33 dBm |
| 1 | 30 dBm (VERIFY T-RF-07) | 37 dBm | 17 + 11 = 28 dBm |
| 2 (brown-out boot code) | 27 dBm (VERIFY T-RF-07) | 37 dBm | 14 + 11 = 25 dBm |
| 3 | 24 dBm (VERIFY T-RF-07) | 37 dBm | 10 + 11 = 21 dBm |

Sources: [drivers/radio.cpp](../vgq1-spacecraft/sketch/src/drivers/radio.cpp) `kT33dPowerDbm`,
`kT37sPowerDbm`, `kSxDriveDbm`, `sx_tx_power_dbm()`; [config.h](../vgq1-spacecraft/sketch/src/config.h)
`kRadioPowerCode` = 0, `kBrownoutPowerCode` = 2, `kSxPaGainDb` = 11 (VERIFY T-RF-07). The
T33D code 0 = 33 dBm matches the factory default and the "maximum transmit power 33 dBm"
of the manual; the manual's REG1 table itself was copied from the T30D (30 / 27 / 24 / 21 dBm),
so codes 1 to 3 assume 3 dB steps below 33 dBm until measured.

### 2.5 SX126x LoRa profiles 0 to 7

The SX126x rate code selects one of eight LoRa profiles, all with coding rate 4/5, explicit
header, CRC on, a 12-symbol preamble and sync word 0x12. Profile 1 is the default
([config.h](../vgq1-spacecraft/sketch/src/config.h) `kRadioRateCode`; [drivers/radio.h](../vgq1-spacecraft/sketch/src/drivers/radio.h)
`kSxDefaultProfile`, `kSxSyncWord`, `kSxPreambleSym`).

| Profile | SF / BW | Symbol time | LDRO | Bit rate | Air time of a 236-octet CADU | Sensitivity, Rx boosted gain | Sensitivity, power-saving gain | Largest tolerated TX-RX offset (FERR_L) |
|---|---|---|---|---|---|---|---|---|
| 0 | SF12 / 31.25 kHz | 131.072 ms | on | 73.24 bit/s | 34.636 s | -143.1 dBm (computed) | -139.1 dBm (estimated) | 7812.5 Hz (18.0 ppm) |
| **1 (default)** | SF12 / 62.5 kHz | 65.536 ms | on | 146.48 bit/s | 17.318 s | -140.0 dBm (computed) | -136.0 dBm (estimated) | 15625 Hz (36.1 ppm) |
| 2 | SF12 / 125 kHz | 32.768 ms | on | 292.97 bit/s | 8.659 s | -137.0 dBm (datasheet) | -133.0 dBm (datasheet) | 21656 Hz (50 ppm) |
| 3 | SF11 / 125 kHz | 16.384 ms | on | 537.11 bit/s | 4.739 s | -134.5 dBm (computed) | -130.5 dBm (estimated) | 31250 Hz (72.2 ppm) |
| 4 | SF10 / 125 kHz | 8.192 ms | off | 976.56 bit/s | 2.165 s | -132.0 dBm (computed) | -128.0 dBm (estimated) | 31250 Hz (72.2 ppm) |
| 5 | SF9 / 125 kHz | 4.096 ms | off | 1757.81 bit/s | 1.185 s | -129.5 dBm (computed) | -125.5 dBm (estimated) | 31250 Hz (72.2 ppm) |
| 6 | SF8 / 125 kHz | 2.048 ms | off | 3125.00 bit/s | 0.664 s | -127.0 dBm (computed) | -123.0 dBm (estimated) | 31250 Hz (72.2 ppm) |
| 7 | SF7 / 125 kHz | 1.024 ms | off | 5468.75 bit/s | 0.373 s | -124.0 dBm (datasheet) | -120.5 dBm (estimated) | 31250 Hz (72.2 ppm) |

Sources: profile table [drivers/radio.cpp](../vgq1-spacecraft/sketch/src/drivers/radio.cpp)
`kProfiles` and [dssq/link.py](../dssq-groundstation/python/dssq/link.py) `SX126X_PROFILES`
(identical); symbol time 2^SF / BW; LDRO (low data rate optimisation) when the symbol time is
16.38 ms or longer (Semtech SX1261/2 datasheet rev 2.2, 6.1.1.4; `lora_ldro_required()`);
bit rate SF x BW / 2^SF x 4/5 (`lora_bitrate_bps()`); air time from the Semtech formula
(section 2.7; `link_budget.py --all-rates`); sensitivities section 3; FERR_L section 9.1.
The flight's busy time per CADU is the air time rounded up to whole milliseconds: 34636,
17318, 8659, 4740, 2165, 1185, 665 and 373 ms ([dssq/link.py](../dssq-groundstation/python/dssq/link.py)
`busy_s()`, [01](01_System_Design.md) section 9.5).

Other SX126x settings ([drivers/radio.h](../vgq1-spacecraft/sketch/src/drivers/radio.h),
[config.h](../vgq1-spacecraft/sketch/src/config.h), station sketch
[sketch.ino](../dssq-groundstation/sketch/sketch.ino)):

| Setting | Value | Source |
|---|---|---|
| Rx boosted gain | register 0x08AC = 0x96 (power-saving 0x94), kept through sleep by the retention list 0x029F to 0x02A1 = 01 08 AC; the station reads it back and reports it | SX1261/2 DS Table 9-3; station `kRegRxGain`, `kRxGainBoosted`; flight `kRadioRxBoost` = true |
| TX timeout | 1.5 x air time + 50 ms (flight), + 100 ms (station) | `kSxTxTimeoutMarginUs`; station `kTxMarginMs` |
| CAD before TX | at most 2 s of deferral, then transmit anyway (as the E22's LBT) | `kSxLbtMaxWaitMs`; station `kLbtMaxWaitMs`; flight default off (`kRadioLbt`), station on (`cad_before_tx = true`) |
| Wake-up preamble | 32 symbols, for a hibernating spacecraft that listens duty-cycled | `kSxWakePreambleSym`; station `kSxWakePreamble` |
| TCXO supply on DIO3 | 1.8 V (VERIFY: module datasheet) | `kSxTcxoV`; station `kSxTcxoMv` |
| DIO2 as RF switch | false (VERIFY: module wiring) | `kSxDio2RfSwitch` |
| PA current limit (OCP) | 140 mA, the SX1262 value (VERIFY for the SX1268) | `kSxOcpMa` |
| Module PA gain | 11 dB (VERIFY T-RF-07) | `kSxPaGainDb`; station.toml `pa_gain_db` |

### 2.6 Channel plan

All three models use the E22 channel grid: **f = 410.125 MHz + CH x 1 MHz, CH 0 to 83**
(E22 manuals 7.2, REG2; [drivers/radio.cpp](../vgq1-spacecraft/sketch/src/drivers/radio.cpp)
`kChannel0Hz`, `kChannelStepHz`; [dssq/link.py](../dssq-groundstation/python/dssq/link.py)
`CHANNEL_BASE_MHZ`). The default is **CH 23 = 433.125 MHz** (`kRadioChannel`), wavelength
c / f = 0.692 m. The ground refuses `CHANNEL` / `LINKCHAN` outside `[radio] allowed_channels`,
default 20 to 24 = 430.125 to 434.125 MHz (70 cm amateur band in ITU Regions 1 and 3, below
the 435 to 438 MHz satellite segment). Check your own licence and band plan:
[09_Regulatory_and_Safety.md](09_Regulatory_and_Safety.md).

### 2.7 Air time

| Radio | Formula | Source |
|---|---|---|
| E22, flight busy-time model | n x 10 x 1000 / 9600 + n x 8 x 1300 / rate, each term in integer ms: UART transfer at 9600 bit/s 8N1, plus the LoRa time at the nominal air rate with 30 % allowance for preamble, header and the module's sub-packet handling. 236 octets at 2.4k: 245 + 1022 = 1267 ms | [drivers/radio.cpp](../vgq1-spacecraft/sketch/src/drivers/radio.cpp) `e22_airtime_ms()` |
| E22, ground air time | n x 8 x 1.3 / rate (no UART part): 1022.7 ms at 2.4k | [dssq/link.py](../dssq-groundstation/python/dssq/link.py) `airtime_s()`, `E22_AIRTIME_FACTOR` |
| SX126x (Semtech) | Tsym = 2^SF / BW; n_payload = 8 + max(ceil((8 PL - 4 SF + 28 + 16 CRC - 20 IH) / (4 (SF - 2 DE))) x (CR + 4), 0); T = (n_preamble + 4.25 + n_payload) x Tsym | SX1261/2 DS 6.1.4; `lora_airtime_us()` (flight, integer microseconds) and `lora_airtime_s()` (ground) agree exactly |

The E22 factor 1.3 is an estimate: EBYTE does not publish the LoRa parameters, so the real
air time per code is **VERIFY** (T-RF-03 records the measured frame period).

The frame period is computed from the **measured** busy time of the last transmission; the
model above is used only before the first transmission and after a radio change
([fsw/flight.cpp](../vgq1-spacecraft/sketch/src/fsw/flight.cpp) `busy_estimate_ms()`); the
first frame after a change still waits the period owed to the last frame at the old settings
(`launch_spacing_ms()` in [fsw/datalink.h](../vgq1-spacecraft/sketch/src/fsw/datalink.h)). On the
E22 a transmission counts as done only when AUX is high again *and* the modelled busy time has
passed, so the measured value is never shorter than the model
([04](04_Space_Data_Link_ICD.md) section 3.2). On the SX126x it is the chip's TX-done time.
The capacity figures of this document and of [01](01_System_Design.md) section 9.5 use the
model.

> Note: an earlier owner briefing quoted "about 13 s" per CADU at SF12 / 62.5 kHz. The
> code's Semtech computation gives 17.318 s; the code wins.

---

## 3 Where the sensitivity numbers come from

### 3.1 Source order

Every sensitivity the software uses carries its source, shown on the dashboard as a badge
([dssq/link.py](../dssq-groundstation/python/dssq/link.py) `sensitivity()`):

1. **measured**: `[radio.sensitivity_measured]` in station.toml, written only from test
   T-RF-03 (section 3.5). Overrides everything else.
2. **datasheet**: EBYTE (T33D -128 dBm, T37S -126 dBm at 2.4 kbit/s) or Semtech SX1261/2
   Table 3-8.
3. **estimated**: E22 rates above 2.4k; SX126x power-saving gain except BW 125 kHz SF12.
4. **computed**: SX126x profiles that Table 3-8 does not list.

### 3.2 Semtech SX1261/2 Table 3-8

LoRa sensitivity with Rx boosted gain, split RF paths, RF-switch insertion loss excluded;
test conditions PER 1 %, 64-octet packets, preamble 8, CR 4/5, CRC on, explicit header
(SX1261/2 datasheet rev 2.2, Table 3-8; [dssq/link.py](../dssq-groundstation/python/dssq/link.py)
`SX1262_SENSITIVITY_DBM`):

| BW | SF7 | SF12 |
|---|---|---|
| 10.4 kHz | -134 dBm | -148 dBm |
| 125 kHz | -124 dBm | -137 dBm |
| 250 kHz | -121 dBm | -134 dBm |
| 500 kHz | -117 dBm | -129 dBm |

With power-saving gain the table gives -133 dBm at BW 125 kHz SF12, 4 dB worse; the code
applies the same 4 dB to every other profile and labels those values "estimated"
(`SX126X_POWER_SAVING_PENALTY_DB`, flight `kSxPowerSavingPenaltyDb`). Note the test
conditions: 64-octet packets at 1 % packet error rate. A 236-octet CADU needs a slightly
stronger signal for the same frame error rate; the RS code then repairs up to 16 bad octets
per frame (section 11.3).

### 3.3 Computed and estimated values

Profiles absent from Table 3-8 use the thermal-noise formula
([drivers/radio.cpp](../vgq1-spacecraft/sketch/src/drivers/radio.cpp) `lora_sensitivity_dbm()`,
[dssq/link.py](../dssq-groundstation/python/dssq/link.py) `lora_sensitivity_computed_dbm()`):

**S = -174 dBm/Hz + 10 log10(BW / Hz) + NF + SNRmin(SF)**

| Term | Value | Source |
|---|---|---|
| Thermal noise density | -174 dBm/Hz (kT at 290 K) | physics |
| Noise figure NF | 6 dB, **assumed** for Rx boosted gain (VERIFY T-RF-03) | `kSxNoiseFigureDb`, `SX126X_NF_DB` (contract section 2.2) |
| SNRmin | SF7 -7.5, SF8 -10, SF9 -12.5, SF10 -15, SF11 -17.5, SF12 -20 dB | `LORA_SNR_MIN_DB` (Semtech LoRa demodulator limits) |

Check against the datasheet: SF12 / 125 kHz gives -174 + 51.0 + 6 - 20 = -137.0 dBm and SF7 /
125 kHz gives -124.5 dBm against the listed -137 and -124 dBm, so NF 6 dB reproduces the table
within 0.5 dB.

E22 rates above 2.4k: S = S(2.4k) + 10 log10(rate / 2400 bit/s), the loss of spreading gain
when the air rate rises ([drivers/radio.cpp](../vgq1-spacecraft/sketch/src/drivers/radio.cpp)
`e22_sensitivity_ddbm()`, [dssq/link.py](../dssq-groundstation/python/dssq/link.py) `sensitivity()`).
EBYTE publishes nothing above 2.4k, so these are labelled "estimated" (VERIFY T-RF-03).

> Code wins (contract C-14): the contract listed -143.0 dBm for profile 0 and -124.5 dBm for
> profile 7. The code gives -143.1 dBm (the formula yields -143.05, rounded to 0.1 dB) and
> uses the datasheet's -124.0 dBm for SF7 / 125 kHz.

### 3.4 Plausibility: the Shannon limit

No receiver can beat the Shannon limit for its bit rate: **S_min = -174 + 10 log10(Rb) - 1.59
dB + NF** (the -1.59 dB is the minimum Eb/N0 of any code). `link_budget.py` prints it next to
every budget.

| Bit rate | Shannon limit, NF 0 dB | Shannon limit, NF 6 dB | Sensitivity used | Distance from the NF 6 dB limit |
|---|---|---|---|---|
| 2400 bit/s (E22 2.4k) | -141.8 dBm | -135.8 dBm | T33D -128.0 / T37S -126.0 dBm | 7.8 / 9.8 dB |
| 292.97 bit/s (profile 2) | -150.9 dBm | -144.9 dBm | -137.0 dBm (datasheet) | 7.9 dB |
| 146.48 bit/s (profile 1) | -153.9 dBm | -147.9 dBm | -140.0 dBm | 7.9 dB |
| 73.24 bit/s (profile 0) | -156.9 dBm | -150.9 dBm | -143.1 dBm | 7.8 dB |

All values sit 8 to 10 dB above the limit, as real modems do. A figure far below -142 dBm
quoted "at 2.4 kbit/s" (seen for some sibling modules) cannot refer to a 2.4 kbit/s
information rate.

### 3.5 Measuring it: test T-RF-03

`python3 tools/link_budget.py sensitivity` turns a stepped-attenuator session into a
measured sensitivity ([tools/link_budget.py](../tools/link_budget.py) module text):

1. The spacecraft transmits continuously; the operator inserts attenuation in steps and
   writes each step as a row `start_utc,end_utc,atten_db,rate_code[,model]` (or time-stamps
   them live with `--live`).
2. Per step: expected frames = duration / frame period; FER = 1 - frames decoded OK /
   expected; received power = reference power - attenuation.
3. The sensitivity is the received power at **FER = 10 %**, interpolated linearly between the
   two steps that bracket it. A step needs at least 10 expected frames; the reference step must
   be error-free (FER below 1 %).
4. The tool **refuses** (exit code 2) when the FER never crosses 10 %, and accepts only
   results between -160 and -60 dBm (`MEASURED_RANGE_DBM`).
5. Output: TOML lines for `[radio.sensitivity_measured]`, for example
   `"E22-400T33D:2" = -127.5`. Nobody enters measured values without this test.

---

## 4 Equations

| Quantity | Equation | Notes |
|---|---|---|
| Free-space path loss | FSPL = 20 log10(4 pi d f / c) = 32.44778 + 20 log10(f / MHz) + 20 log10(d / km) dB | c = 299 792 458 m/s; the exact constant is 32.44778 dB, not 32.44 ([tools/link_budget.py](../tools/link_budget.py)) |
| EIRP | P_tx + G_tx - L_tx | dBm |
| Received power | P_r = EIRP - FSPL - L_excess + G_rx - L_rx | free space: L_excess = 0 |
| Margin | M = P_r - S | S = sensitivity of the current rate code |
| Free-space range with fade margin F | d such that FSPL(d) = EIRP + G_rx - L_rx - S - F | F = 10 dB in this document (`--fade-margin`) |
| Radio horizon (4/3-earth refraction) | d_h = 4.12 (sqrt(h1 / m) + sqrt(h2 / m)) km | heights above the surrounding terrain |
| First Fresnel zone radius | r = 17.32 sqrt(d1 d2 / (f_GHz d)) m, d in km | keep at least 60 % of it free of obstacles |
| One-way light time | OWLT = d / c | 33.36 us at 10 km |
| Antenna effective area | A_e = G lambda^2 / (4 pi) | section 6.6 |
| Shannon limit | -174 + 10 log10(Rb) - 1.59 + NF dBm | section 3.4 |

Rules of thumb at 433 MHz: FSPL at 1 km is 85.2 dB; every doubling of distance adds 6.0 dB;
every 6 dB of extra margin doubles the free-space range; 20 dB multiplies it by 10.

---

## 5 Worked link budgets

Common assumptions, the `link_budget.py` defaults: spacecraft antenna 2.15 dBi (half-wave
dipole), spacecraft feeder loss 0.5 dB, ground feeder loss 1.0 dB, NF 6 dB for the Shannon
line, 10 dB fade margin, channel 23 (433.125 MHz), power code 0. These match
[station.toml](../dssq-groundstation/python/config/station.toml) `[spacecraft]
antenna_gain_dbi = 2.15`, `tx_line_loss_db = 0.5` and `[station] rx_line_loss_db = 1.0`.
Station.toml's ground antenna is a 12 dBi Yagi (`antenna_gain_dbi = 12.0`).

### 5.1 Budget A: E22-400T33D, two dipoles, 10 km, antennas at 2 m and 10 m (the default)

| Item | Value |
|---|---|
| TX power (code 0) | 33.0 dBm = 2.00 W |
| TX antenna gain / feeder loss | +2.15 dBi / 0.5 dB |
| **EIRP** | **34.6 dBm** |
| Distance (OWLT) | 10.00 km (33.36 us) |
| Free-space path loss | 105.2 dB |
| RX antenna gain / feeder loss | +2.15 dBi / 1.0 dB |
| **Received power (free space)** | **-69.4 dBm** |
| Rate | code 2 = 2.4 kbit/s; air time of one CADU 1022.7 ms (estimated) |
| Receiver sensitivity | -128.0 dBm (datasheet) |
| Shannon limit at this rate | -135.8 dBm (NF 6 dB) |
| **Free-space margin** | **58.6 dB** |
| Free-space range with 10 dB fade margin | 2,698 km |
| Radio horizon for 2 m and 10 m | **18.9 km**: the real limit |
| First Fresnel radius at mid-path | 41.6 m (keep 25.0 m clear) |

Command: `python3 tools/link_budget.py budget --all-rates`.

### 5.2 Budget B: E22-400T33D to a 12 dBi Yagi, 40 km, hill to hill (30 m and 300 m)

EIRP 34.6 dBm, FSPL 117.2 dB, received power -71.6 dBm, margin **56.4 dB**, free-space range
8,385 km, radio horizon 93.9 km, Fresnel radius 83.2 m at mid-path (keep 49.9 m clear), OWLT
133.43 us. Command: `budget --gr 12 --distance-km 40 --h1 30 --h2 300`.

### 5.3 Budget C: E22-400T37S, two dipoles, 10 km

EIRP 38.6 dBm, received power -65.4 dBm, sensitivity -126.0 dBm (datasheet), margin
**60.6 dB**, free-space range 3,396 km. The T37S transmits 4 dB more than the T33D but hears
2 dB less, so the net gain is only 2 dB (x1.26 in free-space range). Command:
`budget --model E22-400T37S --all-rates`.

### 5.4 Budget D: SX126x profile 1, two dipoles, 10 km

EIRP 34.6 dBm (code 0: 22 dBm drive + 11 dB PA gain, VERIFY), received power -69.4 dBm,
sensitivity -140.0 dBm (computed), Shannon limit -147.9 dBm, margin **70.6 dB**, free-space
range 10,740 km, FERR_L +-15,625 Hz (36.1 ppm). 12 dB better than budget A. Command:
`budget --model SX126x --rate-code 1 --all-rates`.

### 5.5 Budget E: SX126x profile 0 to a 12 dBi Yagi, 40 km, hill to hill

Received power -71.6 dBm, sensitivity -143.1 dBm, Shannon limit -150.9 dBm, margin
**71.5 dB**, free-space range 47,698 km, FERR_L +-7,812 Hz (18.0 ppm). Command:
`budget --model SX126x --rate-code 0 --gr 12 --distance-km 40 --h1 30 --h2 300`.

### 5.6 Free-space range per rate code (dipoles at both ends, 10 dB fade margin)

| Code | T33D | T37S | SX126x, Rx boosted gain | SX126x, power-saving gain |
|---|---|---|---|---|
| 0 | 2,698 km | 3,396 km | 15,346 km | 9,683 km |
| 1 | 2,698 km | 3,396 km | 10,740 km | 6,776 km |
| 2 | 2,698 km | 3,396 km | 7,603 km | 4,797 km |
| 3 | 1,910 km | 2,404 km | 5,702 km | 3,597 km |
| 4 | 1,352 km | 1,702 km | 4,276 km | 2,698 km |
| 5 | 957 km | 1,205 km | 3,206 km | 2,023 km |
| 6 | 678 km | 853 km | 2,404 km | 1,517 km |
| 7 | 526 km | 662 km | 1,702 km | 1,138 km |

Printed by `link_budget.py budget --all-rates` for each model (SX126x power-saving:
`--no-rx-boost`). With a 12 dBi ground Yagi multiply by 3.11 (+9.85 dB): T33D 8,385 km, T37S
10,556 km, SX126x profile 1 33,381 km, profile 0 47,698 km.

These are free-space numbers. They show how much budget is left over, not how far the link
reaches on Earth (section 6).

---

## 6 Range: what to expect

### 6.1 The short answer

* **In free space** the default link (T33D, dipoles) would close at about 2,700 km, the
  SX126x at profile 0 with a ground Yagi at about 47,700 km (section 5).
* **On Earth** the radio horizon of your antenna heights is the limit, and at that distance
  30 to 70 dB of margin are still left (section 6.3). If the path is clear, the link works up
  to the horizon with any of the three radios.
* **In practice** obstacles in the Fresnel zone, ground reflections and local noise take part
  of that margin. Expect less than the horizon in towns and forests; measure it with the range
  test in [08_Verification_and_Test.md](08_Verification_and_Test.md) and record it in
  [21_Test_Records.md](21_Test_Records.md).

### 6.2 Scenarios

Radio horizon d_h = 4.12 (sqrt(h1) + sqrt(h2)) km; free-space ranges with a 10 dB fade margin
(section 5.6). Ground antenna gain as stated.

| Scenario | Ground antenna | Heights (spacecraft / station) | Radio horizon | Free-space range T33D / T37S / SX p1 / SX p0 |
|---|---|---|---|---|
| Bench, both on tables outdoors | dipole 2.15 dBi | 2 m / 2 m | 11.7 km | 2,698 / 3,396 / 10,740 / 15,346 km |
| Roof | dipole 2.15 dBi | 2 m / 10 m | 18.9 km | 2,698 / 3,396 / 10,740 / 15,346 km |
| Yagi on the roof | Yagi 12 dBi | 2 m / 10 m | 18.9 km | 8,385 / 10,556 / 33,381 / 47,698 km |
| Spacecraft on a 30 m mast | Yagi 12 dBi | 30 m / 10 m | 35.6 km | same as above |
| Hill to hill | Yagi 12 dBi | 30 m / 300 m | 93.9 km | same |
| Mountain to mountain | Yagi 12 dBi | 1000 m / 1000 m | 260.6 km | same |
| High-altitude balloon | Yagi 12 dBi | 30000 m / 10 m | 726.6 km | same |

### 6.3 Margin left at the horizon

Each scenario computed at its horizon distance (`budget --distance-km <horizon> --h1 --h2 --gr`
for every model):

| Scenario (distance) | FSPL | T33D received power / margin | T37S received power / margin | SX p1 margin | SX p0 margin | Fresnel radius at mid-path |
|---|---|---|---|---|---|---|
| Bench (11.7 km) | 106.5 dB | -70.7 dBm / 57.3 dB | -66.7 dBm / 59.3 dB | 69.3 dB | 72.4 dB | 45.0 m |
| Roof (18.9 km) | 110.7 dB | -74.9 dBm / 53.1 dB | -70.9 dBm / 55.1 dB | 65.1 dB | 68.2 dB | 57.2 m |
| Yagi on the roof (18.9 km) | 110.7 dB | -65.1 dBm / 62.9 dB | -61.1 dBm / 64.9 dB | 74.9 dB | 78.0 dB | 57.2 m |
| 30 m mast (35.6 km) | 116.2 dB | -70.6 dBm / 57.4 dB | -66.6 dBm / 59.4 dB | 69.4 dB | 72.5 dB | 78.5 m |
| Hill to hill (93.9 km) | 124.6 dB | -79.0 dBm / 49.0 dB | -75.0 dBm / 51.0 dB | 61.0 dB | 64.1 dB | 127.5 m |
| Mountain (260.6 km) | 133.5 dB | -87.8 dBm / 40.2 dB | -83.8 dBm / 42.2 dB | 52.2 dB | 55.3 dB | 212.4 m |
| Balloon (726.6 km) | 142.4 dB | -96.8 dBm / 31.2 dB | -92.8 dBm / 33.2 dB | 43.2 dB | 46.3 dB | 354.7 m |

Even the balloon case keeps 31 dB with the default radio: geometry, not power, ends the link.
The large margins are what lets a real path with obstacles still work, and what the slower
SX126x profiles buy: tolerance of diffraction and foliage loss, not more line-of-sight range.

### 6.4 EBYTE's reference distances

EBYTE quotes 16 km (T33D) and 20 km (T37S) "clear and open, antenna gain 5 dBi, antenna
height 2.5 m, 2.4 kbps" (manuals section 2.2). The 4/3-earth radio horizon of two antennas at
2.5 m is 4.12 x 2 x sqrt(2.5) = **13.03 km**. Both claims lie beyond it, so the test path
must have had higher ground at one end, or the link worked partly by diffraction over the
horizon. Treat them as vendor claims; the link budget says the radio has the power for them,
the geometry decides.

### 6.5 What improves range, ranked

| Rank | Change | Effect | Example |
|---|---|---|---|
| 1 | Raise the antennas | moves the horizon: sqrt of the height | 2 m / 10 m: 18.9 km; 30 m / 300 m: 93.9 km |
| 2 | Clear the first Fresnel zone | removes obstruction loss (tens of dB behind buildings or hills) | keep 25 m clear at mid-path over 10 km (section 5.1) |
| 3 | Ground antenna gain, short good coax | +9.85 dB with a 12 dBi Yagi over a dipole; every 1 dB of coax loss is lost margin | free-space range x3.1 |
| 4 | SX126x option, slow profile | +12.0 dB (profile 1) or +15.1 dB (profile 0) over the T33D at 2.4k | margin 70.6 / 73.7 dB at 10 km with dipoles |
| 5 | Quiet channel | gets back what local interference costs | `RFSCAN`, `GSCAN`, ambient-noise register |
| - | E22-400T37S instead of T33D | only +2 dB net (4 dB more power, 2 dB less sensitivity), 3 to 4 times the supply current, a licence for 5 W | section 5.3 |
| - | Faster E22 rates | lose 3 dB per doubling of rate | section 2.4 |

### 6.6 The "35 dBi antenna" claim

A gain of 35 dBi is G = 10^3.5 = 3162. At 433.125 MHz (lambda = 0.692 m) the effective area is
A_e = G lambda^2 / (4 pi) = **120.6 m^2**: the aperture of a parabolic dish of 12.39 m diameter
at 100 % efficiency, 15.99 m at 60 %, 16.71 m at 55 % and 17.52 m at 50 %. In short, about a
17 m dish (contract section 31). A hand-sized 433 MHz antenna is a dipole or monopole of about
2 dBi; a 433 MHz Yagi of practical length gives 10 to 13 dBi. This document uses 2.15 dBi for
the supplied antennas and 12 dBi for the station Yagi.

### 6.7 Fresnel zone clearance

The first Fresnel zone is the football-shaped region around the line of sight that carries
most of the energy. Obstacles inside it cost signal even when the antennas "see" each other.
At mid-path its radius is 41.6 m over 10 km, 83.2 m over 40 km and 127.5 m over 93.9 km
(`link_budget.py`); keep at least 60 % of it clear. Over long flat ground the Earth itself
enters the zone before the geometric horizon: that is why antenna height helps twice.

---

## 7 Data rate and the uplink

### 7.1 Downlink capacity

Slower settings buy margin with data rate. The frame period is a multiple of the radio busy
time (4.0 in SAFE, 2.5 in CRUISE, 2.0 in ENCOUNTER and TEST, at least 1.5 s, transmit duty at
most 50 %), and each frame carries 158 octets of packets with TM + SDLS AE. The full tables per
model, rate and mode, and the load plan that stretches packet cadences to fit, are in
[01_System_Design.md](01_System_Design.md) sections 5.2 and 9.5. Key points:

| Setting | Frame period SAFE / ENCOUNTER | Packet capacity SAFE / ENCOUNTER | Consequence |
|---|---|---|---|
| E22 2.4 kbit/s | 5.068 / 2.534 s | 31.2 / 62.4 octets/s | every mode's periodic telemetry fits without stretching |
| SX126x profile 1 | 69.3 / 34.6 s | 2.28 / 4.56 octets/s | periodic packets are stretched (SAFE HK every 72 s instead of 30 s) |
| SX126x profile 0 | 138.5 / 69.3 s | 1.14 / 2.28 octets/s | for maximum range only |

### 7.2 Uplink CLTU air time

A command travels as one CLTU ([04](04_Space_Data_Link_ICD.md) section 5). Its size depends
on the TC frame length: 2 + 8 x ceil(frame / 7) + 8 octets. Air time with each radio's busy
model ([dssq/link.py](../dssq-groundstation/python/dssq/link.py) `busy_s()`):

| CLTU (TC frame) | Example | E22 2.4k | SX126x p1 | SX126x p0 | SX126x p7 |
|---|---|---|---|---|---|
| 26 octets (14) | NOOP, no SDLS | 0.139 s | 3.556 s | 7.111 s | 0.066 s |
| 66 octets (44) | NOOP, TC + AE | 0.354 s | 6.177 s | 12.354 s | 0.128 s |
| 82 octets (63) | SEQLOAD, TC + AE | 0.440 s | 7.160 s | 14.320 s | 0.148 s |
| 90 octets (64) | largest TC frame | 0.483 s | 7.488 s | 14.975 s | 0.164 s |

The ground transmits in the **quiet window** after each received frame: frame period minus
busy time, 3801 ms at E22 2.4k in SAFE and 51,954 ms at SX126x profile 1 in SAFE. The frame
period is at least max(2 x busy, 1.5 s), so the shortest quiet window (ENCOUNTER and TEST) is
one busy time: 1.267 s at E22 2.4k, 17.318 s at profile 1 (more where the 1.5 s floor
applies: E22 9.6k and faster, SX126x profiles 6 and 7).

**The window plan.** The station must stop transmitting before the spacecraft's next frame
starts, or that frame can be lost. The GDS therefore plans every window against an air-time
budget ([dssq/gds.py](../dssq-groundstation/python/dssq/gds.py) `UplinkWindowPlan`,
`_uplink_window()`):

* **Budget** (`_window_budget_s()`): the shortest quiet window of any mode,
  max(2 x busy, 1.5 s) - busy, less a 0.2 s guard (`WINDOW_GUARD_S`: the GDS reaction time and
  the flight's 100 ms receive quiet time). It does not depend on the mode in force: a window
  in SAFE carries no more than one in ENCOUNTER. It counts elapsed time: the transmissions start
  no earlier than the frame's arrival time (ERT) + 0.1 s (`WINDOW_REACTION_S`), the current
  time (a frame handled late, e.g. after a stalled radio thread) and the end of the previous
  window's transmissions (several frames handled in one batch), and must end by ERT + 0.1 s +
  budget. A window handled late gets only what is left; a frame whose quiet window has passed
  books nothing.
* **Time base and spacing**: the plan runs on the monotonic `SteadyClock` (the frame's ERT is
  converted once, as now - max(0, wall clock - ERT stamp), so a wall-clock step cannot move a
  window), and windows open at most once per 1.4 s of frame ERT (`WINDOW_MIN_SPACING_S`). The
  flight's frames are at least 1.5 s apart (`kMinFramePeriodMs`); a frame whose ERT comes less
  than 1.4 s after the previous window's - genuine, replayed or forged - gets no window, and
  its commands wait for the next one. With at most 4 TC frames per window this keeps the
  station below 3 SDLS TC numbers per second ([17](17_Security_Model.md) section 7.6).
* **Cost of one ground packet** (`_tx_cost_s()`): its busy time (table above) + 0.05 s
  turnaround (`TX_TURNAROUND_S`), + 5 symbols of channel-activity detection on the SX126x
  when `[radio.sx126x] cad_before_tx` is on (the default: 0.328 s at profile 1, 0.655 s at
  profile 0; `CAD_SYMBOLS`).
* **Order**: the station identification ([01](01_System_Design.md) section 3.4.1) is booked
  first when it is due, or would become due once this window transmits
  (`StationIdent.due_if_transmitting()`); then external interop TC frames (at most 2) and
  FOP-1 CLTUs (at most 2, bypass first;
  [dssq/fop.py](../dssq-groundstation/python/dssq/fop.py) `Fop1.next_cltus(..., fits=)`) while
  they fit. The identification is booked first but transmitted last, after the window's
  CLTUs. A frame that does not fit waits for a later window and is not built yet, so it spends
  no SDLS sequence number (its length comes from
  [ccsds/tc.py](../dssq-groundstation/python/dssq/ccsds/tc.py) `cltu_length()`).

What one window carries (90-octet CLTUs are the largest; 82 octets is SEQLOAD or MAGCAL under
TC + AE; the identification with the default 16-octet text):

| Setting | Budget | Cost of one 90-octet CLTU | 90-octet CLTUs per window | When the identification is due |
|---|---|---|---|---|
| E22 2.4k | 1.067 s | 0.533 s | 2 (1.066 s) | 1 CLTU of 82 or 90 octets; 2 of up to 74 octets |
| E22 4.8k | 0.556 s | 0.338 s | 1 (the second waits one frame period) | 1, as without it |
| E22 9.6k to 62.5k | 0.800 to 1.016 s | 0.240 to 0.157 s | 2 | 2, as without it |
| SX126x profile 0 | 34.436 s | 15.680 s | 2 | 1 CLTU of 82 or 90 octets; 2 of up to 74 octets |
| SX126x profile 1 | 17.118 s | 7.866 s | 2 | 1 CLTU of 82 or 90 octets; 2 of up to 74 octets |
| SX126x profiles 2 / 3 | 8.459 / 4.540 s | 3.958 / 2.168 s | 2 | 1 CLTU of 74 to 90 octets; 2 of up to 66 octets |
| SX126x profiles 4 / 5 / 6 | 1.965 / 0.985 / 0.635 s | 1.068 / 0.600 / 0.346 s | 1 (the second waits one frame period) | 1, as without it |
| SX126x profile 7 | 0.927 s | 0.219 s | 2 | 2, as without it |

This holds in every mode, because the budget is the shortest mode's window. Worked example,
profile 1: the identification costs 3.28 s (16 octets, "DE N0CALL DSS-Q1") to 4.59 s (36
octets, the longest: callsign 16 characters, name 16) and an 82-octet CLTU 7.54 s. Two
82-octet CLTUs (15.08 s) fit the 17.118 s budget; with the identification due, only one goes
with it (3.28 + 7.54 = 10.82 s) and the second follows one frame period later. A longer
identification text leaves less room: at profile 1 with 36 octets, two 66-octet CLTUs no longer
fit beside it. In a window handled on time the identification always fits: it is booked first,
and even the longest text leaves room for one 90-octet CLTU in every setting. So it is not
deferred and every window still carries a command. It becomes due up to max(60 s, 3 frame
periods) before the 10 minutes are up (at most 600 s; [01](01_System_Design.md) section 3.4.1),
so a window still carries it when up to two downlink frames are lost; with more frames lost (or
a fade shorter than the LOS timeout) the housekeeping sends it without a window 600 s - 2 s -
its own air time after the last one, where it may collide with one downlink frame
([20](20_Known_Limitations.md) L-28). Either way the interval of at most 10 minutes holds. At
the slow profiles the long early margin brings it more often: at profile 0 (5.8 to 8.4 s on air
for the 16- to 36-octet texts) about every 184 to 323 s.
Checked for every model, rate code and mode with the longest identification text
(`tests/test_station_id.py::test_window_plan_never_overruns_and_the_id_interval_holds`,
`test_periodic_id_keeps_10_minutes_when_frames_are_lost`,
`test_late_or_batched_frames_never_overrun_the_quiet_window`).
Air times from [dssq/link.py](../dssq-groundstation/python/dssq/link.py) `busy_s()`; lengths
from `ident_text()` and `[station] callsign` validation (3 to 16 characters,
[dssq/config.py](../dssq-groundstation/python/dssq/config.py) `CALLSIGN_LEN`).

### 7.3 Ground timers follow the frame period

Slow profiles stretch every timer on the ground
([dssq/gds.py](../dssq-groundstation/python/dssq/gds.py) `_frame_period_s()`, `los_timeout_s()`,
`_derive_timers()`): P = max(4 x busy, 1.5 s); loss of signal after max(90 s, 3 P); FOP-1
timer T1 = max(45 s, 2 P); command verification timeout max(300 s, 4 P).

| Setting | P | LOS | T1 | Verification timeout |
|---|---|---|---|---|
| E22 2.4k | 5.068 s | 90 s | 45 s | 300 s |
| SX126x profile 0 | 138.5 s | 416 s | 277 s | 554 s |

---

## 8 What the ground measures and what it estimates

### 8.1 Measured

| Quantity | E22 models | SX126x | Resolution |
|---|---|---|---|
| Packet RSSI | RSSI octet appended to every packet (REG3 bit 7) | RSSI of the packet | E22 1 dB; SX126x 0.5 dB |
| Noise floor | ambient-noise register, `C0 C1 C2 C3 00 02` query in quiet windows (REG1 bit 5) | median of 8 instantaneous RSSI readings (`rf_noise`) | 1 dB / 0.1 dB steps |
| SNR | not available | per-packet SNR (SnrPkt / 4) | 0.25 dB |
| Frequency error | not available | LoRa frequency-error estimate | 1 Hz |
| Frame loss | 8-bit frame counters (TM MCFC, USLP VCF count) | same | 1 frame |
| Codeblock quality | RS symbols corrected, uncorrectable codeblocks, ASM bit errors, FECF failures | same | |
| Spacecraft side | uplink RSSI, SNR (SX126x) and noise floor, downlinked in the RF packet | | |

Sources: [dssq/rxstats.py](../dssq-groundstation/python/dssq/rxstats.py),
[dssq/radio/e22.py](../dssq-groundstation/python/dssq/radio/e22.py),
[dssq/radio/sx126x.py](../dssq-groundstation/python/dssq/radio/sx126x.py). Every reading gets
`[radio] rssi_cal_offset_db` added (calibration: T-RF-02). Absolute RSSI accuracy is not
specified by either maker: **VERIFY** T-RF-02.

Statistics windows: 1 minute, 10 minutes and the pass (`WINDOWS`). The means are shown to 4
decimals with their standard error: averaging many 1-dB-quantised readings of a fading signal
resolves finer than 1 dB, and the standard error says by how much. The FER window is 600 s
(`FER_WINDOW_S`), the same window the link manager uses.

### 8.2 Derived (labelled "est." where an assumption enters)

| Quantity | Equation | Notes |
|---|---|---|
| Signal power with noise removed | P_r = 10 log10(10^(RSSI/10) - 10^(N/10)) | |
| Noise density | N0 = N - 10 log10(BW) | E22: BW = `noise_bw_hz` = 125 kHz, **assumed** (EBYTE publishes no bandwidth); SX126x: the profile BW |
| C/N0 | E22: P_r - N0 (estimate); SX126x: SNR + 10 log10(BW) | |
| Eb/N0 | C/N0 - 10 log10(Rb) | |
| Margin | RSSI - sensitivity (section 3) | not SNR: LoRa works below the noise floor, so "RSSI - noise" near 0 dB is still a good link |
| Measured path loss | EIRP + G_rx - L_rx - RSSI | last value and 10-minute mean |
| Excess loss | measured path loss - FSPL at `[spacecraft] distance_km` | needs the distance entered |
| Range at sensitivity | the distance at which FSPL + measured excess loss uses up the margin | inverse of the FSPL formula |
| Symbol error rate | SER = RS symbols corrected / (codewords x 232) | a lower bound: only corrected symbols are seen |
| Bit error rate | between SER / 8 and SER | one to eight wrong bits per wrong octet |

Defaults used for EIRP and gains come from station.toml: ground antenna 12.0 dBi, ground
line loss 1.0 dB, spacecraft antenna 2.15 dBi, spacecraft line loss 0.5 dB, distance 0.0 km
(enter the measured path length).

---

## 9 Frequency error, Doppler and AFC (SX126x)

### 9.1 How much offset the receiver tolerates

The SX126x loses sensitivity when the transmitter and receiver frequencies differ by more than
FERR_L: +-25 % of the bandwidth, and at SF12 / SF11 / SF10 also +-50 / 100 / 200 ppm of the
carrier, whichever is tighter (SX1261/2 DS Table 3-8;
[dssq/link.py](../dssq-groundstation/python/dssq/link.py) `lora_max_freq_offset_hz()`, flight
`lora_max_freq_offset_hz()`). At 433.125 MHz:

| Profile | FERR_L | Equivalent | Practical need |
|---|---|---|---|
| 0 (SF12 / 31.25 kHz) | +-7,812 Hz | 18.0 ppm | TCXO at both ends |
| 1 (SF12 / 62.5 kHz) | +-15,625 Hz | 36.1 ppm | TCXO at both ends |
| 2 (SF12 / 125 kHz) | +-21,656 Hz | 50 ppm | ordinary crystals marginal |
| 3 to 7 (125 kHz) | +-31,250 Hz | 72.2 ppm | ordinary crystals fine |

The ground shows the offset state from the median |error| of the last 3 packets: **WARN**
above 50 % of FERR_L, **ALARM** above 80 % (`FREQ_ERR_WARN_FRACTION`,
`FREQ_ERR_ALARM_FRACTION`, `rxstats.py FREQ_STATE_SAMPLES`). The E22 modules report no
frequency error; their tolerance is EBYTE's business.

### 9.2 Doppler and range rate

Doppler = raw frequency error - `[radio] freq_offset_cal_hz` (the static crystal offset found
by calibration); range rate = -c x Doppler / f, positive when receding
([dssq/link.py](../dssq-groundstation/python/dssq/link.py) `doppler()`). At 433.125 MHz,
**1 Hz = 0.692 m/s**. On the ground the true Doppler is near zero; what the estimate shows is
crystal drift with temperature. The sign convention of the SX126x estimator is **VERIFY**
T-RF-06 (offset a signal generator by +1 kHz). The TDM export carries Doppler only when
`freq_offset_calibrated = true`.

### 9.3 Ground AFC

The ground automatic frequency control (contract 24.8) steers the station receiver's offset
toward the spacecraft's carrier with the `rf_foff` call to the station MCU
([dssq/link.py](../dssq-groundstation/python/dssq/link.py) `AfcLoop`):

| Rule | Value | Constant |
|---|---|---|
| Packets used | CRC good, \|error\| <= 0.5 x BW, SNR >= SNRmin + 3 dB | `AFC_SNR_MARGIN_DB` |
| Estimate | median of the last 8 accepted packets; first step after 3 | `AFC_WINDOW`, `AFC_MIN_SAMPLES` |
| Deadband | max(50 Hz, 2 % of BW) | `AFC_DEADBAND_MIN_HZ`, `AFC_DEADBAND_BW_FRACTION` |
| Largest step | 25 % of FERR_L | `AFC_SLEW_FERR_FRACTION` |
| Largest correction | 80 % of FERR_L and at most 50,000 Hz (the station's `rf_foff` range) | `AFC_BOUND_FERR_FRACTION`, `AFC_RPC_MAX_HZ` |
| Settling | a packet counts only if it ended one air time + 0.5 s after a change | `AFC_SETTLE_MARGIN_S` |
| Confirmation | `rf_foff` answer within 10 s, else retried after 5 s; results 1 applied, -1 out of range, -2 radio down, -3 no answer | `AFC_CONFIRM_TIMEOUT_S`, `AFC_RETRY_S`, `AFC_RPC_RESULTS` |
| Sign self-check | 3 packets after each step: if the residual grew by more than half the step, the sign is inverted once (event); a second divergence stops the loop (FAULT) and returns to the static offset | `AFC_CHECK_SAMPLES` |
| Loss of signal | hold the correction for 600 s, then return to the static offset | `AFC_LOS_HOLD_S` |

States: DISABLED (E22 models, or `afc = false`), STATIC, TRACK, CHECK, HOLD, FAULT. For
profile 1: deadband 1250 Hz, largest step 3906 Hz, bound 12,500 Hz. The station applies a new
offset at once, or right after a transmission, CAD or packet in progress
([sketch.ino](../dssq-groundstation/sketch/sketch.ino) `rf_foff`), exact to the SX126x PLL
step of 32 MHz / 2^25 = 0.95 Hz (SX1261/2 DS 13.4.1).

---

## 10 Choosing a rate: profiles, AIRRATE and the link manager

### 10.1 What can be chosen

* **E22 models**: only the air rate (codes 2 to 7 are distinct). There is no spreading-factor
  or bandwidth control: EBYTE's firmware decides. 2.4 kbit/s is the slowest and most sensitive
  setting.
* **SX126x**: 8 LoRa profiles (section 2.5). Profile 1 is the default compromise: 12 dB more
  margin than the E22 at 2.4k, at about 1/14 of its data capacity.

Both ends must use the same setting. `AIRRATE code revert_s` (and `CHANNEL ch revert_s`)
changes the spacecraft after two more frames on the old setting and reverts by itself after
`revert_s` (60 to 3600 s, default 180 s from `[linkmgr] revert_s`) unless a valid RF uplink
arrives on the new setting. The ground directives `LINKRATE` / `LINKCHAN` run the whole
coordinated procedure ([04](04_Space_Data_Link_ICD.md) section 8.1).

| Need | E22 setting | SX126x profile |
|---|---|---|
| Maximum range, little data | code 2 (2.4k) | 0 (TCXO needed) or 1 |
| Default | code 2 | 1 |
| More data at short range | code 3 or 4 | 4 or 5 |
| Bench work, fastest | code 5 to 7 | 7 |

### 10.2 Link manager

The adaptive link manager ([dssq/linkmgr.py](../dssq-groundstation/python/dssq/linkmgr.py),
station.toml `[linkmgr]`) recommends or makes rate changes from **measured** margin:

| Rule | Value |
|---|---|
| Target margin | 6.0 dB (`target_margin_db`) |
| Evaluation | every 60 s (`period_s`), at most one change per 600 s (`holdoff_s`) |
| Data needed | at least 10 frames in the last 10 minutes |
| Margin statistic | 10th-percentile RSSI of the 10-minute window minus the sensitivity of the current code (fade-aware) |
| Step down (slower) | margin below target - 3 dB, or FER above 10 % |
| Step up (faster) | margin above target + 9 dB and FER below 1 %, and only if the margin predicted at the faster code stays at least target + 3 dB |
| Limits | lowest code: the model's minimum (E22 2, SX126x 0) unless `min_rate` is set; highest code 4 (`max_rate`) |
| Modes | OFF, ADVISE (default: shows the recommendation), AUTO (runs the coordinated `LINKRATE`) |

The rate is the only lever: a power change would alter both ends' regulatory envelope, and the
T37S has no power levels. Power stays an operator decision (`TXPWR`).

---

## 11 Channel access, duty cycle and error rescue

### 11.1 Listen before talk

* E22: REG3 bit 4 enables LBT inside the module; it defers at most 2 s, then transmits anyway
  (T33D manual 7.2 REG3). Flight default off (`kRadioLbt = false`), station `lbt = false`.
* SX126x: channel-activity detection (CAD) before each transmission, the same 2 s limit
  (`kSxLbtMaxWaitMs`); flight default off, station on (`cad_before_tx = true`).
* `RADIOOPT` switches LBT / CAD (bit 0) and Rx boosted gain (bit 1) on the spacecraft. The
  number of deferred transmissions is in the RF packet (`lbt_busy`).

### 11.2 Duty cycle and PA temperature

The spacecraft never transmits more than 50 % of the time (`kMaxTxDutyPermille` = 500, a PA
thermal limit) and never faster than one frame per 1.5 s (`kMinFramePeriodMs`): the frame
period is at least the busy time / 0.5 ([fsw/datalink.cpp](../vgq1-spacecraft/sketch/src/fsw/datalink.cpp)
`frame_period_ms()`). The rules for the frame period, including the doubling while the T37S
reports over-temperature, are in [01_System_Design.md](01_System_Design.md) section 5.2.

The duty the spacecraft reports (RF `tx_duty_permille`, `sc_status` `duty_pct`) is measured
by `TxDutyMeter` over whole transmission cycles (launch to next launch; the last 16 cycles
launched within 300 s; an open cycle counts as max(elapsed, period in force), so the reading
decays to 0 while the transmitter is silent). At the SF12 busy times it reads the true value
(50.0 % for profile 1 in ENCOUNTER, 25.0 % for profile 0 in SAFE), where a fixed 60 s window
would swing up to 57.7 % and trip the ground's YELLOW limit (55 %; RED 70 %). Details:
[01_System_Design.md](01_System_Design.md) section 5.2. On the E22, the flight does not start
a transmission within 100 ms of a received octet (`kE22RxQuietMs`), so it does not talk over
an uplink in progress.

### 11.3 Error rescue: LoRa CRC plus Reed-Solomon

Two codes protect each downlink packet: the radio's LoRa CRC and the CCSDS RS(255,223) code,
which corrects up to 16 wrong octets per frame.

* The E22 firmware passes on only packets whose LoRa CRC was good.
* The SX126x path also hands over packets with a failed LoRa CRC when they are exactly one
  236-octet CADU (station `rf_rx` with `crc_ok = 0`): the RS decoder often repairs them, which
  gains frames near the sensitivity limit. On the spacecraft every CRC-failed uplink packet is
  passed to the BCH, FECF and SDLS checks; one that passes them is executed and counted
  (`tc_rescued`, EVR 0x0307).

### 11.4 Regulatory

Power, bandwidth, identification and encryption rules depend on your licence and country.
The OID frames send the callsign in clear text and EVR 0x0002 repeats it every 10 minutes
(`kCallsignPeriodS`). Encryption (SDLS AE, the default) is generally not permitted on amateur
frequencies. See [09_Regulatory_and_Safety.md](09_Regulatory_and_Safety.md).

---

## 12 Open items (VERIFY) owned by this document

| Item | Test |
|---|---|
| T33D output power of codes 1 to 3 (assumed 30 / 27 / 24 dBm); SX126x module PA gain (11 dB) | T-RF-07 |
| SX126x receiver noise figure (6 dB assumed); every "computed" and "estimated" sensitivity; E22 air time factor 1.3 | T-RF-03 |
| Absolute RSSI accuracy (`rssi_cal_offset_db`) | T-RF-02 |
| Sign of the SX126x frequency-error estimate | T-RF-06 |
| SX126x module TCXO voltage (1.8 V), DIO2 RF switch (false), OCP 140 mA for the SX1268 | module datasheet and bench check |
| Real range on your path (all budgets here are free-space plus horizon only) | range test, [08](08_Verification_and_Test.md) and [21](21_Test_Records.md) |
| EBYTE reference distances (16 km / 20 km) are vendor claims | range test |
