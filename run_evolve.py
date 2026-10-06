"""One guarded code-evolution step: the optimizer rewrites a policy function, and it only ships if
it survives offline validation AND wins an online A/B against the current champion code.

    python3 run_evolve.py <function> [games_per_arm]      # live: propose + validate + A/B + promote/revert
    python3 run_evolve.py <function> --dry               # offline only: propose + validate, no games

Pipeline (mirrors FunSearch/AlphaEvolve keep-if-verified):
  snapshot champion -> diagnose -> LLM proposes code -> AST+import+SMOKE battery
    -> [pass] A/B (champion code vs candidate code, CUPED+bootstrap) -> promote if P>=THRESH else revert
    -> [fail] discard before any rated game; champion untouched.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from tw import diagnose, evolve, search, window as W

THRESH = float(os.environ.get("GLEE_EVOLVE_THRESH", "0.8"))
N = 14
HLOG = Path("logs/optimizer_human.log")


def hlog(msg):
    line = f"{msg}"
    print(line, flush=True)
    with HLOG.open("a") as fh:
        fh.write(line + "\n")


def eval_arm(n, family, tag):
    out = f"backups/arm_{tag}.json"
    subprocess.run([sys.executable, "eval_play.py", str(n), family, out],
                   check=True, env=dict(os.environ))
    return [tuple(r) for r in json.load(open(out))]


def main():
    fn = sys.argv[1]
    dry = "--dry" in sys.argv
    if fn not in evolve.EDITABLE:
        print(f"function must be one of {evolve.EDITABLE}"); return
    family = {"negotiation": "negotiation", "bargaining": "bargaining", "persuasion": "persuasion"}[fn]

    lessons = search.load_lessons(family)
    diag = diagnose.report(family=family)
    hlog(f"\n===== EVOLVE {fn} ({time.strftime('%H:%M')}) =====")
    hlog(f"diagnostics:\n{diag}")

    champ_backup = evolve.snapshot()
    hlog(f"champion snapshot -> {champ_backup}")

    prop = evolve.propose_code(fn, diag, lessons)
    if not prop:
        hlog("proposer returned nothing (no claude CLI / bad JSON) — aborting, champion untouched.")
        return
    hlog(f"proposed hypothesis: {prop.get('hypothesis','')}")
    hlog(f"  prediction: {prop.get('prediction','')}")
    hlog(f"  new_knobs: {prop.get('new_knobs', [])}")

    ok, full_text, errs = evolve.validate(fn, prop["code"])
    if not ok:
        hlog(f"❌ candidate FAILED offline validation: {errs[:3]} — discarded, champion untouched.")
        search.add_lesson(f"code edit to {fn} failed validation: {errs[:1]}", family)
        return
    hlog("✅ candidate passed AST + import + smoke battery.")

    passed, cf, chf = evolve.sim_gate(full_text, family)
    hlog(f"sim-gate (instant self-play): candidate {cf} vs champion {chf} -> {'PASS' if passed else 'FAIL'}")
    if not passed:
        hlog("❌ candidate regresses in local self-play — discarded before spending any online games.")
        search.add_lesson(f"code edit to {fn} sim-rejected (cand {cf} < champ {chf})", family)
        return

    if dry:
        hlog("(--dry) stopping before online A/B. Candidate is valid and would be A/B-tested live.")
        Path(f"backups/validated_{fn}.py").write_text(full_text)
        hlog(f"validated candidate written to backups/validated_{fn}.py for inspection.")
        return

    # register any new knobs BEFORE the candidate plays (so params.P.get sees them)
    import tw.params as params
    for k in prop.get("new_knobs", []):
        try:
            params.register_knob(k["name"], k["default"], k["bounds"], k["family"])
            hlog(f"  registered new knob {k['name']} default={k['default']} bounds={k['bounds']}")
        except Exception as e:
            hlog(f"  [knob register failed] {e}")

    hlog(f"online A/B: champion vs candidate, {N} games/arm…")
    champ_recs = eval_arm(N, family, "champ")            # champion code still on disk
    evolve.POLICY.write_text(full_text)                  # swap candidate in
    cand_recs = eval_arm(N, family, "cand")

    champ_adj, cand_adj = W.cuped_adjust(champ_recs, cand_recs)
    p = W._boot_prob(champ_adj, cand_adj) if champ_adj and cand_adj else None
    cf = sum(f for f, _ in champ_recs) / max(len(champ_recs), 1)
    kf = sum(f for f, _ in cand_recs) / max(len(cand_recs), 1)
    hlog(f"champion fitness={cf:.3f}  candidate fitness={kf:.3f}  P(cand>champ)={p}")

    if p is not None and p >= THRESH:
        hlog(f"🚀 ADOPT: candidate code promoted (P={p:.2f} ≥ {THRESH}). Champion backup at {champ_backup}.")
        search.add_lesson(f"code edit to {fn} ADOPTED (P={p:.2f}): {prop.get('hypothesis','')[:120]}", family)
    else:
        evolve.restore(champ_backup)
        hlog(f"↩ REVERT: candidate did not beat champion (P={p}). Restored champion code.")
        search.add_lesson(f"code edit to {fn} reverted (P={p}): {prop.get('hypothesis','')[:120]}", family)


if __name__ == "__main__":
    main()
