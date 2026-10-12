// Host test of tools/arduino_routerbridge-0.4.3.patch hunk 2 (contract 41): does
// BridgeClass::update() (the library's "bridge" thread) execute methods registered with
// the "__safe__" tag (Bridge.provide_safe)? Compiled by run.sh against the REAL
// Arduino_RouterBridge bridge.h (the bundled patched copy, or the stock 0.4.3 tag as the
// negative control) and the real Arduino_RPClite 0.3.1 / MsgPack 0.4.2 sources the
// arduino:zephyr platform installs; only Zephyr and the serial port are stubbed (stub/).
// Exit 0 = update() never runs a __safe__ method and update_safe() runs each exactly
// once (12 checks); the stock tag fails 4 of them.
#include <cstdio>
#include <string>
#include <vector>
#include "Arduino.h"
#include "bridge.h"

// Test access to BridgeClass's private transport / server / started members without
// editing the header under test (explicit instantiation may name private members).
template <typename Tag, typename Tag::type M> struct Rob { friend typename Tag::type get(Tag) { return M; } };
struct TransportM { typedef ITransport* BridgeClass::*type; friend type get(TransportM); };
struct ServerM { typedef RPCServer* BridgeClass::*type; friend type get(ServerM); };
struct StartedM { typedef bool BridgeClass::*type; friend type get(StartedM); };
template struct Rob<TransportM, &BridgeClass::transport>;
template struct Rob<ServerM, &BridgeClass::server>;
template struct Rob<StartedM, &BridgeClass::started>;

size_t g_stack_alloc_size = 0;
static HardwareSerial g_serial;
BridgeClass Bridge(g_serial);

struct FakeTransport : ITransport {
  std::vector<uint8_t> in; size_t pos = 0; std::vector<uint8_t> out;
  size_t write(const uint8_t* d, size_t n) override { out.insert(out.end(), d, d + n); return n; }
  size_t read(uint8_t* b, size_t n) override { size_t k = 0; while (k < n && pos < in.size()) b[k++] = in[pos++]; return k; }
  size_t read_byte(uint8_t& r) override { if (pos < in.size()) { r = in[pos++]; return 1; } return 0; }
  bool available() override { return pos < in.size(); }
};

static int safe_calls = 0, safe_off_loop = 0, plain_calls = 0, failures = 0;
static bool g_on_loop = false;                    // true only inside loop_hook()
static bool fp_line(MsgPack::str_t s) {           // provide_safe
  ++safe_calls;
  if (!g_on_loop) ++safe_off_loop;                // ran on the bridge thread
  return s.size() > 0;
}
static void loop_hook() {                         // what __loopHook does on the loop thread
  g_on_loop = true;
  BridgeClassUpdater::safeUpdate(&Bridge);
  g_on_loop = false;
}
static int plain(int x) { ++plain_calls; return x + 1; }                    // provide

static void expect(bool ok, const char* what) {
  std::printf("%s %s\n", ok ? "ok  " : "FAIL", what);
  if (!ok) ++failures;
}

static void push_notify(FakeTransport& t, const char* method, const char* arg) {   // [2, m, [arg]]
  MsgPack::Packer p; MsgPack::arr_size_t outer(3), params(1);
  p.serialize(outer, 2, MsgPack::str_t(method), params, MsgPack::str_t(arg));
  t.in.insert(t.in.end(), p.data(), p.data() + p.size());
}
static void push_call(FakeTransport& t, uint32_t id, const char* method, int arg) {  // [0, id, m, [arg]]
  MsgPack::Packer p; MsgPack::arr_size_t outer(4), params(1);
  p.serialize(outer, 0, id, MsgPack::str_t(method), params, arg);
  t.in.insert(t.in.end(), p.data(), p.data() + p.size());
}
static void push_safe_call(FakeTransport& t, uint32_t id, const char* method, const char* arg) {
  MsgPack::Packer p; MsgPack::arr_size_t outer(4), params(1);
  p.serialize(outer, 0, id, MsgPack::str_t(method), params, MsgPack::str_t(arg));
  t.in.insert(t.in.end(), p.data(), p.data() + p.size());
}

int main() {
  std::printf("UPDATE_THREAD_STACK_SIZE = %d\n", (int)UPDATE_THREAD_STACK_SIZE);
  FakeTransport t;
  Bridge.*get(TransportM()) = &t;
  RPCServer server(t);                                   // outlives every use below
  Bridge.*get(ServerM()) = &server;
  Bridge.*get(StartedM()) = true;
  server.bind("fp_line", fp_line, "__safe__");           // what provide_safe() binds
  server.bind("plain", plain);                           // what provide() binds

  // 1. A __safe__ notification: the bridge thread polls update() many times.
  push_notify(t, "fp_line", "S|1|0|0|-1");
  for (int i = 0; i < 50; ++i) Bridge.update();
  expect(safe_calls == 0, "update() x50 did not execute the __safe__ notification");
  loop_hook();
  expect(safe_calls == 1, "update_safe() executed it exactly once");
  for (int i = 0; i < 5; ++i) { Bridge.update(); loop_hook(); }
  expect(safe_calls == 1, "and never again");

  // 2. A plain (provide) call is still served by update(), never by update_safe().
  t.out.clear();
  push_call(t, 7, "plain", 41);
  for (int i = 0; i < 5; ++i) loop_hook();
  expect(plain_calls == 0, "update_safe() left the untagged call alone");
  Bridge.update();
  expect(plain_calls == 1 && !t.out.empty(), "update() executed the untagged call and answered it");

  // 3. Queue [safe call, plain call]: update() must not take the safe head, and the
  //    plain call behind it is served once the loop has taken the head.
  t.out.clear();
  push_safe_call(t, 8, "fp_line", "Q|-1");
  push_call(t, 9, "plain", 1);
  for (int i = 0; i < 20; ++i) Bridge.update();
  expect(safe_calls == 1 && plain_calls == 1, "update() x20 executed neither (safe head blocks the queue)");
  loop_hook();
  expect(safe_calls == 2 && !t.out.empty(), "update_safe() executed the safe call and sent its response");
  Bridge.update();
  expect(plain_calls == 2, "then update() served the plain call");

  // 4. Queue [plain notify..., safe notify]: order preserved, safe one only on the loop.
  push_call(t, 10, "plain", 2);
  push_notify(t, "fp_line", "S|0|1|0|-1");
  for (int i = 0; i < 20; ++i) Bridge.update();
  expect(plain_calls == 3 && safe_calls == 2, "update() served the plain call, not the safe one behind it");
  loop_hook();
  expect(safe_calls == 3, "update_safe() served the safe one");
  expect(t.pos == t.in.size(), "every request consumed");
  expect(safe_off_loop == 0, "no __safe__ handler ever ran outside the loop hook");

  std::printf("%s: %d failure(s); safe handler calls %d (%d on the bridge thread), plain handler calls %d\n",
              failures ? "FAIL" : "PASS", failures, safe_calls, safe_off_loop, plain_calls);
  return failures ? 1 : 0;
}
