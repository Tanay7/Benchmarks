#!/usr/bin/env bash
# One-time setup of the DSS-Q ground data system in HOST mode (systemd), the documented
# fallback / always-on alternative to running the App in App Lab (contract 18). Run ON THE
# BOARD (the ground UNO Q's Linux side, Debian) from a terminal; it works from any
# directory and for the App folder it lives in (imported by App Lab, or a repository
# checkout):
#
#     ~/ArduinoApps/dssq-groundstation/host/install_station.sh             # setup only
#     ~/ArduinoApps/dssq-groundstation/host/install_station.sh --service   # + start at boot
#
# Why a virtual environment: the GDS itself needs only the Python standard library, but in
# host mode the front panel (CardKB, annunciators), the Radio Control Unit and the SX126x
# radio talk to the station MCU through arduino.router_bridge (PyPI arduino-router-bridge,
# which brings msgpack). App Lab's Python container image has it; the board's host Python
# does not (the arduino-app-cli Debian package depends only on the arduino-router service),
# and a virtual environment keeps pip away from the Debian system Python (which may refuse
# it as "externally managed"; the board's Debian release: VERIFY). Without it the GDS still
# runs but logs "router bridge unavailable" and disables those three functions.
# The environment lives OUTSIDE the App folder ($DSSQ_VENV, default ~/.venvs/dssq-gds):
# App Lab mounts the App folder into its container and an exported App must not carry it.
#
# Steps (each skipped when already in place; a failed step is listed at the end):
#   1. python3-venv (apt)
#   2. the virtual environment with arduino-router-bridge==0.5.1 (needs the internet once)
#   3. --service: host/dssq-gds.service installed for this App folder, the environment of
#      step 2 and the invoking user: python -m dssq.gds --app-data <App>/data, so host mode
#      uses the App's own station.toml, archive (with the SDLS TC sequence-number file), SDLS
#      key and web token - never a second archive, which would restart the TC sequence
#      counter of the key (GCM nonce reuse under AE)
set -uo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GDS_DIR="${APP_DIR}/python"
VENV="${DSSQ_VENV:-${HOME}/.venvs/dssq-gds}"
BRIDGE_PKG="arduino-router-bridge==0.5.1"
UNIT_SRC="${APP_DIR}/host/dssq-gds.service"
UNIT_DST=/etc/systemd/system/dssq-gds.service
# Defaults written in the shipped unit (an App imported by App Lab for user arduino).
UNIT_APP_DIR=/home/arduino/ArduinoApps/dssq-groundstation
UNIT_VENV=/home/arduino/.venvs/dssq-gds
problems=()

sed_escape() {           # sed_escape <text>: <text> as a literal sed replacement
  printf '%s' "$1" | sed -e 's/[\\|&]/\\&/g'
}

if [[ ! -f "${GDS_DIR}/dssq/__init__.py" ]]; then
  echo "error: ${GDS_DIR}/dssq not found - run this script from the App's host/ folder" >&2
  exit 2
fi

echo "== 1. python3-venv (the sudo password may be asked)"
if python3 -c "import ensurepip, venv" 2>/dev/null; then
  echo "   ok: python3-venv is installed"
elif sudo apt-get update && sudo apt-get install -y python3-venv; then
  echo "   python3-venv installed"
else
  problems+=("python3-venv could not be installed: step 2 needs it")
fi

echo "== 2. Virtual environment ${VENV}"
if [[ ! -x "${VENV}/bin/python" ]] && ! { mkdir -p "$(dirname "${VENV}")" && python3 -m venv "${VENV}"; }; then
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
  if [[ "${APP_DIR}${VENV}" =~ [[:space:]] ]]; then
    problems+=("the App folder or the environment path contains white space: unit not installed")
  elif sed -e "s|${UNIT_APP_DIR}|$(sed_escape "${APP_DIR}")|g" -e "s|${UNIT_VENV}|$(sed_escape "${VENV}")|g" \
          -e "s|^User=.*|User=$(id -un)|" "${UNIT_SRC}" | sudo tee "${UNIT_DST}" > /dev/null \
       && sudo systemctl daemon-reload && sudo systemctl enable --now dssq-gds; then
    echo "   dssq-gds enabled and started (journalctl -u dssq-gds -f)"
    echo "   Do not run the App Lab App 'DSS-Q Ground Station' at the same time."
  else
    problems+=("systemd unit not installed / started")
  fi
else
  echo "Run the ground data system in the foreground (same data as the App) with:"
  echo "   cd ${GDS_DIR} && ${VENV}/bin/python -m dssq.gds --app-data ${APP_DIR}/data"
fi

if [[ ${#problems[@]} -eq 0 ]]; then
  echo "== done"
  exit 0
fi
echo "== finished with problems:"
for p in "${problems[@]}"; do echo "   - ${p}"; done
exit 1
