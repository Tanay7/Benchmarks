#!/usr/bin/env bash
# Host-side compile check of all Arduino firmware in this repository.
#
# Arduino toolchains for the UNO Q / VENTUNO Q (Zephyr) and GIGA R1 (mbed) are
# large; this script instead compiles every translation unit with the host g++
# against tools/host_test/arduino_stub (inert Arduino API declarations). It
# catches syntax, type and API-signature errors. It does NOT replace the real
# build in Arduino App Lab / IDE, which you must still run.
#
# Optional: export LIBS_DIR=<dir containing cloned Arduino libraries> to also
# type-check code paths that use the vendor libraries (SparkFun AS7265x/AS7343,
# SparkFun Toolkit, Arduino_NiclaSenseEnv, U8g2).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STUB="$ROOT/tools/host_test/arduino_stub"
INC=(-I"$STUB")
if [[ -n "${LIBS_DIR:-}" ]]; then
  for d in "$LIBS_DIR"/*/src; do INC+=(-I"$d"); done
fi
CXX="${CXX:-g++}"
FLAGS=(-std=gnu++17 -fsyntax-only -Wall -Wextra -Wno-unused-parameter)
fail=0
check() {   # $1 = file, rest = extra flags
  local f="$1"; shift
  if ! "$CXX" "${FLAGS[@]}" "$@" "${INC[@]}" -x c++ "$f" 2>/tmp/fwcheck.$$; then
    echo "FAIL $f"; cat /tmp/fwcheck.$$; fail=1
  else
    echo "ok   $f"
  fi
}
SK="$ROOT/spacecraft/vgq1_flight/sketch"
check "$SK/sketch.ino"
while IFS= read -r f; do check "$f"; done < <(find "$SK/src" -name '*.cpp' | sort)
check "$SK/src/fsw/status_display.cpp" -DVGQ_SC_OLED=1
FP="$ROOT/ground/frontpanel/dssq_frontpanel/dssq_frontpanel.ino"
[[ -f "$FP" ]] && check "$FP" -DFP_HOST_CHECK=1
rm -f /tmp/fwcheck.$$
exit $fail
