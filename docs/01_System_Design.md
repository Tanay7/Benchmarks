# 01 - System Design and Concept of Operations

## 1 Purpose

VGQ-1 / DSS-Q1 is a working, end-to-end model of a deep-space telemetry and telecommand
system built from hobby hardware. It does not imitate one historic probe: it applies the
current CCSDS Blue Books, a modern ground data system and the fault-protection habits of real
missions to a 433 MHz LoRa link, and it is engineered for the longest range the hardware allows
at a useful data rate.

| Element | Real mission analogue | Here |
|---|---|---|
| Spacecraft | flight computer, instruments, transponder | UNO Q (STM32U585 flight software + Linux EGSE), 4 instruments, E22-400T37S |
| Ground antenna | DSN 34 m / 70 m | 433 MHz antenna (Yagi recommended) + E22-400T37S |
| Ground data system | DSN telemetry / command processors, MCS | VENTUNO Q Linux: `dssq` GDS + web dashboard |
| Operator console | command keyboards, two-person rule | CardKB #1 / #2 + web console |
| EGSE / umbilical | test racks before launch | UNO Q Linux EGSE: CADU recorder, hardline commanding |

## 2 Concept of operations

1. **Boot.** The flight computer comes up in SAFE (as real spacecraft do), re-asserts its radio
   configuration from flight constants (volatile write + read-back), discovers its instruments
   and starts transmitting HK/RF at a low cadence. The EGSE gives it its SCLK partition (boot
   count) and restores the uplink anti-replay counter.
2. **Acquisition.** The GDS frame synchronizer locks on the ASM, decodes, and declares AOS. Every
   CADU's arrival opens the uplink window.
3. **Commanding.** Operators type commands (CardKB or web). The GDS validates them against the
   dictionary, asks for confirmation on hazardous ones (optionally a second operator), wraps them
   in SDLS-authenticated COP-1 frames and CLTUs, and follows each through ACCEPTED (CLCW) to
   EXECUTED (CMDVER).
4. **Science.** In CRUISE/ENCOUNTER the spacecraft samples its instruments in transmitter-off
   windows, multiplexes engineering and science on separate virtual channels and records
   everything in its solid-state recorder for later playback.
5. **Link management.** The GDS measures margin continuously and (ADVISE/AUTO) recommends or
   performs coordinated air-rate changes that always revert safely if the link is lost.
6. **Contingencies.** Command loss (24 h without a valid uplink) returns the spacecraft to SAFE on
   the mission-default channel and rate; radio over-temperature silences the transmitter;
   `HIBERNATE` puts the radio into Wake-on-Radio listening with periodic beacons; `WAKE` revives it.

## 3 Architecture

### 3.1 Spacecraft (UNO Q)

```
 STM32U585 (Zephyr, Arduino core) - flight software, sketch.ino -> Flight
   ├─ CCS  : CLTU decode (BCH SEC), TC frame parse, SDLS verify, FARM-1, command dispatch
   ├─ FDS  : packet generation (mode cadence), VC multiplexer, SSR (heap, up to 256 KiB)
   ├─ AACS : Nicla Sense ME fusion + TRIAD (gravity + field), magnetometer statistics
   ├─ Telecom: E22 TX/RX state machines, duty-cycle control, coordinated changes, RF survey, WOR
   ├─ FDIR : command loss, radio self-reports, bus temperature, I2C recovery, congestion, COP-1
   └─ status: 13x8 LED matrix
 QRB2210 Linux (Debian, App Lab container) - EGSE python/main.py
   └─ CADU archive, boot counter/partition, SDLS SN persistence, hardline CLTU queue, status board
```

Efficiency choices driven by the hardware (verified against the UNO Q architecture documented in
the user's LINPACK study): the STM32U585 FPU is single precision only (FP64 is software, ~17x
slower), so all flight numerics are FP32 (Welford statistics instead of sum-of-squares); the
Zephyr loader gives the sketch's heap all remaining SRAM, so the SSR is allocated at boot as the
largest power of two up to 256 KiB that leaves a 32 KiB reserve; Bridge services use
`provide_safe` (loop thread) because plain `provide` runs on a 500-octet stack.

### 3.2 Ground station (VENTUNO Q)

```
 QCS8275 Linux (Ubuntu 24.04) - python -m dssq.gds (systemd service)
   ├─ framesync -> RS decode -> TM frames -> packet extraction -> decommutation -> limits/anomaly
   ├─ receiver statistics (1 min / 10 min / pass), link budget, link manager
   ├─ FOP-1 + SDLS sender + CLTU encoder -> uplink window
   ├─ archive: raw CADUs (+ERT, RSSI), SQLite (frames, packets, parameters, events, commands)
   ├─ web dashboard (stdlib HTTP), Prometheus /metrics, YAMCS/OpenC3-compatible UDP links
   └─ Radio manager (via RCU): verify config, RF survey, WOR wake, coordinated changes
 STM32H5F5 - App ground/station_io
   └─ CardKB #1/#2, AOS/ALARM/UPLINK lamps + buzzer, Radio Control Unit (E22 M0/M1/AUX)
```

The web dashboard is the only display; the MCU's lamps signal AOS/alarm/uplink and a fast-
flashing ALARM lamp means the GDS itself has stopped.

## 4 Flight-software modes

| Mode | Purpose | Telemetry | Frame period (x busy time) |
|---|---|---|---|
| BOOT | power-on | HK, RF every 10 s | 4.0 |
| SAFE | after boot, command loss, PA over-temperature | HK 30 s, RF 60 s, TIMECORR 300 s | 4.0 |
| CRUISE | routine science | + MAG 30 s, ATT 60 s, SPEC 300 s, ENV 120 s | 2.5 |
| ENCOUNTER | intensive science | MAG 4 s, ATT 8 s, SPEC/ENV 30 s | 2.0 |
| TEST | bench | like ENCOUNTER, HK/RF 10 s | 2.0 |

Every period is bounded by the 50 % transmit duty ceiling and a 1.5 s minimum. Full cadence
table: [05 Dictionary](05_Telemetry_and_Command_Dictionary.md) section 2.

## 5 Fault detection, isolation and recovery (FDIR)

| Monitor | Detection | Response | Flag |
|---|---|---|---|
| Command loss | no valid TC for `CMDLOSS` s (default 86 400) | SAFE, mission-default channel/rate, exit hibernation | CMD_LOSS |
| Radio config | volatile write read-back mismatch | flag; retried on every reconfiguration | RADIO_CFG |
| Radio AUX | no AUX completion within 30 s | flag + EVR | AUX_TIMEOUT |
| Radio self-report | `FF FF FF 01..04` from the E22 | EVR; UV -> LOW_BUS_V; OT -> TX off 5 min + SAFE | RADIO_FAULT, LOW_BUS_V, PA_OVERTEMP |
| Bus temperature | Nicla Sense ME >= 75 degC | flag (hysteresis 5 degC) | AVI_OVERTEMP |
| I2C bus | > 10 consecutive errors | re-initialise the controller | I2C_BUS |
| Instrument loss | Nicla Sense ME silent > 5 s | health bits cleared, EVR; auto-recovers | SENSOR_LOST |
| Downlink congestion | VC backlog > 800 octets | science recorded only (play back later) | VC_CONGEST |
| COP-1 | FARM lockout | flag; ground `UNLOCK` | FARM_LOCKOUT |
| Radio change | no TC on the new setting within `revert_s` | revert to previous channel/rate | RATE_REVERT |
| Uplink security | SDLS bad MAC / replay / SPI | frame rejected, counter, EVR | SDLS_AUTH |
| Scheduler | loop > 250 ms | counter | LOOP_OVERRUN |

`FDIRMASK` (hazardous) can disable individual responses for testing.

## 6 Design decisions (and why)

| Decision | Reason |
|---|---|
| One CADU per LoRa packet (236 <= 240 octets) | The E22 drops packets with a bad LoRa CRC, so the unit of loss is the packet; aligning CADUs to packets makes every loss exactly one frame (visible as an MCFC gap). |
| RS(255,223) kept although LoRa has its own FEC | It corrects residual errors the LoRa CRC lets through and provides measured link-quality data (corrections per codeblock) the E22 does not expose. |
| No LDPC/turbo/convolutional codes | Inside a packet that is either delivered or dropped by the radio, a stronger inner code cannot recover dropped packets; the gain would be zero. |
| 2.4 kbit/s default | Slowest = most sensitive setting the E22-400T37S offers (-126 dBm typ). |
| Authentication without encryption | Amateur-radio rules forbid obscuring content; authentication still stops unauthorised commanding. |
| SAFE at boot, command-loss timer, auto-revert | Every state change that could cut the link must heal by itself - the core rule of deep-space operations. |
| Sensor sampling in PA-off windows | Keeps 5 W RF out of the magnetometer data and keeps blocking I2C work out of the radio timing. |
| No I2C multiplexer | The four instruments have distinct addresses; fewer parts, fewer failure points. |
| Web-only receiver display | Unlimited detail (hundreds of numbers), accessible from any device on the LAN. |
