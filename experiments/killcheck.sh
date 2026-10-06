#!/bin/bash
# Overnight safety: trip if logs/STOP exists OR farms genuinely silent for >MAXH hours.
# OUTAGE-PROOF (0825): heartbeat = the MORE RECENT of (loop_start_ts my-tick reset) OR
# (newest games_*.jsonl write = farms actually producing). So a network/laptop outage
# where I can't tick but farms keep writing does NOT trip the kill; it only trips when
# BOTH signals have been silent for MAXH = farms genuinely wedged AND monitor offline.
# On trip: kill all farm.py, mark STOPPED, print STOP. Run at the top of each tick.
cd "$(dirname "$0")/.."
MAXH=${GLEE_MAX_HOURS:-5}
now=$(date +%s)
start=$(cat logs/loop_start_ts 2>/dev/null || echo 0)
# newest mtime among games logs (farm heartbeat); 0 if none
last_game=$(ls -t logs/games_*.jsonl 2>/dev/null | head -1 | xargs -I{} stat -f %m {} 2>/dev/null || echo 0)
[ -z "$last_game" ] && last_game=0
# heartbeat = most recent of the two signals
hb=$start; [ "$last_game" -gt "$hb" ] && hb=$last_game
[ "$hb" -eq 0 ] && hb=$now   # first-run guard
elapsed_h=$(( (now-hb)/3600 ))
gap_game_h=$(( (now-last_game)/3600 ))
if [ -f logs/STOP ] || [ "$elapsed_h" -ge "$MAXH" ]; then
  pkill -f farm.py 2>/dev/null
  touch logs/STOPPED
  echo "STOP TRIGGERED (STOP-file=$([ -f logs/STOP ] && echo yes || echo no), heartbeat_idle=${elapsed_h}h/${MAXH}h, last_game=${gap_game_h}h ago) — farms killed"
else
  echo "run (heartbeat_idle=${elapsed_h}h/${MAXH}h, last_game=${gap_game_h}h ago, no STOP file)"
fi
