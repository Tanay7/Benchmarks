r"""Ground commanding: simplified FOP-1 (CCSDS 232.1-B-2) + command verification.

Uplink is half-duplex: the spacecraft listens between its own transmissions,
so the GDS radiates CLTUs immediately after it has received a CADU (the
spacecraft's frame period is >= 2x its radio busy time, leaving a quiet window).

FOP-1 essentials implemented
  * V(S) numbering of Type-AD frames, initialised from the CLCW report value N(R)
  * acknowledgement from N(R): frames with NN(R) <= N(S) < N(R) (mod 256) are confirmed,
    where NN(R) is the previous valid N(R)
  * CLCW validity: an N(R) outside NN(R)..V(S) (a FARM reset by a spacecraft reboot, or a
    SET V(R) from another station) acknowledges nothing; it raises an alert and suspends
    AD service (232.1-B-2 FOP-1 "invalid N(R)")
  * go-back-N retransmission when the CLCW Retransmit flag is set or timer T1 expires
  * transmission limit -> alert: every unacknowledged frame is purged (SUSPENDED), AD
    service is suspended and V(S) is re-initialised from the next CLCW (operator decides)
  * Lockout detection -> suspend AD service until the operator sends UNLOCK
  * Type-BD (bypass) and Type-BC (UNLOCK, SET V(R)) frames. As in FOP-1 "initiate AD
    service with Unlock / Set V(R)", submitting either directive purges the Sent Queue
    (its frames carry N(S) values the re-initialised FARM would discard or lock out on);
    V(S) is then taken from the next CLCW (UNLOCK) or set to the commanded V(R) when the
    SET V(R) frame is radiated. No AD frame is radiated (and no CLCW is checked against
    V(S)) while the CLCW reports Lockout or a directive still waits for its uplink window or
    for its confirmation.
Frames are TC (232.0-B-4) or USLP TC frames (732.1-B-2) per station.toml [link]
tc_framing; the CLCW and the FARM rules are the same for both (USLP N(S) = VCF count).

Command life cycle shown on all displays:
  QUEUED -> RADIATED(n) -> ACCEPTED (FARM, via CLCW) -> EXECUTED / FAILED (CMDVER)
                       \-> TIMEOUT / SUSPENDED
CMDVER carries the command source separately from tc_seq (interface decision I1, decoded
as `cmd_source`): 0 = AD frame (tc_seq = its N(S), matched by N(S) + opcode), 1 = BD frame
or hardline (matched to the oldest open BD record with that opcode; a hardline command of
the same opcode cannot be told apart), 2 = the ON-BOARD stored sequence, which is not a
reply to anything the FOP sent and never touches the COP-1 bookkeeping.
Bypass frames are confirmed by the 2-bit FARM-B counter: the k-th bypass frame radiated
since the last CLCW is confirmed only once the counter has advanced by at least k
(one increment confirms one frame); a SET V(R) additionally needs the reported V(R) to
lie between the commanded value and V(S), an UNLOCK a CLCW without Lockout.

Timers: T1 and the verification timeout are set by the GDS to max(configured, 2 x P) and
max(configured, 4 x P), P = the spacecraft's longest expected frame period (Phase B PB-3.2:
SX126x profile 0 gives P = 138.5 s, so a 45 s T1 would retransmit every frame).

Memory: `all` keeps at most MAX_RECORDS records; only records in a final state are
dropped (oldest first; their summaries are already in the archive), so a command still
in flight is never forgotten.
"""
from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field

from .ccsds import SP_PRI_HDR_LEN, TC_MAX_FRAME_LEN, TC_PRI_HDR_LEN
from .ccsds.tc import build_cltu, build_tc_frame
from .ccsds.tm import Clcw
from .ccsds.uslp import FIXED_HDR_LEN, TC_TFDF_HDR_LEN
from .cmdparse import ParsedCommand, tc_packet
from .dictionary import CMD_ERRORS, CMD_SOURCE_AD, CMD_SOURCE_BD

MAX_RECORDS = 500
FINAL_STATES = frozenset({"EXECUTED", "FAILED", "TIMEOUT", "SUSPENDED"})
FRAMINGS = ("TC", "USLP")
FECF_LEN = 2


def frame_length(kind: str, framing: str, sec_overhead: int, user_data_len: int) -> int:
    """Octets of the TC frame that will carry a command (Phase B PB-2.4, C-08):
        TC v1      5 + SDLS + [6-octet TC packet header] + user data + FECF 2
        USLP AD    7 + 1 (VCF count = N(S)) + SDLS + 1 (TFDF header) + [6] + user data + 2
        USLP BD/BC 7 + SDLS + 1 + [6] + user data + 2
    `kind` AD / BD carry a TC Space Packet (6-octet header + opcode + arguments); BC carries
    the COP-1 directive itself (Unlock 1 octet, Set V(R) 3). `user_data_len` = opcode +
    arguments (AD/BD) or the directive length (BC)."""
    kind, framing = kind.upper(), framing.upper()
    if kind not in ("AD", "BD", "BC") or framing not in FRAMINGS:
        raise ValueError(f"frame kind {kind!r} / framing {framing!r}")
    body = user_data_len + (0 if kind == "BC" else SP_PRI_HDR_LEN)
    if framing == "TC":
        return TC_PRI_HDR_LEN + sec_overhead + body + FECF_LEN
    vcf = 1 if kind == "AD" else 0
    return FIXED_HDR_LEN + vcf + sec_overhead + TC_TFDF_HDR_LEN + body + FECF_LEN


def frame_size_error(cmd: ParsedCommand, framing: str, sec_overhead: int, service_name: str) -> str | None:
    """None when `cmd` fits one TC frame (<= 64 octets), else the operator message (C-08)."""
    n = frame_length(cmd.kind, framing, sec_overhead, len(cmd.user_data))
    if n <= TC_MAX_FRAME_LEN:
        return None
    return (f"frame {n} > {TC_MAX_FRAME_LEN} octets with {framing.upper()}/{service_name}: "
            "use [link] tc_framing = \"TC\"")


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
    farm_b_at_tx: int | None = None    # bypass frames are confirmed by the FARM-B counter
    farm_b_pos: int = 1                # k-th bypass frame since that CLCW: needs k increments
    history: list = field(default_factory=list)

    def set(self, state: str, detail: str = "", t: float | None = None):
        self.state = state
        self.detail = detail
        self.history.append((t or time.time(), state, detail))
        del self.history[:-20]                           # bounded per record

    def summary(self) -> dict:
        return {"id": self.id, "text": self.cmd.text, "kind": self.cmd.kind, "state": self.state,
                "ns": self.ns, "tx": self.transmissions, "detail": self.detail,
                "hazardous": self.cmd.hazardous,
                "t_queued": self.t_queued, "t_radiated": self.t_radiated,
                "t_accepted": self.t_accepted, "t_done": self.t_done,
                "rtlt_s": (self.t_done - self.t_radiated) if self.t_done and self.t_radiated else None}


class Fop1:
    def __init__(self, t1_s: float = 45.0, tx_limit: int = 3, window: int = 5,
                 verify_timeout_s: float = 300.0, sdls=None, framing: str = "TC"):
        if framing.upper() not in FRAMINGS:
            raise ValueError(f"tc_framing {framing!r}: expected TC or USLP")
        self.sdls = sdls                    # SdlsSender or None (unauthenticated)
        self.framing = framing.upper()
        self.t1_s = t1_s                    # in force (the GDS raises it to >= 2 x frame period)
        self.tx_limit = tx_limit
        self.window = window
        self.verify_timeout_s = verify_timeout_s
        self.vs: int | None = None          # None until initialised from a CLCW
        self.nr = 0
        self.nnr = 0                        # NN(R): last valid N(R) (expected acknowledgement)
        self.nr_invalid = False             # alert raised for an N(R) outside NN(R)..V(S)
        self._bypass_since_clcw = 0
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
        self.events: list[tuple[float, str]] = []
        self.build_errors = 0

    # ----------------------------------------------------------------- helpers
    def _remember(self, r: CommandRecord) -> None:
        self.all.append(r)
        excess = len(self.all) - MAX_RECORDS
        if excess > 0:
            keep: list[CommandRecord] = []
            for rec in self.all:                       # bounded by MAX_RECORDS + 1
                if excess > 0 and rec.state in FINAL_STATES:
                    excess -= 1
                    continue
                keep.append(rec)
            self.all = keep

    def _build(self, r: CommandRecord, seq: int, bypass: bool, control: bool) -> bool:
        """Frame + CLTU for r.data; False (record untouched, event logged) when the frame
        cannot be built - SDLS SN not persisted / blocked, or a frame too long."""
        try:
            frame = build_tc_frame(r.data, seq=seq, bypass=bypass, control=control, sdls=self.sdls,
                                   framing=self.framing)
        except (ValueError, RuntimeError, OSError) as e:
            self.build_errors += 1
            self.events.append((time.time(), f"cmd #{r.id} {r.cmd.text}: frame not built ({e})"))
            return False
        r.frame = frame
        r.cltu = build_cltu(frame)
        return True

    def _purge_sent(self, now: float, why: str) -> None:
        """FOP-1 purge of the Sent Queue: every unacknowledged AD frame is given up."""
        for r in self.sent:
            if r.state not in FINAL_STATES:
                r.set("SUSPENDED", why, now)
        if self.sent:
            self.events.append((now, f"FOP: {len(self.sent)} unacknowledged frame(s) purged ({why})"))
        self.sent = []
        self.retransmit_req = False

    def _init_vs(self, value: int) -> None:
        self.vs = value
        self.nnr = value
        self.nr_invalid = False

    @property
    def uplink_blocked(self) -> bool:
        return bool(self.sdls is not None and getattr(self.sdls, "uplink_blocked", False))

    # ----------------------------------------------------------------- operator
    def submit(self, cmd: ParsedCommand, now: float | None = None) -> CommandRecord:
        now = now or time.time()
        r = CommandRecord(next(self._ids), cmd, now)
        r.set("QUEUED", t=now)
        self._remember(r)
        if cmd.kind in ("BD", "BC"):
            self.bypass_q.append(r)
        else:
            self.queue.append(r)
        if cmd.kind == "BC":
            # Initiate AD service with Unlock / Set V(R): the Sent Queue's frames carry N(S)
            # values the re-initialised FARM would discard (or lock out on), so they go.
            self._purge_sent(now, f"purged by {cmd.mnemonic} (AD service re-initialised)")
            self.vs = None                  # UNLOCK: from the next CLCW; SETVR: when radiated
            self.suspended = False
        return r

    # ------------------------------------------------------------------- CLCW
    def on_clcw(self, c: Clcw, now: float | None = None):
        now = now or time.time()
        self.last_clcw = c
        self.nr = c.report_value
        self._bypass_since_clcw = 0
        setvr_out = any(r.cmd.mnemonic == "SETVR" and r.state == "RADIATED" for r in self.all)
        if (self.vs is None or (not self.sent and not self.queue and not setvr_out)) \
                and self.vs != c.report_value:
            self._init_vs(c.report_value)         # (re)initialise AD service from CLCW
        if c.lockout and not self.lockout:
            self.events.append((now, "FARM LOCKOUT reported - AD service suspended, send UNLOCK"))
            self.suspended = True
        if not c.lockout and self.lockout:
            self.events.append((now, "FARM lockout cleared"))
            self._purge_sent(now, "purged: FARM lockout cleared (AD service re-initialised)")
            self._init_vs(c.report_value)
            self.suspended = False
        self.lockout = c.lockout
        self.retransmit_req = c.retransmit
        # Bypass / control frames: the FARM-B counter (2 bits) increments once per accepted
        # BD/BC frame, so the k-th frame radiated since its base CLCW needs k increments.
        for r in self.all:
            if r.cmd.kind in ("BD", "BC") and r.state == "RADIATED" and r.farm_b_at_tx is not None \
                    and (c.farm_b_counter - r.farm_b_at_tx) & 3 >= r.farm_b_pos:
                if r.cmd.kind == "BC" and not self._directive_effective(r, c):
                    continue                      # not (yet) visible in the CLCW: stays RADIATED
                r.t_accepted = now
                if r.cmd.kind == "BC":
                    r.t_done = now
                    r.set("EXECUTED", f"FARM-B {r.farm_b_at_tx}->{c.farm_b_counter}, V(R)={c.report_value}", now)
                else:
                    r.set("ACCEPTED", "FARM-B counter advanced", now)
        if self.vs is None or self._directive_pending():
            return                                # AD service (re)initialising: nothing to check
        # CLCW validity (FOP-1): NN(R) <= N(R) <= V(S), modulo 256.
        if (self.nr - self.nnr) & 0xFF > (self.vs - self.nnr) & 0xFF:
            if not self.nr_invalid:
                self.events.append((now, f"invalid CLCW N(R)={self.nr} (expected {self.nnr}..{self.vs}) - "
                                         "spacecraft FARM reset or SET V(R) by another station? AD service "
                                         "suspended: resynchronise with SETVR or UNLOCK"))
            self.nr_invalid = True
            self.suspended = True
            return
        self.nr_invalid = False
        # Acknowledge frames with NN(R) <= N(S) < N(R)
        acked = (self.nr - self.nnr) & 0xFF
        still = []
        for r in self.sent:
            if r.ns is not None and (r.ns - self.nnr) & 0xFF < acked:
                if r.state in ("RADIATED", "QUEUED"):
                    r.t_accepted = now
                    r.set("ACCEPTED", f"FARM V(R)={self.nr}", now)
            else:
                still.append(r)
        self.sent = still
        self.nnr = self.nr

    def _directive_pending(self) -> bool:
        """A COP-1 directive is waiting for its window or for its CLCW confirmation."""
        return any(q.cmd.kind == "BC" for q in self.bypass_q) or \
            any(r.cmd.kind == "BC" and r.state == "RADIATED" for r in self.all)

    def _directive_effective(self, r: CommandRecord, c: Clcw) -> bool:
        """The CLCW shows the directive's effect: UNLOCK -> no Lockout; SET V(R) n -> the
        reported V(R) lies in n..V(S) (AD frames radiated after it may already count)."""
        if r.cmd.mnemonic == "UNLOCK":
            return not c.lockout
        if r.cmd.mnemonic == "SETVR":
            vr = int(r.cmd.args.get("vr", -1))
            top = self.vs if self.vs is not None else vr
            return (c.report_value - vr) & 0xFF <= (top - vr) & 0xFF
        return True

    # ------------------------------------------------------------- telemetry
    def on_cmdver(self, v: dict, now: float | None = None):
        """Matches a CMDVER packet to its record by the command source (I1): AD frame ->
        the open AD record with that N(S) and opcode; BD frame / hardline -> the oldest open
        BD record with that opcode. Stored-sequence verifications (and an unknown source)
        are not COP-1 traffic: None."""
        now = now or time.time()
        st = v.get("stage")
        stage = {2: "EXECUTED", 3: "FAILED"}.get(st) if isinstance(st, int) else None
        src, tc_seq, opcode = v.get("cmd_source"), v.get("tc_seq"), v.get("opcode")
        if not stage:
            return None
        if src == CMD_SOURCE_AD:
            cands = [r for r in self.all if r.cmd.kind == "AD" and r.cmd.opcode == opcode
                     and r.state in ("RADIATED", "ACCEPTED") and r.ns is not None and r.ns == tc_seq]
        elif src == CMD_SOURCE_BD:
            cands = [r for r in self.all if r.cmd.kind == "BD" and r.cmd.opcode == opcode
                     and r.state in ("RADIATED", "ACCEPTED")]
        else:
            return None
        if not cands:
            return None
        r = cands[0]
        if r.t_accepted is None:
            r.t_accepted = now
        r.t_done = now
        code = v.get("error_code")
        r.set(stage, CMD_ERRORS.get(code, str(code)) if isinstance(code, int) else str(code), now)
        self.sent = [s for s in self.sent if s is not r]
        return r

    # -------------------------------------------------------------- uplink
    def next_cltus(self, now: float | None = None, max_frames: int = 2) -> list[bytes]:
        """Called when an uplink window opens; returns CLTUs to radiate now. Nothing is
        sent while the SDLS sequence number is blocked (F-64): commands stay queued."""
        now = now or time.time()
        out: list[bytes] = []
        # timeouts: from the FARM acceptance when the CLCW has shown it (the CMDVER then follows in
        # the spacecraft's next frames - a hibernating spacecraft sends one per beacon_s), else
        # from the (last) radiation
        for r in self.all:
            t0 = r.t_accepted or r.t_radiated
            if r.state in ("RADIATED", "ACCEPTED") and t0 and now - t0 > self.verify_timeout_s:
                r.set("TIMEOUT", "no verification", now)
                if r in self.sent:
                    self.sent.remove(r)
        if self.uplink_blocked:
            return out
        # bypass first (BC directives / BD commands)
        while self.bypass_q and len(out) < max_frames:
            r = self.bypass_q[0]
            if r.cmd.kind == "BC":
                r.data = r.cmd.user_data
            elif not r.data:
                r.data = tc_packet(r.cmd, self._next_seq())
            if not self._build(r, 0, True, r.cmd.kind == "BC"):
                break
            self.bypass_q.pop(0)
            r.transmissions += 1
            r.t_radiated = now
            r.farm_b_at_tx = self.last_clcw.farm_b_counter if self.last_clcw else None
            self._bypass_since_clcw += 1
            r.farm_b_pos = self._bypass_since_clcw
            r.set("RADIATED", "bypass frame (confirm via CLCW FARM-B counter)", now)
            out.append(r.cltu)
            if r.cmd.mnemonic == "SETVR":
                self._init_vs(int(r.cmd.args["vr"]))   # FOP-1: V(S) := the commanded V(R)
        if self.vs is None or self.suspended or self.lockout or self._directive_pending():
            return out
        # retransmission (go-back-N)
        need_rtx = self.retransmit_req or any(now - r.t_radiated > self.t1_s for r in self.sent
                                              if r.t_radiated)
        if need_rtx and self.sent:
            for r in list(self.sent):
                if len(out) >= max_frames:
                    break
                if r.transmissions >= self.tx_limit:
                    # FOP-1 alert: the whole Sent Queue goes (go-back-N retransmitted the
                    # later frames with this one; kept, they would re-suspend after UNLOCK and
                    # new frames would be numbered past the FARM's V(R)). V(S) is taken again
                    # from the next CLCW.
                    r.set("SUSPENDED", f"transmission limit {self.tx_limit} reached", now)
                    self.events.append((now, f"FOP suspended: cmd #{r.id} not acknowledged"))
                    self._purge_sent(now, f"transmission limit {self.tx_limit} reached (cmd #{r.id})")
                    self.vs = None
                    self.suspended = True
                    return out
                # fresh SDLS sequence number on every transmission (anti-replay)
                if not self._build(r, r.ns or 0, False, False):
                    return out
                r.transmissions += 1
                r.t_radiated = now
                r.set("RADIATED", f"retransmission {r.transmissions}", now)
                out.append(r.cltu)
            self.retransmit_req = False
            return out
        # new AD frames within the sliding window
        while self.queue and len(out) < max_frames and len(self.sent) < self.window:
            r = self.queue[0]
            if not r.data:
                r.data = tc_packet(r.cmd, self._next_seq())
            if not self._build(r, self.vs, False, False):
                break
            self.queue.pop(0)
            r.ns = self.vs
            self.vs = (self.vs + 1) & 0xFF
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
                "t1_s": self.t1_s, "verify_timeout_s": self.verify_timeout_s,
                "uplink_blocked": self.uplink_blocked, "build_errors": self.build_errors,
                "records": len(self.all), "recent": [r.summary() for r in self.all[-12:]]}
