"""Offline replay / decode of recorded CADUs, TM or USLP, with or without SDLS.

    python -m dssq.replay --cadu-file archive/20261009/cadu.bin              # ground archive
    python -m dssq.replay --cadu-file data/cadu_20261009.bin                 # spacecraft EGSE log
    python -m dssq.replay --cadu-file out/integration_tm_ae.bin --raw        # plain CADU stream
    python -m dssq.replay --cadu-file ... --config config/station.toml --packets --values
    python -m dssq.replay --cadu-file ... --cfdp-dir /tmp/files              # rebuild CFDP files

Every codeblock goes through the same chain as the live GDS (Phase B PB-7 F-45): ASM check
(the frame synchronizer's lock tolerance, <= LOCK_TOL bit errors: the archive keeps the ASM
exactly as received, uncorrected, and the ASM is not protected by RS),
de-randomizer, Reed-Solomon, TM/USLP frame parser ([link] tm_framing), SDLS TM
ProcessSecurity (AE decrypt + tag + (epoch, sn) anti-replay, null SA flagged UNPROTECTED;
[sdls] tm_service / key_file / accept_null), packet extraction per data VC 0..3, OID
identification text, the decommutator, and optionally the CFDP class-1 receiver for VC3.
The configuration is the station's (default: the packaged config/station.toml, a relative
key_file is relative to the working directory, as for the host GDS). Without the key file
the data frames of an AE downlink cannot be opened: null-SA and OID frames still decode,
the others are counted as "NO KEY".

File formats: ground archive [i64 ns][i16 RSSI][u16 len][CADU], EGSE log (name cadu_*)
[u64 ns][u16 len][CADU] (dssq.archive.read_cadu_file), or --raw = concatenated 236-octet
CADUs (tools/host_test vectors). The replay window starts empty, so a recording is judged
on its own (a frame repeated inside it is a REPLAY).
"""
from __future__ import annotations

import argparse
import struct
import sys
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from . import config as station_config
from .archive import read_cadu_file
from .ccsds import ASM, CADU_LEN, NUM_DATA_VCS, VC_NAMES
from .ccsds import sdls as sdls_mod
from .ccsds.cfdp import Receiver
from .ccsds.packets import PacketExtractor, SpacePacket
from .ccsds.tm import FrameLossTracker, TransferFrame, decode_codeblock
from .decom import Decommutator
from .dictionary import APID_CFDP
from .framesync import LOCK_TOL

FRAMINGS = ("AUTO", "TM", "USLP")
MAX_RAW_FILE = 1 << 30            # a CADU recording larger than 1 GiB is refused (memory bound)


class Pipeline:
    """The GDS downlink chain for recorded CADUs. push() returns the packets of one CADU as
    (vcid, packet); the counters describe everything seen so far."""

    def __init__(self, *, key: bytes | None = None, tm_service: str | int = "NONE",
                 accept_null: bool = True, framing: str = "auto",
                 cfdp_dir: str | Path | None = None) -> None:
        if framing.upper() not in FRAMINGS:
            raise ValueError(f"framing {framing!r}: expected auto, TM or USLP")
        self.framing = framing
        self.ae = sdls_mod.parse_service(tm_service) == sdls_mod.SERVICE_AE
        self.key_loaded = bool(key)
        self.sec = sdls_mod.SdlsTmReceiver(key if self.ae else None, "AE" if self.ae else "NONE",
                                           accept_null)
        self.accept_null = accept_null
        # AE geometry on data-VC frames whenever the service is AE, also without the key:
        # the null-SA frames of a spacecraft still waiting for its epoch remain readable.
        self.geometry = ((sdls_mod.AE_HDR_LEN, sdls_mod.AE_TAG_LEN) if self.ae else (0, 0))
        self.extract = {vc: PacketExtractor(vc) for vc in range(NUM_DATA_VCS)}
        self.decom = Decommutator()
        self.cfdp = Receiver(cfdp_dir) if cfdp_dir is not None else None
        self.loss = FrameLossTracker()
        self.frames: Counter[str] = Counter()
        self.verdicts: Counter[str] = Counter()
        self.packets: Counter[str] = Counter()
        self.decode_errors: Counter[str] = Counter()
        self.idents: Counter[str] = Counter()
        self.cfdp_events: list[dict[str, Any]] = []
        self.rs_symbols = 0
        self.asm_bit_errors = 0
        self.lost_frames = 0
        self._protected = False

    def _open(self, f: TransferFrame) -> TransferFrame | None:
        """SDLS ProcessSecurity of one data-VC frame; None when it must not be decoded.
        Sets self._protected (the frame passed AE verification) for the packet extractor."""
        self._protected = False
        if not f.secured:
            self.verdicts["NONE"] += 1
            return f
        if self.key_loaded:
            plain, res = self.sec.open_frame(f)
            self.verdicts[res.verdict] += 1
            self._protected = plain is not None and res.verdict == "OK"
            return plain
        # No key: only a null-SA frame (SPI 0, IV and MAC all zero) can be read.
        spi = int.from_bytes(f.sec_header[:2], "big")
        if (spi == sdls_mod.SPI_NULL and self.accept_null and not any(f.sec_header[2:])
                and not any(f.sec_trailer)):
            self.verdicts["UNPROTECTED"] += 1
            return f.with_plaintext(f.protected)
        self.verdicts["NO KEY" if spi == sdls_mod.SPI_TM_AE else "BAD_SPI"] += 1
        return None

    def push(self, cadu: bytes, ert: float | None = None) -> list[tuple[int, SpacePacket]]:
        if len(cadu) != CADU_LEN:
            self.frames["bad: CADU length"] += 1
            return []
        asm_errors = bin(int.from_bytes(cadu[:len(ASM)], "big") ^ int.from_bytes(ASM, "big")).count("1")
        if asm_errors > LOCK_TOL:
            self.frames["bad: ASM"] += 1
            return []
        self.asm_bit_errors += asm_errors
        r = decode_codeblock(cadu[len(ASM):], *self.geometry, framing=self.framing)
        if not r.ok or r.frame is None:
            self.frames["bad: " + r.reason] += 1
            return []
        self.rs_symbols += r.rs_corrected
        f = r.frame
        self.lost_frames += self.loss.update(f)
        if f.is_oid:
            self.frames[f"{f.format} OID"] += 1
            text = f.ident
            if text:
                self.idents[text] += 1
            return []
        self.frames[f"{f.format} VC{f.vcid}"] += 1
        opened = self._open(f)
        if opened is None:
            return []
        ex = self.extract.get(opened.vcid)
        if ex is None or opened.fhp is None:
            self.frames["no packet channel"] += 1
            return []
        out: list[tuple[int, SpacePacket]] = []
        # Same SDLS continuity rule as the live GDS (I2): SpacePacket.protected per packet.
        for p in ex.push(opened.vcfc, opened.fhp, opened.data, protected=self._protected):
            self.packets[self.decom.packet_name(p.apid)] += 1
            if p.apid == APID_CFDP and self.cfdp is not None:
                self.cfdp_events += self.cfdp.receive(p.user_data, ert)
            out.append((opened.vcid, p))
        return out

    def decode(self, p: SpacePacket) -> dict[str, Any] | None:
        """Decommutated values, or None (counted per packet name) for a malformed packet."""
        try:
            return self.decom.decode(p)
        except (KeyError, ValueError) as e:            # rice.RiceError is a ValueError
            self.decode_errors[f"{self.decom.packet_name(p.apid)}: {e}"] += 1
            return None


def raw_cadus(path: str | Path) -> Iterator[tuple[float | None, int | None, bytes]]:
    """A plain CADU stream (no record headers): (None, None, cadu) per 236 octets."""
    data = Path(path).read_bytes()
    if len(data) % CADU_LEN:
        raise ValueError(f"{path}: {len(data)} octets is not a whole number of {CADU_LEN}-octet CADUs")
    for i in range(0, len(data), CADU_LEN):
        yield None, None, data[i:i + CADU_LEN]


def pipeline_from_config(cfg: dict[str, Any], cfdp_dir: str | Path | None = None) -> tuple[Pipeline, str]:
    """A Pipeline for a station configuration (config.load_config) and a note on its SDLS."""
    sd = cfg.get("sdls", {})
    service = sd.get("tm_service", "AE")
    key_file = sd.get("key_file") or ""
    key = sdls_mod.load_key(key_file) if key_file else None
    framing = str(cfg.get("link", {}).get("tm_framing", "auto"))
    pipe = Pipeline(key=key, tm_service=service, accept_null=bool(sd.get("accept_null", True)),
                    framing=framing, cfdp_dir=cfdp_dir)
    if not pipe.ae:
        note = "SDLS TM service NONE"
    elif key is None:
        note = f"SDLS TM AE, key file '{key_file}' not found: only null-SA and OID frames decode"
    else:
        note = f"SDLS TM AE, key from '{key_file}'"
    return pipe, f"{note}; framing {framing}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m dssq.replay", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cadu-file", required=True)
    ap.add_argument("--raw", action="store_true", help="file is a plain stream of 236-octet CADUs")
    ap.add_argument("--config", default=None, help="station.toml (default: the packaged one)")
    ap.add_argument("--packets", action="store_true", help="print every packet")
    ap.add_argument("--values", action="store_true", help="print decommutated values")
    ap.add_argument("--cfdp-dir", default=None, help="reassemble CFDP (VC3) files into this directory")
    a = ap.parse_args(argv)
    try:
        cfg = station_config.load_config(a.config)
        if a.cfdp_dir:
            Path(a.cfdp_dir).mkdir(parents=True, exist_ok=True)
        pipe, note = pipeline_from_config(cfg, a.cfdp_dir)
        path = Path(a.cadu_file)
        if path.stat().st_size > MAX_RAW_FILE:
            raise ValueError(f"{path}: larger than {MAX_RAW_FILE} octets")
        records = raw_cadus(path) if a.raw else read_cadu_file(path)
        print(f"replay {path}: {note}")
        n = 0
        for ert, rssi, cadu in records:
            n += 1
            for vc, p in pipe.push(cadu, ert):
                name = pipe.decom.packet_name(p.apid)
                if a.packets:
                    t = f"{p.time.seconds:.3f}" if p.time else "-"
                    e = "-" if ert is None else f"{ert:.3f}"
                    print(f"ERT {e} RSSI {rssi} VC{vc} {name:9s} seq {p.seq_count:5d} SCLK {t}")
                values = pipe.decode(p)
                if a.values:
                    print("   ", values if values is not None else "decode error")
    except (OSError, ValueError, struct.error) as e:      # TOMLDecodeError is a ValueError
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"\n{n} CADUs, {pipe.asm_bit_errors} ASM bit errors, {pipe.rs_symbols} RS symbols corrected, "
          f"{pipe.lost_frames} frames lost")
    print("frames:", dict(pipe.frames))
    print("SDLS verdicts:", dict(pipe.verdicts))
    print("packets by name:", dict(pipe.packets))
    if pipe.decode_errors:
        print("decode errors:", dict(pipe.decode_errors))
    if pipe.idents:
        print("OID identification:", dict(pipe.idents))
    for vc, ex in pipe.extract.items():
        print(f"VC{vc} {VC_NAMES[vc]} extractor:", ex.stats)
    if pipe.cfdp is not None:
        for ev in pipe.cfdp_events:
            print(f"CFDP {ev['level']}: {ev['text']}")
        for tx in pipe.cfdp.snapshot()["transactions"]:
            print(f"CFDP {tx['name']}: {tx['state']} {tx['received']}/{tx['size']} octets, "
                  f"checksum ok {tx['checksum_ok']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
