"""The SCIENCE tab's IMU time chart (D-75 / D-76): gds.ImuHistory, the /api/science key
`imu_history` and the three strips of the dashboard.

* ImuHistory: bounded to its newest points (`received` keeps counting), an n/a value
  (None) stays None, `join` only between consecutive ATT sequence counts (mod 16384) of
  one pass.
* GroundStation: a real-time ATT through the packet path adds the tilt, |omega| and
  vibration RMS that imu_products() gives (None where it reports n/a); a recorder playback
  (VC2) ATT is counted and never added, also with the simulator's PLAYBACK.
* Dashboard, rendered in Node.js (the tests/test_web.py harness with a canvas that records
  what is drawn): with data the three strips share one time axis; an n/a value and a
  packet that does not follow the previous one break the line, a lone value is a dot; with
  no point every strip says "no 6-DOF data received in this session".
* docs/13 names every key of `imu_history`.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from dssq import dictionary as dd
from dssq.ccsds.packets import SpacePacket, build_packet
from dssq.ccsds.timecode import CucTime
from dssq.config import load_config
from dssq.decom import encode
from dssq.display import web
from dssq.gds import IMU_HISTORY_LEN, GroundStation, ImuHistory

REPO = Path(__file__).resolve().parents[1]
T0 = 1_800_000_000.0
STRIPS = ("si-imuh-tilt", "si-imuh-rate", "si-imuh-vib")
EMPTY = "no 6-DOF data received in this session"
KEYS = {"max_points", "received", "playback_excluded", "t", "join", "tilt_deg", "rate_abs_dps", "vib_rms_mg"}


def _imu(tilt=None, rate=None, vib=None) -> dict:
    return {"tilt_deg": tilt, "rate_abs_dps": rate, "vib_rms_mg": vib}


# ------------------------------------------------------------------ ImuHistory
def test_history_is_bounded_and_keeps_every_none():
    h = ImuHistory(maxlen=4)
    for i in range(6):
        h.add(T0 + 8 * i, i, 1, _imu(10.0 + i, None if i == 3 else 1.5, None if i == 5 else 2.25))
    s = h.snapshot()
    assert set(s) == KEYS
    assert s["max_points"] == 4 and s["received"] == 6 and s["playback_excluded"] == 0
    assert s["t"] == [T0 + 16, T0 + 24, T0 + 32, T0 + 40]                    # the two oldest dropped
    assert s["tilt_deg"] == [12.0, 13.0, 14.0, 15.0]
    assert s["rate_abs_dps"] == [1.5, None, 1.5, 1.5] and s["vib_rms_mg"] == [2.25, 2.25, 2.25, None]
    assert ImuHistory().points.maxlen == IMU_HISTORY_LEN == 1800
    # values to 0.001, finer than every reading's resolution; the time to 1 ms
    h = ImuHistory()
    h.add(T0 + 0.12345, 0, 1, _imu(12.34567, 0.12351, 2.0004))
    s = h.snapshot()
    assert (s["t"], s["tilt_deg"], s["rate_abs_dps"], s["vib_rms_mg"]) == ([T0 + 0.123], [12.346], [0.124], [2.0])


def test_join_only_between_consecutive_packets_of_one_pass():
    h = ImuHistory()
    for i, (seq, pass_no) in enumerate(((16382, 1), (16383, 1), (0, 1), (2, 1), (3, 1), (4, 2), (5, 2), (5, 2))):
        h.add(T0 + i, seq, pass_no, _imu(1.0, 1.0, 1.0))
    # first point; +1; +1 across the 14-bit wrap; 1 packet lost; +1; new pass; +1; a repeated count
    assert h.snapshot()["join"] == [False, True, True, False, True, False, True, False]


# ------------------------------------------------------------------ GroundStation
def _att_packet(seq: int, **over) -> SpacePacket:
    """A real-time ATT v4 through the real encoder: a level body (identity quaternions),
    rate_mean (0.03, 0.04, 0) deg/s, vib_rms 2.1 mg, 8 windows, IMU flags GRV | STATS |
    PAIRED | GRV_PRESENT (tests/test_sensors.py _att)."""
    v = {"flags": 0x1F, "acc_x": 0.0, "acc_y": 0.0, "acc_z": 1.0, "gyro_x": 0.0, "gyro_y": 0.0, "gyro_z": 0.0,
         "mag_x": 20.0, "mag_y": 0.0, "mag_z": 40.0, "q_fus_acc": 0.035,
         "imu_flags": 0x47, "imu_nwin": 8, "vib_rms": 2.1, "vib_peak": 7, "rate_mean_x": 0.03,
         "rate_mean_y": 0.04, "rate_mean_z": 0.0, "rate_peak": 0.3}
    for i, a in enumerate("wxyz"):
        for q in ("q_fus", "q_triad", "q_grv"):
            v[f"{q}_{a}"] = 1.0 if i == 0 else 0.0
    v.update(over)
    return SpacePacket.parse(build_packet(dd.APID_ATT, seq, encode(dd.APID_ATT, v), CucTime.from_seconds(10.0)))


@pytest.fixture
def bare_gs(tmp_path):
    cfg = load_config()
    cfg["gds"]["archive_dir"] = str(tmp_path / "archive")
    cfg["sdls"]["key_file"] = str(tmp_path / "missing_key.hex")
    gs = GroundStation(cfg, archive=False)
    yield gs
    gs.close()


def test_real_time_att_adds_the_imu_products_and_playback_is_only_counted(bare_gs):
    gs = bare_gs
    h = gs.science_snapshot()["imu_history"]
    assert h["t"] == [] and h["received"] == 0 and h["max_points"] == IMU_HISTORY_LEN
    gs._on_packet(_att_packet(7), 1, T0, True)                              # tilt 0, |omega| 0.05, vib 2.1
    gs._on_packet(_att_packet(8, imu_flags=0x46), 1, T0 + 8, True)          # IMU_F_GRV clear: tilt n/a
    gs._on_packet(_att_packet(9, imu_nwin=0), 1, T0 + 16, True)             # no window: |omega|, vib n/a
    gs._on_packet(_att_packet(10), 2, T0 + 20, True)                        # recorder playback (VC2)
    gs._on_packet(_att_packet(12), 1, T0 + 32, True)                        # ATT 11 lost
    h = json.loads(json.dumps(gs.science_snapshot()["imu_history"], allow_nan=False))
    assert h["t"] == [T0, T0 + 8, T0 + 16, T0 + 32] and h["received"] == 4 and h["playback_excluded"] == 1
    assert h["tilt_deg"] == [0.0, None, 0.0, 0.0]
    assert h["rate_abs_dps"] == [pytest.approx(0.05), pytest.approx(0.05), None, pytest.approx(0.05)]
    assert h["vib_rms_mg"] == [pytest.approx(2.1), pytest.approx(2.1), None, pytest.approx(2.1)]
    assert h["join"] == [False, True, True, False]
    # the IMU panel shows the newest real-time ATT, not the playback copy
    assert gs.snapshot()["tlm"]["ATT"]["seq"] == 12


def test_simulator_playback_is_never_drawn(monkeypatch, tmp_path):
    from test_end_to_end import World

    w = World(monkeypatch, tmp_path)
    w.run(40)
    assert w.command("MODE TEST") == "EXECUTED"
    w.run(60)
    before = w.gs.science_snapshot()["imu_history"]
    assert before["received"] >= 5 and before["playback_excluded"] == 0
    assert all(v is not None for k in ("tilt_deg", "rate_abs_dps", "vib_rms_mg") for v in before[k])
    assert w.command("PLAYBACK START") == "EXECUTED"
    w.run(120)
    h = w.gs.science_snapshot()["imu_history"]
    assert h["playback_excluded"] > 0
    assert h["received"] + h["playback_excluded"] == w.gs.snapshot()["apids"]["ATT"]["count"]
    assert h["received"] == len(h["t"]) > len(before["t"]) and h["t"][:len(before["t"])] == before["t"]
    assert all(a < b for a, b in zip(h["t"], h["t"][1:], strict=False)) and h["t"][-1] <= w.clk.t


# ------------------------------------------------------------------ docs/13
def test_docs_name_every_key_of_the_history():
    text = (REPO / "docs" / "13_Web_UI_Reference.md").read_text(encoding="utf-8")
    assert [k for k in sorted(KEYS) if f"`imu_history.{k}`" not in text] == []
    assert "### 9.5 " in text and EMPTY in text


# ------------------------------------------------------------------ dashboard (Node.js)
@pytest.fixture(scope="module")
def station_snapshots(tmp_path_factory):
    """(state, science) of a GroundStation after a minute of simulated TEST mode (ATT every 8 s)."""
    from test_end_to_end import World

    with pytest.MonkeyPatch.context() as mp:
        w = World(mp, tmp_path_factory.mktemp("imuchart"))
        w.run(40)
        assert w.command("MODE TEST") == "EXECUTED"
        w.run(60)
        out = (json.loads(json.dumps(w.gs.snapshot(), allow_nan=False)),
               json.loads(json.dumps(w.gs.science_snapshot(), allow_nan=False)))
        w.gs.close()
    return out


def _harness() -> str:
    """tests/test_web.py's harness, its canvas context recording moveTo / lineTo / arc /
    fillText with the stroke and fill colours of the last draw of every canvas."""
    from test_web import NODE_HARNESS

    reps = [
        ("const ctx2d = () => new Proxy(", "const ctx2d = ops => new Proxy("),
        ("return k in t ? t[k] : () => {}; }",
         "if (k in t) return t[k];\n"
         '  return (...a) => { if (k === "clearRect") ops.length = 0;\n'
         '    else if (["moveTo", "lineTo", "arc", "fillText"].includes(k))\n'
         "      ops.push([k, a, t.strokeStyle, t.fillStyle]); }; }"),
        ("this._ctx = ctx2d();", "{ this._ops = []; this._ctx = ctx2d(this._ops); }"),
        ('innerHTMLUsed, ids,', 'innerHTMLUsed, ids, canvas: Object.fromEntries(dumpIds.filter(id => byId.get(id) && '
                                'byId.get(id)._ops).map(id => [id, byId.get(id)._ops])),'),
    ]
    out = NODE_HARNESS
    for old, new in reps:
        assert out.count(old) == 1, f"tests/test_web.py NODE_HARNESS changed: {old!r}"
        out = out.replace(old, new)
    return out


def _render(tmp_path: Path, state: dict, sci: dict) -> dict:
    (tmp_path / "harness.js").write_text(_harness())
    (tmp_path / "dict.json").write_bytes(web.dump_json(web.dictionary_doc()))
    (tmp_path / "sci.json").write_text(json.dumps(sci))
    (tmp_path / "state.json").write_text(json.dumps(state))
    ids = ["si-imuh-note", *STRIPS]
    out = subprocess.run(["node", str(tmp_path / "harness.js"), str(web.HTML_PATH), str(tmp_path / "state.json"),
                          str(tmp_path / "sci.json"), str(tmp_path / "dict.json"), json.dumps(ids)],
                         capture_output=True, text=True, timeout=120)
    res = json.loads(out.stdout.strip().splitlines()[-1])
    assert "fatal" not in res, res
    assert res["tabs"] == 11 and res["errors"] == [] and res["leaks"] == [] and res["innerHTMLUsed"] == 0, res
    return {"note": res["ids"]["si-imuh-note"][0], **res["canvas"]}


def _colours() -> list[str]:
    script = web.HTML_PATH.read_text(encoding="utf-8")
    col = dict(re.findall(r'\b(s[1-3]): "(#[0-9a-f]{6})"', script[script.index("const COL = {"):]))
    return [col["s1"], col["s2"], col["s3"]]                                  # IMU_STRIPS: tilt, |omega|, vib


def _series(ops: list, colour: str) -> dict:
    """The strip's own marks: path points (moveTo / lineTo in its colour) and lone dots."""
    return {"move": [o[1][0] for o in ops if o[0] == "moveTo" and o[2] == colour],
            "line": [o[1][0] for o in ops if o[0] == "lineTo" and o[2] == colour],
            "dots": [o[1][0] for o in ops if o[0] == "arc" and o[3] == colour],
            "text": [str(o[1][0]) for o in ops if o[0] == "fillText"]}


def _history(t, join, tilt, rate, vib, received=None, playback=0, max_points=IMU_HISTORY_LEN) -> dict:
    return {"max_points": max_points, "received": len(t) if received is None else received,
            "playback_excluded": playback, "t": t, "join": join, "tilt_deg": tilt, "rate_abs_dps": rate,
            "vib_rms_mg": vib}


node = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")


@node
def test_strips_draw_the_session_on_one_time_axis(station_snapshots, tmp_path):
    state, sci = station_snapshots
    h = sci["imu_history"]
    n = len(h["t"])
    assert n >= 5 and all(h["join"][1:])
    got = _render(tmp_path, state, sci)
    assert got["note"].startswith(f"Real-time ATT packets: {n} (bound 1,800 points), ")
    assert "0 breaks" in got["note"] and "playback" not in got["note"]
    clock = re.compile(r"^\d\d:\d\d:\d\d$")
    firsts = []
    for k, (cid, colour, unit) in enumerate(zip(STRIPS, _colours(), ("°", "°/s", "mg"), strict=True)):
        s = _series(got[cid], colour)
        assert len(s["move"]) == 1 and len(s["line"]) == n - 1 and s["dots"] == [], (cid, s)
        assert unit in s["text"] and EMPTY not in s["text"]
        # one time axis: the same first and last x in every strip, the time labels on the bottom one
        firsts.append((s["move"][0], s["line"][-1]))
        assert any(clock.match(x) for x in s["text"]) == (k == 2), (cid, s["text"])
    assert firsts[0] == firsts[1] == firsts[2]


@node
def test_n_a_values_and_missing_packets_break_the_line(station_snapshots, tmp_path):
    state, sci = station_snapshots
    sci = {**sci, "imu_history": _history(
        [T0 + 8 * i for i in range(6)], [False, True, True, True, False, True],
        [10.0, 11.0, None, 13.0, 14.0, 15.0], [1.0] * 6, [2.0, None, 2.0, None, 2.0, None],
        received=9, playback=3, max_points=6)}
    got = _render(tmp_path, state, sci)
    assert got["note"].startswith("Real-time ATT packets: the newest 6 of 9 (bound 6 points), ")
    assert "; 1 break between ATT packets that do not follow each other (lost or unreceived packet, new pass, " \
           "reboot)" in got["note"]
    assert "3 recorder playback ATT not drawn (receive times are not session times)" in got["note"]
    tilt, rate, vib = (_series(got[c], col) for c, col in zip(STRIPS, _colours(), strict=True))
    # tilt: 0-1 a line; 2 n/a; 3 alone (the packet after it does not follow): a dot; 4-5 a line
    assert len(tilt["move"]) == 3 and len(tilt["line"]) == 2 and len(tilt["dots"]) == 1
    assert tilt["dots"][0] == tilt["move"][1]
    # |omega|: every value, one break before point 4: 0-3 and 4-5, never a line from 3 to 4
    assert len(rate["move"]) == 2 and len(rate["line"]) == 4 and rate["dots"] == []
    assert max(rate["line"][:3]) < rate["move"][1]
    # vibration: every other value n/a, so no line at all, three dots
    assert vib["line"] == [] and len(vib["dots"]) == 3


@node
def test_empty_and_all_n_a_strips_say_so(station_snapshots, tmp_path):
    state, sci = station_snapshots
    got = _render(tmp_path, state, {**sci, "imu_history": _history([], [], [], [], [], playback=2)})
    assert got["note"] == EMPTY + "; 2 recorder playback ATT not drawn (receive times are not session times)"
    for cid, colour in zip(STRIPS, _colours(), strict=True):
        s = _series(got[cid], colour)
        assert EMPTY in s["text"] and s["move"] == s["line"] == s["dots"] == []
    # points, but never a tilt: n/a with the newest ATT's reason
    st = json.loads(json.dumps(state))
    why = "IMU_F_GRV clear: game rotation vector stale, absent or outside the norm gate"
    st["imu"]["tilt_deg"] = None
    st["imu"]["na"]["grv"] = why
    got = _render(tmp_path, st, {**sci, "imu_history": _history(
        [T0, T0 + 8, T0 + 16], [False, True, True], [None] * 3, [1.0, 2.0, 3.0], [2.0, 2.0, 2.0])})
    tilt = _series(got["si-imuh-tilt"], _colours()[0])
    assert f"n/a in all 3 real-time ATT packets (newest: {why})" in tilt["text"] and tilt["line"] == []
    assert len(_series(got["si-imuh-rate"], _colours()[1])["line"]) == 2
