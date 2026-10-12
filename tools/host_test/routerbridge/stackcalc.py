#!/usr/bin/env python3
"""Worst-case stack of the Arduino_RouterBridge "bridge" thread (contract 41, patch hunk 1).

The bundled patched Arduino_RouterBridge 0.4.3 gives the thread that polls
BridgeClass::update() a 1536-octet stack (stock: 500). This tool recomputes the worst
case from a real build and checks that it fits:

  worst case = deepest call chain from the thread function updateEntryPoint()
             + the frame of Zephyr's z_thread_entry() that calls it
             + the FP exception frame (FP_EXCEPTION_FRAME, an interrupt with lazy FP
               stacking on the Cortex-M33 / M33-class cores, CONFIG_FPU_SHARING)
             + the TLS block Zephyr carves from the top of every thread stack
               (__tdata_size + __tbss_size of the core image)

Inputs:
  * an arduino-cli build directory compiled with
      --build-property "compiler.cpp.extra_flags=-fstack-usage -fcallgraph-info=su"
    (every sketch, library and core translation unit then has a GCC .ci call graph with
    exact frame sizes), and its VERBOSE compile log (arduino-cli compile -v), from which
    the core image and objdump are located: <platform>/firmwares/zephyr-<variant>.elf
    (the arduino:zephyr package ships it) and the toolchain's arm-zephyr-eabi-objdump;
  * the core image's functions (kernel, libc, UART driver) are sized from their
    disassembly (push / vpush / stmdb / sub sp).
Indirect calls are resolved by explicit rules (the serial transport, RPClite's function
wrappers, std::function invokers, the sketches' rpc_* shims, the STM32 UART driver).

--cut REGEX never follows a call into a matching function: paths that cannot run on the
bridge thread. CI uses
  egse_              the flight's EGSE services: a Router Bridge shim runs them only on the
                     loop thread (Flight::rpc_* checks on_loop_thread(); on any other thread
                     it posts to the RPC mailbox and waits)
  z_log_msg_runtime  Zephyr's LOG_ERR message construction (a 1056-octet frame), reached
                     only from heap-corruption error paths, after memory is already corrupt

--check BRIDGE_H: the result must fit UPDATE_THREAD_STACK_SIZE of that bridge.h, with no
variable-sized frame and no unresolved indirect call on an explored path, and no unknown
function other than the fatal C++ error paths (UNRESOLVED_OK). Exit status: 0 fits (or no
--check); 1 does not fit / not proven; 2 usage or input error.

usage: stackcalc.py [--log COMPILE_LOG | --elf ELF --objdump OBJDUMP] [--cut REGEX]...
                    [--check BRIDGE_H] BUILD_DIR [ROOT_SYMBOL...]
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

ROOT_DEFAULT = "_Z16updateEntryPointPvS_S_"      # void updateEntryPoint(void*, void*, void*)
THREAD_ENTRY = "z_thread_entry"
# Exception frame pushed when an interrupt preempts a thread with an active FP context:
# 8 basic words + S0-S15 + FPSCR + 1 reserved word = 26 words = 104 octets, plus up to 4
# octets of 8-octet alignment padding (Armv8-M ARM, B3.19 "Exception entry, context
# stacking").
FP_EXCEPTION_FRAME = 104 + 4
# Library functions outside the core image that only end the program (allocation failure,
# an empty std::function called): a thread never returns from them.
UNRESOLVED_OK = frozenset({"_ZSt17__throw_bad_allocv", "_ZSt20__throw_length_errorPKc",
                           "_ZSt25__throw_bad_function_callv"})

_NODE = re.compile(r'node: \{ title: "([^"]*)" label: "([^"]*)"')
_EDGE = re.compile(r'edge: \{ sourcename: "([^"]*)" targetname: "([^"]*)"')
_FUNC = re.compile(r"^[0-9a-f]+ <([^>]+)>:$")
_STACK_SIZE = re.compile(r"^#define\s+UPDATE_THREAD_STACK_SIZE\s+(\d+)\b", re.M)

Chain = tuple[int, list[str]]                     # (octets, description of each frame)


def _bare(title: str) -> str:
    """The symbol of a .ci node title ("<file>:<symbol>" for local functions)."""
    return title.rsplit(":", 1)[-1] if "/" in title else title


def _count_regs(reg_list: str) -> int:
    n = 0
    for r in reg_list.split(","):
        r = r.strip()
        if "-" in r:
            a, b = r.split("-")
            n += int(b.strip()[1:]) - int(a.strip()[1:]) + 1
        elif r:
            n += 1
    return n


class StackGraph:
    """The call graph of one build: GCC .ci files plus the core image disassembly."""

    def __init__(self, build: Path, elf: Path, objdump: str, cuts: Iterable[str]) -> None:
        self.cuts = [re.compile(c) for c in cuts]
        self.frames: dict[str, int] = {}
        self.qual: dict[str, str] = {}
        self.labels: dict[str, str] = {}
        self.edges: dict[str, set[str]] = {}
        self.by_sym: dict[str, list[str]] = {}
        for ci in sorted(build.rglob("*.ci")):
            text = ci.read_text(errors="replace")
            for title, label in _NODE.findall(text):
                m = re.search(r"\\n(\d+) bytes \((\w+)", label)
                if not m:
                    continue
                self.frames[title] = int(m.group(1))
                self.qual[title] = m.group(2)
                self.labels[title] = label.split("\\n")[0]
                self.by_sym.setdefault(_bare(title), []).append(title)
            for s, d in _EDGE.findall(text):
                self.edges.setdefault(s, set()).add(d)
        if not self.frames:
            raise ValueError(f"{build}: no .ci call graphs (build with -fstack-usage -fcallgraph-info=su)")
        # ---- core image: frames and direct calls from the disassembly -------------------
        dis = subprocess.run([objdump, "-d", "--no-show-raw-insn", str(elf)], capture_output=True, text=True,
                             check=True).stdout
        self.zf: dict[str, int] = {}
        self.zcalls: dict[str, set[str]] = {}
        self.zind: dict[str, int] = {}
        self.zdyn: set[str] = set()
        cur: str | None = None
        for line in dis.splitlines():
            m = _FUNC.match(line)
            if m:
                cur = m.group(1)
                self.zf.setdefault(cur, 0)
                self.zcalls.setdefault(cur, set())
                continue
            if cur is None or "\t" not in line:
                continue
            ins = line.split("\t", 1)[1].strip()
            op = ins.split(None, 1)[0] if ins else ""
            args = ins.split(None, 1)[1] if " " in ins or "\t" in ins else ""
            regs = re.search(r"\{([^}]*)\}", args)
            if op in ("push", "push.w", "stmdb") and ("sp!" in args or op.startswith("push")):
                if regs:
                    self.zf[cur] += 4 * _count_regs(regs.group(1))
            elif op in ("vpush", "vpush.64", "vstmdb"):
                if regs:
                    self.zf[cur] += (8 if "d" in regs.group(1) else 4) * _count_regs(regs.group(1))
            elif op in ("sub", "sub.w", "subw", "subs") and args.startswith("sp,"):
                m2 = re.search(r"#(\d+)", args)
                if m2:
                    self.zf[cur] += int(m2.group(1))
                else:
                    self.zdyn.add(cur)
            elif op in ("bl", "blx", "b.w", "b") and "<" in args:
                t = re.search(r"<([^>+]+)(\+0x[0-9a-f]+)?>", args)
                if t and not t.group(2) and t.group(1) != cur:
                    self.zcalls[cur].add(t.group(1))
            elif op == "blx":
                self.zind[cur] = self.zind.get(cur, 0) + 1
        self.unresolved: set[str] = set()
        self.indirect_unresolved: set[str] = set()
        self.recursion: set[str] = set()
        self._zmemo: dict[str, Chain] = {}
        self._zvisit: set[str] = set()
        self._memo: dict[str, Chain] = {}
        self._stack: list[str] = []

    def _is_cut(self, sym: str) -> bool:
        return any(c.search(sym) for c in self.cuts)

    def _syms(self, pattern: str) -> list[str]:
        return [k for sym, ks in self.by_sym.items() if re.match(pattern, sym) for k in ks]

    def _indirect_targets(self, s: str) -> list[str]:
        """Possible targets of the indirect calls in function `s` (sketch side)."""
        out: list[str] = []
        if "SerialTransport" in s or "RpcDecoder" in s or "get_rpc" in s or "update" in s:
            out += self._syms(r"_ZN15SerialTransport")
            out += self._syms(r"_ZN7arduino12ZephyrSerial(9available|4read|5write)")
        if "process_request" in s or "update" in s or "Dispatcher" in s:
            out += self._syms(r"_ZN18RpcFunctionWrapper.*clE")
        if "RpcFunctionWrapper" in s:
            out += [k for sym, ks in self.by_sym.items() if "_M_invoke" in sym for k in ks]
        if "_M_invoke" in s:
            out += self._syms(r"_ZL?\d*rpc_")
        if "ZephyrSerial" in s:
            out += ["Z:" + f for f in self.zf if re.search(r"^uart_stm32_(irq|fifo|poll)", f)]
        return out

    def _worst_z(self, sym: str) -> Chain:
        if sym in self._zmemo:
            return self._zmemo[sym]
        if sym not in self.zf:
            self.unresolved.add(sym)
            return (0, [sym + " (?)"])
        if sym in self._zvisit:
            self.recursion.add(sym)
            return (0, ["<recursion " + sym + ">"])
        self._zvisit.add(sym)
        best: Chain = (0, [])
        for c in self.zcalls.get(sym, ()):
            if self._is_cut(c):
                continue
            r = self._worst_z(c)
            if r[0] > best[0]:
                best = r
        self._zvisit.discard(sym)
        if sym in self.zind:
            self.indirect_unresolved.add(sym)
        out = (self.zf[sym] + best[0], [f"{sym} [{self.zf[sym]}]", *best[1]])
        self._zmemo[sym] = out
        return out

    def _resolve(self, target: str) -> list[str]:
        if target.startswith("Z:"):
            return [target]
        if target == "__indirect_call":
            return []
        if target in self.frames:
            return [target]
        b = _bare(target)
        for alt in (b, b.replace("C1E", "C2E", 1), b.replace("D1E", "D2E", 1), b.replace("D0E", "D2E", 1)):
            ks = self.by_sym.get(alt, [])
            if ks:
                return ks
        return ["Z:" + b]

    def worst(self, key: str) -> Chain:
        """(octets, frames) of the deepest chain from node `key` ("Z:<sym>" = core image)."""
        if key.startswith("Z:"):
            return self._worst_z(key[2:])
        if key in self._memo:
            return self._memo[key]
        if key in self._stack:
            return (0, ["<recursion " + self.labels.get(key, key) + ">"])
        self._stack.append(key)
        cands: list[str] = []
        for t in self.edges.get(key, ()):
            cands += self._indirect_targets(_bare(key)) if t == "__indirect_call" else self._resolve(t)
        best: Chain = (0, [])
        for c in cands:
            if self._is_cut(_bare(c[2:] if c.startswith("Z:") else c)):
                continue
            r = self.worst(c)
            if r[0] > best[0]:
                best = r
        self._stack.pop()
        f = self.frames.get(key, 0)
        out = (f + best[0], [f"{self.labels.get(key, key)} [{f} {self.qual.get(key, '?')}]", *best[1]])
        self._memo[key] = out
        return out

    def root(self, sym: str) -> Chain:
        keys = self.by_sym.get(sym, []) or ["Z:" + sym]
        return max((self.worst(k) for k in keys), key=lambda r: r[0])

    def explored_dynamic(self) -> list[str]:
        return sorted(self.zdyn & set(self._zmemo))


def tls_octets(elf: Path, objdump: str) -> int:
    """__tdata_size + __tbss_size of the core image (the per-thread TLS block)."""
    syms = subprocess.run([objdump, "-t", str(elf)], capture_output=True, text=True, check=True).stdout
    total = 0
    for name in ("__tdata_size", "__tbss_size"):
        m = re.search(r"^([0-9a-f]+)\s.*\s" + name + r"$", syms, re.M)
        if not m:
            raise ValueError(f"{elf}: symbol {name} not found")
        total += int(m.group(1), 16)
    return total


def tools_from_log(log: Path) -> tuple[Path, str]:
    """(core image ELF, objdump) of the build whose verbose arduino-cli log is `log`."""
    text = log.read_text(errors="replace")
    m = re.search(r"^Using core '[^']*' from platform in folder: (.+?)\s*$", text, re.M)
    v = re.search(r"/variants/([A-Za-z0-9_]+)", text)
    g = re.search(r"(\S*/)arm-zephyr-eabi-g\+\+\s", text)
    if not (m and v and g):
        raise ValueError(f"{log}: not a verbose arduino:zephyr compile log (arduino-cli compile -v)")
    elf = Path(m.group(1)) / "firmwares" / f"zephyr-{v.group(1)}.elf"
    if not elf.is_file():
        raise ValueError(f"{elf}: core image not found")
    return elf, g.group(1) + "arm-zephyr-eabi-objdump"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("build", type=Path, help="arduino-cli build directory with .ci call graphs")
    ap.add_argument("roots", nargs="*", default=[ROOT_DEFAULT], help=f"root symbols (default {ROOT_DEFAULT})")
    ap.add_argument("--log", type=Path, help="verbose compile log: locates the core image and objdump")
    ap.add_argument("--elf", type=Path, help="core image (zephyr-<variant>.elf)")
    ap.add_argument("--objdump", help="arm-zephyr-eabi-objdump")
    ap.add_argument("--cut", action="append", default=[], metavar="REGEX", help="never follow into these")
    ap.add_argument("--check", type=Path, metavar="BRIDGE_H", help="require a fit in its UPDATE_THREAD_STACK_SIZE")
    a = ap.parse_args(argv)
    try:
        if a.log:
            elf, objdump = tools_from_log(a.log)
        elif a.elf and a.objdump:
            elf, objdump = a.elf, a.objdump
        else:
            ap.error("give --log, or --elf and --objdump")
        graph = StackGraph(a.build, elf, objdump, a.cut)
        results = {r: graph.root(r) for r in a.roots}
        tls = tls_octets(elf, objdump) if a.check else 0
        limit = 0
        if a.check:
            m = _STACK_SIZE.search(a.check.read_text())
            if not m:
                raise ValueError(f"{a.check}: no #define UPDATE_THREAD_STACK_SIZE")
            limit = int(m.group(1))
    except (OSError, ValueError, subprocess.CalledProcessError) as e:
        print(f"stackcalc: {e}", file=sys.stderr)
        return 2
    print(f"core image {elf}")
    for root, (total, path) in results.items():
        print(f"== {root}: {total} octets")
        for p in path:
            print("   ", p)
    dynamic = graph.explored_dynamic()
    unknown = sorted(graph.unresolved - UNRESOLVED_OK)
    print("core functions with a variable sub sp on explored paths:", dynamic)
    print("recursion cut in the core graph at:", sorted(graph.recursion))
    print("core functions with unresolved indirect calls on explored paths:", sorted(graph.indirect_unresolved))
    print("unresolved symbols:", sorted(graph.unresolved))
    if not a.check:
        return 0
    entry = graph.zf.get(THREAD_ENTRY)
    if entry is None:
        print(f"FAIL {THREAD_ENTRY} not in the core image")
        return 1
    chain = max(total for total, _ in results.values())
    worst = chain + entry + FP_EXCEPTION_FRAME + tls
    print(f"worst case = chain {chain} + {THREAD_ENTRY} {entry} + FP exception frame {FP_EXCEPTION_FRAME} "
          f"+ TLS {tls} = {worst} octets; UPDATE_THREAD_STACK_SIZE {limit}: margin {limit - worst} octets")
    problems = []
    if dynamic:
        problems.append(f"variable-sized frames on explored paths: {dynamic}")
    if graph.indirect_unresolved:
        problems.append(f"unresolved indirect calls: {sorted(graph.indirect_unresolved)}")
    if unknown:
        problems.append(f"unknown functions: {unknown}")
    if worst > limit:
        problems.append(f"{worst} octets do not fit the {limit}-octet stack")
    for p in problems:
        print("FAIL", p)
    if not problems:
        print(f"ok   the bridge thread's worst case fits its {limit}-octet stack")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
