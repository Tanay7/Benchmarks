// =============================================================================
//  VGQ-1 telemetry packers (flight half of the telemetry dictionary, v3) and the
//  MCU -> Linux EGSE status formatter. Field tables: tlm_packets.h / Phase B
//  specification PB-1 and PB-4.1. Pure C++, host-tested (tools/host_test).
//
//  Serialisation is explicit and big-endian, one field at a time (no struct
//  copies, no type punning), so the wire layout never depends on the compiler's
//  padding or the CPU's byte order.
// =============================================================================
#include "tlm_packets.h"
#include <math.h>
#include <stdio.h>
#include <string.h>

namespace vgq {

namespace {

// Minimal big-endian serialiser. The caller guarantees room for the packet
// (every packer writes a fixed, documented number of octets).
struct W {
  uint8_t* p;
  size_t n = 0;
  explicit W(uint8_t* out) : p(out) {}
  void u8(uint8_t v) { p[n++] = v; }
  void i8(int8_t v) { p[n++] = (uint8_t)v; }
  void u16(uint16_t v) { p[n++] = (uint8_t)(v >> 8); p[n++] = (uint8_t)v; }
  void i16(int16_t v) { u16((uint16_t)v); }
  void u32(uint32_t v) { u16((uint16_t)(v >> 16)); u16((uint16_t)v); }
  void i32(int32_t v) { u32((uint32_t)v); }
};

void mag(W& w, const MagVector& m) {
  w.i32(m.bx_nT); w.i32(m.by_nT); w.i32(m.bz_nT); w.u16(m.rms_nT);
}

}  // namespace

// ---- APIDs and modes -------------------------------------------------------------------
uint8_t apid_index(uint16_t apid) {
  switch (apid) {
    case APID_HK:        return 0;
    case APID_RF:        return 1;
    case APID_EVR:       return 2;
    case APID_CMDVER:    return 3;
    case APID_TIMECORR:  return 4;
    case APID_RFSCAN:    return 5;
    case APID_MAG:       return 6;
    case APID_ATT:       return 7;
    case APID_SPEC:      return 8;
    case APID_ENV:       return 9;
    case APID_SPECBURST: return 10;
    case APID_CFDP:      return 11;
    default:             return kApidIndexNone;   // never another APID's counter (PB-1.1)
  }
}

const char* fsw_mode_name(uint8_t mode) {
  static const char* const kName[] = {"BOOT", "SAFE", "CRUISE", "ENCOUNTER", "TEST"};
  return mode <= MODE_TEST ? kName[mode] : "?";
}

// ---- Fixed-length packets ----------------------------------------------------------------
size_t pack_hk(const HkPacket& p, uint8_t* out) {
  W w(out);
  w.u8(p.fsw_mode); w.u16(p.sclk_partition); w.u32(p.uptime_s); w.u8(p.reset_cause);
  w.u16(p.fdir_flags); w.u16(p.sensor_health); w.u16(p.cmd_accepted); w.u16(p.cmd_rejected);
  w.u8(p.cmd_last_opcode); w.u8(p.cmd_last_status); w.u16(p.tc_frames_ok); w.u16(p.tc_frames_bad);
  w.u8(p.farm_state); w.u8(p.farm_vr); w.u32(p.cmd_loss_timer_s);
  w.i16(p.bus_temp_cC); w.u8(p.radio_fault); w.u16(p.radio_fault_reports);
  w.u16(p.loop_max_ms); w.u16(p.loop_overruns); w.u16(p.i2c_errors);
  w.u16(p.ssr_fill_permille); w.u16(p.ssr_dropped); w.u16(p.ssr_kib);
  w.u16(p.vc0_backlog); w.u16(p.vc1_backlog); w.u16(p.vc2_backlog); w.u16(p.vc3_backlog);
  w.u8(p.sdls_tc_service); w.u8(p.sdls_tm_service); w.u16(p.sdls_auth_fail); w.u32(p.sdls_last_sn);
  w.u16(p.sdls_tm_epoch); w.u8(p.sdls_tm_state);
  w.u8(p.cfdp_state); w.u32(p.cfdp_seq); w.u16(p.cfdp_progress_permille);
  w.u8(p.seq_state); w.u8(p.seq_count); w.u16(p.seq_next_dt_s);
  w.u8(p.mag_bist); w.u8(p.magcal_state); w.u8(p.beacon_level);
  return w.n;                                                     // kHkLen = 78
}

size_t pack_rf(const RfPacket& p, uint8_t* out) {
  W w(out);
  w.u8(p.radio_model); w.u8(p.air_rate_code); w.u8(p.tx_power_code); w.u8(p.channel);
  w.u8(p.radio_cfg_ok); w.u8(p.framing);
  w.u32(p.frames_sent); w.u32(p.oid_frames_sent); w.u32(p.tx_airtime_ms_total);
  w.u16(p.tx_duty_permille); w.u16(p.frame_period_ms); w.u16(p.aux_timeouts);
  w.i16(p.uplink_rssi_dbm); w.i16(p.uplink_snr_cdb); w.i32(p.uplink_freq_err_hz);
  w.i16(p.noise_floor_dbm);
  w.u16(p.cltu_ok); w.u16(p.cltu_bad); w.u16(p.bch_corrected); w.u32(p.rx_bytes);
  w.u8(p.mcfc); w.u8(p.last_airtime_ds); w.u16(p.lbt_busy); w.u8(p.flags);
  return w.n;                                                     // kRfLen = 49
}

size_t pack_cmdver(const CmdVerPacket& p, uint8_t* out) {
  W w(out);
  const uint8_t stage = (uint8_t)((p.stage & kCmdVerStageMask) | ((p.source & 3u) << kCmdVerSourceShift));
  w.u8(p.tc_seq); w.u8(p.opcode); w.u8(stage); w.u8(p.error_code); w.u32(p.ground_tag);
  return w.n;                                                     // kCmdVerLen = 8
}

size_t pack_timecorr(const TimeCorrPacket& p, uint8_t* out) {
  W w(out);
  w.u8(p.ref_mcfc); w.u32(p.sclk_coarse); w.u16(p.sclk_fine); w.u8(p.air_rate_code);
  w.u16(p.ref_airtime_ms);
  return w.n;                                                     // kTimeCorrLen = 10
}

size_t pack_mag(const MagPacket& p, uint8_t* out) {
  W w(out);
  w.u8(p.nsamples); w.u8(p.flags);
  mag(w, p.outboard); mag(w, p.inboard); mag(w, p.body);
  w.u16(p.rm3100_cycle_count);
  return w.n;                                                     // kMagLen = 46
}

size_t pack_att(const AttPacket& p, uint8_t* out) {
  W w(out);
  w.u8(p.flags);
  for (uint8_t i = 0; i < 3; ++i) w.i16(p.acc_mg[i]);
  for (uint8_t i = 0; i < 3; ++i) w.i16(p.gyro_ddps[i]);
  for (uint8_t i = 0; i < 3; ++i) w.i16(p.mag_dT[i]);
  for (uint8_t i = 0; i < 4; ++i) w.i16(p.q_fus[i]);
  w.u16(p.q_fus_acc_mrad);
  for (uint8_t i = 0; i < 4; ++i) w.i16(p.q_triad[i]);
  return w.n;                                                     // kAttLen = 37
}

size_t pack_env(const EnvPacket& p, uint8_t* out) {
  W w(out);
  w.u8(p.flags); w.u32(p.p_cPa); w.i16(p.t_cC); w.u16(p.rh_cpct); w.u16(p.iaq); w.u16(p.iaq_s);
  w.u32(p.co2eq_ppm); w.u16(p.bvoc_cppm); w.u32(p.gas_ohm); w.u8(p.accuracy);
  return w.n;                                                     // kEnvLen = 24
}

size_t pack_rfscan(uint8_t first_ch, uint8_t count, uint16_t duration_ms, const int8_t* noise,
                   uint8_t* out) {
  if (out == nullptr || noise == nullptr || count == 0 || count > kRfscanMaxChannels) return 0;
  W w(out);
  w.u8(first_ch); w.u8(count); w.u16(duration_ms);
  for (uint8_t i = 0; i < count; ++i) w.i8(noise[i]);
  return w.n;                                                     // 4 + count
}

size_t pack_evr(uint8_t severity, uint16_t event_id, const char* text, uint8_t* out) {
  W w(out);
  w.u8(severity); w.u16(event_id);
  // Bounded scan (strnlen is not in every embedded C library): at most kEvrTextMax.
  size_t len = 0;
  if (text != nullptr) {
    while (len < kEvrTextMax && text[len] != '\0') ++len;
  }
  for (size_t i = 0; i < len; ++i) w.u8((uint8_t)text[i]);
  return w.n;                                                     // 3 + text length
}

// =============================================================================
// EGSE status notifications (PB-4.1, C-25)
// =============================================================================
namespace {

// One decimal from integer tenths, so the text never depends on the C library's
// floating-point printf support (newlib-nano builds omit it). The value is clamped
// to [lo, hi]; NaN prints `nan_value`.
struct Tenths {
  char sign[2];
  unsigned long whole;
  unsigned long frac;
};
Tenths tenths(float v, float lo, float hi, float nan_value) {
  if (isnan(v)) v = nan_value;
  if (v < lo) v = lo;
  if (v > hi) v = hi;
  const float r = floorf(v * 10.0f + 0.5f);          // |r| <= 9990: exact in FP32
  long t = (long)r;
  Tenths o;
  o.sign[0] = t < 0 ? '-' : '\0';
  o.sign[1] = '\0';
  if (t < 0) t = -t;
  o.whole = (unsigned long)(t / 10);
  o.frac = (unsigned long)(t % 10);
  return o;
}

// Radio reading for JSON: the RF packet's "not available" value (32767) for both
// the packet sentinel and the radio API's INT16_MIN, so the text is always a number.
int na_i16(int16_t v) { return (int)tlm_na_i16(v); }

// snprintf result -> length, or 0 when it failed or did not fit (nothing to send).
size_t fitted(int r, size_t cap) {
  if (r < 0 || (size_t)r >= cap) return 0;
  return (size_t)r;
}

}  // namespace

size_t format_sc_status(const EgseStatus& s, char* out, size_t cap) {
  if (out == nullptr || cap == 0) return 0;
  const Tenths duty = tenths(s.duty_pct, 0.0f, 100.0f, 0.0f);
  const int r = snprintf(out, cap,
      "{\"mode\":\"%s\",\"sclk\":%lu,\"part\":%u,\"epoch\":%u,\"tm_state\":%u,\"tm_sn\":%lu,"
      "\"frames\":%lu,\"oid\":%lu,\"mcfc\":%u,\"period_ms\":%lu,\"busy_ms\":%lu,"
      "\"duty_pct\":%s%lu.%lu,\"model\":%u,\"fr\":%u,\"ch\":%u,\"code\":%u,\"pwr\":%u,"
      "\"lbt\":%u,\"tx\":%u,\"inhibit\":%u,\"hib\":%u,\"beacon\":%u}",
      fsw_mode_name(s.mode), (unsigned long)s.sclk, (unsigned)s.part, (unsigned)s.epoch,
      (unsigned)s.tm_state, (unsigned long)s.tm_sn, (unsigned long)s.frames,
      (unsigned long)s.oid, (unsigned)s.mcfc, (unsigned long)s.period_ms,
      (unsigned long)s.busy_ms, duty.sign, duty.whole, duty.frac, (unsigned)s.model,
      (unsigned)s.fr, (unsigned)s.ch, (unsigned)s.code, (unsigned)s.pwr, s.lbt ? 1u : 0u,
      s.tx ? 1u : 0u, s.inhibit ? 1u : 0u, s.hib ? 1u : 0u, (unsigned)s.beacon);
  const size_t n = fitted(r, cap);
  if (n == 0) out[0] = '\0';
  return n;
}

size_t format_sc_status_x(const EgseStatus& s, char* out, size_t cap) {
  if (out == nullptr || cap == 0) return 0;
  const Tenths bus = tenths(s.bus_c, -999.0f, 999.0f, -999.0f);
  const int r = snprintf(out, cap,
      "{\"sclk\":%lu,\"ul_rssi\":%d,\"ul_snr_cdb\":%d,\"noise\":%d,\"farm\":%u,\"vr\":%u,"
      "\"acc\":%u,\"rej\":%u,\"fdir\":%u,\"sns\":%u,\"vc0\":%u,\"vc1\":%u,\"vc2\":%u,\"vc3\":%u,"
      "\"ssr_pm\":%u,\"bus_c\":%s%lu.%lu,\"rf_fault\":%u,\"tc_svc\":%u,\"tm_svc\":%u,"
      "\"cfdp\":%u,\"cfdp_seq\":%lu,\"cfdp_pm\":%u,\"seq\":%u,\"tc_rescued\":%lu,"
      "\"evr_nd\":%lu,\"cadu_nd\":%lu,\"emit_ref\":%lu,\"relay_ref\":%lu,\"stretch\":%u}",
      (unsigned long)s.sclk, na_i16(s.ul_rssi), na_i16(s.ul_snr_cdb), na_i16(s.noise),
      (unsigned)s.farm, (unsigned)s.vr, (unsigned)s.acc, (unsigned)s.rej, (unsigned)s.fdir,
      (unsigned)s.sns, (unsigned)s.vc[0], (unsigned)s.vc[1], (unsigned)s.vc[2],
      (unsigned)s.vc[3], (unsigned)s.ssr_pm, bus.sign, bus.whole, bus.frac,
      (unsigned)s.rf_fault, (unsigned)s.tc_svc, (unsigned)s.tm_svc, (unsigned)s.cfdp,
      (unsigned long)s.cfdp_seq, (unsigned)s.cfdp_pm, (unsigned)s.seq,
      (unsigned long)s.tc_rescued, (unsigned long)s.evr_nd, (unsigned long)s.cadu_nd,
      (unsigned long)s.emit_ref, (unsigned long)s.relay_ref, (unsigned)s.stretch);
  const size_t n = fitted(r, cap);
  if (n == 0) out[0] = '\0';
  return n;
}

}  // namespace vgq
