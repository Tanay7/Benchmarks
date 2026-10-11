// =============================================================================
//  Rice: CCSDS 121.0-B-3 Lossless Data Compression (Blue Book, August 2020),
//  flight ENCODER
//
//  The standard's source coder (§2.2): a predictive preprocessor followed by the
//  Adaptive Entropy Coder. Input: unsigned n-bit samples. Output: a sequence of
//  Coded Data Sets (CDS, §5.2), MSB first (§1.5.2), zero fill bits appended to
//  the next octet boundary (§5.3.2.1 d/e). No CIP (§6) and no file header (§7):
//  the decoder knows the parameters a priori (§5.3.2.2), they are fixed per APID.
//
//  Preprocessor (§4):
//    unit-delay predictor (§4.2.5): x^_i = x_(i-1); for the first sample of a
//    reference sample interval the prediction is the sample itself, and that
//    sample passes uncoded as the n-bit reference sample (§4.2.6, §5.2.2).
//    Prediction error mapper (§4.4), unsigned data (x_min = 0, x_max = 2^n - 1):
//      theta = min(x^, x_max - x^),  Delta = x - x^
//      delta = 2 Delta              for 0 <= Delta <= theta
//            = 2 |Delta| - 1        for -theta <= Delta < 0
//            = theta + |Delta|      otherwise
//    A reference sample is inserted every r blocks (§4.3, r <= 4096); fewer
//    than r blocks may follow the last one at the end of the input (B-3 change).
//
//  Adaptive Entropy Coder (§3), one decision per block of J samples:
//    Zero-Block (§3.5): consecutive all-zero blocks (a reference block counts
//      when its J-1 mapped samples are zero, §3.5.1) are coded by ONE CDS per
//      run within a segment of 64 blocks (the last segment of a reference
//      interval or of the input may be shorter, §3.5.2); FS codeword per
//      Table 3-2: 1..4 blocks -> m-1 zeros + 1; ROS (the rest of the segment is
//      5 or more zero blocks, also in short segments, §3.5.3) -> 0000 1;
//      5..63 blocks -> m zeros + 1. Always selected for zero blocks (§3.7.2).
//    Second-Extension (§3.4): pairs -> gamma = (a + b)(a + b + 1)/2 + b, FS
//      coded; in a reference block delta_1 = 0 is used (§2.2, §5.2.6).
//    Split-sample k = 0 (Fundamental Sequence, §3.2) .. k_max (§3.3): FS codes
//      of delta >> k for the J (J-1) samples, then their k LSBs (Fig. 5-1).
//    No-compression (§3.6): the J (J-1) samples, n bits each.
//    Selection (§3.7): fewest bits; ties -> no-compression, then
//      second-extension, then the smallest k (§3.7.4). This exact tie order is
//      what reproduces the official CCSDS 121.0-B-2 test data octet for octet
//      (tools/host_test/test_rice.cpp; the common reference library libaec
//      reproduces only their lengths for n > 4 because its k search keeps a
//      larger k on ties).
//    Option ID (Table 5-1): id_len = 3 (n <= 8), 4 (8 < n <= 16); Restricted
//      set (n <= 4 only): 1 (n = 1, 2) or 2 (n = 3, 4). Split option k is ID
//      k + 1 (k <= 2^id_len - 3), no-compression is all ones; Zero-Block and
//      Second-Extension use id_len + 1 bits: all zeros / zeros then a 1.
//    CDS layout (Fig. 5-1 .. 5-4): ID | [n-bit reference sample] | codes.
//
//  Mission parameters (kRiceMission, contract 24.3): n = 16, J = 16, r = 128,
//  Basic option set. Used for SPECBURST (APID 0x024) encoding 1: the burst's
//  raw u16 counts in CHANNEL-MAJOR order (each channel's time series
//  contiguous, so the unit-delay predictor sees temporal correlation).
//  A count that is not a multiple of J is padded (§2.2) by repeating the last
//  sample, which maps to delta = 0 ("compressed data size will be minimized");
//  the decoder discards the padding because it knows the sample count.
//
//  Worst case (rice_max_encoded_len): every block that is not all-zero costs at
//  most id_len + J n bits (no-compression wins ties, and a reference block has
//  1 reference + (J-1) samples); a zero run of c >= 1 blocks costs at most
//  id_len + 1 + n + max(4, c + 1) <= c (id_len + J n) bits for J >= 8. Hence
//      bytes <= ceil(ceil(count / J) * (id_len + J n) / 8)
//  (mission: 260 bits per 16-sample block, i.e. 130 octets for 54 samples
//  against 108 raw: incompressible input EXPANDS slightly; send raw then).
//
//  Pure C++ (no Arduino, no heap, no recursion, every loop bounded by
//  kRiceMaxSamples or a constant). Stack: 384 octets at -O2 / 320 at -Os for
//  rice_encode() (gcc -fstack-usage on x86-64, 64-bit pointers; the 32-bit MCU
//  needs less - VERIFY with the ARM build's -fstack-usage). Work per block is
//  about (k_max + 4) J simple operations (mission: ~270), i.e. tens of
//  microseconds for a 54-sample SPECBURST packet at 160 MHz (estimate, VERIFY
//  on target; a 4096-sample call is ~75 times that). Host-tested by
//  tools/host_test/test_rice.cpp (official CCSDS vectors, test-local decoder)
//  and decoded independently by the ground (ground/dssq/ccsds/rice.py,
//  ground/tests/test_rice.py).
// =============================================================================
#pragma once
#include "ccsds_types.h"

namespace ccsds {

// Limits. kRiceMaxBits: this encoder takes uint16_t samples (the standard
// allows n <= 32, §3.1.6). kRiceMaxSamples bounds one call (loop bound and
// worst-case run time); compress larger data sets in independent chunks,
// which also limits how far a channel error can propagate (§2.3).
static const uint8_t  kRiceMaxBits       = 16;
static const uint8_t  kRiceMaxBlock      = 64;     // J in {8, 16, 32, 64} (§3.1.6)
static const uint16_t kRiceMaxRsi        = 4096;   // r, blocks (§4.3)
static const uint8_t  kRiceSegmentBlocks = 64;     // zero-block segment (§3.5.2)
static const size_t   kRiceMaxSamples    = 4096;

struct RiceConfig {
  uint8_t  bits;          // n: sample resolution, 1..kRiceMaxBits
  uint8_t  block;         // J: samples per block, 8 | 16 | 32 | 64
  uint16_t rsi;           // r: blocks per reference sample interval, 1..4096
  bool     restricted;    // Restricted code-option set (§5.2.1.1); only n <= 4
};

// The mission's parameters (contract 24.3); the ground decoder's defaults
// (dssq.ccsds.rice.MISSION) are identical.
static const RiceConfig kRiceMission = {16, 16, 128, false};

enum RiceStatus : uint8_t {
  RICE_OK           = 0,
  RICE_ERR_CONFIG   = 1,   // parameter outside the standard or this encoder
  RICE_ERR_ARG      = 2,   // null pointer, count 0 or > kRiceMaxSamples
  RICE_ERR_SAMPLE   = 3,   // a sample >= 2^n (out of range for the resolution)
  RICE_ERR_OVERFLOW = 4,   // the encoding does not fit in `cap` octets
};

// True when `c` is a legal parameter set for this encoder.
bool rice_config_ok(const RiceConfig& c);

// Code-option ID length in bits (Table 5-1), 0 for an illegal configuration.
uint8_t rice_id_len(const RiceConfig& c);

// Upper bound on rice_encode()'s output for `count` samples (see header);
// exact for incompressible input whose count is a multiple of J. Returns 0 for
// an illegal configuration or count outside 1..kRiceMaxSamples.
size_t rice_max_encoded_len(const RiceConfig& c, size_t count);

// Compile-time form for the mission parameters, for sizing static buffers:
// 4-bit ID + 16 x 16 bits per block.
constexpr size_t rice_mission_max_len(size_t count) {
  return ((count + 15u) / 16u * (4u + 16u * 16u) + 7u) / 8u;
}

// Encodes x[0..count-1] (each < 2^c.bits) into out[0..cap-1]. On RICE_OK,
// *out_len = octets written (>= 1). On any error *out_len = 0 and the content
// of `out` is unspecified, but nothing is ever written at or beyond out[cap].
// Validation happens before encoding starts (config, pointers, count, every
// sample range), so RICE_ERR_OVERFLOW is the only error that can follow
// partial output. The call is self-contained: each output stream starts with
// a reference sample and needs no state from earlier calls.
RiceStatus rice_encode(const RiceConfig& c, const uint16_t* x, size_t count,
                       uint8_t* out, size_t cap, size_t* out_len);

}  // namespace ccsds
