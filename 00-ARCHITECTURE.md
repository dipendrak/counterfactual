# Counterfactual — Architecture at a Glance

**One line:** You text a money question. We fork your bank account into 500 synthetic futures, hand back a probability instead of a yes, and name the specific recurring charge that causes the bad ones.

**Stack:** Gemini API (parse + render only) · Nessie (synthetic population) · Photon/Spectrum (iMessage delivery)

---

## The pitch in one breath

Everyone else at this hackathon is building a finance chatbot on Nessie. Nessie's data is fake, and they will all spend the first 20 seconds of their demo apologizing for that. We don't. A synthetic bank is a population generator, and we use it as one.

Three things make us not-a-chatbot:

1. **Gemini never writes a number.** The simulation emits typed claims with Nessie transaction IDs attached. Gemini renders prose. A validator rejects any output containing a figure that doesn't trace to a claim.
2. **There is a named villain.** Leave-one-out attribution over the failing paths finds which recurring obligation actually drives the breach.
3. **Every run has a permanent replay URL.** Judges can click it mid-demo and see all 500 paths, the fitted model, and every transaction ID we used.

---

## System diagram

```
            iMessage
               │
               ▼
      ┌─────────────────┐
      │  Photon Agent   │  DEV D
      │  (Spectrum TS)  │
      └────────┬────────┘
               │ POST /ask { text, customer_id }
               ▼
      ┌─────────────────────────────────┐
      │     FastAPI Orchestrator        │  DEV B
      │                                 │
      │  1. parse  ──────────────────┐  │
      │  2. load model               │  │
      │  3. simulate                 │  │
      │  4. render + validate ────┐  │  │
      └───────┬──────────────┬────┼──┼──┘
              │              │    │  │
              ▼              ▼    │  │
   ┌──────────────────┐  ┌────────▼──▼────────┐
   │  Cashflow Fitter │  │   Gemini Layer     │  DEV C
   │  Nessie Cache    │  │  parse / render    │
   │      DEV B       │  │  claims validator  │
   └────────┬─────────┘  └────────────────────┘
            │ CashflowModel
            ▼
   ┌──────────────────────┐
   │  Simulation Core     │  DEV A
   │  500 paths × 24mo    │
   │  leave-one-out       │
   │  attribution         │
   │  emits Claim[]       │
   └──────────┬───────────┘
              │ SimResult
              ▼
   ┌──────────────────────┐
   │  Replay Page         │  DEV D
   │  /r/{run_id}         │
   └──────────────────────┘
```

---

## Ownership

| Dev | Owns | Language | Never touches |
|-----|------|----------|---------------|
| **A** | Simulation core, attribution, claim emission | Python 3.11 + numpy | Network, Gemini, HTTP |
| **B** | Nessie ingest + cache, cashflow fitter, FastAPI orchestrator | Python 3.11 + FastAPI | Prompts, frontend |
| **C** | Gemini parse, Gemini render, claims validator | Python 3.11 | Simulation math, Nessie calls |
| **D** | Photon agent, replay page, deploy | TypeScript, Next.js | Python entirely |

**Hard rule:** Dev A's package has zero network imports. No `requests`, no `httpx`, no `google.generativeai`. If it needs the internet, it isn't the core.

---

## The four frozen contracts

Agreed at Phase 0. Changing one after Friday 2pm requires all four devs to say yes out loud.

### 1. `CashflowModel` — B produces, A consumes

```json
{
  "customer_id": "6752a1...",
  "opening_balance": 2310.40,
  "fit_window_days": 365,
  "income": {
    "cadence_days": 14,
    "mean": 1850.00,
    "std": 120.00,
    "next_due_day": 6,
    "evidence": ["txn_abc", "txn_def"]
  },
  "recurring": [
    {
      "obligation_id": "rec_01",
      "label": "Geico",
      "merchant_id": "6752c9...",
      "amount": 142.00,
      "cadence_days": 30,
      "next_due_day": 12,
      "evidence": ["txn_111", "txn_222", "txn_333"]
    }
  ],
  "discretionary": {
    "weekly_mean": 210.00,
    "weekly_std": 85.00,
    "by_category": { "food": 0.42, "shopping": 0.31, "other": 0.27 },
    "evidence": ["txn_444"]
  },
  "evidence_index": ["txn_abc", "txn_111", "..."]
}
```

### 2. `DecisionDelta` — C produces, A consumes

```json
{
  "kind": "one_time",
  "amount": 1400.00,
  "start_day": 64,
  "cadence_days": null,
  "label": "flight in December",
  "raw_text": "should I take the $1400 flight to see my family in december",
  "confidence": 0.91
}
```

`kind` is `"one_time"` or `"recurring"`. If `recurring`, `cadence_days` is required.

### 3. `Claim` — A produces, C renders and validates

```json
{
  "claim_id": "c3",
  "kind": "breach_rate_with_delta",
  "value": 0.27,
  "unit": "probability",
  "evidence": ["txn_111", "txn_222"]
}
```

`unit` is one of `probability` | `usd` | `week` | `count`.

Claim `kind` vocabulary (frozen, A owns this list):
`baseline_breach_rate`, `breach_rate_with_delta`, `culprit_label`, `culprit_marginal_contribution`, `culprit_amount`, `median_min_balance`, `first_breach_week`, `paths_simulated`

### 4. `SimResult` — A produces, B serves, C and D consume

```json
{
  "run_id": "uuid4",
  "seed": 1337,
  "n_paths": 500,
  "horizon_weeks": 104,
  "breach_threshold": 0.00,
  "baseline_breach_rate": 0.11,
  "delta_breach_rate": 0.27,
  "median_min_balance": -210.00,
  "culprit": {
    "obligation_id": "rec_01",
    "label": "Geico",
    "amount": 142.00,
    "breach_rate_without": 0.08,
    "marginal_contribution": 0.19,
    "evidence": ["txn_111", "txn_222"]
  },
  "ranked_drivers": [],
  "paths": [
    { "path_id": 0, "balances": [2310.4], "min_balance": -210.0, "first_breach_week": 31 }
  ],
  "claims": []
}
```

---

## HTTP surface (B owns, D consumes)

| Method | Path | Returns |
|--------|------|---------|
| `GET` | `/health` | `{ "ok": true }` |
| `GET` | `/customers` | list of `{ customer_id, name }` |
| `GET` | `/model/{customer_id}` | `CashflowModel` |
| `POST` | `/ask` | `{ run_id, prose, replay_url, validated }` |
| `GET` | `/run/{run_id}` | `SimResult` |
| `GET` | `/txn/{txn_id}` | raw cached Nessie record |

`POST /ask` body: `{ "text": "...", "customer_id": "..." }`

---

## Timeline

| When | What | Who |
|------|------|-----|
| **Fri 1:00–2:00 PM** | Phase 0 — contract freeze, fixture generation | All four, same table |
| Fri 2:00–7:00 PM | Phase 1 — solo build against fixtures | Solo |
| **Fri 7:00–7:30 PM** | **Handshake Round 1** — A↔B, C↔D | Pairs |
| Fri 7:30–11:30 PM | Phase 2 | Solo |
| **Fri 11:30 PM–12:30 AM** | **Integration Checkpoint A** — end-to-end with fakes allowed | All four |
| Fri 12:30 AM | Sleep. Actually sleep. | All four |
| Sat 8:00–9:00 AM | Phase 3 | Solo |
| **Sat 9:00–9:30 AM** | **Handshake Round 2** — A↔C, B↔D | Pairs |
| Sat 9:30–11:00 AM | Phase 3 cont. | Solo |
| **Sat 11:00–11:30 AM** | **Handshake Round 3** — A↔D, B↔C | Pairs |
| Sat 11:30 AM–12:00 PM | Final solo pass | Solo |
| **Sat 12:00–1:00 PM** | **FULL INTEGRATION — hard gate** | All four |
| Sat 1:00–2:30 PM | Demo path hardening + Devpost writeup | All four |
| Sat 2:30–4:00 PM | Rehearse three times, freeze the repo | All four |
| **Sat 4:00 PM** | Done. Go outside. | All four |

### The hard gate

At **Sat 12:00 PM**, this must work end to end: text a question from a real phone, get back a probability, a named recurring charge, and a working replay link.

If it doesn't work at 12:00, we cut in this order:
1. Replay page becomes a static screenshot
2. Gemini render falls back to templated prose permanently
3. Drop to 100 paths
4. Hardcode one customer

We do not cut: the simulation, the attribution, or the Photon delivery. Those three are the project.

---

## Handshake map

Three rounds. Two pairs per round. Every dev meets every other dev exactly once.

```
Round 1 (Fri 7:00 PM)     Round 2 (Sat 9:00 AM)     Round 3 (Sat 11:00 AM)
    A ──── B                  A ──── C                  A ──── D
    C ──── D                  B ──── D                  B ──── C
```

Each handshake is **30 minutes, timeboxed, screens side by side**. The deliverable is a passing test, not a conversation. If a handshake runs past 30 minutes, the pair writes down the smallest unblocking fake, ships that, and moves on.

---

## Demo script (Sat 2:30, rehearse verbatim)

1. "Everyone here built a finance chatbot on fake bank data. We built the thing fake bank data is actually good for." (10s)
2. Pick up phone, text the agent a real question out loud. (15s)
3. Response lands. Read the probability out loud. (15s)
4. "It isn't the flight. It's this." Point at the named charge. (15s)
5. Open the replay URL on the projector. Scroll the 500 paths. Click one transaction ID. (45s)
6. "Gemini never saw a number in this pipeline. It renders claims, and a validator rejects anything it can't trace." Show the validator rejecting a seeded bad render. (30s)
7. Stop talking.

Total: under 2:30. Leave room for questions.
