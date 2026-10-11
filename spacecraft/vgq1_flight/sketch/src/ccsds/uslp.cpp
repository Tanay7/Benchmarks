#include "uslp.h"

namespace ccsds {

size_t uslp_put_header(uint8_t* p, const UslpHeader& h) {
  const uint8_t vcid = h.vcid & 0x3F, n = h.vcf_len & 7;
  p[0] = (uint8_t)((kUslpTfvn << 4) | (h.scid >> 12));
  p[1] = (uint8_t)(h.scid >> 4);
  p[2] = (uint8_t)(((h.scid & 0xF) << 4) | ((h.dest ? 1u : 0u) << 3) | (vcid >> 3));
  p[3] = (uint8_t)(((vcid & 7u) << 5) | ((h.map_id & 0xFu) << 1));   // EoFPH = 0
  put_u16(p + 4, (uint16_t)(h.frame_len - 1));
  // bypass | PCC | 2 spare bits '00' | OCF flag | VCF count length
  p[6] = (uint8_t)(((h.bypass ? 1u : 0u) << 7) | ((h.pcc ? 1u : 0u) << 6) |
                   ((h.ocf ? 1u : 0u) << 3) | n);
  for (uint8_t i = 0; i < n; ++i) {               // big-endian, zero above 32 bits
    const uint8_t shift = (uint8_t)(8 * (n - 1 - i));
    p[kUslpFixedHdrLen + i] = shift < 32 ? (uint8_t)(h.vcf_count >> shift) : 0;
  }
  return kUslpFixedHdrLen + n;
}

size_t uslp_get_header(const uint8_t* p, size_t len, UslpHeader& h) {
  if (len < 4 || !uslp_is_uslp(p)) return 0;
  h.scid      = (uint16_t)(((p[0] & 0xF) << 12) | (p[1] << 4) | (p[2] >> 4));
  h.dest      = (p[2] >> 3) & 1;
  h.vcid      = (uint8_t)(((p[2] & 7) << 3) | (p[3] >> 5));
  h.map_id    = (p[3] >> 1) & 0xF;
  h.truncated = p[3] & 1;
  if (h.truncated || len < kUslpFixedHdrLen) return 0;
  h.frame_len = (uint16_t)(get_u16(p + 4) + 1);
  h.bypass    = (p[6] >> 7) & 1;
  h.pcc       = (p[6] >> 6) & 1;
  h.ocf       = (p[6] >> 3) & 1;
  h.vcf_len   = p[6] & 7;
  if (len < kUslpFixedHdrLen + h.vcf_len) return 0;
  h.vcf_count = 0;
  for (uint8_t i = 0; i < h.vcf_len; ++i) h.vcf_count = (h.vcf_count << 8) | p[kUslpFixedHdrLen + i];
  return kUslpFixedHdrLen + h.vcf_len;
}

}  // namespace ccsds
