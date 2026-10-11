"""Space Data Link Security (CCSDS 355.0-B-2), ground side.

Security Associations (mirrors spacecraft/.../ccsds/sdls.h):
  SPI 1  TC AUTH  HMAC-SHA-256 truncated to 128 bits with the master key (v2 format)
  SPI 2  TC AE    AES-256-GCM, K_tc_ae = HMAC-SHA-256(master, b"VGQ1-SDLS-TC-AE")
  SPI 3  TM AE    AES-256-GCM, K_tm_ae = HMAC-SHA-256(master, b"VGQ1-SDLS-TM-AE")
  SPI 0  TM null SA (flight fallback before it has a key epoch): header present,
         IV and MAC all zero, data in clear text - accepted but flagged UNPROTECTED

AUTH:  | hdr | SPI 2 | SN 4 | data | MAC 16 | FECF 2 |
       MAC = HMAC-SHA-256(key, primary header || security header || data)[:16]
AE:    | hdr | SPI 2 | IV 12 | ciphertext | tag 16 | (OCF 4) | FECF 2 |
       IV  = dir u16 ('TC' 0x5443 / 'TM' 0x544D) | epoch u16 | 0x00000000 | sn u32
       AAD = primary header (TC 5 octets; USLP TFPH incl. VCF count) || security header

TC: every (re)transmission gets a FRESH sequence number: COP-1 go-back-N
retransmissions would otherwise be rejected as replays once a later frame had
been accepted. The SN is persisted so it keeps increasing across restarts; AUTH
and AE share it. TM: (epoch, sn) must increase lexicographically; epoch = the
spacecraft's SCLK partition, sn = its frame counter within the epoch.

Crash safety (Phase B PB-7 F-64). An AE sequence number is a GCM nonce, so it must never
be used twice with one key:
  * the TC SN file is written atomically (temporary file, flush, fsync, rename) BEFORE the
    frame that uses the number leaves; a write that fails stops the frame;
  * the GDS and the hardline tool (dssq.cltu) share the file: every use re-reads it and
    continues from the larger number;
  * a missing SN file means first use (0); an empty or unparsable one BLOCKS the TC uplink
    ("SDLS SN file corrupt") until a real-time HK that arrived in an AE-protected frame
    reports the spacecraft's sdls_last_sn: then sn = max(sn, sdls_last_sn) and the uplink is
    released; every later protected HK raises sn the same way (self-healing after a lost
    file). An HK from an unprotected frame never moves the SN (an injected HK could push it
    to the 2^32 wrap);
  * the TM anti-replay state (epoch, sn) is persisted atomically after every accepted
    protected frame and restored at start (a corrupt file restarts at (0, 0) with a
    warning), so old genuine frames replayed after a GDS restart are still refused;
  * a REPLAY verdict whose epoch is LOWER than the last accepted one (the MAC was valid)
    is counted as epoch_regressed: a replayed recording or an EGSE boot-counter
    regression, i.e. possible TM nonce reuse - the GDS raises a RED alarm.

AES-256-GCM is dssq.ccsds.aes_gcm (pure Python, standard library only, validated
against FIPS-197, the GCM specification's AES-256 test cases and the flight C++
vectors), so the station needs no third-party package (contract section 23 rule 2).
One cipher object is built per derived key (about 0.1 ms) and reused for every frame.

Regulatory: encryption is generally not permitted on amateur frequencies; use
AUTH or NONE there (docs/09_Regulatory_and_Safety.md).
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .aes_gcm import AesGcm, InvalidTag

SDLS_HDR_LEN = 6            # AUTH security header: SPI + SN
SDLS_MAC_LEN = 16           # AUTH trailer: truncated HMAC-SHA-256
AE_IV_LEN = 12
AE_HDR_LEN = 2 + AE_IV_LEN  # SPI + IV = 14
AE_TAG_LEN = 16
AE_OVERHEAD = AE_HDR_LEN + AE_TAG_LEN   # 30

SERVICE_NONE, SERVICE_AUTH, SERVICE_AE = 0, 1, 2
SERVICE_NAMES = {SERVICE_NONE: "NONE", SERVICE_AUTH: "AUTH", SERVICE_AE: "AE"}

SPI_NULL, SPI_AUTH, SPI_TC_AE, SPI_TM_AE = 0, 1, 2, 3
IV_DIR_TC, IV_DIR_TM = 0x5443, 0x544D       # ASCII "TC" / "TM"
LABEL_TC_AE = b"VGQ1-SDLS-TC-AE"
LABEL_TM_AE = b"VGQ1-SDLS-TM-AE"

# Flight SdlsTm.state() values (HK sdls_tm_state)
TM_STATE_NAMES = {0: "OFF", 1: "PROTECTED", 2: "WAITING FOR EPOCH", 3: "UNPROTECTED (null SA)"}

U32_MAX = 0xFFFFFFFF
_SN_RE = re.compile(r"[0-9]{1,10}")                    # ASCII decimal only
_REPLAY_RE = re.compile(r"([0-9]{1,5}) ([0-9]{1,10})")


def atomic_write_text(path: str | Path, text: str) -> None:
    """Replaces `path` with `text` so that a crash or power loss leaves either the old
    or the new content, never a partial file: write a temporary file in the same
    directory, flush, fsync, rename over the target, then fsync the directory (the
    rename itself must reach the disk). Mode 0600 (security state). Raises OSError."""
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    data = text.encode("ascii")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        try:
            view = memoryview(data)
            while view:                                # bounded: each pass writes >= 1 octet
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, p)
    except OSError:
        with contextlib.suppress(OSError):             # the target keeps its old content
            os.unlink(tmp)
        raise
    with contextlib.suppress(OSError):                 # not every filesystem allows a directory fsync
        dfd = os.open(p.parent if str(p.parent) else ".", os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)


def parse_service(value: int | str | None) -> int:
    """'NONE' / 'AUTH' / 'AE' (any case) or 0 / 1 / 2 -> service code."""
    if value is None:
        return SERVICE_NONE
    if isinstance(value, int):
        if value in SERVICE_NAMES:
            return value
    else:
        for code, name in SERVICE_NAMES.items():
            if str(value).strip().upper() == name:
                return code
    raise ValueError(f"unknown SDLS service {value!r} (NONE, AUTH or AE)")


def derive_key(master: bytes, label: bytes) -> bytes:
    """K = HMAC-SHA-256(master, ASCII label) - same derivation as the flight."""
    return hmac.new(master, label, hashlib.sha256).digest()


def build_iv(direction: int, epoch: int, sn: int) -> bytes:
    return (direction.to_bytes(2, "big") + (epoch & 0xFFFF).to_bytes(2, "big") + bytes(4)
            + (sn & 0xFFFFFFFF).to_bytes(4, "big"))


def mac(key: bytes, primary: bytes, sec_hdr: bytes, data: bytes) -> bytes:
    return hmac.new(key, primary + sec_hdr + data, hashlib.sha256).digest()[:SDLS_MAC_LEN]


def _keyed(gcm: AesGcm | None) -> AesGcm:
    """The AE cipher of an SA. Using AE without a key is a programming error (every
    caller checks `enabled` / `ae` first): a clear RuntimeError, not an AttributeError."""
    if gcm is None:
        raise RuntimeError("SDLS AE used without a key")
    return gcm


def _gcm_open(gcm: AesGcm, iv: bytes, aad: bytes, ct_tag: bytes) -> bytes | None:
    """Plaintext, or None when the tag does not verify (nothing is decrypted then)."""
    try:
        return gcm.decrypt(iv, ct_tag, aad)
    except InvalidTag:
        return None


# --- TC ---------------------------------------------------------------------------

class SdlsSender:
    """TC sending end (ApplySecurity).

    `spi` is the AUTH SA's SPI ([sdls] spi, default 1); AE always uses SPI 2.
    `self.spi` is the SPI that goes on the wire. `key` is the master key (may be
    reassigned later; the AE key is re-derived). `sn` is the LAST sequence number used;
    the next frame uses sn + 1. With `sn_file` the number is persisted atomically before
    every use, and a corrupt file blocks the sender (module docstring, F-64)."""

    def __init__(self, key: bytes | None, spi: int = SPI_AUTH, sn_file: str | Path | None = None,
                 service: int | str = SERVICE_AUTH):
        self.service = parse_service(service)
        self.spi = SPI_TC_AE if self.service == SERVICE_AE else spi
        self.sn_file = Path(sn_file) if sn_file else None
        self.sn = 0
        self.blocked: str | None = None            # reason the TC uplink is blocked, None = OK
        self._resync()
        self.key = key

    @property
    def key(self) -> bytes | None:
        return self._key

    @key.setter
    def key(self, key: bytes | None) -> None:
        self._key = key
        self._k_ae = derive_key(key, LABEL_TC_AE) if key else None
        self._gcm_ae = AesGcm(self._k_ae) if self._k_ae else None

    @property
    def enabled(self) -> bool:
        return bool(self._key) and self.service != SERVICE_NONE

    @property
    def service_name(self) -> str:
        return SERVICE_NAMES[self.service] if self.enabled else "NONE"

    @property
    def overhead(self) -> int:
        """Octets the service adds to the frame data field (AUTH 22, AE 30, NONE 0)."""
        if not self.enabled:
            return 0
        return SDLS_HDR_LEN + SDLS_MAC_LEN if self.service == SERVICE_AUTH else AE_OVERHEAD

    @property
    def uplink_blocked(self) -> bool:
        """True while this sender may not protect a frame (SDLS in use and the SN unknown)."""
        return self.enabled and self.blocked is not None

    @property
    def sn_state(self) -> str:
        """"OK" or "BLOCKED: <reason>" (snapshot link.sdls.tc_sn_state)."""
        return "OK" if self.blocked is None else f"BLOCKED: {self.blocked}"

    def _persist(self, value: int) -> None:
        if self.sn_file is not None:
            atomic_write_text(self.sn_file, str(value))

    def _resync(self) -> None:
        """Continues from the persisted number when another writer of the same file (the
        hardline tool dssq.cltu) has advanced it; a file that became corrupt blocks."""
        if self.sn_file is None or not self.sn_file.exists():
            return
        try:
            text = self.sn_file.read_text(encoding="ascii").strip()
        except (OSError, UnicodeDecodeError) as e:
            text = f"<unreadable: {e}>"
        if _SN_RE.fullmatch(text) and int(text) <= U32_MAX:
            self.sn = max(self.sn, int(text))
        else:
            self.blocked = (f"SDLS SN file corrupt ({self.sn_file}: {text[:40]!r}): TC uplink blocked until "
                            "an SDLS-protected HK reports the spacecraft's sdls_last_sn")

    def next_sn(self) -> int:
        """Consumes the next sequence number. It is on disk before it is returned, so a
        crash can never make the station use it twice; the file is re-read first, so a
        number taken by the hardline tool (dssq.cltu, same file) is never used again.
        Raises RuntimeError while blocked or at the 32-bit limit (a new key is then
        required: the number may not wrap), OSError when it cannot be persisted (the
        frame must not be sent)."""
        if self.blocked is None:
            self._resync()
        if self.blocked is not None:
            raise RuntimeError(self.blocked)
        if self.sn >= U32_MAX:
            raise RuntimeError("SDLS TC sequence number space exhausted: generate a new key "
                               "(tools/gen_sdls_key.py)")
        nxt = self.sn + 1
        self._persist(nxt)
        self.sn = nxt
        return nxt

    def observe_spacecraft_sn(self, last_sn: int, protected: bool) -> str | None:
        """HK sdls_last_sn (the last TC SN the spacecraft accepted). Only an HK that came in
        an SDLS-protected frame counts: then sn = max(sn, last_sn), which releases a block.
        Returns an event text when something changed, else None. OSError if the new value
        cannot be persisted (the block, if any, stays)."""
        if not protected or isinstance(last_sn, bool) or not isinstance(last_sn, int) \
                or not 0 <= last_sn <= U32_MAX:
            return None
        if self.blocked is None and last_sn <= self.sn:
            return None
        new = max(self.sn, last_sn)
        self._persist(new)
        old, was_blocked = self.sn, self.blocked is not None
        self.sn, self.blocked = new, None
        if was_blocked:
            return (f"SDLS TC sequence number restored to {new} from a protected HK (sdls_last_sn {last_sn}): "
                    "TC uplink released")
        return f"SDLS TC sequence number raised {old} -> {new} from a protected HK (sdls_last_sn {last_sn})"

    def protect(self, hdr: bytes, data: bytes) -> bytes:
        """Security header || (data | ciphertext) || trailer for a frame whose primary
        header is `hdr` (TC v1 5 octets, USLP TFPH 7/8; it must already carry the
        final frame length, i.e. include `overhead`). `data` = TC frame data field
        (TC v1) or the whole TFDF (USLP). Consumes the next sequence number."""
        if not self.enabled or self._key is None:
            return bytes(data)
        sn = self.next_sn()
        if self.service == SERVICE_AUTH:
            sh = self.spi.to_bytes(2, "big") + sn.to_bytes(4, "big")
            return sh + bytes(data) + mac(self._key, bytes(hdr), sh, bytes(data))
        sh = SPI_TC_AE.to_bytes(2, "big") + build_iv(IV_DIR_TC, 0, sn)
        return sh + _keyed(self._gcm_ae).encrypt(sh[2:], bytes(data), bytes(hdr) + sh)

    # The hooks dssq.ccsds.tc.build_tc_frame / build_uslp_tc_frame look for.
    def tc_overhead(self) -> int:
        return self.overhead

    def protect_tc(self, hdr: bytes, data: bytes) -> bytes:
        return self.protect(hdr, data)


class SdlsReceiver:
    """TC receiving end (reference verifier for the software spacecraft simulator
    and tests; mirrors flight Sdls::process). AE decrypts."""

    def __init__(self, key: bytes, spi: int = SPI_AUTH, service: int | str = SERVICE_AUTH):
        self.service = parse_service(service)
        self.key = key
        self.spi = SPI_TC_AE if self.service == SERVICE_AE else spi
        self._k_ae = derive_key(key, LABEL_TC_AE) if key else None
        self._gcm_ae = AesGcm(self._k_ae) if self._k_ae else None
        self.last_sn = 0
        self.failures = 0

    def _fail(self, why: str):
        self.failures += 1
        return None, why

    def process(self, frame: bytes, data: bytes, hdr_len: int = 5):
        """`frame` = whole frame (its first `hdr_len` octets are the primary header:
        TC v1 5, USLP 7/8), `data` = everything between that header and the FECF.
        Returns (plaintext | None, verdict)."""
        hdr = bytes(frame[:hdr_len])
        data = bytes(data)
        if self.service == SERVICE_AUTH:
            if len(data) < SDLS_HDR_LEN + SDLS_MAC_LEN + 1:
                return self._fail("BAD_LENGTH")
            spi = int.from_bytes(data[0:2], "big")
            sn = int.from_bytes(data[2:6], "big")
            body, tag = data[SDLS_HDR_LEN:-SDLS_MAC_LEN], data[-SDLS_MAC_LEN:]
            if spi != self.spi:
                return self._fail("BAD_SPI")
            if not hmac.compare_digest(mac(self.key, hdr, data[:SDLS_HDR_LEN], body), tag):
                return self._fail("BAD_MAC")
        elif self.service == SERVICE_AE:
            if len(data) < AE_OVERHEAD + 1:
                return self._fail("BAD_LENGTH")
            sh, iv = data[:AE_HDR_LEN], data[2:AE_HDR_LEN]
            if int.from_bytes(sh[0:2], "big") != self.spi:
                return self._fail("BAD_SPI")
            if (int.from_bytes(iv[0:2], "big") != IV_DIR_TC or iv[2:4] != bytes(2)
                    or iv[4:8] != bytes(4)):
                return self._fail("BAD_IV")
            sn = int.from_bytes(iv[8:12], "big")
            plain = _gcm_open(_keyed(self._gcm_ae), iv, hdr + sh, data[AE_HDR_LEN:])
            if plain is None:
                return self._fail("BAD_MAC")
            body = plain
        else:
            return bytes(data), "DISABLED"
        if sn <= self.last_sn:              # only after the MAC/tag is proven
            return self._fail("REPLAY")
        self.last_sn = sn
        return body, "OK"


# --- TM ---------------------------------------------------------------------------

class SdlsTmSender:
    """TM ApplySecurity, reference model of the flight SdlsTm (simulator, tests)."""

    def __init__(self, key: bytes | None, service: int | str = SERVICE_AE):
        self.ae = bool(key) and parse_service(service) == SERVICE_AE
        self._k = derive_key(key, LABEL_TM_AE) if self.ae and key else None
        self._gcm = AesGcm(self._k) if self._k else None
        self.epoch = 0
        self.sn = 0
        self.fallback = False

    @property
    def header_len(self) -> int:
        return AE_HDR_LEN if self.ae else 0

    @property
    def trailer_len(self) -> int:
        return AE_TAG_LEN if self.ae else 0

    @property
    def state(self) -> int:
        if not self.ae:
            return 0
        if self.epoch and self.sn != 0xFFFFFFFF:
            return 1
        return 3 if self.fallback else 2

    def set_epoch(self, epoch: int) -> bool:
        if epoch <= 0 or epoch < self.epoch:
            return False
        if epoch != self.epoch:
            self.epoch, self.sn = epoch, 0
        self.fallback = False
        return True

    def protect(self, hdr: bytes, data: bytes) -> bytes | None:
        """Security header || data' || trailer, or None when the frame must not be sent."""
        if not self.ae:
            return bytes(data)
        st = self.state
        if st == 3:
            return bytes(AE_HDR_LEN) + bytes(data) + bytes(AE_TAG_LEN)
        if st != 1:
            return None
        self.sn += 1
        sh = SPI_TM_AE.to_bytes(2, "big") + build_iv(IV_DIR_TM, self.epoch, self.sn)
        return sh + _keyed(self._gcm).encrypt(sh[2:], bytes(data), bytes(hdr) + sh)


@dataclass
class TmSecResult:
    verdict: str                 # OK | UNPROTECTED | NONE | BAD_LENGTH | BAD_SPI | BAD_IV | BAD_MAC | REPLAY
    data: bytes | None           # plaintext data field (TM) / TFDF (USLP); None when rejected
    spi: int | None = None
    epoch: int | None = None
    sn: int | None = None

    @property
    def ok(self) -> bool:
        """The data may be decoded."""
        return self.verdict in ("OK", "UNPROTECTED", "NONE")

    @property
    def protected(self) -> bool:
        return self.verdict == "OK"


class SdlsTmReceiver:
    """TM ProcessSecurity: AE decrypt + tag check + (epoch, sn) anti-replay; null-SA
    frames are accepted (if `accept_null`) and flagged UNPROTECTED. A frame that
    fails is dropped by the caller (never decoded) and never moves the replay state.
    With `state_file` the replay state survives a restart (module docstring, F-64):
    `load_warning` tells why a stored state was discarded, `persist_error` why the last
    write failed (the state in memory stays valid)."""

    def __init__(self, key: bytes | None, service: int | str = SERVICE_AE, accept_null: bool = True,
                 state_file: str | Path | None = None):
        self.ae = bool(key) and parse_service(service) == SERVICE_AE
        self._k = derive_key(key, LABEL_TM_AE) if self.ae and key else None
        self._gcm = AesGcm(self._k) if self._k else None
        self.accept_null = accept_null
        self.last: tuple[int, int] = (0, 0)       # last accepted (epoch, sn)
        self.frames_ok = 0
        self.auth_fail = 0
        self.replay = 0
        self.unprotected = 0
        self.epoch_regressed = 0                  # REPLAY with a lower epoch and a valid MAC
        self.state_file = Path(state_file) if state_file else None
        self.load_warning: str | None = None
        self.persist_error: str | None = None
        if self.state_file is not None and self.state_file.exists():
            try:
                text = self.state_file.read_text(encoding="ascii").strip()
            except (OSError, UnicodeDecodeError) as e:
                text = f"<unreadable: {e}>"
            m = _REPLAY_RE.fullmatch(text)
            if m and int(m.group(1)) <= 0xFFFF and int(m.group(2)) <= U32_MAX:
                self.last = (int(m.group(1)), int(m.group(2)))
            else:
                self.load_warning = (f"SDLS TM replay state file {self.state_file} corrupt ({text[:40]!r}): "
                                     "replay window restarted at (0, 0)")

    @property
    def service(self) -> int:
        return SERVICE_AE if self.ae else SERVICE_NONE

    @property
    def overhead(self) -> int:
        return AE_OVERHEAD if self.ae else 0

    def _persist(self) -> None:
        if self.state_file is None:
            return
        try:
            atomic_write_text(self.state_file, f"{self.last[0]} {self.last[1]}")
            self.persist_error = None
        except OSError as e:
            self.persist_error = f"SDLS TM replay state not saved to {self.state_file}: {e}"

    def reset_replay(self) -> None:
        """Forget the last (epoch, sn) - operator action, e.g. after the EGSE boot
        counter was lost and the spacecraft restarted at a lower epoch. Persisted."""
        self.last = (0, 0)
        self._persist()

    def _fail(self, verdict: str, **kw) -> TmSecResult:
        if verdict == "REPLAY":
            self.replay += 1
            epoch = kw.get("epoch")
            if isinstance(epoch, int) and epoch < self.last[0]:
                self.epoch_regressed += 1
        else:
            self.auth_fail += 1
        return TmSecResult(verdict, None, **kw)

    def process(self, frame: bytes, hdr_len: int, prot_end: int) -> TmSecResult:
        """`frame` = whole TM / USLP frame (FECF already checked); `hdr_len` = primary
        header octets (TM 6, USLP 8 incl. VCF count); `prot_end` = offset just past
        the security trailer (len(frame) - OCF - FECF, i.e. 194 for the 200-octet frame)."""
        frame = bytes(frame)
        if not self.ae:
            return TmSecResult("NONE", frame[hdr_len:prot_end])
        if prot_end - hdr_len < AE_OVERHEAD + 1:
            return self._fail("BAD_LENGTH")
        a, b = hdr_len + AE_HDR_LEN, prot_end - AE_TAG_LEN
        return self.process_parts(frame[:hdr_len], frame[hdr_len:a], frame[a:b], frame[b:prot_end])

    def open_frame(self, tf):
        """For a dssq.ccsds.tm.TransferFrame parsed with the AE geometry (secured=True):
        returns (frame with the plaintext in place, or None when rejected, result)."""
        r = self.process_parts(tf.primary_header, tf.sec_header, tf.protected, tf.sec_trailer)
        return (tf.with_plaintext(r.data) if r.ok else None), r

    def process_parts(self, header: bytes, sec_header: bytes, protected: bytes,
                      trailer: bytes) -> TmSecResult:
        """The same check on a frame already split into primary header (the AAD
        prefix), security header (14), protected data and security trailer (16)."""
        if not self.ae:
            return TmSecResult("NONE", bytes(protected))
        sh, data, trailer = bytes(sec_header), bytes(protected), bytes(trailer)
        if len(sh) != AE_HDR_LEN or len(trailer) != AE_TAG_LEN or not data:
            return self._fail("BAD_LENGTH")
        iv = sh[2:]
        spi = int.from_bytes(sh[0:2], "big")
        if spi == SPI_NULL:
            if not self.accept_null:
                return self._fail("BAD_SPI", spi=spi)
            if iv != bytes(AE_IV_LEN):
                return self._fail("BAD_IV", spi=spi)
            if trailer != bytes(AE_TAG_LEN):
                return self._fail("BAD_MAC", spi=spi)
            self.unprotected += 1
            return TmSecResult("UNPROTECTED", data, spi=spi)
        if spi != SPI_TM_AE:
            return self._fail("BAD_SPI", spi=spi)
        epoch = int.from_bytes(iv[2:4], "big")
        sn = int.from_bytes(iv[8:12], "big")
        if int.from_bytes(iv[0:2], "big") != IV_DIR_TM or iv[4:8] != bytes(4) or epoch == 0:
            return self._fail("BAD_IV", spi=spi, epoch=epoch, sn=sn)
        plain = _gcm_open(_keyed(self._gcm), iv, bytes(header) + sh, data + trailer)
        if plain is None:
            return self._fail("BAD_MAC", spi=spi, epoch=epoch, sn=sn)
        if (epoch, sn) <= self.last:
            return self._fail("REPLAY", spi=spi, epoch=epoch, sn=sn)
        self.last = (epoch, sn)
        self.frames_ok += 1
        self._persist()
        return TmSecResult("OK", plain, spi=spi, epoch=epoch, sn=sn)


def link_snapshot(tc: SdlsSender | None, tm: SdlsTmReceiver | None) -> dict:
    """The snapshot()['link']['sdls'] dictionary of the dashboard (contract section 10)."""
    return {
        "tc_service": tc.service_name if tc else "NONE",
        "tm_service": SERVICE_NAMES[tm.service] if tm else "NONE",
        "tm_epoch": tm.last[0] if tm else 0,
        "tm_last_sn": tm.last[1] if tm else 0,
        "tm_frames_ok": tm.frames_ok if tm else 0,
        "tm_auth_fail": tm.auth_fail if tm else 0,
        "tm_replay": tm.replay if tm else 0,
        "tm_unprotected": tm.unprotected if tm else 0,
        "tc_next_sn": ((tc.sn + 1) & 0xFFFFFFFF) if tc else 0,
    }


def load_key(path: str | Path | None) -> bytes | None:
    """The 256-bit master key from a hex file; None when no path is given or the file does
    not exist. ValueError for a file that is not exactly 64 hex digits."""
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    text = p.read_text(encoding="ascii", errors="replace").strip()
    try:
        key = bytes.fromhex(text)
    except ValueError:
        raise ValueError(f"{p}: not a hexadecimal key") from None
    if len(key) != 32:
        raise ValueError(f"{p}: expected a 256-bit (64 hex digit) key")
    return key
