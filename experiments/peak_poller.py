"""p95 per-family PEAK poller (PROTOTYPE — task #124, endgame bank trigger).

Samples each of our agents' per-family ratings from the SAME server source the farms
and leaderboard identity-matching use — GleeClient(api_key).stats()["scores"][fam]["rating"]
(per-agent, works even outside the public top-50; unplayed family = 1000.0, which matches
the OFFICIAL overall = mean-of-3-with-unplayed=1000 rule; do NOT copy overall_board.py's
mean-of-seen-families bug that over-credits single-family rivals).

Each invocation (intended cadence: <=10 min via cron/loop):
  1. reads farm_env_<agent>.txt for API keys (READ-ONLY),
  2. fetches current per-family ratings per agent,
  3. appends the sample to a rolling state file (scratchpad-only write),
  4. prunes samples older than the trailing window (default 24h),
  5. prints per agent: current 3-fam mean, per-family current vs trailing PEAK and p95,
     plus flags:
       BANK-NOW  — the throttle-to-bank trigger: current 3-fam MEAN within --tol (default
                   1%) of its trailing-window PEAK mean, once the window has >= --min-span-h
                   hours of history (warmup guard: with <min span the "peak" is just the
                   first few samples and the flag would fire trivially).
       fam^p95   — per-family spike signal: that family's CURRENT rating >= its trailing
                   p95 (journal 08-22 22:31 / 08-23 09:15: per-leg staggered freezes arm on
                   the p95 trigger, so per-family spikes are surfaced independently of the
                   mean flag).

Usage:
    python3 peak_poller.py                 # one sample + report (normal tick)
    python3 peak_poller.py --window-h 24 --tol 0.01 --min-span-h 6
    python3 peak_poller.py --report-only   # re-print from state without sampling

READ-ONLY on everything live: only GETs /stats; only write is the state JSON next to
this script (or --state PATH). Install target later: experiments/peak_poller.py with
--state moved somewhere persistent (state survives relocation; it is plain JSON).
"""
import argparse
import json
import os
import sys
import time

GLEE_DIR = "/Users/aayanrizvi/Documents/glee"
AGENTS = ("champion", "gamma", "theta", "delta", "eta")
FAMS = ("bargaining", "negotiation", "persuasion")
FSHORT = {"bargaining": "barg", "negotiation": "neg", "persuasion": "pers"}
DEFAULT_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "peak_state.json")


def api_keys():
    """agent label -> API key, from farm_env_<label>.txt (exact files only, not .pre_* backups)."""
    keys = {}
    for label in AGENTS:
        path = os.path.join(GLEE_DIR, f"farm_env_{label}.txt")
        try:
            for line in open(path):
                line = line.strip()
                if line.startswith("GLEE_API_KEY="):
                    keys[label] = line.split("=", 1)[1].strip()
                    break
        except OSError:
            pass
    return keys


def fetch_ratings(key):
    """Per-family rating dict via the server stats endpoint. Unplayed family -> 1000.0
    (matches run_optim.ratings() and the official mean-of-3 rule). Retries transient errors."""
    sys.path.insert(0, GLEE_DIR)
    from glee_sdk import GleeClient
    client = GleeClient(api_key=key)
    last = None
    for _ in range(3):
        try:
            s = client.stats().get("scores", {})
            return {f: float(s.get(f, {}).get("rating", 1000.0)) for f in FAMS}
        except Exception as e:  # transient 5xx etc — never crash the tick
            last = e
            time.sleep(2)
    raise RuntimeError(f"stats() failed after retries: {last}")


def load_state(path):
    try:
        return json.load(open(path))
    except Exception:
        return {"samples": {}}  # agent -> [[ts, barg, neg, pers], ...] (chronological)


def save_state(state, path):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh)
    os.replace(tmp, path)


def p95(vals):
    """95th percentile, linear interpolation, no numpy."""
    if not vals:
        return float("nan")
    v = sorted(vals)
    if len(v) == 1:
        return v[0]
    k = 0.95 * (len(v) - 1)
    lo = int(k)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (k - lo) * (v[hi] - v[lo])


def main():
    ap = argparse.ArgumentParser(description="p95 per-family peak poller (bank trigger)")
    ap.add_argument("--window-h", type=float, default=24.0, help="trailing window hours (default 24)")
    ap.add_argument("--tol", type=float, default=0.01, help="BANK-NOW: mean within this fraction of trailing peak (default 0.01 = 1%%)")
    ap.add_argument("--min-span-h", type=float, default=6.0, help="min hours of history before flags arm (warmup guard)")
    ap.add_argument("--state", default=DEFAULT_STATE, help="state JSON path (scratchpad)")
    ap.add_argument("--report-only", action="store_true", help="no new sample; report from existing state")
    args = ap.parse_args()

    now = time.time()
    state = load_state(args.state)
    samples = state.setdefault("samples", {})

    if not args.report_only:
        keys = api_keys()
        for agent in AGENTS:
            if agent not in keys:
                print(f"{agent}: no farm_env key file — skipped", file=sys.stderr)
                continue
            try:
                r = fetch_ratings(keys[agent])
            except Exception as e:
                print(f"{agent}: SAMPLE ERR {e}", file=sys.stderr)
                continue
            samples.setdefault(agent, []).append(
                [round(now, 1)] + [round(r[f], 2) for f in FAMS])
        # prune to trailing window (+small slack so the peak at exactly window-edge survives one tick)
        cutoff = now - args.window_h * 3600 - 300
        for agent in list(samples):
            samples[agent] = [s for s in samples[agent] if s[0] >= cutoff]
        save_state(state, args.state)

    # ---- report ----
    print(f"# peak_poller {time.strftime('%Y-%m-%d %H:%M:%S %Z', time.localtime(now))} | "
          f"window={args.window_h:g}h tol={args.tol:.1%} min-span={args.min_span_h:g}h | official mean-of-3, unplayed=1000")
    hdr = f"{'agent':9s} {'MEAN cur':>8} {'peak':>7} {'p95':>7} {'d%':>6}"
    for f in FAMS:
        hdr += f" | {FSHORT[f]:>4} {'cur':>6}/{'peak':>6}/{'p95':>6}"
    print(hdr + "  flags")
    bank = []
    for agent in AGENTS:
        rows = samples.get(agent) or []
        if not rows:
            print(f"{agent:9s} (no samples)")
            continue
        cur = rows[-1]
        span_h = (rows[-1][0] - rows[0][0]) / 3600.0
        means = [(s[1] + s[2] + s[3]) / 3.0 for s in rows]
        cur_mean, peak_mean, p95_mean = means[-1], max(means), p95(means)
        gap = (peak_mean - cur_mean) / peak_mean if peak_mean else float("nan")
        line = f"{agent:9s} {cur_mean:8.1f} {peak_mean:7.1f} {p95_mean:7.1f} {100*gap:5.2f}%"
        flags = []
        for i, f in enumerate(FAMS, start=1):
            fvals = [s[i] for s in rows]
            fcur, fpeak, fp95 = fvals[-1], max(fvals), p95(fvals)
            line += f" | {FSHORT[f]:>4} {fcur:6.0f}/{fpeak:6.0f}/{fp95:6.0f}"
            if span_h >= args.min_span_h and fcur >= fp95:
                flags.append(f"{FSHORT[f]}^p95")
        if span_h < args.min_span_h:
            flags.append(f"warmup({span_h:.1f}h/{args.min_span_h:g}h)")
        elif cur_mean >= (1.0 - args.tol) * peak_mean:
            flags.insert(0, "BANK-NOW")
            bank.append(agent)
        print(line + "  " + (" ".join(flags) or "-"))
    if bank:
        print(f"\n*** BANK-NOW: {', '.join(bank)} — 3-fam mean within {args.tol:.0%} of trailing "
              f"{args.window_h:g}h peak. Throttle/freeze THIS agent's legs now (stagger per-family "
              f"on the fam^p95 spikes; journal 08-23 09:15).")


if __name__ == "__main__":
    main()
