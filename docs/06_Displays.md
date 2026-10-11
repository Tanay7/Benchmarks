# 06 - Displays (receiver web dashboard, station lamps, transmitter status)

## 1 Receiver: the web dashboard (VENTUNO Q)

Served by the GDS itself (`dssq/display/web.py`, Python standard library only) at
`http://<ventuno>:8080` (configure `[gds] http_host/http_port`). It refreshes every second from
`/api/state`. Every value is either **measured** or labelled **est.** with its assumption.
`—` means "no data yet / not available" - never a made-up number.

### 1.1 Header bar (always visible)

Station name · **AOS/LOS** · frame-sync state (**SEARCH/LOCK/FLYWHEEL**) · **ALARM**
(NONE/YELLOW/RED) · spacecraft name and mode · UTC · RSSI · SNR est · margin est · FER · frames ·
SCLK (partition/seconds) · COP-1 state (V(S), N(R), lockout/suspended, SDLS).

### 1.2 OVERVIEW

Tiles: signal (pass duration), RSSI and noise, SNR est, margin est vs sensitivity, frames and
FER, information rate, RS symbols corrected / uncorrectable, spacecraft mode and FDIR count, bus
temperature, spacecraft radio health (E22 self-report), uplink RSSI at the spacecraft, commands
accepted/rejected. Signal-history chart (RSSI, noise, SNR, last 300 frames), spacecraft summary,
latest events, latest commands.

### 1.3 RECEIVER (the detailed receiver page)

| Panel | Contents |
|---|---|
| Receiver statistics | For packet RSSI, noise floor, SNR est, frame inter-arrival time, RS corrections per codeblock and ASM bit errors: last value, then for **1 min, 10 min and the whole pass**: mean, **± standard error**, σ, min, p10, p50, p90, max, n. RSSI/noise/SNR to **4 decimals**. |
| RF & link estimates | frequency/channel, air rate, last RSSI (1 dB steps), noise floor and its age, SNR est, P_r with noise removed, N0, C/N0, Eb/N0, sensitivity, link margin (colour-coded), fade depth p90-p10 (1 min / 10 min), assumed noise bandwidth |
| **Link budget** | predicted (free space) vs measured: TX power (37 dBm fixed), − feeder loss, + antenna gain, **= EIRP**, distance and one-way light time, **− FSPL** (4 decimals) vs measured path loss (last and 10-min mean), + ground antenna gain, − ground feeder loss, **= received power** (predicted vs 10-min mean RSSI ± SEM), excess loss, noise floor, SNR, sensitivity, **= link margin**, range at sensitivity |
| **RSSI detail** | last packet (1 dB resolution stated), means 1 min / 10 min / pass to 4 decimals ± SEM with n, received power in pW (4 significant digits), noise floor mean ± SEM, calibration offset in use |
| Two-way link | downlink (ground E22) vs uplink (spacecraft E22): RSSI, noise floor, SNR, margin, packets/CLTUs, errors corrected (RS symbols / BCH blocks), transmitter activity, frame period/air time, radio configuration verified |
| Frame synchronizer | state, ASMs found, ASM bit errors (total, mean), slips, flywheel frames, lock losses, discarded octets, noise replies, time in lock, serial octets and rate, noise queries/replies |
| Reed-Solomon decoder | codeblocks, clean, corrected, uncorrectable, symbols corrected, max in one codeblock (/16), symbol error rate est, pre-FEC BER range est, FECF failures after RS, foreign SCID/bad header |
| Frames & throughput | frames OK, lost (MCFC gaps), FER, idle frames, channel efficiency, frames per minute, information rate, CADU rate on air, passes, time since last frame |
| Charts | RSSI/noise/SNR/margin time series; RSSI and SNR histograms; RS corrections and ASM-error histograms |
| Ground radio & link manager | RCU present, ground E22 read-back vs station.toml, allowed channels, coordinated change state, link-manager mode/recommendation/margin p10/FER, radio jobs |
| RF spectrum survey | ambient noise per E22 channel from the ground (GSCAN) and the spacecraft (RFSCAN), allowed channels marked |

### 1.4 FRAMES & PACKETS

Per virtual channel: frames, VCFC gaps, lost frames, packets, idle packets, discarded partial
packets, bad lengths. Per APID: count, sequence gaps, lost packets, last sequence count, age,
latency (ERT − SCET: mean/min/max), size, VC. Time correlation: partition, last SCLK, its SCET,
samples, drift (ppm), fit residual RMS (ms), OWLT. CLCW/COP-1: N(R), lockout, retransmit,
FARM-B counter, V(S), outstanding, queued, suspended.

### 1.5 SPACECRAFT

HK and RF packets as full parameter tables with limit colouring (yellow/red from the
dictionary); fault protection: mode, last reset cause, FARM-1 state, E22 self-report, active FDIR
flags, instruments OK/missing, downlink air rate; anomaly detector (EWMA mean/variance per
parameter, |z| > 6 after warm-up) - catches "unusual for this spacecraft" before a limit trips.

### 1.6 SCIENCE

* **Magnetometers**: outboard RM3100, inboard RM3100 (optional), body BMM150 - Bx, By, Bz, |B|,
  RMS fluctuation (µT); gradient IB−OB; with two RM3100s the estimated spacecraft field at the
  outboard sensor and the corrected ambient field (dual-magnetometer method, 1/r³); samples,
  PA state during sampling, RM3100 cycle count.
* **Attitude**: BHI260 fusion quaternion and yaw/pitch/roll, its accuracy estimate (rad and °),
  the independent TRIAD quaternion and Euler angles, the **difference between the two
  solutions** (°), acceleration and |a|, angular rate, BMM150 field and |B|.
* **Environment**: every ENV field (Nicla Sense Env HS4001 / ZMOD4410 / ZMOD4510, Nicla Sense ME
  BMP390 / BME688-BSEC).
* **AS7265X**: calibrated spectrum bar chart (18 bands, colour by wavelength), raw-count chart,
  validity, gain and integration time, lamps in use, three die temperatures.

### 1.7 COMMANDING

Command entry (validated against the dictionary as you send), pending confirmations with
CONFIRM / 2nd-operator AUTH / cancel buttons, command history with the full life cycle and
timestamps (queued, radiated, accepted, done) and radiate-to-done time, COP-1/SDLS state,
**Station I/O** (MCU link, firmware, CardKB #1/#2 presence, RCU, and the line currently being
typed on CardKB #1), console, and the full command dictionary.

### 1.8 EVENTS

Station event log (AOS/LOS, limits, decoder, commands, radio, link manager, security) and the
spacecraft's EVRs with ERT, SCLK, severity and event id.

### 1.9 Machine interfaces

`/api/state` (JSON snapshot), `/api/dictionary`, `/metrics` (Prometheus text format: every
receiver statistic and every telemetry parameter), POST `/api/cmd`, `/api/confirm`, `/api/cancel`
(token-protected when `http_token` is set; the page asks for the token on the first 403).
Optional UDP forwarding of TM frames/packets to YAMCS or OpenC3 and a UDP TC-frame input
(`[interop]`).

## 2 Receiver station lamps (VENTUNO Q MCU, App `ground/station_io`)

| Lamp | Meaning |
|---|---|
| AOS (green) | on = signal acquired (frames arriving) |
| ALARM (red) | steady = YELLOW alarm; 4 Hz blink = RED alarm; **10 Hz blink = GDS link down** (no status from Linux for 5 s) |
| UPLINK (yellow) | commands outstanding (radiated, not yet acknowledged) |
| Buzzer | 0.4 s beep when an alarm turns RED or the signal is lost |

## 3 Transmitter status

### 3.1 UNO Q LED matrix (13 x 8, always present)

| Row | Shows |
|---|---|
| 0 | fully lit while the PA is keyed (transmitting) |
| 1 | flashes when an uplink packet arrives |
| 2 | COP-1: dim = FARM open, blinking = LOCKOUT |
| 3 | VC0 (engineering) backlog bar, 13 columns = 100 % |
| 4 | VC1 (science) backlog bar |
| 5 | solid-state recorder fill bar |
| 6 | instrument health, one column per HK `sensor_health` bit 0-12 |
| 7 | columns 0-4: mode (BOOT, SAFE, CRUISE, ENCOUNTER, TEST); columns 8-12 blink = FDIR flag active |

### 3.2 EGSE status board (UNO Q Linux, App Lab console, every 10 s)

Mode, SCLK partition/seconds, frames sent (and idle frames), MCFC; channel and frequency, air
rate, power code, TX on/off and inhibit; frame period, radio busy time, duty cycle, bus
temperature, E22 fault code; uplink RSSI, noise floor, FARM state and V(R), commands accepted/
rejected; FDIR and instrument bit masks, VC backlogs, SSR fill; CADUs archived per VC and EVR
count. The same values are logged as JSON lines (`egse_data/sc_status_YYYYMMDD.jsonl`).
