"""Persuasion SELLER evaluation from the per-agent turn log (task #35).
Seller games are NOT in the per-cell baseline (buyer acts last -> game_over comes via opponent), so the
baseline/paired_pct can't see them. But each turn's state carries seller_total_payoff (running cumulative),
so per game_id we take the LAST-observed seller_total_payoff = our realized seller earnings. Normalize by
(total_rounds * product_price) = fraction of max-possible sales revenue captured (scale-free [0,1]), bucket
by p (quality prior). Compare a variant turn-log vs a same-pool reference turn-log (both pers-only farms).
Usage: python experiments/pers_seller.py logs/games_theta.jsonl logs/games_gamma.jsonl
"""
import json, sys, collections, statistics as st

def seller_games(path):
    """game_id -> (max_round, seller_total_payoff, total_rounds, product_price, p)."""
    g = {}
    for l in open(path):
        try: r = json.loads(l)
        except: continue
        if r.get("family") != "persuasion": continue
        ph = str(r.get("phase") or "")
        st_ = r.get("state") or {}
        # we're seller iff our move is a seller message/recommendation
        if "seller" not in ph: continue
        gid = r.get("game_id")
        if gid is None: continue
        rnd = st_.get("round", 0) or 0
        stp = st_.get("seller_total_payoff")
        tot = st_.get("total_rounds") or 20
        pp = st_.get("product_price") or 0
        p = st_.get("p")
        cur = g.get(gid)
        if stp is None: continue
        if cur is None or rnd > cur[0]:
            g[gid] = (rnd, stp, tot, pp, p)
    # keep only near-COMPLETE games (in-progress games have partial seller_total_payoff -> underestimate)
    return {gid: v for gid, v in g.items() if v[0] >= (v[2] or 20) - 1}

def summarize(path, label):
    g = seller_games(path)
    by = collections.defaultdict(list)          # p-bucket -> normalized seller payoff
    raw = []
    for gid,(rnd,stp,tot,pp,p) in g.items():
        denom = (tot or 20) * (pp or 0)
        if denom <= 0: continue
        eff = stp / denom                        # fraction of max sales revenue captured
        pb = f"p{round(p,2)}" if p is not None else "p?"
        by[pb].append(eff); raw.append(eff)
    print(f"{label}: {len(g)} seller games, {len(raw)} scored")
    if raw:
        print(f"  ALL: mean sales-efficiency {st.mean(raw):.3f} (n={len(raw)})")
        for pb in sorted(by):
            xs = by[pb]
            if len(xs) >= 5:
                print(f"    {pb}: {st.mean(xs):.3f}  (n={len(xs)})")
    return raw

print("=== PERSUASION SELLER sales-efficiency (seller_total_payoff / max-possible) ===")
pa = sys.argv[1] if len(sys.argv) > 1 else "logs/games_theta.jsonl"
pb = sys.argv[2] if len(sys.argv) > 2 else "logs/games_gamma.jsonl"
a = summarize(pa, f"ARG1 {pa.split('/')[-1]}")
b = summarize(pb, f"ARG2 {pb.split('/')[-1]}")
if a and b:
    print(f"\n  DELTA (arg1 - arg2): {st.mean(a)-st.mean(b):+.3f}  "
          f"(arg1 {st.mean(a):.3f} n={len(a)} vs arg2 {st.mean(b):.3f} n={len(b)})")
