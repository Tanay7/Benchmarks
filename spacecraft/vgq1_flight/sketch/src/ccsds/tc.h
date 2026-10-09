// =============================================================================
//  TC path (uplink): CLTU -> BCH(63,56) decoding -> TC Transfer Frame -> FARM-1
//
//    CLTU (231.0-B-4):  EB 90 | n x [7 info + 1 parity] codeblocks | C5C5C5C5C5C5C579
//      BCH(63,56) generator g(x) = x^7 + x^6 + x^2 + 1; the 7 parity bits are
//      complemented and followed by a filler bit '0'. Decoded here in
//      Single-Error-Correction mode; the tail sequence is recognised because it
//      is designed to be an uncorrectable codeblock.
//    TC frame (232.0-B-4): 5-octet header | data | FECF (CRC-16)
//    FARM-1   (232.1-B-2): sequence-controlled acceptance (Type-AD), bypass
//                          (Type-BD) and control commands (Type-BC: Unlock,
//                          Set V(R)); status reported in the CLCW.
// =============================================================================
#pragma once
#include "ccsds_types.h"
#include "tm.h"

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

struct TcFrame {
  bool bypass = false;          // Type-B (bypass)
  bool control = false;         // Control Command (Type-BC)
  uint16_t scid = 0;
  uint8_t vcid = 0;
  uint16_t length = 0;          // total frame octets
  uint8_t seq = 0;              // N(S)
  const uint8_t* data = nullptr;  // points into caller buffer
  uint16_t data_len = 0;
};

enum TcParseResult : uint8_t {
  TC_OK = 0, TC_TOO_SHORT, TC_BAD_VERSION, TC_BAD_SCID, TC_BAD_LENGTH, TC_BAD_FECF
};

TcParseResult tc_parse(const uint8_t* buf, size_t len, TcFrame& f);

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
