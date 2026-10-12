// Station sketch, default (E22) build, on real host threads (run.sh):
//   * CardKB key-test mode (Phase C finding C51): it can be entered and left, echoes every
//     key code, and nothing typed in it is sent as a command or a confirmation;
//   * the Bridge mailbox (C50 / decision I4): a handler called from another thread (the
//     library's bridge thread) is executed by the loop thread; a request the loop does not
//     serve in time returns the "not done" value below the GDS's 3 s timeout and is never
//     executed afterwards; a handler called on the loop thread runs directly.
// SKETCH (a -D from run.sh) is the sketch file, compiled into this translation unit.
#include "runtime.h"
#include SKETCH
#include <atomic>
#include <string>
#include <thread>

static std::vector<std::string> fp_lines() {
  std::vector<std::string> v;
  std::lock_guard<std::mutex> lock(Bridge.m);
  for (auto& n : Bridge.notes) {
    if (n.method == "fp_in") v.push_back(n.text);
  }
  return v;
}
static bool sent(const char* s) {
  for (auto& x : fp_lines()) {
    if (x == s) return true;
  }
  return false;
}
static void type(const char* s) {
  for (; *s; ++s) on_key(static_cast<uint8_t>(*s));
}
static void clear_notes() {
  std::lock_guard<std::mutex> lock(Bridge.m);
  Bridge.notes.clear();
}

// The loop thread (main) runs loop() until done is set or ms elapse.
static void run_loop(std::atomic<bool>& done, unsigned long ms) {
  const unsigned long t = millis();
  while (!done && millis() - t < ms) loop();
}
// M0 writes made by thread `th` (other = false) or by any other thread (other = true).
static int m0_writes_by(k_tid_t th, bool other) {
  std::lock_guard<std::mutex> lock(g_pins.m);
  int n = 0;
  for (auto& w : g_pins.writes) {
    if (w.pin == PIN_RCU_M0 && ((w.thread == th) != other)) ++n;
  }
  return n;
}
static void clear_writes() {
  std::lock_guard<std::mutex> lock(g_pins.m);
  g_pins.writes.clear();
}

int main() {
  setup();
  const k_tid_t main_tid = k_current_get();
  // ---- C51: KEYTEST can be left -----------------------------------------------------
  type("KEYTEST\r");
  CHECK(g_keytest, "KEYTEST + Enter enters key-test mode");
  clear_notes();
  type("KEYTEST\r");
  CHECK(!g_keytest && sent("K|key test OFF"), "KEYTEST + Enter in key-test mode leaves it");
  CHECK(sent("K|key code 0x4B") && sent("K|key code 0x0D"), "key-test mode echoes every code, Enter's too");
  clear_notes();
  type("AB\r");
  CHECK(sent("CMD|AB"), "command entry works again after key-test mode");
  type("KEYTEST\r");
  on_key(0x1B);
  CHECK(!g_keytest, "ESC leaves key-test mode");
  type("KEYTEST\r");
  clear_notes();
  g_pending_confirm = 7;
  type("XY\r");
  on_key('\r');
  bool cmd = false, confirm = false;
  for (auto& x : fp_lines()) {
    cmd |= x.rfind("CMD|", 0) == 0;
    confirm |= x.rfind("CONFIRM|", 0) == 0;
  }
  CHECK(g_keytest && !cmd && !confirm, "nothing typed in key-test mode is sent as CMD or CONFIRM");
  on_key(0x1B);
  g_pending_confirm = -1;

  // ---- C50 / I4: handlers run in the loop thread -----------------------------------------
  g_pins.level[PIN_RCU_AUX] = HIGH;
  clear_writes();
  {
    std::atomic<bool> done{false};
    int rc = -9;
    std::thread bridge([&] { rc = rpc_rcu_mode(1); done = true; });
    run_loop(done, 3000);
    bridge.join();
    CHECK(rc == 1, "rcu_mode from the bridge thread returns the loop's result (AUX ready)");
    CHECK(m0_writes_by(main_tid, false) >= 1 && m0_writes_by(main_tid, true) == 0,
          "rcu_mode from the bridge thread is executed by the loop thread");
  }
  {
    std::atomic<bool> done{false};
    std::thread bridge([&] { rpc_fp_line(String("S|1|2|0|-1")); done = true; });
    run_loop(done, 3000);
    bridge.join();
    CHECK(g_alarm == 2 && g_aos, "fp_line from the bridge thread reaches handle_line()");
  }
  clear_writes();
  {
    // Loop busy: the request is withdrawn after the wait and never executed later.
    int rc = -9;
    const unsigned long t = millis();
    std::thread bridge([&] { rc = rpc_rcu_mode(3); });
    bridge.join();
    const unsigned long waited = millis() - t;
    std::atomic<bool> never{false};
    run_loop(never, 50);
    CHECK(rc == 0 && waited >= 2000 && waited < 3000, "an unserved request times out below the GDS's 3 s with 0");
    CHECK(m0_writes_by(main_tid, false) == 0 && m0_writes_by(main_tid, true) == 0,
          "a withdrawn request is never executed afterwards");
  }
  clear_writes();
  CHECK(rpc_rcu_mode(2) == 1 && m0_writes_by(main_tid, false) == 1,
        "a handler invoked in the loop thread (update_safe via __loopHook) executes directly");
  printf("%s [E22]: %d failure(s)\n", SKETCH, g_failures);
  return g_failures ? 1 : 0;
}
