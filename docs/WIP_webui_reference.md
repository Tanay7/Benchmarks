# Ground Station Web UI Reference (draft input for Phase D docs)

Source: Phase B spec §5 (snapshot schema, endpoints, dashboard) and §1 (packet layouts), as given to the owner in chat on
2026-10-11. Re-synchronise every key with the final implementation before publishing (contract §31).

## Display conventions
- dB/dBm statistics: 4 decimal places. Single readings: 4 dp plus their hardware resolution (E22 RSSI 1 dB; SX126x RSSI 0.5 dB,
  SNR 0.25 dB). Hz 1 dp, m/s 3 dp, rates/percent 2 dp, FER/BER/CER scientific with 3 significant digits, times UTC ISO-8601.
- Derived values carry a label (est. / approximate / heuristic); sensitivity carries a source badge (measured, datasheet,
  computed, estimated). Unavailable values show "—". Nothing is invented.

## 1. Header bar (always visible)
| Metric | Description |
|---|---|
| Station / Spacecraft | Names: DSS-Q1 / VGQ-1. |
| AOS / LOS | Acquisition / Loss of Signal; "LOS (hibernating)" without an alarm when the spacecraft hibernates. |
| Sync state | Frame synchroniser: SEARCH, LOCK, FLYWHEEL (E22) or PACKET (SX126x). |
| Alarm | NONE / YELLOW / RED; hovering lists every reason (section 14). |
| Beacon banner | Full-width band: NOMINAL none, INTERESTING blue, IMPORTANT amber, URGENT red flashing, "since <time>". |
| Mode | GDS run mode: host / App Lab / simulator. |
| UTC | Station time. |
| RSSI 1-min mean ± SEM | Mean signal strength over the last minute with its standard error, 4 dp. |
| SNR / Margin / FER | Signal-to-noise ratio, margin over sensitivity, frame error rate. |
| Frames, SCLK, COP-1, SDLS TM state, framing seen | Frames received, latest spacecraft clock, uplink protocol state, downlink encryption state, TM/USLP frames seen. |

## 2. OVERVIEW tab
Summary tiles, signal history chart, spacecraft summary (mode, health, beacon), latest events, last command status.

## 3. RECEIVER tab
### 3a. RF now
| Metric | Unit | Description |
|---|---|---|
| Frequency | MHz/Hz | 410.125 + channel MHz. |
| Model | — | E22-400T33D, E22-400T37S or SX126x. |
| Air-rate code / label / bps | — | E22 air data rate or SX126x profile (SF/BW) and its bit rate. |
| RSSI (packet) | dBm | Calibrated signal strength of the last packet. |
| RSSI raw | dBm | Before the station calibration offset. |
| RSSI resolution | dB | 1.0 (E22) or 0.5 (SX126x). |
| RSSI cal offset | dB | Calibration correction from test T-RF-02; sets absolute accuracy. |
| RSSI (mW) | mW | Linear signal power. |
| Noise floor | dBm | Calibrated; E22 noise register or SX126x median of 8 instantaneous RSSI samples. |
| Noise source / age | — / s | Origin and age of the noise value. |
| SNR est. | dB | RSSI − noise. |
| Pr est. | dBm | Received carrier power estimate. |
| N0 est. | dBm/Hz | Noise power spectral density. |
| C/N0 est. | dB-Hz | Carrier-to-noise density with its source (E22 from RSSI/noise and bandwidth; SX126x measured SNR + 10·log10(BW)). |
| Eb/N0 est. | dB | Energy per bit over noise density. |
| Noise bandwidth | Hz | Bandwidth used in the noise maths, with source (profile BW / assumed). |
| Sensitivity | dBm | Receiver sensitivity for the current setting, with source badge. |
| Margin est. | dB | RSSI − sensitivity. |
| Required SNR | dB | LoRa demodulator minimum SNR for the SF (SX126x). |
| SNR margin | dB | Measured SNR − required SNR (SX126x). |
| Frequency error | Hz | Raw carrier offset of the last packet (SX126x). |
| Frequency-offset limit (FERR_L) | Hz | Maximum tolerable offset (±25 % of BW). |
| Frequency-offset state | — | OK / WARN (> 50 % of limit) / ALARM (> 80 %). |
| CRC-fail packets | count | SX126x packets with a bad CRC. |
| Packet error rate | ratio | CRC fails / (good + CRC fails), SX126x. |
| Last packet | — | RSSI, SNR, frequency error, CRC OK, time. |

### 3b. Statistics: 8 metrics × 3 windows × 11 statistics
Metrics: RSSI, noise, SNR est., SNR measured, frequency error, inter-arrival time, RS symbols corrected, ASM bit errors.
Windows: 1 min, 10 min, this pass (since AOS, max 20,000 samples, "truncated" flag).
Statistics: n, mean, min, max, std, SEM, p10, p50 (median), p90, last, fade depth.

### 3c. Charts and histograms
RSSI/noise/SNR and frequency-error time series (last 300 frames); histograms of RSSI (5 dB), SNR (2 dB), frequency error
(100 Hz), RS corrections (0–16), ASM bit errors (0–4); RF survey (ground GSCAN + spacecraft RFSCAN noise per channel).

### 3d. Link budget (design vs measured vs uplink)
| Term | Description |
|---|---|
| SC TX power nominal + power verified | Spacecraft transmit power; VERIFY until test T-RF-07 (T33D table, SX126x). |
| SC antenna gain, line loss, EIRP est. | Spacecraft radiated power. |
| Distance / one-way light time | Configured distance and travel time. |
| FSPL | Free-space path loss, exact 20·log10(4π·d·f/c) (constant 32.44778 dB), formula shown. |
| Ground antenna gain / line loss | Station antenna and cable. |
| Predicted Pr / margin / SNR | What the budget predicts. |
| Measured path loss est. (now, 10-min mean) | Actual loss from measured RSSI. |
| Excess loss est. | Measured − free-space loss (terrain, obstacles, mismatch). |
| Range at sensitivity est. | Distance where the signal would reach the decode threshold. |
| Thermal noise (kTB) | −174 + 10·log10(B) dBm. |
| Noise figure assumed | 6 dB (SX126x computed sensitivity); n/a for E22. |
| Uplink section | Ground TX power and EIRP, spacecraft sensitivity and source, predicted received power and margin at the spacecraft, measured uplink RSSI reported by the spacecraft, measured uplink margin. |

### 3e. Radio configuration
Model, interface, packet mode, rate code/label, bps, SF/BW/CR/LDRO, power code and dBm (VERIFY badge), channel, frequency,
Rx boosted gain, CAD/LBT, sensitivity and source, air time est., busy time est. (incl. UART transfer), FERR_L, static
frequency calibration, fault-log / noise-register capability, model seen in telemetry and mismatch warning, RCU present,
ground config read-back, ground fault, last scan, allowed channels, configured values, link change in progress, jobs pending.

### 3f. AFC (automatic frequency correction, SX126x)
State, correction applied, estimated total offset, residual, sign convention (self-checked), static offset, Doppler,
pending step, samples, limits (bound/slew/deadband), minimum SNR, accepted/rejected (low SNR, invalid)/skipped (settling)
counts, steps, sign flips, RPC failures, last 20 AFC events.

### 3g. Doppler / range-rate
Static calibration offset and calibrated flag, Doppler shift (Hz), range-rate = −c·Doppler/f (m/s), source (AFC estimate or
raw frequency error).

### 3h. Decoder
| Metric | Description |
|---|---|
| Codeblocks / clean / corrected / uncorrectable | Reed-Solomon codeword outcomes. |
| Symbols corrected / max per codeword | Error-correction workload. |
| SER est. / BER est. range | Symbol and bit error rates inferred from RS corrections. |
| CER | Codeword error rate (uncorrectable / total). |
| FECF fail after RS | Frames passing RS but failing the frame checksum. |
| Foreign / bad header | Frames from another spacecraft or malformed. |
| RS rescued / rescue failed | SX126x CRC-failed packets recovered by Reed-Solomon. |
| Rice errors | CCSDS 121.0 decompression failures. |
| Decom errors | Packet-decode failures. |
| SDLS rejected | Security rejections by reason: BAD_MAC, BAD_IV, BAD_SPI, BAD_LENGTH, REPLAY. |

### 3i. Frames
Good/lost, FER = lost/(good+lost), idle (OID) frames and idle %, channel efficiency % (packet octets / data-field octets),
frames per minute, information rate and CADU rate (bit/s), counts by format (TM/USLP), frames and losses per VC.

### 3j. Other links
SX126x MCU status (rx ok, CRC errors, header errors, tx ok/failed, channel busy/forced, faults, resets, Rx-gain register and
verified flag); serial link (E22: octets, octets/s, noise queries/replies); radio link health (connected, faults, reopens,
TX dropped, last error, port); uplink (CLTUs, octets, last time, windows, rejected-too-long, framing); two-way link
(spacecraft's view of the uplink, uplink margin); AOS/LOS list (last 20); receiver state (passes, pass duration, time since
last frame, time-in-lock %, effective LOS timeout, hibernating).

## 4. LINK & SECURITY tab
Framing (configured auto/TM/USLP, frames per format, uplink framing); SDLS (TC/TM services, TM epoch, last SN, TM frames OK,
auth failures, replays, unprotected frames, next TC SN, spacecraft TM state, mismatch suspected, epoch regressed (RED alarm:
possible nonce reuse), key file, key loaded, TC SN state OK/BLOCKED); OID ident text (rendered as text); COP-1/FOP state
(frame limit 64); CLCW; time correlation (partition, last SCLK, SCET, samples, drift ppm, residual RMS ms, one-way light time).

## 5. FRAMES & PACKETS tab
Per VC (VC0–3, OID): frames, lost, last frame count, SDLS rejects, last receive time, packet-extractor statistics.
Per APID: count, sequence gaps, lost, last sequence, age, measured inter-arrival time, latency statistics, size, VC.

## 6. SPACECRAFT tab
### Housekeeping (HK, 78 octets, every field decoded, enums named, limits coloured)
| Field | Unit | Description |
|---|---|---|
| fsw_mode | — | BOOT, SAFE, CRUISE, ENCOUNTER, TEST. |
| sclk_partition | — | Boot counter = downlink encryption epoch. |
| uptime_s | s | Time since boot. |
| reset_cause | — | POWER-ON, PIN, SOFTWARE, WATCHDOG, BROWNOUT (YELLOW), UNKNOWN. |
| fdir_flags | bits | Active fault-protection conditions, named. |
| sensor_health | bits | Instruments OK / missing, named. |
| cmd_accepted / cmd_rejected | count | Command execution totals. |
| cmd_last_opcode / cmd_last_status | — | Last command and its error code. |
| tc_frames_ok / tc_frames_bad | count | Uplink frames passing / failing checks. |
| farm_state / farm_vr | — | Onboard COP-1 receiver state (OPEN/WAIT/LOCKOUT) and next expected frame number. |
| cmd_loss_timer_s | s | Time since last ground contact (autonomous safing timer). |
| bus_temp | °C | Bus temperature (BME688); limits −20/0/60/75 °C. |
| radio_fault / radio_fault_reports | — | Last radio fault (T37S codes or SX126x BUSY-stuck/SPI/re-init/no-IRQ) and count. |
| loop_max_ms / loop_overruns | ms / count | Worst main-loop time, passes over 250 ms. |
| i2c_errors | count | Sensor-bus errors. |
| ssr_fill / ssr_dropped / ssr_kib | % / count / KiB | Recorder fill (limits 80/95 %), drops, size. |
| vc0–vc3 backlog | octets | Data queued per virtual channel. |
| sdls_tc_service / sdls_tm_service | — | Uplink / downlink security modes. |
| sdls_auth_fail / sdls_last_sn | count / — | Rejected uplink frames; last accepted sequence number. |
| sdls_tm_epoch / sdls_tm_state | — | Downlink encryption epoch and state. |
| cfdp_state / cfdp_seq / cfdp_progress | — / — / % | File transfer state (idle, sending recorder, relaying EGSE file), transaction, progress. |
| seq_state / seq_count / seq_next_dt_s | — / count / s | Stored sequence state (IDLE/ARMED/COMPLETE/STOPPED_BY_FDIR), pending entries, time to next. |
| mag_bist | bits | RM3100 self-test: X/Y/Z OK and tested, per unit. |
| magcal_state | bits | Onboard calibration active per magnetometer. |
| beacon_level | — | NOMINAL, INTERESTING, IMPORTANT, URGENT. |

### RF packet (49 octets, all 24 fields)
Radio model, air rate, TX power code, channel, configuration OK, framing, frames sent, idle frames sent, total airtime,
TX duty cycle (limits 55/70 %), frame period, radio timeouts, uplink RSSI / SNR / frequency error measured by the spacecraft,
spacecraft noise floor, CLTUs OK/bad, BCH corrections, bytes received, master frame count, last airtime, LBT busy
deferrals, flags (boosted gain verified, LBT on).

Also: beacon history (last 50 changes), stored-sequence state, MAG BIST, MAGCAL state, CFDP state, anomalies (last 20
statistical outliers), decoded fault/sensor names.

## 7. SPECTROMETER tab (AS7265X)
| Metric | Description |
|---|---|
| Spectrum ± 1σ (linear/log) | 18 channels 410–940 nm, µW/cm², with uncertainty. |
| Raw counts | Per channel with full scale and saturation markers. |
| Normalised spectrum / per-channel SNR | Shape and quality of each channel. |
| Reflectance vs white reference | Target reflectance with compatibility check and uncertainty. |
| Indices ± σ | NDVI, GNDVI, NDRE, WBI proxy, PRI proxy with formula, bands and basis. |
| Radiometry | Total/visible/NIR irradiance (W/m²), PPFD, illuminance (lux); "approximate (18-band)". |
| Colorimetry | CIE XYZ, xy (± σ), u′v′, CCT and Duv, McCamy cross-check; CIE 1931 diagram with Planckian locus. |
| Features | Peak wavelength/value, centroid, visible and NIR sums, NIR/VIS ratio. |
| Classification | Ground vs onboard light-source class, confidence, features, reasons, agreement. |
| Fluorescence (FLUOR) | Excitation, F680/F730, blue-green index, emission total and peak. |
| Library match | Best and ranked matches against saved spectra. |
| Waterfall | Last 300 spectra. |
| Burst | Per-channel series, FFT (flicker), transients, achieved vs reported rate, compression ratio (CCSDS 121.0). |
| Sky map | Polar bins of lux and PPFD by boresight direction. |
| Instrument state | Mode, gain, integration, LED currents, frames merged, die temperatures and drift, boresight zenith/azimuth, change angle. |
| Controls | White reference, library, builders offering only safe LED currents (white ≤ 100 mA, IR ≤ 50 mA, UV 12.5 mA). |
| Event log | Onboard spectral EVRs and ground events. |

## 8. SCIENCE tab
Magnetometers (outboard/inboard RM3100 and body BMM150 X/Y/Z and RMS in µT, |B|, dual-sensor separation, RM3100 cycle
count, flags PA keyed / lamp lit / calibration active); attitude (fusion vs TRIAD quaternion, Euler ZYX, fusion accuracy
mrad, accelerometer mg, gyro °/s, flags TRIAD source / BMM150 scale); environment (pressure hPa, temperature °C, humidity
1 %, IAQ, static IAQ, CO2-equivalent ppm, bVOC ppm, gas resistance Ω, BSEC accuracy 0–3).

## 9. MAGCAL tab (per magnetometer)
Start/stop/reset collection; live coverage (26 direction bins, samples, centre, rejected, duplicates); fit (hard-iron
offset, soft-iron matrix, residual RMS µT, coverage, condition number, axis ratio, refusal reasons); uplink (two-step);
clear (identity); onboard state.

## 10. SEQUENCES tab
Editor; check result (entries: slot, line, time, mnemonic, arguments, encoded command; or first error with line number);
uplink (two-step); START/STOP/CLEAR/DELETE; onboard state, count, time to next; SCLK estimate.

## 11. FILES tab
CFDP transactions (state, progress bar, missing ranges, condition, counters: PDUs, malformed, foreign, unsupported, late);
request/cancel buttons; received-file downloads; TDM (enabled, auto-write, records, dropped, AOS time, last file, file list,
"write TDM now"); XTCE dictionary download.

## 12. COMMANDING tab
Command entry with dictionary-driven help and argument hints; pending confirmations (CONFIRM; AUTH for hazardous) with
expiry; history; console; station I/O (CardKB connected at 0x5F on the header pins, RCU present, firmware, live typed line,
last status line, beacon annunciations sent).

## 13. Health and logs
GDS processing-stage error counters and last error; onboard EVRs (last 40, severity); ground events (last 80); link-manager
state.

## 14. Alarm logic
| Level | Raised by |
|---|---|
| RED | Any RED limit; LOS after a pass (not while hibernating); beacon URGENT; frequency offset ALARM; ground radio fault; E22 serial link down > 30 s; encryption epoch regressed; radio backend unavailable |
| YELLOW | Any YELLOW limit; fault-protection flags set; beacon IMPORTANT; brownout reset; recent SDLS rejects or suspected mismatch; frequency offset WARN; radio model mismatch; AFC fault; key file missing; uplink blocked; new processing errors |

Machine-readable values are also exported at /metrics (Prometheus format).
