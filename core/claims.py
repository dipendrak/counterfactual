"""Typed claims for Dev C to render. Gemini never writes a number that isn't here.

Every claim carries the Nessie transaction IDs behind it, plumbed from the
CashflowModel's evidence arrays. A claim with no evidence can't be validated,
so it is not emitted.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from core.model import CashflowModel, Claim, Culprit, DecisionDelta
from core.simulate import PathSet


def emit_claims(
    model: CashflowModel,
    delta: DecisionDelta | None,
    scenario: PathSet,
    baseline_breach_rate: float,
    delta_breach_rate: float | None,
    median_min_balance: float,
    culprit: Culprit | None,
) -> list[Claim]:
    model_ev = model.all_evidence()
    drafts: list[tuple[str, Any, str | None, list[str]]] = [
        ("paths_simulated", scenario.n_paths, "count", model_ev),
        ("baseline_breach_rate", baseline_breach_rate, "probability", model_ev),
    ]
    if delta is not None and delta_breach_rate is not None:
        drafts.append(("breach_rate_with_delta", delta_breach_rate, "probability", model_ev))
    drafts.append(("median_min_balance", median_min_balance, "usd", model_ev))

    fbw = scenario.first_breach_week
    fbw = fbw[fbw >= 0]
    if fbw.size:
        drafts.append(("first_breach_week", int(np.median(fbw)), "week", model_ev))

    if culprit is not None:
        drafts += [
            ("culprit_label", culprit.label, None, culprit.evidence),
            ("culprit_marginal_contribution", culprit.marginal_contribution, "probability", culprit.evidence),
            ("culprit_amount", culprit.amount, "usd", culprit.evidence),
        ]

    claims = []
    for kind, value, unit, evidence in drafts:
        if not evidence:
            continue
        claims.append(
            Claim(
                claim_id=f"c{len(claims) + 1}",
                kind=kind,
                value=value,
                unit=unit,
                evidence=list(evidence),
            )
        )
    return claims
