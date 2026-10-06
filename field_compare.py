"""Us-vs-field: from champion_baseline.jsonl entries that carry opp_payoff (the across-table field
agent's realized outcome), report per family how often WE out-realize the field agent and the mean
payoff-share of the pie we capture. >0.5 payoff-share and >50% win-rate = beating the field we face.
Zero rated-game cost, zero noise (it's just realized outcomes). Needs recent games (opp_payoff is
only logged going forward). This is the field benchmark the inward champion baseline can't give.
"""
import json
from collections import defaultdict
from pathlib import Path

P = Path("logs/champion_baseline.jsonl")
byf = defaultdict(lambda: {"n": 0, "wins": 0, "ties": 0, "shares": []})
for line in P.read_text(errors="ignore").splitlines() if P.exists() else []:
    try:
        d = json.loads(line)
    except Exception:
        continue
    mp, op = d.get("payoff"), d.get("opp_payoff")
    if mp is None or op is None:
        continue                                  # old entries without opp_payoff
    f = byf[d.get("family", "?")]
    f["n"] += 1
    if mp > op:
        f["wins"] += 1
    elif mp == op:
        f["ties"] += 1
    tot = abs(mp) + abs(op)
    if tot > 0:
        f["shares"].append(mp / tot)             # our share of the two realized payoffs

import statistics as st
print("us vs the field agents we actually face (realized payoff):")
for fam, s in byf.items():
    if s["n"] == 0:
        continue
    wr = s["wins"] / s["n"]
    ps = st.mean(s["shares"]) if s["shares"] else 0.0
    print(f"  {fam:12s} n={s['n']:4d}  win-rate={wr:.2f}  mean_payoff_share={ps:.3f}  "
          f"(>0.50 = out-realizing the field)")
if not any(s["n"] for s in byf.values()):
    print("  (no opp_payoff data yet — accumulating; run again after ~50+ farmed games)")
