"""Operator command language -> validated TC Space Packets.

Syntax (case-insensitive, whitespace separated; numbers decimal or 0x-hex):
    NOOP
    MODE ENCOUNTER            PING 1234          AIRRATE 0 600
    BD <command ...>          send as a bypass (Type-BD) frame, ignores FARM state
    UNLOCK                    COP-1 control: clear FARM lockout   (Type-BC)
    SETVR <n>                 COP-1 control: set V(R) = n         (Type-BC)
    HELP [mnemonic]

Every command is checked against the command dictionary (range, enum, arity)
before it can be queued — the same "build / verify / send" discipline used in
real mission operations. Hazardous commands additionally require confirmation.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from .ccsds.packets import build_packet
from .ccsds.tc import bc_set_vr, bc_unlock
from .dictionary import COMMANDS, DIRECTIVES

APID_TC = 0x0C0


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
    try:
        return int(tok, 0)
    except ValueError:
        raise CommandError(f"'{tok}' is not a number") from None


def help_text(mnemonic: str | None = None) -> list[str]:
    if mnemonic:
        c = COMMANDS.get(mnemonic.upper())
        if not c:
            return [f"unknown command {mnemonic}"]
        args = " ".join(f"<{a[0]}>" for a in c.args)
        return [f"{c.mnemonic} {args}".strip(), c.desc + (" [HAZARDOUS]" if c.hazardous else "")]
    return ["commands: " + " ".join(sorted(COMMANDS)),
            "directives: UNLOCK SETVR <n>  prefix BD = bypass"]


def parse(text: str) -> ParsedCommand:
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
    if len(toks) - 1 != len(c.args):
        raise CommandError(help_text(mn)[0])
    payload = bytes([c.opcode])
    args = {}
    for (name, code, lo, hi, enum), tok in zip(c.args, toks[1:]):
        if enum and tok.upper() in enum:
            v = enum[tok.upper()]
        else:
            v = _num(tok)
        if not lo <= v <= hi:
            raise CommandError(f"{name}={v} outside {lo}..{hi}")
        args[name] = v
        payload += struct.pack(">" + code, v)
    norm = " ".join([("BD " if kind == "BD" else "") + mn] + [str(a) for a in args.values()])
    return ParsedCommand(norm, kind, mn, c.opcode, args, c.hazardous, payload)


def tc_packet(cmd: ParsedCommand, seq: int) -> bytes:
    """Wraps opcode+args into a TC Space Packet (no secondary header)."""
    return build_packet(APID_TC, seq, cmd.user_data, None, tc=True)
