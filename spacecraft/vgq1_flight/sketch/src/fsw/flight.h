// =============================================================================
//  VGQ-1 flight software executive (runs on the UNO Q STM32U585 MCU).
//
//  Functional analogue of a deep-space command & data handling system:
//    * CCS  (command subsystem)  : CLTU decode, FARM-1, command dispatch, timers
//    * FDS  (flight data subsys) : sampling, packetisation, VC multiplexing, SSR
//    * AACS (attitude)           : IMU / ARU / sun sensor / TRIAD processing
//    * Telecom                   : E22 TX/RX state machines, duty-cycle control
//    * Fault protection (FDIR)   : command loss, thermal, bus, radio monitors
//  The main loop never blocks for long and never calls Bridge.call() (which has
//  no timeout); Linux is only ever *notified*.
// =============================================================================
#pragma once
#include <Arduino.h>
#include "../config.h"
#include "../ccsds/sdls.h"
#include "../ccsds/tc.h"
#include "../ccsds/tm.h"
#include "../drivers/e22.h"
#include "../drivers/i2c_bus.h"
#include "../drivers/payload.h"
#include "../drivers/sensors.h"
#include "attitude.h"
#include "commands.h"
#include "sclk.h"
#include "ssr.h"
#include "status_display.h"
#include "tlm_packets.h"

namespace vgq {

class Flight {
 public:
  void setup();
  void loop();

  // ---- EGSE (Linux side) hooks, executed in the loop thread via provide_safe ----
  void egse_set_partition(int p);
  void egse_set_sdls_sn(uint32_t sn) { sdls_.set_last_sn(sn); }
  bool egse_hardline_cltu(const String& hex);   // umbilical commanding for bench I&T

 private:
  enum TxState : uint8_t { TX_IDLE, TX_WAIT_BUSY, TX_WAIT_DONE };
  enum PktIdx : uint8_t { P_HK, P_RF, P_TIMECORR, P_MAG, P_ATT, P_SPEC, P_ENV, P_COUNT };

  // init
  void init_radio();
  void init_sensors();
  uint8_t read_reset_cause();

  // telecom
  void service_rx(uint32_t now);
  void process_rx_packet(uint32_t now);
  void service_tx(uint32_t now);
  void send_next_frame(uint32_t now);
  uint32_t frame_period_ms() const;
  void apply_radio_config(uint32_t now);
  void service_scan(uint32_t now);
  void enter_hibernate(uint16_t beacon_s);
  void exit_hibernate(const char* why);
  void process_tc(const uint8_t* info, size_t n, bool hardline);

  // commanding
  void execute(const uint8_t* pkt, size_t len, uint8_t tc_seq);
  void cmd_verify(uint8_t seq, uint8_t opcode, uint8_t stage, uint8_t err, uint32_t tag = 0);
  void set_mode(uint8_t m, const char* why);

  // data handling
  bool emit(uint16_t apid, const uint8_t* payload, size_t n, uint8_t vc);
  void evr(uint8_t sev, uint16_t id, const char* fmt, ...);
  void tick_1hz(uint32_t now);
  void sample_fast();
  void gen_hk(), gen_rf(), gen_timecorr(), gen_mag(), gen_att(), gen_spec(), gen_env();
  void fdir_1hz(uint32_t now);
  void update_display(uint32_t now);
  void notify_egse_status();

  // ---- subsystems ----
  E22 radio_;
  E22Config rcfg_;
  I2cBus bus_;
  Mpu9250 imu_;
  Rm3100 mag_ob_, mag_ib_;
  Mmc5603 mag_body_;
  Veml7700 veml_;
  Ina226 ina_;
  Bme690 bme_;
  Spectrometers spec_;
  NiclaEnv nenv_;
  NiclaAru aru_;
  Analog analog_;
  StatusDisplay display_;
  Sclk sclk_;
  Ssr ssr_;
  ccsds::TmFramer framer_;
  ccsds::VcStream vc_[ccsds::kNumDataVcs];
  ccsds::Farm1 farm_{10};
  ccsds::BchStats bch_;

  // ---- state ----
  uint8_t mode_ = MODE_BOOT;
  uint8_t reset_cause_ = 255;
  uint16_t seq_[P_COUNT + 4] = {0};   // per-APID source sequence counters
  uint16_t idle_seq_ = 0;
  uint32_t last_gen_s_[P_COUNT] = {0};
  uint16_t period_override_[P_COUNT] = {0};   // 0 = use mode table; 0xFFFF = off
  uint16_t sensor_health_ = 0, sensor_enable_ = 0xFFFF, sensor_fail_count_[16] = {0};
  uint16_t fdir_flags_ = 0, fdir_mask_ = 0xFFFF;
  uint8_t evr_level_ = 1;
  uint16_t evr_budget_ = 10;          // EVR throttle (refilled each minute)

  // telecom state
  TxState tx_state_ = TX_IDLE;
  uint32_t tx_start_ms_ = 0, last_tx_start_ms_ = 0, last_airtime_ms_ = 0;
  uint64_t tx_start_sclk_us_ = 0;
  uint8_t tx_mcfc_ = 0;
  uint16_t busy_ms_bucket_[60] = {0};
  uint32_t frames_sent_ = 0, oid_sent_ = 0, airtime_total_ms_ = 0;
  uint16_t aux_timeouts_ = 0;
  uint8_t last_vc_ = 0;
  uint16_t cmd_frame_period_ms_ = 0;
  bool tx_inhibit_ = false;
  uint32_t tx_inhibit_until_ms_ = 0;
  bool radio_cfg_ok_ = false, radio_reconfig_pending_ = false;
  // Coordinated radio change (air rate / channel): applied after N more frames at
  // the old setting, reverted automatically unless the ground proves it followed
  // by getting a valid TC through on the new setting.
  E22Config pending_cfg_, revert_cfg_;
  bool pending_cfg_valid_ = false, revert_armed_ = false;
  uint8_t cfg_change_after_frames_ = 0;
  uint32_t revert_deadline_ms_ = 0, revert_window_ms_ = 0;
  // RF spectrum survey
  enum ScanState : uint8_t { SCAN_IDLE, SCAN_SET, SCAN_SETTLE, SCAN_WAIT_REPLY };
  ScanState scan_state_ = SCAN_IDLE;
  uint8_t scan_first_ = 0, scan_last_ = 0, scan_ch_ = 0;
  int8_t scan_noise_[84];
  uint32_t scan_t0_ = 0, scan_step_ms_ = 0;
  // Wake-on-Radio hibernation
  bool hibernating_ = false;
  uint16_t beacon_s_ = 600;
  uint8_t mode_before_hib_ = MODE_SAFE;
  ccsds::Sdls sdls_;
  bool timecorr_request_ = false;
  bool reboot_pending_ = false;
  uint8_t reboot_after_frames_ = 0;

  // receive state
  uint8_t rx_buf_[300];
  size_t rx_len_ = 0;
  uint32_t last_rx_byte_ms_ = 0, rx_bytes_ = 0, last_rx_flash_ms_ = 0;
  bool awaiting_rssi_ = false;
  uint32_t rssi_req_ms_ = 0, last_noise_ms_ = 0;
  int16_t uplink_rssi_ = -32768, noise_dbm_ = -32768;
  uint16_t cltu_ok_ = 0, cltu_bad_ = 0, tc_ok_ = 0, tc_bad_ = 0;
  uint32_t last_valid_tc_ms_ = 0, last_cltu_ms_ = 0;
  uint32_t cmd_loss_s_ = kDefaultCmdLossS;

  // command counters
  uint16_t cmd_acc_ = 0, cmd_rej_ = 0;
  uint8_t last_opcode_ = 0, last_status_ = 0;

  // timing
  uint32_t last_1hz_ms_ = 0, last_disp_ms_ = 0, loop_start_ms_ = 0;
  uint16_t loop_max_ms_ = 0, loop_overruns_ = 0;
  uint32_t last_callsign_s_ = 0;

  // instrument data
  MagStats mstat_ob_, mstat_ib_, mstat_body_;
  bool mag_tx_keyed_ = false;
  float body_temp_c_ = NAN, avi_temp_c_ = NAN, pa_temp_c_ = NAN, vradio_ = NAN, iradio_ = NAN;
  uint16_t css_[4] = {0};
  AruReading aru_last_;
  bool aru_valid_ = false;
};

}  // namespace vgq
