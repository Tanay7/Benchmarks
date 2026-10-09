"""EBYTE E22 serial interface for the ground station (USB on the E22-400TBH-02 board).

The test board's USB-UART bridge (CH340X) carries TXD/RXD only; M0/M1 are set
with the board's jumper caps (EBYTE E22-xxxTBH-02 manual: cap fitted = pin to GND):
  * operation    : both caps fitted (mode 0, NORMAL), GDS reads/writes the stream
  * configuration: remove the M1 cap only (mode 2, CONFIG), then run (in ground/)
        python -m dssq.radio.e22 --port /dev/ttyUSB0 --write --config config/station.toml
    and refit the M1 cap. (Configuration mode is always 9600 8N1.)
With the Radio Control Unit wired (both caps removed, VENTUNO Q MCU driving
M0/M1) the GDS does all of this itself - see the RADIO / GSCAN directives.

Register layout: see spacecraft/vgq1_flight/sketch/src/drivers/e22.h (identical).
"""
from __future__ import annotations

import argparse
import sys
import time

try:
    import serial  # pyserial
except ImportError:  # the simulator and tests do not need pyserial
    serial = None

# E22-400T37S (manual v1.5 7.2): codes 0, 1, 2 are all 2.4 kbit/s
AIR_RATES = {0: "2.4k", 1: "2.4k", 2: "2.4k", 3: "4.8k", 4: "9.6k", 5: "19.2k", 6: "38.4k", 7: "62.5k"}
UART_RATES = {0: 1200, 1: 2400, 2: 4800, 3: 9600, 4: 19200, 5: 38400, 6: 57600, 7: 115200}
SUBPKT = {0: 240, 1: 128, 2: 64, 3: 32}


def regs_from_cfg(c: dict) -> bytes:
    addr = int(c.get("address", 0))
    reg0 = (c.get("uart_code", 3) << 5) | (c.get("parity", 0) << 3) | c.get("air_rate", 2)
    reg1 = (c.get("subpacket", 0) << 6) | (int(c.get("rssi_noise", True)) << 5) | \
           (int(c.get("fault_log", True)) << 2) | c.get("power", 0)
    reg3 = (int(c.get("rssi_byte", True)) << 7) | (int(c.get("fixed", False)) << 6) | \
           (int(c.get("relay", False)) << 5) | (int(c.get("lbt", False)) << 4) | \
           (int(c.get("wor_role", False)) << 3) | (c.get("wor_cycle", 0) & 7)
    return bytes([addr >> 8, addr & 0xFF, c.get("netid", 0), reg0, reg1, c.get("channel", 23),
                  reg3, 0, 0])


def describe(regs: bytes) -> dict:
    return {
        "address": (regs[0] << 8) | regs[1], "netid": regs[2],
        "uart_bps": UART_RATES[(regs[3] >> 5) & 7], "parity_code": (regs[3] >> 3) & 3,
        "air_rate": AIR_RATES[regs[3] & 7], "air_rate_code": regs[3] & 7,
        "subpacket_bytes": SUBPKT[(regs[4] >> 6) & 3], "rssi_noise_enable": bool(regs[4] & 0x20),
        "fault_log": bool(regs[4] & 0x04),
        "power_code": regs[4] & 3, "channel": regs[5], "freq_mhz": 410.125 + regs[5],
        "rssi_byte": bool(regs[6] & 0x80), "fixed_tx": bool(regs[6] & 0x40),
        "relay": bool(regs[6] & 0x20), "lbt": bool(regs[6] & 0x10),
        "wor_role": bool(regs[6] & 0x08), "wor_cycle": regs[6] & 7,
    }


class E22Serial:
    """Thin pyserial wrapper used by the GDS in NORMAL mode."""

    def __init__(self, port: str, baud: int = 9600):
        if serial is None:
            raise RuntimeError("pyserial is required: pip install pyserial")
        self.ser = serial.Serial(port, baud, timeout=0.05)

    def read(self) -> bytes:
        n = self.ser.in_waiting
        return self.ser.read(n or 1)

    def write(self, data: bytes):
        self.ser.write(data)
        self.ser.flush()

    def request_noise(self):
        """Ambient-noise RSSI query (requires REG1 bit 5). Reply: C1 00 02 <noise> <last>."""
        self.write(bytes([0xC0, 0xC1, 0xC2, 0xC3, 0x00, 0x02]))

    def close(self):
        self.ser.close()


def _cmd(ser, data: bytes, reply_len: int, timeout=1.5) -> bytes:
    ser.reset_input_buffer()
    ser.write(data)
    ser.flush()
    t0, buf = time.time(), b""
    while len(buf) < reply_len and time.time() - t0 < timeout:
        buf += ser.read(reply_len - len(buf))
    return buf


def main(argv=None):
    ap = argparse.ArgumentParser(description="E22 configuration tool (board jumpers in CONFIG mode)")
    ap.add_argument("--port", required=True)
    ap.add_argument("--config", help="station.toml with a [radio] table")
    ap.add_argument("--write", action="store_true", help="write (save) the configuration")
    ap.add_argument("--volatile", action="store_true", help="use C2 (not saved) instead of C0")
    a = ap.parse_args(argv)
    if serial is None:
        sys.exit("pyserial is required: pip install pyserial")
    ser = serial.Serial(a.port, 9600, timeout=0.2)
    pid = _cmd(ser, bytes([0xC1, 0x80, 0x07]), 10)
    if len(pid) == 10 and pid[0] == 0xC1:
        print("product info:", pid[3:].hex(" "))
    else:
        sys.exit("no reply: are the jumpers in CONFIG mode (M0=0, M1=1) and the board powered?")
    if a.write:
        from ..config import load_config
        cfg = load_config(a.config)["radio"]
        regs = regs_from_cfg(cfg)
        r = _cmd(ser, bytes([0xC2 if a.volatile else 0xC0, 0x00, 0x09]) + regs, 12)
        if len(r) != 12 or r[0] != 0xC1:
            sys.exit(f"write failed, reply={r.hex()}")
        print("written.")
    r = _cmd(ser, bytes([0xC1, 0x00, 0x09]), 12)
    if len(r) != 12 or r[0] != 0xC1:
        sys.exit(f"read failed, reply={r.hex()}")
    for k, v in describe(r[3:]).items():
        print(f"  {k:18s} {v}")
    ser.close()


if __name__ == "__main__":
    main()
