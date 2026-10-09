r"""Ground commanding: simplified FOP-1 (CCSDS 232.1-B-2) + command verification.

Uplink is half-duplex: the spacecraft listens between its own transmissions,
so the GDS radiates CLTUs immediately after it has received a CADU (the
spacecraft's frame period is >= 2x its radio busy time, leaving a quiet window).

FOP-1 essentials implemented
  * V(S) numbering of Type-AD frames, initialised from the CLCW report value N(R)
  * acknowledgement from N(R): frames with N(S) < N(R) (mod 256) are confirmed
  * go-back-N retransmission when the CLCW Retransmit flag is set or timer T1 expires
  * transmission limit -> alert and suspend (operator decides)
  * Lockout detection -> suspend AD service until the operator sends UNLOCK
  * Type-BD (bypass) and Type-BC (UNLOCK, SET V(R)) frames

Command life cycle shown on all displays:
  QUEUED -> RADIATED(n) -> ACCEPTED (FARM, via CLCW) -> EXECUTED / FAILED (CMDVER)
                       \-> TIMEOUT / SUSPENDED
"""
from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field

from .ccsds.tc import build_cltu, build_tc_frame
from .ccsds.tm import Clcw
from .cmdparse import ParsedCommand, tc_packet


@dataclass
class CommandRecord:
    id: int
    cmd: ParsedCommand
    t_queued: float
    state: str = "QUEUED"
    ns: int | None = None              # N(S) of the carrying AD frame
    data: bytes = b""                  # unprotected frame data field (TC packet / BC command)
    frame: bytes = b""
    cltu: bytes = b""
    transmissions: int = 0
    t_radiated: float | None = None
    t_accepted: float | None = None
    t_done: float | None = None
    detail: str = ""
    ground_tag: int | None = None
    farm_b_at_tx: int | None = None    # bypass frames are confirmed by the FARM-B counter
    history: list = field(default_factory=list)

    def set(self, state: str, detail: str = "", t: float | None = None):
        self.state = state
        self.detail = detail
        self.history.append((t or time.time(), state, detail))

    def summary(self) -> dict:
        return {"id": self.id, "text": self.cmd.text, "kind": self.cmd.kind, "state": self.state,
                "ns": self.ns, "tx": self.transmissions, "detail": self.detail,
                "hazardous": self.cmd.hazardous,
                "t_queued": self.t_queued, "t_radiated": self.t_radiated,
                "t_accepted": self.t_accepted, "t_done": self.t_done,
                "rtlt_s": (self.t_done - self.t_radiated) if self.t_done and self.t_radiated else None}


class Fop1:
    def __init__(self, t1_s: float = 45.0, tx_limit: int = 3, window: int = 5,
                 verify_timeout_s: float = 300.0, sdls=None):
        self.sdls = sdls                    # SdlsSender or None (unauthenticated)
        self.t1_s = t1_s
        self.tx_limit = tx_limit
        self.window = window
        self.verify_timeout_s = verify_timeout_s
        self.vs: int | None = None          # None until initialised from a CLCW
        self.nr = 0
        self.lockout = False
        self.suspended = False
        self.retransmit_req = False
        self.last_clcw: Clcw | None = None
        self.queue: list[CommandRecord] = []       # waiting for a frame number
        self.sent: list[CommandRecord] = []        # AD frames not yet acknowledged
        self.bypass_q: list[CommandRecord] = []    # BD / BC frames
        self.all: list[CommandRecord] = []
        self._ids = itertools.count(1)
        self._tc_seq = 0
        self._last_send = 0.0
        self.events: list[tuple[float, str]] = []

    # ----------------------------------------------------------------- operator
    def submit(self, cmd: ParsedCommand, now: float | None = None) -> CommandRecord:
        now = now or time.time()
        r = CommandRecord(next(self._ids), cmd, now)
        if cmd.mnemonic == "PING":
            r.ground_tag = cmd.args.get("tag")
        r.set("QUEUED", t=now)
        self.all.append(r)
        if cmd.kind in ("BD", "BC"):
            self.bypass_q.append(r)
        else:
            self.queue.append(r)
        if cmd.kind == "BC" and cmd.mnemonic == "UNLOCK":
            self.suspended = False
        return r

    # ------------------------------------------------------------------- CLCW
    def on_clcw(self, c: Clcw, now: float | None = None):
        now = now or time.time()
        self.last_clcw = c
        self.nr = c.report_value
        if self.vs is None or (not self.sent and not self.queue):
            if self.vs != c.report_value:
                self.vs = c.report_value          # (re)initialise AD service from CLCW
        if c.lockout and not self.lockout:
            self.events.append((now, "FARM LOCKOUT reported - AD service suspended, send UNLOCK"))
            self.suspended = True
        if not c.lockout and self.lockout:
            self.events.append((now, "FARM lockout cleared"))
            self.vs = c.report_value
            self.suspended = False
        self.lockout = c.lockout
        self.retransmit_req = c.retransmit
        # Bypass / control frames: the FARM-B counter (2 bits) increments on receipt.
        for r in self.all:
            if r.cmd.kind in ("BD", "BC") and r.state == "RADIATED" and r.farm_b_at_tx is not None \
                    and c.farm_b_counter != r.farm_b_at_tx:
                r.t_accepted = now
                if r.cmd.kind == "BC":
                    r.t_done = now
                    r.set("EXECUTED", f"FARM-B {r.farm_b_at_tx}->{c.farm_b_counter}, V(R)={c.report_value}", now)
                else:
                    r.set("ACCEPTED", "FARM-B counter advanced", now)
        # Acknowledge frames with N(S) < N(R) (mod 256, within the window)
        still = []
        for r in self.sent:
            if ((self.nr - r.ns) & 0xFF) != 0 and ((self.nr - r.ns) & 0xFF) <= 128:
                if r.state in ("RADIATED", "QUEUED"):
                    r.t_accepted = now
                    r.set("ACCEPTED", f"FARM V(R)={self.nr}", now)
            else:
                still.append(r)
        self.sent = still

    # ------------------------------------------------------------- telemetry
    def on_cmdver(self, v: dict, now: float | None = None):
        """Matches a CMDVER packet to the oldest open record with that opcode/N(S)."""
        now = now or time.time()
        stage = {2: "EXECUTED", 3: "FAILED"}.get(v.get("stage"), None)
        if not stage:
            return None
        cands = [r for r in self.all if r.cmd.opcode == v.get("opcode")
                 and r.state in ("RADIATED", "ACCEPTED")
                 and (v.get("tc_seq") == 255 or r.ns is None or r.ns == v.get("tc_seq"))]
        if not cands:
            return None
        r = cands[0]
        if r.t_accepted is None:
            r.t_accepted = now
        r.t_done = now
        from .dictionary import CMD_ERRORS
        r.set(stage, CMD_ERRORS.get(v.get("error_code"), str(v.get("error_code"))), now)
        self.sent = [s for s in self.sent if s is not r]
        return r

    # -------------------------------------------------------------- uplink
    def next_cltus(self, now: float | None = None, max_frames: int = 2) -> list[bytes]:
        """Called when an uplink window opens; returns CLTUs to radiate now."""
        now = now or time.time()
        out: list[bytes] = []
        # timeouts
        for r in self.all:
            if r.state in ("RADIATED", "ACCEPTED") and r.t_radiated and \
                    now - r.t_radiated > self.verify_timeout_s:
                r.set("TIMEOUT", "no verification", now)
        # bypass first (BC directives / BD commands)
        while self.bypass_q and len(out) < max_frames:
            r = self.bypass_q.pop(0)
            r.data = r.cmd.user_data if r.cmd.kind == "BC" else tc_packet(r.cmd, self._next_seq())
            r.frame = build_tc_frame(r.data, seq=0, bypass=True, control=(r.cmd.kind == "BC"),
                                     sdls=self.sdls)
            r.cltu = build_cltu(r.frame)
            r.transmissions += 1
            r.t_radiated = now
            r.farm_b_at_tx = self.last_clcw.farm_b_counter if self.last_clcw else None
            r.set("RADIATED", "bypass frame (confirm via CLCW FARM-B counter)", now)
            out.append(r.cltu)
        if self.vs is None or self.suspended:
            return out
        # retransmission (go-back-N)
        need_rtx = self.retransmit_req or any(now - r.t_radiated > self.t1_s for r in self.sent
                                              if r.t_radiated)
        if need_rtx and self.sent:
            for r in list(self.sent):
                if len(out) >= max_frames:
                    break
                if r.transmissions >= self.tx_limit:
                    r.set("SUSPENDED", f"transmission limit {self.tx_limit} reached", now)
                    self.events.append((now, f"FOP suspended: cmd #{r.id} not acknowledged"))
                    self.suspended = True
                    self.sent.remove(r)
                    return out
                r.transmissions += 1
                r.t_radiated = now
                # fresh SDLS sequence number on every transmission (anti-replay)
                r.frame = build_tc_frame(r.data, seq=r.ns, sdls=self.sdls)
                r.cltu = build_cltu(r.frame)
                r.set("RADIATED", f"retransmission {r.transmissions}", now)
                out.append(r.cltu)
            self.retransmit_req = False
            return out
        # new AD frames within the sliding window
        while self.queue and len(out) < max_frames and len(self.sent) < self.window:
            r = self.queue.pop(0)
            r.ns = self.vs
            self.vs = (self.vs + 1) & 0xFF
            r.data = tc_packet(r.cmd, self._next_seq())
            r.frame = build_tc_frame(r.data, seq=r.ns, sdls=self.sdls)
            r.cltu = build_cltu(r.frame)
            r.transmissions = 1
            r.t_radiated = now
            r.set("RADIATED", f"N(S)={r.ns}", now)
            self.sent.append(r)
            out.append(r.cltu)
        return out

    def _next_seq(self) -> int:
        s = self._tc_seq
        self._tc_seq = (self._tc_seq + 1) & 0x3FFF
        return s

    def state(self) -> dict:
        return {"vs": self.vs, "nr": self.nr, "lockout": self.lockout, "suspended": self.suspended,
                "retransmit": self.retransmit_req, "queued": len(self.queue),
                "outstanding": len(self.sent), "pending_bypass": len(self.bypass_q),
                "farm_b": self.last_clcw.farm_b_counter if self.last_clcw else None,
                "recent": [r.summary() for r in self.all[-12:]]}
