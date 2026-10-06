"""Zero-inclusive paired percentile: does the ARM beat champion on the REAL objective?
field_eval scores SHARE on deals only — blind to the no-deal (=0) cost. GLEE scores percentile-
within-cell, convex near 0, so no-deals hurt more than a share-only view shows. Here: score each ARM
neg game as the percentile of its payoff within CHAMPION's payoff distribution for the same coarse cell
(no-deal 0s INCLUDED on both sides). Mean percentile > 0.5 => arm beats champion on that cell/role.
This is the paired gate the tick's step (4) asks for — resolves share-vs-no-deal correctly.
"""
import json, sys, os, collections, statistics as st
sys.path.insert(0, ".")
from tw.baseline import coarse_cell

FAMILY = os.environ.get("PP_FAMILY", "negotiation")

def load(path):
    for l in open(path):
        if not l.strip(): continue
        r = json.loads(l)
        if r.get("family") != FAMILY: continue
        yield r

def role_of(r):
    c = r.get("cell") or ""
    if "buyer" in c: return "buyer"
    if "seller" in c: return "seller"
    # persuasion seller cells are 'pers|p<prob>|...' (no explicit 'seller' token)
    return "seller" if c.startswith("pers|p") else "buyer"

# champion reference: coarse-cell -> sorted payoffs (0s included)
ref = collections.defaultdict(list)
for r in load(sys.argv[2] if len(sys.argv) > 2 else "logs/champion_baseline.jsonl"):
    p = r.get("payoff")
    ref[(role_of(r), coarse_cell(r.get("cell")))].append(p if p is not None else 0.0)
for k in ref: ref[k].sort()

def pct(val, dist):
    # fraction of champion payoffs <= val (midrank), in [0,1]
    import bisect
    lo = bisect.bisect_left(dist, val); hi = bisect.bisect_right(dist, val)
    return (lo + hi) / (2.0 * len(dist))

arm = collections.defaultdict(list); nd = collections.Counter(); nn = collections.Counter()
for r in load(sys.argv[1] if len(sys.argv) > 1 else "logs/theta_baseline.jsonl"):
    role = role_of(r); key = (role, coarse_cell(r.get("cell")))
    if key not in ref: continue          # no champion reference for this cell -> skip
    p = r.get("payoff") or 0.0
    arm[role].append(pct(p, ref[key]))
    nn[role] += 1
    if r.get("outcome") != "agreement": nd[role] += 1

def boot_ci(xs, iters=2000, lo=5, hi=95):
    """Deterministic bootstrap 90% CI of the mean (LCG, no Math.random dependence)."""
    n = len(xs)
    if n < 2:
        return (float("nan"), float("nan"))
    seed = 12345 + n
    means = []
    for _ in range(iters):
        s = 0.0
        for _ in range(n):
            seed = (1103515245 * seed + 12345) & 0x7FFFFFFF
            s += xs[seed % n]
        means.append(s / n)
    means.sort()
    return (means[int(lo/100*iters)], means[int(hi/100*iters)])

print("PAIRED percentile vs champion (0.50 = tie; >0.50 = arm beats; 0s included; 90% bootstrap CI):")
for role in ("buyer", "seller"):
    if arm[role]:
        m = st.mean(arm[role]); clo, chi = boot_ci(arm[role])
        sig = "SIGNIF" if (clo > 0.5 or chi < 0.5) else "n.s."   # CI excludes 0.5 -> real
        print(f"  {role}: pct {m:.3f} [90%CI {clo:.3f},{chi:.3f}] {sig} "
              f"({'BEATS' if m>0.5 else 'loses'} by {(m-0.5)*100:+.1f}pts) | no-deal {nd[role]/nn[role]:.0%} | n={nn[role]}")
    else:
        print(f"  {role}: no comparable games")
