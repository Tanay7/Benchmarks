"""Station front panel + Radio Control Unit link (VENTUNO Q Linux <-> its own MCU).

The GDS runs as an ordinary Linux service on the VENTUNO Q's Dragonwing CPU and
talks to the board's STM32H5 (sketch ground/frontpanel/dssq_frontpanel) through
the Arduino Router: `pip install arduino-router-bridge`, socket
unix:///var/run/arduino-router.sock. No App Lab container is required.

GDS -> MCU   notify("fp_line", line)      display / console lines (<= 200 chars,
                                           the MCU's inbound RPC buffer is 256 B)
             call("rcu_mode", m) -> aux    drive ground E22 M0/M1, returns AUX state
MCU -> GDS   notify("fp_in", line)        CMD|text  CONFIRM|id  AUTH|id  H|hello

Line formats
  P|<n>|<title>|<line1>|...|<line7>   text page n (21 chars/line, 5x7 font)
  G|<v0>,...,<v17>                     spectrum page bars (0..100)
  S|<aos>|<alarm 0/1/2>|<uplink>        status LEDs / buzzer
  C|<text>                             console reply (CMD page)
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("dssq.fp")


def _f(v, d=1):
    return "-" if v is None else f"{v:.{d}f}"


def _i(v):
    return "-" if v is None else f"{int(round(v))}"


def _hms(s):
    if s is None:
        return "-"
    s = int(max(0, s))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def build_pages(s: dict) -> list[str]:
    rx, rf, fr, dec = s["rx"], s["rx"]["rf"], s["rx"]["frames"], s["rx"]["decoder"]
    hk = s["tlm"].get("HK", {}).get("values", {})
    tw = rx.get("two_way") or {}
    w1 = rx["windows"]["1m"]
    cop, t = s["cop"], s["time"]
    pages = []

    def page(title, *lines):
        # '|' is the field separator of the panel protocol: never let it into text
        pages.append("|".join([title.replace("|", "/")] +
                              [str(x).replace("|", "/")[:21] for x in lines] + [""] * (7 - len(lines))))

    page(f"{s['station']} {'LOS' if rx['state']['los'] else 'AOS'} {s['sync']['state']}",
         f"RSSI{_i(rf['rssi_dbm'])} N{_i(rf['noise_dbm'])} dBm",
         f"SNR{_f(rf['snr_est_db'])} M{_f(rf['margin_est_db'])}dB",
         f"FR{fr['ok']} L{fr['lost_mcfc']}",
         f"FER {_f(None if fr['fer'] is None else 100 * fr['fer'], 2)}%",
         f"RS {dec['symbols_corrected']} U{dec['uncorrectable']}",
         f"S/C {s['decoded']['mode'] or '-'}",
         f"ALARM {s['alarm']}")
    page("RF / LINK EST",
         f"{_f(rf['freq_mhz'], 3)} MHz",
         f"{_i(rf['air_rate_bps'])} bit/s",
         f"Pr {_f(rf['pr_est_dbm'])} dBm",
         f"C/N0 {_f(rf['cn0_est_dbhz'])} dBHz",
         f"Eb/N0 {_f(rf['ebn0_est_db'])} dB",
         f"Sens {_i(rf['sensitivity_est_dbm'])} dBm",
         f"Fade {_f(w1['rssi'].get('fade_depth'))} dB")
    r1, n1 = w1["rssi"], w1["snr"]
    page("RX STATS 1 MIN",
         f"RSSI mu{_f(r1.get('mean'))} s{_f(r1.get('std'))}",
         f"min{_f(r1.get('min'))} max{_f(r1.get('max'))}",
         f"SNR mu{_f(n1.get('mean'))} s{_f(n1.get('std'))}",
         f"IAT {_f(w1['iat'].get('mean'), 2)}s j{_f(w1['iat'].get('std'), 2)}",
         f"n={r1.get('n', 0)} frames",
         f"{_f(fr['frames_per_min'], 1)} frm/min",
         f"{_f(fr['info_rate_bps'], 0)} bit/s info")
    page("DECODER / SYNC",
         f"CB {dec['codeblocks']} cor {dec['corrected']}",
         f"sym {dec['symbols_corrected']} max {dec['max_corrections']}",
         f"uncorr {dec['uncorrectable']} FECF {dec['fecf_fail_after_rs']}",
         f"SER {dec['symbol_error_rate_est'] or 0:.2e}",
         f"ASM err {s['sync']['asm_bit_errors']}",
         f"slips {s['sync']['slips']} LL {s['sync']['lock_losses']}",
         f"lock {_f(rx['state']['time_in_lock_pct'])}%")
    page("S/C HEALTH",
         f"{s['decoded']['mode'] or '-'} up {_hms(hk.get('uptime_s'))}",
         f"AVI {_f(hk.get('avionics_temp'))}C PA {_f(hk.get('pa_temp'))}C",
         f"BUS {_f(hk.get('bus_voltage'), 2)}V {_f(hk.get('bus_current'), 2)}A",
         f"SSR {_f(hk.get('ssr_fill'))}%",
         f"FDIR {','.join(s['decoded']['fdir'])[:16] or 'none'}",
         f"miss {len(s['decoded']['sensors_missing'])} sensors",
         f"CMD {hk.get('cmd_accepted', '-')}/{hk.get('cmd_rejected', '-')}")
    page("UPLINK / COP-1",
         f"UL RSSI {_i(tw.get('uplink_rssi'))} dBm",
         f"SC noise {_i(tw.get('sc_noise_floor'))} dBm",
         f"CLTU {tw.get('cltu_ok', '-')} bad {tw.get('cltu_bad', '-')}",
         f"V(S){cop['vs']} N(R){cop['nr']}",
         f"{'LOCKOUT' if cop['lockout'] else 'FARM OPEN'} {'SUSP' if cop['suspended'] else ''}",
         f"SDLS {'ON' if cop['sdls'] else 'OFF'} SN{cop['sdls_sn']}",
         f"queued {cop['queued']} out {cop['outstanding']}")
    page("TIME",
         time.strftime("UTC %H:%M:%S", time.gmtime(s["utc"])),
         f"SCLK {t['partition']}/{_f(t['last_sclk'], 1)}",
         "SCET " + (time.strftime("%H:%M:%S", time.gmtime(t["scet_of_last_sclk"]))
                    if t["scet_of_last_sclk"] else "-"),
         f"drift {_f(t['drift_ppm'], 2)} ppm",
         f"resid {_f(t['residual_rms_ms'], 1)} ms",
         f"OWLT {_f(t['owlt_s'] * 1e6, 2)} us",
         f"samples {t['corr_samples']}")
    mag = s["tlm"].get("MAG", {}).get("values", {})
    att = s["tlm"].get("ATT", {}).get("values", {})

    def bmag(p):
        x, y, z = (mag.get(f"{p}_b_{a}") for a in "xyz")
        return None if x is None else (x * x + y * y + z * z) ** 0.5
    page("SCIENCE",
         f"Bob {_f(bmag('ob'), 2)} uT",
         f"Bib {_f(bmag('ib'), 2)} uT",
         f"Bbody {_f(bmag('body'), 2)} uT",
         f"sun {_f(att.get('sun_x'), 2)} {_f(att.get('sun_y'), 2)} {_f(att.get('sun_z'), 2)}",
         f"qT {_f(att.get('q_triad_w'), 3)} {_f(att.get('q_triad_x'), 3)}",
         f"   {_f(att.get('q_triad_y'), 3)} {_f(att.get('q_triad_z'), 3)}",
         f"ARU acc {att.get('aru_accuracy', '-')}")
    env = s["tlm"].get("ENV", {}).get("values", {})
    page("ENVIRONMENT",
         f"T {_f(env.get('bme_t'))}C RH {_f(env.get('bme_rh'))}%",
         f"P {_f(env.get('bme_p'), 1)} hPa",
         f"gas {_i(env.get('bme_gas'))} ohm",
         f"IAQ {_f(env.get('nenv_iaq'))} eCO2 {_i(env.get('nenv_eco2'))}",
         f"NO2 {_f(env.get('nenv_no2'))} O3 {_f(env.get('nenv_o3'))}",
         f"lux {_i(env.get('veml_lux'))}",
         f"BSEC IAQ {env.get('nme_iaq', '-')}")
    lm = s["linkmgr"]
    rec = lm.get("recommendation") or {}
    rd = s["radio"]
    page("LINK MGR / RADIO",
         f"LM {lm['mode']} tgt {lm['target_margin_db']}dB",
         f"{rec.get('action', '-')} {rec.get('value', '')}",
         f"p10m {_f(rec.get('margin_p10_db'))} dB",
         f"RCU {'yes' if rd['rcu'] else 'no'} jobs {rd['jobs_pending']}",
         f"CH{rd['configured']['channel']} air{rd['configured']['air_rate']}",
         "scan best CH" + str((rd.get('last_scan') or {}).get('quietest', '-')),
         ("chg " + rd['link_change']['state']) if rd.get('link_change') else "no change active")
    return pages


class _RouterTransport:
    def __init__(self, address: str | None = None):
        from arduino.router_bridge import DEFAULT_ADDRESS, Bridge
        self.bridge = Bridge(address or DEFAULT_ADDRESS)
        self.inbox: list[str] = []
        self.bridge.provide("fp_in", lambda line: self.inbox.append(str(line)))
        if not self.bridge.connect(timeout=10):
            log.warning("arduino-router not reachable yet; retrying in the background")

    def send(self, line: str):
        self.bridge.notify("fp_line", line[:200])

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
        self.hello: dict = {}
        self._last_console = 0

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
        while not self.stop_ev.is_set():
            line = self.t.readline()
            if not line:
                continue
            parts = line.split("|")
            kind = parts[0]
            if kind == "H":
                self.hello = {"fw": parts[1:2], "rcu": parts[2:3] == ["1"], "kb1": parts[3:4] == ["1"],
                              "kb2": parts[4:5] == ["1"]}
            elif kind == "CMD" and len(parts) > 1:
                r = self.gs.submit_command("|".join(parts[1:]), "cardkb")
                self.t.send("C|" + (r.get("message") or r.get("status") or "")[:60])
                if r.get("status") == "CONFIRM":
                    self.t.send(f"C|CONFIRM? #{r['id']} Enter=yes")
            elif kind in ("CONFIRM", "AUTH") and len(parts) > 1 and parts[1].isdigit():
                r = self.gs.confirm(int(parts[1]), kind)
                self.t.send("C|" + (r.get("message") or r.get("status") or "")[:60])

    def _tx_loop(self):
        while not self.stop_ev.is_set():
            try:
                s = self.gs.snapshot()
                for n, p in enumerate(build_pages(s)):
                    self.t.send(f"P|{n}|{p}")
                spec = s["tlm"].get("SPEC", {}).get("values", {})
                vals = [spec.get(f"as7265x_{nm}nm") for nm in (410, 435, 460, 485, 510, 535, 560, 585, 610,
                                                                 645, 680, 705, 730, 760, 810, 860, 900, 940)]
                top = max([v for v in vals if v] or [1])
                self.t.send("G|" + ",".join(str(int(100 * (v or 0) / top)) for v in vals))
                alarm = {"NONE": 0, "YELLOW": 1, "RED": 2}[s["alarm"]]
                uplink = int(s["cop"]["outstanding"] > 0 or s["cop"]["pending_bypass"] > 0)
                self.t.send(f"S|{0 if s['rx']['state']['los'] else 1}|{alarm}|{uplink}")
                for ts, text in s["console"]:
                    if ts > self._last_console:
                        self._last_console = ts
                        self.t.send("C|" + text[:60])
            except Exception as e:      # display must never take the GDS down
                log.warning("front panel update failed: %s", e)
            self.stop_ev.wait(1.0)
