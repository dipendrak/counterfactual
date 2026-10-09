"""Single entry point for Dev B: CashflowModel + DecisionDelta -> SimResult."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import replace

import numpy as np

from core.attribute import rank_drivers
from core.claims import emit_claims
from core.model import CashflowModel, DecisionDelta, Driver, PathRecord, SimResult
from core.simulate import flow_components, make_noise, paths_from_components

PROB_DP = 4
USD_DP = 2


def _round_driver(d: Driver) -> Driver:
    return replace(
        d,
        breach_rate_without=round(d.breach_rate_without, PROB_DP),
        marginal_contribution=round(d.marginal_contribution, PROB_DP),
    )


def derive_run_id(model: CashflowModel, delta: DecisionDelta | None, seed: int, n_paths: int, horizon_weeks: int) -> str:
    """Same inputs, same run_id, so a replay URL is reproducible. No uuid4, no clock."""
    payload = json.dumps(
        {
            "model": model.to_dict(),
            "delta": None if delta is None else delta.to_dict(),
            "seed": seed,
            "n_paths": n_paths,
            "horizon_weeks": horizon_weeks,
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode()).digest()
    return str(uuid.UUID(bytes=digest[:16], version=4))


def run(
    model: CashflowModel,
    delta: DecisionDelta | None,
    seed: int = 1337,
    n_paths: int = 500,
    horizon_weeks: int = 104,
    breach_threshold: float = 0.0,
    run_id: str | None = None,
) -> SimResult:
    noise = make_noise(seed, n_paths, horizon_weeks, len(model.recurring))
    comp = flow_components(model, delta, noise)

    baseline = paths_from_components(comp, breach_threshold, include_delta=False)
    scenario = paths_from_components(comp, breach_threshold, include_delta=True) if delta is not None else baseline

    culprit, drivers = rank_drivers(model, comp, breach_threshold, include_delta=delta is not None)
    culprit = None if culprit is None else _round_driver(culprit)
    drivers = [_round_driver(d) for d in drivers]

    baseline_rate = round(baseline.breach_rate, PROB_DP)
    delta_rate = None if delta is None else round(scenario.breach_rate, PROB_DP)
    median_min = round(float(np.median(scenario.min_balance)), USD_DP)

    balances = np.round(scenario.balances, USD_DP)
    mins = np.round(scenario.min_balance, USD_DP)
    fbw = scenario.first_breach_week
    paths = [
        PathRecord(
            path_id=i,
            balances=balances[i].tolist(),
            min_balance=float(mins[i]),
            first_breach_week=None if fbw[i] < 0 else int(fbw[i]),
        )
        for i in range(scenario.n_paths)
    ]

    claims = emit_claims(model, delta, scenario, baseline_rate, delta_rate, median_min, culprit)

    return SimResult(
        run_id=run_id or derive_run_id(model, delta, seed, n_paths, horizon_weeks),
        seed=seed,
        n_paths=n_paths,
        horizon_weeks=horizon_weeks,
        breach_threshold=breach_threshold,
        baseline_breach_rate=baseline_rate,
        delta_breach_rate=delta_rate,
        median_min_balance=median_min,
        culprit=culprit,
        ranked_drivers=drivers,
        paths=paths,
        claims=claims,
    )
