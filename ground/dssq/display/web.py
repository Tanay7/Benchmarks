"""DSS-Q web display server (stdlib only — no external packages, no CDN).

  GET  /                 mission-control dashboard (dashboard.html)
  GET  /api/state        full GDS snapshot (JSON)
  GET  /api/dictionary   command dictionary (for the command-entry help)
  GET  /metrics          Prometheus text exposition (Grafana trending)
  POST /api/cmd          {"text": "MODE ENCOUNTER"}
  POST /api/confirm      {"id": 3, "role": "CONFIRM" | "AUTH"}
  POST /api/cancel       {"id": 3}

Binding defaults to 127.0.0.1. If you expose it (http_host = 0.0.0.0), set
[gds] http_token so that commanding endpoints require the X-DSSQ-Token header.
"""
from __future__ import annotations

import json
import math
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..dictionary import COMMANDS, DIRECTIVES, GROUND_DIRECTIVES

HTML = Path(__file__).with_name("dashboard.html")


def clean(o):
    """JSON-safe copy: NaN/inf -> None, tuples -> lists, unknown -> str."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (int, str, bool)) or o is None:
        return o
    if isinstance(o, bytes):
        return o.hex()
    return str(o)


def prometheus(s: dict) -> str:
    out = []

    def g(name, value, help_, labels=""):
        if value is None or isinstance(value, bool) and False:
            return
        if isinstance(value, bool):
            value = int(value)
        if not isinstance(value, (int, float)) or (isinstance(value, float) and not math.isfinite(value)):
            return
        out.append(f"# HELP dssq_{name} {help_}\n# TYPE dssq_{name} gauge\ndssq_{name}{labels} {value}")

    rx = s["rx"]
    g("los", rx["state"]["los"], "1 = loss of signal")
    for k, v in rx["rf"].items():
        if isinstance(v, (int, float)):
            g(f"rf_{k}", v, f"receiver {k}")
    for k, v in rx["decoder"].items():
        if isinstance(v, (int, float)):
            g(f"decoder_{k}", v, f"RS decoder {k}")
    for k, v in rx["frames"].items():
        if isinstance(v, (int, float)):
            g(f"frames_{k}", v, f"frames {k}")
    for k, v in rx["budget"].items():
        if isinstance(v, (int, float)):
            g(f"budget_{k}", v, f"link budget {k}")
    for pname, p in s["tlm"].items():
        for k, v in p["values"].items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out.append(f'dssq_tlm{{packet="{pname}",param="{k}"}} {v}')
    return "\n".join(out) + "\n"


def start_web(gs, host: str, port: int, token: str | None = None):
    token = token or gs.cfg["gds"].get("http_token") or None

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):          # keep the console quiet
            pass

        def _send(self, code, body: bytes, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(200, HTML.read_bytes(), "text/html; charset=utf-8")
            elif self.path.startswith("/api/state"):
                self._send(200, json.dumps(clean(gs.snapshot())).encode())
            elif self.path.startswith("/api/dictionary"):
                d = {"commands": {k: {"opcode": c.opcode, "args": [a[0] for a in c.args],
                                      "hazardous": c.hazardous, "desc": c.desc} for k, c in COMMANDS.items()},
                     "directives": {**DIRECTIVES, **GROUND_DIRECTIVES}}
                self._send(200, json.dumps(d).encode())
            elif self.path.startswith("/metrics"):
                self._send(200, prometheus(clean(gs.snapshot())).encode(), "text/plain; version=0.0.4")
            else:
                self._send(404, b'{"error":"not found"}')

        def do_POST(self):
            if token and self.headers.get("X-DSSQ-Token") != token:
                self._send(403, b'{"error":"bad or missing X-DSSQ-Token"}')
                return
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                self._send(400, b'{"error":"bad json"}')
                return
            if self.path == "/api/cmd":
                r = gs.submit_command(str(body.get("text", "")), "web")
            elif self.path == "/api/confirm":
                r = gs.confirm(int(body.get("id", 0)), str(body.get("role", "CONFIRM")).upper())
            elif self.path == "/api/cancel":
                gs.cancel(int(body.get("id", 0)))
                r = {"status": "CANCELLED"}
            else:
                self._send(404, b'{"error":"not found"}')
                return
            self._send(200, json.dumps(clean(r)).encode())

    srv = ThreadingHTTPServer((host, port), H)
    threading.Thread(target=srv.serve_forever, daemon=True, name="web").start()
    gs.event("INFO", "WEB", f"dashboard on http://{host}:{port}/" +
             ("" if host in ("127.0.0.1", "localhost") else "  (LAN-exposed" +
              (", token required for commanding)" if token else ", NO TOKEN SET!)")))
    return srv
