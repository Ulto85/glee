"""Replay the persuasion BUYER decision sequence under the thin-margin fix (Fable#5), scored with the
REALLY-realized per-round quality (fully observable -> bias-free). Compares actual logged buyer payoff
vs the counterfactual under buyer_clf_guard (pmc calibration shrink + within-game liar-gate), per cell.
Usage: python experiments/replay_pbuyer.py [calib] [liar_n]
"""
import json, sys, collections
sys.path.insert(0, ".")
from tw import textclf

CALIB = float(sys.argv[1]) if len(sys.argv) > 1 else 0.05
LIAR_N = int(sys.argv[2]) if len(sys.argv) > 2 else 3

# collect final history per persuasion buyer game
games = {}
for l in open("logs/games_champion.jsonl"):
    try: r = json.loads(l)
    except: continue
    if r.get("family") != "persuasion": continue
    st = r.get("state") or {}
    a = r.get("action") or {}
    if a.get("decision") not in ("yes", "no"):  # our buyer turns
        continue
    gid = r.get("game_id")
    if gid is None: continue
    h = st.get("history") or []
    v, u, price, p = st.get("v"), st.get("u", 0.0), st.get("product_price"), st.get("p")
    cur = games.get(gid)
    if cur is None or len(h) > len(cur[0]):
        games[gid] = (h, v, u, price, p)

def cell(v, p):
    return f"p{round(p,3) if p else '?'}|v{v}"

act = collections.defaultdict(float); new = collections.defaultdict(float); cnt = collections.Counter()
for gid, (h, v, u, price, p) in games.items():
    if v is None or price is None: continue
    u = u or 0.0
    c = cell(v, p)
    cnt[c] += 1
    hi = lo = 0                                  # within-game bought record (for liar-gate)
    for e in h:
        q = e.get("quality"); msg = e.get("seller_message")
        realized = (v if q == "high" else u)
        # ACTUAL logged payoff this round
        if e.get("bought"): act[c] += realized - price
        # COUNTERFACTUAL under the fix
        pmc = textclf.p_high(msg)
        buy = False
        if pmc is not None:
            pmc_adj = max(0.0, pmc - CALIB)
            buy = (pmc_adj * v + (1 - pmc_adj) * u) >= price
            if buy and (hi + lo) >= LIAR_N:
                emp = hi / (hi + lo)
                if (emp * v + (1 - emp) * u) < price:
                    buy = False
        if buy:
            new[c] += realized - price
            if q == "high": hi += 1
            elif q == "low": lo += 1
        # NOTE: liar-gate only learns from rounds WE (counterfactually) bought; matches live behavior

print(f"=== replay persuasion buyer: ACTUAL vs FIX (calib={CALIB}, liar_n={LIAR_N}) — negative cells ===")
tot_a = tot_n = 0
for c in sorted(act, key=lambda k: act[k]):
    if cnt[c] < 10: continue
    a, nw = act[c] / cnt[c], new[c] / cnt[c]
    tot_a += act[c]; tot_n += new[c]
    flag = "  <-- was NEGATIVE" if a < 0 else ""
    if a < 0 or abs(nw - a) > 1:
        print(f"  {c}: actual {a:+.0f}/game -> fix {nw:+.0f}/game  (n={cnt[c]}){flag}")
print(f"  TOTAL buyer payoff across all cells: actual {tot_a:+.0f} -> fix {tot_n:+.0f}")
