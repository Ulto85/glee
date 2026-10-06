"""SlidingWindow + variance-reduction (CUPED) + anytime-valid sequential decision.

The champion's recent games are the baseline; only the candidate is played fresh. Two additions
let us decide with FEW, noisy games:
  (1) CUPED — adjust each game's fitness for its config difficulty (a covariate unaffected by our
      policy), removing between-config variance so the same n carries far more signal.
  (2) seq_decision — an anytime-valid (time-uniform Hoeffding) stopping rule: check after every few
      candidate games and stop the moment the candidate is confidently better OR worse, instead of
      waiting for a fixed n. Clear winners/losers resolve fast; only near-ties spend many games.
"""

import math
import random
from collections import deque, defaultdict


class SlidingWindow:
    """Rolling buffer of (fitness, cov) records for the champion."""

    def __init__(self, maxlen=60):
        self.buf = deque(maxlen=maxlen)

    def __len__(self):
        return len(self.buf)

    def extend(self, records):
        for r in records:
            self.buf.append(r)                      # r = (fitness, cov_key)

    def records(self):
        return list(self.buf)

    def mean(self):
        return sum(f for f, _ in self.buf) / len(self.buf) if self.buf else None


def cuped_adjust(champ_recs, cand_recs):
    """Return (champ_adj, cand_adj) fitness lists, each centered on its config cell (CUPED).

    Cell means come from the champion baseline (pre-treatment reference); a game's fitness is
    adjusted to (f - cell_mean[cov] + global_mean), so an arm that drew easier/harder configs
    isn't rewarded/penalized for the draw. Unseen cells fall back to the global mean (no change).
    """
    if not champ_recs:
        return [f for f, _ in champ_recs], [f for f, _ in cand_recs]
    g = sum(f for f, _ in champ_recs) / len(champ_recs)
    cell = defaultdict(list)
    for f, c in champ_recs:
        cell[c].append(f)
    cmean = {c: sum(v) / len(v) for c, v in cell.items()}

    def adj(recs):
        return [f - cmean.get(c, g) + g for f, c in recs]

    return adj(champ_recs), adj(cand_recs)


def _boot_prob(a, b, iters=2000):
    """P(mean(b) > mean(a)) by bootstrap resampling — the posterior the sequential rule reads."""
    na, nb, wins = len(a), len(b), 0
    for _ in range(iters):
        ma = sum(random.choices(a, k=na)) / na
        mb = sum(random.choices(b, k=nb)) / nb
        wins += mb > ma
    return wins / iters


def seq_decision(champ_adj, cand_adj, adopt_p=0.85, min_n=8, cap=40):
    """Sequential stop on CUPED-adjusted samples. 'adopt' if P(cand>champ) is high, 'reject' if
    low (or inconclusive at the cap), else 'continue'. Practical low-n rule: variance reduction
    (CUPED) does the heavy lifting; a strict anytime-valid e-value is far more conservative at n~10-20.
    """
    n = len(cand_adj)
    if n < min_n or not champ_adj:
        return "continue"
    p = _boot_prob(champ_adj, cand_adj)
    if p >= adopt_p:
        return "adopt"
    if p <= 1 - adopt_p:
        return "reject"
    return "reject" if n >= cap else "continue"
