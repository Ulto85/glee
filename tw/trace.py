"""Trace formats: (1) optimizer experiment cards, (2) LLM-agent reason->action traces.

Two structured formats so runs are legible and paper-ready:
 - experiment_card(): one record per optimizer iteration (params diff, metrics, verdict).
 - reasoning_trace(): the strict schema an LLM reasoning layer fills before acting.
"""

import json
from pathlib import Path

CARDS = Path("logs/experiment_cards.jsonl")
TRACES = Path("logs/agent_traces.jsonl")


def experiment_card(it, patch, before, after, summary, verdict):
    """One optimizer iteration as a structured card (append to logs, render to md)."""
    card = {
        "iter": it,
        "patch": patch,                                    # {dotted_key: new_value}
        "ratings_before": before,
        "ratings_after": after,
        "delta": {k: round(after[k] - before[k], 2) for k in after},
        "per_family": summary,                             # from analyzer.summarize
        "verdict": verdict,                                # "kept" | "reverted" | "noop"
    }
    with CARDS.open("a") as fh:
        fh.write(json.dumps(card) + "\n")
    return card


def cards_to_markdown(path=CARDS):
    """Render all experiment cards as a table (for the paper / a quick scan)."""
    if not Path(path).exists():
        return "(no cards yet)"
    rows = [json.loads(l) for l in open(path)]
    head = "| iter | patch | Δbarg | Δneg | Δpers | verdict |\n|---|---|---|---|---|---|"
    body = "\n".join(
        f"| {c['iter']} | `{json.dumps(c['patch'])}` | {c['delta'].get('bargaining',0):+.1f} "
        f"| {c['delta'].get('negotiation',0):+.1f} | {c['delta'].get('persuasion',0):+.1f} | {c['verdict']} |"
        for c in rows
    )
    return head + "\n" + body


# The schema an LLM reasoning layer must return (validated before we act on it).
REASONING_SCHEMA = {
    "opponent_type": "over_conceder | hard_anchorer | tit_for_tat | impatient | credulous | unknown",
    "belief": "one line: your estimate of their hidden value/patience and why (from their message + offers)",
    "tactic": "one line: the move you're making and the lever (anchor / hold / concede / walk / bluff)",
    "message": "the natural-language message to send (<=2000 chars, may bluff)",
}


def reasoning_trace(game_id, family, heuristic_action, llm_out):
    """Log the LLM layer's reasoning next to the deterministic action it wrapped."""
    with TRACES.open("a") as fh:
        fh.write(json.dumps({
            "game_id": game_id, "family": family,
            "heuristic_action": heuristic_action, "llm": llm_out,
        }) + "\n")
