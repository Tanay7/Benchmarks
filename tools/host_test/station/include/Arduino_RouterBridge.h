// Test double of Arduino_RouterBridge for the station sketch harness (run.sh): records
// every notification and every provided method name. Found before
// tools/host_test/arduino_stub/Arduino_RouterBridge.h (include order in run.sh).
#pragma once
#include "Arduino.h"
#include <zephyr/kernel.h>
#include <zephyr/sys/atomic.h>
#include <mutex>
#include <string>
#include <vector>

struct Note {
  std::string method, text;
  int crc;                     // the rf_rx CRC flag, -1 for a one-argument notification
};

struct BridgeRec {
  std::mutex m;                // notify() is called from the loop and from test threads
  std::vector<Note> notes;
  std::vector<std::string> provided;
  bool begin() { return true; }
  template <class F> bool provide(const char* name, F) { provided.push_back(name); return true; }
  template <class F> bool provide_safe(const char* name, F) { provided.push_back(name); return true; }
  void notify(const char* method, const String& s) {
    std::lock_guard<std::mutex> lock(m);
    notes.push_back({method, s.c_str(), -1});
  }
  template <class A, class B, class C, class D>
  void notify(const char* method, const String& s, A, B, C, D crc) {
    std::lock_guard<std::mutex> lock(m);
    notes.push_back({method, s.c_str(), static_cast<int>(crc)});
  }
};
extern BridgeRec Bridge;
