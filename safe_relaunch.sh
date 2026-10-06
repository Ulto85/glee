#!/usr/bin/env bash
# safe_relaunch.sh — SAFE farm (re)launcher. Guards against the 2026-08-13 rate-limit incident.
# The incident: farms were batch-restarted 7+ times -> request burst -> HTTP 429 -> in-flight games
# expired as forfeits (payoff 0 = worst percentile) -> ratings crashed; then a timed-out command killed
# every farm's process group at once. This script structurally prevents all three failure modes:
#   1. relaunches ONLY dead farms  -> never restarts a live one -> no batch-restart request burst
#   2. COOLDOWN between relaunches  -> no bunching -> stays under the 60 req/60s limit -> no 429/forfeits
#   3. setsid + nohup + </dev/null  -> fully detached -> a caller/tool timeout cannot kill the farms
#   4. GLEE_CONCURRENCY forced to 12 (safe steady-state; NEVER 20 during a (re)launch)
# Usage:  bash safe_relaunch.sh            # relaunch any DEAD farm (respecting cooldown)
#         bash safe_relaunch.sh status     # report liveness only, launch nothing
set -u
cd "$(dirname "$0")"
AGENTS="theta champion gamma delta eta"
COOLDOWN=120                 # min seconds between any two relaunches (anti-bunch)
LOCK=logs/last_relaunch_ts

alive() {                    # $1=agent : true if a farm.py process carries GLEE_AGENT=$1 in its env
  local p
  for p in $(pgrep -f 'farm.py' 2>/dev/null); do
    ps eww -o command= -p "$p" 2>/dev/null | tr ' ' '\n' | grep -qx "GLEE_AGENT=$1" && return 0
  done
  return 1
}

MODE="${1:-relaunch}"
now=$(date +%s)
for ag in $AGENTS; do
  if alive "$ag"; then echo "[$ag] ALIVE"; continue; fi
  echo "[$ag] DEAD"
  [ "$MODE" = status ] && continue
  last=$(cat "$LOCK" 2>/dev/null || echo 0)
  if [ $((now-last)) -lt "$COOLDOWN" ]; then
    echo "  -> cooldown active ($((COOLDOWN-(now-last)))s left); NOT relaunching this pass (anti-bunch guard)"
    continue
  fi
  envf="farm_env_${ag}.txt"
  if [ ! -f "$envf" ]; then echo "  -> missing $envf, cannot launch"; continue; fi
  # macOS has no setsid; nohup + </dev/null + a fast-returning script keeps the child alive (the incident's
  # farm-death was a TIMED-OUT command killing the process group, which a fast return avoids).
  ( set -a; . "./$envf"; export GLEE_CONCURRENCY=12; set +a
    nohup python3 -u farm.py >> "logs/farm_${ag}.out" 2>&1 < /dev/null & )   # append (>>) so restart history survives
  echo "$now" > "$LOCK"
  echo "  -> relaunched (detached, concurrency 12); ${COOLDOWN}s cooldown starts now"
  now=$(date +%s)
done
echo "done ($(date '+%H:%M:%S'))."
