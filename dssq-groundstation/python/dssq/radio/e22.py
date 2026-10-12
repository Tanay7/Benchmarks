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
mode pins by hand (M1 = 1, M0 = 0) and run, in the App's python/ folder:
    python -m dssq.radio.e22 --port /dev/ttyUSB0 --write --config config/station.toml
Register layout mirror: vgq1-spacecraft/sketch/src/drivers/e22.h.

Serial transport (SerialPort): standard library only (termios / fcntl / select; no
pyserial, contract section 23 rule 2), POSIX ttys such as /dev/ttyUSB0 or /dev/ttyACM0.
The App Lab container bind-mounts /dev, runs in group dialout and allows ttyUSB / ttyACM
(contract section 23), so the same code serves the App and the systemd service.
"""
from __future__ import annotations

import argparse
import errno
import logging
import os
import select
import struct
import sys
import time
from collections.abc import Mapping
from typing import Self

from ..link import E22_RATE_LABELS, E22_T33D, RadioBackend, RadioModel, channel_freq_mhz, get_model, power_dbm

# POSIX only. Importing this module must still work elsewhere: the simulator and the
# tests use its register helpers without a serial port.
try:
    import fcntl
    import termios
except ImportError:  # pragma: no cover - non-POSIX development hosts
    fcntl = termios = None  # type: ignore[assignment]

log = logging.getLogger("dssq.radio")

AIR_RATES = dict(E22_RATE_LABELS)
UART_RATES = {0: 1200, 1: 2400, 2: 4800, 3: 9600, 4: 19200, 5: 38400, 6: 57600, 7: 115200}
SUBPKT = {0: 240, 1: 128, 2: 64, 3: 32}
NOISE_QUERY = bytes([0xC0, 0xC1, 0xC2, 0xC3, 0x00, 0x02])
REG1_FAULT_LOG = 0x04                  # T37S only
REG1_RESERVED_T33D = 0x1C              # b4..b2 reserved on the T33D
CONFIG_BAUD = 9600                     # configuration mode is always 9600 8N1
READ_TIMEOUT_S = 0.05                  # E22Serial.read(): longest wait for the first octet
REOPEN_HOLDOFF_S = 2.0                 # E22Serial: one reopen attempt per holdoff after a fault


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


# ------------------------------------------------------------------ serial transport
_ERRNO_HINTS = {
    errno.ENOENT: " (no such device: check the USB-UART, ls /dev/serial/by-id/)",
    errno.EACCES: " (permission denied: the user needs group 'dialout')",
    errno.EAGAIN: " (port locked by another process)",
    errno.EBUSY: " (port busy)",
    errno.EIO: " (I/O error: device unplugged?)",
    errno.ENOTTY: " (not a serial device)",
}


def _baud_constant(baud: int) -> int:
    """termios speed constant for one of the E22 UART rates (manual REG0 b7..b5)."""
    if termios is None:
        raise OSError(errno.ENOSYS, "serial ports need a POSIX system (termios)")
    if isinstance(baud, bool) or not isinstance(baud, int) or baud not in UART_RATES.values():
        raise ValueError(f"baud {baud!r} is not an E22 UART rate {sorted(UART_RATES.values())}")
    speed = getattr(termios, f"B{baud}", None)
    if speed is None:                  # pragma: no cover - every E22 rate exists on Linux
        raise ValueError(f"this system's termios has no B{baud}")
    return int(speed)


class SerialPort:
    """Raw 8N1 serial line on a POSIX tty, standard library only.

    Opened O_RDWR | O_NOCTTY (never becomes the controlling terminal) | O_NONBLOCK (every
    wait is a poll() with an explicit timeout, so no call can hang), locked with an
    exclusive flock() so a second GDS or a terminal program cannot interleave octets, then
    switched to raw mode: 8 data bits, no parity, 1 stop bit, receiver on, modem lines
    ignored, no hardware or software flow control (the E22 has neither), no input or
    output processing, VMIN = VTIME = 0. The settings are read back and verified.
    Every failure is an OSError whose message names the port and the operation."""

    def __init__(self, path: str, baud: int, timeout_s: float = READ_TIMEOUT_S) -> None:
        speed = _baud_constant(baud)
        if not 0.0 <= timeout_s <= 60.0:
            raise ValueError(f"timeout_s {timeout_s!r} outside 0..60 s")
        self.path, self.baud, self.timeout_s = str(path), baud, float(timeout_s)
        try:
            self._fd = os.open(self.path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except OSError as e:
            raise self._error(e, "open") from e
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if not os.isatty(self._fd):
                raise OSError(errno.ENOTTY, os.strerror(errno.ENOTTY))
            self._configure(speed)
        except (OSError, termios.error) as e:
            os.close(self._fd)
            self._fd = -1
            raise self._error(e, "configure") from e
        self._poll_in = select.poll()
        self._poll_in.register(self._fd, select.POLLIN)
        self._poll_out = select.poll()
        self._poll_out.register(self._fd, select.POLLOUT)

    def _configure(self, speed: int) -> None:
        iflag, oflag, cflag, lflag, _, _, cc = termios.tcgetattr(self._fd)
        iflag &= ~(termios.IGNBRK | termios.BRKINT | termios.PARMRK | termios.ISTRIP | termios.INLCR
                   | termios.IGNCR | termios.ICRNL | termios.IXON | termios.IXOFF | termios.IXANY
                   | termios.INPCK)
        oflag &= ~termios.OPOST
        lflag &= ~(termios.ECHO | termios.ECHONL | termios.ICANON | termios.ISIG | termios.IEXTEN)
        cflag &= ~(termios.CSIZE | termios.PARENB | termios.CSTOPB | getattr(termios, "CRTSCTS", 0))
        cflag |= termios.CS8 | termios.CREAD | termios.CLOCAL
        cc[termios.VMIN] = 0
        cc[termios.VTIME] = 0
        termios.tcsetattr(self._fd, termios.TCSANOW, [iflag, oflag, cflag, lflag, speed, speed, cc])
        # tcsetattr() succeeds if ANY change was applied (POSIX), so verify what matters.
        got = termios.tcgetattr(self._fd)
        if (got[2] & (termios.CSIZE | termios.PARENB | termios.CSTOPB)) != termios.CS8 \
                or got[3] & termios.ICANON or got[4] != speed or got[5] != speed:
            raise OSError(errno.EINVAL, "the tty did not accept raw 8N1 at the requested speed")
        termios.tcflush(self._fd, termios.TCIOFLUSH)                 # drop stale octets

    def _error(self, exc: BaseException, action: str) -> OSError:
        """OSError carrying the errno, the port and the operation (termios.error too)."""
        if isinstance(exc, OSError):
            code = exc.errno if exc.errno is not None else errno.EIO
            text = exc.strerror or str(exc)
        else:                                  # termios.error: args = (errno, message)
            args = exc.args
            code = args[0] if args and isinstance(args[0], int) else errno.EIO
            text = str(args[1]) if len(args) > 1 else str(exc)
        return OSError(code, f"{self.path}: {action} failed: {text}{_ERRNO_HINTS.get(code, '')}")

    def _live_fd(self) -> int:
        if self._fd < 0:
            raise OSError(errno.EBADF, f"{self.path}: port is closed")
        return self._fd

    @property
    def is_open(self) -> bool:
        return self._fd >= 0

    def fileno(self) -> int:
        return self._live_fd()

    def read(self, max_octets: int = 4096, timeout_s: float | None = None) -> bytes:
        """Waits up to `timeout_s` (default: the port's) for the first octet, then returns
        what has already arrived, at most `max_octets`. b"" on timeout."""
        if isinstance(max_octets, bool) or not isinstance(max_octets, int) or not 1 <= max_octets <= 65536:
            raise ValueError(f"max_octets {max_octets!r} outside 1..65536")
        fd = self._live_fd()
        wait = self.timeout_s if timeout_s is None else max(0.0, min(float(timeout_s), 60.0))
        events = self._poll_in.poll(int(wait * 1000.0 + 0.999))
        if not events:
            return b""
        if events[0][1] & (select.POLLERR | select.POLLNVAL):
            raise OSError(errno.EIO, f"{self.path}: read failed: device error{_ERRNO_HINTS[errno.EIO]}")
        try:
            data = os.read(fd, max_octets)
        except BlockingIOError:
            return b""
        except OSError as e:
            raise self._error(e, "read") from e
        if not data:                           # readable but end-of-file: the tty hung up
            raise OSError(errno.EIO, f"{self.path}: read failed: hang-up{_ERRNO_HINTS[errno.EIO]}")
        return data

    def write(self, data: bytes | bytearray | memoryview, timeout_s: float = 2.0) -> None:
        """Queues every octet of `data`, waiting (at most `timeout_s`) while the kernel
        buffer is full. Raises OSError(ETIMEDOUT) if the deadline passes."""
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError(f"data must be bytes-like, not {type(data).__name__}")
        if not 0.0 < timeout_s <= 60.0:
            raise ValueError(f"timeout_s {timeout_s!r} outside (0, 60] s")
        fd = self._live_fd()
        view = memoryview(bytes(data))
        deadline = time.monotonic() + timeout_s
        while view:                            # each pass writes >= 1 octet or nears the deadline
            left = deadline - time.monotonic()
            if left <= 0.0:
                raise OSError(errno.ETIMEDOUT, f"{self.path}: write timed out, {len(view)} octets unsent")
            if not self._poll_out.poll(int(left * 1000.0 + 0.999)):
                continue
            try:
                view = view[os.write(fd, view):]
            except BlockingIOError:
                continue
            except OSError as e:
                raise self._error(e, "write") from e

    def out_waiting(self) -> int:
        """Octets still queued in the kernel / USB driver for transmission (TIOCOUTQ)."""
        try:
            raw = fcntl.ioctl(self._live_fd(), termios.TIOCOUTQ, b"\0\0\0\0")
        except OSError as e:
            raise self._error(e, "TIOCOUTQ") from e
        return int(struct.unpack("i", raw)[0])

    def flush(self, timeout_s: float = 5.0) -> None:
        """Waits until the queued octets have left the driver (a tcdrain() with a bound:
        tcdrain itself can block forever on a stalled USB adapter). 5 s covers a full
        255-octet buffer at 1200 bit/s twice over."""
        deadline = time.monotonic() + timeout_s
        while (pending := self.out_waiting()) > 0:
            if time.monotonic() >= deadline:
                raise OSError(errno.ETIMEDOUT, f"{self.path}: flush timed out, {pending} octets queued")
            time.sleep(0.002)                  # about two octet times at 9600 bit/s

    def reset_input_buffer(self) -> None:
        """Discards every received octet not yet read (tcflush TCIFLUSH)."""
        try:
            termios.tcflush(self._live_fd(), termios.TCIFLUSH)
        except termios.error as e:
            raise self._error(e, "flush input") from e

    def close(self) -> None:
        """Releases the port and its lock. Idempotent."""
        fd, self._fd = self._fd, -1
        if fd >= 0:
            try:
                os.close(fd)                   # also releases the flock()
            except OSError as e:
                log.warning("%s: close: %s", self.path, e)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __del__(self) -> None:
        if getattr(self, "_fd", -1) >= 0:
            self.close()


class E22Serial(RadioBackend):
    """Byte-stream backend used by the GDS in NORMAL mode.

    Opening fails loudly (a wrong port must stop the station at start-up). After that it
    keeps the RadioBackend contract (link.py): read() and write() never raise on link
    errors. A fault (e.g. the USB-UART unplugged) is logged once, counted, the port is
    closed and reopened at most once per REOPEN_HOLDOFF_S; meanwhile read() sleeps its
    timeout and returns b"" (no busy loop) and write() drops the data (counted)."""
    packet_mode = False

    def __init__(self, port: str, baud: int = CONFIG_BAUD):
        self.port, self.baud = port, baud
        self.ser: SerialPort | None = SerialPort(port, baud, READ_TIMEOUT_S)
        self.faults = 0                        # link errors seen
        self.reopens = 0                       # successful reopens after a fault
        self.tx_dropped = 0                    # octets not sent while the port was down
        self.last_error = ""
        self._retry_t = 0.0

    @property
    def connected(self) -> bool:
        return self.ser is not None

    def _fault(self, e: OSError) -> None:
        self.faults += 1
        self.last_error = str(e)
        log.error("E22 serial link lost: %s (reopening every %.0f s)", e, REOPEN_HOLDOFF_S)
        if self.ser is not None:
            self.ser.close()
        self.ser = None
        self._retry_t = time.monotonic() + REOPEN_HOLDOFF_S

    def _port(self) -> SerialPort | None:
        if self.ser is None and time.monotonic() >= self._retry_t:
            try:
                self.ser = SerialPort(self.port, self.baud, READ_TIMEOUT_S)
            except OSError as e:
                self.last_error = str(e)
                self._retry_t = time.monotonic() + REOPEN_HOLDOFF_S
            else:
                self.reopens += 1
                log.warning("E22 serial link restored: %s", self.port)
        return self.ser

    def read(self) -> bytes:
        ser = self._port()
        if ser is None:
            time.sleep(READ_TIMEOUT_S)
            return b""
        try:
            return ser.read()
        except OSError as e:
            self._fault(e)
            return b""

    def write(self, data: bytes) -> None:
        ser = self._port()
        if ser is None:
            self.tx_dropped += len(data)
            return
        try:
            ser.write(data)
            ser.flush()
        except OSError as e:
            self.tx_dropped += len(data)
            self._fault(e)

    def close(self) -> None:
        if self.ser is not None:
            self.ser.close()
        self.ser = None


def _cmd(port: SerialPort, data: bytes, reply_len: int, timeout_s: float = 1.5) -> bytes:
    """One configuration-mode exchange; returns what arrived before the deadline."""
    port.reset_input_buffer()
    port.write(data)
    port.flush()
    deadline, buf = time.monotonic() + timeout_s, b""
    while len(buf) < reply_len:                # bounded by the deadline
        left = deadline - time.monotonic()
        if left <= 0.0:
            break
        buf += port.read(reply_len - len(buf), timeout_s=left)
    return buf


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="E22 configuration tool (module in CONFIG mode: M1 = 1, M0 = 0)")
    ap.add_argument("--port", required=True)
    ap.add_argument("--config", help="station.toml with a [radio] table")
    ap.add_argument("--model", help="E22-400T33D (default) or E22-400T37S; default: [radio] model")
    ap.add_argument("--write", action="store_true", help="write (save) the configuration")
    ap.add_argument("--volatile", action="store_true", help="use C2 (not saved) instead of C0")
    a = ap.parse_args(argv)
    cfg: dict = {}
    if a.config or a.write:
        from ..config import load_config
        cfg = load_config(a.config)["radio"]
    try:
        model = _e22_model(a.model if a.model else cfg.get("model"))
    except ValueError as e:
        sys.exit(str(e))
    print(f"model: {model.name}")
    try:
        ser = SerialPort(a.port, CONFIG_BAUD, timeout_s=0.2)
    except OSError as e:
        sys.exit(str(e))
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
