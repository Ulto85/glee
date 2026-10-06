"""Independent replay of the SCOPED buyer gates (Fable#10 F1, pers_pbuyer_scoped). The gates are
REMOVE-ONLY (only flip buy->no) so replaying the logged game sequence is bias-free: a refused round
contributes 0 instead of its realized payoff; we never add a buy. Walks each persuasion buyer game's
final history, maintaining the COUNTERFACTUAL bought-record (hi/lo) and the every-round recommend count
(f_rec is observed each round). Compares CURRENT live buyer (memoryless pmc-EV) vs SCOPED, per cell:
total payoff, negative-game count, worst single-game payoff. Confirms +EV cells are bit-identical.
Usage: python experiments/replay_scoped_buyer.py [log] [slack] [minrounds]
"""
import json, sys, collections
sys.path.insert(0, ".")
from tw import textclf
from tw.policy import _pers_signal, _pers_history_stats

LOG   = sys.argv[1] if len(sys.argv) > 1 else "logs/games_champion.jsonl"
SLACK = float(sys.argv[2]) if len(sys.argv) > 2 else 0.05
MINR  = int(sys.argv[3]) if len(sys.argv) > 3 else 3

# final history per buyer game
games = {}
for l in open(LOG):
    try: r = json.loads(l)
    except: continue
    if r.get("family") != "persuasion": continue
    a = r.get("action") or {}
    if a.get("decision") not in ("yes", "no"): continue      # our buyer turns
    st = r.get("state") or {}
    gid = r.get("game_id")
    if gid is None: continue
    h = st.get("history") or []
    cur = games.get(gid)
    if cur is None or len(h) > len(cur[0]):
        games[gid] = (h, st.get("v"), st.get("u", 0.0), st.get("product_price"), st.get("p"),
                      st.get("seller_message_type"))

def cell(v, p, price, mt):
    r = round(v / price, 2) if (v and price) else "?"
    return f"p{p}|r{r}|m{mt}"

cur_pay = collections.defaultdict(float); new_pay = collections.defaultdict(float)
cur_neg = collections.Counter();          new_neg = collections.Counter()
cur_worst = {};                            new_worst = {}
cnt = collections.Counter(); changed_pos_cell = set()

for gid, (h, v, u, price, p, mt) in games.items():
    if v is None or price is None or p is None: continue
    u = u or 0.0
    c = cell(v, p, price, mt)
    cnt[c] += 1
    prior_ev = p * v + (1 - p) * u
    is_neg_cell = prior_ev < price
    gc = gn = 0.0                              # game payoff current / scoped
    hi = lo = rec = rounds = 0                 # counterfactual records (scoped path)
    for e in h:
        q = e.get("quality"); msg = e.get("seller_message")
        realized = (v if q == "high" else u) - price
        sig = _pers_signal(msg); pmc = textclf.p_high(msg)
        base = False
        if sig != "hold" and pmc is not None:
            base = (pmc * v + (1 - pmc) * u) >= price
        # CURRENT live buyer (no scoping)
        if base: gc += realized
        # SCOPED buyer (remove-only, -EV cells only)
        buy = base
        if buy and is_neg_cell:
            if rounds >= MINR and rec > 0:
                f_rec = rec / rounds
                phb = min(1.0, p / f_rec)
                if phb * v + (1 - phb) * u < price * (1 + SLACK):
                    buy = False
            if buy and (hi + lo) >= 2:
                emp2 = hi / (hi + lo); n2 = hi + lo
                post = (2.0 * p + hi) / (2.0 + n2)
                if post * v + (1 - post) * u < price:
                    buy = False
        if buy:
            gn += realized
            if q == "high": hi += 1
            elif q == "low": lo += 1
        # every-round observations (for next round's gates)
        if sig == "buy": rec += 1
        rounds += 1
    cur_pay[c] += gc; new_pay[c] += gn
    if gc < -1e-9: cur_neg[c] += 1
    if gn < -1e-9: new_neg[c] += 1
    cur_worst[c] = min(cur_worst.get(c, 0.0), gc)
    new_worst[c] = min(new_worst.get(c, 0.0), gn)
    if not is_neg_cell and abs(gc - gn) > 1e-9: changed_pos_cell.add(c)

# normalize per game by (rounds*price)? report raw sums + per-cell worst for interpretability
tot_cn = sum(cur_neg.values()); tot_nn = sum(new_neg.values())
print(f"=== scoped buyer replay — {LOG.split('/')[-1]} (slack={SLACK}, minrounds={MINR}) ===")
print(f"  NEGATIVE games: current {tot_cn} -> scoped {tot_nn}  (removed {tot_cn-tot_nn})")
print(f"  +EV cells changed (MUST be 0): {len(changed_pos_cell)}  {sorted(changed_pos_cell)[:5]}")
print(f"  -EV cells where worst-case improved:")
for c in sorted(cur_worst, key=lambda k: cur_worst[k]):
    if cnt[c] < 10: continue
    cw, nw = cur_worst[c], new_worst.get(c, 0.0)
    if cw < -1e-9 and (cw - nw) < -1e-9:
        print(f"    {c}: worst {cw:+.2f} -> {nw:+.2f} | neg {cur_neg[c]}->{new_neg[c]} | payoff {cur_pay[c]:+.1f}->{new_pay[c]:+.1f} (n={cnt[c]})")
