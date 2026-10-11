// =============================================================================
//  VGQ-1 command dictionary (flight half) — opcodes MUST match
//  ground/dssq/dictionary.py COMMANDS. Pure C++ (host-testable).
//
//  A telecommand reaches the dispatcher as a CCSDS TC Space Packet carried in a
//  TC frame accepted by FARM-1:
//     packet primary header (6) : version 0, type 1 (TC), no sec. header, APID 0x0C0
//     user data                 : opcode (1) | arguments (big-endian)
// =============================================================================
#pragma once
#include <stdint.h>
#include <stddef.h>

namespace vgq {

enum Opcode : uint8_t {
  OP_NOOP = 0x01, OP_MODE = 0x02, OP_TXPWR = 0x03, OP_AIRRATE = 0x04, OP_FPERIOD = 0x05,
  OP_PKTRATE = 0x06, OP_PLAYBACK = 0x07, OP_SSRCLEAR = 0x08, OP_PING = 0x09, OP_SENSORS = 0x0A,
  OP_RSTCNT = 0x0B, OP_REBOOT = 0x0C, OP_MAGCC = 0x0D, OP_CMDLOSS = 0x0E, OP_FDIRMASK = 0x0F,
  OP_TXINHIBIT = 0x10, OP_EVRLEVEL = 0x11, OP_TIMECORR = 0x12,
  OP_RFSCAN = 0x13,      // u8 first_ch, u8 last_ch : ambient-noise spectrum survey
  OP_HIBERNATE = 0x14,   // u16 beacon_s : Wake-on-Radio hibernation with periodic beacon
  OP_CHANNEL = 0x15,     // u8 ch, u16 revert_s : coordinated channel change (auto-revert)
  OP_SPECCFG = 0x16,     // u8 gain 0..3, u8 int_cycles 1..255, u8 lamps 0..7 : AS7265X setup
};

enum CmdError : uint8_t {
  CE_OK = 0, CE_UNKNOWN_OPCODE = 1, CE_BAD_LENGTH = 2, CE_BAD_ARGUMENT = 3,
  CE_HARDWARE = 5, CE_BAD_MAGIC = 6,      // 4 retired (was NOT_ALLOWED_IN_MODE)
};

enum CmdStage : uint8_t { STAGE_ACCEPTED = 1, STAGE_EXECUTED = 2, STAGE_FAILED = 3 };

struct Command {
  uint8_t opcode = 0;
  uint8_t a8 = 0;      // first 8-bit argument
  uint8_t b8 = 0;      // second 8-bit argument
  uint8_t c8 = 0;      // third 8-bit argument
  uint16_t a16 = 0;    // first 16-bit argument
  uint16_t b16 = 0;    // second 16-bit argument
  uint32_t a32 = 0;    // 32-bit argument
};

// Validates the TC packet header and decodes/range-checks the arguments.
CmdError parse_tc_packet(const uint8_t* pkt, size_t len, Command& out);

}  // namespace vgq
