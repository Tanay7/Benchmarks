// CCSDS pseudo-randomizer, 255-bit sequence: h(x) = x^8 + x^7 + x^5 + x^3 + 1,
// all-ones seed, applied (XOR) to the RS codeblock starting at its first bit;
// the ASM is never randomized (CCSDS 131.0-B §10).
//
// Note: CCSDS 131.0-B-5 makes a 131071-bit sequence the primary option for new
// (high-rate) missions and keeps the 255-bit sequence for backward
// compatibility. At our <= 9.6 kbit/s rates the spectral-line issue that
// motivated the change does not arise, and the LoRa PHY whitens anyway, so we
// keep the classic sequence that ground equipment universally supports.
// First 40 bits of the sequence: 1111 1111 0100 1000 0000 1110 1100 0000 1001 1010
#pragma once
#include <stdint.h>
#include <stddef.h>

namespace ccsds {
// XORs `len` octets in place with the sequence (restarting at bit 0).
void randomize(uint8_t* data, size_t len);
// Returns the i-th octet of the 255-octet periodic sequence (for tests).
uint8_t randomizer_octet(size_t i);
}
