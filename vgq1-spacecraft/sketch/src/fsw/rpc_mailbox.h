// =============================================================================
//  VGQ-1 Router Bridge RPC mailbox (PB-4.1; critical review finding C50, interface
//  decision I4).
//
//  Why: the stock Arduino_RouterBridge 0.4.3 does NOT confine provide_safe() handlers
//  to the loop thread. BridgeClass::update() runs on the library's own "bridge" thread
//  (bridge.h:204-210: UPDATE_THREAD_PRIORITY 5, UPDATE_THREAD_STACK_SIZE 500 octets,
//  polled every 1 ms) and calls server->get_rpc(req) with NO tag (bridge.h:247);
//  Arduino_RPClite 0.3.1 RPCServer::get_rpc filters on the tag only when one is given
//  (server.h:60), so that thread takes "__safe__" methods too and runs them, preempting
//  loop() (main thread priority 14, CONFIG_MAIN_THREAD_PRIORITY) at any point. Only
//  update_safe(), called from __loopHook between loop() passes, filters on "__safe__".
//  A handler that touched Flight directly would race the loop's VC queues, SDLS state
//  and radio, on a 500-octet stack.
//  The sketch now builds with the bundled patched copy (sketch/libraries/
//  Arduino_RouterBridge, tools/arduino_routerbridge-0.4.3.patch, contract 41): update()
//  leaves "__safe__" methods to update_safe() and the bridge thread has 1536 octets, so
//  every handler reaches this mailbox on the loop thread and executes directly. The
//  mailbox stays as defence in depth (a build against the stock library stays safe).
//
//  What: each RPC handler only posts its arguments into one static slot (a copy, no
//  large locals), signals `req_`, and waits on `done_` with a bounded timeout; the loop
//  calls take() at its top (and between long phases), runs the real handler on its own
//  stack and calls complete(). A handler that the library runs on the loop thread
//  itself (update_safe() in __loopHook) must execute directly (on_loop_thread()):
//  waiting there would wait for itself.
//
//  Timeouts: the wait (kRpcWaitMs) is longer than the EGSE's call timeout (5 s,
//  python/main.py CALL_TIMEOUT_S) and than the loop's longest documented pass (an E22
//  configure on a dead module, ~4 s), so a request is withdrawn only when the EGSE has
//  already given up and applies its no-answer rule (same value again / hl_cltu not
//  re-sent / same PDU again). A withdrawn request is never executed later: the caller
//  takes `req_` back itself. Once the loop has taken a request it completes it in one
//  bounded handler run, so the caller waits for that once more; if even that expires
//  the slot stays owned until complete() frees it (`free_`), and a stale `done_` token
//  is cleared before the next post.
//
//  Host builds (VGQ_HOST_BUILD) use std::mutex / std::condition_variable semaphores so
//  tools/host_test exercises the same protocol with real threads.
// =============================================================================
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#ifdef VGQ_HOST_BUILD
#include <chrono>
#include <condition_variable>
#include <mutex>
#include <thread>
#else
#include <zephyr/kernel.h>
#endif

namespace vgq {

enum RpcOp : uint8_t { RPC_NONE = 0, RPC_SET_PARTITION, RPC_SET_SDLS_SN, RPC_HL_CLTU, RPC_CFDP_PDU };

static const size_t kRpcTextMax = 220;          // hl_cltu / cfdp_pdu: <= 220 hex characters
static const uint32_t kRpcWaitMs = 6000;        // > EGSE CALL_TIMEOUT_S (5 s), > longest loop pass

struct RpcRequest {
  uint8_t op = RPC_NONE;
  int32_t arg = 0;                              // set_partition / set_sdls_sn argument
  uint16_t len = 0;                             // text octets (not terminated)
  char text[kRpcTextMax] = {0};
};

// Counting semaphore with limit 1 (Zephyr k_sem semantics: give saturates at the limit).
class RpcSem {
 public:
#ifdef VGQ_HOST_BUILD
  void init(unsigned initial) {
    std::lock_guard<std::mutex> l(m_);
    count_ = initial ? 1u : 0u;
  }
  void give() {
    {
      std::lock_guard<std::mutex> l(m_);
      count_ = 1;
    }
    cv_.notify_one();
  }
  bool take(uint32_t timeout_ms) {             // 0 = no wait
    std::unique_lock<std::mutex> l(m_);
    if (!cv_.wait_for(l, std::chrono::milliseconds(timeout_ms), [this] { return count_ != 0; })) return false;
    count_ = 0;
    return true;
  }
  void reset() {
    std::lock_guard<std::mutex> l(m_);
    count_ = 0;
  }

 private:
  std::mutex m_;
  std::condition_variable cv_;
  unsigned count_ = 0;
#else
  void init(unsigned initial) { (void)k_sem_init(&s_, initial ? 1u : 0u, 1u); }
  void give() { k_sem_give(&s_); }
  bool take(uint32_t timeout_ms) { return k_sem_take(&s_, timeout_ms ? K_MSEC(timeout_ms) : K_NO_WAIT) == 0; }
  void reset() { k_sem_reset(&s_); }

 private:
  struct k_sem s_;
#endif
};

class RpcMailbox {
 public:
  // On the loop thread, before the first provide_safe() registration (handlers can run
  // from then on). Records that thread; wait_ms is shortened only by host tests.
  void begin(uint32_t wait_ms = kRpcWaitMs) {
    wait_ms_ = wait_ms;
    req_.init(0);
    done_.init(0);
    free_.init(1);
#ifdef VGQ_HOST_BUILD
    loop_ = std::this_thread::get_id();
#else
    loop_ = k_current_get();
#endif
    started_ = true;
  }
  bool started() const { return started_; }
  bool on_loop_thread() const {
#ifdef VGQ_HOST_BUILD
    return std::this_thread::get_id() == loop_;
#else
    return k_current_get() == loop_;
#endif
  }

  // Caller side (a thread other than the loop: the stock library's bridge thread).
  // true: the loop executed the request and `result` holds its answer. false: not
  // executed - text longer than kRpcTextMax, mailbox not started, or withdrawn after
  // the wait (never executed later).
  bool call(uint8_t op, int32_t arg, const char* text, size_t len, int32_t& result) {
    if (!started_ || len > kRpcTextMax || (len && text == nullptr)) return false;
    if (!free_.take(wait_ms_)) return false;   // a timed-out request still runs in the loop
    done_.reset();                             // a token left by an abandoned request
    slot_.op = op;
    slot_.arg = arg;
    slot_.len = (uint16_t)len;
    if (len) memcpy(slot_.text, text, len);
    req_.give();
    if (!done_.take(wait_ms_)) {
      if (req_.take(0)) {                      // the loop never saw it: withdrawn
        free_.give();
        return false;
      }
      if (!done_.take(wait_ms_)) return false; // taken: complete() frees the slot later
    }
    result = result_;
    return true;
  }

  // Loop side: the pending request, or nullptr. Every non-null take() is followed by
  // exactly one complete().
  const RpcRequest* take() { return started_ && req_.take(0) ? &slot_ : nullptr; }
  void complete(int32_t result) {
    result_ = result;
    done_.give();
    free_.give();
  }

 private:
  RpcSem req_, done_, free_;
  RpcRequest slot_;
  int32_t result_ = 0;
  uint32_t wait_ms_ = kRpcWaitMs;
  bool started_ = false;
#ifdef VGQ_HOST_BUILD
  std::thread::id loop_;
#else
  k_tid_t loop_ = nullptr;
#endif
};

}  // namespace vgq
