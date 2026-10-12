"""Stored command sequences, ground half (dssq/cmdparse.py, contract 24.1 / 24.2).

Sequence-file parsing with line-numbered errors, the dictionary encoder, the SEQLOAD
argument block, the refusal policy, and a cross-check against the FLIGHT sequencer
(tools/host_test/test_autonomy.cpp writes seq_vectors.txt: blocks the flight accepted,
the entries it stored, its forbidden-opcode list and a MAGCAL application).
"""
from __future__ import annotations

import shutil
import struct
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from dssq.cmdparse import (
    REBOOT_OPCODE,
    SEQ_CMD_MAX,
    SEQ_HORIZON_S,
    SEQ_SLOTS,
    SEQCTRL_OPCODE,
    SEQLOAD_ARG_LEN,
    SEQLOAD_OPCODE,
    ArgToken,
    CommandError,
    SequenceEntry,
    SequenceError,
    check_user_data,
    encode_dictionary,
    help_text,
    parse,
    pack_seqload,
    parse_sequence,
    seq_forbidden_opcodes,
    seqload_commands,
    tc_packet,
    unpack_seqload,
)
from dssq.dictionary import COMMANDS

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "tools" / "host_test"

GOOD = """\
# VGQ-1 test sequence            (comment line)

T+00:00:00 NOOP                  # first thing after START
t+00:10:00 mode mode=ENCOUNTER
T+01:02:03 MODE 3
SCLK 87400 PING tag=0xDEADBEEF\r
T+168:00:00 PKTRATE apid=0x022 period_s=030
"""


def _entry(text: str) -> SequenceEntry:
    (e,) = parse_sequence(text)
    return e


# ---- parsing -------------------------------------------------------------------------
def test_parse_valid_file() -> None:
    es = parse_sequence(GOOD)
    assert [e.line_no for e in es] == [3, 4, 5, 6, 7]
    assert [(e.relative, e.t_s, e.mnemonic) for e in es] == [
        (True, 0, "NOOP"), (True, 600, "MODE"), (True, 3723, "MODE"), (False, 87400, "PING"),
        (True, SEQ_HORIZON_S, "PKTRATE")]
    assert es[1].args == (("mode", "ENCOUNTER"),)
    assert es[2].args == ((None, "3"),)
    assert es[4].args == (("apid", "0x022"), ("period_s", "030"))
    assert es[1].source == "t+00:10:00 mode mode=ENCOUNTER"
    assert [e.time_text for e in es] == ["T+00:00:00", "T+00:10:00", "T+01:02:03", "SCLK 87400",
                                         "T+168:00:00"]


def test_comment_may_hold_anything() -> None:
    e = _entry("T+00:00:05 NOOP # température, ünïcode — fine in a comment")
    assert e.mnemonic == "NOOP" and e.t_s == 5


@pytest.mark.parametrize("text, line, fragment", [
    ("T+1:2:3 NOOP", 1, "bad relative time"),
    ("T+00:60:00 NOOP", 1, "bad relative time"),
    ("T+00:00:60 NOOP", 1, "bad relative time"),
    ("T+1000:00:00 NOOP", 1, "bad relative time"),
    ("T+-1:00:00 NOOP", 1, "bad relative time"),
    ("T+168:00:01 NOOP", 1, "exceeds 7 days"),
    ("# c\n\nSCLK", 3, "SCLK needs a time"),
    ("SCLK NOOP", 1, "bad SCLK time"),
    ("SCLK 4294967296 NOOP", 1, "outside 0..4294967295"),
    ("SCLK -1 NOOP", 1, "outside 0..4294967295"),
    ("SCLK 1_000 NOOP", 1, "bad SCLK time"),
    ("NOOP", 1, "must start with T+HH:MM:SS or SCLK"),
    ("T+00:00:01", 1, "missing command mnemonic"),
    ("T+00:00:01 9NOOP", 1, "bad mnemonic"),
    ("T+00:00:01 BD NOOP", 1, "BD (bypass) is not allowed"),
    ("T+00:00:01 UNLOCK", 1, "COP-1 directive"),
    ("T+00:00:01 SETVR 3", 1, "COP-1 directive"),
    ("T+00:00:01 WAKE", 1, "ground-station directive"),
    ("T+00:00:01 SEQLOAD", 1, "no nesting"),
    ("T+00:00:01 seqctrl action=0", 1, "no nesting"),
    ("T+00:00:01 REBOOT magic=0xB007", 1, "RAM-only table"),
    ("T+00:00:01 MODE mode=", 1, "bad argument 'mode='"),
    ("T+00:00:01 MODE =3", 1, "bad argument '=3'"),
    ("T+00:00:01 MODE 1x=3", 1, "bad argument '1x=3'"),
    ("T+00:00:01 PKTRATE apid=16 16", 1, "mix of named"),
    ("T+00:00:01 MODE mode=3 MODE=4", 1, "given twice"),
    ("T+00:00:01 NOOP\nT+00:00:02 MODE mode=CRUISE é", 2, "non-ASCII"),
])
def test_parse_errors_carry_line_numbers(text: str, line: int, fragment: str) -> None:
    with pytest.raises(SequenceError) as ei:
        parse_sequence(text)
    assert ei.value.line_no == line
    assert fragment in ei.value.message
    assert str(ei.value).startswith(f"line {line}: ")


def test_table_size_limit() -> None:
    ok = "\n".join(f"T+00:00:{i:02d} NOOP" for i in range(SEQ_SLOTS))
    assert len(parse_sequence(ok)) == SEQ_SLOTS
    with pytest.raises(SequenceError) as ei:
        parse_sequence("# header\n" + ok + "\nT+00:01:00 NOOP")
    assert ei.value.line_no == SEQ_SLOTS + 2 and "more than 32 commands" in ei.value.message


@pytest.mark.parametrize("text", ["", "   \n\t\n", "# only\n# comments\n"])
def test_empty_sequence_refused(text: str) -> None:
    with pytest.raises(SequenceError) as ei:
        parse_sequence(text)
    assert ei.value.line_no is None and "no commands" in str(ei.value)


def test_input_type_and_size_validated() -> None:
    with pytest.raises(SequenceError):
        parse_sequence(b"T+00:00:01 NOOP")  # type: ignore[arg-type]
    with pytest.raises(SequenceError, match="longer than"):
        parse_sequence("#" * 70_000)


def test_sequence_error_is_a_command_error() -> None:
    with pytest.raises(CommandError):
        parse_sequence("bogus")


# ---- dictionary encoder ----------------------------------------------------------------
@pytest.mark.parametrize("mn, args, expect", [
    ("NOOP", (), "01"),
    ("MODE", (("mode", "ENCOUNTER"),), "0203"),
    ("mode", ((None, "enc"),), "0203"),
    ("PING", (("tag", "0xDEADBEEF"),), "09deadbeef"),
    ("PKTRATE", (("period_s", "030"), ("APID", "0x022")), "060022001e"),
    ("PKTRATE", ((None, "34"), (None, "0")), "0600220000"),
    ("SENSORS", (("mask", "-0x0"),), "0a0000"),
])
def test_encode_dictionary(mn: str, args: tuple[ArgToken, ...], expect: str) -> None:
    assert encode_dictionary(mn, args).hex() == expect


@pytest.mark.parametrize("mn, args, fragment", [
    ("FOO", (), "unknown command 'FOO'"),
    ("MODE", (("level", "3"),), "MODE has no argument 'level' (arguments: mode)"),
    ("PKTRATE", (("apid", "16"),), "missing argument(s) period_s"),
    ("PKTRATE", ((None, "16"),), "PKTRATE takes 2 argument(s) (apid, period_s), got 1"),
    ("NOOP", ((None, "1"),), "NOOP takes 0 argument(s) (none), got 1"),
    ("MODE", ((None, "9"),), "mode=9 outside 1..4"),
    ("MODE", ((None, "three"),), "'three' is not a number"),
    ("MODE", ((None, "1_0"),), "'1_0' is not a number"),
    ("MODE", ((None, "0b11"),), "'0b11' is not a number"),
    ("MODE", ((None, "٣"),), "non-ASCII"),          # ARABIC-INDIC DIGIT THREE
    ("MODE", (("mode", "3"), ("mode", "4")), "given twice"),
    ("PKTRATE", (("apid", "16"), (None, "0")), "mix of named"),
])
def test_encode_dictionary_errors(mn: str, args: tuple[ArgToken, ...], fragment: str) -> None:
    with pytest.raises(CommandError) as ei:
        encode_dictionary(mn, args)
    assert fragment in str(ei.value)


# ---- SEQLOAD block -------------------------------------------------------------------------
def test_seqload_block_layout() -> None:
    # Hand-built per 24.1: slot | flags | t_s (u32 BE) | len | cmd[12] zero padded.
    e = _entry("T+01:02:03 MODE mode=3")
    assert pack_seqload(7, e).hex() == "07" "01" "00000e8b" "02" "0203" + "00" * 10
    e = _entry("SCLK 87400 PING tag=0xDEADBEEF")
    assert pack_seqload(17, e).hex() == "11" "00" "00015568" "05" "09deadbeef" + "00" * 7
    for blk in (pack_seqload(0, e), pack_seqload(31, e)):
        assert len(blk) == SEQLOAD_ARG_LEN


def test_unpack_roundtrip_and_validation() -> None:
    e = _entry("T+00:00:09 FPERIOD ms=1000")
    a = unpack_seqload(pack_seqload(5, e))
    assert (a.slot, a.relative, a.t_s, a.cmd) == (5, True, 9, bytes.fromhex("0503e8"))
    good = bytearray(pack_seqload(5, e))
    bad: list[tuple[bytes, str]] = [
        (bytes(good[:18]), "18 octets"),
        (bytes([32]) + bytes(good[1:]), "slot 32"),
        (bytes(good[:1]) + b"\x02" + bytes(good[2:]), "reserved flag"),
        (bytes(good[:6]) + b"\x00" + bytes(good[7:]), "length 0"),
        (bytes(good[:6]) + b"\x0d" + bytes(good[7:]), "length 13"),
        (bytes(good[:-1]) + b"\x01", "padding"),
    ]
    for blk, fragment in bad:
        with pytest.raises(CommandError, match=fragment):
            unpack_seqload(blk)


def test_forbidden_policy() -> None:
    fb = seq_forbidden_opcodes()
    assert {REBOOT_OPCODE, SEQLOAD_OPCODE, SEQCTRL_OPCODE} <= fb
    assert fb == {REBOOT_OPCODE, SEQLOAD_OPCODE, SEQCTRL_OPCODE} | {
        c.opcode for c in COMMANDS.values() if c.hazardous}
    for text in ("T+00:00:01 SSRCLEAR", "T+00:00:01 AIRRATE code=2 revert_s=600",
                 "T+00:00:01 TXINHIBIT seconds=60", "T+00:00:01 HIBERNATE 600"):
        e = _entry(text)
        with pytest.raises(SequenceError, match="cannot be stored") as ei:
            pack_seqload(0, e)
        assert ei.value.line_no == 1
    # The opcode decides, not the name: an encoder producing SEQLOAD is refused too.
    with pytest.raises(SequenceError, match=r"opcode 0x1D"):
        pack_seqload(0, _entry("T+00:00:01 NOOP"), lambda mn, a: bytes([SEQLOAD_OPCODE]))


def test_injected_encoder() -> None:
    seen: list[tuple[str, Sequence[ArgToken]]] = []

    def enc(mn: str, args: Sequence[ArgToken]) -> bytes:
        seen.append((mn, args))
        return bytes([0x01])

    e = _entry("T+00:00:02 WHATEVER x=1 y=2")
    assert pack_seqload(3, e, enc).hex() == "0301000000020101" + "00" * 11
    assert seen == [("WHATEVER", (("x", "1"), ("y", "2")))]

    def too_long(mn: str, args: Sequence[ArgToken]) -> bytes:
        return bytes(SEQ_CMD_MAX + 1)

    def empty(mn: str, args: Sequence[ArgToken]) -> bytes:
        return b""

    def not_bytes(mn: str, args: Sequence[ArgToken]) -> bytes:
        return "01"  # type: ignore[return-value]

    def refuses(mn: str, args: Sequence[ArgToken]) -> bytes:
        raise CommandError("value out of range")

    def packs_badly(mn: str, args: Sequence[ArgToken]) -> bytes:
        return struct.pack(">B", 300)

    cases = [(too_long, "encodes to 13 octets"), (empty, "encodes to 0 octets"),
             (not_bytes, "encoder returned str"), (refuses, "WHATEVER: value out of range"),
             (packs_badly, "WHATEVER: ")]
    for fn, fragment in cases:
        with pytest.raises(SequenceError) as ei:
            pack_seqload(0, e, fn)
        assert ei.value.line_no == 1 and fragment in ei.value.message


def test_slot_and_absolute_horizon() -> None:
    e = _entry("SCLK 1000 NOOP")
    for slot in (-1, 32, True, "1"):
        with pytest.raises(SequenceError, match="slot"):
            pack_seqload(slot, e)  # type: ignore[arg-type]
    assert pack_seqload(0, e, now_sclk_s=999)[2:6] == struct.pack(">I", 1000)
    with pytest.raises(SequenceError, match="not in the future"):
        pack_seqload(0, e, now_sclk_s=1000)
    with pytest.raises(SequenceError, match="not in the future"):
        pack_seqload(0, e, now_sclk_s=1001)
    assert pack_seqload(0, _entry(f"SCLK {1000 + SEQ_HORIZON_S} NOOP"), now_sclk_s=1000)
    with pytest.raises(SequenceError, match="more than 7 days"):
        pack_seqload(0, _entry(f"SCLK {1001 + SEQ_HORIZON_S} NOOP"), now_sclk_s=1000)
    # Modulo 2^32, as on board.
    assert pack_seqload(0, _entry("SCLK 0 NOOP"), now_sclk_s=0xFFFFFFF0)
    with pytest.raises(SequenceError, match="not in the future"):
        pack_seqload(0, _entry(f"SCLK {0xFFFFFFF0 + 2**31 - 2**32} NOOP"),
                     now_sclk_s=0xFFFFFFF0)
    for bad_now in (-1, 2**32, True):
        with pytest.raises(SequenceError, match="spacecraft SCLK"):
            pack_seqload(0, e, now_sclk_s=bad_now)
    # Relative entries do not depend on the spacecraft clock.
    assert pack_seqload(0, _entry("T+00:00:00 NOOP"), now_sclk_s=123)


def test_seqload_commands() -> None:
    es = parse_sequence(GOOD)
    cmds = seqload_commands(es, first_slot=4)
    assert [c.args["slot"] for c in cmds] == [4, 5, 6, 7, 8]
    for c, e in zip(cmds, es, strict=True):
        assert c.mnemonic == "SEQLOAD" and c.opcode == SEQLOAD_OPCODE and c.kind == "AD"
        assert not c.hazardous
        assert c.user_data[0] == SEQLOAD_OPCODE and len(c.user_data) == 1 + SEQLOAD_ARG_LEN
        a = unpack_seqload(c.user_data[1:])
        assert (a.slot, a.relative, a.t_s) == (c.args["slot"], e.relative, e.t_s)
        assert a.cmd.hex() == c.args["cmd"]
        assert c.text == f"SEQLOAD slot={a.slot} {e.source}"
        # 26-octet TC packet: fits the 27-octet limit of an SDLS AE TC frame (contract 5).
        assert len(tc_packet(c, 1)) == 26
    with pytest.raises(SequenceError, match="exceed the 32-slot table"):
        seqload_commands(es, first_slot=28)
    for bad in (-1, 32, True):
        with pytest.raises(SequenceError, match="first slot"):
            seqload_commands(es, first_slot=bad)  # type: ignore[arg-type]
    with pytest.raises(SequenceError) as ei:
        seqload_commands(parse_sequence("T+00:00:01 NOOP\nT+00:00:02 MODE 9"))
    assert ei.value.line_no == 2 and "mode=9 outside 1..4" in ei.value.message


def test_seqload_is_builder_only() -> None:
    """PB-2.4: SEQLOAD is listed (XTCE, help) but never typed: the 19-octet block comes from
    a sequence file. The dictionary's 16 arguments describe exactly that block."""
    with pytest.raises(CommandError, match="built from a sequence file"):
        parse("SEQLOAD 0 1 10 1 1 0 0 0 0 0 0 0 0 0 0 0")
    with pytest.raises(CommandError, match="built from a sequence file"):
        parse("BD SEQLOAD 0 1 10 1 1 0 0 0 0 0 0 0 0 0 0 0")
    assert "SEQLOAD" not in help_text()[0].split()
    assert any("sequence file" in line for line in help_text("SEQLOAD"))
    (cmd,) = seqload_commands(parse_sequence("T+00:00:10 PING tag=0x01020304"), first_slot=9)
    blk = cmd.user_data[1:]
    a = unpack_seqload(blk)
    fields = [a.slot, int(a.relative), a.t_s, len(a.cmd), *a.cmd.ljust(SEQ_CMD_MAX, b"\x00")]
    assert encode_dictionary("SEQLOAD", [(None, str(v)) for v in fields]) == cmd.user_data
    assert check_user_data(cmd.user_data) == (0, "")


def test_seqctrl_and_stored_command_rules() -> None:
    assert parse("SEQCTRL DELETE 7").user_data.hex() == "1e0307"
    assert parse("SEQCTRL START 0").user_data.hex() == "1e0000"
    with pytest.raises(CommandError, match="slot must be 0"):
        parse("SEQCTRL STOP 3")
    # A stored command must pass the live-command rules (flight validate_stored_command).
    with pytest.raises(SequenceError, match=r"led_cur=12 outside 0\.\.11"):
        seqload_commands(parse_sequence("T+00:00:01 SPECCFG gain=AGC cycles=59 lamps=1 led_cur=12 ind=0"))


# ---- flight cross-check -------------------------------------------------------------------
@pytest.fixture(scope="module")
def flight_vectors(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not (shutil.which("make") and (shutil.which("g++") or shutil.which("c++"))):
        pytest.skip("no host C++ toolchain")
    out = tmp_path_factory.mktemp("seqvec")
    subprocess.run(["make", "-s", "-C", str(HOST), "host_test"], check=True)
    subprocess.run([str(HOST / "host_test"), str(out)], check=True, stdout=subprocess.DEVNULL)
    path = out / "seq_vectors.txt"
    if not path.exists():
        pytest.fail("host_test did not write seq_vectors.txt")
    return path


def _records(path: Path, kind: str) -> list[list[str]]:
    rows = []
    for line in path.read_text().splitlines():
        f = line.split()
        if f and f[0] == kind:
            rows.append(f[1:])
    return rows


def test_flight_seqload_blocks_match_ground(flight_vectors: Path) -> None:
    rows = _records(flight_vectors, "seqload")
    assert len(rows) >= 4
    for blk_hex, slot, rel, t_s, cmd_hex in rows:
        blk = bytes.fromhex(blk_hex)
        a = unpack_seqload(blk)
        assert (a.slot, a.relative, a.t_s, a.cmd.hex()) == (int(slot), rel == "1", int(t_s),
                                                            cmd_hex)
        e = SequenceEntry(1, "vector", a.relative, a.t_s, "VEC", ())
        assert pack_seqload(a.slot, e, lambda mn, args, cmd=a.cmd: cmd) == blk


def test_flight_forbidden_list_matches_dictionary(flight_vectors: Path) -> None:
    flight = {int(r[0], 16) for r in _records(flight_vectors, "forbidden")}
    assert flight == seq_forbidden_opcodes(), (
        "flight CommandSequencer::opcode_forbidden() and the ground policy (hazardous "
        "dictionary commands + REBOOT/SEQLOAD/SEQCTRL) differ: "
        f"flight only {sorted(flight - seq_forbidden_opcodes())}, "
        f"ground only {sorted(seq_forbidden_opcodes() - flight)}")


def test_flight_magcal_matches_python(flight_vectors: Path) -> None:
    rows = _records(flight_vectors, "magcal")
    assert rows
    for r in rows:
        blk = bytes.fromhex(r[0])
        assert int(r[1]) == 0 and len(blk) == 19
        unit, ox, oy, oz, m11, m22, m33, m12, m13, m23 = struct.unpack(">B3h6h", blk)
        assert unit == int(r[2])
        q = 1 / 16384
        m = [[m11 * q, m12 * q, m13 * q], [m12 * q, m22 * q, m23 * q], [m13 * q, m23 * q, m33 * q]]
        raw = [float(x) for x in r[3:6]]
        d = [raw[0] - ox / 100, raw[1] - oy / 100, raw[2] - oz / 100]
        want = [sum(m[i][j] * d[j] for j in range(3)) for i in range(3)]
        got = [float(x) for x in r[6:9]]
        assert got == pytest.approx(want, abs=1e-5)
        assert check_user_data(bytes([0x20]) + blk) == (0, ""), r       # the ground accepts it too
