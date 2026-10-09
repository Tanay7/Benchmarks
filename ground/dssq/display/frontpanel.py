"""Station I/O + Radio Control Unit link (VENTUNO Q Linux <-> its own MCU).

The GDS runs as an ordinary Linux service on the VENTUNO Q's Dragonwing CPU and
talks to the board's STM32H5 (App Lab app ground/station_io) through
the Arduino Router: `pip install arduino-router-bridge`, socket
unix:///var/run/arduino-router.sock. No App Lab container is required. The web
dashboard is the station's only display; the MCU provides the CardKB keyboards,
the annunciator LEDs / buzzer and the Radio Control Unit.

GDS -> MCU   notify("fp_line", line)      S|<aos>|<alarm 0/1/2>|<uplink>   LEDs / buzzer
                                           Q|<id>   hazardous command awaiting CONFIRM (-1 none)
             call("rcu_mode", m) -> aux    drive ground E22 M0/M1, returns AUX state
MCU -> GDS   notify("fp_in", line)        K|<typed text>  CMD|text  CONFIRM|id  AUTH|id
                                           H|<fw>|<rcu>|<kb1>|<kb2>   (hello, every 5 s)
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("dssq.fp")


def status_lines(s: dict) -> list[str]:
    """Lines the GDS pushes to the MCU once a second, built from a snapshot."""
    alarm = {"NONE": 0, "YELLOW": 1, "RED": 2}[s["alarm"]]
    uplink = int(s["cop"]["outstanding"] > 0 or s["cop"]["pending_bypass"] > 0)
    pend = [p["id"] for p in s["pending_confirm"] if not p["confirmed"]]
    return [f"S|{0 if s['rx']['state']['los'] else 1}|{alarm}|{uplink}",
            f"Q|{pend[-1] if pend else -1}"]


class _RouterTransport:
    def __init__(self, address: str | None = None):
        from arduino.router_bridge import DEFAULT_ADDRESS, Bridge
        self.bridge = Bridge(address or DEFAULT_ADDRESS)
        self.inbox: list[str] = []
        self.bridge.provide("fp_in", lambda line: self.inbox.append(str(line)))
        if not self.bridge.connect(timeout=10):
            log.warning("arduino-router not reachable yet; retrying in the background")

    def send(self, line: str):
        self.bridge.notify("fp_line", line[:60])

    def call(self, method: str, *args, timeout: float = 3.0):
        return self.bridge.call(method, *args, timeout=timeout)

    def readline(self):
        if self.inbox:
            return self.inbox.pop(0)
        time.sleep(0.05)
        return None


class FrontPanel:
    def __init__(self, gs, router_address: str | None = None):
        self.gs = gs
        self.t = _RouterTransport(router_address)
        self.stop_ev = threading.Event()

    def start(self):
        threading.Thread(target=self._rx_loop, daemon=True, name="fp-rx").start()
        threading.Thread(target=self._tx_loop, daemon=True, name="fp-tx").start()

    def stop(self):
        self.stop_ev.set()

    # -------------------------------------------------------------- RCU
    def rcu(self):
        fp = self

        class Rcu:
            def set_mode(self, m: int) -> bool:
                try:
                    aux = fp.t.call("rcu_mode", int(m))
                except Exception as e:          # TimeoutError / ConnectionError / RpcError
                    log.warning("RCU mode %d failed: %s", m, e)
                    return False
                if not aux:
                    log.warning("RCU mode %d: E22 AUX still busy", m)
                return bool(aux)
        return Rcu()

    # ------------------------------------------------------------- loops
    def _rx_loop(self):
        io = self.gs.station_io
        while not self.stop_ev.is_set():
            line = self.t.readline()
            if not line:
                continue
            kind, _, rest = line.partition("|")
            parts = rest.split("|")
            if kind == "H":
                io.update(connected_t=time.time(), fw=parts[0] if parts else "",
                          rcu=parts[1:2] == ["1"], kb1=parts[2:3] == ["1"], kb2=parts[3:4] == ["1"])
            elif kind == "K":
                io["input"] = rest                     # live CardKB line for the dashboard
            elif kind == "CMD" and rest:
                io["input"] = ""
                self.gs.submit_command(rest, "cardkb")
            elif kind in ("CONFIRM", "AUTH") and rest.isdigit():
                self.gs.confirm(int(rest), kind)

    def _tx_loop(self):
        while not self.stop_ev.is_set():
            try:
                for line in status_lines(self.gs.snapshot()):
                    self.t.send(line)
            except Exception as e:      # the panel must never take the GDS down
                log.warning("station I/O update failed: %s", e)
            self.stop_ev.wait(1.0)
