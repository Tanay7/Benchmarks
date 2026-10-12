"""Software spacecraft + RF channel + ground radio simulator ("testbed"), v3 (Phase B PB-8.4a).

Lets the whole ground system (GDS, displays, commanding, link manager, AFC, TDM, CFDP,
sequences, MAGCAL, front panel) run with no hardware:

  GDS --write()--> [ground radio emulator] --RF--> [VGQ-1 spacecraft model]
  GDS <--read()/packets()-- [ground radio emulator] <--RF-- (CADUs every frame period)

Built only from the ground's reference code - no second implementation of any protocol:
  * Telemetry: HK / RF / TIMECORR / MAG / ATT / ENV through decom.encode (the inverse of the
    decommutator) with v3 values; SPEC v3.1 through spectral.pack_spec_v31; SPECBURST through
    spectral.pack_specburst (encoding chosen like the flight, so raw and CCSDS 121.0 packets
    both occur); EVR / CMDVER / RFSCAN in their packed forms; Space Packets through
    ccsds.packets.build_packet.
  * Downlink: VC0..3 + OID with the flight's Downlink rules (1024-octet VC buffers, the last
    256 octets reserved for priority packets, VC0 > VC1 > VC2 > VC3, VC1 first when it
    holds > 500 octets after a VC0 frame); TM (ccsds.tm.build_frame / build_oid_frame) or
    USLP (build_uslp_frame / build_uslp_oid_frame); SDLS TM through ccsds.sdls.SdlsTmSender
    (AE once the epoch is set; epoch 0 holds the data VCs - OID frames only - and after
    120 s simulated falls back to the null SA, as the flight); OID idle text
    "DE N0CALL VGQ-1 "; CADUs through ccsds.tm.encode_cadu.
  * Uplink: ccsds.tc.cltu_decode -> parse_tc_frame -> ccsds.sdls.SdlsReceiver ([sdls]
    tc_service, as the flight's compile-time VGQ_SDLS_TC; no key = NONE) -> tc_open_tfdf ->
    Farm1Model. Every command is first checked with cmdparse.check_user_data (the flight's
    parse_user_data rules, so a refused command gets the flight's CmdError in CMDVER).
    Simulated effects: NOOP, MODE, TXPWR, AIRRATE / CHANNEL (switch after 2 frames, revert
    without uplink), FPERIOD, PKTRATE, PLAYBACK, SSRCLEAR, PING, RSTCNT, REBOOT, TXINHIBIT,
    TIMECORR, RFSCAN, HIBERNATE / WAKE, SPECCFG, SPECMODE (BURST -> SPECBURST packets for n
    seconds), CFDPPUT 0 (the recorded packets of the in-memory SSR sent with ccsds.cfdp.Sender
    on VC3, sequence number cfdp_make_seq(partition, n)), CFDPPUT 1/2 (relay request: no EGSE
    in the simulator, so it times out after 30 s like the flight), CFDPCANCEL, SEQLOAD /
    SEQCTRL (stored, dispatched by simulated SCLK with CMDVER command source 2), MAGBIST (HK
    mag_bist), MAGCAL (HK magcal_state); the rest are accepted without effect.
  * MAG: a constant field [science] field_ut (50 uT when unset) seen through a KNOWN hard-iron
    offset and soft-iron matrix while the simulated attitude tumbles, so the MAGCAL fit can be
    compared with the truth (MAG_OFFSET_UT, MAG_SOFT_IRON).
  * Radio, E22 byte-stream mode: the v2 ground E22 emulator (read / write / rcu_set_mode,
    [CADU][RSSI] units, CONFIG-mode registers, C0 C1 C2 C3 noise query).
    Packet mode (SX126x): a link.RadioBackend - packets() returns RxPacket with RSSI (0.5 dB
    steps), SNR (0.25 dB) and a frequency error = static offset + slow Doppler ramp - the
    correction applied through rf_foff(hz) (returns 1); with probability crc_fail_prob a
    CADU arrives with crc_ok = False carrying <= 16 symbol errors, so RS rescues it.
  * RF channel: log-normal fading around rssi_mean; loss rises smoothly as the margin above
    the receiver sensitivity (dssq.link) shrinks; both ends only hear each other on the same
    channel and rate code.
Deterministic with `seed` and an injected `clock`.
"""
from __future__ import annotations

import math
import random
import struct
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from . import decom, link, spectral
from .ccsds import cfdp as cf
from .ccsds.packets import build_idle_packet, build_packet
from .ccsds.sdls import SERVICE_AE, SERVICE_NONE, SdlsReceiver, SdlsTmSender, parse_service
from .ccsds.tc import Farm1Model, TcFrameInfo, cltu_decode, parse_tc_frame, tc_open_tfdf
from .ccsds.timecode import CucTime
from .ccsds.tm import (Clcw, build_frame, build_oid_frame, build_uslp_frame, build_uslp_oid_frame, data_field_len,
                       encode_cadu)
from .cmdparse import check_user_data, unpack_seqload
from .dictionary import (APID_ATT, APID_CFDP, APID_CMDVER, APID_ENV, APID_EVR, APID_HK, APID_MAG, APID_RF,
                         APID_RFSCAN, APID_SPEC, APID_SPECBURST, APID_TIMECORR, CMD_SOURCE_AD, CMD_SOURCE_BD,
                         CMD_SOURCE_STORED, FDIR_CLEARED_BY_RSTCNT, FDIR_HIBERNATE, PERIODIC_APIDS)
from .radio.e22 import regs_from_cfg

# Flight datalink.cpp kModeCadence (s, 0 = off), PeriodicIdx order HK RF TIMECORR MAG ATT SPEC ENV.
CADENCE = {0: (10, 10, 0, 0, 0, 0, 0), 1: (30, 60, 300, 0, 0, 0, 0), 2: (60, 60, 300, 30, 60, 300, 120),
           3: (20, 30, 120, 4, 8, 30, 30), 4: (10, 10, 60, 4, 8, 30, 30)}
PERIOD_X10 = {0: 40, 1: 40, 2: 25, 3: 20, 4: 20}       # flight kModePeriodX10
MIN_FRAME_PERIOD_S = 1.5                                # flight kMinFramePeriodMs
GROUND_UART_BAUD = 9600                                 # ground E22 -> USB (config.E22_UART_CODE_9600)
MAX_TX_DUTY = 0.5                                       # flight kMaxTxDutyPermille / 1000
DUTY_CYCLES = 16                                        # flight TxDutyMeter::kCycles
DUTY_WINDOW_S = 300.0                                   # flight TxDutyMeter::kWindowMs / 1000
VC_BUF_LEN = 1024                                       # flight VcStream::kBufLen
VC_RESERVE = 256                                        # flight kVcReserveOctets
SCI_PRIORITY_BACKLOG = 500                              # flight kSciPriorityBacklog
CFDP_VC3_BACKLOG_MAX = 320                              # flight kCfdpVc3BacklogMax
SSR_MAX_BYTES = 262_144                                 # flight kSsrMaxBytes
SDLS_EPOCH_WAIT_S = 120.0                               # flight kSdlsEpochWaitS
RELAY_START_TIMEOUT_S = 30.0                            # flight kRelayStartTimeoutMs
SEQ_LATE_MAX_S = 300                                    # flight kSeqLateMaxS
SEQ_SLOTS = 32
CALLSIGN = "N0CALL"                                     # flight VGQ_CALLSIGN default
IDLE_TEXT = f"DE {CALLSIGN} VGQ-1 ".encode("ascii")
MODE_NAMES = ("BOOT", "SAFE", "CRUISE", "ENCOUNTER", "TEST")
BEACON_NOMINAL, BEACON_INTERESTING, BEACON_IMPORTANT, BEACON_URGENT = 0, 1, 2, 3
SENSORS_ALL = 0x03FF                                    # every SENSOR_BITS instrument healthy
RS_RESCUE_ERRORS = (4, 12)                              # symbol errors on a CRC-failed CADU (<= 16)
NOISE_FLOOR_NF_DB = link.SX126X_NF_DB
DEFAULT_FIELD_UT = 50.0
# MAGCAL truth: B_raw = S_inv (R^T B_world) + offset  (dssq.magcal model, 24.6)
MAG_OFFSET_UT = {0: (12.0, -7.5, 4.0), 1: (-3.0, 9.0, -6.0)}
MAG_SOFT_IRON = {0: ((1.06, 0.03, -0.02), (0.03, 0.95, 0.04), (-0.02, 0.04, 1.02)),
                 1: ((0.97, -0.02, 0.01), (-0.02, 1.04, 0.02), (0.01, 0.02, 0.99))}
MAG_FIELD_DIR = (0.40, 0.10, 0.911)
TUMBLE_RAD_S = (0.11, 0.073, 0.051)                     # incommensurate body rates (full sphere)
SPEC_TEMPLATE_UW = [max(0.05, 40.0 * math.exp(-((nm - 560.0) / 220.0) ** 2)) for nm in spectral.WAVELENGTHS_NM]


def _qmul(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, float, float, float]:
    """Hamilton product a (x) b, scalar first."""
    return (a[0] * b[0] - a[1] * b[1] - a[2] * b[2] - a[3] * b[3],
            a[0] * b[1] + a[1] * b[0] + a[2] * b[3] - a[3] * b[2],
            a[0] * b[2] - a[1] * b[3] + a[2] * b[0] + a[3] * b[1],
            a[0] * b[3] + a[1] * b[2] - a[2] * b[1] + a[3] * b[0])


def _rot(t: float) -> tuple[tuple[float, float, float], ...]:
    """Body-to-world rotation of the tumbling spacecraft at time t (ZYX Euler, rad)."""
    yaw, pitch, roll = (w * t for w in TUMBLE_RAD_S)
    cy, sy, cp, sp, cr, sr = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch), math.cos(roll), \
        math.sin(roll)
    return ((cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr),
            (sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr),
            (-sp, cp * sr, cp * cr))


def _mtv(m: tuple[tuple[float, float, float], ...], v: tuple[float, ...]) -> tuple[float, float, float]:
    """m^T v."""
    return (m[0][0] * v[0] + m[1][0] * v[1] + m[2][0] * v[2], m[0][1] * v[0] + m[1][1] * v[1] + m[2][1] * v[2],
            m[0][2] * v[0] + m[1][2] * v[1] + m[2][2] * v[2])


def _mv(m: tuple[tuple[float, float, float], ...], v: tuple[float, ...]) -> tuple[float, float, float]:
    return (m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2], m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
            m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2])


# =============================================================================
# Downlink (Python port of the flight Downlink / VcStream rules)
# =============================================================================
class _VcStream:
    """One VC buffer: packets queued as a byte stream; frames take data_len octets each,
    padded with an idle packet when the buffer runs short (the idle packet may continue
    into the next frame of the VC, which the FHP handles)."""

    def __init__(self) -> None:
        self.buf = bytearray()
        self.starts: deque[int] = deque()
        self.base = 0                       # absolute index of buf[0]
        self.real_end = 0
        self.idle_seq = 0

    def backlog(self) -> int:
        return len(self.buf)

    def push(self, pkt: bytes, priority: bool, idle: bool = False) -> bool:
        limit = VC_BUF_LEN if priority or idle else VC_BUF_LEN - VC_RESERVE
        if not idle and len(self.buf) + len(pkt) > limit:
            return False
        self.starts.append(self.base + len(self.buf))
        self.buf += pkt
        if not idle:
            self.real_end = self.base + len(self.buf)
        return True

    def has_real(self) -> bool:
        return self.real_end > self.base

    def take(self, n: int) -> tuple[bytes, int]:
        """(n octets, FHP in the TM convention: offset or 0x7FF)."""
        if len(self.buf) < n:
            self.push(build_idle_packet(max(7, n - len(self.buf)), self.idle_seq), True, idle=True)
            self.idle_seq = (self.idle_seq + 1) & 0x3FFF
        while self.starts and self.starts[0] < self.base:
            self.starts.popleft()
        fhp = self.starts[0] - self.base if self.starts and self.starts[0] - self.base < n else 0x7FF
        data = bytes(self.buf[:n])
        del self.buf[:n]
        self.base += n
        while self.starts and self.starts[0] < self.base:
            self.starts.popleft()
        return data, fhp

    def clear(self) -> None:
        self.buf.clear()
        self.starts.clear()
        self.real_end = self.base


class SimDownlink:
    def __init__(self, framing: str, sec: SdlsTmSender | None):
        self.framing = framing.upper()
        if self.framing not in ("TM", "USLP"):
            raise ValueError(f"framing {framing!r}: TM or USLP")
        self.sec = sec
        self.vc = {v: _VcStream() for v in range(4)}
        self.mcfc = 0
        self.vcfc: dict[int, int] = {}
        self.prev_vc0 = False

    @property
    def data_len(self) -> int:
        return data_field_len(self.framing, self.sec.header_len + self.sec.trailer_len if self.sec else 0)

    def push(self, v: int, pkt: bytes, priority: bool = False) -> bool:
        return 0 <= v <= 3 and self.vc[v].push(pkt, priority)

    def backlog(self, v: int) -> int:
        return self.vc[v].backlog() if 0 <= v <= 3 else 0

    def data_ready(self) -> bool:
        return self.sec is None or self.sec.state in (1, 3)

    def next_frame(self, clcw: int) -> tuple[bytes, int, int | None]:
        """(200-octet frame, vcid, TIMECORR reference key or None)."""
        cands = [v for v in range(4) if self.vc[v].has_real()] if self.data_ready() else []
        oid_vc = 63 if self.framing == "USLP" else 7
        if not cands:
            cnt = self.vcfc.get(oid_vc, 0)
            if self.framing == "USLP":
                f, key = build_uslp_oid_frame(cnt, clcw, IDLE_TEXT), None
            else:
                f, key = build_oid_frame(self.mcfc, cnt, clcw, IDLE_TEXT), self.mcfc
                self.mcfc = (self.mcfc + 1) & 0xFF
            self.vcfc[oid_vc] = (cnt + 1) & 0xFF
            return f, oid_vc, key
        v = cands[0]
        if 0 in cands and 1 in cands and self.prev_vc0 and self.vc[1].backlog() > SCI_PRIORITY_BACKLOG:
            v = 1
        self.prev_vc0 = v == 0
        data, fhp = self.vc[v].take(self.data_len)
        cnt = self.vcfc.get(v, 0)
        protect = self.sec.protect if self.sec is not None and self.sec.ae else None
        if self.framing == "USLP":
            f = build_uslp_frame(v, cnt, fhp, data, clcw, protect=protect)    # type: ignore[arg-type]
            key = cnt if v == 0 else None
        else:
            f = build_frame(v, self.mcfc, cnt, fhp, data, clcw, protect=protect)   # type: ignore[arg-type]
            key = self.mcfc
            self.mcfc = (self.mcfc + 1) & 0xFF
        self.vcfc[v] = (cnt + 1) & 0xFF
        return f, v, key


# =============================================================================
# Spacecraft + RF + ground radio
# =============================================================================
class SimSpacecraft(link.RadioBackend):
    """E22 byte mode: read(), write(), close(), rcu_set_mode(). Packet mode (SX126x): the
    link.RadioBackend packet interface plus the Sx126xBridge control calls the GDS uses
    (configure, noise_dbm, wake, get_config, rf_foff, snapshot, connected)."""

    def __init__(self, cfg: dict[str, Any], *, loss: float = 0.02, max_errors: int = 3, rssi_mean: float = -112.0,
                 fading_db: float = 3.0, seed: int | None = None, clock: Callable[[], float] = time.time,
                 packet_mode: bool | None = None, sdls_key: bytes | None = None, epoch: int = 1,
                 framing: str = "TM", crc_fail_prob: float = 0.0, static_offset_hz: float = 0.0,
                 doppler_rate_hz_s: float = 0.0, burst_rate_hz: float = 5.0):
        self.rng = random.Random(seed)
        self.clock = clock
        self.base_loss = loss
        self.max_errors = max_errors
        self.rssi_mean = rssi_mean
        self.fading_db = fading_db
        self.crc_fail_prob = crc_fail_prob
        self.static_offset_hz = static_offset_hz
        self.doppler_rate_hz_s = doppler_rate_hz_s
        self.burst_rate_hz = burst_rate_hz
        r = cfg["radio"]
        station = link.get_model(r.get("model"))
        self.packet_mode = station.packet_mode if packet_mode is None else bool(packet_mode)
        self.model = link.get_model(link.SX126X) if self.packet_mode else (
            station if not station.packet_mode else link.get_model(link.E22_T33D))
        # --- ground radio emulator ---
        self.out: list[tuple[float, bytes]] = []
        self.rx_q: list[tuple[float, link.RxPacket]] = []
        self.g_regs = bytearray(regs_from_cfg(r, model=link.E22_T33D if self.packet_mode else self.model))
        self.g_mode = 0                      # E22: 0 NORMAL, 1 WOR, 2 CONFIG, 3 SLEEP
        self.g_cfg = {"channel": int(r.get("channel", 23)), "rate_code": int(r.get("air_rate", 1)),
                      "power_code": int(r.get("power", 0)), "cad": True, "rx_boost": True}
        self.correction_hz = 0
        self.last_rx_rssi = -128
        self.stats = {"foff_set": 0, "packets": 0, "crc_fail": 0}
        # --- spacecraft state ---
        self.t0 = clock()
        self.sc_ch = int(r.get("channel", 23))
        self.sc_rate = int(r.get("air_rate", 2))
        self.sc_pwr = int(r.get("power", 0))
        self.field_ut = float(cfg["science"].get("field_ut") or DEFAULT_FIELD_UT)
        sd = cfg["sdls"]
        key = sdls_key
        tc_svc = parse_service(sd["tc_service"]) if key else SERVICE_NONE
        self.sdls = SdlsReceiver(key, int(sd["spi"]), tc_svc) if key and tc_svc != SERVICE_NONE else None
        tm_ae = bool(key) and parse_service(sd["tm_service"]) == SERVICE_AE
        self.sec = SdlsTmSender(key, "AE") if tm_ae else None
        self.partition = max(0, int(epoch))
        if self.sec is not None and self.partition:
            self.sec.set_epoch(self.partition)
        self.framing = framing.upper()
        self.dl = SimDownlink(self.framing, self.sec)
        self.farm = Farm1Model()
        self._reset_state(clock())
        self.evr_q.append((1, 0x0001, f"BOOT VGQ-1 FSW 3.0 rst=0 POWER-ON radio={self.model.name} "
                                      f"{self.framing} (SIMULATOR)"))

    def _reset_state(self, now: float) -> None:
        self.t0 = now
        self.mode = 1                        # boots SAFE, like the flight
        self.safe_by_fdir = True
        self.seq: dict[int, int] = {}
        self.last_gen: list[float | None] = [None] * 7
        self.period_override: dict[int, int] = {}
        self.cmd_period_ms = 0
        self.next_tx = now + 1.0
        self.last_tx_t = -math.inf
        self.frames_sent = self.oid_sent = 0
        self.airtime_total_ms = 0
        self.cmd_acc = self.cmd_rej = 0
        self.last_opcode = self.last_status = 0
        self.tc_ok = self.tc_bad = 0
        self.cltu_ok = self.cltu_bad = 0      # flight cltu_ok_ / cltu_bad_ (RF packet)
        self.uplink_rssi: float | None = None
        self.uplink_snr: float | None = None
        self.uplink_ferr: float | None = None
        self.fdir = 0
        self.pending: tuple[str, int, int, int] | None = None     # (field, value, frames_left, revert_s)
        self.revert: tuple[str, int, float] | None = None         # (field, old_value, deadline)
        self.hibernating = False
        self.beacon_s = 600
        self.inhibit_until = 0.0
        self.ref_sclk: dict[int, tuple[float, int]] = {}          # TIMECORR key -> (SCLK, busy ms)
        self.timecorr_req = False
        self.last_tc_t = now
        self.tx_hist: deque[tuple[float, float]] = deque(maxlen=DUTY_CYCLES + 1)   # (launch, busy s)
        # When the radio was last reconfigured (flight apply_radio_config(): last_airtime_ms_ = 0
        # until the next transmission ends; 0 at boot as well).
        self.radio_change_t = -math.inf
        self.evr_q: list[tuple[int, int, str]] = []
        self.rfscan_req: tuple[int, int] | None = None
        # SSR, playback, CFDP
        self.ssr: deque[bytes] = deque()
        self.ssr_bytes = 0
        self.ssr_dropped = 0
        self.ssr_paused_drops = 0
        self.playing = False
        self.play_idx = 0
        self.cfdp_tx: cf.Sender | None = None
        self.cfdp_n = 0
        self.cfdp_seq = 0
        self.cfdp_progress = 0
        self.relay_req_t: float | None = None
        self.relay_seq = 0
        # stored sequence
        self.seq_table: dict[int, tuple[bool, int, bytes]] = {}
        self.seq_armed = False
        self.seq_start = 0
        self.seq_state = 0
        # science
        self.spec_cfg = {"gain": 255, "cycles": 59, "lamps": 0, "led_cur": 0}
        self.spec_mode = 0
        self.burst: dict[str, Any] | None = None
        self.mag_bist = 0
        self.magcal_state = 0
        # beacon (flight BeaconMonitor, simplified to the inputs the simulator has)
        self.acc = {"evr_max": 0, "science": False, "seq_complete": False}
        self.beacon = BEACON_URGENT                               # boot SAFE = safe_by_fdir

    # ================================================================ helpers
    def _sclk(self, now: float) -> float:
        return now - self.t0

    def busy_s(self) -> float:
        return link.busy_s(self.model, self.sc_rate)

    def frame_period_s(self) -> float:
        busy = self.busy_s()
        p = max(MIN_FRAME_PERIOD_S, busy * PERIOD_X10[self.mode] / 10, busy / MAX_TX_DUTY)
        return max(p, self.cmd_period_ms / 1000.0)

    def _beacon_level(self) -> int:
        a = self.acc
        if self.safe_by_fdir or self.seq_state == 3:
            return BEACON_URGENT
        if a["evr_max"] >= 2:
            return BEACON_IMPORTANT
        if a["science"] or a["seq_complete"]:
            return BEACON_INTERESTING
        return BEACON_NOMINAL

    def _beacon_raise(self) -> None:
        self.beacon = max(self.beacon, self._beacon_level())

    def _ground_contact(self) -> None:
        self.acc = {"evr_max": 0, "science": False, "seq_complete": False}
        self.beacon = self._beacon_level()

    def _evr(self, sev: int, eid: int, text: str) -> None:
        self.evr_q.append((sev, eid, text))
        self.acc["evr_max"] = max(self.acc["evr_max"], sev)
        if eid in (0x0701, 0x0702):
            self.acc["science"] = True
        self._beacon_raise()

    def _pkt(self, apid: int, payload: bytes, now: float) -> bytes:
        s = self.seq.get(apid, 0)
        self.seq[apid] = (s + 1) & 0x3FFF
        return build_packet(apid, s, payload, CucTime.from_seconds(self._sclk(now)))

    def _record(self, pkt: bytes) -> None:
        if self.cfdp_tx is not None:
            self.ssr_paused_drops += 1                # recording paused during an SSR export (C-06)
            return
        self.ssr.append(pkt)
        self.ssr_bytes += len(pkt)
        while self.ssr_bytes > SSR_MAX_BYTES and self.ssr:  # bounded: the oldest packets go first
            self.ssr_bytes -= len(self.ssr.popleft())
            self.ssr_dropped += 1
            self.play_idx = max(0, self.play_idx - 1)

    def _emit(self, apid: int, payload: bytes, vc: int, now: float, priority: bool = False,
              record: bool = True) -> None:
        pkt = self._pkt(apid, payload, now)
        if record:
            self._record(pkt)
        self.dl.push(vc, pkt, priority)

    def _emit_values(self, apid: int, values: dict[str, Any], vc: int, now: float) -> None:
        self._emit(apid, decom.encode(apid, values), vc, now)

    # ============================================================ radio (RCU)
    def rcu_set_mode(self, mode: int) -> bool:
        self.g_mode = mode & 3
        return True

    @property
    def connected(self) -> bool:
        return True

    def read(self) -> bytes:
        now = self.clock()
        self._run_spacecraft(now)
        if self.packet_mode:
            if not any(t <= now for t, _ in self.rx_q) and self.clock is time.time:
                time.sleep(0.02)
            return b""
        ready = [b for t, b in self.out if t <= now]
        self.out = [(t, b) for t, b in self.out if t > now]
        if not ready and self.clock is time.time:
            time.sleep(0.02)
        return b"".join(ready)

    def packets(self) -> list[link.RxPacket]:
        now = self.clock()
        ready = [p for t, p in self.rx_q if t <= now]
        self.rx_q = [(t, p) for t, p in self.rx_q if t > now]
        return ready

    def write(self, data: bytes) -> None:
        now = self.clock()
        if self.packet_mode:
            if data[:4] == bytes([0xC0, 0xC1, 0xC2, 0xC3]):
                return                                       # Sx126xBridge never radiates E22 queries
            t_arrive = now + link.busy_s(link.SX126X, self.g_cfg["rate_code"], min(len(data), 255))
            self._uplink(data, t_arrive, False, self.g_cfg["channel"], self.g_cfg["rate_code"])
            return
        if self.g_mode == 2:                                 # E22 CONFIG mode
            self._ground_config(data, now)
            return
        if data[:4] == bytes([0xC0, 0xC1, 0xC2, 0xC3]):      # ambient-noise query
            noise = self._noise_dbm(self.g_regs[5])
            self.out.append((now, bytes([0xC1, 0x00, 0x02, (256 + round(noise)) & 0xFF,
                                                 (256 + self.last_rx_rssi) & 0xFF])))
            return
        if self.g_mode in (0, 1):                            # RF transmission (uplink)
            wor_tx = self.g_mode == 1 and bool(self.g_regs[6] & 0x08)
            t_arrive = now + link.busy_s(self.model, self.g_regs[3] & 7) + (2.0 if wor_tx else 0.0)
            self._uplink(data, t_arrive, wor_tx, self.g_regs[5], self.g_regs[3] & 7)

    def close(self) -> None:
        return None

    # ---------------------------------------------- packet-mode control calls
    def configure(self, channel: int | None = None, rate_code: int | None = None, power_code: int | None = None,
                  cad: bool | None = None, rx_boost: bool | None = None, transient: bool = False) -> int:
        """Sx126xBridge.configure(): applied at once (transient survey steps alike - the
        simulated MCU has no queue and no reboot to re-push a configuration after)."""
        for k, v in (("channel", channel), ("rate_code", rate_code), ("power_code", power_code), ("cad", cad),
                     ("rx_boost", rx_boost)):
            if v is not None:
                self.g_cfg[k] = v
        return 1

    def rf_foff(self, hz: int) -> int:
        if isinstance(hz, bool) or not isinstance(hz, int) or abs(hz) > link.AFC_RPC_MAX_HZ:
            return -1
        self.correction_hz = hz
        self.stats["foff_set"] += 1
        return 1

    def noise_dbm(self) -> float | None:
        return round(self._noise_dbm(self.g_cfg["channel"]) * 10) / 10.0

    def wake(self, cltu: bytes) -> bool:
        now = self.clock()
        self._uplink(cltu, now + link.busy_s(link.SX126X, self.g_cfg["rate_code"], min(len(cltu), 255)), True,
                     self.g_cfg["channel"], self.g_cfg["rate_code"])
        return True

    def get_config(self) -> dict[str, Any]:
        c = self.g_cfg
        return {"model": link.SX126X, "channel": c["channel"], "freq_mhz": link.channel_freq_mhz(c["channel"]),
                "air_rate_code": c["rate_code"], "air_rate": link.rate_label(link.SX126X, c["rate_code"]),
                "power_code": c["power_code"], "lbt": c["cad"], "rssi_byte": True, "rssi_noise_enable": True,
                "rx_boost": c["rx_boost"], "rx_boost_verified": c["rx_boost"], "freq_offset_hz": self.correction_hz,
                "tcxo_fallback": False, "calib_error": False, "cfg_pending": False, "chip": "SX1268 (sim)",
                "fw": "DSSQ-IO sim", "state": "RX"}

    def snapshot(self) -> dict[str, Any]:
        return {"connected": True, "mcu": self.get_config(), "mcu_age_s": 0.0, "stats": dict(self.stats),
                "tx_backlog": 0, "rx_queue": len(self.rx_q), "last_cad": None, "last_tx": None,
                "freq_offset_hz": self.correction_hz, "events": []}

    # ========================================================== ground E22
    def _ground_config(self, data: bytes, now: float) -> None:
        if len(data) >= 3 and data[0] in (0xC0, 0xC2) and data[1] + data[2] <= 9:
            a, n = data[1], data[2]
            self.g_regs[a:a + n] = data[3:3 + n]
            self.out.append((now, bytes([0xC1, a, n]) + bytes(self.g_regs[a:a + n])))
        elif len(data) >= 3 and data[0] == 0xC1:
            a, n = data[1], data[2]
            if a == 0x80:
                self.out.append((now, bytes([0xC1, 0x80, 7]) + bytes([0x00, 0x22, 0x33, 0, 0, 0, 0])))
            elif a + n <= 9:
                self.out.append((now, bytes([0xC1, a, n]) + bytes(self.g_regs[a:a + n])))

    def _noise_dbm(self, ch: int) -> float:
        # a quiet band with "interferers" so the RF surveys have something to find
        extra = 18 if ch == 24 else (9 if ch in (21, 30) else 0)
        return -118 + extra + self.rng.gauss(0, 0.7)

    def _g_link(self) -> tuple[int, int]:
        if self.packet_mode:
            return self.g_cfg["channel"], self.g_cfg["rate_code"]
        return self.g_regs[5], self.g_regs[3] & 7

    # ========================================================== spacecraft
    def _run_spacecraft(self, now: float) -> None:
        if self.revert and now > self.revert[2]:
            fld, old, _ = self.revert
            setattr(self, fld, old)
            self.revert = None
            self.radio_change_t = now
            self.fdir |= 1 << 12
            self._evr(3, 0x0602, f"radio change not confirmed - reverting {fld}={old}")
        if self.sec is not None and self.sec.state == 2 and self._sclk(now) >= SDLS_EPOCH_WAIT_S:
            self.sec.fallback = True
            self._evr(2, 0x0121, f"SDLS TM: no epoch after {int(SDLS_EPOCH_WAIT_S)} s: null SA (clear) until the "
                                 "EGSE sends one")
        if self.relay_req_t is not None and now - self.relay_req_t > RELAY_START_TIMEOUT_S:
            self.relay_req_t = None
            self._evr(2, 0x0A04, f"CFDP relay seq {self.relay_seq}: EGSE did not start within 30 s")
        self._seq_poll(now)
        self._burst_poll(now)
        for sev, eid, text in self.evr_q:
            pri = sev >= 2 or 0x0120 <= eid <= 0x0122
            self._emit(APID_EVR, bytes([sev]) + eid.to_bytes(2, "big") + text.encode("ascii", "replace")[:80], 0,
                       now, priority=pri)
        self.evr_q.clear()
        if self.rfscan_req:
            first, last = self.rfscan_req
            self.rfscan_req = None
            noise = [max(-127, round(self._noise_dbm(c))) for c in range(first, last + 1)]
            payload = bytes([first, len(noise)]) + (2500).to_bytes(2, "big") + bytes((256 + n) & 0xFF for n in noise)
            self._emit(APID_RFSCAN, payload, 0, now)
            best = first + noise.index(min(noise))
            self._evr(1, 0x0303, f"RF survey done in 2500 ms: quietest CH{best} ({min(noise)} dBm)")
            self.radio_change_t = now                       # flight: the operational channel is restored
        t = self._sclk(now)
        gens: tuple[Callable[[float], None], ...] = (self._hk, self._rf, self._timecorr, self._mag, self._att,
                                                     self._spec, self._env)
        for i, period in enumerate(CADENCE[self.mode]):
            p = 0 if self.hibernating else self.period_override.get(i, period)   # beacon HK: below
            lg = self.last_gen[i]
            if p and (lg is None or t - lg >= p):
                self.last_gen[i] = t
                gens[i](now)
        if self.timecorr_req and self.ref_sclk:
            self.timecorr_req = False
            self._timecorr(now)
        self._cfdp_pace(now)
        self._playback_refill(now)
        if now < self.inhibit_until:
            return
        if self.hibernating:                 # flight: one frame per beacon_s after the last one, each
            if now - self.last_tx_t < self.beacon_s:    # carrying a fresh HK (gen_hk before the frame)
                return
            self._hk(now)
            self.last_gen[0] = t
        elif now < self.next_tx:
            return
        self._transmit(now)

    def _transmit(self, now: float) -> None:
        busy = self.busy_s()
        self.last_tx_t = now
        self.next_tx = now + self.frame_period_s()
        clcw = Clcw.pack(lockout=self.farm.state == 3, retransmit=self.farm.retransmit, farm_b=self.farm.farm_b,
                         report_value=self.farm.vr, no_bitlock=(now - self.last_tc_t) > 300)
        frame, vcid, key = self.dl.next_frame(clcw)
        if vcid in (7, 63):
            self.oid_sent += 1
        if key is not None:
            self.ref_sclk[key] = (self._sclk(now), round(busy * 1000))
        self.frames_sent += 1
        self.airtime_total_ms += round(busy * 1000)
        self.tx_hist.append((now, busy))
        if self.pending:
            fld, value, left, revert_s = self.pending
            if left <= 1:
                self.revert = (fld, getattr(self, fld), now + revert_s)
                setattr(self, fld, value)
                self.pending = None
                self.radio_change_t = now + busy            # flight: applied once this frame has ended
            else:
                self.pending = (fld, value, left - 1, revert_s)
        self._downlink(encode_cadu(frame), now + busy)

    # ----------------------------------------------------------- RF channel
    def _rssi(self) -> float:
        return self.rssi_mean + self.rng.gauss(0, self.fading_db)

    def _downlink(self, cadu: bytes, t_arrive: float) -> None:
        if self.packet_mode != self.model.packet_mode:
            return
        if (self.packet_mode or self.g_mode in (0, 1)) and self._g_link() != (self.sc_ch, self.sc_rate):
            return                                          # ground not listening on this link
        if not self.packet_mode and self.g_mode not in (0, 1):
            return
        rssi = self._rssi()
        sens = link.sensitivity(self.model, self.sc_rate)[0]
        margin = rssi - sens
        p_loss = self.base_loss + (1 - self.base_loss) / (1 + math.exp(margin / 1.5))
        if self.rng.random() < p_loss:
            return
        b = bytearray(cadu)
        if not self.packet_mode:
            for _ in range(self.rng.randint(0, self.max_errors)):      # residual symbol errors
                b[self.rng.randrange(4, len(b))] ^= self.rng.randrange(1, 256)
            rssi_i = max(-127, min(-1, round(rssi)))
            self.last_rx_rssi = rssi_i
            # The ground E22 then sends the CADU + RSSI octet over its own 9600-baud UART (8N1):
            # the delay gds._time_correlate removes (E22_UART_CADU_S) before the airtime.
            uart_s = (len(b) + 1) * 10 / GROUND_UART_BAUD
            self.out.append((t_arrive + uart_s, bytes(b) + bytes([(256 + rssi_i) & 0xFF])))
            return
        crc_ok = self.rng.random() >= self.crc_fail_prob
        if not crc_ok:                                      # RS-correctable errors (contract 20)
            for pos in self.rng.sample(range(4, len(b)), self.rng.randint(*RS_RESCUE_ERRORS)):
                b[pos] ^= self.rng.randrange(1, 256)
        p = link.SX126X_PROFILES[self.sc_rate]
        floor = link.THERMAL_NOISE_DBM_HZ + 10 * math.log10(p.bw_hz) + NOISE_FLOOR_NF_DB
        snr = max(-32.0, min(31.75, round((rssi - floor) * 4) / 4))
        ferr = self.static_offset_hz + self.doppler_rate_hz_s * (t_arrive - self.t0) - self.correction_hz \
            + self.rng.gauss(0, 15.0)
        self.stats["packets"] += 1
        self.stats["crc_fail"] += 0 if crc_ok else 1
        self.rx_q.append((t_arrive, link.RxPacket(bytes(b), t_arrive, max(-127.5, min(0.0, round(rssi * 2) / 2)),
                                                  snr, float(round(ferr)), crc_ok)))

    def _uplink(self, data: bytes, t_arrive: float, wor_tx: bool, ch: int, code: int) -> None:
        if (ch, code) != (self.sc_ch, self.sc_rate):
            return
        if self.hibernating and not wor_tx and self.rng.random() < 0.9:
            return                                          # duty-cycled receiver mostly asleep
        if self.rng.random() < self.base_loss:
            return
        rssi = self._rssi()
        self.uplink_rssi = round(rssi)
        if self.model.packet_mode:
            self.uplink_snr = round((rssi + 117.0) * 4) / 4
            self.uplink_ferr = float(round(-self.static_offset_hz))
        self._on_tc(data, t_arrive)

    # ---------------------------------------------------------- commanding
    def _on_tc(self, data: bytes, now: float) -> None:
        info, _ = cltu_decode(data)
        if not info:
            # Flight process_tc: no CLTU in the packet (e.g. the ground's plain-text station
            # identification) is counted in cltu_bad and dropped - no EVR, FDIR or FARM-1 change.
            self.cltu_bad += 1
            return
        self.cltu_ok += 1
        f: TcFrameInfo | None = parse_tc_frame(info, open_tfdf=self.sdls is None)
        if f is None:
            self.tc_bad += 1
            return
        if self.sdls is not None:
            body, verdict = self.sdls.process(info[:f.length], f.data, hdr_len=f.hdr_len)
            if body is None:
                self.tc_bad += 1
                self.fdir |= 1 << 13
                self._evr(3, 0x0111, f"SDLS rejected TC frame: {verdict}")
                return
            f.data = body
            f = tc_open_tfdf(f)
            if f is None:
                self.tc_bad += 1
                self._evr(2, 0x0107, "TC frame rejected: BAD_TFDF")
                return
        self.tc_ok += 1
        self.last_tc_t = now
        if self.hibernating:
            self.hibernating = False
            self.fdir &= ~FDIR_HIBERNATE
            self._evr(2, 0x0801, "WAKE: authenticated uplink received")
        verdict = self.farm.on_frame(f)
        if self.revert and verdict in ("ACCEPT", "ACCEPT_BYPASS"):
            self.revert = None
            self._evr(1, 0x0210, f"radio change confirmed by ground (code {self.sc_rate}, ch {self.sc_ch})")
        if verdict in ("ACCEPT", "ACCEPT_BYPASS"):
            self._execute_packet(f.data, f.seq, CMD_SOURCE_AD if verdict == "ACCEPT" else CMD_SOURCE_BD, now)
        elif verdict == "DISCARD_LOCKOUT":
            self._evr(3, 0x0205, f"FARM LOCKOUT (N(S)={f.seq} V(R)={self.farm.vr})")
        elif verdict.startswith("CONTROL"):
            self._evr(1, 0x0201, f"COP-1 {verdict}")
        if verdict in ("ACCEPT", "ACCEPT_BYPASS", "CONTROL_UNLOCK", "CONTROL_SET_VR"):
            self._ground_contact()

    def _execute_packet(self, pkt: bytes, tc_seq: int, src: int, now: float) -> None:
        if len(pkt) < 7 or (int.from_bytes(pkt[0:2], "big") & 0x7FF) != 0x0C0:
            self._cmdver(tc_seq, src, pkt[6] if len(pkt) > 6 else 0, 2, 0, now)
            return
        n = int.from_bytes(pkt[4:6], "big") + 1
        self._execute(pkt[6:6 + n], tc_seq, src, now)

    def _cmdver(self, tc_seq: int, src: int, opcode: int, err: int, tag: int, now: float) -> None:
        """CMDVER (I1): stage octet = stage (bits 1..0: 2 EXECUTED, 3 FAILED) | command source
        << 6 (0 AD frame, 1 BD frame / hardline, 2 stored sequence); tc_seq = the AD frame's
        N(S), 0 for the other sources."""
        if err:
            self.cmd_rej += 1
            self._evr(2, 0x0500 + min(err, 7), f"command 0x{opcode:02X} rejected, error {err}")
        else:
            self.cmd_acc += 1
        self.last_opcode, self.last_status = opcode, err
        stage = (3 if err else 2) | (src & 3) << 6
        self._emit(APID_CMDVER, bytes([tc_seq & 0xFF if src == CMD_SOURCE_AD else 0, opcode, stage, err])
                   + tag.to_bytes(4, "big"), 0, now, priority=True)

    def _execute(self, ud: bytes, tc_seq: int, src: int, now: float) -> None:
        """One command (opcode + arguments) from a TC or the stored sequence."""
        if not ud:
            self._cmdver(tc_seq, src, 0, 2, 0, now)
            return
        op, a = ud[0], ud[1:]
        err, _why = check_user_data(ud)
        tag = 0
        if not err:
            err, tag = self._effect(op, a, now)
        self._cmdver(tc_seq, src, op, err, tag, now)

    def _effect(self, op: int, a: bytes, now: float) -> tuple[int, int]:
        """Simulated effect of a valid command: (CmdError, ground tag)."""
        sclk = int(self._sclk(now))
        if op == 0x02:                                      # MODE
            old = self.mode
            self.mode, self.safe_by_fdir, self.hibernating = a[0], False, False
            self.fdir &= ~FDIR_HIBERNATE
            self.last_gen = [None] * 7
            self._evr(1, 0x0010 + a[0], f"MODE {MODE_NAMES[old]} -> {MODE_NAMES[a[0]]} (command)")
        elif op == 0x03:                                    # TXPWR
            self.sc_pwr = a[0]
            self.radio_change_t = now                       # flight: applied when the radio is idle
            self._evr(1, 0x0214, f"TXPWR code {a[0]} = {link.power_dbm(self.model, a[0]):.0f} dBm")
        elif op == 0x04:                                    # AIRRATE
            self.pending = ("sc_rate", a[0], 2, int.from_bytes(a[1:3], "big"))
            self._evr(2, 0x0211, f"AIR RATE -> code {a[0]} in 2 frames; revert in {int.from_bytes(a[1:3], 'big')} s "
                                 "without uplink")
        elif op == 0x15:                                    # CHANNEL
            self.pending = ("sc_ch", a[0], 2, int.from_bytes(a[1:3], "big"))
            self._evr(2, 0x0212, f"CHANNEL -> {a[0]} in 2 frames; revert in {int.from_bytes(a[1:3], 'big')} s "
                                 "without uplink")
        elif op == 0x05:                                    # FPERIOD
            self.cmd_period_ms = int.from_bytes(a, "big")
        elif op == 0x06:                                    # PKTRATE
            apid, period = int.from_bytes(a[0:2], "big"), int.from_bytes(a[2:4], "big")
            self.period_override[PERIODIC_APIDS.index(apid)] = period
        elif op == 0x07:                                    # PLAYBACK
            if a[0] and self.cfdp_tx is not None:
                return 7, 0
            self.playing = bool(a[0])
            self.play_idx = 0
            self._evr(1, 0x0401, f"SSR playback {'START' if a[0] else 'STOP'} ({len(self.ssr)} packets)")
        elif op == 0x08:                                    # SSRCLEAR
            if self.cfdp_tx is not None:
                return 7, 0
            self.ssr.clear()
            self.ssr_bytes = 0
            self.dl.vc[2].clear()
        elif op == 0x09:                                    # PING
            return 0, int.from_bytes(a, "big")
        elif op == 0x0B:                                    # RSTCNT (flight: + the event-latched FDIR flags)
            self.cmd_acc = self.cmd_rej = self.tc_ok = self.tc_bad = self.cltu_ok = self.cltu_bad = 0
            self.fdir &= ~FDIR_CLEARED_BY_RSTCNT
        elif op == 0x0C:                                    # REBOOT (simulated: a fresh boot, new epoch)
            self._reboot(now)
            return 0, 0
        elif op == 0x10:                                    # TXINHIBIT
            self.inhibit_until = now + int.from_bytes(a, "big")
        elif op == 0x12:                                    # TIMECORR
            self.timecorr_req = True
        elif op == 0x13:                                    # RFSCAN
            self.rfscan_req = (a[0], a[1])
            self._evr(1, 0x0302, f"RF survey CH{a[0]}..CH{a[1]} starting (downlink paused)")
        elif op == 0x14:                                    # HIBERNATE
            self.hibernating = True
            self.beacon_s = int.from_bytes(a, "big")
            self.fdir |= FDIR_HIBERNATE
            self._evr(2, 0x0800, f"HIBERNATE: beacon every {self.beacon_s} s")
        elif op == 0x16:                                    # SPECCFG
            self.spec_cfg.update(gain=a[0], cycles=a[1], lamps=a[2], led_cur=a[3])
        elif op == 0x17:                                    # CFDPPUT
            return self._cfdp_put(a[0], int.from_bytes(a[1:3], "big"), now, sclk), 0
        elif op == 0x18:                                    # CFDPCANCEL
            what = "nothing active"
            if self.cfdp_tx is not None:
                self.cfdp_tx.cancel()
                what = f"SSR export seq {self.cfdp_seq}"
            elif self.relay_req_t is not None:
                self.relay_req_t = None
                what = f"relay seq {self.relay_seq}"
            self._evr(1, 0x0A02, f"CFDP cancel: {what}")
        elif op == 0x1A:                                    # SPECMODE
            self.spec_mode = a[0]
            if a[0] == 4:
                self.burst = {"t_start": now, "dur": a[1], "t0_ms": int(self._sclk(now) * 1000) & 0xFFFFFFFF,
                              "pending": [], "next": now, "count": 0}
                self._evr(1, 0x0704, f"BURST START n={a[1]} s cycles=1")
        elif op == 0x1D:                                    # SEQLOAD
            blk = unpack_seqload(a)
            replaced = blk.slot in self.seq_table
            self.seq_table[blk.slot] = (blk.relative, blk.t_s, blk.cmd)
            if self.seq_state in (2, 3):
                self.seq_state = 0
            self._evr(1, 0x0900, f"SEQ slot {blk.slot} loaded: {'T+' if blk.relative else 'SCLK '}{blk.t_s} s "
                                 f"op 0x{blk.cmd[0]:02X}" + (" replaced" if replaced else ""))
        elif op == 0x1E:                                    # SEQCTRL
            return self._seq_ctrl(a[0], a[1], sclk), 0
        elif op == 0x1F:                                    # MAGBIST
            mask = {0: 0x0F, 1: 0xF0, 2: 0xFF}[a[0]]
            self.mag_bist = (self.mag_bist & ~mask & 0xFF) | mask
            self._evr(1, 0x0910, "RM3100 BIST: X Y Z OK")
        elif op == 0x20:                                    # MAGCAL
            unit = a[0]
            clear = a[1:] == struct.pack(">3h6h", 0, 0, 0, 16384, 16384, 16384, 0, 0, 0)
            bit = 1 << unit
            self.magcal_state = (self.magcal_state & ~bit) if clear else (self.magcal_state | bit)
            self._evr(1, 0x0912, f"MAGCAL unit {unit} {'cleared' if clear else 'loaded'}")
        return 0, 0

    def _reboot(self, now: float) -> None:
        part = self.partition + 1
        self._reset_state(now)
        self.partition = part
        self.farm = Farm1Model()
        self.dl = SimDownlink(self.framing, self.sec)
        if self.sec is not None:
            self.sec.set_epoch(part)
        self.evr_q.append((1, 0x0001, f"BOOT VGQ-1 FSW 3.0 rst=2 SOFTWARE radio={self.model.name} (SIMULATOR)"))

    # ------------------------------------------------------------- CFDP
    def _cfdp_put(self, source: int, max_kib: int, now: float, sclk: int) -> int:
        if self.partition == 0:
            self._evr(2, 0x0A06, "CFDPPUT refused: no SCLK partition from the EGSE yet")
            return 7
        if self.cfdp_tx is not None or self.relay_req_t is not None:
            return 7
        self.cfdp_n = self.cfdp_n % 0xFFFE + 1
        seq = ((self.partition & 0x7FFF) << 16) | self.cfdp_n            # flight cfdp_make_seq
        if source != 0:                                     # EGSE files: no EGSE in the simulator
            self.relay_seq = cf.EGSE_SEQ_FLAG | seq
            self.relay_req_t = now
            self._evr(1, 0x0A03, f"CFDP relay requested: source {source} seq {self.relay_seq} max {max_kib} KiB")
            return 0
        if self.playing:
            return 7
        name = f"ssr_p{self.partition}_{sclk}.bin"
        data = b"".join(self.ssr)
        self.cfdp_tx = cf.Sender(seq, name, data, max_bytes=max_kib * 1024)
        self.cfdp_seq = seq
        self.cfdp_progress = 0
        self._evr(1, 0x0A00, f"CFDP SSR export {name}: {self.cfdp_tx.size} B seq {seq}")
        return 0

    def _cfdp_pace(self, now: float) -> None:
        tx = self.cfdp_tx
        if tx is None:
            return
        if self.dl.backlog(3) + 12 + cf.HDR_LEN + 136 > CFDP_VC3_BACKLOG_MAX:
            return
        pdu = tx.next_pdu()
        if pdu is not None:
            self._emit(APID_CFDP, pdu, 3, now, record=False)
        self.cfdp_progress = tx.progress_permille
        if tx.done:
            self._evr(1, 0x0A01, f"CFDP EOF cond {tx.condition}: {tx.sent}/{tx.size} B, "
                                 f"{self.ssr_paused_drops} not recorded")
            self.cfdp_tx = None

    def _playback_refill(self, now: float) -> None:
        if not self.playing:
            return
        for _ in range(64):                                 # bounded per pass
            if self.play_idx >= len(self.ssr):
                self.playing = False
                self._evr(1, 0x0402, "SSR playback complete")
                return
            pkt = self.ssr[self.play_idx]
            if not self.dl.push(2, pkt):
                return
            self.play_idx += 1

    # ------------------------------------------------------- stored sequence
    def _seq_ctrl(self, action: int, slot: int, sclk: int) -> int:
        if action == 0:                                     # START
            if not self.seq_table:
                self._evr(2, 0x0903, "SEQCTRL START refused: table empty")
                return 3
            self.seq_armed, self.seq_start, self.seq_state = True, sclk, 1
            self._evr(1, 0x0902, f"SEQCTRL START ({len(self.seq_table)} entries)")
        elif action == 1:
            self.seq_armed = False
            self.seq_state = 0
            self._evr(1, 0x0902, "SEQCTRL STOP")
        elif action == 2:
            self.seq_table.clear()
            self.seq_armed, self.seq_state = False, 0
            self._evr(1, 0x0902, "SEQCTRL CLEAR")
        else:
            self.seq_table.pop(slot, None)
            self._evr(1, 0x0902, f"SEQCTRL DELETE slot {slot}")
        return 0

    def _due(self, entry: tuple[bool, int, bytes]) -> int:
        rel, t_s, _ = entry
        return (self.seq_start + t_s) & 0xFFFFFFFF if rel else t_s

    def _seq_poll(self, now: float) -> None:
        if not self.seq_armed:
            return
        sclk = int(self._sclk(now))
        due = [(self._due(e), slot) for slot, e in self.seq_table.items()
               if (sclk - self._due(e)) & 0xFFFFFFFF < 0x80000000]
        if due:
            t_due, slot = min(due)
            _rel, _t, cmd = self.seq_table.pop(slot)
            late = (sclk - t_due) & 0xFFFFFFFF
            if late > SEQ_LATE_MAX_S:
                self._evr(2, 0x0905, f"SEQ slot {slot} skipped: {late} s late")
            else:
                self._evr(1, 0x0904, f"SEQ slot {slot}: opcode 0x{cmd[0]:02X}")
                self._execute(cmd, 0, CMD_SOURCE_STORED, now)
        if not self.seq_table and self.seq_armed:
            self.seq_armed, self.seq_state = False, 2
            self.acc["seq_complete"] = True
            self._beacon_raise()
            self._evr(1, 0x0906, "sequence COMPLETE")

    def _seq_next_dt(self, now: float) -> int | None:
        if not self.seq_armed or not self.seq_table:
            return None
        sclk = int(self._sclk(now))
        dts = [(self._due(e) - sclk) & 0xFFFFFFFF for e in self.seq_table.values()]
        dts = [0 if d >= 0x80000000 else d for d in dts]
        return min(min(dts), 0xFFFE)

    # ---------------------------------------------------------------- burst
    def _burst_sample(self, t: float) -> list[int | None]:
        k = 1.0 + 0.05 * math.sin(2 * math.pi * 1.5 * t)       # a 1.5 Hz flicker the FFT can find
        return [min(65535, max(0, round(v * 30 * k + self.rng.gauss(0, 3)))) for v in SPEC_TEMPLATE_UW]

    def _burst_poll(self, now: float) -> None:
        b = self.burst
        if b is None:
            return
        end = b["t_start"] + b["dur"]
        for _ in range(1000):                               # bounded catch-up
            if b["next"] > now or b["next"] > end:
                break
            b["pending"].append((round((b["next"] - b["t_start"]) * 1000) & 0xFFFF, self._burst_sample(b["next"])))
            b["count"] += 1
            b["next"] += 1.0 / self.burst_rate_hz
        done = now > end
        while len(b["pending"]) >= 3 or (done and b["pending"]):
            chunk, b["pending"] = b["pending"][:3], b["pending"][3:]
            payload = spectral.pack_specburst(b["t0_ms"], round(self.burst_rate_hz * 10), 0, 1, chunk)
            pkt = self._pkt(APID_SPECBURST, payload, now)
            self._record(pkt)
            if not self.dl.push(1, pkt):
                b["dropped"] = b.get("dropped", 0) + len(chunk)    # VC1 full: the link is the limit
        if done:
            self._evr(1, 0x0704, f"BURST END samples={b['count']} rate={self.burst_rate_hz:.1f} Hz "
                                 f"dropped={b.get('dropped', 0)} readmax=6 ms tx=0")
            self.burst = None
            self.spec_mode = 0

    # ------------------------------------------------------- packet builders
    def _duty(self, now: float) -> float:
        """Flight TxDutyMeter (datalink.h): busy time over WHOLE transmission cycles (launch to
        next launch) of the last DUTY_CYCLES cycles launched within DUTY_WINDOW_S; the open
        cycle counts with max(elapsed, the period in force); integer ms and permille, truncated
        like the flight. A transmission still on the air closes the newest finished cycle."""
        done = [(t, round(b * 1000)) for t, b in self.tx_hist if t + b <= now]
        active = len(done) < len(self.tx_hist)
        active_start = self.tx_hist[-1][0] if active else 0.0
        # The spacing in force (flight tx_period_ms()): after a coordinated rate change it is
        # never shorter than the period the last frame at the old rate was launched with
        # (next_tx was set at that launch: the flight's reconfiguration hold).
        owed = self.next_tx - self.last_tx_t if math.isfinite(self.last_tx_t) else 0.0
        open_ms = round((self.beacon_s if self.hibernating else max(self.frame_period_s(), owed)) * 1000)
        busy = span = 0
        cycle_end = 0.0
        for k, (t, b) in enumerate(reversed(done[-DUTY_CYCLES:])):
            if k > 0 and now - t > DUTY_WINDOW_S:
                break
            if k > 0:
                length = round((cycle_end - t) * 1000)
            elif active:
                length = round((active_start - t) * 1000)
            else:
                length = max(round((now - t) * 1000), open_ms)
            busy += b
            span += max(length, b)
            cycle_end = t
        return min(busy * 1000 // span, 1000) / 1000.0 if span else 0.0

    def _hk(self, now: float) -> None:
        self._beacon_raise()
        tx = self.cfdp_tx
        dl = self.dl
        values = {
            "fsw_mode": self.mode, "sclk_partition": self.partition, "uptime_s": int(self._sclk(now)),
            "reset_cause": 0, "fdir_flags": self.fdir, "sensor_health": SENSORS_ALL,
            "cmd_accepted": min(self.cmd_acc, 0xFFFF), "cmd_rejected": min(self.cmd_rej, 0xFFFF),
            "cmd_last_opcode": self.last_opcode, "cmd_last_status": self.last_status,
            "tc_frames_ok": min(self.tc_ok, 0xFFFF), "tc_frames_bad": min(self.tc_bad, 0xFFFF),
            "farm_state": self.farm.state, "farm_vr": self.farm.vr,
            "cmd_loss_timer_s": int(max(0.0, now - self.last_tc_t)),
            "bus_temp": round(31.0 + 4 * self._duty(now) + self.rng.gauss(0, 0.1), 2),
            "radio_fault": 0, "radio_fault_reports": 0, "loop_max_ms": 12, "loop_overruns": 0, "i2c_errors": 0,
            "ssr_fill": round(100.0 * self.ssr_bytes / SSR_MAX_BYTES, 1),
            "ssr_dropped": min(self.ssr_dropped + self.ssr_paused_drops, 0xFFFF), "ssr_kib": SSR_MAX_BYTES // 1024,
            "vc0_backlog": dl.backlog(0), "vc1_backlog": dl.backlog(1), "vc2_backlog": dl.backlog(2),
            "vc3_backlog": dl.backlog(3),
            "sdls_tc_service": self.sdls.service if self.sdls else 0,
            "sdls_tm_service": 2 if self.sec else 0,
            "sdls_auth_fail": min(self.sdls.failures, 0xFFFF) if self.sdls else 0,
            "sdls_last_sn": self.sdls.last_sn if self.sdls else 0,
            "sdls_tm_epoch": self.sec.epoch if self.sec else 0, "sdls_tm_state": self.sec.state if self.sec else 0,
            "cfdp_state": 1 if tx is not None else (2 if self.relay_req_t is not None else 0),
            "cfdp_seq": self.relay_seq if self.relay_req_t is not None else self.cfdp_seq,
            "cfdp_progress": self.cfdp_progress / 10.0,
            "seq_state": self.seq_state, "seq_count": len(self.seq_table) if self.seq_armed else 0,
            "seq_next_dt_s": self._seq_next_dt(now), "mag_bist": self.mag_bist, "magcal_state": self.magcal_state,
            "beacon_level": self.beacon}
        self._emit_values(APID_HK, values, 0, now)

    def _last_airtime_ms(self, now: float) -> int:
        """Flight last_airtime_ms_: the busy time of the newest transmission that has ended, 0 at
        boot and from a radio reconfiguration until the next transmission ends."""
        done = [(t, b) for t, b in self.tx_hist if t + b <= now]
        if not done:
            return 0
        t, b = done[-1]
        if self.radio_change_t <= now and self.radio_change_t >= t + b:
            return 0
        return round(b * 1000)

    def _rf(self, now: float) -> None:
        sx = self.model.packet_mode
        values = {
            "radio_model": self.model.model_id, "air_rate_code": self.sc_rate, "tx_power_code": self.sc_pwr,
            "channel": self.sc_ch, "radio_cfg_ok": 1, "framing": 1 if self.framing == "USLP" else 0,
            "frames_sent": self.frames_sent, "oid_frames_sent": self.oid_sent,
            "tx_airtime_ms_total": self.airtime_total_ms & 0xFFFFFFFF, "tx_duty": round(self._duty(now) * 1000) / 10,
            "frame_period_ms": min(round(self.frame_period_s() * 1000), 0xFFFF), "radio_timeouts": 0,
            "uplink_rssi": self.uplink_rssi, "uplink_snr": self.uplink_snr if sx else None,
            "uplink_freq_err": self.uplink_ferr if sx else None,
            "sc_noise_floor": round(self._noise_dbm(self.sc_ch)), "cltu_ok": min(self.cltu_ok, 0xFFFF),
            "cltu_bad": min(self.cltu_bad, 0xFFFF), "bch_corrected": 0, "rx_bytes": self.tc_ok * 40,
            # flight: last_airtime_ms_ / 100 (integer division, truncated), saturating at 25.5 s
            "mcfc": self.dl.mcfc, "last_airtime": min(self._last_airtime_ms(now) // 100, 255) / 10, "lbt_busy": 0,
            "rf_flags": 1 if sx else 0}
        self._emit_values(APID_RF, values, 0, now)

    def _timecorr(self, now: float) -> None:
        if not self.ref_sclk:
            return
        key = max(self.ref_sclk, key=lambda k: self.ref_sclk[k][0])     # the latest reference frame
        sclk, busy_ms = self.ref_sclk[key]
        c = CucTime.from_seconds(sclk)
        self._emit_values(APID_TIMECORR, {"ref_mcfc": key, "sclk_coarse": c.coarse, "sclk_fine": c.fine,
                                          "air_rate_code": self.sc_rate,
                                          "ref_airtime_ms": min(busy_ms, 0xFFFF)}, 0, now)

    def mag_truth(self, unit: int, t: float) -> tuple[float, float, float]:
        """Noise-free raw RM3100 vector of a unit at SCLK t (uT): S_inv R^T B_world + offset."""
        bw = tuple(self.field_ut * c / math.sqrt(sum(x * x for x in MAG_FIELD_DIR)) for c in MAG_FIELD_DIR)
        body = _mtv(_rot(t), bw)
        raw = _mv(MAG_SOFT_IRON[unit], body)
        off = MAG_OFFSET_UT[unit]
        return raw[0] + off[0], raw[1] + off[1], raw[2] + off[2]

    def _mag(self, now: float) -> None:
        t = self._sclk(now)
        g = self.rng.gauss
        # nsamples as the flight reports it: the 1 Hz readings in one MAG period (PKTRATE MAG 1
        # gives 1). The simulated vector itself is instantaneous.
        period = self.period_override.get(PERIODIC_APIDS.index(APID_MAG), CADENCE[self.mode][3])
        v: dict[str, Any] = {"nsamples": max(1, min(255, round(period))),
                             "flags": 0x0E | (0x20 if self.magcal_state else 0), "rm3100_cc": 200}
        for unit, prefix in ((0, "ob"), (1, "ib")):
            raw = self.mag_truth(unit, t)
            for axis, val in zip("xyz", raw, strict=True):
                v[f"{prefix}_b_{axis}"] = round(val + g(0, 0.02), 3)
            v[f"{prefix}_rms"] = 0.02
        body = _mtv(_rot(t), tuple(self.field_ut * c / math.sqrt(sum(x * x for x in MAG_FIELD_DIR))
                                   for c in MAG_FIELD_DIR))
        for axis, val in zip("xyz", body, strict=True):
            v[f"body_b_{axis}"] = round(val + g(0, 0.3), 3)
        v["body_rms"] = 0.3
        self._emit_values(APID_MAG, v, 1, now)

    def _att(self, now: float) -> None:
        t = self._sclk(now)
        r = _rot(t)
        w = math.sqrt(max(1e-12, 1.0 + r[0][0] + r[1][1] + r[2][2])) / 2.0
        q = (w, (r[2][1] - r[1][2]) / (4 * w), (r[0][2] - r[2][0]) / (4 * w), (r[1][0] - r[0][1]) / (4 * w))
        v: dict[str, Any] = {"flags": 0x1F, "acc_x": 0.0, "acc_y": 0.0, "acc_z": 1.0,
                             "gyro_x": round(math.degrees(TUMBLE_RAD_S[2]), 1),
                             "gyro_y": round(math.degrees(TUMBLE_RAD_S[1]), 1),
                             "gyro_z": round(math.degrees(TUMBLE_RAD_S[0]), 1),
                             "mag_x": 19.5, "mag_y": 0.5, "mag_z": 44.0, "q_fus_acc": 0.035}
        # q = body -> NED (the simulator's world is NED: MAG_FIELD_DIR points down). The flight
        # fields differ in convention: q_fus = BHI260 device -> ENU (Android world), q_triad =
        # TRIAD NED -> body (attitude.h triad_ned). ENU <-> NED is a 180 deg turn about (1,1,0).
        h = math.sqrt(0.5)
        q_fus = _qmul((0.0, h, h, 0.0), q)
        q_triad = (q[0], -q[1], -q[2], -q[3])
        for i, axis in enumerate("wxyz"):
            v[f"q_fus_{axis}"] = round(q_fus[i] * 16384) / 16384
            v[f"q_triad_{axis}"] = round(q_triad[i] * 16384) / 16384
        self._emit_values(APID_ATT, v, 1, now)

    def _spec(self, now: float) -> None:
        g = self.rng.gauss
        values: dict[str, Any] = {"spec_mode": self.spec_mode if self.spec_mode != 4 else 0, "flags": 0x81,
                                  "gain_code": 2, "int_cycles": 59, "led_cur": 0, "n_frames": 1, "sat_mask": 0,
                                  "temp_0": 24, "temp_1": 25, "temp_2": 25, "bore_zen": 30.0, "bore_az": 120.0,
                                  "spec_class": 2, "spec_conf": 80, "spec_change": 0.5}
        for i, nm in enumerate(spectral.WAVELENGTHS_NM):
            val = max(0.01, SPEC_TEMPLATE_UW[i] * (1 + g(0, 0.01)))
            values[f"spec_{nm}nm"] = val
            values[f"unc_{nm}nm"] = 0.03 * val
            values[f"raw_{nm}nm"] = min(65000, round(val * 600))
        self._emit(APID_SPEC, spectral.pack_spec_v31(values), 1, now)

    def _env(self, now: float) -> None:
        g = self.rng.gauss
        self._emit_values(APID_ENV, {"flags": 0x0F, "p": round(1013.10 + g(0, 0.05), 2),
                                     "t": round(22.1 + g(0, 0.05), 2), "rh": 46.0, "iaq": 42, "iaq_s": 40,
                                     "co2eq": 560, "bvoc": 0.8, "gas": 152000, "accuracy": 3}, 1, now)


__all__ = ["CADENCE", "MAG_OFFSET_UT", "MAG_SOFT_IRON", "SimDownlink", "SimSpacecraft"]
