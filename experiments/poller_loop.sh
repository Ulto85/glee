#!/bin/bash
cd "$(dirname "$0")/.."
while true; do
  python3 experiments/peak_poller.py --state logs/peak_state.json >> logs/poller_loop.log 2>> logs/poller_loop.log
  sleep 900
done
