// =============================================================================
//  Solid-State Recorder (SSR) — onboard packet store (the role tape recorders
//  played on 1970s probes). Every generated TM packet is recorded; on command the recorder is
//  "played back" on VC2 oldest-first. When full, the oldest packets are
//  overwritten (and counted) — like an endless-loop tape.
//  RAM based, allocated once at boot from the heap. On the UNO Q the Zephyr
//  loader gives the sketch's malloc all remaining SRAM of the STM32U585 (786 KB),
//  so begin() takes the largest power of two up to kSsrMaxBytes that still leaves
//  a reserve for the Bridge / String / MsgPack allocations (256 KiB = ~12 h of
//  CRUISE telemetry). Contents are lost on reset; the Linux EGSE keeps the
//  permanent archive. Pure C++ (host-testable).
// =============================================================================
#pragma once
#include <stdint.h>
#include <stddef.h>

namespace vgq {

class Ssr {
 public:
  static const size_t kFallbackSize = 4096;   // static buffer if the heap is exhausted

  // Allocates the recorder: the largest power of two in [min_bytes, max_bytes]
  // after which `reserve` more bytes can still be allocated. Returns the size.
  size_t begin(size_t max_bytes, size_t min_bytes, size_t reserve);
  size_t capacity() const { return size_; }
  void clear();
  void record(const uint8_t* pkt, uint16_t len);
  // Playback control: snapshot the current contents and stream them out.
  void start_playback();
  void stop_playback() { playing_ = false; }
  bool playing() const { return playing_; }
  // Copies the next recorded packet into `out` (cap octets); returns its length,
  // or 0 when playback has finished (playback then stops automatically).
  uint16_t next_playback(uint8_t* out, size_t cap);

  uint16_t fill_permille() const { return (uint16_t)(((uint64_t)used_ * 1000u) / size_); }
  uint32_t dropped() const { return dropped_; }
  uint32_t packets() const { return count_; }

 private:
  uint8_t at(uint32_t i) const { return buf_[i & mask_]; }
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
};

}  // namespace vgq
