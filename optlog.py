"""Human-readable optimizer timeline + are-we-improving summary (no per-turn noise).

    python3 optlog.py           # last ~20 steps + rating trajectory this session

Reads logs/hypotheses.jsonl (one record per optimizer step) and live stats.
"""

import json
import os

from glee_sdk import GleeClient


def load_jsonl(p):
    out = []
    if os.path.exists(p):
        for line in open(p):
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


steps = load_jsonl("logs/hypotheses.jsonl")

print("=" * 72)
print("OPTIMIZER TIMELINE  (most recent 20 steps)")
print("=" * 72)
for s in steps[-20:]:
    ad = s.get("adopt")
    verdict = "✅ ADOPT" if ad else "·  reject"
    cs, cd = s.get("champ_share"), s.get("cand_share")
    think = (s.get("thinking") or "").strip()
    n = s.get("n_per_arm")
    print(f"\nstep {s.get('ep')}  [{s.get('src','?')}]  {verdict}   (n={n})")
    print(f"   champ {cs}  vs  cand {cd}   patch={json.dumps(s.get('patch', {}))}")
    if think:
        print(f"   💭 {think[:160]}{'…' if len(think) > 160 else ''}")

print("\n" + "=" * 72)
print("ARE WE IMPROVING?")
print("=" * 72)
adopts = sum(1 for s in steps if s.get("adopt"))
print(f"steps run: {len(steps)}   |   adopts (validated policy improvements): {adopts}   |   "
      f"rejects: {len(steps) - adopts}")

try:
    sc = GleeClient(api_key=os.environ["GLEE_API_KEY"]).stats().get("scores", {})
    start = json.load(open("logs/overnight_start.json")) if os.path.exists("logs/overnight_start.json") else {}
    print("\nrating trajectory:")
    for f in ("bargaining", "negotiation", "persuasion"):
        d = sc.get(f, {})
        r, g = d.get("rating"), d.get("games_played")
        sg = (start.get(f) or {}).get("games_played")
        delta = f"  (+{g - sg} games this session)" if isinstance(sg, int) and isinstance(g, int) else ""
        print(f"  {f:12s} rating={r}  games={g}{delta}")
    if adopts == 0 and steps:
        print("\n→ No adopts yet: either near the policy ceiling, or candidates aren't clearing the "
              "confidence bar (check champ vs cand above — close = keep exploring).")
except Exception as e:
    print("  (live stats unavailable:", e, ")")
