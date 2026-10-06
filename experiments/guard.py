"""Overnight safety MONITOR — ALERT-ONLY (does NOT auto-revert).

Usage: python3 experiments/guard.py <baseline_backup_dir> [drop]

Prints the per-family delta of current live ratings vs the baseline recorded in <dir>/_meta.json.
If any family dropped more than `drop` (default 60) it prints a WOULD-REVERT warning — but it does
NOT touch any files. Reverting is a human/operator decision (`exp.py restore <dir>` then restart farm).

WHY ALERT-ONLY: an earlier version auto-restored files and wrongly reverted the CHAMPION to a stale
snapshot on natural EMA/field drift, wiping current code. Two reasons that must never recur:
  1. The champion runs ONLY validated fixes — it should never be auto-reverted on drift.
  2. Unvalidated variants now run on a SEPARATE agent (theta); GLEE ranks the account by the BEST
     agent, so a bad variant cannot hurt rank — there is nothing to auto-revert. The shadow agent IS
     the safety mechanism; this script is just a dashboard.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY = os.environ.get("GLEE_API_KEY", "")


def ratings():
    from glee_sdk import GleeClient
    s = GleeClient(api_key=KEY).stats().get("scores", {})
    return {f: round(s.get(f, {}).get("rating", 0)) for f in ("bargaining", "negotiation", "persuasion")}


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else ""
    drop = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0
    if path and not os.path.isdir(path):
        path = os.path.join(ROOT, "backups", path)
    if not path or not os.path.isdir(path):
        print(f"OK (no baseline dir; current={ratings()})"); return
    base = json.load(open(os.path.join(path, "_meta.json")))["baseline_ratings"]
    cur = ratings()
    deltas = {f: cur.get(f, 0) - base.get(f, 0) for f in base if isinstance(base.get(f), int)}
    worst = min(deltas.items(), key=lambda kv: kv[1]) if deltas else (None, 0)
    if worst[1] <= -drop:
        print(f"WOULD-REVERT (ALERT ONLY, no files touched): {worst[0]} {worst[1]:+d} <= -{drop:.0f} | "
              f"deltas={deltas} | if intended: python3 experiments/exp.py restore {path} then restart farm")
    else:
        print(f"OK deltas={deltas} (worst {worst[0]} {worst[1]:+d}, alert at -{drop:.0f})")


if __name__ == "__main__":
    main()
