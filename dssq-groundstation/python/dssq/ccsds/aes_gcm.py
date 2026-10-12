"""AES-256 (FIPS-197) and AES-256-GCM (NIST SP 800-38D), pure Python, standard library only.

Purpose
  The block cipher and AEAD mode under the SDLS authenticated-encryption service
  (CCSDS 355.0-B-2, contract section 5) at the ground station, with no third-party
  package (contract section 23 rule 2). The ground counterpart of the flight sources
  vgq1-spacecraft/sketch/src/ccsds/aes.cpp and gcm.cpp: same inputs, same octets out.

Scope (deliberately narrow, every limit enforced with a ValueError)
  * AES-256 forward cipher only. GCM never runs the inverse cipher (the CTR keystream and
    the hash subkey H = E_K(0^128) both use the forward direction).
  * 96-bit IVs only: J0 = IV || 0^31 || 1 (SP 800-38D section 7.1, step 2). Every SDLS IV
    is 12 octets (dir | epoch | 0 | sn); other IV lengths are refused, never GHASHed.
  * 128-bit tags only (the full tag; SP 800-38D section 5.2.1.2 permits shorter ones).
  * Input lengths of SP 800-38D section 5.2.1.1: plaintext <= 2^39 - 256 bits, AAD < 2^64 bits.

Algorithms
  AES  FIPS-197 section 5.1 (Cipher), 5.2 (KeyExpansion; Nk = 8 words, Nr = 14 rounds).
       The S-box is generated from its definition in section 5.1.1 (multiplicative inverse
       in GF(2^8), then the affine transformation with c = 0x63) instead of being typed in;
       the tests compare it octet for octet with the flight's literal table. Rounds 1..13
       use the 32-bit table formulation: SubBytes + ShiftRows + MixColumns of one output
       column is the XOR of four look-ups in tables derived from the S-box at import.
  GCM  SP 800-38D section 6.3 (multiplication in GF(2^128), bit 0 = MSB of octet 0,
       R = 11100001 || 0^120), 6.4 (GHASH), 6.5 (GCTR, inc32), 7.1 (GCM-AE), 7.2 (GCM-AD).
       The multiplication by H uses Shoup's 8-bit table method: 256 multiples of H per key
       plus one shared 256-entry reduction table, so one block costs 16 table steps
       instead of the 128 bit steps of Algorithm 1.

Validation (tests/test_sdls.py)
  FIPS-197 Appendix C.3 (AES-256 example); McGrew & Viega, "The Galois/Counter Mode of
  Operation (GCM)", revised submission to NIST, test cases 13-16 of its AES test vectors
  (the AES-256 cases with a 96-bit IV; 17 and 18 use 64-bit and 480-bit IVs, which this
  module refuses); every AES / GCM vector written by the flight host test
  (tools/host_test/test_crypto.cpp -> sdls_ae_vectors.txt) reproduced byte for byte; and,
  only when the optional `cryptography` package is installed on a development PC, a
  randomized cross-check against it.

Security notes
  * decrypt() recomputes the tag and compares it with hmac.compare_digest (constant time)
    BEFORE producing any plaintext; on a mismatch it raises InvalidTag and returns nothing.
  * Table look-ups indexed by secret data are not constant-time on CPUs with caches. As for
    the flight implementation, the threat model is an RF adversary who sees frames, not one
    who measures the ground computer's cache timing.
  * An instance holds the expanded key schedule (whose first two round keys ARE the
    256-bit key) and the multiples of H for its whole life. CPython cannot wipe memory
    reliably, so the key's protection is the key file's permissions and the process, not
    this object; never log or serialise an instance.
  * Instances are immutable after construction and therefore safe to share between threads.

Speed (`python -m dssq.ccsds.aes_gcm` prints these figures for the machine it runs on)
  Measured 2026-10-11 on the development host (Intel Xeon @ 2.80 GHz, x86-64), best of 7
  runs of benchmark(200): one 200-octet SDLS TM AE frame (20 octets AAD, 158 octets of
  ciphertext, 16-octet tag) takes 280 us to encrypt or decrypt-and-verify under CPython
  3.11, 320 us under 3.12 and 300-325 us under 3.13; key set-up (once per key) about
  100 us. The VENTUNO Q's QCS8275 has not been measured: VERIFY with the same command on
  the board. Budget: the fastest E22 air rate (62.5 kbit/s) carries at most about 33
  CADUs of 236 octets per second (236 x 8 bit / 62 500 bit/s = 30 ms each): 33 x 0.33 ms
  = 11 ms of CPU per second, about 1 % of one core here (11 % on a ten times slower core).
"""
from __future__ import annotations

import hmac
import time

__all__ = ["BLOCK_LEN", "IV_LEN", "KEY_LEN", "SBOX", "TAG_LEN", "Aes256", "AesGcm", "InvalidTag"]

KEY_LEN = 32                                # AES-256 key, octets
BLOCK_LEN = 16                              # AES block, octets
IV_LEN = 12                                 # GCM IV accepted here, octets (96 bits)
TAG_LEN = 16                                # GCM tag produced and required, octets (128 bits)
ROUNDS = 14                                 # Nr for a 256-bit key, Nk = 8 (FIPS-197 section 5)
MAX_PLAINTEXT_LEN = (2 ** 39 - 256) // 8    # SP 800-38D section 5.2.1.1, octets
MAX_AAD_LEN = (2 ** 64 - 1) // 8            # SP 800-38D section 5.2.1.1, octets

_MASK32 = 0xFFFFFFFF
_GCM_R = 0xE1 << 120                        # R = 11100001 || 0^120 (SP 800-38D section 6.3)


class InvalidTag(Exception):
    """The GCM tag did not verify: the data, AAD, IV or key differ from the sender's."""


def _octets(name: str, value: object) -> bytes:
    """Bytes-like input as bytes. Refuses int and str: bytes(5) would silently be 5 zeros."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    raise TypeError(f"{name} must be bytes-like, not {type(value).__name__}")


# ------------------------------------------------------------------ AES tables
def _xtime(a: int) -> int:
    """a * x in GF(2^8) modulo x^8 + x^4 + x^3 + x + 1 (FIPS-197 section 4.2.1)."""
    a <<= 1
    return a ^ 0x11B if a & 0x100 else a


def _build_sbox() -> tuple[int, ...]:
    """FIPS-197 section 5.1.1: b = inverse of a in GF(2^8) (0 maps to 0), then
    b'_i = b_i ^ b_(i+4) ^ b_(i+5) ^ b_(i+6) ^ b_(i+7) ^ c_i with c = 0x63, which is
    b ^ rotl(b, 1) ^ rotl(b, 2) ^ rotl(b, 3) ^ rotl(b, 4) ^ 0x63."""
    exp = [0] * 255                          # antilog table over the generator 0x03 (x + 1)
    log = [0] * 256
    v = 1
    for i in range(255):
        exp[i] = v
        log[v] = i
        v ^= _xtime(v)                       # v * 0x03 = v * x + v
    sbox = []
    for a in range(256):
        inv = exp[(255 - log[a]) % 255] if a else 0
        b, rot = inv, inv
        for _ in range(4):
            rot = ((rot << 1) | (rot >> 7)) & 0xFF
            b ^= rot
        sbox.append(b ^ 0x63)
    return tuple(sbox)


SBOX: tuple[int, ...] = _build_sbox()

# Round tables: _TE0[s] is the MixColumns image of S-box output s entering row 0 of a
# column, (2s, s, s, 3s) packed most-significant octet first (FIPS-197 section 5.1.3
# matrix, first column); rows 1..3 use the same word rotated right by 8, 16, 24 bits.
_TE0 = tuple((_xtime(s) << 24) | (s << 16) | (s << 8) | (_xtime(s) ^ s) for s in SBOX)
_TE1 = tuple(((t >> 8) | (t << 24)) & _MASK32 for t in _TE0)
_TE2 = tuple(((t >> 16) | (t << 16)) & _MASK32 for t in _TE0)
_TE3 = tuple(((t >> 24) | (t << 8)) & _MASK32 for t in _TE0)


def _sub_word(w: int) -> int:
    """SubWord (FIPS-197 section 5.2): the S-box on each octet of a 32-bit word."""
    s = SBOX
    return (s[w >> 24] << 24) | (s[(w >> 16) & 0xFF] << 16) | (s[(w >> 8) & 0xFF] << 8) | s[w & 0xFF]


class Aes256:
    """AES-256 forward cipher. State words are the columns of FIPS-197 section 3.4
    (octet r of column c = input octet r + 4c), packed most-significant octet first."""

    __slots__ = ("_k_first", "_k_last", "_k_rounds")

    def __init__(self, key: bytes | bytearray | memoryview) -> None:
        k = _octets("key", key)
        if len(k) != KEY_LEN:
            raise ValueError(f"AES-256 key must be {KEY_LEN} octets, got {len(k)}")
        # KeyExpansion (FIPS-197 section 5.2): 4 * (Nr + 1) = 60 words.
        w = [int.from_bytes(k[i:i + 4], "big") for i in range(0, KEY_LEN, 4)]
        rcon = 0x01
        for i in range(8, 4 * (ROUNDS + 1)):
            t = w[i - 1]
            if i % 8 == 0:                                       # RotWord, SubWord, Rcon
                t = _sub_word(((t << 8) | (t >> 24)) & _MASK32) ^ (rcon << 24)
                rcon = _xtime(rcon)
            elif i % 8 == 4:                                     # extra SubWord when Nk = 8
                t = _sub_word(t)
            w.append(w[i - 8] ^ t)
        # Grouped per round so the round loop unpacks four words instead of indexing.
        self._k_first: tuple[int, ...] = tuple(w[0:4])
        self._k_rounds: tuple[tuple[int, ...], ...] = tuple(tuple(w[i:i + 4]) for i in range(4, 4 * ROUNDS, 4))
        self._k_last: tuple[int, ...] = tuple(w[4 * ROUNDS:])

    def encrypt_block(self, block: bytes | bytearray | memoryview) -> bytes:
        """One 16-octet block through the forward cipher."""
        b = _octets("block", block)
        if len(b) != BLOCK_LEN:
            raise ValueError(f"AES block must be {BLOCK_LEN} octets, got {len(b)}")
        return self._encrypt(int.from_bytes(b[0:4], "big"), int.from_bytes(b[4:8], "big"),
                             int.from_bytes(b[8:12], "big"),
                             int.from_bytes(b[12:16], "big")).to_bytes(BLOCK_LEN, "big")

    def _encrypt(self, s0: int, s1: int, s2: int, s3: int) -> int:
        """Cipher (FIPS-197 section 5.1) on four column words; returns the 128-bit output."""
        te0, te1, te2, te3, sb = _TE0, _TE1, _TE2, _TE3, SBOX
        k0, k1, k2, k3 = self._k_first                           # round 0: AddRoundKey
        s0 ^= k0
        s1 ^= k1
        s2 ^= k2
        s3 ^= k3
        # Rounds 1..13. Output column c takes row r from input column c + r (ShiftRows).
        for k0, k1, k2, k3 in self._k_rounds:
            t0 = te0[s0 >> 24] ^ te1[(s1 >> 16) & 0xFF] ^ te2[(s2 >> 8) & 0xFF] ^ te3[s3 & 0xFF] ^ k0
            t1 = te0[s1 >> 24] ^ te1[(s2 >> 16) & 0xFF] ^ te2[(s3 >> 8) & 0xFF] ^ te3[s0 & 0xFF] ^ k1
            t2 = te0[s2 >> 24] ^ te1[(s3 >> 16) & 0xFF] ^ te2[(s0 >> 8) & 0xFF] ^ te3[s1 & 0xFF] ^ k2
            s3 = te0[s3 >> 24] ^ te1[(s0 >> 16) & 0xFF] ^ te2[(s1 >> 8) & 0xFF] ^ te3[s2 & 0xFF] ^ k3
            s0, s1, s2 = t0, t1, t2
        # Round 14: SubBytes, ShiftRows, AddRoundKey (no MixColumns).
        k0, k1, k2, k3 = self._k_last
        o0 = ((sb[s0 >> 24] << 24) | (sb[(s1 >> 16) & 0xFF] << 16)
              | (sb[(s2 >> 8) & 0xFF] << 8) | sb[s3 & 0xFF]) ^ k0
        o1 = ((sb[s1 >> 24] << 24) | (sb[(s2 >> 16) & 0xFF] << 16)
              | (sb[(s3 >> 8) & 0xFF] << 8) | sb[s0 & 0xFF]) ^ k1
        o2 = ((sb[s2 >> 24] << 24) | (sb[(s3 >> 16) & 0xFF] << 16)
              | (sb[(s0 >> 8) & 0xFF] << 8) | sb[s1 & 0xFF]) ^ k2
        o3 = ((sb[s3 >> 24] << 24) | (sb[(s0 >> 16) & 0xFF] << 16)
              | (sb[(s1 >> 8) & 0xFF] << 8) | sb[s2 & 0xFF]) ^ k3
        return (o0 << 96) | (o1 << 64) | (o2 << 32) | o3


# ------------------------------------------------------------------ GHASH tables
def _mulx(v: int) -> int:
    """V * x in GF(2^128), GCM bit order: shift right; a 1 dropping out folds in R."""
    return (v >> 1) ^ _GCM_R if v & 1 else v >> 1


def _times_x8(v: int) -> int:
    for _ in range(8):
        v = _mulx(v)
    return v


# _RED[b] = (b in the 8 least significant integer bits) * x^8. Multiplying any V by x^8 is
# then (V >> 8) ^ _RED[V & 0xFF]: the upper bits shift without reduction (only the 8 bits
# that drop out need it), and the map is linear over GF(2).
_RED = tuple(_times_x8(b) for b in range(256))


class AesGcm:
    """AES-256-GCM with a 96-bit IV and a 128-bit tag.

    encrypt(iv, plaintext, aad) -> ciphertext || tag        (GCM-AE, SP 800-38D section 7.1)
    decrypt(iv, ciphertext || tag, aad) -> plaintext         (GCM-AD, section 7.2)
    The call shape matches the ciphertext-with-appended-tag convention the SDLS layer uses
    for its | security header | data | trailer | frames."""

    __slots__ = ("_aes", "_hmul")

    def __init__(self, key: bytes | bytearray | memoryview) -> None:
        self._aes = Aes256(key)
        h = self._aes._encrypt(0, 0, 0, 0)                       # H = E_K(0^128)
        # _hmul[b] = (octet b placed as the first octet of a block) * H. Bit 7 of b is the
        # coefficient of x^0, so _hmul[0x80] = H, _hmul[0x40] = H * x, ... _hmul[0x01] = H * x^7;
        # every other entry is the XOR of those (the product is linear in b).
        hmul = [0] * 256
        bit = 0x80
        while bit:
            hmul[bit] = h
            h = _mulx(h)
            bit >>= 1
        for b in range(3, 256):
            top = 1 << (b.bit_length() - 1)
            if b != top:
                hmul[b] = hmul[top] ^ hmul[b ^ top]
        self._hmul: tuple[int, ...] = tuple(hmul)

    # ------------------------------------------------------------- primitives
    def _mul_h(self, y: int) -> int:
        """Y * H (SP 800-38D section 6.3) by Horner's rule over the 16 octets of Y, last
        octet first: Z <- Z * x^8 + octet * H."""
        hmul, red = self._hmul, _RED
        z = 0
        for shift in range(0, 128, 8):
            z = (z >> 8) ^ red[z & 0xFF] ^ hmul[(y >> shift) & 0xFF]
        return z

    def _ghash(self, aad: bytes, ct: bytes) -> int:
        """GHASH_H(A || 0^v || C || 0^u || [len(A)]64 || [len(C)]64), section 6.4 / 7.1 step 5."""
        y = 0
        for buf in (aad, ct):
            for off in range(0, len(buf), BLOCK_LEN):
                blk = buf[off:off + BLOCK_LEN]
                y = self._mul_h(y ^ (int.from_bytes(blk, "big") << (8 * (BLOCK_LEN - len(blk)))))
        return self._mul_h(y ^ ((8 * len(aad)) << 64) ^ (8 * len(ct)))

    def _gctr(self, iv: bytes, data: bytes) -> bytes:
        """GCTR from inc32(J0) (section 6.5 / 7.1 step 3). Counter block i ends in 2 + i;
        the plaintext limit keeps that below 2^32, so inc32 never wraps here."""
        n = len(data)
        if n == 0:
            return b""
        w0, w1, w2 = (int.from_bytes(iv[i:i + 4], "big") for i in (0, 4, 8))
        enc = self._aes._encrypt
        ks = b"".join(enc(w0, w1, w2, (2 + i) & _MASK32).to_bytes(BLOCK_LEN, "big")
                      for i in range((n + BLOCK_LEN - 1) // BLOCK_LEN))
        return (int.from_bytes(data, "big") ^ int.from_bytes(ks[:n], "big")).to_bytes(n, "big")

    def _tag(self, iv: bytes, aad: bytes, ct: bytes) -> bytes:
        """T = GCTR(J0, S) = E_K(J0) xor S with J0 = IV || 0^31 || 1 (section 7.1 steps 2, 6)."""
        w0, w1, w2 = (int.from_bytes(iv[i:i + 4], "big") for i in (0, 4, 8))
        return (self._aes._encrypt(w0, w1, w2, 1) ^ self._ghash(aad, ct)).to_bytes(TAG_LEN, "big")

    @staticmethod
    def _check(iv: bytes, aad: bytes) -> None:
        if len(iv) != IV_LEN:
            raise ValueError(f"GCM IV must be {IV_LEN} octets (96 bits), got {len(iv)}")
        if len(aad) > MAX_AAD_LEN:
            raise ValueError("GCM AAD longer than 2^64 - 1 bits (SP 800-38D 5.2.1.1)")

    # ------------------------------------------------------------- public API
    def encrypt(self, iv: bytes | bytearray | memoryview, plaintext: bytes | bytearray | memoryview,
                aad: bytes | bytearray | memoryview = b"") -> bytes:
        """GCM-AE: returns the ciphertext (same length as the plaintext) followed by the
        16-octet tag. The caller guarantees the (key, IV) pair is never reused (SDLS:
        a fresh sequence number per frame, contract section 5)."""
        iv_b, pt, a = _octets("iv", iv), _octets("plaintext", plaintext), _octets("aad", aad)
        self._check(iv_b, a)
        if len(pt) > MAX_PLAINTEXT_LEN:
            raise ValueError("GCM plaintext longer than 2^39 - 256 bits (SP 800-38D 5.2.1.1)")
        ct = self._gctr(iv_b, pt)
        return ct + self._tag(iv_b, a, ct)

    def decrypt(self, iv: bytes | bytearray | memoryview, ciphertext_and_tag: bytes | bytearray | memoryview,
                aad: bytes | bytearray | memoryview = b"") -> bytes:
        """GCM-AD: verifies the trailing 16-octet tag over aad || ciphertext and only then
        returns the plaintext. Raises InvalidTag on a mismatch (nothing is decrypted)."""
        iv_b, data, a = _octets("iv", iv), _octets("ciphertext_and_tag", ciphertext_and_tag), _octets("aad", aad)
        self._check(iv_b, a)
        if len(data) < TAG_LEN:
            raise ValueError(f"GCM input must hold the {TAG_LEN}-octet tag, got {len(data)} octets")
        if len(data) - TAG_LEN > MAX_PLAINTEXT_LEN:
            raise ValueError("GCM ciphertext longer than 2^39 - 256 bits (SP 800-38D 5.2.1.1)")
        ct, tag = data[:-TAG_LEN], data[-TAG_LEN:]
        if not hmac.compare_digest(self._tag(iv_b, a, ct), tag):
            raise InvalidTag("GCM tag mismatch")
        return self._gctr(iv_b, ct)


# ------------------------------------------------------------------ benchmark
def benchmark(frames: int = 300) -> dict[str, float]:
    """Microseconds per operation on this machine, SDLS TM AE geometry (contract section 5):
    AAD = 6-octet TM primary header + 14-octet security header, 158 octets of frame data."""
    if not 1 <= frames <= 100_000:
        raise ValueError(f"frames must be 1..100000, got {frames}")
    key, iv, aad, data = bytes(range(32)), bytes(12), bytes(20), bytes(range(158))
    t0 = time.perf_counter()
    gcm = AesGcm(key)
    setup = time.perf_counter() - t0
    sealed = gcm.encrypt(iv, data, aad)
    t0 = time.perf_counter()
    for _ in range(frames):
        gcm.encrypt(iv, data, aad)
    enc = (time.perf_counter() - t0) / frames
    t0 = time.perf_counter()
    for _ in range(frames):
        gcm.decrypt(iv, sealed, aad)
    dec = (time.perf_counter() - t0) / frames
    return {"key_setup_us": setup * 1e6, "encrypt_frame_us": enc * 1e6, "decrypt_frame_us": dec * 1e6}


if __name__ == "__main__":
    for name, us in benchmark().items():
        print(f"{name:18s} {us:9.1f}")
