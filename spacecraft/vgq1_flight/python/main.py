# =============================================================================
#  VGQ-1 spacecraft EGSE (Electrical Ground Support Equipment) - Linux side of
#  the Arduino UNO Q (Qualcomm QRB2210), run by Arduino App Lab as the App's
#  python/main.py. It is the bench rack next to the flight computer, not part
#  of the flight software: the MCU flies without it (Phase B spec PB-6.3).
#
#  Duties (each one a bounded step of the App Lab user loop, PB-6.3):
#    * SCLK partition = SDLS TM epoch (CCSDS 355.0-B-2 AES-GCM nonce safety,
#      contract 5): a NEW persisted boot-counter value on every MCU boot, at EGSE
#      start and whenever the MCU reports epoch 0; never an old value again.
#    * SDLS TC anti-replay restore: the last sequence number the MCU reported
#      (sdls_sn) is persisted and given back after every MCU boot.
#    * CADU archive: every CADU handed to the radio, data/cadu_YYYYMMDD.bin
#      ([u64 unix_ns][u16 len][CADU], read by dssq.archive.read_cadu_file /
#      python -m dssq.replay), with a per-virtual-channel count (TM and USLP).
#    * Event log (data/evr_YYYYMMDD.jsonl) and status log
#      (data/sc_status_YYYYMMDD.jsonl) from the sc_evr / sc_status /
#      sc_status_x notifications.
#    * CFDP relay (CCSDS 727.0-B-5 class 1, contract 7): CFDPPUT source 1 (this
#      event log) or 2 (the latest CADU archive) is built here with cfdp_tx.py
#      and handed to the MCU PDU by PDU (cfdp_pdu RPC, VC 3).
#    * Hardline ("umbilical") commanding: CLTU hex lines appended to
#      data/hardline_queue.txt (python -m dssq.cltu on the ground side) go to the
#      flight command path through hl_cltu, bypassing RF.
#    * Status board on the App Lab console every 10 s.
#
#  Router Bridge rules (arduino.app_utils, PB-4 / PB-6.1): handlers provided to
#  the MCU run on the bridge's dispatcher thread and must never call
#  Bridge.call - they validate the untrusted arguments and enqueue; every call
#  to the MCU is made from App.run(user_loop=...). Any exception escaping the
#  user loop would stop the App (App.loop returns 1 -> sys.exit(1)), so each
#  duty runs inside one documented boundary (log + count + continue).
#
#  Standard library only (Python 3.11+; the App container runs 3.13). No
#  python/requirements.txt: App Lab would pip-install it from the internet
#  (contract 23/27, PB-7 F-13).
# =============================================================================
"""VGQ-1 spacecraft EGSE for the Arduino UNO Q Linux side (App Lab entry point)."""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple, Protocol

# App Lab runs `python python/main.py`, which already puts this folder on sys.path; a test or
# a developer may load the file from elsewhere, so the sibling module is made importable here.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import cfdp_tx  # noqa: E402  (the EGSE's own CFDP sender, stdlib only)

APP = _HERE.parent                         # the App folder (spacecraft/vgq1_flight)
log = logging.getLogger("vgq1.egse")

# ---- Files in <App>/data (contract 18: App Lab's persistent data folder) ------------------
BOOT_FILE = "boot_count.txt"               # last SCLK partition / SDLS TM epoch issued
SN_FILE = "sdls_last_sn.txt"               # last SDLS TC sequence number the MCU accepted
QUEUE_FILE = "hardline_queue.txt"          # hardline CLTU hex lines, one per command
LEGACY_DIR = Path("python") / "egse_data"  # v2 location, migrated once (PB-6.3)

# ---- Timing ------------------------------------------------------------------------------
LOOP_SLEEP_S = 0.2                         # per user-loop pass (PB-6.3: <= 0.2 s)
CALL_TIMEOUT_S = 5.0                       # every Bridge.call to the MCU
BOARD_PERIOD_S = 10.0                      # status board on the console
LOG_REPEAT_S = 60.0                        # a repeated warning/traceback at most once per minute
HARDLINE_PAUSE_S = 2.0                     # after an unconfirmed hl_cltu call
RELAY_STALL_S = 600.0                      # = flight kCfdpRelayTimeoutMs: the MCU forgets the relay
EPOCH0_GRACE_S = 10.0                      # a status queued before an accepted set_partition
REFUSAL_WAIT_S = 2.5                       # >= two 1 Hz sc_status after a refused epoch

# ---- Protocol limits (PB-4.1) ------------------------------------------------------------
EPOCH_MIN, EPOCH_MAX = 1, 65534            # HK sclk_partition u16; 0xFFFF is the sentinel
MAX_REFUSALS = 3                           # explicit set_partition refusals in a row
MAX_EPOCH0_REISSUES = 3                    # new epochs for "epoch 0" with no epoch ever seen
CADU_LEN = 236                             # ASM 4 + RS(255,223) codeblock of a 200-octet frame
ASM = bytes.fromhex("1ACFFC1D")
HL_MIN_HEX, HL_MAX_HEX = 2, 220            # hl_cltu: <= 110 octets (MCU 256-octet RPC buffer)
HL_QUEUE_MAX = 64                          # hardline lines held in memory
INBOX_MAX = 4096                           # notifications waiting for the loop (~7 min of traffic)
STATUS_MAX_CHARS = 1024                    # flight worst case 409 (sc_status_x)
EVR_MAX_CHARS = 200                        # flight: "0x%04X <SEV> " + <= 80 characters
INT32_MIN, INT32_MAX = -(1 << 31), (1 << 31) - 1

SEVERITIES = ("DIAG", "INFO", "WARN", "ERROR", "FATAL")
MODEL_NAMES = {1: "E22-400T33D", 2: "E22-400T37S", 3: "SX126x"}
FRAMINGS = {0: "TM", 1: "USLP"}
SDLS_SERVICES = {0: "NONE", 1: "AUTH", 2: "AE"}
TM_STATES = {0: "OFF", 1: "PROTECTED", 2: "WAITING FOR EPOCH", 3: "UNPROTECTED (null SA)"}
FARM_STATES = {1: "OPEN", 2: "WAIT", 3: "LOCKOUT"}
CFDP_STATES = {0: "IDLE", 1: "SENDING SSR", 2: "RELAYING EGSE FILE"}
SEQ_STATES = {0: "IDLE/EMPTY", 1: "ARMED", 2: "COMPLETE", 3: "STOPPED_BY_FDIR"}
BEACON_LEVELS = {0: "NOMINAL", 1: "INTERESTING", 2: "IMPORTANT", 3: "URGENT"}
NA_I16 = 32767                             # flight prints n/a int16 values as 32767

_HEX_RE = re.compile(r"[0-9A-Fa-f]*", re.ASCII)
_INT_FILE_RE = re.compile(r"-?[0-9]{1,11}", re.ASCII)
_EVR_RE = re.compile(r"0x([0-9A-Fa-f]{4}) (DIAG|INFO|WARN|ERROR|FATAL) (.*)", re.ASCII | re.DOTALL)
_KEY_RE = re.compile(r"[a-z][a-z0-9_]{0,23}", re.ASCII)

# Bridge.call failures that mean "no answer": retried (arduino.app_utils.Bridge.call documents
# ValueError / TimeoutError / ConnectionError / RuntimeError; socket errors are OSError).
# main() adds arduino.app_utils.AppError (router unreachable within 5 s, derives from Exception).
CALL_ERRORS: tuple[type[Exception], ...] = (TimeoutError, ConnectionError, RuntimeError, ValueError,
                                            OSError)


class BridgeApi(Protocol):
    """What the EGSE uses of arduino.app_utils.Bridge (tests pass a fake)."""

    def call(self, method_name: str, *params: object, timeout: float | None = ...) -> object: ...

    def provide(self, method_name: str, handler: Callable[..., object]) -> None: ...


class RelayRequest(NamedTuple):
    """A validated cfdp_req notification (PB-4.1)."""
    text: str
    source: int
    seq: int
    max_kib: int


class CounterFileError(Exception):
    """A counter file exists but cannot be read as an integer."""


# =============================================================================
# Crash-safe files
# =============================================================================
def _fsync_dir(directory: Path) -> None:
    """Makes a rename durable (POSIX). Some file systems refuse to open a directory:
    the rename itself is still atomic there, so that refusal is not an error."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass                                 # same reason: durability is best effort here
    finally:
        os.close(fd)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write `*.tmp`, flush, fsync, os.replace: a power loss leaves the old or the new
    content, never a truncated file (PB-6.3 counter files)."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def atomic_write_int(path: Path, value: int) -> None:
    atomic_write_bytes(path, f"{value}\n".encode("ascii"))


def read_int_file(path: Path) -> int | None:
    """None when the file does not exist; CounterFileError when it exists but does not
    hold one decimal integer (an empty file is NOT read as 0: that would silently reset
    a counter whose whole purpose is never to go back)."""
    try:
        text = path.read_bytes().decode("ascii")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as e:
        raise CounterFileError(f"{path}: {e}") from e
    t = text.strip()
    if not _INT_FILE_RE.fullmatch(t):
        raise CounterFileError(f"{path}: {t[:20]!r} is not a decimal integer")
    return int(t)


def prepare_data_dir(app_dir: Path, data_dir: Path | None = None) -> Path:
    """Creates <App>/data and migrates the v2 counter files from python/egse_data once:
    the boot counter must never go back, so an existing old counter is carried over
    before anything is issued (PB-6.3). The data/ copy, once it exists, always wins."""
    data = data_dir if data_dir is not None else app_dir / "data"
    data.mkdir(parents=True, exist_ok=True)
    legacy = app_dir / LEGACY_DIR
    for name in (BOOT_FILE, SN_FILE):
        old, new = legacy / name, data / name
        if old.is_file() and not new.exists():
            atomic_write_bytes(new, old.read_bytes())
            log.warning("migrated %s from the v2 location %s to %s", name, legacy, data)
    return data


# =============================================================================
# Pure helpers
# =============================================================================
def backoff_s(attempt: int) -> float:
    """Retry delay for an unanswered Bridge call: 0.5 s doubling to 8 s."""
    return 0.5 * (1 << min(max(attempt, 0), 4))


def _derandomize(data: bytes) -> bytes:
    """CCSDS pseudo-randomizer (131.0-B-5, h(x) = x^8 + x^7 + x^5 + x^3 + 1, all-ones
    seed) applied to the first octets of a codeblock - XOR, so it also removes it."""
    sr, out = 0xFF, bytearray()
    for b in data:
        o = 0
        for _ in range(8):
            o = (o << 1) | ((sr >> 7) & 1)
            fb = ((sr >> 7) ^ (sr >> 4) ^ (sr >> 2) ^ sr) & 1
            sr = ((sr << 1) | fb) & 0xFF
        out.append(b ^ o)
    return bytes(out)


def cadu_vc(cadu: bytes) -> str:
    """Virtual channel of a CADU for the archive statistics: "TM VCn" (version-1 TM
    frame, VCID bits of octet 1) or "USLP VCn" (TFVN 0b1100, VCID = 6 bits across
    octets 2-3 of the TFPH, contract 6); "?" for anything else."""
    if len(cadu) < len(ASM) + 4 or cadu[:4] != ASM:
        return "?"
    h = _derandomize(cadu[4:8])
    if h[0] >> 4 == 0xC:                                      # USLP TFVN '1100'
        return f"USLP VC{((h[2] & 0x07) << 3) | (h[3] >> 5)}"
    if h[0] >> 6 == 0:                                        # TM version '00'
        return f"TM VC{(h[1] >> 1) & 0x07}"
    return "?"


def _printable(text: str, limit: int) -> str:
    """Untrusted text for a log line: printable ASCII only, bounded."""
    return "".join(c if " " <= c <= "~" else "?" for c in text[:limit])


def parse_evr(text: str) -> dict[str, object]:
    """sc_evr "0x%04X <SEVERITY> <text>" (PB-4.1) -> {id, severity, text}; a line that
    does not follow the format is kept whole with id/severity None."""
    m = _EVR_RE.fullmatch(text)
    if m is None:
        return {"id": None, "severity": None, "text": _printable(text, EVR_MAX_CHARS)}
    return {"id": f"0x{m.group(1).upper()}", "severity": m.group(2),
            "text": _printable(m.group(3), EVR_MAX_CHARS)}


def _reject_constant(name: str) -> object:
    raise ValueError(f"non-finite number {name} in a status")


def parse_status(text: str) -> dict[str, object]:
    """sc_status / sc_status_x JSON (PB-4.1) -> a flat dict of short keys and scalar
    values. Raises ValueError for anything else (untrusted input, never trusted to be
    the flight's own formatter)."""
    if len(text) > STATUS_MAX_CHARS:
        raise ValueError(f"status of {len(text)} characters")
    obj = json.loads(text, parse_constant=_reject_constant)
    if not isinstance(obj, dict) or len(obj) > 64:
        raise ValueError("status is not a JSON object of <= 64 keys")
    for k, v in obj.items():
        if not _KEY_RE.fullmatch(k):
            raise ValueError(f"status key {k[:30]!r}")
        if isinstance(v, str):
            if len(v) > 32:
                raise ValueError(f"status value of {k} too long")
        elif not isinstance(v, (bool, int, float)):
            raise ValueError(f"status value of {k} is not a scalar")
    return obj


def _int(d: Mapping[str, object], key: str) -> int | None:
    v = d.get(key)
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _num(d: Mapping[str, object], key: str) -> float | None:
    v = d.get(key)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _txt(d: Mapping[str, object], key: str, names: Mapping[int, str] | None = None) -> str:
    """Display text of one status value ("-" when absent, the name for an enum)."""
    v = d.get(key)
    if v is None or isinstance(v, bool):
        return "-" if v is None else str(int(v))
    if names is not None and isinstance(v, int):
        return f"{names.get(v, '?')}({v})"
    return _printable(str(v), 32)


def _na(d: Mapping[str, object], key: str, scale: float = 1.0, unit: str = "", fmt: str = "{:.0f}") -> str:
    v = _int(d, key)
    if v is None or v == NA_I16:
        return "n/a"
    return fmt.format(v * scale) + unit


def render_board(core: Mapping[str, object], x: Mapping[str, object], egse: Mapping[str, object]) -> list[str]:
    """The console status board (PB-6.3): the latest sc_status (core, 1 Hz) and
    sc_status_x (extended, 5 s) merged, plus the EGSE's own counters."""
    ch = _int(core, "ch")
    mhz = f" = {410.125 + ch:.3f} MHz" if ch is not None else ""
    ssr = _num(x, "ssr_pm")
    snr = _int(x, "ul_snr_cdb")
    stretch = _int(x, "stretch")
    cfdp_pm = _num(x, "cfdp_pm")
    fdir, sns = _int(x, "fdir"), _int(x, "sns")
    lines = [
        "+--------------------------- VGQ-1 SPACECRAFT EGSE ----------------------------+",
        f"| MODE {_txt(core, 'mode')}  SCLK {_txt(core, 'part')}/{_txt(core, 'sclk')} s  "
        f"BEACON {_txt(core, 'beacon', BEACON_LEVELS)}  SEQ {_txt(x, 'seq', SEQ_STATES)}"
        f"  (status age core {egse.get('age_core', '-')} s, ext {egse.get('age_x', '-')} s)",
        f"| RADIO {_txt(core, 'model', MODEL_NAMES)} ch {_txt(core, 'ch')}{mhz}  rate code "
        f"{_txt(core, 'code')}  pwr code {_txt(core, 'pwr')}  LBT/CAD {_txt(core, 'lbt')}  "
        f"framing {_txt(core, 'fr', FRAMINGS)}",
        f"| TX {'ON' if core.get('tx') else 'off'}{'  ** INHIBITED **' if core.get('inhibit') else ''}"
        f"{'  ** HIBERNATING **' if core.get('hib') else ''}  frames {_txt(core, 'frames')} "
        f"(OID {_txt(core, 'oid')})  MCFC {_txt(core, 'mcfc')}  period {_txt(core, 'period_ms')} ms  "
        f"busy {_txt(core, 'busy_ms')} ms  duty {_txt(core, 'duty_pct')} %",
        f"| UPLINK RSSI {_na(x, 'ul_rssi', unit=' dBm')}  SNR "
        f"{'n/a' if snr is None or snr == NA_I16 else f'{snr / 100:.2f} dB'}  noise "
        f"{_na(x, 'noise', unit=' dBm')}  FARM {_txt(x, 'farm', FARM_STATES)} V(R) {_txt(x, 'vr')}  "
        f"CMD acc {_txt(x, 'acc')} rej {_txt(x, 'rej')}  rescued {_txt(x, 'tc_rescued')}",
        f"| FDIR {'-' if fdir is None else f'0x{fdir:04X}'}  sensors "
        f"{'-' if sns is None else f'0x{sns:04X}'}  bus {_txt(x, 'bus_c')} C  radio fault "
        f"{_txt(x, 'rf_fault')}  VC0 {_txt(x, 'vc0')} VC1 {_txt(x, 'vc1')} VC2 {_txt(x, 'vc2')} "
        f"VC3 {_txt(x, 'vc3')} B  SSR {'-' if ssr is None else f'{ssr / 10:.1f}'} %",
        f"| SDLS TC {_txt(x, 'tc_svc', SDLS_SERVICES)}  TM {_txt(x, 'tm_svc', SDLS_SERVICES)} "
        f"{_txt(core, 'tm_state', TM_STATES)} epoch {_txt(core, 'epoch')} sn {_txt(core, 'tm_sn')}  "
        f"cadence stretch {'-' if stretch is None else f'x{stretch / 100:.2f}'}",
        f"| CFDP {_txt(x, 'cfdp', CFDP_STATES)} seq {_txt(x, 'cfdp_seq')} "
        f"{'-' if cfdp_pm is None else f'{cfdp_pm / 10:.1f}'} %  relay {egse.get('relay', 'idle')}  "
        f"MCU refused {_txt(x, 'relay_ref')}",
        f"| EGSE epoch issued {egse.get('epoch', '-')}  archived {egse.get('cadus', 0)} CADUs "
        f"{egse.get('vc_counts', '{}')}  EVRs {egse.get('evrs', 0)}",
        f"| NOT FORWARDED by the MCU: EVRs {_txt(x, 'evr_nd')}  CADUs {_txt(x, 'cadu_nd')}  "
        f"packets refused {_txt(x, 'emit_ref')}  EGSE inbox dropped {egse.get('inbox_dropped', 0)}",
        "+------------------------------------------------------------------------------+",
    ]
    return lines


# =============================================================================
# The EGSE
# =============================================================================
class Egse:
    """All EGSE state. Handlers (`on_*`, bridge dispatcher thread) validate and enqueue;
    `loop_once()` (App Lab user loop) does everything else, including every Bridge.call,
    so the state below is touched by one thread only - except the inbox and the counters
    the handlers bump, which are guarded by `_lock`."""

    DUTIES = ("register", "inbox", "partition", "sdls_sn", "cfdp", "hardline", "board")

    def __init__(self, data_dir: Path, bridge: BridgeApi, *,
                 call_errors: tuple[type[Exception], ...] = CALL_ERRORS,
                 clock: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.data = data_dir
        self.bridge = bridge
        self.call_errors = call_errors
        self._clock, self._mono, self._sleep = clock, monotonic, sleep
        self._lock = threading.Lock()
        self._inbox: deque[tuple[str, object, float]] = deque()
        self._last_log: dict[str, float] = {}
        self.stats: dict[str, int] = {
            "cadus": 0, "evrs": 0, "status_core": 0, "status_x": 0, "boots": 0, "inbox_dropped": 0,
            "bad_notifications": 0, "write_errors": 0, "epochs_issued": 0, "epoch_refusals": 0,
            "relay_started": 0, "relay_completed": 0, "relay_cancelled": 0, "relay_failed": 0,
            "relay_abandoned": 0,
            "relay_pdus": 0, "relay_busy": 0, "hardline_sent": 0, "hardline_failed": 0,
        }
        self.vc_counts: dict[str, int] = {}
        self.duty_errors: dict[str, int] = dict.fromkeys(self.DUTIES, 0)
        self.handlers: dict[str, Callable[..., None]] = {
            "tm_cadu": self.on_tm_cadu, "sc_status": self.on_sc_status, "sc_status_x": self.on_sc_status_x,
            "sc_evr": self.on_sc_evr, "sc_boot": self.on_sc_boot, "sdls_sn": self.on_sdls_sn,
            "cfdp_req": self.on_cfdp_req, "cfdp_cancel": self.on_cfdp_cancel,
        }
        self.registered: set[str] = set()
        self._reg_attempt, self._reg_next = 0, 0.0
        # latest statuses (merged on the board) and the MCU-side counters watched for changes
        self.status_core: dict[str, object] = {}
        self.status_x: dict[str, object] = {}
        self._t_core: float | None = None
        self._t_x: float | None = None
        self._nd_seen: dict[str, int] = {}
        # SCLK partition / SDLS TM epoch (PB-6.3 rules 1-6)
        self.boot_count: int | None = 0        # None: the counter file is unreadable -> blocked
        self.epoch_pending: int | None = None   # value persisted and offered to the MCU
        self.epoch_accepted: int | None = None
        self._epoch_attempt, self._epoch_next = 0, 0.0
        self._epoch_ok_t: float | None = None
        self._refusals = 0
        self._reissue_at: float | None = None   # after a refusal: wait for a fresh status
        self._issue_retry_at: float | None = None   # the counter could not be persisted
        self.epoch_stopped = ""                 # why issuing stopped (operator action needed)
        self._latest_epoch: int | None = None   # "epoch" of the latest sc_status
        self._epoch0_reissues = 0
        # SDLS TC sequence-number restore
        self.sn_pending: int | None = None
        self._sn_attempt, self._sn_next = 0, 0.0
        # CFDP relay
        self.relay: cfdp_tx.RelayTransfer | None = None
        self.relay_last = "idle"
        self._relay_attempt, self._relay_next = 0, 0.0
        self._relay_progress_t = 0.0
        self._relay_unanswered = False          # the current PDU's last offer raised
        # hardline
        self.hl_queue: deque[str] = deque()
        self._hl_next = 0.0
        # board
        self._board_next = 0.0

    # ------------------------------------------------------------------ logging / files
    def _log_limited(self, key: str, level: int, msg: str, *args: object) -> None:
        """A message that could repeat every pass is logged at most once per minute."""
        now = self._mono()
        with self._lock:
            last = self._last_log.get(key)
            if last is not None and now - last < LOG_REPEAT_S:
                return
            self._last_log[key] = now
        log.log(level, msg, *args)

    def _utc(self, t: float | None = None) -> str:
        return datetime.fromtimestamp(self._clock() if t is None else t, UTC).isoformat(timespec="seconds")

    def _day_file(self, prefix: str, ext: str, t: float) -> Path:
        return self.data / f"{prefix}_{datetime.fromtimestamp(t, UTC):%Y%m%d}.{ext}"

    def _append(self, path: Path, data: bytes) -> bool:
        """One archive/log record. A full or failing disk is reported, never fatal."""
        try:
            with open(path, "ab") as f:
                f.write(data)
            return True
        except OSError as e:
            self.stats["write_errors"] += 1
            self._log_limited("write:" + path.name, logging.ERROR, "cannot write %s: %s", path, e)
            return False

    # ------------------------------------------------------------------ handlers (dispatcher thread)
    def _post(self, kind: str, payload: object) -> None:
        with self._lock:
            if len(self._inbox) >= INBOX_MAX:
                self.stats["inbox_dropped"] += 1
                dropped = True
            else:
                self._inbox.append((kind, payload, self._clock()))
                dropped = False
        if dropped:
            self._log_limited("inbox", logging.ERROR, "EGSE inbox full (%d): %s notification dropped",
                              INBOX_MAX, kind)

    def _bad(self, name: str, why: str) -> None:
        with self._lock:
            self.stats["bad_notifications"] += 1
        self._log_limited("bad:" + name, logging.WARNING, "malformed %s notification ignored: %s", name, why)

    def _one(self, name: str, params: tuple[object, ...], kind: type) -> object | None:
        """The single argument of a notification, of the expected type, else None."""
        if len(params) != 1:
            self._bad(name, f"{len(params)} arguments")
            return None
        v = params[0]
        if not isinstance(v, kind) or isinstance(v, bool):
            self._bad(name, f"argument of type {type(v).__name__}")
            return None
        return v

    def on_tm_cadu(self, *params: object) -> None:
        hx = self._one("tm_cadu", params, str)
        if not isinstance(hx, str):
            return
        if len(hx) != 2 * CADU_LEN or not _HEX_RE.fullmatch(hx):
            self._bad("tm_cadu", f"{len(hx)} characters, {2 * CADU_LEN} hex digits expected")
            return
        self._post("cadu", bytes.fromhex(hx))

    def _status(self, name: str, kind: str, params: tuple[object, ...]) -> None:
        text = self._one(name, params, str)
        if not isinstance(text, str):
            return
        try:
            st = parse_status(text)
        except ValueError as e:                     # json.JSONDecodeError is a ValueError
            self._bad(name, str(e))
            return
        self._post(kind, st)

    def on_sc_status(self, *params: object) -> None:
        self._status("sc_status", "core", params)

    def on_sc_status_x(self, *params: object) -> None:
        self._status("sc_status_x", "x", params)

    def on_sc_evr(self, *params: object) -> None:
        text = self._one("sc_evr", params, str)
        if not isinstance(text, str):
            return
        if len(text) > EVR_MAX_CHARS:
            self._bad("sc_evr", f"{len(text)} characters")
            return
        self._post("evr", parse_evr(text))

    def on_sc_boot(self, *params: object) -> None:
        cause = self._one("sc_boot", params, int)
        if not isinstance(cause, int):
            return
        if not 0 <= cause <= 255:                  # flight reset cause is a u8
            self._bad("sc_boot", f"reset cause {cause}")
            return
        self._post("boot", cause)

    def on_sdls_sn(self, *params: object) -> None:
        sn = self._one("sdls_sn", params, int)
        if not isinstance(sn, int):
            return
        if not INT32_MIN <= sn <= INT32_MAX:
            self._bad("sdls_sn", "outside int32")
            return
        self._post("sn", sn)

    def on_cfdp_req(self, *params: object) -> None:
        text = self._one("cfdp_req", params, str)
        if not isinstance(text, str):
            return
        if len(text) > 32:                         # flight: "<1|2>,<10 digits>,<5 digits>"
            self._bad("cfdp_req", f"{len(text)} characters")
            return
        try:
            source, seq, max_kib = cfdp_tx.parse_request(text)
        except ValueError as e:
            self._bad("cfdp_req", str(e))
            return
        self._post("cfdp_req", RelayRequest(text, source, seq, max_kib))

    def on_cfdp_cancel(self, *params: object) -> None:
        text = self._one("cfdp_cancel", params, str)
        if not isinstance(text, str):
            return
        if len(text) > 16:
            self._bad("cfdp_cancel", f"{len(text)} characters")
            return
        try:
            seq = cfdp_tx.parse_seq(text)
        except ValueError as e:
            self._bad("cfdp_cancel", str(e))
            return
        self._post("cfdp_cancel", seq)

    # ------------------------------------------------------------------ start / loop
    def start(self) -> None:
        """EGSE start (PB-6.3): load the counters, offer a NEW epoch (rule 2: the MCU may
        have booted while the EGSE was down; an old value is never re-sent, rule 4) and
        restore the SDLS TC sequence number."""
        try:
            n = read_int_file(self.data / BOOT_FILE)
        except CounterFileError as e:
            self.boot_count = None
            self.epoch_stopped = "boot counter unreadable"
            log.error("%s: SCLK partitions / SDLS TM epochs are NOT issued. Restore the highest value "
                      "ever used (never a lower one), or generate a new key (tools/gen_sdls_key.py) "
                      "and delete the file", e)
        else:
            if n is None:
                log.warning("first start or counter lost (%s missing): if this flight key was used "
                            "before, generate a new key (tools/gen_sdls_key.py)", BOOT_FILE)
                n = 0
            if n < 0:
                self.boot_count = None
                self.epoch_stopped = "boot counter negative"
                log.error("%s holds %d: SCLK partitions / SDLS TM epochs are NOT issued", BOOT_FILE, n)
            else:
                self.boot_count = n
                self._issue_epoch("EGSE start")
        self._load_sn("EGSE start")
        log.info("VGQ-1 EGSE started: data in %s, boot counter %s", self.data,
                 "unreadable" if self.boot_count is None else self.boot_count)

    def loop_once(self) -> None:
        """One App Lab user-loop pass: every duty inside its own boundary, then a short
        sleep. Never raises an Exception (an escaping one would stop the App, PB-6.1)."""
        for name in self.DUTIES:
            try:
                getattr(self, "_duty_" + name)(self._mono())
            except Exception:                     # documented boundary (PB-0.4, PB-6.3): log + count + continue
                self.duty_errors[name] += 1
                if self._should_log("duty:" + name):
                    log.exception("EGSE duty %s failed (%d so far); continuing", name, self.duty_errors[name])
        self._sleep(LOOP_SLEEP_S)

    def _should_log(self, key: str) -> bool:
        now = self._mono()
        with self._lock:
            last = self._last_log.get(key)
            if last is not None and now - last < LOG_REPEAT_S:
                return False
            self._last_log[key] = now
        return True

    # ------------------------------------------------------------------ duty: registration
    def _duty_register(self, now: float) -> None:
        """Bridge.provide every notification name (retried: under App Lab the first use of
        the bridge raises AppError when the router is not reachable within 5 s)."""
        if len(self.registered) == len(self.handlers) or now < self._reg_next:
            return
        for name, fn in self.handlers.items():
            if name in self.registered:
                continue
            try:
                self.bridge.provide(name, fn)
            except self.call_errors as e:
                self._reg_attempt += 1
                self._reg_next = self._mono() + backoff_s(self._reg_attempt)
                self._log_limited("register", logging.ERROR, "Bridge.provide(%s) failed: %s: %s "
                                  "(retrying; another process providing the same name?)",
                                  name, type(e).__name__, e)
                return
            self.registered.add(name)
        self._reg_attempt = 0
        log.info("notifications provided to the MCU: %s", ", ".join(sorted(self.registered)))

    # ------------------------------------------------------------------ duty: inbox
    def _duty_inbox(self, now: float) -> None:
        for _ in range(INBOX_MAX):
            with self._lock:
                if not self._inbox:
                    return
                kind, payload, t = self._inbox.popleft()
            getattr(self, "_do_" + kind)(payload, t, now)

    def _do_cadu(self, cadu: object, t: float, now: float) -> None:
        if not isinstance(cadu, bytes):   # posted only by the handlers above
            return
        rec = int(t * 1e9).to_bytes(8, "big") + len(cadu).to_bytes(2, "big") + cadu
        if self._append(self._day_file("cadu", "bin", t), rec):
            self.stats["cadus"] += 1
        vc = cadu_vc(cadu)
        self.vc_counts[vc] = self.vc_counts.get(vc, 0) + 1

    def _write_status(self, kind: str, st: dict[str, object], t: float) -> None:
        rec: dict[str, object] = {"utc": self._utc(t), "kind": kind}
        rec.update((k, v) for k, v in st.items() if k not in rec)
        self._append(self._day_file("sc_status", "jsonl", t),
                     (json.dumps(rec, separators=(",", ":")) + "\n").encode("ascii"))

    def _do_core(self, st: object, t: float, now: float) -> None:
        if not isinstance(st, dict):   # posted only by the handlers above
            return
        self.status_core, self._t_core = st, now
        self.stats["status_core"] += 1
        self._write_status("core", st, t)
        epoch = _int(st, "epoch")
        if epoch is None or not 0 <= epoch <= 0xFFFF:
            return
        self._latest_epoch = epoch
        if epoch > 0:
            self._epoch0_reissues = 0
            if self.boot_count is not None and epoch > self.boot_count and epoch <= EPOCH_MAX:
                # The MCU holds an epoch the counter never reached: the counter regressed
                # (restored backup, another board's file). The MCU's value is a lower bound of
                # the epochs already used with this key, so the counter is raised to it.
                self._persist_boot_count(epoch, f"the MCU reports epoch {epoch}")
            if self.epoch_pending is not None and self.epoch_pending < epoch:
                # The value waiting to be offered would only be refused (lower than the MCU's
                # epoch): skip it - skipping a value never endangers a nonce.
                self._issue_epoch(f"the pending value {self.epoch_pending} is below the MCU's epoch")
        elif self._epoch0_wanted(now):
            self._epoch0_reissues += 1
            self._issue_epoch("the MCU reports no epoch (it booted while the EGSE was not listening)")

    def _epoch0_wanted(self, now: float) -> bool:
        """Rule 2: epoch 0 with no request in flight -> a new value; not for a status that
        was queued before the last accepted set_partition, and not endlessly if the MCU
        accepts epochs but keeps reporting 0 (a fault the operator must see)."""
        if self.epoch_stopped or self.epoch_pending is not None or self._reissue_at is not None:
            return False
        if self._issue_retry_at is not None:
            return False
        if self._epoch_ok_t is not None and now - self._epoch_ok_t < EPOCH0_GRACE_S:
            return False
        if self._epoch0_reissues >= MAX_EPOCH0_REISSUES:
            self._log_limited("epoch0", logging.ERROR, "the MCU keeps reporting epoch 0 after %d accepted "
                              "epochs: not issuing more (check the flight set_partition service)",
                              MAX_EPOCH0_REISSUES)
            return False
        return True

    def _do_x(self, st: object, t: float, now: float) -> None:
        if not isinstance(st, dict):   # posted only by the handlers above
            return
        self.status_x, self._t_x = st, now
        self.stats["status_x"] += 1
        self._write_status("x", st, t)
        for key, what in (("evr_nd", "EVRs"), ("cadu_nd", "CADU copies")):
            n = _int(st, key)
            if n is None:
                continue
            seen = self._nd_seen.get(key)
            if n != seen and n > 0:
                delta = n - seen if seen is not None and n > seen else n
                log.warning("%d %s not forwarded by the MCU since its boot (+%d): the notification "
                            "budget or the router UART was full", n, what, delta)
            self._nd_seen[key] = n

    def _do_evr(self, evr: object, t: float, now: float) -> None:
        if not isinstance(evr, dict):   # posted only by the handlers above
            return
        rec = {"utc": self._utc(t), **evr}
        if self._append(self._day_file("evr", "jsonl", t),
                        (json.dumps(rec, separators=(",", ":")) + "\n").encode("ascii")):
            self.stats["evrs"] += 1
        sev = evr.get("severity")
        level = logging.WARNING if sev in ("WARN", "ERROR", "FATAL") else logging.INFO
        log.log(level, "EVR %s %s %s", evr.get("id") or "?", sev or "?", evr.get("text"))

    def _do_boot(self, cause: object, t: float, now: float) -> None:
        if not isinstance(cause, int):   # posted only by the handlers above
            return
        self.stats["boots"] += 1
        names = {0: "POWER-ON", 1: "PIN", 2: "SOFTWARE", 3: "WATCHDOG", 4: "BROWNOUT"}
        log.info("flight computer boot (reset cause %d %s)", cause, names.get(cause, "UNKNOWN"))
        self._epoch0_reissues = 0
        self._latest_epoch = 0                  # a booted MCU has no epoch until it is given one
        self._reissue_at = None
        if self.relay is not None:              # the MCU's RelayGate is RAM only (PB-2.5)
            self._relay_end("abandoned", logging.WARNING, "the MCU rebooted (its relay authorisation is gone)")
        self._issue_epoch("MCU boot")           # rule 1: always a NEW value, also over a pending one
        self._load_sn("MCU boot")

    def _do_sn(self, sn: object, t: float, now: float) -> None:
        if not isinstance(sn, int):   # posted only by the handlers above
            return
        try:
            atomic_write_int(self.data / SN_FILE, sn)   # stored as received (int32), never interpreted
        except OSError as e:
            self.stats["write_errors"] += 1
            self._log_limited("sn", logging.ERROR, "cannot persist the SDLS TC SN %d: %s", sn, e)
        if self.sn_pending is not None:
            self.sn_pending = sn                 # a restore still waiting gives the latest value

    def _do_cfdp_req(self, req: object, t: float, now: float) -> None:
        if not isinstance(req, RelayRequest):   # posted only by the handlers above
            return
        text, source, seq, max_kib = req
        if self.relay is not None:
            if self.relay.seq == seq:
                log.info("cfdp_req seq %#010x repeated: transfer already running", seq)
                return
            # The MCU sends cfdp_req only while its RelayGate is NONE (PB-2.5): it has dropped
            # the running transaction and will refuse its PDUs, so the new request replaces it.
            self._relay_end("abandoned", logging.WARNING,
                            f"superseded by the MCU's new request seq {seq:#010x}")
        try:
            tx = cfdp_tx.start_request(self.data, text)
        except FileNotFoundError as e:
            log.error("CFDPPUT source %d: %s - nothing sent (the MCU reports the 30 s relay-start "
                      "timeout)", source, e)
            return
        except (ValueError, OSError) as e:
            log.error("CFDPPUT source %d seq %#010x refused: %s", source, seq, e)
            return
        self.relay = tx
        self._relay_attempt, self._relay_next = 0, now
        self._relay_progress_t, self._relay_unanswered = now, False
        self.stats["relay_started"] += 1
        log.info("CFDP relay seq %#010x: %s, %d octets (max %d KiB, 0 = whole file)", seq, tx.name,
                 tx.size, max_kib)

    def _do_cfdp_cancel(self, seq: object, t: float, now: float) -> None:
        if not isinstance(seq, int):   # posted only by the handlers above
            return
        tx = self.relay
        if tx is None or tx.seq != seq:
            log.info("cfdp_cancel seq %#010x ignored: %s", seq,
                     "no relay running" if tx is None else f"the running relay is seq {tx.seq:#010x}")
            return
        tx.cancel()                                # the next PDU is EOF(cancel), condition 15
        self._relay_attempt, self._relay_next = 0, now
        log.info("CFDP relay seq %#010x cancelled by CFDPCANCEL after %d of %d octets: EOF(cancel) next",
                 seq, tx.sent, tx.size)

    # ------------------------------------------------------------------ partition / epoch
    def _persist_boot_count(self, value: int, why: str) -> bool:
        try:
            atomic_write_int(self.data / BOOT_FILE, value)
        except OSError as e:
            self.stats["write_errors"] += 1
            log.error("cannot persist boot counter %d (%s): %s", value, why, e)
            return False
        log.warning("boot counter raised from %s to %d: %s", self.boot_count, value, why)
        self.boot_count = value
        return True

    def _issue_epoch(self, reason: str) -> None:
        """Rules 1, 2, 5: boot_count + 1, persisted BEFORE it is offered, so a value can
        reach the MCU at most once per key (GCM IV = 'TM' | epoch | 0 | sn)."""
        if self.epoch_stopped or self.boot_count is None:
            self._log_limited("epoch_blocked", logging.ERROR, "epoch not issued (%s): %s", reason,
                              self.epoch_stopped or "boot counter unreadable")
            return
        n = self.boot_count + 1
        if n > EPOCH_MAX:
            self.epoch_stopped = "epoch space exhausted"
            self.epoch_pending = None
            log.error("SDLS TM epoch space exhausted: generate a new key (tools/gen_sdls_key.py) and "
                      "reset data/boot_count.txt")
            return
        try:
            atomic_write_int(self.data / BOOT_FILE, n)
        except OSError as e:
            self.stats["write_errors"] += 1
            self._issue_retry_at = self._mono() + backoff_s(4)
            log.error("cannot persist boot counter %d (%s): epoch NOT issued, retrying", n, e)
            return
        self._issue_retry_at = None
        self.boot_count = n
        self.epoch_pending = n
        self._epoch_attempt, self._epoch_next = 0, self._mono()
        self.stats["epochs_issued"] += 1
        log.info("SCLK partition / SDLS TM epoch %d issued (%s)", n, reason)

    def _duty_partition(self, now: float) -> None:
        if self._issue_retry_at is not None and now >= self._issue_retry_at:
            self._issue_epoch("retry after a write error")
        if self._reissue_at is not None and now >= self._reissue_at:
            self._reissue_at = None
            if self.boot_count is not None and self._latest_epoch is not None \
                    and self._latest_epoch > self.boot_count:
                self._persist_boot_count(min(self._latest_epoch, EPOCH_MAX), "after a refused epoch")
            self._issue_epoch("after a refused epoch")
        value = self.epoch_pending
        if value is None or now < self._epoch_next:
            return
        try:
            r = self.bridge.call("set_partition", value, timeout=CALL_TIMEOUT_S)
        except self.call_errors as e:
            # Rule 3: no answer -> the SAME value later (already persisted, never used for
            # anything else; the MCU treats an equal epoch as a no-op).
            self._epoch_attempt += 1
            self._epoch_next = self._mono() + backoff_s(self._epoch_attempt)
            self._log_limited("set_partition", logging.WARNING, "set_partition(%d) got no answer "
                              "(%s: %s); retrying the same value", value, type(e).__name__, e)
            return
        # The flight handler returns a C++ bool, which the MCU's MsgPack packer encodes as a
        # msgpack bool (Packer::packBool, 0xC2 / 0xC3): it arrives here as True / False.
        if r is True:
            self.epoch_pending = None
            self.epoch_accepted = value
            self._epoch_ok_t = self._mono()
            self._refusals = 0
            log.info("MCU accepted SCLK partition / SDLS TM epoch %d", value)
        elif r is False:
            # Rule 3a: an explicit refusal is never retried with the same value.
            self.epoch_pending = None
            self._refusals += 1
            self.stats["epoch_refusals"] += 1
            log.error("MCU refused epoch %d (current %s): boot counter regressed", value,
                      "unknown" if self._latest_epoch is None else self._latest_epoch)
            if self._refusals >= MAX_REFUSALS:
                self.epoch_stopped = "epoch negotiation failed"
                log.error("epoch negotiation failed: check data/boot_count.txt and the flight key")
            else:
                self._reissue_at = self._mono() + REFUSAL_WAIT_S
        else:
            self._epoch_attempt += 1
            self._epoch_next = self._mono() + backoff_s(self._epoch_attempt)
            self._log_limited("set_partition_answer", logging.ERROR, "set_partition(%d) answered %r "
                              "(a bool expected); retrying the same value", value, r)

    # ------------------------------------------------------------------ SDLS TC SN restore
    def _load_sn(self, reason: str) -> None:
        try:
            sn = read_int_file(self.data / SN_FILE)
        except CounterFileError as e:
            log.error("%s: SDLS TC anti-replay counter NOT restored (%s); the ground's SN file stays "
                      "authoritative", e, reason)
            return
        if sn is None:
            log.info("no %s yet: nothing to restore (%s)", SN_FILE, reason)
            return
        if not INT32_MIN <= sn <= INT32_MAX:
            log.error("%s holds %d, outside int32: not restored", SN_FILE, sn)
            return
        self.sn_pending = sn
        self._sn_attempt, self._sn_next = 0, self._mono()

    def _duty_sdls_sn(self, now: float) -> None:
        sn = self.sn_pending
        if sn is None or now < self._sn_next:
            return
        try:
            r = self.bridge.call("set_sdls_sn", sn, timeout=CALL_TIMEOUT_S)
        except self.call_errors as e:
            self._sn_attempt += 1
            self._sn_next = self._mono() + backoff_s(self._sn_attempt)
            self._log_limited("set_sdls_sn", logging.WARNING, "set_sdls_sn got no answer (%s: %s); "
                              "retrying", type(e).__name__, e)
            return
        self.sn_pending = None
        if r is True:
            log.info("restored the SDLS TC anti-replay counter (sn %d as int32)", sn)
        else:
            log.error("set_sdls_sn(%d) answered %r (always true in the flight contract)", sn, r)

    # ------------------------------------------------------------------ CFDP relay
    def _relay_end(self, outcome: str, level: int, why: str) -> None:
        tx = self.relay
        if tx is None:
            return
        self.relay = None
        self.stats["relay_" + outcome] += 1
        self.relay_last = (f"seq {tx.seq:#010x} {outcome.upper()} ({tx.sent}/{tx.size} B, "
                           f"condition {tx.condition if tx.condition is not None else '-'})")
        log.log(level, "CFDP relay seq %#010x %s: %s", tx.seq, outcome, why)

    def _relay_finished(self, tx: cfdp_tx.RelayTransfer, note: str) -> None:
        """The EOF was queued by the MCU: the outcome follows its condition code."""
        cond = tx.condition
        outcome = {cfdp_tx.COND_NO_ERROR: "completed", cfdp_tx.COND_CANCEL: "cancelled"}.get(
            cond if cond is not None else -1, "failed")      # 4: the source file stopped delivering
        self._relay_end(outcome, logging.INFO if outcome != "failed" else logging.WARNING,
                        f"EOF condition {cond} queued{note}, {tx.sent} of {tx.size} octets, "
                        f"checksum {tx.checksum:08x}")

    def _duty_cfdp(self, now: float) -> None:
        """One offer of the current relay PDU per pass (PB-6.3): 1 queued -> next PDU,
        0 busy -> same PDU after cfdp_tx.retry_delay_s(), -1 refused -> abandoned, no answer
        -> same PDU after the back-off."""
        tx = self.relay
        if tx is None or now < self._relay_next:
            return
        pdu = tx.current()
        if pdu is None:                            # cannot stay here: advance() past the EOF ends it
            self._relay_finished(tx, "")
            return
        if now - self._relay_progress_t > RELAY_STALL_S:
            self._relay_end("abandoned", logging.ERROR,
                            f"no PDU accepted for {RELAY_STALL_S:.0f} s (the MCU has dropped it)")
            return
        eof = tx.offering_eof
        try:
            r = self.bridge.call("cfdp_pdu", pdu.hex(), timeout=CALL_TIMEOUT_S)
        except self.call_errors as e:
            self._relay_unanswered = True
            self._relay_next = self._mono() + cfdp_tx.retry_delay_s(self._relay_attempt)
            self._relay_attempt += 1
            self._log_limited("cfdp_pdu", logging.WARNING, "cfdp_pdu got no answer (%s: %s); retrying",
                              type(e).__name__, e)
            return
        answer = r if isinstance(r, int) and not isinstance(r, bool) else None
        if answer == 1:
            tx.advance()
            self.stats["relay_pdus"] += 1
            self._relay_attempt, self._relay_next = 0, now
            self._relay_progress_t, self._relay_unanswered = self._mono(), False
            if tx.done:
                self._relay_finished(tx, "")
            return
        if answer == 0:
            self.stats["relay_busy"] += 1          # VC3 full or SDLS TM waiting for its epoch
            self._relay_next = self._mono() + cfdp_tx.retry_delay_s(self._relay_attempt)
            self._relay_attempt += 1
            return
        if answer == -1 and eof and self._relay_unanswered:
            # The previous offer of this EOF got no answer but most probably reached the MCU,
            # whose RelayGate closes on an admitted EOF (-> NONE, PB-2.5): the transfer is over.
            tx.advance()
            self._relay_finished(tx, " (refused on a re-offer after an unanswered one: most probably "
                                     "queued by the first)")
            return
        self._relay_end("abandoned", logging.ERROR,
                        f"the MCU refused a PDU (answer {r!r}): not the authorised transaction, or "
                        "malformed; the ground marks it ABANDONED after its inactivity timer")

    # ------------------------------------------------------------------ hardline
    def _load_hardline(self) -> None:
        """Takes the queue file over atomically (rename) so a line appended meanwhile is
        never lost between reading and clearing it; a `.work` file left by a crash is
        read first."""
        queue = self.data / QUEUE_FILE
        work = self.data / (QUEUE_FILE + ".work")
        if not work.exists():
            try:
                if not queue.is_file() or queue.stat().st_size == 0:
                    return
                os.replace(queue, work)
            except OSError as e:
                self._log_limited("hl_take", logging.ERROR, "cannot take %s: %s", queue, e)
                return
        try:
            lines = work.read_bytes().decode("ascii", errors="replace").splitlines()
            work.unlink()
        except OSError as e:
            self._log_limited("hl_read", logging.ERROR, "cannot read %s: %s", work, e)
            return
        for i, raw in enumerate(lines, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if not (HL_MIN_HEX <= len(line) <= HL_MAX_HEX and len(line) % 2 == 0 and _HEX_RE.fullmatch(line)):
                log.error("hardline line %d rejected: %d characters, %d..%d hex digits (even) expected",
                          i, len(line), HL_MIN_HEX, HL_MAX_HEX)
                continue
            if len(self.hl_queue) >= HL_QUEUE_MAX:
                log.error("hardline line %d dropped: more than %d commands queued", i, HL_QUEUE_MAX)
                continue
            self.hl_queue.append(line)

    def _duty_hardline(self, now: float) -> None:
        if now < self._hl_next:
            return
        if not self.hl_queue:
            self._load_hardline()
        if not self.hl_queue:
            return
        hx = self.hl_queue.popleft()
        try:
            r = self.bridge.call("hl_cltu", hx, timeout=CALL_TIMEOUT_S)
        except self.call_errors as e:
            # Not re-sent: the MCU may have executed it (a BD frame without SDLS would run twice).
            self.stats["hardline_failed"] += 1
            self._hl_next = self._mono() + HARDLINE_PAUSE_S
            log.error("hardline CLTU (%d octets) not confirmed (%s: %s): NOT re-sent - check the "
                      "CMDVER on the ground before sending it again", len(hx) // 2, type(e).__name__, e)
            return
        self.stats["hardline_sent"] += 1
        if r is True:
            log.info("hardline CLTU %d octets -> flight command path", len(hx) // 2)
        else:
            log.error("hardline CLTU %d octets refused by the MCU (answer %r: bad hex or length)",
                      len(hx) // 2, r)

    # ------------------------------------------------------------------ board
    def board_info(self) -> dict[str, object]:
        now = self._mono()
        tx = self.relay
        relay = self.relay_last if tx is None else (
            f"seq {tx.seq:#010x} {tx.name} {tx.stage} {tx.sent}/{tx.size} B "
            f"{tx.progress_permille / 10:.1f} % (busy {self.stats['relay_busy']})")
        return {
            "age_core": "-" if self._t_core is None else f"{now - self._t_core:.0f}",
            "age_x": "-" if self._t_x is None else f"{now - self._t_x:.0f}",
            "epoch": (f"{self.epoch_accepted if self.epoch_accepted is not None else '-'}"
                      f"{' pending ' + str(self.epoch_pending) if self.epoch_pending is not None else ''}"
                      f"{' STOPPED: ' + self.epoch_stopped if self.epoch_stopped else ''}"),
            "cadus": self.stats["cadus"],
            "vc_counts": "{" + ", ".join(f"{k}: {v}" for k, v in sorted(self.vc_counts.items())) + "}",
            "evrs": self.stats["evrs"],
            "relay": relay,
            "inbox_dropped": self.stats["inbox_dropped"],
        }

    def _duty_board(self, now: float) -> None:
        if now < self._board_next:
            return
        self._board_next = now + BOARD_PERIOD_S
        if not self.status_core and not self.status_x:
            log.info("waiting for the flight software status (sc_status) ...")
            return
        for line in render_board(self.status_core, self.status_x, self.board_info()):
            log.info("%s", line)


# =============================================================================
# App Lab entry point
# =============================================================================
def _setup_logging() -> None:
    """Console logging for the App Lab log view (stdout, UTC timestamps). The EGSE logger
    is set to INFO itself, so its records pass even when another library configured the
    root logger first; a handler is added only when nobody installed one."""
    log.setLevel(logging.INFO)
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        fmt = logging.Formatter("%(asctime)s EGSE %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%SZ")
        fmt.converter = time.gmtime
        handler.setFormatter(fmt)
        root.addHandler(handler)


def main(argv: list[str] | None = None) -> int:
    """App Lab entry point: prepare <App>/data, provide the notifications, offer a new
    epoch, then run the user loop until App Lab stops the App (SIGTERM). Returns 2 when
    not running under App Lab or when the data directory cannot be prepared."""
    ap = argparse.ArgumentParser(description="VGQ-1 spacecraft EGSE (Arduino App Lab, UNO Q Linux side)")
    ap.add_argument("--data", type=Path, default=None, help="data directory (default <App>/data)")
    args = ap.parse_args(argv)
    _setup_logging()
    try:
        from arduino.app_utils import App, AppError, Bridge  # type: ignore[import-not-found]
    except ImportError as e:
        log.error("not running under Arduino App Lab (%s): this is the UNO Q App's entry point", e)
        return 2
    try:
        data = prepare_data_dir(APP, args.data)
    except OSError as e:
        log.error("cannot prepare the data directory: %s", e)
        return 2
    egse = Egse(data, Bridge, call_errors=(*CALL_ERRORS, AppError))
    egse.start()
    App.run(user_loop=egse.loop_once)
    return 0


if __name__ == "__main__":
    sys.exit(main())
