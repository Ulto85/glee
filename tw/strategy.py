"""Strategy: the strategy(game) entrypoint — observe, classify, best-respond, remember, log."""

import os

from tw import memory, features, archetypes, policy, text, llm
import tw.params as params


def _neg_v2():
    """V2 = the mid-game CONCESSION engine (meet the opponent partway via Boulware, vs snapping counters to
    reservation). Fable#13: it was DEAD on every farm (gated on GLEE_NEG_V2 env, never set) — our counters
    froze at 1.09x/0.91x, opponents conceded then STALLED, 36-55% no-deal. The A/B that 'killed' it was n=14,
    pooled, under PRE-F4 WIN-CENSORING (which dropped exactly v2's opponent-accepted wins) and its own verdict
    was 'inconclusive'. So concession was NEVER validly tested. Re-gate on a PARAMS flag (neg_meet_enable) so a
    shadow can carry it; the env var still force-overrides. NOTE: accept-side-neutral (only touches counters)."""
    return os.environ.get("GLEE_NEG_V2", "0") == "1" or bool(params.P.get("neg_meet_enable"))


def _goodness(family, state, me):
    """Score the opponent's current offer from our side (higher = better for us)."""
    if family == "negotiation":
        price = state["last_offer"]["price"]
        return (price - state[f"{me}_value"]) if state[f"{me}_role"] == "seller" \
            else (state[f"{me}_value"] - price)
    if family == "bargaining":
        return state["last_offer"][f"{me}_gain"]
    return 0.0


def _scale(family, state):
    """A rough magnitude for the game, used to normalize concession speed."""
    if family == "bargaining":
        return float(state.get("money_to_divide", 1) or 1)
    # NEG BUGFIX (gated neg_scale_fix, Fable audit 2026-08-17): `product_price_order` does NOT exist in
    # negotiation states (verified 0/400) so this ALWAYS returned 100, saturating classify()'s concession/
    # generosity features for the ~89% of games with value>=100 (values span 10^1-10^6). That mislabeled
    # ~27% of opponents (142/143 over_conceder spurious) -> we countered FIRMEST vs non-folders. Fix:
    # normalize by the current player's value magnitude. Gated so carriers (no flag) keep the proven code.
    if params.P.get("neg_scale_fix"):
        me = state.get("current_player")
        v = state.get(f"{me}_value") if me else None
        if isinstance(v, (int, float)) and abs(v) > 0:
            return float(abs(v))
    return float(state.get("product_price_order", 100) or 100)


def strategy(game):
    """Infer the opponent's archetype and play the best response for that type."""
    family = game["game_family"]
    actions = game["valid_actions"]
    state = game["game_state"]
    me = state.get("current_player") or game.get("your_player")
    game_id = game["game_id"]
    opp = game.get("opponent") or {}
    name = opp.get("name") if opp.get("type") in ("agent", "human") else None

    action, arch, conf = {}, "unknown", 0.0
    try:
        if family in ("bargaining", "negotiation") and actions["type"] == "decision":
            memory.record_offer(game_id, _goodness(family, state, me))

        if family == "persuasion":
            action = policy.persuasion(actions, state, game_id=game_id)
        else:
            f = features.trajectory_features(memory.trace(game_id), _scale(family, state))
            arch, conf = archetypes.classify(f)
            prior = memory.profile_for(name, family)          # seed from past encounters
            if prior and prior["seen"] >= 1 and conf < prior["confidence"]:
                arch, conf = prior["archetype"], prior["confidence"]
            t_arch, t_conf = text.classify_text(text.opponent_message(state, me))
            if t_conf > conf:                                 # message types them faster than offers do
                arch, conf = t_arch, t_conf
            if family == "negotiation":
                msg_nums = text.extract_numbers(text.opponent_message(state, me))
                # Build a belief when concede-to-close (or legacy GLEE_BELIEF) is on; belief does NOT
                # auto-enable the timid full-BBR (gated behind neg_bbr_replace in policy.negotiation).
                use_belief = bool(params.P.get("neg_close_enable")) or bool(params.P.get("neg_resv")) \
                    or bool(os.environ.get("GLEE_BELIEF"))
                b = None
                if use_belief:
                    role = state.get(f"{me}_role")
                    scale = state.get("product_price_order") or 100
                    b = memory.belief(game_id, scale, opp_is_seller=(role == "buyer"))
                    lo = state.get("last_offer") or {}
                    if actions.get("type") == "decision" and isinstance(lo.get("price"), (int, float)):
                        b.update_offer(lo["price"])
                    for num in msg_nums:
                        if 0 < num < scale * 3:               # ignore wild numbers
                            b.update_message_number(num)
                action = policy.negotiation(actions, state, me, arch, belief=b, v2=_neg_v2(), msg_nums=msg_nums, name=name, trace=memory.trace(game_id))
            else:
                action = policy.bargaining(actions, state, me, arch, name=name)
            memory.update_profile(name, family, arch, conf)
    except Exception as exc:
        action, arch = _fallback(family, actions, state, me), f"error:{exc}"

    # LLM hybrid override (gated): LLM reads opponent + suggests; deterministic action is the fallback.
    if family in ("negotiation", "bargaining", "persuasion") and llm.enabled(family) \
            and isinstance(action, dict) and actions.get("type") in (
                "offer", "decision", "seller_message", "seller_recommendation", "buyer_decision") \
            and (family != "negotiation" or llm.neg_scope_ok(state)):     # frugal+safe: LLM only where it beats deterministic
        try:
            action = llm.llm_move(family, actions, state, me, action, text.opponent_message(state, me), name=name)
        except Exception:
            pass

    if not isinstance(action, dict) or not action:
        action = _fallback(family, actions, state, me)

    # LLM-PERS WIRING BUGFIX (2026-08-26): when persuasion messaging is LLM-driven, policy.py already produced
    # the FINAL LLM message (with the correct buy/hold signal from det_msg). The bank-swap below keys "high" only
    # if message==_BUY_MSG — never true for LLM text — so it MIS-KEYED every pushed-high to the low anti-sell bank
    # line, and the LLM layer at the bottom then re-inferred HOLD from that and wrote "don't buy" (99.8% anti-buy
    # on high-quality rounds; ~5σ pers loss + 2x cap burn). Skip the bank-swap for LLM-pers so the correct message
    # survives; the bottom LLM layer is likewise skipped for persuasion (policy is the single LLM source).
    _llm_pers = (family == "persuasion" and llm.message_enabled("persuasion"))
    if isinstance(action, dict) and action.get("message") is not None and not _llm_pers:  # swap in a bank message
        try:
            # BUGFIX (Fable#8, gated pers_msg_fix): key persuasion by the seller's PUSH DECISION, not true
            # quality. Keying by _quality swapped a PUSHED low (action msg=_BUY_MSG) for a 'low'-bank ANTI-SELL
            # message, forcing the seller fully honest in ~49% of games (text path; binary escapes, no message).
            # This half-unplugged the KG seller and made every seller lever wash. Fixed=route by decision.
            if family == "negotiation":
                key = state.get(f"{me}_role", "any")
            elif family == "persuasion":
                if params.P.get("persuasion", {}).get("pers_msg_fix"):
                    key = "high" if action.get("message") == policy._BUY_MSG else "low"
                else:
                    key = policy._quality(state)                  # OLD (buggy) default until A/B-validated
            else:
                key = "any"
            msg = text.pick_message(family, key, state.get("round", 1))
            if msg:
                action["message"] = msg
        except Exception:
            pass

    # NEG VOICE (gated neg_voice, Fable 2026-08-17): ~95% of our neg reject+counters go out MUTE (no
    # "message" key), while the field's top neg agents talk credible-commitment text EVERY turn. Attach a
    # deterministic line embedding OUR counter price so number-parsing opponents can close AT our number.
    # Numbers stay 100% deterministic; the deadline tier fires only when the schedule is genuinely endgame
    # (behavior-backed, never a detectable bluff). $0 channel-alive test that gates the LLM-rhetoric bet.
    if family == "negotiation" and params.P.get("neg_voice") and isinstance(action, dict) \
            and action.get("decision") == "RejectOffer" \
            and isinstance(action.get("product_price"), (int, float)):
        _pr = action["product_price"]
        _seller = state.get(f"{me}_role") == "seller"
        _msg = (f"I can do {_pr}. Below that I'd rather hold onto it."
                if _seller else f"I can go to {_pr}. Above that it stops working for me.")
        _mr, _rd = state.get("max_rounds"), state.get("round", 1)
        if isinstance(_mr, int) and isinstance(_rd, int) and _rd >= _mr - 1:
            _msg += " This is my last round to make a deal."
        action["message"] = _msg

    # LLM MESSAGE layer (gated GLEE_LLM_MSG): rewrite ONLY the message (numbers stay deterministic),
    # short timeout + bank-message fallback. The correctly-scoped LLM use — language, not numbers.
    if isinstance(action, dict) and action.get("message") is not None and llm.message_enabled(family) \
            and family != "persuasion":   # persuasion LLM message is produced ONCE in policy.py; skip here to avoid the double-call + re-clobber (wiring bugfix 2026-08-26)
        try:
            action["message"] = llm.llm_message(family, state, me, action, text.opponent_message(state, me))
        except Exception:
            pass

    memory.log_turn({
        "game_id": game_id, "family": family, "opponent": name,
        "phase": game.get("phase"), "archetype": arch, "confidence": conf,
        "action": action, "state": state,
    })
    return action


def _fallback(family, actions, state, me):
    """A guaranteed-legal action so a turn never becomes an abandoned (5th-percentile) game."""
    t = actions.get("type")
    if family == "bargaining":
        if t == "offer":
            half = state.get("money_to_divide", 100) / 2
            return {"alice_gain": half, "bob_gain": half, "message": "Even split."}
        return {"decision": "accept"}
    if family == "negotiation":
        if t == "offer":
            return {"product_price": state.get(f"{me}_value", 50), "message": "Let's deal."}
        return {"decision": "AcceptOffer"}
    if family == "persuasion":
        return {"message": "Fair unit this round."} if t == "seller_message" \
            else {"decision": "no"}
    return {}
