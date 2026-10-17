"""Station clock synchronisation state (dssq/clocksync.py, D-76): the interpretation of the
kernel's answer, the read-only query on this machine, and the cache of StationClock."""
from __future__ import annotations

import ctypes
import math
import sys

import pytest

from dssq import clocksync as cs


@pytest.mark.parametrize("ret, status, synced, reason", [
    (0, 0x0001, True, None),                                   # TIME_OK, STA_PLL only
    (1, 0x0000, True, None),                                   # TIME_INS (leap second pending)
    (4, 0x0000, True, None),                                   # TIME_WAIT after a leap
    (5, 0x0000, False, "TIME_ERROR"),                          # kernel reports TIME_ERROR
    (0, cs.STA_UNSYNC, False, "STA_UNSYNC"),                   # status bit alone
    (5, cs.STA_UNSYNC | 0x0001, False, "TIME_ERROR and STA_UNSYNC"),
])
def test_interpret(ret: int, status: int, synced: bool, reason: str | None) -> None:
    r = cs.interpret(ret, status, 1500, 250)
    assert r.synced is synced
    assert r.reason == reason
    assert r.sta_unsync is bool(status & cs.STA_UNSYNC)
    assert r.state == cs.CLOCK_STATES[ret]
    assert (r.maxerror_us, r.esterror_us) == (1500, 250)


def test_interpret_unknown_state_name() -> None:
    assert cs.interpret(9, 0, 0, 0).state == "state 9"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the kernel query is Linux only")
def test_query_kernel_is_read_only_and_never_raises() -> None:
    before = cs.query_kernel()
    after = cs.query_kernel()
    assert isinstance(before.synced, (bool, type(None)))
    if before.synced is None:
        assert before.reason                                    # unknown always says why
    else:
        assert before.state in cs.CLOCK_STATES.values()
        assert before.maxerror_us is not None and before.maxerror_us >= 0
        assert after.synced == before.synced                    # modes = 0 changed nothing


def test_struct_timex_size_on_lp64() -> None:
    # glibc <bits/timex.h> on LP64 (x86_64, aarch64 - the UNO Q's Debian): 208 octets.
    if ctypes.sizeof(ctypes.c_long) == 8:
        assert ctypes.sizeof(cs._Timex) == 208
    assert ctypes.sizeof(cs._TimexBuffer) >= 512


def test_query_kernel_off_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cs.sys, "platform", "darwin")
    r = cs.query_kernel()
    assert r.synced is None and r.reason == "not Linux (darwin)"


def test_station_clock_cache_and_refresh() -> None:
    answers = [cs.ClockReading(True, "TIME_OK", False, 10, 5),
               cs.ClockReading(False, "TIME_ERROR", True, 9, 9, "TIME_ERROR")]
    calls: list[int] = []

    def fake() -> cs.ClockReading:
        calls.append(1)
        return answers[min(len(calls), len(answers)) - 1]

    c = cs.StationClock(fake, refresh_s=10.0)
    assert c.synced is None and c.snapshot()["reason"] == "not queried yet"
    assert c.refresh(100.0).synced is True and len(calls) == 1
    assert c.refresh(105.0).synced is True and len(calls) == 1       # cached
    assert c.refresh(110.0).synced is False and len(calls) == 2      # REFRESH_S elapsed
    assert c.refresh(50.0).synced is False and len(calls) == 3       # wall clock stepped back
    c.refresh(math.nan)
    assert len(calls) == 4                                            # a NaN time re-queries
    snap = c.snapshot()
    assert set(snap) == {"synced", "state", "sta_unsync", "maxerror_us", "esterror_us", "reason", "checked_t",
                         "refresh_s"}
    assert snap["synced"] is False and snap["reason"] == "TIME_ERROR" and snap["refresh_s"] == 10.0


def test_station_clock_query_failure_is_unknown() -> None:
    def broken() -> cs.ClockReading:
        raise OSError("refused by the sandbox")

    c = cs.StationClock(broken)
    r = c.refresh(0.0)
    assert r.synced is None and r.reason == "query failed: refused by the sandbox"


def test_station_clock_default_query_is_looked_up_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cs, "query_kernel", lambda: cs.ClockReading(True, "TIME_OK", False, 1, 1))
    assert cs.StationClock().refresh(0.0).synced is True
