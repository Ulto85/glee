"""Field-anchored negotiation evaluator (Fable audit 2026-08-12).
The baseline/replay judge is self-referential (ranks vs our own champion) — blind to the field.
This scores our SHARE of surplus against the FIELD's realized share, per role, from opp_payoff.

Field benchmark (what field agents extract from us, on deals): computed from a reference baseline's
opp_payoff — field-seller-share = opp share when WE are buyer; field-buyer-share = opp share when WE seller.
Then report an arm's OUR-share by role + no-deal, vs the field benchmark. Higher our-share (closer to
field's 60-75%) with no-deal not worse = genuinely beating the field, which the blind judge can't see.
"""
import json, sys, collections, statistics as st

def load(path):
    out = []
    for l in open(path):
        if not l.strip():
            continue
        r = json.loads(l)
        if (r.get("family") or r.get("game_family")) != "negotiation":
            continue
        out.append(r)
    return out

def role_of(r):
    c = r.get("cell") or ""
    return "seller" if "seller" in c else ("buyer" if "buyer" in c else "?")

def shares(rows):
    d = collections.defaultdict(lambda: {"our": [], "opp": [], "nd": 0, "n": 0})
    for r in rows:
        role = role_of(r); e = d[role]; e["n"] += 1
        if r.get("outcome") and r["outcome"] != "agreement":
            e["nd"] += 1
        op, opp = r.get("payoff"), r.get("opp_payoff")
        if op is not None and opp is not None and (op > 0 or opp > 0) and (op + opp) > 0:
            e["our"].append(op / (op + opp)); e["opp"].append(opp / (op + opp))
    return d

# FIELD benchmark from a reference file (champion baseline = big sample of field opponents)
ref = load(sys.argv[2] if len(sys.argv) > 2 else "logs/champion_baseline.jsonl")
rd = shares(ref)
# field-seller share = opp share in OUR buyer games; field-buyer share = opp share in OUR seller games
field_seller = st.mean(rd["buyer"]["opp"]) if rd["buyer"]["opp"] else None
field_buyer = st.mean(rd["seller"]["opp"]) if rd["seller"]["opp"] else None
print(f"FIELD benchmark (share on deals): seller={field_seller:.1%}  buyer={field_buyer:.1%}")

# ARM under test
arm = load(sys.argv[1] if len(sys.argv) > 1 else "logs/theta_baseline.jsonl")
ad = shares(arm)
for role, fld in (("seller", field_seller), ("buyer", field_buyer)):
    e = ad[role]
    if e["our"]:
        our = st.mean(e["our"]); nd = e["nd"] / e["n"]
        gap = our - fld
        print(f"  ARM {role}: our-share {our:.1%} (field {fld:.1%}, gap {gap:+.1%}) | no-deal {nd:.0%} | n={e['n']} deals={len(e['our'])}")
    else:
        print(f"  ARM {role}: no deals yet (n={e['n']})")
