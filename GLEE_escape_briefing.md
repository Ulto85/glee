# GLEE — "Escape the Pack" briefing for Fable

**Read this as a set of working beliefs that MIGHT BE WRONG. Challenge everything. Your job is to help us break out of the #4–7 mid-pack into a durable top-3 (ideally #3), by the Aug 29 2026 deadline.**

## The competition
- GLEE = a benchmark of 3 economic game FAMILIES played by LLM/agent "players": **bargaining** (alternating-offer split, discount δ), **negotiation** (buyer/seller over a price, sometimes known/unknown horizon, complete/incomplete info), **persuasion** (a seller sends signals/messages about product value to a buyer who then buys or not).
- We submit a **policy** (deterministic/heuristic code in `tw/policy.py`, tuned by `params_*.json`) that plays as an agent. We run several agents (theta, champion, gamma + 2 clones delta/eta), each a farm process (`farm.py`) hammering games via the API.
- **Scoring (verified):** each game → our payoff → **percentile vs the field in that same config+role**, opponent-strength-adjusted → game_rating = 2000 + 8000·(pct−0.5) → rating = **EWMA over games** (not time) → the leaderboard "RATING" = the **mean of the 3 family ratings** → an **account is ranked by its single best agent** (≤5 agents/account; other agents keep ratings but don't take a rank).
- **Diurnal:** rating swings ~100 pts / 12h because it's an EWMA of percentile-vs-a-field-that-changes-by-time-of-day. Peaks ~afternoon/evening, troughs ~pre-dawn/midday. Judge peak-to-peak.
- **Decay:** top-100 must play ≥10 games/day or decay; >2500 decays unless defended; abandoned/timeout games score ~5th percentile.

## Where we stand (2026-08-22, midday)
Leaderboard (accounts, best-agent):
1. **gill bates 2445** — barg 2242, **neg 2585, pers 2509**
2. Mythos01 2356 — barg 2351, neg 2410, pers 2308
3. opus 5 2336 — barg 2420, **neg 2539**, pers 2049
4. Chantra 2183 — barg 2175, neg 2086, pers 2288
5. Test 1 2179 — barg 2339, neg 1935, pers 2264
6. Schelling 2157
7. **delta (US) 2156** — barg ~2210, neg ~2120, pers ~2130

Our band across all 5 agents: **~2150 (midday trough) to ~2190 (afternoon peak)**. Our best single peak this morning ≈ gamma 2191.

## Our profile (the core problem)
- **Bargaining is STRONG** (~2210–2370) — competitive with anyone, not the bottleneck.
- **Negotiation ~2100–2130 and Persuasion ~2100–2130 are the DRAG.** Since RATING = mean of 3 legs, our two weak legs cap the average ~2160.
- The top-3 beat us almost entirely on **neg + pers**. gill/opus5 neg is 2540–2585; ours ~2120. gill/Chantra/Test1 pers is 2264–2509; ours ~2130.

## What I currently believe (CHALLENGE ALL OF THIS)
1. **"The top-3's monster neg (2540–2585) is largely STRUCTURAL / uncopyable."** Thesis from prior audits: they play flat high-commitment anchors and harvest *opponent folds* — rents from a weak field folding to them. We already run ~80% of that fold-harvest; the rest is field-specific rent we can't force. Accepting their thin margins (folding TO them) is already our best response on the percentile curve. → I believe neg has a low ceiling for us (~2270 frontier). **This might be defeatist and wrong.**
2. **"gill bates specifically is not crackable from our logs"** — we only have ~43 real head-to-head games vs them; their neg/pers farm-agents likely play under names we can't attribute. So direct copying is out.
3. **"Adaptive per-opponent conditioning has small payoff (~+5–15 neg)"** — opponents DO repeat (top-50 names = 70% of our named games), but our anchor-hold already harvests folds opponent-agnostically, so conditioning adds little. **Maybe the framing is too narrow — maybe there's a bigger per-opponent or per-config play.**
4. **"Persuasion is more movable than neg."** We just shipped a message-bank fix (removed a weak buy-message worth ~+5–15 pers) and have a queued honesty-shade (`pers_kg_margin` 0.13→0.25, +10–30 pers est). But pers ceiling still unclear — Chantra/Test1 hit 2264–2288 pers, gill 2509. **What are THEY doing in pers that we're not?**
5. **"The path to top-3 is: raise the neg+pers band + bank peaks via throttle."** Banking (freeze an agent at a peak so the trough can't erase it) holds the top of our own band (~2190) but CANNOT lift us above it. Only real policy gains lift the band. **Is banking even the right frame, or a distraction from a real edge?**
6. **Meta-belief that might be the real trap:** we've been playing a STATIC heuristic policy tuned by small param nudges. Maybe the pack is where static heuristics land, and escaping requires a categorically different move (per-config specialization, an LLM-in-the-loop mover on the hard cells, exploiting a scoring/matchmaking mechanic, a dedicated single-family spike agent, etc.).

## The data (logs — sample, never full-read; files are 0.5–1.8 GB)
- `logs/games_{theta,gamma,champion,delta,eta}.jsonl` — per-turn logs; ~50% of games carry the **opponent's name** (null = house bot). Inspect the JSON schema first (python: read a few lines, print keys) — fields include role, config/cell, offers, messages, actions, outcomes, payoffs, opponent id. NOTE: accepts may not appear in `history` — reconstruct outcomes from the final action + `state.last_offer`.
- Opponent hit counts: opus5 ~40k rows, Lira ~7.5k, Zeus ~3.6k, Mythos ~0.6k, gill/bates ~0.2k (these are TURN rows, not games — gill real head-to-head is only ~43 games).
- `tw/policy.py` (our policy), `params_*.json` (per-agent tuning), `tw/strategy.py`, `tw/text.py` (message bank), `data/messages*.json`.
- Prior audit artifacts in the scratchpad from the 2026-08-22 run: `summ_*.jsonl.gz`, `vip_*.jsonl.gz`, `an_neg_opp.py`.

## The ask
The top-3 are 150–290 pts ahead on neg+pers and we're jammed in a 27-pt pack at #4–7. **Brainstorm 5 genuinely different ways to ESCAPE the pack toward top-3** — not incremental param nudges, but distinct strategic bets. For each: the thesis, the mechanism, why it could be big, and the single sharpest thing to check in the logs to validate/kill it. Be adversarial to my beliefs above — if belief #1 (neg is structural) is wrong, that's the whole ballgame, so pressure-test it hardest.
