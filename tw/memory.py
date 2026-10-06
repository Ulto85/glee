"""Memory: per-game offer traces (in RAM) + per-opponent profiles (on disk) + a turn log."""

import json
import os
from pathlib import Path

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

_TRACES = {}                                  # game_id -> list of "good for me" values
_PROFILE_PATH = LOG_DIR / "profiles.json"
# Per-agent turn log so a single agent's SELLER games (never recorded in the per-cell baseline,
# since the buyer acts last) are isolable for offline reconstruction. Falls back to the shared
# games.jsonl when GLEE_AGENT is unset (keeps the historical dataset intact for training).
_AGENT = os.environ.get("GLEE_AGENT")
_GAMES_LOG = LOG_DIR / (f"games_{_AGENT}.jsonl" if _AGENT else "games.jsonl")


def record_offer(game_id, goodness):
    """Append the opponent's latest offer (scored from our side) to this game's trace."""
    _TRACES.setdefault(game_id, []).append(goodness)


def trace(game_id):
    """Return the list of offers seen so far in this game (oldest first)."""
    return _TRACES.get(game_id, [])


_BELIEFS = {}                                   # game_id -> ValueBelief (BBR, per game)


def belief(game_id, scale, opp_is_seller):
    """Get/create the per-game opponent-value belief."""
    b = _BELIEFS.get(game_id)
    if b is None:
        from tw.belief import ValueBelief
        b = _BELIEFS[game_id] = ValueBelief(scale, opp_is_seller)
    return b


def log_turn(record):
    """Write one JSON line per turn — this file is your dataset."""
    with _GAMES_LOG.open("a") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def _load():
    if _PROFILE_PATH.exists():
        try:
            return json.loads(_PROFILE_PATH.read_text())
        except Exception:
            return {}
    return {}


_PROFILES = _load()


def profile_for(name, family):
    """Return the stored profile for an opponent+family, or None if unseen."""
    return _PROFILES.get(f"{name}:{family}") if name else None


def update_profile(name, family, archetype, confidence):
    """Merge this game's read into the durable profile keyed on opponent name."""
    if not name:
        return
    key = f"{name}:{family}"
    p = _PROFILES.get(key, {"archetype": "unknown", "confidence": 0.0, "seen": 0})
    if confidence > 0.4:
        p["archetype"] = archetype
        p["confidence"] = round(0.6 * p["confidence"] + 0.4 * confidence, 3)
    p["seen"] += 1
    _PROFILES[key] = p
    _PROFILE_PATH.write_text(json.dumps(_PROFILES, indent=2))
