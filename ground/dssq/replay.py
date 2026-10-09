"""Offline replay / decode of archived CADUs.

    python -m dssq.replay --cadu-file archive/20261009/cadu.bin          # ground archive
    python -m dssq.replay --cadu-file egse_data/cadu_20261009.bin       # spacecraft EGSE log
    python -m dssq.replay --cadu-file ... --packets --values            # verbose

Runs every codeblock through the same de-randomizer, RS decoder, frame parser,
packet extractor and decommutator as the live GDS and prints a summary.
"""
from __future__ import annotations

import argparse
from collections import Counter

from .archive import read_cadu_file
from .ccsds.packets import PacketExtractor
from .ccsds.tm import decode_codeblock
from .decom import Decommutator


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cadu-file", required=True)
    ap.add_argument("--packets", action="store_true", help="print every packet")
    ap.add_argument("--values", action="store_true", help="print decommutated values")
    a = ap.parse_args(argv)
    dec = Decommutator()
    ex = {vc: PacketExtractor(vc) for vc in (0, 1, 2)}
    stats = Counter()
    apids = Counter()
    for ert, rssi, cadu in read_cadu_file(a.cadu_file):
        stats["cadus"] += 1
        r = decode_codeblock(cadu[4:])
        if not r.ok:
            stats["bad:" + r.reason] += 1
            continue
        stats["rs_symbols_corrected"] += r.rs_corrected
        f = r.frame
        stats[f"vc{f.vcid}"] += 1
        if f.vcid not in ex:
            continue
        for p in ex[f.vcid].push(f.vcfc, f.fhp, f.data):
            name = dec.packet_name(p.apid)
            apids[name] += 1
            if a.packets:
                t = f"{p.time.seconds:.3f}" if p.time else "-"
                print(f"ERT {ert:.3f} RSSI {rssi} VC{f.vcid} {name:9s} seq {p.seq_count:5d} SCLK {t}")
            if a.values:
                try:
                    print("   ", dec.decode(p))
                except (KeyError, ValueError) as e:
                    print("    decode error:", e)
    print("\nframe summary:", dict(stats))
    print("packets by name:", dict(apids))
    for vc, e in ex.items():
        print(f"VC{vc} extractor:", e.stats)


if __name__ == "__main__":
    main()
