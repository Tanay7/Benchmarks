"""FOP-1 (dssq.fop) against the FARM-1 model, and the CMDVER command source (I1).

Phase C regressions: transmission-limit purge (C03), lockout recovery purge (C04),
invalid-N(R) detection (C05), one FARM-B increment per bypass frame (C06) and the
CMDVER source field replacing the tc_seq 0xFE/0xFF sentinels (C55)."""
from __future__ import annotations

from dssq import dictionary as dd
from dssq.ccsds.packets import SpacePacket, build_packet
from dssq.ccsds.tc import Farm1Model, cltu_decode, parse_tc_frame
from dssq.ccsds.timecode import CucTime
from dssq.ccsds.tm import Clcw
from dssq.cmdparse import parse
from dssq.decom import Decommutator, encode
from dssq.fop import Fop1


def clcw(farm: Farm1Model) -> Clcw:
    return Clcw(0, lockout=farm.state == 3, retransmit=farm.retransmit, farm_b_counter=farm.farm_b,
                report_value=farm.vr)


class Loop:
    """One uplink window per step: CLCW -> FOP, FOP CLTUs -> FARM (unless lost)."""

    def __init__(self, vr0: int = 0, **fop_kw):
        self.farm = Farm1Model(10)
        self.farm.vr = vr0
        kw = {"t1_s": 25.0, "tx_limit": 3, "window": 5, "verify_timeout_s": 1e9, **fop_kw}
        self.fop = Fop1(**kw)
        self.t = 1000.0
        self.log: list[tuple[int, bool, str]] = []

    def step(self, submit=(), lose=lambda seq, bypass: False, deliver: int | None = None):
        self.t += 10
        for text in submit:
            self.fop.submit(parse(text), self.t)
        self.fop.on_clcw(clcw(self.farm), self.t)
        for i, c in enumerate(self.fop.next_cltus(self.t)):
            info, _ = cltu_decode(c)
            fr = parse_tc_frame(info)
            assert fr is not None
            if lose(fr.seq, fr.bypass) or (deliver is not None and i >= deliver):
                continue
            self.log.append((fr.seq, fr.bypass, self.farm.on_frame(fr)))

    def states(self) -> dict[str, str]:
        return {r.cmd.text: r.state for r in self.fop.all}

    def events(self) -> str:
        return " | ".join(t for _, t in self.fop.events)


# ---------------------------------------------------------------- C03
def test_transmission_limit_purges_the_sent_queue_and_unlock_restores_ad_service():
    lp = Loop()
    lp.step()                                                       # CLCW: V(S) = 0
    lp.step(["NOOP", "PING 1"], lose=lambda seq, byp: not byp and seq == 0)
    for _ in range(12):
        lp.step(lose=lambda seq, byp: not byp and seq == 0)         # N(S)=0 lost every time
    st = lp.states()
    assert st == {"NOOP": "SUSPENDED", "PING 1": "SUSPENDED"} and lp.fop.suspended
    assert lp.fop.sent == [] and "transmission limit 3 reached" in lp.events()
    n = len(lp.log)
    lp.step(["UNLOCK", "PING 2"])                                   # link healthy again
    for _ in range(4):
        lp.step()
    st = lp.states()
    assert st["UNLOCK"] == "EXECUTED" and st["PING 2"] == "ACCEPTED" and not lp.fop.suspended
    assert lp.farm.vr == 1 and (0, False, "ACCEPT") in lp.log      # PING 2 numbered from N(R) = 0
    assert [v for _, _, v in lp.log[n:]] == ["CONTROL_UNLOCK", "ACCEPT"]   # no stale frame re-sent


# ---------------------------------------------------------------- C04 / C05
def test_spacecraft_reset_is_an_invalid_nr_not_an_acknowledgement():
    lp = Loop(vr0=200)
    lp.step(["MODE SAFE", "PING 5"], lose=lambda seq, byp: True)    # radiated N(S) 200, 201: lost
    lp.farm = Farm1Model(10)                                        # spacecraft reboots: V(R) = 0
    for _ in range(4):
        lp.step()
    st = lp.states()
    assert "ACCEPTED" not in st.values()                            # the FARM never saw them
    assert lp.fop.suspended and "invalid CLCW N(R)=0 (expected 200..202)" in lp.events()
    assert lp.events().count("invalid CLCW") == 1                   # one alert, not one per CLCW
    assert not lp.log                                               # nothing radiated while suspended


def test_unlock_after_a_reset_purges_stale_frames_and_never_relocks():
    lp = Loop(vr0=50)
    lp.step(["MODE SAFE", "PING 5"], lose=lambda seq, byp: True)
    lp.farm = Farm1Model(10)                                        # V(R) 50 -> 0
    for _ in range(4):
        lp.step()
    lp.step(["UNLOCK"])
    lp.step(["PING 6"])
    for _ in range(4):
        lp.step()
    st = lp.states()
    assert st["MODE 1"] == st["PING 5"] == "SUSPENDED" and st["UNLOCK"] == "EXECUTED"
    assert st["PING 6"] == "ACCEPTED" and lp.farm.state == 1 and lp.farm.vr == 1
    assert not any(v == "DISCARD_LOCKOUT" for _, _, v in lp.log)
    assert not lp.fop.suspended and not lp.fop.lockout


def test_lockout_cleared_purges_frames_numbered_for_the_old_vr():
    lp = Loop()
    lp.step()
    lp.step(["NOOP", "PING 1"], lose=lambda seq, byp: True)         # N(S) 0, 1 outstanding
    lp.farm.state, lp.farm.lockout, lp.farm.vr = 3, True, 30        # FARM locked out at V(R) 30
    lp.step()
    assert lp.fop.lockout and lp.fop.suspended
    lp.step(["UNLOCK"])
    lp.step(["PING 2"])
    for _ in range(4):
        lp.step()
    st = lp.states()
    assert st["NOOP"] == st["PING 1"] == "SUSPENDED" and st["PING 2"] == "ACCEPTED"
    assert lp.farm.state == 1 and lp.farm.vr == 31
    assert (30, False, "ACCEPT") in lp.log and not any(v == "DISCARD_LOCKOUT" for _, _, v in lp.log)


def test_normal_acknowledgement_still_follows_nr():
    lp = Loop(vr0=254)
    lp.step()
    lp.step(["NOOP", "PING 1"])
    lp.step(["PING 2"])
    lp.step()
    assert set(lp.states().values()) == {"ACCEPTED"} and lp.farm.vr == 1 and lp.fop.vs == 1
    assert not lp.fop.suspended and not lp.fop.sent and lp.fop.nnr == 1


# ---------------------------------------------------------------- C06
def test_one_farm_b_increment_confirms_one_bypass_frame():
    lp = Loop()
    lp.step()
    lp.step(["UNLOCK", "SETVR 7"], deliver=1)                       # only the UNLOCK arrives
    lp.step()
    st = lp.states()
    assert st["UNLOCK"] == "EXECUTED" and st["SETVR 7"] == "RADIATED" and lp.farm.vr == 0
    lp2 = Loop()
    lp2.step()
    lp2.step(["UNLOCK", "SETVR 7"])                                 # both arrive
    lp2.step(["PING 3"])
    lp2.step()
    lp2.step()
    st = lp2.states()
    assert st["UNLOCK"] == st["SETVR 7"] == "EXECUTED" and st["PING 3"] == "ACCEPTED"
    assert lp2.farm.vr == 8 and (7, False, "ACCEPT") in lp2.log     # AD numbered from the new V(R)


def test_setvr_not_reflected_in_the_clcw_is_not_executed():
    lp = Loop()
    lp.step()
    lp.step(["SETVR 9"])
    lp.farm.vr = 3                                                  # e.g. another station: V(R) != 9
    lp.step()
    assert lp.states()["SETVR 9"] == "RADIATED"


# ---------------------------------------------------------------- C55 / I1
def _cmdver(tc_seq: int, opcode: int, stage: int, source: int) -> dict:
    user = encode(dd.APID_CMDVER, {"tc_seq": tc_seq, "opcode": opcode, "stage": stage, "cmd_source": source,
                                   "error_code": 0, "ground_tag": 0})
    return Decommutator().decode(SpacePacket.parse(build_packet(dd.APID_CMDVER, 0, user, CucTime(1, 0))))


def test_cmdver_stage_octet_decodes_stage_and_source():
    v = _cmdver(254, 1, 2, dd.CMD_SOURCE_STORED)
    assert (v["tc_seq"], v["stage"], v["cmd_source"], v["stage_spare"]) == (254, 2, 2, 0)
    raw = bytes([7, 9, 0x40 | 3, 1]) + bytes(4)                     # source BD, stage FAILED
    w = Decommutator().decode(SpacePacket.parse(build_packet(dd.APID_CMDVER, 1, raw, CucTime(1, 0))))
    assert (w["stage"], w["cmd_source"], dd.CMD_STAGES[w["stage"]]) == (3, dd.CMD_SOURCE_BD, "FAILED")
    assert encode(dd.APID_CMDVER, w) == raw
    assert dd.CMD_SOURCES == {0: "AD", 1: "BD/HARDLINE", 2: "STORED"}


def test_ad_frames_254_and_255_are_verified_like_any_other_nr():
    lp = Loop(vr0=253)
    lp.step()
    lp.step(["NOOP", "BD NOOP"])                                    # AD N(S) 253 + a BD NOOP
    lp.step(["NOOP"])
    lp.step(["NOOP"])
    ad = [r for r in lp.fop.all if r.cmd.kind == "AD"]
    bd = next(r for r in lp.fop.all if r.cmd.kind == "BD")
    assert [r.ns for r in ad] == [253, 254, 255]
    for r in reversed(ad):                                          # out of order on purpose
        got = lp.fop.on_cmdver(_cmdver(r.ns, 1, 2, dd.CMD_SOURCE_AD), lp.t)
        assert got is r and r.state == "EXECUTED"
    assert bd.state != "EXECUTED"                                   # an AD report never closes a BD record
    assert lp.fop.on_cmdver(_cmdver(254, 1, 2, dd.CMD_SOURCE_STORED), lp.t) is None
    assert lp.fop.on_cmdver(_cmdver(0, 1, 3, dd.CMD_SOURCE_BD), lp.t) is bd and bd.state == "FAILED"
    assert lp.fop.on_cmdver(_cmdver(0, 1, 2, dd.CMD_SOURCE_BD), lp.t) is None    # nothing open
    assert lp.fop.on_cmdver(_cmdver(0, 1, 2, 3), lp.t) is None                     # unknown source
