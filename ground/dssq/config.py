"""Station configuration: loader, defaults, validation and App Lab path rules.

The configuration is TOML (config/station.toml), parsed with the standard library's
tomllib (Python >= 3.11; the station runs 3.12, the App Lab container 3.13), so no
package is needed (contract section 23 rule 2).

Merge rule (Phase B specification PB-3.2): the user file is laid over DEFAULTS table by
table and key by key, and the nested tables ([radio.sx126x], [radio.sensitivity_measured],
[spectral.classifier]) are merged the same way one level down, so a partial table keeps
every default it does not mention. DEFAULTS mirrors config/station.toml except [radio]
model: the file names the station's model explicitly, while an ABSENT key means "auto"
(contract 33: an E22 station that follows the radio model the spacecraft reports).

load_config() then calls validate(), which raises ValueError naming the offending key: a
typo or an out-of-range value stops the station at start-up instead of being silently
replaced by a default (the GDS uplinks commands - a wrong channel or SDLS setting must
never be guessed). Warnings (an unknown key, the removed [sdls] enabled) are logged.

App Lab rules (PB-6.2 step 3) live in resolve_app_paths(): inside the App container only
the App folder is mounted, so every persistent file goes under <App>/data.
"""
from __future__ import annotations

import copy
import logging
import math
import os
import re
import secrets
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import link
from .ccsds.sdls import parse_service
from .radio.e22 import regs_from_cfg
from .spectral import ClassThresholds

log = logging.getLogger("dssq.config")

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "config" / "station.toml"

SCID = 0x0A7                           # ground constant (dssq.ccsds.SPACECRAFT_ID)
E22_UART_CODE_9600 = 3                 # REG0 UART code of 9600 bit/s; CONFIG mode is always 9600 (contract 27)
SX126X_SYNC_WORD = 0x12                # flight kSxSyncWord: private-network LoRa sync word
SX126X_PREAMBLE = 12                   # flight kSxPreambleSym
TCXO_RANGE_V = (1.6, 3.3)              # SX126x DIO3 TCXO supply range (SetDIO3AsTCXOCtrl 1.6 .. 3.3 V)
HTTP_TOKEN_FILE = "http_token"
APP_KEY_FILE = "sdls_key.hex"

# Nested tables merged key by key (two-level merge).
NESTED_TABLES = {("radio", "sx126x"), ("radio", "sensitivity_measured"), ("spectral", "classifier")}
# Keys that may be absent (no silent default): [science] field_ut.
OPTIONAL_KEYS = {("science", "field_ut")}

DEFAULTS: dict[str, dict[str, Any]] = {
    "station": {"name": "DSS-Q1", "latitude_deg": 0.0, "longitude_deg": 0.0, "altitude_m": 0.0,
                "antenna": "433 MHz Yagi", "antenna_gain_dbi": 12.0, "rx_line_loss_db": 1.0},
    "spacecraft": {"name": "VGQ-1", "scid": SCID, "distance_km": 0.0, "antenna_gain_dbi": 2.15,
                   "tx_line_loss_db": 0.5},
    "radio": {"model": link.MODEL_AUTO, "port": "/dev/ttyUSB0", "address": 0, "netid": 0,
              "uart_code": E22_UART_CODE_9600, "parity": 0, "air_rate": 2, "subpacket": 0,
              "rssi_noise": True, "fault_log": False, "power": 0, "channel": 23, "rssi_byte": True,
              "fixed": False, "relay": False, "lbt": False, "wor_role": False, "wor_cycle": 0,
              "noise_bw_hz": 125000.0, "noise_poll_s": 20.0, "radio_latency_s": 0.0,
              "rssi_cal_offset_db": 0.0, "allowed_channels": [20, 21, 22, 23, 24],
              "freq_offset_cal_hz": 0.0, "freq_offset_calibrated": False,
              "sx126x": {"chip": "SX1268", "tcxo_v": 1.8, "rx_boosted_gain": True, "cad_before_tx": True,
                         "sync_word": SX126X_SYNC_WORD, "preamble": SX126X_PREAMBLE, "pa_gain_db": 11.0,
                         "afc": True},
              "sensitivity_measured": {}},
    "link": {"tm_framing": "auto", "tc_framing": "TC"},
    "sdls": {"tc_service": "AE", "tm_service": "AE", "key_file": "config/sdls_key.hex", "spi": 1,
             "accept_null": True},
    "cfdp": {"inactivity_s": 600.0, "check_s": 60.0, "files_dir": "files", "max_file_mib": 64},
    "spectral": {"white_ref_file": "spec_white_ref.json", "library_file": "spec_library.json",
                 "classifier": {}},
    "science": {"r_outboard_m": 1.0, "r_inboard_m": 0.5},
    "tdm": {"auto_write": True},
    "linkmgr": {"mode": "ADVISE", "target_margin_db": 6.0, "period_s": 60.0, "holdoff_s": 600.0,
                "min_rate": 2, "max_rate": 4, "revert_s": 180},
    "interop": {"tm_frames_udp": "", "tm_packets_udp": "", "tc_frames_udp_listen": ""},
    "gds": {"archive_dir": "archive", "http_host": "127.0.0.1", "http_port": 8080, "http_token": "",
            "los_timeout_s": 90.0, "frontpanel": True, "rcu": True, "router_address": "",
            "console_interval_s": 10.0},
    "commanding": {"t1_s": 45.0, "tx_limit": 3, "window": 5, "verify_timeout_s": 300.0,
                   "require_two_person_for_hazardous": False, "auth_token": ""},
}

_HOSTPORT_RE = re.compile(r"[A-Za-z0-9.\-]{1,253}:\d{1,5}")
_PLAIN_NAME_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,127}")
_TOKEN_RE = re.compile(r"[\x21-\x7E]{1,256}")      # printable ASCII, no blank (an HTTP header value)


# =============================================================================
# Loading and merging
# =============================================================================
def merge(user: Mapping[str, Any], where: str = "station.toml") -> dict[str, Any]:
    """DEFAULTS overlaid with `user` (two-level merge, module docstring). Raises ValueError
    when an entry the defaults define as a table is not a table."""
    cfg: dict[str, Any] = copy.deepcopy(DEFAULTS)
    for section, values in user.items():
        if section not in DEFAULTS:
            cfg[section] = copy.deepcopy(values)           # unknown table: kept, warned by validate()
            continue
        if not isinstance(values, Mapping):
            raise ValueError(f"{where}: '{section}' must be a [{section}] table, not {type(values).__name__}")
        for key, value in values.items():
            if (section, key) in NESTED_TABLES:
                if not isinstance(value, Mapping):
                    raise ValueError(f"{where}: [{section}] {key} must be a [{section}.{key}] table")
                cfg[section][key] = {**cfg[section][key], **copy.deepcopy(dict(value))}
            else:
                cfg[section][key] = copy.deepcopy(value)
    return cfg


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """DEFAULTS merged with the TOML file at `path` (default: config/station.toml; a missing
    file yields the defaults), then validated. Raises tomllib.TOMLDecodeError (a ValueError)
    on bad TOML and ValueError naming the key on an invalid value."""
    p = Path(path) if path else DEFAULT_PATH
    user: dict[str, Any] = {}
    if p.exists():
        with open(p, "rb") as f:
            user = tomllib.load(f)
    cfg = merge(user, str(p))
    for warning in validate(cfg):
        log.warning("%s: %s", p, warning)
    return cfg


# =============================================================================
# Validation
# =============================================================================
def _get(cfg: Mapping[str, Any], section: str, key: str) -> Any:
    return cfg[section][key]


def _bool(cfg: Mapping[str, Any], section: str, key: str) -> bool:
    v = _get(cfg, section, key)
    if not isinstance(v, bool):
        raise ValueError(f"[{section}] {key} = {v!r} must be true or false")
    return v


def _int(cfg: Mapping[str, Any], section: str, key: str, lo: int, hi: int) -> int:
    v = _get(cfg, section, key)
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ValueError(f"[{section}] {key} = {v!r} must be an integer {lo}..{hi}")
    return v


def _num(cfg: Mapping[str, Any], section: str, key: str, lo: float = -math.inf, hi: float = math.inf,
         lo_open: bool = False) -> float:
    v = _get(cfg, section, key)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"[{section}] {key} = {v!r} must be a finite number")
    if v < lo or v > hi or (lo_open and v == lo):
        rng = f"{'>' if lo_open else '>='} {lo:g}" if hi == math.inf else f"{lo:g}..{hi:g}"
        raise ValueError(f"[{section}] {key} = {v!r} outside {rng}")
    return float(v)


def _str(cfg: Mapping[str, Any], section: str, key: str, max_len: int = 4096) -> str:
    v = _get(cfg, section, key)
    if not isinstance(v, str) or len(v) > max_len:
        raise ValueError(f"[{section}] {key} must be a string of at most {max_len} characters")
    return v


def _choice(cfg: Mapping[str, Any], section: str, key: str, choices: tuple[str, ...]) -> str:
    v = _str(cfg, section, key)
    if v.upper() not in tuple(c.upper() for c in choices):
        raise ValueError(f"[{section}] {key} = {v!r} must be one of {', '.join(choices)}")
    return v


def _validate_radio(cfg: dict[str, Any]) -> None:
    r = cfg["radio"]
    try:
        if not isinstance(r["model"], str) or not r["model"].strip():
            raise ValueError("empty")
        model = link.get_model(r["model"])
    except ValueError:
        raise ValueError(f"[radio] model = {r['model']!r} must be one of {', '.join(link.MODELS)} or "
                         f"\"{link.MODEL_AUTO}\"") from None
    _str(cfg, "radio", "port", 512)
    for key, hi in (("address", 0xFFFF), ("netid", 0xFF), ("parity", 3), ("subpacket", 3), ("wor_cycle", 7),
                    ("uart_code", 7), ("air_rate", 7), ("power", 3), ("channel", link.CHANNEL_MAX)):
        _int(cfg, "radio", key, 0, hi)
    for key in ("rssi_noise", "fault_log", "rssi_byte", "fixed", "relay", "lbt", "wor_role",
                "freq_offset_calibrated"):
        _bool(cfg, "radio", key)
    if not model.packet_mode:
        if r["uart_code"] != E22_UART_CODE_9600:
            raise ValueError(f"[radio] uart_code = {r['uart_code']} must be {E22_UART_CODE_9600} (9600 bit/s) for "
                             f"the {model.name}: the GDS reads the module at 9600 and its CONFIG mode is always "
                             "9600 8N1 (contract 27)")
        regs_from_cfg(r, model)                     # the E22 register encoder's own range checks
    _num(cfg, "radio", "noise_bw_hz", 0.0, lo_open=True)
    _num(cfg, "radio", "noise_poll_s", 5.0)
    _num(cfg, "radio", "radio_latency_s", 0.0, 10.0)
    _num(cfg, "radio", "rssi_cal_offset_db", -30.0, 30.0)
    _num(cfg, "radio", "freq_offset_cal_hz", -50_000.0, 50_000.0)
    ch = r["allowed_channels"]
    if not isinstance(ch, list) or not ch or len(ch) > link.CHANNEL_MAX + 1 or any(
            isinstance(c, bool) or not isinstance(c, int) or not 0 <= c <= link.CHANNEL_MAX for c in ch):
        raise ValueError(f"[radio] allowed_channels = {ch!r} must be a non-empty list of channels 0..83")
    if r["channel"] not in ch:
        raise ValueError(f"[radio] channel {r['channel']} is not in allowed_channels {ch}")
    sx = r["sx126x"]
    if str(sx.get("chip", "")).upper() not in ("SX1268", "SX1262"):
        raise ValueError(f"[radio.sx126x] chip = {sx.get('chip')!r} must be \"SX1268\" or \"SX1262\"")
    tcxo = sx.get("tcxo_v")
    if isinstance(tcxo, bool) or not isinstance(tcxo, (int, float)) or not (
            tcxo == 0 or TCXO_RANGE_V[0] <= tcxo <= TCXO_RANGE_V[1]):
        raise ValueError(f"[radio.sx126x] tcxo_v = {tcxo!r} must be 0 (crystal) or 1.6..3.3 V")
    for key in ("rx_boosted_gain", "cad_before_tx", "afc"):
        if not isinstance(sx.get(key), bool):
            raise ValueError(f"[radio.sx126x] {key} = {sx.get(key)!r} must be true or false")
    if sx.get("sync_word") != SX126X_SYNC_WORD or isinstance(sx.get("sync_word"), bool):
        raise ValueError(f"[radio.sx126x] sync_word = {sx.get('sync_word')!r} must be 0x12, the flight "
                         "kSxSyncWord (the two ends would not hear each other otherwise)")
    if sx.get("preamble") != SX126X_PREAMBLE or isinstance(sx.get("preamble"), bool):
        raise ValueError(f"[radio.sx126x] preamble = {sx.get('preamble')!r} must be 12, the flight kSxPreambleSym")
    pa = sx.get("pa_gain_db")
    if isinstance(pa, bool) or not isinstance(pa, (int, float)) or not 0.0 <= pa <= 20.0:
        raise ValueError(f"[radio.sx126x] pa_gain_db = {pa!r} must be 0..20 dB")
    unknown = sorted(set(sx) - set(DEFAULTS["radio"]["sx126x"]))
    if unknown:
        raise ValueError(f"[radio.sx126x] unknown key(s) {', '.join(unknown)}")
    link.parse_measured(r["sensitivity_measured"])
    link.RadioSettings.from_config(r)                # the GDS builds exactly this at start


def _validate_hostport(cfg: dict[str, Any], key: str) -> None:
    v = _str(cfg, "interop", key, 300)
    if v and (not _HOSTPORT_RE.fullmatch(v) or not 1 <= int(v.rsplit(":", 1)[1]) <= 65535):
        raise ValueError(f"[interop] {key} = {v!r} must be \"\" or \"host:port\"")


def validate(cfg: dict[str, Any]) -> list[str]:
    """Checks every key of a merged configuration (PB-3.2 table). Raises ValueError naming
    the first invalid key; returns warnings. Removes the obsolete [sdls] enabled key."""
    warnings: list[str] = []
    for section, values in cfg.items():
        if section not in DEFAULTS:
            warnings.append(f"unknown table [{section}] ignored")
            continue
        for key in values:
            if key not in DEFAULTS[section] and (section, key) not in OPTIONAL_KEYS and not (
                    section == "sdls" and key == "enabled"):
                warnings.append(f"unknown key [{section}] {key} ignored (typo?)")
    # [station]
    _str(cfg, "station", "name", 64)
    _str(cfg, "station", "antenna", 128)
    _num(cfg, "station", "latitude_deg", -90.0, 90.0)
    _num(cfg, "station", "longitude_deg", -180.0, 180.0)
    _num(cfg, "station", "altitude_m", -500.0, 100_000.0)
    _num(cfg, "station", "antenna_gain_dbi", -20.0, 60.0)
    _num(cfg, "station", "rx_line_loss_db", 0.0, 60.0)
    # [spacecraft]
    _str(cfg, "spacecraft", "name", 64)
    if _get(cfg, "spacecraft", "scid") != SCID or isinstance(_get(cfg, "spacecraft", "scid"), bool):
        raise ValueError(f"[spacecraft] scid = {_get(cfg, 'spacecraft', 'scid')!r} must be 0x0A7 (the "
                         "mission SCID compiled into both ends)")
    _num(cfg, "spacecraft", "distance_km", 0.0)
    _num(cfg, "spacecraft", "antenna_gain_dbi", -20.0, 60.0)
    _num(cfg, "spacecraft", "tx_line_loss_db", 0.0, 60.0)
    # [radio], [radio.sx126x], [radio.sensitivity_measured]
    _validate_radio(cfg)
    # [link]
    _choice(cfg, "link", "tm_framing", ("auto", "TM", "USLP"))
    _choice(cfg, "link", "tc_framing", ("TC", "USLP"))
    # [sdls]
    sd = cfg["sdls"]
    if "enabled" in sd:
        warnings.append("[sdls] enabled is replaced by tc_service / tm_service and is ignored")
        del sd["enabled"]
    codes = {}
    for key in ("tc_service", "tm_service"):
        v = _str(cfg, "sdls", key, 16)
        try:
            codes[key] = parse_service(v)
        except ValueError:
            raise ValueError(f"[sdls] {key} = {v!r} must be NONE, AUTH or AE") from None
    if codes["tm_service"] == 1:
        raise ValueError("[sdls] tm_service = AUTH is not defined for TM (NONE or AE)")
    _str(cfg, "sdls", "key_file", 4096)
    _int(cfg, "sdls", "spi", 1, 0xFFFF)
    _bool(cfg, "sdls", "accept_null")
    # [cfdp]
    _num(cfg, "cfdp", "inactivity_s", 0.0, lo_open=True)
    _num(cfg, "cfdp", "check_s", 0.0, lo_open=True)
    _num(cfg, "cfdp", "max_file_mib", 0.0, 4096.0, lo_open=True)
    fd = _str(cfg, "cfdp", "files_dir", 255)
    if not _PLAIN_NAME_RE.fullmatch(fd) or fd in (".", ".."):
        raise ValueError(f"[cfdp] files_dir = {fd!r} must be a plain directory name (under [gds] archive_dir)")
    # [spectral]
    for key in ("white_ref_file", "library_file"):
        name = _str(cfg, "spectral", key, 128)
        if not _PLAIN_NAME_RE.fullmatch(name) or name in (".", ".."):
            raise ValueError(f"[spectral] {key} = {name!r} must be a plain file name (under [gds] archive_dir)")
    ClassThresholds.from_mapping(cfg["spectral"]["classifier"])
    # [science]
    _num(cfg, "science", "r_outboard_m", 0.0, lo_open=True)
    _num(cfg, "science", "r_inboard_m", 0.0, lo_open=True)
    if "field_ut" in cfg["science"]:
        _num(cfg, "science", "field_ut", 1.0, 800.0)
    # [tdm]
    _bool(cfg, "tdm", "auto_write")
    # [linkmgr]
    _choice(cfg, "linkmgr", "mode", ("OFF", "ADVISE", "AUTO"))
    _num(cfg, "linkmgr", "target_margin_db", -50.0, 100.0)
    _num(cfg, "linkmgr", "period_s", 0.0)
    _num(cfg, "linkmgr", "holdoff_s", 0.0)
    lo = _int(cfg, "linkmgr", "min_rate", 0, 7)
    hi = _int(cfg, "linkmgr", "max_rate", 0, 7)
    if lo > hi:
        raise ValueError(f"[linkmgr] min_rate {lo} > max_rate {hi}")
    _int(cfg, "linkmgr", "revert_s", 60, 3600)                  # AIRRATE / CHANNEL revert_s range
    # [interop]
    for key in ("tm_frames_udp", "tm_packets_udp", "tc_frames_udp_listen"):
        _validate_hostport(cfg, key)
    # [gds]
    _str(cfg, "gds", "archive_dir", 4096)
    _str(cfg, "gds", "http_host", 253)
    _int(cfg, "gds", "http_port", 1, 65535)
    tok = _str(cfg, "gds", "http_token", 256)
    if tok and not _TOKEN_RE.fullmatch(tok):
        raise ValueError("[gds] http_token must be printable ASCII without blanks")
    _num(cfg, "gds", "los_timeout_s", 0.0, lo_open=True)
    _bool(cfg, "gds", "frontpanel")
    _bool(cfg, "gds", "rcu")
    _str(cfg, "gds", "router_address", 4096)
    _num(cfg, "gds", "console_interval_s", 0.0, lo_open=True)
    # [commanding]
    _num(cfg, "commanding", "t1_s", 0.0, lo_open=True)
    _int(cfg, "commanding", "tx_limit", 1, 255)
    _int(cfg, "commanding", "window", 1, 127)
    _num(cfg, "commanding", "verify_timeout_s", 0.0, lo_open=True)
    _bool(cfg, "commanding", "require_two_person_for_hazardous")
    auth = _str(cfg, "commanding", "auth_token", 256)
    if auth and not _TOKEN_RE.fullmatch(auth):
        raise ValueError("[commanding] auth_token must be printable ASCII without blanks")
    return warnings


# =============================================================================
# Derived values used by several modules
# =============================================================================
def is_loopback(host: str) -> bool:
    """True for the local-only HTTP bind addresses."""
    return host in ("127.0.0.1", "localhost", "::1")


def archive_path(cfg: Mapping[str, Any]) -> Path:
    return Path(cfg["gds"]["archive_dir"])


def files_path(cfg: Mapping[str, Any]) -> Path:
    """CFDP received files: <archive_dir>/<[cfdp] files_dir>."""
    return archive_path(cfg) / str(cfg["cfdp"]["files_dir"])


def resolve_app_paths(cfg: dict[str, Any], data_dir: str | Path) -> dict[str, Any]:
    """App Lab rules (PB-6.2 step 3), applied in place to a loaded configuration and
    returned. Inside the App container only the App folder is mounted, so:
      * a relative [gds] archive_dir is resolved against <App>/data;
      * [sdls] key_file is <App>/data/sdls_key.hex unless the operator gave an ABSOLUTE
        path (a relative value, such as the packaged "config/sdls_key.hex", is ignored so
        the key never lands in data/config/);
      * [gds] http_host = "0.0.0.0" (a container service must listen on all interfaces to
        be reachable through the port mapping) unless $DSSQ_HTTP_HOST is set (its value);
      * an empty [gds] http_token is read from <App>/data/http_token, or created there once
        (secrets.token_hex(16), mode 0600) and printed to the App Lab console.
    Raises OSError when the data directory is not writable, ValueError for a corrupt token
    file."""
    data = Path(data_dir)
    data.mkdir(parents=True, exist_ok=True)
    g, sd = cfg["gds"], cfg["sdls"]
    if not Path(g["archive_dir"]).is_absolute():
        g["archive_dir"] = str(data / g["archive_dir"])
    if not Path(sd["key_file"]).is_absolute():
        sd["key_file"] = str(data / APP_KEY_FILE)
    g["http_host"] = os.environ.get("DSSQ_HTTP_HOST") or "0.0.0.0"
    if not g["http_token"]:
        tok_path = data / HTTP_TOKEN_FILE
        token = ""
        if tok_path.exists():
            token = tok_path.read_text(encoding="ascii", errors="replace").strip()
            if token and not _TOKEN_RE.fullmatch(token):
                raise ValueError(f"{tok_path}: not a token (printable ASCII without blanks); delete it to "
                                 "create a new one")
        if not token:
            token = secrets.token_hex(16)
            fd = os.open(tok_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, (token + "\n").encode("ascii"))
                os.fsync(fd)
            finally:
                os.close(fd)
            os.chmod(tok_path, 0o600)
            print(f"DSS-Q web commanding token created in {tok_path}: {token} "
                  "(enter it once in the dashboard)", flush=True)
        g["http_token"] = token
    return cfg
