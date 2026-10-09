# DEV A — Simulation Core

**You own the thing that must not fail on stage.**

Read `00-ARCHITECTURE.md` first for the frozen contracts. This document assumes it.

---

## Mission

A seeded Monte Carlo engine that takes a fitted cashflow model plus a proposed financial decision, simulates 500 futures over 24 months, and identifies which single recurring obligation is actually responsible for the bad ones.

**Package:** `core/` — pure Python 3.11 + numpy.

**Absolute rule:** zero network imports. No `requests`, no `httpx`, no `google.generativeai`, no `datetime.now()`, no unseeded `random`. If your module needs the internet or the wall clock, it belongs to someone else. A reviewer should be able to run your entire test suite on a plane.

---

## What you build

### `core/model.py` — dataclasses
Typed Python representations of `CashflowModel`, `DecisionDelta`, `Claim`, `SimResult`. These are the frozen contracts. Write `from_dict` / `to_dict` on each. Everyone else imports these, so they are load-bearing.

### `core/simulate.py` — the engine

```python
def simulate(model: CashflowModel,
             delta: DecisionDelta | None,
             seed: int = 1337,
             n_paths: int = 500,
             horizon_weeks: int = 104) -> PathSet
```

Weekly ticks. Each path carries a **policy vector** sampled once at path start:

- `discipline` (float, ~0.7–1.3) multiplies discretionary spend
- `shock_prob` (float, ~0.005–0.03) chance per week of an unexpected expense
- `shock_magnitude` (float, dollars) size when a shock fires
- `late_prob` (float) chance a recurring bill slips a week and incurs a fee

Per week per path: apply income if due, apply each recurring obligation if due, draw discretionary from the model's distribution scaled by `discipline`, roll for shock, apply the delta if the week has arrived. Track running balance.

Per path record: full weekly balance array, `min_balance`, `first_breach_week` (or null).

**Breach** = balance below `breach_threshold` (default 0.00) in any week.

### `core/attribute.py` — the villain finder

This is the adversarial moment and it is the most important 40 lines in the repo.

```python
def attribute(model, delta, seed, n_paths) -> Culprit, list[Driver]
```

Run the baseline. Then, for each recurring obligation, re-run with that obligation removed and measure how much the breach rate drops. Biggest drop wins.

**Critical implementation detail:** reuse the identical random draws across every leave-one-out run. Pre-generate the full noise tensor once with the seed, then index into it. Same dice, one variable changed. If you re-seed per run, your comparison is noise and the whole feature is a lie.

With a pre-generated noise tensor, 15 extra runs finish in well under a second. Vectorize over paths with numpy; do not loop 500 times in Python.

### `core/claims.py` — claim emission

Walk the `SimResult` and emit the typed `Claim` list using the frozen `kind` vocabulary. Every claim carries `evidence`: the Nessie transaction IDs that fed the model element behind it. You get those IDs from the `evidence` arrays inside `CashflowModel`, so plumb them through rather than regenerating them.

**You own the claim `kind` vocabulary.** If you add a kind, you tell Dev C in your Round 2 handshake. Do not add one silently.

### `core/tests/`
Target 15+ tests. Judges have rewarded visible test counts. Minimum coverage:

- Determinism: same seed in, byte-identical result out, twice
- Attribution sanity: inject a synthetic model with one obviously dominant obligation, assert it is named
- Zero-recurring edge case does not crash
- Delta larger than lifetime income produces 100% breach, not an exception
- Every emitted claim has non-empty `evidence`

---

## Your three blockers

### Blocker 1 — Round 1, Friday 7:00 PM, with **Dev B**
**What you need:** a real `CashflowModel` emitted by B's fitter from actual Nessie data, not your fixture.

**Why it blocks:** your fixture is a guess about distribution shapes. Real Nessie data has irregular cadences, merchants that look recurring but aren't, and income that doesn't land on a clean 14-day cycle. Your engine may silently produce garbage on real input.

**Say this to B:** "Give me three real `CashflowModel` JSONs from three different Nessie customers. I need one with messy income cadence. I'll run them through and tell you within 20 minutes whether any field breaks me."

**Done when:** `simulate()` runs clean on all three and you've both agreed on how `cadence_days` handles non-integer real-world spacing.

**If B isn't ready:** keep your fixture, flag it, and re-test at Integration Checkpoint A. Do not stall.

---

### Blocker 2 — Round 2, Saturday 9:00 AM, with **Dev C**
**What you need:** confirmation that your `Claim` list is sufficient for C to render natural prose, and that your `kind` vocabulary matches what C's validator expects.

**Why it blocks:** C's validator rejects any number in the output that doesn't trace to a claim. If you emit a claim C can't use, or C needs a number you don't emit, the validator fails open to templated prose every time and the Gemini layer is dead weight.

**Say this to C:** "Here's an actual claim list from a real run. Draft one sentence per claim. Tell me which numbers you want to say that I'm not giving you, and I'll add those kinds in the next 30 minutes."

**Done when:** C can render a full paragraph using only your claims, and the validator passes on it.

---

### Blocker 3 — Round 3, Saturday 11:00 AM, with **Dev D**
**What you need:** agreement on the `paths` payload shape, because 500 paths × 104 weeks of floats is roughly 400KB of JSON and D has to render it in a browser.

**Why it blocks:** if D can't render it, the replay page is the thing we cut, and the replay page is what makes judges believe us.

**Say this to D:** "Full `paths` is about 400KB. Do you want all 500 at full weekly resolution, or do you want me to emit a downsampled band (p10/p50/p90 per week) plus 20 representative full paths? I can do either in 20 minutes, tell me which renders."

**Done when:** D has loaded your real payload in the browser and it doesn't jank.

---

## Hour by hour

| Window | Target |
|--------|--------|
| Fri 1–2 PM | Phase 0. You author `core/model.py` dataclasses live, because everyone depends on them. Push before 2 PM. |
| Fri 2–4 PM | `simulate()` running on your own fixture. Single path first, then vectorize. |
| Fri 4–6 PM | Vectorized 500 paths, under 2 seconds. Determinism test green. |
| Fri 6–7 PM | `attribute()` with the shared noise tensor. |
| **Fri 7:00** | **Handshake with B** |
| Fri 7:30–10 PM | Fix whatever real Nessie data broke. Claim emission. |
| Fri 10–11:30 PM | Tests to 15+. Edge cases. |
| Fri 11:30 PM | Integration Checkpoint A |
| Sat 8–9 AM | Polish attribution ranking, `ranked_drivers` |
| **Sat 9:00** | **Handshake with C** |
| Sat 9:30–11 AM | Add any claim kinds C needs |
| **Sat 11:00** | **Handshake with D** |
| Sat 11:30–12 PM | Emit whatever payload shape D needs |
| **Sat 12:00** | Hard gate |
| Sat 1 PM+ | Freeze. You are support staff now. Help whoever is behind. |

---

## Definition of done

- [ ] `simulate()` returns identical bytes for identical seed, verified by test
- [ ] 500 paths × 104 weeks completes in under 2 seconds
- [ ] `attribute()` uses one shared pre-generated noise tensor across all leave-one-out runs
- [ ] Every `Claim` has non-empty `evidence` with real Nessie transaction IDs
- [ ] 15+ tests passing
- [ ] Zero network imports, verifiable with `grep -rE "requests|httpx|urllib|generativeai" core/`
- [ ] `python -m core.demo` runs the full pipeline on a fixture and prints a `SimResult`

---

## Failure modes to avoid

**Re-seeding per leave-one-out run.** Your attribution becomes noise and you won't notice until a judge asks why the culprit changes between runs. Pre-generate the tensor.

**Python-looping over 500 paths.** It will take 40 seconds and your demo will feel broken. Vectorize with numpy from the start.

**Letting the dataclasses drift.** Three other people import `core/model.py`. If you rename a field at 11 PM without telling anyone, you break the whole team silently. Field renames go through the group chat, always.

**Gold-plating the policy vector.** Four parameters is enough. Nobody is going to ask how you modeled discipline.
