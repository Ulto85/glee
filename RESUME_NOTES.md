# RESUME NOTES — shutdown 2026-08-21 ~08:23

## SHUTDOWN STATE (done 08:23)
- **All 3 farms DRAINING** (0-forfeit): `logs/drain_theta`, `logs/drain_champion`, `logs/drain_gamma` placed. Exit clean at next batch boundary (~2-6 min).
- **Both crons CANCELLED** (tick + audit, session-only). Must RE-CREATE on resume.
- caffeinate: a persistent `caffeinate -dimsu` (holds DISPLAY sleep, PreventUserIdleDisplaySleep=1) is running — leave it; it's harmless.

## TO RESTART (when back + online)
1. `cd /Users/aayanrizvi/Documents/glee`
2. `bash safe_relaunch.sh status` — want all DEAD (wait for batch boundary if any still ALIVE).
3. `rm -f logs/drain_theta logs/drain_champion logs/drain_gamma`  ← REMOVE sentinels FIRST or farms re-drain.
4. `bash safe_relaunch.sh` — revives DEAD farms one at a time (120s anti-bunch cooldown, so run it ~2-3x over ~5 min until all 3 ALIVE; NEVER batch-restart/conc20+).
5. Verify: all ALIVE + exactly one process per agent.
6. RE-ARM crons (CronCreate): tick `3-59/5 * * * *`, audit `8,23,38,53 * * * *`. **Use the HARDENED caffeinate check** (verify `pmset -g assertions | grep 'PreventUserIdleDisplaySleep +1'` AND `pgrep -f 'caffeinate -dimsu'`, not just any caffeinate — a weak `-i -t NNN` one does NOT hold the display). Reuse the exact prompt bodies from this session's cron history.
7. `date +%s > logs/loop_start_ts` (reset 5h watchdog).

## LIVE EXPERIMENT IN FLIGHT — gamma neg L1+r9 (~18h in as of shutdown)
- **gamma (params_good.json) = LAB**: `neg_h0_floor_decay=1` (L1, +~50 neg prize) + `neg_r9_foldaware=1` + F1 pers. **theta (params_prminfix.json) = CLEAN CONTROL + rank carrier (PROTECT — no neg levers). champion (params_champ_h2.json) = F1 pers.**
- **STATUS as of shutdown**: L1 behavioral CONFIRMED all session — gamma CI-h0 accept-rate consistently 3-14× theta-control (theta ~1-3%, gamma ~4-14%). NO crater. gamma-neg RATING neutral-to-slightly-positive vs controls (occasionally edges +25-30 above them); the clean +50 hasn't decisively shown in leaderboard EWMA yet (noisy, diurnal). Verdict still maturing — was targeting a ~24-48h read.
- **READ ON RESUME**: gamma neg vs theta neg each audit; behavioral = gamma CI-h0 accept-rate. **REVERT if crater**: `cp params_good.pre_h0decay.json params_good.json` (drops L1) or `params_good.pre_r9.json` (drops both) + drain+relaunch gamma. Backups exist.

## KEY CONTEXT
- **Diurnal swing is NORMAL** (user asked twice): rating = EWMA of percentile-vs-field, swings ~100pt/12h. Afternoon/evening PEAKS ~2255-2290, pre-dawn TROUGHS ~2150-2170. We rode #4→#8→recovering across the night on theta with ZERO param changes. Judge trend PEAK-to-PEAK, not snapshots. Don't panic-revert in a trough (stand-down rule #54).
- **Fable neg audit (2026-08-20)**: tail-toughness/"copy their never-fold" thesis = NULL (their folds never come to us). +EV levers ranked: L1 (deployed) > L2 neg_h0_deepclose (+13-38, QUEUED, live-only) > L4 ultimatum 1.22→1.19 (QUEUED). Ceiling ~+70-190 neg → frontier, NOT their 2400-2650 (structural). Plus a neg STRIP/KEEP list. Full detail: memory [[glee-neg-tail-toughness]].
- **Log hygiene**: games_*.jsonl trimmed to last 500k lines each 2026-08-21 (was 7.75G→6.9G). Safe live via tail+mv (memory.log_turn opens per-write). Trim again if du logs > ~7.8G.
- Task #103 cloud migration parked. Standing rules: #54 stand-down-restarts, #66 defend, #86 carrier-protection.
