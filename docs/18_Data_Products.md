# 18 - Data Products

What the ground station and the spacecraft EGSE record, where it is, the exact file and table
formats, and how to get the data into Python, pandas or a spreadsheet. Every format below is
taken from the code ([`dssq/archive.py`](../dssq-groundstation/python/dssq/archive.py),
[`datalog.py`](../dssq-groundstation/python/dssq/datalog.py),
[`export.py`](../dssq-groundstation/python/dssq/export.py),
[`products.py`](../dssq-groundstation/python/dssq/products.py),
[`replay.py`](../dssq-groundstation/python/dssq/replay.py),
[`ccsds/cfdp.py`](../dssq-groundstation/python/dssq/ccsds/cfdp.py) and the EGSE
[`vgq1-spacecraft/python/main.py`](../vgq1-spacecraft/python/main.py)). The examples in
sections 8 to 10 were run against an archive produced by the GDS with its software spacecraft
(`--sim`); their output shapes are real.

Operating procedures for the data logger and exports are in
[07_Operations_Procedures.md](07_Operations_Procedures.md) section 8; settings in
[15_Configuration_Reference.md](15_Configuration_Reference.md) (`[gds]`, `[cfdp]`, `[tdm]`,
`[spectral]`); the telemetry fields themselves in
[05_Telemetry_and_Command_Dictionary.md](05_Telemetry_and_Command_Dictionary.md).

---

## 1 Overview

| Product | Location (under the archive unless noted) | Format | Written | Read with |
|---|---|---|---|---|
| Raw CADUs as received | `YYYYMMDD/cadu.bin` | Binary records (§4) | Every received radio unit | `python -m dssq.replay`, `dssq.archive.read_cadu_file` |
| Event log | `YYYYMMDD/events.log` | UTF-8 text, one event per line (§5) | Every ground event | Any text viewer |
| Telemetry database | `gds.sqlite` | SQLite 3, WAL journal (§6) | Frames, packets, parameters, events, commands, 1 Hz receiver statistics | Python `sqlite3`, pandas, any SQLite tool (read-only) |
| CSV exports | `exports/VGQ1_<product>_<from>_<to>.csv` | RFC 4180 CSV, UTF-8 (§8) | On request (FILES tab or CLI) | Spreadsheets, pandas |
| Tracking Data Messages | `tdm/VGQ1_<station>_<UTC>.kvn` | CCSDS 503.0-B-2 KVN (§11) | At LOS and on request | Orbit / tracking tools, `python -m dssq.products tdm validate` |
| XTCE mission database | Downloaded (`/api/xtce`) or written by the CLI | CCSDS 660.0-B-2 / XTCE 1.2 XML (§12) | On request | Mission control systems |
| Files received by CFDP | `files/<name>` (+ `.partial`, `.json`) | The transferred file (§14) | When a transfer ends | Depends on the file |
| White reference, spectral library | `spec_white_ref.json`, `spec_library.json` | JSON (§15) | `SPECREF`, `SPECLIB` | The GDS (reloaded at start) |
| SDLS state | `sdls_sn.txt`, `sdls_sn.txt.lock`, `sdls_tm_replay.txt` | Text counters (§15) | Every protected frame | The GDS only (do not edit) |
| Spacecraft EGSE files | UNO Q `<App>/data/` | Binary and JSON Lines (§10) | By the EGSE | `dssq.replay`, text tools |

---

## 2 Where the archive is

| Mode | Archive directory |
|---|---|
| App Lab (default) | `/home/arduino/ArduinoApps/dssq-groundstation/data/archive` on the VENTUNO Q (shown as `/app/data/archive` in the App log, because the App folder is mounted at `/app` inside the container) |
| Host (systemd) mode | The same `/home/arduino/ArduinoApps/dssq-groundstation/data/archive`: the installed service runs `python -m dssq.gds --app-data <App>/data`, so App Lab and host mode share one archive (and with it one SDLS TC sequence-number file). Only a stand-alone run with `--config` and no `--app-data` puts a relative `[gds] archive_dir` under its working directory |
| Elsewhere | An absolute `[gds] archive_dir`, e.g. an external M.2 2230 NVMe (non-bootable) or a USB 3.0 drive. In App Lab this works only if the path is visible inside the App container: **VERIFY on hardware** before relying on it (listed in [20_Known_Limitations.md](20_Known_Limitations.md)) |

The VENTUNO Q has 64 GB of eMMC (Arduino ABX00181 datasheet). The dashboard FILES tab shows the
archive path, the free space, the space used and the retention state (`logger` section of the
snapshot).

**Copying data off the board.** Copy whole day folders and the export files with `scp` or
`rsync` (they are closed or append-only). For the database, either export CSV (§8) or take a
consistent copy with the SQLite online-backup API while the GDS runs (tested):

```python
import sqlite3
from pathlib import Path
src = sqlite3.connect(f"{Path('gds.sqlite').resolve().as_uri()}?mode=ro", uri=True)
dst = sqlite3.connect("gds-copy.sqlite")
src.backup(dst)      # consistent snapshot; the running GDS keeps writing
dst.close(); src.close()
```

A plain file copy of a live `gds.sqlite` without its `gds.sqlite-wal` file can miss the newest
rows; stop the GDS first if you copy the files themselves.

---

## 3 Archive layout

```
<archive>/
  YYYYMMDD/                UTC day folders (created when the first record of the day is written)
    cadu.bin               raw CADUs as received, with ERT and raw RSSI          (§4)
    events.log             human-readable event log                              (§5)
  gds.sqlite               telemetry database (WAL mode) + gds.sqlite-wal, gds.sqlite-shm   (§6)
  exports/                 CSV exports, VGQ1_<product>_<from>_<to>.csv           (§8)
  tdm/                     Tracking Data Messages, VGQ1_<station>_<UTC>.kvn      (§11)
  files/                   CFDP received files ([cfdp] files_dir)                 (§14)
  spec_white_ref.json      white reference ([spectral] white_ref_file)           (§15)
  spec_library.json        spectral library ([spectral] library_file)            (§15)
  sdls_sn.txt              last TC SDLS sequence number used (+ sdls_sn.txt.lock) (§15)
  sdls_tm_replay.txt       last accepted TM (epoch, sn)                          (§15)
```

Folders other than the day folders appear when first needed. In App Lab the SDLS key
(`<App>/data/sdls_key.hex`), the web token (`<App>/data/http_token`) and the editable
`station.toml` sit next to `archive/`, in `<App>/data/`.

---

## 4 Raw CADU files

### 4.1 Ground: `YYYYMMDD/cadu.bin`

One record per received radio unit (E22: every CADU found by the frame synchronizer; SX126x:
every CADU-length packet, including CRC-failed ones offered to Reed-Solomon), appended in
reception order, big-endian:

| Offset | Size | Type | Field |
|---|---|---|---|
| 0 | 8 | i64 | ERT, nanoseconds since 1970-01-01 UTC (station clock at the end of reception) |
| 8 | 2 | i16 | RSSI in dBm as reported by the radio, **before** `[radio] rssi_cal_offset_db`, rounded to 1 dB; 0x7FFF = not available |
| 10 | 2 | u16 | CADU length (236) |
| 12 | 236 | octets | CADU exactly as received: ASM `1A CF FC 1D` (uncorrected) + randomized RS(255,223)-shortened codeblock |

A record is therefore 248 octets. The day is the UTC day at the time of writing. A truncated
last record (power loss) is ignored by the reader.

### 4.2 Spacecraft EGSE: `cadu_YYYYMMDD.bin`

Every CADU the flight computer handed to its radio, written by the EGSE on the UNO Q:
`u64 unix ns | u16 length | CADU` (10-octet header, no RSSI). `read_cadu_file` recognises this
format by the `cadu_` file-name prefix. Comparing the spacecraft's file with the ground's
gives the true end-to-end frame loss of a pass.

### 4.3 Reading CADUs in Python

```python
import sys
sys.path.insert(0, "dssq-groundstation/python")      # the dssq package
from dssq.archive import read_cadu_file
for ert, rssi, cadu in read_cadu_file("archive/20261012/cadu.bin"):
    ...   # ert: float s, rssi: int dBm or None (EGSE files: always None), cadu: 236 octets
```

To decode them, use the replay tool (§13).

---

## 5 Event log: `YYYYMMDD/events.log`

One line per ground event, UTF-8:

```
2026-10-12T01:54:10+00:00 INFO  RADIO    ground E22-400T33D: ch 23 (433.125 MHz) rate 2.4k pwr 0 LBT/CAD False - matches station.toml
2026-10-12T01:54:13+00:00 INFO  S/C EVR  [0x0001] BOOT VGQ-1 FSW 3.0 rst=0 POWER-ON radio=E22-400T33D TM ...
2026-10-12T01:54:13+00:00 WARN  LIMITS   HK.beacon_level = 3 RED
```

Fields: UTC time (second resolution), level (INFO, WARN, ERROR) padded to 5 characters, source
padded to 8, text. The same events are in the `events` table (§6.5). Every message is
explained in [14_Troubleshooting_and_Event_Response.md](14_Troubleshooting_and_Event_Response.md).

**What the uplink leaves in the archive.** The station records what it receives, not what it
transmits: no uplink CLTU is stored in `cadu.bin` or the database. Commands appear in the
`commands` table (§6.5). Each station identification (`DE <callsign> <name>`, plain ASCII,
[07_Operations_Procedures.md](07_Operations_Procedures.md) §2.8) leaves only an event, source
`IDENT`, e.g. `INFO  IDENT    station identification (periodic): DE G4ABC DSS-Q1` (checked with
the software spacecraft). The closing identification sent while the GDS stops leaves no event;
the identification count and last time are live values of the dashboard (`link.station_id`),
not archived. The start-up warning while `[station] callsign` is still `N0CALL` is an `IDENT`
WARN event.

---

## 6 The telemetry database: `gds.sqlite`

### 6.1 Engine

* SQLite 3 in **WAL** journal mode with `synchronous = NORMAL` (checked at open; a file system
  without WAL support keeps the rollback journal, shown as `journal_mode` in the FILES tab). A
  reader never blocks the GDS, and a power loss can lose at most the last commits, never
  corrupt the file.
* The GDS commits every **2 s** (`COMMIT_INTERVAL_S`); a CSV export commits first.
* Writes are best-effort: a full disk stops the recording, never the downlink or commanding
  (counted as `write_errors`, WARN event, YELLOW alarm).
* The free-space guard pauses only the two growing raw products (`cadu.bin` records and
  `rx_stats` rows); frames, packets, parameters, events and commands keep recording (§7.2).
* Always open it **read-only** for analysis (§9): `sqlite3.connect("file:...gds.sqlite?mode=ro", uri=True)`.

Times are UNIX seconds (REAL, UTC). ERT = earth received time at the station; SCET =
spacecraft event time from the SCLK correlation (NULL until the first TIMECORR correlation
of the current spacecraft boot).

### 6.2 `frames` - one row per received codeblock

| Column | Type | Meaning |
|---|---|---|
| `ert` | REAL | Reception time |
| `mcfc` | INT | Master channel frame count (TM; USLP: the frame's VCF count); NULL when the frame did not decode |
| `vcid` | INT | Virtual channel: 0 real-time engineering, 1 real-time science, 2 recorder playback, 3 CFDP files, 7 (TM) / 63 (USLP) OID identification |
| `vcfc` | INT | Virtual channel frame count |
| `fhp` | INT | First header pointer, octets, in the TM convention for both frame formats: 2047 (0x7FF) = no packet starts in this frame, 2046 (0x7FE) = idle data only (OID frames); NULL for a USLP frame whose data stayed encrypted |
| `rs_corrected` | INT | Reed-Solomon symbols corrected (0..16); negative = uncorrectable |
| `rssi` | INT / REAL | RSSI as reported by the radio, dBm, **before** the calibration offset (SX126x in 0.5 dB steps) |
| `snr` | REAL | SX126x: measured packet SNR, dB. E22: the estimate RSSI - noise floor (both calibrated), dB; NULL before the first noise reading |
| `ok` | INT | 1 = the frame passed RS and its FECF (it may still have been rejected by SDLS: see `reason`); 0 = it did not decode |
| `reason` | TEXT | Empty for a good frame; the decoder verdict for a bad one; `SDLS <verdict>` for a frame rejected by TM security; SX126x: `CRC error, payload discarded`, `<n>-octet packet, not a CADU`, or a verdict with ` (LoRa CRC error)` |

### 6.3 `packets` - one row per extracted Space Packet

| Column | Type | Meaning |
|---|---|---|
| `ert` | REAL | Reception time of the frame that completed the packet (decode time for a CFDP-delivered SSR file) |
| `scet` | REAL | Spacecraft event time of the packet's SCLK, NULL without correlation |
| `apid` | INT | Application process identifier (0x010 HK ... 0x030 CFDP) |
| `name` | TEXT | Packet name: HK, RF, EVR, CMDVER, TIMECORR, RFSCAN, MAG, ATT, SPEC, ENV, SPECBURST, CFDP |
| `seq` | INT | 14-bit per-APID sequence count |
| `sclk` | REAL | Spacecraft clock from the CUC secondary header, s since the flight computer booted (coarse + fine / 65536) |
| `partition` | INT | SCLK partition = EGSE boot counter of that boot (from HK `sclk_partition`; for SSR files, from the file name) |
| `vcid` | INT | 0, 1 real time; 2 recorder playback **or** decoded from a CFDP SSR file |
| `raw` | BLOB | The whole packet: 6-octet primary header, 6-octet CUC secondary header, user data |
| `source` | TEXT | `RF` (received in a frame) or `CFDP` (decoded from an SSR file delivered by CFDP) |

Malformed packets and packets whose decoding failed are stored too (with no parameter rows).

### 6.4 `params` - one row per decoded numeric field

| Column | Type | Meaning |
|---|---|---|
| `ert` | REAL | As `packets.ert` |
| `scet` | REAL | As `packets.scet` |
| `packet` | TEXT | Packet name (HK, RF, MAG, ...) |
| `name` | TEXT | Field name as in the dictionary, e.g. `bus_temp`, `ob_b_x`, `spec_560nm` |
| `value` | REAL | Engineering value = raw x scale (or the field's decoder, e.g. the SPEC log code), in the dictionary unit ([05](05_Telemetry_and_Command_Dictionary.md)). Enumerations and bit masks are stored as numbers |
| `limit_state` | TEXT | `OK`, `YELLOW` or `RED` against the ground limits (section 4.7 of [14](14_Troubleshooting_and_Event_Response.md)) |

Not stored as parameters: fields that are "not available" (their sentinel decodes to None),
booleans and texts, and the whole EVR, SPECBURST and CFDP packets (EVR texts are in `events`,
SPECBURST and CFDP stay in `packets.raw`). Packets played back from the recorder (VC2) are
stored as well: they carry the playback ERT but the original SCLK / SCET, so **sort and
de-duplicate science on `scet` (or `sclk` + `partition`), not on `ert`**, when you played the
recorder back.

### 6.5 `events` and `commands`

| Table | Columns | Notes |
|---|---|---|
| `events` | `t` REAL, `level` TEXT, `source` TEXT, `text` TEXT | Every ground event (§5) |
| `commands` | `t` REAL, `id` INT, `text` TEXT, `state` TEXT, `detail` TEXT | One row when a command is queued (`QUEUED`) and one when its CMDVER arrives (`EXECUTED` / `FAILED`); `id` is the GDS command number, restarting at 1 at every GDS start (identify a command by `t` and `id`) |

### 6.6 `rx_stats` - the 1 Hz receiver time series (data logger)

One row per second while the station runs, also during LOS ([`datalog.py`](../dssq-groundstation/python/dssq/datalog.py)). Counter columns
hold the **increment of that interval**, so the rows of a time range sum to its totals.

| Column | Unit | Meaning |
|---|---|---|
| `t` | s (UNIX) | End of the interval |
| `los` | 0/1 | 1 while the station is in LOS |
| `pass_no` | count | Number of AOS since the GDS started (not persistent across restarts) |
| `n_rx` | count | Radio units received in the interval |
| `rssi_dbm` | dBm | Mean calibrated RSSI of the interval's receptions; NULL without a reception |
| `snr_db` | dB | Mean measured SNR (SX126x only) |
| `snr_est_db` | dB | `rssi_dbm` - `noise_dbm` (estimate) |
| `noise_dbm` | dBm | Current calibrated noise floor while it is fresh (younger than the LOS timeout) |
| `margin_db` | dB | `rssi_dbm` - receiver sensitivity of the current rate code (estimate; source per [02](02_Link_Budget_and_RF_Analysis.md)) |
| `freq_err_hz` | Hz | Mean raw frequency error (SX126x only) |
| `fer_10m` | ratio 0..1 | Frame error rate over the last 10 minutes (the link manager's FER) |
| `rs_symbols` | count | RS symbols corrected in the interval |
| `cb_uncorrectable` | count | Uncorrectable codeblocks in the interval |
| `frames_ok` | count | Frames received in the interval |
| `frames_lost` | count | Frames lost (frame-count gaps) in the interval |
| `afc_hz` | Hz | AFC correction in force (SX126x with AFC) |
| `doppler_hz` | Hz | Doppler estimate during a pass (SX126x) |
| `range_rate_m_s` | m/s | Range rate from the Doppler estimate, positive receding (SX126x) |

### 6.7 Indexes

`params(name, ert)`, `params(ert)`, `packets(apid, ert)`, `packets(ert)`, `rx_stats(t)`,
`frames(ert)`, `events(t)`, `commands(t)`. Queries on these columns are fast; anything else is
a table scan. An archive created by an older version gains the missing column, table and
indexes at the first open (a large old archive takes a while once).

---

## 7 Retention, free-space guard and disk budget

### 7.1 Retention (`[gds] archive_keep_days`, `archive_max_gb`)

Checked 5 s after start and then every 60 s:

* **Age:** day folders whose whole UTC day ended more than `archive_keep_days` (180) days ago
  are deleted, and the `rx_stats`, `params` and `frames` rows older than that.
* **Size:** while day folders + database pages in use exceed `archive_max_gb` (16 GB, SI), the
  oldest data day is pruned, one day per check, repeated at once while still over the cap.
* Never the current UTC day, never a folder dated in the future (a clock that jumped back),
  never a symbolic link. Rows go in batches of 2000 per table per second.
* `exports/` CSV files older than `archive_keep_days`, and beyond the newest 200 files, are
  deleted.
* **Not pruned:** the `packets`, `events` and `commands` tables, TDM files, CFDP files and the
  spectral and SDLS files. They are the mission record; remove old ones by hand if needed (stop
  the GDS before deleting database rows).
* The database file does not shrink (no VACUUM while running): freed pages take new rows, so
  the size cap counts the pages in use.
* Each completed prune is one INFO event `retention: ...`.

### 7.2 Free-space guard (`[gds] archive_min_free_mb`)

The free space of the archive's file system is read every 5 s. Below the floor (2048 MB) the
raw CADU records and the `rx_stats` rows pause (WARN event, YELLOW alarm, counted as skipped);
everything else keeps recording. Logging resumes by itself above the floor + 5 % (INFO
event). A dashboard CSV export is refused while logging is paused, and stops if the free
space falls below the floor while it runs (the command-line export does not apply the floor).

### 7.3 How much space (estimate)

Measured unit sizes (this archive code, SQLite, including indexes): `rx_stats` 79 octets per
row (E22 columns; about 6.8 MB per day), `params` about 92 octets per row, `packets` about
the packet length + 75 octets per row, `frames` 51 octets per row, `cadu.bin` 248 octets per
record.

Worked example, labelled estimate: continuous contact for 24 h, E22 at 2.4 kbit/s, CRUISE
cadence without stretching, TM + SDLS AE (frame period 2.5 x 1267 ms = 3.167 s): 27 281 frames
-> `cadu.bin` 6.8 MB and `frames` 1.4 MB; about 8 500 packets with 195 552 parameter rows ->
`params` 18 MB, `packets` about 1.2 MB; `rx_stats` 6.8 MB. Total about 34 MB per day. The
CADU volume is bounded by the 1.5 s minimum frame period: at most 57 600 records = 14.3 MB per
day. With the defaults, the 180-day age limit (about 6 GB at this rate) acts before the 16 GB
cap. Watch the real figures in the FILES tab.

---

## 8 CSV export

### 8.1 Running an export

* **Dashboard:** FILES tab, panel *CSV export*: enter `from` and `to` (UTC, ISO-8601, e.g.
  `2026-10-11T12:00:00Z`, or use LAST 10 MIN / LAST HOUR / LAST 24 H / TODAY), tick the products
  and press EXPORT. The server chooses the file names (there is no path input); the files are
  listed below the panel for download. Exporting needs the commanding token (it is a POST).
* **Command line** (on the board or on a copy of the archive; read-only access):

```bash
cd dssq-groundstation/python
python3 -m dssq.export --from 2026-10-11T12:00:00Z --to 2026-10-11T13:00:00Z \
    --what rf,params,events --archive /home/arduino/ArduinoApps/dssq-groundstation/data/archive
#   --what   any of rf, params, events, frames, packets, commands, or all (default)
#   --out    output directory (default <archive>/exports, listed by the FILES tab)
#   --config FILE instead of --archive: its [gds] archive_dir, a relative one taken from the
#            working directory (no App path rules), so on the board prefer --archive
```

Times: ISO-8601 (a value without a zone is UTC; a date alone means midnight) or UNIX seconds.
Write dates with dashes: `20261011` would be read as UNIX seconds. The range is
[from, to). One export runs at a time (BUSY otherwise).

### 8.2 Files and format

* One file per product: `VGQ1_<product>_<from>_<to>.csv`, times as `YYYYMMDDTHHMMSSZ`, e.g.
  `VGQ1_rf_20261012T000000Z_20261013T000000Z.csv`. Written as `.tmp` and renamed when
  complete.
* UTF-8 without byte-order mark, RFC 4180 quoting, CR LF line ends, one header row whose names
  carry the unit in brackets (`[dBm]`, `[count]`, `[ratio]` for 0..1, `[0/1]` for a flag).
* Every row starts with `utc [ISO-8601]` (millisecond resolution, `Z`) and `unix_time [s]`.
  An empty field is a value the station did not have.
* Numbers are written unchanged (shortest exact representation). Free-text fields that a
  spreadsheet would treat as a formula (first character `=`, `+`, `-`, `@`, tab or CR) are
  prefixed with an apostrophe: event texts can come from the spacecraft.

| Product | Table | Columns after `utc`, `unix_time` |
|---|---|---|
| `rf` | `rx_stats` | `los [0/1]`, `pass [count]`, `receptions [count]`, `rssi [dBm]`, `snr [dB]`, `snr_est [dB]`, `noise_floor [dBm]`, `link_margin [dB]`, `freq_err [Hz]`, `fer_10m [ratio]`, `rs_symbols_corrected [count]`, `codeblocks_uncorrectable [count]`, `frames_ok [count]`, `frames_lost [count]`, `afc_correction [Hz]`, `doppler [Hz]`, `range_rate [m/s]` |
| `params` | `params` (time = ERT) | `scet_utc [ISO-8601]`, `scet_unix [s]`, `packet`, `parameter`, `value [unit column]`, `unit`, `limit_state` |
| `events` | `events` | `level`, `source`, `text` |
| `frames` | `frames` (time = ERT) | `mcfc [count]`, `vcid`, `vcfc [count]`, `first_header_pointer [octets]`, `rs_corrected [symbols]`, `rssi_raw [dBm]`, `snr [dB]`, `ok [0/1]`, `reason` |
| `packets` | `packets` (time = ERT) | `scet_utc [ISO-8601]`, `scet_unix [s]`, `apid`, `packet`, `seq [count]`, `sclk [s]`, `partition`, `vcid`, `source`, `length [octets]`, `raw [hex]` |
| `commands` | `commands` | `id`, `command`, `state`, `detail` |

Example (first lines of a real `rf` export):

```
utc [ISO-8601],unix_time [s],los [0/1],pass [count],receptions [count],rssi [dBm],snr [dB],snr_est [dB],noise_floor [dBm],link_margin [dB],freq_err [Hz],fer_10m [ratio],rs_symbols_corrected [count],codeblocks_uncorrectable [count],frames_ok [count],frames_lost [count],afc_correction [Hz],doppler [Hz],range_rate [m/s]
2026-10-12T01:54:10.720Z,1791770050.7200806,1,0,0,,,,,,,,0,0,0,0,,,
```

### 8.3 Opening CSV files in a spreadsheet

* LibreOffice Calc: open the file, choose character set **UTF-8**, separator **comma**. Use
  the `unix_time [s]` column for calculations; as a date: `=A2/86400+DATE(1970;1;1)` (UTC).
* Excel: Data -> From Text/CSV, file origin **65001: Unicode (UTF-8)**, delimiter comma. A date
  from `unix_time [s]`: `=B2/86400+DATE(1970,1,1)`, formatted as date and time (UTC).
* A text cell that starts with an apostrophe was a formula-like text (see above); the
  apostrophe is not part of the data.
* `params` is "long" (one row per value). Use a pivot table (rows `utc`, columns `parameter`,
  values `value`) to get one column per field.

---

## 9 Loading the data in Python

All examples below were run as written (Python 3.13; pandas 3.0 for the pandas example).

### 9.1 Standard library (`sqlite3`, read-only)

```python
import sqlite3
from pathlib import Path
db = Path("archive") / "gds.sqlite"
con = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)   # read-only: never blocks the GDS

# 1 Hz receiver statistics of the last hour of data
rows = con.execute(
    "SELECT datetime(t, 'unixepoch') AS utc, rssi_dbm, snr_est_db, noise_dbm, margin_db, frames_ok, frames_lost "
    "FROM rx_stats WHERE t >= (SELECT MAX(t) FROM rx_stats) - 3600 ORDER BY t").fetchall()

# one telemetry parameter as a time series (engineering units)
bus_temp = con.execute("SELECT ert, value FROM params WHERE packet = 'HK' AND name = 'bus_temp' "
                       "ORDER BY ert").fetchall()

# frames per virtual channel: count, decoded, mean raw RSSI
per_vc = con.execute("SELECT vcid, COUNT(*), SUM(ok), AVG(rssi) FROM frames GROUP BY vcid").fetchall()

# warnings
warn = con.execute("SELECT datetime(t, 'unixepoch'), source, text FROM events WHERE level = 'WARN' "
                   "ORDER BY t").fetchall()
con.close()
```

### 9.2 pandas

```python
import sqlite3
from pathlib import Path
import pandas as pd
db = Path("archive") / "gds.sqlite"
con = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)

rx = pd.read_sql_query("SELECT * FROM rx_stats ORDER BY t", con)
rx["utc"] = pd.to_datetime(rx["t"], unit="s", utc=True)
per_minute = rx.set_index("utc")[["rssi_dbm", "snr_est_db", "margin_db"]].resample("1min").mean()

hk = pd.read_sql_query("SELECT ert, name, value FROM params WHERE packet = 'HK'", con)
wide = hk.pivot_table(index="ert", columns="name", values="value")   # one column per HK field
wide.index = pd.to_datetime(wide.index, unit="s", utc=True)
con.close()

# CSV exports load directly
df = pd.read_csv("archive/exports/VGQ1_rf_20261012T000000Z_20261013T000000Z.csv",
                 parse_dates=["utc [ISO-8601]"])
```

### 9.3 Decoding a stored packet again

`packets.raw` holds the complete packet; the ground's own decoder turns it into engineering
values (the `dssq` package from `dssq-groundstation/python`):

```python
import sqlite3, sys
from pathlib import Path
sys.path.insert(0, "dssq-groundstation/python")
from dssq.ccsds.packets import SpacePacket
from dssq.decom import Decommutator
con = sqlite3.connect(f"{(Path('archive') / 'gds.sqlite').resolve().as_uri()}?mode=ro", uri=True)
dec = Decommutator()
for ert, raw in con.execute("SELECT ert, raw FROM packets WHERE name = 'RF' ORDER BY ert LIMIT 1"):
    p = SpacePacket.parse(bytes(raw))
    values = dec.decode(p)             # {"radio_model": 1, "frame_period_ms": 5068, ...}
```

---

## 10 Spacecraft EGSE products (UNO Q)

Written by the EGSE in the spacecraft App's data folder,
`/home/arduino/ArduinoApps/vgq1-spacecraft/data/` (`<App>/data`). UTC day files:

| File | Format | Content |
|---|---|---|
| `cadu_YYYYMMDD.bin` | `u64 unix ns`, `u16 length`, CADU (§4.2) | Every CADU handed to the radio |
| `evr_YYYYMMDD.jsonl` | JSON Lines: `{"utc": "2026-10-12T01:54:13+00:00", "id": "0x0001", "severity": "INFO", "text": "..."}` | Every EVR the MCU notified (also those filtered from the downlink, [14](14_Troubleshooting_and_Event_Response.md) §1) |
| `sc_status_YYYYMMDD.jsonl` | JSON Lines: `{"utc": ..., "kind": "core" or "x", ...}` | Flight status: `core` every second (`mode`, `sclk`, `part`, `epoch`, `tm_state`, `tm_sn`, `frames`, `oid`, `mcfc`, `period_ms`, `busy_ms`, `duty_pct`, `model`, `fr`, `ch`, `code`, `pwr`, `lbt`, `tx`, `inhibit`, `hib`, `beacon`); `x` every 5 s (`ul_rssi`, `ul_snr_cdb`, `noise`, `farm`, `vr`, `acc`, `rej`, `fdir`, `sns`, `vc0`..`vc3`, `ssr_pm`, `bus_c`, `rf_fault`, `tc_svc`, `tm_svc`, `cfdp`, `cfdp_seq`, `cfdp_pm`, `seq`, `tc_rescued`, `evr_nd`, `cadu_nd`, `emit_ref`, `relay_ref`, `stretch`) |
| `boot_count.txt` | Decimal integer | Last SCLK partition = SDLS TM epoch issued. **Security-critical**: never lower it with the same key (07 §10.5) |
| `sdls_last_sn.txt` | Decimal integer (int32 of the u32 SN) | Last TC sequence number the MCU accepted; restored to the MCU after every boot |
| `hardline_queue.txt` | One CLTU per line, hex | Hardline (umbilical) commands to send (07 §2.6) |

The EGSE files of source 1 (newest `evr_*.jsonl`) and 2 (newest `cadu_*.bin`) can be fetched
over the radio with `CFDPPUT EVRLOG` / `CFDPPUT CADU` (07 §9).

---

## 11 CCSDS Tracking Data Message (TDM)

**What it is:** the standard file format (CCSDS 503.0-B-2 with Technical Corrigendum 1, KVN
syntax) in which a station reports what it measured about a pass, for orbit determination
or link analysis tools.

* **When:** at every LOS when `[tdm] auto_write = true` (also at shutdown), and on request
  (`TDM WRITE` or WRITE TDM NOW in FILES). A request rewrites the current pass's file.
* **Where:** `<archive>/tdm/VGQ1_<station>_<UTC of the first record>.kvn`, e.g.
  `VGQ1_DSS-Q1_20261012T015413Z.kvn` (station name reduced to letters, digits and `-`). The
  FILES tab lists the 20 newest; `/api/tdm/<name>` downloads one.
* **Records:** one per received frame, timetag = UTC at the end of reception minus
  `[radio] radio_latency_s`; a record repeating the previous millisecond timetag is dropped; at
  most 100 000 records per pass.
* **Header:** `CCSDS_TDM_VERS = 2.0`, COMMENT lines stating the conventions,
  `CREATION_DATE`, `ORIGINATOR` (station), `MESSAGE_ID` (file name without `.kvn`).
* **Metadata:** `TIME_SYSTEM = UTC`, `PARTICIPANT_1` = station, `PARTICIPANT_2` = spacecraft,
  `MODE = SEQUENTIAL`, `PATH = 2,1` (one-way downlink, Blue Book Annex E convention),
  `TRANSMIT_BAND = RECEIVE_BAND = UHF` (300 MHz..3 GHz), `DATA_QUALITY = RAW`; with frequency
  data also `FREQ_OFFSET` = nominal channel frequency (Hz). A new segment starts when the
  nominal frequency changes.
* **Data keywords:**

| Keyword | Unit | E22 | SX126x | Source |
|---|---|---|---|---|
| `CARRIER_POWER` | dBW | yes | yes | Calibrated RSSI (`rssi_cal_offset_db` applied) - 30 dB, at the receiver input, not corrected for antenna gain or line loss |
| `PC_N0` | dB-Hz | no | yes | Packet SNR + 10 log10(noise bandwidth = the profile bandwidth) |
| `RECEIVE_FREQ_1` | Hz | no | yes | Received carrier minus `FREQ_OFFSET`: AFC correction + measured residual, against the station's frequency reference |
| `TRANSMIT_FREQ_2` | Hz | no | yes | Spacecraft nominal frequency, once per segment |
| `DOPPLER_INSTANTANEOUS` | km/s | no | only with `[radio] freq_offset_calibrated = true` | Range rate = -c (total offset - `freq_offset_cal_hz`) / f, positive receding |

The E22 modules report RSSI only, so an E22 TDM carries `CARRIER_POWER` only (a COMMENT says
so). Example (a real file from a simulated E22 pass, shortened):

```
CCSDS_TDM_VERS = 2.0
COMMENT One-way downlink tracking data from the DSS-Q ground data system
...
META_START
COMMENT Station receiver: E22-400T33D
DATA_TYPES = CARRIER_POWER
TIME_SYSTEM = UTC
START_TIME = 2026-10-12T01:54:13.141
STOP_TIME = 2026-10-12T01:55:24.250
PARTICIPANT_1 = DSS-Q1
PARTICIPANT_2 = VGQ-1
MODE = SEQUENTIAL
PATH = 2,1
TRANSMIT_BAND = UHF
RECEIVE_BAND = UHF
DATA_QUALITY = RAW
META_STOP
DATA_START
CARRIER_POWER = 2026-10-12T01:54:13.141 -146.00
...
DATA_STOP
```

**Command line** (`cd dssq-groundstation/python`):

```bash
python3 -m dssq.products tdm validate FILE.kvn [--lenient]     # strict 503.0-B-2 check (lenient: third-party files)
python3 -m dssq.products tdm build RECORDS.csv --station DSS-Q1 [-o FILE | --archive DIR] \
        [--freq-offset-cal-hz HZ] [--radio-model NAME] [--originator NAME]
#   RECORDS.csv header: t_utc,rx_power_dbm,snr_db,freq_offset_hz,afc_correction_hz,nominal_freq_hz,noise_bw_hz[,integration_s]
```

TDM files are created with owner-only permissions (mode 0600); download them through the
dashboard or copy them as the board user.

---

## 12 XTCE mission database

The telemetry and command dictionary as a CCSDS 660.0-B-2 / OMG XTCE 1.2 document (namespace
`http://www.omg.org/spec/XTCE/20180204`), generated from
[`dssq/dictionary.py`](../dssq-groundstation/python/dssq/dictionary.py) - the same dictionary
that decodes the telemetry, so it cannot drift from the ground software.

* **Get it:** FILES tab, DOWNLOAD XTCE (`/api/xtce`, file `vgq1.xtce.xml`), or
  `python3 -m dssq.products xtce -o vgq1.xtce.xml [--version V] [--date D]` (about 200 kB).
* **Content:** one SpaceSystem `VGQ-1`: an abstract CCSDS packet container (primary header and
  CUC secondary header) and one child per telemetry APID restricted on the APID, big-endian
  integer / IEEE-754 encodings, polynomial calibrators for the scale factors, units, alarm
  ranges from the ground limits; the variable-length EVR, RFSCAN, SPECBURST and CFDP packets as
  described containers; every telecommand as a MetaCommand of the abstract TC packet (APID
  0x0C0) with the opcode as a fixed argument, argument ranges, and the hazardous commands
  marked critical.
* **Use:** import into a mission control system (YAMCS, OpenMCT, ...) to decode the packets
  forwarded with `[interop] tm_packets_udp`. Loading it into a specific MCS is a VERIFY item
  ([08_Verification_and_Test.md](08_Verification_and_Test.md)).

---

## 13 Replay

`dssq.replay` runs recorded CADUs through the same downlink chain as the live GDS: ASM check,
de-randomizer, Reed-Solomon, TM / USLP frame parser, SDLS TM ProcessSecurity, packet
extraction, decommutation, and optionally the CFDP receiver.

```bash
cd /home/arduino/ArduinoApps/dssq-groundstation/python                 # <App>/python on the VENTUNO Q
python3 -m dssq.replay --app-data ../data --cadu-file ../data/archive/20261012/cadu.bin --packets --values
python3 -m dssq.replay --app-data ../data --cadu-file cadu_20261012.bin       # spacecraft EGSE file, copied here
python3 -m dssq.replay --cadu-file vectors.bin --raw                          # plain 236-octet CADUs
python3 -m dssq.replay --app-data ../data --cadu-file ... --cfdp-dir /tmp/files   # rebuild CFDP files
```

`--app-data ../data` gives the replay the station's own configuration, found as the GDS, host
mode and `dssq.cltu` find it ([`config.py`](../dssq-groundstation/python/dssq/config.py)
`load_tool_config()`): `<App>/data/station.toml` when it exists (else the packaged
`config/station.toml`), and the key `<App>/data/sdls_key.hex` unless that `station.toml` gives
an **absolute** `key_file`. Finding them only reads: nothing is created or copied in
`<App>/data`. `--config FILE` names another `station.toml` instead (the key rule of
`--app-data` still applies when both are given); without `--app-data` a relative `key_file` is
looked up under the working directory.

The station archive file is `archive/<day>/cadu.bin` (§4.1). `cadu_YYYYMMDD.bin` is the
spacecraft EGSE log (§4.2): copy it from the UNO Q's `<App>/data/`. Both ends hold the same
key (one key written to two files by [`tools/gen_sdls_key.py`](../tools/gen_sdls_key.py)), so
`--app-data ../data` opens its AE frames too. `read_cadu_file` recognises the EGSE format by the `cadu_` in the file name:
keep that name when you copy it. The command exits with 1 and an `error:` line when a file or
the configuration cannot be read, 2 for a wrong command line, else 0.

* **SDLS must be configured as when the data were recorded.** The replay uses `[sdls]
  tm_service`, `key_file` and `accept_null` of the configuration it found (without
  `--app-data` and `--config`: the packaged `config/station.toml`, AE, key file relative to the
  working directory). A recording made without SDLS replayed with an AE configuration shows
  every data frame as `BAD_SPI`; replay it with `tm_service = "NONE"`. An AE recording needs
  the same key; without it only null-SA and OID frames decode and the others are counted as
  `NO KEY`. (Both cases were reproduced.)
* The replay window starts empty, so a frame repeated inside the recording counts as REPLAY.
* Output: per packet `ERT ... RSSI ... VC0 HK seq ... SCLK ...` (`--packets`) and the decoded
  values (`--values`), then a summary: CADUs, ASM bit errors, RS symbols corrected, frames
  lost, frames per type, SDLS verdicts, packets per name, decode errors, OID identification
  text, per-VC extractor statistics and the CFDP outcome.
* Files larger than 1 GiB are refused (memory bound).

---

## 14 CFDP received files

Files sent by the spacecraft with `CFDPPUT` land in `<archive>/files/` (`[cfdp] files_dir`):

| Outcome | Files | Meaning |
|---|---|---|
| COMPLETE | `<name>` | Every octet arrived and the modular checksum matched |
| INCOMPLETE | `<name>.partial` (gaps zero-filled) + `<name>.json` (missing ranges, `[start, end)` octets) | EOF arrived with gaps; late File Data within `check_s` (60 s) can still complete it |
| CHECKSUM_FAILURE | `.partial` + `.json` | All octets present, checksum wrong |
| CANCELLED | `.partial` + `.json` | EOF with a non-zero condition (15 = cancel request) |
| ABANDONED | `.partial` + `.json` | No PDU for `inactivity_s` (600 s), file larger than `max_file_mib`, or more than 16 open transfers |

An existing file is never overwritten: a name already taken gets the transaction id inserted
(`<stem>.<source>-<seq><suffix>`). A missing Metadata PDU gives the name
`cfdp_<source>_<seq>.bin`. File names from the spacecraft: `ssr_p<partition>_<sclk>.bin` (the
recorder contents: Space Packets back to back, each self-delimiting) and, from the EGSE, the
newest `evr_YYYYMMDD.jsonl` or `cadu_YYYYMMDD.bin`. A COMPLETE `ssr_p...bin` is also decoded
into the database automatically (packets with `source = 'CFDP'`, `vcid = 2`) and reported by
the event `SSR file <name>: <n> packets archived (partition <p>)`. The FILES tab lists the files
with their state (`/api/files`, `/api/files/<name>`).

---

## 15 Other files in the archive

| File | Content | Notes |
|---|---|---|
| `spec_white_ref.json` | The white reference captured with `SPECREF` (time, mode, lamps, exposure, spectrum and uncertainty per channel) | Reloaded at start (a corrupt file is ignored with a WARN event); `SPECREF CLEAR` deletes it |
| `spec_library.json` | Named spectra from `SPECLIB SAVE` (normalised spectrum, reflectance when available), at most 64 | Shown as best matches in the SPECTROMETER tab |
| `sdls_sn.txt` | Last TC SDLS sequence number used (decimal), written atomically **before** each protected frame; `sdls_sn.txt.lock` is its inter-process lock (shared with the hardline tool `dssq.cltu`) | Never lower it by hand (GCM nonce reuse). A missing or corrupt file (with a key and TC AUTH or AE) holds the uplink until an HK reports the spacecraft's `sdls_last_sn` (a protected HK, or any HK at a TM NONE station; never one made within 120 s of a flight boot); the counter then continues on the time floor, 4 numbers per second since 2026-01-01 ([17](17_Security_Model.md) §7.6). The simulator never reads or writes this file |
| `sdls_tm_replay.txt` | Last accepted TM `(epoch, sn)` as `"<epoch> <sn>"`, written after every protected frame | Reset only with `SDLS RESETREPLAY` (07 §10.6) |

The SDLS files and the spacecraft's `boot_count.txt` / `sdls_last_sn.txt` belong to the key:
back them up together with it ([07](07_Operations_Procedures.md) §10.5,
[17_Security_Model.md](17_Security_Model.md)).
