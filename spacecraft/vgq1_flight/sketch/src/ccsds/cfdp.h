// =============================================================================
//  CFDP: CCSDS File Delivery Protocol (727.0-B-5), class 1 (unacknowledged)
//
//  Delivers a file (the SSR contents, or an EGSE log relayed through the MCU) to
//  the ground as a sequence of Protocol Data Units, one PDU per TM Space Packet
//  (APID 0x030, CUC secondary header) on VC 3. Class 1 has no retransmission:
//  the ground detects gaps from the File Data offsets and verifies the result
//  with the file checksum carried in the EOF PDU.
//
//  PDU header, 10 octets with this mission's managed parameters (bit widths):
//    version 3 '001' | PDU type 1 (0 directive, 1 file data) | direction 1 (0 =
//    toward the receiver) | transmission mode 1 (1 = unacknowledged) | CRC flag 1
//    (0) | large-file flag 1 (0: 32-bit offsets and sizes) | PDU data field
//    length 16 | segmentation control 1 (0) | entity-ID length 3 (0 = 1 octet) |
//    segment-metadata flag 1 (0) | transaction-sequence-number length 3 (3 = 4
//    octets) | source entity ID 8 | transaction sequence number 32 | destination
//    entity ID 8
//  Metadata PDU (directive 0x07): reserved 1 | closure requested 1 (0) |
//    reserved 2 | checksum type 4 (0 = modular) | file size 32 | source file name
//    LV | destination file name LV (same name). No TLVs.
//  File Data PDU: offset 32 | data. No segment metadata.
//  EOF PDU (directive 0x04): condition code 4 | spare 4 | file checksum 32 |
//    file size 32 | fault location (Entity ID TLV 06 01 <entity>), present only
//    when the condition code is not "no error".
//  The PDU data field length counts everything after the header (directive
//  code + parameters, or offset + data).
//
//  Modular checksum (727.0-B-5 annex): the file is a sequence of big-endian
//  4-octet words aligned on file offset 0 (the octet at offset o is byte o & 3
//  of word o & ~3; a short last word is zero-padded); the checksum is their sum
//  mod 2^32. Each octet's contribution depends only on its offset, so the sum
//  can be accumulated segment by segment in any order.
//
//  Source of the field layouts: the standard's text could not be retrieved in
//  the development environment (egress policy); every layout, directive code,
//  condition code and the checksum were cross-checked against two independent
//  727.0-B-5 implementations (spacepackets 0.32.0 and cfdp-py 0.7.0). VERIFY
//  against the standard's EOF PDU table that the fault-location TLV is required
//  (not merely permitted) for a non-zero condition code; the ground accepts an
//  EOF with or without it.
//
//  Entities (managed parameters): 0x01 VGQ-1 (MCU and EGSE alike), 0x02 DSS-Q1.
//  Transaction sequence numbers: MCU transfers < 0x80000000, EGSE-built
//  transfers relayed through the MCU >= 0x80000000 (cfdp_tx.py), so one
//  source entity never reuses a number.
//
//  Pure C++ (no Arduino, no heap, no recursion, bounded loops), host-tested by
//  tools/host_test/test_cfdp.cpp against the independent ground receiver
//  ground/dssq/ccsds/cfdp.py.
// =============================================================================
#pragma once
#include "ccsds_types.h"

namespace ccsds {

static const uint8_t  kCfdpVersion        = 1;      // '001': 727.0-B-4 and later
static const size_t   kCfdpHdrLen         = 10;     // fixed 4 + 1 + 4 + 1
static const uint8_t  kCfdpEntityVgq1     = 0x01;   // spacecraft (MCU and EGSE)
static const uint8_t  kCfdpEntityDssq1    = 0x02;   // ground station
static const uint32_t kCfdpEgseSeqFlag    = 0x80000000UL;  // EGSE-built transactions

// File directive codes used by class 1
static const uint8_t kCfdpDirEof      = 0x04;
static const uint8_t kCfdpDirMetadata = 0x07;
// Condition codes this sender emits
static const uint8_t kCfdpCondNoError            = 0;
static const uint8_t kCfdpCondFilestoreRejection = 4;   // the source stopped delivering octets
static const uint8_t kCfdpCondCancel             = 15;  // cancel request received (CFDPCANCEL)
static const uint8_t kCfdpChecksumModular        = 0;
static const uint8_t kCfdpTlvEntityId            = 0x06; // fault-location TLV type

// HK cfdp_state values (tlm_packets.h / ground dictionary)
static const uint8_t kCfdpHkIdle  = 0;
static const uint8_t kCfdpHkSsr   = 1;   // CfdpSender active (SSR contents)
static const uint8_t kCfdpHkRelay = 2;   // CfdpRelayMonitor active (EGSE file)

// Segment sizes (data octets per File Data PDU). Flight: the 142-octet PDU in a
// 154-octet Space Packet fits one TM-AE frame data field (158 octets); in
// USLP-AE mode (TFDZ 153) it spans two frames, which both framers support.
// EGSE relay: every relayed PDU is <= 110 octets so its 220-character hex text
// plus the msgpack request envelope fits the 256-octet Router Bridge RPC buffer.
static const size_t kCfdpFlightSegLen = 128;
static const size_t kCfdpRelaySegLen  = 96;
static const size_t kCfdpMaxNameLen   = 64;         // mission limit (an LV allows 255)
static const size_t kCfdpMetadataFixedLen = kCfdpHdrLen + 1 + 1 + 4 + 2;               // 18 + names
static const size_t kCfdpMetadataMaxLen = kCfdpMetadataFixedLen + 2 * kCfdpMaxNameLen; // 146
static const size_t kCfdpFileDataMaxLen = kCfdpHdrLen + 4 + kCfdpFlightSegLen;         // 142
static const size_t kCfdpEofMaxLen      = kCfdpHdrLen + 1 + 1 + 4 + 4 + 3;             // 23 with TLV
static const size_t kCfdpRelayMaxPduLen = kCfdpHdrLen + 4 + kCfdpRelaySegLen;          // 110
// Longest file name an EGSE Metadata PDU can carry within kCfdpRelayMaxPduLen.
static const size_t kCfdpRelayMaxNameLen = (kCfdpRelayMaxPduLen - kCfdpMetadataFixedLen) / 2;  // 46
// Output buffer that holds any PDU this module builds.
static const size_t kCfdpMaxPduLen =
    kCfdpMetadataMaxLen > kCfdpFileDataMaxLen ? kCfdpMetadataMaxLen : kCfdpFileDataMaxLen;
// A relay with no PDU for this long is considered over (EGSE stopped or died);
// matches the ground receiver's default inactivity limit.
static const uint32_t kCfdpRelayTimeoutMs = 600000UL;

// Transaction sequence number of the n-th MCU transfer in SCLK partition
// `partition` (the EGSE boot counter): unique across 32768 reboots, always
// < 2^31. OR in kCfdpEgseSeqFlag for a transfer the EGSE builds (CFDPPUT 1/2).
inline uint32_t cfdp_make_seq(uint16_t partition, uint16_t n) {
  return ((uint32_t)(partition & 0x7FFFu) << 16) | n;
}

// Mission file-name rule (both ends): 1..max_len characters from
// [A-Z a-z 0-9 . _ -], not starting with '.', so a name can never form a path.
bool cfdp_name_ok(const uint8_t* name, size_t n, size_t max_len = kCfdpMaxNameLen);

// Writes "ssr_p<partition>_<sclk_s>.bin" (decimal, NUL-terminated) into `out`.
// Returns the length without the NUL (at most 25), or 0 (and an empty string)
// when `cap` is too small; a 26-octet buffer always suffices.
size_t cfdp_ssr_file_name(char* out, size_t cap, uint16_t partition, uint32_t sclk_s);

struct CfdpHeader {
  bool     file_data = false;       // PDU type: 0 file directive, 1 file data
  bool     to_sender = false;       // direction: 0 toward the file receiver
  bool     unacknowledged = true;   // transmission mode 1 = class 1
  bool     crc = false;             // PDU CRC present (never in this mission)
  bool     large_file = false;      // 64-bit FSS (never in this mission)
  uint16_t data_len = 0;            // PDU data field length, octets
  uint8_t  source = kCfdpEntityVgq1;
  uint32_t seq = 0;
  uint8_t  dest = kCfdpEntityDssq1;
};

// Writes the 10-octet header into `p` (>= kCfdpHdrLen octets); returns
// kCfdpHdrLen (0 for a null pointer).
size_t cfdp_put_header(uint8_t* p, const CfdpHeader& h);
// Reads a header in this mission's profile. Returns kCfdpHdrLen, or 0 when `len`
// is short, the version is not '001', the CRC / large-file / segment-metadata
// flags are set, the entity IDs are not 1 octet or the sequence number not 4
// octets, or the data field runs past `len` (trailing octets are allowed here).
size_t cfdp_get_header(const uint8_t* p, size_t len, CfdpHeader& h);

// Modular checksum accumulation: returns `sum` plus the contribution of `n`
// octets that sit at file offset `offset` (any alignment, any order).
uint32_t cfdp_checksum(uint32_t sum, uint32_t offset, const uint8_t* data, size_t n);

// PDU builders. Each writes one complete PDU (header included) into `out` and
// returns its length, or 0 when it does not fit in `cap` or an argument is out
// of range (name failing cfdp_name_ok, null pointers). `name` is a
// NUL-terminated file name used as both the source and destination file name.
size_t cfdp_build_metadata(uint8_t* out, size_t cap, uint8_t source, uint32_t seq, uint8_t dest,
                           uint32_t file_size, const char* name);
// `data` may point into `out` itself (the sender reads segments in place).
size_t cfdp_build_file_data(uint8_t* out, size_t cap, uint8_t source, uint32_t seq, uint8_t dest,
                            uint32_t offset, const uint8_t* data, size_t n);
// condition != kCfdpCondNoError appends the fault-location Entity ID TLV naming
// `source` (the entity at which the cancellation was initiated).
size_t cfdp_build_eof(uint8_t* out, size_t cap, uint8_t source, uint32_t seq, uint8_t dest,
                      uint8_t condition, uint32_t checksum, uint32_t file_size);

// Parsed view of one PDU; the pointers refer into the parsed buffer.
struct CfdpPdu {
  CfdpHeader hdr;
  uint8_t  directive = 0;          // directive code (0 for File Data)
  // Metadata
  bool     closure_requested = false;
  uint8_t  checksum_type = 0;
  const uint8_t* src_name = nullptr;  uint8_t src_name_len = 0;
  const uint8_t* dst_name = nullptr;  uint8_t dst_name_len = 0;
  // Metadata and EOF
  uint32_t file_size = 0;
  // File Data
  uint32_t offset = 0;
  const uint8_t* data = nullptr;   size_t data_len = 0;
  // EOF
  uint8_t  condition = 0;
  uint32_t checksum = 0;
  bool     has_fault_entity = false; uint8_t fault_entity = 0;
};
// Parses exactly one Metadata, File Data or EOF PDU occupying all `len`
// octets. Returns false for a bad header, trailing octets, a truncated or
// inconsistent parameter field, or any other directive.
bool cfdp_parse(const uint8_t* pdu, size_t len, CfdpPdu& out);

// ---------------------------------------------------------------------------
// Where the sender gets the file from (SSR snapshot, test buffer, ...).
// ---------------------------------------------------------------------------
class CfdpSource {
 public:
  virtual ~CfdpSource() {}
  // File size in octets; read once by CfdpSender::begin() and then held fixed
  // (the SSR source must not change while a transfer runs: recording paused).
  virtual uint32_t size() = 0;
  // Copies up to `n` (<= kCfdpFlightSegLen) octets from file offset `off` into
  // `dst` and returns the count; must never write more than `n` octets.
  // 0 = cannot deliver: the sender ends the transaction with condition 4.
  // Called at most once per CfdpSender::next_pdu(); must not block.
  virtual size_t read(uint32_t off, uint8_t* dst, size_t n) = 0;
};

// ---------------------------------------------------------------------------
// Class-1 sender for one transaction at a time:
//   begin() -> next_pdu(): Metadata, File Data ..., EOF(no error) -> idle
//   cancel() at any time -> the next next_pdu() is EOF(cancel) -> idle
// next_pdu() is the pacing point: the integrator calls it once per free VC 3
// slot (while the VC 3 backlog is below its threshold) and wraps the PDU in a
// Space Packet. It never blocks; each call reads at most one segment.
// ---------------------------------------------------------------------------
class CfdpSender {
 public:
  enum State : uint8_t { IDLE = 0, METADATA = 1, FILE_DATA = 2, EOF_PENDING = 3 };

  // Starts transaction `seq` for `source` (not owned; must stay valid until
  // idle). `name` is copied and must pass cfdp_name_ok(). `max_bytes` > 0 sends
  // only the first max_bytes octets (the Metadata file size is then the
  // truncated size). seg_len = data octets per File Data PDU
  // (1..kCfdpFlightSegLen). Returns false (and changes nothing) while a
  // transaction is active or for bad arguments.
  bool begin(uint32_t seq, const char* name, CfdpSource* source, uint32_t max_bytes = 0,
             size_t seg_len = kCfdpFlightSegLen, uint8_t source_entity = kCfdpEntityVgq1,
             uint8_t dest_entity = kCfdpEntityDssq1);
  // Builds the next PDU into `out` (cap >= kCfdpMaxPduLen); returns its length,
  // or 0 when idle (or cap is too small: nothing advances then).
  size_t next_pdu(uint8_t* out, size_t cap);
  // Requests cancellation: the next PDU is EOF(condition 15, checksum of the
  // octets sent so far, file size = octets sent so far). No effect when idle or
  // when the EOF is already pending.
  void cancel();

  State state() const { return state_; }
  bool active() const { return state_ != IDLE; }
  uint32_t seq() const { return seq_; }
  uint32_t file_size() const { return size_; }
  uint32_t sent() const { return off_; }            // file octets sent so far
  uint32_t checksum() const { return sum_; }        // over the octets sent so far
  const char* name() const { return name_; }
  // Octets sent / file size in 0.1 % (1000 once a no-error EOF is out;
  // HK cfdp_progress_permille).
  uint16_t progress_permille() const;
  // Condition code of the last EOF sent (valid once idle again).
  uint8_t last_condition() const { return last_cond_; }
  uint32_t transactions() const { return count_; }  // EOFs sent since boot

 private:
  size_t build_eof(uint8_t* out, size_t cap);
  CfdpSource* src_ = nullptr;
  State    state_ = IDLE;
  uint32_t seq_ = 0, size_ = 0, off_ = 0, sum_ = 0, count_ = 0;
  uint8_t  source_ = kCfdpEntityVgq1, dest_ = kCfdpEntityDssq1;
  uint8_t  cond_ = kCfdpCondNoError, last_cond_ = kCfdpCondNoError;
  uint8_t  seg_ = (uint8_t)kCfdpFlightSegLen;
  bool     done_ = false;                           // EOF sent: progress reads 1000
  char     name_[kCfdpMaxNameLen + 1] = {0};
};

// ---------------------------------------------------------------------------
// Follows the PDUs the EGSE relays through the MCU (CFDPPUT source 1/2) so HK
// can report cfdp_seq / cfdp_progress_permille while cfdp_state = 2.
// RPC handler order: acceptable() -> queue the Space Packet -> observe() (only
// once it was queued; observe() is idempotent, so an EGSE retry of the same PDU
// changes nothing). The main loop calls expired() and reset()s a relay that
// went silent, so HK never reports a dead relay as running.
// ---------------------------------------------------------------------------
class CfdpRelayMonitor {
 public:
  // True for a well-formed class-1 Metadata / File Data / EOF PDU of at most
  // kCfdpRelayMaxPduLen octets from VGQ-1 to DSS-Q1, toward the receiver, with
  // an EGSE sequence number (>= 0x80000000), 1..kCfdpRelaySegLen data octets
  // and (Metadata) modular checksum and a name passing cfdp_name_ok().
  // Anything else must be refused.
  static bool acceptable(const uint8_t* pdu, size_t len);
  // `now_ms` = MCU millisecond clock (wraps; only differences are used).
  void observe(const uint8_t* pdu, size_t len, uint32_t now_ms = 0);
  // True while active() and no PDU was observed for `timeout_ms`.
  bool expired(uint32_t now_ms, uint32_t timeout_ms = kCfdpRelayTimeoutMs) const;
  void reset() { active_ = false; done_ = false; seq_ = 0; size_ = 0; hi_ = 0; cond_ = 0; last_ms_ = 0; }
  bool active() const { return active_; }           // a PDU was relayed and no EOF yet
  uint32_t seq() const { return seq_; }
  // Highest File Data end offset / Metadata file size in 0.1 % (1000 after a
  // no-error EOF; 0 while the Metadata has not been seen).
  uint16_t progress_permille() const;
  uint8_t last_condition() const { return cond_; }  // of the last relayed EOF

 private:
  bool active_ = false, done_ = false;
  uint32_t seq_ = 0, size_ = 0, hi_ = 0, last_ms_ = 0;
  uint8_t cond_ = kCfdpCondNoError;
};

}  // namespace ccsds
