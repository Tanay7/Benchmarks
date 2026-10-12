// =============================================================================
//  Solid-State Recorder (SSR) - onboard packet store (the role tape recorders
//  played on 1970s probes). Every generated TM packet is recorded (CFDP PDUs
//  excepted: they are copies of files that already exist); on command the recorder
//  is "played back" on VC2 oldest-first, or exported as ONE file over CFDP on VC3.
//  When full, the oldest packets are overwritten (and counted) - like an endless-
//  loop tape.
//
//  RAM based, allocated once at boot from the heap. On the UNO Q the Zephyr
//  loader gives the sketch's malloc all remaining SRAM of the STM32U585 (786 KB),
//  so begin() takes the largest power of two up to kSsrMaxBytes that still leaves
//  a reserve for the Bridge / String / MsgPack allocations (256 KiB = ~12 h of
//  CRUISE telemetry). Contents are lost on reset; the Linux EGSE keeps the
//  permanent archive.
//
//  Export (CFDPPUT 0, contract 7, PB-2.5): begin_export() freezes the recorded
//  snapshot [head, tail) and PAUSES recording - a packet that arrives meanwhile is
//  not stored and is counted in paused_drops() (C-06: overwrite drops stay in
//  dropped(), which feeds the beacon; HK ssr_dropped reports their saturated sum).
//  The file is the concatenation of the recorded Space Packets in playback order
//  (each self-delimiting through its primary header), without the recorder's
//  2-octet length prefixes. Playback and export exclude each other.
//
//  Pure C++ (host-testable), no recursion, every loop bounded by the buffer size.
// =============================================================================
#pragma once
#include <stddef.h>
#include <stdint.h>
#include "../ccsds/cfdp.h"

namespace vgq {

class Ssr {
 public:
  static const size_t kFallbackSize = 4096;   // static buffer if the heap is exhausted

  // Allocates the recorder: the largest power of two in [min_bytes, max_bytes]
  // after which `reserve` more bytes can still be allocated. Returns the size.
  size_t begin(size_t max_bytes, size_t min_bytes, size_t reserve);
  size_t capacity() const { return size_; }
  // Empties the recorder and ends playback and any export (the executive refuses
  // SSRCLEAR while exporting; this is the defensive backstop - a CFDP sender reading
  // an ended export gets 0 octets and closes with condition 4).
  void clear();
  // Records one packet (len 1..65535; a null or empty packet is ignored). While
  // exporting nothing is stored and paused_drops() counts it; a packet longer than
  // the recorder is ignored.
  void record(const uint8_t* pkt, uint16_t len);

  // ---- Playback (VC2) ----
  // Snapshots the current contents and streams them out; false (nothing changes)
  // while an export runs. Starting on an empty recorder returns true and ends at once.
  bool start_playback();
  void stop_playback() { playing_ = false; }
  bool playing() const { return playing_; }
  // Copies the next recorded packet into `out` (cap octets); returns its length,
  // or 0 when playback has finished (playback then stops automatically).
  uint16_t next_playback(uint8_t* out, size_t cap);

  // ---- Export (CFDPPUT 0) ----
  // Freezes [head, tail) and pauses recording; false while playing or exporting.
  bool begin_export();
  void end_export();                         // resumes recording (no-op when not exporting)
  bool exporting() const { return exporting_; }
  // Sum of the recorded packet lengths (no length prefixes) = the export file size.
  uint32_t export_size() const { return (uint32_t)(used_ - 2u * count_); }
  // Sequential reader of the frozen snapshot as concatenated Space Packets: `off`
  // must equal the octets delivered so far, else 0 (the CFDP sender then ends with
  // condition 4). Copies at most n octets; never writes beyond dst[n-1]. 0 when not
  // exporting, at the end of the file, or for a null dst.
  size_t export_read(uint32_t off, uint8_t* dst, size_t n);
  uint32_t paused_drops() const { return paused_drops_; }

  uint16_t fill_permille() const { return (uint16_t)(((uint64_t)used_ * 1000u) / size_); }
  uint32_t dropped() const { return dropped_; }      // overwrite drops only (C-06)
  uint32_t packets() const { return count_; }

 private:
  uint8_t at(uint32_t i) const { return buf_[i & mask_]; }
  uint16_t len_at(uint32_t i) const { return (uint16_t)((at(i) << 8) | at(i + 1)); }
  void drop_oldest();
  uint8_t fallback_[kFallbackSize];
  uint8_t* buf_ = fallback_;
  size_t size_ = kFallbackSize;
  uint32_t mask_ = kFallbackSize - 1;
  uint32_t head_ = 0;     // absolute index of oldest record
  uint32_t tail_ = 0;     // absolute index of next write
  uint32_t used_ = 0;
  uint32_t count_ = 0;
  uint32_t dropped_ = 0;
  bool playing_ = false;
  uint32_t pb_ = 0, pb_end_ = 0;
  // Export cursor: ring index of the next unread packet octet (ex_left_ > 0) or of the
  // next record header (ex_left_ == 0), octets delivered so far, octets left in the
  // current record.
  bool exporting_ = false;
  uint32_t ex_pos_ = 0, ex_off_ = 0, ex_left_ = 0;
  uint32_t paused_drops_ = 0;
};

// CFDP file source over the SSR export (CfdpSender, ccsds/cfdp.h). The executive owns
// one, bound to its recorder, and passes it to CfdpSender::begin() after begin_export().
class SsrFileSource : public ccsds::CfdpSource {
 public:
  explicit SsrFileSource(Ssr& s) : s_(s) {}
  uint32_t size() override { return s_.export_size(); }
  size_t read(uint32_t off, uint8_t* dst, size_t n) override { return s_.export_read(off, dst, n); }

 private:
  Ssr& s_;
};

}  // namespace vgq
