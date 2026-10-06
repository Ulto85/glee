"""Archetypes: classify an opponent from features; grab() = how hard to push each type."""

import tw.params as params
from tw.features import clamp


def grab(arch):
    """How much of the surplus we try to claim against this archetype (from params)."""
    g = params.P["grab"]
    return g.get(arch, g["unknown"])


def classify(f):
    """Map trajectory features to (archetype, confidence). An over_conceder must BOTH concede a lot
    AND end up offering us the majority — conceding alone (normal bargaining) is not folding."""
    c = params.P["classify"]
    if f["n"] < 2:
        return "unknown", 0.3
    fin = f.get("final_generosity", f["generosity"])
    if f["concession"] >= c["conc_over"] and fin >= 0.5:
        return "over_conceder", clamp(0.4 + f["concession"] / 2)
    if f["concession"] <= c["conc_hard"] and f["generosity"] <= c["gen_hard"]:
        return "hard_anchorer", 0.7
    return "tit_for_tat", 0.5
