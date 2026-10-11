// =============================================================================
//  Host (PC) verification harness for the VGQ-1 flight protocol code.
//
//  Compiles the EXACT flight sources from spacecraft/vgq1_flight/sketch/src/ccsds
//  with a desktop compiler, runs self-checks, and writes test vectors that the
//  independent ground decoder (Python, ground/dssq) must decode bit-exactly:
//
//    <outdir>/cadus.bin       stream of 236-octet CADUs produced by the flight mux
//    <outdir>/packets.txt     "<vc> <hex packet>" for every real packet queued
//    <outdir>/rs_vector.txt   one 232-octet dual-basis codeblock (hex)
//    <outdir>/cltu.txt        CLTU produced by the flight BCH encoder (hex)
//    <outdir>/sdls_frame.txt  an SDLS AUTH TC frame built with the flight HMAC code
//  and then runs every check registered by tools/host_test/test_*.cpp (telemetry
//  and command vectors: test_integration.cpp; CFDP, crypto, USLP, Rice, spectral,
//  radio and autonomy vectors: their own files).
//
//  Build & run:   make -C tools/host_test   (or see Makefile)
// =============================================================================
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "../../spacecraft/vgq1_flight/sketch/src/ccsds/ccsds_types.h"
#include "../../spacecraft/vgq1_flight/sketch/src/ccsds/crc16.h"
#include "../../spacecraft/vgq1_flight/sketch/src/ccsds/randomizer.h"
#include "../../spacecraft/vgq1_flight/sketch/src/ccsds/reed_solomon.h"
#include "../../spacecraft/vgq1_flight/sketch/src/ccsds/sdls.h"
#include "../../spacecraft/vgq1_flight/sketch/src/ccsds/sha256.h"
#include "../../spacecraft/vgq1_flight/sketch/src/ccsds/tc.h"
#include "../../spacecraft/vgq1_flight/sketch/src/ccsds/tm.h"
#include "../../spacecraft/vgq1_flight/sketch/src/fsw/attitude.h"
#include "../../spacecraft/vgq1_flight/sketch/src/fsw/ssr.h"
#include <cmath>

#include "host_test.h"

using namespace ccsds;

namespace host_test {
int& failures() { static int n = 0; return n; }
std::string hex(const uint8_t* p, size_t n) {
  static const char* d = "0123456789abcdef";
  std::string s;
  for (size_t i = 0; i < n; ++i) { s += d[p[i] >> 4]; s += d[p[i] & 15]; }
  return s;
}
size_t unhex(const char* s, uint8_t* out, size_t cap) {
  size_t n = 0;
  int hi = -1;
  for (; *s && n < cap; ++s) {
    const char c = *s;
    int v = (c >= '0' && c <= '9') ? c - '0' : (c >= 'a' && c <= 'f') ? c - 'a' + 10
          : (c >= 'A' && c <= 'F') ? c - 'A' + 10 : -1;
    if (v < 0) continue;
    if (hi < 0) hi = v; else { out[n++] = (uint8_t)(hi << 4 | v); hi = -1; }
  }
  return n;
}
uint32_t rnd() {
  static uint32_t g = 0x12345678;
  g ^= g << 13; g ^= g >> 17; g ^= g << 5;
  return g;
}
namespace {
struct Entry { const char* name; TestFn fn; };
std::vector<Entry>& registry() { static std::vector<Entry> r; return r; }
}  // namespace
Registrar::Registrar(const char* name, TestFn fn) { registry().push_back({name, fn}); }
void run_all(const std::string& out) {
  for (const Entry& e : registry()) {
    std::printf("---- %s\n", e.name);
    e.fn(out);
  }
}
}  // namespace host_test

using host_test::hex;
using host_test::rnd;

int main(int argc, char** argv) {
  const std::string out = argc > 1 ? argv[1] : ".";
  ReedSolomon::init();

  // ---- CRC -------------------------------------------------------------------
  const uint8_t chk[] = {'1','2','3','4','5','6','7','8','9'};
  CHECK(crc16_ccitt(chk, 9) == 0x29B1, "CRC-16/CCITT-FALSE check value 0x29B1");

  // ---- Randomizer --------------------------------------------------------------
  const uint8_t prefix[5] = {0xFF, 0x48, 0x0E, 0xC0, 0x9A};
  uint8_t zeros[5] = {0, 0, 0, 0, 0};
  randomize(zeros, sizeof zeros);              // randomizing zeros yields the sequence itself
  const bool rok = std::memcmp(zeros, prefix, 5) == 0;
  CHECK(rok, "randomizer prefix FF 48 0E C0 9A (CCSDS 131.0-B)");

  // ---- Dual basis tables -------------------------------------------------------
  CHECK(ReedSolomon::toDual(0x01) == 0x7B && ReedSolomon::toDual(0x02) == 0xAF &&
        ReedSolomon::toDual(0x80) == 0x8D, "dual-basis table rows (Annex F)");
  bool inv = true;
  for (int i = 0; i < 256; ++i) inv &= ReedSolomon::fromDual(ReedSolomon::toDual((uint8_t)i)) == i;
  CHECK(inv, "dual-basis T and T^-1 are inverse permutations");

  // ---- RS vector -----------------------------------------------------------------
  {
    uint8_t cb[kCodeblockLen];
    for (size_t i = 0; i < kTmFrameLen; ++i) cb[i] = (uint8_t)rnd();
    ReedSolomon::encode(cb, kTmFrameLen, cb + kTmFrameLen);
    FILE* f = std::fopen((out + "/rs_vector.txt").c_str(), "w");
    if (f) { std::fprintf(f, "%s\n", hex(cb, kCodeblockLen).c_str()); std::fclose(f); }
    uint8_t zero[kTmFrameLen] = {0}, par[32];
    ReedSolomon::encode(zero, kTmFrameLen, par);
    bool z = true;
    for (int i = 0; i < 32; ++i) z &= par[i] == 0;
    CHECK(z, "RS: all-zero frame -> all-zero parity");
  }

  // ---- BCH / CLTU ------------------------------------------------------------------
  {
    BchStats st;
    uint8_t tail_out[16];
    size_t n = cltu_decode((const uint8_t*)"\xEB\x90\xC5\xC5\xC5\xC5\xC5\xC5\xC5\x79", 10, tail_out, 16, st);
    CHECK(n == 0 && st.codeblocks_rejected == 1, "CLTU tail sequence is rejected as a codeblock");

    // Single-bit error correction in every one of the 63 code bit positions.
    uint8_t info[7] = {0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE}, cb[8];
    bch_encode(info, cb);
    bool all = true;
    for (int p = 0; p < 63; ++p) {
      uint8_t buf[2 + 8 + 8];
      buf[0] = 0xEB; buf[1] = 0x90;
      std::memcpy(buf + 2, cb, 8);
      if (p < 56) buf[2 + p / 8] ^= (uint8_t)(0x80 >> (p % 8));
      else        buf[2 + 7] ^= (uint8_t)(0x80 >> (p - 56));
      std::memcpy(buf + 10, kCltuTail, 8);
      uint8_t o[16];
      BchStats s2;
      size_t m = cltu_decode(buf, sizeof(buf), o, sizeof(o), s2);
      all &= (m == 7 && std::memcmp(o, info, 7) == 0 && s2.codeblocks_corrected == 1);
    }
    CHECK(all, "BCH(63,56) corrects any single bit error (63 positions)");

    // A complete TC frame -> CLTU, written for the Python cross-check.
    uint8_t frame[16] = {0};
    const uint8_t payload[] = {0x18, 0xC0, 0xC0, 0x00, 0x00, 0x00, 0x01};  // NOOP packet
    const uint16_t len = (uint16_t)(kTcPriHdrLen + sizeof(payload) + 2);
    put_u16(frame, (uint16_t)(kSpacecraftId & 0x3FF));
    put_u16(frame + 2, (uint16_t)((0u << 10) | (len - 1)));
    frame[4] = 0;
    std::memcpy(frame + 5, payload, sizeof(payload));
    put_u16(frame + len - 2, crc16_ccitt(frame, len - 2));
    std::vector<uint8_t> cltu = {0xEB, 0x90};
    for (size_t i = 0; i < len; i += 7) {
      uint8_t blk[7], enc[8];
      for (int j = 0; j < 7; ++j) blk[j] = (i + j < len) ? frame[i + j] : 0x55;
      bch_encode(blk, enc);
      cltu.insert(cltu.end(), enc, enc + 8);
    }
    cltu.insert(cltu.end(), kCltuTail, kCltuTail + 8);
    FILE* f = std::fopen((out + "/cltu.txt").c_str(), "w");
    if (f) { std::fprintf(f, "%s\n", hex(cltu.data(), cltu.size()).c_str()); std::fclose(f); }

    uint8_t dec[64];
    BchStats s3;
    size_t m = cltu_decode(cltu.data(), cltu.size(), dec, sizeof(dec), s3);
    TcFrame tf;
    CHECK(m >= len && tc_parse(dec, m, tf) == TC_OK && tf.data_len == sizeof(payload),
          "TC frame survives CLTU encode/decode and FECF check");
  }

  // ---- FARM-1 ------------------------------------------------------------------------
  {
    Farm1 farm(10);
    TcFrame f;
    uint8_t d[3] = {0};
    f.data = d; f.data_len = 1;
    f.bypass = false; f.control = false;
    f.seq = 0; CHECK(farm.on_frame(f) == Farm1::ACCEPT && farm.vr() == 1, "FARM: N(S)=V(R) accepted");
    f.seq = 0; CHECK(farm.on_frame(f) == Farm1::DISCARD_NEG_WINDOW, "FARM: duplicate discarded");
    f.seq = 3; CHECK(farm.on_frame(f) == Farm1::DISCARD_RETRANSMIT, "FARM: gap -> retransmit flag");
    Clcw c; farm.fill_clcw(c);
    CHECK(c.retransmit && c.report_value == 1, "CLCW reports retransmit and V(R)=1");
    f.seq = 100; CHECK(farm.on_frame(f) == Farm1::DISCARD_LOCKOUT && farm.state() == Farm1::S3_LOCKOUT,
                       "FARM: out-of-window -> lockout");
    f.bypass = true; f.control = true; d[0] = 0x00; f.data_len = 1;
    CHECK(farm.on_frame(f) == Farm1::CONTROL_UNLOCK && farm.state() == Farm1::S1_OPEN, "FARM: Unlock");
    d[0] = 0x82; d[1] = 0x00; d[2] = 42; f.data_len = 3;
    CHECK(farm.on_frame(f) == Farm1::CONTROL_SET_VR && farm.vr() == 42, "FARM: Set V(R)=42");
  }

  // ---- FARM-1 state table edges (CCSDS 232.1-B §6; W = 10 -> PW = NW = 5) --------------
  {
    uint8_t d[3] = {0};
    auto ad = [&](Farm1& fm, uint8_t ns) {
      TcFrame f;
      f.data = d; f.data_len = 1; f.bypass = false; f.control = false; f.seq = ns;
      return fm.on_frame(f);
    };
    auto unlock = [&](Farm1& fm) {
      TcFrame f;
      d[0] = 0x00;
      f.data = d; f.data_len = 1; f.bypass = true; f.control = true; f.seq = 0;
      return fm.on_frame(f);
    };
    auto set_vr = [&](Farm1& fm, uint8_t vr) {
      TcFrame f;
      d[0] = 0x82; d[1] = 0x00; d[2] = vr;
      f.data = d; f.data_len = 3; f.bypass = true; f.control = true; f.seq = 0;
      return fm.on_frame(f);
    };
    auto bd = [&](Farm1& fm) {
      TcFrame f;
      f.data = d; f.data_len = 1; f.bypass = true; f.control = false; f.seq = 0;
      return fm.on_frame(f);
    };
    Clcw c;
    Farm1 a(10);
    CHECK(ad(a, 0) == Farm1::ACCEPT && a.vr() == 1, "FARM table: N(S)=V(R)=0 accepted");
    CHECK(ad(a, 5) == Farm1::DISCARD_RETRANSMIT, "FARM table: N(S)=V(R)+PW-1 -> retransmit (positive window)");
    a.fill_clcw(c);
    CHECK(c.retransmit && !c.lockout, "FARM table: CLCW Retransmit set by the gap");
    CHECK(ad(a, 6) == Farm1::DISCARD_LOCKOUT && a.state() == Farm1::S3_LOCKOUT,
          "FARM table: N(S)=V(R)+PW -> lockout (outside the positive window)");
    CHECK(set_vr(a, 7) == Farm1::CONTROL_SET_VR && a.vr() == 1 && a.state() == Farm1::S3_LOCKOUT,
          "FARM table: Set V(R) has no effect in Lockout");
    CHECK(unlock(a) == Farm1::CONTROL_UNLOCK && a.state() == Farm1::S1_OPEN, "FARM table: Unlock leaves Lockout");
    a.fill_clcw(c);
    CHECK(!c.retransmit && !c.lockout && !c.wait, "FARM table: Unlock clears Lockout, Wait and Retransmit");

    Farm1 b(10);
    CHECK(set_vr(b, 10) == Farm1::CONTROL_SET_VR && b.vr() == 10, "FARM table: Set V(R)=10 in Open");
    CHECK(ad(b, 5) == Farm1::DISCARD_NEG_WINDOW && b.state() == Farm1::S1_OPEN,
          "FARM table: N(S)=V(R)-NW -> discarded (negative window)");
    CHECK(ad(b, 4) == Farm1::DISCARD_LOCKOUT && b.state() == Farm1::S3_LOCKOUT,
          "FARM table: N(S)=V(R)-NW-1 -> lockout");

    Farm1 g(10);
    (void)set_vr(g, 10);
    CHECK(ad(g, 12) == Farm1::DISCARD_RETRANSMIT, "FARM table: gap at V(R)=10");
    CHECK(set_vr(g, 0) == Farm1::CONTROL_SET_VR && g.vr() == 0, "FARM table: Set V(R)=0 in Open");
    g.fill_clcw(c);
    CHECK(!c.retransmit, "FARM table: Set V(R) clears Retransmit");
    // Modulo-256 wrap: V(R)=255 accepts N(S)=255 and moves to 0.
    (void)set_vr(g, 255);
    CHECK(ad(g, 255) == Farm1::ACCEPT && g.vr() == 0 && ad(g, 254) == Farm1::DISCARD_NEG_WINDOW,
          "FARM table: N(S)=255 accepted, V(R) wraps to 0, 254 is a duplicate");

    // FARM-B counter: every Type-B frame (BD and BC), modulo 4, reported in the CLCW.
    Farm1 h(10);
    h.fill_clcw(c);
    bool fb = c.farm_b_counter == 0;
    for (unsigned k = 1; k <= 5; ++k) {
      CHECK(bd(h) == Farm1::ACCEPT_BYPASS, "FARM table: Type-BD accepted (bypass)");
      h.fill_clcw(c);
      fb = fb && c.farm_b_counter == (k & 3u);
    }
    (void)unlock(h);                                   // a BC frame counts too
    h.fill_clcw(c);
    CHECK(fb && c.farm_b_counter == (6u & 3u) && h.vr() == 0, "FARM table: FARM-B counter = Type-B frames mod 4");
  }

  // ---- TC frame flags (C07): Bypass 0 / Control 1 is not a defined frame type -----------
  {
    auto frame = [](bool bypass, bool control, uint8_t* fr) {
      const uint8_t payload[] = {0x18, 0xC0, 0xC0, 0x00, 0x00, 0x00, 0x01};
      const uint16_t len = (uint16_t)(kTcPriHdrLen + sizeof(payload) + 2);
      put_u16(fr, (uint16_t)((bypass ? 0x2000u : 0u) | (control ? 0x1000u : 0u) | (kSpacecraftId & 0x3FF)));
      put_u16(fr + 2, (uint16_t)(len - 1));
      fr[4] = 0;
      std::memcpy(fr + 5, payload, sizeof(payload));
      put_u16(fr + len - 2, crc16_ccitt(fr, len - 2));
      return (size_t)len;
    };
    uint8_t fr[16];
    TcFrame tf;
    size_t n = frame(false, true, fr);
    CHECK(tc_parse(fr, n, tf) == TC_BAD_HEADER, "TC frame Bypass=0 Control=1 (no such type) -> BAD_HEADER");
    n = frame(false, false, fr);
    CHECK(tc_parse(fr, n, tf) == TC_OK && !tf.bypass && !tf.control, "TC frame Type-AD (00) accepted");
    n = frame(true, false, fr);
    CHECK(tc_parse(fr, n, tf) == TC_OK && tf.bypass && !tf.control, "TC frame Type-BD (10) accepted");
    n = frame(true, true, fr);
    CHECK(tc_parse(fr, n, tf) == TC_OK && tf.bypass && tf.control, "TC frame Type-BC (11) accepted");
  }

  // ---- Multiplexer -> frames -> CADUs ---------------------------------------------------
  {
    TmFramer framer;
    framer.init(kSpacecraftId);
    VcStream vc[3];
    for (auto& s : vc) s.reset();
    uint16_t idle_seq = 0;
    uint16_t seq[0x800] = {0};
    FILE* fc = std::fopen((out + "/cadus.bin").c_str(), "wb");
    FILE* fp = std::fopen((out + "/packets.txt").c_str(), "w");
    if (!fc || !fp) { std::printf("cannot write vectors to %s\n", out.c_str()); return 2; }

    int frames = 0;
    for (int step = 0; step < 120; ++step) {
      // Produce a few packets with random sizes on VC0 / VC1.
      const int burst = (int)(rnd() % 3);
      for (int k = 0; k < burst; ++k) {
        const uint8_t v = (uint8_t)(rnd() % 2);
        const uint16_t apid = v == 0 ? (uint16_t)(0x010 + rnd() % 3) : (uint16_t)(0x020 + rnd() % 4);
        uint8_t payload[160];
        const size_t plen = 1 + rnd() % 150;
        for (size_t i = 0; i < plen; ++i) payload[i] = (uint8_t)rnd();
        uint8_t pkt[200];
        CucTime t{(uint32_t)(1000 + step), (uint16_t)(step * 997)};
        const size_t n = build_tm_packet(pkt, sizeof(pkt), apid, seq[apid], t, payload, plen);
        if (vc[v].push(pkt, n)) {
          seq[apid] = (uint16_t)((seq[apid] + 1) & 0x3FFF);
          std::fprintf(fp, "%d %s\n", v, hex(pkt, n).c_str());
        }
      }
      // Emit one frame: priority VC0 > VC1, else OID.
      uint8_t frame[kTmFrameLen], cadu[kCaduLen];
      Clcw clcw; clcw.report_value = (uint8_t)step;
      if (vc[0].has_real_data())      framer.build_frame(0, vc[0], clcw, idle_seq, frame);
      else if (vc[1].has_real_data()) framer.build_frame(1, vc[1], clcw, idle_seq, frame);
      else                             framer.build_oid_frame(clcw, frame);
      build_cadu(frame, cadu);
      std::fwrite(cadu, 1, kCaduLen, fc);
      ++frames;
    }
    // Flush everything that is still queued.
    for (int guard = 0; guard < 64 && (vc[0].has_real_data() || vc[1].has_real_data()); ++guard) {
      uint8_t frame[kTmFrameLen], cadu[kCaduLen];
      Clcw clcw;
      const uint8_t v = vc[0].has_real_data() ? 0 : 1;
      framer.build_frame(v, vc[v], clcw, idle_seq, frame);
      build_cadu(frame, cadu);
      std::fwrite(cadu, 1, kCaduLen, fc);
      ++frames;
    }
    std::fclose(fc);
    std::fclose(fp);
    CHECK(frames >= 120, "multiplexer produced CADU stream (cadus.bin / packets.txt)");
  }

  // ---- Solid-state recorder ------------------------------------------------------------------
  {
    static vgq::Ssr ssr;
    CHECK(ssr.begin(16384, 16384, 1024) == 16384, "SSR heap allocation (16 KiB requested)");
    uint8_t p[100], o[128];
    for (int i = 0; i < 400; ++i) { std::memset(p, i & 0xFF, sizeof(p)); p[0] = (uint8_t)i; ssr.record(p, 100); }
    CHECK(ssr.dropped() > 0 && ssr.fill_permille() > 950, "SSR overwrites oldest when full");
    CHECK(ssr.start_playback() && ssr.playing(), "SSR playback starts (no export running)");
    int cnt = 0, last = -1;
    bool ordered = true;
    while (uint16_t n = ssr.next_playback(o, sizeof(o))) {
      if (n != 100) ordered = false;
      if (last >= 0 && o[0] != (uint8_t)(last + 1)) ordered = false;
      last = o[0]; ++cnt;
    }
    CHECK(ordered && cnt == (int)ssr.packets() && last == (399 & 0xFF), "SSR playback oldest-first, complete");
  }

  // ---- Attitude ---------------------------------------------------------------------------------
  {
    // True attitude: q = (cos(th/2), u sin(th/2)), arbitrary axis.
    const float th = 0.9f; vgq::Vec3 u = vgq::v_unit({0.3f, -0.5f, 0.8f});
    vgq::Quat qt{std::cos(th / 2), u.x * std::sin(th / 2), u.y * std::sin(th / 2), u.z * std::sin(th / 2)};
    auto rot = [&](const vgq::Quat& q, const vgq::Vec3& v) {   // R(q) v
      const float w = q.w, x = q.x, y = q.y, z = q.z;
      return vgq::Vec3{(1 - 2 * (y * y + z * z)) * v.x + 2 * (x * y - w * z) * v.y + 2 * (x * z + w * y) * v.z,
                       2 * (x * y + w * z) * v.x + (1 - 2 * (x * x + z * z)) * v.y + 2 * (y * z - w * x) * v.z,
                       2 * (x * z - w * y) * v.x + 2 * (y * z + w * x) * v.y + (1 - 2 * (x * x + y * y)) * v.z};
    };
    const float I = 60, D = -1.5f, d2r = 3.14159265f / 180;
    vgq::Vec3 up_ned{0, 0, -1}, b_ned{std::cos(I * d2r) * std::cos(D * d2r), std::cos(I * d2r) * std::sin(D * d2r), std::sin(I * d2r)};
    vgq::Vec3 acc_b = rot(qt, up_ned), mag_b = rot(qt, {b_ned.x * 48.0f, b_ned.y * 48.0f, b_ned.z * 48.0f});
    vgq::Quat qe;
    const bool ok = vgq::triad_ned(acc_b, mag_b, I, D, qe);
    const float dotq = std::fabs(qe.w * qt.w + qe.x * qt.x + qe.y * qt.y + qe.z * qt.z);
    CHECK(ok && dotq > 0.99999f, "TRIAD recovers the true attitude quaternion");
  }

  // ---- SHA-256 / HMAC-SHA-256 (FIPS 180-4, RFC 4231) and SDLS ------------------------
  {
    uint8_t d[32];
    Sha256 sh; sh.update((const uint8_t*)"abc", 3); sh.final(d);
    CHECK(hex(d, 32) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
          "SHA-256(\"abc\") (FIPS 180-4 example)");
    uint8_t k1[20]; std::memset(k1, 0x0b, sizeof k1);
    hmac_sha256(k1, 20, (const uint8_t*)"Hi There", 8, nullptr, 0, nullptr, 0, d);
    CHECK(hex(d, 32) == "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7",
          "HMAC-SHA-256 RFC 4231 test case 1");
    hmac_sha256((const uint8_t*)"Jefe", 4, (const uint8_t*)"what do ya want ", 16,
                (const uint8_t*)"for nothing?", 12, nullptr, 0, d);
    CHECK(hex(d, 32) == "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843",
          "HMAC-SHA-256 RFC 4231 test case 2 (multi-part input)");

    // Build an SDLS-protected TC frame exactly as the ground does, verify it in flight code.
    uint8_t key[32];
    for (int i = 0; i < 32; ++i) key[i] = (uint8_t)i;
    const uint8_t payload[] = {0x18, 0xC0, 0xC0, 0x00, 0x00, 0x00, 0x01};    // NOOP TC packet
    const uint16_t len = (uint16_t)(kTcPriHdrLen + kSdlsHdrLen + sizeof payload + kSdlsMacLen + 2);
    uint8_t fr[64] = {0};
    put_u16(fr, (uint16_t)(kSpacecraftId & 0x3FF));
    put_u16(fr + 2, (uint16_t)(len - 1));
    fr[4] = 0;
    put_u16(fr + 5, 1);                       // SPI
    put_u32(fr + 7, 7);                       // sequence number
    std::memcpy(fr + 11, payload, sizeof payload);
    uint8_t mac[32];
    hmac_sha256(key, 32, fr, 5, fr + 5, 6, fr + 11, sizeof payload, mac);
    std::memcpy(fr + 11 + sizeof payload, mac, kSdlsMacLen);
    put_u16(fr + len - 2, crc16_ccitt(fr, len - 2));
    FILE* f = std::fopen((out + "/sdls_frame.txt").c_str(), "w");
    if (f) { std::fprintf(f, "%s\n", hex(fr, len).c_str()); std::fclose(f); }

    Sdls sdls;
    sdls.init(key, 32, 1);
    TcFrame tf;
    CHECK(tc_parse(fr, len, tf) == TC_OK && sdls.process(fr, tf) == Sdls::SDLS_OK &&
          tf.data_len == sizeof payload && std::memcmp(tf.data, payload, sizeof payload) == 0,
          "SDLS: authentic frame accepted, data field unwrapped");
    TcFrame t2;
    tc_parse(fr, len, t2);
    CHECK(sdls.process(fr, t2) == Sdls::SDLS_REPLAY, "SDLS: replayed frame rejected");
    uint8_t bad[64];
    std::memcpy(bad, fr, len);
    put_u32(bad + 7, 8);                      // new SN but old MAC -> forged
    put_u16(bad + len - 2, crc16_ccitt(bad, len - 2));
    TcFrame t3;
    tc_parse(bad, len, t3);
    CHECK(sdls.process(bad, t3) == Sdls::SDLS_BAD_MAC && sdls.last_sn() == 7,
          "SDLS: forged frame rejected without advancing anti-replay state");
  }

  // ---- Magnetometer statistics in single precision (the M33 FPU is FP32-only) ----
  // Earth-like 50 uT field with a 10 nT (0.01 uT) fluctuation: the FP32 Welford
  // result must match an FP64 two-pass reference to better than 1 nT.
  {
    vgq::MagStats ms;
    std::vector<double> mags;
    uint32_t lcg = 12345;
    double sx = 0;
    for (int i = 0; i < 255; ++i) {
      lcg = lcg * 1664525u + 1013904223u;
      const double u = ((lcg >> 8) / 16777216.0 - 0.5) * 0.0346;   // uniform, sigma ~0.01 uT
      const double bx = 20.0 + u, by = -5.0 + 0.5 * u, bz = 45.5 - 0.3 * u;
      ms.add((float)bx, (float)by, (float)bz);
      mags.push_back(std::sqrt(bx * bx + by * by + bz * bz));
      sx += bx;
    }
    double mu = 0, var = 0;
    for (double m : mags) mu += m;
    mu /= mags.size();
    for (double m : mags) var += (m - mu) * (m - mu);
    const double ref = std::sqrt(var / mags.size());
    float mx, my, mz;
    ms.mean(mx, my, mz);
    CHECK(std::fabs(ms.rms_fluct() - ref) < 0.001 && std::fabs(mx - sx / 255.0) < 1e-4,
          "MagStats FP32 Welford: 10 nT fluctuation on a 50 uT field within 1 nT of FP64 reference");
  }

  // ---- Checks registered by test_*.cpp files ------------------------------------------
  host_test::run_all(out);

  const int fails = host_test::failures();
  std::printf("\n%s (%d failure%s)\n", fails ? "HOST TEST FAILED" : "HOST TEST PASSED", fails,
              fails == 1 ? "" : "s");
  return fails ? 1 : 0;
}
