"""Export fitted handoff models and check all their cached evidence IDs."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from core.model import DecisionDelta
from core.run import run
from data.cache import CacheRepository
from data.fit import FitError, fit


def audit(directory: Path, output_dir: Path | None = None) -> list[dict]:
    repo = CacheRepository(directory)
    report = []
    for customer in repo.customers():
        cid = customer["customer_id"]
        snapshot = repo.snapshot(cid)
        entry = {**customer, "source": snapshot.get("source", "unknown")}
        entry["population_origin"] = snapshot.get("population_origin", snapshot.get("source", "unknown"))
        entry["excluded_accounts"] = [b["account"]["_id"] for b in snapshot["accounts"]
                                      if b.get("include_in_fit", True) is False]
        entry["unavailable_collections"] = snapshot.get("unavailable_collections", [])
        entry["unavailable_merchants"] = snapshot.get("unavailable_merchants", [])
        try:
            model = fit(snapshot)
            missing = [tid for tid in model.evidence_index if tid not in repo.transactions]
            if missing:
                raise FitError(f"Unresolved evidence: {missing}")
            result = run(model, DecisionDelta("one_time", 1400, 0))
            entry.update(fitted=True, evidence_ids=len(model.evidence_index), missing_evidence=[],
                         opening_balance_usd=model.opening_balance,
                         model_monetary_unit="usd",
                         evidence_monetary_units=sorted({repo.transaction_unit(tid) for tid in model.evidence_index}),
                         income_cadence_days=model.income.cadence_days, recurring_count=len(model.recurring),
                         baseline_breach_rate=result.baseline_breach_rate, delta_breach_rate=result.delta_breach_rate,
                         culprit=None if result.culprit is None else result.culprit.label)
            if output_dir:
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / f"{cid}.json").write_text(json.dumps(model.to_dict(), indent=2, allow_nan=False))
        except (ValueError, KeyError) as exc:
            entry.update(fitted=False, reason=str(exc))
        report.append(entry)
    return report


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=Path(os.getenv("COUNTERFACTUAL_CACHE_DIR", "data/cache")))
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.cache_dir, args.output_dir), indent=2))


if __name__ == "__main__":
    main()
