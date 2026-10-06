"""Refined offline validation for the persuasion per-opponent buyer prior (task #29, retest).

Fixes vs pers_oppprior_eval.py:
  (1) LIGHT shrinkage (m=2 pseudocounts toward market prior) so high-confidence opponents barely move.
  (2) UNBIASED metric: predictive quality of P(high | seller="buy") on held-out BOUGHT rounds
      (log-loss + accuracy), NOT decision payoff — the payoff metric was selection-biased against
      selectivity (only scores rounds the logged buyer already bought).
Compares market-prior p vs opponent-conditioned prior at predicting realized quality out-of-sample.
If the opponent prior predicts better, a better-calibrated posterior => better EV buy decisions.
"""
import json, collections, math, statistics as st

HEDGE = ("ordinary","still available","reasonable","decent","fair unit","okay","ok unit","average","standard","acceptable","not bad")
def sig(m):
    m=(m or "").strip().lower()
    if m in ("no","n","false","0") or "do not recommend" in m or "not recommend" in m or "hold" in m or "wouldn't" in m or "avoid" in m: return "hold"
    if any(h in m for h in HEDGE): return "hedge"
    if m in ("yes","y","true","1") or "recommend" in m or "worth buying" in m or "strong" in m or "buy" in m: return "buy"
    return "neutral"
def is_test(gid): return (hash(gid) % 5 == 0)

# longest history per persuasion game with a named opponent
best={}
for l in open("logs/games.jsonl"):
    if not l.strip(): continue
    r=json.loads(l)
    if r.get("family")!="persuasion" or not r.get("opponent"): continue
    st_=r.get("state",{}); h=st_.get("history") or []; g=r["game_id"]
    if g not in best or len(h)>best[g][0]:
        best[g]=(len(h), r["opponent"], st_.get("p"), h)

# TRAIN: per-opponent rec_high/rec_low on buy&bought rounds
prof=collections.defaultdict(lambda:[0,0])
for g,(_,o,p,h) in best.items():
    if is_test(g): continue
    for e in h:
        if not e.get("bought") or sig(e.get("seller_message"))!="buy": continue
        if (e.get("buyer_payoff") or 0)>0: prof[o][0]+=1
        else: prof[o][1]+=1

def clip(x,lo=1e-6,hi=1-1e-6): return max(lo,min(hi,x))
def phat(o,p,m=2.0):
    hi,lo=prof.get(o,[0,0]); n=hi+lo
    return (hi+m*p)/(n+m) if n>0 else p, n

# TEST: predict realized quality on bought+buy rounds; compare market-prior vs opponent-prior
mkt_ll=opp_ll=0.0; mkt_acc=opp_acc=0; N=0; flip_better=flip_worse=0
by_n=collections.Counter()
for g,(_,o,p,h) in best.items():
    if not is_test(g) or p is None: continue
    for e in h:
        if not e.get("bought") or sig(e.get("seller_message"))!="buy": continue
        y = 1 if (e.get("buyer_payoff") or 0)>0 else 0
        po,n = phat(o,p)
        N+=1
        mkt_ll += -(y*math.log(clip(p))+(1-y)*math.log(clip(1-p)))
        opp_ll += -(y*math.log(clip(po))+(1-y)*math.log(clip(1-po)))
        mkt_acc += (round(p)==y); opp_acc += (round(po)==y)
        # did conditioning flip toward the truth?
        if abs(po-y) < abs(p-y)-1e-9: flip_better+=1
        elif abs(po-y) > abs(p-y)+1e-9: flip_worse+=1
        by_n[('n>=20' if n>=20 else 'n<20')]+=1
print(f"held-out bought&buy rounds N={N}   (opponents with n>=20 profiles = {sum(1 for o in prof if sum(prof[o])>=20)})")
print(f"LOG-LOSS  market-prior={mkt_ll/N:.4f}   opponent-prior={opp_ll/N:.4f}   improvement={100*(mkt_ll-opp_ll)/mkt_ll:+.1f}%")
print(f"ACCURACY  market-prior={mkt_acc/N:.3f}   opponent-prior={opp_acc/N:.3f}")
print(f"per-round: opponent-prior closer to truth in {flip_better}, worse in {flip_worse} (net {flip_better-flip_worse:+d})")
print(f"coverage: {dict(by_n)}")
