# 3.1 adversarial review findings (13a60c5..98887f5)

Five review lenses (FL flight + Nicla, ICD cross-program, GS ground port, LS link statistics, UI IMU/dashboard),
each finding re-checked by a skeptical verifier. Status: fixes in progress (branch v31-fixes, not yet merged).

## FL-01 [CONFIRMED / major] Nicla recovery reports only 0x0607, which the ground ignores: the Nicla stays "lost" on the ground and the separation alarm is dropped
`vgq1-spacecraft/sketch/src/fsw/flight.cpp:1998`

**Description.** When the Nicla canary trips (aru_fail_count_ > 5) the flight sends 0x0606 "Nicla Sense ME stopped responding on <bus>", and the ground's SensorBusState marks the Nicla "lost". When the Nicla answers again, the flight only sends 0x0607 "Nicla Sense ME responding again" (flight.cpp:1996-1998). aru_.ok() never goes false, so probe_nicla() and its 0x060B acquisition report never run again. 0x0607 is not in dictionary.SENSOR_BUS_EVRS, and SensorBusState.on_evr (sensors.py:296-323) returns False for it. The RM3100 path is different: it re-reports through 0x060B.

**Failure scenario.** I reproduced this with the real SensorBusState. Nicla and RM3100 outboard both on A4/A5, so the 0x060D "not separated" EVR arrives and separated() is False (YELLOW alarm). Then the ESLOV wire bounces: 0x0606, then 0x0607 a few seconds later. Result: snapshot separated=None, text "unknown (no current acquisition report of the Nicla Sense ME)", Nicla state "lost", lost=['Nicla Sense ME (A4/A5)'], although HK NICLA_ME is set again. The wiring is still wrong but the YELLOW reason disappears until the next reboot.

**Proposed fix.** Flight: in the 0x0607 branch call report_acquired("Nicla Sense ME", aru_bus_, SBUS_NICLA) and report_separation(u) for both units. Alternatively, on the ground, treat 0x0607 as a re-acquisition on the Nicla's last reported bus. Add an e2e test for the drop/return sequence.

**Verifier.** NiclaAru::ok() is set only in begin() (payload.cpp:10, payload.h:53), so after a canary trip aru_.ok() stays true and reprobe_instruments() never calls probe_nicla() again (flight.cpp:2043). The reads simply resume in start_mag_sample, which sets SNS_NICLA_ME again (flight.cpp:2303), and fdir_1hz only sends INFO 0x0607 (flight.cpp:1996-1998). 0x0607 is not in dictionary.SENSOR_BUS_EVRS (dictionary.py:147-156), and SensorBusState.on_evr returns False for it. I reproduced the finding with the real module (verify-FL/fl01.py). Boot reports put the Nicla and the ob unit both on A4/A5 with WARN 0x060D 'not separated', and separated() is False. After 0x0606 then 0x0607, on_evr(0x0607) is False and the snapshot is separated=None, 'unknown (no current acquisition report of the Nicla Sense ME)', nicla state 'lost', lost=['Nicla Sense ME (A4/A5)'], with HK all OK. The YELLOW reason (gds.py:2160) is gone until the next reboot. The Nicla also shows 'lost' in every layout after any outage longer than 6 s. tests/test_sensors.py:225 models the recovery as a 0x060B, which the flight never sends for the Nicla.

**Fix notes.** Simplest fix, ground only: in SensorBusState.on_evr, treat 0x0607 'Nicla Sense ME responding again' as a re-acquisition on the Nicla's last reported bus. Keep the playback rule. Add the format to SENSOR_BUS_EVRS (the cross-check test covers it). The pair verdict then becomes False again from the stored RM3100 bus and the YELLOW returns. Change test_lost_instruments_and_recovery to feed 0x0607. Flight-side alternative: in the 0x0607 branch call report_acquired("Nicla Sense ME", aru_bus_, SBUS_NICLA) and report_separation(0/1). This costs extra VC0 EVRs (see FL-07).

## FL-03 [CONFIRMED / major] Page 2 is read only in PA-off windows: about 31 % of the ARU's 1 s windows are never aggregated and IMU_F_GAP is set in almost every ATT
`vgq1-spacecraft/sketch/src/fsw/flight.cpp:2232`

**Description.** The ARU publishes only the last completed 1.000 s window. The flight reads page 2 only inside the 1 Hz magnetometer sample, which waits in MAG_WAIT while tx_state_ != TX_IDLE. In TEST/ENCOUNTER the E22 at 2.4 kbit/s is busy 1267 ms of every 2534 ms frame period, so reads are delayed by up to 1.27 s. Whenever two windows close between two reads, the older one is lost: ImuStats counts it as a gap and leaves it out of vib_rms, vib_peak, rate_mean and rate_peak. docs/13 9.4 describes imu_nwin as "1 s windows in the ATT period" and rate_peak as "the largest |omega| of one sample in the period". In practice they cover only the windows that happened to be read. GAP (amber on the dashboard) can no longer tell an ARU that misses samples apart from normal transmitting.

**Failure scenario.** I simulated it: 1 Hz due samples deferred to the end of 1.267 s TX bursts every 2.534 s, with random Nicla window phase, over 200 h. Only 68.7 % of windows were read, and 97.2 % of 8 s TEST ATT periods carry IMU_F_GAP. A 1 s motion event (peak rate above still_rate_dps) falls in an unread window about 31 % of the time, and the ground then shows "stationary" for that ATT period.

**Proposed fix.** Read page 2 every second regardless of the PA. It is a read of statistics already computed on the Nicla; the PA-off rule protects the magnetometer samples, and page 2 has none. Keep the paired page-0/page-2 read inside the sample for q_grv/IMU_F_PAIRED. Alternatively, have the ARU keep cumulative sums (count, sum of vib_rms^2, max peaks, sum of rate means) since boot, so the flight can difference them without losing windows. Report flight-side skips separately from ARU_IMU_MISSED, or fix the docs/13 wording.

**Verifier.** service_mag waits in MAG_WAIT while tx_state_ != TX_IDLE (flight.cpp:2232). Page 2 is read only inside start_mag_sample (flight.cpp:2280), and the ARU exposes only the last completed window (nicla_sense_me_aru.ino close_window/g_win, page rebuild at 10 Hz). ENCOUNTER/TEST frame period = 2.0 x busy (datalink.cpp:284). Busy = 236 octets: 246 ms UART + 1023 ms air at 2.4 kbit/s = ~1268 ms (radio.cpp:122-127). TX start is not held back by MAG_WAIT (flight.cpp:963-965 holds only MAG_COLLECT). My Monte Carlo (verify-FL/fl03.py, 50 h, 1 Hz ticks deferred to TX end, random Nicla phase, 20 ppm drift, 0-100 ms rebuild latency) gives windows read 69.2 % in TEST/ENCOUNTER, 75 % in CRUISE and 84 % in SAFE. 97 % of 8 s ATT periods (100 % of 60 s CRUISE periods) contain an unread window, so IMU_F_GAP is effectively always amber and no longer separates ARU_IMU_MISSED from normal transmitting. docs/13 9.4 ('1 s windows in the ATT period', rate_peak 'the largest |omega| of one sample in the period') overstates what is aggregated. The design comment (attitude.h:91-93) knowingly accepts unread windows, but not at this rate. One correction: read gaps never exceed ~1.27 s, so two consecutive windows are never both unread. A sustained 1 s motion at random phase therefore almost always reaches a read window. The ~31 % miss rate applies to sub-second impulses that fall inside one window, not to every 1 s motion.

**Fix notes.** Read page 2 (one ~3 ms transfer on the NICLA bus) once per second independent of the PA. The PA-off rule protects RM3100 samples, and AS7265X polling already runs during TX with a budget. Keep the paired page-0/page-2 read in the sample for q_grv/IMU_F_PAIRED, and feed imu_stats_ from whichever read is newer (win_seq de-dup already handles duplicates). Alternative: cumulative window sums on the ARU. At minimum, fix the docs/13 wording and report flight-side skips separately from ARU_IMU_MISSED.

## GS-1 [CONFIRMED / major] LED-matrix RED alarm row never blinks and is usually blank (blink phase aliased by the 250 ms redraw)
`dssq-groundstation/sketch/sketch.ino:1505`

**Description.** matrix_service() redraws every kMxPeriodMs = 250 ms (line 1452, 1520), and the loop polls far faster than 1 ms, so draws land exactly 250 ms apart. Row 7 for RED uses ((now / kAlarmRedHalfMs) & 1) with kAlarmRedHalfMs = 125 ms (line 1505). floor((t0 + 250k)/125) = floor(t0/125) + 2k, so the parity never changes. Row 7 is therefore either lit all the time or dark all the time, depending on the phase of the first draw. It only flips after a loop stall moves the phase. The link-down flash at line 1482 (kLinkDownHalfMs = 100 ms) aliases to a 1 Hz pattern, not the 'fast like the ALARM lamp' the comment at lines 1444-1446 promises. The harness check 'RED = 13 blinking' (tools/host_test/station/test_e22.cpp:237) calls matrix_frame() at hand-picked times (+700 ms / +100 ms), not at the redraw cadence, so it cannot see the aliasing.

**Failure scenario.** Reproduced with the real sketch compiled against the station harness (scratch /tmp/claude-0/-home-user/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/review31/gs/alias_test.cpp). Send S|1|2|0|0 (RED), then call matrix_service() every 1 ms for 20 s. Result: 'row 7 all lit in 0 frames, dark in 80 frames'. During a RED alarm the matrix alarm row is empty, which looks less severe than GREEN (1 column). In the other phase it is a steady 13-column bar, never blinking. Link down gives '..##..##..##': 1 Hz, not 5 Hz.

**Proposed fix.** Derive every matrix blink from a multiple of the redraw period. For example, set RED and link-down on the matrix to ((now / kMxPeriodMs) & 1) (2 Hz: on one frame, off the next). Or redraw at a period that divides both half-periods, e.g. kMxPeriodMs = 25. Then correct the comment at lines 1444-1446. Add a harness check that drives matrix_service() at the loop cadence and requires both lit and dark row-7 frames within 2 s.

**Verifier.** sketch.ino:1452 kMxPeriodMs = 250. matrix_service() (1518-1524) sets last = now and is called only from loop() (1708). g_matrix.draw() is called only there and in matrix_begin(). Row 7 RED uses (now / kAlarmRedHalfMs) & 1 with kAlarmRedHalfMs = 125 (160, 1505). Frames 250 ms apart are 2 half-periods apart, so the parity can never alternate from one frame to the next. Link-down uses kLinkDownHalfMs = 100 (161, 1482): frames advance 2.5 half-periods, giving a 4-frame (1 s) pattern. Reproduced independently with the REAL setup()/loop() on real host time (scratch verify-GS/loop_alias.cpp, sketch compiled with the station harness runtime, 3 runs). RED gave '...####', '...####.......' and '...############': long steady runs, never alternating. Link down gave '..##..##..##..##' (1 Hz, not 5 Hz). This matches the GS 1-ms-step result (0 lit / 80 dark frames in 20 s). Contract section 1 requires 'row 7 alarm level (... RED 13 blinking)'. The comment at 1444-1446 promises 'blinking fast like the ALARM lamp'. The harness check at test_e22.cpp:237 calls matrix_frame() at hand-picked times (mx_time(true,true) = +700 ms, mx_time(false) = +100 ms), never at the redraw cadence, so it cannot see this. The physical ALARM lamp and buzzer (annunciators(), every loop pass, 438/460) blink correctly; only the matrix is wrong. The lamps are owner-wired extras, so the matrix can be the only local indicator. In one phase a RED alarm shows as an EMPTY row 7, which looks milder than GREEN (1 column).

**Fix notes.** Minimal: sample the matrix at a period that divides every half-period. gcd(100, 125, 500) = 25, so kMxPeriodMs = 25. Better: compute matrix_frame() every 25 ms but call g_matrix.draw() only when memcmp() shows the frame changed. That keeps exact blink edges at low draw cost. Alternative: derive matrix blinks from a frame counter, e.g. RED = (frame & 1) at 250 ms = 2 Hz, and document the rate. Then fix the comment at 1444-1446 and the '4 Hz redraw' comment. Add a harness check that drives matrix_service() in 1 ms steps for 2 s with RED and with link-down. It must see both lit and dark row-7 frames, and at least 8 transitions per second for link-down.

## GS-2 [CONFIRMED / major] Station pin tables in docs still give the old pin plan; wiring by them puts the E22 AUX output against the new AOS lamp output on D4
`docs/15_Configuration_Reference.md:444`

**Description.** Code (pin plan H, sketch.ino:149-152, 532): RCU M0 D2, M1 D3, AUX D7; lamps AOS D4, ALARM D5, UPLINK D6; buzzer A1; SX126x NRST A4. The docs still give AUX D4, lamps D5/D6/D7, buzzer A0 and NRST A1. Affected lines: docs/15:437, 443, 444, 445 and docs/15:406 ('[gds] rcu ... D2/D3/D4'); docs/06_Displays.md:69-72 and 182; docs/03_Hardware_Integration.md:514-515, 570, 603, 620, 623 and 671-676; docs/00_Beginner_Setup_Guide.md:683, 698, 701 and 887; docs/14_Troubleshooting_and_Event_Response.md:88. In 3.1, setup() drives D4 as a push-pull OUTPUT (the AOS lamp, sketch.ino:1669-1670). The E22 AUX pin is an output that drives high and low (T33D manual 5.6).

**Failure scenario.** An owner wires the ground E22 by docs/03 5.1 or docs/00 (AUX to D4, UPLINK LED to D7) and flashes 3.1. D4 is held LOW (AOS off) while the idle module drives AUX HIGH, so two outputs fight on one line. D7 (the RCU AUX input, pull-up) reads HIGH with nothing driving it, so every rcu_mode() reports 'OK' while the module is not followed. The lamps sit one pin off and the buzzer on A0 never sounds.

**Proposed fix.** Update every station pin table and diagram to plan H: AUX D7 (PB2), AOS D4, ALARM D5, UPLINK D6, buzzer A1, SX126x NRST A4 (PC1), A0 free. That covers docs/15 section 5 plus the rcu row, docs/06 section 2 / RCU table, docs/03 5.1 / 5.3.1 / 5.4 / 5.5 mermaid, docs/00 and docs/14:88.

**Verifier.** Code (plan H, D-72): sketch.ino:149 PIN_RCU_M0 = 2, M1 = 3, AUX = 7; 152 AOS = 4, ALARM = 5, UPLINK = 6, BUZZER = A1; 532 PIN_SX_NRST = A4. The harness asserts the same (test_e22.cpp: 'pin plan H'). setup() 1669-1670 makes D4 an OUTPUT driven LOW. The docs still give the old plan. docs/15:437 and 444 say AUX D4; 443 says NRST A1; 445 says lamps D5/D6/D7 and buzzer A0; 406 says 'D2/D3/D4'. docs/06:69-72 and 182; docs/00:683, 698, 700, 701, 887 and 891 (also 'on the VENTUNO Q, A0 drives the buzzer'); docs/03:514, 570, 603, 622, 623, 671 and 676; docs/14:88. Further stale spots the GS list missed: docs/01:297-298 (lamps D5/D6/D7, buzzer A0, AUX D4) and 352 (NRST A1); docs/03:330, 360 and 365; docs/07:659; README.md:178; docs/FAQ.md:237. The T33D manual 5.6 says AUX outputs a high level when idle and a low level when busy, so a D4 driven LOW against an idle AUX is two outputs fighting. Anyone wiring lamps by these docs gets lamps one pin off (the AOS LED on D5 shows ALARM, the ALARM LED on D6 shows UPLINK, the UPLINK LED on D7 is never driven) and a silent buzzer on A0. One correction to the failure scenario: with the UPLINK LED wired to D7 per the old docs, the AUX input's internal pull-up into that LED + 330 ohm gives an undefined level near the LED's forward voltage, not a guaranteed HIGH. rcu_mode() may report OK or FAILED unpredictably; it is not always 'OK'. The docs phase (contract section 7) is still to come, but at 98887f5 these are the shipped wiring instructions.

**Fix notes.** Update every station pin table and diagram to plan H: M0 D2, M1 D3, AUX D7 (PB2); AOS D4, ALARM D5, UPLINK D6; buzzer A1 (PA5); SX126x NRST A4 (PC1); A0 free. That covers docs/15 section 5 (437, 443-445) and the [gds] rcu row (406); docs/06 section 2 and the RCU table; docs/03 (330, 360, 365, 514, 570, 603, 622-623, 671-676); docs/00 (683, 698-701, 887, 891); docs/01 (297-298, 352); docs/07:659; docs/14:88; FAQ:237; README.md:178. Leave the historical test record docs/21:638 and the D-68 entry docs/12:911 as recorded history (label them). Optionally add a docs drift test that greps the station pin constants out of sketch.ino and compares them with docs/15 section 5.

## ICD-1 [CONFIRMED / major] Nicla recovery (EVR 0x0607) is not parsed: after a transient loss the ground shows the Nicla LOST for good and drops the 'not separated' YELLOW alarm
`dssq-groundstation/python/dssq/sensors.py:310`

**Description.** The flight never re-probes the Nicla after a run-time loss. NiclaAru::ok_ is only set in begin(), so reprobe_instruments() never calls probe_nicla() again and no new 0x060B is sent. Recovery is reported only as INFO 0x0607 "Nicla Sense ME responding again" (flight.cpp:1998), which names no bus. dictionary.evr_instrument_bus() and evr_instrument_lost() (dictionary.py:166/194) and SensorBusState.on_evr() ignore 0x0607. The Nicla's report therefore stays 'lost' (from 0x0606), and separation() counts only state == 'acquired' (sensors.py:357). tests/test_sensors.py:225 'recovers' the Nicla with an 0x060B that the flight cannot produce, so the test passes on a path that does not exist. The same stale-LOST display happens when a routine INFO 0x060B for a re-acquired RM3100 is dropped by the EVR gate or the EVRLEVEL filter.

**Failure scenario.** Owner layout with the Nicla and RM3100 outboard both on A4/A5. At boot: 0x060B x2 plus 0x060D 'not separated', so separated=False and the YELLOW alarm 'sensor wiring: RM3100 and Nicla not on separate buses' is up. docs/08 T-FD-04: unplug the Nicla for 10 s, giving 0x0606, then reconnect, giving 0x0607, and HK NICLA_ME is OK again. Reproduced with SensorBusState: snapshot(0x07FF) -> separated None, 'unknown (no current acquisition report of the Nicla Sense ME)', lost=['Nicla Sense ME (A4/A5)'], nicla.state 'lost' with hk_ok True. The YELLOW wiring alarm is gone although the wiring is unchanged, and the SPACECRAFT tab shows the Nicla red LOST until the next reboot.

**Proposed fix.** Flight: print the bus in 0x0607 ("Nicla Sense ME responding again on %s") and add it to SENSOR_BUS_EVRS. In SensorBusState.on_evr, treat 0x0607 as an acquisition of the Nicla on that bus (or on its last bus). As a fallback in snapshot()/separation(), count a 'lost' instrument as acquired when the newest HK has its bit set and that HK is newer than the loss report. Replace the 0x060B Nicla 'recovery' in test_sensors.py:225 with the real 0x0607 sequence.

**Verifier.** Flight: NiclaAru::ok_ is set only in begin() (payload.cpp:10). A run-time loss only clears sensor_health and sets FDIR_SENSOR_LOST (flight.cpp:1988-1995); aru_.ok() stays true, so reprobe_instruments() (flight.cpp:2043, `!aru_.ok() && probe_nicla()`) never runs again and no new 0x060B is sent. Recovery is reported only as INFO 0x0607 "Nicla Sense ME responding again" (flight.cpp:1998), and that text names no bus. Ground: SENSOR_BUS_EVRS, evr_instrument_bus() and evr_instrument_lost() (dictionary.py:147-205) do not handle 0x0607, so SensorBusState.on_evr returns False for it. Reproduced in /tmp/claude-0/-home-user/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/review31/verify-ICD/icd1.py. At boot, A4/A5 shared, the verdict is separated=False with 'NOT SEPARATED ...'. After 0x0606 and then 0x0607, snapshot(0x07FF) gives separated=None, 'unknown (no current acquisition report of the Nicla Sense ME)', lost=['Nicla Sense ME (A4/A5)'], nicla.state='lost', hk_ok=True. gds.py:2160 raises the YELLOW reason only when separated is False, so the alarm drops. dashboard.html:1770 shows the Nicla red LOST. tests/test_sensors.py:233 'recovers' the Nicla with a 0x060B the flight cannot send after a run-time loss; the test passes (42 passed). The secondary claim also holds: a run-time INFO 0x060B for a re-acquired RM3100 is subject to the level filter and the EvrGate (flight.cpp:1550-1554) and to frame loss, which leaves the same stale LOST.

**Fix notes.** Flight: print the bus, evr(1, 0x0607, "Nicla Sense ME responding again on %s", sensor_bus_name(aru_bus_)), and add that format to SENSOR_BUS_EVRS (the cross-check test enforces the literal). Ground: in evr_instrument_bus() (or as a separate parser) map 0x0607 to ("Nicla Sense ME", bus) as an acquisition. Robust fallback in SensorBusState.snapshot()/separation(): treat a 'lost' instrument as acquired on its last bus when the newest HK has its sensor_health bit set and that HK is newer than the loss report. Replace the 0x060B Nicla 'recovery' at tests/test_sensors.py:233 with the real 0x0606 -> 0x0607 sequence, and add a case with the shared-bus layout that checks the YELLOW reason survives.

## ICD-3 [CONFIRMED / major] docs/15 still documents the old (VENTUNO) station pin plan and old defaults: RCU AUX D4, lamps D5/D6/D7, buzzer A0, SX NRST A1, archive_max_gb 16, fault_log false
`docs/15_Configuration_Reference.md:444`

**Description.** The code (sketch.ino:149/152/532, pin plan H, D-72) uses RCU M0/M1/AUX = D2/D3/D7, lamps AOS/ALARM/UPLINK = D4/D5/D6, buzzer A1, SX126x NRST A4. docs/15 contradicts this in: section 5 heading 'Station sketch build settings (VENTUNO Q MCU)' (line 430); line 437 'RCU on D2 M0, D3 M1, D4 AUX'; line 441 'PIN_SX_NRST ... A1'; line 444 'PIN_RCU_AUX D4'; line 445 'D5, D6, D7, A0'; and line 406 ([gds] rcu 'M0/M1/AUX on D2/D3/D4'). It also contradicts config.py: line 410 archive_max_gb default 16.0 (code 8.0, D-80) and line 277 fault_log default 'false' (code: true for model = E22-400T37S, fault_log_default(), D-77). docs/13:2997 also still says 'default 16 GB'. tests/test_docs.py does not check these values.

**Failure scenario.** An operator wires the station from docs/15. The E22 AUX output goes to D4, which the sketch drives as the AOS lamp output: two outputs fight on one net, the RCU never sees AUX and reports FAILED. The SX126x NRST lands on A1, the buzzer driver, so the radio is reset by every buzzer tone. A T37S user reads that the abnormal-status log is off by default when it is on.

**Proposed fix.** Update docs/15: section 5 title to the ground UNO Q; the PIN_RCU_* row to D2/D3/D7; the PIN_LED_*/PIN_BUZZER row to D4/D5/D6/A1; PIN_SX_NRST to A4; the [gds] rcu text to D2/D3/D7; archive_max_gb default to 8.0 (D-80); fault_log default to 'model-dependent: true for E22-400T37S, else false'. Fix docs/13:2997 to 8 GB. Add a docs test that reads these rows from sketch.ino and config.DEFAULTS.

**Verifier.** sketch.ino:149 PIN_RCU_M0/M1/AUX = 2/3/7; :152 lamps AOS/ALARM/UPLINK = 4/5/6, buzzer A1; :532 PIN_SX_NRST = A4. config.py:107 archive_max_gb 8.0; config.py:144-150 fault_log_default() is true for model E22-400T37S. docs/15 contradicts all of these: line 406 (rcu on D2/D3/D4), 410 (archive_max_gb 16.0), 277 (fault_log false), 430 (heading 'VENTUNO Q MCU'), 437, 441 (NRST A1), 444 (AUX D4), 445 (D5, D6, D7, A0). docs/13:2997 says 'default 16 GB'. The diff did touch docs/15 (still_rate_dps etc.) without fixing these rows. The drift is wider than reported: the same old plan is in docs/03:514/570/603/623/671 (the wiring chapter itself), docs/00:683/701/887, docs/01:297, docs/06:72/182, docs/14:88, docs/FAQ.md:237, README.md:178 and dssq-groundstation/README.md:28. Contract section 7 puts the docs overhaul in a later, still pending phase. As of 98887f5, though, anyone wiring from these docs puts the E22 AUX output on D4, which the sketch drives as the AOS lamp output, and SX NRST on A1, the buzzer pin.

**Fix notes.** In the docs phase, update every listed location to pin plan H: RCU D2/D3/D7, lamps D4/D5/D6, buzzer A1 (A0 free), SX NRST A4/RXEN A2/TXEN A3; archive_max_gb 8.0 (D-80); fault_log 'model-dependent: true for E22-400T37S, else false' (D-77); docs/13:2997 to 8 GB; and drop 'VENTUNO Q' from section titles. Add a tests/test_docs.py check that parses the PIN_* constants from sketch.ino and config.DEFAULTS/fault_log_default and compares them with the docs/15 rows (and with the docs/03 wiring table).

## UI-01 [CONFIRMED / major] The ground ignores the Nicla recovery EVR 0x0607, so the Nicla stays LOST and the not-separated alarm clears by itself
`dssq-groundstation/python/dssq/sensors.py:310`

**Description.** After a transient Nicla loss the flight never sends a new acquisition report (0x060B). NiclaAru::ok_ is set only in begin() (drivers/payload.cpp:10) and read failures never clear it. The re-probe at flight.cpp:2043 runs only when !aru_.ok(), so probe_nicla()/report_acquired() are not called again. When reads succeed again, start_mag_sample sets SNS_NICLA_ME and FDIR sends only 0x0607 "Nicla Sense ME responding again" (flight.cpp:1996-1998). SensorBusState.on_evr handles 0x0606 as "lost" but has no case for 0x0607, and dictionary.SENSOR_BUS_EVRS does not list it. separation()/counted() count only state == "acquired". docs/13 section 7.6 says the flight reports "when an instrument is lost or found again", and tests/test_sensors.py:225 models the recovery with a 0x060B that the flight never sends on this path.

**Failure scenario.** I reproduced this with SensorBusState. Boot reports put the Nicla and RM3100 outboard both on A4/A5, plus the 0x060D not-separated WARN: separated is False and the YELLOW reason "sensor wiring: RM3100 and Nicla not on separate buses" is raised. Then the ESLOV bus drops out for more than 6 s: 0x0606 "Nicla Sense ME stopped responding on A4/A5". The Nicla comes back: 0x0607, and HK NICLA_ME is OK again. Result: snapshot nicla.state stays 'lost', the SPACECRAFT row shows red "LOST (EVR 0x0606 ...)", lost = ['Nicla Sense ME (A4/A5)'], separated = None ("unknown (no current acquisition report of the Nicla Sense ME)"). The YELLOW wiring alarm disappears although the two instruments still share A4/A5. This lasts until the next reboot.

**Proposed fix.** In SensorBusState.on_evr, handle event 0x0607 (text starting "Nicla Sense ME responding again"): if reports['nicla'] exists, call _put('nicla', reports['nicla']['bus'], 'acquired', 0x0607, t, playback) and return True. Add 0x0607 to SENSOR_BUS_EVRS and docs/13 section 7.6. Change test_lost_instruments_and_recovery to use 0x0607. (An alternative is a flight-side report_acquired() on recovery.)

**Verifier.** Flight: NiclaAru::ok_ is set only in begin() (drivers/payload.cpp:10). Nothing clears it on a read failure, and nothing else calls aru_.begin()/probe_nicla() (grep: only flight.cpp:500 init_sensors and :2043). reprobe_instruments() re-probes the Nicla only when !aru_.ok() (flight.cpp:2043), so after a loss there is never a new 0x060B or 0x060D. start_mag_sample() keeps reading while aru_.ok(). On the first good read it sets aru_fail_count_=0 and SNS_NICLA_ME (flight.cpp:2276-2278, 2303), and fdir_1hz then sends only 0x0607 "Nicla Sense ME responding again" (flight.cpp:1996-1998). Ground: SensorBusState.on_evr knows only not-separated, lost (0x0606/0x060E) and acquired (0x060B/0x060D/0x0706) (sensors.py:296-323). dictionary.SENSOR_BUS_EVRS has no 0x0607 (dictionary.py:147-156). counted() requires state == 'acquired' (sensors.py:357). I reproduced it with the real class (scratchpad/review31/verify-UI/ui01.py): boot with the Nicla and RM3100 ob both on A4/A5 plus the WARN gives separated False. Then 0x0606 is used, 0x0607 returns False (not used), and the snapshot reads nicla.state 'lost', lost ['Nicla Sense ME (A4/A5)'], separated None, 'unknown (no current acquisition report of the Nicla Sense ME)'. gds.py:2160 raises the YELLOW reason only when separated is False, so the wiring alarm silently drops, and the red LOST (dashboard.html:1770) stays until the next reboot. docs/13:2058 itself names 0x0607 as the recovery EVR. tests/test_sensors.py:225 models the recovery with a 0x060B that the flight does not send on this path.

**Fix notes.** Ground-only fix (smallest, and it matches the flight as it stands). In SensorBusState.on_evr: if event_id == 0x0607 and text.startswith("Nicla Sense ME responding again") and reports.get('nicla') exists with state 'lost', call _put('nicla', reports['nicla']['bus'], 'acquired', 0x0607, t, playback), increment used, and return True. The flight keeps aru_bus_ unchanged on this path, so the old bus is correct, and the ground recomputes separation itself, so the not-separated verdict and the YELLOW reason come back. Add EVR_NICLA_RECOVERED = 0x0607 with its literal format to SENSOR_BUS_EVRS (the cross-check test compares the formats with the flight). Update docs/13 sections 7.6 and 9.x (state 'acquired' also from 0x0607). Change test_lost_instruments_and_recovery to feed 0x0607 and assert that separated is restored. A flight-side report_acquired() plus report_separation() on recovery is the alternative.

## FL-02 [CONFIRMED / minor] MAG-bus FDIR can never fire when only RM3100s are on it: loss detection stops traffic at <= 10 consecutive errors, the FDIR threshold is > 10
`vgq1-spacecraft/sketch/src/fsw/flight.cpp:2322`

**Description.** Each failed RM3100 sample adds one consecutive error (the POLL write8 fails, I2cBus::note(false)). note_mag_sample marks the unit lost after kRm3100LossSamples = 5 failures, and after that it is never polled. That is 5 consecutive errors with one unit and exactly 10 with two. fdir_1hz recovers a bus only when consecutive_errors() > kI2cFdirErrors (10) (flight.cpp:1968). The re-probe path adds nothing: I2cBus::probe() is not counted, and Rm3100::begin stops at the failed probe. bus_clear_failed_[SBUS_MAG] is always false because recover() returns true without pins. So the MAG bus is never re-initialised (the PE reset in setClock). In 3.0 the RM3100s shared the bus with the Nicla, which is polled every second, so the count went past 10 and the bus was recovered.

**Failure scenario.** Recommended layout (RM3100 ob, optionally ib, alone on Qwiic; AS7265X on A4/A5). The I2C4 controller locks up after a glitch on the long boom cable, and every transfer fails. The RM3100 is declared lost after 5 s (0x060E) with consec_ = 5 (or 10). No 0x0605, no FDIR_I2C_BUS and no recover() ever follow. Re-probes every 30 s to 10 min fail on the stuck controller, so the boom magnetometers stay lost until a reboot. FDIR only works in the owner's layout because the AS7265X FAULT path happens to share the bus.

**Proposed fix.** In note_mag_sample when a unit is declared lost, or in reprobe_instruments before probing a lost unit, call bus_recover(b, now, "RM3100 lost") on mag_bus_of_[u] when recover_[b].allowed() and bus_of(b).consecutive_errors() > 0. Alternatively, make the FDIR trigger reachable, e.g. >= kRm3100LossSamples on a bus whose only instruments were declared lost.

**Verifier.** The mechanism is right. Rm3100::start() makes one counted write8 per sample (sensors.cpp:41), and I2cBus::note(false) adds one consecutive error (i2c_bus.cpp:20-24). probe() is not counted (i2c_bus.cpp:26-29), and begin() stops at the failed probe. note_mag_sample marks the unit lost at kRm3100LossSamples=5 (sensor_bus.h:70, flight.cpp:2322-2331). After that, start_mag_sample skips it (want = en && m.ok()). fdir_1hz needs consecutive_errors() > 10 (flight.cpp:87, 1968). I modelled it with the real LossCounter (verify-FL/fl02.cpp): the maximum is 5 consecutive errors with one unit and 10 with two, and FDIR fires for 0 s per hour. bus_clear_failed_[SBUS_MAG] is always false (recover() returns true without pins). So with only RM3100s on Qwiic (the contract section 3 layout), 0x0605, FDIR_I2C_BUS and recover() never happen. The consequence is overstated, though: 'stays lost until reboot' is not supported. The Zephyr 0.90 STM32 v2 driver calls LL_I2C_Disable (PE=0, the same block reset that setClock gives) in i2c_stm32_master_mode_end at the end of every transfer: STOP, NACK->STOP, error and timeout paths, i2c_ll_stm32_v2.c:269-288, 619, 659, 692-693. It also re-enables PE in msg_init (line 233). A controller-state lockup that recover() would clear is therefore already cleared on every transfer, and the 30 s-10 min re-probes keep running. A target holding SDA cannot be fixed on Qwiic either way. The real loss is the missing 0x0605/I2C_BUS report and an unreachable per-bus FDIR path.

**Fix notes.** In note_mag_sample, when a unit is declared lost and bus_of(mag_bus_of_[unit]).consecutive_errors() >= kRm3100LossSamples (the errors are bus-wide), set FDIR_I2C_BUS, send 0x0605 and call bus_recover(b, millis(), "RM3100 lost") if recover_[b].allowed(). Gate this on fdir_mask_ & FDIR_I2C_BUS. Or document that on a Qwiic bus holding only RM3100s, the loss EVR 0x060E replaces the bus FDIR. Also correct the i2c_bus.h claim that setClock is the only controller reset: the driver already toggles PE on every transfer.

## FL-04 [CONFIRMED / minor] ImuStats keeps accumulating while ATT is off (SAFE): the first ATT after SAFE carries hours of windows
`vgq1-spacecraft/sketch/src/fsw/flight.cpp:2290`

**Description.** imu_stats_.add() runs at every 1 Hz sample in every mode, but reset() is called only in gen_att(). ATT cadence is 0 in BOOT and SAFE (datalink.cpp kModeCadence) and when a period override switches ATT off. The spacecraft boots into SAFE and stays there until the ground commands a mode. The first ATT afterwards therefore aggregates everything since boot: imu_nwin saturates at 255, vib_peak and rate_peak are maxima over hours, and gap_/acc_sat_ are latched from any moment in that time.

**Failure scenario.** Boot, then 2 h in SAFE with the spacecraft handled once (a 3 deg/s bump), then CRUISE. The first ATT (60 s period) shows imu_nwin 255, rate_peak 3.0 deg/s and GAP, so the ground shows "moving" for a spacecraft that has been still for 2 h. The IMU time chart plots that period as one point.

**Proposed fix.** Reset imu_stats_ when ATT is not being generated, e.g. in tick_1hz: if (stretched ATT period == 0) imu_stats_.reset(); or reset it on every set_mode(). Optionally do the same for mstat_* (pre-existing).

**Verifier.** imu_stats_.add() runs at every successful 1 Hz sample in every mode (flight.cpp:2290), and reset() is called only in gen_att() (flight.cpp:1869; that is the only reset site). ATT cadence is 0 in BOOT and SAFE (datalink.cpp:277-278) and with a 0xFFFF override (flight.cpp:1565), and tick_1hz skips period 0 (flight.cpp:1600). setup() enters SAFE (flight.cpp:319). set_mode() zeroes last_gen_s_ (flight.cpp:1152), so the first ATT after SAFE->CRUISE is generated at the next tick and carries everything since boot: windows() saturates at 255 (attitude.h:104), the peaks are maxima over hours, and gap_/acc_sat_/gyro_sat_ are latched. A 3 deg/s bump hours earlier gives rate_peak 3.0 > still_rate_dps 0.5, i.e. 'moving'.

**Fix notes.** In tick_1hz, call imu_stats_.reset() when the stretched ATT period is 0. reset() keeps seen_/last_win_, so nothing is double counted. Or reset it in set_mode(). Optionally do the same for mstat_ob_/ib_/body_ (MAG off in SAFE, pre-existing).

## FL-05 [CONFIRMED / minor] MAG packet after a run-time RM3100 loss: the OB/IB flag is set but the vector is the sentinel, and the period's valid samples are discarded
`vgq1-spacecraft/sketch/src/fsw/flight.cpp:1768`

**Description.** Before 3.1, Rm3100::ok() could not go false at run time. Now note_mag_sample() calls mark_lost(). gen_mag sets MAG_F_OB/IB from mstat_*.count() != 0 but fills the vector with mag_vec(mstat_ob_, mag_ob_.ok()). For a unit lost during the MAG period, ok() is false, so the field is 0x7FFFFFFF n/a while the flag says "OB ok". rm3100_cycle_count also switches to the inboard unit.

**Failure scenario.** CRUISE (MAG period 30 s): the outboard RM3100 gives 20 good samples, then its cable is pulled and it is declared lost after 5 more (0x060E) before gen_mag runs. MAG flags b1 OB = 1, nsamples = 20, but ob_bx/by/bz/rms decode to None: 20 valid measurements are dropped and the packet contradicts itself.

**Proposed fix.** Use the accumulator alone: p.outboard = mag_vec(mstat_ob_, true) (count() == 0 already yields the sentinels), and likewise for inboard. Take rm3100_cycle_count from the cycle count in force (mag_cc_) instead of ok().

**Verifier.** Rm3100::mark_lost() sets ok_=false at run time (sensors.h mark_lost, called from note_mag_sample flight.cpp:2325). gen_mag sets MAG_F_OB/IB from mstat_*.count() != 0 but builds the vector with mag_vec(mstat_ob_, mag_ob_.ok()) (flight.cpp:1764-1769), and mag_vec returns the sentinels when !ok (flight.cpp:249-251). Samples added before the loss are valid: finish_mag_unit adds only ok samples (flight.cpp:2340-2345). So a unit lost inside a MAG period gives OB=1 and nsamples>0 with ob_bx..rms = n/a. rm3100_cycle_count switches to mag_ib_.cycle_count() (flight.cpp:1771). For an unfitted ib that is its default cc_=200 (sensors.h), not the commanded mag_cc_.

**Fix notes.** Use p.outboard = mag_vec(mstat_ob_, true) and p.inboard = mag_vec(mstat_ib_, true). count()==0 already yields the sentinels, and the ok guard dates from 3.0, when ok() was constant. Report rm3100_cycle_count = mag_cc_ (the cycle count in force).

## FL-06 [CONFIRMED / minor] An ARU restart is not flagged as a gap, contrary to the comment and to what the host test claims
`vgq1-spacecraft/sketch/src/fsw/attitude.cpp:309`

**Description.** attitude.cpp:314-315 says that a counter moving backwards (an ARU restart) shows as a gap. A rebooted Nicla serves page 2 with win_seq 0, n 0 and flags b5|b6 only for about 1 s before its first window (Wire.begin comes after the 1 s setup delay, and g_win_t0 starts there). The special case at line 309 takes that page silently (seen_ = true, last_win_ = 0), and the next window (win_seq 1) is consecutive. The windows lost during the reboot never set IMU_F_GAP. test_sensor_bus.cpp:143 ("a jump (ARU restart) is a gap") tests 0 -> 200, which is not the sequence a restart produces.

**Failure scenario.** I checked this with a host program against attitude.cpp: add(99), add(100), [Nicla power-cycles, reads fail about 2 s], add(win_seq 0, n 0, flags 0x60), add(1). Result: gap()=0, windows()=3. The ATT for that period shows no GAP although 3 or more windows are missing.

**Proposed fix.** In the win_seq 0 / n 0 branch set gap_ = true when seen_ && last_win_ != 0 (a counter that went back to the pre-first-window state). Change the host test to feed the real restart sequence (..., 100, {0, n 0, flags 0x60}, 1) and expect gap().

**Verifier.** The ARU firmware starts the I2C target (Wire.begin) right before g_win_t0 = millis() (nicla_sense_me_aru.ino:339-342). For the first ~1 s after a restart, page 2 therefore carries win_seq 0, n 0, flags GRV_PRESENT|ACC_RANGE (0x60/0x61, no STATS/MISSED). ImuStats::add takes that through the special case (attitude.cpp:184-188) without a gap and sets last_win_=0, so the next window 1 is consecutive. I built a host program against the real attitude.cpp (verify-FL/fl06.cpp): add(99), add(100), add({0,n0,0x60}), add(1) gives gap=0, windows=3. Only when the first post-restart read is already window 1 does gap become 1. The comment at attitude.cpp:189-190 ('an ARU restart also shows as a gap') and the host test 'a jump (ARU restart) is a gap' (test_sensor_bus.cpp, 0 -> 200) do not match the real restart sequence. Practical impact is low while FL-03 keeps GAP almost always set.

**Fix notes.** In the win_seq 0 / n 0 branch, add 'if (seen_ && last_win_ != 0) gap_ = true;' before last_win_ = 0. Make the host test feed ..., 100, {0, n 0, flags 0x60}, 1 and expect gap(). Keep the first-boot case (seen_ false) gap-free.

## FL-07 [CONFIRMED / minor] New boot-time 0x060B/0x060D EVRs fill the VC0 routine budget, so later boot INFO EVRs (BOOT, BIST, station ID) are refused on the real-time downlink
`vgq1-spacecraft/sketch/src/fsw/flight.cpp:536`

**Description.** All boot EVRs are queued on VC0 during setup(), with nothing transmitted until loop() starts. A non-priority packet is refused once backlog + len > 1024 - 256 = 768 octets (datalink.cpp:66); a priority WARN can use the reserve but still adds to the backlog. 3.1 adds one 0x060B per instrument at boot (~60 octets each) and, for a wrong-bus or not-separated wiring, two WARN 0x060D per RM3100 (~85-88 octets each). My estimate for a normal boot with both RM3100s on Qwiic is ~760 of the 768 octets.

**Failure scenario.** Nicla plus RM3100 ob and ib all on A4/A5. That is the wiring docs/03 still describes, and the boot emits 2 x (0x060B + two 0x060D). Cumulative VC0 is about 756 octets after the sensor EVRs, so the INFO BIST results 0x0910/0x0911, "BOOT VGQ-1 FSW 3.1 rst=...", 0x0003, 0x0004 and "DE <callsign>" are all refused in real time (Downlink overflows counted) and exist only in the SSR. With a single wrong-bus RM3100, 0x0004 and the station ID are lost.

**Proposed fix.** Emit the BOOT EVR before init_sensors(), or send the setup-time EVRs with priority=true (or exempt in_setup_ from the routine reserve). Alternatively, fold the per-instrument acquisition into one boot EVR (e.g. "buses: Nicla A4/A5, ob Qwiic, ib Qwiic, AS7265X Qwiic") that the ground parses.

**Verifier.** Nothing is transmitted during setup(). evr() pushes INFO EVRs as non-priority (flight.cpp:1556), and Downlink::push refuses them once backlog + len > 1024 - 256 (datalink.cpp:66-75). WARNs use the reserve but still add to the backlog. in_setup_ exempts only the EvrGate, not the VC limit (flight.cpp:1553). An EVR packet is 15 + text octets (pack_evr, 6+6 headers). I summed the literal boot formats in order (verify-FL/fl07.py): 0x0308, 0x0306, the 0x060B/0x060D sensor reports, 0x0910/0x0911, 0x0706, MODE 0x0011, BOOT 0x0001, 0x0003, 0x0004, 0x0002. Results: (a) Nicla, ob and ib all on A4/A5 (the layout docs/03 still shows, lines 71/89/133): 687 octets after the sensor EVRs. 0x0911, 0x0706, MODE, BOOT, 0x0003, 0x0004 and 'DE N0CALL' are refused in real time. (b) One RM3100 on A4/A5: 0x0003, 0x0004 and DE are refused. (c) Even the normal 3.1 boot with ob+ib on Qwiic comes to ~758-782 octets, so 0x0004 (strap fitted) or the station-ID EVR (no strap) is refused, which is slightly worse than the reviewer's ~760. The 3.0 equivalent without the 0x060B reports came to ~615. All are still in the SSR and the EGSE log. BOOT 0x0001 is not used by ground logic (dictionary.py:162 only), and the callsign is also sent every 600 s and in the idle frames, so the impact is minor.

**Fix notes.** Emit BOOT/0x0003/0x0004/0x0002 right after init_radio(), before init_sensors(). In report_acquired, send either 0x060B (preferred bus) or the wrong-bus 0x060D, not both: the ground already reads 0x060D as an acquisition (dictionary.evr_instrument_bus). Optionally let setup-time EVRs use the priority reserve (in_setup_ -> priority). Re-check the worst case (all on A4/A5) against 1024.

## GS-3 [CONFIRMED / minor] docs/15 contradicts the 3.1 configuration defaults (fault_log, archive_max_gb, key count) and leaves out --mode2-confirmed
`docs/15_Configuration_Reference.md:277`

**Description.** docs/15:277 gives fault_log default 'false'. config.merge() / fault_log_default() (config.py:143-163) now make an absent fault_log true for model = "E22-400T37S", and station.toml no longer contains the key. docs/15:410 gives archive_max_gb 16.0; config.py:107 and station.toml now use 8.0 (D-80). docs/15:424 says '92 in the shipped file'; the shipped file now holds 91 keys (82 + 8 radio.sx126x before; fault_log removed, still_rate_dps and still_vib_mg added), and fault_log is now an optional key outside the file. The new configuration-tool option --mode2-confirmed (radio/e22.py:439) is not documented anywhere. docs/00:649, docs/03:587 and docs/16:507 still show the tool command without it.

**Failure scenario.** An operator configuring a T37S reads docs/15, believes the abnormal-status log is off unless set, and writes 'fault_log = false', or expects no FF FF FF 0x reports. An operator sizing the archive believes the cap is 16 GB on a 32 GB eMMC. A scripted (non-TTY) run of the documented e22 command now exits 'not confirmed: nothing sent' with no documented way past it.

**Proposed fix.** docs/15:277: default 'absent: true for model = "E22-400T37S", false otherwise (D-77)'. docs/15:410: 8.0 (D-80). docs/15:424: '91 in the shipped file plus the optional field_ut, min_rate and fault_log'. Add --mode2-confirmed to docs/15 section 6 and to the e22 command lines in docs/00, 03, 14 and 16.

**Verifier.** docs/15:277 gives fault_log default 'false'. config.merge() (config.py:143-145) applies fault_log_default(), which returns True for model = "E22-400T37S" when the key is absent. The shipped station.toml no longer sets it. docs/15:410 gives archive_max_gb 16.0; config.py:107 has 8.0 (D-80). The same stale 16 GB also appears at docs/07:683, docs/18:277, docs/20:203 (L-42) and dssq-groundstation/README.md:124. Key count, measured with tomllib (verify-GS/countkeys.py): the shipped file held 90 keys at 13a60c5 and holds 91 at 98887f5. fault_log was removed; still_rate_dps and still_vib_mg were added. The diff changed docs/15:424 from '90 in the shipped file' to '92', counting the +2 but not the -1. --mode2-confirmed exists only in radio/e22.py:439 and tests/test_radio.py; grep finds it in no doc. The documented commands (docs/00:649, docs/03:587, docs/15:35, docs/16:507) still work interactively: on a TTY confirm_mode2() prompts for 'mode2'. Without a TTY the tool exits 'not confirmed: nothing sent' (e22.py:452-454).

**Fix notes.** docs/15:277: default 'absent = true for model = "E22-400T37S", false otherwise (D-77); set false only to switch the T37S log off'. docs/15:410 and the copies in docs/07, 18, 20 and README: 8.0 (D-80). docs/15:424: '93 keys (91 in the shipped file plus the optional [science] field_ut, [linkmgr] min_rate and [radio] fault_log)'. Add --mode2-confirmed and the interactive 'mode2' prompt to docs/15 section 6 and to the e22 tool command lines in docs/00, 03, 15:35 and 16. A docs drift test comparing the docs/15 default column with config.DEFAULTS would catch this class of drift.

## GS-4 [CONFIRMED / minor] Ground App README (packaged into dssq-groundstation-unoq.zip) still describes the VENTUNO Q build and old defaults
`dssq-groundstation/README.md:28`

**Description.** tools/package_apps.py packages this README into the zip; verified by building into scratch: 6 'VENTUNO' occurrences in the packaged copy. Line 3: 'for the Arduino VENTUNO Q'. Lines 8 and 21: 'STM32H5F5', 'FQBN arduino:zephyr:ventunoq' (sketch.yaml is now arduino:zephyr:unoq). Line 22: 'the stock release does not compile for the VENTUNO Q' (that hunk was removed, D-79). Line 28: 'on the VENTUNO Q, A0 drives the buzzer, so never put a jumper there' (now A1 drives it and A0 is free), and it presents the RCU as the default although [gds] rcu = false. Line 36: 'Download dssq-groundstation-ventunoq.zip', a file the packager no longer produces. Lines 37, 47, 62, 81, 92 and 171: VENTUNO Q addresses and steps. Line 112: '64 GB eMMC'. Line 124: 'archive_max_gb, 16' (code: 8). Line 168: 'Ubuntu 24.04' (Debian). Line 205: 'arduino:zephyr:ventunoq'. vgq1-spacecraft/README.md:3, 26, 46, 96, 99 and 116 also still name the VENTUNO Q as the ground board.

**Failure scenario.** A user follows the README shipped inside the 3.1 zip. They look for dssq-groundstation-ventunoq.zip (it does not exist), believe the buzzer is on A0 and the retention cap is 16 GB, and run host-mode steps written for Ubuntu on the Debian board.

**Proposed fix.** Rewrite the listed lines for the UNO Q 4 GB (ABX00173) with the USB-C hub: STM32U585, arduino:zephyr:unoq, dssq-groundstation-unoq.zip, buzzer A1 and A0 free, RCU optional (rcu = false default), 32 GB eMMC, archive_max_gb 8, Debian, <ground-uno-q-address>. Apply the same to vgq1-spacecraft/README.md, then rebuild the zips.

**Verifier.** package_apps.py:157 COMMON_REQUIRED includes README.md, so dssq-groundstation/README.md goes into dssq-groundstation-unoq.zip (package_apps.py:161). The README at 98887f5 contains: line 3 'for the Arduino VENTUNO Q'; 8 and 21 'STM32H5F5' and 'FQBN arduino:zephyr:ventunoq' (sketch.yaml:27 is arduino:zephyr:unoq); 22 'the stock release does not compile for the VENTUNO Q' (that hunk was removed, D-79); 28 'on the VENTUNO Q, A0 drives the buzzer' (now A1; A0 free), which also presents the RCU as the default although [gds] rcu = false; 36 'Download dssq-groundstation-ventunoq.zip' (the packager makes -unoq.zip); 37, 47, 62, 81, 92 and 171 VENTUNO addresses and steps; 112 '64 GB eMMC' (contract: 32 GB); 124 'archive_max_gb, 16' (code 8.0); 168 'Ubuntu 24.04' (contract: Debian); 205 'arduino:zephyr:ventunoq'. vgq1-spacecraft/README.md:26, 46, 96, 99 and 116 still name the VENTUNO Q as the ground board. Contract section 0 says 'VENTUNO Q disappears from all current code, UI, zips, instructions', and section 8 requires a grep for it.

**Fix notes.** Rewrite the listed README lines for the UNO Q 4 GB (ABX00173) with the USB-C hub: STM32U585; FQBN arduino:zephyr:unoq; dssq-groundstation-unoq.zip; buzzer A1, A0 free; RCU optional ([gds] rcu = false by default, E15-USB-T2 jumper caps); 32 GB eMMC; archive_max_gb 8; Debian; <ground-uno-q-address>; drop the 'does not compile for the VENTUNO Q' RadioLib remark. Do the same in vgq1-spacecraft/README.md, then rebuild the zips and SHA256SUMS. Add a packaging test that fails on 'VENTUNO' in any zipped file outside labelled history.

## GS-5 [CONFIRMED / minor] LED-matrix frame-sync row shows LOCK during LOS
`dssq-groundstation/python/dssq/gds.py:2268`

**Description.** _matrix_status() sends self.sync.state unconditionally. FrameSynchronizer changes state only when octets arrive (framesync.py _step). After the last CADU of a pass it stays LOCK (or FLYWHEEL) indefinitely; checked: state 'LOCK' after 3 CADUs and still 'LOCK' after a later feed with no data. The M line keeps sending 'L', so row 1 (frame sync LOCK) stays fully lit during LOS. Every other link row is gated on reception within the LOS timeout.

**Failure scenario.** The pass ends and the LOS timeout expires. Row 0 (AOS) goes dark and rows 3-5 blank, but row 1 stays lit as 'frame sync LOCK' for hours until the next pass. The matrix contradicts itself and reports a lock with no signal.

**Proposed fix.** In _matrix_status, send sync None or 'SEARCH' when self.rx.los is true (or when no frame arrived within los_timeout_s), e.g. "sync": "PACKET" if packet_mode else (None if rx.los else self.sync.state).

**Verifier.** gds.py:2268 sends 'sync': self.sync.state with no LOS gating. FrameSynchronizer (framesync.py:81-98) leaves LOCK only through _lose_lock(): a stale partial unit, or more than 3 misses at the expected position. After complete CADUs the buffer is empty, so feed(b'') changes nothing. Noise replies and fault reports are consumed before the state logic (_step 136-139). gds.py never resets self.sync (its only uses are 421, 703-705, 1285, 2268, 2442). Reproduced (verify-GS/los_sync.py, a real GroundStation): after 3 CADUs and about 3000 s of empty feeds, rx.los = True and sync.state = 'LOCK'. _matrix_status gave rssi_recent False and margin None but sync 'LOCK', and matrix_line() produced 'M|L|0|-|?|0|?'. In the sketch, row 1 is lit for kMxLock regardless of g_aos (sketch.ino:1489), so during LOS the matrix shows AOS dark and 'frame sync LOCK' fully lit. The JSON snapshot sync.state (gds.py:2442) has the same stale LOCK.

**Fix notes.** In _matrix_status, gate on reception: "sync": "PACKET" if packet_mode else ("SEARCH" if rx.los or not recent else self.sync.state). Sending 'SEARCH' rather than None keeps the field 'known: no lock'. Optionally apply the same gating to the snapshot's sync.state, or call self.sync._lose_lock() on the AOS->LOS transition (that also counts a lock loss, so prefer display gating). Add a test: CADUs, then a time past the LOS timeout, then matrix_line() must not carry 'L'.

## GS-6 [CONFIRMED / minor] Radio panels say "[gds] rcu = false" when rcu is true but the RCU path is unavailable
`dssq-groundstation/python/dssq/gds.py:2287`

**Description.** _register_check() returns 'not read back: no Radio Control Unit ([gds] rcu = false) ...' whenever radio_mgr or radio_mgr.rcu is None. The same false attribution appears in dashboard.html:1541 ('off ([gds] rcu = false): ...') and 1505 ('needs the Radio Control Unit, [gds] rcu = false') whenever radio.rcu is false. With [gds] rcu = true that also happens when the Router Bridge is unavailable (gds.py:2609-2617, link None, so no _Rcu) or the E22 serial port did not open (radio None, so mgr None, gds.py:2645-2650).

**Failure scenario.** Checked: cfg['gds']['rcu'] = True, then attach_radio(None, None, 'serial port: ENOENT'). The snapshot gives radio.rcu False and register_check 'not read back: no Radio Control Unit ([gds] rcu = false) ...', while station_io.rcu_configured is True. The operator is sent to station.toml, which is already correct, instead of to the missing bridge package or USB-UART.

**Proposed fix.** Branch on cfg['gds']['rcu'] when there is no rcu path: rcu = false keeps the current text; rcu = true reports 'configured ([gds] rcu = true) but not available: no Router Bridge / radio serial port not open'. Mirror this in dashboard.html 1505 and 1541 (e.g. from station_io.rcu_configured) and in docs/13:1092-1093.

**Verifier.** gds.py:2287-2289 returns 'not read back: no Radio Control Unit ([gds] rcu = false) ...' whenever radio_mgr or radio_mgr.rcu is None, without looking at cfg['gds']['rcu']. With rcu = true, Runner (2607-2650) leaves mgr None when the Router Bridge is unavailable (link None, so rcu None, so no RadioManager for an E22, 2645-2650) or when _open_e22() fails (radio None). Reproduced (verify-GS/rcu_word.py): with cfg gds.rcu = True and attach_radio(None, None, 'serial port: ENOENT'), the snapshot gives radio.rcu False, register_check '... ([gds] rcu = false) ...' and station_io.rcu_configured True. dashboard.html:1541 ('off ([gds] rcu = false): ...') and 1505 ('needs the Radio Control Unit, [gds] rcu = false') key only on R.rcu, so they make the same wrong attribution. The wording is new in 3.1; at 13a60c5 the row was a plain 'off'. Mitigation: the same panel shows 'Backend ... UNAVAILABLE' with the error badge, and an ERROR RADIO event is logged, so the real cause is visible nearby. The specific text still sends the operator to station.toml, which is already correct.

**Fix notes.** In _register_check, when rm or rm.rcu is None: if not cfg['gds']['rcu'], keep the current text; otherwise return 'not read back: [gds] rcu = true but the Radio Control Unit is not available (' + (self.radio_unavailable or 'no Router Bridge') + ')'. In dashboard.html 1505 and 1541, choose the wording from S.station_io.rcu_configured: 'configured but not available' versus 'off ([gds] rcu = false)'. Update docs/13 to match, and add a test_web case with rcu = true and no backend.

## ICD-2 [CONFIRMED / minor] ImuStats only resets in gen_att(): the first ATT after SAFE/BOOT aggregates every window since boot and reports imu_nwin = 255 (saturated)
`vgq1-spacecraft/sketch/src/fsw/flight.cpp:1862`

**Description.** imu_stats_.add() runs at every 1 Hz sample in every mode (start_mag_sample), but imu_stats_.reset() is only called in gen_att() (flight.cpp:1869). ATT is off in BOOT and SAFE (kModeCadence datalink.cpp:277-278), and the spacecraft boots into SAFE and waits for the ground. ImuStats::windows() saturates at 255 (attitude.h:104) while n_ keeps counting, so the first ATT after the operator commands CRUISE/TEST has these values over the whole SAFE time (hours): vib_rms (RMS of all windows), the peaks (max over hours) and rate_mean. imu_nwin is 255 with no saturation marker. The ground presents these as 'window statistics over the ATT period' (docs/13 9.4; dashboard.html:2074 'Windows aggregated 255'), and gds.ImuHistory plots them as one point at that ATT's receive time. The motion state is 'moving' if anything moved at any time in SAFE.

**Failure scenario.** Boot, 2 h in SAFE (about 7200 windows read), then CRUISE. The first ATT (generated at once because last_gen_s_ is 0) has imu_nwin=255 instead of 60, rate_peak and vib_peak equal to the largest values seen during the 2 h, and rate_mean equal to the 2 h mean. The SCIENCE table shows 'Windows aggregated 255' and possibly 'moving'. The IMU chart point at that time stands for 2 h, not one ATT period.

**Proposed fix.** Reset imu_stats_ whenever ATT is not being generated: in tick_1hz, when the ATT period in force is 0, call imu_stats_.reset() (it keeps last_win_). Or cap the aggregation to the nominal ATT period, e.g. reset at set_mode(). On the ground, render imu_nwin == 255 as '>= 255 (saturated)'.

**Verifier.** imu_stats_.add() runs at every 1 Hz sample in every mode (flight.cpp:2290, via tick_1hz sample_due_ at :1580). imu_stats_.reset() is called only in gen_att() (flight.cpp:1869). ATT is off in BOOT and SAFE (datalink.cpp:277-278), and the spacecraft boots into SAFE (flight.cpp:319). set_mode() zeroes last_gen_s_ (flight.cpp:1152), so the first ATT comes at once and holds everything since boot. windows() saturates at 255 while n_ keeps counting (attitude.h:104). Host check built out of tree (/tmp/claude-0/-home-user/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/review31/verify-ICD/imu.cpp): 7200 SAFE windows with one shake give nwin=255, vib_peak=2000, rate_peak=900, which is the hour-1 shake in the first ATT. The simulator does the same (first ATT after 100 s of SAFE has imu_nwin 100). Downgraded from major: only one packet after each SAFE/BOOT -> ATT transition is affected (or after an ATT period override 0xFFFF is lifted), and the attitude.h comment does define the period as 'the time since the previous ATT packet'. Still, the saturated 255 has no marker, and docs/13:2696 ('1 s windows in the ATT period') and the IMU chart misrepresent that point.

**Fix notes.** Flight: in tick_1hz, after periods_in_force(per), call imu_stats_.reset() whenever per[PER_ATT] == 0 (reset() keeps last_win_/seen_, so no false gap). Add a host test: SAFE for N s, then CRUISE, and the first ATT has imu_nwin <= 1. Ground (optional): show imu_nwin == 255 as '>= 255 (saturated)'. Mirror the change in sim._imu_stats (advance imu_win_end while ATT is off).

## ICD-4 [CONFIRMED / minor] IMU_F_ cross-check only checks bit contiguity, not that flight names map to IMU_FLAG_BITS positions
`tests/test_cross_check.py:204`

**Description.** The loop takes only the sorted VALUES of the IMU_F_ enum and asserts that they are [1<<i for i in range(len(names))]. The names are never compared, so the flight ImuFlag order is not checked against dictionary.IMU_FLAG_BITS. Contrast SENSOR_BITS/FDIR_BITS two lines above, which compare names sorted by value. The integration vector (imu_flags == 0x67) and test_bit_and_bist_decoders check numbers or the dictionary alone, so a swapped pair (e.g. IMU_F_PAIRED = 0x02 and IMU_F_STATS = 0x04 in tlm_packets.h) passes every test while the dashboard labels the bits wrongly. The same gap exists for the MAG_F_/ATT_F_/ENV_F_/RF_F_ entries the new one was added to.

**Failure scenario.** Edit tlm_packets.h: IMU_F_STATS = 0x04, IMU_F_PAIRED = 0x02. test_flight_header_constants_match_dictionary still passes. On the ground, a period with windows but no pairing shows the badge 'PAIRED' and 'imu_nwin 0' handling uses the wrong bit.

**Proposed fix.** For each prefix: e = _enum(tlm, rf"\b{prefix}(\w+)\s*=\s*(0x[0-9A-Fa-f]+)"); assert [n for n, _ in sorted(e.items(), key=lambda kv: kv[1])] == names and sorted(e.values()) == [1 << i for i in range(len(names))].

**Verifier.** tests/test_cross_check.py:202-205 compares only sorted(values) == [1<<i ...] for MAG_F_/ATT_F_/IMU_F_/ENV_F_/RF_F_ and never the names. Reproduced in /tmp/claude-0/-home-user/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/review31/verify-ICD/icd4.py: with IMU_F_STATS=0x04 and IMU_F_PAIRED=0x02 in tlm_packets.h the current check still passes. The integration vector 0x67 (test_cross_check.py:405, test_integration.cpp:241) also passes for this swap, because both bits are set in the vector. The proposed fix is wrong as written: flight and dictionary names differ for two prefixes (ATT_F_: ACC/MAG vs ACCEL/BMM150; ENV_F_: PRESSURE/TH vs BARO/TEMP_RH), so a plain name comparison would fail. Names match for IMU_F_, MAG_F_ and RF_F_.

**Fix notes.** Per prefix, build e = _enum(...) and assert both sorted(e.values()) == [1<<i ...] and [alias.get(n, n) for n, _ in sorted(e.items(), key=lambda kv: kv[1])] == names. Use an explicit alias map {'ATT_F_': {'ACC': 'ACCEL', 'MAG': 'BMM150'}, 'ENV_F_': {'PRESSURE': 'BARO', 'TH': 'TEMP_RH'}} (or rename one side so the names match).

## ICD-5 [CONFIRMED / minor] ImuStats: an ARU restart is not flagged as IMU_F_GAP when the pre-first-window page is read
`vgq1-spacecraft/sketch/src/fsw/attitude.cpp:184`

**Description.** The guard for 'the ARU before its first window' (win_seq 0, n 0, no STATS/MISSED) sets last_win_ = 0 and returns before the gap check, whether or not a window was already seen. After a Nicla reset (watchdog, brownout, re-plug) the flight reads a page with win_seq 0 / n 0 within the first second. The next window, win_seq 1, is then 'consecutive'. The seconds of the outage are never marked as a gap, contrary to the header comment ('a counter that moved backwards - an ARU restart - also shows as a gap'). Related doc slip: nicla_sense_me_aru.ino:58 and attitude.h:75 say 'win_seq 0 = none completed yet', but the counter wraps 255 -> 0 every 256 s.

**Failure scenario.** Host check (compiled into the scratch directory): add windows 130..137, reset(), then add(win_seq 0, n 0, flags 0) and add(win_seq 1, n 25, STATS). Result: gap()=0, windows()=1. The ATT says no windows were missed although the ARU was down for several seconds.

**Proposed fix.** In the guard, set gap_ = true when seen_ && last_win_ != 0 (the counter went back to the pre-window state), or keep the gap check before the guard. Fix the '0 = none completed yet' comments to say that win_seq 0 also follows 255.

**Verifier.** attitude.cpp:184-188: the pre-first-window guard sets seen_=true and last_win_=0 and returns before the gap check at :191. The ARU firmware starts its I2C target only after setup() (BHY2.begin + delay(1000), nicla_sense_me_aru.ino:317-342). Its first loop pass then publishes page 2 with g_win.seq 0, n 0 and no STATS/MISSED for the first 1 s, so the flight's 1 Hz read almost always sees that page after a Nicla reset. Host check (/tmp/claude-0/-home-user/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/review31/verify-ICD/imu.cpp): windows 130..137, reset(), add(0,0,0), then add(1,25,STATS) gives gap=0, windows=1. If the restart is first seen at win 3 instead, gap=1. A restart of 2-4 s stays under the >5 failed reads needed for 0x0606, so the outage then shows in neither HK, EVR nor ATT, contrary to the header comment at attitude.cpp:189-190. The wrap 255->0 is handled correctly (gap=0, 4 windows), but the '0 = none completed yet' comments (nicla_sense_me_aru.ino:58, attitude.h:75) are inaccurate because win_seq wraps to 0.

**Fix notes.** In the guard: `if (seen_ && last_win_ != 0) gap_ = true;` before setting last_win_ = 0. This flags a counter that went back to the pre-window state; the boot case, where seen_ is false or last_win_ is already 0, is unchanged. Add the host case above to test_sensor_bus.cpp or its equivalent. Reword the comments to say that win_seq 0 also follows 255; only 0 with n 0 and no STATS/MISSED means no window yet.

## ICD-6 [CONFIRMED / minor] IMU_F_PAIRED also requires a valid 9-DOF quaternion, so the ground's linear acceleration is withheld with a false reason
`vgq1-spacecraft/sketch/src/fsw/flight.cpp:1857`

**Description.** The flight sets IMU_F_PAIRED only when page-2 seq == page-0 seq AND aru_last_.motion_flags b0 (rotation vector valid). The ground gates the linear acceleration (acc - 1 g x u, which needs only q_grv and the page-0 accelerometer) on PAIRED. When q_fus is invalid but acc and q_grv come from the same rebuild, sensors.py:224 reports 'IMU_F_PAIRED clear: acc and q_grv not from one ARU rebuild', which is false. The tlm_packets.h comment and docs/05 define PAIRED purely as 'same ARU rebuild'.

**Failure scenario.** The 9-DOF rotation vector goes stale or fails the norm gate (ATT b0 QUAT clear) while GRV and the accelerometer are valid and paired by seq. The SCIENCE row shows 'n/a (IMU_F_PAIRED clear: acc and q_grv not from one ARU rebuild)' although they are from one rebuild, and the linear acceleration is not shown.

**Proposed fix.** Set IMU_F_PAIRED from aru_imu_paired_ && aru_valid_ alone; the ground already checks ATT b0 QUAT separately for the heading offset (sensors.py:226) and b1 ACCEL for the linear acceleration.

**Verifier.** flight.cpp:1857 sets IMU_F_PAIRED only when aru_imu_paired_ && aru_valid_ && (aru_last_.motion_flags & 1u). ATT_F_QUAT comes from the same motion_flags b0 (flight.cpp:1817), so PAIRED implies QUAT. Two results follow. First, the ground branch sensors.py:229 ('ATT b0 QUAT clear: no valid 9-DOF rotation vector' under PAIRED) can never be reached with real flight data. Second, when the rotation vector is stale or fails the norm gate while GRV and the accelerometer come from the same rebuild, the ground shows 'IMU_F_PAIRED clear: acc and q_grv not from one ARU rebuild' (sensors.py:223-224), which is false, and withholds the linear acceleration. tlm_packets.h:135 defines PAIRED as 'page 2 seq = page 0 seq', and docs/13:2717 treats PAIRED and QUAT as independent conditions. The case is uncommon (RV invalid with GRV valid), hence minor.

**Fix notes.** flight.cpp:1857 -> `if (aru_imu_paired_ && aru_valid_) p.imu_flags |= IMU_F_PAIRED;` (inside the existing ARU_IMU_GRV branch). The ground already gates the heading offset on ATT b0 QUAT and the linear acceleration on b1 ACCEL. Add an integration or unit vector with QUAT clear and PAIRED set.

## ICD-9 [CONFIRMED / minor] Station Bridge ICD (docs/04 9.7, docs/06 5) still shows the 3-field hello and no M line, under a 'VENTUNO Q' heading
`docs/04_Space_Data_Link_ICD.md:901`

**Description.** The code sends 'H|<fw>|<rcu -1/0/1>|<kb>|<rcu age s>' (sketch.ino:1704; frontpanel.parse_hello). The rcu field is the result of the last rcu_mode() call, not 'RCU present'. The GDS also sends 'M|sync|tx|rssi|margin|lost|clock' on fp_line. docs/04:897-901 documents fp_line as S/Q only and the hello as "H|<fw>|<rcu>|<kb>"; docs/06:204 says "H|<fw>|<rcu 0/1>|<kb 0/1>" = 'RCU present'. Both sections still say VENTUNO Q. These files are outside the three named docs but are the interface definition of the station link (the docs phase may still be pending).

**Failure scenario.** Someone writes a replacement station sketch or a test tool from docs/04. They send a 3-field hello (accepted, but the RCU state stays 'unknown') and do not implement the M line, so the LED matrix stays blank.

**Proposed fix.** Update docs/04 9.7 and docs/06 5 to the 4-field hello (rcu -1 never / 0 failed / 1 OK, age in s, -1 never) and to the M line with its field grammar (L/F/S/P, 0/1, dBm | '-' | 'O', [N]dB, lost, 0/1, '?' = unknown), and rename the headings to the ground UNO Q.

**Verifier.** sketch.ino:1703 sends 'H|%s|%d|%d|%ld' (fw, rcu -1/0/1 = last rcu_mode() result, kb, rcu age s). frontpanel.parse_hello (frontpanel.py:190-205) returns rcu_last None for a 3-field hello, and frontpanel.py:187 sends 'M|sync|tx|rssi|margin|lost|clock' on fp_line. docs/04:891-901 (section '9.7 VENTUNO Q') documents fp_line as S/Q only and the hello as "H|<fw>|<rcu>|<kb>". docs/06:192-204 ('Both run on the VENTUNO Q') gives 'H|<fw>|<rcu 0/1>|<kb 0/1>' with rcu = 'RCU present' and has no M line. A tool written from these docs would leave the RCU state unknown and the LED matrix blank. The docs phase is still pending (contract section 7), but at 98887f5 these sections contradict the code.

**Fix notes.** In the docs phase, update docs/04 9.7 and docs/06 5 to the 4-field hello (rcu -1 never / 0 failed / 1 OK; age s, -1 never; older 3-field hello = state unknown) and add the M line grammar from frontpanel.py (sync L/F/S/P, tx 0/1, rssi dBm | '-' | 'O', margin [N]dB, lost count, clock 0/1, '?' unknown, LINE_MAX). Rename the headings to the ground UNO Q. Consider a docs test that compares the documented hello field count with sketch.ino.

## LS-1 [CONFIRMED / minor] Signal power, C/N0 and Eb/N0 still get numbers at NOISE_FLOOR, where D-76 says the signal cannot be measured
`dssq-groundstation/python/dssq/rxstats.py:880`

**Description.** snapshot() computes pr = signal_power_dbm(rssi, noise_pair) at line 880 without checking the margin state. The E22 C/N0 (line 887) and Eb/N0 (line 890) are built on pr. margin_assessment() at line 893 rules that for R - N < 3 dB the signal is below the noise and nothing derived from the RSSI is measurable. Even so, rx.rf.pr_est_dbm, cn0_est_dbhz and ebn0_est_db get a number whenever 0 < R - N < 3 dB. The RECEIVER 'RF now' rows (dashboard.html RF_KEYS 1330-1332) show them, and /metrics exports them as dssq_rf_pr_est_dbm, dssq_rf_cn0_est_dbhz and dssq_rf_ebn0_est_db. With the E22's 1 dB steps these values are quantisation artefacts that look like a strong link.

**Failure scenario.** E22 at the edge of range (the project's design case). Noise register -118 dBm, packet RSSI -117 dBm. I reproduced this in review31/LS/noisefloor.py: margin_state NOISE_FLOOR and margin_worst_est_db None, as intended. But pr_est_dbm = -123.9 dBm, cn0_est_dbhz = 45.1 dB-Hz and ebn0_est_db = +11.3 dB are shown and exported. With R - N = 2 dB, Eb/N0 reads +14.8 dB. The LoRa limit is about 6 dB, so an operator reads a healthy link while the margin row says 'not measurable'.

**Proposed fix.** Compute ma first. Then set pr = signal_power_dbm(rssi, noise_pair) if ma['state'] in ('SIGNAL', 'NEAR_NOISE') else None, so the E22 cn0 and ebn0 become None too. In docs/13 4.2, change the 'Signal power est.' card to say '—' when RSSI - noise < 3 dB (NOISE_FLOOR).

**Verifier.** Reproduced with /tmp/claude-0/-home-user/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/review31/verify-LS/noisefloor.py (E22, noise -118, RSSI -117). Result: margin_state NOISE_FLOOR, margin_worst_est_db None, but pr_est_dbm -123.87, cn0_est_dbhz 45.10 and ebn0_est_db 11.30. /metrics exports dssq_rf_pr_est_dbm, dssq_rf_cn0_est_dbhz and dssq_rf_ebn0_est_db with those values. Cause: rxstats.py:880 calls signal_power_dbm(rssi, noise_pair) before and without looking at margin_assessment() (893). E22 C/N0 (887) and Eb/N0 (890) are built on that pr. This contradicts the rule's own docstring (rxstats.py:60: at NOISE_FLOOR 'S < N, not measurable') and the contract section 5 rule (anything not measurable renders n/a, never a number). It also contradicts docs/13 4.2, which calls P_r 'undefined for LoRa packets received below the noise floor' and quotes a 6.0-6.3 dB Eb/N0 limit. With 1 dB E22 steps, R-N can read only 1 or 2 dB here, giving Eb/N0 11.3 or 14.8 dB. Those are quantisation artefacts. Why minor and not major: only uncoloured 'est.' diagnostic rows and gauges carry the numbers, and nothing acts on them (grep: no other consumer in dssq). The header MARGIN, the tile, the margin rows, the budget and the link manager all apply the rule correctly, and the NOISE FLOOR state row sits in the same table.

**Fix notes.** In snapshot(), compute ma first (move line 893 above 880). Then set pr = signal_power_dbm(rssi, noise_pair) if ma['state'] in ('SIGNAL', 'NEAR_NOISE') else None. The E22 cn0 and ebn0 then become None automatically. The SX126x cn0, built from the measured SNR, stays. In docs/13 4.2 (Signal power est., C/N0, Eb/N0), change the condition to: '—' when RSSI - noise < 3 dB (NOISE_FLOOR) or a value is missing. Add a test in tests/test_link_legitimacy.py that asserts pr/cn0/ebn0 are None at R-N = 1 dB on the E22.

## LS-11 [CONFIRMED / minor] Recommended margin alert never fires at the noise floor or during LOS
`docs/13_Web_UI_Reference.md:3337`

**Description.** Section 16 recommends the alert 'dssq_rf_margin_worst_est_db < 3'. At NOISE_FLOOR margin_worst_est_db is None, and during LOS it is in LIVE_RF_KEYS, so the gauge is absent either way. A Prometheus comparison on an absent series does not fire. No numeric gauge for margin_state exists.

**Failure scenario.** The link fades into the noise: the margin goes from 4 dB to 'not measurable'. The gauge disappears and the recommended alert clears instead of firing, at the moment the link is most at risk.

**Proposed fix.** Export a numeric margin state (for example dssq_rf_margin_state_code). Document an alert such as 'dssq_rf_margin_worst_est_db < 3 or dssq_rf_margin_worst_max_db < 3 or absent(dssq_rf_margin_worst_est_db) and dssq_los == 0'.

**Verifier.** docs/13:3337 recommends the alert 'dssq_rf_margin_worst_est_db < 3'. verify-LS/alert_sx.py shows that the E22 at NOISE_FLOOR exports only dssq_rf_margin_max_db and dssq_rf_margin_worst_max_db, with no margin_worst_est_db sample. During LOS the gauge is in LIVE_RF_KEYS. A Prometheus comparison on an absent series returns nothing, so the alert clears exactly when the link fades into the noise. Worse than reported: on an SX126x station, sensitivity_worst is None, so margin_worst_est_db is ALWAYS absent. alert_sx.py: SX126x at 1.0 dB margin (SIGNAL) exports only dssq_rf_margin_est_db 1.0. The recommended alert can therefore never fire on an SX126x station. margin_state is a string and has no numeric gauge.

**Fix notes.** Export the coloured margin under one name for both radios, for example dssq_rf_margin_colour_db = rxstats.margin_colour_db(). Also export a numeric dssq_rf_margin_state_code (0 SIGNAL, 1 NEAR_NOISE, 2 NOISE_FLOOR, 3 NOISE_UNKNOWN). Document alerts such as 'dssq_rf_margin_colour_db < 3', 'dssq_rf_margin_state_code == 2 and dssq_los == 0' and 'dssq_rf_margin_worst_max_db < 3' in docs/13 section 16.

## LS-12 [CONFIRMED / minor] Rate-window test will start failing on 2027-01-15
`tests/test_link_legitimacy.py:245`

**Description.** _rx() builds ReceiverStats with t0 = the real time.time(). The test feeds T0 = 1.8e9 (2027-01-15T08:00Z) as 'now'. _rates() uses span = max(1, min(win, now - t0)). After that date now - t0 is negative, so span = 1 s and frames_per_min becomes 360 instead of 6*60/692.5.

**Failure scenario.** I reproduced it by setting time.time to T0 + 30 days before construction (review31/LS/future_rates.py): frames_per_min = 360.0 against the expected 0.5199. The assertion at line 245 fails on every CI run from mid-January 2027.

**Proposed fix.** Set r.t0 = T0 - 1 after _rx() in this test, or monkeypatch time.time to T0 while building it.

**Verifier.** ReceiverStats.__init__ sets self.t0 = time.time() (rxstats.py:437). _rates uses span = max(1, min(win, now - t0)) (rxstats.py:862). The test feeds now = T0 + 1385 with T0 = 1.8e9 (2027-01-15T08:00Z). Reproduced with /tmp/claude-0/-home-user/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/review31/verify-LS/future_test.py, which runs the real test function with time.time patched: 'today PASS', '2027-01-15T08:20Z FAIL', '2027-02-14 FAIL' (assertion at tests/test_link_legitimacy.py:245). Every CI run fails from 2027-01-15T08:11:32Z (T0 + 692.5 s), when now - t0 drops below the 692.5 s rate window.

**Fix notes.** In the test, set r.t0 = T0 - 1e6 (or any time well before T0) right after r = _rx(). Better, have _rx() set r.t0 = T0 - 1e6 for all tests in the file, or give ReceiverStats an optional t0 argument. Other test files use the same T0 (test_docs, test_end_to_end, test_imu_chart); check them for the same pattern.

## LS-13 [CONFIRMED / minor] (Outside the LS lens) docs/15 station-sketch pin table contradicts pin plan H
`docs/15_Configuration_Reference.md:444`

**Description.** docs/15 section 5 lists PIN_RCU_AUX = D4, LEDs D5/D6/D7, buzzer A0 and PIN_SX_NRST = A1 (lines 443-445). The sketch changed in this diff uses PIN_RCU_AUX = 7 (sketch.ino:149), LEDs 4/5/6 and buzzer A1 (sketch.ino:152), and PIN_SX_NRST = A4 (sketch.ino:532), as in D-72. docs/15 was not updated.

**Failure scenario.** Someone wiring from docs/15 puts the E22 AUX on D4, where the AOS lamp is driven as an output. They also expect the buzzer on A0 and the SX126x reset on A1, where the buzzer actually is.

**Proposed fix.** Update docs/15 lines 443-445 to RCU D2/D3/D7, LEDs D4/D5/D6, buzzer A1, SX NRST A4, matching sketch.ino and docs/03.

**Verifier.** docs/15_Configuration_Reference.md section 5 (lines 430-445) is still titled '(VENTUNO Q MCU)'. It lists RCU AUX D4 (443 and 437), LEDs D5/D6/D7 with buzzer A0 (444), and PIN_SX_NRST A1 (442). The diff's sketch has PIN_RCU_AUX = 7 (sketch.ino:149), LEDs 4/5/6 with buzzer A1 (152), and PIN_SX_NRST = A4 (532), as in contract D-72. The diff touched docs/15 only for the key count, still_* keys and --sim. One correction to the proposed fix: docs/03 is stale as well. Its section 5 / 5.1 (docs/03_Hardware_Integration.md:504-517) is still 'Ground station (VENTUNO Q) wiring' with AUX D4, lamps D5-D7 and buzzer A0, and D-71..D-81 are not yet in docs/12. So 'matching docs/03' would copy the old plan. Wiring hazard: following the docs puts the E22 AUX output on D4, which the sketch drives as the AOS lamp output.

**Fix notes.** Update docs/15 section 5 (title UNO Q MCU; DSSQ_RADIO_SX126X row 'RCU on D2 M0, D3 M1, D7 AUX'; PIN_RCU_* D2, D3, D7; lamps D4/D5/D6; buzzer A1; PIN_SX_NRST A4, RXEN A2, TXEN A3). Update docs/03 section 5.1 to pin plan H with the UNO Q MCU pin names in the same change. Record D-71..D-81 in docs/12, which the pending documentation phase (contract section 9) requires anyway.

## LS-2 [CONFIRMED / minor] Link manager reads 'no frame decoded' as FER 0 % and recommends a faster rate
`dssq-groundstation/python/dssq/linkmgr.py:104`

**Description.** evaluate() only checks that the window holds at least MIN_FRAMES RSSI samples (receptions, line 93). A reception is not a decoded frame. When no frame decoded, lm_input() returns fer None, and line 104 (fer = rx.get('fer') or 0.0) turns that into a perfect 0 % FER. The HIGH branch then fires on 'FER < 1 %'. The dashboard (dashboard.html:1668) shows num(rec.frames) with frames = 0 as '0 lost of 0 (0 %)': a FER where nothing was measured.

**Failure scenario.** SX126x station: every packet calls on_reception, including foreign LoRa packets that are not CADU-length. Sync word 0x12 is a common default, so such packets are plausible at 433 MHz. I reproduced this in review31/LS/lm_nofer.py with 12 foreign packets at -95 dBm, noise -120 dBm, 0 frames. The result is {'action': 'LINKRATE', 'value': 2, 'fer': 0.0, 'frames': 0, 'why': 'margin high: faster rate (predicted 42.0 dB)'}. This can happen in LOS. In AUTO mode the GDS starts a coordinated LINKRATE.

**Proposed fix.** Return NONE 'insufficient data (< MIN_FRAMES decoded frames in W s)' unless rx['frames'] >= MIN_FRAMES. Never replace fer None with 0.0. In the dashboard, show NA for 'FER in the window' when rec.frames is 0.

**Verifier.** Reproduced with /tmp/claude-0/-home-user/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/review31/verify-LS/lm_nofer.py (SX126x, 12 receptions at -95 dBm, noise -120, 0 frames). lm_input gives frames 0, fer None, rssi n 12. evaluate() returns action LINKRATE value 2 with fer 0.0 and frames 0. Code: linkmgr.py:93 gates on rs['n'], the RSSI window. That window is fed by on_reception (rxstats.py:631) for every received unit. On the SX126x this includes non-CADU 'foreign' packets and CRC-failed packets that RS cannot rescue (gds.py:722-731). Decoded frames come only from lm_w['lost'] in on_frame (rxstats.py:712). linkmgr.py:104 then turns fer None into 0.0, so the HIGH branch's 'FER < 1 %' passes. The 'why' text even says '< 10 frames' although it counts receptions. The dashboard (dashboard.html:1668) calls num(0), which is true, so it shows '0 lost of 0 (0 %)'. evaluate() also runs during LOS (gds.py:2001-2009). Why minor and not major: the default radio is the E22, whose frame sync only yields ASM-found CADUs. AUTO needs the operator and an RCU (default mode ADVISE). The coordinated change reverts when no frames follow (gds.py:1900-1908). The same 'or 0.0' existed in 3.0.

**Fix notes.** In evaluate(), after the RSSI check, return NONE 'insufficient data (< MIN_FRAMES decoded frames in W s)' when rx.get('frames', 0) < MIN_FRAMES. Keep the RSSI-count check too, and fix its wording to 'receptions'. Do not replace fer None with 0.0: with frames >= MIN_FRAMES, fer is never None. In dashboard.html:1668, use rec.frames > 0 (not num(rec.frames)) before printing a FER. Otherwise show 'n/a (no decoded frame in the window)'.

## LS-4 [CONFIRMED / minor] Unsynchronised clock still shows the kernel's saturated maxerror (16 s) as a 'maximum error'
`dssq-groundstation/python/dssq/display/dashboard.html:2348`

**Description.** When the kernel has no discipline it caps time_maxerror at NTP_PHASE_LIMIT (16,000,000 µs) and sets STA_UNSYNC. That value is a placeholder, not a bound: the real error is unknown. clockRows() (line 2348) and the clockView() tooltip (line 508) still print it as 'Station clock maximum / estimated error 16000 ms / 16000 ms (kernel)'.

**Failure scenario.** The ground UNO Q boots without network: the kernel returns TIME_ERROR, STA_UNSYNC, maxerror = esterror = 16000000. This machine returned exactly that from query_kernel(). The GDS health panel and the CLOCK tooltip claim a maximum error of 16 s, while the clock may be hours or days off (for example a time restored from the last shutdown).

**Proposed fix.** Show maxerror and esterror only when ck.synced === true. Otherwise render 'n/a (not synchronised: the kernel gives no bound)'. Document the same in docs/13 13.7.

**Verifier.** This sandbox's kernel returned exactly ClockReading(synced=False, state='TIME_ERROR', sta_unsync=True, maxerror_us=16000000, esterror_us=16000000) from clocksync.query_kernel(). 16,000,000 µs is NTP_PHASE_LIMIT. The Linux kernel (ntp.c second_overflow) caps time_maxerror there and sets STA_UNSYNC, and ntp_clear() initialises both maxerror and esterror to it. So when unsynchronised the value is a ceiling, not a bound on the real error. clockRows (dashboard.html:2348) and the clockView tooltip (508) still print 'maximum / estimated error 16000 ms / 16000 ms (kernel)'. docs/13:3144 and 3162 describe the fields as the kernel's maximum and estimated error without that caveat. Effect: a clock restored from the last shutdown, which can be hours or days off, is presented as within 16 s. The NOT SYNCED state is shown next to it, and the TDM refusal works.

**Fix notes.** Render the error values only when ck.synced === true, in both clockRows and clockView. Otherwise show 'n/a (not synchronised: the kernel gives no bound)'. Optionally do the same in /metrics if maxerror is ever exported (it is not today). Add one sentence to docs/13 13.7: when unsynchronised the kernel reports its 16 s ceiling (NTP_PHASE_LIMIT), which is no bound.

## LS-5 [CONFIRMED / minor] Ground RF survey (GSCAN) ranks E22 end-of-range noise octets as the quietest channel
`dssq-groundstation/python/dssq/gds.py:1829`

**Description.** D-76 says an E22 noise-register octet at the end of its range, or below -160 dBm, is not a noise power (rxstats.on_noise_octet rejects it). The GSCAN path skips that rule. RadioManager.noise_dbm() returns -(256 - octet), so 0x00 gives -256 dBm. survey() takes min(valid) as 'quietest' (radioctl.py:259-260). The GSCAN job calibrates the values and takes min(allowed) as 'quietest_allowed' (gds.py:1825-1829). The RECEIVER RF-survey bars plot the same values.

**Failure scenario.** With [gds] rcu = true, run GSCAN while a strong transmitter is on one channel, for example the spacecraft on the bench side by side (record 2026-10-13-A showed 0x00/0xFF octets). That channel reads -256 dBm. The event log reports 'quietest CH<n>, quietest allowed CH<n>' for the busiest channel, and the operator may send LINKCHAN to it.

**Proposed fix.** In the GSCAN job (E22 only), map each n with rxstats.classify_rssi(n, True) != 'OK' to None before calibration and before both min() calls. Record those channels as 'overload' or 'invalid' in last_scan.

**Verifier.** radioctl.py:214 returns -(256 - octet), so 0x00 gives -256 dBm. survey() takes min(valid) as 'quietest' (radioctl.py:259-260). The GSCAN job (gds.py:1822-1829) only adds the calibration and takes min(allowed). Neither path calls classify_rssi(), yet rxstats.on_noise_octet (602-611) rejects exactly these octets as 'no noise power' under D-76. The RF-survey bars (dashboard.html:1501, lo -130) clip -256 to the bottom, so the channel looks quietest. One correction to the reviewer: docs/21 record 2026-10-13-A shows end-of-range PACKET RSSI octets (-1 / -256, SNRest 71 / -184, so the noise was about -72 dBm then), not noise-register octets. A noise octet of 0x00 is therefore anticipated by the code's own rule but not observed. In practice it needs a saturating transmitter during the dwell (bench), and GSCAN needs [gds] rcu = true.

**Fix notes.** In the GSCAN job, before calibration, map each n with rxs.classify_rssi(n, not rm.packet) != 'OK' to None (octet radio = E22; on the SX126x only the < -160 dBm INVALID rule applies). Record which channels were rejected (for example res['rejected'] = {ch: 'OVERLOAD'|'INVALID'}). Recompute res['quietest'] from the filtered list, because survey() already picked its min on the raw values, and then 'quietest_allowed'. Better still, apply the classification inside RadioManager.survey() so last_scan is clean for every caller. Draw rejected channels as 'overload' in the bar chart, not as a bar.

## LS-6 [CONFIRMED / minor] /metrics exports the link manager's noise-floor upper bound as a plain 'p10 margin'
`dssq-groundstation/python/dssq/display/web.py:418`

**Description.** At NOISE_FLOOR, linkmgr.evaluate() stores the upper bound noise_mean - S in recommendation.margin_p10_db (linkmgr.py:103). The page prefixes it with '≤' (dashboard.html:1666). /metrics exports it as dssq_linkmgr_margin_p10_db, 'link manager: p10 margin of its last evaluation', with no state, so a scraper cannot tell a bound from a measurement. With mode OFF, evaluate() returns early and the old recommendation (and gauge) stays exported indefinitely.

**Failure scenario.** Long-range pass at the noise floor. In the simulator (review31/LS/sim_far.py, RSSI -117, noise -118) the recommendation holds margin_p10_db 7.8 with noise_state NOISE_FLOOR. /metrics shows dssq_linkmgr_margin_p10_db 7.8, a healthy margin above target - 3, while the true margin is unknown and may be negative.

**Proposed fix.** Export the gauge only when noise_state is SIGNAL or NEAR_NOISE (optionally NOISE_UNKNOWN). Export the bound as dssq_linkmgr_margin_p10_max_db. Clear recommendation when the mode is set to OFF.

**Verifier.** Ran /tmp/claude-0/-home-user/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/review31/verify-LS/sim_far.py (GDS + simulator, RSSI -117, noise about -118, 300 s). recommendation = {margin_p10_db 7.8, noise_state 'NOISE_FLOOR', rssi_p10 -118.4, noise_mean -118.25, sensitivity -126 worst case}. That is the upper bound noise - S from linkmgr.py:103 (a['max_db']). web.py:418 exports it unconditionally as dssq_linkmgr_margin_p10_db, 'p10 margin of its last evaluation', with no state, so 7.8 reads as a healthy margin above target - 3. The page marks it '≤' (dashboard.html:1666), the metric does not. The LINKMGR directive (gds.py:1424-1426) sets mode OFF without clearing the recommendation, and evaluate() returns early when OFF (linkmgr.py:86). The stale gauge therefore stays exported indefinitely, including during LOS (not in LIVE_*).

**Fix notes.** In web.prometheus, export dssq_linkmgr_margin_p10_db only when rec.noise_state is 'SIGNAL' or 'NEAR_NOISE' (optionally NOISE_UNKNOWN). Otherwise export dssq_linkmgr_margin_p10_max_db with the same value. Optionally add a numeric state gauge. In the LINKMGR directive, set self.link_mgr.recommendation = None when the new mode is OFF. Document the split in docs/13 section 16.

## LS-7 [CONFIRMED / minor] Uplink noise-floor rule pairs an uplink RSSI from an earlier pass with the current spacecraft noise
`dssq-groundstation/python/dssq/rxstats.py:1024`

**Description.** _budget() applies margin_assessment() to RF uplink_rssi and RF sc_noise_floor. The flight keeps uplink_rssi_ from the last uplink packet indefinitely (flight.cpp:696; never aged or cleared). sc_noise_floor follows the periodic noise queries (flight.cpp:705-711). The two readings can be hours apart, which breaks the same-pass pairing rule (F-P1-5) applied everywhere else. The panel still labels the uplink RSSI and margin 'measured'.

**Failure scenario.** At AOS the first RF packets arrive before the ground has transmitted in this pass. They carry the previous pass's uplink_rssi (for example -95 dBm at close range) and the current noise. budget.uplink shows 'Received power at the S/C -95 dBm (RF uplink_rssi)', state SIGNAL and a large uplink margin, all describing a different geometry.

**Proposed fix.** Assess, and show as measured, only when RF cltu_ok or cltu_bad increased since the previous RF packet (an uplink was received in this pass). Otherwise set measured_uplink_rssi_dbm, measured_margin_db and the state to None ('no uplink received this pass').

**Verifier.** flight.cpp:696 sets uplink_rssi_ only in on_uplink_packet. It is never aged or cleared, and only a reboot resets it to n/a. noise_dbm_ follows the periodic noise samples (flight.cpp:705-711; docs/13:2036: every 15 s or more while quiet). The RF packet (tlm_packets.h:211-236) has no age or timestamp for uplink_rssi. rxstats._budget (1024-1029) still pairs the two with margin_assessment() and publishes measured_uplink_rssi_dbm, measured_margin_state and measured_margin_db. The page badges them 'measured' (dashboard.html:1459-1460) and uses them in the two-way panel. The ground's own same-pass pairing rule (noise_for, F-P1-5) is not applied here. In the design case (an uplink that no longer reaches the spacecraft at range), the panel keeps showing the last successful, possibly much closer, uplink with state SIGNAL and a large margin. Mitigation: the staleness itself predates 3.1 (3.0 already exported measured_uplink_rssi_dbm), and docs/13:999 and 2033 say 'last uplink packet'. 3.1 adds the noise-floor verdict on a mismatched pair.

**Fix notes.** In ReceiverStats, remember RF cltu_ok + cltu_bad (uint16; compare modulo 65536) at AOS and on each RF packet. Treat uplink_rssi as 'this pass' only when that sum changed since AOS, because every uplink packet reaching process_tc increments one of them. Otherwise set measured_uplink_rssi_dbm / measured_margin_* to None, with state 'NO_UPLINK_THIS_PASS', and render 'n/a (no uplink received this pass)'. Longer term, add an uplink-RSSI age field to the RF packet (ICD change).

## LS-9 [CONFIRMED / minor] Link budget 'measured received power' is the raw 10-minute RSSI mean, even at the noise floor
`dssq-groundstation/python/dssq/display/dashboard.html:1448`

**Description.** The _budget docstring says every measured term uses the received power the noise-floor rule accepts, and 'path loss 10-min mean' applies it (ma10). The '= Received power' measured cell is pmres(r10, 'rssi'), the raw 10-minute RSSI mean, and carries a 'measured' badge. At NOISE_FLOOR that number is the noise, placed next to the predicted signal power.

**Failure scenario.** Simulator at RSSI -117 / noise -118 (review31/LS/sim_far.py): measured_path_loss_mean10m_db is None (rule applied), but '= Received power' shows -117 dBm 'measured'. With a configured distance predicting -135 dBm, the budget suggests the link is 18 dB better than predicted.

**Proposed fix.** Export ma10['power_dbm'] (for example budget.measured_pr_mean10m_dbm) and show it, with 'n/a (noise floor)' when ma10 is NOISE_FLOOR. Update the docs/13 4.5 row.

**Verifier.** dashboard.html:1448 renders the measured '= Received power' as pmres(r10, 'rssi'), the raw 10-minute RSSI window mean, with a 'measured' badge. Two rows above, 'path loss 10-min mean' uses budget.measured_path_loss_mean10m_db, which applies margin_assessment() to the 10-minute means (rxstats.py:1003-1004, 1053) and is None at NOISE_FLOOR. sim_far.py shows both together: measured_path_loss_mean10m_db None and measured_power_state NOISE_FLOOR, while the 10m RSSI mean is -117.07 and the noise -118.07. The table thus pairs the predicted signal power with the measured total in-band power, which at the noise floor is the noise. With a configured distance, this suggests a link far better than predicted. The behaviour matches docs/13:981 ('10-min RSSI mean (n)'), so this is an inconsistency inside the table and against the _budget docstring ('every measured term uses the received power the D-76 rule accepts'), not a code/docs mismatch.

**Fix notes.** In _budget, export measured_pr_mean10m_dbm = ma10['power_dbm'] and measured_power_state_10m = ma10['state']. In dashboard.html:1448, show that value: noise-removed in NEAR_NOISE, and 'n/a (noise floor)' when ma10 is NOISE_FLOOR. Update the docs/13 4.5 '= Received power' row to match.

## UI-02 [CONFIRMED / minor] IMU_F_PAIRED means different things on the flight and the ground: the flight also requires a valid 9-DOF quaternion
`dssq-groundstation/python/dssq/sensors.py:222`

**Description.** The flight sets IMU_F_PAIRED only when aru_imu_paired_ && aru_valid_ && (aru_last_.motion_flags & 1u), i.e. only when q_fus is also valid (flight.cpp:1857). tlm_packets.h:135, docs/05 and the ground all define it as 'q_grv from the same ARU rebuild as q_fus'. The ground requires PAIRED for the linear acceleration and explains a clear PAIRED as 'acc and q_grv not from one ARU rebuild' (sensors.py:222-224). Its own 'paired but ATT b0 QUAT clear' branch (sensors.py:226-227) cannot be reached with real flight data. tests/test_sensors.py:100-102 tests that impossible state.

**Failure scenario.** The 9-DOF rotation vector is not valid (page 0 b0 clear, e.g. stale or outside the norm gate), while acc (b1) and q_grv come from the same ARU rebuild (imu.seq == r.seq). The flight clears PAIRED. The ground then shows lin_acc as n/a with the false reason "IMU_F_PAIRED clear: acc and q_grv not from one ARU rebuild", although it could compute the value.

**Proposed fix.** Flight: set IMU_F_PAIRED = aru_imu_paired_ && aru_valid_ (pairing only); the ground already checks ATT b0 QUAT for the heading offset. If the flight is not changed, reword the ground reasons and docs/05 / tlm_packets.h to "not from one ARU rebuild, or no valid 9-DOF rotation vector" and fix the test.

**Verifier.** flight.cpp:1857 sets IMU_F_PAIRED only if aru_imu_paired_ && aru_valid_ && (aru_last_.motion_flags & 1u). ATT_F_QUAT is set from the same aru_valid_ && aru_last_.motion_flags & 1 (flight.cpp:1815-1818), so PAIRED implies QUAT. tlm_packets.h:135 and the contract (section 4.2: 'pairing imu.seq == motion.seq') define PAIRED only as same-rebuild seq equality. The ground's branch at sensors.py:226-227 (PAIRED set but QUAT clear) therefore cannot happen with flight data. The ground norm gate equals the flight's 0.9..1.1 gate in read_motion; only Q14 edge rounding could differ. test_sensors.py:100-102 tests that unreachable state. The degraded case is realistic: the Nicla firmware sets page-0 b0 from rotation-vector freshness and norm (nicla_sense_me_aru.ino:418), independently of acc b1 (:426) and GRV (:475). If the 9-DOF rotation vector goes stale while acc and GRV stay fresh, the flight clears PAIRED, and the ground withholds lin_acc with the false reason 'acc and q_grv not from one ARU rebuild' (sensors.py:224).

**Fix notes.** Preferred: flight.cpp:1857 should set IMU_F_PAIRED when aru_imu_paired_ && aru_valid_ (pairing only). The ground already checks ATT b0 QUAT for the 9-DOF comparison (sensors.py:226). Otherwise keep the flight as it is, redefine PAIRED in tlm_packets.h, docs/05 and docs/13 9.4 as 'same rebuild and a valid q_fus', reword the reasons at sensors.py:223-224 to '... not from one ARU rebuild, or no valid 9-DOF rotation vector', and delete the impossible test case at test_sensors.py:100-102.

## UI-03 [CONFIRMED / minor] A saturated imu_nwin (255) is displayed as an exact window count
`dssq-groundstation/python/dssq/display/dashboard.html:2074`

**Description.** ImuStats::windows() clamps at 255 (attitude.h:104), but the sums keep running over all windows. The ground shows imu_nwin as an exact number, and docs/13:2696 and docs/05 call it the number of 1 s windows in the ATT period without mentioning saturation. ATT periods longer than 255 s are realistic: PKTRATE ATT accepts up to 3600 s; the on-board stretch at ultra-long-range settings gives CRUISE 60 s x 4.74 = 284 s at SX126x profile 0 (R3 section 5.8); and ImuStats is not reset while ATT is off in SAFE, so the first ATT after SAFE covers the whole SAFE period. The simulator (sim.py _imu_stats) aggregates only the newest 255 windows, unlike the flight.

**Failure scenario.** After PKTRATE ATT 600 (or CRUISE at SX126x p0, or the first ATT after an hour in SAFE), the SCIENCE IMU table reads "Windows aggregated 255", while vib_rms / rate_mean / peaks cover 600 or more windows.

**Proposed fix.** When nwin == 255, show ">= 255 (counter saturated)". State the saturation in docs/05 (imu_nwin) and docs/13 section 9.4. In the simulator, aggregate all windows and report only the count as min(n, 255).

**Verifier.** attitude.h:104 windows() returns min(n_, 255), while n_, vib2_ and rate_sum_ keep accumulating (attitude.cpp:198-202). imu_stats_ is reset only in gen_att() (flight.cpp:1869, the only reset site). ATT is off in BOOT and SAFE (datalink.cpp:277-278), and the flight boots in SAFE, so the first ATT after every boot or SAFE period aggregates every window since then: anything longer than 255 s saturates. PKTRATE accepts periods up to 3600 s (commands.cpp:127), and stretched_period_s can stretch CRUISE 60 s further. dashboard.html:2074 shows fx(m.nwin,0) as an exact count, and docs/05:163 and docs/13:2696 say nothing about saturation. sim.py:1228-1244 aggregates only the newest 255 windows ('first = max(imu_win_end, end-255)'), so the simulator's statistics differ from the flight's, whose rate_mean, vib_rms and peaks cover all windows.

**Fix notes.** Dashboard: when nwin == 255, show '>= 255 (counter saturated; the statistics cover the whole period)'. Optionally expose imu.nwin_saturated from sensors.py. State the saturation in docs/05 (imu_nwin) and docs/13 9.4. Simulator: aggregate every window since imu_win_end and send imu_nwin = min(n, 255). If the cost matters, cap the work, but compute rate_mean, vib_rms and peaks over the full set the way the flight does.

## FL-08 [CONFIRMED / nit] docs/05 omits the second 0x060D format ("... not separated") that drives the YELLOW alarm
`docs/05_Telemetry_and_Command_Dictionary.md:623`

**Description.** flight.cpp:550 prints 0x060D "%s and %s both on %s: not separated (docs/03)", and dictionary.SENSOR_BUS_EVRS lists both formats. tools/gen_dictionary_doc.py:124 keeps only the first format per id (rows.setdefault), so the docs/05 EVR table shows only "%s found on %s; the recommended bus is %s (docs/03)".

**Failure scenario.** An operator looks up the WARN 0x060D "Nicla Sense ME and RM3100 outboard both on A4/A5: not separated (docs/03)" in docs/05. The documented text does not match, although this is the event behind the "sensor wiring" YELLOW alarm.

**Proposed fix.** In evrs(), collect every distinct format per id (e.g. rows[key] = list of formats, one row each, or joined with " / ") and regenerate docs/05.

**Verifier.** tools/gen_dictionary_doc.py evrs() uses rows.setdefault(key, ...), which keeps the first literal format per id. docs/05 line 623 lists only '%s found on %s; the recommended bus is %s (docs/03)' for 0x060D. The flight also prints '%s and %s both on %s: not separated (docs/03)' (flight.cpp:550), and dictionary.SENSOR_BUS_EVRS lists both formats. Only docs/13 (lines 2120, 3257) mentions the 'not separated' text.

**Fix notes.** Collect every distinct format per id in evrs() (one row per format, or join them with ' / ') and regenerate docs/05.

## FL-09 [CONFIRMED / nit] The "pairing rule" test does not exercise the pairing logic
`tools/host_test/test_sensor_bus.cpp:97`

**Description.** The file header and the comment at line 97 say the pairing rule (Flight::start_mag_sample) is tested. The block only decodes two pages with seq 200 and 201 and compares them with a constant. The page-0 re-read on a seq mismatch, aru_imu_paired_, and IMU_F_PAIRED in gen_att are in flight.cpp, which the host build excludes, so no test covers them.

**Failure scenario.** A regression that drops the page-0 re-read, or sets aru_imu_paired_ from the first page-0 read (r before r2), passes this test and every host test.

**Proposed fix.** Move the read-and-pair step into a pure helper (e.g. a template over a page-reader callback returning {r, imu, paired}) and test the cases seq equal, seq differs then re-read matches, and re-read differs again or fails. Otherwise, reword the test comment so it does not claim to cover the rule.

**Verifier.** The test_sensor_bus.cpp header lists 'the pairing rule', and the block commented 'Pairing (Flight::start_mag_sample)' only decodes two pages with seq 200/201 and compares them with a constant 200. The page-0 re-read on a seq mismatch, aru_imu_paired_ and IMU_F_PAIRED live in flight.cpp, which the host Makefile excludes (FSW_HOST filter-out). A regression in that logic passes every host test.

**Fix notes.** Move the read-and-pair step into a pure helper (a template over a page-reader callback returning {r, imu, paired}) in attitude.h, and test: seq equal; seq differs then the re-read matches; the re-read differs again or fails. Otherwise reword the test comment and the header so they do not claim to cover the rule.

## GS-7 [CONFIRMED / nit] D-77 fault_log default never reaches upgraded stations: 3.0 station.toml carries an explicit fault_log = false
`dssq-groundstation/python/dssq/config.py:144`

**Description.** The 3.0 shipped station.toml contained 'fault_log = false' as a mirrored default (removed in this diff). The App copies station.toml to data/ once, and README 2.6 tells the operator to restore the old data/ folder on update. merge() applies fault_log_default() only when the key is absent, so every station upgraded from 3.0 keeps fault_log false. Selecting model = "E22-400T37S" then leaves the T37S abnormal-status log off (the configuration tool --write and the GDS volatile writes clear REG1 b2), with no hint.

**Failure scenario.** A 3.0 station is updated to 3.1 by the README procedure and switched to the T37S. regs_from_cfg() writes REG1 b2 = 0, so the module's under-voltage and over-temperature reports never appear, contrary to D-77 ('fault_log defaults to true when model = E22-400T37S').

**Proposed fix.** Note in the 3.1 upgrade text (README 2.6 / CHANGELOG): delete the old 'fault_log = false' line. Optionally log an INFO at start when model is E22-400T37S and fault_log is explicitly false: 'T37S abnormal-status log off by station.toml'.

**Verifier.** The 3.0 shipped station.toml (13a60c5) line 62 has 'fault_log = false' as a mirrored default; 98887f5 removes it. merge() applies fault_log_default() only when the key is absent (config.py:143-145). README 2.6 step 3 tells the operator to copy the old data/ (with its station.toml) back, and to compare against the packaged file only 'for new keys', not removed ones. Reproduced (verify-GS/fl.py): the 3.0 file with model switched to E22-400T37S loads under 3.1 with fault_log False and regs_from_cfg REG1 = 0x20 (b2 = 0). The 3.1 file with the same model gives fault_log True and REG1 = 0x24. CHANGELOG has no note. I re-grade to nit: honouring an explicit value in the operator's file is correct config behaviour; the only effect is that the T37S under-voltage and over-temperature reports stay off (diagnostic, no link impact), and one documented line fixes it.

**Fix notes.** Add a 3.1 upgrade note (CHANGELOG and README 2.6): 'delete the line fault_log = false from a station.toml copied from 3.0 (it was the old mirrored default; D-77 now derives it from [radio] model)'. Optionally log an INFO at start when model = E22-400T37S and fault_log is explicitly false: 'T37S abnormal-status log off by station.toml'. No code change is required.

## GS-8 [CONFIRMED / nit] Station harness checks claim more than they test (4 Hz redraw, millis() wrap)
`tools/host_test/station/test_e22.cpp:253`

**Description.** 'loop() redraws the matrix at 4 Hz' requires only >= 2 draws in 600 ms, so it also passes for a redraw on every loop pass or at ~3.4 Hz. 'the RCU age grows across hellos (re-anchored, never wraps with millis())' (line 181) only moves g_rcu_t_ms back by 7 s and 3 s; no millis() wrap is exercised.

**Failure scenario.** A regression that sets kMxPeriodMs to 0 (redraw every pass) still passes 'at 4 Hz'. A signed or overflowing age computation across the 2^32 ms wrap would not be caught by the 'never wraps' check.

**Proposed fix.** Count draws over a fixed window and require 2-3 per 600 ms (or check the interval between draws equals kMxPeriodMs). For the wrap, set g_rcu_t_ms = millis() + 0xFFFFF000u - 0xFFFFFFFFu (an anchor just before a wrap) and check that the age stays within the expected small value. Or rename the checks to what they test.

**Verifier.** Mutation tests on sketch copies under verify-GS/ (the repository was not touched). The 4 Hz part holds: with kMxPeriodMs = 0 (redraw every loop pass), 'loop() redraws the matrix at 4 Hz' still passes, because it only requires draws >= +2 in 600 ms (test_e22.cpp:250-253). The 290 ms variant failed in my run (phase dependent), so the '~3.4 Hz passes' claim is timing dependent, but the lower-bound-only check is real. The millis()-wrap part is largely REFUTED. The harness millis() starts at 0, and an instrumented copy showed millis() = 2561 when 'g_rcu_t_ms -= 7000' runs, so the anchor becomes 4294962857. The uint32 difference (now - g_rcu_t_ms) therefore really crosses the 2^32 boundary, and a wrong difference across that boundary would be caught (incidentally, as long as the check runs within the first 7 s). A mutation that drops the re-anchoring (sketch.ino:346) but keeps the accumulation makes the check FAIL. What is not exercised is an age beyond 49.7 days, which is the property re-anchoring exists for.

**Fix notes.** Check the redraw interval exactly. Record the draw times inside run_loop() (or count draws in a 1 s window) and require the gaps to equal kMxPeriodMs within a few ms (e.g. 3-5 draws per 1 s), not just a lower bound. For the RCU age: make the wrap crossing explicit and independent of start-up time. Set g_rcu_t_ms = (uint32_t)millis() - 7000u and comment that it crosses 2^32 when millis() < 7000. Add a long-age case: call rcu_age_s() repeatedly after moving the anchor back 0x7FFF0000 ms twice, and check the age keeps growing past 4294967 s (49.7 days) and saturates at 0x7FFFFFFF. Or rename the check to what it tests.

## ICD-10 [CONFIRMED / nit] Simulator sets IMU_F_STATS even when imu_nwin is 0 (the flight never does)
`dssq-groundstation/python/dssq/sim.py:1255`

**Description.** IMU_FLAGS_SIM = 0x47 (sim.py:125) is always sent, but _imu_stats() returns {'imu_nwin': 0} with sentinel statistics when no whole SCLK second has passed since the previous ATT (sim.py:1238). The flight sets IMU_F_STATS only when windows() != 0 (flight.cpp:1863). The ground copes (sensors.py:242 checks both), but the simulator, which the dashboard tests and docs examples rely on, breaks the ATT v4 invariant 'STATS <=> imu_nwin >= 1'.

**Failure scenario.** Two simulated ATTs in the same SCLK second (or the first ATT at SCLK < 1 s) give imu_flags with STATS set and imu_nwin 0, a state the real spacecraft cannot send.

**Proposed fix.** In _att(), clear bit 0x02 from imu_flags when the stats dict has imu_nwin == 0.

**Verifier.** sim.py:125 IMU_FLAGS_SIM = 0x47 (STATS bit 0x02 set) is always written (sim.py:1255), while _imu_stats() returns {'imu_nwin': 0} when no whole SCLK second has passed since the previous ATT (sim.py:1236-1238). Reproduced in /tmp/claude-0/-home-user/5ee836b4-fd12-52ad-8530-d6659d1475b9/scratchpad/review31/verify-ICD/icd10.py: _att at SCLK 100.2 gives (0x47, 100), and _att at SCLK 100.7 gives (0x47, 0, vib_rms None). The case is reachable because a mode change resets last_gen (sim.py:835), so a mode change in the same second as an ATT sends a second ATT at once. The flight sets STATS only when windows() != 0 (flight.cpp:1863). The ground copes (sensors.py:242 checks both).

**Fix notes.** In _att(), after v.update(self._imu_stats(t)): `if not v['imu_nwin']: v['imu_flags'] &= ~0x02`. Optionally add an assertion in the sim tests that STATS is set exactly when imu_nwin >= 1.

## ICD-7 [CONFIRMED / nit] The displayed 'noise floor est.' for |omega| of the MEAN rate is the single-sample floor, too large by sqrt(25 x imu_nwin)
`dssq-groundstation/python/dssq/sensors.py:81`

**Description.** RATE_FLOOR_DPS = 0.07 x sqrt(3) = 0.121 deg/s is the noise magnitude of ONE gyro sample. ATT rate_mean is the mean of the window means, i.e. of about 25 x imu_nwin samples, whose white-noise floor is 0.121 / sqrt(25 x nwin), about 0.0086 deg/s at nwin 8. dashboard.html:2077 prints '|omega| of the mean rate ... (noise floor est. 0.12 deg/s)' next to that mean, and the spin-axis gate is 10 x this per-sample figure. The number shown is not the floor of the quantity it is attached to. vib_floor is correct, because vib_rms is a per-sample RMS.

**Failure scenario.** TEST mode, still spacecraft, imu_nwin 8. The table shows '|omega| 0.03 deg/s (noise floor est. 0.12 deg/s)', which suggests the reading is buried in noise, while the white-noise floor of that 200-sample mean is about 0.009 deg/s (the limit is the unspecified residual offset, not noise).

**Proposed fix.** Report the floor of the mean, RATE_FLOOR_DPS / sqrt(25 * nwin), labelled 'white-noise floor of the mean (residual offset not included)'. Or label 0.12 deg/s as 'per-sample noise (peak rate)' and move it to the Peak |rate| row.

**Verifier.** RATE_FLOOR_DPS = 0.07*sqrt(3) = 0.121 deg/s (sensors.py:81) is the per-sample noise magnitude. dashboard.html:2077 prints it as the 'noise floor est.' of '|omega| of the mean rate', and rate_mean is the mean over about 25 x imu_nwin samples, whose white-noise floor is 0.121/sqrt(25 n), about 0.009 deg/s at n=8. The label therefore does not describe the quantity it sits next to. Downgraded to nit: the practical conclusion (do not trust about 0.03 deg/s) is still right, because the unspecified residual gyro offset (±1 deg/s with FOC off, cited in docs/13:2699) dominates the mean. The per-sample figure is also a defensible conservative base for the 10x spin-axis gate (1.21 deg/s). Replacing the gate with the white-noise floor of the mean (about 0.09 deg/s) would produce spurious spin axes from the offset.

**Fix notes.** Labelling only. Show 'per-sample noise 0.12 °/s; the mean is limited by the unspecified residual gyro offset' (optionally adding the white-noise floor of the mean, RATE_FLOOR_DPS/sqrt(25*nwin)). Keep the spin-axis gate on the conservative figure and say in docs/13 9.4 why.

## ICD-8 [CONFIRMED / nit] docs/05 lists only the first format of EVR 0x060D: the 'not separated' WARN that drives a YELLOW alarm is undocumented
`tools/gen_dictionary_doc.py:124`

**Description.** evrs() keeps one row per id (rows.setdefault). The flight prints two formats under 0x060D (flight.cpp:538 wrong bus, :552 'not separated'), and dictionary.SENSOR_BUS_EVRS lists both. docs/05:623 shows only '%s found on %s; the recommended bus is %s (docs/03)'.

**Failure scenario.** An operator who sees 'Nicla Sense ME and RM3100 outboard both on A4/A5: not separated (docs/03)' and the YELLOW alarm looks up 0x060D in docs/05 and finds only the wrong-bus text.

**Proposed fix.** Collect a list of formats per id and print each one (one row per distinct format), then regenerate docs/05.

**Verifier.** tools/gen_dictionary_doc.py:124 rows.setdefault(key, ...) keeps only the first format per id. The flight has two 0x060D formats (flight.cpp:538 wrong bus, :550 'not separated'), and dictionary.SENSOR_BUS_EVRS lists both (dictionary.py:152-153). docs/05:623 shows only '%s found on %s; the recommended bus is %s (docs/03)'. The 'not separated' text appears only in docs/13:2120/3257 and is absent from docs/05 and docs/14. The same code would also hide a second severity for ids such as 0x0910 (WARN or INFO with the same format).

**Fix notes.** Collect a set of (severity, format) per id, emit one row per distinct pair (sorted for stable output), and regenerate docs/05.

## LS-10 [CONFIRMED / nit] During LOS, /metrics still exports the last pass's noise floor and N0 as current gauges
`dssq-groundstation/python/dssq/display/web.py:352`

**Description.** LIVE_RF_KEYS keeps last-reception values out of LOS exports. The E22 noise is queried only after a received frame (gds._module_answering docstring). So during LOS, rf.noise_dbm and rf.n0_est_dbm_hz are the last pass's values, frozen, but they are not in the set. Neither are budget.uplink.measured_* from the last RF packet.

**Failure scenario.** Hours into LOS, dssq_rf_noise_dbm and dssq_rf_n0_est_dbm_hz keep the last pass's values as a flat line. A noise-floor dashboard or alert sees a steady floor while nothing is being measured.

**Proposed fix.** Add 'noise_dbm' and 'n0_est_dbm_hz' to LIVE_RF_KEYS (keep noise_age_s). Skip budget_uplink measured_* during LOS. Update docs/13 section 16.

**Verifier.** web.py:352-354 LIVE_RF_KEYS omits noise_dbm and n0_est_dbm_hz. Noise queries go out only in the post-frame uplink window (gds.py:1275-1287; gds._module_answering docstring 2302-2303), so during LOS rf.noise_dbm and n0 (rxstats.py:882 uses self.noise_dbm, not the paired value) freeze at the last pass's reading. They are exported as current gauges. budget_uplink measured_* (web.py:411) are exported during LOS too: LIVE_BUDGET_KEYS covers only top-level budget keys. Mitigation: dssq_rf_noise_age_s is exported and grows, so the staleness is detectable. docs/13 section 16 promises exclusion only for 'values of the last reception' and the top-level budget, so nothing is contradicted. Hence nit.

**Fix notes.** Add 'noise_dbm' and 'n0_est_dbm_hz' to LIVE_RF_KEYS, or better, skip them whenever noise_age_s > pair_s, which also covers the early AOS period. Apply the LOS skip to the budget_uplink measured_* keys. Update docs/13 section 16 accordingly.

## LS-14 [CONFIRMED / nit] OVERVIEW Frames-tile example uses 2 significant figures; the page shows 3
`docs/13_Web_UI_Reference.md:497`

**Description.** The example reads 'FER 0/344 ≤ 0.87 %'. fpct3() uses toPrecision(3), and 1 - 0.05^(1/344) = 0.867 %, so the tile shows '0.867 %'.

**Failure scenario.** An operator comparing the page with the documentation sees '0.867 %', not '0.87 %'.

**Proposed fix.** Change the example to '0/344 ≤ 0.867 %'.

**Verifier.** rxstats.fer_block(0, 344).one_sided_hi = 0.0086707 (computed in verify-LS). fpct3 (dashboard.html:465) uses toPrecision(3), so the page shows '0.867 %', while docs/13:497 says '0/344 ≤ 0.87 %'. The other examples use 3 significant figures and are consistent: 178 frames gives 1.67 % and 2.05 %, and docs/13:436 and 499 use '1.67 %'.

**Fix notes.** In docs/13:497, change the example to 'FER 0/344 ≤ 0.867 %'.

## LS-3 [CONFIRMED / nit] FER colour uses the point estimate, so 0 lost of a few frames is green even when the bound is up to 95 %
`dssq-groundstation/python/dssq/display/dashboard.html:480`

**Description.** ferText() sets cls from ci.fer only: green when fer <= 1 %. With zero losses fer is 0, so it is always green, whatever the exact upper bound. The header 'FER 10m', the RECEIVER frame rows and the OVERVIEW 'Frames lost (10 min)' tile all use this class. That contradicts the purpose of the bounds ('a FER from a few frames is not a precise number').

**Failure scenario.** First frame of a pass: fer_10m_ci = {lost 0, received 1}. The header shows '0/1 ≤ 95.0 %' in green ('ok'). With 0 of 20 frames, the one-sided bound is 13.9 %, above the 10 % 'bad' threshold, and it is still shown green.

**Proposed fix.** Colour by the one-sided upper bound: bad if one_sided_hi > 0.1, warn if > 0.01, else ok. Or use a neutral class whenever one_sided_hi > 0.01 and lost == 0. Update the docs/13 header and tile colour rules to match.

**Verifier.** dashboard.html:480 sets cls from ci.fer only, so zero losses are always 'ok' (green). rxstats.fer_block(0,1).one_sided_hi = 0.95, so the header reads '0/1 ≤ 95.0 %' in green. fer_block(0,20).one_sided_hi = 0.139 (13.9 %), above the 10 % 'bad' level, and is still green (both computed in verify-LS). This is the documented rule, though: docs/13:436, 499 and 1278 all specify green / amber > 1 % / red > 10 % on the point FER and tell the reader to read the bounds. The bound itself is printed in the same cell. So code and docs agree: this is a presentation weakness, not a contradiction or a wrong value.

**Fix notes.** Optional improvement. Colour by the evidence instead: when lost == 0 and one_sided_hi > 0.01, use a neutral class (or 'warn' when one_sided_hi > 0.1), with the title 'too few frames to show FER <= 1 %'. Otherwise keep the point-estimate bands. Update the docs/13 4.0 / 3.1 / 4.11 colour rules in the same change.

## LS-8 [CONFIRMED / nit] Two-way panel shows the spacecraft noise floor unclassified (-256 dBm possible)
`dssq-groundstation/python/dssq/display/dashboard.html:1651`

**Description.** rx-2way shows fres(tw.sc_noise_floor), the raw RF field. _budget() rejects the same value when classify_rssi() is not OK (rxstats.py:1026-1028), but the dashboard never sees the classified value. A spacecraft E22 noise octet 0x00 or 0xFF, which flight on_noise stores unfiltered, is displayed as a noise floor.

**Failure scenario.** The spacecraft E22 noise register reads 0x00 (strong signal or bench overload). The UPLINK column shows 'Noise floor -256 dBm'. In the same table the uplink RSSI shows OVERLOAD and the station noise row never contains rejected octets.

**Proposed fix.** Export the classified value from _budget (budget.uplink.measured_sc_noise_dbm, None when rejected, plus its state) and display that, with 'n/a (end-of-range octet)' when rejected.

**Verifier.** radio.cpp:343 converts the spacecraft E22 noise octet as octet - 256 (0x00 gives -256). Flight on_noise (flight.cpp:711) stores it unfiltered, and the RF packet carries it as noise_floor_dbm. rx.two_way is {**sc_rf, ...} (rxstats.py:974), so dashboard.html:1651 shows fres(tw.sc_noise_floor) raw. _budget (rxstats.py:1026-1028) rejects the same value with classify_rssi(), but the classified value is never exported. The two-way table would therefore show 'Noise floor -256 dBm' next to an uplink RSSI marked OVERLOAD. As with LS-5, end-of-range noise octets are anticipated by D-76 but not recorded on hardware. Display-only, no derived number uses it, hence nit.

**Fix notes.** Export the classified value from _budget: budget.uplink.measured_sc_noise_dbm (None when rejected) and measured_sc_noise_state. In the UPLINK column of rx-2way, render that value, or 'n/a (end-of-range octet)' when the state is not OK. Optionally also mark the SPACECRAFT-tab RF sc_noise_floor value.

## UI-04 [CONFIRMED / nit] The noise floor shown for |omega| of the MEAN rate is the single-sample noise
`dssq-groundstation/python/dssq/sensors.py:81`

**Description.** RATE_FLOOR_DPS = 0.07 x sqrt(3) = 0.121 deg/s is the |omega| noise of one gyro sample. The dashboard shows it next to "|omega| of the mean rate" (dashboard.html:2077), and the spin-axis n/a reason (sensors.py:254-255) and docs/13:2699 also use it. rate_mean is the mean of 20-25 samples per window (ARU close_window) and then the mean of nwin windows. With independent samples its white-noise floor is 0.121/sqrt(25*nwin): 0.024 deg/s for one window, 0.0086 deg/s for 8 windows. So the displayed 'floor' is 5-14x too high. It is also not the real limit, which is the unspecified residual gyro offset (+/-1 deg/s with fast offset compensation off).

**Failure scenario.** TEST mode, 8 windows, body at rest. The table says "|omega| 0.05 deg/s (noise floor est. 0.12 deg/s)", which suggests the reading is below the noise. The averaging floor is about 0.009 deg/s, and the value that actually limits the reading (the residual offset) is not stated at all.

**Proposed fix.** Either compute and show the floor of the mean as RATE_FLOOR_DPS/sqrt(25*nwin), labelled as an estimate, or relabel the value as "single-sample |omega| noise". In both cases, state next to it that the residual gyro offset (not specified) limits |omega_mean|. Update docs/13 section 9.4 to match.

**Verifier.** RATE_FLOOR_DPS = 0.07*sqrt(3) = 0.121 deg/s (sensors.py:81) is the single-sample three-axis noise. rate_mean is the mean of ng gyro samples per window (nicla_sense_me_aru.ino:303-307: sum/ng, at least 20 samples for STATS), and then the mean of n window means (attitude.cpp:202, 221). Its white-noise floor is therefore 0.121/sqrt(25*nwin): 0.024 deg/s for one window, 0.0086 for 8 (computed). The dashboard row '|omega| of the mean rate ... (noise floor est. 0.12 deg/s)' (dashboard.html:2077), the spin n/a reason (sensors.py:254-255) and docs/13:2699 all present 0.12 as the floor of that mean. The label is wrong. Downgraded to nit: the figure is conservative, the residual offset is already disclosed in the same docs/13 row, and R3 says '|omega_mean| below ~0.2 deg/s indistinguishable from offset', so 0.12 is closer to the practical limit than the white-noise value. The spin-axis threshold (10 x 0.121) is a documented D-75 design choice and is not wrong.

**Fix notes.** Relabel instead of recomputing. In the dashboard and docs/13 9.4, call RATE_FLOOR_DPS 'single-sample |omega| noise est. 0.12 deg/s; the mean is limited by the unspecified residual gyro offset (T-SC-14)'. Reword the spin n/a reason to '... 10 x the 0.12 deg/s single-sample noise est.'. Leave SPIN_AXIS_FACTOR unchanged. If a white-noise floor of the mean is wanted as well, show RATE_FLOOR_DPS/sqrt(25*nwin) separately, labelled as an estimate.

## UI-05 [CONFIRMED / nit] "stationary" is reported for the whole ATT period even when windows were missed (IMU_F_GAP)
`dssq-groundstation/python/dssq/sensors.py:168`

**Description.** motion_state() decides from the windows the flight happened to read. The flight reads page 2 only in its 1 Hz sample, and that sample is deferred while the transmitter is keyed (flight.cpp:2234 `if (tx_state_ != TX_IDLE) return;`). At 2.4 kbit/s (busy 1267 ms, frame period >= 2.5 s at the 50 % duty limit), 1 s windows regularly go unread, so IMU_F_GAP is set in most TEST periods. The motion row and docs/13:2702 still present the verdict as the state of the whole ATT period.

**Failure scenario.** In TEST mode the body moves during windows the flight did not read (they fell inside transmission bursts). The windows it did read are quiet: rate_peak 0.3 deg/s, vib_peak 7 mg. The panel says "stationary" for the period, while the GAP badge sits in another row.

**Proposed fix.** When GAP is set, qualify the verdict, e.g. "stationary in the N windows seen (windows missed)", or return "unknown" with a reason in na['motion']. Document this in docs/13 section 9.4.

**Verifier.** service_mag waits in MAG_WAIT while tx_state_ != TX_IDLE (flight.cpp:2232). Page 2 holds only the last completed 1 s window. ImuStats sets gap_ when win_seq jumps (attitude.cpp:191) but aggregates only the windows it saw. motion_state() (sensors.py:163-168) and the docs ('motion state over the ATT period', sensors.py:36, docs/13:2702) do not qualify the verdict. A Monte Carlo of the flight's sampling (verify-UI/ui05.py: 1 Hz tick deferred to TX end, random phases) gives at least one unread window in 97 % of 8 s periods at busy 1.267 s / frame period 2.53 s, 75 % at 3.0 s, and 0 % at busy 0.3 s / 1.0 s. So GAP is indeed routine at 2.4 kbit/s. Impact is small: usually 1-2 of 8 windows are missed, the GAP badge is in the same table ('windows or samples missed'), and only a motion confined to a missed window is invisible. Graded nit.

**Fix notes.** When imu.gap is set, qualify a 'stationary' verdict on the ground. Add na-style text, or a motion_note such as 'stationary in the <nwin> windows read (windows missed: a short motion between them is not seen)', in sensors.py and render it in the Motion state row. Document this in docs/13 9.4. Do not change the verdict logic itself.

## UI-06 [CONFIRMED / nit] The simulator sets IMU_F_STATS with imu_nwin = 0
`dssq-groundstation/python/dssq/sim.py:1255`

**Description.** _att always sends imu_flags = IMU_FLAGS_SIM = 0x47, which includes STATS (b1). When _imu_stats finds no completed window it returns {'imu_nwin': 0}. The flight sets STATS exactly when windows() != 0 (flight.cpp:1863), so the simulator sends a combination the flight never produces.

**Failure scenario.** First ATT after simulator start or a software reset (SCLK < 1 s), or two ATTs within one SCLK second: STATS is set while imu_nwin is 0 and the statistics are sentinels. The ground copes with it, so this is not caught.

**Proposed fix.** When imu_nwin == 0, send IMU_FLAGS_SIM & ~0x02.

**Verifier.** sim.py:1255 always sends imu_flags = IMU_FLAGS_SIM = 0x47 (STATS b1 included), and _imu_stats returns {'imu_nwin': 0} when no window completed (sim.py:1237-1238). The flight sets STATS iff windows() != 0 (flight.cpp:1863). Reachability is narrower than claimed. 'Two ATTs within one SCLK second' is impossible: the scheduler requires t - last_gen >= p >= 1 (sim.py:647-650), which implies floor(t) > floor(last). A first ATT with SCLK < 1 s needs ATT enabled within the first second after boot or reboot, which boot in SAFE with ATT off (sim.py:366, CADENCE). I did reproduce it with a PKTRATE ATT override applied 0.5 s after boot (verify-UI/ui06.py): imu_flags 71 (0x47) with imu_nwin 0 and sentinel statistics. The ground handles it (sensors.py:242 checks both).

**Fix notes.** In SimSpacecraft._att: stats = self._imu_stats(t); v['imu_flags'] = IMU_FLAGS_SIM if stats.get('imu_nwin') else IMU_FLAGS_SIM & ~0x02. Optionally add a test that STATS == (imu_nwin != 0).

## UI-07 [CONFIRMED / nit] docs/05 EVR table leaves out the 0x060D "not separated" WARN text
`tools/gen_dictionary_doc.py:124`

**Description.** evrs() keeps only the first format per event id (rows.setdefault). 0x060D has two flight formats: the wrong-bus WARN and "%s and %s both on %s: not separated (docs/03)" (flight.cpp:550). docs/05 line 623 lists only the first, so the WARN behind the YELLOW alarm "sensor wiring: RM3100 and Nicla not on separate buses" is missing from the EVR reference.

**Failure scenario.** An operator looks up the EVR text "... both on A4/A5: not separated (docs/03)" in docs/05 and does not find it.

**Proposed fix.** Collect every distinct format per id (e.g. dict[str, list]) and print one row per format; regenerate docs/05.

**Verifier.** tools/gen_dictionary_doc.py:122-124 builds rows with rows.setdefault(key, ...), which keeps only the first literal format per event id. The flight has two 0x060D formats (flight.cpp:538 wrong-bus, flight.cpp:550 '%s and %s both on %s: not separated (docs/03)'). docs/05:623 lists only the wrong-bus format. grep shows that the not-separated text appears only in docs/13 and in no EVR reference table. dictionary.SENSOR_BUS_EVRS correctly carries both formats, so only the generated doc is incomplete.

**Fix notes.** In evrs(), collect dict[str, list[tuple[sev, fmt]]], append each distinct (sev, fmt), and print one row per format (repeating the id). Then regenerate docs/05. The same change also covers any other id with more than one format.

## UI-08 [CONFIRMED / nit] The sigma multiples given for still_rate_dps / still_vib_mg are wrong
`docs/15_Configuration_Reference.md:366`

**Description.** docs/15:366-367 call the thresholds about 3 sigma (rate) and about 3.5 sigma (vibration) of the three-axis datasheet noise, and docs/13:2702 says 'about 3 sigma'. With the same docs' noise values (0.07 deg/s and 1.2 mg per axis, i.e. 0.121 deg/s and 2.08 mg over three axes), 0.5 deg/s is 4.1 sigma and 10 mg is 4.8 sigma. The 3 sigma / 3.5 sigma values would be 0.36 deg/s and 7.3 mg (R3 section 5.9).

**Failure scenario.** An operator tuning the thresholds from the docs sets still_rate_dps = 0.36 ('3 sigma'). The peak of 200 or more samples of a 3-D Gaussian magnitude at rest reaches about 3.6-4.2 sigma, so a body at rest is flagged as moving.

**Proposed fix.** Write "about 4 sigma (0.5 / 0.121)" and "about 5 sigma (10 / 2.08)", and note that a peak over many samples needs that margin.

**Verifier.** docs/15:366-367 say still_rate_dps 0.5 is 'about 3 sigma of the three-axis gyro noise (0.07 deg/s per axis)' and still_vib_mg 10 'about 3.5 sigma' of 1.2 mg per axis. docs/13:2702 says 'about 3 sigma'. With the three-axis values the same docs use (0.121 deg/s, 2.08 mg), 0.5 is 4.12 sigma and 10 is 4.81 sigma (computed). R3 section 5.9 derived 3 sigma = 0.36 deg/s and 3.5 sigma = 7 mg and then chose 0.5 and 10, so the docs copied the sigma words but not the chosen values. The finding's failure scenario is overstated. Simulation of the peak of a 3-D Gaussian magnitude: for 200 samples the median is 2.13 and the 95th percentile 2.51 three-axis sigma (3.7 / 4.3 per-axis sigma); P(peak > 0.36 deg/s) is about 0.05 % for 200 samples and 1 % for 2000. So a 0.36 threshold would rarely flag a body at rest under datasheet white noise. The doc contradiction is real; the claimed consequence is not.

**Fix notes.** Correct the text in docs/15 and docs/13 9.4: 'about 4 sigma (0.5 / 0.121)' and 'about 5 sigma (10 / 2.08) of the three-axis datasheet noise, margin for the peak over many samples and the unspecified 25 Hz noise, VERIFY T-SC-14'. No code change.

## UI-09 [CONFIRMED / nit] The 'older ATT without the 6-DOF part' case cannot come out of the GDS
`tests/test_sensors.py:156`

**Description.** imu_products has a path for an ATT without imu_flags ("imu_flags missing: no 6-DOF part in this ATT"), and test_sentinels_give_reasons_never_numbers tests it with a hand-made dict. The Decommutator rejects a 37-octet ATT v3 ("ATT: 37 octets of user data, the layout has 59"), so the whole packet is dropped as a decom error, including its v3 attitude fields.

**Failure scenario.** A 3.1 ground with a 3.0 flight: every ATT gives a DECOM WARN, the Attitude panel shows 'no ATT packet yet', and the IMU panel says "no ATT packet yet" instead of the tested reason. The test passes but does not cover the real behaviour.

**Proposed fix.** Either decode a v3-length ATT as the 37-octet prefix (fields 0-36 present, 6-DOF absent) so the documented reason appears, or remove the unreachable branch and the test claim.

**Verifier.** Decommutator.decode rejects any ATT whose length differs from the 59-octet layout (decom.py:146-148). Reproduced in verify-UI/ui09.py: a 37-octet ATT raises 'ATT: 37 octets of user data, the layout has 59'. Through the GDS a v3 ATT is therefore a decom error, the latest ATT stays None, and imu_products shows 'no ATT packet yet'. The 'imu_flags missing: no 6-DOF part in this ATT' branch (sensors.py:198) is reachable only with a hand-made dict, which is what test_sensors.py:156-158 uses. Neither the contract nor the docs promise 3.0-flight compatibility (grep found nothing), so this is dead-branch and test-claim hygiene, not a functional regression.

**Fix notes.** Simplest: remove the claim from the test comment, or keep the branch as defensive code and rename the test comment to 'a dict without imu_flags (defensive; the Decommutator rejects a v3-length ATT)'. Decode the 37-octet prefix only if mixed 3.0/3.1 operation is a requirement. It is not one now.

## UI-10 [CONFIRMED / nit] The docs understate the simulator's tumble rate
`docs/13_Web_UI_Reference.md:2722`

**Description.** docs/13 says the simulator tumbles at 5-8 deg/s. For the _rot tumble, |omega|^2 = psi'^2 + theta'^2 + phi'^2 - 2 psi' phi' sin(theta), which gives 5.4 to 10.1 deg/s. The simulated ATT shows rate_peak up to 10.3 deg/s.

**Failure scenario.** A reader checking the simulator example finds |omega| and peak values above 8 deg/s and suspects an error.

**Proposed fix.** Change the text to "5-10 deg/s".

**Verifier.** TUMBLE_RAD_S = (0.11, 0.073, 0.051) rad/s (sim.py:116). Sampling |_body_rates(t)| over 0..20000 s gives a range of 5.38 to 10.13 deg/s (computed). docs/13:2722 says '5-8 deg/s'.

**Fix notes.** Change docs/13:2722 to 'about 5-10 deg/s'.

## UI-11 [CONFIRMED / nit] The game rotation vector row says 'as sent' but shows the normalised quaternion
`dssq-groundstation/python/dssq/display/dashboard.html:2063`

**Description.** imu.q_grv is unit_quat(), i.e. divided by its norm (sensors.py:118-119). The flight gate lets norms of 0.9-1.1 through. The row label says "(device->GRV world, as sent)", while docs/13:2688 correctly says 'normalised'.

**Failure scenario.** An ARU sends q_grv with norm 1.05 (inside the gate). The table shows components about 5 % smaller than the telemetry under a label that says 'as sent'.

**Proposed fix.** Relabel the row "(device->GRV world, normalised)".

**Verifier.** imu.q_grv is unit_quat(), i.e. divided by its norm (sensors.py:118-119). The row label reads 'Quaternion w x y z (device→GRV world, as sent)' (dashboard.html:2063). On the same dashboard, the ATT panel's '(as sent)' row shows raw telemetry values (dashboard.html:2152), and docs/13:2688 says 'normalised'. The label contradiction is real. In practice it is invisible: the BHI260AP fusion output is a unit quaternion and Q14 rounding moves the norm by about 1e-4, below the 4-decimal display. The 'norm 1.05' scenario would need corrupt data that still passes the gate.

**Fix notes.** Relabel the row 'Quaternion w x y z (device→GRV world, scalar first, normalised)'. No code change.
