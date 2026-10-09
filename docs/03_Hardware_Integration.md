# 03 - Hardware Integration (wiring, pins, power)

Every pin here matches the code: spacecraft `spacecraft/vgq1_flight/sketch/src/config.h`,
ground MCU `ground/station_io/sketch/sketch.ino`. Change both together.

Sources for board facts: EBYTE *E22-xxxT37S User Manual* v1.5, EBYTE *E22/E32-xxxTBH-02 User
Manual* v1.0, Arduino *Nicla Sense ME (ABX00050)* and *Nicla Sense Env (ABX00089)* datasheets,
the Arduino UNO Q / VENTUNO Q board definitions (ArduinoCore-zephyr variants), PNI RM3100 and
SparkFun AS7265X documentation. Anything not stated by a datasheet is marked *verify*.

---

## 1 System block diagram

```
          VGQ-1 SPACECRAFT (transmitter)                         DSS-Q1 GROUND STATION (receiver)
 ┌──────────────────────────────────────────────┐        ┌─────────────────────────────────────────────┐
 │  Arduino UNO Q                                │        │  Arduino VENTUNO Q                           │
 │  ┌─────────────────┐   Router   ┌───────────┐ │        │ ┌──────────────────────┐ Router ┌──────────┐ │
 │  │ STM32U585 MCU   │◄─Bridge──►│ QRB2210   │ │        │ │ QCS8275 Linux CPU    │◄Bridge►│ STM32H5  │ │
 │  │ flight software │  (RPC)    │ Linux EGSE│ │        │ │ GDS + web dashboard  │ (RPC)  │ station  │ │
 │  │ CCSDS TM/TC     │           │ recorder  │ │        │ │ CCSDS decode, COP-1  │        │ I/O + RCU│ │
 │  └──┬─────────┬────┘           └───────────┘ │        │ └──────────┬───────────┘        └─┬──┬──┬──┘ │
 │     │UART3    │ Qwiic I2C (Wire1, 3.3 V)      │        │            │USB (CH340X)          │  │  │    │
 │  ┌──▼──────┐  ├──► AS7265X        0x49       │        │       ┌────▼─────┐   M0/M1/AUX     │  │  │    │
 │  │E22-400  │  ├──► RM3100 OB      0x20       │  433   │       │ E22-400  │◄────────────────┘  │  │    │
 │  │T37S on  │  ├──► RM3100 IB opt. 0x23       │  MHz   │       │ T37S on  │  CardKB #1 (Wire)──┘  │    │
 │  │TBH-02   │◄─┼──► Nicla Sense Env 0x21      │◄──────►│       │ TBH-02   │  CardKB #2 (Wire1)    │    │
 │  │ 5 W     │  └──► Nicla Sense ME  0x2A      │  LoRa  │       │ 5 W      │  AOS/ALARM/UPLINK LEDs,│    │
 │  └─────────┘                                │        │       └──────────┘  buzzer ───────────────┘    │
 └──────────────────────────────────────────────┘        └─────────────────────────────────────────────┘
       downlink: CADUs (ASM + RS(255,223) + randomized TM frame)  ───────────►
       uplink:   CLTUs (BCH) carrying SDLS-authenticated TC frames ◄───────────
```

---

## 2 Logic levels and buses

| Board | Logic | Notes |
|---|---|---|
| UNO Q (STM32U585) | 3.3 V | Qwiic connector = `Wire1` (I2C4: PD12 SCL / PD13 SDA) |
| VENTUNO Q (STM32H5F5) | 3.3 V | UNO-style header SDA/SCL = `Wire`; `Wire1` = second I2C (*verify* pins on your board) |
| E22-400T37S | 3.3 V TTL | EBYTE: "Using 5V level may cause burnout risk" |
| AS7265X (SparkFun) | 3.3 V | Qwiic |
| RM3100 | 3.3 V | I2C mode, address 0x20 + (SA1<<1 \| SA0) |
| Nicla Sense ME | VDDIO_EXT = 3.3 V | header I/O level-translated to VDDIO_EXT; the ARU firmware enables 3.3 V (BHY2.begin) |
| Nicla Sense Env | 3.3 V | "operates at +3.3 VDC ... pins are +5 VDC tolerant" |
| CardKB (M5Stack v1.1) | 5 V supply | I2C level not documented by M5Stack: use a BSS138 level shifter to 3.3 V |

---

## 3 Spacecraft (UNO Q) wiring

### 3.1 Power

```
 12 V battery / supply (>= 3 A; 3S Li-ion 9.0-12.6 V is inside the E22's 4.5-15 V)
   │
   ├──[3 A fuse]──┬──────────────────────────────────► E22-400TBH-02 DC jack / green terminal
   │              │                                     (12 V: TX 1.1 A typ, 1.3 A max, RX 43 mA)
   │              └──[buck 12 V -> 5 V, >= 3 A]──┬────► UNO Q USB-C (5 V)
   │                                             ├────► Nicla Sense ME  J2 pin 9 VIN (3.5-5.5 V)
   │                                             └────► Nicla Sense Env ESLOV pin 1 5V (or IN pin, 2.3-6.5 V)
   └── GND ── common ground to every board ──────────────────────────────────────────────────────
 Qwiic red wire = 3.3 V from the UNO Q: powers AS7265X and RM3100(s) only.
```

Notes: EBYTE recommends >= 47 uF low-ESR at the module supply (the TBH-02 board carries its own
input capacitors). If you use a bare module, add it.

### 3.2 E22-400TBH-02 board header and jumpers (from the EBYTE TBH-02 manual)

```
             top row:   11 3V3  12 GND  13 SWCLK 14 SWDIO 15 NC 16 NC 17 NC 18 485-EN 19 STATE 20 GND
   [USB-C]  [RXD cap][TXD cap]  <- connect the CH340X USB-serial chip to the radio
   [DC jack][green terminal]                      ...E22-400T37S module...                 [SMA]
             bottom row: 1 GND  2 GND  3 RESET  4 AUX  5 TXD  6 RXD  7 M1  8 GND  9 GND  10 M0
                                                                 [cap 7-8]   [cap 9-10]
   cap fitted = pin to GND (0); removed = pulled up (1).   M1 M0: 00 normal, 01 WOR, 10 config, 11 sleep
```

### 3.3 UNO Q <-> spacecraft radio

**Remove all four caps (M0, M1, RXD, TXD).**

| E22 board header | Direction | UNO Q pin | MCU signal | Notes |
|---|---|---|---|---|
| 1 GND | - | GND | - | common ground |
| 4 AUX | E22 -> UNO Q | D7 | PB2 | LOW = busy (self-test, buffering, transmitting) |
| 5 TXD | E22 -> UNO Q | header **SDA** | PB11 = USART3 RX (`Serial3`) | used as UART, not I2C |
| 6 RXD | UNO Q -> E22 | header **SCL** | PB10 = USART3 TX | |
| 7 M1 | UNO Q -> E22 | D3 | PB0 | + 10 kOhm to 3.3 V |
| 10 M0 | UNO Q -> E22 | D2 | PB3 | + 10 kOhm to 3.3 V |
| 3 RESET, 11 3V3, 19 STATE | - | not connected | | EBYTE: 3V3 "no need to care" |

Why not D0/D1: on the UNO Q, `Serial1` (D0/D1) is also the Zephyr console; a console message would
be transmitted over the air and corrupt a frame. `config.h` can switch to D0/D1
(`VGQ_RADIO_ON_SERIAL1 1`) if you ever need it. The 10 kOhm pull-ups hold M0 = M1 = 1 (deep
sleep) while the MCU boots, so the 5 W PA can never key with floating mode pins.

### 3.4 Sensor bus (Qwiic, `Wire1`, 3.3 V, 100 kHz)

```
 UNO Q Qwiic ──Qwiic── AS7265X ──Qwiic── (Qwiic-to-jumper) ──┬── RM3100 OUTBOARD (boom tip, 0x20)
   (3.3 V, GND,          0x49                                ├── RM3100 INBOARD  (mid-boom, 0x23, optional)
    SDA, SCL)                                                ├── Nicla Sense Env (ESLOV cable)      0x21
                                                             └── Nicla Sense ME  (J2 header)        0x2A
 Qwiic colours: black GND, red 3.3 V, blue SDA, yellow SCL.
```

| Instrument | Address | Wires | Power | Library / firmware |
|---|---|---|---|---|
| SparkFun AS7265X | 0x49 | Qwiic cable | 3.3 V (Qwiic) | SparkFun Spectral Triad AS7265X 1.0.5 |
| RM3100 outboard | 0x20 | VCC, GND, SDA, SCL; **SA0 = GND, SA1 = GND**; I2C mode selected (I2CEN high on breakouts that expose it) | 3.3 V | flight driver `drivers/sensors.cpp` |
| RM3100 inboard (optional) | 0x23 | as above with **SA0 = 3.3 V, SA1 = 3.3 V** | 3.3 V | auto-detected |
| Nicla Sense Env | 0x21 | ESLOV cable: pin 3 SCL, pin 4 SDA, pin 5 GND to the bus; pin 1 5V to the 5 V rail; pin 2 INT not used | 5 V via ESLOV | Arduino_NiclaSenseEnv 1.0.1 (factory firmware on the board) |
| Nicla Sense ME | 0x2A | J2 header: pin 1 SDA, pin 2 SCL, pin 6 GND to the bus; pin 9 VIN to 5 V | 5 V on VIN | `spacecraft/nicla_sense_me_aru` (flash with Arduino IDE) |

Address plan (no conflicts, no multiplexer): 0x20, 0x21, 0x23, 0x2A, 0x49. **Never strap an RM3100
to 0x21.** The RM3100's address pins are labelled SA0/SA1 on most breakouts; on PNI's bare module
they share pins with the SPI interface - follow your breakout's silkscreen (*verify*).

Pull-ups: every SparkFun Qwiic board has its own I2C pull-ups (with a cut-jumper). Five boards in
parallel can make the pull-up too strong; if the bus is unreliable, open the pull-up jumper on all
but one or two boards.

Boom: mount the RM3100(s) on a non-magnetic boom (wood, GRP, aluminium tube - not steel), as far
as practical from the radio, the battery and DC wiring. Use a twisted cable (SDA+GND, SCL+GND,
3.3 V+GND), at most ~1 m at 100 kHz. Enter the boom distances in `station.toml` `[science]`:
with two RM3100s the GDS separates the spacecraft's own field (falls as 1/r^3) from the ambient
field (the classic dual-magnetometer method).

### 3.5 Spacecraft pin summary (UNO Q)

| UNO Q pin | Function |
|---|---|
| SDA / SCL header | `Serial3` UART to the E22 (RX / TX) |
| D2, D3 | E22 M0, M1 |
| D7 | E22 AUX |
| Qwiic | sensor I2C bus (`Wire1`) |
| USB-C | 5 V power + App Lab |
| built-in 13x8 LED matrix | status lights (see [06 Displays](06_Displays.md)) |

---

## 4 Ground station (VENTUNO Q) wiring

### 4.1 Power

* VENTUNO Q: its own supply, >= 60 W (12 V/5 A or 24 V/3 A barrel jack) per the Arduino datasheet;
  keep the board fan running.
* Ground E22 board: separate 12 V >= 1.5 A supply into its DC jack (the uplink is also 5 W).
* Common ground through the USB cable and the RCU GND wire.

### 4.2 Ground radio

| Connection | Detail |
|---|---|
| E22 board USB-C -> VENTUNO Q USB | data (CH340X, appears as `/dev/ttyUSB0`) - **keep the RXD/TXD caps fitted** |
| Antenna | SMA; on the bench the attenuator chain of [00 Part G](00_Beginner_Setup_Guide.md) |
| M0 / M1 caps | remove only if the Radio Control Unit below is wired; otherwise keep both fitted (normal mode) |

### 4.3 Station I/O MCU (App `ground/station_io`)

| VENTUNO Q pin | Connects to | Notes |
|---|---|---|
| D2 | E22 board pin 10 (M0) | optional 10 kOhm to 3.3 V; **no pull-down** |
| D3 | E22 board pin 7 (M1) | optional 10 kOhm to 3.3 V |
| D4 | E22 board pin 4 (AUX) | input with pull-up |
| GND | E22 board pin 8 (GND) | |
| D5 | AOS LED (green) + 330 Ohm to GND | lit = signal acquired |
| D6 | ALARM LED (red) + 330 Ohm | steady = YELLOW, 4 Hz blink = RED, 10 Hz = GDS link down |
| D7 | UPLINK LED (yellow) + 330 Ohm | commands outstanding |
| A0 | 1 kOhm -> NPN base; 5 V active buzzer -> collector; emitter GND | 0.4 s beep on RED alarm / LOS |
| SDA / SCL (`Wire`) | CardKB #1 via level shifter (LV 3.3 V, HV 5 V) | command keyboard |
| `Wire1` (*verify* pins) | CardKB #2 via level shifter | 2nd-operator 'Y' (optional) |

```
 CardKB (Grove/HY2.0 cable)        BSS138 level shifter            VENTUNO Q
   GND ─────────────────────────── GND ─────────────────────────── GND
   5V  ─────────────────────────── HV                LV ─────────── 3.3 V
   SDA ─────────────────────────── HV1              LV1 ─────────── SDA (Wire)
   SCL ─────────────────────────── HV2              LV2 ─────────── SCL (Wire)
```

---

## 5 Thermal

* The E22-400T37S stops transmitting above 120 degC and reports it (`FF FF FF 03`); the flight
  software then silences the transmitter for 5 min and goes SAFE ([04 ICD](04_Space_Data_Link_ICD.md)).
  Mount the board with free air around it; a small heat sink on the module's shield helps at
  40-50 % duty cycle. The flight software never exceeds 50 % transmit duty.
* Bus temperature is reported from the Nicla Sense ME (HK `bus_temp`, RED at 75 degC).

## 6 Magnetic cleanliness (for the RM3100 science)

The MAG packet records whether any sample was taken with the PA keyed (`flags` bit 0); the
flight software schedules all sensor sampling in PA-off windows, so this bit should stay 0. Keep
current loops (battery leads, 12 V wiring) twisted and away from the boom; use non-magnetic
fasteners near the sensors.
