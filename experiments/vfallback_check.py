"""Validate pers_kg_vfallback (Fable#9a): the KG seller was DEAD when buyer value v is hidden
(is_seller_know_cv=false) -> mid-game low-push rate 0.00. After the fix (v_eff=price*r_assumed) it
should rise toward the field's 0.55-0.80. Measures the LOW-PUSH rate (fraction of low-quality items the
seller recommends BUY = deception) on binary turns, split by v-hidden vs v-visible, mid-game only
(remaining > endgame_force). Compares a pre-fix log slice vs a post-fix slice.
Usage: python experiments/vfallback_check.py <log> [start_line] [endgame_force]
"""
import json, sys, collections

LOG   = sys.argv[1] if len(sys.argv) > 1 else "logs/games_gamma.jsonl"
START = int(sys.argv[2]) if len(sys.argv) > 2 else 0
ENDG  = int(sys.argv[3]) if len(sys.argv) > 3 else 2      # pers_low_push_endgame (forced-push tail)

push = collections.Counter(); tot = collections.Counter()
for i, l in enumerate(open(LOG)):
    if i < START: continue
    try: r = json.loads(l)
    except: continue
    if r.get("family") != "persuasion": continue
    if "seller" not in str(r.get("phase") or ""): continue
    st = r.get("state") or {}
    q = st.get("current_quality")
    if q != "low": continue                              # low-push = deception decision KG governs
    tr = st.get("total_rounds") or 20; rnd = st.get("round") or 1
    if (tr - rnd) <= ENDG: continue                      # skip forced-push endgame -> isolate KG effect
    a = r.get("action") or {}
    dec = a.get("decision")
    if dec not in ("yes", "no"): continue                # binary format only (unambiguous push signal)
    vh = "v-hidden" if not st.get("is_seller_know_cv") else "v-visible"
    tot[vh] += 1
    if dec == "yes": push[vh] += 1

print(f"=== vfallback low-push check — {LOG.split('/')[-1]} (from line {START}, binary, mid-game, low items) ===")
for vh in ("v-hidden", "v-visible"):
    n = tot[vh]
    rate = push[vh] / n if n else 0.0
    tag = "  <-- KG target (field 0.55-0.80)" if vh == "v-hidden" else ""
    print(f"  {vh}: low-push {rate:.3f}  ({push[vh]}/{n}){tag}")
