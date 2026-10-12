"""Build a CLTU for "umbilical" (hardline) commanding of the spacecraft on the bench.

    cd dssq-groundstation/python
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
  * stand-alone: a relative key_file is relative to the working directory;
  * --app-data DIR (a ground App Lab App's data directory): key_file is DIR/sdls_key.hex
    unless station.toml gives an ABSOLUTE path, and a relative [gds] archive_dir is under
    DIR (PB-6.2 step 3);
  * no key file: the frame goes unprotected (NONE), as both ends do without a key.
With SDLS the frame takes the next sequence number from the SN file the GDS uses
(<archive_dir>/sdls_sn.txt, written atomically before the number is used, PB-7 F-64);
the GDS re-reads that file before each frame, so the two never use one number twice. A
missing or corrupt SN file refuses the command with a non-zero exit, as the GDS holds its
uplink then: the numbers already used with the key are unknown. The GDS re-synchronises the
counter onto the time floor (4 numbers per second since 2026-01-01, SDLS_SN_TIME_RATE) from
an HK sent at least 120 s after the spacecraft booted, and writes the file. A number above
the time floor is refused too (the rate bound of the floor rule); see
docs/17_Security_Model.md section 7.6.

Frames above the mission TC frame limit (64 octets; with USLP + AE a 26-octet TC packet
such as SEQLOAD or MAGCAL does not fit, C-08) are refused with the same message as the GDS.
"""
from __future__ import annotations

import argparse
import dataclasses
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .ccsds.sdls import (SDLS_SN_TIME_RATE, SERVICE_NAMES, SERVICE_NONE, SN_DOC, SdlsSender, load_key,
                         parse_service, sn_time_floor)
from .ccsds.tc import build_cltu, build_tc_frame, build_uslp_tc_frame
from .cmdparse import CommandError, parse, tc_packet
from .config import apply_app_data as apply_app_data  # re-exported: the hardline tool's path rules
from .config import load_tool_config
from .fop import frame_size_error

MAX_HARDLINE_OCTETS = 110      # hl_cltu accepts 2..220 hex characters (PB-4.1)


class HardlineError(Exception):
    """A command that cannot be turned into a hardline CLTU (message for the operator)."""


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
    # The GDS's rule (require_file): with a key, a missing or corrupt SN file means the numbers
    # already used with it are unknown - refused, never started at 1 (docs/17 7.6).
    sdls = SdlsSender(key, int(sd["spi"]), sn_file if key else None, service=service, require_file=True)
    err = frame_size_error(dataclasses.replace(cmd, kind="BD"), framing, sdls.overhead, sdls.service_name)
    if err:
        raise HardlineError(err)
    if sdls.uplink_blocked:
        raise HardlineError(f"TC uplink BLOCKED: {sdls.blocked}. Start the GDS on this data folder and let an HK "
                            "re-synchronise the counter (it writes the SN file), or follow that section; "
                            "never start a used key at 1")
    # The time floor's rate bound (ccsds/sdls.py): no number above SDLS_SN_TIME_RATE per second since
    # 2026-01-01 - a burst of scripted hardline commands may not outrun it (a clock before then: no bound).
    floor = sn_time_floor(time.time())
    if sdls.enabled and floor is not None and sdls.sn + 1 > floor:
        raise HardlineError(f"SDLS: sequence number {sdls.sn + 1} would be above the time bound {floor} "
                            f"({SDLS_SN_TIME_RATE} per second since 2026-01-01; {SN_DOC}): the station clock "
                            "is behind, or commands were sent faster than that - check the clock, or wait")
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
    ap.add_argument("--config", default=None, help="station.toml (default: <APP_DATA>/station.toml with "
                    "--app-data when it exists, else config/station.toml next to dssq/)")
    ap.add_argument("--app-data", metavar="APP_DATA", default=None,
                    help="the ground App's data folder: its station.toml, key and SN file (as the GDS uses them)")
    a = ap.parse_args(argv)
    try:
        cfg = load_tool_config(a.config, a.app_data)   # the App's rules (config.apply_app_data)
    except (OSError, ValueError) as e:
        print(f"configuration: {e}", file=sys.stderr)
        return 2
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
