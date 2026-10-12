#include "as7265x.h"
#include <math.h>
#include <stdio.h>
#include <string.h>

namespace vgq {

namespace {

// Physical I2C slave registers (datasheet Figure 24).
const uint8_t kRegStatus = 0x00, kRegWrite = 0x01, kRegRead = 0x02;
const uint8_t kTxValid = 0x02, kRxValid = 0x01;
// Virtual registers (Figure 26). Multi-byte values are big-endian and are read in
// ascending address order (the chip shadows the rest after the first byte). The writable
// ones and the write policy live in spectral.h (host-tested).
const uint8_t kVHwH = 0x00, kVHwL = 0x01, kVTemp = 0x06, kVRaw = 0x08, kVCal = 0x14,
              kVCoefData = 0x50;
const uint8_t kVFwH = kSpecVFwH, kVFwL = kSpecVFwL, kVConfig = kSpecVConfig, kVInteg = kSpecVInteg,
              kVLed = kSpecVLed, kVDevSel = kSpecVDevSel, kVCoefRead = kSpecVCoefRead;
// CONFIG (Figure 29) and LED_CONFIG (Figure 32) bits.
const uint8_t kCfgSrst = 0x80, kCfgInt = 0x40, kCfgDataRdy = 0x02;
const uint8_t kLedDrvEn = kSpecLedDrvEn, kLedIndEn = kSpecLedIndEn;
const uint8_t kBankOneShot = 3;
const uint8_t kAbortCmd = 0xFE;           // abort_req_ value of a commanded abort
// begin(): passes of (poll + 5 ms) that cover identification plus one soft reset with its
// settling time and boot wait (kSpecResetWaitMs + kSpecBootWaitMs), plus 200 passes of work.
const uint16_t kBeginMaxPolls = (uint16_t)((kSpecResetWaitMs + kSpecBootWaitMs) / 5u + 200u);
const float kCalSanity = 1.0e7f;          // |uW/cm2| above this is a corrupted read

// Elapsed ms from t to now; 0 when t is later than now (time stamps taken with millis()
// inside a pass may be newer than the pass's `now`).
inline uint32_t since(uint32_t now, uint32_t t) {
  const int32_t d = (int32_t)(now - t);
  return d > 0 ? (uint32_t)d : 0u;
}

// Lock-in frame order off on | on off | off on | ... ("ABBA"): cancels a linear drift
// (LED warm-up, die temperature) between the two halves of the sequence.
inline bool abba_on(uint8_t j) { return ((j >> 1) & 1u) ? (j & 1u) == 0 : (j & 1u) == 1; }

inline uint8_t u8min(uint8_t a, uint8_t b) { return a < b ? a : b; }

// Two's-complement octet (temperature register, Figure 31) without an
// implementation-defined narrowing conversion.
inline int8_t to_i8(uint8_t v) { return v < 128u ? (int8_t)v : (int8_t)((int16_t)v - 256); }

}  // namespace

// =============================================================================
// Public interface
// =============================================================================
bool As7265x::begin(I2cBus& bus, int int_pin, I2cBus* alt_bus) {
  bus_ = main_ = &bus;
  alt_ = alt_bus == &bus ? nullptr : alt_bus;
  int_pin_ = int_pin;
  info_ = Info();
  info_.int_pin_used = int_pin >= 0;
  if (int_pin_ >= 0) pinMode(int_pin_, INPUT_PULLUP);   // INT is active LOW
  n_ops_ = op_i_ = sub_ = 0;
  phase_ = PH_NONE;
  cycle_active_ = burst_active_ = result_ready_ = result_held_ = false;
  diag_pending_ = diag_line_ready_ = false;
  lamps_lit_ = false;
  ring_n_ = 0;
  abort_req_ = ERR_NONE;
  resync_ = resync_srst_ = false;
  lamp_armed_ = false;
  cfg_ = SpecConfig();
  // The AS72651 answers its address only once its firmware runs (loaded from the board's
  // EEPROM at power on): poll for the ACK like SparkFun isConnected() before declaring it
  // absent. Bounded by time (kSpecBootWaitMs plus the last probe), not by a probe count, so
  // a slow probe on an empty bus cannot stretch it.
  const uint32_t t0 = millis();
  bool present = probe_any();
  while (!present && millis() - t0 < kSpecBootWaitMs) {
    delay(kSpecBootProbeMs);
    present = probe_any();
  }
  if (!present) {
    state_ = ST_ABSENT;
    retry_at_ = millis() + retry_ms_;
    return false;
  }
  reset_tried_ = false;
  // A lamp may have been left on (e.g. an MCU reset during an exposure): unknown until the
  // configuration's LED_CONFIG read-back shows every LED_DRV disabled.
  lamps_lit_ = true;
  start_init();
  // Boot only: drive identification/configuration (and at most one soft-reset retry)
  // to completion. Bounded by kBeginMaxPolls.
  for (uint16_t i = 0; i < kBeginMaxPolls; ++i) {
    if (state_ != ST_INIT && state_ != ST_RESET) break;
    poll(millis());
    delay(5);
  }
  return ok();
}

As7265x::State As7265x::poll(uint32_t now_ms) {
  if (!bus_) return state_;
  uint32_t now = now_ms;
  // Lamp watchdog: a lit exposure running (since its trigger) longer than conversion +
  // DATA_RDY margin + kSpecLampMarginMs (e.g. poll() starved by the caller) is ended at the
  // next safe point, lamps off. A lamp-off list already running is the lamp-off action
  // itself and is never interrupted.
  if (lamps_lit_ && lamp_armed_ && cycle_active_ && abort_req_ == ERR_NONE &&
      phase_ != PH_LAMPS_OFF && since(now, lamp_on_ms_) > lamp_limit_ms_)
    abort_req_ = ERR_LAMP_WATCHDOG;
  const uint32_t t0 = micros();
  for (uint8_t i = 0; i < kSpecMaxStepsPerPoll; ++i) {   // each step <= 2 I2C transactions
    // Lamp-off lists keep at least the default budget whatever the caller set.
    const bool lamp_off = phase_ == PH_LAMPS_OFF || phase_ == PH_SAFE_OFF;
    const uint32_t budget = lamp_off && budget_us_ < kSpecPollBudgetUs ? kSpecPollBudgetUs : budget_us_;
    if ((uint32_t)(micros() - t0) >= budget) break;
    if (!advance(now)) break;
    now = millis();
  }
  return state_;
}

bool As7265x::start_cycle(const SpecConfig& cfg, uint32_t now_ms) {
  if (!spec_config_valid(cfg) || state_ != ST_IDLE || phase_ != PH_NONE || n_ops_) return false;
  cfg_ = cfg;
  result_ready_ = result_held_ = false;
  cycle_dev_ = info_.dev_mask;
  // Lamp bit d is the LED_DRV of device d: a lamp on an absent slave cannot be lit and is
  // neither used nor reported (re-masked after this cycle's DEV_SEL read).
  lamps_eff_ = (uint8_t)(spec_effective_lamps(cfg) & cycle_dev_);
  lamps_used_ = 0;
  ind_on_ = cfg.ind > 0;
  agc_ = cfg.gain == kSpecGainAuto && cfg.mode != SPEC_BURST;
  frames_done_ = probes_ = main_j_ = main_total_ = n_bracket_ = 0;
  sat_acc_ = on_sat_ = 0;
  on_peak_ = 0;
  for (uint8_t i = 0; i < kSpecChannels; ++i) on_raw_[i] = 0xFFFF;
  lock_.reset();
  t_start_ = now_ms;
  cycle_active_ = true;
  state_ = ST_BUSY;
  if (agc_exp_.gain_code > 3) agc_exp_.gain_code = 2;
  if (agc_exp_.int_cycles == 0) agc_exp_.int_cycles = cfg.int_cycles;

  if (cfg.mode == SPEC_BURST) {
    burst_exp_.gain_code = cfg.gain == kSpecGainAuto ? agc_exp_.gain_code : cfg.gain;
    burst_exp_.int_cycles = cfg.burst_cycles;
    burst_bank_ = cfg.burst_bank;
    burst_active_ = true;
    burst_n_ = burst_drop_ = burst_read_max_ = 0;
    burst_sat_ = 0;
    burst_t0_ms_ = burst_last_ms_ = 0;
    burst_start_ = now_ms;                           // re-stamped when continuous mode starts
    memset(burst_sum_, 0, sizeof burst_sum_);
    ring_head_ = ring_n_ = 0;
    exp_ = burst_exp_;
    ops_begin(PH_BURST_SETUP);
    devsel_op_ = n_ops_;
    op_r(kVDevSel);
    if (burst_exp_.int_cycles != int_shadow_) op_w(kVInteg, burst_exp_.int_cycles);
    led_want_[0] = led_value(0, false, ind_on_);
    if (led_want_[0] != led_shadow_[0]) { op_w(kVDevSel, 0); op_w(kVLed, led_want_[0]); }
    op_w(kVConfig, config_value(burst_exp_.gain_code, burst_bank_));   // continuous BANK 0/1/2
    return true;
  }
  if (agc_) {
    exp_ = agc_exp_;
    stage_ = STAGE_PROBE;
    begin_frame(now_ms);
  } else {
    exp_.gain_code = cfg.gain;
    exp_.int_cycles = cfg.int_cycles;
    begin_main(now_ms);
  }
  return true;
}

void As7265x::abort() {
  if (cycle_active_ || burst_active_) abort_req_ = kAbortCmd;
}

uint16_t As7265x::progress_permille() const {
  if (burst_active_) {
    const uint32_t dur = (uint32_t)cfg_.n * 1000u;
    const uint32_t e = since(millis(), burst_start_);
    return (uint16_t)(e >= dur ? 1000u : e * 1000u / dur);
  }
  if (!cycle_active_) return result_ready_ ? 1000 : 0;
  uint16_t planned;
  switch (cfg_.mode) {
    case SPEC_HDR: planned = cfg_.n; break;
    case SPEC_LOCKIN: case SPEC_FLUOR: planned = (uint16_t)(2u * cfg_.n); break;
    default: planned = lamps_eff_ ? 2 : 1; break;
  }
  if (stage_ == STAGE_PROBE) planned = (uint16_t)(planned + probes_ + 1u);
  else planned = (uint16_t)(planned + probes_);
  const uint32_t p = (uint32_t)frames_done_ * 1000u / (planned ? planned : 1u);
  return (uint16_t)(p > 999u ? 999u : p);
}

uint8_t As7265x::burst_pop(SpecBurstSample* out, uint8_t max) {
  if (!out) return 0;
  uint8_t n = 0;
  while (n < max && ring_n_ > 0 && n < kSpecBurstRing) {
    out[n++] = ring_[ring_head_];
    ring_head_ = (uint8_t)((ring_head_ + 1u) % kSpecBurstRing);
    --ring_n_;
  }
  return n;
}

uint16_t As7265x::burst_rate_dhz() const {
  if (burst_n_ < 2) return 0;
  const uint32_t dt = burst_last_ms_ - burst_t0_ms_;
  if (!dt) return 0;
  const uint32_t r = (uint32_t)(burst_n_ - 1u) * 10000u / dt;   // (n - 1) / dt[s] x 10
  return (uint16_t)(r > 65535u ? 65535u : r);
}

bool As7265x::request_diag(uint8_t device, uint8_t n_sub) {
  if (device >= kSpecDevices || n_sub > 32 || diag_pending_ || state_ == ST_ABSENT) return false;
  diag_dev_ = device;
  diag_n_ = n_sub;
  diag_k_ = 0;
  diag_hdr_ = true;
  diag_pending_ = true;
  return true;
}

bool As7265x::pop_diag_line(char* text, size_t cap) {
  if (!diag_line_ready_ || !text || cap == 0) return false;
  (void)snprintf(text, cap, "%s", diag_line_);
  diag_line_ready_ = false;
  return true;
}

bool As7265x::take_recovered() {
  const bool r = recovered_;
  recovered_ = false;
  return r;
}

// =============================================================================
// Virtual-register operation engine
// =============================================================================
void As7265x::ops_begin(Phase ph) {
  phase_ = ph;
  n_ops_ = op_i_ = sub_ = 0;
  sub_polls_ = 0;
  status_errs_ = 0;
  ops_bad_ = false;
  devsel_op_ = 0xFF;
  // A failed transaction of a virtual write left the chip idle or waiting for that
  // write's data byte. Resynchronise with the one byte that is harmless in both cases
  // (spec_vreg_resync_byte, spectral.h) - never the "owed" data byte, which the chip
  // takes as a WRITE address if it is in fact idle and the byte has bit 7 set.
  if (resync_) ops_[n_ops_++] = Op{OPK_DATA, resync_vreg_, spec_vreg_resync_byte(resync_vreg_)};
  op_base_ = n_ops_;
}

void As7265x::op_r(uint8_t vreg) {
  if (n_ops_ >= kMaxOps) { ops_bad_ = true; return; }
  ops_[n_ops_++] = Op{OPK_READ, vreg, 0};
}

void As7265x::op_w(uint8_t vreg, uint8_t data) {
  if (n_ops_ >= kMaxOps || !spec_vreg_write_allowed(vreg, data)) { ops_bad_ = true; return; }
  ops_[n_ops_++] = Op{OPK_WRITE, vreg, data};
}

// A transaction of virtual write `op` failed: the chip may or may not have latched the
// byte. The next list starts with a resynchronisation byte; the registers the write may
// have changed are no longer known (the resync byte itself may land as INTEGRATION or
// LED_CONFIG data), so the integration value is rewritten and every LED_CONFIG is treated
// as possibly lit until a lamp-off list reads it back.
void As7265x::mark_unknown(const Op& op) {
  resync_ = true;
  resync_vreg_ = op.vreg;
  if (op.kind == OPK_WRITE && op.vreg == kVConfig && (op.data & kCfgSrst)) resync_srst_ = true;
  int_shadow_ = 0;
  for (uint8_t d = 0; d < kSpecDevices; ++d) led_shadow_[d] = kLedUnknown;
}

// SparkFun-proven transaction shape for this chip: register pointer write with STOP,
// then a separate read (no repeated start).
bool As7265x::reg_read(uint8_t reg, uint8_t& v) {
  return bus_->write_raw(kAddr, &reg, 1) && bus_->read_raw(kAddr, &v, 1);
}

bool As7265x::reg_write(uint8_t reg, uint8_t v) { return bus_->write8(kAddr, reg, v); }

As7265x::Step As7265x::handshake_wait() {
  if (sub_polls_ == 0) sub_t0_ = millis();
  ++sub_polls_;
  const bool late = since(millis(), sub_t0_) > kSpecVregTimeoutMs &&
                    sub_polls_ >= kSpecMinStatusPolls;
  if (late || sub_polls_ > kSpecMaxStatusPolls) {
    step_err_ = ERR_HANDSHAKE;
    return STEP_FAIL;
  }
  return STEP_CONT;
}

// A failed STATUS read (or a failed drain of a stale READ byte) leaves the chip's
// protocol state untouched, so it is retried - bounded by kSpecStatusRetries in a row
// and by the poll limits of handshake_wait() - instead of failing the whole cycle. The
// other transactions (virtual address, data byte, READ of a requested byte) are not
// idempotent and fail the operation at once.
As7265x::Step As7265x::bus_glitch() {
  if (++status_errs_ > kSpecStatusRetries) {
    status_errs_ = 0;
    step_err_ = ERR_I2C;
    return STEP_FAIL;
  }
  return handshake_wait();
}

// One handshake step of ops_[op_i_] (datasheet "I2C Virtual Register Byte Write/Read"):
//   0 poll STATUS until TX_VALID = 0 (a stale READ byte is drained first)
//   1 write the virtual address to WRITE (bit 7 set = write)
//   2 poll STATUS: read -> RX_VALID = 1, write -> TX_VALID = 0
//   3 read READ / write the data byte to WRITE
//   4 (resync byte only) poll STATUS until TX_VALID = 0, then kSpecResyncPolls idle polls,
//     draining the answer if the chip took the byte as a read request
// A failure in steps 1..4 of a write leaves the chip's protocol state unknown: advance()
// then calls mark_unknown().
As7265x::Step As7265x::step_op() {
  const Op& op = ops_[op_i_];
  uint8_t st = 0;
  switch (sub_) {
    case 0:
      if (!reg_read(kRegStatus, st)) return bus_glitch();
      if (st & kRxValid) {
        uint8_t junk = 0;                            // a stale byte: losing it is harmless
        if (!reg_read(kRegRead, junk)) return bus_glitch();
        status_errs_ = 0;
        return handshake_wait();
      }
      status_errs_ = 0;
      if (st & kTxValid) return handshake_wait();
      sub_ = op.kind == OPK_DATA ? 3 : 1;
      sub_polls_ = 0;
      return STEP_CONT;
    case 1: {
      const uint8_t a = op.kind == OPK_READ ? op.vreg : (uint8_t)(op.vreg | 0x80);
      if (!reg_write(kRegWrite, a)) { step_err_ = ERR_I2C; return STEP_FAIL; }
      sub_ = 2;
      sub_polls_ = 0;
      return STEP_CONT;
    }
    case 2: {
      if (!reg_read(kRegStatus, st)) return bus_glitch();
      status_errs_ = 0;
      const bool ready = op.kind == OPK_READ ? (st & kRxValid) != 0 : (st & kTxValid) == 0;
      if (!ready) return handshake_wait();
      sub_ = 3;
      sub_polls_ = 0;
      return STEP_CONT;
    }
    case 3:
      if (op.kind == OPK_READ) {
        if (!reg_read(kRegRead, rd_[op_i_])) { step_err_ = ERR_I2C; return STEP_FAIL; }
        return op_complete();
      }
      // Backstop: a lone data byte (no address phase of its own) must never have bit 7 set,
      // or a chip that is in fact idle takes it as a WRITE address (datasheet "I2C Virtual
      // Register Write Access"). Only spec_vreg_resync_byte() builds these; a byte breaking
      // the rule is refused, not sent.
      if (op.kind == OPK_DATA && (op.data & 0x80)) { step_err_ = ERR_INTERNAL; return STEP_FAIL; }
      if (!reg_write(kRegWrite, op.data)) { step_err_ = ERR_I2C; return STEP_FAIL; }
      if (op.kind == OPK_DATA) {
        sub_ = 4;
        sub_polls_ = 0;
        settle_ = 0;
        return STEP_CONT;
      }
      return op_complete();
    default: {                                       // 4: settle after a resync byte
      if (!reg_read(kRegStatus, st)) return bus_glitch();
      status_errs_ = 0;
      if (st & kRxValid) {                           // taken as a read request: drain it
        uint8_t junk = 0;
        if (!reg_read(kRegRead, junk)) return bus_glitch();
      } else if (st & kTxValid) {
        return handshake_wait();                     // not consumed yet
      } else if (++settle_ < kSpecResyncPolls) {
        return STEP_CONT;
      }
      resync_ = false;                               // the chip is idle again
      return op_complete();
    }
  }
}

As7265x::Step As7265x::op_complete() {
  ++op_i_;
  sub_ = 0;
  sub_polls_ = 0;
  return STEP_CONT;
}

// One unit of work; false = nothing more can be done in this pass (waiting or idle).
bool As7265x::advance(uint32_t now) {
  if (ops_bad_) {                                    // builder refused an operation: never sent
    ops_bad_ = false;
    n_ops_ = op_i_ = sub_ = 0;
    on_failure(now, ERR_INTERNAL);
    return true;
  }
  // An abort belongs to the cycle it was raised in; a cycle that already ended (e.g. by a
  // failure, whose recovery operations are now in flight) makes it void.
  if (abort_req_ != ERR_NONE && !cycle_active_ && !burst_active_) abort_req_ = ERR_NONE;
  if (op_i_ < n_ops_) {
    // An abort drops the list only between complete handshakes, and never a lamp-off list
    // (it is the lamp-off action; the abort takes effect once it has completed).
    if (abort_req_ != ERR_NONE && sub_ == 0 && phase_ != PH_LAMPS_OFF) {
      n_ops_ = op_i_ = 0;
    } else {
      if (step_op() == STEP_FAIL) {
        const Op& op = ops_[op_i_];
        if (op.kind != OPK_READ && sub_ >= 1) mark_unknown(op);   // byte may have been latched
        n_ops_ = op_i_ = sub_ = 0;
        on_failure(now, step_err_);
        return true;
      }
      if (op_i_ >= n_ops_) {
        n_ops_ = op_i_ = 0;
        phase_done(now);
      }
      return true;
    }
  }
  if (abort_req_ != ERR_NONE) {
    const uint8_t why = abort_req_;
    abort_req_ = ERR_NONE;
    if (why != kAbortCmd) { on_failure(now, why); return true; }
    cycle_active_ = burst_active_ = false;
    result_ready_ = false;
    start_safe_off();
    return true;
  }
  return phase_tick(now);
}

// Phases without operations in flight: time-based waits and idle housekeeping.
bool As7265x::phase_tick(uint32_t now) {
  switch (phase_) {
    case PH_NONE:
      if (state_ == ST_ABSENT || state_ == ST_FAULT) {
        if ((int32_t)(now - retry_at_) < 0) return false;
        if (state_ == ST_FAULT) return fault_retry(now);
        retry_at_ = now + retry_ms_;
        retry_ms_ = retry_ms_ * 2u > kSpecRetryMaxMs ? kSpecRetryMaxMs : retry_ms_ * 2u;
        if (!probe_any()) return false;                // still absent (on either bus)
        reset_tried_ = false;
        lamps_lit_ = true;                             // never commanded: unknown until verified
        start_init();
        return true;
      }
      return state_ == ST_IDLE && build_diag();
    case PH_GATE:
      if (!exposure_gate_open_ || (frame_lamps_ && !lamp_gate_open_)) return false;
      build_setup(now);
      return true;
    case PH_WAIT: {
      const uint32_t t = since(now, t_trig_);
      // Wait out the conversion plus 1/32: covers the RC oscillator's 15.7..16.3 MHz
      // (datasheet "Electrical Characteristics", +/-1.9 %).
      if (t < conv_ms_ + conv_ms_ / 32u + 1u) return false;
      if (int_pin_ >= 0 && !info_.int_pin_fault) {
        if (digitalRead(int_pin_) == LOW) { after_drdy(now); return true; }
        if (t <= drdy_deadline_) return false;
        // INT did not assert in time: one register read tells a dead INT line from a late chip.
      } else if (since(now, last_drdy_poll_) < kSpecDrdyPollMs) {
        return false;
      }
      last_drdy_poll_ = now;
      ops_begin(PH_DRDY);
      op_r(kVConfig);
      return true;
    }
    case PH_BURST_WAIT: {
      if (since(now, burst_start_) >= (uint32_t)cfg_.n * 1000u) {
        ops_begin(PH_BURST_STOP);
        op_w(kVConfig, config_value(burst_exp_.gain_code, kBankOneShot));   // ends continuous mode
        for (uint8_t d = 0; d < kSpecDevices; ++d) {   // fresh die temperatures for the result
          if (!(cycle_dev_ & (1u << d))) continue;
          op_w(kVDevSel, d);
          op_r(kVTemp);
        }
        return true;
      }
      // Continuous BANK modes: the chip keeps converting and reading the data releases
      // INT / DATA_RDY (datasheet "Interrupt Operation"; the SparkFun max-rate example
      // relies on the same). A new conversion cannot finish sooner than one conversion
      // time after the previous DATA_RDY, so sampling never runs faster than that even
      // if the flag were not cleared (VERIFY with T-SC-08: achieved rate <= 1 / conversion).
      // One sample read is up to 3 DEV_SEL writes + 36 single-octet reads (BANK 2), each a
      // four-step handshake of at least 6 (write) or 7 (read) I2C transactions: at 100 kHz
      // that is tens of ms, i.e. several conversions at burst_cycles = 1 (12 ms in BANK 2,
      // spec_conversion_ms). Samples are therefore not contiguous and the channels of one
      // sample come from successive conversions (only the two octets of one value are
      // shadow-protected); dt_ms stamps DATA_RDY and burst_read_max_ms reports the longest
      // read (spectral.h SpecBurstSample).
      const uint32_t t = since(now, burst_ref_ms_);
      if (burst_n_ == 0 && t < conv_ms_ + conv_ms_ / 32u + 1u) return false;
      if (burst_n_ > 0 && since(now, burst_sample_ms_) < conv_ms_) return false;
      if (int_pin_ >= 0 && !info_.int_pin_fault) {
        if (digitalRead(int_pin_) == LOW) {
          burst_sample_ms_ = millis();
          build_burst_read();
          return true;
        }
        if (t <= drdy_deadline_) return false;
      } else if (since(now, last_drdy_poll_) < kSpecDrdyPollMs) {
        return false;
      }
      last_drdy_poll_ = now;
      ops_begin(PH_BURST_DRDY);
      op_r(kVConfig);
      return true;
    }
    case PH_SRST_WAIT: {
      const uint32_t t = since(now, srst_t_);
      if (t < kSpecResetWaitMs) return false;
      // Boot wait: identify only once the rebooted chip ACKs its address (polled like
      // SparkFun isConnected(), uncounted probes), for at most kSpecBootWaitMs more; then
      // identification runs anyway and its failure takes the normal recovery path.
      if (t < kSpecResetWaitMs + kSpecBootWaitMs) {
        if (since(now, probe_t_) < kSpecBootProbeMs) return false;
        probe_t_ = now;
        if (!bus_->probe(kAddr)) return false;
      }
      start_init();
      return true;
    }
    default:
      // An operation phase without operations cannot occur by design; if it ever does,
      // recover through the normal failure path instead of idling in it forever.
      on_failure(now, ERR_INTERNAL);
      return true;
  }
}

// The operation list of phase_ completed; decode rd_[] and move on.
void As7265x::phase_done(uint32_t now) {
  const uint8_t* b = &rd_[op_base_];
  switch (phase_) {
    case PH_INIT_ID:
      info_.hw_type = b[0];
      info_.hw_version = b[1];
      info_.fw_major = b[4];
      info_.fw_patch = b[7];
      info_.fw_build = b[10];
      decode_devsel(b[11]);
      ops_begin(PH_INIT_CFG);
      for (uint8_t d = 0; d < kSpecDevices; ++d) {    // all three: no lamp may stay lit
        led_want_[d] = led_value(d, false, false);
        op_w(kVDevSel, d);
        op_w(kVLed, led_want_[d]);
        op_r(kVLed);                                   // read back: LED_DRV must be off
      }
      op_w(kVInteg, cfg_.int_cycles);
      op_r(kVInteg);
      op_w(kVConfig, config_value(agc_exp_.gain_code, kBankOneShot));
      for (uint8_t d = 0; d < kSpecDevices; ++d) {
        if (!(info_.dev_mask & (1u << d))) continue;
        op_w(kVDevSel, d);
        op_r(kVTemp);
      }
      return;
    case PH_INIT_CFG: {
      // b: 3 x {DEV_SEL w, LED w, LED r}, INTEG w, INTEG r, CONFIG w, n x {DEV_SEL w, TEMP r}
      const bool lit = led_readback_lit(b, 2, 3, 7);
      uint8_t k = (uint8_t)(3u * kSpecDevices);
      const uint8_t integ = b[k + 1u];                 // read-back of the integration register
      k = (uint8_t)(k + 3u);
      for (uint8_t d = 0; d < kSpecDevices; ++d) {
        if (!(info_.dev_mask & (1u << d))) continue;
        info_.temp[d] = to_i8(b[k + 1u]);
        k = (uint8_t)(k + 2u);
      }
      if (lit) lamps_lit_ = true;                      // a lamp is known to be on
      if (lit || integ != cfg_.int_cycles) { on_failure(now, ERR_VERIFY); return; }
      for (uint8_t d = 0; d < kSpecDevices; ++d) led_shadow_[d] = led_want_[d];
      int_shadow_ = integ;
      lamps_lit_ = lamp_armed_ = false;
      consec_fail_ = 0;
      srst_fails_ = 0;
      reset_tried_ = false;
      fault_init_next_ = true;
      retry_ms_ = kSpecRetryMs;
      if (in_recovery_) {
        in_recovery_ = false;
        recovered_ = true;
        if (resets_ < 65535) ++resets_;
      }
      state_ = ST_IDLE;
      phase_ = PH_NONE;
      return;
    }
    case PH_SETUP:
      if (devsel_op_ != 0xFF) {
        decode_devsel(rd_[devsel_op_]);
        cycle_dev_ = info_.dev_mask;
        // A slave reported missing now: its lamp is not used (nor reported) from here on.
        lamps_eff_ &= cycle_dev_;
        frame_lamps_ &= cycle_dev_;
        cur_.lamps &= cycle_dev_;
      }
      int_shadow_ = exp_.int_cycles;
      for (uint8_t d = 0; d < kSpecDevices; ++d) led_shadow_[d] = led_want_[d];
      t_trig_ = millis();
      if (lamps_lit_) { lamp_armed_ = true; lamp_on_ms_ = t_trig_; }   // lamp watchdog runs
      conv_ms_ = spec_conversion_ms(exp_.int_cycles, kBankOneShot);
      drdy_deadline_ = conv_ms_ + conv_ms_ / 32u + kSpecDrdyMarginMs;
      last_drdy_poll_ = t_trig_;
      phase_ = PH_WAIT;
      return;
    case PH_DRDY:
    case PH_BURST_DRDY: {
      const bool burst = phase_ == PH_BURST_DRDY;
      const uint32_t t = since(millis(), burst ? burst_ref_ms_ : t_trig_);
      if (b[0] & kCfgDataRdy) {
        if (int_pin_ >= 0 && t > drdy_deadline_) info_.int_pin_fault = true;   // INT line dead
        if (burst) { burst_sample_ms_ = millis(); build_burst_read(); }
        else after_drdy(now);
        return;
      }
      if (t > drdy_deadline_) { on_failure(now, ERR_DRDY); return; }
      phase_ = burst ? PH_BURST_WAIT : PH_WAIT;
      return;
    }
    case PH_LAMPS_OFF:
      // b: per device in off_list_ {DEV_SEL w, LED w, LED r}
      if (led_readback_lit(b, 2, 3, off_list_)) { lamps_lit_ = true; on_failure(now, ERR_VERIFY); return; }
      for (uint8_t d = 0; d < kSpecDevices; ++d) led_shadow_[d] = led_want_[d];
      lamps_lit_ = shadow_lit(0) || shadow_lit(1) || shadow_lit(2);
      if (!lamps_lit_) lamp_armed_ = false;
      start_read(0);
      return;
    case PH_READ: {
      const uint8_t d = read_dev_;
      const uint8_t* p = b + 1;                        // b[0] is the DEV_SEL write
      for (uint8_t k = 0; k < kSpecSlots; ++k) {
        const uint8_t ch = kSpecSlotChannel[d][k];
        cur_.raw[ch] = (uint16_t)(((uint16_t)p[2 * k] << 8) | p[2 * k + 1]);
        if (!need_cal_) { cur_.valid |= 1u << ch; continue; }
        // Calibrated float, MSB at the lowest address: the datasheet states "all multi-byte
        // entities are Big Endian" (its Figure 39-44 byte captions disagree); the SparkFun
        // library reads it the same way.
        const uint8_t* c = p + 12 + 4 * k;
        const uint32_t u = ((uint32_t)c[0] << 24) | ((uint32_t)c[1] << 16) |
                           ((uint32_t)c[2] << 8) | c[3];
        float f;
        memcpy(&f, &u, sizeof f);                      // IEEE-754 binary32 (datasheet EQ1)
        if (isfinite(f) && fabsf(f) < kCalSanity) { cur_.cal[ch] = f; cur_.valid |= 1u << ch; }
      }
      info_.temp[d] = to_i8(p[need_cal_ ? 36 : 12]);
      start_read((uint8_t)(d + 1u));
      return;
    }
    case PH_BURST_SETUP:
      if (devsel_op_ != 0xFF) { decode_devsel(rd_[devsel_op_]); cycle_dev_ = info_.dev_mask; }
      int_shadow_ = burst_exp_.int_cycles;
      led_shadow_[0] = led_want_[0];
      t_trig_ = burst_start_ = burst_ref_ms_ = last_drdy_poll_ = millis();
      conv_ms_ = spec_conversion_ms(burst_exp_.int_cycles, burst_bank_);
      drdy_deadline_ = conv_ms_ + conv_ms_ / 32u + kSpecDrdyMarginMs;
      phase_ = PH_BURST_WAIT;
      return;
    case PH_BURST_READ:
      burst_sample_done();
      return;
    case PH_BURST_STOP: {
      uint8_t k = 1;                                   // b[0] is the CONFIG write
      for (uint8_t d = 0; d < kSpecDevices; ++d) {     // n x {DEV_SEL w, TEMP r}
        if (!(cycle_dev_ & (1u << d))) continue;
        info_.temp[d] = to_i8(b[k + 1u]);
        k = (uint8_t)(k + 2u);
      }
      finish_burst(now);
      return;
    }
    case PH_END:
      led_shadow_[0] = led_want_[0];
      result_held_ = false;
      result_ready_ = true;
      state_ = ST_IDLE;
      phase_ = PH_NONE;
      return;
    case PH_DIAG: {
      const uint8_t* p = b + 1;                        // b[0] is the DEV_SEL write
      if (diag_hdr_) {
        // p: HW H, HW L, DEV_SEL, LED_CONFIG, CONFIG, INTEGRATION, temperature (build_diag)
        (void)snprintf(diag_line_, sizeof diag_line_,
                       "AS7265X dev%u HW %02X%02X FW %u.%u.%u SEL %02X LED %02X CFG %02X IT %u T %d C",
                       (unsigned)diag_dev_, (unsigned)p[0], (unsigned)p[1], (unsigned)info_.fw_major,
                       (unsigned)info_.fw_patch, (unsigned)info_.fw_build, (unsigned)p[2],
                       (unsigned)p[3], (unsigned)p[4], (unsigned)p[5], (int)to_i8(p[6]));
        diag_hdr_ = false;
      } else {
        // COEF words are printed as the octets of COEF_DATA 0x50..0x53 in address order;
        // Figure 49 places COEF_DATA_3 (0x53) in bits 31:24, contrary to the "big endian"
        // rule of Figure 26, so the ground must not assume a byte significance (VERIFY).
        int n = snprintf(diag_line_, sizeof diag_line_, "AS7265X dev%u COEF %02u-%02u:",
                         (unsigned)diag_dev_, (unsigned)diag_k_,
                         (unsigned)(diag_k_ + diag_chunk_ - 1u));
        for (uint8_t i = 0; i < diag_chunk_ && n > 0 && (size_t)n < sizeof diag_line_; ++i) {
          const uint8_t* w = p + 5 * i + 1;            // skip the COEF_READ write of word i
          n += snprintf(diag_line_ + n, sizeof diag_line_ - (size_t)n, " %02X%02X%02X%02X",
                        (unsigned)w[0], (unsigned)w[1], (unsigned)w[2], (unsigned)w[3]);
        }
        diag_k_ = (uint8_t)(diag_k_ + diag_chunk_);
      }
      diag_line_ready_ = true;
      if (!diag_hdr_ && diag_k_ >= diag_n_) diag_pending_ = false;
      state_ = ST_IDLE;
      phase_ = PH_NONE;
      return;
    }
    case PH_SAFE_OFF:
      // b: CONFIG w, 3 x {DEV_SEL w, LED w, LED r}. ACKs alone do not prove the lamps off
      // (a desynchronised byte stream is ACKed too): a lit read-back escalates to a soft reset.
      if (led_readback_lit(b, 3, 3, 7)) { lamps_lit_ = true; on_failure(now, ERR_VERIFY); return; }
      for (uint8_t d = 0; d < kSpecDevices; ++d) led_shadow_[d] = led_want_[d];
      lamps_lit_ = lamp_armed_ = false;
      ind_on_ = false;
      state_ = ST_IDLE;
      phase_ = PH_NONE;
      return;
    case PH_SRST:
      // A soft reset returns LED_CONFIG to its defaults (both drivers off, Figure 32).
      for (uint8_t d = 0; d < kSpecDevices; ++d) led_shadow_[d] = 0xFF;
      lamps_lit_ = lamp_armed_ = false;
      srst_t_ = probe_t_ = millis();
      phase_ = PH_SRST_WAIT;
      return;
    default:
      phase_ = PH_NONE;
      return;
  }
}

// Any failed handshake, I2C error, DATA_RDY timeout or verification error.
void As7265x::on_failure(uint32_t now, uint8_t err) {
  if (errors_ < 0xFFFFFFFFu) ++errors_;
  last_err_ = err;
  if (consec_fail_ < 255) ++consec_fail_;
  n_ops_ = op_i_ = sub_ = 0;
  // The failed list may have written INTEGRATION (e.g. a BURST setup) before it ended:
  // the shadow is no longer trusted and the next frame rewrites the register.
  int_shadow_ = 0;
  const Phase failed = phase_;
  if (result_held_) {                                // only the indicator-off write failed:
    result_held_ = false;                            // the measurement itself is complete
    result_ready_ = true;
  }
  if (cycle_active_ || burst_active_) {              // the cycle is reported, invalid
    spec_result_clear(result_);
    result_.mode = cfg_.mode;
    result_.t_start_ms = t_start_;
    result_.t_end_ms = millis();
    result_ready_ = true;
    cycle_active_ = burst_active_ = false;
  }
  if (failed == PH_DIAG) { diag_pending_ = false; diag_line_ready_ = false; }
  switch (failed) {
    case PH_INIT_ID:
    case PH_INIT_CFG:
      if (!reset_tried_) start_srst(now); else enter_fault(now);
      return;
    case PH_SRST:                                    // wait and re-initialise; back off if it persists
      if (++srst_fails_ >= kSpecMaxConsecFails) { srst_fails_ = 0; enter_fault(now); return; }
      srst_t_ = probe_t_ = millis();
      phase_ = PH_SRST_WAIT;
      state_ = ST_RESET;
      return;
    case PH_SAFE_OFF:
      start_srst(now);
      return;
    default:
      break;
  }
  if (consec_fail_ >= kSpecMaxConsecFails) start_srst(now);
  else start_safe_off();
}

// =============================================================================
// Initialisation and recovery
// =============================================================================
void As7265x::start_init() {
  if (resync_srst_) {
    // A soft reset whose delivery is unknown: issue it again in full (resynchronisation
    // byte, address, data) and let the chip settle, rather than identify a chip that may
    // be about to reset - or resend a lone 0x80, which an idle chip takes as an address.
    start_srst(millis());
    return;
  }
  state_ = ST_INIT;
  for (uint8_t d = 0; d < kSpecDevices; ++d)          // unknown: rewrite (keep "possibly lit")
    if (led_shadow_[d] != kLedUnknown) led_shadow_[d] = 0xFF;
  int_shadow_ = 0;
  ops_begin(PH_INIT_ID);
  op_r(kVHwH);
  op_r(kVHwL);
  // FW version (Figure 28): selector 1..3 = MAJOR, PATCH, BUILD written to 0x02 and 0x03,
  // value read from 0x03 - the SparkFun library's reading of an ambiguous table. VERIFY.
  for (uint8_t k = 1; k <= 3; ++k) {
    op_w(kVFwH, k);
    op_w(kVFwL, k);
    op_r(kVFwL);
  }
  op_r(kVDevSel);
}

void As7265x::start_srst(uint32_t now) {
  (void)now;
  reset_tried_ = true;
  in_recovery_ = true;
  resync_srst_ = false;                              // issued now (set again if it fails)
  state_ = ST_RESET;
  ops_begin(PH_SRST);
  op_w(kVConfig, kCfgSrst);
}

void As7265x::start_safe_off() {
  state_ = ST_BUSY;
  int_shadow_ = 0;                                   // an aborted list may have written it
  ops_begin(PH_SAFE_OFF);
  // CONFIG first: it ends a continuous burst, and its address byte 0x84 is the least
  // harmful first byte should the chip ever be waiting for data unexpectedly (as CONFIG
  // data it is a soft reset, as LED_CONFIG data it lights nothing) - unlike 0xCF (DEV_SEL
  // write), which as CONFIG data would be SRST | FRST and as LED_CONFIG data a lit lamp.
  op_w(kVConfig, config_value(exp_.gain_code, kBankOneShot));
  for (uint8_t d = 0; d < kSpecDevices; ++d) {       // all three, whatever DEV_SEL reported
    led_want_[d] = led_value(d, false, false);
    op_w(kVDevSel, d);
    op_w(kVLed, led_want_[d]);
    op_r(kVLed);                                     // read back (phase_done PH_SAFE_OFF)
  }
}

void As7265x::enter_fault(uint32_t now) {
  (void)now;
  state_ = ST_FAULT;
  phase_ = PH_NONE;
  if (lamps_lit_) {
    // A lamp may still be lit: lamp safety outranks bus-noise backoff. Retry (probe, then
    // soft reset or re-initialisation, both of which switch every LED driver off) once per
    // settling time.
    retry_at_ = millis() + kSpecResetWaitMs;
    return;
  }
  retry_at_ = millis() + retry_ms_;
  retry_ms_ = retry_ms_ * 2u > kSpecRetryMaxMs ? kSpecRetryMaxMs : retry_ms_ * 2u;
}

// One FAULT retry. A chip that does not ACK its address cannot receive a soft reset, so it
// is only probed (uncounted: no bus-error noise while it is unpowered or disconnected).
// A chip that answers is alternately re-identified without a reset (a healthy chip that
// merely booted slowly) and soft-reset first.
bool As7265x::fault_retry(uint32_t now) {
  if (!probe_any()) {
    enter_fault(now);                                // reschedule (lamp-safety cadence or backoff)
    return false;
  }
  const bool init_only = fault_init_next_;
  fault_init_next_ = !fault_init_next_;
  if (!init_only) { start_srst(now); return true; }
  reset_tried_ = true;                               // a failure returns to FAULT directly
  start_init();                                      // writes every LED_CONFIG off as well
  return true;
}

// Uncounted probe of 0x49 on the bus in use, then on the other bus (if any): the board
// may be on the header pins or on the Qwiic connector, and may be moved between them while
// it is absent. The driver switches to the bus that answers.
bool As7265x::probe_any() {
  if (bus_->probe(kAddr)) return true;
  if (alt_ == nullptr) return false;
  I2cBus* other = bus_ == alt_ ? main_ : alt_;
  if (!other->probe(kAddr)) return false;
  bus_ = other;
  return true;
}

// True if a LED_CONFIG read-back in b (the k-th device listed in dev_list, ascending, at
// b[first + k x stride]) still has LED_DRV enabled on a device DEV_SEL reports present.
// An absent slave's read-back is not meaningful and is ignored.
bool As7265x::led_readback_lit(const uint8_t* b, uint8_t first, uint8_t stride,
                               uint8_t dev_list) const {
  uint8_t idx = first;
  for (uint8_t d = 0; d < kSpecDevices; ++d) {
    if (!(dev_list & (1u << d))) continue;
    if ((info_.dev_mask & (1u << d)) && (b[idx] & kLedDrvEn)) return true;
    idx = (uint8_t)(idx + stride);
  }
  return false;
}

void As7265x::decode_devsel(uint8_t v) {
  info_.dev_sel = v;
  // DEV_SEL b4 = first slave (AS72652), b5 = second slave (AS72653). The datasheet table
  // (Figure 48) prints "bit 5" for both; the SparkFun library tests b5:4 together. VERIFY.
  info_.dev_mask = (uint8_t)(1u | ((v & 0x10) ? 2u : 0u) | ((v & 0x20) ? 4u : 0u));
  for (uint8_t d = 1; d < kSpecDevices; ++d)
    if (!(info_.dev_mask & (1u << d))) info_.temp[d] = -128;
}

// Shadow says LED_DRV of device d may be enabled (0xFF = unknown after a reset: drivers
// off; kLedUnknown = unknown after a failed write: possibly lit).
bool As7265x::shadow_lit(uint8_t d) const {
  if (led_shadow_[d] == kLedUnknown) return true;
  return led_shadow_[d] != 0xFF && (led_shadow_[d] & kLedDrvEn) != 0;
}

uint8_t As7265x::led_value(uint8_t dev, bool lamp_on, bool ind_on) const {
  // LED_DRV current code, capped at the lamp's rating (kSpecLedMaxCode) whatever cfg_ holds.
  uint8_t v = (uint8_t)(spec_led_code(cfg_.led_cur, dev) << 4);
  if (lamp_on) v |= kLedDrvEn;
  if (dev == 0 && cfg_.ind > 0) {                    // indicator LED sits on the AS72651
    v |= (uint8_t)((cfg_.ind - 1u) << 1);            // 1/2/4/8 mA = codes 0..3
    if (ind_on) v |= kLedIndEn;
  }
  return v;
}

// CONFIG.INT is always set, as the SparkFun library does on this board (begin() ends with
// enableInterrupt(), SparkFun_AS7265X.cpp:66; Example3_Settings.ino marks it "Default"):
// the datasheet describes DATA_RDY only "if interrupts are enabled" ("Interrupt
// Operation"), so DATA_RDY polling uses the documented configuration whether or not the
// INT line is wired (it is read only when int_pin_ >= 0).
uint8_t As7265x::config_value(uint8_t gain, uint8_t bank) const {
  return (uint8_t)(kCfgInt | ((gain & 3u) << 4) | ((bank & 3u) << 2));
}

// =============================================================================
// Acquisition cycle
// =============================================================================
bool As7265x::frame_wants_lamps() const {
  if (stage_ == STAGE_PROBE) return cfg_.mode != SPEC_HDR && lamps_eff_ != 0;
  switch (cfg_.mode) {
    case SPEC_SURVEY: return main_j_ == 0 && lamps_eff_ != 0;
    case SPEC_LOCKIN: case SPEC_FLUOR: return abba_on(main_j_);
    default: return false;
  }
}

void As7265x::begin_frame(uint32_t now) {
  if ((cfg_.mode == SPEC_LOCKIN || cfg_.mode == SPEC_FLUOR) && lamps_eff_ == 0) {
    finish_no_lamp(now);                             // every lamp sits on an absent slave
    return;
  }
  frame_lamps_ = frame_wants_lamps() ? lamps_eff_ : 0;
  // Probe frames only steer the AGC: raw counts suffice, except in SURVEY where the
  // final probe becomes the measurement.
  need_cal_ = stage_ == STAGE_MAIN || cfg_.mode == SPEC_SURVEY;
  cur_.exp = exp_;
  cur_.lamps = frame_lamps_;
  cur_.valid = 0;
  for (uint8_t i = 0; i < kSpecChannels; ++i) { cur_.raw[i] = 0xFFFF; cur_.cal[i] = NAN; }
  if (!exposure_gate_open_ || (frame_lamps_ && !lamp_gate_open_)) { phase_ = PH_GATE; return; }
  build_setup(now);
}

void As7265x::build_setup(uint32_t now) {
  (void)now;
  ops_begin(PH_SETUP);
  if (frames_done_ == 0) { devsel_op_ = n_ops_; op_r(kVDevSel); }   // slave health, once per cycle
  if (exp_.int_cycles != int_shadow_) op_w(kVInteg, exp_.int_cycles);
  bool lit = false;
  for (uint8_t d = 0; d < kSpecDevices; ++d) {
    const bool on = ((frame_lamps_ >> d) & 1u) != 0;
    led_want_[d] = led_shadow_[d];
    if (!(cycle_dev_ & (1u << d))) continue;
    led_want_[d] = led_value(d, on, ind_on_);
    if (on) { lit = true; lamps_used_ = (uint8_t)(lamps_used_ | (1u << d)); }
    if (led_want_[d] != led_shadow_[d]) { op_w(kVDevSel, d); op_w(kVLed, led_want_[d]); }
  }
  // BANK 3 write = trigger. INTEGRATION and CONFIG are written under whatever DEV_SEL the
  // LED writes left, as the SparkFun library does; the AS72651 "transparently controls
  // data collection" of the slaves (datasheet "Interrupt Operation"), so they are taken to
  // apply to all three devices - VERIFY with T-SC-08 (a gain step moves all 18 channels).
  op_w(kVConfig, config_value(exp_.gain_code, kBankOneShot));
  if (lit) {
    lamps_lit_ = true;                               // conservative: from the list on
    // The watchdog timer starts at the trigger (phase_done PH_SETUP), so that the rest of
    // this list on a slow bus (reduced poll budget, long loop) does not eat into the
    // exposure margin; every operation of the list is bounded by the handshake limits.
    lamp_armed_ = false;
    const uint32_t conv = spec_conversion_ms(exp_.int_cycles, kBankOneShot);
    lamp_limit_ms_ = conv + conv / 32u + kSpecDrdyMarginMs + kSpecLampMarginMs;
  }
}

void As7265x::after_drdy(uint32_t now) {
  // Electronic shutter: lamps off before the data are read. Driven by the LED shadows, not by
  // the device mask: a slave reported missing after its lamp was switched on still gets the
  // off command.
  bool any = false;
  for (uint8_t d = 0; d < kSpecDevices; ++d) {
    led_want_[d] = led_shadow_[d];
    if (shadow_lit(d)) any = true;
  }
  if (!any) {
    (void)now;
    start_read(0);
    return;
  }
  ops_begin(PH_LAMPS_OFF);
  off_list_ = 0;
  for (uint8_t d = 0; d < kSpecDevices; ++d) {
    if (!shadow_lit(d)) continue;
    off_list_ = (uint8_t)(off_list_ | (1u << d));
    led_want_[d] = led_value(d, false, ind_on_);
    op_w(kVDevSel, d);
    op_w(kVLed, led_want_[d]);
    op_r(kVLed);                                     // read back (phase_done PH_LAMPS_OFF)
  }
}

void As7265x::start_read(uint8_t from_dev) {
  for (uint8_t d = from_dev; d < kSpecDevices; ++d) {
    if (!(cycle_dev_ & (1u << d))) continue;
    read_dev_ = d;
    ops_begin(PH_READ);
    op_w(kVDevSel, d);
    for (uint8_t k = 0; k < 2 * kSpecSlots; ++k) op_r((uint8_t)(kVRaw + k));
    if (need_cal_)
      for (uint8_t k = 0; k < 4 * kSpecSlots; ++k) op_r((uint8_t)(kVCal + k));
    op_r(kVTemp);
    return;
  }
  frame_done(millis());
}

void As7265x::begin_main(uint32_t now) {
  stage_ = STAGE_MAIN;
  main_j_ = 0;
  center_ = exp_;
  switch (cfg_.mode) {
    case SPEC_HDR:
      n_bracket_ = spec_hdr_bracket(center_, cfg_.n, bracket_);
      exp_ = bracket_[0];
      main_total_ = n_bracket_;
      break;
    case SPEC_LOCKIN:
    case SPEC_FLUOR:
      lock_.reset();
      main_total_ = (uint8_t)(2u * cfg_.n);
      break;
    default:
      main_total_ = lamps_eff_ ? 2 : 1;
      break;
  }
  begin_frame(now);
}

void As7265x::frame_done(uint32_t now) {
  if (frames_done_ < 255) ++frames_done_;
  const uint32_t sat = spec_saturation_mask(cur_.raw, cur_.exp.int_cycles, cur_.valid);
  if (stage_ == STAGE_PROBE) {
    ++probes_;
    SpecExposure nx;
    const SpecAgcResult a =
        spec_agc_step(exp_, cfg_.int_cycles, spec_peak(cur_.raw, cur_.valid), sat != 0, nx);
    agc_exp_ = nx;
    if (a == SPEC_AGC_STEPPED && probes_ < kSpecAgcMaxProbes) {
      exp_ = nx;
      begin_frame(now);
      return;
    }
    if (cfg_.mode == SPEC_SURVEY) {                  // the last probe is the measurement
      keep_ = cur_;
      sat_acc_ |= sat;
      stage_ = STAGE_MAIN;
      main_total_ = keep_.lamps ? 2 : 1;
      main_j_ = 1;
      if (keep_.lamps) { begin_frame(now); return; } // dark frame at the same exposure
      finish_cycle(now);
      return;
    }
    if (a == SPEC_AGC_STEPPED) exp_ = nx;            // probes exhausted: use the latest guess
    begin_main(now);
    return;
  }
  switch (cfg_.mode) {
    case SPEC_HDR:
      if (main_j_ < 4) hdr_[main_j_] = cur_;
      ++main_j_;
      if (main_j_ < n_bracket_) { exp_ = bracket_[main_j_]; begin_frame(now); return; }
      finish_cycle(now);
      return;
    case SPEC_LOCKIN:
    case SPEC_FLUOR: {
      sat_acc_ |= sat;
      if (cur_.lamps) {
        memcpy(on_raw_, cur_.raw, sizeof on_raw_);
        const uint16_t pk = spec_peak(cur_.raw, cur_.valid);
        if (pk > on_peak_) on_peak_ = pk;
        on_sat_ |= sat;
      }
      if ((main_j_ & 1u) == 0) keep_ = cur_;
      else if (cur_.lamps) lock_.add(cur_, keep_);
      else lock_.add(keep_, cur_);
      ++main_j_;
      if (main_j_ < main_total_) { begin_frame(now); return; }
      finish_cycle(now);
      return;
    }
    default:                                         // SURVEY
      sat_acc_ |= sat;
      if (main_j_ == 0) {
        keep_ = cur_;
        main_j_ = 1;
        if (keep_.lamps) { begin_frame(now); return; }   // its dark frame
        main_total_ = 1;                             // lamp on an absent slave: passive
      }
      finish_cycle(now);
      return;
  }
}

void As7265x::finish_cycle(uint32_t now) {
  SpecResult& r = result_;
  spec_result_clear(r);
  // Lamps actually driven: commanded on (build_setup) on a device still present after this
  // cycle's DEV_SEL read. A lamp on an absent slave is never reported as used.
  const uint8_t used = (uint8_t)(lamps_used_ & cycle_dev_ & 7u);
  r.mode = cfg_.mode;
  r.led_cur = spec_led_cur_applied(cfg_.led_cur);
  r.lamps = used;
  r.dev_mask = cycle_dev_;
  r.gain_code = exp_.gain_code;
  r.int_cycles = exp_.int_cycles;
  uint8_t flags = (uint8_t)(SPEC_F_VALID | (used << 1) | (agc_ ? SPEC_F_AGC : 0));
  uint32_t sat = sat_acc_;
  switch (cfg_.mode) {
    case SPEC_HDR: {
      uint8_t chosen[kSpecChannels];
      spec_hdr_merge(hdr_, n_bracket_, r.spec, r.unc, r.raw, sat, chosen);
      r.gain_code = center_.gain_code;
      r.int_cycles = center_.int_cycles;
      r.n_frames = n_bracket_;
      break;
    }
    case SPEC_LOCKIN:
    case SPEC_FLUOR: {
      float model[kSpecChannels];                    // n = 1: sigma of one on - off difference
      for (uint8_t ch = 0; ch < kSpecChannels; ++ch) {
        const float a = spec_sigma_uw(keep_.cal[ch], keep_.raw[ch], exp_.gain_code, exp_.int_cycles);
        const float c = spec_sigma_uw(cur_.cal[ch], cur_.raw[ch], exp_.gain_code, exp_.int_cycles);
        model[ch] = sqrtf(a * a + c * c);
      }
      lock_.finish(model, r.spec, r.unc);
      memcpy(r.raw, on_raw_, sizeof r.raw);
      r.n_frames = lock_.count();
      if (used) flags |= SPEC_F_ACTIVE;
      else flags &= (uint8_t)~SPEC_F_VALID;          // no lamp was driven: dark minus dark
      if (agc_) {                                    // start the next cycle nearer the target
        SpecExposure nx;
        spec_agc_step(exp_, cfg_.int_cycles, on_peak_, on_sat_ != 0, nx);
        agc_exp_ = nx;
      }
      break;
    }
    default: {                                       // SURVEY: keep_ = ambient or lamp-on frame
      const bool active = keep_.lamps != 0 && used != 0;
      for (uint8_t ch = 0; ch < kSpecChannels; ++ch) {
        const uint32_t bit = 1u << ch;
        r.raw[ch] = keep_.raw[ch];
        if (!(keep_.valid & bit) || (active && !(cur_.valid & bit))) continue;
        const float s1 = spec_sigma_uw(keep_.cal[ch], keep_.raw[ch], keep_.exp.gain_code,
                                       keep_.exp.int_cycles);
        if (!active) { r.spec[ch] = keep_.cal[ch]; r.unc[ch] = s1; continue; }
        const float s0 = spec_sigma_uw(cur_.cal[ch], cur_.raw[ch], cur_.exp.gain_code,
                                       cur_.exp.int_cycles);
        r.spec[ch] = keep_.cal[ch] - cur_.cal[ch];   // lamp-on minus lamp-off
        r.unc[ch] = sqrtf(s1 * s1 + s0 * s0);
      }
      r.gain_code = keep_.exp.gain_code;
      r.int_cycles = keep_.exp.int_cycles;
      r.n_frames = 1;
      if (active) flags |= SPEC_F_ACTIVE;
      break;
    }
  }
  r.sat_mask = sat & kSpecAllChannels;
  if (r.sat_mask) flags |= SPEC_F_SAT;
  for (uint8_t ch = 0; ch < kSpecChannels; ++ch)
    if (isfinite(r.spec[ch])) r.valid_mask |= 1u << ch;
  if (!r.valid_mask) flags &= (uint8_t)~SPEC_F_VALID;
  r.flags = flags;
  for (uint8_t d = 0; d < kSpecDevices; ++d)
    r.temp[d] = (cycle_dev_ & (1u << d)) ? info_.temp[d] : (int8_t)-128;
  r.t_start_ms = t_start_;
  r.t_end_ms = millis();
  consec_fail_ = 0;
  end_cycle(now);
}

// LOCKIN / FLUOR with no lamp on a present device: no difference can be formed, so the
// cycle ends at once with an invalid result (no VALID, no ACTIVE) instead of reporting
// dark minus dark as an own-lamp spectrum. Not a chip failure: no recovery follows.
void As7265x::finish_no_lamp(uint32_t now) {
  SpecResult& r = result_;
  spec_result_clear(r);
  r.mode = cfg_.mode;
  r.led_cur = spec_led_cur_applied(cfg_.led_cur);
  r.dev_mask = cycle_dev_;
  r.gain_code = exp_.gain_code;
  r.int_cycles = exp_.int_cycles;
  r.flags = agc_ ? SPEC_F_AGC : 0;
  r.t_start_ms = t_start_;
  r.t_end_ms = millis();
  last_err_ = ERR_NO_LAMP;
  end_cycle(now);
}

// The result is published only once the chip is quiet (indicator off), so that
// result_ready() implies ST_IDLE and the next start_cycle() is accepted at once.
void As7265x::end_cycle(uint32_t now) {
  (void)now;
  cycle_active_ = false;
  if (ind_on_) {                                     // indicator off: measurement finished
    ind_on_ = false;
    result_held_ = true;
    ops_begin(PH_END);
    led_want_[0] = led_value(0, false, false);
    op_w(kVDevSel, 0);
    op_w(kVLed, led_want_[0]);
    return;
  }
  result_ready_ = true;
  state_ = ST_IDLE;
  phase_ = PH_NONE;
}

// =============================================================================
// BURST
// =============================================================================
void As7265x::build_burst_read() {
  ops_begin(PH_BURST_READ);
  for (uint8_t d = 0; d < kSpecDevices; ++d) {
    if (!(cycle_dev_ & (1u << d))) continue;
    const uint8_t slots = spec_bank_slots(burst_bank_, d);
    op_w(kVDevSel, d);
    for (uint8_t k = 0; k < kSpecSlots; ++k) {
      if (!(slots & (1u << k))) continue;
      op_r((uint8_t)(kVRaw + 2 * k));
      op_r((uint8_t)(kVRaw + 2 * k + 1));
    }
  }
}

void As7265x::burst_sample_done() {
  // Walk the operation list in the order build_burst_read() made it.
  SpecBurstSample& s = bsample_;
  for (uint8_t i = 0; i < kSpecChannels; ++i) s.raw[i] = 0xFFFF;
  const uint16_t thr = (uint16_t)(spec_full_scale(burst_exp_.int_cycles) - 1u);
  uint8_t idx = op_base_;
  for (uint8_t d = 0; d < kSpecDevices; ++d) {
    if (!(cycle_dev_ & (1u << d))) continue;
    const uint8_t slots = spec_bank_slots(burst_bank_, d);
    ++idx;                                           // DEV_SEL write
    for (uint8_t k = 0; k < kSpecSlots; ++k) {
      if (!(slots & (1u << k))) continue;
      const uint8_t ch = kSpecSlotChannel[d][k];
      const uint16_t v = (uint16_t)(((uint16_t)rd_[idx] << 8) | rd_[idx + 1u]);
      idx = (uint8_t)(idx + 2u);
      s.raw[ch] = spec_burst_raw(v);                 // 65535 -> 0xFFFE (0xFFFF = not converted)
      burst_sum_[ch] += v;
      if (v >= thr) burst_sat_ |= 1u << ch;
    }
  }
  const uint32_t rd_ms = since(millis(), burst_sample_ms_);   // DATA_RDY seen -> data read
  if (rd_ms > burst_read_max_) burst_read_max_ = (uint16_t)(rd_ms > 65535u ? 65535u : rd_ms);
  if (burst_n_ == 0) burst_t0_ms_ = burst_sample_ms_;
  const uint32_t dt = burst_sample_ms_ - burst_t0_ms_;
  s.dt_ms = (uint16_t)(dt > 65535u ? 65535u : dt);
  burst_last_ms_ = burst_sample_ms_;
  if (burst_n_ < 65535) ++burst_n_;
  if (ring_n_ < kSpecBurstRing) {
    ring_[(uint8_t)((ring_head_ + ring_n_) % kSpecBurstRing)] = s;
    ++ring_n_;
  } else if (burst_drop_ < 65535) {
    ++burst_drop_;                                   // executive is not draining: newest dropped
  }
  burst_ref_ms_ = millis();
  phase_ = PH_BURST_WAIT;
}

void As7265x::finish_burst(uint32_t now) {
  SpecResult& r = result_;
  spec_result_clear(r);
  r.mode = SPEC_BURST;
  r.gain_code = burst_exp_.gain_code;
  r.int_cycles = burst_exp_.int_cycles;
  r.led_cur = spec_led_cur_applied(cfg_.led_cur);
  r.dev_mask = cycle_dev_;
  r.n_frames = (uint8_t)(burst_n_ > 255 ? 255 : burst_n_);
  r.burst_samples = burst_n_;
  r.burst_rate_dhz = burst_rate_dhz();
  r.burst_dropped = burst_drop_;
  r.burst_read_max_ms = burst_read_max_;
  uint32_t conv = 0;                                 // channels converted in this BANK mode
  for (uint8_t d = 0; d < kSpecDevices; ++d) {
    if (!(cycle_dev_ & (1u << d))) continue;
    const uint8_t slots = spec_bank_slots(burst_bank_, d);
    for (uint8_t k = 0; k < kSpecSlots; ++k)
      if (slots & (1u << k)) conv |= 1u << kSpecSlotChannel[d][k];
  }
  for (uint8_t ch = 0; ch < kSpecChannels; ++ch)     // raw = mean over the burst (rounded)
    if (burst_n_ && (conv & (1u << ch)))
      r.raw[ch] = (uint16_t)((burst_sum_[ch] + burst_n_ / 2u) / burst_n_);
  r.valid_mask = burst_n_ ? conv : 0;
  r.sat_mask = burst_sat_ & conv;
  r.flags = (uint8_t)((burst_n_ ? SPEC_F_VALID : 0) | (cfg_.gain == kSpecGainAuto ? SPEC_F_AGC : 0) |
                      (r.sat_mask ? SPEC_F_SAT : 0));
  for (uint8_t d = 0; d < kSpecDevices; ++d)          // read by the PH_BURST_STOP list
    r.temp[d] = (cycle_dev_ & (1u << d)) ? info_.temp[d] : (int8_t)-128;
  r.t_start_ms = t_start_;
  r.t_end_ms = millis();
  burst_active_ = false;
  consec_fail_ = 0;
  int_shadow_ = burst_exp_.int_cycles;
  exp_ = burst_exp_;
  end_cycle(now);
}

// =============================================================================
// SPECDIAG (read-only)
// =============================================================================
bool As7265x::build_diag() {
  if (!diag_pending_ || diag_line_ready_) return false;
  state_ = ST_BUSY;
  ops_begin(PH_DIAG);
  op_w(kVDevSel, diag_dev_);
  if (diag_hdr_) {
    // Whether HW version / CONFIG / INTEGRATION follow DEV_SEL is not stated by the
    // datasheet; they are read with the device selected so the line shows what it reports.
    op_r(kVHwH);
    op_r(kVHwL);
    op_r(kVDevSel);
    op_r(kVLed);
    op_r(kVConfig);
    op_r(kVInteg);
    op_r(kVTemp);
    diag_chunk_ = 0;
    return true;
  }
  diag_chunk_ = u8min(4, (uint8_t)(diag_n_ - diag_k_));
  for (uint8_t i = 0; i < diag_chunk_; ++i) {
    op_w(kVCoefRead, (uint8_t)(diag_k_ + i));       // sub-address, then the 4-octet word
    for (uint8_t k = 0; k < 4; ++k) op_r((uint8_t)(kVCoefData + k));
  }
  return true;
}

}  // namespace vgq
