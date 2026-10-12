#!/usr/bin/env python3
"""Will the board's loader accept this sketch? Undefined-symbol check of an LLEXT sketch.

arduino:zephyr builds a sketch as a Zephyr loadable extension (LLEXT, link mode
"dynamic"): a partially linked ELF whose undefined symbols the loader firmware resolves
when it loads the sketch. Zephyr's loader refuses the whole sketch if one of them is not
in its export table, weak references included ("Undefined symbol with no entry in symbol
table", llext_link.c). The core's check link (syms-dynamic.ld) catches most of these at
build time, but not all: a weak reference such as the C++ TLS init function
_ZTH13z_tls_current that k_current_get() produces on cores up to 0.90.0 (built without
-fno-extern-tls-init) links fine and fails only on the board.

This tool takes two arduino-cli builds made with the same core, the sketch and a
baseline sketch that uses nothing but the core (setup/loop, pinMode, digitalWrite), each
with its VERBOSE compile log (arduino-cli compile -v), and requires that every undefined
symbol of the sketch's final ELF is
  * exported by the loader: PROVIDE(<name> = ...) in the variant's syms-dynamic.ld, or
  * also undefined in the baseline (resolved for every sketch by the core's own
    loading: heap and section bounds, kheap_llext_heap, ...).
The toolchain's nm and the variant folder are located through the compile log.

Usage:
  llext_check.py --log BUILD.log --build DIR --baseline-log BASE.log --baseline-build DIR
Exit status: 0 every symbol resolvable; 1 unresolved symbols (listed); 2 usage or
environment error (log, ELF, nm or syms-dynamic.ld not found).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

NM_RE = re.compile(r"(\S*?)arm-zephyr-eabi-g\+\+(?=\s)")
VARIANT_RE = re.compile(r"-imacros\"?(\S+?)/llext-edk/")
PROVIDE_RE = re.compile(r"^PROVIDE\(\s*([A-Za-z_.$][A-Za-z0-9_.$]*)\s*=", re.MULTILINE)


class CheckError(Exception):
    """Usage or environment error (exit status 2)."""


def tools_from_log(log: Path) -> tuple[Path, Path]:
    """(nm, syms-dynamic.ld) of the toolchain and variant named in a verbose compile log."""
    try:
        text = log.read_text(errors="replace")
    except OSError as e:
        raise CheckError(f"cannot read {log}: {e}") from e
    m = NM_RE.search(text)
    if not m:
        raise CheckError(f"{log}: no arm-zephyr-eabi-g++ command line (not a verbose arduino:zephyr log?)")
    nm = Path(m.group(1) + "arm-zephyr-eabi-nm")
    v = VARIANT_RE.search(text)
    if not v:
        raise CheckError(f"{log}: no -imacros<variant>/llext-edk/ flag (not a verbose arduino:zephyr log?)")
    syms = Path(v.group(1)) / "syms-dynamic.ld"
    for p in (nm, syms):
        if not p.is_file():
            raise CheckError(f"{p} not found (named in {log})")
    return nm, syms


def final_elf(build: Path) -> Path:
    """The final sketch ELF of an arduino-cli build directory (<name>.ino.elf)."""
    elfs = list(build.glob("*.ino.elf"))
    if len(elfs) != 1:
        raise CheckError(f"{build}: expected one *.ino.elf, found {len(elfs)} (arduino:zephyr build directory?)")
    return elfs[0]


def undefined(nm: Path, elf: Path) -> set[str]:
    """Undefined symbols of an ELF, strong (U) and weak (w / v)."""
    try:
        out = subprocess.run([str(nm), "-u", str(elf)], check=True, capture_output=True, text=True).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        raise CheckError(f"{nm} -u {elf} failed: {e}") from e
    return {line.split()[-1] for line in out.splitlines() if line.strip()}


def check(log: Path, build: Path, base_log: Path, base_build: Path) -> int:
    nm, syms = tools_from_log(log)
    base_nm, base_syms = tools_from_log(base_log)
    if syms.resolve() != base_syms.resolve():
        raise CheckError(f"the sketch ({syms}) and the baseline ({base_syms}) were built for different variants")
    exported = set(PROVIDE_RE.findall(syms.read_text(errors="replace")))
    elf = final_elf(build)
    sketch_undef = undefined(nm, elf)
    base_undef = undefined(base_nm, final_elf(base_build))
    unresolved = sorted(sketch_undef - exported - base_undef)
    n_exp = len(sketch_undef & exported)
    n_base = len((sketch_undef - exported) & base_undef)
    print(f"{elf.name}: {len(sketch_undef)} undefined symbols - {n_exp} exported by the loader "
          f"({syms}), {n_base} also undefined in the baseline sketch, {len(unresolved)} unresolved")
    for s in unresolved:
        hint = " (C++ TLS init function of an extern thread_local)" if s.startswith("_ZTH") else ""
        print(f"  unresolved: {s}{hint}")
    return 1 if unresolved else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--log", type=Path, required=True, help="verbose compile log of the sketch")
    ap.add_argument("--build", type=Path, required=True, help="arduino-cli build directory of the sketch")
    ap.add_argument("--baseline-log", type=Path, required=True, help="verbose compile log of the baseline sketch")
    ap.add_argument("--baseline-build", type=Path, required=True, help="build directory of the baseline sketch")
    a = ap.parse_args(argv)
    try:
        return check(a.log, a.build, a.baseline_log, a.baseline_build)
    except CheckError as e:
        print(f"llext_check: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
