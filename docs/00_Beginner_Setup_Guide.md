# 00 - Beginner Setup Guide (step by step)

This guide takes you from a box of parts to a working two-way radio link:

* the **VGQ-1 spacecraft** - an Arduino UNO Q with a radio and three instruments - sends
  CCSDS telemetry, and
* the **DSS-Q1 ground station** - an Arduino VENTUNO Q with a radio - receives it, shows it
  on a web dashboard and sends commands back, typed on a small keyboard or in the browser.

You do not need to know CCSDS, LoRa or Linux. Every step says what to **do**, what you should
**see** (the check), and what to do **if you do not see it**. Work through the parts in order:
each part ends with a check, so a problem is caught where it happens.

| Part | What you do | About |
|---|---|---|
| [1](#part-1---safety-rules) | Safety rules | 5 min |
| [2](#part-2---what-you-need) | Check you have every part and tool | 15 min |
| [3](#part-3---pc-tools-and-project-files) | Install the PC tools, get the project files, optional dry run | 30 min |
| [4](#part-4---flash-the-nicla-sense-me) | Flash the Nicla Sense ME | 15 min |
| [5](#part-5---set-up-the-boards-and-import-the-two-apps) | Set up both boards, import the two App Lab zips | 45 min |
| [6](#part-6---generate-the-security-keys-after-the-import) | Generate the security keys (after the import) | 15 min |
| [7](#part-7---settings-for-your-station-and-site) | Enter your callsign (both ends) and site settings | 15 min |
| [8](#part-8---wire-the-spacecraft-step-by-step) | Wire the spacecraft, one piece at a time | 2-3 h |
| [9](#part-9---wire-the-ground-station-step-by-step) | Wire the ground station, one piece at a time | 1-2 h |
| [10](#part-10---first-power-up-of-the-complete-system) | First power-up of the complete system | 15 min |
| [11](#part-11---first-contact-on-the-bench) | First contact on the bench | 30 min |
| [12](#part-12---your-first-commands) | Your first commands | 15 min |
| [13](#part-13---switching-to-the-5-w-e22-400t37s-the-a0-strap) | Optional: the 5 W radio (A0 strap) | 1 h |
| [14](#part-14---going-for-distance) | Going for distance | a day out |
| [15](#part-15---host-mode-fallback-for-the-ground-station) | Fallback: run the ground software as a Linux service | 30 min |
| [16](#part-16---updating-or-re-importing-an-app-without-losing-state) | Updating an App without losing its state | 15 min |
| [17](#part-17---troubleshooting-pointers) | Troubleshooting pointers | - |

Words used below:

| Word | Meaning |
|---|---|
| TX / RX | transmit / receive |
| MCU | the small real-time microcontroller on each board (runs the "sketch") |
| Linux side | the board's main processor running Linux (runs the Python part of an App) |
| App | an Arduino App Lab project: a sketch for the MCU plus Python for the Linux side |
| FSW | flight software (the spacecraft sketch) |
| EGSE | the spacecraft's Linux-side helper program (keeps counters, archives what is sent) |
| GDS | ground data system (the ground station's Linux-side program and web dashboard) |
| EVR | an event report line from the spacecraft, e.g. `EVR 0x0306 INFO radio ... OK` |
| SDLS | the link security (encryption and/or authentication of the radio frames) |

Wiring tables, pin maps, the power budget and the full parts list are in
[03_Hardware_Integration.md](03_Hardware_Integration.md). This guide refers to them as
"docs/03 section x".

---

## Part 1 - Safety rules

Read these once before you touch anything.

1. ⚠ **Never power a radio without an antenna or a 50 Ohm dummy load on its SMA socket.**
   EBYTE: "No-load transmission may cause permanent damage to the module."
   The spacecraft starts transmitting as soon as its software runs. During the build (parts 8-10)
   keep a 50 Ohm dummy load on each radio.
2. ⚠ **Never put two radios close together with antennas fitted.** The receiver input is rated
   for at most +10 dBm. Two 2 W radios one metre apart exceed that. On the bench use the
   attenuator set of part 11, or keep the antennas at least 10 m apart (100 m is better).
   Numbers: docs/03 section 10.
3. ⚠ **Licence.** Transmitting at 433 MHz needs a licence; in most countries this is the
   amateur-radio licence, and your callsign goes into the software at both ends (part 7).
   5 W needs a licence that allows 5 W. Encryption is generally **not** allowed on amateur frequencies:
   part 6 shows how to switch it off. Details: [09_Regulatory_and_Safety.md](09_Regulatory_and_Safety.md).
4. ⚠ **RF exposure.** Do not stand next to an antenna that is transmitting
   ([09_Regulatory_and_Safety.md](09_Regulatory_and_Safety.md)).
5. **3.3 V logic everywhere.** Every signal wire in this project is 3.3 V. 5 V and 12 V go only
   to the power pins named in the tables. Never connect a 5 V signal to a board pin.
6. **Polarity and fuses.** Check + and - with a meter before you connect a board. Reversed
   polarity destroys the radio modules. Put the fuse at the battery end.
7. **Lithium batteries.** Use a pack with a protection board. Never heat it, never charge it
   unattended, never charge it inside the spacecraft structure.
8. **Heat tests.** Only with a hair dryer at a distance, never an oven or a flame (record TMP in
   [21_Test_Records.md](21_Test_Records.md)).

---

## Part 2 - What you need

### 2.1 Parts

The complete list with part numbers is in docs/03 section 7. The minimum for the default
system:

| Qty | Part | Role |
|---|---|---|
| 1 | Arduino UNO Q (ABX00162) | spacecraft computer |
| 1 | Arduino VENTUNO Q (ABX00181) | ground station computer |
| 2 | EBYTE E22-400T33D radio module | one per end |
| 2 | 433 MHz antennas (SMA) and 2 x 50 Ohm dummy loads (>= 5 W) | one per end |
| 1 | Arduino Nicla Sense ME (ABX00050) | attitude and environment sensor |
| 1 | SparkFun Spectral Triad AS7265x (SEN-15050) | spectrometer |
| 1 (+1) | RM3100 magnetometer breakout | magnetometer on a boom (second one optional) |
| 1 | 3.3 V-logic USB-UART adapter | ground radio to the VENTUNO Q |
| 1 | M5Stack CardKB (U035 or v1.1) + Grove-to-jumper cable | command keyboard |
| 1 | 12 V battery or bench supply (>= 4 A), 4 A fuse, 12 V to 5 V buck converter (>= 3 A) | spacecraft power |
| 1 | 12 V / 5 A power supply, 5.5 x 2.1 mm plug | VENTUNO Q power |
| - | resistors (10 kOhm x 4, 330 Ohm x 3, 1 kOhm, 4.7 kOhm x 2 spare), 3 LEDs, 5 V buzzer, NPN transistor, 470-1000 uF + 100 nF capacitors x 2 | small parts |
| - | jumper wires, header pins, heat-shrink, a non-magnetic boom (wood or plastic, 0.5-1 m) | mechanics |
| 1 set | bench RF set: 2 x 30 dB (>= 5 W) + 1 x 40 dB SMA attenuators, short SMA cables | first contact (part 11) |

### 2.2 Tools

Multimeter, soldering iron, wire stripper, small screwdriver. A USB-C cable for each board,
a micro-USB cable for the Nicla Sense ME.

### 2.3 Other things

* A PC (Windows, macOS or Linux) and a Wi-Fi network that the PC and both boards can join.
* Internet access during setup (App Lab, the Arduino IDE board package, the board updates).
* Your licence and callsign.

---

## Part 3 - PC tools and project files

### 3.1 Install the tools

1. **Arduino App Lab** (from arduino.cc, Software page). It programs the UNO Q and the VENTUNO Q.
   *Linux PCs:* follow the "USB permissions" (udev rules) section of Arduino's UNO Q user manual,
   otherwise App Lab and `adb` cannot see the board.
2. **Arduino IDE 2** (from arduino.cc). Only for the Nicla Sense ME.
3. **Python 3.11 or newer** (python.org; Linux and macOS usually have it). Needed for the key
   generator and the optional dry run. Check: `python3 --version` (Windows: `py -3 --version`)
   prints 3.11 or higher.
4. A terminal with `ssh` and `scp`. They are built into Windows 10/11, macOS and Linux.

### 3.2 Get the project files

Either clone the repository:

```bash
git clone https://github.com/Tanay7/vgq1-deep-space-link.git
cd vgq1-deep-space-link
```

or download it as a ZIP from the GitHub page and unpack it. You need:

| File | Used in |
|---|---|
| `dist/vgq1-spacecraft-unoq.zip` | part 5 (UNO Q App) |
| `dist/dssq-groundstation-ventunoq.zip` | part 5 (VENTUNO Q App) |
| `nicla_sense_me_aru/nicla_sense_me_aru.ino` | part 4 |
| `tools/gen_sdls_key.py` | part 6 |

**Do not unzip the two App zips**: App Lab imports them as they are. They already contain
everything the boards need, including the two bundled libraries, so the boards download nothing
([22_Bundled_Libraries.md](22_Bundled_Libraries.md)). They never contain a key (part 6).

Check: both zips are in the `dist/` folder. Optionally check that they arrived intact:
`cd dist && sha256sum -c SHA256SUMS` (macOS: `shasum -a 256 -c SHA256SUMS`; Windows:
`certutil -hashfile <zip> SHA256` and compare with the line in `SHA256SUMS`). Every line must
say OK. (Developers rebuild the zips with `tools/package_apps.py`,
[16_Developer_Guide.md](16_Developer_Guide.md).)

### 3.3 Optional: dry run on the PC with the simulator

This proves the ground software works and teaches you the dashboard before any wiring.

```bash
cd dssq-groundstation/python
python3 -m dssq.gds --sim          # Windows: py -3 -m dssq.gds --sim
```

Check:

* The log shows `SIMULATION MODE - software spacecraft, no RF`, the warning
  `[station] callsign is the placeholder N0CALL: ...` (you set it in part 7), and every 10 s a
  line like `STATUS AOS sync=LOCK RSSI=... dBm ... frames=...`.
* Open **http://127.0.0.1:8080** in a browser. The header shows **AOS** and **LOCK** and the
  frame counter rises. **ALARM RED** with the reasons "beacon URGENT" (the simulated spacecraft
  boots in SAFE mode and asks for attention, as the real one does, part 10) and "SDLS key file
  not found: config/sdls_key.hex" (no key yet) is expected here.
* COMMANDING tab: type `NOOP` and press Enter. The command goes QUEUED -> RADIATED ->
  ACCEPTED -> EXECUTED. Try `HELP` and `PING 42`, then `MODE CRUISE`: the event list shows
  `[0x0012] MODE SAFE -> CRUISE (command)` and `spacecraft beacon URGENT -> NOMINAL`, and the
  alarm drops to YELLOW (only the key reason is left). After the first command the event list
  also shows `station identification (periodic): DE N0CALL DSS-Q1`: the station identifies its
  own transmissions (part 7.2).

Stop it with Ctrl+C. The tabs are explained in [13_Web_UI_Reference.md](13_Web_UI_Reference.md).

If not: `python3: command not found` or a message about TOML -> install Python 3.11+ (step 3.1.3).
Port 8080 in use -> close the other program, or edit `[gds] http_port` in
`dssq-groundstation/python/config/station.toml`.

---

## Part 4 - Flash the Nicla Sense ME

The Nicla Sense ME needs this project's own firmware ("ARU", attitude reference unit). It is the
only part flashed with the Arduino IDE, and it needs the internet once (board package and
library).

1. Arduino IDE 2 -> **Boards Manager** -> search `Nicla` -> install **Arduino Mbed OS Nicla Boards**.
2. **Library Manager** -> search `Arduino_BHY2` -> install version **1.0.8** (the version the
   firmware was checked against). If the IDE offers to install dependencies, choose
   "Install all".
3. Connect **only** the Nicla Sense ME to the PC with a micro-USB cable (nothing else wired to it).
4. File -> Open -> `nicla_sense_me_aru/nicla_sense_me_aru.ino`. Tools -> Board -> **Nicla Sense ME**,
   Tools -> Port -> the Nicla's port.
5. Click **Upload**. Check: "Done uploading".
6. Tools -> Serial Monitor, 115200 baud. Check: a line starting
   `VGQ-1 ARU (Nicla Sense ME) ready at I2C 0x2A, accel +/-... g, gyro +/-... dps, mag +/-... uT`.
   If it ends with `(fallback, range not reported)`, write that down for test T-SC-06
   (record MAG in [21_Test_Records.md](21_Test_Records.md)); it is not an error.
7. Unplug the USB cable. The Nicla is wired to the spacecraft in part 8.

If not: the board does not appear or the upload fails -> double-tap the Nicla's reset button
right after power-up to enter the bootloader (Arduino ABX00050 datasheet) and upload again.

---

## Part 5 - Set up the boards and import the two Apps

### 5.1 First start of each board

Do this once per board, with nothing wired to it yet.

1. **VENTUNO Q**: plug the 12 V / 5 A supply into the barrel jack and connect the board's USB-C
   to the PC. **UNO Q**: connect its USB-C to the PC.
2. Start App Lab and click the board. The first-start wizard asks for a **board name**, keyboard
   language, **Wi-Fi** and a **password**, and installs board updates. Let it finish.
3. Note each board's **IP address** (App Lab shows it; or in a terminal on the board,
   `hostname -I`). This guide writes them as `<UNO-Q-IP>` and `<VENTUNO-Q-IP>`.

Check: from the PC, `ssh arduino@<UNO-Q-IP>` and `ssh arduino@<VENTUNO-Q-IP>` log in with the
password you chose (type `exit` to leave).

If the UNO Q resets or disappears from App Lab: the PC's USB port may not supply enough current
(Arduino asks for a 5 V / 3 A source). Use a powered USB-C hub, or power it from 12 V on VIN as
in part 8.2 (USB-C and VIN can be connected at the same time).

### 5.2 Import the Apps

1. Connect App Lab to the **UNO Q**.
2. **My Apps** -> open any App -> click the **▼** next to its name -> **Import App** -> choose
   `dist/vgq1-spacecraft-unoq.zip`.
3. Check: the App **VGQ-1 Flight Software** appears under My Apps. On the board it is the folder
   `/home/arduino/ArduinoApps/vgq1-spacecraft`.
4. Connect App Lab to the **VENTUNO Q** and import `dist/dssq-groundstation-ventunoq.zip` the
   same way.
5. Check: the App **DSS-Q Ground Station** appears; folder
   `/home/arduino/ArduinoApps/dssq-groundstation`.

⚠ If an App of the same folder name already exists, App Lab imports the zip into a second folder
with the date added. To replace an App, follow part 16 first: deleting an App also deletes its
`data` folder, which holds counters that must never go back.

### 5.3 First run of the spacecraft App (nothing wired)

1. Open **VGQ-1 Flight Software** and press **Run**. App Lab compiles the sketch (no download:
   the libraries are inside the App), flashes the MCU and starts the Python EGSE. The first
   build takes a few minutes.
2. Check the App Lab console, on the tab with the Python output (App Lab shows three tabs: the
   launch log, the Python output and the sketch's serial output; the flight sketch prints
   nothing there by default). You should see (inside App Lab's container the App folder is
   called `/app`; your numbers differ):

   ```
   ... EGSE WARNING first start or counter lost (boot_count.txt missing): if this flight key was used before, generate a new key (tools/gen_sdls_key.py)
   ... EGSE INFO SCLK partition / SDLS TM epoch 1 issued (EGSE start)
   ... EGSE INFO VGQ-1 EGSE started: data in /app/data, boot counter 1
   ... EGSE INFO notifications provided to the MCU: cfdp_cancel, cfdp_req, sc_boot, sc_evr, ...
   ```

   "first start or counter lost" is normal on a first start (part 6.5 explains when it is not).
3. Every 10 s the EGSE prints a **status box**. This box is your main check while the spacecraft
   has no radio link. Example (your numbers differ):

   ```
   +--------------------------- VGQ-1 SPACECRAFT EGSE ----------------------------+
   | MODE SAFE  SCLK 1/95 s  BEACON URGENT(3)  SEQ IDLE/EMPTY(0)  (status age core 0 s, ext 2 s)
   | RADIO E22-400T33D(1) ch 23 = 433.125 MHz  rate code 2  pwr code 0  LBT/CAD 0  framing TM(0)
   | TX off  frames 18 (OID 14)  MCFC 18  period 5068 ms  busy 1267 ms  duty 25.0 %
   | UPLINK RSSI n/a  SNR n/a  noise n/a  FARM OPEN(1) V(R) 0  CMD acc 0 rej 0  rescued 0
   | FDIR 0x0001  sensors 0x0000  bus -999.0 C  radio fault 0  VC0 0 VC1 0 VC2 0 VC3 0 B  SSR 0.2 %
   | SDLS TC NONE(0)  TM NONE(0) OFF(0) epoch 1 sn 0  cadence stretch x1.00
   ...
   ```

   Right now, with nothing wired, `FDIR 0x0001` (bit 0 = RADIO_CFG: the radio could not be
   configured), `sensors 0x0000` and `bus -999.0 C` (no temperature reading: the Nicla Sense ME
   is not connected yet) are correct. `TC NONE TM NONE`: no key yet (part 6). The
   frame counter rises even without a radio, so it proves nothing yet.
4. Check the board: the 13 x 8 LED matrix on the UNO Q lights. Row 7, second column = SAFE mode;
   the right-hand end of row 7 blinks while a fault flag is set (layout in
   [06_Displays.md](06_Displays.md)).
5. Click **▼** next to the App name and switch **Run at startup** on (a DEFAULT badge appears).
   The spacecraft then starts by itself at every power-up, without a PC.

The flight software also reports **events** (EVR lines such as
`EGSE INFO EVR 0x0001 INFO BOOT VGQ-1 FSW 3.1 rst=0 POWER-ON radio=E22-400T33D TM TC NONE TM NONE`).
Its start-up events reach this console only if the EGSE was already listening when the MCU
started, and at most 8 per second are forwarded (config.h `kEvrNotifyMaxPerS`). On the owner's
board the MCU started about 4 s before the EGSE, so they were missed (`8 EVRs not forwarded`). All of them
also go into the telemetry, so the ground station shows them once the radio link works (part 11).

If not: a build error -> make sure you imported the zip from `dist/` (a folder copied from the
repository lacks the bundled libraries). The EGSE prints `waiting for the flight software status
(sc_status) ...` instead of the box for more than a minute -> the MCU is not running the flight
sketch: press Stop and Run again; see
[14_Troubleshooting_and_Event_Response.md](14_Troubleshooting_and_Event_Response.md).

### 5.4 First run of the ground App (nothing wired)

1. Open **DSS-Q Ground Station** and press **Run**. App Lab flashes the station sketch to the
   VENTUNO Q MCU and starts the ground data system.
2. Check the console:

   ```
   DSS-Q web commanding token created in /app/data/http_token: 3f9c... (enter it once in the dashboard)
   ... DSS-Q ground station running: dashboard on port 8080 (bound to 0.0.0.0); archive /app/data/archive; ...
   ```

   Copy the **token** (32 hexadecimal characters). It is also stored on the board in
   `/home/arduino/ArduinoApps/dssq-groundstation/data/http_token`.
3. On the PC open **http://&lt;VENTUNO-Q-IP&gt;:8080** (`hostname -I` on the board prints the IP), or by
   the board's name, **http://&lt;board name&gt;.local:8080** (the name in the SSH prompt
   `arduino@<board name>`; the owner's: `http://TanVentuno.local:8080`). Check: the dashboard "DSS-Q1 GROUND STATION"
   loads. Click **TOKEN** in the header and paste the token (the browser keeps it for this
   session; without it the dashboard is read-only).
4. Expected for now: ALARM RED with "radio backend unavailable: E22-400T33D serial port: ..."
   (no radio yet) and YELLOW "SDLS key file not found" (no key yet). The console and the event
   list also show the warning `[station] callsign is the placeholder N0CALL: set your amateur
   callsign in station.toml before transmitting (the uplink identifies with it)` until you do
   part 7.2.
5. Switch **Run at startup** on for this App too.

If the dashboard does not load from the PC: check first with
`curl http://<VENTUNO-Q-IP>:8080/api/state` (it should print JSON). Whether App Lab makes the
App's port reachable from the network has not yet been confirmed on hardware (VERIFY). If it is
not reachable but the App runs, use the host mode of part 15.

---

## Part 6 - Generate the security keys (after the import)

The radio link is protected by SDLS ([17_Security_Model.md](17_Security_Model.md)): only
holders of the key can command the spacecraft and, with encryption, read the telemetry. One
256-bit key is shared by both ends. The keys are generated **after** importing the Apps
because the zips never contain one.

### 6.1 Choose the protection (both ends must match)

| Use | Flight `config.h` (spacecraft) | `station.toml` `[sdls]` (ground) |
|---|---|---|
| **Amateur radio** (no encryption allowed): commands authenticated, telemetry readable | `#define VGQ_SDLS_TC 1` and `#define VGQ_SDLS_TM 0` | `tc_service = "AUTH"`, `tm_service = "NONE"` |
| Licence that allows encryption (the default in the code) | `#define VGQ_SDLS_TC 2` and `#define VGQ_SDLS_TM 2` | `tc_service = "AE"`, `tm_service = "AE"` |

Where to edit:

* **Spacecraft**: in App Lab open VGQ-1 Flight Software, file `sketch/src/config.h`, section
  "Downlink framing and SDLS". Change the two `#define` lines. (Or in a terminal on the UNO Q:
  `nano ~/ArduinoApps/vgq1-spacecraft/sketch/src/config.h`.)
* **Ground**: in a terminal on the VENTUNO Q:
  `nano ~/ArduinoApps/dssq-groundstation/data/station.toml`. The App copied its default there on
  the first run; always edit this `data/` copy, never `python/config/station.toml` (the App, and
  host mode of part 15, read only the `data/` copy).

### 6.2 Generate one key pair on the PC

In the repository folder on the PC:

```bash
python3 tools/gen_sdls_key.py --flight-header ~/vgq1-keys/sdls_key.h --ground-key ~/vgq1-keys/sdls_key.hex
```

(Windows: `py -3 tools/gen_sdls_key.py ...` with the same options; `~` means your user folder.)

Check: it prints `SDLS key written (SPI = 1):` with the two paths, and the folder `vgq1-keys`
in your home folder holds `sdls_key.h` and `sdls_key.hex`. Both files come from **one** key.

* Run the generator **once**, on one machine, and copy each of the two files to its board
  (6.3). Running it again, for example on the other board, makes a **different** key, and the
  two ends no longer understand each other.
* Keep `vgq1-keys` **outside** the repository folder: keys must never be committed or shared.
* `keys already exist (use --force to replace them on BOTH ends)` -> you already have a pair.
  Use `--force` only when you are replacing the key on both boards.

### 6.3 Copy each file to its board

```bash
scp ~/vgq1-keys/sdls_key.h   arduino@<UNO-Q-IP>:ArduinoApps/vgq1-spacecraft/sketch/src/sdls_key.h
scp ~/vgq1-keys/sdls_key.hex arduino@<VENTUNO-Q-IP>:ArduinoApps/dssq-groundstation/data/sdls_key.hex
```

The file names must stay exactly `sdls_key.h` (the flight build includes only that name) and
`sdls_key.hex` (the ground station, as an App and in host mode, reads `data/sdls_key.hex`
unless `[sdls] key_file` is an absolute path). The `data` folder exists after the ground App's
first run (part 5.4).

### 6.4 Restart both Apps

1. Spacecraft: press **Run** again (the sketch is rebuilt with the key).
2. Ground: press **Stop**, then **Run**.

Check:

* Spacecraft status box: `SDLS TC AUTH(1)  TM NONE(0) ...` (amateur choice) or
  `SDLS TC AE(2)  TM AE(2) PROTECTED(1) epoch N` (AE). With AE the console also shows
  `EVR 0x0120 INFO SDLS TM epoch N: downlink protected` when the EGSE hands over the epoch.
* Ground: the "SDLS key file not found" alarm reason is gone; the LINK & SECURITY tab shows the
  services you chose.
* Ground, **normal at this point**: a new YELLOW alarm reason `TC uplink BLOCKED: no TC
  sequence-number file ...` and a WARN event `no SDLS TC sequence-number file ...`. Every
  command carries a sequence number that must never repeat with one key, and the station has
  no counter file for the new key yet. It refuses commands until a housekeeping packet arrives
  that the spacecraft made at least 2 minutes after it booted (part 11; earlier ones are
  ignored, without any message). It then starts the counter by itself on the "time floor" - 4
  numbers per second counted from 1 January 2026, about 98 million in October 2026, which is
  above every number the key can have used - and releases the uplink: WARN event `SDLS TC
  sequence number re-synchronised to <n> (the time floor: 4 per second since 2026-01-01;
  sdls_last_sn 0) ...: TC uplink released`. Nothing to do, as long as the station's clock is
  right ([17_Security_Model.md](17_Security_Model.md) section 7.6).

If the box still shows `TC NONE(0) TM NONE(0)`: the header is not at
`ArduinoApps/vgq1-spacecraft/sketch/src/sdls_key.h`, or the sketch was not rebuilt (press Run).

### 6.5 Keep the key safe - three rules

1. The PC copies in `vgq1-keys` are secrets. Keep them on offline storage (you need the
   `sdls_key.h` again when you update the spacecraft App, part 16), or delete them.
2. Always change the key on **both** ends together.
3. If the spacecraft App's `data` folder is ever lost (App deleted without a backup, board
   re-installed), **generate a new key pair** before transmitting again. The folder holds the
   boot counter that the encryption depends on; with the same key it must never go back
   (`tools/gen_sdls_key.py`). After any key change on a system that already used another key,
   type `SDLS RESETREPLAY` on the ground and confirm it
   ([07_Operations_Procedures.md](07_Operations_Procedures.md),
   [17_Security_Model.md](17_Security_Model.md)).

---

## Part 7 - Settings for your station and site

Each setting is explained in [15_Configuration_Reference.md](15_Configuration_Reference.md).
After editing `config.h` press **Run** on the spacecraft App; after editing `station.toml`
restart the ground App.

**Your callsign goes in at both ends.** Both transmitters identify themselves in plain text, and
both settings default to the placeholder `N0CALL`, which is not a licensed callsign. Set
`VGQ_CALLSIGN` (7.1) **and** `[station] callsign` (7.2) before anything transmits on air. The
rules behind it: [09_Regulatory_and_Safety.md](09_Regulatory_and_Safety.md) section 1.3.
The callsign is the one on your amateur-radio licence; without a licence you have none. Keep
`N0CALL` until then: the ground refuses anything that does not look like a callsign (3-16
letters and digits, at least one of each: `TAN`, for example, stops the App), and a made-up one
may be someone else's.

### 7.1 Spacecraft (`sketch/src/config.h`)

| Setting | Default | Set it to |
|---|---|---|
| `VGQ_CALLSIGN` | `"N0CALL"` | your callsign. The spacecraft sends it in clear text at boot and every 600 s (EVR 0x0002 `DE <callsign>`) and in every idle frame as `DE <callsign> VGQ-1` |
| `kGeomagInclDeg` | 60.0 | the magnetic inclination at your site, degrees (positive = field points down) |
| `kGeomagDeclDeg` | 0.0 | the magnetic declination at your site, degrees east of true north |

Get both angles from a World Magnetic Model calculator (NOAA or BGS, free online).

### 7.2 Ground (`data/station.toml` on the VENTUNO Q)

| Table | Keys to check |
|---|---|
| `[station]` | `callsign` (default `"N0CALL"`): your callsign, see below; `name`, `latitude_deg`, `longitude_deg`, `altitude_m`, `antenna_gain_dbi`, `rx_line_loss_db` |
| `[spacecraft]` | `antenna_gain_dbi`, `tx_line_loss_db`, `distance_km` (enter it when you know it) |
| `[radio]` | `allowed_channels`: only channels your licence and band plan allow (default 20-24 = 430.125-434.125 MHz); `channel` (default 23 = 433.125 MHz) |
| `[science]` | `field_ut`: total magnetic field at your site in uT, from the same WMM calculator (needed for the magnetometer calibration) |

What the ground transmits with `[station] callsign`: whenever it sends commands, the station
also radiates the plain-text identification `DE <callsign> <name>` (for example
`DE G4ABC DSS-Q1`, with `<name>` from `[station] name`) through the same radio as the
commands: right after the first uplink of a run, then at least every 10 minutes while it keeps
transmitting, and once more when the uplink has been idle for 120 s (or the command
retransmission timer, if that is longer) (`gds.py` `StationIdent`).
The spacecraft cannot mistake it for a command: it counts the packet in its RF `cltu_bad`
counter and ignores it.

Check after restarting the ground App:

* The console and event list no longer show
  `[station] callsign is the placeholder N0CALL: set your amateur callsign in station.toml before
  transmitting (the uplink identifies with it)`.
* If the App does not start and names `[station] callsign`: a callsign has 3-16 letters and
  digits (at least one of each), optionally with `/` parts such as `G4ABC/P`.

These pairs must be equal at both ends, or the link does not work
([15_Configuration_Reference.md](15_Configuration_Reference.md) lists every pair):

| Spacecraft `config.h` | Ground `station.toml` | Default |
|---|---|---|
| `kRadioChannel` | `[radio] channel` | 23 |
| `kRadioRateCode` | `[radio] air_rate` | 2 (2.4 kbit/s) |
| radio model (`VGQ_RADIO` + A0 strap) | `[radio] model` | E22-400T33D |
| `VGQ_SDLS_TC` / `VGQ_SDLS_TM` | `[sdls] tc_service` / `tm_service` | AE / AE |

---

## Part 8 - Wire the spacecraft step by step

Before each step: **power off** (unplug the battery and USB). After each step: power on and do
the check. The full tables are in docs/03 section 4; the wiring diagram is docs/03 section 4.8.

### 8.1 Prepare

1. Put the UNO Q on a non-conductive surface.
2. Screw a **50 Ohm dummy load** onto the E22-400T33D's SMA socket now. It stays there until
   part 11.
3. Fit the two 10 kOhm resistors: one from the E22's M0 pin and one from its M1 pin to the
   3.3 V wire that will go to UNO Q JANALOG pin 4.

### 8.2 Power (configuration A, docs/03 section 4.2.1)

1. Battery (+) -> 4 A fuse -> UNO Q **JANALOG pin 8 (VIN)** and -> buck converter input.
2. Battery (-) -> UNO Q GND (JANALOG pin 6 or 7) and buck GND. One common ground for everything.
3. Do not connect the buck output to anything yet.

Check (meter, battery connected, boards not yet powered from the buck):

* Buck output: 5.0 V +/- 0.2 V, never above 5.5 V (the E22's absolute limit).
* JANALOG pin 8 to GND: the battery voltage (7-24 V allowed; a 3S pack reads 9-12.6 V), positive.

Then the UNO Q starts (power LED on) and the App runs by itself (Run at startup).

### 8.3 Radio E22-400T33D (docs/03 section 4.3)

| E22 pin | to UNO Q |
|---|---|
| 1 M0 | D2 (+ 10 kOhm to 3.3 V) |
| 2 M1 | D3 (+ 10 kOhm to 3.3 V only) |
| 3 RXD | header pin printed **SCL** |
| 4 TXD | header pin printed **SDA** |
| 5 AUX | D7 |
| 6 VCC | buck 5 V (+ 470-1000 uF and 100 nF directly at the pins) |
| 7 GND | GND |

The UNO Q pins printed SDA/SCL are used as a serial port (UART) here, not as I2C. The sensors use
A4/A5 instead (step 8.5).

Check (power on, wait for the App and its status box):

* The status box shows `FDIR 0x0000`: bit 0 (RADIO_CFG) is clear. At boot the software wrote
  the module's configuration and read it back, which proves both UART directions and the
  M0/M1/AUX wires. If the start-up events reached the console you also see
  `EVR 0x0306 INFO radio E22-400T33D OK: ch 23 code 2 pwr 33 dBm sens -128.0 dBm datasheet`.
* `busy` is about 1530 ms and `duty` about 25 % (SAFE mode; measured on the owner's bench,
  record 2026-10-12-A in docs/21); LED matrix row 0 lights during
  every transmission; the right-hand end of row 7 no longer blinks.

If not: `FDIR 0x0001` (or `EVR 0x0306 ... FAILED`) -> TXD/RXD swapped (E22 TXD goes to "SDA"),
no 5 V at VCC, missing common ground, M0/M1 swapped. `FDIR 0x0002` (AUX_TIMEOUT) -> the AUX wire.
See [14_Troubleshooting_and_Event_Response.md](14_Troubleshooting_and_Event_Response.md).

### 8.4 Model strap: leave A0 open

For the E22-400T33D, nothing is connected to JANALOG pin 9 (A0).

Check: the status box line `RADIO E22-400T33D(1) ...` (if the start-up events reached the
console: `EVR 0x0308 INFO radio model E22-400T33D (strap open, 0/5 low; build E22-400T33D)`).

### 8.5 Sensor bus, one instrument at a time (docs/03 section 4.7)

All instruments share A4 (SDA) and A5 (SCL) and 3.3 V from JANALOG pin 4. Add them **one at a
time**, power off before each, and check after each. Where to look:

* the EGSE status box line `FDIR ... sensors 0x....` (the HK `sensor_health` bits: b0..b6 Nicla
  Sense ME and its sensors, b7 AS7265X, b8 RM3100 outboard, b9 RM3100 inboard), and
* LED matrix row 6: one lit column per healthy bit.

| Order | Instrument | Wiring | Check after power-on: `sensors` in the status box |
|---|---|---|---|
| a | AS7265X (it carries the bus pull-ups) | its pins 3.3V, GND, SDA -> A4, SCL -> A5; INT not connected; front "I2C" jumper closed, JP2 open. **Or** a Qwiic cable from its Qwiic socket to the UNO Q Qwiic socket: then the A4/A5 bus needs its own 4.7 kOhm pull-ups to 3.3 V for steps b-d (docs/03 section 4.7) | `0x0080` (bit 7) |
| b | RM3100 outboard | 3.3 V, GND, SDA -> A4, SCL -> A5, I2CEN -> 3.3 V, SA0 -> GND, SA1 -> GND | `0x0180` (+ bit 8) |
| c | RM3100 inboard (optional) | as b, but SA0 -> 3.3 V and SA1 -> 3.3 V | `0x0380` (+ bit 9) |
| d | Nicla Sense ME | J2 pin 1 SDA -> A4, pin 2 SCL -> A5, pin 6 GND -> GND, pin 9 VIN -> buck 5 V; pin 7 not connected | `0x03BF` within seconds (+ bits 0-5), `0x03FF` once bit 6 (gas/air-quality fusion) is set after the gas sensor's warm-up |

Without the inboard RM3100 the final value is `0x01FF`. If the start-up events reached the
console, you also see `EVR 0x0706 INFO AS7265X HW ... on A4/A5` (or `on Qwiic`: spectrometer identified, and where) and
`EVR 0x0910 INFO RM3100 OB BIST PASS X ok Y ok Z ok (reg ...)` (outboard self-test, with
the value of the self-test register; 0x0911 for the inboard unit); the ground shows them later in any case. LED matrix row 6 lights one column per bit.

Bus check if an instrument stays absent: with power **off**, the resistance from A4 to 3.3 V and
from A5 to 3.3 V reads roughly 1-5 kOhm (docs/03 section 4.7.2). Open circuit = no pull-ups.
Under 1 kOhm = too many pull-ups (open a breakout's pull-up jumper).

If an instrument is plugged in after boot, it is found later by itself
(`EVR 0x060B INFO instrument acquired: ...`).

### 8.6 Mount the magnetometers

Put the RM3100(s) on the non-magnetic boom, the outboard unit at the tip, as far as practical
(0.5-1 m) from the radio, the battery and the power wires. Twist the boom cable pairs. Rules:
docs/03 section 9. Enter the two boom distances in `station.toml` `[science] r_outboard_m` and
`r_inboard_m`.

Check: the EGSE status box shows the radio line, `FDIR 0x0000`, `frames` rising, the sensors you
fitted, and (with a key) the SDLS line `TC AE(2) TM AE(2) PROTECTED(1) epoch N` or
`TC AUTH(1) TM NONE(0)`.

---

## Part 9 - Wire the ground station step by step

Full tables: docs/03 section 5; diagram: docs/03 section 5.5.

### 9.1 Power and radio supply

1. The VENTUNO Q runs from its 12 V / 5 A supply (barrel jack).
2. Screw a **50 Ohm dummy load** (or, for part 11, the antenna or the attenuator set) onto the
   ground E22-400T33D.
3. Supply the module:
   * **On the EBYTE E15-USB-T2 (default, 9.2):** the module takes 5 V from the VENTUNO Q USB-A
     port through the adapter; put the adapter's supply jumper on **5V**. That is enough to
     receive. Transmitting (commands, part 12) draws up to 1.2 A (T33D manual 2.2), more than a
     USB port is meant to supply (docs/20 V-69): before the ground transmits, give the module
     its own 5 V supply as in 9.3.
   * **On a plain USB-UART adapter (9.3):** 5 V to pin 6 VCC from VENTUNO Q **JANALOG pin 5**
     (+5V_LIMITED) or from its own 5 V supply (>= 2 A); GND to pin 7; 470-1000 uF + 100 nF at
     the pins. Check: 4.75-5.25 V at the module's VCC pin.

### 9.2 Ground radio on the EBYTE E15-USB-T2, one-time configuration

The default ground radio interface is EBYTE's USB test board **E15-USB-T2** (CP2102 USB-UART):
the E22 plugs straight onto its 7-pin socket and the board into a VENTUNO Q USB-A port
(`/dev/ttyUSB0`). Its jumper caps set the module's M0 and M1: **a fitted cap = 0, a removed cap
= 1**; a second jumper selects the module supply, 3.3 V or 5 V (EBYTE E51-TTL-500 datasheet
v1.0, sections 3.6 and 5, which describe this board). On the owner's board (USB plug up) the
supply header (3 pins, "5V" printed at its top) is on the left edge and the M0/M1 header (two
pairs) on the right edge (docs/21 record 2026-10-13-A).

A new E22 has the RSSI byte and the noise register switched off; the ground needs both. The
spacecraft configures its own radio at every boot; the ground radio is configured **once** now.

1. In App Lab press **Stop** on the ground App: while it runs it holds the serial port.
2. Supply jumper on **5V**.
3. Configuration mode is **M1 = 1, M0 = 0**: one cap on the **M0** pair, the M1 pair open.
   (Both caps removed is the E51's configuration mode in that datasheet, but an E22's **deep
   sleep**, in which it does not answer.) Not sure which pair is M0? Put the cap on one pair,
   run step 4, and on `no reply` move it to the other pair.
4. In a terminal on the VENTUNO Q (`ssh arduino@<VENTUNO-Q-IP>`):

   ```bash
   ls -l /dev/serial/by-id/                 # a Silicon_Labs_CP2102 entry -> ttyUSB0
   cd ~/ArduinoApps/dssq-groundstation/python
   python3 -m dssq.radio.e22 --port /dev/ttyUSB0 --write --config ~/ArduinoApps/dssq-groundstation/data/station.toml
   ```

   Check: the output shows `model: E22-400T33D`, `product info: ...`, `written.` and a table with
   `channel 23`, `freq_mhz 433.125`, `air_rate 2.4k`, `uart_bps 9600`, `rssi_byte True`,
   `rssi_noise_enable True`.
5. Fit **both** caps (M0 = M1 = 0, normal mode).
6. `[gds] rcu = false` (the default in `data/station.toml`): the ground never switches the module's
   mode itself, and the commands that need that (`RADIO`, `GSCAN`, `WAKE`, `LINKRATE`,
   `LINKCHAN`) are refused. To change channel or rate, repeat steps 1-5 with the new values in
   `station.toml`, or use the Radio Control Unit (9.3).

Press **Run** on the ground App. Check: no RADIO error at start, and every 10 s
`STATUS LOS sync=SEARCH ...` until the spacecraft transmits.

If not: `no reply: is the module in CONFIG mode` -> the cap on the other pair, the supply jumper,
the module pushed fully onto the socket. `ERROR RADIO ground E22-400T33D did not answer the
configuration read` at start -> `rcu = true` in `data/station.toml` without the 9.3 wires: set it
to `false`. `permission denied` -> `sudo usermod -aG dialout $USER`, log out and in again. A
different device name (`/dev/ttyUSB1`, `/dev/ttyACM0`) -> use it here and in `station.toml`
`[radio] port`.

### 9.3 Option: Radio Control Unit (M0/M1/AUX)

With the Radio Control Unit the VENTUNO Q MCU switches the module between NORMAL, WOR,
CONFIG and SLEEP itself, which `RADIO`, `GSCAN`, `WAKE`, `LINKRATE` and `LINKCHAN` need. It
needs the module's M0, M1 and AUX pins, so the module sits on a plain **3.3 V-logic** USB-UART
adapter instead of the E15-USB-T2 (a 5 V adapter can damage the E22), with its own supply (9.1):
adapter TXD -> E22 pin 3 RXD, adapter RXD -> E22 pin 4 TXD, adapter GND -> E22 pin 7 GND, and

| E22 pin | VENTUNO Q |
|---|---|
| 1 M0 | D2 (+ 10 kOhm to 3.3 V) |
| 2 M1 | D3 (+ 10 kOhm to 3.3 V) |
| 5 AUX | D4 |
| 7 GND | GND |

Do the one-time configuration of 9.2 (steps 1, 4) with wires instead of caps, **before** the
M0/M1 wires go to D2/D3: E22 pin 2 M1 -> 3.3 V (JANALOG pin 4), pin 1 M0 -> GND. Then set
`[gds] rcu = true` and press **Run**. Check: an event
`ground E22-400T33D: ch 23 (433.125 MHz) rate 2.4k pwr 0 LBT/CAD False - matches station.toml`
and the RED "radio backend unavailable" alarm is gone.

If it says `MISMATCH [...]`: repeat the configuration (docs/03 section 5.3.1).

### 9.4 Lamps and buzzer

| Item | Wiring |
|---|---|
| AOS (green LED) | D5 -> 330 Ohm -> LED -> GND |
| ALARM (red LED) | D6 -> 330 Ohm -> LED -> GND |
| UPLINK (yellow LED) | D7 -> 330 Ohm -> LED -> GND |
| Buzzer (5 V active) | A0 -> 1 kOhm -> NPN base; buzzer + to 5 V (JANALOG pin 5), buzzer - to the collector; emitter to GND |

Check:

1. With the App running, ALARM is off or steady (a YELLOW reason) - see the dashboard header.
2. Press **Stop** in App Lab: within 5 s AOS goes dark and ALARM blinks fast (5 Hz) = "no status
   from the ground software". Press **Run**: it stops blinking.
3. Buzzer: unplug the USB-UART adapter for 40 s. After 30 s the alarm turns RED: ALARM blinks
   at 4 Hz and the buzzer sounds for 0.4 s. Plug it back in; the alarm clears.

### 9.5 CardKB keyboard

Option A (try first): CardKB GND -> GND, CardKB "5V" wire -> **3.3 V** (JANALOG pin 4), SDA ->
the header pin printed **SDA**, SCL -> the header pin printed **SCL**. M5Stack colours: black GND,
red 5V, yellow SDA, white SCL; check the print on your cable.

Check: COMMANDING tab -> Station I/O shows the keyboard present; type `HELP` on the CardKB and
press Enter: the dashboard shows the typing line and then the command list.

If not: use option B (5 V through a BSS138 level shifter, docs/03 section 5.4). Type `KEYTEST` +
Enter to see raw key codes; ESC leaves that mode.

---

## Part 10 - First power-up of the complete system

Both radios still have their **dummy loads** on. This part checks power, start-up order and the
software on both ends without a radio link.

1. Power the **ground station** first and wait until its App runs (dashboard reachable).
2. Power the **spacecraft**. Its MCU waits until the UNO Q's Linux side has started (this can
   take a minute or more), then the flight software starts in **SAFE** mode.

Check, spacecraft (App Lab console or LED matrix):

* Status box: `MODE SAFE`; `RADIO E22-400T33D(1) ch 23 = 433.125 MHz rate code 2 pwr code 0`;
  `frames` rising, `period` about 6110 ms, `busy` about 1530 ms, `duty` about 25 % (SAFE mode
  sends one frame per about 6 s at 2.4 kbit/s: 4 x the radio busy time of one 236-octet frame,
  `frame_period_ms()` in fsw/datalink.cpp); `FDIR 0x0000`; `sensors 0x03FF`; with a key the SDLS
  line (`TC AE(2) TM AE(2) PROTECTED(1) epoch N` or `TC AUTH(1) TM NONE(0)`).
* `BEACON URGENT(3)` is normal after a start: the spacecraft came up in SAFE mode by itself and
  "asks" for attention until the ground acknowledges it (part 12).
* LED matrix: row 0 lights during each frame; in row 7 the second column (SAFE) is lit.

Check, ground: header **LOS** (no signal yet, the dummy loads stop it), no RED alarm.

---

## Part 11 - First contact on the bench

Two transmitters on one table can damage each other's receivers. Use one of these:

* **Coax (best)**: spacecraft SMA -> **30 dB (>= 5 W)** -> **40 dB** -> **30 dB (>= 5 W)** ->
  ground SMA. 100 dB in total: the ground receives about 33 - 100 = **-67 dBm**. Both outer
  attenuators must take the full power of the radio next to them (both ends transmit).
* **Antennas far apart**: at least 10 m, better 100 m (docs/03 section 10).
* **A step attenuator in line on one side**, antennas still fitted (record 2026-10-13-A: 54 dB
  gave RSSI -39 / -40 dBm on the bench). It must take the full 2 W of the radio on its side.
  Fitted on the spacecraft side, it also cuts what the spacecraft radiates.

1. Power off both. Remove the dummy loads and connect the coax set (or the antennas).
2. Power the ground station, then the spacecraft.

Check (within one or two frame periods, about 5-10 s in SAFE mode):

* Dashboard header: **AOS**, **LOCK**, frames counting; the green **AOS lamp** lights.
* RECEIVER tab: RSSI about -67 dBm with the 100 dB set (the exact value depends on cable and
  attenuator tolerances); frames lost 0; RS decoder: no uncorrectable code blocks.
  ⚠ RSSI jumping between -1 and -256 dBm (`SNRest` about 71 / -184 dB) means the receiver is
  overdriven: add attenuation or distance at once (docs/20 V-70).
* LINK & SECURITY tab: the spacecraft identification `DE <your callsign> VGQ-1`; with AE the TM
  epoch and "protected".
* The ground console prints every 10 s: `STATUS AOS sync=LOCK RSSI=-67.0 dBm ... frames=...`.
* **ALARM RED with the reason "beacon URGENT"**, the ALARM lamp blinking at 4 Hz and a short
  buzzer tone with three chirps are expected now: it is the spacecraft's SAFE-mode beacon
  (previous part). The spacecraft's start-up events also appear in the event list now.
  Part 12 acknowledges it.
* With a key: the YELLOW reason `TC uplink BLOCKED` from part 6.4 goes away with the first
  housekeeping packet the spacecraft makes at least 2 minutes after its boot (so up to about
  2 minutes after the spacecraft was switched on, plus one housekeeping period), with the WARN
  event `SDLS TC sequence number re-synchronised to <n> (the time floor: 4 per second since
  2026-01-01; sdls_last_sn <m>) ...: TC uplink released`.

![Dashboard OVERVIEW tab with signal acquired](img/ui_overview.png)

*The OVERVIEW tab with a signal (here from the simulator): AOS, LOCK and the alarm state in the
header; signal, RSSI, SNR, margin and frame tiles below. Every tab and metric:
[13_Web_UI_Reference.md](13_Web_UI_Reference.md).*

If there is no AOS:

| Check | How |
|---|---|
| Same channel and rate at both ends | EGSE box `ch 23 ... rate code 2` vs `station.toml` `[radio]` and the 9.3 event |
| The ground radio is open | no RED "radio backend unavailable" |
| `sync=SEARCH` while bytes arrive | settings differ between the ends, or the ground radio was never configured (9.2) |
| Frames arrive but are rejected by SDLS | different keys or services at the two ends (part 6) |
| Nothing at all | the attenuator chain is not connected; the spacecraft status box shows `FDIR 0x0000` and `frames` rising? |

Then record the bench result (RSSI with a known attenuation) in record RSSI of
[21_Test_Records.md](21_Test_Records.md): it is the start of the RSSI calibration (T-RF-02).

---

## Part 12 - Your first commands

Type on the **CardKB** (Enter sends) or in the **COMMANDING** tab of the dashboard.

| Type | You should see |
|---|---|
| `NOOP` | the command history goes QUEUED -> RADIATED -> ACCEPTED -> EXECUTED; the yellow **UPLINK lamp** is lit while it is outstanding; LED matrix row 1 flashes when the spacecraft receives it |
| `PING 1234` | EXECUTED, with the round-trip time |
| `MODE CRUISE` | `EVR 0x0012 INFO MODE SAFE -> CRUISE (...)`; with the next housekeeping packet (within about a minute) the beacon drops from URGENT and the RED alarm clears (a mode command acknowledges the SAFE start-up); magnetometer, attitude, spectrometer and environment packets start (CRUISE cadence: MAG every 30 s, ATT 60 s, ENV 120 s, SPEC 300 s; fsw/datalink.cpp) |
| `RADIO` | (ground only) reads back the ground radio configuration as an event |
| `HELP` / `HELP SPECCFG` | the command list / one command's arguments and ranges |

Station identification: right after your first command the event list shows
`station identification (periodic): DE <your callsign> DSS-Q1`, and the LINK & SECURITY tab
(uplink table, row "Station identification") counts each one. The spacecraft's RF counter
`cltu_bad` (RECEIVER tab, two-way link table, "Frames / CLTUs ... bad") rises by 1 for each
identification it hears: that is normal, it is the identification text, not a damaged
command. The station repeats it at least every 10 minutes while you command, and once more
about 2 minutes after your last command
([09_Regulatory_and_Safety.md](09_Regulatory_and_Safety.md) section 1.3).

Why the beacon behaves like this: the beacon level is latched until the ground makes contact
(an accepted command), and "came up in SAFE by fault protection" keeps it URGENT until a `MODE`
command is executed (fsw/autonomy.cpp `beacon_level()`). `MODE SAFE` acknowledges it too, if you
want to stay in SAFE mode.

Every command and its arguments: [05_Telemetry_and_Command_Dictionary.md](05_Telemetry_and_Command_Dictionary.md).

**Hazardous commands** (marked [HAZARDOUS] by `HELP`, e.g. `TXINHIBIT 60`) are not sent at once:
the dashboard asks for **CONFIRM** (on the CardKB: press Enter on an empty line). With
`[commanding] require_two_person_for_hazardous = true` a second person also presses **AUTH** in
the web dashboard. Routine operations: [07_Operations_Procedures.md](07_Operations_Procedures.md).

If a command stays RADIATED: the spacecraft did not receive it. Check the uplink RSSI on the
SPACECRAFT tab, the key/services (part 6), and that `FARM` is not in LOCKOUT (type `UNLOCK`).

If a command is REJECTED with `TC uplink BLOCKED: ...`: the station is still waiting for the
first housekeeping packet after the key was installed that the spacecraft made at least
2 minutes after its boot (part 6.4). Wait until the WARN event
`... re-synchronised ...: TC uplink released` appears, then send it again.

---

## Part 13 - Switching to the 5 W E22-400T37S (the A0 strap)

One spacecraft binary serves both E22 modules: a jumper on A0 selects the 5 W module at boot,
no code edit (docs/03 section 4.5).

Before you start: a licence that allows 5 W; a 12 V supply of at least 1.5 A per module with a
2 A fuse; attenuators rated >= 10 W for the bench; the module on its E22-400TBH-02 board.

### 13.1 Spacecraft

1. Power off. Antenna or a >= 10 W dummy load on the TBH-02's SMA socket.
2. On the TBH-02 board remove **all four jumper caps**: M0, M1 (bottom header, pins 7-8 and
   9-10) and RXD, TXD (next to the USB-C socket).
3. Wire the header (docs/03 section 4.4): 10 M0 -> D2, 7 M1 -> D3, 6 RXD -> "SCL",
   5 TXD -> "SDA", 4 AUX -> D7, 1 GND -> GND; 10 kOhm pull-ups from M0 and M1 to 3.3 V.
4. Power the board from its own fused 12 V branch (DC jack or green terminal, 4.5-15 V).
5. Fit the strap: a wire from **JANALOG pin 9 (A0)** to a **GND** pin (JANALOG pin 6 or 7).
   ⚠ Never connect A0 to 5 V or VIN.
6. Power on (the strap is read once at boot).

Check:

* Status box: `RADIO E22-400T37S(2) ...` and `FDIR 0x0000` (radio configured and verified).
* Events (EGSE console, or the ground's event list once the link works):
  `EVR 0x0308 INFO radio model E22-400T37S (strap to GND, 5/5 low; build E22-400T33D)` and
  `EVR 0x0306 INFO radio E22-400T37S OK: ch 23 code 2 pwr 37 dBm sens -126.0 dBm datasheet`.
  `EVR 0x0308 WARN ... (strap SPLIT, ...)` means a loose jumper.
* The transmit duty stays at or below 50 % (status box `duty`).
* Ground SPACECRAFT tab: the RF packet's radio model reads E22-400T37S.

### 13.2 Ground station

* **Ground keeps the E22-400T33D**: set `[radio] model = "auto"` in `data/station.toml`; the
  station then follows the model the spacecraft reports.
* **Ground also uses an E22-400T37S**: first set `[radio] model = "E22-400T37S"` and
  `fault_log = true` in `data/station.toml` (the one-time configuration writes them into the
  module). Give the TBH-02 its own 12 V >= 1.5 A supply and a dummy load, plug its USB-C into the
  VENTUNO Q and keep its RXD/TXD caps fitted. Do the one-time configuration of 9.2 with only the
  M1 cap removed (configuration mode) instead of the wires. Then remove both M0/M1 caps and wire
  the RCU: 10 M0 -> D2, 7 M1 -> D3, 4 AUX -> D4, 8 GND -> GND, 10 kOhm pull-ups on M0/M1.

Check: no YELLOW "radio model mismatch" reason on the dashboard.

⚠ The ground station has no model strap: on the VENTUNO Q, A0 drives the buzzer transistor
(part 9.4). Never wire it to GND as a strap; the ground's model is chosen only in
`station.toml`.

Going back to the T33D: remove the A0 wire and power-cycle the UNO Q.

---

## Part 14 - Going for distance

Plan first, then measure. The link budget is explained in
[02_Link_Budget_and_RF_Analysis.md](02_Link_Budget_and_RF_Analysis.md).

1. **Predict**: on the PC,
   `python3 tools/link_budget.py --model E22-400T33D --rate-code 2 --distance-km 25 --gr 12`
   prints the free-space budget and the margin.
2. **Height beats everything.** Radio horizon = 4.12 x (sqrt(h1) + sqrt(h2)) km with the antenna
   heights in metres: 2 m and 10 m -> 18.9 km; a 500 m hill and 10 m -> 105 km
   (`tools/link_budget.py`).
3. **Keep the first Fresnel zone clear**: radius 17.32 x sqrt(d1 x d2 / (f_GHz x d)) m; at
   433 MHz over 10 km that is about 42 m at mid-path.
4. **Ground antenna**: a 430-440 MHz Yagi with a published gain and a short, low-loss cable. Enter
   the real gain and cable loss in `station.toml` (`antenna_gain_dbi`, `rx_line_loss_db`).
5. **Slowest rate is the most sensitive.** The E22 is already at its most sensitive setting,
   2.4 kbit/s (codes 0-2 are all 2.4 kbit/s). Faster rates cost about 10 x log10(rate / 2400) dB.
   Change rate or channel only with the coordinated ground directives `LINKRATE` and `LINKCHAN`:
   both ends switch, and the spacecraft reverts by itself if the new setting loses the uplink.
6. **More range**: the SX126x option (SPI) at SF12 is computed to be about 12-15 dB more
   sensitive than the E22 at 2.4 kbit/s (docs/03 section 4.6, docs/02).
7. Enter the measured distance in `[spacecraft] distance_km`: the RECEIVER tab then shows
   predicted against measured received power ("excess loss").
8. Write everything down in records RNG and RSSI of [21_Test_Records.md](21_Test_Records.md).

---

## Part 15 - Host-mode fallback for the ground station

Use this only if the App mode does not work for you:

* the dashboard cannot be reached from the network while the App runs (part 5.4), or
* the App cannot open the radio's serial port (RED "radio backend unavailable: ... serial port"
  although `ls /dev/serial/by-id/` lists the adapter), or
* you want the ground software as a plain Linux service.

In host mode the same ground software runs directly on the VENTUNO Q's Linux (a systemd
service) instead of in App Lab's container. The station sketch on the MCU stays the same.

1. In App Lab, **Stop** the ground App and switch its **Run at startup** off. Never run the App
   and the service at the same time (they would fight over the radio port, port 8080 and the
   Router Bridge). The station sketch flashed by the App should stay on the MCU after the App is
   stopped (not yet confirmed on hardware, VERIFY): if the dashboard later shows the station I/O
   offline, press Run once in App Lab to flash it, then Stop again before starting the service.
2. Nothing to copy. Host mode runs `python -m dssq.gds --app-data <App>/data`, so it uses the
   App's own `data` folder exactly as the App does: `data/station.toml` (your settings from
   part 7), the archive `data/archive` with the uplink sequence-number file `sdls_sn.txt`, the
   key `data/sdls_key.hex` and the web token `data/http_token`. Both modes therefore share
   **one** archive and **one** set of security counters. As in the App, the dashboard listens on
   the network and commanding needs the same token.

   ⚠ Always start the ground software on the board with `--app-data` (the service below does).
   A run without it (`python3 -m dssq.gds` in `python/`) keeps a separate, fresh archive with its
   own uplink sequence-number file: it refuses commands until a housekeeping packet arrives
   (YELLOW `TC uplink BLOCKED`), and switching between the two archives with the same key can
   re-use sequence numbers, which with encryption (AE) re-uses encryption nonces.
3. Install and start the service:

   ```bash
   ~/ArduinoApps/dssq-groundstation/host/install_station.sh --service
   ```

   It installs `python3-venv` if needed, creates the virtual environment `~/.venvs/dssq-gds`
   with the `arduino-router-bridge` package (needs the internet once; App Lab's container already
   has it) and installs and starts the `dssq-gds` systemd service for this App folder and your
   user. Without that package the front panel, the RCU and the SX126x radio stay disabled
   ("router bridge unavailable"). Without `--service` the script only prepares the environment
   and prints the command for a run in the terminal.
4. Watch the log: `journalctl -u dssq-gds -f`. After editing `data/station.toml`:
   `sudo systemctl restart dssq-gds`. To keep the dashboard on the board only, add
   `Environment=DSSQ_HTTP_HOST=127.0.0.1` under `[Service]` in
   `/etc/systemd/system/dssq-gds.service`, then `sudo systemctl daemon-reload` and restart.

Check: the dashboard loads at http://&lt;VENTUNO-Q-IP&gt;:8080 and shows the same data as before.

Back to App mode: `sudo systemctl disable --now dssq-gds`, then Run the App and switch its
Run at startup on again.

---

## Part 16 - Updating or re-importing an App without losing state

Deleting an App in App Lab also deletes its `data` folder. On the spacecraft that folder holds the
boot counter (`boot_count.txt`) and the last accepted command number (`sdls_last_sn.txt`); on
the ground it holds `station.toml`, the key, the token and the archive with the uplink counter.
Move the folder out of the way on the board before deleting the App, and move it back before the
new App runs for the first time. A move on the same board is instant and needs no free space.

1. In App Lab press **Stop** on the App.
2. In a terminal on the board, move the state out of the App folder:

   ```bash
   # UNO Q (spacecraft): the data folder and the flight key header
   mv ~/ArduinoApps/vgq1-spacecraft/data ~/vgq1-data-keep
   mv ~/ArduinoApps/vgq1-spacecraft/sketch/src/sdls_key.h ~/vgq1-sdls_key.h.keep
   # VENTUNO Q (ground): the data folder
   mv ~/ArduinoApps/dssq-groundstation/data ~/dssq-data-keep
   ```

3. In App Lab: **▼ -> Delete App**, then import the new zip (part 5.2). Do **not** press Run yet.
4. Move the state back:

   ```bash
   # UNO Q
   mv ~/vgq1-data-keep ~/ArduinoApps/vgq1-spacecraft/data
   mv ~/vgq1-sdls_key.h.keep ~/ArduinoApps/vgq1-spacecraft/sketch/src/sdls_key.h
   # VENTUNO Q
   mv ~/dssq-data-keep ~/ArduinoApps/dssq-groundstation/data
   ```

   If `mv` reports that `data` already exists in the new App folder, the new App has already run
   once: stop it, look inside with `ls ~/ArduinoApps/<App>/data`, and only then replace it.
5. Press **Run**. Check: the spacecraft console says `boot counter N` with N higher than before,
   and does **not** say "first start or counter lost"; the ground keeps its settings and archive.

If the spacecraft data was lost: generate a new key pair for both ends (part 6.5, rule 3).
For a copy off the board, `scp -r` the folder to the PC as well. More on key management:
[07_Operations_Procedures.md](07_Operations_Procedures.md) and
[17_Security_Model.md](17_Security_Model.md).

---

## Part 17 - Troubleshooting pointers

The complete fault tree and every event: [14_Troubleshooting_and_Event_Response.md](14_Troubleshooting_and_Event_Response.md).

| Symptom | First thing to check | Part |
|---|---|---|
| Build error in App Lab | import the zip from `dist/`, not a copied folder | 5 |
| Ground WARN `[station] callsign is the placeholder N0CALL`, or the ground App does not start and names `[station] callsign` | set your callsign in `data/station.toml` (and `VGQ_CALLSIGN` on the spacecraft) | 7 |
| RF `cltu_bad` rises by 1 about every 10 minutes while commanding | normal: the station's own identification `DE <callsign> <name>` | 12 |
| Status box `FDIR 0x0001` / `EVR 0x0306 ... FAILED` (spacecraft radio) | E22 TXD -> "SDA", RXD -> "SCL"; 5 V at VCC; common ground | 8.3 |
| Instrument absent | one at a time; SDA/SCL swapped; pull-ups 1-5 kOhm; 3.3 V present | 8.5 |
| Nicla Sense ME absent although powered | firmware not flashed; VIN below 3.5 V | 4, 8.5 |
| Dashboard not reachable from the PC | `curl http://<VENTUNO-Q-IP>:8080/api/state`; host mode | 5.4, 15 |
| RED "radio backend unavailable" | USB-UART plugged in; `[radio] port`; the App was stopped for 9.2? | 9 |
| `MISMATCH` in the ground radio event | one-time configuration | 9.2 |
| ALARM lamp blinking fast, AOS dark | the ground App is not running | 9.4 |
| `sync=SEARCH` with bytes arriving | channel/rate differ, ground radio not configured | 11 |
| Frames rejected by SDLS | keys or services differ between the ends | 6 |
| YELLOW `TC uplink BLOCKED: no TC sequence-number file ...`, commands REJECTED, after installing the key | normal until the first housekeeping packet made at least 2 minutes after the spacecraft's boot arrives; the station then releases the uplink by itself (its clock must be right) | 6.4, 11 |
| Commands stay RADIATED | uplink path; keys; `UNLOCK` after a FARM lockout | 12 |
| YELLOW "reset cause BROWNOUT" | the radio supply sags while transmitting; restore with `TXPWR 0` after fixing it | docs/03 4.2.2 |
| `EVR 0x0311` (T37S under-voltage) | supply below 4.5 V under the transmit load: thicker wires, bigger supply | 13 |
| Spacecraft console: "first start or counter lost" after an update | the data folder was not restored: generate a new key pair | 16 |

The hardware tests that close the remaining open items are in
[08_Verification_and_Test.md](08_Verification_and_Test.md); write your measurements into
[21_Test_Records.md](21_Test_Records.md).
