"""Build a CLTU for "umbilical" (hardline) commanding of the spacecraft on the bench.

    cd ground
    python -m dssq.cltu NOOP
    python -m dssq.cltu "MODE CRUISE"
    python -m dssq.cltu "PING 1234" --config config/station.toml

Prints one hex line. Append it to the spacecraft EGSE queue file on the UNO Q
(spacecraft/vgq1_flight/python/egse_data/hardline_queue.txt); the EGSE passes it
to the flight computer over the Router Bridge, which runs it through exactly the
same CLTU -> TC frame -> SDLS -> FARM-1 -> dispatcher path as an RF uplink.

The frame is a Type-BD (bypass) frame, so it does not disturb the COP-1
sequence numbers the GDS is using over RF. If [sdls] enabled = true, the frame is
authenticated with the station key and takes the next SDLS sequence number from
the same file the GDS uses (<archive_dir>/sdls_sn.txt), so the two never reuse an
SN. Run it on the machine that holds the key, with the GDS stopped or idle.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .ccsds.sdls import SdlsSender, load_key
from .ccsds.tc import build_cltu, build_tc_frame
from .cmdparse import CommandError, parse, tc_packet
from .config import load_config

MAX_HARDLINE_OCTETS = 100      # 200 hex chars: the MCU's 256-byte inbound RPC buffer


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", help='command text, e.g. "MODE SAFE" (same syntax as the console)')
    ap.add_argument("--config", default=None, help="station.toml (default: ground/config/station.toml)")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    try:
        cmd = parse(a.command)
    except CommandError as e:
        sys.exit(f"rejected: {e}")
    if cmd.kind == "BC":
        sys.exit("COP-1 directives (UNLOCK / SETVR) are RF-only; send them from the GDS console")
    sd = cfg.get("sdls", {})
    key = load_key(sd.get("key_file")) if sd.get("enabled") else None
    if sd.get("enabled") and not key:
        sys.exit(f"[sdls] enabled but key file {sd.get('key_file')} not found")
    sdls = SdlsSender(key, int(sd.get("spi", 1)), Path(cfg["gds"]["archive_dir"]) / "sdls_sn.txt")
    if key:
        Path(cfg["gds"]["archive_dir"]).mkdir(parents=True, exist_ok=True)
    frame = build_tc_frame(tc_packet(cmd, 0), seq=0, bypass=True, sdls=sdls if key else None)
    cltu = build_cltu(frame)
    if len(cltu) > MAX_HARDLINE_OCTETS:
        sys.exit(f"CLTU is {len(cltu)} octets; the hardline path accepts at most {MAX_HARDLINE_OCTETS}")
    print(cltu.hex().upper())
    print(f"# BD {cmd.text}  ({len(cltu)} octets{', SDLS SN %d' % sdls.sn if key else ', unauthenticated'})",
          file=sys.stderr)


if __name__ == "__main__":
    main()
