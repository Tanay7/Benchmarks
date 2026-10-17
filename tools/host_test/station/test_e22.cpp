// Station sketch, default (E22) build, on real host threads (run.sh):
//   * CardKB key-test mode (Phase C finding C51): it can be entered and left, echoes every
//     key code, and nothing typed in it is sent as a command or a confirmation;
//   * the Bridge mailbox (C50 / decision I4): a handler called from another thread (the
//     library's bridge thread) is executed by the loop thread; a request the loop does not
//     serve in time returns the "not done" value below the GDS's 3 s timeout and is never
//     executed afterwards; a handler called on the loop thread runs directly;
//   * pin plan H (D-72);
//   * the RCU state in the hello line (D-78): result and age of the last rcu_mode() call,
//     never a constant;
//   * the LED-matrix link status (D-78) rendered from the S and M lines: every row, the
//     blink phases, unknown fields left blank, stale M data and "GDS link down".
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

// The next hello, forced now: one loop() iteration with the hello period elapsed.
static std::string hello_now() {
  g_last_hello_ms = millis() - kHelloMs;
  loop();
  std::string h;
  for (auto& x : fp_lines()) {
    if (x.rfind("H|", 0) == 0) h = x;
  }
  return h;
}

// LED matrix: columns lit in a row, and "the first n columns at level, the rest dark".
static int lit(const uint8_t* f, int row) {
  int n = 0;
  for (int c = 0; c < kMxCols; ++c) n += f[row * kMxCols + c] != 0;
  return n;
}
static bool bar(const uint8_t* f, int row, int n, uint8_t level) {
  for (int c = 0; c < kMxCols; ++c) {
    if (f[row * kMxCols + c] != (c < n ? level : 0)) return false;
  }
  return true;
}
// A time shortly after the last line from the GDS whose 1 Hz blink phase is on / off
// (and whose 4 Hz RED phase is on for `red`).
static uint32_t mx_time(bool on, bool red = false) {
  const uint32_t base = g_last_rx_ms + 1000;
  return base - base % 1000 + (on ? (red ? 700 : 600) : 100);
}
static void line(const char* s) { rpc_fp_line(String(s)); }   // on the loop thread: direct

int main() {
  setup();
  const k_tid_t main_tid = k_current_get();
  // ---- D-72 pin plan H, D-78 matrix start and RCU state before any mode change ---------
  CHECK(PIN_RCU_M0 == 2 && PIN_RCU_M1 == 3 && PIN_RCU_AUX == 7 && PIN_LED_AOS == 4 && PIN_LED_ALARM == 5 &&
            PIN_LED_UPLINK == 6 && PIN_BUZZER == A1,
        "pin plan H: RCU D2/D3/D7 (as the spacecraft's E22), lamps D4-D6, buzzer A1 (A0 free)");
  CHECK(g_matrix_rec.begins == 1 && g_matrix_rec.bits == 3, "LED matrix started in setup() with 3-bit grayscale");
  {
    uint8_t f0[kMxCols * kMxRows];
    matrix_frame(f0, 300);                               // (300 / 100) odd: the link-down flash on
    bool dark = true;
    for (int r = 0; r <= 6; ++r) dark = dark && lit(f0, r) == 0;
    CHECK(dark && bar(f0, 7, 13, kMxBright), "matrix before the first line from the GDS: only the link-down row");
  }
  clear_notes();
  CHECK(hello_now() == "H|DSSQ-IO 4.0|-1|0|-1", "hello before any rcu_mode(): RCU never used (-1), age -1");
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

  // ---- D-78: the hello reports the last rcu_mode() result and its age -----------------
  clear_notes();
  CHECK(hello_now() == "H|DSSQ-IO 4.0|1|0|0", "hello after a mode change with AUX high: OK (1), age 0 s");
  g_rcu_t_ms -= 7000;
  const std::string h7 = hello_now();
  g_rcu_t_ms -= 3000;
  CHECK(h7 == "H|DSSQ-IO 4.0|1|0|7" && hello_now() == "H|DSSQ-IO 4.0|1|0|10",
        "the RCU age grows across hellos (re-anchored, never wraps with millis())");
  g_pins.level[PIN_RCU_AUX] = LOW;
  const int busy = rpc_rcu_mode(3);
  CHECK(busy == 0 && hello_now() == "H|DSSQ-IO 4.0|0|0|0", "AUX stuck low for 1 s: FAILED (0), age restarts");
  g_pins.level[PIN_RCU_AUX] = HIGH;
  (void)rpc_rcu_mode(1);
  CHECK(rpc_rcu_mode(7) == 0 && hello_now() == "H|DSSQ-IO 4.0|0|0|0", "a mode outside 0..3 is a FAILED change");

  // ---- D-78: LED matrix ------------------------------------------------------------------
  uint8_t f[kMxCols * kMxRows];
  line("S|1|0|0|0");
  line("M|L|1|-82|12|3|1");
  matrix_frame(f, mx_time(true));
  CHECK(bar(f, 0, 13, kMxBright) && bar(f, 1, 13, kMxBright) && bar(f, 2, 13, kMxBright) && bar(f, 3, 7, kMxBar) &&
            bar(f, 4, 7, kMxBar) && bar(f, 5, 6, kMxBar) && bar(f, 6, 3, kMxDim) && bar(f, 7, 1, kMxBright),
        "matrix: AOS, LOCK, TX, RSSI -82 dBm = 7 columns, margin 12 dB = 6, 3 frames lost, GREEN = 1 column");
  line("M|?|?|?|?|?|?");
  matrix_frame(f, mx_time(true));
  bool blank = true;
  for (int r = 1; r <= 6; ++r) blank = blank && lit(f, r) == 0;
  CHECK(blank && bar(f, 0, 13, kMxBright) && bar(f, 7, 1, kMxBright),
        "matrix: unknown M fields leave rows 1-6 blank (clock unknown: last column dark)");
  line("M|S|0|-|?|0|?");
  matrix_frame(f, mx_time(true));
  CHECK(lit(f, 1) == 0 && lit(f, 2) == 0 && lit(f, 3) == 0 && lit(f, 4) == 0 && lit(f, 6) == 0,
        "matrix: SEARCH, no TX, no packet within the LOS timeout, no frame lost: rows dark");
  line("M|F|0|O|N4|20|0");
  uint8_t g[kMxCols * kMxRows];
  matrix_frame(f, mx_time(true));
  matrix_frame(g, mx_time(false));
  CHECK(bar(f, 1, 13, kMxBright) && lit(g, 1) == 0 && bar(f, 3, 13, kMxBar) && lit(g, 3) == 0 &&
            bar(f, 5, 2, kMxBar) && lit(g, 5) == 0 && bar(f, 6, 13, kMxDim) && bar(g, 6, 13, kMxDim) &&
            f[7 * kMxCols + 12] == kMxBright && g[7 * kMxCols + 12] == 0,
        "matrix blinks: FLYWHEEL, OVERLOAD, margin not measurable (N4 = 2 columns), clock unsynchronised; "
        "lost frames saturate at 13");
  line("M|L|0|-30|N0|0|1");
  matrix_frame(f, mx_time(true));
  CHECK(bar(f, 3, 13, kMxBar) && bar(f, 5, 1, kMxBar), "matrix: -30 dBm = 13 columns; N0 still blinks one column");
  line("M|L|0|-135|30|0|1");
  matrix_frame(f, mx_time(true));
  const bool low_end = lit(f, 3) == 0 && bar(f, 5, 13, kMxBar);
  line("M|L|0|-300|-5|0|1");
  matrix_frame(f, mx_time(true));
  CHECK(low_end && lit(f, 3) == 0 && lit(f, 5) == 0,
        "matrix: -135 dBm and below = 0 columns, margin >= 26 dB = 13, a negative margin = 0");
  line("M|LL|2|abc|N|99999999|5");
  matrix_frame(f, mx_time(true));
  blank = true;
  for (int r = 1; r <= 6; ++r) blank = blank && lit(f, r) == 0;
  CHECK(blank && f[7 * kMxCols + 12] == 0, "matrix: malformed M fields are unknown (blank), never guessed");
  line("S|1|1|0|0");
  matrix_frame(f, mx_time(true));
  const bool yellow = bar(f, 7, 5, kMxBright);
  line("S|1|2|0|0");
  matrix_frame(f, mx_time(true, true));
  matrix_frame(g, mx_time(false));
  CHECK(yellow && bar(f, 7, 13, kMxBright) && lit(g, 7) == 0, "matrix: YELLOW = 5 columns, RED = 13 blinking");
  line("M|L|1|-82|12|3|1");
  line("S|0|0|0|0");
  g_last_mx_ms -= kGdsSilentMs + 1000;
  matrix_frame(f, mx_time(true));
  blank = true;
  for (int r = 0; r <= 6; ++r) blank = blank && lit(f, r) == 0;
  CHECK(blank && bar(f, 7, 1, kMxBright), "matrix: LOS dark; M data older than 5 s is not shown");
  const uint32_t down = g_last_rx_ms + kGdsSilentMs + 1;
  matrix_frame(f, down - down % 200 + 300);                  // > 5 s silent, (t / 100) odd: flash on
  blank = true;
  for (int r = 0; r <= 6; ++r) blank = blank && lit(f, r) == 0;
  CHECK(blank && bar(f, 7, 13, kMxBright), "matrix: GDS link down - only row 7, flashing");
  const int draws = g_matrix_rec.draws;
  std::atomic<bool> never{false};
  run_loop(never, 600);
  CHECK(g_matrix_rec.draws >= draws + 2, "loop() redraws the matrix at 4 Hz");
  printf("%s [E22]: %d failure(s)\n", SKETCH, g_failures);
  return g_failures ? 1 : 0;
}
