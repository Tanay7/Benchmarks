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
#include "../../spacecraft/vgq1_flight/sketch/src/fsw/commands.h"
#include "../../spacecraft/vgq1_flight/sketch/src/fsw/ssr.h"
#include "../../spacecraft/vgq1_flight/sketch/src/fsw/tlm_packets.h"
#include <cmath>

using namespace ccsds;

static int g_fail = 0;
#define CHECK(cond, msg)                                              \
  do {                                                                \
    if (!(cond)) { std::printf("FAIL: %s\n", msg); ++g_fail; }        \
    else         { std::printf("ok:   %s\n", msg); }                  \
  } while (0)

static std::string hex(const uint8_t* p, size_t n) {
  static const char* d = "0123456789abcdef";
  std::string s;
  for (size_t i = 0; i < n; ++i) { s += d[p[i] >> 4]; s += d[p[i] & 15]; }
  return s;
}

// Deterministic PRNG (xorshift32) so vectors are reproducible.
static uint32_t g_rng = 0x12345678;
static uint32_t rnd() { g_rng ^= g_rng << 13; g_rng ^= g_rng >> 17; g_rng ^= g_rng << 5; return g_rng; }

int main(int argc, char** argv) {
  const std::string out = argc > 1 ? argv[1] : ".";
  ReedSolomon::init();

  // ---- CRC -------------------------------------------------------------------
  const uint8_t chk[] = {'1','2','3','4','5','6','7','8','9'};
  CHECK(crc16_ccitt(chk, 9) == 0x29B1, "CRC-16/CCITT-FALSE check value 0x29B1");

  // ---- Randomizer --------------------------------------------------------------
  const uint8_t prefix[5] = {0xFF, 0x48, 0x0E, 0xC0, 0x9A};
  bool rok = true;
  for (int i = 0; i < 5; ++i) rok &= randomizer_octet(i) == prefix[i];
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

  // ---- Telemetry packet vectors (flight packers -> ground dictionary) -----------------
  {
    FILE* f = std::fopen((out + "/tlm_vectors.txt").c_str(), "w");
    if (!f) return 2;
    CucTime t{4242, 32768};
    uint8_t pl[vgq::kMaxPayload], pkt[256];
    auto emit = [&](const char* name, uint16_t apid, size_t n, const char* expect) {
      const size_t m = build_tm_packet(pkt, sizeof(pkt), apid, 7, t, pl, n);
      std::fprintf(f, "%s %s %s\n", name, hex(pkt, m).c_str(), expect);
    };
    vgq::HkPacket hk{};
    hk.fsw_mode = vgq::MODE_ENCOUNTER; hk.sclk_partition = 12; hk.uptime_s = 86400;
    hk.reset_cause = 0; hk.fdir_flags = vgq::FDIR_PA_OVERTEMP | vgq::FDIR_CMD_LOSS;
    hk.sensor_health = 0x1FFF; hk.cmd_accepted = 100; hk.cmd_rejected = 3; hk.cmd_last_opcode = 9;
    hk.cmd_last_status = 0; hk.tc_frames_ok = 101; hk.tc_frames_bad = 4; hk.farm_state = 1;
    hk.farm_vr = 55; hk.cmd_loss_timer_s = 3600; hk.avionics_temp_cC = 3125; hk.pa_temp_cC = -550;
    hk.bus_voltage_mV = 12034; hk.bus_current_mA = -1250; hk.loop_max_ms = 42; hk.loop_overruns = 1;
    hk.i2c_errors = 2; hk.ssr_fill_permille = 512; hk.ssr_dropped = 0; hk.vc0_backlog = 10;
    hk.vc1_backlog = 200; hk.vc2_backlog = 0;
    hk.sdls_enabled = 1; hk.sdls_auth_fail = 3; hk.sdls_last_sn = 123456789;
    size_t n = vgq::pack_hk(hk, pl);
    CHECK(n == 59, "HK packet is 59 octets");
    emit("HK", vgq::APID_HK, n, "fsw_mode=3,sclk_partition=12,uptime_s=86400,fdir_flags=20,"
         "farm_vr=55,avionics_temp=31.25,pa_temp=-5.5,bus_voltage=12.034,bus_current=-1.25,"
         "ssr_fill=51.2,vc1_backlog=200,cmd_loss_timer_s=3600,sdls_enabled=1,sdls_auth_fail=3,"
         "sdls_last_sn=123456789");

    vgq::RfPacket rf{};
    rf.air_rate_code = 2; rf.tx_power_code = 0; rf.channel = 23; rf.e22_cfg_ok = 1;
    rf.frames_sent = 123456; rf.tx_duty_permille = 433; rf.frame_period_ms = 2000;
    rf.uplink_rssi_dbm = -97; rf.noise_floor_dbm = -118; rf.mcfc = 200; rf.last_airtime_ds = 9;
    n = vgq::pack_rf(rf, pl);
    CHECK(n == 38, "RF packet is 38 octets");
    emit("RF", vgq::APID_RF, n, "channel=23,frames_sent=123456,tx_duty=43.3,uplink_rssi=-97.0,"
         "sc_noise_floor=-118.0,mcfc=200,last_airtime=0.9");

    vgq::MagPacket mg{};
    mg.nsamples = 4; mg.flags = 0x0F;
    mg.outboard = {12345, -23456, 45678, 50}; mg.inboard = {-1, 2, -3, 4};
    mg.body = {vgq::kNaI32, vgq::kNaI32, vgq::kNaI32, vgq::kNaU16}; mg.body_temp_cC = 2500;
    n = vgq::pack_mag(mg, pl);
    CHECK(n == 46, "MAG packet is 46 octets");
    emit("MAG", vgq::APID_MAG, n, "ob_b_x=12.345,ob_b_y=-23.456,ob_b_z=45.678,ob_rms=0.05,"
         "ib_b_z=-0.003,body_b_x=None,body_rms=None,body_temp=25.0");

    vgq::AttPacket at{};
    at.flags = 0x1F; at.acc_mg[2] = 1000; at.gyro_cdps[0] = -150; at.ak_dT[1] = 255;
    at.q_aru[0] = 16384; at.aru_accuracy = 3; at.css_raw[3] = 4095; at.sun_body[2] = 32000;
    at.q_triad[1] = -8192;
    n = vgq::pack_att(at, pl);
    CHECK(n == 50, "ATT packet is 50 octets");
    emit("ATT", vgq::APID_ATT, n, "acc_z=1.0,gyro_x=-1.5,ak_y=25.5,q_aru_w=1.0,aru_accuracy=3,"
         "css_3=4095,sun_z=1.0,q_triad_x=-0.5");

    vgq::SpecPacket sp{};
    sp.flags = 3; sp.as7265x_gain = 1; sp.as7265x_int_cycles = 50;
    for (int i = 0; i < 18; ++i) { sp.as7265x_uW_cm2[i] = 1.5f * i; sp.as7343_counts[i] = (uint16_t)(1000 + i); }
    sp.as7343_gain = 9; sp.as7265x_temp_c = -3;
    n = vgq::pack_spec(sp, pl);
    CHECK(n == 113, "SPEC packet is 113 octets");
    emit("SPEC", vgq::APID_SPEC, n, "as7265x_410nm=0.0,as7265x_940nm=25.5,as7343_F_450_FZ=1000,"
         "as7343_FD_3=1017,as7343_gain=9,as7265x_temp=-3.0");

    vgq::EnvPacket ev{};
    ev.flags = 0xF; ev.bme_t_cC = 2234; ev.bme_p_Pa = 101325; ev.bme_rh_cpct = 4567;
    ev.bme_gas_ohm = 150000; ev.nenv_iaq = 1.5f; ev.nenv_outdoor_aqi = 42; ev.nme_p_hPa = 1001.25f;
    ev.nme_co2eq_ppm = 600; ev.veml_lux = 12345.5f;
    n = vgq::pack_env(ev, pl);
    CHECK(n == 63, "ENV packet is 63 octets");
    emit("ENV", vgq::APID_ENV, n, "bme_t=22.34,bme_p=1013.25,bme_rh=45.67,bme_gas=150000.0,"
         "nenv_iaq=1.5,nenv_aqi=42,nme_p=1001.25,nme_co2eq=600,veml_lux=12345.5");

    vgq::CmdVerPacket cv{5, vgq::OP_PING, vgq::STAGE_EXECUTED, 0, 0xDEADBEEF};
    n = vgq::pack_cmdver(cv, pl);
    emit("CMDVER", vgq::APID_CMDVER, n, "tc_seq=5,opcode=9,stage=2,ground_tag=3735928559");

    const int8_t noise[5] = {-118, -121, -97, -128, -120};
    n = vgq::pack_rfscan(20, 5, 812, noise, pl);
    CHECK(n == 9, "RFSCAN packet is 4 + N octets");
    emit("RFSCAN", vgq::APID_RFSCAN, n, "first_ch=20,count=5,duration_ms=812,noise=-118;-121;-97;None;-120");

    vgq::TimeCorrPacket tc{17, 4000, 1234, 2, 950};
    n = vgq::pack_timecorr(tc, pl);
    emit("TIMECORR", vgq::APID_TIMECORR, n, "ref_mcfc=17,sclk_coarse=4000,sclk_fine=1234,ref_airtime_ms=950");
    std::fclose(f);
  }

  // ---- Command parsing --------------------------------------------------------------------
  {
    // TC packet: version 0, type 1, no sec hdr, APID 0x0C0, seq flags 11
    auto mk = [](const std::vector<uint8_t>& user) {
      std::vector<uint8_t> p = {0x10, 0xC0, 0xC0, 0x00, 0x00, (uint8_t)(user.size() - 1)};
      p.insert(p.end(), user.begin(), user.end());
      return p;
    };
    vgq::Command c;
    auto p1 = mk({vgq::OP_PING, 0x01, 0x02, 0x03, 0x04});
    CHECK(vgq::parse_tc_packet(p1.data(), p1.size(), c) == vgq::CE_OK && c.a32 == 0x01020304,
          "CMD: PING tag decoded");
    auto p2 = mk({vgq::OP_MODE, 9});
    CHECK(vgq::parse_tc_packet(p2.data(), p2.size(), c) == vgq::CE_BAD_ARGUMENT, "CMD: bad mode rejected");
    auto p3 = mk({vgq::OP_REBOOT, 0x12, 0x34});
    CHECK(vgq::parse_tc_packet(p3.data(), p3.size(), c) == vgq::CE_BAD_MAGIC, "CMD: reboot needs magic");
    auto p4 = mk({0x7E});
    CHECK(vgq::parse_tc_packet(p4.data(), p4.size(), c) == vgq::CE_UNKNOWN_OPCODE, "CMD: unknown opcode");
    auto p5 = mk({vgq::OP_AIRRATE, 0, 0x01, 0x2C});
    CHECK(vgq::parse_tc_packet(p5.data(), p5.size(), c) == vgq::CE_OK && c.a8 == 0 && c.a16 == 300,
          "CMD: AIRRATE code + revert timer");
  }

  // ---- Solid-state recorder ------------------------------------------------------------------
  {
    static vgq::Ssr ssr;
    ssr.clear();
    uint8_t p[100], o[128];
    for (int i = 0; i < 400; ++i) { std::memset(p, i & 0xFF, sizeof(p)); p[0] = (uint8_t)i; ssr.record(p, 100); }
    CHECK(ssr.dropped() > 0 && ssr.fill_permille() > 950, "SSR overwrites oldest when full");
    ssr.start_playback();
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

    const float dark[4] = {10, 10, 10, 10};
    const vgq::Vec3 s_true = vgq::v_unit({0.2f, -0.3f, 0.93f});
    const float k = 0.70710678f;
    const vgq::Vec3 nrm[4] = {{k, 0, k}, {-k, 0, k}, {0, k, k}, {0, -k, k}};
    float counts[4];
    for (int i = 0; i < 4; ++i) counts[i] = 10 + 3000 * vgq::v_dot(nrm[i], s_true);
    vgq::Vec3 s_est;
    const bool sok = vgq::css_sun_vector(counts, dark, 50, s_est);
    CHECK(sok && vgq::v_dot(s_est, s_true) > 0.99999f, "Pyramid CSS sun vector inside FOV");
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

  std::printf("\n%s (%d failure%s)\n", g_fail ? "HOST TEST FAILED" : "HOST TEST PASSED", g_fail,
              g_fail == 1 ? "" : "s");
  return g_fail ? 1 : 0;
}
