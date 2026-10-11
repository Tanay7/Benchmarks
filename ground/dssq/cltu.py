"""Build a CLTU for "umbilical" (hardline) commanding of the spacecraft on the bench.

    cd ground
    python -m dssq.cltu NOOP
    python -m dssq.cltu "MODE CRUISE"
    python -m dssq.cltu "PING 1234" --config config/station.toml
    python -m dssq.cltu NOOP --app-data /path/to/ground-app/data     # key/SN of a ground App

Prints one hex line. Append it to the spacecraft EGSE App's queue file
<App>/data/hardline_queue.txt on the UNO Q; the EGSE passes it to the flight computer
with the Router Bridge call `hl_cltu` (at most 110 octets, Phase B PB-4.1), which runs it
through exactly the same CLTU -> TC frame -> SDLS -> FARM-1 -> dispatcher path as an RF
uplink.

The frame is a Type-BD (bypass) frame on VC 0, so it does not disturb the COP-1 sequence
numbers the GDS is using over RF. Framing follows [link] tc_framing ("TC" = TC Transfer
Frame v1, "USLP" = USLP TC frame), security follows [sdls] tc_service with the key from
[sdls] key_file, exactly like the GDS (contract 5):
  * host: a relative key_file is relative to the working directory (ground/);
  * --app-data DIR (a ground App Lab App's data directory): key_file is DIR/sdls_key.hex
    unless station.toml gives an ABSOLUTE path, and a relative [gds] archive_dir is under
    DIR (PB-6.2 step 3);
  * no key file: the frame goes unprotected (NONE), as both ends do without a key.
With SDLS the frame takes the next sequence number from the SN file the GDS uses
(<archive_dir>/sdls_sn.txt, written atomically before the number is used, PB-7 F-64);
the GDS re-reads that file before each frame, so the two never use one number twice. A
corrupt SN file refuses the command (the GDS restores the number from a protected HK).

Frames above the mission TC frame limit (64 octets; with USLP + AE a 26-octet TC packet
such as SEQLOAD or MAGCAL does not fit, C-08) are refused with the same message as the GDS.
"""
from __future__ import annotations

import argparse
import dataclasses
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .ccsds.sdls import SERVICE_NAMES, SERVICE_NONE, SdlsSender, load_key, parse_service
from .ccsds.tc import build_cltu, build_tc_frame, build_uslp_tc_frame
from .cmdparse import CommandError, parse, tc_packet
from .config import APP_KEY_FILE, load_config
from .fop import frame_size_error

MAX_HARDLINE_OCTETS = 110      # hl_cltu accepts 2..220 hex characters (PB-4.1)


class HardlineError(Exception):
    """A command that cannot be turned into a hardline CLTU (message for the operator)."""


def apply_app_data(cfg: dict[str, Any], data_dir: str | Path) -> None:
    """The ground App's path rules for the key and the archive (PB-6.2 step 3), in place."""
    data = Path(data_dir)
    sd, g = cfg["sdls"], cfg["gds"]
    if not Path(sd["key_file"]).is_absolute():
        sd["key_file"] = str(data / APP_KEY_FILE)
    if not Path(g["archive_dir"]).is_absolute():
        g["archive_dir"] = str(data / g["archive_dir"])


def build_hardline_cltu(cfg: dict[str, Any], text: str) -> tuple[bytes, str]:
    """(CLTU, description) for one command; raises HardlineError with the reason."""
    try:
        cmd = parse(text)
    except CommandError as e:
        raise HardlineError(f"rejected: {e}") from e
    if cmd.kind == "BC":
        raise HardlineError("COP-1 directives (UNLOCK / SETVR) are RF-only; send them from the GDS console")
    sd = cfg["sdls"]
    framing = str(cfg["link"]["tc_framing"]).upper()
    service = parse_service(sd["tc_service"])
    key: bytes | None = None
    if service != SERVICE_NONE:
        try:
            key = load_key(sd["key_file"])
        except (OSError, ValueError) as e:
            raise HardlineError(f"SDLS key file {sd['key_file']} unreadable: {e}") from e
    sn_file = Path(cfg["gds"]["archive_dir"]) / "sdls_sn.txt"
    sdls = SdlsSender(key, int(sd["spi"]), sn_file if key else None, service=service)
    err = frame_size_error(dataclasses.replace(cmd, kind="BD"), framing, sdls.overhead, sdls.service_name)
    if err:
        raise HardlineError(err)
    if sdls.uplink_blocked:
        raise HardlineError(f"TC uplink BLOCKED: {sdls.blocked}")
    if sdls.enabled:
        sn_file.parent.mkdir(parents=True, exist_ok=True)
    builder = build_uslp_tc_frame if framing == "USLP" else build_tc_frame
    try:
        frame = builder(tc_packet(cmd, 0), seq=0, bypass=True, sdls=sdls if sdls.enabled else None)
    except (RuntimeError, OSError) as e:                 # SN space exhausted / SN not persisted
        raise HardlineError(f"SDLS: {e}") from e
    cltu = build_cltu(frame)
    if len(cltu) > MAX_HARDLINE_OCTETS:
        raise HardlineError(f"CLTU is {len(cltu)} octets; the hardline path accepts at most {MAX_HARDLINE_OCTETS}")
    sec = (f"SDLS {SERVICE_NAMES[service]} SN {sdls.sn}" if sdls.enabled
           else "unprotected (no key file)" if service != SERVICE_NONE else "unprotected (tc_service NONE)")
    return cltu, f"BD {cmd.text} {framing} ({len(frame)}-octet frame, {len(cltu)}-octet CLTU, {sec})"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", help='command text, e.g. "MODE SAFE" (same syntax as the console)')
    ap.add_argument("--config", default=None, help="station.toml (default: ground/config/station.toml)")
    ap.add_argument("--app-data", default=None, help="a ground App Lab App's data directory (key and SN file)")
    a = ap.parse_args(argv)
    try:
        cfg = load_config(a.config)
    except (OSError, ValueError) as e:
        print(f"configuration: {e}", file=sys.stderr)
        return 2
    if a.app_data:
        apply_app_data(cfg, a.app_data)
    try:
        cltu, info = build_hardline_cltu(cfg, a.command)
    except HardlineError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(cltu.hex().upper())
    print(f"# {info}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
