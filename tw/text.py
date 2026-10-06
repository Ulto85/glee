"""Text layer: read the opponent's message to type them fast, and send better messages.

LLM-authored assets live in data/ (text_rules.json, messages.json) and are applied cheaply
at runtime — no per-turn LLM call. A background agent mines transcripts to enrich these files;
until then the built-in DEFAULTS are used.
"""

import json
import os
from pathlib import Path

DATA = Path("data")
DATA.mkdir(exist_ok=True)
_RULES_PATH = DATA / "text_rules.json"
# GLEE_MSG_BANK lets one agent A/B a different message bank (default unchanged for others)
_MSG_PATH = Path(os.environ["GLEE_MSG_BANK"]) if os.environ.get("GLEE_MSG_BANK") else DATA / "messages.json"

# substrings in the OPPONENT's message -> archetype hint (lowercased match)
DEFAULT_RULES = {
    "over_conceder": ["fine", "okay deal", "i can do", "let's just", "works for me", "deal"],
    "hard_anchorer": ["final offer", "take it or leave", "won't go", "that's my price",
                      "non-negotiable", "best i can do", "firm"],
    "impatient": ["quickly", "right now", "no time", "hurry", "today", "let's wrap"],
}

# outgoing message bank, keyed by family then situation
DEFAULT_MSGS = {
    "negotiation": {
        "seller": ["Premium unit — this is my price.", "Priced to move; I can't go lower."],
        "buyer": ["That's my budget.", "I'll meet you partway, but not there."],
    },
    "bargaining": {"any": ["Waiting costs us both — this is my firm, fair split.",
                           "Let's settle now; delay only shrinks the pot."]},
    "persuasion": {"high": ["Genuinely strong this round — worth buying.", "This one's a clear buy."],
                   "low": ["I'd hold off this round.", "Not this one."],
                   "unknown": ["Reasonable pick this round."]},
}


def _load(path, default):
    if path.exists():
        try:
            return {**default, **json.loads(path.read_text())}
        except Exception:
            pass
    return dict(default)


RULES = _load(_RULES_PATH, DEFAULT_RULES)
MSGS = _load(_MSG_PATH, DEFAULT_MSGS)


def opponent_message(state, me):
    """The opponent's most recent free-text message, if any."""
    lo = state.get("last_offer") or {}
    if isinstance(lo, dict) and lo.get("message"):
        return str(lo["message"])
    for h in reversed(state.get("history") or []):
        if isinstance(h, dict) and h.get("message") and h.get("player") not in (me, None):
            return str(h["message"])
    return ""


import re as _re
_NUM = _re.compile(r"[-+]?\$?\d[\d,]*\.?\d*")


def extract_numbers(msg):
    """Numbers stated in an opponent message (weak evidence about their reservation value)."""
    out = []
    for m in _NUM.findall(msg or ""):
        try:
            x = float(m.replace(",", "").replace("$", ""))
            if x > 0:
                out.append(x)
        except ValueError:
            pass
    return out


def classify_text(msg):
    """Map an opponent message to (archetype, confidence) via substring rules."""
    if not msg:
        return None, 0.0
    m = msg.lower()
    best, best_c = None, 0.0
    for arch, pats in RULES.items():
        hits = sum(1 for p in pats if p.lower() in m)
        if hits:
            c = min(0.8, 0.4 + 0.15 * hits)
            if c > best_c:
                best, best_c = arch, c
    return best, best_c


def pick_message(family, key, idx=0):
    """Pick an outgoing message from the bank for this family/situation."""
    bank = MSGS.get(family, {})
    opts = bank.get(key) or bank.get("any") or bank.get("unknown") or []
    return opts[idx % len(opts)] if opts else ""
