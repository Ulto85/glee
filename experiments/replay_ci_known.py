"""Replay the negotiation COMPLETE-INFO KNOWN-HORIZON accept decision under neg_ci_known_accept=1 vs =0
(Fable#9b). CI games are fully observable (both values logged) -> the accept-side replay is bias-free:
offers are logged, and accepting a given offer yields a deterministic surplus. For each CI+known game
where WE are the responder, walk the logged offer sequence and, under each policy, accept at the FIRST
round the policy says accept; payoff = realized surplus (0 if never = no-deal). Compare totals per cell.
Usage: python experiments/replay_ci_known.py [log] [ci_known_prog] [eps] [patience]
"""
import json, sys, collections

LOG   = sys.argv[1] if len(sys.argv) > 1 else "logs/games_champion.jsonl"
PROG0 = float(sys.argv[2]) if len(sys.argv) > 2 else 0.6      # neg_ci_known_prog
EPS   = float(sys.argv[3]) if len(sys.argv) > 3 else 0.02
PAT   = int(sys.argv[4])   if len(sys.argv) > 4 else 2        # neg_hz_patience

def progress(rnd, maxr):
    return min(0.9, (rnd - 1) / max(1, (maxr or 10) - 1))

# gather our RESPONDER decision turns per CI+known game
games = collections.defaultdict(list)   # gid -> list of dicts
for l in open(LOG):
    try: r = json.loads(l)
    except: continue
    if r.get("family") != "negotiation": continue
    st = r.get("state") or {}
    if not st.get("complete_information"): continue
    if not st.get("horizon_known", True): continue          # KNOWN horizon only
    a = r.get("action") or {}
    if a.get("decision") not in ("AcceptOffer", "RejectOffer"): continue
    gid = r.get("game_id")
    if gid is None: continue
    me = st.get("current_player")
    role = st.get(f"{me}_role"); myv = st.get(f"{me}_value")
    opp = "player_1" if me == "player_2" else "player_2"
    ov = st.get(f"{opp}_value")
    lo = st.get("last_offer") or {}
    op = lo.get("price")
    if None in (role, myv, ov, op): continue
    games[gid].append({
        "rnd": st.get("round", 1), "maxr": st.get("max_rounds") or 10,
        "is_seller": role == "seller", "myv": myv, "ov": ov, "op": op,
    })

def decide(t, ci_known_on):
    """Return realized surplus if this policy ACCEPTS at turn t, else None (reject)."""
    is_seller, myv, ov, op = t["is_seller"], t["myv"], t["ov"], t["op"]
    prog = progress(t["rnd"], t["maxr"])
    last_round = t["rnd"] >= t["maxr"]
    if is_seller:
        target = max(myv, ov * (1 - EPS)); good = op >= target; profit = op > myv
    else:
        target = min(myv, ov * (1 + EPS)); good = op <= target; profit = op < myv
    ci_known_ok = ci_known_on and prog >= PROG0
    # neg_ci_hz_accept=1 (unknown-horizon twin) is ON in champion; for KNOWN horizon it needs ci_known_ok
    hz_take = (t["rnd"] >= PAT) and ci_known_ok
    if good or (last_round and profit) or (hz_take and profit):
        return (op - myv) if is_seller else (myv - op)
    return None

cur = collections.defaultdict(float); fix = collections.defaultdict(float)
cnt = collections.Counter(); conv = collections.Counter()
nodeal_conv = collections.Counter()      # cur=0 (no-deal) -> fix>0 : the convex-near-zero percentile win
worse = collections.Counter()            # fix takes an EARLIER, strictly WORSE deal than current
nd_gain = collections.defaultdict(float) # summed fix payoff on no-deal conversions
for gid, turns in games.items():
    turns.sort(key=lambda x: x["rnd"])
    role = "seller" if turns[0]["is_seller"] else "buyer"
    cnt[role] += 1
    def sim(on):
        for t in turns:
            s = decide(t, on)
            if s is not None: return s
        return 0.0
    c, f = sim(False), sim(True)
    cur[role] += c; fix[role] += f
    if f > c + 1e-9: conv[role] += 1
    if c <= 1e-9 and f > 1e-9:
        nodeal_conv[role] += 1; nd_gain[role] += f
    elif f < c - 1e-9:
        worse[role] += 1

print(f"=== neg CI KNOWN-horizon accept replay (prog>={PROG0}, eps={EPS}, patience={PAT}) — {LOG.split('/')[-1]} ===")
for role in ("buyer", "seller"):
    n = cnt[role]
    if not n: continue
    c, f = cur[role], fix[role]
    ndc = nodeal_conv[role]
    print(f"  {role}: current {c/n:+.1f}/game -> fix {f/n:+.1f}/game  (Δ {(f-c)/n:+.1f}/game, n={n})")
    print(f"         NO-DEAL->DEAL conversions: {ndc}/{n} ({ndc/n:.0%})  |  "
          f"fix takes WORSE-earlier: {worse[role]}/{n}  |  "
          f"avg payoff on converted no-deals: {(nd_gain[role]/ndc if ndc else 0):+.0f}")
tot_n = sum(cnt.values()); tot_c = sum(cur.values()); tot_f = sum(fix.values())
if tot_n:
    print(f"  TOTAL: current {tot_c/tot_n:+.1f} -> fix {tot_f/tot_n:+.1f} per game  "
          f"({sum(conv.values())}/{tot_n} converted)")
