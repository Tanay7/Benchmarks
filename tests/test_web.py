"""Web server, dashboard security and the ground App Lab entry point (Phase B PB-8.5 WS5).

* HTTP API (dssq/display/web.py) against a recording stub station: every endpoint, the
  commanding token / second-operator AUTH / LAN-exposure rules, the CSRF and DNS-rebinding
  defences, 413 / 411 / 400 / 404 / 415, path traversal and symbolic links on the file
  downloads, json_safe() (NaN never reaches the wire), the request-thread error boundary,
  the connection bound and a bounded shutdown.
* Dashboard (dssq/display/dashboard.html), a static scan: one inline script whose SHA-256 is
  the CSP hash source the server sends, no inline event-handler attribute, no localStorage,
  no dynamic innerHTML, no external resource; untrusted texts are served unchanged.
* A real GroundStation fed by the v3 simulator behind start_web(): /api/state, /api/science,
  /metrics, /api/dictionary, /api/xtce. When Node.js is installed, the page script renders
  all 11 tabs against those snapshots in a minimal DOM (and with injected markup in every
  RF-derived text) with no render error and no innerHTML use.
* dssq-groundstation/python/main.py: prepare() on a temporary App folder (PB-6.2 (a)) and a
  subprocess run of the App layout with a fake arduino.app_utils (PB-6.2 (b)).
"""
import base64
import contextlib
import hashlib
import http.client
import importlib.util
import json
import math
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from dssq import config as station_config
from dssq.display import web
from dssq.gds import json_safe

ROOT = Path(__file__).resolve().parents[1]
GROUND = ROOT / "dssq-groundstation"                    # the ground App folder
MAIN_PY = GROUND / "python" / "main.py"
XSS = "<img src=x onerror=alert(1)>"
TOKEN = "t0k3n-for-tests"
AUTH = "second-operator"


# =============================================================================
# Stub station and HTTP helper
# =============================================================================
def base_snapshot():
    return {"schema": 4, "version": "3.0.0", "utc": 1.8e9, "station": "DSS-Q1", "spacecraft": "VGQ-1",
            "alarm": "NONE", "alarm_reasons": [], "beacon": {"level": 1, "name": "INTERESTING"},
            "rx": {"state": {"los": False, "passes": 1, "hibernating": False},
                   "rf": {"rssi_dbm": -101.5, "sensitivity_dbm": -132.0, "noise_bw_hz": 125000.0, "nan": float("nan"),
                          "inf": float("inf"), "freq_err_hz": {"mean": 188.3, "sem": 10.6, "n": 68},
                          "afc": {"correction_hz": 5992, "estimate_hz": 6301.0, "state": "TRACK"},
                          "packet": {"crc_ok": True}},
                   "decoder": {"rs_rescued": 3, "rice_errors": 0, "sdls_rejected": {"BAD_MAC": 1, "REPLAY": 0}},
                   "frames": {"ok": 10, "lost": 1, "by_format": {"TM": 10}, "per_vc": {"0": {"frames": 4, "lost": 0}},
                              "fer_10m_ci": {"lost": 1, "received": 10, "fer": 1 / 11, "confidence": 0.95,
                                             "two_sided_lo": 0.0023, "two_sided_hi": 0.41, "one_sided_hi": 0.36}},
                   "budget": {"fspl_db": 107.1182, "uplink": {"gs_power_dbm": 33.0}}},
            "radio": {"packet_mode": True},              # an SX126x station: AFC and frequency-error gauges
            "station_clock": {"synced": False},
            "link": {"sdls": {"tm_frames_ok": 9, "tm_auth_fail": 1, "tm_replay": 0, "tm_unprotected": 0,
                              "tm_epoch_regressed": 0, "tc_service": "AE"},
                     "ident": {"text": XSS, "t": 1.0, "count": 1}},
            "cfdp": {"counters": {"pdus": 5, "malformed": 0}, "transactions": [{"name": XSS}]},
            "health": {"stage_errors": {"ingest": 2}, "tdm_rejected": 0},
            "apids": {"HK": {"count": 3, "lost": 0, "iat_s": 10.0, "age_s": 1.0}},
            "tlm": {"HK": {"values": {"bus_temp": 31.84, "fsw_mode": 2, "flag": True}}},
            "evrs": [{"severity": "WARN", "event_id": 0x0701, "text": XSS}],
            "events": [{"t": 1.0, "level": "WARN", "source": "S/C EVR", "text": XSS}],
            "console": [[1.0, XSS]],
            "pending_confirm": [{"id": 1, "text": XSS, "kind": "command", "confirmed": False, "authorized": True}]}


class StubStation:
    """The StationApi surface of dssq.gds.GroundStation; records every call."""

    def __init__(self, tmp: Path, *, token: str = "", auth: str = ""):
        self.cfg = {"gds": {"http_token": token}, "commanding": {"auth_token": auth}}
        self.files_dir = tmp / "files"
        self.tdm_dir = tmp / "tdm"
        self.exports_dir = tmp / "exports"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.tdm_dir.mkdir(parents=True, exist_ok=True)
        self.calls: list[tuple] = []
        self.ev: list[tuple] = []
        self.snap = base_snapshot()
        self.fail = False

    def event(self, level, source, text, t=None):
        self.ev.append((level, source, text))

    def snapshot(self):
        self.calls.append(("snapshot",))
        if self.fail:
            raise RuntimeError("injected failure")
        return self.snap

    def science_snapshot(self):
        self.calls.append(("science_snapshot",))
        return {"spectral": {"available": False, "x": float("nan")}, "spectral_history": {}, "spectral_burst": None,
                "spectral_skymap": {}, "spectral_library": {}, "cfg": {}, "led_limits": {}}

    def submit_command(self, text, source="web"):
        self.calls.append(("submit_command", text, source))
        return {"status": "QUEUED", "id": 7, "text": text}

    def confirm(self, cid, role="CONFIRM"):
        self.calls.append(("confirm", cid, role))
        return {"status": "QUEUED", "id": cid}

    def cancel(self, cid):
        self.calls.append(("cancel", cid))

    def seq_check(self, text, first_slot=0):
        self.calls.append(("seq_check", text, first_slot))
        return {"status": "OK", "entries": []}

    def seq_uplink(self, text, first_slot=0, source="web"):
        self.calls.append(("seq_uplink", text, first_slot, source))
        return {"status": "CONFIRM", "id": 3}

    def magcal_action(self, action, unit, source="web"):
        self.calls.append(("magcal_action", action, unit, source))
        return {"status": "OK"}

    def tdm_write(self, source="web"):
        self.calls.append(("tdm_write", source))
        return {"status": "OK", "path": None, "message": "written"}

    def export_csv(self, t_from, t_to, what, source="web"):
        self.calls.append(("export_csv", t_from, t_to, list(what), source))
        return {"status": "OK", "files": []}

    def xtce_bytes(self):
        return b"<?xml version='1.0' encoding='UTF-8'?><SpaceSystem name='VGQ-1'/>"


def request(port, method, path, body=None, headers=None, raw=None, ctype="application/json"):
    """(status, headers with lower-case names, body octets). The path is sent verbatim."""
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    h = {} if data is None or ctype is None else {"Content-Type": ctype}
    h.update(headers or {})
    try:
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
    finally:
        c.close()


def strict_json(b: bytes):
    def bad(name):
        raise ValueError(f"non-standard JSON constant {name}")
    return json.loads(b, parse_constant=bad)


@pytest.fixture
def make_server(tmp_path):
    servers = []

    def make(**kw):
        stub = StubStation(tmp_path, token=kw.pop("token", ""), auth=kw.pop("auth", ""))
        if "allowed_hosts" in kw:
            stub.cfg["gds"]["http_allowed_hosts"] = kw.pop("allowed_hosts")
        srv = web.start_web(stub, kw.pop("host", "127.0.0.1"), 0, **kw)
        servers.append(srv)
        return srv.server_address[1], stub, srv

    yield make
    for s in servers:
        s.shutdown()
        s.server_close()


# =============================================================================
# GET endpoints
# =============================================================================
def test_every_get_endpoint(make_server):
    port, _stub, _ = make_server()
    st, h, body = request(port, "GET", "/")
    assert st == 200 and h["content-type"].startswith("text/html") and b"DSS-Q1 Mission Operations" in body
    assert request(port, "GET", "/index.html")[0] == 200
    for path in ("/api/state", "/api/state?x=1"):
        st, h, body = request(port, "GET", path)
        s = strict_json(body)                                     # NaN / inf became null
        assert st == 200 and h["content-type"] == "application/json" and "science" not in s
        assert s["rx"]["rf"]["nan"] is None and s["rx"]["rf"]["inf"] is None
    sci = strict_json(request(port, "GET", "/api/science")[2])
    assert {"spectral", "spectral_history", "spectral_burst", "spectral_skymap", "spectral_library"} <= set(sci)
    d = strict_json(request(port, "GET", "/api/dictionary")[2])
    assert d["commands"]["SPECCFG"]["opcode"] == 0x16 and d["commands"]["SEQLOAD"]["builder_only"]
    assert d["commands"]["AIRRATE"]["hazardous"] and not d["commands"]["AIRRATE"]["stored_ok"]
    assert d["commands"]["NOOP"]["stored_ok"] and "UNLOCK" in d["directives"] and "TDM" in d["ground_directives"]
    assert d["packets"]["16"]["name"] == "HK" and len(d["packets"]["16"]["fields"]) == 43
    assert d["packets"]["48"] == {"name": "CFDP", "variable": True, "fields": [], "desc": web.VARIABLE_DESC[0x030]}
    sp = d["spectral"]
    assert sp["led_max_code"] == {"white": 3, "IR": 2, "UV": 0} and sp["led_cur_allowed"] == list(range(12))
    assert sp["n_range"]["1"] == [2, 4] and len(sp["planck_locus"]) > 50 and len(sp["spectrum_locus"]) > 50
    red = [p[1] for p in sp["spectrum_locus"] if p[0] >= 600]                     # drawn without a red-end hook
    assert red == sorted(red) and red[-1] > 0.72
    assert d["enums"]["BEACON_LEVELS"]["3"] == "URGENT" and d["field_enums"]["HK.beacon_level"] == "BEACON_LEVELS"
    st, h, body = request(port, "GET", "/api/xtce")
    assert st == 200 and h["content-type"].startswith("application/xml") and body.startswith(b"<?xml")
    assert h["content-disposition"] == 'attachment; filename="vgq1.xtce.xml"'
    st, h, body = request(port, "GET", "/metrics")
    assert st == 200 and h["content-type"].startswith("text/plain; version=0.0.4")
    assert strict_json(request(port, "GET", "/api/exports")[2]) == []
    for path in ("/nope", "/api", "/api/statex", "/api/files/", "/api/tdm/", "/api/exports/"):
        st, _, body = request(port, "GET", path)
        assert st == 404 and "error" in strict_json(body)


def test_common_headers_and_dashboard_csp(make_server):
    port, _, _ = make_server()
    for path in ("/", "/api/state", "/metrics", "/nope"):
        _, h, _ = request(port, "GET", path)
        assert h["cache-control"] == "no-store" and h["x-content-type-options"] == "nosniff"
        assert h["x-frame-options"] == "DENY" and "content-security-policy" in h
    _, h, body = request(port, "GET", "/")
    csp = h["content-security-policy"]
    m = re.search(r"script-src 'sha256-([A-Za-z0-9+/=]+)'", csp)
    scripts = re.findall(rb"<script\b[^>]*>(.*?)</script\s*>", body, re.S | re.I)
    assert m and len(scripts) == 1 and body.lower().count(b"<script") == 1
    assert m.group(1) == base64.b64encode(hashlib.sha256(scripts[0]).digest()).decode()
    for part in ("default-src 'none'", "connect-src 'self'", "base-uri 'none'", "form-action 'none'",
                 "frame-ancestors 'none'", "style-src 'unsafe-inline'", "img-src 'self' data:"):
        assert part in csp
    assert "unsafe-inline" not in csp.split("script-src", 1)[1].split(";", 1)[0]


def test_file_and_tdm_listing_and_download(make_server, tmp_path):
    port, stub, _ = make_server()
    (stub.files_dir / "ssr_p1_267.bin").write_bytes(b"\x01\x02\x03")
    (stub.files_dir / "ssr_p1_300.bin.partial").write_bytes(b"\x00" * 8)
    (stub.files_dir / "ssr_p1_300.bin.json").write_text(json.dumps({"state": "INCOMPLETE"}))
    (stub.files_dir / "busy.bin.tmp").write_bytes(b"x")                 # an atomic write in progress
    (stub.files_dir / ".hidden").write_bytes(b"x")
    (stub.tdm_dir / "VGQ1_DSS-Q1_20270115T080001Z.kvn").write_text("CCSDS_TDM_VERS = 2.0\n")
    (stub.tdm_dir / "notes.txt").write_text("x")
    outside = tmp_path / "secret.txt"
    outside.write_text("SECRET")
    os.symlink(outside, stub.files_dir / "link.bin")
    os.symlink(outside, stub.tdm_dir / "VGQ1_link.kvn")
    files = {f["name"]: f for f in strict_json(request(port, "GET", "/api/files")[2])}
    assert set(files) == {"ssr_p1_267.bin", "ssr_p1_300.bin.partial", "ssr_p1_300.bin.json"}
    assert files["ssr_p1_267.bin"]["state"] == "COMPLETE" and files["ssr_p1_267.bin"]["size"] == 3
    assert files["ssr_p1_300.bin.partial"]["state"] == "INCOMPLETE" == files["ssr_p1_300.bin.json"]["state"]
    tdm = strict_json(request(port, "GET", "/api/tdm")[2])
    assert [f["name"] for f in tdm] == ["VGQ1_DSS-Q1_20270115T080001Z.kvn"]
    st, h, body = request(port, "GET", "/api/files/ssr_p1_267.bin")
    assert st == 200 and body == b"\x01\x02\x03" and h["content-type"] == "application/octet-stream"
    assert h["content-disposition"] == 'attachment; filename="ssr_p1_267.bin"'
    st, h, body = request(port, "GET", "/api/tdm/VGQ1_DSS-Q1_20270115T080001Z.kvn")
    assert st == 200 and body.startswith(b"CCSDS_TDM_VERS") and h["content-type"].startswith("text/plain")
    for path in ("/api/files/../secret.txt", "/api/files/..%2fsecret.txt", "/api/files/%2e%2e%2fsecret.txt",
                 "/api/files/..", "/api/files/.hidden", "/api/files/link.bin", "/api/files/busy.bin.tmp",
                 "/api/files/%2Fetc%2Fpasswd", "/api/files//etc/passwd", "/api/files/a/b", "/api/files/" + "a" * 200,
                 "/api/tdm/../../secret.txt", "/api/tdm/VGQ1_..%2f..%2fsecret.kvn", "/api/tdm/VGQ1_link.kvn",
                 "/api/tdm/notes.txt", "/api/tdm/VGQ1_missing.kvn", "/api/files/missing.bin"):
        st, _, body = request(port, "GET", path)
        assert st == 404 and b"SECRET" not in body, path


# =============================================================================
# POST endpoints and the commanding rules
# =============================================================================
def test_every_post_endpoint_reaches_the_station(make_server):
    port, stub, _ = make_server()
    cases = [("/api/cmd", {"text": "NOOP"}, ("submit_command", "NOOP", "web")),
             ("/api/confirm", {"id": 4, "role": "confirm"}, ("confirm", 4, "CONFIRM")),
             ("/api/confirm", {"id": 4, "role": "AUTH"}, ("confirm", 4, "AUTH")),
             ("/api/cancel", {"id": 4}, ("cancel", 4)),
             ("/api/seq/check", {"text": "T+00:00:10 NOOP\n", "first_slot": 2}, ("seq_check", "T+00:00:10 NOOP\n", 2)),
             ("/api/seq/uplink", {"text": "T+00:00:10 NOOP\n"}, ("seq_uplink", "T+00:00:10 NOOP\n", 0, "web")),
             ("/api/magcal", {"action": "FIT", "unit": 1}, ("magcal_action", "fit", 1, "web")),
             ("/api/tdm/write", {}, ("tdm_write", "web")),
             ("/api/export", {"from": "2027-01-15T08:00:00Z", "to": 1.8e9 + 60, "what": ["rf", "events"]},
              ("export_csv", "2027-01-15T08:00:00Z", 1.8e9 + 60, ["rf", "events"], "web"))]
    for path, body, call in cases:
        st, _, out = request(port, "POST", path, body)
        assert st == 200, (path, out)
        assert stub.calls[-1] == call
        strict_json(out)
    assert strict_json(request(port, "POST", "/api/cancel", {"id": 9})[2]) == {"status": "CANCELLED", "id": 9}
    st, _, _ = request(port, "POST", "/api/tdm/write", raw=b"")                   # empty body = {}
    assert st == 200


def test_token_and_exposure_rules(make_server):
    port, stub, _ = make_server(exposed=True)                                   # LAN-exposed, no token configured
    st, _, body = request(port, "POST", "/api/cmd", {"text": "NOOP"})
    assert st == 403 and strict_json(body)["error"] == "commanding disabled: set [gds] http_token"
    assert request(port, "GET", "/api/state")[0] == 200                         # read-only dashboard still works
    assert not [c for c in stub.calls if c[0] == "submit_command"]
    assert any(lvl == "WARN" and "commanding disabled" in text for lvl, _, text in stub.ev)
    port, stub, _ = make_server(token=TOKEN, exposed=True)
    for hdr in ({}, {"X-DSSQ-Token": "wrong"}, {"X-DSSQ-Token": TOKEN + "x"}, {"X-DSSQ-Token": ""}):
        st, _, body = request(port, "POST", "/api/cmd", {"text": "NOOP"}, hdr)
        assert st == 403 and "X-DSSQ-Token" in strict_json(body)["error"]
    st, _, _ = request(port, "POST", "/api/cmd", {"text": "NOOP"}, {"X-DSSQ-Token": TOKEN})
    assert st == 200 and stub.calls[-1] == ("submit_command", "NOOP", "web")
    port, stub, _ = make_server()                                               # loopback, no token: open (v2)
    assert request(port, "POST", "/api/cmd", {"text": "PING 1"})[0] == 200
    port, stub, _ = make_server(token=TOKEN)                                    # loopback with a token: required
    assert request(port, "POST", "/api/cmd", {"text": "PING 1"})[0] == 403


def test_second_operator_auth_header(make_server):
    port, stub, _ = make_server(token=TOKEN, auth=AUTH)
    tok = {"X-DSSQ-Token": TOKEN}
    for hdr in (tok, {**tok, "X-DSSQ-Auth": "nope"}):
        st, _, body = request(port, "POST", "/api/confirm", {"id": 1, "role": "AUTH"}, hdr)
        assert st == 403 and "X-DSSQ-Auth" in strict_json(body)["error"]
    assert not [c for c in stub.calls if c[0] == "confirm"]
    assert request(port, "POST", "/api/confirm", {"id": 1, "role": "AUTH"}, {**tok, "X-DSSQ-Auth": AUTH})[0] == 200
    assert stub.calls[-1] == ("confirm", 1, "AUTH")
    assert request(port, "POST", "/api/confirm", {"id": 1, "role": "CONFIRM"}, tok)[0] == 200   # CONFIRM: token only
    st, _, _ = request(port, "POST", "/api/confirm", {"id": 1, "role": "AUTH"}, {"X-DSSQ-Auth": AUTH})
    assert st == 403                                                            # AUTH never replaces the token


def test_bad_requests(make_server):
    port, stub, _ = make_server()
    st, _, body = request(port, "POST", "/api/cmd", raw=b"x" * (web.MAX_BODY + 1))
    assert st == 413 and "larger" in strict_json(body)["error"]
    for raw in (b"{", b"[1, 2]", b'"NOOP"', b'{"text": NaN}', b'{"id": Infinity}', b"\xff\xfe", b"[" * 100_000):
        st, _, body = request(port, "POST", "/api/cmd", raw=raw)
        assert st == 400, raw[:20]
        strict_json(body)
    for path, body in (("/api/cmd", {"text": 5}), ("/api/cmd", {}), ("/api/cmd", {"text": "x" * 2000}),
                       ("/api/confirm", {"id": "1"}), ("/api/confirm", {"id": True}), ("/api/confirm", {"id": -1}),
                       ("/api/confirm", {"id": 1, "role": "ROOT"}), ("/api/cancel", {"id": 1.5}),
                       ("/api/seq/check", {"text": "x", "first_slot": 32}), ("/api/seq/uplink", {"text": None}),
                       ("/api/seq/check", {"text": "x" * (web.SEQ_TEXT_MAX + 1)}),
                       ("/api/magcal", {"action": "explode", "unit": 0}),
                       ("/api/magcal", {"action": "fit", "unit": 2})):
        st, _, out = request(port, "POST", path, body)
        assert st == 400, (path, body, out)
    assert request(port, "POST", "/api/nope", {"text": "NOOP"})[0] == 404
    assert request(port, "PUT", "/api/cmd", {"text": "NOOP"})[0] == 501
    assert request(port, "DELETE", "/")[0] == 501
    assert not [c for c in stub.calls if c[0] != "snapshot"]                  # nothing reached the station
    # A body shorter than its Content-Length: the socket times out or the server answers 400.
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    s.sendall(b"POST /api/cmd HTTP/1.0\r\nContent-Type: application/json\r\nContent-Length: 50\r\n\r\n{}")
    s.shutdown(socket.SHUT_WR)
    resp = s.recv(4096)
    s.close()
    assert resp.startswith(b"HTTP/1.0 400")
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    s.sendall(b"POST /api/cmd HTTP/1.0\r\nContent-Type: application/json\r\n"
              b"Transfer-Encoding: chunked\r\n\r\n0\r\n\r\n")
    assert s.recv(4096).startswith(b"HTTP/1.0 411")
    s.close()


def test_csrf_and_dns_rebinding_defences(make_server):
    port, stub, _ = make_server()
    st, _, body = request(port, "POST", "/api/cmd", {"text": "NOOP"}, ctype="text/plain")
    assert st == 415 and "application/json" in strict_json(body)["error"]
    st, _, _ = request(port, "POST", "/api/cmd", {"text": "NOOP"}, ctype=None)
    assert st == 415
    for origin in ("http://evil.example", "null", f"http://127.0.0.1:{port + 1}"):
        st, _, body = request(port, "POST", "/api/cmd", {"text": "NOOP"}, {"Origin": origin})
        assert st == 403 and "cross-origin" in strict_json(body)["error"], origin
    assert request(port, "POST", "/api/cmd", {"text": "NOOP"}, {"Origin": f"http://127.0.0.1:{port}"})[0] == 200
    for host in ("evil.example", f"evil.example:{port}", "192.168.1.5:8080"):
        st, _, body = request(port, "GET", "/api/state", headers={"Host": host})
        assert st == 403 and "Host" in strict_json(body)["error"], host
    for host in (f"localhost:{port}", f"127.0.0.1:{port}", "[::1]:8080", "localhost"):
        assert request(port, "GET", "/api/state", headers={"Host": host})[0] == 200, host
    assert [c for c in stub.calls if c[0] == "submit_command"] == [("submit_command", "NOOP", "web")]


def test_untrusted_texts_are_served_unchanged(make_server):
    port, stub, _ = make_server()
    s = strict_json(request(port, "GET", "/api/state")[2])
    assert s["evrs"][0]["text"] == XSS and s["events"][0]["text"] == XSS and s["console"][0][1] == XSS
    assert s["pending_confirm"][0]["text"] == XSS and s["link"]["ident"]["text"] == XSS
    assert s["cfdp"]["transactions"][0]["name"] == XSS
    (stub.files_dir / "x.bin").write_bytes(b"1")
    _, h, _ = request(port, "GET", "/api/files")
    assert h["content-type"] == "application/json" and "content-security-policy" in h
    assert XSS.encode() not in request(port, "GET", "/")[2]                     # the page never embeds data


def test_error_boundary_and_bounded_resources(make_server):
    port, stub, srv = make_server(max_clients=2)
    stub.fail = True
    st, _, body = request(port, "GET", "/api/state")
    assert st == 500 and strict_json(body)["error"].startswith("internal error")
    assert request(port, "GET", "/metrics")[0] == 500
    stub.fail = False
    assert request(port, "GET", "/api/state")[0] == 200                         # the server thread survived
    idle = [socket.create_connection(("127.0.0.1", port), timeout=5) for _ in range(2)]
    deadline = time.time() + 5
    while time.time() < deadline and srv._slots._value:                         # both idle ones hold a slot
        time.sleep(0.01)
    third = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        third.sendall(b"GET /api/state HTTP/1.0\r\n\r\n")
        try:
            data = third.recv(4096)
        except ConnectionResetError:
            data = b""
        assert data == b""                                                      # refused, not queued
    finally:
        third.close()
    for s in idle:
        s.close()
    deadline = time.time() + 5
    while time.time() < deadline:
        try:                                     # until the slots are released the server refuses by
            if request(port, "GET", "/api/state")[0] == 200:     # closing: RemoteDisconnected / reset
                break
        except (ConnectionError, http.client.HTTPException):
            pass
        time.sleep(0.05)
    assert request(port, "GET", "/api/state")[0] == 200
    hold = socket.create_connection(("127.0.0.1", port), timeout=5)            # a stalled client
    t0 = time.monotonic()
    srv.shutdown()
    srv.server_close()
    hold.close()
    assert time.monotonic() - t0 < 1.5                                          # Runner.close() budget


def test_slow_clients_cannot_hold_the_slots(make_server, monkeypatch):
    """C38: a client trickling its request octet by octet keeps no slot past the request
    deadline (the per-receive socket timeout alone never expires while octets arrive)."""
    monkeypatch.setattr(web, "REQUEST_DEADLINE_S", 1.0)
    port, _stub, _srv = make_server(max_clients=2)
    slow = [socket.create_connection(("127.0.0.1", port), timeout=5) for _ in range(2)]
    for s in slow:
        s.sendall(b"POST /api/cmd HTTP/1.0\r\nContent-Type: application/json\r\nContent-Length: 100000\r\n\r\n")
    t0 = time.monotonic()
    answers, ok = [], False
    try:
        while time.monotonic() - t0 < 4.0:
            for s in slow:
                with contextlib.suppress(OSError):
                    s.sendall(b" ")                                              # one octet at a time
            try:
                ok = request(port, "GET", "/api/state")[0] == 200
            except (ConnectionError, http.client.HTTPException):
                ok = False
            if ok:
                break
            time.sleep(0.2)
        for s in slow:
            with contextlib.suppress(OSError):
                answers.append(s.recv(4096))
    finally:
        for s in slow:
            s.close()
    assert ok and time.monotonic() - t0 < 3.0                                    # slots freed by the deadline
    assert any(a.startswith(b"HTTP/1.0 408") for a in answers), answers


def test_lan_server_refuses_foreign_host_names(make_server):
    """C39: a 0.0.0.0-bound server answers an IP literal, a LAN-only name (single label,
    <name>.local), its own host name or a configured name; a rebound public DNS name is
    refused on GET and POST."""
    port, stub, _ = make_server(host="0.0.0.0", token="t0k", allowed_hosts=["station.example.org"])
    me = socket.gethostname()
    for host in ("evil.example", f"evil.example:{port}", "station.example.org.evil.example", "local.evil.example"):
        st, _, body = request(port, "GET", "/api/state", headers={"Host": host})
        assert st == 403 and "http_allowed_hosts" in strict_json(body)["error"], host
        st, _, _ = request(port, "POST", "/api/cmd", {"text": "NOOP"}, {"Host": host, "X-DSSQ-Token": "t0k"})
        assert st == 403, host
    for host in (f"127.0.0.1:{port}", f"192.168.1.5:{port}", "[fe80::1]:8080", f"{me}:{port}",
                 "dssq-ground.local:8080", "dssq-ground", "STATION.example.org", "localhost"):
        assert request(port, "GET", "/api/state", headers={"Host": host})[0] == 200, host
    assert not [c for c in stub.calls if c[0] == "submit_command"]
    from dssq.config import is_loopback
    assert is_loopback("127.0.0.2") and is_loopback("::1") and not is_loopback("0.0.0.0")


def test_prometheus_exposition_format():
    s = json_safe(base_snapshot())
    text = web.prometheus(s)
    families, order = {}, []
    for line in text.splitlines():
        if line.startswith("# HELP "):
            fam = line.split()[2]
            assert fam not in families, f"duplicate family {fam}"
            families[fam] = 0
            order.append(fam)
            continue
        if line.startswith("# TYPE "):
            assert line.split()[2] == order[-1] and line.endswith(" gauge")
            continue
        m = re.fullmatch(r"(dssq_[a-zA-Z0-9_]+)(\{.*\})? (\S+)", line)
        assert m, line
        assert m.group(1) == order[-1], f"{m.group(1)} not contiguous"
        float(m.group(3))
    for fam in ("dssq_los", "dssq_rf_rssi_dbm", "dssq_rf_sensitivity_dbm", "dssq_rf_noise_bw_hz", "dssq_frames_lost",
                "dssq_rf_freq_err_hz_mean", "dssq_rf_afc_correction_hz", "dssq_rf_afc_estimate_hz",
                "dssq_decoder_rs_rescued", "dssq_decoder_rice_errors", "dssq_decoder_sdls_rejected",
                "dssq_budget_fspl_db", "dssq_budget_uplink_gs_power_dbm", "dssq_beacon_level", "dssq_sdls_tm_frames_ok",
                "dssq_sdls_tm_auth_fail", "dssq_sdls_tm_replay", "dssq_sdls_tm_unprotected",
                "dssq_sdls_tm_epoch_regressed",
                "dssq_cfdp_pdus", "dssq_stage_errors", "dssq_tlm", "dssq_frames_by_format", "dssq_apid_count"):
        assert fam in families, fam
    assert 'dssq_stage_errors{stage="ingest"} 2' in text and 'dssq_decoder_sdls_rejected{verdict="BAD_MAC"} 1' in text
    assert 'dssq_tlm{packet="HK",param="bus_temp"} 31.84' in text and "param=\"flag\"" not in text
    assert "dssq_rf_nan" not in text and "dssq_rf_inf" not in text
    # D-76: FER bounds, the station clock (0 = not synchronised), the SX126x CRC verdict
    assert "\ndssq_frames_fer_10m_ci_two_sided_hi 0.41\n" in text
    assert "\ndssq_frames_fer_10m_ci_one_sided_hi 0.36\n" in text
    assert "\ndssq_station_clock_synced 0\n" in text and "\ndssq_rf_packet_crc_ok 1\n" in text
    escaped = web.prometheus({"tlm": {'a"b\\c\nd': {"values": {"x": 1}}}})
    assert 'dssq_tlm{packet="a\\"b\\\\c\\nd",param="x"} 1' in escaped


def test_metrics_follow_what_the_receiver_can_measure():
    """F-P1-10 (D-76): the last reception's gauges are absent during LOS; an E22 station exports
    no SX126x-only quantity (Doppler block, CRC verdict); an unknown station clock is absent."""
    s = json_safe(base_snapshot())
    s["radio"] = {"packet_mode": False}
    s["rx"]["rf"].update(margin_est_db=12.0, doppler={"offset_cal_hz": 0.0, "calibrated": False})
    s["station_clock"] = {"synced": None}
    text = web.prometheus(s)
    assert "\ndssq_rf_rssi_dbm -101.5\n" in text and "\ndssq_rf_margin_est_db 12.0\n" in text
    assert "dssq_rf_packet_crc_ok" not in text and "dssq_rf_doppler" not in text and "dssq_rf_afc" not in text
    assert "dssq_station_clock_synced" not in text
    s["rx"]["state"]["los"] = True
    text = web.prometheus(s)
    assert "dssq_rf_rssi_dbm" not in text and "dssq_rf_margin_est_db" not in text
    assert "\ndssq_rf_sensitivity_dbm -132.0\n" in text and "\ndssq_los 1\n" in text           # configuration stays


def test_dashboard_overload_text_is_the_gds_text():
    """The page warns with exactly the GDS's operator text (rxstats.OVERLOAD_TEXT)."""
    from dssq import rxstats
    assert f'const OVERLOAD_TEXT = "{rxstats.OVERLOAD_TEXT}";' in _page()[1]
    assert "+10 dBm" in rxstats.OVERLOAD_TEXT and "too strong to measure" in rxstats.OVERLOAD_TEXT


def test_start_web_refuses_a_page_with_a_second_script(tmp_path):
    bad = tmp_path / "page.html"
    bad.write_text("<html><script>a()</script><script src='x.js'></script></html>")
    with pytest.raises(ValueError, match="exactly one"):
        web.start_web(StubStation(tmp_path), "127.0.0.1", 0, html_path=bad)
    assert web.inline_script_hash("<script>\r\nx\r</script>") == web.inline_script_hash("<script>\nx\n</script>")


# =============================================================================
# Dashboard static security scan (PB-5.7)
# =============================================================================
def _page():
    html = web.HTML_PATH.read_text(encoding="utf-8")
    scripts = re.findall(r"<script\b([^>]*)>(.*?)</script\s*>", html, re.S | re.I)
    assert len(scripts) == 1 and not scripts[0][0].strip() and html.lower().count("<script") == 1
    return html, scripts[0][1], html.replace(scripts[0][1], "")


def test_dashboard_has_no_external_resource_and_no_inline_handler():
    html, script, markup = _page()
    assert "http://" not in html and "https://" not in html                   # no CDN, no web font
    assert not re.search(r"""(?:src|href|action)\s*=\s*["']?\s*//""", html)
    assert "@import" not in html and "url(" not in markup.replace("data:", "")
    assert not re.search(r"<[a-zA-Z][^>]*\son[a-z]+\s*=", markup, re.I)        # no inline on*= attribute
    assert not re.search(r"\.on[a-z]+\s*=(?!=)", script)                       # no el.onclick = ... either
    assert not re.search(r"""setAttribute\(\s*["']on""", script, re.I)
    assert "javascript:" not in html.lower()
    assert not re.search(r"\beval\s*\(|new\s+Function\s*\(|setTimeout\(\s*[\"']|setInterval\(\s*[\"']", script)


def test_dashboard_token_handling_and_text_only_rendering():
    html, script, _ = _page()
    assert "localStorage" not in html                                          # token: sessionStorage only
    assert "sessionStorage" in script and "X-DSSQ-Token" in script
    # the second-operator secret is prompted per AUTH action and never stored
    auth_uses = [m.start() for m in re.finditer(r"X-DSSQ-Auth", script)]
    assert auth_uses and all("Storage" not in script[max(0, i - 300):i + 300] for i in auth_uses)
    assert len(re.findall(r"function esc\(", script)) == 1                     # the ONE escaping function
    for m in re.finditer(r"\binnerHTML\s*(\+?=)\s*", script):                   # only literal or esc() markup
        rhs = script[m.end():m.end() + 200]
        assert re.match(r"""(["'])(?:(?!\1)[^\\$]|\\.)*\1\s*;|esc\(""", rhs), rhs[:80]
    for api in ("outerHTML", "insertAdjacentHTML", "document.write", "createContextualFragment", "DOMParser"):
        assert api not in script, api
    assert "replaceChildren" in script and "createTextNode" in script


# =============================================================================
# Real GroundStation behind the web server (+ the page script in Node.js)
# =============================================================================
class _Clock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def sleep(self, dt):
        self.t += max(dt, 1e-3)


@pytest.fixture(scope="module")
def sim_station(tmp_path_factory):
    """A GroundStation that received a minute of v3 simulator traffic (SDLS AE both ways,
    a SPECMODE BURST, a MAGCAL collection), on a virtual clock."""
    from dssq.config import load_config
    from dssq.gds import GroundStation, _SimRcu
    from dssq.radioctl import RadioManager
    from dssq.sim import SimSpacecraft

    tmp = tmp_path_factory.mktemp("websim")
    clk = _Clock()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(time, "time", clk)
        mp.setattr(time, "sleep", clk.sleep)
        cfg = load_config()
        cfg["gds"]["archive_dir"] = str(tmp / "archive")
        (tmp / "archive").mkdir(exist_ok=True)
        (tmp / "archive" / "sdls_sn.txt").write_text("0")              # a station whose TC counter is known
        key = tmp / "sdls_key.hex"
        key.write_text(bytes(range(32)).hex() + "\n")
        cfg["sdls"]["key_file"] = str(key)
        cfg["radio"]["air_rate"] = 7
        cfg["science"]["field_ut"] = 50.0
        gs = GroundStation(cfg, archive=True)
        sim = SimSpacecraft(cfg, clock=clk, sdls_key=gs.sdls_tc.key, loss=0.0, max_errors=4, rssi_mean=-95.0,
                            fading_db=1.0, seed=7)
        gs.attach_radio(sim, RadioManager(sim, _SimRcu(sim), clock=clk, sleep=clk.sleep, model=gs.settings.model.name),
                        None)

        def run(seconds):
            end = clk.t + seconds
            while clk.t < end:
                gs.ingest(sim.read(), clk.t)
                for chunk in gs.take_outbox():
                    sim.write(chunk)
                if gs.radio_jobs:
                    gs.run_radio_jobs()
                gs.housekeeping(clk.t)
                clk.t += 0.25

        run(40)
        gs.magcal_action("start", 0)
        gs.submit_command("SPECMODE BURST 5")
        run(50)
        gs.submit_command("SSRCLEAR")                                           # a pending confirmation
        snap, sci = gs.snapshot(), gs.science_snapshot()
    return gs, snap, sci


def test_real_station_through_the_web_server(sim_station):
    gs, _, _ = sim_station
    srv = web.start_web(gs, "127.0.0.1", 0)
    port = srv.server_address[1]
    try:
        s = strict_json(request(port, "GET", "/api/state")[2])
        assert s["schema"] == 4 and "science" not in s and s["rx"]["frames"]["ok"] > 10
        assert s["link"]["sdls"]["tm_frames_ok"] > 0 and s["pending_confirm"][0]["text"] == "SSRCLEAR"
        sci = strict_json(request(port, "GET", "/api/science")[2])
        assert {"spectral", "spectral_history", "spectral_burst", "spectral_skymap", "spectral_library"} <= set(sci)
        assert sci["led_limits"]["max_code"] == {"white": 3, "IR": 2, "UV": 0}
        metrics = request(port, "GET", "/metrics")[2].decode()
        for fam in ("dssq_rf_sensitivity_dbm", "dssq_rf_noise_bw_hz", "dssq_frames_lost", "dssq_beacon_level",
                    "dssq_sdls_tm_frames_ok", "dssq_decoder_rs_rescued", "dssq_decoder_rice_errors", "dssq_cfdp_pdus"):
            assert f"\n{fam} " in metrics or f"\n{fam}{{" in metrics, fam
        assert "dssq_rf_sensitivity_est_dbm" not in metrics and "dssq_frames_lost_mcfc" not in metrics
        st, _, xml = request(port, "GET", "/api/xtce")
        assert st == 200 and b"SpaceSystem" in xml
        assert isinstance(strict_json(request(port, "GET", "/api/tdm")[2]), list)
        assert isinstance(strict_json(request(port, "GET", "/api/files")[2]), list)
    finally:
        srv.shutdown()
        srv.server_close()


NODE_HARNESS = r"""
"use strict";
// Minimal DOM for the dashboard script: element stubs with text-only content; innerHTML and
// inline handler attributes throw. Renders every tab twice and reports render errors.
const fs = require("fs"), vm = require("vm");
const [htmlPath, statePath, sciPath, dictPath, idsJson] = process.argv.slice(2);
const dumpIds = idsJson ? JSON.parse(idsJson) : [];         // elements whose text and class are reported
const html = fs.readFileSync(htmlPath, "utf8");
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const fixtures = {"/api/state": statePath, "/api/science": sciPath, "/api/dictionary": dictPath};
const lists = {"/api/files": [{name: "<img src=x onerror=alert(1)>", size: 1, mtime: 1.8e9, state: "COMPLETE"}],
  "/api/tdm": [], "/api/exports": [{name: "<img src=x onerror=alert(1)>", size: 2, mtime: 1.8e9}]};
let innerHTMLUsed = 0;
const byId = new Map();
const ctx2d = () => new Proxy({}, {get(t, k) { if (k === "measureText") return s => ({width: String(s).length * 6});
  return k in t ? t[k] : () => {}; }, set(t, k, v) { t[k] = v; return true; }});
class Txt { constructor(s) { this.nodeType = 3; this._t = String(s); this.parentNode = null; }
  get textContent() { return this._t; } set textContent(v) { this._t = String(v); } }
class El {
  constructor(tag) { this.nodeType = 1; this.tagName = tag.toUpperCase(); this.childNodes = []; this.parentNode = null;
    this.attrs = {}; this.dataset = {}; this.style = {}; this.className = ""; this.title = ""; this.value = "";
    this.checked = false; const me = this;
    this.classList = {toggle(c, on) { const s = new Set(me.className.split(/\s+/).filter(Boolean));
      if (on) s.add(c); else s.delete(c); me.className = [...s].join(" "); }};
    if (this.tagName === "CANVAS") this._ctx = ctx2d(); }
  set innerHTML(v) { innerHTMLUsed++; throw new Error("innerHTML"); }
  get innerHTML() { return ""; }
  get id() { return this.attrs.id || ""; } set id(v) { this.setAttribute("id", v); }
  setAttribute(k, v) { if (/^on/i.test(k)) throw new Error("handler attribute " + k); this.attrs[k] = String(v);
    if (k === "id") byId.set(String(v), this);
    if (k.startsWith("data-")) this.dataset[k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = String(v); }
  appendChild(c) { c.parentNode = this; this.childNodes.push(c);
    if (c.attrs && c.attrs.id) byId.set(c.attrs.id, c); return c; }
  replaceChildren(...k) { this.childNodes = []; for (const c of k) this.appendChild(c); }
  get textContent() { return this.childNodes.map(c => c.textContent).join(""); }
  set textContent(v) { this.childNodes = []; if (v !== "") this.appendChild(new Txt(v)); }
  get options() { return this.childNodes.filter(c => c.tagName === "OPTION"); }
  getBoundingClientRect() { return {width: 640, height: 220, left: 0, top: 0}; }
  getContext() { return this._ctx || null; }
  addEventListener() {}
  querySelectorAll() { return []; }
  closest() { return null; }
  get offsetWidth() { return 200; } get offsetHeight() { return 60; }
}
for (const m of html.matchAll(/<(\w+)[^>]*?\sid="([^"]+)"/g)) new El(m[1]).setAttribute("id", m[2]);
const sections = [...html.matchAll(/<section data-tab="([^"]+)"/g)].map(m => { const e = new El("section");
  e.dataset.tab = m[1].replace(/&amp;/g, "&"); return e; });
const tabs = sections.map(s => s.dataset.tab);
const document = {getElementById: id => byId.get(id) || null, createElement: t => new El(t),
  createTextNode: s => new Txt(s), addEventListener() {},
  querySelectorAll: sel => sel === "main > section" ? sections : []};
const store = {};
const ctx = {document, console, AbortController, Date, Math, JSON, Promise, Number, String, Object, Array, Set, Map,
  WeakMap, RegExp, Error, isNaN, isFinite, parseInt, encodeURIComponent,
  window: {devicePixelRatio: 2, innerWidth: 1400, innerHeight: 900, addEventListener() {}, prompt: () => null},
  sessionStorage: {getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem() {}},
  fetch: async url => { const u = url.split("?")[0];
    const body = u in fixtures ? JSON.parse(fs.readFileSync(fixtures[u], "utf8")) : lists[u];
    return {ok: body !== undefined, status: body !== undefined ? 200 : 404, json: async () => body}; },
  setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0};
ctx.window.console = console;
vm.createContext(ctx);
(async () => {
  vm.runInContext(script, ctx);
  for (let i = 0; i < 8; i++) await new Promise(r => setImmediate(r));
  const errors = [];
  for (let pass = 0; pass < 2; pass++) for (const t of tabs) {
    vm.runInContext("showTab(" + JSON.stringify(t) + ")", ctx);
    for (let i = 0; i < 6; i++) await new Promise(r => setImmediate(r));
    vm.runInContext("render()", ctx);
    const e = byId.get("err").textContent; if (e) errors.push(t + ": " + e);
  }
  const texts = [];
  const walk = e => { for (const c of e.childNodes) {
    if (c.nodeType === 3) texts.push(c.textContent); else walk(c); } };
  for (const e of byId.values()) walk(e);
  const leaks = texts.filter(t => /\[object Object\]|\bundefined\b|\bNaN\b/.test(t)).slice(0, 5);
  const classes = e => [e.className, ...e.childNodes.filter(c => c.nodeType === 1).flatMap(classes)];
  const ids = Object.fromEntries(dumpIds.map(id => { const e = byId.get(id);
    return [id, e ? [e.textContent, classes(e).filter(Boolean).join(" ")] : null]; }));
  console.log(JSON.stringify({tabs: tabs.length, errors, leaks, innerHTMLUsed, ids,
    injected: texts.filter(t => t.includes("<img src=x onerror=alert(1)>")).length}));
})().catch(e => { console.log(JSON.stringify({fatal: String(e && e.stack || e)})); process.exit(2); });
"""


def test_dashboard_reads_every_snapshot_key(sim_station):
    """Drift guard: the page reads every top-level snapshot() key, labels every rx.rf scalar
    (RF_KEYS: unit and resolution) and shows every science section and spectral key."""
    _, snap, sci = sim_station
    script = _page()[1]
    used = set(re.findall(r"\bS\.(\w+)", script)) | set(re.findall(r"""\bS\[["'](\w+)["']\]""", script))
    assert set(snap) - used == set()
    rf_table = script[script.index("const RF_KEYS"):script.index("const STAT_METRICS")]
    labelled = set(re.findall(r'\["(\w+)", "', rf_table))
    nested = {"packet", "snr_db", "freq_err_hz", "doppler", "afc"}               # shown in their own sections
    assert set(snap["rx"]["rf"]) - nested - labelled == set()
    assert all(re.search(rf'\brf\.{k}\b|\["{k}", "', script) for k in nested)
    assert [k for k in sci if not re.search(rf"\bSCI\.{k}\b", script)] == []
    assert [k for k in sci["spectral"] if not re.search(rf"\.{k}\b|\"{k}\"", script)] == []
    for w in ("1m", "10m", "pass"):                                             # 3 windows x 8 metrics
        assert set(snap["rx"]["windows"][w]) <= set(re.findall(r'\["(\w+)", "[^"]+", "[^"]*", (?:\d|"[RD]")\]', script))


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_dashboard_compares_fusion_and_triad_in_one_frame(tmp_path):
    """C47: q_fus is device -> ENU, q_triad NED -> body (flight spec_attitude); the page
    brings both to body -> NED. A level spacecraft heading 30 deg, perfect sensors: both
    give yaw 30, pitch 0, roll 0, and the angle between them is 0 (it showed 180)."""
    script = _page()[1]
    parts = [re.search(rf"^function {n}\(.*?^}}$", script, re.S | re.M).group(0) for n in ("q2e", "qmul")]
    parts += [re.search(r"^const Q_ENU_TO_NED = .*?;$", script, re.M).group(0)]
    parts += [re.search(rf"^function {n}\(.*?$", script, re.M).group(0) for n in ("fusToNed", "triadToNed")]
    h = math.radians(15.0)
    q_ned = [math.cos(h), 0.0, 0.0, math.sin(h)]                                # body -> NED, heading 30 deg
    # device -> ENU of that attitude (the BHI260 / Android world: x East, y North, z Up)
    q_fus = [0.0, math.sqrt(0.5) * (q_ned[0] + q_ned[3]), math.sqrt(0.5) * (q_ned[0] - q_ned[3]), 0.0]
    assert q_fus == pytest.approx([0.0, 0.8660254, 0.5, 0.0])
    q_tri = [q_ned[0], -q_ned[1], -q_ned[2], -q_ned[3]]                          # NED -> body (triad_ned)
    js = "\n".join(parts) + f"""
const f = fusToNed({q_fus}), t = triadToNed({q_tri});
const dot = Math.abs(f.reduce((s, v, i) => s + v * t[i], 0));
console.log(JSON.stringify({{ef: q2e(...f), et: q2e(...t), angle: 2 * Math.acos(Math.min(1, dot)) * 180 / Math.PI}}));
"""
    (tmp_path / "q.js").write_text(js)
    out = json.loads(subprocess.run(["node", str(tmp_path / "q.js")], capture_output=True, text=True,
                                    timeout=60, check=True).stdout)
    assert out["angle"] == pytest.approx(0.0, abs=1e-3)
    for e in (out["ef"], out["et"]):
        assert e == pytest.approx([30.0, 0.0, 0.0], abs=1e-6)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_dashboard_renders_every_tab_in_node(sim_station, tmp_path):
    _, snap, sci = sim_station
    hostile = json.loads(json.dumps(snap))
    hostile["evrs"].append({"ert": 1.8e9, "scet": None, "sclk": 1.0, "severity": "WARN", "event_id": 0x0701,
                            "text": XSS})
    hostile["events"].append({"t": 1.8e9, "level": "WARN", "source": "S/C EVR", "text": XSS})
    hostile["console"].append([1.8e9, XSS])
    hostile["link"]["ident"]["text"] = XSS
    hostile["pending_confirm"].append({"id": 99, "text": XSS, "kind": "command", "confirmed": False, "authorized": True,
                                       "t": 1.8e9, "expires_s": 60.0})
    hostile["alarm_reasons"].append(XSS)
    harness = tmp_path / "harness.js"
    harness.write_text(NODE_HARNESS)
    (tmp_path / "dict.json").write_bytes(web.dump_json(web.dictionary_doc()))
    (tmp_path / "sci.json").write_text(json.dumps(sci))
    for name, state in (("real", snap), ("hostile", hostile)):
        (tmp_path / "state.json").write_text(json.dumps(state))
        out = subprocess.run(["node", str(harness), str(web.HTML_PATH), str(tmp_path / "state.json"),
                              str(tmp_path / "sci.json"), str(tmp_path / "dict.json")],
                             capture_output=True, text=True, timeout=120)
        res = json.loads(out.stdout.strip().splitlines()[-1])
        assert "fatal" not in res, res
        assert res["tabs"] == 11 and res["errors"] == [], (name, res)
        assert res["leaks"] == [] and res["innerHTMLUsed"] == 0, (name, res)
        if name == "hostile":
            assert res["injected"] >= 6                                         # shown as text, never parsed


def _render(tmp_path, state, sci, ids):
    harness = tmp_path / "harness.js"
    harness.write_text(NODE_HARNESS)
    (tmp_path / "dict.json").write_bytes(web.dump_json(web.dictionary_doc()))
    (tmp_path / "sci.json").write_text(json.dumps(sci))
    (tmp_path / "state.json").write_text(json.dumps(state))
    out = subprocess.run(["node", str(harness), str(web.HTML_PATH), str(tmp_path / "state.json"),
                          str(tmp_path / "sci.json"), str(tmp_path / "dict.json"), json.dumps(ids)],
                         capture_output=True, text=True, timeout=120)
    res = json.loads(out.stdout.strip().splitlines()[-1])
    assert "fatal" not in res, res
    assert res["errors"] == [] and res["leaks"] == [] and res["innerHTMLUsed"] == 0, res
    return res["ids"]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_dashboard_shows_only_what_the_receiver_can_measure(sim_station, tmp_path):
    """D-76 in the rendered page: an overload is never a number, a margin at the noise floor is an
    upper bound and never green, the E22 shows no CRC verdict, SER, BER or Doppler, an
    unsynchronised clock shows in the header, a missing distance is "not configured", every FER
    carries its exact bounds labelled one- or two-sided, and the SX126x path renders."""
    _, snap, sci = sim_station
    ids = ["h-rssi", "h-margin", "h-fer", "h-clock", "ov-tiles", "rx-now", "rx-dec", "rx-dop", "rx-frames", "rx-budget",
           "ls-time", "cm-health"]
    st = json.loads(json.dumps(snap))
    st["rx"]["rf"].update(rssi_state="OVERLOAD", rssi_dbm=None, rssi_raw_dbm=-256, margin_state=None,
                          margin_est_db=None, margin_worst_est_db=None, rssi_overloads=3)
    st["station_clock"] = {"synced": False, "state": "TIME_ERROR", "reason": "TIME_ERROR and STA_UNSYNC",
                           "maxerror_us": 16_000_000, "esterror_us": 16_000_000, "checked_t": st["utc"],
                           "refresh_s": 10.0}
    st["time"]["owlt_s"] = None
    st["rx"]["frames"]["fer_10m_ci"] = {"lost": 0, "received": 178, "fer": 0.0, "confidence": 0.95, "two_sided_lo": 0.0,
                                        "two_sided_hi": 0.0205, "one_sided_hi": 0.0167}
    got = _render(tmp_path, st, sci, ids)
    assert got["h-rssi"] == ["OVERLOAD", "bad"] and "-256" not in got["rx-now"][0].split("reads")[0]
    assert "too strong to measure" in got["ov-tiles"][0] and got["h-margin"][0] == "n/a"
    assert got["h-clock"] == ["NOT SYNCED", "warn"] and "TIME_ERROR" in got["cm-health"][0]
    assert got["h-fer"][0] == "0/178 ≤ 1.67 %"
    assert "≤ 1.67 % (95 %, one-sided) · two-sided 95 % [0, 2.05 %]" in got["rx-frames"][0]
    assert "not configured" in got["ls-time"][0]
    assert "CRC report" in got["rx-now"][0] and "n/a on the E22 path" in got["rx-dec"][0] and "n/a" in got["rx-dop"][0]
    # the noise floor: an upper bound, amber, never green
    st = json.loads(json.dumps(snap))
    st["rx"]["rf"].update(margin_state="NOISE_FLOOR", margin_est_db=None, margin_worst_est_db=None, margin_max_db=10.0,
                          margin_worst_max_db=8.0, margin_basis="not measurable")
    got = _render(tmp_path, st, sci, ids)
    assert got["h-margin"][0] == "≤ 8 dB" and got["h-margin"][1] == "warn"
    assert "not measurable (at the noise floor)" in got["ov-tiles"][0]
    # not checked against the noise floor: at most amber
    st["rx"]["rf"].update(margin_state="NOISE_UNKNOWN", margin_worst_est_db=30.0, margin_est_db=32.0)
    assert _render(tmp_path, st, sci, ids)["h-margin"] == ["30 dB", "warn"]
    st["rx"]["rf"]["margin_state"] = "SIGNAL"
    assert _render(tmp_path, st, sci, ids)["h-margin"] == ["30 dB", "ok"]
    # an SX126x station: the measured SNR margin, the CRC verdict and the Doppler block
    st = json.loads(json.dumps(snap))
    st["radio"]["packet_mode"] = True
    packet = {**st["rx"]["rf"]["packet"], "crc_ok": True, "snr_db": -7.75}
    st["rx"]["rf"].update(snr_margin_db=12.25, required_snr_db=-20.0, packet=packet)
    got = _render(tmp_path, st, sci, ids)
    assert got["h-margin"][0] == "12.25 dB SNR" and "CRC" in got["rx-now"][0] and "Doppler shift" in got["rx-dop"][0]


TIMES = "\u00d7"                                       # the multiplication sign of "1.3 x 10^-15 W"
SUPERSCRIPT = str.maketrans("-0123456789", "\u207b\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079")


def _watts(text: str) -> str:
    """A power in W as the page shows it (fwatt): "5.0e-15" -> 5.0, the multiplication sign,
    10 and the exponent -15 in superscript digits, " W"."""
    mant, exp = text.split("e")
    return f"{mant} {TIMES} 10{str(int(exp)).translate(SUPERSCRIPT)} W"


# any 2-figure _watts() text with a negative exponent (re reads the \uXXXX escapes)
W_RE = rf"\d\.\d {TIMES} 10\u207b[\u2070\u00b9\u00b2\u00b3\u2074-\u2079]+ W"


def _power_windows(st, mean_mw):
    """10-min RSSI window of two readings, -100 and -110 dBm (calibrated): the dB statistics as
    rxstats.Window gives them and the given linear mean; the 1-min noise window empty."""
    st["rx"]["windows"]["10m"]["rssi"] = {"n": 2, "mean": -105.0, "sem": 5.0, "std": 5.0, "min": -110.0, "p10": -109.0,
                                          "p50": -105.0, "p90": -101.0, "max": -100.0, "last": -110.0,
                                          "fade_depth": 8.0, "mean_mw": mean_mw}
    st["rx"]["windows"]["1m"]["noise"] = {"n": 0}


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_receiver_tab_shows_received_power_in_watts(sim_station, tmp_path):
    """D-76 (the owner: "the received power in watts (exponent)"): every received power on the
    RECEIVER tab also in watts with a real exponent (_watts()), P = 10^((dBm - 30) / 10), with 2
    significant figures for 1 dB and 0.5 dB readings and 3 for the SX126x's 0.1 dB noise floor;
    the window mean in watts is the GDS's linear mean (mean_mw), not 10^(mean dBm); no SEM or
    sigma in watts; an overload, an empty window or a missing noise reading is "n/a (<reason>)"."""
    _, snap, sci = sim_station
    ids = ["rx-now", "rx-stats"]
    # the simulator's own E22 snapshot: watts next to the RSSI, the noise floor and the windows
    got = _render(tmp_path, snap, sci, ids)
    now, stats = got["rx-now"][0], got["rx-stats"][0]
    assert snap["rx"]["rf"]["rssi_resolution_db"] == 1.0 and snap["rx"]["windows"]["10m"]["rssi"]["n"] > 0
    assert re.search(r"Packet RSSI, last \(calibrated\), in watts" + W_RE, now), now
    assert re.search(r"Noise floor \(calibrated, latest reading\), in watts" + W_RE, now), now
    assert "rows \"in watts\": mean = linear mean (mean of W)" in stats
    assert stats.count("10 min, in watts") == 2 and stats.count("in watts") == 6 + 2    # 3 windows x RSSI, noise
    assert re.search(r"10 min, in watts\d+" + W_RE + r"n/a \(dB only\)n/a \(dB only\)", stats), stats
    # E22 (1 dB): 2 significant figures; the linear mean is shown, not 10^(mean dBm)
    st = json.loads(json.dumps(snap))
    st["rx"]["rf"].update(rssi_dbm=-113.0, rssi_mw=10 ** -11.3, rssi_state="OK", noise_dbm=-119.0)
    _power_windows(st, 5.5e-11)                                                 # (1e-10 + 1e-11) / 2 mW
    got = _render(tmp_path, st, sci, ids)
    now, stats = got["rx-now"][0], got["rx-stats"][0]
    assert "in watts" + _watts("5.0e-15") in now and "latest reading), in watts" + _watts("1.3e-15") in now
    # n, linear mean, SEM, sigma, then min p10 p50 p90 max last converted (-110 -109 -105 -101 -100 -110 dBm),
    # fade depth: the mean 5.5e-14 W is not the p50 = 10^(mean dBm) = 3.2e-14 W
    db_only = "n/a (dB only)"
    row = ["10 min, in watts", "2", _watts("5.5e-14"), db_only, db_only, _watts("1.0e-14"), _watts("1.3e-14"),
           _watts("3.2e-14"), _watts("7.9e-14"), _watts("1.0e-13"), _watts("1.0e-14"), db_only]
    assert "".join(row) in stats, stats
    assert "1 min, in watts0n/a (no reading in this window)" in stats
    # SX126x: RSSI in 0.5 dB steps (2 significant figures), noise in 0.1 dB steps (3)
    st["radio"]["packet_mode"] = True
    st["rx"]["rf"].update(rssi_resolution_db=0.5, rssi_dbm=-126.5, rssi_mw=10 ** -12.65, noise_dbm=-117.4)
    st["rx"]["windows"]["10m"]["noise"] = {"n": 3, "mean": -117.4, "sem": 0.1, "std": 0.1, "min": -117.5,
                                           "p10": -117.5, "p50": -117.4, "p90": -117.3, "max": -117.3,
                                           "last": -117.4, "fade_depth": 0.2, "mean_mw": 10 ** -11.74}
    got = _render(tmp_path, st, sci, ids)
    now, stats = got["rx-now"][0], got["rx-stats"][0]
    assert "(calibrated), in watts" + _watts("2.2e-16") in now
    assert "latest reading), in watts" + _watts("1.82e-15") in now
    assert "10 min, in watts3" + _watts("1.82e-15") in stats and _watts("1.78e-15") in stats   # min -117.5 dBm
    # an overload reading has no power; nor has a station without a noise reading
    st["rx"]["rf"].update(rssi_state="OVERLOAD", rssi_dbm=None, rssi_mw=None, noise_dbm=None)
    now = _render(tmp_path, st, sci, ids)["rx-now"][0]
    assert "in wattsn/a (overload: too strong to measure)" in now and "in wattsn/a (no noise reading)" in now
    st["rx"]["rf"].update(rssi_state="INVALID")
    assert "in wattsn/a (invalid reading: no received power)" in _render(tmp_path, st, sci, ids)["rx-now"][0]
    st["rx"]["rf"].update(rssi_state=None)
    assert "in wattsn/a (no reception yet)" in _render(tmp_path, st, sci, ids)["rx-now"][0]


# =============================================================================
# App Lab entry point (PB-6.2)
# =============================================================================
def _load_main():
    spec = importlib.util.spec_from_file_location("dssq_station_main", MAIN_PY)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_prepare_on_a_fresh_app_folder(tmp_path, monkeypatch, capsys):
    main = _load_main()
    for var in ("DSSQ_CONFIG", "DSSQ_HTTP_HOST", "DSSQ_SIM"):
        monkeypatch.delenv(var, raising=False)
    app = tmp_path / "app"
    app.mkdir()
    cfg = main.prepare(app)
    data = app / "data"
    toml = data / "station.toml"
    assert toml.read_bytes() == station_config.DEFAULT_PATH.read_bytes()      # copied on the first start
    tok = data / "http_token"
    assert stat.S_IMODE(tok.stat().st_mode) == 0o600 and cfg["gds"]["http_token"] == tok.read_text().strip()
    assert "enter it once in the dashboard" in capsys.readouterr().out
    assert cfg["sdls"]["key_file"] == str(data / "sdls_key.hex")              # packaged relative key_file ignored
    assert cfg["gds"]["archive_dir"] == str(data / "archive") and cfg["gds"]["http_host"] == "0.0.0.0"
    assert not (data / "config").exists()
    key = tmp_path / "keys" / "k.hex"
    edited = f'[station]\nname = "DSS-Q1-EDITED"\n[sdls]\nkey_file = "{key}"\n'   # the operator's own copy
    toml.write_text(edited)
    monkeypatch.setenv("DSSQ_HTTP_HOST", "127.0.0.1")
    cfg2 = main.prepare(app)
    assert toml.read_text() == edited                                          # never overwritten
    assert cfg2["station"]["name"] == "DSS-Q1-EDITED" and cfg2["sdls"]["key_file"] == str(key)
    assert cfg2["gds"]["http_host"] == "127.0.0.1" and cfg2["gds"]["http_token"] == cfg["gds"]["http_token"]
    monkeypatch.setenv("DSSQ_CONFIG", str(tmp_path / "missing.toml"))
    with pytest.raises(main.SetupError, match="no such file"):
        main.prepare(app)
    bad = tmp_path / "bad.toml"
    bad.write_text("[gds\n")
    monkeypatch.setenv("DSSQ_CONFIG", str(bad))
    with pytest.raises(main.SetupError, match="configuration"):
        main.prepare(app)
    assert station_config.copy_if_absent(station_config.DEFAULT_PATH, toml) is False
    assert sorted(p.name for p in data.iterdir()) == ["http_token", "station.toml"]   # no temporary left


def test_copy_if_absent_without_hard_links(tmp_path, monkeypatch):
    """A data folder on a file system without hard links still gets its first copy, and
    an existing copy is never replaced."""

    def no_links(src, dst):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(station_config.os, "link", no_links)
    dst = tmp_path / "station.toml"
    assert station_config.copy_if_absent(station_config.DEFAULT_PATH, dst) is True
    assert dst.read_bytes() == station_config.DEFAULT_PATH.read_bytes()
    dst.write_text("edited")
    assert station_config.copy_if_absent(station_config.DEFAULT_PATH, dst) is False and dst.read_text() == "edited"
    assert [p.name for p in tmp_path.iterdir()] == ["station.toml"]                  # no temporary left


def test_host_mode_shares_the_app_data_folder(tmp_path, monkeypatch, capsys):
    """Host mode (python -m dssq.gds --app-data <App>/data, installed by host/install_station.sh)
    resolves exactly like the App Lab entry point: one station.toml, one archive (with the SDLS
    TC sequence-number file), one key and one web token - switching modes never restarts the
    TC sequence counter of a key (GCM nonce reuse under AE)."""
    from dssq import gds
    main = _load_main()
    for var in ("DSSQ_CONFIG", "DSSQ_HTTP_HOST", "DSSQ_SIM"):
        monkeypatch.delenv(var, raising=False)
    app = tmp_path / "dssq-groundstation"
    app.mkdir()
    in_app = main.prepare(app)
    data = app / "data"
    in_host = station_config.load_app_config(data)
    for section, key in (("gds", "archive_dir"), ("sdls", "key_file"), ("gds", "http_token"), ("gds", "http_host")):
        assert in_host[section][key] == in_app[section][key], (section, key)
    assert in_host["gds"]["archive_dir"] == str(data / "archive") and in_host["sdls"]["key_file"] == str(
        data / "sdls_key.hex")
    (data / "station.toml").write_text('[station]\nname = "EDITED"\n')        # the operator's copy rules both
    assert station_config.load_app_config(data)["station"]["name"] == main.prepare(app)["station"]["name"] == "EDITED"
    bad = tmp_path / "bad.toml"
    bad.write_text("[gds\n")
    assert gds.main(["--app-data", str(data), "--config", str(bad)]) == 2       # the CLI takes the same path
    unit = (GROUND / "host" / "dssq-gds.service").read_text(encoding="utf-8")
    assert re.search(r"^WorkingDirectory=/home/arduino/ArduinoApps/dssq-groundstation/python$", unit, re.M)
    assert re.search(r"^ExecStart=/home/arduino/\.venvs/dssq-gds/bin/python -m dssq\.gds "
                     r"--app-data /home/arduino/ArduinoApps/dssq-groundstation/data$", unit, re.M)
    installer = (GROUND / "host" / "install_station.sh").read_text(encoding="utf-8")
    assert "--app-data" in installer and "UNIT_APP_DIR=/home/arduino/ArduinoApps/dssq-groundstation" in installer


FAKE_APP_UTILS = '''"""Test double of arduino.app_utils: App.run calls the user loop until the dashboard answers."""
import json, os, time, urllib.request


class AppError(Exception):
    pass


class Bridge:
    @staticmethod
    def provide(name, fn):
        raise AppError("no router in the test")

    @staticmethod
    def notify(name, *args):
        raise AppError("no router in the test")

    @staticmethod
    def call(name, *args, timeout=10):
        raise AppError("no router in the test")


class App:
    @staticmethod
    def run(user_loop):
        url = "http://127.0.0.1:%s/api/state" % os.environ["DSSQ_TEST_PORT"]
        deadline = time.time() + 40
        while time.time() < deadline:
            user_loop()
            try:
                with urllib.request.urlopen(url, timeout=2) as r:
                    snap = json.loads(r.read())
            except OSError:
                continue
            if snap.get("rx", {}).get("frames", {}).get("ok", 0) >= 2:
                with open(os.environ["DSSQ_TEST_OUT"], "w") as f:
                    json.dump(snap, f)
                return
        raise SystemExit("the dashboard never answered with frames")
'''


def test_app_lab_layout_runs_main_in_a_subprocess(tmp_path):
    app = tmp_path / "dssq-groundstation"
    (app / "python").mkdir(parents=True)
    shutil.copy2(MAIN_PY, app / "python" / "main.py")
    shutil.copytree(GROUND / "python" / "dssq", app / "python" / "dssq", ignore=shutil.ignore_patterns("__pycache__"))
    (app / "python" / "config").mkdir()
    shutil.copy2(station_config.DEFAULT_PATH, app / "python" / "config" / "station.toml")
    (app / "data").mkdir()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    (app / "data" / "station.toml").write_text(
        f'[gds]\nhttp_port = {port}\nfrontpanel = false\nrcu = false\narchive_dir = "archive"\n'
        '[radio]\nair_rate = 7\n')
    fake = tmp_path / "fakepkgs" / "arduino"
    fake.mkdir(parents=True)
    (fake / "__init__.py").write_text("")
    (fake / "app_utils.py").write_text(FAKE_APP_UTILS)
    out = tmp_path / "snapshot.json"
    env = {k: v for k, v in os.environ.items() if k not in ("DSSQ_CONFIG", "PYTHONPATH")}
    env.update(PYTHONPATH=str(tmp_path / "fakepkgs"), DSSQ_SIM="1", DSSQ_HTTP_HOST="127.0.0.1",
               DSSQ_TEST_PORT=str(port), DSSQ_TEST_OUT=str(out))
    r = subprocess.run([sys.executable, str(app / "python" / "main.py")], cwd=tmp_path, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    snap = json.loads(out.read_text())
    assert snap["schema"] == 4 and snap["mode"] == "sim" and snap["rx"]["frames"]["ok"] >= 2
    assert (app / "data" / "http_token").exists() and (app / "data" / "archive").is_dir()
    # No App installs packages: App Lab would pip-install a python/requirements.txt (internet).
    for folder in (app, GROUND, ROOT / "vgq1-spacecraft"):
        assert not (folder / "python" / "requirements.txt").exists(), folder
    # Not under App Lab: a clear message and exit status 2.
    env2 = {k: v for k, v in env.items() if k != "PYTHONPATH"}
    r2 = subprocess.run([sys.executable, str(app / "python" / "main.py")], cwd=tmp_path, env=env2,
                        capture_output=True, text=True, timeout=60)
    assert r2.returncode == 2 and "not running under App Lab" in r2.stdout + r2.stderr


def test_app_yaml_declares_the_dashboard_port():
    text = (GROUND / "app.yaml").read_text(encoding="utf-8")
    assert re.search(r"^ports:\n  - 8080\n?$", text, re.M) and text.startswith("name: DSS-Q Ground Station\n")
    assert "Python standard library only" in text


# ------------------------------------------------- Phase D gate: COP-1 panel SN label
def test_cop_panel_labels_the_last_sn_used(tmp_path):
    """cop.sdls_sn is the LAST TC sequence number used; the next one is link.sdls.tc_next_sn
    (the SDLS panel shows that). The COP-1 panel must not call the last one "next"."""
    html = (ROOT / "dssq-groundstation" / "python" / "dssq" / "display" / "dashboard.html").read_text()
    assert '"SDLS TC service / last SN used", (c.sdls_service || NA) + " / " + fint(c.sdls_sn)' in html
    assert "service / next SN" not in html
    assert '["Next SN / last SN", fint(sd.tc_next_sn), fint(sd.tm_last_sn)]' in html
    from dssq.gds import GroundStation
    from test_end_to_end import _config
    gs = GroundStation(_config(tmp_path), archive=True)
    try:
        gs.sdls_tc.sn = 41
        s = gs.snapshot()
        assert s["cop"]["sdls_sn"] == 41 and s["link"]["sdls"]["tc_next_sn"] == 42
    finally:
        gs.close()
