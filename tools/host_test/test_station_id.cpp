// =============================================================================
//  Ground station identification on the uplink (amateur "DE <callsign> <name>",
//  dssq.gds StationIdent): what the flight does with such a packet.
//
//  The ground radiates its plain-text identification through the same radio path as
//  the CLTUs (E22 transparent packet / SX126x packet). The flight must take it for what
//  it is - no CLTU - and drop it without side effects beyond the RF cltu_bad counter
//  (Flight::process_tc, NO_CLTU branch: no EVR, no FDIR flag, no FARM-1 change). Checked
//  here with the exact flight code of the receive chain: E22StreamParser (radio.cpp)
//  -> tc_accept() (datalink.cpp: cltu_decode -> tc_parse -> SDLS -> FARM-1).
// =============================================================================
#include <cstring>
#include <string>
#include <vector>

#include "ccsds/crc16.h"
#include "ccsds/tc.h"
#include "drivers/radio.h"
#include "fsw/datalink.h"
#include "host_test.h"

using namespace ccsds;
using namespace vgq;

namespace {

using Bytes = std::vector<uint8_t>;

// TC (232.0-B-4) Type-AD frame, no SDLS, around one NOOP telecommand packet.
Bytes tc_ad_frame(uint8_t ns) {
  static const uint8_t kNoop[] = {0x10, 0xC0, 0xC0, 0x00, 0x00, 0x00, 0x01};
  const size_t len = kTcPriHdrLen + sizeof kNoop + 2;
  Bytes f(len, 0);
  put_u16(f.data(), (uint16_t)kSpacecraftId);                   // AD: bypass 0, control 0
  put_u16(f.data() + 2, (uint16_t)(len - 1));                   // VCID 0
  f[4] = ns;
  std::memcpy(f.data() + kTcPriHdrLen, kNoop, sizeof kNoop);
  put_u16(f.data() + len - 2, crc16_ccitt(f.data(), len - 2));
  return f;
}

Bytes cltu_of(const Bytes& frame) {
  Bytes c = {(uint8_t)(kCltuStart >> 8), (uint8_t)kCltuStart};
  for (size_t i = 0; i < frame.size(); i += 7) {
    uint8_t blk[7], enc[8];
    for (size_t j = 0; j < 7; ++j) blk[j] = i + j < frame.size() ? frame[i + j] : 0x55;
    bch_encode(blk, enc);
    c.insert(c.end(), enc, enc + 8);
  }
  c.insert(c.end(), kCltuTail, kCltuTail + 8);
  return c;
}

Bytes ascii(const char* s) { return Bytes(s, s + std::strlen(s)); }

Bytes cat(const Bytes& a, const Bytes& b) {
  Bytes r = a;
  r.insert(r.end(), b.begin(), b.end());
  return r;
}

// One E22 burst (octets about 1 ms apart, then the module's RSSI octet) through the flight
// parser; returns the event and fills `rx`.
RadioEvent e22_burst(E22StreamParser& p, const Bytes& payload, uint8_t rssi_octet, uint32_t& t, RxPacket& rx) {
  for (uint8_t b : payload) p.on_byte(b, t++);
  p.on_byte(rssi_octet, t++);
  int32_t value = 0;
  RadioEvent ev = p.poll(t, rx, value);                         // within the 40 ms gap: nothing yet
  if (ev != RadioEvent::NONE) return ev;
  t += kE22RxGapMs + 1;
  return p.poll(t, rx, value);
}

}  // namespace

HOST_TEST(station_id_packet_is_not_a_cltu) {
  const char* kIds[] = {"DE N0CALL DSS-Q1", "DE VK2/G4ABC/P DSS-Q1", "DE G4ABC"};
  // Every printable ASCII octet: the identification can never hold the start sequence EB 90.
  Bytes all;
  for (int c = 0x20; c <= 0x7E; ++c) all.push_back((uint8_t)c);
  bool none_high = true;
  for (uint8_t b : all) none_high = none_high && b < 0x80 && b != (uint8_t)(kCltuStart >> 8);
  CHECK(none_high, "station ID: printable ASCII never contains 0xEB (first octet of the CLTU start 0xEB90)");

  uint8_t work[kTcWorkMin];
  Farm1 farm;
  BchStats bch;
  Sdls sdls;                                                    // SDLS off (NONE), as on the bench
  // A first AD frame: V(R) 0 -> 1.
  const Bytes c0 = cltu_of(tc_ad_frame(0));
  TcOutcome o = tc_accept(c0.data(), c0.size(), work, sizeof work, bch, sdls, farm);
  const bool first = o.stage == TcStage::FARM && o.verdict == Farm1::ACCEPT && farm.vr() == 1;
  CHECK(first, "station ID: reference AD frame N(S)=0 accepted (V(R) = 1)");
  const BchStats before = bch;
  bool silent = true;
  for (const char* id : kIds) {
    const Bytes p = ascii(id);
    o = tc_accept(p.data(), p.size(), work, sizeof work, bch, sdls, farm);
    silent = silent && o.stage == TcStage::NO_CLTU && o.info_len == 0;
  }
  o = tc_accept(all.data(), all.size(), work, sizeof work, bch, sdls, farm);
  silent = silent && o.stage == TcStage::NO_CLTU && o.info_len == 0;
  CHECK(silent, "station ID packets: NO_CLTU (the flight counts cltu_bad and drops them, no EVR / FDIR)");
  CHECK(bch.codeblocks_ok == before.codeblocks_ok && bch.codeblocks_corrected == before.codeblocks_corrected &&
            bch.codeblocks_rejected == before.codeblocks_rejected,
        "station ID packets: BCH statistics untouched (no codeblock was decoded)");
  CHECK(farm.vr() == 1 && farm.state() == Farm1::S1_OPEN,
        "station ID packets: FARM-1 V(R) and state unchanged");
  const Bytes c1 = cltu_of(tc_ad_frame(1));
  o = tc_accept(c1.data(), c1.size(), work, sizeof work, bch, sdls, farm);
  CHECK(o.stage == TcStage::FARM && o.verdict == Farm1::ACCEPT && farm.vr() == 2,
        "station ID packets: the next AD frame N(S)=1 is accepted in sequence");

  // With SDLS AUTH configured nothing reaches SDLS either: anti-replay state untouched.
  uint8_t key[32];
  for (int i = 0; i < 32; ++i) key[i] = (uint8_t)i;
  Sdls auth;
  auth.configure(SDLS_SVC_AUTH, key, sizeof key, kSdlsSpiAuth);
  Farm1 farm2;
  const uint32_t sn0 = auth.last_sn();
  const Bytes p = ascii(kIds[0]);
  o = tc_accept(p.data(), p.size(), work, sizeof work, bch, auth, farm2);
  CHECK(o.stage == TcStage::NO_CLTU && auth.last_sn() == sn0 && farm2.vr() == 0 && farm2.state() == Farm1::S1_OPEN,
        "station ID packet with SDLS AUTH: NO_CLTU before SDLS, anti-replay and FARM state untouched");
}

HOST_TEST(station_id_e22_bursts) {
  // The flight's E22 receive framing: the ID alone, the ID right after a CLTU and right
  // before one (the ground E22 may pack back-to-back writes into one air packet).
  uint8_t work[kTcWorkMin];
  E22StreamParser p(VGQ_RADIO_E22_T33D);
  uint32_t t = 1000;
  RxPacket rx;
  const Bytes id = ascii("DE N0CALL DSS-Q1");
  RadioEvent ev = e22_burst(p, id, 0x9C, t, rx);               // RSSI octet 0x9C = -100 dBm
  CHECK(ev == RadioEvent::PACKET && rx.len == id.size() && std::memcmp(rx.data, id.data(), id.size()) == 0 &&
            rx.rssi_dbm == -100,
        "E22: the station ID arrives as one uplink packet (payload = the text, RSSI octet stripped)");
  Farm1 farm;
  BchStats bch;
  Sdls sdls;
  TcOutcome o = tc_accept(rx.data, rx.len, work, sizeof work, bch, sdls, farm);
  CHECK(o.stage == TcStage::NO_CLTU && farm.vr() == 0, "E22: the ID packet -> NO_CLTU");

  ev = e22_burst(p, cat(cltu_of(tc_ad_frame(0)), id), 0x9C, t, rx);
  o = tc_accept(rx.data, rx.len, work, sizeof work, bch, sdls, farm);
  CHECK(ev == RadioEvent::PACKET && o.stage == TcStage::FARM && o.verdict == Farm1::ACCEPT && farm.vr() == 1,
        "E22: CLTU + ID in one burst: the CLTU is accepted, the trailing text ignored");
  ev = e22_burst(p, cat(id, cltu_of(tc_ad_frame(1))), 0x9C, t, rx);
  o = tc_accept(rx.data, rx.len, work, sizeof work, bch, sdls, farm);
  CHECK(ev == RadioEvent::PACKET && o.stage == TcStage::FARM && o.verdict == Farm1::ACCEPT && farm.vr() == 2,
        "E22: ID + CLTU in one burst: the start sequence is found after the text, CLTU accepted");

  // A pending ambient-noise query is not satisfied by the ID ("C1 00 02" never matches 'D').
  p.noise_requested(t);
  ev = e22_burst(p, id, 0x9C, t, rx);
  int32_t value = 0;
  RadioEvent later = RadioEvent::NONE;
  for (int i = 0; i < 4 && later == RadioEvent::NONE; ++i) {   // bounded: the 300 ms timeout
    t += 100;
    later = p.poll(t, rx, value);
  }
  CHECK(ev == RadioEvent::PACKET && later == RadioEvent::NOISE && value == kRadioNaI32,
        "E22: an ID during a noise query is a packet; the query ends as n/a (as for any uplink)");
}
