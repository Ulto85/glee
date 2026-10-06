"""Counterfactual replay: a LOW-NOISE evaluator for accept-side policy, built on logged games.

The online A/B is ~44% noise at feasible n because it compares two policies on DIFFERENT random
games. Replay removes every source of that noise: it re-runs a candidate policy on the EXACT recorded
decision points the champion faced (same config, same opponent offers), deterministically, spending
zero new rated games. Config + opponent variance cancel because it's perfectly paired.

Scope & rigor: this is UNBIASED for policies that accept offers that ACTUALLY APPEARED in the log
(e.g. 'accept earlier / more readily' — V2, accept-floor). It CANNOT see the continuation after a
point where the candidate rejects an offer the champion accepted (the game ended there), so such
games are marked CENSORED and excluded from the paired comparison; we report the censoring rate so a
high one flags an incomplete verdict. Offer/counter policy (what WE propose) is out of scope here —
the opponent's response is counterfactual; use the sim or a large-N online check for those.
"""
import json
import os
from collections import defaultdict
from pathlib import Path

import tw.params as params
from tw import policy

_GAMES = Path("logs/games.jsonl")


def load_decisions(family, recent=200000):
    """Group logged DECISION points by game, in round order. Each carries the recorded state, our
    seat, the archetype we had classified then, and the offer's value to us."""
    games = defaultdict(list)
    lines = _GAMES.read_text(errors="ignore").splitlines()[-recent:]
    for line in lines:
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("family") != family:
            continue
        s, a = d.get("state", {}), d.get("action", {})
        if "decision" not in a:
            continue
        me = s.get("current_player")
        lo = s.get("last_offer") or {}
        if family == "bargaining":
            money = s.get("money_to_divide") or 1
            g = lo.get(f"{me}_gain")
            if g is None:
                continue
            # DISCOUNT-FORWARD (audit): realized payoff = M·δ^(round-1)·share. Ranking raw share made the
            # whole eval reward holding out (the exact wrong thing) — discount it so accepting-earlier wins.
            didx = str(me).split("_")[-1]
            dlt = float(s.get(f"delta_{didx}", 0.9) or 0.9)
            rnd = s.get("round", 1)
            val = (g / money) * (dlt ** (rnd - 1))
        else:
            role, myv, price = s.get(f"{me}_role"), s.get(f"{me}_value"), lo.get("price")
            scale = s.get("product_price_order") or myv or 1
            if myv is None or price is None:
                continue
            val = ((price - myv) if role == "seller" else (myv - price)) / scale
        games[d["game_id"]].append({"state": s, "me": me, "arch": d.get("archetype", "unknown"),
                                    "val": val, "round": s.get("round", 1),
                                    "did_accept": a["decision"] in ("accept", "AcceptOffer")})
    for gid in games:
        games[gid].sort(key=lambda r: r["round"])
    return games


def _accepts(family, rec):
    """Does the CURRENT policy (params.P) accept the offer at this recorded decision point?"""
    if family == "bargaining":
        act = policy.bargaining({"type": "decision"}, rec["state"], rec["me"], rec["arch"])
    else:
        v2 = os.environ.get("GLEE_NEG_V2", "0") == "1"     # match live toggle; set to test V2
        act = policy.negotiation({"type": "decision"}, rec["state"], rec["me"], rec["arch"], v2=v2)
    return act.get("decision") in ("accept", "AcceptOffer")


def replay(family, games=None, recent=200000):
    """Per game, the value the current policy realizes = the value of the FIRST recorded offer it
    accepts. Returns (values, censored) where censored games accepted nothing in the observed
    sequence (their continuation is unknown)."""
    games = games if games is not None else load_decisions(family, recent)
    values, censored = {}, set()
    for gid, recs in games.items():
        got = None
        for rec in recs:
            if _accepts(family, rec):
                got = max(0.0, rec["val"]); break
        if got is None:
            censored.add(gid)
        else:
            values[gid] = got
    return values, censored


def compare(family, champion_params, candidate_params, recent=200000):
    """Paired champion-vs-candidate on identical logged games. Compares only games BOTH decide within
    the observed offers (excludes censored either side); reports means, the paired delta, and censor
    rates so an incomplete verdict is visible."""
    games = load_decisions(family, recent)
    params.P.clear(); params.P.update(params._merge(params.DEFAULTS, champion_params))
    cv, cc = replay(family, games)
    params.P.clear(); params.P.update(params._merge(params.DEFAULTS, candidate_params))
    kv, kc = replay(family, games)
    both = [g for g in games if g not in cc and g not in kc]
    if not both:
        return {"n_paired": 0}
    champ = sum(cv[g] for g in both) / len(both)
    cand = sum(kv[g] for g in both) / len(both)
    wins = sum(kv[g] > cv[g] for g in both)
    losses = sum(kv[g] < cv[g] for g in both)
    return {"n_paired": len(both), "n_games": len(games),
            "champ_mean": round(champ, 4), "cand_mean": round(cand, 4),
            "delta": round(cand - champ, 4), "cand_wins": wins, "cand_losses": losses,
            "censor_champ": round(len(cc) / len(games), 3), "censor_cand": round(len(kc) / len(games), 3)}
