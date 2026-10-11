"""SX126x ground radio over the Arduino Router Bridge (packet backend).

The VENTUNO Q MCU (ground/station_io/sketch, build switch DSSQ_RADIO_SX126X = 1)
drives an SX1268 / SX1262 module (reference: EBYTE E22-400M33S) over SPI with
RadioLib 7.8.1 and exchanges WHOLE LoRa packets with Linux through the Router Bridge
(MessagePack-RPC). Unlike the E22 UART firmware it reports, for every packet, the
packet RSSI, the SNR and the LoRa frequency-error estimate (contract v3 2.2, 14).

MCU -> Linux notifications (handlers registered by attach()):
  rf_rx(hex: str, rssi_ddbm: int, snr_cdb: int, freq_err_hz: int, crc_ok: int)
      one received packet: hex = payload (one 236-octet CADU), "" when crc_ok = 0
      (the MCU discards a payload whose CRC failed); rssi_ddbm = RssiPkt in 0.1 dBm
      (-1275..0); snr_cdb = SNR in 0.01 dB (SnrPkt / 4: -3200..3175); freq_err_hz =
      raw frequency-error estimate in Hz (signed, no calibration applied).
  rf_evt(line: str), '|'-separated:
      S|fw|chip|state|ch|code|pwr|flags|rx_gain_reg|rx_ok|rx_crc|rx_hdr|tx_ok|tx_fail|
        cad_busy|cad_forced|faults|resets|txq|uptime_s     status, every 5 s and after
        each configuration change. flags: b0 CAD before TX, b1 Rx boosted gain
        requested, b2 boosted gain VERIFIED (register 0x08AC read back = 0x96).
      C|detected|ms        result of an operator CAD (rf_cad)
      T|ok|ms|octets       an uplink transmission finished (ok = 0: TX timeout)
      E|code|text          fault / recovery event (EVENT_CODES)
Linux -> MCU calls (Bridge.provide_safe on the MCU: executed in its loop thread):
  rf_tx(hex) -> 1 queued | 0 queue full | -1 rejected (length/hex) | -2 radio down
      at most RF_TX_MAX_OCTETS octets, so the request fits the MCU's 256-octet RPC
      inbound buffer (a CLTU of a 64-octet TC frame is 90 octets).
  rf_cfg(channel, rate_code, power_code, flags) -> 1 applied | 2 queued (applied when the
      radio is idle) | -1 out of range; -1 in an argument = keep the current value.
      flags: b0 CAD before every transmission, b1 Rx boosted gain.
  rf_noise() -> median of 8 instantaneous RSSI samples in 0.1 dBm, NOISE_NA = n/a
  rf_cad() -> 1 started (result as a C| event) | 0 busy | -2 radio down
  rf_wake(hex) -> as rf_tx; transmitted with the 32-symbol wake-up preamble that the
      hibernating spacecraft's duty-cycled receiver listens for (flight
      kSxWakePreambleSym; the SX126x counterpart of the E22 WOR wake-up code)

Routing: the Router Bridge lets ONE Linux client provide a given method name. The
GDS already owns a Bridge client in display/frontpanel.py (FrontPanel.t.bridge,
created by _RouterTransport); pass it here and call attach(), which registers rf_rx
and rf_evt on that client (registration is declarative and survives reconnects).
Without the front panel, Sx126xBridge.from_router() opens a client of its own.
Threading: handlers run on the bridge's dispatcher thread; they only validate and
enqueue (they never call back into the bridge, which the bridge forbids). read(),
packets(), write() and the control calls are made from the GDS radio thread.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from typing import Any

from ..link import CHANNEL_MAX, SX126X, RadioBackend, RadioSettings, RxPacket, channel_freq_mhz, rate_label

log = logging.getLogger("dssq.sx126x")

RF_TX_MAX_OCTETS = 112                 # hex request must fit the MCU's 256-octet RPC buffer
RF_RX_MAX_OCTETS = 255                 # SX126x maximum LoRa payload
RSSI_DDBM_RANGE = (-1275, 0)           # RssiPkt / 2, unsigned octet
SNR_CDB_RANGE = (-3200, 3175)          # SnrPkt / 4, signed octet
FERR_ABS_MAX_HZ = 500_000              # far outside any LoRa bandwidth: a corrupt value
NOISE_NA = -32768
NOISE_DDBM_RANGE = (-1600, 0)
CFG_APPLIED, CFG_QUEUED = 1, 2
STATUS_STALE_S = 15.0                  # MCU status older than this: backend not connected
PUSH_RETRY_S = 5.0                     # pacing of configuration pushes that failed
EVT_MAX_LEN = 256
E22_QUERY_PREFIX = bytes([0xC0, 0xC1, 0xC2, 0xC3])
STATE_NAMES = {0: "OFF", 1: "RX", 2: "CAD", 3: "TX", 4: "RESET"}
EVENT_CODES = {1: "BUSY_TIMEOUT", 2: "SPI_ERROR", 3: "TX_TIMEOUT", 4: "INIT_FAILED", 5: "RECOVERED",
               6: "RX_GAIN_REASSERTED", 7: "CAD_TIMEOUT", 8: "DEVICE_ERRORS", 9: "RX_ERROR",
               10: "CONFIG_FAILED", 11: "MODE_RESTORED"}
STATUS_FIELDS = ("fw", "chip", "state", "channel", "rate_code", "power_code", "flags", "rx_gain_reg",
                 "rx_ok", "rx_crc_err", "rx_hdr_err", "tx_ok", "tx_fail", "cad_busy", "cad_forced",
                 "faults", "resets", "txq", "uptime_s")
_U32 = 0xFFFFFFFF
STATUS_RANGES = {"state": (0, 4), "channel": (0, CHANNEL_MAX), "rate_code": (0, 7), "power_code": (0, 3),
                 "flags": (0, 7), "rx_gain_reg": (0, 255), "txq": (0, 16)}
RX_GAIN_BOOSTED = 0x96                 # SX1261/2 DS Table 9-3


def _int(v: object, lo: int, hi: int) -> int | None:
    """Range-checked integer from the bridge (bool is not an integer here)."""
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        return None
    return v


def parse_rf_rx(args: tuple, ert: float) -> RxPacket | None:
    """Validates one rf_rx notification; None if any argument is malformed."""
    if len(args) != 5:
        return None
    hex_txt, rssi, snr, ferr, crc = args
    if not isinstance(hex_txt, str) or len(hex_txt) % 2 or len(hex_txt) > 2 * RF_RX_MAX_OCTETS:
        return None
    try:
        data = bytes.fromhex(hex_txt)
    except ValueError:
        return None
    rssi_i = _int(rssi, *RSSI_DDBM_RANGE)
    snr_i = _int(snr, *SNR_CDB_RANGE)
    ferr_i = _int(ferr, -FERR_ABS_MAX_HZ, FERR_ABS_MAX_HZ)
    crc_i = 1 if crc is True else 0 if crc is False else _int(crc, 0, 1)
    if rssi_i is None or snr_i is None or ferr_i is None or crc_i is None:
        return None
    if crc_i and not data:
        return None                                    # a good packet always has a payload
    return RxPacket(data if crc_i else b"", ert, rssi_i / 10.0, snr_i / 100.0, float(ferr_i), bool(crc_i))


def parse_status(fields: list[str]) -> dict | None:
    """Validates the fields of an S| status line (without the leading 'S')."""
    if len(fields) != len(STATUS_FIELDS):
        return None
    out: dict[str, Any] = {}
    for name, txt in zip(STATUS_FIELDS, fields, strict=True):
        if name in ("fw", "chip"):
            if not 0 < len(txt) <= 32:
                return None
            out[name] = txt
            continue
        if not txt.isdigit():
            return None
        lo, hi = STATUS_RANGES.get(name, (0, _U32))
        v = _int(int(txt), lo, hi)
        if v is None:
            return None
        out[name] = v
    out["state_name"] = STATE_NAMES[out["state"]]
    out["cad_before_tx"] = bool(out["flags"] & 1)
    out["rx_boost"] = bool(out["flags"] & 2)
    out["rx_boost_verified"] = bool(out["flags"] & 4)
    return out


class Sx126xBridge(RadioBackend):
    """Packet backend for the GDS. `bridge` needs provide(name, fn) and
    call(name, *args, timeout=s) (arduino.router_bridge.Bridge or a test double)."""
    packet_mode = True

    def __init__(self, bridge: Any, radio_cfg: Mapping[str, object] | None = None,
                 clock: Callable[[], float] = time.time, call_timeout_s: float = 2.0,
                 poll_s: float = 0.05, max_queue: int = 256, max_tx_backlog: int = 16):
        self.bridge = bridge
        self.settings = RadioSettings.from_config({**(radio_cfg or {}), "model": SX126X})
        self.clock = clock
        self.call_timeout_s = call_timeout_s
        self.poll_s = poll_s
        self._own_bridge = False
        self._lock = threading.Lock()
        self._rx_ready = threading.Event()
        self._rx: deque[RxPacket] = deque()
        self._max_queue = max_queue
        self._tx_backlog: deque[bytes] = deque()
        self._max_tx_backlog = max_tx_backlog
        self._need_push = False                          # station config must be (re)sent
        self._next_push_t = 0.0
        self._last_uptime: int | None = None
        self.mcu: dict | None = None                     # last validated status line
        self.mcu_t: float | None = None
        self.last_cad: dict | None = None
        self.last_tx: dict | None = None
        self.events: deque[dict] = deque(maxlen=50)
        self.stats = {k: 0 for k in ("rx_packets", "rx_crc_fail", "rx_rejected", "rx_overflow",
                                     "evt_rejected", "tx_sent", "tx_busy", "tx_rejected", "tx_dropped",
                                     "tx_done", "tx_failed", "call_errors", "e22_queries_ignored",
                                     "mcu_reboots", "config_pushes")}

    # ----------------------------------------------------------- wiring
    def attach(self) -> None:
        """Registers the MCU -> Linux notification handlers on the bridge. The station
        configuration is pushed to the MCU as soon as its first status arrives."""
        self.bridge.provide("rf_rx", self._on_rf_rx)
        self.bridge.provide("rf_evt", self._on_rf_evt)
        self._need_push = True

    @classmethod
    def from_router(cls, address: str | None = None, radio_cfg: Mapping[str, object] | None = None,
                    timeout_s: float = 10.0) -> Sx126xBridge:
        """Opens a dedicated Router Bridge client (when the front panel is not running)."""
        from arduino.router_bridge import DEFAULT_ADDRESS, Bridge     # pip install arduino-router-bridge
        obj = cls(Bridge(address or DEFAULT_ADDRESS), radio_cfg)
        obj._own_bridge = True
        obj.attach()
        if not obj.bridge.connect(timeout=timeout_s):
            log.warning("arduino-router not reachable yet; retrying in the background")
        return obj

    # --------------------------------------------------- bridge handlers
    def _on_rf_rx(self, *args: object) -> None:
        pkt = parse_rf_rx(args, self.clock())
        with self._lock:
            if pkt is None:
                self.stats["rx_rejected"] += 1
                return
            if len(self._rx) >= self._max_queue:             # GDS stalled: keep the newest
                self._rx.popleft()
                self.stats["rx_overflow"] += 1
            self._rx.append(pkt)
            self.stats["rx_packets"] += 1
            self.stats["rx_crc_fail"] += 0 if pkt.crc_ok else 1
        self._rx_ready.set()

    def _on_rf_evt(self, line: object) -> None:
        now = self.clock()
        if not isinstance(line, str) or not 0 < len(line) <= EVT_MAX_LEN:
            with self._lock:
                self.stats["evt_rejected"] += 1
            return
        kind, *f = line.split("|")
        with self._lock:
            if kind == "S" and (st := parse_status(f)) is not None:
                if self._last_uptime is not None and st["uptime_s"] < self._last_uptime:
                    self.stats["mcu_reboots"] += 1
                    self._need_push = True                   # MCU rebooted with compiled defaults
                self._last_uptime = st["uptime_s"]
                self.mcu, self.mcu_t = st, now
            elif kind == "C" and len(f) == 2 and f[0] in ("0", "1") and f[1].isdigit():
                self.last_cad = {"t": now, "detected": f[0] == "1", "duration_ms": int(f[1])}
            elif kind == "T" and len(f) == 3 and f[0] in ("0", "1") and f[1].isdigit() and f[2].isdigit():
                self.last_tx = {"t": now, "ok": f[0] == "1", "airtime_ms": int(f[1]), "octets": int(f[2])}
                self.stats["tx_done" if f[0] == "1" else "tx_failed"] += 1
            elif kind == "E" and len(f) >= 2 and f[0].isdigit() and int(f[0]) in EVENT_CODES:
                code = int(f[0])
                text = "|".join(f[1:])[:120]
                self.events.append({"t": now, "code": code, "name": EVENT_CODES[code], "text": text})
                if code != 5:                                # 5 = RECOVERED
                    log.warning("ground SX126x %s: %s", EVENT_CODES[code], text)
                else:
                    log.info("ground SX126x recovered: %s", text)
            else:
                self.stats["evt_rejected"] += 1

    # ------------------------------------------------------ RPC helper
    def _count(self, key: str) -> None:
        with self._lock:
            self.stats[key] += 1

    def _call(self, method: str, *args: object) -> int | None:
        """One bridge call; link failures are counted and logged, never raised."""
        try:
            rc = self.bridge.call(method, *args, timeout=self.call_timeout_s)
        except (TimeoutError, ConnectionError, ValueError, RuntimeError, OSError) as e:
            self._count("call_errors")
            log.warning("ground SX126x %s failed: %s", method, e)
            return None
        if isinstance(rc, bool) or not isinstance(rc, int):
            self._count("call_errors")
            log.warning("ground SX126x %s: unexpected reply %r", method, rc)
            return None
        return rc

    # ---------------------------------------------------- GDS interface
    def read(self) -> bytes:
        """Waits up to poll_s for a packet, services the uplink backlog; returns b""
        (packets are taken with packets())."""
        self._rx_ready.wait(self.poll_s)
        self._rx_ready.clear()
        self._service()
        return b""

    def packets(self) -> list[RxPacket]:
        with self._lock:
            out = list(self._rx)
            self._rx.clear()
        return out

    def write(self, data: bytes) -> None:
        """Queues one CLTU for transmission. E22 module commands (noise queries) are
        never radiated: use noise_dbm()."""
        if data[:4] == E22_QUERY_PREFIX:
            self._count("e22_queries_ignored")
            return
        if not 1 <= len(data) <= RF_TX_MAX_OCTETS:
            self._count("tx_rejected")
            log.error("ground SX126x: %d-octet uplink rejected (1..%d)", len(data), RF_TX_MAX_OCTETS)
            return
        if self._tx_backlog or not self._send(data):
            if len(self._tx_backlog) >= self._max_tx_backlog:
                self._tx_backlog.popleft()
                self._count("tx_dropped")                   # COP-1 retransmits what is lost
            self._tx_backlog.append(bytes(data))

    def _send(self, data: bytes) -> bool:
        """True when the MCU took the CLTU or definitively rejected it (not retried)."""
        rc = self._call("rf_tx", data.hex())
        if rc == 1:
            self._count("tx_sent")
            return True
        if rc == 0 or rc is None:                            # queue full / link error: retry later
            self._count("tx_busy")
            return False
        self._count("tx_rejected")
        log.error("ground SX126x rejected an uplink (rf_tx = %d)", rc)
        return True

    def _service(self) -> None:
        now = self.clock()
        if self._need_push and self.connected and now >= self._next_push_t:
            self._next_push_t = now + PUSH_RETRY_S
            if self.push_config() in (CFG_APPLIED, CFG_QUEUED):
                self._need_push = False
        if self._tx_backlog and self._send(self._tx_backlog[0]):   # one attempt per read()
            self._tx_backlog.popleft()

    def close(self) -> None:
        if self._own_bridge:
            self.bridge.disconnect()

    # --------------------------------------------------------- control
    def configure(self, channel: int | None = None, rate_code: int | None = None,
                  power_code: int | None = None, cad: bool | None = None,
                  rx_boost: bool | None = None) -> int | None:
        """rf_cfg; returns CFG_APPLIED, CFG_QUEUED, a negative MCU code or None (link)."""
        for v, hi, what in ((channel, CHANNEL_MAX, "channel"), (rate_code, 7, "rate code"),
                            (power_code, 3, "power code")):
            if v is not None and (isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= hi):
                raise ValueError(f"{what} {v!r} outside 0..{hi}")
        s = self.settings
        new_cad = s.cad if cad is None else bool(cad)
        new_boost = s.rx_boost if rx_boost is None else bool(rx_boost)
        flags = -1 if cad is None and rx_boost is None else int(new_cad) | (int(new_boost) << 1)
        rc = self._call("rf_cfg", -1 if channel is None else channel, -1 if rate_code is None else rate_code,
                        -1 if power_code is None else power_code, flags)
        if rc in (CFG_APPLIED, CFG_QUEUED):
            self.settings = s.with_(channel=s.channel if channel is None else channel,
                                    rate_code=s.rate_code if rate_code is None else rate_code,
                                    power_code=s.power_code if power_code is None else power_code,
                                    cad=new_cad, rx_boost=new_boost)
        return rc

    def push_config(self) -> int | None:
        """Sends the complete station configuration (done automatically after attach()
        and after an MCU reboot, from the GDS radio thread in read())."""
        self._count("config_pushes")
        s = self.settings
        return self.configure(s.channel, s.rate_code, s.power_code, s.cad, s.rx_boost)

    def noise_dbm(self) -> float | None:
        rc = self._call("rf_noise")
        if rc is None or rc == NOISE_NA or _int(rc, *NOISE_DDBM_RANGE) is None:
            return None
        return rc / 10.0

    def cad(self) -> bool:
        """Starts one channel-activity detection; the result arrives in last_cad."""
        return self._call("rf_cad") == 1

    def wake(self, cltu: bytes) -> bool:
        """Queues one CLTU with the wake-up preamble (GDS WAKE directive)."""
        if not 1 <= len(cltu) <= RF_TX_MAX_OCTETS:
            raise ValueError(f"wake-up CLTU of {len(cltu)} octets outside 1..{RF_TX_MAX_OCTETS}")
        ok = self._call("rf_wake", cltu.hex()) == 1
        self._count("tx_sent" if ok else "tx_busy")
        return ok

    # ---------------------------------------------------------- status
    @property
    def connected(self) -> bool:
        t = self.mcu_t
        return t is not None and self.clock() - t < STATUS_STALE_S

    def get_config(self) -> dict | None:
        """Active MCU configuration in the key set of e22.describe() that the GDS
        compares with station.toml, plus the SX126x specifics."""
        st = self.mcu
        if st is None or not self.connected:
            return None
        return {"model": SX126X, "channel": st["channel"], "freq_mhz": channel_freq_mhz(st["channel"]),
                "air_rate_code": st["rate_code"], "air_rate": rate_label(SX126X, st["rate_code"]),
                "power_code": st["power_code"], "lbt": st["cad_before_tx"],
                "rssi_byte": True, "rssi_noise_enable": True,      # per-packet RSSI, rf_noise
                "rx_boost": st["rx_boost"], "rx_boost_verified": st["rx_boost_verified"],
                "chip": st["chip"], "fw": st["fw"], "state": st["state_name"]}

    def snapshot(self) -> dict:
        with self._lock:
            return {"connected": self.connected, "mcu": dict(self.mcu) if self.mcu else None,
                    "mcu_age_s": None if self.mcu_t is None else self.clock() - self.mcu_t,
                    "stats": dict(self.stats), "tx_backlog": len(self._tx_backlog),
                    "rx_queue": len(self._rx), "last_cad": self.last_cad, "last_tx": self.last_tx,
                    "events": list(self.events)[-10:]}
