// =============================================================================
//  VGQ-1 data-link core of the flight executive (see datalink.h). Pure C++,
//  host-tested by tools/host_test/test_integration.cpp, which also writes the
//  CADU streams and command vectors the ground cross-check decodes.
//
//  No Arduino, no heap, no recursion; every loop is bounded by a constant
//  (packet counts, buffer sizes, kNumPeriodic).
// =============================================================================
#include "datalink.h"
#include <string.h>
#include "../config.h"

namespace vgq {

using namespace ccsds;

// =============================================================================
// OID identification text
// =============================================================================
size_t format_idle_text(const char* callsign, char* out, size_t cap) {
  if (out == nullptr || cap == 0) return 0;
  // Assembled octet by octet (no printf): "DE " + callsign + " VGQ-1 ".
  static const char kPre[] = "DE ";
  static const char kPost[] = " VGQ-1 ";
  const size_t limit = (cap - 1u < kIdleTextMax) ? cap - 1u : kIdleTextMax;
  size_t n = 0;
  for (size_t i = 0; kPre[i] != '\0' && n < limit; ++i) out[n++] = kPre[i];
  const bool has_call = callsign != nullptr && callsign[0] != '\0';
  if (has_call) {
    for (size_t i = 0; i < kIdleTextMax && callsign[i] != '\0' && n < limit; ++i) out[n++] = callsign[i];
  }
  // Without a callsign "DE " + "VGQ-1 " (skip the separator space).
  for (size_t i = has_call ? 0u : 1u; kPost[i] != '\0' && n < limit; ++i) out[n++] = kPost[i];
  out[n] = '\0';
  return n;
}

// =============================================================================
// Downlink
// =============================================================================
void Downlink::init(Framing framing, SdlsTm* sec, const char* idle_text) {
  framer_.init(kSpacecraftId, framing);
  for (uint8_t v = 0; v < kNumDataVcs; ++v) {
    vc_[v].reset();
    ovf_[v] = 0;
  }
  sec_ = sec;
  idle_len_ = 0;
  if (idle_text != nullptr) {
    while (idle_len_ < kIdleTextMax && idle_text[idle_len_] != '\0') {
      idle_[idle_len_] = (uint8_t)idle_text[idle_len_];
      ++idle_len_;
    }
  }
  idle_seq_ = 0;
  last_data_vc_ = 0xFF;
  oid_ = 0;
  refused_ = 0;
}

bool Downlink::push(uint8_t v, const uint8_t* pkt, size_t len, bool priority) {
  if (v >= kNumDataVcs || pkt == nullptr || len == 0) return false;
  // The last kVcReserveOctets of every VC buffer are kept for priority packets, so
  // routine traffic (periodic packets, INFO EVRs) can never crowd out a warning or a
  // command verification (PB-1.2).
  const size_t limit = priority ? VcStream::kBufLen : VcStream::kBufLen - kVcReserveOctets;
  if (vc_[v].bytes() + len > limit || !vc_[v].push(pkt, len)) {
    ++ovf_[v];
    return false;
  }
  return true;
}

size_t Downlink::data_field_len() const { return framer_.data_len(sec_); }

size_t Downlink::backlog(uint8_t v) const { return v < kNumDataVcs ? vc_[v].bytes() : 0; }

bool Downlink::has_real_data(uint8_t v) const {
  return v < kNumDataVcs && vc_[v].has_real_data();
}

void Downlink::reset_vc(uint8_t v) {
  if (v < kNumDataVcs) vc_[v].reset();          // ovf_[v] is kept (counted since init())
}

uint8_t Downlink::prepare_beacon(size_t hk_len, uint8_t max_frames) {
  const size_t dfl = data_field_len();
  if (max_frames == 0) max_frames = 1;
  vc_[VC_RT_SCI].reset();
  size_t n = vc_[VC_RT_ENG].bytes() + hk_len;
  if (dfl == 0 || n > (size_t)max_frames * dfl) {
    vc_[VC_RT_ENG].reset();
    n = hk_len;
  }
  const size_t frames = dfl ? (n + dfl - 1) / dfl : 1;
  return (uint8_t)(frames == 0 ? 1 : frames > max_frames ? max_frames : frames);
}

uint32_t Downlink::overflows(uint8_t v) const { return v < kNumDataVcs ? ovf_[v] : 0; }

void Downlink::next_cadu(const Clcw& clcw, uint8_t cadu[kCaduLen], DownlinkFrame& info) {
  // Nonce safety (contract 5): no VC0..3 octet is popped into a frame while the SDLS TM
  // protector waits for its epoch - popped data would be lost when protect() refused.
  const bool data_ok = (sec_ == nullptr) || sec_->ready();
  int vc = -1;
  if (data_ok) {
    // v2 rule: engineering first, but science goes first after an engineering frame
    // once it has backed up beyond kSciPriorityBacklog octets (anti-starvation).
    const bool sci_backlog = vc_[VC_RT_SCI].bytes() > kSciPriorityBacklog;
    if (vc_[VC_RT_ENG].has_real_data() && !(sci_backlog && last_data_vc_ == VC_RT_ENG))
      vc = VC_RT_ENG;
    else if (vc_[VC_RT_SCI].has_real_data())
      vc = VC_RT_SCI;
    else if (vc_[VC_RT_ENG].has_real_data())
      vc = VC_RT_ENG;
    else if (vc_[VC_PLAYBACK].has_real_data())
      vc = VC_PLAYBACK;
    else if (vc_[VC_FILE].has_real_data())
      vc = VC_FILE;
  }

  uint8_t security = DL_SEC_CLEAR;
  if (sec_ != nullptr && sec_->service() == SDLS_SVC_AE)
    security = sec_->state() == SdlsTm::TM_PROTECTED ? DL_SEC_AE : DL_SEC_NULL_SA;

  uint8_t frame[kTmFrameLen];
  bool data_frame = false;
  if (vc >= 0) {
    data_frame = framer_.build_frame((uint8_t)vc, vc_[vc], clcw, idle_seq_, frame, sec_);
    if (data_frame) last_data_vc_ = (uint8_t)vc;
    else            ++refused_;                 // backstop: the stream was rolled back
  }
  if (!data_frame) {
    framer_.build_oid_frame(clcw, frame, idle_, idle_len_);
    ++oid_;
  }
  // Describe the frame actually built (build_frame's own OID fallback included).
  const uint8_t oid_vcid = framer_.framing() == FRAMING_USLP ? kUslpVcidOid : (uint8_t)VC_IDLE;
  info.vcid = framer_.last_vcid();
  info.count = framer_.last_count();
  info.oid = info.vcid == oid_vcid;
  info.security = info.oid ? (uint8_t)DL_SEC_CLEAR : security;
  build_cadu(frame, cadu);
}

uint8_t Downlink::mcfc() const { return framer_.mcfc(); }
Framing Downlink::framing() const { return framer_.framing(); }
uint32_t Downlink::oid_frames() const { return oid_; }
uint32_t Downlink::refused() const { return refused_; }

// =============================================================================
// Uplink acceptance chain
// =============================================================================
const char* tc_parse_result_name(TcParseResult r) {
  static const char* const kName[] = {"OK",         "TOO_SHORT", "BAD_VERSION", "BAD_SCID",
                                      "BAD_LENGTH", "BAD_FECF",  "BAD_HEADER",  "BAD_TFDF"};
  return (unsigned)r < sizeof kName / sizeof kName[0] ? kName[r] : "?";
}

TcOutcome tc_accept(const uint8_t* in, size_t n, uint8_t* work, size_t work_cap, BchStats& bch,
                    Sdls& sdls, Farm1& farm) {
  TcOutcome o;
  if (in == nullptr || n == 0 || work == nullptr || work_cap < kTcWorkMin) return o;
  o.info_len = cltu_decode(in, n, work, work_cap, bch);
  if (o.info_len == 0) return o;                                  // NO_CLTU

  TcFrame f;
  const bool secured = sdls.enabled();
  // Under SDLS the USLP TFDF header is inside the protected region: open it afterwards.
  o.parse = tc_parse(work, o.info_len, f, !secured);
  if (o.parse != TC_OK) {
    o.stage = TcStage::FRAME_REJECTED;                            // `frame` stays default
    return o;
  }
  if (secured) {
    o.sdls = sdls.process(work, f);       // AE decrypts in place, only an authentic, fresh frame
    if (o.sdls != Sdls::SDLS_OK) {
      o.stage = TcStage::SDLS_REJECTED;
      o.frame = f;                                                // header fields only
      return o;
    }
    o.parse = tc_open_tfdf(f);
    if (o.parse != TC_OK) {
      o.stage = TcStage::TFDF_REJECTED;
      o.frame = f;
      return o;
    }
  }
  o.frame = f;
  o.verdict = farm.on_frame(f);
  o.stage = TcStage::FARM;
  return o;
}

// =============================================================================
// CFDP relay authorisation
// =============================================================================
void RelayGate::request(uint32_t seq, uint32_t now_ms) {
  if (state_ != NONE) return;
  state_ = REQUESTED;
  seq_ = seq;
  t_ms_ = now_ms;
}

bool RelayGate::cancel(uint32_t now_ms) {
  if (state_ != REQUESTED && state_ != ACTIVE) return false;
  state_ = CANCELLING;              // only the EGSE's EOF(cancel) is admitted from now on
  t_ms_ = now_ms;
  return true;
}

int RelayGate::admit(const uint8_t* pdu, size_t len, uint32_t now_ms) {
  if (state_ == NONE) return refuse();                           // nothing requested (or reboot)
  CfdpPdu p;
  if (!CfdpRelayMonitor::acceptable(pdu, len) || !cfdp_parse(pdu, len, p)) return refuse();
  if (p.hdr.seq != seq_) return refuse();                        // not the authorised transfer
  const bool eof = !p.hdr.file_data && p.directive == kCfdpDirEof;
  if (state_ == CANCELLING && !eof) return refuse();
  if (eof) {
    state_ = NONE;                     // the transfer (or its cancellation) is complete
  } else if (state_ == REQUESTED) {
    state_ = ACTIVE;                   // the EGSE started: CfdpRelayMonitor times it from now
  }
  t_ms_ = now_ms;
  return 1;
}

RelayGate::Event RelayGate::poll(uint32_t now_ms, uint32_t timeout_ms) {
  if (state_ != REQUESTED && state_ != CANCELLING) return EV_NONE;
  if ((uint32_t)(now_ms - t_ms_) < timeout_ms) return EV_NONE;   // wrap-safe difference
  const Event e = state_ == REQUESTED ? EV_START_TIMEOUT : EV_CANCEL_TIMEOUT;
  state_ = NONE;
  return e;
}

void RelayGate::reset() { state_ = NONE; }     // seq() keeps the last transfer for HK

// =============================================================================
// Routine-EVR budget
// =============================================================================
void EvrGate::refill(uint32_t elapsed_ms, uint32_t milli_per_s) {
  // elapsed [ms] x rate [milli-EVR/s] is the refill in units of 1/1000 milli-EVR; the
  // remainder below one milli-EVR carries over, so a slow rate is not lost to truncation
  // when refill() is called often (167 milli-EVR/s refilled every 10 ms = 1.67 milli-EVR).
  const uint64_t acc = (uint64_t)elapsed_ms * milli_per_s + rem_;
  const uint64_t t = (uint64_t)tokens_ + acc / 1000u;
  if (t >= kEvrBucketMilli) {
    tokens_ = kEvrBucketMilli;
    rem_ = 0;
  } else {
    tokens_ = (uint32_t)t;
    rem_ = (uint32_t)(acc % 1000u);
  }
}

void EvrGate::fill() {
  tokens_ = kEvrBucketMilli;
  rem_ = 0;
}

bool EvrGate::admit(uint8_t severity, bool exempt) {
  if (severity >= 3 || exempt) return true;     // errors are never throttled
  if (tokens_ >= 1000u) {
    tokens_ -= 1000u;
    return true;
  }
  ++throttled_;
  return false;
}

// =============================================================================
// Downlink load control (PB-3.3, C-27)
// =============================================================================
// Packet generation periods in seconds (0 = off) - the v2 cadence table, unchanged.
//                                            HK   RF  TCORR  MAG  ATT  SPEC  ENV
const uint16_t kModeCadence[5][kNumPeriodic] = {
    /* BOOT      */ {  10,  10,    0,    0,   0,    0,    0},
    /* SAFE      */ {  30,  60,  300,    0,   0,    0,    0},
    /* CRUISE    */ {  60,  60,  300,   30,  60,  300,  120},
    /* ENCOUNTER */ {  20,  30,  120,    4,   8,   30,   30},
    /* TEST      */ {  10,  10,   60,    4,   8,   30,   30},
};
// Frame period as a multiple of the measured radio busy time (x10), per mode (v2).
const uint8_t kModePeriodX10[5] = {40, 40, 25, 20, 20};
// Whole Space Packets: 6 + 6 header octets + user data.
const uint16_t kPeriodicPktLen[kNumPeriodic] = {
    (uint16_t)(kSpPriHdrLen + kSpSecHdrLen + kHkLen),        // 90
    (uint16_t)(kSpPriHdrLen + kSpSecHdrLen + kRfLen),        // 61
    (uint16_t)(kSpPriHdrLen + kSpSecHdrLen + kTimeCorrLen),  // 22
    (uint16_t)(kSpPriHdrLen + kSpSecHdrLen + kMagLen),       // 58
    (uint16_t)(kSpPriHdrLen + kSpSecHdrLen + kAttLen),       // 49
    (uint16_t)(kSpPriHdrLen + kSpSecHdrLen + kSpecV31Len),   // 141
    (uint16_t)(kSpPriHdrLen + kSpSecHdrLen + kEnvLen),       // 36
};
static_assert(kSpPriHdrLen + kSpSecHdrLen + kHkLen == 90 && kSpPriHdrLen + kSpSecHdrLen + kRfLen == 61 &&
              kSpPriHdrLen + kSpSecHdrLen + kSpecV31Len == 141 && kSpPriHdrLen + kSpSecHdrLen + kEnvLen == 36,
              "periodic packet lengths (PB-3.3)");

uint32_t frame_period_ms(uint32_t busy_ms, uint8_t mode, bool pa_overtemp, uint16_t cmd_period_ms) {
  const uint8_t m = mode <= MODE_TEST ? mode : (uint8_t)MODE_SAFE;
  uint64_t p = (uint64_t)busy_ms * kModePeriodX10[m] / 10u;
  if (pa_overtemp) p *= 2u;                                        // thermal back-off (T37S)
  const uint64_t duty_floor = (uint64_t)busy_ms * 1000u / kMaxTxDutyPermille;
  if (p < duty_floor) p = duty_floor;
  if (p < kMinFramePeriodMs) p = kMinFramePeriodMs;
  if (p < cmd_period_ms) p = cmd_period_ms;
  return p > 0xFFFFFFFFull ? (uint32_t)0xFFFFFFFFu : (uint32_t)p;
}

LoadPlan plan_downlink_load(const uint16_t period_s[kNumPeriodic], uint32_t fp_ms,
                            uint16_t data_field_octets) {
  LoadPlan plan;
  plan.stretch_x100 = 100;
  plan.evr_milli_per_s = 0;
  plan.capacity_octets_per_min = 0;
  // Periodic load L in micro-octets per second, each term rounded UP (never under-
  // estimated). Fine units are needed: PB-3.3's reference case (SX126x profile 1, SAFE,
  // USLP + AE) is 246.9 % and must give 247, which octet-level rounding misses.
  uint64_t load = 0;
  bool any = false;
  if (period_s != nullptr) {
    for (uint8_t i = 0; i < kNumPeriodic; ++i) {
      if (period_s[i] == 0) continue;
      any = true;
      load += ((uint64_t)kPeriodicPktLen[i] * 1000000u + period_s[i] - 1u) / period_s[i];
    }
  }
  // Capacity C in micro-octets per second, rounded DOWN (never over-estimated).
  const uint64_t cap = (fp_ms == 0) ? 0 : (uint64_t)data_field_octets * 1000000000ull / fp_ms;
  if (period_s == nullptr || cap == 0) {
    plan.stretch_x100 = any ? 65535 : 100;      // no capacity: stretch to the limit
    return plan;
  }
  plan.capacity_octets_per_min = (uint32_t)((uint64_t)data_field_octets * 60000u / fp_ms);
  // stretch = max(100, ceil(100 L / (0.75 C))), saturating at 65535.
  const uint64_t num = load * 100u * 1000u;
  const uint64_t den = cap * kPeriodicLoadPermille;
  uint64_t s = (num + den - 1u) / den;
  if (s < 100u) s = 100u;
  if (s > 65535u) s = 65535u;
  plan.stretch_x100 = (uint16_t)s;
  // Routine EVRs: what is left of 95 % of the capacity after the stretched periodic load
  // (rounded up), at kEvrNominalOctets per EVR, never above the v2 10 per minute.
  const uint64_t use = cap * kLinkUsePermille / 1000u;
  const uint64_t periodic = (load * 100u + s - 1u) / s;
  const uint64_t rest = use > periodic ? use - periodic : 0u;
  uint64_t evr = rest / ((uint64_t)kEvrNominalOctets * 1000u);    // milli-EVR per second
  if (evr > kEvrMaxMilliPerS) evr = kEvrMaxMilliPerS;
  plan.evr_milli_per_s = (uint32_t)evr;
  return plan;
}

uint16_t stretched_period_s(uint16_t period_s, uint16_t stretch_x100) {
  if (period_s == 0) return 0;
  const uint32_t s = stretch_x100 < 100 ? 100u : stretch_x100;
  const uint32_t p = ((uint32_t)period_s * s + 99u) / 100u;      // ceil
  return p > 65535u ? (uint16_t)65535u : (uint16_t)p;
}

}  // namespace vgq
