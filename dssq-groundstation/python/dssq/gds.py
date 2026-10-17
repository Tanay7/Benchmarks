"""DSS-Q Ground Data System - main process (Phase B, v3).

Data flow (one radio thread owns the state; web and front-panel threads read snapshots
or enqueue operator requests under `GroundStation.lock`):

  E22 USB serial --> FrameSynchronizer --> CADU --+
  SX126x RxPacket (Router Bridge, per-packet RSSI/SNR/frequency error, CRC flag) --+
                                                  v
           de-randomize + RS(255,223) --> TM / USLP frame (FECF; CLCW -> FOP-1)
                                                  |  OID: clear-text ident; data VC 0..3:
                                                  v  SDLS TM ProcessSecurity (AE / null SA)
                       PacketExtractor (VC0 RT-ENG, VC1 RT-SCI, VC2 PLAYBACK, VC3 CFDP)
                                                  v
   Decommutator -> limits -> anomalies -> archive / UDP / snapshot; HK beacon, SDLS SN
   self-healing, CMDVER -> FOP, TIMECORR -> SCLK correlation, MAG -> MAGCAL collectors,
   SPEC / SPECBURST -> spectral analytics, CFDP -> class-1 receiver (files, SSR decode)
  Uplink: operator / CardKB / sequence files -> dictionary checks -> FOP-1 (TC or USLP
  frames, SDLS AUTH / AE) -> CLTU -> radio, in the quiet window after every received frame.
  SX126x: ground AFC (link.AfcLoop -> rf_foff), CCSDS TDM per pass (products.TdmRecorder).
  Receiver data logger (contract 39, dssq.datalog): a 1 Hz rx_stats row, archive retention
  and the free-space guard from the housekeeping; CSV export (dssq.export) for the web UI.

Runner (Phase B PB-8.4) drives one bounded iteration per step(): register, radio_read, ingest,
packets, outbox, jobs, afc, tdm, housekeeping, console - each inside ONE documented
error boundary that catches Exception (never BaseException, so SIGTERM / SIGINT / SystemExit
still stop the App), counts it in health.stage_errors, logs the traceback at most once per
stage per 60 s and continues with the next stage: under App Lab's App.run() any escaping
exception would stop the App.

Run (host, from the App's python/ folder):
    python -m dssq.gds --app-data ../data                        # host mode of the App
    python -m dssq.gds --config config/station.toml            # a stand-alone station.toml
    python -m dssq.gds --sim                                     # software spacecraft
--app-data loads <App>/data/station.toml with the App's rules (dssq.config.load_app_config),
so host mode (host/install_station.sh) and the App share one archive - including the SDLS TC
sequence-number file - one SDLS key and one web token.
App Lab: the App entry point python/main.py builds Runner(cfg, bridge=..., mode="applab").
"""
from __future__ import annotations

import argparse
import contextlib
import logging
import math
import re
import signal
import socket
import sqlite3
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__, link
from . import config as station_config
from . import export as csv_export
from . import products
from .archive import Archive
from .ccsds import ASM, CADU_LEN, NUM_DATA_VCS, TC_MAX_FRAME_LEN, VC_NAMES
from .ccsds.cfdp import COMPLETE as CFDP_COMPLETE
from .ccsds.cfdp import Receiver
from .ccsds.packets import PacketExtractor, SpacePacket
from .ccsds.rice import RiceError
from .ccsds.sdls import (AE_HDR_LEN, AE_TAG_LEN, SDLS_SN_RESTORE_WAIT_S, SERVICE_AE, SERVICE_NAMES, SERVICE_NONE,
                         SN_DOC, SdlsSender, SdlsTmReceiver, boot_hk, link_snapshot, load_key, parse_service)
from .ccsds.tc import build_cltu, build_tc_frame, cltu_length, parse_tc_frame
from .ccsds.timecode import SclkCorrelator
from .ccsds.tm import TransferFrame, decode_codeblock
from .cmdparse import (CommandError, ParsedCommand, SequenceError, help_text, pack_seqload, parse,
                       parse_sequence, seqload_commands, tc_packet)
from .datalog import DataLogger
from .decom import Decommutator
from .dictionary import (APID_CFDP, APID_CMDVER, APID_EVR, APID_HK, APID_MAG, APID_RF,
                         APID_RFSCAN, APID_SPEC, APID_SPECBURST, APID_TIMECORR, BEACON_LEVELS, CFDP_STATES,
                         CMD_ERRORS, CMD_SOURCE_AD, CMD_SOURCE_BD, CMD_SOURCE_STORED, CMD_SOURCES, CMD_STAGES,
                         FARM_STATES, FDIR_BITS, FDIR_HIBERNATE,
                         FDIR_TX_INHIBIT, FRAMINGS, MAG_FLAG_BITS, MODES, RADIO_FAULTS, RADIO_MODELS,
                         RESET_BROWNOUT, RESET_CAUSES, RF_FLAG_BITS, SDLS_SERVICES, SDLS_TM_STATES, SENSOR_BITS,
                         SEQ_STATES, decode_bits, decode_mag_bist)
from .fop import Fop1, frame_size_error
from .framesync import RADIO_FAULTS as GROUND_RADIO_FAULTS
from .framesync import FrameSynchronizer, NoiseReply, RadioFault, RawCadu
from .linkmgr import LinkManager
from .magcal import MagCalCollector, MagCalResult, clear_args, magcal_command
from .radio.e22 import NOISE_QUERY
from .radioctl import RadioManager
from .rxstats import E22_RSSI_RESOLUTION_DB, SX126X_RSSI_RESOLUTION_DB, ReceiverStats, Window, at_resolution
from .sensors import NOT_SEPARATED_REASON, SensorBusState, imu_products
from .spectral import LED_CUR_ALLOWED, LED_MAX_CODE, SpectralState

log = logging.getLogger("dssq")

SCHEMA_VERSION = 3
STAGES = ("register", "radio_read", "ingest", "packets", "outbox", "jobs", "afc", "tdm", "housekeeping", "console")
CONFIRM_EXPIRY_S = 120.0
HIBERNATE_LOS_ALARM_S = 7200.0      # two maximum beacon intervals (HIBERNATE beacon_s <= 3600)
HIB_CMD_GRACE_MIN_S = 30.0          # HK generated before a radiated HIBERNATE may still arrive this long
RADIO_LINK_DOWN_RED_S = 30.0
EPOCH_REGRESSION_WINDOW_S = 86_400.0
RECENT_WINDOW_S = 60.0
FRAME_PERIOD_FACTOR = 4.0           # flight kModePeriodX10 SAFE (40) / 10: the longest mode factor
MIN_FRAME_PERIOD_S = 1.5            # flight kMinFramePeriodMs
E22_UART_CADU_S = (CADU_LEN + 1) * 10 / 9600.0   # ground E22 -> USB: one CADU + RSSI octet at 9600 8N1
STATION_IO_STALE_S = 12.0
PANEL_TX_HOLD_S = 1.0                # LED-matrix uplink row: held one status-line period (frontpanel TX_PERIOD_S)
SDLS_SN_FILE = "sdls_sn.txt"          # <archive_dir>/: last SDLS TC sequence number used (F-64)
MISMATCH_FRAMES = 3                 # consecutive BAD_SPI / BAD_IV data frames -> service mismatch
STAGE_LOG_EVERY_S = 60.0
READ_FAIL_SLEEP_AFTER = 5
E22_REOPEN_EVERY_S = 10.0
REGISTER_RETRY_S = 10.0             # Router Bridge registrations retried (front panel, SX126x): App Lab's
                                    # bridge fails fast for frontpanel.APP_RETRY_S after "router unreachable"
CLOSE_BUDGET_S = 3.8
IDENT_FLUSH_S = 0.6                 # shutdown: how long the closing ID may wait for a free MCU TX slot
EVR_LEVEL = {"DIAG": "INFO", "INFO": "INFO", "WARN": "WARN"}       # anything else -> ERROR
SSR_FILE_RE = re.compile(r"ssr_p([0-9]{1,5})_([0-9]{1,10})\.bin")
BRIDGE_ERRORS = (TimeoutError, ConnectionError, ValueError, RuntimeError, OSError)
JSON_MAX_DEPTH = 64
SPACE_PACKET_MAX = 4096
# Amateur station identification of the ground transmitter (StationIdent, docs/09 1.3).
ID_INTERVAL_S = 600.0               # at least every 10 min while transmitting (ITU RR No. 25.9 "at short
                                    # intervals"; e.g. US 47 CFR 97.119(a): every 10 min and at the end)
ID_EARLY_S = 60.0                   # the periodic ID goes this long (or one frame period, if longer) before
                                    # the 10 min are up: it can only be sent in an uplink window
ID_END_IDLE_S = 120.0               # uplink idle this long (and >= the FOP T1): session over, closing ID
ID_NAME_MAX = 16                    # station-name characters in the identification text
ID_LOST_FRAMES = 2                  # downlink frames that may be lost when the periodic ID falls due and a
                                    # window still carries it within the 10 minutes (early margin)
ID_OVERDUE_GUARD_S = 2.0            # more frames lost: the housekeeping sends it this long (plus its own
                                    # air time) before the 10 minutes are up, without a window
# Uplink window plan (UplinkWindowPlan): what the station radiates after a received frame - external
# TC frames, FOP-1 CLTUs, the identification - must end before the spacecraft's next frame.
WINDOW_GUARD_S = 0.2                # GDS reaction time after the frame + the flight's 100 ms receive
                                    # quiet time (kE22RxQuietMs) before it may key its PA again
WINDOW_REACTION_S = 0.1             # the GDS-reaction part of WINDOW_GUARD_S: a window processed later
                                    # than this after its frame's ERT loses the difference
WINDOW_MIN_SPACING_S = 1.4          # windows open at most once per this much frame ERT (flight frames are
                                    # >= kMinFramePeriodMs = 1.5 s apart): with at most 4 TC frames per
                                    # window this bounds the SDLS TC numbers used (sdls.py time floor),
                                    # also against replayed or forged downlink frames
WAKE_MIN_INTERVAL_S = 10.0          # a WAKE (one TC frame, one SDLS number) at most this often
TX_TURNAROUND_S = 0.05              # per ground packet: USB-UART / Router Bridge hand-over, PA ramp
CAD_SYMBOLS = 5                     # SX126x CAD before each ground packet (cad_before_tx): RadioLib
                                    # 7.8.1 CAD on 4 symbols (SX126x_commands.cpp default) + 1 for CadDone
# Radio jobs that must not start on top of the station's own transmissions: WAKE transmits, GSCAN
# retunes the radio (an SX126x MCU would send its queued uplinks on the surveyed channel).
TX_QUIET_JOBS = ("WAKE", "GSCAN")
GSCAN_WAIT_MIN_S = 60.0             # a GSCAN waits for the station transmitter at most max(this, 2 frame
                                    # periods), then is refused: the jobs behind it are not held for ever


# =============================================================================
# JSON-safe deep copy
# =============================================================================
def _wall_clock() -> float:
    """time.time looked up at call time (one clock for every GDS timer, patchable in tests)."""
    return time.time()


class SteadyClock:
    """A monotonic clock for short intervals (the uplink window's busy deadline, radio-job
    waits): it advances with the wall clock's forward steps (_wall_clock, so a test's virtual
    clock drives it like every other GDS timer) and does not move when the wall clock steps
    back - an NTP correction can neither hold the identification and the radio jobs for the
    size of the step nor run them early by it. A forward step only ends a wait early."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_wall: float | None = None
        self._t = 0.0

    def __call__(self) -> float:
        w = _wall_clock()
        with self._lock:
            if self._last_wall is not None and w > self._last_wall:
                self._t += w - self._last_wall
            self._last_wall = w
            return self._t


def json_safe(o: object, _depth: int = 0) -> object:
    """A JSON-serialisable deep copy sharing no container with `o`: NaN / inf -> None,
    tuples / sets / deques -> lists, bytes -> hex, mapping keys -> str, any other object
    -> str. Depth-limited (a cyclic structure cannot recurse forever)."""
    if _depth > JSON_MAX_DEPTH:
        return str(o)
    if o is None or isinstance(o, (bool, int, str)):
        return o
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, Mapping):
        return {str(k): json_safe(v, _depth + 1) for k, v in list(o.items())}
    if isinstance(o, (list, tuple, deque)):
        return [json_safe(v, _depth + 1) for v in list(o)]
    if isinstance(o, (set, frozenset)):
        return [json_safe(v, _depth + 1) for v in sorted(o, key=str)]
    if isinstance(o, (bytes, bytearray, memoryview)):
        return bytes(o).hex()
    return str(o)


class AnomalyDetector:
    """EWMA mean/variance per numeric parameter; flags |z| > threshold after warm-up.
    Complements static limits: it catches 'unusual for this spacecraft' changes."""

    def __init__(self, alpha: float = 0.05, z: float = 6.0, warmup: int = 30, max_params: int = 4096):
        self.alpha, self.z, self.warmup, self.max_params = alpha, z, warmup, max_params
        self.s: dict[str, list[float]] = {}       # name -> [n, mean, var]
        self.recent: deque[dict[str, Any]] = deque(maxlen=50)

    def update(self, t: float, name: str, v: float) -> None:
        if not math.isfinite(v):
            return
        st = self.s.get(name)
        if st is None:
            if len(self.s) >= self.max_params:      # bounded memory
                return
            st = self.s[name] = [0.0, v, 0.0]
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


def ident_text(callsign: str, station_name: str) -> str:
    """"DE <callsign> <name>": the [station] name reduced to ASCII letters, digits, '-', '_'
    and '.' (at most ID_NAME_MAX), so every octet of the identification is printable ASCII."""
    name = "".join(c for c in station_name if c.isascii() and (c.isalnum() or c in "-_."))[:ID_NAME_MAX]
    return f"DE {callsign} {name}" if name else f"DE {callsign}"


class StationIdent:
    """Amateur station identification of the GROUND transmitter (docs/09 1.3).

    Amateur rules require a transmitting station to identify in plain language at short
    intervals and at the end of a communication (e.g. 47 CFR 97.119(a): at least every
    10 minutes and at the end). The GDS calls on_transmit() for every uplink transmission
    (CLTU, external TC frame, wake-up) and due() in every uplink window - and from the
    housekeeping while the downlink is lost, when no window comes; when due() names a
    reason the GDS radiates `payload` through the same radio path as the CLTUs (E22
    transparent packet / SX126x packet) and calls sent().
    The payload is printable ASCII ("DE <callsign> <name>"; every octet < 0x80), so it
    never holds the CLTU start sequence EB 90: the flight's CLTU acquisition finds no
    CLTU, counts the packet in RF cltu_bad and drops it with no EVR, FDIR or FARM-1
    effect (tools/host_test/test_station_id.cpp).
    Rules (now = wall clock, s):
      * nothing is due while every transmission is covered by an identification;
      * "periodic": a transmission is uncovered and the last identification is at least
        ID_INTERVAL_S - early_s old, or there was none yet (the first uplink of a run
        identifies at once, right after its data);
      * "end": a transmission is uncovered and the uplink has been idle for end_idle_s.
    A wall clock that steps back counts as "the interval is over" (identify rather than
    miss one)."""

    def __init__(self, callsign: str, station_name: str) -> None:
        self.callsign = callsign
        self.text = ident_text(callsign, station_name)
        self.payload = self.text.encode("ascii")
        self.count = 0
        self.last_t: float | None = None           # last identification radiated
        self.last_reason: str | None = None
        self.uncovered_t: float | None = None      # first transmission after the last identification
        self.last_tx_t: float | None = None        # last uplink transmission (not an identification)

    def on_transmit(self, now: float) -> None:
        if self.uncovered_t is None:
            self.uncovered_t = now
        self.last_tx_t = now

    @property
    def pending(self) -> bool:
        return self.uncovered_t is not None

    def _interval_over(self, now: float, early_s: float) -> bool:
        last = self.last_t
        return last is None or now < last or now - last >= ID_INTERVAL_S - min(max(early_s, 0.0), ID_INTERVAL_S)

    def due_if_transmitting(self, now: float, early_s: float = ID_EARLY_S, end_idle_s: float = ID_END_IDLE_S) -> bool:
        """True when an identification is due now, or becomes due as soon as this window
        transmits anything (the periodic interval is over): the window plan books it first."""
        return self.due(now, early_s, end_idle_s) is not None or self._interval_over(now, early_s)

    def due(self, now: float, early_s: float = ID_EARLY_S, end_idle_s: float = ID_END_IDLE_S) -> str | None:
        if self.uncovered_t is None:
            return None
        if self._interval_over(now, early_s):
            return "periodic"
        tx = self.last_tx_t
        if tx is not None and (now < tx or now - tx >= end_idle_s):
            return "end"
        return None

    def sent(self, now: float, reason: str) -> None:
        self.count += 1
        self.last_t = now
        self.last_reason = reason
        self.uncovered_t = None

    def snapshot(self) -> dict[str, Any]:
        """Snapshot link.station_id."""
        return {"callsign": self.callsign, "text": self.text, "count": self.count, "last_t": self.last_t,
                "last_reason": self.last_reason, "pending_since": self.uncovered_t}


class UplinkWindowPlan:
    """The air-time budget of one uplink window. take(octets) books one ground packet's
    transmitter time (GroundStation._tx_cost_s) and returns False, booking nothing, when it no
    longer fits: the caller then leaves that packet for a later window."""

    def __init__(self, budget_s: float, cost_s: Callable[[int], float]) -> None:
        self.budget_s = budget_s
        self.left_s = budget_s
        self._cost_s = cost_s

    def take(self, octets: int) -> bool:
        c = self._cost_s(octets)
        if c > self.left_s:
            return False
        self.left_s -= c
        return True

    @property
    def used_s(self) -> float:
        return self.budget_s - self.left_s


def _int_or_none(v: object) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _label(table: Mapping[int, str], v: object) -> str:
    """Enumeration label of a decoded field (the number itself when unknown)."""
    code = _int_or_none(v)
    return table.get(code, str(v)) if code is not None else str(v)


def _hamming32(a: bytes, b: bytes) -> int:
    return bin(int.from_bytes(a, "big") ^ int.from_bytes(b, "big")).count("1")


# =============================================================================
# Ground station state
# =============================================================================
class GroundStation:
    """All ground state. Every public method takes `lock` itself, so the web, front-panel
    and radio threads call them directly."""

    def __init__(self, cfg: dict[str, Any], archive: bool = True, mode: str = "host"):
        self.cfg = cfg
        self.mode = mode
        self.lock = threading.RLock()
        self.t_start = time.time()
        g = cfg["gds"]
        self.archive_enabled = archive
        self.archive_dir = Path(g["archive_dir"])
        self.archive = Archive(self.archive_dir, enabled=archive)
        self.logger = DataLogger(self.archive, g)
        self.exports_dir = self.archive.exports_dir
        self._logger_t: float | None = None
        self._export_lock = threading.Lock()             # one CSV export at a time (web)
        self.files_dir = station_config.files_path(cfg)
        self.tdm_dir = self.archive_dir / "tdm"
        self.settings = link.RadioSettings.from_config(cfg["radio"])
        self.sync = FrameSynchronizer(rssi_byte=bool(cfg["radio"]["rssi_byte"]))
        self.rx = ReceiverStats(cfg)
        self.extract = {vc: PacketExtractor(vc) for vc in range(NUM_DATA_VCS)}
        self.vc_info: dict[str, dict[str, Any]] = {
            name: {"frames": 0, "lost": 0, "last_vcfc": None, "sdls_rejected": 0, "last_ert": None}
            for name in [*(VC_NAMES[v] for v in range(NUM_DATA_VCS)), "OID"]}
        self.decom = Decommutator()
        self.corr = SclkCorrelator()
        self.anomaly = AnomalyDetector()
        self.events: deque[dict[str, Any]] = deque(maxlen=400)
        self.health: dict[str, Any] = {"stage_errors": {}, "last_error": None, "tdm_rejected": 0,
                                       "archive_write_errors": 0, "archive_last_error": None}
        self._archive_errors_seen = 0
        self._archive_error_t: float | None = None
        self._stage_error_times: deque[float] = deque(maxlen=256)
        self._limited: dict[str, float] = {}
        # ---- SDLS (contract 5; F-64) ----
        sd = cfg["sdls"]
        self.key_file = str(sd["key_file"])
        self.key_error: str | None = None
        key: bytes | None = None
        try:
            key = load_key(self.key_file)
        except (OSError, ValueError) as e:
            self.key_error = f"SDLS key file unreadable: {e}"
        self.key_loaded = key is not None
        if key is None and self.key_error is None:
            self.key_error = f"SDLS key file not found: {self.key_file}"
        self.sdls_tc_configured = parse_service(sd["tc_service"])
        self.sdls_tm_configured = parse_service(sd["tm_service"])
        # The TC sequence number and the TM anti-replay state are security state, not archive data
        # (docs/17 A3): they stay in <archive_dir> also with --no-archive. The software simulator
        # keeps its own in memory, with or without an archive: nothing it "radiates" uses a real
        # number, and its spacecraft's HK must never end the real station's hold or write the
        # real files (it would set a counter that the real key may already have used).
        keep_security_state = mode != "sim"
        sn_file = self.archive_dir / SDLS_SN_FILE if keep_security_state else None
        # Nonce safety: an existing key whose TC sequence-number file is missing (a new archive
        # folder, another station, a restored backup) may already have been used with higher
        # numbers - under AE the number is the GCM nonce, under AUTH a reused number makes a
        # recorded unaccepted frame valid again. Nothing on disk can tell a new key from a used
        # one, so the uplink is held (require_file) until an HK reports the spacecraft's
        # sdls_last_sn; the counter then continues above every number the key can have used
        # (SdlsSender.observe_spacecraft_sn: the time floor; docs/17 7.6).
        self.sn_file_fresh = (sn_file is not None and key is not None and self.sdls_tc_configured != SERVICE_NONE
                              and not sn_file.exists())
        self.sdls_tc = SdlsSender(key, int(sd["spi"]), sn_file, service=self.sdls_tc_configured, require_file=True,
                                  clock=_wall_clock)
        tm_ae = key is not None and self.sdls_tm_configured == SERVICE_AE
        # Without TM protection no HK is authenticated: an unprotected one may then end the hold
        # (it can only choose where the counter continues, observe_spacecraft_sn).
        self._sn_release_unprotected = not tm_ae
        self.sdls_tm = SdlsTmReceiver(key if tm_ae else None, SERVICE_AE if tm_ae else SERVICE_NONE,
                                      bool(sd["accept_null"]),
                                      state_file=self.archive_dir / "sdls_tm_replay.txt"
                                      if keep_security_state and tm_ae else None)
        self.tm_geometry = (AE_HDR_LEN, AE_TAG_LEN) if tm_ae else (0, 0)
        self._persist_error_seen: str | None = None
        self._sdls_tm_rejects: deque[float] = deque(maxlen=256)
        self._mismatch_run = 0
        self.mismatch_suspected = False
        self._epoch_regressed_seen = 0
        self._epoch_regressed_times: deque[float] = deque(maxlen=256)
        # ---- commanding ----
        c = cfg["commanding"]
        self.fop = Fop1(float(c["t1_s"]), int(c["tx_limit"]), int(c["window"]), float(c["verify_timeout_s"]),
                        sdls=self.sdls_tc, framing=str(cfg["link"]["tc_framing"]))
        self._timers_derived = False
        self.pending_confirm: dict[int, dict[str, Any]] = {}
        self._confirm_ids = 1
        self.console_lines: deque[tuple[float, str]] = deque(maxlen=30)
        self.outbox: deque[bytes] = deque()
        self.external_tc: deque[bytes] = deque(maxlen=64)
        self.station_id = StationIdent(str(cfg["station"]["callsign"]), str(cfg["station"]["name"]))
        # End of the last window's planned transmissions, on the monotonic clock `mono` (a wall
        # clock step back must not hold the LOS identification and the radio jobs, SteadyClock).
        self.mono: Callable[[], float] = SteadyClock()
        self._uplink_busy_until = -math.inf
        self._uplink_tx_until = -math.inf            # planned end of the last transmission handed to the radio
        self._last_window_ert = -math.inf                 # ERT (steady clock) of the last window opened
        self._last_wake = -math.inf                       # when the last WAKE was radiated (steady clock)
        self._job_wait: tuple[tuple[str, Any], float] | None = None   # (head radio job, waiting since)
        # ---- telemetry state ----
        self.tlm: dict[str, dict[str, Any]] = {}
        self.apid_stats: dict[int, dict[str, Any]] = {}
        self.evrs: deque[dict[str, Any]] = deque(maxlen=200)
        self.sensor_bus = SensorBusState()          # D-73: instrument buses from the flight's EVRs
        self.partition = 0
        self.last_sclk: float | None = None
        self.last_sclk_ert: float | None = None
        self.ert_by_key: dict[int, float] = {}        # TIMECORR reference key (u8) -> ERT
        self.timecorr_key_name = "MCFC"
        self.owlt_s = float(cfg["spacecraft"]["distance_km"]) / (link.C_M_S / 1000.0)
        self.beacon: dict[str, Any] = {"level": None, "name": None, "t": None, "since": None,
                                       "history": deque(maxlen=50), "hibernating": False,
                                       "hibernate_expected": False}
        # A radiated HIBERNATE is confirmed only by the first beacon (one frame per beacon_s,
        # up to 3600 s later): until then the GDS expects hibernation, so the LOS that follows
        # is not an alarm. (t radiated) of that command, None when nothing is expected.
        self._hib_cmd_t: float | None = None
        self.model_seen: str | None = None
        self.model_mismatch = False
        self.framing_seen: dict[str, Any] = {"TM": 0, "USLP": 0, "last": None}
        self.ident: dict[str, Any] = {"text": None, "t": None, "count": 0}
        # ---- radio ----
        self.radio_backend: Any = None
        self.radio_mgr: RadioManager | None = None
        self.radio_unavailable: str | None = None      # reason the backend is missing (RED alarm)
        self._radio_connected: bool | None = None
        self._radio_down_since: float | None = None
        self.radio_jobs: deque[tuple[str, Any]] = deque(maxlen=64)
        self.ground_radio: dict[str, Any] | None = None
        self.ground_radio_fault: dict[str, Any] | None = None
        self.last_noise_query = 0.0
        self.noise_wait_until = 0.0
        self.allowed_channels = list(cfg["radio"]["allowed_channels"])
        lm = cfg["linkmgr"]
        self.link_mgr = LinkManager(float(lm["target_margin_db"]), float(lm["period_s"]), float(lm["holdoff_s"]),
                                    lm.get("min_rate"), int(lm["max_rate"]), model=self.settings.model,
                                    measured=self.settings.measured, rx_boost=self.settings.rx_boost)
        self.link_mgr.mode = str(lm["mode"]).upper()
        self.link_change: dict[str, Any] | None = None
        # ---- AFC (SX126x; contract 24.8, F-32) ----
        self.afc = link.AfcLoop.from_settings(self.settings, enabled=bool(cfg["radio"]["sx126x"]["afc"]))
        self.afc_out: deque[int] = deque(maxlen=8)
        self.afc_events: deque[dict[str, Any]] = deque(maxlen=20)
        self._afc_no_foff_logged = False
        # ---- CFDP (contract 7, F-47) ----
        cf = cfg["cfdp"]
        self.cfdp = Receiver(self.files_dir, inactivity_s=float(cf["inactivity_s"]), check_s=float(cf["check_s"]),
                             max_file_bytes=int(float(cf["max_file_mib"]) * 1024 * 1024), clock=_wall_clock)
        self._ssr_decoded: set[tuple[int, int]] = set()
        self._ssr_queue: deque[tuple[tuple[int, int], str, str]] = deque(maxlen=16)
        # ---- TDM (contract 24.7, F-33) ----
        self.tdm = products.TdmRecorder(cfg["station"]["name"], cfg["spacecraft"]["name"])
        self._tdm_pending: products.TdmRecorder | None = None
        self._tdm_warned = False
        self.tdm_state: dict[str, Any] = {"last_file": None, "last_write_t": None}
        self._tdm_files_cache: tuple[float, list[dict[str, Any]]] = (-1e18, [])
        # ---- science ----
        self.spectral = SpectralState.from_config(cfg, archive=archive)
        fu = cfg["science"].get("field_ut")
        self.field_ut: float | None = float(fu) if isinstance(fu, (int, float)) and not isinstance(fu, bool) else None
        self.magcal_units: dict[int, dict[str, Any]] = {
            u: {"collector": None, "collecting": False, "fit": None, "command": None, "averaged_skipped": 0}
            for u in (0, 1)}
        self.seq_file: dict[str, Any] | None = None
        self.seq_uplink_state: dict[str, Any] = {"pending_confirm_id": None, "queued_ids": [], "first_slot": None}
        # ---- station I/O MCU (display/frontpanel.py) ----
        # RCU (D-78): rcu_configured = [gds] rcu; rcu_last / rcu_last_t = what the MCU reports
        # in its hello about its last rcu_mode() call ("OK" / "FAILED" at that time, "NEVER"
        # since its boot; None = no hello yet, or a sketch that does not report it).
        self.station_io: dict[str, Any] = {"connected_t": None, "fw": "", "kb": False, "input": "",
                                           "last_status_line": None, "beacon_sent": None,
                                           "rcu_configured": bool(cfg["gds"]["rcu"]), "rcu_last": None,
                                           "rcu_last_t": None}
        # ---- alarm ----
        self.alarm = "NONE"
        self.alarm_reasons: list[str] = []
        self.alarm_red_count = 0                         # alarm_reasons[:alarm_red_count] are RED, the rest YELLOW
        self._hk_1hz_t = -1e18
        self._xtce_cache: tuple[tuple[str, str], bytes] | None = None
        self._xtce_lock = threading.Lock()
        # ---- interop UDP ----
        self._udp: socket.socket | None = None
        io = cfg["interop"]
        self._fwd_frames = self._parse_addr(io["tm_frames_udp"])
        self._fwd_packets = self._parse_addr(io["tm_packets_udp"])
        if self._fwd_frames or self._fwd_packets:
            self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.event("INFO", "GDS", f"DSS-Q ground station {__version__} started ({mode}), radio "
                   f"{self.settings.model.name}{' (auto: follows the spacecraft)' if self.settings.model_auto else ''} "
                   f"CH{self.settings.channel} code {self.settings.rate_code}; SDLS TC "
                   f"{self.sdls_tc.service_name} / TM {SERVICE_NAMES[self.sdls_tm.service]}; tc_framing "
                   f"{self.fop.framing}, tm_framing {cfg['link']['tm_framing']}")
        if self.key_error and (self.sdls_tc_configured or self.sdls_tm_configured):
            self.event("WARN", "SDLS", f"{self.key_error}: uplink and downlink run without SDLS (NONE)")
        if self.sn_file_fresh:
            self.event("WARN", "SDLS", f"no SDLS TC sequence-number file {sn_file} for the key {self.key_file}: if "
                       "this key was used before - by the App Lab App or host mode with another archive folder, by "
                       "another station, or before the archive was restored without it - its numbers are unknown. "
                       f"The TC uplink is held until {'an SDLS-protected' if tm_ae else 'an'} HK reports the "
                       "spacecraft's sdls_last_sn; the counter then continues above every number the key can have "
                       f"used (the time floor, {SN_DOC}). A new key never used before needs nothing else.")
        if self.sdls_tc.uplink_blocked:                  # a sender without a key protects nothing: not blocked
            self.event("ERROR", "SDLS", str(self.sdls_tc.blocked))
        if self.sdls_tm.load_warning:
            self.event("WARN", "SDLS", self.sdls_tm.load_warning)
        for ev in self.spectral.drain_events():
            self.event(ev["level"], ev["source"], ev["text"])
        if self.field_ut is None:
            self.event("INFO", "MAGCAL", "[science] field_ut not set: the MAGCAL fit is disabled until it is")
        if self.station_id.callsign == station_config.CALLSIGN_PLACEHOLDER:
            self.event("WARN", "IDENT", f"[station] callsign is the placeholder {self.station_id.callsign}: set your "
                       "amateur callsign in station.toml before transmitting (the uplink identifies with it)")

    @staticmethod
    def _parse_addr(s: str) -> tuple[str, int] | None:
        if not s:
            return None
        host, port = s.rsplit(":", 1)
        return host, int(port)

    # ------------------------------------------------------------------ events
    def event(self, level: str, source: str, text: str, t: float | None = None) -> None:
        t = time.time() if t is None else t
        try:
            line = self.archive.event(t, level, source, text)
        except (OSError, ValueError, sqlite3.Error) as e:   # archive closed (writes are best-effort)
            line = f"{level} {source} {text} (archive: {e})"
        self.events.append({"t": t, "level": level, "source": source, "text": text})
        log.log(logging.WARNING if level in ("WARN", "ERROR") else logging.INFO, line)

    def _limited_event(self, key: str, every_s: float, level: str, source: str, text: str,
                       t: float | None = None) -> None:
        now = time.time()
        last = self._limited.get(key)
        if last is None or now - last >= every_s or now < last:
            self._limited[key] = now
            self.event(level, source, text, t)

    def console(self, text: str) -> None:
        self.console_lines.append((time.time(), text))

    # ------------------------------------------------------------- radio wiring
    def attach_radio(self, backend: Any, radio_mgr: RadioManager | None, unavailable: str | None = None) -> None:
        with self.lock:
            self.radio_backend = backend
            self.radio_mgr = radio_mgr
            self.radio_unavailable = unavailable
            if radio_mgr is not None:
                self.radio_jobs.append(("VERIFY", None))
            if unavailable:
                self.event("ERROR", "RADIO", f"radio backend unavailable: {unavailable}")

    def take_outbox(self) -> list[bytes]:
        with self.lock:
            out = list(self.outbox)
            self.outbox.clear()
            return out

    def take_afc_out(self) -> list[int]:
        with self.lock:
            out = list(self.afc_out)
            self.afc_out.clear()
            return out

    def note_no_rf_foff(self) -> None:
        """The radio backend has no rf_foff: reported once (corrections confirm as no answer)."""
        with self.lock:
            if not self._afc_no_foff_logged:
                self._afc_no_foff_logged = True
                self.event("WARN", "AFC", "the radio backend provides no rf_foff: the AFC cannot steer the station "
                           "(corrections are reported as 'no answer')")

    def afc_confirm(self, hz: int, result: int | None) -> None:
        """Result of rf_foff(hz) from the radio thread; None (no answer) -> AFC_RPC_NO_ANSWER."""
        with self.lock:
            self.afc.confirm(hz, link.AFC_RPC_NO_ANSWER if result is None else result, time.time())
            self._drain_afc_events()

    def _drain_afc_events(self) -> None:
        for t, level, text in self.afc.pop_events():
            self.afc_events.append({"t": t, "level": level, "text": text})
            self.event(level, "AFC", text, t if t > 0 else None)

    # ------------------------------------------------------------- byte input
    def ingest(self, data: bytes, now: float | None = None) -> None:
        """E22 byte stream ([CADU][RSSI] units, noise replies, fault reports)."""
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
                    self.rx.on_reception(item.ert, item.rssi_dbm)
                    self._on_cadu(item.cadu, item.ert, item.rssi_dbm, item.asm_errors)

    def ingest_packet(self, p: link.RxPacket) -> None:
        """One packet from a packet-mode radio (SX126x): per-packet RSSI / SNR / frequency
        error; AFC; CRC-failed CADU-length payloads go through RS (contract 20 'RS rescue'),
        which never bypasses RS / FECF / SDLS."""
        with self.lock:
            self.rx.on_reception(p.ert, p.rssi_dbm, p.snr_db, p.freq_err_hz, p.crc_ok)
            corr = self.afc.on_packet(p.ert, p.freq_err_hz, p.snr_db, p.crc_ok)
            if corr is not None:
                self.afc_out.append(corr)
            self._drain_afc_events()
            if len(p.data) != CADU_LEN:
                if p.data:
                    self.rx.foreign += 1                    # a LoRa packet that is not one CADU
                self.archive.frame(ert=p.ert, rssi=p.rssi_dbm, snr=p.snr_db, ok=0,
                                   reason="CRC error, payload discarded" if not p.crc_ok else
                                   f"{len(p.data)}-octet packet, not a CADU")
                return
            self._on_cadu(p.data, p.ert, p.rssi_dbm, _hamming32(p.data[:4], ASM), rescue=not p.crc_ok,
                          snr=p.snr_db, ferr=p.freq_err_hz)

    def _on_radio_fault(self, f: RadioFault) -> None:
        # The station E22-400T37S repeats the report every 500 ms while the fault lasts:
        # log the first one and then at most once a minute.
        last = self.ground_radio_fault
        text = GROUND_RADIO_FAULTS.get(f.code, f"code {f.code}")
        if not last or last["code"] != f.code or f.ert - last["logged"] > 60:
            self.event("ERROR", "RADIO", f"ground {self.settings.model.name} reports {text} - its transmitter is "
                       "disabled until the condition clears (check the station supply / cooling)", f.ert)
            self.ground_radio_fault = {"code": f.code, "text": text, "logged": f.ert, "t": f.ert}
        else:
            last["t"] = f.ert

    def _tdm_add(self, ert: float, rssi_raw: float | None, snr: float | None, ferr: float | None) -> None:
        """One TdmRecord per received spacecraft frame; a bad station clock never stops reception."""
        packet = self.settings.model.packet_mode
        bw = self.rx.noise_bw[0]
        try:
            rec = products.TdmRecord(
                t_utc=ert - float(self.cfg["radio"]["radio_latency_s"]),
                rx_power_dbm=self.rx.calibrated(rssi_raw),
                snr_db=snr if packet else None,
                freq_offset_hz=self.afc.residual_hz(ferr) if packet and ferr is not None else None,
                afc_correction_hz=float(self.afc.correction_hz) if packet else 0.0,
                nominal_freq_hz=self.settings.freq_hz, noise_bw_hz=bw)
        except ValueError as e:
            self.health["tdm_rejected"] += 1
            if not self._tdm_warned:
                self._tdm_warned = True
                # Name the real cause: on the first bench link an E22 RSSI byte at the end of its
                # range (-256 dBm) was reported as a clock fault (record 2026-10-13-A).
                why = "station clock outside 2000..2100 (NTP not synchronised?)" if str(e).startswith("t_utc") \
                    else "a measurement outside the TDM limits"
                self.event("WARN", "TDM", f"{why}: TDM records skipped ({e})")
            return
        self.tdm.add(rec)

    def _on_cadu(self, cadu: bytes, ert: float, rssi_raw: float | None, asm_errors: int, rescue: bool = False,
                 snr: float | None = None, ferr: float | None = None) -> None:
        res = decode_codeblock(cadu[len(ASM):], *self.tm_geometry, framing=str(self.cfg["link"]["tm_framing"]))
        self.archive.raw_cadu(ert, rssi_raw, cadu)
        self.rx.on_codeblock(ert, res.rs_corrected, asm_errors, res.ok, res.reason, rescue)
        if not res.ok or res.frame is None:
            self.archive.frame(ert=ert, rs=res.rs_corrected, rssi=rssi_raw, snr=snr, ok=0,
                               reason=res.reason + (" (LoRa CRC error)" if rescue else ""))
            if res.rs_corrected < 0 and not rescue:
                self._limited_event("rs_uncorrectable", 60.0, "WARN", "DECODER",
                                    "RS uncorrectable codeblock(s) (logged at most once a minute)", ert)
            return
        f = res.frame
        self.framing_seen[f.format] = self.framing_seen.get(f.format, 0) + 1
        self.framing_seen["last"] = f.format
        self.timecorr_key_name = "MCFC" if f.format == "TM" else "VC0 VCF count"
        key = f.timecorr_key
        if key is not None:
            self.ert_by_key[key] = ert
        if f.is_oid:
            info = self.vc_info["OID"]
            text = f.ident
            if text is not None:
                self.ident.update(text=text, t=ert, count=self.ident["count"] + 1)
            self._frame_done(f, ert, res.rs_corrected, rssi_raw, snr, ferr, info, 0, 0, clcw_ok=True)
            return
        info = self.vc_info[VC_NAMES.get(f.vcid, "OID")] if f.vcid < NUM_DATA_VCS else self.vc_info["OID"]
        opened: TransferFrame | None = f
        protected = False
        if f.secured:
            opened, r = self.sdls_tm.open_frame(f)
            if opened is None:
                self._sdls_reject(r.verdict, f, ert, info)
                self._frame_done(f, ert, res.rs_corrected, rssi_raw, snr, ferr, info, 0, 0, clcw_ok=False,
                                 reason=f"SDLS {r.verdict}")
                return
            protected = r.verdict == "OK"
        self._mismatch_run = 0
        pkts: list[SpacePacket] = []
        ex = self.extract.get(f.vcid)
        if ex is not None and opened is not None and opened.fhp is not None:
            # I2: the frame's SDLS status goes with its octets; a status change on the VC is a
            # discontinuity (partial packet dropped), and a packet is protected only when
            # EVERY octet came from an AE-verified frame (packet spanning a null-SA frame: not).
            pkts = ex.push(opened.vcfc, opened.fhp, opened.data, protected=protected)
        data_octets = sum(len(p.raw) for p in pkts)
        field = len(opened.data) if opened is not None else 0
        self._frame_done(f, ert, res.rs_corrected, rssi_raw, snr, ferr, info, data_octets, field, clcw_ok=True)
        for p in pkts:
            self._on_packet(p, f.vcid, ert, p.protected is True)

    def _sdls_reject(self, verdict: str, f: TransferFrame, ert: float, info: dict[str, Any]) -> None:
        self.rx.on_sdls_reject(verdict)
        info["sdls_rejected"] += 1
        self._sdls_tm_rejects.append(ert)
        if verdict in ("BAD_SPI", "BAD_IV"):
            self._mismatch_run += 1
            if self._mismatch_run >= MISMATCH_FRAMES and not self.mismatch_suspected:
                self.mismatch_suspected = True
                self.event("WARN", "SDLS", f"{self._mismatch_run} consecutive data frames without a valid AE header "
                           "(BAD_SPI / BAD_IV): the spacecraft probably runs SDLS TM NONE - check [sdls] "
                           "tm_service against the flight VGQ_SDLS_TM", ert)
        else:
            self._mismatch_run = 0
        self._limited_event(f"sdls_{verdict}", 10.0, "WARN", "SDLS",
                            f"TM frame VC{f.vcid} rejected by SDLS: {verdict} (dropped, never decoded)", ert)

    def _frame_done(self, f: TransferFrame, ert: float, rs: int, rssi_raw: float | None, snr: float | None,
                    ferr: float | None, info: dict[str, Any], data_octets: int, field_octets: int, *,
                    clcw_ok: bool, reason: str = "") -> None:
        """Book-keeping common to every frame that passed RS and the FECF."""
        was_los = self.rx.los
        self.rx.on_frame(ert, f, rs, data_octets, field_octets)
        if was_los and not self.rx.los:                 # AOS: the new pass's TDM starts with THIS frame
            self.tdm.clear()
            self._tdm_warned = False
        info["frames"] += 1
        info["last_vcfc"] = f.vcfc
        info["last_ert"] = ert
        self.archive.frame(ert=ert, mcfc=f.mcfc, vcid=f.vcid, vcfc=f.vcfc, fhp=f.fhp, rs=rs, rssi=rssi_raw,
                           snr=snr if snr is not None else self.rx.series["snr_est"][-1], ok=1, reason=reason)
        if self._fwd_frames and self._udp is not None:
            with contextlib.suppress(OSError):
                self._udp.sendto(f.raw, self._fwd_frames)
        # The CLCW of a data frame that failed SDLS is not trusted (a forged frame must not
        # drive COP-1); OID and clear frames carry the spacecraft's report as on any link.
        if clcw_ok and f.clcw is not None:
            self.fop.on_clcw(f.clcw, ert)
        self._tdm_add(ert, rssi_raw, snr, ferr)
        self._uplink_window(ert)                         # the spacecraft just finished transmitting

    # ----------------------------------------------------------------- packets
    def _apid_stats(self, p: SpacePacket, vcid: int, now: float) -> dict[str, Any]:
        st = self.apid_stats.get(p.apid)
        if st is None:
            st = self.apid_stats[p.apid] = {"count": 0, "seq_gaps": 0, "lost": 0, "last_seq": None,
                                            "last_ert": None, "iat_s": None, "latency": Window(600.0, 2000),
                                            "size": len(p.raw), "vcid": vcid}
        realtime = vcid != 2
        if realtime and st["last_seq"] is not None:
            gap = (p.seq_count - st["last_seq"] - 1) & 0x3FFF
            if gap:
                st["seq_gaps"] += 1
                st["lost"] += gap
        if realtime:
            if st["last_ert"] is not None:
                dt = now - st["last_ert"]
                st["iat_s"] = dt if st["iat_s"] is None else st["iat_s"] + 0.2 * (dt - st["iat_s"])
            st["last_seq"], st["last_ert"] = p.seq_count, now
        st["count"] += 1
        st["size"], st["vcid"] = len(p.raw), vcid
        return st

    def _on_packet(self, p: SpacePacket, vcid: int, now: float, protected: bool) -> None:
        if self._fwd_packets and self._udp is not None:
            with contextlib.suppress(OSError):
                self._udp.sendto(p.raw, self._fwd_packets)
        realtime = vcid != 2
        st = self._apid_stats(p, vcid, now)
        sclk = p.time.seconds if p.time else None
        scet = self.corr.scet(sclk) if sclk is not None else None
        if sclk is not None and realtime:
            self.last_sclk, self.last_sclk_ert = sclk, now
            if scet is not None:
                st["latency"].add(now, now - scet)
        name = self.decom.packet_name(p.apid)
        if p.apid == APID_CFDP:
            for e in self.cfdp.receive(p.user_data, now):
                self.event(e["level"], e["source"], e["text"], e["t"])
            self._queue_ssr_files()
            self.archive.packet(now, scet, p.apid, name, p.seq_count, sclk, self.partition, vcid, p.raw, {}, {})
            return
        try:
            values = self.decom.decode(p)
        except RiceError as e:                           # a corrupt CCSDS 121.0 SPECBURST payload
            self.rx.rice_errors += 1
            self._limited_event("rice", 10.0, "WARN", "DECOM", f"{name}: Rice decode failed ({e}); packet dropped",
                                now)
            self.archive.packet(now, scet, p.apid, name, p.seq_count, sclk, self.partition, vcid, p.raw, {}, {})
            return
        except (KeyError, ValueError) as e:
            self.rx.decom_errors += 1
            self._limited_event(f"decom_{p.apid}", 10.0, "WARN", "DECOM", f"{name}: {e}", now)
            self.archive.packet(now, scet, p.apid, name, p.seq_count, sclk, self.partition, vcid, p.raw, {}, {})
            return
        if p.apid == APID_EVR:
            self._on_evr(values, now, scet, sclk, vcid)
            self.archive.packet(now, scet, p.apid, name, p.seq_count, sclk, self.partition, vcid, p.raw, {}, {})
            return
        if p.apid == APID_SPECBURST:
            self.spectral.on_specburst(values, scet if scet is not None else now)
        elif p.apid == APID_SPEC:
            self.spectral.on_spec(values, scet if scet is not None else now, sclk=sclk, partition=self.partition)
        for ev in self.spectral.drain_events():
            self.event(ev["level"], ev["source"], ev["text"], ev["t"])
        limits = self.decom.limits(p.apid, values)
        if realtime:
            for k, state in limits.items():
                prev = self.tlm.get(name, {}).get("limits", {}).get(k, "OK")
                if state != prev:
                    self.event("WARN" if state != "OK" else "INFO", "LIMITS", f"{name}.{k} = {values.get(k)} {state}",
                               now)
            self.tlm[name] = {"apid": p.apid, "values": values, "limits": limits, "ert": now, "scet": scet,
                              "sclk": sclk, "seq": p.seq_count, "vcid": vcid, "units": self.decom.units(p.apid)}
            if p.apid not in (APID_SPECBURST, APID_RFSCAN):
                for k, v in values.items():
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        self.anomaly.update(now, f"{name}.{k}", float(v))
        self.archive.packet(now, scet, p.apid, name, p.seq_count, sclk, self.partition, vcid, p.raw,
                            values if p.apid not in (APID_SPECBURST,) else {}, limits)
        if not realtime:
            return
        if p.apid == APID_HK:
            self._on_hk(values, now, protected, scet)
        elif p.apid == APID_RF:
            self._on_rf(values, now)
        elif p.apid == APID_CMDVER:
            self._on_cmdver(values, now)
        elif p.apid == APID_TIMECORR:
            self._time_correlate(values, now)
        elif p.apid == APID_MAG and vcid == 1:
            self._on_mag(values)

    def _on_evr(self, values: dict[str, Any], now: float, scet: float | None, sclk: float | None, vcid: int) -> None:
        rec = {"ert": now, "scet": scet, "sclk": sclk, "severity": values["severity"],
               "event_id": values["event_id"], "text": values["text"], "vcid": vcid}
        self.evrs.append(rec)
        tag = " (SSR playback)" if vcid == 2 else ""
        self.event(EVR_LEVEL.get(str(values["severity"]), "ERROR"), "S/C EVR",
                   f"[{values['event_id']:#06x}] {values['text']}{tag}", now)
        self.spectral.on_evr(values["event_id"], str(values["severity"]), values["text"], scet or now)
        self.sensor_bus.on_evr(values["event_id"], str(values["text"]), now, playback=vcid == 2)

    def _on_hk(self, v: dict[str, Any], now: float, protected: bool, scet: float | None = None) -> None:
        part = _int_or_none(v.get("sclk_partition"))
        if part is not None:
            self.partition = part
        level = _int_or_none(v.get("beacon_level"))
        b = self.beacon
        b["hibernating"] = bool((_int_or_none(v.get("fdir_flags")) or 0) & FDIR_HIBERNATE)
        hc = self._hib_cmd_t
        # An HK generated before the command took effect (it can wait in VC0 for the first
        # beacon frame) must not cancel the expectation: judged by its SCET when correlated.
        made = now if scet is None else scet
        if hc is not None and (b["hibernating"] or made - hc > max(HIB_CMD_GRACE_MIN_S, 4 * self._frame_period_s())):
            self._hib_cmd_t = None                      # the HK bit governs from now on
            b["hibernate_expected"] = False
        if level is not None and level != b["level"]:
            prev = b["level"]
            name = BEACON_LEVELS.get(level, f"level {level}")
            b.update(level=level, name=name, since=now)
            b["history"].append([now, level])
            if prev is not None:
                lvl = "INFO" if level < prev else ("ERROR" if level >= 3 else "WARN")
                self.event(lvl, "BEACON", f"spacecraft beacon {BEACON_LEVELS.get(prev, prev)} -> {name}", now)
            elif level > 0:
                self.event("ERROR" if level >= 3 else "WARN", "BEACON", f"spacecraft beacon {name}", now)
        b["t"] = now
        sn = _int_or_none(v.get("sdls_last_sn"))
        if sn is not None:
            was_blocked = self.sdls_tc.uplink_blocked
            uptime = _int_or_none(v.get("uptime_s"))
            if was_blocked and sn == 0 and boot_hk(uptime) and (protected or self._sn_release_unprotected):
                self._limited_event("sdls_boot_hk", 60.0, "WARN", "SDLS",
                                    f"an HK made {uptime} s after a spacecraft boot reports sdls_last_sn 0 (the "
                                    "flight's TC counter is not restored by the EGSE yet): it does not end the TC "
                                    f"uplink hold - an HK from {SDLS_SN_RESTORE_WAIT_S} s after the boot, or one "
                                    "reporting a restored number, does; a spacecraft that resets more often keeps "
                                    f"the station held: write the floor by hand ({SN_DOC})", now)
            try:
                msg = self.sdls_tc.observe_spacecraft_sn(sn, protected, self._sn_release_unprotected, uptime)
            except OSError as e:
                self._limited_event("sdls_sn_write", 60.0, "ERROR", "SDLS", f"SDLS SN file not written: {e}", now)
            else:
                if msg:                                  # re-synchronised: WARN; refused: ERROR; raised: INFO
                    sev = ("ERROR" if self.sdls_tc.uplink_blocked else "WARN") if was_blocked else "INFO"
                    self.event(sev, "SDLS", msg, now)

    def _on_rf(self, v: dict[str, Any], now: float) -> None:
        self.rx.sc_rf = dict(v)
        mid = _int_or_none(v.get("radio_model"))
        seen = RADIO_MODELS.get(mid) if mid is not None else None
        if seen is None:
            return
        if self.settings.model_auto and seen != self.settings.model.name and seen in link.MODELS \
                and not link.MODELS[seen].packet_mode:
            self._follow_sc_model(link.MODELS[seen], now)
        mismatch = seen != self.settings.model.name
        if seen != self.model_seen or mismatch != self.model_mismatch:
            if mismatch:
                self.event("WARN", "RADIO", f"spacecraft reports radio model {seen}, the station is configured "
                           f"for {self.settings.model.name} ([radio] model): check station.toml and the flight build",
                           now)
            elif self.model_mismatch:
                self.event("INFO", "RADIO", f"spacecraft radio model {seen} matches the station again", now)
        self.model_seen, self.model_mismatch = seen, mismatch

    def _follow_sc_model(self, model: link.RadioModel, now: float) -> None:
        """[radio] model = "auto" / absent (contract 33): the two E22 modules share one
        register protocol and the flight software selects between them with a strap at
        boot, so the station follows the model the spacecraft reports. It drives the
        link-budget, sensitivity, power and busy-time figures (settings, receiver
        statistics, link manager). The station module's own register writes stay with
        the RadioManager built at start-up (T33D rules: the T37S abnormal-status bit is
        never written to a module whose model is not configured explicitly). An SX126x
        report never switches an E22 station: that is a wiring difference, flagged by
        the mismatch check."""
        old = self.settings.model.name
        self.settings = self.settings.with_(model=model)
        self.rx.set_settings(self.settings)
        self.link_mgr.model = model
        self.event("INFO", "RADIO", f"[radio] model = auto: following the spacecraft's radio model {model.name} "
                   f"(was {old}) for the link budget, sensitivity and power figures", now)

    def _on_cmdver(self, v: dict[str, Any], now: float) -> None:
        # Routed on the command source (I1), never on tc_seq: a stored-sequence command has no
        # FOP record (fop.on_cmdver returns None for it) and is reported here instead.
        stage, src = _int_or_none(v.get("stage")), _int_or_none(v.get("cmd_source"))
        if src == CMD_SOURCE_STORED:
            label = _label(CMD_STAGES, stage)
            err = _label(CMD_ERRORS, v.get("error_code"))
            text = f"stored sequence: opcode 0x{(v.get('opcode') or 0):02X} {label}" + \
                   ("" if v.get("error_code") == 0 else f" ({err})")
            self.console(text)
            self.event("INFO" if stage == 2 else "WARN", "CMD", text, now)
            return
        if src not in (CMD_SOURCE_AD, CMD_SOURCE_BD):
            self._limited_event("cmdver_src", 60.0, "WARN", "CMD", f"CMDVER with unknown command source {src} "
                                f"(opcode 0x{(v.get('opcode') or 0):02X}) ignored", now)
            return
        r = self.fop.on_cmdver(v, now)
        lc = self.link_change
        if r and lc and lc.get("cmd_id") == r.id and lc["state"] == "WAIT_EXEC":
            if r.state == "EXECUTED":
                lc.update(state="WAIT_SWITCH", t=now, frames_at=self.rx.frames_ok)
            else:
                self.event("WARN", "LINK", f"coordinated {lc['kind']} change rejected by the spacecraft", now)
                self.link_change = None
        if r:
            self.archive.command(r.summary())
            self.console(f"#{r.id} {r.cmd.text}: {r.state} {r.detail}")
            self.event("INFO" if r.state == "EXECUTED" else "WARN", "CMD",
                       f"#{r.id} {r.cmd.text} -> {r.state} ({r.detail})", now)

    def _time_correlate(self, v: dict[str, Any], now: float) -> None:
        key = _int_or_none(v.get("ref_mcfc"))
        ert_end = self.ert_by_key.get(key) if key is not None else None
        if ert_end is None:
            return
        # The E22 delivers a packet over its 9600-baud UART after reception; the SX126x
        # packet reaches Linux right after RxDone (its latency belongs in radio_latency_s).
        uart_s = 0.0 if self.settings.model.packet_mode else E22_UART_CADU_S
        airtime = (_int_or_none(v.get("ref_airtime_ms")) or 0) / 1000.0
        scet = ert_end - uart_s - airtime - self.owlt_s - float(self.cfg["radio"]["radio_latency_s"])
        coarse, fine = _int_or_none(v.get("sclk_coarse")), _int_or_none(v.get("sclk_fine"))
        if coarse is None or fine is None:
            return
        self.corr.add(self.partition, coarse + fine / 65536.0, scet)

    def _on_mag(self, v: dict[str, Any]) -> None:
        # A MAG vector is the MEAN of `nsamples` 1 Hz readings over the MAG period (flight
        # gen_mag). While the spacecraft rotates - which the collection requires - a mean lies
        # inside the field ellipsoid and biases the soft-iron scale high; ob_rms (the RMS of
        # |B|) does not reveal it. The flight calibrates instantaneous readings, so only
        # unaveraged packets (nsamples == 1: PKTRATE MAG 1) are collected.
        single = v.get("nsamples") == 1
        for unit, prefix in ((0, "ob_b"), (1, "ib_b")):
            u = self.magcal_units[unit]
            col = u["collector"]
            if not u["collecting"] or col is None:
                continue
            if not single:
                u["averaged_skipped"] += 1
                if u["averaged_skipped"] == 1:
                    self.event("WARN", "MAGCAL", f"unit {unit}: MAG packets average {v.get('nsamples')} readings and "
                               "are not collected - command PKTRATE MAG 1 (1 s, unaveraged) while calibrating")
                continue
            vec = [v.get(f"{prefix}_{a}") for a in "xyz"]
            if all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in vec):
                col.add(vec)

    # ----------------------------------------------------------------- CFDP
    def _queue_ssr_files(self) -> None:
        """COMPLETE ssr_p<partition>_<sclk>.bin files go to the archive (F-47)."""
        live = set(self.cfdp.finished)
        self._ssr_decoded &= live                        # bounded by the receiver's history
        for key, tx in self.cfdp.finished.items():
            if key in self._ssr_decoded or tx.state != CFDP_COMPLETE or not tx.path or not tx.name:
                continue
            if SSR_FILE_RE.fullmatch(tx.name):
                self._ssr_decoded.add(key)
                self._ssr_queue.append((key, tx.path, tx.name))

    def _decode_ssr_file(self, path: str, name: str) -> None:
        """Archives every Space Packet of an SSR export (vcid 2, source CFDP); the live
        telemetry (tlm, alarms, FOP) is not touched."""
        m = SSR_FILE_RE.fullmatch(name)
        part = int(m.group(1)) if m else 0
        try:
            data = Path(path).read_bytes()
        except OSError as e:
            self.event("WARN", "CFDP", f"SSR file {name} not readable: {e}")
            return
        now = time.time()
        i = n_ok = n_bad = 0
        for _ in range(len(data) // 7 + 1):              # bounded: every packet has >= 7 octets
            if i + 6 > len(data):
                break
            plen = 6 + ((data[i + 4] << 8) | data[i + 5]) + 1
            if plen > SPACE_PACKET_MAX or i + plen > len(data) or (data[i] >> 5) != 0:
                n_bad += 1
                break
            p = SpacePacket.parse(data[i:i + plen])
            i += plen
            sclk = p.time.seconds if p.time else None
            scet = self.corr.scet(sclk) if sclk is not None and part == self.partition else None
            pname = self.decom.packet_name(p.apid)
            try:
                values = self.decom.decode(p)
                limits = self.decom.limits(p.apid, values)
                n_ok += 1
            except (KeyError, ValueError):               # RiceError included
                values, limits = {}, {}
                n_bad += 1
            if p.apid in (APID_EVR, APID_SPECBURST, APID_RFSCAN):
                values = {}
            self.archive.packet(now, scet, p.apid, pname, p.seq_count, sclk, part, 2, p.raw, values, limits,
                                source="CFDP")
        rest = len(data) - i
        self.event("INFO" if not n_bad and not rest else "WARN", "CFDP",
                   f"SSR file {name}: {n_ok} packets archived (partition {part})"
                   + (f", {n_bad} undecodable" if n_bad else "") + (f", {rest} trailing octets" if rest else ""))

    # ----------------------------------------------------------------- uplink
    def _uplink_window(self, now: float) -> None:
        """The quiet window after a received frame (`now` = its ERT). UplinkWindowPlan keeps
        everything planned here inside what is LEFT of _window_budget_s(): the transmissions
        start no earlier than ERT + WINDOW_REACTION_S, than the current time (a frame handled
        late, e.g. after a stalled radio thread) and than the end of the previous window's
        transmissions (several frames handled in one batch), and must end by ERT +
        WINDOW_REACTION_S + _window_budget_s(); a frame whose window is gone books nothing.
        The station identification is booked first when it is due or becomes due with this
        window's data, then external TC frames and FOP-1 CLTUs while they fit; the others
        wait for the next window, as when max_frames is reached."""
        self.rx.on_uplink_window()
        # One time base, the steady clock: the frame's age is taken from the wall clock at once and
        # clamped at 0, so a wall-clock step between the ERT stamp and now cannot move the window.
        wall, mono = _wall_clock(), self.mono()
        ert = mono - max(0.0, wall - now)
        start = max(ert + WINDOW_REACTION_S, mono, self._uplink_busy_until)
        budget = max(0.0, ert + WINDOW_REACTION_S + self._window_budget_s() - start)
        if ert - self._last_window_ert < WINDOW_MIN_SPACING_S:
            budget = 0.0                                # frames closer than the flight ever sends them
        else:
            self._last_window_ert = ert
        plan = UplinkWindowPlan(budget, self._tx_cost_s)
        early_s, end_idle_s = self._ident_margins()
        id_octets = len(self.station_id.payload)
        id_booked = self.station_id.due_if_transmitting(now, early_s, end_idle_s) and plan.take(id_octets)
        sent = 0
        while self.external_tc and sent < 2 and not self.fop.uplink_blocked:
            raw = self.external_tc[0]
            info = parse_tc_frame(raw)
            if info is None:
                self.external_tc.popleft()
                self.event("WARN", "INTEROP", "external TC frame rejected (bad header/FECF)", now)
                continue
            taken = False
            try:                                       # its length first: no SDLS number spent on a frame
                plain = build_tc_frame(info.data, info.seq, info.bypass, info.control, info.vcid, sdls=None,
                                       framing=self.fop.framing)    # that waits for the next window
                if not plan.take(cltu_length(len(plain) + self.sdls_tc.overhead)):
                    break
                self.external_tc.popleft()
                taken = True
                frame = build_tc_frame(info.data, info.seq, info.bypass, info.control, info.vcid, sdls=self.sdls_tc,
                                       framing=self.fop.framing)
            except (ValueError, RuntimeError, OSError) as e:
                if not taken:
                    self.external_tc.popleft()
                self.event("WARN", "INTEROP", f"external TC frame not sent: {e}", now)
                continue
            self._radiate(build_cltu(frame), now)
            sent += 1
        for cltu in self.fop.next_cltus(now, fits=plan.take):
            self._radiate(cltu, now)
            sent += 1
        id_sent = self._identify(now) if id_booked else 0     # after the window's data (amateur ID)
        sent += id_sent
        unused_id_s = self._tx_cost_s(id_octets) if id_booked and not id_sent else 0.0
        self._uplink_busy_until = start + plan.used_s - unused_id_s
        if sent:
            self._uplink_tx_until = max(self._uplink_tx_until, self._uplink_busy_until)
        for r in self.fop.all[-12:]:
            if r.history and r.history[-1][0] == now and r.state == "RADIATED":
                self.console(f"#{r.id} {r.cmd.text}: RADIATED ({r.detail})")
                if r.cmd.mnemonic == "HIBERNATE":
                    self._hib_cmd_t = now
                    self.beacon["hibernate_expected"] = True
        for t, text in self.fop.events:
            self.event("WARN", "FOP", text, t)
        self.fop.events.clear()
        if sent or now - self.last_noise_query <= float(self.cfg["radio"]["noise_poll_s"]):
            return
        self.last_noise_query = now
        if self.settings.model.packet_mode:
            if self.radio_mgr is not None and self.radio_mgr.available and \
                    not any(j == "NOISE" for j, _ in self.radio_jobs):
                self.radio_jobs.append(("NOISE", None))
                self.rx.on_noise_query()
        elif self.cfg["radio"]["rssi_noise"]:
            self.noise_wait_until = now + 1.0
            self.sync.expect_noise_reply = True
            self.rx.on_noise_query()
            self.outbox.append(NOISE_QUERY)

    def _radiate(self, cltu: bytes, now: float) -> None:
        self.outbox.append(cltu)
        self.rx.on_uplink(len(cltu), now)
        self.station_id.on_transmit(now)

    def _ident_margins(self) -> tuple[float, float]:
        """(early_s, end_idle_s) for StationIdent: the early margin spans ID_LOST_FRAMES + 1
        frame periods (the longest expected, or the period the spacecraft reports when longer,
        e.g. after FPERIOD), so the periodic ID still finds a window within the 10 minutes when
        that many frames are lost; the end-of-session idle time is at least the FOP T1 (a
        retransmission still to come is part of the session)."""
        period = self._frame_period_s()
        fp = self.tlm.get("RF", {}).get("values", {}).get("frame_period_ms")
        if isinstance(fp, (int, float)) and not isinstance(fp, bool) and fp > 0:
            period = max(period, min(float(fp), 65535.0) / 1000.0)
        return (min(ID_INTERVAL_S, max(ID_EARLY_S, (ID_LOST_FRAMES + 1) * period)),
                max(ID_END_IDLE_S, float(self.fop.t1_s)))

    def _uplink_busy(self) -> bool:
        """The last window's planned transmissions are not over yet (monotonic clock)."""
        return self.mono() < self._uplink_busy_until

    def _id_overdue(self, now: float) -> bool:
        """The periodic identification is about to miss the 10 minutes (no window carried it:
        more than ID_LOST_FRAMES frames lost, a fade shorter than the LOS timeout): due now,
        window or not. A wall clock that stepped back counts as overdue (StationIdent)."""
        sid = self.station_id
        ref = sid.last_t if sid.last_t is not None else sid.uncovered_t
        if not sid.pending or ref is None:
            return False
        return now < ref or now - ref >= ID_INTERVAL_S - ID_OVERDUE_GUARD_S - self._tx_cost_s(len(sid.payload))

    def _window_budget_s(self) -> float:
        """Air time the station may use after a received frame: the SHORTEST quiet window of
        any mode, less WINDOW_GUARD_S. The flight's frame period is at least
        max(2 x busy (the 50 % duty floor; ENCOUNTER / TEST use exactly 2.0), 1.5 s)
        (datalink.cpp frame_period_ms()), so after a frame of busy time B the spacecraft is
        silent for at least max(2 B, 1.5 s) - B; SAFE and CRUISE leave more."""
        model, code = self._sc_radio()
        b = link.busy_s(model, code)
        return max(2.0 * b, MIN_FRAME_PERIOD_S) - b - WINDOW_GUARD_S

    def _tx_cost_s(self, octets: int) -> float:
        """Transmitter time of one ground packet of `octets`: the flight-model busy time of the
        station's radio (link.busy_s: E22 UART transfer + air time, SX126x time on air) plus
        TX_TURNAROUND_S, plus CAD_SYMBOLS symbols when the SX126x listens before talking."""
        s = self.settings
        t = link.busy_s(s.model, s.rate_code, octets) + TX_TURNAROUND_S
        if s.model.packet_mode and s.cad:
            t += CAD_SYMBOLS * link.SX126X_PROFILES[s.rate_code].symbol_s
        return t

    def _identify(self, now: float) -> int:
        """Radiates the station identification when StationIdent says it is due (an uplink
        window that booked it, or the housekeeping during LOS); 1 if it was queued."""
        early_s, end_idle_s = self._ident_margins()
        reason = self.station_id.due(now, early_s=early_s, end_idle_s=end_idle_s)
        if reason is None:
            return 0
        self.outbox.append(self.station_id.payload)
        self._uplink_tx_until = max(self._uplink_tx_until,
                                    self.mono() + self._tx_cost_s(len(self.station_id.payload)))
        self.station_id.sent(now, reason)
        self.event("INFO", "IDENT", f"station identification ({reason}): {self.station_id.text}", now)
        return 1

    # ---------------------------------------------------------------- operator
    def submit_command(self, text: str, source: str = "web") -> dict[str, Any]:
        with self.lock:
            if not isinstance(text, str):
                return {"status": "ERROR", "message": "command text must be a string"}
            t = text.strip()
            if not t:
                return {"status": "ERROR", "message": "empty"}
            toks = t.split()
            head = toks[0].upper()
            if head in ("GSCAN", "WAKE", "LINKRATE", "LINKCHAN", "LINKMGR", "RADIO", "SPECREF", "SPECLIB", "TDM",
                        "AFC", "SDLS"):
                return self._ground_directive(toks, source)
            if head == "HELP":
                lines = help_text(toks[1] if len(toks) > 1 else None)
                for ln in lines:
                    self.console(ln)
                return {"status": "HELP", "lines": lines}
            try:
                cmd = parse(t)
            except CommandError as e:
                self.console(f"REJECTED: {e}")
                return {"status": "REJECTED", "message": str(e)}
            if cmd.mnemonic == "CHANNEL" and cmd.args.get("ch") not in self.allowed_channels:
                msg = f"CH{cmd.args.get('ch')} not in [radio] allowed_channels {self.allowed_channels}"
                self.console(f"REJECTED: {msg}")
                return {"status": "REJECTED", "message": msg}
            err = self._uplink_check(cmd)
            if err:
                self.console(f"REJECTED: {err}")
                return {"status": "REJECTED", "message": err}
            if cmd.hazardous:
                return self._pending(cmd.text, "command", source, hazardous=True, cmd=cmd)
            return self._queue(cmd, source)

    def _uplink_check(self, cmd: ParsedCommand) -> str | None:
        """C-08 frame size and the F-64 SN block: None when the command can be uplinked."""
        err = frame_size_error(cmd, self.fop.framing, self.sdls_tc.overhead, self.sdls_tc.service_name)
        if err:
            self.rx.on_uplink_rejected()
            return err
        if self.fop.uplink_blocked:
            return f"TC uplink BLOCKED: {self.sdls_tc.blocked}"
        return None

    def _pending(self, text: str, kind: str, source: str, *, hazardous: bool, **payload: Any) -> dict[str, Any]:
        cid = self._confirm_ids
        self._confirm_ids += 1
        need_auth = hazardous and bool(self.cfg["commanding"]["require_two_person_for_hazardous"])
        self.pending_confirm[cid] = {"text": text, "kind": kind, "source": source, "t": time.time(),
                                     "confirmed": False, "authorized": not need_auth, **payload}
        msg = f"{'HAZARDOUS ' if hazardous else ''}{text}: confirm id {cid}" + \
              (" + 2nd-operator AUTH" if need_auth else "")
        self.console(msg)
        self.event("WARN" if hazardous else "INFO", "CMD", f"{msg} (from {source})")
        return {"status": "CONFIRM", "id": cid, "message": msg, "needs_auth": need_auth}

    def _radio_rejection(self, d: str) -> str:
        if self.settings.model.packet_mode:
            return (f"{d} needs the station SX126x radio (station UNO Q MCU sketch built with DSSQ_RADIO_SX126X = 1, "
                    "connected through the Router Bridge)")
        return (f"{d} needs the Radio Control Unit (station UNO Q MCU D2/D3/D7 wired to the "
                f"{self.settings.model.name} M0/M1/AUX, [gds] rcu = true)")

    def _ground_directive(self, toks: list[str], source: str) -> dict[str, Any]:
        d = toks[0].upper()
        now = time.time()
        if d == "LINKMGR":
            if len(toks) != 2 or toks[1].upper() not in ("OFF", "ADVISE", "AUTO"):
                return {"status": "REJECTED", "message": "usage: LINKMGR OFF|ADVISE|AUTO"}
            self.link_mgr.mode = toks[1].upper()
            self.event("INFO", "LINK", f"link manager -> {self.link_mgr.mode} ({source})")
            return {"status": "OK", "message": f"link manager {self.link_mgr.mode}"}
        if d in ("SPECREF", "SPECLIB"):
            r = self.spectral.directive(toks, now)
            for e in self.spectral.drain_events():
                self.event(e["level"], e["source"], e["text"], e["t"])
            self.console(f"{' '.join(toks)}: {r.get('message')}")
            return r
        if d == "TDM":
            if len(toks) != 2 or toks[1].upper() != "WRITE":
                return {"status": "REJECTED", "message": "usage: TDM WRITE"}
            return self.tdm_write(source)
        if d == "AFC":
            if len(toks) != 2 or toks[1].upper() != "RESET":
                return {"status": "REJECTED", "message": "usage: AFC RESET"}
            if not self.afc.enabled:
                return {"status": "REJECTED", "message": "AFC is not active (SX126x with [radio.sx126x] afc = true)"}
            self.afc.reset()
            self.event("INFO", "AFC", f"AFC reset by {source}: back to the static offset {self.afc.static_hz} Hz")
            return {"status": "OK", "message": "AFC reset"}
        if d == "SDLS":
            if len(toks) != 2 or toks[1].upper() != "RESETREPLAY":
                return {"status": "REJECTED", "message": "usage: SDLS RESETREPLAY"}
            return self._pending("SDLS RESETREPLAY (forget the TM epoch/sn replay window)", "sdls", source,
                                 hazardous=True)
        if not (self.radio_mgr and self.radio_mgr.available):
            return {"status": "REJECTED", "message": self._radio_rejection(d)}
        if d == "RADIO":
            self.radio_jobs.append(("RADIO", None))
        elif d == "GSCAN":
            try:
                first, last = (int(toks[1], 10), int(toks[2], 10)) if len(toks) == 3 else (0, link.CHANNEL_MAX)
            except ValueError:
                return {"status": "REJECTED", "message": "usage: GSCAN [first last]"}
            if len(toks) not in (1, 3) or not 0 <= first <= last <= link.CHANNEL_MAX:
                return {"status": "REJECTED", "message": "usage: GSCAN [first last], channels 0..83"}
            self.radio_jobs.append(("GSCAN", (first, last)))
        elif d == "WAKE":
            if self.fop.uplink_blocked:
                return {"status": "REJECTED", "message": f"TC uplink BLOCKED: {self.sdls_tc.blocked}"}
            self.radio_jobs.append(("WAKE", None))
        else:                                            # LINKRATE / LINKCHAN
            if len(toks) != 2:
                return {"status": "REJECTED", "message": f"usage: {d} <value>"}
            try:
                v = int(toks[1], 10)
            except ValueError:
                return {"status": "REJECTED", "message": f"usage: {d} <decimal value>"}
            if d == "LINKCHAN" and v not in self.allowed_channels:
                return {"status": "REJECTED", "message": f"CH{v} not in allowed_channels {self.allowed_channels}"}
            if d == "LINKRATE" and not 0 <= v <= 7:
                return {"status": "REJECTED", "message": "rate code 0..7"}
            kind = "rate" if d == "LINKRATE" else "chan"
            return self._pending(f"{d} {v} (changes BOTH ends of the link)", "directive", source, hazardous=True,
                                 directive=(kind, v))
        self.console(f"{d}: scheduled (runs in the next quiet window)")
        return {"status": "QUEUED", "message": f"{d} scheduled"}

    def start_link_change(self, kind: str, value: int, source: str) -> dict[str, Any]:
        if self.link_change:
            return {"status": "REJECTED", "message": "a coordinated change is already in progress"}
        revert_s = int(self.cfg["linkmgr"]["revert_s"])
        text = f"{'AIRRATE' if kind == 'rate' else 'CHANNEL'} {value} {revert_s}"
        cmd = parse(text)
        err = self._uplink_check(cmd)
        if err:
            return {"status": "REJECTED", "message": err}
        res = self._queue(cmd, source + "/link")
        old = self.settings.rate_code if kind == "rate" else self.settings.channel
        self.link_change = {"kind": kind, "value": value, "cmd_id": res["id"], "state": "WAIT_EXEC",
                            "t": time.time(), "revert_s": revert_s, "old": old}
        self.event("INFO", "LINK", f"coordinated {kind} change to {value} started ({source})")
        return {"status": "QUEUED", "id": res["id"], "message": f"coordinated change {text}"}

    def confirm(self, cid: int, role: str = "CONFIRM") -> dict[str, Any]:
        with self.lock:
            p = self.pending_confirm.get(cid)
            if not p:
                return {"status": "ERROR", "message": f"no pending confirmation {cid}"}
            if time.time() - p["t"] > CONFIRM_EXPIRY_S:
                del self.pending_confirm[cid]
                return {"status": "ERROR", "message": f"confirmation expired ({CONFIRM_EXPIRY_S:.0f} s)"}
            if role == "AUTH":
                p["authorized"] = True
            elif role == "CONFIRM":
                p["confirmed"] = True
            else:
                return {"status": "ERROR", "message": f"role {role!r}: CONFIRM or AUTH"}
            if not (p["confirmed"] and p["authorized"]):
                return {"status": "WAITING", "message": "waiting for " +
                        ("2nd-operator AUTH" if not p["authorized"] else "CONFIRM")}
            del self.pending_confirm[cid]
            src = p["source"] + f"+{role.lower()}"
            kind = p["kind"]
            if kind == "directive":
                dkind, dvalue = p["directive"]
                return self.start_link_change(dkind, dvalue, p["source"])
            if kind == "sdls":
                self.sdls_tm.reset_replay()
                self.event("WARN", "SDLS", f"TM replay window reset by {src}: the next (epoch, sn) is accepted")
                return {"status": "OK", "message": "SDLS TM replay window reset"}
            if kind == "sequence":
                if self.fop.uplink_blocked:
                    return {"status": "REJECTED", "message": f"TC uplink BLOCKED: {self.sdls_tc.blocked}"}
                ids = [self._queue(c, src)["id"] for c in p["cmds"]]
                self.seq_uplink_state.update(pending_confirm_id=None, queued_ids=ids)
                return {"status": "QUEUED", "ids": ids, "message": f"{len(ids)} SEQLOAD queued in order"}
            cmd = p["cmd"]
            err = self._uplink_check(cmd)
            if err:
                return {"status": "REJECTED", "message": err}
            return self._queue(cmd, src)

    def cancel(self, cid: int) -> None:
        with self.lock:
            p = self.pending_confirm.pop(cid, None)
            if p is not None:
                self.console(f"confirmation {cid} cancelled: {p['text']}")
                if p["kind"] == "sequence":
                    self.seq_uplink_state["pending_confirm_id"] = None

    def _queue(self, cmd: ParsedCommand, source: str) -> dict[str, Any]:
        r = self.fop.submit(cmd)
        self.console(f"#{r.id} {cmd.text}: QUEUED ({cmd.kind})")
        self.event("INFO", "CMD", f"#{r.id} {cmd.text} queued by {source}")
        self.archive.command(r.summary())
        return {"status": "QUEUED", "id": r.id, "text": cmd.text}

    # ------------------------------------------------------ stored sequences
    def sclk_estimate_s(self, now: float | None = None) -> int | None:
        """Spacecraft SCLK now: the newest real-time packet's SCLK plus the time since its ERT."""
        if self.last_sclk is None or self.last_sclk_ert is None:
            return None
        now = time.time() if now is None else now
        return int(self.last_sclk + max(0.0, now - self.last_sclk_ert)) & 0xFFFFFFFF

    def _seq_eval(self, text: str, first_slot: int) -> dict[str, Any]:
        out: dict[str, Any] = {"name": None, "text_lines": 0, "entries": [], "error": None,
                               "checked_t": time.time(), "first_slot": first_slot}
        if not isinstance(text, str):
            out["error"] = {"line_no": None, "message": "sequence text must be a string"}
            return out
        out["text_lines"] = len(text.splitlines())
        if isinstance(first_slot, bool) or not isinstance(first_slot, int) or not 0 <= first_slot <= 31:
            out["error"] = {"line_no": None, "message": f"first slot {first_slot!r} outside 0..31"}
            return out
        try:
            entries = parse_sequence(text)
        except SequenceError as e:
            out["error"] = {"line_no": e.line_no, "message": e.message}
            return out
        if first_slot + len(entries) > 32:
            out["error"] = {"line_no": None, "message": f"{len(entries)} commands from slot {first_slot} exceed the "
                                                        "32-slot table"}
            return out
        est = self.sclk_estimate_s()
        for i, ent in enumerate(entries):
            row: dict[str, Any] = {"slot": first_slot + i, "line_no": ent.line_no, "time_text": ent.time_text,
                                   "relative": ent.relative, "t_s": ent.t_s, "mnemonic": ent.mnemonic,
                                   "args": [[k, v] for k, v in ent.args], "cmd_hex": None}
            try:
                blk = pack_seqload(first_slot + i, ent, now_sclk_s=est)
                row["cmd_hex"] = blk[7:7 + blk[6]].hex()
            except SequenceError as err:
                out["entries"].append(row)
                out["error"] = {"line_no": err.line_no, "message": err.message}
                return out
            out["entries"].append(row)
        return out

    def seq_check(self, text: str, first_slot: int = 0) -> dict[str, Any]:
        """Checks a sequence file (syntax, dictionary, flight refusal policy, SCLK window);
        nothing is uplinked. The result is also shown in the SEQUENCES panel."""
        with self.lock:
            res = self._seq_eval(text, first_slot)
            self.seq_file = res
            return {"status": "ERROR" if res["error"] else "OK", **res, "sclk_estimate_s": self.sclk_estimate_s()}

    def seq_uplink(self, text: str, first_slot: int = 0, source: str = "web") -> dict[str, Any]:
        """Checks, then asks for ONE confirmation for the whole batch; on confirm every
        SEQLOAD is queued in file order through COP-1. SEQCTRL START stays separate."""
        with self.lock:
            res = self._seq_eval(text, first_slot)
            self.seq_file = res
            if res["error"]:
                return {"status": "REJECTED", "message": f"line {res['error']['line_no']}: {res['error']['message']}"
                        if res["error"]["line_no"] else res["error"]["message"], "error": res["error"]}
            try:
                cmds = seqload_commands(parse_sequence(text), first_slot=first_slot,
                                        now_sclk_s=self.sclk_estimate_s())
            except SequenceError as e:
                return {"status": "REJECTED", "message": str(e)}
            err = self._uplink_check(cmds[0])
            if err:
                return {"status": "REJECTED", "message": err}
            last = first_slot + len(cmds) - 1
            r = self._pending(f"SEQUENCE upload: {len(cmds)} x SEQLOAD, slots {first_slot}..{last}", "sequence",
                              source, hazardous=False, cmds=cmds)
            self.seq_uplink_state = {"pending_confirm_id": r["id"], "queued_ids": [], "first_slot": first_slot}
            return r

    # --------------------------------------------------------------- MAGCAL
    def magcal_action(self, action: str, unit: int, source: str = "web") -> dict[str, Any]:
        """start / stop / reset / fit / uplink / clear for unit 0 (outboard) or 1 (inboard)."""
        with self.lock:
            if isinstance(unit, bool) or unit not in (0, 1):
                return {"status": "REJECTED", "message": "unit must be 0 (outboard) or 1 (inboard)"}
            act = str(action).lower()
            u = self.magcal_units[unit]
            if act == "start":
                if self.field_ut is None:
                    return {"status": "REJECTED", "message": "set [science] field_ut (site total field, uT) first"}
                if u["collector"] is None:
                    u["collector"] = MagCalCollector(unit, self.field_ut)
                u["collecting"] = True
                u["averaged_skipped"] = 0
                self.event("INFO", "MAGCAL", f"unit {unit} collection started by {source}: rotate the spacecraft "
                           "(MAG at a 1 s period, PKTRATE MAG 1: averaged MAG packets are not collected)")
                return {"status": "OK", "message": f"unit {unit} collecting"}
            if act == "stop":
                u["collecting"] = False
                return {"status": "OK", "message": f"unit {unit} collection stopped"}
            if act == "reset":
                if u["collector"] is not None:
                    u["collector"].reset()
                u.update(fit=None, command=None, averaged_skipped=0)
                return {"status": "OK", "message": f"unit {unit} samples cleared"}
            if act == "fit":
                if u["collector"] is None:
                    return {"status": "REJECTED", "message": "no samples: start a collection first"}
                res: MagCalResult = u["collector"].fit()
                u["fit"] = res
                u["command"] = magcal_command(res.magcal_args(unit)) if res.ok else None
                self.event("INFO" if res.ok else "WARN", "MAGCAL",
                           f"unit {unit} fit {'OK' if res.ok else 'REFUSED'}: " +
                           (f"residual {res.residual_rms_ut:.3f} uT, coverage {res.bins_hit}/26" if res.ok
                            else "; ".join(res.reasons)))
                return {"status": "OK" if res.ok else "REFUSED", "fit": res.as_dict(), "command": u["command"]}
            if act in ("uplink", "clear"):
                if act == "uplink" and not u["command"]:
                    return {"status": "REJECTED", "message": "no accepted fit to uplink"}
                text = u["command"] if act == "uplink" else magcal_command(clear_args(unit))
                try:
                    cmd = parse(text)
                except CommandError as e:
                    return {"status": "REJECTED", "message": str(e)}
                err = self._uplink_check(cmd)
                if err:
                    return {"status": "REJECTED", "message": err}
                label = "MAGCAL upload" if act == "uplink" else "MAGCAL clear (identity, zero offsets)"
                return self._pending(f"{label} unit {unit}: {cmd.text}", "magcal", source, hazardous=False, cmd=cmd)
            return {"status": "REJECTED", "message": "action: start, stop, reset, fit, uplink or clear"}

    # ------------------------------------------------------------------ TDM
    def _tdm_write(self, rec: products.TdmRecorder) -> tuple[Path | None, str | None]:
        cal = float(self.cfg["radio"]["freq_offset_cal_hz"]) if self.cfg["radio"]["freq_offset_calibrated"] else None
        try:
            path = rec.write(self.archive_dir, freq_offset_cal_hz=cal, radio_model=self.settings.model.name)
        except (OSError, ValueError) as e:
            return None, str(e)
        return path, None

    def tdm_write(self, source: str = "web") -> dict[str, Any]:
        """Writes the CCSDS TDM of the current pass now (same file name, rewritten)."""
        with self.lock:
            rec = self.tdm if len(self.tdm) else self._tdm_pending
            if rec is None or not len(rec):
                return {"status": "ERROR", "path": None, "message": "no tracking record in the current pass"}
            path, err = self._tdm_write(rec)
            if err:
                self.event("WARN", "TDM", f"TDM not written: {err}")
                return {"status": "ERROR", "path": None, "message": err}
            if path is None:
                return {"status": "ERROR", "path": None, "message": "no record with a measurement to report"}
            self.tdm_state.update(last_file=path.name, last_write_t=time.time())
            self._tdm_files_cache = (-1e18, [])
            self.event("INFO", "TDM", f"TDM written by {source}: {path.name} ({len(rec)} records)")
            return {"status": "OK", "path": str(path), "message": f"{path.name} written"}

    def tdm_service(self) -> None:
        """Runner 'tdm' stage: writes the TDM of a pass that ended (prepared at LOS)."""
        with self.lock:
            rec, self._tdm_pending = self._tdm_pending, None
            if rec is None or not len(rec):
                return
            path, err = self._tdm_write(rec)
            if err:
                self.event("WARN", "TDM", f"pass TDM not written: {err}")
            elif path is not None:
                self.tdm_state.update(last_file=path.name, last_write_t=time.time())
                self._tdm_files_cache = (-1e18, [])
                self.event("INFO", "TDM", f"pass TDM written: {path.name} ({len(rec)} records)")

    def tdm_final(self) -> None:
        """Shutdown: the in-progress pass is written when [tdm] auto_write is set."""
        with self.lock:
            if self.cfg["tdm"]["auto_write"] and len(self.tdm) and self._tdm_pending is None:
                self._tdm_pending = self.tdm
                self.tdm = products.TdmRecorder(self.cfg["station"]["name"], self.cfg["spacecraft"]["name"])
        self.tdm_service()

    def _tdm_files(self) -> list[dict[str, Any]]:
        t, cached = self._tdm_files_cache
        now = time.time()
        if now - t < 10.0:
            return cached
        files: list[dict[str, Any]] = []
        with contextlib.suppress(OSError):
            for p in self.tdm_dir.glob("VGQ1_*.kvn"):
                st = p.stat()
                files.append({"name": p.name, "size": st.st_size, "mtime": st.st_mtime})
        files.sort(key=lambda f: f["mtime"], reverse=True)
        self._tdm_files_cache = (now, files[:20])
        return files[:20]

    # ------------------------------------------------------------------ XTCE
    def xtce_bytes(self) -> bytes:
        """The XTCE 1.2 mission database, built OUTSIDE `lock` (it reads only the static
        dictionary) and cached per (version, UTC date)."""
        key = (__version__, datetime.now(UTC).strftime("%Y-%m-%d"))
        with self._xtce_lock:
            cached = self._xtce_cache
            if cached is not None and cached[0] == key:
                return cached[1]
            data = products.xtce_bytes(products.build_xtce(version=key[0], date_text=key[1]))
            self._xtce_cache = (key, data)
            return data

    # ------------------------------------------------------------ radio jobs
    def run_radio_jobs(self) -> None:
        """Executed by the radio thread between reads (procedures own the radio briefly). In
        queue order; WAKE and GSCAN (TX_QUIET_JOBS) never start on top of a window's planned
        transmissions; a WAKE runs at most once per WAKE_MIN_INTERVAL_S (it takes an SDLS
        number: the rate bound of the sdls.py time floor) and waits while the SX126x station is
        not yet back on its channel after a survey (RadioManager.off_home()); a GSCAN waits until
        the station reports its transmitter idle (RadioManager.tx_busy(): uplinks still queued
        on the MCU, or that same channel check). Both wait at most _tx_wait_max_s(), then are
        refused so the jobs behind them run."""
        for _ in range(len(self.radio_jobs)):              # bounded: jobs queued so far
            with self.lock:
                if not self.radio_jobs:
                    return
                head = self.radio_jobs[0]
                job, arg = head
                now = self.mono()
                if job in TX_QUIET_JOBS and self._uplink_busy():
                    return                              # never on top of a window's planned transmissions
                if job == "WAKE" and now - self._last_wake < WAKE_MIN_INTERVAL_S:
                    return
                rm = self.radio_mgr
                busy = None if rm is None else rm.tx_busy() if job == "GSCAN" else \
                    rm.off_home() if job == "WAKE" else None
                if busy is not None:
                    if self._job_wait is None or self._job_wait[0] is not head:
                        self._job_wait = (head, now)
                    waited = now - self._job_wait[1]
                    if waited < self._tx_wait_max_s():
                        return                          # retune / transmit only once the station is ready
                    self.radio_jobs.popleft()
                    self._job_wait = None
                    what = f"ground RF survey CH{arg[0]}-{arg[1]} not run" if job == "GSCAN" else "WAKE not sent"
                    self.event("WARN", "RADIO", f"{what}: station transmitter still busy after {waited:.0f} s "
                               f"({busy})")
                    continue
                self.radio_jobs.popleft()
                self._job_wait = None
            if rm is None:
                return
            self._run_job(rm, job, arg)

    def _tx_wait_max_s(self) -> float:
        return max(GSCAN_WAIT_MIN_S, 2.0 * self._frame_period_s())

    def _run_job(self, rm: RadioManager, job: str, arg: Any) -> None:
        name = self.settings.model.name
        if job in ("RADIO", "VERIFY"):
            cfg = rm.read_config()
            with self.lock:
                self.ground_radio = cfg
                if cfg is None:
                    self.event("ERROR", "RADIO", f"ground {name} did not answer the configuration read")
                    return
                r = self.cfg["radio"]
                bad = [k for k, want in (("channel", self.settings.channel),
                                         ("air_rate_code", self.settings.rate_code),
                                         ("rssi_byte", bool(r["rssi_byte"])),
                                         ("rssi_noise_enable", bool(r["rssi_noise"]))) if cfg.get(k) != want]
                self.event("WARN" if bad else "INFO", "RADIO",
                           f"ground {name}: ch {cfg.get('channel')} ({cfg.get('freq_mhz')} MHz) rate "
                           f"{cfg.get('air_rate')} pwr {cfg.get('power_code')} LBT/CAD {cfg.get('lbt')}"
                           + (f" MISMATCH {bad}" if bad else " - matches station.toml"))
        elif job == "GSCAN":
            home = self.settings.channel                  # the operational channel, never a survey step
            res = rm.survey(arg[0], arg[1], home=home)
            with self.lock:
                if res:
                    # The survey reads the raw radio (E22 register / SX126x RSSI median): calibrated
                    # here like every other noise reading ([radio] rssi_cal_offset_db), so the
                    # bars compare with the RECEIVER noise floor. The ordering is unchanged.
                    res["noise"] = [None if n is None else n + self.rx.rssi_cal_db for n in res["noise"]]
                    res["calibrated"] = True
                    allowed = [(n, arg[0] + i) for i, n in enumerate(res["noise"])
                               if n is not None and arg[0] + i in self.allowed_channels]
                    res["quietest_allowed"] = min(allowed)[1] if allowed else None
                    self.event("INFO", "RADIO", f"ground RF survey CH{arg[0]}-{arg[1]} in {res['duration_s']:.1f} s: "
                               f"quietest CH{res['quietest']}, quietest allowed CH{res['quietest_allowed']}")
                    if not res["home_restored"]:
                        self.event("ERROR", "RADIO", f"ground RF survey: the station radio did not return to "
                                   f"CH{home} - " + (
                                       "it may be receiving on a surveyed channel; every uplink is held "
                                       f"(radio.sx126x.tx_hold) until the MCU reports CH{home} again, and the "
                                       "station configuration is re-sent until the MCU accepts it" if rm.packet else
                                       "it may be receiving and transmitting on a surveyed channel; check it with "
                                       "RADIO"))
                elif rm.survey_refused:
                    self.event("WARN", "RADIO", f"ground RF survey CH{arg[0]}-{arg[1]} not run: {rm.survey_refused}")
                else:
                    self.event("WARN", "RADIO", "ground RF survey failed (radio not available)")
        elif job == "WAKE":
            with self.lock:
                if self.fop.uplink_blocked:
                    self.event("WARN", "RADIO", f"WAKE not sent: TC uplink BLOCKED ({self.sdls_tc.blocked})")
                    return
                try:
                    self._last_wake = self.mono()           # one SDLS number per WAKE (rate bound)
                    frame = build_tc_frame(tc_packet(parse("BD MODE SAFE"), 0), seq=0, bypass=True,
                                           sdls=self.sdls_tc, framing=self.fop.framing)
                except (ValueError, RuntimeError, OSError) as e:
                    self.event("ERROR", "RADIO", f"WAKE frame not built: {e}")
                    return
            ok = rm.wor_transmit(build_cltu(frame))
            with self.lock:
                self.station_id.on_transmit(time.time())   # attempted: identify for it (conservative)
                self.event("INFO" if ok else "ERROR", "RADIO",
                           "wake-up transmitted (BD MODE SAFE)" if ok else "wake-up transmission failed")
        elif job in ("SETRATE", "SETCHAN"):
            ok = rm.set_air_rate(arg) if job == "SETRATE" else rm.set_channel(arg)
            with self.lock:
                if ok:
                    if job == "SETRATE":
                        self.cfg["radio"]["air_rate"] = arg
                        self.settings = self.settings.with_(rate_code=arg)
                    else:
                        self.cfg["radio"]["channel"] = arg
                        self.settings = self.settings.with_(channel=arg)
                    self.rx.set_settings(self.settings)
                    if self.settings.model.packet_mode:
                        p = link.SX126X_PROFILES[self.settings.rate_code]
                        corr = self.afc.reconfigure(time.time(), p.sf, p.bw_hz, self.settings.freq_hz,
                                                    link.profile_airtime_s(self.settings.rate_code))
                        if corr is not None:
                            self.afc_out.append(corr)
                what = "rate code" if job == "SETRATE" else "channel"
                self.event("INFO" if ok else "ERROR", "RADIO", f"ground {what} -> {arg}: {'OK' if ok else 'FAILED'}")
        elif job == "NOISE":
            v = rm.noise_dbm()
            with self.lock:
                if v is not None:
                    self.rx.on_noise(v + self.rx.rssi_cal_db, time.time())

    def _link_change_tick(self, now: float) -> None:
        lc = self.link_change
        if not lc:
            return
        period = self._frame_period_s()
        rf = self.tlm.get("RF", {}).get("values", {})
        fp = rf.get("frame_period_ms")
        if isinstance(fp, (int, float)) and fp > 0:
            period = max(period, fp / 1000.0) if fp >= 65535 else fp / 1000.0
        if lc["state"] == "WAIT_EXEC" and now - lc["t"] > max(600.0, 4 * period):
            self.event("WARN", "LINK", "coordinated change: no command verification, abandoned")
            self.link_change = None
        elif lc["state"] == "WAIT_SWITCH" and (self.rx.frames_ok > lc["frames_at"] or now - lc["t"] > 1.5 * period):
            self.radio_jobs.append(("SETRATE" if lc["kind"] == "rate" else "SETCHAN", lc["value"]))
            self._queue(parse("NOOP"), "link-confirm")       # proves the new link -> cancels the revert
            lc.update(state="SWITCHED", t=now, frames_at=self.rx.frames_ok)
        elif lc["state"] == "SWITCHED":
            if self.rx.frames_ok > lc["frames_at"]:
                self.event("INFO", "LINK", f"coordinated {lc['kind']} change to {lc['value']} COMPLETE")
                self.link_change = None
            elif now - lc["t"] > lc["revert_s"] + 30 + 2 * period:
                self.radio_jobs.append(("SETRATE" if lc["kind"] == "rate" else "SETCHAN", lc["old"]))
                self.event("WARN", "LINK", f"no frames after the {lc['kind']} change - ground reverting to {lc['old']}")
                self.link_change = None

    # ---------------------------------------------------------- housekeeping
    def _sc_radio(self) -> tuple[link.RadioModel, int]:
        """The spacecraft's radio model and rate code (RF packet), else the station's."""
        rf = self.rx.sc_rf
        mid, code = rf.get("radio_model"), rf.get("air_rate_code")
        model = link.model_from_id(mid) if isinstance(mid, int) and mid in link.MODEL_IDS else self.settings.model
        code = code if isinstance(code, int) and 0 <= code <= 7 else self.settings.rate_code
        return model, code

    def _frame_period_s(self) -> float:
        """P: the longest expected spacecraft frame period, 4.0 x busy time (SAFE factor)."""
        model, code = self._sc_radio()
        return max(FRAME_PERIOD_FACTOR * link.busy_s(model, code), MIN_FRAME_PERIOD_S)

    def los_timeout_s(self) -> float:
        return max(float(self.cfg["gds"]["los_timeout_s"]), 3.0 * self._frame_period_s())

    def _derive_timers(self) -> None:
        c = self.cfg["commanding"]
        p = self._frame_period_s()
        t1, vt = float(c["t1_s"]), float(c["verify_timeout_s"])
        self.fop.t1_s, self.fop.verify_timeout_s = max(t1, 2 * p), max(vt, 4 * p)
        derived = self.fop.t1_s > t1 or self.fop.verify_timeout_s > vt
        if derived != self._timers_derived:
            self._timers_derived = derived
            if derived:
                self.event("INFO", "FOP", f"frame period up to {p:.1f} s: T1 {self.fop.t1_s:.0f} s, verification "
                           f"timeout {self.fop.verify_timeout_s:.0f} s (configured minima {t1:.0f} / {vt:.0f} s)")

    def _hibernating(self, now: float) -> bool:
        """HK fdir b14, or a HIBERNATE radiated within HIBERNATE_LOS_ALARM_S and not yet
        contradicted by a later HK."""
        hc = self._hib_cmd_t
        return bool(self.beacon["hibernating"]) or (hc is not None and now - hc <= HIBERNATE_LOS_ALARM_S)

    def housekeeping(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self.lock:
            self._derive_timers()
            self.rx.tick(now, self.los_timeout_s(), self._hibernating(now))
            for t, e in self.rx.pending_events:
                if e == "AOS":
                    self.event("INFO", "RX", "AOS", t)      # the TDM was cleared by the frame (_frame_done)
                else:
                    hib = self._hibernating(now)
                    self.event("INFO" if hib else "WARN", "RX", "LOS (hibernating)" if hib else "LOS", t)
                    if self.cfg["tdm"]["auto_write"] and len(self.tdm):
                        self._tdm_pending = self.tdm
                        self.tdm = products.TdmRecorder(self.cfg["station"]["name"], self.cfg["spacecraft"]["name"])
            self.rx.pending_events.clear()
            self._link_change_tick(now)
            expired = [k for k, p in self.pending_confirm.items() if now - p["t"] > CONFIRM_EXPIRY_S]
            for k in expired:
                p = self.pending_confirm.pop(k)
                self.console(f"confirmation {k} expired: {p['text']}")
                if p["kind"] == "sequence":
                    self.seq_uplink_state["pending_confirm_id"] = None
            corr = self.afc.tick(now, self.rx.los)
            if corr is not None:
                self.afc_out.append(corr)
            if (self.rx.los or self._id_overdue(now)) and not self._uplink_busy():
                self._identify(now)                     # no uplink window comes (downlink lost or fading)
            self._drain_afc_events()
            if now - self._hk_1hz_t < 1.0 and now >= self._hk_1hz_t:
                return
            self._hk_1hz_t = now
            self._housekeeping_1hz(now)

    def _housekeeping_1hz(self, now: float) -> None:
        if self.link_mgr.mode != "OFF" and now - self.link_mgr.last_eval >= self.link_mgr.period_s:
            # FER over the margin's 10-minute window without the AOS frame's MCFC jump: the
            # lifetime ratio counts every inter-pass gap as lost frames (rxstats.fer_window).
            rx = {"windows": {"10m": {"rssi": self.rx.w["10m"]["rssi"].stats(now)}},
                  "frames": {"fer": self.rx.fer_window(now)}}
            rec = self.link_mgr.evaluate(now, rx, self.settings.rate_code, self.rx.sc_rf.get("tx_power_code"))
            if rec:
                self.event("INFO", "LINK", f"link manager AUTO: {rec['action']} {rec.get('value')} - {rec['why']}")
                if rec["action"] == "LINKRATE" and self.radio_mgr and self.radio_mgr.available:
                    self.start_link_change("rate", rec["value"], "linkmgr")
        for e in self.cfdp.poll():                        # its timers run on its own clock (cfdp.Receiver)
            self.event(e["level"], e["source"], e["text"], e["t"])
        self._queue_ssr_files()
        if self._ssr_queue:
            _key, path, name = self._ssr_queue.popleft()
            self._decode_ssr_file(path, name)
        for e in self.spectral.drain_events():
            self.event(e["level"], e["source"], e["text"], e["t"])
        err = self.sdls_tm.persist_error
        if err and err != self._persist_error_seen:
            self.event("ERROR", "SDLS", err)
        self._persist_error_seen = err
        if self.sdls_tm.epoch_regressed > self._epoch_regressed_seen:
            self._epoch_regressed_seen = self.sdls_tm.epoch_regressed
            self._epoch_regressed_times.append(now)
            self.event("ERROR", "SDLS", "TM epoch went backwards with a valid MAC: replayed recording or EGSE "
                       "boot-counter regression (possible GCM nonce reuse) - investigate; generate a new key if the "
                       "counter regressed")
        self._log_rx_stats(now)
        self._archive_health(now)
        self._radio_link_events(now)
        self.alarm, self.alarm_reasons, self.alarm_red_count = self._compute_alarm(now)

    def _log_rx_stats(self, now: float) -> None:
        """Data logger step (dssq.datalog): the rx_stats row of the interval since the last
        one, the free-space guard and the retention; their events go to the log as ARCHIVE."""
        since = self._logger_t if self._logger_t is not None and self._logger_t < now else now - 1.0
        afc = self.afc if self.settings.model.packet_mode else None
        for level, text in self.logger.tick(now, lambda: self.rx.interval_row(since, now, afc)):
            self.event(level, "ARCHIVE", text)
        self._logger_t = now

    def export_csv(self, t_from: str | float, t_to: str | float, what: str | list[str],
                   source: str = "web") -> dict[str, Any]:
        """CSV export of [t_from, t_to) into <archive>/exports (dssq.export), file names chosen
        here. One export at a time; the archive is committed first so the files hold every row
        so far; the export itself reads through its own read-only connection WITHOUT the
        station lock (the radio thread never waits for it). Refused while the free-space guard
        pauses logging. Raises csv_export.ExportError for a bad request."""
        t0, t1 = csv_export.parse_time(t_from), csv_export.parse_time(t_to)
        names = csv_export.parse_products(what)
        if not t0 < t1:
            raise csv_export.ExportError("'from' must be earlier than 'to'")
        if not self.archive_enabled:
            return {"status": "REFUSED", "message": "the archive is disabled (--no-archive)"}
        if not self._export_lock.acquire(blocking=False):
            return {"status": "BUSY", "message": "another export is running; try again when it is done"}
        try:
            with self.lock:
                if self.logger.paused:
                    return {"status": "REFUSED", "message": "free space below [gds] archive_min_free_mb: "
                                                            "logging is paused, no export"}
                self.archive.commit()
                floor = self.logger.min_free_bytes
            try:
                res = csv_export.export_range(self.archive.db_path, self.exports_dir, t0, t1, names, floor)
            except (OSError, sqlite3.Error) as e:
                with self.lock:
                    self.event("WARN", "EXPORT", f"CSV export ({source}) failed: {type(e).__name__}: {e}"[:300])
                return {"status": "ERROR", "message": f"export failed: {type(e).__name__}: {e}"[:300]}
            except csv_export.ExportError as e:
                return {"status": "REFUSED", "message": str(e)}
            rows = sum(r.rows for r in res)
            msg = (f"CSV export {csv_export.iso_utc(t0)} .. {csv_export.iso_utc(t1)} ({source}): "
                   + ", ".join(f"{r.name} {r.rows} rows" for r in res))
            with self.lock:
                self.event("INFO", "EXPORT", msg)
            return {"status": "OK", "message": f"{len(res)} file(s), {rows} rows", "from": t0, "to": t1,
                    "files": [r.as_dict() for r in res]}
        finally:
            self._export_lock.release()

    def _archive_health(self, now: float) -> None:
        """Archive writes are best-effort (archive.py): a failed write (disk full) is counted
        there and reported here - once a minute as an event, YELLOW while it lasts."""
        a = self.archive
        self.health["archive_write_errors"], self.health["archive_last_error"] = a.write_errors, a.last_error
        if a.write_errors > self._archive_errors_seen:
            self._archive_errors_seen = a.write_errors
            self._archive_error_t = now
            self._limited_event("archive_write", 60.0, "WARN", "ARCHIVE",
                                f"archive write failed ({a.write_errors} so far): {a.last_error} - reception and "
                                "commanding continue, the recording is incomplete")
            self._archive_errors_seen = a.write_errors     # the event's own failed write is not news

    def _radio_link_events(self, now: float) -> None:
        b = self.radio_backend
        connected = bool(getattr(b, "connected", True)) if b is not None else False
        if self._radio_connected is not None and connected != self._radio_connected:
            name = self.settings.model.name
            if connected:
                self.event("INFO", "RADIO", f"ground {name} link connected")
            else:
                detail = getattr(b, "last_error", "") if b is not None else (self.radio_unavailable or "")
                self.event("ERROR", "RADIO", f"ground {name} link lost{': ' + str(detail) if detail else ''}")
        if connected:
            self._radio_down_since = None
        elif self._radio_down_since is None:
            self._radio_down_since = now
        self._radio_connected = connected

    def _compute_alarm(self, now: float) -> tuple[str, list[str], int]:
        """(level, reasons RED first then YELLOW (PB-5.5), number of RED reasons)."""
        red: list[str] = []
        yellow: list[str] = []
        for pname, p in self.tlm.items():
            for k, s in p["limits"].items():
                if s == "RED":
                    red.append(f"{pname}.{k} RED limit")
                elif s == "YELLOW":
                    yellow.append(f"{pname}.{k} YELLOW limit")
        st = self.rx
        if st.los and st.passes:
            since = None if st.last_frame_t is None else now - st.last_frame_t
            if not self._hibernating(now):
                red.append("LOS")
            elif since is not None and since > HIBERNATE_LOS_ALARM_S:
                red.append(f"LOS: no beacon for {since:.0f} s while hibernating")
        lvl = self.beacon["level"]
        if lvl == 3:
            red.append("beacon URGENT")
        elif lvl == 2:
            yellow.append("beacon IMPORTANT")
        fstate = st.freq_state()[1]
        if fstate == "ALARM":
            red.append("frequency offset > 80 % of FERR_L")
        elif fstate == "WARN":
            yellow.append("frequency offset > 50 % of FERR_L")
        gf = self.ground_radio_fault
        if gf and now - gf["t"] < 5:
            red.append(f"ground radio fault: {gf['text']}")
        if self.radio_unavailable:
            red.append(f"radio backend unavailable: {self.radio_unavailable}")
        elif self._radio_down_since is not None and now - self._radio_down_since > RADIO_LINK_DOWN_RED_S:
            red.append(f"ground radio link down for {now - self._radio_down_since:.0f} s")
        if any(now - t < EPOCH_REGRESSION_WINDOW_S for t in self._epoch_regressed_times):
            red.append("TM epoch went backwards with a valid MAC: replayed recording or EGSE boot-counter "
                       "regression (possible GCM nonce reuse) - investigate; generate a new key if the counter "
                       "regressed")
        hk = self.tlm.get("HK", {}).get("values", {})
        fdir = _int_or_none(hk.get("fdir_flags")) or 0
        other = fdir & ~(FDIR_HIBERNATE | FDIR_TX_INHIBIT) & 0xFFFF
        if other:
            yellow.append("FDIR " + ",".join(decode_bits(other, FDIR_BITS)))
        if hk.get("reset_cause") == RESET_BROWNOUT:
            yellow.append("spacecraft reset cause BROWNOUT (check the radio supply)")
        if self.sensor_bus.separated(_int_or_none(hk.get("sensor_health"))) is False:
            yellow.append(NOT_SEPARATED_REASON)        # D-73 separation rule (flight WARN 0x060D)
        recent = sum(1 for t in self._sdls_tm_rejects if now - t < RECENT_WINDOW_S)
        if recent:
            yellow.append(f"{recent} SDLS TM rejects in the last 60 s")
        if self.mismatch_suspected:
            yellow.append("SDLS service mismatch suspected (spacecraft TM without AE header)")
        if self.model_mismatch:
            yellow.append(f"radio model mismatch: spacecraft {self.model_seen}, station {self.settings.model.name}")
        hw = getattr(self.radio_backend, "hw_mismatch", None) if self.settings.model.packet_mode else None
        if hw:                                           # I3: SX126x sketch chip / TCXO vs [radio.sx126x]
            yellow.append(f"ground SX126x hardware mismatch: {hw}")
        if self.afc.state == "FAULT":
            yellow.append("AFC FAULT (frequency-error sign check failed twice): AFC RESET after checking")
        if self.key_error and (self.sdls_tc_configured or self.sdls_tm_configured):
            yellow.append(self.key_error)
        if self.sdls_tc.uplink_blocked:
            yellow.append(f"TC uplink BLOCKED: {self.sdls_tc.blocked}")
        if self.logger.paused:
            free = self.logger.free_bytes
            yellow.append(f"archive logging paused: free space {'?' if free is None else f'{free / 1e6:.0f}'} MB "
                          f"< [gds] archive_min_free_mb {self.logger.min_free_bytes / 1e6:.0f} MB (raw CADU, rx_stats)")
        if self._archive_error_t is not None and now - self._archive_error_t < RECENT_WINDOW_S:
            yellow.append(f"archive write errors in the last 60 s ({self.archive.last_error})")
        if any(now - t < RECENT_WINDOW_S for t in self._stage_error_times):
            yellow.append("GDS stage errors in the last 60 s (health.stage_errors)")
        reasons = red + yellow
        return ("RED" if red else "YELLOW" if yellow else "NONE"), reasons, len(red)

    def stage_failed(self, stage: str, exc: BaseException) -> bool:
        """Runner error boundary book-keeping; True when the traceback should be logged."""
        with self.lock:
            now = time.time()
            se = self.health["stage_errors"]
            se[stage] = se.get(stage, 0) + 1
            self.health["last_error"] = {"stage": stage, "text": f"{type(exc).__name__}: {exc}"[:300], "t": now}
            self._stage_error_times.append(now)
            key = f"stage_{stage}"
            last = self._limited.get(key)
            if last is None or now - last >= STAGE_LOG_EVERY_S or now < last:
                self._limited[key] = now
                self.event("WARN", "GDS", f"stage '{stage}' failed: {type(exc).__name__}: {exc}"[:300])
                return True
            return False

    # -------------------------------------------------------- station I/O MCU
    def station_hello(self, fw: str, kb: bool, rcu_last: str | None = None, rcu_age_s: float | None = None) -> None:
        """The MCU's hello (display/frontpanel.py parse_hello). The time of its last RCU mode
        change is the hello time minus the reported age (whole seconds), kept while the
        report stays the same so it does not jitter by a second between hellos."""
        with self.lock:
            now = time.time()
            io = self.station_io
            t = None if rcu_age_s is None else now - rcu_age_s
            if t is not None and rcu_last == io["rcu_last"] and io["rcu_last_t"] is not None and \
                    abs(t - io["rcu_last_t"]) < 2.0:
                t = io["rcu_last_t"]
            io.update(connected_t=now, fw=fw, kb=kb, rcu_last=rcu_last, rcu_last_t=t)

    def station_input(self, text: str) -> None:
        with self.lock:
            self.station_io["input"] = text[:128]

    def station_status_sent(self, line: str) -> None:
        with self.lock:
            self.station_io["last_status_line"] = line
            parts = line.split("|")
            self.station_io["beacon_sent"] = int(parts[4]) if len(parts) == 5 and \
                re.fullmatch(r"-?[0-9]", parts[4]) else None

    def panel_status(self) -> dict[str, Any]:
        """The values of the station annunciator S/Q lines and of the LED-matrix M line
        (cheap, no snapshot)."""
        with self.lock:
            f = self.fop
            return {"los": self.rx.los, "alarm": self.alarm,
                    "uplink": bool(f.sent or f.bypass_q or f.queue),
                    "beacon": self.beacon["level"],
                    "pending_ids": [k for k, p in self.pending_confirm.items() if not p["confirmed"]],
                    "matrix": self._matrix_status(time.time())}

    def _matrix_status(self, now: float) -> dict[str, Any]:
        """The station MCU's LED-matrix fields (D-78; frontpanel.matrix_line). Only what this
        GDS measures; None = not produced, and the MCU leaves that row blank:
          tx            the GDS handed CLTUs or the identification to the radio and their
                        planned air time is not over, held PANEL_TX_HOLD_S (one status line)
                        so a short burst shows (the SX126x sketch adds its own TX state)
          rssi_dbm      the last reception's calibrated RSSI, rssi_recent = within the LOS
                        timeout
          rssi_overload receiver overload of that reading: not classified yet (None)
          margin_db, margin_floor  margin against the worst-case sensitivity and whether
                        it is only an upper bound (noise floor): not produced yet (None)
          lost_10m      frames lost in the last 10 minutes of reception (the link-manager
                        FER window: a LOS gap is not counted)
          clock_synced  station clock synchronisation: not produced yet (None)"""
        rx = self.rx
        t = rx.last_rx.get("t")
        los_s = rx.los_timeout_s if rx.los_timeout_s is not None else float(self.cfg["gds"]["los_timeout_s"])
        return {"sync": "PACKET" if self.settings.model.packet_mode else self.sync.state,
                "tx": self.mono() < self._uplink_tx_until + PANEL_TX_HOLD_S,
                "rssi_dbm": rx.last_rx.get("rssi_dbm"),
                "rssi_recent": t is not None and 0.0 <= now - t <= los_s,
                "rssi_overload": None, "margin_db": None, "margin_floor": None,
                "lost_10m": int(math.fsum(rx.fer_lost.values(now))),
                "clock_synced": None}

    # -------------------------------------------------------------- snapshot
    def _radio_link_snapshot(self) -> dict[str, Any]:
        b = self.radio_backend
        if b is None:
            return {"connected": False, "error": self.radio_unavailable}
        if self.settings.model.packet_mode:
            snap = b.snapshot() if hasattr(b, "snapshot") else {}
            return {"connected": bool(getattr(b, "connected", False)), "mcu_age_s": snap.get("mcu_age_s")}
        return {"connected": bool(getattr(b, "connected", True)), "faults": getattr(b, "faults", 0),
                "reopens": getattr(b, "reopens", 0), "tx_dropped": getattr(b, "tx_dropped", 0),
                "last_error": getattr(b, "last_error", ""), "port": getattr(b, "port", None)}

    def _decoded(self) -> dict[str, Any]:
        hk = self.tlm.get("HK", {}).get("values", {})
        rf = self.tlm.get("RF", {}).get("values", {})
        fdir = _int_or_none(hk.get("fdir_flags"))
        sns = _int_or_none(hk.get("sensor_health"))
        mid, code = _int_or_none(rf.get("radio_model")), _int_or_none(rf.get("air_rate_code"))
        model = link.MODEL_IDS.get(mid) if mid is not None else None
        rate_label = bps = None
        if model is not None and code is not None and 0 <= code <= 7:
            rate_label, bps = link.rate_label(model, code), link.rate_bps(model, code)
        mc = _int_or_none(hk.get("magcal_state"))
        return {"mode": MODES.get(hk.get("fsw_mode")),
                "fdir": decode_bits(fdir, FDIR_BITS) if fdir is not None else [],
                "sensors_ok": [n for i, n in enumerate(SENSOR_BITS) if sns is not None and sns >> i & 1],
                "sensors_missing": [n for i, n in enumerate(SENSOR_BITS) if sns is not None and not sns >> i & 1],
                "reset_cause": RESET_CAUSES.get(hk.get("reset_cause")),
                "farm_state": FARM_STATES.get(hk.get("farm_state")),
                "radio_fault": RADIO_FAULTS.get(hk.get("radio_fault")),
                "radio_model": RADIO_MODELS.get(mid) if mid is not None else None,
                "framing": FRAMINGS.get(rf.get("framing")), "rate_label": rate_label, "air_rate_bps": bps,
                "sdls_tc_service": SDLS_SERVICES.get(hk.get("sdls_tc_service")),
                "sdls_tm_service": SDLS_SERVICES.get(hk.get("sdls_tm_service")),
                "sdls_tm_state": SDLS_TM_STATES.get(hk.get("sdls_tm_state")),
                "cfdp_state": CFDP_STATES.get(hk.get("cfdp_state")), "seq_state": SEQ_STATES.get(hk.get("seq_state")),
                "beacon": BEACON_LEVELS.get(hk.get("beacon_level")),
                "mag_bist": decode_mag_bist(_int_or_none(hk.get("mag_bist"))),
                "magcal": {"ob_active": None if mc is None else bool(mc & 1),
                           "ib_active": None if mc is None else bool(mc & 2)},
                "rf_flags": decode_bits(_int_or_none(rf.get("rf_flags")), RF_FLAG_BITS),
                "mag_flags": decode_bits(_int_or_none(self.tlm.get("MAG", {}).get("values", {}).get("flags")),
                                         MAG_FLAG_BITS)}

    def _magcal_snapshot(self) -> dict[str, Any]:
        hk = self.tlm.get("HK", {}).get("values", {})
        mc = _int_or_none(hk.get("magcal_state"))
        units = {}
        for u, d in self.magcal_units.items():
            col = d["collector"]
            units[str(u)] = {"collecting": d["collecting"], "coverage": col.live_coverage() if col else None,
                             "averaged_skipped": d["averaged_skipped"],
                             "fit": d["fit"].as_dict() if d["fit"] is not None else None, "command": d["command"]}
        return {"field_ut": self.field_ut, "units": units,
                "onboard": {"state": mc, "ob_active": None if mc is None else bool(mc & 1),
                            "ib_active": None if mc is None else bool(mc & 2)},
                "bist": decode_mag_bist(_int_or_none(hk.get("mag_bist")))}

    def snapshot(self) -> dict[str, Any]:
        """The dashboard state (PB-5): ONE JSON-safe dict built inside the lock (it shares no
        container with the live state). Never contains `science` (science_snapshot())."""
        with self.lock:
            now = time.time()
            hk = self.tlm.get("HK", {}).get("values", {})
            rx = self.rx.snapshot(now, self.afc if self.settings.model.packet_mode else None, list(self.afc_events))
            rx["radio_link"] = self._radio_link_snapshot()
            sc_model, sc_code = self._sc_radio()
            sx = self.radio_backend.snapshot() if self.settings.model.packet_mode and \
                self.radio_backend is not None and hasattr(self.radio_backend, "snapshot") else None
            rm = self.radio_mgr
            sdls = {**link_snapshot(self.sdls_tc, self.sdls_tm),
                    "spacecraft_tm_state": SDLS_TM_STATES.get(hk.get("sdls_tm_state")),
                    "mismatch_suspected": self.mismatch_suspected,
                    "tm_epoch_regressed": self.sdls_tm.epoch_regressed, "key_file": self.key_file,
                    "key_loaded": self.key_loaded, "tc_sn_state": self.sdls_tc.sn_state,
                    "tc_service_configured": SERVICE_NAMES[self.sdls_tc_configured],
                    "tm_service_configured": SERVICE_NAMES[self.sdls_tm_configured]}
            vc = {}
            for v in range(NUM_DATA_VCS):
                name = VC_NAMES[v]
                vc[name] = {**self.extract[v].stats, **self.vc_info[name],
                            "lost": self.rx.per_vc.get(v, {}).get("lost", 0)}
            oid_vc = 63 if self.framing_seen.get("last") == "USLP" else 7
            vc["OID"] = {**self.vc_info["OID"], "lost": self.rx.per_vc.get(oid_vc, {}).get("lost", 0)}
            n_corr = len(self.corr.samples)
            seq_hk = {"state": hk.get("seq_state"), "state_name": SEQ_STATES.get(hk.get("seq_state")),
                      "count": hk.get("seq_count"), "next_dt_s": hk.get("seq_next_dt_s")}
            snap: dict[str, Any] = {
                "schema": SCHEMA_VERSION, "version": __version__, "utc": now,
                "station": self.cfg["station"]["name"], "spacecraft": self.cfg["spacecraft"]["name"],
                "uptime_s": now - self.t_start, "mode": self.mode,
                "alarm": self.alarm, "alarm_reasons": list(self.alarm_reasons),
                "alarm_red_count": self.alarm_red_count,
                "beacon": self.beacon,
                "rx": rx,
                "radio": {**link.describe(self.settings), "model_seen": self.model_seen,
                          # The RCU drives the E22 M0/M1/AUX lines; a packet (SX126x) manager has none.
                          "model_mismatch": self.model_mismatch,
                          "rcu": bool(rm and rm.rcu is not None and rm.available),
                          "backend_available": self.radio_backend is not None,
                          "backend_error": self.radio_unavailable,
                          "ground_config": self.ground_radio,
                          "ground_fault": (self.ground_radio_fault if self.ground_radio_fault and
                                           now - self.ground_radio_fault["t"] < 5 else None),
                          "last_scan": rm.last_scan if rm else None,
                          "allowed_channels": self.allowed_channels,
                          "configured": {k: self.cfg["radio"][k] for k in ("channel", "air_rate", "power", "lbt")},
                          "link_change": self.link_change, "jobs_pending": len(self.radio_jobs), "sx126x": sx,
                          "sc_model": sc_model.name, "sc_rate_code": sc_code,
                          "sc_airtime_s": link.airtime_s(sc_model, sc_code)[0],
                          "sc_busy_s_est": link.busy_s(sc_model, sc_code),
                          "frame_period_max_s_est": self._frame_period_s()},
                "link": {"tm_framing_cfg": self.cfg["link"]["tm_framing"], "tm_framing_seen": self.framing_seen,
                         "tc_framing": self.fop.framing, "sdls": sdls, "ident": self.ident,
                         "station_id": self.station_id.snapshot()},
                "sync": {"state": "PACKET" if self.settings.model.packet_mode else self.sync.state, **self.sync.stats},
                "vc": vc,
                "apids": {self.decom.packet_name(a): {
                    "apid": a, "count": s["count"], "seq_gaps": s["seq_gaps"], "lost": s["lost"],
                    "last_seq": s["last_seq"], "age_s": None if s["last_ert"] is None else now - s["last_ert"],
                    "iat_s": s["iat_s"], "latency": s["latency"].stats(now), "size": s["size"], "vcid": s["vcid"]}
                    for a, s in sorted(self.apid_stats.items())},
                "tlm": self.tlm,
                "decoded": self._decoded(),
                # D-75 / D-73: 6-DOF ground products of the newest ATT, instrument buses (dssq/sensors.py)
                "imu": imu_products(self.tlm.get("ATT", {}).get("values"), float(self.cfg["science"]["still_rate_dps"]),
                                    float(self.cfg["science"]["still_vib_mg"])),
                "sensor_bus": self.sensor_bus.snapshot(_int_or_none(hk.get("sensor_health"))),
                "time": {"partition": self.partition, "last_sclk": self.last_sclk,
                         "scet_of_last_sclk": self.corr.scet(self.last_sclk) if self.last_sclk is not None else None,
                         # PB-5.1 "nothing is invented": a drift needs two samples, a residual
                         # three (a line through two points always fits exactly).
                         "corr_samples": n_corr, "drift_ppm": self.corr.drift_ppm if n_corr >= 2 else None,
                         "residual_rms_ms": self.corr.residual_rms * 1000 if n_corr >= 3 else None,
                         "owlt_s": self.owlt_s,
                         "timecorr_key": self.timecorr_key_name, "sclk_estimate_s": self.sclk_estimate_s(now)},
                "cop": {**self.fop.state(), "tc_framing": self.fop.framing,
                        "sdls_service": self.sdls_tc.service_name, "sdls_sn": self.sdls_tc.sn,
                        "frame_limit": TC_MAX_FRAME_LEN},
                "cfdp": {**self.cfdp.snapshot(), "files_dir": str(self.files_dir),
                         "onboard": {"state": hk.get("cfdp_state"), "state_name": CFDP_STATES.get(hk.get("cfdp_state")),
                                     "seq": hk.get("cfdp_seq"), "progress_pct": hk.get("cfdp_progress")}},
                "tdm": {"enabled": self.archive_enabled, "auto_write": bool(self.cfg["tdm"]["auto_write"]),
                        "records": len(self.tdm), "dropped": self.tdm.dropped, "aos_t": self.rx.aos_t,
                        **self.tdm_state, "files": self._tdm_files()},
                "sequence": {"onboard": seq_hk, "file": self.seq_file, "uplink": self.seq_uplink_state,
                             "sclk_estimate_s": self.sclk_estimate_s(now)},
                "magcal": self._magcal_snapshot(),
                "health": self.health,
                "logger": self.logger.health(now),
                "linkmgr": self.link_mgr.state(),
                "pending_confirm": [{"id": k, "text": v["text"], "kind": v["kind"], "confirmed": v["confirmed"],
                                     "authorized": v["authorized"], "t": v["t"],
                                     "expires_s": max(0.0, CONFIRM_EXPIRY_S - (now - v["t"]))}
                                    for k, v in self.pending_confirm.items()],
                "evrs": list(self.evrs)[-40:],
                "events": list(self.events)[-80:],
                "anomalies": list(self.anomaly.recent)[-20:],
                "console": list(self.console_lines),
                "station_io": {**self.station_io,
                               "connected": bool(self.station_io["connected_t"]
                                                 and now - self.station_io["connected_t"] < STATION_IO_STALE_S)},
                "cmd_stages": CMD_STAGES, "cmd_errors": CMD_ERRORS, "cmd_sources": CMD_SOURCES,
            }
            out = json_safe(snap)
            assert isinstance(out, dict)
            return out

    def science_snapshot(self) -> dict[str, Any]:
        """PB-5.3.8 (/api/science only): the spectral analytics plus [science] and the LED limits."""
        with self.lock:
            sci = self.spectral.snapshot(time.time())
            sci["cfg"] = dict(self.cfg["science"])
            sci["led_limits"] = {"max_code": {"white": LED_MAX_CODE[0], "IR": LED_MAX_CODE[1], "UV": LED_MAX_CODE[2]},
                                 "allowed_led_cur": list(LED_CUR_ALLOWED)}
            for e in self.spectral.drain_events():
                self.event(e["level"], e["source"], e["text"], e["t"])
            out = json_safe(sci)
            assert isinstance(out, dict)
            return out

    def flush(self) -> None:
        """Shutdown: archive committed and synced (the SDLS state is written per frame)."""
        with self.lock:
            self.archive.flush()

    def close(self) -> None:
        with self.lock:
            self.archive.close()
            if self._udp is not None:
                self._udp.close()
                self._udp = None


# =============================================================================
# Runner: one bounded iteration per step()
# =============================================================================
class _SimRcu:
    """Simulated Radio Control Unit (the simulator's ground E22 model)."""

    def __init__(self, sim: Any):
        self.sim = sim

    def set_mode(self, m: int) -> bool:
        return bool(self.sim.rcu_set_mode(m))


def _bounded(name: str, fn: Callable[[], object], timeout_s: float) -> bool:
    """Runs fn in a helper thread and waits at most timeout_s; an overrun is abandoned
    (logged) so the rest of the shutdown still runs. True when fn completed."""
    if timeout_s <= 0:
        log.error("shutdown step '%s' skipped: no time left", name)
        return False
    err: list[BaseException] = []

    def body() -> None:
        try:
            fn()
        except Exception as e:                           # shutdown step boundary: log, continue
            err.append(e)

    t = threading.Thread(target=body, daemon=True, name=f"close-{name}")
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        log.error("shutdown step '%s' did not finish within %.1f s: abandoned", name, timeout_s)
        return False
    if err:
        log.error("shutdown step '%s' failed: %s", name, err[0])
        return False
    return True


class Runner:
    """The GDS main loop for the host CLI and App Lab (PB-8.4)."""

    def __init__(self, cfg: dict[str, Any], *, bridge: Any = None, mode: str = "host", sim: bool = False,
                 sim_loss: float = 0.05, sim_errors: int = 3, archive: bool = True, web: bool = True) -> None:
        self.cfg = cfg
        self.mode = "sim" if sim else mode
        # Checked before anything starts: a refused address must not leave threads behind.
        listen = cfg["interop"]["tc_frames_udp_listen"]
        if listen:
            station_config.parse_udp_listen(listen)
        self.gs = GroundStation(cfg, archive=archive, mode=self.mode)
        self.link: Any = bridge
        self._own_link = False
        self.fp: Any = None
        self.radio: Any = None
        self.web_srv: Any = None
        self._closed = False
        self._data = b""
        self._read_fail_streak = 0
        self._last_console = time.time()
        self._e22_retry_t = 0.0
        self._reg_retry_t = 0.0
        self._fp_pending = False                   # FrontPanel registration failed: retried by _stage_register
        self._sx_pending = False                   # SX126x backend attach failed: retried by _stage_register
        self._udp_stop = threading.Event()
        self._udp_sock: socket.socket | None = None
        self._udp_thread: threading.Thread | None = None
        gs = self.gs
        model = gs.settings.model
        rcu: Any = None
        unavailable: str | None = None
        g = cfg["gds"]
        if sim:
            from .sim import SimSpacecraft
            sim_framing = "USLP" if str(cfg["link"]["tm_framing"]).upper() == "USLP" else "TM"
            self.radio = SimSpacecraft(cfg, loss=sim_loss, max_errors=sim_errors, sdls_key=gs.sdls_tc.key,
                                       framing=sim_framing)
            gs.event("INFO", "GDS", "SIMULATION MODE - software spacecraft, no RF")
            if not model.packet_mode:
                rcu = _SimRcu(self.radio)
        else:
            need = bool(g["frontpanel"]) or model.packet_mode or bool(g["rcu"])
            if self.link is None and need:
                from .display.frontpanel import BridgeLink
                try:
                    self.link = BridgeLink.from_router(g["router_address"] or None)
                    self._own_link = True
                except (ImportError, ConnectionError) as e:
                    self.link = None
                    gs.event("WARN", "GDS",
                             f"router bridge unavailable: front panel / RCU / SX126x radio disabled ({e})")
            if self.link is not None and g["frontpanel"]:
                from .display.frontpanel import FrontPanel
                try:
                    self.fp = FrontPanel(gs, self.link)
                    self.fp.start()
                except BRIDGE_ERRORS as e:
                    self.fp = None
                    self._fp_pending = True
                    self._reg_retry_t = time.monotonic() + REGISTER_RETRY_S
                    gs.event("WARN", "GDS", f"front panel not started: {e} (retried every {REGISTER_RETRY_S:.0f} s)")
            if model.packet_mode:
                if self.link is None:
                    unavailable = "no Router Bridge for the SX126x radio (station MCU)"
                else:
                    from .radio.sx126x import Sx126xBridge
                    try:
                        self.radio = Sx126xBridge(self.link, cfg["radio"], freq_offset_hz=int(gs.afc.correction_hz))
                        self.radio.attach()
                    except BRIDGE_ERRORS as e:
                        self.radio = None
                        self._sx_pending = True
                        self._reg_retry_t = time.monotonic() + REGISTER_RETRY_S
                        unavailable = f"SX126x backend not attached: {e} (retried every {REGISTER_RETRY_S:.0f} s)"
            else:
                unavailable = self._open_e22()
                if self.link is not None and g["rcu"]:
                    from .display.frontpanel import _Rcu
                    rcu = self.fp.rcu() if self.fp is not None else _Rcu(self.link)
        mgr: RadioManager | None = None
        if self.radio is not None:
            if model.packet_mode:
                mgr = RadioManager(self.radio, None)
            elif rcu is not None:
                mgr = RadioManager(self.radio, rcu, model=model.name)
        gs.attach_radio(self.radio, mgr, unavailable)
        if web:
            from .display.web import start_web
            self.web_srv = start_web(gs, g["http_host"], int(g["http_port"]))
        listen = cfg["interop"]["tc_frames_udp_listen"]
        if listen:
            self._start_udp_listener(listen)

    # ------------------------------------------------------------ radio open
    def _open_e22(self) -> str | None:
        from .radio.e22 import E22Serial
        port = self.cfg["radio"]["port"]
        try:
            self.radio = E22Serial(port)
        except OSError as e:
            self.radio = None
            self._e22_retry_t = time.monotonic() + E22_REOPEN_EVERY_S
            return f"{self.gs.settings.model.name} serial port: {e}"
        return None

    def _retry_e22(self) -> None:
        if self.gs.settings.model.packet_mode or time.monotonic() < self._e22_retry_t:
            return
        reason = self._open_e22()
        if reason is None:
            gs = self.gs
            with gs.lock:
                gs.radio_unavailable = None
                gs.radio_backend = self.radio
                if gs.radio_mgr is None and self.link is not None and self.cfg["gds"]["rcu"]:
                    from .display.frontpanel import _Rcu
                    rcu = self.fp.rcu() if self.fp is not None else _Rcu(self.link)
                    gs.radio_mgr = RadioManager(self.radio, rcu, model=gs.settings.model.name)
                elif gs.radio_mgr is not None:
                    gs.radio_mgr.radio = self.radio
                gs.event("INFO", "RADIO", f"{self.cfg['radio']['port']} opened")
        else:
            with self.gs.lock:
                self.gs.radio_unavailable = reason

    # -------------------------------------------------------------- interop
    def _start_udp_listener(self, addr: str) -> None:
        """[interop] tc_frames_udp_listen: frames from an external MCS, re-wrapped and radiated
        without authentication or CONFIRM (docs/17 T16), so the address is re-checked here
        (loopback only, ValueError otherwise) even when the configuration bypassed validate()."""
        family, host, port = station_config.parse_udp_listen(addr)
        s = socket.socket(family, socket.SOCK_DGRAM)
        s.bind((host, port))
        s.settimeout(0.5)
        self._udp_sock = s

        def run() -> None:
            while not self._udp_stop.is_set():
                try:
                    data, _ = s.recvfrom(2048)
                except TimeoutError:
                    continue
                except OSError:
                    return
                if 7 <= len(data) <= TC_MAX_FRAME_LEN:
                    with self.gs.lock:
                        self.gs.external_tc.append(data)

        self._udp_thread = threading.Thread(target=run, daemon=True, name="udp-tc")
        self._udp_thread.start()

    # ---------------------------------------------------------------- stages
    def _stage_register(self) -> None:
        """Retries the Router Bridge registrations that failed at start (App Lab: the first
        use of arduino.app_utils.Bridge raises AppError when the router is not reachable
        within 5 s). Without this the CardKB / annunciators and the SX126x radio would stay
        disabled for the whole run although the router comes up seconds later."""
        if not (self._fp_pending or self._sx_pending) or self.link is None or \
                time.monotonic() < self._reg_retry_t:
            return
        self._reg_retry_t = time.monotonic() + REGISTER_RETRY_S
        gs = self.gs
        if self._fp_pending:
            from .display.frontpanel import FrontPanel
            try:
                fp = FrontPanel(gs, self.link)
                fp.start()
            except BRIDGE_ERRORS as e:
                log.info("front panel registration retry failed: %s", e)
            else:
                self.fp, self._fp_pending = fp, False
                gs.event("INFO", "GDS", "front panel started (Router Bridge reachable)")
        if self._sx_pending:
            from .radio.sx126x import Sx126xBridge
            try:
                with gs.lock:
                    foff = int(gs.afc.correction_hz)
                radio = Sx126xBridge(self.link, self.cfg["radio"], freq_offset_hz=foff)
                radio.attach()
            except BRIDGE_ERRORS as e:
                with gs.lock:
                    gs.radio_unavailable = f"SX126x backend not attached: {e} (retried every " \
                                           f"{REGISTER_RETRY_S:.0f} s)"
            else:
                self.radio, self._sx_pending = radio, False
                gs.attach_radio(radio, RadioManager(radio, None), None)
                gs.event("INFO", "RADIO", "SX126x backend attached (Router Bridge reachable)")

    def _stage_radio_read(self) -> None:
        if self.radio is None:
            self._data = b""
            self._retry_e22()
            time.sleep(0.05)                               # no backend: no busy loop
            return
        self._data = self.radio.read()

    def _stage_ingest(self) -> None:
        data, self._data = self._data, b""
        self.gs.ingest(data, time.time())

    def _stage_packets(self) -> None:
        if self.radio is not None and getattr(self.radio, "packet_mode", False):
            for p in self.radio.packets():
                self.gs.ingest_packet(p)

    def _stage_outbox(self) -> None:
        chunks = self.gs.take_outbox()
        if self.radio is None:
            return
        for chunk in chunks:
            self.radio.write(chunk)

    def _stage_jobs(self) -> None:
        if self.gs.radio_jobs and self.gs.radio_mgr is not None:
            self.gs.run_radio_jobs()

    def _stage_afc(self) -> None:
        """Sends AFC corrections with the backend's rf_foff(hz) -> 1 | -1 | -2 | None (contract
        24.8; Sx126xBridge.rf_foff, an alias of its set_freq_offset, which is used for a
        backend without rf_foff). No backend / no answer confirms as AFC_RPC_NO_ANSWER."""
        for corr in self.gs.take_afc_out():
            result: int | None = None
            radio = self.radio
            fn = None if radio is None else (getattr(radio, "rf_foff", None) or
                                             getattr(radio, "set_freq_offset", None))
            if fn is None:
                if radio is not None:
                    self.gs.note_no_rf_foff()
            else:
                try:
                    r = fn(corr)
                    result = r if isinstance(r, int) and not isinstance(r, bool) else None
                except BRIDGE_ERRORS as e:
                    log.warning("rf_foff(%d) failed: %s", corr, e)
            self.gs.afc_confirm(corr, result)

    def _stage_tdm(self) -> None:
        self.gs.tdm_service()

    def _stage_housekeeping(self) -> None:
        self.gs.housekeeping(time.time())

    def _stage_console(self) -> None:
        now = time.time()
        if now - self._last_console < float(self.cfg["gds"]["console_interval_s"]):
            return
        self._last_console = now
        gs = self.gs
        with gs.lock:
            r = gs.rx
            rssi, noise = r.last_rx["rssi_dbm"], r.noise_dbm
            snr = None if rssi is None or noise is None else rssi - noise
            sx = gs.settings.model.packet_mode
            log.info("STATUS %s sync=%s RSSI=%s dBm noise=%s dBm SNRest=%s dB frames=%d lost=%d RScorr=%d alarm=%s",
                     "LOS" if r.los else "AOS", "PACKET" if sx else gs.sync.state,
                     at_resolution(rssi, SX126X_RSSI_RESOLUTION_DB if sx else E22_RSSI_RESOLUTION_DB),
                     at_resolution(noise, 0.1 if sx else E22_RSSI_RESOLUTION_DB),
                     at_resolution(snr, 0.1 if sx else E22_RSSI_RESOLUTION_DB),
                     r.frames_ok, r.frames_lost, r.sym_corrected, gs.alarm)

    def step(self) -> None:
        """One bounded iteration. Every stage runs inside its own error boundary."""
        for name in STAGES:
            fn = getattr(self, f"_stage_{name}")
            try:
                fn()
            except Exception as e:                          # documented stage boundary (PB-8.4)
                if self.gs.stage_failed(name, e):
                    log.exception("GDS stage '%s' failed", name)
                if name == "radio_read":
                    self._read_fail_streak += 1
            else:
                if name == "radio_read":
                    self._read_fail_streak = 0
        if self._read_fail_streak >= READ_FAIL_SLEEP_AFTER:
            time.sleep(1.0)                                 # a dead radio must not make a busy loop

    # -------------------------------------------------------------- shutdown
    def close(self) -> None:
        """Idempotent; finishes within CLOSE_BUDGET_S (App Lab kills 5 s after SIGTERM)."""
        if self._closed:
            return
        self._closed = True
        deadline = time.monotonic() + CLOSE_BUDGET_S
        steps: list[tuple[str, Callable[[], object], float]] = []
        radio = self.radio
        if radio is not None:
            # The closing identification goes first, while the radio is surely still open. An ID
            # the last step queued but never wrote (the housekeeping runs after the outbox stage,
            # e.g. the LOS ID after a WAKE), or one still last in the SX126x bridge's backlog (the
            # MCU's TX queue was full), is sent here too; anything else left is not radiated. An ID
            # with uplinks queued behind it covers none of them: a new one goes after them. It
            # counts as sent only once the radio took it (SX126x: the MCU accepted it, bounded wait).
            with self.gs.lock:
                left = self.gs.take_outbox()
                sid = self.gs.station_id
                queued_last = getattr(radio, "queued_last", None)
                in_backlog = bool(callable(queued_last) and queued_last(sid.payload))
                closing_id = sid.payload if sid.pending or sid.payload in left or in_backlog else None
            if closing_id is not None:                 # the session ends with the station: closing ID
                ident_payload: bytes = closing_id
                gs = self.gs

                def ident() -> None:
                    if not in_backlog:
                        radio.write(ident_payload)
                    flush = getattr(radio, "flush_tx", None)
                    ok = bool(flush(IDENT_FLUSH_S)) if callable(flush) else True
                    with gs.lock:
                        if not ok:
                            gs.event("ERROR", "IDENT", "closing identification NOT radiated: the station MCU did "
                                     f"not take it within {IDENT_FLUSH_S:.1f} s (TX queue full or radio down)")
                        elif gs.station_id.pending:
                            gs.station_id.sent(time.time(), "end")
                steps.append(("ident", ident, IDENT_FLUSH_S + 0.2))
        srv = self.web_srv
        if srv is not None:
            def web_down() -> None:
                srv.shutdown()
                srv.server_close()
            steps.append(("web", web_down, 1.0))
        if self.fp is not None:
            steps.append(("frontpanel", lambda: self.fp.stop(1.0), 1.2))
        if self._udp_sock is not None:
            def udp_down() -> None:
                self._udp_stop.set()
                if self._udp_sock is not None:
                    self._udp_sock.close()
            steps.append(("udp", udp_down, 0.3))
        steps.append(("tdm", self.gs.tdm_final, 0.6))
        steps.append(("archive", self.gs.flush, 0.6))
        if radio is not None:
            steps.append(("radio", radio.close, 0.5))
        if self._own_link and self.link is not None:
            steps.append(("bridge", self.link.disconnect, 0.3))
        steps.append(("state", self.gs.close, 0.3))
        for name, fn, budget in steps:
            _bounded(name, fn, min(budget, deadline - time.monotonic()))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="DSS-Q ground data system")
    ap.add_argument("--config", default=None, help="station.toml (default: config/station.toml next to dssq/, "
                    "or <APP_DATA>/station.toml with --app-data)")
    ap.add_argument("--app-data", metavar="APP_DATA", default=None,
                    help="the ground App's data folder (host mode): its station.toml, archive, SDLS key and "
                         "web token, exactly as the App Lab App uses them")
    ap.add_argument("--port", help="override [radio] port (E22 models)")
    ap.add_argument("--sim", action="store_true", help="use the software spacecraft simulator")
    ap.add_argument("--sim-loss", type=float, default=0.05, help="simulated packet loss probability")
    ap.add_argument("--sim-errors", type=int, default=3,
                    help="no effect: the simulated E22 sends no octet errors (D-76); kept for existing scripts")
    ap.add_argument("--no-archive", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        if a.app_data:
            cfg = station_config.load_app_config(a.app_data, a.config)
        else:
            cfg = station_config.load_config(a.config)
    except (OSError, ValueError) as e:                     # TOMLDecodeError is a ValueError
        log.error("configuration: %s", e)
        return 2
    if a.port:
        cfg["radio"]["port"] = a.port
    runner = Runner(cfg, mode="host", sim=a.sim, sim_loss=a.sim_loss, sim_errors=a.sim_errors,
                    archive=not a.no_archive)
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    try:
        while not stop.is_set():
            runner.step()
    finally:
        runner.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
