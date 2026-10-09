#!/usr/bin/env bash
# Fetches third-party source that is not redistributed in this repository.
#
#   * Bosch BME690 SensorAPI (BSD-3-Clause) -> spacecraft/vgq1_flight/sketch/src/vendor/bme690
#     The flight software compiles the BME690 driver only if these files exist.
#
# Usage:  tools/fetch_vendor_libs.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/spacecraft/vgq1_flight/sketch/src/vendor/bme690"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

git clone --depth 1 https://github.com/boschsensortec/BME690_SensorAPI.git "$TMP/bme690"
mkdir -p "$DEST"
cp "$TMP/bme690/bme69x.c" "$TMP/bme690/bme69x.h" "$TMP/bme690/bme69x_defs.h" "$TMP/bme690/LICENSE" "$DEST/"
echo "BME690 SensorAPI installed in $DEST (license: BSD-3-Clause, see LICENSE there)"
