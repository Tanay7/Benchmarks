// =============================================================================
//  TC path (uplink): CLTU -> BCH(63,56) decoding -> TC Transfer Frame -> FARM-1
//
//    CLTU (231.0-B-4):  EB 90 | n x [7 info + 1 parity] codeblocks | C5C5C5C5C5C5C579
//      BCH(63,56) generator g(x) = x^7 + x^6 + x^2 + 1; the 7 parity bits are
//      complemented and followed by a filler bit '0'. Decoded here in
//      Single-Error-Correction mode; the tail sequence is recognised because it
//      is designed to be an uncorrectable codeblock.
//    TC frame (232.0-B-4): 5-octet header | data | FECF (CRC-16)
//    USLP TC  (732.1-B-2): TFPH 7 (+1 VCF count = N(S) on Type-AD) | TFDF header
//                          1 (rule 111, UPID 0 packet / 1 COP-1) | data | FECF;
//                          accepted next to TC frames (auto-detected, uslp.h)
//    FARM-1   (232.1-B-2): sequence-controlled acceptance (Type-AD), bypass
//                          (Type-BD) and control commands (Type-BC: Unlock,
//                          Set V(R)); status reported in the CLCW. Runs
//                          unchanged on both formats (USLP: bypass flag, PCC
//                          flag, N(S) = VCF count).
//
//  Order on the spacecraft: cltu_decode -> tc_parse(.., open_tfdf = !SDLS) ->
//  SDLS (AAD = f.hdr_len header octets) -> tc_open_tfdf -> Farm1::on_frame.
//  Under SDLS the USLP TFDF header is inside the protected (encrypted) region,
//  which is why it is opened only after SDLS.
// =============================================================================
#pragma once
#include "ccsds_types.h"
#include "tm.h"
#include "uslp.h"

namespace ccsds {

struct BchStats {
  uint32_t codeblocks_ok = 0;
  uint32_t codeblocks_corrected = 0;
  uint32_t codeblocks_rejected = 0;
};

// Encodes 7 info octets into an 8-octet codeblock (used by host tests / EGSE).
void bch_encode(const uint8_t info[7], uint8_t out[8]);

// Decodes a CLTU found anywhere in `in`. Writes the recovered info octets to
// `out` (cap bytes). Returns number of octets recovered (0 = no CLTU).
size_t cltu_decode(const uint8_t* in, size_t len, uint8_t* out, size_t cap, BchStats& st);

enum TcFormat : uint8_t { TC_FMT_TC = 0, TC_FMT_USLP = 1 };

struct TcFrame {
  bool bypass = false;          // Type-B (bypass); USLP: bypass/sequence control flag
  bool control = false;         // Control Command (Type-BC); USLP: PCC flag
  uint16_t scid = 0;            // 10 bits (TC) / 16 bits (USLP)
  uint8_t vcid = 0;
  uint16_t length = 0;          // total frame octets
  uint8_t seq = 0;              // N(S); USLP: the 1-octet VCF count (0 on Type-B)
  const uint8_t* data = nullptr;  // points into caller buffer
  uint16_t data_len = 0;
  TcFormat format = TC_FMT_TC;
  uint8_t hdr_len = kTcPriHdrLen;  // primary header octets = SDLS AAD length (TC 5, USLP 7/8)
  uint8_t map_id = 0;           // USLP MAP ID
  uint8_t upid = 0;             // USLP UPID, valid once the TFDF is open
  bool tfdf_open = true;        // false while `data` still begins with the USLP TFDF header
};

enum TcParseResult : uint8_t {
  TC_OK = 0, TC_TOO_SHORT, TC_BAD_VERSION, TC_BAD_SCID, TC_BAD_LENGTH, TC_BAD_FECF,
  TC_BAD_HEADER,   // USLP: truncated TFPH, source flag, Type-AD without a 1-octet count, PCC on AD
  TC_BAD_TFDF      // USLP: construction rule not '111' or UPID not matching the PCC flag
};

// Parses one TC frame at `buf` and checks its FECF. The format is detected from
// the version field: '00' = TC (232.0-B-4), '1100' = USLP (732.1-B-2). For a
// USLP frame `data` first covers the whole TFDF; with `open_tfdf` its 1-octet
// header is checked and stripped here as well (pass false when SDLS protects
// the frame, then call tc_open_tfdf() after SDLS). TC frames are always open.
TcParseResult tc_parse(const uint8_t* buf, size_t len, TcFrame& f, bool open_tfdf = true);
// Checks and strips the USLP TFDF header (no-op for TC frames / an open TFDF).
TcParseResult tc_open_tfdf(TcFrame& f);

// FARM-1 receiving-end state machine (one per TC virtual channel).
class Farm1 {
 public:
  enum State : uint8_t { S1_OPEN = 1, S2_WAIT = 2, S3_LOCKOUT = 3 };
  enum Verdict : uint8_t { ACCEPT = 0, DISCARD_RETRANSMIT, DISCARD_NEG_WINDOW,
                           DISCARD_LOCKOUT, ACCEPT_BYPASS, CONTROL_UNLOCK,
                           CONTROL_SET_VR, CONTROL_INVALID };

  explicit Farm1(uint8_t window = 10) : window_(window) {}
  Verdict on_frame(const TcFrame& f);
  void fill_clcw(Clcw& c) const;
  State state() const { return state_; }
  uint8_t vr() const { return vr_; }

 private:
  uint8_t window_;           // FARM sliding window width W (even, 2..254)
  State state_ = S1_OPEN;
  uint8_t vr_ = 0;
  uint8_t farm_b_ = 0;
  bool lockout_ = false, wait_ = false, retransmit_ = false;
};

}  // namespace ccsds
