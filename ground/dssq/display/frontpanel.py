"""Station I/O, Radio Control Unit and the GDS's single Router Bridge client.

The GDS runs on the VENTUNO Q's Linux processor and talks to the board's STM32H5 MCU
(sketch ground/station_io) through the Arduino Router (MessagePack-RPC over the board's
internal link, socket unix:///var/run/arduino-router.sock or App Lab's APP_SOCKET).

One Linux client per provided name (arduino.router_bridge: a method name belongs to ONE
client), so the whole GDS shares one BridgeLink: the front panel, the SX126x radio backend
(dssq.radio.sx126x.Sx126xBridge) and the Radio Control Unit all use it.
  * host mode   BridgeLink.from_router(address): arduino.router_bridge.Bridge, connects in
                the background and reconnects by itself;
  * App Lab     BridgeLink.from_app_utils(arduino.app_utils.Bridge): the App's process-wide
                bridge (connected on first use with APP_SOCKET).
Both packages ship with App Lab's Python image (arduino-router-bridge, app-bricks-py);
nothing is downloaded and they are imported only here, so the simulator and the tests run
without them. BridgeLink turns every library failure into the exceptions the callers
handle: arduino.app_utils.AppError (router unreachable within 5 s; derives from Exception
only) -> ConnectionError; TimeoutError / ConnectionError / ValueError (RpcError) /
RuntimeError pass through unchanged.

GDS -> MCU   notify("fp_line", line)     S|<aos 0/1>|<alarm 0/1/2>|<uplink 0/1>|<beacon -1/0..3>
                                          Q|<id>  hazardous command awaiting CONFIRM (-1 none)
             call("rcu_mode", m) -> int   drive the ground E22 M0/M1; 1 = AUX ready, 0 busy
MCU -> GDS   notify("fp_in", line)       K|<typed text>  CMD|<text>  CONFIRM|<id>
                                          H|<fw>|<rcu 0/1>|<kb 0/1>   (hello, every 5 s)
One CardKB on the header SDA/SCL bus (contract 16.3 / 17.1); a second operator authorises
with the web AUTH button, not on the panel. The SX126x services (rf_*) are documented in
dssq/radio/sx126x.py.

Threads: the bridge's dispatcher thread only validates and enqueues fp_in lines (a
provided handler must never call back into the bridge); "fp-rx" executes them, "fp-tx"
sends the status lines once a second from GroundStation.panel_status() (cheap, no
snapshot). Each thread body has ONE documented `except Exception` boundary (log and
continue): a dead panel thread would go unnoticed, and a panel fault must never take the
GDS down.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from typing import Any

log = logging.getLogger("dssq.fp")

LINE_MAX = 60                    # fp_line: the MCU's line buffer
INBOX_MAX = 256                  # fp_in lines waiting for the rx thread
FP_IN_MAX_LEN = 256
TX_PERIOD_S = 1.0
RCU_TIMEOUT_S = 3.0              # rcu_mode blocks the MCU loop for <= ~1 s (AUX wait)
APP_RETRY_S = 10.0               # app_utils: after "router unreachable", fail fast this long
BRIDGE_ERRORS = (TimeoutError, ConnectionError, ValueError, RuntimeError, OSError)
ALARM_CODES = {"NONE": 0, "YELLOW": 1, "RED": 2}


class BridgeLink:
    """One Router Bridge client for the whole GDS (FrontPanel, SX126x backend, RCU)."""

    def __init__(self, bridge: Any, kind: str, app_errors: tuple[type[BaseException], ...] = ()):
        if kind not in ("router", "app_utils", "test"):
            raise ValueError(f"bridge kind {kind!r}")
        self._b = bridge
        self.kind = kind
        self._app_errors = app_errors
        self._down_until = 0.0
        self.failures = 0

    @classmethod
    def from_router(cls, address: str | None = None, timeout_s: float = 10.0) -> BridgeLink:
        """Host mode: a dedicated arduino.router_bridge client. Raises ImportError without
        the package and ConnectionError for an unusable address; an unreachable router is
        NOT an error (the client keeps connecting in the background, logged)."""
        from arduino.router_bridge import DEFAULT_ADDRESS, Bridge  # type: ignore[import-not-found]
        try:
            b = Bridge(address or DEFAULT_ADDRESS)
        except ValueError as e:
            raise ConnectionError(f"router address {address!r}: {e}") from e
        link = cls(b, "router")
        if not link.connect(timeout_s):
            log.warning("arduino-router not reachable at %s yet; connecting in the background",
                        getattr(b, "address", address))
        return link

    @classmethod
    def from_app_utils(cls, bridge_cls: type) -> BridgeLink:
        """App Lab: arduino.app_utils.Bridge (process-wide, connected on first use)."""
        try:
            from arduino.app_utils import AppError  # type: ignore[import-not-found]
            errs: tuple[type[BaseException], ...] = (AppError,)
        except ImportError:                       # an app_utils without AppError: nothing to map
            errs = ()
        return cls(bridge_cls, "app_utils", errs)

    @property
    def address(self) -> str:
        return str(getattr(self._b, "address", "APP_SOCKET" if self.kind == "app_utils" else "?"))

    def _invoke(self, fn: Callable[..., Any], *args: Any, **kw: Any) -> Any:
        if self.kind == "app_utils" and time.monotonic() < self._down_until:
            raise ConnectionError(f"arduino-router unreachable (not tried again for {APP_RETRY_S:.0f} s)")
        try:
            return fn(*args, **kw)
        except self._app_errors as e:            # AppError: the router could not be reached
            self.failures += 1
            self._down_until = time.monotonic() + APP_RETRY_S
            raise ConnectionError(str(e)) from e

    def provide(self, name: str, fn: Callable[..., object]) -> None:
        self._invoke(self._b.provide, name, fn)

    def notify(self, name: str, *args: object) -> None:
        self._invoke(self._b.notify, name, *args)

    def call(self, name: str, *args: object, timeout: float = 3.0) -> object:
        return self._invoke(self._b.call, name, *args, timeout=timeout)

    def connect(self, timeout: float) -> bool:
        """router: wait up to `timeout` for the connection (True when connected; it keeps
        trying in the background otherwise). app_utils connects on first use: True."""
        if self.kind == "router":
            return bool(self._b.connect(timeout=timeout))
        return True

    def disconnect(self) -> None:
        """router: closes this client. app_utils: nothing (the App owns its bridge)."""
        if self.kind == "router":
            self._b.disconnect()


def status_lines(st: Mapping[str, Any]) -> list[str]:
    """The lines pushed to the MCU once a second, from GroundStation.panel_status()."""
    alarm = ALARM_CODES.get(str(st.get("alarm")), 2)
    beacon = st.get("beacon")
    b = beacon if isinstance(beacon, int) and not isinstance(beacon, bool) and 0 <= beacon <= 3 else -1
    pend = [i for i in st.get("pending_ids", []) if isinstance(i, int)]
    return [f"S|{0 if st.get('los', True) else 1}|{alarm}|{1 if st.get('uplink') else 0}|{b}"[:LINE_MAX],
            f"Q|{pend[-1] if pend else -1}"[:LINE_MAX]]


class _Rcu:
    """Radio Control Unit: the station MCU drives the ground E22 M0/M1 and reads AUX."""

    def __init__(self, link: BridgeLink):
        self.link = link

    def set_mode(self, m: int) -> bool:
        if isinstance(m, bool) or not isinstance(m, int) or not 0 <= m <= 3:
            raise ValueError(f"E22 mode {m!r} outside 0..3")
        try:
            aux = self.link.call("rcu_mode", m, timeout=RCU_TIMEOUT_S)
        except BRIDGE_ERRORS as e:
            log.warning("RCU mode %d failed: %s", m, e)
            return False
        if isinstance(aux, bool) or not isinstance(aux, int):
            log.warning("RCU mode %d: unexpected reply %r", m, aux)
            return False
        if aux != 1:
            log.warning("RCU mode %d: E22 AUX still busy", m)
        return aux == 1


class FrontPanel:
    """Station I/O MCU link: CardKB input, annunciator status lines, RCU."""

    def __init__(self, gs: Any, link: BridgeLink):
        self.gs = gs
        self.link = link
        self.inbox: deque[str] = deque(maxlen=INBOX_MAX)
        self.rejected = 0
        self._wake = threading.Event()
        self.stop_ev = threading.Event()
        self._threads: list[threading.Thread] = []
        self._last_tx_error = 0.0
        link.provide("fp_in", self._on_fp_in)

    # -------------------------------------------------------- bridge handler
    def _on_fp_in(self, line: object) -> None:
        """Dispatcher thread: validate and enqueue only (never call back into the bridge)."""
        if not isinstance(line, str) or not 0 < len(line) <= FP_IN_MAX_LEN or not line.isprintable():
            self.rejected += 1
            return
        self.inbox.append(line)
        self._wake.set()

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        for name, body in (("fp-rx", self._rx_loop), ("fp-tx", self._tx_loop)):
            t = threading.Thread(target=body, daemon=True, name=name)
            t.start()
            self._threads.append(t)

    def stop(self, timeout_s: float = 1.0) -> bool:
        """Stops both threads; waits at most `timeout_s` in total. False if one is stuck."""
        self.stop_ev.set()
        self._wake.set()
        deadline = time.monotonic() + timeout_s
        for t in self._threads:
            t.join(max(0.0, deadline - time.monotonic()))
        return not any(t.is_alive() for t in self._threads)

    def rcu(self) -> _Rcu:
        return _Rcu(self.link)

    # ----------------------------------------------------------------- loops
    def _handle(self, line: str) -> None:
        kind, _, rest = line.partition("|")
        parts = rest.split("|")
        if kind == "H" and len(parts) >= 3:
            self.gs.station_hello(parts[0][:32], parts[1] == "1", parts[2] == "1")
        elif kind == "K":
            self.gs.station_input(rest)
        elif kind == "CMD" and rest:
            self.gs.station_input("")
            self.gs.submit_command(rest, "cardkb")
        elif kind == "CONFIRM" and rest.isdecimal() and len(rest) <= 9:
            self.gs.confirm(int(rest), "CONFIRM")
        else:                                       # incl. AUTH: the second operator uses the web UI
            self.rejected += 1

    def _rx_loop(self) -> None:
        while not self.stop_ev.is_set():
            self._wake.wait(0.2)
            self._wake.clear()
            while self.inbox and not self.stop_ev.is_set():
                line = self.inbox.popleft()
                try:
                    self._handle(line)
                except Exception:                   # documented thread boundary: log, continue
                    log.exception("station I/O line %r failed", line[:40])

    def _tx_loop(self) -> None:
        while not self.stop_ev.is_set():
            try:
                for line in status_lines(self.gs.panel_status()):
                    try:
                        self.link.notify("fp_line", line)
                    except BRIDGE_ERRORS as e:
                        now = time.monotonic()
                        if now - self._last_tx_error > 60.0:
                            self._last_tx_error = now
                            log.warning("station I/O update failed: %s", e)
                        break
                    if line.startswith("S|"):
                        self.gs.station_status_sent(line)
            except Exception:                       # documented thread boundary: log, continue
                log.exception("station I/O status thread")
            self.stop_ev.wait(TX_PERIOD_S)
