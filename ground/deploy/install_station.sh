#!/usr/bin/env bash
# One-time setup of the DSS-Q ground data system in HOST mode (systemd), the documented
# fallback / always-on alternative to the App Lab App (contract 18). Run ON THE BOARD
# (VENTUNO Q Linux side) from a terminal; it works from any directory:
#
#     ~/Benchmarks/ground/deploy/install_station.sh             # setup only
#     ~/Benchmarks/ground/deploy/install_station.sh --service   # setup + start at boot (systemd)
#
# Why a virtual environment: the GDS itself needs only the Python standard library, but in
# host mode the front panel (CardKB, annunciators), the Radio Control Unit and the SX126x
# radio talk to the station MCU through arduino.router_bridge (PyPI arduino-router-bridge,
# which brings msgpack). App Lab's Python container image has it; the board's host Python
# does not (the arduino-app-cli Debian package depends only on the arduino-router service),
# and Ubuntu 24.04 does not allow pip into the system Python. Without it the GDS still runs
# but logs "router bridge unavailable" and disables those three functions.
#
# Steps (each skipped when already in place; a failed step is listed at the end):
#   1. python3-venv (apt)
#   2. <ground>/.venv with arduino-router-bridge==0.5.1 (needs the internet once)
#   3. --service: dssq-gds.service installed with this checkout's user and paths
set -uo pipefail

GROUND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${GROUND_DIR}/.venv"
BRIDGE_PKG="arduino-router-bridge==0.5.1"
UNIT_SRC="${GROUND_DIR}/deploy/dssq-gds.service"
UNIT_DST=/etc/systemd/system/dssq-gds.service
problems=()

echo "== 1. python3-venv (the sudo password may be asked)"
if python3 -c "import ensurepip, venv" 2>/dev/null; then
  echo "   ok: python3-venv is installed"
elif sudo apt-get update && sudo apt-get install -y python3-venv; then
  echo "   python3-venv installed"
else
  problems+=("python3-venv could not be installed: step 2 needs it")
fi

echo "== 2. Virtual environment ${VENV}"
if [[ ! -x "${VENV}/bin/python" ]] && ! python3 -m venv "${VENV}"; then
  problems+=("python3 -m venv ${VENV} failed")
fi
if [[ -x "${VENV}/bin/python" ]]; then
  if "${VENV}/bin/python" -c "import arduino.router_bridge" 2>/dev/null; then
    echo "   ok: arduino.router_bridge importable"
  elif "${VENV}/bin/pip" install "${BRIDGE_PKG}"; then
    echo "   ${BRIDGE_PKG} installed"
  else
    problems+=("pip install ${BRIDGE_PKG} failed: front panel / RCU / SX126x radio stay disabled")
  fi
  "${VENV}/bin/python" -c "import sys; assert sys.version_info >= (3, 11)" 2>/dev/null \
    || problems+=("${VENV} runs Python < 3.11 (tomllib is needed)")
fi

if [[ "${1:-}" == "--service" ]]; then
  echo "== 3. systemd unit ${UNIT_DST}"
  # The shipped unit names /home/arduino/Benchmarks/ground and user arduino: rewritten for
  # this checkout and the invoking user.
  if sed -e "s|/home/arduino/Benchmarks/ground|${GROUND_DIR}|g" -e "s|^User=.*|User=$(id -un)|" \
        "${UNIT_SRC}" | sudo tee "${UNIT_DST}" > /dev/null \
     && sudo systemctl daemon-reload && sudo systemctl enable --now dssq-gds; then
    echo "   dssq-gds enabled and started (journalctl -u dssq-gds -f)"
    echo "   Do not run the App Lab App 'DSS-Q Ground Station' at the same time."
  else
    problems+=("systemd unit not installed / started")
  fi
fi

if [[ ${#problems[@]} -eq 0 ]]; then
  echo "== done"
  exit 0
fi
echo "== finished with problems:"
for p in "${problems[@]}"; do echo "   - ${p}"; done
exit 1
