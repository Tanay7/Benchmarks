# Resume notes (paused 2026-10-17 ~19:10 UTC for the owner's session limit)
NAMING: this folder "v4" is only my internal label for the UNO Q branch contract. The owner's
"version 4" is the later feature list in ../version4/PLAN.md. Software on both branches still says
3.0 (flight 3.0, dssq 3.0.0). OWNER DECIDED (2026-10-17): UNO Q branch = 3.1, features = 4.0.
Drop 1 must bump: flight.cpp:321 "BOOT VGQ-1 FSW 3.1" (+ dictionary EVR format, sim.py:300/866,
test_integration.cpp:534, regenerate docs/05), dssq/__init__.py "3.1.0" (+ test_end_to_end.py:131,
test_web.py:53 fixture), station sketch kFw "DSSQ-IO 4.0" -> "DSSQ-IO 3.1-UNOQ" (+ " SX126x" variant;
the original branch's station fw is already "DSSQ-IO 3.1", keep them distinct; update test_station_io.py,
station/test_e22.cpp). CHANGELOG intro + a [3.1.0] section; version examples in docs in Drop 2.
Branch head pushed: 11f87e0 (= f48c9dd merges of WS-A + WS-C, plus the sensor-wiring slice of WS-B:
AS7265X no preferred bus, Nicla/RM3100 separation WARN, dictionary parsers, cross-check tests).
Partial, NOT committed (resume from these):
- WS-B worktree .claude/worktrees/wf_f9407f66-6d4-1 (patch partial/ws1.final.patch): remaining
  unfinished parts = dssq/sensors.py (sensor-bus state), gds.py (alarm reason + snapshot), config.py /
  station.toml ([science] still_* keys). The flight/dictionary/test parts are already on the branch.
  Still to do: products (6-DOF), SCIENCE IMU panel, SPACECRAFT bus row, simulator physics + E22
  no-injection + move the RS assertion to the SX126x world, docs/13 rows.
- WS-D worktree .claude/worktrees/wf_f9407f66-6d4-2 (patch partial/ws2.final.patch): clocksync.py is now ON THE BRANCH (commit after 11f87e0, with tests/test_clocksync.py) - do not re-add it;
  link.py, rxstats.py partially done. Everything else of contract section 5 still to do.
RESUMED 2026-10-17 ~20:30 UTC ("continue 3.1"): workflow wf_b036b95d-345 (script
unoq-31-implement-wf_b036b95d-345.js), branches v31-wsB / v31-wsD from 7b159b8; progress patches
partial/wsB.31.patch, wsD.31.patch (+ .status). Next: merge both, version bump 3.1, review, checks, zips, push.
Old resume plan: new workflow, two agents, each starting from the branch head 11f87e0 and applying its
partial patch (WS-B: only the files listed above), then finishing its stream as in the stopped
workflow prompts (script: unoq-branch-implement-b-wf_f9407f66-6d4.js). Then verify, rebuild dist zips,
push Drop 1; then the docs overhaul (Drop 2).
Later (owner's "version 4", a separate branch cut from the finished UNO Q branch): see
../version4/PLAN.md. Drop 2 should list it in the roadmap so it is on GitHub, not only here.

STATE 2026-10-17 ~22:40 UTC: branch pushed at 98887f5 (merges WS-B, WS-D, version bump 50aad20,
watts, IMU chart). All checks green at 98887f5 (831 passed), real builds rb31/ all OK (flight
rel090/rel100/main + SX, station E22 + SX, 0 unresolved). RadioLib/RouterBridge in the main tree
re-fetched from local clones (scratchpad of -home-user-Benchmarks: libs/RadioLib, realbuild/libsrc/
Arduino_RouterBridge). Review workflow wf_f0ee88c4-731 (script unoq-31-review-wf_f0ee88c4-731.js)
running: 5 lenses -> verify -> fixer (worktree, branch v31-fixes). If the fixer fails to create a
worktree (cwd not a git repo), resume with resumeFromRunId. Then: merge v31-fixes, CHANGELOG 3.1
section + intro, rebuild dist zips + SHA256SUMS (tools/package_apps.py), final checks, push = Drop 1.
Then Drop 2 docs (task #62), and add the 6 DSN-inspired candidates to version4/PLAN.md if the owner says yes.

STATE 2026-10-18 ~01:30 UTC (owner: "spin up more agents"; all approvals given):
Stopped the single fixer. Findings split into scratchpad/v4/fix31/{flight,station,sensors,link,docs,all}.json.
Running in parallel (each agent makes its own worktree under .claude/worktrees/, base 98887f5):
 - fix workflows (script unoq-31-fixgroups-wf_801217af-f75.js): [flight, station] wf_801217af-f75,
   [sensors, link] wf_8b564b3a-672 -> branches v31-fix-<group>; progress fix31/<group>.patch/.status
 - docs workflows (script unoq-31-docs-wf_e97dd2a7-426.js): [W1a,W1b] wf_e97dd2a7-426, [W2,W3]
   wf_6167cb55-fed, [W4,W5] wf_0fb65488-28a, [W6,W7] wf_0281c9b6-95d, [W8,W9] wf_1e4fa730-9fe
   -> branches v31-doc-<W>; W1b writes docs/03_part2.fragment.md to splice at "<!-- DOCS03-PART2 -->"
Then: merge all (fixes first), splice docs/03, regenerate docs/05, docs/13 final pass, reference sweep
(xrefs/code_refs from writers), CHANGELOG 3.1, refresh counts, all checks + real builds, dist zips, push.
