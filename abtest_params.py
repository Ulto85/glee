"""A/B a specific param candidate vs the current champion, per-family, and adopt only what wins ONLINE.

    python3 abtest_params.py [games_per_arm_per_family]

Candidate = aggression/toughness push (theory + sim backed). Plays champion then candidate over BOTH
families, CUPED-adjusts per family, and adopts a family's knobs only if P(cand>champ) >= ADOPT_P.
Ships nothing on the proxy alone — this is the online gate.
"""
import atexit
import json
import os
import shutil
import signal
import sys

from glee_sdk import GleeClient


def _restore_live_to_champion():
    """Guarantee the LIVE params file is always the champion (params_good) on ANY exit — kill, crash,
    or normal. Adoption rewrites params_good first, so this leaves live=current champion either way.
    Fixes the class of bug where a killed A/B left an unconfirmed candidate live on rated games."""
    try:
        shutil.copy("params_good.json", "params.json")
    except Exception:
        pass


atexit.register(_restore_live_to_champion)
signal.signal(signal.SIGTERM, lambda *a: sys.exit(1))   # make SIGTERM run atexit

import tw.params as params
from tw import search, window as W
from run_optim import play_batch

N = int(sys.argv[1]) if len(sys.argv) > 1 else 12
ADOPT_P = float(os.environ.get("ADOPT_P", "0.75"))
FAMILIES = ["bargaining", "negotiation"]

# disjoint per-family knob patches
CAND = {
    "negotiation": {"neg_keep_coeff": 0.55, "neg_keep_floor": 0.03},
    "bargaining":  {"barg_delta_coeff": 0.72, "barg_share_cap": 0.80, "barg_offer_bump": 0.12,
                    "barg_arch_bump": {"over_conceder": 0.18, "hard_anchorer": 0.03,
                                       "tit_for_tat": 0.0, "unknown": 0.0}},
}

client = GleeClient(api_key=os.environ["GLEE_API_KEY"])
champion = json.load(open("params_good.json"))


def recs_by_family(outcomes, fam):
    return search.game_records([o for o in outcomes if o["family"] == fam])


def play(p, label):
    params.save(p); params.reload()
    print(f"\n--- arm: {label} ({N} games/family) ---", flush=True)
    return play_batch(client, N * len(FAMILIES), FAMILIES, max_time=3000)


champ_out = play(champion, "CHAMPION")
cand_params = json.loads(json.dumps(champion))
for fam in FAMILIES:
    for k, v in CAND[fam].items():
        cand_params[k] = v
cand_out = play(cand_params, "CANDIDATE (aggression+toughness)")

print("\n===== PARAM A/B RESULT (per family) =====")
new_champ = json.loads(json.dumps(champion))
adopted = []
for fam in FAMILIES:
    cr, kr = recs_by_family(champ_out, fam), recs_by_family(cand_out, fam)
    ca, ka = W.cuped_adjust(cr, kr)
    cm = sum(f for f, _ in cr) / len(cr) if cr else 0.0
    km = sum(f for f, _ in kr) / len(kr) if kr else 0.0
    p = W._boot_prob(ca, ka) if ca and ka else None
    win = p is not None and p >= ADOPT_P
    print(f"{fam}: champ {cm:.3f} (n={len(cr)}) vs cand {km:.3f} (n={len(kr)}) | P(cand>champ)={p} -> "
          f"{'ADOPT' if win else 'keep champion'}")
    if win:
        for k, v in CAND[fam].items():
            new_champ[k] = v
        adopted.append(fam)

if adopted:
    params.save(new_champ)
    json.dump(new_champ, open("params_good.json", "w"), indent=2)
    print(f"\nADOPTED {adopted} -> new champion written to params.json + params_good.json")
else:
    params.save(champion); params.reload()
    print("\nno family beat champion online — champion unchanged.")
