"""DSS-Q Ground Data System — main process.

Data flow (one thread owns all state; web/front-panel threads only read
snapshots or enqueue operator requests under a lock):

  E22 USB serial ──► FrameSynchronizer ──► CADU ──► derandomize + RS(255,223)
     ▲                                              │
     │ CLTUs (uplink window opens after every       ▼
     │ received CADU)                          TM frame (FECF, CLCW ─► FOP-1)
     │                                              │ per-VC
  Fop1 ◄── operator (web / CardKB front panel)      ▼
                                              PacketExtractor ─► Space Packets
                                                    │
         SCLK correlation ◄─ TIMECORR               ▼
                     Decommutator ─► limits ─► anomaly detector ─► archive / UDP / displays

Run:
    python -m dssq.gds --config config/station.toml            # real radio
    python -m dssq.gds --sim                                     # software spacecraft
"""
from __future__ import annotations

import argparse
import logging
import math
import signal
import socket
import threading
import time
from collections import deque
from pathlib import Path

from .archive import Archive
from .ccsds import TM_DATA_LEN, VC_NAMES
from .ccsds.packets import PacketExtractor
from .ccsds.sdls import SdlsSender, load_key
from .ccsds.tc import build_cltu, build_tc_frame, parse_tc_frame
from .ccsds.timecode import CucTime, SclkCorrelator
from .ccsds.tm import decode_codeblock
from .cmdparse import CommandError, help_text, parse
from .config import load_config
from .decom import Decommutator
from .dictionary import (AIR_RATES_BPS, APID_EVR, CMD_STAGES, FARM_STATES, FDIR_BITS, MODES,
                         RADIO_FAULTS, RESET_CAUSES, SENSOR_BITS)
from .fop import Fop1
from .framesync import RADIO_FAULTS, FrameSynchronizer, NoiseReply, RadioFault, RawCadu
from .linkmgr import LinkManager
from .radio.e22 import NOISE_QUERY
from .rxstats import ReceiverStats, Window

log = logging.getLogger("dssq")


class AnomalyDetector:
    """EWMA mean/variance per numeric parameter; flags |z| > threshold after warm-up.
    Complements static limits: it catches 'unusual for this spacecraft' changes."""

    def __init__(self, alpha: float = 0.05, z: float = 6.0, warmup: int = 30):
        self.alpha, self.z, self.warmup = alpha, z, warmup
        self.s: dict[str, list] = {}       # name -> [n, mean, var]
        self.recent: deque = deque(maxlen=50)

    def update(self, t: float, name: str, v: float):
        st = self.s.setdefault(name, [0, v, 0.0])
        n, m, var = st
        if n >= self.warmup and var > 0:
            zz = (v - m) / math.sqrt(var)
            if abs(zz) > self.z:
                self.recent.append({"t": t, "param": name, "value": v, "mean": m,
                                    "sigma": math.sqrt(var), "z": zz})
        d = v - m
        m += self.alpha * d
        var = (1 - self.alpha) * (var + self.alpha * d * d)
        self.s[name] = [n + 1, m, var]


class GroundStation:
    def __init__(self, cfg: dict, archive: bool = True):
        self.cfg = cfg
        self.lock = threading.RLock()
        self.t_start = time.time()
        g = cfg["gds"]
        self.archive = Archive(g["archive_dir"], enabled=archive)
        self.sync = FrameSynchronizer(rssi_byte=bool(cfg["radio"]["rssi_byte"]))
        self.rx = ReceiverStats(cfg)
        self.extract = {vc: PacketExtractor(vc) for vc in (0, 1, 2)}
        self.decom = Decommutator()
        self.corr = SclkCorrelator()
        sd = cfg.get("sdls", {})
        key = load_key(sd.get("key_file")) if sd.get("enabled") else None
        if sd.get("enabled") and not key:
            log.warning("SDLS enabled but no key file found - uplink will be UNAUTHENTICATED")
        self.sdls = SdlsSender(key, int(sd.get("spi", 1)),
                               Path(g["archive_dir"]) / "sdls_sn.txt" if archive else None)
        c = cfg["commanding"]
        self.fop = Fop1(c["t1_s"], c["tx_limit"], c["window"], c["verify_timeout_s"], sdls=self.sdls)
        self.anomaly = AnomalyDetector()
        self.tlm: dict[str, dict] = {}
        self.apid_stats: dict[int, dict] = {}
        self.evrs: deque = deque(maxlen=200)
        self.events: deque = deque(maxlen=400)
        self.ert_by_mcfc: dict[int, tuple[float, float]] = {}
        self.pending_confirm: dict[int, dict] = {}
        self._confirm_ids = 1
        self.console_lines: deque = deque(maxlen=30)
        # Station I/O MCU (CardKB keyboards, LEDs, RCU): filled by display/frontpanel.py
        self.station_io: dict = {"connected_t": None, "fw": "", "rcu": False, "kb1": False,
                                 "kb2": False, "input": ""}
        self.outbox: deque = deque()          # bytes to write to the radio
        self.external_tc: deque = deque()     # raw TC frames from an external MCS (UDP)
        self.last_noise_query = 0.0
        self.noise_wait_until = 0.0
        self.owlt_s = (cfg["spacecraft"].get("distance_km", 0) or 0) / 299_792.458
        self.partition = 0
        self.last_sclk: float | None = None
        self.alarm = "NONE"
        self.radio_mgr = None                 # RadioManager, attached by main() when an RCU exists
        self.radio_jobs: deque = deque()      # procedures run by the radio thread
        self.ground_radio: dict | None = None
        self.ground_radio_fault: dict | None = None   # last E22 abnormal-status report
        lm = cfg.get("linkmgr", {})
        self.link_mgr = LinkManager(float(lm.get("target_margin_db", 6.0)), float(lm.get("period_s", 60)),
                                    float(lm.get("holdoff_s", 600)), int(lm.get("min_rate", 2)),
                                    int(lm.get("max_rate", 4)))
        self.link_mgr.mode = str(lm.get("mode", "ADVISE")).upper()
        self.link_change: dict | None = None
        self.allowed_channels = list(cfg["radio"].get("allowed_channels", list(range(20, 25))))
        self._udp = None
        self._fwd_frames = self._parse_addr(cfg.get("interop", {}).get("tm_frames_udp", ""))
        self._fwd_packets = self._parse_addr(cfg.get("interop", {}).get("tm_packets_udp", ""))
        if self._fwd_frames or self._fwd_packets:
            self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.event("INFO", "GDS", f"DSS-Q ground station started, SDLS "
                   f"{'ENABLED (SPI %d)' % self.sdls.spi if self.sdls.enabled else 'OFF (unauthenticated uplink)'}")

    @staticmethod
    def _parse_addr(s: str):
        if not s:
            return None
        host, port = s.rsplit(":", 1)
        return host, int(port)

    # ------------------------------------------------------------------ events
    def event(self, level: str, source: str, text: str, t: float | None = None):
        t = t or time.time()
        line = self.archive.event(t, level, source, text)
        self.events.append({"t": t, "level": level, "source": source, "text": text})
        log.log(logging.WARNING if level in ("WARN", "ERROR") else logging.INFO, line)

    # ------------------------------------------------------------- byte input
    def ingest(self, data: bytes, now: float | None = None):
        now = time.time() if now is None else now
        with self.lock:
            if data:
                self.rx.on_serial(len(data), now)
            if self.sync.expect_noise_reply and now > self.noise_wait_until:
                self.sync.expect_noise_reply = False
            for item in self.sync.feed(data, now):
                if isinstance(item, NoiseReply):
                    self.rx.on_noise(item.noise_dbm + self.rx.rssi_cal_db, item.ert)
                elif isinstance(item, RadioFault):
                    self._on_radio_fault(item)
                elif isinstance(item, RawCadu):
                    self._on_cadu(item)
            self._housekeeping(now)

    def _on_radio_fault(self, f: RadioFault):
        # The station E22 repeats the report every 500 ms while the fault lasts:
        # log the first one and then at most once a minute.
        last = self.ground_radio_fault
        if not last or last["code"] != f.code or f.ert - last["logged"] > 60:
            self.event("ERROR", "RADIO", f"ground E22 reports {RADIO_FAULTS[f.code]} - its transmitter "
                       "is disabled until the condition clears (check the station supply / cooling)", f.ert)
            self.ground_radio_fault = {"code": f.code, "text": RADIO_FAULTS[f.code], "logged": f.ert, "t": f.ert}
        else:
            last["t"] = f.ert

    def _on_cadu(self, rc: RawCadu):
        now = rc.ert
        res = decode_codeblock(rc.cadu[4:])
        self.archive.raw_cadu(rc.ert, rc.rssi_dbm, rc.cadu)
        self.rx.on_codeblock(now, res.rs_corrected, rc.asm_errors, res.ok, res.reason)
        if not res.ok:
            self.archive.frame(ert=now, rs=res.rs_corrected, rssi=rc.rssi_dbm, ok=0, reason=res.reason)
            if res.rs_corrected < 0:
                self.event("WARN", "DECODER", "RS uncorrectable codeblock", now)
            return
        f = res.frame
        self.ert_by_mcfc[f.mcfc] = (now, rc.rssi_dbm or 0)
        rssi = None if rc.rssi_dbm is None else rc.rssi_dbm + self.rx.rssi_cal_db   # calibrated
        data_octets = 0
        pkts = []
        if f.vcid in self.extract:
            pkts = self.extract[f.vcid].push(f.vcfc, f.fhp, f.data)
            data_octets = sum(len(p.raw) for p in pkts)
        self.rx.on_frame(now, rssi, f.mcfc, f.is_oid, data_octets)
        snr = None if rssi is None or self.rx.noise_dbm is None else rssi - self.rx.noise_dbm
        self.archive.frame(ert=now, mcfc=f.mcfc, vcid=f.vcid, vcfc=f.vcfc, fhp=f.fhp,
                           rs=res.rs_corrected, rssi=rc.rssi_dbm, snr=snr, ok=1)
        if self._fwd_frames:
            self._udp.sendto(f.raw, self._fwd_frames)
        if f.clcw:
            self.fop.on_clcw(f.clcw, now)
        for p in pkts:
            self._on_packet(p, f.vcid, now)
        # The spacecraft has just finished transmitting: uplink window is open.
        self._uplink_window(now)

    def _on_packet(self, p, vcid: int, now: float):
        if self._fwd_packets:
            self._udp.sendto(p.raw, self._fwd_packets)
        st = self.apid_stats.setdefault(p.apid, {"count": 0, "seq_gaps": 0, "lost": 0,
                                                 "last_seq": None, "last_ert": None,
                                                 "latency": Window(600), "size": len(p.raw),
                                                 "vcid": vcid})
        if st["last_seq"] is not None:
            gap = (p.seq_count - st["last_seq"] - 1) & 0x3FFF
            if gap and vcid != 2:            # playback re-sends old sequence numbers
                st["seq_gaps"] += 1
                st["lost"] += gap
        st["last_seq"], st["last_ert"], st["count"] = p.seq_count, now, st["count"] + 1
        sclk = p.time.seconds if p.time else None
        scet = self.corr.scet(sclk) if sclk is not None else None
        if sclk is not None and vcid != 2:
            self.last_sclk = sclk
            if scet:
                st["latency"].add(now, now - scet)
        name = self.decom.packet_name(p.apid)
        try:
            values = self.decom.decode(p)
        except (KeyError, ValueError) as e:
            self.event("WARN", "DECOM", f"{name}: {e}", now)
            return
        if p.apid == APID_EVR:
            self.evrs.append({"ert": now, "scet": scet, "sclk": sclk, **values})
            self.event({"DIAG": "INFO", "INFO": "INFO", "WARN": "WARN"}.get(values["severity"], "ERROR"),
                       "S/C EVR", f"[{values['event_id']:#06x}] {values['text']}", now)
            self.archive.packet(now, scet, p.apid, name, p.seq_count, sclk, self.partition, vcid,
                                p.raw, {}, {})
            return
        limits = self.decom.limits(p.apid, values)
        for k, state in limits.items():
            prev = self.tlm.get(name, {}).get("limits", {}).get(k, "OK")
            if state != prev and vcid != 2:
                self.event("WARN" if state != "OK" else "INFO", "LIMITS",
                           f"{name}.{k} = {values.get(k)} {state}", now)
        self.tlm[name] = {"apid": p.apid, "values": values, "limits": limits, "ert": now,
                          "scet": scet, "sclk": sclk, "seq": p.seq_count, "vcid": vcid,
                          "units": self.decom.units(p.apid)}
        if vcid != 2:
            for k, v in values.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    self.anomaly.update(now, f"{name}.{k}", float(v))
        self.archive.packet(now, scet, p.apid, name, p.seq_count, sclk, self.partition, vcid,
                            p.raw, values, limits)
        if name == "HK":
            self.partition = values.get("sclk_partition", 0)
        elif name == "RF":
            self.rx.sc_rf = values
        elif name == "CMDVER":
            r = self.fop.on_cmdver(values, now)
            lc = self.link_change
            if r and lc and lc.get("cmd_id") == r.id and lc["state"] == "WAIT_EXEC":
                if r.state == "EXECUTED":
                    lc.update(state="WAIT_SWITCH", t=now, frames_at=self.rx.frames_ok)
                else:
                    self.event("WARN", "LINK", f"coordinated {lc['kind']} change rejected by spacecraft")
                    self.link_change = None
            if r:
                self.archive.command(r.summary())
                self.console(f"#{r.id} {r.cmd.text}: {r.state} {r.detail}")
                self.event("INFO" if r.state == "EXECUTED" else "WARN", "CMD",
                           f"#{r.id} {r.cmd.text} -> {r.state} ({r.detail})", now)
        elif name == "TIMECORR":
            self._time_correlate(values, now)

    def _time_correlate(self, v: dict, now: float):
        ref = self.ert_by_mcfc.get(v["ref_mcfc"])
        if not ref:
            return
        ert_end = ref[0]
        uart_s = 237 * 10 / 9600.0      # ground E22 -> USB output time of one CADU + RSSI
        airtime = (v["ref_airtime_ms"] or 0) / 1000.0
        scet = ert_end - uart_s - airtime - self.owlt_s - float(self.cfg["radio"].get("radio_latency_s", 0))
        sclk = v["sclk_coarse"] + v["sclk_fine"] / 65536.0
        self.corr.add(self.partition, sclk, scet)

    # ----------------------------------------------------------------- uplink
    def _uplink_window(self, now: float):
        sent = 0
        while self.external_tc and sent < 2:
            raw = self.external_tc.popleft()
            info = parse_tc_frame(raw)
            if info is None:
                self.event("WARN", "INTEROP", "external TC frame rejected (bad header/FECF)", now)
                continue
            frame = build_tc_frame(info.data, info.seq, info.bypass, info.control, info.vcid,
                                   sdls=self.sdls)
            self._radiate(build_cltu(frame), now, "external")
            sent += 1
        for cltu in self.fop.next_cltus(now):
            self._radiate(cltu, now, "FOP")
            sent += 1
        for r in self.fop.all[-12:]:
            if r.history and r.history[-1][0] == now and r.state == "RADIATED":
                self.console(f"#{r.id} {r.cmd.text}: RADIATED ({r.detail})")
        for t, text in self.fop.events:
            self.event("WARN", "FOP", text, t)
        self.fop.events.clear()
        if not sent and now - self.last_noise_query > float(self.cfg["radio"].get("noise_poll_s", 20)) \
                and self.cfg["radio"].get("rssi_noise", True):
            self.last_noise_query = now
            self.noise_wait_until = now + 1.0
            self.sync.expect_noise_reply = True
            self.rx.noise_queries += 1
            self.outbox.append(NOISE_QUERY)

    def _radiate(self, cltu: bytes, now: float, source: str):
        self.outbox.append(cltu)
        self.rx.on_uplink(len(cltu), now)

    # ---------------------------------------------------------------- operator
    def console(self, text: str):
        self.console_lines.append((time.time(), text))

    def submit_command(self, text: str, source: str = "web") -> dict:
        with self.lock:
            t = text.strip()
            if not t:
                return {"status": "ERROR", "message": "empty"}
            head = t.split()[0].upper()
            if head in ("GSCAN", "WAKE", "LINKRATE", "LINKCHAN", "LINKMGR", "RADIO"):
                return self._ground_directive(t.split(), source)
            if t.upper().startswith("HELP"):
                parts = t.split()
                lines = help_text(parts[1] if len(parts) > 1 else None)
                for ln in lines:
                    self.console(ln)
                return {"status": "HELP", "lines": lines}
            try:
                cmd = parse(t)
            except CommandError as e:
                self.console(f"REJECTED: {e}")
                return {"status": "REJECTED", "message": str(e)}
            if cmd.hazardous:
                cid = self._confirm_ids
                self._confirm_ids += 1
                need_auth = bool(self.cfg["commanding"].get("require_two_person_for_hazardous"))
                self.pending_confirm[cid] = {"cmd": cmd, "text": cmd.text, "source": source, "t": time.time(),
                                             "confirmed": False, "authorized": not need_auth}
                msg = f"HAZARDOUS {cmd.text}: confirm id {cid}" + (" + 2nd-operator AUTH" if need_auth else "")
                self.console(msg)
                self.event("WARN", "CMD", f"{msg} (from {source})")
                return {"status": "CONFIRM", "id": cid, "message": msg, "needs_auth": need_auth}
            return self._queue(cmd, source)

    # ------------------------------------------------ ground-station directives
    def _ground_directive(self, toks: list[str], source: str) -> dict:
        d = toks[0].upper()
        if d == "LINKMGR":
            if len(toks) != 2 or toks[1].upper() not in ("OFF", "ADVISE", "AUTO"):
                return {"status": "REJECTED", "message": "usage: LINKMGR OFF|ADVISE|AUTO"}
            self.link_mgr.mode = toks[1].upper()
            self.event("INFO", "LINK", f"link manager -> {self.link_mgr.mode} ({source})")
            return {"status": "OK", "message": f"link manager {self.link_mgr.mode}"}
        if not (self.radio_mgr and self.radio_mgr.available):
            return {"status": "REJECTED", "message": f"{d} needs the Radio Control Unit (VENTUNO Q MCU wired to the E22 M0/M1/AUX, [gds] rcu = true)"}
        if d == "RADIO":
            self.radio_jobs.append(("RADIO", None))
        elif d == "GSCAN":
            first, last = (int(toks[1]), int(toks[2])) if len(toks) == 3 else (0, 83)
            if not 0 <= first <= last <= 83:
                return {"status": "REJECTED", "message": "channels 0..83"}
            self.radio_jobs.append(("GSCAN", (first, last)))
        elif d == "WAKE":
            self.radio_jobs.append(("WAKE", None))
        elif d in ("LINKRATE", "LINKCHAN"):
            if len(toks) != 2:
                return {"status": "REJECTED", "message": f"usage: {d} <value>"}
            v = int(toks[1], 0)
            if d == "LINKCHAN" and v not in self.allowed_channels:
                return {"status": "REJECTED", "message": f"CH{v} not in allowed_channels {self.allowed_channels}"}
            if d == "LINKRATE" and not 0 <= v <= 7:
                return {"status": "REJECTED", "message": "air-rate code 0..7"}
            kind = "rate" if d == "LINKRATE" else "chan"
            cid = self._confirm_ids
            self._confirm_ids += 1
            need_auth = bool(self.cfg["commanding"].get("require_two_person_for_hazardous"))
            self.pending_confirm[cid] = {"cmd": None, "directive": (kind, v), "text": f"{d} {v}",
                                         "source": source, "t": time.time(), "confirmed": False,
                                         "authorized": not need_auth}
            msg = f"HAZARDOUS {d} {v} (changes BOTH ends of the link): confirm id {cid}"
            self.console(msg)
            return {"status": "CONFIRM", "id": cid, "message": msg, "needs_auth": need_auth}
        self.console(f"{d}: scheduled (runs in the next quiet window)")
        return {"status": "QUEUED", "message": f"{d} scheduled"}

    def start_link_change(self, kind: str, value: int, source: str) -> dict:
        if self.link_change:
            return {"status": "REJECTED", "message": "a coordinated change is already in progress"}
        revert_s = int(self.cfg.get("linkmgr", {}).get("revert_s", 180))
        text = f"{'AIRRATE' if kind == 'rate' else 'CHANNEL'} {value} {revert_s}"
        cmd = parse(text)
        res = self._queue(cmd, source + "/link")
        self.link_change = {"kind": kind, "value": value, "cmd_id": res["id"], "state": "WAIT_EXEC",
                            "t": time.time(), "revert_s": revert_s,
                            "old": self.cfg["radio"]["air_rate"] if kind == "rate" else self.cfg["radio"]["channel"]}
        self.event("INFO", "LINK", f"coordinated {kind} change to {value} started ({source})")
        return {"status": "QUEUED", "id": res["id"], "message": f"coordinated change {text}"}

    def run_radio_jobs(self):
        """Executed by the radio thread between reads (procedures own the radio briefly)."""
        while self.radio_jobs:
            job, arg = self.radio_jobs.popleft()
            rm = self.radio_mgr
            if job == "RADIO" or job == "VERIFY":
                cfg = rm.read_config()
                with self.lock:
                    self.ground_radio = cfg
                    if cfg is None:
                        self.event("ERROR", "RADIO", "ground E22 did not answer in CONFIG mode")
                    else:
                        r = self.cfg["radio"]
                        bad = [k for k, want in (("channel", r["channel"]), ("air_rate_code", r["air_rate"]),
                                                 ("rssi_byte", bool(r["rssi_byte"])),
                                                 ("rssi_noise_enable", bool(r["rssi_noise"]))) if cfg[k] != want]
                        self.event("WARN" if bad else "INFO", "RADIO",
                                   f"ground E22: ch {cfg['channel']} ({cfg['freq_mhz']:.3f} MHz) air "
                                   f"{cfg['air_rate']} pwr {cfg['power_code']} LBT {cfg['lbt']}"
                                   + (f" MISMATCH {bad}" if bad else " - matches station.toml"))
            elif job == "GSCAN":
                res = rm.survey(*arg)
                with self.lock:
                    if res:
                        q = res["quietest"]
                        allowed = [(n, arg[0] + i) for i, n in enumerate(res["noise"])
                                   if n is not None and arg[0] + i in self.allowed_channels]
                        res["quietest_allowed"] = min(allowed)[1] if allowed else None
                        self.event("INFO", "RADIO", f"ground RF survey CH{arg[0]}-{arg[1]} in "
                                   f"{res['duration_s']:.1f} s: quietest CH{q}, quietest allowed "
                                   f"CH{res['quietest_allowed']}")
            elif job == "WAKE":
                cmd = parse("BD MODE SAFE")
                from .cmdparse import tc_packet
                frame = build_tc_frame(tc_packet(cmd, 0), seq=0, bypass=True, sdls=self.sdls)
                ok = rm.wor_transmit(build_cltu(frame))
                with self.lock:
                    self.event("INFO" if ok else "ERROR", "RADIO",
                               "WOR wake-up transmitted (BD MODE SAFE)" if ok else "WOR wake-up failed")
            elif job == "SETRATE":
                ok = rm.set_air_rate(arg)
                with self.lock:
                    if ok:
                        self.cfg["radio"]["air_rate"] = arg
                        self.rx.air_rate_code = arg
                    self.event("INFO" if ok else "ERROR", "RADIO", f"ground air rate -> code {arg}: {ok}")
            elif job == "SETCHAN":
                ok = rm.set_channel(arg)
                with self.lock:
                    if ok:
                        self.cfg["radio"]["channel"] = arg
                        self.rx.freq_mhz = 410.125 + arg
                    self.event("INFO" if ok else "ERROR", "RADIO", f"ground channel -> {arg}: {ok}")

    def _link_change_tick(self, now: float):
        lc = self.link_change
        if not lc:
            return
        period = 4.0
        rf = self.tlm.get("RF", {}).get("values", {})
        if rf.get("frame_period_ms"):
            period = rf["frame_period_ms"] / 1000.0
        if lc["state"] == "WAIT_EXEC" and now - lc["t"] > 600:
            self.event("WARN", "LINK", "coordinated change: no command verification, abandoned")
            self.link_change = None
        elif lc["state"] == "WAIT_SWITCH" and (self.rx.frames_ok > lc["frames_at"] or
                                               now - lc["t"] > 1.5 * period):
            self.radio_jobs.append(("SETRATE" if lc["kind"] == "rate" else "SETCHAN", lc["value"]))
            self._queue(parse("NOOP"), "link-confirm")       # proves the new link -> cancels revert
            lc.update(state="SWITCHED", t=now, frames_at=self.rx.frames_ok)
        elif lc["state"] == "SWITCHED":
            if self.rx.frames_ok > lc["frames_at"]:
                self.event("INFO", "LINK", f"coordinated {lc['kind']} change to {lc['value']} COMPLETE")
                self.link_change = None
            elif now - lc["t"] > lc["revert_s"] + 30:
                self.radio_jobs.append(("SETRATE" if lc["kind"] == "rate" else "SETCHAN", lc["old"]))
                self.event("WARN", "LINK", f"no frames after {lc['kind']} change - ground reverting to {lc['old']}")
                self.link_change = None

    def confirm(self, cid: int, role: str = "CONFIRM") -> dict:
        with self.lock:
            p = self.pending_confirm.get(cid)
            if not p:
                return {"status": "ERROR", "message": f"no pending confirmation {cid}"}
            if time.time() - p["t"] > 120:
                del self.pending_confirm[cid]
                return {"status": "ERROR", "message": "confirmation expired (120 s)"}
            if role == "AUTH":
                p["authorized"] = True
            else:
                p["confirmed"] = True
            if p["confirmed"] and p["authorized"]:
                del self.pending_confirm[cid]
                if p.get("directive"):
                    return self.start_link_change(*p["directive"], p["source"])
                return self._queue(p["cmd"], p["source"] + f"+{role.lower()}")
            return {"status": "WAITING", "message": "waiting for " +
                    ("2nd-operator AUTH" if not p["authorized"] else "CONFIRM")}

    def cancel(self, cid: int):
        with self.lock:
            self.pending_confirm.pop(cid, None)

    def _queue(self, cmd, source):
        r = self.fop.submit(cmd)
        self.console(f"#{r.id} {cmd.text}: QUEUED ({cmd.kind})")
        self.event("INFO", "CMD", f"#{r.id} {cmd.text} queued by {source}")
        self.archive.command(r.summary())
        return {"status": "QUEUED", "id": r.id, "text": cmd.text}

    # ------------------------------------------------------------- periodic
    def _housekeeping(self, now: float):
        self.rx.tick(now, float(self.cfg["gds"]["los_timeout_s"]))
        for t, e in self.rx.events:
            self.event("INFO" if e == "AOS" else "WARN", "RX", e, t)
        self.rx.events.clear()
        self._link_change_tick(now)
        rec = self.link_mgr.evaluate(now, self.rx.snapshot(now), int(self.cfg["radio"]["air_rate"]),
                                     self.rx.sc_rf.get("tx_power_code"))
        if rec:
            self.event("INFO", "LINK", f"link manager AUTO: {rec['action']} {rec.get('value')} - {rec['why']}")
            if rec["action"] == "LINKRATE" and self.radio_mgr and self.radio_mgr.available:
                self.start_link_change("rate", rec["value"], "linkmgr")
        # alarm level
        red = any(s == "RED" for p in self.tlm.values() for s in p["limits"].values())
        yellow = any(s == "YELLOW" for p in self.tlm.values() for s in p["limits"].values())
        fdir = self.tlm.get("HK", {}).get("values", {}).get("fdir_flags") or 0
        self.alarm = "RED" if red or (self.rx.los and self.rx.passes) else \
            ("YELLOW" if yellow or fdir else "NONE")

    # -------------------------------------------------------------- snapshot
    def snapshot(self) -> dict:
        with self.lock:
            now = time.time()
            hk = self.tlm.get("HK", {}).get("values", {})
            fdir = hk.get("fdir_flags") or 0
            sns = hk.get("sensor_health") or 0
            return {
                "utc": now,
                "station": self.cfg["station"]["name"],
                "spacecraft": self.cfg["spacecraft"]["name"],
                "uptime_s": now - self.t_start,
                "alarm": self.alarm,
                "rx": self.rx.snapshot(now),
                "sync": {"state": self.sync.state, **self.sync.stats},
                "vc": {VC_NAMES.get(vc, str(vc)): e.stats for vc, e in self.extract.items()},
                "apids": {self.decom.packet_name(a): {
                    "apid": a, "count": s["count"], "seq_gaps": s["seq_gaps"], "lost": s["lost"],
                    "last_seq": s["last_seq"], "age_s": None if s["last_ert"] is None else now - s["last_ert"],
                    "latency": s["latency"].stats(now), "size": s["size"], "vcid": s["vcid"]}
                    for a, s in sorted(self.apid_stats.items())},
                "tlm": self.tlm,
                "decoded": {"mode": MODES.get(hk.get("fsw_mode")),
                            "fdir": [n for i, n in enumerate(FDIR_BITS) if fdir >> i & 1],
                            "sensors_ok": [n for i, n in enumerate(SENSOR_BITS) if sns >> i & 1],
                            "sensors_missing": [n for i, n in enumerate(SENSOR_BITS) if not sns >> i & 1],
                            "air_rate_bps": AIR_RATES_BPS.get(self.rx.sc_rf.get("air_rate_code")),
                            "reset_cause": RESET_CAUSES.get(hk.get("reset_cause")),
                            "farm_state": FARM_STATES.get(hk.get("farm_state")),
                            "radio_fault": RADIO_FAULTS.get(hk.get("radio_fault"))},
                "time": {"partition": self.partition, "last_sclk": self.last_sclk,
                         "scet_of_last_sclk": self.corr.scet(self.last_sclk) if self.last_sclk else None,
                         "corr_samples": len(self.corr.samples), "drift_ppm": self.corr.drift_ppm,
                         "residual_rms_ms": self.corr.residual_rms * 1000, "owlt_s": self.owlt_s},
                "cop": {**self.fop.state(), "sdls": self.sdls.enabled, "sdls_sn": self.sdls.sn},
                "radio": {"rcu": bool(self.radio_mgr and self.radio_mgr.available),
                          "ground_config": self.ground_radio,
                          "ground_fault": (self.ground_radio_fault
                                           if self.ground_radio_fault and now - self.ground_radio_fault["t"] < 5
                                           else None),
                          "last_scan": self.radio_mgr.last_scan if self.radio_mgr else None,
                          "allowed_channels": self.allowed_channels,
                          "configured": {k: self.cfg["radio"][k] for k in ("channel", "air_rate", "power", "lbt")},
                          "link_change": self.link_change, "jobs_pending": len(self.radio_jobs)},
                "linkmgr": self.link_mgr.state(),
                "science_cfg": self.cfg.get("science", {"r_outboard_m": 1.0, "r_inboard_m": 0.5}),
                "pending_confirm": [{"id": k, "text": v["text"], "confirmed": v["confirmed"],
                                     "authorized": v["authorized"]} for k, v in self.pending_confirm.items()],
                "evrs": list(self.evrs)[-40:],
                "events": list(self.events)[-80:],
                "anomalies": list(self.anomaly.recent)[-20:],
                "console": list(self.console_lines),
                "station_io": {**self.station_io,
                               "connected": bool(self.station_io["connected_t"]
                                                 and now - self.station_io["connected_t"] < 12)},
                "cmd_stages": CMD_STAGES,
            }


# ======================================================================= runner
def _start_udp_tc_listener(gs: GroundStation, addr: str):
    host, port = addr.rsplit(":", 1)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind((host, int(port)))

    def run():
        while True:
            data, _ = s.recvfrom(2048)
            with gs.lock:
                gs.external_tc.append(data)
    threading.Thread(target=run, daemon=True, name="udp-tc").start()


def main(argv=None):
    ap = argparse.ArgumentParser(description="DSS-Q ground data system")
    ap.add_argument("--config", default=None)
    ap.add_argument("--port", help="override [radio].port")
    ap.add_argument("--sim", action="store_true", help="use the software spacecraft simulator")
    ap.add_argument("--sim-loss", type=float, default=0.05, help="simulated packet loss probability")
    ap.add_argument("--sim-errors", type=int, default=3, help="max simulated symbol errors per CADU")
    ap.add_argument("--no-archive", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config(a.config)
    if a.port:
        cfg["radio"]["port"] = a.port
    gs = GroundStation(cfg, archive=not a.no_archive)

    if a.sim:
        from .sim import SimSpacecraft
        radio = SimSpacecraft(cfg, loss=a.sim_loss, max_errors=a.sim_errors)
        gs.event("INFO", "GDS", "SIMULATION MODE - software spacecraft, no RF")
    else:
        from .radio.e22 import E22Serial
        radio = E22Serial(cfg["radio"]["port"])

    from .radioctl import RadioManager
    rcu = None
    if a.sim:
        class _SimRcu:
            def set_mode(self, m):
                return radio.rcu_set_mode(m)
        rcu = _SimRcu()
    from .display.web import start_web
    start_web(gs, cfg["gds"]["http_host"], int(cfg["gds"]["http_port"]))
    fp = None
    if cfg["gds"].get("frontpanel", False):
        from .display.frontpanel import FrontPanel
        fp = FrontPanel(gs, cfg["gds"].get("router_address") or None)
        fp.start()
        if cfg["gds"].get("rcu", False) and not a.sim:
            rcu = fp.rcu()
    gs.radio_mgr = RadioManager(radio, rcu) if rcu else None
    if gs.radio_mgr:
        gs.radio_jobs.append(("VERIFY", None))
    tc_listen = cfg.get("interop", {}).get("tc_frames_udp_listen")
    if tc_listen:
        _start_udp_tc_listener(gs, tc_listen)

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    last_console = 0.0
    while not stop.is_set():
        data = radio.read()
        gs.ingest(data)
        with gs.lock:
            out = list(gs.outbox)
            gs.outbox.clear()
        for chunk in out:
            radio.write(chunk)
        if gs.radio_jobs and gs.radio_mgr:
            gs.run_radio_jobs()
        now = time.time()
        if now - last_console > float(cfg["gds"]["console_interval_s"]):
            last_console = now
            s = gs.snapshot()
            rf = s["rx"]["rf"]
            fr = s["rx"]["frames"]
            log.info("STATUS %s sync=%s RSSI=%s dBm noise=%s dBm SNRest=%s dB frames=%d lost=%d "
                     "RScorr=%d alarm=%s", "LOS" if s["rx"]["state"]["los"] else "AOS", s["sync"]["state"],
                     rf["rssi_dbm"], rf["noise_dbm"],
                     None if rf["snr_est_db"] is None else round(rf["snr_est_db"], 1),
                     fr["ok"], fr["lost_mcfc"], s["rx"]["decoder"]["symbols_corrected"], s["alarm"])
    if fp:
        fp.stop()
    gs.archive.close()
    radio.close()


if __name__ == "__main__":
    main()
