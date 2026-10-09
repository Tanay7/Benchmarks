// =============================================================================
//  DSS-Q1 Station Front Panel + Radio Control Unit — VENTUNO Q MCU (STM32H5)
//
//  Split of work on the VENTUNO Q:
//    Linux CPU (Dragonwing IQ-8275) : the whole ground data system (python -m dssq.gds)
//    MCU (this sketch)              : real-time station I/O, linked to Linux by the
//                                     Arduino Router Bridge (MessagePack-RPC)
//
//  Functions
//   * 2.42" SSD1309 128x64 OLED (hardware SPI): 10 telemetry pages pushed by the
//     GDS, a spectrum page, a local COMMAND CONSOLE page, "GDS LINK DOWN" watchdog
//   * CardKB #1 (Wire, header SDA/SCL) : operator command entry, paging, CONFIRM
//   * CardKB #2 (Wire1, auto-detected) : optional 2nd-operator AUTHORISE key ('Y');
//     without it the second operator authorises from the web dashboard
//   * AOS / ALARM / UPLINK LEDs and a buzzer (alarm escalation, LOS)
//   * Radio Control Unit: drives the ground E22-400TBH-02 M0/M1 pins and reads
//     AUX so the GDS can switch the station radio NORMAL / WOR / CONFIG / SLEEP
//
//  Bridge services
//   provide_safe  "fp_line"  (String)  display/console line from the GDS
//   provide_safe  "rcu_mode" (int)     set E22 mode 0..3, returns AUX (1 = ready)
//   notify        "fp_in"    (String)  CMD|text, CONFIRM|id, AUTH|id, H|hello
//  Line formats: ground/dssq/display/frontpanel.py
//
//  CardKB key codes vary between firmware revisions: type KEYTEST + Enter on the
//  console to see raw codes and adjust the KEY_* constants if needed.
// =============================================================================
#include <Arduino.h>
#include <Arduino_RouterBridge.h>
#include <Wire.h>
#include <U8g2lib.h>

static const char* kFw = "DSSQ-FP 1.1";

// ---------------------------------------------------------------- pin map
// OLED SPI: SCK = D13, MOSI = D11 (hardware SPI), CS = D10, DC = D9, RES = D8
static const int PIN_OLED_CS = 10, PIN_OLED_DC = 9, PIN_OLED_RES = 8;
U8G2_SSD1309_128X64_NONAME2_F_4W_HW_SPI oled(U8G2_R0, PIN_OLED_CS, PIN_OLED_DC, PIN_OLED_RES);
// Radio Control Unit -> ground E22 test-board header. Remove the M0/M1 jumper
// caps and fit 10 kOhm pull-DOWNs on M0/M1, so the station radio stays in NORMAL
// (listening) mode whenever this MCU is not driving the pins.
static const int PIN_RCU_M0 = 2, PIN_RCU_M1 = 3, PIN_RCU_AUX = 4;
// Annunciators (via 330 Ohm resistors / an NPN transistor for the buzzer)
static const int PIN_LED_AOS = 5, PIN_LED_ALARM = 6, PIN_LED_UPLINK = 7, PIN_BUZZER = A0;

// ---------------------------------------------------------------- CardKB
static const uint8_t CARDKB_ADDR = 0x5F;
static const uint8_t KEY_ENTER_CR = 0x0D, KEY_ENTER_LF = 0x0A, KEY_BS = 0x08, KEY_DEL = 0x7F;
static const uint8_t KEY_ESC = 0x1B, KEY_TAB = 0x09;
static const uint8_t KEY_LEFT = 0xB4, KEY_UP = 0xB5, KEY_DOWN = 0xB6, KEY_RIGHT = 0xB7;

// ---------------------------------------------------------------- state
static const int kPages = 10;          // telemetry pages from the GDS
static const int kPageSpectrum = 10;
static const int kPageCmd = 11;
static const int kTotalPages = 12;
static char g_page[kPages][8][22];     // title + 7 lines (21 chars + NUL)
static uint8_t g_bars[18];
static char g_console[6][27];
static char g_input[64];
static uint8_t g_input_len = 0;
static int g_cur = 0;
static int g_pending_confirm = -1;
static bool g_keytest = false;
static uint8_t g_lastkey = 0;
static bool g_aos = false, g_uplink = false;
static uint8_t g_alarm = 0, g_prev_alarm = 0;
static uint32_t g_last_rx_ms = 0, g_last_hello_ms = 0, g_buzz_until = 0;
static bool g_kb1 = false, g_kb2 = false;

static void send_line(const char* s) { Bridge.notify("fp_in", String(s)); }

static void console_push(const char* s) {
  for (int i = 0; i < 5; ++i) memcpy(g_console[i], g_console[i + 1], sizeof g_console[0]);
  strncpy(g_console[5], s, sizeof g_console[0] - 1);
  g_console[5][sizeof g_console[0] - 1] = 0;
}

static int split(char* s, char** f, int maxf) {   // "a|b|c" -> fields, in place
  int n = 0;
  f[n++] = s;
  for (char* p = s; *p && n < maxf; ++p)
    if (*p == '|') { *p = 0; f[n++] = p + 1; }
  return n;
}

static void handle_line(char* line) {
  g_last_rx_ms = millis();
  char* f[10];
  const int n = split(line, f, 10);
  if (f[0][0] == 'P' && n >= 3) {                   // P|idx|title|l1..l7
    const int idx = atoi(f[1]);
    if (idx < 0 || idx >= kPages) return;
    for (int i = 0; i < 8; ++i) {
      strncpy(g_page[idx][i], (i + 2 < n) ? f[i + 2] : "", 21);
      g_page[idx][i][21] = 0;
    }
  } else if (f[0][0] == 'G' && n >= 2) {            // G|v0,...,v17
    char* p = f[1];
    for (int i = 0; i < 18 && p && *p; ++i) {
      g_bars[i] = (uint8_t)constrain(atoi(p), 0, 100);
      p = strchr(p, ',');
      if (p) ++p;
    }
  } else if (f[0][0] == 'S' && n >= 4) {            // S|aos|alarm|uplink
    const bool aos = atoi(f[1]) != 0;
    g_alarm = (uint8_t)atoi(f[2]);
    g_uplink = atoi(f[3]) != 0;
    if ((g_alarm == 2 && g_prev_alarm != 2) || (g_aos && !aos)) g_buzz_until = millis() + 400;
    g_prev_alarm = g_alarm;
    g_aos = aos;
  } else if (f[0][0] == 'C' && n >= 2) {            // C|text
    console_push(f[1]);
    const char* q = strstr(f[1], "CONFIRM? #");
    if (q) g_pending_confirm = atoi(q + 10);
  }
}

// --- Bridge services (provide_safe: executed in the loop thread) -------------
static bool rpc_fp_line(String s) {
  char buf[224];
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
  g_lastkey = c;
  if (g_keytest && c != KEY_ENTER_CR && c != KEY_ENTER_LF && c < 0x20) return;
  if (c == KEY_TAB || c == KEY_RIGHT || c == KEY_DOWN) { g_cur = (g_cur + 1) % kTotalPages; return; }
  if (c == KEY_LEFT || c == KEY_UP) { g_cur = (g_cur + kTotalPages - 1) % kTotalPages; return; }
  if (c == KEY_ESC) { g_input_len = 0; g_input[0] = 0; g_pending_confirm = -1; return; }
  if (c == KEY_ENTER_CR || c == KEY_ENTER_LF) {
    g_cur = kPageCmd;
    if (g_input_len == 0 && g_pending_confirm >= 0) {       // empty Enter = CONFIRM
      char m[24];
      snprintf(m, sizeof m, "CONFIRM|%d", g_pending_confirm);
      send_line(m);
      console_push("> CONFIRM sent");
      g_pending_confirm = -1;
      return;
    }
    if (!g_input_len) return;
    g_input[g_input_len] = 0;
    if (!strcmp(g_input, "KEYTEST")) {
      g_keytest = !g_keytest;
      console_push(g_keytest ? "key test ON" : "key test OFF");
    } else {
      char m[80];
      snprintf(m, sizeof m, "CMD|%s", g_input);
      send_line(m);
      char e[27];
      snprintf(e, sizeof e, "> %s", g_input);
      console_push(e);
    }
    g_input_len = 0;
    g_input[0] = 0;
    return;
  }
  if (c == KEY_BS || c == KEY_DEL) {
    if (g_input_len) g_input[--g_input_len] = 0;
    return;
  }
  if (c >= 0x20 && c < 0x7F && g_input_len < sizeof g_input - 1) {
    g_cur = kPageCmd;
    g_input[g_input_len++] = (char)toupper(c);
    g_input[g_input_len] = 0;
  }
}

static void on_key2(uint8_t c) {                    // second operator: 'Y' = authorise
  if ((c == 'y' || c == 'Y') && g_pending_confirm >= 0) {
    char m[24];
    snprintf(m, sizeof m, "AUTH|%d", g_pending_confirm);
    send_line(m);
    console_push("> 2nd operator AUTH");
  }
}

// ---------------------------------------------------------------- display
static void draw_title(const char* t, int idx) {
  oled.setDrawColor(1);
  oled.drawBox(0, 0, 128, 9);
  oled.setDrawColor(0);
  oled.setFont(u8g2_font_5x7_tf);
  oled.drawStr(1, 7, t);
  char pg[8];
  snprintf(pg, sizeof pg, "%d/%d", idx + 1, kTotalPages);
  oled.drawStr(128 - 6 * (int)strlen(pg), 7, pg);
  oled.setDrawColor(1);
}

static void render() {
  oled.clearBuffer();
  if (g_cur < kPages) {
    draw_title(g_page[g_cur][0][0] ? g_page[g_cur][0] : "waiting for GDS", g_cur);
    for (int i = 1; i < 8; ++i) oled.drawStr(0, 9 + i * 8 - 1, g_page[g_cur][i]);
  } else if (g_cur == kPageSpectrum) {
    draw_title("AS7265x SPECTRUM", g_cur);
    for (int i = 0; i < 18; ++i) {
      const int h = g_bars[i] * 44 / 100;
      oled.drawBox(2 + i * 7, 56 - h, 5, h);
    }
    oled.drawStr(0, 64, "410nm");
    oled.drawStr(98, 64, "940nm");
  } else {
    draw_title(g_keytest ? "CMD  (KEY TEST)" : "CMD CONSOLE", g_cur);
    for (int i = 0; i < 6; ++i) oled.drawStr(0, 16 + i * 7, g_console[i]);
    char in[28];
    if (g_keytest) snprintf(in, sizeof in, "key code 0x%02X", g_lastkey);
    else snprintf(in, sizeof in, ">%s_", g_input_len > 20 ? g_input + g_input_len - 20 : g_input);
    oled.drawHLine(0, 56, 128);
    oled.drawStr(0, 64, in);
    if (g_pending_confirm >= 0) oled.drawStr(98, 64, "ENT=OK");
  }
  if (millis() - g_last_rx_ms > 5000 && (millis() / 500) % 2) {   // GDS watchdog
    oled.setDrawColor(0);
    oled.drawBox(14, 24, 100, 16);
    oled.setDrawColor(1);
    oled.drawFrame(14, 24, 100, 16);
    oled.drawStr(25, 35, "GDS LINK DOWN");
  }
  oled.sendBuffer();
}

static void annunciators() {
  const uint32_t now = millis();
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
  oled.begin();
  oled.setContrast(200);
  memset(g_page, 0, sizeof g_page);
  memset(g_console, 0, sizeof g_console);
  console_push("type a command, ENTER");
  console_push("TAB / arrows: pages");
  oled.clearBuffer();
  oled.setFont(u8g2_font_5x7_tf);
  oled.drawStr(10, 34, "DSS-Q1 waiting for Linux");
  oled.sendBuffer();

  Bridge.begin();                                   // waits for the arduino-router service
  Bridge.provide_safe("fp_line", rpc_fp_line);
  Bridge.provide_safe("rcu_mode", rpc_rcu_mode);
  Wire.begin();
  Wire.setClock(100000);
  Wire1.begin();
  Wire1.setClock(100000);
}

void loop() {
  static uint32_t last_kb = 0, last_draw = 0;
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
  if (now - last_draw >= 100) { last_draw = now; render(); }
  if (now - g_last_hello_ms >= 5000) {
    g_last_hello_ms = now;
    char h[48];
    snprintf(h, sizeof h, "H|%s|1|%d|%d", kFw, g_kb1, g_kb2);
    send_line(h);
  }
  annunciators();
}
