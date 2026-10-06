"""Offline replay-validation for the persuasion per-opponent honesty prior (task #29).

Splits logged persuasion games (logs/games.jsonl) into train/test by game_id hash.
Builds per-opponent P(high | seller said "buy") on TRAIN (quality recovered on BOUGHT
rounds via buyer_payoff>0). On TEST, scores realized payoff of two buyer policies on
bought rounds (quality is ground-truth there):
  A) current prior-anchor: Beta a0=k*p, b0=k*(1-p)
  B) per-opponent blend:    a0=k*[(1-w)*p + w*p_hat_o], p_hat_o shrunk toward p by n_o
Both then EV-decide buy iff pm*v+(1-pm)*u >= price, updating within-game on prior
bought rounds. Reports total realized payoff A vs B (all bought rounds, and round-1 only).
No live agent is touched — pure offline counterfactual on the observed (bought) subset.
"""
import json, collections, statistics as st

HEDGE = ("ordinary", "still available", "reasonable", "decent", "fair unit", "okay",
         "ok unit", "average", "standard", "acceptable", "not bad")

def sig(m):
    m = (m or "").strip().lower()
    if m in ("no","n","false","0") or "do not recommend" in m or "not recommend" in m \
            or "hold" in m or "wouldn't" in m or "avoid" in m: return "hold"
    if any(h in m for h in HEDGE): return "hedge"
    if m in ("yes","y","true","1") or "recommend" in m or "worth buying" in m \
            or "strong" in m or "buy" in m: return "buy"
    return "neutral"

def is_test(gid): return (hash(gid) % 5 == 0)   # 20% test

# collect longest history per persuasion game with a named opponent
best = {}
for l in open("logs/games.jsonl"):
    if not l.strip(): continue
    r = json.loads(l)
    if r.get("family") != "persuasion" or not r.get("opponent"): continue
    st_ = r.get("state", {})
    h = st_.get("history") or []
    g = r["game_id"]
    if g not in best or len(h) > best[g][0]:
        best[g] = (len(h), r["opponent"], st_.get("p"), st_.get("v"), st_.get("u", 0.0),
                   st_.get("product_price"), h)

# TRAIN profiles: per-opponent high/low on buy&bought rounds
prof = collections.defaultdict(lambda: [0, 0])
for g, (_, o, p, v, u, price, h) in best.items():
    if is_test(g): continue
    for e in h:
        if not e.get("bought") or sig(e.get("seller_message")) != "buy": continue
        if (e.get("buyer_payoff") or 0) > 0: prof[o][0] += 1
        else: prof[o][1] += 1

def phat(o, p, k_shrink=8.0):
    hi, lo = prof.get(o, [0, 0]); n = hi + lo
    if n == 0: return p, 0
    # shrink observed rate toward market prior p by pseudo-count k_shrink
    return (hi + k_shrink * p) / (n + k_shrink), n

def eval_policy(w, k=2.0, liar_only=False):
    tot = collections.Counter(); r1 = collections.Counter()
    tag = "A" if w == 0 else "B"
    for g, (_, o, p, v, u, price, h) in best.items():
        if not is_test(g) or p is None or v is None or not price: continue
        hi_seen = lo_seen = 0
        for e in h:
            known = e.get("bought")
            s = sig(e.get("seller_message"))
            # decide with info available BEFORE this round's outcome
            if w > 0:
                ph, no = phat(o, p)
                if liar_only and ph >= p:      # risk-free half: only DOWN-weight proven liars
                    a0, b0 = k * p, k * (1 - p)
                else:
                    a0 = k * ((1 - w) * p + w * ph); b0 = k - a0 if k - a0 > 0 else 0.01
            else:
                a0, b0 = k * p, k * (1 - p)
            pm = (a0 + hi_seen) / (a0 + b0 + hi_seen + lo_seen)
            if s == "hold": dec = False
            else:
                ev = pm * v + (1 - pm) * u; dec = ev >= price
            if known:  # ground-truth round: score realized payoff
                high = (e.get("buyer_payoff") or 0) > 0
                realized = (v - price) if high else (u - price)
                payoff = realized if dec else 0.0
                tot[tag] += payoff
                if e.get("round") == 1: r1[tag] += payoff
                # update within-game belief from the observed outcome
                if s == "buy":
                    if high: hi_seen += 1
                    else: lo_seen += 1
    return tot, r1

A, A1 = eval_policy(w=0.0)
B, B1 = eval_policy(w=0.7)
C, C1 = eval_policy(w=0.7, liar_only=True)
D, D1 = eval_policy(w=1.0, liar_only=True)
nprof = sum(1 for o in prof if sum(prof[o]) >= 5)
base = A['A']
print(f"train profiles (n>=5) = {nprof}   baseline A(prior) total = {base:,.0f}")
def rpt(name, tot, r1):
    d = tot[list(tot)[0] if tot else 'B'] if False else (tot['B'] - base)
    print(f"  {name:28s} total_delta={tot['B']-base:+,.0f} ({100*(tot['B']-base)/max(abs(base),1):+.2f}%)   round1_delta={r1['B']-A1['A']:+,.0f}")
print("vs baseline prior-anchor:")
rpt("B symmetric blend w=0.7", B, B1)
rpt("C liar-only w=0.7", C, C1)
rpt("D liar-only w=1.0", D, D1)
