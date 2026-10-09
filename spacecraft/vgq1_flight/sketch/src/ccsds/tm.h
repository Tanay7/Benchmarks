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
// =============================================================================
#pragma once
#include "ccsds_types.h"

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
  // Removes `n` octets into dst and returns the FHP for that data field.
  uint16_t pop_field(uint8_t* dst, size_t n);
  uint32_t overflows() const { return overflows_; }

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
class TmFramer {
 public:
  void init(uint16_t scid);
  // Builds a 200-octet frame for `vc` from `stream`, padding with an idle packet
  // when the stream holds less than a full data field. `idle_seq` is the
  // running sequence counter for APID 0x7FF (incremented when used).
  void build_frame(uint8_t vc, VcStream& stream, const Clcw& clcw, uint16_t& idle_seq,
                   uint8_t frame[kTmFrameLen]);
  // Builds an Only-Idle-Data frame on VC7 (FHP = 0x7FE).
  void build_oid_frame(const Clcw& clcw, uint8_t frame[kTmFrameLen]);
  uint8_t mcfc() const { return mcfc_; }

 private:
  void header(uint8_t* f, uint8_t vc, uint16_t fhp);
  void trailer(uint8_t* f, const Clcw& clcw);
  uint16_t scid_ = kSpacecraftId;
  uint8_t mcfc_ = 0;
  uint8_t vcfc_[8] = {0};
};

// Channel coding: ASM + RS(255,223) shortened codeblock + randomization.
void build_cadu(const uint8_t frame[kTmFrameLen], uint8_t cadu[kCaduLen]);

}  // namespace ccsds
