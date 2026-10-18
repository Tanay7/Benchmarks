# R1 - Ground station port inventory: VENTUNO Q -> Arduino UNO Q 4 GB (ABX00173) + USB-C hub

Branch inspected: `/home/user/Benchmarks`, `claude/unoq-ground-station` (= v1 head `13a60c5`), read only.
Every fact carries a citation. "not found" = searched in the listed sources without result.

## 0. Abbreviations used for citations

| Tag | Path |
|---|---|
| `SK` | `dssq-groundstation/sketch/sketch.ino` (station MCU sketch, 1474 lines) |
| `SY` | `dssq-groundstation/sketch/sketch.yaml` |
| `CFG` | `vgq1-spacecraft/sketch/src/config.h` (flight pin choices) |
| `OVL` | `/tmp/claude-0/-home-user-Benchmarks/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/refs/ArduinoCore-zephyr/variants/arduino_uno_q_stm32u585xx/arduino_uno_q_stm32u585xx.overlay` |
| `VOVL` | same refs tree, `variants/arduino_ventuno_q_stm32h5f5xx/arduino_ventuno_q_stm32h5f5xx.overlay` |
| `VH` / `VVH` | UNO Q / VENTUNO Q `variant.h` in those two variant folders |
| `UCONF` | `variants/arduino_uno_q_stm32u585xx/arduino_uno_q_stm32u585xx.conf` |
| `DTS` | `/tmp/claude-0/-home-user-Benchmarks/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/refs/zephyr/boards/arduino/uno_q/arduino_uno_q-common.dtsi` (+ `arduino_uno_q.dts`) |
| `BT` | `refs/ArduinoCore-zephyr/boards.txt` |
| `DS` | `/tmp/claude-0/-home-user/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/ds/4c60b196-ABX00162-datasheet.txt` (UNO Q user manual, SKU ABX00162-ABX00173, "Modified 17/06/2026"); `DS §x` = its section, `DS:n` = text line |
| `T33D` | `/tmp/claude-0/-home-user/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/ds/b8c54790-E22-400T33D-V2.1_UserManual_CN_v1.05.txt` (pdftotext of the upload) |
| `E51` | `.../scratchpad/ds/5b3a258d-E51-TTL-500_Datasheet_EN_v1.0.txt` (describes the E15-USB-T2) |
| `TBH` | `.../scratchpad/ds/6e826cce-E22-400TBH-02TestBoarddatasheet_EN_v1.0_2.txt` |
| `VDS` | `.../scratchpad/ds/ventuno.txt` (pdftotext of ABX00181 datasheet, comparison only) |
| `CKB` | `.../scratchpad/ds/cardkb.txt` (docx2txt of `b41d1005-Cardkb_mini_keyboard.docx`) |

---

## 1. Executive summary (what must be decided / changed)

1. **The station sketch ports to the UNO Q with no functional rewrite.** Every pin it uses maps to a
   plain UNO Q GPIO; `Wire` on the UNO Q is again the header SDA/SCL pair (I2C2, D20 = PB11 SDA,
   D21 = PB10 SCL: `OVL:393` `i2cs = <&i2c2>, <&i2c4>, <&i2c3>`, `DTS:119-126`, `VH:23-24`,
   `DS §9.6` pins 17/18 = `DS:2181-2320`), and `SPI` is again SPI2 on D11/D12/D13 with NSS D10
   (`OVL:394`, `DTS:138-143`). Change list in section 3.5; pin-plan options in 3.4.
2. **The station must now also build and LOAD on arduino:zephyr 0.90.0** (the UNO Q App Lab
   stable core; no core before 1.0.0 had the VENTUNO Q, `.github/workflows/ci.yml:302`,
   `docs/08_Verification_and_Test.md:172`). Required: replace `k_current_get()` (`SK:1375`,
   `SK:1425`) with `k_sched_current_thread_query()` - on 0.90.0 `k_current_get()` references
   `_ZTH13z_tls_current`, which the 0.90.0 loader refuses (`vgq1-spacecraft/sketch/src/fsw/rpc_mailbox.h:168-175`,
   `docs/08_Verification_and_Test.md:200-208`). The `__errno` shim is NOT strictly required
   (no errno-setting libm call is reachable, section 4.2) but is cheap insurance; the 64-octet
   serial rings only make long `Bridge.notify()` calls block (section 4.1). CI must add the
   station (both radio options) to the 0.90.0 legacy builds + loader check (section 6.1).
3. **Python ground data system is board-agnostic.** Only docstrings / comments / two operator
   messages name the VENTUNO Q (`gds.py:1300-1302`) and one dashboard label
   (`dashboard.html:342`). The archive defaults (`archive_max_gb` 16 GB, sized for a 64 GB eMMC)
   should be revisited for the 32 GB eMMC of the ABX00173 (`DS:438`, `DS:473`).
4. **Web UI legitimacy bug found (requirement 7):** the sketch's hello line hard-codes the RCU
   field to 1 (`SK:1466` `"H|%s|1|%d"`), so the COMMANDING tab row "Radio Control Unit:
   present" (`dashboard.html:2014`, documented as "the MCU drives the E22 M0 / M1 and reads AUX"
   in `docs/13_Web_UI_Reference.md:2873`) is not a measurement. Fix in section 5.3.
5. **Build/packaging:** `SY:23` FQBN `arduino:zephyr:ventunoq` -> `arduino:zephyr:unoq`;
   `tools/package_apps.py:161` AppSpec board/zip name; `dist/dssq-groundstation-ventunoq.zip`
   -> `dist/dssq-groundstation-unoq.zip`; CI, `check_firmware.sh`, station harness `run.sh`
   board macros `-DARDUINO_VENTUNO_Q` -> `-DARDUINO_UNO_Q`. RadioLib patch hunk 1 (a VENTUNO Q
   platform entry, `tools/radiolib-7.8.1.patch:18-34`, `:91-95`) becomes dead: decide keep vs
   remove (section 4.4).
6. **USB / power / container:** the ground E22 reaches Linux through the hub as `/dev/ttyUSBn`
   (CP2102 on the E15-USB-T2, `E51:508`, `E51:857`; CH340X on the TBH-02, `TBH:311`). Nothing
   in code depends on a VENTUNO Q USB port. The power picture changes completely: the UNO Q
   takes only 5 V / 3 A over USB-C PD (`DS:685`, `DS §3.1`) and has no current-limited +5 V
   rail; the E22-400T33D draws 850-1200 mA when transmitting (`T33D:210-212`) and needs
   >= 4.7 V for full power (`T33D:206`). App-container serial access was proven only on the
   VENTUNO Q (`docs/20_Known_Limitations.md:78`, V-31): VERIFY on the UNO Q.
7. **Operational change with the single USB-C port:** the ground UNO Q can no longer be
   USB-connected to the PC while the hub is plugged in; App Lab must reach it over the network
   after a first USB setup (`DS:2534-2541`, clearer in the Portuguese text `DS:7612-7620`).

---

## 2. Target hardware facts (UNO Q, both SKUs) that the port depends on

| Fact | Value | Citation |
|---|---|---|
| SKUs | ABX00162 = 2 GB RAM / 16 GB eMMC; **ABX00173 = 4 GB RAM / 32 GB eMMC** | `DS §2.1`, `DS:436-438`, `DS:473-474` |
| MPU / OS | Qualcomm Dragonwing QRB2210, 4 x Cortex-A53 @ 2.0 GHz, **Debian** Linux | `DS:19`, `DS:455`, `DS:1067` (VENTUNO Q was QCS8275 + Ubuntu: `VDS:19-21`, `VDS:212-218`) |
| MCU | STM32U585, Cortex-M33 up to 160 MHz, 2 MB flash, 786 kB SRAM | `DS:458-470` |
| Sketch limits (arduino-cli) | 786 432 B flash, 262 144 B RAM (`unoq.upload.maximum_size` / `maximum_data_size`) | `docs/08_Verification_and_Test.md:177`, `docs/01_System_Design.md:767` |
| Sketch partition | `user_sketch` 768 KiB at 0x100000 | `OVL:91-93` |
| Station sketch size (on VENTUNO Q) | E22 build 100 828 B / 40 716 B RAM; SX1268 build 207 692 B / 89 216 B RAM -> fits the UNO Q (26 % flash, 34 % RAM for the SX build) | `docs/08_Verification_and_Test.md:187-189` |
| Router link MCU<->Linux | LPUART1 (`arduino,router-serial = <&lpuart1>`), 115200 baud, PG7/PG8 + RTS/CTS | `OVL:390`, `DTS:105-110` (VENTUNO Q: USB CDC-ACM, `VOVL:316`) |
| Zephyr console | USART1 on D0/D1 (PB7 RX / PB6 TX), not deferred, carries boot / kernel logs | `arduino_uno_q.dts:17-18`, `OVL:252-255`, `DS:2181ff` (JDIGITAL 1/2) |
| Serial objects | `serials = <&usart1>, <&lpuart1>, <&usart3>` -> Serial1 / (router) / Serial3; `Serial` = RouterBridge Monitor | `OVL:390-391`, `VH:8-16`, `CFG:50-64` |
| I2C objects | `Wire` = I2C2 (PB10 SCL, PB11 SDA = header D21/D20 "SCL"/"SDA"), `Wire1` = I2C4 (PD12 SCL / PD13 SDA = Qwiic), `Wire2` = I2C3 (PC0 SCL / PC1 SDA = A5/A4) | `OVL:393`, `DTS:119-136`, `OVL:7-38`, `DS §9.3` (`DS:2032ff`), `CFG:178-185` |
| SPI objects | `SPI` = SPI2 (PB13 SCK, PB14 MISO, PB15 MOSI, PB9 NSS), `SPI1` = SPI3 | `OVL:394`, `DTS:138-149`, `VH:19-22` |
| D20/D21 | D20 = PB11 = I2C2_SDA, D21 = PB10 = I2C2_SCL; same pins are USART3 TX PB10 / RX PB11 | `DS §9.6` pins 17-18, `OVL:257-267`, `OVL:299-300`, `VH:23-24` |
| 5 V tolerance | JDIGITAL mostly FT (5 V-tolerant as inputs); **D3 = PB0 is TT, 3.6 V max, not 5 V tolerant in any mode**; A0-A5 not 5 V tolerant (analog); A4/A5 as I2C3: pull-ups to 3.3 V only | `DS:2310-2321`, `DS:1339-1344`, `DS:2441` |
| Qwiic | JST 4-pin: GND, +3V3 OUT, SDA PD13, SCL PD12 (I2C4), 3.3 V | `DS §9.3` (`DS:2032ff`) |
| Power inputs | USB-C VBUS 5 V up to 3 A (PD: requests only the 5 V / 3 A contract); VIN 7-24 V on JANALOG 8 / JMEDIA; "5 V pin" on JANALOG (5 V up to 3 A); diode-OR into 5V_SYS | `DS §3.1` (`DS:640-690`), `DS §4.3` (`DS:884-888`) |
| JANALOG pin 5 | "+5V USB VBUS", 5V_USB_VBUS, "5 V supply (pass-through)", power only | `DS:2360` |
| USB host | "A protected P-channel MOSFET (Q2801) can source USB VBUS from 5V_SYS when the board operates as a USB host/OTG" | `DS:888` |
| Hub / dongle | Related products: "USB-C dongle with external power delivery capabilities"; for SBC-mode peripherals "use a USB-C dongle with external power delivery capabilities" | `DS:606-609`, `DS:2534-2541`, `DS:7618-7620` |
| USB-C sharing | One USB-C port (JUSB1), USB 3.1 role switching + DP Alt-Mode; "When DisplayPort Alt-Mode is active, USB data speed is reduced" | `DS:510-520`, `DS:2460`, `DS:2542` |
| USB while App runs | "While an App is bound and running, USB interfaces may be occupied by the system ... To use external CLI tools over USB, stop the App or disconnect the board." | `DS:2572` |
| PSU guidance | "Use a 5 V / 3 A USB-C source and cable, or power from the 5 V or VIN pins" | `DS:2554` |
| Board power consumption figure | **not found** in DS | - |
| Board-level pull-ups on D20/D21 | **not found** in DS | - |
| On-board MCU LEDs | LED3 / LED4 RGB on PH10-PH15, driven by the STM32U585, **active-low** | `DS:1040-1047`, `DTS:19-51`, `VH:37-44`, `OVL:332-337` |
| LED matrix | 8 x 13, PF0-PF10 + TIM17; shows the boot logo 20-30 s; "Accessing the matrix before startup completes may interfere with MCU operation" | `DS:1051`, `DTS:276-304`, `OVL:339-349` |
| User / App folder | user `arduino`, Apps in `/home/arduino/ArduinoApps/<folder>` | `vgq1-spacecraft/README.md:36` |
| Debian release / host Python version | **not found** in DS (only "Debian OS (upstream support)", `DS:455`) | - |

---

## 3. (a) Station sketch (`SK`) and `SY`

### 3.1 What the sketch exposes to Linux (unchanged by the port)

| Direction | Name | Registered at | Signature / meaning |
|---|---|---|---|
| Linux -> MCU | `fp_line` | `SK:1438` (provide_safe) | String `S|aos|alarm|uplink|beacon` / `Q|id` (`SK:46-47`, `SK:179-208`) |
| Linux -> MCU | `rcu_mode` | `SK:1439` | int 0..3 -> AUX (1 ready) (`SK:219-226`) |
| MCU -> Linux | `fp_in` | `SK:163` notify | `K|text`, `CMD|text`, `CONFIRM|id`, `H|fw|rcu|kb` every 5 s (`SK:1463-1468`) |
| Linux -> MCU (SX build) | `rf_tx`, `rf_cfg`, `rf_foff`, `rf_noise`, `rf_cad`, `rf_wake` | `SK:1444-1449` | `SK:51-62`, bodies `SK:1180-1272` |
| MCU -> Linux (SX build) | `rf_rx`, `rf_evt` | `SK:1006`, `SK:677` | `SK:63-68` |

Python side of the same interface: `dssq-groundstation/python/dssq/display/frontpanel.py:22-29`,
`radio/sx126x.py:9-60`. The interface is MCU-board independent (MessagePack-RPC over the router).
Mailbox to the loop thread: `SK:1299-1421`; bundled patched RouterBridge runs provide_safe
handlers only in `update_safe()` (`dssq-groundstation/sketch/libraries/Arduino_RouterBridge/src/bridge.h:245-272`, `:301ff`).

Libraries: `Arduino.h`, `Arduino_RouterBridge.h`, `Wire.h` (`SK:74-76`); SX build adds `SPI.h`,
`RadioLib.h` (`SK:100-101`), checked to be RadioLib 7.8.1 + `RADIOLIB_STATIC_ONLY` (`SK:102-107`).
No display library; no Serial use; no LED matrix; no `LED_BUILTIN`.

### 3.2 Pin / peripheral map, current (VENTUNO Q) -> UNO Q

UNO Q MCU pins: `OVL:276-300` (digital-pin-gpios index = Arduino pin number); header positions
`DS §9.6` (JDIGITAL) / `DS §9.7` (JANALOG). VENTUNO Q MCU pins: `VOVL:197-221`.

| Function (symbol, line) | Arduino pin | VENTUNO Q MCU pin | **UNO Q MCU pin** | UNO Q header | Same Arduino pin on the spacecraft UNO Q (`CFG`) | Notes |
|---|---|---|---|---|---|---|
| RCU E22 M0 (`PIN_RCU_M0`, `SK:127`) | D2 | PB0 | **PB3** (`OVL:278`) | JDIGITAL 3 | E22 M0 (`CFG:90`) | FT pin; output 3.3 V |
| RCU E22 M1 (`PIN_RCU_M1`, `SK:127`) | D3 | PB1 | **PB0** (`OVL:279`) | JDIGITAL 4 | E22 M1 (`CFG:91`) | **TT, 3.6 V max** (`DS:2321`): the documented 10 kOhm pull-up must go to 3.3 V, never 5 V |
| RCU E22 AUX in, pull-up (`PIN_RCU_AUX`, `SK:127`, `SK:1431`) | D4 | PB6 | **PA12** (`OVL:280`) | JDIGITAL 5 | SX126x BUSY (`CFG:115`); flight E22 AUX is **D7** (`CFG:92`) | PA12 = FDCAN1_TX / USB_OTG_FS_DP; FDCAN1 deferred-init (`OVL:235-242`), USB OTG disabled (`DTS:159-163`) -> free |
| AOS lamp (`PIN_LED_AOS`, `SK:129`) | D5 | PB5 | **PA11** (`OVL:281`) | JDIGITAL 6 | SX126x DIO1 (`CFG:116`) | PA11 = FDCAN1_RX / USB DM, same remark |
| ALARM lamp (`PIN_LED_ALARM`, `SK:129`) | D6 | PB2 | **PB1** (`OVL:282`) | JDIGITAL 7 | SX126x NRST (`CFG:117`) | - |
| UPLINK lamp (`PIN_LED_UPLINK`, `SK:129`) | D7 | PB3 | **PB2** (`OVL:283`) | JDIGITAL 8 | **E22 AUX (`CFG:92`)** | - |
| Buzzer NPN base (`PIN_BUZZER`, `SK:129`) | A0 (= D14) | PA4 | **PA4** (`OVL:292`) | JANALOG 9 | **T37S model strap, jumper to GND (`CFG:107`)** | PA4 = DAC1_OUT1 in the board DTS (`DTS:241-245`); the DAC driver only muxes at init, `pinMode()` makes it a GPIO (`CFG:100-105`). Not 5 V tolerant (`DS:2441`) |
| CardKB SDA (`Wire`, `SK:1440`) | SDA = D20 | PH12 (I2C4) | **PB11 (I2C2_SDA)** (`DTS:121`, `VH:23`) | JDIGITAL 17 | E22 UART RX `Serial3` (`CFG:54-63`) | FT (5 V tolerant input, `DS:2310`); shared with USART3_RX (`OVL:257-267`) |
| CardKB SCL (`Wire`) | SCL = D21 | PH11 | **PB10 (I2C2_SCL)** (`DTS:121`, `VH:24`) | JDIGITAL 18 | E22 UART TX `Serial3` | shared with USART3_TX |
| SX SCK / MISO / MOSI (`SPI`, SX build, `SK:355-356`) | D13 / D12 / D11 | PB13 / PB14 / PB15 | **PB13 / PB14 / PB15** (`OVL:287-289`, `DTS:140`) | JDIGITAL 14 / 13 / 12 | same (`docs/03_Hardware_Integration.md:352-356`) | SPI2 deferred-init (`OVL:244-246`); `SK:1275-1279` (SPI.begin applies the default mux incl. D10 = SPI2_NSS) stays true: UNO Q DTS lists `spi2_nss_pb9` (`DTS:140`) |
| SX NSS (`PIN_SX_NSS`, `SK:407`) | D10 | PB12 | **PB9** (`OVL:286`) | JDIGITAL 11 | SX NSS (`CFG:114`) | - |
| SX BUSY (`PIN_SX_BUSY`, `SK:407`) | D8 | PB4 | **PB4** (`OVL:284`) | JDIGITAL 9 | SX RXEN (`CFG:118`) | - |
| SX DIO1 polled (`PIN_SX_DIO1`, `SK:407`) | D9 | PB7 | **PB8** (`OVL:285`) | JDIGITAL 10 | SX TXEN (`CFG:119`) | - |
| SX NRST (`PIN_SX_NRST`, `SK:408`) | A1 (D15) | PA5 | **PA5** (`OVL:293`) | JANALOG 10 | - | DAC1_OUT2 at init (`DTS:241-245`); GPIO via pinMode |
| SX RXEN (`PIN_SX_RXEN`, `SK:408`) | A2 (D16) | PE12 | **PA6** (`OVL:294`) | JANALOG 11 | - | ADC1 deferred (`OVL:176-183`) |
| SX TXEN (`PIN_SX_TXEN`, `SK:408`) | A3 (D17) | PE13 | **PA7** (`OVL:295`) | JANALOG 12 | - | - |
| 3.3 V out | - | JANALOG 4 (+3V3_LIMITED) | JANALOG 4 `+3V3 OUT` PWR_3P3V | JANALOG 4 | - | `DS:2360` table |
| 5 V out | - | JANALOG 5 (+5V_LIMITED, 2.8 A limit) | JANALOG 5 `+5V USB VBUS` **pass-through, no limiter documented** | JANALOG 5 | - | `DS:2360`; old: `docs/03_Hardware_Integration.md:522` |

Not used by the station on either board: D0/D1, A4/A5, Qwiic, JSPI, JMISC, LED matrix, on-board RGB LEDs.

### 3.3 Conflict / hazard analysis on the UNO Q

| Item | Finding | Citation | Action |
|---|---|---|---|
| A0 | On the spacecraft UNO Q, A0 is the T37S model strap (jumper to GND). On the ground UNO Q the same header pin is the buzzer driver output. With two identical boards the owner can put the strap on the wrong board; a jumper there grounds the buzzer output (through the 1 kOhm base resistor of the documented driver, `docs/03_Hardware_Integration.md:623`, so no damage, but a silent buzzer). Today's warning texts say "on the VENTUNO Q, A0 drives the buzzer" | `CFG:94-107`, `SK:129`, `dssq-groundstation/README.md:28`, `vgq1-spacecraft/README.md:26,116`, `README.md:178` | Either keep A0 and rewrite every warning as "the A0 strap belongs to the spacecraft UNO Q only", or move the buzzer to a pin with no spacecraft meaning (options in 3.4) |
| D0/D1 | USART1 = Zephyr console: boot banner / kernel logs come out there | `arduino_uno_q.dts:17-18`, `OVL:252-255`, `CFG:50-56` | Keep unused (the sketch never touches them) |
| SDA/SCL = USART3 | On the spacecraft these pins are the E22 UART (Serial3) and `Wire.begin()` is forbidden; on the ground they are the CardKB I2C bus (`Wire.begin()`, `SK:1440`) and `Serial3` must never be begun | `CFG:54-56`, `OVL:257-267`, `SK:1440-1441` | Document the opposite roles explicitly (same pins, different job per board) |
| LED matrix | No pin conflict (PF0-PF10); sketch does not use it | `DTS:276-304` | Optional: a station status display (precedent `vgq1-spacecraft/sketch/src/fsw/status_display.h:1-14`); init only after `Bridge.begin()` (Linux booted, `DS:1051`) |
| Qwiic | Free on the ground (I2C4 = `Wire1`, 3.3 V supply only) | `DS §9.3`, `OVL:393` | Optional second CardKB position (Grove HY2.0 -> JST-SH adapter; would need a `Wire1` probe like the flight's AS7265X auto-detect, `CFG:182-185`) |
| D3 = PB0 TT | M1 line: never pulled to 5 V | `DS:2321` | Docs: pull-up to 3.3 V only; check that the TBH-02 / E15 side never drives 5 V into M1 (VERIFY, T37S report) |
| PA11/PA12 | USB_OTG_FS DM/DP and FDCAN1; OTG disabled, CAN deferred | `DTS:159-163`, `OVL:235-242` | none |
| PA4/PA5 | DAC1 outputs at init | `DTS:241-245`, `CFG:100-105` | `pinMode(OUTPUT)` overrides (flight precedent for PA4 input) |
| CardKB levels | CardKB is specified for 5 V (Grove red = 5 V, ATMega8A); I2C levels and pull-ups not published | `CKB` PinMap (lines ~100-110), `docs/03_Hardware_Integration.md:630-646` | UNO Q D20/D21 are FT (5 V tolerant inputs, `DS:2310`): a CardKB on 5 V no longer strictly needs the BSS138 for MCU protection, but the high level seen by the ATMega8A with 3.3 V pull-ups is not specified -> keep options A/B, VERIFY |
| Board pull-ups on D20/D21 | not found | - | keep "measure, fit 4.7 kOhm to 3.3 V if open" (`docs/03_Hardware_Integration.md:640-644`) |

### 3.4 Pin-plan options (decision for the v4 contract)

* **P - preserve** (minimal change): keep every Arduino pin number (table 3.2). Valid on the UNO Q,
  no conflict inside the station. Only comments change; harness and docs keep Arduino pin names.
  Drawbacks: RCU AUX on D4 differs from the spacecraft's E22 AUX on D7 (`CFG:92`), and A0 means
  "buzzer" on one UNO Q and "T37S strap" on the other.
* **H - align the E22 control pins with the flight (recommended)**: M0 D2, M1 D3, **AUX D7**
  (identical to `CFG:90-92`, so one TBH-02 / E22 wiring table serves both boards - helps
  requirement 2); lamps AOS **D4**, ALARM **D5**, UPLINK **D6**; SX126x pins unchanged
  (D8/D9/D10/A1/A2/A3). Buzzer either stays A0 (with the warning) or moves to **A4 (PC1)** or
  **A5 (PC0)** (unused at the ground; 3.3 V GPIO; `OVL:296-297`), which removes the A0 confusion.
  Code impact: 4-5 constants (`SK:127`, `SK:129`); harness uses the symbols only
  (`tools/host_test/station/test_e22.cpp:47,87`, `test_sx.cpp:54-89`).
* **M - mirror the flight completely** (also SX126x: NSS D10, BUSY D4, DIO1 D5, NRST D6, RXEN D8,
  TXEN D9 per `CFG:114-119`): forces the lamps onto A1-A3 and the buzzer to A0/A4/A5. More churn,
  no extra benefit beyond H for the default E22 build.
* Independent option: also mirror AOS / ALARM / UPLINK on the on-board MCU RGB LEDs LED3/LED4
  (active-low, `DS:1047`, `VH:37-44`), so a station without external lamps still shows them; and/or
  a status page on the LED matrix.

### 3.5 Change list - `SK` (station sketch)

| Line(s) | Current text (abridged) | Required change |
|---|---|---|
| 3 | `//  VENTUNO Q MCU (STM32H5F5, Arduino core on Zephyr)` | `UNO Q MCU (STM32U585, Arduino core on Zephyr)` |
| 5-9 | `Split of work on the VENTUNO Q: Linux CPU (Dragonwing QCS8275) ...` | UNO Q: Linux MPU Qualcomm Dragonwing QRB2210 (4 x Cortex-A53, Debian) (`DS:19`); MCU STM32U585 |
| 12-19 | CardKB on `Wire` = JDIGITAL header SDA (PH12) / SCL (PH11); VENTUNO manual vs overlay D20/D21 discussion; "I2C4 peripheral fixes the roles"; level-shifter text; "The Qwiic socket is not used" | `Wire` = I2C2, header SDA = D20 = PB11, SCL = D21 = PB10 (`DTS:119-126`, `VH:23-24`, `DS §9.6`); delete the D20/D21 discrepancy remark (consistent on the UNO Q); add "same pins are USART3 (Serial3): never begin Serial3 on the ground; on the spacecraft the roles are reversed (`CFG:54-56`)"; keep the CardKB level text, add the FT note (`DS:2310`); Qwiic = `Wire1` (I2C4) unused (or optional, 3.4) |
| 30-31 | RCU: "drives the station module's M0/M1 and reads AUX" | add: optional on the E15-USB-T2 (`[gds] rcu = false` default, `station.toml:169-171`) |
| 84-90 | RadioLib note "(VENTUNO Q platform entry with the PinMode / PinStatus casts the Zephyr core needs; ...)" | the UNO Q has its own entry in stock RadioLib 7.8.1 (`.../libraries/RadioLib/src/BuildOpt.h:417-422`); describe the patch per the hunk-1 decision (4.4) |
| 108, 119 | `kFw = "DSSQ-IO 3.1 SX126x"` / `"DSSQ-IO 3.1"` | bump (e.g. `DSSQ-IO 4.0`) so the dashboard Firmware row (`dashboard.html:2014`) identifies the UNO Q build; update `docs/13_Web_UI_Reference.md:2871`. Hello buffer 48 chars (`SK:1465`) and GDS truncation 32 chars (`frontpanel.py:212`) allow it |
| 122-127 | RCU pin comment + `PIN_RCU_M0 = 2, PIN_RCU_M1 = 3, PIN_RCU_AUX = 4` | per 3.4 (H: AUX = 7); add UNO Q MCU pins PB3 / PB0 (TT, 3.3 V pull-up only) / PA12 or PB2 |
| 128-129 | `PIN_LED_AOS = 5, PIN_LED_ALARM = 6, PIN_LED_UPLINK = 7, PIN_BUZZER = A0` | per 3.4 (H: 4, 5, 6, A0 or A4/A5) |
| 350-360 | "Wiring (VENTUNO Q JDIGITAL / JANALOG headers ...): NSS D10 (PB12) ... DIO1 D9 (PB7) ... NRST A1 (PA5) ... RXEN A2 (PE12); TXEN A3 (PE13). No conflict with ... the CardKB bus (PH11/PH12)" | UNO Q MCU pins: NSS PB9, BUSY PB4, DIO1 PB8, NRST PA5, RXEN PA6, TXEN PA7 (`OVL:284-295`); CardKB bus PB10/PB11; module 5 V supply note: JANALOG 5 is the USB VBUS pass-through on the UNO Q (`DS:2360`) |
| 407-408 | `PIN_SX_NSS = 10, PIN_SX_BUSY = 8, PIN_SX_DIO1 = 9; PIN_SX_NRST = A1, PIN_SX_RXEN = A2, PIN_SX_TXEN = A3` | unchanged under options P/H (valid UNO Q pins) |
| 1275-1278 | SPI.begin() comment "D10 = SPI2_NSS included (core init_dev_apply_pinctrl)" | still true on the UNO Q (`DTS:140` `spi2_nss_pb9`); name PB9 |
| 1302-1306 | "UPDATE_THREAD_PRIORITY 5 - above the main thread's 14 in both Q cores' CONFIG_MAIN_THREAD_PRIORITY" | "in the UNO Q core (0.90.0 and >= 1.0.0)" (14 on 0.90.0: `docs/12_Design_Decisions.md:892`) |
| 1344 | `static k_tid_t g_loop_tid = nullptr; // the Arduino main thread` | unchanged type; see next row |
| **1375** | `if (k_current_get() == g_loop_tid)` | **`k_sched_current_thread_query()`** (0.90.0 loader: `_ZTH13z_tls_current` unresolved; `rpc_mailbox.h:168-175`, `docs/08_Verification_and_Test.md:200-208`) |
| **1425** | `g_loop_tid = k_current_get();` | **`k_sched_current_thread_query()`** |
| 1440 | `Wire.begin(); // header SDA/SCL (I2C4): the CardKB` | `(I2C2, D20/D21 = PB11/PB10)` |
| 1466 | `snprintf(h, sizeof h, "H|%s|1|%d", kFw, g_kb ? 1 : 0);` | the `1` is a constant -> see 5.3 (legitimate RCU field) |
| new | - | optional `__errno` weak shim (4.2) as a separate sketch-folder `.cpp` guarded like `vgq1-spacecraft/sketch/src/libc_compat.cpp:16` with a host-build macro that `tools/host_test/station/run.sh` defines |

Citation tags: `PB-6.4` is cited only in `SK` (checked with the regexes of `tests/test_citations.py:37-60`);
removing it from comments fails `test_every_row_of_appendix_b_is_still_cited_in_code` unless
`docs/12_Design_Decisions.md` appendix B is edited. New comments must not write "contract N" for a
v4 contract (the regex at `tests/test_citations.py:44-45` reads any "contract N" as a v3 tag).

### 3.6 Change list - `SY` and the bundled libraries

| Line | Current | Required |
|---|---|---|
| `SY:1` | `# VENTUNO Q MCU (STM32H5F5, Arduino core on Zephyr). FQBN: arduino:zephyr:ventunoq` | `# UNO Q MCU (STM32U585 ...). FQBN: arduino:zephyr:unoq` |
| `SY:23` | `fqbn: arduino:zephyr:ventunoq` | `fqbn: arduino:zephyr:unoq` (board id `unoq`, `BT:584-628`, `build.board=UNO_Q` -> `-DARDUINO_UNO_Q`, `BT:611`) |
| `SY:24-25` | `- platform: arduino:zephyr` (no version) | keep: App Lab uses the installed core (`ci.yml:176-178`); the station must then work on 0.90.0 and >= 1.0.0 (section 4) |
| `SY:26-28` | `- dir: libraries/RadioLib`, `- dir: libraries/Arduino_RouterBridge` | unchanged |
| `SY:4-20` | comments (bundled libs, fetch commands) | text OK; mention the UNO Q |

Bundled (`dssq-groundstation/sketch/libraries/`, git-ignored, `.gitignore:18`): **Arduino_RouterBridge
0.4.3** patched by `tools/arduino_routerbridge-0.4.3.patch` (bridge thread stack 1536, provide_safe
only on the loop thread; `PROVENANCE.txt` tag 0.4.3, patch-sha256 `b9e95dc6...`) and **RadioLib
7.8.1** patched by `tools/radiolib-7.8.1.patch` (`PROVENANCE.txt` patch-sha256 `e935551c...`).
Its dependencies **Arduino_RPClite 0.3.1 and MsgPack 0.4.2 come from the platform**
(`tools/arduino_routerbridge-0.4.3.patch:5-8`, `SY:15-16`). The UNO Q `variant.h` demands a
RouterBridge that provides `routerbridge_provides_serial.h` (`VH:8-16`): present in the bundled
copy (`.../Arduino_RouterBridge/src/routerbridge_provides_serial.h`). The same bundled copies build
and load on 0.90.0 for the flight (`ci.yml:322-332`, `docs/21_Test_Records.md:598`), so they are
compatible with the 0.90.0 platform's RPClite/MsgPack.

---

## 4. (b) arduino:zephyr core: 0.90.0 (UNO Q App Lab stable) vs >= 1.0.0

| Issue on 0.90.0 | Flight handling | Station today | Required for the UNO Q station |
|---|---|---|---|
| 64-octet Serial rings (`CONFIG_ARDUINO_API_SERIAL_BUFFER_SIZE` 64 before core commit 40abc1f7) | UART pump for the E22 (`drivers/uart_pump.h:1-25`) + `notify_room()` (`fsw/flight.cpp:2398-2411`); D-66 (`docs/12_Design_Decisions.md:885-892`) | No `Serial` use at all. Router link = LPUART1 ring (`OVL:390`); `Bridge.notify()` takes the write mutex and blocks in `ZephyrSerial::write()` until all but one ring-full is out (`bridge.h:297-306`) | No pump needed. Longest notifications: `rf_rx` up to 472 hex chars (~490 octets MsgPack, `docs/20_Known_Limitations.md:63`) -> ~43 ms at 11.5 octets/ms; `S|` status ~190 chars (`SK:704-717`) -> ~17 ms; E22 build only short `K|`/`H|` lines (< 80 chars). The bridge thread keeps reading under `read_mutex` (`bridge.h:245-272`), all station handlers are provide_safe so the bridge thread never needs `write_mutex`. Acceptable; document as a VERIFY (like V-68) |
| `__errno` undefined (newlib-nano libm wrappers `expf`, `logf`, `log10f`, `powf`, `sqrtf`) | weak shim `src/libc_compat.cpp:16-21` | `SK` calls only `lrintf` (`SK:1006`, SX build); RadioLib SX126x paths use `fabsf`, `floor`, `ceil` (`.../RadioLib/src/modules/SX126x/SX126x.cpp:1271,1287`, `SX126x_config.cpp:677-697`, `SX1262.cpp:182`, `SX1268.cpp:183`); none of these sets errno. No errno-wrapped call found (grep) | Not strictly needed; add the same weak shim as insurance (zero cost on >= 1.0.0, `libc_compat.cpp:11-14`). The decisive proof is the 0.90.0 build + `tools/llext_check.py` in CI (6.1) |
| C++ TLS init `_ZTH13z_tls_current` (no `-fno-extern-tls-init` before 1.0.0) | `RpcMailbox::current_thread()` = `k_sched_current_thread_query()` (`rpc_mailbox.h:168-175`) | **uses `k_current_get()` at `SK:1375`, `SK:1425`** | **Must change** (3.5). The 0.90.0 build would link but the loader refuses the sketch (`docs/08_Verification_and_Test.md:200-208`) |
| `arduino.app_utils.AppError` missing (app-bricks-py < 0.13.0) | EGSE imports it only when present (`vgq1-spacecraft/python/main.py:1233-1235`) | already tolerant: `frontpanel.py:90-94` | none |
| Main thread priority 14, bridge thread 5 | - | assumed in `SK:1302-1306` | unchanged (`docs/12_Design_Decisions.md:892`) |
| 0.90.0 variant overlay (i2cs order, serials) | flight works on 0.90.0 with Wire2 = A4/A5, Wire1 = Qwiic (record 2026-10-12-A) | - | the 0.90.0 overlay itself is **not in the refs** (refs = core main `b08a650`); VERIFY that `Wire` = I2C2 on 0.90.0 too |

### 4.1 Router link throughput
The VENTUNO Q router link was a USB CDC-ACM UART (`VOVL:316`); the UNO Q's is LPUART1 at
115200 baud (`OVL:390`, `DTS:105-110`). V-29 (`docs/20_Known_Limitations.md:70`) and V-22
(`docs/20_Known_Limitations.md:63`, station `rf_rx` "not yet run") now apply to the station.

### 4.2 libm / errno evidence
grep over `SK` and the compiled RadioLib parts (modules excluded by `BuildOptUser.h:28-50` not
counted): no `expf/logf/log10f/powf/sqrtf/pow/log/exp/sqrt` call; only `SX126x_LR_FHSS.cpp:131`
(a comment) and `ADSB.cpp` (excluded, `RADIOLIB_EXCLUDE_ADSB`, `BuildOptUser.h:50`).

### 4.3 Loader / LLEXT
`tools/llext_check.py` is board-generic (needs a baseline of the same variant,
`tools/llext_check.py:14-23,86`). Station on UNO Q: baseline `unoq` instead of `ventunoq`.

### 4.4 RadioLib patch hunk 1 (VENTUNO Q platform entry)
* Stock 7.8.1 already has `ARDUINO_ARCH_ZEPHYR && ARDUINO_UNO_Q` (`BuildOpt.h:417-422`); the patch
  adds a VENTUNO Q branch (`tools/radiolib-7.8.1.patch:18-34`, `:88-96`; bundled `BuildOpt.h:424-431`).
* With no VENTUNO Q, hunk 1 is dead code but still puts "VENTUNO Q" into BOTH App zips (flight and
  ground bundle the same patched copy).
* Removing it changes the patch file -> new `patch-sha256` in both `PROVENANCE.txt`
  (`tools/libbundle.py:311-323`); copies are re-fetched (git-ignored) and the zips rebuilt anyway.
  No test pins the patch hash (grep). It also removes the reason for the `check_firmware.sh`
  negative control (`tools/host_test/check_firmware.sh:31-35`, `:270-286`), which compiles the stock
  `ArduinoHal.cpp` with `-DARDUINO_VENTUNO_Q`. Replacement that keeps the stub-strictness proof:
  compile the stock `ArduinoHal.cpp` with `-DARDUINO=10607 -DARDUINO_ARCH_ZEPHYR` and no board macro
  ("Unknown Arduino" branch, `BuildOpt.h:433-436`), expecting the same PinMode / PinStatus errors.
* Docs tied to the decision: `docs/22_Bundled_Libraries.md:78-90`, `:354-355`, `:462`.
* Recommendation: remove hunk 1 (owner: "eliminate the VENTUNO Q entirely"); keep hunk 2.

---

## 5. (c) Python ground data system, App descriptor, host scripts

### 5.1 Findings
* No runtime code path depends on the board. Bridge access is by method name over the router
  (`frontpanel.py:59-130`; App Lab: `APP_SOCKET`, host: `arduino.router_bridge.DEFAULT_ADDRESS`,
  refs `app-bricks-py/src/arduino/app_utils/bridge.py:11,26-32`). `get_board_name()` exists in
  app_utils (`app-bricks-py/src/arduino/app_utils/utils.py:8-15`) but the GDS does not use it.
* Serial: `E22Serial` opens `[radio] port` by path, reopens after faults every 2 s
  (`radio/e22.py:313-355`), any path up to 512 chars accepted (`config.py:211`), symlinks followed
  (`os.open`, `radio/e22.py:164`) -> `/dev/serial/by-id/...` works if visible in the container (VERIFY).
* Grep for QCS8275 / NVMe / M.2 / PTC / +5V_LIMITED / D20 / D21 / buzzer pin / JDIGITAL in
  `python/`: only the lines listed below; **no** occurrence of PTC, +5V_LIMITED, D20/D21 or a buzzer pin.
* "Voyager": no occurrence in `dssq-groundstation/` (grep); one in `vgq1-spacecraft/sketch/src/fsw/autonomy.h:7` (not ground scope).

### 5.2 Change list
| File:line | Current text | Required change |
|---|---|---|
| `python/main.py:1` | `"""DSS-Q ground station - Arduino App Lab entry point (VENTUNO Q Linux side; Phase B PB-6.2).` | `(UNO Q Linux side; ...)` - keep the `PB-6.2` tag |
| `python/dssq/display/frontpanel.py:3-4` | `The GDS runs on the VENTUNO Q's Linux processor and talks to the board's STM32H5 MCU` | `UNO Q's Linux processor (QRB2210) ... STM32U585 MCU`; router link LPUART1 |
| `frontpanel.py:27` | `One CardKB on the header SDA/SCL bus (contract 16.3 / 17.1)` | keep (only place citing contract 16.3 / 17.1); optionally "(D20/D21, Wire = I2C2)" |
| `python/dssq/gds.py:1300-1301` | `needs the station SX126x radio (VENTUNO Q MCU sketch built with DSSQ_RADIO_SX126X = 1, ...` | `(UNO Q MCU sketch ...)` - operator-visible REJECTED text |
| `gds.py:1302-1303` | `needs the Radio Control Unit (VENTUNO Q MCU wired to the {model} M0/M1/AUX, [gds] rcu = true)` | `(station UNO Q MCU wired to ... M0/M1/AUX on D2/D3/D<aux>, [gds] rcu = true)`; `docs/14_Troubleshooting_and_Event_Response.md:88` quotes it |
| `gds.py:2370` | `router bridge unavailable: front panel / RCU / SX126x radio disabled` | board-neutral; if reworded update `tests/test_end_to_end.py:766` |
| `python/dssq/radioctl.py:6` | `the VENTUNO Q MCU drives the module's M0/M1 pins and reads AUX` | `the station UNO Q MCU ...` |
| `radioctl.py:49` | `set_mode(int) -> bool (VENTUNO Q MCU or simulator)` | `(station MCU or simulator)` |
| `python/dssq/radio/e22.py:18-20` | `... (T33D) or the E22-400TBH-02 board's CH340X (T37S). With the Radio Control Unit (VENTUNO Q MCU on M0/M1/AUX) ...` | name the E15-USB-T2 (CP2102, `E51:857`) as the default T33D adapter, the TBH-02 CH340X (`TBH:311`), "plugged into the UNO Q's USB-C hub"; `UNO Q MCU` |
| `e22.py:27-28` | `The App Lab container bind-mounts /dev, runs in group dialout and allows ttyUSB / ttyACM (contract section 23)` | keep the claim but mark it as verified on the old ground board only (V-31, `docs/20_Known_Limitations.md:78`); VERIFY on the UNO Q |
| `e22.py:126` | ENOENT hint `check the USB-UART, ls /dev/serial/by-id/` | add "and the USB hub / its power" |
| `python/dssq/radio/sx126x.py:3` | `The VENTUNO Q MCU (dssq-groundstation/sketch, build switch DSSQ_RADIO_SX126X = 1)` | `The station UNO Q MCU ...` |
| `python/dssq/ccsds/aes_gcm.py:56-59` | `The VENTUNO Q's QCS8275 has not been measured: VERIFY ...` | `The UNO Q's QRB2210 (4 x Cortex-A53 @ 2.0 GHz, DS:455) has not been measured: VERIFY ...` (budget sentence "11 % on a ten times slower core" stays relevant) |
| `python/dssq/config.py:4` | `tomllib (Python >= 3.11; the station runs 3.12, the App Lab container 3.13)` | host Python of the UNO Q's Debian: version not found in DS -> "the board's Debian Python (>= 3.11 needed)"; container 3.13 |
| `config.py:76` | `"port": "/dev/ttyUSB0"` | keep (CP2102 / CH340X both enumerate as ttyUSB); optionally recommend by-id paths in comments |
| `config.py:100-102` | `"frontpanel": True, "rcu": False, ... "archive_max_gb": 16.0, "archive_min_free_mb": 2048.0` | `frontpanel`/`rcu` keep; **`archive_max_gb`**: 16 GB was sized for the 64 GB eMMC (`station.toml:178-179`, `docs/12_Design_Decisions.md:737`); the ABX00173 has 32 GB shared with Debian and App Lab containers (free space not found) -> propose 8.0 (and keep 2048 MB floor), then `tests/test_logger.py:79` and the mirrored `station.toml:185` change together |
| `python/config/station.toml:2` | `#  DSS-Q1 ground station configuration (VENTUNO Q or any Linux PC)` | `(Arduino UNO Q 4 GB or any Linux PC)` |
| `station.toml:37-40` | `"SX126x" (SX1268 / SX1262 over SPI on the VENTUNO Q MCU, via RadioLib; ...)` | `... on the station UNO Q MCU ...` |
| `station.toml:47` | `port = "/dev/ttyUSB0" # E22 models only: the USB-UART (check with: ls /dev/serial/by-id/)` | add: "through the USB-C hub; E15-USB-T2 = CP2102, TBH-02 = CH340X" |
| `station.toml:168` | `frontpanel = true # VENTUNO Q MCU station I/O (CardKB, LEDs, buzzer) via arduino-router` | `# station UNO Q MCU ...` |
| `station.toml:169-171` | `... true: the VENTUNO Q MCU drives M0/M1/AUX on D2/D3/D4 instead (needed for RADIO, GSCAN, WAKE, LINK*)` | `the UNO Q MCU drives M0/M1/AUX on D2/D3/D<aux>` (pin plan 3.4) |
| `station.toml:172` | `router_address = "" # host default unix:///var/run/arduino-router.sock; App: APP_SOCKET` | keep (VERIFY socket path on the UNO Q in host mode; the Python package default is not in the refs) |
| `station.toml:178-181` | `VENTUNO Q (App Lab): <App>/data/archive = ... on the 64 GB eMMC. An ABSOLUTE archive_dir puts it elsewhere, e.g. an external M.2 2230 NVMe or a USB 3.0 drive - only when that path is visible INSIDE the App container (VERIFY on the hardware)` | `UNO Q 4 GB (App Lab): ... on the 32 GB eMMC (DS:438) ... e.g. a USB drive on the hub - only when visible inside the App container (VERIFY)`. **Keep the words `VERIFY` and `ArduinoApps` in the file** (`tests/test_logger.py:82-83`) |
| `station.toml:185` | `archive_max_gb = 16.0` | see `config.py:101` row |
| `python/dssq/display/dashboard.html:342` | `<h3>Station I/O <small>VENTUNO Q MCU: one CardKB on the header Wire bus, annunciators, Radio Control Unit</small>` | `UNO Q MCU: one CardKB on header SDA/SCL (D20/D21, Wire = I2C2), annunciators, Radio Control Unit` (inline-script hash unaffected: text is outside the `<script>`, `web.py:199`) |
| `dashboard.html:2014` | `["CardKB (header Wire, 0x5F)", ...], ["Radio Control Unit", io.rcu ? "present" : "absent"]` | see 5.3 |
| `python/dssq/__init__.py:1,24,29` | "long-range telemetry mission (v3)"; `display/ ... (station I/O MCU)`; `__version__ = "3.0.0"` | wording per requirement 6 (ultra long range); version bump for the branch (shown in the start event, `gds.py:534`) |
| `app.yaml:3` | description (board-neutral) | optional: name the UNO Q; keys limited to name/icon/description/ports (`tools/package_apps.py:142-143`) |
| `host/install_station.sh:4` | `BOARD (VENTUNO Q Linux side)` | `(UNO Q Linux side)` |
| `install_station.sh:13-16` | `... the board's host Python does not (the arduino-app-cli Debian package depends only on the arduino-router service), and Ubuntu 24.04 does not allow pip into the system Python` | Debian on the UNO Q (`DS:1067`); the externally-managed rule must be re-checked on the board's Debian release (release not found) - the venv approach stays valid either way |
| `install_station.sh:33,37-38` | `arduino-router-bridge==0.5.1`, `/home/arduino/ArduinoApps/dssq-groundstation`, `/home/arduino/.venvs/dssq-gds` | unchanged (user `arduino` on the UNO Q, `vgq1-spacecraft/README.md:36`) |
| `host/dssq-gds.service:1` | `on the VENTUNO Q Linux side` | `on the UNO Q Linux side` |
| `dssq-gds.service:19,23` | `(>= 3.11; Ubuntu 24.04 ships 3.12)`, `(Ubuntu 24.04 refuses pip into the system Python)` | Debian wording (version not found) |
| `dssq-gds.service:34,45-46` | `After=network-online.target arduino-router.service`; `SupplementaryGroups=dialout` | keep (Debian has `dialout`; router service name VERIFY on the UNO Q) |
| `dssq-groundstation/python/archive/tdm/*.kvn` | 4 files present in the working tree | git-ignored runtime data (`.gitignore:11`), never packaged (`tools/package_apps.py:139`); no action |

### 5.3 Web UI metric legitimacy (requirement 7) - station I/O panel
* `SK:1466` sends `H|<fw>|1|<kb>`: the RCU field is the constant `1`.
* `frontpanel.py:211-212` stores it as `rcu`; `gds.py:2068` -> snapshot `station_io.rcu`;
  `dashboard.html:2014` shows "Radio Control Unit: present"; `docs/13_Web_UI_Reference.md:2873`
  documents it as "the MCU drives the E22 M0 / M1 and reads AUX". It is shown even when nothing is
  wired (the E15-USB-T2 default, `station.toml:169-171`).
* AUX is read with `INPUT_PULLUP` (`SK:1431`): an unwired AUX reads HIGH like an idle module, so
  presence cannot be sensed from AUX alone.
* Legitimate alternatives: (i) report the last `rcu_mode` result and its age (the only real
  evidence: AUX went HIGH after a mode change, `SK:219-226`), plus `[gds] rcu`; (ii) rename the row
  to "RCU firmware" (built in: yes) and add "RCU in use" from `gds.radio_mgr` (`dashboard.html:1355`
  already shows `R.rcu ? "active" : "off"`). `station_io` keys are checked key by key against
  docs/13 (`tests/test_docs.py:43-48`): a renamed/added key needs docs/13 13.8 and the index at
  `docs/13_Web_UI_Reference.md:3121`.
* "CardKB (header Wire, 0x5F)" is legitimate (`SK:229-232`, `SK:1459-1460`: `requestFrom(0x5F,1)`).

---

## 6. (d) Tools, CI, tests, packaging, dist

### 6.1 `.github/workflows/ci.yml`
| Line | Current | Required |
|---|---|---|
| 5-6 | `dssq-groundstation: VENTUNO Q station sketch + ground data system` | `UNO Q station sketch` |
| 126 | `boards and its negative control (the stock tag must fail for the VENTUNO Q).` | per 4.4 |
| 176-178 | `"Arduino Q Boards" = arduino:zephyr (UNO Q and VENTUNO Q; ...)` | `(UNO Q)` |
| 226 | `- name: VENTUNO Q station sketch (default radio, E22 via the RCU)` | `UNO Q station sketch ...` (the build follows `SY` automatically, `ci.yml:194`) |
| 238 | `- name: VENTUNO Q station sketch, SX126x radio option (SX1268)` | `UNO Q station sketch ...` |
| 291-296 | `baseline unoq unoq` / `baseline ventunoq ventunoq` / `llext station ventunoq` / `llext station-sx126x ventunoq` | drop the `ventunoq` baseline; `llext station unoq`, `llext station-sx126x unoq` |
| 298-302 | comment "... The UNO Q flight sketch must build and load there too; no core before 1.0.0 has the VENTUNO Q." | the station now must too |
| 322-332 | legacy step builds only `vgq1-spacecraft/sketch` (`flight-legacy`, `flight-legacy-sx126x`) | add `build dssq-groundstation/sketch station-legacy` and `station-legacy-sx126x` (`-DDSSQ_RADIO_SX126X=1 -DDSSQ_SX_CHIP=1268`), `llext station-legacy unoq-legacy`, `llext station-legacy-sx126x unoq-legacy`; rename the step |
| 252-260, 265-283 | bundled-library log check, RouterBridge host test with `build-station.log`, stack bound | board-neutral; keep |

### 6.2 `tools/host_test/check_firmware.sh`
| Line | Current | Required |
|---|---|---|
| 4 | `Arduino toolchains for the UNO Q / VENTUNO Q (Zephyr) are large` | `UNO Q` |
| 22-27 | `every RadioLib translation unit ..., UNO Q and VENTUNO Q`; `the station sketch with DSSQ_RADIO_SX126X=1, VENTUNO Q, both chips` | UNO Q only |
| 31-35 | negative control "... ArduinoHal.cpp for the VENTUNO Q ..." | per 4.4 |
| 61 | `VENTUNOQ=(-DARDUINO=10607 -DARDUINO_VENTUNO_Q -DARDUINO_ARCH_ZEPHYR)` | remove (or replace by an `UNKNOWN_ZEPHYR` set for the negative control) |
| 63 | `FP=... # VENTUNO Q station sketch` | `# UNO Q station sketch` |
| 204 | `check_library "$RADIOLIB" "VENTUNO Q" "${VENTUNOQ[@]}"` | remove (UNO Q already at line 203) |
| 259-268 | `# ---- Station I/O sketch (VENTUNO Q)` ... `check "$FP" "" "${VENTUNOQ[@]}"` and the SX loop with `${VENTUNOQ[@]}` | `${UNOQ[@]}`; add `-DCONFIG_ARDUINO_API_SERIAL_BUFFER_SIZE=64` variant (like the flight, line 194-196) and a check of the new `__errno` shim file if added |
| 270-286 | negative control with `${VENTUNOQ[@]}` and messages "for the VENTUNO Q" | per 4.4 |

### 6.3 Station harness `tools/host_test/station/`
| File:line | Current | Required |
|---|---|---|
| `run.sh:2` | `Station sketch regression harness (VENTUNO Q MCU, ...)` | `(UNO Q MCU, ...)` |
| `run.sh:45` | `BOARD=(-DARDUINO=10607 -DARDUINO_VENTUNO_Q -DARDUINO_ARCH_ZEPHYR)` | `-DARDUINO_UNO_Q` (also used for the RadioLib objects, `run.sh:90`) |
| `include/zephyr/kernel.h:22-28` | provides only `k_current_get()` | add `inline k_tid_t k_sched_current_thread_query() { return k_current_get(); }` (as `tools/host_test/arduino_stub/zephyr/kernel.h:29`) once `SK` uses it |
| `test_e22.cpp:47,58,87`, `test_sx.cpp:54-89`, `runtime.cpp:39` | pin symbols / `k_current_get()` in test code | pins by symbol: unaffected by a re-plan; test code may keep `k_current_get()` |
| `runtime.h`, `include/Arduino_RouterBridge.h` | board-neutral | none |

### 6.4 Stubs and tools
| File:line | Current | Required |
|---|---|---|
| `tools/host_test/arduino_stub/Arduino.h:11-12` | "... RadioLib's ArduinoHal needs its PinMode / PinStatus casts on a platform it does not know (VENTUNO Q), ..." | "(an unknown Zephyr board)" |
| `tools/host_test/arduino_stub/SPI.h:71` | `// spis = <&spi2>, <&spi3> on both the UNO Q and the VENTUNO Q overlays.` | `on the UNO Q overlay (OVL:394)` |
| `tools/host_test/arduino_stub/Arduino.h:80`, `Wire.h:16` | `A0 = 14 ...`, `Wire, Wire1, Wire2` | correct for the UNO Q; none |
| `tools/radiolib-7.8.1.patch:18-34, 88-96` | hunk 1 VENTUNO Q | per 4.4 |
| `tools/arduino_routerbridge-0.4.3.patch:20-22` | "... for the UNO Q flight sketch and the VENTUNO Q station sketch." | header comment only; editing it changes `patch-sha256` (`PROVENANCE.txt`) -> re-fetch both copies |
| `tools/gen_sdls_key.py:14-15` | `--flight-header ... (UNO Q)` / `--ground-key ... (VENTUNO Q)` | `(spacecraft UNO Q)` / `(ground UNO Q)` |
| `tools/llext_check.py` | board-generic | none |
| `tools/host_test/routerbridge/*` | board-generic | none |

### 6.5 `tools/package_apps.py`
| Line | Current | Required |
|---|---|---|
| 8-9 | `dist/dssq-groundstation-ventunoq.zip   folder dssq-groundstation  Arduino VENTUNO Q` | `dist/dssq-groundstation-unoq.zip ... Arduino UNO Q (4 GB, ABX00173)` |
| 67 | `called as 'PATH <unoq|ventunoq> <sketch>` | `'PATH unoq <sketch>` |
| 161 | `AppSpec("dssq-groundstation", "dssq-groundstation-ventunoq.zip", "ventunoq", ...)` | `"dssq-groundstation-unoq.zip", "unoq"`; `check_sketch_yaml` compares fqbn with `arduino:zephyr:{app.board}` (`:733-734`), so `SY:23` and this line change together |
| 1042-1043 | argparse description `... dist/dssq-groundstation-ventunoq.zip.` | new name |
| 931 | SHA256SUMS must list exactly the AppSpec zip names | rebuilt `SHA256SUMS`; `git rm dist/dssq-groundstation-ventunoq.zip` (an extra file is not flagged by this check) |

### 6.6 Tests
| File:line | Current | Required |
|---|---|---|
| `tests/test_packaging.py:405` | `zip_names(built / "dssq-groundstation-ventunoq.zip")` | `-unoq.zip` |
| `tests/test_packaging.py:531, 541, 544` | `"ok   dssq-groundstation-ventunoq.zip"`, `.unlink()`, `"FAIL dssq-groundstation-ventunoq.zip: missing"` | `-unoq.zip` |
| `tests/test_web.py:475` | allowed hosts `"ventunoq.local:8080", "ventunoq"` | e.g. `"dssq-ground.local:8080", "dssq-ground"` (any `.local` / single-label name) |
| `tests/test_logger.py:79` | `== (180, 16.0, 2048.0)` | follow any default change (5.2) |
| `tests/test_logger.py:82-83` | station.toml must contain `VERIFY` and `ArduinoApps` | keep those words when rewriting `station.toml:178-181` |
| `tests/test_end_to_end.py:766` | asserts `router bridge unavailable: front panel / RCU / SX126x radio disabled` | only if `gds.py:2370` is reworded |
| `tests/test_docs.py:43-48` | `station_io` key-by-key vs docs/13 | keep docs/13 13.8 in step with 5.3 |
| `tests/test_citations.py` | tags in code vs docs/12 appendix B | see 3.5 last paragraph; `frontpanel.py:27` is the only citer of contract 16.3 / 17.1 |
| `tests/test_radio.py:320,1088,1119,1506` | literal `S|DSSQ-IO 3.x SX126x|...` status lines | independent of `kFw`; none |

### 6.7 `dist/`
* `dist/SHA256SUMS:1` lists `dssq-groundstation-ventunoq.zip` (sha `d300a189...`); the zip holds
  `dssq-groundstation/README.md` with 12 VENTUNO mentions and the patched RadioLib with the VENTUNO
  hunk (`unzip -p` check). Rebuild both zips (the flight zip also carries the RadioLib hunk and its
  README names the VENTUNO Q 6 times).

### 6.8 App READMEs and top-level README (dist README text)
| File:line | Current (abridged) | Required |
|---|---|---|
| `dssq-groundstation/README.md:3` | "Ground station ... for the **Arduino VENTUNO Q**" | "**Arduino UNO Q 4 GB (ABX00173)** with a USB-C hub" |
| `:8` | "runs on the board's STM32H5F5 microcontroller: one CardKB keyboard on the header I2C pins ..." | STM32U585; D20/D21 |
| `:21` | `STM32H5F5 (..., FQBN arduino:zephyr:ventunoq)` | `STM32U585 (..., arduino:zephyr:unoq)` |
| `:22` | "RadioLib 7.8.1 with a small documented patch (the stock release does not compile for the VENTUNO Q)" | per 4.4 |
| `:28` | "E22-400T33D on a USB-UART adapter (`/dev/ttyUSB0` by default), with its M0/M1/AUX pins on the station sketch's Radio Control Unit ... on the VENTUNO Q, A0 drives the buzzer, so never put a jumper there" | default = E15-USB-T2 on the hub, RCU optional (`rcu = false`, `station.toml:169`); A0 warning per 3.3 |
| `:36-37` | `dssq-groundstation-ventunoq.zip`; "App Lab connected to the VENTUNO Q" | new zip; App Lab connected to the **ground** UNO Q (name the boards differently, `DS:2528` step 3) |
| `:47`, `:81`, `:92` | `<ventuno-q-address>` | `<ground-uno-q-address>` (e.g. `<name>.local`, accepted by the host guard, `:84`) |
| `:62` | "builds `sketch/` for the VENTUNO Q's MCU" | UNO Q MCU; note the core version App Lab uses (0.90.0 or >= 1.0.0) |
| `:96` | "Whether App Lab's container exposes port 8080 to the network is not verified on hardware yet" | re-state for the UNO Q (VERIFY) |
| `:112` | "on the board's 64 GB eMMC" | 32 GB (`DS:438`) |
| `:124` | `archive_max_gb`, 16 | follow 5.2 |
| `:142` | serial port troubleshooting | add hub / power checks |
| `:168` | "the board's Linux (Ubuntu 24.04)" | Debian (`DS:1067`) |
| `:171` | `# on the VENTUNO Q, after stopping the App in App Lab` | `# on the ground UNO Q ...` |
| `:205` | `sketch/sketch.yaml  build profile: arduino:zephyr:ventunoq + ...` | `unoq` |
| `vgq1-spacecraft/README.md:3` | "... ground station App ... on an Arduino VENTUNO Q" | "on a second Arduino UNO Q (4 GB)" |
| `vgq1-spacecraft/README.md:26`, `:116` | "on the VENTUNO Q, A0 drives the station buzzer, so never put a jumper there" | per 3.3 |
| `vgq1-spacecraft/README.md:46` | `arduino@<ventuno-q-address>:...` | ground UNO Q address |
| `vgq1-spacecraft/README.md:96`, `:99` | "Decode one on the VENTUNO Q", "Make a line on the VENTUNO Q" | "on the ground UNO Q" |
| `README.md:1, 13, 36, 101, 150, 159, 178, 180` | title "... on Arduino UNO Q and VENTUNO Q", ground = VENTUNO Q, mermaid subgraph, zip name, scp target, A0 warning, dashboard URL | UNO Q x 2; new zip; addresses |
| `README.md:24` | "the ground station on the VENTUNO Q received and decoded its downlink" (historical fact, record 2026-10-13-A) | keep as history or reword ("the first ground station"); owner decision (open question 7) |

---

## 7. (e) USB, hub, power, App container

### 7.1 What the code assumes
* Default port `/dev/ttyUSB0` (`config.py:76`, `station.toml:47`, `e22.py:22`); no USB topology,
  vendor/product id or VENTUNO-specific port is assumed anywhere (grep "usb" in `python/`, `host/`,
  `app.yaml`: only `config.py:76`, `gds.py:6,105,143`, `sim.py:77`, `e22.py:18-28,126,269,278,318`,
  `station.toml:37,47,169,180`, `dssq-gds.service:45`).
* Serial fault handling already covers a USB re-enumeration (fd closed on error, reopen by path
  every 2 s: `e22.py:336-355`; GDS retries a failed open every 10 s: `gds.py:111`, `:2414-2440`).
  If the CP2102 re-enumerates while another ttyUSB exists, the number can change -> recommend a
  `/dev/serial/by-id/usb-Silicon_Labs_CP2102...` path in docs (exact id string not found; VERIFY
  with `ls -l /dev/serial/by-id/` on the board, and that the symlinks exist inside the container).
* No code reads USB power limits. Power constraints live only in docs: V-69
  (`docs/20_Known_Limitations.md:87`), `docs/03_Hardware_Integration.md:551-557`, `:762-778`.
* Container device access: claimed in `e22.py:27-28`; proven only on the VENTUNO Q
  (`docs/20_Known_Limitations.md:78`, record 2026-10-13-A obs. 2, `docs/21_Test_Records.md:651`). Not in the refs
  (the App container definition belongs to arduino-app-cli, not in `refs/`); a USB camera example
  using `/dev/video1` exists for App Lab Apps (`refs/app-bricks-examples/core-and-foundational/06-camera-basics/02-camera-from-sources/python/main.py:15`), indirect evidence only. VERIFY on the UNO Q (T-GS-02).
* Linux drivers: CP2102 -> `cp210x`, CH340X -> `ch341` (kernel module names are general Linux
  knowledge, presence in the UNO Q image **not found**: VERIFY `ls /dev/ttyUSB*` after plugging).

### 7.2 Power facts for the hub set-up
| Item | Value | Citation |
|---|---|---|
| UNO Q USB-C input | 5 V, up to 3 A; PD requests only 5 V / 3 A | `DS:640-690` (§3.1) |
| UNO Q VIN | 7-24 V (JANALOG 8 / JMEDIA), buck -> diode-OR 5V_SYS | `DS:640-660`, `DS:884-888` |
| JANALOG 5 V pin | USB VBUS pass-through; no current-limited +5 V output documented (VENTUNO Q had +5V_LIMITED 2.8 A, `VDS:567-568`, `docs/03_Hardware_Integration.md:522`) | `DS:2360` |
| E22-400T33D | 3.3-5.5 V, >= 4.7 V for rated output; TX 850-1200 mA (1000 mA instantaneous @ 33 dBm); RX 11-15 mA; UART 3.3 V level | `T33D:180-216` (§2.1-2.2) |
| E15-USB-T2 | CP2102; supply jumper 3.3 V / 5 V; mode jumpers M0/M1 | `E51:508-512`, `E51:857-860` |
| E22-400TBH-02 | CH340X USB-C; DC / terminal 4.5-15 V | `TBH:185-192`, `TBH:266`, `TBH:311` |
| Board power consumption | not found | - |
| Hub per-port current, PD pass-through behaviour | not found (hub model unknown) | - |

Consequences for docs / design (not code): receiving through the hub is the proven VENTUNO Q case;
transmitting (commands, WAKE, station ID - essential for ULTRA LONG RANGE uplink at full power)
needs >= 4.7 V at 1.2 A at the module: either a hub whose USB-A port is fed from its PD input and
measured under load (T-HW-07 method, V-69), or the existing alternative "plain 3.3 V USB-UART on
the hub + E22 on its own 5 V >= 2 A supply, common GND" (`docs/03_Hardware_Integration.md:558-583`).
Power the ground UNO Q either from the hub's PD pass-through (5 V / 3 A contract only) or from VIN
7-24 V; the old ground power budget (`docs/03_Hardware_Integration.md:762-778`: 12 V barrel, 17.9 W
VENTUNO Q boot peak, +5V_LIMITED) is void.

### 7.3 Operation with one USB-C port
* PC-hosted mode needs a USB data link for the first set-up; afterwards App Lab reaches the board over
  the LAN; SBC mode needs no USB link; SBC peripherals go through a "USB-C dongle with external
  power delivery capabilities" (`DS:2534-2541`; Portuguese `DS:7612-7620` is complete).
  -> First-time set-up of the ground UNO Q over USB-C (Wi-Fi credentials, board name `DS:2528`),
  then unplug the PC, plug the hub, use App Lab over the network.
* Give the two UNO Qs distinct names at set-up; App Lab switching boards stopped the ground App in
  record 2026-10-13-A obs. 12 (`docs/21_Test_Records.md:661`): "Run at startup" on both.
* `DS:2572`: USB interfaces may be occupied while an App runs (PC-hosted mode).

---

## 8. Documentation pointers (for the docs writers; not exhaustive rewrite guidance)

VENTUNO mentions per file (`grep -c -i ventuno`): docs/03 39, docs/00 31, docs/08 18, docs/12 14,
dssq-groundstation/README.md 12, docs/22 12, docs/01 11, docs/06 10, README.md 9, docs/FAQ 7,
docs/21 7, docs/20 7, CHANGELOG 7, vgq1-spacecraft/README.md 6, docs/19 6, docs/07 6, docs/10 4,
docs/18 3, docs/16 3, docs/14 3, docs/13 3, docs/17 2, docs/09 2, Project_Summary 1, docs/15 1,
docs/11 1, docs/04 1. Other VENTUNO-only terms (QCS8275, STM32H5, +5V_LIMITED, NVMe, JDIGITAL,
PH11/PH12): docs/03 36, docs/01 4, docs/20 4, docs/FAQ 4, docs/12 5, and 1-3 in docs/00, 06, 07,
08, 09, 10, 15, 18, 19, 21, Project_Summary.

Key passages that encode VENTUNO Q facts:
* `docs/03_Hardware_Integration.md` section 5 (`:506-682`: pin map 5.1, power 5.2 = barrel / screw
  terminal / USB-C PD 9-20 V, E15 5.3.0, RCU 5.3.1, TBH-02 5.3.2 "plug the board's USB-C into a
  VENTUNO Q USB-A port", lamps / CardKB 5.4, mermaid 5.5), 4.6 SX table VENTUNO column (`:352-362`),
  6.5 power budget (`:762-778`), BOM 7.2 (`:838-858`).
* `docs/08_Verification_and_Test.md` 2.6 build table (`:165-209`), T-GS-05 (`:450`).
* `docs/01_System_Design.md:55`, `:765-767`, `:945-951` (9.7 ground resources).
* `docs/12_Design_Decisions.md:128` (platform decision), `:737` (eMMC 64 GB), decision list.
* `docs/22_Bundled_Libraries.md:78-90`, `:165`, `:323`, `:354-355`, `:412`, `:462`.
* `docs/16_Developer_Guide.md:443-444`, `:519-523`; `docs/14_Troubleshooting_and_Event_Response.md:88`, `:197`.
* `docs/13_Web_UI_Reference.md:2862-2876` (station I/O panel, includes the RCU row of 5.3).
* `docs/18_Data_Products.md:48`; `docs/10_References.md:72`; `docs/20_Known_Limitations.md` V-29,
  V-31, V-68, V-69.
* Historical records naming the VENTUNO Q: `docs/21_Test_Records.md` (record 2026-10-13-A,
  `:623-675`), CHANGELOG history.

---

## 9. Open questions / VERIFY list

1. Pin plan P / H / M (3.4) and whether the buzzer leaves A0.
2. RadioLib hunk 1: remove (recommended) or keep; replacement negative control (4.4).
3. `archive_max_gb` default for the 32 GB eMMC; free space after Debian + App Lab on ABX00173 (not found).
4. App container access to `/dev/ttyUSB*` and `/dev/serial/by-id` on the UNO Q App Lab (V-31 proven on VENTUNO Q only).
5. arduino:zephyr 0.90.0 UNO Q overlay = same `i2cs` / `spis` order as core main (refs contain main only).
6. Hub specifics: which dongle, per-port current, PD pass-through behaviour, whether its USB-A ports are fed from the PD input; E22 VCC under TX through the hub (V-69).
7. Treatment of historical text (README.md:24, docs/21 records, CHANGELOG) that truthfully names the VENTUNO Q.
8. Whether to make RCU presence observable (5.3) or rename the row.
9. Debian release and host Python version on the UNO Q (host mode, `install_station.sh` checks >= 3.11).
10. Router socket path and `arduino-router.service` name on the UNO Q for host mode (only asserted in repo text).
11. Bridge long-notify blocking on 0.90.0 for the SX126x station (`rf_rx` ~43 ms) - accept and record as VERIFY like V-68.
12. CardKB at 5 V directly on FT pins D20/D21 without the BSS138 (pins tolerate 5 V per `DS:2310`; the CardKB's own thresholds / pull-ups are not published).
