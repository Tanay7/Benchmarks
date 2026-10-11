"""DSS-Q — a CCSDS ground data system for the VGQ-1 long-range telemetry mission (v3).

Package layout
--------------
ccsds/       Protocol layers: CRC, RS(255,223) dual-basis codec, randomizer, TM and USLP
             frames, Space Packets, TC/CLTU/COP-1, SDLS (AUTH/AE, anti-replay), CUC time
             codes, CFDP class 1, Rice (CCSDS 121.0) decoder
radio/       Ground radio backends: EBYTE E22 serial driver and configuration tool,
             SX126x through the station MCU (Router Bridge)
dictionary   Telemetry/command dictionary (single source for decom, XTCE and the docs)
framesync    Frame synchronizer (ASM search / check / lock / flywheel)
decom        Decommutation, EU conversion, limit checking (and the inverse, encode)
cmdparse     Command text -> user data, with the flight's acceptance rules
link         Radio models, air rates, airtime / busy time, sensitivities, AFC loop
rxstats      Receiver statistics (RSSI/SNR windows, FER, link budget, events)
linkmgr      Adaptive air rate / power advisor
spectral     AS7265X spectra, SPECBURST, classifier and reference library
magcal       Magnetometer hard/soft-iron calibration fit (MAGCAL)
products     Ground products: CCSDS TDM, XTCE export
replay       Offline replay / decode of recorded CADUs (TM or USLP, with or without SDLS)
archive      Raw CADU + SQLite archiving
fop          FOP-1 command sender (COP-1, SDLS TC, frame-size check)
gds          Ground data system process (GroundStation state, Runner, main)
display/     Web dashboard, front panel / Router Bridge link (station I/O MCU)
sim          Software spacecraft + RF channel + ground radio simulator (no hardware)
cltu         Hardline CLTU generator (EGSE uplink without RF)
"""

__version__ = "3.0.0"
