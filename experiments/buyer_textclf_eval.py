"""Independent validation of task #34: buyer message text-classifier for persuasion.
Reproduces the auditor's temporal-holdout claim from scratch + leakage & regression checks.
Labels: history entries with an explicit `quality` field (present on non-bought rounds too).
Temporal split: earliest 80% train / latest 20% test (file order ~ temporal). No random shuffle.
"""
import json, math
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.linear_model import LogisticRegression

HEDGE=("ordinary","still available","reasonable","decent","fair unit","okay","ok unit","average","standard","acceptable","not bad")
def keyword_signal(m):
    m=(m or "").strip().lower()
    if m in ("no","n","false","0") or "do not recommend" in m or "not recommend" in m or "hold" in m or "wouldn't" in m or "avoid" in m: return "hold"
    if any(h in m for h in HEDGE): return "hedge"
    if m in ("yes","y","true","1") or "recommend" in m or "worth buying" in m or "strong" in m or "buy" in m: return "buy"
    return "neutral"

# collect ONE (message, quality) per (game_id, round) — the true observation unit. Preserves frequency
# (no full-dedup) but avoids the per-turn history-repeat duplication that leaks across the split.
obs={}  # (game_id, round) -> (msg, label); first_seen_order tracked via insertion
order=[]
for l in open("logs/games.jsonl"):
    if not l.strip(): continue
    r=json.loads(l)
    if r.get("family")!="persuasion": continue
    gid=r.get("game_id")
    for e in (r.get("state",{}).get("history") or []):
        q=e.get("quality"); msg=e.get("seller_message"); rd=e.get("round")
        if q is None or msg is None: continue
        key=(gid,rd)
        if key not in obs:
            obs[key]=(msg, 1 if str(q).lower()=="high" else 0); order.append((gid,key))
# temporal split BY GAME (a game's first-seen file order); no game spans train/test
game_first={}
for gid,key in order:
    game_first.setdefault(gid, len(game_first))
ngames=len(game_first); gsplit=int(ngames*0.8)
tr=[obs[k] for _,k in order if game_first[k[0]]<gsplit]
te=[obs[k] for _,k in order if game_first[k[0]]>=gsplit]
n=len(obs); split=len(tr)
Xtr=[m for m,_ in tr]; ytr=[y for _,y in tr]
Xte=[m for m,_ in te]; yte=[y for _,y in te]
print(f"unique (msg,label) pairs={n}  train={len(tr)} test={len(te)}  test base-rate P(high)={sum(yte)/len(yte):.3f}")

vec=CountVectorizer(ngram_range=(1,2), min_df=20, lowercase=True)
Xtr_v=vec.fit_transform(Xtr); Xte_v=vec.transform(Xte)
clf=LogisticRegression(max_iter=1000, C=1.0)
clf.fit(Xtr_v, ytr)
proba=clf.predict_proba(Xte_v)[:,1]

def metrics(probs, ys):
    ll=sum(-(y*math.log(max(p,1e-6))+(1-y)*math.log(max(1-p,1e-6))) for p,y in zip(probs,ys))/len(ys)
    acc=sum((round(p)==y) for p,y in zip(probs,ys))/len(ys)
    return ll,acc

# baselines need a per-row prior p — approximate with the TRAIN base rate as the 'market prior' proxy
p_prior=sum(ytr)/len(ytr)
prior_probs=[p_prior]*len(yte)
pen_probs=[max(0.0,p_prior-0.10)]*len(yte)

# NEUTRAL bucket (keyword-missed) — the target
neu=[i for i,m in enumerate(Xte) if keyword_signal(m)=="neutral"]
def sub(probs,idx): return [probs[i] for i in idx]
print("\n--- NEUTRAL bucket (keyword-missed), n_test =", len(neu), "---")
for name,pr in [("prior p",prior_probs),("p-0.10 penalty",pen_probs),("bow-LR",list(proba))]:
    ll,acc=metrics(sub(pr,neu),[yte[i] for i in neu]); print(f"  {name:16s} logloss={ll:.3f} acc={acc:.3f}")

print("\n--- FULL channel (all test msgs), n =", len(yte), "---")
for name,pr in [("prior p",prior_probs),("bow-LR",list(proba))]:
    ll,acc=metrics(pr,yte); print(f"  {name:16s} logloss={ll:.3f} acc={acc:.3f}")

# REGRESSION check: on CONFIDENT keyword buckets (buy/hold), does clf keep them right?
for bucket in ("buy","hold"):
    idx=[i for i,m in enumerate(Xte) if keyword_signal(m)==bucket]
    if idx:
        ll,acc=metrics(sub(list(proba),idx),[yte[i] for i in idx])
        base=sum(yte[i] for i in idx)/len(idx)
        print(f"confident '{bucket}' bucket n={len(idx)}: clf acc={acc:.3f} (actual P(high)={base:.3f})")

# LEAKAGE sniff: top tokens
import numpy as np
names=vec.get_feature_names_out(); coef=clf.coef_[0]
top_hi=np.argsort(coef)[-8:]; top_lo=np.argsort(coef)[:8]
print("\ntop HIGH-signal tokens:", [names[i] for i in top_hi][::-1])
print("top LOW-signal tokens:", [names[i] for i in top_lo])
