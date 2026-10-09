# 04 - Space Data Link Interface Control Document (ICD)

Applies to: VGQ-1 flight software (`spacecraft/vgq1_flight/sketch/src/ccsds`, `fsw`) and the
DSS-Q ground data system (`ground/dssq/ccsds`, `framesync.py`, `fop.py`, `gds.py`). Both
implementations are written independently (C++ / Python) and cross-checked bit-for-bit by
`tools/host_test` + `ground/tests/test_cross_check.py`.

## 1 Managed parameters

| Parameter | Value | Standard |
|---|---|---|
| Spacecraft ID (SCID) | 0x0A7 (private - **not** SANA-registered) | 132.0-B-3, 232.0-B-4 |
| Physical channel | EBYTE E22-400T37S, LoRa, 410.125 + CH MHz (default CH 23 = 433.125 MHz) | - |
| Air data rate | 2.4 kbit/s default (codes 0-2 are all 2.4k on the T37S); 4.8k/9.6k/... selectable | EBYTE manual v1.5 7.2 |
| TM frame length | 200 octets (fixed) | 132.0-B-3 |
| TM frame data field | 188 octets | |
| OCF | present (CLCW) in every frame | 132.0-B-3, 232.1-B-2 |
| FECF | CRC-16/CCITT (poly 0x1021, init 0xFFFF) | 132.0-B-3 |
| Channel coding | RS(255,223), E = 16, I = 1, dual basis, shortened to (232,200) (23 octets virtual fill) | 131.0-B-5 sec. 4 |
| Pseudo-randomizer | 255-bit sequence h(x) = x^8 + x^7 + x^5 + x^3 + 1, all-ones seed, over the codeblock (not the ASM) | 131.0-B-5 sec. 10 |
| ASM | 0x1ACFFC1D (32 bit) | 131.0-B-5 sec. 9 |
| CADU | 4 + 232 = 236 octets = one E22 packet (sub-packet size 240) | |
| Virtual channels | 0 RT-ENG, 1 RT-SCI, 2 PLAYBACK, 7 idle (OID) | 132.0-B-3 |
| Space packets | version 0, APIDs 0x010-0x023, idle APID 0x7FF, CUC secondary header on TM | 133.0-B-2 |
| Time code | CUC, implicit P-field 0x2E (agency epoch, 4 coarse + 2 fine octets) | 301.0-B-4 |
| TC frame | max 64 octets, VCID 0, COP-1 | 232.0-B-4, 232.1-B-2 |
| CLTU | start 0xEB90, BCH(63,56) codeblocks, fill 0x55, tail C5C5C5C5C5C5C579 | 231.0-B-4 |
| FARM-1 window | 10 | 232.1-B-2 |
| FOP-1 | window 5, T1 45 s, transmission limit 3 (station.toml `[commanding]`) | 232.1-B-2 |
| Security | SDLS authentication only, HMAC-SHA-256 truncated to 128 bit, SPI 1, 32-bit anti-replay SN | 355.0-B-2 |

## 2 Downlink (spacecraft -> ground)

### 2.1 CADU

```
| ASM 1ACFFC1D (4) | randomized codeblock (232) = TM transfer frame (200) + RS check symbols (32) |
```

One CADU is handed to the E22 per transmission (236 octets <= the 240-octet sub-packet), so every
LoRa packet carries exactly one codeblock. The receiving E22 appends one RSSI octet
(REG3 bit 7): the GDS reads `[CADU 236][RSSI 1]` per packet.

### 2.2 TM transfer frame (200 octets)

```
 Primary header (6):  TFVN 2b=0 | SCID 10b | VCID 3b | OCF flag 1b=1 | MCFC 8b | VCFC 8b |
                      Sec hdr 1b=0 | Sync 1b=0 | Pkt order 1b=0 | Seg len ID 2b=11 | FHP 11b
 Data field (188):    Space packets (may span frames; FHP = first packet header, 0x7FF none, 0x7FE OID)
 OCF (4):             CLCW (COP-1 report: FARM state, lockout, wait, retransmit, report value N(R))
 FECF (2):            CRC-16/CCITT over the 198 octets before it
```

Virtual channel selection (flight `send_next_frame`): engineering first; science is never
starved when it backs up (> 500 octets); playback last; an OID frame (VC 7) when nothing is
queued, so the CLCW and the ground lock are refreshed every frame period.

### 2.3 Space packets

```
| Version 3b=0 | Type 1b=0 | SecHdr 1b=1 | APID 11b | SeqFlags 2b=11 | SeqCount 14b | Length 16b |
| CUC time: coarse 4 octets + fine 2 octets (1/65536 s) | user data (05 Dictionary) |
```

SCLK = seconds since the flight computer booted; the **partition** (boot count, kept by the Linux
EGSE) makes SCLK unambiguous across reboots - the JPL "partition/count" convention.

### 2.4 Frame timing and duty cycle

* The flight software measures each transmission's radio busy time from the E22 AUX pin
  (AUX low while buffering/transmitting).
* Frame period = max(busy x {SAFE 4.0, CRUISE 2.5, ENCOUNTER/TEST 2.0}, busy x 2 (50 % duty
  ceiling), 1.5 s), x 2 while the PA over-temperature flag is set, or longer on `FPERIOD`.
* EBYTE notes AUX can return high while the last packet is still on air; the measured busy time
  is therefore a lower bound of the true air time. The duty ceiling and the period multipliers
  leave the margin, and the uplink window (2.6) only opens *after* the ground has received a
  complete CADU.

### 2.5 Ground acquisition

`framesync.py`: SEARCH (ASM with <= 2 bit errors) -> LOCK (next ASM exactly one CADU + RSSI octet
later, <= 4 bit errors) -> FLYWHEEL (up to 3 misses) -> SEARCH. Inside the stream it also
recognises the E22's ambient-noise replies (`C1 00 02 noise last`) and abnormal-status reports
(`FF FF FF code`). Each codeblock is de-randomized, RS-decoded (Berlekamp-Massey, Chien,
Forney), FECF-checked, then packets are extracted per VC with VCFC gap accounting.

### 2.6 Uplink window (half duplex)

The E22 is half duplex. The GDS transmits CLTUs immediately after it has received a CADU: the
spacecraft has just finished transmitting and listens until its next frame (>= 1 busy time of
quiet by construction). At most 2 CLTUs per window; ambient-noise queries use quiet windows.

## 3 Uplink (ground -> spacecraft)

### 3.1 CLTU

```
| EB 90 | codeblock 1 | ... | codeblock n | C5 C5 C5 C5 C5 C5 C5 79 |
 codeblock = 7 information octets + 1 octet (7 BCH parity bits, inverted, + filler bit 0)
 BCH(63,56), g(x) = x^7 + x^6 + x^2 + 1; the spacecraft corrects single-bit errors (SEC mode).
```

### 3.2 TC transfer frame (<= 64 octets)

```
| TFVN 2b=0 | Bypass 1b | Ctrl 1b | spare 2b | SCID 10b | VCID 6b=0 | Length-1 10b | N(S) 8b |
| [SDLS security header: SPI 2 | SN 4] | data field | [SDLS MAC 16] | FECF 2 |
```

* Type-AD (sequence controlled, FARM window 10), Type-BD (bypass) and Type-BC (control: UNLOCK
  `00`, SET V(R) `82 00 vr`).
* Data field of AD/BD frames: one TC space packet, APID 0x0C0, no secondary header, user data =
  `opcode | arguments` (05 Dictionary section 4).

### 3.3 SDLS authentication (CCSDS 355.0-B-2, authentication-only service)

* Security header: SPI (u16) + sequence number SN (u32). Trailer: MAC = HMAC-SHA-256 truncated to
  16 octets, computed over `TC primary header || security header || data field`.
* Spacecraft order of checks: length, SPI, **MAC**, then anti-replay (SN must exceed the last
  accepted SN) - a forged frame can never advance the replay window.
* The ground gives every (re)transmission a fresh SN (COP-1 retransmissions would otherwise be
  replays). Both ends persist the SN (`archive/sdls_sn.txt`; spacecraft EGSE
  `egse_data/sdls_last_sn.txt`, restored into the MCU at boot).
* Authentication, not encryption: amateur-radio rules require uplink content to be readable.

### 3.4 COP-1

FARM-1 on the spacecraft (window 10, lockout, retransmit, wait flags; the CLCW in every TM
frame). FOP-1 on the ground (`fop.py`): V(S) numbering from N(R), go-back-N retransmission on
the CLCW retransmit flag or T1 expiry, transmission limit -> suspend, lockout -> operator
`UNLOCK`. Command life cycle shown everywhere: QUEUED -> RADIATED(n) -> ACCEPTED (CLCW N(R)) ->
EXECUTED / FAILED (CMDVER packet) or TIMEOUT / SUSPENDED.

## 4 Link-layer procedures specific to this mission

### 4.1 Coordinated air-rate / channel change (`LINKRATE`, `LINKCHAN`)

1. GDS uplinks `AIRRATE code revert_s` or `CHANNEL ch revert_s` (hazardous: confirmation).
2. Spacecraft verifies (CMDVER EXECUTED), sends **two more frames on the old setting**, switches,
   and arms a revert timer (`revert_s`, default 180 s).
3. GDS retunes the station E22 (Radio Control Unit) after it sees the next frame (or 1.5 frame
   periods), then sends `NOOP` on the new setting.
4. A valid TC received on the new setting disarms the spacecraft's revert. Without it the
   spacecraft reverts by itself (EVR 0x0602, FDIR RATE_REVERT); the GDS also reverts if no frame
   arrives within `revert_s + 30 s`. The link always heals.

### 4.2 Hibernation and Wake-on-Radio

`HIBERNATE beacon_s`: the spacecraft E22 becomes a WOR receiver (cycle code 3 = 2000 ms, EBYTE
"T = (1 + WOR) x 500 ms"), sends one HK frame every `beacon_s` and stays silent otherwise. `WAKE`
(ground directive): the station E22 transmits `BD MODE SAFE` as a WOR transmitter with the same
cycle code (EBYTE: "the sender and receiver must be consistent"); the wake-up preamble spans the
spacecraft's listening cycle.

### 4.3 Radio health self-reports

Both E22s run with REG1 bit 2 set: while under-voltage (< 4.5 V), over-voltage (> 15 V) or
over-temperature (> 120 degC) the module stops transmitting and emits `FF FF FF 01/02/03/04`
every 500 ms (EBYTE manual 5.7). The spacecraft turns these into FDIR flags (LOW_BUS_V,
PA_OVERTEMP, RADIO_FAULT), EVRs and - for over-temperature - a 5-minute transmitter cool-down;
the GDS logs the station radio's own reports as RADIO errors.

### 4.4 Time correlation

`TIMECORR` packets carry the SCLK at which the first octet of a reference frame (identified by
its MCFC) entered the radio and its measured busy time. The GDS computes
`SCET = ERT_end - UART time (237 x 10 / 9600 s) - air time - OWLT - radio_latency_s` and fits
SCLK -> SCET by least squares per partition (drift in ppm and residual RMS on the dashboard).
`radio_latency_s` is the station calibration constant ([08 V&V](08_Verification_and_Test.md) T-TM-04).

## 5 Interfaces inside each board (Arduino Router Bridge)

| Board | Method | Direction | Payload |
|---|---|---|---|
| UNO Q | `tm_cadu` (notify) | MCU -> Linux EGSE | CADU, hex (archived) |
| UNO Q | `sc_status`, `sc_evr`, `sc_boot`, `sdls_sn` (notify) | MCU -> EGSE | JSON status, EVR text, reset cause, SN |
| UNO Q | `set_partition`, `set_sdls_sn`, `hl_cltu` (provide_safe) | EGSE -> MCU | boot count, SN restore, hardline CLTU (<= 100 octets: 256-octet inbound RPC buffer) |
| VENTUNO Q | `fp_line` (provide_safe) | GDS -> MCU | `S|aos|alarm|uplink`, `Q|confirm-id` |
| VENTUNO Q | `rcu_mode` (provide_safe, called) | GDS -> MCU | E22 mode 0-3, returns AUX |
| VENTUNO Q | `fp_in` (notify) | MCU -> GDS | `K|typed`, `CMD|text`, `CONFIRM|id`, `AUTH|id`, `H|hello` |

`provide_safe` handlers run in the sketch's loop thread (Arduino_RouterBridge 0.4.3; plain
`provide` handlers would run in a 500-octet-stack bridge thread). The flight loop never calls
`Bridge.call` (it has no timeout); it only notifies.
