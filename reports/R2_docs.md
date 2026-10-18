# R2 - Documentation inventory and structure (branch `claude/unoq-ground-station`)

Input for the implementers and documentation writers of the UNO Q ground-station branch.
Repository: `/home/user/Benchmarks`, branch `claude/unoq-ground-station`, tree = v1 head `13a60c5`
(nothing in the repository was modified by this task). Every "L<n>" is a line number in that tree.
Datasheet texts are cited by the extracted-text file and line ("ds/<file> L<n>") under
`/tmp/claude-0/-home-user/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/ds/`, or by datasheet section.

Abbreviations used in the VENTUNO tables: **ID** = identity, title, diagram, repository map; **HW** =
hardware / wiring / pins; **PWR** = power; **SETUP** = setup steps; **APPLAB** = App Lab use (zips,
import, URLs, ssh/scp to the board); **PERF** = performance or resource figures; **CITE** = datasheet
citation (ABX00181); **TEST** = test item; **LIMIT** = known-limitation / VERIFY item; **DEC** =
design decision; **HIST** = historical record (changelog, completed test record); **impl.** = the
passage is VENTUNO-specific without naming the board (station MCU pins D2/D3/D4/D5/D6/D7/A0,
PH11/PH12, +5V_LIMITED, barrel jack, STM32H5F5, QCS8275, Ubuntu, 16 GB/64 GB, M.2, `ventunoq`).

---

## 1 Summary of findings

1. **29 project Markdown files, 243,684 words** (plus `.pytest_cache/README.md`, a pytest-generated
   file, out of scope). Biggest: docs/13 (42,026 words), docs/12 (23,450), docs/14 (15,932), docs/08
   (13,095), docs/03 (11,429). No Markdown files exist under the bundled `libraries/` folders.
2. **VENTUNO Q is named on 226 lines in 27 of 29 files** (only docs/02 and docs/05 have none), plus
   dozens of implicit references (station-MCU pins, +5V_LIMITED, QCS8275, 16 GB/64 GB, M.2 NVMe,
   Ubuntu 24.04, `ventunoq` FQBN and zip name, the owner's host name `TanVentuno.local`, docs/00
   L325). The heaviest are docs/03 (39 lines, the whole §5 and its diagram), docs/00 (31), docs/08
   (18), docs/12 (14), docs/22 (12), dssq-groundstation/README.md (12+), docs/01 (11), docs/06 (10,
   the whole document is the VENTUNO "front panel").
3. **E22-400T37S / E22-400TBH-02 material is spread over 24 files and 232 lines** (272 with the
   "5 W" / "37 dBm" mentions) with no
   single authoritative chapter (details in section 4). Confusing points: a cap drawing that hides
   which pins the caps bridge (docs/03 L291-297), two different ground procedures (docs/03 §5.3.2
   vs docs/00 §13.2; the latter forces the RCU against the D-68 default), attenuator ratings
   ">= 5 W" vs ">= 10 W", an unlabeled or contradictory meaning of `FF FF FF 04` (docs/04 L99 says
   "other abnormal status"; the T37S manual 5.7 and docs/05/13/14 say over-voltage + over-temperature),
   a dangling source reference "extract scratchpad `t37s.txt`" (docs/02 L122), the STATE pin
   (TBH-02 header 19) never documented, and vendor contradictions the docs do not warn about
   (TBH-02 manual: supply 4.5-15 V vs 5-12 V; its §4 says "unplug M0 and M1 caps" for
   configuration although §3 calls that deep hibernation). Whether the TBH-02 can draw module power
   from its USB-C is **not found** in the TBH-02 manual (its "Power supply interface" row is blank,
   ds/6e826cce L118-128): a hazard question for the hub-connected ground UNO Q.
4. **Sensor interfacing**: the ESLOV connector is mentioned nowhere (0 hits). All four instruments
   are documented on one shared bus `Wire2` A4/A5 (docs/03 §4.7, D-13, FAQ L258-266, docs/00 §8.5,
   docs/15 §3.5, docs/16 §4.5) - every one of these must change for separate buses. The Nicla ARU
   I2C page protocol (two 32-octet pages, CRC-8) is documented only in the firmware header
   (`nicla_sense_me_aru.ino` L13-33); the ICD (docs/04) has **no** instrument-interface section.
   The internal Nicla topology (BMP390 + BME688 on SPI to the BHI260AP, BMM150 on a dedicated I2C
   to the BHI260AP; ds/414d9b73 L406-419) is not described. BHI260AP 6-DoF content is limited to
   the raw accel/gyro fields of ATT (docs/05 L135-157, docs/13 §9.2 L2489).
5. **"Voyager" occurs 7 times**: 6 in docs (docs/01 L43, L633; docs/10 L158; docs/12 L92, L683;
   docs/20 L195) and 1 code comment (`vgq1-spacecraft/sketch/src/fsw/autonomy.h` L7, also inside
   `dist/vgq1-spacecraft-unoq.zip`). None is the title of a cited document; docs/20 L195 sits next
   to a real citation (J. Taylor (ed.), *Deep Space Communications*, JPL 2014, 3.7.3.2) whose title
   does not contain the word. The D-49 anchor is an explicit `<a id="d-49">`, so renaming the heading
   is link-safe.
6. **Short-range orientation**: the default worked budget is "10 km, two dipoles, 2 m / 10 m (the
   default)" (docs/02 L387), the scenario table starts with "Bench" (docs/02 L477), the setup guide
   culminates in "First contact on the bench" (docs/00 Part 11) with "Going for distance" as a
   25-line afterthought (docs/00 L899-924), the field-test chapter has 3 rows (docs/08 §12 L838-846)
   against 12 bench RF tests, and docs/03 §10 is titled "Antennas, coax and bench RF".
7. **Machine-checked documentation** constrains the writers: docs/05 is generated
   (`tools/gen_dictionary_doc.py`), docs/13 is drift-tested in both directions (`tests/test_docs.py`),
   docs/12 Appendix B is checked against code citation tags (`tests/test_citations.py`), and code
   strings cite "docs/17_Security_Model.md section 7.6" (asserted by `tests/test_sdls.py` L705).
8. **Stale/contradictory content to fix while rewriting** (section 6): D-68 default (E15-USB-T2,
   `rcu = false`) contradicted by dssq-groundstation/README.md L28, docs/03 L70, docs/03 §5.5 diagram,
   docs/03 BOM §7.2 (no E15-USB-T2 at all) and docs/00 L99; docs/08 says "70 ok" (L30) and "64 ok"
   (L127); docs/08 ground-test arithmetic 739 + 7 + 4 = 750 vs "752" (L29, L110, L119); flight build
   sizes differ between docs/01 §9.1 and docs/08 §2.6; CADU volume 16 MB/day (docs/01 L952) vs at
   most 14.3 MB/day (docs/18 L308); D-13/D-15 text "no Qwiic" vs the 2026-10-12 revision.
9. The App READMEs link to `https://github.com/Tanay7/vgq1-deep-space-link/blob/main/docs/...`
   (34 links, 17 in each); on this branch they would open the **main-branch (VENTUNO) documents**. The READMEs
   are packaged into the zips (`tools/package_apps.py` L157).

---

## 2 Inventory (every Markdown file)

Word counts from `wc -w` (the outline generator in Appendix A counts the same text with
`str.split`, differences of 1-9 words). "V" = lines naming VENTUNO (case-insensitive, incl.
`ventunoq`); "T" = lines naming T37S/TBH; "Vg" = Voyager.

| File | Purpose (from its own opening) | Lines | Words | V | T | Vg | Notes |
|---|---|---|---|---|---|---|---|
| README.md | Project landing page: what it is, at a glance, repo map, quick start, doc index, verification | 234 | 2108 | 9 | 4 | 0 | title names VENTUNO (L1) |
| CHANGELOG.md | Keep-a-Changelog history by phase (L1-15) | 490 | 5234 | 7 | 3 | 0 | history: keep verbatim, add a new section |
| docs/00_Beginner_Setup_Guide.md | "from a box of parts to a working two-way radio link" (L3) step by step | 1047 | 9941 | 31 | 12 | 0 | the "handbook"; Part 9 is VENTUNO wiring |
| docs/01_System_Design.md | System design, ConOps, architecture, data flow, modes, FDIR, autonomy, COP-1, budgets | 971 | 10348 | 11 | 6 | 2 | |
| docs/02_Link_Budget_and_RF_Analysis.md | "how far VGQ-1 and DSS-Q1 can talk, why, and how to check it" (L3) | 874 | 9495 | 0 | 22 | 0 | short-range default budget |
| docs/03_Hardware_Integration.md | Wiring reference for both ends: pins, power, BOM, thermal, magnetics, RF (L3-10) | 1025 | 11429 | 39 | 56 | 0 | central rewrite target |
| docs/04_Space_Data_Link_ICD.md | Every octet across the radio link and every MCU-Linux call (L3-6) | 956 | 9585 | 1 | 5 | 0 | no instrument-interface section |
| docs/05_Telemetry_and_Command_Dictionary.md | GENERATED dictionary (L3) | 625 | 8553 | 0 | 5 | 0 | never edit by hand |
| docs/06_Displays.md | Every display except the dashboard: station lamps/buzzer/CardKB/RCU on the VENTUNO MCU, LED matrix, EGSE board (L3-6) | 316 | 3207 | 10 | 0 | 0 | ground half is VENTUNO-only |
| docs/07_Operations_Procedures.md | How to run both ends once built (L3-5) | 891 | 9383 | 6 | 10 | 0 | |
| docs/08_Verification_and_Test.md | Automated checks and hardware tests (L3-5) | 904 | 13095 | 18 | 11 | 0 | T-GS section VENTUNO; thin field tests |
| docs/09_Regulatory_and_Safety.md | Licence, RF/electrical/thermal/optical safety, software safety features (L3-6) | 244 | 2842 | 2 | 12 | 0 | |
| docs/10_References.md | Every external document, standard, library, tool with version (L3-5) | 167 | 2447 | 4 | 2 | 1 | needs E15/E51, ESLOV, ABX00173 entries |
| docs/11_Standards_Status_and_Roadmap.md | Standards implemented, omissions, roadmap (L3-6) | 130 | 1911 | 1 | 3 | 0 | |
| docs/12_Design_Decisions.md | Decision log D-01..D-70, superseded table, Appendix A (Phase C), Appendix B (citation tags) | 1221 | 23450 | 14 | 13 | 2 | 70 explicit `<a id="d-NN">` anchors |
| docs/13_Web_UI_Reference.md | Operator reference for every tab/panel/number of the dashboard (L3-7) | 3126 | 42026 | 3 | 24 | 0 | drift-tested by tests/test_docs.py |
| docs/14_Troubleshooting_and_Event_Response.md | Fault tree + complete EVR/ground-event/alarm/FDIR catalogues (L3-9) | 737 | 15932 | 3 | 10 | 0 | |
| docs/15_Configuration_Reference.md | Every `config.h` setting and `station.toml` key; station sketch build settings (L3-8) | 474 | 7980 | 1 | 10 | 0 | §5 = VENTUNO sketch pins |
| docs/16_Developer_Guide.md | Repo map, how pieces connect, checklists, coding standard, checks, CI, packaging (L3-6) | 584 | 4958 | 3 | 0 | 0 | |
| docs/17_Security_Model.md | Security controls, assets, trust boundaries, threats, procedures (L3-7) | 400 | 7290 | 2 | 0 | 0 | §7.6 cited by code |
| docs/18_Data_Products.md | What is recorded, where, formats, loading in Python (L3-6) | 647 | 5668 | 3 | 0 | 0 | 64 GB eMMC / M.2 assumptions |
| docs/19_Glossary.md | Every acronym and term (L3-6) | 320 | 4667 | 6 | 1 | 0 | |
| docs/20_Known_Limitations.md | Register of VERIFY items, limits by design, residual risks (L3-6) | 261 | 8034 | 7 | 6 | 1 | §1.3 is the VENTUNO platform |
| docs/21_Test_Records.md | Fill-in templates + completed hardware records (L3-8) | 668 | 7469 | 7 | 9 | 0 | completed records are history |
| docs/22_Bundled_Libraries.md | RadioLib 7.8.1 and Arduino_RouterBridge 0.4.3, patches, tools (L3-7) | 491 | 5715 | 12 | 0 | 0 | RadioLib hunk 1 exists for the VENTUNO Q |
| docs/FAQ.md | Short plain-language answers with links (L3-5) | 366 | 4048 | 7 | 4 | 0 | one-bus answer L258-266 |
| docs/Project_Summary.md | One-page summary (L1-5) | 72 | 764 | 1 | 1 | 0 | |
| vgq1-spacecraft/README.md | Spacecraft App README (packaged into the zip) | 173 | 2612 | 6 | 2 | 0 | links to blob/main |
| dssq-groundstation/README.md | Ground App README (packaged into the zip) | 223 | 3493 | 12 | 1 | 0 | L28 contradicts D-68 |

Section outlines (H2 and H3 headings with line numbers) for every file are in **Appendix A**.

---

## 3 Constraints the documentation writers must respect

| Constraint | Where | Consequence for the rewrite |
|---|---|---|
| docs/05 is generated from `dssq/dictionary.py` and the flight source | header docs/05 L3; `tools/gen_dictionary_doc.py` L2, L18, L36, L163 | New 6-DoF fields, sensor bits, commands appear only by regenerating; never hand-edit |
| docs/13 drift test, both directions | `tests/test_docs.py` L1-17, `KEY_BY_KEY_BLOCKS` L42-50, tests L174-260 | Every new snapshot key (e.g. 6-DoF IMU, hub/USB status, station I/O on the UNO Q) must be written in docs/13; every backticked dotted path written in docs/13 must exist in a real snapshot. A path like `station_io.fw` must not be renamed in docs without the code |
| docs/12 Appendix B vs code citation tags | `tests/test_citations.py` L1-8, L121-144; anchor `#appendix-b---citation-tags-in-code` used by docs/08 L118, docs/19 L62, docs/20 L70 | Keep the Appendix B heading text; any new tag in code (e.g. "contract 4x") needs a row; rows no longer cited fail |
| Code cites doc sections | `dssq/ccsds/sdls.py` L149 `SN_DOC = "docs/17_Security_Model.md section 7.6"` (asserted in `tests/test_sdls.py` L705, also L484, L694, L715, L779); `dssq/cltu.py` L32, L81; `gds.py` L121, L240 (docs/09 1.3), L402 (docs/17 A3), L414 (docs/17 7.6), L2448 (docs/17 T16); `config.py` L385; `station.toml` L21 (docs/09 1.3), L65, L79, L100 (docs/08 T-RF-07/02/03), L112 (docs/09); `config.h` L4 (docs/03), L7, L201, L215, L225 (docs/08); flight `sketch.ino` L14-15; full list Appendix C | Keep docs/17 §7.6, docs/17 IDs A3/T16, docs/09 §1.3 and docs/08 test IDs stable, or change code + tests in the same commit |
| Cross-file anchors | docs/09 `#13-station-identification` (both App READMEs, absolute URLs); docs/12 `#appendix-b---citation-tags-in-code`; docs/12 `#d-NN` are explicit `<a id>` (70) | Renaming docs/09 §1.3 breaks the App README links; D-NN anchors are safe |
| Prose section references | Counted 186 "docs/NN section x / part x / §x" references (lower bound): docs/17 36 (30 of them "section 7.6"), docs/03 36, docs/01 16, docs/00 15, docs/07 12, docs/09 12, docs/02 10, docs/04 9, docs/08 8, docs/15 7, docs/13 6, docs/14 5, docs/18 5, docs/16 4, docs/22 2, docs/05 1, docs/11 1, docs/20 1 | A renumbered docs/03 or docs/00 needs a sweep of every referencing document (the docs/00 headings themselves cite "docs/03 section 4.2.1", "4.3", "4.7": L516, L529, L565) |
| Packaging | `tools/package_apps.py` L9, L67, L161, L1043 (`dssq-groundstation-ventunoq.zip`, `ventunoq`); `tests/test_packaging.py` L405, L531-544 | Zip name appears in README L150, docs/00 L147/L246, docs/14 L197, docs/16 L522, docs/21 L635, docs/22 L412, dssq README L36: change all together with the tool |
| `station.toml` text checked | `tests/test_logger.py` L76-85 (needs "VERIFY", "ArduinoApps"; archive defaults 180 / 16.0 / 2048.0) | docs/15 §4.14 and docs/18 §7 restate these defaults; a 32 GB eMMC may justify new defaults, which needs the test changed |
| App README links | 34 absolute `blob/main/docs/...` links (vgq1 README 17, dssq README 17) | Decide branch-specific URLs or a relative policy (open question Q7) |

---

## 4 Per-document findings (a-f)

Items (a)-(f) as in the task. (g) is in section 7. "none" means searched and not found.

### 4.1 README.md (234 lines, 2108 words)
Purpose: project landing page. Outline: Appendix A.
- **(a) VENTUNO**: ID L1 (title "...on Arduino UNO Q and VENTUNO Q"), L13 (DSS-Q1 = VENTUNO Q), L36 (mermaid), L101 (repo map), impl. L37 and L108 (station I/O sketch: CardKB, lamps, RCU); HIST L24 (status); APPLAB L150 (`dssq-groundstation-ventunoq.zip`), L159 (`scp ... <VENTUNO-Q-IP>`), L180 (`http://<VENTUNO-Q-address>:8080`); HW L178 (A0 drives the buzzer).
- **(b) T37S**: L18-19 (selection), L57 (free-space ranges), L176-179 (A0 jumper + `model`).
- **(c) Sensors**: L10-12 instrument list; L33 diagram "Nicla Sense ME, AS7265X, RM3100 x1-2 (I2C, A4/A5)" (one bus); L98; L111; L148 (flash Nicla). Missing: ESLOV, separate buses, 6-DoF IMU.
- **(d) Voyager**: none.
- **(e) Short range**: L22-28 status "first bench link" (history - keep, but the page should state the ultra-long-range goal first); L57-58 range rows start with 11.7 km at 2 m.
- **(f)**: L53 and the docs/04 reference "section 2" fine; L57 cites docs/02 "section 6.2" for per-model free-space ranges (they are tabulated in docs/02 §5.6 and repeated as a column of §6.2) - minor.

### 4.2 CHANGELOG.md (490 lines, 5234 words)
- **(a)** HIST only: L18, L147, L151, L162, L342, L412, L485. Recommend: keep verbatim (history), add a new top section for this branch (open question Q2).
- **(b)** L401, L433, L483 (history). **(c)** L453-454 (BMP390/BMM150 scaling fix), L462, L477 (history). **(d)** none. **(e)** history only. **(f)** none.

### 4.3 docs/00_Beginner_Setup_Guide.md (1047 lines, 9941 words) - "the handbook"
- **(a) VENTUNO (31 lines)**: ID L7; HW parts L93 (ABX00181), L99 ("3.3 V-logic USB-UART adapter | ground radio to the VENTUNO Q"); PWR L102 (12 V/5 A barrel), L225, L612, L616, L621 (+5V_LIMITED); SETUP/APPLAB L124, L147 (zip), L230, L232, L246, L313, L323, L325 (owner host name `TanVentuno.local`), L336, L361, L390, L465 (heading 7.2 "on the VENTUNO Q"), L644, L935, L972, L994, L1005, L1033; HW L628, L673, L679 (RCU table), L885 (T37S on the ground), L891 (A0 buzzer). impl.: the whole Part 9 L606-724 (D2/D3/D4 RCU, D5/D6/D7 lamps, A0 buzzer, header SDA/SCL CardKB, JANALOG 4/5), Part 15 L926-978 (host mode, "Linux service"), Part 16 L979-1019 (paths).
- **(b) T37S**: L28 (parts table), L68, L95/L105 (">= 5 W" loads/attenuators), L848-897 Part 13 (13.1 spacecraft L856-876, 13.2 ground L878-893), L1042 (EVR 0x0311).
- **(c) Sensors**: Part 4 L194-216 (flash Nicla: Mbed Nicla core, Arduino_BHY2 1.0.8, banner `... accel +/-... g, gyro +/-... dps, mag +/-... uT`); L96-98 parts; §8.5 L565-591 (one bus; Nicla row d L579 "J2 pin 1 SDA -> A4, pin 2 SCL -> A5, pin 6 GND, pin 9 VIN -> buck 5 V; pin 7 not connected"); §8.6 L593-604 (boom); §9.5 L711-723 (CardKB on the VENTUNO header). Missing: ESLOV cable (which 4 of 5 wires, colours, connector), separate buses and their pull-ups, a 6-DoF IMU check (still/rotate), per-sensor checks beyond `sensor_health`.
- **(d) Voyager**: none.
- **(e) Short range**: Part 1 rule 2 L62-64 (bench separation); parts L105 "bench RF set"; L511, L613 (dummy loads); Part 10 L727, L745; **Part 11 "First contact on the bench" L749-803** (the guide's climax); Part 13 L854 ("attenuators rated >= 10 W for the bench"); **Part 14 "Going for distance" L899-924** only 25 lines, labelled "a day out" (L29).
- **(f)**: L99 (parts) and the BOM ignore the D-68 default (E15-USB-T2); 9.4 check 3 L708 "unplug the USB-UART adapter" (works for the E15 too); Part 13.2 L882-888 wires the RCU although `rcu = false` is the default (D-68); L95 "dummy loads (>= 5 W)" vs Part 13 ">= 10 W"; duplicated key procedure (Part 6 vs README step 3, docs/07 §10, docs/17 §7.1, both App READMEs §2.2); host mode duplicated (Part 15 vs docs/07 §1.2, dssq README §6); "update without losing state" duplicated (Part 16 vs App READMEs §2.5/2.6). `<UNO-Q-IP>` placeholder becomes ambiguous with two UNO Qs.

### 4.4 docs/01_System_Design.md (971 lines, 10348 words)
- **(a)**: ID L37, L163-172 (mermaid: QCS8275, STM32H5F5), L236 (GDS "as the VENTUNO Q App"); HW/CITE L55 (QCS8275, Ubuntu, 16 GB LPDDR5, 64 GB eMMC, ABX00181), L56 (STM32H5F5 up to 250 MHz), L134 ("ABX00162 / ABX00181 Bridge"); PERF L767 (`ventunoq` 1048576/262144), L772 ("rel090 ... has no VENTUNO Q"), L784-787 impl. (station build rows), L835 (variant `.conf` heap), L951-954 (16 GB, 64 GB, AES speed VERIFY), L971 (VERIFY row). impl.: §3.5 L293-300 (station pins), L350-352 (radio options, station pins), L458.
- **(b)**: L52, L159, L215, L351, L577 (abnormal status row), L905.
- **(c)**: L53 (instruments), L158 (one bus diagram), L203 (attitude), L578-582 (FDIR rows: avionics temperature, Nicla loss, re-probe), §7.6 L673-686, L854 (2 Nicla pages 2 x 3 ms at 100 kHz). Missing: 6-DoF IMU data path; separate-bus timing.
- **(d) Voyager**: L43 ("Voyager's stored command sequences"), L633 ("A Voyager-style table of 32 entries") - spacecraft analogy, not a citation.
- **(e)**: L41 states "built for the longest range the radios allow" (good, keep); L75-77 (mission-day preparation on the bench); L508 (TEST mode "bench").
- **(f)**: §9.1 flight build sizes L781-786 (234676/129020 ...) differ from docs/08 §2.6 L181-186 (234392/128924 ...): docs/08 predates D-67/D-69 (stale); L952 "raw CADUs about 16 MB per day" vs docs/18 L305-308 "at most 14.3 MB per day".

### 4.5 docs/02_Link_Budget_and_RF_Analysis.md (874 lines, 9495 words)
- **(a)**: none named; impl. L207 (station sketch), L747 (`rf_foff` to the station MCU).
- **(b) T37S**: L4, L81 (option table), **§2.3 L119-141**, L149, L164-172 (power codes), L268, L329, **§5.3 L414-419**, L436-448 (§5.6), L475-492 (§6.2-6.3), L506 (EBYTE 20 km), L522 (§6.5 "+2 dB net"), L810, L830.
- **(c)**: none (RF document).
- **(d)**: none.
- **(e) Short range**: §1 L51-55 ("about 59 dB of margin at 10 km"); **§5.1 L387 "Budget A: E22-400T33D, two dipoles, 10 km, antennas at 2 m and 10 m (the default)"**; §5.3/5.4 at 10 km; **§6.2 L477 first scenario "Bench, both on tables outdoors"**; §6.3 L492; §6.5 L518, L520 ("over 10 km"); §10.1 L790-791 ("More data at short range", "Bench work, fastest"); L131 ("bench tests need attenuators").
- **(f)**: L122 "(extract scratchpad `t37s.txt`)" is a reference to a file outside the repository; L135 lists `04` without its meaning; §5 L384 notes station.toml's antenna is a 12 dBi Yagi while Budget A "the default" uses two dipoles (confusing which is "the default"); missing: per-direction budgets for mixed pairs (T37S at one end, T33D at the other: +4 dB EIRP one way, -2 dB sensitivity the other).

### 4.6 docs/03_Hardware_Integration.md (1025 lines, 11429 words) - main rewrite target
- **(a) VENTUNO (39 lines)**: ID/CITE L6, L27 (source row ABX00181); HW L54 (contents), L72 (keyboard), L91-98 (diagram), L119 (logic levels), L136-138 (buses), L146-148 (CardKB silkscreen), L330, L352-362 (SX126x VENTUNO column), **§5 L506-680 entire** (5.1 pin map L508-527, 5.3.0 L542-556, 5.3.1 L558-591, 5.3.2 L593-609, 5.3.3 L611-614, 5.4 L616-645, 5.5 diagram L647-680); PWR L70, L260-261, **§5.2 L529-538**, L571, L691 (load row), **§6.5 L762-779**; BOM L842-858 (G1 ABX00181, G2 12 V/5 A); thermal L885, L918 ("never enclose the VENTUNO Q fan"); LIMIT L1023. impl. L17 (ground pins in sketch.ino).
- **(b) T37S (56 lines)**: L30-31 (sources), L68, L70, L88, L96, L121, L163, **§4.2.3 L247-261**, **§4.4 L286-313** (TBH-02 drawing and spacecraft wiring), **§4.5 L315-343** (strap), L492, **§5.3.2 L593-609**, L694-695, **§6.3 L731-744**, L776-777, L799, L804, L825, L827, L864, L867, L875, L887-888, L900, L909-911, L916-917, L967-969, L983, L989-1001, L1019.
- **(c) Sensors**: §3 L118-126 (levels: AS7265X, RM3100, Nicla VDDIO_EXT, CardKB), L133 (one bus), **§4.7 L373-456** (address table L378-385; per-device wiring: Nicla J2 L389-397, AS7265X L399-421, RM3100 L423-437; pull-ups and cable L439-456), §4.8 diagram L458-502, §5.4 CardKB L616-645, §6.1 loads L696-700, §8 L889-891, L912-918, §9 L923-959, §11 L1015-1016, L1021-1023. **Missing**: ESLOV connector J5 (pin 1 5 V, 2 INT, 3 SCL, 4 SDA, 5 GND; part SM05B-SRSS-TB; ds/414d9b73 L628-650, L400) and Nicla power over ESLOV (L435); the Nicla internal topology (L406-419); the ARU page protocol; separate buses per instrument; the RM3100 manual's own SA0/SA1 contradiction (Table 4-1 pin 3 = SSN/SA0, pin 28 = MISO/SA1, ds/f8597506 L615-651, page 20; but §4.3 text "SA1 (pin 3)", "SA0 (pin 28)", L726-746, page 22): docs/03 L435-436 follow the table and the chosen 0x20/0x23 are immune, yet L385 "Do not use 0x21 or 0x22" gives no reason; RM3100 SCL limit "1 MHz or less" (L734); BMM150/BMP390/BME688 are not described as hardware at all (internal to the Nicla).
- **(d)**: none.
- **(e)**: L59/§10 title "Antennas, coax and bench RF" L963-1002 (bench separation table, coax bench set); §7.3 "Bench RF test set" L860-869; L207, L721, L825 ("bench supply", "bench phase").
- **(f)**: L70 "Ground radio power: Its own 5 V supply, or the VENTUNO Q +5V_LIMITED pin" contradicts the default USB-powered E15 (§5.3.0 L554); §5.5 diagram L647-680 shows the RCU option, not the default; §7.2 BOM has no E15-USB-T2 (G4 is a plain USB-UART); L74-75 vs D-13 decision text; T37S cap drawing L291-297 and §5.3.2 L599-609 (see section 5).

### 4.7 docs/04_Space_Data_Link_ICD.md (956 lines, 9585 words)
- **(a)**: L890 heading "9.7 VENTUNO Q: GDS (Linux) <-> station MCU" (section L890-916: `fp_line`, `rcu_mode`, `fp_in`, `rf_*`).
- **(b)**: §3.2 L88-99 (registers, abnormal status), L126, L761.
- **(c)**: **none** - no instrument interface (0 hits for Nicla/ARU/0x2A/RM3100/AS7265X). Missing: ARU I2C page protocol (firmware L13-33), and a 6-DoF page if added.
- **(d)**: none. **(e)**: none.
- **(f)**: L99 "`FF FF FF 01..04` ... under-voltage, over-voltage, over-temperature, other abnormal status" contradicts T37S manual 5.7 (ds/c2d8b245 L548-552: `04` = "Overpressure and overheating") and docs/05 L349, docs/13 L1835, docs/14 L291.

### 4.8 docs/05_Telemetry_and_Command_Dictionary.md (625 lines, 8553 words) - GENERATED
- **(a)**: none. **(b)**: L39, L70, L341-354, L424, L487.
- **(c)**: L37 (`bus_temp` BME688), MAG L115-133 (BMM150 body), **ATT L135-157** (acc, gyro, BMM150, BHI260 quaternion, TRIAD), ENV L233-246 (BMP390, BME688, BSEC), `sensor_health` L283-296, flags L298-327, `SENSORS` L494, EVR 0x0606/0x0607 L591-592. Missing (code-dependent): 6-DoF items (e.g. game rotation vector, linear acceleration, gravity, IMU accuracy) - appear only after code + regeneration.
- **(d)(e)**: none. **(f)**: none (generated).

### 4.9 docs/06_Displays.md (316 lines, 3207 words)
- **(a)**: HW L5, L43-45 (overview rows), L52 (diagram), L64 (wiring "VENTUNO Q JDIGITAL / JANALOG"), L129-130 (CardKB D20/D21 PH12/PH11), L195; TEST L297 (checks); impl. §2 L62-75 (D5/D6/D7/A0), §4 RCU L173-191, §5 L193-229.
- **(b)**: none. **(c)**: CardKB §3 L127-171; L259 (LED matrix row 6 instruments).
- **(d)(e)**: none (L248, L288 "bench view" of the spacecraft matrix/board - neutral).
- **(f)**: title/scope L1-6 tie the station front panel to the VENTUNO MCU.

### 4.10 docs/07_Operations_Procedures.md (891 lines, 9383 words)
- **(a)**: SETUP/APPLAB L35-36, L43 (`http://<VENTUNO Q address>:8080/`), L62 (host mode); procedure L222 (hardline "On the VENTUNO Q"); HW L659 (A0 buzzer).
- **(b)**: L96, L266, **§7.8 L649-670**, L864, L867.
- **(c)**: L102-108, L263 (`SENSORS`), §5 L358-499 (MAGCAL, MAGBIST, spectrometer). Missing: IMU (6-DoF) checks/procedures.
- **(d)**: none.
- **(e)**: §2.6 "Hardline (umbilical) commanding on the bench" L217; `# bench demo` L326 (sequence example); L803 NONE "Bench only". Missing: long-range pass operations (site set-up, antenna pointing, slow-profile operation with long frame periods, LOS timeouts at profile 0/1).
- **(f)**: none beyond duplication of key/host-mode procedures.

### 4.11 docs/08_Verification_and_Test.md (904 lines, 13095 words)
- **(a)**: TEST L31 ("real VENTUNO Q station sketch"), L135, L137 (RadioLib negative control for the VENTUNO Q), L325, L357 (T-HW-01 "+5V rail 4.75-5.25 V"), L377-378 (T-HW-07 +5V_LIMITED), **§7 T-GS L438-476** (L440, L451 T-GS-06 AES on the VENTUNO Q; impl. L450 T-GS-05 "M.2 2230 NVMe ... 64 GB eMMC"), L496 (T-RF-11), L497 (T-RF-12), L607 (SX126x pins A2/A3), L632 (T-TC-05 hardline), L866 (coverage); PERF L173, L177, L187-189.
- **(b)**: L106, L132, L181, L357, L362, L365 (T-HW-09), L482, L494 (T-RF-09), §8.4 L577-598 (T-RF-07), L806 (T-FD-05).
- **(c)**: L360 (T-HW-04), §10 L733-797 (T-SC-01..12, §10.1 BMM150 scale L750-760, §10.2 AS7265X L762-781, §10.3 boresight L783-797), L805 (T-FD-04 unplug Nicla), L868-872, L901. Missing: ESLOV wiring test, per-bus tests, 6-DoF IMU tests (gyro zero-rate/bias, accelerometer per axis exists only as T-SC-02 "magnitude 1.000 +/- 0.02 g", game-RV vs RV comparison).
- **(d)**: none.
- **(e)**: status L8-10; §5 equipment L312-335 (attenuators/dummy loads); §8 bench set-up L480-497 (T-RF-01 "Bench link", T-RF-05 "at bench level"); **§12 Field L838-846 only 3 tests** (T-FLD-01 range, -02 RSSI vs distance, -03 power).
- **(f)**: L30 "70 ok" vs L127 "Last run: 64 ok"; ground-test totals L110 (739) + L112 (7) + L116 (4) = 750 vs L29/L119 "752"; §2.6 build table stale vs docs/01 §9.1.

### 4.12 docs/09_Regulatory_and_Safety.md (244 lines, 2842 words)
- **(a)**: SETUP L108 (key path on the VENTUNO Q); HW/PWR L174 (VENTUNO pins row: A1, +5V_LIMITED, +3V3_LIMITED).
- **(b)**: L3, L21, L35-37 (EIRP with Yagi), L124, L132-134, L151-153, L171, L190, L221-222.
- **(c)**: L193 (bus temperature), L202 (AS7265X LEDs 875 nm / 405 nm), L233.
- **(d)**: none. **(e)**: L29 (licence-exempt "short-range device" - regulatory term, keep); L137-139 (separation, "on the bench") - keep as safety.
- **(f)**: none. Missing for ULR: higher-gain ground antennas and masts (wind, lightning) are not covered.

### 4.13 docs/10_References.md (167 lines, 2447 words)
- **(a)**: CITE L72 (ABX00181 datasheet), L82 (variant overlay `arduino_ventuno_q_stm32h5f5xx`), L86 (VENTUNO user manual), L87 (LINPACK packages).
- **(b)**: L58-59 (T37S manual v1.5; TBH-02 manual v1.0 2024-03-06).
- **(c)**: L62-69 (AS7265x v1-04, SparkFun guide, RM3100 Doc 1017252 r06, Nicla ABX00050 modified 08/10/2026, BHI260AP DS000-02 rev 1.1, BMM150 DS001-05 rev 1.4, BMP390 DS002-07 rev 1.7, BME688 DS000-03 rev 1.3 - revisions match the supplied PDFs), L73-74 (CardKB), L103 (Arduino_BHY2 1.0.8). Missing: the E51-TTL-500 datasheet (the docs' source for the E15-USB-T2, cited in docs/03 L547, docs/00 L630, docs/12 L914); the Nicla ESLOV pinout document; the UNO Q 4 GB SKU ABX00173 (only in L71 alongside ABX00162); the specific USB-C hub's datasheet (not available: open question); Arduino_BHY2Host (ESLOV protocol, if referenced).
- **(d) Voyager**: L158 "Voyager Computer Command Subsystem time-tagged sequences - stored command sequences." A concept reference (no author/title/publisher), not a document title.
- **(e)(f)**: none.

### 4.14 docs/11_Standards_Status_and_Roadmap.md (130 lines, 1911 words)
- **(a)**: L69 ("a VENTUNO Q platform entry" RadioLib hunk). **(b)**: L60-67. **(c)(d)**: none.
- **(e)**: L49 (Proximity-1 "short-range relay" - standards text, keep); L111 roadmap "antenna rotator tracking ... long-range tests" (ULR candidate).
- **(f)**: none.

### 4.15 docs/12_Design_Decisions.md (1221 lines, 23450 words)
- **(a) DEC**: L58 (index D-15), D-01 L128/L131 (ground = VENTUNO, evidence ABX00181), D-05 L172, **D-15 L285-292**, D-16 L301 impl. (station MCU OLED alternative), D-17 L311 (ground T33D from +5V_LIMITED), D-35 L523 (AES speed on the VENTUNO Q), D-40 L584-587 (RadioLib VENTUNO platform entry), D-53 L737 impl. ("The eMMC is 64 GB"), D-55 L757 (host mode, "Ubuntu 24.04"), D-68 L911 ("in a VENTUNO Q USB-A port").
- **(b)**: index L52, L55; D-08 L211-212; **D-09 L218-226**; **D-12 L251-259**; superseded L945; Appendix A C21 L998; Appendix B L1079.
- **(c)**: **D-13 L262-272** (one bus), **D-14 L274-281**, D-15 (CardKB), D-44..D-48 (L628-681), **D-52 L716-726** (sensor facts from datasheets), D-67 L896-905 (empty A4/A5 bus), superseded L946, C22/C28/C47 (L999, L1005, L1024), Appendix B L1138, L1177, L1190.
- **(d) Voyager**: L92 index and **L683 heading "D-49 Stored command sequences (Voyager CCS style), RAM only"** - analogy, not a citation; anchor `<a id="d-49">` is explicit (safe to rename the heading).
- **(e)**: D-28 L438 "NONE on the bench" (title), L141, L617, L935 (bench) - context only.
- **(f)**: D-13 decision text L266 "No Qwiic connector or cable for any sensor" vs its own revision L271 (AS7265X on Qwiic) and docs/03 L74-75; D-15 L289 "Neither board's Qwiic socket is used" (stale).

### 4.16 docs/13_Web_UI_Reference.md (3126 lines, 42026 words)
- **(a)**: L4 (intro: "serves on the Arduino VENTUNO Q"), L1214 (§4.13 "the VENTUNO Q MCU (station sketch ...)"), L2864 (§13.8 "The VENTUNO Q MCU (the App's sketch ...)"). The dashboard text itself says "VENTUNO Q MCU" (`dashboard.html` L342), which §13.8 mirrors.
- **(b)**: L223, L228, L547, L618, L747-749, L908, L929, L976, L990, L998, L1005, L1021, L1143, L1774, L1835-1836, L1899, L1901, L1946, L1953, L1959, L2929.
- **(c)**: L492 (bus temperature tile), §7.6 Sensors L1970-1991, §9 SCIENCE L2434-2508 (9.1 magnetometers, **9.2 attitude L2478-2493**: "Acceleration, rate, BMM150 field" L2489 is the only 6-DoF content, 9.3 environment), §10 MAGCAL, §8 SPECTROMETER, §13.8 L2862-2878. Missing: a 6-DoF IMU panel and its provenance labels; ESLOV/bus health.
- **(d)**: none.
- **(e)**: L324 (D-70 "after the first bench link"), L1834 ("Bench: room temperature") - context; §17 L3018 "Reading the RECEIVER tab during a range test" supports ULR (keep, expand).
- **(f)**: none found beyond the board naming.

### 4.17 docs/14_Troubleshooting_and_Event_Response.md (737 lines, 15932 words)
- **(a)**: APPLAB L80 ("after App Lab was switched to the other board ... On the VENTUNO Q: curl ..."), L197 (zip name), L202 (OpenOCD `linuxgpiod` messages when flashing the VENTUNO Q). impl. L86-94 (station panel, RCU on D2/D3/D4).
- **(b)**: L105, L146, L288-292 (EVR 0x0311-0x0315), L319, L413, L684, L721, L728, L734.
- **(c)**: §2.6 L149-160 (L153 "Check A4/A5 wiring"), L321-326 (EVR 0x0606/0x0607/0x060B), L320, L335-339 (AS7265X EVRs).
- **(d)**: none. **(e)**: L109 (overdriven receiver: "More distance (>= 10 m)") - keep as bench symptom.
- **(f)**: L202 row is VENTUNO-flash specific (UNO Q flashing output differs: VERIFY on the ground UNO Q).

### 4.18 docs/15_Configuration_Reference.md (474 lines, 7980 words)
- **(a)**: L428 heading "5 Station sketch build settings (VENTUNO Q MCU)" and table L433-445 (pins D2/D3/D4, D5/D6/D7, A0, D8/D9/D10, A1/A2/A3).
- **(b)**: L32, L69, L112, L114, L121, L146, L220, L268, L277, L278.
- **(c)**: **§3.5 L164-182** (`SENSOR_WIRE` Wire2 only, `QWIIC_WIRE` AS7265X only, bus clear pins, RM3100 addresses, `kRm3100ToBody`, `kAruMagUtPerLsb`), §3.6 L184-194 (AS7265X). Missing: per-instrument bus keys after the split; ESLOV INT pin if used; 6-DoF rates/ranges if configurable.
- **(d)(e)**: none. **(f)**: §4.14 L397 "external NVMe / USB drive" (VENTUNO-era).

### 4.19 docs/16_Developer_Guide.md (584 lines, 4958 words)
- **(a)**: L76 (repo map "VENTUNO Q App"), L443 (FQBN `arduino:zephyr:ventunoq`), L522 (packaging table). impl. L92, L104.
- **(b)**: none. **(c)**: L68 (drivers), **§4.5 "Add a sensor" L251-272** (assumes the single `Wire2` bus and "no Qwiic (only the AS7265X ...)").
- **(d)(e)**: L508 (bench-only debug) context. **(f)**: none.

### 4.20 docs/17_Security_Model.md (400 lines, 7290 words)
- **(a)**: L59 (trust-boundary diagram "Ground - Arduino VENTUNO Q"), L197 (`scp ... arduino@<ventuno-q>:...`).
- **(b)(c)(d)**: none (L41, L61 CardKB as an asset/actor). **(e)**: L124, L126 "bench work" context.
- **(f)**: Missing for the new hardware: the USB-C hub as a physical attack surface (any USB device on the ground UNO Q), PD power path. §7.6 and IDs A3/T16 must stay (code cites them).

### 4.21 docs/18_Data_Products.md (647 lines, 5668 words)
- **(a)**: L44 (archive path "on the VENTUNO Q"), L48 ("The VENTUNO Q has 64 GB of eMMC (Arduino ABX00181 datasheet)"), L574; impl. L46 ("external M.2 2230 NVMe (non-bootable) or a USB 3.0 drive").
- **(b)(c)(d)(e)**: none.
- **(f)**: §7.3 L298-310 disk budget sized against 64 GB; the UNO Q ABX00173 has 32 GB eMMC (ds/4c60b196 L438) - re-derive (archive cap 16 GB + floor 2 GB on 32 GB minus the OS: VERIFY on the board).

### 4.22 docs/19_Glossary.md (320 lines, 4667 words)
- **(a)**: L19 (ABX00162 / ABX00181), L27 (App Lab "UNO Q / VENTUNO Q"), L86 (DSS-Q1 "on the VENTUNO Q"), L117 (FQBN), L139 (Host mode), L181 (MCU/MPU STM32H5F5, QCS8275).
- **(b)**: L92. **(c)**: L28 (ARU 0x2A), L33, L41 (Bosch parts), L46 (BSEC), L97 (ENU/NED), L147 (IAQ), L271 (TRIAD). Missing: ESLOV, 6-DoF, game rotation vector, linear acceleration, PD (USB Power Delivery), DP Alt Mode, USB-C hub/dongle, ABX00173.
- **(d)(e)(f)**: none.

### 4.23 docs/20_Known_Limitations.md (261 lines, 8034 words)
- **(a) LIMIT**: L36 (V-04 SX126x pins A2/A3), L68 (V-27 +5V_LIMITED), **§1.3 L73-87** (heading; V-30..V-38; V-35 L82 QCS8275; V-38 L85 CardKB pins; V-69 L87 E15 on a VENTUNO USB-A port; impl. V-34 L81 M.2/64 GB), L122 (V-60 station sketch stays on the VENTUNO MCU), L126 (V-64 pull-ups on the VENTUNO header I2C).
- **(b)**: L42-43 (V-10, V-11), L124 (V-62), L174 (L-22), L188 (L-32), L243 (R-04).
- **(c)**: V-40..V-49 L93-102, V-62 L124, V-63 L125, V-64 L126, S-05 L231, S-06 L232, P-02 L142, L-37 L193.
- **(d) Voyager**: **L195 (L-39)** "Voyager's Command Loss assumes an onboard fault and works through the receive chain (J. Taylor (ed.), *Deep Space Communications*, JPL 2014, 3.7.3.2)" - "Voyager" names the spacecraft; the cited document title does not contain it.
- **(e)**: L9-10 status ("on the bench"); L52 (V-70 "both antennas on one bench").
- **(f)**: V-61 L123 points to "02 section 12" (the analysis is in 02 §6.4; §12 only lists the item) - minor.

### 4.24 docs/21_Test_Records.md (668 lines, 7469 words)
- **(a)**: TEST L143-145 (PWR-2 "VENTUNO Q 12 V input" rows), CITE L150 (ABX00181 3.4.1), L345-346 (TMP ratings); HIST L581 (record 2026-10-12-A "no ground station"), **L635, L638, L659** (completed record 2026-10-13-A: zip, E15 in a VENTUNO USB-A port, hardline from the VENTUNO Q).
- **(b)**: L72, L85, L103, L118, L134, L173, L355, L391, L402.
- **(c)**: MAG L255-339 (MAG-4 L321-339 BMM150 scale), TMP L341-382 (BME688 etc.), PWR-1 L117 (Nicla VIN J2 pin 9), L591, L606.
- **(d)**: none.
- **(e)**: RSSI-1 "Bench calibration" L166; RNG template L213-253 exists (one site pair) - expand for long-range campaigns; L48, L87-88, L162-163, L519.
- **(f)**: completed records are history: do not rewrite their facts (open question Q2).

### 4.25 docs/22_Bundled_Libraries.md (491 lines, 5715 words)
- **(a)**: **§3.2 L78-90** (RadioLib hunk 1 "a platform entry for the VENTUNO Q", defect/evidence/fix rows), L165 (station Bridge methods "Station (VENTUNO Q)"), L323 (`fqbn` comment `arduino:zephyr:ventunoq`), L354-355 (check_firmware units and negative control "for the VENTUNO Q"), L412 (zip name), L462 (upgrade step "fixes the VENTUNO Q entry").
- **(b)(c)(d)(e)**: none (L6 "long-range SPI radio option" good).
- **(f)**: if the station sketch moves to the UNO Q, hunk 1 is no longer needed by any build in this project (RadioLib 7.8.1 already has the UNO Q branch, docs/22 L88): the patch, `check_firmware.sh` negative control, CI comment (ci.yml L126), docs/08 L137, docs/11 L69, docs/12 D-40, FAQ L320/L350 change together (decision needed: Q5).

### 4.26 docs/FAQ.md (366 lines, 4048 words)
- **(a)**: L22, L176-177 (processing table: STM32H5F5, QCS8275), L232 ("runs on the VENTUNO Q's Linux processor"), L245 (hardline "on the VENTUNO Q"), L293 (QCS8275, 16 GB, 64 GB), L298 (archive path), L320, L350 (RadioLib VENTUNO entry).
- **(b)**: L8 (index), **L97-107**, L219, L272.
- **(c)**: L21, **L258-266 "Why do four sensors on one I2C bus not clash?"** (contradicts the new separate-bus requirement), L268-276 (heat test), L278-286 (AS7265X).
- **(d)**: none.
- **(e)**: L48-65 range table (starts with "Both antennas 2 m above flat ground"); L255 "bench tests".
- **(f)**: L52-60 uses a 12 dBi Yagi (67 dB margin at 11.7 km) while docs/02 §6.3 L492 uses dipoles (57.3 dB) for the same "2 m / 2 m" case: two numbers for one scenario.

### 4.27 docs/Project_Summary.md (72 lines, 764 words)
- **(a)**: L11 (board row: VENTUNO Q, STM32H5F5 + QCS8275). **(b)**: L14. **(c)**: L15. **(d)**: none.
- **(e)**: L60-62 status "First bench link". **(f)**: none.

### 4.28 vgq1-spacecraft/README.md (173 lines, 2612 words)
- **(a)**: L3 (other half "on an Arduino VENTUNO Q"), L26 (A0 buzzer warning), L46 (`scp ... <ventuno-q-address>`), L96 (decode CADU "on the VENTUNO Q"), L99 (hardline line "on the VENTUNO Q"), L116 (A0 buzzer).
- **(b)**: L26, L116. **(c)**: L154 (AS7265X). Missing: Nicla ESLOV wiring, bus split.
- **(d)**: none. **(e)**: L3, L8 ("bench EGSE", "bench rack" - EGSE terminology, keep), L53.
- **(f)**: 17 absolute `blob/main` links (Q7).

### 4.29 dssq-groundstation/README.md (223 lines, 3493 words)
- **(a)**: L3 (title paragraph), L8 (STM32H5F5 station sketch), L21 (FQBN `ventunoq`), L22 ("stock release does not compile for the VENTUNO Q"), L28 (A0 buzzer), L36-37 (zip, App Lab), L47 (scp), L62, L81, L92 (URLs), L171 (host mode), L205 (`sketch.yaml` profile); impl. L168 ("Ubuntu 24.04").
- **(b)**: L28. **(c)**: L8, L21, L202 (CardKB).
- **(d)**: none. **(e)**: L55 ("bench tests").
- **(f)**: **L28 "The default station radio is an EBYTE E22-400T33D on a USB-UART adapter ... with its M0/M1/AUX pins on the station sketch's Radio Control Unit"** contradicts D-68 (E15-USB-T2, `rcu = false`); 17 absolute `blob/main` links.

---

## 5 E22-400T37S / E22-400TBH-02: every passage, and why the current text confuses

### 5.1 Where it is (to be consolidated into ONE chapter, all others become pointers)

| Document | Passage | Content |
|---|---|---|
| docs/03 | §1 L68, L70 | option row; "E22-400T37S: its own 12 V supply" |
| docs/03 | §3 L121 | logic level 3.3 V ("level conversion circuit is required", T37S manual 2.2) |
| docs/03 | §4.2.3 L247-261 | TBH-02 supply 4.5-15 V, DC jack vs terminal, 12 V/5 V currents, "never from a board header" (cites VENTUNO VIN PTC and +5V_LIMITED) |
| docs/03 | §4.4 L286-313 | TBH-02 header drawing, caps, spacecraft wiring table |
| docs/03 | §4.5 L315-343 | A0 strap (selection logic, checks, licence) |
| docs/03 | §5.3.2 L593-609 | ground: CH340X USB into a VENTUNO USB-A port, caps, RCU on VENTUNO D2/D3/D4, `fault_log = true`, configuration with only the M1 cap removed |
| docs/03 | §6.1 L694-695, §6.3 L731-744, §6.5 L776-777, §6.7 L799, L804 | loads, spacecraft budget with T37S, ground note, fuse, polarity |
| docs/03 | §7 L825, L827, L864, L867, L875 | BOM: battery >= 5 A, 2 A fuse, attenuators/dummy load >= 10 W, option row |
| docs/03 | §8 L887-888, L900, L909-911, L916-917 | thermal: 120 degC cut-off, heat 8.2 W, FDIR response, heat-sink advice |
| docs/03 | §10 L967-969, L983, L989-1001; §11 L1019 | antenna/no-load rule, connectors, blocking, bench table, VERIFY row |
| docs/00 | L28, Part 13 L848-897 | switching procedure: 13.1 spacecraft, 13.2 ground (RCU on VENTUNO), "Going back" |
| docs/02 | L4, L81, §2.3 L119-141, L149, L164-172, L268, L329, §5.3 L414-419, §5.6 L436-448, §6.2-6.5 L475-522, L810, L830 | RF facts, budgets, range, "+2 dB net" |
| docs/07 | L96, L266, §7.8 L649-670, L864, L867 | operations |
| docs/01 | L52, L159, L215, L351, L577, L905 | architecture, FDIR row |
| docs/04 | §3.2 L88-99, L126, L761 | register protocol, abnormal-status reports |
| docs/05 | L39, L70, L341-354, L424, L487 | generated fields |
| docs/08 | L106, L132, L181, L357, L362, L365, L482, L494, L577-598, L806 | tests T-HW-01/06/09, T-RF-07/09, T-FD-05 |
| docs/09 | L3, L21, L35-37, L124, L132-134, L151-153, L171, L190, L221-222 | licence, separation, exposure, supply, thermal, software safety |
| docs/10 | L58-59 | manuals |
| docs/11 | L60-67 | driver status |
| docs/12 | L52, L55, D-08 L211-212, D-09 L218-226, D-12 L251-259, L945, L998, L1079 | decisions |
| docs/13 | 24 lines (L223 ... L2929, section 4.4 of this report) | badges, budget, radio configuration, faults, limits |
| docs/14 | L105, L146, L288-292, L319, L413, L684, L721, L728, L734 | fault tree, EVRs, ground events, alarms, FDIR |
| docs/15 | L32, L69, L112, L114, L121, L146, L220, L268, L277, L278 | config |
| docs/19 | L92 | glossary |
| docs/20 | L42-43, L124, L174, L188, L243 | V-10, V-11, V-62, L-22, L-32, R-04 |
| docs/21 | L72, L85, L103, L118, L134, L173, L355, L391, L402 | PWR, TMP, TXP templates |
| FAQ | L8, L97-107, L219, L272 | how to switch |
| README / summaries | README L18-19, L57, L176-179; Project_Summary L14; vgq1 README L26, L116; dssq README L28 | overview |

### 5.2 What confuses (concrete)

1. **No single home**: the spacecraft wiring is in docs/03 §4.4, the strap in §4.5, the supply in §4.2.3
   and §6.3, the ground in §5.3.2, the procedure in docs/00 Part 13 and again in docs/07 §7.8 and
   FAQ L97-107, each slightly different (e.g. docs/07 L653-654 gives "about 1.1 A at 12 V", docs/00
   L853 "a 12 V supply of at least 1.5 A per module with a 2 A fuse").
2. **Cap drawing** (docs/03 L291-297): `[cap 7-8]` and `[cap 9-10]` under the bottom row means the M1
   cap bridges header 7 (M1) - 8 (GND) and the M0 cap bridges 9 (GND) - 10 (M0) (D-12 L255 says so
   in words: "they tie M1-GND and GND-M0"); the drawing alone does not say it, and docs/00 L859
   writes "M0, M1 (bottom header, pins 7-8 and 9-10)" in the opposite order. The RXD/TXD caps are
   only "near USB-C". The physical orientation of the rows is not verifiable from the TBH-02 text
   (figures only) - VERIFY against the board silkscreen.
3. **Two ground recipes**: docs/03 §5.3.2 L600 ("remove them when the RCU is wired; otherwise keep
   both fitted (mode 0) and set `[gds] rcu = false`") vs docs/00 §13.2 L882-888 (always wire the RCU
   after configuration). With D-68 (`rcu = false` default) and a UNO Q ground, both must be redone.
4. **Supply range**: docs use 4.5-15 V (T37S manual L137, L297; TBH-02 manual L90-93 and note 2
   L266-267) but the TBH-02 manual's own component table says "5-12V power supply DC connector" and
   "5-12V ... terminal" (ds/6e826cce L185-190), and the T37S manual feature list has the typo
   "4.5 ~ 1.5 V" (ds/c2d8b245 L181). Currents are given only at 5 V and 12 V; the docs flag below-12 V
   current as VERIFY (docs/03 L739, L1019) - keep, explain once.
5. **Attenuator/dummy-load ratings**: docs/00 L95, L105 ">= 5 W", docs/00 L854/L858 and docs/03
   L864/L867/L1001 ">= 10 W for the T37S", docs/09 L138 "first attenuator rated >= 10 W".
6. **Abnormal-status code 04**: docs/04 L99 "other abnormal status" vs T37S manual 5.7 (L548-552)
   "Overpressure and overheating" = docs/05 L349, docs/13 L1835, docs/14 L291; docs/02 L135 lists
   `04` unlabeled.
7. **Undocumented hardware outputs**: TBH-02 header 19 STATE ("Module status indication output",
   ds/6e826cce L261-263) and the T37S STATE indicator patterns per fault (T37S manual 5.7 item 1,
   ds/c2d8b245 L529-541: under-voltage 500 ms, over-voltage 1 s, over-temperature 2 s, both = steady)
   are never mentioned; AUX indicator LED (TBH-02 2.1 item 7, L195-196) neither.
8. **Vendor contradiction not flagged**: TBH-02 manual §4 step 2 "Unplug the jumper caps at M0 and M1"
   for parameter configuration (L314-318) contradicts its §3 (both caps removed = deep hibernation,
   mode 3, L298) and refers to "the 7PIN holder of the adapter board" (L319-323), i.e. text copied
   from the USB-adapter instructions. docs/03 L608-609 correctly uses "only the M1 cap removed" (§3)
   but does not warn about §4.
9. **USB power question (not found)**: the TBH-02 manual leaves the "Power supply interface" row empty
   (L118-128); whether the board's USB-C VBUS can feed the module is not stated. At 5 V the T37S
   draws 2.9-3.3 A when transmitting (T37S manual 2.2; docs/03 L256). Through a hub on the ground
   UNO Q this must be ruled out on the board (VERIFY) before the TBH-02 USB-C is plugged in.
10. **Dangling reference**: docs/02 L122 "(extract scratchpad `t37s.txt`)".
11. **Mixed pairs not analysed**: only "T37S at both ends" budgets exist (docs/02 §5.3, §6.3); the
    common ULR case of one T37S (e.g. ground uplink power) and one T33D is not computed per direction.
12. **VENTUNO-specific supply argument**: docs/03 L259-261 justifies "never from a board header" with
    VENTUNO VIN PTC 1.1 A and +5V_LIMITED 2.8 A; for two UNO Qs the argument must be restated with
    ABX00162/ABX00173 facts (JANALOG pin 5 = USB VBUS pass-through; USB-C PD 5 V/3 A only,
    ds/4c60b196 L527, L675-685).

---

## 6 Sensor interfacing: coverage and gaps per sensor

| Sensor | Where documented (main passages) | What it says | Missing (datasheet-based, for the new chapters) |
|---|---|---|---|
| Nicla Sense ME (board) | docs/03 L125, L382, **L389-397**, L211, L696, L889; docs/00 Part 4 L194-216, L579; docs/12 D-14 L278; docs/10 L65; docs/19 L28 | I2C target 0x2A on J2 pins 1/2, VIN 3.5-5.5 V from the buck, VDDIO_EXT left open, ARU firmware flashing, 0.1 A budget estimate | **ESLOV J5** (ds/414d9b73 L628-650: 1 5V, 2 INT, 3 SCL, 4 SDA, 5 GND; connector SM05B-SRSS-TB L400); power through ESLOV (L435, Schottky-protected); ESLOV as a firmware-update path (L455-456); the ARU firmware's `Wire` is the bus the Arduino ESLOV handler also uses (ArduinoCore-mbed `variants/NICLA/pins_arduino.h` L62-63, `PIN_ESLOV_INT` p19 L82; Arduino_BHY2 `src/EslovHandler.cpp` L23) - whether ESLOV SDA/SCL pass the VDDIO_EXT translator: **not found** in the datasheet text; the ARU page protocol (firmware L13-33) is in no document |
| BHI260AP (6-DoF IMU + fusion) | docs/05 ATT L135-157; docs/13 §9.2 L2478-2493, §7.6 L1978-1981; docs/08 §10.1 L750-760, L786-787; docs/10 L66; docs/12 D-52; docs/20 V-40, V-42, P-02 | fusion quaternion (rotation vector) + accuracy, accel (g), gyro (deg/s), BMM150 via the BHI260; ENU convention VERIFY; scaling from Table 88 | 6-axis IMU facts (ds/3f577a50 L59, L86, L530); virtual sensors: Game Rotation Vector "6DoF, Accelerometer and Gyroscope" L3430, Rotation Vector 9DoF L3442, Geomagnetic RV L3454; ODR up to 800 Hz L3515; dynamic range only for single physical sensors L6742; FIFO event table (Table 88, index L486); accel/gyro ranges (today only printed on the Nicla serial port, docs/00 L209); axis frame and mounting |
| BMM150 | docs/05 MAG L129-132, ATT L146-148; docs/13 L2463, L2488; docs/08 §10.1; docs/21 MAG-4 L321-339; docs/15 L181 | body field 0.1 uT from the BHI260 range; fallback 1/16 uT | hardware path: dedicated I2C to the BHI260 (ds/414d9b73 L419); range +/-1300 uT (x, y) / +/-2500 uT (z), 0.3 uT resolution (firmware L50-51, BMM150 DS001-05) - only in the firmware header |
| BMP390 | docs/05 ENV L238; docs/13 L2499; docs/10 L68 | pressure hPa | path: SPI to the BHI260, same bus as the BME688 (ds/414d9b73 L416); range 300-1250 hPa, relative accuracy +/-0.03 hPa (L416); 1/128 Pa raw (firmware L41-44) |
| BME688 + BSEC | docs/05 L37, ENV L239-246; docs/13 L492, L2500-2507; docs/03 L912-918; docs/20 S-05 | temperature (bus thermometer), RH (whole %), gas, IAQ/CO2eq/bVOC, accuracy | path: SPI to the BHI260 (ds/414d9b73 L416); gas scan cycle 10.8 s (L413); BSEC warm-up/accuracy meaning in one place |
| RM3100 | docs/03 **L423-437**, L380-385, L699, §9 L923-959; docs/00 §8.5 L577-578, §8.6; docs/15 L175-179; docs/12 D-52 L720; docs/20 V-41, V-48 | MagI2C pins, straps 0x20/0x23, I2CEN high, DRDY polled, boom rules | separate bus (new); manual's SA0/SA1 contradiction (ds/f8597506 L615-651 vs L726-746) and why 0x20/0x23 were chosen; SCL "1 MHz or less" (L734); RES pins 2/20 "connect to ground" and REXT pin 25 (L618, L638, L644); breakout pin names VERIFY |
| AS7265X | docs/03 **L399-421**, L123, L697-698, L890; docs/09 L202; docs/12 D-44/D-45; docs/15 §3.6; docs/08 §10.2 | 3.3 V, A4/A5 or Qwiic, I2C jumper closed, JP2 open, INT unconnected (INT/RST swapped), lamp currents | bus allocation after the split; LED set (5700 K white, 405 nm UV, 875 nm IR: hookup guide ds/f7bee49d L83) is only in docs/09; the virtual-register protocol is cited (docs/10 L62) but not described in an ICD |
| CardKB (ground) | docs/03 L126, §5.4 L627-645; docs/06 §3; docs/00 §9.5; docs/12 D-15; docs/20 V-37, V-38, V-64 | 0x5F, Grove colours, 3.3 V option A / BSS138 option B, VENTUNO header I2C | ground UNO Q bus and pins (the UNO Q header "SDA/SCL" = `Wire` I2C2 PB10/PB11 is free on the ground because the radio is on USB; on the spacecraft the same pins are `Serial3`, docs/03 L140-144); CardKB facts (0x5F, 5 V; ds/b41d1005 L31, L106, L121) |

Instrument-bus facts the split must respect (from the docs): the UNO Q variant exposes `Wire`
(I2C2, PB10/PB11 = header pins printed SDA/SCL), `Wire1` (I2C4, Qwiic PD12/PD13) and `Wire2`
(I2C3, A4/A5) (docs/12 D-13 L268; docs/03 L133-144, L175-176); on the spacecraft PB10/PB11 carry
`Serial3` for the E22 and `Wire` is never started (docs/03 L140-144).

---

## 7 Proposed section structure per document (g)

General rules for every document on this branch:
- Keep all file names (00-22, FAQ, Project_Summary, READMEs, CHANGELOG). Keep docs/06's file name even if its scope changes.
- Same front matter everywhere: purpose and audience, scope, sources table (short names), conventions, related documents; end with "Open items (VERIFY) owned by this document".
- One home per topic: hardware facts and wiring in docs/03; step-by-step in docs/00; operations in docs/07; settings in docs/15; protocol octets in docs/04; decisions in docs/12; dashboard in docs/13; tests in docs/08; records in docs/21; limitations in docs/20. Other documents link, never restate tables.
- ULR first: long-range configuration and figures lead; the bench is "pre-deployment checkout".
- Terminology: "ground UNO Q" (ABX00173, 4 GB) and "spacecraft UNO Q" (ABX00162 2 GB or ABX00173); placeholders `<SPACECRAFT-IP>` / `<GROUND-IP>`; "Interstellar probe" where "Voyager" stood.
- Do not renumber docs/17 §7.6, docs/09 §1.3, test IDs, D-NN, docs/12 Appendix B; renumbering elsewhere requires the reference sweep of section 3.

### docs/03 Hardware Integration (central rewrite)
1. Configurations at a glance (both ends UNO Q; default/option table; ULR station kit)
2. System overview (diagram: spacecraft UNO Q; ground UNO Q + USB-C hub/dongle with PD + E15-USB-T2/E22 + power; options TBH-02/T37S, SX126x)
3. Logic levels, buses and pin budget (both boards: every MCU bus `Wire`/`Wire1`/`Wire2`/`Serial1`/`Serial3`/`SPI`, what each end uses; 5 V-tolerance rules ABX00162 9.6/9.7)
4. Spacecraft (UNO Q) wiring: 4.1 pin map, 4.2 power (A, B; Nicla supply from ESLOV pin 1 or J2 VIN), 4.3 E22-400T33D, 4.4 radio-model strap, 4.5 SX126x option, **4.6 instrument buses (separate pins per instrument, pull-ups per bus, bus clear, addresses)**, 4.7 wiring diagram
5. **Instrument interfaces (one chapter per sensor, datasheet-based)**: 5.1 Nicla Sense ME as ARU (board, internal topology, power, levels, J2 vs ESLOV, ESLOV pinout and loose-wire table with the 4 used wires, INT, flashing, checks); 5.2 BHI260AP 6-DoF IMU and fusion (virtual sensors used, ranges, ODR, frames, accuracy); 5.3 BMM150; 5.4 BMP390; 5.5 BME688 + BSEC; 5.6 RM3100 (pins, straps, manual contradiction, DRDY, cycle counts, self-test, boom cable); 5.7 AS7265X (SEN-15050 jumpers, INT/RST swap, LEDs, Qwiic, current); 5.8 CardKB (ground)
6. **Ground station (UNO Q ABX00173) wiring**: 6.1 board (4 GB / 32 GB, ds/4c60b196 L438; SBC-mode note L2519), **6.2 USB-C hub / dongle** (requirements: PD pass-through, data ports, port power for the E15/E22 and the TBH-02 CH340X, USB 3.1 and DP Alt-Mode sharing L2460-2464, host VBUS from Q2801 L888; what plugs where), **6.3 power** (PD 5 V / 3 A only L527, L685; VIN 7-24 V alternative; budget), 6.4 E22-400T33D on the E15-USB-T2 (default), 6.5 RCU option (ground UNO Q MCU pins), 6.6 SX126x option, 6.7 station I/O (lamps, buzzer, CardKB on the ground UNO Q; no A0 strap rule restated), 6.8 wiring diagram
7. **E22-400T37S on the E22-400TBH-02 (consolidated, rewritten)**: 7.1 what it is and when to use it (ULR: +4 dB EIRP, -2 dB sensitivity; mixed-pair budgets); 7.2 board anatomy (pins 1-20 from the TBH-02 manual 2.2, cap positions, CH340X, DC jack vs terminal, SMA, AUX LED, STATE pin); 7.3 supply and current (4.5-15 V; 12 V recommended; vendor 5-12 V inconsistency; fuse; USB-power question); 7.4 modes and jumpers vs MCU control; 7.5 spacecraft UNO Q wiring + A0 strap; 7.6 ground UNO Q wiring (CH340X USB through the hub, optional RCU); 7.7 one-time configuration (M1 cap only; vendor §4 warning); 7.8 fault reporting (FF FF FF 01..04, STATE patterns, EVRs, FDIR); 7.9 thermal and RF safety (attenuators >= 10 W, separation); 7.10 checklist
8. Power budget and PSU sizing (spacecraft A/B/T37S; ground UNO Q + hub + E15 + optional T37S)
9. Bill of materials (spacecraft; ground incl. UNO Q ABX00173, hub, PD supply, E15-USB-T2; long-range antenna system; checkout RF set; options)
10. Thermal
11. Magnetic cleanliness
12. Antennas, feeders and RF for long range (ground Yagi, height, polarisation, feeder loss at 433 MHz, connectors, separation) + 12.x bench checkout RF (attenuator chain)
13. Hardware items to verify

### docs/00 Beginner Setup Guide (the handbook)
Parts 1-3 unchanged in role (parts list: two UNO Qs, hub, PD supply, E15-USB-T2, Yagi); Part 4 flash the Nicla (+ ESLOV cable preparation); Part 5 set up both UNO Qs (distinct names, `<SPACECRAFT-IP>`/`<GROUND-IP>`, ground UNO Q on the hub); Part 6 keys; Part 7 settings; Part 8 wire the spacecraft (8.5 Nicla via ESLOV loose wires; 8.6 magnetometer bus; 8.7 spectrometer; 8.8 boom); Part 9 wire the ground station (9.1 hub and power; 9.2 E15-USB-T2 radio; 9.3 RCU option; 9.4 lamps/buzzer; 9.5 CardKB); Part 10 first power-up; Part 11 "Pre-deployment checkout on the bench" (renamed); Part 12 first commands; Part 13 the 5 W T37S (steps only, facts in docs/03 §7); **Part 14 "Long-range deployment"** (expanded: planning with `link_budget.py`, site and height, Yagi and feeder, slow rates, first long-range contact, range campaign records); Part 15 host mode; Part 16 updating; Part 17 pointers. The contents table anchors (L15-32) must follow any renamed part.

### docs/01 System Design
§1.1/§1.2 elements table (ground = UNO Q 4 GB; "Interstellar probe" analogy L43); §2 ConOps for a long-range pass; §3.1 both boards (two UNO Qs); §3.4 GDS on the ground UNO Q; §3.5 ground station MCU; **new §3.x ground USB topology (hub, radio, storage)**; §3.7 radio options; §4 data flow incl. the 6-DoF IMU path; §7.3 "Interstellar-probe-style" sequence table (L633); §9.1 builds (station on `unoq` limits); §9.7 ground side (QRB2210, 4 GB, 32 GB eMMC; AES speed VERIFY); §10 open items.

### docs/02 Link Budget and RF Analysis
§1 what limits range (ULR framing); §2 radios (2.3 T37S rewritten, no scratchpad reference); §5 worked budgets re-ordered: Budget A = long-range reference configuration (e.g. hill-to-hill with the station Yagi), bench/10 km cases as checkout examples; **new §5.x mixed T33D/T37S pairs per direction**; §6 range scenarios starting with the long ones; §10.1 rate table ("shorter range", "bench checkout"); new "Long-range station design" (antenna gain, height, feeder loss, noise environment); §12 open items.

### docs/04 ICD
Keep §1-§8 numbering; §9.7 "Ground UNO Q: GDS (Linux) <-> station MCU"; **new §9.8 (or §11) "Instrument interfaces on the spacecraft buses"**: ARU I2C pages 0/1 (+ any 6-DoF page), CRC-8, sequence, timing; RM3100 and AS7265X register protocols by reference; §3.2 fix `04` meaning.

### docs/05 Dictionary
Regenerate only (`python3 tools/gen_dictionary_doc.py`).

### docs/06 Displays
§1 overview (ground UNO Q MCU front panel; decide whether the ground UNO Q's 13 x 8 LED matrix is used); §2 lamps/buzzer with ground UNO Q pins; §3 CardKB on the ground UNO Q bus; §4 RCU; §5 GDS <-> MCU; §6 GDS status line; §7 spacecraft LED matrix; §8 EGSE board; §9 checks.

### docs/07 Operations
§1.1 ground start-up (hub, radio enumeration `ls /dev/serial/by-id/`); §1.2 host mode (Debian on the UNO Q); §2.6 hardline from the ground UNO Q; §5 instruments + **new §5.4 IMU (6-DoF) checks**; §7.8 T37S (pointer to docs/03 §7); **new §13 Long-range pass operations** (pointing, slow profiles, long frame periods, LOS timeouts, link manager, Doppler for balloons).

### docs/08 Verification and Test
§2.3-2.6 station sketch on the UNO Q (builds, limits, negative control decision); §6 T-HW (new: ESLOV wiring, per-bus checks, hub PD power under ground TX); §7 T-GS rewritten for the ground UNO Q + hub (port 8080, USB-UART in the container with CP2102 and CH340X, ModemManager, router socket, external USB storage, AES speed on QRB2210, clock, UDP listener); §10 T-SC (new 6-DoF tests: gyro zero-rate, accel per axis, game RV vs RV); **§12 Field expanded** (long-range campaign: increasing distances, horizon-limited paths, elevated sites/balloon, uplink closure); refresh §1 numbers.

### docs/09 Regulatory and Safety
§1.1 radios (T37S row), §1.2 EIRP with high-gain antennas (ULR), §2 RF safety (Yagi front distances), §3 electrical (replace the VENTUNO row with ground UNO Q + hub + PD rules), new mast/antenna mechanical safety.

### docs/10 References
§3 hardware: UNO Q ABX00162/ABX00173 (4c60b196 datasheet), E51-TTL-500 (E15-USB-T2), TBH-02, Nicla ABX00050 incl. ESLOV pinout, BHI260AP sections for 6-DoF, hub (if a product is chosen); §4 board support: drop the VENTUNO variant; §6: "Interstellar probe Computer Command Subsystem time-tagged sequences" (L158).

### docs/11 Standards and Roadmap
§3 RadioLib hunk wording; §4 roadmap: ULR items (rotator tracking L111).

### docs/12 Design Decisions (new decisions, superseded table)
New D-71.. (numbering to be fixed by the contract): ground station on UNO Q ABX00173 (supersedes the ground half of D-01, D-15, D-17(2) ground, D-35 board, D-55 OS, D-68 "VENTUNO USB-A"); USB-C hub/dongle and PD power; separate instrument buses (supersedes D-13; D-14 unchanged); Nicla over ESLOV loose wires; BHI260AP 6-DoF data in telemetry and UI; T37S documentation/wiring rework; ULR orientation (defaults, budgets); RadioLib hunk 1 kept or dropped (D-40); "Voyager" -> "Interstellar probe" in D-49 title (anchor stable). Update the index (L40-120), "Superseded decisions" (L941-969), Appendix B only if code tags change.

### docs/13 Web UI Reference
L4 intro; §4.13 SX126x station MCU (ground UNO Q); §7.6 Sensors (new bits/buses); **§9.2 Attitude + new §9.x "IMU (6-DoF)"**: every new key, unit, provenance badge (measured / estimated / computed, §1.4 L216); §13.8 Station I/O panel (board text, mirrors `dashboard.html` L342); §16 /metrics; §18 key index; keep §17 range-test walkthrough. Screenshots `docs/img/ui_science.png`, `ui_commanding.png` (Station I/O) need retaking.

### docs/14 Troubleshooting
§2.2 station (hub/USB enumeration, PD brown-out), §2.6 instruments (ESLOV, per-bus), §2.10 App Lab (two UNO Qs, zip names; replace the VENTUNO OpenOCD row L202 with the ground UNO Q's flashing output, VERIFY); §4 ground events unchanged unless code changes.

### docs/15 Configuration
§3.5 instrument buses (per-instrument bus keys), §4.14 `[gds]` (`frontpanel`, `rcu` text, archive defaults vs 32 GB), **§5 "Station sketch build settings (ground UNO Q MCU)"** (new pins), §6 env vars.

### docs/16 Developer Guide
§2 repo map, §4.5 add a sensor (bus choice), §6.4 FQBN `arduino:zephyr:unoq` for both sketches, §9 packaging table and zip names.

### docs/17 Security Model
§3 diagram (ground UNO Q, hub), §4 threats (USB devices on the hub), §7.1 key copy (`<GROUND-IP>`), §9 residual risks. Keep §7.6, A3, T16.

### docs/18 Data Products
§2 archive location (UNO Q 32 GB eMMC; external USB storage via the hub; no M.2), §7.3 disk budget re-derived, §10 EGSE.

### docs/19 Glossary
Remove VENTUNO, STM32H5F5, QCS8275 rows; add ABX00173, ESLOV, USB PD, DP Alt Mode, USB-C hub/dongle, 6-DoF, game rotation vector, linear acceleration/gravity (if used), Interstellar probe (if the term is defined).

### docs/20 Known Limitations
§1.3 "Ground-station platform (App Lab, ground UNO Q)": re-state V-30..V-38, V-60, V-64, V-69 for the UNO Q; new VERIFY items: hub PD pass-through holds 5 V/3 A during ground TX, USB-UART drivers (cp210x, ch341) in the UNO Q image, AES speed on QRB2210, ESLOV signal level, separate-bus pins, 6-DoF scaling, TBH-02 USB power path; §4 science (IMU limits); L-39 wording ("Interstellar probe").

### docs/21 Test Records
PWR-2 for the ground UNO Q + hub; new IMU and ESLOV records; RNG expanded into a long-range campaign set; completed records left as recorded (Q2).

### docs/22 Bundled Libraries
§3.2 hunk 1 (keep/remove), §4.1 station Bridge methods (ground UNO Q), §6 build (`unoq`), §7 negative control, §9 zip names, §10.

### FAQ
Rewrite: ground radio (USB through the hub), "four sensors on one bus" -> separate buses, range (ULR), switching to 5 W, data storage (32 GB), processing table (two UNO Qs); add "Why a USB-C hub?", "Why ESLOV?", "What does the 6-DoF IMU show?".

### README, Project_Summary, App READMEs, CHANGELOG
README: title, diagram, at-a-glance (ULR first), repo map, quick start (zip names, IPs), docs index (06 title). Project_Summary: board row L11, status. vgq1 README: Nicla ESLOV, bus split, links. dssq README: board, FQBN, default radio (fix L28), hub, OS for host mode, links. CHANGELOG: new top section for this branch.

---

## 8 Duplicated content (consolidate)

| Topic | Places | Proposed single home |
|---|---|---|
| T37S switching | docs/00 Part 13, docs/03 §4.4/4.5/5.3.2, docs/07 §7.8, FAQ L97-107, README L176-179, vgq1 README L26/L116, dssq README L28 | docs/03 §7 (facts) + docs/00 Part 13 (steps) |
| Key generation/installation | README step 3 L153-166, docs/00 Part 6, docs/07 §10.1-10.2, docs/17 §7.1, vgq1 README §2.2, dssq README §2.2 | docs/17 §7.1 (+ docs/00 Part 6 steps) |
| Ground radio one-time configuration | docs/00 §9.2/9.3, docs/03 §5.3.0/5.3.1, docs/14 L89-90, D-68 | docs/03 §6.4/6.5 + docs/00 §9.2 |
| Host mode | docs/00 Part 15, docs/07 §1.2, dssq README §6, D-55 | docs/07 §1.2 |
| Update without losing state | docs/00 Part 16, vgq1 README §2.5, dssq README §2.6 | docs/00 Part 16 |
| Sensor bus wiring | docs/00 §8.5, docs/03 §4.7, D-13, FAQ L258-266, docs/16 §4.5, docs/15 §3.5 | docs/03 §4.6/§5 |
| Verification counts | README L219-228, docs/08 §1, FAQ L35, L311-322, Project_Summary L38-46 | docs/08 §1 (others quote it after the final run) |
| Range numbers | README L57-64, docs/02 §5-6, docs/00 Part 14, FAQ L48-65 | docs/02 |

---

## 9 Short-range / bench orientation: passages to re-frame for ultra long range

Re-frame (keep the bench as checkout): docs/02 L51-55, **L387 (Budget A "the default")**, L414,
L421, **L477 (scenario "Bench")**, L492, L518, L520, L790-791; docs/00 L29 (Part 14 "a day out"),
**Part 11 L749-803**, **Part 14 L899-924** (expand), L62-64, L105; docs/03 **§10 L963-1002**
(title and order), §7.3 L860-869; docs/08 status L8-10, **§12 L838-846** (expand), §8 L480-497;
docs/07 §2.6 title L217, L326; docs/21 RNG L213-253 (expand to campaign); FAQ L48-65; README
L57-58. Keep as safety/regulatory: docs/09 L29, L137-139; docs/00 Part 1 rule 2.

---

## 10 Open questions

Q1. Is "the original handbook" in the owner's message ("we will change our original handbook as
well later on") docs/00 only or the whole docs set? The session's task list also carries
"Resume the beginner handbook (paused by the owner)". The original branch stays untouched now;
should this branch's structure be designed so it can later be ported back unchanged?
Q2. "Eliminate the VENTUNO Q entirely": may historical records keep the name (CHANGELOG L18-485;
completed record 2026-10-13-A in docs/21 L623-668; docs/12 superseded rows), or must they be
reworded ("the previous ground computer")? Rewriting records changes what was measured on.
Q3. Ground front panel on the UNO Q: keep lamps/buzzer/CardKB on the ground UNO Q header, use the
UNO Q's 13 x 8 LED matrix, or both? Which pins (the VENTUNO map D2-D7/A0/PH11-PH12 does not
carry over by itself)?
Q4. Separate buses: which bus for the Nicla (ESLOV loose wires to header pins) and which for the
RM3100(s), given `Wire` (PB10/PB11) carries `Serial3` for the E22 on the spacecraft and `Wire1`
is the Qwiic socket (today the AS7265X option)? A software I2C or moving the radio UART would
change docs/03 §3/§4 and D-11/D-13.
Q5. RadioLib patch hunk 1 (VENTUNO platform entry): drop it (no board in this branch needs it) or
keep it (main-branch compatibility)? Affects docs/22, docs/08, docs/11, docs/12 D-40, FAQ, CI.
Q6. Which ESLOV wires are "the 4 used": 5V/SCL/SDA/GND (INT unused) or INT/SCL/SDA/GND with the
Nicla powered from J2 VIN? On the spacecraft in configuration A, UNO Q JANALOG pin 5 carries 5 V
only when the board is powered through USB-C (docs/03 L224-226): ESLOV pin 1 cannot be fed from it.
Is the ESLOV SDA/SCL level 3.3 V (VDDIO_EXT translator) - not found in the datasheet text.
Q7. App README links: point to `blob/claude/unoq-ground-station/...` (branch-specific), keep `main`
(will show VENTUNO docs until merged), or switch to relative links (not usable inside a zip)?
Q8. USB-C hub/dongle model: needed for its datasheet (PD pass-through wattage, per-port current,
USB 2 vs 3, Ethernet). Not supplied: the docs can only state requirements.
Q9. Does the E22-400TBH-02 draw module power from its USB-C? Not found in the TBH-02 manual;
must be measured before plugging it into the ground hub (5 V TX current 2.9-3.3 A).
Q10. What does "VGQ" stand for? Not found in any document or commit message. If it abbreviates
"Voyager", item 8 of the request would reach the mission name, folder names, App names and the
on-air identification `DE <callsign> VGQ-1` - owner decision.
Q11. docs/20 L-39's citation: the text cites J. Taylor (ed.), *Deep Space Communications*, 3.7.3.2;
whether that chapter's own title contains "Voyager" is not verifiable from the repository. Keep the
citation by title and section only, and describe the spacecraft as "the Interstellar probe"?
Q12. Archive defaults (`archive_max_gb` 16, `archive_min_free_mb` 2048; asserted in
`tests/test_logger.py` L79) on a 32 GB eMMC: keep or lower? The real free space of the UNO Q image
is VERIFY.

---

## Appendix B - Non-Markdown files that name the VENTUNO Q (for implementers; not documents)

From `grep -rli "ventuno\|ABX00181"` excluding caches: `.github/workflows/ci.yml` (L5, L126, L176,
L226, L238, L292-296, L302), `dist/SHA256SUMS`, `dist/dssq-groundstation-ventunoq.zip`,
`dssq-groundstation/host/dssq-gds.service`, `dssq-groundstation/host/install_station.sh`,
`dssq-groundstation/python/config/station.toml` (L2, L39, L168, L170, L178),
`dssq-groundstation/python/dssq/ccsds/aes_gcm.py`, `dssq/display/dashboard.html` (L342 "VENTUNO Q
MCU: one CardKB ..."), `dssq/display/frontpanel.py` (L3), `dssq/gds.py` (L1300, L1302),
`dssq/radio/e22.py`, `dssq/radio/sx126x.py`, `dssq/radioctl.py`, `dssq-groundstation/python/main.py`
(L1), `dssq-groundstation/sketch/sketch.ino` (L3, L5, L13, L85, L354), `dssq-groundstation/sketch/sketch.yaml`
(L1, L23), both bundled `RadioLib/src/BuildOpt.h` copies, `tests/test_packaging.py` (L405, L531-544),
`tests/test_web.py` (L475 `ventunoq.local`), `tools/arduino_routerbridge-0.4.3.patch`,
`tools/gen_sdls_key.py`, `tools/host_test/arduino_stub/Arduino.h`, `.../SPI.h`,
`tools/host_test/check_firmware.sh`, `tools/host_test/station/run.sh`, `tools/package_apps.py`
(L9, L67, L161, L1043), `tools/radiolib-7.8.1.patch`, `vgq1-spacecraft/sketch/libraries/RadioLib/src/BuildOpt.h`.
"Voyager" in code: `vgq1-spacecraft/sketch/src/fsw/autonomy.h` L7 only (and its copy in
`dist/vgq1-spacecraft-unoq.zip`).

## Appendix C - Code and test references to documentation sections (do not break)

| Code location | Reference |
|---|---|
| `dssq-groundstation/python/dssq/ccsds/sdls.py` L88, L132, L149 | docs/09; docs/17 7.6; `SN_DOC = "docs/17_Security_Model.md section 7.6"` |
| `dssq-groundstation/python/dssq/cltu.py` L32, L81 | docs/17 section 7.6 |
| `dssq-groundstation/python/dssq/gds.py` L121, L240, L402, L414, L2448 | docs/09 1.3; docs/17 A3, 7.6, T16 |
| `dssq-groundstation/python/dssq/config.py` L385 | T16, docs/17 |
| `dssq-groundstation/python/dssq/link.py` L72, L291, L539; `rxstats.py` L14; `radio/e22.py` L15; `spectral.py` L962 | docs/08 (T-RF-07, T-RF-02, signal-generator test) |
| `dssq-groundstation/python/dssq/dictionary.py` L7 | docs/05 generated |
| `dssq-groundstation/python/dssq/ccsds/uslp.py` L14 | docs/04 |
| `dssq-groundstation/python/config/station.toml` L21, L65, L79, L100, L112 | docs/09 1.3; docs/08 T-RF-07, T-RF-02, T-RF-03; docs/09 |
| `vgq1-spacecraft/sketch/src/config.h` L4, L7, L201, L215, L225 | docs/03; docs/08 (T-SC-09) |
| `vgq1-spacecraft/sketch/sketch.ino` L14-15 | docs/07, docs/03, docs/04 |
| `vgq1-spacecraft/sketch/src/ccsds/*.h`, `drivers/*.h/.cpp`, `fsw/*.h` | docs/04, docs/05, docs/08, docs/09, docs/10, docs/22 (by number only) |
| `tests/test_sdls.py` L484, L694, L705, L715, L779; `tests/test_end_to_end.py` L167; `tests/test_radio.py` L1392 | docs/17 7.6 (asserted at L705), docs/17 T16 |
| `tests/test_docs.py` | docs/13 (whole) |
| `tests/test_citations.py` | docs/12 Appendix B |
| `tools/gen_dictionary_doc.py` L2, L18, L36 | writes docs/05 |
| `tools/link_budget.py` L10; `tools/gen_sdls_key.py` L25 | docs/08 T-RF-03; docs/09 |

## Appendix A - Heading outlines (H2 and H3 with line numbers, outside code blocks)

#### `README.md` - 234 lines, 2108 words
H2: At a glance (L48) | Highlights (L66) | Repository map (L86) | Quick start (L129) | Documentation (L184) | Verification (L215) | Licences (L230)

#### `CHANGELOG.md` - 490 lines, 5235 words
H2: [Unreleased] - First downlink to the ground station; E15-USB-T2 ground radio (2026-10-13) (L16) | [Unreleased] - UNO Q flight App on arduino:zephyr before 1.0.0 (2026-10-12) (L85) | [Unreleased] - Phase D: repository layout, packaging, final fixes, documentation (2026-10-12) (L158) | [3.0.0] - 2026-10-11 - v3 (L347) | [2.0.0] - 2026-10-09 - v2: first complete system (L466)
H3: Changed (L23) | Fixed (L44) | Added (L61) | Fixed (L92) | Added (L129) | Changed (L149) | Changed (L160) | Added (L182) | Fixed (L218) | Security (L278) | Removed (L332) | Verification after the final fixes (L338) | Phase C - adversarial review and hardening (L349) | Phase B - integration (L388) | Phase A - v3 workstreams (L421) | Added (L468) | Changed (L482) | Removed (L488)

#### `docs/00_Beginner_Setup_Guide.md` - 1047 lines, 9950 words
H2: Part 1 - Safety rules (L54) | Part 2 - What you need (L83) | Part 3 - PC tools and project files (L120) | Part 4 - Flash the Nicla Sense ME (L194) | Part 5 - Set up the boards and import the two Apps (L219) | Part 6 - Generate the security keys (after the import) (L342) | Part 7 - Settings for your station and site (L440) | Part 8 - Wire the spacecraft step by step (L503) | Part 9 - Wire the ground station step by step (L606) | Part 10 - First power-up of the complete system (L725) | Part 11 - First contact on the bench (L749) | Part 12 - Your first commands (L805) | Part 13 - Switching to the 5 W E22-400T37S (the A0 strap) (L848) | Part 14 - Going for distance (L899) | Part 15 - Host-mode fallback for the ground station (L926) | Part 16 - Updating or re-importing an App without losing state (L979) | Part 17 - Troubleshooting pointers (L1021)
H3: 2.1 Parts (L85) | 2.2 Tools (L107) | 2.3 Other things (L112) | 3.1 Install the tools (L122) | 3.2 Get the project files (L133) | 3.3 Optional: dry run on the PC with the simulator (L161) | 5.1 First start of each board (L221) | 5.2 Import the Apps (L239) | 5.3 First run of the spacecraft App (nothing wired) (L255) | 5.4 First run of the ground App (nothing wired) (L310) | 6.1 Choose the protection (both ends must match) (L349) | 6.2 Generate one key pair on the PC (L366) | 6.3 Copy each file to its board (L386) | 6.4 Restart both Apps (L398) | 6.5 Keep the key safe - three rules (L425) | 7.1 Spacecraft (`sketch/src/config.h`) (L455) | 7.2 Ground (`data/station.toml` on the VENTUNO Q) (L465) | 8.1 Prepare (L508) | 8.2 Power (configuration A, docs/03 section 4.2.1) (L516) | 8.3 Radio E22-400T33D (docs/03 section 4.3) (L529) | 8.4 Model strap: leave A0 open (L558) | 8.5 Sensor bus, one instrument at a time (docs/03 section 4.7) (L565) | 8.6 Mount the magnetometers (L593) | 9.1 Power and radio supply (L610) | 9.2 Ground radio on the EBYTE E15-USB-T2, one-time configuration (L625) | 9.3 Option: Radio Control Unit (M0/M1/AUX) (L671) | 9.4 Lamps and buzzer (L694) | 9.5 CardKB keyboard (L711) | 13.1 Spacecraft (L856) | 13.2 Ground station (L878)

#### `docs/01_System_Design.md` - 971 lines, 10348 words
H2: 1 Purpose (L24) | 2 Concept of operations (L71) | 3 Architecture (L128) | 4 Data flow (L360) | 5 Flight-software modes (L464) | 6 Fault detection, isolation and recovery (L562) | 7 Onboard autonomy (L608) | 8 Link-layer control: COP-1 (L688) | 9 Resource budgets (L759) | 10 Open items (VERIFY) owned by this document (L959)
H3: 1.1 In plain words (L26) | 1.2 Elements and their deep-space analogues (L46) | 1.3 What it is not (L59) | 2.1 A mission day, step by step (L73) | 2.2 Who decides what (L115) | 3.1 Both boards at a glance (L130) | 3.2 Spacecraft MCU: the flight software (L189) | 3.3 Spacecraft Linux side: the EGSE (L218) | 3.4 Ground station Linux side: the ground data system (L234) | 3.5 Ground station MCU: station I/O (L293) | 3.6 The MCU-Linux link and the RPC mailbox (L302) | 3.7 Radio options (L346) | 4.1 Downlink, layer by layer (plain words) (L362) | 4.2 Downlink data flow diagram (L389) | 4.3 Uplink data flow (L416) | 4.4 Virtual channels and the onboard recorder (L432) | 4.5 Where each piece of processing happens (L451) | 5.1 Mode state machine (L466) | 5.2 What each mode does (L495) | 5.3 Hibernation and transmitter inhibit (L537) | 7.1 Command-loss timer (L610) | 7.2 Coordinated radio changes with automatic revert (L616) | 7.3 Stored command sequences (L631) | 7.4 Beacon levels (L642) | 7.5 Downlink load control (L659) | 7.6 Magnetometer self-test and calibration, spectrometer autonomy (L673) | 8.1 FARM-1 (spacecraft, window 10) (L695) | 8.2 FOP-1 (ground) (L717) | 8.3 Command life cycle (every display) (L741) | 9.1 Flash and static RAM per build (L761) | 9.2 Stack (L805) | 9.3 Heap (L830) | 9.4 Main-loop timing budget (L842) | 9.5 Link capacity per radio setting (L893) | 9.6 MCU-Linux link budget (L938) | 9.7 Ground side (L948)

#### `docs/02_Link_Budget_and_RF_Analysis.md` - 874 lines, 9495 words
H2: 1 What limits the range (plain words) (L39) | 2 The radios (L74) | 3 Where the sensitivity numbers come from (L259) | 4 Equations (L358) | 5 Worked link budgets (L378) | 6 Range: what to expect (L456) | 7 Data rate and the uplink (L544) | 8 What the ground measures and what it estimates (L666) | 9 Frequency error, Doppler and AFC (SX126x) (L712) | 10 Choosing a rate: profiles, AIRRATE and the link manager (L770) | 11 Channel access, duty cycle and error rescue (L814) | 12 Open items (VERIFY) owned by this document (L864)
H3: 2.1 The three options at a glance (L76) | 2.2 E22-400T33D (default) (L97) | 2.3 E22-400T37S (preserved option) (L119) | 2.4 E22 air-rate codes and power codes (L143) | 2.5 SX126x LoRa profiles 0 to 7 (L178) | 2.6 Channel plan (L221) | 2.7 Air time (L232) | 3.1 Source order (L261) | 3.2 Semtech SX1261/2 Table 3-8 (L273) | 3.3 Computed and estimated values (L294) | 3.4 Plausibility: the Shannon limit (L321) | 3.5 Measuring it: test T-RF-03 (L338) | 5.1 Budget A: E22-400T33D, two dipoles, 10 km, antennas at 2 m and 10 m (the default) (L387) | 5.2 Budget B: E22-400T33D to a 12 dBi Yagi, 40 km, hill to hill (30 m and 300 m) (L408) | 5.3 Budget C: E22-400T37S, two dipoles, 10 km (L414) | 5.4 Budget D: SX126x profile 1, two dipoles, 10 km (L421) | 5.5 Budget E: SX126x profile 0 to a 12 dBi Yagi, 40 km, hill to hill (L428) | 5.6 Free-space range per rate code (dipoles at both ends, 10 dB fade margin) (L434) | 6.1 The short answer (L458) | 6.2 Scenarios (L470) | 6.3 Margin left at the horizon (L485) | 6.4 EBYTE's reference distances (L504) | 6.5 What improves range, ranked (L513) | 6.6 The "35 dBi antenna" claim (L525) | 6.7 Fresnel zone clearance (L534) | 7.1 Downlink capacity (L546) | 7.2 Uplink CLTU air time (L560) | 7.3 Ground timers follow the frame period (L652) | 8.1 Measured (L668) | 8.2 Derived (labelled "est." where an assumption enters) (L691) | 9.1 How much offset the receiver tolerates (L714) | 9.2 Doppler and range rate (L734) | 9.3 Ground AFC (L744) | 10.1 What can be chosen (L772) | 10.2 Link manager (L793) | 11.1 Listen before talk (L816) | 11.2 Duty cycle and PA temperature (L825) | 11.3 Error rescue: LoRa CRC plus Reed-Solomon (L843) | 11.4 Regulatory (L855)

#### `docs/03_Hardware_Integration.md` - 1025 lines, 11429 words
H2: Contents (L48) | 1 Configurations at a glance (L64) | 2 System overview (L79) | 3 Logic levels and buses (L112) | 4 Spacecraft (UNO Q) wiring (L152) | 5 Ground station (VENTUNO Q) wiring (L506) | 6 Power budget and PSU sizing (L684) | 7 Bill of materials (L808) | 8 Thermal (L880) | 9 Magnetic cleanliness (L923) | 10 Antennas, coax and bench RF (L963) | 11 Hardware items to verify (L1006)
H3: 4.1 Pin map (UNO Q, ABX00162) (L154) | 4.2 Power (L193) | 4.3 Radio: E22-400T33D (default) (L263) | 4.4 Radio: E22-400T37S on the E22-400TBH-02 board (5 W option) (L286) | 4.5 Radio-model strap: selecting the 5 W module without a code edit (L315) | 4.6 SX126x option on SPI (reference module EBYTE E22-400M33S) (L345) | 4.7 Sensor bus (`Wire2` on A4/A5, 3.3 V, 100 kHz) (L373) | 4.8 Spacecraft wiring diagram (configuration A, E22-400T33D) (L458) | 5.1 Pin map (VENTUNO Q MCU, ABX00181) (L508) | 5.2 Power (L529) | 5.3 Ground radio (L540) | 5.4 Station I/O: lamps, buzzer, CardKB (L616) | 5.5 Ground station wiring diagram (E22-400T33D) (L647) | 6.1 Load data (L686) | 6.2 Spacecraft, configuration A, E22-400T33D (L713) | 6.3 Spacecraft with the E22-400T37S (5 W) (L731) | 6.4 Spacecraft, configuration B (board-powered E22-400T33D) (L746) | 6.5 Ground station (L762) | 6.6 Battery sizing (spacecraft) (L781) | 6.7 Fuses and wiring (L794) | 7.1 Spacecraft (VGQ-1) (L815) | 7.2 Ground station (DSS-Q1) (L838) | 7.3 Bench RF test set (L860) | 7.4 Options (L871)

#### `docs/04_Space_Data_Link_ICD.md` - 956 lines, 9585 words
H2: 1 Scope and conventions (L25) | 2 Managed parameters (L45) | 3 Physical layer and radio transport (L72) | 4 Downlink (spacecraft to ground) (L132) | 5 Uplink (ground to spacecraft) (L366) | 6 COP-1 (L548) | 7 CFDP (727.0-B-5, class 1, unacknowledged) (L614) | 8 Link-layer procedures (L699) | 9 MCU-Linux RPC contract (Arduino Router Bridge) (L768) | 10 Interoperability and verification (L918) | 11 Open items (VERIFY) owned by this document (L948)
H3: 3.1 One CADU per radio packet (L74) | 3.2 E22 UART transport (models E22-400T33D and E22-400T37S) (L88) | 3.3 SX126x SPI transport (build option) (L107) | 3.4 Ground frame synchronisation (L115) | 4.1 CADU (L134) | 4.2 TM Transfer Frame (VGQ_DOWNLINK_FRAMING = 0, default) (L144) | 4.3 USLP TM frame (VGQ_DOWNLINK_FRAMING = 1) (L168) | 4.4 Only-Idle-Data (OID) frames (L196) | 4.5 CLCW (Operational Control Field) (L206) | 4.6 Virtual channels and frame selection (L230) | 4.7 SDLS on the downlink (TM) (L258) | 4.8 Space packets (L292) | 4.9 Time code and time correlation (L330) | 4.10 Command verification (CMDVER) (L350) | 5.1 CLTU (L368) | 5.2 TC Transfer Frame (232.0-B-4, `tc_framing = "TC"`, default) (L385) | 5.3 USLP TC frame (732.1-B-2, `tc_framing = "USLP"`) (L407) | 5.4 SDLS on the uplink (TC) (L421) | 5.5 TC packet (L460) | 5.6 Frame lengths and the 64-octet limit (L477) | 5.7 Acceptance chain on the spacecraft (L500) | 5.8 Station identification packets on the uplink (L529) | 6.1 FARM-1 (spacecraft) (L553) | 6.2 FOP-1 (ground) (L573) | 6.3 Known limits (L603) | 7.1 PDU header (10 octets) (L616) | 7.2 PDUs (L637) | 7.3 Transactions (L659) | 7.4 Relay of EGSE files (L672) | 7.5 Ground receiver (L688) | 8.1 Coordinated rate or channel change (L701) | 8.2 Hibernation and wake (L718) | 8.3 Uplink window (L728) | 8.4 Radio health reports (L759) | 9.1 Common rules (L770) | 9.2 UNO Q: EGSE (Linux) -> flight MCU calls (L792) | 9.3 UNO Q: flight MCU -> EGSE notifications (L804) | 9.4 Status keys (L825) | 9.5 Mailbox semantics (flight and station) (L838) | 9.6 EGSE rules (nonce safety) (L871) | 9.7 VENTUNO Q: GDS (Linux) <-> station MCU (L890) | 10.1 Interop outputs (L920) | 10.2 How this ICD is verified (L937)

#### `docs/05_Telemetry_and_Command_Dictionary.md` - 625 lines, 8553 words
H2: 1 Telemetry packets (L7) | 2 Packet generation cadence per flight-software mode (L248) | 3 Bit maps and enumerations (L260) | 4 Telecommands (uplinked; APID 0x0C0, user data = opcode + arguments) (L479) | 5 Event reports (EVR, APID 0x012) (L541)
H3: Variable-length packets (L9) | APID 0x010 - HK: Engineering housekeeping (beacon in hibernation) (78 octets of user data, VC0) (L18) | APID 0x011 - RF: Telecom subsystem (any radio model) (49 octets of user data, VC0) (L66) | APID 0x013 - CMDVER: Command verification (8 octets of user data, VC0) (L95) | APID 0x014 - TIMECORR: SCLK correlation sample (10 octets of user data, VC0) (L105) | APID 0x020 - MAG: Magnetometer science (RM3100 boom + BMM150 body), raw (46 octets of user data, VC1) (L115) | APID 0x021 - ATT: Attitude determination (Nicla Sense ME + TRIAD) (37 octets of user data, VC1) (L135) | APID 0x022 - SPEC: AS7265X spectrometer (SPEC v3.1) (129 octets of user data, VC1) (L159) | APID 0x023 - ENV: Environment (Nicla Sense ME) (24 octets of user data, VC1) (L233) | HK.fdir_flags (L262) | HK.sensor_health (SENSORS enable mask: NICLA_ME, AS7265X, RM3100_OB, RM3100_IB = 0x0381) (L283) | MAG.flags (L298) | ATT.flags (L309) | ENV.flags (L321) | RF.flags (rf_flags) (L330) | HK.mag_bist (L337) | HK.radio_fault (1..4 E22-400T37S abnormal status, 16..19 SX126x) (L341) | HK.reset_cause (L355) | HK.fsw_mode (L366) | HK.sdls_tc_service / sdls_tm_service (L376) | HK.sdls_tm_state (L384) | HK.cfdp_state (L393) | HK.seq_state (L401) | HK.beacon_level (L410) | RF.radio_model (L419) | RF.framing (L427) | SPEC.spec_mode (L434) | SPEC.spec_class (onboard heuristic) (L444) | CMDVER.error_code / HK.cmd_last_status (L457) | CMDVER.cmd_source (stage octet b7..6) (L469) | COP-1 control directives (Type-BC frames) (L518) | Ground-station directives (executed by the GDS, never uplinked as such) (L525)

#### `docs/06_Displays.md` - 316 lines, 3210 words
H2: Contents (L25) | 1 Overview (L39) | 2 Station lamps and buzzer (L62) | 3 CardKB console (L127) | 4 Radio Control Unit (L173) | 5 How the station MCU and the GDS talk (L193) | 6 GDS status line (L231) | 7 Spacecraft LED matrix (L246) | 8 Spacecraft EGSE status board (L270) | 9 Checks and items to verify on hardware (L293)
H3: 2.1 What each lamp means (L77) | 2.2 Buzzer (L101) | 2.3 Reading the lamps during operations (L113) | 3.1 Keys (L135) | 3.2 Hazardous commands and the second operator (L151) | 3.3 If keys do nothing or produce wrong characters (L162)

#### `docs/07_Operations_Procedures.md` - 891 lines, 9384 words
H2: 1 Start-up (L29) | 2 Routine commanding (L148) | 3 Modes and downlink cadence (L296) | 4 Stored command sequences (L319) | 5 Instrument procedures (L358) | 6 Hibernation and wake (L500) | 7 Link procedures (L535) | 8 Data logger and exports (L672) | 9 File transfer (CFDP) (L707) | 10 Security keys (SDLS) (L748) | 11 Contingencies (L852) | 12 Shut-down (L875)
H3: 1.1 Ground station, App Lab mode (normal) (L31) | 1.2 Ground station, host (systemd) mode (L60) | 1.3 Spacecraft (L93) | 1.4 First contact (every pass after a spacecraft boot) (L111) | 1.5 Practising without hardware (L129) | 2.1 Typing commands (L150) | 2.2 Hazardous commands and confirmation (L168) | 2.3 Following a command (L184) | 2.4 Acknowledging the spacecraft beacon (L191) | 2.5 COP-1: lockout and resynchronisation (L205) | 2.6 Hardline (umbilical) commanding on the bench (L217) | 2.7 Routine command table (L251) | 2.8 Station identification (callsign at both ends) (L274) | 5.1 Magnetometer calibration (MAGCAL) (L360) | 5.2 Magnetometer self-test (MAGBIST) (L418) | 5.3 Spectrometer (AS7265X) (L425) | 7.1 Coordinated air-rate change (`LINKRATE`) with automatic revert (L537) | 7.2 Coordinated channel change (`LINKCHAN`) (L573) | 7.3 Find a quiet channel (L580) | 7.4 Bringing the station back to the spacecraft's setting (L604) | 7.5 Adaptive link manager (L624) | 7.6 Transmit power and receiver options (L635) | 7.7 Silencing the transmitter (L643) | 7.8 Switching the spacecraft to the 5 W E22-400T37S (L649) | 9.1 Recorder contents (`CFDPPUT SSR`) (L714) | 9.2 EGSE files (`CFDPPUT EVRLOG`, `CFDPPUT CADU`) (L728) | 9.3 Cancel and failures (L736) | 10.1 Generate a key (after importing the two Apps) (L754) | 10.2 Install it (L774) | 10.3 Choosing the services (L797) | 10.4 Key rotation and suspected compromise (L807) | 10.5 Counter files: backup and restore (L823) | 10.6 `SDLS RESETREPLAY` (L843)

#### `docs/08_Verification_and_Test.md` - 904 lines, 13095 words
H2: 1 Verification at a glance (L24) | 2 What each automated layer proves (L51) | 3 Running every check on your PC (L229) | 4 Continuous integration (L292) | 5 Before any hardware test (L310) | 6 Power, wiring and platform (T-HW) (L353) | 7 Ground-station platform (T-GS) (L438) | 8 Radio frequency (T-RF) (L478) | 9 Telemetry and telecommand (T-TM, T-TC) (L618) | 10 Instruments (T-SC) (L733) | 11 Fault protection (T-FD) (L798) | 12 Field (T-FLD) (L838) | 13 Coverage of every VERIFY item (L848) | 14 One-off verifications on record (L890)
H3: 2.1 Host tests (`tools/host_test`) (L53) | 2.2 Ground tests (`tests/`, pytest) (L82) | 2.3 Firmware type-check (`tools/host_test/check_firmware.sh`) (L123) | 2.4 Station sketch harness (`tools/host_test/station/run.sh`) (L144) | 2.5 RouterBridge harness and stack bound (`tools/host_test/routerbridge`) (L152) | 2.6 Real builds (arduino-cli + arduino:zephyr) (L166) | 2.7 Lint (L210) | 2.8 Simulator acceptance (L218) | 5.1 Equipment (L312) | 5.2 Rules (L328) | 6.1 T-HW-07 - board-powered radio (contract sections 15 and 16.2) (L370) | 6.2 T-HW-08 - reset cause and the BKP31R brown-out marker (L390) | 6.3 T-HW-10 - endurance, timing and the bundled RouterBridge on hardware (L414) | 7.1 T-GS-08 - UDP telecommand listener refusal (L455) | 8.1 T-RF-02 - RSSI calibration (L499) | 8.2 T-RF-03 - measured sensitivity (L516) | 8.3 T-RF-06 - SX126x frequency error: sign, scale and static offset (L540) | 8.4 T-RF-07 - transmit power per code (L577) | 8.5 T-RF-08 - SX126x bring-up (option only) (L599) | 9.1 T-TM-04 - time correlation and `radio_latency_s` (L636) | 9.2 T-TM-05 - SDLS TM epoch (nonce safety) (L646) | 9.3 T-TC-03 - SDLS on the uplink (L661) | 9.4 T-TC-07 - station identification on air, both ends (L672) | 10.1 T-SC-06 - BMM150 scale (L750) | 10.2 T-SC-08 - AS7265X checks (L762) | 10.3 T-SC-09 - boresight frame (L783) | 11.1 T-FD-09 - hardware watchdog (L813)

#### `docs/09_Regulatory_and_Safety.md` - 244 lines, 2844 words
H2: 1 Licensing (L14) | 2 RF safety (L118) | 3 Electrical safety (L166) | 4 Thermal safety (L183) | 5 Optical and mechanical safety (L200) | 6 Safety features built into the software (L213)
H3: 1.1 What the radios can do (L16) | 1.2 Why you need an amateur-radio licence (L27) | 1.3 Station identification (L49) | 1.4 Encryption: choose AUTH on amateur frequencies (L86) | 2.1 Protect the radios (L120) | 2.2 Protect people (RF exposure) (L141)

#### `docs/10_References.md` - 167 lines, 2447 words
H2: 1 CCSDS Recommended Standards (Blue Books) implemented (L14) | 2 Other standards and specifications (L37) | 3 Hardware documentation (L53) | 4 Board support and platform (L76) | 5 Software libraries (L89) | 6 Algorithms and physics (L131) | 7 Further reading (L160)
H3: 5.1 In the sketches (L91) | 5.2 On the Linux sides (L105) | 5.3 Development and verification only (never on the boards) (L113)

#### `docs/11_Standards_Status_and_Roadmap.md` - 130 lines, 1911 words
H2: 1 What is implemented, and how current it is (October 2026) (L10) | 2 Deliberately not used - and why (L37) | 3 Radio software: what is used now (L54) | 4 Roadmap (L77)
H3: 4.1 Done since the previous roadmap (L79) | 4.2 Deep-space-network-style features not implemented ("leave it for now") (L88) | 4.3 Open items (L123)

#### `docs/12_Design_Decisions.md` - 1221 lines, 23451 words
H2: Index (L40) | Architecture (L121) | Hardware and wiring (L203) | Link layer and coding (L330) | Security (L433) | Commanding (L538) | MCU <-> Linux link and third-party libraries (L575) | Science and payload (L625) | Ground data system (L728) | Quality and verification (L765) | Final fix round (Phase D) (L813) | Superseded decisions (L941) | Appendix A - Phase C review outcomes (L971) | Appendix B - Citation tags in code (L1053) | How to add a decision (L1212)
H3: D-01 Two Arduino Q boards, each split into MCU (real time) and Linux (L124) | D-02 All flight software on the MCU; the UNO Q Linux side is optional EGSE (L135) | D-03 CCSDS protocols end to end, private spacecraft ID 0x0A7 (L146) | D-04 Build options are `config.h` defines; the E22 model is read at boot (L157) | D-05 The repository is the two App Lab App folders (L168) | D-06 Offline Apps: standard library only, no `requirements.txt` (L179) | D-07 Pure C++ protocol and algorithm code, compiled and tested on a PC (L191) | D-08 EBYTE E22-400T33D is the default radio (L206) | D-09 E22-400T37S kept, chosen at boot by a jumper on header A0 (L218) | D-10 SX126x SPI module as the long-range option, LoRa profiles 0-7 (L229) | D-11 E22 UART on `Serial3` (header SDA/SCL pins), not on D0/D1 (L240) | D-12 External 10 kOhm pull-ups on M0/M1; T37S board jumpers removed (L251) | D-13 One sensor bus: `Wire2` = I2C3 on A4/A5 (the AS7265X optionally on the Qwiic socket), no multiplexer (L262) | D-14 Instrument set: Nicla Sense ME, AS7265X, one or two RM3100 (L274) | D-15 One CardKB on the VENTUNO Q header I2C; second operator in the web UI (L285) | D-16 The web dashboard is the only station display (L296) | D-17 Board-powered radio supported; brown-out derating; reset cause by backup register (L307) | D-18 Channel grid default 433.125 MHz, allowed-channel list, callsign in clear (L318) | D-19 200-octet frames + RS(255,223) = 236-octet CADU = one LoRa packet (L333) | D-20 No inner LDPC/turbo/convolutional code; 255-bit randomizer (L344) | D-21 "RS rescue": concatenated coding on the SX126x path (L355) | D-22 Virtual channels VC0-VC3 + OID, fixed priority, priority reserve (L366) | D-23 TM frames by default, USLP selectable, uplink format auto-detected (L377) | D-24 Downlink paced by measured capacity; 1.5 s frame floor; 50 % TX duty (L388) | D-25 Every sensitivity value carries its source (L399) | D-26 SX126x: Rx boosted gain with retention, CAD, ground AFC (L410) | D-27 CFDP class 1 (unacknowledged) with an EGSE relay (L421) | D-28 SDLS AE by default; AUTH for amateur operation; NONE on the bench (L438) | D-29 One master key, per-direction derived keys, never in Git or a zip (L449) | D-30 TM nonce safety: epoch = EGSE boot counter, hold, null-SA fallback (L460) | D-31 An epoch value is offered to the MCU at most once, ever (L471) | D-32 TC sequence-number file: atomic, never lowered, locked, self-healing (L482) | D-33 TC anti-replay held until the EGSE restores the counter (L493) | D-34 A change of SDLS status on a VC is a packet discontinuity (L504) | D-35 Pure-Python AES-256-GCM on the ground, a different formulation (L515) | D-36 Web UI security: token, Host checks, hash CSP, bounded server (L526) | D-37 One two-stage command parser for live and stored commands (L541) | D-38 CMDVER carries the command source in the stage octet (L552) | D-39 Hazardous commands: confirmation, optional two-person rule, self-healing changes (L563) | D-40 RadioLib 7.8.1 bundled and patched (reversal of the in-house driver plan) (L580) | D-41 Arduino_RouterBridge 0.4.3 bundled and patched (L591) | D-42 RPC mailbox to the loop thread, kept as defence in depth (L602) | D-43 Bridge rules: no `Bridge.call`, no `Monitor`, gated notifications (L613) | D-44 Own AS7265X driver; LED current limits; INT pin not used (L628) | D-45 Exposures in PA-off windows; lamps never lit during magnetometer samples (L639) | D-46 CCSDS 121.0-B-3 lossless compression for SPECBURST (L650) | D-47 Log-encoded spectra; heuristics and approximations labelled (L661) | D-48 Magnetometer calibration fitted on the ground, applied on board to attitude only (L672) | D-49 Stored command sequences (Voyager CCS style), RAM only (L683) | D-50 Beacon urgency levels (Deep Space 1 style) (L694) | D-51 Standard ground products: CCSDS TDM and XTCE (L705) | D-52 Sensor facts taken from the manufacturers' data, not from libraries (L716) | D-53 Data logger: SQLite WAL, 1 Hz receiver series, retention, free-space guard (L731) | D-54 Ground robustness: snapshot copy, error boundaries, best-effort archive (L742) | D-55 Host (systemd) mode kept as a fallback (L753) | D-56 Coding standard: Power of Ten, FP32 only, no heap after start-up (L768) | D-57 Five verification layers, all automated in CI except hardware (L779) | D-58 Dictionary document generated from the code; drift tests (L790) | D-59 Adversarial reviews before integration and before release (L801) | D-60 Hardware watchdog: the STM32U585 IWDG, programmed by direct register writes (L819) | D-61 The ground station identifies itself in clear text on the uplink (L830) | D-62 The UDP telecommand listener accepts loopback addresses only (L841) | D-63 Transmitter duty measured over whole transmission cycles (`TxDutyMeter`) (L852) | D-64 RSTCNT also clears the four event-latched FDIR flags (L863) | D-65 An unknown TC counter is held, then re-synchronised onto a time floor (4 numbers per second since 2026-01-01) (L874) | D-66 64-octet Serial rings (arduino:zephyr before 1.0.0): a UART pump thread for the E22 port, long notifications into an empty ring (L885) | D-67 I2C bus clear releases the lines with the MCU's weak pull-ups: an empty A4/A5 bus is not a stuck bus (L896) | D-68 Ground radio by default on the EBYTE E15-USB-T2, `[gds] rcu = false`; the Radio Control Unit becomes the option (L907) | D-69 Spectrometer saturation judged on the raw counts of every channel read, valid or not (L918) | D-70 Received power shown at the readings' resolution (E22 whole dB), not 4 decimals (L929)

#### `docs/13_Web_UI_Reference.md` - 3126 lines, 42114 words
H2: Contents (L71) | 1 How the dashboard works (L94) | 2 Header bar and beacon banner (L397) | 3 OVERVIEW tab (L457) | 4 RECEIVER tab (L533) | 5 LINK & SECURITY tab (L1349) | 6 FRAMES & PACKETS tab (L1611) | 7 SPACECRAFT tab (L1697) | 8 SPECTROMETER tab (L2130) | 9 SCIENCE tab (L2434) | 10 MAGCAL tab (L2511) | 11 SEQUENCES tab (L2567) | 12 FILES tab (L2607) | 13 COMMANDING tab (L2758) | 14 Health, events and logs (L2880) | 15 Alarm logic (L2912) | 16 Machine interfaces: /metrics and the JSON API (L2963) | 17 Reading the RECEIVER tab during a range test (L3018) | 18 Snapshot key index (L3086)
H3: 1.1 The path of a number (L96) | 1.2 Opening the dashboard (L138) | 1.3 How often things change (L153) | 1.4 Measured, estimated, computed: the badges and labels (L216) | 1.5 Display conventions (L240) | 1.6 The statistics windows explained (L284) | 1.7 Snapshot keys and the notation in this document (L339) | 3.1 Tiles (L480) | 3.2 Charts (L497) | 3.3 Spacecraft summary (L504) | 3.4 Alarm reasons, latest events, command status (L520) | 4.1 What the tab is for (L535) | 4.2 RF now (L582) | 4.3 Receiver statistics (L831) | 4.4 Charts and histograms (L872) | 4.5 Link budget (L891) | 4.6 Radio configuration (L965) | 4.7 AFC (L1039) | 4.8 Doppler and range rate (L1100) | 4.9 Frame synchronizer (L1124) | 4.10 Decoder (L1150) | 4.11 Frames (L1178) | 4.12 Packets per APID (L1203) | 4.13 SX126x station MCU (L1212) | 4.14 Serial and radio link (L1243) | 4.15 Two-way link (L1259) | 4.16 Link manager and pass events (L1285) | 4.17 RF survey (L1315) | 4.18 Receiver state (L1332) | 5.1 Framing (L1367) | 5.2 SDLS (L1380) | 5.3 SDLS TM rejections (L1413) | 5.4 OID identification (L1429) | 5.5 COP-1 FOP (L1439) | 5.6 CLCW (L1460) | 5.7 Uplink (L1475) | 5.8 Time correlation (L1486) | 5.9 Station identification (L1518) | 6.1 Virtual channels and APIDs in one paragraph (L1630) | 6.2 Virtual channels (L1639) | 6.3 Packets by APID (L1663) | 6.4 Frames by format and VC, time correlation, CLCW / COP-1 (L1688) | 7.1 Reading a telemetry table (L1723) | 7.2 Limits and how they are checked (L1756) | 7.3 Housekeeping (HK): all 43 fields (L1786) | 7.4 Telecom (RF): all 24 fields (L1888) | 7.5 Fault-protection flags (L1934) | 7.6 Sensors (L1970) | 7.7 Beacon (L1993) | 7.8 Onboard state and the decoded block (L2025) | 7.9 Other packets: CMDVER, TIMECORR, RFSCAN (L2061) | 7.10 Spacecraft EVRs (L2086) | 7.11 Anomaly detector (L2103) | 8.1 Instrument state (L2181) | 8.2 Spectrum, raw counts, normalized spectrum, channels table (L2204) | 8.3 Reflectance, white reference and library controls (L2220) | 8.4 Spectral features and counters (L2243) | 8.5 Vegetation and spectral indices (L2256) | 8.6 Radiometry (L2277) | 8.7 Colorimetry (L2298) | 8.8 Light-source classification (L2316) | 8.9 Fluorescence (L2342) | 8.10 Spectral library (L2355) | 8.11 Builders: SPECCFG and SPECMODE (L2366) | 8.12 History, waterfall, illuminance and CCT history (L2380) | 8.13 Burst (L2389) | 8.14 Sky map, die-temperature drift, events and notes (L2412) | 9.1 Magnetometers (L2454) | 9.2 Attitude (L2478) | 9.3 Environment (L2495) | 12.1 CFDP transactions (L2630) | 12.2 Receiver counters, onboard state and TDM summary (L2656) | 12.3 Received files (L2669) | 12.4 Tracking Data Messages and XTCE (L2676) | 12.5 Receiver data logger (L2694) | 12.6 CSV export (L2735) | 13.1 Command entry (L2780) | 13.2 Pending confirmations (L2797) | 13.3 Command history (L2808) | 13.4 Console (L2830) | 13.5 Event log (L2837) | 13.6 Dictionary (L2842) | 13.7 GDS health panel (L2849) | 13.8 Station I/O panel (L2862)

#### `docs/14_Troubleshooting_and_Event_Response.md` - 737 lines, 15932 words
H2: 1 Where messages appear and how to read them (L23) | 2 Fault tree (L57) | 3 Spacecraft event reports (EVR) - complete catalogue (L206) | 4 Ground events - complete catalogue by source (L379) | 5 Alarm reasons (dashboard header, OVERVIEW, station ALARM lamp) (L672) | 6 Fault-protection (FDIR) flags - `HK.fdir_flags` (L711)
H3: 2.1 Expected indications that are not faults (L59) | 2.2 Station and dashboard (L74) | 2.3 No AOS (no frames received) (L97) | 2.4 Commands do not get through (L111) | 2.5 Spacecraft resets or goes SAFE unexpectedly (L139) | 2.6 Instruments (L149) | 2.7 RF details (SX126x) (L162) | 2.8 Security (SDLS) (L171) | 2.9 Files, archive and time (L182) | 2.10 Importing and starting an App in App Lab (L193) | 3.1 System 0x00xx (L216) | 3.2 Uplink and SDLS 0x01xx (L232) | 3.3 COP-1 and link changes 0x02xx (L258) | 3.4 Radio 0x03xx (L275) | 3.5 Recorder 0x04xx (L294) | 3.6 Command rejection 0x05xx (L301) | 3.7 Fault protection 0x06xx (L312) | 3.8 Spectrometer 0x07xx (L329) | 3.9 Hibernation 0x08xx (L341) | 3.10 Autonomy 0x09xx (L348) | 3.11 File delivery (CFDP) 0x0Axx (L365) | 4.1 GDS (process) (L383) | 4.2 WEB (L394) | 4.3 RADIO (L402) | 4.4 RX (receiver) (L433) | 4.5 DECODER and DECOM (L441) | 4.6 SDLS (L449) | 4.7 LIMITS (L469) | 4.8 BEACON (L493) | 4.9 S/C EVR (L500) | 4.10 CMD (commanding) (L504) | 4.11 FOP (COP-1 sending end) (L514) | 4.12 LINK (coordinated changes, link manager) (L526) | 4.13 AFC (SX126x) (L538) | 4.14 CFDP (file receiver) (L555) | 4.15 TDM (L575) | 4.16 MAGCAL (L585) | 4.17 SPECTRAL (ground spectral analytics) (L594) | 4.18 ARCHIVE (data logger) (L607) | 4.19 EXPORT (L616) | 4.20 INTEROP (L623) | 4.21 IDENT (station identification) (L630) | 4.22 Messages that are not events (L638) | 4.23 Spacecraft EGSE messages (UNO Q App log) (L652)

#### `docs/15_Configuration_Reference.md` - 474 lines, 7980 words
H2: 1 How a change takes effect (L27) | 2 Settings that MUST MATCH between the two ends (L62) | 3 Flight configuration: `config.h` (L98) | 4 Station configuration: `station.toml` (L234) | 5 Station sketch build settings (VENTUNO Q MCU) (L428) | 6 Environment variables and command-line options (L447) | 7 Generated and secret files (never in Git, never in an App Lab zip) (L464)
H3: 3.1 Radio model and radio interface (L107) | 3.2 SX126x module on SPI (only with `VGQ_RADIO = VGQ_RADIO_SX126X`) (L125) | 3.3 Radio defaults (MATCH: `station.toml [radio]`) (L140) | 3.4 Framing and SDLS (L153) | 3.5 Sensor bus and instruments (L164) | 3.6 AS7265X spectrometer (L183) | 3.7 CFDP, EGSE link and debugging (L195) | 3.8 Solid-state recorder, timing, thermal (L205) | 3.9 Site and identification (L223) | 4.1 `[station]` (L241) | 4.2 `[spacecraft]` (L254) | 4.3 `[radio]` (L264) | 4.4 `[radio.sx126x]` (SX126x station only; unknown keys are an error) (L294) | 4.5 `[radio.sensitivity_measured]` (open table, empty by default) (L307) | 4.6 `[link]` (L313) | 4.7 `[sdls]` (L320) | 4.8 `[cfdp]` (L332) | 4.9 `[spectral]` and `[spectral.classifier]` (L341) | 4.10 `[science]` (L359) | 4.11 `[tdm]` (L367) | 4.12 `[linkmgr]` (L373) | 4.13 `[interop]` (L385) | 4.14 `[gds]` (L393) | 4.15 `[commanding]` (L411)

#### `docs/16_Developer_Guide.md` - 584 lines, 4958 words
H2: 1 What you need (L13) | 2 Repository map (L43) | 3 How the pieces connect (L123) | 4 End-to-end checklists (L155) | 5 Coding standard (L300) | 6 Running every check locally (L359) | 7 Continuous integration (L483) | 8 Debugging aids (L500) | 9 Release and packaging (`tools/package_apps.py`) (L515) | 10 Updating a bundled library (L580)
H3: 4.1 Add a field to an existing packet (L160) | 4.2 Add a new packet (APID) (L183) | 4.3 Add a telecommand (L206) | 4.4 Add an event report (EVR) (L231) | 4.5 Add a sensor (instrument) (L251) | 4.6 Add a configuration key (L273) | 5.1 Flight C++ (MCU) (L302) | 5.2 Python (ground, EGSE, tools) (L320) | 5.3 Repository rules (L336) | 5.4 Citation tags in comments (L346) | 6.1 Host tests (flight code on the PC) (L363) | 6.2 Firmware compile check and the harnesses (L391) | 6.3 Ground test suite, lint and types (L408) | 6.4 Real builds (the App Lab toolchain) (L429) | 6.5 Workflow lint and generated documents (L460) | 6.6 The gate before a commit (L468)

#### `docs/17_Security_Model.md` - 400 lines, 7291 words
H2: 1 Summary of the controls (L20) | 2 Assets (L36) | 3 Trust boundaries (L50) | 4 Threats and mitigations (L83) | 5 Choosing an SDLS service (L120) | 6 Nonce safety - the one rule that must never break (L143) | 7 Procedures (L181) | 8 Web UI access rules (operator view) (L365) | 9 Residual risks (L380)
H3: 7.1 Generating and installing a key (after importing the Apps) (L183) | 7.2 Storing keys and backing up counter files (L226) | 7.3 Rotating the key (planned) (L241) | 7.4 A wrong or missing key on one end (L266) | 7.5 Key compromise (the key, a key file or a board left your control) (L275) | 7.6 A counter file was lost, is corrupt, or went backwards (L295)

#### `docs/18_Data_Products.md` - 647 lines, 5668 words
H2: 1 Overview (L23) | 2 Where the archive is (L40) | 3 Archive layout (L70) | 4 Raw CADU files (L93) | 5 Event log: `YYYYMMDD/events.log` (L132) | 6 The telemetry database: `gds.sqlite` (L158) | 7 Retention, free-space guard and disk budget (L269) | 8 CSV export (L315) | 9 Loading the data in Python (L381) | 10 Spacecraft EGSE products (UNO Q) (L454) | 11 CCSDS Tracking Data Message (TDM) (L473) | 12 XTCE mission database (L545) | 13 Replay (L567) | 14 CFDP received files (L613) | 15 Other files in the archive (L636)
H3: 4.1 Ground: `YYYYMMDD/cadu.bin` (L95) | 4.2 Spacecraft EGSE: `cadu_YYYYMMDD.bin` (L111) | 4.3 Reading CADUs in Python (L118) | 6.1 Engine (L160) | 6.2 `frames` - one row per received codeblock (L177) | 6.3 `packets` - one row per extracted Space Packet (L192) | 6.4 `params` - one row per decoded numeric field (L209) | 6.5 `events` and `commands` (L227) | 6.6 `rx_stats` - the 1 Hz receiver time series (data logger) (L234) | 6.7 Indexes (L260) | 7.1 Retention (`[gds] archive_keep_days`, `archive_max_gb`) (L271) | 7.2 Free-space guard (`[gds] archive_min_free_mb`) (L290) | 7.3 How much space (estimate) (L298) | 8.1 Running an export (L317) | 8.2 Files and format (L339) | 8.3 Opening CSV files in a spreadsheet (L368) | 9.1 Standard library (`sqlite3`, read-only) (L385) | 9.2 pandas (L411) | 9.3 Decoding a stored packet again (L434)

#### `docs/19_Glossary.md` - 320 lines, 4667 words
H2: Units and link quantities (L309)
H3: A (L14) | B (L35) | C (L50) | D (L75) | E (L88) | F (L103) | G (L121) | H (L129) | I (L142) | K (L152) | L (L159) | M (L171) | N (L184) | O (L195) | P (L203) | Q (L217) | R (L223) | S (L239) | T (L261) | U (L275) | V (L283) | W (L291) | X (L301)

#### `docs/20_Known_Limitations.md` - 261 lines, 8034 words
H2: 1 Values not yet verified on hardware (every "VERIFY" in the code and the documents) (L27) | 2 Not fully offline (installation) (L133) | 3 Limits by design (L148) | 4 Science limits (what a hobby-grade instrument set really gives) (L223) | 5 Regulatory and operational items (L236) | 6 Corrections to earlier figures (code wins) (L247)
H3: 1.1 Radio (L29) | 1.2 Spacecraft computer and platform (L57) | 1.3 Ground-station platform (Arduino App Lab, VENTUNO Q) (L73) | 1.4 Instruments (L89) | 1.5 Standards and data products (L104) | 1.6 Items other documents mark VERIFY (L112) | 3.1 Protocols and commanding (L150) | 3.2 Radio and link (L168) | 3.3 Spacecraft software (L182) | 3.4 Ground station (L197) | 3.5 Bundled third-party libraries (residual risks) (L209)

#### `docs/21_Test_Records.md` - 668 lines, 7471 words
H2: How to use these templates (L10) | Index (L33) | SES - Session header (L53) | PWR - Power draw and supply voltages (L80) | RSSI - Received power against attenuation and distance (L160) | RNG - Range test, one site pair (L213) | MAG - Magnetometer self-test, noise and calibration (L255) | TMP - Temperature test (L341) | TXP - Transmit power per power code (L384) | SENS - Receiver sensitivity (L418) | IDENT - Station identification on air (L447) | WDG - Hardware watchdog (L510) | Completed records (L565)
H3: PWR-1 Spacecraft (L101) | PWR-2 Ground station (L139) | RSSI-1 Bench calibration (known attenuation) (L166) | RSSI-2 Field, received power against distance (L185) | MAG-1 Self-test (L273) | MAG-2 Noise and cleanliness at rest (L283) | MAG-3 Calibration (MAGCAL) (L295) | MAG-4 Nicla Sense ME magnetometer scale (T-SC-06) (L321) | 2026-10-12-A - First bench run of the flight App (L567) | 2026-10-13-A - First downlink received by the ground station (L623)

#### `docs/22_Bundled_Libraries.md` - 491 lines, 5715 words
H2: 1 Summary (L24) | 2 Why bundle a patched copy instead of downloading the library (L47) | 3 RadioLib 7.8.1 (L63) | 4 Arduino_RouterBridge 0.4.3 (L152) | 5 The tools: fetch, verify, patch, check (L240) | 6 How the build uses the bundled copy (L314) | 7 Verification (L347) | 8 Continuous integration (L372) | 9 Packaging (App Lab zips) (L410) | 10 Moving to a newer upstream release (L421) | 11 Licences (L474) | 12 Related documents (L484)
H3: 3.1 What it does in this project (L65) | 3.2 Hunk 1 - `src/BuildOpt.h`: a platform entry for the VENTUNO Q (audit RL-01) (L78) | 3.3 Hunk 2 - `src/BuildOptUser.h`: no heap per SPI transfer, unused modules excluded (audit RL-02, RL-03) (L93) | 3.4 Defects that are NOT patched - handled in our drivers (L111) | 3.5 Guards in our code (L133) | 4.1 What it does in this project (L154) | 4.2 Hunk 1 - `src/bridge.h`: bridge thread stack 500 -> 1536 octets (L175) | 4.3 Hunk 2 - `src/bridge.h`: `update()` never executes `"__safe__"` methods (L190) | 4.4 Defence in depth: the RPC mailbox stays (L209) | 5.1 Commands (L248) | 5.2 The exact-hunk patch applier (L265) | 5.3 The pristine digest (L276) | 5.4 PROVENANCE.txt (L293)

#### `docs/FAQ.md` - 366 lines, 4048 words
H2: General (L16) | Radio and range (L46) | Data path (L110) | Security and rules (L203) | Hardware (L229) | Software (L308)
H3: What is this project? (L18) | Is it really "deep space"? (L27) | Has it been tested on real hardware? (L34) | How far will it reach? (L48) | What about my 35 dBi antennas? (L67) | Which spreading factor / profile should I use? (L74) | How do I switch to the 5 W (37 dBm) module? (L97) | How does complex telemetry travel over a simple radio? (L112) | How big are the packets and frames? (L134) | What is Rice compression? (L157) | Where does the signal processing happen? (L169) | What commands can I send? (L181) | What is SDLS, in plain words? (L205) | Is encryption allowed? Which mode should I use? (L217) | Why does the ground radio need a USB-UART adapter? (L231) | Why is the spectrometer page empty? (L241) | Which callsign do I use before I have a licence? (L251) | Why do four sensors on one I2C bus not clash? (L258) | What happens if it gets hot? (L267) | What can the AS7265X really measure? (L278) | How busy are the Linux processors? (L288) | Where is my data stored? (L297) | What are the tests, in plain words? (L310) | What does CI do? (L330) | What coding standard does the code follow? (L338) | Why are RadioLib and Arduino_RouterBridge bundled and patched? (L348) | What is not implemented? (L359)

#### `docs/Project_Summary.md` - 72 lines, 764 words
H2: What it is (L7) | How it works (L19) | How it is verified (L38) | Status (L58)

#### `vgq1-spacecraft/README.md` - 173 lines, 2613 words
H2: 1. What runs where (L14) | 2. Installing and running (L30) | 3. Where the data lives (L88) | 4. Configuration (L105) | 5. Troubleshooting (L120) | 6. Files (L142) | 7. More documentation (L163)
H3: 2.1 Import the App (L32) | 2.2 Generate the link key (once, for both Apps) (L38) | 2.3 First start (L55) | 2.4 See that it works (L64) | 2.5 Updating to a new version without losing the counters (L76)

#### `dssq-groundstation/README.md` - 223 lines, 3495 words
H2: 1. What runs where (L14) | 2. Installing and running (L32) | 3. Where the data lives (L110) | 4. Troubleshooting (L128) | 5. Configuration (L153) | 6. Host mode (fallback and always-on operation) (L166) | 7. Files (L190) | 8. More documentation (L214)
H3: 2.1 Import the App (L34) | 2.2 Generate the link key (once, for both Apps) (L40) | 2.3 First start (L58) | 2.4 Open the dashboard (L79) | 2.5 See that it works (L86) | 2.6 Updating to a new version without losing the archive or the counters (L100)

