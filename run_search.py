"""Quality-diverse, bandit-allocated, Reflexion-guided search over rulesets.

    export GLEE_API_KEY=glee_...
    export GLEE_OPTIM_MODEL=gpt-4o-mini   # optional: LLM proposer w/ lessons; else built-in mutation
    python run_search.py

Loop: bandit picks a region of the archive -> proposer mutates it (LLM or random) ->
evaluate the candidate on FRESH games -> insert into the MAP-Elites archive if it wins its
cell -> record a lesson -> deploy the archive's best to params.json. Env: GLEE_SEARCH_ITERS (5),
GLEE_EVAL_N (6), GLEE_FAMILIES (negotiation,bargaining).
"""

import json
import os

from glee_sdk import GleeClient

import tw.params as params
from tw import search, optimizer
from tw.strategy import strategy
from run_optim import play_batch

ITERS = int(os.environ.get("GLEE_SEARCH_ITERS", "5"))
EVAL_N = int(os.environ.get("GLEE_EVAL_N", "6"))
FAMILIES = os.environ.get("GLEE_FAMILIES", "negotiation,bargaining").split(",")


def llm_propose(parent, lessons):
    """Optional LLM proposer with Reflexion lessons; returns (patch, hypothesis, prediction)."""
    model = os.environ.get("GLEE_OPTIM_MODEL")
    if not model:
        return None
    try:
        import litellm
        knobs = {p: {"value": search.get_from(parent, p), "bounds": list(b)}
                 for p, b in params.BOUNDS.items()}
        prompt = (
            "You tune a deterministic GLEE agent's ruleset to raise our self-gain (fitness = "
            "scale-free captured share, no-deals count as 0). Change 1-3 knobs, stay in bounds.\n"
            f"KNOBS:\n{json.dumps(knobs, indent=2)}\n"
            f"LESSONS FROM PAST EXPERIMENTS:\n{json.dumps(lessons[-12:], indent=2)}\n"
            'Reply with ONLY JSON: {"patch": {dotted_key: value}, '
            '"hypothesis": "...", "prediction": "what metric should move and how"}'
        )
        txt = litellm.completion(model=model, messages=[{"role": "user", "content": prompt}],
                                 temperature=0.5)["choices"][0]["message"]["content"]
        obj = json.loads(txt[txt.index("{"):txt.rindex("}") + 1])
        patch = {k: params.clamp_path(k, v) for k, v in obj.get("patch", {}).items()
                 if k in params.BOUNDS}
        return patch, obj.get("hypothesis", ""), obj.get("prediction", "")
    except Exception as e:
        print(f"[search] LLM proposer failed ({e}); using mutation.")
        return None


def main():
    client = GleeClient(api_key=os.environ["GLEE_API_KEY"])
    archive, bandit = search.Archive(), search.ThompsonBandit()

    def play(n):
        return play_batch(client, n, FAMILIES, max_time=300)

    if not archive.keys():                               # seed with the current champion
        champ = params.load()
        m = search.outcome_metrics(play(EVAL_N))
        archive.add(champ, m)
        print("seed champion:", m)

    for it in range(ITERS):
        cell = bandit.pick(archive.keys())
        parent = archive.cells[cell]["params"]
        parent_fit = archive.cells[cell]["metrics"].get("fitness") or 0.0

        prop = llm_propose(parent, search.load_lessons())
        if prop is None:
            patch, hypothesis = search.mutate(parent)
            prediction = ""
        else:
            patch, hypothesis, prediction = prop
        if not patch:
            print(f"[iter {it}] empty patch, skipping"); continue

        candidate = optimizer.apply_patch(parent, patch)
        if not optimizer.validate(candidate):            # applies+reloads candidate as a side effect
            continue

        metrics = search.outcome_metrics(play(EVAL_N))   # candidate is live after validate()
        inserted, ccell = archive.add(candidate, metrics)
        bandit.update(cell, metrics.get("fitness") or 0.0)

        verdict = "improved" if (metrics.get("fitness") or 0) > parent_fit else "regressed"
        lesson = f"{json.dumps(patch)} -> fitness {metrics.get('fitness')} vs parent {round(parent_fit,3)} [{verdict}]"
        search.add_lesson(lesson)
        search.log_hypothesis({"iter": it, "patch": patch, "hypothesis": hypothesis,
                               "prediction": prediction, "metrics": metrics, "verdict": verdict,
                               "inserted": inserted, "cell": ccell})
        print(f"[iter {it}] parent={cell} patch={patch} -> {metrics} ({verdict}, cell {ccell}, new={inserted})")

        best = archive.best()                            # always deploy the best-known ruleset
        params.save(best["params"]); params.reload()

    best = archive.best()
    print("\nARCHIVE cells:", {k: v["metrics"].get("fitness") for k, v in archive.cells.items()})
    print("BEST fitness:", best["metrics"], "\nDeployed to params.json.")


if __name__ == "__main__":
    main()
