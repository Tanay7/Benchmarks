// =============================================================================
//  CCSDS 121.0-B-3 lossless compression (src/ccsds/rice.*): flight encoder
//  verification.
//
//  1. Official CCSDS test data (121B2TestData, CCSDS SLS-DC working group,
//     distributed with libaec under data/121B2TestData): a representative
//     subset is embedded below as verbatim hex with each file's SHA-256 (checked
//     here), covering every option and every ID width the encoder supports:
//     test_p256n16 (the mission's n = 16: FS, k = 1..13, no-compression),
//     test_p256n10 (4-bit IDs with Zero-Block/ROS and Second-Extension),
//     test_p256n04 / n02 Basic and Restricted (3-, 2- and 1-bit IDs) and
//     Lowset1 (Zero-Block runs inside a segment). The flight encoder must
//     reproduce each .rz octet for octet; the test-local decoder must restore
//     each .dat. Set CCSDS121_TESTDATA=<path>/121B2TestData to also run every
//     official file with n <= 16 (AllOptions n01..n16, LowEntropyOptions).
//     Parameters the official README leaves implicit: J = 16 everywhere;
//     AllOptions has 16 blocks, so any r >= 16 gives the same stream (one
//     reference sample); LowEntropyOptions uses r = 64 (Lowset3's 128 blocks
//     carry two reference samples, confirmed bit-exactly).
//  2. Hand-derived streams (derivations in the comments).
//  3. A corpus of random and adversarial inputs for the mission parameters and
//     for other legal parameter sets: round trip through an independent
//     test-local decoder, output bound, option coverage (every option of the
//     mission's 4-bit ID set must occur), argument and overflow handling.
//  4. <out>/rice_vectors.txt for ground/tests/test_rice.py, one stream per line:
//       name n J r restricted count samples_hex(u16 big-endian) encoded_hex
//     The ground decoder must restore the samples and the ground reference
//     encoder must reproduce the encoded stream bit for bit.
// =============================================================================
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "ccsds/rice.h"
#include "ccsds/sha256.h"
#include "host_test.h"

using namespace ccsds;
using host_test::hex;

namespace {

// ---- Official CCSDS test data (subset) -----------------------------------------------------
// ground/tests/test_rice.py parses this table (between the BEGIN/END markers): keep the layout.
struct OfficialInput {
  const char* file;        // path below 121B2TestData
  unsigned width;          // octets per sample in the file (little-endian), 1 or 2
  const char* sha256;      // of the whole official file
  const char* hex;         // the whole official file
};
struct OfficialCoded {
  const char* file;
  unsigned input;          // index into kOfficialInputs
  unsigned n, j, r;
  bool restricted;
  const char* sha256;
  const char* hex;
};

// BEGIN CCSDS121 OFFICIAL VECTORS
const OfficialInput kOfficialInputs[] = {
    {"AllOptions/test_p256n16.dat", 2,
     "566ac528028e6335b7b1bd957f98cf5d61e51e2b80e461392747a3164863b741",
     "ff7ffeffff7fffffff7f00000080ffff0080010000800000ff7ffdff0000ffff00000060002000200050001000100040"
     "000000000060002000200050001000100000002000300040005000600070008000900070006000500040003000200010"
     "002000200030004000500060007000800090007000600050004000300020002000100010001800200028003000380040"
     "0048003800300028002000180010001000080008000c001000140018001c00200024001c001800140010000c00080008"
     "0004000400060008000a000c000e00100012000e000c000a000800060004000400020002000300040005000600070008"
     "000900070006000500040003000200020001000180010002800200038003000480048003000380020002800100010001"
     "80008000c000000140018001c00100024002c001800140010001c000800080004000400060008000a000c000e0000001"
     "2001e000c000a00080006000400040002000200030004000500060007000800090007000600050004000300020002000"
     "1000100018002000280030003800400048003800300028002000180010001000080008000c001000140018001c002000"
     "24001c001800140010000c000800080004000400060008000a000c000e00100012000e000c000a000800060004000400"
     "0200020003000400050006000700080009000700060005000400030002000200"},
    {"AllOptions/test_p256n10.dat", 2,
     "c8a91fd7d4537b7a4d65d1d3347ec3fab35c3eaf3e0e58088d2f0b4e741409db",
     "ff01fe03ff01ff03ff0100000002ff030002010000020000ff01fd030000ff0300008001800080004001400040000001"
     "0000000080018000800040014000400000008000c000000140018001c00100024002c001800140010001c00080004000"
     "80008000c000000140018001c00100024002c001800140010001c000800080004000400060008000a000c000e0000001"
     "2001e000c000a00080006000400040002000200030004000500060007000800090007000600050004000300020002000"
     "1000100018002000280030003800400048003800300028002000180010001000080008000c001000140018001c002000"
     "24001c001800140010000c000800080004000400060008000a000c000e00100012000e000c000a000800060004000400"
     "020002000300040005000600070008000900070006000500040003000200020000000100010001000100010001000100"
     "010001000100010001000100010001000100010001000100010001000100010001000100010001000100010001000100"
     "010001000100010001000100010001000100010001000100010001000100010001000100010001000100010001000100"
     "010001000100010001000100010001000100010001000100010001000100010001000100010001000100010001000100"
     "0100010001000100010001000100010001000100010001000100010001000100"},
    {"AllOptions/test_p256n04.dat", 1,
     "dfc6815ecfc1147309a55d67650601da4113c6ccf2934d0da162de7781e3f21b",
     "070e070f0700080f08010800070d000f0006020205010104000006020205010100020304050607080907060504030201"
     "020203040506070809070605040302020001010101010101010101010101010101010101010101010101010101010101"
     "010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101"
     "010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101"
     "010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101"
     "01010101010101010101010101010101"},
    {"AllOptions/test_p256n02.dat", 1,
     "cdf8b818d9d2367e48d4800d1a9f28d0b5ac301c99d642ffa031e897f7746de3",
     "010201030100020302010200010100030001000001000001000001000001000000010101010101010101010101010101"
     "010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101"
     "010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101"
     "010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101"
     "010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101"
     "01010101010101010101010101010101"},
    {"LowEntropyOptions/Lowset1_8bit.dat", 1,
     "7f7531cec41dcc2d5841666c2059ba54a9ecdd7f7782a4fc195e6bc6e25efbcb",
     "000000000000000000000000000000000100010001000100010001000100010000000000000000000000000000000000"
     "000000000000000000000000000000000100010001000100010001000100010000000000000000000000000000000000"
     "000000000000000000000000000000000000000000000000000000000000000001000100010001000100010001000100"
     "000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
     "000000000000000000000000000000000100010001000100010001000100010000000000000000000000000000000000"
     "000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
     "000000000000000000000000000000000100010001000100010001000100010000000000000000000000000000000000"
     "000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
     "000000000000000000000000000000000000000000000000000000000000000001000100010001000100010001000100"},
};
const OfficialCoded kOfficialCoded[] = {
    {"AllOptions/test_p256n16.rz", 0, 16, 16, 16, false,
     "2463bcb83fb8756681bdcf24cac8b68a9a4f0a3a39814e46b11b60c652de82e8",
     "f7ffffffe8000ffff8000fffd8000fffe7ffffffd8000ffff7ffffffcfffffffffffff60007fff000050007fff000040"
     "007fff000060007fff000050007fff0000eaaaabffffc00000000000000000000000003ffffffffffffffffffffffe99"
     "249245558000000000000000000000000007fffffffffffffffff80060c924922aafff80000000000000000000007fff"
     "ffffffffffffe002c6492491557ff800000000000000000007ffffffffffffff80143249248aabff8000000000000000"
     "007ffffffffffffe0091924924555ff80000000000000007fffffffffff8040c924922aaff800000000000007fffffff"
     "ffe01c6492491557f8000000000007ffffffff80c3249248aabf80000000007ffffffe051924924555f800000007ffff"
     "f820c924922aaf8000007fffe0c649249155780007ff843249248aab807e119249245558"},
    {"AllOptions/test_p256n10.rz", 1, 10, 16, 16, false,
     "62a7c02b115457a32b4f1f5569a8156fc73cb086e67c64e1f53dd20291c2078a",
     "f7fffe803ff803fd803fe7fffd803ff7fffcfffff915ad6b5ffc07f80207f80007f80407f80207f80455555fff800000"
     "000000007fffffffffffdcc924922aac0000000000000fffffffff0186492491557f0000000000fffffffc0a3249248a"
     "abf00000000ffffff041924924555f000000ffffc18c924922aaf0000fff08649249155700fc23249248aab0800ff004"},
    {"AllOptions/test_p256n04-basic.rz", 2, 4, 16, 16, false,
     "7e0e01a8a0e632480963b7d205d0ab6a4fa0ec7dbe6fc69d4b61c5bd336f4d5f",
     "efd1f1b1cfb1ef9fec56b5ad7d8e19638aaaaaff00fe4c924922aac4007f8040"},
    {"AllOptions/test_p256n04-restricted.rz", 2, 4, 16, 16, true,
     "61dd238fa142df0df00df45ffc8e6dac4d79d85cb8378295a12632fc1a521291",
     "dfa3e3639f63df3fff670570470670570aaaaaff00fe99249245559001fe02"},
    {"AllOptions/test_p256n02-basic.rz", 3, 2, 16, 16, false,
     "5756b4aa50dd54aaff255cc6cf7acb83d4f7c5fd9d5d39d159af88e64d4421ee",
     "ecf34b68e456b5ad627f8040"},
    {"AllOptions/test_p256n02-restricted.rz", 3, 2, 16, 16, true,
     "bdd27c357ac1c699f1aa16cb75ce2082b2ad6638abb9c6c43466817e08819595",
     "b3cd2da3f514514513fc08"},
    {"LowEntropyOptions/Lowset1_8bit.n08.rz", 4, 8, 16, 64, false,
     "95d81c7d59d1a15ecc006762331cc2622a28fae767624424cad27b0133b1d0f1",
     "00095555555504aaaaaaaa812aaaaaaaa02555555554012aaaaaaaa004aaaaaaaa80"},
    {"LowEntropyOptions/Lowset1_8bit.n03-basic.rz", 4, 3, 16, 64, false,
     "b9b59a7a3311521915931d8775b16386f942e4fbe1023a322a0128fe5fd34f93",
     "012aaaaaaaa09555555550255555555404aaaaaaaa802555555554009555555550"},
    {"LowEntropyOptions/Lowset1_8bit.n01-restricted.rz", 4, 1, 16, 64, true,
     "a46dc4c6a370116a93e6f5531063faf6aad2a6e8c0c6ae7459f4e274f36d7490",
     "1ffff8ffffc3ffff07fffe03ffff00ffffc0"},
};
// END CCSDS121 OFFICIAL VECTORS

// ---- Test-local decoder ----------------------------------------------------------------------
// Written from the standard independently of rice.cpp (no shared code), strict: every
// structural rule is checked and the stream must end with < 8 zero fill bits.
struct Stats {
  unsigned zero = 0, ros = 0, se = 0, nc = 0;
  unsigned split[14] = {0};                     // k = 0 (FS) .. 13
};

class Reader {
 public:
  Reader(const uint8_t* p, size_t len) : p_(p), nbits_(len * 8) {}
  bool bits(unsigned nb, uint32_t& v) {
    v = 0;
    if (pos_ + nb > nbits_) return false;
    for (unsigned i = 0; i < nb; ++i, ++pos_) v = (v << 1) | ((p_[pos_ >> 3] >> (7 - (pos_ & 7))) & 1u);
    return true;
  }
  bool fs(uint32_t& m) {                        // zeros up to the next one
    m = 0;
    for (; pos_ < nbits_; ++pos_, ++m) {
      if ((p_[pos_ >> 3] >> (7 - (pos_ & 7))) & 1u) { ++pos_; return true; }
    }
    return false;
  }
  bool tail_ok() const {                        // < 8 fill bits, all zero
    if (nbits_ - pos_ >= 8) return false;
    for (size_t q = pos_; q < nbits_; ++q)
      if ((p_[q >> 3] >> (7 - (q & 7))) & 1u) return false;
    return true;
  }

 private:
  const uint8_t* p_;
  size_t nbits_, pos_ = 0;
};

bool ref_decode(const RiceConfig& c, const uint8_t* data, size_t len, size_t count,
                std::vector<uint16_t>& x, Stats* st = nullptr) {
  const unsigned n = c.bits, J = c.block, r = c.rsi;
  const unsigned il = c.restricted ? (n <= 2 ? 1 : 2) : (n <= 8 ? 3 : 4);
  const uint32_t nc_id = (1u << il) - 1u, xmax = (1u << n) - 1u;
  const size_t nblocks = (count + J - 1) / J;
  std::vector<uint32_t> d(nblocks * J, 0);      // mapped samples; reference samples verbatim
  std::vector<bool> is_ref(nblocks * J, false);
  Reader rd(data, len);
  size_t b = 0;
  while (b < nblocks) {
    const size_t rsi0 = b - b % r;
    const size_t rsi_end = std::min(rsi0 + r, nblocks);
    const size_t seg_end = std::min(rsi0 + ((b - rsi0) / 64 + 1) * 64, rsi_end);
    const bool ref = b == rsi0;
    const size_t base = b * J;
    uint32_t id = 0, v = 0, m = 0;
    if (!rd.bits(il, id)) return false;
    auto get_ref = [&]() {
      if (!ref) return true;
      if (!rd.bits(n, v)) return false;
      d[base] = v;
      is_ref[base] = true;
      return true;
    };
    if (id == 0) {                                          // Zero-Block or Second-Extension
      uint32_t low = 0;
      if (!rd.bits(1, low) || !get_ref()) return false;
      if (low == 0) {
        if (!rd.fs(m)) return false;
        size_t blocks = 0;
        if (m == 4) { blocks = seg_end - b; if (blocks < 5) return false; if (st) ++st->ros; }
        else { blocks = m < 4 ? m + 1 : m; if (st) ++st->zero; }
        if (blocks > seg_end - b) return false;
        b += blocks;                                         // d[] already zero
        continue;
      }
      for (unsigned i = 0; i < J; i += 2) {
        if (!rd.fs(m)) return false;
        uint32_t s = 0;
        while ((s + 1) * (s + 2) / 2 <= m) ++s;              // gamma -> (a + b) = s
        const uint32_t bb = m - s * (s + 1) / 2, aa = s - bb;
        if (aa > xmax || bb > xmax || (ref && i == 0 && aa != 0)) return false;
        if (!(ref && i == 0)) d[base + i] = aa;
        d[base + i + 1] = bb;
      }
      if (st) ++st->se;
    } else if (id == nc_id) {
      if (!get_ref()) return false;
      for (unsigned i = ref ? 1 : 0; i < J; ++i) {
        if (!rd.bits(n, v)) return false;
        d[base + i] = v;
      }
      if (st) ++st->nc;
    } else {
      const unsigned k = id - 1;
      if (!get_ref()) return false;
      for (unsigned i = ref ? 1 : 0; i < J; ++i) {
        if (!rd.fs(m) || m > (xmax >> k)) return false;
        d[base + i] = m << k;
      }
      for (unsigned i = ref ? 1 : 0; k > 0 && i < J; ++i) {
        if (!rd.bits(k, v)) return false;
        d[base + i] |= v;
      }
      if (st && k < 14) ++st->split[k];
    }
    ++b;
  }
  if (!rd.tail_ok()) return false;
  // Inverse mapper (§4.4).
  x.assign(count, 0);
  uint32_t pred = 0;
  for (size_t i = 0; i < count; ++i) {
    uint32_t s;
    if (is_ref[i]) {
      s = d[i];
    } else {
      const uint32_t dl = d[i], theta = std::min(pred, xmax - pred);
      if (dl > xmax) return false;
      if (dl <= 2 * theta) s = (dl & 1u) ? pred - (dl + 1) / 2 : pred + dl / 2;
      else s = pred <= xmax - pred ? dl : xmax - dl;        // only one side left
    }
    x[i] = (uint16_t)s;
    pred = s;
  }
  return true;
}

// ---- Helpers ------------------------------------------------------------------------------
std::vector<uint8_t> unhex_vec(const char* s) {
  std::vector<uint8_t> v(std::strlen(s) / 2 + 1);
  v.resize(host_test::unhex(s, v.data(), v.size()));
  return v;
}

std::string sha256_hex(const std::vector<uint8_t>& v) {
  Sha256 h;
  h.update(v.data(), v.size());
  uint8_t dg[32];
  h.final(dg);
  return hex(dg, sizeof dg);
}

std::vector<uint16_t> samples_from_file(const std::vector<uint8_t>& raw, unsigned width) {
  std::vector<uint16_t> s;
  for (size_t i = 0; i + width <= raw.size(); i += width)
    s.push_back((uint16_t)(width == 2 ? raw[i] | (raw[i + 1] << 8) : raw[i]));
  return s;
}

bool encode_vec(const RiceConfig& c, const std::vector<uint16_t>& x, std::vector<uint8_t>& out,
                RiceStatus* status = nullptr) {
  out.assign(rice_max_encoded_len(c, x.size()) + 1, 0);
  size_t n = 0;
  const RiceStatus s = rice_encode(c, x.data(), x.size(), out.data(), out.size(), &n);
  if (status) *status = s;
  out.resize(n);
  return s == RICE_OK;
}

// Local deterministic generator (independent of host_test::rnd() and test order).
struct Xorshift {
  uint32_t s;
  uint32_t operator()() { s ^= s << 13; s ^= s >> 17; s ^= s << 5; return s; }
};

bool read_file(const std::string& path, std::vector<uint8_t>& v) {
  FILE* f = std::fopen(path.c_str(), "rb");
  if (!f) return false;
  v.clear();
  uint8_t buf[4096];
  size_t n;
  while ((n = std::fread(buf, 1, sizeof buf, f)) > 0) v.insert(v.end(), buf, buf + n);
  std::fclose(f);
  return true;
}

// ---- Corpus --------------------------------------------------------------------------------
struct Case {
  std::string name;
  RiceConfig cfg;
  std::vector<uint16_t> x;
};

// |Delta| chosen so the mapped values fall where split option k is optimal:
// delta in [3 * 2^(k-1), 2^(k+1)) around mid-scale (delta = 2 |Delta|).
std::vector<uint16_t> k_sweep(Xorshift& g) {
  std::vector<uint16_t> x;
  for (unsigned k = 0; k <= 13; ++k) {
    for (unsigned i = 0; i < 32; ++i) {
      if (k == 0) {
        x.push_back((uint16_t)(40000u - x.size()));            // ramp: every delta = 1 -> FS
      } else {
        const uint32_t lo = 3u << (k - 1), hi = 1u << (k + 1);
        const uint32_t dl = lo + g() % (hi - lo);
        x.push_back((uint16_t)(32768u + ((i & 1u) ? (dl + 1) / 2 : 0u)));
      }
    }
  }
  return x;
}

// Blocks that are either all-zero after mapping (constant) or not (one step).
// pattern: 'Z' = zero block, 'X' = non-zero block.
std::vector<uint16_t> blocks_from_pattern(const std::string& pattern, unsigned J) {
  std::vector<uint16_t> x;
  uint16_t level = 1000;
  for (char ch : pattern) {
    for (unsigned i = 0; i < J; ++i) {
      if (ch == 'X' && i == J / 2) level = (uint16_t)(level + 3);
      x.push_back(level);
    }
  }
  return x;
}

std::vector<Case> corpus() {
  std::vector<Case> v;
  Xorshift g{0x9E3779B9u};
  const RiceConfig M = kRiceMission;
  auto add = [&](const std::string& name, const RiceConfig& c, const std::vector<uint16_t>& x) {
    v.push_back(Case{name, c, x});
  };
  // Degenerate and adversarial inputs, mission parameters.
  add("one_sample", M, {0x1234});
  add("zeros_17", M, std::vector<uint16_t>(17, 0));
  add("zeros_2064", M, std::vector<uint16_t>(2064, 0));         // 128 + 1 blocks: 2 intervals
  add("const_ffff_300", M, std::vector<uint16_t>(300, 0xFFFF));
  {
    std::vector<uint16_t> x;
    for (unsigned i = 0; i < 256; ++i) x.push_back((i & 1u) ? 0xFFFF : 0);
    add("alternating_extremes", M, x);
    x.clear();
    for (unsigned i = 0; i < 1000; ++i) x.push_back((uint16_t)i);
    add("ramp_up_1", M, x);
    x.clear();
    for (unsigned i = 0; i < 1000; ++i) x.push_back((uint16_t)(65535u - 7u * i));
    add("ramp_down_7", M, x);
    x.clear();
    for (unsigned i = 0; i < 1500; ++i) x.push_back((uint16_t)(i * 997u));   // wraps: sawtooth
    add("sawtooth", M, x);
    x.clear();
    for (unsigned i = 0; i < 4096; ++i) x.push_back((uint16_t)g());
    add("max_entropy_4096", M, x);                                // 2 intervals, all NC
    x.clear();
    for (unsigned i = 0; i < 4096; ++i) x.push_back((uint16_t)((i & 4u) ? 65535u - (g() & 3u) : (g() & 3u)));
    add("near_extremes", M, x);                                   // mapper "otherwise" branch
    x.clear();
    uint32_t walk = 30000;
    for (unsigned i = 0; i < 3000; ++i) {
      const uint32_t step = g() % 41u;
      walk = (uint32_t)((int32_t)walk + (int32_t)step - 20) & 0xFFFFu;
      x.push_back((uint16_t)walk);
    }
    add("random_walk", M, x);
    x.clear();
    for (unsigned i = 0; i < 2000; ++i) x.push_back((uint16_t)(500u + ((g() % 23u) == 0 ? 1u : 0u)));
    add("sparse_steps", M, x);                                    // Second-Extension territory
  }
  add("k_sweep", M, k_sweep(g));
  // Zero-Block structure: runs of 1..7 inside a segment, a run crossing the
  // 64-block segment boundary, a whole zero segment (ROS), a run of exactly 4
  // at a segment end (count code, not ROS), and the end of the input.
  add("zero_runs", M, blocks_from_pattern(
      "XZXZZXZZZXZZZZXZZZZZXZZZZZZXZZZZZZZX" + std::string(40, 'Z') + "X" + std::string(23, 'Z') +
      std::string(64, 'Z') + std::string(60, 'X') + "ZZZZ" + "X" + std::string(9, 'Z'), 16));
  {
    // r = 100: the second segment of every interval has 36 blocks; ROS must
    // also close those short segments, and a zero run may start at a reference block.
    RiceConfig c = M;
    c.rsi = 100;
    add("short_segment_r100", c, blocks_from_pattern(
        "X" + std::string(63, 'Z') + std::string(30, 'X') + std::string(6, 'Z') +
        std::string(100, 'Z') + "ZZXX", 16));
  }
  // SPECBURST-shaped data: 18 channels x 3 samples, CHANNEL-MAJOR (contract 24.3):
  // per-channel levels with small temporal noise; channels 6..11 not converted
  // in this BANK mode (0xFFFF).
  {
    std::vector<uint16_t> x;
    for (unsigned ch = 0; ch < 18; ++ch) {
      const uint32_t level = 200u + ch * 2100u;
      for (unsigned t = 0; t < 3; ++t)
        x.push_back(ch >= 6 && ch < 12 ? 0xFFFF : (uint16_t)(level + g() % 9u));
    }
    add("specburst_54", M, x);
    x.resize(18);
    add("specburst_18", M, x);
  }
  // Other legal parameter sets (exercised for the ground decoder too).
  {
    RiceConfig c = M;
    c.block = 8; c.rsi = 1;                                       // every block a reference
    std::vector<uint16_t> x;
    for (unsigned i = 0; i < 203; ++i) x.push_back((uint16_t)(1000u + (g() % 64u)));
    add("j8_r1", c, x);
    c = M; c.block = 32; c.rsi = 3;
    x.clear();
    for (unsigned i = 0; i < 700; ++i) x.push_back((uint16_t)(i < 300 ? 7 : (g() & 1u)));
    add("j32_r3", c, x);
    c = M; c.block = 64; c.rsi = 4096;
    x.clear();
    for (unsigned i = 0; i < 4096; ++i) x.push_back((uint16_t)((i / 64) % 3 == 0 ? 9 : 9 + (g() % 3u)));
    add("j64_r4096", c, x);
    c = M; c.bits = 12;
    x.clear();
    for (unsigned i = 0; i < 777; ++i) x.push_back((uint16_t)((2048u + i * 3u + (g() % 5u)) & 0xFFFu));
    add("n12", c, x);
    c = M; c.bits = 8;
    x.clear();
    for (unsigned i = 0; i < 500; ++i) x.push_back((uint16_t)(g() % 256u));
    add("n8_random", c, x);
    for (unsigned bits = 1; bits <= 4; ++bits) {
      for (int res = 0; res <= 1; ++res) {
        c = M; c.bits = (uint8_t)bits; c.restricted = res != 0; c.rsi = 8;
        x.clear();
        for (unsigned i = 0; i < 600; ++i)
          x.push_back((uint16_t)((i / 80) % 2 ? g() % (1u << bits) : (i / 160) % 2));
        add("n" + std::to_string(bits) + (res ? "_restricted" : "_basic"), c, x);
      }
    }
  }
  return v;
}

}  // namespace

HOST_TEST(rice_official_vectors) {
  (void)out;                                    // writes no vector file
  int failed = 0;
  std::vector<std::vector<uint16_t>> inputs;
  for (const OfficialInput& in : kOfficialInputs) {
    const std::vector<uint8_t> raw = unhex_vec(in.hex);
    if (sha256_hex(raw) != in.sha256) {
      std::printf("  %s: embedded copy does not match the official SHA-256\n", in.file);
      ++failed;
    }
    inputs.push_back(samples_from_file(raw, in.width));
  }
  CHECK(failed == 0, "rice: embedded official inputs match their SHA-256");
  unsigned bad_sha = 0, bad_enc = 0, bad_dec = 0;
  for (const OfficialCoded& oc : kOfficialCoded) {
    const std::vector<uint8_t> want = unhex_vec(oc.hex);
    const RiceConfig c = {(uint8_t)oc.n, (uint8_t)oc.j, (uint16_t)oc.r, oc.restricted};
    std::vector<uint8_t> got;
    std::vector<uint16_t> back;
    const bool sha_ok = sha256_hex(want) == oc.sha256;
    const bool enc_ok = encode_vec(c, inputs[oc.input], got) && got == want;
    const bool dec_ok = ref_decode(c, want.data(), want.size(), inputs[oc.input].size(), back) &&
                        back == inputs[oc.input];
    bad_sha += sha_ok ? 0u : 1u;
    bad_enc += enc_ok ? 0u : 1u;
    bad_dec += dec_ok ? 0u : 1u;
    if (!(sha_ok && enc_ok && dec_ok))
      std::printf("  %s: sha %d encode %d decode %d\n", oc.file, sha_ok, enc_ok, dec_ok);
  }
  CHECK(bad_sha == 0, "rice: embedded official .rz streams match their SHA-256");
  CHECK(bad_enc == 0, "rice: flight encoder reproduces the 9 embedded official CCSDS .rz streams "
                      "octet for octet (n = 16, 10, 8, 4, 3, 2, 1; Basic and Restricted)");
  CHECK(bad_dec == 0, "rice: test decoder restores every embedded official .dat");
}

HOST_TEST(rice_official_full_suite) {
  (void)out;                                    // writes no vector file
  const char* root = std::getenv("CCSDS121_TESTDATA");
  if (root == nullptr || *root == '\0') {
    std::printf("skip: rice full official suite (set CCSDS121_TESTDATA=<path>/121B2TestData)\n");
    return;
  }
  struct Job { std::string dat, rz; RiceConfig c; unsigned width; };
  std::vector<Job> jobs;
  const std::string base(root);
  for (unsigned n = 1; n <= 16; ++n) {
    char nn[3];
    std::snprintf(nn, sizeof nn, "%02u", n);
    const std::string dat = base + "/AllOptions/test_p256n" + nn + ".dat";
    const unsigned w = n <= 8 ? 1u : 2u;
    const RiceConfig basic = {(uint8_t)n, 16, 16, false}, restr = {(uint8_t)n, 16, 16, true};
    if (n <= 4) {
      jobs.push_back({dat, base + "/AllOptions/test_p256n" + nn + "-basic.rz", basic, w});
      jobs.push_back({dat, base + "/AllOptions/test_p256n" + nn + "-restricted.rz", restr, w});
    } else {
      jobs.push_back({dat, base + "/AllOptions/test_p256n" + nn + ".rz", basic, w});
    }
  }
  for (unsigned set = 1; set <= 3; ++set) {
    const std::string stem = base + "/LowEntropyOptions/Lowset" + std::to_string(set) + "_8bit";
    for (unsigned n = 1; n <= 8; ++n) {
      const std::string nn = std::string(n < 10 ? ".n0" : ".n") + std::to_string(n);
      const RiceConfig basic = {(uint8_t)n, 16, 64, false}, restr = {(uint8_t)n, 16, 64, true};
      if (n <= 4) {
        jobs.push_back({stem + ".dat", stem + nn + "-basic.rz", basic, 1});
        jobs.push_back({stem + ".dat", stem + nn + "-restricted.rz", restr, 1});
      } else {
        jobs.push_back({stem + ".dat", stem + nn + ".rz", basic, 1});
      }
    }
  }
  unsigned ok = 0, bad = 0;
  for (const Job& jb : jobs) {
    std::vector<uint8_t> raw, want, got;
    std::vector<uint16_t> back;
    if (!read_file(jb.dat, raw) || !read_file(jb.rz, want)) {
      std::printf("  cannot read %s / %s\n", jb.dat.c_str(), jb.rz.c_str());
      ++bad;
      continue;
    }
    const std::vector<uint16_t> x = samples_from_file(raw, jb.width);
    const bool enc = encode_vec(jb.c, x, got) && got == want;
    const bool dec = ref_decode(jb.c, want.data(), want.size(), x.size(), back) && back == x;
    if (enc && dec) ++ok;
    else { ++bad; std::printf("  %s: encode %d decode %d\n", jb.rz.c_str(), enc, dec); }
  }
  std::printf("  rice official suite: %u of %zu files bit-exact\n", ok, jobs.size());
  CHECK(bad == 0 && ok == 56, "rice: flight encoder bit-exact on all 56 official files with n <= 16 "
                               "(20 AllOptions, 36 LowEntropyOptions)");
}

HOST_TEST(rice_hand_derived) {
  (void)out;                                    // writes no vector file
  std::vector<uint8_t> e;
  // One sample 0x1234: the block is padded with copies (delta = 0) -> one
  // Zero-Block CDS: ID 00000 | reference 0001001000110100 | FS(1 block) = 1,
  // then 2 fill bits: 00000000 10010001 10100100.
  CHECK(encode_vec(kRiceMission, {0x1234}, e) && hex(e.data(), e.size()) == "0091a4",
        "rice: single sample -> zero-block CDS 00 91 a4");
  // 16 zeros: 00000 | 16 zero bits | 1 | 00 -> 00 00 04.
  CHECK(encode_vec(kRiceMission, std::vector<uint16_t>(16, 0), e) && hex(e.data(), e.size()) == "000004",
        "rice: all-zero block -> 00 00 04");
  // 0, 65535, 0, ...: predictor at an extreme -> theta = 0 -> every delta = 65535;
  // no-compression: 1111 | reference 0x0000 | 15 x 0xFFFF | 0000 = F0 00 0F FF*29 F0.
  {
    std::vector<uint16_t> x;
    for (unsigned i = 0; i < 16; ++i) x.push_back((i & 1u) ? 0xFFFF : 0);
    const std::string want = "f0000f" + std::string(58, 'f') + "f0";
    CHECK(encode_vec(kRiceMission, x, e) && hex(e.data(), e.size()) == want,
          "rice: extremes -> no-compression CDS f0 00 0f ff..ff f0 (33 octets = bound)");
    CHECK(e.size() == rice_max_encoded_len(kRiceMission, 16), "rice: that is exactly the bound");
  }
  // 2, 3, 4, ..., 17: delta 2 everywhere after the reference. FS: 15 x (2 zeros + 1) + 15
  // = 45 bits; k = 1: 15 x 1 + 15 x 2 = 45; k = 2: 15 x 3 = 45 (three-way tie -> the
  // smallest k, i.e. FS); SE pairs (0,2),(2,2)x7: gamma 5 then 12 -> 1 + 6 + 7 x 13 = 98;
  // no-compression 240. CDS: ID 0001 | reference 0000000000000010 | 001 x 15 | 7 fill bits,
  // 72 bits: 00010000 00000000 00100010 01001001 00100100 10010010 01001001 00100100 10000000.
  {
    std::vector<uint16_t> x;
    for (unsigned i = 0; i < 16; ++i) x.push_back((uint16_t)(2 + i));
    CHECK(encode_vec(kRiceMission, x, e) && hex(e.data(), e.size()) == "100022492492492480",
          "rice: constant slope -> FS option (k tie resolved to the smallest k, 3.7.4 c)");
  }
}

HOST_TEST(rice_arguments_and_bound) {
  (void)out;                                    // writes no vector file
  static_assert(rice_mission_max_len(54) == 130, "SPECBURST worst case: 4 blocks x 260 bits");
  CHECK(rice_max_encoded_len(kRiceMission, 54) == 130 && rice_mission_max_len(1) == 33,
        "rice: bound 130 octets for 54 samples, 33 for 1");
  CHECK(rice_id_len(kRiceMission) == 4 && rice_id_len({8, 16, 1, false}) == 3 &&
        rice_id_len({2, 16, 1, true}) == 1 && rice_id_len({4, 16, 1, true}) == 2,
        "rice: ID lengths per Table 5-1 (4, 3, Restricted 1 and 2)");
  const RiceConfig bad[] = {{0, 16, 128, false},  {17, 16, 128, false}, {16, 12, 128, false},
                            {16, 128, 128, false}, {16, 16, 0, false},  {16, 16, 4097, false},
                            {5, 16, 128, true},   {16, 16, 128, true}};
  bool all_rejected = true;
  uint16_t one = 1;
  uint8_t buf[8];
  size_t n = 99;
  for (const RiceConfig& c : bad)
    all_rejected = all_rejected && !rice_config_ok(c) && rice_id_len(c) == 0 &&
                   rice_max_encoded_len(c, 10) == 0 &&
                   rice_encode(c, &one, 1, buf, sizeof buf, &n) == RICE_ERR_CONFIG && n == 0;
  CHECK(all_rejected, "rice: illegal n, J, r and Restricted with n > 4 are rejected");
  CHECK(rice_encode(kRiceMission, nullptr, 1, buf, sizeof buf, &n) == RICE_ERR_ARG &&
        rice_encode(kRiceMission, &one, 1, nullptr, sizeof buf, &n) == RICE_ERR_ARG &&
        rice_encode(kRiceMission, &one, 0, buf, sizeof buf, &n) == RICE_ERR_ARG &&
        rice_encode(kRiceMission, &one, kRiceMaxSamples + 1, buf, sizeof buf, &n) == RICE_ERR_ARG &&
        rice_encode(kRiceMission, &one, 1, buf, sizeof buf, nullptr) == RICE_ERR_ARG && n == 0,
        "rice: null pointers and counts outside 1..4096 are rejected");
  {
    const RiceConfig c12 = {12, 16, 128, false};
    const uint16_t x[3] = {1, 4095, 4096};
    uint8_t big[32];
    CHECK(rice_encode(c12, x, 2, big, sizeof big, &n) == RICE_OK && n > 0 &&
          rice_encode(c12, x, 3, big, sizeof big, &n) == RICE_ERR_SAMPLE && n == 0,
          "rice: a sample >= 2^n is rejected before any output");
  }
  // Overflow: incompressible input with cap = bound - 1 must fail without
  // touching the octet after cap; cap = bound succeeds.
  {
    Xorshift g{12345};
    std::vector<uint16_t> x(512);
    for (uint16_t& s : x) s = (uint16_t)g();
    const size_t bound = rice_max_encoded_len(kRiceMission, x.size());
    std::vector<uint8_t> o(bound + 1, 0xA5);
    const RiceStatus s1 = rice_encode(kRiceMission, x.data(), x.size(), o.data(), bound - 1, &n);
    CHECK(s1 == RICE_ERR_OVERFLOW && n == 0 && o[bound - 1] == 0xA5 && o[bound] == 0xA5,
          "rice: cap = bound - 1 -> RICE_ERR_OVERFLOW, nothing written beyond cap");
    CHECK(rice_encode(kRiceMission, x.data(), x.size(), o.data(), bound, &n) == RICE_OK &&
          n == bound && o[bound] == 0xA5, "rice: cap = bound -> fits exactly (max entropy = bound)");
    CHECK(rice_encode(kRiceMission, x.data(), x.size(), o.data(), 0, &n) == RICE_ERR_OVERFLOW,
          "rice: cap = 0 -> RICE_ERR_OVERFLOW");
    // The integrator's "send whichever is shorter" pattern: cap = raw length - 1.
    const size_t raw54 = 2 * 54;
    CHECK(rice_encode(kRiceMission, x.data(), 54, o.data(), raw54 - 1, &n) == RICE_ERR_OVERFLOW,
          "rice: 54 random samples do not compress below 108 octets -> send raw");
  }
}

HOST_TEST(rice_corpus_round_trip) {
  const std::vector<Case> cases = corpus();
  FILE* f = std::fopen((out + "/rice_vectors.txt").c_str(), "w");
  CHECK(f != nullptr, "rice: vector file opened");
  Stats mission;
  unsigned bad = 0, written = 0;
  size_t raw_total = 0, coded_total = 0;
  for (const Case& cs : cases) {
    std::vector<uint8_t> e;
    std::vector<uint16_t> back;
    Stats st;
    const bool enc = encode_vec(cs.cfg, cs.x, e);
    const bool rt = enc && ref_decode(cs.cfg, e.data(), e.size(), cs.x.size(), back, &st) && back == cs.x;
    const bool bounded = e.size() <= rice_max_encoded_len(cs.cfg, cs.x.size());
    if (!(rt && bounded)) {
      ++bad;
      std::printf("  %s: encode %d round-trip %d bounded %d\n", cs.name.c_str(), enc, rt, bounded);
    }
    if (cs.cfg.bits == 16 && cs.cfg.block == 16) {
      mission.zero += st.zero; mission.ros += st.ros; mission.se += st.se; mission.nc += st.nc;
      for (unsigned k = 0; k < 14; ++k) mission.split[k] += st.split[k];
      raw_total += 2 * cs.x.size();
      coded_total += e.size();
    }
    if (f) {
      std::string sx;
      for (uint16_t s : cs.x) { const uint8_t be[2] = {(uint8_t)(s >> 8), (uint8_t)s}; sx += hex(be, 2); }
      std::fprintf(f, "%s %u %u %u %u %zu %s %s\n", cs.name.c_str(), cs.cfg.bits, cs.cfg.block,
                   cs.cfg.rsi, cs.cfg.restricted ? 1u : 0u, cs.x.size(), sx.c_str(),
                   hex(e.data(), e.size()).c_str());
      ++written;
    }
  }
  if (f) std::fclose(f);
  CHECK(bad == 0, "rice: every corpus stream round-trips through the independent decoder "
                  "within rice_max_encoded_len()");
  bool all_k = true;
  unsigned k_min = ~0u;
  for (unsigned k = 0; k < 14; ++k) {
    all_k = all_k && mission.split[k] > 0;
    if (k > 0) k_min = std::min(k_min, mission.split[k]);
  }
  std::printf("  mission-parameter option use: zero %u ROS %u SE %u NC %u FS %u each k=1..13 >= %u\n",
              mission.zero, mission.ros, mission.se, mission.nc, mission.split[0], k_min);
  CHECK(mission.zero > 0 && mission.ros > 0 && mission.se > 0 && mission.nc > 0 && all_k,
        "rice: corpus exercises Zero-Block, ROS, Second-Extension, FS, k = 1..13 and "
        "no-compression at n = 16, J = 16");
  std::printf("  mission-parameter corpus: %zu raw octets -> %zu coded\n", raw_total, coded_total);
  CHECK(written == cases.size(), "rice: rice_vectors.txt written for the ground cross-check");
}
