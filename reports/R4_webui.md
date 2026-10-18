# R4 - Web UI legitimacy audit (dashboard.html, docs/13, /metrics, JSON API)

Branch `claude/unoq-ground-station` (head 13a60c5, working tree clean before and after this audit).
Scope: requirement 7 ("every metric displayed on the web UI must be legitimate and working"),
with requirements 1 (VENTUNO Q eliminated, UNO Q 4 GB ground station, E22 on an E15-USB-T2 via a
USB-C hub), 4 (BHI260AP 6-DOF IMU in the transmitter, receiver and web UI), 6 (ultra long range)
and 8 (no "Voyager") as they touch the UI. Nothing in `/home/user/Benchmarks` was modified.

---

## 0. Method, evidence and citation keys

**What was read in full:** `dashboard.html` (2168 lines), `web.py`, `rxstats.py`, `link.py`,
`linkmgr.py`, `framesync.py`, the snapshot/ingest/alarm parts of `gds.py`, the relevant parts of
`sim.py`, `radioctl.py`, `radio/e22.py`, `dictionary.py`, `nicla_sense_me_aru.ino`, the ATT/ENV
builders of `flight.cpp`, and docs/13 sections 1-4, 9, 13.7-18 (the rest of docs/13 by heading).

**What was run (read-only on the repo):**
1. `PYTHONDONTWRITEBYTECODE=1 /root/.local/bin/pytest -q -p no:cacheprovider tests/test_web.py`
   -> **26 passed in 16.43 s**.
2. A scratch copy of the `tests/test_web.py::sim_station` recipe
   (`scratchpad/r4work/dump_snap.py`, `dump_snap_test.py`) produced real `/api/state`,
   `/api/science` and `/metrics` outputs of the repo's `GroundStation` + simulator on a virtual
   clock, for: `e22raw` (shipped `station.toml` unchanged: E22-400T33D, code 2, distance 0),
   `e22` (code 7), `e22test` (code 7 + `MODE TEST`, so MAG/ATT/ENV/SPEC exist) and `sx`
   (SX126x profile 4). Files: `/tmp/claude-0/-home-user/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/r4work/out/<run>/{state.json,science.json,metrics.txt}`.
3. The page script itself was executed in Node.js against those snapshots with the repo's own
   DOM harness (`tests/test_web.py:649-728`, extended to dump every table/text per tab):
   `/tmp/claude-0/-home-user/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/r4work/out/<run>/render.json`;
   the E22 default-path rendering is excerpted in
   `/tmp/claude-0/-home-user/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/r4work/out/e22raw/rx_render.txt`.
   All 11 tabs rendered with no display error in every run. "Renders as" columns below quote
   these renderings.
4. `scratchpad/r4work/snapcost.py`: cost of `ReceiverStats.snapshot()` with full windows.

**Citation keys** (paths relative to `/home/user/Benchmarks` unless absolute):

| Key | File |
|---|---|
| DH | `dssq-groundstation/python/dssq/display/dashboard.html` |
| WEB | `dssq-groundstation/python/dssq/display/web.py` |
| FP | `dssq-groundstation/python/dssq/display/frontpanel.py` |
| GDS | `dssq-groundstation/python/dssq/gds.py` |
| RXS | `dssq-groundstation/python/dssq/rxstats.py` |
| LNK | `dssq-groundstation/python/dssq/link.py` |
| LM | `dssq-groundstation/python/dssq/linkmgr.py` |
| FS | `dssq-groundstation/python/dssq/framesync.py` |
| SIM | `dssq-groundstation/python/dssq/sim.py` |
| RCT | `dssq-groundstation/python/dssq/radioctl.py` |
| E22PY | `dssq-groundstation/python/dssq/radio/e22.py` |
| DICT | `dssq-groundstation/python/dssq/dictionary.py` |
| STOML | `dssq-groundstation/python/config/station.toml` |
| SK | `dssq-groundstation/sketch/sketch.ino` (station MCU sketch) |
| NICLA | `nicla_sense_me_aru/nicla_sense_me_aru.ino` |
| FL | `vgq1-spacecraft/sketch/src/fsw/flight.cpp` |
| D13 / D20 | `docs/13_Web_UI_Reference.md` / `docs/20_Known_Limitations.md` |
| T33D | `scratchpad/ds/b8c54790-E22-400T33D-V2.1_UserManual_CN_v1.05.txt` (extracted text line numbers; manual v1.05) |
| T37S | `scratchpad/ds/c2d8b245-E22-xxxT37S_UserManual_EN_v1.5.txt` |
| BHI | `scratchpad/ds/3f577a50-bst-bhi260ap-ds000.txt` (BST-BHI260AP-DS000) |
| NME | `scratchpad/ds/414d9b73-nicla_me_datasheet.txt` |
| UNOQ | `scratchpad/ds/4c60b196-ABX00162-datasheet.txt` (ABX00162 / ABX00173) |
| VQ | `scratchpad/ds/ventuno.txt` (ABX00181, comparison only) |
| BHY2 | `/tmp/claude-0/-home-user-Benchmarks/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/refs/Arduino_BHY2/src/sensors/` |
| R1 | sibling report `scratchpad/v4/reports/R1_ground_port.md` |

---

## 1. Executive summary

1. **The UI is honest in most places** - nulls render as "—", estimates carry "est." /
   source badges, received power is shown at the readings' resolution (D-70), SX126x-only
   fields are empty on the E22. But on the **default hardware path (E22-400T33D on an
   E15-USB-T2, `[gds] rcu = false`)** several displayed numbers are not what they claim to be.
2. **Overload readings are shown as measurements (bench-proven).** The E22 RSSI octet at the end
   of its range reads -256 dBm / -1 dBm; it enters every statistic, chart, histogram, margin and
   the "SNR est." (71 / -184 dB on the bench) - D20 V-70 (line 52) and L-45 (line 206, "Open").
   Only TDM rejects it, and that rejection is then mislabelled "station clock" in GDS health.
3. **E22 path: Reed-Solomon / ASM / SER / BER / CER cannot measure the radio channel.** The E22
   module delivers a LoRa packet only when its own CRC passed (D20 L-20 line 172; docs/11 line 41),
   so on hardware these counters are ~0 forever and "Pre-FEC BER est." / "SER" imply a perfect
   channel. They look alive only because the simulator injects 0..`max_errors` octet errors into
   E22 CADUs (SIM:641-643), which also feeds the docs/13 examples and screenshots.
4. **E22 "CRC ok" is a constant.** `GDS:645` calls `on_reception(ert, rssi)` without `crc_ok`, so
   `RXS:312` defaults it to `True`; the RECEIVER "last packet: CRC ok" (DH:1244) and the metric
   `dssq_rf_packet_crc_ok 1` are never measured on the E22.
5. **Frame loss across passes is counted mod 256.** At AOS the MCFC jump (frames sent while out
   of view, or a spacecraft reboot) is added to `rx.frames.lost` and per-VC `lost`
   (RXS:377-388), modulo 256 (`ccsds/tm.py:252`). The header FER and FRAMES "lost"
   (DH:1099-1100), the OVERVIEW Frames tile (DH:1170) and `dssq_frames_fer` show this lifetime
   figure. Only `fer_10m` excludes the jump (RXS:381).
6. **Station I/O "Radio Control Unit: present" is hard-coded** by the station sketch hello
   `H|<fw>|1|<kb>` (SK:1466; parsed FP:211-212; shown DH:2014; documented as real at D13:2873).
   Same finding as R1 §5.3. The panel title still names the VENTUNO Q (DH:342).
7. **Ultra-long-range blind spot (needs one bench test, VERIFY).** Every margin, path-loss,
   excess-loss, range and link-manager figure is built on the packet RSSI
   (RXS:559, 634, 643; LM:76). If the E22 RSSI octet is the total in-band power (as the SX126x
   `RssiPkt` the station sketch uses, SK:972), it cannot fall below the noise floor; LoRa decodes
   below the noise, so near the sensitivity limit the UI would show a margin of about
   noise - sensitivity (about +10 dB with a floor near -118 dBm, the value the simulator models and D13:662 quotes, and the -128 dBm datasheet sensitivity)
   in **green** (DH:1169, 1283) while frames are being lost, and the link manager would advise a
   *faster* rate. Neither E22 manual says which RSSI the octet is (T33D:782-784, T37S:914-919).
8. **Long-range timing breaks several statistics.** `frames_per_min`, `info_rate_bps` and
   `cadu_rate_bps` use a fixed 60 s window (RXS:576-612); the link manager needs >= 10 RSSI
   samples in 10 min (LM:72-74) which the SX126x profiles 0 and 1 (frame periods 34.6-138.5 s,
   D13:192-193) never reach in SAFE (and profile 0 in no mode), so it reports "insufficient data"
   forever at exactly the most sensitive settings.
9. **"Not configured" shown as a number:** OWLT renders "0.000 µs" (GDS:461, DH:1475) and
   "Range at sensitivity" shows a pure free-space 26 515 km in the *measured* column when
   `distance_km = 0` (the shipped default, STOML:32; RXS:641-644; DH:1284).
10. **With `rcu = false` (the shipped default, STOML:169) the module's registers are never read
    back**, so the RSSI-byte and noise-enable assumptions behind every RSSI/noise value are
    unverified, and the UI says nothing about it (the read-back section simply disappears,
    DH:1361-1365); the link manager keeps advising LINKRATE although nothing can execute it
    (GDS:1298-1303, DH:1454).
11. **/metrics** exports the last RSSI / margin as current gauges after LOS (WEB:359-362), exports
    config/never-measured values on the E22 (`dssq_rf_doppler_offset_cal_hz`,
    `dssq_rf_doppler_calibrated`, `dssq_rf_packet_crc_ok`), and omits `sync`, `rx.serial`,
    `rx.radio_link`, `linkmgr`, window percentiles and `station_io`.
12. **BHI260AP / Nicla:** the Nicla firmware sets the gyro-valid and temperature-valid flags
    unconditionally (NICLA:185, 209), so "Rate 0.0 0.0 0.0 °/s" or "0.00 °C" can be displayed as
    measurements when those virtual sensors never produced data. The BMM150 values are BSX
    "corrected" (SENSOR_ID_MAG = 22, BHY2 `SensorID.h:22`) but the UI/dictionary/docs call the
    MAG packet "raw" (DICT:277, D13:2456-2457). Integrating 6-DOF (Game Rotation Vector, ID 37)
    requires an accuracy rule: the BHI260AP reports its accuracy as 0 (BHI:9118).
13. **"Voyager" does not occur** in `dashboard.html`, `web.py`, `frontpanel.py`, `gds.py` or docs/13
    (grep). It occurs elsewhere (FL `autonomy.h`, docs 01/10/12/20) - other reports.
14. docs/13 contradicts the code in at least 12 places (section 8).

The prioritised fix list is in section 9 (7 P0, 10 P1, 7 P2, plus P3 cosmetics).

---

## 2. Classification legend and how "no data" renders

| Class | Meaning |
|---|---|
| **C1** | real measurement on the default E22 path (radio octet, decoder/counter, spacecraft telemetry field, station clock) |
| **C2** | derived / estimated, with its basis stated on the page (formula, "est.", source badge, label) |
| **C3** | only meaningful on the SX126x path (null / empty on the E22) |
| **C4** | configuration value (station.toml, model constant, datasheet constant) |
| **C5** | never populated / always empty or always 0 in the default configuration |
| **C6** | placeholder, hard-coded, simulated-only or misleading as displayed |

A row can carry two classes (e.g. "C1, C6 at overload"). "E22 HW" = what real E22 hardware
produces, which can differ from the simulator.

**Rendering helpers** (DH:387-411): `nil()` treats null, undefined, NaN and ±inf as missing; `fx`,
`fu`, `fsig`, `fsci`, `fint`, `iso`, `dur`, `owlt`, `fres` return **"—"** (`NA`, DH:364) for a
missing number; `fint(0)` prints **"0"**; `fyn(null)` prints "—"; the statistics table prints
`n` as "0" and the other columns "—" for an empty window (DH:1258); empty charts print "no
frames yet" / "no data" / "no samples" (DH:650-655, 1155, 1306); `td()` prints "—" for
null cells (DH:476-479); a missing packet gives a one-row table "no <PKT> packet yet"
(DH:1525, 1827, 1844, 1851). JSON NaN/inf are converted to null server-side (`json_safe`,
WEB:571-573). Counters therefore never show "—": a counter that does not exist for the fitted
radio shows "0" (see C5 items).

---

## 3. Hardware facts the classification depends on

| Fact | Source |
|---|---|
| E22 appends one RSSI octet per received packet when REG3 b7 is set; "the current data packet RSSI is dBm = -(256 - RSSI)"; which RSSI (total in-band or despread signal) is **not stated** | T33D:782-784; T37S:914-919 |
| E22 ambient-noise register (REG1 b5): query `C0 C1 C2 C3 00 02`, reply `C1 00 02 <noise> <last RSSI>`, "can be sent in the transmission mode or the WOR transmission mode"; behaviour with b5 = 0 not stated (not found) | T33D:737-756; T37S:866-885 |
| E22 drops LoRa-CRC failures inside the module | project statement D20:172 (L-20), docs/11:41; **not found** in the T33D / T37S manuals |
| E22-400T33D sensitivity at 2.4 kbit/s: min -126 / typ -128 / max -130 dBm | T33D:222 (manual §2.2) |
| E22-400T37S sensitivity at 2.4 kbit/s: -125 / -126 / -127 dBm; blocking power 10 dBm | T37S:259, :221 |
| The GDS uses the *typical* value only: T33D -128, T37S -126 | LNK:68 |
| E22 internal LoRa parameters (SF, BW) are not published; the GDS assumes a 125 kHz noise bandwidth | LNK:17-21; STOML:76 |
| Overload on the bench: RSSI octet alternated 0xFF (-1 dBm) / 0x00 (-256 dBm), SNRest 71 / -184 dB | D20:52 (V-70), D20:206 (L-45) |
| Shipped ground configuration: `[radio] model = "E22-400T33D"`, `air_rate = 2`, `rssi_byte = true`, `rssi_noise = true`, `distance_km = 0.0`, `[gds] rcu = false` (E15-USB-T2 jumpers hold M0/M1) | STOML:32, 61, 70, 169-171 |
| With `rcu = false` no `RadioManager` exists (no VERIFY read-back, GSCAN, WAKE, LINKRATE, LINKCHAN) | GDS:2396-2405; GDS:1298-1303 |
| SX126x station path reports `RssiPkt/2` (packet RSSI) | SK:972 |
| BHI260AP Game Rotation Vector = 6DoF (accel + gyro); ID 37; "For Game Rotation Vector the Estimated Accuracy in Radians is reported as 0" | BHI:3430, 8495, 9118 |
| BHI260AP sensor status (Meta Event "Sensor Status"): 0 Unreliable, 1 Accuracy Low, 2 Medium, 3 High (Table 100) | BHI:9719-9734 |
| Gravity (ID 28) and Linear Acceleration (ID 31) virtual sensors exist | BHI:8592; BHY2 `SensorID.h:26, 28` |
| Arduino_BHY2 IDs used by the Nicla firmware: ACC 4 "corrected", GYRO 13 "corrected", MAG 22 "corrected", RV 34 | BHY2 `SensorID.h:10, 16, 22, 30` |
| `SensorClass::dataAvailable()` exists in Arduino_BHY2 | BHY2 `SensorClass.h:47` |
| Nicla Sense ME: BHI260AP with 6-axis 16-bit accel + gyro; BMP390 300-1250 hPa, ±0.5 hPa abs. | NME:105-136, 408 |
| UNO Q: QRB2210, 4 × Cortex-A53 @ 2.0 GHz; eMMC 16/32 GB; LPDDR4X 2/4 GB (the VENTUNO Q has a QCS8275 octa-core, 16 GB, 64 GB eMMC) | UNOQ:19-20, 453, 473-474; VQ:202-236, 690 |

---

## 4. Tab-by-tab, panel-by-panel audit

Columns: **Shown as** | **snapshot key** | **producer** | **class** | **E22 default path: renders as** | **notes / fix id** (section 9).

### 4.0 Header bar, beacon banner, pending strip (DH:117-152, renderHeader DH:1080-1119)

| Shown as | Key | Producer | Class | E22 renders as | Notes |
|---|---|---|---|---|---|
| DSS-Q1 GROUND STATION | `station` | GDS:2179 `[station] name` | C4 | "DSS-Q1 GROUND STATION" | - |
| S/C | `spacecraft` | GDS:2179 | C4 | "VGQ-1" | - |
| AOS / LOS pill | `rx.state.los`, `.hibernating` | RXS:362-373, 432-440 | C1 | "AOS" green / "LOS" red | real |
| SYNC pill | `sync.state` | FS:59-191; GDS:2205 (PACKET for SX) | C1 | "LOCK"; between passes "SEARCH" in **red** | red at an expected LOS is alarm noise (F-P2-6) |
| ALARM pill | `alarm`, `alarm_reasons` | GDS:1977-2047 | C2 | "ALARM RED" + tooltip | rules real; inherits F-P0-1 (no overload alarm) |
| MODE | `decoded.mode` | GDS:2113 (HK fsw_mode) | C1 | "SAFE" | - |
| UTC | `utc` | GDS:2178 station clock | C1 | ISO time | - |
| RSSI | `rx.rf.rssi_dbm` | FS:128 -> GDS:645 -> RXS:314-317, 550 | C1; **C6 at overload** | "-96 dBm"; "—" at LOS | F-P0-1; floor-limited at range F-P0-6 |
| SNR | `rx.rf.packet.snr_db` else `rx.rf.snr_est_db` | RXS:546, 556 | E22: C2 ("dB est."); SX: C3 measured | "22 dB est." | E22 C6 at overload (bench 71/-184 dB) |
| MARGIN | `rx.rf.margin_est_db` | RXS:559 (RSSI - typical sensitivity) | C2 | "32 dB" | F-P0-6, F-P1-3 |
| FER | `rx.frames.fer` | RXS:604 lifetime | **C6** | "0" (fsci(0)) | includes AOS jumps mod 256: F-P0-4 |
| FRAMES | `rx.frames.ok` / `.lost` | RXS:377-389 | C1 / **C6** (lost) | "18 / lost 0" | F-P0-4 |
| SCLK | `time.partition`, `time.sclk_estimate_s` | GDS:2214-2221 | C2 | "1/55" | - |
| COP-1 | `cop.*` | GDS:2222-2224 (fop.state) | C1 | "V(S)=1 N(R)=1 OK" | - |
| SDLS TM | `link.sdls.spacecraft_tm_state`, `tm_service` | GDS:2160-2166 | C1 | "PROTECTED (AE)" | - |
| FRAMING | `link.tm_framing_seen` | GDS:717-718 | C1 | "TM TM 18 / USLP 0" | - |
| GDS version/mode, LINK DOWN | `version`, `mode` + browser timer | DH:578-585 | C1 | "GDS 3.0.0 host" | - |
| Beacon banner | `beacon.*` | GDS:896-920 | C1 | hidden at level 0 | - |
| Pending strip | `pending_confirm` | GDS:2237-2240 | C1 | buttons | - |

### 4.1 OVERVIEW (DH:156-166, renderOverview DH:1160-1200)

| Shown as | Key | Producer | Class | E22 renders as | Notes |
|---|---|---|---|---|---|
| Tile Signal | `rx.state.*` | RXS:581-585 | C1 | "AOS · pass 1m 27s" | - |
| Tile RSSI "dBm, newest frame" | `rx.rf.rssi_dbm` | as header | C1 / C6 overload | "-96" | docs say "averaged" (D13:485) - wrong |
| Tile SNR est. | `rx.rf.snr_est_db` (+ `packet.snr_db`) | RXS:546 | C2 (SX: est. shown big, measured small) | "22" | SX: measured should be primary (F-P2-4) |
| Tile Margin est. | `rx.rf.margin_est_db`, `sensitivity_dbm/source` | RXS:536, 559 | C2 | "32 · dB vs -128.0 dBm (datasheet)" green | F-P0-6 (green at the noise floor), F-P1-3 (typical, not worst-case) |
| Tile Frames | `rx.frames.ok/lost/fer` | RXS:603-605 | C1 / **C6** | "18 · lost 0 · FER 0" | F-P0-4 |
| Tile Throughput | `rx.frames.info_rate_bps`, `frames_per_min` | RXS:576-612 | C2 | "150.4 bit/s info · 12.00 frames/min" | 60 s window: F-P1-2 |
| Tile RS decoder | `rx.decoder.symbols_corrected`, `uncorrectable` | RXS:333-360 | **C6 on E22 HW** (always ~0; sim-only non-zero) | "32 sym corrected" (simulated) | F-P0-3 |
| Tile S/C mode | `decoded.mode/beacon/fdir` | GDS:2113-2126 | C1 | "SAFE · beacon URGENT · FDIR 0" | - |
| Tile Bus temperature | `tlm.HK.values.bus_temp` | FL:1634 (Nicla temp) | C1 (C6 if the Nicla temp virtual sensor never ran: NICLA:209) | "31.99 °C" | F-P1-9 |
| Tile SDLS TM | `link.sdls.*` | GDS:2160 | C1 | "PROTECTED · frames ok 11 · fail 0" | - |
| Tile Uplink | `cop.outstanding/queued/vs` | GDS:2222 | C1 | "0 out / 0 q · V(S) 1 · FOP OK" | - |
| Tile CFDP onboard | `cfdp.onboard.*` | GDS:2226-2227 | C1 | "IDLE · seq 0 · 0.0 %" | seq 0 means "none" (DICT HK `cfdp_seq`): F-P2-5 |
| Chart Received power | `rx.series.rssi`, `.noise` | RXS:396-401 | C1 | line chart; overload points plotted at -256 | F-P0-1; noise line repeats the latest reading of any age |
| Chart Signal-to-noise | `rx.series.snr_est/snr_meas/margin` | RXS:402-405 | C2 / C3 / C2 | snr_est pairs with a noise reading of **any age** (RXS:402) | F-P1-5 |
| Spacecraft summary (11 rows) | `decoded.*`, `tlm.HK.values.*`, `tlm.<pkt>.ert` | GDS:2102-2132 | C1 | e.g. "Last HK / RF / MAG / SPEC: 21.2 s ago · 21.2 s ago · — · —" | real |
| Alarm reasons | `alarm_reasons`, `alarm_red_count` | GDS:1977-2047 | C2 | list | - |
| Latest events | `events` | GDS event log | C1 | list | - |
| Command status | `cop.recent` | fop | C1 | table | - |

### 4.2 RECEIVER (DH:168-197, renderReceiver DH:1224-1457)

#### 4.2.1 "RF now" (`rx.rf`, RF_KEYS DH:1206-1220; producer RXS:547-571)

| Shown as | Key | Class | E22 renders as | Notes |
|---|---|---|---|---|
| Station radio model | `model` | C4 (or follows RF `radio_model` when "auto", GDS:947-949) | "E22-400T33D" | - |
| Frequency (MHz, Hz) | `freq_mhz`, `freq_hz` | C4 (LNK:185-186) | "433.125000 MHz", "433125000.0 Hz" | - |
| Air-rate code / Air rate / Air data rate | `air_rate_code`, `rate_label`, `air_rate_bps` | C4 (LNK:300-309) | "2", "2.4k", "2400.00 bit/s" | E22 nominal rate; LoRa params unpublished |
| Packet RSSI, last (calibrated / raw) | `rssi_dbm`, `rssi_raw_dbm` | C1; **C6 at overload** | "-96 dBm (resolution 1.0 dB)" | F-P0-1; F-P0-6 |
| RSSI resolution | `rssi_resolution_db` | C4 (RXS:61-62, 551) | "1.00 dB" | - |
| RSSI calibration offset (T-RF-02) | `rssi_cal_offset_db` | C4 | "0.0000 dB" | uncalibrated (D20 V-18) |
| Received power, linear | `rssi_mw` | C2 | "0.25119 pW" | 5 significant digits from a 1 dB reading: false precision (F-P2-3) |
| Noise floor (calibrated) | `noise_dbm` | C1 (E22 noise register via FS:106-114, GDS:641; polled GDS:1163-1175) | "-118 dBm" | needs REG1 b5 set in the module: unverifiable with rcu=false (F-P1-4) |
| Noise source / age | `noise_source`, `noise_age_s` | C4 / C1 | "E22 noise register" / "0.5 s" | - |
| SNR est. (RSSI − noise) | `snr_est_db` | C2 | "22 dB" | same-pass rule here (RXS:540-546) but not in windows/series (F-P1-5); C6 at overload |
| Signal power est. (noise removed) | `pr_est_dbm` | C2 | "-96 dBm" | the better input for margin at long range (F-P0-6) |
| N₀ est. / Noise bandwidth / basis | `n0_est_dbm_hz`, `noise_bw_hz`, `noise_bw_source` | C2 with **assumed** B (STOML:76) / C4 | "-169 dBm/Hz", "125000.0 Hz", "assumed" | E22 bandwidth unknown (LNK:17-21) |
| C/N₀ est. / basis | `cn0_est_dbhz`, `cn0_source` | C2 (assumed B) | "73 dB-Hz" | - |
| Eᵦ/N₀ est. | `ebn0_est_db` | C2 (assumed B, nominal Rb) | "39 dB" | - |
| Sensitivity / source | `sensitivity_dbm`, `sensitivity_source` | C4 datasheet **typical**; "estimated" above 2.4k (LNK:393-413) | "-128.0000 dBm datasheet" | 4 decimals on a typ. integer; worst case -126 not shown (F-P1-3) |
| Link margin est. (RSSI − sensitivity) | `margin_est_db` | C2 | "32 dB" | F-P0-6 |
| Required SNR, SNR margin | `required_snr_db`, `snr_margin_db` | C3 | "—", "—" | F-P2-1 (hide on E22) |
| FERR_L, \|freq err\| median, state | `freq_offset_limit_hz`, `freq_offset_abs_median_hz`, `freq_offset_state` | C3 | "—" ×3 | F-P2-1 |
| CRC-failed packets, PER (CRC) | `crc_fail_packets`, `packet_error_rate` | C3 (RXS:563-564) | "—", "—" | F-P2-1 |
| last packet: RSSI | `packet.rssi_dbm` | C1 | "-96 dBm" | duplicate of RSSI |
| last packet: SNR (SnrPkt/4) | `packet.snr_db` | C3 | "—" | - |
| last packet: Frequency error (raw) | `packet.freq_err_hz` | C3 | "—" | - |
| last packet: **CRC** | `packet.crc_ok` | **C6 on E22** (constant True: GDS:645 -> RXS:312 default) | "ok" | F-P0-2 |
| last packet: Time | `packet.t` | C1 | ISO | - |
| SNR window 10 min | `snr_db` (`brief`) | C3 | "—" | - |
| Frequency error window 10 min | `freq_err_hz` | C3 | "—" | - |
| (generic extra rows) | any unknown scalar of `rx.rf` | - | none today | DH:1241 prints unknown keys raw (drift safety) |

#### 4.2.2 Receiver statistics (`rx.windows.<w>.<m>`, STAT_METRICS DH:1221-1223; RXS:91-159, 588)

| Metric | Class (E22) | E22 renders as (code 2 run) | Notes |
|---|---|---|---|
| Packet RSSI (dBm) | C1; C6 at overload | n 12/18/18, mean -96/-95, SEM "—" (D-70) | -256 drags means/min (L-45) |
| Noise floor (dBm) | C1 | n 3/5/5, -118 | - |
| SNR est. (RSSI − noise) | C2 | n 12/17/17, 22/23 | pairs with any-age noise (RXS:319) |
| SNR measured (SX126x) | C3 | n "0", rest "—" | F-P2-1 |
| Frequency error (Hz) | C3 | n "0", rest "—" | F-P2-1 |
| Frame inter-arrival (s) | C1 | 5.1053 ± 0.0021 (CRUISE/SAFE period) | real |
| RS symbols corrected / codeblock | **C6 on E22 HW** | mean 1.78 (simulated errors) | always 0 on hardware (F-P0-3) |
| ASM bit errors / frame | **C6 on E22 HW** (only UART corruption) | 0.0000 | F-P0-3 |

Window horizons are fixed (60 s, 600 s, pass; RXS:54): with SX126x profile 0/1 frame periods of
34.6-138.5 s (D13:192-193) the "1 min" column holds 0-1 sample (F-P1-2).

#### 4.2.3 Link budget (`rx.budget`, RXS:624-679; DH:1264-1291)

| Row | Design key / measured key | Class | E22 renders as (distance 0, default) | Notes |
|---|---|---|---|---|
| S/C transmitter power (model) | `sc_tx_power_dbm_nominal` | C4 nominal from RF `tx_power_code` (LNK:312-320) | "33.00 … VERIFY T-RF-07" | `power_verified` only for T37S (RXS:661, LNK:488) - docs claim T33D code 0 is not VERIFY (D13:228) |
| − S/C line loss / + S/C antenna gain | `sc_line_loss_db`, `sc_antenna_gain_dbi` | C4 | "0.50", "2.15" | - |
| = EIRP | `sc_eirp_dbm_est` | C2 | "34.65" | - |
| Distance / OWLT | `distance_km`, `owlt_s` | C4 | "— km", "—" | correct here; OWLT elsewhere shows 0.000 µs (F-P0-7) |
| − Free-space path loss / measured (last) | `fspl_db` / `measured_path_loss_db_est` | C4-computed / C2 | "—" / "142 (last)" | measured PL = EIRP+Gr−Lr−RSSI: floor-limited at range (F-P0-6) |
| path loss 10-min mean | `measured_path_loss_mean10m_db` | C2 | "141" | - |
| + Ground gain / − line loss | `ground_gain_dbi`, `ground_line_loss_db` | C4 | "12.00", "1.00" | - |
| = Received power | `predicted_pr_dbm` / 10-min RSSI mean | C4-computed / C1 | "—" / "-95 (n=18)" | - |
| Excess loss | `excess_loss_db_est` | C2 | "—" | needs distance |
| Thermal noise kTB | `thermal_noise_dbm` / 10-min noise | C4-computed (assumed 125 kHz on E22) / C1 | "-123.0309" / "-118 (n=5)" | label should say "assumed B" on E22 |
| SNR | `predicted_snr_db` / measured or est. | C2 (design uses *current* noise, RXS:668) / C2 | "—" / "23 (n=17) est." | - |
| Sensitivity | `sensitivity_dbm` | C4 typical | "-128.0000 datasheet" | F-P1-3 |
| = Link margin | `predicted_margin_db` / `rx.rf.margin_est_db` | C2 | "—" / "32" green | F-P0-6 |
| Range at sensitivity | `range_at_sensitivity_km_est` | **C6** when distance is 0: pure free space, shown in the **measured** column | "26515.364 km" | F-P1-7 |
| Uplink: station TX power / EIRP | `uplink.gs_power_dbm`, `gs_eirp_dbm_est` | C4 / C2 | "33.00 VERIFY", "44.00" | USB-powered E15 may not reach 33 dBm (D20 V-69): label |
| Uplink: S/C sensitivity | `uplink.sc_sensitivity_dbm/source` | C4 | "-128.0000 datasheet" | - |
| Uplink: received power at S/C | `uplink.predicted_pr_dbm` / `measured_uplink_rssi_dbm` | C4 / C1 (spacecraft E22 RSSI octet) | "—" / "-96 (RF uplink_rssi)" | same RSSI caveats |
| Uplink margin | `uplink.predicted_margin_db` / `measured_margin_db` | C2 | "—" / "32" | - |

#### 4.2.4 Charts, gauge, histograms (DH:1293-1318, 909-932)

| Panel | Key | Class (E22) | E22 renders as | Notes |
|---|---|---|---|---|
| Packet RSSI and noise floor | `rx.series.rssi/noise/t` | C1 | chart | overload points |
| SNR and margin | `rx.series.snr_est/snr_meas/margin` | C2/C3/C2 | chart | F-P1-5 |
| Frequency error chart | `rx.series.freq_err` | C3 | "no frequency-error reports (SX126x only)" | F-P2-1 |
| Frequency-error gauge + note | `rx.windows.10m.freq_err`, `rx.rf.freq_offset_limit_hz` | C3 | "no frequency-offset limit (E22 radio: no frequency-error report)" / "the E22 UART firmware reports no frequency error" | F-P2-1 |
| RSSI distribution | `rx.hist.rssi` (-150..-20, 5 dB; RXS:199) | C1 | bars | -256 and -1 dBm are clamped into the *weakest* and *strongest* bins (RXS:170) - an overload looks like a very weak signal (F-P0-1) |
| SNR distribution | `rx.hist.snr` (measured, else est.; RXS:323) | C2 on E22 | bars | title should say "SNR est." on E22 (F-P2-3) |
| Frequency-error distribution | `rx.hist.freq_err` | C3 | "no samples" | F-P2-1 |
| RS corrections per codeblock | `rx.hist.rs_corrections` | **C6 on E22 HW** | bars from simulated errors | F-P0-3 |
| ASM bit errors per frame | `rx.hist.asm_bit_errors` | **C6 on E22 HW** | all in bin 0 | F-P0-3 |
| RF survey | `radio.last_scan` (GSCAN), `tlm.RFSCAN.values` | C1 / C1 | "no ground survey (GSCAN) · no spacecraft survey (RFSCAN)" | GSCAN needs the RCU: with `rcu = false` never available (GDS:1298-1303) - say so (F-P1-4) |

#### 4.2.5 Radio configuration (`radio`, GDS:2185-2201; DH:1331-1366)

| Rows | Key | Class | E22 renders as | Notes |
|---|---|---|---|---|
| Model, Model id, Interface, Packet mode, Channel, Frequency, Rate code, Rate, Bit rate | `radio.*` from LNK:473-497 | C4 | "E22-400T33D", "1", "UART", "no", "23", "433.1250", "2", "2.4k", "2400" | - |
| SF, BW, CR, LDRO, Rx boosted gain, CAD before TX, FERR_L | `sf`, `bw_khz`, `cr`, `ldro`, `rx_boost`, `cad`, `freq_offset_limit_hz` | C3 | "—" | F-P2-1 |
| Power code / TX power | `power_code`, `power_dbm` (+ `power_verified`) | C4 | "0", "33 VERIFY T-RF-07" | - |
| LBT | `lbt` | C4 | "no" | - |
| Sensitivity | `sensitivity_dbm/source` | C4 typ | "-128 datasheet" | F-P1-3 |
| Air time of one CADU | `airtime_s/source` | C2 estimated (LNK:323-330) | "1.0227 estimated" | - |
| Frequency offset calibration | `freq_offset_cal_hz` | C4 (SX-only use) | "0" | F-P2-1 |
| Fault log / Noise register | `has_fault_log`, `has_noise_register` | C4 model capability (LNK:155-162) | "no", "yes" | "Noise register yes" is a capability, not proof the module is configured (F-P1-4) |
| Model seen / mismatch | `model_seen`, `model_mismatch` | C1 / C2 (GDS:941-958) | "E22-400T33D" | - |
| S/C model / rate code | `sc_model`, `sc_rate_code` | C1 (RF packet) | "E22-400T33D / 2" | - |
| Air time est., Busy time est., Longest frame period est. | `sc_airtime_s`, `sc_busy_s_est`, `frame_period_max_s_est` | C2 | "1.0227 s", "1.2670 s", "5.068 s" | - |
| Backend | `backend_available`, `backend_error` | C1 | "available" | = serial port open, not module alive (F-P1-4) |
| Radio Control Unit | `radio.rcu` (GDS:2188) | C1 | sim "active"; **hardware with rcu=false: "off"** | - |
| Configured (station.toml), Allowed channels | `configured`, `allowed_channels` | C4 | "CH23 code 2 pwr 0 LBT no", "20 21 22 23 24" | - |
| Coordinated change, Radio jobs pending, Ground fault | `link_change`, `jobs_pending`, `ground_fault` | C1 | "none", "0", "none" | ground fault is T37S-only: C5 on T33D |
| Ground radio read-back (section) | `ground_config` | C1 when an RCU exists; **C5 with rcu=false** | sim shows 23 registers; hardware: section absent | F-P1-4 |

#### 4.2.6 AFC (`rx.rf.afc`, LNK:842-852; DH:1368-1379) - all **C3**. E22 renders "AFC: not active (E22 radio, or [radio.sx126x] afc = false)". SX126x: real loop state (C2 estimates from C3 measurements).

#### 4.2.7 Doppler and range rate (`rx.rf.doppler`, RXS:465-480; DH:1380-1383)

| Row | Class | E22 renders as | Notes |
|---|---|---|---|
| Doppler shift, Range rate, Source | C3 (C2 on SX: sign convention VERIFY, LNK:288-296) | "—" | - |
| Offset calibration | C4 | "0.0 Hz (not calibrated: VERIFY T-RF frequency calibration)" | a config line for a quantity that cannot exist on the E22 (F-P2-1) |
| Carrier, Relation | C4 / text | "433125000.0 Hz", formula | - |

#### 4.2.8 Frame synchronizer (`sync`, FS:73-75; GDS:2205; DH:1385-1393)

| Row | Class (E22) | E22 renders as | Notes |
|---|---|---|---|
| State | C1 | "LOCK" | SX126x "PACKET" |
| ASMs found, CADUs delivered | C1 | "18", "18" | SX126x: "0" forever (C5 on SX, F-P2-1) |
| ASM bit errors (total), Mean per CADU | C1 but **C6 as a channel metric** (E22 HW: only UART errors) | "0", "0.0000" | F-P0-3 |
| Slips, Flywheel frames, Lock losses, Discarded octets | C1 (UART / framing integrity) | "0" | real and useful on the E22 |
| Noise replies parsed | C1 | "5" | - |
| Ground radio fault reports | C1 for T37S; **C5 on T33D** (no fault log, LNK:156-158) | "0" | F-P2-1 |
| Time in lock | C1 (`rx.state.time_in_lock_pct`, RXS:584) **mislabelled**: it is time in AOS since GDS start, not synchronizer lock | "96.90 %" | F-P2-3 |

#### 4.2.9 Decoder (`rx.decoder`, RXS:592-602; DH:1395-1402)

| Row | Class (E22 HW) | E22 renders as (sim) | Notes |
|---|---|---|---|
| Codeblocks | C1 | "18" | - |
| Clean / corrected | C1, **corrected ~0 on HW** | "4 / 14" (simulated) | F-P0-3 |
| Uncorrectable | C1 (~0 on HW: CRC-failed packets never arrive) | "0" | - |
| Symbols corrected / max | **C6 on HW** | "32 / 4 of 16" | F-P0-3 |
| Symbol error rate est. (SER) | **C6 on HW** (0 = "perfect channel") | "7.66e-3" | F-P0-3 |
| Pre-FEC BER est. range | **C6 on HW** | "9.58e-4 … 7.66e-3" | F-P0-3 |
| Codeword error rate (CER) | **C6 on HW** | "0" | F-P0-3 |
| FECF failures after RS | C1 | "0" | - |
| Foreign / bad header | C1 | "0" | - |
| RS rescued / failed | C3 (C5 on E22) | "0 / 0" | shows "0" not "—" on E22 (F-P2-1) |
| Rice errors, Decom errors | C1 | "0", "0" | - |
| SDLS rejected | C1 | "BAD_IV 0 · …" | - |

#### 4.2.10 Frames (`rx.frames`, RXS:603-614; DH:1403-1411)

| Row | Class | E22 renders as | Notes |
|---|---|---|---|
| Frames OK / lost | C1 / **C6** | "18 / 0" | lost includes AOS jumps mod 256 (F-P0-4) |
| FER (since start) | **C6** | "0" | F-P0-4 |
| FER, 10 min (link manager) | C1/C2 (RXS:408-412) | "0" | the honest in-pass figure |
| Idle (OID) frames | C1 + C2 % | "7 (38.89 %)" | - |
| Channel efficiency | C2 | "78.25 %" | - |
| Frames per minute, Information rate (60 s), CADU rate on air (60 s) | C2, **60 s fixed window** | "12.00", "150.40 bit/s", "377.60 bit/s" | F-P1-2; "on air" = CADU bits only, no LoRa overhead (label) |
| Passes / this pass | C1 | "1 / 1m 27s" | - |
| Time in lock | C1 mislabelled | "96.90 %" | F-P2-3 |
| Effective LOS timeout | C2 (GDS:1811-1812) | "90.0 s" | - |
| VC / format table | C1 (`by_format`, `per_vc`) / C6 (`per_vc.lost` includes AOS jumps, RXS:382-388) | "TM 18", "VC 0 6 0" | lost column blank for formats (DH:1410) |

#### 4.2.11 Packets per APID (`apids`, GDS:2207-2211, 796-815; DH:1412-1413)

| Column | Class | Notes |
|---|---|---|
| packet, count, age | C1 | - |
| lost | C1 within a pass; **C6 across passes** (real-time packets generated while out of view count as "lost", GDS:803-807) | F-P0-4 |
| IAT s (meas.) | **C2**: an EWMA with weight 0.2 (GDS:809-811), not a measurement of one interval | label (F-P2-3) |

#### 4.2.12 SX126x station MCU (`radio.sx126x`, DH:1415-1435) - all **C3**; E22 renders "SX126x: not in use (E22 station radio)". On the simulator the `rx_gain_reg` row shows "— boosted 0x96 verified" (the simulator reports `rx_boost_verified` without a register value) - simulator inconsistency (F-P2-7). Panel subtitle names the "station MCU", which on the new branch is the UNO Q STM32U585 (R1).

#### 4.2.13 Serial / radio link (`rx.radio_link`, GDS:2091-2100; `rx.serial`, RXS:615-617; DH:1437-1440)

| Row | Class | E22 renders as | Notes |
|---|---|---|---|
| Connected | C1 = USB-UART port open (E22PY:332-334) | "yes" | not "module alive": an unpowered module behind a working CP2102 still says yes (F-P1-4) |
| Port | C4 | "/dev/ttyUSB0" (sim "—") | through the USB-C hub: VERIFY the name is stable (`/dev/serial/by-id/`, STOML:47) |
| Faults / reopens, TX dropped, Last error | C1 | "0 / 0", "0", "—" | - |
| MCU status age (SX126x) | C3 | "—" | F-P2-1 |
| Serial octets / rate | C1 (C5 on SX: "0 / 0.0 B/s") | "4,291 / 47.9 B/s" | - |
| Noise queries / replies | C1 | "5 / 5" | the only evidence the E22 answers at all with rcu=false (F-P1-4) |

#### 4.2.14 Two-way link (`rx.two_way` = RF packet + estimate, RXS:619; `rx.uplink`; DH:1441-1452)

| Row | Downlink / Uplink class | E22 renders as | Notes |
|---|---|---|---|
| RSSI | C1 / C1 (spacecraft E22 RSSI octet, 1 dB, uncalibrated) | "-96 dBm / -96 dBm" | overload/floor caveats on both |
| SNR, Frequency error | C3 / C3 | "— / —" | F-P2-1 |
| Noise floor | C1 / C1 (RF `sc_noise_floor`) | "-118 / -117 dBm" | - |
| Margin est. | C2 / C2 (RXS:619, 679) | "32 / 32 dB" | F-P0-6 |
| Frames / CLTUs | C1 / C1, **"bad" includes every plain-text station identification** (SIM:685-687 mirrors flight `process_tc`) | "18 frames / 1 ok / 1 bad" | the "1 bad" is the ground's own ID (F-P2-5) |
| Errors corrected | C6 on E22 HW (RS) / C1 (BCH) | "32 RS sym / 0 BCH" | F-P0-3 |
| Transmitted, Duty/airtime, Frame period/last air time, Radio config/LBT/timeouts, Uplink windows | C1 | real values | - |

#### 4.2.15 Link manager and pass events (`linkmgr`, LM:100-103; `rx.events`; DH:1453-1456)

| Row | Class | E22 renders as | Notes |
|---|---|---|---|
| Mode / Target margin / Rate limits / Hold-off | C4 | "ADVISE", "6.0 dB", "2..4", "600 s" | - |
| Recommendation | C2 from RSSI p10 − typical sensitivity (LM:71-93) | "LINKRATE 3 — margin high: faster rate (predicted 29.0 dB)" | **cannot be executed with rcu=false** (GDS:1298-1303, 1873-1874) and nothing says so (F-P1-6); RSSI-floor issue (F-P0-6); "insufficient data" forever on slow SX profiles (F-P1-1) |
| Margin p10 / FER | C2 / C1 (`fer_window`) | "32.00 dB / 0" | - |
| Pass events | C1 | "AOS" | - |

### 4.3 LINK & SECURITY (DH:199-210, renderLink DH:1477-1505)

| Panel / row | Key | Class | Renders as | Notes |
|---|---|---|---|---|
| Framing (5 rows) | `link.tm_framing_cfg/seen`, `decoded.framing`, `link.tc_framing` | C4 / C1 | "auto", "TM 18 / USLP 0", … | - |
| SDLS (14 rows) | `link.sdls.*`, HK fields | C1 / C4 | real counters | "Key file / loaded" concatenates path and badge without space (DH:1491) - cosmetic |
| SDLS TM rejections | `rx.decoder.sdls_rejected` | C1 | "BAD_IV 0 …" | - |
| OID identification | `link.ident` | C1 (untrusted text) | "DE N0CALL VGQ-1" | - |
| COP-1 FOP (9 rows) | `cop.*` | C1 / C2 (effective timers) | real | - |
| CLCW (6 rows) | `cop.*`, HK | C1 | real | - |
| Uplink (6 rows) | `rx.uplink.*`, `link.station_id` | C1 | real | - |
| Time correlation: partition, last SCLK, SCET, estimate, samples, drift, residual | `time.*` (GDS:2214-2221) | C1 / C2 (drift needs ≥2, residual ≥3 samples) | "—" until correlated | honest |
| Time correlation: One-way light time (configured) | `time.owlt_s` (GDS:461) | **C6** when distance is 0 | **"0.000 µs"** | F-P0-7 |
| TIMECORR reference key | `time.timecorr_key` | C4 | "MCFC" | - |

### 4.4 FRAMES & PACKETS (DH:212-220, renderFrames DH:1506-1518)

| Panel | Class | Notes |
|---|---|---|
| Virtual channels table (`vc.<name>`: frames, lost, VCFC gaps, last VCFC, packets, idle, partials, bad lengths, SDLS rejected, last ERT; GDS:2167-2173) | C1; `lost` C6 across passes (F-P0-4) | OID row shows "—" for packet columns (no extractor) - correct |
| Packets by APID (count, seq gaps, lost, last seq, size, IAT, age, latency mean/SEM/min/max/n) | C1; IAT C2 (EWMA); latency C2 (needs time correlation, "—" otherwise) | lost across passes C6 |
| Frames by format and VC | C1 | duplicate of RECEIVER |
| Time correlation | as 4.3 | OWLT "0.000 µs" (F-P0-7) |
| CLCW / COP-1 | C1 | duplicate of LINK & SECURITY |

### 4.5 SPACECRAFT (DH:222-234, renderSpacecraft DH:1523-1584)

| Panel | Class | Notes |
|---|---|---|
| Housekeeping table (43 fields, DICT:184-233): value, decoded enum/bits, unit, limit | C1 (on-board values) + C2 (enum decode); NA sentinels -> "—" (decom) | `cfdp_seq 0` = none; `seq_next_dt_s` "—" honest |
| Telecom table (24 fields, DICT:234-259) | C1; on an E22 spacecraft `uplink_snr`, `uplink_freq_err` are NA -> "—" (C3); `rf_flags` b0 "Rx boosted gain verified" always 0 on E22 (C3) | - |
| Fault protection bits / Sensors bits | C1 | "b0 … SET" / "OK / MISSING" |
| Beacon chart + history | C1 | - |
| Onboard state (10 rows) | C1 | - |
| Other packets (CMDVER, TIMECORR, RFSCAN) | C1 | - |
| Spacecraft EVRs | C1 (untrusted text) | - |
| Anomaly detector (`anomalies`, GDS:203-229, 868-871) | C2 (EWMA |z| > 6 after 30 samples) | tested in isolation with counter steps and a 30 s uptime ramp: no false anomaly - no fix needed |

### 4.6 SPECTROMETER (DH:236-269, renderSpectrometer DH:1594-1684; `/api/science` from `spectral.py`)

All values come from the AS7265X SPEC v3.1 / SPECBURST packets (C1) or the ground analysis
(C2, every panel labelled "approximate (18-band)", "heuristic", "proxy" or "informational").
Rendered with the `e22test` run (see scratch `render.json`):

| Panel | Class | Notes |
|---|---|---|
| Instrument state (15 rows) | C1 (C2 for "Onboard spectral change", class) | real |
| Spectrum ± 1σ, Raw counts, Normalized | C1 / C1 / C2 | real |
| Reflectance | C2, needs SPECREF | "not compatible: — · reference — ·" when there is no white reference - wording (F-P2-6) |
| Channels table (18 rows) | C1 + C2 | real |
| Spectral features & counters | C2 / C1 | real |
| Vegetation / spectral indices | C2; basis line says "computed on the raw spectrum (no compatible white reference): not surface reflectance indices" | NDVI -0.65 of a light source is shown as a number in a vegetation table; acceptable with the basis line, better greyed (F-P2-6) |
| Radiometry, Colorimetry, CIE chart | C2 "approximate (18-band)" | real derivations with formulas |
| Light-source classification | C2 heuristic vs C1 onboard | ground confidence "3 %" next to onboard "80 %": two different scales - document |
| Fluorescence, Spectral library, white reference | C2 / C1 ground state | honest placeholders "only in FLUOR mode", "no library match" |
| SPECCFG / SPECMODE builders | C4 (dictionary limits) | - |
| Waterfall, Illuminance history, CCT history | C1 / C2 | - |
| Burst table, series, FFT, channels, transients, previous | C1 / C2 (transients heuristic) | real |
| Sky map | C2, needs attitude-tagged spectra (BHI260 fusion via SPEC flags b7) | depends on requirement 4 attitude quality |
| Die-temperature drift | C2 "informational" (die temperature ±8.5 °C, shown in notes) | honest |
| Event log, Warnings & notes | C1 / C4 (datasheet facts) | - |

No placeholder or simulated-only value was found on this tab; it does not depend on the station radio.

### 4.7 SCIENCE (DH:271-277, renderScience DH:1810-1852)

**Magnetometers** (`tlm.MAG.values`, DICT:277-286; FL MAG builder):

| Row | Class | Notes |
|---|---|---|
| RM3100 outboard / inboard Bx By Bz, RMS | C1 (raw, uncalibrated, mean of `nsamples`); RMS C2 (on-board) | requirement 3 (separate pins) does not change the UI |
| BMM150 body | C1 but **mislabelled raw**: it is the BHI260 "Magnetometer corrected" (BHY2 `SensorID.h:22`, NICLA:63, FL:2218) | F-P2-6: DICT:277 desc "raw", D13:2456-2457 |
| |B| | C2 | - |
| Gradient, S/C field (1/r³), Ambient field | C2 with stated dipole assumption | radii C4 from `/api/science` `cfg` |
| Samples / RM3100 cycle count, Flags, Boom radii, Packet time | C1 / C4 | - |

**Attitude** (`tlm.ATT.values`, DICT:287-298; FL:1763-1806):

| Row | Class | Renders (e22test) | Notes |
|---|---|---|---|
| Quaternion w x y z, fusion / TRIAD | C1 (BHI260 RV, 9-DOF) / C2 (on-board TRIAD) | "0.5454 -0.0823 0.4540 0.6997 (device→ENU)" | - |
| Yaw / pitch / roll (body→NED) | C2 (page conversion DH:1781-1800; world frame VERIFY T-SC-09) | "-29.62 / -37.62 / -136.46 °" | - |
| Valid | C1 (flags) | "yes / yes" | - |
| Fusion accuracy estimate | C1 for RV (rad) | "0.035 rad (2.01°)" | with a Game RV (6-DOF) the chip reports 0 (BHI:9118): must render "n/a", never "0.000 rad" (F-P1-9) |
| Fusion vs TRIAD angle | C2 | "0.004 °" (the simulator makes them agree) | - |
| TRIAD magnetometer, BMM150 scale | C1 (flags + HK magcal_state) | "BMM150 (body)", "firmware fallback 1/16 µT/LSB" | - |
| Acceleration (g), \|a\| | C1 (flag b1 only when non-zero, NICLA:181) | "0.000 0.000 1.000 \|a\| 1.000" | - |
| Rate (°/s) | C1, **C6 when the gyro virtual sensor never ran**: NICLA:185 sets b2 unconditionally, so the flight sends the zeros as valid (FL:1782-1785) | "2.9 4.2 6.3" | F-P1-9 |
| BMM150 field (µT) | C1 (corrected) | "19.5 0.5 44.0 \|B\| 48.1" | - |
| Flags, Packet time | C1 | badges run together ("QUATACCELGYRO…", DH:1843) | cosmetic |

**Environment** (`tlm.ENV.values`, DICT:299-311; FL:1809-1845):

| Row | Class | Notes |
|---|---|---|
| Pressure (BMP390) | C1 (only when 300-1250 hPa, NICLA:203-207) | - |
| Temperature (BME688) | C1, **C6 if the temperature virtual sensor never ran** (NICLA:209 sets b2 unconditionally) | F-P1-9 |
| Relative humidity | C1 (b3 only when > 0) | whole %RH stated |
| IAQ / static IAQ, CO₂eq, bVOC, BSEC accuracy | C1 when BSEC flag, else "—" (DH:1848-1849) | - |
| Gas resistance | C1 (b4 only when > 0) | - |
| Flags, Packet time | C1 | - |

**What requirement 4 (BHI260AP 6-DOF IMU in the transmitter) adds to the UI** - proposal for
the contract (keys are proposals; the ATT layout must change in DICT, flight and Nicla together):

| Proposed value | Basis | UI rule |
|---|---|---|
| 6-DOF Game Rotation Vector quaternion (ID 37) next to the 9-DOF RV (ID 34) | BHI:3430, 8495; BHY2 `SensorID.h:30, 32` | show both with their frames; GRV has no absolute heading |
| GRV accuracy | BHI:9118 ("reported as 0") | render "n/a (6-DOF: no estimate)", never 0 rad |
| Per-virtual-sensor status 0..3 (Unreliable / Low / Medium / High) for accel, gyro, mag, RV | BHI:9719-9734 (Table 100) | colour Unreliable red, Low amber; only if the Nicla sketch can obtain it (Arduino_BHY2 support not verified in this audit) |
| Gravity vector (ID 28) and Linear acceleration (ID 31) | BHI:8592; BHY2 `SensorID.h:26, 28` | g units, with \|g\| check |
| Data age / update flag per sensor (from `dataAvailable()`) | BHY2 `SensorClass.h:47` | "—" when no new sample since the last page |
| Accel / gyro dynamic range in use | NICLA:35-40 (range read via parameter 0x0500+ID) | show as configuration (C4) |
| Rotation-rate magnitude, tilt from gravity | C2 | label "est." |

Each new snapshot key needs: the SCIENCE renderer, docs/13 §9.2 and §18, `tests/test_docs.py`
key checks (test_docs.py:12-13, 43-48) and the XTCE (generated from DICT, `products.py:1318`).
The two 32-octet Nicla pages are full (NICLA:16-33): more fields need a third page or a layout
change - out of R4 scope (interface report).

### 4.8 MAGCAL (DH:279-285, renderMagcal DH:1857-1889; GDS:2134-2146)

| Panel | Class | Notes |
|---|---|---|
| Site field note | C4 (`[science] field_ut`) | "NOT SET (the fit refuses without it)" honest |
| Unit cards: collecting, samples/rejected/duplicates, coverage, centre, fit (offset, soft-iron rows, residuals, condition, axis ratio, encoded args, refusal reasons), uplink command | C1 ground collector / C2 fit | not collecting: "— / — / —" (coverage None) - acceptable |
| Onboard calibration and BIST | C1 (HK) | "not tested" |

### 4.9 SEQUENCES (DH:287-302, renderSequences DH:1890-1903) - C1 (HK seq_state/count/next) and C2 (ground check). "Next entry in —", "first slot —" honest.

### 4.10 FILES (DH:304-329, renderFiles DH:1978-1994, renderLogger DH:1926-1954)

| Panel | Class | Notes |
|---|---|---|
| CFDP transactions | C1 | - |
| Receiver counters and onboard state | C1; "Sequence number 0x00000000 (0)" when HK `cfdp_seq` = 0 = "none" | F-P2-5 |
| TDM summary rows | C1 | - |
| Received files / TDM list / exports | C1 (file system) | - |
| Receiver data logger (21 rows) | C1 (datalog health) / C4 (keep days, caps) | "of 16.00 GB" cap = `archive_max_gb` sized for the VENTUNO Q 64 GB eMMC (STOML comment; UNO Q 4 GB has 32 GB, UNOQ:473; R1 item 3) |
| CSV export | C1 | `rf` export of an E22 station has always-empty columns `snr_db`, `freq_err_hz`, `afc_hz`, `doppler_hz`, `range_rate_m_s` (RXS:504-515; archive `RX_STATS_COLUMNS`) - C3, document |

### 4.11 COMMANDING (DH:331-345, renderCommanding DH:1999-2016)

| Panel / row | Key | Class | Renders as | Notes |
|---|---|---|---|---|
| Command entry, pending, history, console, event log, dictionary | various | C1 / C4 | real | - |
| GDS health: version/mode, schema, uptime, snapshot time, stage errors, last stage error | `version`, `schema`, `uptime_s`, `utc`, `health.*` | C1 | real | - |
| GDS health: "TDM records refused (station clock)" | `health.tdm_rejected` (GDS:692-700) | C1, **label C6** | "0" | also counts out-of-range measurements (the -256 dBm RSSI, GDS:696-699) - F-P2-3 |
| Station I/O panel title | DH:342 | **C6** | "VENTUNO Q MCU: one CardKB on the header Wire bus, annunciators, Radio Control Unit" | F-P1-8 |
| MCU link / Last hello | `station_io.connected`, `connected_t` (GDS:2245-2247; stale 12 s, GDS:106) | C1 | "no hello (station I/O off or MCU not running)" | real |
| Firmware | `station_io.fw` | C1 | "—" | - |
| CardKB (header Wire, 0x5F) | `station_io.kb` | C1 (SK read_cardkb, R1 §5.3) | "not detected" | real |
| **Radio Control Unit** | `station_io.rcu` | **C6 constant 1** (SK:1466) | "present" whenever a hello arrives | F-P0-5 |
| Typing on the CardKB | `station_io.input` | C1 | "> _" even with no MCU link | show "—" when not connected (P3) |
| Last annunciator line, Beacon level sent | `station_io.last_status_line`, `beacon_sent` (GDS:2074-2079) | C1 | "—" | real |

---

## 5. Machine interfaces: /metrics and the JSON API (WEB:348-401, 818-859)

Observed in `scratchpad/r4work/out/e22/metrics.txt` (132 families E22, 171 SX126x;
D13:3009 quotes 141 / 179 for other runs).

| Issue | Evidence | Class | Fix |
|---|---|---|---|
| Last-reception gauges stay "current" after LOS (`dssq_rf_rssi_dbm`, `dssq_rf_margin_est_db`, `dssq_rf_snr_est_db`, `dssq_rf_packet_*`, `dssq_budget_measured_*`) while the page hides them (DH:1096-1098) | WEB:359-362 exports `rx.rf` unconditionally | C6 | F-P1-10 |
| E22 exports never-measured / config values as gauges: `dssq_rf_packet_crc_ok 1`, `dssq_rf_doppler_offset_cal_hz 0.0`, `dssq_rf_doppler_calibrated 0`, `dssq_rf_snr_db_n 0`, `dssq_rf_freq_err_hz_n 0` | e22 metrics.txt; WEB:361-362 | C6 / C4 / C3 | F-P0-2, F-P1-10 |
| `dssq_frames_fer`, `dssq_frames_lost`, `dssq_frames_vc_lost` carry the AOS-jump mod-256 counts | WEB:368-375 | C6 | F-P0-4 |
| `dssq_decoder_symbol_error_rate_est`, `…_symbols_corrected`, `…_corrected` on the E22 | WEB:364 | C6 on HW | F-P0-3 |
| `dssq_budget_range_at_sensitivity_km_est` with distance 0 (pure free space) | WEB:377 | C6 | F-P1-7 |
| Overload readings (-256 dBm) reach `dssq_rf_rssi_dbm` | - | C6 | F-P0-1 |
| Missing families: `sync.*` (slips, lock losses, discarded octets: the E22's real integrity counters), `rx.serial` (noise queries/replies), `rx.radio_link.connected`, `linkmgr.recommendation.margin_p10_db`, window percentiles (`rx.windows.10m.rssi.p10`), `station_io.connected`, `radio.sx126x.stats` | WEB:348-401 has no call for them | C5 | F-P1-10 |
| `dssq_apid_iat_s` help says EWMA (correct) while the page says "meas." | WEB:394; DH:185 | label | F-P2-3 |
| `/api/state` `schema` stays 3 (GDS:2178) | semantics of `frames.lost`, `packet.crc_ok`, `station_io.rcu` change with the fixes | - | bump to 4 when keys/semantics change (F-P1-10) |
| `/api/state` exposes `radio.ground_config = null` with rcu=false without a reason | GDS:2191 | C5 | F-P1-4 |
| `/api/xtce` is generated from DICT (`products.py:1318`): no change needed except for new ATT fields | - | - | regenerate with requirement 4 |

The JSON API itself is consistent (`json_safe`, NaN -> null, WEB:571-573); every rendered key
exists (`tests/test_web.py:731-746`).

---

## 6. VENTUNO-Q-specific items and the UNO Q ground station

| Item | Location | Status on the UNO Q branch |
|---|---|---|
| Panel subtitle "VENTUNO Q MCU: one CardKB on the header Wire bus, annunciators, Radio Control Unit" | DH:342 | rename to "Station MCU (UNO Q STM32U585)"; the sketch ports (R1 §1 item 1) |
| `station_io.rcu` constant | SK:1466, FP:211-212, DH:2014, D13:2873 | F-P0-5 |
| docs/13 intro and panels name the VENTUNO Q | D13:4, 1214, 2864 (3 occurrences) | rewrite |
| `frontpanel.py` docstring "VENTUNO Q's Linux processor ... STM32H5 MCU" | FP:3 | UNO Q: QRB2210 + STM32U585 (UNOQ:19) |
| Operator messages "VENTUNO Q MCU wired to the …" | GDS:1300-1303 | reword (R1 item 3) |
| SX126x MCU panel, AFC, `rf_foff`, `mcu_age_s` | DH:1415-1435, GDS:2157-2158 | not VENTUNO-specific: the same sketch/Router Bridge path on the UNO Q MCU (R1); still C3 |
| Snapshot cost on the slower CPU | `snapshot()` is built inside the GDS lock (GDS:2151); each `/api/state` poll (1 Hz per browser) and each `/metrics` scrape (WEB:833) builds one; measured `ReceiverStats.snapshot()` with 3 × 8 × 20 000 samples: 138.8 ms on this x86 machine (realistically only the pass window fills; ~1/3 of that) | **VERIFY on the QRB2210** (4 × A53 @ 2.0 GHz, UNOQ:453 vs QCS8275 octa-core, VQ:690). Option: cache one snapshot per second for all clients (F-P2-7) |
| Archive caps and free-space floor | STOML `archive_max_gb = 16.0` | 32 GB eMMC on ABX00173 (UNOQ:473; R1 item 3) |

---

## 7. Ultra-long-range review of what the UI shows

| # | Topic | Today | Why it matters at ultra long range | Fix |
|---|---|---|---|---|
| U1 | RSSI-based margin, path loss, excess loss, range, link manager | RXS:559, 634, 639-644; LM:76 | if the RSSI octet is total in-band power, it cannot go below the noise floor; LoRa decodes below the noise (the docs' own claim D13:677), so the margin saturates at noise - sensitivity (≈ +10 dB, green) exactly where frames start to drop | F-P0-6 |
| U2 | Sensitivity = datasheet typical | LNK:68 | worst-case module is 2 dB (T33D) / 1 dB (T37S) less sensitive (T33D:222, T37S:259) | F-P1-3 |
| U3 | 60 s rate windows, "1 min" statistics | RXS:54, 576-612 | SX126x profile 0/1 frame periods 34.6-138.5 s -> 0-1 frame per window: frames/min and info rate jump between 0 and 60/span values | F-P1-2 |
| U4 | Link manager minimum of 10 RSSI samples in 600 s | LM:72-74 | profile 0: 4.3 / 6.9 / 8.7 frames per 10 min (SAFE / CRUISE / ENCOUNTER), profile 1 SAFE 8.7: never evaluates | F-P1-1 |
| U5 | Lifetime FER / lost frames mod 256 | RXS:377-388, `ccsds/tm.py:252` | long LOS gaps between passes are the norm: the header FER becomes a function of gap length mod 256 | F-P0-4 |
| U6 | Distance and OWLT | STOML:32 `distance_km = 0.0`; GDS:461 | design column empty, OWLT "0.000 µs", range "26515 km measured" | F-P0-7, F-P1-7 |
| U7 | SNR est. pairs RSSI with noise of any age in windows/series/histogram | RXS:319, 323, 402, 668 | with a 20 s noise poll and multi-minute frame periods, the first frames of every pass pair with the previous pass's noise | F-P1-5 |
| U8 | Charts' fixed ranges | RSSI histogram -150..-20 dBm (RXS:199), SNR -30..60 dB (RXS:200), RF survey -130..-50 dBm (DH:1325) | adequate for E22 (-130 max) and SX126x SF12 (-148 dBm, SNR -20 dB); no change | - |
| U9 | LOS timeout, FOP timers | scale with the frame period (GDS:1806-1824) | correct | - |
| U10 | TDM / Doppler | SX126x only | E22 cannot measure range rate; say so once on the RECEIVER tab | F-P2-1 |

---

## 8. docs/13 statements that contradict the code (docs writers)

| # | docs/13 | Code | Correct statement |
|---|---|---|---|
| D1 | line 4 "serves on the Arduino VENTUNO Q"; 1214; 2864 | - | UNO Q (ABX00173) |
| D2 | line 408 header diagram "RSSI-1m" | DH:1096 newest frame | "RSSI (newest frame)" |
| D3 | lines 467, 485 "RSSI 1-min", "signal strength, averaged"; 486 SNR "averaged" | DH:1166-1167 newest frame | newest frame |
| D4 | line 228 "VERIFY T-RF-07 … E22-400T33D codes 1-3" | LNK:488, RXS:661: every T33D code (incl. 0) is VERIFY | "every T33D code and every SX126x code" |
| D5 | line 677 "SNR est. goes negative near the limit … about -10 dB" | depends on which RSSI the E22 octet is (not stated, T33D:782-784) | VERIFY item (section 11 Q1) |
| D6 | lines 2456-2457 "raw: no calibration is applied to the MAG packet" | BMM150 values are BSX-corrected (BHY2 `SensorID.h:22`) | RM3100 raw, BMM150 BHI260-corrected |
| D7 | line 2873 RCU "the MCU drives the E22 M0 / M1 and reads AUX" | SK:1466 constant | after F-P0-5 |
| D8 | lines 869-870, 1173-1176 RS examples from simulator-injected errors on the E22 | SIM:641-643 | E22 hardware shows 0; examples from the SX126x path |
| D9 | line 923 "without a measurement (no RSSI yet) the excess loss is taken as 0" | excess is None whenever `distance_km` is 0 (RXS:635-636), with RSSI present | after F-P1-7 |
| D10 | line 53 examples "with a configured distance of 2 km" | STOML:32 default 0 | state both |
| D11 | line 1016/2873 RCU described as available | default `rcu = false` (STOML:169) -> "off", no read-back, GSCAN/LINK* refused | describe the E15-USB-T2 default first |
| D12 | lines 841-850 statistics "dBm, 4" decimals | D-70 resolution rendering (DH:1258) | already explained below the table; align the column |
| D13 | line 3009 family counts 141 / 179 | 132 / 171 in this audit's runs (depends on packets seen) | say "about" or drop |
| D14 | section 13.7 "TDM records refused (station clock)" | GDS:696-699 also out-of-range measurements | after F-P2-3 |
| D15 | screenshots `docs/img/ui_*.png` (D13:63-65) | E22 code 7 sim with injected RS errors, VENTUNO labels | retake after the fixes |

---

## 9. Prioritised fix list (exact code locations)

Priority: **P0** = a number shown as a measurement that is wrong or constant on the default
hardware, or a hard-coded value; **P1** = legitimate value that is misleading in context
(ultra long range, rcu=false, metrics); **P2** = presentation / labels / SX-only clutter /
simulator; **P3** = cosmetic.

### P0

**F-P0-1 E22 RSSI overload / out-of-range readings (L-45, V-70).**
Where: FS:125-133 (`_emit`, RSSI octet -> dBm at FS:128); RXS:311-331 (`on_reception`), RXS:322
(histogram), RXS:396-405 (series), RXS:547-571 (rf); GDS:645; GDS:1977-2047 (alarm).
Change: keep the raw octet; classify `0x00` (-256 dBm) and readings above the module's
plausible maximum (blocking power +10 dBm, T37S:221; bench overload read 0xFF = -1 dBm, D20:52)
as `OVERLOAD`, and readings below a floor (e.g. -160 dBm, LNK:59 `MEASURED_RANGE_DBM`) as
`INVALID`; such readings do **not** enter windows, histograms, series, margins or `snr_est`;
add `rx.rf.rssi_state` ("OK" / "OVERLOAD" / "INVALID") and counters `rx.rf.rssi_overloads`,
`rx.rf.rssi_invalid`; WARN event + YELLOW alarm "receiver overload: RSSI octet at the end of
its range - add attenuation / distance". Dashboard: RF_KEYS (DH:1206-1220) labels for the new
keys (the drift test `tests/test_web.py:738-741` requires every `rx.rf` scalar labelled),
header/tile show "OVERLOAD" instead of a number. Docs: D13 §4.2, §15, §18; D20 L-45 closed.

**F-P0-2 E22 "CRC ok" is a constant.**
Where: GDS:645 (`self.rx.on_reception(item.ert, item.rssi_dbm)`), RXS:311-317 (default
`crc_ok: bool = True`), RXS:565-566, DH:1244, WEB:361.
Change: `on_reception(..., crc_ok: bool | None = None)`; E22 passes `None`; `packet.crc_ok`
null on E22; DH:1244 renders "n/a (the E22 discards CRC-failed packets inside the module)";
no `dssq_rf_packet_crc_ok` sample on E22. Docs D13:821.

**F-P0-3 RS / ASM / SER / BER / CER presented as channel metrics on the E22.**
Where: tile DH:1172; STAT_METRICS DH:1222-1223 (`rs`, `asm`); histograms DH:1315-1318; decoder
DH:1395-1402; sync DH:1386-1391 ("ASM bit errors"); two-way DH:1447; panel titles DH:183,
193-194; simulator SIM:641-643; test `tests/test_end_to_end.py:133` asserts
`symbols_corrected > 0` on the E22 world.
Change: model-aware presentation (`S.radio.packet_mode` already in the snapshot, GDS:2185 via
LNK:482): on the E22 show these counters under "UART / framing integrity (module discards
CRC-failed LoRa packets, docs/20 L-20)", hide SER / pre-FEC BER / CER (or show "n/a on the E22
path"), and replace the OVERVIEW "RS decoder" tile by "Frames lost (10 min)" from
`rx.frames.fer_10m`. Simulator: in E22 mode do not inject octet errors (SIM:641-643; model a
CRC failure as packet loss, which `p_loss` already does) - keep RS errors for the SX126x
`crc_fail_prob` path (SIM:653-657); move the RS assertion of `test_end_to_end.py:133` to the
SX126x world. Docs D13 §4.10 examples, §3.1 tile, §4.4.

**F-P0-4 Frame loss across passes (mod 256) and lifetime FER in the header.**
Where: RXS:362-392 (`on_frame`: `lost` added at AOS, `vc_loss` not reset), RXS:603-605;
`ccsds/tm.py:244-252` (mod 256); GDS:796-807 (APID `lost` across passes); DH:1099-1100 (header),
DH:1170 (tile), DH:1403-1411, DH:1508-1511; WEB:368-375.
Change: at AOS (`aos` True) do not add the MCFC / VCFC jump to `frames_lost` / `per_vc.lost`;
record it as `rx.frames.aos_gap_frames` (documented as ambiguous mod 256 / reboot); reset
`vc_loss` and the APID gap baseline at AOS (or count gaps across LOS separately as
"not received while out of view"); header and OVERVIEW show `fer_10m` (or a new pass FER
`rx.frames.fer_pass`) labelled "FER 10 min (in pass)". Keep `fer` only in the RECEIVER
frames table with its explanation. Docs D13:428-429, 1185-1187.

**F-P0-5 Station I/O "Radio Control Unit: present" hard-coded.**
Where: SK:1466 (`"H|%s|1|%d"`), FP:211-212, GDS:2066-2068, DH:2014, D13:2873. Same as R1 §5.3.
Change: either send the real state (the station's last `rcu_mode` result and its age, the only
evidence: AUX went HIGH after a mode change, R1 §5.3) or drop the field and show
"RCU in use: yes/no" from `radio.rcu` (GDS:2188) plus `[gds] rcu`. `tests/test_docs.py:43-48`
checks `station_io` keys against D13.

**F-P0-6 RSSI-derived margin at the noise floor (ultra long range) - conditional on VERIFY Q1.**
Where: RXS:559 (`margin_est_db`), RXS:634, 639-644 (path loss, excess, range), RXS:505-506
(logger `margin_db`), LM:71-93, RXS:619/679 (uplink margin), DH:1098, 1168-1169, 1283, 1445.
Change: add `rx.rf.rssi_above_noise_db` (same-pass RSSI - noise, already computed as
`snr_est_db`) and a state: >= 10 dB "signal-dominated" (RSSI-based figures valid), 3-10 dB
use `pr_est_dbm` (RXS:443-448) instead of RSSI for margin / path loss, < 3 dB "at the noise
floor: RSSI cannot measure the margin" -> margin shown as "≤ <noise - sensitivity> dB
(not measurable)" in amber, never green; link manager refuses "margin high -> faster" when
the 10-min RSSI p10 is within 3 dB of the noise mean. If the bench test (Q1) proves the E22
octet tracks the signal below the noise (SignalRssi-like), keep the current formula and
document the proof instead.

**F-P0-7 OWLT "0.000 µs" when the distance is not configured.**
Where: GDS:461 (`self.owlt_s = distance_km / c`), GDS:2220, GDS:1016 (SCET uses it), DH:1475
(`owlt(t.owlt_s)`).
Change: snapshot `time.owlt_s = None` when `distance_km <= 0` (keep 0.0 internally for SCET at
GDS:1016) -> "—"/"not configured". Docs D13 §5.8.

### P1

**F-P1-1 Link manager unusable at slow SX126x profiles.** LM:72-74 (n >= 10 in the 10-min
window), GDS:1866-1870 (passes `self.rx.w["10m"]`), RXS:53 `FER_WINDOW_S`.
Change: evaluation window = max(600 s, 10 x expected frame period from `_frame_period_s()`,
GDS:1806-1809); the `ReceiverStats` needs a variable-horizon RSSI window (or use the pass
window); report the window used in `linkmgr.recommendation`. Docs D13 §4.16.

**F-P1-2 Rates over a fixed 60 s window.** RXS:576-578, 610-612 (`frames_per_min`,
`info_rate_bps`, `cadu_rate_bps`); windows "1m" (RXS:54) and their table rows (DH:1254-1260).
Change: span = max(60 s, 5 x expected frame period) (frame period available in the GDS; pass
it to `snapshot()`), add `rx.frames.rate_window_s`; label "(n s)" on DH:1405-1406 and the
OVERVIEW tile. Docs D13 §4.11.

**F-P1-3 Sensitivity = typical only.** LNK:68 (`E22_SENS_2K4_DBM`), LNK:393-413, DH:1169,
1282, 1343.
Change: add worst-case values (T33D -126, T37S -125 dBm at 2.4 kbit/s; T33D:222, T37S:259) as
`sensitivity_worst_dbm`; show "typ -128 (min -126) dBm"; colour the margin against the worst
case; keep "measured" (T-RF-03) precedence. RF_KEYS label needed.

**F-P1-4 Unverified E22 registers and module presence with rcu = false.** GDS:2191
(`ground_config` None), DH:1361-1366 (section silently absent), DH:1354 (Backend
"available" = port open), E22PY:332-334; RXS:615-617 (noise replies).
Change: `radio.register_check` = "not read back: no Radio Control Unit ([gds] rcu = false) -
RSSI byte and noise enable assumed from station.toml" and `radio.module_answering`
(= a noise reply within 3 x `noise_poll_s`, from `rx.serial.noise_replies` and `noise_t`);
render both in the radio-configuration and serial panels; GSCAN/LINKRATE rows say
"needs RCU". Docs D13 §4.6, §4.14.

**F-P1-5 SNR-est. pairing with stale noise in windows / series / histogram / design SNR.**
RXS:319, 323, 402, 668 vs the same-pass rule at RXS:540-546 and RXS:502-503.
Change: one helper `_noise_for(t)` returning the noise only when `|noise_t - t| <=
los_timeout_s` (or `STALE_PAIR_DEFAULT_S`), used everywhere. Docs D13:845.

**F-P1-6 Link-manager recommendation not executable without an RCU.** GDS:1298-1303,
1873-1874; LM:100-103; DH:1454.
Change: `linkmgr.executable` (radio manager available) and `why_not` text; DH shows
"advice only: LINKRATE needs the Radio Control Unit ([gds] rcu = false)".

**F-P1-7 "Range at sensitivity" in the measured column with no distance.** RXS:641-644,
DH:1284, WEB:377.
Change: when `excess_loss_db_est` is None, return `range_at_sensitivity_km_est = None` and a
separate design figure `range_at_sensitivity_free_space_km` (design column); the measured
column only with a measured excess loss. Docs D13:923.

**F-P1-8 VENTUNO Q wording in the UI.** DH:342; FP:3; GDS:1300-1303; D13:4, 1214, 2864.
Change: "Station MCU (UNO Q STM32U585)"; see R1 section 5.2 for the full list.

**F-P1-9 Nicla validity flags (affects SCIENCE and the bus temperature).** NICLA:185 (gyro b2
unconditional), NICLA:209 (temperature b2 unconditional); FL:1782-1785, 1833-1836; DH:1841,
1847.
Change: set each flag only when `SensorClass::dataAvailable()` reported a sample since the
last page (BHY2 `SensorClass.h:47-48`; note `clearDataAvailFlag()` there is declared `bool`
without a return value) or when a non-default reading arrived; with 6-DOF GRV render its
accuracy as "n/a" (BHI:9118).

**F-P1-10 /metrics legitimacy.** WEB:348-401.
Change: skip `rf_rssi_dbm`, `rf_margin_est_db`, `rf_snr_est_db`, `rf_pr_est_dbm`,
`rf_packet_*`, `budget_measured_*` while `rx.state.los` (or export `dssq_rf_packet_age_s`
and document staleness); do not export `rf_doppler_*` / `rf_packet_crc_ok` on the E22; add
`dssq_sync_*`, `dssq_serial_*`, `dssq_radio_link_connected`, `dssq_linkmgr_margin_p10_db`,
`dssq_rssi_10m_p10_dbm`, `dssq_station_io_connected`, `dssq_rf_rssi_overloads`; bump
`schema` (GDS:2178) to 4. `tests/test_web.py:636-639` checks family names. Docs D13 §16.

### P2

**F-P2-1 SX126x-only panels on an E22 station.** DH:177-181 (freq-error chart, gauge, AFC,
Doppler), 186 (SX126x MCU), 192 (freq-error histogram); RF-now rows for `required_snr_db`,
`snr_margin_db`, `freq_offset_*`, `crc_fail_packets`, `packet_error_rate`; window rows
`snr_meas`, `freq_err`; two-way SNR/frequency rows; sync counters on the SX126x; "RS rescued"
and "Ground radio fault reports" (T33D) showing "0".
Change: render these only when `S.radio.packet_mode` (or the T37S for the fault log); one
explanatory line instead ("E22 UART module: no SNR, frequency error, AFC or Doppler -
SX126x option only, docs/20 L-20"). Note `tests/test_web.py:731-746` requires every key to be
*read* by the script, not displayed - keep the reads.

**F-P2-2 OVERVIEW on the SX126x.** DH:1167-1169: show measured SNR as the big value and the
SNR margin (`rx.rf.snr_margin_db`, RXS:560) as the margin when available (independent of the
RSSI calibration, D13:779).

**F-P2-3 Labels and precision.** DH:1392 and 1407 "Time in lock" -> "Time in AOS since GDS
start"; DH:2011 "TDM records refused (station clock)" -> "(station clock or out-of-range
value)"; DH:185 and 1412 "IAT s (meas.)" -> "IAT s (EWMA)"; DH:191 "SNR distribution" ->
"SNR est. distribution" on the E22; DH:1233 linear power 5 -> 2 significant digits (D-70);
DH:1282 and RF_KEYS sensitivity 4 decimals -> 1; DH:1280 thermal-noise row "B assumed" on the
E22; DH:1406 "CADU rate on air" -> "CADU rate (payload bits)".

**F-P2-4 OVERVIEW "RS decoder" tile replacement** - part of F-P0-3 (tile DH:1172).

**F-P2-5 Values that mean "none".** DH:1177 and 1987 CFDP `seq` 0 -> "none" (DICT HK
`cfdp_seq` "0 = none"); DH:1446 "1 ok / 1 bad": annotate that the spacecraft counts the
ground's plain-text identification as a bad CLTU (`link.station_id.count`, GDS:2204).

**F-P2-6 Context wording.** DH:1087 SYNC pill SEARCH red during an expected LOS -> grey when
`rx.state.los`; DH:1626 "not compatible: —" -> "no white reference"; DH:1641-1642 indices
greyed unless the basis is reflectance; DH:1820 "BMM150 body (Nicla Sense ME,
BHI260-corrected)" and DICT:277 description; D13:2456-2457.

**F-P2-7 Simulator and performance.** SIM:641-643 (F-P0-3); SIM `get_config()` lacks
`rx_gain_reg` while reporting `rx_boost_verified` (DH:1427-1430 renders "— boosted 0x96
verified"); snapshot cost on the QRB2210 (Q6): optional shared 1 s snapshot cache in
`GroundStation.snapshot()` (GDS:2148-2252) for `/api/state` + `/metrics`.

### P3 (cosmetic)

DH:2015 "Typing on the CardKB > _" when the MCU link is down -> "—"; DH:1491 key file and badge
without a space; DH:1843 ATT flag badges run together; DH:1410 blank "lost" cells for format
rows; DH:1341 "Frequency (MHz)" 4 decimals vs 6 in RF now.

---

## 10. Tests and drift guards affected by the fixes

| Test | What it pins | Affected by |
|---|---|---|
| `tests/test_web.py:731-746` `test_dashboard_reads_every_snapshot_key` | every top-level key read; every `rx.rf` scalar labelled in RF_KEYS; every window metric labelled | F-P0-1, F-P0-6, F-P1-3 (new `rx.rf` keys need RF_KEYS rows) |
| `tests/test_web.py:624-646` | /metrics family names (`dssq_frames_lost`, `dssq_decoder_rs_rescued`, …) | F-P0-4, F-P1-10 |
| `tests/test_web.py:778-803` Node render of all 11 tabs, no "undefined"/"NaN" leaks | renderer robustness | every DH change |
| `tests/test_docs.py` (lines 1-48) | every snapshot key documented in D13 and every D13 path exists | every new/renamed key, `station_io` keys (F-P0-5) |
| `tests/test_end_to_end.py:133` | `symbols_corrected > 0` on the E22 world | F-P0-3 (simulator change) |
| `tests/test_end_to_end.py:77`, `tests/test_docs.py:151-155`, `tests/test_web.py:599` | simulator `max_errors` on E22 runs | F-P0-3 |
| `tests/test_end_to_end.py:898` | link-manager recommendation | F-P1-1, F-P0-6 |

---

## 11. Open questions / VERIFY

1. **Q1 (decisive for F-P0-6):** is the E22 RSSI octet the total in-band power (like SX126x
   `RssiPkt`) or a despread signal estimate (like `SignalRssiPkt`)? Neither manual says
   (T33D:782-784, T37S:914-919). Bench test: attenuate the downlink in 1-2 dB steps through
   the sensitivity limit at code 2 and record RSSI octet, noise register and frame loss; if
   RSSI stops at the noise reading while frames still decode, F-P0-6 is required.
2. **Q2:** does the E22 drop CRC-failed packets? Stated by the project (D20 L-20) but not found
   in the manuals; a frame with RS corrections > 0 on real hardware would disprove it.
3. **Q3:** what does the E22 do with `C0 C1 C2 C3 00 02` when REG1 b5 (noise enable) is 0 -
   ignore it or radiate it as 6 octets of payload? Not stated (T33D:737-756). With
   `rcu = false` the register cannot be checked by the GDS; a radiated query every 20 s would
   be an unidentified transmission.
4. **Q4:** which accuracy / status information can the Nicla sketch obtain through Arduino_BHY2
   for the 6-DOF integration (per-sensor status, Table 100, BHI:9719-9734)? Not verified here.
5. **Q5:** should the ground show the T33D sensitivity typical (-128) or worst case (-126) as the
   primary figure (owner decision for F-P1-3)?
6. **Q6:** `GroundStation.snapshot()` duration on the UNO Q QRB2210 under a real pass (pass
   window filling) with one browser plus a Prometheus scrape - measure (section 6).
7. **Q7:** keep the SX126x option (C3 panels) on the UNO Q station MCU? R1 says the sketch
   ports; the UI rules in F-P2-1 assume it stays optional.
8. **Q8:** does the owner want the Station I/O panel at all (CardKB, annunciators) on the UNO Q
   ground station? If not, the panel and `station_io` keys can go (with D13 §13.8, §18 and
   `tests/test_docs.py`).
