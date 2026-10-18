# R6 - Datasheet fact sheet for sensor and board documentation

Scope: every component the v4 branch documents (UNO Q 2 GB / 4 GB, Nicla Sense ME and its Bosch
sensors BHI260AP / BMM150 / BMP390 / BME688, ams AS7265X on the SparkFun Triad, PNI RM3100,
M5Stack CardKB, EBYTE E15-USB-T2 + E22-400T33D, with a short E22-400T37S / E22-400TBH-02
cross-check). Read only; nothing in `/home/user/Benchmarks` was changed. One helper file was
written next to the datasheet texts: `ds/rm3100_quickguide.txt` (pdftotext of the PNI "RM3100
Arduino Quick Guide" PDF found in the refs folder; it is the only source for the breakout pin-out).

Conventions

* Every fact carries a citation. "not found" = searched in the listed sources without result.
  "Inference" = a conclusion drawn from cited facts that no source states. "VERIFY" = must be
  measured or checked on the hardware.
* Page numbers are the PDF page (form-feed count of the pdftotext output). Where the printed page
  number differs it is given as "printed p. N". Files without page breaks (UNO Q, Nicla, pinout,
  BHI260AP, hookup guide, CardKB) are cited by section and by line `L` of the extracted text.
* Units: the RM3100 text extraction dropped the "µ" sign ("T" in the text = µT); restored here.

## 0 Citation tags

| Tag | Source (all read only) |
|---|---|
| `UQ` | `.../scratchpad/ds/4c60b196-ABX00162-datasheet.txt` = Arduino UNO Q User Manual, SKU ABX00162-ABX00173, "Modified: 17/06/2026" (UQ L4, L44). Identical copy: `d00f08af-ABX00162-datasheet.txt` (`cmp` equal) |
| `OVL` | `/tmp/claude-0/-home-user-Benchmarks/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/refs/ArduinoCore-zephyr/variants/arduino_uno_q_stm32u585xx/arduino_uno_q_stm32u585xx.overlay` |
| `DTS` | `.../refs/zephyr/boards/arduino/uno_q/arduino_uno_q-common.dtsi` |
| `ZSER`, `ZWIRE` | `.../refs/ArduinoCore-zephyr/cores/arduino/zephyrSerial.h`, `.../libraries/Wire/Wire.cpp` |
| `VQ` | VENTUNO Q datasheet ABX00181 (`ds/2d82e8a7-ABX00181-datasheet_2.txt`), comparison only |
| `NDS` | `ds/414d9b73-nicla_me_datasheet.txt` = Nicla Sense ME User Manual SKU ABX00050 |
| `NPIN` | `ds/cd6c13b5-Nicla_ESLOV_pinout.txt` = Nicla Sense ME "Full Pinout", 8 pages, "Last update 27 Feb 2024" (page 5 missing in the extract). Pinout page boundaries in the text: p1 L1-300, p2 L301-417, p3 L418-529, p4 L530-652, p6 L653-845, p7 L846-1002, p8 L1003-1134 |
| `MBV.h` / `MBV.cpp` | `.../refs/ArduinoCore-mbed/variants/NICLA/pins_arduino.h` / `variant.cpp` |
| `NSYS` | `.../refs/ArduinoCore-mbed/libraries/Nicla_System/src/Nicla_System.cpp` |
| `BHY2` | `/tmp/claude-0/-home-user/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/niclafw/nicla-sense-me-fw-main/Arduino_BHY2/src/` |
| `BHI` | `ds/3f577a50-bst-bhi260ap-ds000.txt` = Bosch BHI260AP datasheet BST-BHI260AP-DS000-02 rev 1.1, 15 Apr 2021 (BHI L1-14). Page numbers from its table of contents (BHI L150-518) |
| `BMM` | `/root/.claude/uploads/.../407b699d-bst-bmm150-ds001.pdf` = BMM150 datasheet BST-BMM150-DS001-05 rev 1.4, April 2020 (BMM p1) |
| `BMP` | `.../6e980c1d-bst-bmp390-ds002.pdf` = BMP390 datasheet rev 1.7, March 2021, BST-BMP390-DS002-07 (BMP p1, p15) |
| `BME` | `.../53c89a2d-bst-bme688-ds000.pdf` = BME688 datasheet rev 1.3, Feb 2024, BST-BME688-DS000-03 (BME p1) |
| `ASD` | `.../817df498-AS7265x_Datasheet.pdf` = ams AS7265x datasheet v1-04, 2018-Jul-09 (ASD p14) |
| `ASG` | `ds/f7bee49d-AS7265X_hookup_guide.txt` = SparkFun "Spectral Triad (AS7265x) Hookup Guide", SEN-15050 |
| `SFH` / `SFC` | `.../refs/SparkFun_AS7265x_Arduino_Library/src/SparkFun_AS7265X.h` / `.cpp` (library.properties version 1.0.5) |
| `RMM` | `.../f8597506-RM3100-User-Manual.pdf` = PNI "RM3100 & RM2100 Sensor Suite User Manual" Doc 1017252 r06 (printed page = PDF page - 1) |
| `RMQ` | `.../refs/RM3100-Arduino/RM3100 Arduino Quick Guide.pdf` (v1.1, 19 Dec 2023), extracted to `ds/rm3100_quickguide.txt` |
| `RMI` | `.../refs/RM3100-Arduino/RM3100_Arduino_I2C/RM3100_Arduino_I2C.ino` |
| `CKB` | `ds/b41d1005-Cardkb_mini_keyboard.txt` (= `ds/cardkb.txt`) = M5Stack "Unit CardKB" product page, SKU U035 |
| `E51` | `.../5b3a258d-E51-TTL-500_Datasheet_EN_v1.0.pdf` (the only source that describes the E15-USB-T2 board) |
| `T33` | `.../b8c54790-E22-400T33D-V2.1_UserManual_CN_v1.05.pdf` (English text; running header says "E22-400T30D User Manual"; printed p. = PDF p. - 1) |
| `T37` | `.../c2d8b245-E22-xxxT37S_UserManual_EN_v1.5.pdf` (printed p. = PDF p. - 1) |
| `TBH` | `.../6e826cce-E22-400TBH-02TestBoarddatasheet_EN_v1.0_2.pdf` |
| `CFG`, `ARU` | `/home/user/Benchmarks/vgq1-spacecraft/sketch/src/config.h`, `/home/user/Benchmarks/nicla_sense_me_aru/nicla_sense_me_aru.ino` |
| `R3` | sibling report `.../scratchpad/v4/reports/R3_sensors.md` (ESLOV wiring analysis; consistent with this sheet) |

---

## 1 Summary of the facts that drive the v4 design

| # | Fact | Source |
|---|---|---|
| 1 | UNO Q has exactly two variants: 2 GB RAM + 16 GB eMMC, and 4 GB RAM + 32 GB eMMC. The SKU names are rendered as graphics and lost in the text; the document covers ABX00162-ABX00173 and the radio approval lists both SKUs. The owner's mapping ABX00173 = 4 GB is consistent with the order but the text itself does not pair SKU and size (VERIFY on the box label) | UQ L4, L437-438, L8202 |
| 2 | Running Arduino App Lab ON the board (SBC mode) - which a stand-alone ground station does - is recommended on the 4 GB variant | UQ §11.1 L2519 |
| 3 | UNO Q power: USB-C PD requests ONLY 5 V / 3 A (no higher PD profiles); VIN 7-24 V (JANALOG pin 8 / JMEDIA); a regulated 5 V on the JANALOG 5 V pin, "up to 3 A". USB-C VBUS and the 7-24 V buck are diode-ORed onto 5V_SYS | UQ §3.1 L667-686 |
| 4 | UNO Q has ONE USB-C port (USB 3.1, role switching, DP Alt-Mode). Peripherals in SBC mode need "a USB-C dongle with external power delivery capabilities"; in host/OTG mode a protected P-FET (Q2801) can source VBUS from 5V_SYS | UQ L510-527, L609, L888; Portuguese L7622-7623 |
| 5 | The MCU (STM32U585) headers JDIGITAL / JANALOG / JSPI / Qwiic are 3.3 V logic. JDIGITAL pins are FT (5 V tolerant as inputs) except D3 (PB0, TT, 3.6 V max). A0-A5 are NOT 5 V tolerant; A4/A5 as I2C3 need pull-ups to 3.3 V only | UQ §9 L1341-1344, §9.6 L2310-2321, §9.7 L2441-2454 |
| 6 | Three MCU I2C controllers reach headers: I2C2 on D20/D21 (PB11 SDA / PB10 SCL) = `Wire`; I2C4 on Qwiic (PD13 SDA / PD12 SCL; alt PF15/PF14 on JMISC 18/16) = `Wire1`; I2C3 on A4/A5 (PC1 SDA / PC0 SCL) = `Wire2`. USART3 uses the same PB10/PB11 pins as I2C2 (the spacecraft uses them as the E22 UART, so `Wire` is not available there) | UQ L2052-2057, L2298-2306, L2412-2424, L1451-1465; OVL L7-37, L257-266, L393; DTS L119-136; ZWIRE L279-280; CFG L54-63, L178-185 |
| 7 | Nicla ESLOV J5 = JST SM05B-SRSS-TB 5-pin: 1 5V, 2 INT, 3 SCL, 4 SDA, 5 GND. ESLOV SDA/SCL are nRF52832 P0_22/P0_23, i.e. the same `Wire` bus as J2 pins 1/2; ESLOV INT = P0_19 | NDS L399-401, L628-653; NPIN L560-572; MBV.h L62-63, L82; MBV.cpp L77-78, L90 |
| 8 | Nicla I/O on the headers goes through bidirectional translators at VDDIO_EXT (1.8-3.3 V, software programmable); Arduino_BHY2 `begin()` sets it to 3.3 V. VIH 0.7 VDDIO_EXT, VIL 0.3 VDDIO_EXT | NDS L220-249, L435, L469; BHY2 Arduino_BHY2.cpp L55-57; NSYS L56-82 |
| 9 | On the Nicla the BHI260AP is the sensor hub: BMP390 (pinout: "BMP390L") and BME688 hang on its master SPI M1 (chip selects MCSB1/MCSB2), the BMM150 on its auxiliary pads ASCX/ASDX ("dedicated I2C connection"); the nRF52 talks to the BHI260AP over SPI (HIRQ = P0_14) | NPIN L871-905; NDS L406-419; MBV.cpp L92 |
| 10 | Pinout page 7 also shows the BHI260AP master I2C port M3 (M3SCL/M3SDA) on nRF pins P0_23/P0_22 - the same nets as the J2/ESLOV I2C bus - and master SPI M2 on the J1 SPI pins (P0_11/P0_27/P0_28/P0_29). Inference: the external I2C bus is physically shared with a BHI260AP master port; the stock firmware must leave M3 idle (VERIFY: no unexpected traffic on the bus) | NPIN L897-905; NDS L406 |
| 11 | BHI260AP: 16-bit accel ±2/4/8/16 g (4096 LSB/g at ±8 g, 1.2 mg rms noise), 16-bit gyro ±125...±2000 °/s (16.4 LSB/(°/s) at ±2000, 0.07 °/s rms, 0.007 °/s/√Hz), fusion ODR up to 800 Hz; below 100 Hz the output is sub-sampled from a 100 Hz signal | BHI Tables 117, 119 (L11107-11400); BHI L3515-3518 |
| 12 | BHI260AP "accuracy" has two different meanings: the Sensor Status meta event (0 Unreliable, 1 Low, 2 Medium, 3 High) and the Quaternion+ field "estimated accuracy in radians" (Q14), which is ALWAYS 0 for the Game Rotation Vector | BHI Table 100 (L9722-9733), Table 89 (L9083-9118) |
| 13 | BME688 + BSEC: IAQ 0-500 (resolution 1), static IAQ, CO2-equivalent ppm (derived from VOCs, not measured), b-VOC-equivalent ppm, accuracy status 0-3 (0 during stabilisation, 3 = best performance), gas % and gas-scan classes | BME p3, p8-9 (Table 3 + footnote 4), p11 (Table 6), p31 (Table 20) |
| 14 | RM3100: I2C address 0b01000 + SA1 SA0 = 0x20-0x23; I2CEN high = I2C; SCL ≤ 1 MHz (standard/fast/fast-plus/high-speed supported); 24-bit two's complement results; gain 20/38/75 LSB/µT at 50/100/200 cycle counts; DRDY pin high when done. The manual contradicts itself on which pin is SA0 (resolved by Table 4-1, the §4.5 example and PNI's breakout guide: the SSN pad = SA0, the MISO/SO pad = SA1) | RMM printed p. 5, 19-25, 27-33; RMQ p2, p5 |
| 15 | AS7265X: one I2C address 0x49 for the whole 3-chip set (master AS72651 = NIR R-W, AS72652 = VIS G-L, AS72653 = UV/blue A-F), SCL ≤ 400 kHz, 2.7-3.6 V on I2C; virtual-register protocol; gain 1 / 3.7 / 16 / 64x; integration in 2.78 ms steps; calibrated outputs are IEEE-754 floats; channel accuracy ±12 % | ASD p1, p8, p10, p13, p15-18, p23-32; SFH L30, L80-82 |
| 16 | CardKB: I2C 0x5F, Grove HY2.0-4P GND / 5V / SDA / SCL; logic level, pull-ups and current: not published | CKB L31, L34, L97-108, L119-125 |
| 17 | E22-400T33D: supply 3.3-5.5 V (≥ 4.7 V for full power), UART logic 3.3 V ("risk of burnout" at 5 V), TX current 850 / 1000 / 1200 mA (min/typ/max) at 33 dBm, RX 11-15 mA; air rate 2.4-62.5 kbps (2.4 kbps is the floor), sensitivity -128 dBm typ at 2.4 kbps | T33 p6-7 (printed p. 5-6) |
| 18 | E15-USB-T2: CP2102 USB-UART driver; mode jumpers M0/M1 (fitted = 0, removed = 1 per the E51 procedure); supply jumper 3.3 V or 5 V | E51 p8 §3.6, p13 §5 |

---

## 2 Arduino UNO Q (ABX00162 2 GB / ABX00173 4 GB)

### 2.1 Variants, processors, memory, radio

| Item | Fact | Source |
|---|---|---|
| Document | "User Manual SKU: ABX00162-ABX00173", modified 17/06/2026 | UQ L4, L44 |
| Variants | "UNO Q is available in two variants: 2 GB RAM, 16 GB on-board storage : 4 GB RAM, 32 GB on-board storage" (SKU labels lost in extraction, also in the CN and PT sections) | UQ §2.1 L436-438; L3018-3022; L5542-5545 |
| SKU evidence | CMIIT radio approval lists "ABX00162 - CMIIT ID 26J996Q0A162 / ABX00173 - CMIIT ID 26J996Q0A173" | UQ L8202 |
| MPU | Qualcomm Dragonwing QRB2210, 4 × Arm Cortex-A53 @ 2.0 GHz, 64-bit; Adreno 702 GPU @ 845 MHz; dual ISP; Debian OS (upstream support) | UQ §2.2.1 L452-455 |
| MPU I/O | USB 3.1 with role switching over the USB connector, SDIO 3.0, 4-lane MIPI-CSI-2, 4-lane MIPI-DSI; MPU I/O at 1.8 V | UQ L456, L475, §6.1 L1067-1070 |
| MCU | ST STM32U585, Arm Cortex-M33 up to 160 MHz, 2 MB flash, 786 kB SRAM; "Arduino Core on Zephyr OS"; manages ADC, PWM, CAN, LED matrix and the 3.3 V headers JDIGITAL, JANALOG, JSPI, Qwiic | UQ L458-470, L475, §6.2 L1072-1074 |
| Memory | eMMC 16 or 32 GB (EMMC1); LPDDR4X 2 or 4 GB, single rank, 32-bit (DRAM1) | UQ L473-474, L20 |
| Inter-processor | "Bridge", RPC layer on both sides; services in both directions plus notifications | UQ §7 L1104 |
| Wireless | WCBN3536A (Qualcomm WCN3980): Wi-Fi 5 802.11a/b/g/n/ac dual-band + Bluetooth 5.1; Wi-Fi over SDIO, BT over UART, shared PCB antenna | UQ §2.2.2 L507-509, L528 |
| Display | DisplayPort Alt-Mode on USB-C via ANX7625 (U3001) DSI-to-DP bridge; SBC mode Full HD 1920 × 1080 supported, 1280 × 720 optimal; DSI shared with JMEDIA, only one display output at a time | UQ L512-526, L2464 |
| Temperature | T_OP -10 ... 60 °C (ambient near the board); near the limits less output current is available | UQ §3.2 L739-745 |
| Mechanical | 68.58 mm × 53.34 mm, bottom parts < 2 mm, UNO form factor and hole pattern | UQ §12 L2697, L2714, L2775 |

### 2.2 Power inputs (Ratings §3.1-3.3)

| Input / rail | Value | Notes | Source |
|---|---|---|---|
| USB-C VBUS (JUSB1) | 5 V, up to 3 A | "Over USB Power Delivery, it requests only the 5 V / 3 A contract and does not request higher-voltage PD profiles. Use a supply and cable rated for 5 V at 3 A to avoid undervoltage during short activity peaks such as wireless bursts or display initialization" | UQ L673-676, L685, L527, L1069 |
| VBUS_USBC operating range | 4.5 / 5.0 / 5.5 V (min/typ/max) | min = lowest continuous value; brief dips can cause resets or link drops | UQ §3.2 L721-726, L745 |
| VIN (DC IN) | 7-24 V | on JANALOG pin 8 "VIN IN" (power only) and JMEDIA (raw 7-24 V, power only) | UQ L677-680, L2376-2380, L2002 |
| DC_IN operating range | 7.0 ... 24.0 V | "select a supply that comfortably covers the 5 V load and use short cables" | UQ L727-732, L745 |
| Reverse polarity | "Verified with -24 V applied to DC IN. The operation is specified only with the correct polarity. Do not apply reverse voltage during normal use." | | UQ L687-688 |
| VIN path diode drop | buck output -> 5V_SYS: 0.35 V @ 1.0 A, 0.37 V @ 1.5 A, 0.39 V @ 2.0 A (0.35 / 0.56 / 0.78 W) | measured with JANALOG VIN injection | UQ L689-710 |
| 5 V pin | 5 V, up to 3 A, JANALOG | "A regulated external 5 V DC source can also be used to supply power to the board via the 5 V pin on the JANALOG header" | UQ L681-685 |
| 5V_SYS | diode-OR of USB-C VBUS and the 7-24 V buck output (both via Schottky rectifiers) | | UQ §3.3 L751-753, L686, L885 |
| PWR_3P8V | buck from 5V_SYS, "reserved for system design and future features"; VBAT (JMISC 60) connects to it | | UQ L754-756, L887-888 |
| PWR_3P3V | buck from PWR_3P8V; supplies STM32U585, ANX7625 3.3 V rails, Wi-Fi 3.3 V domain and "the 3.3 V header pins"; operating range 3.1 / 3.3 / 3.5 V | | UQ L757-759, L733-738, L887 |
| VREG_L15A_1P8V | PM4125 PMIC LDO L15A from 5V_SYS: SoC I/O, ANX7625 DVDD18, Wi-Fi logic, on-board level shifters; available on JMISC/JCTL | | UQ L760-762, L886-887 |
| VCOIN | powers only the PMIC RTC, not Linux or MCU | | UQ L888 |
| Host-mode VBUS | "A protected P-channel MOSFET (Q2801) can source USB VBUS from 5V_SYS when the board operates as a USB host/OTG"; "VBUS load-switch/back-drive protection (Q2801)"; JCTL pin 10 VBUS_DISABLE "Controls VBUS path" (1.8 V) | current limit of Q2801: not found | UQ L888, L527, L2150-2153 |
| Output current of +3V3 OUT / IOREF / Qwiic 3.3 V / +5V pins | not found (no mA figure anywhere in the English section) | | UQ grep " mA" (none) |

### 2.3 Header pin tables relevant to I2C, UART, SPI, PWM and power

All MCU header pins are 3.3 V logic (UQ L1072-1074). "Most [JDIGITAL] pins use an FT-type I/O
structure and are 5 V-tolerant as inputs. D3 (PB0) uses a TT-type I/O structure and is only 3.6
V-tolerant" (UQ L2310-2321). Analog/ADC use is never 5 V tolerant: max VDD + 0.3 V ≈ 3.6 V
(UQ L1342-1343, L2441-2445).

#### JDIGITAL (A2, JDIGITAL1), 18 pins (UQ §9.6 L2181-2321; Arduino pin numbers from OVL L276-300)

| Pin | Name | MCU pin | Functions | Note | Source |
|---|---|---|---|---|---|
| 1 | D0 | PB7 | USART1_RX, TIM4_CH2 | UART | UQ L2190-2196 |
| 2 | D1 | PB6 | USART1_TX, TIM4_CH1 | UART | UQ L2197-2203 |
| 3 | D2 | PB3 | TIM2_CH2 | - | UQ L2204-2209 |
| 4 | ~D3 | PB0 | OPAMP2_OUTPUT, TIM3_CH3 | PWM / **not 5 V tolerant** | UQ L2210-2216 |
| 5 | D4 | PA12 | FDCAN1_TX, TIM1_ETR | - | UQ L2217-2223 |
| 6 | ~D5 | PA11 | FDCAN1_RX, TIM1_CH4 | PWM | UQ L2224-2230 |
| 7 | ~D6 | PB1 | TIM3_CH4 | PWM | UQ L2231-2236 |
| 8 | D7 | PB2 | TIM8_CH4N | - | UQ L2237-2242 |
| 9 | D8 | PB4 | TIM3_CH1 | - | UQ L2243-2248 |
| 10 | ~D9 | PB8 | TIM4_CH3 | PWM | UQ L2249-2254 |
| 11 | ~D10 | PB9 | SPI2_SS, TIM4_CH4 | PWM | UQ L2255-2261 |
| 12 | ~D11 | PB15 | SPI2_MOSI, TIM1_CH3N | PWM | UQ L2262-2268 |
| 13 | D12 | PB14 | SPI2_MISO, TIM1_CH2N | - | UQ L2269-2275 |
| 14 | D13 | PB13 | SPI2_SCK, TIM1_CH1N | - | UQ L2276-2282 |
| 15 | GND | - | ground | | UQ L2283-2288 |
| 16 | AREF | - | analog reference, not a GPIO | | UQ L2289-2294 |
| 17 | D20 | PB11 | I2C2_SDA, TIM2_CH4 | silk "SDA"; also USART3_RX (OVL) | UQ L2295-2301; OVL L257-266 |
| 18 | D21 | PB10 | I2C2_SCL, TIM2_CH3 | silk "SCL"; also USART3_TX (OVL) | UQ L2302-2308; OVL L264 |

PWM: the core enables PWM only on the "~" pins (OVL L396-397).

#### JANALOG (A3, JANALOG1), 14 pins (UQ §9.7 L2325-2454)

| Pin | Name | Net | Function | Note | Source |
|---|---|---|---|---|---|
| 1 | BOOT | MCU_BOOT0 | boot strap, 3.3 V | | UQ L2333-2338 |
| 2 | IOREF | PWR_3P3V | I/O voltage reference (mirrors 3.3 V) | "Output only; do not back-feed" | UQ L2339-2344, L2449-2454 |
| 3 | RESET | MCU_NRST | MCU reset | | UQ L2345-2350 |
| 4 | +3V3 OUT | PWR_3P3V | 3.3 V supply | current limit not found | UQ L2351-2356 |
| 5 | +5V USB VBUS | 5V_USB_VBUS | "5 V supply (pass-through)" | "Power only" | UQ L2357-2362 |
| 6, 7 | GND | GND | ground | | UQ L2363-2374 |
| 8 | VIN IN | DC_IN | 7-24 V input | "Power only" | UQ L2375-2380 |
| 9 | A0 / D14 | PA4 | ADC, DAC0, TIM2_CH1 | direct ADC, not 5 V tolerant | UQ L2381-2388 |
| 10 | A1 / D15 | PA5 | ADC, DAC1, TIM3_CH1 | direct ADC, not 5 V tolerant | UQ L2389-2396 |
| 11 | A2 / D16 | PA6 | ADC, OPAMP2_INPUT+, TIM3_CH2 | | UQ L2397-2402 |
| 12 | A3 / D17 | PA7 | ADC, OPAMP2_INPUT- | | UQ L2404-2410 |
| 13 | A4 / D18 | PC1 | ADC, **I2C3_SDA**, LPTIM1_CH1 | pull-ups to 3.3 V only | UQ L2411-2418, L1343 |
| 14 | A5 / D19 | PC0 | ADC, **I2C3_SCL**, LPTIM1_IN1 | pull-ups to 3.3 V only | UQ L2419-2426, L1343 |

#### Qwiic (A4, QWIIC1), 4 pins, 3.3 V (UQ §9.3 L2032-2059)

| Pin | Name | Net | Note |
|---|---|---|---|
| 1 | GND | Ground | |
| 2 | +3V3 OUT | PWR_3P3V | "Supply for Qwiic devices" |
| 3 | SDA | PD13 (I2C4_SDA) | 3.3 V |
| 4 | SCL | PD12 (I2C4_SCL) | 3.3 V |

On-board pull-ups on Qwiic, D20/D21 or A4/A5: not found (UQ, DTS). "Plug-and-play compatibility with
Modulino nodes" (UQ L1345-1347). 5 V tolerance of PD12/PD13: not stated (not found).

#### JSPI (A5, JSPI1), 6 pins (UQ §9.4 L2060-2096, §9 L1348-1349)

| Pin | Name | Net | Note |
|---|---|---|---|
| 1 | MISO | PC2 (SPI2_MISO) | 3.3 V |
| 2 | +5V | 5V_USB_VBUS | "Power only" |
| 3 | SCK | PD1 (SPI2_SCK) | 3.3 V |
| 4 | MOSI | PC3 (SPI2_MOSI) | 3.3 V |
| 5 | RESET | MCU_NRST | 3.3 V |
| 6 | GND | ground | |

FT-type; "5 V-tolerant as inputs or in open-drain, while outputs drive 3.3 V"; chip select must be a
GPIO on JDIGITAL/JMISC (UQ L1348-1349). The Zephyr board file maps SPI2 by default to D10-D13
(PB9/PB13/PB14/PB15) and documents PD1/PC2/PC3 as the "ICSP connector" alternative (DTS L138-142).
Datasheet inconsistency: §9 text says JSPI "Includes a 5V_SYS power pin" (UQ L1349) while the pin map
names the net 5V_USB_VBUS (UQ L2072-2076).

#### JCTL (A1, JCTL1), 10 pins, 1.8 V (UQ §9.5 L2097-2153)

| Pin | Name | Function |
|---|---|---|
| 1, 7 | GND | |
| 2 | USB_BOOT | boot strap |
| 3 / 5 | VOL_DOWN / VOL_UP | GPIO_36 / GPIO_96 |
| 4 / 6 | SOC_SE4_TX / RX | Linux system console UART (SE4); "should not be repurposed for user I/O" (UQ L1363-1365) |
| 8 | PMIC_RESET | PM4125 reset |
| 9 | +1V8 OUT | VREG_L15A_1P8V |
| 10 | VBUS_DISABLE | VBUS power switch disable |

#### JMISC (B1, JMISC1), 60 pins, mixed 1.8 V / 3.3 V - relevant rows (UQ §9.1 L1368-1706)

| Pin | Name | MCU pin | Domain | Note |
|---|---|---|---|---|
| 16 | MCU_I2C4_SCL | PF14 | 3.3 V MCU | alternate pins of the same I2C4 as Qwiic (OVL L11 "sleep" pinctrl, DTS L131) |
| 18 | MCU_I2C4_SDA | PF15 | 3.3 V MCU | as above |
| 12, 14 | MCU_PE7 / MCU_PE8 | PE7 / PE8 | 3.3 V MCU | GPIO |
| 23 | MCU_MCO | PA8 | 3.3 V | used for the LED-matrix counter clock (OVL L41-48) |
| 37-52 | SOC_GPIO_x | - | 1.8 V MPU | "interface-dedicated (not maker GPIO)" (UQ L1706) |
| 53, 55 | +3V3 (OUT) | - | power | |
| 54, 56 | +5V_USB (OUT) | - | power | |
| 57 | +1V8 (IN) | - | power | (table says IN; UQ L887 says the 1.8 V rail "is also available on JMISC") |
| 59 | VCOIN (IN) | - | PMIC RTC | |
| 60 | VBAT (OUT) | - | reserved | |

JMEDIA (B2) carries 1.8 V MIPI lanes, camera CCI I2C (1.8 V, reserved), +3V3 OUT (58, 60) and raw VIN
(UQ L1831-2002, L1366-1367).

### 2.4 Arduino software view of the buses (ArduinoCore-zephyr, UNO Q variant)

| Object | Peripheral | Pins | DT default clock | Source |
|---|---|---|---|---|
| `Wire` | I2C2 | D20 SDA (PB11), D21 SCL (PB10) | `I2C_BITRATE_FAST` (400 kHz) | OVL L393 order `i2cs = <&i2c2>, <&i2c4>, <&i2c3>`; ZWIRE L271-280; DTS L119-126 |
| `Wire1` | I2C4 | Qwiic SDA PD13 / SCL PD12 (sleep pinctrl PF15/PF14 = JMISC) | 400 kHz | OVL L7-13; DTS L128-136 |
| `Wire2` | I2C3 | A4 SDA (PC1), A5 SCL (PC0) | 400 kHz | OVL L32-38 |
| `Serial` | Monitor through the router (lpuart1 to the MPU) | - | - | OVL L390 "arduino,router-serial = <&lpuart1>; 'Serial' is provided by the Monitor"; ZSER L167-171, L201-203 |
| `Serial1` | USART1 | D1 TX (PB6), D0 RX (PB7) | 115200 | OVL L252-255 (also "zephyr,console"), L391; DTS L112-117; ZSER L201-203 |
| `Serial2` | LPUART1 | PG7/PG8 (+RTS/CTS) - the router link | 115200 | DTS L105-110; OVL L269-272 (inference: index 1 of `serials`) |
| `Serial3` | USART3 | D21 TX (PB10), D20 RX (PB11) - **same pins as `Wire`** | 115200 | OVL L257-266; CFG L54-63 |
| `SPI` / `SPI1` | SPI2 (D10-D13) / SPI3 (internal, "zephyr,spi-slave") | | | OVL L221-245, L394; DTS L138-149 |

Inference (from the ZSER macros): with a router serial present, index 0 of `serials` becomes `Serial1`,
index 1 `Serial2`, index 2 `Serial3`. The spacecraft already uses USART3 on D20/D21 for its E22 and
therefore must never call `Wire.begin()` (CFG L54-56).

### 2.5 USB-C, SBC mode, dongle / hub, App Lab (ground-station relevant)

| Topic | Fact | Source |
|---|---|---|
| Port | single USB-C (JUSB1): USB 3.1 with role switching; DP Alt-Mode; SuperSpeed pairs shared between DP and USB 3.1 - "When DisplayPort Alt-Mode is active, USB data speed is reduced" | UQ L510-512, §10 L2460, L2542 |
| PD | sink requests 5 V / 3 A only | UQ L527, L685, L1069 |
| Host power | Q2801 sources VBUS from 5V_SYS in host/OTG mode | UQ L888 |
| Related products | "Full 24-pin USB-C cable"; "USB-C dongle with external power delivery capabilities" | UQ §2.3 L606-609 |
| Peripherals in SBC mode | "For peripherals in SBC mode (keyboard, mouse, USB camera, microphone), use a USB-C dongle with external power delivery capabilities." (English text garbled at L2535-2541; complete in PT L7622-7623 and CN L5094-5095) | UQ L2535-2541, L7622-7623 |
| Modes | PC-hosted mode needs a USB data link for first-time setup, then LAN (SSH); SBC mode needs no USB data link: "just power the board" and use the network target once it joins the network | UQ L2524-2525, L2535-2541; PT L7612-7623 |
| App Lab on board | "Arduino App Lab can be installed on your PC, or executed directly on the UNO Q in Single-Board Computer mode. For this setup, the UNO Q's 4GB LPDDR4X variant is recommended" | UQ L2519 |
| First setup | board checks for updates; asks for device name/password and Wi-Fi credentials | UQ L2526-2529 |
| App structure | Python program on Linux + sketch on the MCU + optional Bricks; Run builds the Linux part, flashes the MCU, deploys Bricks; three log tabs (launch, Python, serial) | UQ L2514-2523, L2559-2569, L2609-2614 |
| USB occupation | "While an App is bound and running, USB interfaces may be occupied by the system ... To use external CLI tools over USB, stop the App or disconnect the board." | UQ L2572 |
| Boot | first boot 20-30 s; wait for the boot LED sequence or matrix animation | UQ L2557, L1050-1051 |
| Power button | short press reboots Linux (no power cut); long-press reboot may interrupt Apps; the board boots when power is applied | UQ §11.5 L2640-2671 |
| CP2102 (E15-USB-T2) driver on UNO Q Debian | not found in UQ. The previous record shows `/dev/ttyUSB0` (CP2102) opened inside an App on the VENTUNO Q (docs/20 L78) - VERIFY on the UNO Q image | docs/20_Known_Limitations.md L78 |
| Hub downstream current per port | not found (hub-specific) - VERIFY | - |

### 2.6 UI and indicators

| Item | Fact | Source |
|---|---|---|
| RGB LED 1, 2 | driven by the QRB2210 via `/sys/class/leds/` (red/green/blue:user; LED2 default panic / wlan / bt); PWM ≈ 2 kHz | UQ §5 L1017-1030 |
| RGB LED 3, 4 | driven by the STM32U585 (LED3_R/G/B, LED4_R/G/B); active-low | UQ L1031-1047; OVL L380-381, L414-415 |
| LED matrix | 8 × 13 monochrome blue (104 pixels) driven by the STM32U585; shows the boot logo for ~20-30 s; "Accessing the matrix before startup completes may interfere with MCU operation" | UQ L1050-1051 |
| Power LED | green, tied to 3.3 V | UQ L1052 |

### 2.7 Comparison with the VENTUNO Q being retired (for the migration notes only)

| Item | UNO Q | VENTUNO Q (ABX00181) | Source |
|---|---|---|---|
| MPU / OS | QRB2210, Debian | Dragonwing IQ8 (QCS8275), Ubuntu (Debian also supported) | UQ L453-455; VQ p1, p6 |
| MCU | STM32U585 M33 160 MHz | STM32H5F5 M33 250 MHz | UQ L458; VQ p6 |
| RAM / eMMC | 2/16 or 4/32 GB | 16 GB LPDDR5 / 64 GB | UQ L437-438; VQ p6-7 |
| USB | 1 × USB-C only | 1 × USB-C + 2 × USB 3.0 Type A (+2 on header) | UQ L510; VQ p7 |
| PD input | 5 V / 3 A only | 20 V up to 3 A | UQ L685; VQ p9 |
| Ethernet | none listed | 2.5 Gbit RJ45 | VQ p7 |

Inference: the ground UNO Q needs the USB-C dongle/hub for the E15-USB-T2 (no USB-A port) and has only
15 W input for itself plus everything on the hub.

### 2.8 UNO Q caveats / datasheet inconsistencies

1. SKU-to-memory pairing is not in the text (2.1).
2. JSPI pin 2 and JANALOG pin 5 are called "5V_USB_VBUS ... pass-through, power only" in the pin maps
   (UQ L2072-2076, L2357-2362) but §3.1 lists "5 V Pin 5 V up to 3 A JANALOG" as an INPUT method
   (UQ L681-685) and §9 calls the JSPI pin "5V_SYS" (UQ L1349). Which way current may flow and its
   limit: not specified - VERIFY before using JANALOG 5 as a 5 V source with the board on VIN only.
3. No current limit is published for any power-out pin (+3V3 OUT, IOREF, Qwiic, JMISC +5V_USB).
4. Operating temperature is -10...60 °C, narrower than every sensor on the board stack (all
   -40...85 °C).

---

## 3 Arduino Nicla Sense ME (ABX00050)

### 3.1 Board composition

| Ref | Part | Role | Source |
|---|---|---|---|
| MD1 | u-blox ANNA-B112 (nRF52832, Cortex-M4F 64 MHz, 512 KB flash, 64 KB SRAM, BLE 2402-2480 MHz, internal antenna) | application MCU, runs the sketch; operating voltage 1.8 V | NDS L78-103, L346-347, L406 |
| U5 | Bosch BHI260AP | 6-axis IMU + Fuser2 sensor hub with fusion | NDS L107-118, L354-355, L409-410 |
| U3 | Bosch BMP390 (pinout label "BMP390L") | pressure | NDS L122-137, L350-351, L416; NPIN L885 |
| U4 | Bosch BMM150 | 3-axis magnetometer | NDS L141-150, L352-353, L419 |
| U6 | Bosch BME688 | gas / T / RH / p | NDS L154-163, L356-357, L413 |
| U2, U7 | MX25R1635F 2 MB flash (U2 = BHI260AP XiP flash on QSPI, U7 = nRF data flash) | | NDS L348-349, L409; NPIN L898-904 |
| U1 | ATSAMD11D14A | USB bridge + JTAG for the ANNA | NDS L167-172, L391-392, L425 |
| U9 | TI BQ25120A | PMIC / charger / LDO that sets VDDIO_EXT | NDS L360-361, L435-436 |
| U10 | SN74LVC1T45 | 1-channel level translator | NDS L362-363 |
| U11 | TXB0108 | bidirectional translator | NDS L364-365 |
| U12, U13 | NTS0304 | 4-bit translating transceivers (U13: SAMD11 3.3 V <-> ANNA 1.8 V) | NDS L366-367, L393-394, L425 |
| U14 | AP2112K-3.3, 0.6 A LDO | 3.3 V for the SAMD11 from USB only | NDS L395-396, L425, L435 |
| U8 / DL1 | IS31FL3194 LED driver (max 40 mA) / RGB LED | on `Wire1` with the PMIC | NDS L358-359, L422; NSYS L38 |
| J5 | JST SM05B-SRSS-TB(LF)(SN), 5-pin "Eslov" | ESLOV connector | NDS L399-401 |
| J4 | 3-pin 1.2 mm ACH battery connector BM03B-ACHSS-GAN-TF | | NDS L397-398 |
| J7 | micro USB | | NDS L402-403 |

### 3.2 Recommended operating conditions (NDS "Ratings", L198-249)

| Symbol | Description | Min | Typ | Max | Unit |
|---|---|---|---|---|---|
| VIN | input voltage from VIN pad | 3.5 | 5.0 | 5.5 | V |
| VUSB | input voltage from USB connector | 4.8 | 5.0 | 5.5 | V |
| VDDIO_EXT | level translator voltage | 1.8 | 3.3 | 3.3 | V |
| VIH | input high level | 0.7 VDDIO_EXT | | VDDIO_EXT | V |
| VIL | input low level | 0 | | 0.3 VDDIO_EXT | V |
| TOP | operating temperature | -40 | 25 | 85 | °C |

Notes in the table: "VDDIO_EXT is software programmable"; "All I/O pins operate at VDDIO_EXT apart
from ADC1 and ADC2 - 1V8, JTAG_SAMD11 - 3V3, JTAG_ANNA - 1V8, JTAG_BHI - 1V8"; "If the internal
VDDIO_EXT is disabled, it is possible to supply it externally" (NDS L244-249). ESLOV pin-1 input range:
not found (treat like USB, behind a Schottky - inference, see R3 4.5).

### 3.3 Power tree

* Inputs: micro USB (J7), ESLOV (J5) or VIN, into the BQ25120 (U9); "A Schottky diode provides
  reverse polarity protection to the USB and ESLOV voltages" (NDS L435).
* 1.8 V buck rail feeds "all other components"; LED driver on a 5 V boost; PMID ORs VIN and BATT;
  "All I/O broken out to the pins are fed through a bidirectional voltage translator running at
  VDDIO_EXT" (NDS L435).
* Battery: single-cell 3.7 V Li-Po on J4; charge current 40 mA, termination 4 mA (NDS L436).
* VDDIO_EXT setting: BQ25120 LDO register, LDO = 0.8 V + code × 100 mV; 3.3 V = 0xE4, 1.8 V = 0xA8;
  `nicla::enable3V3LDO()` writes 0xE4 (NSYS L56-82). Arduino_BHY2 `begin()` calls `nicla::begin()`
  then `nicla::enable3V3LDO()` (BHY2 Arduino_BHY2.cpp L55-57). Pinout note: "Disabling the LS/LDO pin
  it is possible to power the Nicla Sense ME using VDDIO_EXT pin on the header. If LS/LDO pin is
  enabled, it is possible to configure the Nicla Sense ME to work at +3V3 or +1V8" (NPIN L778-782).
* LPIO limits: "LPIOs are driven by bidirectional translators powered by VDDIO_EXT. These translators
  are meant for low power and can only drive very limited current" (NPIN L289-290).
* Without the stock library a PMIC keep-alive thread pings it every 10 s (NSYS L41-49).

### 3.4 Connector pin-outs (NDS "Connector Pinouts", L468-653)

"All the pins on J1 and J2 (excluding fins) are referenced to the VDDIO_EXT voltage" (NDS L469).

| J1 (Header A) | Function | | J2 (Header B) | Function |
|---|---|---|---|---|
| 1 | LPIO0_EXT | | 1 | SDA (I2C data) |
| 2 | NC | | 2 | SCL (I2C clock) |
| 3 | CS (SPI) | | 3 | LPIO1_EXT |
| 4 | COPI | | 4 | LPIO2_EXT |
| 5 | CIPO | | 5 | LPIO3_EXT |
| 6 | SCLK | | 6 | GND |
| 7 | ADC2 | | 7 | VDDIO_EXT (logic level reference) |
| 8 | ADC1 | | 8 | N/C |
| | | | 9 | VIN |

(NDS L471-552.) nRF pin numbers: J2 SDA = P0_22 ("4/SDA1"), SCL = P0_23 ("3/SCL1") (NPIN L126-131;
MBV.cpp L77-78).

| J5 ESLOV pin | Function | Type (NDS) | nRF52 net | Source |
|---|---|---|---|---|
| 1 | 5V | "5V Power Rail" | (to U9 via Schottky) | NDS L634-637, L435 |
| 2 | INT | "Digital IO" | P0_19 (`PIN_ESLOV_INT`, Arduino pin 12 "INT ESLOV") | NDS L638-641; NPIN L571; MBV.h L82; MBV.cpp L90 |
| 3 | SCL | I2C clock | P0_23 (= J2 pin 2) | NDS L642-645; NPIN L571 |
| 4 | SDA | I2C data | P0_22 (= J2 pin 1) | NDS L646-649; NPIN L572 |
| 5 | GND | Ground | | NDS L650-653 |

Fins (J2 test points P1-P8): BHI_SWDIO/SWDCLK, ANNA_SWDIO/SWDCLK, RESET, SAMD11_SWDIO/SWDCLK, +1V8;
"All JTAG logic levels operate at 1.8V apart from the SAMD11 pins (P6 and P8) which are 3.3V ... don't
scale with VDDIO" (NDS L554-592). J3 pads / J4 connector: VBAT, NTC, (GND) (NDS L594-626).

Not found: the translator part behind J5, on-board pull-ups on J2/J5, the ESLOV connector pitch
(the JST "SM05B-SRSS" designation is the SH family; the pitch is not stated in NDS - VERIFY), cable
colours.

### 3.5 Internal bus topology (which sensor sits where)

| Link | Fact | Source |
|---|---|---|
| nRF52 -> BHI260AP | SPI host interface HSCX/HCSB/HSDX/HSDO + HIRQ (P0_14 "INT BHI260"); ANNA is "SPI host for the data logging 2MB flash (U7) and the BHI260 (U5)" and "secondary for the BHI260 (U5) I2C and SPI connection" | NPIN L897, L902-903; MBV.cpp L92; NDS L406 |
| BHI260AP M1 SPI -> BMP390 | M1SCX/M1SDX/M1SDI, chip select MCSB1 | NPIN L878-887; NDS L416 ("controlled via an SPI interface to the BHI260 ... on the same bus as the BME688") |
| BHI260AP M1 SPI -> BME688 | same bus, chip select MCSB2 | NPIN L888-895 |
| BHI260AP aux -> BMM150 | ASCX/ASDX to BMM150 SCK/SDI; NDS: "a dedicated I2C connection to the BHI260 ..., acting as the host" | NPIN L872-877; NDS L419 |
| BHI260AP M2 SPI | M2SCX/M2SDX/M2SDI/MCSB4 on P0_11/P0_27/P0_28/P0_29 = the J1 SPI header nets | NPIN L899-903; NDS J1 L486-500 |
| BHI260AP M3 I2C | M3SCL/M3SDA on P0_23/P0_22 = the J2/ESLOV I2C nets | NPIN L899, L903 |
| `Wire` (nRF TWI) | P0_22 SDA / P0_23 SCL (header + ESLOV) | MBV.h L62-63, L120-121 |
| `Wire1` | P0_15 SDA / P0_16 SCL: PMIC + LED driver | MBV.h L65-66; NPIN L709-747, L765; NSYS L38 |

Reference-designator errors in NDS: the BMP390/BMM150 paragraphs call the BHI260 "U2" (the table says
U5; U2 is flash) and the LED paragraph calls the ANNA "U5" (NDS L416-422 vs L346-355). The power
paragraph names the PMIC "BQ2512BAYFPR" (typo for BQ25120AYFPR) (NDS L435 vs L360-361).

### 3.6 Power consumption (NDS L659-686)

| Condition | Typ |
|---|---|
| standby | 460 µA |
| blink sketch | 960 µA |
| advertising with sensor polling at 1 Hz | 2.5 mA |
| advertising with polling once per hour | 1.15 mA |

"measurements ... activating the temperature sensor, accelerometer and gyroscope ... 1 Hz sample rate
and 1 ms latency" (NDS L686). No figure for 5 V ESLOV operation with fusion + BSEC running: not found.

### 3.7 Other Nicla facts for documentation writers

* BHI260AP OTA via WebBLE; "This board can act as a secondary to an ESLOV controller and have the
  firmware updated through this method" (NDS L452-456).
* Recovery: double-tap reset right after power-up enters the bootloader (NDS L465).
* BME688 gas scan cycle on the Nicla "typical ... 10.8 seconds" (NDS L413).
* BMP390 on the Nicla: "relative accuracy of ±0.03 hPa and an RMS of 0.02 Pa in high-resolution
  mode ... 200 Hz, or ... 1 Hz, consuming less than 3.2 µA" (NDS L416).
* BME688 table: "Sensor-to-sensor deviation (IAQ): ± 15% ± 15 IAQ"; outputs "IAQ, bVOC- and
  CO2-equivalents (ppm), gas scan result (%), intensity level" (NDS L161-163).
* BLE radio: 2.402-2.480 GHz, 40 channels, +6 dBm typical; 20 cm RF exposure distance (NDS L791-816).

---

## 4 Bosch BHI260AP (smart sensor hub with 6-axis IMU)

### 4.1 Device

| Item | Fact | Source |
|---|---|---|
| Core | Fuser2, 32-bit ARC EM4 with FPU; 20 MHz "Long Run" (950 µA CoreMark) or 50 MHz "Turbo" (2.8 mA) | BHI L20, L64-68 |
| Memory | 256 kB SRAM, 144 kB ROM, external flash XiP over QSPI | BHI L73-77 |
| Host interface | SPI or I2C; SPI and GPIO up to 50 MHz; I2C up to 3.4 MHz; I2C address 0x28 (HSDO low) / 0x29 (HSDO high), 7-bit only, no clock stretching | BHI L78-84; Table 5 p.27 (L1711-1718, L1700-1730) |
| Master ports | M1-M3, mapped to 2 SPI + 2 I2C controllers (Table 15) | BHI p.59 L3655-3665 |
| Supply | VDD (IMU analog) 1.71 / 1.8 / 3.6 V; VDDIO (I/O + Fuser2) 1.71 / 1.8 / 1.89 V; -40...85 °C | BHI Table 114 p.146 (L10717-10740) |
| I/O levels | VIL 0.3 VDDIO, VIH 0.7 VDDIO; internal pull-ups 71/121/214 kΩ | BHI Table 115 (L10763-10800) |
| Host interrupt | GPIO0; default firmware: active high, push-pull, level, low drive | BHI L1697-1700 |
| Package | LGA 44 pads, 3.6 × 4.1 × 0.83 mm | BHI L97 |

### 4.2 Accelerometer (Tables 116-117, p.148-149)

| Parameter | Value | Source |
|---|---|---|
| Ranges | ±2, ±4, ±8, ±16 g | BHI L10952-10973 |
| Resolution | 16 bit | BHI L11107-11120 |
| Sensitivity | 16384 / 8192 / 4096 / 2048 LSB/g (±2/4/8/16 g) | BHI Table 117 |
| Sensitivity error | ±0.8 % typ, ±2 % max | Table 117 |
| Zero-g offset | ±20 mg (component, ±8 g), ±50 mg soldered over life | Table 117 |
| Offset temperature drift | ±0.5 mg/K | Table 117 |
| Nonlinearity | ±0.5 %FS (±2 g) | Table 117 |
| Output noise | 1.2 mg rms (±8 g, 80 Hz filter, ODR 200 Hz) | Table 117 |
| Cross-axis / alignment | 1 % / ±0.5 ° | Table 117 |
| Physical ODR | 12.5 ... 1600 Hz | Table 117 |
| Start-up | 3.2 ms (suspend/low-power -> normal) | Table 116 |

### 4.3 Gyroscope (Tables 118-119, p.149-150)

| Parameter | Value | Source |
|---|---|---|
| Ranges | ±125, ±250, ±500, ±1000, ±2000 °/s | BHI Table 118 (L11238-11260, L11250-11252) |
| Sensitivity | 262.4 / 131.2 / 65.6 / 32.8 / 16.4 LSB/(°/s) | Table 119 (L11297-11320) |
| Sensitivity error | ±3 % soldered over life (±1.3 % application-PCB compensated) | Table 119 |
| Zero-rate offset | ±1 °/s at 25 °C (fast offset compensation off); ±1.5 °/s over -40...85 °C; 0.05 °/s/K | Table 119 |
| Noise | 0.007 °/s/√Hz @ 10 Hz; 0.07 °/s rms (74.6 Hz filter, ODR 200 Hz) | Table 119 |
| Bias stability | 5 °/h (vibration-free, 25 °C, earth rotation compensated) | Table 119 |
| Nonlinearity | 0.02 %FS (±1000/±2000) | Table 119 |
| Physical ODR | 25 ... 6400 Hz, ODR accuracy ±1 % | Table 119 |
| Start-up | 55 ms suspend -> normal (ODR 1600 Hz); 10 ms from fast start-up | Table 118 |

### 4.4 Virtual sensors (Table 88, p.119; IDs non-wake / wake-up)

| Virtual sensor | IDs | Payload format | Scale | Mode | Needs external sensor |
|---|---|---|---|---|---|
| Rotation Vector (9-DoF) | 34 / 35 | Quaternion+ | Q14 | continuous | BMM150 or AK09915 |
| Game Rotation Vector (6-DoF) | 37 / 38 | Quaternion+ | Q14 | continuous | - |
| Geomagnetic Rotation Vector | 40 / 41 | Quaternion+ | Q14 | continuous | BMM150 or AK09915 |
| Orientation (Euler) | 43 / 44 | Euler | 360°/2^15 | continuous | BMM150 or AK09915 |
| Accelerometer passthrough / corrected / raw / offset | 1 / 4,6 / 3,7 / 5,91 | 3D vector | dynamic (range-dependent) | continuous | - |
| Gyroscope passthrough / corrected / raw / offset | 10 / 13,15 / 12,16 / 14,92 | 3D vector | dynamic | continuous | - |
| Magnetometer passthrough / corrected / raw / offset | 19 / 22,24 / 21,25 / 23,93 | 3D vector | dynamic | continuous | BMM150 or AK09915 |
| Gravity | 28 / 29 | 3D vector | dynamic | continuous | - |
| Linear acceleration | 31 / 32 | 3D vector | dynamic | continuous | - |
| Temperature | 128 / 132 | int16 | °C/100 (-4000 ... 8500) | on-change | BME280 or BME680 |
| Barometer | 129 / 133 | uint24 | 1/128 Pa | continuous | BME280 / BME680 / BMP280 |
| Humidity | 130 / 134 | uint8 | 1 %RH | on-change | BME280 or BME680 |
| Gas | 131 / 135 | uint32 | Ohms | continuous | BME680 |
| Step counter / detector, significant motion, tilt, wake / glance / pick-up gestures, activity, device orientation, stationary / motion detect, any motion, PDR, SWIM, self-learning AI | various | events / structs | - | one-shot / on-change / special | - |
| Timestamps | 251/245 small delta, 252/246 large delta, 253/247 full (40-bit, wraps every 198 days) | 1/64000 s | | | |

Source: BHI Table 88 L8433-9075. Note: the datasheet lists the generic drivers (BME280/BME680/BMP280);
the Nicla's Arduino firmware for the BHI260AP adds BMP390/BME688 and the BSEC virtual sensors
(IDs 115 BSEC 1.x, 116 BSEC2 collector, 117 BSEC2, 171 BSEC legacy) - these IDs come from Arduino_BHY2,
not from the datasheet (BHY2 sensors/SensorID.h L59-61, L89). Arduino_BHY2 scale factors: Orientation
0.01098 (= 360/32768), Temperature 0.01, Barometer 0.0078, Humidity 1.0 (BHY2 SensorID.h L165, L189-195).
The ARU already compensates the library's 0.0078 vs the datasheet's 1/128 = 0.0078125 (ARU L41-45,
L77).

### 4.5 Output formats (§15.1, p.121-124)

| Format | Layout | Source |
|---|---|---|
| Quaternion+ | ID, x, y, z, w as int16 LSB-first scaled 2^-14, then "Estimated accuracy in radians" uint16 scaled 2^-14; "For Game Rotation Vector the Estimated Accuracy in Radians is reported as 0" | BHI Table 89 L9083-9118 |
| Euler | ID, heading, pitch, roll as int16 scaled 360°/2^15 | BHI Table 90 L9121-9140 |
| 3D Vector | ID, X, Y, Z int16 LSB-first; scale from Table 88 / range | BHI Table 91 L9149-9160 |
| Event size | fixed per sensor ID, includes the ID byte | BHI L9078 |

### 4.6 "Accuracy" and calibration (what the dashboard may legitimately show)

| Item | Fact | Source |
|---|---|---|
| Sensor Status meta event | per virtual sensor, sent whenever the status changes: 0 Unreliable, 1 Accuracy Low, 2 Accuracy Medium, 3 Accuracy High | BHI Table 100 p.128 (L9715-9733) |
| Quaternion accuracy | radians estimate (Q14); always 0 for GRV | BHI Table 89 |
| Calibration state | parameters 0x0201 (accel), 0x0203 (gyro), 0x0205 (mag); "should be saved when the accuracy of the corresponding sensor reaches 3 and loaded on system boot to achieve the effect of warm start"; soft-iron matrix can be written | BHI L7354-7386 |
| Dynamic range | settable per physical sensor (accel g, gyro °/s, mag µT); not for fused sensors; "Dynamic Range Changed" meta event tells the host when to switch scale | BHI Table 58 L6682-6751; meta event L9745 |
| Default ranges | not found in the datasheet | - |

### 4.7 ODR, latency, FIFO behaviour

* Virtual sensor ODR up to 800 Hz; supported rates step ×0.5 down to 1.5625 Hz; "Down to an ODR of
  100 Hz, the sensor fusion software will accordingly reduce the data rate ... Below an ODR of 100 Hz,
  the data provided is sub sampled from a 100 Hz signal"; unsupported requests land within 90-210 % of
  the request (BHI L3515-3518, L3363).
* Enable = sample rate > 0 Hz (float); latency 24-bit ms (0 = interrupt per sample; max ~4.6 h)
  (BHI L3355-3367).
* FIFO overflow discards up to 512 bytes and emits a FIFO Overflow meta event (BHI L9738-9741).

### 4.8 Current (Table 14, p.48; Long Run / Turbo)

| Virtual sensor | ODR | mA |
|---|---|---|
| Game Rotation Vector | 25 / 400 / 800 Hz | 1.068 / 1.301 / - (Turbo 1.085 / 1.372 / 1.779) |
| Rotation Vector (9-DoF, magnetometer current excluded) | 25 / 400 / 800 Hz | 1.126 / 1.399 / - (Turbo 1.167 / 1.550 / 1.950) |
| Geomagnetic RV | 25 / 200 Hz | 0.213 / 0.382 |

(BHI L3420-3500.)

### 4.9 Axes and mounting

"The default axis orientation is shown in Figure 33. This orientation is valid for all sensor outputs
(both physical and virtual). For any other sensor connected to the hub, e.g. magnetometer, the
physical alignment ... should be according to Figure 34 to guarantee correct 9DoF data fusion";
axes can be remapped by the orientation matrix (BHI §20.3 p.159-160, L12292-12300; Table 70 L7331-7350).
The Nicla board-level axis drawing: not found in NDS/NPIN text (VERIFY from Arduino's board drawing or
by measurement: gravity on +Z with the board flat, component side up).

---

## 5 Bosch BMM150 (Nicla magnetometer, behind the BHI260AP)

| Parameter | Value | Source |
|---|---|---|
| Measures | 3-axis magnetic field (FlipCore), plus Hall-plate resistance for temperature compensation | BMM p3, p14 |
| Range | typ ±1300 µT (x, y), ±2500 µT (z); min ±1200 / ±2000 µT | BMM p2, p6 Table 1 |
| Resolution | ~0.3 µT; data 13 bit (x, y), 15 bit (z), RHALL 14 bit, two's complement | BMM p2, p7, p14 |
| Heading accuracy | ±2.5 ° (30 µT horizontal field, fully calibrated, ideal tilt compensation) | BMM p6 |
| Gain error | ±2 % typ, ±5 % max (after API compensation) | BMM p7 |
| Zero-B offset | ±40 µT uncompensated; ±2 µT after Bosch eCompass calibration with calibration movements | BMM p7-8 |
| Noise (presets) | low power 1.0/1.0/1.4 µT; regular 0.6; enhanced regular 0.5; high accuracy 0.3 µT rms | BMM p8, p13 Table 3 |
| Presets (nXY / nZ / ODR / max forced ODR / current) | LP 3/3/10 Hz/>300 Hz/0.17 mA; regular 9/15/10/100/0.5 mA; enhanced 15/27/10/60/0.8 mA; high accuracy 47/83/20/20/4.9 mA | BMM p13 Table 3 |
| Max forced-mode rate | f ≈ 1 / (145 µs·nXY + 500 µs·nZ + 980 µs) | BMM p13 |
| Supply | VDD 1.62-3.6 V (typ 2.4), VDDIO 1.2-3.6 V; peak 18-20 mA during measurement; suspend 1-3 µA | BMM p6-7 |
| Timing | POR 1.0 ms; suspend -> sleep 3.0 ms | BMM p7 |
| Interface | SPI 3/4-wire or I2C (standard/fast, 7-bit); I2C 0x10 + CSB·2 + SDO (0x10-0x13); SCL ≤ 400 kHz; chip ID 0x32 (reg 0x40, readable only with power bit = 1) | BMM p2, p23, p32, p36 Table 30, p37 Table 31 |
| Axes | "when the BMM150's X axis points towards the north pole, the measured field will be positive" (Earth's north pole is a magnetic south pole); remap via API | BMM p46 §8.2, Table 33 |
| Abs. max | VDD/VDDIO -0.3 ... 4.0 V; field > 7 T any direction | BMM p9 Table 2 |
| On the Nicla | not directly addressable from the nRF or the UNO Q; data only through the BHI260AP virtual sensors 19-25, 34/35, 40/41, 43/44 | NPIN L872-877; BHI Table 88 |

---

## 6 Bosch BMP390 (Nicla barometer, behind the BHI260AP)

| Parameter | Value | Source |
|---|---|---|
| Measures | pressure + temperature (piezo-resistive) | BMP p9 §3 |
| Range | 300-1250 hPa; full-accuracy temperature 0-65 °C; operational -40...85 °C | BMP p3, p7 Table 2 |
| Absolute accuracy | ±0.33 hPa (900 hPa, 25 °C); ±0.50 hPa (300-1100 hPa, 0-65 °C); ±0.40 hPa (900-1100 hPa, 25-40 °C); ±0.50 hPa (1100-1250 hPa) | BMP p7-8 Table 2 |
| Relative accuracy | ±0.03 hPa (= ±25 cm) per 10 kPa step, 700-1100 hPa, 25-40 °C | BMP p7 |
| TCO | ±0.62 Pa/K (900 hPa, 25-40 °C) | BMP p7 |
| Resolution / noise | 0.016 Pa (highest resolution); 0.9 Pa rms full bandwidth (25 Hz, ×32/×2), 0.02 Pa filtered; Table 8 noise by OSR × IIR | BMP p8, p16 Table 8 |
| Temperature | ±0.5 °C @ 25 °C, ±1.5 °C over 0-65 °C; "typically above ambient" (PCB + self-heating) | BMP p8 |
| Long-term / solder drift | ±0.16 hPa / 12 months; < ±0.8 hPa solder drift | BMP p8 |
| ODR | up to 200 Hz (osrs = 1); ODR accuracy ±2 % typ (±12 % max); IIR coefficient 0-127 | BMP p8, p10 |
| Supply / current | VDD 1.65-3.6 V, VDDIO 1.2-3.6 V; 3.2 µA @ 1 Hz; sleep 1.4 µA; peak 660-730 µA during pressure | BMP p3, p7 |
| Timing | first communication 2 ms after power-up; power-on from standby 3 ms; supply ramp ≥ 10 ms | BMP p8, p11 |
| Interface | I2C up to 3.4 MHz (0x76 SDO=GND / 0x77 SDO=VDDIO; SDO must not float), SPI 3/4-wire up to 10 MHz; chip ID 0x60 | BMP p3, p30, p40 |
| FIFO | 512 bytes | BMP p3, p18 |
| On the Nicla | on BHI260AP M1 SPI (CS MCSB1); delivered as BHI virtual sensor 129 (1/128 Pa) | NPIN L878-887; BHI Table 88 |
| Caveat | the Nicla pinout labels the part "BMP390L"; a "BMP390L" is not mentioned in the BMP390 datasheet (not found) | NPIN L885 |

---

## 7 Bosch BME688 (Nicla gas / T / RH / p, behind the BHI260AP) and BSEC outputs

### 7.1 Sensor

| Parameter | Value | Source |
|---|---|---|
| Measures | VOC / VSC / CO / H2 (MOX gas sensor with heater, ppb range), humidity, pressure, temperature | BME p2 |
| Operating range | -40...85 °C, 0-100 %RH (non-condensing), 300-1100 hPa | BME p3, p12 footnote 13 |
| Humidity | ±3 %RH (20-80 %RH, 25 °C, incl. hysteresis); hysteresis ±1.5 %RH; τ63 ≈ 8 s; resolution 0.008 %RH; noise 0.01 %RH; drift 0.5 %RH/yr; full accuracy 10-90 %RH, 0-65 °C | BME p3, p12 Table 8 |
| Pressure | abs ±0.6 hPa (300-1100 hPa, 0-65 °C); rel ±0.12 hPa; resolution 0.18 Pa; noise 1.4 Pa full BW / 0.2 Pa reduced BW; TCO ±1.3 Pa/K; long-term ±1.0 hPa/yr; solder drift -0.5/+1.2/+2.0 hPa | BME p3, p13 Table 9 |
| Temperature | ±0.5 °C (0-65 °C); resolution 0.01 °C; noise 0.005 °C; "typically above ambient" | BME p14 Table 10 |
| Gas | heater 320 °C: 9/12/13 mA; peak 15-18 mA; resolution 0.05-0.11 %; noise 1.5 % rms; response time (new sensor) ULP 92 s, LP 1.4 s, continuous 0.75 s | BME p8 Table 2 |
| Supply | VDD 1.71-3.6 V (optimised for 1.8 V), VDDIO 1.2-3.6 V; sleep 0.15 µA | BME p3, p7 Table 1 |
| Interface | I2C up to 3.4 MHz (0x76 / 0x77 by SDO; not floating), SPI 3/4-wire up to 10 MHz | BME p3, p45 |
| Start-up | 2 ms after VDD > 1.58 V and VDDIO > 0.65 V | BME p7 |
| Airflow | ~1 m/s toward the vent hole needed to observe the specified humidity response time | BME p12 footnote 16 |
| On the Nicla | on BHI260AP M1 SPI (CS MCSB2); raw T/RH/p/gas as virtual sensors 128-131; BSEC through Arduino's BHI firmware (ID 115) | NPIN L888-895; BHY2 SensorID.h L59 |

### 7.2 BSEC (Bosch Software Environmental Cluster) outputs and their meaning

| Output | Meaning (datasheet wording, condensed) | Source |
|---|---|---|
| IAQ (0-500) | Index for Air Quality, resolution 1, "especially recommended for mobile devices, since the auto-trim algorithm automatically adopts to different environments" | BME p8, p9 Table 3, p31 Table 20 |
| Static IAQ (s-IAQ) | IAQ without auto-trimming, "especially recommended for stationary devices" | BME p31 |
| CO2 equivalent (ppm) | "The sensor does not directly measure CO2, but derives this from the average correlation between VOCs and CO2 in human's exhaled breath" | BME p31 |
| b-VOC equivalent (ppm) | conversion into breath-VOC equivalents, scaled from lab tests with the Table 7 mixture (ethane 5 ppm, isoprene 10, ethanol 10, acetone 50, CO 15 ppm) | BME p11 Table 7, p31 |
| Accuracy status (0-3) | accuracy status of IAQ; "equal to zero during the power-on stabilization times of the sensor and is equal to 3 when the sensor achieves best performance"; Android compatible | BME p9 Table 3 footnote 4, p31 |
| Stabilization time status / run-in status | sensor "undergoing initial stabilization during its first use after production" / "ready after switch-on" | BME p31 |
| Gas (%) | 0 % = lowest air pollution ever measured, 100 % = highest, by individual sensor history | BME p31 |
| Gas scan result (%) | probability per class (standard scan: H2S vs non-H2S; up to 4 classes with BME AI-Studio) | BME p10 Table 4, p31 |
| Sensor-compensated T / RH / gas resistance | compensated for internal cross-influences | BME p31 |
| IAQ classes | 0-50 excellent, 51-100 good, 101-150 lightly polluted, 151-200 moderately, 201-250 heavily, 251-350 severely, > 351 extremely polluted | BME p11 Table 6 |
| Calibration | "BSEC software auto-calibrates the low and high concentrations applied during testing to IAQ of 50 and 200"; IAQ specs "valid after the calibration period of the BSEC algorithm"; specs deduced from sensors operated ≥ 5 days | BME p8, p9 footnote 5, p11 footnote 12 |
| Sensor-to-sensor | ±15 % ± 15 IAQ (all modes) | BME p9 Table 3 |
| Power modes | standard scan 1/10.8 s, 3.96 mA; ULP 3.3 mHz (300 s), 0.09 mA; q-ULP 0.33 Hz T/p/h + 3.3 mHz IAQ, 0.1 mA; LP 0.33 Hz (3 s), 0.9 mA; continuous 1 Hz, 12 mA (testing only) | BME p30 Table 19 |
| Arduino BSEC frame | iaq u16, iaq_s u16, b_voc_eq u16 ×0.01, co2_eq u24, accuracy u8, comp_t i16/256, comp_h u16/500, comp_g float | BHY2 sensors/DataParser.cpp L27-39 |

Legitimacy note for the dashboard (inference): CO2eq and bVOCeq are model estimates, not
measurements, and are meaningless while accuracy = 0; IAQ needs the BSEC calibration period. In a
sealed or vacuum environment the BMP390/BME688 pressure range (300 hPa minimum) is exceeded.

---

## 8 ams AS7265X 18-channel spectral chipset on the SparkFun Triad (SEN-15050)

### 8.1 Devices and channels

| Device | Role | Channels (nm, FWHM 20 nm, ±10 nm) | Calibration source (GAIN 16x, INT_T 166 ms, 3.3 V, 25 °C) | Source |
|---|---|---|---|---|
| AS72651 | master (I2C/UART slave to the host, I2C master to the others, SPI to flash) | R 610, S 680, T 730, U 760, V 810, W 860 (NIR) | incandescent ~1500 µW/cm² (300-1000 nm) | ASD p1, p13 Fig.14, p14 Fig.16, p15 notes |
| AS72652 | slave 1 | G 560, H 585, I 645, J 705, K 900, L 940 (VIS + NIR) | 3300 K white LED (G-J), incandescent (K), 940 nm LED (L) | ASD p1, p16 Fig.18 |
| AS72653 | slave 2 | A 410, B 435, C 460, D 485, E 510, F 535 (UV/blue) | LEDs 395/415/428 nm + 5600 K white | ASD p1, p18 Fig.20 |

* Sensitivity 35 counts/(µW/cm²) typ per channel; "The accuracy of the channel counts/µW/cm² is
  ±12%"; dark 5 counts (GAIN 64, 25 °C) (ASD p14-18 notes).
* Field of view ±20.5 ° (aperture-limited) "to deliver specified accuracy"; optics optimised for
  diffused light (ASD p19).
* Pre-calibrated with a specific light source; "Any operation other than these conditions might
  require a new calibration in the application" (ASD p1).
* Not designed for high-energy UV environments "including upward looking outdoor applications"
  (ASD p7).
* SparkFun library naming: `AS72651_NIR 0x00`, `AS72652_VISIBLE 0x01`, `AS72653_UV 0x02`; getters
  A-F read the UV device, G-L VIS, R-W NIR (SFH L80-82; SFC L150-226). The hookup guide's "AS72651 (UV),
  AS72652 (VIS), and AS72653 (NIR)" (ASG L77) contradicts the datasheet and the library.

### 8.2 Electrical and interface

| Parameter | Value | Source |
|---|---|---|
| Supply | 2.7-3.6 V with I2C (2.97-3.6 V with UART); VDD1/VDD2 from the same supply; abs max 5 V | ASD p7-8 Fig.6-7 |
| Operating current | 5 mA typ | ASD p8 |
| Logic | CMOS VIH 0.7 VDD, VIL 0.3 VDD; I/O abs max VDD + 0.3 V | ASD p7, p9 |
| I2C | slave address 0x49 (8-bit 0x92 W / 0x93 R), fast mode 400 kHz max, 7-bit; bus capacitance ≤ 400 pF; SDA input delay and SCL spike filtering | ASD p10 Fig.8, p23-24; SFH L30 |
| Physical registers | 0x00 STATUS (bit1 TX_VALID, bit0 RX_VALID), 0x01 WRITE, 0x02 READ; everything else is a virtual register (write with bit 7 set) | ASD p24-26; SFH L32-37 |
| Interface select | I2C_ENB pin: low = UART, high = I2C | ASD p4 |
| Interrupt | INT active low (AS72651) | ASD p5, p22-23 |
| Reset | RESN low > 100 ms | ASD p22 |
| Firmware | requires the external serial flash (SparkFun: 4 Mbit EEPROM read at power-on) | ASD p1, p19, p23; ASG L80 |
| Polling | SparkFun polls the virtual-register status every 5 ms | SFH L78 |

### 8.3 Measurement control

| Register / item | Fact | Source |
|---|---|---|
| 0x04 Configuration | GAIN bits 5:4: 00 = 1x, 01 = 3.7x (default), 10 = 16x, 11 = 64x; BANK bits 3:2: mode 0 / 1 (4 channels), 2 (all 6, continuous, default), 3 (one-shot of mode 2); DATA_RDY bit 1; INT enable bit 6 | ASD p31 Fig.29 |
| BANK timing | mode 0/1 data ready after one integration time; mode 2 after two; minimum IT for one bank 2.8 ms | ASD p19, p23 |
| 0x05 Integration time | "Integration time = <value> × 2.8 ms ... value 1-255"; table: 0x00 = 1 cycle 2.78 ms (max ADC 1023), 0x01 = 5.56 ms, 0x11 = 50 ms, 0x40 = 181 ms (65535), 0xFF = 256 cycles 711 ms; full scale needs ≥ 64 × 2.78 ms; default 20 | ASD p32 Fig.30 |
| ADC | 16-bit; "For every 2.78ms of integration time, the maximum sensitivity value increases by 1024 counts" | ASD p19, p32 |
| 0x06 Temperature | °C of the device selected by DEV_SEL, -127...127, -128 = error; internal sensor accuracy ±8.5 °C | ASD p8, p32 Fig.31 |
| 0x07 LED config | LED_DRV current 12.5 / 25 / 50 / 100 mA (sink); LED_IND 1 / 2 / 4 / 8 mA; per DEV_SEL device | ASD p22, p33 Fig.32 |
| 0x08-0x13 raw | 16-bit big-endian per channel slot | ASD p28 |
| 0x14-0x2B calibrated | 32-bit IEEE-754 float, big-endian, read in ascending address order | ASD p27-30 |
| 0x4F DEV_SEL | selects device 0/1/2; bits 4-5 report detected slaves (SparkFun `begin()` fails if none) | ASD p30; SFC L43-46 |
| SparkFun defaults | `begin()`: re-`begin()`s the Wire port ("resets any setClock()"), bulbs 12.5 mA off, indicator 8 mA on, integration 49 (comment "50 × 2.8 ms = 140 ms"), gain 64x, mode 3 one-shot, interrupt on | SFC L35-66 |
| SparkFun integration formula | "Time will be 2.8ms * [integration cycles + 1]" | SFC L376-383 |

Datasheet inconsistency: the overview table maps 0x08 to "R, G, A" (ASD p28) while the per-register
pages say "Channel R or J or D" (ASD p33-35); the SparkFun library follows the overview (SFH L52-65).
The integration-time text ("value × 2.8 ms, value 1-255") and its own table ((value + 1) × 2.78 ms) also
disagree (ASD p32). Units of the calibrated float: not stated (not found).

### 8.4 SparkFun board

| Item | Fact | Source |
|---|---|---|
| Connection | 2 Qwiic connectors or 4 pins GND / 3.3V / SDA / SCL; "3.3V compatible so don't use with a 5V Arduino Uno without proper conversion" | ASG L89-90 |
| Pull-ups | the front "I2C" jumper connects the pull-ups (opening it removes them from TX/RX for UART use); value not found | ASG L106-108 |
| UART | open the I2C jumper and close JP2; 115200 baud; AT commands | ASG L106-114 |
| LEDs | 5700 K white, 405 nm UV, 875 nm IR; individually switchable; "bulb" footprints ≤ 100 mA | ASG L83-85 |
| LED wiring (library) | white on AS72651 (x51), IR on x52, UV on x53 | SFH L84-86 |
| Status / power LED | blue status LED (library can disable), red power LED (cut jumper to disable) | ASG L87 |
| IDs | device type 0x41, HW version 0x40, firmware major "currently 0x0C" | ASG L149-151; ASD p31 Fig.27 |

---

## 9 PNI RM3100 geomagnetic sensor (MagI2C + 3 coils)

### 9.1 Performance (RMM Table 3-1, printed p. 5)

| Cycle count | 50 | 100 | 200 (default) |
|---|---|---|---|
| Range | ±800 µT | ±800 µT | ±800 µT |
| Gain (LSB/µT) | 20 | 38 | 75 |
| Sensitivity (nT/LSB) | 50 | 26 | 13 |
| Noise (nT) | 30 | 20 | 15 |
| Repeatability over 200 µT (nT) | 15 | 8 | 8 |
| Max single-axis sample rate (Hz; ÷3 for 3 axes) | 1600 | 850 | 440 |
| Single-axis average current @ 24 Hz (= 3-axis @ 8 Hz) | 70 µA | 135 µA | 260 µA |

Also: noise density 1.2 nT/√Hz at max single-axis rate; hysteresis 15 nT; linearity 0.5 % over 200 µT;
Rb 121 Ω, REXT 33 kΩ, 180 kHz oscillation; -40...85 °C; values at 3.0 V, room temperature (RMM printed
p. 5). Linear regime 200 µT, Earth's field ~50 µT; field falls as 1/distance³ (RMM printed p. 18).
Useful cycle-count range ~30 ... ~400 (quantisation / noise) (RMM printed p. 28).
"Measurements are stable over temperature and inherently free from offset drift" (RMM printed p. 4).

### 9.2 Electrical (Tables 3-4, 3-5, printed p. 7)

| Parameter | Value |
|---|---|
| AVDD, DVDD | 2.0 / 3.0 / 3.6 V (abs max 3.7 V); DVDD must be on whenever AVDD is on; |DVDD-AVDD| ≤ 0.1 V; ripple ≤ 50 mVpp |
| Input levels | VIH 0.7 DVDD, VIL 0.3 DVDD; input pin voltage ≤ AVDD/DVDD |
| Output current | IOH -1 mA, IOL 1 mA |
| Idle current | 1 µA (automatic idle when not measuring or communicating) |

(RMM printed p. 7, p. 19, p. 27.)

### 9.3 Interface

| Item | Fact | Source |
|---|---|---|
| Selection | I2CEN pin 22: HIGH = I2C, LOW = SPI | RMM Table 4-1 printed p. 20; §4.3.1 p. 21 |
| I2C address | 0b01000 + bit1 + bit0 -> 0x20-0x23; "Standard, fast, fast plus, and high speed modes" (UM10204 rev 03); SCL "should be 1 MHz or less" | RMM §4.5 printed p. 25, §4.3.3 p. 22 |
| Address pins | Table 4-1: pin 3 = SSN / SA0 (bit 0), pin 28 = MISO / SA1 (bit 1); §4.5 example "pin 3 HIGH and pin 28 LOW results in an address of 0b0100001" agrees; §4.3.3 text says the opposite (SA1 = pin 3, SA0 = pin 28) | RMM printed p. 20, 22-23, 25 |
| SPI | SCLK ≤ 1 MHz; CPOL = CPHA = 0 or 1; first byte R/W + 7-bit address; status returned on the address byte; MISO tri-state when SSN high; timing Table 4-2 (50 ns setup/hold, 100 ns SSN) | RMM printed p. 21-25 |
| DRDY (pin 23) | goes HIGH when the measurement completes; cleared by reading results or by a write (HSHAKE DRC0/DRC1 defaults); optional (STATUS 0x34 bit 7 also tells) | RMM printed p. 21, 32, 35 |
| Registers | POLL 0x00 (bits PMX/PMY/PMZ = 4/5/6), CMM 0x01 (START bit 0, DRDM, CMX/CMY/CMZ), CCX/CCY/CCZ 0x04-0x09 (default 0x00C8 = 200), TMRC 0x0B (default 0x96 ≈ 37 Hz), MX/MY/MZ 0x24-0x2C (24-bit two's complement, -8388608 ... 8388607, read from 0xA4), BIST 0x33, STATUS 0x34, HSHAKE 0x35 (default 0x1B), REVID 0x36 | RMM Table 5-1 printed p. 27, p. 29-35 |
| Read address | register | 0x80 for reads (I2C: write the register address, then read) | RMM printed p. 26-27 |
| Mode interplay | writing POLL during CMM is ignored and NACKs (HSHAKE NACK1); reading CMM or writing TMRC terminates CMM; reading results with DRDY = 0 sets NACK2 | RMM printed p. 26, 29, 35 |
| Self-test | BIST: STE bit 7 runs on the next POLL write; XOK/YOK/ZOK; BW timeout 30/60/120 µs; BP LR periods 1/2/4 | RMM printed p. 33-34 |
| Layout | NED orientation per Figure 4-4; Sen-Z-f polarity indicator matters; MagI2C within 10 cm of the coils; keep tantalum capacitors and ferrous/current sources away | RMM printed p. 17-19 |

### 9.4 The PNI RM3100 breakout / test board pin-out (RMQ p2, p5-6)

| Pin | Name (SPI / I2C) | I2C wiring per PNI | SPI wiring per PNI |
|---|---|---|---|
| 1 | SCK / SCL | host SCL | host SCK |
| 2 | SO / SA1 (bit 1) | GND (0) or 3V3 (1) | host MISO |
| 3 | SI / SDA | host SDA | host MOSI |
| 4 | SSN / SA0 (bit 0) | GND (0) or 3V3 (1) | host CS |
| 5 | DRDY | optional GPIO input | optional GPIO input |
| 7 | AVSS | GND | GND |
| 10 | I2CEN | 3V3 (HIGH = I2C) | GND |
| 12 | DVDD | 3V3 | 3V3 |
| 13 | AVDD | 3V3 | 3V3 |
| 14 | DVSS | GND | GND |
| 6, 8, 9, 11 | NC | do not connect | do not connect |

"Pin 1 is next to the top left white triangle"; pins run counter-clockwise from top left (RMQ p2).
"You may have to enable the internal pullup resistors ... You can also use external 4.7k Ohm pull up
resistors on the SCL and SDA lines" (RMQ p5) - the breakout carries no I2C pull-ups according to this
note (inference). PNI's example uses address 0x20 "with Pin 2 and Pin 4 set to LOW" (RMI L4). This
breakout pairing (SO = SA1, SSN = SA0) confirms Table 4-1 of the manual.

### 9.5 RM3100 caveats

1. SA0/SA1 contradiction in RMM §4.3.3 (9.3); 0x20 (both low) and 0x23 (both high) are immune to it.
2. Table 3-1 says "divide by 3 for max. 3-axis sample rate" (440/3 ≈ 147 Hz at CC 200) while §5.2.1
   says "if the cycle count is set to 200D, then the maximum 3-axis update rate is ~430 Hz" (RMM printed
   p. 5 vs p. 30): contradiction - VERIFY by timing DRDY.
3. TMRC table rows 0x9E (~6.7 s, "~0.015 Hz") and 0x9F (~13 s, "~0.075 Hz") are internally inconsistent
   (6.7 s ↔ 0.15 Hz) (RMM Table 5-4 printed p. 31).
4. The user manual lists the PNI evaluation board, but the third-party breakout the owner uses is not
   identified anywhere (docs/03 L822 "VERIFY with your supplier"); only PNI's own board is described (RMQ).

---

## 10 M5Stack CardKB (Unit CardKB, SKU U035)

| Item | Fact | Source |
|---|---|---|
| Status | EOL; successor "Unit CardKB v1.1" | CKB L19-28 |
| MCU | page title "MEGA328P"; the pin-map section (v1.1) refers to an "ATMega8A ISP Download Interface" | CKB L2, L109 |
| Keys | 50 keys, 1 RGB LED; Sym / Shift / Fn combinations (double-tap locks Sym/Shift) | CKB L42-46, L92-96 |
| Interface | I2C, address 0x5F; single read register "keyboard_value_reg[0] 0x5F R KEYBOARD VALUE" | CKB L31, L48, L119-125 |
| Connector | HY2.0-4P (Grove): Black GND, Red 5V, Yellow SDA, White SCL ("PORT.A") | CKB L34, L97-108 |
| Supply | 5 V per the pin map | CKB L106 |
| I2C logic level, pull-ups, current | not found | - |
| Size / weight | 88.0 × 54.0 × 5.0 mm, 17.0 g | CKB L49-52 |
| On the UNO Q (inference) | Its SDA/SCL must not exceed the UNO Q pin limits: D20/D21 are FT (5 V tolerant as inputs/open-drain), A4/A5 are 3.3 V-only, Qwiic tolerance is not stated. The project's established options are 3.3 V supply (option A) or 5 V via a BSS138 shifter (option B) | UQ L1341-1344, L2310-2321; docs/03_Hardware_Integration.md L627-634 |

---

## 11 EBYTE ground radio: E15-USB-T2 adapter + E22-400T33D (and T37S / TBH-02 cross-check)

### 11.1 E15-USB-T2 (USB test board)

| Item | Fact | Source |
|---|---|---|
| USB-UART chip | "Please install the USB adapter driver (CP2102)" | E51 p13 §5 step 1 |
| Mode jumpers | "Plug mode-select jumper in the USB test board (M1 = 0, M0 = 0)" for normal mode; "Pull the M0, M1 jumper out" for the E51's mode 3 (= fitted jumper = 0, removed = 1) | E51 p8 §3.6, p13 §5 step 2 |
| Supply jumper | "Optional power supply, 3.3V or 5V"; "3.3V or 5V are available for jumper" | E51 p8 §3.6, p13 |
| Board identification in the project | "EBYTE E15-USB-T2 V1.2 (CP2102 USB-UART)", supply jumper 5V, `/dev/ttyUSB0` | docs/21_Test_Records.md L638, L652 |
| AUX on the adapter headers | not found ("AUX is not on its headers; not verified") | docs/12 L912 |
| Current capacity of the adapter's supply path / 3.3 V regulator | not found | - |
| E22 mode mapping (inference from T33 §6 + E51 jumper logic) | E22 normal = M1 0 / M0 0 (both caps fitted); E22 configuration = M1 1 / M0 0 (cap on M0 only); both removed = E22 deep sleep (not the E51's configuration mode) | T33 p13 §6; docs/21 L652 |

### 11.2 E22-400T33D (SX1262, 2 W)

| Parameter | Min | Typ | Max | Note | Source |
|---|---|---|---|---|---|
| Supply | 3.3 | 5.0 | 5.5 V | "≥4.7V Guaranteed output power"; > 5.5 V may burn the module | T33 p6 §2.1-2.2 (printed p. 5) |
| UART level | - | 3.3 V | - | "There is a risk of burnout when using the 5V level"; with a 5 V MCU use level conversion; a 1k-5.1k series resistor "not recommended, there is still a risk of damage" | T33 p6, p9, p22 |
| TX current @ 33 dBm | 850 | 1000 | 1200 mA | "Instantaneous power consumption@33dBm" | T33 p6 (layout L210-211) |
| RX current | 11 | 13 | 15 mA | | T33 p6 |
| Sleep | | 2 µA | | software shutdown (mode 3) | T33 p6 |
| TX power | 32 | 33 | 34 dBm | | T33 p6 |
| Sensitivity | -126 | -128 | -130 dBm | at 2.4 kbps air rate | T33 p6 |
| Air rate | 2.4k | 2.4k | 62.5k | REG0 codes 000/001/010 are all 2.4k; 4.8 / 9.6 / 19.2 / 38.4 / 62.5k | T33 p7, p17 |
| Frequency | 410.125 | | 493.125 MHz | CH 0-83, f = 410.125 + CH × 1 MHz; factory default 433.125 MHz (CH 0x17) | T33 p6, p18, p19 |
| Packet / buffer | | 240 B | | sub-packet 240/128/64/32; cache 1000 B | T33 p7, p17 |
| UART | 1200-115200 baud, 8N1 default 9600; configuration only at 9600 8N1 | | | T33 p15-17 |
| Reference range | 16 km (clear, 5 dBi, 2.5 m height) | | | T33 p7 |
| Size / RF | 37 × 60 × 7.6 mm, SMA ~50 Ω, 2.54 mm pin header | | | T33 p7 |
| Temperature | -40 ... 85 °C | | | T33 p6 |

Pins (T33 p8-9 §3): 1 M0, 2 M1 (inputs, "very weak pull-up", must not float), 3 RXD, 4 TXD, 5 AUX (low
during power-on self-test; can float), 6 VCC, 7 GND. Modes (T33 p13 §6): M1 M0 = 00 transparent,
01 WOR, 10 configuration (registers over UART at 9600 8N1), 11 deep sleep. Mode switch: wait for AUX
high then 2 ms (T33 p13 §6.1). RSSI: REG1 bit 5 enables ambient-noise RSSI read (C0 C1 C2 C3 + addr +
len; dBm = -(256 - RSSI)); REG3 bit 7 appends an RSSI byte after each received packet (T33 p17-18).
Hardware design: regulated supply, ≥ 30 % margin, keep away from switching supplies; antenna vertical,
not inside metal (T33 p21-22).

Manual errors: the running header reads "E22-400T30D User Manual" (T33 p2-22); REG1 power table lists
"30dBm (default) / 27 / 24 / 21 dBm" (T30D values) while the factory-default table says 33 dBm for the
E22-400T33D (T33 p17 vs p19) - the T33D's four power codes are not found (VERIFY with C1 read-back and a
power meter); pin 6 "VCC" is described as "Connect to the internal reference ground" (copy error,
T33 p9).

### 11.3 E22-400T37S and E22-400TBH-02 (cross-check only; the full analysis belongs to the T37S report)

| Item | Fact | Source |
|---|---|---|
| T37S supply | 4.5-15 V (> 15 V may burn); "≥ 5 V can guarantee output power, the lower the voltage, the greater the supply current" | T37 p7 §2.1-2.2 (printed p. 6) |
| T37S TX current | 2900 / 3100 / 3300 mA at 5 V ("at least 3.5A" supply); 900 / 1100 / 1300 mA at 12 V ("at least 1.5A") | T37 p7 |
| T37S RX / sleep | 43 mA at 12 V / 2 µA | T37 p7 |
| T37S TX power / sensitivity / air rate | 36 / 37 / 37.5 dBm; -125 / -126 / -127 dBm at 2.4 kbps; 0.3k-62.5k | T37 p7 |
| T37S logic | 3.3 V TTL; "Using 5V level may cause burnout risk, so a level conversion circuit is required" | T37 p7, p10 |
| TBH-02 supply | DC header or green terminal, 5-12 V on the connectors / 4.5-15 V module range; "green terminal is invalid when DC head is inserted" | TBH p4, p6, p8 |
| TBH-02 USB | Type-C USB port with a CH340X USB-UART ("install the USB adapter board driver (CH340X)"); M0/M1 jumper caps; 3.3 V / 5 V power jumper | TBH p6, p10 |
| Ultra-long-range note (inference) | the T33D cannot go below 2.4 kbps (REG0), the T37S reaches 0.3 kbps (T37 p7) - relevant to the owner's "ultra long range" requirement | T33 p17; T37 p7 |

---

## 12 Cross-component interfacing matrix (for the wiring chapters)

| Device | Supply | Logic | Bus / address | Max SCL | Pull-ups | UNO Q compatibility |
|---|---|---|---|---|---|---|
| Nicla Sense ME (ARU firmware) | ESLOV pin 1 "5V" (or VIN 3.5-5.5 V / USB 4.8-5.5 V) | VDDIO_EXT = 3.3 V once `BHY2.begin()` ran | I2C target 0x2A on `Wire` (J2 1/2 = J5 4/3) | nRF TWI: not stated in NDS (not found) | not found | 3.3 V I2C pins OK; supply from a 5 V rail, never from 3.3 V (NDS L208-213) |
| RM3100 (MagI2C) | 2.0-3.6 V (3.3 V) | 0.7/0.3 DVDD | I2C 0x20-0x23 (I2CEN high) or SPI ≤ 1 MHz | 1 MHz (fast-plus / HS supported) | none on PNI board (inference RMQ p5) | 3.3 V only; never 5 V (abs max 3.7 V) |
| AS7265X (SparkFun) | 3.3 V (2.7-3.6 V) | 0.7/0.3 VDD | I2C 0x49 | 400 kHz | on board via "I2C" jumper | 3.3 V Qwiic or 3.3 V pins |
| CardKB | 5 V (pin map) | not published | I2C 0x5F | not found | not found | level shifting decision needed (section 10) |
| E22-400T33D | 3.3-5.5 V, ≥ 4.7 V for 33 dBm, 1.2 A peak | 3.3 V UART | UART 9600 8N1 default | - | M0/M1 weak pull-ups | via E15-USB-T2 (CP2102) on USB, or 3.3 V UART pins |

Inference for the separate-bus requirement (owner item 3): on the spacecraft UNO Q `Wire` (D20/D21) is
taken by the E22 UART (USART3) (CFG L54-63), leaving `Wire1` (Qwiic, I2C4) and `Wire2` (A4/A5, I2C3)
as I2C buses, plus SPI2 (D10-D13, or the JSPI header) for an RM3100 in SPI mode (I2CEN low). Datasheet
limits that matter: A4/A5 pull-ups to 3.3 V only (UQ L1343); RM3100 SPI ≤ 1 MHz (RMM printed p. 22); the
RM3100 and the Nicla never need the same address space (0x20-0x23 vs 0x2A), so the split is about fault
isolation and cable length, not address conflicts.

---

## 13 Datasheet inconsistencies and errata (collected)

| # | Document | Issue | Where |
|---|---|---|---|
| E1 | UNO Q | SKU-to-memory pairing not in the text | UQ L437-438 |
| E2 | UNO Q | 5 V pins: "pass-through, power only" (pin maps) vs "input up to 3 A" (§3.1) vs "5V_SYS" (§9 JSPI) | UQ L681-685, L1349, L2072-2076, L2357-2362 |
| E3 | UNO Q | JMISC pin 57 "+1V8 (IN)" vs "1.8 V rail is also available on JMISC" | UQ L1684-1688 vs L887 |
| E4 | Nicla | wrong reference designators (BHI260 "U2", ANNA "U5"); PMIC "BQ2512BAYFPR" | NDS L416-422, L435 |
| E6 | AS7265X | channel slot naming overview vs register pages; integration-time formula vs table | ASD p28 vs p33-35; p32 |
| E7 | AS7265X | hookup guide swaps UV/NIR device names | ASG L77 vs ASD p1, SFH L80-82 |
| E8 | RM3100 | SA0/SA1 pins swapped in §4.3.3; 3-axis max-rate contradiction; TMRC 0x9E/0x9F rates | RMM printed p. 20-25, p. 5 vs 30, p. 31 |
| E9 | E22-400T33D | T30D header and power table copied into the T33D manual; VCC pin description | T33 p2-22, p17 vs p19, p9 |
| E10 | E22-xxxT37S | "Supports 4.5 ~ 1.5 V wide voltage" (typo for 15 V) | T37 p6 |
| E11 | BHI260AP | Table 88 references "Table 79: Overview of FIFO Event IDs" inside Tables 89-91 (wrong table number) | BHI L9092, L9132 |
| E12 | Arduino_BHY2 | barometer factor 0.0078 vs 1/128 (handled in ARU) | BHY2 SensorID.h L190; ARU L41-45 |
| E13 | Nicla pinout | BMP390 labelled "BMP390L" | NPIN L885 |
| E14 | CardKB | page title MEGA328P vs v1.1 ATMega8A | CKB L2, L109 |

## 14 Not found (searched, no source)

1. UNO Q output current limits of +3V3 OUT, IOREF, Qwiic 3.3 V, JANALOG/JSPI/JMISC 5 V pins; Q2801
   current limit; on-board I2C pull-ups on any header; 5 V tolerance of the Qwiic pins PD12/PD13.
2. UNO Q SKU label for each memory size in the text.
3. Nicla: ESLOV pin-1 voltage range and current; J5 translator part; pull-ups on J2/J5; board-level axis
   drawing; nRF TWI maximum speed in the datasheet; ARU current at 5 V.
4. BHI260AP default dynamic ranges on the Nicla firmware; the list of virtual sensors present in the
   Nicla's BHI firmware (needs `hasSensor` at run time).
5. AS7265X calibrated-value unit; SparkFun pull-up value.
6. RM3100: the owner's breakout part number and its pull-ups.
7. CardKB: logic level, pull-ups, current.
8. E15-USB-T2: datasheet of its own; current capability of its supply path; AUX on its headers.
9. E22-400T33D: the four REG1 power codes for the 33 dBm version.
10. USB-C hub per-port current and PD pass-through rating (owner's hub, unidentified).

## 15 Open questions / VERIFY

1. Confirm ABX00173 = 4 GB / 32 GB from the box or `free -h` / `lsblk` on the ground board.
2. CP2102 (`cp210x`) driver present in the UNO Q Debian image and `/dev/ttyUSB0` visible inside an
   App Lab App on the UNO Q (the VENTUNO Q result in docs/20 V-31 does not transfer automatically).
3. Power budget of the ground UNO Q: 5 V / 3 A PD in through the hub must cover the UNO Q plus the
   E15-USB-T2 with the E22 transmitting at up to 1.2 A (T33 p6) - measure VCC at the E22 during an
   uplink (docs/20 V-69), or give the E22 its own 5 V supply.
4. Which hub ports supply power while the UNO Q is the USB host (data) and a PD sink (power); whether
   the hub's PD pass-through negotiates the 5 V / 3 A contract the UNO Q requests (UQ L685).
5. RM3100 3-axis update rate at CC 200 (147 Hz or ~430 Hz) by timing DRDY.
6. E22-400T33D REG1 power codes for the 33 dBm module.
7. Nicla J5 levels (3.3 V when running, no back-feed when unpowered) and continuity J5-3/4 ↔ J2-2/1
   (shared with R3 4.2-4.3).
8. Whether the BHI260AP's M3 I2C master (wired to the same P0_22/P0_23 nets, NPIN L899-903) is ever
   active in Arduino's BHI firmware (logic-analyser check of the external bus).
