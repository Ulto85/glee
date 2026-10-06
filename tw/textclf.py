"""Frozen buyer message text-classifier (task #34): P(high | seller_message) from a bag-of-words+bigram
logistic regression fit offline (experiments/fit_buyer_textclf.py -> logs/buyer_textclf.json). No sklearn
at runtime. Validated on a by-game temporal holdout: neutral-bucket acc 0.96 vs 0.43 prior; no regression
on keyword-confident buckets. Returns None if the artifact is missing (caller falls back to its prior)."""
import json, re, math
from pathlib import Path

_TOK = re.compile(r"(?u)\b\w\w+\b")     # matches sklearn CountVectorizer default token_pattern
_ART = None
_PATH = Path("logs/buyer_textclf.json")


def _load():
    global _ART
    if _ART is None:
        try:
            _ART = json.loads(_PATH.read_text())
        except Exception:
            _ART = {}
    return _ART


def p_high(msg):
    """P(product is high quality | seller's message text), or None if the model isn't loaded."""
    art = _load()
    if not art or "weights" not in art:
        return None
    toks = _TOK.findall((msg or "").lower())
    grams = list(toks) + [toks[i] + " " + toks[i + 1] for i in range(len(toks) - 1)]
    w = art["weights"]
    z = art.get("bias", 0.0) + sum(w.get(g, 0.0) for g in grams)
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))
