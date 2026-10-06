"""Play N games and print ratings before/after. Used by the dashboard and on its own.

    python play.py [n_games] [families]
    python play.py 15 negotiation,bargaining
"""

import logging
import os
import sys

from glee_sdk import GleeClient
from tw.strategy import strategy

logging.basicConfig(level=logging.INFO, format="%(message)s")


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    fams = sys.argv[2].split(",") if len(sys.argv) > 2 else ["negotiation", "bargaining", "persuasion"]
    c = GleeClient(api_key=os.environ["GLEE_API_KEY"])
    print(f"playing {n} games in {fams}", flush=True)
    print("BEFORE", {k: round(v["rating"], 1) for k, v in c.stats()["scores"].items()}, flush=True)
    mt = int(os.environ.get("GLEE_PLAY_MAXTIME", "1200"))   # raise for overnight runs
    c.run(strategy, game_families=fams, max_games=n, max_time=mt, concurrency=4)
    print("AFTER", {k: (round(v["rating"], 1), v["games_played"]) for k, v in c.stats()["scores"].items()}, flush=True)
    print("done.", flush=True)


if __name__ == "__main__":
    main()
