"""AS7265X ground spectral analytics (dssq/spectral.py; contract sections 8 and 13).

Pure-Python checks against hand calculations, published constants and synthetic spectra
(every synthetic spectrum here is generated, not measured): colour-matching-function
sanity values, the logarithmic spectral code (edge codes, round trip, resolution), the
SPEC v3.1 / SPECBURST codecs, CCT / Duv / McCamy on blackbody spectra, index uncertainty
propagation, PPFD of a flat spectrum, spectral angle and library match, the burst FFT and
transient detector, sky-map binning, temperature drift, the classifier, SpectralState
directives / persistence / hostile input / JSON safety, and the flight cross-check: the
binary32 log encoder/decoder, the packed SPEC v3.1 field set (every field) and a SPECBURST
packet from tools/host_test's spectral_vectors.txt (skipped, with the reason, when the host
test cannot be built).
"""
import cmath
import json
import math
import random
import shutil
import statistics
import struct
import subprocess
from pathlib import Path

import pytest

from dssq import spectral as S
from dssq.ccsds import rice

ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / "tools" / "host_test"
NM = S.WAVELENGTHS_NM


def gauss(nm: float, mu: float, sigma: float) -> float:
    return math.exp(-0.5 * ((nm - mu) / sigma) ** 2)


def spec_packet(spec, unc=None, *, mode=0, flags=S.F_VALID, gain=2, cycles=59, sat=0,
                zen=None, az=None, temps=(25, 25, 25), cls=0, conf=0, raw=None) -> dict:
    """Decoded SPEC v3.1 values as the dictionary-driven decommutator produces them."""
    unc = unc or [None] * 18
    raw = raw or [1000] * 18
    v = {"spec_mode": mode, "flags": flags, "gain_code": gain, "int_cycles": cycles, "led_cur": 0,
         "n_frames": 1, "sat_mask": sat, "bore_zen": zen, "bore_az": az, "spec_class": cls,
         "spec_conf": conf, "spec_change": None}
    for i, nm in enumerate(NM):
        v[f"spec_{nm}nm"], v[f"unc_{nm}nm"], v[f"raw_{nm}nm"] = spec[i], unc[i], raw[i]
    for d in range(3):
        v[f"temp_{d}"] = temps[d]
    return v


def json_safe(obj) -> None:
    json.dumps(obj, allow_nan=False)


# --- colour-matching functions (contract section 8 sanity values) ----------------------

def test_cmf_sanity_values():
    assert S.cmf_ybar(555) == pytest.approx(1.0, abs=0.01)
    assert S.cmf_xbar(600) == pytest.approx(1.06, abs=0.01)
    assert S.cmf_zbar(445) == pytest.approx(1.78, abs=0.01)
    assert S.cmf_xbar(1000) < 1e-6 and S.cmf_ybar(300) < 1e-3


# --- logarithmic spectral code (contract 13.3) ------------------------------------------

def test_log_decode_edge_codes():
    assert S.log_decode(0x0000) == 0.0
    assert S.log_decode(0x8000) == 0.0                     # negative below 1e-4: zero, as flight
    assert math.isnan(S.log_decode(0xFFFF))
    assert S.log_decode(0x0001) == pytest.approx(1e-4, rel=1e-15)
    assert S.log_decode(0x8001) == pytest.approx(-1e-4, rel=1e-15)
    assert S.log_decode(0x0401) == pytest.approx(2e-4, rel=1e-15)      # one octave = 1024 codes
    top = S.log_decode(S.LOG_M_MAX)
    assert top == pytest.approx(1e-4 * 2 ** (32765 / 1024), rel=1e-15)
    assert 4.28e5 < top < 4.30e5                             # contract: range 1e-4 .. 4.29e5
    for bad in (-1, 0x10000, 1.5, True, None, "1"):
        with pytest.raises(ValueError):
            S.log_decode(bad)


def test_log_encode_edges():
    assert S.log_encode(None) == S.log_encode(math.nan) == S.log_encode("x") == 0xFFFF
    assert S.log_encode(0.0) == S.log_encode(-0.0) == S.log_encode(5e-5) == 0
    assert S.log_encode(-5e-5) == 0x8000
    assert S.log_encode(1e-4) == 1 and S.log_encode(-1e-4) == 0x8001
    assert S.log_encode(math.inf) == S.log_encode(1e12) == S.log_encode(10 ** 400) == S.LOG_M_MAX
    assert S.log_encode(-math.inf) == 0x8000 | S.LOG_M_MAX
    assert S.log_encode(2e-4) == 0x0401


def test_log_round_trip_every_code_and_resolution():
    for m in range(1, S.LOG_M_MAX + 1):
        for s in (0, 0x8000):
            assert S.log_encode(S.log_decode(s | m)) == s | m
    step = S.log_decode(1001) / S.log_decode(1000) - 1.0
    assert step == pytest.approx(0.000677, abs=1e-6)        # contract: step 0.068 %
    rng = random.Random(13)
    worst = 0.0
    for _ in range(20000):
        v = 10 ** rng.uniform(-4, 5.6)
        worst = max(worst, abs(S.log_decode(S.log_encode(v)) - v) / v)
    assert worst <= 2 ** (0.51 / 1024) - 1                  # half a step (+ binary32 slack)


# --- packet codecs (contract 13.4 / 13.5) -----------------------------------------------

def test_spec_v31_layout_and_round_trip():
    assert struct.calcsize(">" + "".join(f[1] for f in S.SPEC_V31_FIELDS)) == 129
    names = [f[0] for f in S.SPEC_V31_FIELDS]
    assert names[:7] == ["spec_mode", "flags", "gain_code", "int_cycles", "led_cur", "n_frames",
                         "sat_mask"]
    assert names[-5:] == ["bore_zen", "bore_az", "spec_class", "spec_conf", "spec_change"]
    vals = spec_packet([1.5 * (i + 1) for i in range(18)], [0.01] * 18, mode=2,
                       flags=S.F_VALID | S.F_WHITE | S.F_ACTIVE | S.F_ATTITUDE, sat=0x20001,
                       zen=33.25, az=270.5, cls=7, conf=100, raw=[None] + [123] * 17)
    vals["spec_change"] = 4.56
    data = S.pack_spec_v31(vals)
    assert len(data) == S.SPEC_V31_LEN
    # bore_az travels wrapped to (-180, 180]: 270.5 deg -> -89.5 deg -> -8950 cdeg
    off = 10 + 3 * 36 + 3 + 2
    assert struct.unpack_from(">h", data, off)[0] == -8950
    dec = S.decode_spec_v31(data)
    assert dec["bore_az"] == pytest.approx(270.5) and dec["bore_zen"] == pytest.approx(33.25)
    assert dec["raw_410nm"] is None and dec["raw_940nm"] == 123
    assert dec["spec_change"] == pytest.approx(4.56)
    for i, nm in enumerate(NM):
        assert dec[f"spec_{nm}nm"] == pytest.approx(1.5 * (i + 1), rel=3.4e-4)
    assert S.pack_spec_v31(dec) == data                       # bit-exact re-encode
    empty = S.decode_spec_v31(S.pack_spec_v31({}))           # every field unavailable
    assert empty["bore_zen"] is None and empty["spec_410nm"] is None and empty["temp_0"] is None
    with pytest.raises(ValueError):
        S.decode_spec_v31(data[:128])


def test_specburst_raw_layout_by_hand():
    """Contract 28 / PB-1.12: header 10 octets incl. encoding, dt_ms[n], then n x 18 u16
    TIME-MAJOR; raw codes 0xFFFF = not converted, a count of 65535 travels as 0xFFFE."""
    samples = [(0, list(range(18))), (25, [None] * 4 + [7] * 14), (50, [65535] * 18)]
    data = S.pack_specburst(123456, 401, 1, 1, samples, encoding=0)
    assert len(data) == 10 + 3 * 38 == S.BURST_MAX_LEN == 124
    assert data[:10] == struct.pack(">IHBBBB", 123456, 401, 1, 1, 3, 0)
    assert struct.unpack_from(">3H", data, 10) == (0, 25, 50)
    assert struct.unpack_from(">18H", data, 16) == tuple(range(18))            # sample 0 first
    assert struct.unpack_from(">5H", data, 16 + 36) == (0xFFFF,) * 4 + (7,)    # then sample 1
    assert struct.unpack_from(">H", data, 16 + 72)[0] == 0xFFFE                 # 65535 -> 0xFFFE
    d = S.decode_specburst(data)
    assert (d["t0_ms"], d["rate_dhz"], d["rate_hz"], d["gain_code"], d["int_cycles"], d["n"]) == \
           (123456, 401, 40.1, 1, 1, 3)
    assert (d["encoding"], d["encoding_name"], d["payload_len"], d["compression_ratio"]) == (0, "raw", 108, 1.0)
    assert d["samples"][1]["dt_ms"] == 25 and d["samples"][1]["raw"][:5] == [None] * 4 + [7]
    # N = 1: full scale 2047, so >= 2046 is saturated; 0xFFFE always is (AS-DP-07).
    assert d["samples"][2]["raw"][0] == 0xFFFE and all(d["samples"][2]["saturated"])
    assert not any(d["samples"][0]["saturated"]) and d["samples"][1]["saturated"][:5] == [False] * 5
    hi = S.decode_specburst(S.pack_specburst(0, 0, 0, 63, [(0, [65533, 65534] + [1] * 16)], encoding=0))
    assert hi["samples"][0]["saturated"][:3] == [False, True, False]            # fs 65535 at N = 63


def test_specburst_rice_layout_and_choice():
    steady = [(30 * k, [1000 + (ch % 3) + k for ch in range(18)]) for k in range(3)]
    auto = S.pack_specburst(7, 333, 2, 1, steady)
    assert auto[9] == S.BURST_RICE and len(auto) < S.pack_specburst(7, 333, 2, 1, steady, encoding=0).__len__()
    assert auto == S.pack_specburst(7, 333, 2, 1, steady, encoding=1)
    payload = auto[16:]
    cm = [steady[t][1][ch] for ch in range(18) for t in range(3)]              # channel-major
    assert payload == rice.encode(cm) and 1 <= len(payload) <= 36 * 3 - 1
    d = S.decode_specburst(auto)
    assert [s["raw"] for s in d["samples"]] == [list(r) for _dt, r in steady]
    assert d["compression_ratio"] == pytest.approx(108 / len(payload)) and d["encoding_name"] == "CCSDS 121.0-B-3"
    noisy = [(0, [random.Random(k).randint(0, 65535) for k in range(18)])]
    assert S.pack_specburst(0, 0, 0, 1, noisy)[9] == S.BURST_RAW                # Rice not shorter
    with pytest.raises(ValueError, match="not shorter"):
        S.pack_specburst(0, 0, 0, 1, noisy, encoding=1)


def test_specburst_rejects_malformed_packets():
    good = S.pack_specburst(1, 2, 0, 1, [(0, [5] * 18)], encoding=0)
    rice_pkt = S.pack_specburst(1, 2, 0, 1, [(0, [5] * 18)], encoding=1)
    hdr = bytearray(good[:10])
    bad = [good[:9],                                      # short header
           good[:-1], good + b"\x00",                    # raw payload length
           bytes(hdr[:8]) + b"\x00\x00" + good[10:],      # n = 0
           bytes(hdr[:8]) + b"\x04\x00" + bytes(8 + 144),  # n = 4
           bytes(hdr[:9]) + b"\x02" + good[10:],           # unknown encoding
           good[:10] + good[10:12],                       # no payload
           rice_pkt[:12] + bytes(36),                     # "Rice" as long as raw
           bytes(125)]                                    # longer than any SPECBURST
    for b in bad:
        with pytest.raises(ValueError):
            S.decode_specburst(b)
    with pytest.raises(rice.RiceError):                   # undecodable CCSDS 121.0 stream
        S.decode_specburst(rice_pkt[:12] + b"\x00")
    samples = [(0, [1] * 18)]
    for kw in ({"encoding": 2}, {"encoding": True}):
        with pytest.raises(ValueError):
            S.pack_specburst(0, 0, 0, 1, samples, **kw)
    for args in ((-1, 0, 0, 1), (1 << 32, 0, 0, 1), (0, 1 << 16, 0, 1), (0, 0, 256, 1), (0, 0, 0, -1)):
        with pytest.raises(ValueError):
            S.pack_specburst(*args, samples)
    for smp in ([], samples * 4, [(70000, [1] * 18)], [(0, [1] * 17)], [(0, [-1] + [1] * 17)],
                [(0, [1.5] + [1] * 17)], [(0, [True] + [1] * 17)]):
        with pytest.raises(ValueError):
            S.pack_specburst(0, 0, 0, 1, smp)


def test_led_current_limits():
    """Contract 26: white code <= 3, IR <= 2, UV = 0 - exactly the integers 0..11."""
    assert S.LED_MAX_CODE == (3, 2, 0)
    assert list(S.LED_CUR_ALLOWED) == list(range(12))
    assert all(S.led_cur_allowed(v) for v in range(12))
    assert not any(S.led_cur_allowed(v) for v in (12, 15, 16, 63, 64, -1, 1.0, True, None))


def test_hdr_packet_semantics():
    """AS-DP-04: in HDR the gain / integration are the bracket centre, so raw_fraction is
    meaningless (None) and the exposure carries the note."""
    hdr = S.analyse(spec_packet([1.0] * 18, [0.1] * 18, mode=S.MODE_HDR, cycles=20))
    assert hdr["raw_fraction"] == [None] * 18 and hdr["exposure"]["note"] == S.HDR_EXPOSURE_NOTE
    survey = S.analyse(spec_packet([1.0] * 18, [0.1] * 18, cycles=20))
    assert survey["exposure"]["note"] is None
    assert survey["raw_fraction"][0] == pytest.approx(1000 / S.full_scale(20))


def test_burst_compression_statistics():
    st = S.SpectralState()
    steady = [(30 * k, [800 + k] * 18) for k in range(3)]
    for t0, enc in ((100, 1), (100, 0), (100, 1)):
        st.on_specburst(S.decode_specburst(S.pack_specburst(t0, 333, 0, 1, steady, encoding=enc)), 1.0)
    c = st.snapshot(now=1.5)["spectral_burst"]["compression"]
    assert (c["packets_raw"], c["packets_rice"]) == (1, 2)
    assert c["octets_raw"] == 3 * 108 and c["ratio_mean"] == pytest.approx(c["octets_raw"] / c["octets_sent"])
    assert c["last_ratio"] > 1.0
    json_safe(st.snapshot(now=2.0))


# --- colorimetry (contract 13.7) --------------------------------------------------------

@pytest.mark.parametrize("temp", [2856.0, 4000.0, 6500.0])
def test_cct_duv_of_blackbody_through_the_bands(temp):
    unc = [None] * 18
    # Through the module's 20 nm Gaussian band model (the locus uses the same model) ...
    c = S.colorimetry(S.band_values(lambda nm: S.planck_rel(nm, temp)), unc)
    assert c["available"] and c["cct_valid"]
    assert abs(c["cct_k"] - temp) / temp < 0.02 and abs(c["duv"]) < 0.002
    assert abs(c["cct_k"] - temp) < 0.5                     # in fact self-consistent to < 0.5 K
    # ... and through an independent band model (point samples at the centres x 20 nm).
    c2 = S.colorimetry([20.0 * S.planck_rel(nm, temp) for nm in NM], unc)
    assert abs(c2["cct_k"] - temp) / temp < 0.02 and abs(c2["duv"]) < 0.002
    # McCamy (1992) on the band-sampled xy: stated tolerance 3 %. Twelve 20 nm bands bias xy
    # slightly (measured: +1.8 % at 2856 K, +0.8 % at 4000 K, -0.3 % at 6500 K).
    assert c["mccamy_in_range"] and abs(c["cct_mccamy_diff_pct"]) < 3.0


def test_illuminant_a_chromaticity_close_to_cie():
    # CIE standard illuminant A (2856 K blackbody): x = 0.44757, y = 0.40745 (CIE 015).
    c = S.colorimetry(S.band_values(lambda nm: S.planck_rel(nm, 2856.0)), [None] * 18)
    assert c["x"] == pytest.approx(0.44757, abs=0.005)
    assert c["y"] == pytest.approx(0.40745, abs=0.005)


def test_duv_sign_and_off_locus():
    base = S.band_values(lambda nm: S.planck_rel(nm, 4000.0))
    greener = [v * (1.0 + 0.3 * gauss(nm, 540, 30)) for v, nm in zip(base, NM, strict=True)]
    pinker = [v * (1.0 - 0.3 * gauss(nm, 540, 30)) for v, nm in zip(base, NM, strict=True)]
    assert S.colorimetry(greener, [None] * 18)["duv"] > 0.002
    assert S.colorimetry(pinker, [None] * 18)["duv"] < -0.002
    mono = S.colorimetry([10.0 if nm == 510 else 0.0 for nm in NM], [None] * 18)
    assert not mono["cct_valid"]                             # a spectral colour has no CCT


# --- indices with uncertainty, radiometry -----------------------------------------------

def test_ndvi_and_uncertainty_propagation():
    r = [0.1] * 18
    s = [0.005] * 18
    r[NM.index(680)], r[NM.index(860)] = 0.05, 0.5
    out = S.vegetation_indices(r, s, "reflectance", [False] * 18)
    a, b = 0.5, 0.05
    assert out["ndvi"]["value"] == pytest.approx((a - b) / (a + b), rel=1e-12)
    hand = 2.0 * math.sqrt((b * 0.005) ** 2 + (a * 0.005) ** 2) / (a + b) ** 2
    assert out["ndvi"]["sigma"] == pytest.approx(hand, rel=1e-12)
    rng = random.Random(7)                                   # Monte Carlo check of first order
    draws = []
    for _ in range(20000):
        x, y = a + rng.gauss(0, 0.005), b + rng.gauss(0, 0.005)
        draws.append((x - y) / (x + y))
    assert statistics.stdev(draws) == pytest.approx(hand, rel=0.05)
    assert out["wbi_proxy"]["value"] == pytest.approx(1.0) and "970" in out["wbi_proxy"]["note"]
    assert "531" in out["pri_proxy"]["note"] and out["basis"] == "reflectance"


def test_indices_on_reflectance_vs_spectrum_label():
    lamp = [100.0] * 18
    leaf = [100.0 * (0.05 if nm < 700 else 0.5) for nm in NM]
    ref = S.WhiteReference(1.0, 2, 1, True, 2, 59, lamp, [1.0] * 18)
    a = S.analyse(spec_packet(leaf, [1.0] * 18, mode=2, flags=S.F_VALID | S.F_WHITE | S.F_ACTIVE),
                  white_ref=ref)
    assert a["indices"]["basis"] == "reflectance" and a["reflectance"]["compatible"]
    assert a["indices"]["ndvi"]["value"] == pytest.approx(0.45 / 0.55)
    ambient = S.analyse(spec_packet(leaf, [1.0] * 18), white_ref=ref)   # passive vs active ref
    assert ambient["indices"]["basis"] == "spectrum"
    assert not ambient["reflectance"]["compatible"]
    assert "not surface reflectance" in ambient["indices"]["label"]


def test_ppfd_and_irradiance_of_a_flat_spectrum():
    v = 100.0                                                # uW/cm2 in every channel
    e = v * 0.01 / 20.0                                      # W m-2 nm-1 (stated assumption)
    hcna = 6.62607015e-34 * 299792458.0 * 6.02214076e23     # J m mol-1
    ppfd_hand = e * (700.0 ** 2 - 400.0 ** 2) / 2.0 * 1e-9 / hcna * 1e6
    rad = S.radiometry([v] * 18, [1.0] * 18)
    assert rad["ppfd"]["value"] == pytest.approx(ppfd_hand, rel=1e-12)
    assert rad["ppfd"]["value"] == pytest.approx(68.96, abs=0.01)
    assert rad["irradiance_vis"]["value"] == pytest.approx(e * 300.0, rel=1e-12)
    assert rad["irradiance_nir"]["value"] == pytest.approx(e * 250.0, rel=1e-12)
    assert rad["irradiance_total"]["value"] == pytest.approx(e * 550.0, rel=1e-12)
    assert rad["irradiance_total"]["sigma"] > 0 and rad["label"] == S.APPROX_LABEL
    fine = math.fsum(S.cmf_ybar(400.0 + 0.1 * k) for k in range(3151)) * 0.1  # 400..715 nm
    assert rad["illuminance"]["value"] == pytest.approx(683.0 * e * fine, rel=0.02)
    assert S.radiometry([None] + [v] * 17, [1.0] * 18)["ppfd"]["value"] is None


# --- spectral angle and library ---------------------------------------------------------

def test_spectral_angle():
    a = [1.0] * 18
    assert S.spectral_angle_deg(a, [3.0] * 18) == pytest.approx(0.0, abs=1e-12)
    x = [1.0] * 6 + [0.0] * 12
    y = [0.0] * 6 + [1.0] * 6 + [0.0] * 6
    assert S.spectral_angle_deg(x, y) == pytest.approx(90.0)
    z = [1.0] * 6 + [1.0] * 6 + [0.0] * 6
    assert S.spectral_angle_deg(x, z) == pytest.approx(45.0)
    assert S.spectral_angle_deg([None] * 13 + [1.0] * 5, a) is None     # < 6 common channels
    assert S.spectral_angle_deg([0.0] * 18, a) is None


def test_library_match_and_persistence(tmp_path):
    st = S.SpectralState(tmp_path)
    assert st.directive(["SPECLIB", "SAVE", "leaf"])["status"] == "REJECTED"   # nothing yet
    leaf = [10.0 * (0.05 if nm < 700 else 0.5) + 0.1 for nm in NM]
    soil = [0.1 * nm - 30.0 for nm in NM]
    st.on_spec(spec_packet(leaf, [0.01] * 18), 100.0)
    assert st.directive(["SPECLIB", "SAVE", "leaf"])["status"] == "OK"
    st.on_spec(spec_packet(soil, [0.01] * 18), 101.0)
    assert st.directive(["SPECLIB", "SAVE", "soil"])["status"] == "OK"
    assert st.directive(["SPECLIB", "SAVE", "../evil"])["status"] == "REJECTED"
    assert st.directive(["SPECLIB", "LIST"])["entries"] == ["leaf", "soil"]
    a = st.on_spec(spec_packet([2.0 * v * (1.0 + 0.01 * math.sin(i)) for i, v in enumerate(leaf)],
                               [0.01] * 18), 102.0)
    best = a["library_match"]["best"]
    assert best["name"] == "leaf" and best["angle_deg"] < 1.0
    assert a["library_match"]["ranked"][1]["name"] == "soil"
    st2 = S.SpectralState(tmp_path)                          # reload from disk
    assert sorted(st2.library.entries) == ["leaf", "soil"]
    assert st2.directive(["SPECLIB", "DEL", "soil"])["status"] == "OK"
    assert st2.directive(["SPECLIB", "DEL", "soil"])["status"] == "REJECTED"
    assert sorted(S.SpectralState(tmp_path).library.entries) == ["leaf"]
    (tmp_path / "spec_library.json").write_text("{not json")
    st3 = S.SpectralState(tmp_path)
    assert not st3.library.entries and any("ignored" in e["text"] for e in st3.drain_events())


# --- BURST ------------------------------------------------------------------------------

def test_fft_matches_naive_dft():
    rng = random.Random(3)
    x = [complex(rng.uniform(-1, 1), rng.uniform(-1, 1)) for _ in range(16)]
    naive = [sum(x[n] * cmath.exp(-2j * math.pi * k * n / 16) for n in range(16))
             for k in range(16)]
    for p, q in zip(S.fft(x), naive, strict=True):
        assert abs(p - q) < 1e-9
    with pytest.raises(ValueError):
        S.fft([0.0] * 12)


def test_fft_of_sinusoid_burst_finds_the_bin():
    fs, f0, amp, n = 50.0, 7.3, 100.0, 1000
    y = [500.0 + amp * math.sin(2 * math.pi * f0 * k / fs) for k in range(n)]
    sp = S.amplitude_spectrum(y, fs)
    assert sp["n_fft"] == 1024 and sp["nyquist_hz"] == 25.0
    assert len(sp["magnitude"]) == 513
    k = max(range(1, len(sp["magnitude"])), key=sp["magnitude"].__getitem__)
    assert abs(k - f0 / sp["df_hz"]) <= 1.0                  # the right bin
    assert abs(sp["peak_hz"] - f0) < sp["df_hz"] / 2
    assert 0.8 * amp < sp["peak_amplitude"] <= 1.05 * amp


def test_burst_analysis_jittered_samples_and_transient():
    rng = random.Random(5)
    fs, f0 = 40.0, 3.1
    samples = []
    for k in range(800):
        t_ms = round(k * 1000.0 / fs + rng.uniform(-2.0, 2.0))
        level = 1000.0 + 80.0 * math.sin(2 * math.pi * f0 * k / fs) + rng.gauss(0, 3)
        if k == 500:
            level += 2000.0                                   # injected flash
        raw = [int(level)] * 18
        raw[0] = raw[1] = None                                # BANK mode without those slots
        samples.append((t_ms, raw))
    samples.append(samples[10])                               # playback duplicate
    a = S.analyse_burst(samples, int_cycles=3, rate_hz_reported=40.0)    # full scale 4095
    assert a["n_samples"] == 800 and a["rate_hz_measured"] == pytest.approx(fs, rel=0.01)
    assert a["broadband"]["channels_nm"] == list(NM[2:])
    assert abs(a["fft"]["peak_hz"] - f0) < 2 * a["fft"]["df_hz"]
    assert abs(a["channels"][5]["peak_hz"] - f0) < 0.2
    assert not a["channels"][0]["present"] and a["series"]["raw"][0] is None
    assert len(a["transients"]) == 1
    assert a["transients"][0]["t_ms"] == pytest.approx(500 * 25, abs=3)
    assert a["transients"][0]["sign"] == 1
    assert a["channels"][5]["saturated_samples"] == 0
    assert len(a["series"]["t_s"]) <= S.BURST_PLOT_POINTS
    json_safe(a)


def test_state_burst_packets_and_close():
    st = S.SpectralState()
    for p in range(4):
        smp = [{"dt_ms": 30 * (3 * p + k), "raw": [100 + k] * 18} for k in range(3)]
        st.on_specburst({"t0_ms": 9000, "rate_dhz": 333, "rate_hz": 33.3, "gain_code": 0,
                         "int_cycles": 1, "n": 3, "samples": smp}, 10.0 + p)
    snap = st.snapshot(now=12.0)["spectral_burst"]
    assert snap["active"] and snap["n_samples"] == 12 and snap["t0_ms"] == 9000
    st.on_specburst({"t0_ms": 20000, "gain_code": 0, "int_cycles": 1,
                     "samples": [{"dt_ms": 0, "raw": [5] * 18}]}, 20.0)
    snap = st.snapshot(now=100.0)["spectral_burst"]               # idle > 30 s: closed
    assert not snap["active"] and [b["t0_ms"] for b in snap["previous"]] == [9000, 20000]
    texts = [e["text"] for e in st.drain_events()]
    assert any("complete: 12 samples" in t for t in texts)
    st.on_specburst({"t0_ms": None}, 30.0)
    assert any("invalid header" in e["text"] for e in st.drain_events())


# --- sky map, temperature drift ---------------------------------------------------------

def test_skymap_binning():
    m = S.SkyMap()
    assert m.add(5.0, 100.0, 1000.0, 20.0) and m.add(14.9, 119.0, 3000.0, 60.0)
    assert m.add(0.0, None, 50.0, None)                       # boresight on the vertical
    assert m.add(180.0, 359.9, 7.0, 0.1)
    assert not m.add(181.0, 0.0, 1.0, 1.0) and not m.add(10.0, 0.0, None, 1.0)
    bins = {(b["zen_lo"], b["az_lo"]): b for b in m.snapshot()["bins"]}
    b = bins[(0.0, 90.0)]
    assert b["n"] == 2 and b["lux_mean"] == pytest.approx(2000.0)
    assert b["ppfd_mean"] == pytest.approx(40.0)
    assert b["lux_std"] == pytest.approx(1000.0)
    assert bins[(0.0, 0.0)]["ppfd_mean"] is None
    assert bins[(165.0, 330.0)]["n"] == 1
    assert m.snapshot()["n_total"] == 4


def test_state_skymap_only_for_passive_spectra_with_attitude():
    st = S.SpectralState()
    sun = S.band_values(lambda nm: 0.05 * S.planck_rel(nm, 5800.0))
    st.on_spec(spec_packet(sun, [0.01] * 18, flags=S.F_VALID | S.F_ATTITUDE, zen=20.0, az=45.0),
               1.0)
    st.on_spec(spec_packet(sun, [0.01] * 18, flags=S.F_VALID, zen=20.0, az=45.0), 2.0)
    st.on_spec(spec_packet(sun, [0.01] * 18, mode=2, flags=S.F_VALID | S.F_ATTITUDE | S.F_ACTIVE,
                           zen=20.0, az=45.0), 3.0)
    sm = st.snapshot()["spectral_skymap"]
    assert sm["n_total"] == 1 and sm["bins"][0]["zen_lo"] == 15.0 and sm["bins"][0]["az_lo"] == 30.0


def test_temperature_drift_slope():
    d = S.TemperatureDrift()
    for k in range(30):
        temp = 20 + k % 10
        spec = [None] * 18
        for i in S.DEVICE_CHANNELS[1]:
            spec[i] = 100.0 - 0.5 * (temp - 20)               # -3 uW/cm2 per degC over 6 channels
        d.add([None, temp, 25], spec, [False] * 18)
    snap = d.snapshot()
    assert snap[0]["n"] == 0 and snap[0]["slope_uw_per_c"] is None
    assert snap[1]["slope_uw_per_c"] == pytest.approx(-3.0) and snap[1]["r"] == pytest.approx(-1.0)
    assert snap[2]["n"] == 0                                    # its channels were missing


# --- classification ---------------------------------------------------------------------

def test_classifier_on_synthetic_sources_and_onboard_comparison():
    def unc(v):
        return [max(0.01, 0.01 * x) for x in v]

    cases = {
        S.CLASS_INCANDESCENT: S.band_values(lambda nm: 500.0 * S.planck_rel(nm, 2856.0)),
        S.CLASS_DAYLIGHT: S.band_values(lambda nm: 300.0 * S.planck_rel(nm, 6500.0)),
        S.CLASS_WHITE_LED: S.band_values(
            lambda nm: 10 * gauss(nm, 450, 10) + 6 * gauss(nm, 560, 45 if nm < 560 else 55)),
        S.CLASS_FLUORESCENT: S.band_values(
            lambda nm: 6 * gauss(nm, 436, 5) + 10 * gauss(nm, 546, 5) + 9 * gauss(nm, 611, 5)
            + 0.2),
        S.CLASS_NARROWBAND: S.band_values(lambda nm: 10 * gauss(nm, 630, 8)),
    }
    for want, spec in cases.items():
        a = S.analyse(spec_packet(spec, unc(spec), cls=want, conf=80))
        c = a["classification"]
        assert c["class_id"] == want, (want, c["reasons"])
        assert c["agrees"] and c["reasons"] and 0 < c["confidence"] <= 100
    dark = S.analyse(spec_packet([0.01] * 18, [0.01] * 18, cls=S.CLASS_DAYLIGHT))
    assert dark["classification"]["class_id"] == S.CLASS_DARK
    assert dark["classification"]["agrees"] is False
    own = S.analyse(spec_packet([5.0] * 18, [0.1] * 18, mode=2,
                                flags=S.F_VALID | S.F_ACTIVE | S.F_UV))
    assert own["classification"]["class_id"] == S.CLASS_OWN_LAMP
    inc = S.analyse(spec_packet(cases[S.CLASS_INCANDESCENT], unc(cases[S.CLASS_INCANDESCENT])))
    assert any("supports" in r for r in inc["classification"]["consistency"])


def test_thresholds_from_config():
    t = S.ClassThresholds.from_mapping({"dark_total_uw": 2.0})
    assert t.dark_total_uw == 2.0 and t.dark_snr == 5.0
    for bad in ({"dark_totl_uw": 1.0}, {"dark_snr": -1.0}, {"dark_snr": "x"}):
        with pytest.raises(ValueError):
            S.ClassThresholds.from_mapping(bad)


# --- fluorescence -----------------------------------------------------------------------

def test_fluorescence_metrics_mask_excitation_band():
    spec = [500.0, 400.0, 300.0, 2.0, 3.0, 1.0, 1.0, 1.0, 1.0, 2.0, 8.0, 6.0, 4.0, 1.0, 0.5, 0.2,
            0.1, 0.1]
    a = S.analyse(spec_packet(spec, [0.1] * 18, mode=3, flags=S.F_VALID | S.F_UV | S.F_ACTIVE))
    f = a["fluorescence"]
    assert f["masked"][:3] == [True] * 3 and not any(f["masked"][3:])
    assert f["f680_f730"]["value"] == pytest.approx(2.0) and f["f680_f730"]["sigma"] > 0
    assert f["blue_green_index"]["value"] == pytest.approx(5.0 / 12.0)
    assert f["emission_peak_nm"] == 680                     # 410 nm (excitation) is ignored
    assert a["indices"] is None


# --- SpectralState: directives, persistence, dedupe, JSON safety ------------------------

def test_specref_capture_clear_and_persistence(tmp_path):
    st = S.SpectralState(tmp_path)
    assert st.directive(["SPECREF"])["status"] == "REJECTED"
    white = [200.0] * 18
    st.on_spec(spec_packet(white, [1.0] * 18, mode=2, flags=S.F_VALID | S.F_WHITE | S.F_ACTIVE,
                           sat=1 << 17), 10.0)
    r = st.directive(["specref"])
    assert r["status"] == "OK" and "17 channels" in r["message"]
    assert (tmp_path / "spec_white_ref.json").exists()
    assert st.white_ref.spectrum[17] is None                 # saturated channel excluded
    a = st.on_spec(spec_packet([100.0] * 18, [1.0] * 18, mode=2,
                               flags=S.F_VALID | S.F_WHITE | S.F_ACTIVE), 11.0)
    assert a["reflectance"]["values"][0] == pytest.approx(0.5)
    assert a["reflectance"]["values"][17] is None
    assert a["white_ref_angle_deg"] == pytest.approx(0.0, abs=1e-6)
    assert S.SpectralState(tmp_path).white_ref.spectrum[0] == 200.0      # reloaded
    assert st.directive(["SPECREF", "CLEAR"])["status"] == "OK"
    assert not (tmp_path / "spec_white_ref.json").exists() and st.white_ref is None
    assert st.snapshot()["spectral"]["reflectance"] is None             # latest re-analysed
    assert st.directive(["SPECREF", "NOW"])["status"] == "REJECTED"
    assert st.directive(["SPECLIB"])["status"] == "REJECTED"
    assert st.directive([])["status"] == "REJECTED"
    levels = [e["level"] for e in st.drain_events()]
    assert levels.count("INFO") >= 2 and st.drain_events() == []


def test_history_dedupe_and_snapshot_layout():
    st = S.SpectralState(history_len=5)
    spec = S.band_values(lambda nm: 0.05 * S.planck_rel(nm, 5800.0))
    for k in range(8):
        st.on_spec(spec_packet(spec, [0.01] * 18), 100.0 + k, sclk=1000.0 + k)
    assert st.on_spec(spec_packet(spec, [0.01] * 18), 99.0, sclk=1003.0) is None   # playback copy
    assert st.on_spec(spec_packet(spec, [0.01] * 18), 99.5, sclk=1003.0, partition=2) is not None
    st.on_spec(spec_packet([None] * 18, mode=4), 200.0)     # BURST-mode SPEC: no history row
    snap = st.snapshot()
    assert set(snap) == {"spectral", "spectral_history", "spectral_burst", "spectral_skymap",
                         "spectral_library"}
    h = snap["spectral_history"]
    assert h["max_rows"] == 5 and h["t"] == [99.5, 104.0, 105.0, 106.0, 107.0]  # time order
    assert len(h["values"][0]) == 18 and h["lux"][0] > 0
    assert snap["spectral"]["mode_name"] == "BURST" and snap["spectral"]["radiometry"] is None
    assert snap["spectral"]["counters"]["duplicates"] == 1
    st.on_evr(0x0701, "INFO", "spectral event 6.2 deg", 300.0)
    st.on_evr(0x0123, "INFO", "unrelated", 300.0)
    log = st.snapshot()["spectral"]["events"]
    assert [e["event_id"] for e in log if e["source"] == "S/C"] == [0x0701]
    json_safe(st.snapshot())


def test_analyse_is_robust_to_hostile_values():
    rng = random.Random(11)
    junk = [None, math.nan, math.inf, -math.inf, -1e300, 1e300, "x", True, [], 7, -3, 0, 2 ** 70]
    for _ in range(300):
        v = {f[0]: rng.choice(junk) for f in S.SPEC_V31_FIELDS}
        v["flags"] = rng.choice([S.F_VALID, S.F_VALID | S.F_ACTIVE, 0, 255, None, 1e9])
        a = S.analyse(v, t=rng.choice([None, 1.0, math.nan]))
        json_safe(a)
    a = S.analyse({"spec_mode": 9, "gain_code": 7, "bore_zen": 400.0})
    assert any("spec_mode" in w for w in a["warnings"]) and a["exposure"]["gain_code"] is None
    st = S.SpectralState()
    for _ in range(50):
        st.on_spec({f[0]: rng.choice(junk) for f in S.SPEC_V31_FIELDS} | {"flags": S.F_VALID}, 1.0)
    st.on_spec(spec_packet([1.0] * 18, [0.1] * 18, cls=S.CLASS_DAYLIGHT), math.nan)
    hdr = {"t0_ms": 1, "gain_code": 0, "int_cycles": 1}
    st.on_specburst(hdr | {"samples": [{"dt_ms": 0, "raw": [1] * 18}]}, math.inf)  # bad time
    st.on_specburst(hdr | {"samples": "junk"}, 2.0)
    st.on_specburst(hdr | {"samples": [{"dt_ms": -1, "raw": [1] * 18}, 7]}, 3.0)
    st.on_evr(0x0702, "INFO", "class changed", math.nan)
    json_safe(st.snapshot(now=math.nan))


def test_analyse_full_packet_through_the_codec():
    sun = S.band_values(lambda nm: 0.05 * S.planck_rel(nm, 5800.0))
    vals = spec_packet(sun, [0.002 * v for v in sun], flags=S.F_VALID | S.F_AGC | S.F_ATTITUDE,
                       zen=12.0, az=200.0, cls=S.CLASS_DAYLIGHT, conf=60)
    dec = S.decode_spec_v31(S.pack_spec_v31(vals))
    a = S.analyse(dec, t=5.0)
    json_safe(a)
    assert a["mode_name"] == "SURVEY" and a["flags"]["agc"] and a["flags"]["attitude"]
    assert a["exposure"]["integration_ms"] == pytest.approx(166.8)
    assert a["exposure"]["full_scale"] == 61439
    assert a["colorimetry"]["cct_k"] == pytest.approx(5800.0, rel=0.002)
    assert a["features"]["peak_nm"] is not None and 400 < a["features"]["centroid_nm"] < 940
    assert a["boresight"] == {"zenith_deg": pytest.approx(12.0),
                              "azimuth_deg": pytest.approx(200.0)}
    assert a["classification"]["class"] == "DAYLIGHT" and a["classification"]["agrees"]
    assert a["indices"]["basis"] == "spectrum" and a["radiometry"]["ppfd"]["value"] > 0


def test_saturated_raw_sentinel_is_disambiguated():
    raw = [None] * 18                                        # decom maps 0xFFFF to None
    for cycles in (100, 10):                                 # AS-DP-04: whatever int_cycles is
        a = S.analyse(spec_packet([1.0] * 18, cycles=cycles, sat=0b11, raw=raw))
        assert a["raw"][0] == 0xFFFF and a["raw"][2] is None
        assert a["saturated"][:3] == [True, True, False]
        assert a["indices"]["ndvi"]["saturated"] is False


def test_spectral_evr_range():
    assert list(S.EVR_SPECTRAL_IDS) == list(range(0x0701, 0x0708))
    st = S.SpectralState()
    for eid in (0x0700, 0x0706, 0x0707, 0x0708):
        st.on_evr(eid, "INFO", f"evr {eid:#06x}", 1.0)
    assert [e["event_id"] for e in st.snapshot()["spectral"]["events"] if e["source"] == "S/C"] == [0x0706, 0x0707]


# --- flight cross-check (vectors written by tools/host_test/test_spectral.cpp) -----------
# <out>/spectral_vectors.txt, one record per line, '#' = comment, flight FP32 code:
#   log <value %.9g> <code hex>          spec_log_encode(value) == code
#   logdec <code hex> <value %.9g>       spec_log_decode(code)
#   spec31 <hex>                         a packed SPEC v3.1 user-data field (129 octets)
#   field <name> <raw> <eng %.9g | nan>  every field of that packet, ground names and units
#   specburst <encoding> <hex> <samples> a packed SPECBURST user-data field (contract 28 layout),
#                                        samples "<dt_ms>:<raw0>;...;<raw17>" joined by '/'
#                                        (65535 = not converted, 65534 = saturated)
# Nine significant digits identify a binary32 value uniquely: to_f32(float(text)) is the
# flight's value bit for bit.
VECTOR_KINDS = ("log", "logdec", "spec31", "field", "specburst")
NA_RAW = {"b": -128, "h": 0x7FFF, "H": 0xFFFF}


@pytest.fixture(scope="module")
def flight_vectors(tmp_path_factory) -> dict[str, list[list[str]]]:
    if not (shutil.which("make") and (shutil.which("g++") or shutil.which("c++"))):
        pytest.skip("no host C++ toolchain to build tools/host_test")
    build = subprocess.run(["make", "-s", "-C", str(HOST), "host_test"], capture_output=True,
                           text=True, check=False)
    if build.returncode != 0:
        pytest.skip("tools/host_test does not build at the moment: " + build.stderr[-300:])
    out = tmp_path_factory.mktemp("host_test")
    subprocess.run([str(HOST / "host_test"), str(out)], capture_output=True, check=False)
    f = out / "spectral_vectors.txt"
    if not f.exists():
        pytest.skip("tools/host_test wrote no spectral_vectors.txt (test_spectral.cpp absent)")
    rec: dict[str, list[list[str]]] = {k: [] for k in VECTOR_KINDS}
    for line in f.read_text().splitlines():
        tok = line.split("#", 1)[0].split()
        if tok and tok[0] in rec:
            rec[tok[0]].append(tok[1:])
    counts = {k: len(v) for k, v in rec.items()}
    assert counts["log"] and counts["logdec"] and counts["spec31"] == 1 and \
        counts["field"] == len(S.SPEC_V31_FIELDS) and counts["specburst"] >= 1, counts
    return rec


def _f32(text: str) -> float:
    return S.to_f32(float(text))


def _ulp32(x: float) -> float:
    b = struct.unpack(">I", struct.pack(">f", abs(x)))[0]
    return struct.unpack(">f", struct.pack(">I", b + 1))[0] - abs(x)


def _check_flight_decode(code: int, flight: float) -> None:
    """The ground's exact decode is within 2 ulp of the flight's binary32 one, and the binary32
    emulation is bit-exact except at the exp2f near-tie codes (see log_decode_f32)."""
    exact = S.log_decode(code)
    if math.isnan(flight):
        assert code == S.LOG_CODE_NAN and math.isnan(exact)
        return
    assert abs(exact - flight) <= 2 * _ulp32(flight), hex(code)
    if (code & 0x7FFF) % 1024 != 89:
        assert S.log_decode_f32(code) == flight, hex(code)


def test_flight_log_encoder_bit_exact(flight_vectors):
    for value, code in flight_vectors["log"]:
        assert S.log_encode(_f32(value)) == int(code, 16), (value, code)


def test_flight_log_decoder(flight_vectors):
    for code, value in flight_vectors["logdec"]:
        _check_flight_decode(int(code, 16), _f32(value))


def test_flight_spec_v31_packet_and_fields(flight_vectors):
    data = bytes.fromhex(flight_vectors["spec31"][0][0])
    assert len(data) == S.SPEC_V31_LEN
    raw = struct.unpack(">" + "".join(f[1] for f in S.SPEC_V31_FIELDS), data)
    rec = {name: (int(r), v) for name, r, v in flight_vectors["field"]}
    assert list(rec) == [f[0] for f in S.SPEC_V31_FIELDS]   # same names, same order
    assert [rec[f[0]][0] for f in S.SPEC_V31_FIELDS] == list(raw)
    dec = S.decode_spec_v31(data)
    for name, code, _scale, _unit, _desc, _lim, decoder in S.SPEC_V31_FIELDS:
        r, eng = rec[name]
        if decoder is S.log_decode:
            _check_flight_decode(r, _f32(eng))
        if dec[name] is None:
            assert r == NA_RAW[code], name
        else:
            assert dec[name] == pytest.approx(float(eng), rel=1e-7, abs=1e-9), name
    assert dec["bore_az"] == pytest.approx(200.0) and dec["temp_2"] is None
    json_safe(S.analyse(dec))
    # Bit-exact re-encode. The one non-injective code is 0x8000 ("negative, below 1e-4"),
    # which decodes to 0 like the flight decoder and re-encodes as 0x0000.
    want = bytearray(data)
    for k in range(36):
        if want[10 + 2 * k:12 + 2 * k] == b"\x80\x00":
            want[10 + 2 * k:12 + 2 * k] = b"\x00\x00"
    assert S.pack_spec_v31(dec) == bytes(want)


def test_flight_specburst(flight_vectors):
    """Every flight SPECBURST record ("specburst <encoding> <hex> <samples>", one Rice and one
    raw): the header's encoding is the stated one, the payload decodes to the stated samples
    (65535 = not converted -> None, 65534 = saturated), it re-encodes bit-exactly with that
    encoding, and the ground reference encoder makes the same raw/Rice choice."""
    seen = set()
    for enc_tok, hx, samples_txt, *_rest in flight_vectors["specburst"]:
        data = bytes.fromhex(hx)
        d = S.decode_specburst(data)
        assert d["encoding"] == int(enc_tok)
        seen.add(d["encoding"])
        want = []
        for item in samples_txt.split("/"):
            dt, raws = item.split(":")
            want.append((int(dt), [None if int(r) == 0xFFFF else int(r) for r in raws.split(";")]))
        assert [(x["dt_ms"], x["raw"]) for x in d["samples"]] == want
        for x, (_dt, raw) in zip(d["samples"], want, strict=True):
            assert x["saturated"] == [r is not None and r >= S.full_scale(d["int_cycles"]) - 1 for r in raw]
        hdr = (d["t0_ms"], d["rate_dhz"], d["gain_code"], d["int_cycles"])
        assert S.pack_specburst(*hdr, want, encoding=d["encoding"]) == data
        assert S.pack_specburst(*hdr, want) == data                    # same choice as the flight
    assert seen == {S.BURST_RAW, S.BURST_RICE}
