// =============================================================================
//  DSS-Q1 Station I/O + Radio Control Unit - VENTUNO Q MCU (STM32H5F5)
//
//  Split of work on the VENTUNO Q:
//    Linux CPU (Dragonwing QCS8275) : the whole ground data system and its web
//                                     dashboard (python -m dssq.gds) - the ONLY display
//    MCU (this sketch)              : real-time station I/O, linked to Linux by the
//                                     Arduino Router Bridge (MessagePack-RPC)
//
//  Functions
//   * CardKB #1 (Wire, header SDA/SCL) : operator command entry. Every keystroke is
//     echoed to the GDS, which shows the line being typed on the web dashboard
//     (COMMANDING tab); Enter sends it; an empty Enter CONFIRMs a hazardous command.
//   * CardKB #2 (Wire1, auto-detected) : optional 2nd-operator AUTHORISE key ('Y');
//     without it the second operator authorises from the web dashboard
//   * AOS / ALARM / UPLINK LEDs and a buzzer (alarm escalation, LOS). If the GDS
//     falls silent for 5 s, AOS goes dark and ALARM flashes fast ("GDS link down").
//   * Radio Control Unit: drives the ground E22-400TBH-02 M0/M1 pins and reads AUX
//     so the GDS can switch the station radio NORMAL / WOR / CONFIG / SLEEP
//
//  Bridge services
//   provide_safe  "fp_line"  (String)  S|aos|alarm|uplink   Q|<confirm id or -1>
//   provide_safe  "rcu_mode" (int)     set E22 mode 0..3, returns AUX (1 = ready)
//   notify        "fp_in"    (String)  K|<typed text>  CMD|text  CONFIRM|id  AUTH|id
//                                      H|fw|rcu|kb1|kb2 (hello, every 5 s)
//  Python side: ground/dssq/display/frontpanel.py
//
//  CardKB key codes vary between firmware revisions: type KEYTEST + Enter to have
//  every raw key code echoed to the dashboard, and adjust the KEY_* constants.
// =============================================================================
#include <Arduino.h>
#include <Arduino_RouterBridge.h>
#include <Wire.h>

static const char* kFw = "DSSQ-IO 2.0";

// ---------------------------------------------------------------- pin map
// Radio Control Unit -> ground E22-400TBH-02 10-pin header: 10 M0 <- D2,
// 7 M1 <- D3, 4 AUX -> D4, GND (pin 8 or 9) -> GND. Remove ONLY the M0/M1 jumper
// caps; keep the RXD/TXD caps fitted (the GDS uses the board's USB port). With
// the caps removed M0/M1 rest HIGH (deep sleep) until setup() below drives both
// LOW (NORMAL). The module's own pull-ups are "very weak": optional 10 kOhm
// pull-ups to 3.3 V make that rest state robust. Never fit pull-downs.
static const int PIN_RCU_M0 = 2, PIN_RCU_M1 = 3, PIN_RCU_AUX = 4;
// Annunciators (via 330 Ohm resistors / an NPN transistor for the buzzer)
static const int PIN_LED_AOS = 5, PIN_LED_ALARM = 6, PIN_LED_UPLINK = 7, PIN_BUZZER = A0;

// ---------------------------------------------------------------- CardKB
static const uint8_t CARDKB_ADDR = 0x5F;
static const uint8_t KEY_ENTER_CR = 0x0D, KEY_ENTER_LF = 0x0A, KEY_BS = 0x08, KEY_DEL = 0x7F;
static const uint8_t KEY_ESC = 0x1B;

// ---------------------------------------------------------------- state
static char g_input[64];
static uint8_t g_input_len = 0;
static int g_pending_confirm = -1;
static bool g_keytest = false;
static bool g_aos = false, g_uplink = false;
static uint8_t g_alarm = 0, g_prev_alarm = 0;
static uint32_t g_last_rx_ms = 0, g_last_hello_ms = 0, g_buzz_until = 0;
static bool g_kb1 = false, g_kb2 = false;

static void send_line(const char* s) { Bridge.notify("fp_in", String(s)); }

static void echo_input() {                          // live typing echo for the dashboard
  char m[72];
  snprintf(m, sizeof m, "K|%s", g_input);
  send_line(m);
}

static int split(char* s, char** f, int maxf) {     // "a|b|c" -> fields, in place
  int n = 0;
  f[n++] = s;
  for (char* p = s; *p && n < maxf; ++p)
    if (*p == '|') { *p = 0; f[n++] = p + 1; }
  return n;
}

static void handle_line(char* line) {
  g_last_rx_ms = millis();
  char* f[6];
  const int n = split(line, f, 6);
  if (f[0][0] == 'S' && n >= 4) {                   // S|aos|alarm|uplink
    const bool aos = atoi(f[1]) != 0;
    g_alarm = (uint8_t)atoi(f[2]);
    g_uplink = atoi(f[3]) != 0;
    if ((g_alarm == 2 && g_prev_alarm != 2) || (g_aos && !aos)) g_buzz_until = millis() + 400;
    g_prev_alarm = g_alarm;
    g_aos = aos;
  } else if (f[0][0] == 'Q' && n >= 2) {            // Q|id : hazardous command awaiting CONFIRM
    g_pending_confirm = atoi(f[1]);
  }
}

// --- Bridge services (provide_safe: executed in the loop thread) -------------
static bool rpc_fp_line(String s) {
  char buf[64];
  strncpy(buf, s.c_str(), sizeof buf - 1);
  buf[sizeof buf - 1] = 0;
  handle_line(buf);
  return true;
}

static int rpc_rcu_mode(int m) {
  m &= 3;
  digitalWrite(PIN_RCU_M0, (m & 1) ? HIGH : LOW);
  digitalWrite(PIN_RCU_M1, (m & 2) ? HIGH : LOW);
  delay(3);                                          // E22: >= 2 ms after a mode change
  const uint32_t t0 = millis();
  while (digitalRead(PIN_RCU_AUX) == LOW && millis() - t0 < 1000) delay(1);
  return digitalRead(PIN_RCU_AUX) == HIGH ? 1 : 0;
}

// ---------------------------------------------------------------- keyboards
static int read_cardkb(TwoWire& w) {
  if (w.requestFrom(CARDKB_ADDR, (size_t)1) != 1) return -1;   // not present
  return w.available() ? w.read() : 0;
}

static void on_key1(uint8_t c) {
  if (g_keytest && c != KEY_ENTER_CR && c != KEY_ENTER_LF) {
    char m[24];
    snprintf(m, sizeof m, "K|key code 0x%02X", c);
    send_line(m);
    return;
  }
  if (c == KEY_ESC) {                                // clear the line, drop a pending confirm
    g_input_len = 0;
    g_input[0] = 0;
    g_pending_confirm = -1;
    echo_input();
    return;
  }
  if (c == KEY_ENTER_CR || c == KEY_ENTER_LF) {
    if (g_input_len == 0 && g_pending_confirm >= 0) {       // empty Enter = CONFIRM
      char m[24];
      snprintf(m, sizeof m, "CONFIRM|%d", g_pending_confirm);
      send_line(m);
      g_pending_confirm = -1;
      return;
    }
    if (!g_input_len) return;
    g_input[g_input_len] = 0;
    if (!strcmp(g_input, "KEYTEST")) {
      g_keytest = !g_keytest;
      send_line(g_keytest ? "K|key test ON (KEYTEST + Enter to leave)" : "K|key test OFF");
    } else {
      char m[72];
      snprintf(m, sizeof m, "CMD|%s", g_input);
      send_line(m);
    }
    g_input_len = 0;
    g_input[0] = 0;
    if (!g_keytest) echo_input();
    return;
  }
  if (c == KEY_BS || c == KEY_DEL) {
    if (g_input_len) g_input[--g_input_len] = 0;
    echo_input();
    return;
  }
  if (c >= 0x20 && c < 0x7F && g_input_len < sizeof g_input - 1) {
    g_input[g_input_len++] = (char)toupper(c);
    g_input[g_input_len] = 0;
    echo_input();
  }
}

static void on_key2(uint8_t c) {                    // second operator: 'Y' = authorise
  if ((c == 'y' || c == 'Y') && g_pending_confirm >= 0) {
    char m[24];
    snprintf(m, sizeof m, "AUTH|%d", g_pending_confirm);
    send_line(m);
  }
}

static void annunciators() {
  const uint32_t now = millis();
  if (now - g_last_rx_ms > 5000) {                   // GDS link down: no status from Linux
    digitalWrite(PIN_LED_AOS, LOW);
    digitalWrite(PIN_LED_ALARM, ((now / 100) & 1) ? HIGH : LOW);
    digitalWrite(PIN_LED_UPLINK, LOW);
    digitalWrite(PIN_BUZZER, LOW);
    return;
  }
  digitalWrite(PIN_LED_AOS, g_aos ? HIGH : LOW);
  digitalWrite(PIN_LED_ALARM, g_alarm == 2 ? (((now / 250) & 1) ? HIGH : LOW) : (g_alarm == 1 ? HIGH : LOW));
  digitalWrite(PIN_LED_UPLINK, g_uplink ? HIGH : LOW);
  digitalWrite(PIN_BUZZER, now < g_buzz_until ? HIGH : LOW);
}

// ---------------------------------------------------------------- setup/loop
void setup() {
  pinMode(PIN_RCU_M0, OUTPUT);
  pinMode(PIN_RCU_M1, OUTPUT);
  digitalWrite(PIN_RCU_M0, LOW);                    // station radio: NORMAL (listening)
  digitalWrite(PIN_RCU_M1, LOW);
  pinMode(PIN_RCU_AUX, INPUT_PULLUP);
  const int outs[] = {PIN_LED_AOS, PIN_LED_ALARM, PIN_LED_UPLINK, PIN_BUZZER};
  for (int p : outs) { pinMode(p, OUTPUT); digitalWrite(p, LOW); }

  Bridge.begin();                                   // waits for the arduino-router service
  Bridge.provide_safe("fp_line", rpc_fp_line);
  Bridge.provide_safe("rcu_mode", rpc_rcu_mode);
  Wire.begin();
  Wire.setClock(100000);
  Wire1.begin();
  Wire1.setClock(100000);
}

void loop() {
  static uint32_t last_kb = 0;
  const uint32_t now = millis();
  if (now - last_kb >= 20) {                        // 50 Hz keyboard scan
    last_kb = now;
    const int k1 = read_cardkb(Wire);
    g_kb1 = k1 >= 0;
    if (k1 > 0) on_key1((uint8_t)k1);
    const int k2 = read_cardkb(Wire1);
    g_kb2 = k2 >= 0;
    if (k2 > 0) on_key2((uint8_t)k2);
  }
  if (now - g_last_hello_ms >= 5000) {
    g_last_hello_ms = now;
    char h[48];
    snprintf(h, sizeof h, "H|%s|1|%d|%d", kFw, g_kb1, g_kb2);
    send_line(h);
  }
  annunciators();
}
