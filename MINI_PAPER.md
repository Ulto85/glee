# Interpretable Self-Improvement for Language-Based Economic Games: An LLM-Guided, Quality-Diverse Optimizer with Honest Evaluation

*Draft mini-paper (competition track, IAB @ NeurIPS 2026). This is a scaffold to rewrite in your own words — every claim is grounded in the code (`tw/`) and live GLEE results. Math is shown with its source so nothing is a black box.*

---

## Abstract

We study self-improving agents in GLEE, a benchmark of two-player, language-based economic games (bargaining, negotiation, persuasion) scored on payoff-percentile against the field. We keep a **deterministic, interpretable policy** that decides all numeric moves and treat improvement as a search over that policy. Our central finding is methodological and it emerged the hard way: **the binding constraint on automated self-improvement in these games is evaluation, not policy search.** At feasible sample sizes (n≈14/arm, thin matchmaking) paired online A/B tests are **~44% noise** — we show this by simulating two identical policies — so a search loop gated on them mostly pays rated games to lock onto noise. Worse, three independent offline proxies each *confidently* proposed changes that were neutral or negative on real data: (i) a behavioral diagnostic motivated an "accept the bird-in-hand" negotiation change (economically rational) that **halved captured share** — reward-hacking one level up; (ii) an opponent-exploitation premise built on an archetype classifier that mislabeled 60% of a competent field as "folders" evaporated when we mined what opponents *actually accept* (≥0.50 for themselves); (iii) a self-play simulator that was calibrated for one family was *anti-calibrated* for another until its opponent model was fixed. We resolve this with a **multi-stage low-noise evaluator** — an instant calibrated simulator and zero-cost counterfactual replay as free pre-filters, and, as the keystone, **per-config-cell rank scoring against a persisted champion baseline** (which cancels config heterogeneity and the bimodal 0/positive payoff structure and needs only our own payoffs to decide candidate≥champion) — adopting a change only when independent signals agree. The gains that actually moved the live leaderboard were, accordingly, **bug fixes and a provably-dominated-corner fix in the neglected persuasion family, plus volume of a frozen champion** — not the search loop, which produced zero confirmed adoptions. We argue an interpretable ruleset makes both the improvements and these instructive failures legible in a way reinforcement-learned weights do not.

*Methodology note (as actually run — see PROCESS.md):* the live system is **freeze-and-farm** (a frozen champion farmed for volume) plus the low-noise evaluator above and an autonomous analyze/optimize tick; the MAP-Elites/bandit/Reflexion optimizer described in §3–4 is retained as a studied artifact (it never beat hand-tuning) rather than the rank engine.

---

## 1. Introduction

GLEE [1] asks whether LLMs behave as rational economic agents across three game families. The 2026 competition ranks agents on **self-gain** (own payoff), which is a single scalar proxy for "good economic agent." Optimizing any scalar proxy hard enough invites **Goodhart's law** and reward hacking [2,3]: the measure and the intent diverge. This tension is the paper.

We make three choices that follow from it:
1. **Deterministic core, LLM-as-optimizer.** LLMs are poor at the *numbers* of bargaining (they anchor at 0.8–1.0 of the target, leaving no room [4]) but good at *language* and *proposing changes*. So the policy is deterministic and interpretable; the LLM proposes ruleset edits and writes messages, never prices.
2. **Diversity over greed.** A single champion overfits the opponent pool. We keep a MAP-Elites [5] archive of the best ruleset per behavior niche, explored by a bandit.
3. **Honest evaluation.** Every candidate is confirmed on held-out games with a bootstrap test before adoption — because noisy fitness is how Goodhart enters.

## 2. Background and the scoring math (where the target comes from)

**Rating.** The server maps a game's payoff to a percentile against all games at the same configuration and role, adjusts for opponent strength, and sets
```
game_rating = 2000 + 8000 · (percentile − 0.5)          (Eq. 1, from the competition docs)
```
A player's rating is an exponential average, `R_{t+1} = R_t + η (game_rating − R_t)`, whose fixed point is `R* = E[game_rating] = 2000 + 8000·(E[percentile] − 0.5)`. Hence **median play → R=2000**, and reaching the top of the board (~3000) requires
```
percentile* = 0.5 + (3000 − 2000)/8000 = 0.625,          (Eq. 2)
```
i.e. beating ~62.5% of the field consistently. This is why *exploitation*, not *not-losing*, is required — a not-losing agent converges to the 2000 median.

**The three games (canonical models and the payoffs we optimize).**
- *Bargaining* is Rubinstein alternating-offers [6]: split a pie of size M, discounted by δ each round. The subgame-perfect first-proposer share is
  ```
  x* = (1 − δ_B) / (1 − δ_A δ_B)                          (Eq. 3, Rubinstein 1982)
  ```
  which encodes the patience-as-power intuition our policy uses.
- *Negotiation* is bilateral trade under private values [7]: seller utility `p − V_A`, buyer utility `V_B − p`, with gains from trade only if `V_B ≥ V_A` (Myerson–Satterthwaite: no mechanism guarantees efficient trade under two-sided private information).
- *Persuasion* is repeated Bayesian persuasion / lemons [8]: the seller privately knows quality and is paid per sale; a long-lived buyer punishes deception by withdrawing trust.

## 3. Method

### 3.1 The deterministic policy (inner loop)
Each turn, `strategy(game)` (i) infers an opponent **archetype**, (ii) picks a numeric action conditioned on it, (iii) swaps in a message, (iv) logs everything, and (v) falls back to a guaranteed-legal action so a turn never becomes an abandoned (5th-percentile) game.

*Opponent model.* From the opponent's offer trajectory we compute two features:
```
generosity = clip(first_offer_to_us / scale)             concession = clip(mean(Δ toward us) / (0.15·scale))
```
`classify` maps these to {over_conceder, hard_anchorer, tit_for_tat, unknown}. A cheap **text layer** additionally reads the opponent's message (substring rules mined from transcripts) to type them on move 1, and a per-opponent profile (keyed on the disclosed name) seeds a prior across games.

*Numeric policy (examples).* In negotiation we hold a minimum surplus
```
keep = max(neg_keep_floor, neg_keep_coeff · grab(arch) · (1 − progress)),  keep→0 on the final round,
```
so a buyer accepts iff `p ≤ V_B·(1 − keep)` and otherwise counters at `V_B·(1 − keep)` — we never chase a deal to ~0 surplus, but we take any positive deal at the horizon rather than no-deal. In bargaining the reservation is `base = δ · barg_delta_coeff` (a proxy for the Rubinstein continuation value `δ·x*`), plus an archetype bump; patient agents refuse lowballs and capitulate only on the last round. *All numbers come from here; the LLM never sets one.*

### 3.2 The optimizer (outer loop)
We treat ruleset tuning as **quality-diversity search**.

*Fitness (scale-free).* For a batch of N games,
```
share_i = u_me,i / (|u_me,i| + |u_opp,i|),     f(θ) = (1/N) Σ_i (share_i if a deal, else 0).   (Eq. 4)
```
Using *share* makes payoffs comparable across pies of size 100 and 10^6; counting no-deals as 0 penalizes over-aggression.

*Behavior descriptor and archive (MAP-Elites [5]).* We describe *how* a ruleset plays, not how well:
```
φ(θ) = ( ⌊5 · share⌋ , ⌊4 · no_deal_rate⌋ )   →   a cell id like "3-0".      (Eq. 5)
```
E.g. share 0.68 with no walk-aways → cell `"3-0"` = "high capture, never no-deals." The archive keeps the elite of each cell:
```
archive[φ(θ)] ← θ   iff   f(θ) > f(current elite of that cell).            (Eq. 6)
```
This keeps a *diverse* set of good-but-different rulesets (a patient holder and an aggressive anchorer both survive), which is the concrete defense against opponent-distribution overfitting. *(PTA — a transitive/cyclic performance embedding — will replace Eq. 5 so cells are strategic niches, not crude share/no-deal bins.)*

*Exploration (Thompson sampling [9]).* To choose which cell to mutate next, we sample each cell's expected fitness from a Normal posterior `N(μ_c, σ_c/√n_c)` (optimistic for unseen cells) and take the argmax. Exploration is explicit code, not LLM judgment, because LLMs under-explore [10].

*Proposer + Reflexion memory.* A candidate is a small bounded patch to the parent ruleset: either a random mutation (works with no LLM key) or an LLM proposal fed the accumulated **lessons** (e.g. *"raising barg_share_cap → fitness 0.47 vs parent 0.68 [regressed]"*) and forced to state a falsifiable hypothesis [11,12].

### 3.3 Honest evaluation (the gate)
Before adoption we A/B champion vs candidate on **fresh** games and estimate significance by bootstrap:
```
P(chall > champ) ≈ (1/B) Σ_b 1[ mean(resample_chall_b) > mean(resample_champ_b) ].   (Eq. 7)
```
Adopt iff `P ≥ 0.8` **and** each arm has ≥ 8 games in that family (a minimum-sample gate).

### 3.4 Deciding under sparse feedback (thin matchmaking)
Live matchmaking is thin (often 1–2 concurrent games), so fixed-n A/B stalls. Two standard tools make low-n decisions trustworthy, and we adopt both:

*Variance reduction (CUPED [15]).* At low n the enemy is variance from config heterogeneity (payoff scales 10²–10⁶; horizons 1/10/∞). We adjust each game's fitness for a **config covariate** `c` (role, own-value bucket, horizon; unaffected by our policy):
```
f_adj = f − mean_champ[c] + mean_champ,     with cell means from the champion baseline.   (Eq. 8)
```
An arm that happened to draw harder configs is no longer penalized for the draw — in practice this flips correct decisions the raw metric gets wrong (a candidate that only faced hard configs: raw 0.285 < champ 0.408, but CUPED-adjusted 0.483 > 0.408).

*Anytime-valid sequential stopping.* Rather than a fixed n, we play the candidate in small chunks and stop the moment the (CUPED-adjusted) evidence is decisive — clear winners/losers resolve in few games; only near-ties spend to a cap. We note an honest tradeoff surfaced empirically: **strict e-value / Hoeffding confidence sequences [16,17] are too conservative at n≈10–20** (a 0.2-effect needs ~80 games), so we use a **sequential bootstrap-posterior** rule (adopt at `P≥0.85`, `n≥8`, cap 40) — CUPED does the variance heavy-lifting, the sequential rule stops early. This is the sliding-window optimizer: reuse the champion's rolling window as the baseline; only the candidate is played fresh.

## 4. Results

**Live standing.** After bug fixes and volume, the agent sits well above the 1000 start across all three families — as of writing, bargaining ≈ 1500–1700, negotiation ≈ 1350–1500, persuasion ≈ 1130–1220 (bargaining strongest), with **0 invalid moves** across 500+ games. Ratings are live and still evolving; the two mature families have converged to the current policy's skill ceiling (see §4, plateau).

**A latent, high-cost bug the interpretable pipeline exposed.** Bargaining *offers* require `alice_gain`/`bob_gain` keys, but the code emitted `player_i_gain`. This was invisible while the agent always *accepted* in round 1; once a patience fix made it *propose*, every offer was rejected as invalid → forced losses, costing ~100 rating points until fixed. Recovery after the fix was clean and sustained (bargaining ≈ 889 → 1028 → 1247), with the alice=player_1 mapping confirmed by observing that our captured share when proposing was the *larger* share (0.6–0.74).

**A false positive from automated search — the central finding.** A first search run (4 iterations, 6 games/candidate) built a MAP-Elites archive of cells `{2-0, 3-0, 3-2}` and "found" a champion at cell `3-0` (fitness 0.682 vs seed 0.418) by lowering `grab.unknown` 0.60→0.558. The confirmation A/B reported `P(chall>champ)=0.986` **in bargaining** — but `grab` is used *only* in negotiation, so the two rulesets play byte-identical bargaining. The "significant" bargaining gap was pure small-sample noise on a causally-irrelevant metric, and the negotiation arm (where the change *could* matter) had only n=1 due to sparse matchmaking. We therefore rejected the candidate and hardened the evaluator with the ≥8-per-arm gate (Eq. 7); the same data now correctly yields *reject*.

**Play converges to the policy's skill ceiling.** Running the *fixed* champion (no learning) climbs the rating, then flattens: over one window, bargaining and negotiation moved <5 points across ~90 games while persuasion (fewer games, still de-shrinking) kept rising. This is the expected fixed-point behavior of the rating (`R → E[game_rating]`), and it is the empirical motivation for the optimizer: past the plateau, only a *better policy* raises the ceiling — more games cannot.

## 5. Discussion: evaluation integrity is the binding constraint

Both failures above are instances of the same phenomenon our benchmark studies. Skalse et al. [3] define a proxy as *hackable* if one can raise expected proxy return while lowering true return; Gao et al. [2] show proxy optimization eventually degrades the true objective. Our optimizer, optimizing a noisy 6-game fitness, produced a "gain" that was noise (a hackable proxy realized in miniature). The mitigations are not incidental — they are the contribution: (i) **quality-diversity** resists overfitting a single champion; (ii) **explicit bandit exploration** resists premature convergence; (iii) **held-out bootstrap + minimum-n + causal scoping** resist crediting noise. An interpretable ruleset is what made the failures diagnosable at all — we could point to the exact line (`grab` used only in negotiation) that made the bargaining "win" impossible.

## 6. Limitations and future work
- **Sparse matchmaking** limits per-candidate samples. *Addressed* (§3.4) by CUPED variance reduction + a sliding-window sequential test; **causal scoping** (only score a change on the families it can affect) is implemented — the `grab` false positive is the motivating example.
- **Off-policy evaluation (sketch, next).** The biggest sparsity unlock is to *reuse the whole logged corpus* to estimate a candidate's value without playing it. Standard OPE (importance sampling / doubly-robust [18]) needs action *propensities* and overlap — our deterministic policy has neither. Plan: make the policy mildly stochastic (ε-randomize over a few near-optimal candidate actions per decision) and log the propensity; then a **doubly-robust estimator** scores many candidate rulesets offline from logged games, and only the top few are confirmed live. Cost: some determinism/interpretability. This turns thousands of already-played games into candidate evaluations — the natural successor to the sliding window.
- **PTA / AdaPTA:** replace the crude behavior descriptor (Eq. 5) with a performance-relation embedding (transitive skill + cyclic matchup) so archive cells are strategic niches; quality-diversity then explicitly covers the non-transitive dimension where exploitability lives [13,14].
- **Exploitability vs. rating:** a high rating need not imply robustness; measuring best-response exploitability against the leaderboard is an open, novel direction [13,14].

## 7. Conclusion
Keeping the policy interpretable and using the LLM only to *optimize* it yields a competitive GLEE agent whose improvements — and, more importantly, whose *failures* — are legible. The recurring lesson is that self-improvement in a noisy, adversarial economic game is gated not by the search but by the honesty of its evaluation.

---

## References
*(verified against primary sources during this project unless flagged; confirm venues before final submission)*

[1] Shapira, Madmon, Reinman, Amouyal, Reichart, Tennenholtz. *GLEE: A Unified Framework and Benchmark for Language-based Economic Environments.* arXiv:2410.05254, 2024.
[2] Gao, Schulman, Hilton. *Scaling Laws for Reward Model Overoptimization.* ICML 2023 (arXiv:2210.10760).
[3] Skalse, Howe, Krasheninnikov, Krueger. *Defining and Characterizing Reward Hacking.* NeurIPS 2022 (arXiv:2209.13085).
[4] Xia et al. *Measuring Bargaining Abilities of LLMs: A Benchmark and a Buyer-Enhancement Method (OG-Narrator).* ACL Findings 2024 (arXiv:2402.15813).
[5] Mouret, Clune. *Illuminating search spaces by mapping elites (MAP-Elites).* arXiv:1504.04909, 2015.
[6] Rubinstein. *Perfect Equilibrium in a Bargaining Model.* Econometrica 50(1), 1982.
[7] Myerson, Satterthwaite. *Efficient Mechanisms for Bilateral Trading.* J. Economic Theory 29(2), 1983.
[8] Kamenica, Gentzkow. *Bayesian Persuasion.* American Economic Review 101(6), 2011.
[9] Thompson. *On the likelihood that one unknown probability exceeds another…* Biometrika, 1933. (See Russo et al., *A Tutorial on Thompson Sampling*, 2018.)
[10] Krishnamurthy et al. / "When Greedy Wins" — LLMs explore sub-optimally on bandits. (arXiv:2509.24923; confirm.)
[11] Shinn et al. *Reflexion: Language Agents with Verbal Reinforcement Learning.* arXiv:2303.11366, 2023.
[12] Yang et al. *Large Language Models as Optimizers (OPRO).* arXiv:2309.03409, 2023. Romera-Paredes et al. *FunSearch*, Nature 2024. DeepMind *AlphaEvolve*, 2025 (confirm).
[13] Lanctot et al. *A Unified Game-Theoretic Approach to MARL (PSRO; NashConv/exploitability).* NeurIPS 2017 (arXiv:1711.00832).
[14] Balduzzi et al. *Re-evaluating Evaluation (Nash averaging).* NeurIPS 2018 (arXiv:1806.02643).
[15] Deng, Xu, Kohavi, Walker. *Improving the Sensitivity of Online Controlled Experiments by Utilizing Pre-Experiment Data (CUPED).* WSDM 2013.
[16] Howard, Ramdas, McAuliffe, Sekhon. *Time-uniform, nonparametric, nonasymptotic confidence sequences.* Annals of Statistics, 2021.
[17] Ramdas, Grünwald, Vovk, Shafer. *Game-theoretic statistics and safe anytime-valid inference.* Statistical Science, 2023 (arXiv:2210.01948).
[18] Dudík, Langford, Li. *Doubly Robust Policy Evaluation and Learning*, ICML 2011; Jiang & Li, *Doubly Robust Off-policy Value Evaluation for RL*, ICML 2016.

*Adjacent, directly relevant: Bergemann, Ghili, Hu, Li, Yang, Training LMs for Bilateral Trade with Private Information (arXiv:2604.16472); Miceli-Barone, Belle, Cohen, Used Car Salesbots? (arXiv:2605.31445); Guo et al., Suspicion-Agent (arXiv:2309.17277).*

---

## Appendix A — What actually worked (empirical, live session)

This appendix records the process as run (see `PROCESS.md`), which diverged from the optimizer-centric plan of §3–4 and is the paper's real contribution.

**A.1 The evaluator is the product.** Three independent audits and our own logs agreed the search loop had **0 confirmed adoptions**; all real gains were bug fixes + hand-tuning + volume. We therefore froze the champion and rebuilt evaluation:
- *Noise floor.* Simulating two identical policies at n=14/arm, ~44% of runs cross a P≥0.8 adoption threshold — small online A/Bs are coin flips. CUPED cannot fix the bimodal 0/positive payoff structure at that n.
- *Per-cell rank baseline (keystone).* Score = payoff percentile within the same config+role cell. For a fixed cell this is monotone in our own payoff, so candidate≥champion is decidable from our payoffs alone, cell by cell; ranking (bounded, scale-free) removes bimodal variance, and degenerate no-ZOPA cells (everyone scores 0 = 2000) are dropped. The champion baseline is harvested for free from the freeze-and-farm runner.
- *Free pre-filters.* A calibrated self-play simulator (instant) and counterfactual replay of accept-policy on logged games (paired, zero new games). Adoption requires cross-signal agreement to block Goodharting.

**A.2 Three proxies that lied (the evidence).**
- *Rational-but-hacked.* A "diagnostic" showed we rejected profitable offers and over-held under unknown horizon; the implied fix (accept the bird-in-hand) is economically correct yet **halved share-on-deals (0.45→0.22) with no fewer no-deals** — proxy-gaming of per-deal share vs true expected payoff (Skalse [3]).
- *Exploit mirage.* A recurring-opponent exploiter looked strong until we mined *acceptance*: opponents demand ≥0.50 for themselves (chotu 0.69), and the archetype classifier had labelled 60% of a competent field "over-conceder." Live aggression regressed. There is no bargaining exploit against this pool.
- *Anti-calibrated sim.* The simulator reproduced the negotiation ground truth but *rewarded* bargaining aggression its opponents would never tolerate — trustworthy for rejecting, not for positives, until opponent walk-away floors were added.

**A.3 What moved the leaderboard.** Fixing our own bugs in the neglected persuasion family — a buyer that ignored the seller's signal (and was blind to the binary `yes`/`no` format, ~54% of games) and a fully-honest seller at a dominated corner — plus trust-aware Kamenica–Gentzkow [8] signaling (buyers punish deception, buy-rate 0.82→0.55 after a pushed low), plus complete-information best-response in negotiation, plus volume of the frozen champion. Over the session the 3-family average rose from ≈1490 to ≈1610, driven by persuasion and negotiation; bargaining converged to its true ≈1670 level, which no *safe* change could lift.

**A.4 Takeaway.** In a slow-, noisy-, censored-evaluation setting, the disciplined move is not a cleverer search but a *trustworthy, low-cost evaluator* plus the humility to ship only what independent signals confirm. Interpretability made every one of the lies above diagnosable.
