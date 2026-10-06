# GLEE Competition — Situation Brief for External Audit
**Date:** 2026-08-24 (~11:30 local) · **Deadline:** 2026-08-29 · **Status: we have slipped to ~#13 overall and want a second opinion on WHY and WHAT TO DO.**

This document is a cold-readable handoff for a fresh model. It is written to be self-contained and honest, including where the operating model (me) may have mis-prioritized. Please audit the diagnosis in §6 and the plan in §9 skeptically.

---

## 1. The ask (what we need from you)
1. **Diagnose "what happened":** we were around top-5 to top-8 at diurnal peak days ago; we are now ~#13. Rivals (Clod, Mythos01, etc.) held their positions. Is our diagnosis in §6 correct, or are we missing something (a bug, a metric misunderstanding, a self-inflicted regression)?
2. **Sanity-check the endgame plan (§9)** given ~5 days to the deadline.
3. **Flag anything that looks like a measurement artifact vs. a real drop.**

---

## 2. Competition setup (the game)
- **GLEE benchmark**, 3 game families: **bargaining, negotiation, persuasion**. Each is a multi-round economic game vs. other submitted agents.
- **Rating per family** = an EWMA of our per-game **percentile** vs. the field (not absolute payoff). Ratings > ~1800 decay if idle; unplayed family defaults to 1000.
- **Account overall rating = MEAN of the 3 family ratings** (unplayed family = 1000). This is confirmed from the official site (glee-competition.com/llms.txt) and board screenshots.
- **Account rank = the single BEST of our up-to-5 agents' mean-of-3.** So only our strongest agent's balanced 3-family average counts.
- **Specialization is a trap:** dropping a family to spike another swaps a ~2100 score for a 1000 default → craters the mean. All our agents play all 3 families. This is structurally correct and settled.
- **Ratings are a moving target:** strong diurnal swing (see §5) and the field is actively climbing (§6).

## 3. Our infrastructure
- **5 agents ("farms"), each a long-running `farm.py` loop** playing all 3 families continuously, sharing `tw/policy.py` (a heuristic policy) but each loading its own `params_*.json`:
  - **champion** = `params_champ_h2.json` (carrier)
  - **gamma** = `params_good.json` (carrier)
  - **theta** = `params_prminfix.json` (carrier)
  - **delta**, **eta** = clones/lab vehicles (`params_delta.json`, `params_eta.json`)
- Policy is **pure heuristic** (Rubinstein-style bargaining thresholds, negotiation anchor/accept rules, persuasion KG seller + Bayesian buyer). No LLM in the live loop.
- Ops harness: `killcheck.sh` (5h watchdog), `safe_relaunch.sh` (revives only DEAD farms, conc 12, 120s cooldown — built after a 2026-08-13 restart-spree that caused a 429 rate-limit crash), `caffeinate` (keeps the laptop awake), `experiments/peak_poller.py` (per-family ratings), `experiments/leaderboard.py` (our rank within each family's field).

## 4. Current hard numbers (2026-08-24 ~11:30, a DIURNAL TROUGH)
**Our farms, mean-of-3 (current / trailing-24h peak):**
| agent | cur mean | 24h-peak mean | barg cur/peak | neg cur/peak | pers cur/peak |
|---|---|---|---|---|---|
| gamma | 2110 | 2264 | 2085/2545* | 2155/2158 | 2092/2178 |
| theta | 2110 | 2261 | 2125/2684* | 2083/2218 | 2122/2133 |
| champion | 2064 | 2260 | 2104/2440* | 2100/2468* | 1988/2159 |
| eta | 2067 | 2247 | 2141/2500* | 2088/2163 | 1972/2128 |
| delta | 2065 | 2217 | 2099/2382 | 2138/2170 | 1959/2159 |

*Per-family "peak" figures are trailing-window highs and include spiky low-volume reads; treat the mean columns as the reliable signal.

**Our rank WITHIN each family's field (leaderboard.py, individual-agent):**
- **bargaining:** field top=2567, top10=2199, floor50=2105 · our best = theta **#44 @2125**
- **negotiation:** field top=2911, top10=2267, floor50=2074 · our best = gamma **#27 @2155**
- **persuasion:** field top=2556, top10=2160, floor50=2092 · our best = theta **#34 @2122**

**Account overall (mean-of-3, user-reported from official board): ~#13.** Reconstructed: our best agent's mean is ~2110 at trough, ~2263 at peak. The #5 overall line has historically been ~2347 and rising.

## 5. Diurnal context (critical to interpret the rank)
- Our per-agent mean swings **~150–240 pts/12h** (peak ~2255–2290, trough ~2110–2170). Peaks ~19:00–03:30 local, troughs ~06:00–14:00.
- **Rivals swing far less** (adjacent rivals observed moving <±26 over 21h vs. our 150–240/day). This is the single most important asymmetry: **at trough we fall much further than they do, so our RANK craters at trough even when our PEAK is competitive.**
- The board the user saw showing #13 is at/near a trough, AND (see §6) this particular cycle's peak has been running shallow.

## 6. Candidate causes of the drop (our diagnosis — please audit)
Ranked by our confidence:

**A. Structural: our rank is peak-dependent and we're at a trough.** Highest confidence. At peak we're ~#6–8; at trough ~#13. Rivals' flatter curves mean rank comparisons at trough are unfavorable. (Evidence: §5, and repeated peak/trough leaderboard reads over days.)

**B. Secular: the field is climbing while our ceiling is ~flat.** High confidence. Rivals observed climbing +50–370/day (e.g. Clod's negotiation 2484→2630 over days; field #5 line rising ~+30–50/day). Our peak mean has been ~flat around 2260. So even our PEAK rank slips a few places per day. We have exhaustively searched for a copyable "spike" and found none (see §7); the top of each family is largely opponent-mix rent, not a copyable trick.

**C. Acute #1 — LARGELY EXPLAINED (updated 11:45): PERSUASION field-erosion is the main driver of the drop.** HIGH confidence now. Offline time-sliced field analysis shows the field's *binary persuasion sellers* went push-heavy starting ~Aug-22 (near-full-extraction sellers 0.30→0.48; field-seller pool units p1/3-bin 5.9→8.3, p0.5-bin 9.8→12.9). Our persuasion seller is unchanged, so our per-game percentile in those cells collapsed. Rating impact is real and large: **carrier persuasion daily means fell 2190–2236 → 2051–2088, night peaks 2361–2410 → 2139–2184 across all 3 carriers** — same signature as the earlier d0.9-barg collapse. Because account rank = mean-of-3 and persuasion is our weakest leg, a −100–150 pers hit drags the whole account mean down ~to #13. **This explains the "shallow peak" anomaly and why rivals held (their exposed leg wasn't persuasion).** It is field-side (opponents changed), not a self-inflicted regression. **Counter search now EXHAUSTED (12:07): the last candidate (p0.5-binary full-push) was tested offline and is DEAD (ratio-draw-composition artifact, net ~0); the persuasion seller push axis is closed at all p. There is NO heuristic seller-side counter to this erosion.** The only response that touches it is the LLM persuasion class-jump (§7, blocked on the user's API key). This is the crux: our weakest, now-bleeding leg cannot be defended with heuristics.

**D. Acute #2: barg mr12 known-horizon field-erosion.** Medium confidence, newly found today. Offline analysis shows the field converging into our bank-critical bargaining cell (δ0.95, known-horizon, "mr12", ~12.4% of bargaining): our per-game percentile there fell ~0.54→0.48 over ~2 days across 4/5 farms, field-side (opponents closing earlier), trajectory ~−50 to −100 barg-cell-pts by the deadline if unchecked. Bargaining is our strongest family, so erosion there pulls the mean down. **A counter is being shipped right now (§8).**

**NOT a likely cause: self-inflicted regression.** The live param/policy changes this session were all offline-validated; the one actively-harmful legacy gate found (`neg_h0_deepclose` on gamma, ~−15–30 neg) is queued for removal but predates the drop. The barg ship in flight (§8) is too recent (started ~11:13 today) to have caused a multi-day slide. Please still check this independently.

## 7. What we've tried (honest lever history)
Over ~2 weeks: dozens of heuristic levers, each offline-validated, most small (+2 to +30 pts):
- **Bargaining:** discount-forward accept, deadline-SPE endgame, δ-advantage floor-guard (R1), hz-bank (δ0.95 unknown-horizon accept). These lifted barg to our strongest family (peaks ~2500–2680 per-family).
- **Negotiation:** horizon-aware CI accept, ultimatum reprice (mr1 tier cap, validated +10–15), field-style anchor+glide, guaranteed-close. Neg is our structural ceiling (~2120–2270 frontier).
- **Persuasion:** KG seller + Bayesian buyer, liar-gates, kg_margin, push-timing riders. Persuasion is our weakest/floor family (~2080–2160).
- **Extensive opponent-mining** of the top climbers (Clod/Priori/Tiberius, 6 consecutive rivals): conclusion = their high family ratings are **tier-mix rent** (they get matched into softer fields at their rating), NOT copyable single policies. In head-to-head we are near-parity or ahead.
- **The one identified "class jump"** that could close the ~+200 gap to top-5 is **LLM-based persuasion/negotiation messaging** (replacing heuristic message selection with an LLM). This is **blocked on the user providing an Anthropic API key + a small inference budget.** It has not been attempted.

**Honest self-critique for you to weigh:** the operating model (me) spent heavily on incremental heuristic tuning — each lever validated but small — while the field climbed +50–370/day. The aggregate of our gains has not kept pace. The heuristic frontier appears ~exhausted (many recent offline studies return "dead/wash/already-optimal"). If the real answer is "heuristics can't reach top-5 and only the LLM class-jump can," we may have under-pushed the user to unblock that path sooner.

## 8. What is LIVE / IN-FLIGHT right now (as of writing)
A **bargaining erosion-counter ship** is mid-execution (staged, one farm at a time, ≥1 carrier always up — the safe pattern, NOT a batch restart):
- **Code:** `tw/policy.py` line 607 gate changed from `(not known)` to `(not known or P.get("barg_hz_bank_known"))` — extends the validated hz-bank accept to the eroding known-horizon d0.95 cell. Backward-compatible (inert unless a param sets the flag). Backup: `tw/policy.py.pre_hzknown_0824`. Verified: parses + imports clean.
- **Params being shipped:** `barg_hz_bank_known=1` (the counter to §6-D, offline ~7σ, +8–16 barg on that cell) + `barg_dadv_min 0.05→0.045` (a genuine IEEE-float bugfix: the δ-advantage guard's (0.95,0.90) pair was silently dead because 0.95−0.90 < 0.05; +2–5 barg, zero-risk).
- **Progress:** theta ✅ shipped & booted clean; **champion draining now** (0-forfeit graceful drain); **gamma next** (also gets the full hz-bank add — it currently lacks it). Backups: `params_*.pre_hzknown_0824.json`.
- **Explicitly EXCLUDED** from this reactive ship (deferred to a deliberate window): neg/pers riders, and **`neg_ult_tier12` was dropped because it has 0 code refs** (would ship inert). Per-lever code presence was verified before shipping.

## 9. Endgame plan (please critique)
- **Short term:** finish the barg ship (champion, gamma) to stop the mr12 erosion + bank the validated barg gains before the field converges further.
- **Aug 25–26:** ship the remaining validated riders in one clean reload per agent (neg mr1-tier extension, pers push-timing, remove the harmful `neg_h0_deepclose` on gamma, revert dead d0.9 rung on eta). Est. aggregate +15–40 across families.
- **Aug 28→29 (deadline eve):** the snapshot must catch our diurnal PEAK. Plan: per-family "freeze" the best carrier's peaked legs (a proven zero-drift hold) during the 19:00–03:30 peak window so the final snapshot banks a peak, not a trough. This is worth ~150–240 pts vs. banking at a trough.
- **The only identified top-5 path (class jump):** wire an LLM into persuasion (and re-try negotiation) messaging on a clone. **Blocked on the user's API key + budget.** Everything else is incremental.

**The hard truth we want you to confirm or refute:** with heuristics apparently maxed and the field climbing, our realistic ceiling looks like ~#6–8 at peak / ~#13 at trough, and a genuine top-5 hold likely requires the LLM class-jump. Is that right, or is there a lever/return we're missing (e.g., is cause 6-C a fixable regression that would recover a chunk of rank)?

## 10. Where to look (files/data for the auditor)
- `tw/policy.py` — the live heuristic policy (bargaining accept logic around lines 595–615; the hz-bank gate is line 607).
- `params_{champ_h2,good,prminfix,delta,eta}.json` — per-agent params.
- `logs/research_journal.md` — full RUN LOG of ~30 offline studies (each dated, with method + number + verdict) and a "🔔 FLAGS FOR HUMAN" section. This is the richest evidence trail.
- `logs/games_*.jsonl` — raw game records per farm (large; sample, don't full-read).
- `logs/*_baseline.jsonl` — per-game outcomes used for offline percentile scoring.
- `experiments/leaderboard.py`, `experiments/peak_poller.py` — the measurement tools.
- Memory notes (external): `glee-metric-and-true-rank`, `glee-consolidated-ship-plan`, `glee-freeze-vs-throttle`, `glee-r1-shipped-spikehunt-done`.

---
*Prepared by the operating model (Claude) mid-session. Farms are healthy (5/5 alive); the barg ship is in flight and safe. Nothing in this brief is speculative unless labeled "candidate/medium confidence."*
