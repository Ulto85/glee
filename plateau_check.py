"""Plateau detector for the autonomous tick. Prints 'PLATEAU <family>' when a SPECIFIC family is
genuinely stuck — gated on GAMES PLAYED, not just idle time — else 'OK'. Tuned a bit aggressive so we
iterate before getting cooked, but a plateau must be confirmed by real volume so we don't audit noise.

Trigger: family flat (rating move < FLAT) for >= FLAT_WINDOW AND >= MIN_GAMES of that family played in
the window AND rating < CEILING. The per-family window resets after each audit (and whenever the rating
moves), so a still-stuck family re-audits after another FLAT_WINDOW of continued flatness. No global
cooldown. State: logs/audit_state.json.
"""
import json
import os
import time
from pathlib import Path

from glee_sdk import GleeClient

FLAT = 8.0          # rating move < this = flat
FLAT_WINDOW = 720   # flat at least this long (s) — ~12 min (was 20; more aggressive)
MIN_GAMES = 12      # ...AND at least this many of that family played in the window (real evidence, not idle)
CEILING = 1950      # only audit families below this (room to improve)
_PATH = Path("logs/audit_state.json")
_FARM = Path("logs/farm.log")


def _load():
    if _PATH.exists():
        try:
            return json.loads(_PATH.read_text())
        except Exception:
            pass
    return {"last_audit_ts": 0, "families": {}}


def _games_played():
    """Finished games per family in the current farm.log (resets on farm restart — fine)."""
    out = {"bargaining": 0, "negotiation": 0, "persuasion": 0}
    if _FARM.exists():
        txt = _FARM.read_text(errors="ignore")
        for f in out:
            out[f] = txt.count(f"finished {f}")
    return out


def main():
    now = time.time()
    st = _load()
    s = GleeClient(api_key=os.environ["GLEE_API_KEY"]).stats().get("scores", {})
    ratings = {f: s.get(f, {}).get("rating", 0) for f in ("bargaining", "negotiation", "persuasion")}
    played = _games_played()
    fam_st = st.setdefault("families", {})
    verdict = "OK"
    for fam, r in ratings.items():
        ref = fam_st.get(fam)
        if ref is None or abs(r - ref["ref_rating"]) >= FLAT or played[fam] < ref.get("ref_games", 0):
            fam_st[fam] = {"ref_rating": r, "ref_ts": now, "ref_games": played[fam]}   # moved/reset window
            continue
        flat_for = now - ref["ref_ts"]
        games_in_window = played[fam] - ref.get("ref_games", 0)
        if r < CEILING and flat_for >= FLAT_WINDOW and games_in_window >= MIN_GAMES:
            st["last_audit_ts"] = now
            fam_st[fam] = {"ref_rating": r, "ref_ts": now, "ref_games": played[fam]}   # reset so it must re-plateau
            verdict = f"PLATEAU {fam} (rating {round(r)}, flat {int(flat_for/60)}min, {games_in_window} games)"
            break
    _PATH.write_text(json.dumps(st, indent=2))
    print(verdict)


if __name__ == "__main__":
    main()
