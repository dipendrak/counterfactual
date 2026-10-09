# Counterfactual

A cached synthetic-bank population drives seeded cashflow simulations. See
[00-ARCHITECTURE.md](00-ARCHITECTURE.md) for the contracts and ownership boundaries.

Dev B's implementation is in `data/` and `api/`. It reads Nessie only during
ingestion; HTTP requests fit locally cached records and call `core.run.run` in
process. Dev C's Gemini modules and Dev D's replay frontend are separate work.

Install with Python 3.11 or newer:

```sh
uv venv .venv
uv pip install --python .venv/bin/python -e '.[dev]'
cp .env.example .env
```

For live data, set `NESSIE_API_KEY` in `.env`, then run:

```sh
.venv/bin/python -m data.ingest --count 5
.venv/bin/python -m data.audit --output-dir data/cache/models
.venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

The example configuration pins the verified HTTPS production origin. Obtain a
key at [Nessie’s production portal](https://prod.nessieisreal.com/). Ingest
records the successful origin in each snapshot. Cache files contain all
returned account resources; fitting uses the last 365 calendar days ending at
the snapshot's `as_of` date. Ingestion is read-only. Use repeated
`--customer-id ID` arguments to select specific customers. The count option
caches the first available customers; use `--offset` to inspect a later batch.
`--population auto` uses the enterprise population when your personal list is
empty. Use `--population owned` to limit ingestion to your personal list, or
`--population enterprise` explicitly. `--probe` reports accessible population
counts and sample account resource schemas. Inspect the audit report and ingest more
to choose five usable, different profiles. The report exposes fit failures,
income cadence, recurring count, breach rates, and unresolved evidence. Population
and ingest reports are saved under `data/cache/reports/`.

The live origin uses `payment_amount` for bills. A 404 for the legacy transfer
or loan list route is recorded in `unavailable_collections`; its empty local list
does not assert an observed absence of transactions. Deleted merchant references
are retained in raw purchases and recorded in `unavailable_merchants`. Merchant
labels then use the cached payee/description or original merchant ID. Other
request failures skip the affected customer and appear in the ingest report.

Restart the API after ingestion to load the new cache. Set `REPLAY_BASE_URL` to
Dev D's frontend origin; the API returns an absolute `/r/{run_id}` URL. The API
does not implement that frontend page.

For offline development without keys:

```sh
.venv/bin/python -m data.demo
.venv/bin/python -m data.audit --cache-dir data/cache/demo --output-dir data/cache/demo/models
COUNTERFACTUAL_CACHE_DIR=data/cache/demo COUNTERFACTUAL_DB_PATH=data/cache/demo/runs.sqlite3 COUNTERFACTUAL_PARSER=explicit .venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

These five customers are **generated fixtures**, not records fetched from Nessie.
Their names and snapshot metadata identify them as synthetic fixtures. The
fixtures are anchored to 2026-10-09 for repeatable fitting.

Example request:

```sh
curl http://127.0.0.1:8000/ask \
  -H 'Content-Type: application/json' \
  -d '{"customer_id":"demo_steady","text":"Can I afford $1400 today?"}'
```

The success shape is `{run_id, prose, replay_url, validated}`. The other routes
are `/health`, `/customers`, `/model/{customer_id}`, `/run/{run_id}`, and
`/txn/{txn_id}`. The additive `/run/{run_id}/inputs` route returns the exact
fitted model, parsed decision, and raw evidence used in that run. SQLite stores
these alongside the simulation and response. Transaction evidence from saved
runs remains available when a subsequent cache no longer contains it.

`COUNTERFACTUAL_PARSER=auto` uses Dev C's `llm.parse.parse(text)` when available.
Until then, a narrow offline parser accepts complete forms such as
`Can I afford $1400 today?`, `Can I afford $65/month starting today?`, and
`spend $25/week in 30 days`. It rejects unsupported wording, dates, multiple
amounts, and conditions. Set `COUNTERFACTUAL_PARSER=llm` to require Dev C's parser.
There is no Gemini implementation in Dev B's package; Dev C will also need
`GEMINI_API_KEY`.

Rendering calls `llm.render.render(claims, culprit)` and
`llm.validate.validate(prose, claims)`. Rejected renders retry once, then use a
deterministic template whose values are checked against the simulation and whose
evidence resolves locally. `validated: true` can therefore describe a checked
template; it does not imply a Gemini call. Parse failures/low confidence and
unfittable data return HTTP 422 with a rephrase or data error in `detail`.
Unknown resources return 404. Simulation or persistence failure returns 503
without a financial estimate or an unsaved replay link. Dev D should handle
these status codes and display the `detail` message.

Run checks and measure live HTTP latency:

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m api.benchmark --customer-id demo_steady --runs 10
```

Benchmarking creates saved runs. The measured development result and remaining
integration items are in [data/HANDSHAKE_NOTES.md](data/HANDSHAKE_NOTES.md).

The completed controlled synthetic population is cached in `data/cache/seeded/`:
five customers, five active checking accounts, 15 merchants, 114 deposits, and
380 purchases. **The user explicitly opted for integer USD cents stored in
Nessie, decoded to dollars in this app.** For example, raw `9450` represents
$94.50. Ordinary Nessie data retains dollar semantics. This is our application
convention; Nessie itself does not know these integers represent cents.

See [docs/MONEY_UNITS.md](docs/MONEY_UNITS.md) for the unit contract and
[SESSION_HANDOFF.md](SESSION_HANDOFF.md) for the approved design, actual profile
IDs, verification, and next steps. Alex's customer ID is
`39b2bdb2-e13c-4734-8522-1e2d43df6d1f`; the fitted opening balance is exactly
$2,310.40. Raw records and original evidence IDs are preserved. `/txn/{id}` adds
an `X-Monetary-Unit` response header, and saved `/run/{id}/inputs` includes an
`amount_units` mapping. The model, simulation, claims, prose, and decisions all
use dollars. An earlier partial dollar account is retained and excluded from
fitting, with its legacy records recorded in the migration journal.

The seed executor validates locally without requests, journals before each
POST, reconciles uncertain outcomes, and never blindly retries POSTs. Its
519 mapped active resources and original plan hash are in
`data/cache/seeded/reports/seed-journal.json`. The existing seed can be
re-ingested using the same plan and journal:

```sh
.venv/bin/python -m data.seed
.venv/bin/python -m data.seed --execute --ingest
.venv/bin/python -m data.audit --cache-dir data/cache/seeded --output-dir data/cache/seeded/models
COUNTERFACTUAL_CACHE_DIR=data/cache/seeded COUNTERFACTUAL_DB_PATH=data/cache/seeded/runs.sqlite3 COUNTERFACTUAL_PARSER=explicit .venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8002
.venv/bin/python -m api.benchmark --url http://127.0.0.1:8002 --customer-id 39b2bdb2-e13c-4734-8522-1e2d43df6d1f --verify-evidence
```

A fresh checkout can generate a **new** local seed proposal with
`python -m data.seed --write-plan`. It refuses to overwrite an existing plan.
Do not regenerate a plan to resume an existing journal: the original hashes
must match. To fetch this existing population from a fresh checkout, use
read-only `data.ingest --population owned` with the customer IDs listed in the
handoff; the remote account nicknames retain the unit and retirement markers.
Caches, journals, SQLite and credentials are gitignored.

Verification: **136 tests passed**, all five profiles fit and drive the actual
core, all **494 active evidence IDs resolve**, exact cent values match the
approved plan, and runs/units survive restart. The tracked summary is
[docs/seed-verification.json](docs/seed-verification.json). The earlier
enterprise-source cache and its reports remain in `data/cache/live-demo/`.
The seeded localhost HTTP benchmark measured a median of **0.01344 seconds**
over ten requests with one worker, explicit parser, checked template, and
SQLite. Gemini, Photon, deployment and frontend rendering are excluded.
