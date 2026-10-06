"""Params: the tunable ruleset the LLM optimizer edits. Nested dict, patched by dotted keys."""

import json
from pathlib import Path

_PATH = Path("params.json")

DEFAULTS = {
    "grab": {"over_conceder": 0.80, "hard_anchorer": 0.45, "tit_for_tat": 0.60, "unknown": 0.60},
    "barg_delta_coeff": 0.62,
    "barg_arch_bump": {"over_conceder": 0.06, "hard_anchorer": 0.03, "tit_for_tat": 0.0, "unknown": 0.0},
    "barg_offer_bump": 0.08,
    "barg_share_cap": 0.75,
    "barg_accept_floor": 0.45,
    "barg_patience_k": 1.2,     # don't accept below this share while rounds remain (stop mid-game capitulation)
    "barg_concede": 0,          # 1 = δ-gated proposer concession: at δ<=delta_max, concede demand toward
    #   barg_concede_floor over ~barg_concede_rounds to CLOSE fast (premium=0 there causes re-offer deadlock)
    "barg_concede_delta_max": 0.9,
    "barg_concede_floor": 0.50,
    "barg_concede_rounds": 2.0,
    "barg_hi_open": 0,          # 1 = at δ>=hi_delta_min open at barg_hi_open_share and hold (safe aggression; no-deal cliff far)
    "barg_hi_delta_min": 0.98,
    "barg_hi_open_share": 0.68,
    "barg_accept_forward": 0,   # 1 = discount-forward accept thr δ·rub_resp (=δ²/(1+δ)): take ~0.40 early
    #   instead of rub_resp δ/(1+δ) which sits ABOVE the field's median offer (0.42) and forces us to grind
    #   rounds while δ eats the pie. Preserves δ=1.0 at 0.5 (no discount → holding genuinely pays there).
    "neg_keep_coeff": 0.35,
    "neg_accept_keep": 0.02,
    "neg_keep_floor": 0.05,
    # V2 (structural): don't over-hold. In UNKNOWN-horizon games the opponent can quit any round,
    # so a profitable offer in hand beats holding — accept it once past neg_hz_patience rounds,
    # using a tiny keep (neg_hz_keep). neg_meet_frac = how far our counter concedes toward their
    # offer as the game progresses (0 = counter at our reservation = old greedy behavior).
    "neg_meet_frac": 0.5,
    "neg_conc_alpha": 1.0,   # Boulware concession curvature (UNKNOWN-horizon): meet = neg_meet_frac*prog^alpha.
    #   1.0 = linear (current); >1 defends near reservation then concedes late (offer-side, shadow-only)
    "neg_conc_alpha_known": 1.0,  # curvature in KNOWN-horizon; kept linear (h1 gamma read showed Boulware drags there)
    "neg_opp_posture": 0,     # 1 = dossier-conditioned posture: concede LESS (meet*=neg_posture_firm) vs opponents
    #   whose historical cross-rate (logs/neg_dossier.json) >= neg_posture_hi. Targets surplus leak (open 1.30x,
    #   settle 1.07x) by holding firm only vs PROVEN payers. Offer-side -> shadow only.
    "neg_posture_hi": 0.35,
    "neg_posture_firm": 0.55,
    "neg_hz_keep": 0.04,
    "neg_hz_patience": 2,
    "neg_ci_hz_accept": 0,   # 1 = complete-info accept is horizon-aware: take a profitable CI offer past the
    #   patience round in UNKNOWN-horizon games instead of re-demanding 98% of the ZOPA forever (no-deal grind)
    "neg_ci_known_accept": 0,  # 1 = also take a profitable CI bird-in-hand in KNOWN-horizon games (was grinding
    #   known-horizon complete-info deals into no-deals: buyer|h1 had 1 accept vs 156 rejects)
    "neg_ci_known_prog": 0.6,  # known-horizon CI: only take the bird-in-hand once this far into the game
    #   (early on we can still hold for better since the opponent can't quit before the known horizon ends)
    "neg_msg_reservation": 0, # 1 = extract opponent's STATED number (bluff-robust) to price just inside their
    "neg_msg_eps": 0.02,      #   revealed reservation — only when it extracts MORE than their standing offer
    # CONDITIONAL concede-to-close: close "bridgeable stalls" (opponent conceding, thin gap, revealed ZOPA)
    "neg_close_enable": 0,    # 1 = enable; gated late+belief-evidence, prices at accept-quantile, keeps aggressive default
    "neg_close_prog": 0.5,    # only concede once game progress >= this (or unknown-horizon past patience)
    "neg_close_pacc": 0.55,   # target opponent-accept probability for the closing price
    "neg_close_frac": 0.6,    # how far to move from reservation toward the closing target
    "neg_close_min_margin": 0.02,  # never concede below this surplus margin over our value
    "neg_resv": 0,             # 1 = opponent-reservation estimator: price/accept vs inferred r̂, not own value+clock
    "neg_resv_eps": 0.02,
    "neg_resv_kproject": 0.5,
    "neg_bbr_replace": 0,     # 1 = let belief best_price REPLACE pricing (timid EV-max; lost a prior A/B — keep OFF)
    "classify": {"conc_over": 0.5, "conc_hard": 0.1, "gen_hard": 0.3},
    # persuasion: sell_low_bias = how often we push a KNOWN-low product (0=honest, 1=always sell);
    # buyer_skepticism = extra margin we demand before buying (higher = more cautious).
    # persuasion: pers_low_push_unknown = low-quality push rate when we can't see the value (KG used
    # when we can); buyer_margin = extra safety multiple on price before buying.
    "persuasion": {"sell_low_bias": 0.0, "buyer_skepticism": 0.0,
                   "pers_low_push_unknown": 0.35, "buyer_margin": 0.0,
                   "pers_honest_p": 0.65, "pers_low_push_endgame": 3,
                   "buyer_prior_k": 2.0,    # Beta pseudocount for the prior-anchored buyer cold-start (a0=k·p, b0=k·(1-p))
                   "buyer_frec": 0,         # 1 = free-channel buyer: buy iff recommend-rate f_rec <= p·r (fixes LEAK-cell trap)
                   "buyer_explore_rounds": 2, "buyer_frec_slack": 0.0,
                   "pers_kg": 0,            # 1 = KG-optimal seller: push lows at q*·pers_kg_cap (wires _pers_kg_push)
                   "pers_kg_cap": 0.85,     # credibility margin: push slightly below q* so a statistical buyer keeps buying
                   "buyer_hedge_skip": 1,   # 1 = treat a HEDGED seller 'buy' as hold — PRIOR-GATED (only when p<pers_hedge_pmax)
                   "pers_hedge_pmax": 0.6,  # hedge-skip only applies below this prior p (at high p a hedged msg is mostly high)
                   "buyer_neutral_penalty": 0.10,  # subtract from posterior on UNPARSED "neutral" msgs (P(hi|neutral)≈p-0.1)
                   "buyer_ev_floor": 0,     # 1 = floor pm at prior p on 'buy' signals in prior-+EV cells (stop irrational over-refusal)
                   "buyer_liar_n": 5,       # min bought-round evidence before a proven-liar overrides the +EV floor
                   "buyer_text_clf": 0,     # 1 = buyer reads seller message via offline text-classifier
                   #   (tw/textclf.py + logs/buyer_textclf.json) -> calibrated P(high); replaces keyword+penalty
                   "pers_frec_target": 0},  # >0 = seller pushes lows until cumulative recommend-rate hits this
    #   committed target (replaces the KG q* coin; offer-side, shadow-only). 0 = off (use pers_kg/honest_p path)
}

# Bounds keyed by dotted path — the optimizer's proposals are clamped to these.
BOUNDS = {
    "grab.over_conceder": (0.2, 0.95), "grab.hard_anchorer": (0.2, 0.95),
    "grab.tit_for_tat": (0.2, 0.95), "grab.unknown": (0.2, 0.95),
    "barg_delta_coeff": (0.30, 0.90),
    "barg_arch_bump.over_conceder": (-0.1, 0.2), "barg_arch_bump.hard_anchorer": (-0.1, 0.2),
    "barg_arch_bump.tit_for_tat": (-0.1, 0.2), "barg_arch_bump.unknown": (-0.1, 0.2),
    "barg_offer_bump": (0.0, 0.3), "barg_share_cap": (0.5, 0.9), "barg_accept_floor": (0.3, 0.55), "barg_patience_k": (0.0, 3.0),
    "neg_keep_coeff": (0.05, 0.6), "neg_accept_keep": (0.0, 0.10), "neg_keep_floor": (0.0, 0.2),
    "neg_meet_frac": (0.0, 1.0), "neg_conc_alpha": (1.0, 5.0), "neg_conc_alpha_known": (1.0, 5.0),
    "neg_hz_keep": (0.0, 0.15), "neg_hz_patience": (1, 5),
    "classify.conc_over": (0.2, 0.9), "classify.conc_hard": (0.02, 0.3), "classify.gen_hard": (0.1, 0.6),
    "persuasion.sell_low_bias": (0.0, 1.0), "persuasion.buyer_skepticism": (0.0, 0.5),
    "persuasion.pers_low_push_unknown": (0.0, 1.0), "persuasion.buyer_margin": (0.0, 0.5),
}


_EXTRA_PATH = Path("logs/knobs_extra.json")


def _load_extras():
    """Knobs the optimizer added itself (new 'groups'): {name: {default, bounds:[lo,hi], family}}.
    Kept out of source so the loop can register a knob without a code edit."""
    if _EXTRA_PATH.exists():
        try:
            return json.loads(_EXTRA_PATH.read_text())
        except Exception:
            pass
    return {}


def register_knob(name, default, bounds, family):
    """Add a new tunable knob at runtime (top-level). Persists to knobs_extra.json and wires it into
    DEFAULTS/BOUNDS/KNOB_FAMILY so both the proposer and clamp see it immediately."""
    extras = _load_extras()
    extras[name] = {"default": default, "bounds": list(bounds), "family": family}
    _EXTRA_PATH.parent.mkdir(exist_ok=True)
    _EXTRA_PATH.write_text(json.dumps(extras, indent=2))
    DEFAULTS.setdefault(name, default)
    BOUNDS[name] = tuple(bounds)
    KNOB_FAMILY[name] = family
    reload()
    return name


def _merge(a, b):
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(a[k], v) if isinstance(v, dict) and isinstance(a.get(k), dict) else v
    return out


def load():
    """Read params.json merged over DEFAULTS (so new knobs get defaults)."""
    if _PATH.exists():
        try:
            return _merge(DEFAULTS, json.loads(_PATH.read_text()))
        except Exception:
            pass
    return _merge(DEFAULTS, {})


P = load()                                     # read as params.P[...] at call time (not `from ... import P`)


def save(p):
    """Persist a params dict to params.json."""
    _PATH.write_text(json.dumps(p, indent=2))


def reload():
    """Re-read params.json in place so live modules see the new values."""
    P.clear()
    P.update(load())
    return P


def get(path, default=None):
    """Read a value by dotted path, e.g. get('grab.unknown')."""
    node = P
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


KNOB_FAMILY = {
    "grab": "negotiation", "neg_keep_coeff": "negotiation", "neg_accept_keep": "negotiation", "neg_keep_floor": "negotiation",
    "neg_meet_frac": "negotiation", "neg_hz_keep": "negotiation", "neg_hz_patience": "negotiation",
    "barg_delta_coeff": "bargaining", "barg_arch_bump": "bargaining",
    "barg_offer_bump": "bargaining", "barg_share_cap": "bargaining", "barg_accept_floor": "bargaining", "barg_patience_k": "bargaining",
    "classify": "shared_nb",             # archetype thresholds affect negotiation AND bargaining
    "persuasion": "persuasion",
}


def knobs_for(families):
    """Dotted knob keys whose family is being trained (causal scoping)."""
    fams = set(families)
    out = []
    for key in BOUNDS:
        fam = KNOB_FAMILY.get(key.split(".")[0])
        if fam == "shared_nb":
            if "negotiation" in fams or "bargaining" in fams:
                out.append(key)
        elif fam in fams:
            out.append(key)
    return out


def clamp_path(path, value):
    """Clamp a proposed value to the bound registered for its dotted path."""
    lo, hi = BOUNDS.get(path, (None, None))
    if lo is None:
        return value
    return max(lo, min(hi, value))


for _name, _spec in _load_extras().items():             # fold AI-registered knobs in at import
    DEFAULTS.setdefault(_name, _spec["default"])
    BOUNDS[_name] = tuple(_spec["bounds"])
    KNOB_FAMILY[_name] = _spec["family"]
reload()
