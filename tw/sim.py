"""Local self-play simulator: an INSTANT evaluator so code-evolution doesn't wait on the slow server.

GLEE's server is a ~1hr evaluator; FunSearch/Eureka only work because their evaluator is instant.
This plays our policy against scripted opponent archetypes (conceder / tough / fair / tit-for-tat)
over randomized configs, in milliseconds. It is a PROXY, not ground truth — so it is used only as a
fast pre-filter: a candidate must not regress in sim before it is allowed to spend online games.

Calibrated against the V2 A/B (old policy beat V2): sim_eval.py checks the sim reproduces old > V2.
"""
import random

# opponent aggressiveness: the share/margin they hold out for, decaying by round toward a FLOOR they
# will never go below — they would rather walk (no deal) than accept less. The floor is what makes the
# sim punish over-demanding (calibrated so aggressive bargaining scores worse, matching the real dip).
OPP = {
    "conceder":    {"start": 0.35, "decay": 0.10, "floor": 0.10},   # accepts thin margins, folds fast
    "fair":        {"start": 0.50, "decay": 0.06, "floor": 0.22},
    "tough":       {"start": 0.70, "decay": 0.03, "floor": 0.38},   # holds out for a big share
    "titfortat":   {"start": 0.55, "decay": 0.05, "floor": 0.25},
}


def _opp_keep(kind, prog):
    o = OPP[kind]
    return max(o["floor"], o["start"] - o["decay"] * prog * 10)


# ---------------- bargaining ----------------
def sim_bargaining(policy, opp_kind, cfg, me="player_1"):
    """Alternating money-split. Returns our realized share (0 if no deal by the horizon)."""
    money = 100.0
    max_rounds = cfg["max_rounds"]
    other = "player_2" if me == "player_1" else "player_1"
    idx, oidx = me.split("_")[-1], other.split("_")[-1]
    state = {"money_to_divide": money, "max_rounds": max_rounds, "horizon_known": True,
             f"delta_{idx}": cfg["my_delta"], f"delta_{oidx}": cfg["opp_delta"]}
    turn = cfg["first"]                                   # who offers first
    standing = None                                      # (offerer, my_gain_for_ME)

    def my_offer(rnd):
        state.update({"round": rnd})
        a = policy.bargaining({"type": "offer"}, dict(state), me, cfg["arch"])
        mine = a.get("alice_gain") if me.endswith("1") else a.get("bob_gain")
        return float(mine if mine is not None else money / 2)

    def my_decide(rnd, my_gain):
        state.update({"round": rnd, "last_offer": {f"{me}_gain": my_gain,
                      f"{other}_gain": money - my_gain}})
        a = policy.bargaining({"type": "decision"}, dict(state), me, cfg["arch"])
        return a.get("decision") == "accept"

    for rnd in range(1, max_rounds + 1):
        prog = (rnd - 1) / max(1, max_rounds - 1)
        if turn == me:
            if standing is not None and standing[0] == other:
                if my_decide(rnd, standing[1]):
                    return standing[1] / money
            mine = my_offer(rnd)
            standing = (me, mine)
        else:  # opponent's turn
            if standing is not None and standing[0] == me:
                opp_gain = money - standing[1]
                if opp_gain / money >= _opp_keep(opp_kind, prog):     # opp accepts our split
                    return standing[1] / money
            opp_take = money * min(0.95, _opp_keep(opp_kind, prog))    # opp counters
            standing = (other, money - opp_take)
        turn = other if turn == me else me
    return 0.0


# ---------------- negotiation ----------------
def sim_negotiation(policy, opp_kind, cfg, me="player_1"):
    """Price haggling with private values. Returns our surplus/scale (0 if no deal / no ZOPA reached)."""
    scale = 100.0
    role = cfg["role"]
    is_seller = role == "seller"
    my_value = cfg["my_value"]
    opp_value = cfg["opp_value"]                          # buyer's max / seller's cost (hidden to policy)
    other = "player_2" if me == "player_1" else "player_1"
    max_rounds = cfg["max_rounds"]
    known = cfg["horizon_known"]
    base = {f"{me}_role": role, f"{me}_value": my_value, "product_price_order": scale,
            "max_rounds": max_rounds, "horizon_known": known}

    def my_offer(rnd):
        st = dict(base); st["round"] = rnd
        a = policy.negotiation({"type": "offer"}, st, me, cfg["arch"], v2=cfg.get("v2", False))
        return a.get("product_price")

    def my_decision(rnd, price):
        st = dict(base); st.update({"round": rnd, "last_offer": {"price": price}})
        a = policy.negotiation({"type": "decision"}, st, me, cfg["arch"], v2=cfg.get("v2", False))
        return a.get("decision"), a.get("product_price")

    def opp_reservation(prog):
        # opponent's walk-away price, moving toward their true value as they concede
        k = _opp_keep(opp_kind, prog)
        if is_seller:      # opponent is BUYER: reservation is a price below their value
            return opp_value * (1 - k)
        return opp_value * (1 + k)   # opponent is SELLER: asks above their cost

    def surplus(price):
        return (price - my_value) if is_seller else (my_value - price)

    turn = cfg["first"]
    standing = None                                      # price on the table (from either side)
    for rnd in range(1, max_rounds + 1):
        prog = min(0.9, (rnd - 1) / max(1, max_rounds - 1)) if known else min(0.6, (rnd - 1) / 8)
        if turn == me:
            if standing is not None and standing[0] == other:
                dec, cp = my_decision(rnd, standing[1])
                if dec == "AcceptOffer":
                    return max(0.0, surplus(standing[1])) / scale
                standing = (me, cp) if cp is not None else standing
            else:
                p = my_offer(rnd)
                standing = (me, p) if p is not None else standing
        else:  # opponent
            oppres = opp_reservation(prog)
            if standing is not None and standing[0] == me:
                price = standing[1]
                takes = (price >= oppres) if not is_seller else (price >= oppres)
                # opponent (buyer) accepts our ask if price <= their reservation; (seller) if price >= theirs
                acc = (price <= oppres) if is_seller else (price >= oppres)
                if acc:
                    return max(0.0, surplus(price)) / scale
            standing = (other, oppres)                    # opponent counters at its reservation
        turn = other if turn == me else me
    return 0.0


# ---------------- persuasion ----------------
def sim_persuasion(policy, cfg, seed=0):
    """Repeated persuasion. We play cfg['role']; opponent is scripted cfg['opp']. Returns our
    normalized self-gain in [0,1] (seller: sale rate; buyer: captured / max-capturable surplus)."""
    r = random.Random(seed)
    p, v, u, price, T = cfg["p"], cfg["v"], cfg["u"], cfg["price"], cfg["total_rounds"]
    role, opp = cfg["role"], cfg["opp"]
    hist = []
    if role == "seller":
        sales = 0
        for rnd in range(1, T + 1):
            quality = "high" if r.random() < p else "low"
            st = {"game_family": "persuasion", "phase": "seller_message", "round": rnd,
                  "total_rounds": T, "product_price": price, "p": p, "v": v, "u": u,
                  "current_quality": quality, "seller_message_type": "text", "history": list(hist)}
            msg = policy.persuasion({"type": "seller_message"}, st).get("message", "")
            sig = _sig(msg)
            # opponent buyer decides
            emp = _emp_high(hist)
            if opp == "credulous":
                buy = sig == "buy"
            elif opp == "skeptical":
                buy = sig == "buy" and (p * v + (1 - p) * u) >= price and (emp is None or emp >= price / v)
            else:  # rational: trust buy-signal up to demonstrated honesty
                ph = emp if emp is not None else p
                buy = sig == "buy" and (ph * v + (1 - ph) * u) >= price
            sales += buy
            hist.append({"round": rnd, "quality": quality, "seller_message": msg,
                         "buyer_decision": "yes" if buy else "no", "bought": buy})
        return sales / T
    else:  # we are buyer, opponent is seller
        got, best = 0.0, 0.0
        for rnd in range(1, T + 1):
            quality = "high" if r.random() < p else "low"
            best += max(0.0, (v - price) if quality == "high" else 0.0)
            if opp == "honest":
                sig = "buy" if quality == "high" else "hold"
            elif opp == "greedy":
                sig = "buy"
            else:  # kg-optimal seller
                q = _pers_kg(p, v, u, price)
                sig = "buy" if (quality == "high" or r.random() < q) else "hold"
            msg = _BUY if sig == "buy" else _HOLD
            st = {"game_family": "persuasion", "phase": "buyer_decision", "round": rnd,
                  "total_rounds": T, "product_price": price, "p": p, "v": v, "u": u,
                  "seller_message": msg, "seller_message_type": "text", "history": list(hist)}
            buy = policy.persuasion({"type": "buyer_decision"}, st).get("decision") == "yes"
            if buy:
                got += (v - price) if quality == "high" else (u - price)
            hist.append({"round": rnd, "quality": quality, "seller_message": msg,
                         "buyer_decision": "yes" if buy else "no", "bought": buy})
        return max(0.0, got) / best if best > 0 else 0.0


_BUY = "I recommend buying this product."
_HOLD = "I do not recommend buying this product."


def _sig(msg):
    m = (msg or "").lower()
    if "do not recommend" in m or "not recommend" in m or "hold" in m:
        return "hold"
    return "buy" if ("recommend" in m or "buy" in m or "strong" in m) else "neutral"


def _emp_high(hist):
    hi = lo = 0
    for e in hist:
        if _sig(e.get("seller_message")) == "buy":
            hi += e.get("quality") == "high"
            lo += e.get("quality") != "high"
    n = hi + lo
    return (hi / n) if n else None


def _pers_kg(p, v, u, price):
    denom = (1 - p) * (price - u)
    return max(0.0, min(1.0, p * (v - price) / denom)) if denom > 0 else (1.0 if v > price else 0.0)


# ---------------- batch evaluate ----------------
def _rng_cfgs(family, n, seed):
    r = random.Random(seed)
    cfgs = []
    for _ in range(n):
        arch = r.choice(["unknown", "over_conceder", "hard_anchorer", "tit_for_tat"])
        if family == "bargaining":
            cfgs.append({"max_rounds": r.choice([4, 6, 8, 12]), "my_delta": r.uniform(0.6, 0.98),
                         "opp_delta": r.uniform(0.6, 0.98), "first": r.choice(["player_1", "player_2"]),
                         "arch": arch})
        elif family == "persuasion":
            p = r.choice([1/3, 0.5, 0.8])
            vmult = r.choice([1.2, 1.25, 2, 3, 4])
            price = r.choice([1000, 10000, 100000])
            cfgs.append({"p": p, "v": price * vmult, "u": 0.0, "price": price, "total_rounds": 20,
                         "role": r.choice(["seller", "buyer"]), "seed": r.randint(0, 10**6)})
        else:
            zopa = r.random() < 0.6                       # ~60% of draws have gains-from-trade
            role = r.choice(["buyer", "seller"])
            mv = r.uniform(40, 90)
            if role == "seller":                          # my cost mv; buyer value above (zopa) or below
                ov = mv + r.uniform(5, 40) if zopa else mv - r.uniform(5, 25)
            else:                                         # my value mv; seller cost below (zopa) or above
                ov = mv - r.uniform(5, 40) if zopa else mv + r.uniform(5, 25)
            cfgs.append({"role": role, "my_value": mv, "opp_value": max(1.0, ov),
                         "max_rounds": r.choice([5, 8, 10]), "horizon_known": r.random() < 0.5,
                         "first": r.choice(["player_1", "player_2"]), "arch": arch})
    return cfgs


# opponent kind -> the archetype our classifier would assign it, so archetype-conditional strategies
# (the exploiter) are tested against an arch label that actually MATCHES the opponent, as in real play.
ARCH_OF = {"conceder": "over_conceder", "fair": "unknown", "tough": "hard_anchorer", "titfortat": "tit_for_tat"}


def evaluate(policy, family, n=240, seed=7, v2=None):
    """Mean fitness of `policy` over n randomized configs × 4 opponent archetypes. Deterministic by seed."""
    cfgs = _rng_cfgs(family, n, seed)
    kinds = list(OPP)
    tot, cnt, deals = 0.0, 0, 0
    SELLER_OPPS = ["credulous", "rational", "skeptical"]
    BUYER_OPPS = ["honest", "greedy", "kg"]
    for i, cfg in enumerate(cfgs):
        if family == "persuasion":
            opps = SELLER_OPPS if cfg["role"] == "seller" else BUYER_OPPS
            cfg = dict(cfg, opp=opps[i % len(opps)])
            f = sim_persuasion(policy, cfg, seed=cfg["seed"])
        else:
            kind = kinds[i % len(kinds)]
            cfg = dict(cfg, arch=ARCH_OF[kind])      # arch label matches the true opponent kind
            if v2 is not None:
                cfg = dict(cfg, v2=v2)
            f = sim_bargaining(policy, kind, cfg) if family == "bargaining" else sim_negotiation(policy, kind, cfg)
        tot += f; cnt += 1; deals += (f > 0)
    return {"mean_fitness": round(tot / max(cnt, 1), 4), "deal_rate": round(deals / max(cnt, 1), 3), "n": cnt}
