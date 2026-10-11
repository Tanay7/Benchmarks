"""DSS-Q web display server: the operator dashboard and its HTTP API (Phase B PB-5.6).

Python standard library only (http.server.ThreadingHTTPServer): no external package, no
CDN, no web font. The dashboard (dashboard.html, ONE self-contained file) therefore works
on an isolated station network.

Endpoints
  GET  /, /index.html     dashboard.html, with a Content-Security-Policy whose script-src is
                          the SHA-256 of the page's one inline <script>
  GET  /api/state         GroundStation.snapshot(): receiver, link, spacecraft (no science)
  GET  /api/science       GroundStation.science_snapshot(): spectral analytics (PB-5.3.8)
  GET  /api/dictionary    commands, directives, packets, enumerations, spectrometer limits and
                          the Planckian / spectrum loci of the ground colorimetry model
  GET  /metrics           Prometheus text exposition derived from the snapshot
  GET  /api/xtce          XTCE 1.2 mission database (attachment vgq1.xtce.xml)
  GET  /api/tdm           CCSDS TDM files [{name, size, mtime}];  GET /api/tdm/<name>
  GET  /api/files         CFDP received files [{name, size, mtime, state}];  GET /api/files/<name>
  POST /api/cmd           {"text"}                       telecommand, COP-1 directive, ground directive
  POST /api/confirm       {"id", "role": CONFIRM|AUTH}   two-step confirmation
  POST /api/cancel        {"id"}
  POST /api/seq/check     {"text", "first_slot"}         stored-sequence check (no side effects)
  POST /api/seq/uplink    {"text", "first_slot"}         -> CONFIRM id; every SEQLOAD on confirm
  POST /api/magcal        {"action", "unit"}             MAGCAL collect / fit / uplink / clear
  POST /api/tdm/write     {}                             write the pass TDM now

Security model (PB-5.6, PB-5.7)
  * Commanding = every POST. With [gds] http_token set, a POST needs header X-DSSQ-Token equal
    to it (constant-time compare). A server bound to a non-loopback address WITHOUT a token
    refuses every POST (403): the dashboard stays read-only. An AUTH confirmation also needs
    X-DSSQ-Auth = [commanding] auth_token when that is set (the second operator's secret).
  * Cross-site request forgery / DNS rebinding (defence in depth, matters most for a loopback
    server without a token): a POST must be Content-Type application/json, which a browser
    cannot send cross-origin without a CORS preflight that this server never grants; when the
    browser sends Origin, its host must equal the Host header; a loopback-bound server also
    requires a loopback Host header, so a rebound domain name cannot read or command. A
    LAN-bound server (App Lab: 0.0.0.0) answers only a Host that is an IP literal, a
    single-label or mDNS <name>.local name (both resolve only on the LAN), this machine's
    host name, or an entry of [gds] http_allowed_hosts: a web page that rebinds its own DNS
    name to the board cannot read telemetry or downloaded files through a browser on the
    station LAN. Any other DNS name used to reach the dashboard must be listed in
    http_allowed_hosts.
  * The page itself inserts every telemetry-derived string as text (PB-5.7); its CSP hash
    source makes the browser refuse injected scripts and inline event handlers anyway.
  * Every response: Cache-Control no-store, X-Content-Type-Options nosniff, a restrictive CSP.
    Request bodies are limited to MAX_BODY octets; JSON is validated field by field; a file
    download accepts only a name matching a strict pattern that is a regular file (not a
    symbolic link) directly inside its directory.
  * Bounded resources: at most MAX_CLIENTS concurrent connections, at most
    MAX_CLIENTS_PER_IP of them from one address, a TOTAL deadline of REQUEST_DEADLINE_S
    for receiving the request line, headers and body (a client trickling one octet at a
    time cannot hold a slot), a socket timeout per send, HTTP/1.0 (one request per
    connection).
The request handler is a documented error boundary (PB-0.4): an unexpected exception in a
GroundStation call is logged and answered with 500; it never stops the server thread. The
web thread never calls the Router Bridge and never blocks on the radio (PB-5.6).
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import io
import ipaddress
import json
import logging
import math
import os
import re
import shutil
import socket
import threading
import time
from collections.abc import Callable, Mapping
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit

from .. import dictionary as dic
from .. import spectral
from ..cmdparse import SEQ_CMD_MAX, seq_forbidden_opcodes
from ..config import is_loopback
from ..gds import json_safe

log = logging.getLogger("dssq.web")

HTML_PATH = Path(__file__).with_name("dashboard.html")
MAX_BODY = 131_072                  # octets of a POST body (PB-5.6: larger -> 413)
DRAIN_MAX = 4 * MAX_BODY            # an oversized body is read and discarded up to this, so the
                                    # client receives the 413 instead of a connection reset
MAX_CLIENTS = 32                    # concurrent connections; more are closed at once
MAX_CLIENTS_PER_IP = 8              # of them from one client address (one host cannot take every slot)
REQUEST_DEADLINE_S = 10.0           # whole request (line, headers, body) must arrive within this
SOCKET_TIMEOUT_S = 15.0             # per send of a response (a socket timeout bounds ONE operation only)
CMD_TEXT_MAX = 1024                 # characters of one command line
SEQ_TEXT_MAX = 65_536               # characters of a sequence file (PB-5.6)
LIST_MAX = 1000                     # entries of /api/files and /api/tdm (newest first)
NAME_MAX = 160                      # characters of a downloadable file name
SIDECAR_MAX = 262_144               # octets of a CFDP .json report read for /api/files
COPY_CHUNK = 65_536
TDM_NAME_RE = re.compile(r"^VGQ1_[A-Za-z0-9_.-]+\.kvn$")
FILE_NAME_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}$")
STATE_RE = re.compile(r"^[A-Z_]{1,32}$")
SCRIPT_RE = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.S | re.I)
MAGCAL_ACTIONS = frozenset({"start", "stop", "reset", "fit", "uplink", "clear"})
ROLES = frozenset({"CONFIRM", "AUTH"})
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# Headers on every response. The API answers carry their own strict CSP: a JSON or a file
# opened directly in a browser can never run anything.
COMMON_HEADERS = (("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"),
                  ("Referrer-Policy", "no-referrer"), ("X-Frame-Options", "DENY"))
API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"

# SPEC v3.1 flags octet (PB-1.10): b0 valid, b1 white, b2 IR, b3 UV lamp actually driven,
# b4 AGC, b5 active (on-off), b6 any channel saturated, b7 attitude valid.
SPEC_FLAG_BITS = ["VALID", "WHITE", "IR", "UV", "AGC", "ACTIVE", "SATURATED", "ATTITUDE"]
# SPECCFG `ind` argument: indicator LED 0 off, 1..4 = 1/2/4/8 mA (dictionary SPECCFG text).
IND_MA = (0.0, 1.0, 2.0, 4.0, 8.0)
# Colour temperatures labelled on the CIE 1931 diagram (K).
PLANCK_MARKS_K = (1500.0, 2000.0, 2700.0, 4000.0, 5000.0, 6500.0, 10_000.0)
PLANCK_DECIMATE = 5                 # every 5th point of the 2-mired locus grid (10 mired)
RED_END_FROM_NM = 600               # beyond this the drawn spectrum locus stops where x turns back

# Which enumeration / bit list decodes which telemetry field (presentation only; every table
# named here is the dictionary's own, served under "enums").
FIELD_ENUMS = {
    "HK.fsw_mode": "MODES", "HK.reset_cause": "RESET_CAUSES", "HK.farm_state": "FARM_STATES",
    "HK.radio_fault": "RADIO_FAULTS", "HK.cmd_last_status": "CMD_ERRORS",
    "HK.sdls_tc_service": "SDLS_SERVICES", "HK.sdls_tm_service": "SDLS_SERVICES",
    "HK.sdls_tm_state": "SDLS_TM_STATES", "HK.cfdp_state": "CFDP_STATES", "HK.seq_state": "SEQ_STATES",
    "HK.beacon_level": "BEACON_LEVELS", "RF.radio_model": "RADIO_MODELS", "RF.framing": "FRAMINGS",
    "CMDVER.stage": "CMD_STAGES", "CMDVER.cmd_source": "CMD_SOURCES", "CMDVER.error_code": "CMD_ERRORS",
    "SPEC.spec_mode": "SPEC_MODES", "SPEC.spec_class": "SPEC_CLASSES"}
FIELD_BITS = {
    "HK.fdir_flags": "FDIR_BITS", "HK.sensor_health": "SENSOR_BITS", "RF.rf_flags": "RF_FLAG_BITS",
    "MAG.flags": "MAG_FLAG_BITS", "ATT.flags": "ATT_FLAG_BITS", "ENV.flags": "ENV_FLAG_BITS",
    "SPEC.flags": "SPEC_FLAG_BITS"}
VARIABLE_DESC = {dic.APID_EVR: "Event report: severity, event id, text (0..80 characters)",
                 dic.APID_RFSCAN: "Spacecraft RF survey: noise per channel",
                 dic.APID_SPECBURST: "AS7265X burst samples (raw or CCSDS 121.0-B-3 payload per encoding)",
                 dic.APID_CFDP: "One CFDP class-1 PDU (file delivery, VC3)"}


class StationApi(Protocol):
    """What the web server needs from the ground station (dssq.gds.GroundStation); every
    method takes the station lock itself, so the request threads call them directly."""

    @property
    def cfg(self) -> Mapping[str, Any]: ...
    @property
    def files_dir(self) -> Path: ...
    @property
    def tdm_dir(self) -> Path: ...
    def event(self, level: str, source: str, text: str, t: float | None = None) -> None: ...
    def snapshot(self) -> dict[str, Any]: ...
    def science_snapshot(self) -> dict[str, Any]: ...
    def submit_command(self, text: str, source: str = "web") -> dict[str, Any]: ...
    def confirm(self, cid: int, role: str = "CONFIRM") -> dict[str, Any]: ...
    def cancel(self, cid: int) -> None: ...
    def seq_check(self, text: str, first_slot: int = 0) -> dict[str, Any]: ...
    def seq_uplink(self, text: str, first_slot: int = 0, source: str = "web") -> dict[str, Any]: ...
    def magcal_action(self, action: str, unit: int, source: str = "web") -> dict[str, Any]: ...
    def tdm_write(self, source: str = "web") -> dict[str, Any]: ...
    def xtce_bytes(self) -> bytes: ...


class RequestError(Exception):
    """A request the API refuses: HTTP status + the message returned as {"error": ...}."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# =============================================================================
# Static responses: CSP hash, dictionary, loci
# =============================================================================
def inline_script_hash(html: str) -> str:
    """base64(SHA-256) of the text of the page's single inline <script> element, as the
    browser computes it for a CSP hash source: over the UTF-8 octets of the element's text
    after the HTML input-stream newline normalisation (CR LF and CR -> LF). Raises
    ValueError unless the page has exactly one <script> and it has no attributes (a script
    with src= would be blocked by the policy and the page would not work)."""
    scripts = SCRIPT_RE.findall(html)
    if len(scripts) != 1 or scripts[0][0].strip():
        raise ValueError("dashboard.html must contain exactly one <script> element, without attributes")
    text = scripts[0][1].replace("\r\n", "\n").replace("\r", "\n")
    return base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode("ascii")


def dashboard_csp(script_hash: str) -> str:
    """The dashboard's Content-Security-Policy (PB-5.6)."""
    return (f"default-src 'none'; script-src 'sha256-{script_hash}'; style-src 'unsafe-inline'; "
            "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; "
            "frame-ancestors 'none'")


def _uv_to_xy(u: float, v: float) -> tuple[float, float]:
    """CIE 1960 (u, v) -> CIE 1931 (x, y): x = 3u / (2u - 8v + 4), y = 2v / (2u - 8v + 4)."""
    d = 2.0 * u - 8.0 * v + 4.0
    return 3.0 * u / d, 2.0 * v / d


def colour_loci() -> dict[str, Any]:
    """The Planckian locus of the ground colorimetry (spectral.planck_locus(): blackbodies
    through the SAME 12 bands, band model and Wyman CMF weights as a measured spectrum,
    contract 13.7) and the spectrum locus of the same CMF fit, in CIE 1931 xy, so the UI
    draws exactly the model the CCT/Duv numbers come from. ~0.1 s once (cached by the caller)."""
    loc = spectral.planck_locus()
    planck: list[list[float]] = []
    last = len(loc.mired) - 1
    for i, (m, (u, v)) in enumerate(zip(loc.mired, loc.uv, strict=True)):
        if i % PLANCK_DECIMATE == 0 or i == last:
            x, y = _uv_to_xy(u, v)
            planck.append([round(1e6 / m, 1), round(x, 6), round(y, 6)])
    marks = []
    for t in PLANCK_MARKS_K:
        x, y = _uv_to_xy(*loc.uv_at(t))
        marks.append([t, round(x, 6), round(y, 6)])
    spectrum: list[list[float]] = []
    for nm in range(380, 701, 5):
        xb, yb, zb = spectral.cmf_xbar(nm), spectral.cmf_ybar(nm), spectral.cmf_zbar(nm)
        s = xb + yb + zb
        if s <= 1e-9:
            continue
        x = xb / s
        # The true locus runs monotonically to its red end (x = 0.7347 at 700 nm); the
        # multi-lobe fit's tails turn back beyond ~645 nm (x falls again), which would draw
        # a hook. The locus is therefore drawn up to its red extreme and closed from there.
        if nm >= RED_END_FROM_NM and spectrum and x < spectrum[-1][1]:
            break
        spectrum.append([nm, round(x, 6), round(yb / s, 6)])
    return {"planck_locus": planck, "planck_marks": marks, "spectrum_locus": spectrum,
            "cct_range_k": [spectral.CCT_MIN_K, spectral.CCT_MAX_K],
            "model": "CIE 1931 2 deg, Wyman 2013 CMF fit; Planckian locus through the 12 visible "
                     "AS7265X bands (same model as the CCT/Duv values); spectrum locus 380.."
                     f"{spectrum[-1][0] if spectrum else 0} nm (the fit's red extreme)"}


def dictionary_doc() -> dict[str, Any]:
    """/api/dictionary (PB-5.6): the command and telemetry dictionary plus the enumerations
    and spectrometer limits the dashboard needs, all from the dictionary/spectral modules
    (single source of truth; the page hard-codes no protocol table)."""
    forbidden = seq_forbidden_opcodes()
    commands = {}
    for mn, c in dic.COMMANDS.items():
        commands[mn] = {
            "opcode": c.opcode, "hazardous": c.hazardous, "builder_only": c.builder_only,
            "desc": c.desc, "check_doc": c.check_doc,
            "stored_ok": (c.opcode not in forbidden and not c.builder_only and 1 + c.arg_octets <= SEQ_CMD_MAX),
            "args": [{"name": n, "code": code, "lo": lo, "hi": hi, "enum": dict(enum) if enum else None}
                     for n, code, lo, hi, enum in c.args]}
    packets = {}
    for apid, name in sorted(dic.PACKET_NAMES.items()):
        spec = dic.PACKETS.get(apid)
        fields = [] if spec is None else [
            {"name": f[0], "code": f[1], "scale": f[2], "unit": f[3], "desc": f[4],
             "limits": list(f[5]) if f[5] is not None else None} for f in spec["fields"]]
        packets[str(apid)] = {"name": name, "variable": spec is None, "fields": fields,
                              "desc": str(spec["desc"]) if spec is not None else VARIABLE_DESC.get(apid, "")}
    enums = {"MODES": dic.MODES, "RESET_CAUSES": dic.RESET_CAUSES, "FARM_STATES": dic.FARM_STATES,
             "EVR_SEVERITY": dic.EVR_SEVERITY, "CMD_STAGES": dic.CMD_STAGES, "CMD_ERRORS": dic.CMD_ERRORS,
             "CMD_SOURCES": dic.CMD_SOURCES,
             "RADIO_MODELS": dic.RADIO_MODELS, "FRAMINGS": dic.FRAMINGS, "SDLS_SERVICES": dic.SDLS_SERVICES,
             "SDLS_TM_STATES": dic.SDLS_TM_STATES, "CFDP_STATES": dic.CFDP_STATES,
             "SEQ_STATES": dic.SEQ_STATES, "BEACON_LEVELS": dic.BEACON_LEVELS,
             "RADIO_FAULTS": dic.RADIO_FAULTS, "SPEC_MODES": spectral.MODE_NAMES,
             "SPEC_CLASSES": spectral.CLASS_NAMES, "FDIR_BITS": dic.FDIR_BITS, "SENSOR_BITS": dic.SENSOR_BITS,
             "MAG_FLAG_BITS": dic.MAG_FLAG_BITS, "ATT_FLAG_BITS": dic.ATT_FLAG_BITS,
             "ENV_FLAG_BITS": dic.ENV_FLAG_BITS, "RF_FLAG_BITS": dic.RF_FLAG_BITS,
             "SPEC_FLAG_BITS": SPEC_FLAG_BITS}
    led_max = dict(zip(spectral.LAMP_NAMES, spectral.LED_MAX_CODE, strict=True))
    spec_doc = {
        "wavelength_nm": spectral.WAVELENGTHS_NM, "channel_letters": spectral.CHANNEL_LETTERS,
        "device_names": spectral.DEVICE_NAMES, "modes": dic.SPEC_MODES,
        "n_range": {str(k): list(v) for k, v in dic.SPECMODE_N_RANGE.items()},
        "gains": dic.SPECCFG_GAINS, "gain_x": spectral.GAIN_X, "cycle_ms": spectral.CYCLE_MS,
        "led_ma": spectral.LED_DRV_MA, "led_max_code": led_max, "led_cur_allowed": spectral.LED_CUR_ALLOWED,
        "lamp_names": spectral.LAMP_NAMES, "ind_ma": IND_MA, **colour_loci()}
    return {"commands": commands, "directives": dict(dic.DIRECTIVES),
            "ground_directives": dict(dic.GROUND_DIRECTIVES), "packets": packets, "enums": enums,
            "field_enums": FIELD_ENUMS, "field_bits": FIELD_BITS,
            "apids": {"HK": dic.APID_HK, "RF": dic.APID_RF, "CMDVER": dic.APID_CMDVER},
            "spectral": spec_doc}


# =============================================================================
# Prometheus exposition
# =============================================================================
_METRIC_BAD = re.compile(r"[^a-zA-Z0-9_]")


def _metric_value(v: object) -> int | float | None:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, float) and math.isfinite(v):
        return v
    return None


def _label_value(s: object) -> str:
    return str(s).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


class _Prom:
    """Text exposition format 0.0.4: one HELP/TYPE header per family, samples of a family
    emitted consecutively (each family name is produced by exactly one call site)."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.seen: set[str] = set()

    def gauge(self, name: str, value: object, help_: str, labels: Mapping[str, object] | None = None) -> None:
        v = _metric_value(value)
        if v is None:
            return
        fam = "dssq_" + _METRIC_BAD.sub("_", name)
        if fam not in self.seen:
            self.seen.add(fam)
            self.lines.append(f"# HELP {fam} {help_.replace(chr(92), chr(92) * 2).replace(chr(10), ' ')}")
            self.lines.append(f"# TYPE {fam} gauge")
        lbl = "" if not labels else "{" + ",".join(f'{k}="{_label_value(x)}"' for k, x in labels.items()) + "}"
        self.lines.append(f"{fam}{lbl} {v}")

    def scalars(self, prefix: str, d: object, help_: str) -> None:
        """Every numeric scalar of a dict as <prefix>_<key> (v2 naming: a renamed snapshot key
        renames its metric, PB-5.6)."""
        if isinstance(d, Mapping):
            for k, v in d.items():
                self.gauge(f"{prefix}_{k}", v, f"{help_} {k}")


def prometheus(s: Mapping[str, Any]) -> str:
    """Prometheus gauges from a snapshot (PB-5.6). The rx.rf / rx.decoder / rx.frames /
    rx.budget gauges are named after the snapshot keys exactly as v2 did; the v3 renames
    are dssq_rf_sensitivity_dbm, dssq_rf_noise_bw_hz and dssq_frames_lost."""
    p = _Prom()
    rx = s.get("rx") or {}
    st = rx.get("state") or {}
    p.gauge("los", st.get("los"), "1 = loss of signal")
    for k in ("passes", "pass_duration_s", "since_last_frame_s", "time_in_lock_pct", "los_timeout_s",
              "hibernating"):
        p.gauge(f"state_{k}", st.get(k), f"receiver state {k}")
    rf = rx.get("rf") or {}
    p.scalars("rf", rf, "receiver")
    for sub in ("packet", "snr_db", "freq_err_hz", "doppler", "afc"):
        p.scalars(f"rf_{sub}", rf.get(sub), f"receiver {sub}")
    dec = rx.get("decoder") or {}
    p.scalars("decoder", dec, "decoder")
    for verdict, n in (dec.get("sdls_rejected") or {}).items():
        p.gauge("decoder_sdls_rejected", n, "TM frames rejected by SDLS, by verdict", {"verdict": verdict})
    fr = rx.get("frames") or {}
    p.scalars("frames", fr, "frames")
    for fmt, n in (fr.get("by_format") or {}).items():
        p.gauge("frames_by_format", n, "frames by transfer-frame format", {"format": fmt})
    per_vc = fr.get("per_vc") or {}
    for vc, d in per_vc.items():
        p.gauge("frames_vc_frames", (d or {}).get("frames"), "frames per virtual channel", {"vc": vc})
    for vc, d in per_vc.items():
        p.gauge("frames_vc_lost", (d or {}).get("lost"), "frames lost per virtual channel", {"vc": vc})
    bud = rx.get("budget") or {}
    p.scalars("budget", bud, "link budget")
    p.scalars("budget_uplink", bud.get("uplink"), "uplink budget")
    beacon = s.get("beacon") or {}
    p.gauge("beacon_level", beacon.get("level"), "spacecraft beacon level 0 NOMINAL .. 3 URGENT")
    p.gauge("alarm_level", {"NONE": 0, "YELLOW": 1, "RED": 2}.get(str(s.get("alarm"))), "0 none, 1 yellow, 2 red")
    p.gauge("uptime_s", s.get("uptime_s"), "GDS uptime")
    p.scalars("sdls", ((s.get("link") or {}).get("sdls")), "SDLS")
    cf = s.get("cfdp") or {}
    p.scalars("cfdp", cf.get("counters"), "CFDP receiver")
    p.gauge("cfdp_transactions", len(cf.get("transactions") or []), "CFDP transactions held")
    health = s.get("health") or {}
    for stage, n in (health.get("stage_errors") or {}).items():
        p.gauge("stage_errors", n, "Runner stage failures (error boundary)", {"stage": stage})
    p.gauge("tdm_rejected", health.get("tdm_rejected"), "TDM records refused (station clock)")
    apids = s.get("apids") or {}
    for key, help_ in (("count", "packets received"), ("lost", "packets lost (sequence gaps)"),
                       ("iat_s", "packet inter-arrival time EWMA"), ("age_s", "time since the last packet")):
        for name, a in apids.items():
            p.gauge(f"apid_{key}", (a or {}).get(key), f"per APID: {help_}", {"packet": name})
    for pname, pk in (s.get("tlm") or {}).items():
        for k, v in ((pk or {}).get("values") or {}).items():
            if not isinstance(v, bool):
                p.gauge("tlm", v, "latest decoded telemetry value", {"packet": pname, "param": k})
    return "\n".join(p.lines) + "\n"


# =============================================================================
# File listings and safe downloads
# =============================================================================
def safe_file(directory: Path, name: str, pattern: re.Pattern[str]) -> Path | None:
    """`directory/name` when `name` matches `pattern`, is not a temporary file, and the
    path is a regular file (not a symbolic link) whose resolved parent IS the resolved
    directory; None otherwise (path traversal, links, anything else)."""
    if len(name) > NAME_MAX or not pattern.fullmatch(name) or name.endswith(".tmp"):
        return None
    p = directory / name
    try:
        if p.is_symlink() or not p.is_file() or p.resolve().parent != directory.resolve():
            return None
    except (OSError, RuntimeError):
        return None
    return p


def list_tdm(tdm_dir: Path) -> list[dict[str, Any]]:
    """`<archive>/tdm/*.kvn` that /api/tdm/<name> would serve, newest first."""
    out: list[dict[str, Any]] = []
    try:
        with os.scandir(tdm_dir) as it:
            for e in it:
                if TDM_NAME_RE.fullmatch(e.name) and len(e.name) <= NAME_MAX and e.is_file(follow_symlinks=False):
                    st = e.stat(follow_symlinks=False)
                    out.append({"name": e.name, "size": st.st_size, "mtime": st.st_mtime})
    except OSError:
        return []
    out.sort(key=lambda f: f["mtime"], reverse=True)
    return out[:LIST_MAX]


def _sidecar_state(path: Path) -> str | None:
    """The "state" of a CFDP receiver report (<name>.json), None when unreadable."""
    try:
        if path.stat().st_size > SIDECAR_MAX:
            return None
        info = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    st = info.get("state") if isinstance(info, dict) else None
    return st if isinstance(st, str) and STATE_RE.fullmatch(st) else None


def list_files(files_dir: Path) -> list[dict[str, Any]]:
    """CFDP files in `<archive>/files`, newest first: a complete file is COMPLETE; a
    `.partial` (gaps zero-filled) and its `.json` report carry the transaction state from the
    report (INCOMPLETE, CHECKSUM_FAILURE, CANCELLED, ABANDONED). Temporary files are skipped."""
    entries: list[tuple[str, int, float]] = []
    try:
        with os.scandir(files_dir) as it:
            for e in it:
                if (FILE_NAME_RE.fullmatch(e.name) and not e.name.endswith(".tmp")
                        and e.is_file(follow_symlinks=False)):
                    st = e.stat(follow_symlinks=False)
                    entries.append((e.name, st.st_size, st.st_mtime))
    except OSError:
        return []
    entries.sort(key=lambda x: x[2], reverse=True)
    entries = entries[:LIST_MAX]
    states: dict[str, str] = {}
    for name, _, _ in entries:
        if name.endswith(".json"):
            report = _sidecar_state(files_dir / name)
            if report is not None:
                states[name[:-5]] = report
    out = []
    for name, size, mtime in entries:
        if name.endswith(".json"):
            state = states.get(name[:-5], "REPORT")
        elif name.endswith(".partial"):
            state = states.get(name[:-8], "PARTIAL")
        else:
            state = "COMPLETE"
        out.append({"name": name, "size": size, "mtime": mtime, "state": state})
    return out


# =============================================================================
# Request validation helpers
# =============================================================================
def _req_int(body: Mapping[str, Any], key: str, lo: int, hi: int, default: int | None = None) -> int:
    v = body.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise RequestError(400, f"'{key}' must be an integer {lo}..{hi}")
    return v


def _req_str(body: Mapping[str, Any], key: str, max_len: int) -> str:
    v = body.get(key)
    if not isinstance(v, str) or len(v) > max_len:
        raise RequestError(400, f"'{key}' must be a string of at most {max_len} characters")
    return v


def _reject_constant(name: str) -> None:
    raise ValueError(f"{name} is not valid JSON here")


def parse_json_body(raw: bytes) -> dict[str, Any]:
    """A JSON object from a request body (empty body = {}); RequestError(400) otherwise.
    NaN / Infinity are refused, and so is nesting deep enough to exhaust the parser."""
    if not raw.strip():
        return {}
    try:
        body = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError) as e:
        raise RequestError(400, f"bad JSON: {e}"[:200]) from None
    if not isinstance(body, dict):
        raise RequestError(400, "the body must be a JSON object")
    return body


def _host_only(netloc: str) -> str:
    """'host' of 'host', 'host:port', '[v6]' or '[v6]:port' (lower case)."""
    h = netloc.strip().lower()
    if h.startswith("["):
        return h[1:h.find("]")] if "]" in h else h
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


# =============================================================================
# Server
# =============================================================================
class WebApp:
    """Per-server state: the station, the security settings and the static responses."""

    def __init__(self, gs: StationApi, *, token: str, auth_token: str, exposed: bool, loopback_bind: bool,
                 html_path: Path = HTML_PATH, allowed_hosts: tuple[str, ...] = ()):
        self.gs = gs
        self.token = token
        self.auth_token = auth_token
        self.exposed = exposed
        self.loopback_bind = loopback_bind
        name = socket.gethostname().strip().lower()
        self.allowed_hosts = frozenset({*LOOPBACK_HOSTS, *(h.strip().lower() for h in allowed_hosts),
                                        *((name, name + ".local") if name else ())})
        html = html_path.read_text(encoding="utf-8")
        self.script_hash = inline_script_hash(html)
        self.csp = dashboard_csp(self.script_hash)
        self.html = html.encode("utf-8")
        self._dict_lock = threading.Lock()
        self._dict_json: bytes | None = None

    def host_allowed(self, host: str) -> bool:
        """Host header name (no port) a browser may use to reach this server."""
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None
        if self.loopback_bind:
            return host in LOOPBACK_HOSTS or (ip is not None and ip.is_loopback)
        # A rebinding page needs a public DNS name its owner controls. A single-label name or an
        # mDNS <name>.local resolves only on the LAN itself (inside the App Lab container the host
        # name is the container's, so the board's own <name>.local could not be listed anyway).
        return ip is not None or host in self.allowed_hosts or (
            bool(host) and (host.endswith(".local") or "." not in host.rstrip(".")))

    def dictionary_json(self) -> bytes:
        """Built once on first use (the loci take ~0.1 s), then served from memory."""
        with self._dict_lock:
            if self._dict_json is None:
                self._dict_json = dump_json(dictionary_doc())
            return self._dict_json


def dump_json(obj: object) -> bytes:
    """Compact JSON through gds.json_safe() (NaN / inf -> null; never a non-standard token)."""
    return json.dumps(json_safe(obj), separators=(",", ":"), allow_nan=False).encode("ascii")


class WebServer(ThreadingHTTPServer):
    """ThreadingHTTPServer with a bound on concurrent connections. Request threads are
    daemons and server_close() does not wait for them (Runner.close() has 4 s in all)."""

    daemon_threads = True
    block_on_close = False

    def __init__(self, addr: tuple[str, int], app: WebApp, max_clients: int = MAX_CLIENTS):
        self.app = app
        self._slots = threading.BoundedSemaphore(max_clients)
        self.max_per_ip = max(2, min(MAX_CLIENTS_PER_IP, max_clients // 4))
        self._per_ip: dict[str, int] = {}
        self._per_ip_lock = threading.Lock()
        family = socket.AF_INET6 if ":" in addr[0] else socket.AF_INET
        self.address_family = family
        super().__init__(addr, _Handler)

    def process_request(self, request: Any, client_address: Any) -> None:
        ip = str(client_address[0]) if isinstance(client_address, tuple) and client_address else ""
        with self._per_ip_lock:
            busy = self._per_ip.get(ip, 0) >= self.max_per_ip
            if not busy:
                self._per_ip[ip] = self._per_ip.get(ip, 0) + 1
        if busy or not self._slots.acquire(blocking=False):
            if not busy:
                self._ip_done(ip)
            self.shutdown_request(request)          # saturated: refuse, never queue unboundedly
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            self._ip_done(ip)
            raise

    def _ip_done(self, ip: str) -> None:
        with self._per_ip_lock:
            n = self._per_ip.get(ip, 0) - 1
            if n > 0:
                self._per_ip[ip] = n
            else:
                self._per_ip.pop(ip, None)

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()
            self._ip_done(str(client_address[0]) if isinstance(client_address, tuple) and client_address else "")


class _DeadlineReader(io.RawIOBase):
    """Socket reader with a total deadline: each receive waits at most the time left,
    then TimeoutError (http.server closes the connection; _read_body answers 408)."""

    def __init__(self, sock: socket.socket, deadline: float):
        self._sock = sock
        self._deadline = deadline

    def readable(self) -> bool:
        return True

    def readinto(self, b: Any) -> int:
        left = self._deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("request deadline exceeded")
        self._sock.settimeout(left)
        return self._sock.recv_into(b)


class _Handler(BaseHTTPRequestHandler):
    server: WebServer
    server_version = "DSS-Q"
    sys_version = ""
    protocol_version = "HTTP/1.0"
    timeout = SOCKET_TIMEOUT_S

    def setup(self) -> None:
        super().setup()
        self._started = False
        # Total request deadline (C38): every receive gets only the time left, so trickling
        # one octet per SOCKET_TIMEOUT_S cannot keep the connection (and its slot) forever.
        self._deadline = time.monotonic() + REQUEST_DEADLINE_S
        self.rfile.close()                  # the makefile() reader (it holds a socket reference)
        self.rfile = io.BufferedReader(_DeadlineReader(self.connection, self._deadline))

    def _reading_done(self) -> None:
        """The request is in: sending gets the normal per-operation timeout again."""
        with contextlib.suppress(OSError):
            self.connection.settimeout(SOCKET_TIMEOUT_S)

    def log_message(self, format: str, *args: Any) -> None:
        """Per-request access logging is off; commanding requests are audited explicitly."""

    # ------------------------------------------------------------ responses
    def _send(self, status: int, body: bytes, ctype: str, extra: Mapping[str, str] | None = None) -> None:
        self._reading_done()
        self._started = True
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in COMMON_HEADERS:
            self.send_header(k, v)
        hdrs = {"Content-Security-Policy": API_CSP, **(extra or {})}
        for k, v in hdrs.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj: object) -> None:
        self._send(status, dump_json(obj), "application/json")

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """http.server's own errors (malformed request, unsupported method) as JSON with the
        common headers, instead of its HTML page."""
        self.close_connection = True
        try:
            phrase = HTTPStatus(code).phrase
        except ValueError:
            phrase = "error"
        self._json(code, {"error": message or phrase})

    def _send_file(self, path: Path, ctype: str) -> None:
        self._reading_done()
        with open(path, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            self._started = True
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
            for k, v in COMMON_HEADERS:
                self.send_header(k, v)
            self.send_header("Content-Security-Policy", API_CSP)
            self.end_headers()
            shutil.copyfileobj(f, self.wfile, COPY_CHUNK)

    # ------------------------------------------------------------ entry points
    def do_GET(self) -> None:
        self._guard(self._get)

    def do_POST(self) -> None:
        self._guard(self._post)

    def _guard(self, fn: Callable[[], None]) -> None:
        """Documented error boundary of the request thread (PB-0.4)."""
        try:
            fn()
        except RequestError as e:
            if e.status >= 400 and self.command == "POST":
                log.warning("web POST %s from %s refused: %d %s", self.path[:80], self.client_address[0],
                            e.status, e.message)
            self._answer(e.status, {"error": e.message})
        except (ConnectionError, TimeoutError):
            self.close_connection = True                # the client went away: nothing to answer
        except Exception:                               # documented boundary: log, answer 500, continue
            log.exception("web request %s %s failed", self.command, self.path[:80])
            self._answer(500, {"error": "internal error (see the GDS log)"})

    def _answer(self, status: int, obj: object) -> None:
        """An error answer, unless a response was already started (then only close)."""
        self.close_connection = True
        if self._started:
            return
        with contextlib.suppress(OSError):              # the client went away meanwhile
            self._json(status, obj)

    # ------------------------------------------------------------ checks
    def _route(self) -> str:
        path = urlsplit(self.path).path
        if len(path) > 512:
            raise RequestError(404, "not found")
        return path

    def _check_host(self) -> None:
        """DNS-rebinding defence (GET and POST): a loopback-bound server answers only loopback
        Host names; a LAN-bound one an IP literal, this machine's name or [gds]
        http_allowed_hosts (module docstring). A request without Host (HTTP/1.0 tool, not
        a browser) is not a rebinding vector."""
        app = self.server.app
        host = self.headers.get("Host")
        if host is not None and not app.host_allowed(_host_only(host)):
            raise RequestError(403, "Host not allowed (this server is bound to the loopback interface)"
                               if app.loopback_bind else
                               "Host not allowed: add the name to [gds] http_allowed_hosts")

    def _check_post_origin(self) -> None:
        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            raise RequestError(415, "POST bodies must be Content-Type: application/json")
        origin = self.headers.get("Origin")            # browsers send it on every POST
        if origin is None:
            return                                      # a non-browser client (curl, tests)
        host = (self.headers.get("Host") or "").strip().lower()
        if origin == "null" or urlsplit(origin).netloc.lower() != host:
            raise RequestError(403, "cross-origin request refused")

    def _check_token(self) -> None:
        app = self.server.app
        if not app.token:
            if app.exposed:
                raise RequestError(403, "commanding disabled: set [gds] http_token")
            return
        got = self.headers.get("X-DSSQ-Token") or ""
        if not hmac.compare_digest(got.encode("utf-8", "replace"), app.token.encode("utf-8")):
            raise RequestError(403, "bad or missing X-DSSQ-Token")

    def _check_auth(self) -> None:
        app = self.server.app
        if not app.auth_token:
            return
        got = self.headers.get("X-DSSQ-Auth") or ""
        if not hmac.compare_digest(got.encode("utf-8", "replace"), app.auth_token.encode("utf-8")):
            raise RequestError(403, "bad or missing X-DSSQ-Auth (second-operator secret)")

    def _read_body(self) -> bytes:
        if self.headers.get("Transfer-Encoding"):
            raise RequestError(411, "send the body with Content-Length (no transfer encoding)")
        txt = self.headers.get("Content-Length") or "0"
        if not txt.isascii() or not txt.isdigit() or len(txt) > 12:
            raise RequestError(400, "bad Content-Length")
        n = int(txt)
        try:
            if n > MAX_BODY:
                left = min(n, DRAIN_MAX)
                while left > 0:                          # drain, so the client reads the 413
                    chunk = self.rfile.read(min(COPY_CHUNK, left))
                    if not chunk:
                        break
                    left -= len(chunk)
                self.close_connection = True
                raise RequestError(413, f"request body larger than {MAX_BODY} octets")
            data = self.rfile.read(n) if n else b""
        except TimeoutError:
            self.close_connection = True
            raise RequestError(408, f"request not received within {REQUEST_DEADLINE_S:.0f} s") from None
        if len(data) != n:
            raise RequestError(400, "request body shorter than its Content-Length")
        self._reading_done()
        return data

    # ------------------------------------------------------------ GET
    def _get(self) -> None:
        self._reading_done()
        self._check_host()
        app = self.server.app
        gs = app.gs
        path = self._route()
        if path in ("/", "/index.html"):
            self._send(200, app.html, "text/html; charset=utf-8", {"Content-Security-Policy": app.csp})
        elif path == "/api/state":
            self._json(200, gs.snapshot())
        elif path == "/api/science":
            self._json(200, gs.science_snapshot())
        elif path == "/api/dictionary":
            self._send(200, app.dictionary_json(), "application/json")
        elif path == "/metrics":
            self._send(200, prometheus(gs.snapshot()).encode("utf-8"), "text/plain; version=0.0.4; charset=utf-8")
        elif path == "/api/xtce":
            self._send(200, gs.xtce_bytes(), "application/xml; charset=utf-8",
                       {"Content-Disposition": 'attachment; filename="vgq1.xtce.xml"'})
        elif path == "/api/tdm":
            self._json(200, list_tdm(Path(gs.tdm_dir)))
        elif path == "/api/files":
            self._json(200, list_files(Path(gs.files_dir)))
        elif path.startswith("/api/tdm/"):
            p = safe_file(Path(gs.tdm_dir), unquote(path[len("/api/tdm/"):]), TDM_NAME_RE)
            if p is None:
                raise RequestError(404, "no such TDM file")
            self._send_file(p, "text/plain; charset=utf-8")
        elif path.startswith("/api/files/"):
            p = safe_file(Path(gs.files_dir), unquote(path[len("/api/files/"):]), FILE_NAME_RE)
            if p is None:
                raise RequestError(404, "no such file")
            self._send_file(p, "application/octet-stream")
        else:
            raise RequestError(404, "not found")

    # ------------------------------------------------------------ POST
    def _post(self) -> None:
        raw = self._read_body()                          # read first: a refusal never resets the client
        path = self._route()
        handler = _POST_ROUTES.get(path)
        if handler is None:
            raise RequestError(404, "not found")
        self._check_host()
        self._check_post_origin()
        self._check_token()
        body = parse_json_body(raw)
        result = handler(self, body)
        log.info("web POST %s from %s -> %s", path, self.client_address[0],
                 result.get("status", "-") if isinstance(result, dict) else "-")
        self._json(200, result)

    def _cmd(self, body: Mapping[str, Any]) -> dict[str, Any]:
        text = _req_str(body, "text", CMD_TEXT_MAX)
        return self.server.app.gs.submit_command(text, "web")

    def _confirm(self, body: Mapping[str, Any]) -> dict[str, Any]:
        cid = _req_int(body, "id", 0, 2**31 - 1)
        role = str(body.get("role", "CONFIRM")).upper()
        if role not in ROLES:
            raise RequestError(400, "'role' must be CONFIRM or AUTH")
        if role == "AUTH":
            self._check_auth()
        return self.server.app.gs.confirm(cid, role)

    def _cancel(self, body: Mapping[str, Any]) -> dict[str, Any]:
        cid = _req_int(body, "id", 0, 2**31 - 1)
        self.server.app.gs.cancel(cid)
        return {"status": "CANCELLED", "id": cid}

    def _seq_check(self, body: Mapping[str, Any]) -> dict[str, Any]:
        text = _req_str(body, "text", SEQ_TEXT_MAX)
        return self.server.app.gs.seq_check(text, _req_int(body, "first_slot", 0, 31, 0))

    def _seq_uplink(self, body: Mapping[str, Any]) -> dict[str, Any]:
        text = _req_str(body, "text", SEQ_TEXT_MAX)
        return self.server.app.gs.seq_uplink(text, _req_int(body, "first_slot", 0, 31, 0), "web")

    def _magcal(self, body: Mapping[str, Any]) -> dict[str, Any]:
        action = str(body.get("action", "")).lower()
        if action not in MAGCAL_ACTIONS:
            raise RequestError(400, "'action' must be one of " + ", ".join(sorted(MAGCAL_ACTIONS)))
        return self.server.app.gs.magcal_action(action, _req_int(body, "unit", 0, 1), "web")

    def _tdm_write(self, body: Mapping[str, Any]) -> dict[str, Any]:
        return self.server.app.gs.tdm_write("web")


_POST_ROUTES: dict[str, Callable[[_Handler, Mapping[str, Any]], dict[str, Any]]] = {
    "/api/cmd": _Handler._cmd, "/api/confirm": _Handler._confirm, "/api/cancel": _Handler._cancel,
    "/api/seq/check": _Handler._seq_check, "/api/seq/uplink": _Handler._seq_uplink,
    "/api/magcal": _Handler._magcal, "/api/tdm/write": _Handler._tdm_write}


def start_web(gs: StationApi, host: str, port: int, token: str | None = None, *,
              auth_token: str | None = None, exposed: bool | None = None, html_path: Path = HTML_PATH,
              max_clients: int = MAX_CLIENTS) -> WebServer:
    """Starts the dashboard / API server in a daemon thread and returns it (shutdown() and
    server_close() stop it; server_address[1] is the bound port, useful with port 0).
    token / auth_token default to [gds] http_token / [commanding] auth_token; `exposed`
    (default: host is not a loopback address) decides whether POSTs without a configured
    token are refused. Raises OSError when the address cannot be bound and ValueError
    when dashboard.html breaks the one-inline-script rule."""
    cfg = gs.cfg
    tok = token if token else str(cfg.get("gds", {}).get("http_token") or "")
    auth = auth_token if auth_token is not None else str(cfg.get("commanding", {}).get("auth_token") or "")
    if isinstance(port, bool) or not 0 <= int(port) <= 65535:
        raise ValueError(f"http port {port!r} outside 0..65535")
    loop = is_loopback(host)
    allowed = cfg.get("gds", {}).get("http_allowed_hosts") or []
    app = WebApp(gs, token=tok, auth_token=auth, exposed=(not loop) if exposed is None else exposed,
                 loopback_bind=loop, html_path=html_path, allowed_hosts=tuple(str(h) for h in allowed))
    srv = WebServer((host, int(port)), app, max_clients)
    threading.Thread(target=srv.serve_forever, daemon=True, name="web").start()
    bound = srv.server_address[1]
    shown = f"[{host}]" if ":" in host else host
    if not app.exposed:
        note = "loopback only" + ("" if not tok else ", token required for commanding")
    elif tok:
        note = "LAN-exposed, token required for commanding"
    else:
        note = "LAN-exposed WITHOUT [gds] http_token: commanding disabled (read-only dashboard)"
    gs.event("WARN" if app.exposed and not tok else "INFO", "WEB", f"dashboard on http://{shown}:{bound}/ ({note})")
    return srv
