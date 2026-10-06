"""Low-noise candidate test: play a candidate, score each game as its per-cell rank vs the PERSISTED
champion baseline (tw/baseline), aggregate mean-of-cell-means, stop anytime-valid. No noisy champion
arm — the baseline is the reference. Adopt only if it beats the champion AND the sim agrees (anti-Goodhart).

    GLEE_NEG_V2=1 python3 eval_candidate.py negotiation '{"neg_keep_coeff":0.5}' [max_games]

The candidate patch is applied over params_good; sim direction is checked as a second, independent signal.
Requires a warm baseline (>=~20 samples in several cells) — run farm.py for a while first.
"""
import json
import math
import os
import sys

from glee_sdk import GleeClient

import tw.params as params
from tw import baseline, sim, policy, search
from run_optim import play_batch

family = sys.argv[1] if len(sys.argv) > 1 else "negotiation"
patch = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
CAP = int(sys.argv[3]) if len(sys.argv) > 3 else 40
CHUNK = 6
client = GleeClient(api_key=os.environ["GLEE_API_KEY"])

champ = json.load(open("params_good.json"))
cand = json.loads(json.dumps(champ)); cand.update(patch)
base = baseline.load()

# --- signal 1: does the candidate beat champion in the calibrated sim? (independent, instant) ---
params.P.clear(); params.P.update(params._merge(params.DEFAULTS, cand))
cand_sim = sim.evaluate(policy, family if family in ("bargaining", "negotiation") else "bargaining", n=1500)["mean_fitness"]
params.P.clear(); params.P.update(params._merge(params.DEFAULTS, champ))
champ_sim = sim.evaluate(policy, family if family in ("bargaining", "negotiation") else "bargaining", n=1500)["mean_fitness"]
sim_dir = cand_sim - champ_sim
print(f"[sim] cand {cand_sim:.4f} vs champ {champ_sim:.4f} -> dir {sim_dir:+.4f}", flush=True)

# --- signal 2: online games scored by per-cell rank vs persisted baseline, anytime-valid ---
params.save(cand); params.reload()
ranks = []                                          # per-game rank-vs-baseline in [0,1]
while len(ranks) < CAP:
    outs = play_batch(client, CHUNK, [family], max_time=1800)
    for o in outs:
        b = base.get(baseline.coarse_cell(o.get("cov", "?")), [])
        if len(b) >= 20 and max(b) != min(b):        # skip degenerate/thin cells
            ranks.append(baseline.rank(b, search._one_fitness(o)))
    if len(ranks) >= 8:
        m = sum(ranks) / len(ranks)
        sd = (sum((x - m) ** 2 for x in ranks) / len(ranks)) ** 0.5
        half = 1.96 * sd / math.sqrt(len(ranks))     # ~anytime-ish CI on bounded [0,1] rank
        print(f"  online rank mean={m:.3f} ±{half:.3f} (n={len(ranks)})", flush=True)
        if m - half > 0.5:
            verdict = "ONLINE WIN"; break
        if m + half < 0.5:
            verdict = "ONLINE LOSS"; break
else:
    verdict = "inconclusive"
    m = sum(ranks) / len(ranks) if ranks else None

params.save(champ); params.reload()              # never leave the candidate live
online_win = verdict == "ONLINE WIN"
print(f"\n=== VERDICT === online: {verdict} (rank {round(m,3) if ranks else None}); sim dir {sim_dir:+.4f}")
if online_win and sim_dir >= 0:
    print("ADOPT: online beats champion AND sim agrees. (apply patch to params_good manually to ship)")
else:
    print("KEEP CHAMPION: needs online win + sim agreement to adopt (anti-Goodhart).")
