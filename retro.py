"""Retrospective sweep over the last N completed games (READ-ONLY — safe alongside a live run).

Joins outcomes (logs/overnight.log) with per-turn context (logs/games.jsonl) + profiles.json to:
  - report no-deal rate and captured share by family over the recent window,
  - validate the opponent model (does our archetype label predict outcome? over_conceder should score high),
  - list named opponents seen and how their modeled type lines up.
No files are written; use it to decide where the optimizer should push.
"""

import ast
import json
import re
from collections import Counter, defaultdict

N = 30
OUT, GAMES, PROF = "logs/overnight.log", "logs/games.jsonl", "logs/profiles.json"

fam, me, res, order = {}, {}, {}, []
fin = re.compile(r"Game ([0-9a-f-]+) finished! Result: (\{.*\})")
ctx = re.compile(r"Game ([0-9a-f-]+) \((\w+)\).*your turn as (player_\d)")
for line in open(OUT, errors="replace"):
    m = ctx.search(line)
    if m:
        fam[m.group(1)], me[m.group(1)] = m.group(2), m.group(3)
        continue
    m = fin.search(line)
    if m:
        try:
            res[m.group(1)] = ast.literal_eval(m.group(2))
            order.append(m.group(1))
        except Exception:
            pass
last = order[-N:]

opp, read = {}, {}
for l in open(GAMES):
    try:
        r = json.loads(l)
    except Exception:
        continue
    g = r.get("game_id")
    if not g:
        continue
    if r.get("opponent") and not opp.get(g):
        opp[g] = r["opponent"]
    read[g] = r.get("archetype")

try:
    profiles = json.load(open(PROF))
except Exception:
    profiles = {}


def share(gid):
    r = res[gid]
    mp = me.get(gid, "player_1")
    op = "player_1" if mp == "player_2" else "player_2"
    a, b = r.get(f"{mp}_payoff"), r.get(f"{op}_payoff")
    if a is None or b is None:
        return None
    if r.get("outcome") == "no_deal" or (a == 0 and b == 0):
        return 0.0
    tot = abs(a) + abs(b)
    return a / tot if tot else 0.0


byfam, byarch, nod, cnt = defaultdict(list), defaultdict(list), defaultdict(int), defaultdict(int)
for gid in last:
    f = fam.get(gid, "?")
    cnt[f] += 1
    s = share(gid)
    if s is None:
        continue
    byfam[f].append(s)
    if s == 0:
        nod[f] += 1
    byarch[read.get(gid) or "unknown"].append(s)

print(f"=== RETRO SWEEP — last {len(last)} completed games ===\n")
print("by family:")
for f in sorted(byfam):
    v = byfam[f]
    print(f"  {f:12s} games={cnt[f]:2d}  no_deal={nod[f]/max(cnt[f],1):.2f}  avg_our_share={sum(v)/len(v):.3f}")
print("\nby our archetype read (sanity-check the model — over_conceder should be highest):")
for a in sorted(byarch, key=lambda k: -sum(byarch[k]) / len(byarch[k])):
    v = byarch[a]
    print(f"  {a:14s} n={len(v):2d}  avg_share={sum(v)/len(v):.3f}")
print("\nnamed opponents this window (modeled type from profiles.json):")
def opp_name(g):
    o = opp.get(g)
    return o.get("name") if isinstance(o, dict) else (o if isinstance(o, str) else None)


seen = Counter(n for g in last if (n := opp_name(g)))
for name, c in seen.most_common(10):
    key = next((k for k in profiles if k.startswith(name + ":")), None)
    arch = profiles.get(key, {}).get("archetype", "?") if key else "not-modeled"
    print(f"  {name:16s} games={c}  modeled={arch}")
if not seen:
    print("  (opponents were hidden in this window — no names disclosed)")
