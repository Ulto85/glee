# H2H Win-Mining Study — 2026-08-26

Scope: entire history in logs/games_{champion,delta,eta,gamma,theta}.jsonl (not just tail),
filtered to games where opponent ∈ {Test 4, opus 5, opus 5(2), opus 5(4), opus 5(5), Clod,
grok 4.6, gill bates, Mythos01, nemo}. 39,395 matching per-turn log lines → 3,521 distinct
(our-agent, game_id) games reconstructed by taking the terminal (max-round) record per game.
Outcomes/payoffs computed from state fields (negotiation: player_{1,2}_value + accepted price;
bargaining: player_{1,2}_gain × delta^(round-1); persuasion: seller_total_payoff/buyer_total_payoff
directly from state). "Win" = our realized payoff > opponent's realized payoff in games that
actually closed (AcceptOffer/"accept"); no-deal games are counted separately and excluded from
win-rate denominators. "us" role/slot inferred from mode of state.current_player across a game's
records (since these are our-agent action logs, current_player at decision time = us).

## 1. Win-rates vs top agents, by family

### Persuasion (n=1178 games, ALL close — no no-deal state)
Split by our role — this is the dominant variable, far more than opponent identity:
- **Seller (us): 74% win rate overall (n=306)**
  - vs opus 5(4): 92% (n=49) | vs opus 5(5): 82% (n=51) | vs opus 5: 79% (n=47)
  - vs Clod: 77% (n=30) | vs opus 5(2): 67% (n=61) | vs Test 4: 57% (n=23)
  - vs grok 4.6: 54% (n=41) | vs nemo: 50% (n=4, tiny)
- **Buyer (us): 26% win rate overall (n=603)** — flat losing across every opponent, 21-32% band
  (Test4 21%, Clod 23%, opus5(2) 25%, grok4.6 25%, nemo 25%, opus5 26%, opus5(4) 28%, opus5(5) 32%)

### Negotiation (n=544 games; very high no-deal rate)
No-deal rate 47-93% depending on opponent (Test4 84%, Clod 93%, opus5(4) 90%, grok4.6 56% —
lowest — nemo 47% — lowest). Of games that DID close: **4 wins / 83 decided (4.8%) total.**
Per-opponent decided win-rate: Test4 0/23, grok4.6 0/39, opus5 0/6, opus5(2) 0/5, opus5(5) 1/5,
Clod 0/0 (all no-deal), nemo 3/5 (60%, n=5 only).

### Bargaining (n=1799 games; moderate-high no-deal rate)
No-deal rate 25-73% (Clod 73% highest, opus5-family 61-64%, grok4.6 37% lowest, Mythos01 25%
n=4 tiny). Of games that closed: **10 wins / 378 decided (2.6%) total.**
Per-opponent: grok4.6 7/77 (9%), Clod 3/19 (16%), everyone else (Test4, opus5, opus5(2/4/5),
nemo, gill bates, Mythos01) = 0 wins out of 50-118 decided games each.

## 2. What differs in the wins vs losses — is it our policy?

**Persuasion seller wins are the one real, replicable, already-partially-exploited pattern.**
Final-round timing is identical between wins/losses (~19.8-19.9 avg — not a stalling/rushing
effect) and message-type mix (text vs binary) is nearly identical too (wins 196T/187B vs losses
254B/254T) — so the seller edge isn't "we send more of message-type X" or "we close faster/slower."
It's a role-conditioned policy edge: our seller-side pricing/quality-signal policy structurally
beats every top agent's buyer-side policy, while our buyer-side policy structurally loses to
every one of their seller-side policies. This exactly reproduces the already-known
"buyer was the drag" finding (glee-persuasion-buyer-prior.md) and the F1 liar-gate fix
(glee-pers-neg-lift-audit.md) — this H2H cut is independent confirmation that those fixes
are working specifically against the top tier, not just the general field.

**Negotiation/bargaining wins are NOT a policy signature — they are opponent-mistake capture.**
100% of negotiation wins (4/4) and 100% of bargaining wins (10/10) — and for that matter 100%
of bargaining LOSSES too (368/368) — have `we_proposed_final=False`: we only ever win (or lose)
by ACCEPTING the opponent's offer; we never had our own offer accepted in any decided H2H game
vs a top agent in these two families. Our bargaining/negotiation policy against top agents is
purely reactive (wait-and-accept), never a closer. So the "win" pattern isn't a tactic we chose —
it's that the opponent occasionally over-concedes enough that accepting becomes +EV for us:
- Bargaining wins cluster in **δ-advantage cells**: avg us_delta 0.95 vs opp_delta 0.8-0.9 in
  wins, vs avg us_delta 0.877 in losses — i.e. we win when we're assigned the more-patient
  (higher discount factor) seat and the opponent still tries to close. This is exactly the cell
  the already-shipped R1 `barg_dadv_guard` lever (glee-r1-shipped-spikehunt-done.md) targets —
  this analysis independently re-confirms that lever is pointed at the right cell, it doesn't
  surface a new one.
- Negotiation wins are all `us_role=buyer`, all CI=True, split ~51-55% in our favor — thin (n=4)
  and opponent-specific (3 of 4 vs nemo, who is evidently the softest top-tier negotiator: also
  has the lowest no-deal rate, 47%, meaning nemo actually closes deals with us more than any
  other top agent, and more of them go our way).

## 3. Exploitable matchups

- **Persuasion, us-as-seller vs opus 5(4) and opus 5(5): 92% and 82% win rates** (n=49, n=51) —
  the single most lopsided matchup found. Both are "opus 5" variants, both weak specifically on
  the buyer side against our seller policy.
- **nemo in negotiation**: lowest no-deal rate (47%) of any top opponent AND our only real win
  cluster (3/5 decided). Worth flagging as "the soft top-agent" for negotiation, though n is too
  small (19 games total) to build a dedicated lever on.
- Everyone else in negotiation/bargaining (Test4, opus5, opus5(2), Clod, gill bates, Mythos01):
  effectively 0% win rate when a deal closes — these opponents are not exploitable with current
  policy; the only lever available there is the already-known δ-advantage guard.

## 4. Endogeneity check
Persuasion role split is a real policy effect (message/pricing content differs by role; timing
doesn't, ruling out a "just close faster" artifact). Negotiation/bargaining "wins" are largely
cell/opponent-behavior driven (δ assignment, opponent's willingness to over-concede) rather than
something our policy actively produces — proposer-never-us across all 388 decided games in those
two families is itself a policy fact (we're structurally reactive), but which side of the coin
we land on in a given cell is opponent-driven, not policy-driven.

## 5. Lever proposal
No new lever discovered beyond what's already shipped/queued. Two confirmations + one prioritization:
1. **Persuasion buyer-side remains the highest-value unfixed leg** — 26% win rate flat across
   every top opponent (not just weak ones) means the drag is systemic, not matchup-specific.
   Any further buyer-prior/cold-start work continues to be correctly prioritized.
2. **R1 barg_dadv_guard (already shipped) is confirmed as the right bargaining lever** — this
   independent H2H cut lands on the same δ-advantage cell as the source of our only bargaining
   wins vs top agents. No action needed beyond what's already deployed.
3. **Negotiation has no policy-based lever visible in this data** — wins vs top agents are too
   rare (4 in the whole history) and opponent-mistake-driven to generalize. The actionable lever
   remains reducing no-deal rate (already the subject of the CI-accept / h0-floor-pin work), not
   chasing a "how we win" pattern that doesn't really exist yet.
