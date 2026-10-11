#pragma once
#include "Arduino.h"
class Arduino_LED_Matrix {
 public:
  int begin() { return 1; }
  void setGrayscaleBits(uint8_t) {}
  void draw(const uint8_t*) {}
};
