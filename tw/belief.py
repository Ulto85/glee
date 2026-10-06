"""Belief-Based Best-Response (BBR): a per-game posterior over the opponent's reservation value,
updated from their revealed offers (strong) and message numbers (weak), used to price at the
expected-surplus-maximizing point instead of a fixed margin. Deterministic + interpretable.

Negotiation only for now (opponent's private valuation). Bargaining δ-belief is the same pattern.
"""

import math


class ValueBelief:
    """Grid posterior over the opponent's value. `opp_is_seller`: is the OPPONENT the seller?
    (seller asks >= their cost → their value upper-bounded by their ask; buyer bids <= their value)."""

    def __init__(self, scale, opp_is_seller):
        self.scale = float(scale) or 1.0
        self.opp_is_seller = opp_is_seller
        self.grid = [round(self.scale * f, 2) for f in (0.3, 0.5, 0.7, 0.85, 1.0, 1.15, 1.3, 1.5, 1.7)]
        self.w = {v: 1.0 for v in self.grid}
        self.n_evidence = 0
        self.offers = []                                       # raw opponent price path (for reservation_estimate)
        self._norm()

    def _norm(self):
        s = sum(self.w.values())
        if s > 0:
            for v in self.w:
                self.w[v] /= s

    def update_offer(self, price):
        """Opponent offered `price`. Seller's ask bounds value above; buyer's bid bounds it below."""
        band = 0.25 * self.scale
        for v in self.grid:
            near = math.exp(-abs(v - price) / band)
            consistent = (v <= price * 1.05) if self.opp_is_seller else (v >= price * 0.95)
            self.w[v] *= max(near if consistent else 0.15 * near, 1e-6)
        self.n_evidence += 1
        try:
            self.offers.append(float(price))
        except (TypeError, ValueError):
            pass
        self._norm()

    def reservation_estimate(self, k_project=0.5):
        """Estimate the OPPONENT's reservation (a seller-opponent's cost / a buyer-opponent's value) from
        their concession path. They concede toward their reservation: a seller's asks fall toward cost, a
        buyer's bids rise toward value. Project one damped step past their latest offer, clamp to the
        observed range ± a band. Returns None until we have >=2 offers. (Iteration-1: simple trend
        extrapolation; a geometric β-fit / per-opponent prior is the documented iteration-2.)"""
        offs = self.offers
        if len(offs) < 2:
            return None
        last, prev = offs[-1], offs[-2]
        proj = last + (last - prev) * k_project              # project further along their concession
        lo, hi = min(offs), max(offs)
        band = 0.15 * self.scale
        return max(lo - band, min(hi + band, proj))

    def update_message_number(self, num):
        """A number stated in the opponent's message — weak (cheap talk), so a gentle nudge."""
        band = 0.3 * self.scale
        for v in self.grid:
            self.w[v] *= 0.5 + 0.5 * math.exp(-abs(v - num) / band)
        self._norm()

    def p_accept(self, price, my_is_seller):
        """P(opponent accepts `price`): buyer accepts if their value ≥ price; seller if ≤ price."""
        return sum(w for v, w in self.w.items()
                   if (v >= price if my_is_seller else v <= price))

    def price_at_pacc(self, target_pacc, my_value, my_is_seller):
        """The MOST aggressive still-profitable price whose acceptance prob >= target_pacc. Used for
        conditional concede-to-close: price just aggressively enough to clear a target accept quantile,
        rather than EV-max (best_price shades low and gives away share). None if none qualifies."""
        cands = sorted(set(self.grid), reverse=my_is_seller)     # seller high→low, buyer low→high
        for p in cands:
            surplus = (p - my_value) if my_is_seller else (my_value - p)
            if surplus <= 0:
                continue
            if self.p_accept(p, my_is_seller) >= target_pacc:
                return p
        return None

    def best_price(self, my_value, my_is_seller):
        """argmax over candidate prices of surplus × P(accept). None if no profitable price."""
        cands = sorted(set(self.grid + [my_value * 1.1, my_value * 1.25, my_value * 1.5]))
        best, best_ev = None, 0.0
        for p in cands:
            surplus = (p - my_value) if my_is_seller else (my_value - p)
            if surplus <= 0:
                continue
            ev = surplus * self.p_accept(p, my_is_seller)
            if ev > best_ev:
                best, best_ev = p, ev
        return best

    def summary(self):
        exp = sum(v * w for v, w in self.w.items())
        return {"E[value]": round(exp, 1), "n": self.n_evidence}
