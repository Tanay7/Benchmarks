"""DSS-Q ground station - Arduino App Lab entry point (ground UNO Q Linux side; Phase B PB-6.2).

The App runs the whole ground data system in App Lab's Python container: the radio
receiver chain, telecommanding, the archive, and the operator web dashboard on port 8080
(app.yaml `ports: [8080]`, published N:N by App Lab), talking to its own station I/O sketch
(CardKB, annunciators, Radio Control Unit, optional SX126x radio) through
arduino.app_utils.Bridge. Python standard library only: no requirements.txt (App Lab would
pip-install it at the first start, which needs the internet, contract 23).

Layout facts (PB-6.1): only the App folder is mounted in the container (at /app), so the
`dssq` package and its packaged default configuration live INSIDE the App folder:
python/dssq and python/config/station.toml next to this file. The repository's
dssq-groundstation/ folder IS this App, so a checkout runs exactly what App Lab imports.

Start-up (prepare(), no network, no bridge):
  1. <App>/data is the App's persistent folder (App Lab reserves it); created if missing.
  2. The dssq package is put on the import path (<App>/python, else the folder of this file).
  3. Configuration: $DSSQ_CONFIG if set, else <App>/data/station.toml, which is copied from
     the packaged default on the first start (never overwritten), so the operator edits a
     persistent copy. dssq.config.resolve_app_paths() then applies the App rules: archive
     under data/, SDLS key data/sdls_key.hex unless an absolute path is configured, web
     server on all interfaces ($DSSQ_HTTP_HOST overrides), and the web commanding token
     read from (or created once in) data/http_token, mode 0600.
Run (main()): BridgeLink.from_app_utils(Bridge), gds.Runner(..., mode="applab"), then
App.run(user_loop=runner.step) until App Lab stops the App (SIGTERM); runner.close() then
finishes within 4 s, inside App Lab's 5 s grace period. Runner.step() never lets an
Exception escape (its documented stage boundaries), so the App stops only on a signal.

Host mode (host/install_station.sh, host/dssq-gds.service: python -m dssq.gds --app-data
<App>/data) loads the same <App>/data/station.toml with the same rules
(dssq.config.load_app_config), so the App and host mode share one archive (and with it the
SDLS TC sequence-number file), one SDLS key and one web token. Never run both at the same
time: the Router Bridge lets one Linux client provide a given method name.

Environment:
  DSSQ_CONFIG      station.toml to load instead of <App>/data/station.toml (must exist)
  DSSQ_HTTP_HOST   web server bind address (default 0.0.0.0 inside the App container)
  DSSQ_SIM=1       software spacecraft instead of a radio (demonstration without hardware)
Exit status: 0 stopped normally; 1 the ground data system could not start (e.g. the web
port is in use); 2 set-up error (configuration, package layout, not running under App Lab).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger("dssq.app")

DATA_DIR_NAME = "data"


class SetupError(Exception):
    """A start-up problem the operator must fix (reported, exit status 2)."""


def package_root(app_dir: Path) -> Path:
    """The directory that contains the `dssq` package: <App>/python, else the folder of
    this file (the same folder when main() runs; another one only when prepare() is given
    a different App folder, e.g. an empty data-only folder in a test)."""
    for cand in (app_dir / "python", Path(__file__).resolve().parent):
        if (cand / "dssq" / "__init__.py").is_file():
            return cand
    raise SetupError("dssq package not found: the App must contain python/dssq next to python/main.py")


def _use_package(root: Path) -> None:
    path = str(root)
    if path not in sys.path:
        sys.path.insert(0, path)


def prepare(app_dir: Path) -> dict[str, Any]:
    """Start-up steps 1-3 (module docstring): the data folder, the import path and the
    validated, App-resolved configuration. No network, no bridge. Raises SetupError."""
    app_dir = Path(app_dir).resolve()
    data = app_dir / DATA_DIR_NAME
    try:
        data.mkdir(exist_ok=True)
    except OSError as e:
        raise SetupError(f"cannot create the App data folder {data}: {e}") from e
    _use_package(package_root(app_dir))
    from dssq import config as station_config     # importable only now (step 2)

    override = os.environ.get("DSSQ_CONFIG")
    if override and not Path(override).is_file():
        raise SetupError(f"DSSQ_CONFIG={override}: no such file")
    path = Path(override) if override else data / station_config.APP_CONFIG_FILE
    try:
        # <data>/station.toml (copied once from the packaged default) and the App path rules;
        # host mode (python -m dssq.gds --app-data <App>/data) loads it the same way.
        return station_config.load_app_config(data, override or None)
    except (OSError, ValueError) as e:            # tomllib.TOMLDecodeError is a ValueError
        raise SetupError(f"configuration {path}: {e}") from e


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="DSS-Q ground station (Arduino App Lab entry point)")
    ap.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app_dir = Path(__file__).resolve().parents[1]
    try:
        cfg = prepare(app_dir)
    except SetupError as e:
        log.error("%s", e)
        return 2
    try:
        from arduino.app_utils import App, Bridge  # type: ignore[import-not-found]
    except ImportError:
        log.error("not running under App Lab (no arduino.app_utils): host mode runs  python -m dssq.gds "
                  "--app-data %s  in %s (host/install_station.sh sets it up)", app_dir / DATA_DIR_NAME,
                  app_dir / "python")
        return 2
    from dssq.display.frontpanel import BridgeLink
    from dssq.gds import Runner

    sim = os.environ.get("DSSQ_SIM", "") == "1"
    try:
        runner = Runner(cfg, bridge=BridgeLink.from_app_utils(Bridge), mode="applab", sim=sim)
    except (OSError, ValueError) as e:            # e.g. the web port is already in use
        log.error("ground data system not started: %s", e)
        return 1
    g = cfg["gds"]
    log.info("DSS-Q ground station running: dashboard on port %s (bound to %s); archive %s; commanding "
             "token in %s", g["http_port"], g["http_host"], g["archive_dir"], app_dir / DATA_DIR_NAME / "http_token")
    try:
        App.run(user_loop=runner.step)
    finally:
        runner.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
