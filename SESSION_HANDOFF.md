# Session handoff — Dev B data/API

Updated 2026-10-09. Workspace: `/Users/aaravtiwari/counterfactual`.

## Explicitly approved design: integer cents in Nessie, dollars in our app

**The user opted for storing this engineered synthetic seed's money as integer
USD cents in Nessie and converting it back to USD dollars in our app.** They
approved this with “alright, lets do this then” and explicitly requested that
other sessions know this is a design choice. This supersedes the previous
`blocked_exact_amounts` handoff. **No cent is discarded and whole-dollar
truncation is not authorized.**

Examples: $94.50 → Nessie `amount: 9450` → model/display $94.50;
$2,310.40 → Nessie `balance: 231040` → model balance 2310.40. Nessie itself
interprets these integer fields according to its existing dollar API; the cents
encoding is **our application convention**, scoped to this synthetic population.
Ordinary Nessie data remains dollars. Never guess a unit from an amount's size.

Read [docs/MONEY_UNITS.md](docs/MONEY_UNITS.md) for the integration contract.
`data/money.py` uses Decimal at the encoding/decoding boundary. Fresh seed
journals use `storage_unit: usd_cents` and `monetary_policy:
integer_cents_storage`. Raw records and original server IDs remain unchanged in
the cache/evidence. Models, decision deltas, simulation balances, claims, and
prose keep the existing dollar contracts; the core is unchanged.

Each cents account's remote nickname begins `Synthetic USD cents `.
`data.ingest` recognizes this explicit marker and records bundle metadata
`monetary_unit: usd_cents`. Ordinary accounts/old snapshots default to `usd`.
The fitter converts per bundle and fails if a cents marker lacks metadata or
if a unit is unknown. The fixture generator now uses Decimal multiplication.
The original approved proposal/hash is unchanged; one-float-step multiplication
residue in that older proposal is canonicalized to its exact cent value, while
true sub-cent inputs are rejected.

## Current outcome and push authorization

**The approved seed is complete and verified.** After receiving the readiness
report, the user explicitly authorized switching to `dev-data-b`, committing
with the exact message `Dev B finished`, and pushing to `origin`. This
supersedes the earlier instruction to wait for branch/push authorization.
Use Git history and remote tracking status to verify the publication result.

Created and ingested: **five customers, five active checking accounts, fifteen
merchants, 114 deposits and 380 purchases (494 active transactions)**.
All active money readbacks match the exact cent encoding. The durable journal
at `data/cache/seeded/reports/seed-journal.json` has `status: complete`, 519
unique mapped server IDs, and the original approved plan hash:
`dc41eaea75cb2be148ef64dd44849a03a6fb4fac6f2293714991a4e110beb87e`.

An earlier partial dollar attempt left one extra checking account, two deposits
and one purchase. That account (`33ad0f0b-1bf3-4f54-ba8f-40e0d263acaa`) is retained
with nickname prefix `Superseded dollar seed ` and `include_in_fit: false`.
It is excluded from active balances and histories, so no double counting occurs.
The migration reused Alex's customer and the three existing merchants. Legacy
operations/IDs remain in `cent_migration.legacy_operations`; the pre-migration
journal is `data/cache/seeded/reports/pre-cents-seed-journal.json`. No DELETE was
issued. The raw cache contains 497 transactions including those three excluded
legacy records; the active models/evidence contain exactly 494.

## Actual profile IDs and outcomes

The following rates use a $1,400 immediate expense, seed 1337, 500 paths, and
104 weeks. These are synthetic simulations, not real-world accuracy claims.

| Profile | Name | Nessie customer ID | Opening balance USD | Evidence IDs | Baseline breach | Scenario breach | Culprit |
|---|---|---|---:|---:|---:|---:|---|
| demo_monthly | Ellis | `27dc775c-a552-453f-831b-5cf060de367c` | 900.00 | 88 | 1.000 | 1.000 | none |
| demo_steady | Alex | `39b2bdb2-e13c-4734-8522-1e2d43df6d1f` | 2310.40 | 102 | 0.000 | 0.040 | Geico |
| demo_cushion | Drew | `4ba43709-2dbc-483e-8ca4-cf1959052b71` | 16000.00 | 102 | 0.000 | 0.000 | none |
| demo_irregular | Blair | `62be185a-effc-4f17-bd5e-61b2336836e8` | 1100.00 | 100 | 0.036 | 1.000 | none |
| demo_heavy | Casey | `75414169-d39d-4cbe-aed5-765c496ccbb7` | 750.00 | 102 | 0.992 | 1.000 | none |

**Recommended seeded demo: Alex**, customer
`39b2bdb2-e13c-4734-8522-1e2d43df6d1f`, active account
`4dfd528c-84c8-444f-bdf7-fe4fb59c581a`. All profiles have two detected obligations
(Rent and Geico). Blair retains irregular observed income gaps with a fitted
median cadence of 14 days. Histories are engineered across roughly one year,
anchored to 2026-10-09; `fit_window_days: 365` is a lookback, not a guarantee of
365 daily observations. When a $1,400 day-zero expense itself forces a breach,
attribution may correctly return no contributing recurring culprit.

## Verification and reports

- **136 tests passed** across core, data and API, with one Starlette/HTTPX
  deprecation warning. `git diff -- core` is empty.
- All five cents models fit and drive the actual 500-path core. Tests compare
  all five encoded models and simulation results with their dollar originals.
- `api.verify` checked **494 evidence IDs**, zero missing IDs, raw record equality,
  monetary-unit headers, saved run inputs, and persistence across app restart.
- Tests also cover cents evidence after cache removal and dollar defaults for
  older SQLite runs, mixed account units, retired account exclusion, interrupted
  migration, delayed read visibility, and no duplicate POST after timeout.
- Live HTTP benchmark: ten `/ask` requests on Alex, one Uvicorn worker, explicit
  parser, checked template, SQLite. p50 **0.01344 seconds**, first/max
  **0.02958**, minimum **0.01296**. It fetched health/model/run/
  inputs and all 102 evidence records, including their unit headers. Gemini,
  Photon, deployment, and frontend rendering are excluded.

Tracked summary for other checkouts: [docs/seed-verification.json](docs/seed-verification.json).
Local detailed reports: `data/cache/seeded/reports/{seed-journal,precision,audit,
verification,benchmark,ingest,production-behavior}.json`. Exported models:
`data/cache/seeded/models/`. Exact inputs/runs: `data/cache/seeded/runs.sqlite3`.
Caches, journals, SQLite and `.env` are gitignored; the tracked summary contains
synthetic IDs and verification results, no credentials. Do not regenerate the
plan to resume this seed: the stored original plan and journal hashes must match.

## API and C/D integration

Frozen routes remain `/health`, `/customers`, `/model/{customer_id}`,
`POST /ask`, `/run/{run_id}`, and `/txn/{txn_id}`. `/ask` body is
`{text, customer_id}` and success is exactly
`{run_id, prose, replay_url, validated}`. Errors are 404 for missing resources,
422 for parsing/unfittable data, and 503 for simulation/persistence failures.
Runtime uses local cache and `core.run.run` in process, never Nessie.

**Raw evidence unit contract for all other sessions:**

- `/txn/{id}` returns the unmodified raw server record, with header
  `X-Monetary-Unit: usd_cents` or `usd`. Divide cents monetary fields by 100
  for dollar display. Preserve the original `_id`.
- `/run/{id}/inputs` returns `{model, delta, evidence, amount_units}`.
  `amount_units` is `{model: "usd", delta: "usd", evidence: {id: unit}}`.
  The mapping is saved alongside the run and survives cache replacement.
- SQLite unit metadata is additive. Older saved runs without unit rows default
  to dollars. Conflicting archived raw records/units reject a save.
- `CashflowModel`, `DecisionDelta`, `SimResult`, claims and prose use dollars.
  Dev C and Dev D must use the raw evidence unit mapping for any evidence display.

Dev C's `llm/` package/key and Dev D's replay frontend/Photon caller are still
absent. Expected C adapters: `llm.parse.parse(text)`,
`llm.render.render(claims, culprit)`, `llm.validate.validate(prose, claims)`.
Validator `passed` must be literal boolean. Rendering retries once then uses a
checked deterministic template. `validated: true` does not imply Gemini ran.
The explicit parser handles forms such as “Can I afford $1400 today?” and
“Can I afford $65/month starting today?”, rejecting unsupported ambiguity.
Replay URLs point at D's future `/r/{run_id}` page using `REPLAY_BASE_URL`.
No replay frontend is supplied by this Python stage.

## Running and resuming

A dedicated seeded API worker was started at `http://127.0.0.1:8002`, process
947, exec session 31098. Check it before assuming it survived a session switch.
Other fixture/live-demo workers were left alone.

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m data.audit --cache-dir data/cache/seeded --output-dir data/cache/seeded/models
.venv/bin/python -m api.verify --cache-dir data/cache/seeded --db-path data/cache/seeded/runs.sqlite3 --customer-id 39b2bdb2-e13c-4734-8522-1e2d43df6d1f
.venv/bin/python -m api.benchmark --url http://127.0.0.1:8002 --customer-id 39b2bdb2-e13c-4734-8522-1e2d43df6d1f --verify-evidence
COUNTERFACTUAL_CACHE_DIR=data/cache/seeded COUNTERFACTUAL_DB_PATH=data/cache/seeded/runs.sqlite3 COUNTERFACTUAL_PARSER=explicit .venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8002
```

`data.seed --execute --ingest` can resume/re-ingest using the existing plan and
journal without recreating mapped records. New checkouts can regenerate a new
local proposal with `data.seed --write-plan` (refuses overwrite); that is a new
plan, not recovery of this journal. To read these existing seeded profiles from
another checkout, use read-only `data.ingest --population owned` with their
customer IDs; account nicknames carry the unit/retirement markers durably.
`--migrate-to-cents` exists for an earlier partial dollar journal and is a no-op
after migration. Seed writes require the pinned HTTPS production origin.

## Environment, boundaries and remaining work

`.env` contains the Nessie key and pins `https://prod-api.nessieisreal.com`.
Never print it or credential-bearing URLs; never send the key to legacy HTTP.
The supplied key is sufficient for Nessie. Python 3.11 and `.venv` are available.
Network calls/port binding require sandbox escalation when the shell blocks them.
Follow the tool approval mechanism. User approval of the synthetic population
and integer-cent design persists; do not ask for it again.

Read `00-ARCHITECTURE.md`, `02-DEV-B-DATA-API.md`, `core/HANDSHAKE_NOTES.md`,
`03-DEV-C-GEMINI-CLAIMS.md`, and `data/HANDSHAKE_NOTES.md` for stage boundaries.
A's core was not edited. Five earlier enterprise profiles remain in
`data/cache/live-demo/`, with their historical 337-ID verification preserved.

Official schema: `https://prod.nessieisreal.com/nessie-openapi-spec.yaml`;
purchase POST fields from the official Nessie JavaScript SDK. Direct dollar
floats were observed to truncate, decimal-string purchase PUT was rejected as
non-integer, and AccountUpdate did not change balance. Those findings are
historical context for the approved encoding, not a current seed blocker.

Ruflo/ToolSearch tools remain unavailable in the supplied metadata. No subagents
or teammate messages were used. Claim-discipline evidence rules were applied;
its connectome-specific provisions do not apply. No user work was deleted.

Remaining: integrate Dev C/D and repeat deployed/Photon verification.
Gemini and frontend integration remain
external to B's completed data/API stage.
