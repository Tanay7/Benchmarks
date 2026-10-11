#include "autonomy.h"
#include <stdio.h>
#include "commands.h"

namespace vgq {

namespace {

// Half of the u32 SCLK circle: (now - due) below this means "due reached" (file header).
const uint32_t kHalfRange = 0x80000000u;

uint16_t get_be16(const uint8_t* p) { return (uint16_t)(((uint16_t)p[0] << 8) | p[1]); }
uint32_t get_be32(const uint8_t* p) {
  return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | p[3];
}
// Two's-complement big-endian i16 without implementation-defined conversions.
int16_t get_be16s(const uint8_t* p) {
  const uint16_t u = get_be16(p);
  return u < 0x8000u ? (int16_t)u : (int16_t)((int32_t)u - 65536);
}

// "t is strictly after now and at most kSeqHorizonS later", modulo 2^32.
bool in_horizon(uint32_t t, uint32_t now_s) {
  const uint32_t lead = t - now_s;
  return lead >= 1u && lead <= kSeqHorizonS;
}

// snprintf into a bounded buffer; returns the characters actually stored.
size_t stored_len(int n, size_t cap) {
  if (n < 0 || cap == 0) return 0;
  return (size_t)n < cap ? (size_t)n : cap - 1;
}

const char* unit_name(uint8_t unit) { return unit == 0 ? "OB" : unit == 1 ? "IB" : "??"; }

}  // namespace

// =============================================================================
// 1. Stored command sequences
// =============================================================================
uint8_t seq_cmd_error(SeqStatus s) {
  if (s == SEQ_OK) return CE_OK;
  return s == SEQ_ERR_LENGTH ? (uint8_t)CE_BAD_LENGTH : (uint8_t)CE_BAD_ARGUMENT;
}

const char* seq_status_text(SeqStatus s) {
  switch (s) {
    case SEQ_OK:               return "ok";
    case SEQ_ERR_LENGTH:       return "argument block length";
    case SEQ_ERR_SLOT:         return "slot out of range";
    case SEQ_ERR_FLAGS:        return "reserved flag bits set";
    case SEQ_ERR_CMD_LENGTH:   return "command length not 1..12";
    case SEQ_ERR_PADDING:      return "padding not zero";
    case SEQ_ERR_TIME_PAST:    return "time not in the future";
    case SEQ_ERR_TIME_RANGE:   return "time beyond 7 days";
    case SEQ_ERR_FORBIDDEN:    return "opcode not allowed in a sequence";
    case SEQ_ERR_NO_VALIDATOR: return "no command validator";
    case SEQ_ERR_COMMAND:      return "embedded command invalid";
    case SEQ_ERR_ACTION:       return "action out of range";
    case SEQ_ERR_SLOT_ARG:     return "slot must be 0 for this action";
    case SEQ_ERR_SLOT_EMPTY:   return "slot empty";
    case SEQ_ERR_EMPTY:        return "no entries loaded";
    case SEQ_ERR_ARMED:        return "already armed";
    case SEQ_ERR_FDIR_SAFE:    return "fault protection holds SAFE";
  }
  return "unknown";
}

bool CommandSequencer::opcode_forbidden(uint8_t opcode) {
  switch (opcode) {
    // Hazardous in ground/dssq/dictionary.py: they change the link or erase data, so
    // only an operator may send them, live and with confirmation.
    case OP_AIRRATE: case OP_SSRCLEAR: case OP_CMDLOSS: case OP_FDIRMASK:
    case OP_TXINHIBIT: case OP_HIBERNATE: case OP_CHANNEL:
    // REBOOT would also destroy the (RAM-only) table that holds it; SEQLOAD / SEQCTRL
    // would nest sequences.
    case OP_REBOOT: case kOpcodeSeqLoad: case kOpcodeSeqCtrl:
      return true;
    default:
      return false;
  }
}

void CommandSequencer::reset() {
  for (uint8_t i = 0; i < kSeqSlots; ++i) slots_[i] = SeqEntry();
  epoch_s_ = 0;
  state_ = SEQ_IDLE;
}

void CommandSequencer::set_validator(SeqCmdValidator fn, void* ctx) {
  validator_ = fn;
  validator_ctx_ = ctx;
}

uint8_t CommandSequencer::count() const {
  uint8_t n = 0;
  for (uint8_t i = 0; i < kSeqSlots; ++i) n = (uint8_t)(n + (slots_[i].used ? 1u : 0u));
  return n;
}

bool CommandSequencer::entry(uint8_t slot, SeqEntry& out) const {
  if (slot >= kSeqSlots || !slots_[slot].used) return false;
  out = slots_[slot];
  return true;
}

uint32_t CommandSequencer::due_of(const SeqEntry& e) const {
  return e.relative ? epoch_s_ + e.t_s : e.t_s;          // modulo 2^32 by definition
}

// Earliest due time among the occupied slots; ties go to the lower slot. The key
// due - now + 2^31 maps the half-circle before now to [0, 2^31) and the half-circle
// after it to [2^31, 2^32), so an unsigned comparison of keys orders by due time.
bool CommandSequencer::earliest(uint32_t now_s, uint8_t& slot, uint32_t& due) const {
  bool found = false;
  uint32_t best_key = 0;
  for (uint8_t i = 0; i < kSeqSlots; ++i) {
    if (!slots_[i].used) continue;
    const uint32_t d = due_of(slots_[i]);
    const uint32_t key = d - now_s + kHalfRange;
    if (!found || key < best_key) {
      found = true;
      best_key = key;
      slot = i;
      due = d;
    }
  }
  return found;
}

SeqStatus CommandSequencer::load(const uint8_t* args, size_t n, uint32_t now_s,
                                 SeqLoadReport& rep) {
  rep = SeqLoadReport();
  if (args == nullptr || n != kSeqLoadLen) return SEQ_ERR_LENGTH;
  const uint8_t slot = args[0];
  const uint8_t flags = args[1];
  const uint32_t t_s = get_be32(args + 2);
  const uint8_t len = args[6];
  const uint8_t* cmd = args + 7;
  rep.slot = slot;
  rep.opcode = cmd[0];
  rep.relative = (flags & kSeqFlagRelative) != 0;
  rep.t_s = t_s;

  if (slot >= kSeqSlots) return SEQ_ERR_SLOT;
  if ((flags & (uint8_t)~kSeqFlagRelative) != 0) return SEQ_ERR_FLAGS;
  if (len == 0 || len > kSeqCmdMax) return SEQ_ERR_CMD_LENGTH;
  for (uint8_t i = len; i < kSeqCmdMax; ++i) {
    if (cmd[i] != 0) return SEQ_ERR_PADDING;
  }
  if (rep.relative) {
    if (t_s > kSeqHorizonS) return SEQ_ERR_TIME_RANGE;
    // While armed a relative entry is timed at once: it must still lie ahead.
    if (state_ == SEQ_ARMED && !in_horizon(epoch_s_ + t_s, now_s)) return SEQ_ERR_TIME_PAST;
  } else {
    const uint32_t lead = t_s - now_s;
    if (lead == 0 || lead >= kHalfRange) return SEQ_ERR_TIME_PAST;
    if (lead > kSeqHorizonS) return SEQ_ERR_TIME_RANGE;
  }
  if (opcode_forbidden(cmd[0])) return SEQ_ERR_FORBIDDEN;
  if (validator_ == nullptr) return SEQ_ERR_NO_VALIDATOR;
  const uint8_t ce = validator_(validator_ctx_, cmd, len);
  if (ce != 0) {
    rep.cmd_error = ce;
    return SEQ_ERR_COMMAND;
  }

  SeqEntry& e = slots_[slot];
  rep.replaced = e.used;
  e = SeqEntry();
  e.used = true;
  e.relative = rep.relative;
  e.len = len;
  e.t_s = t_s;
  for (uint8_t i = 0; i < kSeqCmdMax; ++i) e.cmd[i] = cmd[i];
  return SEQ_OK;
}

SeqStatus CommandSequencer::control(uint8_t action, uint8_t slot, uint32_t now_s,
                                    bool fdir_safe) {
  if (action > SEQ_DELETE_SLOT) return SEQ_ERR_ACTION;
  if (action != SEQ_DELETE_SLOT && slot != 0) return SEQ_ERR_SLOT_ARG;
  switch (action) {
    case SEQ_START:
      if (state_ == SEQ_ARMED) return SEQ_ERR_ARMED;
      if (count() == 0) return SEQ_ERR_EMPTY;
      if (fdir_safe) return SEQ_ERR_FDIR_SAFE;
      epoch_s_ = now_s;
      state_ = SEQ_ARMED;
      return SEQ_OK;
    case SEQ_STOP:
      state_ = SEQ_IDLE;
      return SEQ_OK;
    case SEQ_CLEAR_ALL:
      reset();
      return SEQ_OK;
    default:   // SEQ_DELETE_SLOT
      if (slot >= kSeqSlots) return SEQ_ERR_SLOT;
      if (!slots_[slot].used) return SEQ_ERR_SLOT_EMPTY;
      slots_[slot] = SeqEntry();
      if (state_ == SEQ_ARMED && count() == 0) state_ = SEQ_IDLE;
      return SEQ_OK;
  }
}

SeqEventKind CommandSequencer::poll(uint32_t now_s, bool fdir_safe, SeqEvent& ev) {
  ev = SeqEvent();
  if (state_ != SEQ_ARMED) return SEQ_EV_NONE;
  if (fdir_safe) {
    state_ = SEQ_STOPPED_BY_FDIR;
    ev.kind = SEQ_EV_STOPPED_BY_FDIR;
    return SEQ_EV_STOPPED_BY_FDIR;
  }
  uint8_t slot = 0;
  uint32_t due = 0;
  if (!earliest(now_s, slot, due)) {     // unreachable: an armed table is never empty
    state_ = SEQ_IDLE;
    return SEQ_EV_NONE;
  }
  const uint32_t late = now_s - due;
  if (late >= kHalfRange) return SEQ_EV_NONE;     // earliest entry still in the future

  const SeqEntry& e = slots_[slot];
  ev.slot = slot;
  ev.len = e.len;
  for (uint8_t i = 0; i < kSeqCmdMax; ++i) ev.cmd[i] = e.cmd[i];
  ev.due_s = due;
  ev.late_s = late;
  ev.kind = late > kSeqLateMaxS ? SEQ_EV_SKIPPED_LATE : SEQ_EV_EXECUTE;
  slots_[slot] = SeqEntry();
  if (count() == 0) {
    state_ = SEQ_COMPLETE;
    ev.completed = true;
  }
  return (SeqEventKind)ev.kind;
}

uint16_t CommandSequencer::next_dt_s(uint32_t now_s) const {
  uint8_t slot = 0;
  uint32_t due = 0;
  if (state_ != SEQ_ARMED || !earliest(now_s, slot, due)) return kSeqDtNone;
  if (now_s - due < kHalfRange) return 0;           // due now or late
  const uint32_t lead = due - now_s;
  return lead > kSeqDtMax ? kSeqDtMax : (uint16_t)lead;
}

// =============================================================================
// 2. Beacon urgency level
// =============================================================================
uint8_t beacon_level(const BeaconInputs& in) {
  if ((in.fdir_raised & kBeaconUrgentFdir) != 0 || in.safe_by_fdir || in.seq_stopped_by_fdir ||
      in.ssr_dropped || in.brownout)
    return BEACON_URGENT;
  if (in.evr_max_severity >= kEvrSevWarn || in.sensor_lost) return BEACON_IMPORTANT;
  if (in.science_event || in.seq_complete) return BEACON_INTERESTING;
  return BEACON_NOMINAL;
}

void BeaconMonitor::begin(uint8_t reset_cause) {
  acc_ = BeaconInputs();
  acc_.brownout = reset_cause == kResetCauseBrownout;
  prev_fdir_ = 0;
  prev_health_ = 0;
  prev_ssr_dropped_ = 0;
  prev_seq_state_ = SEQ_IDLE;
  have_sample_ = false;
  latched_ = beacon_level(acc_);
}

void BeaconMonitor::raise() {
  const uint8_t l = beacon_level(acc_);
  if (l > latched_) latched_ = l;
}

void BeaconMonitor::note_evr(uint8_t severity, uint16_t id) {
  if (severity > acc_.evr_max_severity) acc_.evr_max_severity = severity;
  if (id == kEvrSpectralEvent || id == kEvrLightSourceChange) acc_.science_event = true;
  raise();
}

void BeaconMonitor::baseline(const BeaconSample& s) {
  prev_fdir_ = s.fdir_flags;
  prev_health_ = s.sensor_health;
  prev_ssr_dropped_ = s.ssr_dropped;
  prev_seq_state_ = s.seq_state;
  acc_.safe_by_fdir = s.safe_by_fdir;
  acc_.seq_stopped_by_fdir = s.seq_state == SEQ_STOPPED_BY_FDIR;
  have_sample_ = true;
}

uint8_t BeaconMonitor::update(const BeaconSample& s) {
  if (!have_sample_) {
    // First sample after boot is the baseline: instruments found absent at boot are not
    // "lost" and earlier SSR drops are not new. FDIR flags already set at boot DO count
    // (prev_fdir_ stays 0): they were raised during this boot.
    baseline(s);
    prev_fdir_ = 0;
  }
  acc_.fdir_raised = (uint16_t)(acc_.fdir_raised | (s.fdir_flags & (uint16_t)~prev_fdir_));
  const uint16_t lost =
      (uint16_t)(prev_health_ & (uint16_t)~s.sensor_health & s.sensor_enable & kBeaconInstruments);
  if (lost != 0) acc_.sensor_lost = true;
  if (s.ssr_dropped > prev_ssr_dropped_) acc_.ssr_dropped = true;
  if (s.seq_state == SEQ_COMPLETE && prev_seq_state_ != SEQ_COMPLETE) acc_.seq_complete = true;
  baseline(s);
  raise();
  return latched_;
}

void BeaconMonitor::on_ground_contact(const BeaconSample& now) {
  acc_ = BeaconInputs();             // events are acknowledged (brownout included)
  baseline(now);                     // level conditions are re-evaluated from `now`
  latched_ = beacon_level(acc_);
}

// =============================================================================
// 3. Magnetometer calibration
// =============================================================================
uint8_t magcal_cmd_error(MagCalStatus s) {
  if (s == MAGCAL_OK) return CE_OK;
  return s == MAGCAL_ERR_LENGTH ? (uint8_t)CE_BAD_LENGTH : (uint8_t)CE_BAD_ARGUMENT;
}

const char* magcal_status_text(MagCalStatus s) {
  switch (s) {
    case MAGCAL_OK:               return "ok";
    case MAGCAL_ERR_LENGTH:       return "argument block length";
    case MAGCAL_ERR_UNIT:         return "unit out of range";
    case MAGCAL_ERR_OFFSET:       return "offset beyond 300 uT";
    case MAGCAL_ERR_DIAGONAL:     return "diagonal not 0.5..2.0";
    case MAGCAL_ERR_OFFDIAG:      return "off-diagonal beyond 0.5";
    case MAGCAL_ERR_NOT_POSITIVE: return "matrix not positive definite";
  }
  return "unknown";
}

MagCalCoeffs magcal_identity() {
  MagCalCoeffs c = MagCalCoeffs();
  c.m[0] = c.m[1] = c.m[2] = kMagCalOne;
  return c;
}

bool magcal_is_identity(const MagCalCoeffs& c) {
  return c.off[0] == 0 && c.off[1] == 0 && c.off[2] == 0 && c.m[0] == kMagCalOne &&
         c.m[1] == kMagCalOne && c.m[2] == kMagCalOne && c.m[3] == 0 && c.m[4] == 0 &&
         c.m[5] == 0;
}

MagCalStatus magcal_validate(const MagCalCoeffs& c) {
  for (uint8_t i = 0; i < 3; ++i) {
    if (c.off[i] > kMagCalOffsetMax || c.off[i] < -kMagCalOffsetMax) return MAGCAL_ERR_OFFSET;
  }
  for (uint8_t i = 0; i < 3; ++i) {
    if (c.m[i] < kMagCalDiagMin) return MAGCAL_ERR_DIAGONAL;   // upper bound = INT16_MAX
  }
  for (uint8_t i = 3; i < 6; ++i) {
    if (c.m[i] > kMagCalOffDiagMax || c.m[i] < -kMagCalOffDiagMax) return MAGCAL_ERR_OFFDIAG;
  }
  // Sylvester's criterion on M = [[a d e] [d b f] [e f c]], exact in int64 (|entries| <=
  // 2^15, so every product of three stays below 2^46). a > 0 holds (a >= 8192).
  const int64_t a = c.m[0], b = c.m[1], cc = c.m[2], d = c.m[3], e = c.m[4], f = c.m[5];
  const int64_t minor2 = a * b - d * d;
  const int64_t det = a * (b * cc - f * f) - d * (d * cc - f * e) + e * (d * f - b * e);
  if (minor2 <= 0 || det <= 0) return MAGCAL_ERR_NOT_POSITIVE;
  return MAGCAL_OK;
}

MagCalStatus magcal_parse(const uint8_t* args, size_t n, uint8_t& unit, MagCalCoeffs& c) {
  c = magcal_identity();
  unit = 0xFF;
  if (args == nullptr || n != kMagCalLen) return MAGCAL_ERR_LENGTH;
  unit = args[0];
  if (unit >= kMagUnits) return MAGCAL_ERR_UNIT;
  MagCalCoeffs in = MagCalCoeffs();
  for (uint8_t i = 0; i < 3; ++i) in.off[i] = get_be16s(args + 1 + 2 * i);
  for (uint8_t i = 0; i < 6; ++i) in.m[i] = get_be16s(args + 7 + 2 * i);
  const MagCalStatus s = magcal_validate(in);
  if (s == MAGCAL_OK) c = in;
  return s;
}

void MagCalStore::store(uint8_t unit, const MagCalCoeffs& c) {
  c_[unit] = c;
  active_[unit] = !magcal_is_identity(c);
  // Wire order m11 m22 m33 m12 m13 m23 -> symmetric row-major 3 x 3.
  static const uint8_t kIdx[9] = {0, 3, 4, 3, 1, 5, 4, 5, 2};
  for (uint8_t k = 0; k < 9; ++k) m_[unit][k] = (float)c.m[kIdx[k]] / kMagCalQ14Scale;
  for (uint8_t i = 0; i < 3; ++i) b_[unit][i] = (float)c.off[i] / kMagCalOffsetLsbPerUt;
}

void MagCalStore::reset() {
  for (uint8_t u = 0; u < kMagUnits; ++u) store(u, magcal_identity());
}

MagCalStatus MagCalStore::set(uint8_t unit, const MagCalCoeffs& c) {
  if (unit >= kMagUnits) return MAGCAL_ERR_UNIT;
  const MagCalStatus s = magcal_validate(c);
  if (s == MAGCAL_OK) store(unit, c);
  return s;
}

MagCalStatus MagCalStore::load(const uint8_t* args, size_t n, uint8_t& unit) {
  MagCalCoeffs c;
  const MagCalStatus s = magcal_parse(args, n, unit, c);
  if (s != MAGCAL_OK) return s;
  return set(unit, c);
}

bool MagCalStore::active(uint8_t unit) const { return unit < kMagUnits && active_[unit]; }

uint8_t MagCalStore::state() const {
  return (uint8_t)((active_[0] ? 1u : 0u) | (active_[1] ? 2u : 0u));
}

MagCalCoeffs MagCalStore::coeffs(uint8_t unit) const {
  return unit < kMagUnits ? c_[unit] : magcal_identity();
}

bool MagCalStore::apply(uint8_t unit, const float raw_ut[3], float cal_ut[3]) const {
  if (unit >= kMagUnits || !active_[unit]) {
    for (uint8_t i = 0; i < 3; ++i) cal_ut[i] = raw_ut[i];
    return unit < kMagUnits;
  }
  float d[3];
  for (uint8_t i = 0; i < 3; ++i) d[i] = raw_ut[i] - b_[unit][i];
  for (uint8_t r = 0; r < 3; ++r) {
    const float* row = &m_[unit][3 * r];
    cal_ut[r] = row[0] * d[0] + row[1] * d[1] + row[2] * d[2];
  }
  return true;
}

size_t magcal_evr_text(uint8_t unit, const MagCalCoeffs& c, char* out, size_t cap) {
  if (out == nullptr || cap == 0) return 0;
  int n;
  if (magcal_is_identity(c)) {
    n = snprintf(out, cap, "MAGCAL %s cleared (identity)", unit_name(unit));
  } else {
    n = snprintf(out, cap, "MAGCAL %s b=%d,%d,%d M=%d,%d,%d,%d,%d,%d", unit_name(unit),
                 (int)c.off[0], (int)c.off[1], (int)c.off[2], (int)c.m[0], (int)c.m[1],
                 (int)c.m[2], (int)c.m[3], (int)c.m[4], (int)c.m[5]);
  }
  return stored_len(n, cap);
}

// =============================================================================
// 4. RM3100 built-in self-test
// =============================================================================
MagBistResult mag_bist_not_run() {
  MagBistResult r = MagBistResult();
  r.status = MAG_BIST_NOT_RUN;
  r.cleanup_ok = true;
  return r;
}

MagBistResult rm3100_bist_decode(uint8_t reg) {
  MagBistResult r = mag_bist_not_run();
  r.reg = reg;
  if ((reg & kRm3100BistSte) == 0) {      // 5.6.1: OK bits are valid only while STE = 1
    r.status = MAG_BIST_INVALID;
    return r;
  }
  r.axes_ok = (uint8_t)((reg >> kRm3100BistOkShift) & 0x07u);   // b0 X, b1 Y, b2 Z
  r.status = r.axes_ok == 0x07u ? MAG_BIST_PASS : MAG_BIST_AXIS_FAIL;
  return r;
}

bool mag_bist_tested(const MagBistResult& r) {
  return r.status != MAG_BIST_NOT_RUN && r.status != MAG_BIST_BUSY;
}

uint8_t mag_bist_hk(const MagBistResult& outboard, const MagBistResult& inboard) {
  const MagBistResult* units[2] = {&outboard, &inboard};
  uint8_t v = 0;
  for (uint8_t u = 0; u < 2; ++u) {
    const MagBistResult& r = *units[u];
    if (!mag_bist_tested(r)) continue;
    const bool decoded = r.status == MAG_BIST_PASS || r.status == MAG_BIST_AXIS_FAIL;
    const uint8_t nib = (uint8_t)((decoded ? (r.axes_ok & 0x07u) : 0u) | 0x08u);
    v = (uint8_t)(v | (uint8_t)(nib << (4 * u)));
  }
  return v;
}

uint8_t magbist_unit_mask(uint8_t unit) {
  return unit == 0 ? 0x01 : unit == 1 ? 0x02 : unit == 2 ? 0x03 : 0x00;
}

uint8_t mag_bist_evr_severity(const MagBistResult& r) {
  const bool benign = r.status == MAG_BIST_PASS || r.status == MAG_BIST_NOT_RUN ||
                      r.status == MAG_BIST_BUSY;
  return (benign && r.cleanup_ok) ? 1 : kEvrSevWarn;
}

size_t mag_bist_evr_text(uint8_t unit, const MagBistResult& r, char* out, size_t cap) {
  if (out == nullptr || cap == 0) return 0;
  static const char* const kName[7] = {"NOT RUN (not fitted)", "PASS", "FAIL",
                                       "BUSY (measurement in progress)", "BUS ERROR",
                                       "TIMEOUT (no DRDY)", "INVALID (STE read 0)"};
  const char* name = r.status < 7 ? kName[r.status] : "?";
  const char* tail = r.cleanup_ok ? "" : " STE NOT CLEARED";
  int n;
  if (r.status == MAG_BIST_PASS || r.status == MAG_BIST_AXIS_FAIL) {
    n = snprintf(out, cap, "RM3100 %s BIST %s X %s Y %s Z %s (reg 0x%02X)%s", unit_name(unit),
                 name, (r.axes_ok & 1u) ? "ok" : "FAIL", (r.axes_ok & 2u) ? "ok" : "FAIL",
                 (r.axes_ok & 4u) ? "ok" : "FAIL", (unsigned)r.reg, tail);
  } else if (r.status == MAG_BIST_INVALID) {
    n = snprintf(out, cap, "RM3100 %s BIST %s (reg 0x%02X)%s", unit_name(unit), name,
                 (unsigned)r.reg, tail);
  } else {
    n = snprintf(out, cap, "RM3100 %s BIST %s%s", unit_name(unit), name, tail);
  }
  return stored_len(n, cap);
}

}  // namespace vgq
