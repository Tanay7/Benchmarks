// =============================================================================
//  Solid-State Recorder implementation (see ssr.h).
//
//  Ring buffer of records [len_hi][len_lo][packet ...] addressed by absolute
//  32-bit octet indices (head_ = oldest record, tail_ = next write); the buffer
//  size is a power of two, so index & mask_ is the ring position and the indices
//  may wrap modulo 2^32 freely. Every loop below is bounded by the buffer size or
//  by the record count.
// =============================================================================
#include "ssr.h"
#include <stdlib.h>

namespace vgq {

size_t Ssr::begin(size_t max_bytes, size_t min_bytes, size_t reserve) {
  // Largest power of two <= max_bytes (at most log2(SIZE_MAX) doublings).
  size_t sz = kFallbackSize;
  while (sz <= max_bytes / 2) sz *= 2;
  for (; sz >= min_bytes && sz > kFallbackSize; sz >>= 1) {
    uint8_t* p = static_cast<uint8_t*>(malloc(sz));
    if (!p) continue;
    void* probe = reserve ? malloc(reserve) : nullptr;   // keep headroom for everyone else
    if (reserve && !probe) { free(p); continue; }
    free(probe);
    if (buf_ != fallback_) free(buf_);
    buf_ = p;
    size_ = sz;
    mask_ = (uint32_t)(sz - 1);
    break;
  }
  clear();
  return size_;
}

void Ssr::clear() {
  head_ = tail_ = used_ = count_ = 0;
  playing_ = false;
  pb_ = pb_end_ = 0;
  exporting_ = false;                          // a running export ends: export_read() -> 0
  ex_pos_ = ex_off_ = ex_left_ = 0;
}

void Ssr::drop_oldest() {
  const uint16_t len = len_at(head_);
  head_ += 2u + len;
  used_ -= 2u + len;
  --count_;
  ++dropped_;
  // A playback cursor pointing at overwritten data skips forward.
  if (playing_ && (int32_t)(pb_ - head_) < 0) pb_ = head_;
}

void Ssr::record(const uint8_t* pkt, uint16_t len) {
  if (pkt == nullptr || len == 0) return;
  if (exporting_) {                            // the exported snapshot must not change (C-06)
    ++paused_drops_;
    return;
  }
  const uint32_t need = 2u + len;
  if (need > size_) return;
  // Each drop frees >= 2 octets, so at most count_ iterations.
  while (size_ - used_ < need && count_ > 0) drop_oldest();
  if (size_ - used_ < need) return;            // defensive: cannot happen once empty
  buf_[tail_ & mask_] = (uint8_t)(len >> 8);
  buf_[(tail_ + 1u) & mask_] = (uint8_t)len;
  for (uint32_t i = 0; i < len; ++i) buf_[(tail_ + 2u + i) & mask_] = pkt[i];
  tail_ += need;
  used_ += need;
  ++count_;
}

bool Ssr::start_playback() {
  if (exporting_) return false;
  pb_ = head_;
  pb_end_ = tail_;
  playing_ = (pb_ != pb_end_);
  return true;
}

uint16_t Ssr::next_playback(uint8_t* out, size_t cap) {
  if (!playing_ || out == nullptr) return 0;
  if ((int32_t)(pb_ - head_) < 0) pb_ = head_;
  if ((int32_t)(pb_end_ - pb_) <= 0) { playing_ = false; return 0; }
  const uint16_t len = len_at(pb_);
  if (len > cap) { playing_ = false; return 0; }
  for (uint32_t i = 0; i < len; ++i) out[i] = at(pb_ + 2u + i);
  pb_ += 2u + len;
  return len;
}

bool Ssr::begin_export() {
  if (playing_ || exporting_) return false;
  exporting_ = true;
  ex_pos_ = head_;                             // first record header
  ex_off_ = 0;
  ex_left_ = 0;
  return true;
}

void Ssr::end_export() {
  exporting_ = false;
  ex_pos_ = ex_off_ = ex_left_ = 0;
}

size_t Ssr::export_read(uint32_t off, uint8_t* dst, size_t n) {
  if (!exporting_ || dst == nullptr || n == 0 || off != ex_off_) return 0;
  size_t k = 0;
  // Each pass either opens one record or copies the rest of the current one (or the
  // part that fits), so 2 x count_ + 1 passes always suffice.
  for (uint32_t pass = 0; pass <= 2u * count_ && k < n; ++pass) {
    if (ex_left_ == 0) {
      if ((int32_t)(tail_ - ex_pos_) <= 0) break;            // end of the file
      ex_left_ = len_at(ex_pos_);
      ex_pos_ += 2u;
      continue;
    }
    size_t chunk = n - k;
    if (chunk > ex_left_) chunk = ex_left_;
    for (size_t i = 0; i < chunk; ++i) dst[k + i] = at(ex_pos_ + (uint32_t)i);
    ex_pos_ += (uint32_t)chunk;
    ex_left_ -= (uint32_t)chunk;
    k += chunk;
  }
  ex_off_ += (uint32_t)k;
  return k;
}

}  // namespace vgq
