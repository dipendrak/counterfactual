# Dev B handoff

## User-approved monetary design — applies to all sessions

The user explicitly opted for **integer USD cents stored in Nessie** for the
controlled synthetic population and **USD dollars in the app's model/simulation
contracts**. No cent is discarded. This is an app convention; Nessie itself has
not changed its integer dollar schema. Example: raw amount 9450 represents
$94.50; raw Alex balance 231040 represents $2,310.40.

Full specification: [docs/MONEY_UNITS.md](../docs/MONEY_UNITS.md).
Current session state and push authorization: [SESSION_HANDOFF.md](../SESSION_HANDOFF.md).
Do not reintroduce dollar truncation or apply cent conversion to ordinary
Nessie accounts. Account nicknames explicitly mark the cents population;
ingest records `monetary_unit: usd_cents` on each bundle. Missing cents metadata
or unknown units fail fitting. Models, deltas, claims, and prose remain dollars.
Raw server records/IDs remain unmodified. Decimal handles the unit boundary;
the core is unchanged.

## Completed Nessie seed and verification

As of 2026-10-09: five customers, five active checking accounts, fifteen merchants,
114 deposits and 380 purchases (494 active transactions). Every active amount
and balance read back as the exact approved cent encoding. The original plan
hash remains `dc41eaea75cb2be148ef64dd44849a03a6fb4fac6f2293714991a4e110beb87e`.

| Profile | Customer ID | Opening balance USD | Evidence IDs |
|---|---|---:|---:|
| demo_steady | `39b2bdb2-e13c-4734-8522-1e2d43df6d1f` | 2310.40 | 102 |
| demo_irregular | `62be185a-effc-4f17-bd5e-61b2336836e8` | 1100.00 | 100 |
| demo_heavy | `75414169-d39d-4cbe-aed5-765c496ccbb7` | 750.00 | 102 |
| demo_cushion | `4ba43709-2dbc-483e-8ca4-cf1959052b71` | 16000.00 | 102 |
| demo_monthly | `27dc775c-a552-453f-831b-5cf060de367c` | 900.00 | 88 |

The journal at `data/cache/seeded/reports/seed-journal.json` is complete with
519 unique mapped IDs. The former partial dollar account
`33ad0f0b-1bf3-4f54-ba8f-40e0d263acaa`, two deposits and one purchase are retained
for audit with a `Superseded dollar seed ` nickname and `include_in_fit: false`.
They do not enter the model. Alex's active cents account is
`4dfd528c-84c8-444f-bdf7-fe4fb59c581a`. All legacy operation IDs are archived in
`cent_migration.legacy_operations`. No DELETE or duplicate POST was made.
The raw cache includes 497 transactions; exactly 494 enter the active models.

**136 tests passed** with one Starlette/HTTPX deprecation warning. All five
models drive 500 paths over 104 weeks. All 494 model/claim evidence IDs resolve
through the actual handlers with raw record equality and matching unit headers.
Saved inputs/results/unit metadata survive app restart. Tests cover evidence
surviving cache removal, old SQLite dollar defaults, all-five dollar/cent model
and simulation equality, mixed units, retired account exclusion, interruption,
and delayed readback without duplicate POSTs.

Summary for other checkouts: `docs/seed-verification.json`. Detailed local
reports: `data/cache/seeded/reports/{precision,audit,verification,benchmark,
ingest,seed-journal}.json`. Exported dollar models: `data/cache/seeded/models/`.
The original local plan and `.env` remain gitignored and unchanged.

## A: fitter/core boundary

Cadences use rounded median gaps. Recurring groups need at least three charges,
gaps within four days of the median, and amounts within fifteen percent of the
median. Income uses the largest posted deposit-description group with at least
three observations; irregular gaps retain their original evidence. The fitter
converts each account bundle's money to dollars before these calculations.
Rent/housing/loan categories set A's optional `essential` flag; Geico remains
nonessential. Zero spend has zero moments and no invented evidence. Checking/
Savings balances count, credit accounts/internal transfers do not become income.
Cash withdrawals/outbound external transfers become discretionary; loan objects
are not payment histories. Weekly statistics include zero weeks and normalize
the trailing partial week. The fit date is cached `as_of`, currently 2026-10-09.

All five models have two detected obligations. For Alex and $1400 today, baseline
breach is 0.0, scenario breach is 0.04, culprit is Geico. The full summary records
the other profiles' outcomes, including null culprits where no recurring removal
improves a day-zero breach. Teammate confirmation remains a separate handshake.

## C: claims/evidence boundary

`llm/` is absent. Expected adapters remain `llm.parse.parse(text)`,
`llm.render.render(claims, culprit)`, and `llm.validate.validate(prose, claims)`.
Validator `passed` must be literal boolean. Rendering retries once and falls
back to a checked deterministic template. `validated: true` does not imply a
Gemini call. C needs its Gemini key and must align relative dates with model
`as_of`. Probability claims are fractions; `culprit_label` has unit null.

**Evidence units:** `/txn/{id}` returns raw server JSON, with header
`X-Monetary-Unit: usd_cents` or `usd`. Raw 9450 becomes $94.50 for display only.
`/run/{id}/inputs` adds persisted `amount_units` with dollar model/delta and a
unit for each raw evidence ID. C must keep original IDs and use this mapping for
any raw-money comparison. Runtime evidence checks remain offline.

## D: HTTP/replay boundary

`POST /ask` takes `{text, customer_id}`, returns exactly
`{run_id, prose, replay_url, validated}`, and uses 404/422/503 errors with detail.
`GET /run/{id}` retains the core result schema. `GET /run/{id}/inputs` returns
`{model, delta, evidence, amount_units}`; old SQLite runs default to dollars.
Dollar units in the model/delta/result are unchanged. D must inspect evidence
units before showing raw financial amounts. Replay URLs use `REPLAY_BASE_URL`;
D's replay frontend/Photon caller are still absent.

Seeded localhost worker: `http://127.0.0.1:8002`, process 947, exec session 31098
(check survival before relying on it). Ten HTTP `/ask` requests, one worker,
explicit parser, checked template and SQLite: p50 **0.01344 seconds**,
first/max **0.02958**, minimum **0.01296**. Benchmark also
fetched health/model/run/inputs and all 102 Alex evidence records with unit
headers. Gemini, Photon, deployment and frontend rendering are excluded.
Repeat when C/D and deployment are integrated.

## Historical enterprise data and remaining work

`data/cache/live-demo/` and its reports are preserved: five earlier usable
enterprise profiles, 337 evidence checks, zero missing IDs, earlier p50 0.01129
seconds on Demo Student. Those observed histories were shorter than a year;
they motivated the now-complete designed population. The live production
transfer list still returns 404 and is explicitly recorded as unavailable,
not treated as observed zero. Bills use `payment_amount`; missing merchant
records retain raw IDs and fallback labels.

After the readiness report, the user explicitly authorized branch `dev-data-b`,
commit message `Dev B finished`, and a push to `origin`. Verify the publication
result through Git history and remote tracking status. Remaining: Dev C's
Gemini package/key, Dev D's replay/Photon integration, and deployed end-to-end
verification. No teammate messages have been sent.
