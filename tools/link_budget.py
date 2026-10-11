#!/usr/bin/env python3
"""VGQ-1 link budget and T-RF-03 receiver-sensitivity analysis (433 MHz, every radio model).

Two subcommands (the radio tables come from ground/dssq/link.py, the single source):

  budget (default)  free-space link budget for one model / rate code / power code
      python3 tools/link_budget.py --model E22-400T33D --rate-code 2 --distance-km 25 --gr 12
      python3 tools/link_budget.py budget --model SX126x --rate-code 1 --all-rates
      python3 tools/link_budget.py budget --config ground/config/station.toml
  sensitivity       docs/08 test T-RF-03: stepped attenuation -> measured sensitivity
      python3 tools/link_budget.py sensitivity --archive ground/archive --steps steps.csv --ref-dbm -40
      python3 tools/link_budget.py sensitivity --archive ground/archive --live --rate-code 2

Physics (all standard):
  FSPL(dB)            = 32.44 + 20 log10(f_MHz) + 20 log10(d_km)
  Radio horizon (km)  = 4.12 (sqrt(h1_m) + sqrt(h2_m))            (4/3-earth refraction)
  1st Fresnel radius  = 17.32 sqrt(d1 d2 / (f_GHz d))  m            (d in km)
  Shannon limit       = -174 + 10 log10(Rb) - 1.59 dB + NF          (no receiver can beat it)
  Sensitivity         = link.sensitivity(): measured > datasheet > estimated / computed,
                        always printed with its source

T-RF-03 method. During the session the GDS archives every frame (archive/gds.sqlite,
table frames: ert, ok, rssi, snr; fallback archive/YYYYMMDD/cadu.bin, RS-decoded here).
The operator inserts attenuation in steps; each step is a row of the steps CSV
    start_utc,end_utc,atten_db,rate_code[,model]
(ISO 8601 UTC or UNIX seconds; model defaults to --model / [radio] model) or is
time-stamped interactively with --live. Per step:
    expected frames  = round(duration / frame period)   (the spacecraft transmits
                       continuously; period from --frame-period-s or the RF packet's
                       frame_period_ms in the archive)
    FER              = 1 - frames decoded OK / expected (clamped to 0..1)
    received power   = P_ref - atten_db, P_ref = received power at 0 dB step attenuation:
                       --ref-dbm (measured path) or, without it, mean RSSI (+ calibration
                       offset, T-RF-02) + atten of the strongest error-free step
The sensitivity is the received power at FER = 10 %, linearly interpolated between the
two adjacent steps that bracket it. The tool REFUSES to report a value when the FER
never crosses 10 % (it prints why) and exits 2. Output: TOML lines for station.toml
[radio.sensitivity_measured]. Nobody may enter measured values without this test.
"""
from __future__ import annotations

import argparse
import csv
import math
import sqlite3
import statistics
import struct
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ground"))
from dssq import link  # noqa: E402

FER_THRESHOLD = 0.10                   # sensitivity criterion (T-RF-03)
ERROR_FREE_FER = 0.01                  # steps usable as the RSSI reference
MIN_EXPECTED_FRAMES = 10               # shorter steps are reported but not used
MAX_LIVE_STEPS = 64                    # bound of the interactive session
LIVE_SETTLE_S = 3.0                    # the GDS commits the archive every 2 s
EXIT_OK, EXIT_NO_DATA, EXIT_REFUSED = 0, 1, 2


# ============================================================= link budget
def fspl(f_mhz: float, d_km: float) -> float:
    return 32.44 + 20 * math.log10(f_mhz) + 20 * math.log10(d_km)


def _load_radio_cfg(path: str | None) -> dict:
    if not path:
        return {}
    from dssq.config import load_config
    return dict(load_config(path)["radio"])


def budget(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="link_budget.py budget", description="free-space link budget")
    ap.add_argument("--config", help="station.toml: model, channel, rate / power code, measured table")
    ap.add_argument("--model", help=f"{', '.join(link.MODELS)} (default {link.DEFAULT_MODEL})")
    ap.add_argument("--channel", type=int)
    ap.add_argument("--power-code", type=int, help="TX power code 0..3 (meaning per model)")
    ap.add_argument("--rate-code", "--air-rate", dest="rate_code", type=int,
                    help="E22 air-rate code / SX126x LoRa profile 0..7")
    ap.add_argument("--pa-gain-db", type=float, help=f"SX126x module PA gain (default {link.SX126X_PA_GAIN_DB:g})")
    ap.add_argument("--no-rx-boost", action="store_true", help="SX126x receiver in power-saving gain")
    ap.add_argument("--gt", type=float, default=2.15, help="TX antenna gain dBi")
    ap.add_argument("--gr", type=float, default=2.15, help="RX antenna gain dBi")
    ap.add_argument("--lt", type=float, default=0.5, help="TX feeder loss dB")
    ap.add_argument("--lr", type=float, default=1.0, help="RX feeder loss dB")
    ap.add_argument("--distance-km", type=float, default=10.0)
    ap.add_argument("--h1", type=float, default=2.0, help="TX antenna height above ground, m")
    ap.add_argument("--h2", type=float, default=10.0, help="RX antenna height above ground, m")
    ap.add_argument("--nf", type=float, default=6.0, help="receiver noise figure dB (Shannon line)")
    ap.add_argument("--fade-margin", type=float, default=10.0)
    ap.add_argument("--all-rates", action="store_true", help="also tabulate every rate code")
    a = ap.parse_args(argv)
    try:
        r = _load_radio_cfg(a.config)
        for key, val in (("model", a.model), ("channel", a.channel), ("power", a.power_code),
                         ("air_rate", a.rate_code)):
            if val is not None:
                r[key] = val
        sx = dict(r.get("sx126x") or {})
        if a.pa_gain_db is not None:
            sx["pa_gain_db"] = a.pa_gain_db
        if a.no_rx_boost:
            sx["rx_boosted_gain"] = False
        r["sx126x"] = sx
        s = link.RadioSettings.from_config(r)
        if a.distance_km <= 0 or a.h1 < 0 or a.h2 < 0:
            raise ValueError("distance must be > 0 and antenna heights >= 0")
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_NO_DATA
    m = s.model
    f = link.channel_freq_mhz(s.channel)
    p = link.power_dbm(m, s.power_code, s.pa_gain_db)
    eirp = p + a.gt - a.lt
    sens, src = s.sensitivity
    rb = link.rate_bps(m, s.rate_code)
    air, air_src = link.airtime_s(m, s.rate_code)
    shannon = -174 + 10 * math.log10(rb) - 1.59 + a.nf
    loss = fspl(f, a.distance_km)
    pr = eirp + a.gr - a.lr - loss
    allow = eirp + a.gr - a.lr - sens - a.fade_margin
    d_max = 10 ** ((allow - 32.44 - 20 * math.log10(f)) / 20)
    horizon = 4.12 * (math.sqrt(a.h1) + math.sqrt(a.h2))
    r_fresnel = 17.32 * math.sqrt((a.distance_km / 2) ** 2 / (f / 1000 * a.distance_km))
    pwr_note = {link.E22_T33D: "  (power ladder VERIFY: T-RF-07)",
                link.SX126X: f"  (drive + {s.pa_gain_db:g} dB module PA gain: VERIFY)"}.get(m.name, "")
    rows = [
        ("Radio model", f"{m.name} - {m.description}"),
        ("Frequency", f"{f:.3f} MHz (CH{s.channel}), lambda = {299.792458 / f:.3f} m"),
        ("TX power (nominal for code)", f"{p:.1f} dBm = {10 ** (p / 10) / 1000:.2f} W (code {s.power_code}){pwr_note}"),
        ("TX antenna gain / feeder loss", f"{a.gt:+.2f} dBi / {a.lt:.1f} dB"),
        ("EIRP", f"{eirp:.1f} dBm"),
        ("Distance", f"{a.distance_km:.2f} km  (OWLT {a.distance_km / 299792.458 * 1e6:.2f} us)"),
        ("Free-space path loss", f"{loss:.1f} dB"),
        ("RX antenna gain / feeder loss", f"{a.gr:+.2f} dBi / {a.lr:.1f} dB"),
        ("Received power (free space)", f"{pr:.1f} dBm"),
        ("Rate / bit rate", f"code {s.rate_code} = {link.rate_label(m, s.rate_code)}, {rb:,.1f} bit/s"),
        ("Packet air time (236-octet CADU)", f"{air * 1000:,.1f} ms ({air_src})"),
        ("Receiver sensitivity", f"{sens:.1f} dBm ({src})"),
        ("Shannon-limit sensitivity at this rate", f"{shannon:.1f} dBm (NF {a.nf:.0f} dB): physically unbeatable"),
        ("Free-space margin", f"{pr - sens:.1f} dB"),
        (f"Max free-space range with {a.fade_margin:.0f} dB fade margin", f"{d_max:,.0f} km"),
        ("Radio horizon for these heights", f"{horizon:.1f} km  <-- usually the REAL limit"),
        ("1st Fresnel radius at mid-path", f"{r_fresnel:.1f} m (keep >= 60 % = {0.6 * r_fresnel:.1f} m clear)"),
    ]
    if m.packet_mode:
        prof = link.SX126X_PROFILES[s.rate_code]
        lim = s.freq_offset_limit_hz or 0.0
        rows.insert(10, ("LoRa symbol time / LDRO", f"{prof.symbol_s * 1000:.3f} ms / {'on' if prof.ldro else 'off'}"))
        rows.insert(12, ("Max TX-RX frequency offset (FERR_L)",
                         f"+-{lim:,.0f} Hz ({lim / (f * 1e6) * 1e6:.1f} ppm); TCXO at both ends below BW 62.5 kHz"))
    w = max(len(k) for k, _ in rows)
    print(f"\n| {'Item'.ljust(w)} | Value |\n|{'-' * (w + 2)}|-------|")
    for k, v in rows:
        print(f"| {k.ljust(w)} | {v} |")
    if a.all_rates:
        print("\n| code | rate | bit/s | air time 236 B | sensitivity | source | "
              f"max range ({a.fade_margin:.0f} dB fade) |")
        print("|---|---|---|---|---|---|---|")
        for c in link.RATE_CODES:
            sc, sr = link.sensitivity(m, c, s.measured, s.rx_boost)
            at, _ = link.airtime_s(m, c)
            dm = 10 ** ((eirp + a.gr - a.lr - sc - a.fade_margin - 32.44 - 20 * math.log10(f)) / 20)
            print(f"| {c} | {link.rate_label(m, c)} | {link.rate_bps(m, c):,.1f} | {at * 1000:,.1f} ms | "
                  f"{sc:.1f} dBm | {sr} | {dm:,.0f} km |")
    if a.distance_km > horizon:
        print(f"\nWARNING: {a.distance_km} km is beyond the {horizon:.1f} km radio horizon: no line of sight; "
              "raise the antennas or use terrain (hill/mountain/balloon).")
    return EXIT_OK


# =================================================== T-RF-03 sensitivity
@dataclass(frozen=True)
class Step:
    start: float                       # UNIX s
    end: float
    atten_db: float
    rate_code: int
    model: str


@dataclass(frozen=True)
class FrameObs:
    ert: float
    ok: bool
    rssi: float | None
    snr: float | None


@dataclass
class StepResult:
    step: Step
    expected: int
    ok: int
    bad: int
    fer: float | None
    rssi_mean: float | None
    rssi_sem: float | None
    snr_mean: float | None
    p_rx: float | None = None
    usable: bool = True


def parse_time(txt: str) -> float:
    """ISO 8601 (Z / offset; naive = UTC) or UNIX seconds."""
    t = txt.strip()
    try:
        v = float(t)
    except ValueError:
        d = datetime.fromisoformat(t.replace("Z", "+00:00"))
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
    if not math.isfinite(v) or v < 0:
        raise ValueError(f"bad time {txt!r}")
    return v


def read_steps(path: Path, default_model: str) -> list[Step]:
    """Validated steps CSV; raises ValueError naming the line."""
    steps: list[Step] = []
    with open(path, newline="") as fh:
        rows = list(csv.reader(fh))
    if not rows or [c.strip().lower() for c in rows[0][:4]] != ["start_utc", "end_utc", "atten_db", "rate_code"]:
        raise ValueError(f"{path}: header must be start_utc,end_utc,atten_db,rate_code[,model]")
    for n, row in enumerate(rows[1:], start=2):
        if not row or all(not c.strip() for c in row):
            continue
        if len(row) not in (4, 5):
            raise ValueError(f"{path}:{n}: expected 4 or 5 columns")
        try:
            start, end = parse_time(row[0]), parse_time(row[1])
            atten = float(row[2])
            code = int(row[3])
            model = link.get_model(row[4] if len(row) == 5 and row[4].strip() else default_model).name
        except ValueError as e:
            raise ValueError(f"{path}:{n}: {e}") from None
        if not end > start:
            raise ValueError(f"{path}:{n}: end_utc must be after start_utc")
        if not math.isfinite(atten) or atten < 0:
            raise ValueError(f"{path}:{n}: atten_db must be a finite value >= 0")
        if not 0 <= code <= 7:
            raise ValueError(f"{path}:{n}: rate_code {code} outside 0..7")
        steps.append(Step(start, end, atten, code, model))
    if not steps:
        raise ValueError(f"{path}: no steps")
    return steps


def write_steps(path: Path, steps: Iterable[Step]) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["start_utc", "end_utc", "atten_db", "rate_code", "model"])
        for s in steps:
            w.writerow([datetime.fromtimestamp(s.start, timezone.utc).isoformat(timespec="milliseconds"),
                        datetime.fromtimestamp(s.end, timezone.utc).isoformat(timespec="milliseconds"),
                        f"{s.atten_db:g}", s.rate_code, s.model])


def _db(archive: Path) -> sqlite3.Connection | None:
    p = archive / "gds.sqlite"
    if not p.exists():
        return None
    return sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5.0)


def load_frames(archive: Path, t0: float, t1: float, source: str = "auto") -> list[FrameObs]:
    """Frames received in [t0, t1): from gds.sqlite, or decoded from the raw cadu.bin."""
    db = _db(archive) if source in ("auto", "sqlite") else None
    if db is not None:
        try:
            rows = db.execute("SELECT ert, ok, rssi, snr FROM frames WHERE ert >= ? AND ert < ? ORDER BY ert",
                              (t0, t1)).fetchall()
        finally:
            db.close()
        return [FrameObs(float(e), bool(ok), None if r is None else float(r), None if s is None else float(s))
                for e, ok, r, s in rows]
    if source == "sqlite":
        raise FileNotFoundError(f"{archive / 'gds.sqlite'} not found")
    from dssq.ccsds.tm import decode_codeblock
    out: list[FrameObs] = []
    for f in sorted(archive.glob("*/cadu.bin")):
        data = f.read_bytes()
        i = 0
        while i + 12 <= len(data):                         # record: >q h H + CADU
            ns, rssi, n = struct.unpack_from(">qhH", data, i)
            i += 12
            cadu = data[i:i + n]
            i += n
            ert = ns / 1e9
            if t0 <= ert < t1 and len(cadu) == n:
                out.append(FrameObs(ert, decode_codeblock(cadu[4:]).ok, None if rssi == 0x7FFF else float(rssi), None))
    out.sort(key=lambda o: o.ert)
    return out


def frame_period_from_archive(archive: Path, t0: float, t1: float) -> float | None:
    """Median RF frame_period_ms in [t0, t1) (spacecraft telemetry), in seconds."""
    db = _db(archive)
    if db is None:
        return None
    try:
        vals = [v for (v,) in db.execute("SELECT value FROM params WHERE packet = 'RF' AND name = "
                                         "'frame_period_ms' AND ert >= ? AND ert < ?", (t0, t1)) if v and v > 0]
    finally:
        db.close()
    return statistics.median(vals) / 1000.0 if vals else None


def measure_steps(steps: list[Step], frames: list[FrameObs], period_s: float,
                  min_expected: int = MIN_EXPECTED_FRAMES) -> list[StepResult]:
    if not period_s > 0:
        raise ValueError("frame period must be > 0")
    out = []
    for s in steps:
        sel = [o for o in frames if s.start <= o.ert < s.end]
        good = [o for o in sel if o.ok]
        rssi = [o.rssi for o in good if o.rssi is not None]
        snr = [o.snr for o in good if o.snr is not None]
        expected = round((s.end - s.start) / period_s)
        fer = None if expected < 1 else min(1.0, max(0.0, 1.0 - len(good) / expected))
        out.append(StepResult(s, expected, len(good), len(sel) - len(good), fer,
                              statistics.fmean(rssi) if rssi else None,
                              statistics.stdev(rssi) / math.sqrt(len(rssi)) if len(rssi) > 1 else None,
                              statistics.fmean(snr) if snr else None, usable=expected >= min_expected))
    return out


def reference_power(results: list[StepResult], ref_dbm: float | None, rssi_cal_db: float) -> tuple[float, str]:
    """Received power at 0 dB step attenuation and how it was obtained."""
    if ref_dbm is not None:
        return ref_dbm, "operator (--ref-dbm)"
    clean = [r for r in results if r.usable and r.fer is not None and r.fer <= ERROR_FREE_FER
             and r.rssi_mean is not None]
    if not clean:
        raise ValueError("no error-free step with RSSI to derive the reference: pass --ref-dbm")
    best = max(clean, key=lambda r: r.rssi_mean)                # strongest = most accurate RSSI
    ref = best.rssi_mean + rssi_cal_db + best.step.atten_db
    return ref, (f"RSSI {best.rssi_mean:.1f} dBm {rssi_cal_db:+.1f} dB cal at atten "
                 f"{best.step.atten_db:g} dB (strongest error-free step)")


def threshold_crossing(points: list[tuple[float, float]], thr: float = FER_THRESHOLD) -> tuple[float | None, str]:
    """points = (received power dBm, FER). Returns (power at FER = thr, explanation);
    None when the FER does not cross thr between two adjacent steps."""
    pts = sorted(points, key=lambda p: -p[0])                  # strongest first
    if len(pts) < 2:
        return None, "fewer than two usable steps"
    fers = [f for _, f in pts]
    if max(fers) < thr:
        return None, f"FER never reached {thr:.0%} (max {max(fers):.1%}): add attenuation"
    if min(fers) >= thr:
        return None, f"FER never fell below {thr:.0%} (min {min(fers):.1%}): reduce attenuation"
    for i in range(len(pts) - 1):
        (p0, f0), (p1, f1) = pts[i], pts[i + 1]
        if f0 < thr <= f1:
            p = p0 + (thr - f0) * (p1 - p0) / (f1 - f0)
            note = f"FER {f0:.1%} at {p0:.1f} dBm -> {f1:.1%} at {p1:.1f} dBm"
            if any(f < thr for _, f in pts[i + 2:]):
                note += "; WARNING: FER not monotonic beyond the crossing"
            return round(p, 1), note
    return None, f"FER does not rise through {thr:.0%} from the strongest step (check the setup)"


def analyse(steps: list[Step], frames: list[FrameObs], period_s: float, ref_dbm: float | None,
            rssi_cal_db: float, thr: float = FER_THRESHOLD, min_expected: int = MIN_EXPECTED_FRAMES) -> dict:
    results = measure_steps(steps, frames, period_s, min_expected)
    ref, ref_src = reference_power(results, ref_dbm, rssi_cal_db)
    for r in results:
        r.p_rx = ref - r.step.atten_db
    groups: dict[tuple[str, int], list[StepResult]] = {}
    for r in results:
        groups.setdefault((r.step.model, r.step.rate_code), []).append(r)
    verdicts = []
    for (model, code), rs in sorted(groups.items()):
        pts = [(r.p_rx, r.fer) for r in rs if r.usable and r.fer is not None and r.p_rx is not None]
        value, why = threshold_crossing(pts, thr)
        verdicts.append({"model": model, "rate_code": code, "sensitivity_dbm": value, "why": why,
                         "steps": len(rs), "used": len(pts)})
    return {"results": results, "ref_dbm": ref, "ref_source": ref_src, "period_s": period_s, "verdicts": verdicts}


def toml_lines(report: dict, when: str) -> list[str]:
    lines = []
    for v in report["verdicts"]:
        if v["sensitivity_dbm"] is not None:
            key = f'"{v["model"]}:{v["rate_code"]}"'
            lines.append(f'{key} = {v["sensitivity_dbm"]:.1f}    # T-RF-03 {when}: {v["why"]}')
    return lines


def print_report(report: dict, out: Callable[[str], None] = print) -> None:
    out(f"\nframe period {report['period_s']:.3f} s; P_ref (0 dB step attenuation) = "
        f"{report['ref_dbm']:.1f} dBm from {report['ref_source']}")
    out("\n| step start (UTC) | s | model:code | atten dB | P_rx dBm | expected | ok | bad | FER "
        "| RSSI dBm (+-SEM) | SNR dB |")
    out("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in report["results"]:
        s = r.step
        t = datetime.fromtimestamp(s.start, timezone.utc).strftime("%H:%M:%S")
        sem = "" if r.rssi_sem is None else f" +-{r.rssi_sem:.2f}"
        rssi = "-" if r.rssi_mean is None else f"{r.rssi_mean:.1f}{sem}"
        fer = "-" if r.fer is None else f"{r.fer:.1%}"
        out(f"| {t} | {s.end - s.start:.0f} | {s.model}:{s.rate_code} | {s.atten_db:g} | {r.p_rx:.1f} "
            f"| {r.expected} "
            f"| {r.ok} | {r.bad} | {fer}{'' if r.usable else ' (too short, unused)'} | {rssi} | "
            f"{'-' if r.snr_mean is None else f'{r.snr_mean:.1f}'} |")
    out("")
    for v in report["verdicts"]:
        val = "REFUSED" if v["sensitivity_dbm"] is None else f"{v['sensitivity_dbm']:.1f} dBm"
        out(f"{v['model']}:{v['rate_code']}: sensitivity at FER {FER_THRESHOLD:.0%} = {val} ({v['why']})")


def run_live(rate_code: int, model: str, input_fn: Callable[[str], str] = input,
             clock: Callable[[], float] = time.time, out: Callable[[str], None] = print) -> list[Step]:
    """Interactive session: the operator sets each attenuation and ends each step."""
    out("T-RF-03 live session: the GDS must be running and archiving. For each step set the "
        "attenuator, enter its value, wait, then press Enter to close the step.")
    steps: list[Step] = []
    for n in range(1, MAX_LIVE_STEPS + 1):
        txt = input_fn(f"step {n}: attenuation in dB (Enter = finish): ").strip()
        if not txt:
            break
        try:
            atten = float(txt)
        except ValueError:
            out(f"  not a number: {txt!r}")
            continue
        if not math.isfinite(atten) or atten < 0:
            out("  attenuation must be a finite value >= 0")
            continue
        start = clock()
        input_fn("  receiving ... press Enter to close this step: ")
        steps.append(Step(start, clock(), atten, rate_code, model))
    return steps


def sensitivity(argv: list[str], input_fn: Callable[[str], str] = input, clock: Callable[[], float] = time.time,
                sleep: Callable[[float], None] = time.sleep, out: Callable[[str], None] = print) -> int:
    ap = argparse.ArgumentParser(prog="link_budget.py sensitivity", description="T-RF-03 sensitivity analysis")
    ap.add_argument("--archive", required=True, help="GDS archive directory ([gds] archive_dir)")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--steps", help="CSV: start_utc,end_utc,atten_db,rate_code[,model]")
    src.add_argument("--live", action="store_true", help="time-stamp the steps interactively")
    ap.add_argument("--config", help="station.toml (model, rssi_cal_offset_db)")
    ap.add_argument("--model", help="default model for the steps")
    ap.add_argument("--rate-code", type=int, help="rate code of a --live session")
    ap.add_argument("--steps-out", help="--live: where to save the steps CSV")
    ap.add_argument("--frame-period-s", type=float, help="spacecraft frame period (default: from the archive)")
    ap.add_argument("--ref-dbm", type=float, help="received power at 0 dB step attenuation (measured path)")
    ap.add_argument("--rssi-cal-db", type=float, help="RSSI calibration offset (default [radio] rssi_cal_offset_db)")
    ap.add_argument("--min-expected", type=int, default=MIN_EXPECTED_FRAMES)
    ap.add_argument("--source", choices=("auto", "sqlite", "cadu"), default="auto")
    a = ap.parse_args(argv)
    archive = Path(a.archive)
    try:
        r = _load_radio_cfg(a.config)
        model = link.get_model(a.model or r.get("model")).name
        rssi_cal = a.rssi_cal_db if a.rssi_cal_db is not None else float(r.get("rssi_cal_offset_db", 0.0))
        if not archive.is_dir():
            raise ValueError(f"archive directory {archive} not found")
        if a.live:
            if a.rate_code is None or not 0 <= a.rate_code <= 7:
                raise ValueError("--live needs --rate-code 0..7")
            steps = run_live(a.rate_code, model, input_fn, clock, out)
            if not steps:
                raise ValueError("no steps recorded")
            dest = Path(a.steps_out) if a.steps_out else archive / time.strftime(
                "trf03_steps_%Y%m%dT%H%M%SZ.csv", time.gmtime(steps[0].start))
            write_steps(dest, steps)
            out(f"steps saved to {dest}")
            sleep(LIVE_SETTLE_S)
        else:
            steps = read_steps(Path(a.steps), model)
        t0, t1 = min(s.start for s in steps), max(s.end for s in steps)
        period = a.frame_period_s or frame_period_from_archive(archive, t0, t1)
        if not period or period <= 0:
            raise ValueError("frame period unknown: pass --frame-period-s")
        frames = load_frames(archive, t0, t1, a.source)
        if not frames:
            out("no frames archived during the steps (is the GDS archiving to this directory?)")
            return EXIT_NO_DATA
        report = analyse(steps, frames, period, a.ref_dbm, rssi_cal, FER_THRESHOLD, a.min_expected)
    except (ValueError, FileNotFoundError, sqlite3.Error) as e:
        out(f"error: {e}")
        return EXIT_NO_DATA
    print_report(report, out)
    lines = toml_lines(report, datetime.fromtimestamp(t0, timezone.utc).strftime("%Y-%m-%d"))
    if lines:
        out("\n# add to ground/config/station.toml:\n[radio.sensitivity_measured]")
        for line in lines:
            out(line)
    refused = [f"{v['model']}:{v['rate_code']}" for v in report["verdicts"] if v["sensitivity_dbm"] is None]
    if refused:
        out(f"\nREFUSED for {', '.join(refused)}: no value is reported where the FER did not cross 10 %.")
        return EXIT_REFUSED
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] == "sensitivity":
        return sensitivity(args[1:])
    return budget(args[1:] if args and args[0] == "budget" else args)


if __name__ == "__main__":
    sys.exit(main())
