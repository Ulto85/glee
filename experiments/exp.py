"""Safe-experiment harness for GLEE variant trials — the backup -> variant -> test -> revert loop.

Usage (CLI):
  python3 experiments/exp.py backup  <label>        # snapshot champion code+params + live ratings
  python3 experiments/exp.py restore <backup_dir>   # roll a variant back to a snapshot (one call)
  python3 experiments/exp.py list                    # list snapshots, newest first
  python3 experiments/exp.py baseline <backup_dir>   # print the ratings recorded at snapshot time

Design: we have no git, so a snapshot is a file-copy of the champion's *live* surface — the params
files the farm loads + the policy/strategy/params code that defines behavior — plus the live ratings
at snapshot time (the trial baseline). restore() copies them back verbatim, so reverting a bad variant
is a single call. This is the durable backbone of the overnight loop: never mutate the champion
without a snapshot first, and never leave a variant live that fell below its snapshot baseline.
"""
import json
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BK = os.path.join(ROOT, "backups")
# The champion's full live surface: what the farm loads + what defines behavior.
FILES = ["params_good.json", "params.json",
         "tw/policy.py", "tw/params.py", "tw/strategy.py", "tw/llm.py"]
KEY = os.environ.get("GLEE_API_KEY", "")


def _ratings():
    try:
        from glee_sdk import GleeClient
        s = GleeClient(api_key=KEY).stats().get("scores", {})
        return {f: round(s.get(f, {}).get("rating", 0)) for f in ("bargaining", "negotiation", "persuasion")}
    except Exception as e:
        return {"error": str(e)}


def _stamp():
    # Date.now-free-safe: use a monotonic-ish counter from existing dirs so labels stay unique+ordered
    n = len([d for d in os.listdir(BK)]) if os.path.isdir(BK) else 0
    return f"{n:03d}"


def backup(label):
    os.makedirs(BK, exist_ok=True)
    r = _ratings()
    tag = f"{_stamp()}_{label}"
    dst = os.path.join(BK, tag)
    os.makedirs(dst, exist_ok=True)
    for f in FILES:
        src = os.path.join(ROOT, f)
        if os.path.exists(src):
            out = os.path.join(dst, f.replace("/", "__"))
            shutil.copy2(src, out)
    meta = {"label": label, "baseline_ratings": r, "files": FILES, "wall": time.time()}
    json.dump(meta, open(os.path.join(dst, "_meta.json"), "w"), indent=2)
    print(f"backup -> {dst}\n  baseline ratings: {r}")
    return dst


def restore(path):
    if not os.path.isdir(path):
        path = os.path.join(BK, path)
    meta = json.load(open(os.path.join(path, "_meta.json")))
    for f in meta["files"]:
        saved = os.path.join(path, f.replace("/", "__"))
        if os.path.exists(saved):
            shutil.copy2(saved, os.path.join(ROOT, f))
    print(f"restored champion from {path}\n  (was baselined at {meta['baseline_ratings']})")
    print("  -> restart farm to load it: pkill -f farm.py; then relaunch")


def lst():
    if not os.path.isdir(BK):
        print("(no backups yet)"); return
    for d in sorted(os.listdir(BK), reverse=True):
        m = os.path.join(BK, d, "_meta.json")
        if os.path.exists(m):
            meta = json.load(open(m))
            print(f"  {d:32s} baseline={meta['baseline_ratings']}")


def baseline(path):
    if not os.path.isdir(path):
        path = os.path.join(BK, path)
    print(json.load(open(os.path.join(path, "_meta.json")))["baseline_ratings"])


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    arg = sys.argv[2] if len(sys.argv) > 2 else ""
    {"backup": lambda: backup(arg or "champion"), "restore": lambda: restore(arg),
     "list": lst, "baseline": lambda: baseline(arg)}.get(cmd, lst)()
