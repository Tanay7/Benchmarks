"""pytest set-up for the repository's test suite (run from the repository root).

The code under test lives in the two App Lab App folders: the ground data system
package `dssq` in dssq-groundstation/python and the spacecraft EGSE modules (cfdp_tx,
main.py) in vgq1-spacecraft/python. Both folders go on the import path here, so every
test imports `dssq` and `cfdp_tx` directly. C++ cross-checks build tools/host_test.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for _path in (REPO / "vgq1-spacecraft" / "python", REPO / "dssq-groundstation" / "python"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


@pytest.fixture(scope="session", autouse=True)
def synchronised_station_clock():
    """Every GroundStation built by a test sees a synchronised station clock (D-76): the
    real kernel query (dssq.clocksync.query_kernel) reports the state of the machine running
    the tests - a container without NTP says "not synchronised", which would refuse every TDM
    record. tests/test_clocksync.py calls the real query itself; tests of the unsynchronised
    case give a GroundStation its own StationClock."""
    from dssq import clocksync, gds
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gds, "STATION_CLOCK_QUERY", lambda: clocksync.ClockReading(True, "TIME_OK", False, 1000, 100))
        yield
