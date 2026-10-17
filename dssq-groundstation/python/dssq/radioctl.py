"""Ground radio control: configuration, RF survey and Wake-on-Radio procedures.

Two control paths behind one API (RadioManager), selected by the radio backend:

  E22 UART models (E22-400T33D default, E22-400T37S) - through the Radio Control Unit:
    the station UNO Q MCU drives the module's M0/M1 pins and reads AUX (the App's sketch,
    D2 / D3 / D7, RPC rcu_mode), so the GDS can enter CONFIG mode for register read-back and
    volatile (C2) writes, read the ambient-noise register, and use the module's WOR
    transmitter role. Model differences: the T33D's REG1 b4..b2 are reserved and are
    never written 1 (e22.sanitize_reg1); power codes mean different dBm per model.
  SX126x (packet backend radio/sx126x.py) - through the Router Bridge RPCs rf_cfg /
    rf_noise / rf_wake of the same MCU, which owns the SPI radio; no M0/M1 pins.

  * configuration read-back - verifies the station against station.toml
  * RF spectrum survey - step channels, sample the noise floor on each
  * wake-up transmission - long-preamble wake-up of a hibernating spacecraft (E22 WOR
    transmitter role; SX126x 32-symbol preamble via rf_wake)
  * the ground half of coordinated air-rate / channel / power changes

All procedures run in the GDS radio thread; they briefly take the radio off the
operational channel (the station is deaf meanwhile), so the GDS schedules them right
after a frame has been received - a survey or wake-up only once that window's own uplink
transmissions are over (gds.GroundStation.run_radio_jobs), since a retuned SX126x would
send uplinks still queued on its MCU on the surveyed channel.
"""
from __future__ import annotations

import time
from collections.abc import Callable

from .link import CHANNEL_MAX, SX126X, RadioModel, get_model
from .radio.e22 import NOISE_QUERY, describe, sanitize_reg1

NORMAL, WOR, CONFIG, SLEEP = 0, 1, 2, 3
MODE_SETTLE_S = 0.05                   # E22: >= 2 ms after a mode change, plus AUX settle
CHANNEL_SETTLE_S = 0.06                # receiver settles on the new channel before a noise sample
WOR_TAIL_S = 1.0                       # payload time after the WOR preamble (E22 manual 6.3)
# rf_cfg results (station sketch rf_cfg_exec / rpc_rf_cfg, radio/sx126x.py CFG_*): 1 applied now;
# 2 accepted and queued - the MCU applies it as soon as the radio is idle (TX, CAD or a packet
# arriving), before any further reception or transmission, so it is a success; -1 out of range
# and -2 MCU busy (nothing changed) are failures.
SX_APPLIED = 1
SX_QUEUED = 2
SURVEY_RESTORE_TRIES = 3               # attempts to return to the home channel after a survey


class RadioManager:
    """Model-aware ground radio procedures. `radio` is the GDS radio backend;
    `rcu` an object with set_mode(int) -> bool (station MCU or simulator), None
    for packet backends. `model` defaults to the backend's model (SX126x for a packet
    backend, else the default E22 model)."""

    def __init__(self, radio, rcu, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep, model: str | RadioModel | None = None):
        self.radio = radio
        self.rcu = rcu
        self.clock = clock
        self.sleep = sleep
        self.packet = bool(getattr(radio, "packet_mode", False))
        self.model = get_model(model if model is not None else (SX126X if self.packet else None))
        if self.packet != self.model.packet_mode:
            raise ValueError(f"radio backend does not match model {self.model.name}")
        self.regs: bytes | None = None
        self.last_scan: dict | None = None
        self.survey_refused: str | None = None               # why the last survey() did not start
        self.log: list[str] = []

    @property
    def available(self) -> bool:
        if self.packet:
            return bool(getattr(self.radio, "connected", False))
        return self.rcu is not None

    def _note(self, text: str) -> None:
        self.log.append(text)
        del self.log[:-100]                                  # bounded history

    # ------------------------------------------------------------- primitives
    def _mode(self, m: int) -> bool:
        ok = bool(self.rcu.set_mode(m))
        self.sleep(MODE_SETTLE_S)
        return ok

    def _wait(self, prefix: bytes, n: int, timeout: float = 1.0) -> bytes | None:
        buf, t0 = b"", self.clock()
        while self.clock() - t0 < timeout:
            buf += self.radio.read()
            i = buf.find(prefix)
            if i >= 0 and len(buf) - i >= n:
                return buf[i:i + n]
            self.sleep(0.01)
        return None

    def read_config(self) -> dict | None:
        if not self.available:
            return None
        if self.packet:
            cfg = self.radio.get_config()
            if cfg is None:
                self._note("ground SX126x: no status from the station MCU")
            return cfg
        if not self._mode(CONFIG):
            self._mode(NORMAL)                               # never leave the radio half-switched
            return None
        self.radio.write(bytes([0xC1, 0x00, 0x09]))
        r = self._wait(bytes([0xC1, 0x00, 0x09]), 12)
        self._mode(NORMAL)
        if r is None:
            self._note("ground E22 config read: no reply")
            return None
        self.regs = r[3:]
        return describe(self.regs, self.model)

    def write_regs(self, start: int, values: bytes) -> bool:
        """E22 volatile register write (C2) with echo check, back to NORMAL mode."""
        if self.packet:
            self._note("write_regs: the SX126x has no E22 register set")
            return False
        if not 0 <= start <= 8 or not values or start + len(values) > 9:
            raise ValueError(f"register range {start}+{len(values)} outside 00..08")
        if start <= 4 < start + len(values):                 # REG1: never set reserved bits
            v = bytearray(values)
            v[4 - start] = sanitize_reg1(v[4 - start], self.model)
            values = bytes(v)
        if not self.available:
            return False
        if not self._mode(CONFIG):
            self._mode(NORMAL)                               # never leave the radio half-switched
            return False
        self.radio.write(bytes([0xC2, start, len(values)]) + values)
        r = self._wait(bytes([0xC1, start, len(values)]), 3 + len(values))
        self._mode(NORMAL)
        ok = r is not None and r[3:] == values
        if ok and self.regs is not None:
            regs = bytearray(self.regs)
            regs[start:start + len(values)] = values
            self.regs = bytes(regs)
        return ok

    def _cached_regs(self) -> bytes | None:
        """E22 registers 00..08 as last read back (read once on demand), None if unreadable."""
        if self.regs is None and self.read_config() is None:
            return None
        return self.regs

    def _sx_configure(self, **kw: int) -> int | None:
        """rf_cfg result code (SX_APPLIED, SX_QUEUED, a negative MCU code), None when the
        radio is not available or did not answer."""
        if not self.available:
            return None
        rc = self.radio.configure(**kw)
        return rc if isinstance(rc, int) and not isinstance(rc, bool) else None

    def set_channel(self, ch: int) -> bool:
        if not 0 <= ch <= CHANNEL_MAX:
            raise ValueError(f"channel {ch} outside 0..{CHANNEL_MAX}")
        if self.packet:
            return self._sx_configure(channel=ch) in (SX_APPLIED, SX_QUEUED)
        return self.write_regs(0x05, bytes([ch]))

    def _tune_now(self, ch: int) -> bool:
        """Survey step: True only when the receiver IS on `ch` now. A queued SX126x change
        (SX_QUEUED) takes effect after the packet in progress, so a noise sample taken
        right away could still come from the previous channel: no sample then. The SX126x
        step is transient: the backend's station configuration keeps the home channel."""
        if not 0 <= ch <= CHANNEL_MAX:
            raise ValueError(f"channel {ch} outside 0..{CHANNEL_MAX}")
        if self.packet:
            return self._sx_configure(channel=ch, transient=True) == SX_APPLIED
        return self.set_channel(ch)

    def off_home(self) -> str | None:
        """Why the SX126x station holds its uplinks after a survey (the backend's TX gate: the
        MCU has not yet reported its channel again), None otherwise."""
        fn = getattr(self.radio, "home_unconfirmed", None)
        return fn() if self.packet and callable(fn) else None

    def tx_busy(self) -> str | None:
        """Why the station transmitter may still key up (SX126x backend: uplinks queued on
        the MCU or in the backlog, TX or CAD in progress), None when idle or not known. The
        E22 path reports nothing here: the GDS waits for the end of its window plan instead."""
        fn = getattr(self.radio, "tx_busy", None)
        return fn() if self.packet and callable(fn) else None

    def set_air_rate(self, code: int) -> bool:
        if not 0 <= code <= 7:
            raise ValueError(f"rate code {code} outside 0..7")
        if self.packet:
            return self._sx_configure(rate_code=code) in (SX_APPLIED, SX_QUEUED)
        regs = self._cached_regs()
        if regs is None:
            return False
        reg0 = (regs[3] & ~0x07) | code
        return self.write_regs(0x03, bytes([reg0]))

    def set_power(self, code: int) -> bool:
        """Ground transmit power code 0..3 (meaning per model, link.power_dbm)."""
        if not 0 <= code <= 3:
            raise ValueError(f"power code {code} outside 0..3")
        if self.packet:
            return self._sx_configure(power_code=code) in (SX_APPLIED, SX_QUEUED)
        regs = self._cached_regs()
        if regs is None:
            return False
        reg1 = (regs[4] & ~0x03) | code
        return self.write_regs(0x04, bytes([reg1]))

    def noise_dbm(self) -> float | None:
        """Ambient noise on the current channel (E22 register / SX126x RSSI median)."""
        if self.packet:
            return self.radio.noise_dbm() if self.available else None
        self.radio.write(NOISE_QUERY)
        r = self._wait(bytes([0xC1, 0x00, 0x02]), 5, 0.5)
        return None if r is None else -(256 - r[3])

    # ------------------------------------------------------------ procedures
    def survey(self, first: int, last: int, home: int | None = None) -> dict | None:
        """Ground RF survey: noise floor on each channel (the station is deaf meanwhile).
        home: the channel to return to - the GDS passes its operational channel (the one it
        last set, applied or queued: a queued SX126x change is not yet the active channel);
        None reads the radio's active channel (direct use, the simulator). Refused (None,
        survey_refused says why) while the SX126x station may still transmit (tx_busy()): the
        MCU would send its queued uplinks on a surveyed channel. last_scan['home_restored']
        is False when the radio did not accept the return after SURVEY_RESTORE_TRIES."""
        if not 0 <= first <= last <= CHANNEL_MAX:
            raise ValueError(f"survey range {first}..{last} outside 0..{CHANNEL_MAX}")
        if home is not None and not 0 <= home <= CHANNEL_MAX:
            raise ValueError(f"home channel {home} outside 0..{CHANNEL_MAX}")
        self.survey_refused = None
        if not self.available:
            return None
        busy = self.tx_busy()
        if busy is not None:
            self.survey_refused = f"station transmitter busy: {busy}"
            self._note(f"survey refused: {self.survey_refused}")
            return None
        if home is None:
            cfg = self.read_config() if self.packet or self.regs is None else describe(self.regs, self.model)
            if cfg is None:
                return None
            home = int(cfg["channel"])
        t0 = self.clock()
        noise: list[float | None] = []
        for ch in range(first, last + 1):                    # bounded: at most 84 channels
            if not self._tune_now(ch):
                noise.append(None)
                continue
            self.sleep(CHANNEL_SETTLE_S)
            noise.append(self.noise_dbm())
        restored = False
        for attempt in range(SURVEY_RESTORE_TRIES):          # bounded; -2 / no answer: retry
            if attempt:
                self.sleep(MODE_SETTLE_S)
            restored = self.set_channel(home)
            if restored:
                break
        if not restored:
            self._note(f"survey: could not return to home channel {home}")
        valid = [(n, first + i) for i, n in enumerate(noise) if n is not None]
        best = min(valid)[1] if valid else None
        self.last_scan = {"t": self.clock(), "first": first, "noise": noise, "quietest": best,
                          "duration_s": self.clock() - t0, "home": home, "home_restored": restored}
        return self.last_scan

    def wor_transmit(self, cltu: bytes, wor_cycle_code: int = 3) -> bool:
        """Send one wake-up CLTU: E22 Wake-on-Radio transmitter role (preamble spans the
        WOR cycle), or for the SX126x the long wake-up preamble of the flight's
        duty-cycled receiver (rf_wake). wor_cycle_code applies to the E22 only."""
        if self.packet:
            return bool(self.available and self.radio.wake(cltu))
        if not 0 <= wor_cycle_code <= 7:
            raise ValueError(f"WOR cycle code {wor_cycle_code} outside 0..7")
        if not self.available:
            return False
        regs = self._cached_regs()
        if regs is None:
            return False
        reg3 = regs[6]
        wor_reg3 = (reg3 & ~0x0F) | 0x08 | wor_cycle_code             # WOR role = transmitter
        if not self.write_regs(0x06, bytes([wor_reg3])):
            return False
        self._mode(WOR)
        self.radio.write(cltu)
        self.sleep((wor_cycle_code + 1) * 0.5 + WOR_TAIL_S)          # preamble + payload
        self._mode(NORMAL)
        return self.write_regs(0x06, bytes([reg3]))                   # restore receiver role
