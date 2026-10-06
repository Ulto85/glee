"""Evaluator: tell a real ruleset improvement from noise via A/B on FRESH games.

Fitness = avg_our_share (scale-free: our payoff / (ours+theirs)), which is comparable across
the wildly different money scales, plus rating delta. A bootstrap gives P(challenger > champion)
so the optimizer stops chasing noise (the failure mode that hid the offer-key bug).
"""

import random

import tw.params as params
from tw import analyzer


def _shares_by_family(outcomes):
    """Per-family list of our_share values (drops no-deals / undefined)."""
    out = {}
    for o in outcomes:
        r = o.get("result") or {}
        me = o.get("your_player", "player_1")
        other = "player_1" if me == "player_2" else "player_2"
        mine, theirs = r.get(f"{me}_payoff"), r.get(f"{other}_payoff")
        if mine is None or theirs is None or (abs(mine) + abs(theirs)) == 0:
            continue
        out.setdefault(o["family"], []).append(mine / (abs(mine) + abs(theirs)))
    return out


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def bootstrap_win_prob(champ, chall, iters=4000):
    """P(mean(challenger) > mean(champion)) by resampling — a cheap significance signal."""
    if not champ or not chall:
        return None
    wins = 0
    for _ in range(iters):
        a = _mean([random.choice(champ) for _ in champ])
        b = _mean([random.choice(chall) for _ in chall])
        wins += (b > a)
    return round(wins / iters, 3)


MIN_N = 8          # per-arm games in a family before its verdict counts (guards small-n false positives)


def evaluate(play, champ_params, chall_params, n_per_arm=10,
             families=("negotiation", "bargaining")):
    """Block A/B: play n games under each ruleset on fresh games, compare, restore champion.

    `play(n, families) -> outcomes` plays n games under whatever params are currently live.
    """
    params.save(champ_params); params.reload()
    out_champ = play(n_per_arm, families)
    params.save(chall_params); params.reload()
    out_chall = play(n_per_arm, families)
    params.save(champ_params); params.reload()                # always restore champion

    sc, sh = _shares_by_family(out_champ), _shares_by_family(out_chall)
    report, keep = {}, False
    for fam in families:
        c, h = sc.get(fam, []), sh.get(fam, [])
        p = bootstrap_win_prob(c, h)
        enough = len(c) >= MIN_N and len(h) >= MIN_N          # ignore under-powered families
        report[fam] = {
            "champ_share": round(_mean(c), 3) if c else None, "champ_n": len(c),
            "chall_share": round(_mean(h), 3) if h else None, "chall_n": len(h),
            "P(chall>champ)": p, "enough_n": enough,
        }
        if enough and p is not None and p >= 0.8:             # adopt only with power AND confidence
            keep = True
    return {"per_family": report, "verdict": "adopt" if keep else "reject",
            "note": f"a family counts only with >={MIN_N} games/arm; small-n or wrong-dimension wins are ignored"}
