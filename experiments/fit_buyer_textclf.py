"""Fit the buyer message text-classifier (task #34) and export a dependency-free frozen artifact.
Exports logs/buyer_textclf.json = {bias, weights:{ngram:w}}. Self-verifies the pure-python inference
(tw.textclf.p_high) reproduces sklearn's predict_proba, so the shipped farm needs no sklearn.
"""
import json, re, math
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.linear_model import LogisticRegression

obs={}; order=[]
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
            obs[key]=(msg, 1 if str(q).lower()=="high" else 0)
X=[m for m,_ in obs.values()]; y=[t for _,t in obs.values()]
print(f"training on {len(X)} (game,round) obs, P(high)={sum(y)/len(y):.3f}")

vec=CountVectorizer(ngram_range=(1,2), min_df=20, lowercase=True)  # default token_pattern \b\w\w+\b
Xv=vec.fit_transform(X)
clf=LogisticRegression(max_iter=1000, C=1.0).fit(Xv, y)
vocab=vec.get_feature_names_out(); coef=clf.coef_[0]; bias=float(clf.intercept_[0])
weights={vocab[i]: float(coef[i]) for i in range(len(vocab)) if abs(coef[i])>1e-4}
art={"bias": bias, "weights": weights}
json.dump(art, open("logs/buyer_textclf.json","w"))
print(f"exported {len(weights)} ngram weights + bias to logs/buyer_textclf.json")

# --- pure-python inference (must match tw/textclf.py) ---
_TOK=re.compile(r"(?u)\b\w\w+\b")
def p_high(msg, art):
    toks=_TOK.findall((msg or "").lower())
    grams=list(toks)+[toks[i]+" "+toks[i+1] for i in range(len(toks)-1)]
    z=art["bias"]+sum(art["weights"].get(g,0.0) for g in grams)
    return 1.0/(1.0+math.exp(-max(-30,min(30,z))))

# self-verify vs sklearn on 400 samples
import numpy as np
idx=list(range(0,len(X),max(1,len(X)//400)))[:400]
sk=clf.predict_proba(vec.transform([X[i] for i in idx]))[:,1]
mine=[p_high(X[i], art) for i in idx]
maxerr=max(abs(a-b) for a,b in zip(sk,mine))
print(f"pure-python vs sklearn max abs proba error over {len(idx)} samples = {maxerr:.4f}  ({'OK' if maxerr<0.02 else 'MISMATCH'})")
