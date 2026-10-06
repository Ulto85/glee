"""Analyzer: compress a batch of game outcomes into a compact report for the optimizer."""


def _our(outcome):
    """Our realized payoff and the opponent's, from a finished result dict."""
    r = outcome.get("result") or {}
    me = outcome.get("your_player", "player_1")
    other = "player_1" if me == "player_2" else "player_2"
    return r.get(f"{me}_payoff"), r.get(f"{other}_payoff")


def summarize(outcomes):
    """Per-family diagnostics: games, no-deal rate, and scale-free share of the pie we took."""
    fams = {}
    for o in outcomes:
        f = o.get("family", "?")
        d = fams.setdefault(f, {"games": 0, "no_deal": 0, "shares": [], "payoffs": []})
        d["games"] += 1
        mine, theirs = _our(o)
        if o.get("result", {}).get("outcome") in ("no_deal",) or (mine == 0 and theirs == 0):
            d["no_deal"] += 1
        if mine is not None:
            d["payoffs"].append(mine)
        if mine is not None and theirs is not None and (abs(mine) + abs(theirs)) > 0:
            d["shares"].append(mine / (abs(mine) + abs(theirs)))
    out = {}
    for f, d in fams.items():
        shares = d["shares"]
        out[f] = {
            "games": d["games"],
            "no_deal_rate": round(d["no_deal"] / d["games"], 2) if d["games"] else 0.0,
            "avg_our_share": round(sum(shares) / len(shares), 3) if shares else None,  # >0.5 = we took more
            "avg_our_payoff": round(sum(d["payoffs"]) / len(d["payoffs"]), 1) if d["payoffs"] else None,
        }
    return out


def report_text(summary, rating_delta, params):
    """A short human/LLM-readable report of one batch."""
    lines = ["BATCH REPORT", "per-family outcomes (avg_our_share > 0.5 means we captured more than the opponent):"]
    for f, s in summary.items():
        lines.append(f"  {f:12s} games={s['games']:2d}  no_deal={s['no_deal_rate']:.2f}  "
                     f"our_share={s['avg_our_share']}  our_payoff={s['avg_our_payoff']}")
    lines.append("rating change this batch (server percentile-adjusted fitness):")
    for f, d in rating_delta.items():
        lines.append(f"  {f:12s} {d:+.2f}")
    return "\n".join(lines)
