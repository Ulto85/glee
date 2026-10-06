"""Policy: given the archetype, produce a concrete legal action. All knobs come from params."""

import hashlib
import os
import random

import tw.params as params
from tw import exploit
from tw import llm
from tw.archetypes import grab


def _exploiting():
    """Meta-game exploiter: squeeze recognized opponent types to their tolerance. GLEE_EXPLOIT=1."""
    return os.environ.get("GLEE_EXPLOIT") == "1"


def _progress(state, default_h=10, soft=8):
    """0..~0.9 fraction of the game elapsed — drives how much we concede over time."""
    rounds = state.get("round", 1)
    if state.get("horizon_known", True):
        h = state.get("max_rounds") or default_h
        return min(0.9, (rounds - 1) / max(1, h - 1))
    return min(0.6, (rounds - 1) / soft)


def _neg_M(v):
    """Scale M of this game, recovered from own value (values are F*M, F in {0.8,1,1.2,1.5},
    M in {1e2,1e4,1e6}; the decomposition is unique so M is exact from own value alone).
    Returns None on an unexpected value -> caller falls through to legacy policy (never guess scale)."""
    for s in (1e2, 1e4, 1e6):
        r = v / s
        for f in (0.8, 1.0, 1.2, 1.5):
            if abs(r - f) < 1e-6:
                return s
    return None


def _msg_reservation_counter(counter, is_seller, my_value, offer_price, msg_nums, scale, eps):
    """Bluff-robust use of a number the opponent STATED in their message. Opponents bluff in the
    direction that helps THEM, so a stated number is only credible when it concedes room to US relative
    to their standing offer. We use it ONLY to extract MORE than their current offer, never to concede."""
    valid = [x for x in (msg_nums or []) if 0 < x < scale * 3]
    if not valid:
        return counter
    if is_seller:                                     # buyer revealed they can pay up to X (> their offer)
        cands = [x for x in valid if x > offer_price and x > my_value]
        if cands:
            return max(counter, max(cands) * (1 - eps))   # price just under their revealed max
    else:                                             # seller revealed a floor X (< their ask)
        cands = [x for x in valid if x < offer_price and x < my_value]
        if cands:
            return min(counter, min(cands) * (1 + eps))   # price just over their revealed floor
    return counter


_NEG_DOSSIER = None


def _neg_cross_rate(name, is_seller):
    """Opponent's historical rate of crossing our value (offering above our cost as seller / below our
    value as buyer), from the offline dossier. High => they pay, hold firm; low => they won't, don't over-hold."""
    global _NEG_DOSSIER
    if _NEG_DOSSIER is None:
        try:
            import json
            from pathlib import Path
            _NEG_DOSSIER = json.loads(Path("logs/neg_dossier.json").read_text())
        except Exception:
            _NEG_DOSSIER = {}
    rec = _NEG_DOSSIER.get(name or "")
    if not rec:
        return None
    return rec.get("as_seller_opp_crosses" if is_seller else "as_buyer_opp_crosses")


def _ci_release_arm(mv, ov):
    """Deterministic, log-recoverable within-agent A/B coin from the (visible, per-game-constant) CI values.
    arm 0 = release (treatment), arm 1 = hold 0.98 (control). Used by neg_ci_release_split for a clean A/B."""
    return (int(mv) * 1000003 + int(ov)) % 2


def negotiation(actions, state, me, arch, belief=None, v2=True, msg_nums=None, name=None, trace=None):
    """LLM-HYBRID WRAPPER (gated GLEE_LLM=negotiation): compute the deterministic action, then — ONLY in
    incomplete-info, multi-round games (neg_scope_ok), where reading the opponent's concession path to infer
    its reservation value is the whole edge our deterministic policy lacks — let the LLM override, with the
    deterministic action as fallback. llm_move refuses losing accepts + clamps counters to sane bounds and
    falls back to `det` on ANY parse issue or budget exhaustion. Flag OFF => returns `det` bit-identically
    (zero risk to the deterministic path). This is the one untested avenue to break our neg ~2100 ceiling
    toward the top accounts' 2600-2730 (they read language; our rules can't)."""
    det = _negotiation_det(actions, state, me, arch, belief=belief, v2=v2, msg_nums=msg_nums, name=name, trace=trace)
    # DECISION turns only: the accept-vs-hold read (take the bird-in-hand before they walk, or hold + re-counter)
    # is where the LLM's opponent-reading beats our rules; our OPENING anchor/glide + messages are already tuned,
    # so leave offer-phase turns deterministic (also ~halves call cost).
    if llm.enabled("negotiation") and actions.get("type") == "decision" and llm.neg_scope_ok(state):
        opp_msg = (state.get("last_offer") or {}).get("message", "") or ""
        return llm.llm_move("negotiation", actions, state, me, det, opp_msg=opp_msg, name=name)
    return det


def _negotiation_det(actions, state, me, arch, belief=None, v2=True, msg_nums=None, name=None, trace=None):
    """Hold a firm minimum surplus, but never over-hold: under an UNKNOWN horizon the opponent can
    quit any round, so a profitable offer in hand beats holding out (V2). Counters MEET the opponent
    as the game runs on rather than snapping to our reservation. If a belief over the opponent's
    value is informed, price at the expected-surplus-maximizing point (BBR) instead."""
    P = params.P
    role = state[f"{me}_role"]
    my_value = state[f"{me}_value"]
    g = grab(arch)
    prog = _progress(state)
    is_seller = role == "seller"
    known = state.get("horizon_known", True)
    rounds = state.get("round", 1)
    last_round = known and rounds >= (state.get("max_rounds") or 10)

    # STALL-CONDITIONED HOLD (Fable transcript audit, gated neg_stall_hold): the field's winning neg agents
    # (NegoMind, Poseidon, HAWK) beat us by REFUSING to accept while WE'RE still being conceded to — we book
    # share 0.09-0.36 by grabbing the opponent's 1st-2nd counter at rd 3-4 while they glide 6-14%/step with
    # runway left; ridden to their endpoint the SAME deals land at 0.89-0.94. This is the exact complement of
    # neg_walk_accept (which ACCEPTS once the opponent STALLS): here we HOLD our counter while the opponent is
    # STILL CONCEDING and rounds remain, suppressing ONLY the early bird-in-hand accepts (CI hz_take/ci_known
    # and the thin akeep) — never the real-target `good` accept, the last-round accept, or the wire. `trace`
    # is our offer-goodness (higher=better for us) at each decision, so a rising last step = opponent conceded.
    # Flat-holders (the no-deal bots) stall by rd 2-3, so our behavior vs them is UNCHANGED; only mid-glide
    # accepts get delayed. Accept-side -> replay-validatable. maxwait bounds h0 walk risk. theta-only until proven.
    _rem = ((state.get("max_rounds") or 10) - rounds) if known else 999
    _still_conceding = bool(trace) and len(trace) >= 2 and \
        (trace[-1] - trace[-2]) > P.get("neg_stall_eps", 0.01) * max(1.0, my_value)
    _room = (not last_round) and (_rem >= 2 if known else rounds < P.get("neg_stall_maxwait", 30))
    stall_hold = bool(P.get("neg_stall_hold")) and _still_conceding and _room

    # --- Complete information: both valuations visible -> exact best-response (audit P1) ---
    if state.get("complete_information"):
        opp = "player_1" if me == "player_2" else "player_2"
        ov = state.get(f"{opp}_value")
        if ov is not None:
            eps = 0.02
            if is_seller:
                target = max(my_value, ov * (1 - eps))     # price just under buyer's value; never below cost
            else:
                target = min(my_value, ov * (1 + eps))     # offer just over seller's cost; never above value
            # CI SELLER RELEASE (Fable neg audit 2026-08-19, gated neg_ci_release): player_1 is always seller and
            # the BUYER makes the r10 (final) offer -> a fixed last-mover edge. Holding 0.98 for 9 rounds wire-folds
            # us to ~0.21 in 89% of CI-seller games (only 2% of buyers fold to 0.98); the field's winners (doodod)
            # jump to the MIDPOINT and our own fair-share accept closes 41% of buyers at ~0.51. So RELEASE only the
            # OFFER/counter (NOT `good`/accept — we still take wire profit at r10, so downside == today) toward
            # midpoint late-game: mr10 known @prog>=0.6 -> share ~0.55; h0 @rounds>=12 -> ~0.50. CI => zero info leak
            # (buyer already sees our cost). Within-agent game-hash arm split (neg_ci_release_split) for a clean A/B.
            offer_target = target
            if is_seller and P.get("neg_ci_release") and (not P.get("neg_ci_release_split")
                    or _ci_release_arm(my_value, ov) == 0):
                _rel = None
                if known and int(state.get("max_rounds") or 0) == 10 and prog >= P.get("neg_ci_release_prog", 0.6):
                    _rel = P.get("neg_ci_release_share_h1", 0.55)
                elif (not known) and rounds >= P.get("neg_ci_release_h0round", 12):
                    _rel = P.get("neg_ci_release_share_h0", 0.50)
                if _rel is not None:
                    offer_target = max(my_value, min(target, my_value + _rel * (ov - my_value)))
            if actions["type"] == "offer":
                return {"product_price": round(offer_target, 2),
                        "message": "Premium unit." if is_seller else "My budget."}
            op = state["last_offer"]["price"]
            good = (op >= target) if is_seller else (op <= target)
            profit = (op > my_value) if is_seller else (op < my_value)
            # HORIZON-AWARE CI ACCEPT (audit): CI games are ~always winnable (432/433 have a ZOPA), but
            # demanding ~98% of the ZOPA every round with no horizon awareness grinds them into no-deals —
            # in UNKNOWN-horizon CI, last_round is never True, so we re-demand 98% forever and the opponent
            # walks. Take the profitable bird-in-hand past the patience round instead (converts ~116 CI
            # no-deals -> deals; cost on already-closed games ~-0.007). Unbiased under replay (offers logged).
            # Bird-in-hand for CI: unknown-horizon (neg_ci_hz_accept) OR known-horizon (neg_ci_known_accept).
            # KNOWN-horizon CI was grinding winnable deals into no-deals — we demand ~98% of the ZOPA every
            # round and only relent at last_round, so buyer|h1 had 1 accept vs 156 rejects. Take a profitable
            # offer past the patience round regardless of horizon. Accept-side -> replay-validatable (unbiased).
            # Known-horizon: only take the bird-in-hand LATE (prog >= neg_ci_known_prog) — early on we can
            # still hold for a better offer since the opponent can't quit before the known horizon ends.
            # Fable#9b: BUYER-only by default. Replay (n=971 CI-known) showed the buyer converts 45% of
            # no-deals -> deals (0 -> +40k avg, only 11/642 worse-earlier — monotone percentile win), but the
            # SELLER trades 121/329 already-won deals down for 86 conversions (capitulates below target at
            # prog>=0.6). The known-horizon grind is a buyer leak; keep sellers holding unless neg_ci_known_seller.
            ci_known_ok = P.get("neg_ci_known_accept") and prog >= P.get("neg_ci_known_prog", 0.6) \
                and (not is_seller or P.get("neg_ci_known_seller"))
            hz_take = P.get("neg_ci_hz_accept") and rounds >= P.get("neg_hz_patience", 2) and \
                (not known or ci_known_ok) and not stall_hold      # hold the bird-in-hand while they still concede
            # CI FAIR-SHARE FLOOR (Fable hostile neg audit, gated neg_ci_fairshare): the bird-in-hand accepts
            # (hz_take/ci_known) took ANY profitable offer -> CI-h0 accepts booked a median 0.22-0.28 of the
            # ZOPA (95% under half the pie, 39-41% grabbed the opponent's FIRST offer) while the field books
            # 0.43-0.70. The earlier washed fixes never set a BAR (keep_floor=static counter; stall_hold=accept
            # delay). Set a hard floor on ACCEPTED share of the visible ZOPA: 0.5 until prog>=neg_fs_prog, then
            # decay 0.5->0.3 to the wire; last round still takes any profit. `good` (crosses our 0.98 target)
            # and the last-round accept are untouched. Accept-side, replay-validatable.
            take_bird = hz_take and profit
            if take_bird and P.get("neg_ci_fairshare") and not last_round:
                denom = (ov - my_value) if is_seller else (my_value - ov)
                share = (((op - my_value) if is_seller else (my_value - op)) / denom) if denom > 0 else 1.0
                fsp = P.get("neg_fs_prog", 0.7)
                floor = 0.5 if prog < fsp else max(0.3, 0.5 - 0.2 * (prog - fsp) / max(1e-6, 1.0 - fsp))
                # H0 FLOOR-PIN FIX (Opus audit 2026-08-16, gated neg_h0_floor_decay, LAB trial): in hidden-horizon
                # _progress caps at 0.6 < fsp=0.7, so the 0.5->0.3 decay above NEVER fires — the floor is pinned at
                # 0.5 forever, blocking late-crossing convertible no-deals (true CI-h0 no-deal 56-62%). Drive the
                # decay by ROUND instead (h0-only; h1 untouched): hold 0.5 until r10, then decay to 0.30 by ~r20.
                if P.get("neg_h0_floor_decay") and not known:
                    floor = 0.5 if rounds < 10 else max(0.30, 0.50 - 0.02 * (rounds - 10))
                if share < floor:
                    take_bird = False                          # hold + re-counter at target; don't fold the ZOPA
            # R9 FOLD-AWARE ACCEPT (Fable opponent-audit F5, gated neg_r9_foldaware): as BUYER in a known-horizon
            # game our TRUE last accept is round (max_rounds-1) — a round-max_rounds counter is DEAD (no reply
            # round for the seller). `last_round` (policy.py:102) is seat-blind (fires only at ==max_rounds), so
            # at r9 the fair-share floor rejects a positive offer and we gamble a dead r10 counter that converts
            # only ~6-12% vs NEVER-FOLDERS. Data (167/221 r9 CI candidates are never-folders @ mean share 0.249;
            # gamble EV ~0.09): accept locks +0.16 share. Fire ONLY buyer+CI+known+round==max_rounds-1+profitable
            # +share>=neg_r9_minshare+opponent last-step concession<=neg_r9_conc_eps (never-folder). FOLDERS (where
            # gambling wins) EXCLUDED by the concession gate. Accept-side, bounded (only turns reject->accept).
            if P.get("neg_r9_foldaware") and (not is_seller) and known and profit \
                    and rounds == (state.get("max_rounds") or 10) - 1 and not (good or take_bird):
                _dn = (my_value - op)
                _dz = (my_value - ov)
                _sh = (_dn / _dz) if _dz > 0 else 1.0
                if _sh >= P.get("neg_r9_minshare", 0.15):
                    _os = "player_2" if me == "player_1" else "player_1"
                    _sp = [h["offer"]["price"] for h in (state.get("history") or [])
                           if isinstance(h.get("offer"), dict) and h["offer"].get("from_player") == _os
                           and isinstance(h["offer"].get("price"), (int, float))]
                    if len(_sp) >= 2 and _sp[-2] and (_sp[-2] - _sp[-1]) / abs(_sp[-2]) <= P.get("neg_r9_conc_eps", 0.02):
                        return {"decision": "AcceptOffer"}   # never-folder + positive share at our real last round
            # H0 TERMINAL ACCEPT (neg no-deal hunt 2026-08-26, gated neg_h0_terminal_accept): in unknown-horizon CI
            # where the opponent flatlines 50+ rounds and NEVER caves, our fair-share floor holds our counter until
            # the hidden ~r98 cap -> we book a 0 despite a profitable offer standing (61/341 h0 no-deals, +0.083x
            # value left). Take any profit at r>=neg_h0_term_round (near the cap). MECHANICALLY DISTINCT from the
            # DEAD neg_h0_floor_decay (that relaxed the MID-game floor -> forfeited caves, -305): this fires only at
            # the terminal round where the opponent has already flatlined 90+ rounds (no cave window left; downside
            # -2-4 modeled). Accept-side: converts 0 -> positive, CANNOT create a no-deal. LIVE A/B (eta neg).
            _h0_term = P.get("neg_h0_terminal_accept") and (not known) \
                and rounds >= P.get("neg_h0_term_round", 96) and profit
            if good or (last_round and profit) or take_bird or _h0_term:
                return {"decision": "AcceptOffer"}
            return {"decision": "RejectOffer", "product_price": round(offer_target, 2)}

    # --- NEG GRANITE ARM (Fable 2026-08-23 crack-top-neg #15; delta lab only, gated neg_granite): clone of the
    # top-board commitment schedule (Clod 2608 / gill 2585 / opus5 clones 2448-2525 @13-27k games). Constants
    # anchor to the game SCALE M (values are F*M, F in {0.8,1,1.2,1.5}) — NOT to own value. ci=F only (the ci=T
    # block above already best-responds + returned). mr1 buyer falls through. Thesis: the field ladder is soft
    # enough that flat-hold-high (seller) / flat-grind-low (buyer) harvests it → neg spike; our own soft climb-
    # and-fold was the endogenous reason prior audits called neg "structural". Either +250-450 or a cheap kill.
    if P.get("neg_granite") and not state.get("complete_information"):
        _M = _neg_M(my_value)
        _mr = state.get("max_rounds") or (10 if known else 99)
        if _M is not None and not (known and _mr == 1 and not is_seller) \
                and (not (known and _mr == 1) or P.get("neg_gr_mr1", 1)):
            _t = max(0, (rounds - 1) // 2)                     # own-offer index (p1 is always seller)
            _wire = last_round or ((not known) and rounds >= P.get("neg_gr_h0_late", 90))
            if is_seller:
                _step = P.get("neg_gr_step_h1", 0.023) if known else P.get("neg_gr_step_h0", 0.005)
                ask = max(P.get("neg_gr_floor", 1.25) * _M,
                          P.get("neg_gr_open", 1.485) * _M - _step * _M * _t)
                ask = max(ask, my_value * 1.02)                # never price below cost+2%
                if actions["type"] == "offer":
                    return {"product_price": round(ask, 2), "message": "Premium unit."}
                op = state["last_offer"]["price"]
                floor_ok = op >= my_value * (1 + P.get("neg_gr_keep", 0.02))
                if floor_ok and (_wire or op >= max(my_value * 1.02, P.get("neg_gr_sell_acc", 1.0) * _M)):
                    return {"decision": "AcceptOffer"}
                return {"decision": "RejectOffer", "product_price": round(ask, 2)}
            else:
                bid = min(P.get("neg_gr_bid", 0.808) * _M + P.get("neg_gr_bid_creep", 0.0024) * _M * _t,
                          P.get("neg_gr_bid_cap", 0.85) * _M)
                bid = min(bid, my_value * 0.98)                # never bid above 98% of own value
                if actions["type"] == "offer":
                    return {"product_price": round(bid, 2), "message": "My budget."}
                op = state["last_offer"]["price"]
                cap = P.get("neg_gr_buy_wire", 1.10) * _M if _wire else \
                    min(my_value - P.get("neg_gr_margin", 0.05) * _M, P.get("neg_gr_buy_acc", 1.0) * _M)
                if op < my_value * 0.98 and op <= cap:
                    return {"decision": "AcceptOffer"}
                return {"decision": "RejectOffer", "product_price": round(bid, 2)}

    # --- Belief-Based Best-Response: GATED behind neg_bbr_replace (default OFF) — EV-max best_price shades
    # LOW and lost a prior A/B; we pass `belief` mainly for the conditional concede-to-close below. ---
    if P.get("neg_bbr_replace") and belief is not None and belief.n_evidence >= 2:
        target = belief.best_price(my_value, is_seller)
        if target is not None:
            if actions["type"] == "offer":
                return {"product_price": round(target, 2), "message": "Premium unit." if is_seller else "My budget."}
            offer_price = state["last_offer"]["price"]
            good = (offer_price >= target) if is_seller else (offer_price <= target)
            profitable = (offer_price > my_value) if is_seller else (offer_price < my_value)
            if good or (last_round and profitable):
                return {"decision": "AcceptOffer"}
            return {"decision": "RejectOffer", "product_price": round(target, 2)}

    # --- keep threshold: how much surplus margin we insist on before accepting ---
    if v2 and not known and rounds >= P.get("neg_hz_patience", 2):
        keep = P.get("neg_hz_keep", 0.04)                      # unknown horizon, past patience: take the bird in hand
    else:
        keep = max(P["neg_keep_floor"], P["neg_keep_coeff"] * g * (1 - prog))
    # CONCESSION-BUDGET CAP (neg ZOPA-extraction crack 2026-08-26, gated neg_conc_cap): the field FOLD-HARVESTS
    # gradual concession — when WE grind >15% of range our close-rate collapses to 13% / share 0.33, but when we
    # HOLD (<5%) we close 70% / share 0.53 (n>100/stratum; crucially the no-deal downside does NOT reproduce —
    # holding closes MORE deals, opposite of the old indiscriminate-squeeze failure, because conceding SIGNALS
    # weakness and the field exploits it). So cap how far `keep` may decay below our OPEN unless the opponent is
    # RECIPROCATING (_still_conceding, computed above from their offer trajectory). Offer-side (opp response is
    # counterfactual in replay) -> LIVE A/B only; the +~20pp share is an optimistic ceiling. last_round below still
    # zeroes keep (takes any final profit) so this never turns a takeable endgame into a no-deal.
    if P.get("neg_conc_cap") and not _still_conceding:
        _open_keep = max(P["neg_keep_floor"], P["neg_keep_coeff"] * g)
        keep = max(keep, _open_keep - P.get("neg_conc_cap_frac", 0.12))
    # NOTE: negotiation exploiter disabled — the trusted sim showed flat-keep squeezing backfires
    # (you can't extract surplus beyond the opponent's value; it just causes no-deals). Negotiation
    # extraction belongs to reservation/belief estimation, not a flat archetype keep. Bargaining only.
    if last_round:
        keep = 0.0                                             # final round: take any positive surplus

    reservation = my_value * (1 + keep) if is_seller else my_value * (1 - keep)

    # FIELD-STYLE ANCHOR (audit 2026-08-12): our own logs show the field extracts 63-75% of surplus from us
    # WHILE closing (opp_payoff), because our floor is only ~1.09x (keep~0.09) so we settle at 1.07x/25% share.
    # The field opens HIGH (~2-3x) and glides to a HIGH floor (~1.5x). Replicate: demand a premium that glides
    # from neg_open_prem -> neg_floor_prem over the game, then HOLD the high floor and release it SMOOTHLY
    # across the last neg_anchor_endgame rounds (floor->keep), fully caving only on the actual last round.
    # A hard jump to keep near the end undoes the whole anchor (early n=1 buyer settled at 2.1% share doing
    # exactly that) — the field holds ~1.5x to the wire and still closes. Accept iff opp crosses the current
    # gliding target. Offer-side -> shadow.
    # VERDICT (paired zero-inclusive percentile, n=37): the BUYER anchor LOSES to champion (-7 to -10 pts) —
    # 61-63% no-deal sinks the percentile despite 54-78% share (share-only was misleading). A static schedule
    # can't win the share-vs-no-deal frontier; the field's high-share+low-no-deal needs opponent-conditioning
    # (#36 walk-risk / LLM). So the buyer anchor is OFF by default (neg_field_anchor_buyer); only the SELLER
    # side stays live, where reads straddled 0.5 and a firmer endgame (neg_seller_endgame_floor) may have room.
    if P.get("neg_field_anchor") and (is_seller or P.get("neg_field_anchor_buyer")):
        op_p = P.get("neg_open_prem", 1.0)
        fl_p = P.get("neg_floor_prem_seller" if is_seller else "neg_floor_prem_buyer", P.get("neg_floor_prem", 0.4))
        eg = max(1, P.get("neg_anchor_endgame", 2))
        rem = (state.get("max_rounds") or 10) - rounds if known else 99
        # SELLER endgame floor (#38): the field proves buyers cross for a firm seller (field-seller 62.6% vs our
        # 25%), so don't release all the way to keep — hold >= neg_seller_endgame_floor to the wire (except the
        # actual last round, which still takes any profitable offer via the accept branch below).
        eg_floor = P.get("neg_seller_endgame_floor", 0.0) if is_seller else 0.0
        if last_round:
            base = P.get("neg_keep_floor", 0.09)               # final offer still books a modest margin (not 0);
            #                                                     the accept branch below still takes any profitable incoming offer
        elif rem <= eg:
            frac = rem / eg                                    # rem=eg -> ~floor, rem=1 -> ~keep: smooth release
            base = max(eg_floor, keep + (fl_p - keep) * frac)  # hold the high floor, let go gradually (>= seller eg floor)
        else:
            base = fl_p + (op_p - fl_p) * (1 - prog)           # glide high-open -> high-floor
        tgt = my_value * (1 + base) if is_seller else my_value * max(0.05, 1 - base)
        if actions["type"] == "offer":
            return {"product_price": round(tgt, 2), "message": "Premium unit." if is_seller else "My budget."}
        offer_price = state["last_offer"]["price"]
        good = (offer_price >= tgt) if is_seller else (offer_price <= tgt)
        profit = (offer_price > my_value) if is_seller else (offer_price < my_value)
        if good or (last_round and profit):
            return {"decision": "AcceptOffer"}
        return {"decision": "RejectOffer", "product_price": round(tgt, 2)}

    if actions["type"] == "offer":
        openm = max(keep, 0.5 * g * (1 - prog))
        price = my_value * (1 + openm) if is_seller else max(0.0, my_value * (1 - openm))
        # INCOMPLETE-INFO ULTIMATUM reprice (Fable#5, verified): in max_rounds==1 seller ultimatums we can't
        # see the buyer value and offer median 1.30x/mean 1.36x cost (n=347), but buyer values are
        # {1.2:17%,1.25:33%,1.5:33%,1.9:16%} -> a 1.30-1.40x offer closes only ~49% (~51% no-deal). Offering
        # ~neg_ultimatum_mult(1.22x) closes ~99% (EV 0.198c vs 0.147c) and converts half these from a convex-
        # bottom 0. CI ultimatums (we already price to value, close 100%) are excluded. Seller-only for now.
        if P.get("neg_ultimatum_mult") and is_seller and not state.get("complete_information") \
                and (state.get("max_rounds") or 99) == 1:
            price = my_value * P["neg_ultimatum_mult"]
            # VALUE-AWARE ASK CAP (Fable 2026-08-23, GO +15-25 neg, refutation-survived P~0.001).
            # In the F_s=1.0 cell (cost == M), the 1.22x ask = 1.22M OVERSHOOTS the lowest viable
            # buyer tier F_b=1.2 (value 1.2M): they CANNOT rationally accept 1.22M, so we auto-reject
            # that whole tier (accept 0.284 vs 0.516 one notch down). Cap the ask at 0.99*1.2*M=1.188M
            # so those buyers can close; higher tiers still pay it. PER-CELL (only cost==1.0*M) — a flat
            # cap would bleed the already-optimal F_s=0.8 cell. Gated; default off. _utM==my_value <=> F_s=1.0.
            if P.get("neg_ult_tier"):
                _utM = _neg_M(my_value)
                if _utM is not None and abs(my_value - _utM) < 1e-3 * _utM:
                    price = min(price, 0.99 * 1.2 * _utM)
        # GUARANTEED-CLOSE endgame (audit 2026-08-13, VERIFIED): ZOPA is UNIVERSAL in GLEE neg (buyer value
        # always >=1.2x seller cost, complete-info logs 100%/1521) yet we no-deal ~42% via mutual-stubbornness
        # deadlock — in known-horizon seller no-deals we offered <= buyer value in 93% of games but our min
        # offer bottoms ~1.28x cost, above 1.2-1.25x buyers, so we never concede into a price they can take and
        # both sides hit the deadline at 0. Fix: in the last neg_close_rounds, capitulate INTO the ZOPA
        # (cost*(1+eps) seller / value*(1-eps) buyer) so a rational opponent closes — any positive surplus beats
        # their 0 (convex percentile: a deal vaults the whole no-deal mass). Known-horizon only (unknown already
        # closes via bird-in-hand, 0% no-deal). Offer-side -> farm at HIGH n (leaderboard effect < old n~40 floor).
        # BOTH ROLES, KNOWN-horizon (audit 2026-08-13, corrected via Fable#4 + complete-info verification):
        # the leak is KNOWN-horizon (h1), where no-deal is 41-75% despite 100% ZOPA — BOTH roles (seller h1
        # 54-75% is the WORST). Earlier buyer-only restriction came from field_pct.py which had a cross-role
        # config-matching BUG (v-bucket is our OWN value, so buyer|v0.8 != seller|v0.8). Unknown-horizon (h0)
        # already closes (4-19% no-deal) so leave it. never-a-loss clamp below keeps this safe in the
        # incomplete-info no-ZOPA cells (it just won't close them, correctly).
        if P.get("neg_guaranteed_close") and known:
            rem = (state.get("max_rounds") or 10) - rounds
            if rem <= P.get("neg_close_rounds", 2):
                eps = P.get("neg_close_eps", 0.05)
                price = my_value * (1 + eps) if is_seller else my_value * max(0.02, 1 - eps)
        return {"product_price": round(price, 2), "message": "Premium unit." if is_seller else "My budget."}

    if actions["type"] == "decision":
        offer_price = state["last_offer"]["price"]
        # TARGETED unknown-horizon SELLER firmness (audit 2026-08-13, per-cell finding): neg|seller|h0
        # captures only 18% share at 0% no-deal (n=360) — we grab any >2% offer to avoid the game ending,
        # but buyers ALWAYS keep offering (0% no-deal proves it), so we cave for nothing. Hold a coherent
        # F-margin (accept AND counter at (1+F)*cost) only here — seller-only (buyer|h0 is already 45%
        # no-deal, no room). Accept-side -> replay-validatable, champion-safe. Off by default (knob absent).
        if not known and is_seller and P.get("neg_hz_seller_keep"):
            F = P["neg_hz_seller_keep"]
            tgt = my_value * (1 + F)
            if offer_price >= tgt:
                return {"decision": "AcceptOffer"}
            return {"decision": "RejectOffer", "product_price": round(tgt, 2), "message": "Premium unit."}
        # BUYER SOFTPROBE (Fable #21, gated neg_softprobe): as buyer in ci=F multi-round neg, don't insta-accept a
        # cheap-but-not-great r1/r2 ask from a revealed-soft seller — counter low ONCE (neg has no time discount so
        # it's free except ~3-4% walk), then fall through to the normal akeep accept next turn (re-takes their held
        # ask for free). In-scope 2.5% of neg; validated +5-12 neg pts (dPCT +0.05-0.06/probe), positive every split.
        if P.get("neg_softprobe") and not is_seller and not state.get("complete_information") \
                and (state.get("max_rounds") or 99) > 1 and rounds <= P.get("neg_sp_maxr", 2):
            _spM = _neg_M(my_value)
            _mine_sp = [h for h in (state.get("history") or [])
                        if isinstance(h.get("offer"), dict) and h["offer"].get("from_player") == me]
            if _spM is not None and not _mine_sp and offer_price <= my_value * 0.98 \
                    and offer_price > P.get("neg_sp_lo", 0.90) * _spM:
                _sp_cnt = max(P.get("neg_sp_floor", 0.82) * _spM, offer_price - P.get("neg_sp_step", 0.25) * _spM)
                return {"decision": "RejectOffer", "product_price": round(min(_sp_cnt, my_value * 0.98), 2),
                        "message": "My budget."}
        # DECOUPLED accept (audit): take any offer with >= neg_accept_keep surplus — a profitable bird in
        # hand beats gambling into a no-deal (convex per-cell scoring: closing a winnable deal vaults over
        # the ~73% no-deal mass). Counters stay aggressive (keep-based reservation), so we don't give up
        # share on deals we'd win anyway. Fixes both known- and unknown-horizon over-holding.
        akeep = 0.0 if last_round else P.get("neg_accept_keep", 0.02)
        accept_resv = my_value * (1 + akeep) if is_seller else my_value * (1 - akeep)
        good = (offer_price >= accept_resv) if is_seller else (offer_price <= accept_resv)
        if good and not stall_hold:                            # hold the thin bird-in-hand while they still concede
            return {"decision": "AcceptOffer"}
        # WALK-RISK accept (#36, the only viable deterministic opponent-conditioning: opponents never
        # repeat — dossier has 0 seen >=3x — so read the WITHIN-GAME concession trajectory). `trace` is the
        # opponent's offer goodness (higher=better for us) at each of our decisions. If they've STALLED
        # (last gain <= eps*value) late-game AND their current offer is still PROFITABLE, take it instead of
        # holding for the 2% bar and gambling into a no-deal (=0, convex percentile hit). Against opponents
        # still conceding, we keep the firm bar. Converts walked thin-deals -> closed thin-deals. Accept-side
        # (replay-validatable), but read on theta shadow first via paired_pct.
        if P.get("neg_walk_accept") and trace and len(trace) >= 2:
            profitable = (offer_price > my_value) if is_seller else (offer_price < my_value)
            stalled = (trace[-1] - trace[-2]) <= P.get("neg_walk_stall_eps", 0.01) * max(1.0, my_value)
            late = prog >= P.get("neg_walk_late", 0.5)
            if profitable and stalled and late:
                return {"decision": "AcceptOffer"}
        if v2:                                                 # meet them partway instead of snapping to reservation
            # BOULWARE concession (audit): concede convexly (prog^alpha, alpha>1) — defend near reservation
            # then concede late — instead of linearly giving back 25% of the gap by mid-game. HORIZON-
            # CONDITIONAL (gamma read: h0 9/10 cells +30k, h1 8/20 -2k): apply convexity only in UNKNOWN-
            # horizon where it wins; keep linear (alpha_known, default 1.0) in KNOWN-horizon where it drags.
            # alpha=1.0 reproduces the old linear walk-down. Offer-side -> shadow-only.
            alpha = P.get("neg_conc_alpha", 1.0) if not known else P.get("neg_conc_alpha_known", 1.0)
            meet = P.get("neg_meet_frac", 0.5) * (prog ** alpha)
            # DOSSIER POSTURE (offer-side, shadow): against opponents who HISTORICALLY cross our value
            # (they'll pay), concede LESS to capture more of the surplus we currently give away (open 1.30x,
            # settle 1.07x). Targeted by opponent history -> avoids the blanket-aggression that sank barg_hi_open.
            if P.get("neg_opp_posture") and name:
                cr = _neg_cross_rate(name, is_seller)
                if cr is not None and cr >= P.get("neg_posture_hi", 0.35):
                    meet *= P.get("neg_posture_firm", 0.55)     # hold firmer vs proven payers
            counter = reservation + meet * (offer_price - reservation)
            counter = max(counter, my_value * 1.001) if is_seller else min(counter, my_value * 0.999)
        else:
            counter = reservation
        # OPPONENT-RESERVATION ESTIMATOR (structural fix): price relative to the opponent's INFERRED
        # reservation r̂ (from their concession path), NOT our own value+clock. Hold when r̂ is far (soft
        # opp → extract), meet-and-close when r̂ is near (hard opp → convert a no-deal). Prices AT the
        # reservation quantile (ε small), unlike the middle-shading EV that sank BBR. Offer-side → shadow.
        if P.get("neg_resv") and belief is not None:
            rhat = belief.reservation_estimate(P.get("neg_resv_kproject", 0.5))
            if rhat is not None:
                eps = P.get("neg_resv_eps", 0.02)
                fl = P.get("neg_keep_floor", 0.05)
                if is_seller:
                    counter = max(my_value * (1 + fl), rhat * (1 - eps))
                    if offer_price >= counter and offer_price > my_value:   # they already beat our target → close
                        return {"decision": "AcceptOffer"}
                else:
                    counter = min(my_value * (1 - fl), rhat * (1 + eps))
                    if offer_price <= counter and offer_price < my_value:
                        return {"decision": "AcceptOffer"}
        if P.get("neg_msg_reservation"):                       # extract from opponent's stated number (bluff-robust)
            scale = state.get("product_price_order") or my_value or 1
            counter = _msg_reservation_counter(counter, is_seller, my_value, offer_price,
                                               msg_nums, scale, P.get("neg_msg_eps", 0.02))
        # CONDITIONAL CONCEDE-TO-CLOSE (audit): close "bridgeable stalls" — opponent conceding, thin gap,
        # revealed ZOPA, but our reservation floor stalls them into a no-deal. Concede the last mile ONLY
        # when late AND belief has real evidence AND a still-profitable price clears a target accept-quantile.
        # Keeps the aggressive default on games we already win; prices at a quantile, not EV-max.
        if P.get("neg_close_enable") and belief is not None and belief.n_evidence >= 2:
            late = prog >= P.get("neg_close_prog", 0.5) or (not known and rounds >= P.get("neg_hz_patience", 2))
            if late:
                tgt = belief.price_at_pacc(P.get("neg_close_pacc", 0.55), my_value, is_seller)
                if tgt is not None:
                    mm = P.get("neg_close_min_margin", 0.02)
                    tgt = max(tgt, my_value * (1 + mm)) if is_seller else min(tgt, my_value * (1 - mm))
                    cc = reservation + P.get("neg_close_frac", 0.6) * (tgt - reservation)
                    if (is_seller and cc < counter) or (not is_seller and cc > counter):
                        counter = cc
        # GUARANTEED-CLOSE on the COUNTEROFFER (audit 2026-08-13, Fable #3 bug fix): the endgame price is
        # emitted here as a RejectOffer counter ~12x more often than via the offer branch, so gclose must
        # capitulate HERE too — otherwise the lever is mostly inert (explained the weak 6-10pt no-deal drop).
        # Same logic as the offer branch: in the last neg_close_rounds of KNOWN horizon, price into the ZOPA.
        if P.get("neg_guaranteed_close") and known:                    # BOTH roles, known-horizon (see offer branch)
            rem = (state.get("max_rounds") or 10) - rounds
            if rem <= P.get("neg_close_rounds", 2):
                eps = P.get("neg_close_eps", 0.05)
                gc = my_value * (1 + eps) if is_seller else my_value * max(0.02, 1 - eps)
                counter = min(counter, gc) if is_seller else max(counter, gc)
        # H0 DEEP-CLOSE PROBE (Fable opp-cond audit 2026-08-18, gated neg_h0_deepclose): the ONLY untested neg
        # region. In HIDDEN-horizon (h0), 92% of games reaching r15 die at 0 (mutual floor deadlock) — and a
        # SUB-FLOOR price (below our ~1.09c/0.91V hold floor) has literally NEVER been shown to any opponent here.
        # When late (r>=neg_h0_deepclose_round) AND the opponent has STALLED (no longer conceding to us), price
        # DEEP into their side to try to convert an otherwise-dead game. Risks only the ~8% late-closers (trades
        # their share down); break-even if ~4% of the dead mass accepts. Still profitable (clamped >0 below).
        # LIVE-ONLY (response to a never-shown price is unknowable offline); h0-only, non-CI. champion-lab A/B.
        if P.get("neg_h0_deepclose") and not known and rounds >= P.get("neg_h0_deepclose_round", 15) \
                and not _still_conceding:
            dce = P.get("neg_h0_deepclose_eps", 0.03)
            dc = my_value * (1 + dce) if is_seller else my_value * (1 - dce)
            counter = min(counter, dc) if is_seller else max(counter, dc)
        counter = max(counter, my_value * 1.001) if is_seller else min(counter, my_value * 0.999)  # never a loss
        return {"decision": "RejectOffer", "product_price": round(max(0.0, counter), 2)}

    return {}


def bargaining(actions, state, me, arch, name=None):
    """Reservation set by our own patience (delta): patient = hold out, refuse lowballs."""
    P = params.P
    money = state["money_to_divide"]
    idx = me.split("_")[-1]
    delta = float(state.get(f"delta_{idx}", 0.9) or 0.9)
    other = "player_1" if me == "player_2" else "player_2"
    rounds = state.get("round", 1)
    max_rounds = state.get("max_rounds") or 12
    known = state.get("horizon_known", True)
    prog = _progress(state, default_h=12)

    base = delta * P["barg_delta_coeff"]
    base += P["barg_arch_bump"].get(arch, 0.0)
    floor_share = max(0.30, base * (1 - prog) + 0.30 * prog)
    cap = P["barg_share_cap"]
    if _exploiting():                                     # squeeze to this opponent's learned tolerance
        t = exploit.target("bargaining", arch, name)
        floor_share = max(floor_share, t * (1 - 0.4 * prog))   # relax a little late to still close
        cap = max(cap, t + 0.05)
    floor = money * floor_share

    # RUBINSTEIN TIMING (audit): bargaining is a *timing* problem, not a share problem — the discount
    # δ^(round-1) destroys value, and holding out to round ~3.8 at δ=0.8 grinds a 0.50 share to ~0.24
    # realized. Accept at the responder value δ/(1+δ) (round-invariant), open at the proposer value
    # 1/(1+δ), so deals close in round 1-2. A patience premium survives only when delay is cheap (δ≈1).
    rub_resp = delta / (1.0 + delta)                       # 0.444@0.8, 0.474@0.9, 0.5@1.0
    premium = max(0.0, delta - 0.9) * P.get("barg_patience_k", 1.2) * (1 - prog)

    if actions["type"] == "decision":
        my_gain = state["last_offer"][f"{me}_gain"]
        # R1 δ-ADVANTAGE FLOOR-GUARD (Fable #20, gated barg_dadv_guard): in complete-info barg where WE are the
        # MORE-PATIENT party (δ − δ_opp ≥ min), we over-concede — the early low banks + accept-forward take
        # <0.47·money at r≤6 (realized pct 0.436 vs SPE 0.64-0.79). Held CF: the impatient opponent CROSSES (83%
        # cave, 0 no-deal in-scope) → dPCT +0.165 (+12-18 barg). So when in-scope, SUPPRESS the mid-game low banks
        # (not_r1 below) and REJECT the low thr accept — hold for the better split. The final-round (above) and
        # deadline_spe (below) endgame accepts still fire first, so this never turns a takeable endgame into a ND.
        _dadv_oi = "1" if idx == "2" else "2"
        _d_opp = float(state.get(f"delta_{_dadv_oi}", 0) or 0)
        _r1 = bool(P.get("barg_dadv_guard")) and state.get("complete_information") \
            and (delta - _d_opp) >= P.get("barg_dadv_min", 0.05) \
            and rounds <= P.get("barg_dadv_rounds", 6) \
            and my_gain < P.get("barg_dadv_floor", 0.47) * money
        # ANCHOR-HOLD COUNTER (new-field scout 2026-08-25, gated barg_anchor_counter): frontier LLM entrants
        # (opus5*, llm-*, Morphling) open extreme (offer us <=0.25 of pie) and barely concede (<=0.07 across the
        # game), holding to the horizon; our frozen banks/threshold then capitulate at frac ~0.154 (pctl 0.083).
        # Held CF over 12k games: opponent caves to our ~0.53 standing counter 71.8% (100% known-horizon) -> pctl
        # 0.231. Downside bounded (-0.8 worst): the capitulation-accepts we forgo are already ~worthless; break-even
        # cave rate 3.6% << empirical. Detect BEHAVIORALLY from the opp's OWN offer trajectory (open low + barely
        # glide), NOT by name. Mid-game only (rounds<=barg_ah_rounds) so the final-round(above)/deadline_spe endgame
        # accepts still fire as the safety net -> never turns a takeable endgame into a fresh no-deal. Mirrors _r1
        # (suppress low banks + hold). Invents rejects -> LIVE A/B only (default OFF). See glee-newfield-baseline-0825.
        _ah = False
        if P.get("barg_anchor_counter") and rounds <= P.get("barg_ah_rounds", 8) \
                and my_gain < P.get("barg_ah_floor", 0.45) * money:
            _ah_of = "player_1" if me == "player_2" else "player_2"
            _ah_fr = [h["offer"][f"{me}_gain"] / money for h in (state.get("history") or [])
                      if isinstance(h.get("offer"), dict) and h["offer"].get("from_player") == _ah_of
                      and isinstance(h["offer"].get(f"{me}_gain"), (int, float))]
            if len(_ah_fr) >= 2 and _ah_fr[0] <= P.get("barg_ah_open", 0.25) \
                    and (max(_ah_fr) - _ah_fr[0]) <= P.get("barg_ah_conc", 0.07):
                _ah = True
        _hold = _r1 or _ah
        if known and rounds >= max_rounds and my_gain > 0:
            return {"decision": "accept"}                 # truly final round: take any positive
        # DEADLINE SPE (Fable#11, barg_deadline_spe): finite-horizon backward induction. At round mr-1 the
        # responder is (by strict alternation) the one who PROPOSES the final round and can take ~barg_final_take
        # there, so their continuation of rejecting is δ·final_take. We were accepting at ~δ·0.47 (Rubinstein
        # stationary) and getting ground to a mean 0.209 at the deadline — hold for the final-round power instead.
        if P.get("barg_deadline_spe") and known and rounds == max_rounds - 1:
            cont = delta * P.get("barg_final_take", 0.79) * money
            return {"decision": "accept"} if my_gain >= cont else {"decision": "reject"}
        # d≈1 UNKNOWN-horizon BIRD-IN-HAND (top-5 push, task #44): at δ>=0.98 waiting is ~free (no discount)
        # so we hold out — but the UNKNOWN-horizon game can END at 0 (d1.0 no-deal ~10%, all unknown-horizon
        # where the known-final-round accept above never fires). Past barg_hz_close_round, take a DECENT
        # positive offer (>= barg_hz_close_floor) to bank it before the game dies. Conservative gates keep it
        # off the games we win by our own offer (those close early/high). Shadow-tested before champion.
        # SEAT-GATE (Fable barg transcript audit): the un-gated rule fired in the δ1.0 PROPOSER seat too, where
        # replay shows accept rules cost -0.026..-0.05 pct (that's our best cell, field-pct 0.93-0.95 — leave it).
        # Restrict to responder parity (rounds%2==1) where replay banks the (1.0,?,resp) 14% no-deal zeros:
        # theta +0.054 / champion +0.03 pct at round 6 / floor 0.45.
        if P.get("barg_hz_close") and not _hold and not known and delta >= 0.98 \
                and (rounds % 2 == 1) \
                and rounds >= P.get("barg_hz_close_round", 4) \
                and my_gain >= P.get("barg_hz_close_floor", 0.35) * money:
            return {"decision": "accept"}
        # DOOMED-SEAT BANK (Fable barg transcript audit, gated barg_doomed_bank): known-horizon games where the
        # OPPONENT owns the final proposal (even mr, we opened) are structurally lost to holdout bots — our
        # threshold δ²/(1+δ)+premium refuses decent mid-game offers (0.35-0.40), then the final-round clause
        # (line 368) capitulates at share 0.12-0.18 as opus5/zoro/Rubinstein ride us down. Bank a decent offer
        # EARLY instead. Accept-side, replay-validated on theta+champion independently: (0.95,12,prop) +0.099
        # pct, (0.9,12,prop) +0.02, (0.8,12) +0.004. Only accepts offers the opponent actually made -> no new
        # no-deal risk (strictly converts grind/zeros into banked deals). Floor 0.30 at δ≤0.9, 0.35 at δ=0.95.
        if P.get("barg_doomed_bank") and not _hold and known and rounds >= 2 and delta <= 0.97 \
                and (max_rounds % 2 == rounds % 2):           # opponent owns the final proposal (doomed seat)
            fl = max(0.30, delta * delta / (1.0 + delta) - 0.11)
            if my_gain >= fl * money:
                return {"decision": "accept"}
        # GRIND-BANK (Fable barg d≤0.95 audit, gated barg_grind_bank): once a discounted game reaches round 3
        # the δ^(r-1) discount has already eaten 2 rounds and every further round of holdout costs more than the
        # share we're holding out FOR — the replay counterfactual over 17.9k recorded d0.8-0.95 games (all seats,
        # both horizons, accept-side so exactly realizable) shows a round-DECAYING accept floor strictly beats the
        # current flat δ·δ/(1+δ) threshold: d0.8 +0.008, d0.9 +0.016, d0.95 +0.028 realized share, no-deal only
        # CONVERTED (31/56/75 zeros banked), never created. Complements barg_doomed_bank (known+doomed only, r≥2):
        # this fires in unknown-horizon and non-doomed seats too, where over half the replay gain lives. Gated to
        # δ≤0.97 so the elite d1.0 hold (pct .93-.95) is untouched.
        if P.get("barg_grind_bank") and not _hold and P.get("barg_grind_bank_dmin", 0.93) <= delta <= P.get("barg_grind_bank_dmax", 0.97) and rounds >= P.get("barg_grind_bank_round", 3):
            fl = P.get("barg_gb_r3", 0.30) if rounds <= 4 else \
                (P.get("barg_gb_r5", 0.25) if rounds <= 6 else P.get("barg_gb_r7", 0.15))
            if my_gain >= fl * money:
                return {"decision": "accept"}
        # HZ-BANK (Fable offline-analyst 2026-08-15, gated barg_hz_bank): the ONE live-uncovered pocket = δ∈{0.9,0.95}
        # UNKNOWN-horizon (barg|d0.9|mr?), where the only accept path is the flat Rubinstein threshold (0.426/0.463·money)
        # and opponents park at 0.37-0.48 nominal at rounds 2-3 while δ^r eats 5-10%/round. Banking their r2/r3 offer
        # beats the grind + the late capitulation + the 4-5% no-deals. HELD-OUT replay (train/test split by log order,
        # current-era, per-agent): TEST +0.0211 pct / +0.0266 share (SE 0.0022, n=1367), stable across all 3 agents,
        # both δ, both halves, all 4 eras. Distinct from grind_bank (which washed LIVE but at n=64/SE.023 = underpowered
        # ~10x): this is unknown-horizon-only, δ0.9/0.95-only (excludes d0.8 ~0-headroom + d1.0 elite), and fires at r>=2
        # (grind_bank r>=3 missed 45% of the value — r2 is 204/455 fires). Accept-side only: accepts an offer already on
        # the table, never creates a no-deal. ON TRIAL (theta only, powered DiD n>=400, pre-registered kills).
        if P.get("barg_hz_bank") and not _hold and (not known or P.get("barg_hz_bank_known")) and P.get("barg_hz_bank_dmin", 0.85) <= delta <= 0.97 \
                and rounds >= P.get("barg_hz_bank_round", 2) \
                and my_gain >= P.get("barg_hz_bank_floor", 0.35) * money:
            return {"decision": "accept"}   # dmin gate: δ0.95-only (dmin~0.93) is the validated +pct pocket; d0.9 is unfarmable rent
        if _hold:
            return {"decision": "reject"}                # R1/anchor-hold: in-scope (patient party or AH archetype, low early offer) -> hold for the cross
        disc = delta if P.get("barg_accept_forward") else 1.0    # discount-forward: field offers ~0.40, not δ/(1+δ)
        thr = min(P["barg_share_cap"], disc * rub_resp + premium)
        return {"decision": "accept"} if my_gain >= money * thr else {"decision": "reject"}

    if actions["type"] == "offer":
        my_share = min(cap, (1.0 / (1.0 + delta)) + premium)   # SPE proposer share; close in round 1
        # δ-GATED CONCESSION (audit): at δ<=0.9 premium=0, so we re-offer the IDENTICAL share every round →
        # deadlock → grind to round ~6.6 → the δ^(round-1) discount collapses a nominal 0.55 to a realized
        # 0.35. Closing at round 2 with a smaller share beats grinding (0.50·δ >> 0.55·δ^5). So concede our
        # demand toward a closeable floor over the first ~2 rounds. Leave δ=1.0 untouched (no discount →
        # holding genuinely pays). Offer-side (opponent response counterfactual) → validated shadow-live.
        if P.get("barg_concede") and delta <= P.get("barg_concede_delta_max", 0.9):
            floor_c = P.get("barg_concede_floor", 0.50)
            frac = min(1.0, (rounds - 1) / max(1.0, P.get("barg_concede_rounds", 2.0)))
            my_share = min(cap, max(floor_c, my_share - frac * (my_share - floor_c)))
        # HIGH-δ AGGRESSION (audits converge): at δ≈1.0 there's no discount, so holding is free and the
        # top bargainers open ~0.67 and hold while we settle ~0.50 (we realize 0.45 vs field 0.55). Bargaining
        # is SAFE to push (no-deal ~1%, "the no-deal cliff is far"), unlike negotiation. Open higher and hold
        # when delay is cheap. Offer-side → shadow-validated. Gated to high δ (holding genuinely pays there).
        if P.get("barg_hi_open") and delta >= P.get("barg_hi_delta_min", 0.98):
            my_share = min(P["barg_share_cap"], max(my_share, P.get("barg_hi_open_share", 0.68)))
        # LOW-δ OPEN-STEP-DOWN (barg offer-side erosion audit 2026-08-23): at δ≈0.9 we sit at bare
        # Rubinstein 0.5263 flat; the only above-median path in these harvested cells is the field's
        # r1-accept of our open (14-24% of games), so opening slightly higher (0.55) then reverting
        # captures more when they DO accept round-1 without changing the already-lost late paths.
        # SEPARATE gate from barg_hi_open (audit: do NOT lower barg_hi's δ-gate — it pins late-round
        # demand and wrecks the healthy d0.95/d1.0 schedules). Rounds<=2 only → reverts to Rubinstein
        # after. Est +10-40 barg pts (~10% of erosion); untestable offline → live A/B on eta (clone).
        if P.get("barg_lo_open") and (state.get("horizon_known", True) or not P.get("barg_lo_known_only")) \
                and P.get("barg_lo_delta_min", 0.85) <= delta <= P.get("barg_lo_delta_max", 0.93) and rounds <= P.get("barg_lo_rounds", 2):
            my_share = min(P["barg_share_cap"], max(my_share, P.get("barg_lo_open_share", 0.55)))
        # ASYM-CLOSE (Fable 2026-08-16, barg_asym_close): complete-info games hand us delta_opp but bargaining()
        # was blind to it (line 357 reads own delta only). Vs a visibly-patient opponent (d_opp>=0.95, ours lower)
        # our frozen demand gets ground by delta^round over long games (realize ~0.12 at d0.8). Cap our demand at
        # THEIR responder reservation d_opp/(1+d_opp)+eps so they accept NOW instead of grinding us. Known-horizon
        # only (unknown-horizon accept@0.50 <=0.12, unmeasurable). ΔEV Wilson-lower-positive all 4 cells
        # (d0.8/d0.9 × d_opp 0.95/1.0); ~+0.02 realized share/in-scope game. Insert before deadline_spe so the
        # finite-horizon endgame still overrides. ON TRIAL (champion only, barg controls theta+gamma).
        if P.get("barg_asym_close") and state.get("complete_information") and known and rounds >= 2:
            _oi = "1" if idx == "2" else "2"
            d_opp = float(state.get(f"delta_{_oi}", 0) or 0)
            # Fable audit (2026-08-16): d_opp=1.0 is dominated by flat-holdout bots (offer us ~0.12, demand
            # ~0.85, never concede) — the cap converts 0 of them but turns the ~12% who DO accept our 0.556
            # demand into 0.48 closes (−0.076 each) = net −0.009 share for zero upside. Narrow to [0.95,1.0):
            # the only subcell where opponents actually concede (accepts land 0.38-0.46, end rounds 3-5).
            if 0.95 <= d_opp < 1.0 and (d_opp - delta) >= 0.05 - 1e-9:  # eps: 0.95-0.9==0.04999… in floats
                my_share = max(0.30, min(my_share, 1.0 - (d_opp / (1.0 + d_opp) + P.get("barg_asym_eps", 0.02))))
        # HIDDEN-δ BEHAVIORAL PATIENCE CAP (harsh audit 2026-08-16, barg_asym_hidden): the incomplete-info analog
        # of asym_close. When WE are impatient (delta<=0.9, our pie decays δ^r each round) we re-offer 1/(1+δ)≈0.556
        # every round; vs a behaviorally patient+FIRM opponent (their offers to US unmoved over the last 3 rounds AND
        # sitting in the MEETABLE 0.42-0.52 band) that deadlock grinds our realized share to ~0.11 (audit: mean
        # offer-round R15+, 18% of d0.8-seat games at realized 0.111). Meet their standing number to CLOSE NOW.
        # Scoped OUT: complete-info (asym_close handles it), non-impatient seats, and UNMEETABLE holdouts (offer us
        # <0.42, where meeting is -EV — same reason asym_close excludes d_opp=1.0). Offer-side; NB this is kin to the
        # washed accept-bank class (grind/hz_bank) so it is champion-only on a powered DiD with hard kills. Controls theta+gamma.
        if P.get("barg_asym_hidden") and not state.get("complete_information") \
                and delta <= P.get("barg_asym_hidden_dmax", 0.9) and rounds >= 3 and money:
            _hist = state.get("history") or []
            _opp_us = [e["offer"].get(f"{me}_gain") for e in _hist
                       if isinstance(e.get("offer"), dict) and e["offer"].get("proposer") == other
                       and isinstance(e["offer"].get(f"{me}_gain"), (int, float))]
            _opp_us = [x / money for x in _opp_us[-3:]]
            if len(_opp_us) >= 3 and (max(_opp_us) - min(_opp_us)) <= 0.03 \
                    and 0.42 <= min(_opp_us) <= 0.52:
                my_share = max(0.30, min(my_share, _opp_us[-1] + P.get("barg_asym_hidden_eps", 0.0)))
        # DEADLINE SPE offer (Fable#11, barg_deadline_spe): the finite-horizon endgame the share_cap misses.
        # As the FINAL-round proposer the responder accepts any positive, so demand barg_final_take (cap bypassed
        # — we were offering the Rubinstein 0.51 and leaving ~0.28 on the table). At round mr-1 (doomed seat: if
        # they reject they propose the final round and take final_take) give them just their continuation δ·ft
        # and keep the rest (1−δ·ft ≈ 0.21-0.29 > the 0.209 we get at their mercy), which also closes the game.
        if P.get("barg_deadline_spe") and known:
            ft = P.get("barg_final_take", 0.79)
            if rounds >= max_rounds:
                my_share = ft                                  # final-round proposer: take (almost) all
            elif rounds == max_rounds - 1:
                my_share = max(0.0, 1.0 - delta * ft - P.get("barg_deadline_eps", 0.02))
        mine = round(money * my_share, 2)
        theirs = round(money - mine, 2)
        i_am_alice = str(me).endswith("1") or str(me).lower() == "alice"  # player_1 == alice
        return {                                    # server requires alice_gain/bob_gain keys
            "alice_gain": mine if i_am_alice else theirs,
            "bob_gain": theirs if i_am_alice else mine,
            "message": "Waiting costs us both — this is my firm, fair split.",
        }

    return {}


_BUY_MSG = "I recommend buying this product."
_HOLD_MSG = "I do not recommend buying this product."
# HEDGED MID-TIER (research #13, field copy): top pers sellers convert marginal (low-quality) pushes with a
# hedged tone that still carries a 'recommend' token (keyword buyers still buy) but does NOT over-claim high
# quality — so the occasional lemon burns far less trust than a full _BUY_MSG, keeping the buyer following at a
# ~0.70 recommend rate. This is the piece crude full-push frec_target (#30) was missing.
_HEDGE_MSG = "I'd recommend it — fair value at this price, though the call's yours."


_PERS_HEDGE = ("ordinary", "still available", "reasonable", "decent", "fair unit", "okay",
               "ok unit", "average", "standard", "acceptable", "not bad")


def _pers_signal(msg):
    """Read a seller message/recommendation as buy / hold / hedge / neutral. Handles text and the binary
    'yes'/'no' format (~54% of games). HEDGE = a hedged 'buy' ('ordinary'/'fair unit'…) which is lemon-
    leaning at low prior p but high-leaning at high p — the buyer decides based on p (prior-gated)."""
    m = (msg or "").strip().lower()
    if m in ("no", "n", "false", "0") or "do not recommend" in m or "not recommend" in m \
            or "hold" in m or "wouldn't" in m or "avoid" in m:
        return "hold"
    if any(h in m for h in _PERS_HEDGE):
        return "hedge"
    if m in ("yes", "y", "true", "1") or "recommend" in m or "worth buying" in m \
            or "strong" in m or "buy" in m:
        return "buy"
    return "neutral"


def _pers_kg_push(p, v, u, price, margin=0.0):
    """Kamenica–Gentzkow low-quality push rate, generalized with a posterior-honesty MARGIN (Fable#7).
    Original q* parks the per-rec posterior P(hi|rec) exactly at the buyer's break-even (price-u)/(v-u), where
    real (not knife-edge-rational) field buyers COLLAPSE their follow-through to ~0.5 — so at p<=0.5 r=2-3 our
    seller sits at percentile 0.26-0.29 while the field pushes less, keeps honesty margin, and gets ~2x sales.
    Fix: target posterior h = break-even + margin, giving q = p(1-h)/((1-p)h). margin=0 is BIT-IDENTICAL to
    the original q*; margin>0 pushes lows LESS. No-op where q>=cap (high-p cells: cap binds regardless)."""
    if v <= u:
        return 1.0 if v > price else 0.0
    h = (price - u) / (v - u) + margin                    # target per-rec posterior honesty
    if h <= 0.0:
        return 1.0
    if h >= 1.0:
        return 0.0
    return max(0.0, min(1.0, p * (1 - h) / ((1 - p) * h)))


def _pers_history_stats(history, bought_only=False):
    """From past rounds: E[value | seller said BUY] and how honest 'buy' signals have been.
    bought_only (buyer_liar_bought_only, Fable pers audit Finding 2): skip rounds with NO revealed quality.
    On the BUYER side quality is only revealed on BOUGHT rounds, so a refused recommendation has no quality
    key — counting it as a lemon (the legacy default `else: lo`) self-poisons the liar-gate into an absorbing
    never-buy. When set, unrevealed rounds are ignored instead of scored as lows."""
    hi = lo = 0
    for e in history or []:
        if _pers_signal(e.get("seller_message")) == "buy":
            q = e.get("quality")
            if q == "high":
                hi += 1
            elif q is None:
                if not bought_only:
                    lo += 1                                # legacy: unrevealed (refused) counted as low
            else:
                lo += 1
    n = hi + lo
    return (hi / n if n else None), n


def _pers_arm(game_id):
    """Stable within-agent A/B arm (0=treatment, 1=control) from md5(game_id). Same game_id -> same arm
    for every turn in the game -> a clean contemporaneous split immune to diurnal/cross-agent confounds
    (unlike the degenerate parity hash the neg_ci_release_split used). game_id absent -> arm 0."""
    if not game_id:
        return 0
    return int(hashlib.md5(str(game_id).encode()).hexdigest()[:8], 16) % 2


def persuasion(actions, state, game_id=None):
    """Seller: KG-optimal signaling to MAXIMIZE sales (seller earns per sale, any quality) while a
    rational buyer still follows our 'buy'. Buyer: follow the seller's signal ONLY as far as their
    demonstrated honesty warrants — buy when E[value | evidence] >= price, else refuse (defends against
    a seller pushing lemons, exploits an honest one). Replaces the dominated fully-honest corner."""
    pp = params.P.get("persuasion", {})
    price = state.get("product_price", 0) or 0
    p = state.get("p")
    v = state.get("v")
    u = state.get("u", 0.0) or 0.0

    if actions["type"] in ("seller_message", "seller_recommendation"):
        q = _quality(state)
        if q == "high":
            push = True
        elif q == "low":
            # Buyers PUNISH deception (logs: buy-rate 0.66 -> 0.51 after we push a low). So the trust-
            # optimal low-push depends on quality rate p: when highs are common, preserve trust and only
            # cash in near the end; when lows are common, honesty sells nothing so push them.
            remaining = (state.get("total_rounds") or 20) - state.get("round", 1)
            frec_target = pp.get("pers_frec_target", 0)
            # SCOPE to low-p only (Fable #23): the frec controller is GLOBAL and mechanically caps the p=0.8
            # binary cell DOWN (KG already pushes 0.946 there) → −80..−183 pts in that cell, making the as-armed
            # 0.85 target a net wash. Gate to p<=pers_frec_pmax(0.6): push-more helps only the low-p cells;
            # p>0.6 falls through to the KG branch. Scoped => +40-105 pers (+13-35 mean-of-3) per #23.
            if frec_target and frec_target > 0 and p is not None and p <= pp.get("pers_frec_pmax", 0.6):
                # COMMITTED target recommend-rate (audit): our q* coin under-sells (low-push 0.19 vs field
                # 0.55-0.80) and the empirical buyer follow-curve is nearly INELASTIC to our recommend-rate
                # (follow 0.55->0.71 across f_rec quartiles) — so a committed, legible recommend-rate f* beats
                # a per-round garble. Push lows until our cumulative recommend-rate reaches the target (then
                # hold to keep the rate stable/legible). Endgame force-push. Offer-side -> shadow-only.
                if remaining <= pp.get("pers_low_push_endgame", 3):
                    push = True
                else:
                    hist = state.get("history") or []
                    rounds = len(hist)
                    rec = sum(1 for e in hist if _pers_signal(e.get("seller_message")) == "buy")
                    f_rec = rec / rounds if rounds else 0.0
                    push = f_rec < frec_target
            elif pp.get("pers_kg") and p is not None and price and (v is not None or pp.get("pers_kg_vfallback")):
                # KG-OPTIMAL low-push (audit): our seller was pathologically honest (pushes lows 0.09-0.25
                # vs the field's 0.55-0.80) and under-sold — pure percentile hole (seller payoff floors at 0).
                # Push lows at q* = p(v-price)/((1-p)(price-u)) — the max deception a rational buyer still
                # tolerates — scaled by pers_kg_cap. Force push in the endgame (no reputation left to protect).
                # V-FALLBACK (Fable#9): when the buyer value v is HIDDEN (is_seller_know_cv=false, ~48% of games,
                # BOTH formats) this branch was skipped -> fell through to the pers_honest_p=0 honest branch ->
                # seller FULLY HONEST (push 0.00). Substitute v_eff=price*pers_r_assumed (field r-grid median ~2).
                v_eff = v if v is not None else price * pp.get("pers_r_assumed", 2.0)
                if remaining <= pp.get("pers_low_push_endgame", 3):
                    push = True
                else:
                    _kgm = pp.get("pers_kg_margin", 0.0)
                    # S2 honesty-shade A/B (escape-audit 0822): md5(game_id) arm-split — arm0 shades toward
                    # honesty (higher margin -> push lows LESS), arm1 = live margin. Bit-identical unless
                    # pers_kg_margin_split is set (eta lab only). Reads sell-rate/round per cell per arm.
                    if pp.get("pers_kg_margin_split") and _pers_arm(game_id) == 0:
                        _kgm = pp.get("pers_kg_margin_hi", _kgm)
                    _q = _pers_kg_push(p, v_eff, u, price, _kgm) * pp.get("pers_kg_cap", 0.85)
                    # LATE-RAMP push timing (Fable 2026-08-23, GO +4-12 central +7, rate-neutral, refutation-survived):
                    # revealing a lemon poisons FUTURE push-follow (-1.3pp/lemon), goods ~0 -> push lows LATE to
                    # minimize the follow-loss. Reshape WHEN the low-q coin fires (later-weighted) at the SAME rate.
                    # ONLY in unsaturated cells (_q<=qmax): saturated cells (q_base>0.625) are bit-identical (factor
                    # 1.000) so the naive -23..-52 rate-cut trap is excluded. RD=state.round (1-indexed); ramp spans
                    # the live coin support rds 3-17. Gated default-off; disjoint from high-p guard / low-p / endgame.
                    if pp.get("pers_push_lateramp") and _q <= pp.get("pers_lateramp_qmax", 0.625):
                        _lo = pp.get("pers_lateramp_lo", 0.4); _hi = pp.get("pers_lateramp_hi", 1.6)
                        _w = _lo + (_hi - _lo) * min(1.0, max(0.0, (state.get("round", 1) - 3) / 14.0))
                        _q = min(1.0, _q * _w)
                    push = random.random() < _q
            elif p is not None and p >= pp.get("pers_honest_p", 0.65):
                push = remaining <= pp.get("pers_low_push_endgame", 3)   # high-p: guard reputation
            else:
                push = True                                             # low-p: must push lows to sell
            # +EV-CELL PUSH (Fable#11, pers_evplus_push): where the prior alone makes buying +EV
            # (p·v+(1-p)·u >= price), a rational buyer buys under an always-recommend policy, so the KG garble
            # (q*·cap) and the warmup just forgo guaranteed sales — the field sells 0.72-0.95/round here, we sat
            # at 0.31-0.88. Recommend every round in those cells. No-op in -EV cells (pbuyer_scoped untouched).
            v_eff2 = v if v is not None else price * pp.get("pers_r_assumed", 2.0)
            is_evplus = (p is not None) and bool(price) and \
                (p * v_eff2 + (1 - p) * u) >= price * (1 + pp.get("pers_evplus_eps", 0.0))
            evplus_active = bool(pp.get("pers_evplus_push")) and is_evplus
            if evplus_active:
                push = True
            # PUSH WARMUP (Fable#10 F2, pers_push_warmup): hold lows honest until round 3 AND the buyer's first
            # purchase, to avoid a round-1 burn->quit. pers_warmup_scope (Fable#11) restricts it to the cells it
            # was meant for (v-hidden or prior--EV); in +EV cells it only forgoes sales, so skip it there.
            warmup_here = (not evplus_active) and (
                (not pp.get("pers_warmup_scope")) or (v is None) or (not is_evplus))
            if pp.get("pers_push_warmup") and push and warmup_here and remaining > pp.get("pers_low_push_endgame", 3):
                hist = state.get("history") or []
                if state.get("round", 1) < pp.get("pers_warmup_round", 3) \
                        or not any(e.get("bought") for e in hist):
                    push = False
        else:                                                  # quality unknown to us: lean on prior
            push = (p is None) or (p >= 0.5)
        # OPPONENT-CONDITIONED FOLLOWER PUSH (opp-cond study 2026-08-27, gated pers_follower_push): buyers are
        # TEXT-BLIND but follow our RECOMMEND DECISION. "Follower" buyers (our first 2 pushes both bought) buy
        # ~80% of PUSHED LOWS (P(buy|push,low)=0.80 OBSERVED n=19.2k — not counterfactual) yet we currently HOLD
        # ~24% of rounds vs them (all lows) = ~3.7 forfeited sales/game; holds earn ~0 and followers DON'T punish
        # (next-push buy 95.9% after a bought lemon). So once detected, PUSH every round (override the KG-garble/
        # warmup holds), with a 2-consecutive-decline REVOCATION guard (stop if they turn out to punish). Decision-
        # side, +20-50 pers est, detection ~82% precision by round 3-4. Endgame already forces push. Default OFF.
        if pp.get("pers_follower_push") and not push and remaining > pp.get("pers_low_push_endgame", 3):
            _mp = [bool(e.get("bought")) for e in (state.get("history") or [])
                   if _pers_signal(e.get("seller_message")) == "buy"]
            if len(_mp) >= 2 and all(_mp[:2]) and not (not _mp[-1] and not _mp[-2]):
                push = True                                    # detected follower, not in decline-revocation -> sell
        if actions["type"] == "seller_recommendation":
            return {"decision": "yes" if push else "no"}
        # FIELD-MIMIC MESSAGE TIERS (research #13, faithful copy of top pers sellers): high-quality pushes get
        # the strong honest rec (_BUY_MSG, liar-gate immune); low-quality pushes get the HEDGED tier (_HEDGE_MSG)
        # so a higher recommend rate (pers_frec_target≈0.70, all-highs + filler-lows → P(high|bought)≈0.47, right
        # at the field's 0.40-0.61 break-even band) doesn't burn buyer trust the way crude full-push did (#30).
        # Gated by pers_hedge_lowq. The optional LLM rewrite (GLEE_LLM_MSG, stood down #125) stays available/inert.
        if push:
            det_msg = _HEDGE_MSG if (q == "low" and pp.get("pers_hedge_lowq")) else _BUY_MSG
        else:
            det_msg = _HOLD_MSG
        if llm.message_enabled("persuasion"):
            opp_msg = (state.get("last_offer") or {}).get("message", "") or state.get("buyer_message", "") or ""
            return {"message": llm.llm_message("persuasion", state, "seller", {"message": det_msg}, opp_msg=opp_msg)}
        return {"message": det_msg}

    if actions["type"] == "buyer_decision":
        signal = _pers_signal(state.get("seller_message"))
        hist = state.get("history") or []
        margin = pp.get("buyer_margin", 0.0)
        if signal == "hold":
            return {"decision": "no"}                          # a sales-seeking seller saying 'hold' => it's a lemon
        _binary = state.get("seller_message_type") == "binary"
        if pp.get("buyer_text_clf") and not (pp.get("buyer_clf_text_only") and _binary):
            # BINARY-SKIP (Fable pers audit Finding 1, buyer_clf_text_only): in mbinary cells the message is
            # literally "yes"/"no", and textclf.p_high("yes") is a CONSTANT 0.766 -> the clf collapses to
            # "buy iff r>=1.31", i.e. NEVER buy at r<=1.25, in EVERY game (all 6 low-r binary buyer cells were
            # a hard 0). It also RETURNS here, killing the f_rec free-channel rule below. Skip the clf for binary
            # messages so they route to buyer_frec (recommend-rate inversion), which is the correct estimator.
            # TEXT-CLASSIFIER buyer (task #34, validated): read the seller's message as a calibrated
            # P(high) from an offline bag-of-words+bigram model instead of keyword buckets + a fixed
            # neutral penalty. Beats prior/keyword on a by-game temporal holdout (neutral acc 0.96 vs
            # 0.43; no regression on confident buckets). Hard hold->refuse kept above; EV-decide otherwise.
            from tw import textclf
            pmc = textclf.p_high(state.get("seller_message"))
            if pmc is not None:
                if v is not None:
                    # THIN-MARGIN FIX (Fable#5, verified): the clf reads only the CURRENT message and RETURNED
                    # here, bypassing the liar-gate below -> in thin-margin cells (p*v~price) its overconfidence
                    # (~10pt: realized 0.78 vs needed 0.83) bought -EV repeatedly (6 buyer cells mean-negative,
                    # -194k worst, 420 games). Two dominant-direction guards: (1) shrink pmc by the measured
                    # calibration gap; (2) revive the within-game liar-gate — if our bought-round record already
                    # proves buying is -EV, refuse regardless of the message. Both only turn buy->skip (>=0), never
                    # skip->buy, so bounded below by 0.
                    if pp.get("buyer_clf_guard"):
                        pmc_adj = max(0.0, pmc - pp.get("buyer_clf_calib", 0.0))
                        buy = (pmc_adj * v + (1 - pmc_adj) * u) >= price * (1 + margin)
                        if buy:
                            emp2, n2 = _pers_history_stats(hist, pp.get("buyer_liar_bought_only"))
                            if n2 >= pp.get("buyer_liar_n", 3) and emp2 is not None \
                                    and (emp2 * v + (1 - emp2) * u) < price:
                                buy = False                        # proven liar this game -> stop buying
                        return {"decision": "yes" if buy else "no"}
                    # PRIOR-CORRECTION (Fable 2026-08-23, GO +3-12 pers, floor +1, refutation-survived): the
                    # prior-blind text clf gives pmc from the message alone; fold the round's quality prior p back
                    # in via Bayes so the buy test uses the calibrated posterior. Scoped p>=0.6 (p0.8 cells, where
                    # the clf most under-weights the prior). Gated default-off; disjoint from the seller side.
                    if pp.get("buyer_clf_prior_adj") and p is not None and p >= pp.get("buyer_prior_adj_pmin", 0.6):
                        pmc = pmc * p / (pmc * p + (1 - pmc) * (1 - p)) if (pmc * p + (1 - pmc) * (1 - p)) > 0 else pmc
                    buy = pmc * v + (1 - pmc) * u >= price * (1 + margin)
                    # SCOPED BUYER GATES (Fable#10, pers_pbuyer_scoped): the clf RETURNS on every message, so
                    # all the Bayesian/f_rec/liar logic below is DEAD in prod -> the live buyer is a memoryless
                    # "buy iff pmc*v>=price". In PRIOR--EV cells (p*v+(1-p)*u < price) that overbuys lemons into
                    # deep-negative games (worst -0.64 = ~0th pct, convex-worst). Apply two REMOVE-ONLY gates
                    # ONLY in -EV cells (+EV cells stay bit-identical -> no rich-cell false-trip, the failure mode
                    # that sank the old unscoped buyer_clf_guard). Both only flip buy->no, so bounded below by 0.
                    if pp.get("pers_pbuyer_scoped") and buy and p is not None \
                            and (p * v + (1 - p) * u) < price:
                        rounds = len(hist)
                        if rounds >= pp.get("buyer_scoped_minrounds", 3):
                            rec = sum(1 for e in hist if _pers_signal(e.get("seller_message")) == "buy")
                            f_rec = rec / max(rounds, 1)
                            if f_rec > 0:                           # P(high|buy)=p/f_rec (KG free-channel inversion)
                                phb = min(1.0, p / f_rec)
                                if phb * v + (1 - phb) * u < price * (1 + pp.get("buyer_scoped_slack", 0.05)):
                                    buy = False
                        emp2, n2 = _pers_history_stats(hist, pp.get("buyer_liar_bought_only"))        # prior-anchored (k=2) liar-gate on bought record
                        if n2 >= 2 and emp2 is not None:
                            hi2 = round(emp2 * n2)
                            post = (2.0 * p + hi2) / (2.0 + n2)
                            if post * v + (1 - post) * u < price:
                                buy = False
                    # THIN-CELL FALLTHROUGH (Fable pers audit 2026-08-18, gated buyer_clf_frec_fallback): for TEXT
                    # messages the clf RETURNS here, so the calibrated f_rec free-channel rule below never runs.
                    # Field sell-text scores pmc~0.77 < the 0.80-0.83 break-even of thin cells (r<=1.3), so the clf
                    # refuses ENTIRE games (text thin cells 45-59% zero-buy vs 4-18% in same-economics BINARY cells
                    # where f_rec runs; binary earns up to +1.21/game vs text +0.61). When the clf says NO on a
                    # recommend in a thin, +EV-prior cell, fall through to f_rec instead of folding the whole game.
                    # Add-only, thin-cell + p*r>=prmin scoped (wide low-p cells where the clf BEATS f_rec untouched).
                    if not (pp.get("buyer_clf_frec_fallback") and not buy and signal == "buy"
                            and p is not None and price
                            and (v / float(price)) <= pp.get("buyer_clf_fallback_rmax", 1.3)
                            and p * (v / float(price)) >= pp.get("buyer_frec_prmin", 0.0)):
                        return {"decision": "yes" if buy else "no"}
                    # else: thin-cell text under-buy -> fall through to the f_rec free-channel rule below
                elif v is None:
                    return {"decision": "yes" if pmc >= 0.5 * (1 + margin) else "no"}
        if signal == "hedge":
            # Hedged 'buy' is lemon-leaning at LOW prior p (~0.13) but high-leaning at high p (~0.63-0.77).
            # Prior-gate: treat as hold only when p is low/thin; else fall through to the EV rule.
            if pp.get("buyer_hedge_skip") and (p is None or p < pp.get("pers_hedge_pmax", 0.6)):
                return {"decision": "no"}
        # FREE-CHANNEL rule (audit): the seller's recommend-rate f_rec is observed EVERY round (incl. no-buy),
        # so P(high|buy)=p/f_rec (KG inversion) -> buy iff P(high|buy)*v >= price  <=>  f_rec <= p*r. This
        # avoids the absorbing 'never buy -> never learn' trap the pure prior-anchor created in the 5 LEAK
        # cells (+EV under the signal, -EV under the raw prior). Cold-start explore to seed f_rec; a Beta on
        # bought rounds is the liar-gate. Fully observable -> replay of the decision sequence is bias-free.
        if pp.get("buyer_frec") and p is not None and v is not None and price \
                and p * (v / float(price)) >= pp.get("buyer_frec_prmin", 0.0):
            # SCOPE GATE (buyer_frec_prmin): only trust the f_rec inversion where the prior is near-+EV
            # (p*r >= prmin). In thin-margin (r1.25) / low-prior cells the field sellers lie, the inversion
            # P(high|buy)=p/f_rec over-estimates, and we buy -EV lemons (p0.5|r1.25 mean -250k, min -1.75M —
            # convex percentile-killers). Below prmin we fall through to the prior-anchored buyer, which
            # correctly REFUSES prior--EV cells (bounded at 0). Keeps the p0.8 wins, kills the thin-cell tails.
            r = v / float(price)
            rounds = len(hist)
            if rounds < pp.get("buyer_explore_rounds", 2):
                ok = (p * v >= price)                           # explore-buy ONLY in prior-+EV cells: f_rec is
                #   observable from the seller's recs WITHOUT buying, so in prior--EV cells (p*v<price) we wait
                #   and buy only once f_rec proves separation — never eat cold-start lemons (the -750k tails in
                #   p0.33/p0.5|r1.25 that made the raw fix percentile-negative despite a +mean).
            else:
                rec = sum(1 for e in hist if _pers_signal(e.get("seller_message")) == "buy")
                f_rec = rec / max(rounds, 1)
                ok = f_rec <= p * r * (1.0 + pp.get("buyer_frec_slack", 0.0))
                emp2, n2 = _pers_history_stats(hist, pp.get("buyer_liar_bought_only"))            # liar-gate: bought-round record proves -EV
                # B1 (Fable pers audit 2026-08-21, buyer_liar_evplus_off): in prior-+EV binary cells this gate
                # LATCHES OFF on a 3-sample lucky-lemon dip and forfeits ~13-16 rounds of +EV recs (12,677 refusals,
                # 100% gate-caused; implied P(high|"no")~0 so recs are +EV; sim +0.077-0.086 pct x8-9 cells; top
                # accounts follow our recs 0.92-1.00 vs our 0.57-0.78). When the PRIOR alone is +EV, skip the gate
                # (the dip is noise, not a real liar here). Bit-identical when the flag is off.
                _ev_plus = (p is not None and (p * v + (1 - p) * u) >= price)
                if n2 >= 3 and emp2 is not None and (emp2 * v + (1 - emp2) * u) < price \
                        and not (pp.get("buyer_liar_evplus_off") and _ev_plus):
                    ok = False
                    # RE-ENTRY PROBE (Fable pers audit 2026-08-20 F1, gated buyer_liar_reentry): the raw liar-gate
                    # is an ABSORBING trap — emp2/n2 are bought-only, so once we stop buying they FREEZE and a
                    # first-3-round noise dip latches OFF for the whole game. Offline replay (games_champion.jsonl):
                    # old gate quits early in 28-56% of games in every reachable binary cell, ALL of which have a
                    # realized bought-round high-rate ABOVE break-even -> the quits are mistakes worth ~13-16 rounds.
                    # Threshold swaps FAIL targeting (Wilson-UB overrides everywhere; prior-post overrides in NO
                    # noise cell). FIX = keep the gate but probe-buy once every `buyer_liar_reentry_period` rounds
                    # so emp2 keeps updating: noise cells reveal the true ~0.87 rate and un-latch; a genuine liar
                    # re-confirms lows and re-latches at bounded probe cost. buyer_liar_reentry_split => WITHIN-agent
                    # md5(game_id) A/B (arm0=probe, arm1=frozen control) for a confound-free contemporaneous read.
                    if pp.get("buyer_liar_reentry") \
                            and (not pp.get("buyer_liar_reentry_split") or _pers_arm(game_id) == 0):
                        since = 0
                        for e in reversed(hist):
                            if e.get("bought"):
                                break
                            since += 1
                        if since >= pp.get("buyer_liar_reentry_period", 4):
                            ok = True                                        # probe buy: gather fresh quality signal
            return {"decision": "yes" if ok else "no"}
        emp, n = _pers_history_stats(hist, pp.get("buyer_liar_bought_only"))
        # OPTIMISTIC BAYESIAN buyer (audit): condition on P(high | seller said "buy"), NOT the unconditional
        # prior p. Beta posterior seeded optimistically (a0=3,b0=1 -> mean 0.75) so cold-start TRUSTS the
        # signal and buys (quality is only revealed on bought rounds -> this is the exploration), then the
        # posterior collapses on a liar and we stop (exploitation -> avoids the negative-payoff cells). This
        # replaces the brittle prior-fallback that made the buyer play the prior and buy nothing 23-71% of games.
        hi = round(emp * n) if (emp is not None) else 0        # high-quality outcomes seen on our "buy" purchases
        lo = max(0, n - hi)
        # PRIOR-ANCHORED cold-start (audit): the buyer KNOWS the true prior p (state["p"]) — seed the Beta at
        # it (cold-start pm == p), NOT a blind 0.75. The old 0.75 made the buyer BUY from round 1 even in
        # -EV configs (prior-EV < price), eating ~5 lows before break-even -> mean -83k/game, below the
        # do-nothing floor. Anchored, it REFUSES -EV configs at cold-start (kills that bleed) and still buys
        # +EV, ratcheting up as accumulating highs prove the seller separates honestly. Convex-near-0 scoring
        # => escaping negative-payoff games is worth far more than the tiny high-EV concession.
        if p is not None:
            k = pp.get("buyer_prior_k", 2.0)
            a0, b0 = k * p, k * (1.0 - p)
        else:
            a0, b0 = pp.get("buyer_prior_a", 3.0), pp.get("buyer_prior_b", 1.0)
        pm = (a0 + hi) / (a0 + b0 + hi + lo)                   # posterior P(high | buy signal)
        if signal == "neutral":
            # "neutral" = an unparsed paragraph (15-27% of msgs); empirically P(high|neutral) ≈ p−0.10
            # (BELOW prior), so evaluating at the full prior overbuys lemons. Discount pm; self-gates via EV.
            pm = max(0.0, pm - pp.get("buyer_neutral_penalty", 0.0))
        # +EV-CELL FLOOR (audit): in a cell where the PRIOR ALONE justifies buying (p·v >= price), an explicit
        # 'buy' signal is (by KG rationality) weakly good news — P(high|buy) >= p in every logged cell — so
        # refusing there is strictly irrational. The prior-anchor is flimsy: 1-2 unlucky early lemons collapse
        # pm below break-even and we then forfeit ~15 rounds of +EV highs. Floor pm at the prior on buy-signals
        # in prior-+EV cells, UNLESS confident liar evidence (n>=liar_n and bought-record proves -EV). No-op in
        # -EV cells, on neutral, and in already-buying high-p cells -> downside bounded by construction.
        if pp.get("buyer_ev_floor") and signal == "buy" and p is not None and v is not None \
                and p * v >= price and not (n >= pp.get("buyer_liar_n", 5) and emp is not None
                                            and emp * v + (1 - emp) * u < price):
            pm = max(pm, p)
        if v is not None:
            ev = pm * v + (1 - pm) * u
            return {"decision": "yes" if ev >= price * (1 + margin) else "no"}
        return {"decision": "yes" if pm >= 0.5 * (1 + margin) else "no"}

    return {}


def _quality(state):
    """Read the seller's private quality signal if the state exposes it, else 'unknown'."""
    for k in ("current_quality", "quality", "product_quality", "is_high"):
        if k in state:
            v = state[k]
            if isinstance(v, bool):
                return "high" if v else "low"
            s = str(v).lower()
            if s in ("high", "1", "true"):
                return "high"
            if s in ("low", "0", "false"):
                return "low"
    return "unknown"
