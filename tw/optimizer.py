"""Optimizer: the LLM is the optimizer, not the agent. It proposes a patch to the ruleset.

Auto mode: if litellm + an LLM key is available, it calls the model and parses a JSON patch.
Manual mode: otherwise it writes the prompt to optim_prompt.txt and reads a patch you drop in
optim_patch.json — so the loop runs today with or without an LLM key.
"""

import json
import os
import re
from pathlib import Path

import tw.params as params

_PROMPT_FILE = Path("optim_prompt.txt")
_PATCH_FILE = Path("optim_patch.json")


def build_prompt(report_text, history):
    """Assemble the optimizer prompt: current ruleset + bounds + this batch + past patches."""
    knobs = {p: {"value": params.get(p), "bounds": list(b)} for p, b in params.BOUNDS.items()}
    hist = "\n".join(
        f"  iter {h['iter']}: patch={json.dumps(h['patch'])} -> total_rating {h['fitness']:+.2f}"
        for h in history[-8:]
    ) or "  (none yet)"
    return (
        "You are the OPTIMIZER of a deterministic agent that plays GLEE economic games "
        "(bargaining, negotiation, persuasion). You do NOT play; you tune its numeric ruleset "
        "to maximize our own self-gain (the server rating is percentile- and opponent-adjusted).\n\n"
        "TUNABLE KNOBS (dotted path -> current value and [lo, hi] bounds):\n"
        + json.dumps(knobs, indent=2) + "\n\n"
        "MEANING: grab.* = how much surplus we claim vs each opponent archetype; "
        "barg_delta_coeff = how patience maps to our bargaining reservation; "
        "neg_keep_* = the minimum surplus we insist on in negotiation; "
        "classify.* = thresholds that label opponents.\n\n"
        f"LATEST BATCH:\n{report_text}\n\n"
        f"PATCH HISTORY (what you tried -> resulting total rating):\n{hist}\n\n"
        "Propose a SMALL patch (change 1-3 knobs) as a JSON object of {dotted_key: new_value}. "
        "Only use keys from the knob list; stay within bounds. Prefer changes the batch evidence "
        "supports (e.g. raise grab where our_share is low but no_deal is also low). "
        "Reply with ONLY the JSON object, then one '# rationale: ...' line."
    )


def _extract_json(text):
    """Pull the first JSON object out of an LLM reply."""
    m = re.search(r"\{.*?\}", text, re.DOTALL)
    return json.loads(m.group(0)) if m else {}


def _call_llm(prompt):
    """Try litellm with the model in GLEE_OPTIM_MODEL; return {} if unavailable."""
    model = os.environ.get("GLEE_OPTIM_MODEL")
    if not model:
        return None
    try:
        import litellm
        resp = litellm.completion(model=model, messages=[{"role": "user", "content": prompt}],
                                  temperature=0.4)
        return _extract_json(resp["choices"][0]["message"]["content"])
    except Exception as e:
        print(f"[optimizer] LLM call failed ({e}); falling back to manual mode.")
        return None


def propose(report_text, history):
    """Return a validated, bounds-clamped patch dict. Auto (LLM) if possible, else manual file."""
    prompt = build_prompt(report_text, history)
    patch = _call_llm(prompt)
    if patch is None:                                   # manual mode
        _PROMPT_FILE.write_text(prompt)
        if _PATCH_FILE.exists():
            patch = json.loads(_PATCH_FILE.read_text())
            _PATCH_FILE.unlink()                        # consume it
        else:
            print(f"[optimizer] Wrote {_PROMPT_FILE}. Drop a patch in {_PATCH_FILE} to apply one; "
                  "skipping this iteration.")
            patch = {}
    return {k: params.clamp_path(k, v) for k, v in patch.items() if k in params.BOUNDS}


def apply_patch(base, patch):
    """Return a new params dict with the dotted-key patch applied."""
    new = json.loads(json.dumps(base))                  # deep copy
    for path, value in patch.items():
        node = new
        parts = path.split(".")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = value
    return new


def validate(candidate):
    """Reject a patch that would crash the policy — apply, then dry-run on synthetic turns."""
    params.save(candidate)
    params.reload()
    try:
        from tw.policy import negotiation, bargaining
        negotiation({"type": "offer"}, {"player_1_role": "seller", "player_1_value": 100,
                    "round": 1, "max_rounds": 10, "horizon_known": True}, "player_1", "over_conceder")
        bargaining({"type": "decision"}, {"money_to_divide": 100, "current_player": "player_2",
                   "delta_2": 0.9, "round": 1, "max_rounds": 12, "horizon_known": True,
                   "last_offer": {"player_2_gain": 40}}, "player_2", "unknown")
        return True
    except Exception as e:
        print(f"[optimizer] candidate rejected (policy error: {e})")
        return False
