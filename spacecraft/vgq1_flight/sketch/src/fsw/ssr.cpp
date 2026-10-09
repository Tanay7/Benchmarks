#include "ssr.h"

namespace vgq {

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
  if (need > kSize) return;
  while (kSize - used_ < need) drop_oldest();
  buf_[tail_ & (kSize - 1)] = (uint8_t)(len >> 8);
  buf_[(tail_ + 1) & (kSize - 1)] = (uint8_t)len;
  for (uint16_t i = 0; i < len; ++i) buf_[(tail_ + 2 + i) & (kSize - 1)] = pkt[i];
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
