"""Leave-one-out attribution: which recurring obligation drives the breaches.

One noise tensor, drawn once. Every leave-one-out run reads the same dice, so
the only thing that changes between runs is the obligation we removed.
Re-seeding per run would turn the comparison into noise.
"""
from __future__ import annotations

from core.model import CashflowModel, Culprit, DecisionDelta, Driver
from core.simulate import FlowComponents, flow_components, make_noise, paths_from_components


def rank_drivers(
    model: CashflowModel,
    comp: FlowComponents,
    breach_threshold: float,
    include_delta: bool = True,
) -> tuple[Culprit | None, list[Driver]]:
    scenario_rate = paths_from_components(comp, breach_threshold, include_delta).breach_rate
    drivers = []
    for j, ob in enumerate(model.recurring):
        without = paths_from_components(comp, breach_threshold, include_delta, exclude=j).breach_rate
        drivers.append(
            Driver(
                obligation_id=ob.obligation_id,
                label=ob.label,
                amount=ob.amount,
                breach_rate_without=without,
                marginal_contribution=scenario_rate - without,
                evidence=list(ob.evidence),
                essential=ob.essential,
            )
        )
    # Biggest drop wins. Ties go to the larger bill, then to id, so ranking is stable.
    drivers.sort(key=lambda d: (-d.marginal_contribution, -d.amount, d.obligation_id))
    # Money is fungible, so removing rent always "fixes" the most. The villain is the top
    # obligation the user could actually cut; essentials win only if nothing else contributes.
    contributing = [d for d in drivers if d.marginal_contribution > 0]
    avoidable = [d for d in contributing if not d.essential]
    culprit = (avoidable or contributing or [None])[0]
    return culprit, drivers


def attribute(
    model: CashflowModel,
    delta: DecisionDelta | None,
    seed: int = 1337,
    n_paths: int = 500,
    horizon_weeks: int = 104,
    breach_threshold: float = 0.0,
) -> tuple[Culprit | None, list[Driver]]:
    """Culprit (None if removing nothing reduces breaches) and every obligation ranked."""
    noise = make_noise(seed, n_paths, horizon_weeks, len(model.recurring))
    comp = flow_components(model, delta, noise)
    return rank_drivers(model, comp, breach_threshold, include_delta=delta is not None)
