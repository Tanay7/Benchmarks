"""CCSDS Tracking Data Message (503.0-B-2 Cor. 1) and XTCE 1.2 (660.0-B-2) products.

Expected values come from the documents, never from the code under test:
  * TDM: Blue Book Annex E examples (Figures E-1 one-way, E-3 two-way, transcribed below),
    Table 3-3 (metadata order and normative values), Table 3-5 (units), section 4 (KVN);
    physical expectations (dBm -> dBW, C/N0 = SNR + 10 log10 B, one-way range rate
    = -c x Doppler / f) are recomputed here by hand.
  * XTCE: the generated database must decode packets and encode commands exactly like the
    GDS (decom.Decommutator, cmdparse.tc_packet) - checked with a small independent XTCE
    interpreter in this file - and validate against SpaceSystem.xsd when the xmlschema
    package and the schema file are available (dev bench; skipped otherwise).
"""
import math
import os
import random
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from dssq import cmdparse, dictionary as dd, products as pr
from dssq.ccsds.packets import SpacePacket, build_packet
from dssq.ccsds.timecode import CucTime
from dssq.decom import Decommutator

C = 299_792_458.0
F0 = 433.125e6                                  # channel 23: 410.125 + 23 MHz
T0 = 1_790_985_600.0                            # 2026-10-03T00:00:00Z


def _rec(i, **kw):
    base = {"t_utc": T0 + 20.0 * i, "rx_power_dbm": -120.0 + i, "snr_db": -8.0 + 0.5 * i,
            "freq_offset_hz": 40.0 - i, "afc_correction_hz": 2500.0, "nominal_freq_hz": F0,
            "noise_bw_hz": 62_500.0}
    base.update(kw)
    return pr.TdmRecord(**base)


def _obs(seg, key):
    return [o for o in seg.observations if o.keyword == key]


# ============================================================== TDM producer
def test_tdm_sx126x_pass_is_strictly_valid_with_hand_computed_values():
    recs = [_rec(i, integration_s=17.318) for i in range(4)]
    text = pr.build_tdm(recs, station="DSS-Q1", freq_offset_cal_hz=300.0, radio_model="SX126x (E22-400M33S)",
                        creation_utc=T0 + 3600, message_id="VGQ1_DSS-Q1_TEST")
    tdm = pr.parse_tdm(text, strict=True)
    assert tdm.version == "2.0" and tdm.originator == "DSS-Q1" and tdm.message_id == "VGQ1_DSS-Q1_TEST"
    assert tdm.creation_date == "2026-10-03T01:00:00.000"
    (seg,) = tdm.segments
    md = seg.metadata
    # One-way numbering as in Blue Book Figures E-1/E-2: station = 1, spacecraft = 2, PATH = 2,1.
    assert (md["PARTICIPANT_1"], md["PARTICIPANT_2"]) == ("DSS-Q1", "VGQ-1")
    assert (md["MODE"], md["PATH"]) == ("SEQUENTIAL", "2,1")
    assert (md["TIME_SYSTEM"], md["TRANSMIT_BAND"], md["RECEIVE_BAND"]) == ("UTC", "UHF", "UHF")
    assert (md["INTEGRATION_INTERVAL"], md["INTEGRATION_REF"]) == ("17.318000", "END")
    assert float(md["FREQ_OFFSET"]) == F0 and md["DATA_QUALITY"] == "RAW"
    assert (md["START_TIME"], md["STOP_TIME"]) == ("2026-10-03T00:00:00.000", "2026-10-03T00:01:00.000")
    assert md["DATA_TYPES"] == "TRANSMIT_FREQ_2,RECEIVE_FREQ_1,DOPPLER_INSTANTANEOUS,CARRIER_POWER,PC_N0"
    # Metadata keywords appear in Table 3-3 order.
    order = list(md)
    assert order == sorted(order, key=lambda k: pr.META_ORDER[k])
    assert _obs(seg, "TRANSMIT_FREQ_2")[0].value == F0 and len(_obs(seg, "TRANSMIT_FREQ_2")) == 1
    for i in range(len(recs)):
        total = 2500.0 + (40.0 - i)                                      # AFC correction + residual
        assert _obs(seg, "RECEIVE_FREQ_1")[i].value == pytest.approx(total)
        assert _obs(seg, "CARRIER_POWER")[i].value == pytest.approx(-120.0 + i - 30.0)    # dBm -> dBW
        assert _obs(seg, "PC_N0")[i].value == pytest.approx(-8.0 + 0.5 * i + 10 * math.log10(62_500.0), abs=0.005)
        rr_km_s = -C * (total - 300.0) / F0 / 1000.0                    # above nominal -> approaching -> < 0
        assert _obs(seg, "DOPPLER_INSTANTANEOUS")[i].value == pytest.approx(rr_km_s, abs=1e-6)
        assert rr_km_s < 0
    assert pr.doppler_range_rate_km_s(1000.0, 1000.0, F0) == 0.0


def test_tdm_power_only_records_say_why():
    recs = [_rec(i, snr_db=None, freq_offset_hz=None, afc_correction_hz=0.0) for i in range(3)]
    tdm = pr.parse_tdm(pr.build_tdm(recs, station="DSS-Q1", radio_model="E22-400T33D", creation_utc=T0))
    (seg,) = tdm.segments
    assert {o.keyword for o in seg.observations} == {"CARRIER_POWER"}
    assert any("RSSI only" in c for c in seg.comments)
    assert "FREQ_OFFSET" not in seg.metadata and "INTEGRATION_INTERVAL" not in seg.metadata


def test_tdm_without_offset_calibration_has_no_range_rate():
    tdm = pr.parse_tdm(pr.build_tdm([_rec(0), _rec(1)], station="DSS-Q1", creation_utc=T0))
    (seg,) = tdm.segments
    assert not _obs(seg, "DOPPLER_INSTANTANEOUS") and len(_obs(seg, "RECEIVE_FREQ_1")) == 2
    assert any("not calibrated" in c for c in seg.comments)


def test_tdm_segments_split_sort_dedup_and_skip():
    recs = [_rec(3), _rec(0), _rec(1, nominal_freq_hz=F0 + 1e6), _rec(2, nominal_freq_hz=F0 + 1e6),
            _rec(4), _rec(4, rx_power_dbm=-50.0),                               # same millisecond -> dropped
            _rec(5, rx_power_dbm=None, snr_db=None, freq_offset_hz=None)]       # nothing to report
    tdm = pr.parse_tdm(pr.build_tdm(recs, station="DSS-Q1", creation_utc=T0))
    assert [float(s.metadata["FREQ_OFFSET"]) for s in tdm.segments] == [F0, F0 + 1e6, F0]
    assert [len(_obs(s, "CARRIER_POWER")) for s in tdm.segments] == [1, 2, 2]
    assert _obs(tdm.segments[2], "CARRIER_POWER")[-1].value == pytest.approx(-120.0 + 4 - 30.0)
    with pytest.raises(ValueError):
        pr.build_tdm([_rec(0, rx_power_dbm=None, snr_db=None, freq_offset_hz=None)], station="DSS-Q1")


@pytest.mark.parametrize("bad", [{"t_utc": float("nan")}, {"rx_power_dbm": float("inf")}, {"snr_db": True},
                                 {"nominal_freq_hz": 0.0}, {"noise_bw_hz": -1.0}, {"t_utc": 1.0},
                                 {"integration_s": 0.0}, {"freq_offset_hz": "12"}])
def test_tdm_record_rejects_invalid_values(bad):
    with pytest.raises(ValueError):
        _rec(0, **bad)


def test_tdm_producer_argument_checks():
    with pytest.raises(ValueError):
        pr.build_tdm([_rec(0, nominal_freq_hz=145.8e6)], station="DSS-Q1")      # VHF is not "UHF"
    with pytest.raises(ValueError):
        pr.build_tdm([_rec(0)], station="DSS-Q1", band="S")
    with pytest.raises(ValueError):
        pr.build_tdm([_rec(0)], station="")
    with pytest.raises(ValueError):
        pr.build_tdm([_rec(0)], station="DSS-Q1", integration_ref="LATE")
    with pytest.raises(ValueError):
        pr.build_tdm(["not a record"], station="DSS-Q1")


def test_kvn_number_and_time_formatting():
    assert pr.format_value(-0.0004, 2) == "0.00"                       # never "-0" (4.3)
    assert pr.format_value(433125000.0, 1) == "433125000.0"
    big = pr.format_value(123456789012345.678, 6)                       # > 16 digits -> floating point
    assert pr._number(big, True, "x") == pytest.approx(123456789012345.678)
    assert pr.format_epoch(T0 + 59.9996) == "2026-10-03T00:01:00.000"   # no "60.000"
    assert pr.format_epoch(T0 + 0.0004) == "2026-10-03T00:00:00.000"
    assert pr.parse_epoch("2005-159T17:41:00") == pr.parse_epoch("2005-06-08T17:41:00Z")
    assert pr.parse_epoch("2016-366T23:59:60.5")[1] == 86400 + pr.Decimal("0.5")   # leap year, leap second
    for bad in ("2005-366T00:00:00", "2005-02-29T00:00:00", "2005-159T24:00:00", "2005-159T12:00:60",
                "2005-159 17:41:00", "05-159T17:41:00"):
        with pytest.raises(ValueError):
            pr.parse_epoch(bad)


def test_recorder_writes_standard_file_names(tmp_path):
    rec = pr.TdmRecorder("DSS-Q1", max_records=3)
    assert rec.write(tmp_path) is None                                  # empty pass -> no file
    assert all(rec.add(_rec(i)) for i in range(3)) and not rec.add(_rec(3)) and rec.dropped == 1
    assert not rec.add("x")                                             # type: ignore[arg-type]
    path = rec.write(tmp_path, freq_offset_cal_hz=0.0, radio_model="SX126x", creation_utc=T0)
    assert path == tmp_path / "tdm" / "VGQ1_DSS-Q1_20261003T000000Z.kvn"
    tdm = pr.parse_tdm(path.read_text(encoding="ascii"))
    assert tdm.message_id == "VGQ1_DSS-Q1_20261003T000000Z"
    assert sum(len(s.observations) for s in tdm.segments) == 3 * 4 + 1           # 4 keywords + one beacon
    assert not list((tmp_path / "tdm").glob("*.tmp"))
    rec.clear()
    assert len(rec) == 0 and rec.dropped == 0
    with pytest.raises(ValueError):
        pr.TdmRecorder("DSS-Q1", max_records=0)


# ============================================================== TDM reader
# Blue Book Figure E-1 (one-way data), first records, as printed.
BLUE_BOOK_E1 = """CCSDS_TDM_VERS = 2.0
COMMENT TDM example created by yyyyy-nnnA Nav Team (NASA/JPL)
COMMENT StarTrek 1-way data, Ka band down
CREATION_DATE = 2005-160T20:15:00Z
ORIGINATOR = NASA
META_START
COMMENT Data quality degraded by antenna pointing problem...
COMMENT Slightly noisy data
TIME_SYSTEM = UTC
PARTICIPANT_1 = DSS-25
PARTICIPANT_2 = yyyy-nnnA
MODE = SEQUENTIAL
PATH = 2,1
INTEGRATION_INTERVAL = 1
INTEGRATION_REF = MIDDLE
FREQ_OFFSET = 0
TRANSMIT_DELAY_1 = 0.000077
RECEIVE_DELAY_1 = 0.000077
DATA_QUALITY = DEGRADED
META_STOP
DATA_START
COMMENT TRANSMIT_FREQ_2 is spacecraft reference downlink
TRANSMIT_FREQ_2 = 2005-159T17:41:00 32023442781.733
RECEIVE_FREQ_1 = 2005-159T17:41:00 32021034790.7265
RECEIVE_FREQ_1 = 2005-159T17:41:01 32021034828.8432
RECEIVE_FREQ_1 = 2005-159T17:41:02 32021034866.9449
DATA_STOP
"""

# Blue Book Figure E-3 (two-way frequency data), abridged; no blanks around '=' as printed.
BLUE_BOOK_E3 = """CCSDS_TDM_VERS=2.0
COMMENT TDM example created by yyyyy-nnnA Nav Team (NASA/JPL)
CREATION_DATE=2005-184T20:15:00
ORIGINATOR=NASA
META_START
TIME_SYSTEM=UTC
START_TIME=2005-184T11:12:23
STOP_TIME=2005-184T13:59:43.27
PARTICIPANT_1=DSS-55
PARTICIPANT_2=yyyy-nnnA
MODE=SEQUENTIAL
PATH=1,2,1
INTEGRATION_INTERVAL=1.0
INTEGRATION_REF=MIDDLE
META_STOP
DATA_START
TRANSMIT_FREQ_1=2005-184T11:12:23 7175173383.615373
TRANSMIT_FREQ_RATE_1=2005-184T11:12:23 0.40220
TRANSMIT_FREQ_1=2005-184T11:12:24 7175173384.017573
TRANSMIT_FREQ_RATE_1=2005-184T11:12:24 0.40220
RECEIVE_FREQ_1=2005-184T13:59:27.27 8429753135.986102
RECEIVE_FREQ_1=2005-184T13:59:43.27 8429749418.986191
DATA_STOP
"""


def test_reader_accepts_the_blue_book_examples_strictly():
    e1 = pr.parse_tdm(BLUE_BOOK_E1, strict=True)
    assert e1.comments == ["TDM example created by yyyyy-nnnA Nav Team (NASA/JPL)", "StarTrek 1-way data, Ka band down"]
    (seg,) = e1.segments
    assert seg.metadata["PATH"] == "2,1" and seg.comments[1] == "Slightly noisy data"
    assert seg.values("RECEIVE_FREQ_1") == [32021034790.7265, 32021034828.8432, 32021034866.9449]
    assert seg.data_comments == ["TRANSMIT_FREQ_2 is spacecraft reference downlink"]
    e3 = pr.parse_tdm(BLUE_BOOK_E3, strict=True)
    assert e3.segments[0].values("TRANSMIT_FREQ_RATE_1") == [0.4022, 0.4022]
    assert e3.segments[0].observations[-1].text == "8429749418.986191"
    crlf = pr.parse_tdm(BLUE_BOOK_E1.replace("\n", "\r\n"), strict=True)            # 4.2: CR LF lines
    assert len(crlf.segments[0].observations) == 4


def _mutate(old, new, text=BLUE_BOOK_E1):
    assert old in text
    return text.replace(old, new, 1)


@pytest.mark.parametrize("old,new", [
    ("TIME_SYSTEM = UTC\n", ""),                                              # mandatory
    ("PARTICIPANT_1 = DSS-25\nPARTICIPANT_2 = yyyy-nnnA\n", ""),              # mandatory, PATH refs
    ("FREQ_OFFSET = 0\n", "FREQ_OFFSET = 0\nWRONG_KEYWORD = 1\n"),            # Table 3-3 only
    ("RECEIVE_FREQ_1 = 2005-159T17:41:01", "WRONG_KEYWORD = 2005-159T17:41:01"),   # Table 3-5 only
    ("32021034828.8432", "32021034828.8432 extra"),                           # timetag + value only
    ("32021034828.8432", "this-is-not-a-number"),
    ("32021034828.8432", "NaN"),
    ("2005-159T17:41:01", "2005-366T17:41:01"),                               # 2005 has 365 days
    ("PATH = 2,1", "PATH = 2, 1"),                                            # no blanks in PATH
    ("PATH = 2,1", "PATH = 2,3"),                                             # undefined participant
    ("PATH = 2,1\n", ""),                                                     # SEQUENTIAL needs PATH
    ("MODE = SEQUENTIAL", "MODE = ROUND_TRIP"),                               # normative value set
    ("DATA_QUALITY = DEGRADED", "DATA_QUALITY = SUPERB"),
    ("INTEGRATION_INTERVAL = 1", "INTEGRATION_INTERVAL = -1"),                # positive double
    ("TRANSMIT_DELAY_1 = 0.000077", "TRANSMIT_DELAY_1 = this-is-not-a-number"),
    ("FREQ_OFFSET = 0\n", "FREQ_OFFSET = 0\nCOMMENT late comment\n"),        # comments first only
    ("RECEIVE_FREQ_1 = 2005-159T17:41:02", "RECEIVE_FREQ_1 = 2005-159T17:41:01"),   # repeated pair
    ("RECEIVE_FREQ_1 = 2005-159T17:41:02", "RECEIVE_FREQ_1 = 2005-159T17:40:59"),   # not chronological
    ("DATA_STOP\n", ""),
    ("ORIGINATOR = NASA\n", ""),
    ("CREATION_DATE = 2005-160T20:15:00Z\nORIGINATOR = NASA", "ORIGINATOR = NASA\nCREATION_DATE = 2005-160T20:15:00Z"),
    ("CCSDS_TDM_VERS = 2.0", "CCSDS_TDM_VERS = 2"),
    ("META_START\n", ""),
    ("DATA_START\n", ""),
])
def test_reader_rejects_violations_in_both_modes(old, new):
    for strict in (True, False):
        with pytest.raises(pr.TdmError):
            pr.parse_tdm(_mutate(old, new), strict=strict)


@pytest.mark.parametrize("old,new", [
    ("PATH = 2,1", "PATH\t= 2,1"),                                            # TAB (4.2)
    ("32021034828.8432", "32021034828."),                                     # digit after the point (4.3)
    ("32021034828.8432", "32.021034828e9"),                                   # mantissa d.ddd (4.3)
    ("32021034828.8432", "32021034828.84321234"),                             # > 16 digits (4.3)
    ("FREQ_OFFSET = 0", "FREQ_OFFSET = -0.0"),                                # no -0 (4.3)
    ("TIME_SYSTEM = UTC\nPARTICIPANT_1 = DSS-25", "PARTICIPANT_1 = DSS-25\nTIME_SYSTEM = UTC"),  # Table 3-3 order
    ("TRANSMIT_FREQ_2 = 2005-159T17:41:00 32023442781.733", "TRANSMIT_FREQ_2 = 2005-159T17:41:00 -1.0"),  # positive
    ("TRANSMIT_FREQ_2 = 2005", "TRANSMIT_FREQ_3 = 2005"),                     # PARTICIPANT_3 undefined
    ("COMMENT Slightly noisy data", "COMMENT " + "x" * 260),                 # > 254 characters
])
def test_strict_only_rules(old, new):
    with pytest.raises(pr.TdmError):
        pr.parse_tdm(_mutate(old, new), strict=True)
    pr.parse_tdm(_mutate(old, new), strict=False)


def test_strict_time_span_rule_and_non_ascii():
    e3 = _mutate("STOP_TIME=2005-184T13:59:43.27", "STOP_TIME=2005-184T13:59:30", BLUE_BOOK_E3)
    with pytest.raises(pr.TdmError):
        pr.parse_tdm(e3, strict=True)                                          # data after STOP_TIME
    with pytest.raises(pr.TdmError):
        pr.parse_tdm(_mutate("STOP_TIME=2005-184T13:59:43.27", "STOP_TIME=2005-184T11:00:00", BLUE_BOOK_E3),
                     strict=False)                                       # START_TIME after STOP_TIME
    with pytest.raises(pr.TdmError):
        pr.parse_tdm(_mutate("NASA\n", "NASÄ\n"), strict=True)
    with pytest.raises(pr.TdmError):
        pr.parse_tdm(_mutate("NASA\n", "NASA\x07\n"), strict=False)
    with pytest.raises(pr.TdmError):
        pr.parse_tdm("", strict=False)


# Orekit's KVN transcriptions of the Annex E examples (and its malformed-file test cases);
# dev-bench reference copy, the test skips when it is absent.
# Set DSSQ_OREKIT_TDM_DIR to Orekit's src/test/resources/ccsds/tdm/kvn to run it.
OREKIT = Path(os.environ.get("DSSQ_OREKIT_TDM_DIR") or "/nonexistent/orekit-tdm-kvn")


@pytest.mark.skipif(not OREKIT.is_dir(), reason="Orekit TDM example files not on this bench")
def test_reader_parses_orekit_transcriptions_of_the_annex_e_examples():
    seen = 0
    for path in sorted(OREKIT.glob("TDMExample*.txt")):
        tdm = pr.parse_tdm(path.read_text(encoding="utf-8"), strict=False)
        assert tdm.segments and all(s.observations for s in tdm.segments), path.name
        seen += 1
    assert seen >= 5
    ex2 = pr.parse_tdm((OREKIT / "TDMExample2.txt").read_text(encoding="utf-8"), strict=False)
    md = ex2.segments[0].metadata
    assert (md["PARTICIPANT_1"], md["PARTICIPANT_2"], md["PATH"], md["FREQ_OFFSET"]) == \
        ("DSS-25", "yyyy-nnnA", "2,1", "32021035200.0")
    assert ex2.segments[0].values("RECEIVE_FREQ_1")[:2] == [-409.2735, -371.1568]
    for bad in ("TDM-data-inconsistent-line.txt", "TDM-data-number-format-error.txt", "TDM-data-wrong-keyword.txt",
                "TDM-metadata-number-format-error.txt", "TDM-metadata-wrong-keyword.txt", "TDM-issue963.txt"):
        with pytest.raises(pr.TdmError):
            pr.parse_tdm((OREKIT / bad).read_text(encoding="utf-8"), strict=False)


# ================================================================ CLI
def test_cli_builds_validates_and_exports(tmp_path, capsys):
    csv_path = tmp_path / "recs.csv"
    csv_path.write_text("t_utc,rx_power_dbm,snr_db,freq_offset_hz,afc_correction_hz,nominal_freq_hz,noise_bw_hz\n"
                        f"{T0},-118.5,-6.25,12,1000,{F0},62500\n"
                        "2026-10-03T00:00:20Z,-119.0,,,,433125000,62500\n", encoding="utf-8")
    out = tmp_path / "x.kvn"
    assert pr.main(["tdm", "build", str(csv_path), "--station", "DSS-Q1", "-o", str(out),
                    "--freq-offset-cal-hz", "0"]) == 0
    tdm = pr.parse_tdm(out.read_text(encoding="ascii"))
    (seg,) = tdm.segments                                                 # same channel: one segment
    assert seg.values("RECEIVE_FREQ_1") == [1012.0] and seg.values("CARRIER_POWER") == [-148.5, -149.0]
    assert pr.main(["tdm", "validate", str(out)]) == 0
    assert "valid TDM 2.0" in capsys.readouterr().out
    assert pr.main(["tdm", "build", str(csv_path), "--station", "DSS-Q1", "--archive", str(tmp_path)]) == 0
    assert (tmp_path / "tdm" / "VGQ1_DSS-Q1_20261003T000000Z.kvn").is_file()
    bad = tmp_path / "bad.kvn"
    bad.write_text(BLUE_BOOK_E1.replace("PATH = 2,1", "PATH = 2,9"), encoding="ascii")
    assert pr.main(["tdm", "validate", str(bad)]) == 1
    assert pr.main(["tdm", "validate", str(tmp_path / "missing.kvn")]) == 1
    csv_path.write_text("t_utc,rx_power_dbm\n1,2\n", encoding="utf-8")
    assert pr.main(["tdm", "build", str(csv_path), "--station", "DSS-Q1"]) == 1
    xml_path = tmp_path / "vgq1.xtce.xml"
    assert pr.main(["xtce", "-o", str(xml_path), "--date", "2026-10-11"]) == 0
    assert xml_path.read_bytes() == pr.xtce_bytes(pr.build_xtce(date_text="2026-10-11"))


# ================================================================ XTCE
X = f"{{{pr.XTCE_NS}}}"


class Xtce:
    """Minimal independent XTCE reader: name resolution, container inheritance, the
    encodings and calibrators the generator is allowed to use (anything else fails)."""

    def __init__(self, root):
        self.items = {}                                    # (system path, kind, name) -> element
        self.systems = {}
        self._walk(root, "")

    def _walk(self, ss, parent):
        path = f"{parent}/{ss.get('name')}"
        self.systems[path] = ss
        for kind, xp in (("ptype", "TelemetryMetaData/ParameterTypeSet/*"),
                         ("param", "TelemetryMetaData/ParameterSet/*"),
                         ("cont", "TelemetryMetaData/ContainerSet/*"),
                         ("atype", "CommandMetaData/ArgumentTypeSet/*"),
                         ("mc", "CommandMetaData/MetaCommandSet/*")):
            for e in ss.findall("/".join(X + p if p != "*" else "*" for p in xp.split("/"))):
                key = (path, kind, e.get("name"))
                assert key not in self.items, f"duplicate {key}"
                self.items[key] = e
        for child in ss.findall(f"{X}SpaceSystem"):
            self._walk(child, path)

    def get(self, path, kind, ref):
        if ref.startswith("/"):
            path, _, ref = ref.rpartition("/")
        e = self.items.get((path, kind, ref))
        assert e is not None, f"unresolved {kind} {path}/{ref}"
        return path, e

    # -------------------------------------------------------------- decoding
    def decode(self, path, cont, data, pos=0, vals=None):
        vals = {} if vals is None else vals
        base = cont.find(f"{X}BaseContainer")
        if base is not None:
            bpath, bcont = self.get(path, "cont", base.get("containerRef"))
            pos = self.decode(bpath, bcont, data, pos, vals)
            crit = base.find(f"{X}RestrictionCriteria/{X}Comparison")
            cpath, cparam = self.get(path, "param", crit.get("parameterRef"))
            if vals[(cpath, cparam.get("name"))][0] != int(crit.get("value")):
                raise LookupError("restriction not met")
        for e in cont.find(f"{X}EntryList"):
            ppath, param = self.get(path, "param", e.get("parameterRef"))
            tpath, ptype = self.get(ppath, "ptype", param.get("parameterTypeRef"))
            if e.tag == f"{X}ArrayParameterRefEntry":
                dim = ptype.find(f"{X}DimensionList/{X}Dimension")
                start = int(dim.find(f"{X}StartingIndex/{X}FixedValue").text)
                end = self._dynamic(ppath, dim.find(f"{X}EndingIndex/{X}DynamicValue"), vals)
                epath, etype = self.get(tpath, "ptype", ptype.get("arrayTypeRef"))
                items = []
                for _ in range(end - start + 1):
                    raw, val, pos = self._value(epath, etype, data, pos, vals)
                    items.append((raw, val))
                vals[(ppath, param.get("name"))] = ([r for r, _ in items], [v for _, v in items])
            else:
                raw, val, pos = self._value(tpath, ptype, data, pos, vals)
                vals[(ppath, param.get("name"))] = (raw, val)
        return pos

    def _dynamic(self, path, dv, vals):
        rpath, rparam = self.get(path, "param", dv.find(f"{X}ParameterInstanceRef").get("parameterRef"))
        lin = dv.find(f"{X}LinearAdjustment")
        return int(float(lin.get("slope")) * vals[(rpath, rparam.get("name"))][0] + float(lin.get("intercept")))

    def _value(self, path, ptype, data, pos, vals):
        tag = ptype.tag[len(X):]
        if tag == "StringParameterType":
            enc = ptype.find(f"{X}StringDataEncoding")
            assert enc.get("encoding") == "US-ASCII"
            bits = self._dynamic(path, enc.find(f"{X}Variable/{X}DynamicValue"), vals)
            assert bits % 8 == 0 and pos % 8 == 0 and bits <= int(enc.find(f"{X}Variable").get("maxSizeInBits"))
            raw = data[pos // 8:(pos + bits) // 8]
            return raw, raw.decode("ascii", "replace"), pos + bits
        ienc, fenc = ptype.find(f"{X}IntegerDataEncoding"), ptype.find(f"{X}FloatDataEncoding")
        enc = ienc if ienc is not None else fenc
        assert enc.get("byteOrder") == "mostSignificantByteFirst"
        bits = int(enc.get("sizeInBits"))
        word = _get_bits(data, pos, bits)
        if ienc is not None:
            raw = word - (1 << bits) if enc.get("encoding") == "twosComplement" and word >> (bits - 1) else word
            assert enc.get("encoding") in ("unsigned", "twosComplement")
        else:
            assert enc.get("encoding") == "IEEE754_1985" and bits in (32, 64)
            raw = struct.unpack(">f" if bits == 32 else ">d", word.to_bytes(bits // 8, "big"))[0]
        val = raw
        terms = enc.findall(f"{X}DefaultCalibrator/{X}PolynomialCalibrator/{X}Term")
        if terms:
            val = sum(float(t.get("coefficient")) * raw ** int(t.get("exponent")) for t in terms)
        if tag == "EnumeratedParameterType":
            labels = {int(en.get("value")): en.get("label")
                      for en in ptype.findall(f"{X}EnumerationList/{X}Enumeration")}
            val = labels.get(raw)
        return raw, val, pos + bits

    # -------------------------------------------------------------- encoding
    def encode(self, mnemonic, args, seq):
        _, mc = self.get(f"/{pr.SPACE_SYSTEM}", "mc", mnemonic)
        bm = mc.find(f"{X}BaseMetaCommand")
        _, base = self.get(f"/{pr.SPACE_SYSTEM}", "mc", bm.get("metaCommandRef"))
        assert base.get("abstract") == "true"
        values = {a.get("argumentName"): int(a.get("argumentValue"))
                  for a in bm.findall(f"{X}ArgumentAssignmentList/{X}ArgumentAssignment")}
        values.update(args)
        values.setdefault("ccsds_seq_count", seq)
        argdefs = {a.get("name"): a for m in (base, mc) for a in m.findall(f"{X}ArgumentList/{X}Argument")}
        for name, a in argdefs.items():
            if name not in values and a.get("initialValue") is not None:
                values[name] = int(a.get("initialValue"))
        cont = mc.find(f"{X}CommandContainer")
        assert cont.find(f"{X}BaseContainer").get("containerRef") == base.find(f"{X}CommandContainer").get("name")
        bits = []
        for c in (base.find(f"{X}CommandContainer"), cont):
            for e in c.find(f"{X}EntryList"):
                if e.tag == f"{X}FixedValueEntry":
                    n = int(e.get("sizeInBits"))
                    bits.append((int(e.get("binaryValue"), 16) & ((1 << n) - 1), n))
                else:
                    a = argdefs[e.get("argumentRef")]
                    _, at = self.get(f"/{pr.SPACE_SYSTEM}", "atype", a.get("argumentTypeRef"))
                    enc = at.find(f"{X}IntegerDataEncoding")
                    n = int(enc.get("sizeInBits"))
                    v = values[e.get("argumentRef")]
                    self.check_arg(at, v)
                    bits.append((v & ((1 << n) - 1), n))
        word, total = 0, 0
        for v, n in bits:
            word, total = (word << n) | v, total + n
        assert total % 8 == 0
        return word.to_bytes(total // 8, "big")

    @staticmethod
    def check_arg(at, v):
        if at.tag == f"{X}EnumeratedArgumentType":
            assert v in {int(e.get("value")) for e in at.findall(f"{X}EnumerationList/{X}Enumeration")}
        else:
            r = at.find(f"{X}ValidRangeSet/{X}ValidRange")
            assert int(r.get("minInclusive")) <= v <= int(r.get("maxInclusive"))


def _get_bits(data, pos, n):
    word = int.from_bytes(data, "big")
    total = 8 * len(data)
    assert pos + n <= total, "read past the end of the packet"
    return (word >> (total - pos - n)) & ((1 << n) - 1)


@pytest.fixture(scope="module")
def xtce_root():
    return ET.fromstring(pr.xtce_bytes(pr.build_xtce(date_text="2026-10-11")))


def test_xtce_document_is_well_formed_and_deterministic(xtce_root):
    a, b = pr.xtce_bytes(pr.build_xtce()), pr.xtce_bytes(pr.build_xtce())
    assert a == b and a.startswith(b"<?xml version='1.0' encoding='UTF-8'?>")
    assert xtce_root.tag == f"{X}SpaceSystem" and xtce_root.get("name") == "VGQ-1"
    assert pr.XTCE_NS == "http://www.omg.org/spec/XTCE/20180204"                  # CCSDS 660.0-B-2 / XTCE 1.2
    assert xtce_root.find(f"{X}Header").get("validationStatus") == "Working"
    names = [ss.get("name") for ss in xtce_root.findall(f"{X}SpaceSystem")]
    assert names == [dd.PACKETS[a]["name"] for a in sorted(dd.PACKETS)] + ["EVR", "RFSCAN", "SPECBURST", "CFDP"]


def test_xtce_references_resolve_and_containers_match_the_dictionary(xtce_root):
    xt = Xtce(xtce_root)
    root = f"/{pr.SPACE_SYSTEM}"
    for (path, kind, name), e in xt.items.items():
        assert pr._NAME_RE.match(name)
        if kind == "param":
            xt.get(path, "ptype", e.get("parameterTypeRef"))
        if kind == "cont" and e.get("abstract") != "true":
            # Every concrete container restricts on the APID, directly or through its abstract
            # parent (SPECBURST_N1..N3 restrict on the sample count n of SPECBURST).
            base = e.find(f"{X}BaseContainer")
            crit = base.find(f"{X}RestrictionCriteria/{X}Comparison")
            if crit.get("parameterRef") != f"{root}/ccsds_apid":
                assert crit.get("parameterRef") == "n" and path == f"{root}/SPECBURST"
                _, parent = xt.get(path, "cont", base.get("containerRef"))
                assert parent.get("abstract") == "true"
                pcrit = parent.find(f"{X}BaseContainer/{X}RestrictionCriteria/{X}Comparison")
                assert pcrit.get("parameterRef") == f"{root}/ccsds_apid"
                assert int(pcrit.get("value")) == dd.APID_SPECBURST
    by_name = {s["name"]: a for a, s in dd.PACKETS.items()}
    for apid, spec in dd.PACKETS.items():
        _, cont = xt.get(f"{root}/{spec['name']}", "cont", spec["name"])
        crit = cont.find(f"{X}BaseContainer/{X}RestrictionCriteria/{X}Comparison")
        assert int(crit.get("value")) == apid == by_name[spec["name"]]
        refs = [e.get("parameterRef") for e in cont.find(f"{X}EntryList")]
        want = []
        for f in spec["fields"]:                                  # a packed octet: its sub-fields, MSB first
            subs = dd.BITFIELDS.get(apid, {}).get(f[0])
            want += [n for n, _lsb, _w in sorted(subs, key=lambda x: -x[1])] if subs else [f[0]]
        assert [r for r in refs if "_reserved_" not in r] == want
    mcs = xtce_root.findall(f"{X}CommandMetaData/{X}MetaCommandSet/{X}MetaCommand")
    assert len(mcs) == len(dd.COMMANDS) + 1
    for mc in mcs[1:]:
        c = dd.COMMANDS[mc.get("name")]
        assign = {a.get("argumentName"): a.get("argumentValue") for a in mc.iter(f"{X}ArgumentAssignment")}
        assert int(assign["opcode"]) == c.opcode
        assert mc.find(f"{X}DefaultSignificance").get("consequenceLevel") == ("critical" if c.hazardous else "normal")
        args = [a.get("name") for a in mc.findall(f"{X}ArgumentList/{X}Argument")]
        assert args == [a[0] for a in c.args]
        for (_name, _code, lo, hi, enum), a in zip(c.args, mc.findall(f"{X}ArgumentList/{X}Argument"), strict=True):
            _, at = xt.get(root, "atype", a.get("argumentTypeRef"))
            if at.tag == f"{X}EnumeratedArgumentType":
                assert {int(e.get("value")) for e in at.iter(f"{X}Enumeration")} == set(range(lo, hi + 1))
                assert set(enum.values()) == set(range(lo, hi + 1))
            else:
                r = at.find(f"{X}ValidRangeSet/{X}ValidRange")
                assert (int(r.get("minInclusive")), int(r.get("maxInclusive"))) == (lo, hi)


def _random_raw(code, rng, na_rate=0.15):
    if code in dd.NA and rng.random() < na_rate:
        return dd.NA[code]                     # a sentinel, or a saturated reading (dd.NO_NA)
    if code == "f":
        return struct.unpack(">f", struct.pack(">f", rng.uniform(-1e4, 1e4)))[0]
    bits, signed = {"B": (8, 0), "b": (8, 1), "H": (16, 0), "h": (16, 1), "I": (32, 0), "i": (32, 1)}[code]
    return rng.randrange(-(1 << (bits - 1)), 1 << (bits - 1)) if signed else rng.randrange(0, 1 << bits)


def test_xtce_decodes_every_packet_exactly_like_the_decommutator(xtce_root):
    xt = Xtce(xtce_root)
    root = f"/{pr.SPACE_SYSTEM}"
    _, base = xt.get(root, "cont", pr.TM_BASE)
    decom = Decommutator()
    rng = random.Random(20261011)
    for apid, spec in dd.PACKETS.items():
        sys_path = f"{root}/{spec['name']}"
        _, cont = xt.get(sys_path, "cont", spec["name"])
        for trial in range(25):
            raws = [_random_raw(f[1], rng) for f in spec["fields"]]
            payload = struct.pack(">" + "".join(f[1] for f in spec["fields"]), *raws)
            t = CucTime(rng.randrange(1 << 32), rng.randrange(1 << 16))
            pkt = build_packet(apid, trial, payload, t)
            vals = {}
            end = xt.decode(sys_path, cont, pkt, 0, vals)
            assert end == 8 * len(pkt)                                    # every bit accounted for
            assert vals[(root, "ccsds_apid")][0] == apid and vals[(root, "ccsds_seq_count")][0] == trial
            assert vals[(root, "ccsds_packet_length")][0] == len(pkt) - 7
            assert vals[(root, "cuc_coarse")][0] == t.coarse
            assert vals[(root, "cuc_fine")][1] == pytest.approx(t.fine / 65536.0, abs=0)
            ref = decom.decode(SpacePacket.parse(pkt))
            for f, raw in zip(spec["fields"], raws, strict=True):
                subs = dd.BITFIELDS.get(apid, {}).get(f[0])
                if subs:                                                   # packed octet (I1): per sub-field
                    for sub, lsb, width in subs:
                        assert vals[(sys_path, sub)][0] == (raw >> lsb) & ((1 << width) - 1) == ref[sub]
                    continue
                raw_x, val_x = vals[(sys_path, f[0])]
                assert raw_x == raw or (f[1] == "f" and math.isnan(raw) and math.isnan(raw_x))
                decoder = f[6] if len(f) > 6 else None
                if ref[f[0]] is None:
                    assert raw == dd.field_na(apid, f)                     # documented sentinel
                elif decoder is not None:                                  # raw code + named decoder
                    assert ref[f[0]] == decoder(raw_x)
                elif val_x is None or isinstance(val_x, str):
                    assert raw == ref[f[0]]                                # enumerated: raw code
                else:
                    assert val_x == pytest.approx(ref[f[0]], rel=1e-12, abs=1e-12), (spec["name"], f[0])
            with pytest.raises(LookupError):                              # other APIDs do not match
                other = next(a for a in dd.PACKETS if a != apid)
                xt.decode(sys_path, cont, build_packet(other, 0, payload, t), 0, {})
    assert base.get("abstract") == "true"


def test_xtce_variable_length_evr_and_rfscan(xtce_root):
    xt = Xtce(xtce_root)
    root = f"/{pr.SPACE_SYSTEM}"
    decom = Decommutator()
    for text in (b"", b"BOOT partition 7", b"X" * pr.EVR_TEXT_MAX):
        pkt = build_packet(dd.APID_EVR, 1, bytes([2]) + (0x0601).to_bytes(2, "big") + text, CucTime(5, 0))
        vals = {}
        _, cont = xt.get(f"{root}/EVR", "cont", "EVR")
        assert xt.decode(f"{root}/EVR", cont, pkt, 0, vals) == 8 * len(pkt)
        ref = decom.decode(SpacePacket.parse(pkt))
        assert vals[(f"{root}/EVR", "severity")][1] == ref["severity"] == "WARN"
        assert vals[(f"{root}/EVR", "event_id")][0] == ref["event_id"] == 0x0601
        assert vals[(f"{root}/EVR", "text")][1] == ref["text"] == text.decode()
    for noise in ([-100], [-100, -128, 5], [-90] * pr.RFSCAN_MAX_CHANNELS):
        body = bytes([0, len(noise)]) + (1234).to_bytes(2, "big") + bytes(n & 0xFF for n in noise)
        pkt = build_packet(dd.APID_RFSCAN, 2, body, CucTime(9, 1))
        vals = {}
        _, cont = xt.get(f"{root}/RFSCAN", "cont", "RFSCAN")
        assert xt.decode(f"{root}/RFSCAN", cont, pkt, 0, vals) == 8 * len(pkt)
        ref = decom.decode(SpacePacket.parse(pkt))
        raws = vals[(f"{root}/RFSCAN", "noise")][0]
        assert raws == noise and [None if r == -128 else r for r in raws] == ref["noise"]
        assert vals[(f"{root}/RFSCAN", "duration_ms")][0] == ref["duration_ms"] == 1234


def _valid_args(c, rng):
    """Random argument values that pass the dictionary ranges and the command's check (the
    cross-argument rules are prose in XTCE, so only valid commands are compared)."""
    for _ in range(500):
        values = {aname: rng.choice([lo, hi, rng.randint(lo, hi)]) for (aname, _c, lo, hi, _e) in c.args}
        if c.check is None or c.check(values) is None:
            return values
    raise AssertionError(f"{c.mnemonic}: no valid argument set found")


def test_xtce_encodes_every_command_exactly_like_cmdparse(xtce_root):
    xt = Xtce(xtce_root)
    rng = random.Random(7)
    for mn, c in dd.COMMANDS.items():
        for _ in range(10):
            values = _valid_args(c, rng)
            seq = rng.randrange(1 << 14)
            if c.builder_only:                    # SEQLOAD: built from a sequence file
                ud = cmdparse.encode_dictionary(mn, [(None, str(v)) for v in values.values()])
                ref = build_packet(dd.APID_TC, seq, ud, None, tc=True)
            else:
                ref = cmdparse.tc_packet(cmdparse.parse(" ".join([mn, *map(str, values.values())])), seq)
            assert xt.encode(mn, values, seq) == ref, (mn, values)
    with pytest.raises(AssertionError):                                   # ranges are enforced
        c = next(c for c in dd.COMMANDS.values() if c.args and c.args[0][2] > 0)
        xt.encode(c.mnemonic, {c.args[0][0]: c.args[0][2] - 1, **{a[0]: a[2] for a in c.args[1:]}}, 0)


def _blob(xt, path, ptype, data, pos, vals):
    """BinaryParameterType with a DynamicValue size (the generator's only binary form)."""
    dv = ptype.find(f"{X}BinaryDataEncoding/{X}SizeInBits/{X}DynamicValue")
    bits = xt._dynamic(path, dv, vals)
    assert bits % 8 == 0 and pos % 8 == 0 and bits >= 0
    return data[pos // 8:(pos + bits) // 8], pos + bits


def _decode_variable(xt, sys_path, cont_name, pkt):
    """Decodes a packet against a container, handing BinaryParameterType entries to _blob."""
    vals = {}
    _, cont = xt.get(sys_path, "cont", cont_name)
    orig = xt._value

    def value(path, ptype, data, pos, v):
        if ptype.tag == f"{X}BinaryParameterType":
            raw, end = _blob(xt, path, ptype, data, pos, v)
            return raw, raw, end
        return orig(path, ptype, data, pos, v)
    xt._value = value
    try:
        end = xt.decode(sys_path, cont, pkt, 0, vals)
    finally:
        xt._value = orig
    return end, vals


def test_xtce_specburst_and_cfdp_containers(xtce_root):
    from dssq import spectral
    xt = Xtce(xtce_root)
    root = f"/{pr.SPACE_SYSTEM}"
    ss = f"{root}/SPECBURST"
    decom = Decommutator()
    _, abstract = xt.get(ss, "cont", "SPECBURST")
    assert abstract.get("abstract") == "true"
    rng = random.Random(28)
    for n in (1, 2, 3):
        for enc in (0, 1):
            if enc == 0:                                   # anything, incl. not converted / saturated
                samples = [(30 * k, [rng.choice([None, 0xFFFF, rng.randint(0, 900)]) for _ in range(18)])
                           for k in range(n)]
            else:                                          # a steady light: compresses
                samples = [(30 * k, [400 + rng.randint(0, 3) for _ in range(18)]) for k in range(n)]
            user = spectral.pack_specburst(123456, 401, 2, 1, samples, encoding=enc)
            pkt = build_packet(dd.APID_SPECBURST, n, user, CucTime(9, 9))
            end, vals = _decode_variable(xt, ss, f"SPECBURST_N{n}", pkt)
            assert end == 8 * len(pkt)
            ref = decom.decode(SpacePacket.parse(pkt))
            assert vals[(ss, "n")][0] == ref["n"] == n and vals[(ss, "encoding")][0] == ref["encoding"] == enc
            assert vals[(ss, "t0_ms")][0] == ref["t0_ms"] and vals[(ss, "rate_hz")][1] == pytest.approx(40.1)
            assert vals[(ss, "dt_ms")][0] == [s["dt_ms"] for s in ref["samples"]]
            assert vals[(ss, f"payload_n{n}")][0] == user[10 + 2 * n:]
            assert len(user[10 + 2 * n:]) == ref["payload_len"]
            for other in {1, 2, 3} - {n}:                      # the restriction on n selects one
                with pytest.raises(LookupError):
                    _decode_variable(xt, ss, f"SPECBURST_N{other}", pkt)
    from dssq.ccsds import cfdp
    for pdu in cfdp.Sender(0x00070001, "ssr_p7_1.bin", bytes(range(200))):
        pkt = build_packet(dd.APID_CFDP, 0, pdu, CucTime(1, 2))
        end, vals = _decode_variable(xt, f"{root}/CFDP", "CFDP", pkt)
        assert end == 8 * len(pkt) and vals[(f"{root}/CFDP", "pdu")][0] == pdu
        assert decom.decode(SpacePacket.parse(pkt)) == {"pdu_len": len(pdu)}


def test_xtce_notes_carry_rules_sentinels_and_labels(xtce_root):
    def long_desc(path):
        e = xtce_root.find(path)
        assert e is not None, path
        ld = e.find(f"{X}LongDescription")
        return "" if ld is None else ld.text
    mc = f"{X}CommandMetaData/{X}MetaCommandSet/{X}MetaCommand"
    assert dd.COMMANDS["SPECCFG"].check_doc in long_desc(f"{mc}[@name='SPECCFG']")
    assert "sequence file" in long_desc(f"{mc}[@name='SEQLOAD']")
    pts = f"{X}SpaceSystem[@name='RF']/{X}TelemetryMetaData/{X}ParameterTypeSet"
    assert "Every raw value is a reading" in long_desc(f"{pts}/*[@name='frame_period_ms_Type']")
    assert "(0x7FFF) = not available" in long_desc(f"{pts}/*[@name='uplink_rssi_Type']")
    hk = f"{X}SpaceSystem[@name='HK']/{X}TelemetryMetaData/{X}ParameterTypeSet"
    assert "3 = URGENT" in long_desc(f"{hk}/*[@name='beacon_level_Type']")         # limits keep it an integer
    labels = xtce_root.find(f"{hk}/{X}EnumeratedParameterType[@name='cfdp_state_Type']")
    assert {e.get("label") for e in labels.iter(f"{X}Enumeration")} == set(dd.CFDP_STATES.values())


def test_xtce_generator_refuses_what_it_cannot_express():
    with pytest.raises(ValueError):
        pr.build_xtce({0x050: {"name": "BAD", "desc": "", "fields": [("x", "e", None, "", "half float", None)]}})
    with pytest.raises(ValueError):
        pr.build_xtce({0x050: {"name": "BAD", "desc": "", "fields": [("a.b", "B", None, "", "dot", None)]}})
    with pytest.raises(ValueError):
        pr.build_xtce({0x050: {"name": "BAD", "desc": "", "fields": [("a", "B", None, "", "x", None)] * 2}})
    with pytest.raises(ValueError):
        pr.build_xtce(commands={"A": dd.CommandDef("B", 1)})


def test_xtce_custom_decoder_fields_are_raw_with_a_note():
    def spec_log_decode(raw):                                            # stands in for spectral.py's decoder
        return raw
    root = pr.build_xtce({0x022: {"name": "SPEC", "desc": "test", "fields": [
        ("spec_410nm", "H", None, "uW/cm2", "log-coded 410 nm", None, spec_log_decode)]}}, commands={})
    t = root.find(f"{X}SpaceSystem/{X}TelemetryMetaData/{X}ParameterTypeSet/{X}IntegerParameterType")
    assert t.get("name") == "spec_410nm_Type" and "spec_log_decode" in t.find(f"{X}LongDescription").text


# The normative XTCE 1.2 schema (OMG SpaceSystem.xsd, e.g. yamcs-xtce SpaceSystemV1.2.xsd): set
# DSSQ_XTCE_XSD to its path to run the validation (skipped otherwise).
XSD = Path(os.environ.get("DSSQ_XTCE_XSD") or "/nonexistent/SpaceSystemV1.2.xsd")


def test_xtce_validates_against_the_xtce_1_2_schema():
    xmlschema = pytest.importorskip("xmlschema")
    if not XSD.is_file():
        pytest.skip("SpaceSystem.xsd (XTCE 1.2) not on this bench; set DSSQ_XTCE_XSD")
    schema = xmlschema.XMLSchema(str(XSD))
    doc = pr.xtce_bytes(pr.build_xtce(date_text="2026-10-11")).decode("utf-8")
    errors = list(schema.iter_errors(doc))
    assert not errors, errors[:3]
    broken = doc.replace('consequenceLevel="critical"', 'consequenceLevel="dangerous"', 1)
    assert list(schema.iter_errors(broken))                                # the validator does bite
