// =============================================================================
//  VGQ-1 — CCSDS protocol core: shared constants and helpers
// -----------------------------------------------------------------------------
//  This header is deliberately free of any Arduino dependency so that the very
//  same protocol code can be compiled on a PC (tools/host_test) and verified
//  byte-for-byte against the ground segment's independent Python decoder.
//
//  Standards implemented (see docs/10_References.md):
//    CCSDS 131.0-B   TM Synchronization and Channel Coding (ASM, RS, randomizer)
//    CCSDS 132.0-B-3 TM Space Data Link Protocol           (Transfer Frames)
//    CCSDS 133.0-B-2 Space Packet Protocol                 (Space Packets)
//    CCSDS 231.0-B-4 TC Synchronization and Channel Coding (CLTU, BCH)
//    CCSDS 232.0-B-4 TC Space Data Link Protocol           (TC frames)
//    CCSDS 232.1-B-2 Communications Operation Procedure-1  (FARM-1 / CLCW)
//    CCSDS 301.0-B-4 Time Code Formats                     (CUC time)
// =============================================================================
#pragma once
#include <stdint.h>
#include <stddef.h>

namespace ccsds {

// ---- Mission identifiers ----------------------------------------------------
// NOTE: SCID 0x0A7 is NOT registered with SANA. It is a private value for this
// terrestrial demonstration only. Change it in BOTH flight and ground configs.
static const uint16_t kSpacecraftId = 0x0A7;     // 10-bit TM/TC Spacecraft ID

// ---- Physical-layer / coding constants (CCSDS 131.0-B) -----------------------
static const uint32_t kAsm = 0x1ACFFC1DUL;       // 32-bit Attached Sync Marker
static const size_t   kAsmLen = 4;
static const size_t   kRsN = 255;                // RS(255,223), E = 16
static const size_t   kRsK = 223;
static const size_t   kRsParity = 32;            // 2E parity symbols

// ---- TM Transfer Frame geometry (fixed for the mission phase, 132.0-B-3) ----
// One CADU must fit into ONE E22 sub-packet (240 octets) so that every LoRa
// packet carries exactly one codeblock:  4 (ASM) + 200 (frame) + 32 (RS) = 236.
static const size_t kTmFrameLen   = 200;         // total Transfer Frame octets
static const size_t kTmPriHdrLen  = 6;
static const size_t kTmOcfLen     = 4;           // CLCW (COP-1) in every frame
static const size_t kTmFecfLen    = 2;           // CRC-16 Frame Error Control
static const size_t kTmDataLen    = kTmFrameLen - kTmPriHdrLen - kTmOcfLen - kTmFecfLen; // 188
static const size_t kRsVirtualFill = kRsK - kTmFrameLen;                 // 23 (shortened code)
static const size_t kCodeblockLen = kTmFrameLen + kRsParity;            // 232
static const size_t kCaduLen      = kAsmLen + kCodeblockLen;            // 236

// First Header Pointer special values (132.0-B-3 §4.1.2.7)
static const uint16_t kFhpNoPacketStart = 0x7FF;  // no packet starts in this frame
static const uint16_t kFhpOnlyIdleData  = 0x7FE;  // OID frame (idle data only)

// Virtual channel assignment (see docs/04_Space_Data_Link_ICD.md)
enum Vc : uint8_t {
  VC_RT_ENG   = 0,   // real-time engineering: HK, RF, EVR, CMD verification, time
  VC_RT_SCI   = 1,   // real-time science: MAG, ATT, SPEC, ENV
  VC_PLAYBACK = 2,   // solid-state-recorder playback
  VC_IDLE     = 7,   // Only-Idle-Data frames (keeps the link + CLCW alive)
  kNumDataVcs = 3
};

// ---- Space Packet constants (133.0-B-2) ---------------------------------------
static const size_t   kSpPriHdrLen  = 6;
static const size_t   kSpSecHdrLen  = 6;          // CUC: 4 octets coarse + 2 fine
static const uint16_t kApidIdle     = 0x7FF;      // idle packet APID (all ones)
static const uint8_t  kCucPField    = 0x2E;       // implicit P-field: CUC, agency epoch, 4+2

// ---- TC constants (231.0-B-4 / 232.0-B-4) -------------------------------------
static const uint16_t kCltuStart = 0xEB90;
static const uint8_t  kCltuTail[8] = {0xC5,0xC5,0xC5,0xC5,0xC5,0xC5,0xC5,0x79};
static const size_t   kTcPriHdrLen = 5;
static const size_t   kTcMaxFrameLen = 64;        // mission limit (fits one E22 packet)

// ---- Big-endian helpers -------------------------------------------------------
inline void put_u16(uint8_t* p, uint16_t v) { p[0] = (uint8_t)(v >> 8); p[1] = (uint8_t)v; }
inline void put_u24(uint8_t* p, uint32_t v) { p[0] = (uint8_t)(v >> 16); p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)v; }
inline void put_u32(uint8_t* p, uint32_t v) {
  p[0] = (uint8_t)(v >> 24); p[1] = (uint8_t)(v >> 16); p[2] = (uint8_t)(v >> 8); p[3] = (uint8_t)v;
}
inline uint16_t get_u16(const uint8_t* p) { return (uint16_t)((p[0] << 8) | p[1]); }
inline uint32_t get_u32(const uint8_t* p) {
  return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | p[3];
}

}  // namespace ccsds
