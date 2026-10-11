// =============================================================================
//  Host checks for the SDLS cryptography and frame security:
//    AES-256 forward cipher      FIPS-197 Appendix C.3
//    AES-256-GCM                 McGrew-Viega GCM specification, test cases 13-16
//                                (all four were also re-derived with the Python
//                                `cryptography` package before being embedded)
//    key derivation, TC AE (TC v1 and USLP TFPH as AAD), AUTH v2 compatibility,
//    tamper and replay rejection, TM AE / null-SA protection (TM and USLP
//    geometry) and the epoch rules that keep GCM nonces unique.
//
//  Writes <out>/sdls_ae_vectors.txt for ground/tests/test_sdls.py, one case per
//  line: "<name> key=value ..." - octet strings in hex, the integer fields
//  (hdr_len, prot_end, epoch, sn) in decimal.
// =============================================================================
#include <cstdio>
#include <cstring>
#include <string>

#include "ccsds/aes.h"
#include "ccsds/crc16.h"
#include "ccsds/gcm.h"
#include "ccsds/sdls.h"
#include "ccsds/tc.h"
#include "host_test.h"

using namespace ccsds;
using host_test::hex;
using host_test::unhex;

namespace {

// Own deterministic generator, so these vectors do not depend on test order.
uint32_t prng() {
  static uint32_t s = 0x5D15C0DEu;
  s ^= s << 13; s ^= s >> 17; s ^= s << 5;
  return s;
}
void fill(uint8_t* p, size_t n) { for (size_t i = 0; i < n; ++i) p[i] = (uint8_t)prng(); }

void vec(const std::string& out, const std::string& line) {
  static bool started = false;                 // first line of the run truncates the file
  FILE* f = std::fopen((out + "/sdls_ae_vectors.txt").c_str(), started ? "a" : "w");
  started = true;
  if (f) { std::fprintf(f, "%s\n", line.c_str()); std::fclose(f); }
}
std::string kv(const char* k, const uint8_t* p, size_t n) { return std::string(" ") + k + "=" + hex(p, n); }
std::string kv(const char* k, unsigned long v) { return std::string(" ") + k + "=" + std::to_string(v); }

// Shared known answers (identical constants in ground/tests/test_sdls.py).
// Master key 00 01 .. 1f; the derived keys and frames were computed with
// Python hmac/hashlib + `cryptography` (an implementation independent of this one).
const char* const kKatTcKey = "6a0fc847b89c96f2a51182cb604701ce276d706174bda2907b13e4d1e7bdacf1";
const char* const kKatTmKey = "e2344f0a091558afade4702042d0a9c9a9209464743e95fca02c3bd2ac3d6a59";
// TC v1 AD frame, SCID 0x0A7, VC 0, N(S) 0, SPI 2, sn 7, NOOP packet 18c0c000000001.
const char* const kKatTcAe =
    "00a7002b0000025443000000000000000000073e53f988b9490963030370d67a616f9bb7736a06f68fbed67d";
// The v2 AUTH frame (SPI 1, sn 7, same packet): AUTH stays byte-exact.
const char* const kKatTcAuth =
    "00a700230000010000000718c0c0000000010a45be9321446143549fe23fdaf3d435473a";
const uint8_t kNoop[] = {0x18, 0xC0, 0xC0, 0x00, 0x00, 0x00, 0x01};

void master_key(uint8_t k[32]) { for (int i = 0; i < 32; ++i) k[i] = (uint8_t)i; }

// | hdr (hdr_len) | SPI 2 | IV 12 | ciphertext | tag | FECF |, built with the
// primitives directly (not through Sdls) so the receive path is checked
// against an independent composition. Returns the frame length.
size_t build_tc_ae(uint8_t* fr, const uint8_t* hdr, size_t hdr_len, uint32_t sn,
                   const uint8_t* plain, size_t n, const uint8_t master[32]) {
  uint8_t k[32];
  sdls_derive_key(master, 32, kSdlsLabelTcAe, k);
  AesGcm g;
  g.set_key(k);
  std::memcpy(fr, hdr, hdr_len);
  put_u16(fr + hdr_len, kSdlsSpiTcAe);
  sdls_iv(kSdlsIvDirTc, 0, sn, fr + hdr_len + 2);
  uint8_t* ct = fr + hdr_len + kSdlsAeHdrLen;
  std::memcpy(ct, plain, n);
  g.encrypt(fr + hdr_len + 2, fr, hdr_len + kSdlsAeHdrLen, ct, n, ct + n);
  const size_t len = hdr_len + kSdlsAeOverhead + n + 2;
  put_u16(fr + len - 2, crc16_ccitt(fr, len - 2));
  return len;
}

// TM v1 primary header (132.0-B-3 §4.1.2): SCID 0x0A7, OCF flag 1, FHP.
void tm_header(uint8_t* f, uint8_t vc, uint8_t mcfc, uint8_t vcfc, uint16_t fhp) {
  put_u16(f, (uint16_t)((kSpacecraftId & 0x3FF) << 4 | (vc & 7) << 1 | 1));
  f[2] = mcfc;
  f[3] = vcfc;
  put_u16(f + 4, (uint16_t)(3u << 11 | (fhp & 0x7FF)));
}
// USLP TFPH (732.1-B-2): TFVN 1100, SCID 16 bits, src/dst, VCID, MAP 0,
// EoFPH 0, frame length - 1, bypass 0, PCC 0, OCF flag, VCF count length 1.
void uslp_header(uint8_t* f, bool dest, uint8_t vcid, uint16_t frame_len, bool ocf, uint8_t vcf) {
  put_u32(f, 0xC0000000u | (uint32_t)kSpacecraftId << 12 | (uint32_t)dest << 11 | (uint32_t)(vcid & 63) << 5);
  put_u16(f + 4, (uint16_t)(frame_len - 1));
  f[6] = (uint8_t)((ocf ? 0x08 : 0x00) | 0x01);
  f[7] = vcf;
}
// OCF (a CLCW-like word) + FECF after the protected region.
void tm_trailer(uint8_t* f) {
  put_u32(f + kTmFrameLen - 6, 0x01000000u);
  put_u16(f + kTmFrameLen - 2, crc16_ccitt(f, kTmFrameLen - 2));
}

}  // namespace

// ---- AES-256 ------------------------------------------------------------------
HOST_TEST(crypto_aes256_fips197) {
  uint8_t key[32], pt[16], ct[16];
  unhex("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f", key, 32);
  unhex("00112233445566778899aabbccddeeff", pt, 16);
  Aes256 a;
  a.set_key(key);
  a.encrypt(pt, ct);
  CHECK(hex(ct, 16) == "8ea2b7ca516745bfeafc49904b496089", "AES-256 FIPS-197 Appendix C.3");
  a.encrypt(pt, pt);                             // in place
  CHECK(std::memcmp(pt, ct, 16) == 0, "AES-256 encrypt in place");
  vec(out, "aes_c3" + kv("key", key, 32) + " pt=00112233445566778899aabbccddeeff" + kv("ct", ct, 16));
}

// ---- AES-256-GCM ----------------------------------------------------------------
HOST_TEST(crypto_gcm_test_vectors) {
  struct Case { const char *name, *key, *iv, *aad, *pt, *ct, *tag; };
  static const char* const K0 = "0000000000000000000000000000000000000000000000000000000000000000";
  static const char* const K = "feffe9928665731c6d6a8f9467308308feffe9928665731c6d6a8f9467308308";
  static const char* const IV = "cafebabefacedbaddecaf888";
  static const char* const P =
      "d9313225f88406e5a55909c5aff5269a86a7a9531534f7da2e4c303d8a318a72"
      "1c3c0c95956809532fcf0e2449a6b525b16aedf5aa0de657ba637b391aafd255";
  static const char* const P60 =
      "d9313225f88406e5a55909c5aff5269a86a7a9531534f7da2e4c303d8a318a72"
      "1c3c0c95956809532fcf0e2449a6b525b16aedf5aa0de657ba637b39";
  static const char* const C =
      "522dc1f099567d07f47f37a32a84427d643a8cdcbfe5c0c97598a2bd2555d1aa"
      "8cb08e48590dbb3da7b08b1056828838c5f61e6393ba7a0abcc9f662898015ad";
  static const char* const C60 =
      "522dc1f099567d07f47f37a32a84427d643a8cdcbfe5c0c97598a2bd2555d1aa"
      "8cb08e48590dbb3da7b08b1056828838c5f61e6393ba7a0abcc9f662";
  static const Case kCases[] = {
      {"gcm_tc13", K0, "000000000000000000000000", "", "", "", "530f8afbc74536b9a963b4f1c4cb738b"},
      {"gcm_tc14", K0, "000000000000000000000000", "", "00000000000000000000000000000000",
       "cea7403d4d606b6e074ec5d3baf39d18", "d0d1c8a799996bf0265b98b5d48ab919"},
      {"gcm_tc15", K, IV, "", P, C, "b094dac5d93471bdec1a502270e3cc6c"},
      {"gcm_tc16", K, IV, "feedfacedeadbeeffeedfacedeadbeefabaddad2", P60, C60,
       "76fc6ece0f4e1768cddf8853bb2d551b"},
  };
  for (const Case& c : kCases) {
    uint8_t key[32], iv[12], aad[32], buf[64], ct[64], tag[16], t[16];
    unhex(c.key, key, 32);
    unhex(c.iv, iv, 12);
    const size_t na = unhex(c.aad, aad, sizeof aad);
    const size_t n = unhex(c.pt, buf, sizeof buf);
    unhex(c.ct, ct, sizeof ct);
    unhex(c.tag, tag, 16);
    AesGcm g;
    g.set_key(key);
    g.encrypt(iv, aad, na, buf, n, t);
    const std::string name = c.name;
    CHECK(std::memcmp(buf, ct, n) == 0 && std::memcmp(t, tag, 16) == 0,
          (name + ": ciphertext and tag (McGrew-Viega)").c_str());
    vec(out, name + kv("key", key, 32) + kv("iv", iv, 12) + kv("aad", aad, na) + " pt=" + c.pt +
                 kv("ct", buf, n) + kv("tag", t, 16));
    CHECK(g.decrypt(iv, aad, na, buf, n, tag) && hex(buf, n) == c.pt,
          (name + ": decrypts to the plaintext").c_str());
    // Tamper: the tag must fail and leave the buffer exactly as it was.
    g.encrypt(iv, aad, na, buf, n, t);
    uint8_t keep[64];
    std::memcpy(keep, buf, n);
    t[15] ^= 0x01;
    bool ok = !g.decrypt(iv, aad, na, buf, n, t) && std::memcmp(buf, keep, n) == 0;
    t[15] ^= 0x01;
    if (n) { buf[n / 2] ^= 0x80; ok &= !g.decrypt(iv, aad, na, buf, n, t); buf[n / 2] ^= 0x80; }
    if (na) { aad[0] ^= 0x01; ok &= !g.decrypt(iv, aad, na, buf, n, t); aad[0] ^= 0x01; }
    iv[11] ^= 0x01;
    ok &= !g.decrypt(iv, aad, na, buf, n, t);
    CHECK(ok, (name + ": flipped tag / ciphertext / AAD / IV bit rejected, buffer untouched").c_str());
  }
}

// Partial blocks and AAD lengths around the block size, for the Python cross-check.
HOST_TEST(crypto_gcm_random_vectors) {
  static const size_t kAad[] = {0, 1, 5, 13, 16, 17, 20, 22, 0, 8, 19, 32};
  static const size_t kPt[] = {0, 1, 15, 16, 17, 31, 32, 33, 64, 100, 158, 156};
  bool ok = true;
  for (size_t i = 0; i < sizeof kPt / sizeof kPt[0]; ++i) {
    uint8_t key[32], iv[12], aad[32], pt[160], buf[160], tag[16];
    fill(key, 32); fill(iv, 12); fill(aad, kAad[i]); fill(pt, kPt[i]);
    std::memcpy(buf, pt, kPt[i]);
    AesGcm g;
    g.set_key(key);
    g.encrypt(iv, aad, kAad[i], buf, kPt[i], tag);
    vec(out, "gcm_r" + std::to_string(i) + kv("key", key, 32) + kv("iv", iv, 12) +
                 kv("aad", aad, kAad[i]) + kv("pt", pt, kPt[i]) + kv("ct", buf, kPt[i]) + kv("tag", tag, 16));
    ok &= g.decrypt(iv, aad, kAad[i], buf, kPt[i], tag) && std::memcmp(buf, pt, kPt[i]) == 0;
  }
  CHECK(ok, "AES-256-GCM round trip, 12 random lengths (vectors for the Python check)");
}

// ---- SDLS key derivation ----------------------------------------------------------
HOST_TEST(sdls_key_derivation) {
  uint8_t m[32], tc[32], tm[32];
  master_key(m);
  sdls_derive_key(m, 32, kSdlsLabelTcAe, tc);
  sdls_derive_key(m, 32, kSdlsLabelTmAe, tm);
  CHECK(hex(tc, 32) == kKatTcKey && hex(tm, 32) == kKatTmKey,
        "SDLS K_tc_ae / K_tm_ae = HMAC-SHA-256(master, label) known answers");
  vec(out, "kdf" + kv("master", m, 32) + kv("tc", tc, 32) + kv("tm", tm, 32));
  uint8_t iv[12];
  sdls_iv(kSdlsIvDirTm, 0x1234, 0xA0B0C0D0u, iv);
  CHECK(hex(iv, 12) == "544d123400000000a0b0c0d0", "SDLS IV = dir | epoch | 0 | sn");
}

// ---- TC: AUTH kept byte-exact, service selection ----------------------------------
HOST_TEST(sdls_tc_auth_compat) {
  uint8_t m[32], fr[64];
  master_key(m);
  const size_t len = unhex(kKatTcAuth, fr, sizeof fr);
  Sdls a;
  a.configure(SDLS_SVC_AUTH, m, 32);
  TcFrame f;
  CHECK(a.service() == SDLS_SVC_AUTH && a.spi() == 1 && a.overhead() == 22 &&
        tc_parse(fr, len, f) == TC_OK && a.process(fr, f) == Sdls::SDLS_OK &&
        f.data_len == sizeof kNoop && std::memcmp(f.data, kNoop, sizeof kNoop) == 0 &&
        a.last_sn() == 7, "SDLS AUTH via configure(): v2 frame (known answer) accepted");
  Sdls n;
  n.configure(SDLS_SVC_NONE, m, 32);
  Sdls nk;
  nk.configure(SDLS_SVC_AE, nullptr, 0);
  TcFrame f2;
  tc_parse(fr, len, f2);
  CHECK(!n.enabled() && !nk.enabled() && nk.service() == SDLS_SVC_NONE && nk.overhead() == 0 &&
        n.process(fr, f2) == Sdls::SDLS_DISABLED && n.auth_failures() == 0,
        "SDLS NONE (or AE without a key) is disabled and counts no failures");
  CHECK(std::strcmp(Sdls::result_name(Sdls::SDLS_BAD_IV), "bad IV") == 0 &&
        std::strcmp(Sdls::result_name(Sdls::SDLS_REPLAY), "REPLAY") == 0, "SDLS result names");
}

// ---- TC: authenticated encryption ---------------------------------------------------
HOST_TEST(sdls_tc_ae) {
  uint8_t m[32];
  master_key(m);
  // TC v1 AD frame, SCID 0x0A7, VC 0, N(S) 0, length 5 + 30 + 7 + 2 = 44.
  uint8_t hdr[5];
  put_u16(hdr, kSpacecraftId & 0x3FF);
  put_u16(hdr + 2, (uint16_t)(5 + kSdlsAeOverhead + sizeof kNoop + 2 - 1));
  hdr[4] = 0;
  uint8_t orig[64], fr[64];
  const size_t len = build_tc_ae(orig, hdr, 5, 7, kNoop, sizeof kNoop, m);
  CHECK(len == 44 && hex(orig, len) == kKatTcAe, "SDLS TC AE frame equals the shared known answer");
  vec(out, "tc_ae_v1" + kv("master", m, 32) + kv("hdr_len", 5) + kv("sn", 7) + kv("frame", orig, len) +
               kv("plain", kNoop, sizeof kNoop));

  Sdls rx;
  rx.configure(SDLS_SVC_AE, m, 32, 1);
  CHECK(rx.enabled() && rx.service() == SDLS_SVC_AE && rx.spi() == kSdlsSpiTcAe && rx.overhead() == 30,
        "SDLS TC AE configured: SPI 2, 30 octets overhead");

  // Tampering on a fresh receiver: every failure leaves last_sn and the buffer alone.
  struct Tamper { size_t off; uint8_t mask; Sdls::Result want; const char* what; };
  const Tamper kT[] = {
      {5 + 14 + 3, 0x01, Sdls::SDLS_BAD_MAC, "ciphertext bit"},
      {4, 0x01, Sdls::SDLS_BAD_MAC, "AAD bit (primary header N(S))"},
      {5 + 13, 0x01, Sdls::SDLS_BAD_MAC, "AAD bit (IV sn 7 -> 6)"},
      {5 + 13, 0x0F, Sdls::SDLS_BAD_MAC, "IV sn raised to 8"},
      {len - 3, 0x80, Sdls::SDLS_BAD_MAC, "tag bit"},
      {5 + 2, 0x01, Sdls::SDLS_BAD_IV, "IV direction octet"},
      {5 + 1, 0x03, Sdls::SDLS_BAD_SPI, "SPI 2 -> 1"},
  };
  for (const Tamper& t : kT) {
    std::memcpy(fr, orig, len);
    fr[t.off] ^= t.mask;
    uint8_t keep[64];
    std::memcpy(keep, fr, len);
    uint8_t* d = nullptr;
    size_t dl = 0;
    const Sdls::Result r = rx.process(fr, 5, len - 5 - 2, d, dl);
    CHECK(r == t.want && rx.last_sn() == 0 && std::memcmp(fr, keep, len) == 0,
          (std::string("SDLS TC AE rejects a flipped ") + t.what + ", state and buffer untouched").c_str());
  }
  CHECK(rx.auth_failures() == sizeof kT / sizeof kT[0], "SDLS TC AE failures counted");
  uint8_t* d = nullptr;
  size_t dl = 0;
  std::memcpy(fr, orig, len);
  CHECK(rx.process(fr, 5, 20, d, dl) == Sdls::SDLS_BAD_LENGTH, "SDLS TC AE short frame rejected");

  // The authentic frame through the TC v1 path (tc_parse -> process), decrypted in place.
  std::memcpy(fr, orig, len);
  TcFrame f;
  CHECK(tc_parse(fr, len, f) == TC_OK && rx.process(fr, f) == Sdls::SDLS_OK && rx.last_sn() == 7 &&
        f.data == fr + 19 && f.data_len == sizeof kNoop && std::memcmp(f.data, kNoop, sizeof kNoop) == 0,
        "SDLS TC AE frame accepted after the failures, decrypted in place");
  std::memcpy(fr, orig, len);
  TcFrame f2;
  tc_parse(fr, len, f2);
  CHECK(rx.process(fr, f2) == Sdls::SDLS_REPLAY && std::memcmp(fr, orig, len) == 0 && rx.last_sn() == 7,
        "SDLS TC AE replay rejected, buffer untouched");
  uint8_t auth[64];
  const size_t alen = unhex(kKatTcAuth, auth, sizeof auth);
  TcFrame f3;
  tc_parse(auth, alen, f3);
  // (36 octets: too short for AE, so it fails the length check before the SPI check)
  CHECK(rx.process(auth, f3) == Sdls::SDLS_BAD_LENGTH && rx.last_sn() == 7,
        "SDLS TC AE receiver rejects an AUTH-format frame");

  // USLP TC frame: 8-octet TFPH (N(S) in the VCF count) is the AAD, the TFDF the plaintext.
  uint8_t tfdf[1 + sizeof kNoop] = {0xE0};       // rule '111', UPID 0 (space packet)
  std::memcpy(tfdf + 1, kNoop, sizeof kNoop);
  const size_t ulen = 8 + kSdlsAeOverhead + sizeof tfdf + 2;
  uint8_t uh[8];
  uslp_header(uh, true, 0, (uint16_t)ulen, false, 5);
  uint8_t uorig[64];
  build_tc_ae(uorig, uh, 8, 9, tfdf, sizeof tfdf, m);
  vec(out, "tc_ae_uslp" + kv("master", m, 32) + kv("hdr_len", 8) + kv("sn", 9) + kv("frame", uorig, ulen) +
               kv("plain", tfdf, sizeof tfdf));
  std::memcpy(fr, uorig, ulen);
  fr[7] ^= 0x01;                                 // VCF count is authenticated
  CHECK(rx.process(fr, 8, ulen - 10, d, dl) == Sdls::SDLS_BAD_MAC && rx.last_sn() == 7,
        "SDLS TC AE (USLP) rejects a flipped VCF count");
  std::memcpy(fr, uorig, ulen);
  CHECK(rx.process(fr, 8, ulen - 10, d, dl) == Sdls::SDLS_OK && rx.last_sn() == 9 && d == fr + 22 &&
        dl == sizeof tfdf && std::memcmp(d, tfdf, dl) == 0,
        "SDLS TC AE (USLP TFPH 8 as AAD) accepted, TFDF recovered");
}

// ---- TM: AE and the null SA -----------------------------------------------------------
HOST_TEST(sdls_tm_protect) {
  uint8_t m[32], ktm[32];
  master_key(m);
  sdls_derive_key(m, 32, kSdlsLabelTmAe, ktm);
  AesGcm ref;                                    // independent receiver
  ref.set_key(ktm);
  const size_t kHdr = kTmPriHdrLen, kData = kTmDataLen - kSdlsAeOverhead;   // 6, 158
  const size_t kEnd = kTmFrameLen - kTmOcfLen - kTmFecfLen;                  // 194
  CHECK(kData == 158 && kHdr + kSdlsAeHdrLen + kData + kSdlsAeTagLen == kEnd, "TM AE geometry 6|14|158|16");

  SdlsTm none;
  none.init(SDLS_SVC_AE, nullptr, 0);
  uint8_t fr[kTmFrameLen], keep[kTmFrameLen];
  fill(fr, sizeof fr);
  std::memcpy(keep, fr, sizeof fr);
  CHECK(none.service() == SDLS_SVC_NONE && none.state() == SdlsTm::TM_OFF && none.header_len() == 0 &&
        none.trailer_len() == 0 && none.ready() && none.protect(fr, kHdr, kTmDataLen) &&
        std::memcmp(fr, keep, sizeof fr) == 0, "SDLS TM without a key: NONE, no-op, frames flow");

  SdlsTm tm;
  tm.init(SDLS_SVC_AE, m, 32);
  CHECK(tm.service() == SDLS_SVC_AE && tm.header_len() == 14 && tm.trailer_len() == 16 &&
        tm.state() == SdlsTm::TM_WAIT_EPOCH && !tm.ready(), "SDLS TM AE: waits for an epoch");
  tm_header(fr, 0, 0, 0, 0);
  fill(fr + kHdr + kSdlsAeHdrLen, kData);
  std::memcpy(keep, fr, sizeof fr);
  CHECK(!tm.protect(fr, kHdr, kData) && std::memcmp(fr, keep, sizeof fr) == 0 && tm.frames_refused() == 1,
        "SDLS TM AE refuses to protect before the epoch (frame untouched)");

  // Epoch timeout -> null SA, exactly once.
  const bool early = tm.poll_epoch_timeout(119, 120);
  const bool fire = tm.poll_epoch_timeout(120, 120);
  const bool again = tm.poll_epoch_timeout(121, 120);
  CHECK(!early && fire && !again && tm.state() == SdlsTm::TM_UNPROTECTED && tm.ready(),
        "SDLS TM null-SA fallback engages once at the epoch timeout");
  uint8_t plain[kData];
  std::memcpy(plain, fr + kHdr + kSdlsAeHdrLen, kData);
  bool zero = true;
  CHECK(tm.protect(fr, kHdr, kData), "SDLS TM null SA protect()");
  for (size_t i = 0; i < kSdlsAeHdrLen; ++i) zero &= fr[kHdr + i] == 0;
  for (size_t i = 0; i < kSdlsAeTagLen; ++i) zero &= fr[kHdr + kSdlsAeHdrLen + kData + i] == 0;
  CHECK(zero && std::memcmp(fr + kHdr + kSdlsAeHdrLen, plain, kData) == 0 && tm.frames_null() == 1,
        "SDLS TM null SA: SPI 0, IV 0, MAC 0, data in clear");
  tm_trailer(fr);
  vec(out, "tm_null" + kv("hdr_len", kHdr) + kv("prot_end", kEnd) + kv("frame", fr, sizeof fr) +
               kv("plain", plain, kData));

  CHECK(!tm.set_epoch(0) && tm.state() == SdlsTm::TM_UNPROTECTED, "SDLS TM epoch 0 refused");
  CHECK(tm.set_epoch(5) && tm.state() == SdlsTm::TM_PROTECTED && tm.epoch() == 5 && tm.last_sn() == 0,
        "SDLS TM epoch 5 ends the fallback");

  // A sequence of protected frames: epoch 5 sn 1..4, (re-sent epoch 5 is a no-op,
  // epoch 4 refused), epoch 6 sn 1 (TM), epoch 6 sn 2 (USLP).
  int idx = 0;
  bool all_ok = true;
  auto emit = [&](size_t hdr_len, size_t data_len, uint16_t want_epoch, uint32_t want_sn) {
    uint8_t p[kTmFrameLen], pt[kTmDataLen];
    fill(p, sizeof p);
    if (hdr_len == kHdr) {
      tm_header(p, 0, (uint8_t)idx, (uint8_t)idx, 0);
    } else {                                     // USLP TFPH 8 + TFDF header (rule 000, UPID 0, FHP 0)
      uslp_header(p, false, 0, kTmFrameLen, true, (uint8_t)idx);
      p[hdr_len + kSdlsAeHdrLen] = 0x00;
      put_u16(p + hdr_len + kSdlsAeHdrLen + 1, 0x0000);
    }
    std::memcpy(pt, p + hdr_len + kSdlsAeHdrLen, data_len);
    const bool ok = tm.protect(p, hdr_len, data_len);
    uint8_t iv[12];
    sdls_iv(kSdlsIvDirTm, want_epoch, want_sn, iv);
    uint8_t* c = p + hdr_len + kSdlsAeHdrLen;
    const bool hdr_ok = get_u16(p + hdr_len) == kSdlsSpiTmAe && std::memcmp(p + hdr_len + 2, iv, 12) == 0;
    uint8_t dec[kTmDataLen];
    std::memcpy(dec, c, data_len);
    const bool dec_ok = ref.decrypt(iv, p, hdr_len + kSdlsAeHdrLen, dec, data_len, c + data_len) &&
                        std::memcmp(dec, pt, data_len) == 0 && std::memcmp(c, pt, data_len) != 0;
    tm_trailer(p);
    vec(out, "tm_ae_" + std::to_string(idx) + kv("hdr_len", hdr_len) + kv("prot_end", kEnd) +
                 kv("epoch", want_epoch) + kv("sn", want_sn) + kv("frame", p, sizeof p) + kv("plain", pt, data_len));
    ++idx;
    all_ok &= ok && hdr_ok && dec_ok && tm.last_sn() == want_sn;
  };
  emit(kHdr, kData, 5, 1);
  emit(kHdr, kData, 5, 2);
  emit(kHdr, kData, 5, 3);
  const bool same = tm.set_epoch(5);
  emit(kHdr, kData, 5, 4);
  const bool lower = tm.set_epoch(4);
  const bool higher = tm.set_epoch(6);
  emit(kHdr, kData, 6, 1);
  const size_t kUslpData = kTmFrameLen - 8 - kSdlsAeOverhead - kTmOcfLen - kTmFecfLen;   // 156
  emit(8, kUslpData, 6, 2);
  CHECK(all_ok && tm.frames_protected() == 6,
        "SDLS TM AE frames (TM 6|14|158|16 and USLP 8|14|156|16) decrypt with an independent GCM");
  CHECK(same && !lower && higher && tm.epoch() == 6,
        "SDLS TM epoch rules: same = no-op (sn continues), lower refused, higher restarts sn at 1");
}
