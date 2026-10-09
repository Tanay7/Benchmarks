"""Link-quality metrics computed by the ground station.

Measured directly
  * Packet RSSI (dBm)     — E22 appended RSSI octet, dBm = -(256 - value)
  * Noise floor (dBm)     — E22 ambient-noise register 0x00 (C0 C1 C2 C3 query)
  * Frame accounting      — MCFC continuity, RS corrections, FECF, ASM errors

Estimated (clearly labelled "est." on every display)
  * SNR_est   = RSSI - noise floor (dB). LoRa demodulates *below* the noise floor
                (required SNR is negative), so SNR_est near 0 dB is still a usable
                link; the RSSI register saturates near the floor in that regime.
  * Pr        = 10log10(10^(RSSI/10) - 10^(N/10)) (signal power with noise removed)
  * C/N0      = Pr - (N - 10log10(B))   dB-Hz, B = assumed receiver noise bandwidth
  * Eb/N0     = C/N0 - 10log10(Rb)      Rb = air data rate
  * Margin    = RSSI - S_est            S_est = sensitivity estimate for the air rate
These mirror the DSN's Pc/N0, Pd/N0 and Eb/N0 monitor quantities, but the E22
does not expose carrier/symbol-loop SNRs, so they are estimates, not measurements.
"""
from __future__ import annotations

import math

# E22-400T37S (EBYTE "E22-xxxT37S User Manual" v1.5):
#   * REG0 air-rate codes 0, 1 and 2 are ALL 2.4 kbit/s (7.2) - there is no 0.3k/1.2k.
#   * Receiving sensitivity -126 dBm typical (-125 min / -127 max) at 2.4 kbit/s (2.2).
# Faster rates are NOT specified by EBYTE: their values below are ESTIMATES scaled
# from the 2.4k figure at -10log10(rate ratio) dB. Measure yours (docs/08, T-RF-03).
AIR_RATE_BPS = {0: 2400, 1: 2400, 2: 2400, 3: 4800, 4: 9600, 5: 19200, 6: 38400, 7: 62500}
SENSITIVITY_EST_DBM = {c: round(-126.0 + 10 * math.log10(AIR_RATE_BPS[c] / 2400.0), 1) for c in AIR_RATE_BPS}
# T37S output power is 37 dBm for every power code ("This module has no power levels").
TX_POWER_DBM = {0: 37.0, 1: 37.0, 2: 37.0, 3: 37.0}
MIN_EFFECTIVE_RATE_CODE = 2          # 0 and 1 are aliases of 2 on the T37S


def dbm_to_mw(d):
    return 10 ** (d / 10.0)
