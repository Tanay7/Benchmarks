// =============================================================================
//  AS7265X 18-channel spectrometer driver (ams AS72651 + AS72652 + AS72653, SparkFun
//  "Triad" board), I2C address 0x49 on the sensor bus (Wire2, header pins - never a
//  Qwiic connector). Own implementation of the datasheet's virtual-register protocol
//  (ams AS7265x datasheet v1-04, "I2C Virtual Register Write/Read Access",
//  Figures 24-51); no vendor library.
//
//  Non-blocking by construction. Every virtual-register access is the datasheet's
//  four-step handshake (poll STATUS.TX_VALID, write the address, poll RX_VALID /
//  TX_VALID, move the data byte); each step is one I2C transaction and is a state of
//  a small engine. poll() runs that engine for at most the poll budget (8 ms) and at
//  most kSpecMaxStepsPerPoll steps (each <= 2 transactions), then returns;
//  integration times (up to 2 x 711 ms) are waited for by time, never by spinning.
//  Every status poll has a deadline and a count limit, so a missing or hung chip set
//  costs a bounded number of transactions and then a soft-reset recovery - it can
//  never stall the flight loop. A failed STATUS read (side-effect free) is retried;
//  any other failed transaction ends the cycle with an invalid result.
//
//  Protocol resynchronisation: a failed transaction of a virtual WRITE leaves the chip
//  either idle or waiting for that write's data byte - the master cannot tell which (a
//  lost ACK looks like a NACK). The next operation list therefore starts with the single
//  byte spec_vreg_resync_byte() (spectral.h), never with a write address or a resent
//  data byte: it is harmless as data and as a read request, and leaves the chip idle.
//  The integration and LED shadows are invalidated, and every lamp-off list reads
//  LED_CONFIG back: lamps_lit() clears only on a read-back with LED_DRV disabled.
//
//  Chip features used: GAIN, integration time, BANK modes 0-3,
//  DATA_RDY and the optional INT pin (level-polled, active LOW, no ISR), LED_DRV per
//  device as electronic shutter (white = AS72651, IR = AS72652, UV = AS72653 on the
//  SparkFun board), LED_IND on the AS72651 as a "measurement in progress" marker,
//  per-device temperature, DEV_SEL slave-available flags, HW/FW version, soft reset,
//  read-only COEF dump. NEVER written: COEF_WRITE (0x55, persistent calibration
//  memory), factory reset (CONFIG.FRST) and the firmware-update registers - the
//  operation builder refuses them (spec_vreg_write_allowed), and a byte with bit 7 set
//  is only ever sent as a screened write address or as the data byte of the same
//  handshake, never on its own. Lamp currents are capped per lamp by
//  kSpecLedMaxCode (UV 12.5 mA, IR 50 mA, white 100 mA: the board vendor's limits).
//
//  Acquisition modes (spectral.h SpecMode): SURVEY (+AGC), HDR, LOCKIN (ABBA
//  lamp-on/lamp-off pairs), FLUOR (UV excitation), BURST (continuous BANK 2/0/1
//  sampling into a ring). Lamps are lit only for an exposure and switched off as soon
//  as DATA_RDY is seen, before the data are read; lamps_lit() / set_lamp_gate() let
//  the executive keep them dark while the RM3100s sample, and set_exposure_gate()
//  lets it hold every new exposure (e.g. to keep exposures in PA-off windows).
//
//  Integrator call order:
//    setup: begin(bus, kPinSpecInt)        (bounded, see begin())
//    loop : set_lamp_gate(!rm3100_sample_due); set_exposure_gate(...)   (optional)
//           poll(now)                                   (every pass, PA on or off)
//           if (result_ready()) { use result(); consume_result(); }
//           while (burst_available() >= 3 || (!burst_active() && burst_available()))
//             burst_pop(...)
//           pop_diag_line(), take_recovered()
//    tick : start_cycle(cfg, now) when a SPEC packet is due (false = busy: retry later)
// =============================================================================
#pragma once
#include <Arduino.h>
#include "i2c_bus.h"
#include "../fsw/spectral.h"

namespace vgq {

// ---- Timing and bounds -----------------------------------------------------------------
static const uint32_t kSpecPollBudgetUs = 8000;    // default I2C time per poll() (loop limit ~20 ms)
static const uint8_t  kSpecMaxStepsPerPoll = 32;   // hard bound on engine steps per poll()
// One handshake step (STATUS poll). The datasheet gives no response-time bound (its
// sample code polls forever); this only bounds a hung chip. VERIFY with T-SC-08 (docs/08).
// The time limit counts only once kSpecMinStatusPolls polls were made, so a long gap
// between two loop passes is never mistaken for a hung chip; the poll count alone
// bounds the work.
static const uint32_t kSpecVregTimeoutMs = 50;
static const uint16_t kSpecMinStatusPolls = 8;
static const uint16_t kSpecMaxStatusPolls = 400;   // second bound, independent of millis()
static const uint8_t  kSpecStatusRetries = 3;      // failed STATUS reads tolerated in a row
static const uint32_t kSpecDrdyMarginMs = 50;      // DATA_RDY deadline = conversion + 1/32 + this
static const uint32_t kSpecDrdyPollMs = 3;         // CONFIG polling period while waiting
// Soft-reset settling time. Not specified by the datasheet (RESN needs > 100 ms); the
// SparkFun library allows 1000 ms. VERIFY with T-SC-08.
static const uint32_t kSpecResetWaitMs = 1000;
// Boot wait: the AS72651 loads its firmware from the board's EEPROM at power on (SparkFun
// hookup guide) and does not ACK its address until it runs. SparkFun isConnected() polls
// for the ACK every 10 ms, 100 times ("Give IC 660ms for startup - max 1000ms"); the
// driver does the same at begin() and after kSpecResetWaitMs of every soft reset, and
// identifies the chip only once it answers. VERIFY the boot time with T-SC-08.
static const uint32_t kSpecBootProbeMs = 10;
static const uint32_t kSpecBootWaitMs = 1000;
// After a resynchronisation byte (spectral.h spec_vreg_resync_byte) the chip may answer it
// as a read request. The datasheet does not say how soon RX_VALID follows TX_VALID = 0,
// so this many further idle STATUS polls are made (draining an answer that appears)
// before the next operation. VERIFY with T-SC-08.
static const uint16_t kSpecResyncPolls = kSpecMinStatusPolls;
static const uint8_t  kSpecMaxConsecFails = 3;     // failed cycles before a soft reset
static const uint32_t kSpecRetryMs = 30000;        // FAULT / ABSENT retry, doubling to 10 min
static const uint32_t kSpecRetryMaxMs = 600000;
static const uint32_t kSpecLampMarginMs = 500;     // lamp watchdog beyond the DATA_RDY deadline
static const uint8_t  kSpecAgcMaxProbes = 4;       // AGC probe exposures per cycle
static const uint8_t  kSpecBurstRing = 12;         // burst samples buffered (4 packets)

class As7265x {
 public:
  static const uint8_t kAddr = 0x49;
  enum State : uint8_t {
    ST_ABSENT = 0,   // no ACK at 0x49 (re-probed every kSpecRetryMs)
    ST_INIT = 1,     // identification / configuration in progress
    ST_IDLE = 2,     // ready for start_cycle()
    ST_BUSY = 3,     // acquisition cycle, burst or SPECDIAG in progress
    ST_RESET = 4,    // soft reset issued, settling
    ST_FAULT = 5,    // recovery failed, retry scheduled
  };
  enum Error : uint8_t {
    ERR_NONE = 0, ERR_I2C = 1, ERR_HANDSHAKE = 2, ERR_DRDY = 3, ERR_VERIFY = 4,
    ERR_LAMP_WATCHDOG = 5, ERR_INTERNAL = 6,
    ERR_NO_LAMP = 7,     // LOCKIN / FLUOR whose lamps all sit on absent slaves: cycle invalid
  };
  struct Info {
    uint8_t hw_type = 0, hw_version = 0;      // virtual 0x00 / 0x01 (datasheet defaults 0x40 / 0x41)
    uint8_t fw_major = 0, fw_patch = 0, fw_build = 0;
    uint8_t dev_sel = 0;                      // last DEV_SEL read: b4 first slave, b5 second (VERIFY)
    uint8_t dev_mask = 0;                     // b0 AS72651 (always), b1 AS72652, b2 AS72653 per DEV_SEL
    int8_t temp[kSpecDevices] = {-128, -128, -128};   // degC, -128 = n/a
    bool int_pin_used = false;
    bool int_pin_fault = false;               // INT never asserted although DATA_RDY was set
  };

  // Probes 0x49 (every kSpecBootProbeMs for up to kSpecBootWaitMs, as SparkFun
  // isConnected(): the chip may still be loading its firmware) and runs identification +
  // configuration to completion (lamps and indicator off, one-shot mode). int_pin: MCU pin
  // wired to the board's INT, -1 = not wired (DATA_RDY is then polled over I2C). Blocking
  // but bounded: the boot probe (<= kSpecBootWaitMs plus 100 probes) then at most
  // kBeginMaxPolls passes of poll() + 5 ms, which covers one soft reset with its boot wait;
  // call from setup() only. false = absent or not responding: poll() keeps retrying
  // (ST_ABSENT / ST_FAULT) and the instrument comes up when it answers.
  bool begin(I2cBus& bus, int int_pin);

  // Advances the state machine; never blocks longer than the poll budget plus one engine
  // step (<= 2 I2C transactions). Safe with the PA keyed. now_ms = millis() of this pass.
  State poll(uint32_t now_ms);
  // Per-poll I2C time budget, clamped to 1000..15000 us (e.g. 2000 us while the PA is keyed,
  // to keep AUX-edge timing tight; kSpecPollBudgetUs otherwise). The lamp-off lists (after
  // DATA_RDY and the safe-off recovery) always run with at least kSpecPollBudgetUs: they
  // are the lamp-safety action.
  void set_poll_budget_us(uint32_t us) { budget_us_ = us < 1000 ? 1000 : (us > 15000 ? 15000 : us); }

  // Starts one acquisition cycle (validated with spec_config_valid; the config is copied).
  // Returns false if the config is invalid or the driver is not ST_IDLE. Discards an
  // unconsumed result.
  bool start_cycle(const SpecConfig& cfg, uint32_t now_ms);
  // Ends the current cycle / burst at the next safe point: lamps off, no result.
  void abort();

  // True once a cycle or burst has ended (valid or not, see SpecResult.flags b0); the driver
  // is then ST_IDLE (lamps and indicator off) unless a recovery is under way.
  bool result_ready() const { return result_ready_; }
  const SpecResult& result() const { return result_; }   // valid until consume_result()
  void consume_result() { result_ready_ = false; }
  uint16_t progress_permille() const;

  // ---- BURST ----
  bool burst_active() const { return burst_active_; }
  uint8_t burst_available() const { return ring_n_; }
  // Copies up to max (<= kSpecBurstPerPacket is the packet size) oldest samples; returns count.
  uint8_t burst_pop(SpecBurstSample* out, uint8_t max);
  uint32_t burst_t0_ms() const { return burst_t0_ms_; }   // millis() of the first sample
  uint16_t burst_rate_dhz() const;                        // achieved so far, 0.1 Hz
  const SpecExposure& burst_exposure() const { return burst_exp_; }

  // ---- RM3100 coordination and PA-off windows ----
  // True from the moment a lamp-on write is issued until a read-back of LED_CONFIG shows
  // LED_DRV disabled on every present device (conservative). A closed gate never
  // interrupts an exposure: the lamp gate keeps the next lamp-on exposure from starting,
  // the exposure gate every next exposure (BURST is continuous and is not gated). A gate
  // held closed holds the cycle indefinitely; it ends only when the gate opens or on abort().
  // The lamp watchdog ends a lit exposure whose DATA_RDY is overdue (trigger + conversion
  // + kSpecDrdyMarginMs + kSpecLampMarginMs) but never interrupts a lamp-off list.
  bool lamps_lit() const { return lamps_lit_; }
  // Lamp state unknown: ST_FAULT while a lamp may still be lit (the chip stopped answering
  // before a lamp-off was confirmed). The driver probes 0x49 every kSpecResetWaitMs
  // (uncounted) and, as soon as it ACKs, re-initialises or soft-resets it (both switch every
  // LED driver off); until then the executive must treat the lamp as lit (contract 3.3:
  // sample anyway and set MAG flags b4, or skip).
  bool lamp_state_unknown() const { return state_ == ST_FAULT && lamps_lit_; }
  void set_lamp_gate(bool open) { lamp_gate_open_ = open; }
  void set_exposure_gate(bool open) { exposure_gate_open_ = open; }

  // ---- SPECDIAG (0x1C) ----
  // Queues a read-only dump of one device: a header line (HW version as read with that
  // DEV_SEL, the boot FW version, DEV_SEL, LED_CONFIG, CONFIG, INTEGRATION, temperature)
  // then COEF words for sub-addresses 0..n_sub-1, four per line (<= 80 characters each).
  // Runs between cycles; false = invalid arguments, a dump already queued or no chip.
  bool request_diag(uint8_t device, uint8_t n_sub);
  bool pop_diag_line(char* text, size_t cap);             // false = no line waiting
  bool diag_pending() const { return diag_pending_ || diag_line_ready_; }

  // ---- health ----
  bool ok() const { return state_ == ST_IDLE || state_ == ST_BUSY; }
  State state() const { return state_; }
  const Info& info() const { return info_; }
  bool take_recovered();          // true once after a successful soft-reset recovery (EVR 0x0705)
  uint32_t errors() const { return errors_; }
  uint16_t resets() const { return resets_; }
  uint8_t last_error() const { return last_err_; }

 private:
  enum OpKind : uint8_t { OPK_READ = 0, OPK_WRITE = 1, OPK_DATA = 2 };
  struct Op { uint8_t kind, vreg, data; };
  enum Phase : uint8_t {
    PH_NONE, PH_INIT_ID, PH_INIT_CFG, PH_GATE, PH_SETUP, PH_WAIT, PH_DRDY, PH_LAMPS_OFF,
    PH_READ, PH_BURST_SETUP, PH_BURST_WAIT, PH_BURST_DRDY, PH_BURST_READ, PH_BURST_STOP,
    PH_END, PH_DIAG, PH_SAFE_OFF, PH_SRST, PH_SRST_WAIT,
  };
  enum Stage : uint8_t { STAGE_PROBE, STAGE_MAIN };
  enum Step : uint8_t { STEP_CONT, STEP_FAIL };
  static const uint8_t kMaxOps = 48;

  // operation engine
  void ops_begin(Phase ph);
  void op_r(uint8_t vreg);
  void op_w(uint8_t vreg, uint8_t data);
  Step step_op();
  Step op_complete();
  Step handshake_wait();
  Step bus_glitch();
  void mark_unknown(const Op& op);
  bool reg_read(uint8_t reg, uint8_t& v);
  bool reg_write(uint8_t reg, uint8_t v);
  bool advance(uint32_t now);
  bool phase_tick(uint32_t now);
  void phase_done(uint32_t now);
  void on_failure(uint32_t now, uint8_t err);

  // sequencing
  void start_init();
  void start_srst(uint32_t now);
  void start_safe_off();
  void enter_fault(uint32_t now);
  bool fault_retry(uint32_t now);
  bool led_readback_lit(const uint8_t* b, uint8_t first, uint8_t stride, uint8_t dev_list) const;
  void begin_frame(uint32_t now);
  void build_setup(uint32_t now);
  void after_drdy(uint32_t now);
  void start_read(uint8_t from_dev);
  void frame_done(uint32_t now);
  void begin_main(uint32_t now);
  void finish_cycle(uint32_t now);
  void finish_no_lamp(uint32_t now);
  void finish_burst(uint32_t now);
  void end_cycle(uint32_t now);
  void build_burst_read();
  void burst_sample_done();
  bool build_diag();
  bool frame_wants_lamps() const;
  uint8_t led_value(uint8_t dev, bool lamp_on, bool ind_on) const;
  bool shadow_lit(uint8_t d) const;
  uint8_t config_value(uint8_t gain, uint8_t bank) const;
  void decode_devsel(uint8_t v);

  // bus and engine state
  I2cBus* bus_ = nullptr;
  int int_pin_ = -1;
  uint32_t budget_us_ = kSpecPollBudgetUs;
  State state_ = ST_ABSENT;
  Phase phase_ = PH_NONE;
  Op ops_[kMaxOps];
  uint8_t rd_[kMaxOps];
  uint8_t n_ops_ = 0, op_i_ = 0, sub_ = 0;
  uint8_t op_base_ = 0;                       // first operation of the phase (after a resync byte)
  bool ops_bad_ = false;                      // builder refused an operation (internal fault)
  uint32_t sub_t0_ = 0;
  uint16_t sub_polls_ = 0;
  uint8_t status_errs_ = 0;                   // consecutive failed STATUS reads
  uint16_t settle_ = 0;                       // idle STATUS polls after a resync byte
  uint8_t step_err_ = ERR_NONE;
  bool resync_ = false;                       // a virtual write's outcome is unknown
  uint8_t resync_vreg_ = 0;                   // ... its register (selects the resync byte)
  bool resync_srst_ = false;                  // ... and it was the soft reset (reissued)
  uint8_t devsel_op_ = 0xFF;                  // index of a DEV_SEL read in the current list
  uint8_t abort_req_ = ERR_NONE;              // pending abort (0 none, else an Error or 0xFF = command)

  // chip shadows (0 = unknown integration -> always written; LED: 0xFF = unknown after a
  // reset (drivers off), kLedUnknown = unknown after a failed write (possibly lit))
  static const uint8_t kLedUnknown = 0xFE;
  uint8_t led_shadow_[kSpecDevices] = {0xFF, 0xFF, 0xFF};
  uint8_t led_want_[kSpecDevices] = {0, 0, 0};
  uint8_t int_shadow_ = 0;
  Info info_;

  // health / recovery
  uint8_t consec_fail_ = 0, last_err_ = ERR_NONE, srst_fails_ = 0;
  uint32_t errors_ = 0;
  uint16_t resets_ = 0;
  bool reset_tried_ = false, in_recovery_ = false, recovered_ = false;
  bool fault_init_next_ = true;               // FAULT retries alternate init-only / soft reset
  uint32_t retry_at_ = 0, retry_ms_ = kSpecRetryMs, srst_t_ = 0, probe_t_ = 0;

  // cycle
  SpecConfig cfg_;
  bool cycle_active_ = false, agc_ = false, need_cal_ = true, ind_on_ = false;
  uint8_t lamps_eff_ = 0, frame_lamps_ = 0, cycle_dev_ = 0, read_dev_ = 0;
  uint8_t lamps_used_ = 0;                    // lamps actually commanded on in this cycle
  uint8_t off_list_ = 0;                      // devices in the current lamp-off list
  uint8_t stage_ = STAGE_MAIN, probes_ = 0, main_j_ = 0, main_total_ = 0, frames_done_ = 0;
  SpecExposure exp_{2, 59}, agc_exp_{2, 59}, center_{2, 59};
  SpecExposure bracket_[kSpecHdrMaxFrames];
  uint8_t n_bracket_ = 0;
  SpecFrame cur_, keep_, hdr_[kSpecHdrMaxFrames];
  SpecLockin lock_;
  uint16_t on_raw_[kSpecChannels];
  uint16_t on_peak_ = 0;
  uint32_t sat_acc_ = 0, on_sat_ = 0;
  uint32_t t_start_ = 0, t_trig_ = 0, conv_ms_ = 0, drdy_deadline_ = 0, last_drdy_poll_ = 0;
  bool lamps_lit_ = false, lamp_gate_open_ = true, exposure_gate_open_ = true;
  bool lamp_armed_ = false;                   // watchdog timer runs (a lit exposure was triggered)
  uint32_t lamp_on_ms_ = 0, lamp_limit_ms_ = 0;
  SpecResult result_;
  bool result_ready_ = false;
  bool result_held_ = false;                  // result complete, indicator-off write pending

  // burst
  bool burst_active_ = false;
  SpecExposure burst_exp_{0, 1};
  uint8_t burst_bank_ = 2;
  uint32_t burst_start_ = 0, burst_t0_ms_ = 0, burst_last_ms_ = 0, burst_ref_ms_ = 0;
  uint32_t burst_sample_ms_ = 0;
  uint16_t burst_n_ = 0, burst_drop_ = 0, burst_read_max_ = 0;
  uint32_t burst_sum_[kSpecChannels];
  uint32_t burst_sat_ = 0;
  SpecBurstSample ring_[kSpecBurstRing];
  uint8_t ring_head_ = 0, ring_n_ = 0;
  SpecBurstSample bsample_;

  // diagnostics
  bool diag_pending_ = false, diag_line_ready_ = false, diag_hdr_ = false;
  uint8_t diag_dev_ = 0, diag_n_ = 0, diag_k_ = 0, diag_chunk_ = 0;
  char diag_line_[81];
};

}  // namespace vgq
