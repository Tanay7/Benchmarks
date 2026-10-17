#!/usr/bin/env bash
# Station sketch regression harness (ground UNO Q MCU, dssq-groundstation/sketch/sketch.ino).
#
# Builds the REAL station sketch with the host compiler - against tools/host_test/arduino_stub
# plus the test doubles in include/ (a recording Arduino_RouterBridge, a recording
# Arduino_LED_Matrix, a <zephyr/kernel.h> on real host threads) and runtime.cpp (Arduino core
# on real time, a pin table, a fake SX126x behind the SPI) - and runs it on real threads:
#   test_e22.cpp  default E22 build: CardKB key-test mode (Phase C C51), the Bridge
#                 mailbox (C50 / decision I4): a handler called from another thread is
#                 executed by the loop thread; an unserved request times out and never
#                 runs; pin plan H (D-72); the RCU state in the hello and the LED-matrix
#                 link status (D-78)
#   test_sx.cpp   SX126x build (DSSQ_RADIO_SX126X=1) for the SX1268 and the SX1262,
#                 compiled with the bundled patched RadioLib: rf_rx length bound (C54),
#                 TX-fault dequeue (C25), S| status fields (I3), SX126x pins, time on air,
#                 the matrix uplink row in TX
# 31 checks + 2 x 10 checks = 51. CI runs it under AddressSanitizer + UBSan and under
# ThreadSanitizer (the mailbox is a cross-thread protocol).
#
# Usage: tools/host_test/station/run.sh [OUT_DIR]
#   OUT_DIR   build directory (default: a temporary directory, removed afterwards)
# Environment:
#   SAN       extra compiler / linker flags, e.g.
#               SAN="-fsanitize=address,undefined -fno-sanitize-recover=undefined"
#               SAN=-fsanitize=thread
#   SKETCH    the station sketch to test (default: the repository's)
#   RADIOLIB  a RadioLib copy written by tools/radiolib.py (default: the bundled
#             dssq-groundstation/sketch/libraries/RadioLib, else a fresh fetch)
#   CXX       host C++ compiler (default g++); PYTHON (default python3)
# Exit status: 0 every check passed; 1 a build or a check failed; 2 no verified RadioLib
# copy (the SX126x half cannot be built).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
STUB="$ROOT/tools/host_test/arduino_stub"
CXX="${CXX:-g++}"
PY="${PYTHON:-python3}"
SK="${SKETCH:-$ROOT/dssq-groundstation/sketch/sketch.ino}"
SAN_FLAGS=()
read -r -a SAN_FLAGS <<<"${SAN:-}"
if [[ $# -ge 1 ]]; then
  OUT="$1"
  mkdir -p "$OUT"
else
  OUT="$(mktemp -d)"
  trap 'rm -rf "$OUT"' EXIT
fi
BOARD=(-DARDUINO=10607 -DARDUINO_UNO_Q -DARDUINO_ARCH_ZEPHYR)
# include/ first: its Arduino_RouterBridge.h and zephyr/ replace the syntax-check stubs.
INC=(-I"$HERE/include" -I"$HERE" -I"$STUB")
CXXF=(-std=gnu++17 -O1 -g -Wall -Wextra -Wno-unused-parameter -Werror -pthread "${SAN_FLAGS[@]}" "${BOARD[@]}")

# ---- RadioLib: a copy that tools/radiolib.py verifies (tag 7.8.1 + the project patch) --
RL_DIR="${RADIOLIB:-$ROOT/dssq-groundstation/sketch/libraries/RadioLib}"
if [[ -d "$RL_DIR" ]]; then
  "$PY" "$ROOT/tools/radiolib.py" check "$RL_DIR" >/dev/null \
    || { echo "FAIL $RL_DIR is not a verified RadioLib copy (re-run tools/radiolib.py fetch)"; exit 1; }
else
  RL_DIR="$OUT/RadioLib"
  rc=0
  "$PY" "$ROOT/tools/radiolib.py" fetch "$RL_DIR" >/dev/null || rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "no verified RadioLib copy: tools/radiolib.py fetch exited $rc"
    exit 2
  fi
fi
RL="$RL_DIR/src"

fail=0
run() {   # $1 = label, $2 = binary; prints the binary's check lines
  local label="$1" bin="$2" rc=0
  "$bin" >"$OUT/$label.out" 2>&1 || rc=$?
  cat "$OUT/$label.out"
  if [[ $rc -ne 0 ]]; then echo "FAIL $label exited with status $rc"; fail=1; fi
}
build() {   # $1 = label, rest = compiler arguments
  local label="$1"; shift
  if ! "$CXX" "$@" 2>"$OUT/$label.log"; then
    echo "FAIL $label does not build:"; cat "$OUT/$label.log"; fail=1
    return 1
  fi
}

# ---- E22 build ---------------------------------------------------------------------------
if build e22 "${CXXF[@]}" "${INC[@]}" -DSKETCH="\"$SK\"" "$HERE/test_e22.cpp" "$HERE/runtime.cpp" -o "$OUT/e22"; then
  run e22 "$OUT/e22"
fi

# ---- RadioLib objects (the library is not held to this repository's warning flags) ------
mkdir -p "$OUT/radiolib"
jobs="$(nproc 2>/dev/null || echo 2)"
export CXX OUT RL HERE STUB
export RL_FLAGS="-std=gnu++17 -O1 -g -w -pthread ${SAN:-} ${BOARD[*]}"
# One compiler per translation unit, $jobs at a time; the quoted script is expanded by the
# inner bash (RL_FLAGS deliberately unquoted there: a list of flags).
# shellcheck disable=SC2016
find "$RL" -name '*.cpp' -print0 | LC_ALL=C sort -z | xargs -0 -P "$jobs" -I{} bash -c '
  f="$1"; o="$OUT/radiolib/$(printf "%s" "$f" | md5sum | cut -c1-16).o"
  "$CXX" $RL_FLAGS -I"$HERE/include" -I"$STUB" -I"$RL" -c "$f" -o "$o" \
    || { echo "FAIL RadioLib $f does not build"; exit 255; }' _ {} \
  || { echo "FAIL RadioLib objects"; exit 1; }

# ---- SX126x builds -------------------------------------------------------------------------
for chip in 1268 1262; do
  if build "sx$chip" "${CXXF[@]}" "${INC[@]}" -isystem "$RL" -DDSSQ_RADIO_SX126X=1 -DDSSQ_SX_CHIP=$chip \
       -DSKETCH="\"$SK\"" "$HERE/test_sx.cpp" "$HERE/runtime.cpp" "$OUT"/radiolib/*.o -o "$OUT/sx$chip"; then
    run "sx$chip" "$OUT/sx$chip"
  fi
done

n_ok="$(cat "$OUT"/*.out 2>/dev/null | grep -c '^ok ' || true)"
n_fail="$(cat "$OUT"/*.out 2>/dev/null | grep -c '^FAIL ' || true)"
echo "station harness${SAN:+ [$SAN]}: $n_ok checks passed, $n_fail failed"
if [[ $fail -ne 0 || $n_fail -ne 0 || $n_ok -eq 0 ]]; then exit 1; fi
exit 0
