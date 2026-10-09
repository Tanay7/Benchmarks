#include "sdls.h"
#include "sha256.h"
#include <string.h>

namespace ccsds {

void Sdls::init(const uint8_t* key, size_t key_len, uint16_t spi) {
  key_len_ = key_len > sizeof key_ ? sizeof key_ : key_len;
  if (key_len_) memcpy(key_, key, key_len_);
  spi_ = spi;
}

Sdls::Result Sdls::process(const uint8_t* frame, TcFrame& f) {
  if (!enabled()) return SDLS_DISABLED;
  if (f.data_len < kSdlsHdrLen + kSdlsMacLen + 1) { ++fails_; return SDLS_BAD_LENGTH; }
  const uint8_t* sh = f.data;
  const uint16_t spi = get_u16(sh);
  const uint32_t sn = get_u32(sh + 2);
  if (spi != spi_) { ++fails_; return SDLS_BAD_SPI; }
  const uint8_t* body = sh + kSdlsHdrLen;
  const uint16_t body_len = (uint16_t)(f.data_len - kSdlsHdrLen - kSdlsMacLen);
  const uint8_t* mac = body + body_len;
  uint8_t full[32];
  hmac_sha256(key_, key_len_, frame, kTcPriHdrLen, sh, kSdlsHdrLen, body, body_len, full);
  if (!ct_equal(full, mac, kSdlsMacLen)) { ++fails_; return SDLS_BAD_MAC; }
  // Replay check only AFTER the MAC is proven, so forged frames cannot move SN.
  if (sn <= last_sn_) { ++fails_; return SDLS_REPLAY; }
  last_sn_ = sn;
  f.data = body;
  f.data_len = body_len;
  return SDLS_OK;
}

}  // namespace ccsds
