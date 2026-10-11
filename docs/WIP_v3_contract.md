# VGQ-1 / DSS-Q1 — v3 change contract (binding for every workstream)

Repository: /home/user/Benchmarks (branch claude/wonderful-hamilton-dzjfby). Read this whole
file before touching code. When this contract and the existing code disagree, the contract wins.
When the contract is silent, follow the existing code's conventions.

## 0 Global rules

* Facts must come from the datasheets/manuals listed in §11 or from source code you read. Never
  invent a number. Where a value is unknown or the datasheet is inconsistent, make it a named,
  commented constant, say "VERIFY" in the comment, and mention the test in docs/08 that verifies it.
* No AI-model names or identifiers anywhere in code, comments, docs or commit messages.
* Never create/commit `ground/config/sdls_key.hex` or `spacecraft/vgq1_flight/sketch/src/sdls_key.h`
  (gitignored). Tests generate keys in temp dirs.
* Flight MCU code (STM32U585, Zephyr Arduino core): FP32 only (`float`, `sqrtf`, `f` literals), no
  `double` math in hot paths, no dynamic allocation after boot except the existing SSR heap, the
  main loop must never block > ~20 ms in one call (state machines, not waits), never call
  `Bridge.call()` (only `notify` and `provide_safe` handlers). Pure-C++ algorithm/protocol code that
  must be host-tested lives in `src/ccsds/` (protocols) or `src/core/` (algorithms) and must NOT
  include Arduino.h. `tools/host_test/Makefile` compiles every `src/ccsds/*.cpp` and
  `src/core/*.cpp` automatically, plus tlm_packets/commands/ssr/attitude.
* Comment density/style: match the existing files (header block explaining the module and the
  standard section it implements, short "why" comments). Big-endian on the wire everywhere.
* Host tests: put new checks in a NEW file `tools/host_test/test_<topic>.cpp` using
  `#include "host_test.h"` and `HOST_TEST(name) { CHECK(cond, "msg"); }` (helpers:
  `host_test::hex`, `host_test::unhex`, `host_test::rnd`). Write vectors for the Python cross-check
  to `out + "/<file>"`. Do not edit host_test.cpp unless your workstream owns it (B1).
* Python: 3.11+ (station runs 3.12), type hints, dataclasses, no new third-party deps except
  `cryptography` (AES-GCM; add to ground/requirements.txt). Tests in NEW files
  `ground/tests/test_<topic>.py`. Use `pytest`. Run with
  `/tmp/claude-0/-home-user-Benchmarks/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/venv/bin/python -m pytest -q` from `ground/`.
* Checks every workstream must keep green for the files it owns:
  `make -C tools/host_test && tools/host_test/host_test <tmpdir>`;
  `bash tools/host_test/check_firmware.sh` (and with
  `LIBS_DIR=/tmp/claude-0/-home-user-Benchmarks/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/libs` — it holds RadioLib 7.8.1,
  Arduino_RouterBridge, SparkFun AS7265x (to be dropped) etc.); ground pytest.
  Phase-A workstreams: other workstreams edit other files concurrently — if a check fails ONLY
  because of a file you do not own, note it in your report and move on; do not edit that file.
* Do not run git commit/push. The orchestrator commits.

## 1 Hardware baseline (v3)

| Item | Spacecraft (UNO Q, transmitter) | Ground (VENTUNO Q, receiver) |
|---|---|---|
| Default radio | EBYTE **E22-400T33D** (SX1262, 2 W / 33 dBm, UART, DIP, 3.3-5.5 V supply, >= 4.7 V for full power, 3.3 V logic) | same module |
| Preserved radio (code kept, disabled) | E22-400T37S on E22-400TBH-02 (5 W) | same |
| Long-range option | SX126x over SPI via RadioLib 7.8.1 — reference module EBYTE **E22-400M33S** (SX1268 + PA/LNA, SPI). The T33D's UART firmware hides SF/BW, SNR, frequency error, CAD and Rx boosted gain, so those features exist only with this option | same, driven by the VENTUNO Q MCU (station_io sketch) |
| Instruments | Nicla Sense ME (0x2A, custom ARU firmware), AS7265X (0x49), RM3100 outboard (0x20) + optional inboard (0x23). **Nicla Sense Env removed entirely.** | — |
| Sensor bus | **`Wire2` = I2C3 on the UNO Q header pins A4/D18 = SDA (PC1) and A5/D19 = SCL (PC0)**, 3.3 V, 100 kHz, wired with header jumpers / soldered leads. **No sensor uses any Qwiic connector or Qwiic cable.** | — |

### 1.1 Pins (UNO Q)
* E22 UART models: `Serial3` = USART3 on header SDA (PB11 = RX <- E22 TXD) / SCL (PB10 = TX -> E22 RXD) (unchanged, verified in ArduinoCore-zephyr variant: serials = usart1, lpuart1, usart3 with router on lpuart1 -> Serial3 = usart3). Never call `Wire.begin()` (Wire = I2C2 shares PB10/PB11). M0 = D2 (PB3), M1 = D3 (PB0, NOT 5 V tolerant), AUX = D7 (PB2). 10 kΩ pull-ups to 3.3 V on M0/M1 (T33D manual: M0/M1 "very weak pull-up ... can not be suspended").
* SX126x SPI option: `SPI` (SPI2): SCK D13 (PB13), MISO D12 (PB14), MOSI D11 (PB15), NSS D10 (PB9); BUSY D4 (PA12), DIO1 D5 (PA11, interrupt), NRST D6 (PB1), RXEN D8 (PB4), TXEN D9 (PB8). All named constants in config.h.
* Sensors: Wire2 SDA A4 (PC1), SCL A5 (PC0). A4/A5 are 3.3 V pins (not 5 V tolerant per Arduino datasheet for analog pins — say "3.3 V only").

### 1.2 Pins (VENTUNO Q MCU, station_io sketch)
* Unchanged: CardKB #1 on `Wire` (header SDA/SCL, PH12/PH11), CardKB #2 on `Wire1` (Qwiic, PA8/PC9 — the CardKB is not a sensor), LEDs D5/D6/D7, buzzer A0, RCU for UART radios D2 = M0, D3 = M1, D4 = AUX.
* SX126x option: `SPI` on D10 NSS / D11 MOSI / D12 MISO / D13 SCK (check `spis` in variants/arduino_ventuno_q_stm32h5f5xx overlay at /tmp/claude-0/-home-user-Benchmarks/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/ext_core/core and document what you find), BUSY D8, DIO1 D9, NRST A1, RXEN A2, TXEN A3 (named constants; adjust if the overlay shows a conflict and document).

### 1.3 Power (documented by docs workstream)
Spacecraft: 12 V battery -> 4 A fuse -> UNO Q VIN (JANALOG pin 8) and a 12->5 V buck >= 3 A feeding the T33D VCC (5 V; 1.0 A typ / 1.2 A max at 33 dBm per manual; >= 30 % margin per manual §10) and Nicla Sense ME VIN. AS7265X + RM3100 from UNO Q +3V3 OUT. Ground: T33D on its own 5 V >= 2 A supply; UART via a 3.3 V-logic USB-UART adapter to Linux (/dev/ttyUSB0) with the RCU on the MCU (as before).

## 2 Radio models, rate codes, power codes, sensitivity

Channel grid for ALL models: f = 410.125 MHz + CH x 1 MHz, CH 0..83 (E22 manuals). Default CH 23 = 433.125 MHz.

### 2.1 E22 UART models (register protocol identical to the T37S driver: C0/C1/C2, REG0..REG3, C0 C1 C2 C3 noise query, RSSI byte REG3 b7)
| | E22-400T33D (default) | E22-400T37S (preserved) |
|---|---|---|
| Air-rate codes REG0[2:0] | 0,1,2 = 2.4k; 3 = 4.8k; 4 = 9.6k; 5 = 19.2k; 6 = 38.4k; 7 = 62.5k | same |
| Power codes REG1[1:0] | 0 = 33 dBm (factory default C0 00 00 62 00 17 and "max transmit power 33 dBm"); 1 = 30, 2 = 27, 3 = 24 dBm — VERIFY: the v1.05 manual's REG1 table was copied from the T30D (30/27/24/21 dBm). Test T-RF-07 measures it. | every code = 37 dBm |
| Abnormal-status log REG1 b2 | **not present** (bits 4..2 reserved) — never set it | present (FF FF FF 01..04) |
| Sensitivity 2.4k | -128 dBm typ (manual: -126 / -128 / -130) | -126 dBm typ |
| Other rates | estimated: S(2.4k) + 10·log10(rate/2400), source "estimated" | same |
| Supply / TX current | 3.3-5.5 V (>= 4.7 V full power); TX 1.0 A typ, 1.2 A max; RX 13 mA typ | 4.5-15 V; 1.1 A @ 12 V |
| Packet length | 240 (REG1[7:6] = 00) | same |
| Pins | M0, M1, RXD, TXD, AUX, VCC, GND (2.54 mm DIP) | TBH-02 header |

### 2.2 SX126x (SPI) LoRa profiles — the "rate code" of this model
Sensitivity is COMPUTED: S = -174 + 10·log10(BW_Hz) + NF + SNRmin(SF), NF = 6 dB (assumed, boosted gain), SNRmin = {7:-7.5, 8:-10, 9:-12.5, 10:-15, 11:-17.5, 12:-20} dB (Semtech LoRa demodulator limits). Source label "computed". Explicit header, CRC on, LDRO on when symbol time >= 16.38 ms. Sync word 0x12 (private), preamble 12 symbols.

| code | SF | BW kHz | CR | S computed dBm |
|---|---|---|---|---|
| 0 | 12 | 31.25 | 4/5 | -143.0 |
| 1 | 12 | 62.5 | 4/5 | -140.0 (DEFAULT for SX126x) |
| 2 | 12 | 125 | 4/5 | -137.0 |
| 3 | 11 | 125 | 4/5 | -134.5 |
| 4 | 10 | 125 | 4/5 | -132.0 |
| 5 | 9 | 125 | 4/5 | -129.5 |
| 6 | 8 | 125 | 4/5 | -127.0 |
| 7 | 7 | 125 | 4/5 | -124.5 |
(Compute and round to 0.1 dB in code; the table must match.) Air time from the Semtech formula
(Tsym = 2^SF/BW; n_payload = 8 + max(ceil((8PL - 4SF + 28 + 16 - 20·IH)/(4(SF - 2DE)))·(CR+4), 0); T = (n_preamble + 4.25 + n_payload)·Tsym).
Power codes (chip drive): 0 = +22, 1 = +17, 2 = +14, 3 = +10 dBm; radiated = drive + module PA gain `kSxPaGainDb` (default 11 dB for the E22-400M33S, VERIFY against the module datasheet; the module's maximum is 33 dBm). TCXO voltage `kSxTcxoV` default 1.8 V (VERIFY), DIO2 as RF switch default false with RXEN/TXEN driven by RadioLib `setRfSwitchPins` (VERIFY module wiring). Chip class selectable SX1268 (default, E22-400M33S) / SX1262.
Rx boosted gain (SX126x register 0x08AC = 0x96, RadioLib `setRxBoostedGainMode(true, true)`): ON at the ground receiver (required); flight default ON (configurable). CAD (`scanChannel`) before every transmission when LBT is enabled (count busy channels). Per-packet `getRSSI()`, `getSNR()`, `getFrequencyError()`.
Frequency offset: report raw `freq_err_hz`; ground subtracts `freq_offset_cal_hz` (static crystal offset, calibrated) to estimate Doppler; range-rate = -c · doppler / f.

### 2.3 Measured sensitivity (T-RF-03)
station.toml `[radio.sensitivity_measured]` with keys `"<MODEL>:<code>" = dBm` (empty by default). When present it overrides datasheet/estimated/computed, source "measured". `tools/sensitivity_test.py` turns a stepped-attenuation session into these lines (see §9). Nobody may fill measured values without hardware.

## 3 Telemetry (flight `tlm_packets.h` ⇄ ground `dictionary.py`), all big-endian

APIDs unchanged except: ENV now Nicla Sense ME only; new **APID_CFDP = 0x030** (CFDP PDUs, VC 3).
VCs: 0 RT engineering, 1 RT science, 2 SSR playback, **3 file delivery (CFDP)**, 7 OID (TM) / VCID 63 OID (USLP). Downlink priority VC0 > VC1 > VC2 > VC3 > OID.

### 3.1 HK (0x010, VC0) — 71 octets. Ground names in brackets.
fsw_mode u8 [fsw_mode] | sclk_partition u16 | uptime_s u32 | reset_cause u8 | fdir_flags u16 | sensor_health u16 | cmd_accepted u16 | cmd_rejected u16 | cmd_last_opcode u8 | cmd_last_status u8 | tc_frames_ok u16 | tc_frames_bad u16 | farm_state u8 | farm_vr u8 | cmd_loss_timer_s u32 | bus_temp_cC i16 [bus_temp, 0.01 degC] | radio_fault u8 | radio_fault_reports u16 | loop_max_ms u16 | loop_overruns u16 | i2c_errors u16 | ssr_fill_permille u16 [ssr_fill 0.1 %] | ssr_dropped u16 | ssr_kib u16 | vc0_backlog u16 | vc1_backlog u16 | vc2_backlog u16 | **vc3_backlog u16** | **sdls_tc_service u8** (0 NONE, 1 AUTH, 2 AE) | **sdls_tm_service u8** (0 NONE, 2 AE) | sdls_auth_fail u16 | sdls_last_sn u32 | **sdls_tm_epoch u16** | **sdls_tm_state u8** (0 off, 1 protected, 2 waiting for epoch, 3 unprotected fallback) | **cfdp_state u8** (0 idle, 1 sending SSR, 2 relaying EGSE file) | **cfdp_seq u32** | **cfdp_progress_permille u16** [cfdp_progress 0.1 %].
(`sdls_enabled` is removed; replaced by sdls_tc_service.)

### 3.2 RF (0x011, VC0) — 49 octets
**radio_model u8** (1 E22-400T33D, 2 E22-400T37S, 3 SX126x) | air_rate_code u8 | tx_power_code u8 | channel u8 | radio_cfg_ok u8 [radio_cfg_ok] (was e22_cfg_ok) | **framing u8** (0 TM, 1 USLP) | frames_sent u32 | oid_frames_sent u32 | tx_airtime_ms_total u32 | tx_duty_permille u16 [tx_duty 0.1 %] | frame_period_ms u16 | aux_timeouts u16 [radio_timeouts] | uplink_rssi_dbm i16 [uplink_rssi] | **uplink_snr_cdb i16** [uplink_snr, 0.01 dB, NA 0x7FFF] | **uplink_freq_err_hz i32** [uplink_freq_err, Hz, NA 0x7FFFFFFF] | noise_floor_dbm i16 [sc_noise_floor] | cltu_ok u16 | cltu_bad u16 | bch_corrected u16 | rx_bytes u32 | mcfc u8 | last_airtime_ds u8 [last_airtime 0.1 s] | **lbt_busy u16** (transmissions deferred by LBT/CAD) | **flags u8** [rf_flags] (b0 Rx boosted gain on, b1 LBT/CAD on).

### 3.3 MAG (0x020) 46 octets — unchanged layout; flags gains **b4 = an AS7265X lamp was lit during an RM3100 sample**.
### 3.4 ATT (0x021) — unchanged.
### 3.5 SPEC (0x022, VC1) — 119 octets
flags u8 (b0 valid, b1 white lamp, b2 IR lamp, b3 UV lamp, **b4 AGC active, b5 active-illumination spectrum (lamp-on minus lamp-off), b6 any channel saturated, b7 dark (lamp-off) frame taken this cycle**) | gain_code u8 (0 1x, 1 3.7x, 2 16x, 3 64x) | int_cycles u8 (integration = (int_cycles + 1) x 2.78 ms per the datasheet table; maximum ADC value = min(65535, 1024·(int_cycles + 1) - 1)) | **led_cur u8** (b1:0 white, b3:2 IR, b5:4 UV; 0 = 12.5 mA, 1 = 25, 2 = 50, 3 = 100 mA) | **sat_mask u32** (bit i = channel i in wavelength order) [sat_mask] | cal f32[18] (µW/cm², wavelength order 410..940; if b5: lamp-on minus lamp-off) [cal_<nm>nm] | raw u16[18] (raw counts of the frame the spectrum came from: lamp-on frame if b5, else the ambient frame) [raw_<nm>nm] | temp i8[3] [temp_0..2, -128 = n/a].
Channel order (wavelength): A410 B435 C460 D485 E510 F535 G560 H585 R610 I645 S680 J705 T730 U760 V810 W860 K900 L940. Device 0 = AS72651 (NIR: R S T U V W), 1 = AS72652 (VIS: G H I J K L), 2 = AS72653 (UV: A B C D E F); per device registers 0x08..0x13 raw, 0x14..0x2B calibrated float, in order (R,S,T,U,V,W) / (G,H,I,J,K,L) / (A,B,C,D,E,F) — the mapping used by the SparkFun library; note in a comment that the datasheet's per-register "channel X or Y or Z" captions are inconsistent.

### 3.6 ENV (0x023, VC1) — Nicla Sense ME only — 24 octets
flags u8 (b0 BMP390 pressure, b1 BSEC, b2 temperature/humidity, b3 gas resistance) | p_cPa u32 [p, 0.01 hPa] | t_cC i16 [t, 0.01 degC] | rh_cpct u16 [rh, 0.01 %] | iaq u16 [iaq] | iaq_s u16 [iaq_s] | co2eq_ppm u32 [co2eq, ppm] | bvoc_cppm u16 [bvoc, 0.01 ppm] | gas_ohm u32 [gas, ohm] | accuracy u8 [accuracy].

### 3.7 Sensor bits (HK.sensor_health and SENSORS enable mask)
b0 NICLA_ME, b1 ME_QUAT, b2 ME_ACC, b3 ME_GYRO, b4 ME_MAG, b5 ME_BARO, b6 ME_BSEC, **b7 AS7265X, b8 RM3100_OB, b9 RM3100_IB**. SENSORS mask instrument bits: 0, 7, 8, 9.
FDIR bits unchanged (bit 15 RADIO_FAULT = E22-400T37S abnormal status only).

## 4 Telecommands (flight `commands.h` ⇄ ground `dictionary.py`)
Unchanged opcodes keep their argument layouts, EXCEPT:
* `TXPWR` 0x03 u8 0..3 — meaning per model (§2).
* `AIRRATE` 0x04 u8 0..7, u16 revert_s 60..3600 — per model (§2: E22 air-rate code / SX126x profile).
* `SPECCFG` 0x16 — u8 gain (0..3, or 255 = automatic exposure), u8 int_cycles 1..255, u8 lamps 0..7 (b0 white, b1 IR, b2 UV), u8 led_cur 0..63 (b1:0 white, b3:2 IR, b5:4 UV codes). Not hazardous.
* NEW `CFDPPUT` 0x17 — u8 source 0..2 (0 = SSR contents, 1 = EGSE event log, 2 = latest EGSE CADU archive file), u16 max_kib 0..65535 (0 = whole file). Not hazardous.
* NEW `CFDPCANCEL` 0x18 — no arguments.
* NEW `RADIOOPT` 0x19 — u8 flags 0..3 (b0 LBT/CAD before transmit, b1 Rx boosted gain [SX126x only; ignored by E22]).
Ground directive NEW: `SPECREF` (capture the current active spectrum as white reference; `SPECREF CLEAR` drops it).

## 5 SDLS (CCSDS 355.0-B-2) — authentication (existing) + authenticated encryption (new)

Services: TC: NONE / AUTH (existing HMAC-SHA-256-128 format, master key, SPI 1 — keep byte-exact) / **AE (AES-256-GCM, SPI 2)**. TM: NONE / **AE (SPI 3)**, plus **null SA SPI 0** (security header present, IV all-zero, MAC all-zero, data clear) used only as the TM fallback.
Defaults: flight `VGQ_SDLS_TC = 2 (AE)`, `VGQ_SDLS_TM = 2 (AE)`; station.toml `[sdls] tc_service = "AE"`, `tm_service = "AE"`. Without a key file/header both ends run NONE and say so (as today).
* Keys (256-bit): master key = existing key file. K_tc_ae = HMAC-SHA-256(master, ASCII "VGQ1-SDLS-TC-AE"); K_tm_ae = HMAC-SHA-256(master, ASCII "VGQ1-SDLS-TM-AE"). AUTH keeps using the master key directly.
* Security header (AE and null SA): SPI u16 | IV 12 octets. IV = dir u16 ('TC' = 0x5443, 'TM' = 0x544D) | epoch u16 | 0x00000000 | sn u32. TC: epoch 0, sn = ground's persistent counter (archive/sdls_sn.txt, as today). TM: epoch = SCLK partition (EGSE boot counter, >= 1), sn = per-boot frame counter starting at 1, incremented for every protected TM frame.
* Security trailer: 16-octet GCM tag.
* AAD = (frame primary header, or USLP TFPH incl. VCF count) || security header. Plaintext = TC: the TC frame data field (one TC packet). TM v1: frame data field. USLP: the whole TFDF (TFDF header + zone).
* Layouts: TC AE: hdr 5 | SPI 2 | IV 12 | C | tag 16 | FECF 2 (max frame 64 -> packet <= 27 octets). TM AE (v1): hdr 6 | sec 14 | data 158 | tag 16 | OCF 4 | FECF 2 = 200. USLP TM AE: TFPH 8 | sec 14 | TFDF 156 (hdr 3 + zone 153) | tag 16 | OCF 4 | FECF 2 = 200. OID frames (TM VC7 / USLP VCID 63) never carry SDLS.
* Anti-replay: TC: sn must be > last accepted sn (32-bit, as today; failures never touch FARM). TM (ground): (epoch, sn) must be lexicographically greater than the last accepted; a frame failing the tag is dropped (event + counter), never decoded.
* TM nonce safety (GCM nonce reuse is catastrophic): the flight protects TM only once it has an epoch from the EGSE (`set_partition`). Until then VC0-3 frames are held (only OID frames go out). If no epoch after `kSdlsEpochWaitS = 120` s, frames go out under the null SA (SPI 0) with an EVR WARN and HK sdls_tm_state = 3; protection resumes as soon as an epoch arrives. The EGSE must issue a NEW partition (persisted boot counter + 1) on EVERY MCU boot it observes (sc_boot notification), not only when Linux starts.
* OID frames carry clear-text identification: idle data = repeating ASCII "DE <VGQ_CALLSIGN> VGQ-1 " (amateur identification stays readable). Ground extracts and shows it.
* Regulatory note (docs): encryption is generally not permitted on amateur frequencies; AE is implemented at the owner's request; use AUTH/NONE for amateur operation.

## 6 USLP (CCSDS 732.1-B-2) framing mode

Downlink framing selected by flight `VGQ_DOWNLINK_FRAMING` (0 = TM 132.0-B-3 default, 1 = USLP); uplink framing selected by station.toml `[link] tc_framing = "TC" | "USLP"`. The flight ACCEPTS both uplink formats (auto-detect: TC v1 frames start with version bits '00'; USLP TFVN '1100'). The ground AUTO-DETECTS the downlink format per frame (first nibble 0xC = USLP); station.toml `[link] tm_framing = "auto"` (or force "TM"/"USLP").
* SCID: 0x00A7 in the 16-bit USLP field (same number as the 10-bit TM SCID).
* TM-direction USLP frame, fixed 200 octets, CADU unchanged (ASM + RS(255,223) shortened + randomizer):
  TFPH 8 octets: TFVN 0b1100 (4) | SCID (16) | source/destination flag 0 (1) | VCID (6) | MAP ID 0 (4) | end-of-TFPH flag 0 (1) | frame length = 199 (16) | bypass/sequence-control flag 0 (1) | protocol-control-command flag 0 (1) | spare 00 (2) | OCF flag 1 (1) | VCF count length = 1 (3) | VCF count u8.
  TFDF header 3 octets: construction rule '000' (packets spanning fixed-length TFDZs) | UPID '00000' (Space Packets) | first header pointer u16 (0xFFFF = no packet starts in this zone).
  TFDZ 183 octets (153 with SDLS AE) | OCF 4 (CLCW) | FECF 2 (CRC-16, same polynomial as TM).
  OID frame: VCID 63, TFDF header rule '000', UPID '11111' (idle), FHP 0xFFFF, zone = idle text (§5), no SDLS.
  MCFC equivalent: the ground uses each VC's VCF count for continuity; time correlation uses a per-frame counter — keep `TimeCorrPacket.ref_mcfc` meaning "the u8 frame counter the ground sees" (TM: MCFC; USLP: the flight keeps an all-frames counter and puts it in... NO: in USLP mode ref_mcfc = VCF count of the reference frame on VC0, and the TIMECORR reference frame must be a VC0 frame). Document this.
* TC-direction USLP frame (variable length, <= 64 octets): TFPH 7 octets (+1 VCF count octet when sequence-controlled): TFVN 0b1100 | SCID | src/dst 1 (destination = spacecraft) | VCID 0 | MAP 0 | EoFPH 0 | frame length | bypass flag (0 = AD sequence-controlled -> VCF count length 1 = N(S); 1 = BD/BC -> VCF count length 0) | PCC flag (1 for COP-1 control commands Unlock / Set V(R)) | spare | OCF flag 0 | VCF count length.
  TFDF header 1 octet: rule '111' (no segmentation) | UPID '00000' for space packets, '00001' for COP-1 control commands (PCC = 1).
  Then the TC packet or the COP-1 directive (same encodings as today: Unlock = 0x00, Set V(R) = 0x82 0x00 V(R)), FECF 2. SDLS (TC) applies to the TFDF.
  CLTU/BCH unchanged. FARM-1 runs on N(S) = VCF count for AD frames.
* The CLCW format is unchanged and is carried in the OCF in both modes.

## 7 CFDP class 1 (CCSDS 727.0-B-5, unacknowledged)

* PDU header: version '001' | PDU type (0 directive, 1 file data) | direction 0 | transmission mode 1 (unacknowledged) | CRC flag 0 | large-file 0 | data-field length u16 | segmentation control 0 | entity-ID length field 0 (1 octet) | segment-metadata flag 0 | transaction-seq-number length field 3 (4 octets) | source entity u8 | transaction seq u32 | destination entity u8. Entities: 0x01 = VGQ-1 (spacecraft, both MCU and EGSE), 0x02 = DSS-Q1.
* Metadata (directive 0x07): reserved 0 | closure requested 0 | reserved 00 | checksum type 0 (modular) | file size u32 | source file name LV | destination file name LV (same name). No TLVs.
* File Data: offset u32 | data (flight <= 128 octets, EGSE-relayed <= 96 octets so the hex PDU fits the 256-octet Router Bridge inbound buffer).
* EOF (0x04): condition code (4 bits: 0 no error, 15 cancel request received) | spare 0 (4) | file checksum u32 | file size u32 (on cancel: bytes sent so far).
* Modular checksum (727.0-B-5 Annex): 32-bit sum of big-endian 4-octet words aligned on file offset (octet at offset o goes into word o & ~3, position o & 3), mod 2^32.
* Carriage: each PDU is the user data of a TM space packet APID 0x030 (with the usual CUC secondary header) on VC 3.
* Flight sender (`src/ccsds/cfdp.h/.cpp`, pure C++): generic over `CfdpSource { virtual uint32_t size(); virtual size_t read(uint32_t off, uint8_t* dst, size_t n); }`; sequence Metadata -> File Data ... -> EOF; `cancel()` emits EOF(cancel). Paced by the integrator (one PDU per frame slot while VC3 backlog < threshold).
* Sources: 0 SSR contents — the recorded packet stream in playback order (each packet self-delimiting), file name `ssr_p<partition>_<sclk>.bin`; SSR recording is PAUSED for the duration (would-be-recorded packets count as ssr_dropped); 1 EGSE event log and 2 latest CADU archive — built by the EGSE (spacecraft/vgq1_flight/python) using `cfdp_tx.py` (standalone, stdlib-only) with transaction seq >= 0x80000000, relayed via MCU RPC `cfdp_pdu(hex) -> 1 queued / 0 busy` (EGSE retries with back-off). MCU notifies the EGSE `cfdp_req` "<source>,<seq>,<max_kib>" on CFDPPUT source 1/2 and `cfdp_cancel` on CFDPCANCEL.
* Ground receiver (`ground/dssq/ccsds/cfdp.py`): per (source entity, seq) transaction; writes `archive/files/<name>` when all octets 0..size-1 arrived and checksum matches (state "COMPLETE"); EOF with gaps -> "INCOMPLETE" (missing ranges listed, partial saved as `<name>.partial` + `<name>.json`); checksum mismatch -> "CHECKSUM_FAILURE"; cancel -> "CANCELLED"; no PDU for `inactivity_s` (default 600) -> "ABANDONED". Events INFO/WARN accordingly.

## 8 AS7265X processing

* Flight driver `src/drivers/as7265x.h/.cpp` (own implementation of the datasheet's I2C virtual-register protocol on `I2cBus`, with timeouts; the SparkFun library dependency is removed): physical regs STATUS 0x00 (TX_VALID 0x02, RX_VALID 0x01), WRITE 0x01, READ 0x02; virtual: HW version 0x00/0x01, CONFIG 0x04 (b6 INT, b5:4 GAIN, b3:2 BANK mode, b1 DATA_RDY, b7 SRST write), INTEGRATION 0x05, DEVICE_TEMP 0x06, LED_CONFIG 0x07 (b5:4 LED_DRV current, b3 LED_DRV enable, b2:1 LED_IND current, b0 LED_IND enable), raw 0x08..0x13, calibrated float 0x14..0x2B, DEV_SEL 0x4F (0 AS72651 NIR master, 1 AS72652 VIS, 2 AS72653 UV). Indicator LED off. Non-blocking: `start(cfg)` (writes gain/integration/LED, BANK mode 3 one-shot), `poll()` advances a state machine across loop passes (wait DATA_RDY with timeout = 2·integration + 50 ms, then read one device per pass), `result()`.
* Pure processing `src/core/spectral.h/.cpp` (host-tested): full-scale counts; saturation mask (raw >= full-scale - 1); automatic exposure: target the brightest unsaturated channel to 25-75 % of full scale — step gain first (1x, 3.7x, 16x, 64x), then integration cycles (x2 / /2, 1..255); a saturated frame always steps down; active difference cal_on - cal_off (per channel, float) when lamps are used.
* Cycle per SPEC packet: lamp-off frame (ambient / dark) -> if any lamp configured: lamp-on frame with the configured LED currents, lamps OFF again immediately after the frame -> packet. Lamps must not be lit while the RM3100s sample (MAG flags b4 records any overlap).
* Ground `ground/dssq/spectral.py` (pure, tested): wavelength-ordered spectrum; normalized spectrum; peak wavelength (parabolic interpolation), spectral centroid, VIS (410-705 nm) and NIR (730-940 nm) sums and NIR/VIS ratio; indices on reflectance when a white reference exists, else on the spectrum (label which): NDVI (860, 680), GNDVI (860, 560), NDRE (860, 705), water-band ratio R900/R940 (labelled "WBI proxy"), PRI proxy (535 vs 560 nm; standard PRI uses 531/570 — say so); colorimetry from the 12 visible bands using the Wyman-Sloan-Shirley (2013) multi-lobe Gaussian fit of the CIE 1931 2° colour-matching functions: X, Y, Z, chromaticity x, y, CCT (McCamy 1992, valid ~2000-12500 K) and an illuminance estimate (lux ~ 683 · Σ E_i·ȳ(λ_i) with E_i in W/m² = µW/cm² x 0.01) — all labelled "approximate (18-band)"; spectral angle (degrees) to the white reference; saturation per channel; uncertainty notes from the datasheet (±12 % calibrated counts, ±10 nm wavelength accuracy, 20 nm FWHM, ±20.5° field of view, dark counts <= 5 at gain 64 / 165 ms / 25 °C; ±8.5 °C temperature sensor accuracy). Wyman CMF lobes:
  x̄ = 1.056·g(599.8, 37.9, 31.0) + 0.362·g(442.0, 16.0, 26.7) - 0.065·g(501.1, 20.4, 26.2);
  ȳ = 0.821·g(568.8, 46.9, 40.5) + 0.286·g(530.9, 16.3, 31.1);
  z̄ = 1.217·g(437.0, 11.8, 36.0) + 0.681·g(459.0, 26.0, 13.8);
  g(λ; μ, σL, σR) = exp(-½((λ-μ)/σ)²), σ = σL if λ < μ else σR. (Sanity: ȳ(555) ≈ 1.0, x̄(600) ≈ 1.06, z̄(445) ≈ 1.78 — test these.)

## 9 Configuration surface

config.h (flight): `VGQ_RADIO` (VGQ_RADIO_E22_T33D = 1 default, VGQ_RADIO_E22_T37S = 2, VGQ_RADIO_SX126X = 3); `VGQ_DOWNLINK_FRAMING` (0 TM default, 1 USLP); `VGQ_SDLS_TC`, `VGQ_SDLS_TM` (0 NONE, 1 AUTH (TC only), 2 AE); `SENSOR_WIRE Wire2`; radio defaults kRadioChannel 23, kRadioRateCode (E22: 2, SX126x: 1), kRadioPowerCode 0, kRadioLbt false, kRadioRxBoost true; SX126x pins/TCXO/PA gain/chip; kSdlsEpochWaitS 120; CFDP segment sizes.
station.toml (ground): `[radio] model = "E22-400T33D"` (| "E22-400T37S" | "SX126x"), existing UART keys, `freq_offset_cal_hz = 0.0`, `[radio.sx126x]` (chip, tcxo_v, rx_boosted_gain = true, cad_before_tx = true, sync_word = 0x12, preamble = 12), `[radio.sensitivity_measured]` (empty); `[link] tm_framing = "auto"`, `tc_framing = "TC"`; `[sdls] tc_service = "AE"`, `tm_service = "AE"`, existing key_file / spi; `[cfdp] inactivity_s = 600`, `files_dir = "files"` (under archive_dir); `[spectral] white_ref_file = "spec_white_ref.json"`.
tools/sensitivity_test.py: input = archive dir + a steps CSV (`start_utc,end_utc,atten_db,rate_code[,model]`) or `--live` (prompts the operator, timestamps steps); output = per step: frames expected vs received (from frame period / counters), FER, mean RSSI/SNR; the received power at FER = 10 % by interpolation, as TOML lines for `[radio.sensitivity_measured]`; refuses to output a value if FER never crossed 10 %.

## 10 Ground snapshot keys added for the dashboard (gds.snapshot())
`radio`: {model, packet_mode, rate_code, rate_label, bps, sf, bw_khz, cr, power_dbm, channel, freq_mhz, rx_boost, cad, sensitivity_dbm, sensitivity_source}; `rf.packet`: last {rssi_dbm, snr_db, freq_err_hz, t}; `rf.snr_db`, `rf.freq_err_hz`: window stats {mean, sem, min, max, n}; `rf.doppler`: {offset_cal_hz, doppler_hz, range_rate_m_s}; `link`: {tm_framing_seen, tc_framing, sdls: {tc_service, tm_service, tm_epoch, tm_last_sn, tm_frames_ok, tm_auth_fail, tm_replay, tm_unprotected, tc_next_sn}, ident (callsign text from OID frames)}; `cfdp`: {transactions: [{source, seq, name, size, received, progress, state, checksum_ok, missing, path, t_start, t_end}]}; `science.spectral`: output of spectral.py for the latest SPEC packet (+ white_ref info).

## 11 Reference documents (in /root/.claude/uploads/5ee836b4-fd12-52ad-8530-d6659d1475b9/, text extracts in /tmp/claude-0/-home-user-Benchmarks/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/ds2/ and .../scratchpad/*.txt)
E22-400T33D V2.1 user manual v1.05 (ds2/b8c54790-...txt); E22-xxxT37S manual v1.5 (scratchpad/t37s.txt); E22-400TBH-02 manual (scratchpad/tbh02.txt); AS7265x datasheet v1-04 (ds2/817df498-...txt); RM3100 user manual r06 (ds2/f8597506-...txt); Nicla Sense ME ABX00050 (scratchpad/f0d71b6c-...txt); Arduino UNO Q / VENTUNO Q docs (scratchpad/arduino_docs/dc/content/hardware/...); ArduinoCore-zephyr (scratchpad/ext_core/core); RadioLib 7.8.1 (scratchpad/libs/RadioLib). CCSDS: 131.0-B-5, 132.0-B-3, 133.0-B-2, 231.0-B-4, 232.0-B-4, 232.1-B-2, 301.0-B-4, 355.0-B-2, 732.1-B-2, 727.0-B-5. NIST SP 800-38D (GCM), FIPS-197 (AES).

## 12 Shared seam already in the tree
`spacecraft/vgq1_flight/sketch/src/ccsds/frame_security.h` defines `ccsds::FrameSecurity`
(header_len / trailer_len / protect(frame, aad_len, data_len)). The SDLS workstream implements the
TM AE / null-SA protector as a subclass; the USLP/TM framing workstream makes both framers call it
(`build_frame(..., FrameSecurity* sec = nullptr)`, data field shrinks by header_len + trailer_len).
Do not change this header without saying so in your report.
`tools/host_test/Makefile` builds every `src/ccsds/*.cpp`, `src/core/*.cpp`, `tools/host_test/test_*.cpp`
with `-I<src>` (so `#include "ccsds/xyz.h"` / `"core/xyz.h"` work from tests).

## 13 Advanced spectroscopy (SUPERSEDES 3.5, the SPECCFG line of 4 and section 8 where they differ)

Goal: use every AS7265X capability the datasheet documents and add instrument-level capabilities on
top. Every heuristic is labelled heuristic, every approximation is labelled approximate, thresholds
are named constants (config/station.toml) — never present an invented number as a measured fact.

### 13.1 Chip features used (datasheet v1-04)
Raw u16 + calibrated float for 18 channels; GAIN 1x/3.7x/16x/64x; integration 1..255 cycles;
BANK modes 0/1 (4-channel), 2 (6-channel continuous), 3 (one-shot); DATA_RDY (and the optional INT pin,
`kPinSpecInt` = A2 on the UNO Q, -1 = not wired -> polling); LED_DRV per device 12.5/25/50/100 mA as an
electronic shutter (white = AS72651 dev 0, IR = AS72652 dev 1, UV = AS72653 dev 2 on the SparkFun board,
as the existing code/library maps them — keep that mapping); LED_IND 1/2/4/8 mA per device ("measurement
in progress" marker / low-power illumination); per-device temperature; DEV_SEL slave-available flags
(AS72652/AS72653 present) for health and partial-spectrum flags; HW version 0x00/0x01 and FW version
0x02/0x03 at boot (EVR); soft reset (CONFIG b7) for recovery after repeated timeouts; COEF calibration
heap 0x50-0x55 READ-ONLY on demand (SPECDIAG) — never COEF_WRITE (it writes persistent memory); firmware
update registers are NOT used.

### 13.2 Acquisition modes (flight, non-blocking state machines, PA-off windows, lamps never lit during RM3100 samples)
* 0 SURVEY (default): one exposure, automatic exposure (AGC) when gain = 255.
* 1 HDR: n = 2..4 exposures bracketed around the AGC exposure in factors of ~4 of exposure (gain x time);
  per channel take the exposure with the highest raw count below 90 % of full scale; report its calibrated
  value; uncertainty from the model sigma_counts^2 = q^2/12 + dark^2 (q = 1 count; dark = 5 counts, datasheet
  maximum, at gain 64 — scale by gain ratio) converted to µW/cm² through that channel's cal/raw ratio.
* 2 LOCKIN: n = 1..16 lamp-on / lamp-off pairs with the configured lamps and LED_DRV currents ("electronic
  shutter", datasheet): output = mean(on - off), uncertainty = sample std / sqrt(n) (n = 1: model sigma).
  Rejects ambient light and slow drifts — the active-illumination reflectance mode.
* 3 FLUOR: UV LED excitation only (lamps forced to UV): mean(on - off) like LOCKIN; channels <= 460 nm
  flagged "excitation band" in the packet's sat_mask high bits? NO — use flags: set mode = 3; the ground
  masks <= 460 nm for fluorescence metrics.
* 4 BURST: n = 1..60 s of continuous BANK mode 2 sampling at `kSpecBurstCycles` (default 1 -> 2.78 ms
  integration per bank, two banks per 6-channel set), raw counts only, as fast as the bus allows; samples go
  to SPECBURST packets (VC1, recorded in the SSR like all science) with millisecond time offsets; achieved
  sample rate reported. Uses INT when wired.
* Mode cadence: SPEC packets follow the existing PKTRATE/mode table; LOCKIN/FLUOR/HDR take several
  exposures — the state machine spreads them over loop passes; a cycle that cannot finish before the next
  packet is due simply finishes late (no overlap, no blocking).

### 13.3 Onboard spectral intelligence (src/core/spectral.*, pure C++, host-tested)
* Log-encoding ("spectral compression") of signed float values to u16: NaN -> 0xFFFF; s = sign; a = |v|;
  a < 1e-4 -> m = 0 (value 0); else m = min(32766, 1 + round(1024*log2(a/1e-4))); code = s<<15 | m;
  decode = (s ? -1 : 1) * 1e-4 * 2^((m-1)/1024) for m >= 1, 0 for m = 0. Unit µW/cm². Range 1e-4..4.29e5,
  step 0.068 %. Identical encoder/decoder in Python (ground/dssq/spectral.py) with a round-trip test and a
  shared vector file.
* Light-source classifier (heuristic, features documented in code): classes 0 UNKNOWN, 1 DARK, 2 DAYLIGHT,
  3 INCANDESCENT/HALOGEN, 4 WHITE LED, 5 FLUORESCENT, 6 NARROWBAND, 7 OWN LAMP (active modes); features
  e.g. total signal, NIR/VIS ratio, blue-peak (460/560) ratio, spectral flatness, peak-to-mean; thresholds in
  one named table; confidence 0..100 from the decision margin. Ground has a richer explainable version.
* Change detection: spectral angle between the current normalised spectrum and an EWMA baseline
  (alpha `kSpecBaselineAlpha`); EVR 0x0701 "spectral event" when the angle exceeds the threshold
  (`kSpecEventCdeg` default 500 = 5.00 deg, settable by SPECBASE), rate-limited (>= 60 s between events).
* Spectro-attitude fusion: sensor boresight in body axes `kSpecBoresightBody` (default {0,0,1}, VERIFY
  against the mounting) rotated by the Nicla fusion quaternion (fallback TRIAD) into NED -> boresight zenith
  angle (deg from local vertical up) and azimuth (deg from north, 0..360) in the packet.

### 13.4 SPEC packet v3.1 (0x022, VC1) — 129 octets (replaces 3.5)
mode u8 [spec_mode] | flags u8 (b0 valid, b1 white, b2 IR, b3 UV lamp used, b4 AGC, b5 active (on-off)
difference, b6 any channel saturated, b7 attitude valid) | gain_code u8 | int_cycles u8 (base exposure;
time = (int_cycles + 1) x 2.78 ms; full scale = min(65535, 1024*(int_cycles+1) - 1)) | led_cur u8 |
n_frames u8 (exposures merged or on/off pairs; 1 for SURVEY) | sat_mask u32 (bit i = channel i, wavelength
order) | spec_log u16[18] [spec_<nm>nm, decoded µW/cm²] | unc_log u16[18] [unc_<nm>nm, 1-sigma µW/cm²] |
raw u16[18] [raw_<nm>nm] | temp i8[3] [temp_0..2] | bore_zen_cdeg i16 [bore_zen, 0.01 deg, NA 0x7FFF] |
bore_az_cdeg i16 [bore_az, 0.01 deg, NA 0x7FFF] | class_id u8 [spec_class] | class_conf u8 [spec_conf, %] |
change_cdeg u16 [spec_change, 0.01 deg, NA 0xFFFF].
The ground dictionary needs a decoder hook for the log fields (field code 'H' with a custom
decode function — add a small generic mechanism, e.g. a 7th tuple element or a per-field transform map).

### 13.5 SPECBURST packet (APID 0x024, VC1) — variable
t0_ms u32 (SCLK milliseconds of the first sample of the burst) | rate_dhz u16 (achieved rate, 0.1 Hz) |
gain_code u8 | int_cycles u8 | n u8 (1..3 samples in this packet) | n x { dt_ms u16 (since t0), raw u16[18] }.

### 13.6 Commands (supersede the SPECCFG entry in section 4)
* `SPECCFG` 0x16: u8 gain 0..3 | 255 (AGC), u8 int_cycles 1..255, u8 lamps 0..7, u8 led_cur 0..63,
  u8 ind 0..4 (indicator LED: 0 off, 1..4 = 1/2/4/8 mA).
* `SPECMODE` 0x1A: u8 mode 0..4, u8 n 1..60 (HDR exposures 2..4, LOCKIN/FLUOR pairs 1..16, BURST seconds 1..60; validated per mode).
* `SPECBASE` 0x1B: u16 threshold_cdeg 0..9000 (0 = only reset the change-detection baseline).
* `SPECDIAG` 0x1C: u8 device 0..2, u8 n_sub 0..32: EVRs 0x0703 with HW/FW version, slave flags,
  temperature and COEF words for sub-addresses 0..n_sub-1 (read-only).
EVRs: 0x0701 spectral event, 0x0702 light-source class changed, 0x0703 SPECDIAG lines, 0x0704 burst
start/end (samples, rate), 0x0705 spectrometer recovered by soft reset.

### 13.7 Ground analytics (ground/dssq/spectral.py; supersedes section 8 ground list, keeps everything in it)
Decode log fields; spectrum ± uncertainty; reflectance from a white reference (SPECREF) and a named spectral
library (SPECLIB SAVE <name> / SPECLIB DEL <name>; best spectral-angle match with angle); indices WITH
propagated uncertainty (NDVI, GNDVI, NDRE, WBI proxy, PRI proxy); radiometry under ONE stated assumption —
each calibrated channel value is the band irradiance through a 20 nm FWHM filter, so spectral irradiance
≈ value / 20 nm, integrated with the trapezoid rule over channel centres: VIS/NIR totals, PAR 400-700 nm as
PPFD (µmol m^-2 s^-1, photon energy hc/λ, Avogadro), illuminance (683 lm/W with ȳ), all labelled
approximate; colorimetry XYZ / xy / CCT and Duv by a Planckian-locus search in CIE 1960 (u, v) (blackbody
spectra through the same 12 visible bands and Wyman CMFs, so the comparison is self-consistent), McCamy as
a cross-check; light-source classification (explainable features, compared with the onboard class);
fluorescence metrics in FLUOR mode (F680/F730 red/far-red ratio as a chlorophyll-fluorescence proxy, blue-
green index; <= 460 nm masked as excitation band); history ring (last 300 spectra) for a waterfall; BURST:
per-channel time series, achieved rate, FFT magnitude up to Nyquist, transient detection; sky map: binned
mean illuminance/PPFD vs boresight zenith/azimuth (polar bins); die-temperature drift (per-device slope of
ambient signal vs temperature, informational); event log (onboard EVR 0x0701/0x0702 + ground events).
Snapshot key: science.spectral (+ spectral_history, spectral_burst, spectral_skymap, spectral_library).

## 14 SX126x bulletproofing (Semtech DS SX1261/2 rev 2.2, user-supplied; text at
## /tmp/claude-0/-home-user-Benchmarks/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/ds3/sx1262.txt and ds3/sx1262_tables.txt)
Applies to spacecraft drivers/sx126x.* and the station_io SX126x code (same rules both ends).
* Sensitivity, Rx boosted gain, split RF paths, switch loss excluded (Table 3-8): BW 10.4 kHz SF7 -134,
  SF12 -148; BW 125 kHz SF7 -124, SF12 -137; BW 250 kHz SF7 -121, SF12 -134; BW 500 kHz SF7 -117, SF12 -129 dBm;
  Rx power-saving gain BW 125 SF12 -133 dBm (so boosted gain buys ~4 dB). Profiles whose (SF, BW) appear in
  this table use the datasheet value with source "datasheet"; the others stay "computed" (contract 2.2).
  Note the computed model agrees with the table at BW125/SF12 (-137) and BW125/SF7 (-124.5 vs -124).
* Maximum tolerated TX-RX frequency offset (FERR_L): +-25 % of BW and SF12 +-50 ppm, SF11 +-100 ppm,
  SF10 +-200 ppm — the tighter applies. At 433 MHz with SF12/BW31.25 that is +-7.8 kHz (18 ppm). The ground
  raises a WARN when |freq_err| exceeds 50 % of that limit and an ALARM above 80 %; both ends need a TCXO
  for BW < 62.5 kHz (document).
* Rx boosted gain (reg 0x08AC 0x96 vs 0x94 default) is NOT retained across warm sleep / RxDutyCycle unless
  registers 0x029F = 0x01, 0x02A0 = 0x08, 0x02A1 = 0xAC are set (§9.6): use RadioLib
  setRxBoostedGainMode(true, true) (persist) and verify in RadioLib source that persist writes exactly these;
  re-assert after every reset/cold start; read 0x08AC back and expose the verified state.
* Known limitations (§15) — confirm RadioLib 7.8.1 applies each workaround in the code path we use, else
  apply it ourselves: 15.1 BW500 modulation quality (0x0889 bit 2 = 0 for BW 500 kHz, 1 otherwise, before
  every TX); 15.2 PA clamping (TxClampConfig 0x08D8 bits 4-1 = 1111 after POR / cold-start wake, SX1262);
  15.3 implicit-header RX timeout (stop RTC 0x0902, clear 0x0944 after any RX with timeout); 15.4 inverted IQ
  (0x0736 bit 2). We use explicit header and standard IQ, but keep the fixes where RadioLib provides them.
* TCXO: when the 32 MHz reference is a TCXO the POR calibration fails — call the full calibration after
  SetDIO3AsTCXOCtrl (check RadioLib setTCXO/begin does), VDDop > VTCXO + 200 mV; image calibration for the
  430-440 MHz band (RadioLib setFrequency calibrates the image band — verify it covers 433 MHz).
* BUSY: every SPI command waits for BUSY low with a bounded timeout (RadioLib Module with BUSY pin); a BUSY
  stuck high, a command error status or SPI failure counts a radio fault: reset via NRST, re-init, re-apply
  configuration, EVR, FDIR RADIO_CFG — never hang the loop.
* OCP: SX1262 default after SetPaConfig is 140 mA (60 mA at POR); set the current limit explicitly after
  configuring power. TX power drops ~2 dB at VBAT 2.7 V (supply the module properly).
* TX timeout: always start TX with a timeout = 1.5 x computed air time (+ margin) so a missing TxDone IRQ can
  never wedge the state machine; RX: continuous with header/CRC error IRQs counted.
* Packet status per packet: RSSI (packet), SNR, frequency error read right after RxDone and before any other
  command; CRC errors counted and reported, payload discarded.

## 15 Board-powered radio option (owner's decision: the T33D will be tested powered from the boards)
* Documented as a SUPPORTED configuration next to the external-supply one (docs 00/03/08):
  spacecraft T33D VCC <- UNO Q +5V (JANALOG pin 5) with the UNO Q powered from 12 V on VIN (JANALOG pin 8);
  ground T33D VCC <- VENTUNO Q +5V_LIMITED (shield header 5V, 2.8 A limited, 4.75-5.25 V per Arduino);
  never a 3.3 V pin; 470-1000 uF low-ESR + 100 nF at the module VCC/GND; short thick wires. Facts to
  state: UNO Q 5V_SYS sits behind a Schottky OR diode (Arduino measured 0.35 V at 1 A, 0.39 V at 2 A); the
  T33D needs >= 4.7 V for full power and still works down to 3.3 V at reduced power (manual); Arduino warns
  current-limited peaks may reset the board. Verification test T-HW-07 in docs/08: VCC at the module during
  a long transmission, HK reset_cause / sclk_partition, RSSI comparison.
* Flight brownout derating: if the MCU boots with reset cause BROWNOUT, the radio starts at a reduced
  power code (`kBrownoutPowerCode`, default 2 = the third T33D code) instead of kRadioPowerCode, EVR WARN
  0x0608 "brownout reset: TX power derated (code N) - check radio supply"; the operator restores power with
  TXPWR. HK already reports reset_cause; ground raises a YELLOW alarm on a BROWNOUT reset cause.

## 16 Owner directives added later (SUPERSEDE earlier sections where they differ)
### 16.1 No Qwiic connectors ANYWHERE in the system
Not on the UNO Q, not on the VENTUNO Q, not on any breakout (sensor boards are wired through their header
pins / solder pads), not for the CardKB. Consequences:
* Spacecraft: unchanged from section 1 (sensors on Wire2 = I2C3, UNO Q header A4/D18 = SDA PC1, A5/D19 = SCL
  PC0). Arduino UNO Q datasheet (ABX00162, modified 17/06/2026): "For A4/A5 when used as I2C3 (PC1/PC0),
  use pull-ups to 3.3 V only ... not 5 V-tolerant in any mode" -> every I2C pull-up on that bus must go to
  3.3 V (SparkFun AS7265X and RM3100 breakouts pull up to their 3.3 V supply, the Nicla Sense ME to VDDIO_EXT
  = 3.3 V); never power a sensor breakout from 5 V if its pull-ups go to its supply.
* Ground: CardKB #2 is REMOVED (the VENTUNO Q's only other MCU I2C bus, I2C2 PF1/PF0, is on the 60-pin
  high-density JMISC carrier connector; two CardKBs cannot share one bus: fixed address 0x5F). The second
  operator authorises with a physical AUTH KEY: a key switch or push-button between VENTUNO Q A4 (PE14,
  INPUT_PULLUP, active LOW, debounced 30 ms, must be held >= 0.5 s) and GND -> same "AUTH" line to the GDS as
  before; the web AUTH button remains. (Alternative documented only as an option: a TCA9548A I2C multiplexer
  on the header bus to keep a second CardKB — not built.)
### 16.2 UNO Q JANALOG pin 5 is "+5V USB VBUS — 5 V supply (pass-through), power only" (net 5V_USB_VBUS,
confirmed in the ABX00162 schematic), NOT the 5V_SYS diode-OR output (the older web datasheet said so).
5V_USB_VBUS is the USB-C VBUS: it carries 5 V when the UNO Q is powered through USB-C (pass-through from the
source, no Schottky drop), and is driven from 5V_SYS only when the board acts as USB host/OTG (P-MOSFET Q2801,
gated). Therefore the board-powered spacecraft option of section 15 becomes: power the UNO Q through USB-C
from a regulated 5 V supply that can deliver the board's own 5 V / 3 A PLUS the radio's 1.2 A peaks
(e.g. a 12 V -> 5 V >= 5 A buck with a USB-C lead), and take the T33D VCC from JANALOG pin 5; do NOT power the
UNO Q from VIN in this option (pin 5 may then be unpowered). Measure pin 5 under transmit (>= 4.7 V for full
power). The external-supply option (section 1.3) is unchanged.

### 16.3 Ground keyboard (owner's latest instruction — SUPERSEDES 1.2 and 16.1's ground bullets)
* ONE CardKB only, on the VENTUNO Q **Qwiic port = `Wire1` (I2C3, PA8 SCL / PC9 SDA, 3.3 V)** — the owner
  explicitly allows the Qwiic port for the CardKB. The "no Qwiic" rule stays in force for every sensor.
* ALL CardKB #2 logic is removed (station_io sketch, frontpanel.py, gds, dashboard, docs). The AUTH key switch
  proposed in 16.1 is NOT built. With the two-person rule enabled the second operator authorises from the
  web dashboard AUTH button only. The header SDA/SCL bus (`Wire`) is unused by the station.
* CardKB electrical note for docs: M5Stack specifies a 5 V supply for the CardKB v1.1 and does not publish
  its I2C logic level or pull-ups. Option A (try first, no extra parts, nothing can be over-voltaged): Grove
  -> Qwiic cable, CardKB powered from the Qwiic 3.3 V; it may not run reliably below its rated 5 V. Option B:
  CardKB on 5 V through a BSS138 bidirectional level shifter (LV = Qwiic 3.3 V, HV = 5 V) to the Qwiic port.
  Detect presence at 0x5F on Wire1 at boot and report it (as today for CardKB #1).

## 17 Latest owner directives (SUPERSEDE everything above where they differ)
### 17.1 Ground keyboard — final
Exactly ONE CardKB, connected with loose jumper wires from VENTUNO Q HEADER pins to the CardKB's Grove (HY2.0-4P)
connector through an adapter: `Wire` = SDA D20 (PH12) / SCL D21 (PH11), GND, and 3.3 V (or 5 V through a BSS138
level shifter, see 16.3 electrical note). The Qwiic sockets of BOTH boards are not used at all, for anything.
All CardKB #2 logic removed; no AUTH key; with the two-person rule the second operator authorises in the web UI.
### 17.2 Repository minimalism
No new directories. As few new files as possible; consolidate where it keeps the code clear. Pure-C++ algorithm
code lives in src/fsw/ (host-tested automatically: tools/host_test/Makefile compiles every src/fsw/*.cpp except
flight.cpp and status_display.cpp, every src/ccsds/*.cpp, and src/drivers/radio.cpp). Section 0's `src/core/` is
withdrawn. Ground radio: keep to radio/e22.py + one new radio/sx126x.py; model registry, LoRa maths and the
RxPacket type go in the existing dssq/link.py; the T-RF-03 analysis becomes a `sensitivity` subcommand of the
existing tools/link_budget.py (no tools/sensitivity_test.py).
### 17.3 Engineering standard ("as if built by a space agency")
C++ flight code follows NASA/JPL "Power of Ten" discipline: no recursion; every loop has a fixed upper bound; no
heap allocation after initialisation (SSR heap at boot only); every return value checked; range checks on every
value arriving from outside (telecommands, radio, sensors, Bridge); smallest variable scope; no undefined
behaviour (no type-punning casts on buffers — use memcpy / put_u16 helpers); warning-free with
-Wall -Wextra -Wshadow. Python: typed, no bare except, validated inputs, deterministic tests. Professional
comments: a module header (purpose, governing standard/datasheet section, units), contracts on non-obvious
functions, "why" comments; no noise. Formatting is applied repository-wide at the end (clang-format / ruff).
### 17.4 Ground web UI
A very advanced, polished operator web UI on the VENTUNO Q (single self-contained dashboard.html, no external
CDN at runtime) presenting all advanced spectroscopy (section 13.7) plus link/radio/security/CFDP status.

## 18 Arduino App Lab packages (owner request) — two standalone importable zips
Model: the owner's LINPACK packages (scratchpad/linpack_unoq/uno-q-linpack, linpack_ventunoq/ventuno-q-linpack):
top-level folder = the App; app.yaml (name, icon, description; no Bricks); python/main.py (App Lab entry point),
python/requirements.txt (installed by App Lab into the App's Python 3.13 container), python/<package>/;
sketch/sketch.ino + sketch/sketch.yaml (+ headers; src/ subfolders are legal Arduino sketch layout); host/ with an
install_*.sh for anything that must run on the host (LINPACK: "App Lab runs the App's Python in a Docker container.
The container only gets the device nodes App Lab whitelists: DRM, DMA-heap, media/V4L2, ALSA, tty and the FastRPC
nodes"); README.md; data/ for outputs. Import: App Lab -> My Apps -> open any App -> ▼ -> Import App (lands in
~/ArduinoApps/<folder>).
* dist/vgq1-spacecraft-unoq.zip  (folder vgq1-spacecraft): flight software sketch + EGSE python (CADU archive, boot
  partition, SDLS SN restore, CFDP EGSE sender) — the UNO Q App.
* dist/dssq-groundstation-ventunoq.zip (folder dssq-groundstation): station I/O sketch (CardKB on header Wire, lamps,
  RCU, optional SX126x) + the ground data system python (GDS, web UI) run by App Lab in the container (pyserial on the
  whitelisted tty for the E22 USB-UART) + host/install_station.sh and the systemd unit as the documented fallback /
  always-on mode (venv on Ubuntu 24.04) if the container cannot serve the web UI on the LAN or open the serial port.
  Whether the App container exposes the web UI port is NOT verified -> docs say how to check and the fallback.
* The repository layout should BE these two App folders (plus tools/, docs/, the Nicla firmware) so the zips are
  plain archives of folders — consolidation in the final phase. Tests/tools stay out of the zips.
* Secrets never enter a zip: packaging refuses if sdls_key.h / sdls_key.hex are present in the tree being zipped
  and the docs explain generating keys after import.
* tools/package_apps.py (or a Makefile target) builds both zips reproducibly; the built zips are committed under dist/
  and also handed to the owner.
* Entry-point pattern (from the LINPACK main.py the owner supplied twice, byte-identical): `from arduino.app_utils import
  App, Bridge`; put the App's python/ folder on sys.path so the package next to main.py imports; persistent data under
  `<app>/data` ("App Lab reserved folder for persistent app data"); `App.run(user_loop=...)` with a short sleeping
  loop. The ground App's python/main.py therefore starts the GDS (HTTP server thread, radio I/O, archive under
  <app>/data) and talks to its own sketch through `arduino.app_utils.Bridge` inside the container; the host-mode
  (systemd) path keeps using the arduino-router-bridge client on unix:///var/run/arduino-router.sock. Both through one
  small adapter so the GDS code is identical. sketch.yaml format: profiles.default.platforms [- platform:
  arduino:zephyr], libraries [...], default_profile: default. app.yaml: name, icon, description only.

## 19 AS7265X board facts (SparkFun SEN-15050, product repo sparkfun/Qwiic_Spectral_Sensor_AS7265x)
* No separate programming: the ams firmware lives in the on-board serial flash (datasheet: firmware "must be loaded
  into a serial flash"; SparkFun ships the board working over I2C with its library; the repo's
  Firmware/AS7265x/AS7265_complete_moonlight.bin + EEPROM programmer are recovery resources).
* SparkFun Firmware/AS7265x/Readme.md: the board uses first-generation "Moonlight" ICs, "the !INT and !RST-ADDR pins are
  swapped". Therefore `kPinSpecInt` defaults to -1 (INT not wired; DATA_RDY polling) and the docs say to leave INT
  unconnected. Power from 3.3 V only (pull-ups to its supply; UNO Q A4/A5 are 3.3 V-only).
* SparkFun hookup guide (owner-supplied, scratchpad/ds5/hg.txt) confirms: "These sensors from AMS ... ship without
  firmware. The firmware to drive the system is loaded onto a 4Mbit EEPROM that is read by the AS72651 at power on" ->
  no separate programming. I2C is the default; the four solder pins are GND / 3.3V / SDA / SCL; "3.3V compatible".
  The front "I2C" jumper carries the SDA/SCL pull-ups (leave closed = pull-ups to 3.3 V); JP2 on the back stays OPEN
  (closing it plus opening I2C switches to UART/AT commands — never do that here). enableBulb: 0 = White (5700 K),
  1 = IR (875 nm), 2 = UV (405 nm). getDeviceType() 0x41, getHardwareVersion() 0x40, firmware major "currently 0x0C"
  (the guide reads 0x00 as device type and 0x01 as HW version — the datasheet labels them the other way round; the
  driver records both bytes and does not gate detection on them). The guide labels AS72651 "UV" and AS72653 "NIR";
  the datasheet channel tables and the SparkFun library code say AS72651 = NIR (R..W), AS72652 = VIS (G..L),
  AS72653 = UV (A..F) — datasheet + library are authoritative.

## 20 Concatenated coding on the SX126x path ("RS rescue")
With the E22 UART modules the module discards LoRa-CRC failures internally, so RS(255,223) rarely gets to correct.
On the SX126x SPI path we control the modem: keep LoRa CRC enabled (it still flags errors) but DELIVER packets that fail
the LoRa CRC instead of dropping them, flagged crc_ok = false (RadioLib readData() returns
RADIOLIB_ERR_CRC_MISMATCH while still filling the buffer — verify in RadioLib 7.8.1 source), when the payload length is
exactly one CADU (236 octets). The ground runs the normal ASM check + RS decode on them: a frame whose RS decode
succeeds is accepted and counted as "RS-rescued" (rxstats + dashboard + Prometheus), one that fails is counted and
dropped. This makes LoRa's Hamming code (inner) + RS (outer) a concatenated code, as on deep-space links. Same on the
spacecraft for uplink CLTUs (BCH per codeblock + FECF already protect them; deliver CRC-failed uplink packets to the
CLTU decoder and count BCH-corrected rescues). Integration phase wires this; it must never bypass the FECF/RS/BCH checks.

## 21 Ground keyboard model (owner): M5Stack "CardKB Mini Keyboard Programmable Unit (MEGA328P)", SKU U035 (EOL; the
## v1.1 MEGA8A is its successor). Owner's product page text: scratchpad/ds6/kb.txt.
* I2C address 0x5F; 50 keys; HY2.0-4P (Grove) connector, 20 cm cable included; Sym / Shift / Fn combinations; one RGB LED.
* PORT.A pin map on the page: Black = GND, Red = 5V, Yellow = SDA, White = SCL (M5Stack colour order — tell the owner to
  confirm against the CardKB's own silkscreen/connector because adapter cables differ).
* Supply: the page specifies 5 V on PORT.A; logic level / pull-ups not published. ATmega328P (Microchip datasheet speed
  grades, general knowledge — label as such): 0-10 MHz at 2.7-5.5 V, 0-20 MHz at 4.5-5.5 V; the CardKB's clock source is
  not documented, so 3.3 V operation is "try first" (nothing can be over-voltaged), 5 V + BSS138 shifter if keys are
  erratic (unchanged advice, 16.3/17.1).
* Firmware: the CardKB ships programmed by M5Stack ("Programmable Unit" means it CAN be reprogrammed); no action needed.
* Station sketch already accepts Enter = 0x0D or 0x0A, Backspace = 0x08 or 0x7F, ESC 0x1B, printable 0x20-0x7E — valid for
  both CardKB versions; keep it that way.

## 22 Owner-supplied references: RM3100-Driver-master (QRM3100, I2C) and arduino/nicla-sense-me-fw (main)
### 22.1 RM3100 — driver verified against the PNI manual, no change
PNI manual §5 says reads set bit 7 ("effectively adding 0x80"), but its own I2C transaction example §5.8.4 writes the MX2
pointer as 0x24 (bits 0010 0100) before the read; on I2C the R/W bit is in the address byte, the 0x80 rule is the SPI
command byte. Our src/drivers/sensors.cpp (POLL 0x70 at 0x00, CCX..CCZ from 0x04, STATUS 0x34 bit 7 DRDY, MX..MZ 9 octets
from 0x24, 24-bit two's complement, REVID 0x36) matches the manual's I2C example exactly. QRM3100 uses 0xA4/0xB4 —
also accepted by the chip; keep ours (manufacturer's I2C example). Optional improvement for Phase B/C: BIST at boot
(0x33, STE + timeout/LR periods, run via POLL, read ZOK/YOK/XOK) reported in the instruments EVR.
### 22.2 Nicla Sense ME (Arduino_BHY2 1.0.8 in the repo = the version we pin)
SensorID.h gives MAG scale factor 1.0 (the library hands out raw counts); the physical scaling is not in the library.
Arduino_BHY2 exposes each virtual sensor's configured dynamic range through getConfiguration() (bhy2_get_virt_sensor_cfg;
our ARU firmware already scales accel/gyro as raw/32768 * range). Phase B: the ARU firmware reads the magnetometer's
range the same way and sends the BMM150 field in 0.1 uT on page 0 bytes 25..30 (int16; flag bit set when the range was
read, else the documented fallback 1/16 uT/LSB is used and flagged); the flight software then uses the value directly.
Test T-SC-06 (rotate; |B| constant and equal to the site WMM total field) stays as the physical confirmation.
Phase C reviewers must also cross-check nicla_sense_me_aru.ino against this repository's Arduino_BHY2 sources (sensor IDs,
SensorXYZ / SensorQuaternion / SensorBSEC field scaling in DataParser.cpp: BSEC b-VOC x0.01, comp_t /256, comp_h /500).
### 22.3 Bosch datasheets supplied by the owner (BHI260AP ds000, BMM150 ds001, BMP390 ds002, BME688 ds000) — APPLIED
* BHI260AP Table 88 (FIFO event IDs): Accelerometer/Gyroscope/Magnetometer Corrected (IDs 4/13/22) are int16 3D
  vectors "scaled to current dynamic range of sensor"; §13.2.8: dynamic range units g, deg/s, uT; scale =
  range / 2^15 for signed 16-bit. Virtual Sensor Configuration parameter 0x0500+ID, octets 0x0E..0x0F = dynamic
  range; bhy2_get_virt_sensor_cfg maps that to .range, which Arduino_BHY2 getConfiguration() returns.
  => ARU now sends the BMM150 field in 0.1 uT (page 0 bytes 25..30), page 0 flag b4 = scale read from the BHI260
  (else fallback 1/16 uT/LSB, retried once per second); flight config.h kAruMagUtPerLsb = 0.1f. DONE (not yet committed).
* Barometer (ID 129): 24-bit unsigned, 1/128 Pa (also Bosch bhy2_parse.c and Arduino's bhy-controller map 0.0078125).
  Arduino_BHY2 1.0.8 uses 0.0078 (-0.16 %). value() is therefore Pa, NOT hPa: the old ARU sent value()*100 as
  "hPa x 100" = 100x too large (ground would show ~101 000 hPa). FIXED: ARU recovers the count with the library's own
  factor (looked up in SensorList at boot) and sends exact Pa (wire field = hPa x 100, unchanged for flight/ground);
  pressure flag only inside the BMP390 operating range 300..1250 hPa.
* Humidity (ID 130): 8-bit unsigned, 1 %RH (whole percent). Temperature (ID 128): int16 0.01 degC. Gas: uint32 ohm.
* BMM150: range typ +/-1300 uT (x, y), +/-2500 uT (z); resolution 0.3 uT; noise 0.3..1.0 uT rms by preset;
  zero-B offset +/-40 uT before / +/-2 uT after software calibration => int16 0.1 uT covers the full range.
* BMP390: operating 300..1250 hPa, -40..+85 degC; relative accuracy +/-0.03 hPa (+/-25 cm) 700..1100 hPa 25..40 degC;
  absolute +/-0.5 hPa typ (+/-50 Pa) 300..1100 hPa 0..65 degC; resolution 0.016 Pa; TCO +/-0.6 Pa/K.
* BME688: -40..+85 degC, 0..100 %RH, 300..1100 hPa; T abs +/-0.5 degC (0..65); RH +/-3 %RH, hysteresis +/-1.5 %RH,
  tau63 ~8 s; IAQ 0..500 (BSEC, resolution 1, accuracy status 0..3; IAQ needs the BSEC calibration period).
* Verification: scratchpad/nicla_harness (real SensorID.h + API-faithful stubs) — strict build with no warnings;
  behavioural test PASSED (mag 262/-131/655 counts @2500 uT -> 200/-100/500 x0.1 uT; 101325 Pa exact; 300/1250 hPa
  limits; flag withdrawn on 0). Phase C reviewers: re-check against the datasheet tables above.
* Docs (Phase D): sensor spec table must quote these numbers; ground ENV display shows RH to 1 % (not 0.01 %).

## 23 Self-contained, offline setup (owner, 2026-10-11: "make sure all the code are already within the files that u are
## creating and if possible the setup shouldnt require any internet to download the packages. just try it ... if it
## aint possible dont implement it")
Verified against the App Lab runtime sources (arduino/arduino-app-cli compose_template.go, app-specification.md;
arduino/app-bricks-py python-apps-base run.sh, pyproject.toml):
* run.sh installs pip packages only if python/requirements.txt exists and has non-blank lines (comment lines count!).
  => App folders ship NO requirements.txt. (python-libraries/*.whl would be installed offline, but we need none.)
* The Python base image already contains arduino.app_utils (App, Bridge) and arduino-router-bridge + msgpack
  (app-bricks-py dependencies) => `from arduino.app_utils import App, Bridge` and `arduino.router_bridge` need no download.
* Main container: /dev bind-mounted, group dialout, cgroup rules ttyUSB/ttyACM => the station E22-400TBH USB-UART is
  usable from python/main.py. app.yaml `ports: [N]` is exposed N:N => the web UI is served by our own stdlib HTTP server
  (no web_ui brick). Every HTML/JS/CSS asset inline, no CDN, no web fonts.
* arduino:zephyr > 0.54.1 bundles Arduino_RouterBridge (App Lab migration message); Wire, SPI, Arduino_LED_Matrix ship
  with the core => sketch.yaml lists the platform only, NO `libraries:`.
RULES (binding for Phase B/C/D):
1. No third-party Arduino library. RadioLib is replaced by an in-house SX126x driver written from the owner's SX1261/2
   datasheet: pure C++ command layer (sx126x_core.*) over a tiny HAL (SPI transfer, BUSY/NRST/DIO1/RXEN/TXEN, time),
   host-tested against a command-level chip model; used by the flight drivers/sx126x.cpp and by the station sketch
   (byte-identical copy in the ground app; a host test enforces identity). Also removes the RadioLib VENTUNO Q
   build blocker and RadioLib's per-transfer heap allocation (no heap after init). Errata 15.1-15.4, image calibration,
   OCP, TCXO, boosted-gain retention, bounded BUSY waits, CAD, GetPacketStatus SNR/RSSI, frequency error register
   (0x076B..0x076D, document provenance and the VERIFY sign test) all implemented explicitly.
2. No pip package at run time. cryptography -> pure-Python AES-256 + GCM (dssq/ccsds, validated: NIST SP 800-38D GCM
   vectors, FIPS-197 vectors, the flight C++ vectors); pyserial -> stdlib termios serial port (Linux); Bridge from the
   App Lab image. Dev-only tools (pytest, reedsolo as independent RS reference) are documented for the PC test bench,
   never needed on the boards; tests that use reedsolo skip cleanly if it is absent.
3. Remove stale library lines (SparkFun AS7265X, Arduino_NiclaSenseEnv) from sketch.yaml.
4. NOT possible offline (documented, not implemented): the Nicla Sense ME firmware needs the Arduino IDE with the
   "Arduino Mbed OS Nicla Boards" core and Arduino_BHY2 (includes the BHI260AP firmware) — a one-time install; and the
   boards' App Lab platform/Python image themselves (shipped with / installed by App Lab, outside this project).

## 24 Advanced functions selected by the owner (2026-10-11), all within the project constraints (sections 16, 17, 23)
Owner question: "do you think you can implement any other advanced functionalities in your knowledge within the hardware
used in this project?" + "and projects constraints as well?" -> owner selected ALL EIGHT below. Constraints: existing
hardware only, no Qwiic, no downloads/third-party libraries, no heap after init, bounded loops, few new files.
References (read-only, untrusted copies): CCSDS 121.0-B-3 text /tmp/claude-0/-home-user-Benchmarks/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/ds9/121x0b3.txt (owner-supplied PDF);
CCSDS 660.0-B-2 text /tmp/claude-0/-home-user-Benchmarks/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/ds9/660x0b2.txt (owner-supplied; adopts OMG XTCE 1.2, namespace
http://www.omg.org/spec/XTCE/20180204); XTCE 1.2 schema /tmp/claude-0/-home-user-Benchmarks/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/refs3/yamcs/yamcs-xtce/SpaceSystemV1.2.xsd;
libaec (CCSDS 121.0-B-3 reference implementation, DWD) /tmp/claude-0/-home-user-Benchmarks/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/refs3/libaec with OFFICIAL CCSDS test data
/tmp/claude-0/-home-user-Benchmarks/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/refs3/libaec/data/121B2TestData; CCSDS 503.0-B-2 TDM: public.ccsds.org is blocked here -> keyword set and example
files from Orekit /tmp/claude-0/-home-user-Benchmarks/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/refs3/orekit/src/main/java/org/orekit/files/ccsds/ndm/tdm and .../src/test/resources/ccsds/tdm
(TDMExample*.txt are the Blue Book annex examples); RM3100 manual /tmp/claude-0/-home-user-Benchmarks/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/ds2/f8597506-RM3100-User-Manual.txt (5.6.1).

### 24.1 New opcodes (next free after 0x1C) and HK growth
* 0x1D SEQLOAD  u8 slot 0..31 | u8 flags (b0 = time relative to SEQCTRL START, else absolute SCLK s) | u32 t_s |
  u8 len 1..12 | u8 cmd[12] (opcode + args exactly as a live TC's application data, zero-padded). Fixed 19 octets.
* 0x1E SEQCTRL  u8 action (0 START, 1 STOP, 2 CLEAR_ALL, 3 DELETE_SLOT) | u8 slot (DELETE only, else 0).
* 0x1F MAGBIST  u8 unit (0 outboard, 1 inboard, 2 both).
* 0x20 MAGCAL   u8 unit (0 outboard, 1 inboard) | i16 off_x, off_y, off_z (0.01 uT, |v| <= 30000) |
  i16 m11, m22, m33, m12, m13, m23 (Q2.14 = value/16384; diagonal 8192..32767 i.e. 0.5..2.0; |off-diagonal| <= 8192).
  Identity matrix + zero offsets = calibration cleared. 19 octets. Not hazardous.
* HK (0x010) grows 71 -> 78 octets, appended in this order: seq_state u8 (0 IDLE/EMPTY, 1 ARMED, 2 COMPLETE,
  3 STOPPED_BY_FDIR) | seq_count u8 (entries still pending) | seq_next_dt_s u16 (s to next due entry, 0xFFFF none or not
  armed, saturating) | mag_bist u8 (b0..2 outboard X/Y/Z OK, b3 outboard tested, b4..6 inboard X/Y/Z OK, b7 inboard
  tested) | magcal_state u8 (b0 outboard calibration active, b1 inboard active) | beacon_level u8 (24.4).
### 24.2 Stored command sequences (Voyager CCS-style) — flight src/fsw/autonomy.h/.cpp (pure, host-tested)
* Fixed table of 32 entries in static storage; RAM only (cleared at boot, EVR says so). Each entry: time, relative flag,
  len, 12 command octets. SEQLOAD validates the embedded command with the SAME validator as live commands (known opcode,
  exact argument length, every value in range) and REFUSES hazardous opcodes, REBOOT, SEQLOAD/SEQCTRL themselves (no
  nesting), and absolute times not in the future; relative times <= 604800 s (7 days).
* START arms (relative epoch = SCLK at START); STOP disarms (entries kept); CLEAR_ALL empties; DELETE_SLOT removes one.
* When armed: at most ONE due entry per loop pass (earliest time, tie -> lower slot) goes through the normal command
  dispatcher with source "SEQ slot n" (CMDVER + EVR). Entry more than kSeqLateMaxS = 300 s late -> skipped with EVR WARN.
  Last entry done -> COMPLETE (EVR; beacon INTERESTING). Entering SAFE (or the flight's equivalent fault mode) ->
  STOPPED_BY_FDIR (EVR WARN). Sequences keep running in hibernation (as on Voyager).
* Ground: sequence file (text, one command per line: "T+HH:MM:SS <MNEMONIC> arg=value ..." relative, or
  "SCLK <s> ..." absolute; '#' comments) -> SEQLOAD commands via the normal dictionary encoder -> uplinked through COP-1
  in order; START is a separate operator action. Web UI: sequence panel (loaded table, armed/next, upload file, START/
  STOP/CLEAR).
### 24.3 CCSDS 121.0-B-3 lossless compression ("Rice") — flight src/ccsds/rice.h/.cpp (encoder), ground
### dssq/ccsds/rice.py (decoder + reference encoder)
* Adaptive Entropy Coder exactly per 121.0-B-3: n = 16 (unsigned), J = 16 samples/block, reference sample interval
  r = 128 blocks, unit-delay predictor + prediction-error mapper (preprocessor ON), options zero-block,
  second-extension, k-split (FS = k 0) and no-compression; MSB-first packing; output padded to a whole octet.
* MUST be bit-exact: encode the CCSDS test inputs in 121B2TestData with matching options and compare to the reference
  outputs; the ground decoder decodes them and round-trips random + adversarial data (constant, alternating extremes,
  ramps, all-zero, max-entropy). Static buffers only, worst-case output bound documented (no-compression option bounds
  expansion).
* Use: SPECBURST (APID 0x024) gets an encoding field: 0 raw, 1 = CCSDS 121.0 (above parameters) over the burst's u16
  samples in CHANNEL-MAJOR order (each channel's time series contiguous, so the unit-delay predictor sees temporal
  correlation). The flight sends whichever is shorter. Ground decodes transparently; dashboard shows the ratio.
### 24.4 Beacon Monitor urgency levels (Deep Space 1 Beacon Monitor Operations Experiment)
* beacon_level u8 in HK: 0 NOMINAL, 1 INTERESTING, 2 IMPORTANT, 3 URGENT. Pure function in autonomy.* from inputs the
  flight already has: URGENT = active FDIR condition needing action (radio fault, brownout reset, SSR overflow/drops, SAFE
  mode, sequence STOPPED_BY_FDIR, sensor-bus failure); IMPORTANT = any WARN-or-worse EVR or a lost sensor since the last
  ground contact; INTERESTING = science event (EVR 0x0701 spectral event, 0x0702 light-source change) or sequence
  COMPLETE since last contact; else NOMINAL. Latched (only rises) until the next ground contact = an accepted
  authenticated TC. The hibernation beacon HK carries it. Ground: big tone banner in the web UI, event on change, station
  annunciator pattern (ALARM LED blink 1/2/3 Hz + buzzer chirps for IMPORTANT/URGENT, via the existing front-panel RPC).
### 24.5 RM3100 built-in self-test — flight drivers/sensors.h/.cpp
* Manual 5.6.1: BIST register 0x33 (I2C register address 0x33; the 0xB3 read form is SPI): b7 STE, b6..4 ZOK YOK XOK
  (read-only, valid only while STE = 1), b3..2 BW (timeout: 01 = 1, 10 = 2, 11 = 4 sleep-oscillation cycles = 30/60/120
  us), b1..0 BP (LR periods 1/2/4; Table 5-7's header mislabels them BW1/BW0). Run: write BIST = STE|BW=11|BP=11 (0x8F),
  write POLL 0x00 = 0x70, wait DRDY (STATUS 0x34 b7) with a bounded timeout, read 0x33, then write BIST 0x00 (STE off)
  so normal measurements are unaffected. Run at boot for each fitted unit and on MAGBIST; result in HK mag_bist and an
  EVR (WARN when an axis fails). Non-blocking or bounded to a few ms; never during an AS7265X lamp window.
### 24.6 Magnetometer calibration (spacecraft-roll "MAGROL" analogue)
* Ground dssq/magcal.py (pure Python, no numpy): collect RM3100 outboard (and inboard) vectors while the operator
  rotates the spacecraft; least-squares general-quadric ellipsoid fit (9 parameters, normal equations solved by
  Gaussian elimination with partial pivoting), derive hard-iron offset b and symmetric soft-iron matrix M with
  |M(B - b)| = reference field (config [science] field_ut, e.g. WMM total field); quality: residual RMS (uT), sphere
  coverage (fraction of 26 direction bins or octants hit), condition/eigen-sanity; refuse to produce coefficients when
  coverage or conditioning is poor. Encodes MAGCAL arguments (24.1). Web UI: start collection, live coverage, fit
  result, uplink button (normal two-step confirmation).
* Flight autonomy.*: validate and store MAGCAL; apply B_cal = M (B_raw - b) to the ATTITUDE path (TRIAD) only; MAG
  packet stays raw science (flags b5 = onboard calibration active). EVR echoes the accepted coefficients.
### 24.7 Tracking Data Message — ground dssq/products.py
* CCSDS 503.0-B-2 KVN: keywords/units exactly as Orekit's TdmMetadataKey / ObservationType and the Blue Book example
  files (no invented keyword). One-way downlink: PARTICIPANT_1 = spacecraft (VGQ-1), PARTICIPANT_2 = station, PATH 1,2
  per the standard's convention (verify against the examples), TIME_SYSTEM UTC, MODE SEQUENTIAL. Data per received
  frame/packet: received carrier frequency (SX126x: nominal + AFC correction + measured residual), Doppler/range-rate
  where available, CARRIER_POWER (dBW, from RSSI with the station calibration offset), PC_N0 (dB-Hz, from SNR and noise
  bandwidth) where available; E22 models give power only (COMMENT says why). Files: <archive>/tdm/VGQ1_<station>_<UTC>.kvn
  per pass (AOS..LOS) and on demand from the web UI; a small validating reader in the same module for tests.
### 24.8 Automatic frequency correction (SX126x option only)
* Ground link.py AfcLoop (pure): per-packet frequency error with SNR >= profile SNRmin + 3 dB; robust median of the last
  8; apply when |estimate - correction| > max(50 Hz, 2 % BW); slew <= 25 % of FERR_L per step; |correction| <= 0.8 FERR_L;
  hold through LOS for 600 s then return to the static [radio] freq_offset_cal_hz. Sign self-check: if after a step the
  residual grows by more than half the step, invert the sign convention once and raise an event (the chip's
  frequency-error sign is a VERIFY item).
* Station: new MCU RPC rf_foff(int32 hz) -> 1 applied | -1 range (|hz| <= 50000) | -2 radio down; applies
  SetRfFrequency(channel + hz) (step 32 MHz / 2^25) in STBY between operations; the same offset is used for RX and TX
  so both directions are centred. Snapshot rf.afc {enabled, correction_hz, estimate_hz, residual_hz, sign, state}.
  station.toml [radio.sx126x] afc = true.
### 24.9 XTCE export — ground dssq/products.py
* CCSDS 660.0-B-2 / XTCE 1.2 document generated from dictionary.py with xml.etree (stdlib): SpaceSystem "VGQ-1";
  TelemetryMetaData (parameter types with sizeInBits, unsigned/twosComplement/IEEE754, big-endian, units, polynomial
  calibrators for scale factors), ParameterSet, ContainerSet (abstract CCSDS packet container with primary-header and
  CUC secondary-header entries, one container per APID with a RestrictionCriteria on the APID); CommandMetaData
  (argument types, abstract TC packet, one MetaCommand per opcode with the opcode as a fixed ArgumentAssignment or
  FixedValueEntry, hazardous flagged). Validated in tests against SpaceSystemV1.2.xsd when an XML-schema validator is
  available in the dev venv (skip cleanly otherwise; well-formedness and structural checks always run). Web UI
  download link and CLI (python -m dssq.products xtce > vgq1.xtce.xml).
### 24.10 Reference update
* CCSDS 503.0-B-2 Cor. 1 (TDM) owner-supplied: text /tmp/claude-0/-home-user-Benchmarks/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/ds10/503x0b2c1.txt — the PRIMARY TDM reference (keywords,
  units, PATH convention, bands incl. UHF, FREQ_OFFSET, CARRIER_POWER dBW, PC_N0 dB-Hz, DOPPLER_INSTANTANEOUS km/s,
  RECEIVE_FREQ_n Hz). Orekit sources/examples are secondary cross-checks only.

## 25 Owner reversal for the SX126x driver (2026-10-11) — SUPERSEDES section 23 rule 1 and section 24's mention of an in-house driver
Owner: "sorry continue building SX126x driver on RadioLib through the internet" + "also fix any issue that might be present
in that library yourself". => The SX126x driver STAYS ON RadioLib (the committed Phase A code: flight drivers/sx126x.*,
station sketch SX126x path). Everything else in section 23 (no pip packages, stdlib serial/crypto, no other Arduino
library, self-hosted web UI) STILL APPLIES.
### 25.1 Facts established from source (2026-10-11)
* RadioLib latest release = 7.8.1 = tag commit 2325a38a89dd0f0e402c19f67fe910111da1e246 (2026-09-28); master
  (4df94a9, 2026-10-11) has NO VENTUNO Q support either. Local pristine clone: /tmp/claude-0/-home-user-Benchmarks/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/radiolib_rel/RadioLib (read-only).
* VENTUNO Q builds with -DARDUINO_VENTUNO_Q (boards.txt ventunoq.build.board=VENTUNO_Q). RadioLib BuildOpt.h only has
  "#elif defined(ARDUINO_ARCH_ZEPHYR) && defined(ARDUINO_UNO_Q)" -> VENTUNO Q = "Unknown Arduino": no PinMode/PinStatus
  casts -> ArduinoHal.cpp fails against the core's pinMode(pin_size_t, PinMode) / digitalWrite(pin_size_t, PinStatus) /
  attachInterrupt(pin_size_t, voidFuncPtr, PinStatus) (ArduinoCore-zephyr cores/arduino/wiring_digital.cpp,
  WInterrupts.cpp), plus a "#warning ... might not be compatible".
* RadioLib Module.cpp allocates new[]/delete[] buffers on EVERY SPI transfer unless RADIOLIB_STATIC_ONLY (default 0);
  static mode uses stack arrays of RADIOLIB_STATIC_SPI_ARRAY_SIZE = 12 + RADIOLIB_STATIC_ARRAY_SIZE (256) octets.
* RadioLib src/TypeDef.h includes "BuildOptUser.h" (guard _RADIOLIB_USER_BUILD_OPTIONS_H) before the build options =>
  options defined THERE reach every RadioLib TU and every sketch TU identically (no ODR hazard).
* App Lab (arduino-app-cli, embeds arduino-cli v1.5.2) ALWAYS compiles with the sketch's default profile
  (orchestrator.go initializeArduinoCli: "sketch has no default profile" is an error). Profiles cannot carry build
  flags (arduino-cli internal/arduino/sketch/profiles.go: no build_properties). Profile libraries may be index
  libraries "Name (version)" (downloaded from the internet) or "- dir: <path>" (absolute or relative to the sketch
  folder; arduino-cli >= 1.3.0).
* GitHub git clone works from this environment; github.com archive downloads, downloads.arduino.cc and public.ccsds.org
  are blocked by the proxy here (not on the owner's network).
### 25.2 Policy
* Library-internal defects (cannot be fixed from our code) are fixed by a SMALL, documented patch applied to the
  pristine RadioLib 7.8.1 obtained from the internet (git tag 7.8.1, commit verified): the packaging tool fetches it,
  verifies the commit, applies the patch and bundles the result in each App Lab zip as sketch/libraries/RadioLib,
  referenced by sketch.yaml "- dir: libraries/RadioLib". The repository holds only the patch + the fetch/patch logic (no
  RadioLib sources). Minimum patch: VENTUNO Q platform branch with the casts; BuildOptUser.h: RADIOLIB_STATIC_ONLY 1 and
  RADIOLIB_EXCLUDE_* for every module/protocol we do not use (smaller, faster on-board builds). Anything else only when
  an audit finding is CONFIRMED and cannot be handled from our side.
* Defects that CAN be handled from our side stay fixed in our drivers (Phase A: image calibration after NRST/channel
  change, explicit OCP + read-back, bounded waits around config/launch/CAD, boosted-gain read-back, writeRegister
  verification by read-back).
* The RadioLib include in our code is guarded by the radio-model macro so the default E22 builds never pull RadioLib.
### 25.3 VENTUNO Q facts (owner-supplied datasheet ABX00181, modified 06/10/2026; text /tmp/claude-0/-home-user-Benchmarks/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/ds11/abx00181.txt)
* Hardware I2C on the JDIGITAL header pins 17 SDA = PH12, 18 SCL = PH11 (I2C4/I3C1), 3.3 V. Wire the CardKB to the pins
  silk-screened SDA/SCL (resolves the D20/D21 naming discrepancy: wire by silkscreen). A4 (PE14)/A5 (PE15) have NO
  hardware I2C. Qwiic = I2C3 (PC9/PA8) - NOT used.
* JDIGITAL: D0/RX PB11, D1/TX PB10, D2 PB0, D3 PB1, D4 PB6, D5 PB5, D6 PB2, D7 PB3, D8 PB4, D9 PB7, D10/CS PB12, D11/MOSI
  PB15, D12/MISO PB14, D13/SCK PB13; "most pins are 5 V-tolerant as inputs in digital mode". JANALOG: A0 PA4, A1 PA5 (not
  5 V tolerant), A2 PE12, A3 PE13, A4 PE14, A5 PE15; pin 5 "+5V USB" = +5V_LIMITED (5 V supply out); pin 4 +3V3_LIMITED.
* +5V rail 4.75..5.25 V (MPQ4371 buck); +5V_LIMITED and +3V3_LIMITED behind MP5077GG-Z load switches limited to 2.8 A
  each (UNO shield headers and JSPI). => The board-powered E22-400T33D (TX 1.0 A typ / 1.2 A max, >= 4.7 V for full
  power) on the station is within the board's ratings, provided the board's input supply is adequate (Arduino
  recommends >= 60 W supplies for heavy loads; USB-C PD 9/15/20 V or 12 V/24 V DC input).

## 26 AS7265X cross-check outcome (commit 8115f61) — SUPERSEDES 13.6 / section 4 where they differ
Twenty findings confirmed against SparkFun AS7265x 1.0.5 + examples and ams datasheet v1-04; driver fixes committed.
Full report: /tmp/claude-0/-home-user-Benchmarks/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/as_xcheck.txt.
Phase B MUST also do:
* LED drive limits (HAZARD, SparkFun Example3_Settings 65-81): led_cur b1:0 white 0..3, b3:2 IR 0..2, b5:4 UV 0 only
  (UV max 30 mA, IR max 65 mA). SPECCFG validation in the flight command validator AND the ground dictionary.py /
  spectral.py / web UI use the same set (flight spec_config_valid already enforces it; kSpecLedMaxCode {3,2,0}).
  Contract 13.6 / section 4 "Not hazardous" for led_cur is WRONG: values outside the set are rejected.
* AS-T6: I2cBus::recover() must perform a real bus clear (release SDA held low by a hung AS72651: up to 9 SCL pulses + STOP,
  bounded) before re-begin; escalate from the AS7265X driver's FAULT path.
* Executive: honour As7265x::lamp_state_unknown() (lamps possibly lit -> keep RM3100 samples flagged / defer) and the
  new ERR_NO_LAMP = 7 result.
* Ground decode: SPECBURST raw 0xFFFE = saturated (65535), 0xFFFF = not converted; HDR packet exposure/raw semantics per
  spectral.h comments (AS-DP-04); burst per-sample read time if the packet gains it (AS-T8, SpecResult.burst_read_max_ms).
* Docs/test plan T-SC-08: DATA_RDY with CONFIG.INT (now always set), one-shot re-trigger checks, calibrated-value
  comparability across gain/integration (AS-DP-05).

## 27 Offline ground rework outcome (commit c8c3b04) — follow-ups for Phase B/D
* Add the 4 pseudo-terminal serial tests from scratchpad/test_e22_serial_pty.py as ground/tests/ coverage (drop the
  sys.path line and the unused import sys).
* gds.py: expose E22Serial connected/faults/reopens/tx_dropped/last_error in the snapshot (rx.radio_link) + events on
  connect/disconnect; enforce [radio] uart_code == 3 (9600) in the config check (radioctl CONFIG mode is always 9600).
* Update comments that call the 'cryptography' package the cross-check (flight gcm.h lines 18-19, tools/host_test/
  test_crypto.cpp lines 5-6, 50-51): the cross-check is ground/dssq/ccsds/aes_gcm.py (+ cryptography when installed).
* Docs: no 'pip install -r requirements.txt' for the station (README.md:86, docs/00:110, :324); references list stdlib
  termios instead of pyserial, add FIPS-197, SP 800-38D, McGrew-Viega GCM test cases 13-16; docs/08 VERIFY items:
  AES-GCM benchmark on the VENTUNO Q, ModemManager ignore rule for the USB-UART, App can open /dev/ttyUSB0, router socket.
* Packaging: NEVER copy ground/requirements.txt into an App's python/ folder (run.sh would pip-install it).
* frontpanel.py rewrite: single CardKB hello H|fw|rcu|kb, drop kb2 parsing, no bare 'except Exception'.

## 28 Rice outcome (CCSDS 121.0-B-3) — follow-ups for Phase B
* SPECBURST layout: t0_ms u32 | rate_dhz u16 | gain u8 | int_cycles u8 | n u8 | encoding u8 (0 raw, 1 CCSDS 121.0) |
  n x dt_ms u16 | payload. Flight: channel-major cm[ch*n+t] = s[t].raw[ch]; rice_encode(kRiceMission, cm, 18*n, z,
  36*n - 1, &zl): RICE_OK -> encoding 1; RICE_ERR_OVERFLOW -> raw; other status -> raw + EVR. kSpecBurstMaxLen +1.
* Ground: encoding 1 -> rows = time_major(rice.decode(payload, 18*n), 18); RiceError -> count in rxstats, WARN event,
  drop; dashboard shows compression ratio 36*n/len(payload).
* Docs: CCSDS 121.0-B-3 reference; official test data verified; CCSDS121_TESTDATA env var for the full suite.
* VERIFY on target: rice_encode stack (host 320-384 B) and run time (worst case kRiceMaxSamples 4096) vs 20 ms loop.

## 29 Autonomy (8ebb2a1) and ground products (a970823) — integration hand-off (verbatim from the workstream reports)
### autonomy
[
 "spacecraft/.../fsw/commands.h: add to enum Opcode OP_SEQLOAD = 0x1D, OP_SEQCTRL = 0x1E, OP_MAGBIST = 0x1F, OP_MAGCAL = 0x20 (equal to autonomy.h kOpcodeSeqLoad/SeqCtrl/MagBist/MagCal); optionally static_assert equality in commands.cpp.",
 "spacecraft/.../fsw/commands.cpp: split parse_tc_packet() into the header check plus a new `CmdError parse_user_data(const uint8_t* u, size_t n, Command& c)` (u = opcode + args, n >= 1), so the same parser validates live and stored commands. Add cases: SEQLOAD na==19 -> CE_OK (full checks in CommandSequencer::load); SEQCTRL na==2: a8 = action <= 3, b8 = slot <= 31; MAGBIST na==1: a8 <= 2; MAGCAL na==19 -> CE_OK (full checks in MagCalStore::load). Install with seq_.set_validator([](void*, const uint8_t* cmd, uint8_t len) -> uint8_t { Command c; return parse_user_data(cmd, len, c); }, nullptr).",
 "flight.h/.cpp (Phase B): add members CommandSequencer seq_; BeaconMonitor beacon_; MagCalStore magcal_; MagBistResult bist_ob_ = mag_bist_not_run(), bist_ib_ = mag_bist_not_run(); bool safe_by_fdir_. In setup(): seq_.reset() + set_validator; beacon_.begin(reset_cause_); EVR 'stored sequence table and MAGCAL are RAM only: empty after boot'; after init_sensors (lamps off) run mag_ob_.run_bist() / mag_ib_.run_bist() on fitted units and EVR mag_bist_evr_text with mag_bist_evr_severity.",
 "flight.cpp evr(): call beacon_.note_evr(sev, id) FIRST, before the evr_level_ filter and the evr_budget_ throttle.",
 "flight.cpp loop(): once per pass call seq_.poll(sclk_.seconds(), safe_by_fdir_, ev). On EXECUTE, dispatch ev.cmd/ev.len through the normal command path, which needs an execute variant taking user data instead of a TC packet. Suggested CMDVER tc_seq sentinel 0xFE = stored sequence, plus EVR INFO 'SEQ slot n: opcode 0xXX'. On SKIPPED_LATE: EVR WARN with ev.late_s. On STOPPED_BY_FDIR: EVR WARN. If ev.completed: EVR INFO 'sequence COMPLETE'. Suggested free EVR block 0x0900-0x090F for the sequencer and 0x0910-0x091F for MAGBIST/MAGCAL.",
 "flight.cpp safe_by_fdir_: set true on boot SAFE and on every FDIR set_mode(MODE_SAFE, ...) (command loss, PA over-temperature); set false when OP_MODE executes (live or stored).",
 "flight.cpp process_tc(): after the FARM verdict switch, for ACCEPT, ACCEPT_BYPASS and CONTROL_*, call beacon_.on_ground_contact(beacon_sample()). Do it after execute() so the command's own EVRs and mode changes are acknowledged. beacon_sample() = {fdir_flags_, sensor_health_, sensor_enable_, ssr_.dropped(), seq_.state(), safe_by_fdir_}; tick_1hz() calls beacon_.update(beacon_sample()).",
 "flight.cpp execute() for the new opcodes: SEQLOAD -> seq_.load(u+1, n-1, sclk_.seconds(), rep), result = seq_cmd_error(st), EVR with seq_status_text and rep.cmd_error. SEQCTRL -> seq_.control(c.a8, c.b8, sclk_.seconds(), safe_by_fdir_). MAGBIST -> magbist_unit_mask(c.a8), defer run_bist to the next PA-off window with lamps off, and store results unless status == MAG_BIST_BUSY. MAGCAL -> magcal_.load(u+1, n-1, unit), result = magcal_cmd_error(s), EVR magcal_evr_text (INFO) or magcal_status_text (WARN).",
 "flight.cpp ATT/TRIAD: TRIAD currently uses the BMM150 (last_mag_ut_). For 24.6, feed TRIAD from magcal_.apply(unit, rm3100_field_uT, cal), preferring outboard, then inboard, then the BMM150 when no RM3100 is fitted. MAG packets stay raw, with MAG flags b5 = (magcal_.state() != 0).",
 "flight.cpp: EVR 0x0701 is currently 'RF survey done' (service_scan). Contract 13.6 assigns 0x0701 to the spectral event, and BeaconMonitor treats 0x0701/0x0702 as science events, so the RF-survey EVR must move to another ID or every RFSCAN would set the beacon to INTERESTING.",
 "flight.cpp FDIR (recommended): clear FDIR_I2C_BUS after a successful recovery (bus_.consecutive_errors() == 0 for some seconds) and FDIR_AUX_TIMEOUT after the next good TX. The beacon counts FDIR rising edges, so a sticky flag cannot re-raise it after a ground contact.",
 "fsw/tlm_packets.h/.cpp (and ground dictionary.py HK): append to HkPacket, in order (71 -> 78 octets): seq_state u8 = seq_.state(), seq_count u8 = seq_.count(), seq_next_dt_s u16 = seq_.next_dt_s(sclk_.seconds()), mag_bist u8 = mag_bist_hk(bist_ob_, bist_ib_), magcal_state u8 = magcal_.state(), beacon_level u8 = beacon_.level().",
 "ground/dssq/dictionary.py COMMANDS (suggested): MAGBIST 0x1F [('unit','B',0,2,{'OB':0,'OUTBOARD':0,'IB':1,'INBOARD':1,'BOTH':2})], not hazardous.",
 "ground/dssq/dictionary.py COMMANDS (suggested): MAGCAL 0x20 [('unit','B',0,1,{'OB':0,'IB':1}), ('off_x','h',-30000,30000,None), ('off_y',...), ('off_z',...), ('m11','h',8192,32767,None), ('m22',...), ('m33',...), ('m12','h',-8192,8192,None), ('m13',...), ('m23',...)], not hazardous. This is 19 octets and encodes through the existing scalar model.",
 "ground/dssq/dictionary.py COMMANDS (suggested): SEQCTRL 0x1E [('action','B',0,3,{'START':0,'STOP':1,'CLEAR':2,'CLEAR_ALL':2,'DELETE':3}), ('slot','B',0,31,None)]. SEQLOAD 0x1D cannot be described by the scalar argument model; build it only through cmdparse.seqload_commands(), and if it is listed for XTCE, mark it so parse() refuses it.",
 "ground/dssq/dictionary.py hazard flags: whatever hazardous flag RADIOOPT (0x19) or any other new opcode receives must also be mirrored in CommandSequencer::opcode_forbidden() in autonomy.cpp. tests/test_sequence.py::test_flight_forbidden_list_matches_dictionary enforces this.",
 "ground GDS / web UI sequence panel: parse_sequence(file text) -> seqload_commands(entries, now_sclk_s=<SCLK estimate or None>) -> queue each ParsedCommand via tc_packet() through COP-1 in order. START is a separate operator command: parse('SEQCTRL START 0').",
 "ground dssq/magcal.py (other workstream): use b = off/100 uT and M = m/16384, matching the flight. seq_vectors.txt from host_test has a 'magcal <block> <status> <unit> <raw xyz> <cal xyz>' line usable as a flight cross-check vector."
]
### ground-products
[
 "ground/dssq/dictionary.py (Phase B owner): add CommandDef('MAGCAL', 0x20, args [('unit','B',0,1,None), ('off_x','h',-30000,30000,None), ('off_y','h',-30000,30000,None), ('off_z','h',-30000,30000,None), ('m11','h',8192,32767,None), ('m22','h',8192,32767,None), ('m33','h',8192,32767,None), ('m12','h',-8192,8192,None), ('m13','h',-8192,8192,None), ('m23','h',-8192,8192,None)], hazardous False). magcal.magcal_command() output then parses unchanged and build_xtce() picks it up automatically, as it will SEQLOAD/SEQCTRL/MAGBIST.",
 "ground/dssq/dictionary.py: build_xtce() needs only fixed-layout fields (struct codes B b H h I i Q q f d). A log-coded SPEC field should carry its decoder as a callable 7th tuple element; products exports it raw with a note. New variable-length APIDs (SPECBURST 0x024 with n samples, CFDP 0x030) need an explicit builder in products.py like the existing _evr_system/_rfscan_system. Ask the products owner, or add one following that pattern. EVR_TEXT_MAX=80 and RFSCAN_MAX_CHANNELS=84 are currently constants in products.py citing flight tlm_packets.h; they could move to dictionary.py.",
 "ground/dssq/config.py DEFAULTS and config/station.toml: [radio.sx126x] afc = true (contract 24.8); new [radio] freq_offset_calibrated = false, which the GDS reads to pass freq_offset_cal_hz (when true) or None (when false) to TdmRecorder.write() and so avoid publishing a crystal offset as range rate; [science] field_ut = site WMM total field in uT, with no silent default (magcal must refuse to run without it).",
 "ground/dssq/gds.py (integration):",
 "(a) AFC: afc = AfcLoop.from_settings(settings, enabled=cfg['radio']['sx126x']['afc']). Per RxPacket: corr = afc.on_packet(pkt.ert, pkt.freq_err_hz, pkt.snr_db, pkt.crc_ok). In housekeeping: corr = afc.tick(now, rx.los). For any int corr: afc.confirm(corr, sx126x_backend.rf_foff(corr), time.time()). Forward afc.pop_events() to the event log. Snapshot rf.afc = afc.snapshot(). On LINKRATE or LINKCHAN, call afc.reconfigure(now, sf, bw, freq) and send any returned value the same way. Use afc.estimate_hz (total offset) in place of the raw freq_err_hz for rf.doppler when AFC is enabled.",
 "(b) TDM: TdmRecorder(cfg['station']['name'], cfg['spacecraft']['name']). Call clear() at AOS. For each received frame or packet, call add(TdmRecord(...)) AFTER afc.on_packet() and BEFORE any confirm(), with t_utc = ert - radio_latency_s, rx_power_dbm = calibrated RSSI, snr_db and freq_offset_hz = afc.residual_hz(raw) for the SX126x (else None), afc_correction_hz = afc.correction_hz (else 0), nominal_freq_hz = settings.freq_hz, and noise_bw_hz = profile BW (else [radio] noise_bw_hz). At LOS, call write(archive_dir, freq_offset_cal_hz=..., radio_model=settings.model.name) and then clear(); also expose a web 'write TDM now' action.",
 "(c) Web endpoints: XTCE download = xtce_bytes(build_xtce(version=dssq.__version__, date_text=UTC date)). MAGCAL panel = MagCalCollector per unit fed from MAG ob_b_*/ib_b_* while collecting, live_coverage(), fit().as_dict(), and uplink of magcal_command(result.magcal_args(unit)) through the normal two-step confirmation.",
 "ground/dssq/radio/sx126x.py (backend owner): add rf_foff(hz:int) -> int (1 applied / -1 range / -2 radio down) as specified in contract 24.8, so the GDS can call confirm() with its result. Optionally report SignalRssiPkt (see the VERIFY item on CARRIER_POWER).",
 "ground/dssq/display/dashboard.html / web.py: rf.afc block (state, correction, estimate, residual, sign, events); MAGCAL panel (live coverage x/26, fit result, reasons, uplink); download links for TDM and XTCE.",
 "docs/08 (Phase D): add VERIFY tests for the AFC sign (signal generator +1 kHz), the SnrPkt bandwidth, NTP-disciplined station clock / radio_latency_s, a real RM3100 rotation for the magcal thresholds, and loading vgq1.xtce.xml into an MCS. Document the TDM ICD conventions that are written into each file's COMMENT lines."
]
### Full reports (API summaries, verify items): /tmp/claude-0/-home-user-Benchmarks/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/a2_reports.txt

## 30 Owner decision (2026-10-11): "go ahead with the bundled patched copy"
Contract 25.2 is CONFIRMED by the owner: RadioLib 7.8.1 is fetched from GitHub at packaging time (commit verified),
patched, and bundled in each App Lab zip as sketch/libraries/RadioLib ("- dir: libraries/RadioLib" in sketch.yaml).
The boards do not download RadioLib. Do not offer the stock Library-Manager variant again.

## 31 Owner request (2026-10-11): "include these detailed description in the documentation as well"
Phase D docs MUST contain a "Ground Station Web UI Reference": every metric the VENTUNO Q web UI shows, organised by
dashboard tab (header bar, OVERVIEW, RECEIVER incl. RF-now / statistics windows / histograms / link budget / radio config /
AFC / Doppler / decoder / frames / other links, LINK & SECURITY, FRAMES & PACKETS, SPACECRAFT incl. all 43 HK fields and
all 24 RF fields, SPECTROMETER, SCIENCE, MAGCAL, SEQUENCES, FILES, COMMANDING, health/logs, alarm logic, /metrics), each
with unit and a plain-language description, plus the display conventions (4 dp dB statistics, resolution of single
readings, est./approximate labels, source badges, "—" for n/a). Starting text: scratchpad/docs_inputs/webui_metrics.md
(given to the owner in chat). It MUST be re-synchronised with the FINAL snapshot()/science_snapshot()/dashboard
implementation (Phase B spec §5 may have shifted during implementation): every key named in the doc must exist, and a
test (ground/tests/test_web.py or a docs check) asserts that each top-level snapshot key and each RECEIVER rx.rf scalar
appears in the reference, so the doc cannot drift. Also offer in docs (FAQ) the owner's other explained topics with the
facts given in chat: expected range per scenario (link budget assumptions; 35 dBi antenna claim not physical: ~17 m dish
at 433 MHz), SF/profile selection (8 profiles, AIRRATE command, E22 has no SF control), where signal processing happens,
UNO Q Linux-side (EGSE) workload, coding standard summary, CI, regulatory note (433 MHz power/encryption).

## 32 Repository renamed by the owner (2026-10-11): Tanay7/Benchmarks -> Tanay7/vgq1-deep-space-link
Same repository (history, branch claude/wonderful-hamilton-dzjfby, PR #1 carried over). Local working copy stays at
/home/user/Benchmarks with origin = https://github.com/Tanay7/vgq1-deep-space-link. Phase D: every doc/README/link that
names the repository uses the new name (grep for "Benchmarks").

## 33. Radio model selection without code edits (user request 2026-10-11)
- Requirement: switching between E22-400T33D and the preserved E22-400T37S must need NO source edit.
- Flight: VGQ_RADIO stays the compile-time default, but the E22 build reads a strap pin at boot:
  a spare header pin (proposed A0, INPUT_PULLUP; verify against ABX00162 before fixing it) jumpered
  to GND = E22-400T37S, open = E22-400T33D. Both share the E22 UART register protocol, so one
  binary serves both. Selected model goes in the RF packet radio_model field (contract 3.2)
  and an EVR at boot. T37S implies: power code ignored (always 37 dBm), 50 % TX duty limit,
  FF FF FF 0x fault log decoded, REG1 = 0x24.
- Ground: station.toml [radio] model = "E22-400T33D" | "E22-400T37S" | "SX126x" (config, not code);
  if absent, the ground follows the radio_model the spacecraft reports. Station sketch is
  model-agnostic for both E22s.
- SX126x (SPI) remains a build switch: a different wiring, not a strap.
- Docs: a "Switching to the 5 W E22-400T37S" section (jumper, PSU, M0/M1 pull-ups, TBH-02 caps, licence).
- Schedule: implemented in WS6b (after G1), tested in host_test (strap -> model) and ground pytest.

## 34. Phase C review scale (user, 2026-10-11)
- User agreed to a larger read-only reviewer panel in Phase C (about 15-20 reviewers, distinct lenses:
  protocol/CCSDS, memory safety, radio driver, SDLS/security, sensors, ground UI, docs vs code). Findings
  are adversarially verified before any fix; fixes are applied serially with the full gates.

## 35. Knowledge capture (user, 2026-10-11)
- Phase D adds docs/12_Design_Decisions.md: every decision (choice, alternatives, reason, source/test).
- Fold the chat explanations into docs: radio transport path, SDLS in plain words, Rice, the test layers,
  range/SF/power FAQ, CV-neutral project summary. Goal: any future session answers from the repo alone.

## 36. Additional documentation sections (CONFIRMED by the user 2026-10-11: Tier 1 and Tier 2 are both required in Phase D)
Tier 1: Glossary & acronyms; Configuration reference (every config.h define / station.toml key: default,
range, must-match pairs); Troubleshooting fault tree + EVR/alarm operator-response catalogue; Known
limitations & open-items register (all VERIFY tags, hardware-unverified items); Developer guide (code map,
"add a sensor/packet/command end to end" touch-point checklist, coding standard, local test commands);
Security model & recovery (threat model, key compromise, counter-file backup: sdls_sn.txt / boot counter
loss = nonce risk, re-flash, E22 register factory reset); Hardware test record templates (range,
RSSI vs distance, power draw, magcal) - the measured numbers for the CV.
Tier 2: BOM with part numbers/cost; power budget & PSU sizing (T37S 5 W); resource budgets (flash/RAM/
stack/loop timing, link capacity per mode); data products guide (archive layout, TDM, XTCE, loading in
Python); build/release/packaging + RadioLib update procedure; Mermaid diagrams (architecture, data flow,
mode state machine, COP-1); CHANGELOG; one-page project summary.
Also: docs/11 §3 "why not RadioLib" is obsolete (v3 uses the patched bundled RadioLib) - rewrite.

## 37. Web UI Reference: highly detailed (user, 2026-10-11) - extends §31
The Phase D "Ground Station Web UI Reference" (docs/13_Web_UI_Reference.md) is a full explainer, not a table dump.
Per tab: a plain-language introduction (what the tab is for, when to look at it, a screenshot-free layout sketch).
Per metric (every one, incl. all 43 HK and 24 RF fields): name as shown | snapshot key | unit and displayed
resolution | exact source (which receiver measurement, packet + field + scaling, or computation) | formula with
symbols defined (e.g. FSPL = 20log10(d_km) + 20log10(f_MHz) + 32.44; margin; C/N0; SEM; Doppler -> range-rate) |
update rate / window | typical, good, marginal and bad ranges for THIS hardware (E22-400T33D, T37S, SX126x profiles) |
how to interpret it | what to do when it is abnormal (cross-reference the event/alarm catalogue) | related metrics |
a worked numeric example. Plus: display conventions, the statistics windows explained (mean/SEM/min/max/std/
percentiles, why 4 dp), estimate vs measured badges, alarm logic tables, the /metrics endpoint, and a
"reading the RECEIVER tab during a range test" walkthrough. Every number and formula is checked against the
final code (gds.py snapshot/science_snapshot, rxstats.py, link.py, dashboard.html); the drift test of §31 applies.

## 38. RESUME STATE (written 2026-10-11 17:50 UTC, before a usage-limit pause)
Pushed on claude/wonderful-hamilton-dzjfby: f0ff5e8 (G1 RadioLib), a90e7e7 (WS1-WS4), 02c273a (WS6a); CI green on f0ff5e8.
PR #1 was merged into main by the owner (4554bff); the branch was restarted from main; a new PR only if asked.
In flight at the pause (uncommitted, half-written possible): WS5 web UI (web.py, dashboard.html, station_io python,
app.yaml, test_web.py) and WS6b (F-40 RS rescue, F-55/56/57/62, F-65 CI flight build, PB-6.4 annunciator, RL
handoffs, §33 strap-selected E22 model: flight config.h/e22.*/radio.*/sx126x.*/flight.*, ground link/config/gds/
station.toml/sx126x.py, station sketch, check_firmware.sh, ci.yml, .gitignore, tools/host_test/test_radio_model.cpp).
On resume: read each in-progress file fully, keep correct partial edits, finish, then gate each set in a clean worktree
(host tests strict + ASan/UBSan, check_firmware, ground pytest 3.13 + 3.11, real builds via realbuild/bin/acompile.sh
unoq/ventunoq main + rel100, actionlint for ci.yml) and commit/push; then screenshots of all v3 tabs (scratchpad/shot/
shot.js, zero page errors), gate B4, Phase C (15-20 read-only reviewers, adversarial verify, serial fixes), Phase D
(§31, §35, §36, §37 docs; App Lab zips via tools/package_apps.py; remove docs/WIP_*; final token/time report).
