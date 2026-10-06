"""Subprocess play helper: fresh-imports whatever policy.py is on disk, plays N games, dumps records.
Used by run_evolve.py to A/B champion-code vs candidate-code without in-process module reloading.

    python3 eval_play.py N family1,family2 out.json
"""
import json
import os
import sys

from glee_sdk import GleeClient

from run_optim import play_batch
from tw import search

n = int(sys.argv[1])
families = sys.argv[2].split(",")
out = sys.argv[3]
client = GleeClient(api_key=os.environ["GLEE_API_KEY"])
recs = search.game_records(play_batch(client, n, families, max_time=2400))
json.dump(recs, open(out, "w"))
print(f"[eval_play] wrote {len(recs)} records to {out}", flush=True)
