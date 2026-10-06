"""Overall GLEE leaderboard poller — reconstructs the ACCOUNT board (mean-of-3-family rating, the real
ranking) from the 3 public per-family feeds, and flags every one of OUR agents by matching the board's
player ids/names to our own agents' stats (via each farm's API key). Run each tick for ground-truth RANK.

    python3 experiments/overall_board.py
"""
import glob
import json
import os
import re
import urllib.request

FAMS = ("bargaining", "negotiation", "persuasion")
HDRS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Accept": "application/json"}


def fetch(family):
    req = urllib.request.Request(f"https://glee-competition.com/api/leaderboard?family={family}", headers=HDRS)
    d = json.loads(urllib.request.urlopen(req, timeout=20).read())
    return d if isinstance(d, list) else d.get("leaderboard") or d.get("entries") or d.get("data") or []


def our_agents():
    """Map board-key -> our label, by asking each farm key's stats() for its agent id+name."""
    keys = {}
    for envf in sorted(glob.glob("farm_env_*.txt")):
        label = re.sub(r"farm_env_|\.txt", "", os.path.basename(envf))
        m = re.search(r"GLEE_API_KEY=(\S+)", open(envf).read())
        if m:
            keys[label] = m.group(1)
    ids, names = {}, {}
    try:
        from glee_sdk import GleeClient
        for label, k in keys.items():
            try:
                s = GleeClient(api_key=k).stats() or {}
                aid = s.get("agent_id") or s.get("player_id")
                nm = s.get("agent_name") or s.get("player_name")
                if aid:
                    ids[aid] = label
                if nm:
                    names[nm] = label
            except Exception:
                pass
    except Exception:
        pass
    return ids, names


def key_of(r):
    return r.get("player_id") or r.get("agent_id"), r.get("player_name") or r.get("agent_name")


def main():
    ids, names = our_agents()
    board = {}  # pid -> {name, fam:rating}
    for fam in FAMS:
        try:
            rows = fetch(fam)
        except Exception as e:
            print(f"{fam}: ERR {e}"); continue
        for r in rows:
            pid, nm = key_of(r)
            k = pid or nm
            if k is None:
                continue
            b = board.setdefault(k, {"name": nm, "pid": pid})
            b[fam] = r.get("rating")
    # overall = mean of the 3 family ratings we can see (board agents have all 3)
    rowso = []
    for k, b in board.items():
        vals = [b.get(f) for f in FAMS if isinstance(b.get(f), (int, float))]
        if not vals:
            continue
        overall = sum(vals) / len(vals)
        label = ids.get(b.get("pid")) or names.get(b.get("name"))
        rowso.append((overall, b.get("name"), label, b.get("bargaining"), b.get("negotiation"), b.get("persuasion")))
    rowso.sort(key=lambda x: -x[0])
    print(f"{'#':>3} {'player':22s} {'OVR':>6} {'barg':>6} {'neg':>6} {'pers':>6}")
    ours_seen = []
    for i, (ov, nm, label, ba, ne, pe) in enumerate(rowso, 1):
        us = f"  <== US ({label})" if label else ""
        if label:
            ours_seen.append((i, label, ov))
        if i <= 12 or label:
            print(f"{i:>3} {str(nm)[:22]:22s} {ov:6.0f} {ba or 0:6.0f} {ne or 0:6.0f} {pe or 0:6.0f}{us}")
    if ours_seen:
        best = min(ours_seen)  # lowest rank number = best
        print(f"\nBEST OF OURS: {best[1]} at #{best[0]} (overall {best[2]:.0f}) | all ours: " +
              ", ".join(f"{l}#{r}" for r, l, _ in sorted(ours_seen)))
    else:
        print("\n(no US agents matched on the board — outside top-50 in all families)")


if __name__ == "__main__":
    main()
