// =============================================================================
//  VGQ-1 flight software executive, v3 (runs on the UNO Q STM32U585 MCU).
//
//  Functional analogue of a deep-space command & data handling system:
//    * CCS  (command subsystem)  : CLTU -> TC/USLP frame -> SDLS -> FARM-1 ->
//                                  command dispatch (live and stored sequences)
//    * FDS  (flight data subsys) : sampling, packetisation, VC0..3 multiplexing,
//                                  SDLS TM protection, SSR, CFDP file delivery
//    * AACS (attitude)           : Nicla Sense ME fusion + independent TRIAD
//                                  (calibrated boom RM3100 or BMM150) + BHI260AP
//                                  6-DOF (game rotation vector, 1 s window statistics)
//    * Telecom                   : one vgq::Radio (E22-400T33D, E22-400T37S or
//                                  SX126x) driven by events, duty-cycle control,
//                                  downlink load plan
//    * Fault protection (FDIR)   : command loss, thermal, bus (per sensor bus, D-73),
//                                  radio monitors, instrument loss, beacon urgency level
//  The protocol- and security-critical decisions live in pure, host-tested
//  modules (datalink.h, commands.h, autonomy.h, spectral.h, tlm_packets.h,
//  ssr.h); this class wires them to the hardware. Phase B specification PB-8.6
//  is the integration recipe; every rule cited as PB-n is from that document.
//
//  Timing rules (PB-0.4): the main loop never blocks for long and never calls
//  Bridge.call() (it has no timeout); Linux is only ever NOTIFIED, and only when
//  the router UART has room for the whole notification, so no write waits.
//  Documented exceptions, setup() only: Bridge.begin(), Bridge.provide_safe(), the
//  instrument identification; in the loop: E22 configure() / set_hibernation()
//  (typically 60-80 ms, bounded ~4 s on a dead module) and one RM3100 self-test
//  (<= ~5 ms). Monitor/Serial output is compiled out (VGQ_DEBUG_MONITOR = 0).
// =============================================================================
#pragma once
#include <Arduino.h>
#include "../config.h"
#include "../ccsds/cfdp.h"
#include "../ccsds/sdls.h"
#include "../ccsds/tc.h"
#include "../ccsds/tm.h"
#include "../drivers/as7265x.h"
#include "../drivers/i2c_bus.h"
#include "../drivers/payload.h"
#include "../drivers/radio.h"
#include "../drivers/sensors.h"
#include "attitude.h"
#include "autonomy.h"
#include "commands.h"
#include "datalink.h"
#include "rpc_mailbox.h"
#include "sclk.h"
#include "sensor_bus.h"
#include "spectral.h"
#include "ssr.h"
#include "status_display.h"
#include "tlm_packets.h"

namespace vgq {

class Flight {
 public:
  // Boot sequence (PB-8.6 item 2). bridge_failures: names of the Router Bridge
  // registrations sketch.ino could not complete ("" or nullptr = none), reported as
  // EVR 0x0005 after the boot EVRs.
  void setup(const char* bridge_failures);
  void loop();
  // Hardware watchdog refresh (config.h VGQ_IWDG): sketch.ino loop() calls it once per
  // completed pass. It refreshes only on the loop thread (never from the Bridge thread).
  void watchdog_feed();

  // ---- EGSE (Linux side) services: Router Bridge RPC handlers (PB-4.1) ----
  // sketch.ino's provide_safe() handlers call these. The bundled patched
  // Arduino_RouterBridge (contract 41) runs them only from update_safe() in __loopHook,
  // on the loop thread, where they run the egse_* service below directly. Called on any
  // other thread (the stock 0.4.3 also runs provide_safe handlers on its "bridge" thread,
  // rpc_mailbox.h) they only post into the mailbox and wait for the loop, which runs the
  // service on its own stack. rpc_begin() first, before any registration.
  void rpc_begin() { rpc_.begin(); }
  // set_partition: SCLK partition = SDLS TM epoch. false if p is not 1..65534 or the
  // epoch is lower than the current one (nonce safety); the same value is a no-op.
  bool rpc_set_partition(int p);
  // set_sdls_sn: restores the TC anti-replay counter (int32 two's complement of the u32
  // SN); it can only rise. Always true once executed.
  bool rpc_set_sdls_sn(int sn);
  // hl_cltu: one CLTU, 2..220 hex characters, through the same acceptance chain as RF.
  bool rpc_hardline_cltu(const char* hex, size_t len);
  // cfdp_pdu: one EGSE-built CFDP PDU (20..220 hex characters): 1 queued on VC3, 0 busy
  // (retry), -1 refused (malformed, or not the authorised relay transfer).
  int rpc_cfdp_pdu(const char* hex, size_t len);

 private:
  // The services themselves, loop thread only (service_rpc() or a handler run there).
  bool egse_set_partition(int p);
  bool egse_set_sdls_sn(int sn);
  bool egse_hardline_cltu(const char* hex, size_t len);
  int egse_cfdp_pdu(const char* hex, size_t len);
  void service_rpc();                                   // one mailbox request, if any

  enum TxState : uint8_t { TX_IDLE = 0, TX_WAIT_DONE = 1 };
  enum ScanState : uint8_t { SCAN_IDLE = 0, SCAN_SET, SCAN_SETTLE, SCAN_WAIT_REPLY };
  enum MagPhase : uint8_t { MAG_IDLE = 0, MAG_WAIT = 1, MAG_COLLECT = 2 };
  enum SpecReport : uint8_t { SPEC_REP_OK = 0, SPEC_REP_ABSENT, SPEC_REP_FAULT };

  // ---- boot ----
  static SpecTuning spec_tuning();                      // config.h kSpecBaselineAlpha, kSpecEventCdeg
  static SpecConfig spec_default_config();              // config.h kSpecDefault*, kSpecBurstCycles
  uint8_t read_reset_cause();
  void watchdog_start();                                // end of setup(): IWDG armed (config.h)
  void init_radio();
  void init_sensors();
  void report_radio(bool boot);                         // EVR 0x0306
  void report_radio_model();                            // EVR 0x0308 (E22 model strap, contract 33)
  void report_tcxo();                                   // EVR 0x0309 (SX126x TCXO fall-back, DS-09)
  void report_spec_identity();                          // EVR 0x0706 (+ 0x060D off its bus)
  // EVR 0x060B "instrument acquired: <name> on <bus>", and WARN 0x060D when `bus` is not the
  // instrument's preferred bus (D-73).
  void report_acquired(const char* name, uint8_t bus, uint8_t preferred);
  bool probe_nicla();                                   // A4/A5 then Qwiic; aru_bus_
  bool probe_rm3100(uint8_t unit, uint16_t cc);         // Qwiic then A4/A5; mag_bus_of_[unit]
  void run_boot_bist();

  // ---- telecom ----
  void service_radio(uint32_t now);                     // <= 4 Radio::poll() events per pass
  void on_uplink_packet(uint32_t now);
  void on_noise(uint32_t now, int32_t dbm);
  void on_tx_end(int32_t elapsed_ms, bool timeout);
  void on_radio_fault(uint32_t now, int32_t code);
  void service_tx(uint32_t now);
  void send_next_frame(uint32_t now);
  uint32_t busy_estimate_ms() const;
  uint32_t period_now_ms() const;                       // frame period in force (PB-3.3)
  uint32_t tx_period_ms() const;                        // spacing service_tx() applies (hibernation: beacon_s)
  uint16_t duty_permille(uint32_t now) const;           // TxDutyMeter over whole transmission cycles
  void apply_radio_config(uint32_t now);
  void service_scan(uint32_t now);
  void scan_store(int32_t dbm, uint32_t now);
  void enter_hibernate(uint16_t beacon_s);
  void do_reboot(const char* why);                      // commanded REBOOT: drain, NVIC_SystemReset
  void exit_hibernate(const char* why);
  void process_tc(const uint8_t* in, size_t n, bool hardline, bool crc_ok);

  // ---- commanding ----
  // tc_seq: N(S) of the carrying AD frame (0 otherwise); source: CmdSource of the command,
  // passed by each call site (CMDVER stage bits 7..6, PB-1.6 / I1).
  void execute_user(const uint8_t* u, size_t n, uint8_t tc_seq, CmdSource source);
  void cmd_reject(uint8_t tc_seq, CmdSource source, uint8_t opcode, CmdError e);
  void cmd_verify(uint8_t seq, CmdSource source, uint8_t opcode, uint8_t stage, uint8_t err, uint32_t tag);
  void set_mode(uint8_t m, const char* why);
  void enter_fdir_safe(const char* why);
  uint8_t cmd_cfdp_put(const Command& c);
  void cmd_cfdp_cancel();
  uint8_t cmd_specmode(const Command& c);
  uint8_t cmd_magbist(uint8_t unit);
  uint8_t cmd_seqload(const Command& c);
  uint8_t cmd_seqctrl(const Command& c);
  uint8_t cmd_magcal(const Command& c);

  // ---- data handling ----
  bool emit(uint16_t apid, const uint8_t* payload, size_t n, uint8_t vc, bool record = true,
            bool priority = false);
  void evr(uint8_t sev, uint16_t id, const char* fmt, ...) __attribute__((format(printf, 4, 5)));
  void tick_1hz(uint32_t now);
  void periods_in_force(uint16_t period_s[kNumPeriodic]) const;
  void gen_periodic(uint8_t idx);
  void gen_hk(bool beacon), gen_rf(), gen_timecorr(), gen_mag(), gen_att(), gen_env();
  void fdir_1hz(uint32_t now);
  void update_display(uint32_t now);
  BeaconSample beacon_sample() const;

  // ---- instruments ----
  void service_spec(uint32_t now);
  void handle_spec_result(uint32_t now);
  void drain_burst();
  void spec_health(uint32_t now);
  bool spec_attitude(SpecAttitude& att);
  bool triad(Quat& q, bool& from_rm3100);
  void service_mag(uint32_t now);
  void start_mag_sample();
  void finish_mag_unit(uint8_t unit, Rm3100& m);
  void note_mag_sample(uint8_t unit, bool ok);          // RM3100 loss detection (D-73)
  void service_bist(uint32_t now);
  void run_bist_unit(uint8_t unit);
  I2cBus& bus_of(uint8_t b) { return b == SBUS_MAG ? mag_bus_ : nicla_bus_; }
  void bus_recover(uint8_t bus, uint32_t now, const char* why);   // that bus only, its own limiter
  void reprobe_instruments(uint32_t now);               // C28: units missing since boot or lost
  bool apply_mag_cc();                                  // MAGCC between samples (C29)
  void note_valid_tc(uint32_t now);                     // command-loss timer and HK age

  // ---- CFDP (PB-2.5) ----
  void service_cfdp(uint32_t now);
  void cfdp_hk(uint8_t& state, uint32_t& seq, uint16_t& pm) const;

  // ---- EGSE notifications (PB-0.4) ----
  bool notify_room(size_t payload_octets) const;
  void queue_cadu_copy(const uint8_t* cadu);
  bool send_cadu(const uint8_t* cadu);
  void flush_notifications(uint32_t now);
  void fill_status(EgseStatus& s);

  // ---- subsystems ----
  Radio& radio_ = flight_radio();
  RadioSettings rcfg_, pending_cfg_, revert_cfg_;
  RxPacket rx_pkt_;
  I2cBus nicla_bus_;                     // NICLA_WIRE, A4/A5, bus clear: Nicla Sense ME (+ AS7265X)
  I2cBus mag_bus_;                       // MAG_WIRE, Qwiic, re-init only: the boom RM3100s
  RecoverLimiter recover_[kSensorBuses]; // per-bus FDIR recovery rate limit (D-73)
  bool bus_clear_failed_[kSensorBuses] = {false, false};   // the last bus clear left SDA/SCL low
  NiclaAru aru_;                         // Nicla Sense ME (attitude reference unit)
  As7265x spec_;                         // AS7265X spectrometer driver
  SpecConfig spec_cfg_ = spec_default_config();   // SPECCFG / SPECMODE configuration in force
  SpecProcessor spec_proc_{spec_tuning()};
  Rm3100 mag_ob_, mag_ib_;               // science magnetometers (boom tip, optional mid-point)
  StatusDisplay display_;
  Sclk sclk_;
  Ssr ssr_;
  SsrFileSource ssr_src_{ssr_};
  Downlink dl_;
  ccsds::Farm1 farm_{10};
  ccsds::BchStats bch_;
  ccsds::Sdls sdls_tc_;
  ccsds::SdlsTm sdls_tm_;
  ccsds::CfdpSender cfdp_tx_;
  ccsds::CfdpRelayMonitor relay_;
  RelayGate relay_gate_;
  EvrGate evr_gate_;
  LoadPlan load_{100, kEvrMaxMilliPerS, 0};
  CommandSequencer seq_;
  BeaconMonitor beacon_;
  MagCalStore magcal_;
  MagBistResult bist_ob_ = mag_bist_not_run(), bist_ib_ = mag_bist_not_run();
  bool safe_by_fdir_ = true;             // SAFE entered by boot or fault protection (autonomy)
  bool in_setup_ = true;                 // boot EVRs are exempt from the routine budget
  bool watchdog_armed_ = false;          // IWDG started by watchdog_start()

  // ---- mode, FDIR, cadence ----
  uint8_t mode_ = MODE_BOOT;
  uint8_t reset_cause_ = 255;
  uint16_t apid_seq_[kNumApids] = {0};   // per-APID source sequence counters (PB-1.1)
  uint32_t last_gen_s_[kNumPeriodic] = {0};
  uint16_t period_override_[kNumPeriodic] = {0};   // PKTRATE: 0 = mode table, 0xFFFF = off
  uint16_t sensor_health_ = 0, sensor_enable_ = kSensorInstrumentMask;
  uint8_t aru_fail_count_ = 0;
  uint16_t fdir_flags_ = 0, fdir_mask_ = 0xFFFF;
  uint8_t evr_level_ = 1;
  uint16_t stretch_reported_ = 100;      // LoadPlan stretch of the last EVR 0x0215
  uint32_t last_refill_ms_ = 0;

  // ---- telecom ----
  TxState tx_state_ = TX_IDLE;
  uint32_t tx_start_ms_ = 0, last_tx_start_ms_ = 0, last_airtime_ms_ = 0;
  uint64_t tx_start_sclk_us_ = 0;
  DownlinkFrame tx_frame_;               // the frame in flight
  // TIMECORR reference: the latest frame that qualifies (TM: any; USLP: VC0, §6)
  bool tc_ref_valid_ = false;
  uint8_t tc_ref_count_ = 0, tc_ref_rate_ = 0;
  uint64_t tc_ref_sclk_us_ = 0;
  uint32_t tc_ref_airtime_ms_ = 0;
  bool timecorr_request_ = false;
  TxDutyMeter duty_;                     // RF tx_duty / sc_status duty_pct (whole TX cycles)
  uint32_t hold_period_ms_ = 0;          // spacing owed to the last frame at the previous radio
                                         // settings (datalink.h reconfig_hold_ms), 0 = none
  uint32_t frames_sent_ = 0, oid_sent_ = 0, airtime_total_ms_ = 0, tx_refused_ = 0;
  uint16_t aux_timeouts_ = 0;
  uint16_t cmd_frame_period_ms_ = 0;
  bool tx_inhibit_ = false;
  uint32_t tx_inhibit_until_ms_ = 0;
  bool radio_cfg_ok_ = false, radio_reconfig_pending_ = false;
  // Coordinated radio change (rate code / channel): applied after N more frames at the
  // old setting, reverted automatically unless the ground proves it followed by getting
  // a valid TC through on the new setting.
  bool pending_cfg_valid_ = false, revert_armed_ = false;
  uint8_t cfg_change_after_frames_ = 0;
  uint32_t revert_deadline_ms_ = 0, revert_window_ms_ = 0;
  // RF spectrum survey (RFSCAN)
  ScanState scan_state_ = SCAN_IDLE;
  uint8_t scan_first_ = 0, scan_last_ = 0, scan_ch_ = 0;
  int8_t scan_noise_[kRfscanMaxChannels];
  uint32_t scan_t0_ = 0, scan_step_ms_ = 0;
  // Hibernation (E22 Wake-on-Radio / SX126x duty-cycled RX)
  bool hibernating_ = false, hib_apply_pending_ = false;
  uint16_t beacon_s_ = 600;
  // Radio faults: 1..4 E22-400T37S abnormal status, 16..19 SX126x driver faults
  uint8_t radio_fault_code_ = 0;
  uint32_t radio_fault_ms_ = 0;
  uint16_t radio_fault_reports_ = 0;
  bool reboot_pending_ = false;
  uint8_t reboot_after_frames_ = 0;
  // REBOOT normally resets after 2 more frames (the CMDVER goes out first, PB-2.3); this
  // deadline bounds the wait when no frame can be sent (dead radio, TX inhibit).
  uint32_t reboot_deadline_ms_ = 0;
  uint8_t beacon_frames_left_ = 0;       // hibernation beacon: frames still due (<= 2)

  // ---- uplink ----
  uint32_t last_rx_flash_ms_ = 0, last_noise_ms_ = 0;
  int16_t uplink_rssi_ = kRadioNaI16, uplink_snr_cdb_ = kRadioNaI16, noise_dbm_ = kRadioNaI16;
  int32_t uplink_ferr_hz_ = kRadioNaI32;
  uint16_t cltu_ok_ = 0, cltu_bad_ = 0, tc_ok_ = 0, tc_bad_ = 0;
  uint32_t tc_rescued_ = 0;              // CRC-failed uplink packets accepted (contract 20)
  uint32_t last_valid_tc_ms_ = 0, last_cltu_ms_ = 0;
  // Latched in tick_1hz / process_tc rather than recomputed from a millis() difference,
  // which repeats every 2^32 ms (49.7 days without uplink, C23).
  bool no_bit_lock_ = false;
  uint32_t tc_age_s_ = 0, tc_age_ref_ms_ = 0;   // HK cmd_loss_timer_s: s since the last valid TC
  uint32_t cmd_loss_s_ = kDefaultCmdLossS;
  uint16_t cmd_acc_ = 0, cmd_rej_ = 0;
  uint8_t last_opcode_ = 0, last_status_ = 0;

  // ---- timing ----
  uint32_t last_1hz_ms_ = 0, last_disp_ms_ = 0, loop_start_ms_ = 0;
  uint16_t loop_max_ms_ = 0, loop_overruns_ = 0;
  uint32_t last_callsign_s_ = 0;
  uint8_t status_x_div_ = 0;

  // ---- magnetometers and the ARU ----
  MagStats mstat_ob_, mstat_ib_, mstat_body_;
  bool mag_tx_keyed_ = false;            // MAG b0: a sample of this interval saw the PA keyed
  bool mag_lamp_ = false;                // MAG b4: ... or a lamp lit / in an unknown state
  MagPhase mag_phase_ = MAG_IDLE;
  bool sample_due_ = false;              // 1 Hz sample requested
  bool mag_sample_pending_ = false;      // closes the AS7265X lamp gate
  uint32_t mag_wait_t0_ = 0, mag_collect_t0_ = 0;
  bool mag_pending_[kMagUnits] = {false, false};
  float rm_ut_[kMagUnits][3] = {{0, 0, 0}, {0, 0, 0}};   // latest raw RM3100 field, sensor axes
  bool rm_fresh_[kMagUnits] = {false, false};            // the latest attempt succeeded
  float bus_temp_c_ = NAN;               // Nicla Sense ME temperature (spacecraft bus); NAN = no fresh reading
  uint16_t mag_cc_ = 200;                // RM3100 cycle count in force (MAGCC; begin() default)
  bool mag_cc_pending_ = false;          // MAGCC waits for the end of a sample
  ReprobeTimer reprobe_;                 // re-acquisition of instruments missing since boot or lost
  uint8_t aru_bus_ = kSensorBusNone;     // bus the Nicla Sense ME answered on (SensorBus)
  uint8_t mag_bus_of_[kMagUnits] = {kSensorBusNone, kSensorBusNone};   // ... each RM3100
  uint8_t spec_bus_reported_ = kSensorBusNone;   // AS7265X bus of the last identity EVR
  LossCounter mag_loss_[kMagUnits];      // consecutive failed RM3100 samples (D-73)
  AruReading aru_last_;
  bool aru_valid_ = false, aru_env_valid_ = false;
  // BHI260AP 6-DOF (ARU page 2, D-75): the latest page, whether it pairs with aru_last_
  // (same ARU rebuild), and the window statistics of the current ATT period.
  AruImu aru_imu_;
  bool aru_imu_valid_ = false, aru_imu_paired_ = false;
  ImuStats imu_stats_;
  float last_mag_ut_[3] = {0, 0, 0};     // latest BMM150 sample (ATT, TRIAD fallback)
  uint8_t bist_pending_ = 0;             // MAGBIST units waiting for a PA-off, lamps-off window
  uint32_t bist_wait_t0_ = 0;

  // ---- spectrometer ----
  bool spec_start_req_ = false;          // a SPEC cycle is due (cadence or SPECMODE BURST)
  SpecReport spec_reported_ = SPEC_REP_OK;
  bool spec_unknown_reported_ = false;   // EVR 0x0707 LAMP STATE UNKNOWN raised
  bool no_lamp_reported_ = false;        // EVR 0x0707 for ERR_NO_LAMP, once per configuration
  uint16_t burst_tx_frames_ = 0;         // frames transmitted while the burst ran (C-26)
  uint32_t i2c_err_snapshot_ = 0, i2c_quiet_s_ = 0;

  // ---- CFDP ----
  uint16_t cfdp_n_ = 0;                  // per-boot transfer counter 1..0xFFFE
  bool cfdp_ssr_active_ = false;         // an SSR export is being sent (end_export pending)
  uint32_t cfdp_paused_base_ = 0;        // Ssr::paused_drops() when the export began
  uint32_t cfdp_last_seq_ = 0;
  uint16_t cfdp_last_pm_ = 0;

  // ---- EGSE notifications (PB-0.4) ----
  RpcMailbox rpc_;                       // handler off the loop thread -> loop (rpc_mailbox.h)
  bool large_sent_ = false;              // one large notification per pass
  uint8_t evr_notes_pass_ = 0, evr_notes_sec_ = 0;
  uint32_t evr_note_window_ = 0;         // millis() / 1000 of the current sc_evr window
  uint8_t cadu_copy_[ccsds::kCaduLen];
  bool cadu_copy_valid_ = false;
  uint32_t evr_nd_ = 0, cadu_nd_ = 0, emit_refused_ = 0;
  bool status_due_ = false, status_x_due_ = false;
  bool boot_note_pending_ = false, sdls_sn_pending_ = false;
  bool cfdp_req_pending_ = false, cfdp_cancel_pending_ = false;
  char cfdp_req_text_[32] = {0};
  uint32_t cfdp_cancel_seq_ = 0;
};

}  // namespace vgq
