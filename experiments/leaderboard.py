"""Live GLEE leaderboard tracker. Fetches the public top-50-per-family board and locates our agents,
reporting rank, rating, gap-to-top-50-floor, and gap-to-top-10. Use each tick to watch RANK (the real
objective), not just our internal rating.

    GLEE_API_KEY=... python3 experiments/leaderboard.py
"""
import json
import os
import urllib.request

FAMS = ("bargaining", "negotiation", "persuasion")
HDRS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Accept": "application/json"}


def fetch(family):
    req = urllib.request.Request(f"https://glee-competition.com/api/leaderboard?family={family}", headers=HDRS)
    d = json.loads(urllib.request.urlopen(req, timeout=15).read())
    return d if isinstance(d, list) else d.get("leaderboard") or d.get("entries") or d.get("data") or []


def our_ids():
    from glee_sdk import GleeClient
    ids = {}
    for label, env in (("champion", "GLEE_API_KEY"), ("theta", "GLEE_API_KEY2")):
        k = os.environ.get(env)
        if k:
            try:
                s = GleeClient(api_key=k).stats()
                ids[s.get("agent_id") or s.get("player_id")] = (label, s.get("agent_name"))
            except Exception:
                pass
    return ids


def main():
    ids = our_ids()
    for fam in FAMS:
        try:
            rows = fetch(fam)
        except Exception as e:
            print(f"{fam}: ERR {e}"); continue
        ranked = [r for r in rows if r.get("rank")]
        top = rows[0]["rating"] if rows else 0
        floor50 = rows[-1]["rating"] if rows else 0
        top10 = ranked[9]["rating"] if len(ranked) >= 10 else (ranked[-1]["rating"] if ranked else 0)
        mine = [(i + 1, r) for i, r in enumerate(rows)
                if r.get("player_id") in ids or r.get("player_name") in ("Tester1", "theta", "gamma")]
        tag = ""
        for pos, r in mine:
            tag += f"  US[{r.get('player_name')}] #{pos} @{r['rating']:.0f}"
        print(f"{fam:12s} top={top:.0f} top10={top10:.0f} floor50={floor50:.0f}{tag or '  (us: outside top-50)'}")


if __name__ == "__main__":
    main()
