// =============================================================================
//  USLP framing mode (CCSDS 732.1-B-2): flight framer and TC parser checks, and
//  the vectors ground/tests/test_uslp.py decodes with the independent Python
//  stack:
//
//    <out>/uslp_cadus.bin       USLP CADUs: VC0/VC1/VC3 data + VCID 63 OID frames
//    <out>/uslp_packets.txt     "<vc> <hex packet>" for every packet queued
//    <out>/uslp_sec_cadus.bin   the same through a dummy FrameSecurity (14 + 16
//    <out>/uslp_sec_packets.txt octets; refuses every 5th frame -> rollback path)
//    <out>/tm_sec_cadus.bin     TM-mode frames through the same dummy security
//    <out>/tm_sec_packets.txt
//    <out>/uslp_vectors.txt     "<name> <hex> [k=v,...]": USLP TC frames, a CLTU
//
//  The dummy security is NOT cryptography: it writes a recognisable security
//  header (D5 D5 | aad_len | data_len u16 | sn u32 | A5 x5), XORs the protected
//  region with 0x3C and writes a trailer (CRC-16 over AAD + header + region |
//  5A x14), so the ground test proves it unwraps exactly the right octets.
// =============================================================================
#include <cstring>
#include <string>
#include <vector>

#include "ccsds/crc16.h"
#include "ccsds/tc.h"
#include "ccsds/tm.h"
#include "ccsds/uslp.h"
#include "host_test.h"

using namespace ccsds;
using host_test::hex;

namespace {

const char kIdent[] = "DE N0CALL VGQ-1 ";            // what the integrator passes
const size_t kIdentLen = sizeof(kIdent) - 1;

uint32_t g_prng = 0x2545F491;                         // local: leaves host_test::rnd() alone
uint32_t prng() { g_prng ^= g_prng << 13; g_prng ^= g_prng >> 17; g_prng ^= g_prng << 5; return g_prng; }

class DummySecurity : public FrameSecurity {
 public:
  size_t header_len() const override { return 14; }
  size_t trailer_len() const override { return 16; }
  bool protect(uint8_t* f, size_t aad_len, size_t data_len) override {
    ++calls;
    if (refuse || (refuse_every && calls % refuse_every == 0)) { ++refusals; return false; }
    last_aad = aad_len;
    last_data = data_len;
    uint8_t* sh = f + aad_len;
    put_u16(sh, 0xD5D5);
    sh[2] = (uint8_t)aad_len;
    put_u16(sh + 3, (uint16_t)data_len);
    put_u32(sh + 5, ++sn);
    std::memset(sh + 9, 0xA5, 5);
    for (size_t i = 0; i < data_len; ++i) sh[14 + i] ^= 0x3C;
    uint8_t* tr = sh + 14 + data_len;
    put_u16(tr, crc16_ccitt(f, aad_len + 14 + data_len));
    std::memset(tr + 2, 0x5A, 14);
    return true;
  }
  bool refuse = false;
  uint32_t refuse_every = 0, calls = 0, refusals = 0, sn = 0;
  size_t last_aad = 0, last_data = 0;
};

// Runs the flight multiplexer the way flight.cpp does (VC0 > VC1 > VC3 > OID)
// and writes the CADUs plus the packets queued per VC. A frame refused by the
// security layer is replaced by an OID frame (flight: VC0-3 held, OID goes out).
int run_mux(Framing mode, DummySecurity* sec, const std::string& cadus, const std::string& pkts,
            int& oid_frames) {
  TmFramer fr;
  fr.init(kSpacecraftId, mode);
  static VcStream vc[4];
  for (auto& s : vc) s.reset();
  uint16_t idle_seq = 0, seq[0x800] = {0};
  FILE* fc = std::fopen(cadus.c_str(), "wb");
  FILE* fp = std::fopen(pkts.c_str(), "w");
  if (!fc || !fp) { if (fc) std::fclose(fc); if (fp) std::fclose(fp); return -1; }
  int frames = 0;
  oid_frames = 0;
  auto emit = [&](int step) {
    uint8_t frame[kTmFrameLen], cadu[kCaduLen];
    Clcw clcw;
    clcw.report_value = (uint8_t)step;
    int v = -1;
    for (int c : {0, 1, 3}) if (vc[c].has_real_data()) { v = c; break; }
    if (v < 0 || !fr.build_frame((uint8_t)v, vc[v], clcw, idle_seq, frame, sec)) {
      fr.build_oid_frame(clcw, frame, (const uint8_t*)kIdent, kIdentLen);
      ++oid_frames;
    }
    build_cadu(frame, cadu);
    std::fwrite(cadu, 1, kCaduLen, fc);
    ++frames;
  };
  static const uint8_t kDataVcs[3] = {0, 1, 3};
  for (int step = 0; step < 100; ++step) {
    const int burst = (int)(prng() % 3);
    for (int k = 0; k < burst; ++k) {
      const uint8_t v = kDataVcs[prng() % 3];
      const uint16_t apid = (uint16_t)(v == 3 ? 0x030 : 0x010 + v * 0x10 + prng() % 3);
      uint8_t payload[160], pkt[200];
      const size_t plen = 1 + prng() % 150;
      for (size_t i = 0; i < plen; ++i) payload[i] = (uint8_t)prng();
      const CucTime t{(uint32_t)(5000 + step), (uint16_t)(step * 331)};
      const size_t n = build_tm_packet(pkt, sizeof pkt, apid, seq[apid], t, payload, plen);
      if (vc[v].push(pkt, n)) {
        seq[apid] = (uint16_t)((seq[apid] + 1) & 0x3FFF);
        std::fprintf(fp, "%d %s\n", v, hex(pkt, n).c_str());
      }
    }
    emit(step);
  }
  for (int guard = 0; guard < 200 && (vc[0].has_real_data() || vc[1].has_real_data() ||
                                      vc[3].has_real_data()); ++guard)
    emit(100 + guard);
  std::fclose(fc);
  std::fclose(fp);
  return frames;
}

// Builds a USLP TC frame with the flight header packer (the ground builds the
// same frames independently in dssq/ccsds/tc.py; the test compares the bytes).
size_t uslp_tc(uint8_t* out, const uint8_t* data, size_t n, uint8_t seq, bool bypass, bool pcc,
               uint8_t vcid = 0, uint8_t rule = kUslpRuleNoSeg, int upid = -1, bool dest = true,
               uint8_t vcf_len = 0xFF, uint16_t scid = kSpacecraftId) {
  UslpHeader h;
  h.scid = scid;
  h.dest = dest;
  h.vcid = vcid;
  h.bypass = bypass;
  h.pcc = pcc;
  h.vcf_len = vcf_len == 0xFF ? (bypass ? 0 : 1) : vcf_len;
  h.vcf_count = seq;
  h.frame_len = (uint16_t)(kUslpFixedHdrLen + h.vcf_len + kUslpTcTfdfHdrLen + n + 2);
  const size_t hl = uslp_put_header(out, h);
  out[hl] = uslp_tfdf_byte(rule, (uint8_t)(upid >= 0 ? upid : (pcc ? kUslpUpidCop1 : kUslpUpidPackets)));
  std::memcpy(out + hl + 1, data, n);
  put_u16(out + h.frame_len - 2, crc16_ccitt(out, h.frame_len - 2));
  return h.frame_len;
}

void refcrc(uint8_t* f, size_t len) { put_u16(f + len - 2, crc16_ccitt(f, len - 2)); }

}  // namespace

HOST_TEST(uslp_header_fields) {
  UslpHeader h, g;
  bool rt = true;
  for (int i = 0; i < 200; ++i) {
    h.scid = (uint16_t)prng(); h.dest = prng() & 1; h.vcid = prng() & 0x3F; h.map_id = prng() & 0xF;
    h.frame_len = (uint16_t)(8 + prng() % 1000); h.bypass = prng() & 1; h.pcc = prng() & 1;
    h.ocf = prng() & 1; h.vcf_len = prng() % 5; h.vcf_count = prng() & (h.vcf_len >= 4 ? 0xFFFFFFFFu
                                                     : ((1u << (8 * h.vcf_len)) - 1));
    uint8_t b[16];
    const size_t n = uslp_put_header(b, h);
    rt &= n == 7u + h.vcf_len && uslp_get_header(b, n, g) == n && g.scid == h.scid &&
          g.dest == h.dest && g.vcid == h.vcid && g.map_id == h.map_id && !g.truncated &&
          g.frame_len == h.frame_len && g.bypass == h.bypass && g.pcc == h.pcc && g.ocf == h.ocf &&
          g.vcf_len == h.vcf_len && g.vcf_count == h.vcf_count;
  }
  CHECK(rt, "USLP TFPH pack/unpack round trip (200 random headers)");
  uint8_t t[8] = {0xC0, 0x0A, 0x70, 0x01, 0, 9, 0, 0};       // EoFPH = 1
  CHECK(uslp_get_header(t, 8, g) == 0 && g.truncated, "USLP: truncated TFPH reported, not parsed");

  TmFramer tm, us;
  tm.init(kSpacecraftId);
  us.init(kSpacecraftId, FRAMING_USLP);
  DummySecurity d;
  CHECK(tm.data_len() == 188 && us.data_len() == 183 && tm.data_len(&d) == 158 &&
        us.data_len(&d) == 153, "data field: TM 188 / USLP 183, with a 14+16 SDLS SA 158 / 153");
}

HOST_TEST(uslp_tm_frames) {
  TmFramer fr;
  fr.init(kSpacecraftId, FRAMING_USLP);
  static VcStream s;
  s.reset();
  uint8_t pkt[64], frame[kTmFrameLen];
  const uint8_t pl[4] = {1, 2, 3, 4};
  const size_t n = build_tm_packet(pkt, sizeof pkt, 0x010, 0, CucTime{1, 2}, pl, 4);
  s.push(pkt, n);
  Clcw clcw;
  clcw.report_value = 9;
  uint16_t idle_seq = 0;
  CHECK(fr.build_frame(0, s, clcw, idle_seq, frame), "USLP data frame built");
  CHECK(frame[0] == 0xC0 && frame[1] == 0x0A && frame[2] == 0x70 && frame[3] == 0x00,
        "USLP TFPH: TFVN 1100, SCID 0x00A7, source flag 0, VCID 0, MAP 0, EoFPH 0");
  CHECK(get_u16(frame + 4) == 199 && frame[6] == 0x09 && frame[7] == 0,
        "USLP TFPH: frame length 199, bypass 0, PCC 0, OCF 1, VCF count length 1, count 0");
  CHECK(frame[8] == 0x00 && get_u16(frame + 9) == 0 && std::memcmp(frame + 11, pkt, n) == 0,
        "USLP TFDF header: rule 000, UPID 0, FHP 0; packet at TFDZ offset 0");
  CHECK(get_u32(frame + 194) == clcw.pack() && get_u16(frame + 198) == crc16_ccitt(frame, 198),
        "USLP frame: CLCW in the OCF, FECF CRC-16");
  CHECK(fr.last_count() == 0 && fr.last_vcid() == 0, "last_count() = VCF count of the frame");

  // A second frame on VC0 that holds only the rest of the idle packet: FHP 0xFFFF.
  static VcStream big;
  big.reset();
  uint8_t p2[200], body[180];
  std::memset(body, 0x42, sizeof body);
  const size_t m = build_tm_packet(p2, sizeof p2, 0x011, 1, CucTime{3, 4}, body, sizeof body);  // 192
  big.push(p2, m);
  fr.build_frame(0, big, clcw, idle_seq, frame);
  fr.build_frame(0, big, clcw, idle_seq, frame);
  CHECK(frame[7] == 2 && get_u16(frame + 9) == 9 && fr.last_count() == 2,
        "USLP: VCF count advances per VC, FHP = offset of the idle packet after a spanning packet");

  fr.build_oid_frame(clcw, frame, (const uint8_t*)kIdent, kIdentLen);
  CHECK(frame[2] == 0x77 && frame[3] == 0xE0 && frame[7] == 0 && frame[8] == 0x1F &&
        get_u16(frame + 9) == 0xFFFF && std::memcmp(frame + 11, kIdent, kIdentLen) == 0 &&
        frame[11 + 183 - 1] == (uint8_t)kIdent[(183 - 1) % kIdentLen] && fr.last_vcid() == 63,
        "USLP OID: VCID 63, rule 000, UPID 31, FHP 0xFFFF, TFDZ = repeating identification text");

  TmFramer tm;
  tm.init(kSpacecraftId);
  tm.build_oid_frame(clcw, frame, (const uint8_t*)kIdent, kIdentLen);
  CHECK(get_u16(frame) == ((kSpacecraftId << 4) | (7 << 1) | 1) && (get_u16(frame + 4) & 0x7FF) == 0x7FE &&
        std::memcmp(frame + 6, kIdent, kIdentLen) == 0, "TM OID: VC7, FHP 0x7FE, identification text");
  tm.build_oid_frame(clcw, frame);
  bool fill = true;
  for (size_t i = 6; i < 194; ++i) fill &= frame[i] == 0x55;
  CHECK(fill && frame[2] == 1 && tm.last_count() == 1, "TM OID without a pattern: 0x55 fill, MCFC 1");
}

HOST_TEST(uslp_security_rollback) {
  // Two identical framers/streams; A's security refuses the first attempt. After
  // the refusal A must produce exactly the frame B produces: nothing consumed,
  // no counter advanced.
  for (Framing mode : {FRAMING_TM, FRAMING_USLP}) {
    TmFramer a, b;
    a.init(kSpacecraftId, mode);
    b.init(kSpacecraftId, mode);
    static VcStream sa, sb;
    sa.reset();
    sb.reset();
    for (int i = 0; i < 5; ++i) {
      uint8_t pkt[120], pl[100];
      for (auto& x : pl) x = (uint8_t)prng();
      const size_t n = build_tm_packet(pkt, sizeof pkt, 0x010, (uint16_t)i, CucTime{7, 8}, pl, sizeof pl);
      sa.push(pkt, n);
      sb.push(pkt, n);
    }
    DummySecurity da, db;
    da.refuse = true;
    Clcw c;
    uint16_t ia = 0, ib = 0;
    uint8_t fa[kTmFrameLen], fb[kTmFrameLen];
    const size_t before = sa.bytes();
    const bool refused = !a.build_frame(1, sa, c, ia, fa, &da);
    CHECK(refused && sa.bytes() == before && a.mcfc() == 0, "refused frame: stream and MCFC untouched");
    da.refuse = false;
    bool same = true;
    for (int k = 0; k < 4; ++k) {
      same &= a.build_frame(1, sa, c, ia, fa, &da) && b.build_frame(1, sb, c, ib, fb, &db);
      same &= std::memcmp(fa, fb, kTmFrameLen) == 0;
    }
    CHECK(same && da.last_aad == (mode == FRAMING_USLP ? 8u : 6u) &&
          da.last_data == (mode == FRAMING_USLP ? 156u : 158u),
          mode == FRAMING_USLP ? "USLP + SDLS: rollback after a refusal; protect(aad 8, TFDF 156)"
                               : "TM + SDLS: rollback after a refusal; protect(aad 6, data 158)");
  }
}

HOST_TEST(uslp_cadu_streams) {
  int oid = 0;
  int n = run_mux(FRAMING_USLP, nullptr, out + "/uslp_cadus.bin", out + "/uslp_packets.txt", oid);
  CHECK(n >= 100 && oid > 0, "USLP CADU stream written (uslp_cadus.bin / uslp_packets.txt)");
  DummySecurity d;
  d.refuse_every = 5;
  n = run_mux(FRAMING_USLP, &d, out + "/uslp_sec_cadus.bin", out + "/uslp_sec_packets.txt", oid);
  CHECK(n >= 100 && d.refusals > 0, "USLP + dummy SDLS stream written, with refusals (rollback path)");
  DummySecurity t;
  t.refuse_every = 7;
  n = run_mux(FRAMING_TM, &t, out + "/tm_sec_cadus.bin", out + "/tm_sec_packets.txt", oid);
  CHECK(n >= 100 && t.refusals > 0, "TM + dummy SDLS stream written, with refusals (rollback path)");
}

HOST_TEST(uslp_tc_frames) {
  FILE* f = std::fopen((out + "/uslp_vectors.txt").c_str(), "w");
  if (!f) { CHECK(false, "cannot write uslp_vectors.txt"); return; }
  const uint8_t noop[] = {0x18, 0xC0, 0xC0, 0x00, 0x00, 0x00, 0x01};
  uint8_t fr[80];
  TcFrame t;

  size_t n = uslp_tc(fr, noop, sizeof noop, 5, false, false);
  std::fprintf(f, "TC_AD %s seq=5,bypass=0,control=0,vcid=0,data=%s\n", hex(fr, n).c_str(),
               hex(noop, sizeof noop).c_str());
  CHECK(n == 8 + 1 + 7 + 2 && tc_parse(fr, n, t) == TC_OK && t.format == TC_FMT_USLP &&
        t.hdr_len == 8 && t.seq == 5 && !t.bypass && !t.control && t.scid == kSpacecraftId &&
        t.upid == 0 && t.data_len == sizeof noop && std::memcmp(t.data, noop, sizeof noop) == 0,
        "USLP TC Type-AD: TFPH 8 (N(S) = VCF count), TFDF rule 111 / UPID 0, packet unwrapped");
  CHECK(fr[2] == 0x78 && fr[6] == 0x01 && fr[8] == 0xE0, "USLP TC header: destination flag 1, VCF length 1");

  // Deferred TFDF opening (SDLS order): header still in `data` until tc_open_tfdf().
  CHECK(tc_parse(fr, n, t, false) == TC_OK && !t.tfdf_open && t.data_len == sizeof noop + 1 &&
        t.data[0] == 0xE0 && tc_open_tfdf(t) == TC_OK && t.tfdf_open && t.data_len == sizeof noop &&
        tc_open_tfdf(t) == TC_OK && t.data_len == sizeof noop,
        "tc_parse(open_tfdf=false) + tc_open_tfdf(): TFDF opened once, after SDLS");

  // CLTU round trip (BCH unchanged).
  std::vector<uint8_t> cltu = {0xEB, 0x90};
  for (size_t i = 0; i < n; i += 7) {
    uint8_t blk[7], enc[8];
    for (int j = 0; j < 7; ++j) blk[j] = (i + j < n) ? fr[i + j] : 0x55;
    bch_encode(blk, enc);
    cltu.insert(cltu.end(), enc, enc + 8);
  }
  cltu.insert(cltu.end(), kCltuTail, kCltuTail + 8);
  std::fprintf(f, "TC_AD_CLTU %s\n", hex(cltu.data(), cltu.size()).c_str());
  uint8_t info[96];
  BchStats st;
  const size_t m = cltu_decode(cltu.data(), cltu.size(), info, sizeof info, st);
  CHECK(m >= n && tc_parse(info, m, t) == TC_OK && t.seq == 5 && t.data_len == sizeof noop,
        "USLP TC frame survives CLTU encode/decode (fill octets after the frame ignored)");

  n = uslp_tc(fr, noop, sizeof noop, 0, true, false, 2);
  std::fprintf(f, "TC_BD %s seq=0,bypass=1,control=0,vcid=2,data=%s\n", hex(fr, n).c_str(),
               hex(noop, sizeof noop).c_str());
  CHECK(tc_parse(fr, n, t) == TC_OK && t.hdr_len == 7 && t.bypass && !t.control && t.vcid == 2 &&
        t.data_len == sizeof noop, "USLP TC Type-BD: TFPH 7 (no VCF count), VCID 2");

  const uint8_t unlock[] = {0x00}, setvr[] = {0x82, 0x00, 42};
  n = uslp_tc(fr, unlock, 1, 0, true, true);
  std::fprintf(f, "TC_BC_UNLOCK %s seq=0,bypass=1,control=1,vcid=0,data=00\n", hex(fr, n).c_str());
  CHECK(tc_parse(fr, n, t) == TC_OK && t.bypass && t.control && t.upid == kUslpUpidCop1 &&
        t.data_len == 1 && t.data[0] == 0 && fr[6] == 0xC0 && fr[7] == 0xE1,
        "USLP TC Type-BC Unlock: bypass 1, PCC 1, UPID 1");
  n = uslp_tc(fr, setvr, 3, 0, true, true);
  std::fprintf(f, "TC_BC_SETVR %s seq=0,bypass=1,control=1,vcid=0,data=82002a\n", hex(fr, n).c_str());

  // FARM-1 on USLP frames, same verdicts as on TC frames.
  {
    Farm1 farm(10);
    uint8_t b[80];
    auto ad = [&](uint8_t s) { const size_t k = uslp_tc(b, noop, sizeof noop, s, false, false);
                               tc_parse(b, k, t); return farm.on_frame(t); };
    CHECK(ad(0) == Farm1::ACCEPT && farm.vr() == 1, "FARM/USLP: N(S)=V(R) accepted");
    CHECK(ad(0) == Farm1::DISCARD_NEG_WINDOW, "FARM/USLP: duplicate discarded");
    CHECK(ad(3) == Farm1::DISCARD_RETRANSMIT, "FARM/USLP: gap -> retransmit");
    CHECK(ad(100) == Farm1::DISCARD_LOCKOUT && farm.state() == Farm1::S3_LOCKOUT, "FARM/USLP: lockout");
    size_t k = uslp_tc(b, unlock, 1, 0, true, true);
    CHECK(tc_parse(b, k, t) == TC_OK && farm.on_frame(t) == Farm1::CONTROL_UNLOCK, "FARM/USLP: Unlock (PCC/UPID 1)");
    k = uslp_tc(b, setvr, 3, 0, true, true);
    CHECK(tc_parse(b, k, t) == TC_OK && farm.on_frame(t) == Farm1::CONTROL_SET_VR && farm.vr() == 42,
          "FARM/USLP: Set V(R)=42");
    CHECK(ad(42) == Farm1::ACCEPT && farm.vr() == 43, "FARM/USLP: N(S)=42 accepted after Set V(R)");
  }

  // Rejections.
  n = uslp_tc(fr, noop, sizeof noop, 1, false, false, 0, kUslpRuleNoSeg, -1, false);
  CHECK(tc_parse(fr, n, t) == TC_BAD_HEADER, "USLP TC: source flag (dest = 0) rejected");
  n = uslp_tc(fr, noop, sizeof noop, 1, false, false, 0, kUslpRuleNoSeg, -1, true, 0);
  CHECK(tc_parse(fr, n, t) == TC_BAD_HEADER, "USLP TC: Type-AD without a VCF count rejected");
  n = uslp_tc(fr, unlock, 1, 1, false, true);
  CHECK(tc_parse(fr, n, t) == TC_BAD_HEADER, "USLP TC: PCC on a sequence-controlled frame rejected");
  n = uslp_tc(fr, noop, sizeof noop, 1, false, false, 0, kUslpRulePackets);
  CHECK(tc_parse(fr, n, t) == TC_BAD_TFDF, "USLP TC: construction rule 000 rejected");
  n = uslp_tc(fr, noop, sizeof noop, 1, false, false, 0, kUslpRuleNoSeg, kUslpUpidCop1);
  CHECK(tc_parse(fr, n, t) == TC_BAD_TFDF, "USLP TC: UPID 1 without PCC rejected");
  n = uslp_tc(fr, noop, sizeof noop, 1, false, false, 0, kUslpRuleNoSeg, -1, true, 0xFF, 0x00A8);
  CHECK(tc_parse(fr, n, t) == TC_BAD_SCID, "USLP TC: foreign SCID rejected");
  n = uslp_tc(fr, noop, sizeof noop, 1, false, false);
  fr[3] |= 1;                                               // EoFPH -> truncated header
  CHECK(tc_parse(fr, n, t) == TC_BAD_HEADER, "USLP TC: truncated primary header rejected");
  n = uslp_tc(fr, noop, sizeof noop, 1, false, false);
  fr[n - 1] ^= 1;
  CHECK(tc_parse(fr, n, t) == TC_BAD_FECF, "USLP TC: FECF error rejected");
  n = uslp_tc(fr, noop, sizeof noop, 1, false, false);
  fr[0] = (uint8_t)(0x40 | (fr[0] & 0x0F));                 // TFVN '0100'
  CHECK(tc_parse(fr, n, t) == TC_BAD_VERSION, "unknown TFVN rejected");
  uint8_t big[64];
  std::memset(big, 0x11, sizeof big);
  n = uslp_tc(fr, big, 60, 1, false, false);                // 8 + 1 + 60 + 2 = 71 > 64
  CHECK(tc_parse(fr, n, t) == TC_BAD_LENGTH, "USLP TC: frame above the 64-octet mission limit rejected");

  // An OCF (allowed by 732.1, not sent by DSS-Q) is skipped, not taken as data.
  {
    UslpHeader h;
    h.dest = true; h.bypass = true; h.ocf = true;
    h.frame_len = (uint16_t)(7 + 1 + sizeof noop + 4 + 2);
    size_t hl = uslp_put_header(fr, h);
    fr[hl] = uslp_tfdf_byte(kUslpRuleNoSeg, kUslpUpidPackets);
    std::memcpy(fr + hl + 1, noop, sizeof noop);
    put_u32(fr + hl + 1 + sizeof noop, 0xDEADBEEF);
    refcrc(fr, h.frame_len);
    CHECK(tc_parse(fr, h.frame_len, t) == TC_OK && t.data_len == sizeof noop &&
          std::memcmp(t.data, noop, sizeof noop) == 0, "USLP TC with OCF flag: OCF skipped");
  }

  // TC 232.0-B-4 frames are still parsed as before.
  {
    const uint16_t len = (uint16_t)(kTcPriHdrLen + sizeof noop + 2);
    put_u16(fr, kSpacecraftId);
    put_u16(fr + 2, (uint16_t)(len - 1));
    fr[4] = 9;
    std::memcpy(fr + 5, noop, sizeof noop);
    refcrc(fr, len);
    CHECK(tc_parse(fr, len, t, false) == TC_OK && t.format == TC_FMT_TC && t.hdr_len == 5 &&
          t.tfdf_open && t.seq == 9 && t.data_len == sizeof noop, "TC v1 frame: format TC, header 5, open");
  }
  std::fclose(f);
}
