"""Run the Type Whisperer agent against the GLEE competition server.

    pip install glee-sdk
    export GLEE_API_KEY=glee_...        # from your dashboard; NOT hardcoded here
    python run_agent.py

Logs land in ./logs/  (games.jsonl = your dataset, profiles.json = opponent memory).
"""

import logging
import os

from glee_sdk import GleeClient

from tw.strategy import strategy

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")


def main():
    api_key = os.environ.get("GLEE_API_KEY", "")
    if not api_key:
        raise SystemExit("Set GLEE_API_KEY  (export GLEE_API_KEY=glee_...)")

    base_url = os.environ.get("GLEE_API_URL")           # only for a local dev backend
    client = GleeClient(api_key=api_key, base_url=base_url) if base_url \
        else GleeClient(api_key=api_key)

    print("stats:", client.stats())                     # verifies the key before playing
    client.run(strategy, concurrency=6)                 # plays all three families, continuously


if __name__ == "__main__":
    main()
