# Evidence for implementation claims

These claims describe the synthetic data/API implementation, not real-world
financial accuracy. The user explicitly approved integer-cent storage for this
controlled Nessie population; [MONEY_UNITS.md](MONEY_UNITS.md) defines the
application convention and distinguishes raw cents from dollar models.

| Claim | Evidence | Verified by |
|---|---|---|
| HTTP runtime uses cached records and does not call Nessie | `data/cache.py`, `api/main.py`, transport prohibition in `api/tests/test_api.py` | Codex, pytest |
| Models preserve original server transaction IDs | `data/fit.py`, full evidence resolver tests, `docs/seed-verification.json` | Codex, pytest and actual API handlers |
| User-approved seed stores integers representing exact USD cents | `data/seed.py`, `data/money.py`, `docs/MONEY_UNITS.md`, complete seed journal and precision report | Codex, authenticated POST/readback |
| All five cents models and simulations match dollar originals | `data/tests/test_money.py::test_all_five_cent_models_and_simulations_equal_dollar_originals` | Codex, pytest |
| Five active accounts and 494 transactions match the approved cent encoding | `docs/seed-verification.json`, `data/cache/seeded/reports/precision.json`, original plan hash | Codex, all-record comparison |
| All 494 active evidence IDs resolve with correct raw records and units | `data/cache/seeded/reports/verification.json`, tracked summary | Codex, `api.verify` |
| Superseded dollar account is retained and excluded from fitting | Journal `cent_migration`, `data/fit.py`, `data/tests/test_money.py` | Codex, authenticated rename/readback and pytest |
| Interrupted creation/migration does not blindly retry POSTs | `data/tests/test_seed.py`, journal; delayed visibility retries only GET | Codex, MockTransport tests and live resume |
| Raw evidence and monetary units survive restart/cache removal | `api/store.py`, `api/tests/test_api.py::test_cents_evidence_and_units_survive_restart_and_cache_removal` | Codex, pytest and actual handlers |
| Old saved runs without unit rows remain dollar-based | `api/tests/test_api.py::test_existing_database_without_unit_rows_defaults_to_dollars` | Codex, pytest |
| Rendering retries once then uses a checked deterministic template | `api/main.py`, render/validator failure tests | Codex, pytest |
| API does not return estimates after simulation/persistence failure | Failure tests in `api/tests/test_api.py` | Codex, pytest |
| Core code/contracts remain unchanged | `git diff -- core` | Codex, local diff |
| 136 tests passed | Full `.venv/bin/python -m pytest -q`; recorded in handoff and tracked summary | Codex, local execution |
| Seeded localhost HTTP p50 was 0.01344 seconds over ten requests | `docs/seed-verification.json`, detailed benchmark; one worker/explicit parser/template/SQLite, excluding Gemini/Photon/deployment | Codex, live localhost HTTP |
| Earlier enterprise cache remains available | `data/cache/live-demo/`, prior 337-ID report | Codex, local inspection |
| The original approved proposal is unchanged | Stable SHA-256 in tracked summary and journal; original local proposal | Codex, hash comparison |
| Numeric dollar decimals truncated and decimal-string updates failed on production | Historical `data/cache/seeded/reports/production-behavior.json` and journal repairs | Codex, authenticated POST/PUT/readback |
| Gemini and replay/Photon integration remain pending | No `llm/` package or frontend in this checkout; handoff | Codex, repository inspection |

The connectome-specific licensing/scientific provisions of the personal
claim-discipline skill do not apply to this banking project.
