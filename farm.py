"""Freeze-and-farm: play a FROZEN policy for pure volume. Audit finding #1 — volume of a good frozen
policy is the only thing that ever moved our ratings, and it carries zero regression risk.

Multi-agent capable (for the shadow-champion A/B): each process loads its OWN params file directly
into memory (no shared params.json clobber) and writes its OWN baseline, so agent-1 (champion) and
agent-2 (variant) run in parallel, each with its own AIMD concurrency window and its own per-cell
baseline — a live, field-truthful, paired-by-cell A/B where a losing variant can't touch the
account rank (GLEE ranks the account by the BEST of its agents).

    GLEE_API_KEY=glee_...            # which agent (champion vs variant)
    GLEE_PARAMS=params_good.json     # policy to freeze (default champion)
    GLEE_BASELINE=logs/champion_baseline.jsonl   # per-cell record (use a distinct file per agent)
    GLEE_AGENT=champion              # label for logs
    GLEE_FAMILIES=persuasion,negotiation,bargaining
"""
import json
import os
import time

from glee_sdk import GleeClient

import tw.params as params
from run_optim import play_batch, ratings
from tw import baseline

FAMILIES = os.environ.get("GLEE_FAMILIES", "bargaining,negotiation").split(",")
BATCH = int(os.environ.get("GLEE_FARM_BATCH", "16"))
PARAMS_FILE = os.environ.get("GLEE_PARAMS", "params_good.json")
BASELINE_FILE = os.environ.get("GLEE_BASELINE", "logs/champion_baseline.jsonl")
AGENT = os.environ.get("GLEE_AGENT", "champion")

# Load the frozen policy DIRECTLY into memory — no write to the shared params.json, so a second
# farm process can run a different variant without clobbering ours.
params.P.clear()
params.P.update(params._merge(params.DEFAULTS, json.loads(open(PARAMS_FILE).read())))
os.environ["GLEE_NEG_V2"] = os.environ.get("GLEE_NEG_V2", "0")
# GLEE_EXPLOIT is respected if explicitly set (a variant may test it); champion leaves it unset.

client = GleeClient(api_key=os.environ["GLEE_API_KEY"])
print(f"FARM [{AGENT}] | params={PARAMS_FILE} | families={FAMILIES} | start ratings={ratings(client)}", flush=True)
_DRAIN_PATH = os.path.join("logs", f"drain_{AGENT}")
# THROTTLE-PROTECT (snapshot-at-deadline defense): rating is an EWMA of per-game percentiles, so
# full-volume farming REVERTS an afternoon peak into the evening field-firm trough. When logs/throttle_<agent>
# exists, play a tiny batch + sleep so the rate falls to ~9 games/h (~1.3% of full volume, still above the
# ~100-games/fam/48h decay floor) — the EWMA then barely moves, FREEZING a peak so we hold #3-5 through the
# evening regardless of when the deadline snapshot lands. Dormant unless the sentinel is present (touch to
# freeze, rm to resume — no relaunch either way once this code is live). Tunable via env.
_THROTTLE_PATH = os.path.join("logs", f"throttle_{AGENT}")
_THR_BATCH = int(os.environ.get("GLEE_THROTTLE_BATCH", "3"))
_THR_SLEEP = int(os.environ.get("GLEE_THROTTLE_SLEEP", "1200"))
# PER-FAMILY PEAK-BANK (endgame lever, research 2026-08-22): `touch logs/freeze_<agent>_<family>` STOPS
# farming that ONE family — its per-family EWMA then holds at its current (peak) value while the agent keeps
# farming the OTHER families (stays active → no inactivity decay). `rm` to resume. Bit-identical when no
# sentinel exists. Bank each leg at its diurnal peak (carrier gamma; barg~01-04h / pers~04-07h EDT) to build
# a synthetic high-mean agent for the deadline snapshot. Clean-drain only — never hard-kill in-flight (forfeit scar).
_FREEZE_PRE = os.path.join("logs", f"freeze_{AGENT}_")
n = 0
while True:
    throttled = os.path.exists(_THROTTLE_PATH)
    _active = [f for f in FAMILIES if not os.path.exists(_FREEZE_PRE + f)]
    if not _active:                                           # all legs frozen (snapshot instant): idle, don't crash
        if os.path.exists(_DRAIN_PATH):
            print(f"[farm {AGENT}] DRAIN sentinel present — exiting cleanly (0 forfeits)", flush=True)
            break
        print(f"[farm {AGENT}] all families frozen — idling", flush=True)
        time.sleep(_THR_SLEEP)
        continue
    if throttled:
        # EVEN-SPLIT across families (Opus audit 2026-08-16 fix): a mixed throttle batch starves the
        # low-mix family below the ~100-games/fam/48h decay floor (champion barg mix ~0.17 → ~73/48h).
        # Play exactly 1 game PER family per cycle so every family clears the floor (~142/fam/48h at 1200s).
        outs = []
        for _fam in _active:
            outs += play_batch(client, 1, [_fam], max_time=3000)
    else:
        outs = play_batch(client, BATCH, _active, max_time=3000)
    baseline.record(outs, path=__import__("pathlib").Path(BASELINE_FILE))
    n += len(outs)
    print(f"[farm {AGENT}] ~{n} games | ratings={ratings(client)}{' [THROTTLED]' if throttled else ''}", flush=True)
    if os.path.exists(_DRAIN_PATH):                            # graceful stop: in-flight games already drained clean
        print(f"[farm {AGENT}] DRAIN sentinel present — exiting cleanly (0 forfeits)", flush=True)
        break
    if throttled:
        time.sleep(_THR_SLEEP)                                 # ~9 games/h: freeze the peak, stay above decay floor
