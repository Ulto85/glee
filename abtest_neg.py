"""A/B: negotiation with the V2 anti-over-hold policy OFF vs ON (only GLEE_NEG_V2 differs).

    python3 abtest_neg.py [games_per_arm]
"""
import os, statistics, sys
from glee_sdk import GleeClient
from run_optim import play_batch
from tw import search, window as W

N = int(sys.argv[1]) if len(sys.argv) > 1 else 14
client = GleeClient(api_key=os.environ["GLEE_API_KEY"])


def arm(on):
    os.environ["GLEE_NEG_V2"] = "1" if on else "0"
    os.environ.pop("GLEE_BELIEF", None)                       # isolate: belief off in both arms
    print(f"\n--- arm: V2 {'ON' if on else 'OFF (old greedy)'} (target {N} negotiation games) ---", flush=True)
    return search.game_records(play_batch(client, N, ["negotiation"], max_time=2400))


off = arm(False)
on = arm(True)


def report(name, recs):
    fit = [f for f, _ in recs]
    nod = sum(1 for f in fit if f == 0)
    deals = [f for f in fit if f > 0]
    print(f"{name}: games={len(fit)}  no_deal={nod/max(len(fit),1):.2f}  "
          f"mean_fitness={statistics.mean(fit) if fit else 0:.3f}  "
          f"share_on_deals={statistics.mean(deals) if deals else 0:.3f}", flush=True)


print("\n===== NEGOTIATION V2 A/B RESULT =====")
report("V2 OFF", off)
report("V2 ON ", on)
champ_adj, cand_adj = W.cuped_adjust(off, on)
p = W._boot_prob(champ_adj, cand_adj) if champ_adj and cand_adj else None
print(f"P(V2 ON > OFF), CUPED-adjusted: {p}")
print("verdict:", "ADOPT V2 (make default)" if (p and p >= 0.8) else "inconclusive", flush=True)
