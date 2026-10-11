// =============================================================================
//  Onboard autonomy (src/fsw/autonomy.*, contract section 24):
//    * CommandSequencer: every SEQLOAD refusal (with the REAL flight command parser
//      injected as the live-command validator), ordering and tie rule, one entry per
//      pass, late skip, START/STOP epochs, FDIR stop, SEQCTRL refusals, HK getters,
//      u32 SCLK wrap, and a randomised comparison with a 64-bit reference model
//    * beacon_level() and the BeaconMonitor latch
//    * MAGCAL parsing / validation (incl. exact positive-definiteness) and
//      B_cal = M (B - b) against hand-computed values
//    * RM3100 BIST register values, decoding and the HK mag_bist bits
//  Writes, for ground/tests/test_sequence.py:
//    <out>/seq_vectors.txt   one record per line, '#' = comment:
//      seqload <19-octet block hex> <slot> <relative 0|1> <t_s> <cmd hex>
//                            a block the flight ACCEPTED and the entry it stored
//      forbidden <opcode hex>  every opcode the sequencer's own policy refuses
//      magcal <19-octet block hex> <status> <unit> <raw x y z uT> <cal x y z uT>
// =============================================================================
#include <cmath>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "fsw/autonomy.h"
#include "fsw/commands.h"
#include "drivers/radio.h"     // radio_boot_power_code (contract 15)
#include "host_test.h"

using namespace vgq;
using host_test::hex;

namespace {

// ---- the live-command validator: the flight's own TC packet parser ---------------------
struct ValidatorCtx {
  int calls = 0;
};

uint8_t live_validator(void* ctx, const uint8_t* cmd, uint8_t len) {
  if (ctx != nullptr) ++static_cast<ValidatorCtx*>(ctx)->calls;
  uint8_t pkt[6 + kSeqCmdMax] = {0};
  const uint16_t w0 = 0x1000 | APID_TC;                 // version 0, type 1 (TC), no sec. header
  pkt[0] = (uint8_t)(w0 >> 8);
  pkt[1] = (uint8_t)w0;
  pkt[2] = 0xC0;                                        // unsegmented, sequence count 0
  pkt[4] = 0;
  pkt[5] = (uint8_t)(len - 1);                          // packet data length - 1
  std::memcpy(pkt + 6, cmd, len);
  Command c;
  return parse_tc_packet(pkt, (size_t)6 + len, c);
}

struct Block {
  uint8_t b[kSeqLoadLen];
};

// SEQLOAD argument block (24.1). `len` defaults to the command's size.
Block seqload(uint8_t slot, bool relative, uint32_t t_s, std::vector<uint8_t> cmd, int len = -1) {
  Block k;
  std::memset(k.b, 0, sizeof k.b);
  k.b[0] = slot;
  k.b[1] = relative ? kSeqFlagRelative : 0;
  k.b[2] = (uint8_t)(t_s >> 24);
  k.b[3] = (uint8_t)(t_s >> 16);
  k.b[4] = (uint8_t)(t_s >> 8);
  k.b[5] = (uint8_t)t_s;
  k.b[6] = (uint8_t)(len < 0 ? cmd.size() : (size_t)len);
  for (size_t i = 0; i < cmd.size() && i < kSeqCmdMax; ++i) k.b[7 + i] = cmd[i];
  return k;
}

const std::vector<uint8_t> kNoop = {OP_NOOP};
const std::vector<uint8_t> kMode3 = {OP_MODE, 3};
const std::vector<uint8_t> kPing = {OP_PING, 0xDE, 0xAD, 0xBE, 0xEF};
const std::vector<uint8_t> kEvrLevel2 = {OP_EVRLEVEL, 2};
const std::vector<uint8_t> kFperiod = {OP_FPERIOD, 0x03, 0xE8};

SeqStatus load(CommandSequencer& s, const Block& k, uint32_t now) {
  SeqLoadReport rep;
  return s.load(k.b, sizeof k.b, now, rep);
}

std::string msg(const char* what, long a = 0) {
  char b[160];
  std::snprintf(b, sizeof b, "%s %ld", what, a);
  return b;
}

FILE* vec_file(const std::string& out, bool& started) {
  FILE* f = std::fopen((out + "/seq_vectors.txt").c_str(), started ? "a" : "w");
  if (f != nullptr && !started) {
    std::fprintf(f, "# test_autonomy.cpp: flight-accepted SEQLOAD blocks, sequencer policy,"
                    " MAGCAL application\n");
  }
  started = true;
  return f;
}
bool g_vec_started = false;

}  // namespace

// =============================================================================
// Command sequencer
// =============================================================================
HOST_TEST(seq_load_validation) {
  CommandSequencer s;
  s.reset();
  const uint32_t now = 1000;
  CHECK(load(s, seqload(0, true, 10, kNoop), now) == SEQ_ERR_NO_VALIDATOR,
        "SEQLOAD without a validator is refused (fail safe)");
  ValidatorCtx ctx;
  s.set_validator(&live_validator, &ctx);

  SeqLoadReport rep;
  const Block ok = seqload(3, true, 10, kNoop);
  CHECK(s.load(ok.b, 18, now, rep) == SEQ_ERR_LENGTH, "SEQLOAD 18-octet block -> LENGTH");
  uint8_t big[20] = {0};
  std::memcpy(big, ok.b, sizeof ok.b);
  CHECK(s.load(big, 20, now, rep) == SEQ_ERR_LENGTH, "SEQLOAD 20-octet block -> LENGTH");
  CHECK(s.load(nullptr, 19, now, rep) == SEQ_ERR_LENGTH, "SEQLOAD null block -> LENGTH");
  CHECK(seq_cmd_error(SEQ_ERR_LENGTH) == CE_BAD_LENGTH, "LENGTH maps to CE_BAD_LENGTH");
  CHECK(seq_cmd_error(SEQ_ERR_FORBIDDEN) == CE_BAD_ARGUMENT &&
            seq_cmd_error(SEQ_ERR_COMMAND) == CE_BAD_ARGUMENT && seq_cmd_error(SEQ_OK) == CE_OK,
        "other refusals map to CE_BAD_ARGUMENT, OK to CE_OK");

  CHECK(s.load(seqload(32, true, 10, kNoop).b, 19, now, rep) == SEQ_ERR_SLOT && rep.slot == 32,
        "slot 32 -> SLOT (report carries the slot)");
  Block f = seqload(0, true, 10, kNoop);
  f.b[1] = 0x02;
  CHECK(load(s, f, now) == SEQ_ERR_FLAGS, "reserved flag bit 1 -> FLAGS");
  f.b[1] = 0x81;
  CHECK(load(s, f, now) == SEQ_ERR_FLAGS, "reserved flag bit 7 -> FLAGS");
  CHECK(load(s, seqload(0, true, 10, kNoop, 0), now) == SEQ_ERR_CMD_LENGTH, "len 0 -> CMD_LENGTH");
  CHECK(load(s, seqload(0, true, 10, kNoop, 13), now) == SEQ_ERR_CMD_LENGTH, "len 13 -> CMD_LENGTH");
  Block pad = seqload(0, true, 10, kNoop);
  pad.b[7 + 11] = 0x01;
  CHECK(load(s, pad, now) == SEQ_ERR_PADDING, "non-zero padding after the command -> PADDING");

  // Times (absolute: 1 .. 604800 s ahead; relative: 0 .. 604800).
  CHECK(load(s, seqload(0, false, now, kNoop), now) == SEQ_ERR_TIME_PAST, "absolute t == now -> TIME_PAST");
  CHECK(load(s, seqload(0, false, now - 1, kNoop), now) == SEQ_ERR_TIME_PAST, "absolute t < now -> TIME_PAST");
  CHECK(load(s, seqload(0, false, now + kSeqHorizonS + 1, kNoop), now) == SEQ_ERR_TIME_RANGE,
        "absolute t > now + 7 d -> TIME_RANGE");
  CHECK(load(s, seqload(0, true, kSeqHorizonS + 1, kNoop), now) == SEQ_ERR_TIME_RANGE,
        "relative 604801 s -> TIME_RANGE");
  CHECK(s.count() == 0 && ctx.calls == 0, "no refused block was stored or reached the validator");

  // Policy: hazardous / REBOOT / nesting, refused BEFORE the validator is consulted.
  const std::vector<std::vector<uint8_t>> forbidden = {
      {OP_AIRRATE, 2, 0x02, 0x58}, {OP_SSRCLEAR}, {OP_REBOOT, 0xB0, 0x07},
      {OP_CMDLOSS, 0, 0, 0x0E, 0x10}, {OP_FDIRMASK, 0xFF, 0xFF}, {OP_TXINHIBIT, 0, 60},
      {OP_HIBERNATE, 0x02, 0x58}, {OP_CHANNEL, 23, 0x02, 0x58},
      {kOpcodeSeqLoad, 0, 1, 0, 0, 0, 10, 1, 1}, {kOpcodeSeqCtrl, 0, 0}};
  for (const auto& c : forbidden) {
    CHECK(s.load(seqload(1, true, 10, c).b, 19, now, rep) == SEQ_ERR_FORBIDDEN && rep.opcode == c[0],
          msg("forbidden opcode refused:", c[0]).c_str());
  }
  CHECK(ctx.calls == 0, "forbidden opcodes never reach the validator");

  // The live-command validator (the flight's real parse_tc_packet).
  CHECK(s.load(seqload(1, true, 10, {0x7F}).b, 19, now, rep) == SEQ_ERR_COMMAND &&
            rep.cmd_error == CE_UNKNOWN_OPCODE,
        "unknown opcode -> COMMAND, cmd_error UNKNOWN_OPCODE");
  CHECK(s.load(seqload(1, true, 10, {OP_MODE, 9}).b, 19, now, rep) == SEQ_ERR_COMMAND &&
            rep.cmd_error == CE_BAD_ARGUMENT,
        "MODE 9 -> COMMAND, cmd_error BAD_ARGUMENT");
  CHECK(s.load(seqload(1, true, 10, {OP_NOOP, 0}).b, 19, now, rep) == SEQ_ERR_COMMAND &&
            rep.cmd_error == CE_BAD_LENGTH,
        "NOOP with an argument -> COMMAND, cmd_error BAD_LENGTH");
  CHECK(s.load(seqload(1, true, 10, {OP_PING, 1, 2, 3}).b, 19, now, rep) == SEQ_ERR_COMMAND &&
            rep.cmd_error == CE_BAD_LENGTH,
        "PING with a 3-octet tag -> COMMAND, cmd_error BAD_LENGTH");
  CHECK(ctx.calls == 4 && s.count() == 0, "validator consulted once per candidate, nothing stored");

  // Accepted entries; boundaries.
  CHECK(load(s, seqload(0, true, 0, kNoop), now) == SEQ_OK, "relative t = 0 accepted");
  CHECK(load(s, seqload(1, true, kSeqHorizonS, kMode3), now) == SEQ_OK, "relative t = 604800 accepted");
  CHECK(load(s, seqload(2, false, now + 1, kPing), now) == SEQ_OK, "absolute t = now + 1 accepted");
  CHECK(load(s, seqload(3, false, now + kSeqHorizonS, kFperiod), now) == SEQ_OK,
        "absolute t = now + 604800 accepted");
  CHECK(s.load(seqload(31, true, 5, kEvrLevel2).b, 19, now, rep) == SEQ_OK && !rep.replaced &&
            rep.opcode == OP_EVRLEVEL && rep.relative && rep.t_s == 5,
        "slot 31 accepted, report fields");
  CHECK(s.load(seqload(31, true, 7, kNoop).b, 19, now, rep) == SEQ_OK && rep.replaced,
        "loading an occupied slot overwrites it (replaced = true)");
  SeqEntry e;
  CHECK(s.entry(2, e) && e.used && !e.relative && e.len == 5 && e.t_s == now + 1 &&
            std::memcmp(e.cmd, kPing.data(), 5) == 0 && e.cmd[5] == 0,
        "stored entry equals the block (PING tag intact, zero padding)");
  CHECK(s.entry(31, e) && e.t_s == 7 && e.cmd[0] == OP_NOOP, "slot 31 holds the replacement");
  CHECK(!s.entry(4, e) && !s.entry(32, e), "entry(): empty and out-of-range slots");
  CHECK(s.count() == 5 && s.state() == SEQ_IDLE, "five entries pending, loading does not arm");
  CHECK(s.next_dt_s(now) == kSeqDtNone, "seq_next_dt_s = 0xFFFF while not armed");

  // Exhaustive policy table: exactly the hazardous opcodes + REBOOT/SEQLOAD/SEQCTRL.
  int forbidden_n = 0;
  bool exact = true;
  for (int op = 0; op < 256; ++op) {
    const bool want = op == OP_AIRRATE || op == OP_SSRCLEAR || op == OP_REBOOT || op == OP_CMDLOSS ||
                      op == OP_FDIRMASK || op == OP_TXINHIBIT || op == OP_HIBERNATE ||
                      op == OP_CHANNEL || op == kOpcodeSeqLoad || op == kOpcodeSeqCtrl;
    exact = exact && (CommandSequencer::opcode_forbidden((uint8_t)op) == want);
    forbidden_n += want ? 1 : 0;
  }
  CHECK(exact && forbidden_n == 10, "opcode_forbidden(): exactly the 10 policy opcodes of 256");
  bool texts = true;
  for (int st = SEQ_OK; st <= SEQ_ERR_FDIR_SAFE; ++st)
    texts = texts && std::strcmp(seq_status_text((SeqStatus)st), "unknown") != 0;
  CHECK(texts, "every SeqStatus has an EVR text");

  // Cross-check vectors: blocks the flight accepted and what it stored.
  if (FILE* fv = vec_file(out, g_vec_started)) {
    std::fprintf(fv, "# seqload <block> <slot> <relative> <t_s> <cmd>\n");
    const Block vecs[] = {seqload(0, true, 0, kNoop), seqload(7, true, 3723, kMode3),
                          seqload(17, false, now + 86400, kPing),
                          seqload(31, true, kSeqHorizonS, kFperiod)};
    CommandSequencer v;
    v.set_validator(&live_validator, nullptr);
    bool all = true;
    for (const Block& k : vecs) {
      all = all && load(v, k, now) == SEQ_OK;
      SeqEntry ve;
      if (!v.entry(k.b[0], ve)) continue;
      std::fprintf(fv, "seqload %s %u %u %lu %s\n", hex(k.b, sizeof k.b).c_str(), k.b[0],
                   ve.relative ? 1u : 0u, (unsigned long)ve.t_s, hex(ve.cmd, ve.len).c_str());
    }
    for (int op = 0; op < 256; ++op)
      if (CommandSequencer::opcode_forbidden((uint8_t)op)) std::fprintf(fv, "forbidden %02x\n", op);
    std::fclose(fv);
    CHECK(all, "seq_vectors.txt: SEQLOAD vectors accepted and written");
  }
}

HOST_TEST(seq_ordering_and_dispatch) {
  CommandSequencer s;
  s.set_validator(&live_validator, nullptr);
  const uint32_t t0 = 1000;
  CHECK(load(s, seqload(5, true, 10, kMode3), t0) == SEQ_OK &&
            load(s, seqload(2, true, 10, kPing), t0) == SEQ_OK &&
            load(s, seqload(7, true, 5, kEvrLevel2), t0) == SEQ_OK &&
            load(s, seqload(1, false, t0 + 30, kNoop), t0) == SEQ_OK,
        "four entries loaded (two relative ties at +10 s, one absolute)");
  CHECK(s.control(SEQ_START, 0, t0, false) == SEQ_OK && s.state() == SEQ_ARMED &&
            s.epoch_s() == t0,
        "START arms, epoch = SCLK at START");
  SeqEvent ev;
  CHECK(s.poll(t0 + 4, false, ev) == SEQ_EV_NONE && s.next_dt_s(t0 + 4) == 1,
        "nothing due at +4 s, next in 1 s");
  CHECK(s.poll(t0 + 5, false, ev) == SEQ_EV_EXECUTE && ev.slot == 7 && ev.len == 2 &&
            ev.cmd[0] == OP_EVRLEVEL && ev.cmd[1] == 2 && ev.due_s == t0 + 5 && ev.late_s == 0 &&
            !ev.completed,
        "earliest entry (+5 s, slot 7) dispatched first, on time");
  CHECK(s.poll(t0 + 5, false, ev) == SEQ_EV_NONE && s.count() == 3, "a dispatched entry is removed");
  CHECK(s.poll(t0 + 10, false, ev) == SEQ_EV_EXECUTE && ev.slot == 2 && ev.len == 5 &&
            std::memcmp(ev.cmd, kPing.data(), 5) == 0,
        "time tie -> lower slot first (slot 2 before slot 5), command copied");
  CHECK(s.poll(t0 + 10, false, ev) == SEQ_EV_EXECUTE && ev.slot == 5,
        "at most one entry per poll: slot 5 on the next pass");
  CHECK(s.poll(t0 + 10, false, ev) == SEQ_EV_NONE && s.next_dt_s(t0 + 10) == 20,
        "absolute entry 20 s ahead");
  CHECK(s.poll(t0 + 31, false, ev) == SEQ_EV_EXECUTE && ev.slot == 1 && ev.late_s == 1 &&
            ev.completed && s.state() == SEQ_COMPLETE && s.count() == 0,
        "last entry 1 s late -> executed, sequence COMPLETE");
  CHECK(s.next_dt_s(t0 + 31) == kSeqDtNone && s.poll(t0 + 40, false, ev) == SEQ_EV_NONE,
        "COMPLETE: no next entry, poll idle");
}

HOST_TEST(seq_late_skip) {
  CommandSequencer s;
  s.set_validator(&live_validator, nullptr);
  const uint32_t t0 = 5000;
  CHECK(load(s, seqload(0, true, 0, kNoop), t0) == SEQ_OK &&
            load(s, seqload(1, true, 100, kNoop), t0) == SEQ_OK &&
            load(s, seqload(2, true, 400, kMode3), t0) == SEQ_OK &&
            s.control(SEQ_START, 0, t0, false) == SEQ_OK,
        "three relative entries armed");
  SeqEvent ev;
  CHECK(s.poll(t0 + 300, false, ev) == SEQ_EV_EXECUTE && ev.slot == 0 && ev.late_s == kSeqLateMaxS,
        "exactly 300 s late -> still executed");
  CHECK(s.poll(t0 + 401, false, ev) == SEQ_EV_SKIPPED_LATE && ev.slot == 1 && ev.late_s == 301 &&
            ev.len == 1 && !ev.completed,
        "301 s late -> SKIPPED_LATE (reported with its lateness)");
  CHECK(s.poll(t0 + 401, false, ev) == SEQ_EV_EXECUTE && ev.slot == 2 && ev.late_s == 1 &&
            ev.completed && s.state() == SEQ_COMPLETE,
        "the skip did not consume the next entry; COMPLETE after it");
}

HOST_TEST(seq_start_stop_epochs) {
  CommandSequencer s;
  s.set_validator(&live_validator, nullptr);
  CHECK(load(s, seqload(0, true, 50, kNoop), 100) == SEQ_OK &&
            load(s, seqload(1, true, 200, kMode3), 100) == SEQ_OK &&
            load(s, seqload(2, false, 400, kPing), 100) == SEQ_OK,
        "two relative + one absolute entry");
  CHECK(s.control(SEQ_START, 0, 100, false) == SEQ_OK, "START at 100");
  CHECK(s.control(SEQ_START, 0, 120, false) == SEQ_ERR_ARMED && s.epoch_s() == 100,
        "START while armed refused, epoch unchanged");
  SeqEvent ev;
  CHECK(s.poll(150, false, ev) == SEQ_EV_EXECUTE && ev.slot == 0, "relative +50 runs at 150");
  // Loading while armed: a relative entry is timed against the running epoch.
  CHECK(load(s, seqload(3, true, 50, kNoop), 160) == SEQ_ERR_TIME_PAST,
        "armed: relative entry already in the past -> TIME_PAST");
  CHECK(load(s, seqload(3, true, 60, kNoop), 160) == SEQ_ERR_TIME_PAST,
        "armed: relative entry due exactly now -> TIME_PAST");
  CHECK(load(s, seqload(3, true, 61, kNoop), 160) == SEQ_OK && s.next_dt_s(160) == 1,
        "armed: relative entry 1 s ahead accepted and timed at once");
  CHECK(s.control(SEQ_DELETE_SLOT, 3, 160, false) == SEQ_OK, "DELETE_SLOT 3");
  CHECK(s.control(SEQ_STOP, 0, 160, false) == SEQ_OK && s.state() == SEQ_IDLE && s.count() == 2,
        "STOP disarms, entries kept");
  CHECK(s.poll(500, false, ev) == SEQ_EV_NONE && s.next_dt_s(500) == kSeqDtNone,
        "stopped: nothing runs");
  CHECK(s.control(SEQ_START, 0, 1000, false) == SEQ_OK && s.epoch_s() == 1000,
        "re-START at 1000: new relative epoch");
  CHECK(s.next_dt_s(1000) == 0, "the absolute entry (400) is overdue");
  CHECK(s.poll(1000, false, ev) == SEQ_EV_SKIPPED_LATE && ev.slot == 2 && ev.late_s == 600,
        "absolute entry 600 s late -> skipped");
  CHECK(s.next_dt_s(1000) == 200, "relative +200 re-timed to the new epoch (1200)");
  CHECK(s.poll(1199, false, ev) == SEQ_EV_NONE, "not before 1200");
  CHECK(s.poll(1200, false, ev) == SEQ_EV_EXECUTE && ev.slot == 1 && ev.completed,
        "relative +200 executes at 1200, COMPLETE");
}

HOST_TEST(seq_fdir_and_control) {
  CommandSequencer s;
  ValidatorCtx ctx;
  s.set_validator(&live_validator, &ctx);
  CHECK(s.control(SEQ_START, 0, 10, false) == SEQ_ERR_EMPTY, "START with an empty table refused");
  CHECK(load(s, seqload(4, true, 100, kNoop), 10) == SEQ_OK &&
            load(s, seqload(9, true, 200, kMode3), 10) == SEQ_OK,
        "two entries");
  CHECK(s.control(SEQ_START, 0, 10, true) == SEQ_ERR_FDIR_SAFE && s.state() == SEQ_IDLE,
        "START refused while fault protection holds SAFE");
  CHECK(s.control(SEQ_START, 0, 10, false) == SEQ_OK, "START");
  SeqEvent ev;
  CHECK(s.poll(20, true, ev) == SEQ_EV_STOPPED_BY_FDIR && s.state() == SEQ_STOPPED_BY_FDIR &&
            s.count() == 2,
        "SAFE by FDIR -> STOPPED_BY_FDIR, entries kept");
  CHECK(s.poll(500, false, ev) == SEQ_EV_NONE && s.next_dt_s(500) == kSeqDtNone,
        "stopped by FDIR: nothing runs");
  CHECK(s.control(9, 0, 30, false) == SEQ_ERR_ACTION, "action 9 -> ACTION");
  CHECK(s.control(SEQ_START, 1, 30, false) == SEQ_ERR_SLOT_ARG, "START with slot 1 -> SLOT_ARG");
  CHECK(s.control(SEQ_STOP, 4, 30, false) == SEQ_ERR_SLOT_ARG, "STOP with slot 4 -> SLOT_ARG");
  CHECK(s.control(SEQ_CLEAR_ALL, 31, 30, false) == SEQ_ERR_SLOT_ARG, "CLEAR_ALL with slot 31 -> SLOT_ARG");
  CHECK(s.control(SEQ_DELETE_SLOT, 32, 30, false) == SEQ_ERR_SLOT, "DELETE slot 32 -> SLOT");
  CHECK(s.control(SEQ_DELETE_SLOT, 5, 30, false) == SEQ_ERR_SLOT_EMPTY, "DELETE empty slot -> SLOT_EMPTY");
  CHECK(s.control(SEQ_START, 0, 30, false) == SEQ_OK && s.state() == SEQ_ARMED,
        "re-START after FDIR stop");
  CHECK(s.control(SEQ_DELETE_SLOT, 4, 31, false) == SEQ_OK && s.state() == SEQ_ARMED &&
            s.count() == 1,
        "DELETE one of two while armed stays ARMED");
  CHECK(s.control(SEQ_DELETE_SLOT, 9, 31, false) == SEQ_OK && s.state() == SEQ_IDLE &&
            s.count() == 0,
        "DELETE the last pending entry disarms to IDLE (not COMPLETE)");
  CHECK(load(s, seqload(0, true, 1, kNoop), 40) == SEQ_OK &&
            s.control(SEQ_CLEAR_ALL, 0, 40, false) == SEQ_OK && s.count() == 0 &&
            s.state() == SEQ_IDLE,
        "CLEAR_ALL empties the table");
  s.reset();
  const int before = ctx.calls;
  CHECK(load(s, seqload(0, true, 1, kNoop), 50) == SEQ_OK && ctx.calls == before + 1,
        "reset() keeps the installed validator");
}

HOST_TEST(seq_wrap_and_saturation) {
  CommandSequencer s;
  s.set_validator(&live_validator, nullptr);
  const uint32_t t0 = 0xFFFFFF00u;
  CHECK(load(s, seqload(3, true, 0x80, kNoop), t0) == SEQ_OK &&
            load(s, seqload(1, true, 0x200, kMode3), t0) == SEQ_OK &&
            load(s, seqload(0, false, 0x00000050u, kPing), t0) == SEQ_OK,
        "entries straddling the u32 wrap loaded (absolute 0x50 is 0x150 s ahead)");
  CHECK(s.control(SEQ_START, 0, t0, false) == SEQ_OK && s.next_dt_s(t0) == 0x80, "next in 128 s");
  SeqEvent ev;
  CHECK(s.poll(0xFFFFFF80u, false, ev) == SEQ_EV_EXECUTE && ev.slot == 3, "pre-wrap entry runs first");
  CHECK(s.next_dt_s(0xFFFFFF80u) == 0xD0, "next_dt across the wrap = 208 s");
  CHECK(s.poll(0xFFFFFFFFu, false, ev) == SEQ_EV_NONE, "nothing due at the last second before wrap");
  CHECK(s.poll(0x50, false, ev) == SEQ_EV_EXECUTE && ev.slot == 0 && ev.late_s == 0,
        "absolute entry after the wrap on time");
  CHECK(s.poll(0x100, false, ev) == SEQ_EV_EXECUTE && ev.slot == 1 && ev.due_s == 0x100 && ev.completed,
        "relative entry (epoch + 0x200 mod 2^32 = 0x100) on time, COMPLETE");

  // Absolute horizon across the wrap.
  const uint32_t t1 = 0xFFFFFFF0u;
  CHECK(load(s, seqload(0, false, t1, kNoop), t1) == SEQ_ERR_TIME_PAST, "wrap: t == now refused");
  CHECK(load(s, seqload(0, false, 0, kNoop), t1) == SEQ_OK, "wrap: t = 0 is 16 s ahead");
  CHECK(load(s, seqload(1, false, t1 + kSeqHorizonS, kNoop), t1) == SEQ_OK,
        "wrap: exactly 7 days ahead accepted");
  CHECK(load(s, seqload(2, false, t1 + kSeqHorizonS + 1, kNoop), t1) == SEQ_ERR_TIME_RANGE,
        "wrap: 7 days + 1 s refused");
  CHECK(load(s, seqload(2, false, t1 + 0x7FFFFFFFu, kNoop), t1) == SEQ_ERR_TIME_RANGE,
        "wrap: 2^31 - 1 s ahead refused (horizon)");
  CHECK(load(s, seqload(2, false, t1 + 0x80000000u, kNoop), t1) == SEQ_ERR_TIME_PAST,
        "wrap: 2^31 s away counts as the past half of the circle");
  // Lateness across the wrap.
  CHECK(s.control(SEQ_CLEAR_ALL, 0, t1, false) == SEQ_OK &&
            load(s, seqload(0, true, 0, kNoop), t1) == SEQ_OK &&
            s.control(SEQ_START, 0, t1, false) == SEQ_OK,
        "entry due at 0xFFFFFFF0");
  CHECK(s.poll(0x200, false, ev) == SEQ_EV_SKIPPED_LATE && ev.late_s == 0x210,
        "late by 528 s across the wrap -> skipped");

  // HK seq_next_dt_s saturation.
  CHECK(load(s, seqload(0, true, 65534, kNoop), 0) == SEQ_OK &&
            s.control(SEQ_START, 0, 0, false) == SEQ_OK && s.next_dt_s(0) == 65534,
        "next_dt 65534 s reported exactly");
  CHECK(s.control(SEQ_STOP, 0, 0, false) == SEQ_OK &&
            load(s, seqload(0, true, 65535, kNoop), 0) == SEQ_OK &&
            s.control(SEQ_START, 0, 0, false) == SEQ_OK && s.next_dt_s(0) == kSeqDtMax,
        "next_dt 65535 s saturates at 0xFFFE");
  CHECK(s.control(SEQ_STOP, 0, 0, false) == SEQ_OK &&
            load(s, seqload(0, true, kSeqHorizonS, kNoop), 0) == SEQ_OK &&
            s.control(SEQ_START, 0, 0, false) == SEQ_OK && s.next_dt_s(0) == kSeqDtMax,
        "next_dt 7 days saturates at 0xFFFE (0xFFFF = none)");
}

// Randomised: sequencer (u32 SCLK) vs a 64-bit reference across the wrap, with irregular
// poll gaps so entries are on time, late and skipped.
HOST_TEST(seq_random_vs_reference) {
  struct Ref {
    bool used;
    uint64_t due;
  };
  int mismatches = 0, executed = 0, skipped = 0;
  for (int trial = 0; trial < 200; ++trial) {
    CommandSequencer s;
    s.set_validator(&live_validator, nullptr);
    const uint64_t now0 = 0x100000000ULL - (host_test::rnd() % 4000);  // near the wrap
    Ref ref[kSeqSlots] = {};
    const uint64_t start = now0 + host_test::rnd() % 50;
    for (uint8_t slot = 0; slot < kSeqSlots; ++slot) {
      if (host_test::rnd() % 3 == 0) continue;
      const bool rel = host_test::rnd() % 2 == 0;
      const uint32_t t = rel ? host_test::rnd() % 3000 : (uint32_t)(now0 + 1 + host_test::rnd() % 3000);
      if (load(s, seqload(slot, rel, t, kNoop), (uint32_t)now0) != SEQ_OK) {
        ++mismatches;
        continue;
      }
      ref[slot].used = true;
      ref[slot].due = rel ? start + t : now0 + 1 + (t - (uint32_t)(now0 + 1));
    }
    if (s.count() == 0) continue;
    if (s.control(SEQ_START, 0, (uint32_t)start, false) != SEQ_OK) ++mismatches;
    uint64_t now = start;
    for (int step = 0; step < 400 && s.state() == SEQ_ARMED; ++step) {
      const uint32_t gap_max = host_test::rnd() % 4 == 0 ? 400 : 20;   // some long gaps -> late
      now += 1 + host_test::rnd() % gap_max;
      // Reference: earliest due <= now, ties -> lower slot.
      int pick = -1;
      for (int i = 0; i < kSeqSlots; ++i)
        if (ref[i].used && ref[i].due <= now && (pick < 0 || ref[i].due < ref[pick].due)) pick = i;
      SeqEvent ev;
      const SeqEventKind k = s.poll((uint32_t)now, false, ev);
      if (pick < 0) {
        mismatches += k != SEQ_EV_NONE;
        continue;
      }
      const uint64_t late = now - ref[pick].due;
      const SeqEventKind want = late > kSeqLateMaxS ? SEQ_EV_SKIPPED_LATE : SEQ_EV_EXECUTE;
      if (k != want || ev.slot != pick || ev.late_s != late) ++mismatches;
      executed += want == SEQ_EV_EXECUTE;
      skipped += want == SEQ_EV_SKIPPED_LATE;
      ref[pick].used = false;
    }
  }
  CHECK(mismatches == 0, msg("200 random sequences across the u32 wrap match the 64-bit "
                             "reference; mismatches =", mismatches).c_str());
  CHECK(executed > 500 && skipped > 50, msg("random trials exercised both outcomes; executed =",
                                            executed).c_str());
}

// =============================================================================
// Beacon urgency level
// =============================================================================
HOST_TEST(beacon_level_pure) {
  BeaconInputs in = BeaconInputs();
  CHECK(beacon_level(in) == BEACON_NOMINAL, "no inputs -> NOMINAL");
  bool urgent_bits = true, quiet_bits = true;
  for (int b = 0; b < 16; ++b) {
    in = BeaconInputs();
    in.fdir_raised = (uint16_t)(1u << b);
    const uint8_t l = beacon_level(in);
    if (kBeaconUrgentFdir & (1u << b)) urgent_bits = urgent_bits && l == BEACON_URGENT;
    else quiet_bits = quiet_bits && l == BEACON_NOMINAL;
  }
  CHECK(urgent_bits, "every FDIR bit of the URGENT mask -> URGENT");
  CHECK(quiet_bits, "SENSOR_LOST/LOOP_OVERRUN/VC_CONGEST/TX_INHIBIT/RATE_REVERT/SDLS_AUTH/"
                    "HIBERNATE alone -> NOMINAL");
  CHECK(kBeaconUrgentFdir == (FDIR_RADIO_CFG | FDIR_AUX_TIMEOUT | FDIR_PA_OVERTEMP |
                              FDIR_AVI_OVERTEMP | FDIR_CMD_LOSS | FDIR_I2C_BUS | FDIR_LOW_BUS_V |
                              FDIR_FARM_LOCKOUT | FDIR_RADIO_FAULT),
        "URGENT FDIR mask = radio, thermal, command loss, I2C bus, FARM lockout");
  in = BeaconInputs(); in.safe_by_fdir = true;
  CHECK(beacon_level(in) == BEACON_URGENT, "SAFE by fault protection -> URGENT");
  in = BeaconInputs(); in.seq_stopped_by_fdir = true;
  CHECK(beacon_level(in) == BEACON_URGENT, "sequence STOPPED_BY_FDIR -> URGENT");
  in = BeaconInputs(); in.ssr_dropped = true;
  CHECK(beacon_level(in) == BEACON_URGENT, "SSR drops -> URGENT");
  in = BeaconInputs(); in.brownout = true;
  CHECK(beacon_level(in) == BEACON_URGENT, "brownout reset -> URGENT");
  in = BeaconInputs(); in.evr_max_severity = 1;
  CHECK(beacon_level(in) == BEACON_NOMINAL, "INFO EVR -> NOMINAL");
  in.evr_max_severity = 2;
  CHECK(beacon_level(in) == BEACON_IMPORTANT, "WARN EVR -> IMPORTANT");
  in.evr_max_severity = 4;
  CHECK(beacon_level(in) == BEACON_IMPORTANT, "FATAL EVR -> IMPORTANT");
  in = BeaconInputs(); in.sensor_lost = true;
  CHECK(beacon_level(in) == BEACON_IMPORTANT, "lost instrument -> IMPORTANT");
  in = BeaconInputs(); in.science_event = true;
  CHECK(beacon_level(in) == BEACON_INTERESTING, "science event -> INTERESTING");
  in = BeaconInputs(); in.seq_complete = true;
  CHECK(beacon_level(in) == BEACON_INTERESTING, "sequence complete -> INTERESTING");
  in.evr_max_severity = 3;
  CHECK(beacon_level(in) == BEACON_IMPORTANT, "highest category wins (IMPORTANT over INTERESTING)");
  in.brownout = true;
  CHECK(beacon_level(in) == BEACON_URGENT, "highest category wins (URGENT)");
}

HOST_TEST(beacon_monitor_latch) {
  const uint16_t health = SNS_NICLA_ME | SNS_ME_QUAT | SNS_ME_BSEC | SNS_AS7265X | SNS_RM3100_OB;
  BeaconSample s = BeaconSample();
  s.sensor_health = health;
  s.sensor_enable = 0xFFFF;
  BeaconMonitor m;
  m.begin(1);
  CHECK(m.level() == BEACON_NOMINAL && m.update(s) == BEACON_NOMINAL, "PIN reset, clean -> NOMINAL");
  m.note_evr(1, 0x0100);
  CHECK(m.level() == BEACON_NOMINAL, "routine INFO EVR -> still NOMINAL");
  m.note_evr(1, kEvrSpectralEvent);
  CHECK(m.level() == BEACON_INTERESTING, "EVR 0x0701 (INFO) -> INTERESTING immediately");
  s.sensor_health = (uint16_t)(health & ~SNS_ME_BSEC);
  CHECK(m.update(s) == BEACON_INTERESTING, "a sub-sensor bit (ME_BSEC) dropping is not a lost instrument");
  s.sensor_health = (uint16_t)(health & ~(SNS_ME_BSEC | SNS_AS7265X));
  CHECK(m.update(s) == BEACON_IMPORTANT, "AS7265X lost -> IMPORTANT");
  s.fdir_flags = FDIR_RADIO_FAULT;
  CHECK(m.update(s) == BEACON_URGENT, "E22 abnormal status raised -> URGENT");
  s.fdir_flags = 0;
  CHECK(m.update(s) == BEACON_URGENT, "condition cleared: level stays latched");
  m.note_evr(1, 0x0100);
  CHECK(m.level() == BEACON_URGENT, "latch never falls before contact");
  m.on_ground_contact(s);
  CHECK(m.level() == BEACON_NOMINAL, "ground contact clears the latch");
  CHECK(m.update(s) == BEACON_NOMINAL, "the instrument already reported lost is not lost again");

  // Sticky FDIR flag (the flight never clears I2C_BUS): edge, not level.
  s.fdir_flags = FDIR_I2C_BUS;
  CHECK(m.update(s) == BEACON_URGENT, "I2C bus failure -> URGENT");
  m.on_ground_contact(s);
  CHECK(m.level() == BEACON_NOMINAL && m.update(s) == BEACON_NOMINAL,
        "sticky I2C_BUS still set after contact: no re-raise");
  s.fdir_flags = (uint16_t)(FDIR_I2C_BUS | FDIR_FARM_LOCKOUT);
  CHECK(m.update(s) == BEACON_URGENT, "a new FDIR bit rising -> URGENT");
  s.fdir_flags = 0;
  m.on_ground_contact(s);

  // Level conditions persist through a contact.
  s.safe_by_fdir = true;
  CHECK(m.update(s) == BEACON_URGENT, "SAFE by FDIR -> URGENT");
  m.on_ground_contact(s);
  CHECK(m.level() == BEACON_URGENT, "contact while still SAFE by FDIR -> URGENT again at once");
  s.safe_by_fdir = false;                          // the ground's MODE command left SAFE
  m.on_ground_contact(s);
  CHECK(m.level() == BEACON_NOMINAL, "contact sample taken after MODE -> NOMINAL (no stale re-latch)");

  // Sequence events.
  s.seq_state = SEQ_ARMED;
  CHECK(m.update(s) == BEACON_NOMINAL, "sequence armed -> NOMINAL");
  s.seq_state = SEQ_COMPLETE;
  CHECK(m.update(s) == BEACON_INTERESTING, "sequence COMPLETE -> INTERESTING");
  m.on_ground_contact(s);
  CHECK(m.update(s) == BEACON_NOMINAL, "COMPLETE persisting after contact: no re-raise");
  s.seq_state = SEQ_STOPPED_BY_FDIR;
  CHECK(m.update(s) == BEACON_URGENT, "sequence STOPPED_BY_FDIR -> URGENT");
  m.on_ground_contact(s);
  CHECK(m.level() == BEACON_URGENT, "still STOPPED_BY_FDIR after contact -> URGENT");
  s.seq_state = SEQ_IDLE;
  m.on_ground_contact(s);

  // SSR drops: counter increases.
  s.ssr_dropped = 5;
  CHECK(m.update(s) == BEACON_URGENT, "SSR drop counter rose -> URGENT");
  m.on_ground_contact(s);
  CHECK(m.update(s) == BEACON_NOMINAL, "counter unchanged after contact -> NOMINAL");
  s.ssr_dropped = 6;
  CHECK(m.update(s) == BEACON_URGENT, "drops continue -> URGENT again");
  m.on_ground_contact(s);

  // Disabled instruments are not "lost".
  s.sensor_enable = (uint16_t)(0xFFFF & ~SNS_RM3100_OB);
  s.sensor_health = (uint16_t)(s.sensor_health & ~SNS_RM3100_OB);
  CHECK(m.update(s) == BEACON_NOMINAL, "RM3100 disabled by SENSORS, then absent -> not lost");

  // EVR severity.
  m.note_evr(kEvrSevWarn, 0x0602);
  CHECK(m.level() == BEACON_IMPORTANT, "WARN EVR -> IMPORTANT");

  // Boot behaviour.
  BeaconMonitor b;
  b.begin(kResetCauseBrownout);
  CHECK(b.level() == BEACON_URGENT, "brownout reset -> URGENT from boot");
  BeaconSample z = BeaconSample();
  z.sensor_enable = 0xFFFF;
  CHECK(b.update(z) == BEACON_URGENT, "brownout stays latched");
  b.on_ground_contact(z);
  CHECK(b.level() == BEACON_NOMINAL && b.update(z) == BEACON_NOMINAL,
        "brownout acknowledged by contact, not re-raised");
  BeaconMonitor c;
  c.begin(0);
  z.fdir_flags = FDIR_RADIO_CFG;
  CHECK(c.update(z) == BEACON_URGENT, "FDIR flag already set at the first sample (boot) counts");
  BeaconMonitor d;
  d.begin(0);
  z.fdir_flags = 0;
  z.sensor_health = 0;
  CHECK(d.update(z) == BEACON_NOMINAL, "instruments absent at boot are not 'lost'");
}

// =============================================================================
// Magnetometer calibration
// =============================================================================
namespace {

struct MagBlock {
  uint8_t b[kMagCalLen];
};
MagBlock magcal_block(uint8_t unit, const int16_t off[3], const int16_t m[6]) {
  MagBlock k;
  k.b[0] = unit;
  for (int i = 0; i < 3; ++i) {
    const uint16_t u = (uint16_t)off[i];
    k.b[1 + 2 * i] = (uint8_t)(u >> 8);
    k.b[2 + 2 * i] = (uint8_t)u;
  }
  for (int i = 0; i < 6; ++i) {
    const uint16_t u = (uint16_t)m[i];
    k.b[7 + 2 * i] = (uint8_t)(u >> 8);
    k.b[8 + 2 * i] = (uint8_t)u;
  }
  return k;
}

MagCalStatus try_cal(const int16_t off[3], const int16_t m[6], uint8_t unit = 0) {
  uint8_t u = 0;
  MagCalCoeffs c;
  const MagBlock k = magcal_block(unit, off, m);
  return magcal_parse(k.b, sizeof k.b, u, c);
}

}  // namespace

HOST_TEST(magcal_parse_validate) {
  const int16_t z[3] = {0, 0, 0};
  const int16_t id[6] = {16384, 16384, 16384, 0, 0, 0};
  MagCalCoeffs c;
  uint8_t unit = 0;
  const MagBlock idb = magcal_block(0, z, id);
  CHECK(magcal_parse(idb.b, sizeof idb.b, unit, c) == MAGCAL_OK && unit == 0 && magcal_is_identity(c),
        "identity + zero offsets parses as 'cleared'");
  CHECK(magcal_validate(magcal_identity()) == MAGCAL_OK, "magcal_identity() is valid");
  CHECK(magcal_parse(idb.b, 18, unit, c) == MAGCAL_ERR_LENGTH && unit == 0xFF &&
            magcal_cmd_error(MAGCAL_ERR_LENGTH) == CE_BAD_LENGTH,
        "18 octets -> LENGTH (CE_BAD_LENGTH)");
  CHECK(try_cal(z, id, 2) == MAGCAL_ERR_UNIT && magcal_cmd_error(MAGCAL_ERR_UNIT) == CE_BAD_ARGUMENT,
        "unit 2 -> UNIT (CE_BAD_ARGUMENT)");
  const int16_t o_max[3] = {30000, -30000, 0}, o_hi[3] = {0, 30001, 0}, o_lo[3] = {0, 0, -30001},
                o_min[3] = {-32768, 0, 0};
  CHECK(try_cal(o_max, id) == MAGCAL_OK, "offsets +/-30000 (300 uT) accepted");
  CHECK(try_cal(o_hi, id) == MAGCAL_ERR_OFFSET && try_cal(o_lo, id) == MAGCAL_ERR_OFFSET &&
            try_cal(o_min, id) == MAGCAL_ERR_OFFSET,
        "offsets 30001 / -30001 / -32768 -> OFFSET");
  const int16_t d_lo[6] = {8191, 16384, 16384, 0, 0, 0}, d_min[6] = {8192, 8192, 8192, 0, 0, 0},
                d_max[6] = {32767, 32767, 32767, 0, 0, 0}, d_neg[6] = {16384, -16384, 16384, 0, 0, 0};
  CHECK(try_cal(z, d_lo) == MAGCAL_ERR_DIAGONAL && try_cal(z, d_neg) == MAGCAL_ERR_DIAGONAL,
        "diagonal 8191 / negative -> DIAGONAL");
  CHECK(try_cal(z, d_min) == MAGCAL_OK && try_cal(z, d_max) == MAGCAL_OK, "diagonal 8192 and 32767 accepted");
  const int16_t od_hi[6] = {16384, 16384, 16384, 8193, 0, 0},
                od_lo[6] = {16384, 16384, 16384, 0, 0, -8193},
                od_max[6] = {16384, 16384, 16384, 8192, -8192, 0};
  CHECK(try_cal(z, od_hi) == MAGCAL_ERR_OFFDIAG && try_cal(z, od_lo) == MAGCAL_ERR_OFFDIAG,
        "off-diagonal 8193 / -8193 -> OFFDIAG");
  CHECK(try_cal(z, od_max) == MAGCAL_OK, "off-diagonal +/-8192 accepted when M is positive definite");
  // Inside the contract's ranges but not a valid soft-iron matrix.
  const int16_t rank1[6] = {8192, 8192, 8192, 8192, 8192, 8192};          // 0.5 x ones: rank 1
  const int16_t sing[6] = {16384, 16384, 16384, -8192, -8192, -8192};     // eigenvalue 0 exactly
  const int16_t near_sing[6] = {16384, 16384, 16384, -8191, -8191, -8191};
  const int16_t indef[6] = {8192, 8192, 32767, 8192, 0, 0};               // 2x2 minor = 0
  CHECK(try_cal(z, rank1) == MAGCAL_ERR_NOT_POSITIVE, "0.5 x ones (rank 1) -> NOT_POSITIVE");
  CHECK(try_cal(z, sing) == MAGCAL_ERR_NOT_POSITIVE, "det exactly 0 (exact integer test) -> NOT_POSITIVE");
  CHECK(try_cal(z, near_sing) == MAGCAL_OK, "one LSB away from singular -> accepted");
  CHECK(try_cal(z, indef) == MAGCAL_ERR_NOT_POSITIVE, "singular leading 2x2 minor -> NOT_POSITIVE");
  bool texts = true;
  for (int st = MAGCAL_OK; st <= MAGCAL_ERR_NOT_POSITIVE; ++st)
    texts = texts && std::strcmp(magcal_status_text((MagCalStatus)st), "unknown") != 0;
  CHECK(texts, "every MagCalStatus has an EVR text");
}

HOST_TEST(magcal_apply_math) {
  // b = (10, -20, 5) uT; M = [[1, 0.25, -0.125], [0.25, 1.25, 0.0625], [-0.125, 0.0625, 0.75]].
  // raw (30, -10, 45) -> d = (20, 10, 40):
  //   x = 20 + 2.5 - 5 = 17.5;  y = 5 + 12.5 + 2.5 = 20;  z = -2.5 + 0.625 + 30 = 28.125
  // (all exactly representable: the FP32 result must be exact).
  const int16_t off[3] = {1000, -2000, 500};
  const int16_t m[6] = {16384, 20480, 12288, 4096, -2048, 1024};
  const MagBlock k = magcal_block(0, off, m);
  uint8_t want_b[kMagCalLen];
  const size_t nb = host_test::unhex("00 03e8 f830 01f4 4000 5000 3000 1000 f800 0400", want_b,
                                     sizeof want_b);
  CHECK(nb == kMagCalLen && std::memcmp(k.b, want_b, kMagCalLen) == 0, "MAGCAL wire layout (24.1)");
  MagCalStore st;
  CHECK(st.state() == 0 && !st.active(0) && magcal_is_identity(st.coeffs(1)),
        "default-constructed store: both units cleared");
  uint8_t unit = 9;
  CHECK(st.load(k.b, sizeof k.b, unit) == MAGCAL_OK && unit == 0 && st.active(0) && !st.active(1) &&
            st.state() == 0x01,
        "outboard calibration stored, HK magcal_state b0");
  const float raw[3] = {30.0f, -10.0f, 45.0f};
  float cal[3];
  CHECK(st.apply(0, raw, cal) && cal[0] == 17.5f && cal[1] == 20.0f && cal[2] == 28.125f,
        "B_cal = M (B - b) equals the hand-computed (17.5, 20, 28.125) uT");
  CHECK(st.apply(1, raw, cal) && cal[0] == 30.0f && cal[1] == -10.0f && cal[2] == 45.0f,
        "uncalibrated inboard unit: field passed through");
  CHECK(!st.apply(2, raw, cal) && cal[0] == 30.0f, "unit 2: refused, field passed through");
  const float nan_in[3] = {NAN, 0.0f, 0.0f};
  CHECK(st.apply(0, nan_in, cal) && std::isnan(cal[0]) && std::isnan(cal[1]) && std::isnan(cal[2]),
        "NaN input propagates (M has no zero in column x)");
  char txt[96];
  const MagCalCoeffs c0 = st.coeffs(0);
  const size_t n = magcal_evr_text(0, c0, txt, sizeof txt);
  CHECK(std::strcmp(txt, "MAGCAL OB b=1000,-2000,500 M=16384,20480,12288,4096,-2048,1024") == 0 &&
            n == std::strlen(txt),
        "EVR echo of the accepted coefficients");
  const int16_t wo[3] = {-30000, -30000, -30000}, wm[6] = {32767, 32767, 32767, -8192, -8192, -8192};
  MagCalCoeffs worst;
  for (int i = 0; i < 3; ++i) worst.off[i] = wo[i];
  for (int i = 0; i < 6; ++i) worst.m[i] = wm[i];
  CHECK(magcal_evr_text(1, worst, txt, sizeof txt) <= 80, "worst-case EVR text fits 80 characters");
  CHECK(magcal_evr_text(0, c0, txt, 10) == 9 && std::strlen(txt) == 9, "EVR text truncates safely");

  // A refused MAGCAL leaves the stored calibration untouched.
  const int16_t bad[6] = {16384, 16384, 16384, 9000, 0, 0};
  const MagBlock kb = magcal_block(0, off, bad);
  CHECK(st.load(kb.b, sizeof kb.b, unit) == MAGCAL_ERR_OFFDIAG && st.active(0) &&
            st.coeffs(0).m[3] == 4096,
        "refused MAGCAL keeps the previous coefficients");
  // Inboard, then clear the outboard with identity.
  const MagBlock ki = magcal_block(1, off, m);
  CHECK(st.load(ki.b, sizeof ki.b, unit) == MAGCAL_OK && unit == 1 && st.state() == 0x03,
        "inboard calibration: magcal_state 0x03");
  const int16_t z[3] = {0, 0, 0};
  const int16_t id[6] = {16384, 16384, 16384, 0, 0, 0};
  const MagBlock kc = magcal_block(0, z, id);
  CHECK(st.load(kc.b, sizeof kc.b, unit) == MAGCAL_OK && st.state() == 0x02 && !st.active(0),
        "identity MAGCAL clears the outboard calibration");
  CHECK(magcal_evr_text(0, st.coeffs(0), txt, sizeof txt) > 0 &&
            std::strcmp(txt, "MAGCAL OB cleared (identity)") == 0,
        "EVR echo of a cleared calibration");
  st.reset();
  CHECK(st.state() == 0, "reset(): both cleared");

  // Vector for the ground MAGCAL encoder (dssq/magcal.py).
  if (FILE* fv = vec_file(out, g_vec_started)) {
    MagCalStore v;
    const MagCalStatus s = v.load(k.b, sizeof k.b, unit);
    float o[3];
    const bool a = v.apply(0, raw, o);
    std::fprintf(fv, "# magcal <block> <status> <unit> <raw x y z uT> <cal x y z uT>\n");
    std::fprintf(fv, "magcal %s %u %u %.9g %.9g %.9g %.9g %.9g %.9g\n", hex(k.b, sizeof k.b).c_str(),
                 (unsigned)s, (unsigned)unit, raw[0], raw[1], raw[2], o[0], o[1], o[2]);
    std::fclose(fv);
    CHECK(s == MAGCAL_OK && a, "seq_vectors.txt: MAGCAL vector written");
  }
}

// =============================================================================
// RM3100 built-in self-test
// =============================================================================
HOST_TEST(rm3100_bist_decode) {
  CHECK(kRm3100RegBist == 0x33 && kRm3100RegPoll == 0x00 && kRm3100RegStatus == 0x34 &&
            kRm3100PollXyz == 0x70 && kRm3100StatusDrdy == 0x80,
        "register map: BIST 0x33, POLL 0x00 = 0x70 (PMZ PMY PMX), STATUS 0x34 b7 DRDY");
  CHECK(kRm3100BistStart == (kRm3100BistSte | (0x3u << 2) | 0x3u) && kRm3100BistOff == 0,
        "BIST start = STE | BW 11 (120 us) | BP 11 (4 LR periods) = 0x8F");
  MagBistResult r = rm3100_bist_decode(0xFF);
  CHECK(r.status == MAG_BIST_PASS && r.axes_ok == 7 && r.reg == 0xFF, "0xFF -> PASS, X Y Z ok");
  r = rm3100_bist_decode(0x8F);
  CHECK(r.status == MAG_BIST_AXIS_FAIL && r.axes_ok == 0, "0x8F -> all three axes failed");
  r = rm3100_bist_decode(0xEF);
  CHECK(r.status == MAG_BIST_AXIS_FAIL && r.axes_ok == 0x6, "0xEF -> X failed (b4), Y Z ok");
  r = rm3100_bist_decode(0x9F);
  CHECK(r.status == MAG_BIST_AXIS_FAIL && r.axes_ok == 0x1, "0x9F -> only X ok");
  r = rm3100_bist_decode(0x7F);
  CHECK(r.status == MAG_BIST_INVALID && r.axes_ok == 0, "STE read back 0 -> INVALID (bits not valid)");

  MagBistResult ob = rm3100_bist_decode(0xFF), ib = mag_bist_not_run();
  CHECK(mag_bist_hk(ob, ib) == 0x0F, "HK: outboard PASS, inboard not fitted -> 0x0F");
  ob = rm3100_bist_decode(0xEF);
  ib = rm3100_bist_decode(0xFF);
  CHECK(mag_bist_hk(ob, ib) == 0xFE, "HK: outboard X failed, inboard PASS -> 0xFE");
  ob = mag_bist_not_run(); ob.status = MAG_BIST_TIMEOUT;
  ib = mag_bist_not_run(); ib.status = MAG_BIST_BUS_ERROR;
  CHECK(mag_bist_hk(ob, ib) == 0x88, "HK: timeout / bus error -> tested, no axis OK (0x88)");
  ob.status = MAG_BIST_BUSY;
  ib = rm3100_bist_decode(0x7F);
  CHECK(mag_bist_hk(ob, ib) == 0x80 && !mag_bist_tested(ob) && mag_bist_tested(ib),
        "HK: BUSY is not a test result; INVALID is tested with no axis OK");
  CHECK(magbist_unit_mask(0) == 1 && magbist_unit_mask(1) == 2 && magbist_unit_mask(2) == 3 &&
            magbist_unit_mask(3) == 0 && magbist_unit_mask(255) == 0,
        "MAGBIST unit argument 0/1/2 -> mask, 3+ invalid");

  MagBistResult pass = rm3100_bist_decode(0xFF);
  pass.cleanup_ok = true;
  CHECK(mag_bist_evr_severity(pass) == 1 && mag_bist_evr_severity(rm3100_bist_decode(0xEF)) == 2,
        "EVR severity: PASS INFO, axis failure WARN");
  MagBistResult dirty = pass;
  dirty.cleanup_ok = false;
  CHECK(mag_bist_evr_severity(dirty) == 2, "EVR severity: STE not cleared -> WARN");
  char t[96];
  mag_bist_evr_text(0, pass, t, sizeof t);
  CHECK(std::strcmp(t, "RM3100 OB BIST PASS X ok Y ok Z ok (reg 0xFF)") == 0, "EVR text PASS");
  mag_bist_evr_text(1, rm3100_bist_decode(0xEF), t, sizeof t);
  CHECK(std::strcmp(t, "RM3100 IB BIST FAIL X FAIL Y ok Z ok (reg 0xEF)") == 0, "EVR text axis failure");
  bool fits = true;
  for (uint8_t st = 0; st <= MAG_BIST_INVALID; ++st) {
    MagBistResult x = rm3100_bist_decode(0x8F);
    x.status = st;
    x.cleanup_ok = false;
    fits = fits && mag_bist_evr_text(1, x, t, sizeof t) <= 80;
  }
  CHECK(fits, "every BIST EVR text fits 80 characters");
}

// ---- Reset cause (C16 / C56): a cold power-up is POWER-ON, not BROWNOUT -------------------
HOST_TEST(reset_cause_classification) {
  // STM32U5 cold power-up: BORRSTF | PINRSTF (BOR = POR/PDR, no PORRSTF), no warm marker.
  CHECK(classify_reset_cause(RST_F_BOR | RST_F_PIN, false) == 0, "power-up (BOR + PIN, no marker) -> 0 POWER-ON");
  CHECK(classify_reset_cause(RST_F_BOR, false) == 0, "BOR flag alone, no marker -> 0 POWER-ON");
  CHECK(classify_reset_cause(RST_F_BOR | RST_F_PIN, true) == kResetCauseBrownout,
        "BOR with the marker of a running MCU -> 4 BROWNOUT");
  CHECK(classify_reset_cause(RST_F_PIN, true) == 1 && classify_reset_cause(RST_F_PIN, false) == 1,
        "NRST (Linux side) -> 1 PIN");
  CHECK(classify_reset_cause(RST_F_SOFT | RST_F_PIN, true) == 2, "NVIC_SystemReset -> 2 SOFTWARE");
  CHECK(classify_reset_cause(RST_F_IWDG | RST_F_PIN, true) == 3 && classify_reset_cause(RST_F_WWDG, false) == 3,
        "IWDG / WWDG -> 3 WATCHDOG");
  CHECK(classify_reset_cause(0, true) == 255, "no flag -> 255 UNKNOWN");
  BeaconMonitor b;
  b.begin(classify_reset_cause(RST_F_BOR | RST_F_PIN, false));
  CHECK(b.level() != BEACON_URGENT && radio_boot_power_code(false, 3, 2) == 3,
        "a power-up neither latches the beacon URGENT nor derates the TX power");
}
