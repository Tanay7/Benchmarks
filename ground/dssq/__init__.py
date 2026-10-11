"""DSS-Q — a CCSDS ground data system for the VGQ-1 long-range telemetry mission.

Package layout
--------------
ccsds/      Protocol layers (CRC, RS(255,223) dual-basis codec, randomizer,
            TM frames, Space Packets, TC/CLTU/COP-1, CUC time codes)
radio/      EBYTE E22 serial driver and configuration tool
framesync   Frame synchronizer (ASM search / check / lock / flywheel)
decom       Telemetry dictionary decommutation, EU conversion, limit checking
link        Link-quality metrics (RSSI, noise, SNR / C/N0 / Eb/N0 estimates, FER)
archive     Raw CADU + SQLite + JSONL archiving
fop         Simplified FOP-1 command sender
gds         Main ground-data-system process
display/    Web dashboard, console status, OLED front-panel feed
sim         Software spacecraft simulator (loop-back testing without radios)
"""

__version__ = "1.0.0"
