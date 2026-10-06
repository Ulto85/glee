"""Diagnose: turn the raw turn-log into a behavioral report the optimizer can reason about.

The old proposer only saw a scalar fitness — too thin to discover structural fixes. This gives it
Eureka/FunSearch-style *execution feedback*: where we walk from profitable deals, how aggressive
our counters are, how greedy we are by horizon. That is what lets the optimizer find fixes like V2
(accept the bird in hand under an unknown horizon) instead of only nudging knobs.
"""
import json
import statistics as st
from collections import defaultdict
from pathlib import Path

_GAMES = Path("logs/games.jsonl")


def _tail(path, n):
    if not path.exists():
        return []
    lines = path.read_text(errors="ignore").splitlines()
    return lines[-n:]


def diagnose(recent=4000, family=None):
    """Behavioral stats over the last `recent` turns. Returns {family: {metric: value}}."""
    neg, barg = [], []
    for line in _tail(_GAMES, recent):
        try:
            d = json.loads(line)
        except Exception:
            continue
        fam, s, a = d.get("family"), d.get("state", {}), d.get("action", {})
        if family and fam != family:
            continue
        me = s.get("current_player")
        if not me or "decision" not in a:
            continue
        lo = s.get("last_offer") or {}
        if fam == "negotiation":
            role, val, price = s.get(f"{me}_role"), s.get(f"{me}_value"), lo.get("price")
            if val is None or price is None:
                continue
            scale = s.get("product_price_order") or val or 1
            surplus = (price - val) if role == "seller" else (val - price)
            counter = a.get("product_price")
            neg.append((a["decision"], surplus / scale, surplus > 0,
                        (abs(counter - price) / scale) if counter is not None else None,
                        bool(s.get("horizon_known", True)), s.get("round", 1)))
        elif fam == "bargaining":
            money = s.get("money_to_divide") or 1
            g = lo.get(f"{me}_gain")
            if g is None:
                continue
            barg.append((a["decision"], g / money))

    out = {}
    if neg:
        acc = [r for r in neg if r[0] == "AcceptOffer"]
        rej = [r for r in neg if r[0] != "AcceptOffer"]
        prof_rej = [r for r in rej if r[2]]
        unk = [r for r in neg if not r[4]]
        unk_acc = [r for r in unk if r[0] == "AcceptOffer"]
        cg = [r[3] for r in rej if r[3] is not None]
        out["negotiation"] = {
            "decisions": len(neg),
            "accept_rate": round(len(acc) / len(neg), 3),
            "unknown_hz_accept_rate": round(len(unk_acc) / max(len(unk), 1), 3),
            "profitable_reject_rate": round(len(prof_rej) / max(len(rej), 1), 3),
            "median_walked_surplus_frac": round(st.median([r[1] for r in prof_rej]), 3) if prof_rej else 0.0,
            "counter_aggressiveness_frac": round(st.median(cg), 3) if cg else 0.0,
            "median_accept_surplus_frac": round(st.median([r[1] for r in acc]), 3) if acc else 0.0,
        }
    if barg:
        bacc = [r[1] for r in barg if r[0] == "accept"]
        brej = [r[1] for r in barg if r[0] != "accept"]
        out["bargaining"] = {
            "decisions": len(barg),
            "accept_rate": round(len(bacc) / len(barg), 3),
            "median_accept_share": round(st.median(bacc), 3) if bacc else 0.0,
            "median_reject_share": round(st.median(brej), 3) if brej else 0.0,
            "reject_rate_of_fair_offers(>=0.4)": round(sum(1 for x in brej if x >= 0.4) / max(len(brej), 1), 3),
        }
    return out


def report(recent=4000, family=None):
    """A short human/LLM-readable interpretation with flagged pathologies."""
    d = diagnose(recent, family)
    lines = []
    n = d.get("negotiation")
    if n:
        lines.append(f"NEGOTIATION ({n['decisions']} decisions): accept_rate={n['accept_rate']}, "
                     f"unknown_horizon_accept_rate={n['unknown_hz_accept_rate']}, "
                     f"profitable_reject_rate={n['profitable_reject_rate']} "
                     f"(median surplus walked from = {n['median_walked_surplus_frac']} of scale), "
                     f"counter_aggressiveness={n['counter_aggressiveness_frac']} of scale.")
        lines.append("  NOTE: these are DECISION-turn stats — they count our rejects but NOT games won when "
                     "the opponent accepts our (tough) offer, so they OVER-state greed. A 14v14 A/B showed "
                     "conceding on these signals REGRESSED share (0.45->0.22). Treat flags as hypotheses "
                     "ONLY — the online A/B (no_deal, share_on_deals) is the sole judge; never ship on the flag alone.")
        if n["unknown_hz_accept_rate"] < 0.1:
            lines.append("  ? low unknown-horizon accept rate — MIGHT be over-holding, but toughness may be extracting share.")
        if n["counter_aggressiveness_frac"] > 0.25:
            lines.append("  ? counters land far from their offer — MIGHT cause walks, or MIGHT anchor a better price.")
    b = d.get("bargaining")
    if b:
        lines.append(f"BARGAINING ({b['decisions']} decisions): accept_rate={b['accept_rate']}, "
                     f"median_accept_share={b['median_accept_share']}, "
                     f"median_reject_share={b['median_reject_share']}, "
                     f"reject_rate_of_fair(>=0.4)_offers={b['reject_rate_of_fair_offers(>=0.4)']}.")
        if b["median_accept_share"] < 0.55:
            lines.append("  ⚠ we settle near 50/50 — with near-zero no-deal risk there is room to demand more.")
    return "\n".join(lines) if lines else "(no recent decision data)"


if __name__ == "__main__":
    import sys
    fam = sys.argv[1] if len(sys.argv) > 1 else None
    print(report(family=fam))
