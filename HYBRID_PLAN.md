> ⚠️ **SUPERSEDED (Aug 2026).** This early plan aimed the code-editing optimizer at rank. Three audits + live results showed the search loop is a paper artifact (0 confirmed adoptions); the real process is **freeze-and-farm + a low-noise per-cell evaluator + disciplined bug/structural fixes**. See **PROCESS.md** for how we actually operate now. Kept for history.

# Next architecture: hybrid agent + code-editing optimizer

Goal: break past ~median (2000) toward the top of the board (~3000 = ~62.5th percentile sustained).
The knob-tuning ceiling is real — the numeric core can't use the message channel, which is where the
percentile hides. Answer from the literature (OG-Narrator ~10× profit; ASTRA; Cicero): **keep the
deterministic decisions, add an LLM layer for language + inference. Do NOT let the LLM pick the numbers.**

## 1. Division of labor (why hybrid beats pure-model)
| Job | Owner | Why |
|---|---|---|
| Offer prices / accept / reject / walk | **deterministic core** (current policy) | LLMs anchor badly (open at 0.8–1.0 → no room); our core is disciplined & fast & valid |
| Read opponent's message → infer type/value | **LLM layer** | text is the signal our core throws away; enables move-1 best response |
| Write the outgoing message | **LLM layer** | persuasion/bluffing raises gains (GLEE: language helps); templates don't |
| Tune the core's knobs | **LLM optimizer** (idea 1, done) | already working |
| Add new opponent categories + rules | **LLM optimizer w/ code edit** (below, → AdaPTA) | knob ceiling: some fixes need structure, not numbers |

Numbers stay deterministic ⇒ still fast (<120 s), cheap, and analyzable for the paper. The LLM only
touches language + belief. This is the OG-Narrator recipe.

## 2. The reasoning trace format (what the LLM layer returns each turn)
Strict JSON, validated before use; if it doesn't parse or violates guardrails, fall back to the
heuristic action unchanged. Schema (see tw/trace.py REASONING_SCHEMA):
```json
{
  "opponent_type": "over_conceder|hard_anchorer|tit_for_tat|impatient|credulous|unknown",
  "belief": "their value looks ~80: they jumped 55->68->74 and said 'waiting hurts us both'",
  "tactic": "hold near anchor; they're conceding fast",
  "message": "This unit moves at a premium — 86 and it's yours today."
}
```
Only `opponent_type` (feeds our archetype/GRAB) and `message` are used to ACT; `belief`/`tactic` are
for the log + the paper. The numeric action still comes from the deterministic policy, conditioned on
the LLM's `opponent_type`.

## 3. System prompt for the LLM layer (draft)
```
You are the reasoning layer of a GLEE {family} agent. You do NOT choose prices or splits —
a separate engine does that. Your only jobs: (1) infer the opponent's hidden type and value
from their offers and message, (2) write a short message that advances our position.

Our role: {role}. Our private value/patience: {my_value}. Round {round}/{max_rounds}.
Offer history (theirs vs ours): {offer_history}
Their latest message: "{opponent_message}"
Our engine intends to: {heuristic_action}   # e.g. counter at 86, or accept

Return ONLY this JSON:
{ "opponent_type": one of [...], "belief": "...", "tactic": "...", "message": "<=2000 chars" }

Rules: the message may bluff or apply pressure but must not state a number that contradicts the
engine's action. Be concise and human. If unsure of the type, say "unknown".
```

## 4. Wiring (tw/llm_agent.py — to build)
- `enrich(game, heuristic_action) -> action`: parse opponent message from game_state.history,
  call the LLM (litellm, model from env `GLEE_LLM_MODEL`), validate JSON, then:
  - override our archetype with `opponent_type` (so GRAB/reservation adapt immediately),
  - replace the `message` field of `heuristic_action` with the LLM's message,
  - keep every NUMBER from the heuristic action.
- Guardrails: JSON must parse; message <=2000 chars; on any failure return `heuristic_action`
  unchanged. Log via trace.reasoning_trace().
- Off by default: `strategy()` uses it only if `GLEE_LLM_MODEL` is set (so the pure-heuristic
  baseline still runs free/fast — and is your ablation).
- Cost/latency: one LLM call per turn; well under the 120 s limit; you pay inference. Cache by
  (family, round) if needed.

## 5. Letting the optimizer change CODE, not just knobs (gated → AdaPTA)
The knob ceiling (bargaining no-deals couldn't be fixed numerically) is the motivation. Plan:
- Confine edits to ONE file with a fixed interface, e.g. `tw/rules.py` exposing
  `classify(features) -> archetype` and `grab(archetype) -> float`. The optimizer may add
  archetypes and branches there — nowhere else.
- Every candidate goes through: `py_compile` → smoke test → **offline replay** on logged games
  → live champion/challenger on **held-out** configs. Snapshot `tw/rules.py` to `rules_best.py`;
  revert on regression. Never touch the SDK loop, params bounds, or the fallback.
- This is where AdaPTA lives: PTA gives performance/payoff relations over opponents (transitive
  skill + cyclic matchup, the disc-game decomposition); the LLM names new categories from that
  structure and writes their `classify`/`grab` branches. Numbers still deterministic; the
  *category system* becomes learned. Interpretable, and it breaks the knob ceiling.

## 6. Order of operations (highest expected percentile first)
1. **LLM message + type-inference layer** (§3–4) — the untapped alpha; biggest jump.
2. **Anchoring discipline** (OG-Narrator FBR≈0.5) — cheap, pure heuristic.
3. **Held-out champion/challenger fitness** — so the optimizer stops chasing noise.
4. **Code-editing optimizer + AdaPTA** (§5) — the novel finale.

## Sources
- Xia et al., "Measuring Bargaining Abilities of LLMs" (OG-Narrator), ACL Findings 2024, arXiv:2402.15813
- "ASTRA: Negotiation Agent with Adaptive & Strategic Reasoning via Tool-integrated Action", arXiv:2503.07129 (verify before formal cite)
- Bakhtin et al., CICERO, Science 2022
- GLEE (Shapira et al., 2024) — language raises efficiency/gains
