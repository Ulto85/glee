"""Sliding-window optimizer.

Instead of a paired A/B (which starves when matchmaking is thin), we keep a rolling buffer of the
CHAMPION's recent per-game fitness and only play the CANDIDATE fresh, then compare the candidate to
the (recency-weighted) window. Each step also refreshes the window with a few champion games so the
baseline tracks the current population. Adopt only if the candidate beats the window with confidence.

    export GLEE_API_KEY=glee_...
    GLEE_FAMILIES=negotiation GLEE_SW_HALFLIFE=20 python3 train_sw.py

Env: GLEE_SW_WINDOW (40), GLEE_SW_STEP (10 champion games/step), GLEE_SW_CAND (12 candidate games/step),
GLEE_SW_HALFLIFE (1e9 = uniform/standard; small = weight recent), GLEE_SW_STEPS (0 = run until killed),
GLEE_FAMILIES (causal scope).
"""

import importlib
import logging
import os
import shutil

from glee_sdk import GleeClient

import tw.params as params
from tw import search, optimizer, propose, diagnose, evolve, policy, sim, window as W
from tw.window import SlidingWindow
from run_optim import play_batch

logging.basicConfig(level=logging.INFO, format="%(message)s")

WINDOW = int(os.environ.get("GLEE_SW_WINDOW", "60"))
STEP = int(os.environ.get("GLEE_SW_STEP", "8"))       # champion games to refresh the window each step
CHUNK = int(os.environ.get("GLEE_SW_CHUNK", "4"))     # candidate games between anytime-valid checks
CAP = int(os.environ.get("GLEE_SW_CAP", "40"))        # max candidate games before giving up (reject)
FAMILIES = os.environ.get("GLEE_FAMILIES", "negotiation").split(",")
STEPS = int(os.environ.get("GLEE_SW_STEPS", "0"))
EVOLVE_EVERY = int(os.environ.get("GLEE_SW_EVOLVE_EVERY", "3"))  # every N steps, try a CODE edit (0=off)
EVOLVE_THRESH = float(os.environ.get("GLEE_EVOLVE_THRESH", "0.8"))


def main():
    client = GleeClient(api_key=os.environ["GLEE_API_KEY"])
    shutil.copy("params.json", "params_prechampion.json")
    allowed = params.knobs_for(FAMILIES)
    fam_scope = FAMILIES[0] if len(FAMILIES) == 1 else None   # family-scoped Reflexion lessons
    champion = params.load()
    win = SlidingWindow(WINDOW)

    def hlog(msg):                                          # clean, human-readable stream
        with open("logs/optimizer_human.log", "a") as fh:
            fh.write(msg + "\n")

    hlog(f"\n===== optimizer session | families={FAMILIES} =====")

    def play(n, mt=900):
        return play_batch(client, n, FAMILIES, max_time=mt)

    def code_evolve_step():
        """A guarded CODE edit (structural), not just a knob: propose -> validate -> A/B vs window."""
        if EVOLVE_EVERY <= 0 or fam_scope is None or fam_scope not in evolve.EDITABLE:
            return
        diag = diagnose.report(family=fam_scope)
        backup = evolve.snapshot()
        hlog(f"\n── code-evolve {fam_scope} ──\n   diagnostics:\n{diag}")
        prop = evolve.propose_code(fam_scope, diag, search.load_lessons(fam_scope))
        if not prop:
            hlog("   proposer returned nothing — skipping code edit."); return
        hlog(f"   hypothesis: {prop.get('hypothesis','')}  | new_knobs: {prop.get('new_knobs', [])}")
        ok, full, errs = evolve.validate(fam_scope, prop["code"])
        if not ok:
            hlog(f"   ❌ failed validation {errs[:2]} — discarded (champion untouched).")
            search.add_lesson(f"[code] {fam_scope} edit failed validation: {errs[:1]}", fam_scope); return
        passed, cf, chf = evolve.sim_gate(full, fam_scope)          # INSTANT proxy filter (ms)
        hlog(f"   sim-gate: cand {cf} vs champ {chf} -> {'PASS' if passed else 'FAIL (skip online, no games spent)'}")
        if not passed:
            search.add_lesson(f"[code sim-reject] {fam_scope} cand {cf} < champ {chf}: {prop.get('hypothesis','')[:90]}", fam_scope)
            return
        for k in prop.get("new_knobs", []):
            try:
                params.register_knob(k["name"], k["default"], k["bounds"], k["family"])
                hlog(f"   + registered knob {k['name']} {k['bounds']}")
            except Exception as e:
                hlog(f"   [knob register failed] {e}")
        evolve.POLICY.write_text(full); importlib.reload(policy)   # candidate live in-process
        cand_recs = search.game_records(play(min(CAP, 14), 1800))
        champ_adj, cand_adj = W.cuped_adjust(win.records(), cand_recs)
        base = sum(champ_adj) / len(champ_adj) if champ_adj else 0.0
        cand_m = sum(cand_adj) / len(cand_adj) if cand_adj else 0.0
        p = W._boot_prob(champ_adj, cand_adj) if champ_adj and cand_adj else None
        if p is not None and p >= EVOLVE_THRESH:
            hlog(f"   🚀 CODE ADOPT (P={p:.2f}): cand {cand_m:.3f} vs champ {base:.3f}. backup {backup}")
            search.add_lesson(f"[code ADOPT P={p:.2f}] {fam_scope}: {prop.get('hypothesis','')[:110]}", fam_scope)
        else:
            evolve.restore(backup); importlib.reload(policy)
            hlog(f"   ↩ CODE REVERT (P={p}): cand {cand_m:.3f} vs champ {base:.3f}. restored champion.")
            search.add_lesson(f"[code revert P={p}] {fam_scope}: {prop.get('hypothesis','')[:110]}", fam_scope)

    print(f"sliding-window optimizer | families={FAMILIES} | window={WINDOW} | "
          f"CUPED variance-reduction + anytime-valid stop | tuning {len(allowed)} knobs", flush=True)
    print(f"warming up champion window ({min(WINDOW, 20)} games)…", flush=True)
    params.save(champion); params.reload()
    win.extend(search.game_records(play(min(WINDOW, 20), 1800)))

    step = 0
    while STEPS == 0 or step < STEPS:
        step += 1
        print(f"\n=== step {step} === champion baseline={round(win.mean(), 3) if win.mean() else None} "
              f"(window n={len(win)})", flush=True)

        params.save(champion); params.reload()             # keep the champion baseline current
        win.extend(search.game_records(play(STEP)))

        if EVOLVE_EVERY > 0 and step % EVOLVE_EVERY == 0:   # periodic structural CODE edit
            code_evolve_step()
            win = SlidingWindow(WINDOW)                     # code may have changed — rebuild baseline
            win.extend(search.game_records(play(STEP)))

        diag = diagnose.report(family=fam_scope)
        patch, hyp, src = propose.propose(champion, search.load_lessons(fam_scope), allowed, diag=diag)
        print(f"proposed via {src}: {patch}", flush=True)
        if hyp:
            print(f"  optimizer thinking: {hyp}", flush=True)
        hlog(f"\n── step {step} ── champion baseline {round(win.mean(), 3) if win.mean() else None}")
        hlog(f"   proposed [{src}]: {patch}")
        if hyp:
            hlog(f"   thinking: {hyp}")
        cand = optimizer.apply_patch(champion, patch)
        if not optimizer.validate(cand):
            print("invalid patch, skipping step", flush=True)
            continue

        # INSTANT sim screen: reject a knob patch that regresses in self-play before spending games
        if fam_scope in ("bargaining", "negotiation"):
            params.save(cand); params.reload()
            cs = sim.evaluate(policy, fam_scope, n=500)["mean_fitness"]
            params.save(champion); params.reload()
            chs = sim.evaluate(policy, fam_scope, n=500)["mean_fitness"]
            hlog(f"   sim-screen: cand {cs:.4f} vs champ {chs:.4f} -> {'pass' if cs >= chs * 0.98 else 'SKIP online'}")
            if cs < chs * 0.98:
                search.add_lesson(f"[SW sim-skip] {patch} cand {cs:.4f} < champ {chs:.4f}", fam_scope)
                continue

        # anytime-valid test: play the candidate in small chunks, stop as soon as we're confident
        print("testing candidate (CUPED-adjusted, anytime-valid stop)…", flush=True)
        params.save(cand); params.reload()
        cand_recs, dec = [], "continue"
        while dec == "continue":
            cand_recs += search.game_records(play(CHUNK, 1800))
            champ_adj, cand_adj = W.cuped_adjust(win.records(), cand_recs)
            base = sum(champ_adj) / len(champ_adj) if champ_adj else 0.0
            dec = W.seq_decision(champ_adj, cand_adj, cap=CAP)
            print(f"  cand n={len(cand_recs)} adj_mean={round(sum(cand_adj)/len(cand_adj),3) if cand_adj else None} "
                  f"vs champ_adj {round(base,3)} -> {dec}", flush=True)

        adopt = dec == "adopt"
        if adopt:
            champion = cand
            win = SlidingWindow(WINDOW)                     # reset baseline for the new champion
        params.save(champion); params.reload()              # deploy champion (candidate if adopted)

        cand_mean = sum(f for f, _ in cand_recs) / len(cand_recs) if cand_recs else None
        search.add_lesson(f"[SW {src}][{'ADOPT' if adopt else 'reject'}] {patch}" + (f" | {hyp}" if hyp else ""), fam_scope)
        search.log_hypothesis({"ep": step, "src": src, "patch": patch, "thinking": hyp,
                               "adopt": adopt, "decision": dec,
                               "champ_share": round(base, 3), "cand_share": round(cand_mean, 3) if cand_mean else None,
                               "n_per_arm": [len(win), len(cand_recs)]})
        print(f"step {step}: champ_adj {round(base,3)} vs cand {round(cand_mean,3) if cand_mean else None} "
              f"(cand n={len(cand_recs)}) -> {'ADOPT' if adopt else 'reject'}", flush=True)
        hlog(f"   → {'ADOPT ✅' if adopt else 'reject'}: cand {round(cand_mean,3) if cand_mean else None} "
             f"vs champ {round(base,3)} (n={len(cand_recs)})")

    print("optimizer stopped.", flush=True)


if __name__ == "__main__":
    main()
