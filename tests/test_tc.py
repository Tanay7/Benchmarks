"""Ground TC frame parser: the flight tc_parse acceptance rules (Phase C C08).

parse_tc_frame validates frames an external MCS sends to the GDS UDP interop port before
the GDS rebuilds them for SPACECRAFT_ID; a frame addressed to another spacecraft, or
longer than the 64-octet TC frame limit, must be rejected (flight tc.cpp TC_BAD_SCID /
TC_BAD_LENGTH)."""
from __future__ import annotations

import pytest

from dssq.ccsds import SPACECRAFT_ID, TC_MAX_FRAME_LEN
from dssq.ccsds.crc import crc16_ccitt
from dssq.ccsds.tc import build_tc_frame, parse_tc_frame

NOOP = bytes.fromhex("18c0c000000001")


@pytest.mark.parametrize("framing", ["TC", "USLP"])
@pytest.mark.parametrize("bypass", [False, True])
def test_foreign_scid_is_rejected(framing, bypass):
    own = build_tc_frame(NOOP, seq=3, bypass=bypass, framing=framing)
    assert parse_tc_frame(own) is not None and parse_tc_frame(own).scid == SPACECRAFT_ID
    for scid in (0x123, SPACECRAFT_ID ^ 1, 0):
        assert parse_tc_frame(build_tc_frame(NOOP, seq=3, bypass=bypass, scid=scid, framing=framing)) is None


def _tc_v1(length: int) -> bytes:
    """A TC v1 frame whose header claims `length` octets (at least 7 octets are sent)."""
    w0 = SPACECRAFT_ID                                              # version 0, AD, SCID
    hdr = w0.to_bytes(2, "big") + ((length - 1) & 0x3FF).to_bytes(2, "big") + b"\x00"
    body = hdr + bytes(max(length, 7) - 7)
    return body + crc16_ccitt(body[:max(length - 2, 0)]).to_bytes(2, "big")


def test_tc_v1_length_limits_match_the_flight():
    assert parse_tc_frame(_tc_v1(TC_MAX_FRAME_LEN)) is not None
    assert parse_tc_frame(_tc_v1(TC_MAX_FRAME_LEN + 1)) is None      # > 64 octets
    assert parse_tc_frame(_tc_v1(7)) is not None                      # header + FECF
    assert parse_tc_frame(_tc_v1(6)) is None                          # shorter than header + FECF
