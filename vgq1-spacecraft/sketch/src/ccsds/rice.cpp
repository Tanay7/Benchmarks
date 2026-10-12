// =============================================================================
//  Rice: CCSDS 121.0-B-3 lossless compression encoder. See rice.h for the
//  algorithm, the section references and the output bound.
// =============================================================================
#include "rice.h"

namespace ccsds {

namespace {

// Bit lengths are compared as uint32_t; this marks an option as unusable.
const uint32_t kNoOption = 0xFFFFFFFFu;
// FS value of the Remainder-Of-Segment codeword 0000 1 (Table 3-2).
const uint32_t kRosFs = 4;

// MSB-first bit writer bounded by `cap`. An attempt to write past the end sets
// `overflow` (sticky) and drops the octet, so the caller can finish a block
// cheaply and report RICE_ERR_OVERFLOW afterwards.
struct BitWriter {
  uint8_t* out;
  size_t   cap;
  size_t   pos;        // whole octets produced so far
  uint32_t acc;        // pending bits, right-aligned (fewer than 8 between calls)
  uint8_t  nacc;       // number of pending bits
  bool     overflow;

  void octet(uint8_t v) {
    if (pos < cap) out[pos++] = v;
    else overflow = true;
  }
  // Appends the `nb` (0..16) low bits of v, most significant first.
  void put(uint32_t v, uint8_t nb) {
    acc = (acc << nb) | (v & ((1u << nb) - 1u));
    nacc = (uint8_t)(nacc + nb);
    while (nacc >= 8) {                      // at most 3 passes (7 + 16 bits)
      nacc = (uint8_t)(nacc - 8);
      octet((uint8_t)(acc >> nacc));
    }
    acc &= (1u << nacc) - 1u;
  }
  // Fundamental Sequence codeword (§3.2): m zeros then a one. m < 2^17 here
  // (a split FS value is < 2^16, a gamma is bounded by the caller).
  void fs(uint32_t m) {
    for (uint32_t i = 0; i < (1u << 17) / 16u && m > 0; ++i) {
      const uint8_t chunk = m > 16u ? (uint8_t)16 : (uint8_t)m;
      put(0, chunk);
      m -= chunk;
    }
    put(1, 1);
  }
  // Zero fill to the next octet boundary (§5.3.2.1 d, e).
  void pad() {
    if (nacc > 0) put(0, (uint8_t)(8u - nacc));
  }
};

uint8_t id_len_of(uint8_t n, bool restricted) {
  if (restricted) return n <= 2 ? 1 : 2;     // Table 5-1, Restricted columns
  return n <= 8 ? 3 : 4;                     // Basic: n <= 8, 8 < n <= 16
}

// Prediction error mapper, §4.4, unsigned samples. Result <= xmax.
uint16_t map_error(uint32_t x, uint32_t pred, uint32_t xmax) {
  const uint32_t theta = pred < xmax - pred ? pred : xmax - pred;
  if (x >= pred) {
    const uint32_t d = x - pred;
    return (uint16_t)(d <= theta ? 2u * d : theta + d);
  }
  const uint32_t d = pred - x;
  return (uint16_t)(d <= theta ? 2u * d - 1u : theta + d);
}

// Encoder state that persists across blocks of one call.
struct Encoder {
  BitWriter w;
  uint8_t   n;           // resolution
  uint8_t   j;           // block size
  uint8_t   id_len;
  int8_t    kmax;        // largest split k; -1 = none (Restricted, n <= 2)
  uint32_t  run;         // all-zero blocks pending in the current segment
  bool      run_ref;     // the pending run starts with a reference block
  uint16_t  run_ref_sample;

  // One Zero-Block CDS for `run` blocks (§3.5, Table 3-2, Fig. 5-3).
  void emit_zero_run(bool ros) {
    w.put(0, (uint8_t)(id_len + 1u));         // Zero-Block ID: id_len + 1 zeros
    if (run_ref) w.put(run_ref_sample, n);
    if (ros) w.fs(kRosFs);
    else w.fs(run >= 5u ? run : run - 1u);    // 1..4 -> m-1, 5..63 -> m zeros
    run = 0;
    run_ref = false;
  }

  // Codes one block that is not all-zero (§3.7.3, §3.7.4). d[0] = 0 in a
  // reference block (delta_1 = 0 for Second-Extension, §2.2); split and
  // no-compression code d[first..j-1].
  void emit_block(const uint16_t* d, bool ref, uint16_t ref_sample) {
    const uint32_t first = ref ? 1u : 0u;
    const uint32_t count = (uint32_t)j - first;
    const uint32_t nc_len = count * n;        // <= 1024 bits

    // Split-sample options k = 0..kmax: FS part sum(d >> k) + (k + 1) per sample.
    uint32_t best_len = kNoOption;
    int8_t best_k = -1;
    for (int8_t k = 0; k <= kmax; ++k) {      // kmax <= 13
      uint32_t len = count * (uint32_t)(k + 1);
      for (uint32_t i = first; i < j; ++i) len += (uint32_t)(d[i] >> k);
      if (len < best_len) {                   // strict: the smallest k wins ties
        best_len = len;
        best_k = k;
      }
    }

    // Second-Extension: 1 extra ID bit + sum(gamma + 1). Abandoned as soon as it
    // cannot beat no-compression (which wins a tie), which also keeps gamma
    // small: a + b <= nc_len -> gamma < 2^20.
    uint32_t se_len = 1;
    for (uint32_t i = 0; i < j; i += 2) {
      const uint32_t s = (uint32_t)d[i] + d[i + 1u];
      if (s > nc_len) { se_len = kNoOption; break; }
      se_len += s * (s + 1u) / 2u + d[i + 1u] + 1u;
      if (se_len >= nc_len) { se_len = kNoOption; break; }
    }

    if (nc_len <= best_len && nc_len <= se_len) {           // §3.7.4 a)
      w.put((1u << id_len) - 1u, id_len);
      if (ref) w.put(ref_sample, n);
      for (uint32_t i = first; i < j; ++i) w.put(d[i], n);
    } else if (se_len <= best_len) {                        // §3.7.4 b)
      w.put(1, (uint8_t)(id_len + 1u));
      if (ref) w.put(ref_sample, n);
      for (uint32_t i = 0; i < j; i += 2) {
        const uint32_t s = (uint32_t)d[i] + d[i + 1u];
        w.fs(s * (s + 1u) / 2u + d[i + 1u]);
      }
    } else {                                                // §3.7.4 c)
      const uint8_t k = (uint8_t)best_k;
      w.put((uint32_t)k + 1u, id_len);
      if (ref) w.put(ref_sample, n);
      for (uint32_t i = first; i < j; ++i) w.fs((uint32_t)(d[i] >> k));
      if (k > 0) {
        for (uint32_t i = first; i < j; ++i) w.put(d[i], k);   // put() keeps the k LSBs
      }
    }
  }
};

}  // namespace

bool rice_config_ok(const RiceConfig& c) {
  if (c.bits < 1 || c.bits > kRiceMaxBits) return false;
  if (c.block != 8 && c.block != 16 && c.block != 32 && c.block != 64) return false;
  if (c.rsi < 1 || c.rsi > kRiceMaxRsi) return false;
  if (c.restricted && c.bits > 4) return false;           // §5.2.1.1
  return true;
}

uint8_t rice_id_len(const RiceConfig& c) {
  return rice_config_ok(c) ? id_len_of(c.bits, c.restricted) : 0;
}

size_t rice_max_encoded_len(const RiceConfig& c, size_t count) {
  if (!rice_config_ok(c) || count < 1 || count > kRiceMaxSamples) return 0;
  const size_t blocks = (count + c.block - 1u) / c.block;
  const size_t block_bits = (size_t)id_len_of(c.bits, c.restricted) + (size_t)c.block * c.bits;
  return (blocks * block_bits + 7u) / 8u;               // <= 526 336 / 8: no overflow
}

RiceStatus rice_encode(const RiceConfig& c, const uint16_t* x, size_t count,
                       uint8_t* out, size_t cap, size_t* out_len) {
  if (out_len == nullptr) return RICE_ERR_ARG;
  *out_len = 0;
  if (!rice_config_ok(c)) return RICE_ERR_CONFIG;
  if (x == nullptr || out == nullptr || count < 1 || count > kRiceMaxSamples) return RICE_ERR_ARG;

  // Range-check every input before producing anything (external data).
  const uint32_t xmax = (1u << c.bits) - 1u;
  for (size_t i = 0; i < count; ++i) {
    if (x[i] > xmax) return RICE_ERR_SAMPLE;
  }

  Encoder e;
  e.w.out = out;
  e.w.cap = cap;
  e.w.pos = 0;
  e.w.acc = 0;
  e.w.nacc = 0;
  e.w.overflow = false;
  e.n = c.bits;
  e.j = c.block;
  e.id_len = id_len_of(c.bits, c.restricted);
  e.kmax = (int8_t)((1 << e.id_len) - 3);              // Table 5-1: IDs 1..2^id_len - 2
  e.run = 0;
  e.run_ref = false;
  e.run_ref_sample = 0;

  const size_t blocks = (count + c.block - 1u) / c.block;   // <= kRiceMaxSamples / 8
  uint16_t d[kRiceMaxBlock];
  uint32_t pred = 0;
  uint16_t ref_sample = 0;

  for (size_t b = 0; b < blocks && !e.w.overflow; ++b) {
    const size_t rsi_pos = b % c.rsi;
    const bool ref = rsi_pos == 0;
    // A block closes its segment at 64 blocks into the reference interval, at
    // the end of the interval, or at the end of the input (§3.5.2).
    const bool seg_end = (rsi_pos % kRiceSegmentBlocks) == kRiceSegmentBlocks - 1u ||
                         rsi_pos == (size_t)c.rsi - 1u || b == blocks - 1u;

    // Preprocess one block. Padding past `count` repeats the last sample.
    bool zero = true;
    for (size_t i = 0; i < c.block; ++i) {
      const size_t idx = b * c.block + i;
      const uint32_t s = x[idx < count ? idx : count - 1u];
      if (ref && i == 0) {
        d[0] = 0;                              // reference sample: prediction = itself
        ref_sample = (uint16_t)s;
      } else {
        d[i] = map_error(s, pred, xmax);
        if (d[i] != 0) zero = false;
      }
      pred = s;
    }

    if (zero) {
      if (e.run == 0) {
        e.run_ref = ref;
        e.run_ref_sample = ref_sample;
      }
      ++e.run;
      if (seg_end) e.emit_zero_run(e.run >= 5u);   // ROS only for 5+ blocks (§3.5.3)
      continue;
    }
    if (e.run > 0) e.emit_zero_run(false);         // run interrupted inside the segment
    e.emit_block(d, ref, ref_sample);
  }
  e.w.pad();
  if (e.w.overflow) return RICE_ERR_OVERFLOW;
  *out_len = e.w.pos;
  return RICE_OK;
}

}  // namespace ccsds
