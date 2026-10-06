"""Quick overnight progress readout: rating, total games, and games/time this session.

    python3 progress.py
"""

import json
import os
import time

from glee_sdk import GleeClient

s = GleeClient(api_key=os.environ["GLEE_API_KEY"]).stats().get("scores", {})
try:
    start = json.load(open("logs/overnight_start.json"))
except Exception:
    start = {}

print(f"{'family':12s} {'rating':>8s} {'games':>7s} {'+session':>9s}")
total = 0
for f in ("bargaining", "negotiation", "persuasion"):
    d = s.get(f) or {}
    r, g = d.get("rating"), d.get("games_played")
    sg = (start.get(f) or {}).get("games_played", g) if start else g
    plus = (g - sg) if (g is not None and sg is not None) else 0
    total += plus
    print(f"{f:12s} {str(r):>8} {str(g):>7} {'+' + str(plus):>9}")

ts = start.get("_start_ts")
if ts:
    hrs = (time.time() - ts) / 3600
    rate = total / max(hrs, 0.01)
    print(f"\nelapsed: {hrs:.1f}h   games this session: +{total}   (~{rate:.0f}/hr)")
else:
    print(f"\ngames this session: +{total}")
