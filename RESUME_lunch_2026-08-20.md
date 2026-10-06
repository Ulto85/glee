# RESUME NOTES — lunch wind-down 2026-08-20 ~14:06

## SHUTDOWN STATE (done at 14:06:41)
- **All 3 farms DRAINING** (0-forfeit): `logs/drain_theta`, `logs/drain_champion`, `logs/drain_gamma` sentinels placed. They exit clean at next batch boundary (~2-6 min).
- **Both crons CANCELLED**: tick `35a5a06d` + audit `b7ff3748` deleted (session-only). Must RE-CREATE on resume.
- caffeinate left running (harmless).

## TO RESTART (when back + online)
1. `cd /Users/aayanrizvi/Documents/glee`
2. Confirm farms fully drained: `bash safe_relaunch.sh status` (want all DEAD) — if any still ALIVE, wait for batch boundary.
3. `rm -f logs/drain_theta logs/drain_champion logs/drain_gamma`  ← REMOVE sentinels FIRST or the relaunched farms immediately re-drain.
4. `bash safe_relaunch.sh` (dead-only, detached, conc 12) — revives all 3.
5. Verify: `bash safe_relaunch.sh status` all ALIVE; confirm one process per agent (no dupes).
6. RE-ARM crons (CronCreate): tick every 5min `3-59/5 * * * *`, audit `8,23,38,53 * * * *`. Reuse the exact prompt bodies from this session's cron history.
7. `date +%s > logs/loop_start_ts` (reset 5h watchdog clock).

## LIVE EXPERIMENT IN FLIGHT (the important part) — gamma neg L1+r9
- **gamma (params_good.json) = LAB**, running `neg_h0_floor_decay=1` (L1, the +~50 neg prize) + `neg_r9_foldaware=1` (small rider) + F1 pers (`buyer_liar_reentry=1`). Deployed+verified 14:04 (fresh PID 12444 loaded both, THEN drained for lunch).
- **theta (params_prminfix.json) = CLEAN CONTROL + current RANK CARRIER (~2229, barg #2). PROTECT — do not add neg levers.**
- **champion (params_champ_h2.json) = F1 pers full-on carrier.**
- **READ ON RESUME**: gamma neg rating vs theta neg (control) over 24-48h; behavioral = gamma CI-h0 accept-rate UP vs theta. L1 offline-validated: 38.8% of gamma's late CI-h0 rejects flip reject→accept.
- **REVERT if crater**: `cp params_good.pre_h0decay.json params_good.json` (drops L1) or `params_good.pre_r9.json` (drops both L1+r9), then drain+relaunch gamma. Backups exist: `params_good.pre_h0decay.json`, `params_good.pre_r9.json`, `params_good.pre_reentry.json`.

## FABLE NEG AUDIT (2026-08-20) — full results in memory [[glee-neg-tail-toughness]]
- **Tail-toughness / "copy their never-fold" thesis = NULL & INVERTED.** No-deal mass sits at pct 0.24-0.26 (bottom, not median); any deal vaults it; their folds never come to us (need p*=0.43-0.90, observed 0.02-0.27). DO NOT ship walk-more/hold-higher. `neg_accept_keep=0.02` thin accepts are our most valuable behavior — KEEP.
- **+EV levers ranked**: L1 neg_h0_floor_decay (DEPLOYED gamma ✓) → L2 neg_h0_deepclose (+13-38, live-only, QUEUED) → L4 ultimatum 1.22→1.19 (+0-90, QUEUED). Total ceiling ~+70-190 neg → ~2120-2270 (frontier, NOT their 2400-2650 which is structural/unreachable).
- **NEG STRIP/KEEP list** (reliability cleanup, do later): FIX `neg_scale_fix` (archetype _scale()=100 bug injects noise into non-CI opens). STRIP dead-by-config: neg_conc_alpha (dead if v2), LLM hybrid, belief machinery, neg_field_anchor, neg_stall_hold, neg_walk_accept, neg_guaranteed_close, neg_opp_posture. KEEP the CI 0.98-target spine + accepts + ultimatum + keep_floor.

## OTHER OPEN THREADS
- **Pers ship-to-theta**: NOT triggered — needs champion+gamma pers BOTH clearly above theta pers over a full diurnal cycle (gamma pers keeps lagging theta). Hold theta clean.
- Task #103 cloud migration: parked ("cumbersome").
- Standing rules: #54 stand-down-restarts, #66 defend-don't-freeze, #86 carrier-protection.
