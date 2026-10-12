// =============================================================================
//  TM path: Space Packets -> Virtual Channel streams -> Transfer Frames -> CADU
//
//    Space Packet (133.0-B-2)       6-octet primary header + 6-octet CUC time
//    VC packet stream               per-VC byte FIFO; packets may SPAN frames
//    TM Transfer Frame (132.0-B-3)  200 octets: hdr 6 | data 188 | CLCW 4 | FECF 2
//    CADU (131.0-B)                 ASM 4 | randomized RS codeblock 232
//
//  The multiplexer follows the standard packet-service rules: a frame's data
//  field is always completely filled; when a VC must be flushed with less than a
//  frame's worth of packets the remainder is padded with an IDLE packet
//  (APID 0x7FF), which itself may span into the next frame of the same VC. The
//  First Header Pointer (FHP) marks the first packet header in each data field.
//
//  The framer can instead emit USLP frames (732.1-B-2, see uslp.h) of the same
//  200-octet length on the same CADU: TFPH 8 | TFDF hdr 3 | TFDZ 183 | CLCW | FECF.
//  Either format can carry SDLS (355.0-B-2) through a FrameSecurity: the data
//  field shrinks by the security header + trailer, protect() runs once the
//  header and plaintext are in place, and the OCF/FECF are written afterwards.
// =============================================================================
#pragma once
#include "ccsds_types.h"
#include "frame_security.h"

namespace ccsds {

struct CucTime {
  uint32_t coarse;   // seconds since the SCLK epoch (see fsw/sclk.h)
  uint16_t fine;     // units of 2^-16 s
};

// Writes a complete TM Space Packet (primary + CUC secondary header + payload).
// Returns total packet length, or 0 if it does not fit in `cap`.
size_t build_tm_packet(uint8_t* out, size_t cap, uint16_t apid, uint16_t seq_count,
                       const CucTime& t, const uint8_t* payload, size_t payload_len);

// Writes an idle packet of exactly `total_len` octets (>= 7), no secondary header.
size_t build_idle_packet(uint8_t* out, size_t total_len, uint16_t seq_count);

// CLCW (Communications Link Control Word), 232.0-B-4 §4.2 / 232.1-B-2.
struct Clcw {
  uint8_t vcid = 0;
  bool no_rf_available = false;
  bool no_bit_lock = false;
  bool lockout = false;
  bool wait = false;
  bool retransmit = false;
  uint8_t farm_b_counter = 0;   // 2 bits
  uint8_t report_value = 0;     // V(R)
  uint32_t pack() const;
};

// ---------------------------------------------------------------------------
// One virtual channel's packet stream (ring buffer of octets + packet starts).
// ---------------------------------------------------------------------------
class VcStream {
 public:
  static const size_t kBufLen = 1024;   // power of two
  static const size_t kMaxStarts = 64;  // power of two

  void reset();
  // Queues a complete packet. Returns false (and queues nothing) if full.
  bool push(const uint8_t* pkt, size_t len, bool is_idle = false);
  size_t bytes() const { return (size_t)(in_ - out_); }
  size_t free_bytes() const { return kBufLen - bytes(); }
  // True when real (non-idle) packet bytes are waiting to be sent.
  bool has_real_data() const { return (int32_t)(last_real_end_ - out_) > 0; }
  // Removes `n` octets into dst and returns the FHP for that data field
  // (kFhpNoPacketStart when no packet starts in it).
  uint16_t pop_field(uint8_t* dst, size_t n);
  uint32_t overflows() const { return overflows_; }
  // Read-position snapshot so a framer can undo pop_field() when the frame it
  // built is not sent (SDLS refused it). Only valid while nothing was pushed
  // since mark(): a push may reuse the octets that pop_field() released.
  struct Mark { uint32_t out, s_out; };
  Mark mark() const { return Mark{out_, s_out_}; }
  void rewind(const Mark& m) { out_ = m.out; s_out_ = m.s_out; }

 private:
  uint8_t buf_[kBufLen];
  uint32_t starts_[kMaxStarts];
  uint32_t in_ = 0, out_ = 0;          // absolute octet indices (mod 2^32)
  uint32_t s_in_ = 0, s_out_ = 0;      // start-marker ring indices
  uint32_t last_real_end_ = 0;
  uint32_t overflows_ = 0;
};

// ---------------------------------------------------------------------------
// Transfer Frame generator for one physical channel.
// ---------------------------------------------------------------------------
// Downlink frame format; the values are those of config.h VGQ_DOWNLINK_FRAMING.
enum Framing : uint8_t { FRAMING_TM = 0, FRAMING_USLP = 1 };

class TmFramer {
 public:
  // Resets all frame counters. The format holds for the whole session: the
  // ground detects it per frame, but each VC's frame count must stay continuous.
  void init(uint16_t scid, Framing framing = FRAMING_TM);
  Framing framing() const { return framing_; }

  // Packet-data octets one data-VC frame carries: TM 188 / USLP 183, minus the
  // SDLS header + trailer of `sec` (TM AE 158, USLP AE 153). 0 if they do not fit.
  size_t data_len(const FrameSecurity* sec = nullptr) const;

  // Builds a 200-octet frame for `vc` (TM 0..6; USLP VCID 0..62) from `stream`,
  // padding with an idle packet when the stream holds less than a full data
  // field. `idle_seq` is the running sequence counter for APID 0x7FF
  // (incremented when used). With `sec` the data field is protected (see the
  // file header). Returns false when sec->protect() refuses: the stream and the
  // frame counters are then rolled back (no packet octet is lost, no counter
  // gap appears) and `frame` must not be sent.
  bool build_frame(uint8_t vc, VcStream& stream, const Clcw& clcw, uint16_t& idle_seq,
                   uint8_t frame[kTmFrameLen], FrameSecurity* sec = nullptr);

  // Builds an Only-Idle-Data frame, never protected: TM VC7 with FHP 0x7FE, or
  // USLP VCID 63 with UPID 31 (idle) and FHP 0xFFFF. The idle data repeats
  // `idle` (`idle_len` octets, e.g. "DE <callsign> VGQ-1 ") from the first octet
  // of the data field; without a pattern it is 0x55 fill.
  void build_oid_frame(const Clcw& clcw, uint8_t frame[kTmFrameLen],
                       const uint8_t* idle = nullptr, size_t idle_len = 0);

  // Next TM Master Channel Frame Count (kept in USLP mode too, where no frame
  // carries it — USLP has no master-channel counter).
  uint8_t mcfc() const { return mcfc_; }
  // The u8 counter the ground sees in the last frame built: TM its MCFC, USLP
  // its VC frame count. TIMECORR.ref_mcfc = this value of the reference frame;
  // in USLP mode the reference frame must be a VC0 frame (docs/04).
  uint8_t last_count() const { return last_count_; }
  // VCID of the last frame built (TM OID 7, USLP OID 63).
  uint8_t last_vcid() const { return last_vcid_; }

 private:
  void header(uint8_t* f, uint8_t vc, uint16_t fhp);
  size_t uslp_header(uint8_t* f, uint8_t vcid, uint8_t upid, uint16_t fhp, size_t sec_hdr_len);
  void trailer(uint8_t* f, const Clcw& clcw);
  void commit(uint8_t vcid);
  uint16_t scid_ = kSpacecraftId;
  Framing framing_ = FRAMING_TM;
  uint8_t mcfc_ = 0;
  uint8_t vcfc_[64] = {0};      // per VCID (TM uses 0..7)
  uint8_t last_count_ = 0, last_vcid_ = 0;
};

// Fills `n` octets with repetitions of `pattern` (0x55 fill when it is empty).
void idle_fill(uint8_t* dst, size_t n, const uint8_t* pattern, size_t pattern_len);

// Channel coding: ASM + RS(255,223) shortened codeblock + randomization.
void build_cadu(const uint8_t frame[kTmFrameLen], uint8_t cadu[kCaduLen]);

}  // namespace ccsds
