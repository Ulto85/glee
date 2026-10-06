"""Features: turn a sequence of opponent offers into simple, interpretable statistics."""

from statistics import mean


def clamp(x, lo=0.0, hi=1.0):
    """Keep a value inside [lo, hi]."""
    return max(lo, min(hi, x))


def trajectory_features(goodness, scale):
    """From offers scored 'good for me', estimate their opening generosity, where they END UP, and
    total concession. `concession` is now CUMULATIVE (last-first)/(0.4·scale), not a per-round rate
    blown up — the old version flagged any normal concession as over-conceding (54/120 opponents)."""
    if scale <= 0:
        scale = 1.0
    n = len(goodness)
    f = {"n": n, "generosity": 0.5, "final_generosity": 0.5, "concession": 0.0}
    if n >= 1:
        f["generosity"] = clamp(goodness[0] / scale)
        f["final_generosity"] = clamp(goodness[-1] / scale)
    if n >= 2:
        f["concession"] = clamp((goodness[-1] - goodness[0]) / (0.4 * scale))
    return f
