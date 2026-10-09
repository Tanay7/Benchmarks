"""Software spacecraft + RF channel + ground-E22 simulator ("testbed").

Lets the whole ground system (GDS, displays, commanding, link manager, front
panel) run with no hardware:

  GDS ──write()──► [ground E22 emulator] ──RF──► [VGQ-1 spacecraft model]
  GDS ◄──read()─── [ground E22 emulator] ◄─RF─── (CADUs every frame period)

* Spacecraft model: same packet layouts, cadences, VC multiplexing, TM framing,
  RS coding, CLCW/FARM-1, SDLS verification, command set (incl. AIRRATE/CHANNEL
  with auto-revert, RFSCAN, HIBERNATE/WOR) as the flight software.
* RF channel: log-normal fading around a configurable mean RSSI; packet loss is
  a smooth function of margin above the air-rate sensitivity estimate; a few
  random symbol errors are injected to exercise the RS decoder. Both ends only
  hear each other when channel AND air rate match (as with real E22 modules).
* Ground E22 emulator: honours M0/M1 mode (via the simulated RCU), answers C0/C1/C2
  configuration commands and C0 C1 C2 C3 noise queries, appends RSSI octets.
"""
from __future__ import annotations

import math
import random
import struct
import time
from collections import deque

from . import ccsds as C
from .ccsds.packets import build_idle_packet, build_packet
from .ccsds.sdls import SdlsReceiver, load_key
from .ccsds.tc import Farm1Model, cltu_decode, parse_tc_frame
from .ccsds.timecode import CucTime
from .ccsds.tm import Clcw, build_frame, encode_cadu
from .link import AIR_RATE_BPS, SENSITIVITY_EST_DBM

CADENCE = {  # mode -> (HK, RF, TIMECORR, MAG, ATT, SPEC, ENV) seconds, as flight
    0: (10, 10, 0, 0, 0, 0, 0), 1: (30, 60, 300, 0, 0, 0, 0), 2: (60, 60, 300, 30, 60, 300, 120),
    3: (20, 30, 120, 4, 8, 30, 30), 4: (10, 10, 60, 4, 8, 30, 30)}
PERIOD_X10 = {0: 40, 1: 40, 2: 25, 3: 20, 4: 20}


def airtime_s(rate_code: int) -> float:
    """UART transfer (236 B at 9600) + LoRa air time estimate (+30 %), as flight."""
    return 236 * 10 / 9600 + 236 * 8 * 1.3 / AIR_RATE_BPS[rate_code]


class VcMux:
    """Python port of the flight VcStream/TmFramer behaviour."""

    def __init__(self):
        self.buf = {0: bytearray(), 1: bytearray(), 2: bytearray()}
        self.starts = {0: deque(), 1: deque(), 2: deque()}
        self.real_end = {0: 0, 1: 0, 2: 0}
        self.base = {0: 0, 1: 0, 2: 0}         # absolute index of buf[0]
        self.mcfc = 0
        self.vcfc = [0] * 8
        self.idle_seq = 0

    def push(self, vc, pkt, idle=False):
        if len(self.buf[vc]) + len(pkt) > 1024:
            return False
        self.starts[vc].append(self.base[vc] + len(self.buf[vc]))
        self.buf[vc] += pkt
        if not idle:
            self.real_end[vc] = self.base[vc] + len(self.buf[vc])
        return True

    def has_real(self, vc):
        return self.real_end[vc] > self.base[vc]

    def frame(self, vc, clcw: int) -> bytes:
        b = self.buf[vc]
        if len(b) < C.TM_DATA_LEN:
            gap = C.TM_DATA_LEN - len(b)
            n = max(7, gap)
            self.push(vc, build_idle_packet(n, self.idle_seq), idle=True)
            self.idle_seq = (self.idle_seq + 1) & 0x3FFF
        out = self.base[vc]
        fhp = C.FHP_NO_PACKET_START
        st = self.starts[vc]
        while st and st[0] < out:
            st.popleft()
        if st and st[0] - out < C.TM_DATA_LEN:
            fhp = st[0] - out
        data = bytes(b[:C.TM_DATA_LEN])
        del b[:C.TM_DATA_LEN]
        self.base[vc] += C.TM_DATA_LEN
        while st and st[0] < self.base[vc]:
            st.popleft()
        f = build_frame(vc, self.mcfc, self.vcfc[vc], fhp, data, clcw)
        self.mcfc = (self.mcfc + 1) & 0xFF
        self.vcfc[vc] = (self.vcfc[vc] + 1) & 0xFF
        return f

    def oid(self, clcw: int) -> bytes:
        f = build_frame(7, self.mcfc, self.vcfc[7], C.FHP_ONLY_IDLE_DATA, b"\x55" * C.TM_DATA_LEN, clcw)
        self.mcfc = (self.mcfc + 1) & 0xFF
        self.vcfc[7] = (self.vcfc[7] + 1) & 0xFF
        return f


class SimSpacecraft:
    """Implements the radio interface used by gds.main(): read(), write(), close(),
    plus a simulated Radio Control Unit (rcu_*) for M0/M1 mode control."""

    def __init__(self, cfg: dict, loss: float = 0.02, max_errors: int = 3, rssi_mean: float = -112.0,
                 fading_db: float = 3.0, seed: int | None = None, clock=time.time):
        self.rng = random.Random(seed)
        self.clock = clock
        self.base_loss = loss
        self.max_errors = max_errors
        self.rssi_mean = rssi_mean
        self.fading_db = fading_db
        r = cfg["radio"]
        # --- ground E22 emulator state ---
        self.g_regs = bytearray(9)
        from .radio.e22 import regs_from_cfg
        self.g_regs[:] = regs_from_cfg(r)
        self.g_mode = 0                      # 0 NORMAL, 1 WOR, 2 CONFIG, 3 SLEEP
        self.out: list[tuple[float, bytes]] = []
        self.last_rx_rssi = -128
        # --- spacecraft state ---
        self.sc_ch = int(r.get("channel", 23))
        self.sc_rate = int(r.get("air_rate", 2))
        self.sc_pwr = 0
        self.mode = 1                        # boots SAFE, like flight
        self.t0 = clock()
        self.partition = 1
        self.mux = VcMux()
        self.farm = Farm1Model()
        key = load_key(cfg.get("sdls", {}).get("key_file")) if cfg.get("sdls", {}).get("enabled") else None
        self.sdls = SdlsReceiver(key, int(cfg.get("sdls", {}).get("spi", 1))) if key else None
        self.seq = {}
        self.last_gen = [0.0] * 7
        self.next_tx = clock() + 1.0
        self.frames_sent = 0
        self.oid_sent = 0
        self.cmd_acc = self.cmd_rej = 0
        self.last_opcode = self.last_status = 0
        self.tc_ok = self.tc_bad = 0
        self.uplink_rssi = None
        self.fdir = 0
        self.pending = None                  # (field, value, frames_left, revert_s)
        self.revert = None                   # (field, old_value, deadline)
        self.hibernating = False
        self.beacon_s = 600
        self.ert_mcfc_sclk = {}
        self.timecorr_req = False
        self.last_tc_t = clock()
        self.tx_duty_hist = deque(maxlen=60)
        self.evr_q: list[tuple[int, int, str]] = [(1, 0x0001, "BOOT VGQ-1 FSW 1.0 (SIMULATOR)")]
        self.rfscan_req = None
        self.ping_tag = 0

    # ================================================================ RCU (sim)
    def rcu_set_mode(self, mode: int) -> bool:
        self.g_mode = mode & 3
        return True

    def rcu_aux(self) -> bool:
        return True

    # ===================================================== GDS-facing interface
    def read(self) -> bytes:
        now = self.clock()
        self._run_spacecraft(now)
        ready = [b for t, b in self.out if t <= now]
        self.out = [(t, b) for t, b in self.out if t > now]
        if not ready:
            time.sleep(0.02) if self.clock is time.time else None
        return b"".join(ready)

    def write(self, data: bytes):
        now = self.clock()
        if self.g_mode == 2:                                   # CONFIG mode
            self._ground_config(data, now)
            return
        if data[:4] == bytes([0xC0, 0xC1, 0xC2, 0xC3]):        # ambient-noise query
            noise = self._noise_dbm(self._g_ch())
            self.out.append((now + 0.02, bytes([0xC1, 0x00, 0x02, (256 + int(noise)) & 0xFF,
                                                 (256 + self.last_rx_rssi) & 0xFF])))
            return
        if self.g_mode in (0, 1):                              # RF transmission (uplink)
            wor_tx = self.g_mode == 1 and (self.g_regs[6] & 0x08)
            t_arrive = now + airtime_s(self._g_rate()) + (2.0 if wor_tx else 0.0)
            self._uplink(data, t_arrive, bool(wor_tx))

    def close(self):
        pass

    # ======================================================== ground E22 model
    def _g_ch(self):
        return self.g_regs[5]

    def _g_rate(self):
        return self.g_regs[3] & 7

    def _ground_config(self, data: bytes, now: float):
        if len(data) >= 3 and data[0] in (0xC0, 0xC2) and data[1] + data[2] <= 9:
            a, n = data[1], data[2]
            self.g_regs[a:a + n] = data[3:3 + n]
            self.out.append((now + 0.05, bytes([0xC1, a, n]) + bytes(self.g_regs[a:a + n])))
        elif len(data) >= 3 and data[0] == 0xC1:
            a, n = data[1], data[2]
            if a == 0x80:
                self.out.append((now + 0.05, bytes([0xC1, 0x80, 7]) + bytes([0x00, 0x22, 0x37, 0, 0, 0, 0])))
            else:
                self.out.append((now + 0.05, bytes([0xC1, a, n]) + bytes(self.g_regs[a:a + n])))

    def _noise_dbm(self, ch: int) -> float:
        # quiet band with two "interferers" so the RF survey has something to find
        extra = 18 if ch == 24 else (9 if ch in (21, 30) else 0)
        return -118 + extra + self.rng.gauss(0, 0.7)

    # ====================================================== spacecraft model
    def _sclk(self, now):
        return now - self.t0

    def _pkt(self, apid, payload, now):
        s = self.seq.get(apid, 0)
        self.seq[apid] = (s + 1) & 0x3FFF
        return build_packet(apid, s, payload, CucTime.from_seconds(self._sclk(now)))

    def _emit(self, apid, payload, vc, now):
        self.mux.push(vc, self._pkt(apid, payload, now))

    def _evr(self, sev, eid, text):
        self.evr_q.append((sev, eid, text))

    def _run_spacecraft(self, now):
        # revert / command-loss timers
        if self.revert and now > self.revert[2]:
            field, old, _ = self.revert
            setattr(self, field, old)
            self.revert = None
            self.fdir |= 1 << 12
            self._evr(3, 0x0602, f"radio change NOT confirmed by uplink - reverting {field}={old}")
        t = self._sclk(now)
        for sev, eid, text in self.evr_q:
            self._emit(0x012, bytes([sev]) + eid.to_bytes(2, "big") + text.encode()[:80], 0, now)
        self.evr_q.clear()
        if self.rfscan_req:
            first, last = self.rfscan_req
            self.rfscan_req = None
            noise = [max(-127, int(round(self._noise_dbm(c)))) for c in range(first, last + 1)]
            payload = bytes([first, len(noise)]) + int(2500).to_bytes(2, "big") + \
                bytes((256 + n) & 0xFF for n in noise)
            self._emit(0x015, payload, 0, now)
            best = first + noise.index(min(noise))
            self._evr(1, 0x0701, f"RF survey done: quietest CH{best} ({min(noise)} dBm)")
        cad = CADENCE[self.mode]
        gens = (self._hk, self._rf, self._timecorr, self._mag, self._att, self._spec, self._env)
        for i, period in enumerate(cad):
            if period and (self.last_gen[i] == 0 or t - self.last_gen[i] >= period):
                self.last_gen[i] = t
                if not self.hibernating or i == 0:
                    gens[i](now)
        if self.timecorr_req:
            self.timecorr_req = False
            self._timecorr(now)
        if now < self.next_tx:
            return
        at = airtime_s(self.sc_rate)
        period = (self.beacon_s if self.hibernating else
                  max(1.5, at * PERIOD_X10[self.mode] / 10, at * 1000 / 500))
        self.next_tx = now + period
        clcw = Clcw.pack(lockout=self.farm.state == 3, retransmit=self.farm.retransmit,
                         farm_b=self.farm.farm_b, report_value=self.farm.vr,
                         no_bitlock=(now - self.last_tc_t) > 300)
        vc = next((v for v in (0, 1, 2) if self.mux.has_real(v)), None)
        if vc is None:
            frame = self.mux.oid(clcw)
            self.oid_sent += 1
        else:
            frame = self.mux.frame(vc, clcw)
        self.ert_mcfc_sclk[frame[2]] = (t, at)
        self.frames_sent += 1
        self.tx_duty_hist.append((now, at))
        if self.pending:
            field, value, left, revert_s = self.pending
            if left <= 1:
                self.revert = (field, getattr(self, field), now + revert_s)
                setattr(self, field, value)
                self.pending = None
            else:
                self.pending = (field, value, left - 1, revert_s)
        self._downlink(encode_cadu(frame), now + at)

    # ----------------------------------------------------------- RF channel
    def _rssi(self) -> float:
        p_adj = 0.0                      # E22-400T37S: every power code is 37 dBm
        return self.rssi_mean + p_adj + self.rng.gauss(0, self.fading_db)

    def _downlink(self, cadu: bytes, t_arrive: float):
        if self.g_mode not in (0, 1) or self._g_ch() != self.sc_ch or self._g_rate() != self.sc_rate:
            return                                          # ground not listening on this link
        rssi = self._rssi()
        margin = rssi - SENSITIVITY_EST_DBM[self.sc_rate]
        p_loss = self.base_loss + (1 - self.base_loss) / (1 + math.exp(margin / 1.5))
        if self.rng.random() < p_loss:
            return
        b = bytearray(cadu)
        for _ in range(self.rng.randint(0, self.max_errors)):   # residual symbol errors
            b[self.rng.randrange(4, len(b))] ^= self.rng.randrange(1, 256)
        rssi_i = max(-127, min(-1, int(round(rssi))))
        self.last_rx_rssi = rssi_i
        self.out.append((t_arrive, bytes(b) + bytes([(256 + rssi_i) & 0xFF])))

    def _uplink(self, data: bytes, t_arrive: float, wor_tx: bool):
        if self._g_ch() != self.sc_ch or self._g_rate() != self.sc_rate:
            return
        if self.hibernating and not wor_tx and self.rng.random() < 0.9:
            return                                          # WOR receiver mostly asleep
        rssi = self._rssi() + 0.0
        if self.rng.random() < self.base_loss:
            return
        self.uplink_rssi = int(round(rssi))
        self._on_tc(data, t_arrive)

    # ---------------------------------------------------------- commanding
    def _on_tc(self, data: bytes, now: float):
        info, _ = cltu_decode(data)
        f = parse_tc_frame(info) if info else None
        if not f:
            self.tc_bad += 1
            return
        frame_bytes = info[:f.length]
        body = f.data
        if self.sdls:
            body, verdict = self.sdls.process(frame_bytes, f.data)
            if body is None:
                self.tc_bad += 1
                self.fdir |= 1 << 13
                self._evr(3, 0x0110, f"SDLS rejected TC frame: {verdict}")
                return
            f.data = body
        self.tc_ok += 1
        self.last_tc_t = now
        if self.hibernating:
            self.hibernating = False
            self.fdir &= ~(1 << 14)
            self._evr(2, 0x0801, "WAKE: authenticated uplink received")
        verdict = self.farm.on_frame(f)
        if self.revert and verdict in ("ACCEPT", "ACCEPT_BYPASS"):
            self.revert = None
            self._evr(1, 0x0210, "radio change confirmed by ground")
        if verdict in ("ACCEPT", "ACCEPT_BYPASS"):
            self._execute(f.data, f.seq if verdict == "ACCEPT" else 0xFF, now)
        elif verdict == "DISCARD_LOCKOUT":
            self._evr(3, 0x0205, f"FARM LOCKOUT (N(S)={f.seq} V(R)={self.farm.vr})")
        elif verdict.startswith("CONTROL"):
            self._evr(1, 0x0201, f"COP-1 {verdict}")

    def _execute(self, pkt: bytes, seq: int, now: float):
        opcode = pkt[6] if len(pkt) > 6 else 0
        a = pkt[7:]
        err = 0
        tag = 0
        if opcode == 0x01:
            pass
        elif opcode == 0x02 and a and 1 <= a[0] <= 4:
            self.mode = a[0]
            self.hibernating = False
            self.last_gen = [0.0] * 7
            self._evr(1, 0x0010 + a[0], f"MODE -> {['BOOT','SAFE','CRUISE','ENCOUNTER','TEST'][a[0]]}")
        elif opcode == 0x03 and a and a[0] <= 3:
            self.sc_pwr = a[0]
        elif opcode == 0x04 and len(a) == 3:
            self.pending = ("sc_rate", a[0], 2, int.from_bytes(a[1:3], "big"))
            self._evr(2, 0x0211, f"AIR RATE -> code {a[0]} in 2 frames")
        elif opcode == 0x15 and len(a) == 3:
            self.pending = ("sc_ch", a[0], 2, int.from_bytes(a[1:3], "big"))
            self._evr(2, 0x0212, f"CHANNEL -> {a[0]} in 2 frames")
        elif opcode == 0x09 and len(a) == 4:
            tag = int.from_bytes(a, "big")
        elif opcode == 0x12:
            self.timecorr_req = True
        elif opcode == 0x13 and len(a) == 2:
            self.rfscan_req = (a[0], a[1])
        elif opcode == 0x14 and len(a) == 2:
            self.hibernating = True
            self.beacon_s = int.from_bytes(a, "big")
            self.fdir |= 1 << 14
            self._evr(2, 0x0800, f"HIBERNATE: beacon every {self.beacon_s} s")
        elif opcode in (0x05, 0x06, 0x07, 0x08, 0x0A, 0x0B, 0x0D, 0x0E, 0x0F, 0x10, 0x11, 0x16):
            pass                                            # accepted, no simulated effect
        elif opcode == 0x0C:
            err = 0 if a == b"\xb0\x07" else 6
        else:
            err = 1
        if err:
            self.cmd_rej += 1
        else:
            self.cmd_acc += 1
        self.last_opcode, self.last_status = opcode, err
        self._emit(0x013, bytes([seq, opcode, 3 if err else 2, err]) + tag.to_bytes(4, "big"), 0, now)

    # ------------------------------------------------------- packet builders
    def _hk(self, now):
        t = self._sclk(now)
        duty = sum(a for tt, a in self.tx_duty_hist if now - tt < 60) / 60
        p = struct.pack(">BHIBHHHHBBHHBBIhB" + "H" * 10 + "BHI",
                        self.mode, self.partition, int(t), 0, self.fdir, 0x3FFF, self.cmd_acc,
                        self.cmd_rej, self.last_opcode, self.last_status, self.tc_ok, self.tc_bad,
                        self.farm.state, self.farm.vr, int(max(0.0, now - self.last_tc_t)),
                        int((31 + 4 * duty + self.rng.gauss(0, 0.1)) * 100), 0, 0,
                        120, 0, 0, 0, 0, 256, len(self.mux.buf[0]), len(self.mux.buf[1]),
                        len(self.mux.buf[2]), int(self.sdls is not None),
                        self.sdls.failures if self.sdls else 0, self.sdls.last_sn if self.sdls else 0)
        self._emit(0x010, p, 0, now)

    def _rf(self, now):
        duty = sum(a for tt, a in self.tx_duty_hist if now - tt < 60) / 60
        at = airtime_s(self.sc_rate)
        p = struct.pack(">BBBBIIIHHHhhHHHIBB", self.sc_rate, self.sc_pwr, self.sc_ch, 1,
                        self.frames_sent, self.oid_sent, int(self.frames_sent * at * 1000),
                        int(duty * 1000), int(max(1.5, at * PERIOD_X10[self.mode] / 10) * 1000), 0,
                        self.uplink_rssi if self.uplink_rssi is not None else 0x7FFF,
                        int(round(self._noise_dbm(self.sc_ch))), self.tc_ok, self.tc_bad, 0,
                        self.tc_ok * 40, self.mux.mcfc, min(255, int(at * 10)))
        self._emit(0x011, p, 0, now)

    def _timecorr(self, now):
        if not self.ert_mcfc_sclk:
            return
        mcfc = (self.mux.mcfc - 1) & 0xFF
        sclk, at = self.ert_mcfc_sclk.get(mcfc, (None, None))
        if sclk is None:
            return
        c = CucTime.from_seconds(sclk)
        self._emit(0x014, struct.pack(">BIHBH", mcfc, c.coarse, c.fine, self.sc_rate, int(at * 1000)), 0, now)

    def _mag(self, now):
        g = self.rng.gauss
        tx = 2.0 if self.tx_duty_hist and now - self.tx_duty_hist[-1][0] < 4 else 0.0

        def v(bx, by, bz, extra, noise):
            return [int((bx + g(0, noise)) * 1000), int((by + g(0, noise)) * 1000),
                    int((bz + extra + g(0, noise)) * 1000), int(abs(g(noise * 600, 3)))]
        ob = v(19.5, 0.5, 44.0, 0.1 * tx, 0.02)          # RM3100 at the boom tip
        ib = v(19.6, 0.6, 44.0, 0.8 * tx, 0.02)          # RM3100 at mid-boom
        bd = v(20.1, 0.2, 46.0, 3.0 * tx, 0.3)           # BMM150 on the bus (noisier)
        p = struct.pack(">BB", 4, 0x0E) + struct.pack(">iiiH", *ob) + \
            struct.pack(">iiiH", *ib) + struct.pack(">iiiH", *bd) + struct.pack(">H", 200)
        self._emit(0x020, p, 1, now)

    def _att(self, now):
        g = self.rng.gauss
        # Slow yaw drift so both attitude solutions move; TRIAD tracks the fusion.
        yaw = 0.05 * math.sin(now / 600)
        qw, qz = math.cos(yaw / 2), math.sin(yaw / 2)
        q = [int(qw * 16384), 0, 0, int(qz * 16384)]
        qt = [int((qw + g(0, 0.002)) * 16384), int(g(0, 30)), int(g(0, 30)), int((qz + g(0, 0.002)) * 16384)]
        p = struct.pack(">B3h3h3h4hH4h", 0x1F, int(g(0, 5)), int(g(0, 5)), 1000 + int(g(0, 3)),
                        int(g(0, 3)), int(g(0, 3)), int(g(0, 3)), 195, 5, 440, *q, 35, *qt)
        self._emit(0x021, p, 1, now)

    def _spec(self, now):
        nm = [410, 435, 460, 485, 510, 535, 560, 585, 610, 645, 680, 705, 730, 760, 810, 860, 900, 940]
        cal = [max(0.0, 40 * math.exp(-((w - 560) / 220) ** 2) + self.rng.gauss(0, 0.4)) for w in nm]
        raw = [min(65535, int(c * 120 + self.rng.gauss(0, 15))) for c in cal]
        p = struct.pack(">BBB", 1, 2, 50) + struct.pack(">18f", *cal) + struct.pack(">18H", *raw) + \
            struct.pack(">3b", 24, 25, 25)
        self._emit(0x022, p, 1, now)

    def _env(self, now):
        g = self.rng.gauss
        p = struct.pack(">HhHfffHffIhHHHIHIB", 0x7F, int(2210 + g(0, 5)), int(4600 + g(0, 20)), 1.4, 0.21,
                        520.0, 18, 6.0, 21.0, int(101310 + g(0, 10)), int(2240 + g(0, 5)), 4550, 42, 40, 560,
                        80, 152000, 3)
        self._emit(0x023, p, 1, now)
