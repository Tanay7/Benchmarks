// =============================================================================
//  CFDP class 1 (CCSDS 727.0-B-5): flight PDU header bit layout, builders and
//  parser, the modular checksum (incremental, unaligned offsets), file-name
//  rules, CfdpSender and CfdpRelayMonitor, plus the vectors
//  ground/tests/test_cfdp.py feeds to the independent Python receiver:
//
//    <out>/cfdp_source.bin        the pseudo-random 1000-octet file
//    <out>/cfdp_pdus.txt          one hex PDU per line: its complete transfer
//                                 (seq 0x000C0003, "ssr_p12_345678.bin", 128-octet segments)
//    <out>/cfdp_cancel_pdus.txt   the same transfer cancelled after 3 File Data PDUs
//    <out>/cfdp_relay_pdus.txt    EGSE-style transfer of the first 600 octets
//                                 (seq 0x80000007, "evr_20261010.jsonl", 96-octet segments)
// =============================================================================
#include <cstring>
#include <string>
#include <vector>

#include "ccsds/cfdp.h"
#include "host_test.h"

using namespace ccsds;
using host_test::hex;

namespace {

const uint32_t kSeq = 0x000C0003;               // cfdp_make_seq(12, 3); test_cfdp.py uses the same
const char kName[] = "ssr_p12_345678.bin";
const uint32_t kRelaySeq = 0x80000007;
const char kRelayName[] = "evr_20261010.jsonl";
const size_t kFileLen = 1000;
const size_t kRelayLen = 600;

// Deterministic file content (xorshift32 from a fixed seed): independent of
// test order and of host_test::rnd().
void fill_source(uint8_t* d, size_t n, uint32_t seed) {
  for (size_t i = 0; i < n; ++i) {
    seed ^= seed << 13;
    seed ^= seed >> 17;
    seed ^= seed << 5;
    d[i] = (uint8_t)seed;
  }
}
const uint32_t kSourceSeed = 0x6D2B79F5;

// The straightforward definition: zero-pad to whole words, add the words.
uint32_t ref_checksum(const uint8_t* d, size_t n) {
  uint32_t sum = 0;
  for (size_t i = 0; i < n; i += 4) {
    uint32_t w = 0;
    for (size_t k = 0; k < 4; ++k) w = (w << 8) | (i + k < n ? d[i + k] : 0u);
    sum += w;
  }
  return sum;
}

class BufSource : public CfdpSource {
 public:
  BufSource(const uint8_t* d, uint32_t n) : d_(d), n_(n) {}
  uint32_t size() override { return n_; }
  size_t read(uint32_t off, uint8_t* dst, size_t n) override {
    ++reads;
    if (off >= fail_at || off >= n_) return 0;
    if (n > n_ - off) n = n_ - off;
    std::memcpy(dst, d_ + off, n);
    return n + overclaim;                       // a lying source claims more than asked
  }
  uint32_t fail_at = 0xFFFFFFFFu, reads = 0;
  size_t overclaim = 0;

 private:
  const uint8_t* d_;
  uint32_t n_;
};

using Pdu = std::vector<uint8_t>;

// Drains a sender; `cancel_after` > 0 cancels after that many File Data PDUs.
std::vector<Pdu> drain(CfdpSender& s, int cancel_after = 0) {
  std::vector<Pdu> v;
  uint8_t buf[kCfdpMaxPduLen];
  int fd = 0;
  for (int guard = 0; guard < 10000; ++guard) {
    const size_t n = s.next_pdu(buf, sizeof buf);
    if (!n) break;
    v.push_back(Pdu(buf, buf + n));
    if (buf[0] & 0x10) ++fd;
    if (cancel_after && fd == cancel_after) s.cancel();
  }
  return v;
}

bool write_pdus(const std::string& path, const std::vector<Pdu>& v) {
  FILE* f = std::fopen(path.c_str(), "w");
  if (!f) return false;
  for (const Pdu& p : v) std::fprintf(f, "%s\n", hex(p.data(), p.size()).c_str());
  return std::fclose(f) == 0;
}

// Reassembles a parsed PDU stream; returns false on any parse failure or when
// the Metadata or the EOF is missing.
bool reassemble(const std::vector<Pdu>& v, std::vector<uint8_t>& file, CfdpPdu& md, CfdpPdu& eof,
                int& n_fd) {
  n_fd = 0;
  file.clear();
  bool have_md = false, have_eof = false;
  for (const Pdu& p : v) {
    CfdpPdu x;
    if (!cfdp_parse(p.data(), p.size(), x)) return false;
    if (x.hdr.file_data) {
      if (x.offset + x.data_len > file.size()) file.resize(x.offset + x.data_len);
      std::memcpy(file.data() + x.offset, x.data, x.data_len);
      ++n_fd;
    } else if (x.directive == kCfdpDirMetadata) {
      md = x;
      have_md = true;
    } else if (x.directive == kCfdpDirEof) {
      eof = x;
      have_eof = true;
    }
  }
  return have_md && have_eof;
}

}  // namespace

HOST_TEST(cfdp_header) {
  CfdpHeader h;
  h.data_len = 0x1234;
  h.source = 0x01;
  h.seq = 0x89ABCDEF;
  h.dest = 0x02;
  std::vector<uint8_t> b(kCfdpHdrLen + 0x1234, 0);
  CHECK(cfdp_put_header(b.data(), h) == 10, "CFDP header is 10 octets");
  CHECK(hex(b.data(), 10) == "24123403" "0189abcdef02", "CFDP directive header octets");
  CHECK((b[0] >> 5) == 1 && !((b[0] >> 4) & 1) && !((b[0] >> 3) & 1) && ((b[0] >> 2) & 1) &&
        !((b[0] >> 1) & 1) && !(b[0] & 1),
        "CFDP octet 0: version 001 | directive | toward receiver | unacknowledged | no CRC | small file");
  CHECK(!(b[3] >> 7) && ((b[3] >> 4) & 7) == 0 && !((b[3] >> 3) & 1) && (b[3] & 7) == 3,
        "CFDP octet 3: seg ctrl 0 | entity ID len-1 = 0 | no segment metadata | seq len-1 = 3");
  h.file_data = true;
  cfdp_put_header(b.data(), h);
  CHECK(b[0] == 0x34, "CFDP file data PDU type bit (octet 0 = 0x34)");
  CHECK(cfdp_put_header(nullptr, h) == 0, "CFDP header refuses a null buffer");

  CfdpHeader g;
  CHECK(cfdp_get_header(b.data(), b.size(), g) == 10 && g.file_data && g.unacknowledged &&
        !g.to_sender && g.data_len == 0x1234 && g.source == 1 && g.seq == 0x89ABCDEF && g.dest == 2,
        "CFDP header round trip");
  CHECK(cfdp_get_header(b.data(), b.size() - 1, g) == 0, "CFDP header rejects a data field past the end");
  CHECK(cfdp_get_header(b.data(), 9, g) == 0 && cfdp_get_header(nullptr, 100, g) == 0,
        "CFDP header rejects 9 octets and a null pointer");
  struct { size_t at; uint8_t v; const char* what; } bad[] = {
      {0, 0x14, "version 000"}, {0, 0x54, "version 010"}, {0, 0x36, "CRC flag"},
      {0, 0x35, "large-file flag"}, {3, 0x13, "2-octet entity IDs"},
      {3, 0x01, "2-octet sequence number"}, {3, 0x0B, "segment metadata flag"}};
  for (const auto& t : bad) {
    std::vector<uint8_t> c = b;
    c[t.at] = t.v;
    CHECK(cfdp_get_header(c.data(), c.size(), g) == 0, (std::string("CFDP header rejects ") + t.what).c_str());
  }
  std::vector<uint8_t> c = b;
  c[3] = 0x83;
  CHECK(cfdp_get_header(c.data(), c.size(), g) == 10, "CFDP header accepts the segmentation-control bit");
  CHECK(cfdp_make_seq(12, 3) == 0x000C0003 && cfdp_make_seq(0xFFFF, 1) == 0x7FFF0001 &&
        !(cfdp_make_seq(0xFFFF, 0xFFFF) & kCfdpEgseSeqFlag),
        "cfdp_make_seq: partition << 16 | n, always below the EGSE range");
}

HOST_TEST(cfdp_checksum) {
  const uint8_t digits[] = {'1', '2', '3', '4', '5', '6', '7', '8', '9'};
  CHECK(cfdp_checksum(0, 0, digits, 9) == 0x9F686A6C, "modular checksum \"123456789\" = 0x9F686A6C");
  const uint8_t aa = 0xAA;
  CHECK(cfdp_checksum(0, 1, &aa, 1) == 0x00AA0000 && cfdp_checksum(0, 2, &aa, 1) == 0x0000AA00 &&
        cfdp_checksum(0, 3, &aa, 1) == 0x000000AA && cfdp_checksum(0, 4, &aa, 1) == 0xAA000000 &&
        cfdp_checksum(0, 4097, &aa, 1) == 0x00AA0000,
        "modular checksum: octet position = file offset & 3");
  const uint8_t w[] = {0xFF, 0xFF, 0xFF, 0xFF, 0x00, 0x00, 0x00, 0x02};
  CHECK(cfdp_checksum(0, 0, w, 8) == 1, "modular checksum wraps mod 2^32");
  CHECK(cfdp_checksum(0x1234, 7, w, 0) == 0x1234 && cfdp_checksum(0x1234, 0, nullptr, 8) == 0x1234,
        "modular checksum: an empty or null segment adds nothing");
  CHECK(cfdp_checksum(0, 1, digits, 9) == 0x00313233u + 0x34353637u + 0x38390000u,
        "modular checksum \"123456789\" at offset 1");

  uint8_t d[kFileLen];
  fill_source(d, sizeof d, 0xC0FFEE01);
  const uint32_t ref = ref_checksum(d, sizeof d);
  uint32_t lcg = 0x2545F491;                     // split points
  bool split_ok = true, reverse_ok = true;
  for (int trial = 0; trial < 200; ++trial) {
    size_t cuts[8];
    lcg = lcg * 1664525u + 1013904223u;
    const int nc = 1 + (int)((lcg >> 8) % 7);
    for (int i = 0; i < nc; ++i) {
      lcg = lcg * 1664525u + 1013904223u;
      cuts[i] = (lcg >> 8) % (sizeof d + 1);
    }
    for (int i = 1; i < nc; ++i)                 // insertion sort (nc <= 7)
      for (int j = i; j > 0 && cuts[j] < cuts[j - 1]; --j) {
        const size_t t = cuts[j];
        cuts[j] = cuts[j - 1];
        cuts[j - 1] = t;
      }
    size_t edges[10];
    int ne = 0;
    edges[ne++] = 0;
    for (int i = 0; i < nc; ++i) edges[ne++] = cuts[i];
    edges[ne++] = sizeof d;
    uint32_t fwd = 0, rev = 0;
    for (int i = 0; i + 1 < ne; ++i) fwd = cfdp_checksum(fwd, (uint32_t)edges[i], d + edges[i], edges[i + 1] - edges[i]);
    for (int i = ne - 2; i >= 0; --i) rev = cfdp_checksum(rev, (uint32_t)edges[i], d + edges[i], edges[i + 1] - edges[i]);
    split_ok &= fwd == ref;
    reverse_ok &= rev == ref;
  }
  CHECK(split_ok, "modular checksum: 200 random unaligned splits == whole-file reference");
  CHECK(reverse_ok, "modular checksum: segments in reverse order give the same sum");
  bool odd_ok = true;
  for (size_t n = 0; n <= 9; ++n) odd_ok &= cfdp_checksum(0, 0, d, n) == ref_checksum(d, n);
  CHECK(odd_ok, "modular checksum: lengths 0..9 zero-pad the last word");
}

HOST_TEST(cfdp_names) {
  auto ok = [](const char* s, size_t max_len = kCfdpMaxNameLen) {
    return cfdp_name_ok(reinterpret_cast<const uint8_t*>(s), std::strlen(s), max_len);
  };
  CHECK(ok("ssr_p12_345678.bin") && ok("evr_20261010.jsonl") && ok("a") && ok("A-Z.0_9"),
        "CFDP names: mission names accepted");
  CHECK(!ok("") && !ok(".hidden") && !ok("../x") && !ok("a/b") && !ok("a\\b") && !ok("a b") &&
        !ok("caf\xc3\xa9"), "CFDP names: empty, dot-first, path separators, spaces, non-ASCII refused");
  CHECK(ok(std::string(64, 'x').c_str()) && !ok(std::string(65, 'x').c_str()) &&
        ok(std::string(46, 'x').c_str(), kCfdpRelayMaxNameLen) &&
        !ok(std::string(47, 'x').c_str(), kCfdpRelayMaxNameLen),
        "CFDP names: 64 octets flight, 46 octets relay");
  CHECK(kCfdpRelayMaxNameLen == 46 && kCfdpRelayMaxPduLen == 110, "CFDP relay limits 46 / 110");

  char nm[32];
  CHECK(cfdp_ssr_file_name(nm, sizeof nm, 12, 345678) == 18 && std::strcmp(nm, "ssr_p12_345678.bin") == 0,
        "CFDP SSR file name ssr_p12_345678.bin");
  CHECK(cfdp_ssr_file_name(nm, sizeof nm, 65535, 4294967295u) == 25 &&
        std::strcmp(nm, "ssr_p65535_4294967295.bin") == 0, "CFDP SSR file name at the field maxima");
  CHECK(cfdp_ssr_file_name(nm, sizeof nm, 0, 0) == 12 && std::strcmp(nm, "ssr_p0_0.bin") == 0,
        "CFDP SSR file name ssr_p0_0.bin");
  CHECK(cfdp_ssr_file_name(nm, 18, 12, 345678) == 0 && nm[0] == '\0' &&
        cfdp_ssr_file_name(nm, 19, 12, 345678) == 18 && cfdp_ssr_file_name(nullptr, 32, 1, 1) == 0,
        "CFDP SSR file name: exact capacity, short buffer and null refused");
  CHECK(ok(nm), "CFDP SSR file name passes the name rule");
}

HOST_TEST(cfdp_pdus) {
  uint8_t b[kCfdpMaxPduLen];
  size_t n = cfdp_build_metadata(b, sizeof b, 1, 0x000C0003, 2, 1000, "a.bin");
  CHECK(n == 28 && hex(b, n) == "24001203" "01000c000302" "07" "00" "000003e8" "05612e62696e" "05612e62696e",
        "CFDP Metadata PDU octets (modular checksum, closure 0, LV names)");
  CfdpPdu p;
  CHECK(cfdp_parse(b, n, p) && !p.hdr.file_data && p.directive == kCfdpDirMetadata &&
        p.checksum_type == 0 && !p.closure_requested && p.file_size == 1000 && p.src_name_len == 5 &&
        !std::memcmp(p.src_name, "a.bin", 5) && p.dst_name_len == 5 && !std::memcmp(p.dst_name, "a.bin", 5),
        "CFDP Metadata parse");
  CHECK(!cfdp_parse(b, n - 1, p), "CFDP parse rejects a truncated PDU");
  CHECK(!cfdp_parse(b, n + 1, p) && !cfdp_parse(nullptr, n, p), "CFDP parse rejects a trailing octet and null");
  b[2] = 0x0E;                                   // data field ends inside the destination name
  CHECK(!cfdp_parse(b, kCfdpHdrLen + 0x0E, p), "CFDP parse rejects an LV running past the data field");

  std::string longname(kCfdpMaxNameLen, 'x');
  CHECK(cfdp_build_metadata(b, sizeof b, 1, 1, 2, 0, longname.c_str()) == kCfdpMetadataMaxLen,
        "CFDP Metadata with a 64-character name = kCfdpMetadataMaxLen");
  longname += 'x';
  CHECK(cfdp_build_metadata(b, sizeof b, 1, 1, 2, 0, longname.c_str()) == 0 &&
        cfdp_build_metadata(b, sizeof b, 1, 1, 2, 0, "") == 0 &&
        cfdp_build_metadata(b, sizeof b, 1, 1, 2, 0, "../etc") == 0 &&
        cfdp_build_metadata(b, sizeof b, 1, 1, 2, 0, nullptr) == 0 &&
        cfdp_build_metadata(b, 20, 1, 1, 2, 0, "a.bin") == 0,
        "CFDP Metadata refuses a 65-character / empty / path name, null and a short buffer");

  const uint8_t three[] = {0xDE, 0xAD, 0xBE};
  n = cfdp_build_file_data(b, sizeof b, 1, 0x000C0003, 2, 0x100, three, 3);
  CHECK(n == 17 && hex(b, n) == "34000703" "01000c000302" "00000100" "deadbe", "CFDP File Data PDU octets");
  CHECK(cfdp_parse(b, n, p) && p.hdr.file_data && p.offset == 0x100 && p.data_len == 3 &&
        !std::memcmp(p.data, three, 3) && p.directive == 0, "CFDP File Data parse");
  CHECK(cfdp_build_file_data(b, kCfdpHdrLen + 4 + 2, 1, 1, 2, 0, three, 3) == 0 &&
        cfdp_build_file_data(b, sizeof b, 1, 1, 2, 0, nullptr, 3) == 0,
        "CFDP File Data refuses a short buffer and null data");
  b[kCfdpHdrLen + 4] = 0x11;                     // in-place segment (as the sender does)
  b[kCfdpHdrLen + 5] = 0x22;
  n = cfdp_build_file_data(b, sizeof b, 1, 5, 2, 8, b + kCfdpHdrLen + 4, 2);
  CHECK(n == 16 && b[kCfdpHdrLen + 4] == 0x11 && b[kCfdpHdrLen + 5] == 0x22 && get_u32(b + kCfdpHdrLen) == 8,
        "CFDP File Data built in place");

  n = cfdp_build_eof(b, sizeof b, 1, 0x000C0003, 2, kCfdpCondNoError, 0x9F686A6C, 9);
  CHECK(n == 20 && hex(b, n) == "24000a03" "01000c000302" "04" "00" "9f686a6c" "00000009",
        "CFDP EOF(no error) octets: no fault location");
  CHECK(cfdp_parse(b, n, p) && p.directive == kCfdpDirEof && p.condition == 0 &&
        p.checksum == 0x9F686A6C && p.file_size == 9 && !p.has_fault_entity, "CFDP EOF parse");
  n = cfdp_build_eof(b, sizeof b, 1, 0x000C0003, 2, kCfdpCondCancel, 0x01020304, 384);
  CHECK(n == kCfdpEofMaxLen && hex(b, n) == "24000d03" "01000c000302" "04" "f0" "01020304" "00000180" "060101",
        "CFDP EOF(cancel) octets: condition 15 + Entity ID TLV of the source");
  CHECK(cfdp_parse(b, n, p) && p.condition == kCfdpCondCancel && p.file_size == 384 &&
        p.has_fault_entity && p.fault_entity == 1, "CFDP EOF(cancel) parse");
  b[kCfdpHdrLen] = 0x05;                         // a Finished PDU is not class-1 sender traffic
  CHECK(!cfdp_parse(b, n, p), "CFDP parse refuses other directives");
  CHECK(kCfdpMaxPduLen == 146 && kCfdpFileDataMaxLen == 142, "CFDP PDU size limits (146 / 142)");
}

HOST_TEST(cfdp_transfer) {
  static uint8_t d[kFileLen];
  fill_source(d, sizeof d, kSourceSeed);
  const uint32_t ref = ref_checksum(d, sizeof d);
  bool wrote = false;
  if (FILE* f = std::fopen((out + "/cfdp_source.bin").c_str(), "wb")) {
    wrote = std::fwrite(d, 1, sizeof d, f) == sizeof d;
    wrote &= std::fclose(f) == 0;
  }
  CHECK(wrote, "CFDP source file written");

  BufSource src(d, sizeof d);
  CfdpSender s;
  CHECK(!s.active() && s.next_pdu(nullptr, 0) == 0, "CFDP sender idle at boot");
  CHECK(s.begin(kSeq, kName, &src), "CFDP sender begin");
  CHECK(!s.begin(kSeq + 1, kName, &src), "CFDP sender refuses a second transaction while active");
  uint8_t small[kCfdpMaxPduLen - 1];
  CHECK(s.next_pdu(small, sizeof small) == 0 && s.state() == CfdpSender::METADATA,
        "CFDP sender: a short buffer advances nothing");

  // Step through by hand to check progress at a known point.
  uint8_t buf[kCfdpMaxPduLen];
  std::vector<Pdu> v;
  size_t n = s.next_pdu(buf, sizeof buf);
  v.push_back(Pdu(buf, buf + n));
  CHECK(s.state() == CfdpSender::FILE_DATA && s.progress_permille() == 0, "CFDP Metadata first, progress 0");
  for (int i = 0; i < 4; ++i) {
    n = s.next_pdu(buf, sizeof buf);
    v.push_back(Pdu(buf, buf + n));
  }
  CHECK(s.sent() == 512 && s.progress_permille() == 512 && std::strcmp(s.name(), kName) == 0,
        "CFDP progress 512 permille after 4 x 128 octets");
  std::vector<Pdu> rest = drain(s);
  v.insert(v.end(), rest.begin(), rest.end());
  CHECK(!s.active() && s.progress_permille() == 1000 && s.last_condition() == kCfdpCondNoError &&
        s.transactions() == 1 && s.next_pdu(buf, sizeof buf) == 0,
        "CFDP sender idle after EOF, progress 1000");

  std::vector<uint8_t> file;
  CfdpPdu md, eof;
  int n_fd = 0;
  bool ok = reassemble(v, file, md, eof, n_fd);
  CHECK(ok && v.size() == 10 && n_fd == 8, "CFDP transfer: Metadata + 8 File Data + EOF");
  bool seg_ok = true;
  for (size_t i = 1; i + 1 < v.size(); ++i) seg_ok &= v[i].size() == kCfdpHdrLen + 4 + (i < 8 ? 128 : 104);
  CHECK(seg_ok, "CFDP transfer: 7 x 128 + 104 data octets");
  CHECK(ok && md.file_size == kFileLen && md.src_name_len == sizeof kName - 1 &&
        !std::memcmp(md.src_name, kName, sizeof kName - 1) && md.hdr.seq == kSeq && md.hdr.source == 1 &&
        md.hdr.dest == 2, "CFDP transfer: Metadata size / name / ids");
  CHECK(file.size() == kFileLen && !std::memcmp(file.data(), d, kFileLen), "CFDP transfer: file reassembled");
  CHECK(ok && eof.condition == 0 && eof.file_size == kFileLen && eof.checksum == ref && s.checksum() == ref,
        "CFDP transfer: EOF checksum == reference");
  CHECK(write_pdus(out + "/cfdp_pdus.txt", v), "CFDP transfer PDUs written");

  // max_bytes truncation, 96-octet relay segments: the EGSE-style vector
  CHECK(s.begin(kRelaySeq, kRelayName, &src, kRelayLen, kCfdpRelaySegLen), "CFDP begin max 600, seg 96");
  v = drain(s);
  ok = reassemble(v, file, md, eof, n_fd);
  CHECK(ok && v.size() == 9 && n_fd == 7 && md.file_size == kRelayLen && eof.file_size == kRelayLen &&
        eof.checksum == ref_checksum(d, kRelayLen) && file.size() == kRelayLen &&
        !std::memcmp(file.data(), d, kRelayLen),
        "CFDP transfer: max_bytes 600 in 96-octet segments (6 x 96 + 24)");
  bool relay_ok = true;
  for (const Pdu& p : v) relay_ok &= CfdpRelayMonitor::acceptable(p.data(), p.size());
  CHECK(relay_ok, "CFDP relay vector: every PDU acceptable to the MCU relay");
  CHECK(write_pdus(out + "/cfdp_relay_pdus.txt", v), "CFDP relay PDUs written");

  // empty file: Metadata + EOF
  BufSource empty(d, 0);
  CHECK(s.begin(kSeq + 2, "empty.bin", &empty), "CFDP begin empty file");
  v = drain(s);
  CHECK(v.size() == 2 && reassemble(v, file, md, eof, n_fd) && md.file_size == 0 && eof.file_size == 0 &&
        eof.checksum == 0 && s.progress_permille() == 1000, "CFDP transfer: empty file = Metadata + EOF");

  // a source that stops delivering at 256 -> EOF condition 4 (filestore rejection)
  BufSource flaky(d, sizeof d);
  flaky.fail_at = 256;
  CHECK(s.begin(kSeq + 3, kName, &flaky), "CFDP begin failing source");
  v = drain(s);
  CHECK(v.size() == 4 && reassemble(v, file, md, eof, n_fd) && eof.condition == kCfdpCondFilestoreRejection &&
        eof.file_size == 256 && eof.checksum == ref_checksum(d, 256) && eof.has_fault_entity &&
        s.last_condition() == kCfdpCondFilestoreRejection && s.progress_permille() == 256,
        "CFDP transfer: source failure -> EOF(condition 4, 256 octets sent)");

  // a source claiming more octets than asked is treated as failed
  BufSource liar(d, sizeof d);
  liar.overclaim = 1;
  CHECK(s.begin(kSeq + 4, kName, &liar), "CFDP begin over-claiming source");
  v = drain(s);
  CHECK(v.size() == 2 && cfdp_parse(v[1].data(), v[1].size(), eof) &&
        eof.condition == kCfdpCondFilestoreRejection && eof.file_size == 0,
        "CFDP transfer: over-claiming source -> EOF(condition 4) before any File Data");

  CHECK(!s.begin(kSeq, "", &src) && !s.begin(kSeq, kName, nullptr) && !s.begin(kSeq, kName, &src, 0, 129) &&
        !s.begin(kSeq, kName, &src, 0, 0) && !s.begin(kSeq, nullptr, &src) && !s.begin(kSeq, "a/b", &src) &&
        !s.active(), "CFDP begin refuses empty / null / path name, no source, bad segment");
}

HOST_TEST(cfdp_cancel) {
  static uint8_t d[kFileLen];
  fill_source(d, sizeof d, kSourceSeed);         // same content as cfdp_source.bin
  BufSource src(d, sizeof d);
  CfdpSender s;
  CHECK(s.begin(kSeq, kName, &src), "CFDP cancel: begin");
  std::vector<Pdu> v = drain(s, 3);
  std::vector<uint8_t> file;
  CfdpPdu md, eof;
  int n_fd = 0;
  CHECK(reassemble(v, file, md, eof, n_fd) && v.size() == 5 && n_fd == 3, "CFDP cancel: Metadata + 3 File Data + EOF");
  CHECK(eof.condition == kCfdpCondCancel && eof.file_size == 384 && eof.checksum == ref_checksum(d, 384) &&
        eof.has_fault_entity && eof.fault_entity == kCfdpEntityVgq1,
        "CFDP cancel: EOF(15), 384 octets sent, checksum of those, fault location VGQ-1");
  CHECK(!s.active() && s.last_condition() == kCfdpCondCancel && s.progress_permille() == 384,
        "CFDP cancel: sender idle, progress frozen at 384 permille");
  CHECK(write_pdus(out + "/cfdp_cancel_pdus.txt", v), "CFDP cancel PDUs written");

  s.cancel();
  uint8_t buf[kCfdpMaxPduLen];
  CHECK(s.next_pdu(buf, sizeof buf) == 0, "CFDP cancel when idle does nothing");
  CHECK(s.begin(kSeq + 1, kName, &src), "CFDP cancel: second begin");
  s.cancel();
  v = drain(s);
  CHECK(v.size() == 1 && cfdp_parse(v[0].data(), v[0].size(), eof) && eof.directive == kCfdpDirEof &&
        eof.condition == kCfdpCondCancel && eof.file_size == 0, "CFDP cancel before the Metadata: EOF(15) only");
  CHECK(s.begin(kSeq + 2, "x.bin", &src, 128), "CFDP cancel: third begin");
  s.next_pdu(buf, sizeof buf);                   // Metadata
  s.next_pdu(buf, sizeof buf);                   // the only File Data -> EOF pending
  s.cancel();                                    // too late: the file is complete
  v = drain(s);
  CHECK(v.size() == 1 && cfdp_parse(v[0].data(), v[0].size(), eof) && eof.condition == 0 &&
        eof.file_size == 128, "CFDP cancel after the last segment: EOF(no error) still sent");
}

HOST_TEST(cfdp_relay_monitor) {
  static uint8_t d[600];
  fill_source(d, sizeof d, 0xBADC0DE5);
  BufSource src(d, sizeof d);
  CfdpSender s;
  CfdpRelayMonitor m;
  const uint32_t seq = 7u | kCfdpEgseSeqFlag;
  CHECK(s.begin(seq, "cadu_20261010.bin", &src, 0, kCfdpRelaySegLen), "CFDP relay: begin");
  std::vector<Pdu> v = drain(s);
  bool all_ok = true, mid_ok = false;
  uint32_t t = 0xFFFFF000u;                      // the millisecond clock wraps mid-transfer
  for (size_t i = 0; i < v.size(); ++i) {
    all_ok &= CfdpRelayMonitor::acceptable(v[i].data(), v[i].size()) && v[i].size() <= kCfdpRelayMaxPduLen;
    m.observe(v[i].data(), v[i].size(), t);
    m.observe(v[i].data(), v[i].size(), t);      // EGSE retry: idempotent
    if (i == 3) mid_ok = m.active() && m.seq() == seq && m.progress_permille() == 480 && !m.expired(t + 1000);
    t += 2000;
  }
  CHECK(all_ok, "CFDP relay: every EGSE PDU acceptable and <= 110 octets");
  CHECK(mid_ok, "CFDP relay monitor: progress 480 permille after 3 x 96 of 600");
  CHECK(!m.active() && m.progress_permille() == 1000 && m.last_condition() == 0 && !m.expired(t + kCfdpRelayTimeoutMs),
        "CFDP relay monitor: done at EOF, never expires once done");

  // a relay that goes silent expires (wrap-safe) and reset() clears it
  m.reset();
  m.observe(v[0].data(), v[0].size(), 0xFFFFFF00u);
  CHECK(m.active() && !m.expired(0x00000100u, 1000) && m.expired(0x00000400u, 1000),
        "CFDP relay monitor: expiry across the clock wrap");
  m.reset();
  CHECK(!m.active() && m.progress_permille() == 0 && m.seq() == 0, "CFDP relay monitor: reset");

  uint8_t b[kCfdpMaxPduLen];
  size_t n = cfdp_build_file_data(b, sizeof b, 1, 7, 2, 0, d, 10);
  CHECK(!CfdpRelayMonitor::acceptable(b, n), "CFDP relay refuses an MCU sequence number");
  n = cfdp_build_file_data(b, sizeof b, 1, seq, 2, 0, d, 97);
  CHECK(!CfdpRelayMonitor::acceptable(b, n), "CFDP relay refuses 97 data octets");
  n = cfdp_build_file_data(b, sizeof b, 1, seq, 2, 0, d, 0);
  CHECK(!CfdpRelayMonitor::acceptable(b, n), "CFDP relay refuses an empty File Data PDU");
  n = cfdp_build_file_data(b, sizeof b, 1, seq, 3, 0, d, 10);
  CHECK(!CfdpRelayMonitor::acceptable(b, n), "CFDP relay refuses another destination");
  n = cfdp_build_file_data(b, sizeof b, 2, seq, 2, 0, d, 10);
  CHECK(!CfdpRelayMonitor::acceptable(b, n), "CFDP relay refuses another source entity");
  n = cfdp_build_file_data(b, sizeof b, 1, seq, 2, 0, d, 10);
  b[0] |= 0x08;                                  // toward the sender
  CHECK(!CfdpRelayMonitor::acceptable(b, n), "CFDP relay refuses direction 1");
  b[0] &= (uint8_t)~0x0C;                        // acknowledged mode (class 2)
  CHECK(!CfdpRelayMonitor::acceptable(b, n) && !CfdpRelayMonitor::acceptable(d, 40),
        "CFDP relay refuses acknowledged mode and garbage");

  CHECK(CfdpRelayMonitor::acceptable(b, cfdp_build_metadata(b, sizeof b, 1, seq, 2, 9, std::string(46, 'n').c_str())),
        "CFDP relay accepts a 46-character name (110-octet Metadata)");
  n = cfdp_build_metadata(b, sizeof b, 1, seq, 2, 9, std::string(47, 'n').c_str());
  CHECK(n == 112 && !CfdpRelayMonitor::acceptable(b, n), "CFDP relay refuses a 47-character name (112 octets)");
  n = cfdp_build_metadata(b, sizeof b, 1, seq, 2, 9, "ok.bin");
  b[kCfdpHdrLen + 1] = 0x03;                     // CRC-32 checksum type
  CHECK(!CfdpRelayMonitor::acceptable(b, n), "CFDP relay refuses a non-modular checksum type");
  n = cfdp_build_metadata(b, sizeof b, 1, seq, 2, 9, "ok.bin");
  b[kCfdpHdrLen + 7] = '/';                      // source name "/k.bin"
  CHECK(!CfdpRelayMonitor::acceptable(b, n), "CFDP relay refuses a name with a path separator");
  n = cfdp_build_eof(b, sizeof b, 1, seq, 2, kCfdpCondCancel, 0, 0);
  CHECK(CfdpRelayMonitor::acceptable(b, n), "CFDP relay accepts EOF(cancel)");
}
