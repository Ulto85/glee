"""Search: quality-diversity archive + Thompson bandit + Reflexion memory over rulesets.

The optimizer stops being greedy hill-climbing: it keeps an ARCHIVE of the best ruleset per
behavior cell (MAP-Elites), a BANDIT picks which region to explore next, and a LESSON memory
(Reflexion) plus forced hypotheses make the LLM proposer scientific instead of noisy.
The behavior descriptor is a PLACEHOLDER now (measured share/no-deal) — PTA plugs in here later.
"""

import json
import math
import random
import statistics
from pathlib import Path

import tw.params as params

LOG = Path("logs")
LOG.mkdir(exist_ok=True)
ARCHIVE_PATH = LOG / "archive.json"
LESSONS_PATH = LOG / "lessons.json"
HYPO_PATH = LOG / "hypotheses.jsonl"


# ---------- behavior descriptor (PTA replaces this later) ----------
def behavior_cell(share, no_deal):
    """Discretize measured behavior into a MAP-Elites cell. Swap for PTA coords later."""
    if share is None:
        return "na"
    sb = min(4, int(share * 5))              # capture/aggression bucket 0..4
    nb = min(3, int((no_deal or 0) * 4))     # no-deal bucket 0..3
    return f"{sb}-{nb}"


# ---------- fitness from a batch of played games ----------
def get_from(prms, path):
    node = prms
    for p in path.split("."):
        node = node[p]
    return node


def cov_key(game):
    """Config covariate (unaffected by our policy) for CUPED variance reduction."""
    fam = game.get("game_family")
    st = game.get("game_state") or {}
    # OUR FIXED SEAT (your_player), NOT current_player: role/value must reflect the seat WE played all game.
    # current_player is whoever acts last at record time — in persuasion the buyer always decides last, so it
    # mislabeled 100% of our seller games as 'buyer' (and read value off the wrong seat). your_player is stable.
    me = game.get("your_player") or st.get("current_player") or "p"
    if fam == "negotiation":
        role = st.get(f"{me}_role", "?")
        v, scale = st.get(f"{me}_value"), (st.get("product_price_order") or 100)
        vb = round(v / scale, 1) if isinstance(v, (int, float)) and scale else "?"
        return f"neg|{role}|v{vb}|h{int(bool(st.get('horizon_known', True)))}"
    if fam == "bargaining":
        idx = str(me).split("_")[-1]
        d = st.get(f"delta_{idx}")
        # round(d,2) not round(d,1): round(0.95,1)==0.9 (float 0.95 stored as 0.9499…), which POOLED
        # true-δ 0.90 and 0.95 into one "d0.9" cell — they have materially different realized shares
        # (0.90≈0.46 drag, 0.95≈0.51/0.54 healthy). Pooling co-authored the hz_bank mis-scope (Fable 2026-08-16).
        return f"barg|d{round(d, 2) if isinstance(d, (int, float)) else '?'}|mr{st.get('max_rounds', '?')}"
    if fam == "persuasion":
        role = st.get(f"{me}_role", "?")     # role-key (task #17): seller-side levers must be scored vs
        # DIFFICULTY = r=v/price (Fable#10 F3), NOT raw v (a scale): a game's hardness is the value/price
        # ratio, and the field r-grid is {1.2,1.25,2,3,4}. Include seller_message_type (text vs binary) —
        # buyers behave very differently by channel. coarse_cell keeps role+p+r so cells no longer pool.
        vv, pr = st.get("v"), st.get("product_price")
        rb = round(vv / pr, 2) if isinstance(vv, (int, float)) and isinstance(pr, (int, float)) and pr else "?"
        return f"pers|{role}|p{st.get('p', '?')}|r{rb}|m{st.get('seller_message_type', '?')}"
    return fam or "?"


def _one_fitness(o):
    r = o.get("result") or {}
    me = o.get("your_player", "player_1")
    ot = "player_1" if me == "player_2" else "player_2"
    mine, th = r.get(f"{me}_payoff"), r.get(f"{ot}_payoff")
    if r.get("outcome") == "no_deal" or (mine == 0 and th == 0):
        return 0.0
    if mine is not None and th is not None and abs(mine) + abs(th) > 0:
        return mine / (abs(mine) + abs(th))
    return 0.0


def game_records(outcomes):
    """Per-game (fitness, cov) records — cov tags the config for CUPED adjustment."""
    return [(_one_fitness(o), o.get("cov", "?")) for o in outcomes]


def game_fitness(outcomes):
    """Per-game fitness list: our_share if a deal happened, else 0 (scale-free)."""
    return [_one_fitness(o) for o in outcomes]


def outcome_metrics(outcomes):
    """Scale-free metrics over a batch: fitness = mean(our_share if deal else 0)."""
    vals, shares, nd, n = [], [], 0, 0
    for o in outcomes:
        n += 1
        r = o.get("result") or {}
        me = o.get("your_player", "player_1")
        ot = "player_1" if me == "player_2" else "player_2"
        mine, th = r.get(f"{me}_payoff"), r.get(f"{ot}_payoff")
        deal = not (r.get("outcome") == "no_deal" or (mine == 0 and th == 0))
        if not deal:
            nd += 1; vals.append(0.0); continue
        if mine is not None and th is not None and abs(mine) + abs(th) > 0:
            s = mine / (abs(mine) + abs(th)); shares.append(s); vals.append(s)
        else:
            vals.append(0.0)
    return {
        "n": n,
        "fitness": round(sum(vals) / n, 3) if n else None,
        "share": round(sum(shares) / len(shares), 3) if shares else None,
        "no_deal": round(nd / n, 3) if n else None,
    }


# ---------- quality-diversity archive ----------
class Archive:
    def __init__(self):
        self.cells = {}
        if ARCHIVE_PATH.exists():
            try:
                self.cells = json.loads(ARCHIVE_PATH.read_text())
            except Exception:
                pass

    def save(self):
        ARCHIVE_PATH.write_text(json.dumps(self.cells, indent=2))

    def add(self, prms, metrics):
        """Insert if this ruleset beats the current elite of its behavior cell."""
        cell = behavior_cell(metrics.get("share"), metrics.get("no_deal"))
        cur = self.cells.get(cell)
        if cur is None or (metrics.get("fitness") or 0) > (cur["metrics"].get("fitness") or 0):
            self.cells[cell] = {"params": prms, "metrics": metrics}
            self.save()
            return True, cell
        return False, cell

    def best(self):
        if not self.cells:
            return None
        return max(self.cells.values(), key=lambda c: c["metrics"].get("fitness") or 0)

    def keys(self):
        return list(self.cells.keys())


# ---------- Thompson bandit over archive cells (which region to explore) ----------
class ThompsonBandit:
    def __init__(self):
        self.obs = {}                        # cell -> list of child fitnesses

    def update(self, cell, reward):
        self.obs.setdefault(cell, []).append(reward)

    def pick(self, cells):
        """Thompson sampling: sample each cell's mean, pick the argmax (optimistic if unseen)."""
        best, best_v = None, -1e9
        for c in cells:
            xs = self.obs.get(c, [])
            if not xs:
                s = 1.0 + random.random()    # optimism for unexplored regions
            else:
                m = sum(xs) / len(xs)
                sd = (statistics.pstdev(xs) if len(xs) > 1 else 0.3) / math.sqrt(len(xs))
                s = random.gauss(m, sd + 1e-6)
            if s > best_v:
                best, best_v = c, s
        return best


# ---------- built-in mutation proposer (works with NO LLM key) ----------
def mutate(base_params, k=2, allowed=None):
    """Perturb 1-k knobs (restricted to `allowed` keys if given). Returns (patch, hypothesis)."""
    keys = allowed if allowed else list(params.BOUNDS)
    patch = {}
    for key in random.sample(keys, min(k, len(keys))):
        lo, hi = params.BOUNDS[key]
        cur = get_from(base_params, key)
        step = (hi - lo) * 0.12
        patch[key] = round(min(hi, max(lo, cur + random.uniform(-step, step))), 3)
    return patch, f"random perturbation of {list(patch)}"


# ---------- Reflexion lesson memory + hypothesis log ----------
def load_lessons(family=None):
    """Reflexion lessons for `family` (family-scoped so one family's lessons can't mislead another).
    Entries tagged with a different family are excluded; None-tagged/legacy entries apply to all."""
    if not LESSONS_PATH.exists():
        return []
    try:
        data = json.loads(LESSONS_PATH.read_text())
    except Exception:
        return []
    out = []
    for l in data:
        if isinstance(l, dict):
            if family is None or l.get("fam") in (None, family):
                out.append(l.get("text", ""))
        else:                                   # legacy plain-string lesson: applies to all
            out.append(l)
    return out


def add_lesson(text, family=None):
    data = []
    if LESSONS_PATH.exists():
        try:
            data = json.loads(LESSONS_PATH.read_text())
        except Exception:
            data = []
    data.append({"fam": family, "text": text})
    LESSONS_PATH.write_text(json.dumps(data, indent=2))


def log_hypothesis(record):
    with HYPO_PATH.open("a") as fh:
        fh.write(json.dumps(record) + "\n")
