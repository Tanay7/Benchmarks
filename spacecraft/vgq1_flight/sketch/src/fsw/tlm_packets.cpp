#include "tlm_packets.h"
#include <string.h>

namespace vgq {

namespace {
// Minimal big-endian serializer.
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
  void f32(float v) { uint32_t b; memcpy(&b, &v, 4); u32(b); }   // IEEE-754 binary32
};
void mag(W& w, const MagVector& m) { w.i32(m.bx_nT); w.i32(m.by_nT); w.i32(m.bz_nT); w.u16(m.rms_nT); }
}  // namespace

size_t pack_hk(const HkPacket& p, uint8_t* out) {
  W w(out);
  w.u8(p.fsw_mode); w.u16(p.sclk_partition); w.u32(p.uptime_s); w.u8(p.reset_cause);
  w.u16(p.fdir_flags); w.u16(p.sensor_health); w.u16(p.cmd_accepted); w.u16(p.cmd_rejected);
  w.u8(p.cmd_last_opcode); w.u8(p.cmd_last_status); w.u16(p.tc_frames_ok); w.u16(p.tc_frames_bad);
  w.u8(p.farm_state); w.u8(p.farm_vr); w.u32(p.cmd_loss_timer_s);
  w.i16(p.bus_temp_cC); w.u8(p.radio_fault); w.u16(p.radio_fault_reports);
  w.u16(p.loop_max_ms); w.u16(p.loop_overruns); w.u16(p.i2c_errors);
  w.u16(p.ssr_fill_permille); w.u16(p.ssr_dropped); w.u16(p.ssr_kib);
  w.u16(p.vc0_backlog); w.u16(p.vc1_backlog); w.u16(p.vc2_backlog);
  w.u8(p.sdls_enabled); w.u16(p.sdls_auth_fail); w.u32(p.sdls_last_sn);
  return w.n;
}

size_t pack_rf(const RfPacket& p, uint8_t* out) {
  W w(out);
  w.u8(p.air_rate_code); w.u8(p.tx_power_code); w.u8(p.channel); w.u8(p.e22_cfg_ok);
  w.u32(p.frames_sent); w.u32(p.oid_frames_sent); w.u32(p.tx_airtime_ms_total);
  w.u16(p.tx_duty_permille); w.u16(p.frame_period_ms); w.u16(p.aux_timeouts);
  w.i16(p.uplink_rssi_dbm); w.i16(p.noise_floor_dbm);
  w.u16(p.cltu_ok); w.u16(p.cltu_bad); w.u16(p.bch_corrected); w.u32(p.rx_bytes);
  w.u8(p.mcfc); w.u8(p.last_airtime_ds);
  return w.n;
}

size_t pack_cmdver(const CmdVerPacket& p, uint8_t* out) {
  W w(out);
  w.u8(p.tc_seq); w.u8(p.opcode); w.u8(p.stage); w.u8(p.error_code); w.u32(p.ground_tag);
  return w.n;
}

size_t pack_timecorr(const TimeCorrPacket& p, uint8_t* out) {
  W w(out);
  w.u8(p.ref_mcfc); w.u32(p.sclk_coarse); w.u16(p.sclk_fine); w.u8(p.air_rate_code);
  w.u16(p.ref_airtime_ms);
  return w.n;
}

size_t pack_mag(const MagPacket& p, uint8_t* out) {
  W w(out);
  w.u8(p.nsamples); w.u8(p.flags);
  mag(w, p.outboard); mag(w, p.inboard); mag(w, p.body);
  w.u16(p.rm3100_cycle_count);
  return w.n;
}

size_t pack_att(const AttPacket& p, uint8_t* out) {
  W w(out);
  w.u8(p.flags);
  for (int i = 0; i < 3; ++i) w.i16(p.acc_mg[i]);
  for (int i = 0; i < 3; ++i) w.i16(p.gyro_ddps[i]);
  for (int i = 0; i < 3; ++i) w.i16(p.mag_dT[i]);
  for (int i = 0; i < 4; ++i) w.i16(p.q_fus[i]);
  w.u16(p.q_fus_acc_mrad);
  for (int i = 0; i < 4; ++i) w.i16(p.q_triad[i]);
  return w.n;
}

size_t pack_spec(const SpecPacket& p, uint8_t* out) {
  W w(out);
  w.u8(p.flags); w.u8(p.gain_code); w.u8(p.int_cycles);
  for (int i = 0; i < 18; ++i) w.f32(p.cal_uW_cm2[i]);
  for (int i = 0; i < 18; ++i) w.u16(p.raw[i]);
  for (int i = 0; i < 3; ++i) w.i8(p.temp_c[i]);
  return w.n;
}

size_t pack_env(const EnvPacket& p, uint8_t* out) {
  W w(out);
  w.u16(p.flags);
  w.i16(p.nenv_t_cC); w.u16(p.nenv_rh_cpct); w.f32(p.nenv_iaq); w.f32(p.nenv_tvoc_mg_m3);
  w.f32(p.nenv_eco2_ppm); w.u16(p.nenv_outdoor_aqi); w.f32(p.nenv_no2_ppb); w.f32(p.nenv_o3_ppb);
  w.u32(p.nme_p_cPa); w.i16(p.nme_t_cC); w.u16(p.nme_rh_cpct); w.u16(p.nme_iaq); w.u16(p.nme_iaq_s);
  w.u32(p.nme_co2eq_ppm); w.u16(p.nme_bvoc_cppm); w.u32(p.nme_gas_ohm); w.u8(p.nme_accuracy);
  return w.n;
}

size_t pack_rfscan(uint8_t first_ch, uint8_t count, uint16_t duration_ms, const int8_t* noise,
                   uint8_t* out) {
  W w(out);
  w.u8(first_ch); w.u8(count); w.u16(duration_ms);
  for (uint8_t i = 0; i < count; ++i) w.i8(noise[i]);
  return w.n;
}

size_t pack_evr(uint8_t severity, uint16_t event_id, const char* text, uint8_t* out) {
  W w(out);
  w.u8(severity); w.u16(event_id);
  size_t len = text ? strlen(text) : 0;
  if (len > 80) len = 80;
  for (size_t i = 0; i < len; ++i) w.u8((uint8_t)text[i]);
  return w.n;
}

}  // namespace vgq
