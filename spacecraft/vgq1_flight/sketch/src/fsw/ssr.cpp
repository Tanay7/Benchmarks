#include "ssr.h"
#include <stdlib.h>

namespace vgq {

size_t Ssr::begin(size_t max_bytes, size_t min_bytes, size_t reserve) {
  size_t sz = kFallbackSize;
  while (sz * 2 <= max_bytes) sz *= 2;              // largest power of two <= max_bytes
  for (; sz >= min_bytes && sz > kFallbackSize; sz >>= 1) {
    uint8_t* p = static_cast<uint8_t*>(malloc(sz));
    if (!p) continue;
    void* probe = reserve ? malloc(reserve) : nullptr;  // keep headroom for everyone else
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

// Record format: [len_hi][len_lo][packet ...]
void Ssr::clear() {
  head_ = tail_ = used_ = count_ = 0;
  playing_ = false;
}

void Ssr::drop_oldest() {
  const uint16_t len = (uint16_t)((at(head_) << 8) | at(head_ + 1));
  head_ += 2u + len;
  used_ -= 2u + len;
  --count_;
  ++dropped_;
  // A playback cursor pointing at overwritten data skips forward.
  if (playing_ && (int32_t)(pb_ - head_) < 0) pb_ = head_;
}

void Ssr::record(const uint8_t* pkt, uint16_t len) {
  const uint32_t need = 2u + len;
  if (need > size_) return;
  while (size_ - used_ < need) drop_oldest();
  buf_[tail_ & mask_] = (uint8_t)(len >> 8);
  buf_[(tail_ + 1) & mask_] = (uint8_t)len;
  for (uint16_t i = 0; i < len; ++i) buf_[(tail_ + 2 + i) & mask_] = pkt[i];
  tail_ += need;
  used_ += need;
  ++count_;
}

void Ssr::start_playback() {
  pb_ = head_;
  pb_end_ = tail_;
  playing_ = (pb_ != pb_end_);
}

uint16_t Ssr::next_playback(uint8_t* out, size_t cap) {
  if (!playing_) return 0;
  if ((int32_t)(pb_ - head_) < 0) pb_ = head_;
  if ((int32_t)(pb_end_ - pb_) <= 0) { playing_ = false; return 0; }
  const uint16_t len = (uint16_t)((at(pb_) << 8) | at(pb_ + 1));
  if (len > cap) { playing_ = false; return 0; }
  for (uint16_t i = 0; i < len; ++i) out[i] = at(pb_ + 2 + i);
  pb_ += 2u + len;
  return len;
}

}  // namespace vgq
