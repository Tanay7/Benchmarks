#!/usr/bin/env bash
# Host test of the bundled patched Arduino_RouterBridge 0.4.3 (contract 41; hunk 2 of
# tools/arduino_routerbridge-0.4.3.patch): BridgeClass::update(), the library's "bridge"
# thread, must never execute a method bound with provide_safe() ("__safe__"), and
# update_safe(), called by the core's __loopHook on the loop thread, must execute each
# exactly once (bridge_safe_test.cpp, 12 checks). The test compiles the REAL bridge.h and
# the real Arduino_RPClite 0.3.1, MsgPack 0.4.2, DebugLog, ArxContainer and ArxTypeTraits
# sources; only Zephyr and the serial port are stubbed (stub/).
#
# Those platform libraries come with the arduino:zephyr platform: arduino-cli installs them
# (the platform's libraryDependencies) when it first compiles a sketch profile, so the
# script reads their folders from the "Using library <name> at version <v> in folder:
# <dir>" lines of a VERBOSE compile log of either sketch (arduino-cli compile -v).
#
# Negative control: the stock 0.4.3 tag (tools/routerbridge.py fetch --unpatched, needs
# github.com) must FAIL exactly the 4 checks the patch fixes - the proof that the test
# sees the defect. Without network access the control is reported as skipped.
#
# Usage: tools/host_test/routerbridge/run.sh COMPILE_LOG [BRIDGE_DIR]
#   COMPILE_LOG  verbose arduino-cli compile log of vgq1-spacecraft/sketch or
#                dssq-groundstation/sketch
#   BRIDGE_DIR   the copy under test (default: the one the log names, which must be a
#                bundled copy that tools/routerbridge.py check accepts)
# Environment: SAN (e.g. "-fsanitize=address,undefined -fno-sanitize-recover=undefined";
# RPClite's bind() allocates its wrappers once and never frees them, by design on the
# MCU, so LeakSanitizer is switched off), CXX (default g++), PYTHON (default python3).
# Exit status: 0 pass (and the control failed as expected, or was skipped); 1 failure;
# 2 usage error or a library missing from the log.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
CXX="${CXX:-g++}"
PY="${PYTHON:-python3}"
LOG_IN="${1:?usage: run.sh COMPILE_LOG [BRIDGE_DIR]}"
[[ -f "$LOG_IN" ]] || { echo "no such compile log: $LOG_IN"; exit 2; }
SAN_FLAGS=()
read -r -a SAN_FLAGS <<<"${SAN:-}"
export ASAN_OPTIONS="${ASAN_OPTIONS:-detect_leaks=0}"
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT

lib_dir() {   # $1 = library name -> its folder from the compile log (empty if absent)
  sed -n "s/^Using library $1 at version [^ ]* in folder: \(.*[^ ]\) *\$/\1/p" "$LOG_IN" | head -n 1
}
lib_version() {   # $1 = library name -> its version from the compile log
  sed -n "s/^Using library $1 at version \([^ ]*\) in folder: .*/\1/p" "$LOG_IN" | head -n 1
}
INC=(-I"$HERE/stub")
for name in Arduino_RPClite MsgPack DebugLog ArxContainer ArxTypeTraits; do
  dir="$(lib_dir "$name")"
  if [[ -z "$dir" || ! -d "$dir" ]]; then
    echo "FAIL $name: no 'Using library $name ... in folder:' line with an existing folder in $LOG_IN"
    exit 2
  fi
  echo "     $name $(lib_version "$name"): $dir"
  if [[ -d "$dir/src" ]]; then INC+=(-isystem "$dir/src"); else INC+=(-isystem "$dir"); fi
done
# The patch was audited against these releases (patch header); another version needs a
# new audit, so the test refuses it instead of passing on code nobody reviewed.
for pin in Arduino_RPClite:0.3.1 MsgPack:0.4.2; do
  if [[ "$(lib_version "${pin%%:*}")" != "${pin#*:}" ]]; then
    echo "FAIL ${pin%%:*} $(lib_version "${pin%%:*}") is not the audited ${pin#*:}: re-audit the patch"
    exit 1
  fi
done

BRIDGE="${2:-$(lib_dir Arduino_RouterBridge)}"
if ! "$PY" "$ROOT/tools/routerbridge.py" check "$BRIDGE" >"$OUT/check.log" 2>&1; then
  echo "FAIL $BRIDGE is not the bundled patched copy:"; cat "$OUT/check.log"
  exit 1
fi
# -Wno-maybe-uninitialized: Arduino_RPClite 0.3.1 request.h:43-65 copies req_id, which a
# NOTIFY message never sets, into msg_id (unused for notifications); GCC reports it in the
# library code inlined here. The library is used as the platform ships it.
CXXF=(-std=gnu++17 -O1 -g -Wall -Wextra -Wshadow -Werror -Wno-maybe-uninitialized "${SAN_FLAGS[@]}")

build_and_run() {   # $1 = label, $2 = Arduino_RouterBridge folder; sets RC and prints the checks
  local label="$1" lib="$2"
  if ! "$CXX" "${CXXF[@]}" "${INC[@]}" -isystem "$lib/src" "$HERE/bridge_safe_test.cpp" -o "$OUT/t_$label" \
       2>"$OUT/$label.log"; then
    echo "FAIL bridge_safe_test.cpp does not build against $lib:"; cat "$OUT/$label.log"
    exit 1
  fi
  RC=0
  "$OUT/t_$label" >"$OUT/$label.out" 2>&1 || RC=$?
  cat "$OUT/$label.out"
}

echo "== patched copy $BRIDGE${SAN:+ [$SAN]}"
build_and_run patched "$BRIDGE"
if [[ $RC -ne 0 ]] || ! grep -q "^UPDATE_THREAD_STACK_SIZE = 1536$" "$OUT/patched.out"; then
  echo "FAIL the bundled patched Arduino_RouterBridge"
  exit 1
fi

echo "== negative control: stock tag 0.4.3"
rc=0
"$PY" "$ROOT/tools/routerbridge.py" fetch --unpatched "$OUT/stock/Arduino_RouterBridge" >"$OUT/fetch.log" 2>&1 || rc=$?
if [[ $rc -eq 3 ]]; then
  echo "skip negative control: upstream not reachable"; cat "$OUT/fetch.log"
  exit 0
elif [[ $rc -ne 0 ]]; then
  echo "FAIL tools/routerbridge.py fetch --unpatched, exit $rc"; cat "$OUT/fetch.log"
  exit 1
fi
build_and_run stock "$OUT/stock/Arduino_RouterBridge"
n_fail="$(grep -c '^FAIL ' "$OUT/stock.out" || true)"
if [[ $RC -eq 1 && $n_fail -eq 4 ]]; then
  echo "ok   negative control: the stock tag fails the 4 checks the patch fixes"
  exit 0
fi
echo "FAIL negative control: the stock tag gave exit $RC with $n_fail failed checks (expected 1 and 4)"
exit 1
