// =============================================================================
//  Hook between the downlink framers (TM 132.0-B-3 and USLP 732.1-B-2) and the
//  Space Data Link Security protocol (355.0-B-2). The framer reserves room for
//  the Security Header and Trailer and calls protect() once the frame's
//  primary header and plaintext data field are in place; the OCF and FECF are
//  written afterwards, outside the protected region.
//
//     | primary header (aad_len) | sec. header | data field (data_len) | sec. trailer | OCF | FECF |
//
//  Pure C++ (no Arduino), host-tested.
// =============================================================================
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace ccsds {

class FrameSecurity {
 public:
  virtual ~FrameSecurity() {}
  // Octets the framer reserves directly after the primary header (0 = none).
  virtual size_t header_len() const = 0;
  // Octets the framer reserves directly after the protected data field.
  virtual size_t trailer_len() const = 0;
  // `frame` starts with the primary header (aad_len octets, for USLP including the
  // VC frame count), followed by header_len() reserved octets, `data_len` octets
  // of plaintext and trailer_len() reserved octets. Fills the security header,
  // encrypts and/or authenticates in place and writes the trailer. Returns false
  // if the frame must not be sent (e.g. no key epoch yet).
  virtual bool protect(uint8_t* frame, size_t aad_len, size_t data_len) = 0;
};

}  // namespace ccsds
