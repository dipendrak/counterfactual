# Dev A — handshake notes and open items

## Contract change: `essential` on recurring obligations (needs all four devs' yes)

**Problem.** Leave-one-out removes a whole bill. Money is fungible, so the biggest bill always
"fixes" the most breaches. On the fixture Rent won every time, which kills the demo line
"It isn't the flight. It's this."

**Decision (proposed by A).** An optional field is added to `CashflowModel.recurring[]`:

```json
{ "obligation_id": "rec_01", "label": "Rent", "...": "...", "essential": true }
```

- Default `false`, so a model without the field still parses. The change is additive; no field was renamed.
- `essential` = can't realistically be cancelled or shopped around: housing, loan and car payments.
  Insurance, phone, subscriptions and gyms are not essential.
- Every obligation is still ranked in `ranked_drivers`, and each `Driver` now carries `essential`.
- `culprit` = the top non-essential obligation with a positive contribution. It falls back to the top
  essential only if no avoidable bill contributes, and is `null` if nothing contributes.
- On the fixture: Rent tops the ranking (0.112). The culprit is Geico: the breach rate drops from 0.112 to 0.050 without it.

**Rejected:** ranking by breach drop per dollar. Because money is fungible, per-dollar scores
differ only by bill timing. A $15 bill that flips 2 of 500 paths would rank as the worst villain,
which is noise.

**Owner actions**
- **Dev B:** set `essential` in the fitter from Nessie merchant category. Without it, the old
  behaviour returns and Rent wins.
- **Dev C:** the claims are unchanged. `culprit_*` claims describe the avoidable culprit.
- **Dev D:** optional: show essential drivers greyed out on the replay page.

## Round 1 with Dev B — BLOCKED: need 3 real CashflowModel JSONs, one with messy income cadence
- Run each with `python -m core.demo path/to/model.json` (uses fixture delta).
- `cadence_days` and `next_due_day` accept floats. Occurrence days are
  `next_due_day + k * cadence_days`, floored into week `day // 7`. A negative `next_due_day`
  (past due) rolls forward. Proposal: B emits the mean spacing as a float, no rounding.
- `cadence_days <= 0` and duplicate `obligation_id` raise `ValueError` in `from_dict`. Fail loud in dev.
- Orchestrator entry point: `core.run.run(model, delta, seed, n_paths, horizon_weeks, breach_threshold, run_id=None) -> SimResult`.
  `run_id` is derived deterministically from inputs unless B passes one.

## Round 2 with Dev C — ACTION FOR DEV C: `culprit_label` has no unit
- The frozen `unit` enum is probability | usd | week | count. None of these fits a name.
- A currently emits `{"kind": "culprit_label", "value": "Geico", "unit": null, ...}`.
- **Dev C:** the validator must accept `unit: null` for `culprit_label` and treat `value` as a
  string to match against prose, not as a number. If C prefers a new unit (e.g. `"label"`), it's a
  one-line change on A's side. Decide at Round 2.
- Other semantics C needs:
  - `first_breach_week` = median first-breach week across breaching paths; omitted if no path breaches.
  - `breach_rate_with_delta` is omitted when there is no delta.
  - Claims with no evidence are dropped, not emitted.
  - Probabilities are rounded to 4 dp and usd to 2 dp. The claim values equal the `SimResult` fields exactly.

## Round 3 with Dev D — NOTE FOR DEV D: payload is ~508 KB, not ~400 KB
- Measured on the fixture: 500 paths, each with 105 balances, rounded to cents.
- `paths[i].balances[0]` is the opening balance and `balances[w]` is the end of week w.
  `first_breach_week` uses the same indexing.
- Ready if it janks: `core.payload.band_payload(result)` returns about 27 KB. It has the same summary
  fields as `SimResult`, `paths` cut to 20 representative full paths (spread across the min-balance
  ranking, including the worst and best), `paths_total`, and `band: {p10, p50, p90}`. Each band array
  has one value per balance index, with the same indexing as `balances`. `SimResult` itself is
  unchanged. Dev B can serve the band from `/run/{run_id}` or behind a query flag. Tell A which renders.

## Modelling notes
- Balances are checked at week end only; a mid-week dip that recovers by Sunday isn't a breach.
- A late bill slips one week and adds a $35 fee. Policy-vector ranges live at the top of `core/simulate.py`.
