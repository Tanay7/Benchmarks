#include "tc.h"
#include "crc16.h"
#include <string.h>

namespace ccsds {

// ---------------------------------------------------------------------------
// BCH(63,56)
// ---------------------------------------------------------------------------
static uint8_t bch_remainder(const uint8_t info[7]) {
  // Shift-register division by g(x) = x^7 + x^6 + x^2 + 1 (taps 0x45 below x^7).
  uint8_t reg = 0;
  for (int i = 0; i < 7; ++i) {
    for (int b = 7; b >= 0; --b) {
      const uint8_t in = (info[i] >> b) & 1;
      const uint8_t fb = (uint8_t)(in ^ ((reg >> 6) & 1));
      reg = (uint8_t)((reg << 1) & 0x7F);
      if (fb) reg ^= 0x45;
    }
  }
  return reg;  // 7 bits
}

void bch_encode(const uint8_t info[7], uint8_t out[8]) {
  memcpy(out, info, 7);
  out[7] = (uint8_t)(((~bch_remainder(info)) & 0x7F) << 1);  // complemented + filler 0
}

namespace {
// syndrome (7 bits) -> bit position to flip (0..55 info, 56..62 parity), 0xFF none
uint8_t g_syn[128];
bool g_syn_ready = false;

uint8_t syndrome_of(const uint8_t cb[8]) {
  const uint8_t expect = (uint8_t)((~bch_remainder(cb)) & 0x7F);
  return (uint8_t)(expect ^ ((cb[7] >> 1) & 0x7F));
}

void build_syndromes() {
  memset(g_syn, 0xFF, sizeof(g_syn));
  uint8_t zero_info[7] = {0};
  uint8_t cb[8];
  bch_encode(zero_info, cb);
  for (uint8_t p = 0; p < 63; ++p) {
    uint8_t e[8];
    memcpy(e, cb, 8);
    if (p < 56) e[p / 8] ^= (uint8_t)(0x80 >> (p % 8));
    else        e[7] ^= (uint8_t)(0x80 >> (p - 56));   // parity bits occupy bits 7..1
    g_syn[syndrome_of(e)] = p;
  }
  g_syn_ready = true;
}
}  // namespace

size_t cltu_decode(const uint8_t* in, size_t len, uint8_t* out, size_t cap, BchStats& st) {
  if (!g_syn_ready) build_syndromes();
  // Locate the start sequence (exact match; the 16-bit pattern is long enough
  // given the packetised LoRa link).
  size_t i = 0;
  for (; i + 1 < len; ++i)
    if (in[i] == (uint8_t)(kCltuStart >> 8) && in[i + 1] == (uint8_t)kCltuStart) break;
  if (i + 1 >= len) return 0;
  i += 2;

  size_t n = 0;
  while (i + 8 <= len) {
    uint8_t cb[8];
    memcpy(cb, in + i, 8);
    i += 8;
    const uint8_t s = syndrome_of(cb);
    if (s != 0) {
      const uint8_t p = g_syn[s];
      if (p == 0xFF) { ++st.codeblocks_rejected; break; }   // tail or bad block: end
      if (p < 56) cb[p / 8] ^= (uint8_t)(0x80 >> (p % 8));
      ++st.codeblocks_corrected;
    } else {
      ++st.codeblocks_ok;
    }
    if (n + 7 > cap) break;
    memcpy(out + n, cb, 7);
    n += 7;
  }
  return n;
}

// ---------------------------------------------------------------------------
// TC Transfer Frame
// ---------------------------------------------------------------------------
// USLP TC frame (732.1-B-2). Mission rules: destination flag set (the SCID is
// the receiving spacecraft), full TFPH, Type-AD frames carry exactly one VCF
// count octet (= N(S), FARM-1 counts modulo 256) and no PCC; an OCF, if a
// foreign ground system sends one, is skipped.
static TcParseResult uslp_tc_parse(const uint8_t* b, size_t len, TcFrame& f, bool open_tfdf) {
  UslpHeader h;
  const size_t hl = uslp_get_header(b, len, h);
  if (!hl) return h.truncated ? TC_BAD_HEADER : TC_TOO_SHORT;
  f.format    = TC_FMT_USLP;
  f.hdr_len   = (uint8_t)hl;
  f.bypass    = h.bypass;
  f.control   = h.pcc;
  f.scid      = h.scid;
  f.vcid      = h.vcid;
  f.map_id    = h.map_id;
  f.length    = h.frame_len;
  f.seq       = h.vcf_len ? (uint8_t)h.vcf_count : 0;
  f.upid      = 0;
  f.tfdf_open = false;
  if (f.scid != kSpacecraftId) return TC_BAD_SCID;
  const size_t ocf = h.ocf ? kTmOcfLen : 0;
  if (f.length > len || f.length < hl + kUslpTcTfdfHdrLen + ocf + 2 || f.length > kTcMaxFrameLen)
    return TC_BAD_LENGTH;
  const uint16_t crc = crc16_ccitt(b, f.length - 2);
  if (crc != get_u16(b + f.length - 2)) return TC_BAD_FECF;
  if (!h.dest || (!h.bypass && (h.vcf_len != 1 || h.pcc))) return TC_BAD_HEADER;
  f.data = b + hl;
  f.data_len = (uint16_t)(f.length - hl - ocf - 2);
  return open_tfdf ? tc_open_tfdf(f) : TC_OK;
}

TcParseResult tc_parse(const uint8_t* b, size_t len, TcFrame& f, bool open_tfdf) {
  if (len < kTcPriHdrLen + 2) return TC_TOO_SHORT;
  if (uslp_is_uslp(b)) return uslp_tc_parse(b, len, f, open_tfdf);
  const uint16_t w0 = get_u16(b);
  const uint16_t w1 = get_u16(b + 2);
  if ((w0 >> 14) != 0) return TC_BAD_VERSION;
  f.format  = TC_FMT_TC;
  f.hdr_len = kTcPriHdrLen;
  f.map_id  = 0;
  f.upid    = 0;
  f.tfdf_open = true;
  f.bypass  = (w0 >> 13) & 1;
  f.control = (w0 >> 12) & 1;
  f.scid    = w0 & 0x3FF;
  f.vcid    = (uint8_t)(w1 >> 10);
  f.length  = (uint16_t)((w1 & 0x3FF) + 1);
  f.seq     = b[4];
  if (f.scid != kSpacecraftId) return TC_BAD_SCID;
  if (f.length > len || f.length < kTcPriHdrLen + 2 || f.length > kTcMaxFrameLen) return TC_BAD_LENGTH;
  const uint16_t crc = crc16_ccitt(b, f.length - 2);
  if (crc != get_u16(b + f.length - 2)) return TC_BAD_FECF;
  f.data = b + kTcPriHdrLen;
  f.data_len = (uint16_t)(f.length - kTcPriHdrLen - 2);
  return TC_OK;
}

TcParseResult tc_open_tfdf(TcFrame& f) {
  if (f.tfdf_open) return TC_OK;
  if (f.data_len < kUslpTcTfdfHdrLen) return TC_BAD_TFDF;
  const uint8_t rule = f.data[0] >> 5, upid = f.data[0] & 0x1F;
  // No segmentation; UPID 1 (COP-1 directive) exactly when the PCC flag is set.
  if (rule != kUslpRuleNoSeg || upid != (f.control ? kUslpUpidCop1 : kUslpUpidPackets))
    return TC_BAD_TFDF;
  f.upid = upid;
  f.data += kUslpTcTfdfHdrLen;
  f.data_len = (uint16_t)(f.data_len - kUslpTcTfdfHdrLen);
  f.tfdf_open = true;
  return TC_OK;
}

// ---------------------------------------------------------------------------
// FARM-1 (232.1-B-2 §6). Window arithmetic is modulo 256.
// ---------------------------------------------------------------------------
Farm1::Verdict Farm1::on_frame(const TcFrame& f) {
  if (f.bypass) {
    farm_b_ = (uint8_t)((farm_b_ + 1) & 3);
    if (!f.control) return ACCEPT_BYPASS;                       // Type-BD
    // Type-BC control commands
    if (f.data_len == 1 && f.data[0] == 0x00) {                  // Unlock
      lockout_ = wait_ = retransmit_ = false;
      state_ = S1_OPEN;
      return CONTROL_UNLOCK;
    }
    if (f.data_len == 3 && f.data[0] == 0x82 && f.data[1] == 0x00) {  // Set V(R)
      if (state_ != S3_LOCKOUT) {
        vr_ = f.data[2];
        retransmit_ = wait_ = false;
        state_ = S1_OPEN;
      }
      return CONTROL_SET_VR;
    }
    return CONTROL_INVALID;
  }
  // Type-AD (sequence controlled)
  if (state_ == S3_LOCKOUT) return DISCARD_LOCKOUT;
  const uint8_t pw = (uint8_t)(window_ / 2), nw = (uint8_t)(window_ / 2);
  const uint8_t diff = (uint8_t)(f.seq - vr_);
  if (diff == 0) {
    vr_ = (uint8_t)(vr_ + 1);
    retransmit_ = false;
    return ACCEPT;
  }
  if (diff < pw) {                 // ahead of V(R): a frame was lost
    retransmit_ = true;
    return DISCARD_RETRANSMIT;
  }
  if ((uint8_t)(vr_ - f.seq) <= nw) return DISCARD_NEG_WINDOW;   // duplicate
  lockout_ = true;
  state_ = S3_LOCKOUT;
  return DISCARD_LOCKOUT;
}

void Farm1::fill_clcw(Clcw& c) const {
  c.lockout = lockout_;
  c.wait = wait_;
  c.retransmit = retransmit_;
  c.farm_b_counter = farm_b_;
  c.report_value = vr_;
}

}  // namespace ccsds
