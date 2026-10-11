# 00 - Beginner Setup Guide (step by step)

This guide takes you from a box of parts to a working link: the **UNO Q "spacecraft"
(VGQ-1, the transmitter)** sending CCSDS telemetry over the E22 radio to the
**VENTUNO Q "ground station" (DSS-Q1, the receiver)**, which decodes it, shows it on a
web dashboard and sends commands back - typed on the CardKB keyboard or in the browser.

You do not need to know CCSDS, LoRa or Linux to follow it. Every step says **what to do**,
**what you should see** and **what to do if you do not see it**. Work through the parts in
order; each one ends with a check, so a problem is caught where it happens.

| Part | What you do | Time |
|---|---|---|
| A | Safety rules (read once) | 5 min |
| B | Check you have every part | 15 min |
| C | Try the whole system with NO hardware (simulator) | 20 min |
| D | Prepare the two radios | 30 min |
| E | Build the spacecraft (UNO Q) one instrument at a time | 2-3 h |
| F | Build the ground station (VENTUNO Q) | 1-2 h |
| G | First contact on the bench | 30 min |
| H | Send your first commands | 15 min |
| I | Switch on uplink authentication (SDLS) | 15 min |
| J | Go for distance | a day out |
| K | Troubleshooting | - |

Words used below: **TX** = transmit, **RX** = receive, **GDS** = ground data system (the
receiver software), **FSW** = flight software (the spacecraft program), **App Lab** =
Arduino's tool for UNO Q / VENTUNO Q apps.

---

## Part A - Safety rules (read before anything else)

1. **Never power an E22-400T37S without an antenna or a 50-ohm load on its SMA socket.**
   EBYTE: "The module must be connected to a 50 ohm impedance antenna when sending. No-load
   transmission may cause permanent damage to the module." The module transmits 5 W (37 dBm)
   and its power **cannot be turned down in software** (all power codes are 37 dBm).
2. **Never put two E22s close together with antennas on.** The receiver input is rated for at
   most +10 dBm. On the bench use the attenuator set-up in Part G, or keep the stations more
   than ~100 m apart.
3. **Licence.** 433 MHz at 5 W is amateur-radio territory almost everywhere: you need an
   amateur licence and your callsign in `config.h` (`VGQ_CALLSIGN`). See
   [09 Regulatory & Safety](09_Regulatory_and_Safety.md). Encryption is not allowed on amateur
   bands, which is why the uplink is *authenticated* but not encrypted.
4. **RF exposure.** Do not stand next to a transmitting antenna; keep at least 1 m from a
   5 W 433 MHz antenna while it transmits.
5. **Logic levels.** The UNO Q, the VENTUNO Q, the E22 (TTL 3.3 V), the AS7265X and the RM3100
   all use 3.3 V logic. **Never connect a 5 V signal to them.** Only the *power* inputs of the
   Nicla boards and the CardKB take 5 V.
6. **Polarity.** The E22 test board and the Nicla boards have no reverse-polarity protection on
   every input. Check + and - with a meter before switching on.

---

## Part B - Parts list

### B.1 What you already have (used in this project)

| Qty | Part | Role |
|---|---|---|
| 1 | Arduino UNO Q | **Spacecraft** flight computer (transmitter) |
| 1 | Arduino VENTUNO Q | **Ground station** (receiver, web dashboard, command uplink) |
| 2 | EBYTE E22-400T37S on E22-400TBH-02 test board | Radios: one on each end |
| 1 | Arduino Nicla Sense ME | Attitude reference unit: orientation, IMU, magnetometer, pressure, gas |
| 1 | Arduino Nicla Sense Env | Temperature, humidity, indoor/outdoor air quality |
| 1 | SparkFun AS7265X Spectral Triad | 18-channel spectrometer (410-940 nm) |
| 1-2 | RM3100 magnetometer breakout | Science magnetometer on a boom (second one optional) |
| 2 | M5Stack CardKB mini keyboard | #1 command entry, #2 second-operator approval (ground side) |
| 2 | 433 MHz antennas (SMA) | One per radio |

About the antennas: a small 433 MHz antenna sold as "35 dBi" cannot have that gain - a 35 dBi
antenna at 433 MHz would need about 120 m² of effective area (a dish ~16 m across). Treat each
as a ~2 dBi whip/dipole. For more distance use a 433 MHz Yagi on the ground (see Part J).

### B.2 Parts to buy (small)

| Qty | Part | Why |
|---|---|---|
| 1 | 12 V supply or battery for the spacecraft, >= 4 A (e.g. 3S Li-ion 11.1 V, 12.6 V full) | E22 needs 4.5-15 V; at 12 V it draws 1.1 A typ / 1.3 A max when transmitting (EBYTE asks for a supply of >= 1.5 A) |
| 1 | 12 V -> 5 V buck converter, >= 1 A | Powers both Nicla boards (the UNO Q takes 12 V on its VIN pin) |
| 2 | Fuse + holder: 4 A (spacecraft 12 V line), 2 A (ground E22 12 V line) | Protects wiring and battery |
| 1 | 12 V, >= 1.5 A supply for the ground E22 | Separate from the VENTUNO Q supply |
| 1 | VENTUNO Q power supply: 12 V/3 A or more on the barrel jack (60 W, 12 V/5 A, for full headroom) | Arduino recommends >= 60 W for heavy AI/USB loads; this station is light (13.3 W measured peak for a simple app) |
| 4 | 10 kOhm resistors | M0/M1 pull-ups on each E22 board |
| 3 + 1 | 3 mm LEDs (green, red, yellow) + 330 Ohm resistors | AOS / ALARM / UPLINK lamps |
| 1 | 5 V active buzzer, NPN transistor (2N2222/BC547), 1 kOhm resistor | Alarm sound |
| 2 | 4-channel bidirectional I2C level shifter (BSS138 type) | CardKB (5 V) to VENTUNO Q (3.3 V) |
| 3-5 | Qwiic cables (100-500 mm) + 2 Qwiic-to-jumper "breadboard" cables | Sensor bus |
| - | Jumper wires (female-female), heat-shrink, small breadboard | Wiring |
| 1 | Multimeter | Checks in every part |
| Bench RF set | One 40 dB / 10 W and two 30 dB / 2 W SMA attenuators, SMA cables, or a 50 Ohm 10 W dummy load | Safe bench tests (Part G) |

### B.3 Software you will install

* On a PC: **Arduino App Lab** (for the UNO Q and the VENTUNO Q) and **Arduino IDE 2** (only to
  program the Nicla Sense ME). Python 3.11 or newer if you want to run the simulator on the PC.
* Nothing to buy; everything else is in this repository.

---

## Part C - Try everything with no hardware (simulator)

This proves the software works before you touch a wire, and teaches you the dashboard. You can
do it on any Linux/macOS/Windows PC with Python 3.11+ (or later on the VENTUNO Q itself).

```bash
git clone <this repository URL> Benchmarks
cd Benchmarks/ground
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip install -r requirements.txt
.venv/bin/python -m dssq.gds --sim                # Windows: .venv\Scripts\python -m dssq.gds --sim
```

**You should see** log lines such as `SIMULATION MODE - software spacecraft, no RF`, then
`RX AOS` and every 10 s a `STATUS AOS sync=LOCK RSSI=... frames=...` line.

Open **http://127.0.0.1:8080** in a browser. Click through the tabs:

* **OVERVIEW** - big tiles: signal, RSSI, SNR, margin, frames, spacecraft mode.
* **RECEIVER** - the detailed receiver: link budget, FSPL, RSSI statistics to 4 decimals with
  their standard error, decoder and frame counters, histograms.
* **SCIENCE** - magnetometers, attitude, environment and the spectrum (simulated values).
* **COMMANDING** - type `NOOP` and press Enter. Watch it go QUEUED -> RADIATED -> ACCEPTED ->
  EXECUTED in the history table. Then try `MODE ENCOUNTER`, `PING 42`, `HELP`.

Stop the program with **Ctrl+C**. If this part works, every later problem is hardware/wiring.

**Did not work?** `No module named ...` -> you skipped the `pip install` step.
`TOML support needs Python 3.11+` -> install a newer Python (Ubuntu 24.04 has 3.12).

---

## Part D - Prepare the two radios

The E22-400TBH-02 board (EBYTE manual "E22/E32-xxxTBH-02") has:

* a **20-pin header** in two rows (bottom row pins 1-10, top row 11-20, see the picture in the
  EBYTE manual): `1 GND, 2 GND, 3 RESET, 4 AUX, 5 TXD, 6 RXD, 7 M1, 8 GND, 9 GND, 10 M0` and
  `11 3.3V, 12 GND, ..., 19 STATE, 20 GND`;
* **two jumper caps on the bottom header** that short **M1-GND (pins 7-8)** and **GND-M0
  (pins 9-10)**. Cap fitted = 0, cap removed = 1 (the module pulls the pin up);
* **two orange jumper caps labelled RXD and TXD** next to the USB-C socket - they connect the
  on-board USB-serial chip (CH340X) to the radio;
* a **DC jack and a green screw terminal** for 4.5-15 V (the terminal is disabled when the DC
  jack is plugged in), an **SMA antenna socket**, and an **AUX LED**.

Mode table (EBYTE manual, chapter 6): M1 M0 = `0 0` normal, `0 1` Wake-on-Radio,
`1 0` configuration (always 9600 8N1), `1 1` deep sleep.

### D.1 Look at the factory settings (optional but recommended)

1. Screw an antenna (or a 50 Ohm load) onto the SMA socket. **Always, before power.**
2. Leave all four caps fitted. Remove only the **M1** cap -> configuration mode.
3. Connect the board's USB-C to the PC/VENTUNO Q and 12 V to the DC jack.
4. Run (in `Benchmarks/ground`):

   ```bash
   .venv/bin/python -m dssq.radio.e22 --port /dev/ttyUSB0      # Windows: --port COM5
   ```

   **You should see** `product info: ...` and a table: `channel 23`, `freq_mhz 433.125`,
   `air_rate 2.4k`, `uart_bps 9600` (EBYTE factory default: address 0, channel 0x17 = 23,
   2.4 kbit/s, 9600 8N1).
5. Put the M1 cap back.

You do not need to change the spacecraft radio's settings - the flight software writes the
complete configuration into it at every boot and reads it back to verify it. The ground radio
is configured the same way by the GDS when the Radio Control Unit is wired (Part F), or once
by hand:

```bash
# M1 cap removed (configuration mode):
.venv/bin/python -m dssq.radio.e22 --port /dev/ttyUSB0 --write --config config/station.toml
```

This sets channel 23 (433.125 MHz), 2.4 kbit/s, RSSI byte on, ambient-noise reading on and the
abnormal-status reports on. **Check your band plan/licence before choosing a channel**
(`allowed_channels` in `config/station.toml`).

---

## Part E - Build the spacecraft (UNO Q), one piece at a time

Wiring tables and diagrams: [03 Hardware Integration](03_Hardware_Integration.md) section 3.
Here you build it in small steps and test after each.

### E.1 Set up the UNO Q and App Lab

1. Install **Arduino App Lab** on your PC and connect the UNO Q by USB-C. Follow App Lab's
   first-start wizard (it updates the board, sets the board's name, password and Wi-Fi).
2. Get the repository onto the board. Easiest: in App Lab open a terminal on the board (or
   `ssh arduino@<board-name>.local`, or `adb shell`) and run
   `git clone <repository URL> ~/Benchmarks`.
3. Copy the spacecraft app into App Lab's apps folder:

   ```bash
   cp -r ~/Benchmarks/spacecraft/vgq1_flight ~/ArduinoApps/vgq1-flight
   ```

   (Alternative: zip the `spacecraft/vgq1_flight` folder on your PC and use App Lab **My Apps ->
   open any app -> the arrow next to its name -> Import App**. Imported apps land in
   `~/ArduinoApps/` on the board.)
4. In App Lab open **VGQ-1 Flight Software**. App Lab installs the two instrument libraries
   listed in `sketch/sketch.yaml` (SparkFun Spectral Triad AS7265X, Arduino_NiclaSenseEnv).

### E.2 Step 1 - flight computer alone (no wires yet)

Press **Run** in App Lab. The sketch is built for the STM32U585 (`arduino:zephyr:unoq`) and the
Linux-side EGSE (`python/main.py`) starts.

**You should see** in the console: `VGQ-1 flight software boot`, `SSR 256 KiB allocated from the
heap` (or a smaller power of two), `E22 config FAILED` (correct - no radio yet),
`instruments: ... ABSENT`, and from the EGSE every 10 s a box titled
`VGQ-1 TRANSMITTER (EGSE)`. The LED matrix on the UNO Q shows the status lights (row 7: one
lit column = SAFE mode).

### E.3 Step 2 - power plan

Wire the power before signals (diagram in 03 section 3.1):

* 12 V battery/supply -> 4 A fuse -> E22 board DC jack (or green terminal).
* Same fused 12 V -> UNO Q **VIN** (JANALOG header pin 8, accepts 7-24 V); GND to a UNO Q GND pin.
  (Arduino asks for a dedicated 5 V / 3 A source if you power the UNO Q through USB-C instead.)
* 12 V -> buck converter -> 5 V -> Nicla Sense ME VIN, Nicla Sense Env 5 V.
* **One common ground** for everything.

Check with the meter: 12 V at the E22 terminal and at the UNO Q VIN pin, 5.0-5.2 V at the buck
output, correct polarity.

### E.4 Step 3 - the spacecraft radio

1. Antenna (or 50 Ohm load) on the SMA socket. **Always.**
2. Remove **all four caps**: M0, M1 (bottom header) and RXD, TXD (by the USB socket). The RXD/TXD
   caps must go: they connect the board's USB chip to the same radio pins the UNO Q will drive.
3. Fit a **10 kOhm resistor from M0 to 3.3 V and one from M1 to 3.3 V** (EBYTE specifies the
   module's own pull-ups as "very weak"; with these the radio sleeps safely while the UNO Q boots).
4. Wire (TBH-02 header -> UNO Q):

   | E22 board pin | UNO Q pin |
   |---|---|
   | 1 GND | GND |
   | 4 AUX | D7 |
   | 5 TXD | header pin printed **SDA** (it is used as UART RX, USART3) |
   | 6 RXD | header pin printed **SCL** (UART TX) |
   | 7 M1 | D3 |
   | 10 M0 | D2 |

   The UNO Q's D0/D1 serial port is *not* used: it also carries the Zephyr console, whose text
   would be transmitted over the air. The SDA/SCL header pins become a UART; all sensors use the
   Qwiic connector instead.
5. Power up and **Run**. **You should see** `E22 product info ...` and
   `E22 config verified: ch=23 (433.125 MHz) air=2 pwr=0`. The board's AUX LED blinks every few
   seconds and row 0 of the LED matrix lights while it transmits.

**"E22 config FAILED"?** Check: TXD/RXD crossed correctly (board TXD -> UNO Q SDA), caps removed,
common ground, 12 V present. The verify step proves both directions of the serial link work.

### E.5 Step 4 - the sensor bus (Qwiic), one instrument at a time

The UNO Q's **Qwiic connector** is a 3.3 V I2C bus (`Wire1`). All four instruments share it -
each has its own address, so no multiplexer is needed:

| Instrument | Address | How to connect |
|---|---|---|
| AS7265X (SparkFun) | 0x49 | Qwiic cable (it has two Qwiic sockets: daisy-chain through it) |
| RM3100 outboard | 0x20 | Qwiic-to-jumper cable: 3.3V, GND, SDA, SCL; address pins SA0 = SA1 = GND |
| RM3100 inboard (optional) | 0x23 | as above, SA0 = SA1 = 3.3 V. **Never 0x21** (that is the Nicla Sense Env) |
| Nicla Sense Env | 0x21 | ESLOV cable: SDA/SCL/GND to the Qwiic bus, 5V wire to the 5 V rail |
| Nicla Sense ME | 0x2A | J2 header: pin 1 SDA, pin 2 SCL, pin 6 GND to the bus; pin 9 VIN to 5 V |

Qwiic colours: black GND, red 3.3 V, blue SDA, yellow SCL.

Add them **one at a time**, pressing Run after each and reading the `instruments:` log line
(also the LED-matrix row 6 - one column per healthy item):

1. **AS7265X** -> `AS7265X ok`. Every few minutes a SPEC packet is produced.
2. **RM3100 outboard** -> `RM3100 OB ok`. Put it on a non-magnetic boom (wood/plastic), as far
   as practical (0.5-1 m) from the radio and the battery; use a twisted 4-wire cable of at most
   ~1 m (100 kHz I2C). Optional second RM3100 at mid-boom (address 0x23) -> `RM3100 IB ok`.
   Enter the two boom distances in `ground/config/station.toml` `[science]`.
3. **Nicla Sense Env** -> `Nicla Env ok`. It runs Arduino's factory firmware; nothing to flash.
4. **Nicla Sense ME** -> first flash its firmware (next step), then connect -> `Nicla ME ok`.

If an instrument shows `ABSENT`: check SDA/SCL are not swapped, 3.3 V/5 V present, and run the
I2C scan by watching the boot log after each addition. Too many pull-up resistors on one bus
can also stop it: SparkFun boards have I2C pull-up jumpers - leave them on one board only if
the bus misbehaves.

### E.6 Step 5 - program the Nicla Sense ME (attitude reference unit)

1. Install **Arduino IDE 2**. In Boards Manager install **Arduino Mbed OS Nicla Boards**; in
   Library Manager install **Arduino_BHY2**.
2. Open `spacecraft/nicla_sense_me_aru/nicla_sense_me_aru.ino`, select **Nicla Sense ME** and
   its USB port, click **Upload**.
3. Open the Serial Monitor at 115200: **you should see**
   `VGQ-1 ARU (Nicla Sense ME) ready at I2C 0x2A, accel +/-8 g, gyro +/-2000 dps`.
4. Disconnect USB, wire it to the Qwiic bus (table above), power from 5 V, Run the UNO Q app.

### E.7 Step 6 - set your site's magnetic field

In `spacecraft/vgq1_flight/sketch/src/config.h` set `kGeomagInclDeg` and `kGeomagDeclDeg` for
your location (NOAA or BGS "World Magnetic Model calculator", free online) - used by the TRIAD
attitude solution - and your callsign in `VGQ_CALLSIGN`.

---

## Part F - Build the ground station (VENTUNO Q)

Wiring: [03 Hardware Integration](03_Hardware_Integration.md) section 4.

### F.1 The VENTUNO Q itself

1. Power it from 12 V or 24 V on the barrel jack (12 V/3 A is enough here; 60 W gives full
   headroom). A USB-C supply must be USB PD (9-20 V). Keep its fan running.
2. Do App Lab's first-start set-up (Wi-Fi, password). Open a terminal on the board
   (`ssh arduino@<board-name>.local` or `adb shell`).
3. Get the code and the Python environment (the board runs Ubuntu 24.04, which does not allow
   `pip install` into the system Python - hence the virtual environment):

   ```bash
   git clone <repository URL> ~/Benchmarks
   cd ~/Benchmarks/ground
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   sudo usermod -aG dialout $USER        # permission to open the E22 USB port; log out and in again
   ```

4. Quick check: `.venv/bin/python -m dssq.gds --sim` and open `http://<board>:8080` from your PC
   after setting `http_host = "0.0.0.0"` and an `http_token` in `config/station.toml` (or browse
   on the board itself at `http://127.0.0.1:8080`). Ctrl+C to stop.

### F.2 The ground radio

1. Antenna on the SMA socket (or the attenuator chain of Part G for the bench test).
2. **Keep the RXD/TXD caps fitted** (the GDS uses the board's USB port). Remove the **M0 and M1
   caps** if you wire the Radio Control Unit (F.3); otherwise leave all caps fitted.
3. USB-C from the E22 board to a VENTUNO Q USB port; 12 V >= 1.5 A to its DC jack.
4. `ls /dev/serial/by-id/` should show a CH340 device; it is usually `/dev/ttyUSB0`. Put the
   right name in `config/station.toml` `[radio] port`.

### F.3 Station I/O: CardKB keyboards, lamps, Radio Control Unit

The VENTUNO Q's own microcontroller (STM32H5) runs the small App `ground/station_io`. It reads
the keyboards, drives the lamps and switches the ground radio's mode pins (so the GDS can
configure the radio, scan the band, and wake a hibernating spacecraft).

| Item | Wire to VENTUNO Q |
|---|---|
| E22 board header pin 10 (M0) | D2 (+ optional 10 kOhm to 3.3 V) |
| E22 board header pin 7 (M1) | D3 (+ optional 10 kOhm to 3.3 V) |
| E22 board header pin 4 (AUX) | D4 |
| E22 board header pin 8 (GND) | GND |
| AOS lamp (green LED + 330 Ohm) | D5 -> LED -> GND |
| ALARM lamp (red) | D6 |
| UPLINK lamp (yellow) | D7 |
| Buzzer | A0 -> 1 kOhm -> NPN base; buzzer from 5 V to collector; emitter to GND |
| CardKB #1 (commands) | through a 3.3 V <-> 5 V I2C level shifter to the header SDA/SCL (`Wire`); CardKB 5 V and GND |
| CardKB #2 (2nd operator, optional) | through a level shifter to the VENTUNO Q's **Qwiic connector** (`Wire1`) |

On the VENTUNO Q, `Wire1` is the Qwiic connector (I2C3) and `Wire` is the header SDA/SCL (I2C4),
per Arduino's board files. The sketch auto-detects CardKB #2; if it is not found, the second operator approves from the web page.

Install the App: copy `~/Benchmarks/ground/station_io` to `~/ArduinoApps/dssq-station-io`, open
**DSS-Q Station IO** in App Lab and press **Run** (it flashes the STM32H5;
`arduino:zephyr:ventunoq`).

### F.4 Start the GDS as a service

```bash
cd ~/Benchmarks/ground
nano config/station.toml      # station name, antenna gain, distance, radio port, frontpanel/rcu
sudo cp deploy/dssq-gds.service /etc/systemd/system/   # edit User/WorkingDirectory first if needed
sudo systemctl daemon-reload && sudo systemctl enable --now dssq-gds
journalctl -u dssq-gds -f      # live log; Ctrl+C leaves it running
```

With the Radio Control Unit wired, the log shows `ground E22: ch 23 (433.125 MHz) ... matches
station.toml` a few seconds after start. The lamps: AOS off, ALARM steady/flashing if there is
an alarm. **If the GDS stops, the ALARM lamp flashes fast** - the MCU's link-down warning.

---

## Part G - First contact (bench)

Two 5 W transmitters on one table can damage each other's receivers. Use one of these:

* **Coax with attenuators (best):** spacecraft SMA -> **40 dB / 10 W** attenuator -> 30 dB ->
  30 dB -> ground SMA. Total 100 dB: the ground receives about 37 - 100 = **-63 dBm**, safe and
  realistic. The first attenuator must be rated for the full 5 W.
* **Antennas far apart:** at least ~100 m of separation.

Then:

1. Power the ground station first (GDS running), then the spacecraft.
2. Within one frame period (about 5 s in SAFE mode at 2.4 kbit/s) the web page header turns
   **AOS** and **LOCK**, the AOS lamp lights, and frames start counting.
3. On the **RECEIVER** tab check: `Frames lost` 0, `Uncorrectable` 0, RSSI near your expected
   value, and the link budget's *measured* column filled in.

**No AOS?** Same channel and air rate on both ends (`config.h` `kE22Channel`/`kE22AirRate` and
`station.toml` `[radio]`)? Spacecraft log shows `E22 config verified`? Ground port correct
(`journalctl` shows no serial error)? `sync=SEARCH` with bytes arriving means wrong settings on
one end.

---

## Part H - Your first commands

Type on **CardKB #1** (the dashboard shows the line as you type) or in the web console:

| Type | You should see |
|---|---|
| `NOOP` + Enter | History: QUEUED -> RADIATED -> ACCEPTED -> EXECUTED; UPLINK lamp lit while outstanding |
| `PING 1234` | EXECUTED with a round-trip time |
| `MODE CRUISE` | spacecraft mode changes; MAG/ATT/SPEC/ENV packets start |
| `SPECCFG 2 50 1` | spectrometer gain 16x, 140 ms integration, white lamp during measurements |
| `HELP` | the command list in the console |

Hazardous commands (marked in `HELP`/dictionary, e.g. `TXINHIBIT 60`) ask for confirmation:
press **Enter again on an empty line** (or the CONFIRM button). If
`require_two_person_for_hazardous = true`, a second person presses **Y on CardKB #2** (or AUTH
on the web page). Full list: [05 Dictionary](05_Telemetry_and_Command_Dictionary.md).

---

## Part I - Switch on uplink authentication (SDLS)

Without it anyone on the channel could command your spacecraft. Authentication proves each
command came from your station (HMAC-SHA-256, CCSDS 355.0-B), without encrypting it.

```bash
cd ~/Benchmarks                       # on the machine that builds BOTH ends
python3 tools/gen_sdls_key.py         # writes ground/config/sdls_key.hex and the flight sdls_key.h
```

1. Copy `spacecraft/vgq1_flight/sketch/src/sdls_key.h` into the UNO Q app folder
   (`~/ArduinoApps/vgq1-flight/sketch/src/`) and press Run - the log says
   `SDLS uplink authentication ENABLED`.
2. Copy `ground/config/sdls_key.hex` to the VENTUNO Q (`~/Benchmarks/ground/config/`) and set
   `[sdls] enabled = true` in `station.toml`; restart the GDS.
3. Send `NOOP`: it still works. The COMMANDING tab shows `SDLS authentication ON`.

The key files are in `.gitignore` - never commit or share them.

---

## Part J - Going for distance

1. The air rate is already at the most sensitive setting the E22-400T37S has: **2.4 kbit/s**
   (EBYTE: codes 0, 1 and 2 are all 2.4k). Faster rates cost about 3 dB per doubling.
2. **Height beats everything.** The radio horizon for antennas at h1 and h2 metres is
   4.12 x (sqrt(h1) + sqrt(h2)) km: 2 m + 10 m -> 18.9 km; a hilltop at 500 m + 10 m -> 105 km.
3. **Ground antenna**: a 433 MHz Yagi (10-13 dBi), low-loss coax (short, LMR-400 class) - every
   dB of feeder loss is a dB of margin.
4. Keep the first Fresnel zone clear (about 42 m radius at mid-path for 10 km).
5. Enter the real distance in `station.toml` `[spacecraft] distance_km`: the RECEIVER tab then
   shows predicted vs measured received power and the *excess loss*.
6. Use `tools/link_budget.py` to plan: see [02 Link Budget & RF Analysis](02_Link_Budget_and_RF_Analysis.md).

---

## Part K - Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `E22 config FAILED` on the spacecraft | serial wires swapped, RXD/TXD caps still fitted, no 12 V | Part E.4 |
| Instrument `ABSENT` | SDA/SCL swapped, no power, address clash | Part E.5 |
| `Nicla ME ABSENT` but powered | firmware not flashed, or VIN below 3.5 V | Part E.6 |
| Web page "GDS LINK DOWN" | GDS not running | `systemctl status dssq-gds` |
| ALARM lamp flashing fast | station I/O MCU gets no status from the GDS | start the GDS; check `frontpanel = true` |
| `sync=SEARCH`, bytes arriving | channel/air rate differ between ends | Part G |
| Frames OK but many `Uncorrectable` | interference or very weak signal | GSCAN, change channel (`LINKCHAN`) |
| Commands stay RADIATED | spacecraft not receiving the uplink | check uplink RSSI on RECEIVER "two-way" table; antennas |
| `FARM LOCKOUT` event | sequence lost | type `UNLOCK` |
| `SDLS rejected TC frame: bad MAC` | different keys on the two ends | redo Part I with the same key files |
| `E22 reports UNDER-VOLTAGE` | supply sags under the 1.1 A transmit load | thicker wires, bigger supply/battery |
| CardKB #2 "not detected" | not on the Qwiic connector, no 5 V, or level shifter wired LV/HV the wrong way round | check the wiring, or use the web AUTH button |
