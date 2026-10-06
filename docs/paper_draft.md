# Evaluation Is the Binding Constraint: Lessons from Optimizing a GLEE Agent

*Working draft — GLEE benchmark (Games in Language-based Economic Environments: bargaining, negotiation, persuasion). All results from our own competition logs.*

## Abstract

We report a multi-week effort to climb the GLEE leaderboard by tuning a deterministic
best-response agent across three families of repeated economic games. Our central finding is
methodological rather than strategic: **the binding constraint on improvement was not the policy
search space but the evaluation.** A self-referential offline evaluator systematically hid the
field, an under-powered online gate could not distinguish leaderboard-relevant effects from noise,
and two separate measurement bugs (a matchmaking-pool confound and a cross-role config mismatch)
each produced confident but false conclusions that we acted on before catching. Once evaluation was
made field-referenced, correctly-powered, and same-pool, we found the deterministic frontier is
**flat near a well-tuned champion**: every offer- and accept-side lever we tried lands inside the
measurement noise floor. We argue the residual gap to the field top is an *opponent-modeling*
problem — reading an adversary's language and bluff structure — not a tuning problem, and we
quantify why. We also describe a practical debugging technique: rotating a *different* model in as
an adversarial "fresh-eyes" auditor caught four load-bearing errors in our own analysis, including
two in tools we had just built to fix the previous error.

## 1. Setup

GLEE scores each game by the **percentile of our per-game payoff versus the field**, within a
config-cell + role, and the per-family rating is an EMA of these game scores (≈ 2000 + 8000·(pct −
0.5)). The scoring is **convex near zero**: a no-deal (payoff 0) lands near the midrank of the
field's zero-mass, so converting a no-deal to any positive payoff leapfrogs the entire zero-mass —
a large percentile jump. The leaderboard averages the three family ratings and ranks each account by
its single best agent.

Our agent is a deterministic policy: archetype classification from the opponent's offer trajectory,
Rubinstein-timing best responses in bargaining, horizon-conditional concession in negotiation, and
Kamenica–Gentzkow-style signaling in persuasion. We farm a frozen champion at volume to build a
per-cell reference distribution and test candidate policies against it.

## 2. The self-referential evaluator trap

Our first offline evaluator ranked a candidate's payoff **within our own champion's** per-cell payoff
distribution and dropped thin/degenerate cells as noise. This is a natural design — it is low
variance and needs only our own payoffs — but it is **blind to the field**: a champion is by
construction near the 50th percentile of its own distribution, so *every* candidate "ties," and the
dimension on which the field actually beats us is never observed. This single design choice explained
months of "we can't crack it" conclusions.

The fix was to build a **field-referenced** metric from the opponents' realized payoffs
(`opp_payoff`), which are a sample of the field playing against us. On the games we *do* close, the
field extracts 63–75% of surplus versus our 25–37% — a real, large gap that the inward evaluator
could not see.

## 3. Two measurement bugs that produced false conclusions

Field-referencing is necessary but not sufficient; the reference must be constructed correctly. We
made two errors, each of which produced a confident, wrong conclusion we acted on:

**(a) The matchmaking-pool confound.** To bank negotiation games faster we ran test arms in a
*negotiation-only* queue. But a negotiation-only agent is matched into a *different, easier* opponent
pool than a mixed-family agent. Measured against a mixed-family champion baseline, an arm running
*identical logic* read +7 percentile points "significant" — with a 15-point lower no-deal rate — a
pure artifact of the pool difference. Fix: the reference must be **same-pool** (a neg-only champion
reference for a neg-only arm).

**(b) The cross-role config mismatch.** Our second field-referenced tool matched a game "config" by
stripping the role token from the cell key. But the value bucket in the cell is *our own private
value*: `buyer|v0.8` (we value the good at 0.8) and `seller|v0.8` (our cost is 0.8) are *different
games*. Matching across them referenced our buyer against field agents who played an easier config,
inverting a buyer-vs-seller weakness diagnosis. The lesson generalizes: a field reference is only
valid within an identical config, and "config" must be defined by the game, not by our seat.

## 4. The statistical-power reality

The leaderboard-relevant effect size is small: the entire span from 50th place to the top is roughly
5–7 percentile points. Our paired online gate at n≈40 had a bootstrap-and-cross-sample noise floor of
±10–16 points — *larger than any effect that matters*. "Inside the noise floor" was therefore the
**pre-determined outcome** of every test we ran; we were mistaking *unmeasurable at this n* for
*non-existent*. Detecting a real +1-point effect needs n ≈ thousands, not tens. This reframes A/B
discipline for this class of problem: either farm evaluations at genuinely high n, or restrict
shipping to changes that are *theoretically dominant* (cannot lose), because you will not be able to
measure the marginal ones.

## 5. The core empirical result: a flat deterministic frontier

With evaluation made field-referenced, same-pool, and correctly-powered, we tested the strongest
levers we could design: field-style high-anchoring with a glide to a high floor, horizon-conditional
firmness, within-game walk-risk accept classifiers, and — after verifying that a zone of agreement
is essentially universal in this benchmark — an endgame "guaranteed-close" capitulation aimed
directly at our ~42% no-deal rate on winnable games.

Every one of them lands inside the noise floor. The guaranteed-close lever is instructive: the
no-deal *leak* is real and large (41–75% no-deal in known-horizon cells despite a guaranteed deal
zone), but our policy *already* capitulates on the final round, and the residual no-deals are
**strategic opponents making a final-move lowball we cannot profitably accept**. No amount of our own
capitulation converts them; distinguishing a real "I will walk" from a bluff, and inferring an
opponent's hidden reservation from their language, is exactly what a deterministic rule cannot do.
We conclude the deterministic frontier is flat near a well-tuned champion, and the residual gap is an
**opponent-modeling** problem.

## 6. A debugging technique: adversarial fresh-eyes model rotation

Four times, a *different* model invoked as a skeptical "fresh-eyes" auditor — explicitly instructed
to challenge our stated assumptions — caught a load-bearing error we had not: the self-referential
evaluator; that our "no-deals are structural" claim confused opponent *offers* (lowballs) with their
*values* (a deal zone is universal); a wiring bug that made a deployed lever fire on 1/12 of its
intended channel; and the cross-role config bug **in a tool we had built to fix the previous
audit's finding.** The pattern is that one's own analysis inherits one's own blind spots, and a
model with no stake in the prior conclusions, pointed at the same logs, surfaces them cheaply. We
recommend it as standing practice for self-play optimization, where the optimizer grades its own
homework.

## 7. What did move the number

Durably, two things: (i) a handful of *theoretically dominant* shipped fixes (a persuasion buyer
message classifier; horizon-aware complete-information accepts; discount-forward bargaining accepts),
and (ii) volume — the rating EMA converging toward the policy mean and the field settling. Notably,
volume is *not* a durable lever: it asymptotes and moves both ways, and only shipped policy changes
move the destination. This is consistent with the flat-frontier finding: absent a better *policy*,
there is nowhere for the mean to move.

## 8. Conclusion

For agent optimization in language-based economic games, we found evaluation — not policy search —
to be the binding constraint at every level: a blind reference distribution, an under-powered gate,
and two config/pool confounds each independently produced false progress. Once evaluation was fixed,
the deterministic ceiling was *earned* rather than assumed, and the remaining headroom is
opponent-modeling. We offer the field-referenced same-pool metric, the power analysis, and the
adversarial-audit practice as transferable tools.

---
*Appendix: per-cell no-deal and ZOPA tables; noise-floor bootstrap; the four audit corrections in
full — to be added.*
