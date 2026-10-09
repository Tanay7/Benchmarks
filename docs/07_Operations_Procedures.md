# 07 - Operations Procedures

First-time installation is in [00 Beginner Setup Guide](00_Beginner_Setup_Guide.md). This
document covers running the system. Commands are typed on CardKB #1 or in the web console
(identical syntax); `HELP` and `HELP <command>` list them, the full dictionary is
[05](05_Telemetry_and_Command_Dictionary.md).

## 1 Configuration files

| File | Where | What |
|---|---|---|
| `spacecraft/vgq1_flight/sketch/src/config.h` | UNO Q app | radio channel/air rate (must match the ground), pins, I2C addresses, callsign, local geomagnetic inclination/declination, magnetometer scale, SSR size, thermal limits |
| `spacecraft/vgq1_flight/sketch/src/sdls_key.h` | UNO Q app (generated, never committed) | uplink authentication key + SPI |
| `ground/config/station.toml` | VENTUNO Q | station/spacecraft antennas and distance, radio port and settings, allowed channels, RSSI calibration, GDS web server and token, station I/O + RCU, SDLS, link manager, interop UDP links, magnetometer boom distances, COP-1 timers, two-person rule |
| `ground/config/sdls_key.hex` | VENTUNO Q (generated, never committed) | same key as the spacecraft |

After changing `config.h`, press Run in App Lab (rebuild + flash). After changing
`station.toml`, `sudo systemctl restart dssq-gds`.

## 2 Station start-up checklist

1. Antennas connected on both ends (never transmit without a load).
2. Ground: E22 powered (12 V), VENTUNO Q booted, `systemctl status dssq-gds` = active.
3. Web dashboard reachable; header shows `LOS` and `SEARCH` (normal before the spacecraft
   transmits); EVENTS shows `DSS-Q ground station started` and, with the RCU,
   `ground E22: ch 23 ... matches station.toml`.
4. Spacecraft: power on; App Lab console shows `E22 config verified`, the instruments line and
   SSR capacity.
5. Within one frame period: `AOS`, `LOCK`, AOS lamp on, frames counting, no uncorrectable
   codeblocks.

## 3 Routine commanding

| Goal | Type |
|---|---|
| Link check | `NOOP` |
| Round-trip time | `PING 1` (history shows the radiate-to-done time) |
| Science on | `MODE CRUISE` (or `MODE ENCOUNTER` for high cadence) |
| Change one packet's rate | `PKTRATE 0x020 10` (MAG every 10 s), `PKTRATE 0x022 0` (SPEC off) |
| Spectrometer set-up | `SPECCFG <gain 0-3> <cycles 1-255> <lamps 0-7>`, e.g. `SPECCFG 3 100 0` (64x, 280 ms, passive) |
| Magnetometer resolution | `MAGCC 400` (higher = less noise, slower; 50-400) |
| Instruments on/off | `SENSORS <mask>` - bits 0 Nicla ME, 7 Nicla Env, 11 AS7265X, 12 RM3100 OB, 13 RM3100 IB (`SENSORS 0xFFFF` = all) |
| Event verbosity | `EVRLEVEL 0` (all) ... `EVRLEVEL 3` (errors only) |
| Time correlation sample now | `TIMECORR` |
| Recorder playback | `PLAYBACK START` (VC2), `PLAYBACK STOP` |

Hazardous commands (`AIRRATE`, `CHANNEL`, `TXINHIBIT`, `HIBERNATE`, `CMDLOSS`, `FDIRMASK`,
`SSRCLEAR`, `REBOOT 0xB007`, and the `LINKRATE`/`LINKCHAN` directives) show
`HAZARDOUS ... confirm id N`: press Enter on an empty line (CardKB) or CONFIRM (web); with the
two-person rule a second operator presses `Y` on CardKB #2 or AUTH. Confirmations expire after
120 s.

## 4 Link procedures

### 4.1 Change air rate or channel (both ends together)

Use the ground directives, never the raw commands: `LINKRATE 3` (4.8k) or `LINKCHAN 21`. They
need the Radio Control Unit. The dashboard's "Coordinated change" row shows
WAIT_EXEC -> WAIT_SWITCH -> SWITCHED -> COMPLETE. If anything fails, both ends revert by
themselves after `revert_s` (default 180 s). `LINKCHAN` only accepts `allowed_channels`.

### 4.2 Find a quiet channel

`GSCAN 20 24` (ground E22 measures the ambient noise on channels 20-24; a few seconds) and
`RFSCAN 20 24` (the spacecraft does the same at its end; its downlink pauses for the survey).
Both appear in the RECEIVER tab's spectrum chart. Then `LINKCHAN <quietest allowed>`.

### 4.3 Adaptive link manager

`LINKMGR ADVISE` (default) shows a recommendation; `LINKMGR AUTO` executes it (air-rate steps
only - the T37S has no power levels): slower when the 10-minute 10th-percentile margin falls
below target - 3 dB or FER > 10 %, faster when it exceeds target + 9 dB with FER < 1 % and the
predicted margin at the faster rate stays >= target + 3 dB. At most one change per `holdoff_s`.

### 4.4 Hibernate and wake

`HIBERNATE 600`: the spacecraft listens in Wake-on-Radio mode (2 s cycle) and sends one HK beacon
every 600 s. To wake: `WAKE` (needs the RCU) - the station transmits `BD MODE SAFE` with a
wake-up preamble. Any authenticated uplink also wakes it; command loss wakes it automatically.

### 4.5 Silence the transmitter

`TXINHIBIT 300` stops the downlink for 5 minutes; it re-enables itself.

## 5 Contingency procedures

| Situation | Indication | Action |
|---|---|---|
| FARM lockout | FDIR FARM_LOCKOUT, CLCW lockout, EVR 0x0205 | `UNLOCK`; if sequence numbers drift, `SETVR 0` |
| Commands time out | history TIMEOUT/SUSPENDED, two-way table shows no uplink | check uplink RSSI and antennas; try `BD NOOP` |
| Command loss fired | EVR 0x0601, mode SAFE | normal: the spacecraft returned to the default link; resume with `MODE CRUISE` |
| Radio change reverted | EVR 0x0602, FDIR RATE_REVERT | investigate the margin before retrying |
| Spacecraft radio over-temperature | EVR 0x0313/0x0604, FDIR PA_OVERTEMP + TX_INHIBIT | wait for the 5-minute cool-down; improve airflow; consider `MODE SAFE` |
| Spacecraft radio under-voltage | EVR 0x0311, FDIR LOW_BUS_V | battery low or wiring too thin for 1.3 A |
| Station radio fault | EVENTS: `ground E22 reports ...` | check the station 12 V supply and cooling |
| Instrument lost | FDIR SENSOR_LOST, HK `sensor_health` bit cleared | check wiring/power; it recovers automatically when the instrument answers again |
| SDLS rejections | HK `sdls_auth_fail` rising, EVR 0x0115 | someone else is transmitting, or the keys differ (re-run `gen_sdls_key.py`, install both files) |
| GDS stopped | ALARM lamp 10 Hz | `sudo systemctl restart dssq-gds`; `journalctl -u dssq-gds` |

## 6 Bench (umbilical) commanding

Without RF, a CLTU can be injected through the UNO Q's EGSE:

```bash
cd ~/Benchmarks/ground
.venv/bin/python -m dssq.cltu "MODE CRUISE"        # prints one hex line (BD frame, SDLS if enabled)
# on the UNO Q: append that line to ~/ArduinoApps/vgq1-flight/python/egse_data/hardline_queue.txt
```

The EGSE passes it to the flight computer, which processes it exactly like an RF uplink.

## 7 Data archive and replay

| Data | Location |
|---|---|
| Raw CADUs as received (+ ERT, RSSI) | `ground/archive/YYYYMMDD/cadu.bin` |
| Frames, packets, parameters, events, commands | `ground/archive/gds.sqlite` |
| Human-readable event log | `ground/archive/YYYYMMDD/events.log` |
| Every CADU the spacecraft transmitted | UNO Q `egse_data/cadu_YYYYMMDD.bin` |
| Transmitter status / EVRs | UNO Q `egse_data/sc_status_*.jsonl`, `evr_*.jsonl` |

Replay any archive through the full decoder chain:

```bash
.venv/bin/python -m dssq.replay --cadu-file archive/20261009/cadu.bin --packets --values
```

Comparing the spacecraft's and the ground's CADU files gives the true end-to-end frame loss.

## 8 Key management (SDLS)

* Generate: `python3 tools/gen_sdls_key.py` (refuses to overwrite; `--force` to rotate).
* Install the two files on both ends at the same time; the GDS and the spacecraft keep their
  sequence numbers, so rotating the key does not need an SN reset.
* Never commit or share the key files (they are git-ignored).

## 9 Monitoring integrations

* Prometheus: scrape `http://<ventuno>:8080/metrics` (every receiver statistic and telemetry
  parameter as gauges) - e.g. for Grafana long-term plots.
* YAMCS / OpenC3: set `[interop] tm_frames_udp` / `tm_packets_udp` to forward decoded TM frames
  or packets, and `tc_frames_udp_listen` to accept TC frames that the GDS then wraps (SDLS,
  CLTU) and radiates.
