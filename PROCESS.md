# GLEE — Current Operating Process

*How we actually run the agent now (supersedes HYBRID_PLAN.md). Updated live during the Aug 2026 session after three independent audits converged on the same picture.*

## Goal
Rank as high as possible on the GLEE leaderboard (3-family average of `2000 + 8000·(percentile−0.5)`), and write a short competition paper from the same system. Realistic target: solid, climbing average; **3000 (62.5th pct) is a stretch against a competent field**.

## Current standing (moves with the farm)
- bargaining ≈ 1700, negotiation ≈ 1830, persuasion ≈ 1300, **leaderboard avg ≈ 1610** (up from ~1490 at session start).
- Live policy = frozen champion `params_good.json`; `GLEE_NEG_V2=0`, `GLEE_EXPLOIT` unset.

## The core loop (three parts)

1. **Freeze-and-farm (the only proven rating mover).** `farm.py` plays the frozen champion at volume across all 3 families and does nothing else. It restores `params_good.json` to `params.json` on start (so a stray candidate can never be live) and **persists every finished game** to `logs/champion_baseline.jsonl` (the per-cell reference the evaluator needs). Volume converges each family's displayed rating to its true percentile.

2. **Non-noisy multi-stage evaluator (how we test a change reliably).** Small online A/Bs at feasible n (~14/arm) are **~44% noise** — retired as decision tools. Instead:
   - `tw/sim.py` — instant local self-play vs scripted opponents (conceder/fair/tough/tit-for-tat with walk-away floors; persuasion buyers/sellers). Calibrated per family (reproduces the neg V2 ground truth). **Trusted to REJECT regressions; untrusted for bargaining *positives* (its opponents fold more than real ones).** Used as a free pre-filter.
   - `tw/replay.py` — counterfactual replay of accept-side policy on logged games: paired, deterministic, zero new games. **Unbiased for "accept-more" changes** (e.g. V2); censors "hold-for-more."
   - `tw/baseline.py` — the keystone: score a candidate as its **rank within the champion's own distribution for the same config+role cell**, aggregated mean-of-cell-means, dropping degenerate (no-ZOPA / thin) cells. Cells are **coarsened** (family+role+horizon / discount / p) so they reach n≥20 fast; we rank *share* (scale-free) so coarsening is safe. This removes config-heterogeneity + bimodal-payoff noise and needs only our own payoffs to decide candidate≥champion.
   - `eval_candidate.py` — runner: sim signal + online per-cell-rank with anytime-valid stop; **adopt only if online beats champion AND sim agrees** (cross-signal, anti-Goodhart); restores champion on exit.

3. **Autonomous feedback loop.** A CronCreate tick (every 5 min, session-only) re-invokes the assistant to: health-check + restart farm if down → light status unless real new data → deeper *safe* pass through the non-noisy evaluator only → deploy iff validated → feed back. Session-only, auto-expires 7 days.

## The discipline (STOP / DO) — from the audits
**STOP:** shipping unconfirmed params to live; small online A/Bs as decision tools; treating the knob/MAP-Elites/bandit optimizer as a rank engine (0 confirmed adopts ever — it is a paper artifact).
**DO:** freeze-and-farm; fix our own bugs; make one *structural* bet at a time big enough to clear noise, validated by the non-noisy evaluator; measure against per-cell percentile, not absolute share; keep persuasion (cheapest lever) and volume as the primary gains.

## What's deployed (validated fixes this session)
- **Persuasion buyer** — reads the seller signal (incl. binary `yes`/`no`, ~54% of games — was parsed as neutral → blind) and tracks empirical honesty; refuses `hold` / lemons instead of buying on prior EV.
- **Persuasion seller** — trust-aware: buyers punish deception (buy-rate 0.82→0.55 after we push a low), so preserve reputation when quality is usually high (push lows only at endgame) and push lows only when they're common enough to be worth it (p<0.65).
- **Negotiation** — complete-information best-response (both values visible ~11% of turns → price just inside the opponent's reservation). V2 "accept-more" tested and left OFF (replay: neutral).
- **Bargaining** — `barg_accept_floor` 0.45 (stop mid-game capitulation); classifier de-biased (over_conceder 60%→18%); param residue reset. Aggression tested and rejected — opponents demand ≥0.50, don't fold.

## Honest strategic picture
- **Persuasion** was the one big *lever* (dominated corner fixed) — climbing.
- **Bargaining/negotiation opponents are competent** (demand ≥0.50, don't fold); we are near-frontier there. Aggression/exploitation looked great in theory and sim but **evaporated on real data** — the exploit targets came out ~0.50, and live aggression regressed. Volume + our own bug-fixes are the real gains.
- **The binding constraint is evaluation, not policy search** (the paper's thesis) — offline proxies (behavioral diagnostics, an over-optimistic sim, an exploit premise built on a biased classifier) each confidently proposed changes that were neutral/negative on real data; only held-out, per-cell online scoring is trustworthy.

## File map
- `farm.py` — freeze-and-farm runner (the only rated-game runner) + baseline persistence.
- `tw/policy.py` — the deterministic policy (bargaining / negotiation / persuasion).
- `tw/params.py` — knobs + bounds; `params_good.json` = champion.
- `tw/sim.py`, `tw/replay.py`, `tw/baseline.py`, `eval_candidate.py` — the non-noisy evaluator.
- `tw/exploit.py` — meta-game exploiter (built, kept OFF — no exploit found on real data).
- `tw/{diagnose,evolve,search,window,optimizer,propose}.py` — the LLM/knob optimizer stack (paper artifact only, not used for rank).
- `logs/games.jsonl` (turn log), `logs/champion_baseline.jsonl` (per-cell reference), `logs/profiles.json` (opponent→archetype).
