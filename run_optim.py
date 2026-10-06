"""LLM-as-optimizer loop: play a batch -> analyze traces -> LLM patches the ruleset -> repeat.

    export GLEE_API_KEY=glee_...
    # optional auto mode: pick any litellm model + its provider key, else manual mode
    export GLEE_OPTIM_MODEL=gpt-4o-mini            # (needs OPENAI_API_KEY), or a claude/gemini id
    python run_optim.py

Env knobs: GLEE_FAMILIES (default bargaining,negotiation), GLEE_BATCH (games/iter, 6),
GLEE_ITERS (3), GLEE_MAXTIME (seconds/batch, 240).

Manual mode (no LLM key): each iteration writes optim_prompt.txt; paste it into any chat,
then drop the returned JSON patch into optim_patch.json and it's applied next iteration.
"""

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

_MOVE_WORKERS = int(os.environ.get("GLEE_MOVE_WORKERS", "16"))   # process pending moves in parallel so a
# slow LLM call (Opus) doesn't serially back up the queue and expire games ('Game is not active')

from glee_sdk import GleeClient

import tw.params as params
from tw import analyzer, optimizer, search
from tw.strategy import strategy

# --- AIMD congestion signal: count the SDK's rate-limit / transport warnings ------------------
_RL = {"n": 0}


class _RLCounter(logging.Handler):
    def emit(self, record):
        try:
            m = record.getMessage()
        except Exception:
            return
        if "Rate limited" in m or "Request error" in m:
            _RL["n"] += 1


_sdk_log = logging.getLogger("glee_sdk")
_sdk_log.addHandler(_RLCounter())
_sdk_log.setLevel(logging.WARNING)

FAMILIES = os.environ.get("GLEE_FAMILIES", "bargaining,negotiation").split(",")
BATCH = int(os.environ.get("GLEE_BATCH", "6"))
ITERS = int(os.environ.get("GLEE_ITERS", "3"))
MAX_TIME = int(os.environ.get("GLEE_MAXTIME", "240"))


def ratings(client):
    # Resilient: a TRANSIENT server error (e.g. [500]) on this non-critical boot call must NOT crash the farm —
    # a boot crash leaves in-flight games to time out on our turn, which trips GLEE's stuck-agent queue pause
    # (~25min). Retry a few times, then fall back to placeholders and keep running (2026-08-19 cascade lesson).
    for _attempt in range(4):
        try:
            s = client.stats().get("scores", {})
            return {f: s.get(f, {}).get("rating", 1000.0) for f in ("bargaining", "negotiation", "persuasion")}
        except Exception as e:
            print(f"  [ratings] transient error (attempt {_attempt+1}/4): {str(e)[:100]}", flush=True)
            time.sleep(3)
    print("  [ratings] giving up after retries — using placeholders, continuing", flush=True)
    return {f: 1000.0 for f in ("bargaining", "negotiation", "persuasion")}


def play_batch(client, n, families, max_time, poll=2.0):
    """Play up to n games under the current params, capturing each finished result (verbose)."""
    # THROUGHPUT via AIMD (TCP-style congestion control): the concurrent-games cap was self-imposed.
    # The server allows far more (probed to ~49) but throttles with soft 429s that, if they delay a
    # move past its turn deadline, cost an abandoned game. So we ADAPT the window: additive-increase
    # (cwnd += INC) each healthy cycle, MULTIPLICATIVE-DECREASE (cwnd //= 2) the moment we see a
    # rate-limit / transport warning (the _RL congestion signal) or a hard queue error. This auto-finds
    # the max sustainable concurrency and backs off before throttling turns into abandonment.
    CWND_MIN = int(os.environ.get("GLEE_CWND_MIN", "12"))
    CWND_MAX = int(os.environ.get("GLEE_CWND_MAX", "64"))
    CWND_INC = int(os.environ.get("GLEE_CWND_INC", "3"))
    # GENTLE backoff: a soft 429 is harmless (SDK just waits+retries); the ONLY real harm is a move
    # delayed past its turn deadline (abandonment), and we have no fairness obligation to other flows.
    # KEY FIX: at high concurrency the necessary MOVES themselves draw soft 429s every cycle, so
    # treating "any rate-limit" as congestion made cwnd collapse while active stayed healthy ("goes to
    # 18 and stays"). Only back off on SUSTAINED congestion (>= RLTOL rate-limits in one cycle); a
    # stray throttle is absorbed. Reserve a hard halve for a real queue exception.
    BACKOFF = float(os.environ.get("GLEE_CWND_BACKOFF", "0.85"))
    RLTOL = int(os.environ.get("GLEE_CWND_RLTOL", "4"))       # rate-limits/cycle before we call it congestion
    cwnd = [int(os.environ.get("GLEE_CONCURRENCY", "8"))]
    # GLEE_FAM_ROTATE=1 fixes the topup skew: `families[i % len]` restarts i=0 every topup call, so with
    # small `need` (steady state 1-2) the FIRST-listed family absorbs most queues (Fable 2026-08-21). A
    # persistent counter round-robins evenly across families instead. Bit-identical when off (default).
    _FAM_ROTATE = os.environ.get("GLEE_FAM_ROTATE", "0") == "1"
    qrot = [0]
    seen_rl = [_RL["n"]]

    # DRAIN mode (Fable#14b): a graceful stop for the freeze/switch sequence. Touch logs/drain_<agent> and
    # the farm STOPS queuing new games but keeps moving in-flight ones to completion, then exits — so a
    # family switch or a final freeze costs ZERO forfeits (a plain kill abandons ~12-20 in-flight games at
    # the 5th percentile, branding that scar onto whatever family fossilizes next; measured -17.6 pers once).
    _drain_path = os.path.join("logs", f"drain_{os.environ.get('GLEE_AGENT', '')}")
    def draining():
        return os.path.exists(_drain_path)

    def topup():
        rl = _RL["n"] - seen_rl[0]                             # rate-limits since last cycle
        seen_rl[0] = _RL["n"]
        congested = rl >= RLTOL                                # only SUSTAINED throttling counts
        prev = cwnd[0]
        if congested:
            cwnd[0] = max(CWND_MIN, int(cwnd[0] * BACKOFF))    # gentle multiplicative decrease
        else:
            cwnd[0] = min(CWND_MAX, cwnd[0] + CWND_INC)        # additive increase
        try:
            active = client.stats().get("active_games", 0)
        except Exception:
            active = 0
        need = max(0, cwnd[0] - active)
        for i in range(need):                                  # spread across families
            try:
                if _FAM_ROTATE:
                    client.queue(families[qrot[0] % len(families)]); qrot[0] += 1
                else:
                    client.queue(families[i % len(families)])
            except Exception:
                cwnd[0] = max(CWND_MIN, cwnd[0] // 2); break   # hard queue error = real failure -> halve
        if cwnd[0] != prev:
            print(f"  [aimd] cwnd {prev}->{cwnd[0]} (active={active}, congested={congested})", flush=True)
        return active
    topup(); print(f"  AIMD concurrency control on (start {cwnd[0]}, max {CWND_MAX}, {','.join(families)})", flush=True)
    print(f"  waiting for matches… (target {n} games)", flush=True)
    outcomes, start, moves, last_beat = [], time.monotonic(), 0, time.monotonic()
    seen = {}                                                  # F4: game_id -> (family, your_player, cov) for every game we observe
    while len(outcomes) < n and time.monotonic() - start < max_time:
        if draining():                                         # graceful stop: finish in-flight, queue nothing
            try:
                active = client.stats().get("active_games", 0)
                pend = len(client.pending_games() or [])
            except Exception:
                active, pend = 1, 1                            # be conservative; don't exit on a transient error
            if active == 0 and pend == 0:
                print("  [drain] all in-flight games finished — exiting batch clean", flush=True)
                break
        try:
            games = client.pending_games()
        except Exception as e:
            print("  [pending error]", e, flush=True); time.sleep(poll); continue
        for g in games:                                        # F4: remember it, so an opponent-ended game can still be captured
            _op = g.get("opponent") or {}                      # opp-log: named rated opponent (null = house bot)
            _opn = _op.get("name") if _op.get("type") in ("agent", "human") else None
            seen[g["game_id"]] = (g["game_family"], g.get("your_player"), search.cov_key(g), _opn)
        def _move_one(g):
            try:
                res = client.move(g["game_id"], strategy(g))
                if res.get("valid") is False:
                    print(f"  [invalid move] {res.get('error')}", flush=True)
                if res.get("game_over"):
                    r = res.get("result") or {}
                    _op = g.get("opponent") or {}              # opp-log: attach named opponent + game_id
                    return {"family": g["game_family"], "your_player": g.get("your_player"),
                            "result": r, "cov": search.cov_key(g), "game_id": g["game_id"],
                            "opponent": _op.get("name") if _op.get("type") in ("agent", "human") else None}
            except Exception as e:
                print("  [move error]", e, flush=True)
            return None
        if games:                                              # process moves in PARALLEL (overlap slow LLM calls)
            with ThreadPoolExecutor(max_workers=min(len(games), _MOVE_WORKERS)) as ex:
                for out in ex.map(_move_one, games):
                    moves += 1
                    if out:
                        outcomes.append(out)
                        print(f"  ✓ finished {out['family']} [{len(outcomes)}/{n}] "
                              f"outcome={out['result'].get('outcome')}", flush=True)
        now = time.monotonic()
        if not games and now - last_beat > 8:                 # heartbeat so it never looks frozen
            active = 0
            try:
                active = client.stats().get("active_games", 0)
            except Exception:
                pass
            print(f"  … waiting for a turn (moves so far: {moves}, active games: {active})", flush=True)
            last_beat = now
        if len(outcomes) < n and not draining():               # DRAIN: stop queuing new games; only finish in-flight
            topup()                                            # keep the queue full toward TARGET concurrency
        time.sleep(poll)
    # F4 (outcome_capture_full, Fable#11): games that ended on the OPPONENT'S move — they accepted our offer
    # (our BEST bargaining/neg games) and EVERY persuasion game where we're seller (buyer moves last) — never
    # fire our game_over, so ~2/3 of finished games (incl. all our wins) were silently dropped, biasing every
    # offer-side gate ('aggression regresses', 'knobs wash'). Poll the state of each game we saw but didn't
    # record; synthesize the outcome. Paced ~1 req/s (well under 60/60s — the rate-limit incident). Gate off
    # via GLEE_CAPTURE_FULL=0.
    # F4 v2 (Fable#12 fix): the v1 loop polled ALL seen games with a 1.1s sleep each (35-70s/batch of
    # ZERO moves) -> starved live turns past their 120s deadline -> abandonments at the 5th pctile, and it
    # shipped in the same restart as barg_deadline_spe so the rating crash was misattributed to the lever.
    # v2: poll ONLY games that VANISHED from the queue (ended on the opponent's move), no per-game sleep,
    # hard-capped, and only after the batch is done (no live moves left to starve). GLEE_CAPTURE_FULL=0 disables.
    if os.environ.get("GLEE_CAPTURE_FULL", "1") == "1":
        done = {o.get("game_id") for o in outcomes}
        try:
            still = {g["game_id"] for g in (client.pending_games() or [])}
        except Exception:
            still = set()
        vanished = [gid for gid in seen if gid not in done and gid not in still]
        captured = 0
        for gid in vanished[:int(os.environ.get("GLEE_CAPTURE_MAX", "25"))]:
            try:
                gs = client.game_state(gid) or {}
            except Exception:
                continue
            res = gs.get("result") or {}
            if gs.get("status") == "completed" or res.get("outcome"):
                fam, yp, cov, oppn = seen[gid]
                outcomes.append({"family": fam, "your_player": yp, "result": res,
                                 "cov": cov, "game_id": gid, "captured": "poll", "opponent": oppn})
                captured += 1
        if captured:
            print(f"  [F4] captured {captured}/{len(vanished)} opponent-ended outcomes", flush=True)
    try:
        client.leave_queue()
    except Exception:
        pass
    print(f"  batch done: {len(outcomes)} games, {moves} moves", flush=True)
    return outcomes


def main():
    client = GleeClient(api_key=os.environ["GLEE_API_KEY"])
    params.reload()
    history, last_patch, prev_params = [], None, params.load()
    print("start ratings:", ratings(client))

    for it in range(ITERS):
        before = ratings(client)
        outcomes = play_batch(client, BATCH, FAMILIES, MAX_TIME)
        after = ratings(client)
        delta = {f: round(after[f] - before[f], 2) for f in after}
        dsum = round(sum(delta.values()), 2)
        report = analyzer.report_text(analyzer.summarize(outcomes), delta, params.P)
        print(f"\n=== iter {it} (batch fitness {dsum:+.2f}) ===\n{report}")

        if last_patch is not None:                      # judge the patch that drove this batch
            if dsum < -1.0:
                print("[loop] patch hurt -> reverting:", last_patch)
                params.save(prev_params); params.reload()
            history.append({"iter": it, "patch": last_patch, "fitness": dsum})

        prev = params.load()
        patch = optimizer.propose(report, history)
        if patch and optimizer.validate(optimizer.apply_patch(prev, patch)):
            print("[loop] applied patch:", patch)
            last_patch, prev_params = patch, prev
        else:
            params.save(prev); params.reload()
            last_patch = None
        json.dump(history, open("logs/optim_history.json", "w"), indent=2)

    print("\nFINAL params:", json.dumps(params.load(), indent=2))
    print("FINAL ratings:", ratings(client))


if __name__ == "__main__":
    main()
