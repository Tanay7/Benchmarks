"""pytest set-up for the repository's test suite (run from the repository root).

The code under test lives in the two App Lab App folders: the ground data system
package `dssq` in dssq-groundstation/python and the spacecraft EGSE modules (cfdp_tx,
main.py) in vgq1-spacecraft/python. Both folders go on the import path here, so every
test imports `dssq` and `cfdp_tx` directly. C++ cross-checks build tools/host_test.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for _path in (REPO / "vgq1-spacecraft" / "python", REPO / "dssq-groundstation" / "python"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
