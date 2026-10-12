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
#   * flight drivers/sx126x.cpp with VGQ_RADIO=3 for the SX1268 and the SX1262, with
#     the SX126x wiring and module constants from config.h (a missing definition fails);
#   * the station sketch with DSSQ_RADIO_SX126X=1, VENTUNO Q, both chips;
#   * the flight build matrix below with VGQ_RADIO=3 (SX1268, SX1262).
# Flight build matrix (PB-8.5): sketch.ino and fsw/flight.cpp for every build switch
# (VGQ_RADIO 2 / 3, USLP downlink, SDLS TC AUTH, SDLS off, a generated key header,
# the debug monitor); the keyed build uses a temporary key, never the tree.
# Negative control: the stock 7.8.1 tag (fetched with --unpatched, or
# $LIBS_DIR/RadioLib when that is the stock tag) must FAIL to compile its
# ArduinoHal.cpp for the VENTUNO Q with the core's PinMode / PinStatus errors -
# the proof that the stub is strict enough to see what the patch fixes.
# Without network access the RadioLib checks are reported as skipped.
#
# Arduino_RouterBridge (both sketches, contract 41): the sketches are type-checked
# against the stub arduino_stub/Arduino_RouterBridge.h (its Zephyr / MsgPack
# dependencies are not available on the host). Each bundled copy
# <sketch>/libraries/Arduino_RouterBridge must be exactly tag 0.4.3 +
# tools/arduino_routerbridge-0.4.3.patch (`routerbridge.py check`; a stale copy is a
# failure); without a bundled copy, a fresh fetch into a temporary directory proves
# that the pinned tag and the patch still apply (skipped without network access).
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
SK="$ROOT/vgq1-spacecraft/sketch"                     # UNO Q flight sketch (App vgq1-spacecraft)
FP="$ROOT/dssq-groundstation/sketch/sketch.ino"       # VENTUNO Q station sketch (App dssq-groundstation)
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

# ---- Arduino_RouterBridge copies (contract 41) ------------------------------------
RB_TOOL="$ROOT/tools/routerbridge.py"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "skip Arduino_RouterBridge checks: $PY not found"
else
  rb_seen=0
  for d in "$SK/libraries/Arduino_RouterBridge" "$(dirname "$FP")/libraries/Arduino_RouterBridge"; do
    [[ -d "$d" ]] || continue
    rb_seen=1
    if "$PY" "$RB_TOOL" check "$d" >"$LOG" 2>&1; then
      echo "ok   $(cat "$LOG")"
    else
      echo "FAIL $d (stale bundled copy: run python3 tools/routerbridge.py fetch $d)"; cat "$LOG"; fail=1
    fi
  done
  if [[ $rb_seen -eq 0 ]]; then
    rc=0
    "$PY" "$RB_TOOL" fetch "$TMP/rb/Arduino_RouterBridge" >"$LOG" 2>&1 || rc=$?
    if [[ $rc -eq 0 ]] && "$PY" "$RB_TOOL" check "$TMP/rb/Arduino_RouterBridge" >"$LOG" 2>&1; then
      echo "ok   $(cat "$LOG") (fresh fetch; no bundled copy in the sketches)"
    elif [[ $rc -eq 3 ]]; then
      echo "skip Arduino_RouterBridge: upstream not reachable"; cat "$LOG"
    else
      echo "FAIL tools/routerbridge.py fetch / check, exit $rc"; cat "$LOG"; fail=1
    fi
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
  for chip in 1268 1262; do
    check "$DRV/sx126x.cpp" " [VGQ_RADIO=3, SX$chip]" "${UNOQ[@]}" -DVGQ_RADIO=3 -DVGQ_SX126X_CHIP=$chip \
      -isystem "$RADIOLIB"
  done
  check_library "$RADIOLIB" "UNO Q" "${UNOQ[@]}"
  check_library "$RADIOLIB" "VENTUNO Q" "${VENTUNOQ[@]}"
fi

# ---- Flight build matrix (Phase B specification PB-8.5, "WS2 extra") ------------------------
# sketch.ino and fsw/flight.cpp - the two units whose code paths the build switches
# select - for every switch value. The keyed build never touches the tree: a
# generated-format key header in a temporary directory is found through -iquote
# ($KEYDIR/x is searched for "../sdls_key.h" after flight.cpp's own directory), and
# the macro check below proves that the keyed build saw it and the plain build did not.
KEYDIR="$TMP/key"
mkdir -p "$KEYDIR/x"
{
  echo "#pragma once"
  echo "#include <stdint.h>"
  echo "#define VGQ_SDLS_SPI 1"
  printf 'static const uint8_t kVgqSdlsKey[32] = {'
  for i in $(seq 0 31); do printf '0x%02X%s' "$i" "$([[ $i -lt 31 ]] && echo ', ')"; done
  echo '};'
} >"$KEYDIR/sdls_key.h"
matrix() {   # $1 = label, rest = extra flags
  local label="$1"; shift
  check "$SK/sketch.ino" " [$label]" "${UNOQ[@]}" "$@"
  check "$SK/src/fsw/flight.cpp" " [$label]" "${UNOQ[@]}" "$@"
}
matrix "VGQ_RADIO=2" -DVGQ_RADIO=2
if [[ -n "$RADIOLIB" ]]; then
  matrix "VGQ_RADIO=3, SX1268" -DVGQ_RADIO=3 -isystem "$RADIOLIB"
  matrix "VGQ_RADIO=3, SX1262" -DVGQ_RADIO=3 -DVGQ_SX126X_CHIP=1262 -isystem "$RADIOLIB"
else
  echo "skip flight matrix VGQ_RADIO=3: no verified RadioLib copy"
fi
matrix "VGQ_DOWNLINK_FRAMING=1" -DVGQ_DOWNLINK_FRAMING=1
matrix "VGQ_SDLS_TC=1" -DVGQ_SDLS_TC=1
matrix "VGQ_SDLS_TC=0 VGQ_SDLS_TM=0" -DVGQ_SDLS_TC=0 -DVGQ_SDLS_TM=0
matrix "keyed (sdls_key.h via -iquote)" -iquote "$KEYDIR/x"
matrix "keyed + USLP + AUTH" -iquote "$KEYDIR/x" -DVGQ_DOWNLINK_FRAMING=1 -DVGQ_SDLS_TC=1
matrix "VGQ_DEBUG_MONITOR=1" -DVGQ_DEBUG_MONITOR=1
# (captured first: grep -q closing the pipe early would fail the pipeline under pipefail)
macros() { "$CXX" -std=gnu++17 "${UNOQ[@]}" "${INC[@]}" "$@" -dM -E -x c++ "$SK/src/fsw/flight.cpp" 2>/dev/null || true; }
keyed_macros="$(macros -iquote "$KEYDIR/x")"
plain_macros="$(macros)"
if grep -q "define VGQ_HAVE_SDLS_KEY 1" <<<"$keyed_macros"; then
  echo "ok   keyed build resolves ../sdls_key.h to the temporary key header"
else
  echo "FAIL keyed build did not pick up the temporary key header"; fail=1
fi
if [[ -f "$SK/src/sdls_key.h" ]]; then
  echo "note $SK/src/sdls_key.h exists (generated, never committed): the default build is keyed"
elif grep -q "define VGQ_HAVE_SDLS_KEY" <<<"$plain_macros"; then
  echo "FAIL unkeyed build sees a key header without src/sdls_key.h"; fail=1
else
  echo "ok   unkeyed build has no key header"
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
  # LC_ALL=C: GCC quotes types with ASCII ' only in the C locale (UTF-8 locales, as on the CI
  # runner, give U+2018 / U+2019), and the diagnostics are matched below.
  if LC_ALL=C "$CXX" "${LIBFLAGS[@]}" "${VENTUNOQ[@]}" -I"$STUB" -I"$STOCK" -x c++ "$STOCK/hal/Arduino/ArduinoHal.cpp" \
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
