# DEV B — Nessie Data, Cashflow Fitter, API Orchestrator

**You own the ground truth and the plumbing. Everyone depends on you, so you ship early.**

Read `00-ARCHITECTURE.md` first for the frozen contracts.

---

## Mission

Pull 12 months of Nessie data for a handful of customers, cache it locally so the demo never depends on a live API, fit it into a `CashflowModel` with full transaction-ID provenance, and expose the orchestrating HTTP surface that Dev D's Photon agent calls.

**Packages:** `data/` and `api/` — Python 3.11, FastAPI, SQLite.

---

## What you build

### `data/ingest.py` — Nessie puller

Base URL: `http://api.nessieisreal.com` (the newer `https://prod-api.nessieisreal.com` origin also works; test both early and pin whichever is stable). API key goes in as a query parameter.

Endpoints you need:

```
GET /customers
GET /customers/{id}/accounts
GET /accounts/{id}/purchases
GET /accounts/{id}/deposits
GET /accounts/{id}/bills
GET /accounts/{id}/withdrawals
GET /accounts/{id}/transfers
GET /accounts/{id}/loans
GET /merchants
GET /merchants/{id}
```

**Cache everything to `data/cache/{customer_id}.json` at ingest time.** This is not an optimization, it is the demo's life insurance. On Saturday afternoon, `api.nessieisreal.com` being slow must not be able to hurt us. It also reinforces our pitch: we use Nessie as a population, not a live balance lookup.

Ingest at least **5 customers** with meaningfully different profiles. You want at least one with messy income cadence and one with heavy recurring obligations, because those are the interesting demos. Pick your demo customer by Saturday morning and tell the team which one.

### `data/fit.py` — the cashflow fitter

Produces `CashflowModel`. This is the subtle part of your job.

**Recurring detection.** Group purchases and bills by `merchant_id`. A merchant is recurring if you see 3+ charges with roughly consistent spacing (allow ±4 days) and roughly consistent amount (allow ±15%). Emit `cadence_days` as the median gap, rounded. Everything that fails this test falls into discretionary.

**Income.** Same logic over deposits. Find the dominant cadence, emit mean and std.

**Discretionary.** Everything left over. Bucket into weekly totals, emit mean and std, plus a category breakdown for flavor.

**Provenance is mandatory.** Every element of the model carries an `evidence` array of the actual Nessie transaction IDs that produced it. Dev A plumbs these through to claims, and Dev D renders them as clickable links. If you drop the IDs, the entire auditability story dies. Treat `evidence` as a required field, never an afterthought.

### `api/main.py` — FastAPI orchestrator

| Method | Path | Does |
|--------|------|------|
| `GET` | `/health` | `{ "ok": true }` |
| `GET` | `/customers` | cached customers with display names |
| `GET` | `/model/{customer_id}` | runs the fitter, returns `CashflowModel` |
| `POST` | `/ask` | the whole pipeline |
| `GET` | `/run/{run_id}` | stored `SimResult` |
| `GET` | `/txn/{txn_id}` | raw cached Nessie record |

**`POST /ask` is the only endpoint D calls.** Body: `{ "text": "...", "customer_id": "..." }`. You orchestrate:

1. Call C's `parse(text)` → `DecisionDelta`
2. Load `CashflowModel` from your fitter
3. Call A's `simulate()` and `attribute()` in-process (direct import, no HTTP)
4. Call C's `render(claims)` → prose, and C's `validate(prose, claims)`
5. Persist `SimResult` to SQLite keyed by `run_id`
6. Return `{ run_id, prose, replay_url, validated }`

Keep orchestration in your layer. This is deliberate: it means D makes exactly one HTTP call and never has to know that Gemini or the simulator exist. It is the single biggest coupling reduction in the whole design.

**Every stage wrapped in try/except with a defined fallback.** Parse fails → ask the user to rephrase. Render fails → templated prose. Simulation fails → that one we surface honestly, because there is no product without it.

---

## Your three blockers

### Blocker 1 — Round 1, Friday 7:00 PM, with **Dev A**
**What you need:** confirmation that your fitter's output actually drives A's engine without breaking it.

**Why it blocks:** your fixture-vs-reality gap is the biggest unknown on the team. Real Nessie cadences are irregular. If A's engine assumes clean integers and your fitter emits 29.6, somebody is wrong and you both need to know by 7 PM, not by midnight.

**Say this to A:** "Here are three real `CashflowModel` JSONs including one with messy income cadence. Run them. Tell me in 20 minutes if any field breaks you and whether you want `cadence_days` rounded or as a float."

**Done when:** A has run all three clean, and you've agreed on cadence rounding.

**If A isn't ready:** hand over the JSONs anyway, keep building the API against A's fixture `SimResult`, re-verify at Integration Checkpoint A.

---

### Blocker 2 — Round 2, Saturday 9:00 AM, with **Dev D**
**What you need:** agreement that `POST /ask` returns exactly the shape D's Photon agent needs, with real latency measured.

**Why it blocks:** D cannot ship a working message flow against a guess. And latency matters more than you think: if `/ask` takes 25 seconds, the iMessage thread looks dead and D needs to send a typing indicator or an interim message, which is a design change, not a tweak.

**Say this to D:** "Here's `/ask` running live against a real customer. Here's the actual p50 latency. Does your agent need an interim 'working on it' message, and do you want `replay_url` absolute or just the run_id?"

**Done when:** D has called your live endpoint from their agent and gotten a response, and you both know the real number of seconds.

---

### Blocker 3 — Round 3, Saturday 11:00 AM, with **Dev C**
**What you need:** confirmation that the transaction IDs in your `evidence` arrays all resolve through `GET /txn/{txn_id}`, so C's validator can actually verify them.

**Why it blocks:** C's validator checks that every evidence ID exists. If your fitter emits a synthesized ID, or an ID from an uncached account, every validation fails and C falls back to templates permanently. This is a quiet failure that nobody notices until the demo looks flat.

**Say this to C:** "Here are 50 evidence IDs pulled from a real model. Run your validator's existence check against `GET /txn/`. Any that 404, tell me now."

**Done when:** zero 404s across a real model's full evidence index.

---

## Hour by hour

| Window | Target |
|--------|--------|
| Fri 1–2 PM | Phase 0. You generate the fixture files everyone builds against, from real Nessie data if you can get it in an hour. This is your most important contribution all weekend. |
| Fri 2–4 PM | Ingest + cache 5 customers. Verify the Nessie origin that actually responds. |
| Fri 4–6 PM | Fitter: recurring detection working, evidence IDs plumbed. |
| Fri 6–7 PM | Three real `CashflowModel` JSONs ready for A. |
| **Fri 7:00** | **Handshake with A** |
| Fri 7:30–10 PM | FastAPI skeleton, all endpoints, `/ask` wired to A's core |
| Fri 10–11:30 PM | SQLite persistence, `/txn/` resolver, error fallbacks |
| Fri 11:30 PM | Integration Checkpoint A |
| Sat 8–9 AM | Measure real latency, pick the demo customer |
| **Sat 9:00** | **Handshake with D** |
| Sat 9:30–11 AM | Wire C's parse and render into `/ask` for real |
| **Sat 11:00** | **Handshake with C** |
| Sat 11:30–12 PM | Fix any evidence ID resolution gaps |
| **Sat 12:00** | Hard gate |
| Sat 1 PM+ | Deploy, freeze, support |

---

## Definition of done

- [ ] 5+ customers fully cached to local JSON, demo runs with the network unplugged
- [ ] Every `CashflowModel` field carries non-empty `evidence` with real Nessie transaction IDs
- [ ] `POST /ask` runs the full pipeline and returns prose + replay_url
- [ ] Every evidence ID resolves through `GET /txn/{txn_id}`, zero 404s
- [ ] Every pipeline stage has a try/except with a defined fallback
- [ ] p50 latency on `/ask` measured and told to D
- [ ] Demo customer chosen and announced to the team by Sat 9 AM

---

## Failure modes to avoid

**Fitting on live API calls during the demo.** Cache at ingest, read from cache at runtime. Non-negotiable.

**Dropping evidence IDs to simplify the fitter.** It is tempting at 10 PM when the recurring detector is being annoying. Don't. The IDs are the product.

**Letting the fitter get clever.** You do not need seasonality decomposition. Three charges, consistent spacing, consistent amount. Ship it and move on.

**Making D wait on orchestration.** Stub `/ask` to return a canned response in the first hour so D is never blocked on you. Fill in the real pipeline behind the same shape.
