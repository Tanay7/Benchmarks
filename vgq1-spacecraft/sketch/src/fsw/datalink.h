// =============================================================================
//  VGQ-1 data-link core of the flight executive - pure C++, host-tested
//  (tools/host_test/test_integration.cpp builds the downlink vectors the ground
//  cross-check decodes and runs ground-built uplink frames through tc_accept()).
//
//  Moved out of the Arduino-bound flight.cpp so that the protocol- and security-
//  critical decisions are verified on a PC with the exact flight code:
//    * Downlink    - VC0..3 packet streams, VC selection with the v2 anti-
//                    starvation rule, the VC priority reserve, SDLS TM (AE / null
//                    SA) nonce safety, OID identification frames, CADU coding
//                    (CCSDS 131.0-B / 132.0-B-3 / 732.1-B-2 / 355.0-B-2);
//    * tc_accept() - the uplink acceptance chain CLTU -> frame -> SDLS -> TFDF ->
//                    FARM-1 (231.0-B-4 / 232.0-B-4 / 732.1-B-2 / 355.0-B-2 /
//                    232.1-B-2), in the order tc.h documents;
//    * RelayGate   - authorisation of CFDP PDUs relayed from the Linux EGSE (727.0-B-5);
//    * EvrGate     - the routine-EVR budget (token bucket);
//    * the downlink load plan: v2 frame-period rule and run-time stretching of the
//      periodic packet periods to the measured link capacity (Phase B spec PB-3.3, C-27).
//  No Arduino, no heap, no recursion; every loop is bounded by a constant.
// =============================================================================
#pragma once
#include <stddef.h>
#include <stdint.h>
#include "../ccsds/cfdp.h"
#include "../ccsds/sdls.h"
#include "../ccsds/tc.h"
#include "../ccsds/tm.h"
#include "spectral.h"
#include "tlm_packets.h"

namespace vgq {

// Every user-data field fits kMaxPayload (PB-1.1, C-24).
static_assert(kSpecV31Len <= kMaxPayload, "SPEC v3.1 exceeds kMaxPayload");
static_assert(kSpecBurstMaxLen <= kMaxPayload, "SPECBURST exceeds kMaxPayload");
static_assert(ccsds::kCfdpMaxPduLen <= kMaxPayload, "CFDP PDU exceeds kMaxPayload");
static_assert(kHkLen <= kMaxPayload && kRfLen <= kMaxPayload && kEnvLen <= kMaxPayload &&
              kEvrHdrLen + kEvrTextMax <= kMaxPayload &&
              kRfscanHdrLen + kRfscanMaxChannels <= kMaxPayload, "packet exceeds kMaxPayload");
// The largest Space Packet (12 + 146 = 158 octets) fits one TM-AE data field.
static_assert(ccsds::kSpPriHdrLen + ccsds::kSpSecHdrLen + kMaxPayload <=
              ccsds::kTmDataLen - ccsds::kSdlsAeOverhead, "largest packet exceeds a TM-AE data field");

// =============================================================================
// Downlink: VC0..3 -> one CADU per call
// =============================================================================
static const size_t kSciPriorityBacklog = 500;   // v2 anti-starvation threshold, octets (VC1)
static const size_t kIdleTextMax = 32;           // OID identification text, characters
static const size_t kVcReserveOctets = 256;      // last octets of each VC kept for priority packets

struct DownlinkFrame {
  uint8_t vcid = 0;            // TM 0..3 / 7 (OID); USLP 0..3 / 63 (OID)
  uint8_t count = 0;           // TM: this frame's MCFC; USLP: its VCF count (TIMECORR ref_mcfc)
  bool oid = true;
  uint8_t security = 0;        // 0 clear, 1 AE protected, 2 null SA
};
enum DownlinkSecurity : uint8_t { DL_SEC_CLEAR = 0, DL_SEC_AE = 1, DL_SEC_NULL_SA = 2 };

// OID identification text (contract 5, F-42): writes "DE <callsign> VGQ-1 " into out
// (NUL-terminated), truncated to kIdleTextMax characters and to cap - 1. A null or empty
// callsign gives "DE VGQ-1 ". Returns the length (0 for a null / zero-size buffer).
size_t format_idle_text(const char* callsign, char* out, size_t cap);

class Downlink {
 public:
  // framing from VGQ_DOWNLINK_FRAMING; sec = &SdlsTm when VGQ_SDLS_TM == AE and a key
  // exists, else nullptr (clear frames); idle_text = "DE <callsign> VGQ-1 " (contract 5:
  // the OID frames identify the station in clear text), truncated to kIdleTextMax
  // characters; nullptr or "" gives the 0x55 idle pattern. Empties every VC and resets
  // all counters (call once, in setup()).
  void init(ccsds::Framing framing, ccsds::SdlsTm* sec, const char* idle_text);
  // false: v > 3, a null/empty packet, or the VC lacks room. A non-priority packet needs
  // backlog + len <= kBufLen - kVcReserveOctets; a priority packet (EVR severity >= 2,
  // CMDVER, EVR 0x0120..0x0122) may use the whole buffer. Every refusal for lack of room
  // (reserve, buffer or packet-start ring full) is counted in overflows(v).
  bool push(uint8_t v, const uint8_t* pkt, size_t len, bool priority = false);
  size_t data_field_len() const;           // TM 188 / USLP 183 octets, minus 30 with SDLS AE
                                           // configured (the null SA keeps the AE geometry)
  size_t backlog(uint8_t v) const;         // octets queued on VC v (0 for v > 3)
  bool has_real_data(uint8_t v) const;     // VcStream::has_real_data (false for v > 3)
  void reset_vc(uint8_t v);                // SSRCLEAR empties VC 2 (no-op for v > 3)
  // Hibernation beacon (PB-1, C20): called before the beacon HK (hk_len octets) is pushed
  // on VC0. Every routine packet is also in the SSR, so the real-time backlog the beacon
  // must not queue behind is dropped: all of VC1 (it could take every second frame by the
  // anti-starvation rule), and VC0 too when VC0 + the HK would need more than max_frames
  // frames. Returns the frames (1..max_frames) that carry VC0 up to and including the HK.
  uint8_t prepare_beacon(size_t hk_len, uint8_t max_frames);
  // Packets refused by push() on VC v (no room, or the priority reserve), counted since
  // init() and NOT cleared by reset_vc() (0 for v > 3).
  uint32_t overflows(uint8_t v) const;
  // Exactly one CADU per call. Data VCs only while sec is null or sec->ready(); selection
  // VC0 > VC1 > VC2 > VC3, except VC1 before VC0 when VC1 holds > kSciPriorityBacklog octets
  // and the previous data frame was VC0. A refused protect() (backstop) yields an OID frame.
  void next_cadu(const ccsds::Clcw& clcw, uint8_t cadu[ccsds::kCaduLen], DownlinkFrame& info);
  uint8_t mcfc() const;                    // TmFramer::mcfc()
  ccsds::Framing framing() const;
  uint32_t oid_frames() const;
  uint32_t refused() const;                // frames the SDLS protector refused (backstop)

 private:
  ccsds::TmFramer framer_;
  ccsds::VcStream vc_[ccsds::kNumDataVcs];
  ccsds::SdlsTm* sec_ = nullptr;
  uint8_t idle_[kIdleTextMax] = {0};
  size_t idle_len_ = 0;
  uint16_t idle_seq_ = 0;                  // APID 0x7FF sequence counter
  uint8_t last_data_vc_ = 0xFF;            // VC of the previous data frame (0xFF: none yet)
  uint32_t ovf_[ccsds::kNumDataVcs] = {0, 0, 0, 0};
  uint32_t oid_ = 0;
  uint32_t refused_ = 0;
};

// =============================================================================
// Uplink acceptance chain
// =============================================================================
enum class TcStage : uint8_t { NO_CLTU = 0, FRAME_REJECTED = 1, SDLS_REJECTED = 2,
                               TFDF_REJECTED = 3, FARM = 4 };
struct TcOutcome {
  TcStage stage = TcStage::NO_CLTU;
  ccsds::TcParseResult parse = ccsds::TC_OK;
  ccsds::Sdls::Result sdls = ccsds::Sdls::SDLS_DISABLED;
  ccsds::Farm1::Verdict verdict = ccsds::Farm1::CONTROL_INVALID;
  // NOT valid at NO_CLTU / FRAME_REJECTED (tc_parse failed; left default-constructed).
  // From SDLS_REJECTED on the header fields are valid (data not authenticated); from
  // TFDF_REJECTED on the data is authenticated; at FARM frame.data -> the TC packet (or
  // COP-1 directive) inside `work`.
  ccsds::TcFrame frame;
  size_t info_len = 0;         // octets recovered from the CLTU
};
// "TOO_SHORT", "BAD_VERSION", "BAD_SCID", "BAD_LENGTH", "BAD_FECF", "BAD_HEADER", "BAD_TFDF"
// (TcParseResult 1..7 = EVR 0x0101..0x0107 texts); "OK" for 0, "?" otherwise. Lives here
// because tc.h has no Phase B owner.
const char* tc_parse_result_name(ccsds::TcParseResult r);
// Minimum `work` buffer. A TC frame is at most kTcMaxFrameLen = 64 octets = 10 BCH
// codeblocks (70 info octets); 96 octets hold the info octets of 13 codeblocks (91), more
// than the 12 of the longest hardline CLTU (hl_cltu: 110 octets = 2 + 12 x 8 + 8 + 4 spare).
// cltu_decode() stops at the buffer end, which can only cut fill octets beyond a frame.
static const size_t kTcWorkMin = 96;
// cltu_decode -> tc_parse(open_tfdf = !sdls.enabled()) -> Sdls::process (when enabled)
// -> tc_open_tfdf -> Farm1::on_frame (tc.h order). `work` (>= kTcWorkMin octets) receives
// the CLTU info octets and is decrypted in place. FARM state is touched only at the FARM
// stage; SDLS anti-replay state only by a frame SDLS accepts (an authenticated frame whose
// USLP TFDF header is then malformed has consumed its SN - it came from the key holder).
// A null input/buffer or work_cap < kTcWorkMin yields NO_CLTU with nothing decoded.
TcOutcome tc_accept(const uint8_t* in, size_t n, uint8_t* work, size_t work_cap,
                    ccsds::BchStats& bch, ccsds::Sdls& sdls, ccsds::Farm1& farm);

// =============================================================================
// CFDP relay authorisation (PB-2.5). Times are MCU ms (wrap-safe differences).
// RAM only: after an MCU reboot the gate is NONE and any still-running EGSE
// transfer is refused.
// =============================================================================
class RelayGate {
 public:
  enum State : uint8_t { NONE = 0, REQUESTED, ACTIVE, CANCELLING };
  enum Event : uint8_t { EV_NONE = 0, EV_START_TIMEOUT, EV_CANCEL_TIMEOUT };
  // NONE -> REQUESTED with the one authorised `seq` (an EGSE number, >= 0x80000000);
  // ignored in any other state (the executive answers CE_BUSY first).
  void request(uint32_t seq, uint32_t now_ms);
  bool cancel(uint32_t now_ms);                      // REQUESTED/ACTIVE -> CANCELLING; false otherwise
  // 1 admitted, -1 refused: CfdpRelayMonitor::acceptable() && seq == seq() && (REQUESTED or
  // ACTIVE, or CANCELLING and the PDU is an EOF). REQUESTED -> ACTIVE; an admitted EOF -> NONE.
  int admit(const uint8_t* pdu, size_t len, uint32_t now_ms);
  // REQUESTED / CANCELLING for timeout_ms (kRelayStartTimeoutMs) without the PDU that
  // ends the wait -> NONE and the matching event; ACTIVE is left to CfdpRelayMonitor.
  Event poll(uint32_t now_ms, uint32_t timeout_ms);
  void reset();                                      // -> NONE (relay silent, CfdpRelayMonitor expired)
  State state() const { return state_; }
  uint32_t seq() const { return seq_; }
  uint32_t refused() const { return refused_; }      // admit() == -1 count (sc_status_x relay_ref)

 private:
  int refuse() { ++refused_; return -1; }
  State state_ = NONE;
  uint32_t seq_ = 0;
  uint32_t t_ms_ = 0;          // when REQUESTED / CANCELLING began
  uint32_t refused_ = 0;
};

// =============================================================================
// Routine-EVR budget (PB-2.6): token bucket in milli-EVRs, capacity 10 EVRs.
// =============================================================================
static const uint32_t kEvrBucketMilli = 10000;   // 10 EVRs
class EvrGate {
 public:
  // Adds elapsed x rate (milli-EVR/s), keeping the sub-milli remainder, capped at 10 EVRs.
  void refill(uint32_t elapsed_ms, uint32_t milli_per_s);
  void fill();                                   // full bucket (end of setup())
  bool admit(uint8_t severity, bool exempt);     // sev >= 3 or exempt: true, no token
  uint32_t throttled() const { return throttled_; }
  uint32_t tokens_milli() const { return tokens_; }

 private:
  uint32_t tokens_ = kEvrBucketMilli;            // starts full (also before fill())
  uint32_t rem_ = 0;                             // micro-EVRs below one milli-EVR
  uint32_t throttled_ = 0;
};

// =============================================================================
// Downlink load control (PB-3.3, C-27)
// =============================================================================
enum PeriodicIdx : uint8_t { PER_HK = 0, PER_RF, PER_TIMECORR, PER_MAG, PER_ATT, PER_SPEC, PER_ENV,
                             kNumPeriodic };
extern const uint16_t kModeCadence[5][kNumPeriodic];  // s, 0 = off - the v2 kCadence table moved
                                                      // unchanged from flight.cpp
extern const uint8_t  kModePeriodX10[5];              // v2 kPeriodX10 {40, 40, 25, 20, 20}, moved
extern const uint16_t kPeriodicPktLen[kNumPeriodic];  // whole Space Packets: 90, 61, 22, 58, 71, 141, 36
static const uint16_t kPeriodicLoadPermille = 750;
static const uint16_t kLinkUsePermille = 950;         // periodic + routine EVRs together
static const uint16_t kEvrNominalOctets = 80;         // 12 + 3 + ~65 text
static const uint32_t kEvrMaxMilliPerS = 167;         // never more than the v2 10 per minute
// v2 rule, unchanged, now pure: busy x kModePeriodX10[mode] / 10, x2 with PA over-temperature,
// >= busy / kMaxTxDutyPermille, >= kMinFramePeriodMs, >= the FPERIOD command value
// (kMinFramePeriodMs and kMaxTxDutyPermille: config.h). A mode above TEST counts as SAFE.
uint32_t frame_period_ms(uint32_t busy_ms, uint8_t mode, bool pa_overtemp, uint16_t cmd_period_ms);

// Frame spacing across a radio reconfiguration (Flight::apply_radio_config(): rate code or
// channel change, its automatic revert, the command-loss return to the default radio, TXPWR,
// RADIOOPT, the RF-survey restore). The reconfiguration forgets the measured busy time (it is
// re-measured at the new settings), so the period computed afterwards is the NEW setting's:
// from a slow to a fast setting the next frame would launch at once, right after the last
// slow one - a cycle of almost 100 % duty (SX126x profile 0 -> 1 in ENCOUNTER: 99.9 %). The
// last frame at the old settings is owed the period computed from its own busy time:
// reconfig_hold_ms() keeps the longest period owed since the last launch (a second
// reconfiguration before it never shortens it), launch_spacing_ms() is the spacing the next
// launch respects; the hold ends with that launch (Flight::send_next_frame()).
inline uint32_t reconfig_hold_ms(uint32_t hold_ms, uint32_t period_before_ms) {
  return period_before_ms > hold_ms ? period_before_ms : hold_ms;
}
inline uint32_t launch_spacing_ms(uint32_t period_ms, uint32_t hold_ms) {
  return hold_ms > period_ms ? hold_ms : period_ms;
}

// Transmitter duty cycle (RF tx_duty_permille, sc_status duty_pct), averaged over WHOLE
// transmission cycles - from the launch of one transmission to the launch of the next -
// instead of a fixed 60 s window. A fixed window that is not a multiple of the frame
// period swings with the phase: with the SX126x SF12 busy times (17.3 s at profile 1,
// 34.6 s at profile 0) a 60 s window holds one or two transmissions and reads 28.9 % or
// 57.7 % for a true 50 %, a false ground YELLOW (>= 55 %). Each closed cycle lasts at least
// frame_period_ms() of the busy time measured before it, which is >= busy / 0.5
// (kMaxTxDutyPermille), so the average exceeds 50 % only if the flight's own frame
// spacing did. The cycle still open (latest transmission up to now) counts with
// max(elapsed, the spacing in force - Flight::tx_period_ms(), which includes the hold of
// launch_spacing_ms() after a radio reconfiguration), so the value never jumps up when a
// transmission ends and decays towards 0 while the transmitter is silent (TXINHIBIT,
// hibernation).
// Cycles launched more than kWindowMs ago are dropped (the newest one always counts).
class TxDutyMeter {
 public:
  static const uint8_t kCycles = 16;            // at most this many cycles are averaged
  static const uint32_t kWindowMs = 300000u;    // >= 2 cycles of SF12 profile 0 in SAFE (4 x 34.6 s)
  void clear() { head_ = count_ = 0; }
  // One finished transmission (TX_DONE / TX_TIMEOUT): launched at start_ms, busy busy_ms.
  void on_tx(uint32_t start_ms, uint32_t busy_ms);
  // Permille at `now`. tx_active: a transmission launched at active_start_ms is on the air
  // and closes the newest recorded cycle; otherwise that cycle is open and lasts
  // max(now - its launch, open_period_ms). 0 before the first transmission; <= 1000.
  uint16_t permille(uint32_t now, bool tx_active, uint32_t active_start_ms, uint32_t open_period_ms) const;

 private:
  uint32_t start_[kCycles] = {0};
  uint32_t busy_[kCycles] = {0};
  uint8_t head_ = 0;                            // next slot to write
  uint8_t count_ = 0;                           // valid slots (<= kCycles)
};
struct LoadPlan {
  uint16_t stretch_x100;       // >= 100; effective period = ceil(period x stretch / 100), <= 65535 s
  uint32_t evr_milli_per_s;    // routine-EVR refill, 1000 = one EVR per second
  uint32_t capacity_octets_per_min;
};
// period_s = the periods in force (table or PKTRATE override, 0 = off); capacity C =
// data_field_octets / frame_period; load L = sum(kPeriodicPktLen / period). stretch =
// max(100, ceil(100 x L / (0.75 x C))), saturating at 65535; evr = min(kEvrMaxMilliPerS,
// (0.95 x C - L x 100 / stretch) x 1000 / kEvrNominalOctets), which is at least
// min(kEvrMaxMilliPerS, 0.20 x C x 1000 / 80) by construction.
// Integer arithmetic only (octets x 1000 / ms); no float. A null period_s, frame period 0
// or data field 0 gives no capacity: stretch 65535 when anything is enabled (else 100), EVR 0.
LoadPlan plan_downlink_load(const uint16_t period_s[kNumPeriodic], uint32_t frame_period_ms,
                            uint16_t data_field_octets);
// The period the executive applies: ceil(period_s x stretch_x100 / 100), saturating at
// 65535 s; 0 (off) stays 0.
uint16_t stretched_period_s(uint16_t period_s, uint16_t stretch_x100);

}  // namespace vgq
