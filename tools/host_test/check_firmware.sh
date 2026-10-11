#!/usr/bin/env bash
# Host-side compile check of all Arduino firmware in this repository.
#
# Arduino toolchains for the UNO Q / VENTUNO Q (Zephyr) are large; this
# script instead compiles every translation unit with the host g++
# against tools/host_test/arduino_stub (inert Arduino API declarations). It
# catches syntax, type and API-signature errors and reports compiler warnings
# (-Wall -Wextra -Wshadow). It does NOT replace the real build in Arduino App
# Lab / IDE, which you must still run.
#
# Each sketch is compiled with the board macros the arduino:zephyr core passes
# (ARDUINO, ARDUINO_<board>, ARDUINO_ARCH_ZEPHYR), so vendor libraries take the
# same platform branches as in the real build.
#
# Optional: export LIBS_DIR=<dir containing cloned Arduino libraries> to also
# type-check the code paths that use vendor libraries (RadioLib 7.8.1 for the
# SX126x radio option, ...). Library include directories are passed as system
# headers, so only warnings in this repository's code are shown. With RadioLib
# present, the RadioLib sources the SX126x driver links against are type-checked
# against the stub too (proving the stub covers the API they use), and the
# drivers are checked for every radio selection (VGQ_RADIO = 1, 2, and 3 when
# config.h defines the SX126x wiring constants).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STUB="$ROOT/tools/host_test/arduino_stub"
INC=(-I"$STUB")
RADIOLIB=""
if [[ -n "${LIBS_DIR:-}" ]]; then
  for d in "$LIBS_DIR"/*/src; do INC+=(-isystem "$d"); done
  [[ -f "$LIBS_DIR/RadioLib/src/RadioLib.h" ]] && RADIOLIB="$LIBS_DIR/RadioLib/src"
fi
CXX="${CXX:-g++}"
FLAGS=(-std=gnu++17 -fsyntax-only -Wall -Wextra -Wshadow -Wno-unused-parameter)
UNOQ=(-DARDUINO=10607 -DARDUINO_UNO_Q -DARDUINO_ARCH_ZEPHYR)
VENTUNOQ=(-DARDUINO=10607 -DARDUINO_VENTUNO_Q -DARDUINO_ARCH_ZEPHYR)
LOG="$(mktemp)"
trap 'rm -f "$LOG"' EXIT
fail=0
check() {   # $1 = file, $2 = label suffix, rest = extra flags
  local f="$1" tag="$2"; shift 2
  if ! "$CXX" "${FLAGS[@]}" "$@" "${INC[@]}" -x c++ "$f" 2>"$LOG"; then
    echo "FAIL $f$tag"; cat "$LOG"; fail=1
  elif [[ -s "$LOG" ]]; then
    echo "warn $f$tag"; cat "$LOG"
  else
    echo "ok   $f$tag"
  fi
}
SK="$ROOT/spacecraft/vgq1_flight/sketch"
check "$SK/sketch.ino" "" "${UNOQ[@]}"
while IFS= read -r f; do check "$f" "" "${UNOQ[@]}"; done < <(find "$SK/src" -name '*.cpp' | sort)

# Radio drivers for every selectable radio (default build above: E22-400T33D).
DRV="$SK/src/drivers"
check "$DRV/e22.cpp" " [VGQ_RADIO=2]" "${UNOQ[@]}" -DVGQ_RADIO=2
check "$DRV/radio.cpp" " [host, no Arduino]" -DVGQ_HOST_BUILD -I"$SK/src"
if [[ -n "$RADIOLIB" ]]; then
  if grep -q "PIN_SX_NSS" "$SK/src/config.h"; then
    check "$DRV/sx126x.cpp" " [VGQ_RADIO=3]" "${UNOQ[@]}" -DVGQ_RADIO=3
    check "$DRV/sx126x.cpp" " [VGQ_RADIO=3, SX1262]" "${UNOQ[@]}" -DVGQ_RADIO=3 -DVGQ_SX126X_CHIP=1262
  else
    echo "skip $DRV/sx126x.cpp [VGQ_RADIO=3]: config.h does not define the SX126x wiring (PIN_SX_*, kSx*)"
  fi
  # RadioLib's own sources used by the SX126x driver, against the stub (library
  # warnings suppressed: only API coverage is being checked).
  for f in "$RADIOLIB/hal/Arduino/ArduinoHal.cpp" "$RADIOLIB/Hal.cpp" "$RADIOLIB/Module.cpp" \
           "$RADIOLIB/protocols/PhysicalLayer/PhysicalLayer.cpp" "$RADIOLIB"/modules/SX126x/*.cpp \
           "$RADIOLIB"/utils/*.cpp; do
    check "$f" " [RadioLib vs stub]" "${UNOQ[@]}" -w
  done
fi

FP="$ROOT/ground/station_io/sketch/sketch.ino"
[[ -f "$FP" ]] && check "$FP" "" "${VENTUNOQ[@]}"
exit $fail
