"""Train mode: play an episode, then A/B-confirm the optimizer's proposal before deploying.

Each EPISODE:
  1. Thompson bandit picks an archive cell to mutate (exploration).
  2. The LLM proposer (Claude Code) + Reflexion lessons proposes a ruleset patch.
  3. HELD-OUT A/B on fresh games: play the champion for EVAL_N games and the candidate for EVAL_N,
     then a bootstrap decides P(candidate > champion). Adopt ONLY if P >= 0.8 with enough games,
     otherwise keep the champion. This is the anti-Goodhart gate — a noisy win can't get deployed.
  4. The candidate is recorded in the MAP-Elites archive regardless (for diversity / future parents).

Contrast with play.py (inference: fixed policy). Total games per episode ~= 2 * EVAL_N (both arms).

    export GLEE_API_KEY=glee_...
    GLEE_TRAIN_STEPS=8 GLEE_EVAL_N=10 GLEE_FAMILIES=negotiation,bargaining python train.py
"""

import logging
import os
import shutil

from glee_sdk import GleeClient

logging.basicConfig(level=logging.INFO, format="%(message)s")

import tw.params as params
from tw import search, optimizer, propose, evaluator
from run_optim import play_batch

STEPS = int(os.environ.get("GLEE_TRAIN_STEPS", "8"))
EVAL_N = int(os.environ.get("GLEE_EVAL_N", "10"))          # games PER ARM (champion & candidate)
FAMILIES = os.environ.get("GLEE_FAMILIES", "negotiation,bargaining").split(",")


def _union_shares(outcomes):
    """Flatten per-family our_share lists into one list (for an aggregate A/B)."""
    return [x for lst in evaluator._shares_by_family(outcomes).values() for x in lst]


def main():
    client = GleeClient(api_key=os.environ["GLEE_API_KEY"])
    shutil.copy("params.json", "params_prechampion.json")
    archive, bandit = search.Archive(), search.ThompsonBandit()

    def play(n):
        return play_batch(client, n, FAMILIES, max_time=500)

    allowed = params.knobs_for(FAMILIES)                   # causal scoping: only trained families' knobs
    print(f"training families {FAMILIES} — tuning {len(allowed)} knobs: {allowed}", flush=True)
    champion = params.load()                               # the confirmed, deployed ruleset
    if not archive.keys():
        archive.add(champion, search.outcome_metrics(play(EVAL_N)))
        print(f"seed champion: {archive.best()['metrics']}", flush=True)

    for ep in range(STEPS):
        print(f"\n=== episode {ep + 1}/{STEPS} ===", flush=True)
        cell = bandit.pick(archive.keys())
        print(f"bandit picked cell {cell}; asking the optimizer for a tweak "
              "(Claude may take ~30-90s)…", flush=True)
        parent = archive.cells[cell]["params"]
        patch, hyp, src = propose.propose(parent, search.load_lessons(), allowed)
        print(f"proposed via {src}: {patch}", flush=True)
        if hyp:
            print(f"  optimizer thinking: {hyp}", flush=True)
        cand = optimizer.apply_patch(parent, patch)
        if not optimizer.validate(cand):
            print("invalid patch, skipping episode", flush=True)
            continue

        # held-out A/B: champion arm, then candidate arm, on fresh games
        print(f"A/B — playing CHAMPION arm ({EVAL_N} games):", flush=True)
        params.save(champion); params.reload();  champ_out = play(EVAL_N)
        print(f"A/B — playing CANDIDATE arm ({EVAL_N} games):", flush=True)
        params.save(cand);     params.reload();  cand_out = play(EVAL_N)
        cand_metrics = search.outcome_metrics(cand_out)
        cs, hs = _union_shares(champ_out), _union_shares(cand_out)
        P = evaluator.bootstrap_win_prob(cs, hs)
        enough = len(cs) >= evaluator.MIN_N and len(hs) >= evaluator.MIN_N
        adopt = bool(enough and P is not None and P >= 0.8)

        archive.add(cand, cand_metrics)                   # QD keeps it regardless (diversity)
        bandit.update(cell, cand_metrics.get("fitness") or 0.0)
        if adopt:
            champion = cand
        params.save(champion); params.reload()            # deploy ONLY the confirmed champion

        tag = "ADOPT" if adopt else ("reject" if enough else "reject:low-n")
        search.add_lesson(f"[{src}][{tag} P={P}] {patch}" + (f" | {hyp}" if hyp else ""))
        search.log_hypothesis({"ep": ep, "src": src, "patch": patch, "thinking": hyp,
                               "P": P, "adopt": adopt,
                               "champ_share": round(sum(cs)/len(cs), 3) if cs else None,
                               "cand_share": round(sum(hs)/len(hs), 3) if hs else None,
                               "n_per_arm": [len(cs), len(hs)]})
        print(f"episode {ep}: proposer={src} | patch {patch} | "
              f"champ_share {round(sum(cs)/len(cs),3) if cs else None} vs "
              f"cand_share {round(sum(hs)/len(hs),3) if hs else None} | "
              f"P(cand>champ)={P} (n {len(cs)}/{len(hs)}) -> {tag}", flush=True)

    print(f"training done. deployed champion; archive best {archive.best()['metrics']}", flush=True)
    print("revert with: cp params_prechampion.json params.json", flush=True)


if __name__ == "__main__":
    main()
