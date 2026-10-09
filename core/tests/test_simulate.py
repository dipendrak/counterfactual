from __future__ import annotations

import json
import time

import numpy as np
import pytest

from core.model import CashflowModel, DecisionDelta
from core.run import run
from core.simulate import (
    DAYS_PER_WEEK,
    delta_outflow,
    flow_components,
    make_noise,
    occurrences_per_week,
    paths_from_components,
    simulate,
)


def test_same_seed_is_byte_identical(model, delta):
    a = json.dumps(run(model, delta, seed=42).to_dict(), sort_keys=True).encode()
    b = json.dumps(run(model, delta, seed=42).to_dict(), sort_keys=True).encode()
    assert a == b


def test_different_seed_differs(model, delta):
    a = simulate(model, delta, seed=1).balances
    b = simulate(model, delta, seed=2).balances
    assert not np.array_equal(a, b)


def test_shapes_and_opening_balance(model, delta):
    ps = simulate(model, delta, n_paths=37, horizon_weeks=20)
    assert ps.balances.shape == (37, 21)
    assert np.all(ps.balances[:, 0] == model.opening_balance)


def test_500_paths_104_weeks_under_two_seconds(model, delta):
    start = time.perf_counter()
    run(model, delta, n_paths=500, horizon_weeks=104)
    assert time.perf_counter() - start < 2.0


def test_occurrences_integer_cadence():
    # Days 6, 20, 34, 48 -> weeks 0, 2, 4, 6.
    occ = occurrences_per_week(next_due_day=6, cadence_days=14, horizon_weeks=8)
    assert occ.tolist() == [1, 0, 1, 0, 1, 0, 1, 0]


def test_occurrences_non_integer_cadence_keeps_long_run_rate():
    occ = occurrences_per_week(next_due_day=0, cadence_days=14.3, horizon_weeks=104)
    assert occ.sum() == len(np.arange(0, 104 * DAYS_PER_WEEK, 14.3))
    assert occ.max() <= 1


def test_occurrences_past_due_rolls_forward():
    # Due 10 days ago on a 14-day cycle -> next on day 4 -> week 0.
    occ = occurrences_per_week(next_due_day=-10, cadence_days=14, horizon_weeks=4)
    assert occ.tolist() == [1, 0, 1, 0]


def test_cadence_shorter_than_a_week_fires_multiple_times():
    occ = occurrences_per_week(next_due_day=0, cadence_days=3, horizon_weeks=2)
    assert occ.sum() == 5  # days 0, 3, 6, 9, 12
    assert occ.tolist() == [3, 2]


def test_one_time_delta_lands_in_its_week_and_shifts_every_later_balance(model):
    delta = DecisionDelta(kind="one_time", amount=1400.0, start_day=64)
    noise = make_noise(7, 50, 30, len(model.recurring))
    comp = flow_components(model, delta, noise)
    without = paths_from_components(comp, 0.0, include_delta=False).balances
    with_ = paths_from_components(comp, 0.0, include_delta=True).balances
    diff = without - with_
    week = 64 // DAYS_PER_WEEK  # day 64 is in week 9, reflected in balances[:, 10]
    np.testing.assert_allclose(diff[:, : week + 1], 0.0)
    np.testing.assert_allclose(diff[:, week + 1 :], 1400.0)


def test_recurring_delta_charges_every_cadence():
    delta = DecisionDelta(kind="recurring", amount=50.0, start_day=0, cadence_days=7)
    assert delta_outflow(delta, 10).tolist() == [50.0] * 10


def test_delta_beyond_horizon_has_no_effect(model):
    late = DecisionDelta(kind="one_time", amount=10_000.0, start_day=10_000)
    np.testing.assert_array_equal(
        simulate(model, late, n_paths=20).balances, simulate(model, None, n_paths=20).balances
    )


def test_huge_delta_is_full_breach_not_exception(model):
    lifetime_income = model.income.mean * 104 * 7 / model.income.cadence_days
    delta = DecisionDelta(kind="one_time", amount=lifetime_income * 10, start_day=0)
    result = run(model, delta)
    assert result.delta_breach_rate == 1.0


def test_positive_delta_never_improves_any_path(model, delta):
    noise = make_noise(3, 200, 104, len(model.recurring))
    comp = flow_components(model, delta, noise)
    base = paths_from_components(comp, 0.0, include_delta=False)
    scen = paths_from_components(comp, 0.0, include_delta=True)
    assert np.all(scen.balances <= base.balances + 1e-9)
    assert scen.breach_rate >= base.breach_rate


def test_first_breach_week_matches_balances(model, delta):
    result = run(model, delta, n_paths=200)
    for p in result.paths:
        below = [i for i, b in enumerate(p.balances) if b < result.breach_threshold]
        assert p.first_breach_week == (below[0] if below else None)
        assert p.min_balance == min(p.balances)


def test_zero_recurring_does_not_crash(model_dict, delta):
    model_dict["recurring"] = []
    model = CashflowModel.from_dict(model_dict)
    result = run(model, delta)
    assert result.culprit is None
    assert result.ranked_drivers == []
    assert len(result.paths) == 500


def test_no_delta_reports_baseline_only(model):
    result = run(model, None)
    assert result.delta_breach_rate is None
    assert "breach_rate_with_delta" not in {c.kind for c in result.claims}


def test_noise_tensor_obligation_count_mismatch_is_loud(model, delta):
    noise = make_noise(1, 10, 10, len(model.recurring) + 1)
    with pytest.raises(ValueError):
        flow_components(model, delta, noise)
