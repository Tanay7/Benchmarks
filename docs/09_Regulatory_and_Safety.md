# 09 - Regulatory and Safety

This is engineering guidance, not legal advice. Check your national regulator's rules for your
licence class before transmitting.

## 1 Licensing

* The E22-400T37S transmits **5 W (37 dBm)** in 410-493 MHz. 433 MHz licence-exempt (ISM/SRD)
  allowances are typically limited to milliwatts (for example 10 mW e.r.p. in Europe), so this
  project operates as an **amateur-radio station** in the 70 cm band: you need an amateur licence.
* Band: the default allowed channels 20-24 are 430.125-434.125 MHz, inside the 70 cm amateur
  allocation in ITU Regions 1 and 3 and away from the 435-438 MHz satellite segment. Region 2
  (420/430-450 MHz) operators can widen `allowed_channels`. Respect your national band plan.
* **Identification**: the spacecraft transmits `DE <callsign>` in clear text at boot and every
  10 minutes (EVR 0x0002). Put your callsign in `config.h` (`VGQ_CALLSIGN`).
* **No encryption**: amateur rules generally forbid messages encoded to obscure their meaning.
  The uplink uses SDLS *authentication only* (the command content stays readable); the E22's
  CRYPT registers are left at 0.
* Emission: LoRa with a few hundred kHz occupied bandwidth at most - check that your licence
  permits the emission and bandwidth on your chosen frequency.

## 2 RF safety

* Never transmit without an antenna or 50 Ohm load (EBYTE: no-load transmission may permanently
  damage the module).
* The receivers tolerate at most +10 dBm input: keep transmitting antennas apart, or use the
  attenuator chain for bench work (first attenuator rated for >= 5 W, better 10 W).
* RF exposure: keep people at least about 1 m away from a 5 W 433 MHz antenna while it transmits,
  further for directional antennas along their beam; check your regulator's exposure limits.

## 3 Electrical safety

* Lithium batteries: fuse the battery lead (3 A), use a charger and BMS made for the chemistry,
  never leave charging unattended.
* The E22 test board accepts 4.5-15 V; above 15 V it may be permanently damaged (EBYTE). Neither
  the Nicla Sense Env IN/OUT pins nor several other inputs have reverse-polarity protection.
* 3.3 V logic everywhere except the CardKB/Nicla power inputs: use level shifters for 5 V devices.

## 4 Operations safety built into the software

* Boot to SAFE; 50 % transmit-duty ceiling; transmitter cool-down on over-temperature.
* Hazardous commands need confirmation (and optionally a second operator).
* Command-loss and coordinated-change reverts make every link change self-healing.
* `TXINHIBIT` silences the spacecraft transmitter for a set time (e.g. while someone works near
  its antenna); it re-enables itself.
