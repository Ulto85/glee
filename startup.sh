#!/usr/bin/env bash
# startup.sh — resume GLEE farming after shutdown.sh. Relaunches all 3 farms (safe, detached, capped) +
# the sleep guard. Crons are session-only, so re-create the tick/audit crons by ASKING CLAUDE after this.
# Usage: bash startup.sh
set -u
cd "$(dirname "$0")"
echo "=== GLEE startup $(date '+%Y-%m-%d %H:%M:%S') ==="

# 1. sleep guard (needs AC power + lid OPEN for full effect; caffeinate can't stop clamshell sleep on battery)
pkill -f caffeinate 2>/dev/null
nohup caffeinate -dimsu >/dev/null 2>&1 </dev/null &
echo "  caffeinate started — PLUG INTO AC + KEEP LID OPEN for overnight farming"

# 2. reset kill timer + relaunch all 3 farms, staggered 15s (anti-bunch) via the guardrail
date +%s > logs/loop_start_ts
for ag in theta champion gamma; do
  rm -f logs/last_relaunch_ts        # deliberate staggered launch, clear the anti-bunch lock each pass
  bash safe_relaunch.sh >/dev/null 2>&1
  sleep 15
done
echo "  farms relaunched:"; bash safe_relaunch.sh status

# 3. reminders
echo ""
echo "NEXT (ask Claude in the new session):"
echo "  - Re-create the crons: 5-min tick + 15-min audit (session-only, gone after last session)."
echo "  - Read SESSION_STATE.md for standings, shipped fixes, dead levers, and the neg-LLM 400 TODO."
echo "  - Confirm leaderboard.py shows all 3 agents (champion=Tester1, theta, gamma)."
