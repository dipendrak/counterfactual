# DEV C — Gemini Layer and Claims Validator

**You own the most defensible idea in the project: the model that is never allowed to write a number.**

Read `00-ARCHITECTURE.md` first for the frozen contracts.

---

## Mission

Two narrow Gemini jobs plus one validator. Parse an inbound text message into a structured decision. Render a typed claim list into natural prose. Then refuse to let that prose out the door if it contains a figure that doesn't trace back to a claim.

**Package:** `llm/` — Python 3.11, Gemini API with JSON schema mode.

**The framing that wins you the MLH Gemini prize:** everyone else is using Gemini as the whole product. You're using it as a translator at the two boundaries of a deterministic system, and you can prove it with a validator. That's a stronger Gemini story than a chatbot, and it's the one judges haven't heard forty times.

---

## What you build

### `llm/parse.py`

```python
def parse(text: str) -> DecisionDelta
```

Gemini with structured output, constrained to the `DecisionDelta` schema. Input is a raw text message. Output is `{ kind, amount, start_day, cadence_days, label, raw_text, confidence }`.

Resolve relative dates ("in December", "next month") into `start_day`, an integer offset from today. Pass today's date in as context; do not let the model guess what today is.

Handle these forms:
- "should I take the $1400 flight to see my family in december" → one_time, 1400, start_day ~64
- "thinking about a $65/mo gym membership" → recurring, 65, cadence_days 30
- "can I afford a car" → low confidence, no amount. Return `confidence < 0.5` and let B ask the user to rephrase.

Low confidence is a feature. A parser that confidently invents $20,000 is worse than one that asks.

### `llm/render.py`

```python
def render(claims: list[Claim], culprit: Culprit) -> str
```

Gemini gets the claim list and **nothing else**. No raw `SimResult`, no transaction records, no model. Just typed claims.

The prompt instruction that matters: *every number in your output must come from a claim's `value` field, verbatim. Do not compute, round, average, or infer any figure.*

Target 3–5 sentences, iMessage-appropriate. Lead with the probability, name the culprit, end with the replay link reference.

### `llm/validate.py` — **this is the centerpiece**

```python
def validate(prose: str, claims: list[Claim]) -> ValidationResult
```

Three checks:

1. **Numeric trace.** Regex every number out of the prose. Every one must match a claim `value` within tolerance (exact for integers, 2dp for dollars, 1pp for probabilities expressed as percent). Any orphan number is a failure.
2. **Evidence existence.** Every evidence ID referenced must resolve through B's `GET /txn/{txn_id}`. Cache the lookups.
3. **Culprit consistency.** The merchant named in the prose must equal `culprit.label`. The model does not get to pick a different villain than the simulation found.

Return `{ passed: bool, violations: list[str], prose: str }`.

**On failure: retry render once, then fall back to templated prose.** Never fail open into an error, never fail open into unvalidated prose. A slightly stiff templated sentence is completely acceptable. A dead thread on stage is not.

### `llm/template.py` — the fallback

Deterministic string formatting from claims. No model. This must always work, and honestly it should be the first thing you build on Friday afternoon, because it unblocks B's `/ask` immediately and gives you a safety net you'll be glad to have at 3 PM Saturday.

### `llm/tests/`

Include a **seeded adversarial test**: feed the validator a hand-written prose string containing a plausible but fabricated number, and assert it rejects. This is the test you run live on stage during the demo. Make it clean enough to show on a projector.

---

## Your three blockers

### Blocker 1 — Round 1, Friday 7:00 PM, with **Dev D**
**What you need:** agreement on message formatting constraints for iMessage, before you tune the render prompt.

**Why it blocks:** if D's Spectrum flow has a length cap, or wants the replay link as a separate message, or wants the prose split into two bubbles for pacing, that changes your render prompt and your template. Tuning a prompt twice is a waste of your Saturday.

**Say this to D:** "What's your length budget per bubble? Do you want the replay link inline or as a second message? Do you want me to return one string or a list of strings for multi-bubble pacing? And what's your failure string if I return nothing?"

**Done when:** you both agree on return shape (single string vs list) and a character budget, and D has your templated fallback working in their flow.

---

### Blocker 2 — Round 2, Saturday 9:00 AM, with **Dev A**
**What you need:** a real claim list from a real run, and agreement on the claim `kind` vocabulary.

**Why it blocks:** you cannot write a render prompt against imagined claims. And if you need a number A isn't emitting (say, dollars at risk rather than probability), your prose will feel thin and your validator will reject every attempt to add it.

**Say this to A:** "Here's a claim list from your real run. I'm drafting one sentence per claim now. These two numbers I want to say and don't have: can you add those kinds in the next 30 minutes? Also confirm the exact `kind` strings so my validator's switch doesn't miss any."

**Done when:** you can render a full paragraph using only A's claims, and the validator passes on it.

---

### Blocker 3 — Round 3, Saturday 11:00 AM, with **Dev B**
**What you need:** confirmation that every evidence ID in a real model resolves through `GET /txn/{txn_id}`.

**Why it blocks:** your validator's second check depends entirely on B's resolver. If B's fitter synthesizes any IDs, or references uncached accounts, your validator rejects everything and silently falls back to templates for the entire demo. Nobody will notice until a judge asks about the Gemini integration and you have to admit it never fires.

**Say this to B:** "Give me 50 evidence IDs from a real model. I'm running my existence check against `/txn/` right now. Any 404s, you need to know in the next 10 minutes."

**Done when:** zero 404s across a full model's evidence index.

---

## Hour by hour

| Window | Target |
|--------|--------|
| Fri 1–2 PM | Phase 0. Contract freeze. You push back hard on the `Claim` shape now if it won't render well, because it's frozen after 2 PM. |
| Fri 2–3 PM | `llm/template.py` first. Ship it to B immediately so `/ask` is unblocked. |
| Fri 3–5 PM | `parse()` with JSON schema mode. Test on 10 phrasings. |
| Fri 5–7 PM | Validator check 1 (numeric trace). This is the hard regex work, do it while fresh. |
| **Fri 7:00** | **Handshake with D** |
| Fri 7:30–10 PM | `render()` against A's fixture claims, tune prompt |
| Fri 10–11:30 PM | Validator checks 2 and 3, retry-then-fallback logic |
| Fri 11:30 PM | Integration Checkpoint A |
| Sat 8–9 AM | Adversarial test, make it projector-clean |
| **Sat 9:00** | **Handshake with A** |
| Sat 9:30–11 AM | Re-tune render against real claims |
| **Sat 11:00** | **Handshake with B** |
| Sat 11:30–12 PM | Evidence resolution fixes |
| **Sat 12:00** | Hard gate |
| Sat 1 PM+ | You own the "Gemini never writes a number" beat in the demo. Rehearse it. |

---

## Definition of done

- [ ] `parse()` handles one_time, recurring, and ambiguous, with calibrated confidence
- [ ] `render()` receives claims only, never raw `SimResult`
- [ ] Validator rejects orphan numbers, unresolvable evidence IDs, and culprit mismatches
- [ ] Failure path is retry-once-then-template, never an exception
- [ ] Adversarial test exists, passes, and looks good on a projector
- [ ] Templated fallback produces acceptable prose with zero model calls
- [ ] Gemini API key in env, never committed

---

## Failure modes to avoid

**Letting raw `SimResult` into the render prompt.** The moment the model can see the paths array, it will start computing its own numbers and your entire thesis collapses. Claims only. Enforce it with a type signature.

**Building render before template.** Template unblocks B and saves you Saturday afternoon. Build it in the first hour.

**A validator so strict nothing passes.** Watch out for the model writing "27%" when the claim value is `0.27`, or "about $140" when the claim is `142.00`. Normalize percentages, and either allow a hedge-word tolerance or instruct the model to never hedge numerically. Decide which by Friday night, not Saturday noon.

**Tuning the prompt before you know D's format.** That's what Blocker 1 exists to prevent.
