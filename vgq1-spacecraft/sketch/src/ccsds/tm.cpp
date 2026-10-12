#include "tm.h"
#include "crc16.h"
#include "randomizer.h"
#include "reed_solomon.h"
#include "uslp.h"
#include <string.h>

namespace ccsds {

// ---------------------------------------------------------------------------
// Space Packets
// ---------------------------------------------------------------------------
static void sp_primary(uint8_t* p, bool tc, bool sec_hdr, uint16_t apid, uint16_t seq,
                       size_t data_field_len) {
  // Version 000 | Type | SecHdrFlag | APID(11) | SeqFlags=11 (unsegmented) | Count(14)
  const uint16_t w0 = (uint16_t)((0u << 13) | ((tc ? 1u : 0u) << 12) |
                                 ((sec_hdr ? 1u : 0u) << 11) | (apid & 0x7FF));
  const uint16_t w1 = (uint16_t)((0x3u << 14) | (seq & 0x3FFF));
  put_u16(p + 0, w0);
  put_u16(p + 2, w1);
  put_u16(p + 4, (uint16_t)(data_field_len - 1));  // Packet Data Length = octets - 1
}

size_t build_tm_packet(uint8_t* out, size_t cap, uint16_t apid, uint16_t seq_count,
                       const CucTime& t, const uint8_t* payload, size_t payload_len) {
  const size_t data_field = kSpSecHdrLen + payload_len;
  const size_t total = kSpPriHdrLen + data_field;
  if (total > cap || data_field > 65536) return 0;
  sp_primary(out, false, true, apid, seq_count, data_field);
  put_u32(out + 6, t.coarse);          // CUC coarse time (4 octets)
  put_u16(out + 10, t.fine);           // CUC fine time   (2 octets)
  if (payload_len) memcpy(out + 12, payload, payload_len);
  return total;
}

size_t build_idle_packet(uint8_t* out, size_t total_len, uint16_t seq_count) {
  if (total_len < kSpPriHdrLen + 1) return 0;
  sp_primary(out, false, false, kApidIdle, seq_count, total_len - kSpPriHdrLen);
  // Idle data pattern is mission-defined; 0x55 gives good transition density.
  memset(out + kSpPriHdrLen, 0x55, total_len - kSpPriHdrLen);
  return total_len;
}

// ---------------------------------------------------------------------------
// CLCW
// ---------------------------------------------------------------------------
uint32_t Clcw::pack() const {
  uint32_t w = 0;
  // bit0 Control Word Type = 0, bits1-2 Version = 00, bits3-5 Status = 000,
  // bits6-7 COP in Effect = 01 (COP-1)
  w |= (uint32_t)0x1 << 24;
  w |= (uint32_t)(vcid & 0x3F) << 18;               // bits 8-13
  // bits 14-15 spare
  w |= (uint32_t)(no_rf_available ? 1 : 0) << 15;   // bit 16
  w |= (uint32_t)(no_bit_lock ? 1 : 0) << 14;       // bit 17
  w |= (uint32_t)(lockout ? 1 : 0) << 13;           // bit 18
  w |= (uint32_t)(wait ? 1 : 0) << 12;              // bit 19
  w |= (uint32_t)(retransmit ? 1 : 0) << 11;        // bit 20
  w |= (uint32_t)(farm_b_counter & 0x3) << 9;       // bits 21-22
  // bit 23 spare
  w |= (uint32_t)report_value;                       // bits 24-31
  return w;
}

// ---------------------------------------------------------------------------
// VcStream
// ---------------------------------------------------------------------------
void VcStream::reset() {
  in_ = out_ = s_in_ = s_out_ = last_real_end_ = 0;
  overflows_ = 0;
}

bool VcStream::push(const uint8_t* pkt, size_t len, bool is_idle) {
  if (len == 0) return true;
  if (len > free_bytes() || (s_in_ - s_out_) >= kMaxStarts) {
    ++overflows_;
    return false;
  }
  starts_[s_in_ & (kMaxStarts - 1)] = in_;
  ++s_in_;
  for (size_t i = 0; i < len; ++i) buf_[(in_ + i) & (kBufLen - 1)] = pkt[i];
  in_ += (uint32_t)len;
  if (!is_idle) last_real_end_ = in_;
  return true;
}

uint16_t VcStream::pop_field(uint8_t* dst, size_t n) {
  // Drop start markers that are already behind the read pointer (cannot happen
  // in normal operation, defensive only).
  while (s_out_ != s_in_ && (int32_t)(starts_[s_out_ & (kMaxStarts - 1)] - out_) < 0) ++s_out_;

  uint16_t fhp = kFhpNoPacketStart;
  if (s_out_ != s_in_) {
    const uint32_t first = starts_[s_out_ & (kMaxStarts - 1)];
    const uint32_t off = first - out_;
    if (off < n) fhp = (uint16_t)off;
  }
  for (size_t i = 0; i < n; ++i) dst[i] = buf_[(out_ + i) & (kBufLen - 1)];
  out_ += (uint32_t)n;
  // Retire markers for packets that started inside this field.
  while (s_out_ != s_in_ && (int32_t)(starts_[s_out_ & (kMaxStarts - 1)] - out_) < 0) ++s_out_;
  return fhp;
}

// ---------------------------------------------------------------------------
// TmFramer
// ---------------------------------------------------------------------------
void TmFramer::init(uint16_t scid, Framing framing) {
  scid_ = scid;                    // TM uses the low 10 bits, USLP all 16
  framing_ = framing;
  mcfc_ = 0;
  memset(vcfc_, 0, sizeof(vcfc_));
  last_count_ = last_vcid_ = 0;
}

size_t TmFramer::data_len(const FrameSecurity* sec) const {
  const size_t base = framing_ == FRAMING_USLP ? kUslpTfdzLen : kTmDataLen;
  const size_t overhead = sec ? sec->header_len() + sec->trailer_len() : 0;
  return overhead < base ? base - overhead : 0;
}

void TmFramer::header(uint8_t* f, uint8_t vc, uint16_t fhp) {
  // TFVN=00 | SCID(10) | VCID(3) | OCF flag = 1
  const uint16_t id = (uint16_t)((0u << 14) | ((scid_ & 0x3FFu) << 4) | ((vc & 0x7u) << 1) | 1u);
  put_u16(f + 0, id);
  f[2] = mcfc_;                    // counters advance in commit(), once the frame is final
  f[3] = vcfc_[vc & 7];
  // Data Field Status: SecHdr=0 | Sync=0 | PacketOrder=0 | SegLenId=11 | FHP(11)
  put_u16(f + 4, (uint16_t)((0x3u << 11) | (fhp & 0x7FF)));
}

size_t TmFramer::uslp_header(uint8_t* f, uint8_t vcid, uint8_t upid, uint16_t fhp,
                             size_t sec_hdr_len) {
  UslpHeader h;
  h.scid = scid_;
  h.dest = false;                  // the SCID names the source of a downlink frame
  h.vcid = vcid;
  h.frame_len = kTmFrameLen;
  h.ocf = true;                    // CLCW in every frame, as in TM mode
  h.vcf_len = 1;
  h.vcf_count = vcfc_[vcid & 0x3F];
  const size_t n = uslp_put_header(f, h);        // == kUslpTmHdrLen
  // The TFDF header follows the SDLS security header (it is part of the
  // protected TFDF, 355.0-B-2 / 732.1-B-2 §6).
  uint8_t* t = f + n + sec_hdr_len;
  t[0] = uslp_tfdf_byte(kUslpRulePackets, upid);
  put_u16(t + 1, fhp == kFhpNoPacketStart ? kUslpFhpNone : fhp);
  return n;
}

void TmFramer::trailer(uint8_t* f, const Clcw& clcw) {
  put_u32(f + kTmFrameLen - kTmFecfLen - kTmOcfLen, clcw.pack());
  const uint16_t crc = crc16_ccitt(f, kTmFrameLen - kTmFecfLen);
  put_u16(f + kTmFrameLen - kTmFecfLen, crc);
}

void TmFramer::commit(uint8_t vcid) {
  last_vcid_ = vcid;
  last_count_ = framing_ == FRAMING_USLP ? vcfc_[vcid] : mcfc_;
  ++mcfc_;
  ++vcfc_[vcid];
}

bool TmFramer::build_frame(uint8_t vc, VcStream& s, const Clcw& clcw, uint16_t& idle_seq,
                           uint8_t frame[kTmFrameLen], FrameSecurity* sec) {
  const bool uslp = framing_ == FRAMING_USLP;
  const size_t n = data_len(sec);
  if (n == 0) return false;        // security overhead larger than the frame
  if (s.bytes() < n) {
    // Pad with an idle packet. Minimum idle packet = 7 octets; any surplus
    // spills into the next frame of this VC, which is legal (packets span).
    const size_t gap = n - s.bytes();
    const size_t idle_len = gap < 7 ? 7 : gap;
    uint8_t idle[kTmDataLen];
    build_idle_packet(idle, idle_len, idle_seq);
    idle_seq = (uint16_t)((idle_seq + 1) & 0x3FFF);
    s.push(idle, idle_len, true);
    if (s.bytes() < n) {           // cannot happen (see VcStream sizing); be safe
      build_oid_frame(clcw, frame);
      return true;
    }
  }
  const size_t sh = sec ? sec->header_len() : 0;
  const size_t hdr = uslp ? kUslpTmHdrLen : kTmPriHdrLen;
  const size_t zone = hdr + sh + (uslp ? kUslpTfdfHdrLen : 0);   // first packet octet
  const VcStream::Mark m = s.mark();
  const uint16_t fhp = s.pop_field(frame + zone, n);
  const uint8_t vcid = uslp ? (uint8_t)(vc & 0x3F) : (uint8_t)(vc & 7);
  if (uslp) uslp_header(frame, vcid, kUslpUpidPackets, fhp, sh);
  else      header(frame, vcid, fhp);
  // Protected region: TM data field / USLP TFDF (TFDF header + TFDZ).
  if (sec && !sec->protect(frame, hdr, uslp ? kUslpTfdfHdrLen + n : n)) {
    s.rewind(m);
    return false;
  }
  commit(vcid);
  trailer(frame, clcw);
  return true;
}

void TmFramer::build_oid_frame(const Clcw& clcw, uint8_t frame[kTmFrameLen],
                               const uint8_t* idle, size_t idle_len) {
  if (framing_ == FRAMING_USLP) {
    const size_t n = uslp_header(frame, kUslpVcidOid, kUslpUpidIdle, kUslpFhpNone, 0);
    idle_fill(frame + n + kUslpTfdfHdrLen, kUslpTfdzLen, idle, idle_len);
    commit(kUslpVcidOid);
  } else {
    header(frame, VC_IDLE, kFhpOnlyIdleData);
    idle_fill(frame + kTmPriHdrLen, kTmDataLen, idle, idle_len);
    commit(VC_IDLE);
  }
  trailer(frame, clcw);
}

void idle_fill(uint8_t* dst, size_t n, const uint8_t* pattern, size_t pattern_len) {
  if (!pattern || !pattern_len) { memset(dst, 0x55, n); return; }
  for (size_t i = 0; i < n; ++i) dst[i] = pattern[i % pattern_len];
}

// ---------------------------------------------------------------------------
// Channel coding
// ---------------------------------------------------------------------------
void build_cadu(const uint8_t frame[kTmFrameLen], uint8_t cadu[kCaduLen]) {
  put_u32(cadu, kAsm);
  uint8_t* cb = cadu + kAsmLen;
  memcpy(cb, frame, kTmFrameLen);
  ReedSolomon::encode(cb, kTmFrameLen, cb + kTmFrameLen);  // 23 octets virtual fill
  randomize(cb, kCodeblockLen);                             // ASM is not randomized
}

}  // namespace ccsds
