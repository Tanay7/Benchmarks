"""Station configuration loader (TOML)."""
from __future__ import annotations

from pathlib import Path

try:
    import tomllib                      # Python >= 3.11
except ModuleNotFoundError:             # pragma: no cover
    try:
        import tomli as tomllib         # pip install tomli on older Pythons
    except ModuleNotFoundError:
        tomllib = None

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "config" / "station.toml"

DEFAULTS = {
    "station": {"name": "DSS-Q1", "latitude_deg": 0.0, "longitude_deg": 0.0, "altitude_m": 0.0,
                "antenna": "Yagi", "antenna_gain_dbi": 12.0, "rx_line_loss_db": 1.0},
    "spacecraft": {"name": "VGQ-1", "scid": 0x0A7, "distance_km": 0.0, "antenna_gain_dbi": 2.15,
                   "tx_line_loss_db": 0.5},
    "radio": {"port": "/dev/ttyUSB0", "address": 0, "netid": 0, "uart_code": 3, "parity": 0,
              "air_rate": 2, "subpacket": 0, "rssi_noise": True, "fault_log": True, "power": 0, "channel": 23,
              "rssi_byte": True, "fixed": False, "relay": False, "lbt": False,
              "wor_role": False, "wor_cycle": 0, "noise_bw_hz": 125000.0,
              "noise_poll_s": 20.0, "radio_latency_s": 0.0,
              "allowed_channels": [20, 21, 22, 23, 24]},
    "sdls": {"enabled": False, "spi": 1, "key_file": "config/sdls_key.hex"},
    "linkmgr": {"mode": "ADVISE", "target_margin_db": 6.0, "period_s": 60.0, "holdoff_s": 600.0,
                "min_rate": 2, "max_rate": 4, "revert_s": 180},
    "interop": {"tm_frames_udp": "", "tm_packets_udp": "", "tc_frames_udp_listen": ""},
    "science": {"r_outboard_m": 1.0, "r_inboard_m": 0.5},
    "gds": {"archive_dir": "archive", "http_host": "127.0.0.1", "http_port": 8080,
            "los_timeout_s": 90.0, "frontpanel": False, "rcu": False,
            "router_address": "", "http_token": "", "console_interval_s": 10.0},
    "commanding": {"t1_s": 45.0, "tx_limit": 3, "window": 5, "verify_timeout_s": 300.0,
                   "require_two_person_for_hazardous": False},
}


def load_config(path: str | None = None) -> dict:
    cfg = {k: dict(v) for k, v in DEFAULTS.items()}
    p = Path(path) if path else DEFAULT_PATH
    if p.exists():
        if tomllib is None:
            raise RuntimeError("TOML support needs Python 3.11+ or 'pip install tomli'")
        with open(p, "rb") as f:
            user = tomllib.load(f)
        for section, values in user.items():
            cfg.setdefault(section, {}).update(values)
    return cfg
