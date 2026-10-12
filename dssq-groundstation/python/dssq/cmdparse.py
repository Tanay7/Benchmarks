"""Operator command language -> validated TC Space Packets.

Syntax (case-insensitive, whitespace separated; numbers decimal or 0x-hex):
    NOOP
    MODE ENCOUNTER            PING 1234          AIRRATE 0 600
    BD <command ...>          send as a bypass (Type-BD) frame, ignores FARM state
    UNLOCK                    COP-1 control: clear FARM lockout   (Type-BC)
    SETVR <n>                 COP-1 control: set V(R) = n         (Type-BC)
    HELP [mnemonic]

Every command is checked against the command dictionary (range, enum, arity and
the command's cross-argument `check`, the rule the flight parse_user_data() applies
identically) before it can be queued — the same "build / verify / send" discipline
used in real mission operations. Hazardous commands additionally require
confirmation. builder_only commands (SEQLOAD) are refused here: they are built from a
sequence file (seqload_commands).

Stored command sequences (contract 24.1 SEQLOAD 0x1D / 24.2), the ground half of
the flight's CommandSequencer (vgq1-spacecraft/sketch/src/fsw/autonomy.h). A sequence file
is ASCII text, one command per line, '#' starts a comment:

    T+HH:MM:SS <MNEMONIC> [arg=value ...]   relative to the on-board SEQCTRL START
    SCLK <seconds> <MNEMONIC> [arg ...]     absolute spacecraft clock (s since MCU boot)

Arguments are all named (arg=value) or all positional; values as on the command
line (decimal, 0x-hex, enum names). parse_sequence() checks the syntax and the
limits (32 entries, relative times <= 7 days); seqload_commands() encodes each
entry with an injectable encoder (default: this dictionary), applies the flight's
refusal policy (hazardous commands, REBOOT, SEQLOAD, SEQCTRL; at most 12 octets)
and returns one SEQLOAD command per entry, slot = file order, for uplink through
COP-1 in that order. START is a separate operator command (SEQCTRL).
"""
from __future__ import annotations

import re
import struct
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .ccsds.packets import build_packet
from .ccsds.tc import bc_set_vr, bc_unlock
from .dictionary import APID_TC, COMMANDS, DIRECTIVES, GROUND_DIRECTIVES, CommandDef

__all__ = ["APID_TC", "ArgToken", "CommandError", "Encoder", "ParsedCommand", "SeqLoadArgs",
           "SequenceEntry", "SequenceError", "check_user_data", "encode_dictionary", "help_text",
           "pack_seqload", "parse", "parse_sequence", "seq_forbidden_opcodes", "seqload_commands",
           "tc_packet", "unpack_seqload"]

COMMAND_TEXT_MAX = 512          # characters of one typed command (MAGCAL, the longest, is ~70)


class CommandError(ValueError):
    pass


@dataclass
class ParsedCommand:
    text: str                 # normalised command text
    kind: str                 # "AD", "BD" or "BC"
    mnemonic: str
    opcode: int | None
    args: dict
    hazardous: bool
    user_data: bytes          # opcode + args (AD/BD) or BC control command


def _num(tok: str) -> int:
    """Decimal or 0x-hex integer (the sequence-file rule, _seq_int): int(tok, 0) would also
    take '1_000', '0o17', '0b1' and non-ASCII digits, which no operator means."""
    return _seq_int(tok)


def help_text(mnemonic: str | None = None) -> list[str]:
    if mnemonic:
        c = COMMANDS.get(mnemonic.upper())
        if not c:
            return [f"unknown command {mnemonic}"]
        args = " ".join(f"<{a[0]}>" for a in c.args)
        out = [f"{c.mnemonic} {args}".strip(), c.desc + (" [HAZARDOUS]" if c.hazardous else "")]
        for name, _code, lo, hi, enum in c.args:
            labels = f" ({', '.join(f'{k}={v}' for k, v in enum.items())})" if enum else ""
            out.append(f"  {name}: {lo}..{hi}{labels}")
        if c.check_doc:
            out.append(f"  rule: {c.check_doc}")
        if c.builder_only:
            out.append("  built from a sequence file (Sequences panel), not typed")
        return out
    return ["commands: " + " ".join(sorted(m for m, c in COMMANDS.items() if not c.builder_only)),
            "directives: UNLOCK SETVR <n>  prefix BD = bypass",
            "ground: " + " ".join(sorted(GROUND_DIRECTIVES))]


def parse(text: str) -> ParsedCommand:
    """One typed command -> ParsedCommand (CommandError with the reason otherwise)."""
    if not isinstance(text, str):
        raise CommandError("command text must be str")
    if len(text) > COMMAND_TEXT_MAX:
        raise CommandError(f"command longer than {COMMAND_TEXT_MAX} characters")
    if not text.isascii():
        raise CommandError("non-ASCII character in the command")
    toks = text.strip().split()
    if not toks:
        raise CommandError("empty command")
    kind = "AD"
    if toks[0].upper() == "BD":
        kind = "BD"
        toks = toks[1:]
        if not toks:
            raise CommandError("BD needs a command")
    mn = toks[0].upper()
    if mn in DIRECTIVES:
        if kind == "BD":
            raise CommandError("directives are already bypass frames")
        if mn == "UNLOCK":
            if len(toks) != 1:
                raise CommandError("UNLOCK takes no argument")
            return ParsedCommand("UNLOCK", "BC", "UNLOCK", None, {}, False, bc_unlock())
        if len(toks) != 2:
            raise CommandError("usage: SETVR <0-255>")
        vr = _num(toks[1])
        if not 0 <= vr <= 255:
            raise CommandError("V(R) must be 0..255")
        return ParsedCommand(f"SETVR {vr}", "BC", "SETVR", None, {"vr": vr}, False, bc_set_vr(vr))
    c = COMMANDS.get(mn)
    if c is None:
        raise CommandError(f"unknown command '{mn}' (try HELP)")
    if c.builder_only:
        raise CommandError(f"{mn} is built from a sequence file: web Sequences panel / "
                           "cmdparse.seqload_commands")
    if len(toks) - 1 != len(c.args):
        raise CommandError(help_text(mn)[0])
    args, payload = _encode_args(c, toks[1:], _num)
    norm = " ".join([("BD " if kind == "BD" else "") + mn] + [str(a) for a in args.values()])
    return ParsedCommand(norm, kind, mn, c.opcode, args, c.hazardous, payload)


def _encode_args(c: CommandDef, toks: Sequence[str],
                 num: Callable[[str], int]) -> tuple[dict[str, int], bytes]:
    """Range-checks one token per dictionary argument, then applies the command's
    cross-argument check (the flight parse_user_data() rule); returns (values, opcode +
    args). Used by parse() and by the sequence encoder alike."""
    payload = bytearray([c.opcode])
    args: dict[str, int] = {}
    for (name, code, lo, hi, enum), tok in zip(c.args, toks, strict=True):
        v = enum[tok.upper()] if enum and tok.upper() in enum else num(tok)
        if not lo <= v <= hi:
            raise CommandError(f"{name}={v} outside {lo}..{hi}")
        args[name] = v
        payload += struct.pack(">" + code, v)
    if c.check is not None:
        err = c.check(args)
        if err:
            raise CommandError(err)
    return args, bytes(payload)


def tc_packet(cmd: ParsedCommand, seq: int) -> bytes:
    """Wraps opcode+args into a TC Space Packet (no secondary header)."""
    return build_packet(APID_TC, seq, cmd.user_data, None, tc=True)


# --------------------------------------------------------------------------------
# Stored command sequences (contract 24.1 / 24.2)
# --------------------------------------------------------------------------------
SEQ_SLOTS = 32                  # on-board table entries (autonomy.h kSeqSlots)
SEQ_CMD_MAX = 12                # stored command: opcode + arguments, octets (kSeqCmdMax)
SEQ_HORIZON_S = 604_800         # 7 days: relative limit; also the absolute look-ahead limit
SEQLOAD_OPCODE = COMMANDS["SEQLOAD"].opcode     # 0x1D (commands.h OP_SEQLOAD)
SEQCTRL_OPCODE = COMMANDS["SEQCTRL"].opcode     # 0x1E
REBOOT_OPCODE = COMMANDS["REBOOT"].opcode       # 0x0C
MAGCAL_OPCODE = COMMANDS["MAGCAL"].opcode       # 0x20
REBOOT_MAGIC = 0xB007
SEQ_FLAG_RELATIVE = 0x01        # SEQLOAD flags b0
SEQLOAD_ARG_LEN = 19            # u8 slot | u8 flags | u32 t_s | u8 len | u8 cmd[12]
SEQ_TEXT_MAX = 65_536           # characters accepted from a sequence file
_SCLK_MOD = 1 << 32

ArgToken = tuple[str | None, str]                 # (name, value); name None = positional
Encoder = Callable[[str, Sequence[ArgToken]], bytes]

# re.ASCII: \d must not match non-ASCII digits (which int() would accept).
_REL_TIME = re.compile(r"T\+(\d{1,3}):([0-5]\d):([0-5]\d)", re.IGNORECASE | re.ASCII)
_MNEMONIC = re.compile(r"[A-Z][A-Z0-9_]*", re.ASCII)
_ARG_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*", re.ASCII)
_DECIMAL = re.compile(r"[+-]?\d+", re.ASCII)
_HEX = re.compile(r"[+-]?0[xX][0-9A-Fa-f]+", re.ASCII)
_NOT_STORABLE = {
    "SEQLOAD": "SEQLOAD cannot be stored in a sequence (no nesting)",
    "SEQCTRL": "SEQCTRL cannot be stored in a sequence (no nesting)",
    "REBOOT": "REBOOT cannot be stored in a sequence (it would erase the RAM-only table)",
}


class SequenceError(CommandError):
    """A sequence-file error; line_no is 1-based, None for whole-file errors."""

    def __init__(self, line_no: int | None, message: str) -> None:
        super().__init__(f"line {line_no}: {message}" if line_no is not None else message)
        self.line_no = line_no
        self.message = message


@dataclass(frozen=True)
class SequenceEntry:
    line_no: int                     # 1-based line in the sequence file
    source: str                      # the line without its comment, whitespace normalised
    relative: bool                   # True: t_s after SEQCTRL START; False: absolute SCLK
    t_s: int                         # seconds
    mnemonic: str                    # upper case
    args: tuple[ArgToken, ...]       # value tokens as written (names lower case)

    @property
    def time_text(self) -> str:
        if not self.relative:
            return f"SCLK {self.t_s}"
        h, rem = divmod(self.t_s, 3600)
        return f"T+{h:02d}:{rem // 60:02d}:{rem % 60:02d}"


@dataclass(frozen=True)
class SeqLoadArgs:
    slot: int
    relative: bool
    t_s: int
    cmd: bytes                       # opcode + arguments (padding removed)


def _seq_int(tok: str) -> int:
    """Decimal (leading zeros allowed, as in clock-like values) or 0x-hex integer; nothing
    else (int(tok, 0) would also take '1_000', '0o17' and '0b1')."""
    if _DECIMAL.fullmatch(tok):
        return int(tok, 10)
    if _HEX.fullmatch(tok):
        return int(tok, 16)
    raise CommandError(f"'{tok}' is not a number")


def _parse_time(toks: list[str], line_no: int) -> tuple[bool, int, list[str]]:
    head = toks[0]
    if head[:2].upper() == "T+":
        m = _REL_TIME.fullmatch(head)
        if m is None:
            raise SequenceError(line_no, f"bad relative time '{head}' "
                                         "(expected T+HH:MM:SS, minutes and seconds 00-59)")
        t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        if t > SEQ_HORIZON_S:
            raise SequenceError(line_no, f"relative time {head} = {t} s exceeds 7 days "
                                         f"({SEQ_HORIZON_S} s)")
        return True, t, toks[1:]
    if head.upper() == "SCLK":
        if len(toks) < 2:
            raise SequenceError(line_no, "SCLK needs a time in seconds")
        try:
            t = _seq_int(toks[1])
        except CommandError:
            raise SequenceError(line_no, f"bad SCLK time '{toks[1]}'") from None
        if not 0 <= t < _SCLK_MOD:
            raise SequenceError(line_no, f"SCLK time {t} outside 0..{_SCLK_MOD - 1}")
        return False, t, toks[2:]
    raise SequenceError(line_no, f"line must start with T+HH:MM:SS or SCLK <seconds>, "
                                 f"not '{head}'")


def _storable_mnemonic(tok: str, line_no: int) -> str:
    """Upper-case mnemonic, refusing what can never be a stored spacecraft command."""
    mn = tok.upper()
    if not _MNEMONIC.fullmatch(mn):
        raise SequenceError(line_no, f"bad mnemonic '{tok}'")
    if mn == "BD":
        raise SequenceError(line_no, "BD (bypass) is not allowed: stored commands are "
                                     "dispatched on board, not framed")
    if mn in DIRECTIVES:
        raise SequenceError(line_no, f"{mn} is a COP-1 directive, not a stored command")
    if mn in GROUND_DIRECTIVES:
        raise SequenceError(line_no, f"{mn} is a ground-station directive, not a "
                                     "spacecraft command")
    if mn in _NOT_STORABLE:
        raise SequenceError(line_no, _NOT_STORABLE[mn])
    return mn


def _parse_args(toks: list[str], line_no: int) -> tuple[ArgToken, ...]:
    out: list[ArgToken] = []
    seen: set[str] = set()
    for tok in toks:
        if "=" not in tok:
            out.append((None, tok))
            continue
        name, _, value = tok.partition("=")
        if not _ARG_NAME.fullmatch(name) or not value:
            raise SequenceError(line_no, f"bad argument '{tok}' (expected name=value)")
        key = name.lower()
        if key in seen:
            raise SequenceError(line_no, f"argument '{key}' given twice")
        seen.add(key)
        out.append((key, value))
    if seen and len(seen) != len(out):
        raise SequenceError(line_no, "mix of named (name=value) and positional arguments")
    return tuple(out)


def parse_sequence(text: str) -> list[SequenceEntry]:
    """Parses a sequence file into entries (file order = slot order). Raises SequenceError
    with the line number on the first error. Checks syntax, the 7-day relative limit, the
    32-entry table size and the commands that can never be stored; argument values and
    the hazardous policy are checked when the entries are encoded (seqload_commands)."""
    if not isinstance(text, str):
        raise SequenceError(None, "sequence text must be str")
    if len(text) > SEQ_TEXT_MAX:
        raise SequenceError(None, f"sequence text longer than {SEQ_TEXT_MAX} characters")
    entries: list[SequenceEntry] = []
    for line_no, raw in enumerate(text.splitlines(), 1):
        body = raw.split("#", 1)[0].strip()
        if not body:
            continue
        if not body.isascii():
            raise SequenceError(line_no, "non-ASCII character outside a comment")
        toks = body.split()
        relative, t_s, rest = _parse_time(toks, line_no)
        if not rest:
            raise SequenceError(line_no, "missing command mnemonic after the time")
        mn = _storable_mnemonic(rest[0], line_no)
        if len(entries) == SEQ_SLOTS:
            raise SequenceError(line_no, f"more than {SEQ_SLOTS} commands (the on-board "
                                         f"table has {SEQ_SLOTS} slots)")
        entries.append(SequenceEntry(line_no, " ".join(toks), relative, t_s, mn,
                                     _parse_args(rest[1:], line_no)))
    if not entries:
        raise SequenceError(None, "sequence contains no commands")
    return entries


def encode_dictionary(mnemonic: str, args: Sequence[ArgToken]) -> bytes:
    """Default Encoder: opcode + big-endian arguments from the ground dictionary, with the
    same range/enum checks as a live command. Named arguments are matched to the
    dictionary's argument names (case-insensitive)."""
    mn = mnemonic.upper()
    c = COMMANDS.get(mn)
    if c is None:
        raise CommandError(f"unknown command '{mnemonic}'")
    if any(not v.isascii() for _, v in args):
        raise CommandError("non-ASCII argument value")
    names = [a[0] for a in c.args]
    n_named = sum(1 for k, _ in args if k is not None)
    named = {k.lower(): v for k, v in args if k is not None}
    if n_named:
        if n_named != len(args):
            raise CommandError("mix of named (name=value) and positional arguments")
        if len(named) != n_named:
            raise CommandError("an argument is given twice")
        lower = [n.lower() for n in names]
        unknown = sorted(set(named) - set(lower))
        if unknown:
            raise CommandError(f"{mn} has no argument '{unknown[0]}' "
                               f"(arguments: {', '.join(names) or 'none'})")
        missing = [n for n in names if n.lower() not in named]
        if missing:
            raise CommandError(f"{mn}: missing argument(s) {', '.join(missing)}")
        toks = [named[n.lower()] for n in names]
    else:
        toks = [v for _, v in args]
        if len(toks) != len(names):
            raise CommandError(f"{mn} takes {len(names)} argument(s) "
                               f"({', '.join(names) or 'none'}), got {len(toks)}")
    return _encode_args(c, toks, _seq_int)[1]


def seq_forbidden_opcodes() -> frozenset[int]:
    """The flight sequencer's refusal policy (CommandSequencer::opcode_forbidden): every
    hazardous dictionary command plus REBOOT, SEQLOAD and SEQCTRL."""
    ops = {REBOOT_OPCODE, SEQLOAD_OPCODE, SEQCTRL_OPCODE}
    ops.update(c.opcode for c in COMMANDS.values() if c.hazardous)
    return frozenset(ops)


def pack_seqload(slot: int, entry: SequenceEntry, encoder: Encoder = encode_dictionary, *,
                 forbidden: frozenset[int] | None = None,
                 now_sclk_s: int | None = None) -> bytes:
    """The 19-octet SEQLOAD argument block (24.1) for one entry. With now_sclk_s (the
    spacecraft's current SCLK estimate) an absolute time is also checked the way the
    flight will: 1..7 days ahead, modulo 2^32."""
    if isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot < SEQ_SLOTS:
        raise SequenceError(entry.line_no, f"slot {slot!r} outside 0..{SEQ_SLOTS - 1}")
    if entry.relative and not 0 <= entry.t_s <= SEQ_HORIZON_S:
        raise SequenceError(entry.line_no, f"relative time {entry.t_s} s outside "
                                           f"0..{SEQ_HORIZON_S}")
    if not entry.relative and not 0 <= entry.t_s < _SCLK_MOD:
        raise SequenceError(entry.line_no, f"SCLK time {entry.t_s} outside 0..{_SCLK_MOD - 1}")
    if not entry.relative and now_sclk_s is not None:
        if isinstance(now_sclk_s, bool) or not isinstance(now_sclk_s, int) \
                or not 0 <= now_sclk_s < _SCLK_MOD:
            raise SequenceError(None, f"spacecraft SCLK {now_sclk_s!r} outside 0..{_SCLK_MOD - 1}")
        lead = (entry.t_s - now_sclk_s) % _SCLK_MOD
        if lead == 0 or lead >= _SCLK_MOD // 2:
            raise SequenceError(entry.line_no, f"SCLK {entry.t_s} is not in the future "
                                               f"(spacecraft SCLK {now_sclk_s})")
        if lead > SEQ_HORIZON_S:
            raise SequenceError(entry.line_no, f"SCLK {entry.t_s} is {lead} s ahead, more "
                                               f"than 7 days ({SEQ_HORIZON_S} s)")
    try:
        cmd = encoder(entry.mnemonic, entry.args)
    except (ValueError, struct.error) as e:          # CommandError is a ValueError
        raise SequenceError(entry.line_no, f"{entry.mnemonic}: {e}") from None
    if not isinstance(cmd, (bytes, bytearray)):
        raise SequenceError(entry.line_no, f"encoder returned {type(cmd).__name__}, not bytes")
    cmd = bytes(cmd)
    if not 1 <= len(cmd) <= SEQ_CMD_MAX:
        raise SequenceError(entry.line_no, f"{entry.mnemonic} encodes to {len(cmd)} octets; "
                                           f"a stored command holds 1..{SEQ_CMD_MAX}")
    fb = seq_forbidden_opcodes() if forbidden is None else forbidden
    if cmd[0] in fb:
        raise SequenceError(entry.line_no, f"{entry.mnemonic} (opcode 0x{cmd[0]:02X}) cannot "
                                           "be stored: hazardous commands, REBOOT, SEQLOAD "
                                           "and SEQCTRL are refused on board")
    flags = SEQ_FLAG_RELATIVE if entry.relative else 0
    return (struct.pack(">BBIB", slot, flags, entry.t_s, len(cmd))
            + cmd.ljust(SEQ_CMD_MAX, b"\x00"))


def unpack_seqload(block: bytes) -> SeqLoadArgs:
    """Decodes and validates a SEQLOAD argument block (the flight's structural checks)."""
    if len(block) != SEQLOAD_ARG_LEN:
        raise CommandError(f"SEQLOAD block is {len(block)} octets, not {SEQLOAD_ARG_LEN}")
    slot, flags, t_s, n = struct.unpack_from(">BBIB", block)
    if slot >= SEQ_SLOTS:
        raise CommandError(f"SEQLOAD slot {slot} outside 0..{SEQ_SLOTS - 1}")
    if flags & ~SEQ_FLAG_RELATIVE:
        raise CommandError(f"SEQLOAD reserved flag bits set (0x{flags:02X})")
    if not 1 <= n <= SEQ_CMD_MAX:
        raise CommandError(f"SEQLOAD command length {n} outside 1..{SEQ_CMD_MAX}")
    if any(block[7 + n:]):
        raise CommandError("SEQLOAD padding after the command is not zero")
    return SeqLoadArgs(slot, bool(flags & SEQ_FLAG_RELATIVE), t_s, bytes(block[7:7 + n]))


def seqload_commands(entries: Sequence[SequenceEntry], encoder: Encoder = encode_dictionary,
                     *, first_slot: int = 0, forbidden: frozenset[int] | None = None,
                     now_sclk_s: int | None = None) -> list[ParsedCommand]:
    """One SEQLOAD ParsedCommand per entry, slots first_slot, first_slot + 1, ... in file
    order (equal times then run in file order on board). Uplink them in order, then send
    SEQCTRL START as a separate operator action. Each TC packet is 26 octets."""
    if isinstance(first_slot, bool) or not isinstance(first_slot, int) \
            or not 0 <= first_slot < SEQ_SLOTS:
        raise SequenceError(None, f"first slot {first_slot!r} outside 0..{SEQ_SLOTS - 1}")
    if first_slot + len(entries) > SEQ_SLOTS:
        raise SequenceError(None, f"{len(entries)} commands from slot {first_slot} exceed the "
                                  f"{SEQ_SLOTS}-slot table")
    fb = seq_forbidden_opcodes() if forbidden is None else forbidden
    out: list[ParsedCommand] = []
    for i, e in enumerate(entries):
        slot = first_slot + i
        block = pack_seqload(slot, e, encoder, forbidden=fb, now_sclk_s=now_sclk_s)
        n = block[6]
        out.append(ParsedCommand(
            text=f"SEQLOAD slot={slot} {e.source}", kind="AD", mnemonic="SEQLOAD",
            opcode=SEQLOAD_OPCODE,
            args={"slot": slot, "relative": int(e.relative), "t_s": e.t_s, "len": n,
                  "cmd": block[7:7 + n].hex()},
            hazardous=False, user_data=bytes([SEQLOAD_OPCODE]) + block))
    return out


# --------------------------------------------------------------------------------
# The flight's verdict on a TC user-data field (PB-2.1 .. 2.3)
# --------------------------------------------------------------------------------
CE_OK, CE_UNKNOWN_OPCODE, CE_BAD_LENGTH, CE_BAD_ARGUMENT, CE_BAD_MAGIC = 0, 1, 2, 3, 6
# Opcodes whose arguments the flight parse_user_data() checks only for length (19 octets);
# the argument rules run when the command executes (CommandSequencer::load,
# MagCalStore::load) and map to CE_BAD_LENGTH / CE_BAD_ARGUMENT (seq_cmd_error,
# magcal_cmd_error). PB-2.3.
LATE_VALIDATED_OPCODES = frozenset({SEQLOAD_OPCODE, MAGCAL_OPCODE})
_BY_OPCODE: dict[int, CommandDef] = {c.opcode: c for c in COMMANDS.values()}


def _opcode_and_length(user_data: bytes) -> tuple[int, str, CommandDef | None, bytes]:
    """Stage 1 of parse_user_data(): known opcode, exact argument length."""
    if not isinstance(user_data, (bytes, bytearray)) or not user_data:
        return CE_BAD_LENGTH, "empty user data field", None, b""
    op = user_data[0]
    c = _BY_OPCODE.get(op)
    if c is None:
        return CE_UNKNOWN_OPCODE, f"unknown opcode 0x{op:02X}", None, b""
    args = bytes(user_data[1:])
    if len(args) != c.arg_octets:
        return (CE_BAD_LENGTH, f"{c.mnemonic}: {len(args)} argument octets, needs {c.arg_octets}",
                c, args)
    return CE_OK, "", c, args


def _argument_rules(c: CommandDef, args: bytes) -> tuple[int, str]:
    """Stage 2: REBOOT magic, per-argument range, the command's cross-argument check."""
    values = struct.unpack(">" + "".join(a[1] for a in c.args), args)
    if c.opcode == REBOOT_OPCODE and values[0] != REBOOT_MAGIC:
        return CE_BAD_MAGIC, f"REBOOT magic 0x{values[0]:04X} is not 0x{REBOOT_MAGIC:04X}"
    named: dict[str, int] = {}
    for (name, _code, lo, hi, _enum), v in zip(c.args, values, strict=True):
        if not lo <= v <= hi:
            return CE_BAD_ARGUMENT, f"{c.mnemonic}: {name}={v} outside {lo}..{hi}"
        named[name] = v
    if c.check is not None:
        err = c.check(named)
        if err:
            return CE_BAD_ARGUMENT, f"{c.mnemonic}: {err}"
    return CE_OK, ""


def check_user_data(user_data: bytes, *, parse_stage: bool = False) -> tuple[int, str]:
    """(CmdError, reason) the spacecraft returns for a TC user-data field (opcode +
    arguments); (0, "") = accepted. Same order as the flight parse_user_data(): unknown
    opcode 1, argument length 2, REBOOT magic 6, argument range / enum / the command's
    `check` 3. For SEQLOAD and MAGCAL the parse stage checks the length only; parse_stage
    True stops there (exactly parse_user_data(), also the stored-command validator), the
    default adds their execution-stage rules - the error the CMDVER reports. SEQLOAD times
    need the spacecraft SCLK and are checked by pack_seqload(now_sclk_s=...), not here.
    State-dependent refusals (BUSY, HARDWARE, SPECCFG lamp/mode combinations) are not
    predictable from the packet and are not modelled."""
    code, why, c, args = _opcode_and_length(user_data)
    if code != CE_OK or c is None:
        return code, why
    if c.opcode in LATE_VALIDATED_OPCODES and parse_stage:
        return CE_OK, ""
    code, why = _argument_rules(c, args)
    if code != CE_OK or c.opcode != SEQLOAD_OPCODE:
        return code, why
    # SEQLOAD execution stage (CommandSequencer::load): block structure, sequence policy,
    # then the stored command through the parse-stage validator (validate_stored_command).
    try:
        blk = unpack_seqload(args)
    except CommandError as e:
        return CE_BAD_ARGUMENT, str(e)
    if blk.cmd[0] in seq_forbidden_opcodes():
        return CE_BAD_ARGUMENT, (f"SEQLOAD: opcode 0x{blk.cmd[0]:02X} cannot be stored "
                                 "(hazardous, REBOOT, SEQLOAD or SEQCTRL)")
    code, why, inner, inner_args = _opcode_and_length(blk.cmd)
    if code == CE_OK and inner is not None and inner.opcode not in LATE_VALIDATED_OPCODES:
        code, why = _argument_rules(inner, inner_args)
    if code != CE_OK:
        return CE_BAD_ARGUMENT, f"SEQLOAD: stored command refused ({why})"
    return CE_OK, ""
