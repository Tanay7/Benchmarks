// =============================================================================
//  VGQ-1 onboard autonomy (contract section 24) - pure C++, host-tested by
//  tools/host_test/test_autonomy.cpp. No Arduino, no heap, no recursion; every
//  loop is bounded by a compile-time constant; all state is static storage.
//
//  1. CommandSequencer: stored command sequences (24.1 SEQLOAD 0x1D / SEQCTRL
//     0x1E, 24.2), the analogue of the time-tagged sequences of the Voyager
//     Computer Command Subsystem. A 32-entry table holds commands with an
//     absolute SCLK time or a time relative to the START of the sequence; when
//     armed, at most one due entry per loop pass is handed to the normal command
//     dispatcher.
//  2. Beacon urgency level (24.4) after the Deep Space 1 Beacon Monitor
//     Operations Experiment: NOMINAL / INTERESTING / IMPORTANT / URGENT, latched
//     (it only rises) until the next ground contact.
//  3. MagCalStore: RM3100 hard- and soft-iron calibration (24.1 MAGCAL 0x20,
//     24.6), B_cal = M (B_raw - b), applied to the attitude path only.
//  4. RM3100 built-in self-test (24.1 MAGBIST 0x1F, 24.5): register values and
//     the decoding into HK mag_bist. PNI "RM3100 & RM2100 Sensor Suite User
//     Manual", Doc 1017252 r06, section 5.6.1 (BIST register 0x33). The I2C
//     transaction sequence is Rm3100::run_bist() in drivers/sensors.cpp.
//
//  Time: SCLK seconds since MCU boot (Sclk::seconds(), uint32_t). All SCLK
//  arithmetic is modulo 2^32 (unsigned subtraction only, no signed casts): a due
//  time D counts as reached at time N when (N - D) mod 2^32 < 2^31. Every due
//  time is created at most kSeqHorizonS (7 days) ahead of the SCLK at which it is
//  loaded or armed, so ordering and due detection are exact across the u32 wrap
//  (which a seconds-since-boot counter reaches only after 136 years) for as long
//  as an entry is not left pending for 2^31 s (68 years).
//
//  The one indirect call in this module is the injected live-command validator
//  (SeqCmdValidator): it keeps this file free of the flight's command table while
//  guaranteeing that SEQLOAD applies exactly the checks a live command gets.
// =============================================================================
#pragma once
#include <stddef.h>
#include <stdint.h>
#include "tlm_packets.h"

namespace vgq {

// ---- Opcodes introduced by contract 24.1 (commands.h Opcode gains the same values) ----
static const uint8_t kOpcodeSeqLoad = 0x1D;
static const uint8_t kOpcodeSeqCtrl = 0x1E;
static const uint8_t kOpcodeMagBist = 0x1F;
static const uint8_t kOpcodeMagCal  = 0x20;

// =============================================================================
// 1. Stored command sequences (24.1, 24.2)
// =============================================================================
static const uint8_t  kSeqSlots        = 32;       // table entries (24.2)
static const uint8_t  kSeqCmdMax       = 12;       // stored command: opcode + arguments, octets
static const uint8_t  kSeqLoadLen      = 19;       // SEQLOAD argument block, octets (24.1)
static const uint8_t  kSeqFlagRelative = 0x01;     // SEQLOAD flags b0: t_s relative to START
static const uint32_t kSeqHorizonS     = 604800;   // 7 days: relative limit (24.2), also the
                                                   // absolute look-ahead limit (file header)
static const uint32_t kSeqLateMaxS     = 300;      // s; an entry later than this is skipped (24.2)
static const uint16_t kSeqDtNone       = 0xFFFF;   // HK seq_next_dt_s: none due / not armed
static const uint16_t kSeqDtMax        = 0xFFFE;   // HK seq_next_dt_s saturation value

enum SeqState : uint8_t {          // HK seq_state (24.1)
  SEQ_IDLE = 0,                    // empty, loaded but not started, or STOPped
  SEQ_ARMED = 1,                   // running: due entries are dispatched
  SEQ_COMPLETE = 2,                // the last pending entry was dispatched or skipped
  SEQ_STOPPED_BY_FDIR = 3,         // disarmed by fault protection (entries kept)
};

enum SeqAction : uint8_t {         // SEQCTRL action (24.1)
  SEQ_START = 0, SEQ_STOP = 1, SEQ_CLEAR_ALL = 2, SEQ_DELETE_SLOT = 3,
};

// Outcome of load() / control(). The first failing check is reported.
enum SeqStatus : uint8_t {
  SEQ_OK = 0,
  SEQ_ERR_LENGTH,          // SEQLOAD argument block is not exactly kSeqLoadLen octets
  SEQ_ERR_SLOT,            // slot > 31
  SEQ_ERR_FLAGS,           // reserved SEQLOAD flag bits set
  SEQ_ERR_CMD_LENGTH,      // embedded command length outside 1..12
  SEQ_ERR_PADDING,         // octets after the embedded command are not zero
  SEQ_ERR_TIME_PAST,       // absolute time (or relative time while armed) not in the future
  SEQ_ERR_TIME_RANGE,      // more than kSeqHorizonS ahead
  SEQ_ERR_FORBIDDEN,       // hazardous opcode, REBOOT, SEQLOAD or SEQCTRL
  SEQ_ERR_NO_VALIDATOR,    // no live-command validator installed (fail safe)
  SEQ_ERR_COMMAND,         // the live-command validator refused it (SeqLoadReport.cmd_error)
  SEQ_ERR_ACTION,          // SEQCTRL action > 3
  SEQ_ERR_SLOT_ARG,        // SEQCTRL slot != 0 for START / STOP / CLEAR_ALL
  SEQ_ERR_SLOT_EMPTY,      // DELETE_SLOT of an empty slot
  SEQ_ERR_EMPTY,           // START with no entry loaded
  SEQ_ERR_ARMED,           // START while already armed (STOP first: re-arming re-times entries)
  SEQ_ERR_FDIR_SAFE,       // START while fault protection holds the spacecraft safe
};
// CMDVER error code for a status: SEQ_ERR_LENGTH -> 2 (CE_BAD_LENGTH), any other
// refusal -> 3 (CE_BAD_ARGUMENT), SEQ_OK -> 0. The EVR carries the precise reason.
uint8_t seq_cmd_error(SeqStatus s);
const char* seq_status_text(SeqStatus s);   // short ASCII reason for EVR text

// Live-command validator. Returns 0 (CE_OK) when `cmd` (opcode + arguments, `len`
// octets, 1..12) is a command the dispatcher would accept - known opcode, exact
// argument length, every value in range - else the non-zero CmdError code. It must
// have no side effects: it is called at load time only.
typedef uint8_t (*SeqCmdValidator)(void* ctx, const uint8_t* cmd, uint8_t len);

struct SeqEntry {
  bool used;
  bool relative;                   // t_s counts from the START epoch
  uint8_t len;                     // command octets, 1..12
  uint32_t t_s;                    // SCLK seconds (absolute) or seconds after START
  uint8_t cmd[kSeqCmdMax];         // opcode + arguments, zero-padded
};

struct SeqLoadReport {             // filled by load() for the CMDVER / EVR
  uint8_t slot;                    // slot of the block (valid from SEQ_ERR_SLOT on)
  uint8_t opcode;                  // embedded opcode (0 when the block is too short)
  uint8_t cmd_error;               // validator result when SEQ_ERR_COMMAND, else 0
  bool relative;
  bool replaced;                   // SEQ_OK and the slot held an entry, now overwritten
  uint32_t t_s;
};

enum SeqEventKind : uint8_t {
  SEQ_EV_NONE = 0,
  SEQ_EV_EXECUTE = 1,              // dispatch ev.cmd[0..len) now, source "SEQ slot n"
  SEQ_EV_SKIPPED_LATE = 2,         // entry removed unexecuted: ev.late_s > kSeqLateMaxS (EVR WARN)
  SEQ_EV_STOPPED_BY_FDIR = 3,      // disarmed by fault protection (EVR WARN)
};

struct SeqEvent {                  // one poll() result; the caller owns the storage
  uint8_t kind;                    // SeqEventKind
  uint8_t slot;
  uint8_t len;
  uint8_t cmd[kSeqCmdMax];
  uint32_t due_s;                  // SCLK second the entry was due
  uint32_t late_s;                 // now - due_s (0 = on time)
  bool completed;                  // it was the last pending entry: state is now COMPLETE
};

class CommandSequencer {
 public:
  // Boot / power-on state: table empty, IDLE, epoch 0 (RAM only: the flight EVRs that
  // stored sequences do not survive a reset). The validator is kept.
  void reset();
  // Installs the live-command validator (Phase B: the dispatcher's own argument parser).
  // A null function makes every SEQLOAD fail with SEQ_ERR_NO_VALIDATOR.
  void set_validator(SeqCmdValidator fn, void* ctx);

  // SEQLOAD: `args` = the 19 argument octets after the opcode (24.1): u8 slot | u8 flags |
  // u32 t_s | u8 len | u8 cmd[12]. Checks, in order: block length, slot, flags, command
  // length, zero padding, time (absolute: 1..kSeqHorizonS s after now_s; relative:
  // <= kSeqHorizonS and, while armed, epoch + t_s after now_s), forbidden opcode, the
  // validator. On SEQ_OK the entry is stored (an occupied slot is overwritten); the state
  // is not changed. `rep` is always filled.
  SeqStatus load(const uint8_t* args, size_t n, uint32_t now_s, SeqLoadReport& rep);

  // SEQCTRL (decoded arguments, range-checked again here). START arms with epoch = now_s
  // (refused while armed, empty or fdir_safe); STOP disarms to IDLE keeping the entries;
  // CLEAR_ALL empties the table (IDLE); DELETE_SLOT removes one entry (deleting the last
  // pending entry of an armed sequence disarms it to IDLE, not COMPLETE).
  SeqStatus control(uint8_t action, uint8_t slot, uint32_t now_s, bool fdir_safe);

  // Call once per loop pass. fdir_safe = the flight is in SAFE because fault protection
  // (not a MODE command) put it there. While ARMED: fdir_safe -> STOPPED_BY_FDIR;
  // otherwise the earliest entry (ties -> lower slot) that is due is removed from the
  // table and returned as EXECUTE, or as SKIPPED_LATE when more than kSeqLateMaxS late.
  // At most one entry per call. `ev` is always overwritten; the return value is ev.kind.
  SeqEventKind poll(uint32_t now_s, bool fdir_safe, SeqEvent& ev);

  // ---- HK (24.1) ----
  uint8_t state() const { return state_; }
  uint8_t count() const;                       // seq_count: entries pending
  // seq_next_dt_s: seconds until the next entry is due (0 = due now or late), saturating
  // at kSeqDtMax; kSeqDtNone when not armed or nothing pending.
  uint16_t next_dt_s(uint32_t now_s) const;

  uint32_t epoch_s() const { return epoch_s_; }
  bool entry(uint8_t slot, SeqEntry& out) const;   // false: slot out of range or empty
  // The sequencer's own refusal policy (24.2): hazardous opcodes, REBOOT, SEQLOAD and
  // SEQCTRL. MUST equal ground/dssq/dictionary.py hazardous=True plus REBOOT/SEQLOAD/
  // SEQCTRL (ground/tests/test_sequence.py cross-checks the flight's list).
  static bool opcode_forbidden(uint8_t opcode);

 private:
  uint32_t due_of(const SeqEntry& e) const;
  bool earliest(uint32_t now_s, uint8_t& slot, uint32_t& due) const;

  SeqEntry slots_[kSeqSlots] = {};
  SeqCmdValidator validator_ = nullptr;
  void* validator_ctx_ = nullptr;
  uint32_t epoch_s_ = 0;
  uint8_t state_ = SEQ_IDLE;
};

// =============================================================================
// 2. Beacon urgency level (24.4)
// =============================================================================
enum BeaconLevel : uint8_t {
  BEACON_NOMINAL = 0, BEACON_INTERESTING = 1, BEACON_IMPORTANT = 2, BEACON_URGENT = 3,
};
static const uint16_t kEvrSpectralEvent     = 0x0701;   // contract 13.6
static const uint16_t kEvrLightSourceChange = 0x0702;   // contract 13.6
static const uint8_t  kEvrSevWarn           = 2;        // EVR severities 0 DIAG .. 4 FATAL
static const uint8_t  kResetCauseBrownout   = 4;        // HK reset_cause BROWNOUT (see classify_reset_cause)

// Re-probe pacing for instruments absent at boot (C28), like the AS7265X driver's
// kSpecRetryMs back-off: first try kMinMs after begin(), the interval doubling to kMaxMs
// while something stays missing, back to kMinMs after an acquisition.
struct ReprobeTimer {
  static constexpr uint32_t kMinMs = 30000, kMaxMs = 600000;
  uint32_t t0_ms = 0, interval_ms = kMinMs;
  void begin(uint32_t now_ms) { t0_ms = now_ms; interval_ms = kMinMs; }
  bool due(uint32_t now_ms) const { return now_ms - t0_ms >= interval_ms; }
  void done(uint32_t now_ms, bool acquired) {
    t0_ms = now_ms;
    interval_ms = acquired ? kMinMs : (interval_ms >= kMaxMs / 2 ? kMaxMs : interval_ms * 2u);
  }
};

// HK reset_cause from the RCC_CSR reset flags (abstracted here so the rule is host-tested)
// and the "was running" marker. On the STM32U5 BORRSTF is also the power-on flag: the
// always-on BOR provides POR/PDR and there is no PORRSTF (RM0456 RCC_CSR; ST HAL
// stm32u5xx_hal_rcc.h RCC_RESET_FLAG_PWR = BORRSTF, "BOR or POR/PDR reset flag"), so a
// cold power-up shows BORRSTF | PINRSTF. A brown-out is told apart by a marker the
// flight writes into a backup register on every boot: a power-up resets the backup
// domain (no marker), a dip below the BOR level with the MCU running keeps it.
enum ResetFlag : uint8_t {
  RST_F_IWDG = 0x01, RST_F_WWDG = 0x02, RST_F_SOFT = 0x04, RST_F_BOR = 0x08, RST_F_PIN = 0x10,
};
inline uint8_t classify_reset_cause(uint8_t flags, bool warm_marker) {
  if (flags & (RST_F_IWDG | RST_F_WWDG)) return 3;                 // WATCHDOG
  if (flags & RST_F_SOFT) return 2;                                // SOFTWARE
  if (flags & RST_F_BOR) return warm_marker ? kResetCauseBrownout : 0;   // BROWNOUT / POWER-ON
  if (flags & RST_F_PIN) return 1;                                 // PIN
  return 255;                                                      // UNKNOWN
}
// FDIR flags whose RISING edge is an URGENT event: radio faults (configuration, AUX
// timeout, E22 abnormal status incl. its PA over-temperature / under-voltage bits),
// avionics over-temperature, command loss, sensor-bus failure, FARM-1 lockout. Edges,
// not levels, because several flags are sticky in the flight (never cleared after the
// response), which would otherwise hold the beacon at URGENT for the rest of the boot.
static const uint16_t kBeaconUrgentFdir =
    FDIR_RADIO_CFG | FDIR_AUX_TIMEOUT | FDIR_PA_OVERTEMP | FDIR_AVI_OVERTEMP | FDIR_CMD_LOSS |
    FDIR_I2C_BUS | FDIR_LOW_BUS_V | FDIR_FARM_LOCKOUT | FDIR_RADIO_FAULT;
// Instruments whose loss is IMPORTANT (the SENSORS-mask instrument bits, contract 3.7).
static const uint16_t kBeaconInstruments =
    SNS_NICLA_ME | SNS_AS7265X | SNS_RM3100_OB | SNS_RM3100_IB;

// Everything beacon_level() decides on. Level fields describe the spacecraft now; the
// others accumulate since the last ground contact (BeaconMonitor fills them).
struct BeaconInputs {
  // URGENT
  uint16_t fdir_raised;            // FDIR flags that rose since contact (kBeaconUrgentFdir used)
  bool safe_by_fdir;               // level: SAFE entered by fault protection or a reset
  bool seq_stopped_by_fdir;        // level: CommandSequencer state STOPPED_BY_FDIR
  bool ssr_dropped;                // the SSR dropped packets since contact
  bool brownout;                   // this boot started from a brownout reset
  // IMPORTANT
  uint8_t evr_max_severity;        // highest EVR severity since contact (0 = none / DIAG)
  bool sensor_lost;                // an enabled instrument dropped out since contact
  // INTERESTING
  bool science_event;              // EVR 0x0701 (spectral event) / 0x0702 (light source)
  bool seq_complete;               // the stored sequence completed since contact
};
uint8_t beacon_level(const BeaconInputs& in);   // pure: BeaconLevel

struct BeaconSample {              // filled by the flight once per second
  uint16_t fdir_flags;             // HK fdir_flags
  uint16_t sensor_health;          // HK sensor_health
  uint16_t sensor_enable;          // SENSORS mask: a disabled instrument is not "lost"
  uint32_t ssr_dropped;            // Ssr::dropped() (cumulative)
  uint8_t seq_state;               // CommandSequencer::state()
  bool safe_by_fdir;               // see BeaconInputs
};

// Latch: level() only rises until on_ground_contact(). Call order in the flight:
// begin() at boot; note_evr() for EVERY EVR generated (before severity filtering and
// throttling); update() at 1 Hz; on_ground_contact() AFTER executing each TC frame that
// passed SDLS and FARM-1, with a sample taken after that execution (so the EVRs and the
// state changes produced by the ground's own command are acknowledged with it: a MODE
// command that leaves SAFE must not re-latch URGENT from a stale sample). After a contact
// the level is recomputed from the level conditions alone, so a SAFE or STOPPED_BY_FDIR
// state that persists raises it again at once; the sample also becomes the new baseline
// for FDIR-flag, instrument and SSR-drop edges.
class BeaconMonitor {
 public:
  void begin(uint8_t reset_cause);
  void note_evr(uint8_t severity, uint16_t id);
  uint8_t update(const BeaconSample& s);       // returns level()
  void on_ground_contact(const BeaconSample& now);
  uint8_t level() const { return latched_; }   // HK beacon_level
  const BeaconInputs& inputs() const { return acc_; }

 private:
  void raise();
  void baseline(const BeaconSample& s);
  BeaconInputs acc_ = {};
  uint16_t prev_fdir_ = 0;
  uint16_t prev_health_ = 0;
  uint32_t prev_ssr_dropped_ = 0;
  uint8_t prev_seq_state_ = SEQ_IDLE;
  bool have_sample_ = false;
  uint8_t latched_ = BEACON_NOMINAL;
};

// =============================================================================
// 3. Magnetometer calibration (24.1 MAGCAL, 24.6)
// =============================================================================
static const uint8_t kMagUnits          = 2;       // 0 outboard (0x20), 1 inboard (0x23)
static const uint8_t kMagCalLen         = 19;      // MAGCAL argument block, octets (24.1)
static const int16_t kMagCalOffsetMax   = 30000;   // |off| limit, 0.01 uT (= 300 uT)
static const int16_t kMagCalDiagMin     = 8192;    // Q2.14 0.5 (maximum 32767 = 1.99994)
static const int16_t kMagCalOffDiagMax  = 8192;    // |off-diagonal| limit, Q2.14 0.5
static const int16_t kMagCalOne         = 16384;   // Q2.14 1.0
// Conversions by division: correctly rounded (0.01 has no exact binary form; 16384 does).
static const float   kMagCalOffsetLsbPerUt = 100.0f;    // offset: 0.01 uT per LSB
static const float   kMagCalQ14Scale       = 16384.0f;  // matrix: value = raw / 2^14

enum MagCalStatus : uint8_t {
  MAGCAL_OK = 0,
  MAGCAL_ERR_LENGTH,       // argument block is not exactly 19 octets
  MAGCAL_ERR_UNIT,         // unit > 1
  MAGCAL_ERR_OFFSET,       // |off_x|, |off_y| or |off_z| > 30000
  MAGCAL_ERR_DIAGONAL,     // m11, m22 or m33 outside 8192..32767
  MAGCAL_ERR_OFFDIAG,      // |m12|, |m13| or |m23| > 8192
  MAGCAL_ERR_NOT_POSITIVE, // M not positive definite (not a soft-iron correction)
};
uint8_t magcal_cmd_error(MagCalStatus s);       // 2 for LENGTH, 3 for the other refusals
const char* magcal_status_text(MagCalStatus s);

struct MagCalCoeffs {
  int16_t off[3];          // hard-iron offset b (x, y, z), 0.01 uT
  int16_t m[6];            // symmetric soft-iron M, Q2.14, wire order m11 m22 m33 m12 m13 m23
};
// Decodes and validates the 19 MAGCAL argument octets (24.1): u8 unit | i16 off_x, off_y,
// off_z | i16 m11, m22, m33, m12, m13, m23. Positive definiteness is checked exactly in
// 64-bit integers (Sylvester's criterion); the contract's ranges alone admit singular M.
MagCalStatus magcal_parse(const uint8_t* args, size_t n, uint8_t& unit, MagCalCoeffs& c);
MagCalStatus magcal_validate(const MagCalCoeffs& c);
bool magcal_is_identity(const MagCalCoeffs& c);  // zero offsets + identity matrix = cleared
MagCalCoeffs magcal_identity();

class MagCalStore {
 public:
  MagCalStore() { reset(); }
  void reset();                                  // boot: both units cleared (RAM only)
  // MAGCAL: parse, validate, store. `unit` is set as soon as it is decoded.
  MagCalStatus load(const uint8_t* args, size_t n, uint8_t& unit);
  MagCalStatus set(uint8_t unit, const MagCalCoeffs& c);
  bool active(uint8_t unit) const;               // a non-identity calibration is stored
  uint8_t state() const;                         // HK magcal_state: b0 outboard, b1 inboard
  MagCalCoeffs coeffs(uint8_t unit) const;       // identity for an out-of-range unit
  // cal_ut = M (raw_ut - b), uT, FP32. Inactive unit: copy. Returns false (and copies)
  // for an out-of-range unit. NaN inputs propagate.
  bool apply(uint8_t unit, const float raw_ut[3], float cal_ut[3]) const;

 private:
  void store(uint8_t unit, const MagCalCoeffs& c);   // unit < kMagUnits, c validated
  MagCalCoeffs c_[kMagUnits] = {};
  float m_[kMagUnits][9] = {};                   // M row-major, float
  float b_[kMagUnits][3] = {};                   // b, uT
  bool active_[kMagUnits] = {};
};
// EVR echo of accepted coefficients (raw wire integers), NUL-terminated, <= 80 characters:
// "MAGCAL OB b=<x>,<y>,<z> M=<m11>,<m22>,<m33>,<m12>,<m13>,<m23>" (b in 0.01 uT, M in
// 1/16384) or "MAGCAL OB cleared (identity)". Returns the length written (0 if cap == 0).
size_t magcal_evr_text(uint8_t unit, const MagCalCoeffs& c, char* out, size_t cap);

// =============================================================================
// 4. RM3100 built-in self-test (24.5; PNI manual Doc 1017252 r06 section 5.6.1)
// =============================================================================
// On I2C the register address is sent as is (the R/W bit is in the address byte); the
// 0xB3 / 0xB4 "read" forms in the manual are the SPI command bytes (contract 22.1).
static const uint8_t kRm3100RegPoll     = 0x00;   // POLL: b6 PMZ, b5 PMY, b4 PMX (5.3)
static const uint8_t kRm3100RegBist     = 0x33;   // BIST (5.6.1)
static const uint8_t kRm3100RegStatus   = 0x34;   // STATUS: b7 DRDY, b6..0 indeterminate (5.4.1)
static const uint8_t kRm3100PollXyz     = 0x70;   // measure (here: self-test) X, Y and Z
static const uint8_t kRm3100StatusDrdy  = 0x80;
static const uint8_t kRm3100BistSte     = 0x80;   // b7 STE: run the self-test on the next POLL
static const uint8_t kRm3100BistOkShift = 4;      // b6 ZOK, b5 YOK, b4 XOK (read-only)
// STE | BW = 11 (timeout 4 sleep-oscillation cycles = 120 us, Table 5-6) | BP = 11 (4 LR
// periods, Table 5-7, whose header mislabels the BP bits "BW1 BW0").
static const uint8_t kRm3100BistStart   = 0x8F;
static const uint8_t kRm3100BistOff     = 0x00;   // STE off: POLL measures normally again
// Bounded DRDY wait. The manual gives no total self-test duration; 3 axes x 4 LR periods
// plus at most 4 x 120 us of timeout per axis is well below 2 ms. VERIFY (docs/08): time
// DRDY after a MAGBIST on the bench.
static const uint32_t kRm3100BistTimeoutUs = 5000;
static const uint8_t  kRm3100BistMaxPolls  = 20;  // STATUS reads (each ~0.4 ms at 100 kHz)
static const uint8_t  kRm3100BistCleanupTries = 3;

enum MagBistStatus : uint8_t {
  MAG_BIST_NOT_RUN = 0,    // unit not fitted / not attempted
  MAG_BIST_PASS = 1,       // STE read back set, XOK YOK ZOK all 1
  MAG_BIST_AXIS_FAIL = 2,  // STE read back set, at least one axis 0
  MAG_BIST_BUSY = 3,       // not started: a measurement was in progress (keep the old result)
  MAG_BIST_BUS_ERROR = 4,  // an I2C transaction failed
  MAG_BIST_TIMEOUT = 5,    // DRDY did not rise within the bound
  MAG_BIST_INVALID = 6,    // STE read back 0: the OK bits are not valid (5.6.1)
};

struct MagBistResult {
  uint8_t status;          // MagBistStatus
  uint8_t reg;             // BIST register as read (0 when not read)
  uint8_t axes_ok;         // b0 X, b1 Y, b2 Z (PASS / AXIS_FAIL only)
  uint8_t drdy_polls;      // STATUS reads until DRDY (diagnostic)
  bool cleanup_ok;         // STE written back to 0 (false: the driver retries before POLL)
};
MagBistResult mag_bist_not_run();
// Decodes a BIST register value read after DRDY: PASS / AXIS_FAIL / INVALID.
MagBistResult rm3100_bist_decode(uint8_t reg);
// True when the result is an outcome of a test on a fitted unit (HK "tested" bit).
bool mag_bist_tested(const MagBistResult& r);
// HK mag_bist (24.1): b0..2 outboard X/Y/Z OK, b3 outboard tested, b4..6 inboard X/Y/Z OK,
// b7 inboard tested.
uint8_t mag_bist_hk(const MagBistResult& outboard, const MagBistResult& inboard);
// MAGBIST unit argument 0 outboard, 1 inboard, 2 both -> b0 outboard, b1 inboard; 0 = invalid.
uint8_t magbist_unit_mask(uint8_t unit);
// EVR severity: 1 (INFO) for PASS / NOT_RUN / BUSY, 2 (WARN) otherwise or when the
// cleanup failed. Text, <= 80 characters, e.g. "RM3100 OB BIST PASS X ok Y ok Z ok (reg 0xFF)".
uint8_t mag_bist_evr_severity(const MagBistResult& r);
size_t mag_bist_evr_text(uint8_t unit, const MagBistResult& r, char* out, size_t cap);

}  // namespace vgq
