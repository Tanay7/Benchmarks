"""EBYTE E22 UART radios at the ground station: E22-400T33D (default) and E22-400T37S.

Register protocol (identical on both modules, T33D manual v1.05 ch. 7, T37S manual
v1.5 ch. 7): configuration mode M1 = 1 / M0 = 0, always 9600 8N1;
  C0 <addr> <len> <data>  write (saved)     C2 <addr> <len> <data>  write (volatile)
  C1 <addr> <len>         read              reply C1 <addr> <len> <data>
  C0 C1 C2 C3 00 02       ambient-noise query in NORMAL mode (needs REG1 b5),
                          reply C1 00 02 <noise> <last packet RSSI>, dBm = -(256 - x)
Registers 00..06: ADDH ADDL NETID REG0 REG1 REG2(channel) REG3; 07/08 CRYPT (write only).

Model differences handled here (contract 2.1):
  * REG1 b2 "abnormal-status log" exists on the T37S only (manual v1.5 5.7: FF FF FF
    01..04 reports). On the T33D REG1 b4..b2 are RESERVED (manual v1.05 7.2) and are
    always written 0, whatever station.toml says.
  * Power code -> dBm: T33D 33 / 30 / 27 / 24 dBm (VERIFY, docs/08 T-RF-07),
    T37S 37 dBm for every code.

Ground wiring: the module's UART reaches Linux through a 3.3 V-logic USB-UART adapter
(T33D) or the E22-400TBH-02 board's CH340X (T37S). With the Radio Control Unit (VENTUNO Q
MCU on M0/M1/AUX) the GDS switches modes itself (radioctl.py). Without it, set the
mode pins by hand (M1 = 1, M0 = 0) and run, in ground/:
    python -m dssq.radio.e22 --port /dev/ttyUSB0 --write --config config/station.toml
Register layout mirror: spacecraft/vgq1_flight/sketch/src/drivers/e22.h.
"""
from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Mapping

from ..link import E22_RATE_LABELS, E22_T33D, RadioBackend, RadioModel, channel_freq_mhz, get_model, power_dbm

try:
    import serial  # pyserial
except ImportError:  # the simulator and tests do not need pyserial
    serial = None

AIR_RATES = dict(E22_RATE_LABELS)
UART_RATES = {0: 1200, 1: 2400, 2: 4800, 3: 9600, 4: 19200, 5: 38400, 6: 57600, 7: 115200}
SUBPKT = {0: 240, 1: 128, 2: 64, 3: 32}
NOISE_QUERY = bytes([0xC0, 0xC1, 0xC2, 0xC3, 0x00, 0x02])
REG1_FAULT_LOG = 0x04                  # T37S only
REG1_RESERVED_T33D = 0x1C              # b4..b2 reserved on the T33D
CONFIG_BAUD = 9600                     # configuration mode is always 9600 8N1


def _e22_model(model: str | RadioModel | None) -> RadioModel:
    m = get_model(model)
    if m.packet_mode:
        raise ValueError(f"{m.name} is not an E22 UART module (no E22 register set)")
    return m


def _field(c: Mapping[str, object], key: str, default: int, hi: int) -> int:
    v = c.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= hi:
        raise ValueError(f"[radio] {key} = {v!r} outside 0..{hi}")
    return v


def regs_from_cfg(c: Mapping[str, object], model: str | RadioModel | None = None) -> bytes:
    """Registers 00..08 from a station.toml [radio] table. model defaults to c["model"]."""
    m = _e22_model(model if model is not None else c.get("model"))       # type: ignore[arg-type]
    addr = _field(c, "address", 0, 0xFFFF)
    reg0 = (_field(c, "uart_code", 3, 7) << 5) | (_field(c, "parity", 0, 3) << 3) | _field(c, "air_rate", 2, 7)
    fault_log = bool(c.get("fault_log", True)) and m.has_fault_log
    reg1 = (_field(c, "subpacket", 0, 3) << 6) | (int(bool(c.get("rssi_noise", True))) << 5) | \
           (REG1_FAULT_LOG if fault_log else 0) | _field(c, "power", 0, 3)
    reg3 = (int(bool(c.get("rssi_byte", True))) << 7) | (int(bool(c.get("fixed", False))) << 6) | \
           (int(bool(c.get("relay", False))) << 5) | (int(bool(c.get("lbt", False))) << 4) | \
           (int(bool(c.get("wor_role", False))) << 3) | _field(c, "wor_cycle", 0, 7)
    return bytes([addr >> 8, addr & 0xFF, _field(c, "netid", 0, 0xFF), reg0, reg1,
                  _field(c, "channel", 23, 83), reg3, 0, 0])


def sanitize_reg1(reg1: int, model: str | RadioModel | None = None) -> int:
    """REG1 as it may be written to the given model (T33D: reserved bits cleared)."""
    m = _e22_model(model)
    return reg1 & 0xFF & ~(0 if m.has_fault_log else REG1_RESERVED_T33D)


def describe(regs: bytes, model: str | RadioModel | None = None) -> dict:
    """Human-readable view of registers 00..06 (as read back with C1 00 09)."""
    if len(regs) < 7:
        raise ValueError(f"need registers 00..06, got {len(regs)} octets")
    m = _e22_model(model)
    pwr = regs[4] & 3
    return {
        "model": m.name,
        "address": (regs[0] << 8) | regs[1], "netid": regs[2],
        "uart_bps": UART_RATES[(regs[3] >> 5) & 7], "parity_code": (regs[3] >> 3) & 3,
        "air_rate": AIR_RATES[regs[3] & 7], "air_rate_code": regs[3] & 7,
        "subpacket_bytes": SUBPKT[(regs[4] >> 6) & 3], "rssi_noise_enable": bool(regs[4] & 0x20),
        "fault_log": bool(regs[4] & REG1_FAULT_LOG) if m.has_fault_log else False,
        "fault_log_supported": m.has_fault_log,
        "reserved_bits_set": (not m.has_fault_log) and bool(regs[4] & REG1_RESERVED_T33D),
        "power_code": pwr, "power_dbm": power_dbm(m, pwr), "power_verified": m.name != E22_T33D,
        "channel": regs[5], "freq_mhz": channel_freq_mhz(regs[5]) if regs[5] <= 83 else None,
        "rssi_byte": bool(regs[6] & 0x80), "fixed_tx": bool(regs[6] & 0x40),
        "relay": bool(regs[6] & 0x20), "lbt": bool(regs[6] & 0x10),
        "wor_role": bool(regs[6] & 0x08), "wor_cycle": regs[6] & 7,
    }


class E22Serial(RadioBackend):
    """pyserial byte-stream backend used by the GDS in NORMAL mode."""
    packet_mode = False

    def __init__(self, port: str, baud: int = 9600):
        if serial is None:
            raise RuntimeError("pyserial is required: pip install pyserial")
        self.ser = serial.Serial(port, baud, timeout=0.05)

    def read(self) -> bytes:
        n = self.ser.in_waiting
        return self.ser.read(n or 1)

    def write(self, data: bytes) -> None:
        self.ser.write(data)
        self.ser.flush()

    def close(self) -> None:
        self.ser.close()


def _cmd(ser, data: bytes, reply_len: int, timeout: float = 1.5) -> bytes:
    """One configuration-mode exchange; returns what arrived within the timeout."""
    ser.reset_input_buffer()
    ser.write(data)
    ser.flush()
    t0, buf = time.time(), b""
    while len(buf) < reply_len and time.time() - t0 < timeout:
        buf += ser.read(reply_len - len(buf))
    return buf


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="E22 configuration tool (module in CONFIG mode: M1 = 1, M0 = 0)")
    ap.add_argument("--port", required=True)
    ap.add_argument("--config", help="station.toml with a [radio] table")
    ap.add_argument("--model", help="E22-400T33D (default) or E22-400T37S; default: [radio] model")
    ap.add_argument("--write", action="store_true", help="write (save) the configuration")
    ap.add_argument("--volatile", action="store_true", help="use C2 (not saved) instead of C0")
    a = ap.parse_args(argv)
    if serial is None:
        sys.exit("pyserial is required: pip install pyserial")
    cfg: dict = {}
    if a.config or a.write:
        from ..config import load_config
        cfg = load_config(a.config)["radio"]
    try:
        model = _e22_model(a.model if a.model else cfg.get("model"))
    except ValueError as e:
        sys.exit(str(e))
    print(f"model: {model.name}")
    ser = serial.Serial(a.port, CONFIG_BAUD, timeout=0.2)
    try:
        pid = _cmd(ser, bytes([0xC1, 0x80, 0x07]), 10)
        if len(pid) == 10 and pid[0] == 0xC1:
            print("product info:", pid[3:].hex(" "))
        else:
            sys.exit("no reply: is the module in CONFIG mode (M0 = 0, M1 = 1) and powered?")
        if a.write:
            if not model.has_fault_log and cfg.get("fault_log"):
                print(f"note: {model.name} has no abnormal-status log; REG1 b2 written 0")
            regs = regs_from_cfg(cfg, model)
            r = _cmd(ser, bytes([0xC2 if a.volatile else 0xC0, 0x00, 0x09]) + regs, 12)
            if len(r) != 12 or r[0] != 0xC1:
                sys.exit(f"write failed, reply={r.hex()}")
            print("written.")
        r = _cmd(ser, bytes([0xC1, 0x00, 0x09]), 12)
        if len(r) != 12 or r[0] != 0xC1:
            sys.exit(f"read failed, reply={r.hex()}")
        for k, v in describe(r[3:], model).items():
            print(f"  {k:20s} {v}")
    finally:
        ser.close()


if __name__ == "__main__":
    main()
