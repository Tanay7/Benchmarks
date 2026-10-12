// =============================================================================
//  Transmitter duty cycle reported in RF tx_duty / sc_status duty_pct
//  (fsw/datalink.h TxDutyMeter, averaged over whole transmission cycles).
//
//  The flight schedules transmissions with frame_period_ms() >= busy / 0.5
//  (kMaxTxDutyPermille): the reported duty must never read above that limit, whatever
//  the phase of the sample. The replaced 60 s window (sum of the busy time of the
//  transmissions that ENDED in the last 60 one-second slots / 60 s) is kept here as a
//  negative control: with the SX126x SF12 busy times it reads 57.7 % for a true 50 %,
//  above the ground's YELLOW limit of 55 %.
// =============================================================================
#include <cstdio>
#include <cstring>

#include "config.h"         // kMaxTxDutyPermille
#include "drivers/radio.h"
#include "fsw/datalink.h"
#include "host_test.h"

using namespace vgq;

namespace {

// The replaced flight algorithm (flight.cpp before Phase D): busy_ms_bucket_[60].
struct OldWindowDuty {
  uint16_t slot[60] = {0};
  void on_tx_end(uint32_t now, uint32_t busy) {
    uint16_t& s = slot[(now / 1000u) % 60u];
    const uint32_t v = (uint32_t)s + (busy < 60000u ? busy : 60000u);
    s = (uint16_t)(v > 0xFFFFu ? 0xFFFFu : v);
  }
  void tick_1hz(uint32_t now) { slot[((now / 1000u) + 1u) % 60u] = 0; }
  uint16_t permille() const {
    uint32_t b = 0;
    for (uint16_t v : slot) b += v;
    const uint32_t p = b / 60u;
    return (uint16_t)(p > 1000u ? 1000u : p);
  }
};

struct Run {
  uint16_t max_new = 0, min_new = 1000, max_old = 0, min_old = 1000;
};

// `cycles` transmissions of busy_ms, each launched one frame period after the previous
// launch (plus `late_ms` every third cycle: a pass that started the frame late), sampled
// every 250 ms from the third cycle on; TX_DONE is seen at the first sample after the end
// of the transmission. t0 near the millis() wrap exercises the u32 maths.
Run simulate(uint32_t busy_ms, uint8_t mode, uint32_t cycles, uint32_t late_ms, uint32_t t0) {
  TxDutyMeter m;
  OldWindowDuty old;
  Run r;
  const uint32_t period = frame_period_ms(busy_ms, mode, false, 0);
  uint32_t start = t0, next_tick = t0 + 1000u;
  for (uint32_t k = 0; k < cycles; ++k) {                       // bounded by `cycles`
    const uint32_t end = start + busy_ms;
    const uint32_t next = start + period + ((k % 3u == 2u) ? late_ms : 0u);
    bool recorded = false;
    for (uint32_t t = start; (int32_t)(t - next) < 0; t += 250u) {   // bounded: period / 250 ms
      while ((int32_t)(t - next_tick) >= 0) {
        old.tick_1hz(next_tick);
        next_tick += 1000u;
      }
      if (!recorded && (int32_t)(t - end) >= 0) {
        m.on_tx(start, busy_ms);                                // TX_DONE
        old.on_tx_end(t, busy_ms);
        recorded = true;
      }
      if (k < 2) continue;
      const uint16_t pn = m.permille(t, !recorded, start, period);
      const uint16_t po = old.permille();
      if (pn > r.max_new) r.max_new = pn;
      if (pn < r.min_new) r.min_new = pn;
      if (po > r.max_old) r.max_old = po;
      if (po < r.min_old) r.min_old = po;
    }
    start = next;
  }
  return r;
}

}  // namespace

HOST_TEST(tx_duty_never_above_the_pa_limit) {
  // Every radio model and rate code, every mode: the meter never reads above
  // kMaxTxDutyPermille (the frame period is never shorter than busy / 0.5).
  const uint8_t models[] = {VGQ_RADIO_E22_T33D, VGQ_RADIO_E22_T37S, VGQ_RADIO_SX126X};
  uint16_t worst = 0;
  char where[96] = "";
  int combos = 0;
  for (uint8_t model : models) {
    for (uint8_t code = 0; code < 8; ++code) {
      const uint32_t busy = radio_airtime_ms(model, code, ccsds::kCaduLen);
      if (busy == 0) continue;
      for (uint8_t mode = MODE_SAFE; mode <= MODE_TEST; ++mode) {
        const Run r = simulate(busy, mode, 24, 37, 0xFFFF0000u);
        ++combos;
        if (r.max_new > worst) {
          worst = r.max_new;
          std::snprintf(where, sizeof where, "model %u code %u mode %u", (unsigned)model, (unsigned)code,
                        (unsigned)mode);
        }
      }
    }
  }
  std::printf("      %d model/code/mode combinations, highest reading %u permille (%s)\n", combos,
              (unsigned)worst, where);
  CHECK(combos == 3 * 8 * 4 && worst <= kMaxTxDutyPermille,
        "TxDutyMeter: never above kMaxTxDutyPermille for any model, rate code and mode (sampled every 250 ms)");
}

HOST_TEST(tx_duty_sf12_has_no_window_artefact) {
  // SX126x profile 1 (SF12 / 62.5 kHz): 17.3 s per CADU. ENCOUNTER = 2.0 x busy: true duty 50 %.
  const uint32_t busy1 = radio_airtime_ms(VGQ_RADIO_SX126X, 1, ccsds::kCaduLen);
  const uint32_t busy0 = radio_airtime_ms(VGQ_RADIO_SX126X, 0, ccsds::kCaduLen);
  std::printf("      SX126x busy per CADU: profile 0 %lu ms, profile 1 %lu ms\n", (unsigned long)busy0,
              (unsigned long)busy1);
  const Run enc = simulate(busy1, MODE_ENCOUNTER, 30, 0, 5000u);
  std::printf("      profile 1 ENCOUNTER: meter %u..%u permille, old 60 s window %u..%u permille\n",
              (unsigned)enc.min_new, (unsigned)enc.max_new, (unsigned)enc.min_old, (unsigned)enc.max_old);
  CHECK(enc.max_old >= 550, "negative control: the old 60 s window reads >= 55.0 % (false YELLOW) at SF12");
  CHECK(enc.max_new <= 500 && enc.min_new >= 490,
        "TxDutyMeter: SF12 profile 1 ENCOUNTER reads 49.0..50.0 % (true 50 %), below the YELLOW limit");
  const Run cru = simulate(busy1, MODE_CRUISE, 30, 0, 5000u);
  CHECK(cru.max_new <= 400 && cru.min_new >= 390,
        "TxDutyMeter: SF12 profile 1 CRUISE reads 39.0..40.0 % (true 40 %)");
  const Run safe0 = simulate(busy0, MODE_SAFE, 20, 0, 5000u);
  std::printf("      profile 0 SAFE: meter %u..%u permille, old 60 s window %u..%u permille\n",
              (unsigned)safe0.min_new, (unsigned)safe0.max_new, (unsigned)safe0.min_old, (unsigned)safe0.max_old);
  CHECK(safe0.max_new <= 250 && safe0.min_new >= 240 && safe0.max_old >= 500 && safe0.min_old == 0,
        "TxDutyMeter: SF12 profile 0 SAFE reads 24.0..25.0 % (old window swung 0 % .. >= 50 %)");
  const uint32_t busy_e22 = radio_airtime_ms(VGQ_RADIO_E22_T33D, 2, ccsds::kCaduLen);
  const Run e22 = simulate(busy_e22, MODE_CRUISE, 200, 0, 5000u);
  CHECK(e22.max_new <= 400 && e22.min_new >= 395, "TxDutyMeter: E22 2.4k CRUISE reads 39.5..40.0 % (true 40 %)");
}

HOST_TEST(tx_duty_open_cycle_and_silence) {
  TxDutyMeter m;
  CHECK(m.permille(1000, false, 0, 3000) == 0 && m.permille(1000, true, 500, 3000) == 0,
        "TxDutyMeter: 0 before the first transmission");
  // One 1000 ms transmission at t = 10 s, period 4000 ms: right after it the open cycle
  // counts as the full period (25 %), never 1000 / 1000.
  m.on_tx(10000, 1000);
  CHECK(m.permille(11000, false, 0, 4000) == 250 && m.permille(13999, false, 0, 4000) == 250,
        "TxDutyMeter: open cycle = max(elapsed, period in force): no jump when a transmission ends");
  CHECK(m.permille(14000, true, 14000, 4000) == 250, "TxDutyMeter: the next launch closes the cycle");
  // Silence (TXINHIBIT / hibernation): busy / elapsed, decaying to 0.
  const uint16_t a = m.permille(20000, false, 0, 4000), b = m.permille(110000, false, 0, 4000),
                 c = m.permille(1010000, false, 0, 4000);
  CHECK(a == 100 && b == 10 && c == 1, "TxDutyMeter: silence decays the reading (10.0 %, 1.0 %, 0.1 %)");
  // Cycles launched more than kWindowMs ago drop out; the newest always counts.
  TxDutyMeter w1, w2;
  for (uint32_t k = 0; k < 10; ++k) {                                     // 50 % for 20 s
    w1.on_tx(k * 2000u, 1000u);
    w2.on_tx(k * 2000u, 1000u);
  }
  w1.on_tx(250000u, 1000u);                                               // after a 232 s gap
  w2.on_tx(400000u, 1000u);                                               // after a 382 s gap
  CHECK(w1.permille(251000u, false, 0, 2000u) == 11u * 1000u * 1000u / (9u * 2000u + 232000u + 2000u),
        "TxDutyMeter: a silent gap inside the 300 s window counts as one long cycle (4.3 %)");
  CHECK(w2.permille(401000u, false, 0, 2000u) == 500,
        "TxDutyMeter: cycles launched more than 300 s ago are dropped, the newest always counts");
  // Ring overflow: only kCycles cycles are kept.
  TxDutyMeter r;
  for (uint32_t k = 0; k < 100; ++k) r.on_tx(k * 3000u, (k < 90) ? 3000u : 1000u);
  CHECK(r.permille(300000u, false, 0, 3000u) == (uint16_t)((10u * 1000u + 6u * 3000u) * 1000u / (16u * 3000u)),
        "TxDutyMeter: the last kCycles = 16 cycles are averaged");
  // u32 millis() wrap inside the window.
  TxDutyMeter z;
  z.on_tx(0xFFFFF000u, 1000u);
  z.on_tx(0xFFFFF000u + 4000u, 1000u);
  CHECK(z.permille(0xFFFFF000u + 8000u, false, 0, 4000u) == 250, "TxDutyMeter: correct across the millis() wrap");
}

// ---- Radio reconfiguration (Flight::apply_radio_config / service_tx / send_next_frame) ----
namespace {

struct Transition {
  uint8_t model, mode, from_code, to_code;
  uint32_t reconfigs;        // reconfigurations applied back to back (a second one: TXPWR)
};

struct TransitionRun {
  uint16_t worst_cycle = 0;  // highest busy / (next launch - launch) of any transmission
  uint16_t worst_meter = 0;  // highest TxDutyMeter reading, sampled every 10 ms
};

// The flight's downlink scheduling, step by step (10 ms loop passes), with the functions
// Flight uses: radio_airtime_ms() for the busy time (the E22 TX tracker's minimum, the SX126x
// time on air), frame_period_ms() on the measured busy time (or the model before the first
// frame at a setting: busy_estimate_ms()), reconfig_hold_ms() / launch_spacing_ms() for the
// spacing across a reconfiguration (with_hold = false: the code before Phase D item 11) and
// TxDutyMeter fed exactly as on_tx_end() / duty_permille() do. The rate change is applied
// after the 4th frame at the old code, as a coordinated change after N frames.
TransitionRun run_transition(const Transition& tr, bool with_hold) {
  TxDutyMeter m;
  TransitionRun r;
  uint8_t code = tr.from_code;
  uint32_t now = 1000u, last_launch = 0, hold = 0, last_airtime = 0, tx_end = 0, start = 0;
  uint32_t frames = 0, pending = 0, prev_launch = 0, prev_busy = 0;
  bool tx = false, have_prev = false;
  auto period_now = [&]() {
    const uint32_t b = last_airtime ? last_airtime : radio_airtime_ms(tr.model, code, ccsds::kCaduLen);
    return frame_period_ms(b, tr.mode, false, 0);
  };
  auto spacing = [&]() { return with_hold ? launch_spacing_ms(period_now(), hold) : period_now(); };
  for (uint32_t step = 0; step < 4000000u && frames < 10; ++step, now += 10u) {   // bounded
    if (tx && (int32_t)(now - tx_end) >= 0) {                  // TX_DONE (on_tx_end)
      tx = false;
      last_airtime = now - start;
      m.on_tx(start, last_airtime);
      if (frames == 4) pending = tr.reconfigs;
      prev_launch = start;
      prev_busy = last_airtime;
      have_prev = true;
    }
    if (!tx) {                                                 // service_tx
      if (pending) {                                           // apply_radio_config
        if (with_hold) hold = reconfig_hold_ms(hold, period_now());
        code = tr.to_code;                                     // (the 2nd one changes the power only)
        last_airtime = 0;
        --pending;
      } else if (now - last_launch >= spacing()) {             // send_next_frame
        if (have_prev) {
          const uint32_t pm = (uint32_t)((uint64_t)prev_busy * 1000u / (now - prev_launch));
          if (pm > r.worst_cycle) r.worst_cycle = (uint16_t)pm;
        }
        start = last_launch = now;
        hold = 0;
        tx = true;
        tx_end = now + radio_airtime_ms(tr.model, code, ccsds::kCaduLen);
        ++frames;
      }
    }
    const uint16_t pm = m.permille(now, tx, start, spacing());
    if (pm > r.worst_meter) r.worst_meter = pm;
  }
  return r;
}

}  // namespace

HOST_TEST(tx_duty_across_a_radio_reconfiguration) {
  const Transition cases[] = {
      {VGQ_RADIO_SX126X, MODE_ENCOUNTER, 0, 1, 1}, {VGQ_RADIO_SX126X, MODE_ENCOUNTER, 0, 7, 1},
      {VGQ_RADIO_E22_T33D, MODE_ENCOUNTER, 0, 7, 1}, {VGQ_RADIO_SX126X, MODE_TEST, 1, 6, 2},
      {VGQ_RADIO_SX126X, MODE_ENCOUNTER, 7, 0, 1},   {VGQ_RADIO_E22_T33D, MODE_ENCOUNTER, 7, 0, 1},
      {VGQ_RADIO_SX126X, MODE_SAFE, 0, 7, 1},        {VGQ_RADIO_E22_T37S, MODE_CRUISE, 2, 7, 1},
  };
  bool all_ok = true, control_shows_defect = false;
  for (const Transition& tr : cases) {
    const TransitionRun fixed = run_transition(tr, true);
    const TransitionRun before = run_transition(tr, false);
    std::printf("      model %u mode %u code %u -> %u%s: worst cycle %u / meter %u permille (without the hold "
                "%u / %u)\n", (unsigned)tr.model, (unsigned)tr.mode, (unsigned)tr.from_code, (unsigned)tr.to_code,
                tr.reconfigs > 1 ? " + TXPWR" : "", (unsigned)fixed.worst_cycle, (unsigned)fixed.worst_meter,
                (unsigned)before.worst_cycle, (unsigned)before.worst_meter);
    all_ok = all_ok && fixed.worst_cycle <= kMaxTxDutyPermille && fixed.worst_meter <= kMaxTxDutyPermille;
    if (tr.model == VGQ_RADIO_SX126X && tr.mode == MODE_ENCOUNTER && tr.from_code == 0 && tr.to_code == 1)
      control_shows_defect = before.worst_cycle >= 990 && before.worst_meter >= 550;
  }
  CHECK(control_shows_defect,
        "negative control: without the hold, SX126x 0 -> 1 in ENCOUNTER runs a ~100 % cycle, meter >= 55 %");
  CHECK(all_ok, "reconfiguration hold: every cycle and every meter reading <= kMaxTxDutyPermille "
                "(slow -> fast, fast -> slow, two reconfigurations before the next launch, SAFE, CRUISE)");
  CHECK(reconfig_hold_ms(0, 69272) == 69272 && reconfig_hold_ms(69272, 34636) == 69272 &&
            launch_spacing_ms(34636, 69272) == 69272 && launch_spacing_ms(69272, 0) == 69272,
        "reconfig_hold_ms never shortens a hold; launch_spacing_ms = max(period, hold)");
}
