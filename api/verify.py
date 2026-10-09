"""Verify cached evidence, API replay persistence, and a real-model /ask offline."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from fastapi.testclient import TestClient

from api.main import create_app
from api.pipeline import Pipeline, explicit_parse
from data.cache import CacheRepository
from data.money import USD


def verify(cache_dir: Path, db_path: Path, customer_id: str, text: str) -> dict:
    def unavailable(*args):
        raise ModuleNotFoundError("Offline verification uses the checked template")
    pipeline = Pipeline(parser=explicit_parse, renderer=unavailable, validator=unavailable)
    settings = {"cache_dir": cache_dir, "db_path": db_path, "pipeline": pipeline}
    repo = CacheRepository(cache_dir)
    with TestClient(create_app(**settings)) as client:
        assert client.get("/health").json() == {"ok": True}
        response = client.get(f"/model/{customer_id}")
        response.raise_for_status()
        model = response.json()
        response = client.post("/ask", json={"customer_id": customer_id, "text": text})
        response.raise_for_status()
        answer = response.json()
        assert answer["validated"] is True
        response = client.get(f"/run/{answer['run_id']}")
        response.raise_for_status()
        result = response.json()
        assert len(result["paths"]) == 500
        evidence = set(model["evidence_index"])
        for claim in result["claims"]:
            evidence.update(claim["evidence"])
        for tid in sorted(evidence):
            transaction = client.get(f"/txn/{tid}")
            transaction.raise_for_status()
            assert transaction.json()["_id"] == tid
            assert transaction.json() == repo.transaction(tid)
            assert transaction.headers["X-Monetary-Unit"] == repo.transaction_unit(tid)
        inputs = client.get(f"/run/{answer['run_id']}/inputs").json()
        assert inputs["model"] == model
        assert inputs["amount_units"] == {"model": USD, "delta": USD,
                                          "evidence": {tid: repo.transaction_unit(tid) for tid in model["evidence_index"]}}
    with TestClient(create_app(**settings)) as client:
        assert client.get(f"/run/{answer['run_id']}").json() == result
        assert client.get(f"/run/{answer['run_id']}/inputs").json() == inputs
    return {"customer_id": customer_id, "run_id": result["run_id"], "n_paths": result["n_paths"],
            "baseline_breach_rate": result["baseline_breach_rate"], "delta_breach_rate": result["delta_breach_rate"],
            "culprit": None if result["culprit"] is None else result["culprit"]["label"],
            "evidence_ids_resolved": len(evidence), "missing_evidence": [], "persisted_after_restart": True,
            "model_monetary_unit": USD, "evidence_monetary_units": sorted(set(inputs["amount_units"]["evidence"].values())),
            "answer": answer}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=Path("data/cache"))
    parser.add_argument("--db-path", type=Path, default=Path("data/cache/reports/verification.sqlite3"))
    parser.add_argument("--customer-id", required=True)
    parser.add_argument("--text", default="Can I afford $1400 today?")
    args = parser.parse_args()
    print(json.dumps(verify(args.cache_dir, args.db_path, args.customer_id, args.text), indent=2))


if __name__ == "__main__":
    main()
