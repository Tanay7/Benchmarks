# 10 - References

## CCSDS Recommended Standards (Blue Books) implemented

| Document | Title | Issue | Used for |
|---|---|---|---|
| CCSDS 131.0-B-5 | TM Synchronization and Channel Coding | Issue 5, September 2023 | ASM, RS(255,223) E=16 dual basis, shortening, pseudo-randomizer (255-bit sequence) |
| CCSDS 132.0-B-3 | TM Space Data Link Protocol | Issue 3, October 2021 | TM transfer frames, virtual channels, FHP, OCF, FECF |
| CCSDS 133.0-B-2 | Space Packet Protocol | Issue 2, June 2020 | TM/TC space packets, idle packets |
| CCSDS 231.0-B-4 | TC Synchronization and Channel Coding | Issue 4, July 2021 | CLTU, BCH(63,56) |
| CCSDS 232.0-B-4 | TC Space Data Link Protocol | Issue 4, October 2021 | TC transfer frames, Type-AD/BD/BC |
| CCSDS 232.1-B-2 | Communications Operation Procedure-1 | Issue 2, September 2010 | FARM-1, FOP-1, CLCW |
| CCSDS 301.0-B-4 | Time Code Formats | Issue 4, November 2010 | CUC time code, P-field |
| CCSDS 355.0-B-2 | Space Data Link Security Protocol | Issue 2 | authentication-only service (HMAC) |

Issue dates were taken from the CCSDS documents' own document-control pages and ECSS adoption
notices. Public sources also mention a draft/later issue of 131.0-B (B-6); check
https://public.ccsds.org for the current status before relying on an issue number.

## Hardware documentation

* EBYTE, *E22-xxxT37S User Manual*, v1.5 (2025) - E22-400T37S specifications, registers, modes,
  AUX, abnormal-status log, factory defaults.
* EBYTE, *E22/E32-xxxTBH-02 User Manual*, v1.0 (2024-03-06) - test-board header, jumpers, power.
* Arduino, *Nicla Sense ME datasheet* (ABX00050) - power (VIN 3.5-5.5 V), VDDIO_EXT, J2 header,
  ESLOV, BHI260AP / BMP390 / BMM150 / BME688.
* Arduino, *Nicla Sense Env datasheet* (ABX00089) - power (IN 2.3-6.5 V, ESLOV 5 V), 3.3 V
  logic with 5 V-tolerant pins, HS4001 / ZMOD4410 / ZMOD4510.
* Arduino, *UNO Q* (ABX00162/ABX00173): https://docs.arduino.cc/hardware/uno-q/ - datasheet, user
  manual (`Wire` = header SDA/SCL, `Wire1` = Qwiic I2C4 3.3 V only, `Serial1` = D0/D1) and power
  specification (USB-C 5 V / 3 A, VIN 7-24 V on JANALOG pin 8, header I/O 3.3 V, abs. max 3.6 V;
  D3/PB0 and A0/A1 not 5 V tolerant).
* Arduino, *VENTUNO Q* (ABX00181): https://docs.arduino.cc/hardware/ventuno-q/ - datasheet, user
  manual (`Wire` = PH11/PH12 header, `Wire1` = Qwiic PA8/PC9), power specification (barrel jack /
  screw terminal 7-24 V, USB-C PD 9-20 V only, >= 60 W recommended for heavy loads, measured
  13.3 W peak for a simple app at 12 V), NPU guide (Ubuntu 24.04, Python venv). Page sources:
  github.com/arduino/docs-content.
* Board support: github.com/arduino/ArduinoCore-zephyr (`variants/arduino_uno_q_stm32u585xx`,
  `variants/arduino_ventuno_q_stm32h5f5xx`: `serials`/`i2cs` order, hence `Serial3` = USART3 on
  PB10/PB11 and `Wire1` per board; `libraries/Wire`, `libraries/Arduino_LED_Matrix`) and the
  board device trees in github.com/arduino/zephyr (`boards/arduino/uno_q`, `boards/arduino/ventuno_q`).
* The user's *UNO Q LINPACK* and *VENTUNO Q LINPACK* App Lab packages (architecture facts: CPU,
  FP32-only MCU FPUs, heap behaviour, App Lab layout, Router Bridge thread rules, OS versions).
* PNI Sensor, *RM3100 Geomagnetic Sensor* datasheet and user manual.
* ams OSRAM *AS7265x* datasheet; SparkFun *Spectral Triad AS7265X* library 1.0.5.
* M5Stack, *Unit CardKB v1.1* documentation (I2C address 0x5F).
* Semtech, *SX1261/2* and *SX1268* datasheets (LoRa sensitivity of the radio chip family).

## Software libraries

* Arduino_RouterBridge 0.4.3 / Arduino_RPClite (MCU side); arduino-router-bridge 0.5.1 (PyPI,
  Linux side).
* Arduino_BHY2 1.0.8 (Nicla Sense ME), Arduino_NiclaSenseEnv 1.0.1.
* RadioLib (SX126x SPI upgrade path, not used with the UART module).
* reedsolo 1.7.0 (independent RS reference in the tests), pyserial.

## Algorithms and physics

* H. D. Black, "A passive system for determining the attitude of a satellite", AIAA Journal 2(7),
  1964 - TRIAD.
* B. P. Welford, "Note on a method for calculating corrected sums of squares and products",
  Technometrics 4(3), 1962 - single-pass variance.
* S. W. Shepperd, "Quaternion from rotation matrix", J. Guidance and Control 1(3), 1978.
* E. R. Berlekamp, *Algebraic Coding Theory*, 1968; J. L. Massey, "Shift-register synthesis and
  BCH decoding", IEEE Trans. IT-15, 1969; G. D. Forney, "On decoding BCH codes", IEEE Trans.
  IT-11, 1965.
* NIST FIPS 180-4 (SHA-256); IETF RFC 2104 (HMAC), RFC 4231 (HMAC-SHA-256 test vectors).
* ITU-R P.525 (free-space attenuation), ITU-R P.526 (diffraction, Fresnel zones); 4/3 effective
  Earth radius for the radio horizon.
* C. E. Shannon, "A Mathematical Theory of Communication", 1948 (Eb/N0 >= -1.59 dB).
* NOAA NCEI / BGS World Magnetic Model calculators (site inclination, declination, total field).
