# 02 - Link Budget and RF Analysis

All numbers below are either from a datasheet (cited), from physics (equation given), or
labelled **estimate**. Re-compute any case with `python3 tools/link_budget.py --help`.

## 1 The radio, as specified by EBYTE (E22-xxxT37S User Manual v1.5)

| Item | Value | Consequence |
|---|---|---|
| Output power | 37 dBm (5 W), min 36 / max 37.5 dBm | |
| Power levels | **none** - REG1 codes 00/01/10/11 are all 37 dBm | output cannot be reduced in software; bench tests need attenuators |
| Air data rate codes (REG0 bits 2:0) | 000, 001, 010 = **2.4k**; 011 4.8k; 100 9.6k; 101 19.2k; 110 38.4k; 111 62.5k | 2.4 kbit/s is the slowest, most sensitive setting |
| Receiving sensitivity | -125 / **-126 typ** / -127 dBm at 2.4 kbit/s | other rates are not specified: estimated at -10 log10(rate / 2400) dB |
| Supply | 4.5-15 V; TX 3.1 A at 5 V, **1.1 A typ / 1.3 A max at 12 V**; RX 43 mA at 12 V | supply >= 1.5 A at 12 V |
| Blocking (max input) | +10 dBm | never couple two units closely |
| Sub-packet | 240 octets default | one 236-octet CADU per packet |
| Frequency | 410.125 + CH x 1 MHz, CH 0-83 | default CH 23 = 433.125 MHz (factory default 0x17) |
| Reference range (EBYTE) | 20 km, clear air, 5 dBi antennas at 2.5 m, 2.4 kbit/s | consistent with the horizon analysis below |

EBYTE does not publish the LoRa spreading factor / bandwidth behind each air-rate code, so the
GDS's C/N0 and Eb/N0 values use an assumed 125 kHz noise bandwidth (`noise_bw_hz`) and are
labelled "est." on every display.

## 2 Equations

| Quantity | Equation |
|---|---|
| Free-space path loss | FSPL(dB) = 32.44 + 20 log10(f/MHz) + 20 log10(d/km) |
| EIRP | P_tx + G_tx - L_tx |
| Received power | P_r = EIRP - FSPL - L_excess + G_rx - L_rx |
| Margin | P_r - S (sensitivity) |
| Thermal noise density | -174 dBm/Hz at 290 K |
| Shannon-limit sensitivity | S_min = -174 + 10 log10(R_b) + (-1.59 dB) + NF |
| Radio horizon (4/3-earth) | d = 4.12 (sqrt(h1/m) + sqrt(h2/m)) km |
| 1st Fresnel radius at mid-path | r = 17.32 sqrt(d1 d2 / (f/GHz x d)) m |
| Antenna effective area | A_e = G lambda^2 / (4 pi) |

## 3 Worked link budgets (433.125 MHz, 2.4 kbit/s)

### 3.1 Two dipoles (2.15 dBi), 10 km, antennas at 2 m and 10 m

| Item | Value |
|---|---|
| TX power | 37.0 dBm |
| TX feeder loss / antenna gain | -0.5 dB / +2.15 dBi |
| **EIRP** | **38.65 dBm** |
| FSPL at 10 km | -105.17 dB |
| RX antenna gain / feeder loss | +2.15 dBi / -1.0 dB |
| **Received power (free space)** | **-65.4 dBm** |
| Sensitivity (datasheet) | -126 dBm |
| **Free-space margin** | **60.6 dB** |
| Radio horizon for 2 m + 10 m | **18.9 km** |
| 1st Fresnel radius at 5 km | 41.6 m (keep >= 25 m clear) |

### 3.2 Dipole to 12 dBi Yagi, 40 km, antennas at 30 m and 300 m (hill to hill)

EIRP 38.65 dBm, FSPL 117.2 dB, P_r = -67.6 dBm, margin 58.4 dB, horizon 93.9 km, Fresnel radius
83.2 m at mid-path.

### 3.3 What limits the range

In free space this link would close over thousands of kilometres (3,400 km with dipoles and
10,600 km with a 12 dBi ground Yagi, keeping a 10 dB fade margin) - the deep-space analogy is
real: the link budget, not the transmitter, is what makes deep-space communication possible.
**On Earth the limit is geometry**: the radio horizon and the obstruction of the first Fresnel
zone by terrain, buildings and vegetation. Ranking of what to improve, largest effect first:

1. **Antenna height** (horizon and Fresnel clearance): hilltops, masts, balloons.
2. **Line of sight** (no buildings/trees in the first Fresnel zone).
3. **Ground antenna gain** (a 10-13 dBi Yagi adds 8-11 dB over a dipole) and low feeder loss.
4. **Air rate** (2.4 kbit/s is already the minimum on the T37S).
5. **Quiet channel** (`GSCAN` / `RFSCAN` find the quietest allowed channel; the ambient-noise
   register shows local interference that the datasheet sensitivity assumes absent).

### 3.4 Range, frame rate and data rate (from the flight software's timing rules)

Radio busy time per CADU is measured by the spacecraft; before the first measurement it uses
UART time + 1.3 x (CADU bits / air rate). Data-field capacity = 188 octets / frame period.

| Air rate | Busy time per CADU (estimate) | Frame period SAFE / CRUISE / ENCOUNTER | Capacity CRUISE / ENCOUNTER |
|---|---|---|---|
| 2.4k (codes 0-2) | 1.27 s | 5.1 / 3.2 / 2.5 s | 59 / 74 octets/s |
| 4.8k | 0.76 s | 3.0 / 1.9 / 1.5 s | 99 / 124 octets/s |
| 9.6k | 0.50 s | 2.0 / 1.5 / 1.5 s | 125 / 125 octets/s |

Offered telemetry load (packet sizes from the dictionary, 12-octet headers included):
SAFE 3.2 octets/s (26 bit/s), CRUISE 5.8 octets/s (46 bit/s), ENCOUNTER 32.3 octets/s
(258 bit/s). **Every mode fits at 2.4 kbit/s** (ENCOUNTER uses 44 % of the capacity), so the
maximum-range setting costs no science.

## 4 Claims checked against physics

### 4.1 "35 dBi" antennas

A_e = G lambda^2 / 4 pi with G = 10^3.5 = 3162 and lambda = 0.692 m gives A_e = 120.6 m^2 - the
aperture of a parabolic dish about 16 m in diameter (60 % efficiency). A hand-sized 433 MHz
antenna is a monopole/dipole of about 2 dBi; a 433 MHz Yagi of practical length reaches
10-13 dBi. All budgets here use 2.15 dBi for the supplied antennas.

### 4.2 Sensitivity figures

No receiver can do better than the Shannon limit: for 2.4 kbit/s, S_min = -174 + 33.8 - 1.59 =
**-141.8 dBm** even with a noiseless (0 dB NF) front end, -135.8 dBm with 6 dB NF. The T37S
datasheet's -126 dBm sits 9.8 dB above that 6 dB-NF limit - physically plausible. Sensitivity
figures far below -142 dBm quoted "at 2.4 kbps" (seen for some sibling modules) cannot refer to a
2.4 kbit/s information rate; they belong to the lowest-rate setting.

### 4.3 RSSI accuracy

The E22 reports RSSI as one octet, dBm = -(256 - value): **1 dB resolution** per packet, absolute
accuracy not specified by EBYTE. The GDS therefore shows the instantaneous value at 1 dB
resolution and the 1 min / 10 min / pass **means to 4 decimals with their standard error**
(averaging many 1-dB-quantised readings of a fading signal resolves finer than 1 dB; the
standard error says how much). Absolute accuracy comes from calibration: measure with a signal
generator or a known attenuator chain and set `[radio] rssi_cal_offset_db`
([08](08_Verification_and_Test.md) T-RF-02).

### 4.4 SNR below the noise floor

LoRa demodulates below the noise floor (its required SNR is negative), so "RSSI - noise floor"
near 0 dB is still a working link; the RSSI register itself saturates near the floor in that
regime. That is why the margin shown is RSSI minus the datasheet sensitivity, not SNR.

## 5 Measured quantities on the RECEIVER tab

| Measured | Source |
|---|---|
| Packet RSSI | E22 RSSI octet appended to every packet (REG3 bit 7) |
| Noise floor | E22 ambient-noise register (C0 C1 C2 C3 00 02 query, REG1 bit 5) |
| Frame loss | MCFC gaps (8-bit master channel frame counter) |
| Codeblock quality | RS corrections per codeblock, ASM bit errors, FECF failures after RS |
| Uplink RSSI / spacecraft noise floor | the spacecraft's own E22 registers, downlinked in the RF packet |

| Derived (labelled est.) | Equation |
|---|---|
| P_r (noise removed) | 10 log10(10^(RSSI/10) - 10^(N/10)) |
| C/N0, Eb/N0 | P_r - (N - 10 log10 B); C/N0 - 10 log10 R_b |
| Measured path loss | EIRP + G_rx - L_rx - RSSI (last, and 10-min mean) |
| Excess loss | measured path loss - FSPL at the configured distance |
| Range at sensitivity | distance where FSPL + measured excess loss would use up the margin |

## 6 Beyond this module: the SPI path to more range

The E22-400T37S is a UART module: EBYTE's firmware chooses the LoRa parameters and exposes only
the registers above. EBYTE's SPI modules with the same SX1268 radio chip (E22-400M30S 1 W,
E22-400M33S 2 W) give full control through **RadioLib** (`SX1268` class): spreading factor up to
12 and bandwidths down to 7.8 kHz (Semtech specifies the SX126x family down to about -148 dBm),
`getSNR()`, `getFrequencyError()`, channel-activity detection, LR-FHSS. That is ~20 dB more
sensitivity than -126 dBm - 10x the free-space range - at much lower bit rates. Changing to an
SPI module replaces only `drivers/e22.*` and the ground radio interface; everything above the
physical layer (CCSDS, COP-1, SDLS, GDS) stays as it is. See
[11 Standards & Roadmap](11_Standards_Status_and_Roadmap.md).
