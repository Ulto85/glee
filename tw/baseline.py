"""Low-noise evaluation via a persisted per-cell champion baseline (audit-3 design).

Score = percentile of our payoff vs the field within the SAME config+role cell. For a fixed cell that
is a monotone function of our own payoff, so: (a) the objective decomposes per cell — never pool across
heterogeneous cells (that pooling is what made A/Bs ~44% noise); (b) to decide candidate ≥ champion we
need only our OWN payoffs, cell by cell (the field is fixed); (c) rank-within-cell is the variance
reducer — scale-free, bounded [0,1], kills the bimodal 0/positive blowup, and mirrors the true score.

The farm plays the frozen champion at volume; we persist those outcomes as the reference distribution
F_c per cell. A candidate is then scored paired against F_c (no noisy fresh champion arm needed).
Degenerate cells (no gains-from-trade → everyone 0, or too few samples) are dropped: pure noise.
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

from tw import search

_PATH = Path("logs/champion_baseline.jsonl")


def record(outcomes, path=_PATH):
    """Append finished champion games as the per-cell reference. Called by farm.py (free). Also persists
    the ACROSS-TABLE opponent's realized payoff (a field-agent sample) so we can benchmark us-vs-field:
    'do we out-realize the agents we actually face, per config-cell' — the field comparison the inward
    champion baseline can't give (audit: our evaluator never observed the field)."""
    with open(path, "a") as fh:
        for o in outcomes:
            r = o.get("result") or {}
            me = o.get("your_player", "player_1")
            ot = "player_1" if me == "player_2" else "player_2"
            fh.write(json.dumps({
                "cell": o.get("cov", "?"), "family": o.get("family"),
                "share": search._one_fitness(o), "payoff": r.get(f"{me}_payoff"),
                "opp_payoff": r.get(f"{ot}_payoff"),      # field-agent realized outcome (across the table)
                "outcome": r.get("outcome"), "opponent": o.get("opponent"),
                "game_id": o.get("game_id"),               # opp-log: join key to the turn logs
            }) + "\n")


def coarse_cell(cell):
    """Group cells by their main difficulty axes, dropping the fine value bucket, so cells fill to
    n>=20 in ~an hour instead of never. Safe because we rank SHARE (already scale-free), so mixing
    value scales within a coarse cell is fine: neg -> role+horizon; barg -> discount; pers -> p(high)."""
    parts = str(cell).split("|")
    fam = parts[0]
    if fam == "neg":                                  # neg|role|v..|h..
        role = parts[1] if len(parts) > 1 else "?"
        h = next((p for p in parts if p.startswith("h")), "?")
        return f"neg|{role}|{h}"
    if fam == "barg":                                 # barg|d..|mr..
        return f"barg|{parts[1] if len(parts) > 1 else '?'}"
    if fam == "pers":                                 # pers|role|p..|r..|m..  (Fable#10 F3)
        role = parts[1] if len(parts) > 1 else "?"    # was pers|{role} only -> pooled ALL p,r,mtype into
        pp_ = next((x for x in parts[2:] if x.startswith("p")), "?")   # one cell (buyer knobs washed).
        rr = next((x for x in parts[2:] if x.startswith("r")), "?")    # keep role+p+r so -EV cells split out.
        return f"pers|{role}|{pp_}|{rr}"
    return str(cell)


def load(path=_PATH, window=800):
    """Per-COARSE-cell rolling list of champion shares (recent `window` each, tracks non-stationarity)."""
    cells = defaultdict(list)
    if not Path(path).exists():
        return cells
    for line in Path(path).read_text(errors="ignore").splitlines():
        try:
            d = json.loads(line)
        except Exception:
            continue
        cells[coarse_cell(d["cell"])].append(d["share"])
    return {c: v[-window:] for c, v in cells.items()}


def rank(cell_vals, value):
    """Mid-rank percentile of `value` within a cell's champion distribution (0.5 == matches champion)."""
    if not cell_vals:
        return 0.5
    below = sum(1 for x in cell_vals if x < value)
    eq = sum(1 for x in cell_vals if x == value)
    return (below + 0.5 * eq) / len(cell_vals)


def _degenerate(cell_vals, min_n):
    return len(cell_vals) < min_n or max(cell_vals) == min(cell_vals)


def score(outcomes, baseline=None, min_n=20):
    """Per-cell mean rank of candidate games vs the champion baseline, aggregated as mean-of-cell-means.
    >0.5 beats the champion. Degenerate/thin cells dropped. Returns score + coverage for honesty."""
    baseline = baseline if baseline is not None else load()
    by_cell = defaultdict(list)
    for o in outcomes:
        by_cell[coarse_cell(o.get("cov", "?"))].append(search._one_fitness(o))
    cell_means, dropped, degen = [], 0, 0
    for c, vals in by_cell.items():
        base = baseline.get(c, [])
        if _degenerate(base, min_n):
            dropped += 1
            if base and max(base) == min(base):
                degen += 1
            continue
        cell_means.append(statistics.mean(rank(base, v) for v in vals))
    return {"score": round(statistics.mean(cell_means), 4) if cell_means else None,
            "cells_scored": len(cell_means), "cells_dropped": dropped,
            "cells_degenerate": degen, "n_games": len(outcomes)}


def baseline_summary(path=_PATH):
    b = load(path)
    tot = sum(len(v) for v in b.values())
    usable = sum(1 for v in b.values() if not _degenerate(v, 20))
    return {"cells": len(b), "usable_cells(n>=20,non-flat)": usable, "total_samples": tot}
