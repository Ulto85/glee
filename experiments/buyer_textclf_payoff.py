"""Decisive gate for task #34: realized BUYER PAYOFF (not just prediction) of the text-classifier vs
baselines, on a held-out by-game temporal split. Quality is known on every round, so this is an unbiased
counterfactual: for each round compute each policy's buy/no and its realized payoff (buy -> v-price if
high else u-price; no -> 0). If the classifier's payoff clearly beats prior AND keyword+penalty, ship.
"""
import json, math, re
_TOK=re.compile(r"(?u)\b\w\w+\b")
art=json.load(open("logs/buyer_textclf.json"))
def clf_p(msg):
    toks=_TOK.findall((msg or "").lower())
    grams=list(toks)+[toks[i]+" "+toks[i+1] for i in range(len(toks)-1)]
    z=art.get("bias",0.0)+sum(art["weights"].get(g,0.0) for g in grams)
    return 1.0/(1.0+math.exp(-max(-30,min(30,z))))
HEDGE=("ordinary","still available","reasonable","decent","fair unit","okay","ok unit","average","standard","acceptable","not bad")
def kw(m):
    m=(m or "").strip().lower()
    if m in ("no","n","false","0") or "do not recommend" in m or "not recommend" in m or "hold" in m or "wouldn't" in m or "avoid" in m: return "hold"
    if any(h in m for h in HEDGE): return "hedge"
    if m in ("yes","y","true","1") or "recommend" in m or "worth buying" in m or "strong" in m or "buy" in m: return "buy"
    return "neutral"

# per game: constants p,v,u,price + rounds [(msg, is_high)]
games={}; order=[]
for l in open("logs/games.jsonl"):
    if not l.strip(): continue
    r=json.loads(l)
    if r.get("family")!="persuasion": continue
    st=r.get("state",{}); gid=r.get("game_id")
    v=st.get("v"); u=st.get("u",0.0); price=st.get("product_price"); p=st.get("p")
    if gid not in games:
        games[gid]={"p":p,"v":v,"u":u,"price":price,"rounds":{}}; order.append(gid)
    g=games[gid]
    if g["v"] is None and v is not None: g.update(v=v,u=u,price=price,p=p)
    for e in (st.get("history") or []):
        q=e.get("quality"); msg=e.get("seller_message"); rd=e.get("round")
        if q is None or msg is None or rd is None: continue
        g["rounds"].setdefault(rd,(msg, 1 if str(q).lower()=="high" else 0))
# temporal split by game
gsplit=int(len(order)*0.8); test=order[gsplit:]

def realized(pm, is_high, v,u,price,margin=0.0):
    buy = (pm*v+(1-pm)*u) >= price*(1+margin)
    return ((v-price) if is_high else (u-price)) if buy else 0.0

def pm_keyword(msg,p):
    s=kw(msg)
    if s=="hold": return 0.0
    if s=="hedge": return max(0.0,(p or 0.5)-0.10) if (p is not None and p<0.6) else (p or 0.5)
    if s=="neutral": return max(0.0,(p or 0.5)-0.10)
    return max(p or 0.7, 0.7)   # 'buy' -> lean high (approx prior-anchor optimism)

tot={"prior":0.0,"keyword":0.0,"clf":0.0}; N=0; buys={"prior":0,"keyword":0,"clf":0}
for gid in test:
    g=games[gid]; p=g["p"]; v=g["v"]; u=g["u"]; price=g["price"]
    if v is None or price is None or p is None: continue
    for rd,(msg,is_high) in g["rounds"].items():
        N+=1
        pmk=pm_keyword(msg,p); pmc=clf_p(msg)
        # hold hard-refuse applies to keyword & clf (both keep it)
        if kw(msg)=="hold": pmk=0.0
        tot["prior"]+=realized(p,is_high,v,u,price)
        tot["keyword"]+=realized(pmk,is_high,v,u,price)
        tot["clf"]+=realized(0.0 if kw(msg)=="hold" else pmc,is_high,v,u,price)
        buys["prior"]+= (p*v+(1-p)*u>=price)
        buys["keyword"]+= (pmk*v+(1-pmk)*u>=price)
        buys["clf"]+= ((0.0 if kw(msg)=="hold" else pmc)*v+(1-(0.0 if kw(msg)=="hold" else pmc))*u>=price)
print(f"held-out rounds N={N} (test games={len(test)})")
for k in ("prior","keyword","clf"):
    print(f"  {k:8s} total realized buyer payoff = {tot[k]:>16,.0f}   avg/round={tot[k]/N:>10,.1f}   buy-rate={buys[k]/N:.2f}")
print(f"  clf vs prior:  {tot['clf']-tot['prior']:+,.0f}  ({100*(tot['clf']-tot['prior'])/max(abs(tot['prior']),1):+.1f}%)")
print(f"  clf vs keyword:{tot['clf']-tot['keyword']:+,.0f}  ({100*(tot['clf']-tot['keyword'])/max(abs(tot['keyword']),1):+.1f}%)")
