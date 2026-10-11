// =============================================================================
//  VGQ-1 telecommand parser (flight half of the command dictionary, v3).
//
//  Stage 1, parse_tc_header(): the TC Space Packet primary header (133.0-B-2).
//  Stage 2, parse_user_data(): opcode + arguments, range-checked per the command
//  table of the Phase B specification (PB-2.3). Stage 2 alone also validates the
//  commands stored by SEQLOAD (validate_stored_command), so a stored command gets
//  exactly the checks a live one gets, at load time.
//
//  Every rule here is mirrored by the ground dictionary (ground/dssq/dictionary.py
//  CommandDef ranges and `check` functions); tools/host_test writes the decision for
//  every boundary value to command_table.txt and the ground cross-check compares it
//  with cmdparse. Execution-time conditions (resource busy, hardware absent, the
//  SEQLOAD timing and policy, the MAGCAL value checks) are the executive's.
//
//  Pure C++, no state, no side effects; every input octet is read through the
//  bounds established by the argument count.
// =============================================================================
#include "commands.h"
#include "autonomy.h"
#include "spectral.h"
#include "tlm_packets.h"

namespace vgq {

// The autonomy module (autonomy.h) names the four contract-24 opcodes itself so that it
// stays free of this file; both lists must agree.
static_assert(OP_SEQLOAD == kOpcodeSeqLoad && OP_SEQCTRL == kOpcodeSeqCtrl &&
              OP_MAGBIST == kOpcodeMagBist && OP_MAGCAL == kOpcodeMagCal,
              "commands.h and autonomy.h opcodes differ");
static_assert(kSeqLoadLen == 19 && kMagCalLen == 19, "SEQLOAD / MAGCAL argument blocks are 19 octets");

namespace {

uint16_t be16(const uint8_t* p) { return (uint16_t)(((uint16_t)p[0] << 8) | p[1]); }
uint32_t be32(const uint8_t* p) {
  return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | p[3];
}

// Argument octets per opcode (index = opcode, 0 unused); 0xFF = no such opcode.
const uint8_t kArgLen[kOpcodeMax + 1] = {
    0xFF,                    // 0x00 -
    0, 1, 1, 3, 2, 4, 1, 0,  // 0x01 NOOP, MODE, TXPWR, AIRRATE, FPERIOD, PKTRATE, PLAYBACK, SSRCLEAR
    4, 2, 0, 2, 2, 4, 2, 2,  // 0x09 PING, SENSORS, RSTCNT, REBOOT, MAGCC, CMDLOSS, FDIRMASK, TXINHIBIT
    1, 0, 2, 2, 3, 5, 3, 0,  // 0x11 EVRLEVEL, TIMECORR, RFSCAN, HIBERNATE, CHANNEL, SPECCFG, CFDPPUT, CFDPCANCEL
    1, 2, 2, 2,              // 0x19 RADIOOPT, SPECMODE, SPECBASE, SPECDIAG
    19, 2, 1, 19,            // 0x1D SEQLOAD, SEQCTRL, MAGBIST, MAGCAL
};

// Periodic APIDs a PKTRATE command may address (the PeriodicIdx packets).
bool pktrate_apid(uint16_t apid) {
  switch (apid) {
    case APID_HK: case APID_RF: case APID_TIMECORR: case APID_MAG: case APID_ATT:
    case APID_SPEC: case APID_ENV:
      return true;
    default:
      return false;
  }
}

// SPECMODE: n range per mode (PB-2.3 "check"; the same table as spec_config_valid and the
// ground SPECMODE_N_RANGE): SURVEY 1..60 (unused), HDR 2..4, LOCKIN 1..16, FLUOR 1..16,
// BURST 1..60.
bool specmode_n_ok(uint8_t mode, uint8_t n) {
  switch (mode) {
    case SPEC_SURVEY: return n >= 1 && n <= 60;
    case SPEC_HDR:    return n >= 2 && n <= kSpecHdrMaxFrames;
    case SPEC_LOCKIN: return n >= 1 && n <= 16;
    case SPEC_FLUOR:  return n >= 1 && n <= 16;
    case SPEC_BURST:  return n >= 1 && n <= 60;
    default:          return false;
  }
}

inline CmdError ok_if(bool cond) { return cond ? CE_OK : CE_BAD_ARGUMENT; }

}  // namespace

CmdError parse_tc_header(const uint8_t* pkt, size_t len, const uint8_t*& user, size_t& n) {
  user = nullptr;
  n = 0;
  if (pkt == nullptr || len < kTcPktHdrLen + 1) return CE_BAD_LENGTH;
  const uint16_t w0 = be16(pkt);
  const uint16_t w1 = be16(pkt + 2);
  // version 000 | type 1 (TC) | secondary-header flag 0 | APID 0x0C0
  if ((w0 >> 13) != 0 || ((w0 >> 12) & 1u) != 1u || ((w0 >> 11) & 1u) != 0u ||
      (w0 & 0x7FFu) != APID_TC)
    return CE_BAD_ARGUMENT;
  if ((w1 >> 14) != 3u) return CE_BAD_ARGUMENT;               // sequence flags 11 (unsegmented)
  // Packet Data Length = user-data octets - 1; it must describe the packet exactly.
  const size_t ud = (size_t)be16(pkt + 4) + 1u;
  if (kTcPktHdrLen + ud != len) return CE_BAD_LENGTH;
  user = pkt + kTcPktHdrLen;
  n = ud;
  return CE_OK;
}

CmdError parse_user_data(const uint8_t* u, size_t n, Command& c) {
  c = Command();
  if (u == nullptr || n == 0) return CE_BAD_LENGTH;
  c.opcode = u[0];
  if (c.opcode == 0 || c.opcode > kOpcodeMax) return CE_UNKNOWN_OPCODE;
  const size_t na = n - 1;
  if (na != kArgLen[c.opcode]) return CE_BAD_LENGTH;           // exact argument count
  const uint8_t* a = u + 1;
  c.nargs = (uint8_t)na;
  c.args = na ? a : nullptr;

  switch (c.opcode) {
    case OP_NOOP: case OP_SSRCLEAR: case OP_RSTCNT: case OP_TIMECORR: case OP_CFDPCANCEL:
      return CE_OK;
    case OP_MODE:
      c.a8 = a[0];
      return ok_if(c.a8 >= MODE_SAFE && c.a8 <= MODE_TEST);
    case OP_TXPWR:
      c.a8 = a[0];
      return ok_if(c.a8 <= 3);
    case OP_AIRRATE:
      c.a8 = a[0]; c.a16 = be16(a + 1);
      return ok_if(c.a8 <= 7 && c.a16 >= 60 && c.a16 <= 3600);
    case OP_FPERIOD:
      c.a16 = be16(a);
      return ok_if(c.a16 >= 500 && c.a16 <= 60000);
    case OP_PKTRATE:
      c.a16 = be16(a); c.b16 = be16(a + 2);
      return ok_if(pktrate_apid(c.a16) && c.b16 <= 3600);
    case OP_PLAYBACK:
      c.a8 = a[0];
      return ok_if(c.a8 <= 1);
    case OP_PING:
      c.a32 = be32(a);
      return CE_OK;
    case OP_SENSORS:
      c.a16 = be16(a);
      return ok_if((c.a16 & (uint16_t)~kSensorInstrumentMask) == 0);
    case OP_REBOOT:
      c.a16 = be16(a);
      return c.a16 == kRebootMagic ? CE_OK : CE_BAD_MAGIC;
    case OP_MAGCC:
      c.a16 = be16(a);
      return ok_if(c.a16 >= 50 && c.a16 <= 400);
    case OP_CMDLOSS:
      c.a32 = be32(a);
      return ok_if(c.a32 <= 604800UL);
    case OP_FDIRMASK:
      c.a16 = be16(a);
      return CE_OK;
    case OP_TXINHIBIT:
      c.a16 = be16(a);
      return ok_if(c.a16 >= 10 && c.a16 <= 3600);
    case OP_EVRLEVEL:
      c.a8 = a[0];
      return ok_if(c.a8 <= 4);
    case OP_RFSCAN:
      c.a8 = a[0]; c.b8 = a[1];
      return ok_if(c.a8 <= 83 && c.b8 <= 83 && c.a8 <= c.b8);
    case OP_HIBERNATE:
      c.a16 = be16(a);
      return ok_if(c.a16 >= 60 && c.a16 <= 3600);
    case OP_CHANNEL:
      c.a8 = a[0]; c.a16 = be16(a + 1);
      return ok_if(c.a8 <= 83 && c.a16 >= 60 && c.a16 <= 3600);
    case OP_SPECCFG:
      // gain 0..3 or AGC; cycles 1..255; lamps b0..b2; led_cur only codes the lamps are rated
      // for (contract 26: the set is exactly 0..11, i.e. spec_led_cur_applied(v) == v);
      // indicator 0..4.
      c.a8 = a[0]; c.b8 = a[1]; c.c8 = a[2]; c.d8 = a[3]; c.e8 = a[4];
      return ok_if((c.a8 <= 3 || c.a8 == kSpecGainAuto) && c.b8 >= 1 && c.c8 <= 7 &&
                   c.d8 <= 11 && spec_led_cur_applied(c.d8) == c.d8 && c.e8 <= 4);
    case OP_CFDPPUT:
      c.a8 = a[0]; c.a16 = be16(a + 1);
      return ok_if(c.a8 <= 2);
    case OP_RADIOOPT:
      c.a8 = a[0];
      return ok_if(c.a8 <= 3);
    case OP_SPECMODE:
      c.a8 = a[0]; c.b8 = a[1];
      return ok_if(c.a8 <= SPEC_BURST && specmode_n_ok(c.a8, c.b8));
    case OP_SPECBASE:
      c.a16 = be16(a);
      return ok_if(c.a16 <= 9000);
    case OP_SPECDIAG:
      c.a8 = a[0]; c.b8 = a[1];
      return ok_if(c.a8 <= 2 && c.b8 <= 32);
    case OP_SEQLOAD:
      return CE_OK;                               // 19 octets; CommandSequencer::load checks the rest
    case OP_SEQCTRL:
      c.a8 = a[0]; c.b8 = a[1];
      return ok_if(c.a8 <= SEQ_DELETE_SLOT && c.b8 < kSeqSlots &&
                   (c.a8 == SEQ_DELETE_SLOT || c.b8 == 0));
    case OP_MAGBIST:
      c.a8 = a[0];
      return ok_if(c.a8 <= 2);
    case OP_MAGCAL:
      return CE_OK;                               // 19 octets; MagCalStore::load checks the values
    default:
      return CE_UNKNOWN_OPCODE;                   // unreachable: kArgLen covers 0x01..0x20
  }
}

CmdError parse_tc_packet(const uint8_t* pkt, size_t len, Command& c) {
  const uint8_t* u = nullptr;
  size_t n = 0;
  const CmdError e = parse_tc_header(pkt, len, u, n);
  if (e != CE_OK) {
    c = Command();
    return e;
  }
  return parse_user_data(u, n, c);
}

uint8_t validate_stored_command(void* ctx, const uint8_t* cmd, uint8_t len) {
  (void)ctx;
  Command c;
  return (uint8_t)parse_user_data(cmd, len, c);
}

}  // namespace vgq
