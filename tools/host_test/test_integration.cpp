// =============================================================================
//  Phase B integration of the flight data & protocol layer (fsw/datalink.*,
//  fsw/commands.*, fsw/tlm_packets.*, fsw/ssr.*, the SPECBURST packer) and the
//  flight <-> ground vectors decoded by the independent Python ground stack
//  (ground/tests/test_cross_check.py, test_spectral.py).
//
//  Checks (Phase B specification PB-8.5, "WS1 extra"): apid_index; every packer's
//  length and the v3 offsets of PB-1.3 / 1.4 / 1.11; parse_tc_header /
//  parse_user_data at every boundary of all 32 opcodes; Ssr export (sequential
//  reads, paused drops, playback/export exclusion, wrapped ring, CFDP over the
//  export); Downlink (priority reserve, anti-starvation, VC3 lowest, OID while
//  waiting for the SDLS epoch, null SA after the timeout, USLP VCID 63, idle text,
//  TM/USLP counts, data-field lengths); tc_accept (TC/USLP x NONE/AUTH/AE, replay,
//  bad MAC never touches FARM, COP-1 control frames, `frame` untouched at
//  FRAME_REJECTED); tc_parse_result_name; RelayGate; EvrGate; the downlink load
//  plan (PB-3.3) and the EGSE status formatter (PB-4.1).
//
//  Vectors written into <out>:
//    tlm_vectors.txt          "<name> <packet hex> <k=v,...>": every fixed-layout packet
//                             built by the flight packers with known values; keys are
//                             the ground names, values in engineering units
//    integration_<s>.bin      CADU streams (236 octets each) built by Downlink:
//                               tm_ae      TM 132.0-B-3 + SDLS TM AE (355.0-B-2), epoch 7
//                               uslp_ae    USLP 732.1-B-2 + SDLS TM AE, epoch 7
//                               tm_nullsa  TM, AE configured but no epoch: OID frames
//                                          while waiting, then the null SA (SPI 0)
//                               tm_clear   TM, no SDLS
//                             SDLS master key 00 01 .. 1F (TM key = HMAC-SHA-256(master,
//                             "VGQ1-SDLS-TM-AE")); SCLK partition 7 except tm_nullsa (0)
//    integration_expect.txt   what each stream carries, one record per line ('#' starts
//                             a comment line):
//      stream <file> framing=<TM|USLP> sdls=<AE|NULL|NONE> epoch=<n> cadus=<n> oid=<n>
//             protected=<n> null_sa=<n> ident=<hex of the OID identification text>
//      pkt <file> <vc> <name> <packet hex> <k=v,...|->   every packet, in transmission
//             order per VC (VC2 = SSR playback, VC3 = CFDP); same key/value rules as
//             tlm_vectors.txt ("None" = not available)
//      cfdp <file> seq=<n> name=<file name> size=<n> checksum=<n> pdus=<n> content=<hex>
//             the SSR export delivered on VC3 (727.0-B-5 class 1, modular checksum).
//             tm_nullsa carries no transfer: without the EGSE the SCLK partition is 0
//             and the flight refuses CFDPPUT (PB-1.13, PB-2.5)
//    command_table.txt        "<user-data hex> <CmdError>" (decimal) per line: every opcode
//                             at lo, hi, lo-1, hi+1 (when the field can carry them), every
//                             "check" rule, one octet short / long, unknown opcodes. The
//                             value is the flight's verdict: parse_user_data(); for MAGCAL
//                             also MagCalStore's checks (magcal_parse, what CMDVER reports);
//                             SEQLOAD rows use a relative time of 10 s and a NOOP so only
//                             the static block checks of CommandSequencer::load apply
//    flight_tc_results.txt    only when <out>/ground_tc.txt exists (written by the ground
//                             test): each input line "<name> <key hex|-> <NONE|AUTH|AE>
//                             <TC|USLP> <cltu hex>" goes through tc_accept() and
//                             parse_tc_packet() with Sdls / Farm1 state kept per <name>
//                             (fresh for a new name, AUTH SPI 1); output per line
//                             "<name> <stage> <parse> <sdls> <verdict> <opcode> <cmd_error>"
//                             with stage NO_CLTU / FRAME_REJECTED / SDLS_REJECTED /
//                             TFDF_REJECTED / FARM, parse = tc_parse_result_name(), sdls
//                             OK / DISABLED / BAD_LENGTH / BAD_SPI / REPLAY / BAD_MAC /
//                             BAD_IV, verdict = the FARM-1 verdict name (ground
//                             Farm1Model names), "-" where the stage was not reached;
//                             opcode / cmd_error (decimal) only for ACCEPT / ACCEPT_BYPASS
// =============================================================================
#include <cmath>
#include <cstdio>
#include <cstring>
#include <map>
#include <memory>
#include <string>
#include <vector>

#include "ccsds/cfdp.h"
#include "ccsds/crc16.h"
#include "ccsds/gcm.h"
#include "ccsds/randomizer.h"
#include "ccsds/reed_solomon.h"
#include "ccsds/rice.h"
#include "ccsds/sdls.h"
#include "ccsds/sha256.h"
#include "ccsds/tc.h"
#include "ccsds/tm.h"
#include "ccsds/uslp.h"
#include "drivers/radio.h"
#include "fsw/autonomy.h"
#include "fsw/commands.h"
#include "fsw/datalink.h"
#include "fsw/spectral.h"
#include "fsw/ssr.h"
#include "fsw/tlm_packets.h"
#include "host_test.h"

using namespace ccsds;
using namespace vgq;
using host_test::hex;
using host_test::unhex;

namespace {

typedef std::vector<uint8_t> Bytes;

// Own deterministic generator (independent of test order and of host_test::rnd()).
uint32_t prng() {
  static uint32_t s = 0x1B2A3C4Du;
  s ^= s << 13; s ^= s >> 17; s ^= s << 5;
  return s;
}

const uint16_t kEpoch = 7;                 // SDLS TM epoch = SCLK partition of the vectors
const char kCallsign[] = "N0CALL";
const uint32_t kVc3BacklogMax = 320;       // config.h kCfdpVc3BacklogMax (PB-2.5 pacing)

void master_key(uint8_t k[32]) { for (int i = 0; i < 32; ++i) k[i] = (uint8_t)i; }

// ---- value formatting for the expectation strings ------------------------------------
std::string num(long long v) { return std::to_string(v); }
// A float-typed ground value: always with a decimal point, 9 significant digits.
std::string fnum(double v) {
  char b[40];
  std::snprintf(b, sizeof b, "%.9g", v);
  std::string s = b;
  if (s.find_first_of(".en") == std::string::npos) s += ".0";
  return s;
}
void kv(std::string& s, const char* k, const std::string& v) {
  if (!s.empty()) s += ",";
  s += k;
  s += "=";
  s += v;
}

// ---- known packet contents -----------------------------------------------------------
HkPacket hk_known() {
  HkPacket h{};
  h.fsw_mode = MODE_ENCOUNTER; h.sclk_partition = kEpoch; h.uptime_s = 86400; h.reset_cause = 4;
  h.fdir_flags = FDIR_PA_OVERTEMP | FDIR_CMD_LOSS; h.sensor_health = kSensorInstrumentMask;
  h.cmd_accepted = 100; h.cmd_rejected = 3; h.cmd_last_opcode = OP_SPECMODE; h.cmd_last_status = CE_BUSY;
  h.tc_frames_ok = 101; h.tc_frames_bad = 4; h.farm_state = 1; h.farm_vr = 55;
  h.cmd_loss_timer_s = 3600; h.bus_temp_cC = 3125; h.radio_fault = 17; h.radio_fault_reports = 17;
  h.loop_max_ms = 42; h.loop_overruns = 1; h.i2c_errors = 2; h.ssr_fill_permille = 512;
  h.ssr_dropped = 5; h.ssr_kib = 256; h.vc0_backlog = 10; h.vc1_backlog = 200; h.vc2_backlog = 30;
  h.vc3_backlog = 158; h.sdls_tc_service = SDLS_SVC_AE; h.sdls_tm_service = SDLS_SVC_AE;
  h.sdls_auth_fail = 3; h.sdls_last_sn = 123456789; h.sdls_tm_epoch = kEpoch;
  h.sdls_tm_state = SdlsTm::TM_PROTECTED; h.cfdp_state = 1; h.cfdp_seq = cfdp_make_seq(12, 1);
  h.cfdp_progress_permille = 437; h.seq_state = SEQ_ARMED; h.seq_count = 5; h.seq_next_dt_s = 120;
  h.mag_bist = 0x8F; h.magcal_state = 1; h.beacon_level = BEACON_IMPORTANT;
  return h;
}
std::string hk_expect(const HkPacket& h) {
  std::string s;
  kv(s, "fsw_mode", num(h.fsw_mode)); kv(s, "sclk_partition", num(h.sclk_partition));
  kv(s, "uptime_s", num(h.uptime_s)); kv(s, "reset_cause", num(h.reset_cause));
  kv(s, "fdir_flags", num(h.fdir_flags)); kv(s, "sensor_health", num(h.sensor_health));
  kv(s, "cmd_accepted", num(h.cmd_accepted)); kv(s, "cmd_rejected", num(h.cmd_rejected));
  kv(s, "cmd_last_opcode", num(h.cmd_last_opcode)); kv(s, "cmd_last_status", num(h.cmd_last_status));
  kv(s, "tc_frames_ok", num(h.tc_frames_ok)); kv(s, "tc_frames_bad", num(h.tc_frames_bad));
  kv(s, "farm_state", num(h.farm_state)); kv(s, "farm_vr", num(h.farm_vr));
  kv(s, "cmd_loss_timer_s", num(h.cmd_loss_timer_s)); kv(s, "bus_temp", fnum(h.bus_temp_cC * 0.01));
  kv(s, "radio_fault", num(h.radio_fault)); kv(s, "radio_fault_reports", num(h.radio_fault_reports));
  kv(s, "loop_max_ms", num(h.loop_max_ms)); kv(s, "loop_overruns", num(h.loop_overruns));
  kv(s, "i2c_errors", num(h.i2c_errors)); kv(s, "ssr_fill", fnum(h.ssr_fill_permille * 0.1));
  kv(s, "ssr_dropped", num(h.ssr_dropped)); kv(s, "ssr_kib", num(h.ssr_kib));
  kv(s, "vc0_backlog", num(h.vc0_backlog)); kv(s, "vc1_backlog", num(h.vc1_backlog));
  kv(s, "vc2_backlog", num(h.vc2_backlog)); kv(s, "vc3_backlog", num(h.vc3_backlog));
  kv(s, "sdls_tc_service", num(h.sdls_tc_service)); kv(s, "sdls_tm_service", num(h.sdls_tm_service));
  kv(s, "sdls_auth_fail", num(h.sdls_auth_fail)); kv(s, "sdls_last_sn", num(h.sdls_last_sn));
  kv(s, "sdls_tm_epoch", num(h.sdls_tm_epoch)); kv(s, "sdls_tm_state", num(h.sdls_tm_state));
  kv(s, "cfdp_state", num(h.cfdp_state));
  kv(s, "cfdp_seq", h.cfdp_seq == kNaU32 ? "None" : num(h.cfdp_seq));
  kv(s, "cfdp_progress", fnum(h.cfdp_progress_permille * 0.1));
  kv(s, "seq_state", num(h.seq_state)); kv(s, "seq_count", num(h.seq_count));
  kv(s, "seq_next_dt_s", h.seq_next_dt_s == kNaU16 ? "None" : num(h.seq_next_dt_s));
  kv(s, "mag_bist", num(h.mag_bist)); kv(s, "magcal_state", num(h.magcal_state));
  kv(s, "beacon_level", num(h.beacon_level));
  return s;
}

RfPacket rf_known(uint8_t framing) {
  RfPacket r{};
  r.radio_model = VGQ_RADIO_SX126X; r.air_rate_code = 1; r.tx_power_code = 2; r.channel = 23;
  r.radio_cfg_ok = 1; r.framing = framing; r.frames_sent = 123456; r.oid_frames_sent = 789;
  r.tx_airtime_ms_total = 4567890; r.tx_duty_permille = 433; r.frame_period_ms = 2000;
  r.aux_timeouts = 2; r.uplink_rssi_dbm = -97; r.uplink_snr_cdb = -1250; r.uplink_freq_err_hz = -1234;
  r.noise_floor_dbm = tlm_na_i16(kRadioNaI16); r.cltu_ok = 50; r.cltu_bad = 2; r.bch_corrected = 3;
  r.rx_bytes = 9876; r.mcfc = 200; r.last_airtime_ds = 9; r.lbt_busy = 4;
  r.flags = RF_F_RX_BOOST | RF_F_LBT;
  return r;
}
std::string i16_or_none(int16_t v, double scale) { return v == kNaI16 ? "None" : fnum(v * scale); }
std::string rf_expect(const RfPacket& r) {
  std::string s;
  kv(s, "radio_model", num(r.radio_model)); kv(s, "air_rate_code", num(r.air_rate_code));
  kv(s, "tx_power_code", num(r.tx_power_code)); kv(s, "channel", num(r.channel));
  kv(s, "radio_cfg_ok", num(r.radio_cfg_ok)); kv(s, "framing", num(r.framing));
  kv(s, "frames_sent", num(r.frames_sent)); kv(s, "oid_frames_sent", num(r.oid_frames_sent));
  kv(s, "tx_airtime_ms_total", num(r.tx_airtime_ms_total)); kv(s, "tx_duty", fnum(r.tx_duty_permille * 0.1));
  kv(s, "frame_period_ms", num(r.frame_period_ms)); kv(s, "radio_timeouts", num(r.aux_timeouts));
  kv(s, "uplink_rssi", i16_or_none(r.uplink_rssi_dbm, 1.0));
  kv(s, "uplink_snr", i16_or_none(r.uplink_snr_cdb, 0.01));
  kv(s, "uplink_freq_err", r.uplink_freq_err_hz == kNaI32 ? "None" : fnum(r.uplink_freq_err_hz));
  kv(s, "sc_noise_floor", i16_or_none(r.noise_floor_dbm, 1.0));
  kv(s, "cltu_ok", num(r.cltu_ok)); kv(s, "cltu_bad", num(r.cltu_bad));
  kv(s, "bch_corrected", num(r.bch_corrected)); kv(s, "rx_bytes", num(r.rx_bytes));
  kv(s, "mcfc", num(r.mcfc)); kv(s, "last_airtime", fnum(r.last_airtime_ds * 0.1));
  kv(s, "lbt_busy", num(r.lbt_busy)); kv(s, "rf_flags", num(r.flags));
  return s;
}

EnvPacket env_known() {
  EnvPacket e{};
  e.flags = ENV_F_PRESSURE | ENV_F_BSEC | ENV_F_TH | ENV_F_GAS; e.p_cPa = 100125; e.t_cC = -150;
  e.rh_cpct = 4500; e.iaq = 77; e.iaq_s = 80; e.co2eq_ppm = 600; e.bvoc_cppm = 125;
  e.gas_ohm = 150000; e.accuracy = 3;
  return e;
}
std::string env_expect(const EnvPacket& e) {
  std::string s;
  kv(s, "flags", num(e.flags)); kv(s, "p", fnum(e.p_cPa * 0.01)); kv(s, "t", fnum(e.t_cC * 0.01));
  kv(s, "rh", fnum(e.rh_cpct * 0.01)); kv(s, "iaq", num(e.iaq)); kv(s, "iaq_s", num(e.iaq_s));
  kv(s, "co2eq", num(e.co2eq_ppm)); kv(s, "bvoc", fnum(e.bvoc_cppm * 0.01)); kv(s, "gas", num(e.gas_ohm));
  kv(s, "accuracy", num(e.accuracy));
  return s;
}

MagPacket mag_known(uint8_t flags) {
  MagPacket m{};
  m.nsamples = 4; m.flags = flags;
  m.outboard = {12345, -23456, 45678, 50};
  m.inboard = {kNaI32, kNaI32, kNaI32, kNaU16};
  m.body = {-1, 2, -3, 4};
  m.rm3100_cycle_count = 200;
  return m;
}
const char* const kMagExpect =
    "nsamples=4,ob_b_x=12.345,ob_b_y=-23.456,ob_b_z=45.678,ob_rms=0.05,ib_b_x=None,ib_rms=None,"
    "body_b_z=-0.003,rm3100_cc=200";

AttPacket att_known(uint8_t flags) {
  AttPacket a{};
  a.flags = flags; a.acc_mg[2] = 1000; a.gyro_ddps[0] = -15; a.mag_dT[1] = 255;
  a.q_fus[0] = 16384; a.q_fus_acc_mrad = 52; a.q_triad[1] = -8192;
  return a;
}
const char* const kAttExpect =
    "acc_z=1.0,gyro_x=-1.5,mag_y=25.5,q_fus_w=1.0,q_fus_acc=0.052,q_triad_x=-0.5";

// SPEC v3.1 of a synthetic SURVEY result run through the onboard processor.
SpecV31 spec_known() {
  SpecResult r;
  spec_result_clear(r);
  r.mode = SPEC_SURVEY;
  r.flags = SPEC_F_VALID | SPEC_F_AGC;
  r.gain_code = 2;
  r.int_cycles = 59;
  r.n_frames = 1;
  r.dev_mask = 7;
  r.valid_mask = kSpecAllChannels;
  for (uint8_t i = 0; i < kSpecChannels; ++i) {
    const double nm = kSpecWavelengthNm[i];
    r.spec[i] = (float)(20.0 + 0.05 * (nm - 410.0));        // a smooth, positive spectrum
    r.unc[i] = r.spec[i] * 0.01f;
    r.raw[i] = (uint16_t)(1000 + 37 * i);
  }
  r.temp[0] = 28; r.temp[1] = 29; r.temp[2] = 30;
  SpecAttitude att;
  att.valid = true;
  att.world = SPEC_WORLD_ENU;
  att.q_body_to_world = Quat{1, 0, 0, 0};
  att.bore_body = Vec3{0.0f, 1.0f, 1.0f};                    // north-east-up quadrant: az 0, zen 45
  SpecProcessor proc;
  SpecV31 v;
  proc.process(r, att, 1000, v);
  return v;
}
std::string spec_expect(const SpecV31& v) {
  std::string s;
  kv(s, "spec_mode", num(v.mode)); kv(s, "flags", num(v.flags)); kv(s, "gain_code", num(v.gain_code));
  kv(s, "int_cycles", num(v.int_cycles)); kv(s, "led_cur", num(v.led_cur));
  kv(s, "n_frames", num(v.n_frames)); kv(s, "sat_mask", num(v.sat_mask));
  for (uint8_t i = 0; i < kSpecChannels; ++i) {
    char k[24];
    if (v.spec_log[i] != 0xFFFF) {
      std::snprintf(k, sizeof k, "spec_%unm", (unsigned)kSpecWavelengthNm[i]);
      kv(s, k, fnum(spec_log_decode(v.spec_log[i])));
    }
    std::snprintf(k, sizeof k, "raw_%unm", (unsigned)kSpecWavelengthNm[i]);
    kv(s, k, v.raw[i] == 0xFFFF ? "None" : num(v.raw[i]));
  }
  for (uint8_t d = 0; d < kSpecDevices; ++d) {
    char k[16];
    std::snprintf(k, sizeof k, "temp_%u", (unsigned)d);
    kv(s, k, v.temp[d] == kNaI8 ? "None" : num(v.temp[d]));
  }
  kv(s, "bore_zen", v.bore_zen_cdeg == kNaI16 ? "None" : fnum(v.bore_zen_cdeg / 100.0));
  kv(s, "bore_az", v.bore_az_cdeg == kNaI16 ? "None"
                   : fnum(v.bore_az_cdeg < 0 ? v.bore_az_cdeg / 100.0 + 360.0 : v.bore_az_cdeg / 100.0));
  kv(s, "spec_class", num(v.class_id)); kv(s, "spec_conf", num(v.class_conf));
  kv(s, "spec_change", v.change_cdeg == 0xFFFF ? "None" : fnum(v.change_cdeg / 100.0));
  return s;
}

// Burst samples: smooth (Rice wins) or pseudo-random (incompressible: raw).
void burst_samples(SpecBurstSample s[3], bool smooth) {
  for (int t = 0; t < 3; ++t) {
    s[t].dt_ms = (uint16_t)(t * 25);
    for (int ch = 0; ch < kSpecChannels; ++ch)
      s[t].raw[ch] = smooth ? (uint16_t)(2000 + 40 * ch + 3 * t) : (uint16_t)prng();
  }
  if (smooth) {
    s[1].raw[5] = 0xFFFF;                               // not converted (4-channel BANK mode)
    for (int t = 0; t < 3; ++t) s[t].raw[17] = 0xFFFE;  // saturated (count 65535)
  }
}

// =============================================================================
// Space Packet / CADU stream emulation of the flight emit() path
// =============================================================================
struct Emitter {
  Downlink dl;
  SdlsTm tm;
  Ssr ssr;                                     // 4 KiB fallback buffer: no heap
  uint16_t seq[kNumApids] = {0};
  uint32_t sclk = 4000;
  std::string file;                            // stream file name
  std::vector<Bytes> sent[kNumDataVcs];        // packets pushed, per VC, in order
  Bytes ssr_file;                              // model of the SSR export content
  std::vector<std::string> rec_name, rec_expect;   // recorded packets (playback lines)
  std::vector<std::string> lines;              // "pkt" expectation lines
  uint32_t emit_refused = 0;

  bool push_line(uint8_t vc, const uint8_t* pkt, size_t len, const std::string& name,
                 const std::string& expect, bool priority) {
    if (!dl.push(vc, pkt, len, priority)) return false;
    sent[vc].push_back(Bytes(pkt, pkt + len));
    lines.push_back("pkt " + file + " " + num(vc) + " " + name + " " + hex(pkt, len) + " " +
                    (expect.empty() ? std::string("-") : expect));
    return true;
  }
  bool emit(uint16_t apid, const uint8_t* ud, size_t n, uint8_t vc, const char* name,
            const std::string& expect, bool record = true, bool priority = false) {
    const uint8_t i = apid_index(apid);
    if (i == kApidIndexNone) { ++emit_refused; return false; }
    uint8_t pkt[kSpPriHdrLen + kSpSecHdrLen + kMaxPayload];
    const size_t len = build_tm_packet(pkt, sizeof pkt, apid, seq[i], CucTime{sclk, 0x8000}, ud, n);
    if (len == 0) return false;
    seq[i] = (uint16_t)((seq[i] + 1) & 0x3FFF);
    if (record && !ssr.exporting()) {
      ssr.record(pkt, (uint16_t)len);
      ssr_file.insert(ssr_file.end(), pkt, pkt + len);
      rec_name.push_back(name);
      rec_expect.push_back(expect);
    }
    return push_line(vc, pkt, len, name, expect, priority);
  }
};

// Independent receiver for the CADU streams: derandomise, RS parity, FECF, TM / USLP
// header, SDLS TM (AE decrypt with the derived TM key, or the null SA), OID text, and
// packet reassembly per VC with a First Header Pointer consistency check.
struct Decoded {
  std::vector<Bytes> pkts[kNumDataVcs];
  uint32_t cadus = 0, oid = 0, prot = 0, null_sa = 0;
  bool ok = true;
  std::string why;
  void fail(const std::string& w) { if (ok) why = w; ok = false; }
};

Decoded decode_stream(const Bytes& s, Framing fr, int sec /*0 none 1 AE 2 null*/, uint16_t epoch,
                      const char* idle_text) {
  Decoded d;
  uint8_t master[32], ktm[32];
  master_key(master);
  sdls_derive_key(master, 32, kSdlsLabelTmAe, ktm);
  AesGcm g;
  g.set_key(ktm);
  const bool uslp = fr == FRAMING_USLP;
  const size_t hl = uslp ? kUslpTmHdrLen : kTmPriHdrLen;
  const size_t sh = sec ? kSdlsAeHdrLen : 0, st = sec ? kSdlsAeTagLen : 0;
  const size_t zone_len = (uslp ? kUslpTfdzLen : kTmDataLen) - sh - st;
  const uint16_t fhp_none = uslp ? kUslpFhpNone : kFhpNoPacketStart;
  const size_t idle_len = std::strlen(idle_text);
  Bytes stream[kNumDataVcs];
  struct FrameRef { size_t off; uint16_t fhp; };
  std::vector<FrameRef> refs[kNumDataVcs];
  uint32_t last_sn = 0;
  int last_mcfc = -1;
  int last_vcf[64];
  for (int& v : last_vcf) v = -1;
  if (s.size() % kCaduLen) d.fail("stream length not a multiple of 236");
  for (size_t at = 0; at + kCaduLen <= s.size() && d.ok; at += kCaduLen) {
    ++d.cadus;
    const uint8_t* c = s.data() + at;
    if (get_u32(c) != kAsm) { d.fail("ASM"); break; }
    uint8_t cb[kCodeblockLen];
    std::memcpy(cb, c + kAsmLen, kCodeblockLen);
    randomize(cb, kCodeblockLen);                        // XOR sequence: self-inverse
    uint8_t par[32];
    ReedSolomon::encode(cb, kTmFrameLen, par);
    if (std::memcmp(par, cb + kTmFrameLen, 32) != 0) { d.fail("RS parity"); break; }
    if (crc16_ccitt(cb, kTmFrameLen - 2) != get_u16(cb + kTmFrameLen - 2)) { d.fail("FECF"); break; }
    uint8_t vcid;
    uint16_t fhp = 0;
    if (uslp) {
      const uint32_t w = get_u32(cb);
      if ((cb[0] >> 4) != kUslpTfvn || ((w >> 12) & 0xFFFFu) != kSpacecraftId || ((w >> 11) & 1u) != 0 ||
          get_u16(cb + 4) + 1u != kTmFrameLen || (cb[6] & 0x0F) != 0x09) { d.fail("USLP header"); break; }
      vcid = (uint8_t)((w >> 5) & 0x3F);
      if (last_vcf[vcid] >= 0 && cb[7] != (uint8_t)(last_vcf[vcid] + 1)) { d.fail("USLP VCF count"); break; }
      last_vcf[vcid] = cb[7];
    } else {
      const uint16_t w = get_u16(cb);
      if ((w >> 14) != 0 || ((w >> 4) & 0x3FF) != kSpacecraftId || (w & 1u) != 1u) { d.fail("TM header"); break; }
      vcid = (uint8_t)((w >> 1) & 7);
      if (last_mcfc >= 0 && cb[2] != (uint8_t)(last_mcfc + 1)) { d.fail("MCFC"); break; }
      last_mcfc = cb[2];
      if (last_vcf[vcid] >= 0 && cb[3] != (uint8_t)(last_vcf[vcid] + 1)) { d.fail("VCFC"); break; }
      last_vcf[vcid] = cb[3];
      fhp = (uint16_t)(get_u16(cb + 4) & 0x7FF);
      if ((get_u16(cb + 4) >> 11) != 3u) { d.fail("TM data field status"); break; }
    }
    const bool is_oid = uslp ? vcid == kUslpVcidOid : vcid == VC_IDLE;
    if (is_oid) {
      ++d.oid;
      const uint8_t* z;
      size_t zn;
      if (uslp) {
        if (cb[hl] != uslp_tfdf_byte(kUslpRulePackets, kUslpUpidIdle) || get_u16(cb + hl + 1) != kUslpFhpNone) {
          d.fail("USLP OID TFDF");
          break;
        }
        z = cb + hl + kUslpTfdfHdrLen;
        zn = kUslpTfdzLen;
      } else {
        if (fhp != kFhpOnlyIdleData) { d.fail("TM OID FHP"); break; }
        z = cb + hl;
        zn = kTmDataLen;
      }
      bool text = idle_len > 0;
      for (size_t i = 0; i < zn && text; ++i) text = z[i] == (uint8_t)idle_text[i % idle_len];
      if (!text) { d.fail("OID identification text"); break; }
      continue;
    }
    if (vcid >= kNumDataVcs) { d.fail("unexpected VCID"); break; }
    uint8_t* prot = cb + hl + sh;                         // protected region (data / TFDF)
    const size_t plen = uslp ? kUslpTfdfHdrLen + zone_len : zone_len;
    if (sec == 1) {
      const uint8_t* iv = cb + hl + 2;
      if (get_u16(cb + hl) != kSdlsSpiTmAe || get_u16(iv) != kSdlsIvDirTm || get_u16(iv + 2) != epoch ||
          get_u32(iv + 4) != 0 || get_u32(iv + 8) != last_sn + 1) { d.fail("AE security header"); break; }
      last_sn = get_u32(iv + 8);
      if (!g.decrypt(iv, cb, hl + sh, prot, plen, prot + plen)) { d.fail("AE tag"); break; }
      ++d.prot;
    } else if (sec == 2) {
      bool zero = true;
      for (size_t i = 0; i < sh; ++i) zero &= cb[hl + i] == 0;
      for (size_t i = 0; i < st; ++i) zero &= prot[plen + i] == 0;
      if (!zero) { d.fail("null SA header/trailer not zero"); break; }
      ++d.null_sa;
    }
    const uint8_t* zone = prot;
    if (uslp) {
      if (prot[0] != uslp_tfdf_byte(kUslpRulePackets, kUslpUpidPackets)) { d.fail("USLP TFDF"); break; }
      fhp = get_u16(prot + 1);
      zone = prot + kUslpTfdfHdrLen;
    }
    refs[vcid].push_back(FrameRef{stream[vcid].size(), fhp});
    stream[vcid].insert(stream[vcid].end(), zone, zone + zone_len);
  }
  // Reassembly: each VC stream starts with a packet header (empty VC at init).
  for (uint8_t v = 0; v < kNumDataVcs && d.ok; ++v) {
    std::vector<size_t> starts;
    size_t o = 0;
    for (int guard = 0; guard < 10000 && o + kSpPriHdrLen <= stream[v].size(); ++guard) {
      const size_t len = kSpPriHdrLen + get_u16(stream[v].data() + o + 4) + 1u;
      if (o + len > stream[v].size()) break;              // trailing idle fill
      starts.push_back(o);
      if ((get_u16(stream[v].data() + o) & 0x7FF) != kApidIdle)
        d.pkts[v].push_back(Bytes(stream[v].begin() + (long)o, stream[v].begin() + (long)(o + len)));
      o += len;
    }
    for (const FrameRef& r : refs[v]) {
      uint16_t want = fhp_none;
      for (size_t st0 : starts)
        if (st0 >= r.off && st0 < r.off + zone_len) { want = (uint16_t)(st0 - r.off); break; }
      if (r.fhp != want) { d.fail("first header pointer on VC " + num(v)); break; }
    }
  }
  return d;
}

void write_file(const std::string& path, const Bytes& b) {
  FILE* f = std::fopen(path.c_str(), "wb");
  if (f) { std::fwrite(b.data(), 1, b.size(), f); std::fclose(f); }
}

// Builds one integration stream; returns its expectation lines.
std::vector<std::string> build_stream(const std::string& out, const char* file, Framing fr, int sec) {
  std::unique_ptr<Emitter> e(new Emitter());
  e->file = file;
  uint8_t master[32];
  master_key(master);
  const uint16_t partition = sec == 2 ? 0 : kEpoch;
  if (sec) e->tm.init(SDLS_SVC_AE, master, 32);
  if (sec == 1) CHECK(e->tm.set_epoch(kEpoch), "SDLS TM epoch 7 accepted");
  char idle[kIdleTextMax + 1];
  format_idle_text(kCallsign, idle, sizeof idle);
  e->dl.init(fr, sec ? &e->tm : nullptr, idle);
  Bytes cadus;
  Clcw clcw;
  clcw.report_value = 55;
  uint8_t cadu[kCaduLen];
  DownlinkFrame info;
  std::string pre = std::string(file) + ": ";
  uint8_t ud[kMaxPayload];

  // ---- boot EVR; the null-SA stream waits for its epoch first ----
  const char* boot = "BOOT VGQ-1 FSW 3.0 rst=4 BROWNOUT radio=SX126x TM TC AE TM AE";
  size_t n = pack_evr(1, 0x0001, boot, ud);
  e->emit(APID_EVR, ud, n, VC_RT_ENG, "EVR", "severity_code=1,event_id=1");
  if (sec == 2) {
    const size_t before = e->dl.backlog(0);
    bool oid_only = true;
    for (int k = 0; k < 2; ++k) {
      e->dl.next_cadu(clcw, cadu, info);
      cadus.insert(cadus.end(), cadu, cadu + kCaduLen);
      oid_only &= info.oid && info.vcid == VC_IDLE && info.security == DL_SEC_CLEAR;
    }
    CHECK(oid_only && e->dl.backlog(0) == before && e->tm.state() == SdlsTm::TM_WAIT_EPOCH,
          (pre + "OID frames only while waiting for the epoch, VC0 untouched").c_str());
    CHECK(!e->tm.poll_epoch_timeout(119, 120) && e->tm.poll_epoch_timeout(120, 120) &&
              e->tm.state() == SdlsTm::TM_UNPROTECTED,
          (pre + "null SA engages at the epoch timeout (120 s)").c_str());
    n = pack_evr(2, 0x0121, "SDLS TM: no epoch after 120 s: null SA (clear) until the EGSE sends one", ud);
    e->emit(APID_EVR, ud, n, VC_RT_ENG, "EVR", "severity_code=2,event_id=289", true, true);
  } else if (sec == 1) {
    n = pack_evr(1, 0x0120, "SDLS TM epoch 7: downlink protected", ud);
    e->emit(APID_EVR, ud, n, VC_RT_ENG, "EVR", "severity_code=1,event_id=288", true, true);
  }

  // ---- one of each packet ----
  HkPacket hk = hk_known();
  hk.sclk_partition = partition;
  hk.sdls_tm_service = e->tm.service();
  hk.sdls_tm_epoch = e->tm.epoch();
  hk.sdls_tm_state = e->tm.state();
  n = pack_hk(hk, ud);
  e->emit(APID_HK, ud, n, VC_RT_ENG, "HK", hk_expect(hk));
  const RfPacket rf = rf_known((uint8_t)fr);
  n = pack_rf(rf, ud);
  e->emit(APID_RF, ud, n, VC_RT_ENG, "RF", rf_expect(rf));
  const CmdVerPacket cv{kCmdVerSeqStored, OP_SPECMODE, STAGE_EXECUTED, CE_OK, 0};
  n = pack_cmdver(cv, ud);
  e->emit(APID_CMDVER, ud, n, VC_RT_ENG, "CMDVER", "tc_seq=254,opcode=26,stage=2,error_code=0,ground_tag=0",
          true, true);
  const TimeCorrPacket tcp{17, 4000, 1234, 1, 950};
  n = pack_timecorr(tcp, ud);
  e->emit(APID_TIMECORR, ud, n, VC_RT_ENG, "TIMECORR",
          "ref_mcfc=17,sclk_coarse=4000,sclk_fine=1234,air_rate_code=1,ref_airtime_ms=950");
  const int8_t noise[5] = {-118, -121, -97, kNaI8, -120};
  n = pack_rfscan(20, 5, 812, noise, ud);
  e->emit(APID_RFSCAN, ud, n, VC_RT_ENG, "RFSCAN", "first_ch=20,count=5,duration_ms=812,noise=-118;-121;-97;None;-120");
  n = pack_evr(1, 0x0704, "BURST START n=10 s cycles=1", ud);
  e->emit(APID_EVR, ud, n, VC_RT_ENG, "EVR", "severity_code=1,event_id=1796");
  std::string mexp = kMagExpect;
  kv(mexp, "flags", num(0x30));
  n = pack_mag(mag_known(0x30), ud);
  e->emit(APID_MAG, ud, n, VC_RT_SCI, "MAG", mexp);
  std::string aexp = kAttExpect;
  kv(aexp, "flags", num(0x7F));
  n = pack_att(att_known(0x7F), ud);
  e->emit(APID_ATT, ud, n, VC_RT_SCI, "ATT", aexp);
  const SpecV31 sp = spec_known();
  n = pack_spec_v31(sp, ud, sizeof ud);
  e->emit(APID_SPEC, ud, n, VC_RT_SCI, "SPEC", spec_expect(sp));
  const EnvPacket env = env_known();
  n = pack_env(env, ud);
  e->emit(APID_ENV, ud, n, VC_RT_SCI, "ENV", env_expect(env));
  for (int k = 0; k < 2; ++k) {
    SpecBurstSample bs[3];
    burst_samples(bs, k == 0);
    uint8_t rs = 0xFF;
    n = pack_specburst(0x01020304u + 1000u * (uint32_t)k, 400, 3, 1, bs, 3, ud, sizeof ud, rs);
    const uint8_t want = k == 0 ? SPEC_BURST_RICE : SPEC_BURST_RAW;
    CHECK(n > 0 && ud[9] == want && rs == (k == 0 ? RICE_OK : RICE_ERR_OVERFLOW),
          (pre + (k == 0 ? "SPECBURST smooth samples -> Rice" : "SPECBURST random samples -> raw")).c_str());
    std::string bexp;
    kv(bexp, "t0_ms", num(0x01020304u + 1000u * (uint32_t)k)); kv(bexp, "rate_dhz", "400");
    kv(bexp, "gain_code", "3"); kv(bexp, "int_cycles", "1"); kv(bexp, "n", "3");
    kv(bexp, "encoding", num(want)); kv(bexp, "payload_len", num((long long)n - 16));
    e->emit(APID_SPECBURST, ud, n, VC_RT_SCI, "SPECBURST", bexp);
  }
  n = pack_evr(1, 0x0704, "BURST END samples=6 rate=40.0 Hz dropped=0 readmax=12 ms tx=1", ud);
  e->emit(APID_EVR, ud, n, VC_RT_ENG, "EVR", "severity_code=1,event_id=1796");
  CHECK(e->emit_refused == 0 && e->emit(0x025, ud, 1, VC_RT_SCI, "X", "") == false && e->emit_refused == 1,
        (pre + "every packet queued; an unknown APID is refused by emit()").c_str());

  // ---- frames: SSR playback on VC2, then the CFDP export of the SSR on VC3 ----
  CfdpSender tx;
  SsrFileSource src(e->ssr);
  char cname[32] = {0};
  const uint32_t cseq = cfdp_make_seq(partition, 1);
  bool pb_started = false, ex_started = false, ex_done = partition == 0, cfdp_ok = true;
  size_t pb_next = 0;
  uint32_t pdus = 0, trailing_oid = 0;
  size_t vc3_max = 0;
  const size_t max_pkt = kSpPriHdrLen + kSpSecHdrLen + kMaxPayload;
  for (int pass = 0; pass < 600; ++pass) {
    ++e->sclk;
    if (!pb_started) { CHECK(e->ssr.start_playback(), (pre + "playback starts").c_str()); pb_started = true; }
    // Playback refill (the executive's rule): only while VC2 has room for the largest
    // packet below the routine limit, so next_playback() never loses a packet.
    for (int k = 0; k < 4 && e->ssr.playing() &&
                    e->dl.backlog(2) + max_pkt <= VcStream::kBufLen - kVcReserveOctets; ++k) {
      uint8_t p[max_pkt];
      const uint16_t pn = e->ssr.next_playback(p, sizeof p);
      if (pn == 0) break;
      e->push_line(VC_PLAYBACK, p, pn, e->rec_name[pb_next], e->rec_expect[pb_next], false);
      ++pb_next;
    }
    if (!e->ssr.playing() && !ex_started && !ex_done) {
      cfdp_ssr_file_name(cname, sizeof cname, partition, e->sclk);
      cfdp_ok &= e->ssr.begin_export() && tx.begin(cseq, cname, &src);
      ex_started = true;
    }
    if (tx.active() && e->dl.backlog(3) + kSpPriHdrLen + kSpSecHdrLen + kCfdpMaxPduLen <= kVc3BacklogMax) {
      uint8_t pdu[kCfdpMaxPduLen];
      const size_t pn = tx.next_pdu(pdu, sizeof pdu);
      std::string pexp;
      kv(pexp, "pdu_len", num((long long)pn));
      cfdp_ok &= pn > 0 && e->emit(APID_CFDP, pdu, pn, VC_FILE, "CFDP", pexp, false);
      ++pdus;
    }
    if (ex_started && !ex_done && !tx.active()) {
      e->ssr.end_export();
      ex_done = true;
    }
    const bool drained = !e->dl.has_real_data(0) && !e->dl.has_real_data(1) && !e->dl.has_real_data(2) &&
                         !e->dl.has_real_data(3) && !e->ssr.playing() && ex_done;
    if (drained && trailing_oid >= 2) break;
    e->dl.next_cadu(clcw, cadu, info);
    cadus.insert(cadus.end(), cadu, cadu + kCaduLen);
    if (drained) ++trailing_oid;
    if (e->dl.backlog(3) > vc3_max) vc3_max = e->dl.backlog(3);
  }
  CHECK(partition == 0 ? vc3_max == 0 : (vc3_max > 0 && vc3_max <= kVc3BacklogMax),
        (pre + "VC3 backlog never above the pacing limit (max " + num((long long)vc3_max) + " octets)").c_str());
  if (partition != 0)
    CHECK(cfdp_ok && ex_done && !tx.active() && tx.last_condition() == kCfdpCondNoError && pdus > 2,
          (pre + "SSR export over CFDP completed (paced on VC3)").c_str());
  CHECK(pb_next == e->rec_name.size(), (pre + "SSR playback delivered every recorded packet on VC2").c_str());
  write_file(out + "/" + file, cadus);

  // ---- independent decode of what was written ----
  const Decoded d = decode_stream(cadus, fr, sec, kEpoch, idle);
  CHECK(d.ok, (pre + "stream decodes: RS, FECF, headers, SDLS, OID text, FHP (" + d.why + ")").c_str());
  bool same = d.ok;
  for (uint8_t v = 0; v < kNumDataVcs; ++v) same &= d.pkts[v] == e->sent[v];
  CHECK(same, (pre + "every packet recovered bit-exact on VC0..3, in order").c_str());
  if (sec == 1)
    CHECK(d.prot > 0 && d.null_sa == 0 && d.prot + d.oid == d.cadus, (pre + "AE: every data frame protected").c_str());
  if (sec == 2)
    CHECK(d.null_sa > 0 && d.prot == 0 && d.null_sa + d.oid == d.cadus,
          (pre + "every data frame carries the null SA (SPI 0, IV and MAC zero, data clear)").c_str());
  CHECK(d.oid == e->dl.oid_frames() && e->dl.refused() == 0, (pre + "OID frame count, no SDLS refusal").c_str());

  // CFDP: reassemble the file from the VC3 packets with the flight's PDU parser.
  std::vector<std::string> lines;
  char hdr[512];
  std::snprintf(hdr, sizeof hdr,
                "stream %s framing=%s sdls=%s epoch=%u cadus=%u oid=%u protected=%u null_sa=%u ident=%s",
                file, fr == FRAMING_USLP ? "USLP" : "TM", sec == 1 ? "AE" : sec == 2 ? "NULL" : "NONE",
                (unsigned)e->tm.epoch(), (unsigned)d.cadus, (unsigned)d.oid, (unsigned)d.prot,
                (unsigned)d.null_sa, hex((const uint8_t*)idle, std::strlen(idle)).c_str());
  lines.push_back(hdr);
  lines.insert(lines.end(), e->lines.begin(), e->lines.end());
  if (partition != 0) {
    Bytes rx;
    uint32_t size = 0, sum = 0, got_pdus = 0;
    bool eof = false, meta = false, parse_ok = true;
    for (const Bytes& pk : d.pkts[3]) {
      CfdpPdu p;
      const size_t ul = pk.size() - kSpPriHdrLen - kSpSecHdrLen;
      parse_ok &= (get_u16(pk.data()) & 0x7FF) == APID_CFDP &&
                  cfdp_parse(pk.data() + kSpPriHdrLen + kSpSecHdrLen, ul, p) && p.hdr.seq == cseq;
      if (!parse_ok) break;
      ++got_pdus;
      if (p.hdr.file_data) {
        if (rx.size() < p.offset + p.data_len) rx.resize(p.offset + p.data_len);
        std::memcpy(rx.data() + p.offset, p.data, p.data_len);
      } else if (p.directive == kCfdpDirMetadata) {
        meta = std::string((const char*)p.src_name, p.src_name_len) == cname;
        size = p.file_size;
      } else if (p.directive == kCfdpDirEof) {
        eof = p.condition == kCfdpCondNoError && p.file_size == size;
        sum = p.checksum;
      }
    }
    CHECK(parse_ok && meta && eof && rx == e->ssr_file && size == e->ssr_file.size() &&
              sum == cfdp_checksum(0, 0, e->ssr_file.data(), e->ssr_file.size()) && got_pdus == pdus,
          (pre + "CFDP file = the recorded packets, size and modular checksum match").c_str());
    std::snprintf(hdr, sizeof hdr, "cfdp %s seq=%lu name=%s size=%lu checksum=%lu pdus=%u content=", file,
                  (unsigned long)cseq, cname, (unsigned long)size, (unsigned long)sum, (unsigned)got_pdus);
    lines.push_back(std::string(hdr) + hex(e->ssr_file.data(), e->ssr_file.size()));
  }
  return lines;
}

// =============================================================================
// TC frame builders (ground-equivalent, written from the standards' layouts)
// =============================================================================
// NOOP TC packet exactly as ground cmdparse.tc_packet() builds it: version 0, type 1, NO
// secondary header (PB-2.1), APID 0x0C0, sequence flags 11, count 0, length 0.
const uint8_t kNoopPkt[] = {0x10, 0xC0, 0xC0, 0x00, 0x00, 0x00, 0x01};

// TC v1 (232.0-B-4) or USLP (732.1-B-2) TC frame around `data`, optionally SDLS
// AUTH (SPI 1, HMAC-SHA-256/128) or AE (SPI 2, AES-256-GCM) protected.
Bytes tc_frame(bool uslp, bool bypass, bool control, uint8_t ns, const uint8_t* data, size_t n, int svc,
               uint32_t sn) {
  uint8_t master[32];
  master_key(master);
  Bytes body;                                            // what SDLS protects
  if (uslp) body.push_back(uslp_tfdf_byte(kUslpRuleNoSeg, control ? kUslpUpidCop1 : kUslpUpidPackets));
  body.insert(body.end(), data, data + n);
  const size_t ovh = svc == 1 ? kSdlsHdrLen + kSdlsMacLen : svc == 2 ? kSdlsAeOverhead : 0;
  const size_t hl = uslp ? (bypass ? 7u : 8u) : kTcPriHdrLen;
  const size_t len = hl + ovh + body.size() + 2;
  Bytes f(len, 0);
  if (uslp) {
    put_u32(f.data(), 0xC0000000u | (uint32_t)kSpacecraftId << 12 | 1u << 11);   // dest = 1, VCID 0
    put_u16(f.data() + 4, (uint16_t)(len - 1));
    f[6] = (uint8_t)((bypass ? 0x80 : 0) | (control ? 0x40 : 0) | (bypass ? 0 : 1));
    if (!bypass) f[7] = ns;
  } else {
    put_u16(f.data(), (uint16_t)((bypass ? 1u : 0u) << 13 | (control ? 1u : 0u) << 12 | kSpacecraftId));
    put_u16(f.data() + 2, (uint16_t)(len - 1));
    f[4] = ns;
  }
  uint8_t* sh = f.data() + hl;
  if (svc == 0) {
    std::memcpy(sh, body.data(), body.size());
  } else if (svc == 1) {
    put_u16(sh, kSdlsSpiAuth);
    put_u32(sh + 2, sn);
    std::memcpy(sh + kSdlsHdrLen, body.data(), body.size());
    uint8_t mac[32];
    hmac_sha256(master, 32, f.data(), hl, sh, kSdlsHdrLen, sh + kSdlsHdrLen, body.size(), mac);
    std::memcpy(sh + kSdlsHdrLen + body.size(), mac, kSdlsMacLen);
  } else {
    uint8_t k[32];
    sdls_derive_key(master, 32, kSdlsLabelTcAe, k);
    AesGcm g;
    g.set_key(k);
    put_u16(sh, kSdlsSpiTcAe);
    sdls_iv(kSdlsIvDirTc, 0, sn, sh + 2);
    uint8_t* ct = sh + kSdlsAeHdrLen;
    std::memcpy(ct, body.data(), body.size());
    g.encrypt(sh + 2, f.data(), hl + kSdlsAeHdrLen, ct, body.size(), ct + body.size());
  }
  put_u16(f.data() + len - 2, crc16_ccitt(f.data(), len - 2));
  return f;
}
void fix_fecf(Bytes& f) { put_u16(f.data() + f.size() - 2, crc16_ccitt(f.data(), f.size() - 2)); }
Bytes cltu(const Bytes& frame) {
  Bytes c = {0xEB, 0x90};
  for (size_t i = 0; i < frame.size(); i += 7) {
    uint8_t blk[7], enc[8];
    for (size_t j = 0; j < 7; ++j) blk[j] = i + j < frame.size() ? frame[i + j] : 0x55;
    bch_encode(blk, enc);
    c.insert(c.end(), enc, enc + 8);
  }
  c.insert(c.end(), kCltuTail, kCltuTail + 8);
  return c;
}

const char* stage_name(TcStage s) {
  switch (s) {
    case TcStage::NO_CLTU: return "NO_CLTU";
    case TcStage::FRAME_REJECTED: return "FRAME_REJECTED";
    case TcStage::SDLS_REJECTED: return "SDLS_REJECTED";
    case TcStage::TFDF_REJECTED: return "TFDF_REJECTED";
    case TcStage::FARM: return "FARM";
  }
  return "?";
}
const char* sdls_name(Sdls::Result r) {
  static const char* const k[] = {"OK", "DISABLED", "BAD_LENGTH", "BAD_SPI", "REPLAY", "BAD_MAC", "BAD_IV"};
  return (unsigned)r < 7 ? k[r] : "?";
}
const char* verdict_name(Farm1::Verdict v) {
  static const char* const k[] = {"ACCEPT", "DISCARD_RETRANSMIT", "DISCARD_NEG_WINDOW", "DISCARD_LOCKOUT",
                                  "ACCEPT_BYPASS", "CONTROL_UNLOCK", "CONTROL_SET_VR", "CONTROL_INVALID"};
  return (unsigned)v < 8 ? k[v] : "?";
}

// =============================================================================
// command_table.txt generation
// =============================================================================
struct Arg {
  uint8_t width;          // octets: 1, 2 or 4
  bool sgn;               // signed (i16)
  long long lo, hi;       // valid range
  long long base;         // value used while another argument is varied
};
struct Op {
  uint8_t opcode;
  std::vector<Arg> args;
};
void put_arg(Bytes& b, const Arg& a, long long v) {
  const uint32_t u = (uint32_t)v;
  if (a.width == 4) { b.push_back((uint8_t)(u >> 24)); b.push_back((uint8_t)(u >> 16)); }
  if (a.width >= 2) b.push_back((uint8_t)(u >> 8));
  b.push_back((uint8_t)u);
}
bool representable(const Arg& a, long long v) {
  if (a.sgn) return v >= -32768 && v <= 32767;
  const long long max = a.width == 1 ? 0xFF : a.width == 2 ? 0xFFFF : 0xFFFFFFFFLL;
  return v >= 0 && v <= max;
}
Bytes ud_of(uint8_t op, const std::vector<Arg>& args, const std::vector<long long>& v) {
  Bytes b = {op};
  for (size_t i = 0; i < args.size(); ++i) put_arg(b, args[i], v[i]);
  return b;
}

}  // namespace

// =============================================================================
// Checks
// =============================================================================
HOST_TEST(apid_index_map) {
  const uint16_t apids[kNumApids] = {APID_HK, APID_RF, APID_EVR, APID_CMDVER, APID_TIMECORR, APID_RFSCAN,
                                     APID_MAG, APID_ATT, APID_SPEC, APID_ENV, APID_SPECBURST, APID_CFDP};
  bool ok = true;
  for (uint8_t i = 0; i < kNumApids; ++i) ok &= apid_index(apids[i]) == i;
  CHECK(ok, "apid_index: HK..CFDP -> 0..11 in the PB-1.1 order");
  const uint16_t other[] = {0x000, 0x016, 0x01F, 0x025, 0x031, APID_TC, kApidIdle, 0xFFFF};
  bool none = true;
  for (uint16_t a : other) none &= apid_index(a) == kApidIndexNone;
  CHECK(none, "apid_index: every other APID (TC, idle, unknown) -> 0xFF");
  CHECK(std::strcmp(fsw_mode_name(MODE_ENCOUNTER), "ENCOUNTER") == 0 && std::strcmp(fsw_mode_name(5), "?") == 0,
        "fsw_mode_name: names, '?' out of range");
}

HOST_TEST(packet_layouts_v3) {
  uint8_t b[kMaxPayload];
  HkPacket h = hk_known();
  h.vc3_backlog = 0x5152; h.sdls_tc_service = 0x53; h.sdls_tm_service = 0x54; h.sdls_auth_fail = 0x5556;
  h.sdls_last_sn = 0x5758595Au; h.sdls_tm_epoch = 0x5B5C; h.sdls_tm_state = 0x5D; h.cfdp_state = 0x5E;
  h.cfdp_seq = 0x5F606162u; h.cfdp_progress_permille = 0x6364; h.seq_state = 0x65; h.seq_count = 0x66;
  h.seq_next_dt_s = 0x6768; h.mag_bist = 0x69; h.magcal_state = 0x6A; h.beacon_level = 0x6B;
  const size_t hn = pack_hk(h, b);
  bool hok = hn == 78 && hn == kHkLen;
  for (uint8_t i = 0; i < 27 && hok; ++i) hok = b[51 + i] == (uint8_t)(0x51 + i);   // offsets 51..77
  CHECK(hok && b[30] == 17 && get_u16(b + 39) == 512 && get_u16(b + 45) == 10,
        "HK: 78 octets, v3 fields at offsets 51..77 (PB-1.3)");
  RfPacket r = rf_known(1);
  r.radio_model = 0xA0; r.framing = 0xA5; r.uplink_snr_cdb = (int16_t)0xA1A2;
  r.uplink_freq_err_hz = (int32_t)0xA3A4A5A6;
  r.noise_floor_dbm = (int16_t)0xA7A8; r.lbt_busy = 0xA9AA; r.flags = 0xAB;
  const size_t rn = pack_rf(r, b);
  CHECK(rn == 49 && rn == kRfLen && b[0] == 0xA0 && b[5] == 0xA5 && get_u16(b + 26) == 0xA1A2 &&
            get_u32(b + 28) == 0xA3A4A5A6u && get_u16(b + 32) == 0xA7A8 && get_u16(b + 46) == 0xA9AA &&
            b[48] == 0xAB && b[44] == 200 && b[45] == 9,
        "RF: 49 octets, model/framing/SNR/frequency error/noise/LBT/flags at PB-1.4 offsets");
  const size_t en = pack_env(env_known(), b);
  CHECK(en == 24 && en == kEnvLen && b[0] == 0x0F && get_u32(b + 1) == 100125 && get_u16(b + 5) == (uint16_t)-150 &&
            get_u16(b + 7) == 4500 && get_u16(b + 9) == 77 && get_u16(b + 11) == 80 && get_u32(b + 13) == 600 &&
            get_u16(b + 17) == 125 && get_u32(b + 19) == 150000 && b[23] == 3,
        "ENV: 24 octets at PB-1.11 offsets");
  CHECK(pack_mag(mag_known(0x3F), b) == kMagLen && pack_att(att_known(0x7F), b) == kAttLen &&
            pack_timecorr(TimeCorrPacket{1, 2, 3, 4, 5}, b) == kTimeCorrLen &&
            pack_cmdver(CmdVerPacket{kCmdVerSeqStored, 1, 2, 0, 9}, b) == kCmdVerLen && b[0] == 0xFE,
        "MAG 46, ATT 37, TIMECORR 10, CMDVER 8 (stored-sequence tc_seq 0xFE)");
  const int8_t nz[kRfscanMaxChannels + 1] = {0};
  CHECK(pack_rfscan(0, 0, 1, nz, b) == 0 && pack_rfscan(0, kRfscanMaxChannels + 1, 1, nz, b) == 0 &&
            pack_rfscan(0, 3, 1, nullptr, b) == 0 && pack_rfscan(0, kRfscanMaxChannels, 1, nz, b) == 88,
        "RFSCAN: count 1..84 only (0 / 85 / null refused), 4 + count octets");
  char longtext[200];
  std::memset(longtext, 'x', sizeof longtext - 1);
  longtext[sizeof longtext - 1] = '\0';
  CHECK(pack_evr(2, 0x0A06, longtext, b) == 3 + kEvrTextMax && pack_evr(1, 1, nullptr, b) == 3 &&
            b[0] == 1 && get_u16(b + 1) == 1,
        "EVR: text truncated at 80 characters, null text -> header only");
  CHECK(tlm_na_i16(INT16_MIN) == kNaI16 && tlm_na_i16(-5) == -5 && tlm_na_i32(INT32_MIN) == kNaI32,
        "radio-API n/a (INT16_MIN / INT32_MIN) -> packet sentinels");
  CHECK(kMaxPayload == 146 && kCfdpMaxPduLen <= kMaxPayload && kSpecBurstMaxLen == 124 && kSpecV31Len == 129,
        "kMaxPayload 146 holds SPEC v3.1 (129), SPECBURST (124) and every CFDP PDU (146)");
}

// The fixed-layout packets of the ground dictionary, built by the flight packers.
HOST_TEST(tlm_vectors) {
  FILE* f = std::fopen((out + "/tlm_vectors.txt").c_str(), "w");
  if (!f) { CHECK(false, "cannot write tlm_vectors.txt"); return; }
  const CucTime t{4242, 32768};
  uint8_t pl[kMaxPayload], pkt[256];
  auto line = [&](const char* name, uint16_t apid, size_t len, const std::string& expect) {
    const size_t m = build_tm_packet(pkt, sizeof pkt, apid, 7, t, pl, len);
    std::fprintf(f, "%s %s %s\n", name, hex(pkt, m).c_str(), expect.c_str());
  };
  const HkPacket hk = hk_known();
  line("HK", APID_HK, pack_hk(hk, pl), hk_expect(hk));
  RfPacket rf = rf_known(0);
  rf.noise_floor_dbm = -118;
  line("RF", APID_RF, pack_rf(rf, pl), rf_expect(rf));
  RfPacket rf_na = rf_known(1);
  rf_na.uplink_rssi_dbm = tlm_na_i16(kRadioNaI16);
  rf_na.uplink_snr_cdb = kNaI16;
  rf_na.uplink_freq_err_hz = tlm_na_i32(kRadioNaI32);
  line("RF", APID_RF, pack_rf(rf_na, pl), rf_expect(rf_na));
  std::string mexp = kMagExpect;
  kv(mexp, "flags", num(0x3B));
  line("MAG", APID_MAG, pack_mag(mag_known(0x3B), pl), mexp);
  std::string aexp = kAttExpect;
  kv(aexp, "flags", num(0x7F));
  line("ATT", APID_ATT, pack_att(att_known(0x7F), pl), aexp);
  const EnvPacket env = env_known();
  line("ENV", APID_ENV, pack_env(env, pl), env_expect(env));
  EnvPacket env_na{};
  env_na.flags = ENV_F_TH;
  env_na.p_cPa = kNaU32; env_na.t_cC = 2234; env_na.rh_cpct = kNaU16; env_na.iaq = kNaU16; env_na.iaq_s = kNaU16;
  env_na.co2eq_ppm = kNaU32; env_na.bvoc_cppm = kNaU16; env_na.gas_ohm = kNaU32; env_na.accuracy = 0;
  line("ENV", APID_ENV, pack_env(env_na, pl),
       "flags=4,p=None,t=22.34,rh=None,iaq=None,iaq_s=None,co2eq=None,bvoc=None,gas=None,accuracy=0");
  const CmdVerPacket cv{kCmdVerSeqStored, OP_PING, STAGE_EXECUTED, CE_OK, 0xDEADBEEF};
  line("CMDVER", APID_CMDVER, pack_cmdver(cv, pl), "tc_seq=254,opcode=9,stage=2,error_code=0,ground_tag=3735928559");
  const CmdVerPacket cv2{0x12, OP_CFDPPUT, STAGE_FAILED, CE_BUSY, 0};
  line("CMDVER", APID_CMDVER, pack_cmdver(cv2, pl), "tc_seq=18,opcode=23,stage=3,error_code=7,ground_tag=0");
  const int8_t noise[5] = {-118, -121, -97, -128, -120};
  line("RFSCAN", APID_RFSCAN, pack_rfscan(20, 5, 812, noise, pl),
       "first_ch=20,count=5,duration_ms=812,noise=-118;-121;-97;None;-120");
  line("TIMECORR", APID_TIMECORR, pack_timecorr(TimeCorrPacket{17, 4000, 1234, 2, 950}, pl),
       "ref_mcfc=17,sclk_coarse=4000,sclk_fine=1234,air_rate_code=2,ref_airtime_ms=950");
  std::fclose(f);
  CHECK(true, "wrote tlm_vectors.txt (HK 78, RF 49 incl. n/a, MAG, ATT, ENV 24 incl. n/a, CMDVER, RFSCAN, TIMECORR)");
}

HOST_TEST(tc_header_and_user_data) {
  auto mk = [](const Bytes& user) {
    Bytes p = {0x10, 0xC0, 0xC0, 0x00, 0x00, (uint8_t)(user.size() - 1)};
    p.insert(p.end(), user.begin(), user.end());
    return p;
  };
  Command c;
  Bytes p = mk({OP_PING, 0x01, 0x02, 0x03, 0x04});
  CHECK(parse_tc_packet(p.data(), p.size(), c) == CE_OK && c.opcode == OP_PING && c.a32 == 0x01020304 &&
            c.nargs == 4 && c.args == p.data() + 7,
        "CMD: PING tag decoded, args/nargs point into the packet");
  const uint8_t* u = nullptr;
  size_t n = 0;
  CHECK(parse_tc_header(p.data(), p.size(), u, n) == CE_OK && u == p.data() + 6 && n == 5,
        "CMD: header stage gives the user data");
  Bytes bad = p; bad[0] = 0x00;                                   // type 0 (TM)
  CHECK(parse_tc_packet(bad.data(), bad.size(), c) == CE_BAD_ARGUMENT, "CMD: packet type TM refused");
  bad = p; bad[0] = 0x18;                                         // secondary-header flag
  CHECK(parse_tc_packet(bad.data(), bad.size(), c) == CE_BAD_ARGUMENT, "CMD: secondary-header flag refused");
  bad = p; bad[1] = 0xC1;                                         // APID 0x0C1
  CHECK(parse_tc_packet(bad.data(), bad.size(), c) == CE_BAD_ARGUMENT, "CMD: foreign APID refused");
  bad = p; bad[2] = 0x40;                                         // sequence flags 01
  CHECK(parse_tc_packet(bad.data(), bad.size(), c) == CE_BAD_ARGUMENT, "CMD: segmented packet refused");
  bad = p; bad.push_back(0);                                      // one octet more than the header says
  CHECK(parse_tc_packet(bad.data(), bad.size(), c) == CE_BAD_LENGTH, "CMD: trailing octet refused");
  CHECK(parse_tc_packet(p.data(), p.size() - 1, c) == CE_BAD_LENGTH &&
            parse_tc_packet(p.data(), 6, c) == CE_BAD_LENGTH &&
            parse_tc_packet(nullptr, 10, c) == CE_BAD_LENGTH,
        "CMD: truncated / header-only / null packet -> BAD_LENGTH");
  CHECK(parse_user_data(nullptr, 1, c) == CE_BAD_LENGTH && parse_user_data(p.data() + 6, 0, c) == CE_BAD_LENGTH,
        "CMD: empty user data -> BAD_LENGTH");
  const uint8_t spec[] = {OP_SPECCFG, 255, 59, 3, 0x0B, 4};
  CHECK(parse_user_data(spec, sizeof spec, c) == CE_OK && c.a8 == 255 && c.b8 == 59 && c.c8 == 3 && c.d8 == 0x0B &&
            c.e8 == 4,
        "CMD: SPECCFG five arguments in a8..e8");
  const uint8_t seqctl[] = {OP_SEQCTRL, 3, 31};
  const uint8_t cfdp[] = {OP_CFDPPUT, 0, 0x01, 0x00};
  const uint8_t scan[] = {OP_RFSCAN, 10, 20};
  CHECK(parse_user_data(seqctl, 3, c) == CE_OK && c.a8 == 3 && c.b8 == 31 &&
            parse_user_data(cfdp, 4, c) == CE_OK && c.a8 == 0 && c.a16 == 256 &&
            parse_user_data(scan, 3, c) == CE_OK && c.a8 == 10 && c.b8 == 20,
        "CMD: SEQCTRL a8/b8, CFDPPUT a8/a16, RFSCAN a8/b8 mapping");
  uint8_t mc[20] = {OP_MAGCAL};
  CHECK(parse_user_data(mc, 20, c) == CE_OK && c.nargs == 19 && c.args == mc + 1,
        "CMD: MAGCAL 19 octets reach the executive as args/nargs");
  CHECK(validate_stored_command(nullptr, spec, sizeof spec) == CE_OK &&
            validate_stored_command(nullptr, scan, 2) == CE_BAD_LENGTH,
        "CMD: stored-command validator = parse_user_data");
  // The validator is the one CommandSequencer uses: install it and load a SPECCFG.
  CommandSequencer s;
  s.reset();
  s.set_validator(&validate_stored_command, nullptr);
  uint8_t blk[kSeqLoadLen] = {4, kSeqFlagRelative, 0, 0, 0, 10, sizeof spec};
  std::memcpy(blk + 7, spec, sizeof spec);
  SeqLoadReport rep;
  CHECK(s.load(blk, sizeof blk, 1000, rep) == SEQ_OK, "CMD: SEQLOAD of a SPECCFG through the flight validator");
  blk[7 + 4] = 12;                                                // led_cur 12: IR above its rating
  CHECK(s.load(blk, sizeof blk, 1000, rep) == SEQ_ERR_COMMAND && rep.cmd_error == CE_BAD_ARGUMENT,
        "CMD: a stored SPECCFG with an unsafe LED current is refused at load time");
}

// Every opcode at every boundary; writes command_table.txt for the ground cross-check.
HOST_TEST(command_table) {
  const Arg u8_any{1, false, 0, 255, 0};
  std::vector<Op> ops = {
      {OP_NOOP, {}},
      {OP_MODE, {{1, false, 1, 4, 2}}},
      {OP_TXPWR, {{1, false, 0, 3, 1}}},
      {OP_AIRRATE, {{1, false, 0, 7, 2}, {2, false, 60, 3600, 600}}},
      {OP_FPERIOD, {{2, false, 500, 60000, 1000}}},
      {OP_PKTRATE, {{2, false, APID_HK, APID_ENV, APID_HK}, {2, false, 0, 3600, 30}}},
      {OP_PLAYBACK, {{1, false, 0, 1, 1}}},
      {OP_SSRCLEAR, {}},
      {OP_PING, {{4, false, 0, 0xFFFFFFFFLL, 0x12345678}}},
      {OP_SENSORS, {{2, false, 0, kSensorInstrumentMask, kSensorInstrumentMask}}},
      {OP_RSTCNT, {}},
      {OP_REBOOT, {{2, false, kRebootMagic, kRebootMagic, kRebootMagic}}},
      {OP_MAGCC, {{2, false, 50, 400, 200}}},
      {OP_CMDLOSS, {{4, false, 0, 604800, 86400}}},
      {OP_FDIRMASK, {{2, false, 0, 0xFFFF, 0xFFFF}}},
      {OP_TXINHIBIT, {{2, false, 10, 3600, 60}}},
      {OP_EVRLEVEL, {{1, false, 0, 4, 1}}},
      {OP_TIMECORR, {}},
      {OP_RFSCAN, {{1, false, 0, 83, 0}, {1, false, 0, 83, 83}}},
      {OP_HIBERNATE, {{2, false, 60, 3600, 600}}},
      {OP_CHANNEL, {{1, false, 0, 83, 23}, {2, false, 60, 3600, 600}}},
      {OP_SPECCFG, {{1, false, 0, 255, 255}, {1, false, 1, 255, 59}, {1, false, 0, 7, 0}, {1, false, 0, 11, 0},
                    {1, false, 0, 4, 0}}},
      {OP_CFDPPUT, {{1, false, 0, 2, 0}, {2, false, 0, 0xFFFF, 0}}},
      {OP_CFDPCANCEL, {}},
      {OP_RADIOOPT, {{1, false, 0, 3, 3}}},
      {OP_SPECMODE, {{1, false, 0, 4, 0}, {1, false, 1, 60, 1}}},
      {OP_SPECBASE, {{2, false, 0, 9000, 500}}},
      {OP_SPECDIAG, {{1, false, 0, 2, 0}, {1, false, 0, 32, 8}}},
      {OP_SEQCTRL, {{1, false, 0, 3, 3}, {1, false, 0, 31, 0}}},
      {OP_MAGBIST, {{1, false, 0, 2, 2}}},
      {OP_MAGCAL, {{1, false, 0, 1, 0}, {2, true, -30000, 30000, 0}, {2, true, -30000, 30000, 0},
                   {2, true, -30000, 30000, 0}, {2, true, 8192, 32767, 16384}, {2, true, 8192, 32767, 16384},
                   {2, true, 8192, 32767, 16384}, {2, true, -8192, 8192, 0}, {2, true, -8192, 8192, 0},
                   {2, true, -8192, 8192, 0}}},
  };
  (void)u8_any;
  struct Row { Bytes ud; uint8_t want; };
  std::vector<Row> rows;
  auto bad_value = [](uint8_t op) { return op == OP_REBOOT ? (uint8_t)CE_BAD_MAGIC : (uint8_t)CE_BAD_ARGUMENT; };
  for (const Op& o : ops) {
    std::vector<long long> base;
    for (const Arg& a : o.args) base.push_back(a.base);
    rows.push_back({ud_of(o.opcode, o.args, base), CE_OK});
    for (size_t i = 0; i < o.args.size(); ++i) {
      const long long cand[4] = {o.args[i].lo, o.args[i].hi, o.args[i].lo - 1, o.args[i].hi + 1};
      for (int k = 0; k < 4; ++k) {
        if (!representable(o.args[i], cand[k])) continue;
        std::vector<long long> v = base;
        v[i] = cand[k];
        rows.push_back({ud_of(o.opcode, o.args, v), k < 2 ? (uint8_t)CE_OK : bad_value(o.opcode)});
      }
    }
    Bytes full = ud_of(o.opcode, o.args, base);
    Bytes longer = full;
    longer.push_back(0);
    rows.push_back({longer, CE_BAD_LENGTH});
    if (full.size() > 1) rows.push_back({Bytes(full.begin(), full.end() - 1), CE_BAD_LENGTH});
  }
  // "check" rules (PB-2.3), identical in the ground CommandDef.check functions.
  const Bytes checks_ok[] = {
      {OP_PKTRATE, 0x00, 0x14, 0, 60}, {OP_PKTRATE, 0x00, 0x22, 0, 60}, {OP_SENSORS, 0x01, 0x00},
      {OP_RFSCAN, 10, 10}, {OP_SPECCFG, 3, 1, 7, 0x08, 4}, {OP_SPECCFG, 0, 255, 0, 0x03, 0},
      {OP_SPECMODE, 1, 2}, {OP_SPECMODE, 1, 4}, {OP_SPECMODE, 2, 16}, {OP_SPECMODE, 3, 16}, {OP_SPECMODE, 4, 60},
      {OP_SEQCTRL, 3, 5}, {OP_MAGCAL, 1, 0x75, 0x30, 0x8A, 0xD0, 0, 0, 0x40, 0, 0x40, 0, 0x40, 0, 0x20, 0, 0, 0, 0, 0},
  };
  const Bytes checks_bad[] = {
      {OP_PKTRATE, 0x00, 0x12, 0, 60}, {OP_PKTRATE, 0x00, 0x15, 0, 60}, {OP_PKTRATE, 0x00, 0x1F, 0, 60},
      {OP_SENSORS, 0x00, 0x02}, {OP_SENSORS, 0x80, 0x00}, {OP_SENSORS, 0x03, 0x82}, {OP_RFSCAN, 20, 10},
      {OP_SPECCFG, 4, 59, 0, 0, 0}, {OP_SPECCFG, 254, 59, 0, 0, 0}, {OP_SPECCFG, 255, 59, 0, 0x0C, 0},
      {OP_SPECCFG, 255, 59, 0, 0x10, 0}, {OP_SPECCFG, 255, 59, 0, 0x0F, 0}, {OP_SPECMODE, 1, 1},
      {OP_SPECMODE, 1, 5}, {OP_SPECMODE, 2, 17}, {OP_SPECMODE, 3, 17}, {OP_SPECMODE, 4, 61},
      {OP_SPECMODE, 0, 61}, {OP_SEQCTRL, 0, 5}, {OP_SEQCTRL, 1, 1}, {OP_SEQCTRL, 2, 31},
      // MAGCAL with a singular soft-iron matrix (all elements 0.5): not positive definite.
      {OP_MAGCAL, 0, 0, 0, 0, 0, 0, 0, 0x20, 0, 0x20, 0, 0x20, 0, 0x20, 0, 0x20, 0, 0x20, 0},
  };
  for (const Bytes& b : checks_ok) rows.push_back({b, CE_OK});
  for (const Bytes& b : checks_bad) rows.push_back({b, CE_BAD_ARGUMENT});
  // SEQLOAD: relative time 10 s, NOOP; only the static block checks apply.
  auto seqload = [](uint8_t slot, uint8_t flags, uint8_t len, size_t total) {
    Bytes b = {OP_SEQLOAD, slot, flags, 0, 0, 0, 10, len, OP_NOOP};
    b.resize(total, 0);
    return b;
  };
  rows.push_back({seqload(0, 1, 1, 20), CE_OK});
  rows.push_back({seqload(31, 1, 1, 20), CE_OK});
  rows.push_back({seqload(32, 1, 1, 20), CE_BAD_ARGUMENT});
  rows.push_back({seqload(0, 2, 1, 20), CE_BAD_ARGUMENT});
  rows.push_back({seqload(0, 1, 0, 20), CE_BAD_ARGUMENT});
  rows.push_back({seqload(0, 1, 13, 20), CE_BAD_ARGUMENT});
  rows.push_back({seqload(0, 1, 1, 19), CE_BAD_LENGTH});
  rows.push_back({seqload(0, 1, 1, 21), CE_BAD_LENGTH});
  rows.push_back({Bytes{0x00}, CE_UNKNOWN_OPCODE});
  rows.push_back({Bytes{0x21}, CE_UNKNOWN_OPCODE});
  rows.push_back({Bytes{0xFF, 1, 2}, CE_UNKNOWN_OPCODE});

  FILE* f = std::fopen((out + "/command_table.txt").c_str(), "w");
  if (!f) { CHECK(false, "cannot write command_table.txt"); return; }
  int mismatches = 0;
  std::string first;
  bool opcodes_seen[kOpcodeMax + 1] = {false};
  for (const Row& r : rows) {
    Command c;
    uint8_t got = (uint8_t)parse_user_data(r.ud.data(), r.ud.size(), c);
    if (got == CE_OK && c.opcode == OP_MAGCAL) {                  // MagCalStore::load checks
      uint8_t unit = 0;
      MagCalCoeffs mcc;
      got = magcal_cmd_error(magcal_parse(c.args, c.nargs, unit, mcc));
    } else if (got == CE_OK && c.opcode == OP_SEQLOAD) {          // CommandSequencer::load checks
      CommandSequencer s;
      s.reset();
      s.set_validator(&validate_stored_command, nullptr);
      SeqLoadReport rep;
      got = seq_cmd_error(s.load(c.args, c.nargs, 1000, rep));
    }
    if (r.ud[0] <= kOpcodeMax) opcodes_seen[r.ud[0]] = true;
    if (got != r.want) {
      if (!mismatches) first = hex(r.ud.data(), r.ud.size()) + " got " + num(got) + " want " + num(r.want);
      std::printf("      command_table mismatch: %s got %u want %u\n", hex(r.ud.data(), r.ud.size()).c_str(),
                  (unsigned)got, (unsigned)r.want);
      ++mismatches;
    }
    std::fprintf(f, "%s %u\n", hex(r.ud.data(), r.ud.size()).c_str(), (unsigned)got);
  }
  std::fclose(f);
  bool all = true;
  for (uint8_t op = 1; op <= kOpcodeMax; ++op) all &= opcodes_seen[op];
  CHECK(all && rows.size() > 250,
        ("command_table.txt: all 32 opcodes, " + num((long long)rows.size()) + " rows").c_str());
  CHECK(mismatches == 0, ("parse_user_data matches PB-2.3 at every boundary and check rule" +
                          (mismatches ? " (first: " + first + ")" : std::string())).c_str());
}

HOST_TEST(ssr_export) {
  std::unique_ptr<Ssr> s(new Ssr());                               // 4 KiB fallback ring
  Bytes model;
  uint8_t p[300];
  for (int i = 0; i < 9; ++i) {
    const uint16_t len = (uint16_t)(20 + 17 * i);
    for (uint16_t k = 0; k < len; ++k) p[k] = (uint8_t)(i * 31 + k);
    s->record(p, len);
    model.insert(model.end(), p, p + len);
  }
  s->record(p, 0);
  s->record(nullptr, 10);
  CHECK(s->packets() == 9, "SSR: empty / null records ignored");
  CHECK(s->begin_export() && s->exporting() && s->export_size() == model.size(), "SSR: export size = sum of packets");
  CHECK(!s->begin_export() && !s->start_playback(), "SSR: playback and a second export refused while exporting");
  const uint32_t drops = s->paused_drops();
  s->record(p, 50);
  CHECK(s->paused_drops() == drops + 1 && s->export_size() == model.size() && s->dropped() == 0,
        "SSR: a packet recorded during the export is a paused drop, the snapshot is unchanged");
  Bytes got;
  uint8_t chunk[13];
  bool seq_ok = s->export_read(5, chunk, 13) == 0;                 // wrong offset
  for (int guard = 0; guard < 1000; ++guard) {
    const size_t k = s->export_read((uint32_t)got.size(), chunk, sizeof chunk);
    if (k == 0) break;
    got.insert(got.end(), chunk, chunk + k);
  }
  seq_ok &= s->export_read((uint32_t)got.size(), chunk, sizeof chunk) == 0 && s->export_read(0, chunk, 13) == 0;
  CHECK(seq_ok && got == model, "SSR: sequential 13-octet reads give the concatenated packets; any other offset -> 0");
  s->end_export();
  CHECK(!s->exporting() && s->export_read(0, chunk, 13) == 0 && s->start_playback() && s->playing() &&
            !s->begin_export(),
        "SSR: after end_export playback runs again and excludes an export");
  s->stop_playback();
  // Wrapped ring: record past the end, the export is the retained suffix.
  std::unique_ptr<Ssr> w(new Ssr());
  std::vector<Bytes> all;
  for (int i = 0; i < 60; ++i) {
    const uint16_t len = (uint16_t)(100 + (prng() % 120));
    Bytes b(len);
    for (uint16_t k = 0; k < len; ++k) b[k] = (uint8_t)prng();
    w->record(b.data(), len);
    all.push_back(b);
  }
  size_t used = 0, first = all.size();
  while (first > 0 && used + 2 + all[first - 1].size() <= Ssr::kFallbackSize) used += 2 + all[--first].size();
  Bytes want;
  for (size_t i = first; i < all.size(); ++i) want.insert(want.end(), all[i].begin(), all[i].end());
  CHECK(w->dropped() > 0 && w->packets() == all.size() - first, "SSR: ring wrapped, oldest packets overwritten");
  // Export of the wrapped ring through CFDP.
  CHECK(w->begin_export(), "SSR: export of a wrapped ring starts");
  SsrFileSource src(*w);
  CfdpSender tx;
  CHECK(src.size() == want.size() && tx.begin(cfdp_make_seq(3, 9), "ssr_p3_99.bin", &src),
        "CFDP: begin over SsrFileSource");
  Bytes file;
  uint32_t sum = 0, size = 0;
  uint8_t cond = 0xFF;
  for (int guard = 0; guard < 100 && tx.active(); ++guard) {
    uint8_t pdu[kCfdpMaxPduLen];
    const size_t n = tx.next_pdu(pdu, sizeof pdu);
    CfdpPdu pp;
    if (!cfdp_parse(pdu, n, pp)) break;
    if (pp.hdr.file_data) {
      if (file.size() < pp.offset + pp.data_len) file.resize(pp.offset + pp.data_len);
      std::memcpy(file.data() + pp.offset, pp.data, pp.data_len);
    } else if (pp.directive == kCfdpDirEof) {
      cond = pp.condition; sum = pp.checksum; size = pp.file_size;
    }
  }
  w->end_export();
  CHECK(!tx.active() && cond == kCfdpCondNoError && file == want && size == want.size() &&
            sum == cfdp_checksum(0, 0, want.data(), want.size()),
        "CFDP: the wrapped SSR exported intact (size, modular checksum)");
  // clear() during an export ends it: the sender closes with condition 4.
  CHECK(w->begin_export() && tx.begin(cfdp_make_seq(3, 10), "ssr_p3_100.bin", &src), "CFDP: second export starts");
  uint8_t pdu[kCfdpMaxPduLen];
  tx.next_pdu(pdu, sizeof pdu);                                    // Metadata
  w->clear();
  for (int guard = 0; guard < 10 && tx.active(); ++guard) tx.next_pdu(pdu, sizeof pdu);
  CHECK(!w->exporting() && !tx.active() && tx.last_condition() == kCfdpCondFilestoreRejection,
        "SSR: clear() ends a running export, the CFDP EOF reports condition 4");
}

HOST_TEST(downlink_scheduler) {
  std::unique_ptr<Downlink> dl(new Downlink());
  SdlsTm tm;
  uint8_t master[32];
  master_key(master);
  // Data-field lengths: TM 188 / USLP 183, minus 30 with AE (null SA keeps the geometry).
  dl->init(FRAMING_TM, nullptr, "DE N0CALL VGQ-1 ");
  const size_t tm_clear = dl->data_field_len();
  dl->init(FRAMING_USLP, nullptr, "");
  const size_t uslp_clear = dl->data_field_len();
  tm.init(SDLS_SVC_AE, master, 32);
  dl->init(FRAMING_TM, &tm, "");
  const size_t tm_ae = dl->data_field_len();
  dl->init(FRAMING_USLP, &tm, "");
  const size_t uslp_ae = dl->data_field_len();
  CHECK(tm_clear == 188 && uslp_clear == 183 && tm_ae == 158 && uslp_ae == 153,
        "Downlink: data field 188 / 183 / 158 / 153 (TM, USLP, TM AE, USLP AE)");
  char idle[kIdleTextMax + 1];
  CHECK(format_idle_text("N0CALL", idle, sizeof idle) == 16 && std::strcmp(idle, "DE N0CALL VGQ-1 ") == 0 &&
            format_idle_text(nullptr, idle, sizeof idle) == 9 && std::strcmp(idle, "DE VGQ-1 ") == 0 &&
            format_idle_text("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", idle, sizeof idle) == kIdleTextMax &&
            format_idle_text("N0CALL", idle, 6) == 5 && std::strcmp(idle, "DE N0") == 0 &&
            format_idle_text("N0CALL", nullptr, 8) == 0,
        "format_idle_text: \"DE <callsign> VGQ-1 \", truncated to 32 / the buffer");

  // Priority reserve: routine packets stop at 768 octets, priority packets may fill 1024.
  dl->init(FRAMING_TM, nullptr, "DE N0CALL VGQ-1 ");
  uint8_t pkt[100];
  build_idle_packet(pkt, sizeof pkt, 0);
  put_u16(pkt, 0x0810);                                            // a TM packet, APID 0x010
  int routine = 0;
  while (routine < 20 && dl->push(0, pkt, sizeof pkt)) ++routine;
  CHECK(routine == 7 && dl->backlog(0) == 700 && dl->overflows(0) == 1,
        "Downlink: routine pushes stop at kBufLen - 256 (7 x 100 octets), refusal counted");
  int prio = 0;
  while (prio < 20 && dl->push(0, pkt, sizeof pkt, true)) ++prio;
  CHECK(prio == 3 && dl->backlog(0) == 1000 && dl->overflows(0) == 2,
        "Downlink: priority packets use the 256-octet reserve, then refused");
  CHECK(!dl->push(4, pkt, sizeof pkt, true) && !dl->push(0, nullptr, 10) && !dl->push(1, pkt, 0) &&
            dl->backlog(7) == 0 && !dl->has_real_data(9) && dl->overflows(4) == 0,
        "Downlink: VC > 3, null or empty packets refused; VC > 3 queries give 0");

  // Anti-starvation: VC1 above 500 octets goes first after a VC0 frame.
  dl->init(FRAMING_TM, nullptr, "DE N0CALL VGQ-1 ");
  for (int k = 0; k < 7; ++k) dl->push(0, pkt, sizeof pkt);
  for (int k = 0; k < 7; ++k) dl->push(1, pkt, sizeof pkt);
  Clcw clcw;
  uint8_t cadu[kCaduLen];
  DownlinkFrame fi;
  std::string order;
  for (int k = 0; k < 7; ++k) {
    dl->next_cadu(clcw, cadu, fi);
    order += (char)('0' + fi.vcid);
  }
  // 700/700 octets: VC0 first (no previous frame), then VC1 after every VC0 frame while
  // VC1 holds > 500 (700, 512); at 324 engineering goes first again until VC0 is empty.
  CHECK(order == "0101001", ("Downlink: VC1 after each VC0 frame only while VC1 holds > 500 octets (" + order +
                             ")").c_str());
  // Priority among VC2 / VC3 and the counters.
  dl->init(FRAMING_TM, nullptr, "DE N0CALL VGQ-1 ");
  dl->push(3, pkt, sizeof pkt);
  dl->push(2, pkt, sizeof pkt);
  dl->next_cadu(clcw, cadu, fi);
  const uint8_t c0 = fi.count, v0 = fi.vcid;
  dl->next_cadu(clcw, cadu, fi);
  const uint8_t c1 = fi.count, v1 = fi.vcid;
  dl->next_cadu(clcw, cadu, fi);
  CHECK(v0 == 2 && v1 == 3 && fi.oid && fi.vcid == VC_IDLE && c0 == 0 && c1 == 1 && fi.count == 2 && dl->mcfc() == 3 &&
            dl->oid_frames() == 1 && dl->framing() == FRAMING_TM,
        "Downlink: VC2 before VC3 before OID; TM count = MCFC 0, 1, 2");
  dl->push(2, pkt, sizeof pkt);
  const uint32_t ovf = dl->overflows(2);
  dl->reset_vc(2);
  CHECK(dl->backlog(2) == 0 && !dl->has_real_data(2) && dl->overflows(2) == ovf, "Downlink: reset_vc(2) empties VC2");
  // USLP: OID on VCID 63 with the identification text; counts are per-VC frame counts.
  dl->init(FRAMING_USLP, nullptr, "DE N0CALL VGQ-1 ");
  dl->push(1, pkt, sizeof pkt);
  dl->next_cadu(clcw, cadu, fi);
  const bool first_vc1 = fi.vcid == 1 && fi.count == 0 && !fi.oid;
  dl->next_cadu(clcw, cadu, fi);
  const bool oid63 = fi.oid && fi.vcid == kUslpVcidOid && fi.count == 0;
  dl->push(1, pkt, sizeof pkt);
  dl->next_cadu(clcw, cadu, fi);
  uint8_t cb[kCodeblockLen];
  std::memcpy(cb, cadu + kAsmLen, kCodeblockLen);
  randomize(cb, kCodeblockLen);
  CHECK(first_vc1 && oid63 && fi.vcid == 1 && fi.count == 1 && fi.security == DL_SEC_CLEAR,
        "Downlink USLP: OID = VCID 63, counts are VCF counts per VC (TIMECORR ref)");
  dl->next_cadu(clcw, cadu, fi);
  std::memcpy(cb, cadu + kAsmLen, kCodeblockLen);
  randomize(cb, kCodeblockLen);
  CHECK(fi.oid && std::memcmp(cb + kUslpTmHdrLen + kUslpTfdfHdrLen, "DE N0CALL VGQ-1 DE N0CALL", 25) == 0,
        "Downlink USLP: the OID TFDZ repeats \"DE N0CALL VGQ-1 \"");
  // SDLS: OID while waiting for the epoch, then null SA, then AE.
  tm.init(SDLS_SVC_AE, master, 32);
  dl->init(FRAMING_TM, &tm, "DE N0CALL VGQ-1 ");
  dl->push(0, pkt, sizeof pkt, true);
  dl->next_cadu(clcw, cadu, fi);
  const bool waited = fi.oid && dl->backlog(0) == 100 && tm.frames_refused() == 0;
  tm.poll_epoch_timeout(120, 120);
  dl->next_cadu(clcw, cadu, fi);
  const bool null_sa = !fi.oid && fi.vcid == 0 && fi.security == DL_SEC_NULL_SA && tm.frames_null() == 1;
  CHECK(tm.set_epoch(9), "SDLS TM epoch after the fallback");
  dl->push(1, pkt, sizeof pkt);
  dl->next_cadu(clcw, cadu, fi);
  CHECK(waited && null_sa && !fi.oid && fi.vcid == 1 && fi.security == DL_SEC_AE && tm.frames_protected() == 1,
        "Downlink SDLS: OID while WAIT_EPOCH (no data popped), null SA after the timeout, AE once an epoch arrives");
}

HOST_TEST(tc_accept_chain) {
  uint8_t master[32];
  master_key(master);
  CHECK(std::strcmp(tc_parse_result_name(TC_OK), "OK") == 0 &&
            std::strcmp(tc_parse_result_name(TC_TOO_SHORT), "TOO_SHORT") == 0 &&
            std::strcmp(tc_parse_result_name(TC_BAD_VERSION), "BAD_VERSION") == 0 &&
            std::strcmp(tc_parse_result_name(TC_BAD_SCID), "BAD_SCID") == 0 &&
            std::strcmp(tc_parse_result_name(TC_BAD_LENGTH), "BAD_LENGTH") == 0 &&
            std::strcmp(tc_parse_result_name(TC_BAD_FECF), "BAD_FECF") == 0 &&
            std::strcmp(tc_parse_result_name(TC_BAD_HEADER), "BAD_HEADER") == 0 &&
            std::strcmp(tc_parse_result_name(TC_BAD_TFDF), "BAD_TFDF") == 0 &&
            std::strcmp(tc_parse_result_name((TcParseResult)8), "?") == 0 &&
            std::strcmp(tc_parse_result_name((TcParseResult)255), "?") == 0,
        "tc_parse_result_name: the 8 values, '?' beyond");
  uint8_t work[kTcWorkMin];
  for (int uslp = 0; uslp < 2; ++uslp) {
    for (int svc = 0; svc < 3; ++svc) {
      const std::string tag = std::string(uslp ? "USLP" : "TC") + "/" + (svc == 0 ? "NONE" : svc == 1 ? "AUTH" : "AE");
      Sdls sdls;
      sdls.configure((uint8_t)svc, master, 32, kSdlsSpiAuth);
      Farm1 farm;
      BchStats bch;
      // AD frame N(S) 0: accepted, the TC packet decodes.
      const Bytes f0 = tc_frame(uslp, false, false, 0, kNoopPkt, sizeof kNoopPkt, svc, 5);
      const Bytes c0 = cltu(f0);
      TcOutcome o = tc_accept(c0.data(), c0.size(), work, sizeof work, bch, sdls, farm);
      Command cmd;
      const bool ok0 = o.stage == TcStage::FARM && o.verdict == Farm1::ACCEPT && farm.vr() == 1 &&
                       o.frame.data_len == sizeof kNoopPkt &&
                       parse_tc_packet(o.frame.data, o.frame.data_len, cmd) == CE_OK && cmd.opcode == OP_NOOP &&
                       o.sdls == (svc ? Sdls::SDLS_OK : Sdls::SDLS_DISABLED);
      CHECK(ok0, (tag + ": AD frame accepted, NOOP decoded").c_str());
      // Replay of the same frame.
      o = tc_accept(c0.data(), c0.size(), work, sizeof work, bch, sdls, farm);
      if (svc) {
        CHECK(o.stage == TcStage::SDLS_REJECTED && o.sdls == Sdls::SDLS_REPLAY && farm.vr() == 1 &&
                  o.frame.length == f0.size(),
              (tag + ": replayed frame refused by SDLS, FARM untouched, header fields valid").c_str());
        // Forged: a ciphertext / data octet flipped, FECF recomputed (passes the frame layer).
        Bytes forged = tc_frame(uslp, false, false, 1, kNoopPkt, sizeof kNoopPkt, svc, 6);
        forged[forged.size() - 4] ^= 0x01;                          // inside the MAC / tag
        fix_fecf(forged);
        const Bytes cf = cltu(forged);
        const uint32_t sn = sdls.last_sn();
        o = tc_accept(cf.data(), cf.size(), work, sizeof work, bch, sdls, farm);
        CHECK(o.stage == TcStage::SDLS_REJECTED && o.sdls == Sdls::SDLS_BAD_MAC && farm.vr() == 1 &&
                  farm.state() == Farm1::S1_OPEN && sdls.last_sn() == sn,
              (tag + ": bad MAC refused, FARM and anti-replay state untouched").c_str());
      } else {
        CHECK(o.stage == TcStage::FARM && o.verdict == Farm1::DISCARD_NEG_WINDOW && farm.vr() == 1,
              (tag + ": duplicate N(S) discarded by FARM-1 (no SDLS)").c_str());
      }
      // Out-of-window N(S): lockout, then Unlock (BC) and Set V(R) (BC).
      const Bytes fl = tc_frame(uslp, false, false, 100, kNoopPkt, sizeof kNoopPkt, svc, 10);
      const Bytes cl = cltu(fl);
      o = tc_accept(cl.data(), cl.size(), work, sizeof work, bch, sdls, farm);
      const bool lock = o.verdict == Farm1::DISCARD_LOCKOUT && farm.state() == Farm1::S3_LOCKOUT;
      const uint8_t unlock[1] = {0x00};
      const Bytes cu = cltu(tc_frame(uslp, true, true, 0, unlock, 1, svc, 11));
      o = tc_accept(cu.data(), cu.size(), work, sizeof work, bch, sdls, farm);
      const bool unl = o.stage == TcStage::FARM && o.verdict == Farm1::CONTROL_UNLOCK && farm.state() == Farm1::S1_OPEN;
      const uint8_t setvr[3] = {0x82, 0x00, 42};
      const Bytes cs = cltu(tc_frame(uslp, true, true, 0, setvr, 3, svc, 12));
      o = tc_accept(cs.data(), cs.size(), work, sizeof work, bch, sdls, farm);
      const bool svr = o.verdict == Farm1::CONTROL_SET_VR && farm.vr() == 42;
      const Bytes cb = cltu(tc_frame(uslp, true, false, 0, kNoopPkt, sizeof kNoopPkt, svc, 13));
      o = tc_accept(cb.data(), cb.size(), work, sizeof work, bch, sdls, farm);
      CHECK(lock && unl && svr && o.verdict == Farm1::ACCEPT_BYPASS && o.frame.bypass,
            (tag + ": lockout, COP-1 Unlock / Set V(R) (Type-BC), Type-BD bypass").c_str());
      // Frame-layer rejections leave `frame` default and touch neither SDLS nor FARM.
      Bytes bad = tc_frame(uslp, false, false, 42, kNoopPkt, sizeof kNoopPkt, svc, 14);
      bad[bad.size() - 1] ^= 0xFF;                                  // FECF
      const Bytes cbad = cltu(bad);
      const uint32_t sn_before = sdls.last_sn(), fails = sdls.auth_failures();
      o = tc_accept(cbad.data(), cbad.size(), work, sizeof work, bch, sdls, farm);
      CHECK(o.stage == TcStage::FRAME_REJECTED && o.parse == TC_BAD_FECF && o.frame.data == nullptr &&
                o.frame.length == 0 && farm.vr() == 42 && sdls.last_sn() == sn_before && sdls.auth_failures() == fails,
            (tag + ": bad FECF -> FRAME_REJECTED, frame untouched, no SDLS/FARM effect").c_str());
    }
  }
  // USLP under SDLS: a malformed TFDF header inside an authentic frame -> TFDF_REJECTED.
  {
    Sdls sdls;
    sdls.configure(SDLS_SVC_AE, master, 32, kSdlsSpiAuth);
    Farm1 farm;
    BchStats bch;
    // control flag set but UPID 0: build with control=false body then set the PCC bit? Use a
    // bypass frame whose TFDF says COP-1 while PCC = 0: body byte built for control.
    Bytes body = {uslp_tfdf_byte(kUslpRulePackets, kUslpUpidPackets)};      // rule 000: not allowed on TC
    body.insert(body.end(), kNoopPkt, kNoopPkt + sizeof kNoopPkt);
    // Build an AE frame around a raw TFDF (bypass, no PCC) by hand.
    uint8_t k[32];
    sdls_derive_key(master, 32, kSdlsLabelTcAe, k);
    AesGcm g;
    g.set_key(k);
    const size_t hl = 7, len = hl + kSdlsAeOverhead + body.size() + 2;
    Bytes f(len, 0);
    put_u32(f.data(), 0xC0000000u | (uint32_t)kSpacecraftId << 12 | 1u << 11);
    put_u16(f.data() + 4, (uint16_t)(len - 1));
    f[6] = 0x80;                                                    // bypass, no PCC, no VCF count
    put_u16(f.data() + hl, kSdlsSpiTcAe);
    sdls_iv(kSdlsIvDirTc, 0, 77, f.data() + hl + 2);
    std::memcpy(f.data() + hl + kSdlsAeHdrLen, body.data(), body.size());
    g.encrypt(f.data() + hl + 2, f.data(), hl + kSdlsAeHdrLen, f.data() + hl + kSdlsAeHdrLen, body.size(),
              f.data() + hl + kSdlsAeHdrLen + body.size());
    fix_fecf(f);
    const Bytes c = cltu(f);
    const TcOutcome o = tc_accept(c.data(), c.size(), work, sizeof work, bch, sdls, farm);
    CHECK(o.stage == TcStage::TFDF_REJECTED && o.parse == TC_BAD_TFDF && o.sdls == Sdls::SDLS_OK &&
              sdls.last_sn() == 77 && farm.vr() == 0,
          "USLP/AE: authentic frame with a bad TFDF header -> TFDF_REJECTED (SN consumed, FARM untouched)");
  }
  {
    Sdls sdls;
    Farm1 farm;
    BchStats bch;
    const uint8_t junk[20] = {1, 2, 3};
    const Bytes c = cltu(tc_frame(false, false, false, 0, kNoopPkt, sizeof kNoopPkt, 0, 0));
    uint8_t small[kTcWorkMin - 1];
    CHECK(tc_accept(junk, sizeof junk, work, sizeof work, bch, sdls, farm).stage == TcStage::NO_CLTU &&
              tc_accept(c.data(), c.size(), small, sizeof small, bch, sdls, farm).stage == TcStage::NO_CLTU &&
              tc_accept(nullptr, 10, work, sizeof work, bch, sdls, farm).info_len == 0 && farm.vr() == 0,
          "tc_accept: no CLTU, short work buffer or null input -> NO_CLTU");
  }
}

// Ground-built uplink frames (written by ground/tests/test_cross_check.py) through the
// flight acceptance chain.
HOST_TEST(ground_tc_roundtrip) {
  FILE* in = std::fopen((out + "/ground_tc.txt").c_str(), "r");
  if (!in) {
    CHECK(true, "ground_tc.txt absent (written by the ground cross-check): uplink round trip skipped");
    return;
  }
  FILE* res = std::fopen((out + "/flight_tc_results.txt").c_str(), "w");
  if (!res) { std::fclose(in); CHECK(false, "cannot write flight_tc_results.txt"); return; }
  struct State { Sdls sdls; Farm1 farm; BchStats bch; };
  std::map<std::string, std::unique_ptr<State>> groups;
  char line[4096];
  int lines = 0, bad_lines = 0;
  uint8_t work[kTcWorkMin];
  while (lines < 1000 && std::fgets(line, sizeof line, in)) {
    char name[64], key[80], svc[8], fr[8], hx[1024];
    if (line[0] == '#' || line[0] == '\n') continue;
    if (std::sscanf(line, "%63s %79s %7s %7s %1023s", name, key, svc, fr, hx) != 5) { ++bad_lines; continue; }
    ++lines;
    std::unique_ptr<State>& g = groups[name];
    if (!g) {
      g.reset(new State());
      uint8_t k[32];
      const bool has_key = std::strcmp(key, "-") != 0 && unhex(key, k, sizeof k) == 32;
      const uint8_t s = std::strcmp(svc, "AE") == 0 ? SDLS_SVC_AE : std::strcmp(svc, "AUTH") == 0 ? SDLS_SVC_AUTH
                                                                                                   : SDLS_SVC_NONE;
      g->sdls.configure(s, has_key ? k : nullptr, has_key ? 32 : 0, kSdlsSpiAuth);
    }
    uint8_t c[600];
    const size_t cn = unhex(hx, c, sizeof c);
    const TcOutcome o = tc_accept(c, cn, work, sizeof work, g->bch, g->sdls, g->farm);
    std::string op = "-", ce = "-";
    if (o.stage == TcStage::FARM && (o.verdict == Farm1::ACCEPT || o.verdict == Farm1::ACCEPT_BYPASS)) {
      Command cmd;
      const CmdError e = parse_tc_packet(o.frame.data, o.frame.data_len, cmd);
      if (o.frame.data_len > kTcPktHdrLen) op = num(o.frame.data[kTcPktHdrLen]);
      ce = num(e);
    }
    std::fprintf(res, "%s %s %s %s %s %s %s\n", name, stage_name(o.stage),
                 o.stage == TcStage::NO_CLTU ? "-" : tc_parse_result_name(o.parse),
                 o.stage <= TcStage::FRAME_REJECTED ? "-" : sdls_name(o.sdls),
                 o.stage == TcStage::FARM ? verdict_name(o.verdict) : "-", op.c_str(), ce.c_str());
  }
  std::fclose(in);
  std::fclose(res);
  CHECK(bad_lines == 0,
        ("ground_tc.txt: " + num(lines) + " frames through tc_accept -> flight_tc_results.txt").c_str());
}

HOST_TEST(relay_gate) {
  const uint32_t seq = kCfdpEgseSeqFlag | cfdp_make_seq(7, 2);
  uint8_t meta[kCfdpMaxPduLen], fd[kCfdpMaxPduLen], eof[kCfdpMaxPduLen], other[kCfdpMaxPduLen], mcu[kCfdpMaxPduLen];
  const uint8_t data[50] = {1, 2, 3};
  const size_t nm =
      cfdp_build_metadata(meta, sizeof meta, kCfdpEntityVgq1, seq, kCfdpEntityDssq1, 50, "evr_20261011.jsonl");
  const size_t nf = cfdp_build_file_data(fd, sizeof fd, kCfdpEntityVgq1, seq, kCfdpEntityDssq1, 0, data, 50);
  const size_t ne = cfdp_build_eof(eof, sizeof eof, kCfdpEntityVgq1, seq, kCfdpEntityDssq1, 0,
                                   cfdp_checksum(0, 0, data, 50), 50);
  const size_t no = cfdp_build_file_data(other, sizeof other, kCfdpEntityVgq1, seq + 1, kCfdpEntityDssq1, 0, data, 50);
  const size_t nmcu = cfdp_build_file_data(mcu, sizeof mcu, kCfdpEntityVgq1, cfdp_make_seq(7, 2), kCfdpEntityDssq1, 0,
                                           data, 50);
  CHECK(nm && nf && ne && no && nmcu, "relay: PDUs built");
  RelayGate g;
  CHECK(g.admit(meta, nm, 0) == -1 && g.refused() == 1 && g.state() == RelayGate::NONE,
        "relay: unrequested PDU refused (also the fresh gate after an MCU reboot)");
  const uint32_t t0 = 0xFFFFF000u;                                  // near the millis() wrap
  g.request(seq, t0);
  g.request(seq + 5, t0);                                          // ignored: not NONE
  CHECK(g.state() == RelayGate::REQUESTED && g.seq() == seq, "relay: request -> REQUESTED with the authorised seq");
  CHECK(g.admit(other, no, t0 + 1) == -1 && g.admit(mcu, nmcu, t0 + 1) == -1 && g.admit(meta, 3, t0 + 1) == -1 &&
            g.admit(nullptr, 20, t0 + 1) == -1 && g.refused() == 5 && g.state() == RelayGate::REQUESTED,
        "relay: other seq, MCU seq (no EGSE flag), malformed and null PDUs refused");
  CHECK(g.admit(meta, nm, t0 + 2) == 1 && g.state() == RelayGate::ACTIVE && g.admit(fd, nf, t0 + 3) == 1 &&
            g.admit(fd, nf, t0 + 4) == 1 && g.admit(eof, ne, t0 + 5) == 1 && g.state() == RelayGate::NONE,
        "relay: Metadata -> ACTIVE, File Data (retry too), EOF -> NONE");
  CHECK(g.admit(eof, ne, t0 + 6) == -1 && !g.cancel(t0 + 6), "relay: nothing admitted or cancellable after the EOF");
  // Cancellation: only the EOF passes while CANCELLING.
  g.request(seq, t0);
  CHECK(g.admit(meta, nm, t0 + 1) == 1 && g.cancel(t0 + 2) && g.state() == RelayGate::CANCELLING &&
            g.admit(fd, nf, t0 + 3) == -1 && g.admit(eof, ne, t0 + 4) == 1 && g.state() == RelayGate::NONE,
        "relay: CANCELLING admits only the EOF");
  // Start timeout across the u32 wrap.
  g.request(seq, t0);
  CHECK(g.poll(t0 + 29999u, 30000) == RelayGate::EV_NONE && g.poll(t0 + 30000u, 30000) == RelayGate::EV_START_TIMEOUT &&
            g.state() == RelayGate::NONE && g.poll(t0 + 40000u, 30000) == RelayGate::EV_NONE,
        "relay: REQUESTED 30 s without a PDU -> start timeout (wrap-safe)");
  g.request(seq, t0);
  g.cancel(t0 + 100);
  CHECK(g.poll(t0 + 30099u, 30000) == RelayGate::EV_NONE &&
            g.poll(t0 + 30100u, 30000) == RelayGate::EV_CANCEL_TIMEOUT &&
            g.state() == RelayGate::NONE,
        "relay: CANCELLING 30 s without the EOF -> cancel timeout");
  g.request(seq, t0);
  g.admit(meta, nm, t0 + 1);
  CHECK(g.poll(t0 + 99999999u, 30000) == RelayGate::EV_NONE && g.state() == RelayGate::ACTIVE,
        "relay: ACTIVE is not timed by the gate (CfdpRelayMonitor::expired)");
  g.reset();
  CHECK(g.state() == RelayGate::NONE && g.seq() == seq && g.refused() == 7,
        "relay: reset -> NONE, seq kept for HK, refusals counted");
}

HOST_TEST(evr_gate) {
  EvrGate g;
  int admitted = 0;
  for (int i = 0; i < 12; ++i) admitted += g.admit(1, false) ? 1 : 0;
  CHECK(admitted == 10 && g.throttled() == 2, "EvrGate: bucket of 10 routine EVRs, then throttled");
  CHECK(g.admit(3, false) && g.admit(4, false) && g.admit(0, true) && g.throttled() == 2,
        "EvrGate: severity >= 3 and exempt EVRs pass without a token");
  for (int i = 0; i < 1000; ++i) g.refill(10, 167);                 // 10 s at 167 milli-EVR/s
  CHECK(g.tokens_milli() == 1670, "EvrGate: fractional refill keeps its remainder (1000 x 10 ms x 167 = 1.67 EVR)");
  CHECK(g.admit(2, false) && !g.admit(2, false), "EvrGate: one token, one EVR");
  g.refill(0xFFFFFFFFu, 0xFFFFFFFFu);
  CHECK(g.tokens_milli() == kEvrBucketMilli, "EvrGate: refill saturates at 10 EVRs");
  g.admit(1, false);
  g.fill();
  CHECK(g.tokens_milli() == kEvrBucketMilli, "EvrGate: fill() at the end of setup()");
}

HOST_TEST(downlink_load) {
  const uint16_t dfs[4] = {188, 158, 183, 153};                     // TM, TM AE, USLP, USLP AE
  int cases = 0, bad = 0, e22_stretched = 0;
  uint16_t e22_hot_max = 0;
  std::string first;
  for (uint8_t model = 1; model <= 3; ++model) {
    for (uint8_t code = 0; code <= 7; ++code) {
      const uint32_t busy = radio_airtime_ms(model, code, kCaduLen);
      for (uint8_t mode = MODE_SAFE; mode <= MODE_TEST; ++mode) {
        for (uint16_t df : dfs) {
          for (int hot = 0; hot < 2; ++hot) {
            ++cases;
            const uint32_t fp = frame_period_ms(busy, mode, hot != 0, 0);
            const LoadPlan p = plan_downlink_load(kModeCadence[mode], fp, df);
            double load = 0;
            for (uint8_t i = 0; i < kNumPeriodic; ++i) {
              const uint16_t eff = stretched_period_s(kModeCadence[mode][i], p.stretch_x100);
              if (eff) load += (double)kPeriodicPktLen[i] / eff;
            }
            const double cap = df * 1000.0 / fp;
            const double evr = p.evr_milli_per_s / 1000.0 * kEvrNominalOctets;
            const double evr_floor = std::fmin(kEvrMaxMilliPerS, 0.20 * cap * 1000.0 / kEvrNominalOctets) - 1.0;
            const bool ok = busy > 0 && load <= 0.75 * cap + 1e-9 && load + evr <= 0.95 * cap + 1e-9 &&
                            p.evr_milli_per_s >= evr_floor && p.evr_milli_per_s <= kEvrMaxMilliPerS &&
                            p.capacity_octets_per_min == (uint32_t)((uint64_t)df * 60000u / fp);
            if (!ok) {
              if (!bad) first = "model " + num(model) + " code " + num(code) + " mode " + num(mode) + " df " + num(df);
              ++bad;
            }
            if (model != 3 && !hot && p.stretch_x100 != 100) ++e22_stretched;
            if (model != 3 && hot && p.stretch_x100 > e22_hot_max) e22_hot_max = p.stretch_x100;
          }
        }
      }
    }
  }
  CHECK(cases == 3 * 8 * 4 * 4 * 2 && bad == 0,
        ("load plan: stretched periodic load <= 75 %, + routine EVRs <= 95 % of the capacity in all " + num(cases) +
         " cases" + (bad ? " (first failure: " + first + ")" : std::string())).c_str());
  CHECK(e22_stretched == 0, "load plan: every E22 case without PA over-temperature keeps the v2 cadence (stretch 100)");
  std::printf("      E22 with PA over-temperature: largest stretch x%u.%02u\n", e22_hot_max / 100u, e22_hot_max % 100u);
  CHECK(e22_hot_max == 186,
        "load plan: the T37S over-temperature back-off stretches E22 cases by at most x1.86 (PB-3.3)");
  const uint32_t busy1 = radio_airtime_ms(3, 1, kCaduLen);
  const LoadPlan p1 = plan_downlink_load(kModeCadence[MODE_SAFE], frame_period_ms(busy1, MODE_SAFE, false, 0), 153);
  std::printf("      SX126x profile 1 SAFE, USLP + AE: busy %lu ms, stretch %u, EVR %lu milli/s, capacity %lu B/min\n",
              (unsigned long)busy1, (unsigned)p1.stretch_x100, (unsigned long)p1.evr_milli_per_s,
              (unsigned long)p1.capacity_octets_per_min);
  CHECK(p1.stretch_x100 == 247, "load plan: SX126x profile 1 SAFE USLP + AE -> stretch 247 (PB-3.3)");
  // Frame period rule (v2) and edge cases.
  CHECK(frame_period_ms(1267, MODE_SAFE, false, 0) == 5068 && frame_period_ms(1267, MODE_TEST, false, 0) == 2534 &&
            frame_period_ms(1267, MODE_TEST, true, 0) == 5068 && frame_period_ms(100, MODE_TEST, false, 0) == 1500 &&
            frame_period_ms(1267, MODE_TEST, false, 9000) == 9000 && frame_period_ms(1267, 9, false, 0) == 5068 &&
            frame_period_ms(1000, MODE_TEST, false, 0) == 2000,
        "frame_period_ms: busy x mode factor, x2 hot, >= duty floor / 1500 ms / FPERIOD, bad mode = SAFE");
  const uint16_t off[kNumPeriodic] = {0};
  const LoadPlan z = plan_downlink_load(off, 5000, 188);
  const LoadPlan nc = plan_downlink_load(kModeCadence[MODE_TEST], 0, 188);
  CHECK(z.stretch_x100 == 100 && z.evr_milli_per_s == kEvrMaxMilliPerS && nc.stretch_x100 == 65535 &&
            nc.evr_milli_per_s == 0 && plan_downlink_load(nullptr, 5000, 188).stretch_x100 == 100,
        "load plan: nothing enabled -> 100; no capacity -> 65535 and no routine EVRs");
  CHECK(stretched_period_s(0, 500) == 0 && stretched_period_s(30, 247) == 75 && stretched_period_s(60, 247) == 149 &&
            stretched_period_s(3600, 65535) == 65535 && stretched_period_s(10, 50) == 10,
        "stretched_period_s: ceil(period x stretch / 100), 0 stays off, saturates at 65535");
}

namespace {
// Minimal parser for the flat JSON objects of the EGSE status: {"k":v,...} with v a
// string or a number. Returns false on any syntax error.
bool parse_flat_json(const char* s, std::vector<std::pair<std::string, std::string>>& kvs) {
  size_t i = 0;
  if (s[i++] != '{') return false;
  for (int guard = 0; guard < 100; ++guard) {
    if (s[i++] != '"') return false;
    std::string k;
    while (s[i] && s[i] != '"') k += s[i++];
    if (s[i++] != '"' || s[i++] != ':') return false;
    std::string v;
    if (s[i] == '"') {
      v += s[i++];
      while (s[i] && s[i] != '"') v += s[i++];
      if (s[i] != '"') return false;
      v += s[i++];
    } else {
      if (s[i] == '-') v += s[i++];
      if (!(s[i] >= '0' && s[i] <= '9')) return false;
      while (s[i] >= '0' && s[i] <= '9') v += s[i++];
      if (s[i] == '.') {
        v += s[i++];
        if (!(s[i] >= '0' && s[i] <= '9')) return false;
        while (s[i] >= '0' && s[i] <= '9') v += s[i++];
      }
    }
    kvs.push_back({k, v});
    if (s[i] == ',') { ++i; continue; }
    return s[i] == '}' && s[i + 1] == '\0';
  }
  return false;
}
}  // namespace

HOST_TEST(egse_status) {
  EgseStatus s{};
  s.mode = MODE_ENCOUNTER; s.sclk = 0xFFFFFFFFu; s.part = 65535; s.epoch = 65535; s.tm_state = 255;
  s.tm_sn = 0xFFFFFFFFu; s.frames = 0xFFFFFFFFu; s.oid = 0xFFFFFFFFu; s.mcfc = 255; s.period_ms = 0xFFFFFFFFu;
  s.busy_ms = 0xFFFFFFFFu; s.duty_pct = 100.0f; s.model = 255; s.fr = 255; s.ch = 255; s.code = 255; s.pwr = 255;
  s.lbt = s.tx = s.inhibit = s.hib = true; s.beacon = 255;
  s.ul_rssi = -32767; s.ul_snr_cdb = -32767; s.noise = -32767; s.farm = 255; s.vr = 255; s.acc = 65535;
  s.rej = 65535; s.fdir = 65535; s.sns = 65535;
  for (uint16_t& v : s.vc) v = 65535;
  s.ssr_pm = 65535; s.bus_c = -999.0f; s.rf_fault = 255; s.tc_svc = 255; s.tm_svc = 255; s.cfdp = 255;
  s.cfdp_seq = 0xFFFFFFFFu; s.cfdp_pm = 65535; s.seq = 255; s.tc_rescued = 0xFFFFFFFFu; s.evr_nd = 0xFFFFFFFFu;
  s.cadu_nd = 0xFFFFFFFFu; s.emit_ref = 0xFFFFFFFFu; s.relay_ref = 0xFFFFFFFFu; s.stretch = 65535;
  char buf[kEgseStatusCap];
  const size_t n1 = format_sc_status(s, buf, sizeof buf);
  std::vector<std::pair<std::string, std::string>> kvs;
  const bool j1 = n1 > 0 && std::strlen(buf) == n1 && parse_flat_json(buf, kvs);
  static const char* const core[] = {"mode", "sclk", "part", "epoch", "tm_state", "tm_sn", "frames", "oid",
                                     "mcfc", "period_ms", "busy_ms", "duty_pct", "model", "fr", "ch", "code",
                                     "pwr", "lbt", "tx", "inhibit", "hib", "beacon"};
  bool keys1 = j1 && kvs.size() == 22;
  for (size_t i = 0; keys1 && i < 22; ++i) keys1 = kvs[i].first == core[i];
  const bool vals1 = keys1 && kvs[0].second == "\"ENCOUNTER\"" && kvs[1].second == "4294967295" &&
                     kvs[11].second == "100.0" && kvs[20].second == "1";
  std::printf("      sc_status worst case %u characters\n", (unsigned)n1);
  CHECK(j1 && keys1 && vals1 && n1 <= 480, "sc_status: 22 keys, valid JSON, worst case <= 480 characters");
  const size_t n2 = format_sc_status_x(s, buf, sizeof buf);
  kvs.clear();
  const bool j2 = n2 > 0 && parse_flat_json(buf, kvs);
  static const char* const ext[] = {"sclk", "ul_rssi", "ul_snr_cdb", "noise", "farm", "vr", "acc", "rej", "fdir",
                                    "sns", "vc0", "vc1", "vc2", "vc3", "ssr_pm", "bus_c", "rf_fault", "tc_svc",
                                    "tm_svc", "cfdp", "cfdp_seq", "cfdp_pm", "seq", "tc_rescued", "evr_nd",
                                    "cadu_nd", "emit_ref", "relay_ref", "stretch"};
  bool keys2 = j2 && kvs.size() == 29;
  for (size_t i = 0; keys2 && i < 29; ++i) keys2 = kvs[i].first == ext[i];
  const bool vals2 = keys2 && kvs[1].second == "-32767" && kvs[15].second == "-999.0" && kvs[28].second == "65535";
  std::printf("      sc_status_x worst case %u characters\n", (unsigned)n2);
  CHECK(j2 && keys2 && vals2 && n2 <= 480, "sc_status_x: 29 keys, valid JSON, worst case <= 480 characters");
  // Clamps, NaN and the n/a sentinels.
  s.duty_pct = NAN; s.bus_c = NAN; s.ul_rssi = INT16_MIN; s.ul_snr_cdb = kNaI16; s.mode = 77;
  format_sc_status(s, buf, sizeof buf);
  const bool nan1 = std::strstr(buf, "\"duty_pct\":0.0,") != nullptr && std::strstr(buf, "\"mode\":\"?\"") != nullptr;
  format_sc_status_x(s, buf, sizeof buf);
  const bool nan2 = std::strstr(buf, "\"bus_c\":-999.0,") != nullptr && std::strstr(buf, "\"ul_rssi\":32767,") &&
                    std::strstr(buf, "\"ul_snr_cdb\":32767,");
  s.duty_pct = 250.0f; s.bus_c = -0.04f;
  format_sc_status(s, buf, sizeof buf);
  const bool clamp1 = std::strstr(buf, "\"duty_pct\":100.0,") != nullptr;
  format_sc_status_x(s, buf, sizeof buf);
  const bool clamp2 = std::strstr(buf, "\"bus_c\":0.0,") != nullptr;
  s.bus_c = -12.35f;
  format_sc_status_x(s, buf, sizeof buf);
  const bool neg = std::strstr(buf, "\"bus_c\":-12.4,") != nullptr || std::strstr(buf, "\"bus_c\":-12.3,") != nullptr;
  CHECK(nan1 && nan2 && clamp1 && clamp2 && neg, "EGSE status: NaN, clamps, n/a readings (32767), unknown mode '?'");
  char small[100];
  CHECK(format_sc_status(s, small, sizeof small) == 0 && small[0] == '\0' && format_sc_status_x(s, nullptr, 481) == 0,
        "EGSE status: a buffer that is too small -> 0, nothing to send");
}

// The four CADU streams and their expectations.
HOST_TEST(integration_streams) {
  struct Cfg { const char* file; Framing fr; int sec; };
  const Cfg cfgs[] = {
      {"integration_tm_ae.bin", FRAMING_TM, 1},
      {"integration_uslp_ae.bin", FRAMING_USLP, 1},
      {"integration_tm_nullsa.bin", FRAMING_TM, 2},
      {"integration_tm_clear.bin", FRAMING_TM, 0},
  };
  FILE* f = std::fopen((out + "/integration_expect.txt").c_str(), "w");
  if (!f) { CHECK(false, "cannot write integration_expect.txt"); return; }
  std::fprintf(f, "# VGQ-1 Phase B integration vectors - tools/host_test/test_integration.cpp (format: file header)\n");
  std::fprintf(f, "# SDLS master key 000102...1f, TM epoch 7, OID identification \"DE N0CALL VGQ-1 \"\n");
  size_t total = 0;
  for (const Cfg& c : cfgs) {
    const std::vector<std::string> lines = build_stream(out, c.file, c.fr, c.sec);
    for (const std::string& l : lines) std::fprintf(f, "%s\n", l.c_str());
    total += lines.size();
  }
  std::fclose(f);
  CHECK(total > 4 * 30,
        ("wrote integration_expect.txt (" + num((long long)total) + " records) and 4 CADU streams").c_str());
}
