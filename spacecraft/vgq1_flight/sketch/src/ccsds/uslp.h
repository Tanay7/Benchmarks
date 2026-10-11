// =============================================================================
//  USLP: Unified Space Data Link Protocol (CCSDS 732.1-B-2), field packing
//
//  The downlink framer (TmFramer, framing mode USLP = config.h
//  VGQ_DOWNLINK_FRAMING 1) and the uplink parser (tc_parse, which auto-detects
//  USLP next to TC 232.0-B-4 frames) share these helpers. Bit order is the CCSDS
//  one: bit 0 = most significant bit of the first octet, big-endian fields.
//
//  Transfer Frame Primary Header (TFPH, 732.1-B-2 §4.1.2):
//    TFVN 4 '1100' | SCID 16 | source-or-destination 1 | VCID 6 | MAP ID 4 |
//    end-of-frame-primary-header 1 | frame length 16 (octets - 1) |
//    bypass/sequence control 1 | protocol control command (PCC) 1 | spare 2 |
//    OCF flag 1 | VCF count length 3 | VCF count 0..7 octets
//  Transfer Frame Data Field header (§4.1.4.2):
//    TFDZ construction rule 3 | USLP protocol identifier (UPID) 5 |
//    first header pointer 16 (only for the fixed-length rules 000/001/010)
//
//  Mission profile (docs/04_Space_Data_Link_ICD.md):
//    TM  (200, fixed): TFPH 8 | [SDLS hdr] | TFDF hdr 3 (rule 000, UPID 0, FHP) |
//                      TFDZ 183 - SDLS | [SDLS trailer] | OCF 4 (CLCW) | FECF 2
//    OID (200, fixed): VCID 63, rule 000, UPID 31 (idle data), FHP 0xFFFF,
//                      TFDZ = clear-text identification; never SDLS
//    TC  (<= 64):      TFPH 7 (+1 VCF count octet = N(S) on Type-AD) | [SDLS hdr] |
//                      TFDF hdr 1 (rule 111, UPID 0 packet / 1 COP-1 directive) |
//                      data | [SDLS trailer] | FECF 2
//  SCID 0x00A7 in the 16-bit field: the same number as the 10-bit TM/TC SCID.
// =============================================================================
#pragma once
#include "ccsds_types.h"

namespace ccsds {

static const uint8_t  kUslpTfvn        = 0xC;    // '1100'
static const size_t   kUslpFixedHdrLen = 7;      // TFPH without the VCF count
static const size_t   kUslpTmHdrLen    = 8;      // downlink TFPH: 1-octet VCF count
static const size_t   kUslpTfdfHdrLen  = 3;      // rule|UPID + FHP (fixed-length rules)
static const size_t   kUslpTcTfdfHdrLen = 1;     // rule|UPID (variable-length rule 111)
static const size_t   kUslpTfdzLen = kTmFrameLen - kUslpTmHdrLen - kUslpTfdfHdrLen -
                                     kTmOcfLen - kTmFecfLen;              // 183
static const uint16_t kUslpFhpNone     = 0xFFFF; // no packet header starts in this TFDZ
static const uint8_t  kUslpVcidOid     = 63;     // reserved for Only-Idle-Data frames

// TFDZ construction rules (§4.1.4.2.2) used by this mission
static const uint8_t kUslpRulePackets = 0;       // '000' packets spanning fixed-length TFDZs
static const uint8_t kUslpRuleNoSeg   = 7;       // '111' no segmentation (variable-length TC)
// USLP protocol identifiers (SANA registry)
static const uint8_t kUslpUpidPackets = 0;       // Space Packets / Encapsulation Packets
static const uint8_t kUslpUpidCop1    = 1;       // COP-1 control commands
static const uint8_t kUslpUpidIdle    = 31;      // idle data

struct UslpHeader {
  uint16_t scid = kSpacecraftId;
  bool     dest = false;        // 0: SCID is the source (downlink), 1: destination (uplink)
  uint8_t  vcid = 0;            // 6 bits
  uint8_t  map_id = 0;          // 4 bits
  bool     truncated = false;   // end-of-frame-primary-header flag (1 = 4-octet header)
  uint16_t frame_len = 0;       // TOTAL frame octets (the field carries frame_len - 1)
  bool     bypass = false;      // 0 sequence-controlled (Type-A), 1 expedited (Type-B)
  bool     pcc = false;         // 1: the TFDF carries protocol control information
  bool     ocf = false;         // an Operational Control Field precedes the FECF
  uint8_t  vcf_len = 0;         // VC frame count length, octets (0..7)
  uint32_t vcf_count = 0;       // low 32 bits of the VC frame count
};

inline bool uslp_is_uslp(const uint8_t* frame) { return (frame[0] >> 4) == kUslpTfvn; }
inline uint8_t uslp_tfdf_byte(uint8_t rule, uint8_t upid) {
  return (uint8_t)(((rule & 7u) << 5) | (upid & 0x1Fu));
}

// Writes a full (non-truncated) TFPH; returns its length, 7 + vcf_len.
size_t uslp_put_header(uint8_t* p, const UslpHeader& h);
// Reads a full TFPH. Returns its length (7 + VCF count length), or 0 when `len`
// is too short, the TFVN is not '1100' or the header is truncated (EoFPH = 1;
// h.truncated is then set — fixed-length and TC frames never use it here).
size_t uslp_get_header(const uint8_t* p, size_t len, UslpHeader& h);

}  // namespace ccsds
