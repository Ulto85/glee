"""LLM move-picker (hybrid): an LLM reads the opponent + state and SUGGESTS a move; we clamp its
number to legal/sane bounds and fall back to the deterministic policy on any error/timeout. This
tests whether language reasoning beats the tuned deterministic policy — validated, per family, by the
non-noisy evaluator (never shipped on faith). Gated by GLEE_LLM. Backend: `claude -p` CLI.

Design guard: the LLM is prone to anchoring badly at bargaining numbers (lit: OG-Narrator), so it
never sets a raw price/ share unclamped — we bound it around our value and the deterministic move,
and take its accept/reject only when it's clearly profitable. Deterministic is always the safety net.
"""
import json
import os
import re
import shutil
import subprocess
import urllib.request

# --- cost guard: hard cap on API calls so we can't overspend a small balance ---
_CALLS = [0]
_FAILS = [0]
_MODEL = os.environ.get("LLM_MODEL", "claude-haiku-4-5-20251001")   # fast + cheap; NOT sonnet


def _max_calls():
    return int(os.environ.get("ANTHROPIC_MAX_CALLS", "1500"))       # ~1500*0.0008 ≈ $1.2 default ceiling


def calls_used():
    return _CALLS[0]


_KEYCACHE = []
def _key_from_file():
    """Fallback: read the API key from .anthropic_key (repo root) so we never embed the secret in an env
    file. Cached after first read; missing file -> None (LLM stays disabled, deterministic fallback runs)."""
    if _KEYCACHE:
        return _KEYCACHE[0]
    try:
        k = open(os.path.join(os.path.dirname(__file__), "..", ".anthropic_key")).read().strip()
    except Exception:
        k = None
    _KEYCACHE.append(k or None)
    return _KEYCACHE[0]


def _api(prompt, timeout=18):  # short-ish timeout + deterministic fallback so a slow/hung call can't
                               # blow the server's 120s turn limit (that got us queue-banned before)
    """Anthropic API call. Returns text or None. Respects the call cap. max_tokens must leave room for
    a thinking block (Sonnet/Opus 5 think by default) PLUS the JSON answer, else the text is truncated."""
    key = os.environ.get("ANTHROPIC_API_KEY") or _key_from_file()
    if not key or _CALLS[0] >= _max_calls():
        return None
    # Sonnet/Opus 5 auto-think on complex prompts, which eats max_tokens before the JSON answer -> "no-text"
    # fails + wasted spend. We need a terse JSON action, not deliberation -> disable thinking (verified: clean
    # text in ~17 tokens, thinking_tokens=0). Re-enable via LLM_THINK=1 (then also raise LLM_MAX_TOKENS).
    _b = {"model": _MODEL, "max_tokens": int(os.environ.get("LLM_MAX_TOKENS", "300")),
          "messages": [{"role": "user", "content": prompt}]}
    if os.environ.get("LLM_THINK") != "1":
        _b["thinking"] = {"type": "disabled"}
    body = json.dumps(_b).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        d = json.loads(r.read())
        blocks = d.get("content") or []                       # find the first TEXT block (skip thinking/other)
        text = next((b.get("text") for b in blocks
                     if isinstance(b, dict) and b.get("type") == "text" and b.get("text")), None)
        if text is None:
            _FAILS[0] += 1
            if os.environ.get("GLEE_LLM_DEBUG") and _FAILS[0] <= 3:
                print(f"  [llm] no-text response: {str(d)[:220]}", flush=True)
            return None
        _CALLS[0] += 1
        if os.environ.get("GLEE_LLM_DEBUG") and (_CALLS[0] in (1, 10, 100) or _CALLS[0] % 500 == 0):
            print(f"  [llm] OK call #{_CALLS[0]} (fails={_FAILS[0]}) model={_MODEL}", flush=True)
        return text
    except urllib.error.HTTPError as e:                       # capture the API's actual error body (why the 400)
        _FAILS[0] += 1
        if os.environ.get("GLEE_LLM_DEBUG") and (_FAILS[0] <= 3 or _FAILS[0] % 100 == 0):
            try:
                body = e.read().decode()[:400]
            except Exception:
                body = "<no body>"
            print(f"  [llm] HTTP {e.code} model={_MODEL}: {body}", flush=True)
        return None
    except Exception as e:
        _FAILS[0] += 1
        if os.environ.get("GLEE_LLM_DEBUG") and _FAILS[0] <= 3:
            print(f"  [llm] FAIL #{_FAILS[0]}: {type(e).__name__}: {str(e)[:160]}", flush=True)
        return None


def enabled(family):
    """GLEE_LLM = comma list of families to use the LLM for (e.g. 'negotiation,bargaining'), or '1'/'all'."""
    v = os.environ.get("GLEE_LLM", "")
    if not v:
        return False
    return v in ("1", "all") or family in v.split(",")


def neg_scope_ok(state):
    """Where the neg-LLM can actually BEAT deterministic: INCOMPLETE-INFO, MULTI-ROUND games (reading the
    opponent's concession path = its reservation value is the whole point). Skip ultimatums (mr=1, neg_ult
    is already optimal) and complete-info (we best-respond exactly) — there the LLM only adds cost + risk.
    NOTE: unknown-horizon games log max_rounds=None (the horizon is HIDDEN, not absent) yet run 1-99 rounds
    — they are the 83% majority AND the LLM's best case. Treat None as multi-round; exclude only mr==1."""
    if state.get("complete_information"):
        return False
    mr = state.get("max_rounds")
    if mr is None:
        return True                      # unknown horizon = inherently multi-round (the LLM's strongest case)
    return int(mr) > 1                    # known horizon: exclude only true ultimatums (mr==1)


def _claude(prompt, timeout=30):
    if not shutil.which("claude"):
        return None
    try:
        r = subprocess.run(["claude", "-p", prompt], capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except Exception:
        return None


def _json(text):
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _v2():
    return os.environ.get("GLEE_LLM_V2") == "1"


def _short(x):
    """Compact number: 1800000 -> 1.8M, 45000 -> 45k."""
    try:
        x = float(x)
    except Exception:
        return str(x)
    a = abs(x)
    if a >= 1e6:
        return f"{x/1e6:.2f}M"
    if a >= 1e3:
        return f"{x/1e3:.0f}k"
    return f"{x:.0f}"


def _trace(family, state, me):
    """Compact concession trace (last several offers) so the LLM sees the offer PATH, not just the
    standing offer — the concession rate/reciprocity is the opponent's reservation-value proxy."""
    h = state.get("history") or []
    pts = []
    if family == "negotiation":
        for e in h:
            o = e.get("offer") or {}
            p = o.get("price")
            if p is None:
                continue
            who = "you" if o.get("from_player") == me else "them"
            pts.append(f"{who} {_short(p)}")
    elif family == "bargaining":
        for e in h:
            o = e.get("offer") or {}
            g = o.get(f"{me}_gain")
            if g is None:
                continue
            who = "you" if o.get("proposer") == me else "them"
            pts.append(f"{who} offered you {_short(g)}")
    return " → ".join(pts[-8:])


_NEG_DOSS = None


def _neg_dossier_line(name, is_seller):
    """Reflexion-style per-opponent lesson from the offline dossier (logs/neg_dossier.json), free."""
    global _NEG_DOSS
    if _NEG_DOSS is None:
        try:
            _NEG_DOSS = json.loads(__import__("pathlib").Path("logs/neg_dossier.json").read_text())
        except Exception:
            _NEG_DOSS = {}
    rec = _NEG_DOSS.get(name or "")
    if not rec:
        return ""
    cr = rec.get("as_seller_opp_crosses" if is_seller else "as_buyer_opp_crosses")
    if cr is None:
        return ""
    return (f"OPPONENT MEMORY ({name}): historically meets/beats your price {cr:.0%} of the time. "
            f"Use this to judge how hard to push: higher % = they can pay more, so hold for a bigger share; "
            f"lower % = they're tight, so a fair deal beats a no-deal — but NEVER give away surplus you could keep.\n")


def _prompt(family, state, me, fallback, opp_msg, name=None):
    role = state.get(f"{me}_role", "")
    common = (f"You are a shrewd but deal-closing agent in a GLEE {family} game. Maximize YOUR payoff; "
              f"a no-deal scores 0, so don't hold out into no-deals. Reply ONLY JSON.\n"
              f"Opponent's last message: {opp_msg[:300]!r}\n")
    if family == "negotiation":
        doss = _neg_dossier_line(name, state.get(f"{me}_role") == "seller") if _v2() else ""
        trace = f"Offer path: {_trace('negotiation', state, me)}.\n" if _v2() and state.get("history") else ""
        common = common + doss
        anchor = (f"Deterministic FLOOR (a guaranteed-safe action — never do worse than this): {json.dumps(fallback)}."
                  if _v2() else f"Deterministic suggestion = {json.dumps(fallback)}.")
        return common + (
            f"You are the {role}. Your value/cost = {state.get(f'{me}_value')}. "
            f"Standing offer price = {(state.get('last_offer') or {}).get('price')}. "
            f"Round {state.get('round')} of {state.get('max_rounds')} (horizon_known={state.get('horizon_known')}). "
            + trace + anchor + "\n"
            'If deciding: {"decision":"AcceptOffer"|"RejectOffer","product_price":<counter if reject>}. '
            'If offering: {"product_price":<number>}. '
            "Buyer profits when price<value; seller when price>value. Two goals in TENSION: (a) reach a deal "
            "(no-deal=0), (b) CAPTURE MAX SURPLUS. Balance them: infer their reservation from the offer path, "
            "price just inside it, and concede only as much as needed to close — do NOT give away value to close "
            "a deal you'd win anyway. Take a clearly profitable offer rather than risk a no-deal, but don't cave early.")
    if family == "bargaining":
        money = state.get("money_to_divide")
        idx = str(me).split("_")[-1]
        d = state.get(f"delta_{idx}")
        trace = f"Offer path: {_trace('bargaining', state, me)}.\n" if _v2() and state.get("history") else ""
        shrink = (f"Each round you don't agree, the pot shrinks (your discount={d}), so patience has a real cost.\n"
                  if _v2() and d is not None else "")
        anchor = (f"Deterministic FLOOR (a guaranteed-safe action — never do worse than this): {json.dumps(fallback)}."
                  if _v2() else f"Deterministic suggestion = {json.dumps(fallback)}.")
        return common + (
            f"Split {money}. You are {me}. Standing offer to you = "
            f"{(state.get('last_offer') or {}).get(me+'_gain')}. Round {state.get('round')} of {state.get('max_rounds')}.\n"
            + trace + shrink + anchor + "\n"
            'If deciding: {"decision":"accept"|"reject"}. '
            'If offering: {"my_share":<0..1 fraction YOU keep>}. '
            "Demand as much as they will still accept (they rarely walk); don't accept a low split early when "
            "you can hold for more, but take a fair split late rather than no-deal.")
    if family == "persuasion":
        price = state.get("product_price"); p = state.get("p"); v = state.get("v"); u = state.get("u", 0.0)
        hist = state.get("history") or []
        nb = sum(1 for e in hist if e.get("bought"))
        if state.get(f"{me}_role") == "seller":
            qual = "unknown"
            for k in ("current_quality", "quality", "product_quality", "is_high"):
                if k in state:
                    qv = state[k]; qual = "high" if (qv is True or str(qv).lower() in ("high", "1", "true")) else "low"; break
            return common + (
                f"You are the SELLER. This round's product is {qual} quality. Prior P(high)={p}. Price={price}. "
                f"You earn a fixed margin on every SALE regardless of quality; the buyer tracks your credibility "
                f"across {len(hist)} prior rounds and punishes detected lies. Recommend to MAXIMIZE total sales "
                f"while keeping the buyer trusting you (push borderline units, but don't torch credibility). "
                'Reply {"recommend": true|false}.')
        return common + (
            f"You are the BUYER. Seller said: {opp_msg[:200]!r}. Prior P(high)={p}. Your value if high={v}, "
            f"if low={u}. Price={price}. You have bought {nb}/{len(hist)} prior rounds. Buy ONLY if expected "
            f"value >= price given how honest this seller has proven so far. Reply {{\"buy\": true|false}}.")
    return common + f'Deterministic suggestion = {json.dumps(fallback)}. Reply that JSON.'


def llm_move(family, actions, state, me, fallback, opp_msg="", name=None):
    """Return an LLM-chosen action, clamped to legal/sane bounds; fall back to `fallback` on any issue."""
    prompt = _prompt(family, state, me, fallback, opp_msg, name=name)
    # BUDGET GUARD: API only (hard-capped by ANTHROPIC_MAX_CALLS). The `claude` CLI fallback bills a SEPARATE
    # account, so it's off unless GLEE_LLM_CLI=1 — when the API cap is hit we fall back to deterministic, not $.
    out = _api(prompt) or (_claude(prompt) if os.environ.get("GLEE_LLM_CLI") else None)
    obj = _json(out)
    if not obj:
        return fallback
    try:
        if family == "negotiation":
            val = state[f"{me}_value"]
            is_seller = state.get(f"{me}_role") == "seller"
            if actions["type"] == "decision":
                dec = obj.get("decision")
                if dec == "AcceptOffer":
                    op = (state.get("last_offer") or {}).get("price")
                    prof = op is not None and ((op >= val) if is_seller else (op <= val))
                    if prof:
                        return {"decision": "AcceptOffer"}
                    return fallback          # LLM tried to accept a losing deal — refuse (anchoring guard)
                cp = obj.get("product_price")
                if cp is None:
                    return fallback
                cp = _clamp_neg(float(cp), val, is_seller)
                return {"decision": "RejectOffer", "product_price": round(cp, 2)}
            cp = obj.get("product_price")
            if cp is None:
                return fallback
            return {"product_price": round(_clamp_neg(float(cp), val, is_seller), 2), "message": ""}
        if family == "bargaining":
            money = state["money_to_divide"]
            if actions["type"] == "decision":
                if obj.get("decision") == "accept":
                    g = (state.get("last_offer") or {}).get(f"{me}_gain")
                    if g is not None and g / money >= 0.30:   # don't let the LLM accept a lowball
                        return {"decision": "accept"}
                    return fallback
                return {"decision": "reject"}
            sh = obj.get("my_share")
            if sh is None:
                return fallback
            sh = max(0.30, min(0.92, float(sh)))          # clamp to sane demand band
            mine = round(money * sh, 2)
            i_am_alice = str(me).endswith("1")
            return {"alice_gain": mine if i_am_alice else round(money - mine, 2),
                    "bob_gain": round(money - mine, 2) if i_am_alice else mine, "message": ""}
        if family == "persuasion":
            # binary decisions (no price to anchor on) -> safest LLM family; just parse + fall back on error
            if actions["type"] == "buyer_decision":
                b = obj.get("buy")
                return {"decision": "yes" if b else "no"} if b is not None else fallback
            rec = obj.get("recommend")
            if rec is None:
                return fallback
            if actions["type"] == "seller_recommendation":
                return {"decision": "yes" if rec else "no"}
            return {"message": "I recommend buying this product." if rec else "I do not recommend buying this product."}
    except Exception:
        return fallback
    return fallback


def message_enabled(family):
    """GLEE_LLM_MSG = families that get an LLM-written MESSAGE (numbers stay deterministic). e.g.
    'persuasion,negotiation' or '1'/'all'. This is the correctly-scoped LLM use: language, not numbers."""
    v = os.environ.get("GLEE_LLM_MSG", "")
    return bool(v) and (v in ("1", "all") or family in v.split(","))


def _msg_prompt(family, state, me, action, opp_msg):
    role = state.get(f"{me}_role", me)
    opp = f"Opponent just said: {opp_msg[:200]!r}. " if opp_msg else ""
    if family == "persuasion":
        # PROMPT V2 (2026-08-26, winner-mined from top pers agents): imperative + explicit quality/price keywords
        # + reputation-staking on BUY; credibility-contrast frame + NO 'recommend'/market language on HOLD. The
        # top sellers use tiny, plain, in-domain lines ("high quality, worth the price, I checked it myself" /
        # "I only get paid when you buy, so a hold means something"), never finance metaphors or forecasts.
        if _sig_of(action) == "buy":
            return (f"You are a repeat seller in a trust game; the buyer tracks your record and rewards proven "
                    f"credibility. {opp}This unit IS worth buying this round. Write ONE short, plain, confident "
                    f"sentence telling the buyer to buy it NOW — use direct words like 'high quality', 'recommend', "
                    f"'worth the price', or 'buy'. You may stake your reputation ('I checked it myself'). Stay "
                    f"strictly about THIS product: no markets, no forecasts, no metaphors. Reply with only the sentence.")
        return (f"You are a repeat seller in a trust game; your credibility is your only asset. {opp}This unit is "
                f"NOT worth it this round. Write ONE short, plain sentence telling the buyer to SKIP it this time, "
                f"framed as protecting your credibility (e.g. 'I only make money when you buy, so when I say sit "
                f"one out, it means something — pass on this one'). Do NOT use the word 'recommend'. No markets, "
                f"no forecasts, no metaphors. Reply with only the sentence.")
    if family == "bargaining":
        money = state.get("money_to_divide"); mine = action.get("alice_gain") or action.get("bob_gain")
        return (f"You are splitting {money} and just proposed keeping ~{mine} for yourself. {opp}"
                f"Write ONE short, firm-but-amicable sentence to get them to ACCEPT now (anchor on fairness/"
                f"patience/mutual delay cost). Reply with only the sentence.")
    # negotiation
    return (f"You are the {role} and just offered price {action.get('product_price')}. {opp}"
            f"Write ONE short, persuasive sentence to get them to accept (cite a credible floor/BATNA or "
            f"value framing; don't reveal your true reservation). Reply with only the sentence.")


def _sig_of(action):
    m = (action.get("message") or "").lower()
    return "hold" if ("not recommend" in m or "hold" in m or action.get("decision") == "no") else "buy"


def llm_message(family, state, me, action, opp_msg=""):
    """Return an LLM-written message for our already-decided action; fall back to the deterministic bank
    message on cap/timeout/error. Short timeout so it can never blow the server's turn limit."""
    fallback = action.get("message") or ""
    if _CALLS[0] >= _max_calls():
        return fallback
    out = _api(_msg_prompt(family, state, me, action, opp_msg), timeout=7)
    if not out:
        return fallback
    msg = out.strip().strip('"').split("\n")[0][:300]
    return msg or fallback


def _clamp_neg(price, value, is_seller):
    """Keep a negotiation price profitable-ish and within a sane band around our value."""
    if is_seller:                                          # never below cost; cap the ask
        return max(value * 1.0, min(price, value * 2.5))
    return min(value * 1.0, max(price, value * 0.2))      # buyer never above value; floor the bid
