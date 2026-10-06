"""Field-referenced zero-inclusive percentile (Fable #3 fix). paired_pct.py ranks vs CHAMPION's payoff
distribution, whose deal-shape sits just above our capitulation prices -> biased against gclose. The
LEADERBOARD ranks vs the FIELD. Proxy for the field's payoff distribution as role R = the opponents'
realized payoffs (opp_payoff) from OUR games where the opponent played R = our MIRROR-role games, same
cell-minus-role. Includes their 0s (no-deals). Rank our arm's payoff (0s included) within that -> a
leaderboard-aligned gate. Usage: python experiments/field_pct.py <arm.jsonl> [field_source.jsonl]
"""
import json, sys, collections, statistics as st, bisect

def sig(cell):                          # cell with the role token stripped -> matches a game config across roles
    parts = [p for p in str(cell).split("|") if p not in ("seller", "buyer")]
    return "|".join(parts)

def role_of(cell):
    c = cell or ""
    if "buyer" in c: return "buyer"
    if "seller" in c: return "seller"
    return "seller" if c.startswith("pers|p") else "buyer"

OPP = {"seller": "buyer", "buyer": "seller"}

# FIELD reference: field-<role> payoffs = opp_payoff from our OPPOSITE-role games, keyed by cell-minus-role
field = collections.defaultdict(list)
for l in open(sys.argv[2] if len(sys.argv) > 2 else "logs/champion_baseline.jsonl"):
    if not l.strip(): continue
    r = json.loads(l)
    if r.get("family") != "negotiation": continue
    role = role_of(r.get("cell"))                    # the role WE played
    opp = r.get("opp_payoff")                         # the field opponent's payoff (they played OPP[role])
    if opp is None: opp = 0.0
    field[(OPP[role], sig(r.get("cell")))].append(opp)
for k in field: field[k].sort()

def pct(v, dist):
    lo = bisect.bisect_left(dist, v); hi = bisect.bisect_right(dist, v)
    return (lo + hi) / (2.0 * len(dist))

arm = collections.defaultdict(list); nd = collections.Counter(); nn = collections.Counter()
for l in open(sys.argv[1] if len(sys.argv) > 1 else "logs/theta_baseline.jsonl"):
    if not l.strip(): continue
    r = json.loads(l)
    if r.get("family") != "negotiation": continue
    role = role_of(r.get("cell")); key = (role, sig(r.get("cell")))
    if key not in field: continue
    p = r.get("payoff") or 0.0
    arm[role].append(pct(p, field[key])); nn[role] += 1
    if r.get("outcome") != "agreement": nd[role] += 1

print("FIELD-referenced percentile (0.50 = matches field; >0.50 = we beat the field agents; 0s included):")
for role in ("buyer", "seller"):
    if arm[role]:
        m = st.mean(arm[role])
        print(f"  {role}: field-pct {m:.3f} ({'BEATS' if m>0.5 else 'below'} field by {(m-0.5)*100:+.1f}) | our no-deal {nd[role]/nn[role]:.0%} | n={nn[role]}")
    else:
        print(f"  {role}: no comparable games")
