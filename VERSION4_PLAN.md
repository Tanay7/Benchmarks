# Version 4 plan (agreed with the owner 2026-10-17; not started)

Base: cut a new branch from the FINISHED UNO Q branch (claude/unoq-ground-station after Drop 1 + Drop 2),
or from version 3 if the owner defines a version 3 that lands first. The original branch
(claude/wonderful-hamilton-dzjfby) stays untouched. Hardware: E22-400T33D both ends (T37S optional).

Not in version 4 (already on the UNO Q branch, Drop 1): RSSI overload handling (wording: "too strong to
measure", damage limit +10 dBm; not "exceeds +10 dBm"), FER Clopper-Pearson interval (label one- vs
two-sided: 0/178 -> 1.7 % one-sided, 2.1 % two-sided).
Dropped while the E22 is used: Reed-Solomon decoder margin (E22 discards CRC-failed packets; corrections
always 0). Only for the SX126x option.

## Tier 1 - safety and long range
1. Command-loss restart ladder (spacecraft): rewrite with C2 (temporary, never C0) -> deep sleep/wake ->
   power cycle (optional high-side load switch on the E22 supply, config flag, off by default; T33D has no
   reset pin, T37S/TBH-02 has RESET pin 3). Count each step in HK. Revert to the default safe air rate.
2. Radio settings guard: boot writes C0 only if stored differs; in-flight changes C2. Periodic read-back
   only in a slot the ground schedule keeps quiet, every few minutes (config mode = deaf; AUX does not show
   an over-the-air packet). Compare with the expected CURRENT settings; "module fell back to saved base"
   = module reboot event (likely supply dip), distinct EVR.
3. Critical tables in memory (radio base settings, command-loss timer, FP mask, beacon policy): two copies
   + CRC each, checked 1 Hz; restore from the good copy, defaults only if both bad; EVR. Report build ID
   (stamped at compile time) + CRC of the stored image at boot and on request (sketch runs relocated in RAM
   via LLEXT, so a running-code checksum never matches the file).
4. Log E22 product info 80H-86H (7 raw bytes) at boot; detect module swap; include in provenance.
5. Link budget with tolerances (ground): every term best/worst with a cited source, guesses labelled;
   distribution shape shown (uniform/triangular/Gaussian) -> mean, sigma; mean, telemetry (mean-2sigma,
   downlink) and command (mean-3sigma, uplink) margins. New terms: polarization 20log10(cos theta)
   (3 dB at 45 deg, >=20 dB near 90 deg), mismatch from VSWR (2:1 0.51 dB, 3:1 1.25 dB), connectors/coax
   from datasheets. Predicted vs measured received power. Add a site/antenna planner: radio horizon
   4.12(sqrt h1 + sqrt h2) km, first Fresnel radius 17.32 sqrt(d1 d2/(f_GHz D)) m (10 km @ 433 MHz ~42 m
   mid-path; keep >=60 % clear).
6. Rate advice both directions: same air rate both ends, so uplink check mandatory, held to the command
   (3 sigma) rule; uplink data from HK has a staleness rule. Spacecraft auto-reverts a rate change if no
   valid command at the new rate within N minutes. Noise-floor sensitivity uses the required SNR measured
   by the sensitivity curve (item 8); otherwise labelled an estimate.
7. Command round-trip times (median, p95, p99) + COP-1 retransmission rate; recommend the FOP-1 timer
   from p95-p99.
8. Provenance stamp on every export: commit stamped at packaging (no git in the offline zip), config CRC,
   calibration offsets, method, station clock sync state, UTC, radio product info.

## Tier 2 - link science
9. Measured sensitivity curve: loss vs RSSI bins, logistic fit with CI, RSSI at 1 % and 10 % loss; lost
   frames take neighbours' RSSI (stated); survivorship bias noted; ~300 frames/bin for the 1 % point;
   bins with too few frames marked. Field data (bench needs >150 dB isolation).
10. Burst-loss model: run-length histogram + Gilbert-Elliott fit with counts; advice to space repeats.
11. Honest error bars: n_eff = n(1-rho)/(1+rho) (AR(1), stated); keep D-70 (no SEM on 1 dB-step RSSI).
12. Event markers: manual operator marker first; automatic change points (CUSUM/PELT) only as
    "possible step", minimum segment length.
13. RF survey waterfall from GSCAN/RFSCAN, between passes only; spacecraft scans rare.
14. Where the bits go: per packet type table + live fill fraction; E22 sub-packets (240/128/64/32) each
    with its own preamble; PHY overhead calibrated from measured transmit time.
15. Session health trend (daily for a permanent station): medians of both noise floors, RSSI both ways
    as "measured minus predicted", frame loss compared with intervals, TX busy time (computed, labelled);
    3 dB drift flag for dB quantities.

## Tier 3 - sensor science (cheap)
16. Magnetometer noise per axis: static, TX off, detrended; vs RM3100 datasheet for the cycle count.
17. WMM2025 check (offline coefficients, valid 2025-2030): total field + dip (angle to the BHI260 gravity
    vector at rest); declination only with an entered true-north heading; log TX on/off per reading
    (1 A at 1 cm ~20 uT vs Earth 25-65 uT); spacecraft site position from config.
18. Environment: dew point (Magnus) from BME688 T/RH (robust to self-heating), absolute humidity;
    condensation risk vs the coldest surface (needs an outside temperature, else labelled "board only");
    3-hour pressure tendency only when stationary (0.12 hPa per metre of height).

## Tier 4 - deferred
19. Fading statistics: histogram + Rician K with censoring at the sensitivity limit; no fade rate/duration
    unless the packet rate supports it.
20. Clock: Allan deviation only for multi-hour runs (1 ms tag jitter -> ~1.7e-6 at tau 1000 s); drift vs
    board temperature with a quadratic fit; needs a synced station clock.
