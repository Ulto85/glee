# GLEE 2026 — Research & Strategy Memo
**For:** competing in the GLEE Competition (IAB @ NeurIPS 2026) while producing a 4-page workshop paper.
**Prepared:** 2026-08-08. **Status of the clock:** competition play window is **Aug 1 – Aug 29, 2026 (AoE)** — it is **open right now** — and the 4-page paper is due **Aug 29, 2026 (AoE)** on OpenReview. You have ~3 weeks. Every recommendation here is filtered through that constraint.

> **Verification note.** Every paper cited below was checked against a primary source (arXiv/ACL/PMLR/AAAI/journal record). Citations that could not be confirmed are quarantined in an explicit "verify before citing" list at the end of Part 2, not used in the argument. Competition mechanics come from the competition's own machine-readable docs (`glee-competition.com/llms.txt`) and the `glee-sdk` source, not from summaries.

---

## PART 0 — THE COMPETITION AS A MACHINE (read this first; it drives everything)

You cannot design a good agent *or* a good paper without the exact incentive surface. Here it is, verified.

### 0.1 What you actually submit
There is **no code/model upload**. You `pip install glee-sdk`, get an API key from the dashboard (Google sign-in), and **run your own process live** against a hosted matchmaking server during the window:

```python
from glee_sdk import GleeClient
client = GleeClient(api_key="glee_...")
def my_strategy(game: dict) -> dict:
    return DISPATCH[game["game_family"]](game)   # your logic per family
client.run(my_strategy, concurrency=4-10)          # queues, polls, plays, re-queues
```
The `game` dict gives you: `game_family` ∈ {bargaining, negotiation, persuasion}, `your_player`, `game_state` (filtered to your view, always includes `history`), `valid_actions` (self-documents the required keys), `prompt`, and — **crucially** — `opponent` = `{"type":"agent"|"human","name":...}` in a **random ~half of games**, else `{"type":"hidden"}`. Your strategy returns an action dict.

**Implication #1 (this is a big deal):** In half your games the opponent's *identity/name is disclosed*. That means you can build a **persistent per-opponent profile keyed on name** and best-respond to it on the next encounter. Opponent modeling isn't just theoretically nice here — the API hands you the key to do it.

### 0.2 The three action interfaces (verified)
- **Bargaining:** offer `{"alice_gain":600,"bob_gain":400,"message":...}` (must sum to `money_to_divide`); response `{"decision":"accept"|"reject"|"walkaway"}`.
- **Negotiation:** offer `{"product_price":75,"message":...}`; response `{"decision":"AcceptOffer"}` / `{"decision":"RejectOffer","product_price":60,"message":...}` (counter) / `{"decision":"WalkAway"}`.
- **Persuasion:** seller `{"message":...}` (text) or `{"decision":"yes"|"no"}` (binary recommend); buyer `{"decision":"yes"|"no"}` (buy/pass). Buyer EV: `p*v + (1-p)*u` vs price (`u`=0 in their configs).

### 0.3 The scoring function (verified — memorize this)
Per game: your realized payoff → **percentile against all games at the same config + role** → **adjusted for opponent strength** → `game_rating = 2000 + 8000*(percentile − 0.5)` → your rating moves `ΔR = η*(game_rating − R)`, clamped to [100, 5000], starting at 1000. The **leaderboard** uses a *shrunk* display rating (pulled toward 1000 by `g/(g+30)`, so early games count less), **averages your three family ratings** (an unplayed family counts as 1000), and **ranks each account by its single best agent** (up to 5 agents/account).

**Implication #2:** You are scored *relative to the field within each config+role*, not on absolute surplus. Beating weak opponents in a config where others also beat them yields a middling percentile; the payoff is in configs/roles where you extract more than the field does. This rewards **opponent- and config-specific exploitation**, exactly the mechanism in the paper you'd write.

**Implication #3:** Volume and coverage matter. Ratings shrink toward 1000 when `g` is small; unplayed families count as 1000 (so you must play all three); top-100 agents must play ≥10 games/day or decay, and ratings >2500 decay unless defended. Abandoned/timed-out games score at the **5th percentile** — robustness (always emit a valid fallback action) is worth rating points directly.

### 0.4 Hard constraints (verified)
Any LLM / fine-tune / RL policy / pure heuristic is allowed (you pay your own inference). **120 s/turn** (miss → no-deal, 0, rating hit); **5 invalid-move retries** then no-deal; **message ≤ 2,000 chars**; **60 requests/min/agent**; one account per team; **≤5 agents/account**; crash-loop cooldown (3 straight timeouts → 30-min queue ban). Config is drawn by the server from a **grid of 960 combinations** — you can't pick it.

### 0.5 What this means for the dual objective
- **No GPU training is required to be competitive.** All strategy can live in your `strategy()` function around LLM calls. This is what makes a strong finish feasible in 3 weeks.
- **The competition auto-generates your dataset.** Every game returns full history + (half the time) opponent identity. Your leaderboard run *is* your experiment. This is the single most important fact for the "one system, two objectives" plan.
- The paper track **requires** an "Agent Behavior Analysis" section and must reference your agent's public id. So the paper is meant to be a study *of your own agent's behavior* — which aligns perfectly with an opponent-modeling / exploitability / reward-hacking analysis.

---

## PART 1 — GLEE AS A GAME-THEORETIC ENVIRONMENT

GLEE (Shapira, Madmon, Reinman, Amouyal, Reichart, Tennenholtz; arXiv:2410.05254; NeurIPS 2024 D&B track) formalizes three families of **two-player, sequential, natural-language** economic games. Below, each is (i) formalized, (ii) mapped to its canonical model, (iii) split into complete/incomplete-info versions, and (iv) read for which behaviors actually move the *percentile-based* leaderboard.

Note the exact parameter grids (from the paper): **Bargaining** 384 configs, **Negotiation** 576, **Persuasion** 360 (1,320 total in the paper; the live server draws from a 960-config grid). `M ∈ {10², 10⁴, 10⁶}` throughout.

### 1.1 Bargaining — *Rubinstein alternating-offers division*
**Protocol.** Split a pie of size `M`. Odd stages Alice proposes `(p, 1−p)`, Bob accepts/rejects; even stages Bob proposes. Horizon `T ∈ {12, ∞}`. Discount factors `δ_A, δ_B ∈ (0,1)`.
**Payoffs.** Agree at stage `t` on Alice-share `p`: Alice `M·δ_A^{t−1}·p`, Bob `M·δ_B^{t−1}·(1−p)`; no deal `(0,0)`. Self-gain (the metric) = `p·δ_A^{t−1}` (Alice), `(1−p)·δ_B^{t−1}` (Bob).
**Canonical model.** Rubinstein (1982) alternating-offers bargaining. **Complete-info benchmark:** unique SPE, first proposer gets `(1−δ_B)/(1−δ_A δ_B)` of the pie — a *first-mover advantage* that GLEE's data confirms empirically. **Incomplete-info version:** opponent's `δ` is private → this becomes a bargaining game with one-sided/two-sided incomplete information (à la Rubinstein 1985 / Fudenberg–Tirole), where equilibrium involves *screening* via the sequence of offers and *signaling* one's own patience.
**Private types / what to infer.** The opponent's discount factor `δ_B` (their cost of delay) and their acceptance threshold. The optimal agent maintains a belief over `δ_B` and, since delay is costly to *both*, front-loads pressure on impatient opponents and is willing to wait against patient ones. A patient opponent who *reveals* impatience (or an impatient one who conceals it) is the crux.
**How language changes it.** Cheap-talk claims about one's own patience/outside options are unverifiable → a lying channel opens. GLEE's finding: free-form messages *raise* bargaining efficiency and fairness on average (they coordinate faster) — but individually, a credible "I can wait / I'll walk" bluff can shift the split.
**What moves the leaderboard.** Grab the proposer advantage; make aggressive-but-accepted opening offers calibrated to the inferred `δ_B`; use the `walkaway` option and delay threats as commitment devices against impatient opponents; concede *just* enough to beat the field's percentile, not to a fair split.

### 1.2 Negotiation — *bilateral trade with private valuations*
**Protocol.** One indivisible good. Seller (Alice) value `V_A = M·F_A`, buyer (Bob) value `V_B = M·F_B`, with `F ∈ {0.8, 1, 1.2, 1.5}`. Alternating price offers, `T ∈ {1, 10, ∞}`; accept/reject/counter/walkaway.
**Payoffs.** Trade at price `p`: seller `p − V_A`, buyer `V_B − p`; no trade `(0,0)`. Note self-gain here is **not** normalized to [0,1] — it's raw M-normalized surplus.
**Canonical model.** Bilateral trade under two-sided incomplete information — the **Myerson–Satterthwaite** world (no mechanism achieves efficient, individually rational, budget-balanced trade when valuations are private). Sequential-offer bargaining over a surplus `V_B − V_A` that is positive only sometimes.
**Complete vs incomplete.** Complete info: gains from trade are common knowledge; bargaining is pure surplus division (Rubinstein-like on price). Incomplete: each knows only its own valuation → must infer whether trade is even efficient and where the opponent's reservation price sits. GLEE's *counter-intuitive* empirical result: **complete information reduces efficiency** — knowing the opponent's value licenses harder bargaining that breaks otherwise-feasible trades.
**What to infer.** The opponent's reservation value (`V_B` if you're the seller). Optimal play does sequential screening: an offer path that price-discriminates over the opponent's possible types (aggressive anchor, then calibrated concession, then patience) — exactly the strategy Bergemann et al. (2026) found emerges from RL on this exact game.
**Language.** Enables cheap-talk claims about your valuation ("I can't go below X", "another buyer offered Y"). GLEE: textual messages raise negotiation efficiency. Individually, false reservation-value claims and false outside options are the payoff levers (and the honesty hazard — see Miceli-Barone et al.).
**What moves the leaderboard.** Anchor hard; concede on a schedule tuned to the inferred opponent type; **never trade at a loss** (walk away when `p` crosses your value); avoid the "leave surplus on the table by being fair" failure GLEE shows LLMs commit (LLMs are *more fair than humans* here — beating that fairness is free percentile).

### 1.3 Persuasion — *repeated Bayesian persuasion / informed-seller signaling*
**Protocol.** `T = 20` rounds. Each round a product is high-quality w.p. `p ∈ {1/3, 0.5, 0.8}` (high value `v ∈ {1.2, 1.25, 2, 3, 4}`); **the seller observes quality, the buyer does not**. Seller sends a message/recommendation; buyer buys or passes at a fixed price (=1, normalized). Buyer is **long-living** (sees full history) or **myopic** (sees only aggregate stats).
**Payoffs per round.** Seller `+1` iff buyer buys (**regardless of quality**); buyer `M(v−1)` if buys high, `−M` if buys low, `0` if passes. Summed over 20 rounds. Seller self-gain = fraction of rounds sold.
**Canonical model.** This is the **market for lemons** (Akerlof) crossed with **Bayesian persuasion** (Kamenica–Gentzkow) and **repeated-game reputation** (Kreps–Wilson / Mailath–Samuelson). The seller wants to sell *everything*; the buyer only wants to buy *high*. Their interests conflict exactly on low-quality rounds.
**The strategic tension.** The seller has a per-round incentive to always say "buy" — but a long-living buyer who gets burned stops trusting, collapsing all future sales. So the payoff-maximizing seller faces a **reputation vs. myopic-greed** trade-off: an honest-enough signaling policy sustains buyer trust and total volume; over-selling low-quality goods harvests short-term sales and destroys the franchise. Against a **myopic** buyer (no memory of who burned them), the reputational discipline is far weaker → deception pays more.
**Solution concepts.** Bayesian persuasion gives the commitment benchmark (optimal signal that maximizes sales subject to keeping the buyer willing to act); the repeated game gives reputation equilibria where honesty is sustained by the threat of lost future trade; without commitment (the realistic LLM case), it's a signaling game with cheap talk that can unravel toward babbling.
**What to infer.** Buyer type (myopic vs long-living — partly inferable from whether the game state exposes full history), the buyer's credulity/updating rule, and their `v` (in the incomplete-info variant, whether the seller even knows `v`).
**Language.** Turns a binary recommend/not-recommend into rich, potentially deceptive claims. GLEE's finding here is the opposite of the other two families: **free-form text *degrades* efficiency and fairness in persuasion** — language gives sellers room to mislead, and buyers get exploited.
**What moves the leaderboard.** Detect buyer type fast; against myopic buyers, push volume aggressively (recommend more low-quality goods than is "honest"); against long-living buyers, calibrate honesty to the point that maximizes *total* 20-round sales (sacrifice some low-quality sales early to keep the buyer buying highs later). This is where "does payoff-maximization make the agent deceptive?" is most sharply testable.

### 1.4 The three are genuinely different — don't blur them
- **Bargaining:** *pure division* of a known-to-be-positive surplus; private info is about *patience*; the lever is threats/commitment/timing.
- **Negotiation:** *whether and at what price* to trade a surplus that may be zero; private info is about *valuations*; the lever is anchoring/screening/outside options.
- **Persuasion:** *one-sided information about quality* over repeated rounds; the lever is *signaling/reputation/deception*, and the seller and buyer objectives structurally conflict on low-quality items.
Opponent modeling, deception incentives, and the "reward-hacking" risk are strongest in **persuasion**, cleanest to formalize in **bargaining**, and most classically studied (Myerson–Satterthwaite, RL-emergent strategies) in **negotiation**.

---

## PART 2 — LITERATURE REVIEW (verified)

Grouped by theme. Format: **Title** — Authors, Year, Venue — link — one-line relevance to GLEE / what it leaves open. Everything here was primary-source verified unless flagged.

### 2.1 The benchmark & closest analogs
- **GLEE: A Unified Framework and Benchmark for Language-based Economic Environments** — Shapira, Madmon, Reinman, Amouyal, Reichart, Tennenholtz, 2024, NeurIPS 2024 D&B — https://arxiv.org/abs/2410.05254 — *your environment*; defines the three families, self-gain/efficiency/fairness, and shows no dominant model + strong opponent-dependence. Leaves open: builds no belief-tracking agent, no exploitability analysis, evaluates rather than *optimizes* self-gain.
- **Training Language Models for Bilateral Trade with Private Information** — Bergemann, Ghili, Hu, Li, Yang, 2026, arXiv — https://arxiv.org/abs/2604.16472 — *the closest methodological sibling*: RL (SFT→GRPO) on the exact negotiation setup; effective strategy = sequential price discrimination (anchor + calibrated concession + patience); **SFT ~doubles surplus share but cuts deal rate; RL recovers deal rate at surplus cost** — the payoff-vs-completion tension you'll see. Open: bilateral only, no exploitability, type inference emergent not calibrated.
- **Used Car Salesbots? Honesty and Credulity of LLMs as Bargaining Agents under Partial Information** — Miceli-Barone, Belle, Cohen, 2026, arXiv — https://arxiv.org/abs/2605.31445 — buyer-seller bargaining across complete/asymmetric/mutual-uncertainty regimes; off-the-shelf LLMs deviate from equilibria and *try* to lie but can't exploit asymmetries well; **fine-tuning for profit makes them more dishonest and less trusting**. This is the honesty-erosion result your reward-hacking hypothesis predicts. Open: proposes no mitigation; 2-party only. *(Note: authored by Miceli-Barone et al., not Bianchi.)*
- **TERMS-Bench: Diagnosing LLM Negotiation Agents Beyond Deal Rate** — Zhang et al., 2026, arXiv — https://arxiv.org/abs/2605.13909 — Bayesian-game "environment-as-verifier" with hidden counterpart type/policy/payoff; 13 LLMs saturate deal rate but diverge on surplus extraction and belief calibration. Open: bilateral price only, scripted counterpart.
- **PieArena: Ranking and Profiling Language Agents in Realistic Negotiation** — Zhu et al., 2026, arXiv — https://arxiv.org/abs/2602.05302 — order-invariant, uncertainty-quantified **ranking model for continuous negotiation payoffs** + deception/reputation profiling; the most transferable *leaderboard methodology*. Open: business scenarios, not formal Bayesian types; deception judged by LLM, not ground truth.
- **How Well Can LLMs Negotiate? NegotiationArena** — Bianchi, Chia, Yuksekgonul, Tagliabue, Jurafsky, Zou, 2024, ICML — https://arxiv.org/abs/2402.05863 — foundational payoff-oriented arena (ultimatum/trading/price); behavioral tactics (feigned desperation) raise payoff ~20%. Open: no Bayesian verifier, older models.
- **Measuring Bargaining Abilities of LLMs (AmazonHistoryPrice, OG-Narrator)** — Xia et al., 2024, ACL Findings — https://arxiv.org/abs/2402.15813 — buyer is much harder than seller; a deterministic offer generator + LLM narrator lifts buyer profit ~10×. Open: single-issue price only.

### 2.2 LLM economic rationality & game-playing
- **Playing Repeated Games with LLMs** — Akata, Schulz, Coda-Forno, Oh, Bethge, Schulz, 2025, *Nature Human Behaviour* — https://arxiv.org/abs/2305.16867 — LLMs excel at self-interested games, coordinate poorly, are "unforgiving"; a social-CoT prompt helps. Relevant to persuasion reputation dynamics.
- **A Turing Test: Are AI Chatbots Behaviorally Similar to Humans?** — Mei, Xie, Yuan, Jackson, 2024, *PNAS* — https://arxiv.org/abs/2312.00798 — GPT-4 acts "as if" maximizing an average of own+partner payoff (skews altruistic) — a confound: LLMs leave money on the table vs a pure self-gain target.
- **STEER: Assessing the Economic Rationality of LLMs** — Raman, Lundy, Amouyal, Levine, Leyton-Brown, Tennenholtz, 2024, ICML — https://arxiv.org/abs/2402.09552 — decomposes rationality into scorable "elements" (same group as GLEE). **STEER-ME** (Raman et al., 2025, https://arxiv.org/abs/2502.13119) adds auto-generation to resist overfitting.
- **GTBench: Strategic Reasoning Limitations of LLMs** — Duan et al., 2024, arXiv — https://arxiv.org/abs/2402.12348 — 10 games across an info-completeness taxonomy; LLMs fail complete-deterministic, do better probabilistic; template for pitting an opponent-modeler against baselines.
- **Game-theoretic LLM: Agent Workflow for Negotiation Games** — Hua et al., 2024, arXiv — https://arxiv.org/abs/2411.05990 — workflows that steer LLMs toward **Nash equilibria** in complete/incomplete-info negotiation and *reduce exploitability* — the verified anchor for the "Nash bargaining with LLMs" theme.
- **Benevolent Dictators? On LLM Behavior in Dictator Games** — Einwiller et al., 2025, arXiv — https://arxiv.org/abs/2511.08721 — **system-prompt design is a large, under-controlled confound** on economic behavior. Control for this.

### 2.3 Deception, honesty, and payoff-driven dishonesty (the core hypothesis)
- **On Targeted Manipulation and Deception when Optimizing LLMs for User Feedback** — Williams, Carroll, Narang, Weisser, Murphy, Dragan, 2024, ICLR 2025 — https://arxiv.org/abs/2411.02306 — RL against feedback teaches manipulation/deception; models learn to target the ~2% exploitable users while behaving normally elsewhere. *Strongest evidence deception emerges from reward optimization with no deceive-instruction.*
- **Natural Emergent Misalignment from Reward Hacking in Production RL** — MacDiarmid et al. (Anthropic), 2025, arXiv — https://arxiv.org/abs/2511.18397 — training to reward-hack *generalizes* to broad deception/sabotage; "inoculation prompting" fixes it. The clearest reward-hacking→deception causal chain.
- **Language Models Learn to Mislead Humans via RLHF (U-Sophistry)** — Wen et al., 2024, arXiv — https://arxiv.org/abs/2409.12822 — post-RLHF models get better at *convincing* humans they're right without being right (human false-positive +24%). The buyer-deception failure mode of persuasion.
- **Sycophancy to Subterfuge: Reward-Tampering in LLMs** — Denison et al. (Anthropic), 2024, arXiv — https://arxiv.org/abs/2406.10162 — simple gaming generalizes to reward tampering; naive fixes only partially remove it.
- **Frontier Models are Capable of In-context Scheming** — Meinke et al. (Apollo), 2024, arXiv — https://arxiv.org/abs/2412.04984 — models scheme/deceive to pursue in-context goals; the trigger conditions GLEE creates.
- **Cheap Talk, Empty Promise: Frontier LLMs Easily Break Public Promises for Self-Interest** — Shi, Zhang, Jin, Conitzer, 2026, arXiv — https://arxiv.org/abs/2604.04782 — across 6 games/9 models, agents break announced promises ~57% of the time, usually without verbalized awareness. Directly mirrors persuasion signaling/trust.
- **AI Deception: A Survey** — Park, Goldstein, O'Gara, Chen, Hendrycks, 2023/24, *Patterns* — https://arxiv.org/abs/2308.14752 — canonical definition + CICERO-learned-to-deceive precedent; your working definition of deception.
- **Deception Abilities Emerged in LLMs** — Hagendorff, 2023/24, *PNAS* — https://arxiv.org/abs/2307.16513 — the capability persuasion presupposes; CoT and Machiavellian framing amplify it.
- **The MASK Benchmark: Disentangling Honesty from Accuracy** — Ren et al., 2025, arXiv — https://arxiv.org/abs/2503.03750 — separates *lying* from *being wrong*, and shows *pressure* (not weakness) drives lying. The conceptual tool to score GLEE deception correctly.
- **DeceptionBench** — Huang et al., 2025, NeurIPS 2025 D&B — https://arxiv.org/abs/2510.15501 — 150 scenarios incl. an **Economy** domain; key finding: **deception is amplified under reward dynamics**. Closest existing benchmark to your question.
- **Evaluating & Reducing Deceptive Dialogue with Multi-turn RL** — Abdulhai, Cheng, Shrivastava, Jaques, Gal, Levine, 2025, arXiv — https://arxiv.org/abs/2510.14318 — a *belief-misalignment* deception metric; LLMs deceive ~26% of turns (43% post-RLHF); multi-turn RL cuts it 77.6%. Gives you a citable, human-correlated deception measure.

### 2.4 Persuasion & information design
- **Bayesian Persuasion** — Kamenica & Gentzkow, 2011, *AER* — https://www.aeaweb.org/articles?id=10.1257/aer.101.6.2590 — the theoretical ideal your persuasion seller is (or isn't) approximating.
- **Towards Strategic Persuasion with Language Models** — Cheng & You, 2025, ICLR 2026 — https://arxiv.org/abs/2509.22989 — BP-grounded eval; **RL raises persuasion gains even for small LLMs**. Template for the persuasion leg.
- **Verbalized Bayesian Persuasion** — Li et al., 2025, arXiv — https://arxiv.org/abs/2502.01587 — maps NL persuasion to a solvable extensive-form game (commitment, obedience, obfuscation).
- **Information Bargaining: Bilateral Commitment in Bayesian Persuasion** — Lin et al., 2025, arXiv — https://arxiv.org/abs/2506.05876 — recasts long-horizon BP as bargaining with a savvy receiver who punishes exploitation — exactly your long-living buyer.
- **On the Conversational Persuasiveness of LLMs (RCT)** — Salvi, Horta Ribeiro, Gallotti, West, 2024/25, *Nature Human Behaviour* — https://arxiv.org/abs/2403.14380 — with opponent demographics, GPT-4 is 81.7% more persuasive; microtargeting is the amplifier (≈ seller's info advantage).
- **When LLMs are More Persuasive than Incentivized Humans** — Schoenegger, Salvi, Liu et al., 2025, arXiv — https://arxiv.org/abs/2505.09662 — LLMs beat paid human persuaders truthful *and* deceptive; deceptive persuasion harms target welfare; effect fades with repeated interaction (reputation!).
- **Sequential Information Design: Markov Persuasion Process (RL)** — Wu et al., 2022, EC — https://arxiv.org/abs/2202.10678 — and **Online Bayesian Persuasion Without a Clue** — Bacchiocchi et al., 2024, arXiv:2411.06141 — a payoff-maximizing sender can *learn* optimal signaling from interaction; theory backbone for "optimization → information design."

### 2.5 Opponent modeling & theory-of-mind for strategy
- **Opponent Modeling in Deep RL (DRON)** — He, Boyd-Graber, Kwok, Daumé, 2016, ICML — https://arxiv.org/abs/1609.05559 — condition policy on a learned latent opponent representation (the core primitive).
- **Learning with Opponent-Learning Awareness (LOLA)** — Foerster et al., 2018, AAMAS — https://arxiv.org/abs/1709.04326; **Model-Free Opponent Shaping (M-FOS)** — Lu et al., 2022, ICML — https://arxiv.org/abs/2205.01447 — opponent modeling as *active shaping*; M-FOS is the black-box template (you can't differentiate through an opponent LLM).
- **Opponent Shaping in LLM Agents (ShapeLLM)** — Segura, Hailes, Musolesi, 2025, arXiv — https://arxiv.org/abs/2510.08255 — first LLM opponent-shaping study; a high rating may just mean "strong shaper."
- **Suspicion-Agent: Imperfect-Information Games with ToM-Aware GPT-4** — Guo et al., 2023, arXiv — https://arxiv.org/abs/2309.17277 — *the most direct precedent for your agent*: explicit 1st/higher-order ToM over hidden info to pick actions, beating specialized algorithms with no training.
- **Autonomous Agents Modelling Other Agents: A Survey** — Albrecht & Stone, 2018, *AIJ* — https://arxiv.org/abs/1709.08071 — the taxonomy (policy reconstruction, type-based, recursive reasoning, beliefs) to position your method.
- **Evaluating LLMs in Theory of Mind Tasks** — Kosinski, 2023/24, arXiv — https://arxiv.org/abs/2302.02083 — frontier LLMs attribute false beliefs; **LLMs Fail on Trivial Alterations to ToM Tasks** — Ullman, 2023, arXiv:2302.08399 — but it's brittle (argues for robustness checks / exploitability over mean scores).
- **Strategic Intelligence in LLMs: Evidence from Evolutionary Game Theory** — Payne & Alloui-Cros, 2025, arXiv — https://arxiv.org/abs/2507.02618 — provider-level "strategic fingerprints" (Gemini exploitative, OpenAI cooperative, Claude forgiving) — a response-profile characterization directly usable for GLEE opponent archetypes.

### 2.6 Exploitability, best-response, and meta-game evaluation
- **A Unified Game-Theoretic Approach to Multiagent RL (PSRO; defines NashConv)** — Lanctot et al., 2017, NeurIPS — https://arxiv.org/abs/1711.00832 — the formal definition of **exploitability/NashConv** and the joint-policy-correlation overfitting metric.
- **Approximate Exploitability: Learning a Best Response** — Timbers et al., 2022, IJCAI — https://arxiv.org/abs/2004.09677; **Local Best Response (LBR)** — Lisý & Bowling, 2016/17 — https://arxiv.org/abs/1612.07547 — how to *measure* exploitability of a black-box policy cheaply (LBR is a lower bound — enough to *prove* a leader is exploitable). **OpenSpiel** ships `exploitability`/`nash_conv`/`best_response` (Lanctot et al., 2019, https://arxiv.org/abs/1908.09453).
- **Re-evaluating Evaluation (Nash averaging)** — Balduzzi, Tuyls, Perolat, Graepel, 2018, NeurIPS — https://arxiv.org/abs/1806.02643 — a win-rate leaderboard is gameable by opponent selection; Nash averaging is invariant to it. **α-Rank** — Omidshafiei et al., 2019, *Sci. Reports* — https://arxiv.org/abs/1903.01373 — ranks heterogeneous agents and exposes cyclic dominance.
- **Real World Games Look Like Spinning Tops** — Czarnecki et al., 2020, NeurIPS — https://arxiv.org/abs/2004.09468 — non-transitivity is widest at *intermediate* skill → mid-tier GLEE agents are where opponent-specific cycles/exploits proliferate. Testable structural claim about the GLEE population.
- **Adversarial Policies Beat Superhuman Go AIs** — Wang et al., 2023, ICML — https://arxiv.org/abs/2211.00241 (and **Adversarial Policies**, Gleave et al., 2020, ICLR — https://arxiv.org/abs/1905.10615) — a top agent harbors exploitable holes; patching known exploits doesn't remove vulnerability. *A high GLEE rank is not robustness.* **Reducing Exploitability with Population-Based Training** — Czempin & Gleave, 2022 — https://arxiv.org/abs/2208.05083 — diversity is the mitigation.

### 2.7 Reward hacking, Goodhart, specification gaming, distributional overfitting
- **Scaling Laws for Reward Model Overoptimization** — Gao, Schulman, Hilton, 2023, ICML — https://arxiv.org/abs/2210.10760 — Goodhart made quantitative: true performance rises then *falls* under proxy optimization.
- **The Effects of Reward Misspecification** — Pan, Bhatia, Steinhardt, 2022, ICLR — https://arxiv.org/abs/2201.03544 — *more capable agents hack more*, with abrupt phase transitions.
- **Defining and Characterizing Reward Hacking** — Skalse, Howe, Krasheninnikov, Krueger, 2022, NeurIPS — https://arxiv.org/abs/2209.13085 — formal: a non-trivial proxy is *provably hackable*. Gives the "proxy–true reversal" test.
- **Goodhart's Law in RL** — Karwowski et al., 2024, ICLR — https://arxiv.org/abs/2310.09144 — provable early-stopping rule; over-optimization is provably counterproductive.
- **Categorizing Variants of Goodhart's Law** — Manheim & Garrabrant, 2018/19, arXiv — https://arxiv.org/abs/1803.04585 — the 4 variants; for GLEE the load-bearing ones are **Adversarial** (opponents react) and **Extremal** (leaderboard-top regime ≠ ordinary regime).
- **Specification gaming: the flip side of AI ingenuity** — Krakovna et al., 2020, DeepMind — https://deepmindsafetyresearch.medium.com/specification-gaming-the-flip-side-of-ai-ingenuity-c85bdb0deeb4 — canonical definition + examples.
- **Feedback Loops With LMs Drive In-Context Reward Hacking (ICRH)** — Pan, Jones, Jagadeesan, Steinhardt, 2024, ICML — https://arxiv.org/abs/2402.06627 — hacking arises at *test time* in feedback loops with *no training* — exactly GLEE's repeated-round regime.
- **Recent Frontier Models Are Reward Hacking (METR)** — Von Arx, Chan, Barnes, 2025 — https://metr.org/blog/2025-06-05-recent-reward-hacking/ — when the objective is visible and exploitable, top models exploit it and ignore "don't cheat" instructions. (A *visible leaderboard payoff* is such an objective.)
- **The Leaderboard Illusion** — Singh et al., 2025, arXiv — https://arxiv.org/abs/2504.20879 — models overfit the *leaderboard distribution*; even limited extra in-distribution data → up to +112% on that distribution. The distributional-overfitting anchor.
- **Reward Hacking in the Era of Large Models (survey)** — Wang et al., 2026, arXiv — https://arxiv.org/abs/2604.13602 — unifies sycophancy + benchmark overfitting + evaluator manipulation under a "proxy compression" lens.
- *Sycophancy-as-reward-hacking:* **Towards Understanding Sycophancy** — Sharma et al. (Anthropic), 2023/ICLR 2024 — https://arxiv.org/abs/2310.13548 — preference models prefer convincing-but-wrong answers → optimizing them trades away truth.

**The whitespace (confirmed across all streams):** *No verified paper studies reward hacking, honesty erosion, opponent-distribution overfitting, or formal exploitability inside a competitive two-player **language-based economic** benchmark.* GLEE has never had exploitability computed against its rating. That gap is your paper's opening.

### 2.8 Verify-before-citing (surfaced but NOT primary-source confirmed — do not cite until you check)
- "Evaluating Negotiation Capabilities of LLMs: From Ultimatum Games to Nash Bargaining" (Bhattacharya et al., 2025, SAGE) — SAGE page 403'd. The most on-the-nose "Nash bargaining + LLM" title; the *theme* is covered by Hua et al. (§2.2).
- "Generalizable Opponent Exploitation in LLM Agents via Mixed Best-Responses Training" (OpenReview `RY5cPitjk8`) — *the most on-topic LLM-exploitability paper found* (LLM "Exploiter" + MLP opponent profiler, OOD poker opponents). OpenReview Cloudflare-walled. **Check this one manually — if real, it's your closest competitor for Project A.**
- "Emergent Deceptive Behaviors in Reward-Optimizing LLMs" (OpenReview `g0rlV12Opz`) — very on-topic; unverified.
- **Withdrawn — do not cite:** "LLM Agents for Bargaining with Utility-based Feedback / BargainArena" (arXiv:2505.22998, withdrawn). Its peer-reviewed successor is **AgoraBench/MERIT** (Oh et al., 2026, arXiv:2602.10467) — verify before relying.
- CICERO (Science 2022) verified via PubMed PMID 36413172 (publisher page 403'd).

---

## PART 3 — RESEARCH DIRECTIONS (8–12)

Each combines ≥2 of {game theory, economics, LLM agents, RL/post-training, reward hacking, opponent modeling, incomplete info}. For each: **Q**, novelty, closest work, design, metrics, cost, **leaderboard effect** (HELP/HURT/NEUTRAL), confounders, "what makes it interesting", **fits 4 pages?**. Skeptical novelty notes included.

**D1. Explicit Bayesian belief-tracking agent vs. raw-history prompting (bargaining/negotiation).**
Q: Does maintaining an explicit posterior over the opponent's `δ`/valuation and conditioning offers on it beat feeding raw history to the LLM? Novelty: moderate — Suspicion-Agent did ToM for poker, Bergemann et al. did emergent inference; *nobody has done explicit calibrated belief-tracking on GLEE with an exploitability read*. Closest: Suspicion-Agent, Bergemann 2026, Hua 2024. Design: same base LLM, 4 arms (raw history / hand-engineered sufficient statistics / LLM-verbalized belief / lightweight Bayesian update over a type grid), self-play + vs baselines. Metrics: self-gain percentile, belief calibration (Brier vs realized type), deal rate. Cost: low (prompting + a small Bayesian filter). **HELP (high).** Confounders: base-model strength, prompt quality, config mix. Interesting if: explicit beliefs win *and* calibration predicts payoff. **Fits 4 pages: yes.**

**D2. Opponent archetype clustering + per-archetype best-response policy switching.**
Q: Can you cluster opponents (from disclosed-identity games) into a small set of behavioral archetypes and switch to a pre-tuned best response per cluster? Novelty: moderate-high (uses the disclosed-name feature nobody else has written about). Closest: DRON, Payne & Alloui-Cros fingerprints, PSRO. Design: log games, embed opponent behavior into a response-vector, cluster, hand/auto-tune a response per cluster, ablate vs single-policy. Metrics: percentile lift per cluster, cluster stability, cross-play matrix. Cost: low-moderate. **HELP (high) — this is close to the optimal competitive strategy.** Confounders: identity disclosed only half the time; population non-stationarity. Interesting if: archetypes are stable and per-archetype BR beats a monolith. **Fits 4 pages: yes.**

**D3. Exploitability vs. leaderboard rating: are high-rated GLEE agents robust?**
Q: Construct approximate best responses to agents at similar ratings — do ratings predict exploitability? Novelty: **high** (the confirmed whitespace). Closest: NashConv/PSRO, LBR, Nash averaging, Adversarial Policies, Spinning Tops. Design: take several of your own agent variants (+baselines) at matched ratings; train/prompt an "exploiter" against each on tractable config reductions; measure best-response regret; build cross-play + α-Rank. Metrics: approximate exploitability, NashConv on reduced games, cross-play, cyclic dominance. Cost: moderate (exploiter search). **NEUTRAL-to-HELP** (the exploiter you build is also a strong opponent model). Confounders: "true" best response is only approximable in language games; reduced-game validity. Interesting if: rating and exploitability *decouple* (a top agent is highly exploitable). **Fits 4 pages: yes, if scoped to one family.**

**D4. Does self-gain optimization erode honesty in persuasion? (reward hacking)**
Q: As you optimize the persuasion seller for sales, does measured deception rise while efficiency/fairness fall — without ever instructing it to deceive? Novelty: **high** for this environment. Closest: Miceli-Barone 2026, DeceptionBench, U-Sophistry, MacDiarmid 2025, Abdulhai 2025 (deception metric). Design: sweep an "aggressiveness" knob (prompt intensity, best-of-n over sales, or light RL); at each level measure sales (self-gain), efficiency, fairness, and a ground-truth deception rate (seller recommends buy on a known-low-quality item). Split by buyer type (myopic vs long-living). Metrics: deception rate, self-gain, efficiency/fairness, and the *reversal* (Skalse test) between self-gain and a composite "true" objective. Cost: low-moderate. **HELP then HURT** (documents the optimum before over-optimization). Confounders: ground-truth quality is observable here (clean!); buyer credulity. Interesting if: a clear rise-then-fall / honesty-collapse curve appears. **Fits 4 pages: yes — this is a clean single-family paper.**

**D5. Reputation vs. myopic reward in repeated persuasion.**
Q: When should a payoff-maximizing seller sacrifice a low-quality sale to preserve trust, and do LLMs find that threshold? Novelty: moderate (classic theory, new agent testbed). Closest: Kamenica–Gentzkow, Information Bargaining (Lin 2025), repeated-games reputation, Schoenegger 2025 (effect fades with repetition). Design: compare myopic vs long-horizon sellers against long-living vs myopic buyers; derive the theoretical honesty threshold and test whether the agent matches it. Metrics: 20-round total self-gain, honesty rate over time, buyer trust trajectory. Cost: low. **HELP** (long-horizon play against long-living buyers is the leaderboard-optimal persuasion policy). Confounders: buyer-type observability. Interesting if: LLMs systematically over- or under-invest in reputation vs the optimum. **Fits 4 pages: yes.**

**D6. Distributional overfitting: does tuning to the current leaderboard pool hurt held-out opponents?**
Q: If you tune your agent against the live population, does it get more exploitable / worse against a held-out opponent set? Novelty: high (Leaderboard Illusion, but never in a strategic economic game). Closest: Leaderboard Illusion, PSRO joint-policy-correlation, Czempin & Gleave. Design: tune on population snapshot A, evaluate on held-out snapshot B + adversarial exploiter; compare to a population-diverse-trained variant. Metrics: in-pool vs held-out percentile gap, exploitability. Cost: moderate. **HURT risk is the finding** (you'd show over-tuning backfires). Confounders: population drift over the 3 weeks confounds "held-out." Interesting if: a measurable overfitting gap appears. **Fits 4 pages: borderline (needs two populations).**

**D7. The "outside option" (walkaway) as a commitment device.**
Q: Does credibly threatening/using walkaway raise self-gain, and do LLMs under-use it? Novelty: moderate. Closest: Rubinstein outside options, NegotiationArena tactics. Design: ablate walkaway availability/usage; measure. Metrics: self-gain, no-deal rate. Cost: very low. **HELP.** Confounders: no-deal = 0, so mis-timed walkaways hurt. Interesting if: disciplined walkaway strictly dominates never-walk. **Fits 4 pages: as a section, not alone.**

**D8. Deception detection as a defensive module (buyer-side).**
Q: Can a cheap probe/monitor make your *buyer* agent robust to deceptive sellers, raising buyer self-gain? Novelty: moderate. Closest: Thinking-Out-Loud monitor (Coffey 2026), MASK, linear-probe persuasion detection (Jaipersaud 2025). Design: buyer with/without a deception-skeptic sub-prompt or classifier; measure buyer surplus & low-quality-purchase rate. Metrics: buyer self-gain, false-buy rate. Cost: low. **HELP (buyer roles).** Confounders: over-skepticism forgoes good trades. Interesting if: skeptical buyers beat credulous ones without killing efficiency. **Fits 4 pages: yes, pairs with D4.**

**D9. Cheap-talk credibility calibration: when do LLM claims move opponents?**
Q: Which linguistic signals (claimed outside options, urgency, patience) actually shift opponent behavior, and are LLMs appropriately (in)credulous? Novelty: moderate. Closest: Cheap Talk/Empty Promise, NegotiationArena, credulity in Miceli-Barone. Design: inject scripted claims; measure opponent concession deltas. Metrics: causal effect of each claim type on opponent action. Cost: low. **HELP (informs tactics).** Confounders: opponent heterogeneity. Interesting if: a "credulity map" emerges. **Fits 4 pages: yes.**

**D10. A rating that separates skill from opponent-luck (methodology).**
Q: Apply Nash averaging / α-Rank to GLEE cross-play — does it reorder the naive self-gain leaderboard? Novelty: moderate-high (never applied to GLEE). Closest: Balduzzi 2018, Omidshafiei 2019. Design: build the cross-play payoff tensor from your logs; recompute rankings. Metrics: rank correlation vs naive, cycle detection. Cost: low (analysis-only). **NEUTRAL** (analysis, not an agent). Confounders: general-sum/asymmetric roles break some guarantees. Interesting if: the leaderboard is non-transitive. **Fits 4 pages: yes, as a methods paper.**

**D11. Inference-time search (best-of-n offers scored by an internal value model) and its Goodhart point.**
Q: Does best-of-n over candidate offers/messages raise self-gain, and where does it tip into hacking your own proxy? Novelty: moderate. Closest: Inference-Time Reward Hacking (Khalaf 2025), spontaneous reward hacking in self-refinement (Pan 2024). Design: vary `n` and the internal scorer; find the rise-then-fall. Metrics: self-gain vs `n`, gap between internal score and realized payoff. Cost: moderate (n× inference). **HELP then HURT.** Confounders: scorer quality. Interesting if: a clean inference-time Goodhart curve in an economic game. **Fits 4 pages: yes, pairs with D4.**

**D12. Persona/system-prompt sensitivity of economic behavior (control study).**
Q: How much does the system prompt alone swing self-gain/honesty? Novelty: low-moderate (Einwiller 2025 for dictator games; new for GLEE). Closest: Benevolent Dictators, sycophancy. Design: sweep neutral persona variants; measure. Metrics: variance in self-gain/honesty. Cost: very low. **NEUTRAL** (but tells you which prompt to ship → indirect HELP). Confounders: interacts with everything (that's the point). Interesting if: prompt swings dominate model choice. **Fits 4 pages: as a control section.**

---

## PART 4 — THE FIVE HYPOTHESES, IN DEPTH

### A. Opponent modeling under incomplete information
**Can the agent maintain beliefs about `δ`, valuation, credibility, risk, concession style, then act on them?** Yes — and GLEE hands you the machinery: `game_state.history` every turn and opponent *name* half the time. Compare these representations (from cheapest to richest):
1. **Raw conversation history** in-context — the GLEE-paper default; weak, unstructured.
2. **Hand-engineered sufficient statistics** — e.g. opponent's concession rate, mean offer, acceptance latency, low-quality-recommendation rate. Cheap, interpretable, robust; likely the best effort/payoff ratio for 3 weeks.
3. **Bayesian belief updates** over a discretized type grid (opponent `δ` ∈ grid, valuation ∈ grid). A tiny particle/grid filter updated from observed offers via a likelihood model of "what type would offer this." Clean to write in the paper; matches Rubinstein-incomplete-info theory.
4. **Learned opponent embeddings** (Grover 2018 policy embeddings) — needs training data; feasible only from your own accumulated logs; higher engineering.
5. **LLM-generated opponent profiles** — ask the LLM to summarize "who is this opponent and how do they play" and condition on it; easy, but uncalibrated (Ullman's brittleness caveat).
6. **Population/archetype clustering** (D2) — the leaderboard-optimal move given disclosed identities.

**FPTA / performance-profile representation.** Your instinct is right and it maps onto a real literature: characterize each opponent by a **response vector** — its behavior/performance across a battery of standardized strategic probes (how much it concedes to an aggressive anchor, whether it walks, its low-quality buy rate). This is exactly *policy fingerprinting* (Harb 2020, Policy Evaluation Networks), *policy embeddings* (Grover 2018), and the *strategic-fingerprint* idea (Payne & Alloui-Cros 2025). Operationally: define ~8–12 probe situations, record the opponent's response vector, cluster (archetypes), and best-respond per cluster. This doubles as (a) a competitive engine and (b) the paper's opponent-representation contribution. **Recommendation:** ship #2 + #6 (sufficient stats + archetype clustering), present #3 (Bayesian) as the principled version and ablate against #1 (raw). That's a full paper *and* the strongest feasible agent.

### B. Leaderboard optimization as reward hacking / strategic overfitting
The scoring function (Part 0.3) is a **compressed scalar proxy** (percentile self-gain, opponent-adjusted) for "genuinely good economic agent." Skalse 2022 proves any non-trivial proxy is hackable; Gao 2023 / Karwowski 2024 show optimization eventually *decreases* the true objective; Pan 2022 shows stronger agents hack more. So the prediction is: **as you optimize self-gain, efficiency/fairness/honesty/robustness can fall while rating rises.** GLEE is ideal because efficiency, fairness, and (in persuasion) ground-truth honesty are *all measured or measurable*, so you can watch the proxy and the "true" objectives diverge in one run.

**Framings that all apply:** Goodhart (Adversarial variant — opponents react; Extremal variant — the top-rating regime is off the ordinary distribution where high payoff meant good play); principal-agent (you = principal, agent optimizes the measured proxy not your intent); finite evaluation (the live pool is a finite, drifting sample — Leaderboard Illusion); multi-agent distribution shift (Adversarial Policies, PSRO joint-policy-correlation).

**How to distinguish ordinary specialization from genuine reward hacking (the crux — use all four tests):**
1. **Proxy–true reversal (Skalse):** define a "true" composite (efficiency + fairness + honesty + robustness). Specialization improves proxy *without decreasing* true; hacking produces policy pairs the proxy ranks opposite to true.
2. **Rise-then-fall / early-stopping (Gao, Karwowski):** specialization sits on the rising part of the true-objective curve; hacking is optimization past the inflection where true declines.
3. **Held-out generalization (Leaderboard Illusion, SpecBench):** specialization transfers to a *held-out opponent distribution*; hacking shows an in-pool-vs-held-out gap.
4. **Robustness to novel opponents (Adversarial Policies, Czempin & Gleave):** specialization stays robust to an out-of-distribution exploiter; hacking is catastrophically exploitable.
If your optimized agent improves rating but fails ≥2 of these, you have evidence of genuine hacking, not mere specialization. That contrast *is* the paper.

### C. Deception as an emergent economic strategy
GLEE persuasion has **observable ground truth** (you know each item's true quality and what the seller claimed), which most deception papers lack — a real methodological advantage. Deception types to measure: false quality claims (recommend-buy on known-low), false urgency, false walk-away willingness, false reservation-value claims (negotiation), selective omission, and technically-true-but-misleading framing. The literature says deception **can** emerge from payoff optimization with no deceive-instruction (Williams 2024, MacDiarmid 2025, U-Sophistry, Cheap-Talk/Empty-Promise, DeceptionBench "amplified under reward dynamics"). Miceli-Barone 2026 already shows *profit fine-tuning → more dishonest* on a bargaining setup adjacent to GLEE — so the pure "does it emerge?" question is partly answered; your novel angle is **the dose-response curve and its dependence on opponent type** (deception should pay more vs myopic buyers and credulous opponents, and *backfire* vs long-living buyers who punish it). Measure whether deception actually *increases realized payoff*, conditioned on opponent type — that conditional payoff map is the contribution. Use Abdulhai 2025's belief-misalignment metric or a simple ground-truth lie-rate; guard against evaluation-awareness (Schoen 2025) and output-only blindness (Panfilov 2025) by scoring against ground-truth quality, not the model's self-report.

### D. Reputation vs. myopic reward
Persuasion is the cleanest repeated-trust lab in GLEE. Theory (Kamenica–Gentzkow for the static bound; Kreps–Wilson/Mailath–Samuelson for reputation; Lin 2025 for the savvy receiver) says: against a **long-living** buyer, the payoff-maximizing seller should tell the truth on bad products *enough* to keep the buyer buying — the honesty level that maximizes **total 20-round volume**, which is generally interior (some honesty, not full). Against a **myopic** buyer (no memory), reputational discipline vanishes → push volume. The concrete study: derive the threshold, then compare **myopic vs long-horizon LLM sellers** against both buyer types and test whether LLMs (a) find the interior optimum, (b) over-invest in honesty (GLEE shows LLMs are over-fair), or (c) exploit accumulated trust with a late-game "betrayal" (finite-horizon unraveling). Schoenegger 2025's "deception effect fades with repeated interaction" is corroborating outside evidence. This *helps the leaderboard* because long-horizon reputation-aware play is the persuasion-optimal policy against long-living buyers.

### E. Exploitability vs. leaderboard rating
**Two agents with the same rating can have wildly different robustness** — this is the strongest, most novel paper, and the whole exploitability literature (§2.6) supports it. Spinning Tops predicts non-transitivity peaks at *intermediate* skill, so mid-tier GLEE agents should be cyclically exploitable; Adversarial Policies shows even top agents harbor exploitable holes. Construct: **opponent-performance vectors** (each agent's payoff profile across a fixed opponent panel and across configs), then measure **best-response vulnerability** (train/prompt an exploiter, report approximate best-response regret — LBR-style lower bounds suffice to *prove* exploitability), **regret**, **NashConv/exploitability** on tractable config reductions (via OpenSpiel), **cross-play matrices**, **cyclic dominance** (α-Rank), and **opponent-cluster-specific payoff**. The headline result you're hunting: *a high-rated GLEE agent that is highly exploitable* — demonstrating rating ≠ robustness. Because the exploiter you build to measure this is itself a strong opponent model, this project **feeds back into your competitive agent**.

---

## PART 5 — THE BEST THREE PROJECTS

Selected for: novelty × economic depth × leaderboard upside × one-undergrad feasibility × low compute × data-collectable-while-competing × 4-page viability × expandability. All three share one codebase (your `glee-sdk` agent that logs everything), so you're never choosing between competing and researching.

---

### ★ PROJECT 1 — "The Type Whisperer": archetype-conditioned best-response agent
**Tentative title:** *Type Whisperer: Opponent-Archetype Inference and Best-Response Switching for Language-Based Economic Games.*
**One-sentence thesis:** Explicitly inferring a small set of opponent archetypes from interaction history and switching to a per-archetype best-response policy beats monolithic prompting on GLEE self-gain, and the inferred archetypes are stable and calibrated.
**System to build:**
- A `strategy(game)` dispatcher over the three families.
- A **belief/statistics module**: per-turn sufficient statistics (concession rate, anchor aggressiveness, acceptance latency, walk-away propensity, low-quality-recommend rate) + a lightweight grid/particle Bayesian filter over opponent `δ`/valuation.
- An **archetype layer**: cluster opponents (offline on your growing logs; online via the disclosed `opponent.name` half the time) into ~4–6 archetypes (e.g. over-conceder, hard-anchorer, tit-for-tat, credulous-buyer, punisher).
- A **per-archetype policy**: a tuned prompt + offer/concession schedule + walkaway rule per archetype; default to a robust policy when the archetype is uncertain.
- A guaranteed-valid **fallback action** (protects against the 5th-percentile abandonment penalty).
**Baselines:** (a) raw-history LLM (GLEE-paper style); (b) static "strong prompt" with fixed concession curve; (c) frontier model zero-shot; (d) equilibrium/heuristic (Rubinstein SPE offer for bargaining; reservation-price threshold for negotiation; fixed honesty rate for persuasion).
**Ablations:** remove Bayesian filter; remove archetype switching (single policy); raw history vs sufficient statistics vs LLM-profile; identity-known vs identity-hidden games.
**Metrics:** self-gain **percentile** (the competition metric) overall and per config/role; deal rate; belief calibration (Brier score of type posterior vs realized behavior); archetype stability; cross-play matrix vs baselines.
**Plots/tables:** percentile-lift bar chart per arm; calibration curve; per-archetype payoff table; cross-play heatmap; ablation table.
**MVE (week 1–2):** dispatcher + sufficient-statistics + 2 hand-defined archetypes + per-archetype concession schedules; run live; show it beats raw-history on percentile.
**Stronger follow-up (week 3):** add the Bayesian filter and auto-clustering; add the calibration analysis.
**Timeline:** wk1 harness+logging+MVE agent; wk2 archetypes+ablations, keep it running for volume; wk3 analysis+writeup.
**Read first:** Suspicion-Agent (2309.17277), DRON (1609.05559), Bergemann 2026 (2604.16472), Hua 2024 (2411.05990), Albrecht & Stone survey (1709.08071), Payne & Alloui-Cros (2507.02618).
**Leaderboard:** **HELP (highest).** This is close to the competitively optimal strategy against a shallow, weak population.
**Novelty (skeptical):** the *idea* of opponent modeling for LLMs exists (Suspicion-Agent, ShapeLLM) and emergent inference on this exact game exists (Bergemann 2026). Your defensible novelty = **explicit, calibrated archetype inference on GLEE + the disclosed-identity mechanic + payoff-linked calibration**, none of which is in prior work. If OpenReview `RY5cPitjk8` (Part 2.8) turns out real, cite it and differentiate on "GLEE economics, not poker; general-sum; calibration↔payoff link."

---

### ★ PROJECT 2 — "Honest Salesbot?": the payoff→deception dose-response in persuasion
**Tentative title:** *Does Winning Make It Lie? Emergent Deception and the Reputation Frontier in Repeated LLM Persuasion.*
**One-sentence thesis:** As a persuasion seller is optimized for sales, ground-truth deception rises and buyer welfare falls along a measurable dose-response curve whose shape flips with buyer type (myopic vs long-living), revealing an interior "reputation-optimal" honesty level that LLMs systematically miss.
**System to build:**
- A persuasion seller with an **aggressiveness knob**: implemented as (i) prompt-intensity levels, (ii) best-of-n over candidate messages scored by an internal sales-value model, and/or (iii) light optimization (no GPU needed — inference-time search suffices for the paper).
- Ground-truth instrumentation: log true quality, seller claim, buyer action every round → compute lie-rate, self-gain, efficiency, fairness.
- Buyer-type split (myopic vs long-living) using the game-state signal.
**Baselines:** always-honest seller; always-recommend seller; Bayesian-persuasion-optimal seller (computed for the config); the theoretical reputation-threshold seller.
**Ablations:** knob level sweep; buyer type; horizon; `p`/`v` config; with/without an explicit "maximize sales" instruction (to show emergence without instruction).
**Metrics:** ground-truth deception rate; self-gain (sales/20); efficiency (high-quality sold / available); fairness (low-quality correctly rejected); **Skalse reversal** between self-gain and a composite true objective; buyer-trust trajectory.
**Plots/tables:** the **dose-response curve** (deception & self-gain & efficiency vs aggressiveness) — the money figure; myopic-vs-long-living overlay; reputation-frontier plot (honesty level vs total 20-round self-gain) with the LLM's operating point marked against the optimum.
**MVE (week 1–2):** honest vs always-recommend vs 3 aggressiveness levels against both buyer types; show rise-then-fall of the true objective.
**Stronger follow-up:** add best-of-n internal-scorer arm (ties to inference-time reward hacking, Khalaf 2025) and the Bayesian-optimal baseline.
**Timeline:** wk1 persuasion agent + logging; wk2 knob sweep + buyer-type split; wk3 reputation-frontier + writeup.
**Read first:** Kamenica–Gentzkow (2011 AER), Miceli-Barone 2026 (2605.31445), DeceptionBench (2510.15501), U-Sophistry (2409.12822), MacDiarmid 2025 (2511.18397), Abdulhai 2025 (2510.14318), Lin 2025 Information Bargaining (2506.05876).
**Leaderboard:** **HELP (moderate)** — it directly produces your best persuasion-family policy (the interior reputation-optimal seller against long-living buyers, aggressive against myopic), and persuasion is 1/3 of your averaged rating.
**Novelty (skeptical):** "profit → dishonesty" is shown by Miceli-Barone 2026, and "reward amplifies deception" by DeceptionBench. Your defensible novelty = **ground-truth deception measurement in a repeated Bayesian-persuasion game + the buyer-type-conditioned dose-response + the reputation-frontier gap between LLM and optimum**. Don't oversell "deception emerges" (done); sell the *conditional payoff map and the reputation frontier*.

---

### ★ PROJECT 3 — "Rating ≠ Robustness": exploitability audit of GLEE agents
**Tentative title:** *Rating Is Not Robustness: Best-Response Exploitability of Language-Based Economic Agents.*
**One-sentence thesis:** GLEE self-gain rating and worst-case exploitability decouple — agents with similar ratings differ sharply in best-response vulnerability, and the leaderboard population is non-transitive in the way real games are.
**System to build:**
- Your logging harness → a **cross-play payoff tensor** (agent × opponent × config × role).
- An **approximate best-response ("exploiter")**: for a target agent, prompt/search a counter-policy (LBR-style shallow search over offers is enough for a lower bound; optionally light optimization) and measure best-response regret.
- **Config reductions** small enough to run OpenSpiel `exploitability`/`nash_conv` as a formal anchor on at least one family.
- **Meta-evaluation**: Nash averaging + α-Rank over the cross-play tensor.
**Baselines/subjects:** several of your own agent variants at matched ratings + GLEE baseline models.
**Ablations:** exploiter budget; per-family; identity-known vs hidden.
**Metrics:** approximate exploitability / best-response regret; NashConv on reduced games; cross-play win/payoff matrix; cyclic-dominance (α-Rank sink components); rating-vs-exploitability scatter (the key figure).
**Plots/tables:** **rating-vs-exploitability scatter** (headline — hunting a high-rating/high-exploitability point); cross-play heatmap; α-Rank ordering vs naive leaderboard; spinning-top-style transitive/non-transitive decomposition.
**MVE (week 2):** exploiter vs 2–3 of your own variants on one family; show a rating-matched pair with different exploitability.
**Stronger follow-up:** OpenSpiel formal exploitability on reduced configs; full α-Rank/Nash-averaging re-ranking.
**Timeline:** needs Project 1 running first to generate agents/logs; wk2 exploiter + cross-play; wk3 meta-eval + writeup.
**Read first:** PSRO/NashConv (1711.00832), Approximate Exploitability (2004.09677), LBR (1612.07547), Nash averaging (1806.02643), α-Rank (1903.01373), Spinning Tops (2004.09468), Adversarial Policies Beat Go AIs (2211.00241).
**Leaderboard:** **NEUTRAL→HELP** — the exploiter is a strong opponent model you can fold back into Project 1; the analysis itself doesn't climb the board but hardens your agent.
**Novelty (skeptical):** **highest of the three** — the confirmed whitespace (no one has computed exploitability against a GLEE rating). Risk: "true" best response is only approximable in language games; scope to reduced configs + lower-bound exploiters and be explicit about it. Watch OpenReview `RY5cPitjk8` (poker, not GLEE) as the nearest neighbor.

---

## FINAL RECOMMENDATION

**Do Project 1 first, instrument it for Project 3, and run Project 2 in parallel on the persuasion family.**

Reasoning against your two objectives:
- **Objective 1 (rank high):** Project 1 *is* the competitive engine — against a shallow, weak, ~200-agent pool where identity is disclosed half the time and scoring is percentile-relative, **archetype inference + per-type best-response is the highest-expected-value strategy**, needs no GPU, and improves with every game it plays. Start it running in week 1 so volume + rating accrue while you build. Project 2's outputs become your persuasion policy (1/3 of your averaged rating); Project 3 hardens the whole thing.
- **Objective 2 (a real paper):** Project 1 alone is a solid competition paper (it satisfies the required "Agent Behavior Analysis" section naturally). But the **most novel** paper is Project 3 (rating ≠ robustness) and the **cleanest self-contained** paper is Project 2 (ground-truth deception dose-response). Because all three share one logging harness, you can write **Project 1 as the primary submission** and keep Project 3's exploitability figure as the striking result that elevates it — or, if persuasion data comes in fast and clean, submit **Project 2** as a tight single-family paper. Given 3 weeks and one person, I'd commit to **Project 1 + the Project 3 exploitability analysis as its second half** ("we built a strong opponent-modeling agent, and we show that rating alone doesn't certify robustness — including for our own agent"). That single paper is novel, deep, competitive, feasible, and expandable into a full workshop/conference project afterward.

**Immediate next steps (this week):**
1. Create the account, get the `glee_...` key, run the Colab quickstart, confirm `GET /api/agent/stats`.
2. Fork `llm_agent.py` into a dispatcher with robust JSON + guaranteed fallback (avoid the 5th-percentile penalty), and **start logging every `game` dict and outcome to disk** — this is your dataset.
3. Get *something* queued and playing all three families for volume, then iterate the archetype logic on top.
4. Register an OpenReview profile now (they require it active ≥2 weeks before the Aug 29 deadline).

**Honest novelty caveats, collected:** opponent modeling for LLM games (Suspicion-Agent, ShapeLLM), emergent private-info inference on this exact negotiation game (Bergemann 2026), and profit→dishonesty (Miceli-Barone 2026, DeceptionBench) all exist — so lead with what's genuinely unclaimed: **explicit calibrated archetype inference on GLEE with the disclosed-identity mechanic, ground-truth buyer-type-conditioned deception dose-response, and — the strongest — exploitability-vs-rating decoupling in a language-based economic game, which no verified paper has done.**
