"""Documentation drift test for docs/13_Web_UI_Reference.md (the web UI reference).

The reference documents the dashboard key by key, so it goes stale whenever a snapshot key
is added, renamed or removed. This test builds REAL snapshots - GroundStation with the
software spacecraft on a virtual clock, one E22 and one SX126x world, plus a station without
a radio backend - and checks both directions:

  * forward: every top-level key of /api/state and /api/science, every key of rx.rf, every
    key of the receiver data logger (logger), every key of the nested blocks docs/13 documents
    key by key (KEY_BY_KEY_BLOCKS, e.g. link.station_id) and every field name of the HK and RF
    packets is written in docs/13 (a new key in one of those blocks fails until documented);
  * reverse: every snapshot path written in docs/13 (a backticked dotted path whose first
    part is a top-level key, e.g. `rx.rf.rssi_dbm`) exists in at least one of the snapshots.
    A part in angle brackets (`apids.<packet>.count`) matches any key, `[]` any list element
    (`cfdp.transactions[].progress`).

A failure names the keys to document, or the paths in docs/13 to correct.
"""
import json
import re
import time
from pathlib import Path

import pytest

from dssq import dictionary as dic
from dssq.config import load_config
from dssq.gds import GroundStation, _SimRcu
from dssq.radioctl import RadioManager
from dssq.sim import SimSpacecraft

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "13_Web_UI_Reference.md"
KEY = bytes(range(32))                       # test key (the host_test vector key)
T0 = 1_800_000_000.0                         # 2027-01-15, a fixed virtual start time
# Spans that name files, not snapshot paths (`station.toml`, `<archive>/gds.sqlite`, ...).
FILE_SUFFIXES = {"toml", "py", "html", "md", "txt", "json", "hex", "csv", "sqlite", "png", "h", "cpp",
                 "ino", "yaml", "kvn", "xml", "bin", "partial", "jsonl", "service", "sh"}
# Nested blocks that docs/13 documents key by key with full paths (`link.station_id.count`):
# every key any test world has in them must appear. Blocks documented through placeholders
# (`vc.<vc>.packets`, `tlm.<packet>.values.<field>`) are covered by the reverse check instead.
KEY_BY_KEY_BLOCKS = (
    "beacon", "cfdp", "cfdp.counters", "cfdp.onboard", "cop", "decoded", "health", "link", "link.ident",
    "link.sdls", "link.station_id", "link.tm_framing_seen", "linkmgr", "logger", "magcal", "radio", "rx",
    "rx.budget", "rx.budget.uplink", "rx.decoder", "rx.decoder.sdls_rejected", "rx.frames", "rx.radio_link",
    "rx.rf", "rx.rf.afc", "rx.rf.doppler", "rx.rf.packet", "rx.serial", "rx.series", "rx.state", "rx.uplink",
    "sequence", "sequence.file", "sequence.onboard", "sequence.uplink", "station_io", "sync", "tdm", "time",
    "spectral", "spectral.boresight", "spectral.counters", "spectral.exposure", "spectral.features",
    "spectral.indices", "spectral.onboard", "spectral.radiometry", "spectral.reflectance", "spectral_burst",
    "spectral_library", "led_limits", "imu", "sensor_bus", "sensor_bus.instruments")
PATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\[\])?(?:\.(?:<[a-z_]+>|[A-Za-z0-9_-]+)(?:\[\])?)+$")


class _Clock:
    def __init__(self, t: float = T0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def sleep(self, dt: float) -> None:
        self.t += max(dt, 1e-3)


def _world(tmp: Path, mp: pytest.MonkeyPatch, radio: dict, simkw: dict, script: list) -> tuple[dict, dict]:
    """(state, science) snapshots, JSON round-tripped as /api/state and /api/science serve
    them, after running `script` against the software spacecraft."""
    clk = _Clock()
    mp.setattr(time, "time", clk)
    mp.setattr(time, "sleep", clk.sleep)
    cfg = load_config()
    cfg["gds"]["archive_dir"] = str(tmp / "archive")
    (tmp / "archive").mkdir(parents=True, exist_ok=True)
    (tmp / "archive" / "sdls_sn.txt").write_text("0")        # a station whose TC counter is known
    (tmp / "sdls_key.hex").write_text(KEY.hex() + "\n")
    cfg["sdls"]["key_file"] = str(tmp / "sdls_key.hex")
    cfg["radio"].update(radio)
    cfg["science"]["field_ut"] = 50.0
    cfg["spacecraft"]["distance_km"] = 2.0
    gs = GroundStation(cfg, archive=True)
    try:
        sim = SimSpacecraft(cfg, clock=clk, sdls_key=gs.sdls_tc.key, **simkw)
        mgr = (RadioManager(sim, None, clock=clk, sleep=clk.sleep) if sim.packet_mode else
               RadioManager(sim, _SimRcu(sim), clock=clk, sleep=clk.sleep, model=gs.settings.model.name))
        gs.attach_radio(sim, mgr, None)

        def run(seconds: float, step: float = 0.25) -> None:
            end = clk.t + seconds
            while clk.t < end:
                gs.ingest(sim.read(), clk.t)
                if sim.packet_mode:
                    for p in sim.packets():
                        gs.ingest_packet(p)
                for chunk in gs.take_outbox():
                    sim.write(chunk)
                for corr in gs.take_afc_out():
                    gs.afc_confirm(corr, sim.rf_foff(corr))
                if gs.radio_jobs:
                    gs.run_radio_jobs()
                gs.tdm_service()
                gs.housekeeping(clk.t)
                clk.t += step

        for kind, arg in script:
            if kind == "run":
                run(arg)
            elif kind == "cmd":
                r = gs.submit_command(arg)
                if r.get("status") == "CONFIRM":
                    r = gs.confirm(r["id"])
                assert r.get("status") in ("QUEUED", "OK"), (arg, r)
            elif kind == "seq_check":
                gs.seq_check(arg)
        state = json.loads(json.dumps(gs.snapshot(), allow_nan=False))
        science = json.loads(json.dumps(gs.science_snapshot(), allow_nan=False))
    finally:
        gs.close()
    return state, science


def _bare(tmp: Path, mp: pytest.MonkeyPatch) -> tuple[dict, dict]:
    """A station without a radio backend (keys that exist only then, e.g. rx.radio_link.error)."""
    mp.setattr(time, "time", _Clock())
    cfg = load_config()
    cfg["gds"]["archive_dir"] = str(tmp / "archive")
    cfg["sdls"]["key_file"] = str(tmp / "missing_key.hex")
    gs = GroundStation(cfg, archive=False)
    try:
        return (json.loads(json.dumps(gs.snapshot(), allow_nan=False)),
                json.loads(json.dumps(gs.science_snapshot(), allow_nan=False)))
    finally:
        gs.close()


E22_SCRIPT = [
    ("run", 60), ("cmd", "NOOP"), ("run", 20), ("cmd", "MODE TEST"), ("run", 60),
    ("cmd", "PING 42"), ("run", 15), ("cmd", "RFSCAN 20 29"), ("run", 20), ("cmd", "GSCAN 20 24"), ("run", 15),
    ("cmd", "SPECREF"), ("cmd", "SPECLIB SAVE doc_ref"), ("run", 40),
    ("cmd", "CFDPPUT 0 2"), ("run", 90),
    ("cmd", "SPECMODE BURST 2"), ("run", 60), ("cmd", "SPECMODE SURVEY 1"), ("run", 70),
    ("seq_check", "T+00:00:10 NOOP   # drift test\nT+00:00:20 PING 7\n"),
]
SX_SCRIPT = [("run", 60), ("cmd", "NOOP"), ("run", 20), ("cmd", "MODE TEST"), ("run", 150)]


@pytest.fixture(scope="module")
def worlds(tmp_path_factory):
    out = []
    with pytest.MonkeyPatch.context() as mp:
        out.append(_world(tmp_path_factory.mktemp("docs_e22"), mp, {"air_rate": 7},
                          {"loss": 0.03, "max_errors": 3, "rssi_mean": -100.0, "fading_db": 2.0, "seed": 11},
                          E22_SCRIPT))
    with pytest.MonkeyPatch.context() as mp:
        out.append(_world(tmp_path_factory.mktemp("docs_sx"), mp, {"model": "SX126x", "air_rate": 7},
                          {"loss": 0.02, "max_errors": 3, "rssi_mean": -110.0, "fading_db": 2.0, "seed": 5,
                           "crc_fail_prob": 0.05, "static_offset_hz": 1200.0, "doppler_rate_hz_s": 0.2},
                          SX_SCRIPT))
    with pytest.MonkeyPatch.context() as mp:
        out.append(_bare(tmp_path_factory.mktemp("docs_bare"), mp))
    return out


@pytest.fixture(scope="module")
def spans():
    return re.findall(r"`([^`\n]+)`", DOC.read_text(encoding="utf-8"))


def _documented(key_path: str, spans: list[str]) -> bool:
    """True when docs/13 names the path itself or a path below it."""
    return any(s == key_path or s.startswith(key_path + ".") or s.startswith(key_path + "[") for s in spans)


# ------------------------------------------------------------------ forward
def test_every_top_level_key_is_documented(worlds, spans):
    keys = {k for state, _ in worlds for k in state} | {k for _, sci in worlds for k in sci}
    missing = sorted(k for k in keys if not _documented(k, spans))
    assert not missing, f"top-level snapshot keys missing from {DOC.name}: {missing}"


def test_every_rx_rf_key_is_documented(worlds, spans):
    keys = {k for state, _ in worlds for k in (state["rx"].get("rf") or {})}
    assert len(keys) >= 30, sorted(keys)                    # sanity: the real rx.rf block
    missing = sorted(k for k in keys if not _documented(f"rx.rf.{k}", spans))
    assert not missing, f"rx.rf keys missing from {DOC.name}: {missing}"


def test_every_logger_key_is_documented(worlds, spans):
    assert _documented("logger", spans)
    keys = {k for state, _ in worlds for k in state["logger"]}
    missing = sorted(k for k in keys if not _documented(f"logger.{k}", spans))
    assert not missing, f"logger keys missing from {DOC.name}: {missing}"


def _block(root: dict, path: str):
    node = root
    for seg in path.split("."):
        node = node.get(seg) if isinstance(node, dict) else None
    return node


def test_every_key_of_the_key_by_key_blocks_is_documented(worlds, spans):
    missing, seen = [], set()
    for path in KEY_BY_KEY_BLOCKS:
        keys: set[str] = set()
        for state, sci in worlds:
            node = _block(sci if path.split(".")[0] in sci else state, path)
            if isinstance(node, dict):
                keys |= set(node)
                seen.add(path)
        missing += [f"{path}.{k}" for k in sorted(keys) if not _documented(f"{path}.{k}", spans)]
    assert set(KEY_BY_KEY_BLOCKS) <= seen, f"blocks absent from every test world: {set(KEY_BY_KEY_BLOCKS) - seen}"
    assert not missing, f"keys missing from {DOC.name}: {missing}"


@pytest.mark.parametrize("apid", [dic.APID_HK, dic.APID_RF])
def test_every_hk_and_rf_field_is_documented(apid, spans):
    names = [str(f[0]) for f in dic.PACKETS[apid]["fields"]]
    assert len(names) == {dic.APID_HK: 43, dic.APID_RF: 24}[apid]
    missing = [n for n in names if n not in spans]
    assert not missing, f"{dic.PACKET_NAMES[apid]} fields missing from {DOC.name}: {missing}"


# ------------------------------------------------------------------ reverse
def _resolve(node, segs: list[str]) -> str:
    """'yes' when the path exists below node, 'no' when it cannot, 'unknown' when a null or
    an empty container on the way hides the answer."""
    if not segs:
        return "yes"
    seg, rest = segs[0], segs[1:]
    if seg.endswith("[]"):
        name = seg[:-2]
        if name:
            if not isinstance(node, dict) or name not in node:
                return "no"
            node = node[name]
        if node is None:
            return "unknown"
        if not isinstance(node, list):
            return "no"
        return _any([_resolve(x, rest) for x in node])
    if node is None:
        return "unknown"
    if seg.startswith("<"):
        if isinstance(node, dict):
            return _any([_resolve(v, rest) for v in node.values()])
        return "no"
    if not isinstance(node, dict):
        return "no"
    if seg not in node:
        return "no"
    return _resolve(node[seg], rest)


def _any(results: list[str]) -> str:
    if "yes" in results:
        return "yes"
    return "unknown" if not results or "unknown" in results else "no"


def test_every_documented_path_exists(worlds, spans):
    top = {k for state, _ in worlds for k in state} | {k for _, sci in worlds for k in sci}
    paths = sorted({s for s in spans if PATH_RE.match(s) and s.split(".")[0].removesuffix("[]") in top
                    and s.rsplit(".", 1)[-1] not in FILE_SUFFIXES})
    assert len(paths) > 400, len(paths)                      # sanity: the document is the real one
    broken, unverified = [], []
    for p in paths:
        segs = p.split(".")
        results = []
        for state, sci in worlds:
            root = sci if segs[0] in sci else state
            results.append(_resolve(root, segs))
        verdict = _any(results)
        if verdict == "no":
            broken.append(p)
        elif verdict == "unknown":
            unverified.append(p)
    assert not broken, f"paths in {DOC.name} that no snapshot has: {broken}"
    assert not unverified, f"paths in {DOC.name} hidden behind null or empty values in every test world " \
                           f"(extend the test worlds): {unverified}"
