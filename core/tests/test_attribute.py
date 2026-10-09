from __future__ import annotations

from core.attribute import attribute
from core.model import CashflowModel
from core.run import run
from core.simulate import make_noise, paths_from_components, flow_components, simulate


def test_dominant_obligation_is_named(dominant_model_dict):
    model = CashflowModel.from_dict(dominant_model_dict)
    culprit, drivers = attribute(model, None)
    assert culprit is not None
    assert culprit.obligation_id == "rec_villain"
    # Not just the biggest single bill: Rent is larger per payment.
    assert culprit.amount < max(r.amount for r in model.recurring)


def test_leave_one_out_reuses_identical_noise(model, delta):
    """Each LOO rate must equal a fresh simulate() of the model minus that obligation, same seed."""
    _, drivers = attribute(model, delta, seed=99, n_paths=200)
    noise = make_noise(99, 200, 104, len(model.recurring))
    comp = flow_components(model, delta, noise)
    for j, ob in enumerate(model.recurring):
        expected = paths_from_components(comp, 0.0, exclude=j).breach_rate
        got = next(d for d in drivers if d.obligation_id == ob.obligation_id)
        assert got.breach_rate_without == expected


def test_removing_an_obligation_never_increases_breach(model, delta):
    _, drivers = attribute(model, delta)
    assert all(d.marginal_contribution >= 0 for d in drivers)


def test_attribution_is_stable_across_repeat_runs(model, delta):
    a = attribute(model, delta, seed=5)
    b = attribute(model, delta, seed=5)
    assert a == b


def test_drivers_sorted_and_culprit_is_top_avoidable(model, delta):
    culprit, drivers = attribute(model, delta)
    contribs = [d.marginal_contribution for d in drivers]
    assert contribs == sorted(contribs, reverse=True)
    assert culprit == next(d for d in drivers if not d.essential)
    assert len(drivers) == len(model.recurring)


def test_essential_obligation_is_ranked_but_not_named(model, delta):
    culprit, drivers = attribute(model, delta)
    assert drivers[0].essential and drivers[0].label == "Rent"
    assert culprit.label == "Geico"
    assert not culprit.essential


def test_essential_is_named_when_nothing_avoidable_contributes(model_dict, delta):
    for r in model_dict["recurring"]:
        r["essential"] = True
    culprit, drivers = attribute(CashflowModel.from_dict(model_dict), delta)
    assert culprit == drivers[0]
    assert culprit.essential


def test_essential_defaults_false_when_b_omits_it(model_dict):
    for r in model_dict["recurring"]:
        r.pop("essential", None)
    model = CashflowModel.from_dict(model_dict)
    assert not any(r.essential for r in model.recurring)


def test_no_breach_means_no_culprit(model_dict):
    model_dict["opening_balance"] = 1_000_000.0
    model = CashflowModel.from_dict(model_dict)
    culprit, drivers = attribute(model, None)
    assert culprit is None
    assert all(d.marginal_contribution == 0 for d in drivers)


def test_culprit_breach_rate_without_is_consistent(model, delta):
    result = run(model, delta)
    c = result.culprit
    assert c is not None
    assert round(result.delta_breach_rate - c.breach_rate_without, 4) == c.marginal_contribution


def test_attribute_matches_simulate_scenario_rate(model, delta):
    culprit, _ = attribute(model, delta)
    scenario_rate = simulate(model, delta).breach_rate
    assert abs(scenario_rate - culprit.breach_rate_without - culprit.marginal_contribution) < 1e-12
