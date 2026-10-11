#!/usr/bin/env bash
# Host-side compile check of all Arduino firmware in this repository.
#
# Arduino toolchains for the UNO Q / VENTUNO Q (Zephyr) are large; this
# script instead compiles every translation unit with the host g++
# against tools/host_test/arduino_stub (Arduino API declarations as the
# arduino:zephyr core exposes them). It catches syntax, type and API-signature
# errors and reports compiler warnings (-Wall -Wextra -Wshadow). It does NOT
# replace the real build (App Lab / arduino-cli, the CI arduino-build job): .ino
# files are compiled as plain C++, without arduino-cli's prototype generation.
#
# Each sketch is compiled with the board macros the arduino:zephyr core passes
# (ARDUINO, ARDUINO_<board>, ARDUINO_ARCH_ZEPHYR), so vendor libraries take the
# same platform branches as in the real build.
#
# RadioLib (SX126x radio option of both sketches) is checked as the App Lab
# packages ship it: tag 7.8.1 + tools/radiolib-7.8.1.patch, produced and verified
# by tools/radiolib.py. The first copy that `radiolib.py check` accepts is used:
# $LIBS_DIR/RadioLib, then a sketch's libraries/RadioLib; otherwise a fresh copy
# is fetched into a temporary directory (needs github.com). A bundled copy in a
# sketch that does not verify is a failure (stale: re-run radiolib.py fetch). With
# the patched copy:
#   * every RadioLib translation unit (arduino-cli compiles them all), UNO Q and
#     VENTUNO Q, -Wall -Wextra (the library is not held to -Wshadow);
#   * flight drivers/sx126x.cpp with VGQ_RADIO=3 for the SX1268 and the SX1262;
#     while config.h does not define the SX126x wiring yet, the contract 1.1 pins
#     and the default module constants are supplied by a prelude;
#   * the station sketch with DSSQ_RADIO_SX126X=1, VENTUNO Q, both chips.
# Negative control: the stock 7.8.1 tag (fetched with --unpatched, or
# $LIBS_DIR/RadioLib when that is the stock tag) must FAIL to compile its
# ArduinoHal.cpp for the VENTUNO Q with the core's PinMode / PinStatus errors -
# the proof that the stub is strict enough to see what the patch fixes.
# Without network access the RadioLib checks are reported as skipped.
#
# Optional: export LIBS_DIR=<dir containing Arduino libraries> to also
# type-check code paths that use other vendor libraries; their include
# directories are passed as system headers, so only warnings in this
# repository's code are shown. PYTHON selects the interpreter (default python3).
# Exit status: 0 = no failure, 1 = at least one FAIL.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STUB="$ROOT/tools/host_test/arduino_stub"
TOOL="$ROOT/tools/radiolib.py"
PY="${PYTHON:-python3}"
INC=(-I"$STUB")
CXX="${CXX:-g++}"
FLAGS=(-std=gnu++17 -fsyntax-only -Wall -Wextra -Wshadow -Wno-unused-parameter)
LIBFLAGS=(-std=gnu++17 -fsyntax-only -Wall -Wextra)
UNOQ=(-DARDUINO=10607 -DARDUINO_UNO_Q -DARDUINO_ARCH_ZEPHYR)
VENTUNOQ=(-DARDUINO=10607 -DARDUINO_VENTUNO_Q -DARDUINO_ARCH_ZEPHYR)
SK="$ROOT/spacecraft/vgq1_flight/sketch"
FP="$ROOT/ground/station_io/sketch/sketch.ino"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
LOG="$TMP/log"
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

# Every .cpp of a RadioLib src/ tree, in parallel batches; one summary line per board.
check_library() {   # $1 = RadioLib src dir, $2 = board label, rest = board flags
  local src="$1" label="$2"; shift 2
  local out="$TMP/lib-${label// /_}" jobs files i n_ok=0
  jobs="$(nproc 2>/dev/null || echo 2)"
  mkdir -p "$out"
  mapfile -t files < <(find "$src" -name '*.cpp' | LC_ALL=C sort)
  for ((i = 0; i < ${#files[@]}; i++)); do
    ( if "$CXX" "${LIBFLAGS[@]}" "$@" -I"$STUB" -I"$src" -x c++ "${files[$i]}" >"$out/$i.log" 2>&1; then
        : >"$out/$i.ok"
      fi ) &
    if (( (i + 1) % jobs == 0 )); then wait; fi
  done
  wait
  for ((i = 0; i < ${#files[@]}; i++)); do
    if [[ ! -e "$out/$i.ok" ]]; then
      echo "FAIL ${files[$i]} [RadioLib, $label]"; cat "$out/$i.log"; fail=1
    elif [[ -s "$out/$i.log" ]]; then
      echo "warn ${files[$i]} [RadioLib, $label]"; cat "$out/$i.log"
    else
      n_ok=$((n_ok + 1))
    fi
  done
  echo "ok   RadioLib $src: $n_ok of ${#files[@]} translation units clean [$label]"
}

# ---- RadioLib copies -------------------------------------------------------------
RADIOLIB=""   # src/ of the verified patched copy
STOCK=""      # src/ of the verified stock tag (negative control)
if [[ -n "${LIBS_DIR:-}" ]]; then
  for d in "$LIBS_DIR"/*/src; do
    if [[ -d "$d" && "$d" != "$LIBS_DIR/RadioLib/src" ]]; then INC+=(-isystem "$d"); fi
  done
fi
fetch_copy() {   # $1 = label, $2 = destination, rest = radiolib.py options; 0 = fetched
  local label="$1" dest="$2" rc=0; shift 2
  "$PY" "$TOOL" fetch "$@" "$dest" >"$LOG" 2>&1 || rc=$?
  if [[ $rc -eq 0 ]]; then
    echo "ok   $(cat "$LOG")"
    return 0
  elif [[ $rc -eq 3 ]]; then
    echo "skip RadioLib $label: upstream not reachable"; cat "$LOG"
  else
    echo "FAIL tools/radiolib.py fetch ($label), exit $rc"; cat "$LOG"; fail=1
  fi
  return 1
}
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "skip RadioLib checks: $PY not found"
else
  if [[ -n "${LIBS_DIR:-}" && -d "$LIBS_DIR/RadioLib" ]]; then
    if "$PY" "$TOOL" check "$LIBS_DIR/RadioLib" >"$LOG" 2>&1; then
      RADIOLIB="$LIBS_DIR/RadioLib/src"; echo "ok   $(cat "$LOG")"
    elif "$PY" "$TOOL" check --unpatched "$LIBS_DIR/RadioLib" >"$LOG" 2>&1; then
      STOCK="$LIBS_DIR/RadioLib/src"; echo "ok   $(cat "$LOG") (negative control)"
    else
      echo "note $LIBS_DIR/RadioLib is not a copy written by tools/radiolib.py: not used"
    fi
  fi
  for d in "$SK/libraries/RadioLib" "$(dirname "$FP")/libraries/RadioLib"; do
    [[ -d "$d" ]] || continue
    if "$PY" "$TOOL" check "$d" >"$LOG" 2>&1; then
      echo "ok   $(cat "$LOG")"
      if [[ -z "$RADIOLIB" ]]; then RADIOLIB="$d/src"; fi
    else
      echo "FAIL $d (stale bundled copy: run python3 tools/radiolib.py fetch $d)"; cat "$LOG"; fail=1
    fi
  done
  if [[ -z "$RADIOLIB" ]]; then
    if fetch_copy "patched copy" "$TMP/patched/RadioLib"; then RADIOLIB="$TMP/patched/RadioLib/src"; fi
  fi
  if [[ -n "$RADIOLIB" && -z "$STOCK" ]]; then
    if fetch_copy "stock tag" "$TMP/stock/RadioLib" --unpatched; then STOCK="$TMP/stock/RadioLib/src"; fi
  fi
fi

# ---- Flight software (UNO Q) -------------------------------------------------------
check "$SK/sketch.ino" "" "${UNOQ[@]}"
while IFS= read -r f; do check "$f" "" "${UNOQ[@]}"; done < <(find "$SK/src" -name '*.cpp' | LC_ALL=C sort)

# Radio drivers for every selectable radio (default build above: E22-400T33D).
DRV="$SK/src/drivers"
check "$DRV/e22.cpp" " [VGQ_RADIO=2]" "${UNOQ[@]}" -DVGQ_RADIO=2
check "$DRV/radio.cpp" " [host, no Arduino]" -DVGQ_HOST_BUILD -I"$SK/src"
if [[ -n "$RADIOLIB" ]]; then
  WIRING=()
  wtag=""
  if ! grep -q "PIN_SX_NSS" "$SK/src/config.h"; then
    cat >"$TMP/sx_wiring.h" <<'EOF'
// check_firmware.sh stand-in for the config.h SX126x section (contract 1.1 / 2.2 defaults).
#include <stdint.h>
static const int PIN_SX_NSS = 10, PIN_SX_BUSY = 4, PIN_SX_DIO1 = 5, PIN_SX_NRST = 6;
static const int PIN_SX_RXEN = 8, PIN_SX_TXEN = 9;
static const float kSxTcxoV = 1.8f;
static const bool kSxDio2RfSwitch = false;
static const int8_t kSxPaGainDb = 11;
static const float kSxOcpMa = 140.0f;
EOF
    WIRING=(-include "$TMP/sx_wiring.h")
    wtag=", contract 1.1 wiring"
  fi
  for chip in 1268 1262; do
    check "$DRV/sx126x.cpp" " [VGQ_RADIO=3, SX$chip$wtag]" "${UNOQ[@]}" -DVGQ_RADIO=3 -DVGQ_SX126X_CHIP=$chip \
      ${WIRING[@]+"${WIRING[@]}"} -isystem "$RADIOLIB"
  done
  check_library "$RADIOLIB" "UNO Q" "${UNOQ[@]}"
  check_library "$RADIOLIB" "VENTUNO Q" "${VENTUNOQ[@]}"
fi

# ---- Station I/O sketch (VENTUNO Q) ---------------------------------------------------
if [[ -f "$FP" ]]; then
  check "$FP" "" "${VENTUNOQ[@]}"
  if [[ -n "$RADIOLIB" ]]; then
    for chip in 1268 1262; do
      check "$FP" " [DSSQ_RADIO_SX126X=1, SX$chip]" "${VENTUNOQ[@]}" -DDSSQ_RADIO_SX126X=1 -DDSSQ_SX_CHIP=$chip \
        -isystem "$RADIOLIB"
    done
  fi
fi

# ---- Negative control: the stock tag must not build for the VENTUNO Q --------------------
if [[ -n "$STOCK" ]]; then
  if "$CXX" "${LIBFLAGS[@]}" "${VENTUNOQ[@]}" -I"$STUB" -I"$STOCK" -x c++ "$STOCK/hal/Arduino/ArduinoHal.cpp" \
       >"$LOG" 2>&1; then
    echo "FAIL negative control: stock RadioLib 7.8.1 ArduinoHal.cpp compiled for the VENTUNO Q -"
    echo "     the stub no longer models the core's typed pinMode / digitalWrite / attachInterrupt"
    fail=1
  elif grep -q "to 'PinMode'" "$LOG" && grep -q "to 'PinStatus'" "$LOG"; then
    echo "ok   negative control: stock RadioLib 7.8.1 ArduinoHal.cpp fails for the VENTUNO Q" \
         "($(grep -c 'error:' "$LOG") PinMode / PinStatus errors, as with the real core)"
  else
    echo "FAIL negative control: unexpected diagnostics"; cat "$LOG"; fail=1
  fi
fi
exit $fail
