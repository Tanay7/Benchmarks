# 11 - Standards Status, Deliberate Omissions and Roadmap

This page answers three questions: which space standards the project implements and how far
(section 1), what it deliberately leaves out and why (sections 2 and 3), and what could come next
(section 4). Every design choice behind it is in [12_Design_Decisions.md](12_Design_Decisions.md);
the documents themselves are listed in [10_References.md](10_References.md).

---

## 1 What is implemented, and how current it is (October 2026)

"Issue used" is the issue the code was written and verified against; it was the latest issue known
to the project in October 2026. CCSDS revises its books: check https://public.ccsds.org before
relying on an issue number (that site could not be reached from the build environment).

| Layer | Standard | Issue used | What is implemented | What is not (within that standard) |
|---|---|---|---|---|
| Sync and channel coding (TM) | CCSDS 131.0-B | B-5 (2023) | ASM, RS(255,223) E = 16 dual basis with interleave depth 1, shortened to a 200-octet frame (virtual fill 23), 255-bit pseudo-randomizer | LDPC, turbo, convolutional codes, the 131 071-bit randomizer (section 2) |
| TM data link | 132.0-B | B-3 (2021) | fixed-length 200-octet frames, VC0-VC3 + OID (VC7, idle data = the clear-text identification), MCFC / VCFC, packets spanning frames with the First Header Pointer (checked against the buffered packet on the ground), idle packets (APID 0x7FF) to complete a frame, OCF = CLCW, FECF | transfer frame secondary header |
| Unified link | 732.1-B (USLP) | B-2 (2021) | TM direction: fixed 200-octet frames, VCID 0-3, OID on VCID 63, construction rule '000' (packets spanning TFDZs), VCF count length 1, OCF; TC direction: variable frames up to 64 octets, rule '111', COP-1 control commands; auto-detection on both ends | MAP multiplexing (MAP ID always 0), truncated frames, insert zone, variable-length TM frames |
| Space packets | 133.0-B | B-2 (2020) | TM packets with a 6-octet CUC secondary header, TC packets (APID 0x0C0) without one, idle packets, per-APID 14-bit sequence counts | segmented packets (sequence flags other than '11') |
| Time codes | 301.0-B | B-4 (2010) | CUC, 4 coarse + 2 fine octets, implicit P-field 0x2E; SCLK partition carried separately | CDS / CCS formats |
| TC sync and coding | 231.0-B | B-4 (2021) | CLTU, BCH(63,56) with single-error correction on the spacecraft | LDPC TC codes (meant for high-rate uplinks) |
| TC data link | 232.0-B | B-4 (2021) | Type-AD, Type-BD, Type-BC frames on VC0, frame limit 64 octets | segment headers, multiple TC VCs |
| COP-1 | 232.1-B | B-2 (2010) | FARM-1 on the spacecraft (window, lockout, wait, retransmit, FARM-B counter), FOP-1 on the ground (Unlock, Set V(R), transmission limit, suspension, N(R) validation) | - |
| Link security | 355.0-B (SDLS) | B-2 (2022) | three SAs: TC AUTH (HMAC-SHA-256-128, SPI 1), TC AE (AES-256-GCM, SPI 2), TM AE (SPI 3), plus the TM null SA (SPI 0) as a flagged fallback; anti-replay by sequence number (TC) and (epoch, sn) (TM) | SDLS extended procedures (key management, over-the-air rekeying, SA management) - keys are installed by re-flashing (section 2) |
| File delivery | 727.0-B (CFDP) | B-5 (2020) | class 1 (unacknowledged): Metadata, File Data, EOF (with cancel), modular checksum, fault-location TLV on a non-zero condition; receiver states COMPLETE / INCOMPLETE / CHECKSUM_FAILURE / CANCELLED / ABANDONED | class 2 (acknowledged), filestore requests, TLVs in Metadata, segmentation control |
| Lossless compression | 121.0-B | B-3 (2020) | Adaptive Entropy Coder with the unit-delay predictor and mapper, n = 16, J = 16, r = 128, all basic options (zero-block with ROS, second extension, k-split, fundamental sequence, no compression); the ground decoder also covers the other J / n values of the official test data | coding-option and file headers (sections 6, 7): the parameters are fixed per APID |
| Tracking data | 503.0-B (TDM) | B-2 (2020) + Corrigendum 1 (2021) | KVN files per pass: received frequency, Doppler (when calibrated), carrier power, PC/N0 | XML form, two-way / ranging data types |
| Mission database | 660.0-B (XTCE) | B-2 (2020) = OMG XTCE 1.2 | generated from the dictionary: big-endian integer / IEEE-754 encodings, polynomial calibrators, units, static alarm ranges from the GDS limits, one container per APID (restriction on the APID), one MetaCommand per opcode with argument ranges, hazardous commands flagged | decoders that are not polynomials (the SPEC log code is exported raw with a note), algorithms, command verifiers |

Verification of each layer: [08_Verification_and_Test.md](08_Verification_and_Test.md). Protocol
layouts: [04_Space_Data_Link_ICD.md](04_Space_Data_Link_ICD.md).

---

## 2 Deliberately not used - and why

| Standard / technique | Why not here |
|---|---|
| LDPC, turbo or convolutional coding (131.0-B) | The E22 modules deliver a LoRa packet only when their own CRC passes, so a stronger code inside the packet cannot recover packets the radio has already dropped. On the SX126x path the project instead delivers CRC-failed packets to the Reed-Solomon decoder ("RS rescue", a concatenated code; decision D-21). |
| 131 071-bit randomizer (131.0-B-5) | Introduced against spectral lines on high-rate links. At <= 62.5 kbit/s over LoRa, which whitens its own payload, it changes nothing, and the 255-bit sequence is what ground tools decode by default (D-20). |
| AOS Space Data Link Protocol (732.0-B) | Meant for high-rate, often bidirectional, insert-zone services; TM (and USLP for interoperability tests) covers this link. |
| CFDP class 2 (acknowledged) | Needs NAK / ACK traffic on the uplink during a transfer. The uplink here is half-duplex, slow and shared with commanding; class 1 reports gaps (INCOMPLETE with the missing ranges) and a transfer can simply be repeated (D-27). |
| SDLS extended procedures (key management, OTAR) | The flight key is compiled into the firmware and rotated by re-flashing ([17_Security_Model.md](17_Security_Model.md) section 7). Over-the-air rekeying would add a key-management protocol and key storage on the MCU for a link that is normally within reach of its owner. |
| SDLS encryption on amateur bands | Implemented (AE is the default), but encryption that obscures content is generally not permitted on amateur frequencies: use AUTH or NONE there ([09_Regulatory_and_Safety.md](09_Regulatory_and_Safety.md)). |
| DTN / Bundle Protocol v7 | Store-and-forward networking across several hops; this is one point-to-point link. The on-board recorder (SSR) and CFDP cover store-and-forward needs. |
| Space Link Extension (SLE) | Connects ground stations to remote mission centres. One station is served instead by UDP frame / packet links (for Yamcs or OpenC3; telecommand frames are accepted from the station host only, D-62), the XTCE mission database and the Prometheus `/metrics` endpoint. An SLE-style service is on the roadmap. |
| Proximity-1 (211.x) | For short-range relay links between spacecraft; not applicable. |
| The E22 modules' own encryption (CRYPT registers) | Proprietary and not a CCSDS service. The registers are always written 0; SDLS provides security end to end instead. |

---

## 3 Radio software: what is used now

The earlier version of this page explained "why not RadioLib". That text is **obsolete**: since v3
the SX126x option of both sketches uses RadioLib 7.8.1, bundled and patched
([22_Bundled_Libraries.md](22_Bundled_Libraries.md)). In short:

* **UART modules (E22-400T33D default, E22-400T37S):** the project's own driver
  (`vgq1-spacecraft/sketch/src/drivers/e22.*` and `radio.*`; ground `dssq/radio/e22.py` with
  `dssq/radioctl.py`). Third-party E22 libraries exist for the same register protocol (C0 / C1 /
  C2 commands, M0 / M1 / AUX), but the flight loop must never block (transmission is a state
  machine timed from AUX with a modelled minimum busy time), the receive path must separate CLTUs,
  the RSSI byte, ambient-noise replies and the T37S `FF FF FF` abnormal-status reports in one byte
  stream, and the configuration is written volatile (C2) at every boot and verified by read-back.
  One binary serves both modules; a jumper on A0 selects the T37S (D-09).
* **SX126x on SPI (E22-400M33S reference module):** RadioLib 7.8.1 from its upstream release, with
  two documented patch hunks (a VENTUNO Q platform entry; no heap per SPI transfer and unused modules
  excluded), and every library behaviour that can be handled from our side handled in our drivers
  (image calibration, OCP, bounded BUSY waits, packet status, duty-cycle periods). An in-house SX126x
  driver was planned at one point (contract 23) and dropped by the owner in favour of the patched
  RadioLib (contract 25, 30; D-40).

---

## 4 Roadmap

### 4.1 Done since the previous roadmap

| Earlier roadmap item | Status |
|---|---|
| SPI radio for range (SF12 at narrow bandwidth, per-packet SNR and frequency error, CAD) | done: SX126x option, LoRa profiles 0-7, AFC, RS rescue (D-10, D-21, D-26) |
| Measured sensitivity table (test T-RF-03) | tool done (`tools/link_budget.py sensitivity`); the measurement needs hardware |
| CFDP class-1 file transfer of SSR contents and EGSE logs | done (D-27) |
| USLP framing mode | done, selectable (D-23) |

### 4.2 Deep-space-network-style features not implemented ("leave it for now")

These were identified with the owner and deliberately left for later.

**Software (ground or flight)**

| Feature | What it would add |
|---|---|
| Pass reports and data accountability | per-pass summary: frames expected / received / lost, packets per APID, gaps, SSR playback completeness |
| BER / FER test mode with known PN frames | a link test with a known pseudo-noise pattern to measure bit and frame error rates directly |
| Predicts and sequence of events | pass predictions (AOS / LOS times, elevation) and a timed list of planned station and spacecraft actions |
| Acquisition frequency sweep | the ground searching in frequency until it acquires the downlink (today: AFC once packets arrive) |
| Uplink Doppler pre-compensation | the ground shifting its transmit frequency so the spacecraft receives on centre |
| Station G/T and noise-temperature estimate | a measured figure of merit of the receiving station |
| Operator alerts | web-hook or e-mail notification on RED alarms, beacon URGENT or LOS |
| SLE-style frame delivery to an external mission control system | a standard service interface to Yamcs or OpenMCT beyond today's UDP links |
| Light-time-aware commanding | time-tagged commands and verification windows that account for one-way light time |

**Hardware**

| Feature | What it would add |
|---|---|
| RTL-SDR open-loop spectrum recorder / waterfall | an independent recording of the band during passes |
| Antenna rotator tracking (hamlib) | automatic az/el pointing for balloon or long-range tests |
| Arraying / diversity with a second station | combining two receivers' frames (the CADU archive format already carries ERT and RSSI) |
| GPS-PPS station time | an accurate, independent station clock for time correlation and TDM time stamps |

**Not feasible with this hardware**

| Technique | Why |
|---|---|
| True ranging | the SX126x has no ranging engine, and the UART modules hide timing; a 1 ms timing error is already 150 km of range (two-way) |
| Delta-DOR / VLBI | needs two widely separated stations with phase-coherent wide-band recording |
| Carrier-phase tracking | the LoRa modems report only a frequency error per packet, not a continuous carrier phase |

### 4.3 Open items

Hardware verification items (radio power codes, AFC sign, TCXO, supply under transmit, App Lab port
exposure, timing on the real boards, ...) and residual risks are tracked in
[20_Known_Limitations.md](20_Known_Limitations.md); test procedures are in
[08_Verification_and_Test.md](08_Verification_and_Test.md). One design item is marked for owner
review: the three URGENT beacon conditions added by the autonomy work (AVI_OVERTEMP, CMD_LOSS,
FARM_LOCKOUT; D-50).
