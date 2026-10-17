"""Station clock synchronisation state from the kernel (D-76).

Every ERT, the SCLK correlation, the TDM time tags and the archive use the station's
wall clock, so the GDS reports whether the kernel considers that clock synchronised.
The query is READ-ONLY: ntp_adjtime() (glibc / musl; adjtimex() as the fallback) with
modes = 0 changes nothing and needs no privilege. The kernel returns its clock state
(TIME_OK, TIME_INS, TIME_DEL, TIME_OOP, TIME_WAIT or TIME_ERROR) and fills struct timex,
whose status word carries STA_UNSYNC (0x0040, "clock unsynchronized") and whose maxerror /
esterror are the kernel's maximum and estimated error in microseconds (Linux
adjtimex(2), RFC 1589 / RFC 5905 kernel model):

  synchronised      state != TIME_ERROR and STA_UNSYNC clear
  not synchronised  state == TIME_ERROR or STA_UNSYNC set (no NTP / chrony / timesyncd
                    discipline: e.g. the board booted without a network)
  unknown (None)    no libc function, the call failed (a sandbox may refuse it), or not
                    Linux - never guessed as either

StationClock caches the answer and asks the kernel again at most every REFRESH_S (10 s);
the snapshot reads the cache, never the kernel. The GDS (gds.py) shows the state in GDS
health and the header strip, exports /metrics dssq_station_clock_synced, sends it to the
LED matrix (row 7, last column) and refuses TDM records while the clock is NOT
synchronised (a Tracking Data Message carries UTC time tags); "unknown" refuses nothing.
"""
from __future__ import annotations

import ctypes
import math
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

REFRESH_S = 10.0
STA_UNSYNC = 0x0040                 # <sys/timex.h>, Linux adjtimex(2)
TIME_ERROR = 5
CLOCK_STATES = {0: "TIME_OK", 1: "TIME_INS", 2: "TIME_DEL", 3: "TIME_OOP", 4: "TIME_WAIT", 5: "TIME_ERROR"}


class _Timeval(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_long), ("tv_usec", ctypes.c_long)]


class _Timex(ctypes.Structure):
    """glibc struct timex (<bits/timex.h>): __syscall_slong_t is a C long on the ABIs of
    the plain ntp_adjtime / adjtimex symbols (LP64: x86_64, aarch64; ILP32 with 32-bit
    time), 11 reserved int fields at the end."""
    _fields_ = [("modes", ctypes.c_uint), ("offset", ctypes.c_long), ("freq", ctypes.c_long),
                ("maxerror", ctypes.c_long), ("esterror", ctypes.c_long), ("status", ctypes.c_int),
                ("constant", ctypes.c_long), ("precision", ctypes.c_long), ("tolerance", ctypes.c_long),
                ("time", _Timeval), ("tick", ctypes.c_long), ("ppsfreq", ctypes.c_long),
                ("jitter", ctypes.c_long), ("shift", ctypes.c_int), ("stabil", ctypes.c_long),
                ("jitcnt", ctypes.c_long), ("calcnt", ctypes.c_long), ("errcnt", ctypes.c_long),
                ("stbcnt", ctypes.c_long), ("tai", ctypes.c_int), ("_reserved", ctypes.c_int * 11)]


class _TimexBuffer(ctypes.Union):
    """The struct inside a 512-octet buffer: a libc whose struct is larger than this
    declaration still writes into memory that belongs to the query."""
    _fields_ = [("tx", _Timex), ("raw", ctypes.c_char * 512)]


@dataclass(frozen=True)
class ClockReading:
    """One kernel answer. synced None = unknown (reason says why)."""
    synced: bool | None
    state: str | None = None            # CLOCK_STATES name
    sta_unsync: bool | None = None
    maxerror_us: int | None = None
    esterror_us: int | None = None
    reason: str | None = None


def interpret(ret: int, status: int, maxerror: int, esterror: int) -> ClockReading:
    """ClockReading from the return value and the struct timex fields of one query."""
    state = CLOCK_STATES.get(ret, f"state {ret}")
    unsync = bool(status & STA_UNSYNC)
    synced = ret != TIME_ERROR and not unsync
    why = None
    if not synced:
        why = " and ".join(x for x in ("TIME_ERROR" if ret == TIME_ERROR else "",
                                       "STA_UNSYNC" if unsync else "") if x)
    return ClockReading(synced, state, unsync, int(maxerror), int(esterror), why)


def query_kernel() -> ClockReading:
    """The read-only kernel query (modes = 0). Never raises: anything that prevents the
    query gives synced None with the reason."""
    if not sys.platform.startswith("linux"):
        return ClockReading(None, reason=f"not Linux ({sys.platform})")
    try:
        libc = ctypes.CDLL(None, use_errno=True)
    except OSError as e:
        return ClockReading(None, reason=f"libc not loadable: {e}")
    fn: Any = getattr(libc, "ntp_adjtime", None) or getattr(libc, "adjtimex", None)
    if fn is None:
        return ClockReading(None, reason="libc has neither ntp_adjtime nor adjtimex")
    buf = _TimexBuffer()
    buf.tx.modes = 0                                      # read only: nothing is adjusted
    fn.argtypes = [ctypes.POINTER(_TimexBuffer)]
    fn.restype = ctypes.c_int
    ret = int(fn(ctypes.byref(buf)))
    if ret < 0:
        return ClockReading(None, reason=f"ntp_adjtime failed (errno {ctypes.get_errno()})")
    t = buf.tx
    return interpret(ret, int(t.status), int(t.maxerror), int(t.esterror))


class StationClock:
    """Cached station clock state. refresh(now) queries the kernel when the cache is
    REFRESH_S old (or the wall clock stepped back); snapshot() reads the cache only.
    query: the kernel query, looked up at call time when None (tests replace
    query_kernel in this module)."""

    def __init__(self, query: Callable[[], ClockReading] | None = None, refresh_s: float = REFRESH_S):
        self._query = query
        self.refresh_s = refresh_s
        self.reading = ClockReading(None, reason="not queried yet")
        self.checked_t: float | None = None

    def refresh(self, now: float) -> ClockReading:
        t = self.checked_t
        if t is None or not math.isfinite(now) or abs(now - t) >= self.refresh_s:
            q = self._query or query_kernel
            try:
                self.reading = q()
            except (OSError, ValueError, TypeError, AttributeError) as e:   # a broken ctypes binding
                self.reading = ClockReading(None, reason=f"query failed: {e}")
            self.checked_t = now
        return self.reading

    @property
    def synced(self) -> bool | None:
        return self.reading.synced

    def snapshot(self) -> dict[str, Any]:
        """Snapshot block station_clock."""
        r = self.reading
        return {"synced": r.synced, "state": r.state, "sta_unsync": r.sta_unsync, "maxerror_us": r.maxerror_us,
                "esterror_us": r.esterror_us, "reason": r.reason, "checked_t": self.checked_t,
                "refresh_s": self.refresh_s}
