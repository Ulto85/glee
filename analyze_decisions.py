"""Diagnose where we leave surplus / cause no-deals, from the turn log.

Looks at our DECISION points (accept/reject) and quantifies the surplus we accept vs
reject, plus our counter-offer aggressiveness. Read-only over logs/games.jsonl.
"""
import json, statistics as st
from collections import defaultdict

rows = defaultdict(list)
with open("logs/games.jsonl") as fh:
    for line in fh:
        try:
            d = json.loads(line)
        except Exception:
            continue
        fam, s, a = d["family"], d.get("state", {}), d.get("action", {})
        me = s.get("current_player")
        if not me or "decision" not in a:
            continue
        lo = s.get("last_offer") or {}
        if fam == "negotiation":
            role = s.get(f"{me}_role"); val = s.get(f"{me}_value"); price = lo.get("price")
            if val is None or price is None:
                continue
            scale = s.get("product_price_order") or val or 1
            surplus = (price - val) if role == "seller" else (val - price)
            counter = a.get("product_price")
            rows["neg"].append({
                "role": role, "dec": a["decision"], "surplus_frac": surplus / scale,
                "profitable": surplus > 0,
                "counter_gap": (abs(counter - price) / scale) if counter is not None else None,
                "round": s.get("round"), "hz": s.get("horizon_known"),
            })
        elif fam == "bargaining":
            money = s.get("money_to_divide") or 1
            g = lo.get(f"{me}_gain")
            if g is None:
                continue
            rows["barg"].append({
                "dec": a["decision"], "share_offered": g / money,
                "round": s.get("round"), "max_rounds": s.get("max_rounds"),
            })

def pct(xs): return f"{100*sum(xs)/max(len(xs),1):.0f}%"

print("=== NEGOTIATION decisions (n=%d) ===" % len(rows["neg"]))
neg = rows["neg"]
acc = [r for r in neg if r["dec"] == "AcceptOffer"]
rej = [r for r in neg if r["dec"] == "RejectOffer"]
print(f"  accept {len(acc)} / reject {len(rej)}  (accept rate {pct([1]*len(acc)+[0]*len(rej))})")
prof_rej = [r for r in rej if r["profitable"]]
print(f"  REJECTED offers that were PROFITABLE for us: {len(prof_rej)}/{len(rej)} ({pct([r['profitable'] for r in rej])})")
if prof_rej:
    print(f"    ...their median surplus we walked from: {st.median([r['surplus_frac'] for r in prof_rej]):.3f} of scale")
if acc:
    print(f"  surplus_frac we ACCEPT (median): {st.median([r['surplus_frac'] for r in acc]):.3f}")
cg = [r["counter_gap"] for r in rej if r["counter_gap"] is not None]
if cg: print(f"  counter aggressiveness (|counter-their_offer|/scale, median): {st.median(cg):.3f}")

print("\n=== BARGAINING decisions (n=%d) ===" % len(rows["barg"]))
barg = rows["barg"]
bacc = [r for r in barg if r["dec"] == "accept"]
brej = [r for r in barg if r["dec"] == "reject"]
print(f"  accept {len(bacc)} / reject {len(brej)}  (accept rate {pct([1]*len(bacc)+[0]*len(brej))})")
if bacc: print(f"  share we ACCEPT (median): {st.median([r['share_offered'] for r in bacc]):.3f}")
if brej: print(f"  share we REJECT (median): {st.median([r['share_offered'] for r in brej]):.3f}")
# how often do we reject offers >= 0.4 (decent) -> maybe too greedy causing bargaining no-deals?
greedy_rej = [r for r in brej if r["share_offered"] >= 0.4]
print(f"  rejected offers >= 0.40 share: {len(greedy_rej)} ({pct([r['share_offered']>=0.4 for r in brej])} of rejects)")
