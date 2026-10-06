"""Run an A/B evaluation of a candidate ruleset vs the current champion on fresh games.

    export GLEE_API_KEY=glee_...
    python run_eval.py                 # champion (params.json) vs candidate (params_cand.json)

Put the ruleset you want to test in params_cand.json (or it clones the champion = null test).
Prints per-family champion vs challenger share and P(challenger > champion); restores champion.
"""

import json
import os
from pathlib import Path

from glee_sdk import GleeClient

import tw.params as params
from tw import evaluator
from tw.strategy import strategy
from run_optim import play_batch

N_PER_ARM = int(os.environ.get("GLEE_EVAL_N", "10"))
FAMILIES = tuple(os.environ.get("GLEE_FAMILIES", "negotiation,bargaining").split(","))


def main():
    client = GleeClient(api_key=os.environ["GLEE_API_KEY"])
    champ = params.load()
    cand_path = Path("params_cand.json")
    chall = json.loads(cand_path.read_text()) if cand_path.exists() else json.loads(json.dumps(champ))

    def play(n, families):
        return play_batch(client, n, list(families), max_time=300)

    result = evaluator.evaluate(play, champ, chall, n_per_arm=N_PER_ARM, families=FAMILIES)
    print(json.dumps(result, indent=2))
    print("champion restored to params.json:", params.load() == champ)


if __name__ == "__main__":
    main()
