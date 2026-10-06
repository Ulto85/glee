"""Evolve: let the optimizer edit POLICY CODE, not just knobs — safely.

The knob search plateaued because the win required a *structural* change (accept the bird-in-hand
under an unknown horizon), and there was no knob for that. This module lets the LLM rewrite one
whitelisted policy function, then gates it exactly the way FunSearch/AlphaEvolve gate LLM code:

    propose -> AST parse -> import -> SMOKE BATTERY (must return legal actions, no exceptions)
            -> [only then] online A/B vs champion -> promote or auto-revert.

A syntactically or behaviorally broken edit can never reach the live agent — it fails the smoke
battery and is discarded before a single rated game is played. A snapshot enables instant revert.
"""
import ast
import importlib.util
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import tw.params as params
from tw import sim

POLICY = Path("tw/policy.py")
BACKUP_DIR = Path("backups")
EDITABLE = {"negotiation", "bargaining", "persuasion"}   # whitelisted top-level functions


# ---------- snapshot / restore ----------
def snapshot(tag=None):
    BACKUP_DIR.mkdir(exist_ok=True)
    tag = tag or str(int(time.time()))
    dst = BACKUP_DIR / f"policy_{tag}.py"
    shutil.copy(POLICY, dst)
    if Path("params.json").exists():
        shutil.copy("params.json", BACKUP_DIR / f"params_{tag}.json")
    return dst


def restore(policy_backup):
    shutil.copy(policy_backup, POLICY)
    params.reload()


# ---------- function extract / replace ----------
def func_source(name, text=None):
    text = text if text is not None else POLICY.read_text()
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            lines = text.splitlines()
            return "\n".join(lines[node.lineno - 1: node.end_lineno])
    return None


def replace_func(name, new_src, text=None):
    """Return the full module text with top-level function `name` replaced by new_src."""
    text = text if text is not None else POLICY.read_text()
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            lines = text.splitlines()
            before, after = lines[: node.lineno - 1], lines[node.end_lineno:]
            return "\n".join(before + new_src.rstrip().splitlines() + [""] + after)
    raise ValueError(f"function {name} not found")


# ---------- smoke battery ----------
def _states():
    """Synthetic (actions, state, me, arch) covering both roles, horizons, and action types."""
    B, N = [], []
    for me, alice in (("player_1", True), ("player_2", False)):
        B.append(({"type": "offer"}, {"money_to_divide": 100.0, "round": 1, "max_rounds": 12,
                                      "horizon_known": True, f"delta_{me.split('_')[-1]}": 0.9,
                                      "last_offer": {f"{me}_gain": 50.0}}, me))
        B.append(({"type": "decision"}, {"money_to_divide": 100.0, "round": 12, "max_rounds": 12,
                                         "horizon_known": True, f"delta_{me.split('_')[-1]}": 0.9,
                                         "last_offer": {f"{me}_gain": 30.0}}, me))
    for me in ("player_1", "player_2"):
        for role in ("buyer", "seller"):
            for hz in (True, False):
                for typ in ("offer", "decision"):
                    N.append(({"type": typ}, {f"{me}_role": role, f"{me}_value": 60.0, "round": 3,
                                              "max_rounds": 10, "horizon_known": hz,
                                              "product_price_order": 100.0,
                                              "last_offer": {"price": 55.0}}, me))
    return B, N


def _legal(fam, act):
    if not isinstance(act, dict):
        return False
    if fam == "bargaining":
        return ("alice_gain" in act and "bob_gain" in act) or act.get("decision") in ("accept", "reject")
    if fam == "negotiation":
        return "product_price" in act or act.get("decision") in ("AcceptOffer", "RejectOffer")
    return True


def smoke(module):
    """Run every whitelisted function over the battery; return (ok, list_of_errors)."""
    errs = []
    B, N = _states()
    for actions, state, me in B:
        try:
            a = module.bargaining(actions, state, me, "unknown")
            if not _legal("bargaining", a):
                errs.append(f"bargaining illegal action {a} for {actions['type']}/{me}")
        except Exception as e:
            errs.append(f"bargaining raised {e!r}")
    for actions, state, me in N:
        try:
            a = module.negotiation(actions, state, me, "unknown")
            if not _legal("negotiation", a):
                errs.append(f"negotiation illegal action {a} for {actions['type']}")
        except Exception as e:
            errs.append(f"negotiation raised {e!r}")
    return (not errs), errs


def _load(text):
    """Import module text in isolation (never touches the live module) for smoke-testing."""
    BACKUP_DIR.mkdir(exist_ok=True)
    p = BACKUP_DIR / "candidate_policy.py"
    p.write_text(text)
    spec = importlib.util.spec_from_file_location("tw._candidate_policy", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def validate(name, new_func_src):
    """AST-parse + import + smoke a proposed function rewrite. Returns (ok, text_or_None, errors)."""
    try:
        full = replace_func(name, new_func_src)
    except Exception as e:
        return False, None, [f"replace failed: {e!r}"]
    try:
        ast.parse(full)
    except SyntaxError as e:
        return False, None, [f"syntax error: {e!r}"]
    try:
        mod = _load(full)
    except Exception as e:
        return False, None, [f"import error: {e!r}"]
    ok, errs = smoke(mod)
    return ok, (full if ok else None), errs


def sim_gate(full_text, family, n=600, margin=0.98):
    """INSTANT proxy filter (calibrated vs the V2 A/B): does the candidate beat the live champion in
    local self-play? Kills obvious regressions in milliseconds so no online games are wasted on them.
    Returns (pass, cand_fitness, champ_fitness). A proxy — passing still requires the online A/B."""
    import tw.policy as champ
    cand = _load(full_text)
    fam = family if family in ("bargaining", "negotiation") else "bargaining"
    c = sim.evaluate(cand, fam, n=n)["mean_fitness"]
    ch = sim.evaluate(champ, fam, n=n)["mean_fitness"]
    return (c >= ch * margin), round(c, 4), round(ch, 4)


# ---------- LLM code proposer ----------
_PROMPT = """You are evolving ONE function of a deterministic GLEE game agent (Python). Rewrite it to
fix the flagged behavioral pathology and raise fitness (fitness = scale-free captured surplus; a
no-deal scores 0, so avoiding no-deals matters as much as extracting share).

HARD CONSTRAINTS:
- Return the COMPLETE function with the EXACT same signature and name. Top-level (no indentation).
- Pure and deterministic given inputs. You MAY use: params.P.get(...), math, random, and the module's
  existing helpers ({helpers}). No file/network/other imports.
- It MUST return a legal action dict for every branch (bargaining: alice_gain+bob_gain on offer, or
  decision in accept/reject; negotiation: product_price on offer, or decision in AcceptOffer/RejectOffer).
- You MAY introduce a NEW tunable knob by reading params.P.get("some_name", DEFAULT) AND listing it in
  "new_knobs" so it gets registered and can be optimized later. Prefer knobs over hard-coded magic numbers.

FUNCTION TO EVOLVE ({name}):
```python
{src}
```

BEHAVIORAL DIAGNOSTICS (⚠ = pathology to fix):
{diag}

REFLEXION LESSONS (past attempts; don't repeat regressions):
{lessons}

Reply with ONLY JSON:
{{"code": "<full function source>", "new_knobs": [{{"name": "...", "default": 0.0, "bounds": [lo, hi],
  "family": "negotiation|bargaining|persuasion"}}], "hypothesis": "...", "prediction": "..."}}"""


def propose_code(name, diag, lessons, helpers="_progress, grab, _quality"):
    src = func_source(name)
    if src is None:
        return None
    prompt = _PROMPT.format(name=name, src=src, diag=diag or "(none)",
                            lessons=json.dumps(lessons[-8:], indent=1), helpers=helpers)
    if not shutil.which("claude"):
        return None
    try:
        r = subprocess.run(["claude", "-p", prompt], capture_output=True, text=True, timeout=240)
        out = r.stdout
    except Exception:
        return None
    m = re.search(r"\{.*\}", out, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return None
    if "code" not in obj:
        return None
    return obj      # {code, new_knobs, hypothesis, prediction}
