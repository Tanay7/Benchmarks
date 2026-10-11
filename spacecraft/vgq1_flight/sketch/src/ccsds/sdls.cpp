#include "sdls.h"
#include "sha256.h"
#include <string.h>

namespace ccsds {

const char kSdlsLabelTcAe[] = "VGQ1-SDLS-TC-AE";
const char kSdlsLabelTmAe[] = "VGQ1-SDLS-TM-AE";

void sdls_derive_key(const uint8_t* master, size_t master_len, const char* label, uint8_t out[32]) {
  hmac_sha256(master, master_len, (const uint8_t*)label, strlen(label), nullptr, 0, nullptr, 0, out);
}

void sdls_iv(uint16_t dir, uint16_t epoch, uint32_t sn, uint8_t iv[kSdlsIvLen]) {
  put_u16(iv, dir);
  put_u16(iv + 2, epoch);
  put_u32(iv + 4, 0);
  put_u32(iv + 8, sn);
}

// ---------------------------------------------------------------------------
// TC: ProcessSecurity
// ---------------------------------------------------------------------------
const char* Sdls::result_name(Result r) {
  static const char* const kName[] = {"OK", "disabled", "bad length", "bad SPI", "REPLAY",
                                      "bad MAC", "bad IV"};
  return r <= SDLS_BAD_IV ? kName[r] : "?";
}

void Sdls::init(const uint8_t* key, size_t key_len, uint16_t spi) {
  gcm_.wipe();
  secure_zero(key_, sizeof key_);
  key_len_ = key_len > sizeof key_ ? sizeof key_ : key_len;
  if (key_len_) memcpy(key_, key, key_len_);
  spi_ = spi;
  svc_ = key_len_ ? SDLS_SVC_AUTH : SDLS_SVC_NONE;
}

void Sdls::configure(uint8_t service, const uint8_t* master, size_t master_len, uint16_t auth_spi) {
  init(nullptr, 0, auth_spi);                       // NONE until proven otherwise
  if (master == nullptr || master_len == 0) return;
  if (service == SDLS_SVC_AUTH) {
    init(master, master_len, auth_spi);
  } else if (service == SDLS_SVC_AE) {
    uint8_t k[32];
    sdls_derive_key(master, master_len, kSdlsLabelTcAe, k);
    gcm_.set_key(k);
    secure_zero(k, sizeof k);
    spi_ = kSdlsSpiTcAe;
    svc_ = SDLS_SVC_AE;
  }
}

size_t Sdls::overhead() const {
  return svc_ == SDLS_SVC_AUTH ? kSdlsHdrLen + kSdlsMacLen
       : svc_ == SDLS_SVC_AE   ? kSdlsAeOverhead : 0;
}

Sdls::Result Sdls::process(uint8_t* frame, TcFrame& f) {
  if (!enabled()) return SDLS_DISABLED;
  if (f.data < frame) return fail(SDLS_BAD_LENGTH);
  uint8_t* data = nullptr;
  size_t len = 0;
  const Result r = process(frame, (size_t)(f.data - frame), f.data_len, data, len);
  if (r == SDLS_OK) {
    f.data = data;
    f.data_len = (uint16_t)len;
  }
  return r;
}

Sdls::Result Sdls::process(uint8_t* frame, size_t hdr_len, size_t prot_len, uint8_t*& data,
                           size_t& data_len) {
  if (!enabled()) return SDLS_DISABLED;
  uint8_t* sh = frame + hdr_len;
  if (svc_ == SDLS_SVC_AUTH) {
    if (prot_len < kSdlsHdrLen + kSdlsMacLen + 1) return fail(SDLS_BAD_LENGTH);
    const uint16_t spi = get_u16(sh);
    const uint32_t sn = get_u32(sh + 2);
    if (spi != spi_) return fail(SDLS_BAD_SPI);
    uint8_t* body = sh + kSdlsHdrLen;
    const size_t body_len = prot_len - kSdlsHdrLen - kSdlsMacLen;
    uint8_t full[32];
    hmac_sha256(key_, key_len_, frame, hdr_len, sh, kSdlsHdrLen, body, body_len, full);
    if (!ct_equal(full, body + body_len, kSdlsMacLen)) return fail(SDLS_BAD_MAC);
    // Replay check only AFTER the MAC is proven, so forged frames cannot move SN.
    if (sn <= last_sn_) return fail(SDLS_REPLAY);
    last_sn_ = sn;
    data = body;
    data_len = body_len;
    return SDLS_OK;
  }
  // AE: | SPI 2 | IV 12 | ciphertext | tag 16 |
  if (prot_len < kSdlsAeOverhead + 1) return fail(SDLS_BAD_LENGTH);
  if (get_u16(sh) != spi_) return fail(SDLS_BAD_SPI);
  const uint8_t* iv = sh + 2;
  if (get_u16(iv) != kSdlsIvDirTc || get_u16(iv + 2) != 0 || get_u32(iv + 4) != 0)
    return fail(SDLS_BAD_IV);
  const uint32_t sn = get_u32(iv + 8);
  uint8_t* ct = sh + kSdlsAeHdrLen;
  const size_t ct_len = prot_len - kSdlsAeOverhead;
  const uint8_t* tag = ct + ct_len;
  const size_t aad_len = hdr_len + kSdlsAeHdrLen;     // primary header || security header
  if (!gcm_.verify(iv, frame, aad_len, ct, ct_len, tag)) return fail(SDLS_BAD_MAC);
  if (sn <= last_sn_) return fail(SDLS_REPLAY);
  // decrypt() checks the tag once more (one GHASH over <= 64 octets) so that
  // the buffer is only ever modified for an authentic, fresh frame.
  if (!gcm_.decrypt(iv, frame, aad_len, ct, ct_len, tag)) return fail(SDLS_BAD_MAC);
  last_sn_ = sn;
  data = ct;
  data_len = ct_len;
  return SDLS_OK;
}

// ---------------------------------------------------------------------------
// TM: ApplySecurity
// ---------------------------------------------------------------------------
void SdlsTm::init(uint8_t service, const uint8_t* master, size_t master_len) {
  gcm_.wipe();
  ae_ = false;
  fallback_ = false;
  epoch_ = 0;
  sn_ = 0;
  if (service != SDLS_SVC_AE || master == nullptr || master_len == 0) return;
  uint8_t k[32];
  sdls_derive_key(master, master_len, kSdlsLabelTmAe, k);
  gcm_.set_key(k);
  secure_zero(k, sizeof k);
  ae_ = true;
}

bool SdlsTm::set_epoch(uint16_t epoch) {
  if (epoch == 0 || epoch < epoch_) return false;     // never step back onto used nonces
  if (epoch != epoch_) {
    epoch_ = epoch;
    sn_ = 0;                                          // first frame of the epoch gets sn 1
  }
  fallback_ = false;
  return true;
}

bool SdlsTm::poll_epoch_timeout(uint32_t uptime_s, uint32_t wait_s) {
  if (!ae_ || epoch_ != 0 || fallback_ || uptime_s < wait_s) return false;
  fallback_ = true;
  return true;
}

SdlsTm::State SdlsTm::state() const {
  if (!ae_) return TM_OFF;
  if (epoch_ != 0 && sn_ != 0xFFFFFFFFUL) return TM_PROTECTED;   // sn exhausted: needs a new epoch
  return fallback_ ? TM_UNPROTECTED : TM_WAIT_EPOCH;
}

bool SdlsTm::protect(uint8_t* frame, size_t aad_len, size_t data_len) {
  if (!ae_) return true;                              // NONE: nothing reserved, nothing to do
  uint8_t* sh = frame + aad_len;
  uint8_t* data = sh + kSdlsAeHdrLen;
  uint8_t* tag = data + data_len;
  switch (state()) {
    case TM_PROTECTED:
      ++sn_;
      put_u16(sh, kSdlsSpiTmAe);
      sdls_iv(kSdlsIvDirTm, epoch_, sn_, sh + 2);
      gcm_.encrypt(sh + 2, frame, aad_len + kSdlsAeHdrLen, data, data_len, tag);
      ++n_protected_;
      return true;
    case TM_UNPROTECTED:                              // null SA: SPI 0, IV 0, data clear, MAC 0
      memset(sh, 0, kSdlsAeHdrLen);
      memset(tag, 0, kSdlsAeTagLen);
      ++n_null_;
      return true;
    default:
      ++n_refused_;
      return false;
  }
}

}  // namespace ccsds
