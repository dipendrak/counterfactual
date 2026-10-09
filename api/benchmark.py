"""Measure live HTTP /ask latency for the Dev D handoff (creates persisted runs)."""
from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--customer-id", required=True)
    parser.add_argument("--text", default="Can I afford $1400 today?")
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--verify-evidence", action="store_true", help="Check replay and every model/claim evidence ID through live HTTP")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")
    samples = []
    verification = {}
    with httpx.Client(base_url=args.url.rstrip("/"), timeout=90) as client:
        for _ in range(args.runs):
            start = time.perf_counter()
            response = client.post("/ask", json={"text": args.text, "customer_id": args.customer_id})
            response.raise_for_status()
            if response.json().get("validated") is not True:
                raise RuntimeError("Benchmark response was not validated")
            samples.append(time.perf_counter() - start)
        if args.verify_evidence:
            run_id = response.json()["run_id"]
            health = client.get("/health")
            health.raise_for_status()
            assert health.json() == {"ok": True}
            model_response = client.get(f"/model/{args.customer_id}")
            model_response.raise_for_status()
            result_response = client.get(f"/run/{run_id}")
            result_response.raise_for_status()
            result = result_response.json()
            inputs_response = client.get(f"/run/{run_id}/inputs")
            inputs_response.raise_for_status()
            assert inputs_response.json()["model"] == model_response.json()
            units = inputs_response.json()["amount_units"]
            assert units["model"] == units["delta"] == "usd"
            assert len(result["paths"]) == result["n_paths"]
            evidence = set(model_response.json()["evidence_index"])
            for claim in result["claims"]:
                evidence.update(claim["evidence"])
            for tid in sorted(evidence):
                transaction = client.get(f"/txn/{tid}")
                transaction.raise_for_status()
                assert transaction.json()["_id"] == tid
                assert transaction.headers["X-Monetary-Unit"] == units["evidence"][tid]
            verification = {"run_id": run_id, "evidence_ids_resolved": len(evidence), "missing_evidence": [],
                            "n_paths": result["n_paths"], "baseline_breach_rate": result["baseline_breach_rate"],
                            "delta_breach_rate": result["delta_breach_rate"],
                            "culprit": None if result["culprit"] is None else result["culprit"]["label"]}
            verification["evidence_monetary_units"] = sorted(set(units["evidence"].values()))
    report = {"url": args.url, "customer_id": args.customer_id, "runs": len(samples),
              "p50_seconds": statistics.median(samples), "first_seconds": samples[0],
              "min_seconds": min(samples), "max_seconds": max(samples), **verification}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
