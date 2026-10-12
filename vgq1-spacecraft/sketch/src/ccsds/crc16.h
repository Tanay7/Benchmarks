// CRC-16 used by the CCSDS Frame Error Control Field (132.0-B-3 §4.1.6,
// 232.0-B-4 §4.1.4): generator x^16 + x^12 + x^5 + 1 (0x1021), register preset
// to all ones, no reflection, no final XOR (a.k.a. CRC-16/CCITT-FALSE).
// Check value: crc16_ccitt("123456789") == 0x29B1.
#pragma once
#include <stdint.h>
#include <stddef.h>

namespace ccsds {
uint16_t crc16_ccitt(const uint8_t* data, size_t len, uint16_t crc = 0xFFFF);
}
