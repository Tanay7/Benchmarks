// =============================================================================
//  CFDP class 1 (CCSDS 727.0-B-5): PDU codec, modular checksum, sender and the
//  EGSE relay monitor. Layouts and rationale: cfdp.h.
// =============================================================================
#include "cfdp.h"

#include <string.h>

namespace ccsds {

namespace {

size_t put_hdr(uint8_t* out, bool file_data, uint8_t source, uint32_t seq, uint8_t dest,
               size_t data_len) {
  CfdpHeader h;
  h.file_data = file_data;
  h.data_len = (uint16_t)data_len;   // every builder keeps data_len <= 0xFFFF
  h.source = source;
  h.seq = seq;
  h.dest = dest;
  return cfdp_put_header(out, h);
}

bool name_char_ok(uint8_t c) {
  return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '.' ||
         c == '_' || c == '-';
}

// Length of a NUL-terminated name, capped at kCfdpMaxNameLen + 1 (= too long).
size_t name_len(const char* s) {
  size_t n = 0;
  if (!s) return 0;
  while (n <= kCfdpMaxNameLen && s[n] != '\0') ++n;
  return n;
}

// Name check on a NUL-terminated string (begin() and the Metadata builder).
bool c_name_ok(const char* s, size_t n) {
  if (!s || n == 0 || n > kCfdpMaxNameLen || s[0] == '.') return false;
  for (size_t i = 0; i < n; ++i)
    if (!name_char_ok((uint8_t)s[i])) return false;
  return true;
}

// Decimal digits of v into out (no NUL); returns the count, 0 if cap is short.
size_t put_dec(char* out, size_t cap, uint32_t v) {
  char tmp[10];                      // 4294967295 has 10 digits
  size_t n = 0;
  do {
    tmp[n++] = (char)('0' + v % 10u);
    v /= 10u;
  } while (v != 0u && n < sizeof tmp);
  if (n > cap) return 0;
  for (size_t i = 0; i < n; ++i) out[i] = tmp[n - 1 - i];
  return n;
}

}  // namespace

bool cfdp_name_ok(const uint8_t* name, size_t n, size_t max_len) {
  if (!name || n == 0 || n > max_len || n > kCfdpMaxNameLen || name[0] == '.') return false;
  for (size_t i = 0; i < n; ++i)
    if (!name_char_ok(name[i])) return false;
  return true;
}

size_t cfdp_ssr_file_name(char* out, size_t cap, uint16_t partition, uint32_t sclk_s) {
  char buf[32];                      // longest: "ssr_p65535_4294967295.bin" = 25
  memcpy(buf, "ssr_p", 5);
  size_t n = 5;
  n += put_dec(buf + n, sizeof buf - n, partition);
  buf[n++] = '_';
  n += put_dec(buf + n, sizeof buf - n, sclk_s);
  memcpy(buf + n, ".bin", 4);
  n += 4;
  if (!out || cap < n + 1) {
    if (out && cap != 0u) out[0] = '\0';
    return 0;
  }
  memcpy(out, buf, n);
  out[n] = '\0';
  return n;
}

size_t cfdp_put_header(uint8_t* p, const CfdpHeader& h) {
  if (!p) return 0;
  p[0] = (uint8_t)((kCfdpVersion << 5) | ((h.file_data ? 1u : 0u) << 4) |
                   ((h.to_sender ? 1u : 0u) << 3) | ((h.unacknowledged ? 1u : 0u) << 2) |
                   ((h.crc ? 1u : 0u) << 1) | (h.large_file ? 1u : 0u));
  put_u16(p + 1, h.data_len);
  // segmentation control 0 | entity-ID length - 1 = 000 | segment metadata 0 |
  // sequence-number length - 1 = 011
  p[3] = 0x03;
  p[4] = h.source;
  put_u32(p + 5, h.seq);
  p[9] = h.dest;
  return kCfdpHdrLen;
}

size_t cfdp_get_header(const uint8_t* p, size_t len, CfdpHeader& h) {
  if (!p || len < kCfdpHdrLen || (p[0] >> 5) != kCfdpVersion) return 0;
  if (p[0] & 0x03) return 0;                 // PDU CRC / large file: not this profile
  // The segmentation-control bit (b7) does not change the layout; everything
  // else in octet 3 must be 1-octet entity IDs, no segment metadata, 4-octet seq.
  if ((p[3] & 0x7F) != 0x03) return 0;
  const uint16_t data_len = get_u16(p + 1);
  if (kCfdpHdrLen + (size_t)data_len > len) return 0;
  h.file_data      = ((p[0] >> 4) & 1u) != 0;
  h.to_sender      = ((p[0] >> 3) & 1u) != 0;
  h.unacknowledged = ((p[0] >> 2) & 1u) != 0;
  h.crc = false;
  h.large_file = false;
  h.data_len = data_len;
  h.source   = p[4];
  h.seq      = get_u32(p + 5);
  h.dest     = p[9];
  return kCfdpHdrLen;
}

uint32_t cfdp_checksum(uint32_t sum, uint32_t offset, const uint8_t* data, size_t n) {
  if (!data) return sum;
  size_t i = 0;
  // Octet at file offset o sits in byte (o & 3) of its big-endian word; the
  // offset arithmetic is mod 2^32 like the file offsets themselves.
  for (; i < n && ((offset + (uint32_t)i) & 3u) != 0u; ++i)
    sum += (uint32_t)data[i] << (8u * (3u - ((offset + (uint32_t)i) & 3u)));
  for (; i + 4u <= n; i += 4u) sum += get_u32(data + i);   // word-aligned run
  for (; i < n; ++i) sum += (uint32_t)data[i] << (8u * (3u - ((offset + (uint32_t)i) & 3u)));
  return sum;
}

size_t cfdp_build_metadata(uint8_t* out, size_t cap, uint8_t source, uint32_t seq, uint8_t dest,
                           uint32_t file_size, const char* name) {
  const size_t nl = name_len(name);
  if (!c_name_ok(name, nl)) return 0;
  const size_t df = 1 + 1 + 4 + 2 * (1 + nl);
  if (!out || cap < kCfdpHdrLen + df) return 0;
  uint8_t* p = out + put_hdr(out, false, source, seq, dest, df);
  *p++ = kCfdpDirMetadata;
  *p++ = kCfdpChecksumModular;      // reserved 0 | closure requested 0 | reserved 00 | type
  put_u32(p, file_size);
  p += 4;
  for (int k = 0; k < 2; ++k) {     // source and destination file name LVs (same name)
    *p++ = (uint8_t)nl;
    memcpy(p, name, nl);
    p += nl;
  }
  return (size_t)(p - out);
}

size_t cfdp_build_file_data(uint8_t* out, size_t cap, uint8_t source, uint32_t seq, uint8_t dest,
                            uint32_t offset, const uint8_t* data, size_t n) {
  if (!out || n > 0xFFFFu - 4u || cap < kCfdpHdrLen + 4 + n || (n && !data)) return 0;
  const size_t df = 4 + n;
  // memmove first (overlap-safe): the sender reads the segment straight into
  // the data area, and the header must not be written over unread data.
  if (n) memmove(out + kCfdpHdrLen + 4, data, n);
  put_hdr(out, true, source, seq, dest, df);
  put_u32(out + kCfdpHdrLen, offset);
  return kCfdpHdrLen + df;
}

size_t cfdp_build_eof(uint8_t* out, size_t cap, uint8_t source, uint32_t seq, uint8_t dest,
                      uint8_t condition, uint32_t checksum, uint32_t file_size) {
  const uint8_t cond = (uint8_t)(condition & 0x0Fu);
  const bool fault = cond != kCfdpCondNoError;
  const size_t df = 1 + 1 + 4 + 4 + (fault ? 3 : 0);
  if (!out || cap < kCfdpHdrLen + df) return 0;
  uint8_t* p = out + put_hdr(out, false, source, seq, dest, df);
  *p++ = kCfdpDirEof;
  *p++ = (uint8_t)(cond << 4);                // condition code | spare 0000
  put_u32(p, checksum);
  put_u32(p + 4, file_size);
  p += 8;
  if (fault) {                                // fault location: where the cancel began
    *p++ = kCfdpTlvEntityId;
    *p++ = 1;
    *p++ = source;
  }
  return (size_t)(p - out);
}

bool cfdp_parse(const uint8_t* pdu, size_t len, CfdpPdu& o) {
  o = CfdpPdu();
  if (!pdu || cfdp_get_header(pdu, len, o.hdr) == 0) return false;
  const size_t n = o.hdr.data_len;
  if (kCfdpHdrLen + n != len) return false;   // exactly one PDU, no trailing octets
  const uint8_t* p = pdu + kCfdpHdrLen;
  if (o.hdr.file_data) {
    if (n < 4) return false;
    o.offset = get_u32(p);
    o.data = p + 4;
    o.data_len = n - 4;
    return true;
  }
  if (n < 1) return false;
  o.directive = p[0];
  if (o.directive == kCfdpDirMetadata) {
    if (n < 7) return false;                  // code, flags, size, source-name length
    o.closure_requested = ((p[1] >> 6) & 1u) != 0;
    o.checksum_type = (uint8_t)(p[1] & 0x0Fu);
    o.file_size = get_u32(p + 2);
    size_t i = 6;
    o.src_name_len = p[i];
    o.src_name = p + i + 1;
    i += 1u + o.src_name_len;
    if (i + 1 > n) return false;              // destination-name length octet
    o.dst_name_len = p[i];
    o.dst_name = p + i + 1;
    i += 1u + o.dst_name_len;
    return i <= n;                            // options (TLVs) may follow: ignored
  }
  if (o.directive == kCfdpDirEof) {
    if (n < 10) return false;
    o.condition = (uint8_t)(p[1] >> 4);
    o.checksum = get_u32(p + 2);
    o.file_size = get_u32(p + 6);
    if (n >= 13 && p[10] == kCfdpTlvEntityId && p[11] == 1) {
      o.has_fault_entity = true;
      o.fault_entity = p[12];
    }
    return true;
  }
  return false;
}

// ---------------------------------------------------------------------------
// CfdpSender
// ---------------------------------------------------------------------------
bool CfdpSender::begin(uint32_t seq, const char* name, CfdpSource* source, uint32_t max_bytes,
                       size_t seg_len, uint8_t source_entity, uint8_t dest_entity) {
  const size_t nl = name_len(name);
  if (state_ != IDLE || !source || !c_name_ok(name, nl) || seg_len == 0 ||
      seg_len > kCfdpFlightSegLen)
    return false;
  memcpy(name_, name, nl);
  name_[nl] = '\0';
  src_ = source;
  seq_ = seq;
  size_ = source->size();                     // held fixed for the whole transaction
  if (max_bytes != 0u && max_bytes < size_) size_ = max_bytes;
  off_ = 0;
  sum_ = 0;
  cond_ = kCfdpCondNoError;
  done_ = false;
  seg_ = (uint8_t)seg_len;
  source_ = source_entity;
  dest_ = dest_entity;
  state_ = METADATA;
  return true;
}

void CfdpSender::cancel() {
  if (state_ == METADATA || state_ == FILE_DATA) {
    cond_ = kCfdpCondCancel;
    state_ = EOF_PENDING;
  }
}

size_t CfdpSender::build_eof(uint8_t* out, size_t cap) {
  // A no-error EOF carries the full size; a cancelled one the octets sent and
  // the checksum over exactly those (the receiver can verify the prefix).
  const size_t n = cfdp_build_eof(out, cap, source_, seq_, dest_, cond_, sum_,
                                  cond_ == kCfdpCondNoError ? size_ : off_);
  if (n != 0u) {
    last_cond_ = cond_;
    ++count_;
    done_ = true;
    src_ = nullptr;
    state_ = IDLE;
  }
  return n;
}

size_t CfdpSender::next_pdu(uint8_t* out, size_t cap) {
  if (!out || cap < kCfdpMaxPduLen) return 0;
  switch (state_) {
    case IDLE:
      return 0;
    case METADATA: {
      const size_t n = cfdp_build_metadata(out, cap, source_, seq_, dest_, size_, name_);
      if (n != 0u) state_ = size_ != 0u ? FILE_DATA : EOF_PENDING;
      return n;
    }
    case FILE_DATA: {
      uint32_t want = size_ - off_;
      if (want > seg_) want = seg_;
      uint8_t* seg = out + kCfdpHdrLen + 4;   // read straight into the PDU
      const size_t got = src_ ? src_->read(off_, seg, want) : 0u;
      if (got == 0u || got > want) {          // source failed: end the transaction
        cond_ = kCfdpCondFilestoreRejection;
        state_ = EOF_PENDING;
        return build_eof(out, cap);
      }
      const size_t n = cfdp_build_file_data(out, cap, source_, seq_, dest_, off_, seg, got);
      if (n == 0u) return 0;                  // nothing advanced; the read is repeatable
      sum_ = cfdp_checksum(sum_, off_, seg, got);
      off_ += (uint32_t)got;
      if (off_ >= size_) state_ = EOF_PENDING;
      return n;
    }
    case EOF_PENDING:
      return build_eof(out, cap);
  }
  return 0;
}

uint16_t CfdpSender::progress_permille() const {
  if (done_ && last_cond_ == kCfdpCondNoError) return 1000;
  if (size_ == 0u) return 0;
  return (uint16_t)(((uint64_t)off_ * 1000u) / size_);
}

// ---------------------------------------------------------------------------
// CfdpRelayMonitor
// ---------------------------------------------------------------------------
bool CfdpRelayMonitor::acceptable(const uint8_t* pdu, size_t len) {
  if (len > kCfdpRelayMaxPduLen) return false;
  CfdpPdu p;
  if (!cfdp_parse(pdu, len, p)) return false;
  if (p.hdr.source != kCfdpEntityVgq1 || p.hdr.dest != kCfdpEntityDssq1 ||
      (p.hdr.seq & kCfdpEgseSeqFlag) == 0u || !p.hdr.unacknowledged || p.hdr.to_sender)
    return false;
  if (p.hdr.file_data) return p.data_len >= 1u && p.data_len <= kCfdpRelaySegLen;
  if (p.directive == kCfdpDirMetadata)
    return p.checksum_type == kCfdpChecksumModular && !p.closure_requested &&
           cfdp_name_ok(p.src_name, p.src_name_len, kCfdpRelayMaxNameLen) &&
           cfdp_name_ok(p.dst_name, p.dst_name_len, kCfdpRelayMaxNameLen);
  return true;                                // EOF
}

void CfdpRelayMonitor::observe(const uint8_t* pdu, size_t len, uint32_t now_ms) {
  CfdpPdu p;
  if (!cfdp_parse(pdu, len, p)) return;
  if (p.hdr.seq != seq_) {                    // a new transaction
    seq_ = p.hdr.seq;
    size_ = 0;
    hi_ = 0;
    cond_ = kCfdpCondNoError;
    done_ = false;
  }
  if (done_) return;                          // EGSE retry after the EOF
  active_ = true;
  last_ms_ = now_ms;
  if (p.hdr.file_data) {
    const uint32_t end = p.offset + (uint32_t)p.data_len;
    if (end > hi_) hi_ = end;
  } else if (p.directive == kCfdpDirMetadata) {
    size_ = p.file_size;
  } else if (p.directive == kCfdpDirEof) {
    cond_ = p.condition;
    active_ = false;
    done_ = true;
  }
}

bool CfdpRelayMonitor::expired(uint32_t now_ms, uint32_t timeout_ms) const {
  return active_ && (uint32_t)(now_ms - last_ms_) >= timeout_ms;   // wrap-safe difference
}

uint16_t CfdpRelayMonitor::progress_permille() const {
  if (done_ && cond_ == kCfdpCondNoError) return 1000;
  if (size_ == 0u) return 0;
  const uint32_t hi = hi_ < size_ ? hi_ : size_;
  return (uint16_t)(((uint64_t)hi * 1000u) / size_);
}

}  // namespace ccsds
