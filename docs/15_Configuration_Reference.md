# 15 - Configuration Reference

This is the complete list of settings of the system: every setting in the flight
configuration header [`vgq1-spacecraft/sketch/src/config.h`](../vgq1-spacecraft/sketch/src/config.h)
and every key of the station configuration
[`dssq-groundstation/python/config/station.toml`](../dssq-groundstation/python/config/station.toml),
with its default, the allowed range, the unit, what it does and whether it must match a setting
at the other end of the link. The station sketch build switches, the environment variables and
the generated key files are listed at the end.

Plain rule first: **the spacecraft is configured when it is built, the station when the ground
software starts.** A `config.h` change needs a rebuild and upload of the flight sketch; a
`station.toml` change needs a restart of the ground data system (GDS).

Sources: `config.h` (78 settings: 14 `#define` macros, 64 constants), `station.toml` and its
validator [`dssq/config.py`](../dssq-groundstation/python/dssq/config.py) (`DEFAULTS`,
`validate()`), [`dssq/link.py`](../dssq-groundstation/python/dssq/link.py),
[`dssq/radio/e22.py`](../dssq-groundstation/python/dssq/radio/e22.py) (`regs_from_cfg`),
[`dssq/spectral.py`](../dssq-groundstation/python/dssq/spectral.py) (`ClassThresholds`) and the
station sketch [`dssq-groundstation/sketch/sketch.ino`](../dssq-groundstation/sketch/sketch.ino).
Operating procedures that change settings at run time are in
[07_Operations_Procedures.md](07_Operations_Procedures.md); what a wrong setting looks like is in
[14_Troubleshooting_and_Event_Response.md](14_Troubleshooting_and_Event_Response.md).

---

## 1 How a change takes effect

| What you change | Where | How it takes effect | Check |
|---|---|---|---|
| A flight setting (`config.h`) | The `vgq1-spacecraft` App on the UNO Q (App Lab editor), or a checkout of the repository | **Rebuild and upload** the sketch (App Lab: Run). App Lab always compiles the sketch's default profile, which cannot carry `-D` flags, so edit the `#define` itself. A command-line `arduino-cli` build may override the `#ifndef`-guarded macros with `-D` | Boot EVRs 0x0001, 0x0306, 0x0308 ([14 §3](14_Troubleshooting_and_Event_Response.md)) |
| E22 module model (T33D or T37S) | Jumper on header **A0** of the UNO Q | **No edit**: jumper A0 to GND = E22-400T37S, open = E22-400T33D; read once at boot (5 reads 1 ms apart, majority). Power-cycle after moving the jumper | EVR 0x0308, RF packet `radio_model` |
| A station setting (`station.toml`), App Lab | `<App>/data/station.toml` = `/home/arduino/ArduinoApps/dssq-groundstation/data/station.toml` | **Restart the App.** The App copies the packaged `python/config/station.toml` to `data/` at its first start and never overwrites that copy: after the first start, edit the copy in `data/`, not the packaged file | GDS start event in EVENTS ([14 §4.1](14_Troubleshooting_and_Event_Response.md)) |
| A station setting, host (systemd) mode | The same `<App>/data/station.toml`: the installed service runs `python -m dssq.gds --app-data <App>/data` ([`host/dssq-gds.service`](../dssq-groundstation/host/dssq-gds.service)), so App Lab and host mode share one configuration, archive, key and token (the App Lab path rules below apply). A manual run with `--config FILE` and no `--app-data` reads that file instead, with relative paths resolved against the working directory | Restart the service (`sudo systemctl restart dssq-gds`) | `journalctl -u dssq-gds` |
| E22 module registers (`[radio]` channel, air_rate, power, address, netid, lbt, ...) | The station radio module itself | The GDS **does not write** `station.toml` into an E22 module at start: it reads the module back and raises `MISMATCH` (WARN) when channel, rate code, RSSI byte or noise enable differ. The saved configuration is written once with `python -m dssq.radio.e22 --port /dev/ttyUSB0 --write --config config/station.toml` (module in CONFIG mode, M1 = 1, M0 = 0; see [00_Beginner_Setup_Guide.md](00_Beginner_Setup_Guide.md)). Coordinated changes (`LINKRATE` / `LINKCHAN`) write volatile registers only | EVENTS: `ground E22-400T33D: ch 23 (433.125 MHz) ... - matches station.toml` |
| SX126x station radio settings | `station.toml` | The GDS pushes channel, rate code, power code, CAD and Rx boosted gain to the station MCU over the Router Bridge (`rf_cfg`) when the backend attaches, and again after the station MCU rebooted (it starts with its compiled defaults) | RECEIVER tab, station SX126x status |
| SX126x chip type or TCXO voltage of the **station** | `DSSQ_SX_CHIP` / `kSxTcxoMv` in the station sketch | **Rebuild the station sketch.** `[radio.sx126x] chip` / `tcxo_v` only declare what was built; a difference raises `HW_MISMATCH` and a YELLOW alarm | YELLOW `ground SX126x hardware mismatch: ...` |

**Validation.** The GDS validates the merged configuration at start
([`config.py`](../dssq-groundstation/python/dssq/config.py) `validate()`). An invalid value stops
the station with a message that names the key (App Lab: exit status 2, message
`configuration <path>: [section] key ...` in the App log). The station never guesses a channel
or an SDLS setting. An **unknown key** in a known table is only warned in the log
(`unknown key [section] key ignored (typo?)`), except in `[radio.sx126x]` and
`[spectral.classifier]`, where it is an error. Check the log after every edit.

**Merge rule.** The file is laid over the built-in `DEFAULTS` table by table and key by key;
the nested tables `[radio.sx126x]`, `[radio.sensitivity_measured]` and `[spectral.classifier]`
are merged one level down. A table you write replaces only the keys it names.

**App Lab path rules** (`resolve_app_paths()`): a relative `[gds] archive_dir` is placed under
`<App>/data`; a relative `[sdls] key_file` is ignored and `<App>/data/sdls_key.hex` is used (an
absolute path is kept); `[gds] http_host` is forced to `0.0.0.0` unless the environment
variable `DSSQ_HTTP_HOST` is set; an empty `[gds] http_token` is read from
`<App>/data/http_token` or created there once (32 hex digits, mode 0600) and printed to the App
Lab console. Inside the App container the App folder is mounted at `/app`, so logs show
`/app/data/...` for what the board's shell sees as
`/home/arduino/ArduinoApps/dssq-groundstation/data/...`.

---

## 2 Settings that MUST MATCH between the two ends

A mismatch in any of these rows stops the link or the security service. The right-hand column
says what the station shows when they differ.

| # | Spacecraft (`config.h` or build) | Station (`station.toml` or station build) | Symptom when different |
|---|---|---|---|
| 1 | Radio model: `VGQ_RADIO` + strap A0 (E22 T33D / T37S), or the SX126x build | `[radio] model` (`"auto"` follows the spacecraft's E22 model) | WARN `spacecraft reports radio model ...`, YELLOW `radio model mismatch`; an E22 against an SX126x never hears anything |
| 2 | `kRadioChannel` = 23 | `[radio] channel` = 23 (and in `allowed_channels`) | No AOS (different frequency) |
| 3 | `kRadioRateCode` = 2 (E22) / 1 (SX126x) | `[radio] air_rate` = 2 (E22) / 1 (SX126x) | No AOS (different modulation) |
| 4 | `kE22Address` = 0x0000, `kE22NetId` = 0x00 | `[radio] address` = 0, `netid` = 0 | E22 transparent mode: packets filtered out |
| 5 | `VGQ_SDLS_TC` = 2 (AE) | `[sdls] tc_service` = `"AE"` | Every command refused on board: EVR 0x0112..0x0116 (`bad SPI`, `bad length` ...) |
| 6 | `VGQ_SDLS_TM` = 2 (AE) | `[sdls] tm_service` = `"AE"` | Frames dropped: WARN `TM frame VCn rejected by SDLS: BAD_SPI`, YELLOW `SDLS service mismatch suspected` |
| 7 | Master key in `sketch/src/sdls_key.h` | The same key in `sdls_key.hex` (`[sdls] key_file`) | AE: every frame `BAD_MAC` both ways |
| 8 | `VGQ_SDLS_SPI` (from `sdls_key.h`, default 1) | `[sdls] spi` = 1 | AUTH service only: commands refused with EVR 0x0113 `bad SPI` |
| 9 | `VGQ_DOWNLINK_FRAMING` = 0 (TM) | `[link] tm_framing` = `"auto"` (accepts both) | Only when forced to `"TM"`/`"USLP"`: frames not parsed |
| 10 | `kSxSyncWord` = 0x12, `kSxPreambleSym` = 12 ([`drivers/radio.h`](../vgq1-spacecraft/sketch/src/drivers/radio.h)) | `[radio.sx126x] sync_word` = 0x12, `preamble` = 12 (validated: any other value is refused) | - (cannot differ) |
| 11 | SCID 0x0A7 (compiled into both ends) | `[spacecraft] scid` = 0x0A7 (validated) | - (cannot differ) |
| 12 | E22 UART 9600 bit/s (`kE22UartBaud`, drivers/radio.h) | `[radio] uart_code` = 3 (validated for E22) | - (cannot differ) |
| 13 | AIRRATE / CHANNEL `revert_s` range 60..3600 s | `[linkmgr] revert_s` (validated 60..3600) | - |

Pairs that are **not** must-match but are often confused: the transmit power (`kRadioPowerCode`
vs `[radio] power`: each end chooses its own), LBT (`kRadioLbt` vs `[radio] lbt` /
`cad_before_tx`), the uplink frame format (`[link] tc_framing`: the flight accepts TC and USLP
frames at any time), and the PA gain (`kSxPaGainDb` vs `[radio.sx126x] pa_gain_db`: each end's
own module, same value only when both use the same module type). The callsigns
(`VGQ_CALLSIGN` vs `[station] callsign`) are per transmitter, not must-match: each end
identifies its own transmitter (the same callsign when one licensee operates both ends,
different ones otherwise); neither end checks the other's.

Station-internal pairs (station sketch against `station.toml`, rebuild the sketch to change):
`DSSQ_SX_CHIP` against `[radio.sx126x] chip`, `kSxTcxoMv` against `[radio.sx126x] tcxo_v`
(section 5).

---

## 3 Flight configuration: `config.h`

Every row is one setting of
[`vgq1-spacecraft/sketch/src/config.h`](../vgq1-spacecraft/sketch/src/config.h). "Build" =
compile time; all of them need a rebuild and upload. "MATCH" = must match the station
(section 2). "VERIFY" = a datasheet or engineering default that the hardware tests of
[08_Verification_and_Test.md](08_Verification_and_Test.md) must confirm; all are listed in
[20_Known_Limitations.md](20_Known_Limitations.md).

### 3.1 Radio model and radio interface

| Setting | Default | Allowed | Unit | Meaning | Notes |
|---|---|---|---|---|---|
| `VGQ_RADIO_E22_T33D` | 1 | fixed | code | Model code of the EBYTE E22-400T33D (2 W, UART) | Fixed by the ICD (RF packet `radio_model`); do not edit |
| `VGQ_RADIO_E22_T37S` | 2 | fixed | code | Model code of the E22-400T37S on the E22-400TBH-02 board (5 W) | Fixed by the ICD |
| `VGQ_RADIO_SX126X` | 3 | fixed | code | Model code of an SX1268 / SX1262 module on SPI (RadioLib 7.8.1) | Fixed by the ICD |
| `VGQ_RADIO` | `VGQ_RADIO_E22_T33D` | 1, 2 or 3 (`#error` otherwise) | code | Build-time radio. For E22 builds it is only the default model: the strap (below) switches an E22 build to the T37S at boot. `3` selects the SX126x drivers (different wiring) | MATCH row 1 |
| `VGQ_SX126X_CHIP` | 1268 | 1268 or 1262 | - | Transceiver chip of the spacecraft's SX126x module (1268 = EBYTE E22-400M33S reference module) | SX126x build only |
| `VGQ_RADIO_ON_SERIAL1` | 0 | 0 or 1 | flag | 0 = E22 on `Serial3` (USART3, header pins printed SDA = PB11 RX / SCL = PB10 TX); 1 = on D0/D1 (`Serial1`), which is also the Zephyr console: bootloader text would be transmitted | Keep 0. With 0 never call `Wire.begin()` (I2C2 shares PB10/PB11) |
| `RADIO_SERIAL` | `Serial3` | derived | - | The UART used for the E22 (follows `VGQ_RADIO_ON_SERIAL1`) | Not edited directly |
| `PIN_E22_M0` | 2 | header pin | D pin | D2 (PB3) drives E22 M0 | Fit a 10 kOhm pull-up to 3.3 V ([03_Hardware_Integration.md](03_Hardware_Integration.md)) |
| `PIN_E22_M1` | 3 | header pin | D pin | D3 (PB0) drives E22 M1 | PB0 is not 5 V tolerant; 10 kOhm pull-up to 3.3 V |
| `PIN_E22_AUX` | 7 | header pin | D pin | D7 (PB2) reads E22 AUX (LOW = busy) | |
| `PIN_RADIO_MODEL_STRAP` | 14 | header pin, -1 = no strap | D pin | A0 = D14 = PA4: jumper to GND selects the E22-400T37S, open keeps `VGQ_RADIO` | A0 is not 5 V tolerant: a plain jumper to GND only |
| `kStrapSamples` | 5 | 1..255 | reads | Strap reads at boot; majority decides; a split reading gives EVR 0x0308 WARN | |
| `kStrapSettleUs` | 1000 | > 0 | us | Spacing of the strap reads (lets the internal pull-up charge a jumper lead) | |

### 3.2 SX126x module on SPI (only with `VGQ_RADIO = VGQ_RADIO_SX126X`)

| Setting | Default | Allowed | Unit | Meaning | Notes |
|---|---|---|---|---|---|
| `PIN_SX_NSS` | 10 | header pin | D pin | D10 (PB9) SPI chip select | |
| `PIN_SX_BUSY` | 4 | header pin | D pin | D4 (PA12) BUSY input | |
| `PIN_SX_DIO1` | 5 | header pin | D pin | D5 (PA11) DIO1 interrupt | |
| `PIN_SX_NRST` | 6 | header pin | D pin | D6 (PB1) NRESET | |
| `PIN_SX_RXEN` | 8 | header pin, -1 = not wired | D pin | D8 (PB4) module RXEN | |
| `PIN_SX_TXEN` | 9 | header pin, -1 = not wired | D pin | D9 (PB8) module TXEN | |
| `kSxTcxoV` | 1.8 | 0 (crystal) or the module's TCXO supply | V | DIO3 TCXO supply | VERIFY against the module datasheet. A TCXO that does not start gives EVR 0x0309 |
| `kSxDio2RfSwitch` | false | true / false | flag | true = DIO2 drives the RF switch; false = RXEN/TXEN do | VERIFY against the module wiring |
| `kSxPaGainDb` | 11 | int8 | dB | Chip drive -> antenna power gain of the module PA (radiated = drive + gain, capped at 33 dBm) | VERIFY, test T-RF-07 |
| `kSxOcpMa` | 140.0 | > 0 | mA | PA over-current limit set explicitly after the power configuration | SX1262 value; VERIFY for the SX1268 |

### 3.3 Radio defaults (MATCH: `station.toml [radio]`)

| Setting | Default | Allowed | Unit | Meaning | Notes |
|---|---|---|---|---|---|
| `kRadioChannel` | 23 | 0..83 | channel | f = 410.125 MHz + CH x 1 MHz; 23 = 433.125 MHz. Also the channel restored by the command-loss response | MATCH row 2; check your band plan and licence ([09_Regulatory_and_Safety.md](09_Regulatory_and_Safety.md)) |
| `kRadioRateCode` | 2 (E22), 1 (SX126x) | 0..7 | code | E22: REG0 air rate (0, 1, 2 = 2.4 kbit/s; 3 = 4.8k; 4 = 9.6k; 5 = 19.2k; 6 = 38.4k; 7 = 62.5k). SX126x: LoRa profile (1 = SF12 / 62.5 kHz / CR 4/5). Also the rate restored by the command-loss response | MATCH row 3. Profiles and sensitivities: [02_Link_Budget_and_RF_Analysis.md](02_Link_Budget_and_RF_Analysis.md) |
| `kRadioPowerCode` | 0 | 0..3 | code | Boot transmit power code: T33D 0..3 = 33 / 30 / 27 / 24 dBm (codes 1..3 VERIFY, T-RF-07); T37S always 37 dBm; SX126x drive +22 / +17 / +14 / +10 dBm + `kSxPaGainDb` | Changed in flight with `TXPWR` |
| `kBrownoutPowerCode` | 2 | 0..3 | code | Power code used after a BROWNOUT reset (whichever of this and `kRadioPowerCode` is the lower power); EVR 0x0608 | Restore with `TXPWR` after fixing the supply |
| `kRadioLbt` | false | true / false | flag | E22 listen-before-talk / SX126x CAD before every transmission | Changed in flight with `RADIOOPT` b0 |
| `kRadioRxBoost` | true | true / false | flag | SX126x Rx boosted gain (ignored by the E22) | `RADIOOPT` b1 |
| `kE22Address` | 0x0000 | 0..0xFFFF | - | E22 module address (transparent mode) | MATCH row 4 |
| `kE22NetId` | 0x00 | 0..0xFF | - | E22 network ID | MATCH row 4 |

### 3.4 Framing and SDLS

| Setting | Default | Allowed | Unit | Meaning | Notes |
|---|---|---|---|---|---|
| `VGQ_DOWNLINK_FRAMING` | 0 | 0 = TM (CCSDS 132.0-B-3), 1 = USLP (732.1-B-2) (`#error` otherwise) | - | Downlink frame format. The uplink accepts both formats at any time | MATCH row 9 when the station forces a format |
| `VGQ_SDLS_TC` | 2 | 0 NONE, 1 AUTH (HMAC-SHA-256-128), 2 AE (AES-256-GCM) | - | Uplink security service. Without the generated `src/sdls_key.h` both services run NONE and HK says so | MATCH row 5. Encryption is generally not permitted on amateur frequencies: AUTH or NONE there |
| `VGQ_SDLS_TM` | 2 | 0 NONE, 2 AE (`#error` for 1: there is no authentication-only TM service) | - | Downlink security service | MATCH row 6 |
| `kSdlsEpochWaitS` | 120 | > 0 | s | Data frames (VC0..3) are held until the EGSE supplies an SDLS epoch; after this time they go out under the null SA (clear, flagged) with EVR 0x0121 until an epoch arrives | Nonce safety ([04_Space_Data_Link_ICD.md](04_Space_Data_Link_ICD.md)) |
| `kSdlsSnWaitS` | 120 | > 0 | s | Authentic commands are refused (EVR 0x0123) until the EGSE restores the anti-replay counter; after this time they are accepted above the RAM counter (EVR 0x0124) so a spacecraft without its EGSE stays commandable | |
| `VGQ_SDLS_SPI` | 1 | 1..65535 | - | AUTH Security Parameter Index. **Not in `config.h`**: written into the generated `src/sdls_key.h` by `tools/gen_sdls_key.py --spi N`; `flight.cpp` uses 1 when absent | MATCH row 8 |

### 3.5 Sensor bus and instruments

| Setting | Default | Allowed | Unit | Meaning | Notes |
|---|---|---|---|---|---|
| `SENSOR_WIRE` | `Wire2` | `Wire2` | - | The sensor I2C bus = I2C3 on header A4 (SDA, PC1) / A5 (SCL, PC0), 3.3 V only. The AS7265X alone may use `QWIIC_WIRE` instead | Every pull-up on this bus goes to 3.3 V |
| `QWIIC_WIRE` | `Wire1` | `Wire1` | - | The UNO Q Qwiic socket = I2C4 (PD12/PD13), 3.3 V: probed for the AS7265X after A4/A5 (D-13); nothing else uses it | No GPIO bus clear on these pins |
| `kPinSensorSda` | 18 | fixed | GPIO index | A4 = PC1 as a GPIO for the bus clear | |
| `kPinSensorScl` | 19 | fixed | GPIO index | A5 = PC0 as a GPIO for the bus clear | |
| `kI2cClockHz` | 100000 | 100000 typical | Hz | Bus clock; 100 kHz tolerates a long boom cable | |
| `kI2cClearHalfPeriodUs` | 5 | > 0 | us | SCL half period of the bus clear (up to 9 clocks + STOP release a target holding SDA) | |
| `kI2cClearMaxPulses` | 9 | 1..255 | pulses | Clock pulses of the bus clear; EVR 0x060A when SDA or SCL stays low after them | |
| `kI2cBusHealthyS` | 10 | > 0 | s | FDIR `I2C_BUS` clears after this long without a new bus error (EVR 0x0609) | |
| `kRm3100ObAddr` | 0x20 | 7-bit address | - | Outboard (boom tip) RM3100 (SA1 = 0, SA0 = 0) | |
| `kRm3100IbAddr` | 0x23 | 7-bit address | - | Optional inboard RM3100 (SA1 = 1, SA0 = 1), auto-detected | |
| `kRm3100ToBody` | identity 3 x 3 | rotation matrix | - | RM3100 axes -> body (Nicla Sense ME) axes, for the TRIAD attitude only (MAG packets stay raw) | VERIFY the mounting ([08_Verification_and_Test.md](08_Verification_and_Test.md)) |
| `kMagCollectTimeoutMs` | 30 | longer than one conversion | ms | DRDY deadline after the POLL write (a conversion takes about 6.8 ms at cycle count 200, proportionally longer at higher `MAGCC` counts); waited across loop passes | |
| `kSpecLampDeferMaxMs` | 2100 | > 2018 (the driver's lamp-watchdog limit) | ms | Longest wait of a magnetometer sample for the AS7265X lamps to go dark; after it the sample is taken and flagged (MAG flags b4 LAMP) | VERIFY, T-SC-08 |
| `kAruMagUtPerLsb` | 0.1 | > 0 | uT/LSB | Scale of the BMM150 field sent by the Nicla Sense ME ARU firmware | Test T-SC-06 (rotate: constant total field) |

### 3.6 AS7265X spectrometer

| Setting | Default | Allowed | Unit | Meaning | Notes |
|---|---|---|---|---|---|
| `kPinSpecInt` | -1 | -1 (not wired) or a pin | D pin | Board INT pin; -1 = DATA_RDY is polled over I2C. Leave the SparkFun board's INT unconnected (its INT and RST pins are swapped) | |
| `kSpecDefaultGain` | 255 | 0..3 (1x / 3.7x / 16x / 64x), 255 = automatic exposure | code | Boot gain (`SPECCFG` changes it) | |
| `kSpecDefaultCycles` | 59 | 1..255 | cycles | Boot integration (N + 1) x 2.78 ms = 166.8 ms (the datasheet calibration time) | |
| `kSpecBurstCycles` | 1 | 1..255 | cycles | BURST integration per bank: (1 + 1) x 2.78 ms = 5.56 ms | |
| `kSpecBaselineAlpha` | 0.1 | 0 < alpha <= 1 | - | Weight of the newest spectrum in the change-detection baseline (EWMA) | |
| `kSpecEventCdeg` | 500 | 0..9000 | 0.01 deg | Spectral-event threshold, 5.00 deg (EVR 0x0701, at most one per 60 s); `SPECBASE` changes it in flight | |
| `kSpecBoresightBody` | {0, 0, 1} | unit vector | - | Instrument boresight in body axes (for the zenith / azimuth in SPEC packets) | VERIFY the mounting, T-SC-09 |

### 3.7 CFDP, EGSE link and debugging

| Setting | Default | Allowed | Unit | Meaning | Notes |
|---|---|---|---|---|---|
| `kCfdpVc3BacklogMax` | 320 | >= 158 (one largest packet) | octets | A CFDP PDU is built only while VC3 backlog + 12 + 146 <= this (VC3 never holds more than two packets) | Segment sizes are in [`ccsds/cfdp.h`](../vgq1-spacecraft/sketch/src/ccsds/cfdp.h): 128 octets (MCU), 96 (EGSE relay) |
| `kRelayStartTimeoutMs` | 30000 | > 0 | ms | An EGSE relay that does not start (or finish a cancellation) within this time is given up (EVR 0x0A04) | |
| `kEvrNotifyMaxPerS` | 8 | 1..255 | per s | `sc_evr` notifications to the EGSE per second (excess counted `evr_nd`) | |
| `kEvrNotifyMaxPerPass` | 2 | 1..255 | per pass | `sc_evr` notifications per loop pass | |
| `VGQ_DEBUG_MONITOR` | 0 | 0 or 1 | flag | 1 = log lines on the App Lab Monitor. Monitor writes can block the flight loop: bench only, flight builds keep 0 | |

### 3.8 Solid-state recorder, timing, thermal

| Setting | Default | Allowed | Unit | Meaning | Notes |
|---|---|---|---|---|---|
| `kSsrMaxBytes` | 262144 | power of two | octets | Largest SSR heap tried at boot (256 KiB); EVR 0x0003 reports what was allocated | |
| `kSsrMinBytes` | 16384 | <= max | octets | Smallest acceptable SSR | |
| `kHeapReserveBytes` | 32768 | > 0 | octets | Heap that must stay free after the SSR (Bridge notifications use it) | |
| `kMinFramePeriodMs` | 1500 | > 0 | ms | Shortest frame period. Frame period = max(busy x mode factor, busy x 2 (duty), this, `FPERIOD`); after a radio reconfiguration the next launch also waits the old setting's period (`launch_spacing_ms()`) | Mode factors SAFE 4.0, CRUISE 2.5, ENCOUNTER / TEST 2.0 ([`fsw/datalink.cpp`](../vgq1-spacecraft/sketch/src/fsw/datalink.cpp)) |
| `kMaxTxDutyPermille` | 500 | 1..1000 | 0.1 % | Transmit at most 50 % of the time (PA thermal limit) | |
| `kNoiseSampleMs` | 15000 | > 0 | ms | Ambient-noise sampling interval (RF packet `sc_noise_floor`) | |
| `kDefaultCmdLossS` | 86400 | 0..604800, 0 = off | s | Command-loss timer at boot (24 h). No valid uplink for this long -> SAFE + default radio (EVR 0x0601). `CMDLOSS` changes it | |
| `VGQ_IWDG` | 1 | 0 or 1 | flag | 1 = the independent hardware watchdog is armed at the end of `setup()` and refreshed once per loop pass; a pass that never ends resets the MCU (`reset_cause` 3 WATCHDOG). 0 = none (bench debugging only) | Once started it runs until the next reset |
| `kIwdgTimeoutMs` | 40000 | 1..131072 (outside it `watchdog_start()` returns without starting the watchdog, no EVR) | ms | Watchdog timeout at the nominal LSI frequency (prescaler /512, reload 2499; `fsw/autonomy.cpp` `iwdg_settings()`); far above the worst documented loop pass (E22 `configure()` on a dead module about 4 s, I2C time-outs 0.5 s each) | |
| `kIwdgLsiHz` | 32000 | - | Hz | Nominal LSI frequency the watchdog settings are computed for (RM0456) | |
| `kIwdgUpdateWaitMs` | 200 | > 0 | ms | `setup()` waits at most this long for the watchdog settings to reach the IWDG; EVR 0x060C if they do not | |
| `kPaCooldownS` | 300 | > 0 | s | E22-400T37S PA over-temperature: transmitter off for this long, then SAFE cadence (EVR 0x0604) | T37S only |
| `kBusTempRedC` | 75.0 | - | degC | FDIR `AVI_OVERTEMP` at or above this bus (Nicla Sense ME) temperature; clears 5 degC below | Ground limits on HK `bus_temp`: YELLOW >= 60, RED >= 75 degC |

### 3.9 Site and identification

| Setting | Default | Allowed | Unit | Meaning | Notes |
|---|---|---|---|---|---|
| `kGeomagInclDeg` | 60.0 | -90..90 | deg | Local geomagnetic inclination for TRIAD (+ = field points down) | Look up your site (NOAA / BGS World Magnetic Model calculator) |
| `kGeomagDeclDeg` | 0.0 | -180..180 | deg | Local declination (+ = east of true north) | As above |
| `VGQ_CALLSIGN` | `"N0CALL"` | text, <= 22 characters (the OID text `DE <callsign> VGQ-1 ` is cut at 32, `fsw/datalink.cpp` `format_idle_text`) | - | Station identification: EVR 0x0002 `DE <callsign>` and the clear-text OID idle data `DE <callsign> VGQ-1 ` (never encrypted) | **Set your own licensed callsign** before transmitting |
| `kCallsignPeriodS` | 600 | > 0 | s | Interval of the EVR 0x0002 identification | |

---

## 4 Station configuration: `station.toml`

Every key of [`station.toml`](../dssq-groundstation/python/config/station.toml) with its
built-in default (`DEFAULTS` in `config.py`, identical to the shipped file except where noted),
the validation rule, the unit and the effect. "Restart" = restart the GDS. "Module" = an E22
register image: it reaches the module only through the E22 tool (section 1).

### 4.1 `[station]`

| Key | Default | Allowed | Unit | Effect |
|---|---|---|---|---|
| `name` | `"DSS-Q1"` | string <= 64 characters, printable ASCII | - | Station name: dashboard header, TDM `PARTICIPANT_1` / `ORIGINATOR` and file name `VGQ1_<name>_<UTC>.kvn` |
| `callsign` | `"N0CALL"` | amateur callsign: 3..16 characters of letters, digits and single `/` separators (portable prefix or suffix, e.g. `G4ABC/P`), at least one letter and one digit; upper-cased on load | - | Station identification on the uplink, `DE <callsign> <name>` in plain ASCII (09 §1.3; when it is sent: 07 §2.8). Not a MATCH item: each end identifies its own transmitter. **Set your own licensed callsign**: the placeholder `N0CALL` is reported with a WARN at start |
| `latitude_deg` | 0.0 | -90..90 | deg | Station location (informational: validated, not used in computations yet) |
| `longitude_deg` | 0.0 | -180..180 | deg | As above |
| `altitude_m` | 0.0 | -500..100000 | m | As above |
| `antenna` | `"433 MHz Yagi"` | string <= 128 | - | Antenna description (informational) |
| `antenna_gain_dbi` | 12.0 | -20..60 | dBi | Station antenna gain in the link budget (RECEIVER tab). Use the measured or datasheet gain |
| `rx_line_loss_db` | 1.0 | 0..60 | dB | Coax and connectors between antenna and radio (link budget, both directions) |

### 4.2 `[spacecraft]`

| Key | Default | Allowed | Unit | Effect |
|---|---|---|---|---|
| `name` | `"VGQ-1"` | string <= 64, printable ASCII | - | Spacecraft name: dashboard, TDM `PARTICIPANT_2` |
| `scid` | 0x0A7 | must be 0x0A7 | - | Mission spacecraft ID compiled into both ends; any other value is refused |
| `distance_km` | 0.0 | >= 0 | km | Path length for the one-way light time (time correlation) and the predicted link budget; 0 = unknown (no prediction) |
| `antenna_gain_dbi` | 2.15 | -20..60 | dBi | Spacecraft antenna gain (link budget) |
| `tx_line_loss_db` | 0.5 | 0..60 | dB | Spacecraft feed loss (link budget) |

### 4.3 `[radio]`

| Key | Default | Allowed | Unit | Effect | Applies |
|---|---|---|---|---|---|
| `model` | file: `"E22-400T33D"`; built-in (key absent): `"auto"` | `E22-400T33D`, `E22-400T37S`, `SX126x`, `auto` (case-insensitive) | - | Station radio model. `auto` = an E22 station that follows the E22 model the spacecraft reports in its RF packet (contract 33). `E22-400T37S` also enables the station module's abnormal-status log. MATCH row 1 | Restart |
| `port` | `"/dev/ttyUSB0"` | string <= 512 | - | E22 models: the USB-UART device (check `ls /dev/serial/by-id/`) | Restart |
| `address` | 0 | 0..0xFFFF | - | E22 module address. MATCH row 4 | Module |
| `netid` | 0 | 0..255 | - | E22 network ID. MATCH row 4 | Module |
| `uart_code` | 3 | must be 3 for E22 models (0..7 otherwise) | code | E22 UART rate code; 3 = 9600 bit/s (the GDS reads the module at 9600; CONFIG mode is always 9600 8N1) | Module |
| `parity` | 0 | 0..3 | code | E22 UART parity code (0 = 8N1) | Module |
| `air_rate` | 2 | 0..7 | code | Rate code: E22 REG0 air rate (0, 1, 2 = 2.4 kbit/s ... 7 = 62.5k); SX126x LoRa profile 0..7 (set 1). Changed at run time only by `LINKRATE`. MATCH row 3 | Module (E22), Restart (SX126x) |
| `subpacket` | 0 | 0..3 | code | E22 sub-packet size; 0 = 240 octets (one 236-octet CADU = one LoRa packet) | Module |
| `rssi_noise` | true | bool | - | E22: enables the `C0 C1 C2 C3` ambient-noise queries (noise floor, SNR estimate) | Module |
| `fault_log` | false | bool | - | E22-400T37S only: abnormal-status reports `FF FF FF 0x`; always written 0 to a T33D (reserved bits) | Module |
| `power` | 0 | 0..3 | code | Station transmit power code: T33D 33 / 30 / 27 / 24 dBm (codes 1..3 VERIFY, T-RF-07); T37S 37 dBm; SX126x drive 22 / 17 / 14 / 10 dBm + `pa_gain_db` | Module (E22), Restart (SX126x) |
| `channel` | 23 | 0..83, and listed in `allowed_channels` | channel | Operating channel, f = 410.125 + CH MHz. Changed at run time only by `LINKCHAN`. MATCH row 2 | Module (E22), Restart (SX126x) |
| `rssi_byte` | true | bool | - | E22 appends the packet RSSI to every received packet (required for the RSSI statistics) | Module |
| `fixed` | false | bool | - | E22 REG3 fixed-point transmission (keep false: transparent mode) | Module |
| `relay` | false | bool | - | E22 REG3 relay function (keep false) | Module |
| `lbt` | false | bool | - | E22 listen-before-talk on the uplink (SX126x uses `cad_before_tx`) | Module |
| `wor_role` | false | bool | - | E22 REG3 WOR role in the saved configuration; the GDS switches the module to WOR transmitter for `WAKE` and back | Module |
| `wor_cycle` | 0 | 0..7 | code | E22 REG3 WOR cycle in the saved configuration. `WAKE` always uses cycle code 3 = 2 s, the flight's hibernation cycle | Module |
| `noise_bw_hz` | 125000.0 | > 0 | Hz | E22: ASSUMED noise bandwidth for the C/N0 estimate and the TDM `PC_N0` (EBYTE publishes no LoRa bandwidth). SX126x: the profile bandwidth is used instead | Restart |
| `noise_poll_s` | 20.0 | >= 5 | s | Ambient-noise sample period of the station | Restart |
| `radio_latency_s` | 0.0 | 0..10 | s | Calibrated delay of the radio pipeline for time correlation and TDM timetags | Restart. VERIFY, T-TM-04 |
| `rssi_cal_offset_db` | 0.0 | -30..30 | dB | Added to every station RSSI and noise reading (absolute calibration) | Restart. Calibrate with T-RF-02 |
| `allowed_channels` | [20, 21, 22, 23, 24] | non-empty list of 0..83, at most 84 entries | channel | Channels your licence and band plan allow; `CHANNEL` and `LINKCHAN` outside the list are refused by the GDS. Default = 430.125..434.125 MHz (avoids the 435-438 MHz satellite segment) | Restart |
| `freq_offset_cal_hz` | 0.0 | -50000..50000 | Hz | SX126x: static TX-RX crystal offset; start value of the AFC and the zero of the Doppler estimate | Restart |
| `freq_offset_calibrated` | false | bool | - | true only after the frequency calibration; the TDM then carries `DOPPLER_INSTANTANEOUS` (else it never publishes a crystal offset as range rate) | Restart |

### 4.4 `[radio.sx126x]` (SX126x station only; unknown keys are an error)

| Key | Default | Allowed | Unit | Effect | Applies |
|---|---|---|---|---|---|
| `chip` | `"SX1268"` | `"SX1268"` or `"SX1262"` | - | Declares the chip the station sketch was built for (`DSSQ_SX_CHIP`); a difference raises `HW_MISMATCH` | Rebuild the station sketch to change the hardware |
| `tcxo_v` | 1.8 | 0 (crystal) or 1.6..3.3 | V | Declares the sketch's `kSxTcxoMv`; a difference raises `HW_MISMATCH`. VERIFY against the module datasheet | Rebuild the station sketch |
| `rx_boosted_gain` | true | bool | - | Rx boosted gain (register 0x08AC = 0x96, read back and verified); about 4 dB more sensitivity | Restart |
| `cad_before_tx` | true | bool | - | Channel-activity detection before every uplink | Restart |
| `sync_word` | 0x12 | must be 0x12 | - | LoRa sync word (private network) = flight `kSxSyncWord`. MATCH row 10 | - |
| `preamble` | 12 | must be 12 | symbols | Preamble length = flight `kSxPreambleSym`. MATCH row 10 | - |
| `pa_gain_db` | 11.0 | 0..20 | dB | Station module PA gain after the chip (reference E22-400M33S), for the station power figure. VERIFY, T-RF-07 | Restart |
| `afc` | true | bool | - | Ground automatic frequency correction (`rf_foff`); `AFC RESET` returns it to `freq_offset_cal_hz` | Restart |

### 4.5 `[radio.sensitivity_measured]` (open table, empty by default)

| Key | Default | Allowed | Unit | Effect |
|---|---|---|---|---|
| `"<MODEL>:<code>"`, e.g. `"E22-400T33D:2"` | none | model name as in `[radio] model`, code 0..7; value -160..-60 | dBm | Measured receiver sensitivity from test T-RF-03 (`python3 tools/link_budget.py sensitivity ...`). Overrides the datasheet / estimated / computed value (source label `measured`) in the link budget and the link manager. Never enter a value that was not measured |

### 4.6 `[link]`

| Key | Default | Allowed | Effect |
|---|---|---|---|
| `tm_framing` | `"auto"` | `auto`, `TM`, `USLP` | Downlink frame format: `auto` detects it per frame (first nibble 0xC = USLP). MATCH row 9 when forced |
| `tc_framing` | `"TC"` | `TC`, `USLP` | Uplink frame format. The flight accepts both. SEQLOAD and MAGCAL (26-octet TC packets) do not fit a 64-octet USLP + AE frame: keep `TC` for them (the GDS refuses the command with the frame-size message otherwise) |

### 4.7 `[sdls]`

| Key | Default | Allowed | Effect |
|---|---|---|---|
| `tc_service` | `"AE"` | `NONE`, `AUTH`, `AE` | Uplink security service. MATCH row 5 |
| `tm_service` | `"AE"` | `NONE`, `AE` (`AUTH` refused) | Downlink security service. MATCH row 6 |
| `key_file` | `"config/sdls_key.hex"` | path <= 4096 | Master key file (64 hex digits). App Lab and host mode with `--app-data` (the installed service): always `<App>/data/sdls_key.hex` unless an absolute path is given. Stand-alone `--config` run: relative to the working directory. Missing key with a service set: both ends run NONE, WARN event and a YELLOW alarm |
| `spi` | 1 | 1..65535 | AUTH Security Parameter Index (= flight `VGQ_SDLS_SPI`); AE always uses SPI 2 (TC) / 3 (TM). MATCH row 8 |
| `accept_null` | true | bool | Accept the flight's null-SA fallback frames (no epoch yet), flagged UNPROTECTED; false = drop them (`BAD_SPI`) |

The removed v2 key `enabled` is ignored with a warning.

### 4.8 `[cfdp]`

| Key | Default | Allowed | Unit | Effect |
|---|---|---|---|---|
| `inactivity_s` | 600.0 | > 0 | s | No PDU for this long: the transaction ends ABANDONED (`.partial` + `.json` saved) |
| `check_s` | 60.0 | > 0 | s | After an EOF that left gaps, late File Data is still accepted this long (INCOMPLETE can become COMPLETE) |
| `files_dir` | `"files"` | plain directory name (letters, digits, `_`, `.`, `-`) | - | Received files go to `<archive_dir>/<files_dir>` |
| `max_file_mib` | 64 | > 0 and <= 4096 | MiB | Largest file the receiver accepts (a larger one ends ABANDONED) |

### 4.9 `[spectral]` and `[spectral.classifier]`

| Key | Default | Allowed | Effect |
|---|---|---|---|
| `white_ref_file` | `"spec_white_ref.json"` | plain file name | White reference (`SPECREF`) saved under `archive_dir`; reloaded at start |
| `library_file` | `"spec_library.json"` | plain file name | Spectral library (`SPECLIB`, at most 64 entries) under `archive_dir` |
| `[spectral.classifier]` | empty | keys below, positive numbers only; unknown key = error | Overrides of the ground light-source classifier thresholds |

`[spectral.classifier]` keys and their defaults (all HEURISTIC values from synthetic spectra,
[`spectral.py`](../dssq-groundstation/python/dssq/spectral.py) `ClassThresholds`):
`dark_total_uw` 0.5, `dark_snr` 5.0, `narrow_peak_mean` 6.0, `low_nir_vis` 0.15,
`fluor_line` 1.5, `thermal_nir_vis` 0.70, `thermal_blue_max` 0.70, `day_blue_min` 0.70,
`day_flatness_min` 0.60, `conf_full_ln` ln 2 = 0.693, `planckian_duv_max` 0.01,
`incandescent_cct_min_k` 2000, `incandescent_cct_max_k` 3600, `daylight_cct_min_k` 4000,
`daylight_cct_max_k` 10000. The first ten mirror the compiled on-board thresholds
(`fsw/spectral.cpp`); changing them here makes the ground and on-board classes disagree more
often (SPECTRAL WARN `... DISAGREE`). The last five are ground-only plausibility checks.

### 4.10 `[science]`

| Key | Default | Allowed | Unit | Effect |
|---|---|---|---|---|
| `r_outboard_m` | 1.0 | > 0 | m | Boom distance of the outboard RM3100 from the bus centre (SCIENCE tab: spacecraft field and ambient field estimates, 1/r^3 model) |
| `r_inboard_m` | 0.5 | > 0 | m | Boom distance of the inboard RM3100 |
| `field_ut` | **absent** (no default on purpose) | 1..800 | uT | Site total magnetic field (World Magnetic Model) for the MAGCAL fit. Without it the MAGCAL fit is disabled (INFO event at start) |

### 4.11 `[tdm]`

| Key | Default | Allowed | Effect |
|---|---|---|---|
| `auto_write` | true | bool | Write the pass's CCSDS Tracking Data Message at LOS (and at shutdown) to `<archive_dir>/tdm/` |

### 4.12 `[linkmgr]`

| Key | Default | Allowed | Unit | Effect |
|---|---|---|---|---|
| `mode` | `"ADVISE"` | `OFF`, `ADVISE`, `AUTO` | - | Adaptive link manager: ADVISE shows a recommendation; AUTO executes it through the coordinated `LINKRATE` procedure. `LINKMGR` changes it until the next restart |
| `target_margin_db` | 6.0 | -50..100 | dB | Margin target: step down when the 10-minute 10th-percentile margin < target - 3 dB or FER > 10 %; step up when > target + 9 dB, FER < 1 % and the predicted margin at the faster rate >= target + 3 dB |
| `period_s` | 60.0 | >= 0 | s | Evaluation period |
| `holdoff_s` | 600.0 | >= 0 | s | At most one AUTO change per this time |
| `min_rate` | **absent** = the model's slowest distinct code (E22 2, SX126x 0) | 0..7, <= `max_rate` | code | Slowest rate the manager may choose |
| `max_rate` | 4 | 0..7 | code | Fastest rate the manager may choose |
| `revert_s` | 180 | 60..3600 | s | Spacecraft auto-revert window sent with every coordinated `AIRRATE` / `CHANNEL` |

### 4.13 `[interop]`

| Key | Default | Allowed | Effect |
|---|---|---|---|
| `tm_frames_udp` | `""` | `""` or `"host:port"` (port 1..65535) | Forward every received TM frame (after RS and FECF) as UDP datagrams, e.g. to a YAMCS / OpenC3 frame link |
| `tm_packets_udp` | `""` | as above | Forward every extracted Space Packet |
| `tc_frames_udp_listen` | `""` | `""`, `"127.x.y.z:port"` (127.0.0.0/8) or `"[::1]:port"` only: any other address or a host name is refused at start (validation), and a port already in use stops the start (App: `ground data system not started: ...`, exit status 1) | Accept TC frames (7..64 octets) from an external MCS; the GDS re-wraps them (SDLS, CLTU) and radiates them in the uplink window, with no authentication and no hazardous-command CONFIRM, hence loopback only |

### 4.14 `[gds]`

| Key | Default | Allowed | Unit | Effect |
|---|---|---|---|---|
| `archive_dir` | `"archive"` | path <= 4096 | - | Archive root ([18_Data_Products.md](18_Data_Products.md)). Host: relative to the working directory. App Lab: relative = under `<App>/data`; an absolute path (external NVMe / USB drive) works only if it is visible inside the App container (VERIFY on hardware) |
| `http_host` | `"127.0.0.1"` | string <= 253 | - | Web server bind address. `0.0.0.0` exposes the dashboard (and commanding) to the LAN. App Lab forces `0.0.0.0` (or `$DSSQ_HTTP_HOST`) |
| `http_port` | 8080 | 1..65535 | - | Web port (App Lab publishes 8080, `app.yaml`) |
| `http_token` | `""` | printable ASCII without blanks, <= 256 | - | Commanding secret, header `X-DSSQ-Token`. **Required when the server is not on loopback**: without it every POST is refused (read-only dashboard, WARN `WEB` event at start). App Lab creates one in `<App>/data/http_token` |
| `http_allowed_hosts` | `[]` | list of <= 32 host names (no port) | - | DNS names the dashboard may be reached by on a LAN-bound server, besides IP addresses, single-label names, `<name>.local` and the board's host name (DNS-rebinding guard) |
| `los_timeout_s` | 90.0 | > 0 | s | Minimum LOS timeout; the GDS uses max(this, 3 x the spacecraft's longest frame period) - e.g. 90 s at E22 2.4 kbit/s, 208 s at SX126x profile 1 |
| `frontpanel` | true | bool | - | Use the station I/O sketch (CardKB, LEDs, buzzer) over the Router Bridge |
| `rcu` | true | bool | - | The station MCU drives the ground E22's M0/M1/AUX (Radio Control Unit): needed for `RADIO`, `GSCAN`, `WAKE`, `LINKRATE`, `LINKCHAN` with an E22 |
| `router_address` | `""` | string | - | Router Bridge address; host default `unix:///var/run/arduino-router.sock`; App Lab uses `APP_SOCKET` |
| `console_interval_s` | 10.0 | > 0 | s | Period of the `STATUS` line in the log |
| `archive_keep_days` | 180 | 0..36500, 0 = no age limit | days | Day folders and rx_stats / params / frames rows older than this are deleted (never the current UTC day); exports older than this too |
| `archive_max_gb` | 16.0 | 0..1000000, 0 = no cap | GB (1e9 octets) | Size cap of day folders + database pages in use; over it the oldest day is pruned, one day per check (every 60 s, repeated at once while still over the cap) |
| `archive_min_free_mb` | 2048.0 | 0..10000000, 0 = guard off | MB (1e6 octets) | Free-space floor: below it raw CADU and rx_stats logging pause (WARN event, YELLOW alarm) and resume above floor + 5 % |

### 4.15 `[commanding]`

| Key | Default | Allowed | Unit | Effect |
|---|---|---|---|---|
| `t1_s` | 45.0 | > 0 | s | FOP-1 retransmission timer minimum; the GDS uses max(this, 2 x frame period) |
| `tx_limit` | 3 | 1..255 | transmissions | Transmissions per AD frame before the FOP suspends (SUSPENDED) |
| `window` | 5 | 1..127 | frames | Maximum outstanding AD frames |
| `verify_timeout_s` | 300.0 | > 0 | s | Command verification timeout minimum; the GDS uses max(this, 4 x frame period) |
| `require_two_person_for_hazardous` | false | bool | - | true: a hazardous command also needs a second operator's AUTH in the web dashboard |
| `auth_token` | `""` | printable ASCII without blanks, <= 256 | - | When set, AUTH also needs header `X-DSSQ-Auth` (the second operator's secret) |

Key count: 92 keys (90 in the shipped file plus the optional `[science] field_ut` and
`[linkmgr] min_rate`) and two open tables (`[radio.sensitivity_measured]`,
`[spectral.classifier]` with 15 possible keys).

---

## 5 Station sketch build settings (VENTUNO Q MCU)

The station I/O sketch [`dssq-groundstation/sketch/sketch.ino`](../dssq-groundstation/sketch/sketch.ino)
is configured at build time like the flight sketch (edit, then Run in App Lab).

| Setting | Default | Allowed | Meaning | Pairs with |
|---|---|---|---|---|
| `DSSQ_RADIO_SX126X` | 0 | 0 = E22 build (RCU on D2 M0, D3 M1, D4 AUX), 1 = SX126x radio on the MCU | Station radio build switch | `[radio] model = "SX126x"` |
| `DSSQ_SX_CHIP` | 1268 | 1268 or 1262 | SX126x chip of the station module | `[radio.sx126x] chip` |
| `kSxTcxoMv` | 1800 | 0 = crystal, else mV | DIO3 TCXO supply (VERIFY module) | `[radio.sx126x] tcxo_v` x 1000 |
| `kSxDio2RfSwitch` | false | bool | DIO2 drives the RF switch (VERIFY module) | - |
| `kSxSyncWord`, `kSxPreamble`, `kSxWakePreamble` | 0x12, 12, 32 | fixed | Sync word, preamble and wake-up preamble (= flight values) | `[radio.sx126x]`, flight `kSxWakePreambleSym` |
| `PIN_SX_NSS`, `PIN_SX_BUSY`, `PIN_SX_DIO1` | D10, D8, D9 | header pins | SX126x SPI / BUSY / DIO1 | Wiring ([03_Hardware_Integration.md](03_Hardware_Integration.md)) |
| `PIN_SX_NRST`, `PIN_SX_RXEN`, `PIN_SX_TXEN` | A1, A2, A3 | header pins | Reset and RF switch | Wiring |
| `PIN_RCU_M0`, `PIN_RCU_M1`, `PIN_RCU_AUX` | D2, D3, D4 | header pins | Radio Control Unit of the station E22 | `[gds] rcu` |
| `PIN_LED_AOS`, `PIN_LED_ALARM`, `PIN_LED_UPLINK`, `PIN_BUZZER` | D5, D6, D7, A0 | header pins | Annunciators ([06_Displays.md](06_Displays.md)) | `[gds] frontpanel` |

---

## 6 Environment variables and command-line options

| Name | Where | Default | Effect |
|---|---|---|---|
| `DSSQ_CONFIG` | App Lab ground App | unset | Load this `station.toml` instead of `<App>/data/station.toml` (must exist) |
| `DSSQ_HTTP_HOST` | App Lab ground App | unset (`0.0.0.0`) | Web server bind address inside the container |
| `DSSQ_SIM` | App Lab ground App | unset | `1` = software spacecraft instead of a radio (training and demonstration). Like `--sim`, it keeps the SDLS TC counter and TM replay state in memory: the App's real `sdls_sn.txt` and `sdls_tm_replay.txt` are never read or written |
| `--config FILE` | `python -m dssq.gds` (host) | `config/station.toml` next to `dssq/`, or `<APP_DATA>/station.toml` with `--app-data` | Configuration file |
| `--app-data DIR` | host (`dssq.gds`, also `dssq.cltu`) | unset (the installed service passes `<App>/data`) | Use a ground App's data folder exactly as the App does: `DIR/station.toml` (copied from the packaged default on first use; `--config` overrides it), archive, `sdls_key.hex`, `http_token`, App Lab path rules (§1) |
| `--port DEV` | host | `[radio] port` | Override the E22 serial port |
| `--sim`, `--sim-loss P`, `--sim-errors N` | host | off, 0.05, 3 | Software spacecraft; simulated packet-loss probability; maximum symbol errors per CADU. With `--sim` (with or without an archive, also with `--app-data`) the SDLS TC counter and TM replay state live in memory: the real `sdls_sn.txt` and `sdls_tm_replay.txt` are never read or written, so the simulator cannot end or disturb a hold of the real counter (`gds.py` `keep_security_state`) |
| `--no-archive` | host | archive on | Run without writing an archive (CSV export then refused). The SDLS security counters are not archive data and are still read and written in `<archive_dir>`: the TC sequence-number file `sdls_sn.txt` (+ `.lock`) and, with TM AE, the TM replay state `sdls_tm_replay.txt`; a missing `sdls_sn.txt` holds the uplink as with an archive ([17_Security_Model.md](17_Security_Model.md) section 7.6). Only the simulator (`--sim`, with or without `--no-archive`) keeps them in memory |
| `-v` | host | off | Debug logging |
| `--data DIR` | spacecraft EGSE (`vgq1-spacecraft/python/main.py`) | `<App>/data` | EGSE data directory |

---

## 7 Generated and secret files (never in Git, never in an App Lab zip)

| File | Created by | Holds | Must match |
|---|---|---|---|
| `sdls_key.h` (default `vgq1-spacecraft/sketch/src/sdls_key.h`; imported App: `~/ArduinoApps/vgq1-spacecraft/sketch/src/sdls_key.h`) | `python3 tools/gen_sdls_key.py [--spi N] [--force] [--flight-header PATH] [--ground-key PATH]` | `kVgqSdlsKey[32]`, `VGQ_SDLS_SPI` | The ground key file (MATCH rows 7, 8) |
| `sdls_key.hex` (default `dssq-groundstation/data/sdls_key.hex`; imported App: `~/ArduinoApps/dssq-groundstation/data/sdls_key.hex`, read by App Lab and by host mode with `--app-data` unless `[sdls] key_file` is absolute) | same tool, **same run** (`--ground-key`): one run makes one key for both files; copy each file to its board | The same 256-bit master key, 64 hex digits | The flight header |
| `<App>/data/http_token` (ground App) | the GDS at first start when `[gds] http_token` is empty | Web commanding secret, mode 0600 | The token you enter in the dashboard |

The key procedures (generation after import, installation, rotation, compromise, counter-file
backup) are in [07_Operations_Procedures.md](07_Operations_Procedures.md) section 10 and
[17_Security_Model.md](17_Security_Model.md).
