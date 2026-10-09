# 11 - Standards Status, Design Trade-offs and Roadmap

## 1 What is implemented, and how current it is (October 2026)

| Layer | Standard used | Status | Notes |
|---|---|---|---|
| Sync & channel coding | 131.0-B-5 | current issue used | 255-bit randomizer kept (backward-compatible option); RS(255,223) |
| TM data link | 132.0-B-3 | current | |
| Space packets | 133.0-B-2 | current | |
| TC sync & coding | 231.0-B-4 | current | BCH(63,56); the newer LDPC TC option is for high-rate links |
| TC data link | 232.0-B-4 | current | |
| COP-1 | 232.1-B-2 | current | |
| Time codes | 301.0-B-4 | current | CUC with implicit P-field |
| Link security | 355.0-B-2 | current | authentication-only (amateur rules) |

## 2 Deliberately not used - and why

| Standard / technique | Why not here |
|---|---|
| USLP (732.1-B, Unified Space Data Link Protocol) | Designed to unify TM/TC/AOS with larger frames and more virtual channels. TM/TC frames are still what deep-space missions and ground tools (YAMCS, OpenC3) decode by default, and our 236-octet packets gain nothing from USLP's features. A USLP framing option is a possible extension (section 4). |
| LDPC / turbo / convolutional coding (131.0-B) | The E22 delivers a packet only if its own LoRa CRC passes. A stronger code inside the packet cannot recover packets the radio has already dropped; RS(255,223) is kept for residual errors and measurable link quality. |
| 131071-bit randomizer (131.0-B-5) | Introduced to avoid spectral lines on high-rate links; at <= 62.5 kbit/s over LoRa (which whitens its payload itself) it changes nothing, and the 255-bit sequence is what ground tools decode by default. |
| CFDP (727.0-B) | File delivery protocol; useful once files (e.g. SSR dumps) are transferred. Roadmap. |
| DTN / Bundle Protocol v7 | Store-and-forward networking across multiple hops; this is a single point-to-point link. Roadmap if relays are added. |
| SLE (Space Link Extension) | Connects ground stations to remote mission centres. The GDS offers UDP TM/TC links and Prometheus instead, which suit one station. |
| SDLS encryption | Not permitted on amateur bands. |

## 3 Radio software: why not `LoRa_E22.h` or RadioLib

* **`LoRa_E22.h`** (the "EByte LoRa E22 Series Library" by Renzo Mischianti) drives EBYTE E22 UART
  modules - the same register protocol used here (C0/C1/C2 commands, M0/M1/AUX). This project uses
  its own small driver (`drivers/e22.*`) instead, because the flight loop must never block (the
  transmit path is a non-blocking state machine timed from AUX), the receive path must separate
  CLTUs, the RSSI byte, ambient-noise replies and the E22's `FF FF FF` abnormal-status reports in
  one byte stream, and the configuration is written volatile (C2) with read-back verification at
  every boot.
* **RadioLib** supports the SX1268 radio chip over **SPI**. The E22-400T37S is a **UART** module
  with its own microcontroller in front of the SX1268, so RadioLib cannot drive it. It applies to
  EBYTE's SPI variants (E22-400M30S, E22-400M33S): see section 4.

## 4 Roadmap (ranked by value)

1. **SPI radio for range**: an E22-400M30S/M33S with RadioLib's `SX1268` class - SF12 at narrow
   bandwidth (~20 dB more sensitivity than the T37S's 2.4 kbit/s setting), plus `getSNR()`,
   `getFrequencyError()` (Doppler-like measurements), channel activity detection, and true
   per-packet SNR on the dashboard. Only `drivers/e22.*` and the ground radio interface change.
2. **Measured sensitivity table** (test T-RF-03) replacing the scaled estimates for the faster
   air rates.
3. **CFDP** class-1 file transfer of SSR contents and EGSE logs.
4. **USLP framing mode** selectable in `config.h` / `station.toml` for interoperability tests.
5. **Ground antenna tracking** (az/el rotator driven by the GDS) for balloon flights.
