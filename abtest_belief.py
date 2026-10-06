"""A/B: negotiation with the belief layer OFF vs ON (same params, only GLEE_BELIEF differs).

    export GLEE_API_KEY=glee_...
    python3 abtest_belief.py [games_per_arm]
"""

import os
import statistics
import sys

from glee_sdk import GleeClient

from run_optim import play_batch
from tw import search, window as W

N = int(sys.argv[1]) if len(sys.argv) > 1 else 12
client = GleeClient(api_key=os.environ["GLEE_API_KEY"])


def arm(on):
    if on:
        os.environ["GLEE_BELIEF"] = "1"
    else:
        os.environ.pop("GLEE_BELIEF", None)
    print(f"\n--- arm: belief {'ON' if on else 'OFF'} (target {N} negotiation games) ---", flush=True)
    outs = play_batch(client, N, ["negotiation"], max_time=2400)
    return search.game_records(outs)


off = arm(False)
on = arm(True)


def report(name, recs):
    fit = [f for f, _ in recs]
    nod = sum(1 for f in fit if f == 0)
    deals = [f for f in fit if f > 0]
    print(f"{name}: games={len(fit)}  no_deal={nod/max(len(fit),1):.2f}  "
          f"mean_fitness(share-or-0)={statistics.mean(fit) if fit else 0:.3f}  "
          f"mean_share_on_deals={statistics.mean(deals) if deals else 0:.3f}", flush=True)


print("\n===== BELIEF A/B RESULT =====")
report("belief OFF", off)
report("belief ON ", on)
champ_adj, cand_adj = W.cuped_adjust(off, on)     # off = baseline, on = candidate
p = W._boot_prob(champ_adj, cand_adj) if champ_adj and cand_adj else None
print(f"P(belief ON > OFF), CUPED-adjusted: {p}")
print("verdict:", "ADOPT belief" if (p and p >= 0.8) else "inconclusive/keep OFF", flush=True)
