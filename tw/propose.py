"""Proposer: the LLM optimizer is a default part of the loop, not an option.

Each call proposes a small ruleset patch from the current knobs + accumulated Reflexion lessons.
Backend priority: Claude Code CLI (`claude -p`) → litellm (if GLEE_OPTIM_MODEL) → random mutation.
Whichever runs, it returns (patch, hypothesis, source) so the loop can log what proposed it.
"""

import json
import os
import re
import shutil
import subprocess

import tw.params as params
from tw import search


def _prompt(parent, lessons, allowed=None, diag=""):
    keys = allowed if allowed else list(params.BOUNDS)
    knobs = {p: {"value": search.get_from(parent, p), "bounds": list(params.BOUNDS[p])} for p in keys}
    return (
        "You are the optimizer of a deterministic GLEE agent. Raise its self-gain by tuning the "
        "numeric ruleset (fitness = scale-free captured share; no-deals count as 0). Change 1-3 "
        "knobs only, stay within bounds.\n"
        f"KNOBS (dotted key -> value, [lo,hi]):\n{json.dumps(knobs, indent=2)}\n"
        f"BEHAVIORAL DIAGNOSTICS (what the agent actually did lately — ⚠ marks a pathology to fix):\n"
        f"{diag or '(none)'}\n"
        f"REFLEXION LESSONS (past patch -> result; don't repeat what regressed):\n"
        f"{json.dumps(lessons[-12:], indent=2)}\n"
        "Target the flagged pathologies with your knob change where possible.\n"
        'Reply with ONLY JSON: {"patch": {dotted_key: value}, "hypothesis": "...", "prediction": "..."}'
    )


def _parse(text):
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    obj = json.loads(m.group(0))
    patch = {k: params.clamp_path(k, v) for k, v in obj.get("patch", {}).items() if k in params.BOUNDS}
    return patch, obj.get("hypothesis", ""), obj.get("prediction", "")


def _claude(prompt):
    if not shutil.which("claude"):
        return None
    try:
        r = subprocess.run(["claude", "-p", prompt], capture_output=True, text=True, timeout=150)
        return r.stdout
    except Exception:
        return None


def _litellm(prompt):
    model = os.environ.get("GLEE_OPTIM_MODEL")
    if not model:
        return None
    try:
        import litellm
        return litellm.completion(model=model, messages=[{"role": "user", "content": prompt}],
                                  temperature=0.5)["choices"][0]["message"]["content"]
    except Exception:
        return None


def propose(parent, lessons, allowed=None, diag=""):
    """Return (patch, hypothesis, source), restricted to `allowed` knobs (causal scoping).
    `diag` is a behavioral report (see tw.diagnose) that steers the LLM toward real pathologies."""
    prompt = _prompt(parent, lessons, allowed, diag)
    for src, fn in (("claude", _claude), ("litellm", _litellm)):
        out = fn(prompt)
        if out:
            try:
                parsed = _parse(out)
                if parsed and parsed[0]:
                    patch, hyp, pred = parsed
                    if allowed is not None:                     # keep only in-scope knobs
                        patch = {k: v for k, v in patch.items() if k in allowed}
                    if patch:
                        thinking = hyp + (f" — predict: {pred}" if pred else "")
                        return patch, thinking, src
            except Exception:
                pass
    patch, hyp = search.mutate(parent, allowed=allowed)
    return patch, hyp, "mutation"
