// Test double of Arduino_LED_Matrix for the station sketch harness (run.sh): records
// begin(), the grayscale depth and the last frame drawn (13 x 8 = 104 levels, row major).
// Found before tools/host_test/arduino_stub's inert version (include order in run.sh).
// Only the sketch's loop thread draws; the tests read it from that same thread.
#pragma once
#include "Arduino.h"
#include <string.h>

struct MatrixRec {
  int begins = 0, draws = 0;
  uint8_t bits = 0;
  uint8_t frame[104] = {};
};
inline MatrixRec g_matrix_rec;

class Arduino_LED_Matrix {
 public:
  int begin() {
    ++g_matrix_rec.begins;
    return 1;
  }
  void setGrayscaleBits(uint8_t b) { g_matrix_rec.bits = b; }
  void draw(const uint8_t* f) {
    memcpy(g_matrix_rec.frame, f, sizeof g_matrix_rec.frame);
    ++g_matrix_rec.draws;
  }
};
