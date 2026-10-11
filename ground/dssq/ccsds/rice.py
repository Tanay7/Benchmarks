"""CCSDS 121.0-B-3 Lossless Data Compression ("Rice"): ground decoder and reference encoder.

Mirrors the flight encoder spacecraft/vgq1_flight/sketch/src/ccsds/rice.h and is
cross-checked against it bit for bit by ground/tests/test_rice.py (the C++ host test
writes its encodings; this decoder must restore the samples and this encoder must
reproduce the stream). Both implementations reproduce the official CCSDS test data
(121B2TestData) octet for octet.

Source coder (121.0-B-3 §2.2), unsigned n-bit samples, preprocessor present:
  Unit-delay predictor (§4.2.5): x^_i = x_(i-1); the first sample of every reference
  sample interval of r blocks (§4.2.6, §4.3) is predicted by itself and travels
  uncoded as the n-bit reference sample; fewer than r blocks may follow the last
  reference sample at the end of the input.
  Prediction error mapper (§4.4), x_min = 0, x_max = 2^n - 1,
  theta = min(x^, x_max - x^), Delta = x - x^:
      delta = 2 Delta (0 <= Delta <= theta) | 2 |Delta| - 1 (-theta <= Delta < 0)
            | theta + |Delta| (otherwise)
Adaptive Entropy Coder (§3), one Coded Data Set (CDS, §5.2) per block of J samples:
  Zero-Block (§3.5, Table 3-2): one CDS per run of all-zero blocks inside a segment of
      64 blocks (shorter at the end of a reference interval or of the input); FS
      codeword: 1..4 blocks -> m-1 zeros + 1, ROS ("the rest of the segment, 5 or
      more blocks") -> 0000 1, 5..63 blocks -> m zeros + 1.
  Second-Extension (§3.4): gamma = (a + b)(a + b + 1)/2 + b per pair, FS coded;
      delta_1 = 0 in a reference block.
  Split-sample k = 0 (Fundamental Sequence) .. k_max (§3.2, §3.3): FS codes of
      delta >> k, then the k LSBs of every sample (Fig. 5-1).
  No-compression (§3.6).
  Selection (§3.7): fewest bits; ties -> no-compression, Second-Extension, smallest k.
Option IDs (Table 5-1): id_len = 3 (n <= 8), 4 (8 < n <= 16), 5 (16 < n <= 32);
  Restricted set (n <= 4): 1 (n = 1, 2), 2 (n = 3, 4). Split k -> ID k + 1, no-compression
  -> all ones, Zero-Block / Second-Extension -> id_len + 1 bits 0...0 / 0...01.
CDS layout: ID | [n-bit reference sample] | codes; MSB first (§1.5.2). The stream ends
  with zero fill bits to an octet boundary (§5.3.2.1 d, e).

The decoder needs the a-priori parameters of §5.3.2.2: n, J, r, Basic/Restricted and
the sample count N (blocks are padded to J samples by the encoder; the decoder drops
the padding). MISSION holds this project's values (n = 16, J = 16, r = 128, Basic),
used by SPECBURST (APID 0x024) encoding 1: the burst's raw u16 counts in
CHANNEL-MAJOR order (channel_major() / time_major() convert).

`pad_rsi` is NOT part of the standard's packet format: the official ExtendedParameters
test files fill each reference interval to an octet boundary (their README), so the
decoder and encoder accept it in order to verify those files. The mission never uses it.
Not implemented: signed samples, preprocessor absent/bypassed, the Compression
Identification Packet (§6) and the file format (§7).
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

BLOCK_SIZES = (8, 16, 32, 64)          # J (§3.1.6)
MAX_BITS = 32                          # n (§3.1.6)
MAX_RSI = 4096                         # r, blocks (§4.3)
SEGMENT_BLOCKS = 64                    # zero-block segment (§3.5.2)
MAX_SAMPLES = 1 << 22                  # ground limit per stream (official sar32bit: 2^18)
ROS_FS = 4                             # FS value of the Remainder-Of-Segment codeword

__all__ = [
    "MISSION", "MAX_SAMPLES", "RiceError", "RiceParams", "channel_major", "decode", "encode",
    "max_encoded_len", "time_major",
]


class RiceError(ValueError):
    """The compressed stream is malformed, truncated or inconsistent with the parameters."""


@dataclass(frozen=True)
class RiceParams:
    """A-priori compression parameters (121.0-B-3 §5.3.2.2)."""

    n: int = 16                 # sample resolution, bits
    j: int = 16                 # samples per block
    r: int = 128                # blocks per reference sample interval
    restricted: bool = False    # Restricted code-option set (only n <= 4)
    pad_rsi: bool = False       # octet-align every reference interval (official test files)

    def __post_init__(self) -> None:
        for name in ("n", "j", "r"):
            v = getattr(self, name)
            if not isinstance(v, int) or isinstance(v, bool):
                raise ValueError(f"RiceParams.{name} must be an int")
        if not isinstance(self.restricted, bool) or not isinstance(self.pad_rsi, bool):
            raise ValueError("RiceParams.restricted and .pad_rsi must be bool")
        if not 1 <= self.n <= MAX_BITS:
            raise ValueError(f"n = {self.n} outside 1..{MAX_BITS}")
        if self.j not in BLOCK_SIZES:
            raise ValueError(f"J = {self.j} not one of {BLOCK_SIZES}")
        if not 1 <= self.r <= MAX_RSI:
            raise ValueError(f"r = {self.r} outside 1..{MAX_RSI}")
        if self.restricted and self.n > 4:
            raise ValueError("the Restricted option set requires n <= 4 (§5.2.1.1)")

    @property
    def id_len(self) -> int:
        """Option ID length in bits (Table 5-1)."""
        if self.restricted:
            return 1 if self.n <= 2 else 2
        return 3 if self.n <= 8 else 4 if self.n <= 16 else 5

    @property
    def kmax(self) -> int:
        """Largest split-sample k (-1: no split option, Restricted n <= 2)."""
        return (1 << self.id_len) - 3

    @property
    def xmax(self) -> int:
        return (1 << self.n) - 1


MISSION = RiceParams()


def _check_count(count: int) -> None:
    if not isinstance(count, int) or isinstance(count, bool):
        raise ValueError("sample count must be an int")
    if not 1 <= count <= MAX_SAMPLES:
        raise ValueError(f"sample count {count} outside 1..{MAX_SAMPLES}")


def max_encoded_len(count: int, params: RiceParams = MISSION) -> int:
    """Upper bound on encode()'s output in octets: id_len + J n bits per block (no-compression
    wins ties; a zero run never costs more than its blocks would), padded per stream (or
    per reference interval with pad_rsi). Equal to the output for incompressible input
    whose count is a multiple of J; matches the flight rice_max_encoded_len()."""
    _check_count(count)
    blocks = -(-count // params.j)
    block_bits = params.id_len + params.j * params.n
    if not params.pad_rsi:
        return -(-blocks * block_bits // 8)
    full, rest = divmod(blocks, params.r)
    return full * -(-params.r * block_bits // 8) + -(-rest * block_bits // 8)


# ---- Encoder (reference: same algorithm and tie rules as the flight encoder) ---------------

def _map(x: int, pred: int, xmax: int) -> int:
    theta = min(pred, xmax - pred)
    d = x - pred
    if d >= 0:
        return 2 * d if d <= theta else theta + d
    return -2 * d - 1 if -d <= theta else theta - d


def _fs(m: int) -> str:
    return "0" * m + "1"


def encode(samples: Sequence[int], params: RiceParams = MISSION) -> bytes:
    """Compresses `samples` (ints in 0..2^n - 1) into one stream (octet-aligned)."""
    count = len(samples)
    _check_count(count)
    n, J, r, xmax, il, kmax = params.n, params.j, params.r, params.xmax, params.id_len, params.kmax
    for s in samples:
        if not isinstance(s, int) or isinstance(s, bool) or not 0 <= s <= xmax:
            raise ValueError(f"sample {s!r} is not an int in 0..{xmax}")
    nblocks = -(-count // J)
    xs = list(samples) + [samples[-1]] * (nblocks * J - count)   # padding maps to delta 0
    out: list[str] = []
    nbits = 0

    def put(piece: str) -> None:
        nonlocal nbits
        out.append(piece)
        nbits += len(piece)

    def zero_run(blocks: int, ref_sample: int | None, ros: bool) -> None:
        put("0" * (il + 1))
        if ref_sample is not None:
            put(format(ref_sample, f"0{n}b"))
        put(_fs(ROS_FS if ros else blocks if blocks >= 5 else blocks - 1))

    for b0 in range(0, nblocks, r):
        rblocks = min(r, nblocks - b0)
        seg = xs[b0 * J:(b0 + rblocks) * J]
        ref_sample = seg[0]
        delta = [0] + [_map(seg[i], seg[i - 1], xmax) for i in range(1, len(seg))]
        for s0 in range(0, rblocks, SEGMENT_BLOCKS):
            run, run_ref = 0, False
            for bi in range(s0, min(s0 + SEGMENT_BLOCKS, rblocks)):
                blk = delta[bi * J:(bi + 1) * J]
                ref = bi == 0
                if not any(blk):
                    if run == 0:
                        run_ref = ref
                    run += 1
                    continue
                if run:
                    zero_run(run, ref_sample if run_ref else None, False)
                    run = 0
                body = blk[1:] if ref else blk
                nc_len = len(body) * n
                split = [sum(v >> k for v in body) + len(body) * (k + 1) for k in range(kmax + 1)]
                best_k = min(range(len(split)), key=split.__getitem__) if split else -1
                best_len = split[best_k] if split else math.inf
                pairs = list(zip(blk[0::2], blk[1::2]))
                se_len = 1 + sum((a + c) * (a + c + 1) // 2 + c + 1 for a, c in pairs)
                head = format(ref_sample, f"0{n}b") if ref else ""
                if nc_len <= best_len and nc_len <= se_len:                 # §3.7.4 a)
                    put("1" * il + head + "".join(format(v, f"0{n}b") for v in body))
                elif se_len <= best_len:                                     # §3.7.4 b)
                    put("0" * il + "1" + head +
                        "".join(_fs((a + c) * (a + c + 1) // 2 + c) for a, c in pairs))
                else:                                                        # §3.7.4 c)
                    k = best_k
                    lsbs = "".join(format(v & ((1 << k) - 1), f"0{k}b") for v in body) if k else ""
                    put(format(k + 1, f"0{il}b") + head + "".join(_fs(v >> k) for v in body) + lsbs)
            if run:
                zero_run(run, ref_sample if run_ref else None, run >= 5)
        if params.pad_rsi and nbits % 8:
            put("0" * (8 - nbits % 8))
    if nbits % 8:
        put("0" * (8 - nbits % 8))
    bits = "".join(out)
    return int(bits, 2).to_bytes(len(bits) // 8, "big")


# ---- Decoder ---------------------------------------------------------------------------------

class _Bits:
    """MSB-first reader over a '0'/'1' string (str.find gives C-speed FS decoding)."""

    def __init__(self, data: bytes):
        self.total = len(data) * 8
        self.s = format(int.from_bytes(data, "big"), f"0{self.total}b") if data else ""
        self.pos = 0

    def take(self, nb: int) -> int:
        if nb == 0:
            return 0
        end = self.pos + nb
        if end > self.total:
            raise RiceError("compressed stream truncated")
        v = int(self.s[self.pos:end], 2)
        self.pos = end
        return v

    def fs(self) -> int:
        i = self.s.find("1", self.pos)
        if i < 0:
            raise RiceError("compressed stream truncated inside an FS codeword")
        m = i - self.pos
        self.pos = i + 1
        return m

    def align(self, strict: bool) -> None:
        end = min(-(-self.pos // 8) * 8, self.total)
        if strict and "1" in self.s[self.pos:end]:
            raise RiceError("non-zero fill bits")
        self.pos = end


def decode(data: bytes | bytearray | memoryview, count: int, params: RiceParams = MISSION, *,
           strict: bool = True, stats: dict[str, int] | None = None) -> list[int]:
    """Restores `count` samples from one stream.

    strict=True (default) also requires what a conforming encoder always produces: fewer
    than 8 fill bits after the last CDS, all fill bits zero, and ROS only for a remainder of
    5 or more blocks. strict=False ignores trailing octets (e.g. an even-length packet) and
    accepts any ROS. Every structural error (truncation, a mapped value or an SE pair above
    2^n - 1, a zero run longer than its segment, delta_1 != 0 in a reference SE block)
    raises RiceError. `stats`, when given, accumulates CDS counts per option
    ("zero", "ros", "se", "nc", "k0".."k29")."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("data must be bytes-like")
    _check_count(count)
    n, J, r, xmax, il = params.n, params.j, params.r, params.xmax, params.id_len
    nc_id = (1 << il) - 1
    nblocks = -(-count // J)
    bits = _Bits(bytes(data))
    out: list[int] = []
    x = 0                                   # last reconstructed sample = next prediction

    def tally(key: str) -> None:
        if stats is not None:
            stats[key] = stats.get(key, 0) + 1

    def unmap(dl: int) -> int:
        nonlocal x
        if dl > xmax:
            raise RiceError("mapped sample out of range")
        theta = min(x, xmax - x)
        if dl <= 2 * theta:
            x = x - (dl + 1) // 2 if dl & 1 else x + dl // 2
        else:
            x = dl if x <= xmax - x else xmax - dl          # only one side remains
        return x

    for b0 in range(0, nblocks, r):
        rsi_end = min(b0 + r, nblocks)
        b = b0
        while b < rsi_end:
            seg_end = min(b0 + ((b - b0) // SEGMENT_BLOCKS + 1) * SEGMENT_BLOCKS, rsi_end)
            ref = b == b0
            option = bits.take(il)
            if option == 0:                                  # Zero-Block or Second-Extension
                second_ext = bits.take(1)
                if ref:
                    x = bits.take(n)
                    out.append(x)
                if not second_ext:
                    m = bits.fs()
                    if m == ROS_FS:
                        blocks = seg_end - b
                        if strict and blocks < 5:
                            raise RiceError("ROS codeword for fewer than 5 remaining blocks")
                        tally("ros")
                    else:
                        blocks = m + 1 if m < ROS_FS else m
                        tally("zero")
                    if blocks > seg_end - b:
                        raise RiceError("zero-block run exceeds its segment")
                    out.extend([x] * (blocks * J - (1 if ref else 0)))
                    b += blocks
                    continue
                for i in range(J // 2):
                    gamma = bits.fs()
                    s = (math.isqrt(8 * gamma + 1) - 1) // 2
                    second = gamma - s * (s + 1) // 2
                    first = s - second
                    if ref and i == 0:
                        if first != 0:
                            raise RiceError("reference block SE pair with delta_1 != 0")
                    else:
                        out.append(unmap(first))
                    out.append(unmap(second))
                tally("se")
            elif option == nc_id:
                if ref:
                    x = bits.take(n)
                    out.append(x)
                for _ in range(J - (1 if ref else 0)):
                    out.append(unmap(bits.take(n)))
                tally("nc")
            else:
                k = option - 1
                if ref:
                    x = bits.take(n)
                    out.append(x)
                cnt = J - (1 if ref else 0)
                high = [bits.fs() for _ in range(cnt)]
                if max(high) > xmax >> k:
                    raise RiceError("split-sample value out of range")
                for h in high:
                    out.append(unmap((h << k) | bits.take(k)))
                tally(f"k{k}")
            b += 1
        if params.pad_rsi:
            bits.align(strict)
    rest = bits.total - bits.pos
    if strict and (rest >= 8 or "1" in bits.s[bits.pos:]):
        raise RiceError("data after the last CDS (or non-zero fill bits)")
    return out[:count]


# ---- SPECBURST sample order -----------------------------------------------------------------

def channel_major(rows: Sequence[Sequence[int]]) -> list[int]:
    """[[ch0..chC-1] at t0, [..] at t1, ...] -> ch0(t0..tT-1), ch1(t0..), ... (contract 24.3)."""
    if not rows:
        raise ValueError("no samples")
    width = len(rows[0])
    if width == 0 or any(len(row) != width for row in rows):
        raise ValueError("rows differ in length")
    return [row[c] for c in range(width) for row in rows]


def time_major(values: Sequence[int], channels: int) -> list[list[int]]:
    """Inverse of channel_major(): channel-major values -> one list of `channels` per instant."""
    if channels < 1 or len(values) % channels:
        raise ValueError("value count is not a multiple of the channel count")
    t = len(values) // channels
    return [[values[c * t + i] for c in range(channels)] for i in range(t)]
