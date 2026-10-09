#!/usr/bin/env python3
"""VGQ-1 link budget calculator (433 MHz E22-400T37S link).

Physics used (all standard):
  FSPL(dB)            = 32.44 + 20 log10(f_MHz) + 20 log10(d_km)
  Radio horizon (km)  = 4.12 (sqrt(h1_m) + sqrt(h2_m))           (4/3-earth refraction)
  1st Fresnel radius  = 17.32 sqrt(d1 d2 / (f_GHz d))  m           (d in km)
  LoRa sensitivity    = -174 + 10 log10(BW) + NF + SNR_req(SF)     (estimate)
  Shannon limit       = -174 + 10 log10(Rb) - 1.59 dB + NF         (no receiver can beat it)

Example:
  python3 tools/link_budget.py --distance-km 25 --gt 2.15 --gr 12 --air-rate 0
"""
import argparse
import math

SENS_EST = {0: -137.0, 1: -132.0, 2: -129.0, 3: -126.0, 4: -123.0, 5: -120.0, 6: -117.0, 7: -114.0}
RATE_BPS = {0: 300, 1: 1200, 2: 2400, 3: 4800, 4: 9600, 5: 19200, 6: 38400, 7: 62500}
PWR = {0: 37.0, 1: 34.0, 2: 31.0, 3: 28.0}


def fspl(f_mhz, d_km):
    return 32.44 + 20 * math.log10(f_mhz) + 20 * math.log10(d_km)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--channel", type=int, default=23)
    ap.add_argument("--power-code", type=int, default=0, help="E22 power code (0 = 37 dBm nominal)")
    ap.add_argument("--gt", type=float, default=2.15, help="TX antenna gain dBi")
    ap.add_argument("--gr", type=float, default=2.15, help="RX antenna gain dBi")
    ap.add_argument("--lt", type=float, default=0.5, help="TX feeder loss dB")
    ap.add_argument("--lr", type=float, default=1.0, help="RX feeder loss dB")
    ap.add_argument("--air-rate", type=int, default=2, help="E22 air-rate code (0 = 0.3 kbit/s)")
    ap.add_argument("--distance-km", type=float, default=10.0)
    ap.add_argument("--h1", type=float, default=2.0, help="TX antenna height above ground, m")
    ap.add_argument("--h2", type=float, default=10.0, help="RX antenna height above ground, m")
    ap.add_argument("--nf", type=float, default=6.0, help="receiver noise figure dB (Shannon line)")
    ap.add_argument("--fade-margin", type=float, default=10.0)
    a = ap.parse_args()

    f = 410.125 + a.channel
    p = PWR[a.power_code]
    eirp = p + a.gt - a.lt
    s = SENS_EST[a.air_rate]
    rb = RATE_BPS[a.air_rate]
    shannon = -174 + 10 * math.log10(rb) - 1.59 + a.nf
    L = fspl(f, a.distance_km)
    pr = eirp + a.gr - a.lr - L
    margin = pr - s
    allow = eirp + a.gr - a.lr - s - a.fade_margin
    d_max = 10 ** ((allow - 32.44 - 20 * math.log10(f)) / 20)
    horizon = 4.12 * (math.sqrt(a.h1) + math.sqrt(a.h2))
    r_fresnel = 17.32 * math.sqrt((a.distance_km / 2) ** 2 / (f / 1000 * a.distance_km))

    rows = [
        ("Frequency", f"{f:.3f} MHz (CH{a.channel}), lambda = {299.792458 / f:.3f} m"),
        ("TX power (nominal for code)", f"{p:.1f} dBm = {10 ** (p / 10) / 1000:.2f} W"),
        ("TX antenna gain / feeder loss", f"{a.gt:+.2f} dBi / {a.lt:.1f} dB"),
        ("EIRP", f"{eirp:.1f} dBm"),
        ("Distance", f"{a.distance_km:.2f} km  (OWLT {a.distance_km / 299792.458 * 1e6:.2f} us)"),
        ("Free-space path loss", f"{L:.1f} dB"),
        ("RX antenna gain / feeder loss", f"{a.gr:+.2f} dBi / {a.lr:.1f} dB"),
        ("Received power (free space)", f"{pr:.1f} dBm"),
        ("Air rate / sensitivity estimate", f"{rb} bit/s / {s:.0f} dBm"),
        ("Shannon-limit sensitivity at this rate", f"{shannon:.1f} dBm (NF {a.nf:.0f} dB): physically unbeatable"),
        ("Free-space margin", f"{margin:.1f} dB"),
        (f"Max free-space range with {a.fade_margin:.0f} dB fade margin", f"{d_max:,.0f} km"),
        ("Radio horizon for these heights", f"{horizon:.1f} km  <-- usually the REAL limit"),
        ("1st Fresnel radius at mid-path", f"{r_fresnel:.1f} m (keep >= 60 % = {0.6 * r_fresnel:.1f} m clear)"),
    ]
    w = max(len(r[0]) for r in rows)
    print(f"\n| {'Item'.ljust(w)} | Value |\n|{'-' * (w + 2)}|-------|")
    for k, v in rows:
        print(f"| {k.ljust(w)} | {v} |")
    if a.distance_km > horizon:
        print(f"\nWARNING: {a.distance_km} km is beyond the {horizon:.1f} km radio horizon: no line of sight; "
              "raise the antennas or use terrain (hill/mountain/balloon).")


if __name__ == "__main__":
    main()
