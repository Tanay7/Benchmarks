#include "commands.h"
#include "tlm_packets.h"

namespace vgq {

static uint16_t be16(const uint8_t* p) { return (uint16_t)((p[0] << 8) | p[1]); }
static uint32_t be32(const uint8_t* p) {
  return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | p[3];
}

CmdError parse_tc_packet(const uint8_t* pkt, size_t len, Command& c) {
  if (len < 7) return CE_BAD_LENGTH;
  const uint16_t w0 = be16(pkt);
  const uint16_t plen = (uint16_t)(be16(pkt + 4) + 1);
  if ((w0 >> 13) != 0 || ((w0 >> 12) & 1) != 1 || (w0 & 0x7FF) != APID_TC) return CE_BAD_ARGUMENT;
  if ((size_t)plen + 6 > len) return CE_BAD_LENGTH;
  const uint8_t* u = pkt + 6;
  const size_t n = plen;            // user data length (no secondary header)
  c = Command();
  c.opcode = u[0];
  const uint8_t* a = u + 1;
  const size_t na = n - 1;

  switch (c.opcode) {
    case OP_NOOP: case OP_SSRCLEAR: case OP_RSTCNT: case OP_TIMECORR:
      return na == 0 ? CE_OK : CE_BAD_LENGTH;
    case OP_MODE:
      if (na != 1) return CE_BAD_LENGTH;
      c.a8 = a[0];
      return (c.a8 >= MODE_SAFE && c.a8 <= MODE_TEST) ? CE_OK : CE_BAD_ARGUMENT;
    case OP_TXPWR:
      if (na != 1) return CE_BAD_LENGTH;
      c.a8 = a[0];
      return c.a8 <= 3 ? CE_OK : CE_BAD_ARGUMENT;
    case OP_AIRRATE:
      if (na != 3) return CE_BAD_LENGTH;
      c.a8 = a[0]; c.a16 = be16(a + 1);
      return (c.a8 <= 7 && c.a16 >= 60 && c.a16 <= 3600) ? CE_OK : CE_BAD_ARGUMENT;
    case OP_FPERIOD:
      if (na != 2) return CE_BAD_LENGTH;
      c.a16 = be16(a);
      return (c.a16 >= 500 && c.a16 <= 60000) ? CE_OK : CE_BAD_ARGUMENT;
    case OP_PKTRATE:
      if (na != 4) return CE_BAD_LENGTH;
      c.a16 = be16(a); c.b16 = be16(a + 2);
      return (c.a16 >= APID_HK && c.a16 <= APID_ENV && c.b16 <= 3600) ? CE_OK : CE_BAD_ARGUMENT;
    case OP_PLAYBACK:
      if (na != 1) return CE_BAD_LENGTH;
      c.a8 = a[0];
      return c.a8 <= 1 ? CE_OK : CE_BAD_ARGUMENT;
    case OP_PING:
      if (na != 4) return CE_BAD_LENGTH;
      c.a32 = be32(a);
      return CE_OK;
    case OP_SENSORS: case OP_FDIRMASK:
      if (na != 2) return CE_BAD_LENGTH;
      c.a16 = be16(a);
      return CE_OK;
    case OP_REBOOT:
      if (na != 2) return CE_BAD_LENGTH;
      c.a16 = be16(a);
      return c.a16 == 0xB007 ? CE_OK : CE_BAD_MAGIC;
    case OP_MAGCC:
      if (na != 2) return CE_BAD_LENGTH;
      c.a16 = be16(a);
      return (c.a16 >= 50 && c.a16 <= 400) ? CE_OK : CE_BAD_ARGUMENT;
    case OP_CMDLOSS:
      if (na != 4) return CE_BAD_LENGTH;
      c.a32 = be32(a);
      return c.a32 <= 604800UL ? CE_OK : CE_BAD_ARGUMENT;
    case OP_TXINHIBIT:
      if (na != 2) return CE_BAD_LENGTH;
      c.a16 = be16(a);
      return (c.a16 >= 10 && c.a16 <= 3600) ? CE_OK : CE_BAD_ARGUMENT;
    case OP_EVRLEVEL:
      if (na != 1) return CE_BAD_LENGTH;
      c.a8 = a[0];
      return c.a8 <= 4 ? CE_OK : CE_BAD_ARGUMENT;
    default:
      return CE_UNKNOWN_OPCODE;
  }
}

}  // namespace vgq
