// =============================================================================
//  VGQ-1 command dictionary (flight half), v3 - opcodes, argument layouts and
//  ranges MUST match dssq-groundstation/python/dssq/dictionary.py COMMANDS (Phase B
//  specification PB-2.3; tools/host_test writes command_table.txt and the ground
//  cross-check compares every accept/reject decision). Pure C++ (host-testable).
//
//  A telecommand reaches the dispatcher as a CCSDS TC Space Packet (133.0-B-2)
//  carried in a TC frame accepted by SDLS and FARM-1:
//     packet primary header (6) : version 0, type 1 (TC), no secondary header,
//                                 APID 0x0C0, sequence flags 11 (unsegmented)
//     user data                 : opcode (1) | arguments (big-endian)
//
//  Two-stage parser (contract 29): parse_tc_header() checks the packet header,
//  parse_user_data() decodes and range-checks opcode + arguments. The second stage
//  alone validates commands for the stored-sequence table (validate_stored_command
//  is the CommandSequencer validator), so a stored command gets exactly the checks
//  a live one gets. Execution-time checks (resource busy, hardware absent, the
//  MAGCAL matrix, the SEQLOAD time and policy) belong to the executive.
// =============================================================================
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace vgq {

enum Opcode : uint8_t {
  OP_NOOP = 0x01, OP_MODE = 0x02, OP_TXPWR = 0x03, OP_AIRRATE = 0x04, OP_FPERIOD = 0x05,
  OP_PKTRATE = 0x06, OP_PLAYBACK = 0x07, OP_SSRCLEAR = 0x08, OP_PING = 0x09, OP_SENSORS = 0x0A,
  OP_RSTCNT = 0x0B, OP_REBOOT = 0x0C, OP_MAGCC = 0x0D, OP_CMDLOSS = 0x0E, OP_FDIRMASK = 0x0F,
  OP_TXINHIBIT = 0x10, OP_EVRLEVEL = 0x11, OP_TIMECORR = 0x12,
  OP_RFSCAN = 0x13,      // u8 first_ch, u8 last_ch: ambient-noise spectrum survey
  OP_HIBERNATE = 0x14,   // u16 beacon_s: hibernation with a periodic HK beacon
  OP_CHANNEL = 0x15,     // u8 ch, u16 revert_s: coordinated channel change (auto-revert)
  OP_SPECCFG = 0x16,     // u8 gain, u8 cycles, u8 lamps, u8 led_cur, u8 ind: AS7265X setup
  OP_CFDPPUT = 0x17,     // u8 source, u16 max_kib: CFDP class-1 file transfer
  OP_CFDPCANCEL = 0x18,  // cancel the running CFDP transfer / relay
  OP_RADIOOPT = 0x19,    // u8 flags: b0 LBT/CAD, b1 Rx boosted gain
  OP_SPECMODE = 0x1A,    // u8 mode, u8 n: acquisition mode
  OP_SPECBASE = 0x1B,    // u16 threshold_cdeg: spectral change-detection baseline
  OP_SPECDIAG = 0x1C,    // u8 device, u8 n_sub: AS7265X diagnostic dump (EVR 0x0703)
  OP_SEQLOAD = 0x1D,     // 19 octets: stored-sequence entry (autonomy.h)
  OP_SEQCTRL = 0x1E,     // u8 action, u8 slot
  OP_MAGBIST = 0x1F,     // u8 unit: RM3100 built-in self-test
  OP_MAGCAL = 0x20,      // 19 octets: RM3100 hard/soft-iron calibration (autonomy.h)
};
static const uint8_t kOpcodeMax = OP_MAGCAL;

enum CmdError : uint8_t {
  CE_OK = 0, CE_UNKNOWN_OPCODE = 1, CE_BAD_LENGTH = 2, CE_BAD_ARGUMENT = 3,
  CE_HARDWARE = 5,       // instrument / radio absent
  CE_BAD_MAGIC = 6,      // REBOOT without 0xB007
  CE_BUSY = 7,           // resource in use or not ready yet - retry later (PB-2.2)
};                       // 4 stays retired (was NOT_ALLOWED_IN_MODE)

enum CmdStage : uint8_t { STAGE_ACCEPTED = 1, STAGE_EXECUTED = 2, STAGE_FAILED = 3 };

static const uint16_t kRebootMagic = 0xB007;
static const size_t kTcPktHdrLen = 6;         // TC packet primary header (no secondary header)

struct Command {
  uint8_t opcode = 0;
  uint8_t a8 = 0, b8 = 0, c8 = 0, d8 = 0, e8 = 0;   // 8-bit arguments in order
  uint16_t a16 = 0, b16 = 0;                         // 16-bit arguments in order
  uint32_t a32 = 0;                                  // 32-bit argument
  const uint8_t* args = nullptr;                     // first argument octet (nullptr: none)
  uint8_t nargs = 0;                                 // argument octets
};
// Argument mapping per opcode (PB-2.3): MODE/TXPWR/PLAYBACK/EVRLEVEL/RADIOOPT/MAGBIST a8;
// AIRRATE/CHANNEL a8 + a16; FPERIOD/SENSORS/REBOOT/MAGCC/FDIRMASK/TXINHIBIT/HIBERNATE/
// SPECBASE a16; PKTRATE a16 apid + b16 period; PING/CMDLOSS a32; RFSCAN a8 first + b8
// last; SPECCFG a8 gain, b8 cycles, c8 lamps, d8 led_cur, e8 ind; CFDPPUT a8 source +
// a16 max_kib; SPECMODE a8 mode + b8 n; SPECDIAG a8 device + b8 n_sub; SEQCTRL a8
// action + b8 slot; SEQLOAD / MAGCAL: args/nargs only (19 octets, decoded and fully
// checked by CommandSequencer::load / MagCalStore::load).

// Header check: version 0, type 1, no secondary header, APID 0x0C0, sequence flags 11
// (CE_BAD_ARGUMENT otherwise) and a packet length that matches `len` exactly with at
// least the opcode (CE_BAD_LENGTH). On CE_OK `user`/`n` give opcode + arguments.
CmdError parse_tc_header(const uint8_t* pkt, size_t len, const uint8_t*& user, size_t& n);
// Decodes and range-checks opcode + arguments (n >= 1): unknown opcode -> CE_UNKNOWN_OPCODE,
// argument count not exact -> CE_BAD_LENGTH, REBOOT magic -> CE_BAD_MAGIC, any range or
// "check" rule (PB-2.3) -> CE_BAD_ARGUMENT. Identical for live and stored commands; no
// side effects. `c` is reset first (opcode filled whenever n >= 1).
CmdError parse_user_data(const uint8_t* u, size_t n, Command& c);
CmdError parse_tc_packet(const uint8_t* pkt, size_t len, Command& c);   // header + user data
// SeqCmdValidator for CommandSequencer::set_validator (autonomy.h): parse_user_data,
// no side effects; returns the CmdError (0 = acceptable). ctx is unused.
uint8_t validate_stored_command(void* ctx, const uint8_t* cmd, uint8_t len);

}  // namespace vgq
